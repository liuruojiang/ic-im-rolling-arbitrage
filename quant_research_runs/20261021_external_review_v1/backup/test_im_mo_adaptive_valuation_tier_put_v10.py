from __future__ import annotations

import pandas as pd

import im_mo_adaptive_valuation_tier_put_v10 as subject


def test_frozen_inputs_and_spec_hashes() -> None:
    hashes = subject.verify_inputs(require_fresh_output=False)
    assert hashes[str(subject.SPEC.relative_to(subject.ROOT))] == subject.SPEC_SHA256
    assert hashes[str(subject.V7_STATES.relative_to(subject.ROOT))] == subject.V7_STATES_SHA256
    assert hashes[str(subject.V9_DAILY.relative_to(subject.ROOT))] == subject.V9_DAILY_SHA256


def test_candidate_bundle_is_frozen_and_complete() -> None:
    definitions = subject.candidate_definitions()
    assert len(definitions) == 8
    assert definitions["candidate"].nunique() == 8
    assert subject.PRIMARY in set(definitions["candidate"])
    assert subject.LEGACY in set(definitions["candidate"])
    assert set(subject.WINDOW_NEIGHBORS).issubset(set(definitions["candidate"]))
    assert set(subject.LADDER_NEIGHBORS).issubset(set(definitions["candidate"]))
    assert definitions["structure"].eq("3m_monthly_exit").all()
    assert definitions["moneyness"].eq(0.95).all()


def test_v7_schedule_is_causal_and_preserves_frozen_tier() -> None:
    states = subject.load_v7_states()
    real_dates = pd.DatetimeIndex(
        pd.read_csv(subject.v4.UPSTREAM, usecols=["date"], parse_dates=["date"])["date"]
    )
    source = subject.V7_SOURCE_MAP[subject.PRIMARY]
    schedule = subject.build_v7_schedule(states, source, subject.PRIMARY, real_dates)
    assert schedule["execution_date"].gt(schedule["eval_date"]).all()
    assert schedule["binary_target_qty"].between(0, 3).all()
    lookup = states[states["candidate"].eq(source)].set_index("date")["final_tier"]
    expected = schedule["eval_date"].map(lookup).astype(int)
    pd.testing.assert_series_equal(
        schedule["binary_target_qty"].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


def test_v7_dual_identity_and_current_trading_sample_state() -> None:
    states = subject.load_v7_states()
    selected = states[states["candidate"].eq(subject.V7_SOURCE_MAP[subject.PRIMARY])]
    assert selected["final_tier"].eq(
        selected[["absolute_tier", "relative_tier"]].max(axis=1)
    ).all()
    last = selected.sort_values("date").iloc[-1]
    assert last["date"] == subject.v6.END
    assert int(last["final_tier"]) in {1, 2}


def test_cost_stress_only_penalizes_trade_cost_days() -> None:
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    daily = pd.DataFrame(
        {
            "layer": ["real"] * 3,
            "candidate": ["test"] * 3,
            "date": dates,
            "gross_ret": [0.0, 0.0, 0.0],
            "put_pnl_ret": [0.0, 0.0, 0.0],
            "cost_rate": [0.0, 0.0, 0.0],
            "put_cost_rate": [0.0001, 0.0, 0.0001],
            "put_mark_fraction": [0.0, 0.0, 0.0],
            "ret": [0.0, 0.0, 0.0],
            "cash_ret": [0.0, 0.0, 0.0],
        }
    )
    stress_daily, _ = subject.cost_sensitivity(daily)
    pivot = stress_daily.pivot(index="date", columns="cost_multiplier", values="ret_stress")
    assert pivot.loc[dates[0], 5.0] < pivot.loc[dates[0], 2.0] < pivot.loc[dates[0], 1.0]
    assert pivot.loc[dates[1], 5.0] == pivot.loc[dates[1], 2.0] == pivot.loc[dates[1], 1.0]

