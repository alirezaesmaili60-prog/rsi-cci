import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from rsi_cci.data import DataError, HistoryCache, parse_history, prepare_prices
from rsi_cci.intraday import IntradayCache, collect_minutes, minute_prices
from rsi_cci.strategy import StrategyConfig, analyze
from rsi_cci.timeframes import Session, catalog_reference, two_hour_prices, weekly_prices
from rsi_cci.workflow import prepare_timeframe
from tests.market_fixtures import signal_history


def trades_for_day(raw, day):
    row = next(row for row in raw["closingPriceDaily"] if row["dEven"] == int(day))
    volume = int(row["qTotTran5J"])
    quantity = volume // 4
    base = float(row["pDrCotVal"])
    return {"tradeHistory": [{"nTran": i + 1, "dEven": int(day), "hEven": clock,
                               "qTitTran": quantity if i < 3 else volume - 3 * quantity,
                               "pTran": base * change, "canceled": 0}
                              for i, (clock, change) in enumerate(zip([90000, 105959, 110000, 122959], [.995, 1, 1.003, 1]))]}


def simple_trades(clocks=(90000, 105959, 110000, 123000)):
    return {"tradeHistory": [{"nTran": i + 1, "hEven": clock, "qTitTran": i + 1,
                               "pTran": (i + 1) * 10, "canceled": 0, "dEven": 0}
                              for i, clock in enumerate(clocks)]}


def daily_factors(days, factors=None):
    return pd.DataFrame({"date": pd.to_datetime(days), "adjustment_factor": factors or [1.] * len(days)})


class WeeklyTests(unittest.TestCase):
    def prices(self):
        return pd.DataFrame({"date": pd.to_datetime(["2026-11-07", "2026-11-08", "2026-11-11", "2026-11-14", "2026-11-15"]),
                             "open": [10, 11, 12, 20, 21], "high": [15, 16, 18, 25, 26],
                             "low": [8, 9, 10, 18, 19], "close": [12, 13, 14, 22, 23], "volume": [1, 2, 3, 4, 5]})

    def test_wednesday_week_end_and_ohlcv(self):
        weekly = weekly_prices(self.prices(), today=date(2026, 11, 17))
        self.assertEqual(len(weekly), 1)
        row = weekly.iloc[0]
        self.assertEqual(row["date"], pd.Timestamp("2026-11-11"))
        self.assertEqual([row[c] for c in ["open", "high", "low", "close", "volume"]], [10, 18, 8, 14, 6])

    def test_confirming_tuesday_does_not_finalize_current_week(self):
        self.assertEqual(len(weekly_prices(self.prices(), today=date(2026, 11, 17), include_today=True)), 1)

    def test_wednesday_requires_explicit_session_confirmation(self):
        with self.assertRaises(DataError):
            weekly_prices(self.prices().iloc[:3], today=date(2026, 11, 11))
        self.assertEqual(len(weekly_prices(self.prices().iloc[:3], today=date(2026, 11, 11), include_today=True)), 1)

    def test_holiday_and_missing_days_do_not_create_fake_daily_prices(self):
        weekly = weekly_prices(self.prices().iloc[:2], today=date(2026, 11, 12))
        self.assertEqual(weekly.iloc[0]["date"], pd.Timestamp("2026-11-11"))
        self.assertEqual(weekly.iloc[0]["last_trade_date"], pd.Timestamp("2026-11-08"))
        self.assertEqual(weekly.iloc[0]["volume"], 3)

    def test_indicators_are_computed_after_weekly_aggregation(self):
        raw = parse_history(signal_history())
        weekly = prepare_timeframe(raw, "weekly")
        result = analyze(weekly, StrategyConfig())
        self.assertLess(len(result), len(raw) / 3)
        self.assertTrue(result["rsi"].iloc[:14].isna().all())
        self.assertTrue(result["rsi"].iloc[14:].notna().all())


