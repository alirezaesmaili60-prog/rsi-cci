import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from rsi_cci.data import DataError, HistoryCache
from rsi_cci.market import MarketScanner, ScanOptions, filter_results, report_frame
from rsi_cci.tsetmc import TsetmcClient, parse_market_universe
from tests.market_fixtures import fake_client, market_item, signal_history


class UniverseTests(unittest.TestCase):
    def test_compact_market_watch_fields_and_identifier_classification(self):
        identifiers = ["IRO1ABCD0001", "IRO3ABCD0001", "IRO7ABCD0001", "IRO5ABCD0001",
                       "IRT1ABCD0001", "IRR1ABCD0101", "IRO9ABCD2111", "UNKNOWN"]
        payload = {"marketwatch": [{"insCode": str(i), "lva": f"نماد{i}", "lvc": f"شرکت{i}",
                                     "insID": identifier, "dEven": 0, "vc": 5000 + i}
                                    for i, identifier in enumerate(identifiers)]}
        result = parse_market_universe(payload)
        self.assertEqual([item.security_type for item in result], ["300", "303", "309", "313", "305", "400", "311", ""])
        self.assertEqual(result[0].classification_source, "ISIN prefix")
        self.assertEqual(result[-1].classification_source, "")

    def test_flat_and_nested_universe_preserves_codes_and_security_types(self):
        first = {"insCode": "1", "lVal18AFC": "فولاد", "lVal30": "شرکت", "yVal": "300", "dEven": 20250101}
        second = {"insCode": "2", "instrument": {"insCode": "2", "lVal18AFC": "فولاد", "lVal30": "شرکت دوم",
                                                       "yVal": 303, "lastDate": 20250102}}
        result = parse_market_universe({"marketwatch": [first, second, first]})
        self.assertEqual(len(result), 2)
        self.assertEqual({item.code for item in result}, {"1", "2"})
        self.assertEqual({item.security_type for item in result}, {"300", "303"})
        self.assertEqual({item.market for item in result}, {"بورس", "فرابورس"})

    def test_bad_universe_and_conflicting_duplicates_fail_without_silent_drops(self):
        row = {"insCode": "1", "lVal18AFC": "نماد", "lVal30": "نام", "yVal": "300"}
        for payload in [{}, {"marketwatch": []}, {"marketwatch": [row, {}]},
                        {"marketwatch": [row, dict(row, lVal30="متفاوت")]}]:
            with self.subTest(payload=payload), self.assertRaises(DataError):
                parse_market_universe(payload)

    def test_api_requests_all_paper_types_and_nontraded_instruments(self):
        session = Mock()
        session.headers = {}
        session.get.return_value.json.return_value = {"marketwatch": [
            {"insCode": "1", "lVal18AFC": "نماد", "lVal30": "نام", "yVal": "300"}]}
        with TsetmcClient(session) as client:
            self.assertEqual(len(client.market_universe()), 1)
        url = session.get.call_args.args[0]
        self.assertIn("ClosingPrice/GetMarketWatch?", url)
        self.assertIn("paperTypes%5B8%5D=9", url)
        self.assertIn("showTraded=false", url)
        self.assertIn("hEven=0", url)
        self.assertNotIn("verify", session.get.call_args.kwargs)


