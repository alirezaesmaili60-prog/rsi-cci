"""Shared candle preparation for single-symbol UI, market scans and CLI."""

from datetime import date

from .data import DataError, prepare_prices, tehran_today
from .intraday import collect_minutes
from .timeframes import Session, TIMEFRAMES, two_hour_prices, weekly_prices


def prepare_timeframe(raw, timeframe="daily", *, adjusted=True, close_basis="final", include_today=False,
                      today: date | None = None, cache=None, code="", client=None, mode="offline",
                      intraday_days=30, session: Session | None = None, cancel=None,
                      delay_seconds=0.4, progress=None):
    if timeframe not in TIMEFRAMES:
        raise ValueError("تایم‌فریم نامعتبر است.")
    today = today or tehran_today()
    daily = prepare_prices(raw, adjusted=adjusted, close_basis=close_basis, include_today=include_today, today=today)
    if timeframe == "daily":
        daily["timeframe"] = "daily"
        return daily
    if timeframe == "weekly":
        return weekly_prices(daily, today=today, include_today=include_today)
    if cache is None:
        raise DataError("پوشه داده درون‌روزی تعیین نشده است.")
    minutes = collect_minutes(cache, code, raw, daily, days=intraday_days, client=client, mode=mode,
                              cancel=cancel, delay_seconds=delay_seconds, progress=progress)
    return two_hour_prices(minutes, daily, session or Session())
