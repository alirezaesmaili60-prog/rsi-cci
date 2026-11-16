"""Strict TSETMC schema parsing, local cache, and daily price preparation."""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


class DataError(ValueError):
    pass


def tehran_today() -> date:
    return datetime.now(ZoneInfo("Asia/Tehran")).date()


def instrument_code(value: str | int) -> str:
    value = str(value).strip()
    if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 20:
        raise DataError("شناسه نماد باید فقط شامل ارقام انگلیسی باشد.")
    return value


def write_json_atomic(path: Path, document: dict) -> None:
    """Replace a local document only after its complete JSON has been written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temp_path = Path(f.name)
            json.dump(document, f, ensure_ascii=False, allow_nan=False)
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def parse_history(payload: dict) -> pd.DataFrame:
    rows = payload.get("closingPriceDaily") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise DataError("پاسخ TSETMC شامل سابقه روزانه معتبر نیست.")
    mapping = {"dEven": "date", "priceFirst": "open", "priceMax": "high",
               "priceMin": "low", "pDrCotVal": "close", "pClosing": "final",
               "priceYesterday": "yesterday", "qTotTran5J": "volume", "zTotTran": "trades"}
    try:
        df = pd.DataFrame(rows)
        if not set(mapping) <= set(df):
            raise DataError("ساختار پاسخ TSETMC تغییر کرده است؛ ستون‌های لازم وجود ندارند.")
        df = df[list(mapping)].rename(columns=mapping)
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="raise")
        for col in df.columns.drop("date"):
            df[col] = pd.to_numeric(df[col], errors="raise")
        if not np.isfinite(df.drop(columns="date").to_numpy(dtype=float)).all():
            raise DataError("داده روزانه شامل مقدار نامعتبر است.")
        if (df[["volume", "trades"]] < 0).any().any():
            raise DataError("تعداد یا حجم معامله منفی است.")
        df = df.loc[(df["trades"] > 0) & (df["volume"] > 0)].copy()
        if df.empty:
            raise DataError("هیچ روز دارای معامله در پاسخ وجود ندارد.")
        if (df[["open", "high", "low", "close", "final"]] <= 0).any().any() or (df["high"] < df["low"]).any():
            raise DataError("قیمت‌های روزانه معتبر نیستند.")
        if df["date"].duplicated().any():
            raise DataError("برای یک تاریخ چند رکورد وجود دارد؛ تحلیل متوقف شد.")
        return df.sort_values("date").reset_index(drop=True)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("امکان خواندن ساختار داده روزانه وجود ندارد.") from exc


def prepare_prices(raw: pd.DataFrame, *, adjusted: bool = True,
                   close_basis: str = "final", include_today: bool = False,
                   today: date | None = None) -> pd.DataFrame:
    if close_basis not in ("final", "close"):
        raise DataError("مبنای قیمت نامعتبر است.")
    today = today or tehran_today()
    dates = raw["date"].dt.date
    mask = dates <= today if include_today else dates < today
    df = raw.loc[mask].copy().reset_index(drop=True)
    if df.empty:
        raise DataError("داده‌ای برای روزهای کامل‌شده وجود ندارد.")
    # Never use an unconfirmed current bar to adjust prior history.
    factor = pd.Series(1.0, index=df.index)
    if adjusted and len(df) > 1:
        next_reference = df["yesterday"].shift(-1)
        if (next_reference.iloc[:-1] <= 0).any():
            raise DataError("قیمت مرجع برای تعدیل کامل نیست. داده خام را بررسی کنید.")
        ratios = next_reference / df["final"]
        ratios.iloc[-1] = 1.0
        factor = ratios.iloc[::-1].cumprod().iloc[::-1]
        if not np.isfinite(factor).all() or (factor <= 0).any():
            raise DataError("ضریب تعدیل معتبر نیست.")
    df["adjustment_factor"] = factor
    for col in ("open", "high", "low", "close", "final"):
        df[col] = df[col].astype(float) * factor
    df["last_trade"] = df["close"]
    df["close"] = df[close_basis]
    return df


class HistoryCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def path(self, code: str) -> Path:
        return self.directory / f"{instrument_code(code)}.json"

    def save(self, code: str, label: str, payload: dict) -> dict:
        parse_history(payload)  # Reject invalid responses before replacing good data.
        document = {"version": 1, "code": instrument_code(code), "label": str(label),
                    "fetched_at": datetime.now(timezone.utc).isoformat(), "payload": payload}
        write_json_atomic(self.path(code), document)
        return document

    def load(self, code: str) -> dict:
        try:
            doc = json.loads(self.path(code).read_text(encoding="utf-8"))
            if doc["version"] != 1 or doc["code"] != instrument_code(code):
                raise DataError("شناسه یا نسخه فایل ذخیره‌شده معتبر نیست.")
            parse_history(doc["payload"])
            datetime.fromisoformat(doc["fetched_at"])
            return doc
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise DataError("فایل ذخیره‌شده موجود نیست یا قابل خواندن نیست.") from exc
