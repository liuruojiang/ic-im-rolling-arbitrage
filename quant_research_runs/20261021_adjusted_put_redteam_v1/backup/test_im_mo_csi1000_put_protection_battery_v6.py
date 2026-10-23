from __future__ import annotations

import math

import pandas as pd
import pytest

import im_mo_csi1000_put_protection_battery_v6 as study
import im_valuation_frequency_tenor_scan_v4 as v4


@pytest.fixture(scope="module")
def market():
    return study.model_market()[0]


@pytest.fixture(scope="module")
def real_inputs():
    upstream, _, decisions, states, tri, raw_options = v4.load_inputs()
    daily_valuation, diffs = v4.build_daily_valuation()
    return upstream, decisions, states, tri, raw_options, daily_valuation, diffs


def test_frozen_spec_data_and_dependencies():
    assert study.sha256(study.SPEC) == study.SPEC_SHA256
    assert study.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == study.SPEC_SHA256
    manifest = study.json.loads(study.DATA_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["output"]["sha256"] == study.sha256(study.OHLC)
    assert study.sha256(study.Path(v4.__file__).resolve()) == study.V4_SHA256


def test_long_market_is_maximal_observable_qvix_window(market):
    assert market["date"].min() == study.MODEL_START
    assert market["date"].max() == study.END
    assert len(market) == 2756
    assert market["close_relative_error"].median() <= 0.0005
    assert market["close_relative_error"].max() <= 0.005
    assert market[["sigma_open", "sigma_close"]].gt(0).all().all()


def test_candidate_battery_is_complete_and_unique():
    definitions = study.candidate_definitions()
    assert len(definitions) == 48
    assert not definitions["candidate"].duplicated().any()
    assert definitions["group"].value_counts().to_dict() == {
        "valuation_grid": 18,
        "tool_grid": 12,
        "momentum_scan": 10,
        "signal_front95": 8,
    }
    tool = definitions[definitions["group"].eq("tool_grid")]
    assert set(tool["structure"]) == set(study.STRUCTURES)
    assert set(tool["moneyness"]) == set(study.MONEYNESS)


def test_black_scholes_put_is_monotone_in_strike_and_sigma():
    low_strike = study.proxy.bs_put(100, 85, 0.02, 0.01, 0.25, 0.25)
    high_strike = study.proxy.bs_put(100, 95, 0.02, 0.01, 0.25, 0.25)
    high_sigma = study.proxy.bs_put(100, 95, 0.02, 0.01, 0.35, 0.25)
    assert 0 < low_strike < high_strike < high_sigma


def test_signal_schedule_is_t_plus_one_and_momentum_literal(market, real_inputs):
    _, _, _, _, _, daily_valuation, diffs = real_inputs
    assert max(diffs.values()) <= 1e-14
    state = study.signal_state(daily_valuation)
    dates = pd.DatetimeIndex(market["date"])
    schedule = study.daily_signal_schedule("mom120", "mom120", dates, state)
    regular = schedule[~schedule["initial_listing_exception"]]
    assert (regular["execution_date"] > regular["eval_date"]).all()
    joined = state[["date", "tri_close_all", "momentum_120", "mom120"]].copy()
    literal = joined["tri_close_all"] / joined["tri_close_all"].shift(120) - 1.0
    assert (joined["momentum_120"] - literal).abs().dropna().max() <= 1e-14
    assert joined.loc[joined["momentum_120"].notna(), "mom120"].isin([0, 2]).all()


def test_model_front95_smoke_has_exact_entry_ratio(market, real_inputs):
    _, _, _, _, _, daily_valuation, _ = real_inputs
    state = study.signal_state(daily_valuation)
    dates = pd.DatetimeIndex(market["date"])
    schedule = study.daily_signal_schedule("mom120", "mom120", dates, state)
    sample_market = market[market["date"] >= pd.Timestamp("2022-01-01")].reset_index(drop=True)
    sample_schedule = schedule[schedule["execution_date"] >= sample_market["date"].min()].copy()
    overlay, trades, _ = study.run_model_normal(sample_market, sample_schedule, "front", 0.95, "smoke")
    assert len(overlay) == len(sample_market)
    assert not overlay[["put_pnl_ret", "put_cost_rate", "put_mark_fraction"]].isna().any().any()
    assert len(trades) > 0
    assert trades.loc[trades["entry_moneyness"].notna(), "entry_moneyness"].eq(0.95).all()


def test_real_target_selector_is_nearest_liquid(real_inputs):
    upstream, _, _, _, raw_options, _, _ = real_inputs
    active = study.v5.active_im_opens(upstream)
    options = v4.prepare_options(raw_options, v4.actual_expiry_map(raw_options, upstream))
    day = study.REAL_START
    month = v4.selected_month(options, day, day)
    selected = study.target_selector(active, 0.90)(options, day, month)
    assert selected is not None
    im_open = float(active.loc[active["date"].eq(day), "open"].iloc[0])
    chain = options[
        options["date"].eq(day) & options["contract_month"].eq(month)
        & options["open"].gt(0) & options["volume"].gt(0) & options["open_interest"].gt(0)
    ].copy()
    chain["error"] = (chain["strike"] / im_open - 0.90).abs().round(12)
    expected = chain.sort_values(["error", "strike", "contract"]).iloc[0]
    assert selected["contract"] == expected["contract"]


def test_third_friday_and_three_cycle_selection(market):
    dates = pd.DatetimeIndex(market["date"])
    rolls = pd.DatetimeIndex(
        sorted({study.third_friday(pd.Timestamp(y, m, 1), dates) for y in range(2016, 2018) for m in range(1, 13)})
    )
    day = pd.Timestamp("2016-01-04")
    future = rolls[rolls >= day]
    months = [(pd.Timestamp(value.year, value.month, 1), pd.Timestamp(value)) for value in future[:6]]
    selected = study.three_cycle_month(day, months, rolls)
    assert selected is not None
    assert selected[1] == future[2]
    assert int(((rolls >= day) & (rolls <= selected[1])).sum()) == 3
