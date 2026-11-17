"""Trade identity corrections, including the user's public TSETMC excerpt.

The supplied excerpt is not the complete 2026-11-08 response; its volume is
checked only against the sum of its two non-cancelled executions.
"""

import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

from rsi_cci.data import HistoryCache, parse_history, prepare_prices
from rsi_cci.intraday import IntradayCache, IntradayDataError, collect_minutes, minute_prices
from tests.market_fixtures import signal_history
from tests.test_timeframes import simple_trades, trades_for_day


def customer_excerpt():
    return {"tradeHistory": [
        {"insCode": None, "dEven": 0, "nTran": 873, "hEven": 140238, "qTitTran": 18507, "pTran": 4540., "canceled": 1},
        {"insCode": None, "dEven": 0, "nTran": 40440, "hEven": 122959, "qTitTran": 213050, "pTran": 4540., "canceled": 0},
        {"insCode": None, "dEven": 0, "nTran": 40441, "hEven": 122959, "qTitTran": 86950, "pTran": 4540., "canceled": 0}]}


class TradeRepairTests(unittest.TestCase):
    def test_customer_excerpt_excludes_after_session_cancellation(self):
        result = minute_prices(customer_excerpt(), date(2026, 11, 8), 300000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["volume"], 300000)
        self.assertEqual(result.iloc[0]["close"], 4540)

    def test_cancellation_marker_also_invalidates_original_execution(self):
        payload = customer_excerpt()
        original = dict(payload["tradeHistory"][0], canceled=0, hEven=90100)
        payload["tradeHistory"].append(original)
        result = minute_prices(payload, date(2026, 11, 8), 300000)
        self.assertEqual(result["volume"].sum(), 300000)
        self.assertEqual(result.attrs["normalization"]["cancelled_records_removed"], 2)

    def test_exact_duplicate_is_counted_once_but_distinct_ids_are_preserved(self):
        payload = simple_trades()
        payload["tradeHistory"].append(copy.deepcopy(payload["tradeHistory"][0]))
        result = minute_prices(payload, date(2026, 11, 8), 10)
        self.assertEqual(result["volume"].sum(), 10)
        self.assertEqual(result.attrs["normalization"]["exact_duplicates_removed"], 1)
        payload = {"tradeHistory": [{"nTran": n, "hEven": 90000, "qTitTran": 5, "pTran": 10, "canceled": 0} for n in (1, 2)]}
        self.assertEqual(minute_prices(payload, date(2026, 11, 8), 10).iloc[0]["trades"], 2)

    def test_zero_based_trade_id_and_zero_quantity_marker(self):
        payload = {"tradeHistory": [{"nTran": 0, "hEven": 90000, "qTitTran": 5, "pTran": 10, "canceled": 0},
                                     {"nTran": 1, "hEven": 0, "qTitTran": 0, "pTran": 0, "canceled": 0}]}
        result = minute_prices(payload, date(2026, 11, 8), 5)
        self.assertEqual(result["volume"].sum(), 5)
        self.assertEqual(result.attrs["normalization"]["zero_volume_records_removed"], 1)

    def test_conflicting_versions_remain_an_explicit_error(self):
        payload = simple_trades()
        payload["tradeHistory"][1]["nTran"] = 1
        with self.assertRaises(IntradayDataError) as context:
            minute_prices(payload, date(2026, 11, 8), 10)
        self.assertEqual(context.exception.details["reason"], "CONFLICTING_TRADE_VERSIONS")
        self.assertIn("1", str(context.exception))

    def test_invalid_clock_reports_exact_field_and_sample(self):
        payload = simple_trades()
        payload["tradeHistory"][2]["hEven"] = 126000
        with self.assertRaises(IntradayDataError) as context:
            minute_prices(payload, date(2026, 11, 8), 10)
        self.assertEqual(context.exception.details["reason"], "INVALID_CLOCK")
        self.assertEqual(context.exception.details["sample_records"][0]["nTran"], 3)

    def test_refresh_failure_saves_small_diagnostic_and_preserves_good_cache(self):
        payload = signal_history(length=1)
        raw = parse_history(payload)
        daily = prepare_prices(raw)
        day = daily.iloc[0]["date"].date()
        with tempfile.TemporaryDirectory() as temp:
            cache = HistoryCache(Path(temp))
            client = Mock()
            client.trades.side_effect = lambda code, stamp: trades_for_day(payload, stamp)
            collect_minutes(cache, "123", raw, daily, client=client, mode="resume", delay_seconds=0)
            stored = IntradayCache(cache.directory).path("123", day)
            previous = stored.read_bytes()
            broken = trades_for_day(payload, int(day.strftime("%Y%m%d")))
            broken["tradeHistory"][0]["hEven"] = 126000
            client.trades.side_effect = None
            client.trades.return_value = broken
            with self.assertRaises(IntradayDataError):
                collect_minutes(cache, "123", raw, daily, client=client, mode="refresh", delay_seconds=0)
            report = cache.directory / "diagnostics/123" / (day.strftime("%Y%m%d") + "-trades.json")
            document = json.loads(report.read_text())
            self.assertEqual(document["reason"], "INVALID_CLOCK")
            self.assertLessEqual(len(document["sample_records"]), 12)
            self.assertEqual(stored.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
