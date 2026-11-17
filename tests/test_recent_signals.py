"""Recent-signal screening counts actual bars, inclusively from bar zero."""

from dataclasses import asdict

import pandas as pd
import unittest

from rsi_cci.market import ScanOptions, filter_results, summarize


def indicator_result(age, timeframe="daily"):
    if timeframe == "weekly":
        dates = pd.date_range(end="2026-11-11", periods=24, freq="W-WED")
    elif timeframe == "2h":
        days = pd.bdate_range(end="2026-11-16", periods=12)
        dates = pd.DatetimeIndex([pd.Timestamp(f"{day.date()} {clock}", tz="Asia/Tehran")
                                  for day in days for clock in ("11:00", "12:30")])
    else:
        dates = pd.date_range(end="2026-11-16", periods=24)
    frame = pd.DataFrame({"date": dates, "close": 100., "rsi": 40., "cci": -10., "entry_conditions": "",
                          "action": "", "reason": "", "holding": False, "reached_100": False})
    position = len(frame) - 1 - age
    frame.loc[position, ["entry_conditions", "action", "reason", "rsi", "cci", "close"]] = ["A2", "BUY", "A2", 65., 120., 110.]
    return frame


def recent_snapshot():
    rows = []
    for timeframe in ("daily", "weekly", "2h"):
        for age in (0, 2, 3, 4):
            row = summarize(indicator_result(age, timeframe), 1, timeframe)
            row.update(code=str(age), symbol=f"سهم{age}", market="بورس", cci_period=14,
                       timeframe=timeframe, status="OK", stale=False, error="", source="cache", fetched_at="")
            rows.append(row)
    return {"version": 1, "state": "done", "started_at": "2026-11-17T00:00:00+00:00", "today": "2026-11-17",
            "options": asdict(ScanOptions(periods=(14,), timeframes=("daily", "weekly", "2h"))),
            "rows": rows, "total": 4, "processed": 4, "failed": 0, "downloaded": 0, "cached": 4,
            "universe_total": 4, "excluded": 0, "unclassified": 0, "error": ""}


class RecentSignalTests(unittest.TestCase):
    def test_current_two_and_three_previous_are_inclusive_for_each_timeframe(self):
        snapshot = recent_snapshot()
        for timeframe in ("daily", "weekly", "2h"):
            for maximum, expected in [(0, {"0"}), (2, {"0", "2"}), (3, {"0", "2", "3"})]:
                with self.subTest(timeframe=timeframe, maximum=maximum):
                    result = filter_results(snapshot, timeframe=timeframe, max_bars_ago=maximum)
                    self.assertEqual(set(result["code"]), expected)
                    self.assertTrue(result["signal_bars_ago"].between(0, maximum).all())

    def test_window_can_expand_without_fetch_even_if_original_scan_requested_one_bar(self):
        snapshot = recent_snapshot()
        self.assertEqual(snapshot["options"]["lookback"], 1)
        result = filter_results(snapshot, timeframe="weekly", max_bars_ago=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(set(result["lookback"]), {4})
        self.assertEqual(set(result["scan_lookback"]), {1})

    def test_signal_values_and_latest_values_are_kept_separate(self):
        result = filter_results(recent_snapshot(), timeframe="daily", max_bars_ago=3)
        row = result.loc[result["code"].eq("3")].iloc[0]
        self.assertEqual(row["signal_rsi"], 65)
        self.assertEqual(row["signal_cci"], 120)
        self.assertEqual(row["rsi"], 40)
        self.assertEqual(row["cci"], -10)

    def test_historical_entry_remains_visible_after_a_later_exit(self):
        frame = indicator_result(3)
        frame.loc[len(frame) - 2, ["action", "reason"]] = ["EXIT", "CCI_DOWN_100"]
        row = summarize(frame, 1)
        row.update(code="1", symbol="نماد", timeframe="daily", cci_period=14, status="OK", stale=False)
        result = filter_results({"rows": [row], "options": {"lookback": 1}}, max_bars_ago=3)
        self.assertEqual(len(result), 1)
        self.assertFalse(result.iloc[0]["holding"])
        self.assertTrue(result.iloc[0]["exit_signal_date"])

    def test_most_recent_matching_signal_is_selected_once(self):
        frame = indicator_result(3)
        frame.loc[len(frame) - 1, ["entry_conditions", "rsi", "cci"]] = ["A1", 60, 5]
        row = summarize(frame, 4)
        self.assertEqual(row["entry_bars_ago"], 0)
        self.assertEqual(row["entry_conditions"], "A1")

    def test_invalid_window_is_rejected(self):
        for maximum in (-1, 10):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                filter_results(recent_snapshot(), max_bars_ago=maximum)


if __name__ == "__main__":
    unittest.main()
