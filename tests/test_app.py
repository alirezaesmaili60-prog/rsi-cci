import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from rsi_cci.demo import demo_history


APP = str(Path(__file__).resolve().parents[1] / "app.py")


class AppTests(unittest.TestCase):
    def test_whole_market_form_background_results_and_offline_mode(self):
        from rsi_cci.market_ui import market_scanner
        from tests.market_fixtures import market_item, signal_history

        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"RSI_CCI_DATA_DIR": temp}), \
             patch("rsi_cci.tsetmc.TsetmcClient.market_universe", return_value=[market_item("1"), market_item("2")]) as universe, \
             patch("rsi_cci.tsetmc.TsetmcClient.history", return_value=signal_history()) as history:
            app = AppTest.from_file(APP, default_timeout=30).run()
            next(button for button in app.button if button.label == "شروع دریافت و اسکن بازار").click().run()
            scanner = market_scanner(str(Path(temp).resolve()))
            scanner.wait(10)
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(scanner.snapshot()["processed"], 2)
            self.assertTrue(any(metric.label == "نماد در نتیجه انتخابی" and metric.value == "2" for metric in app.metric))
            self.assertTrue(any("پایان پردازش" in text.value for text in app.markdown))
            self.assertTrue((Path(temp) / "1.json").exists())
            history.reset_mock()
            universe.reset_mock()
            next(field for field in app.selectbox if field.label == "روش جمع‌آوری بازار").select("تحلیل آفلاین فایل‌های قبلی")
            next(button for button in app.button if button.label == "شروع دریافت و اسکن بازار").click().run()
            scanner.wait(10)
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("اسکن آفلاین" in message.value for message in app.warning))
            history.assert_not_called()
            universe.assert_not_called()

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
            next(field for field in app.multiselect if field.label == "روش‌های ورود فعال").set_value([]).run()
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
