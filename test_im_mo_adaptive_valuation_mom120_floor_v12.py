from __future__ import annotations

import pandas as pd

import im_mo_adaptive_valuation_mom120_floor_v12 as subject


def test_frozen_inputs_and_spec() -> None:
    observed = subject.verify_inputs(require_fresh_output=False)
    assert observed[str(subject.SPEC.relative_to(subject.ROOT))] == subject.SPEC_SHA256
    assert observed[str(subject.V11_DAILY.relative_to(subject.ROOT))] == subject.V11_DAILY_SHA256


def test_candidate_bundle_count_and_grid() -> None:
    definitions = subject.candidate_definitions()
    assert len(definitions) == 24
    assert definitions["candidate"].nunique() == 24
    assert set(definitions[definitions["family"].eq("mom_only")]["floor_qty"]) == {1, 2, 3}
    combined = definitions[definitions["family"].eq("combined")]
    assert len(combined) == 15
    assert set(combined["valuation_key"]) == set(subject.VALUATION_SOURCES)
    assert definitions["structure"].eq("3m_monthly_exit").all()
    assert definitions["moneyness"].eq(0.95).all()


def test_momentum_formula_and_combined_max_identity() -> None:
    daily_valuation, _ = subject.v4.build_daily_valuation()
    legacy = subject.v6.signal_state(daily_valuation)
    valuation = subject.v10.load_v7_states()
    state = subject.build_momentum_state(
        legacy,
        valuation,
        subject.VALUATION_SOURCES["center"],
        2,
        "combined",
    )
    expected_momentum = state["tri_close_all"] / state["tri_close_all"].shift(120) - 1.0
    valid = state["momentum_120"].notna() & expected_momentum.notna()
    assert (state.loc[valid, "momentum_120"] - expected_momentum.loc[valid]).abs().max() < 1e-14
    expected_target = state[["valuation_tier", "mom120_floor_qty"]].max(axis=1)
    pd.testing.assert_series_equal(
        state["target_qty"].reset_index(drop=True),
        expected_target.astype(int).reset_index(drop=True),
        check_names=False,
    )


def test_schedule_is_t_plus_one_and_preserves_target() -> None:
    daily_valuation, _ = subject.v4.build_daily_valuation()
    legacy = subject.v6.signal_state(daily_valuation)
    valuation = subject.v10.load_v7_states()
    state = subject.build_momentum_state(
        legacy,
        valuation,
        subject.VALUATION_SOURCES["center"],
        1,
        "combined",
    )
    dates = pd.DatetimeIndex(
        pd.read_csv(subject.v4.UPSTREAM, usecols=["date"], parse_dates=["date"])["date"]
    )
    schedule = subject.build_momentum_schedule(
        state, "valmom_center_floor1", dates, subject.VALUATION_SOURCES["center"]
    )
    assert schedule["execution_date"].gt(schedule["eval_date"]).all()
    assert schedule["binary_target_qty"].between(0, 3).all()
    expected = schedule[["valuation_tier", "mom120_floor_qty"]].max(axis=1).astype(int)
    pd.testing.assert_series_equal(
        schedule["binary_target_qty"].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


def test_return_tolerance_relaxes_only_when_both_layers_improve_eight_pp() -> None:
    normal = subject.allowed_return_lag(8.0, 7.999)
    relaxed = subject.allowed_return_lag(8.0, 8.0)
    assert normal == {
        "full": -1.0,
        "last_10y": -1.0,
        "last_5y": -1.0,
        "last_3y": -3.0,
        "last_1y": -3.0,
    }
    assert relaxed == {
        "full": -2.0,
        "last_10y": -2.0,
        "last_5y": -2.0,
        "last_3y": -4.0,
        "last_1y": -4.0,
    }

