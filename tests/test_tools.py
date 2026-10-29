"""tools/ 数据工具的离线测试：用 tests/fixtures 里的虚构样例数据代替网络请求，检查解析、推算和输出逻辑。

运行：python3 -m unittest discover -s tests -v
样例里的公司 Example Corp（EXMP，CIK 1234567）和所有数字都是虚构的。
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "tools"))

import common  # noqa: E402
import macro  # noqa: E402
import news  # noqa: E402
import portfolio  # noqa: E402
import quote  # noqa: E402
import sec  # noqa: E402


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeNet:
    """按 URL 里的关键字返回样例数据；没配置的 URL 一律当作被网络策略拦截。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise common.BlockedError(urllib.parse.urlsplit(url).hostname)


def no_api_keys():
    env = {k: v for k, v in os.environ.items() if k not in ("FMP_API_KEY", "ALPHAVANTAGE_API_KEY")}
    return mock.patch.dict(os.environ, env, clear=True)


class CommonTests(unittest.TestCase):
    def test_fmt_big_uses_chinese_units(self):
        self.assertEqual(common.fmt_big(1.4575e10), "145.8亿")
        self.assertEqual(common.fmt_big(1.97e12), "1.97万亿")
        self.assertEqual(common.fmt_money(-3.5e9), "-$35.0亿")
        self.assertEqual(common.fmt_big(12345678), "1234.6万")
        self.assertEqual(common.fmt_pct(0.0456), "+4.6%")

    def test_policy_block_becomes_blocked_error(self):
        err = urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(common.BlockedError) as ctx:
                common.fetch("https://data.sec.gov/x.json", retries=0)
        self.assertEqual(ctx.exception.host, "data.sec.gov")
        self.assertIn("Allowed domains", common.blocked_hint("data.sec.gov"))

    def test_sec_403_mentions_user_agent(self):
        err = urllib.error.HTTPError("https://www.sec.gov/x", 403, "Forbidden", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(common.FetchError) as ctx:
                common.fetch("https://www.sec.gov/x", retries=0)
        self.assertIn("SEC_USER_AGENT", str(ctx.exception))

    def test_print_table_aligns_cjk(self):
        out = io.StringIO()
        with redirect_stdout(out):
            common.print_table(["指标", "值"], [["营收", "$1.0亿"], ["经营现金流", "-$2.0亿"]])
        lines = out.getvalue().splitlines()
        widths = {common._display_width(line) for line in lines}
        self.assertEqual(len(widths), 1, lines)


class QuoteTests(unittest.TestCase):
    def test_yahoo_parsing_and_previous_close(self):
        with no_api_keys(), mock.patch.object(common, "fetch", FakeNet({"query1.finance.yahoo.com": fixture("yahoo_chart.json")})):
            q = quote.get_quote("exmp", "1mo")
        self.assertEqual(q["source"], "Yahoo Finance")
        self.assertEqual(q["name"], "Example Corp")
        self.assertEqual(q["price"], 104.5)
        self.assertEqual(q["prev_close"], 95.0)  # 最后一根日线就是当天，前收取倒数第二根
        self.assertAlmostEqual(q["change_pct"], 104.5 / 95 - 1)
        self.assertEqual(len(q["bars"]), 6)
        self.assertEqual(q["bars"][-1]["date"], "2026-10-28")
        self.assertEqual(q["high_52w"], 150.0)

    def test_history_stats(self):
        closes = [("d1", 100), ("d2", 110), ("d3", 99), ("d4", 88), ("d5", 95), ("d6", 104.5)]
        stats = quote.history_stats([{"date": d, "close": c, "adj_close": c} for d, c in closes])
        self.assertAlmostEqual(stats["return"], 0.045)
        self.assertAlmostEqual(stats["max_drawdown"], 88 / 110 - 1)
        self.assertEqual((stats["drawdown_from"], stats["drawdown_to"]), ("d2", "d4"))
        self.assertEqual((stats["high_date"], stats["low_date"]), ("d2", "d4"))
        self.assertGreater(stats["volatility"], 0)

    def test_falls_back_to_stooq_when_yahoo_blocked(self):
        with no_api_keys(), mock.patch.object(common, "fetch", FakeNet({"stooq.com": fixture("stooq.csv")})):
            q = quote.get_quote("EXMP")
        self.assertTrue(q["source"].startswith("Stooq"))
        self.assertEqual((q["price"], q["prev_close"]), (104.5, 95.0))
        self.assertEqual(q["high_52w"], 106.0)  # 一年前的数据不计入 52 周区间

    def test_all_sources_blocked(self):
        with no_api_keys(), mock.patch.object(common, "fetch", FakeNet({})):
            with self.assertRaises(common.BlockedError) as ctx:
                quote.get_quote("EXMP")
        self.assertIn("query1.finance.yahoo.com", ctx.exception.host)
        self.assertIn("stooq.com", ctx.exception.host)

    def test_cli_output(self):
        out = io.StringIO()
        net = FakeNet({"query1.finance.yahoo.com": fixture("yahoo_chart.json")})
        with no_api_keys(), mock.patch.object(common, "fetch", net), \
                mock.patch.object(sys, "argv", ["quote.py", "EXMP", "--history", "1mo"]), redirect_stdout(out):
            self.assertEqual(quote.main(), 0)
        text = out.getvalue()
        self.assertIn("最新价 $104.50", text)
        self.assertIn("最大回撤 -20.0%（2026-10-22 → 2026-10-26）", text)


def sec_net() -> FakeNet:
    return FakeNet({
        "company_tickers.json": fixture("sec_tickers.json"),
        "submissions/CIK0001234567.json": fixture("sec_submissions.json"),
        "companyfacts/CIK0001234567.json": fixture("sec_companyfacts.json"),
        "efts.sec.gov": fixture("efts_search.json"),
        "wk-form4_1.xml": fixture("form4.xml"),
        "earnings-q3.htm": fixture("filing.htm"),
    })


class SecTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> str:
        out = io.StringIO()
        with mock.patch.object(common, "fetch", sec_net()), mock.patch.object(sys, "argv", ["sec.py"] + argv), \
                redirect_stdout(out):
            self.assertEqual(sec.main(), 0)
        return out.getvalue()

    def facts(self) -> dict:
        return json.loads(fixture("sec_companyfacts.json"))["facts"]

    def test_resolve(self):
        with mock.patch.object(common, "fetch", sec_net()):
            self.assertEqual(sec.resolve("exmp")[0], 1234567)
            self.assertEqual(sec.resolve("1234567")[0], 1234567)
            with self.assertRaises(common.FetchError):
                sec.resolve("NOPE")

    def test_filings_filter_and_links(self):
        out = self.run_cli(["filings", "EXMP", "--forms", "8-K,10-Q"])
        self.assertIn("经营业绩", out)
        self.assertIn("https://www.sec.gov/Archives/edgar/data/1234567/000123456726000047/earnings-q3.htm", out)
        self.assertNotIn("424B4", out)

    def test_cash_flow_quarters_derived_from_ytd(self):
        spec = next(m for m in sec.METRICS if m[0] == "ocf")
        series = sec.metric_series(self.facts(), spec, annual=False)
        self.assertEqual(series["2026-03-31"], (1_000_000_000, False))
        self.assertEqual(series["2026-06-30"], (2_000_000_000, True))
        self.assertEqual(series["2026-09-30"], (3_500_000_000, True))

    def test_eps_is_never_derived(self):
        spec = next(m for m in sec.METRICS if m[0] == "eps")
        series = sec.metric_series(self.facts(), spec, annual=False)
        self.assertEqual(series, {"2026-06-30": (-0.10, False), "2026-09-30": (0.25, False)})

    def test_instant_keeps_latest_filing(self):
        spec = next(m for m in sec.METRICS if m[0] == "cash")
        series = sec.metric_series(self.facts(), spec, annual=False)
        self.assertEqual(series["2026-09-30"][0], 50_000_000_000)

    def test_facts_table(self):
        out = self.run_cli(["facts", "EXMP"])
        self.assertIn("$100.0亿", out)      # Q3 营收
        self.assertIn("+150.0%", out)       # 同比：100 亿 vs 40 亿
        self.assertIn("-$35.0亿*", out)     # Q3 自由现金流 = 35 亿 - 70 亿，由累计值推算
        self.assertIn("$500.0亿", out)      # 现金取最晚申报的数
        self.assertIn("10.5亿股", out)      # 封面流通股数

    def test_negative_eps_format(self):
        out = self.run_cli(["facts", "EXMP", "--metrics", "eps"])
        self.assertIn("-$0.10", out)

    def test_annual_facts(self):
        out = self.run_cli(["facts", "EXMP", "--annual", "--metrics", "revenue"])
        self.assertIn("2025-12-31", out)
        self.assertIn("$180.0亿", out)

    def test_form4_parsing(self):
        parsed = sec.parse_form4(fixture("form4.xml").decode())
        self.assertEqual(parsed["owners"], ["DOE JANE"])
        self.assertEqual(parsed["roles"], ["董事", "Chief Financial Officer"])
        self.assertTrue(parsed["plan_10b5_1"])
        first = parsed["transactions"][0]
        self.assertEqual((first["code"], first["shares"], first["price"]), ("S", 10000.0, 101.5))

    def test_insiders_cli(self):
        out = self.run_cli(["insiders", "EXMP"])
        self.assertIn("公开市场卖出", out)
        self.assertIn("缴税扣股", out)
        self.assertIn("公开市场卖出 $101.5万", out)

    def test_html_to_text_and_grep(self):
        text = sec.html_to_text(fixture("filing.htm").decode())
        self.assertNotIn("HIDDEN-XBRL-FACT", text)
        self.assertNotIn("in script", text)
        self.assertIn("$10.0 billion", text)
        self.assertIn("Revenue | $10.0", text)
        snippets = sec.grep_text(text, "lock-up", 60, 5)
        self.assertEqual(len(snippets), 1)
        self.assertIn("December 8, 2026", snippets[0])

    def test_doc_cli_grep(self):
        url = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000047/earnings-q3.htm"
        out = self.run_cli(["doc", url, "--grep", "lock-up", "--context", "80"])
        self.assertIn("找到 1 处", out)

    def test_search_keeps_only_requested_company(self):
        out = self.run_cli(["search", '"lock-up"', "--ticker", "EXMP"])
        self.assertIn("https://www.sec.gov/Archives/edgar/data/1234567/000123456726000030/prospectus.htm", out)
        self.assertNotIn("Other Co", out)


def rss(items: list[tuple[str, str, datetime, str]]) -> bytes:
    body = ""
    for title, link, when, source in items:
        src = f'<source url="https://example.com">{source}</source>' if source else ""
        body += f"<item><title>{title}</title><link>{link}</link><pubDate>{format_datetime(when)}</pubDate>{src}</item>"
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'.encode()


class NewsTests(unittest.TestCase):
    def test_collect_dedupes_filters_and_sorts(self):
        now = datetime.now(timezone.utc)
        google = rss([
            ("Example Corp beats estimates - Reuters", "https://news.google.com/a", now - timedelta(hours=5), "Reuters"),
            ("Old story - CNBC", "https://news.google.com/b", now - timedelta(days=10), "CNBC"),
        ])
        yahoo = rss([
            ("Example Corp beats estimates", "https://finance.yahoo.com/c", now - timedelta(hours=6), ""),
            ("Example Corp files 10-Q", "https://www.example-news.com/d", now - timedelta(hours=1), ""),
        ])
        net = FakeNet({"news.google.com": google, "feeds.finance.yahoo.com": yahoo})
        with mock.patch.object(news, "fetch", net):
            items, problems = news.collect(None, "EXMP", 7, "en")
        self.assertEqual(problems, [])
        self.assertEqual([i["title"] for i in items], ["Example Corp files 10-Q", "Example Corp beats estimates"])
        self.assertEqual(items[0]["source"], "example-news.com")
        self.assertEqual(items[1]["source"], "Reuters")  # 时间更早的 Yahoo 重复项被去掉

    def test_partial_block_is_reported(self):
        now = datetime.now(timezone.utc)
        net = FakeNet({"news.google.com": rss([("A - X", "https://n/a", now, "X")])})
        with mock.patch.object(news, "fetch", net):
            items, problems = news.collect(None, "EXMP", 7, "en")
        self.assertEqual(len(items), 1)
        self.assertTrue(any("feeds.finance.yahoo.com" in p for p in problems))

    def test_everything_blocked(self):
        with mock.patch.object(news, "fetch", FakeNet({})):
            with self.assertRaises(common.BlockedError):
                news.collect("Example", None, 7, "en")


class MacroTests(unittest.TestCase):
    def test_daily_series_lookbacks(self):
        start = datetime(2026, 1, 1)
        lines = ["observation_date,DGS10"]
        for i in range(300):
            d = start + timedelta(days=i)
            value = "." if i == 150 else f"{4 + i / 1000:.3f}"  # FRED 用 "." 表示缺失
            lines.append(f"{d:%Y-%m-%d},{value}")
        net = FakeNet({"id=DGS10": "\n".join(lines).encode()})
        with mock.patch.object(common, "fetch", net):
            r = macro.summarize("us10y", "DGS10", "10 年期美债", "level", "2026-01-01")
        self.assertEqual(r["date"], "2026-10-27")
        self.assertAlmostEqual(r["value"], 4.299)
        self.assertAlmostEqual(r["1周前"], 4.292)
        self.assertIsNone(r["1年前"])

    def test_monthly_yoy(self):
        with mock.patch.object(common, "fetch", FakeNet({"id=CPIAUCSL": fixture("fred_cpi.csv")})):
            r = macro.summarize("cpi", "CPIAUCSL", "CPI 同比", "yoy", "2026-01-01")
        self.assertEqual(r["date"], "2026-09-01")
        self.assertAlmostEqual(r["value"], (333 / 321 - 1) * 100)
        self.assertIsNone(r["1周前"])  # 月度数据没有“1 周前”
        self.assertAlmostEqual(r["1月前"], (332 / 320 - 1) * 100)


class PortfolioTests(unittest.TestCase):
    def test_example_file_loads(self):
        holdings = portfolio.load_holdings(ROOT / "portfolio" / "holdings.example.csv")
        self.assertTrue(any(h["symbol"] == "CASH" for h in holdings))

    def test_valuation(self):
        holdings = [{"symbol": "EXMP", "shares": 10, "cost_basis": 100.0, "note": ""},
                    {"symbol": "CASH", "shares": 500, "cost_basis": None, "note": ""}]
        fake_quote = {"price": 104.5, "change_pct": 0.1, "source": "test"}
        with mock.patch.object(portfolio, "get_quote", return_value=fake_quote):
            positions, problems = portfolio.value_portfolio(holdings)
        self.assertEqual(problems, [])
        exmp = positions[0]
        self.assertAlmostEqual(exmp["value"], 1045)
        self.assertAlmostEqual(exmp["pnl"], 45)
        self.assertAlmostEqual(exmp["weight"], 1045 / 1545)


if __name__ == "__main__":
    unittest.main()
