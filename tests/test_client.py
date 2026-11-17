import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock

import requests

from rsi_cci.data import DataError, HistoryCache, parse_history, prepare_prices
from rsi_cci.demo import demo_history
from rsi_cci.strategy import StrategyConfig, analyze
from rsi_cci.tsetmc import TsetmcClient


class ClientTests(unittest.TestCase):
    def test_intraday_uses_real_trade_endpoint_with_validated_date(self):
        session = Mock()
        session.headers = {}
        session.get.return_value.json.return_value = {"tradeHistory": []}
        with TsetmcClient(session) as client:
            client.trades("123", 20261116)
            self.assertTrue(session.get.call_args.args[0].endswith("Trade/GetTradeHistory/123/20261116/false"))
            with self.assertRaises(DataError):
                client.trades("123", 20260230)

    def test_search_unicode_encoding_and_latest_exact_match(self):
        session = Mock()
        session.headers = {}
        session.get.return_value.json.return_value = {"instrumentSearch": [
            {"insCode": "1", "lVal18AFC": "فولاد", "lVal30": "قديمي", "lastDate": 20200101},
            {"insCode": "2", "lVal18AFC": "فولاد", "lVal30": "جديد", "lastDate": 20250101}]}
        with TsetmcClient(session) as client:
            matches = client.search("فولاد")
        self.assertEqual(matches[0].code, "2")
        self.assertIn("%D9", session.get.call_args.args[0])
        self.assertEqual(session.get.call_args.kwargs["timeout"], (5, 25))
        self.assertNotIn("verify", session.get.call_args.kwargs)

    def test_timeout_ssl_http_and_bad_json_are_actionable_errors(self):
        for exception in [requests.Timeout(), requests.exceptions.SSLError(), requests.HTTPError()]:
            session = Mock()
            session.headers = {}
            session.get.side_effect = exception
            with self.subTest(exception=exception), TsetmcClient(session) as client, self.assertRaises(DataError):
                client.history("123")
        session = Mock()
        session.headers = {}
        session.get.return_value.json.side_effect = ValueError("invalid")
        with TsetmcClient(session) as client, self.assertRaises(DataError):
            client.history("123")

    def test_real_http_fetch_parse_cache_indicators_and_both_cci_periods(self):
        payload = demo_history()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/api/ClosingPrice/GetClosingPriceDailyList/123/0":
                    self.send_error(404)
                    return
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TsetmcClient() as client, tempfile.TemporaryDirectory() as temp:
                client.BASE = f"http://127.0.0.1:{server.server_port}/api"
                document = HistoryCache(Path(temp)).save("123", "SYNTHETIC", client.history("123"))
                prices = prepare_prices(parse_history(document["payload"]))
                for period in (14, 20):
                    result = analyze(prices, StrategyConfig(period))
                    self.assertGreater(result["action"].eq("BUY").sum(), 0)
                    self.assertGreater(result["action"].eq("EXIT").sum(), 0)
                    self.assertTrue(result["rsi"].iloc[14:].notna().all())
                    self.assertTrue(result["cci"].iloc[period - 1:].notna().all())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
