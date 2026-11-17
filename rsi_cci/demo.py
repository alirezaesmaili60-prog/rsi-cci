"""Deterministic artificial prices for offline demonstrations, never market data."""

from datetime import timedelta

import numpy as np
import pandas as pd

from .data import tehran_today


def demo_history() -> dict:
    dates = pd.date_range(end=tehran_today() - timedelta(days=1), periods=420)
    dates = dates[dates.weekday.isin([5, 6, 0, 1, 2])][-260:]
    x = np.arange(len(dates))
    prices = np.round(18000 + 10 * x + 2400 * np.sin(x / 9) + 400 * np.sin(x / 2)).astype(int)
    rows = []
    for i, (day, price) in enumerate(zip(dates, prices)):
        yesterday = int(prices[max(0, i - 1)])
        rows.append({"dEven": int(day.strftime("%Y%m%d")), "priceFirst": yesterday,
                     "priceMax": int(max(price, yesterday) + 150),
                     "priceMin": int(min(price, yesterday) - 150),
                     "pDrCotVal": int(price), "pClosing": int(price),
                     "priceYesterday": yesterday, "qTotTran5J": int(300000 + i * 170),
                     "zTotTran": 200 + i})
    return {"closingPriceDaily": rows[::-1]}


def demo_two_hour(daily, session, days=30):
    """Explicitly synthetic minute data for offline UI demonstrations only."""
    from .timeframes import two_hour_prices
    frames = []
    selected = daily if days == 0 else daily.tail(days)
    for _, row in selected.iterrows():
        opening, closing = session.bounds(row["date"].date())
        times = pd.date_range(opening, closing, freq="min", inclusive="left")
        price = float(row["close"]) / float(row["adjustment_factor"]) * (1 + .003 * np.sin(np.arange(len(times)) / 17))
        frame = pd.DataFrame({"date": times, "open": price, "high": price, "low": price, "close": price,
                              "volume": 100, "value": price * 100, "trades": 1,
                              "first_trade_at": times, "last_trade_at": times})
        frames.append(frame)
    return two_hour_prices(pd.concat(frames, ignore_index=True), daily, session)
