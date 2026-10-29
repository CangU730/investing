#!/usr/bin/env python3
"""美股行情与历史价格。

数据源依次尝试：Yahoo Finance → Stooq（都不需要 key）→ FMP、Alpha Vantage（设置了 API key 才用，只有最新价）。

用法：
  python3 tools/quote.py SPCX TSLA                        最新行情
  python3 tools/quote.py SPCX --history 6mo               加上区间统计：涨跌幅、年化波动率、最大回撤
  python3 tools/quote.py SPCX --history 1y --csv out/spcx.csv   导出日线
  python3 tools/quote.py SPCX --json                      输出 JSON

--history 可选：1mo 3mo 6mo ytd 1y 2y 5y max
指数和汇率用 Yahoo 代码，例如 ^GSPC（标普 500）、^IXIC（纳指）、USDCNY=X（美元兑人民币）。
可选环境变量：FMP_API_KEY、ALPHAVANTAGE_API_KEY
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BlockedError, FetchError, fetch_json, fetch_text, fmt_big, fmt_pct, fmt_price, run, to_et  # noqa: E402

RANGES = ["1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y", "max"]
RANGE_DAYS = {"5d": 7, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827}


def _at(values, i):
    if not values or i >= len(values):
        return None
    return values[i]


def range_start(rng: str, end: date) -> date | None:
    if rng == "max":
        return None
    if rng == "ytd":
        return date(end.year, 1, 1)
    return end - timedelta(days=RANGE_DAYS[rng])


# ---------- 数据源 ----------

def yahoo_symbol(symbol: str) -> str:
    # BRK.B → BRK-B（Yahoo 用连字符表示股票类别）
    if re.fullmatch(r"[A-Z]+\.[A-Z]", symbol):
        return symbol.replace(".", "-")
    return symbol


def from_yahoo(symbol: str, history_range: str | None) -> dict:
    rng = history_range or "5d"
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(symbol))}"
        f"?range={rng}&interval=1d&includePrePost=false"
    )
    chart = fetch_json(url, ttl=60).get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise FetchError(f"Yahoo Finance：{err.get('description') or err}")
    results = chart.get("result") or []
    if not results:
        raise FetchError(f"Yahoo Finance 没有 {symbol} 的数据")
    res = results[0]
    meta = res.get("meta") or {}
    offset = meta.get("gmtoffset") or 0
    indicators = res.get("indicators") or {}
    q = (indicators.get("quote") or [{}])[0]
    adj = (indicators.get("adjclose") or [{}])[0].get("adjclose")

    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        close = _at(q.get("close"), i)
        if close is None:
            continue
        bars.append({
            "date": datetime.fromtimestamp(ts + offset, tz=timezone.utc).date().isoformat(),
            "open": _at(q.get("open"), i),
            "high": _at(q.get("high"), i),
            "low": _at(q.get("low"), i),
            "close": close,
            "adj_close": _at(adj, i) if adj else close,
            "volume": _at(q.get("volume"), i),
        })

    price = meta.get("regularMarketPrice")
    if price is None and bars:
        price = bars[-1]["close"]
    if price is None:
        raise FetchError(f"Yahoo Finance 没有 {symbol} 的价格")

    # 前收盘：最后一根日线如果就是最新交易日，取倒数第二根
    prev = None
    market_time = meta.get("regularMarketTime")
    market_day = (
        datetime.fromtimestamp(market_time + offset, tz=timezone.utc).date().isoformat() if market_time else None
    )
    if bars:
        if bars[-1]["date"] == market_day:
            prev = bars[-2]["close"] if len(bars) >= 2 else meta.get("chartPreviousClose")
        else:
            prev = bars[-1]["close"]
    prev = prev or meta.get("previousClose")

    return _quote(
        symbol,
        price=price,
        prev=prev,
        name=meta.get("longName") or meta.get("shortName"),
        exchange=meta.get("fullExchangeName") or meta.get("exchangeName"),
        currency=meta.get("currency"),
        day_high=meta.get("regularMarketDayHigh"),
        day_low=meta.get("regularMarketDayLow"),
        high_52w=meta.get("fiftyTwoWeekHigh"),
        low_52w=meta.get("fiftyTwoWeekLow"),
        volume=meta.get("regularMarketVolume"),
        time=market_time,
        source="Yahoo Finance",
        bars=bars if history_range else None,
    )


def stooq_symbol(symbol: str) -> str | None:
    if symbol.startswith("^") or "=" in symbol:
        return None
    return symbol.lower().replace(".", "-") + ".us"


def from_stooq(symbol: str, history_range: str | None) -> dict:
    s = stooq_symbol(symbol)
    if not s:
        raise FetchError(f"Stooq 不支持 {symbol}")
    text = fetch_text(f"https://stooq.com/q/d/l/?s={s}&i=d", ttl=300)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "Close" not in rows[0]:
        raise FetchError(f"Stooq 没有 {symbol} 的数据（返回内容：{text[:60]!r}）")

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    all_bars = []
    for r in rows:
        close = num(r.get("Close"))
        if close is None:
            continue
        all_bars.append({
            "date": r["Date"], "open": num(r.get("Open")), "high": num(r.get("High")), "low": num(r.get("Low")),
            "close": close, "adj_close": close, "volume": num(r.get("Volume")),
        })
    if not all_bars:
        raise FetchError(f"Stooq 没有 {symbol} 的有效数据")

    last_day = date.fromisoformat(all_bars[-1]["date"])
    year_ago = (last_day - timedelta(days=365)).isoformat()
    last_year = [b for b in all_bars if b["date"] >= year_ago]
    start = range_start(history_range or "5d", last_day)
    bars = [b for b in all_bars if start is None or b["date"] >= start.isoformat()]
    last = all_bars[-1]
    return _quote(
        symbol,
        price=last["close"],
        prev=all_bars[-2]["close"] if len(all_bars) >= 2 else None,
        day_high=last["high"],
        day_low=last["low"],
        high_52w=max(b["high"] or b["close"] for b in last_year),
        low_52w=min(b["low"] or b["close"] for b in last_year),
        volume=last["volume"],
        as_of=last["date"],
        source="Stooq（日线收盘）",
        bars=bars if history_range else None,
    )


def from_fmp(symbol: str, history_range: str | None) -> dict:
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise FetchError("未设置 FMP_API_KEY")
    data = fetch_json(
        f"https://financialmodelingprep.com/stable/quote?symbol={urllib.parse.quote(symbol)}&apikey={key}", ttl=60
    )
    if isinstance(data, dict):
        raise FetchError(f"FMP：{data.get('Error Message') or data}")
    if not data:
        raise FetchError(f"FMP 没有 {symbol} 的数据")
    d = data[0]
    return _quote(
        symbol,
        price=d.get("price"),
        prev=d.get("previousClose"),
        name=d.get("name"),
        exchange=d.get("exchange"),
        day_high=d.get("dayHigh"),
        day_low=d.get("dayLow"),
        high_52w=d.get("yearHigh"),
        low_52w=d.get("yearLow"),
        volume=d.get("volume"),
        market_cap=d.get("marketCap"),
        time=d.get("timestamp"),
        source="FMP",
    )


def from_alphavantage(symbol: str, history_range: str | None) -> dict:
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        raise FetchError("未设置 ALPHAVANTAGE_API_KEY")
    data = fetch_json(
        f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={urllib.parse.quote(symbol)}&apikey={key}",
        ttl=60,
    )
    q = data.get("Global Quote")
    if not q:
        raise FetchError(f"Alpha Vantage：{data.get('Note') or data.get('Information') or data.get('Error Message') or '无数据'}")

    def num(k):
        try:
            return float(q[k])
        except (KeyError, TypeError, ValueError):
            return None

    return _quote(
        symbol,
        price=num("05. price"),
        prev=num("08. previous close"),
        day_high=num("03. high"),
        day_low=num("04. low"),
        volume=num("06. volume"),
        as_of=q.get("07. latest trading day"),
        source="Alpha Vantage",
    )


def _quote(symbol: str, *, price, prev, source, **fields) -> dict:
    if price is None:
        raise FetchError(f"{source} 没有 {symbol} 的价格")
    out = {
        "symbol": symbol, "name": None, "exchange": None, "currency": "USD", "price": price, "prev_close": prev,
        "change": None, "change_pct": None, "day_high": None, "day_low": None, "high_52w": None, "low_52w": None,
        "volume": None, "market_cap": None, "time": None, "as_of": None, "source": source, "bars": None,
    }
    out.update({k: v for k, v in fields.items() if v is not None or k == "bars"})
    if prev:
        out["change"] = price - prev
        out["change_pct"] = price / prev - 1
    return out


SOURCES = [("Yahoo Finance", from_yahoo, True), ("Stooq", from_stooq, True),
           ("FMP", from_fmp, False), ("Alpha Vantage", from_alphavantage, False)]


def get_quote(symbol: str, history_range: str | None = None) -> dict:
    """按顺序尝试各数据源，返回第一个成功的结果。全部失败时抛出异常，说明每个源的失败原因。"""
    symbol = symbol.strip().upper()
    failures, blocked = [], []
    quote_only = None
    for name, func, has_history in SOURCES:
        if func in (from_fmp, from_alphavantage) and not os.environ.get(
            "FMP_API_KEY" if func is from_fmp else "ALPHAVANTAGE_API_KEY"
        ):
            continue
        try:
            result = func(symbol, history_range)
        except BlockedError as e:
            blocked.append(e.host)
            continue
        except FetchError as e:
            failures.append(f"{name}：{e}")
            continue
        if history_range and not has_history:
            quote_only = quote_only or result
            continue
        return result
    if quote_only:
        quote_only["warning"] = "历史数据源都不可用，只取到了最新价"
        return quote_only
    if blocked and not failures:
        raise BlockedError("、".join(blocked))
    detail = "；".join(failures + [f"{h}：被网络策略拦截" for h in blocked])
    raise FetchError(f"{symbol} 所有数据源都失败了：{detail}")


# ---------- 区间统计 ----------

def history_stats(bars: list[dict]) -> dict | None:
    series = [(b["date"], b["adj_close"] or b["close"]) for b in bars if (b["adj_close"] or b["close"])]
    if len(series) < 2:
        return None
    closes = [c for _, c in series]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    vol = statistics.pstdev(log_returns) * math.sqrt(252) if len(log_returns) > 1 else None

    peak_value, peak_date = series[0][1], series[0][0]
    max_dd, dd_from, dd_to = 0.0, None, None
    for d, c in series:
        if c > peak_value:
            peak_value, peak_date = c, d
        dd = c / peak_value - 1
        if dd < max_dd:
            max_dd, dd_from, dd_to = dd, peak_date, d

    high = max(series, key=lambda x: x[1])
    low = min(series, key=lambda x: x[1])
    return {
        "start": series[0][0], "end": series[-1][0], "days": len(series),
        "return": closes[-1] / closes[0] - 1, "volatility": vol,
        "max_drawdown": max_dd, "drawdown_from": dd_from, "drawdown_to": dd_to,
        "high": high[1], "high_date": high[0], "low": low[1], "low_date": low[0],
    }


# ---------- 输出 ----------

def print_quote(q: dict, history_range: str | None) -> None:
    title = q["symbol"]
    if q.get("name"):
        title += f"  {q['name']}"
    if q.get("exchange"):
        title += f"（{q['exchange']}）"
    print(f"{title}  数据源：{q['source']}")
    when = to_et(q["time"]) if q.get("time") else (q.get("as_of") or "—")
    change = "—"
    if q.get("change") is not None:
        change = f"{q['change']:+.2f}（{fmt_pct(q['change_pct'], 2)}）"
    print(f"  最新价 {fmt_price(q['price'])}  涨跌 {change}  时间 {when}")
    parts = []
    if q.get("day_low") is not None and q.get("day_high") is not None:
        parts.append(f"当日区间 {fmt_price(q['day_low'])} – {fmt_price(q['day_high'])}")
    if q.get("low_52w") is not None and q.get("high_52w") is not None:
        parts.append(f"52 周区间 {fmt_price(q['low_52w'])} – {fmt_price(q['high_52w'])}")
    if q.get("volume"):
        parts.append(f"成交量 {fmt_big(q['volume'])}股")
    if q.get("market_cap"):
        parts.append(f"市值 {fmt_big(q['market_cap'], '$')}")
    if parts:
        print("  " + "   ".join(parts))
    if q.get("warning"):
        print(f"  注意：{q['warning']}")
    if history_range and q.get("bars"):
        s = history_stats(q["bars"])
        if s:
            print(f"  区间统计（{history_range}：{s['start']} 至 {s['end']}，{s['days']} 个交易日，按复权收盘价）")
            vol = fmt_pct(s["volatility"], signed=False) if s["volatility"] is not None else "—"
            dd = fmt_pct(s["max_drawdown"])
            if s["drawdown_from"]:
                dd += f"（{s['drawdown_from']} → {s['drawdown_to']}）"
            print(f"    涨跌幅 {fmt_pct(s['return'])}   年化波动率 {vol}   最大回撤 {dd}")
            print(f"    最高 {fmt_price(s['high'])}（{s['high_date']}）   最低 {fmt_price(s['low'])}（{s['low_date']}）")


def write_csv(path: str, quotes: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"])
        for q in quotes:
            for b in q.get("bars") or []:
                w.writerow([q["symbol"], b["date"], b["open"], b["high"], b["low"], b["close"], b["adj_close"], b["volume"]])


def main() -> int:
    parser = argparse.ArgumentParser(description="美股行情与历史价格", formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("symbols", nargs="+", help="股票代码，例如 SPCX TSLA ^GSPC")
    parser.add_argument("--history", choices=RANGES, help="同时取历史日线并计算区间统计")
    parser.add_argument("--csv", help="把日线导出为 CSV（需要配合 --history）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    if args.csv and not args.history:
        parser.error("--csv 需要配合 --history 使用")

    quotes, errors = [], []
    for sym in args.symbols:
        try:
            quotes.append(get_quote(sym, args.history))
        except FetchError as e:
            errors.append((sym, e))
    if not quotes and errors:
        raise errors[0][1]  # 全部失败：交给 run() 输出统一提示

    if args.json:
        for q in quotes:
            if q.get("bars") is not None and args.history:
                q["stats"] = history_stats(q["bars"])
        print(json.dumps(quotes, ensure_ascii=False, indent=2))
    else:
        for i, q in enumerate(quotes):
            if i:
                print()
            print_quote(q, args.history)
    for sym, e in errors:
        reason = f"{e.host} 被网络策略拦截" if isinstance(e, BlockedError) else str(e)
        print(f"[取数失败] {sym}：{reason}", file=sys.stderr)
    if args.csv and quotes:
        write_csv(args.csv, quotes)
        print(f"\n日线已导出到 {args.csv}")
    return 1 if errors else 0


if __name__ == "__main__":
    run(main)
