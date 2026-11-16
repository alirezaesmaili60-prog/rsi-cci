import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from rsi_cci.cli import main
from rsi_cci.demo import demo_history


class CliTests(unittest.TestCase):
    def test_market_scan_and_offline_rescan_export_coverage_and_matches(self):
        from tests.market_fixtures import market_item, signal_history

        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()) as stdout, \
             patch("rsi_cci.tsetmc.TsetmcClient.market_universe", return_value=[market_item("1"), market_item("2")]) as universe, \
             patch("rsi_cci.tsetmc.TsetmcClient.history", return_value=signal_history()) as history:
            with patch("sys.argv", ["cli", "scan", "--data-dir", temp, "--both-cci"]):
                self.assertEqual(main(), 0)
            output = Path(temp) / "market/latest_scan.csv"
            matches = output.with_name("latest_scan_matches.csv")
            self.assertTrue(matches.exists())
            self.assertEqual(len(pd.read_csv(output)), 4)
            self.assertEqual(len(pd.read_csv(matches)), 4)
            self.assertIn("2/2 processed", stdout.getvalue())
            history.reset_mock()
            universe.reset_mock()
            with patch("sys.argv", ["cli", "scan", "--data-dir", temp, "--offline", "--cci", "20"]):
                self.assertEqual(main(), 0)
            self.assertEqual(len(pd.read_csv(output)), 2)
            history.assert_not_called()
            universe.assert_not_called()

    def test_market_listing_failure_has_failure_exit_status(self):
        from rsi_cci.data import DataError
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()) as stdout, \
             patch("rsi_cci.tsetmc.TsetmcClient.market_universe", side_effect=DataError("network unavailable")), \
             patch("sys.argv", ["cli", "scan", "--data-dir", temp]):
            self.assertEqual(main(), 1)
            self.assertIn("network unavailable", stdout.getvalue())
            self.assertEqual(pd.read_csv(Path(temp) / "market/latest_scan.csv")["status"].tolist(), ["SCAN_ERROR"])
            self.assertTrue(pd.read_csv(Path(temp) / "market/latest_scan_matches.csv").empty)

    def test_fetch_then_offline_analysis_export(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch("rsi_cci.tsetmc.TsetmcClient.history", return_value=demo_history()) as history, \
             redirect_stdout(io.StringIO()):
            with patch("sys.argv", ["cli", "fetch", "123", "--data-dir", temp]):
                self.assertEqual(main(), 0)
            self.assertTrue((Path(temp) / "123.json").exists())
            self.assertTrue((Path(temp) / "123_cci14.csv").exists())
            history.assert_called_once_with("123")
            history.side_effect = AssertionError("Offline analysis must not fetch")
            output = Path(temp) / "custom.csv"
            with patch("sys.argv", ["cli", "analyze", "123", "--data-dir", temp,
                                     "--cci", "20", "--raw", "--last-trade", "--output", str(output)]):
                self.assertEqual(main(), 0)
            frame = pd.read_csv(output)
            self.assertEqual(len(frame), 260)
            self.assertEqual(set(frame["cci_period"]), {20})
            self.assertEqual(set(frame["adjusted"]), {False})
            self.assertEqual(set(frame["close_basis"]), {"close"})
            self.assertTrue(frame["action"].eq("BUY").any())

    def test_missing_cache_has_failure_exit_status(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()) as stdout, \
             patch("sys.argv", ["cli", "analyze", "123", "--data-dir", temp]):
            self.assertEqual(main(), 1)
            self.assertIn("Error:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
