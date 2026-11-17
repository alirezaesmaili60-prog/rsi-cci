"""Validate complete trades against daily volume and retain compact minute OHLC."""

import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .data import DataError, HistoryCache, instrument_code, write_json_atomic
from .timeframes import TEHRAN


def trade_date(value) -> date:
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise DataError("تاریخ داده درون‌روزی باید YYYYMMDD باشد.") from exc


class IntradayDataError(DataError):
    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


def minute_prices(payload: dict, day: date, expected_volume: float) -> pd.DataFrame:
    rows = payload.get("tradeHistory") if isinstance(payload, dict) else None
    stats = {"received_records": len(rows) if isinstance(rows, list) else 0,
             "cancelled_records_removed": 0, "zero_volume_records_removed": 0, "exact_duplicates_removed": 0}

    def fail(code, message, frame=None, mask=None):
        sample = []
        columns = list(frame.columns) if frame is not None else []
        if frame is not None:
            selected = frame.loc[mask] if mask is not None else frame
            fields = [name for name in ("nTran", "hEven", "qTitTran", "pTran", "canceled", "dEven") if name in selected]
            sample = json.loads(selected[fields].head(12).to_json(orient="records"))
        raise IntradayDataError(f"ریزمعاملات {day}: {message}",
                                {"day": day.isoformat(), "reason": code, "message": message,
                                 "expected_daily_volume": float(expected_volume), "columns": columns,
                                 "normalization": dict(stats), "sample_records": sample})

    if not isinstance(rows, list) or not rows:
        fail("EMPTY_RESPONSE", "منبع سابقه معتبر ندارد؛ کندل ساختگی تولید نشد.")
    try:
        frame = pd.DataFrame(rows)
        required = {"hEven", "nTran", "qTitTran", "pTran", "canceled"}
        if not required <= set(frame):
            fail("MISSING_COLUMNS", "ستون‌های لازم موجود نیست: " + ", ".join(sorted(required - set(frame))), frame)
        for col in required:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if not frame["canceled"].isin([0, 1]).all():
            fail("INVALID_CANCELLATION", "مقدار canceled باید صفر یا یک باشد.", frame, ~frame["canceled"].isin([0, 1]))
        invalid_id = ~np.isfinite(frame["nTran"]) | (frame["nTran"] < 0) | frame["nTran"].mod(1).ne(0)
        if invalid_id.any():
            fail("INVALID_TRADE_ID", "شناسه معامله نامعتبر است.", frame, invalid_id)
        # An after-session cancellation may appear separately from the original
        # trade. Invalidate that identity, not just the cancellation marker row.
        cancelled_ids = frame.loc[frame["canceled"].eq(1), "nTran"]
        removed = frame["nTran"].isin(cancelled_ids)
        stats["cancelled_records_removed"] = int(removed.sum())
        frame = frame.loc[~removed].copy()
        zero_quantity = frame["qTitTran"].eq(0)
        stats["zero_volume_records_removed"] = int(zero_quantity.sum())
        frame = frame.loc[~zero_quantity].copy()
        if frame.empty or not np.isfinite(frame[list(required)].to_numpy(dtype=float)).all():
            fail("MISSING_NUMERIC_VALUE", "رکورد معتبر باقی نماند یا فیلد عددی خالی/نامعتبر است.", frame,
                 ~np.isfinite(frame[list(required)].to_numpy(dtype=float)).all(axis=1))
        bad_price = (frame[["qTitTran", "pTran"]] <= 0).any(axis=1)
        if bad_price.any():
            fail("INVALID_PRICE_VOLUME", "قیمت یا حجم معامله مثبت نیست.", frame, bad_price)
        for col in ("hEven", "qTitTran", "nTran"):
            if not (frame[col] % 1 == 0).all():
                fail("NONINTEGER_FIELD", f"فیلد {col} عدد صحیح نیست.", frame, frame[col].mod(1).ne(0))
        if "dEven" in frame:
            recorded = pd.to_numeric(frame["dEven"], errors="raise").fillna(0)
            if not recorded.isin([0, int(day.strftime("%Y%m%d"))]).all():
                fail("WRONG_DATE", "تاریخ یک رکورد با روز درخواست‌شده متفاوت است.", frame,
                     ~recorded.isin([0, int(day.strftime("%Y%m%d"))]))
        before = len(frame)
        frame = frame.drop_duplicates(subset=["nTran", "hEven", "qTitTran", "pTran"])
        stats["exact_duplicates_removed"] = before - len(frame)
        duplicates = frame["nTran"].duplicated(keep=False)
        if duplicates.any():
            ids = ", ".join(str(int(value)) for value in frame.loc[duplicates, "nTran"].unique()[:6])
            fail("CONFLICTING_TRADE_VERSIONS", "چند نسخه متفاوت برای شناسه معامله وجود دارد: " + ids, frame, duplicates)
        if not math.isclose(float(frame["qTitTran"].sum()), float(expected_volume), rel_tol=0, abs_tol=0.01):
            stats["observed_volume"] = float(frame["qTitTran"].sum())
            fail("VOLUME_MISMATCH", f"حجم معاملات معتبر ({stats['observed_volume']:g}) با روزانه ({expected_volume:g}) برابر نیست؛ "
                 "دریافت روزانه و درون‌روزی را به‌روز کنید.", frame)
        clocks = frame["hEven"].astype(int).astype(str).str.zfill(6)
        stamps = pd.to_datetime(day.isoformat() + " " + clocks, format="%Y-%m-%d %H%M%S", errors="coerce")
        if stamps.isna().any():
            fail("INVALID_CLOCK", "ساعت hEven معتبر نیست.", frame, stamps.isna())
        frame["timestamp"] = stamps.dt.tz_localize(TEHRAN)
        frame = frame.sort_values(["timestamp", "nTran"])
        frame["minute"] = frame["timestamp"].dt.floor("min")
        frame["value"] = frame["qTitTran"] * frame["pTran"]
        result = frame.groupby("minute", sort=True).agg(
            open=("pTran", "first"), high=("pTran", "max"), low=("pTran", "min"), close=("pTran", "last"),
            volume=("qTitTran", "sum"), value=("value", "sum"), trades=("nTran", "count"),
            first_trade_at=("timestamp", "min"), last_trade_at=("timestamp", "max"))
        result = result.reset_index().rename(columns={"minute": "date"})
        result.attrs["normalization"] = stats
        return result
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, DataError):
            raise
        raise IntradayDataError(f"ریزمعاملات {day}: تبدیل داده ناموفق بود ({str(exc)[:200]}).",
                                {"day": day.isoformat(), "reason": "CONVERSION_ERROR", "message": str(exc)[:300],
                                 "normalization": stats}) from exc


class IntradayCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory) / "intraday"

    def path(self, code: str, day: date) -> Path:
        return self.directory / instrument_code(code) / (day.strftime("%Y%m%d") + ".json")

    def save(self, code: str, day: date, minutes: pd.DataFrame, fetched_at: str):
        records = minutes.copy()
        for col in ("date", "first_trade_at", "last_trade_at"):
            records[col] = records[col].map(lambda x: x.isoformat())
        write_json_atomic(self.path(code, day), {"version": 1, "code": instrument_code(code), "day": day.isoformat(),
                                                "fetched_at": fetched_at, "source": "TSETMC Trade/GetTradeHistory",
                                                "normalization": minutes.attrs.get("normalization", {}),
                                                "minutes": records.to_dict(orient="records")})

    def load(self, code: str, day: date, expected_volume: float) -> pd.DataFrame:
        try:
            doc = json.loads(self.path(code, day).read_text(encoding="utf-8"))
            if doc["version"] != 1 or doc["code"] != instrument_code(code) or doc["day"] != day.isoformat():
                raise ValueError("Wrong intraday cache identity")
            frame = pd.DataFrame(doc["minutes"])
            required = {"date", "first_trade_at", "last_trade_at", "open", "high", "low", "close", "volume", "value", "trades"}
            if frame.empty or not required <= set(frame):
                raise ValueError("Missing minute columns")
            for col in ("date", "first_trade_at", "last_trade_at"):
                frame[col] = pd.to_datetime(frame[col], utc=True).dt.tz_convert(TEHRAN)
            if not (frame["date"].dt.date == day).all() or frame["date"].duplicated().any():
                raise ValueError("Wrong minute dates")
            columns = ["open", "high", "low", "close", "volume", "value", "trades"]
            for col in columns:
                frame[col] = pd.to_numeric(frame[col], errors="raise")
            numeric = frame[columns].to_numpy(dtype=float)
            if not np.isfinite(numeric).all() or (numeric <= 0).any() or (frame["high"] < frame["low"]).any():
                raise ValueError("Invalid minute prices")
            if ((frame["first_trade_at"] < frame["date"]) |
                (frame["last_trade_at"] >= frame["date"] + pd.Timedelta(minutes=1)) |
                (frame["first_trade_at"] > frame["last_trade_at"])).any():
                raise ValueError("Invalid trade timestamps")
            if not math.isclose(float(frame["volume"].sum()), float(expected_volume), rel_tol=0, abs_tol=0.01):
                raise ValueError("Daily volume changed")
            return frame.sort_values("date").reset_index(drop=True)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise DataError(f"داده درون‌روزی معتبر {day} در فایل محلی نیست؛ دریافت درون‌روزی را اجرا کنید.") from exc


