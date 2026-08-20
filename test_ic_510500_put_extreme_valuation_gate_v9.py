from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_extreme_valuation_gate_v9 as v9


def test_frozen_spec_and_dependencies() -> None:
    assert v9.sha256(v9.SPEC) == v9.SPEC_SHA256
    assert v9.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v9.SPEC_SHA256
    assert v9.sha256(v9.V7_PATH) == v9.V7_SHA256
    assert v9.sha256(v9.V5_PATH) == v9.V5_SHA256
    assert v9.sha256(v9.V6_PATH) == v9.V6_SHA256
    assert v9.sha256(v9.PROXY_PATH) == v9.PROXY_SHA256


def test_binary_threshold_boundaries() -> None:
    assert v9.signal_target("fixed_150", 1.49, 1.0) == 0.0
    assert v9.signal_target("fixed_150", 1.50, 0.0) == 1.0
    assert v9.signal_target("fixed_200", 2.00, 0.0) == 1.0
    assert v9.signal_target("dynamic_080", 2.0, 0.79) == 0.0
    assert v9.signal_target("dynamic_080", 0.0, 0.80) == 1.0
    assert v9.signal_target("dynamic_085", 0.0, 0.85) == 1.0


def test_fixed_score_exactly_reuses_v5() -> None:
    frames = v9.v7.core.v2.load_inputs()
    daily, _ = v9.v7.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    scored = v9.valuation_score_frame(daily)
    expected = daily.apply(lambda row: v9.v5.absolute_state(row)["absolute_risk"], axis=1)
    assert np.allclose(scored["fixed_risk"], expected, atol=0.0, rtol=0.0)


def test_dynamic_score_is_causal_under_future_mutation() -> None:
    dates = pd.bdate_range("2000-01-03", periods=700)
    base = pd.DataFrame(
        {
            "date": dates,
            "pb_aggregate": np.linspace(1.5, 3.5, len(dates)),
            "erp": np.linspace(0.04, -0.01, len(dates)),
            "trailing_dividend_contribution": np.linspace(0.025, 0.005, len(dates)),
        }
    )
    changed = base.copy()
    changed.loc[600:, ["pb_aggregate", "erp", "trailing_dividend_contribution"]] = [99.0, -9.0, -1.0]
    left = v9.rolling_dynamic_score(base)
    right = v9.rolling_dynamic_score(changed)
    assert np.allclose(left.iloc[:600], right.iloc[:600], atol=0.0, rtol=0.0)


def test_real_signal_panel_is_binary_and_next_open() -> None:
    frames = v9.v7.core.v2.load_inputs()
    daily, _ = v9.v7.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, state_summary, current = v9.build_signal_panel(frames["ic"], daily)
    assert len(current) == len(v9.SIGNAL_VARIANTS)
    assert schedule["three_tier_target_fraction"].isin([0.0, 1.0]).all()
    regular = schedule[~schedule["initial_exception"]]
    assert (regular["execution_date"] > regular["eval_date"]).all()
    assert set(state_summary["signal_variant"]) == set(v9.SIGNAL_VARIANTS)
    current_lookup = current.set_index("signal_variant")["target_fraction"].to_dict()
    assert current_lookup["fixed_150"] == 0.0
    assert current_lookup["dynamic_075"] == 1.0
    assert current_lookup["dynamic_080"] == 1.0
    assert current_lookup["dynamic_085"] == 0.0
    assert signals["dynamic_risk"].between(0.0, 1.0).all()
