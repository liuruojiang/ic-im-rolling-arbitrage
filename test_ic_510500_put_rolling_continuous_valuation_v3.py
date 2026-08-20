from pathlib import Path

import pandas as pd

import ic_510500_put_rolling_continuous_valuation_v3 as research
import ic_510500_put_full_cycle_valuation_v2 as v2
import ic_510500_put_proxy_validation_v1 as proxy


def test_frozen_spec_hash_and_grid_count() -> None:
    assert research.sha256(research.SPEC) == research.SPEC_SHA256
    assert research.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == research.SPEC_SHA256
    assert len(research.ECON_VARIANTS) == 9
    assert len(research.ALL_VARIANTS) == 13


def test_decimal_half_up_target_rounding() -> None:
    assert research.round_target(0.04) == 0.0
    assert research.round_target(0.05) == 0.1
    assert research.round_target(0.14) == 0.1
    assert research.round_target(0.15) == 0.2
    assert research.round_target(1.5) == 1.0


def test_continuous_mapping_boundaries() -> None:
    assert research.map_score(0.4, 0.4, 0.8) == (0.0, 0.0)
    assert research.map_score(0.6, 0.4, 0.8) == (0.4999999999999999, 0.5)
    assert research.map_score(0.8, 0.4, 0.8) == (1.0, 1.0)


def test_economic_score_does_not_double_count_direct_pe() -> None:
    full = pd.read_csv(research.v2.FULL_STATES_PATH, parse_dates=["date"])
    full = full[full["product"].eq("IC")]
    day = pd.Timestamp("2026-08-14")
    row = full.iloc[-1].copy()
    first = research.risk_score_on_day(day, 10, row, full)
    changed = row.copy()
    changed["pe_aggregate_ttm"] = float(row["pe_aggregate_ttm"]) * 10.0
    second = research.risk_score_on_day(day, 10, changed, full)
    assert first["economic_risk"] == second["economic_risk"]
    assert first["equal4_risk"] != second["equal4_risk"]


def test_rolling_history_is_causal_and_has_minimum_months() -> None:
    frames = v2.load_inputs()
    daily, _ = v2.build_daily_valuation_full(frames["states_full"], frames["states_legacy"])
    day = pd.Timestamp("2015-04-15")
    row = daily.set_index("date").loc[day]
    result = research.risk_score_on_day(day, 8, row, frames["states_full"])
    assert result["history_months"] >= research.MIN_HISTORY_MONTHS
    assert result["history_end"] <= day
    assert result["history_start"] >= day - pd.DateOffset(years=8)


def test_regular_execution_is_next_trading_day() -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(["2026-08-12", "2026-08-13", "2026-08-14"]))
    execution, initial = proxy.next_execution(pd.Timestamp("2026-08-13"), research.MODEL_START, dates)
    assert execution == pd.Timestamp("2026-08-14")
    assert not initial


def test_output_manifest_after_optional_formal_run() -> None:
    assert isinstance(research.OUTPUT, Path)
    if research.OUTPUT.exists():
        assert (research.OUTPUT / "data_manifest.json").exists()
        assert (research.OUTPUT / "candidate_decisions.csv").exists()