class IntradayTests(unittest.TestCase):
    def test_minute_ohlc_order_and_cancelled_trade_filter(self):
        payload = {"tradeHistory": [{"nTran": 2, "hEven": 90020, "qTitTran": 3, "pTran": 20, "canceled": 0},
                                     {"nTran": 1, "hEven": 90010, "qTitTran": 2, "pTran": 10, "canceled": 0},
                                     {"nTran": 3, "hEven": 90030, "qTitTran": 50, "pTran": 1, "canceled": 1}]}
        row = minute_prices(payload, date(2026, 11, 16), 5).iloc[0]
        self.assertEqual([row[c] for c in ["open", "high", "low", "close", "volume", "value", "trades"]], [10, 20, 10, 20, 5, 80, 2])

    def test_incomplete_volume_and_duplicate_trade_are_rejected(self):
        with self.assertRaises(DataError):
            minute_prices(simple_trades(), date(2026, 11, 16), 11)
        payload = simple_trades()
        payload["tradeHistory"][1]["nTran"] = 1
        with self.assertRaises(DataError):
            minute_prices(payload, date(2026, 11, 16), 10)

    def test_bad_time_wrong_day_and_unknown_cancellation_rejected(self):
        for change in [{"hEven": 126000}, {"dEven": 20261115}, {"canceled": 3}, {"pTran": 0}]:
            with self.subTest(change=change), self.assertRaises(DataError):
                payload = simple_trades()
                payload["tradeHistory"][0].update(change)
                minute_prices(payload, date(2026, 11, 16), 10)

    def test_two_hour_boundaries_closing_trade_and_short_bar(self):
        minutes = minute_prices(simple_trades(), date(2026, 11, 16), 10)
        bars = two_hour_prices(minutes, daily_factors(["2026-11-16"]), Session())
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars["bar_minutes"].tolist(), [120, 90])
        self.assertEqual(bars["short_session_bar"].tolist(), [False, True])
        self.assertEqual(bars["open"].tolist(), [10, 30])
        self.assertEqual(bars["close"].tolist(), [20, 40])
        self.assertEqual(bars["volume"].tolist(), [3, 7])
        self.assertEqual(bars["date"].dt.strftime("%H:%M").tolist(), ["11:00", "12:30"])
        self.assertEqual(str(bars["date"].dt.tz), "Asia/Tehran")

    def test_no_overnight_candle_and_daily_factor_applies_to_prices_not_volume(self):
        minutes = pd.concat([minute_prices(simple_trades(), date(2026, 11, d), 10) for d in (16, 17)])
        bars = two_hour_prices(minutes, daily_factors(["2026-11-16", "2026-11-17"], [.5, 1]), Session())
        self.assertEqual(len(bars), 4)
        self.assertEqual(bars["open"].tolist(), [5, 15, 10, 30])
        self.assertEqual(bars["volume"].tolist(), [3, 7, 3, 7])

    def test_full_bars_only_and_exact_full_session_close(self):
        minutes = minute_prices(simple_trades(), date(2026, 11, 16), 10)
        self.assertEqual(len(two_hour_prices(minutes, daily_factors(["2026-11-16"]), Session(include_short=False))), 1)
        minutes = minute_prices(simple_trades((90000, 105959, 110000, 130000)), date(2026, 11, 16), 10)
        bars = two_hour_prices(minutes, daily_factors(["2026-11-16"]), Session(end="13:00"))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars["bar_minutes"].tolist(), [120, 120])
        self.assertEqual(bars["volume"].sum(), 10)

    def test_outside_session_is_visible_error_not_silent_data_loss(self):
        for clocks in [(85959, 100000, 110000, 120000), (90000, 100000, 110000, 123001)]:
            minutes = minute_prices(simple_trades(clocks), date(2026, 11, 16), 10)
            with self.subTest(clocks=clocks), self.assertRaises(DataError):
                two_hour_prices(minutes, daily_factors(["2026-11-16"]), Session())

    def test_empty_slots_are_not_filled_with_invented_trades(self):
        payload = simple_trades((90000,))
        minutes = minute_prices(payload, date(2026, 11, 16), 1)
        self.assertEqual(len(two_hour_prices(minutes, daily_factors(["2026-11-16"]), Session())), 1)

    def test_cache_roundtrip_and_daily_volume_correction_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            store = IntradayCache(Path(temp))
            minutes = minute_prices(simple_trades(), date(2026, 11, 16), 10)
            store.save("123", date(2026, 11, 16), minutes, "2026-11-17T00:00:00+00:00")
            self.assertEqual(len(store.load("123", date(2026, 11, 16), 10)), 4)
            with self.assertRaises(DataError):
                store.load("123", date(2026, 11, 16), 11)
            original = json.loads(store.path("123", date(2026, 11, 16)).read_text())
            original["minutes"][0]["last_trade_at"] = "2026-11-16T11:30:00+03:30"
            store.path("123", date(2026, 11, 16)).write_text(json.dumps(original))
            with self.assertRaises(DataError):
                store.load("123", date(2026, 11, 16), 10)

    def test_collection_resume_offline_and_missing_day_never_silently_skipped(self):
        payload = signal_history(length=3)
        raw = parse_history(payload)
        daily = prepare_prices(raw)
        with tempfile.TemporaryDirectory() as temp:
            cache = HistoryCache(Path(temp))
            client = MagicMock()
            client.trades.side_effect = lambda code, day: trades_for_day(payload, day)
            result = collect_minutes(cache, "123", raw, daily, client=client, mode="resume", days=3, delay_seconds=0)
            self.assertEqual(client.trades.call_count, 3)
            client.trades.reset_mock()
            offline = collect_minutes(cache, "123", raw, daily, mode="offline", days=3)
            self.assertEqual(len(result), len(offline))
            client.trades.assert_not_called()
            store = IntradayCache(cache.directory)
            store.path("123", daily.iloc[0]["date"].date()).unlink()
            with self.assertRaises(DataError):
                collect_minutes(cache, "123", raw, daily, mode="offline", days=3)
            collect_minutes(cache, "123", raw, daily, client=client, mode="resume", days=3, delay_seconds=0)
            self.assertEqual(client.trades.call_count, 1)

    def test_current_day_is_not_fetched_without_confirmation(self):
        payload = signal_history(length=3)
        raw = parse_history(payload)
        today = raw.iloc[-1]["date"].date()
        client = MagicMock()
        client.trades.side_effect = lambda code, day: trades_for_day(payload, day)
        with tempfile.TemporaryDirectory() as temp:
            prepare_timeframe(raw, "2h", today=today, cache=HistoryCache(Path(temp)), code="123",
                               client=client, mode="resume", intraday_days=3, delay_seconds=0)
            self.assertEqual(client.trades.call_count, 2)
            self.assertNotIn(int(today.strftime("%Y%m%d")), [call.args[1] for call in client.trades.call_args_list])

    def test_reference_respects_each_timeframe_and_session_policy(self):
        kwargs = {"today": date(2026, 11, 17), "include_today": False, "session": Session()}
        self.assertEqual(catalog_reference("2026-11-16", "daily", **kwargs), "2026-11-16")
        self.assertEqual(catalog_reference("2026-11-16", "weekly", **kwargs), "")
        self.assertEqual(catalog_reference("2026-11-11", "weekly", **kwargs), "2026-11-11")
        self.assertIn("12:30:00+03:30", catalog_reference("2026-11-16", "2h", **kwargs))


if __name__ == "__main__":
    unittest.main()
