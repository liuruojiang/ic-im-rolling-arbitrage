from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_unbounded_valuation_gate_v18 as v18


def _row(
    old: float = 1.5,
    momentum: float = 0.1,
    mean: float = 1.95,
    median: float = 1.95,
) -> pd.Series:
    return pd.Series(
        {
            "old_fixed_risk": old,
            "momentum_120": momentum,
            "unbounded_mean_knot": mean,
            "unbounded_median_knot": median,
        }
    )


def test_candidate_grid_is_frozen() -> None:
    assert len(v18.VARIANTS) == 12
    assert len(v18.VALUATION_VARIANTS) == 9
    assert set(v18.THRESHOLDS) == {1.9, 2.0, 2.1}
    assert v18.EXECUTION_STRUCTURE == "3m_monthly_exit"
    assert v18.MONEYNESS == 0.95


def test_old_reference_signals() -> None:
    assert v18.signal_target("old_fixed175_only", _row(old=1.75)) == 1.0
    assert v18.signal_target("old_fixed175_only", _row(old=1.50)) == 0.0
    assert (
        v18.signal_target("paper_fixed175_or_mom120", _row(old=1.50, momentum=-0.01))
        == 1.0
    )
    assert (
        v18.signal_target("paper_fixed175_or_mom120", _row(old=1.50, momentum=0.01))
        == 0.0
    )


def test_mean_median_and_intersection_are_distinct() -> None:
    row = _row(mean=2.05, median=1.95)
    assert v18.signal_target("mean_200", row) == 1.0
    assert v18.signal_target("median_200", row) == 0.0
    assert v18.signal_target("intersection_200", row) == 0.0
    row = _row(mean=2.05, median=2.05)
    assert v18.signal_target("intersection_200", row) == 1.0


def test_threshold_boundary_is_inclusive() -> None:
    assert v18.signal_target("mean_210", _row(mean=2.10)) == 1.0
    assert v18.signal_target("median_190", _row(median=1.90)) == 1.0
    assert v18.signal_target("intersection_210", _row(mean=2.10, median=2.10)) == 1.0


def test_metrics_match_compound_return_definition() -> None:
    returns = pd.Series([0.01, -0.005, 0.002])
    result = v18.metrics(returns)
    nav = (1.0 + returns).cumprod()
    expected = nav.iloc[-1] ** (252.0 / len(returns)) - 1.0
    assert np.isclose(result["ann_return"], expected)
    assert np.isclose(result["max_dd"], (nav / nav.cummax() - 1.0).min())


def test_intersection_never_exceeds_parent_signal() -> None:
    for mean in np.linspace(1.5, 2.5, 11):
        for median in np.linspace(1.5, 2.5, 11):
            row = _row(mean=float(mean), median=float(median))
            for threshold in v18.THRESHOLDS:
                suffix = round(threshold * 100)
                intersection = v18.signal_target(f"intersection_{suffix:03d}", row)
                mean_target = v18.signal_target(f"mean_{suffix:03d}", row)
                median_target = v18.signal_target(f"median_{suffix:03d}", row)
                assert intersection <= mean_target
                assert intersection <= median_target
