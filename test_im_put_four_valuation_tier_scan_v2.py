from __future__ import annotations

import numpy as np
import pandas as pd

import im_put_four_valuation_tier_scan_v2 as scan


def _base() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "eval_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "execution_date": pd.to_datetime(["2026-01-06", "2026-01-07", "2026-01-08"]),
            "binary_target_qty": [0, 3, 3],
            "three_tier_target_qty": [0, 3, 3],
            "valuation_tier": [0, 3, 1],
            "mom120_active": [False, False, True],
        }
    )


def _state() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "unbounded_median_knot": [1.0, 4.1, 2.5],
            "rolling_percentile": [0.1, 0.99, 0.6],
            "absolute_tier": [0, 0, 1],
        }
    )


def _thresholds(candidate: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "candidate": [candidate],
            "effective_month": pd.to_datetime(["2026-01-01"]),
            "threshold_1_new": [2.0],
            "threshold_2_new": [3.0],
            "threshold_3_new": [3.5],
            "threshold_4_new": [4.0],
        }
    )


def test_fourth_tier_only_valuation_and_momentum_stays_three() -> None:
    definition = next(item for item in scan.CANDIDATES if item["policy"] == "rolling_four_tier")
    result = scan.build_schedule(_base(), definition, _state(), _thresholds(definition["candidate"]))
    assert result["new_relative_tier"].tolist() == [0, 4, 1]
    assert result["binary_target_qty"].tolist() == [0, 4, 3]


def test_existing_top_promotes_only_valuation_top() -> None:
    definition = next(item for item in scan.CANDIDATES if item["policy"] == "existing_val_top")
    result = scan.build_schedule(_base(), definition, _state(), pd.DataFrame())
    assert result["binary_target_qty"].tolist() == [0, 4, 3]


def test_thresholds_are_causal_and_increasing() -> None:
    dates = pd.date_range("2020-01-31", periods=70, freq="ME")
    monthly = pd.DataFrame(
        {"date": dates, "unbounded_median_knot": np.linspace(1.0, 3.0, len(dates))}
    )
    eval_dates = pd.Series(pd.to_datetime(["2025-11-10"]))
    thresholds = scan.build_thresholds(monthly, eval_dates)
    assert thresholds["sample_months"].eq(57).all()
    assert thresholds["max_input_date"].lt(thresholds["effective_month"]).all()
    values = thresholds[[f"threshold_{idx}_new" for idx in range(1, 5)]].to_numpy()
    assert (np.diff(values, axis=1) > 0).all()
