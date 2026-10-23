from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_extreme_valuation_absolute_momentum_v10 as v10


def test_frozen_spec_and_v9_dependency() -> None:
    assert v10.sha256(v10.SPEC) == v10.SPEC_SHA256
    assert v10.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v10.SPEC_SHA256
    assert v10.sha256(v10.V9_PATH) == v10.V9_SHA256


def test_absolute_momentum_formula() -> None:
    dates = pd.bdate_range("2020-01-01", periods=300)
    close = pd.Series(np.arange(1.0, 301.0))
    frame = pd.DataFrame(
        {
            "date": dates,
            "tri_close": close,
            "pb_aggregate": 2.0,
            "erp": 0.03,
            "trailing_dividend_contribution": 0.02,
        }
    )
    original = v10.v9.valuation_score_frame
    try:
        v10.v9.valuation_score_frame = lambda value: value.copy()
        scored = v10.momentum_score_frame(frame)
    finally:
        v10.v9.valuation_score_frame = original
    expected = close.iloc[250] / close.iloc[130] - 1.0
    assert scored.loc[250, "momentum_120"] == expected


def test_signal_or_logic_and_boundaries() -> None:
    momentum = {60: 0.1, 120: 0.1, 240: 0.1}
    assert v10.signal_target("fixed175_only", 1.74, momentum) == 0.0
    assert v10.signal_target("fixed175_only", 1.75, momentum) == 1.0
    assert v10.signal_target("mom120_only", 0.0, {60: 1.0, 120: 0.0, 240: 1.0}) == 1.0
    assert v10.signal_target("or_mom120_000", 1.75, momentum) == 1.0
    assert v10.signal_target("or_mom120_000", 0.0, {60: 1.0, 120: 0.0, 240: 1.0}) == 1.0
    assert v10.signal_target("or_mom120_000", 0.0, momentum) == 0.0
    assert v10.signal_target("or_mom120_m050", 0.0, {60: 0.0, 120: -0.05, 240: 0.0}) == 1.0
    assert v10.signal_target("or_mom120_p050", 0.0, {60: 0.0, 120: 0.05, 240: 0.0}) == 1.0


def test_momentum_is_causal_under_future_mutation() -> None:
    dates = pd.bdate_range("2000-01-03", periods=800)
    base = pd.DataFrame(
        {
            "date": dates,
            "tri_close": np.linspace(100.0, 200.0, len(dates)),
            "pb_aggregate": 2.0,
            "erp": 0.03,
            "trailing_dividend_contribution": 0.02,
        }
    )
    changed = base.copy()
    changed.loc[700:, "tri_close"] = 9999.0
    original = v10.v9.valuation_score_frame
    try:
        v10.v9.valuation_score_frame = lambda value: value.copy()
        left = v10.momentum_score_frame(base)
        right = v10.momentum_score_frame(changed)
    finally:
        v10.v9.valuation_score_frame = original
    assert np.allclose(
        left.loc[:699, ["momentum_60", "momentum_120", "momentum_240"]],
        right.loc[:699, ["momentum_60", "momentum_120", "momentum_240"]],
        equal_nan=True,
    )


def test_real_signal_panel_is_binary_next_open_and_covers_known_bear() -> None:
    frames = v10.v9.v7.core.v2.load_inputs()
    daily, _ = v10.v9.v7.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, state_summary, current = v10.build_signal_panel(frames["ic"], daily)
    state_summary = v10.enrich_state_summary(schedule, state_summary)
    assert len(current) == len(v10.SIGNAL_VARIANTS)
    assert schedule["three_tier_target_fraction"].isin([0.0, 1.0]).all()
    regular = schedule[~schedule["initial_exception"]]
    assert (regular["execution_date"] > regular["eval_date"]).all()
    assert set(state_summary["signal_variant"]) == set(v10.SIGNAL_VARIANTS)
    known = state_summary.set_index(["layer", "signal_variant"]).loc[("model", "or_mom120_000")]
    assert known["known_drawdown_signal_ratio"] >= 0.50
    lookup = current.set_index("signal_variant")["target_fraction"].to_dict()
    assert lookup["fixed175_only"] == 0.0
    assert lookup["mom120_only"] == 1.0
    assert lookup["or_mom120_000"] == 1.0
