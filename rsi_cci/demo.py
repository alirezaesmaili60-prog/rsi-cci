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
