"""Read-only public TSETMC API. TLS checks and inherited proxy settings stay on."""

from dataclasses import dataclass
from urllib.parse import quote, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .data import DataError, instrument_code, parse_history


def normalize(text: str) -> str:
    return text.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").strip()


@dataclass(frozen=True)
class Instrument:
    code: str
    symbol: str
    name: str
    last_date: int
    security_type: str = ""
    market: str = ""
    classification_source: str = ""

    @property
    def label(self) -> str:
        return f"{self.symbol} | {self.name} | {self.code} | آخرین تاریخ: {self.last_date}"


class TsetmcClient:
    BASE = "https://cdn.tsetmc.com/api"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(429, 502, 503, 504),
                      allowed_methods=frozenset({"GET"}), respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": "RSI-CCI-Local/1.0", "Accept": "application/json"})

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get(self, path: str) -> dict:
        try:
            response = self.session.get(self.BASE + "/" + path, timeout=(5, 25))
            response.raise_for_status()
        except requests.exceptions.SSLError as exc:
            raise DataError("تأیید گواهی HTTPS ناموفق بود؛ ساعت ویندوز و گواهی شبکه را بررسی کنید.") from exc
        except requests.RequestException as exc:
            raise DataError("دریافت از TSETMC ناموفق بود؛ اینترنت، پراکسی و دسترسی به cdn.tsetmc.com را بررسی و دوباره تلاش کنید.") from exc
        try:
            value = response.json()
        except ValueError as exc:
            raise DataError("TSETMC به جای داده JSON پاسخ دیگری فرستاد.") from exc
        if not isinstance(value, dict):
            raise DataError("قالب پاسخ TSETMC معتبر نیست.")
        return value

    def search(self, query: str) -> list[Instrument]:
        query = normalize(query)
        if not query or len(query) > 80:
            raise DataError("نام نماد باید بین ۱ تا ۸۰ نویسه باشد.")
        payload = self._get("Instrument/GetInstrumentSearch/" + quote(query, safe=""))
        rows = payload.get("instrumentSearch")
        if not isinstance(rows, list):
            raise DataError("ساختار نتیجه جست‌وجوی TSETMC معتبر نیست.")
        result = {}
        try:
            for row in rows:
                code = instrument_code(row["insCode"])
                result[code] = Instrument(code, normalize(row["lVal18AFC"]),
                                          normalize(row["lVal30"]), int(row.get("lastDate") or 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError("مشخصات یکی از نمادها قابل خواندن نیست.") from exc
        return sorted(result.values(), key=lambda x: (normalize(x.symbol) != query, -x.last_date, x.symbol))

    def history(self, code: str) -> dict:
        code = instrument_code(code)
        payload = self._get(f"ClosingPrice/GetClosingPriceDailyList/{code}/0")
        parse_history(payload)
        return payload

    def market_universe(self) -> list[Instrument]:
        # Request all paper types and non-traded instruments, not just today's
        # trades. Filter ordinary shares locally by the reported yVal code.
        query = [("market", "0"), ("industrialGroup", "")]
        query += [(f"paperTypes[{i}]", str(i + 1)) for i in range(9)]
        query += [("showTraded", "false"), ("withBestLimits", "false"),
                  ("hEven", "0"), ("RefID", "0")]
        payload = self._get("ClosingPrice/GetMarketWatch?" + urlencode(query))
        return parse_market_universe(payload)


STOCK_TYPES = {"300": "بورس", "303": "فرابورس", "309": "پایه", "313": "سایر سهام فرابورس"}
# Compact GetMarketWatch responses expose insID/lva/lvc rather than yVal and
# long instrument names. These identifier prefixes are also paired with the
# corresponding yVal values in the legacy market-watch format.
ISIN_TYPES = {"IRO1": "300", "IRO3": "303", "IRO7": "309", "IRO5": "313",
              "IRR1": "400", "IRR3": "403", "IRR7": "404", "IRT1": "305",
              "IRT3": "305", "IRTK": "380", "IRO9": "311", "IROF": "312",
              "IROA": "320", "IROB": "321", "IRO4": "other_future", "IRO6": "307",
              "IRB3": "306", "IRB4": "706", "IRB5": "301", "IRB6": "208",
              "IRB7": "206", "IRBE": "200", "IRBK": "308", "IRK1": "701",
              "IRK2": "327", "IRS4": "600", "IRSA": "602"}


def parse_market_universe(payload: dict) -> list[Instrument]:
    """Accept flat and nested public market-watch records; never truncate them."""
    rows = payload.get("marketwatch") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise DataError("فهرست کل بازار در پاسخ TSETMC موجود نیست یا خالی است.")
    result = {}
    try:
        for row in rows:
            info = row.get("instrument") or row
            code = instrument_code(info.get("insCode") or row["insCode"])
            symbol = normalize(info.get("lVal18AFC") or row["lva"])
            name = normalize(info.get("lVal30") or row["lvc"])
            if not symbol or not name:
                raise ValueError("Empty instrument name")
            security_type = str(info.get("yVal") or row.get("yVal") or "").strip()
            classification_source = "yVal" if security_type else ""
            if not security_type:
                identifier = str(info.get("instrumentID") or row.get("insID") or info.get("cIsin") or "")
                security_type = ISIN_TYPES.get(identifier.strip().upper()[:4], "")
                classification_source = "ISIN prefix" if security_type else ""
            last_date = int(info.get("lastDate") or row.get("dEven") or 0)
            if last_date:
                from datetime import datetime
                datetime.strptime(str(last_date), "%Y%m%d")
            item = Instrument(code, symbol, name, last_date, security_type,
                              STOCK_TYPES.get(security_type, str(info.get("flowTitle") or "")), classification_source)
            if code in result and result[code] != item:
                raise ValueError("Conflicting duplicate instrument")
            result[code] = item
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise DataError("ساختار فهرست بازار قابل خواندن نیست؛ هیچ نمادی بی‌صدا حذف نشد.") from exc
    return sorted(result.values(), key=lambda x: (x.symbol, x.code))
