import unittest

from rsi_cci.strategy import StrategyConfig, signal_states


class StrategyTests(unittest.TestCase):
    def test_a1_and_a2_and_b_independently(self):
        cases = [([55, 56], [-1, 1], "A1"),
                 ([55, 56], [90, 101], "A2"),
                 ([50, 51], [20, 21], "B")]
        for rsis, ccis, rule in cases:
            with self.subTest(rule=rule):
                result = signal_states(rsis, ccis, (rule,))
                self.assertEqual(result.iloc[-1]["action"], "BUY")
                self.assertEqual(result.iloc[-1]["reason"], rule)

    def test_touching_level_is_not_crossing_above(self):
        self.assertEqual(signal_states([55, 55], [-5, 0], ("A1",)).iloc[-1]["action"], "")
        self.assertEqual(signal_states([49, 50], [10, 10], ("B",)).iloc[-1]["action"], "")
        self.assertEqual(signal_states([55, 55], [90, 100], ("A2",)).iloc[-1]["action"], "")

    def test_duplicates_one_entry_and_no_pyramiding(self):
        result = signal_states([49, 55, 56, 57], [-1, 1, 110, 120], ("A1", "A2", "B"))
        self.assertEqual(result["action"].tolist(), ["", "BUY", "", ""])
        self.assertEqual(result.iloc[1]["reason"], "A1+B")
        self.assertEqual(result.iloc[-1]["entry_index"], 1)

    def test_exit_100_including_equality_plateau(self):
        result = signal_states([55] * 6, [-10, 10, 130, 100, 100, 99], ("A1",))
        self.assertEqual(result["action"].tolist(), ["", "BUY", "", "", "", "EXIT"])
        self.assertEqual(result.iloc[-1]["reason"], "CCI_DOWN_100")
        self.assertFalse(result.iloc[-1]["holding"])

    def test_stop_minus_100_without_reaching_positive_100(self):
        result = signal_states([55] * 5, [-10, 10, 100, -100, -101], ("A1",))
        self.assertEqual(result.iloc[-1]["reason"], "CCI_STOP_MINUS_100")
        self.assertEqual(result.iloc[3]["action"], "")

    def test_a2_entry_is_immediately_armed_for_exit_100(self):
        result = signal_states([55] * 3, [90, 110, 80], ("A2",))
        self.assertEqual(result["action"].tolist(), ["", "BUY", "EXIT"])
        self.assertEqual(result.iloc[-1]["reason"], "CCI_DOWN_100")

    def test_previous_trades_high_does_not_arm_new_trade(self):
        result = signal_states([55] * 8, [-10, 10, 120, 90, -10, 10, 30, -110], ("A1",))
        self.assertEqual(result["action"].tolist(), ["", "BUY", "", "EXIT", "", "BUY", "", "EXIT"])
        self.assertEqual(result.iloc[-1]["reason"], "CCI_STOP_MINUS_100")

    def test_exit_never_opens_new_trade_on_same_bar(self):
        result = signal_states([55, 55, 49, 55], [-10, 110, 120, 90], ("A1", "B"))
        self.assertEqual(result.iloc[-1]["action"], "EXIT")
        self.assertFalse(result.iloc[-1]["holding"])

    def test_no_entry_until_both_previous_indicators_are_valid(self):
        result = signal_states([float("nan"), 60, 60], [float("nan"), 100, 110], ("A1",))
        self.assertFalse(result["holding"].any())

    def test_unselected_entry_is_disabled(self):
        result = signal_states([55, 55], [-5, 5], ("B",))
        self.assertEqual(result.iloc[-1]["action"], "")

    def test_raw_entry_condition_still_visible_when_holding_suppresses_new_buy(self):
        result = signal_states([55, 55, 55], [-5, 5, 110], ("A1", "A2"))
        self.assertEqual(result.iloc[1]["action"], "BUY")
        self.assertEqual(result.iloc[2]["action"], "")
        self.assertEqual(result.iloc[2]["entry_conditions"], "A2")

    def test_config_rejects_ambiguous_period_and_empty_rules(self):
        with self.assertRaises(ValueError):
            StrategyConfig(18)
        with self.assertRaises(ValueError):
            StrategyConfig(entries=())


if __name__ == "__main__":
    unittest.main()
