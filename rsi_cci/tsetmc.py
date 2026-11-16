"""Read-only public TSETMC API. TLS checks and inherited proxy settings stay on."""

from dataclasses import dataclass
from urllib.parse import quote

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
