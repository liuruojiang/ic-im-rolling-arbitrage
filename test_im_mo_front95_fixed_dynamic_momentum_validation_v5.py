from __future__ import annotations

import pandas as pd
import pytest

import im_mo_front95_fixed_dynamic_momentum_validation_v5 as study
import im_valuation_frequency_tenor_scan_v4 as v4


@pytest.fixture(scope="module")
def inputs():
    upstream, _, _, _, _, raw_options = v4.load_inputs()
    daily_valuation, feature_diffs = v4.build_daily_valuation()
    signal_state = study.build_signal_state(daily_valuation)
    active = study.active_im_opens(upstream)
    return upstream, raw_options, daily_valuation, feature_diffs, signal_state, active


def test_frozen_dependencies_and_spec_hashes():
    assert study.sha256(study.SPEC) == study.SPEC_SHA256
    assert study.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == study.SPEC_SHA256
    assert study.sha256(study.V4_PATH) == study.V4_SHA256


def test_fixed_risk_thresholds_are_literal():
    frame = pd.DataFrame(
        {
            "pb_aggregate": [1.99, 2.00, 2.50],
            "erp": [0.031, 0.030, 0.015],
            "trailing_dividend_contribution": [0.020, 0.019, 0.009],
        }
    )
    score = study.fixed_risk(frame)
    assert score.tolist() == pytest.approx([0.0, 1.0, 2.0])


def test_signal_state_and_t_plus_one_schedule_are_causal(inputs):
    upstream, _, _, feature_diffs, signal_state, _ = inputs
    assert max(feature_diffs.values()) <= 1e-14
    schedules, history, current = study.build_schedules(upstream, signal_state)
    assert set(schedules) == set(study.SIGNALS)
    assert len(current) == len(study.SIGNALS)
    assert history[["signal_variant", "eval_date"]].duplicated().sum() == 0
    for schedule in schedules.values():
        regular = schedule.loc[~schedule["initial_listing_exception"]]
        assert (regular["execution_date"] > regular["eval_date"]).all()
        assert schedule.iloc[0]["execution_date"] == study.START
        assert bool(schedule.iloc[0]["initial_listing_exception"])


def test_dynamic_score_does_not_change_when_future_rows_are_appended(inputs):
    _, _, daily_valuation, _, _, _ = inputs
    cutoff = pd.Timestamp("2024-12-31")
    prefix = daily_valuation.loc[daily_valuation["date"] <= cutoff].copy()
    full_score = study.dynamic_risk(daily_valuation)
    prefix_score = study.dynamic_risk(prefix)
    assert full_score.iloc[: len(prefix)].to_numpy() == pytest.approx(prefix_score.to_numpy())


def test_target95_selector_is_nearest_liquid_contract(inputs):
    upstream, raw_options, _, _, _, active = inputs
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    selector = study.target95_selector(active)
    day = study.START
    month = v4.selected_month(options, day, day)
    selected = selector(options, day, month)
    assert selected is not None
    chain = options.loc[
        options["date"].eq(day)
        & options["contract_month"].eq(month)
        & options["open"].notna()
        & options["open"].gt(0)
        & options["volume"].gt(0)
        & options["open_interest"].gt(0)
    ].copy()
    im_open = float(active.loc[active["date"].eq(day), "open"].iloc[0])
    chain["target_error"] = (chain["strike"] / im_open - 0.95).abs().round(12)
    expected = chain.sort_values(["target_error", "strike", "contract"]).iloc[0]
    assert selected["contract"] == expected["contract"]


def test_delay_is_measured_in_trading_days():
    dates = pd.DatetimeIndex(pd.to_datetime(["2024-01-05", "2024-01-08", "2024-01-09"]))
    scheduled = pd.to_datetime(pd.Series(["2024-01-06", "2024-01-05"]))
    actual = pd.to_datetime(pd.Series(["2024-01-09", "2024-01-08"]))
    delay = dates.searchsorted(actual) - dates.searchsorted(scheduled)
    assert delay.tolist() == [1, 1]
