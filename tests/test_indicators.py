import unittest

import numpy as np
import pandas as pd

from rsi_cci.indicators import cci, rsi


class IndicatorTests(unittest.TestCase):
    def test_wilder_reference_values(self):
        # Published Wilder example: first RSI uses 14 changes, not an EWM seed.
        prices = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10,
                            45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
                            46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21])
        result = rsi(prices)
        self.assertTrue(result.iloc[:14].isna().all())
        self.assertAlmostEqual(result.iloc[14], 70.464135, places=5)
        self.assertAlmostEqual(result.iloc[15], 66.249619, places=5)
        self.assertAlmostEqual(result.iloc[16], 66.480942, places=5)

    def test_flat_rising_and_falling_rsi(self):
        self.assertEqual(rsi(pd.Series([10.] * 30)).iloc[-1], 50)
        self.assertEqual(rsi(pd.Series(range(30))).iloc[-1], 100)
        self.assertEqual(rsi(pd.Series(range(30, 0, -1))).iloc[-1], 0)

    def test_cci_uses_mean_absolute_deviation_not_standard_deviation(self):
        values = pd.Series([1., 2., 3., 9.])
        result = cci(values, values, values, 4)
        # mean=3.75; mean absolute deviation=2.625
        self.assertAlmostEqual(result.iloc[-1], (9 - 3.75) / (0.015 * 2.625))
        self.assertTrue(result.iloc[:3].isna().all())

    def test_cci_flat_windows_are_neutral(self):
        values = pd.Series([10.] * 30)
        self.assertEqual(cci(values, values, values, 20).iloc[-1], 0)

    def test_prefix_does_not_change_when_future_data_added(self):
        values = pd.Series(100 + np.sin(np.arange(80)) * 5)
        np.testing.assert_allclose(rsi(values[:40]), rsi(values)[:40], equal_nan=True)
        np.testing.assert_allclose(cci(values[:40], values[:40], values[:40], 20),
                                   cci(values, values, values, 20)[:40], equal_nan=True)

    def test_nonfinite_rsi_rejected(self):
        with self.assertRaises(ValueError):
            rsi(pd.Series([1., float("nan")]))


if __name__ == "__main__":
    unittest.main()
