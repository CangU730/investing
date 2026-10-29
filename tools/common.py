"""tools/ 下各脚本共用的函数：HTTP 请求（带缓存和重试）、网络受限提示、数字和表格格式化。

只用 Python 标准库，装了 Python 3.9+ 就能直接运行。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache" / "http"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# SEC 要求 User-Agent 写明身份和联系邮箱：https://www.sec.gov/os/accessing-edgar-data
SEC_UA = os.environ.get("SEC_USER_AGENT", "personal-investing-research contact@example.com")
SEC_MIN_INTERVAL = 0.15  # SEC 限制每秒 10 次请求，这里留出余量

_last_sec_request = 0.0


class FetchError(Exception):
    """请求失败：对方返回错误、超时、内容无法解析等。"""


class BlockedError(FetchError):
    """当前环境的网络策略拦截了这个域名。"""

    def __init__(self, host: str):
        super().__init__(host)
        self.host = host


def blocked_hint(host: str) -> str:
    return (
        f"[网络受限] {host} 被当前环境的网络策略拦截，取不到数据。\n"
        "处理办法（任选其一）：\n"
        "  1. 云端会话：会话标题栏的云环境菜单 → Edit → Network access，把域名加入 Allowed domains\n"
        "     （运行 python3 tools/netcheck.py 可以列出全部需要放行的域名）\n"
        "  2. 在自己电脑上用 Claude Code 打开本仓库，本地网络一般不受限\n"
        "  3. 改用 WebSearch 搜索，结果注明“来自搜索摘要，未读原文”"
    )


def _throttle_sec() -> None:
    global _last_sec_request
    wait = SEC_MIN_INTERVAL - (time.monotonic() - _last_sec_request)
    if wait > 0:
        time.sleep(wait)
    _last_sec_request = time.monotonic()


def fetch(url: str, *, headers: dict | None = None, ttl: int = 0, timeout: int = 20, retries: int = 2) -> bytes:
    """GET 一个 URL，返回原始字节。ttl > 0 时使用本地缓存（单位：秒）。"""
    host = urllib.parse.urlsplit(url).hostname or url
    cache_path = None
    if ttl > 0:
        cache_path = CACHE_DIR / hashlib.sha256(url.encode()).hexdigest()
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < ttl:
            return cache_path.read_bytes()

    is_sec = host.endswith("sec.gov")
    request_headers = {
        "User-Agent": SEC_UA if is_sec else BROWSER_UA,
        "Accept": "*/*",
        "Accept-Encoding": "gzip",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)

    data = b""
    for attempt in range(retries + 1):
        if is_sec:
            _throttle_sec()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            hint = ""
            if is_sec and e.code == 403:
                hint = "（SEC 拒绝请求，多半是 User-Agent 不合规：请设置环境变量 SEC_USER_AGENT=\"你的名字 你的邮箱\"）"
            raise FetchError(f"{host} 返回 HTTP {e.code}{hint}") from None
        except urllib.error.URLError as e:
            reason = str(e.reason)
            if "Tunnel connection failed: 403" in reason or "Tunnel connection failed: 407" in reason:
                raise BlockedError(host) from None
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise FetchError(f"无法连接 {host}：{reason}") from None
        except OSError as e:  # 读取过程中的超时、连接重置
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            raise FetchError(f"连接 {host} 失败：{e}") from None

    if cache_path is not None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    return data


def fetch_text(url: str, **kwargs) -> str:
    return fetch(url, **kwargs).decode("utf-8", errors="replace")


def fetch_json(url: str, **kwargs):
    raw = fetch(url, **kwargs)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        host = urllib.parse.urlsplit(url).hostname
        raise FetchError(f"{host} 返回的不是有效 JSON（开头：{raw[:80]!r}）") from None


# ---------- 格式化 ----------

def fmt_big(value: float | None, prefix: str = "") -> str:
    """大数字用中文单位：1.46e10 → 146.0亿。"""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    a = abs(value)
    if a >= 1e12:
        return f"{sign}{prefix}{a / 1e12:.2f}万亿"
    if a >= 1e8:
        return f"{sign}{prefix}{a / 1e8:.1f}亿"
    if a >= 1e4:
        return f"{sign}{prefix}{a / 1e4:.1f}万"
    return f"{sign}{prefix}{a:,.0f}"


def fmt_money(value: float | None) -> str:
    return fmt_big(value, prefix="$")


def fmt_price(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def fmt_pct(value: float | None, digits: int = 1, signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.{digits}f}%" if signed else f"{value * 100:.{digits}f}%"


def to_et(ts: float | None) -> str:
    """Unix 时间戳 → 美东时间字符串。"""
    if ts is None:
        return "—"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    except Exception:  # 没有时区数据库（如部分 Windows）时退回 UTC
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def print_table(headers: list[str], rows: list[list], align: str | None = None) -> None:
    """打印对齐的表格。align 每列一个字符：l 左对齐，r 右对齐；默认首列左对齐、其余右对齐。"""
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in row] for row in rows]
    ncol = len(headers)
    align = align or ("l" + "r" * (ncol - 1))
    widths = [max(_display_width(r[i]) for r in cells) for i in range(ncol)]
    for n, row in enumerate(cells):
        parts = []
        for i, cell in enumerate(row):
            pad = " " * (widths[i] - _display_width(cell))
            parts.append(cell + pad if align[i] == "l" else pad + cell)
        print("  ".join(parts).rstrip())
        if n == 0:
            print("  ".join("-" * w for w in widths))


# ---------- 命令行入口 ----------

def setup_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def run(main) -> None:
    """统一处理网络受限和取数失败，给出中文提示和退出码（2 = 网络受限，1 = 其他失败）。"""
    setup_stdout()
    try:
        code = main()
    except BlockedError as e:
        print(blocked_hint(e.host), file=sys.stderr)
        sys.exit(2)
    except FetchError as e:
        print(f"[取数失败] {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:  # 输出被 head 之类的命令截断，安静退出
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
    sys.exit(code or 0)
