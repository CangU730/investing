#!/usr/bin/env python3
"""宏观数据看板（FRED，美联储圣路易斯分行，免费、无需 key）。

用法：
  python3 tools/macro.py                              默认看板：政策利率、美债收益率、利差、通胀、就业、VIX
  python3 tools/macro.py us10y vix hy_spread           指定别名
  python3 tools/macro.py CPIAUCSL --yoy                任意 FRED 代码，--yoy 计算同比
  python3 tools/macro.py us10y --start 2025-01-01 --csv out/us10y.csv
  python3 tools/macro.py --list                        列出全部别名
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import statistics
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BlockedError, FetchError, fetch_text, print_table, run  # noqa: E402

# 别名: (FRED 代码, 中文名, 显示方式)  显示方式：level 原值，yoy 同比，diff 月度变化
ALIASES = {
    "fedfunds": ("DFF", "联邦基金有效利率（%）", "level"),
    "us3m": ("DTB3", "3 个月美债（%）", "level"),
    "us2y": ("DGS2", "2 年期美债（%）", "level"),
    "us10y": ("DGS10", "10 年期美债（%）", "level"),
    "us30y": ("DGS30", "30 年期美债（%）", "level"),
    "10y2y": ("T10Y2Y", "10Y-2Y 利差（%）", "level"),
    "real10y": ("DFII10", "10 年期实际利率 TIPS（%）", "level"),
    "breakeven10y": ("T10YIE", "10 年期通胀预期（%）", "level"),
    "cpi": ("CPIAUCSL", "CPI 同比", "yoy"),
    "core_cpi": ("CPILFESL", "核心 CPI 同比", "yoy"),
    "pce": ("PCEPI", "PCE 同比", "yoy"),
    "core_pce": ("PCEPILFE", "核心 PCE 同比", "yoy"),
    "unrate": ("UNRATE", "失业率（%）", "level"),
    "payrolls": ("PAYEMS", "非农新增就业（千人）", "diff"),
    "claims": ("ICSA", "初请失业金人数", "level"),
    "gdp": ("GDPC1", "实际 GDP 同比", "yoy"),
    "vix": ("VIXCLS", "VIX 波动率指数", "level"),
    "hy_spread": ("BAMLH0A0HYM2", "高收益债利差（%）", "level"),
    "ig_spread": ("BAMLC0A0CM", "投资级债利差（%）", "level"),
    "usd": ("DTWEXBGS", "美元指数（广义贸易加权）", "level"),
    "wti": ("DCOILWTICO", "WTI 原油（美元/桶）", "level"),
    "mortgage30": ("MORTGAGE30US", "30 年房贷利率（%）", "level"),
    "m2": ("M2SL", "M2 货币供应同比", "yoy"),
    "sp500": ("SP500", "标普 500 指数", "level"),
}
DASHBOARD = ["fedfunds", "us2y", "us10y", "10y2y", "core_cpi", "core_pce", "unrate", "payrolls", "vix", "hy_spread"]

# FRED 会限流云服务器的 IP；这些市场数据可以改从 Yahoo Finance 取
YAHOO_FALLBACK = {"us3m": "^IRX", "us10y": "^TNX", "us30y": "^TYX", "vix": "^VIX", "sp500": "^GSPC", "wti": "CL=F"}


def fetch_series(series_id: str, start: str) -> list[tuple[str, float]]:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({"id": series_id, "cosd": start})
    text = fetch_text(url, ttl=3600)
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise FetchError(f"FRED 没有 {series_id} 的数据（返回内容：{text[:60]!r}）")
    rows = []
    for r in reader:
        if len(r) < 2 or r[1] in (".", ""):
            continue
        try:
            rows.append((r[0], float(r[1])))
        except ValueError:
            continue
    if not rows:
        raise FetchError(f"FRED 的 {series_id} 在这个区间没有数据")
    return rows


def transform(rows: list[tuple[str, float]], mode: str) -> list[tuple[str, float]]:
    if mode == "level":
        return rows
    lookup = dict(rows)
    out = []
    for i, (d, v) in enumerate(rows):
        if mode == "diff":
            if i:
                out.append((d, v - rows[i - 1][1]))
            continue
        current = date.fromisoformat(d)
        try:
            prior = current.replace(year=current.year - 1).isoformat()
        except ValueError:  # 2 月 29 日
            prior = current.replace(year=current.year - 1, day=28).isoformat()
        base = lookup.get(prior)
        if base is None:  # 日期没对齐时找最近的早一点的观测
            earlier = [x for x in rows if x[0] <= prior]
            base = earlier[-1][1] if earlier and (date.fromisoformat(prior) - date.fromisoformat(earlier[-1][0])).days < 45 else None
        if base:
            out.append((d, (v / base - 1) * 100))
    return out


def value_at(rows: list[tuple[str, float]], target: date):
    earlier = [x for x in rows if x[0] <= target.isoformat()]
    return earlier[-1] if earlier else None


def yahoo_series(symbol: str, start: str) -> list[tuple[str, float]]:
    from quote import from_yahoo  # 只有备用源用得到

    days = (date.today() - date.fromisoformat(start)).days
    rng = next((r for r, d in (("1y", 366), ("2y", 731), ("5y", 1827)) if days <= d), "max")
    rows = [(b["date"], b["close"]) for b in from_yahoo(symbol, rng)["bars"] or [] if b["date"] >= start]
    if not rows:
        raise FetchError(f"Yahoo Finance 没有 {symbol} 的数据")
    if symbol in ("^IRX", "^TNX", "^TYX") and statistics.median(v for _, v in rows) > 20:
        rows = [(d, v / 10) for d, v in rows]  # 老口径是收益率 × 10
    return rows


def fmt(v, mode: str) -> str:
    if v is None:
        return "—"
    if mode == "yoy":
        return f"{v:.2f}%"
    if mode == "diff":
        return f"{v:+,.0f}"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.2f}"


def summarize(alias: str, series_id: str, name: str, mode: str, start: str, source: str = "FRED") -> dict:
    fetch_start = (date.fromisoformat(start) - timedelta(days=400)).isoformat() if mode != "level" else start
    raw = yahoo_series(YAHOO_FALLBACK[alias], start) if source == "Yahoo" else fetch_series(series_id, fetch_start)
    rows = transform(raw, mode)
    rows = [r for r in rows if r[0] >= start] or rows
    last_date, last = rows[-1]
    end = date.fromisoformat(last_date)
    ago = {}
    for label, days in (("1周前", 7), ("1月前", 30), ("3月前", 91), ("1年前", 365)):
        hit = value_at(rows, end - timedelta(days=days))
        # 月度、季度数据没有“1 周前”这样的观测，离目标日期太远的不算
        gap = (end - date.fromisoformat(hit[0])).days if hit else None
        ago[label] = hit[1] if hit and 0 < gap <= days * 1.5 + 3 else None
    values = [v for _, v in rows]
    return {"alias": alias, "series": series_id, "name": name, "mode": mode, "date": last_date, "value": last,
            **ago, "min": min(values), "max": max(values), "rows": rows, "source": source}


def main() -> int:
    parser = argparse.ArgumentParser(description="宏观数据看板（FRED）", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("series", nargs="*", help="别名或 FRED 代码，默认显示看板")
    parser.add_argument("--start", default=(date.today() - timedelta(days=730)).isoformat(), help="起始日期，默认两年前")
    parser.add_argument("--yoy", action="store_true", help="对自定义的 FRED 代码计算同比")
    parser.add_argument("--csv", help="把序列导出为 CSV")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true", help="列出全部别名")
    args = parser.parse_args()

    if args.list:
        print_table(["别名", "FRED 代码", "名称"], [[k, v[0], v[1]] for k, v in ALIASES.items()], align="lll")
        return 0

    results, problems, blocked = [], [], []
    fred_down = False
    for item in args.series or DASHBOARD:
        if item.lower() in ALIASES:
            series_id, name, mode = ALIASES[item.lower()]
        else:
            series_id, name, mode = item.upper(), item.upper(), "yoy" if args.yoy else "level"
        alias = item.lower()
        try:
            if blocked or fred_down:  # FRED 已经连不上，不再逐个重试
                if alias not in YAHOO_FALLBACK:
                    problems.append(f"{item}：跳过（FRED 取不到，也没有备用源）")
                    continue
                results.append(summarize(alias, series_id, name, mode, args.start, source="Yahoo"))
                continue
            try:
                results.append(summarize(alias, series_id, name, mode, args.start))
            except FetchError:
                fred_down = True
                if alias not in YAHOO_FALLBACK:
                    raise
                results.append(summarize(alias, series_id, name, mode, args.start, source="Yahoo"))
        except BlockedError as e:
            if e.host not in blocked:
                blocked.append(e.host)
        except FetchError as e:
            problems.append(f"{item}：{e}")
    if not results and blocked:
        raise BlockedError(blocked[0])
    if not results and problems:
        raise FetchError("；".join(problems))

    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "rows"} for r in results], ensure_ascii=False, indent=2))
    else:
        print(f"宏观数据（区间 {args.start} 起）  来源：FRED, Federal Reserve Bank of St. Louis；标注 Yahoo 的来自 Yahoo Finance")
        rows = [[r["name"], r["date"], fmt(r["value"], r["mode"]), fmt(r["1周前"], r["mode"]), fmt(r["1月前"], r["mode"]),
                 fmt(r["3月前"], r["mode"]), fmt(r["1年前"], r["mode"]), f"{fmt(r['min'], r['mode'])} – {fmt(r['max'], r['mode'])}",
                 "Yahoo" if r["source"] == "Yahoo" else "FRED"]
                for r in results]
        print_table(["指标", "日期", "最新", "1周前", "1月前", "3月前", "1年前", "区间低–高", "来源"], rows, align="lrrrrrrrl")
    if args.csv and results:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["series", "date", "value"])
            for r in results:
                for d, v in r["rows"]:
                    w.writerow([r["series"], d, v])
        print(f"\n已导出到 {args.csv}")
    for p in problems + [f"{h} 被网络策略拦截" for h in blocked]:
        print(f"[取数失败] {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    run(main)
