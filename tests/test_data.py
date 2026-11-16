import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rsi_cci.data import DataError, HistoryCache, instrument_code, parse_history, prepare_prices


def bar(day, final, yesterday, **kwargs):
    result = {"dEven": day, "priceFirst": final, "priceMax": final + 1,
              "priceMin": final - 1, "pDrCotVal": final + .5,
              "pClosing": final, "priceYesterday": yesterday, "qTotTran5J": 100,
              "zTotTran": 5}
    result.update(kwargs)
    return result


class DataTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"closingPriceDaily": [bar(20250104, 100, 99), bar(20250105, 110, 100),
                                               bar(20250106, 60, 55)]}

    def test_api_mapping_ordering_and_no_trade_rows(self):
        payload = {"closingPriceDaily": [bar(20250107, 0, 60, zTotTran=0, qTotTran5J=0)]
                   + self.payload["closingPriceDaily"][::-1]}
        raw = parse_history(payload)
        self.assertEqual(len(raw), 3)
        self.assertEqual(raw.iloc[0]["close"], 100.5)
        self.assertEqual(raw.iloc[0]["final"], 100)
        self.assertTrue(raw["date"].is_monotonic_increasing)

    def test_split_adjustment_applies_to_prior_prices_only(self):
        raw = parse_history(self.payload)
        result = prepare_prices(raw, today=date(2025, 1, 7))
        self.assertEqual(result["adjustment_factor"].tolist(), [.5, .5, 1.])
        self.assertEqual(result["close"].tolist(), [50, 55, 60])
        self.assertEqual(result["volume"].tolist(), [100, 100, 100])
        self.assertEqual(raw["final"].tolist(), [100, 110, 60])

    def test_unconfirmed_today_cannot_change_previous_prices(self):
        raw = parse_history(self.payload)
        result = prepare_prices(raw, today=date(2025, 1, 6))
        self.assertEqual(result["close"].tolist(), [100, 110])
        self.assertEqual(len(prepare_prices(raw, today=date(2025, 1, 6), include_today=True)), 3)

    def test_future_bars_always_excluded(self):
        raw = parse_history(self.payload)
        result = prepare_prices(raw, today=date(2025, 1, 5), include_today=True, adjusted=False)
        self.assertEqual(len(result), 2)

    def test_last_trade_and_raw_options(self):
        result = prepare_prices(parse_history(self.payload), today=date(2025, 1, 7),
                                adjusted=False, close_basis="close")
        self.assertEqual(result["close"].tolist(), [100.5, 110.5, 60.5])

    def test_invalid_data_fails_instead_of_silent_filtering(self):
        for change in [{"pClosing": None}, {"priceMax": 1}, {"qTotTran5J": -1},
                       {"dEven": 20250230}, {"pClosing": float("inf")}]:
            with self.subTest(change=change), self.assertRaises(DataError):
                payload = copy.deepcopy(self.payload)
                payload["closingPriceDaily"][0].update(change)
                parse_history(payload)

    def test_missing_schema_and_duplicate_date_rejected(self):
        with self.assertRaises(DataError):
            parse_history({"closingPriceDaily": [{"dEven": 20250101}]})
        with self.assertRaises(DataError):
            parse_history({"closingPriceDaily": [bar(20250101, 10, 10)] * 2})

    def test_adjustment_requires_valid_reference(self):
        raw = parse_history({"closingPriceDaily": [bar(20250101, 10, 10), bar(20250102, 5, 0)]})
        with self.assertRaises(DataError):
            prepare_prices(raw, today=date(2025, 1, 3))
        self.assertEqual(len(prepare_prices(raw, adjusted=False, today=date(2025, 1, 3))), 2)

    def test_atomic_cache_roundtrip_and_bad_refresh_keeps_previous_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = HistoryCache(Path(temp))
            original = cache.save("123", "نمونه", self.payload)
            self.assertEqual(cache.load("123"), original)
            with self.assertRaises(DataError):
                cache.save("123", "bad", {"closingPriceDaily": []})
            self.assertEqual(cache.load("123"), original)
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_cache_corruption_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = HistoryCache(Path(temp))
            cache.path("123").write_text("{broken", encoding="utf8")
            with self.assertRaises(DataError):
                cache.load("123")

    def test_path_injection_rejected(self):
        for code in ["../123", "۱۲۳", "123/../../x", "", "1" * 21]:
            with self.subTest(code=code), self.assertRaises(DataError):
                instrument_code(code)


if __name__ == "__main__":
    unittest.main()
