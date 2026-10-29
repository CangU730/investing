#!/usr/bin/env python3
"""持仓估值：读持仓 CSV，拉最新价格，算市值、浮动盈亏、仓位占比和集中度。

CSV 格式（第一行是表头，cost_basis 是每股成本，单位美元；CASH 行的 shares 填现金金额）：
  symbol,shares,cost_basis,note
  SPCX,100,145.00,第一批
  QQQ,20,480.00,
  CASH,5000,,美元现金

注意：仓库是公开的。真实持仓请放在 portfolio/holdings.csv，这个文件已被 .gitignore 排除，不会提交。

用法：
  python3 tools/portfolio.py                       默认读 portfolio/holdings.csv
  python3 tools/portfolio.py my.csv --cny          额外显示人民币市值
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, BlockedError, FetchError, fmt_money, fmt_pct, fmt_price, print_table, run  # noqa: E402
from quote import get_quote  # noqa: E402

DEFAULT_FILE = ROOT / "portfolio" / "holdings.csv"
EXAMPLE_FILE = ROOT / "portfolio" / "holdings.example.csv"


def load_holdings(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    holdings = []
    for i, r in enumerate(rows, start=2):
        symbol = (r.get("symbol") or "").strip().upper()
        if not symbol or symbol.startswith("#"):
            continue
        try:
            shares = float(r.get("shares") or 0)
            cost = float(r["cost_basis"]) if (r.get("cost_basis") or "").strip() else None
        except ValueError:
            raise FetchError(f"{path} 第 {i} 行的数字格式不对：{r}") from None
        holdings.append({"symbol": symbol, "shares": shares, "cost_basis": cost, "note": (r.get("note") or "").strip()})
    return holdings


def value_portfolio(holdings: list[dict]) -> tuple[list[dict], list[str]]:
    positions, problems = [], []
    for h in holdings:
        if h["symbol"] == "CASH":
            positions.append({**h, "price": 1.0, "value": h["shares"], "pnl": None, "pnl_pct": None, "source": "现金"})
            continue
        try:
            q = get_quote(h["symbol"])
        except BlockedError:
            raise
        except FetchError as e:
            problems.append(f"{h['symbol']}：{e}")
            positions.append({**h, "price": None, "value": None, "pnl": None, "pnl_pct": None, "source": "取价失败"})
            continue
        value = h["shares"] * q["price"]
        cost_total = h["shares"] * h["cost_basis"] if h["cost_basis"] else None
        positions.append({
            **h, "price": q["price"], "change_pct": q.get("change_pct"), "value": value,
            "pnl": value - cost_total if cost_total else None,
            "pnl_pct": value / cost_total - 1 if cost_total else None, "source": q["source"],
        })
    total = sum(p["value"] for p in positions if p["value"] is not None)
    for p in positions:
        p["weight"] = p["value"] / total if total and p["value"] is not None else None
    return positions, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="持仓估值", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("file", nargs="?", help=f"持仓 CSV，默认 {DEFAULT_FILE.relative_to(ROOT)}")
    parser.add_argument("--cny", action="store_true", help="用 USDCNY 汇率额外显示人民币市值")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.file) if args.file else DEFAULT_FILE
    if not path.exists():
        print(f"找不到持仓文件 {path}。可以复制示例文件开始：\n  cp {EXAMPLE_FILE.relative_to(ROOT)} "
              f"{DEFAULT_FILE.relative_to(ROOT)}", file=sys.stderr)
        return 1
    positions, problems = value_portfolio(load_holdings(path))
    total = sum(p["value"] for p in positions if p["value"] is not None)
    cost = sum(p["shares"] * p["cost_basis"] for p in positions if p["cost_basis"] and p["value"] is not None)
    invested = sum(p["value"] for p in positions if p["cost_basis"] and p["value"] is not None)
    fx = None
    if args.cny:
        try:
            fx = get_quote("USDCNY=X")["price"]
        except FetchError as e:
            problems.append(f"USDCNY 汇率：{e}")

    if args.json:
        print(json.dumps({"positions": positions, "total": total, "usdcny": fx}, ensure_ascii=False, indent=2))
        return 0

    rows = []
    for p in sorted(positions, key=lambda x: x["value"] or 0, reverse=True):
        rows.append([
            p["symbol"], f"{p['shares']:,.4g}" if p["symbol"] != "CASH" else "—",
            fmt_price(p["cost_basis"]) if p["cost_basis"] else "—",
            fmt_price(p["price"]) if p["symbol"] != "CASH" else "—",
            fmt_pct(p.get("change_pct"), 2) if p.get("change_pct") is not None else "—",
            fmt_money(p["value"]), fmt_money(p["pnl"]) if p["pnl"] is not None else "—",
            fmt_pct(p["pnl_pct"]) if p["pnl_pct"] is not None else "—",
            fmt_pct(p["weight"], signed=False) if p["weight"] is not None else "—", p["note"],
        ])
    print(f"持仓估值（{path.name}）")
    print_table(["代码", "股数", "成本价", "现价", "今日", "市值", "浮动盈亏", "盈亏%", "占比", "备注"], rows,
                align="lrrrrrrrrl")
    line = f"\n总市值 {fmt_money(total)}"
    if fx:
        line += f"（约 ¥{total * fx:,.0f}，USDCNY {fx:.4f}）"
    if cost:
        line += f"；有成本记录的部分浮动盈亏 {fmt_money(invested - cost)}（{fmt_pct(invested / cost - 1)}）"
    print(line)

    warnings = []
    for p in positions:
        if p["symbol"] != "CASH" and p["weight"] and p["weight"] > 0.2:
            warnings.append(f"{p['symbol']} 占 {fmt_pct(p['weight'], signed=False)}，单一持仓超过 20%，集中度偏高")
    cash = sum(p["value"] for p in positions if p["symbol"] == "CASH")
    if total:
        print(f"现金占比 {fmt_pct(cash / total, signed=False)}")
    for w in warnings:
        print(f"提醒：{w}")
    for p in problems:
        print(f"[取数失败] {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    run(main)
