import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rsi_cci.demo import demo_history


APP = str(Path(__file__).resolve().parents[1] / "app.py")


class AppTests(unittest.TestCase):
    def test_demo_both_periods_and_no_selected_entry(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"RSI_CCI_DATA_DIR": temp}):
            app = AppTest.from_file(APP, default_timeout=30).run()
            self.assertEqual(len(app.exception), 0)
            next(button for button in app.button if button.label == "نمایش داده آزمایشی").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 4)
            self.assertTrue(any("ساختگی" in message.value for message in app.warning))
            app.radio[0].set_value(20).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.metric[2].label, "CCI 20")
            app.multiselect[0].set_value([]).run()
            self.assertEqual(len(app.metric), 0)
            self.assertTrue(any("حداقل" in message.value for message in app.info))

    def test_search_fetch_cache_and_failed_refresh_clears_old_document(self):
        from rsi_cci.data import DataError
        from rsi_cci.tsetmc import Instrument

        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"RSI_CCI_DATA_DIR": temp}), \
             patch("rsi_cci.tsetmc.TsetmcClient.search", return_value=[Instrument("123", "تست", "نمونه", 20250101)]), \
             patch("rsi_cci.tsetmc.TsetmcClient.history", return_value=demo_history()) as history:
            app = AppTest.from_file(APP, default_timeout=30).run()
            next(field for field in app.text_input if field.label == "نام نماد").set_value("تست")
            next(button for button in app.button if button.label == "جست‌وجوی نماد").click().run()
            next(button for button in app.button if button.label == "دریافت و ذخیره سابقه کامل").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 4)
            self.assertTrue((Path(temp) / "123.json").exists())
            # Refresh now fails: previous data must not look like a successful new fetch.
            history.side_effect = DataError("test network failure")
            next(button for button in app.button if button.label == "دریافت و ذخیره سابقه کامل").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 0)
            self.assertTrue(any("test network failure" in item.value for item in app.error))
            next(button for button in app.button if button.label == "تحلیل داده ذخیره‌شده").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.metric), 4)
            self.assertTrue(any("آفلاین" in item.value for item in app.warning))


if __name__ == "__main__":
    unittest.main()
