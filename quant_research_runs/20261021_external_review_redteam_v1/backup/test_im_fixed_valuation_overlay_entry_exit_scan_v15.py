import unittest

import pandas as pd

import im_fixed_valuation_overlay_entry_exit_scan_v15 as subject


class ImFixedValuationOverlayV15Tests(unittest.TestCase):
    def test_frozen_grid_has_144_candidates(self):
        grid = subject.fixed_grid()
        self.assertEqual(len(grid), 144)
        self.assertEqual(len(set(grid)), 144)
        self.assertTrue(all(high - low >= 0.30 - 1e-12 for low, high in grid))

    def test_t_plus_one_open_state_machine(self):
        dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
        market = pd.DataFrame(
            {
                "date": dates,
                "contract": ["IMX"] * 4,
                "open_unit": [100.0, 99.0, 103.0, 106.0],
                "settle_unit": [100.0, 102.0, 105.0, 107.0],
                "pre_settle_unit": [100.0, 100.0, 102.0, 105.0],
                "execution_volume": [1000.0] * 4,
                "roll_event": [False] * 4,
                "unbounded_median_knot": [1.4, 1.5, 2.2, 2.3],
            }
        )
        history = market[["date", "unbounded_median_knot"]]
        daily, trades, cycle = subject.simulate_overlay(
            market,
            history,
            "unbounded_median_knot",
            1.5,
            2.2,
            "fixed_L1.50_H2.20",
            "fixed_score",
            "model",
        )
        self.assertEqual(trades["action"].tolist(), ["buy", "sell"])
        self.assertEqual(trades["execution_date"].tolist(), [dates[1], dates[3]])
        self.assertAlmostEqual(daily.loc[1, "overlay_gross_ret"], 102.0 / 99.0 - 1.0)
        self.assertAlmostEqual(daily.loc[2, "overlay_gross_ret"], 105.0 / 102.0 - 1.0)
        self.assertAlmostEqual(daily.loc[3, "overlay_gross_ret"], 106.0 / 105.0 - 1.0)
        self.assertEqual(cycle["completed_cycles"], 1)
        self.assertEqual(cycle["pending_order_end"], 0)

    def test_real_start_carries_prior_state_at_open(self):
        history = pd.DataFrame(
            {
                "date": pd.to_datetime(["2022-07-20", "2022-07-21"]),
                "unbounded_median_knot": [1.4, 1.5],
            }
        )
        market = pd.DataFrame(
            {
                "date": pd.to_datetime(["2022-07-22", "2022-07-25"]),
                "contract": ["IM2208", "IM2208"],
                "open_unit": [100.0, 102.0],
                "settle_unit": [101.0, 103.0],
                "pre_settle_unit": [100.0, 101.0],
                "execution_volume": [1000.0, 1000.0],
                "roll_event": [False, False],
                "unbounded_median_knot": [1.6, 1.7],
            }
        )
        daily, trades, cycle = subject.simulate_overlay(
            market,
            history,
            "unbounded_median_knot",
            1.5,
            2.2,
            "fixed_L1.50_H2.20",
            "fixed_score",
            "real",
        )
        self.assertEqual(trades.iloc[0]["execution_reason"], "initial_listing_carry")
        self.assertEqual(int(daily.iloc[0]["overlay_buy"]), 1)
        self.assertEqual(cycle["initial_carry"], 1)

    def test_metrics_include_calmar(self):
        result = subject.metrics(pd.Series([0.01, -0.005, 0.007]))
        self.assertIn("calmar", result)
        self.assertGreater(result["total_return"], 0)
        self.assertLess(result["max_dd"], 0)


if __name__ == "__main__":
    unittest.main()

