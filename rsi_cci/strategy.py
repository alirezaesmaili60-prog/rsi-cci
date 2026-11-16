"""End-of-day signals and a hypothetical, single-position state machine.

No order execution, fill assumptions, or performance claims are made here.
"""

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from .indicators import cci, rsi


@dataclass(frozen=True)
class StrategyConfig:
    cci_period: int = 14
    entries: tuple[str, ...] = ("A1", "A2", "B")

    def __post_init__(self):
        if self.cci_period not in (14, 20):
            raise ValueError("CCI period must be 14 or 20")
        if not self.entries or not set(self.entries) <= {"A1", "A2", "B"}:
            raise ValueError("Select at least one entry rule: A1, A2, B")


def crosses_up(previous: float, current: float, level: float) -> bool:
    return previous <= level < current


def crosses_down(previous: float, current: float, level: float) -> bool:
    return previous >= level > current


def signal_states(rsi_values: Iterable[float], cci_values: Iterable[float],
                  entries: tuple[str, ...]) -> pd.DataFrame:
    """Replay from flat. +100 history belongs to the current position only."""
    rsis, ccis = list(rsi_values), list(cci_values)
    if len(rsis) != len(ccis):
        raise ValueError("RSI and CCI lengths differ")
    rows = []
    holding, reached_100 = False, False
    entry_index: int | None = None
    for i, (r, c) in enumerate(zip(rsis, ccis)):
        action, reason = "", ""
        triggers = []
        valid = i > 0 and np.isfinite([rsis[i - 1], ccis[i - 1], r, c]).all()
        if valid:
            prev_r, prev_c = rsis[i - 1], ccis[i - 1]
            if "A1" in entries and r > 50 and crosses_up(prev_c, c, 0):
                triggers.append("A1")
            if "A2" in entries and r > 50 and crosses_up(prev_c, c, 100):
                triggers.append("A2")
            if "B" in entries and c > 0 and crosses_up(prev_r, r, 50):
                triggers.append("B")
            if holding:
                reached_100 = reached_100 or c > 100
                if reached_100 and crosses_down(prev_c, c, 100):
                    action, reason = "EXIT", "CCI_DOWN_100"
                elif not reached_100 and crosses_down(prev_c, c, -100):
                    action, reason = "EXIT", "CCI_STOP_MINUS_100"
                if action:
                    holding, reached_100, entry_index = False, False, None
            else:
                if triggers:
                    action, reason = "BUY", "+".join(triggers)
                    holding, reached_100, entry_index = True, c > 100, i
        rows.append({"action": action, "reason": reason, "entry_conditions": "+".join(triggers), "holding": holding,
                     "reached_100": reached_100, "entry_index": entry_index})
    return pd.DataFrame(rows, columns=["action", "reason", "entry_conditions", "holding", "reached_100", "entry_index"])


def analyze(prices: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Prices must be finite and sorted, one completed daily bar per row."""
    if prices.empty or not prices["date"].is_monotonic_increasing or prices["date"].duplicated().any():
        raise ValueError("Prices must be nonempty, unique and sorted by date")
    result = prices.reset_index(drop=True).copy()
    for col in ("high", "low", "close"):
        if not np.isfinite(result[col].to_numpy(dtype=float)).all():
            raise ValueError(f"Nonfinite {col}")
    result["rsi"] = rsi(result["close"], 14)
    result["cci"] = cci(result["high"], result["low"], result["close"], config.cci_period)
    states = signal_states(result["rsi"], result["cci"], config.entries)
    return pd.concat([result, states], axis=1)
