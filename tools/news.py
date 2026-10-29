#!/usr/bin/env python3
"""新闻标题聚合：Google News RSS（英文或中文）+ Yahoo Finance 个股新闻 RSS。

只给标题、来源、时间和链接。要引用具体内容，必须打开原文或用 WebSearch 交叉核实，不能只凭标题下结论。

用法：
  python3 tools/news.py "SpaceX stock"                    最近 7 天的英文新闻
  python3 tools/news.py "SpaceX Tesla merger" --days 3
  python3 tools/news.py "SpaceX 股价" --lang zh           中文新闻
  python3 tools/news.py --ticker SPCX                     个股：Yahoo Finance + Google News
  python3 tools/news.py --ticker SPCX "Starship" --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BlockedError, FetchError, blocked_hint, fetch, run, to_et  # noqa: E402

LANGS = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
}


def parse_rss(xml_bytes: bytes, default_source: str = "") -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None and source_el.text else ""
        if not source:
            host = urllib.parse.urlsplit(link).hostname or ""
            source = default_source or host.removeprefix("www.")
        # Google News 的标题末尾带“ - 来源名”，去掉
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            title = title[: -len(suffix)]
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        items.append({"title": title, "source": source, "link": link, "published": published})
    return items


def google_news(query: str, days: int, lang: str) -> list[dict]:
    params = {"q": f"{query} when:{days}d", **LANGS[lang]}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    return parse_rss(fetch(url, ttl=600))


def yahoo_news(ticker: str) -> list[dict]:
    url = "https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode(
        {"s": ticker, "region": "US", "lang": "en-US"}
    )
    return parse_rss(fetch(url, ttl=600))


def _norm(title: str) -> str:
    return re.sub(r"[\W_]+", " ", title.lower()).strip()


def collect(query: str | None, ticker: str | None, days: int, lang: str) -> tuple[list[dict], list[str]]:
    """返回 (新闻列表, 失败说明)。"""
    jobs = []
    if ticker:
        jobs.append(("Yahoo Finance", lambda: yahoo_news(ticker)))
    google_query = " ".join(x for x in [f'"{ticker}"' if ticker else "", query or ""] if x)
    if google_query:
        jobs.append(("Google News", lambda: google_news(google_query, days, lang)))

    items, problems, blocked = [], [], []
    for name, job in jobs:
        try:
            items.extend(job())
        except BlockedError as e:
            blocked.append(e.host)
        except (FetchError, ET.ParseError) as e:
            problems.append(f"{name}：{e}")
    if not items and blocked and not problems:
        raise BlockedError("、".join(blocked))
    problems += [f"{h} 被网络策略拦截" for h in blocked]

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        if it["published"] and it["published"] < cutoff:
            continue
        key = _norm(it["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="新闻标题聚合", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("query", nargs="*", help="检索词")
    parser.add_argument("--ticker", help="股票代码，会加上 Yahoo Finance 个股新闻")
    parser.add_argument("--days", type=int, default=7, help="最近几天（默认 7）")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--lang", choices=sorted(LANGS), default="en", help="Google News 的语言（默认 en）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    query = " ".join(args.query).strip() or None
    if not query and not args.ticker:
        parser.error("需要检索词或 --ticker")

    items, problems = collect(query, args.ticker.upper() if args.ticker else None, args.days, args.lang)
    items = items[: args.limit]
    if args.json:
        print(json.dumps([{**it, "published": it["published"].isoformat() if it["published"] else None} for it in items],
                         ensure_ascii=False, indent=2))
    else:
        label = " + ".join(x for x in [args.ticker.upper() if args.ticker else "", query or ""] if x)
        print(f"“{label}”最近 {args.days} 天的新闻（{len(items)} 条，按时间倒序）  来源：Google News / Yahoo Finance RSS")
        for it in items:
            when = to_et(it["published"].timestamp()) if it["published"] else "时间未知"
            print(f"\n{when} | {it['source']}\n  {it['title']}\n  {it['link']}")
        if not items:
            print("没有找到新闻。")
        print("\n提示：这里只有标题。引用具体数字或说法前，要打开原文或用 WebSearch 交叉核实。")
    for p in problems:
        print(f"[部分来源失败] {p}", file=sys.stderr)
    if problems and any("拦截" in p for p in problems):
        print(blocked_hint("部分新闻源"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    run(main)
