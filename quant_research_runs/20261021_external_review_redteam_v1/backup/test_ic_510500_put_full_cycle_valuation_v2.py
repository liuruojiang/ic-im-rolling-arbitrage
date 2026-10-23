from pathlib import Path

import pandas as pd

import ic_510500_put_full_cycle_valuation_v2 as research
import ic_510500_put_proxy_validation_v1 as proxy


def test_frozen_spec_hash_and_sidecar() -> None:
    assert research.sha256(research.SPEC) == research.SPEC_SHA256
    assert research.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == research.SPEC_SHA256


def test_preregistered_state_boundaries() -> None:
    full = pd.read_csv(research.FULL_STATES_PATH, parse_dates=["date"])
    legacy = pd.read_csv(research.LEGACY_STATES_PATH, parse_dates=["date"])
    full = full[full["product"].eq("IC")]
    legacy = legacy[legacy["product"].eq("IC")]
    assert full["date"].min() == pd.Timestamp("2007-01-31")
    assert legacy["date"].min() == pd.Timestamp("2008-01-31")
    assert full["date"].max() == research.END
    assert legacy["date"].max() == research.END


def test_risk_ecdf_directions() -> None:
    calibration = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert research.risk_ecdf(4.0, calibration, True) == 1.0
    assert research.risk_ecdf(4.0, calibration, False) == 0.0
    assert research.risk_ecdf(1.0, calibration, True) == 0.25
    assert research.risk_ecdf(1.0, calibration, False) == 0.75


def test_absolute_gate_boundaries() -> None:
    high = pd.Series(
        {
            "pe_aggregate_ttm": 35.0,
            "pb_aggregate": 3.0,
            "erp": -0.0001,
            "trailing_dividend_contribution": 0.0049,
        }
    )
    medium = pd.Series(
        {
            "pe_aggregate_ttm": 25.0,
            "pb_aggregate": 2.0,
            "erp": 0.005,
            "trailing_dividend_contribution": 0.008,
        }
    )
    low = pd.Series(
        {
            "pe_aggregate_ttm": 24.99,
            "pb_aggregate": 1.99,
            "erp": 0.0101,
            "trailing_dividend_contribution": 0.0101,
        }
    )
    assert research.absolute_four_factor_signal(high)["target_fraction"] == 1.0
    assert research.absolute_four_factor_signal(medium)["target_fraction"] == 0.5
    assert research.absolute_four_factor_signal(low)["target_fraction"] == 0.0


def test_frozen_ecdf_extremes() -> None:
    calibration = pd.DataFrame(
        {
            "pe_aggregate_ttm": [10.0, 20.0, 30.0],
            "pb_aggregate": [1.0, 2.0, 3.0],
            "erp": [0.0, 0.01, 0.02],
            "trailing_dividend_contribution": [0.005, 0.01, 0.02],
        }
    )
    high = pd.Series(
        {
            "pe_aggregate_ttm": 40.0,
            "pb_aggregate": 4.0,
            "erp": -0.01,
            "trailing_dividend_contribution": 0.001,
        }
    )
    low = pd.Series(
        {
            "pe_aggregate_ttm": 5.0,
            "pb_aggregate": 0.5,
            "erp": 0.03,
            "trailing_dividend_contribution": 0.03,
        }
    )
    assert research.frozen_ecdf_signal(high, calibration)["target_fraction"] == 1.0
    assert research.frozen_ecdf_signal(low, calibration)["target_fraction"] == 0.0


def test_regular_execution_is_strictly_after_evaluation() -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(["2026-08-12", "2026-08-13", "2026-08-14"]))
    execution, initial = proxy.next_execution(pd.Timestamp("2026-08-13"), research.MODEL_START, dates)
    assert execution == pd.Timestamp("2026-08-14")
    assert not initial


def test_formal_output_path_and_manifest_after_optional_run() -> None:
    assert isinstance(research.OUTPUT, Path)
    if research.OUTPUT.exists():
        assert (research.OUTPUT / "data_manifest.json").exists()
        assert (research.OUTPUT / "metrics_by_window.csv").exists()
