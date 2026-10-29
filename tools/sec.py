#!/usr/bin/env python3
"""SEC EDGAR 一手资料：公司文件、XBRL 财务数据、文件正文、全文检索、内部人交易（Form 4）。

全部来自 SEC 官方免费接口，不需要 API key。SEC 要求 User-Agent 写明身份和邮箱，
请设置环境变量 SEC_USER_AGENT="你的名字 你的邮箱"（不设也能试，但可能被拒）。

用法：
  python3 tools/sec.py cik SPCX                                 代码 → CIK 和公司名
  python3 tools/sec.py filings SPCX                             最近的文件
  python3 tools/sec.py filings SPCX --forms 10-Q,8-K --since 2026-07-01
  python3 tools/sec.py facts SPCX                               关键财务指标（最近 6 个季度）
  python3 tools/sec.py facts SPCX --annual --periods 4          年度数据
  python3 tools/sec.py doc <文件链接> --grep "lock-up|lockup"     读正文，按关键词找段落
  python3 tools/sec.py search '"lock-up agreement"' --ticker SPCX --forms 424B4,S-1
  python3 tools/sec.py insiders SPCX                            最近的内部人交易

代码表里还没收录的新股，可以直接传 CIK 数字代替代码。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FetchError, fetch_json, fetch_text, fmt_big, fmt_money, fmt_pct, print_table, run  # noqa: E402

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

FORM_NAMES = {
    "10-K": "年报", "10-Q": "季报", "8-K": "重大事项", "S-1": "IPO 注册声明", "424B4": "IPO 最终招股书",
    "424B5": "增发招股书", "S-3": "储架注册", "S-8": "员工股权计划注册", "DEF 14A": "股东大会委托书",
    "3": "内部人首次持股", "4": "内部人交易", "5": "内部人年度申报", "144": "拟出售受限股通知",
    "13F-HR": "机构持仓", "SC 13D": "5% 以上持股（主动）", "SC 13G": "5% 以上持股（被动）",
    "SCHEDULE 13D": "5% 以上持股（主动）", "SCHEDULE 13G": "5% 以上持股（被动）",
    "20-F": "外国公司年报", "6-K": "外国公司报告", "425": "并购沟通材料", "S-4": "并购注册声明",
}

ITEMS_8K = {
    "1.01": "签订重大协议", "1.02": "终止重大协议", "1.03": "破产或接管", "1.05": "网络安全事件",
    "2.01": "完成收购或处置", "2.02": "经营业绩", "2.03": "新增债务", "2.04": "债务加速到期",
    "2.05": "退出或处置成本", "2.06": "资产减值", "3.01": "退市或违反上市规则通知", "3.02": "未注册股权发行",
    "3.03": "证券持有人权利变更", "4.01": "更换审计师", "4.02": "已发布财报不可依赖", "5.01": "控制权变更",
    "5.02": "董事或高管变动", "5.03": "章程或财年变更", "5.07": "股东投票结果", "7.01": "Reg FD 披露",
    "8.01": "其他事项", "9.01": "财务报表及附件",
}

# (键, 中文名, 候选 XBRL 概念（按优先级）, 类型, 单位, 单季能否用累计值相减推算)
METRICS = [
    ("revenue", "营收", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"], "flow", "USD", True),
    ("gross_profit", "毛利", ["GrossProfit"], "flow", "USD", True),
    ("operating_income", "经营利润", ["OperatingIncomeLoss"], "flow", "USD", True),
    ("net_income", "净利润", ["NetIncomeLoss", "ProfitLoss"], "flow", "USD", True),
    ("eps", "稀释每股收益", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
     "flow", "USD/shares", False),
    ("ocf", "经营现金流", ["NetCashProvidedByUsedInOperatingActivities"], "flow", "USD", True),
    ("capex", "资本开支", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
     "flow", "USD", True),
    ("fcf", "自由现金流", [], "derived", "USD", True),
    ("rd", "研发费用", ["ResearchAndDevelopmentExpense"], "flow", "USD", True),
    ("sbc", "股权激励费用", ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"], "flow", "USD", True),
    ("cash", "现金及等价物", ["CashAndCashEquivalentsAtCarryingValue",
                         "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], "instant", "USD", False),
    ("debt", "长期债务", ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
     "instant", "USD", False),
    ("equity", "股东权益", ["StockholdersEquity"], "instant", "USD", False),
]
DEFAULT_METRICS = ["revenue", "operating_income", "net_income", "eps", "ocf", "capex", "fcf", "cash", "debt"]

TX_CODES = {
    "P": "公开市场买入", "S": "公开市场卖出", "A": "获授股份", "M": "期权行权", "X": "期权行权",
    "F": "缴税扣股", "G": "赠与", "C": "转换", "D": "返还公司", "J": "其他", "W": "继承", "I": "自主交易",
}


# ---------- 基础 ----------

def resolve(identifier: str) -> tuple[int, str | None, str | None]:
    """股票代码或 CIK → (CIK, 公司名, 代码)。"""
    s = identifier.strip()
    if s.isdigit():
        return int(s), None, None
    wanted = s.upper().replace(".", "-")
    for row in fetch_json(TICKERS_URL, ttl=86400).values():
        if str(row.get("ticker", "")).upper() == wanted:
            return int(row["cik_str"]), row.get("title"), row.get("ticker")
    raise FetchError(f"SEC 代码表里找不到 {s}（新上市的公司可能还没收录，可以直接传 CIK 数字）")


def get_submissions(cik: int) -> dict:
    return fetch_json(SUBMISSIONS_URL.format(cik=cik), ttl=600)


def iter_filings(submissions: dict):
    recent = (submissions.get("filings") or {}).get("recent") or {}
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "primaryDocDescription", "items"]
    n = len(recent.get("accessionNumber") or [])
    columns = {k: (recent.get(k) or []) for k in keys}
    for i in range(n):
        yield {k: (columns[k][i] if i < len(columns[k]) else None) for k in keys}


def filing_url(cik: int, accession: str, document: str) -> str:
    return ARCHIVE_URL.format(cik=cik, acc=accession.replace("-", ""), doc=document)


def describe(filing: dict) -> str:
    form = filing.get("form") or ""
    base = form.split("/")[0]
    if base == "8-K" and filing.get("items"):
        items = [ITEMS_8K.get(x.strip(), x.strip()) for x in str(filing["items"]).split(",") if x.strip()]
        return "、".join(i for i in items if i != "财务报表及附件") or "财务报表及附件"
    name = FORM_NAMES.get(base, "")
    extra = filing.get("primaryDocDescription") or ""
    if extra and extra.upper() not in (form.upper(), base.upper(), f"FORM {base.upper()}"):
        return f"{name}（{extra}）" if name else extra
    return name


def form_matches(form: str, wanted: set[str]) -> bool:
    form = (form or "").upper()
    return form in wanted or form.split("/")[0] in wanted


# ---------- filings ----------

def cmd_cik(args) -> int:
    cik, name, ticker = resolve(args.ticker)
    if name is None:
        name = get_submissions(cik).get("name")
    print(f"{ticker or args.ticker.upper()}  CIK {cik}（{cik:010d}）  {name}")
    print(f"EDGAR 页面：https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}")
    return 0


def cmd_filings(args) -> int:
    cik, _, _ = resolve(args.ticker)
    subs = get_submissions(cik)
    wanted = {f.strip().upper() for f in args.forms.split(",")} if args.forms else None
    rows, out = [], []
    for f in iter_filings(subs):
        if wanted and not form_matches(f["form"], wanted):
            continue
        if args.since and (f["filingDate"] or "") < args.since:
            continue
        url = filing_url(cik, f["accessionNumber"], f["primaryDocument"] or "")
        rows.append([f["filingDate"], f["form"], describe(f), url])
        out.append({**f, "url": url})
        if len(rows) >= args.limit:
            break
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"{subs.get('name')}（CIK {cik}）最近的 SEC 文件  来源：data.sec.gov submissions")
    if not rows:
        print("没有符合条件的文件。")
        return 0
    print_table(["申报日", "类型", "内容", "链接"], rows, align="llll")
    return 0


# ---------- facts ----------

def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def flow_series(entries: list[dict], annual: bool, additive: bool) -> dict:
    """区间型指标（营收、利润、现金流）→ {期末日: (数值, 是否推算)}。"""
    latest = {}
    for e in entries:
        if not e.get("start") or not e.get("end"):
            continue
        key = (e["start"], e["end"])
        if key not in latest or (e.get("filed") or "") > (latest[key].get("filed") or ""):
            latest[key] = e
    entries = list(latest.values())
    out = {}
    low, high = (350, 380) if annual else (80, 100)
    for e in entries:
        if low <= _days(e["start"], e["end"]) <= high:
            out[e["end"]] = (e["val"], False)
    if annual or not additive:
        return out
    # 10-Q 的现金流量表只给年初至今的累计值，单季 = 相邻两个累计值之差
    by_start = defaultdict(list)
    for e in entries:
        by_start[e["start"]].append(e)
    for group in by_start.values():
        group.sort(key=lambda e: e["end"])
        for prev, cur in zip(group, group[1:]):
            if 80 <= _days(prev["end"], cur["end"]) <= 100 and cur["end"] not in out:
                out[cur["end"]] = (cur["val"] - prev["val"], True)
    return out


def instant_series(entries: list[dict]) -> dict:
    """时点型指标（现金、债务、权益）→ {日期: (数值, False)}。"""
    latest = {}
    for e in entries:
        if e.get("start") or not e.get("end"):
            continue
        if e["end"] not in latest or (e.get("filed") or "") > (latest[e["end"]].get("filed") or ""):
            latest[e["end"]] = e
    return {k: (e["val"], False) for k, e in latest.items()}


def metric_series(facts: dict, spec, annual: bool) -> dict:
    _, _, concepts, kind, unit, additive = spec
    merged = {}
    for concept in concepts:
        node = (facts.get("us-gaap") or {}).get(concept) or (facts.get("ifrs-full") or {}).get(concept)
        if not node:
            continue
        entries = (node.get("units") or {}).get(unit) or []
        series = flow_series(entries, annual, additive) if kind == "flow" else instant_series(entries)
        for end, value in series.items():
            merged.setdefault(end, value)  # 靠前的概念优先
    return merged


def _near(series: dict, target: date, tolerance: int = 20):
    best = None
    for end, value in series.items():
        gap = abs((date.fromisoformat(end) - target).days)
        if gap <= tolerance and (best is None or gap < best[0]):
            best = (gap, value)
    return best[1] if best else None


def cmd_facts(args) -> int:
    cik, _, _ = resolve(args.ticker)
    data = fetch_json(FACTS_URL.format(cik=cik), ttl=6 * 3600)
    facts = data.get("facts") or {}
    specs = {m[0]: m for m in METRICS}
    keys = [k.strip() for k in args.metrics.split(",")] if args.metrics else DEFAULT_METRICS
    unknown = [k for k in keys if k not in specs]
    if unknown:
        raise FetchError(f"不认识的指标：{', '.join(unknown)}；可选：{', '.join(specs)}")

    series = {}
    for k in keys:
        if specs[k][3] == "derived":
            continue
        series[k] = metric_series(facts, specs[k], args.annual)
    if "fcf" in keys:
        ocf = series.get("ocf") or metric_series(facts, specs["ocf"], args.annual)
        capex = series.get("capex") or metric_series(facts, specs["capex"], args.annual)
        series["fcf"] = {
            end: (ocf[end][0] - capex[end][0], ocf[end][1] or capex[end][1]) for end in ocf if end in capex
        }

    flow_keys = [k for k in keys if specs[k][3] in ("flow", "derived") and series.get(k)]
    anchor = series.get("revenue") or (series[flow_keys[0]] if flow_keys else {})
    if not anchor:
        anchor = next((s for s in series.values() if s), {})
    periods = sorted(anchor)[-args.periods:]
    if not periods:
        print(f"{data.get('entityName')} 在 SEC XBRL 里没有这些指标的数据（可能刚上市、还没交过 10-Q/10-K）。")
        return 0

    if args.json:
        out = {k: {p: series[k].get(p, (None, False))[0] for p in periods} for k in keys if k in series}
        print(json.dumps({"entity": data.get("entityName"), "cik": cik, "periods": periods, "metrics": out},
                         ensure_ascii=False, indent=2))
        return 0

    label = "年度" if args.annual else "季度"
    print(f"{data.get('entityName')}（CIK {cik}）{label}数据  来源：SEC XBRL companyfacts")
    rows, any_derived = [], False
    for k in keys:
        s = series.get(k) or {}
        if not s:
            continue
        name, unit = specs[k][1], specs[k][4]
        row = [name]
        for p in periods:
            v = s.get(p)
            if v is None:
                row.append("—")
                continue
            text = f"{'-' if v[0] < 0 else ''}${abs(v[0]):.2f}" if unit == "USD/shares" else fmt_money(v[0])
            if v[1]:
                text += "*"
                any_derived = True
            row.append(text)
        rows.append(row)
        if k == "revenue":
            yoy = ["  同比"]
            for p in periods:
                prior = _near(s, date.fromisoformat(p) - timedelta(days=365))
                cur = s.get(p)
                yoy.append(fmt_pct(cur[0] / prior[0] - 1) if cur and prior and prior[0] else "—")
            rows.append(yoy)
    print_table(["指标"] + periods, rows)
    if any_derived:
        print("* 由年初至今累计值相减推算的单季数据")

    shares = (facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if shares:
        latest = instant_series((shares.get("units") or {}).get("shares") or [])
        if latest:
            end = max(latest)
            print(f"封面披露的流通股数：{fmt_big(latest[end][0])}股（截至 {end}；多类别股票的公司可能只含部分类别）")
    return 0


# ---------- doc ----------

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "ul", "ol", "hr"}
    SKIP = {"script", "style", "head", "ix:header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts).replace("\xa0", " ")
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r" *\n *", "\n", t)
        t = re.sub(r"(\n *\| *)+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def grep_text(text: str, pattern: str, context: int, max_matches: int) -> list[str]:
    regex = re.compile(pattern, re.IGNORECASE)
    windows = []
    for m in regex.finditer(text):
        start, end = max(0, m.start() - context), min(len(text), m.end() + context)
        if windows and start <= windows[-1][1]:
            windows[-1][1] = end
        else:
            windows.append([start, end])
    snippets = []
    for start, end in windows[:max_matches]:
        snippet = text[start:end].replace("\n", " ")
        snippets.append(("…" if start else "") + snippet + ("…" if end < len(text) else ""))
    return snippets


def cmd_doc(args) -> int:
    raw = fetch_text(args.url, ttl=86400)
    looks_html = "<html" in raw[:2000].lower() or "<div" in raw[:5000].lower() or args.url.lower().endswith((".htm", ".html"))
    text = html_to_text(raw) if looks_html else raw
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"全文 {len(text):,} 字符已保存到 {args.out}")
    if args.grep:
        snippets = grep_text(text, args.grep, args.context, args.max_matches)
        total = len(re.findall(args.grep, text, re.IGNORECASE))
        print(f"在 {args.url} 中找到 {total} 处“{args.grep}”，显示前 {len(snippets)} 段：")
        for i, s in enumerate(snippets, 1):
            print(f"\n[{i}] {s}")
        return 0
    if not args.out:
        print(text[: args.max_chars])
        if len(text) > args.max_chars:
            print(f"\n……（全文 {len(text):,} 字符，只显示前 {args.max_chars:,}；用 --grep 找段落，或 --out 保存全文）")
    return 0


# ---------- search ----------

def cmd_search(args) -> int:
    params = {"q": args.query}
    if args.forms:
        params["forms"] = ",".join(f.strip().upper() for f in args.forms.split(","))
    if args.since or args.until:
        params.update(dateRange="custom", startdt=args.since or "2001-01-01", enddt=args.until or date.today().isoformat())
    cik = None
    if args.ticker:
        cik, _, _ = resolve(args.ticker)
        params["ciks"] = f"{cik:010d}"
    data = fetch_json(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}", ttl=600)
    hits = (data.get("hits") or {}).get("hits") or []
    total = ((data.get("hits") or {}).get("total") or {}).get("value", len(hits))
    rows, out = [], []
    for h in hits:
        src = h.get("_source") or {}
        ciks = [int(c) for c in (src.get("ciks") or []) if str(c).isdigit()]
        if cik and cik not in ciks:
            continue
        accession, _, document = (h.get("_id") or "").partition(":")
        url = filing_url(ciks[0], accession, document) if ciks and accession and document else ""
        name = (src.get("display_names") or [""])[0]
        rows.append([src.get("file_date"), src.get("form"), src.get("file_type") or "", name[:48], url])
        out.append({"date": src.get("file_date"), "form": src.get("form"), "file_type": src.get("file_type"),
                    "company": name, "url": url})
        if len(rows) >= args.limit:
            break
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"EDGAR 全文检索“{args.query}”：共 {total} 条，显示 {len(rows)} 条  来源：efts.sec.gov")
    if rows:
        print_table(["申报日", "类型", "文件", "公司", "链接"], rows, align="lllll")
    return 0


# ---------- insiders ----------

def _text(el, path: str) -> str | None:
    node = el.find(path)
    if node is None:
        return None
    value = node.find("value")
    target = value if value is not None else node
    return target.text.strip() if target.text and target.text.strip() else None


def _num(s: str | None) -> float | None:
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def parse_form4(xml_text: str) -> dict:
    root = ET.fromstring(xml_text.strip().encode("utf-8"))
    owners, roles = [], []
    for owner in root.findall("reportingOwner"):
        name = _text(owner, "reportingOwnerId/rptOwnerName")
        if name:
            owners.append(name)
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            def flag(tag):
                return (_text(rel, tag) or "").lower() in ("1", "true")
            found = []
            if flag("isDirector"):
                found.append("董事")
            if flag("isTenPercentOwner"):
                found.append("10% 股东")
            if flag("isOfficer"):
                found.append(_text(rel, "officerTitle") or "高管")
            if flag("isOther"):
                found.append(_text(rel, "otherText") or "其他")
            roles.extend(r for r in found if r not in roles)
    plan = (_text(root, "aff10b5One") or "").lower() in ("1", "true")
    transactions = []
    for table, derivative in (("nonDerivativeTable/nonDerivativeTransaction", False),
                              ("derivativeTable/derivativeTransaction", True)):
        for tx in root.findall(table):
            transactions.append({
                "security": _text(tx, "securityTitle"),
                "date": _text(tx, "transactionDate"),
                "code": _text(tx, "transactionCoding/transactionCode"),
                "shares": _num(_text(tx, "transactionAmounts/transactionShares")),
                "price": _num(_text(tx, "transactionAmounts/transactionPricePerShare")),
                "acquired": _text(tx, "transactionAmounts/transactionAcquiredDisposedCode"),
                "after": _num(_text(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
                "direct": _text(tx, "ownershipNature/directOrIndirectOwnership"),
                "derivative": derivative,
            })
    return {"owners": owners, "roles": roles, "plan_10b5_1": plan, "transactions": transactions}


def cmd_insiders(args) -> int:
    cik, _, _ = resolve(args.ticker)
    subs = get_submissions(cik)
    filings = [f for f in iter_filings(subs) if (f["form"] or "") in ("4", "4/A")][: args.limit]
    if not filings:
        print(f"{subs.get('name')} 最近没有 Form 4 申报。")
        return 0
    rows, out, buys, sells = [], [], 0.0, 0.0
    for f in filings:
        document = (f["primaryDocument"] or "").split("/")[-1]  # 去掉 xslF345X05/ 之类的渲染前缀，取原始 XML
        url = filing_url(cik, f["accessionNumber"], document)
        try:
            parsed = parse_form4(fetch_text(url, ttl=86400))
        except (FetchError, ET.ParseError) as e:
            rows.append([f["filingDate"], "（解析失败）", "", str(e)[:40], "", "", "", "", ""])
            continue
        owner = "、".join(parsed["owners"])[:24]
        role = "、".join(parsed["roles"])[:30]
        for tx in parsed["transactions"]:
            value = tx["shares"] * tx["price"] if tx["shares"] and tx["price"] else None
            if tx["code"] == "P" and value:
                buys += value
            if tx["code"] == "S" and value:
                sells += value
            action = TX_CODES.get(tx["code"] or "", tx["code"] or "")
            if tx["derivative"]:
                action += "（衍生品）"
            rows.append([
                tx["date"] or f["filingDate"], owner, role, action,
                fmt_big(tx["shares"]) if tx["shares"] else "—",
                f"${tx['price']:.2f}" if tx["price"] else "—",
                fmt_money(value) if value else "—",
                fmt_big(tx["after"]) if tx["after"] else "—",
                "是" if parsed["plan_10b5_1"] else "",
            ])
            out.append({**tx, "owners": parsed["owners"], "roles": parsed["roles"],
                        "plan_10b5_1": parsed["plan_10b5_1"], "filed": f["filingDate"], "url": url})
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"{subs.get('name')} 最近 {len(filings)} 份 Form 4 内部人交易  来源：SEC EDGAR")
    print_table(["交易日", "内部人", "身份", "交易", "股数", "价格", "金额", "交易后持股", "10b5-1 计划"], rows,
                align="llllrrrrl")
    print(f"\n合计：公开市场买入 {fmt_money(buys)}，公开市场卖出 {fmt_money(sells)}。")
    print("说明：“获授股份”“缴税扣股”“期权行权”通常不代表看多或看空；10b5-1 计划交易是事先排好的。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SEC EDGAR 一手资料", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("cik", help="代码 → CIK")
    p.add_argument("ticker")
    p.set_defaults(func=cmd_cik)

    p = sub.add_parser("filings", help="最近的文件")
    p.add_argument("ticker")
    p.add_argument("--forms", help="只看这些类型，逗号分隔，例如 10-K,10-Q,8-K,4,144")
    p.add_argument("--since", help="只看这个日期之后的，YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_filings)

    p = sub.add_parser("facts", help="XBRL 财务数据")
    p.add_argument("ticker")
    p.add_argument("--annual", action="store_true", help="看年度数据（默认季度）")
    p.add_argument("--periods", type=int, default=6, help="显示最近几期")
    p.add_argument("--metrics", help="逗号分隔：" + ",".join(m[0] for m in METRICS))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_facts)

    p = sub.add_parser("doc", help="读文件正文")
    p.add_argument("url")
    p.add_argument("--grep", help="按正则找段落，例如 'lock-up|lockup'")
    p.add_argument("--context", type=int, default=300, help="每处匹配前后各显示多少字符")
    p.add_argument("--max-matches", type=int, default=10)
    p.add_argument("--max-chars", type=int, default=15000, help="不用 --grep 时最多显示多少字符")
    p.add_argument("--out", help="把全文保存到文件")
    p.set_defaults(func=cmd_doc)

    p = sub.add_parser("search", help="EDGAR 全文检索")
    p.add_argument("query", help="检索词，短语用双引号包起来")
    p.add_argument("--ticker", help="只看这家公司")
    p.add_argument("--forms", help="只看这些类型，逗号分隔")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("insiders", help="内部人交易（Form 4）")
    p.add_argument("ticker")
    p.add_argument("--limit", type=int, default=15, help="读取最近几份 Form 4")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_insiders)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    run(main)
