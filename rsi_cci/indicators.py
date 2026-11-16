"""Indicators with explicit warm-up and Wilder's original RSI seed."""

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    if period < 2:
        raise ValueError("period must be at least 2")
    values = close.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("close must contain finite values")
    result = np.full(len(values), np.nan)
    if len(values) <= period:
        return pd.Series(result, index=close.index, name="rsi")
    differences = np.diff(values)
    gains, losses = np.maximum(differences, 0), np.maximum(-differences, 0)
    gain, loss = gains[:period].mean(), losses[:period].mean()

    def score() -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = score()
    for i in range(period + 1, len(values)):
        gain = (gain * (period - 1) + gains[i - 1]) / period
        loss = (loss * (period - 1) + losses[i - 1]) / period
        result[i] = score()
    return pd.Series(result, index=close.index, name="rsi")


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    if period < 2:
        raise ValueError("period must be at least 2")
    typical = (high + low + close) / 3.0
    mean = typical.rolling(period).mean()
    deviation = typical.rolling(period).apply(
        lambda window: np.abs(window - window.mean()).mean(), raw=True
    )
    result = (typical - mean) / (0.015 * deviation.replace(0, np.nan))
    # Flat windows are neutral, whereas incomplete windows stay NaN.
    return result.mask(deviation.eq(0), 0.0).rename("cci")
