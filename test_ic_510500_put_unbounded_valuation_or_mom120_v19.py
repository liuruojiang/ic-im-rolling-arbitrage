from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_unbounded_valuation_or_mom120_v19 as v19


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
    assert len(v19.VARIANTS) == 12
    assert len(v19.VALUATION_VARIANTS) == 9
    assert set(v19.THRESHOLDS) == {1.9, 2.0, 2.1}
    assert v19.EXECUTION_STRUCTURE == "3m_monthly_exit"
    assert v19.MONEYNESS == 0.95


def test_momentum_and_old_reference_signals() -> None:
    assert v19.signal_target("mom120_only", _row(momentum=0.0)) == 1.0
    assert v19.signal_target("mom120_only", _row(momentum=0.01)) == 0.0
    assert v19.signal_target("old_fixed175_or_mom120", _row(old=1.75)) == 1.0
    assert (
        v19.signal_target("old_fixed175_or_mom120", _row(old=1.50, momentum=-0.01))
        == 1.0
    )
    assert (
        v19.signal_target("old_fixed175_or_mom120", _row(old=1.50, momentum=0.01))
        == 0.0
    )


def test_new_signal_is_valuation_or_momentum() -> None:
    row = _row(mean=2.05, median=1.95, momentum=0.01)
    assert v19.signal_target("mean200_or_mom120", row) == 1.0
    assert v19.signal_target("median200_or_mom120", row) == 0.0
    assert v19.signal_target("intersection200_or_mom120", row) == 0.0

    momentum_row = _row(mean=1.50, median=1.50, momentum=-0.01)
    for variant in v19.VALUATION_VARIANTS:
        assert v19.signal_target(variant, momentum_row) == 1.0


def test_threshold_boundary_is_inclusive() -> None:
    assert v19.signal_target("mean210_or_mom120", _row(mean=2.10)) == 1.0
    assert v19.signal_target("median190_or_mom120", _row(median=1.90)) == 1.0
    assert (
        v19.signal_target("intersection210_or_mom120", _row(mean=2.10, median=2.10))
        == 1.0
    )


def test_metrics_match_compound_return_definition() -> None:
    returns = pd.Series([0.01, -0.005, 0.002])
    result = v19.metrics(returns)
    nav = (1.0 + returns).cumprod()
    expected = nav.iloc[-1] ** (252.0 / len(returns)) - 1.0
    assert np.isclose(result["ann_return"], expected)
    assert np.isclose(result["max_dd"], (nav / nav.cummax() - 1.0).min())


def test_intersection_never_exceeds_parent_valuation_signal() -> None:
    for mean in np.linspace(1.5, 2.5, 11):
        for median in np.linspace(1.5, 2.5, 11):
            row = _row(mean=float(mean), median=float(median))
            for threshold in v19.THRESHOLDS:
                suffix = round(threshold * 100)
                intersection = v19.valuation_only_target(
                    f"intersection{suffix:03d}_or_mom120", row
                )
                mean_target = v19.valuation_only_target(
                    f"mean{suffix:03d}_or_mom120", row
                )
                median_target = v19.valuation_only_target(
                    f"median{suffix:03d}_or_mom120", row
                )
                assert intersection <= mean_target
                assert intersection <= median_target


def test_exposure_counts_model_and_real_entries_with_correct_fields() -> None:
    common = {
        "date": pd.Timestamp("2026-01-05"),
        "target_fraction": 1.0,
        "put_mark_fraction": 1.0,
        "put_cost_rate": 0.001,
        "deferred_adjustment": False,
        "carried_mark": False,
        "mark_stale_days": 0,
    }
    daily = pd.DataFrame(
        [
            {"candidate": "model_mean190_or_mom120", **common},
            {"candidate": "real_mean190_or_mom120", **common},
        ]
    )
    trades = pd.DataFrame(
        [
            {
                "candidate": "model_mean190_or_mom120",
                "action": "open_buy",
                "new_month": "2026-03-01",
                "new_contract": np.nan,
                "new_entry_moneyness": 0.95,
            },
            {
                "candidate": "real_mean190_or_mom120",
                "action": "open_buy",
                "new_month": np.nan,
                "new_contract": "510500P2603M04000",
                "new_entry_moneyness": 0.951,
            },
        ]
    )

    result = v19.exposure_summary(daily, trades).set_index("layer")

    assert result.loc["model", "entry_or_roll_events"] == 1
    assert result.loc["real", "entry_or_roll_events"] == 1
