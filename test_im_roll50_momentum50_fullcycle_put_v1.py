from __future__ import annotations

import numpy as np
import pandas as pd

import im_roll50_momentum50_fullcycle_put_v1 as subject


def load_daily() -> pd.DataFrame:
    return pd.read_csv(subject.OUTPUT / "daily_nav.csv.gz", parse_dates=["date"], low_memory=False)


def test_spec_hash_and_common_sample() -> None:
    assert subject.sha256(subject.SPEC) == subject.SPEC_HASH.read_text(encoding="utf-8").split()[0]
    daily = load_daily()
    assert daily["date"].min() == pd.Timestamp("2015-04-16")
    assert daily["date"].max() == pd.Timestamp("2026-08-14")
    assert not daily.duplicated("date").any()
    assert pd.Timestamp("2018-06-18") not in set(daily["date"])


def test_put_scaling_and_cash_recomposition() -> None:
    daily = load_daily()
    assert np.allclose(daily["put_fixed_0p5_core_put_scale"], 0.5, atol=0.0)
    assert np.allclose(
        daily["put_scaled_total_exposure_put_scale"], daily["total_im_units"], atol=0.0
    )
    for label in ("put_fixed_0p5_core", "put_scaled_total_exposure"):
        expected_pre_cash = (
            (1.0 + daily["baseline_pre_cash_ret"] + daily[f"{label}_put_pnl_ret"])
            * (1.0 - daily[f"{label}_put_cost_rate"])
            - 1.0
        )
        expected = expected_pre_cash + daily[f"{label}_cash_weight"] * subject.CASH_DAILY
        assert np.allclose(daily[f"{label}_pre_cash_ret"], expected_pre_cash, atol=1e-14)
        assert np.allclose(daily[f"{label}_ret"], expected, atol=1e-14)
        assert daily[f"{label}_cash_weight_raw"].min() >= -1e-12


def test_current_signal_rule_and_causality() -> None:
    state = pd.read_csv(subject.OUTPUT / "put_signal_state.csv.gz", parse_dates=["date"])
    expected = np.maximum(state["valuation_tier"], state["mom120_floor_qty"])
    assert np.array_equal(state["target_qty"].to_numpy(), expected.to_numpy())
    assert set(state["target_qty"].unique()).issubset({0, 1, 2, 3, 4})
    negative = state["momentum_120"].notna() & state["momentum_120"].lt(0.0)
    assert np.array_equal(state["mom120_active"].astype(bool).to_numpy(), negative.to_numpy())
    schedule = pd.read_csv(
        subject.OUTPUT / "theoretical_put_schedule.csv.gz",
        parse_dates=["eval_date", "execution_date"],
    )
    assert schedule["execution_date"].gt(schedule["eval_date"]).all()


def test_real_put_components_equal_frozen_v2() -> None:
    daily = load_daily()
    used = daily[daily["put_source"].eq("real_mo_frozen_v2")][
        ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]
    ]
    frozen = pd.read_csv(subject.REAL_V2, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["product"].eq("IM") & frozen["candidate"].eq(subject.REAL_CANDIDATE)
    ][used.columns]
    joined = used.merge(frozen, on="date", suffixes=("_used", "_frozen"), validate="one_to_one")
    assert len(joined) == len(used) == len(frozen)
    for column in ("put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"):
        assert np.allclose(joined[f"{column}_used"], joined[f"{column}_frozen"], atol=1e-14)