class MarketScannerTests(unittest.TestCase):
    def test_all_three_timeframes_have_independent_references_and_resume(self):
        from tests.test_timeframes import trades_for_day
        payload = signal_history()
        client = fake_client([market_item("1")], {"1": payload})
        client.trades.side_effect = lambda code, day: trades_for_day(payload, day)
        scanner = self.scanner(client)
        options = ScanOptions(timeframes=("daily", "weekly", "2h"), intraday_days=12, delay_seconds=0)
        snapshot = scanner.run(options)
        self.assertEqual(len(snapshot["rows"]), 6)
        self.assertEqual({row["status"] for row in snapshot["rows"]}, {"OK"})
        self.assertEqual(set(snapshot["reference_dates"]), {"daily", "weekly", "2h"})
        self.assertTrue(all(not row["stale"] for row in snapshot["rows"]))
        self.assertEqual(snapshot["intraday_downloaded"], 12)
        self.assertEqual(len(filter_results(snapshot, "all", timeframe="weekly")), 2)
        report = report_frame(snapshot)
        self.assertEqual(set(report.loc[report.timeframe.eq("2h"), "close_basis"]), {"close"})
        self.assertEqual(set(report.loc[report.timeframe.eq("daily"), "close_basis"]), {"final"})
        self.assertTrue(report.loc[report.timeframe.eq("2h"), "short_session_bar"].all())
        client.history.reset_mock()
        client.trades.reset_mock()
        resumed = scanner.run(options)
        self.assertEqual(resumed["intraday_cached"], 12)
        self.assertEqual(resumed["intraday_downloaded"], 0)
        client.history.assert_not_called()
        client.trades.assert_not_called()

    def test_missing_intraday_preserves_daily_weekly_and_cache_for_completed_days(self):
        from tests.test_timeframes import trades_for_day
        payload = signal_history()
        client = fake_client([market_item("1")], {"1": payload})

        def fail_second_day(code, day):
            if client.trades.call_count == 2:
                raise DataError("historical intraday unavailable")
            return trades_for_day(payload, day)

        client.trades.side_effect = fail_second_day
        snapshot = self.scanner(client).run(ScanOptions(timeframes=("daily", "weekly", "2h"), intraday_days=12, delay_seconds=0))
        self.assertEqual({row["status"] for row in snapshot["rows"] if row["timeframe"] != "2h"}, {"OK"})
        self.assertEqual({row["status"] for row in snapshot["rows"] if row["timeframe"] == "2h"}, {"INTRADAY_ERROR"})
        self.assertEqual(len(list((self.cache.directory / "intraday/1").glob("*.json"))), 1)

    def test_intraday_failure_breaker_keeps_other_timeframes_running(self):
        client = fake_client([market_item("1"), market_item("2"), market_item("3")])
        client.trades.side_effect = DataError("intraday API unavailable")
        snapshot = self.scanner(client).run(ScanOptions(timeframes=("daily", "weekly", "2h"), intraday_days=12,
                                                         delay_seconds=0, failure_limit=2))
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["processed"], 3)
        self.assertEqual(client.trades.call_count, 2)
        self.assertTrue(snapshot["intraday_warning"])
        self.assertTrue(all(row["status"] == "OK" for row in snapshot["rows"] if row["timeframe"] != "2h"))

    def test_cancelling_intraday_keeps_other_timeframes_and_resumes_saved_days(self):
        from tests.test_timeframes import trades_for_day
        payload = signal_history()
        client = fake_client([market_item("1")], {"1": payload})
        scanner = self.scanner(client)

        def cancel_second_day(code, day):
            if client.trades.call_count == 2:
                scanner.cancel()
            return trades_for_day(payload, day)

        client.trades.side_effect = cancel_second_day
        options = ScanOptions(timeframes=("daily", "weekly", "2h"), intraday_days=12, delay_seconds=0)
        stopped = scanner.run(options)
        self.assertEqual(stopped["state"], "cancelled")
        self.assertTrue(all(row["status"] == "OK" for row in stopped["rows"] if row["timeframe"] != "2h"))
        self.assertEqual(len(list((self.cache.directory / "intraday/1").glob("*.json"))), 2)
        client.trades.side_effect = lambda code, day: trades_for_day(payload, day)
        resumed = scanner.run(options)
        self.assertEqual(resumed["state"], "done")
        self.assertEqual(resumed["intraday_cached"], 2)
        self.assertEqual(resumed["intraday_downloaded"], 10)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = HistoryCache(Path(self.temp.name))

    def scanner(self, client):
        return MarketScanner(self.cache, client_factory=lambda: client)

    def test_collect_all_stocks_report_partial_errors_and_both_periods(self):
        items = [market_item("1"), market_item("2", security_type="303"),
                 market_item("3", security_type="309"), market_item("4", security_type="305"),
                 market_item("5", security_type="")]
        client = fake_client(items, {"1": signal_history(), "2": signal_history(), "3": DataError("test failure")})
        snapshot = self.scanner(client).run(ScanOptions(delay_seconds=0))
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["universe_total"], 5)
        self.assertEqual(snapshot["total"], 3)
        self.assertEqual(snapshot["processed"], 3)
        self.assertEqual(snapshot["downloaded"], 2)
        self.assertEqual(snapshot["failed"], 1)
        self.assertEqual(snapshot["unclassified"], 1)
        self.assertEqual(len(snapshot["rows"]), 6)
        self.assertEqual({call.args[0] for call in client.history.call_args_list}, {"1", "2", "3"})
        matches = filter_results(snapshot)
        self.assertEqual(set(matches["code"]), {"1", "2"})
        self.assertEqual(set(matches["cci_period"]), {14, 20})
        self.assertTrue(matches["entry_conditions"].ne("").all())
        self.assertTrue(self.cache.path("1").exists())
        stored = json.loads((self.cache.directory / "market/latest_scan.json").read_text(encoding="utf8"))
        self.assertEqual(stored["failed"], 1)
        self.assertEqual(len(stored["rows"]), 6)

    def test_stale_last_bar_does_not_look_like_new_market_signal(self):
        old = signal_history(shift_days=30)
        items = [market_item("1"), market_item("2", history=old)]
        snapshot = self.scanner(fake_client(items, {"1": signal_history(), "2": old})).run(ScanOptions(delay_seconds=0))
        self.assertEqual(set(filter_results(snapshot)["code"]), {"1"})
        self.assertEqual(set(filter_results(snapshot, include_stale=True)["code"]), {"1", "2"})
        self.assertTrue(all(row["stale"] for row in snapshot["rows"] if row["code"] == "2"))

    def test_catalog_date_marks_outdated_history_even_when_no_other_symbol_succeeds(self):
        old = signal_history(shift_days=30)
        item = market_item("1")
        snapshot = self.scanner(fake_client([item], {"1": old})).run(ScanOptions(delay_seconds=0))
        self.assertTrue(all(row["stale"] for row in snapshot["rows"]))
        self.assertTrue(filter_results(snapshot).empty)
        self.assertFalse(filter_results(snapshot, include_stale=True).empty)

    def test_resume_reuses_successful_files_and_recomputes_other_period(self):
        client = fake_client([market_item("1"), market_item("2")])
        scanner = self.scanner(client)
        first = scanner.run(ScanOptions(periods=(14,), delay_seconds=0))
        self.assertEqual(first["downloaded"], 2)
        client.history.reset_mock()
        second = scanner.run(ScanOptions(periods=(20,), delay_seconds=0))
        self.assertEqual(second["downloaded"], 0)
        self.assertEqual(second["cached"], 2)
        self.assertEqual({row["cci_period"] for row in second["rows"]}, {20})
        client.history.assert_not_called()

    def test_refresh_failure_never_falls_back_to_older_good_file(self):
        self.cache.save("1", "cached", signal_history())
        client = fake_client([market_item("1")], {"1": DataError("network down")})
        snapshot = self.scanner(client).run(ScanOptions(mode="refresh", delay_seconds=0))
        self.assertEqual({row["status"] for row in snapshot["rows"]}, {"FETCH_ERROR"})
        self.assertTrue(filter_results(snapshot).empty)
        self.assertEqual(snapshot["cached"], 0)
        self.assertEqual(self.cache.load("1")["label"], "cached")

    def test_offline_scan_performs_no_network_calls(self):
        client = fake_client([market_item("1")])
        scanner = self.scanner(client)
        scanner.run(ScanOptions(delay_seconds=0))
        client.history.reset_mock()
        client.market_universe.reset_mock()
        snapshot = scanner.run(ScanOptions(mode="offline", delay_seconds=0))
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["cached"], 1)
        self.assertEqual({row["source"] for row in snapshot["rows"]}, {"offline"})
        client.history.assert_not_called()
        client.market_universe.assert_not_called()

    def test_cancellation_preserves_data_and_next_run_resumes(self):
        client = fake_client([market_item("1"), market_item("2"), market_item("3")])
        scanner = self.scanner(client)

        def stop_after_first(snapshot):
            if snapshot["processed"] == 1:
                scanner.cancel()

        snapshot = scanner.run(ScanOptions(delay_seconds=0), stop_after_first)
        self.assertEqual(snapshot["state"], "cancelled")
        self.assertEqual(snapshot["processed"], 1)
        self.assertEqual(sum(row["status"] == "PENDING" for row in snapshot["rows"]), 4)
        client.history.reset_mock()
        resumed = scanner.run(ScanOptions(delay_seconds=0))
        self.assertEqual(resumed["state"], "done")
        self.assertEqual(resumed["cached"], 1)
        self.assertEqual(resumed["downloaded"], 2)
        self.assertEqual({call.args[0] for call in client.history.call_args_list}, {"2", "3"})

    def test_background_cancel_and_reject_second_concurrent_scan(self):
        entered, release = threading.Event(), threading.Event()
        client = fake_client([market_item("1"), market_item("2")])

        def slow_fetch(code):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Test timed out")
            return signal_history()

        client.history.side_effect = slow_fetch
        scanner = self.scanner(client)
        try:
            scanner.start(ScanOptions(delay_seconds=0))
            self.assertTrue(entered.wait(3))
            with self.assertRaises(DataError):
                scanner.start(ScanOptions(delay_seconds=0))
            scanner.cancel()
            release.set()
            scanner.wait(5)
            snapshot = scanner.snapshot()
            self.assertFalse(scanner.running)
            self.assertEqual(snapshot["state"], "cancelled")
            self.assertEqual(snapshot["processed"], 1)
            self.assertTrue(self.cache.path("1").exists())
        finally:
            scanner.cancel()
            release.set()
            scanner.wait(5)

    def test_consecutive_failure_limit_leaves_unchecked_symbols_visible(self):
        client = fake_client([market_item(str(i)) for i in range(4)], {})
        snapshot = self.scanner(client).run(ScanOptions(delay_seconds=0, failure_limit=2))
        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["processed"], 2)
        self.assertEqual(client.history.call_count, 2)
        self.assertEqual(sum(row["status"] == "PENDING" for row in snapshot["rows"]), 4)
        self.assertTrue(snapshot["error"])

    def test_short_history_can_succeed_for_14_but_not_20(self):
        payload = signal_history(length=18)
        client = fake_client([market_item("1", history=payload)], {"1": payload})
        snapshot = self.scanner(client).run(ScanOptions(delay_seconds=0))
        statuses = {row["cci_period"]: row["status"] for row in snapshot["rows"]}
        self.assertEqual(statuses, {14: "OK", 20: "INSUFFICIENT"})

    def test_all_securities_scope_includes_fund_and_unknown_type(self):
        client = fake_client([market_item("1", security_type="305"), market_item("2", security_type="")])
        snapshot = self.scanner(client).run(ScanOptions(scope="all", delay_seconds=0))
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(snapshot["excluded"], 0)

    def test_persisted_running_report_is_marked_interrupted_after_restart(self):
        scanner = self.scanner(fake_client([market_item("1")]))
        snapshot = scanner.run(ScanOptions(delay_seconds=0))
        path = self.cache.directory / "market/latest_scan.json"
        snapshot["state"] = "running"
        path.write_text(json.dumps(snapshot), encoding="utf8")
        restored = self.scanner(fake_client([])).snapshot()
        self.assertEqual(restored["state"], "interrupted")
        self.assertTrue(restored["restored"])
        self.assertFalse(restored["running"])
        self.assertEqual(len(report_frame(restored)), 2)

    def test_missing_offline_universe_is_an_explicit_failure(self):
        snapshot = self.scanner(fake_client([])).run(ScanOptions(mode="offline"))
        self.assertEqual(snapshot["state"], "failed")
        self.assertTrue(snapshot["error"])

    def test_invalid_options_rejected(self):
        for kwargs in [{"periods": ()}, {"periods": (18,)}, {"lookback": 0}, {"cache_minutes": 0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ScanOptions(**kwargs)


if __name__ == "__main__":
    unittest.main()