def collect_minutes(cache: HistoryCache, code: str, raw: pd.DataFrame, daily: pd.DataFrame, *,
                    days: int = 30, client=None, mode: str = "offline", cancel=None,
                    delay_seconds: float = 0.4, progress=None) -> pd.DataFrame:
    """All selected days must be present and volume-verified; no silent gaps."""
    if days < 0:
        raise ValueError("تعداد روز درون‌روزی نباید منفی باشد.")
    store = IntradayCache(cache.directory)
    selected = daily if days == 0 else daily.tail(days)
    volumes = {row["date"].date(): float(row["volume"]) for _, row in raw.iterrows()}
    frames = []
    for number, stamp in enumerate(reversed(selected["date"].tolist()), 1):
        if cancel is not None and cancel.is_set():
            raise InterruptedError()
        day = stamp.date()
        source = "cache"
        try:
            if mode == "refresh":
                raise DataError("Refresh requested")
            frame = store.load(code, day, volumes[day])
        except DataError:
            if mode == "offline" or client is None:
                raise DataError(f"داده درون‌روزی {day} موجود نیست؛ حالت دریافت یا ادامه را اجرا کنید.")
            if cancel is not None:
                if cancel.wait(delay_seconds):
                    raise InterruptedError()
            elif delay_seconds:
                import time
                time.sleep(delay_seconds)
            payload = client.trades(code, int(day.strftime("%Y%m%d")))
            try:
                frame = minute_prices(payload, day, volumes[day])
            except IntradayDataError as exc:
                path = cache.directory / "diagnostics" / instrument_code(code) / (day.strftime("%Y%m%d") + "-trades.json")
                try:
                    write_json_atomic(path, {"code": instrument_code(code), **exc.details})
                except OSError:
                    raise exc
                raise IntradayDataError(str(exc) + f" گزارش جزئیات: {path}", exc.details) from exc
            from datetime import timezone
            store.save(code, day, frame, datetime.now(timezone.utc).isoformat())
            source = "download"
        frames.append(frame)
        if progress:
            progress(day, source, number, len(selected))
    if not frames:
        raise DataError("روز معاملاتی کاملی برای دریافت درون‌روزی وجود ندارد.")
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
