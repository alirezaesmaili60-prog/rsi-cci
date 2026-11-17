"""Closed Tehran-session candles, without invented empty/overnight bars."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .data import DataError


TIMEFRAMES = {"daily": "روزانه", "weekly": "هفتگی", "2h": "۲ساعته"}
TEHRAN = ZoneInfo("Asia/Tehran")


@dataclass(frozen=True)
class Session:
    start: str = "09:00"
    end: str = "12:30"
    include_short: bool = True

    def __post_init__(self):
        try:
            opening, closing = time.fromisoformat(self.start), time.fromisoformat(self.end)
            if opening >= closing or len(self.start) != 5 or len(self.end) != 5:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ValueError("ساعت جلسه باید HH:MM باشد و پایان، بعد از شروع همان روز باشد.") from exc

    def bounds(self, day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
        return (pd.Timestamp(datetime.combine(day, time.fromisoformat(self.start)), tz=TEHRAN),
                pd.Timestamp(datetime.combine(day, time.fromisoformat(self.end)), tz=TEHRAN))


def label_timestamp(value, timeframe: str) -> str:
    stamp = pd.Timestamp(value)
    return stamp.isoformat() if timeframe == "2h" else stamp.date().isoformat()


def weekly_prices(daily: pd.DataFrame, *, today: date, include_today: bool = False) -> pd.DataFrame:
    """Weeks end Wednesday. Thursday/Friday, if traded, start the next week."""
    frame = daily.copy()
    frame["week_end"] = frame["date"].dt.to_period("W-WED").dt.end_time.dt.normalize()
    allowed = frame["week_end"].dt.date < today
    if include_today:
        allowed |= frame["week_end"].dt.date == today
    frame = frame.loc[allowed]
    if frame.empty:
        raise DataError("هنوز هفته معاملاتی کامل‌شده‌ای در این سابقه وجود ندارد.")
    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    for column, rule in {"final": "last", "last_trade": "last", "trades": "sum"}.items():
        if column in frame:
            aggregation[column] = rule
    result = frame.groupby("week_end", sort=True).agg(aggregation).reset_index().rename(columns={"week_end": "date"})
    result["last_trade_date"] = frame.groupby("week_end")["date"].max().to_numpy()
    result["timeframe"] = "weekly"
    return result


def two_hour_prices(minutes: pd.DataFrame, daily: pd.DataFrame, session: Session) -> pd.DataFrame:
    """Use genuine minute OHLC and daily adjustment factors; close is last trade."""
    rows = []
    factors = {stamp.date(): float(factor) for stamp, factor in
               zip(daily["date"], daily["adjustment_factor"])}
    for day, group in minutes.groupby(minutes["date"].dt.date, sort=True):
        opening, closing = session.bounds(day)
        if day not in factors:
            raise DataError("مرجع روزانه برای تعدیل داده درون‌روزی موجود نیست.")
        if (group["first_trade_at"] < opening).any() or (group["last_trade_at"] > closing).any():
            raise DataError(f"معامله خارج از جلسه {session.start} تا {session.end} در {day} وجود دارد؛ ساعت جلسه را اصلاح کنید.")
        duration = (closing - opening).total_seconds() / 60
        work = group.sort_values("date").copy()
        # A trade exactly at session close belongs to the last session bar.
        elapsed = (work["date"] - opening).dt.total_seconds() / 60
        slots = (elapsed // 120).astype(int)
        if duration % 120 == 0:
            slots = slots.clip(upper=int(duration / 120) - 1)
        work["slot"] = slots
        for slot, part in work.groupby("slot", sort=True):
            start = opening + pd.Timedelta(minutes=int(slot) * 120)
            end = min(start + pd.Timedelta(hours=2), closing)
            bar_minutes = int((end - start).total_seconds() / 60)
            if bar_minutes < 120 and not session.include_short:
                continue
            factor = factors[day]
            rows.append({"date": end, "bar_start": start, "open": float(part.iloc[0]["open"]) * factor,
                         "high": float(part["high"].max()) * factor, "low": float(part["low"].min()) * factor,
                         "close": float(part.iloc[-1]["close"]) * factor,
                         "last_trade": float(part.iloc[-1]["close"]) * factor,
                         "volume": int(part["volume"].sum()), "value": float(part["value"].sum()) * factor,
                         "trades": int(part["trades"].sum()), "bar_minutes": bar_minutes,
                         "short_session_bar": bar_minutes < 120, "adjustment_factor": factor,
                         "last_trade_at": part.iloc[-1]["last_trade_at"], "timeframe": "2h"})
    if not rows:
        raise DataError("کندل ۲ساعته‌ای مطابق ساعت و سیاست کندل کوتاه تشکیل نشد.")
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def catalog_reference(day_string: str, timeframe: str, *, today: date,
                      include_today: bool, session: Session) -> str:
    if not day_string:
        return ""
    day = date.fromisoformat(day_string)
    if day > today or day == today and not include_today:
        return ""
    if timeframe == "daily":
        return day.isoformat()
    if timeframe == "weekly":
        ending = day + timedelta(days=(2 - day.weekday()) % 7)
        return ending.isoformat() if ending < today or ending == today and include_today else ""
    opening, closing = session.bounds(day)
    if not session.include_short:
        full = int((closing - opening).total_seconds() // 7200)
        if not full:
            return ""
        closing = opening + pd.Timedelta(hours=full * 2)
    return closing.isoformat()
