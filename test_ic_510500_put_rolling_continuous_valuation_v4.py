from pathlib import Path

import pandas as pd

import ic_510500_put_rolling_continuous_valuation_v4 as research
import ic_510500_put_full_cycle_valuation_v2 as v2
import ic_510500_put_proxy_validation_v1 as proxy


def test_frozen_spec_framework_and_grid() -> None:
    assert research.sha256(research.SPEC) == research.SPEC_SHA256
    assert research.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == research.SPEC_SHA256
    assert research.sha256(research.V3_PATH) == research.V3_SHA256
    assert research.HISTORY_MONTHS == [84, 90, 96]
    assert len(research.ECON_VARIANTS) == 9
    assert len(research.ALL_VARIANTS) == 13


def test_variant_parameter_units() -> None:
    params = research.variant_parameters("econ_m90_l50_h90")
    assert params["window_months"] == 90
    assert params["window_years"] == 7.5
    assert params["lower_risk"] == 0.5
    assert params["full_risk"] == 0.9


def test_fixed_history_count_and_causality() -> None:
    frames = v2.load_inputs()
    daily, _ = v2.build_daily_valuation_full(frames["states_full"], frames["states_legacy"])
    day = pd.Timestamp("2015-09-29")
    row = daily.set_index("date").loc[day]
    for months in research.HISTORY_MONTHS:
        result = research.risk_score_on_day(day, months, row, frames["states_full"])
        assert result["history_months"] == months
        assert result["history_target_months"] == months
        assert result["history_end"] <= day


def test_economic_score_omits_direct_pe_duplicate() -> None:
    frames = v2.load_inputs()
    day = pd.Timestamp("2026-08-14")
    row = frames["states_full"].iloc[-1].copy()
    first = research.risk_score_on_day(day, 90, row, frames["states_full"])
    changed = row.copy()
    changed["pe_aggregate_ttm"] = float(row["pe_aggregate_ttm"]) * 10.0
    second = research.risk_score_on_day(day, 90, changed, frames["states_full"])
    assert first["economic_risk"] == second["economic_risk"]
    assert first["equal4_risk"] != second["equal4_risk"]


def test_continuous_rounding_is_decimal_half_up() -> None:
    assert research.core.round_target(0.05) == 0.1
    assert research.core.round_target(0.15) == 0.2
    assert research.core.map_score(0.6, 0.4, 0.8)[1] == 0.5


def test_regular_execution_is_strictly_next_day() -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(["2026-08-12", "2026-08-13", "2026-08-14"]))
    execution, initial = proxy.next_execution(pd.Timestamp("2026-08-13"), proxy.MODEL_START, dates)
    assert execution == pd.Timestamp("2026-08-14")
    assert not initial


def test_output_manifest_after_optional_formal_run() -> None:
    assert isinstance(research.OUTPUT, Path)
    if research.OUTPUT.exists():
        assert (research.OUTPUT / "data_manifest.json").exists()
        assert (research.OUTPUT / "candidate_decisions.csv").exists()
