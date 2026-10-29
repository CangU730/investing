#!/usr/bin/env python3
"""检查各个数据源在当前环境能不能访问，并列出需要放行的域名。

用法：
  python3 tools/netcheck.py
  python3 tools/netcheck.py --json

云端会话的放行方法：会话标题栏的云环境菜单 → Edit → Network access，
把“需要放行”里的域名加入 Allowed domains。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BROWSER_UA, SEC_UA, print_table, run  # noqa: E402

# (用途, 域名列表, 重要性)
SOURCES = [
    ("SEC EDGAR：文件、财务数据、内部人交易", ["www.sec.gov", "data.sec.gov", "efts.sec.gov"], "核心"),
    ("Yahoo Finance：行情、个股新闻", ["query1.finance.yahoo.com", "feeds.finance.yahoo.com"], "核心"),
    ("Google News：新闻聚合", ["news.google.com"], "核心"),
    ("FRED：宏观数据", ["fred.stlouisfed.org"], "核心"),
    ("Stooq：行情备用源", ["stooq.com"], "备用"),
    ("FMP：行情（需 FMP_API_KEY）", ["financialmodelingprep.com"], "可选"),
    ("Alpha Vantage：行情（需 ALPHAVANTAGE_API_KEY）", ["www.alphavantage.co"], "可选"),
    ("WebFetch 读原文：公司官网与财经媒体", [
        "finance.yahoo.com", "www.reuters.com", "www.cnbc.com", "www.marketwatch.com", "www.nasdaq.com",
        "www.investing.com", "www.marketbeat.com", "www.tipranks.com", "stockanalysis.com", "seekingalpha.com",
        "www.fool.com", "en.wikipedia.org",
    ], "推荐"),
]


def probe(host: str) -> tuple[str, str]:
    """返回 (状态, 说明)。服务器自己返回的 4xx/5xx 也算“能访问”，只有代理拦截才算“被拦截”。"""
    ua = SEC_UA if host.endswith("sec.gov") else BROWSER_UA
    request = urllib.request.Request(f"https://{host}/", headers={"User-Agent": ua}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return "可访问", f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return "可访问", f"HTTP {e.code}（服务器有响应）"
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "Tunnel connection failed: 403" in reason or "Tunnel connection failed: 407" in reason:
            return "被拦截", "网络策略拒绝"
        return "连不上", reason[:60]
    except OSError as e:
        return "连不上", str(e)[:60]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查数据源连通性")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    hosts = [(purpose, host, level) for purpose, hs, level in SOURCES for host in hs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda h: probe(h[1]), hosts))

    report = [{"purpose": p, "host": h, "level": lv, "status": s, "detail": d}
              for (p, h, lv), (s, d) in zip(hosts, results)]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print_table(["用途", "域名", "重要性", "状态", "说明"],
                [[r["purpose"], r["host"], r["level"], r["status"], r["detail"]] for r in report], align="lllll")
    blocked = [r for r in report if r["status"] == "被拦截"]
    core_blocked = [r for r in blocked if r["level"] == "核心"]
    print()
    if not blocked:
        print("全部数据源都能访问。")
        return 0
    print(f"有 {len(blocked)} 个域名被网络策略拦截，其中核心数据源 {len(core_blocked)} 个。")
    print("需要放行（复制到云环境设置 → Network access → Allowed domains）：")
    for level in ("核心", "备用", "可选", "推荐"):
        names = [r["host"] for r in blocked if r["level"] == level]
        if names:
            print(f"  [{level}] " + ", ".join(names))
    print("\n放行之前，可以先用 WebSearch 获取信息，结果注明“来自搜索摘要，未读原文”。")
    return 0


if __name__ == "__main__":
    run(main)
