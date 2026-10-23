from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ic_fixed_valuation_raw_economic_score_v5 import (
    EXPECTED_HASHES,
    OLD_THRESHOLDS,
    THRESHOLDS,
    fixed_components,
    old_fixed_risk,
)

ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_raw_economic_score_v5"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_raw_economic_score_v5_fixed_unit_threshold"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_spec_and_inputs() -> None:
    spec = ROOT / "docs" / f"{VERSION}_spec.md"
    expected = (
        (ROOT / "docs" / f"{VERSION}_spec.md.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert digest(spec) == expected
    for path, expected_hash in EXPECTED_HASHES.items():
        assert digest(path) == expected_hash


def test_fixed_formula_integer_anchors_direction_and_clipping() -> None:
    pb_frame = pd.DataFrame(
        {
            "pb_aggregate": [1.0, 1.5, 2.0, 2.5, 3.0],
            "erp": [0.03] * 5,
            "trailing_dividend_contribution": [0.02] * 5,
        }
    )
    erp_frame = pd.DataFrame(
        {
            "pb_aggregate": [2.0] * 5,
            "erp": [0.060, 0.045, 0.030, 0.015, 0.000],
            "trailing_dividend_contribution": [0.02] * 5,
        }
    )
    div_frame = pd.DataFrame(
        {
            "pb_aggregate": [2.0] * 5,
            "erp": [0.03] * 5,
            "trailing_dividend_contribution": [0.040, 0.030, 0.020, 0.010, 0.000],
        }
    )
    np.testing.assert_allclose(
        fixed_components(pb_frame, "knot")["pb_pressure_knot"], [0, 0, 1, 2, 2]
    )
    np.testing.assert_allclose(
        fixed_components(erp_frame, "knot")["erp_pressure_knot"], [0, 0, 1, 2, 2]
    )
    np.testing.assert_allclose(
        fixed_components(div_frame, "knot")["dividend_pressure_knot"], [0, 0, 1, 2, 2]
    )
    assert fixed_components(pb_frame, "knot")[
        "pb_pressure_knot"
    ].is_monotonic_increasing
    assert fixed_components(erp_frame, "knot")[
        "erp_pressure_knot"
    ].is_monotonic_increasing
    assert fixed_components(div_frame, "knot")[
        "dividend_pressure_knot"
    ].is_monotonic_increasing


def test_formal_scores_and_old_discrete_formula() -> None:
    monthly = pd.read_csv(OUTPUT / "monthly_fixed_economic_scores.csv")
    daily = pd.read_csv(OUTPUT / "daily_fixed_economic_scores.csv.gz")
    assert len(monthly) == 236
    assert len(daily) == 4_761
    for frame in (monthly, daily):
        for convention in ("knot", "mid"):
            components = [
                frame[f"{name}_pressure_{convention}"]
                for name in ("pb", "erp", "dividend")
            ]
            np.testing.assert_allclose(
                frame[f"fixed_equal3_{convention}"], sum(components) / 3.0, atol=1e-12
            )
            assert frame[f"fixed_equal3_{convention}"].between(0, 2).all()
        np.testing.assert_allclose(
            frame["old_fixed_risk"], old_fixed_risk(frame), atol=1e-12
        )


def test_threshold_grid_gates_and_platform_rule() -> None:
    selection = pd.read_csv(OUTPUT / "threshold_selection.csv")
    integrity = json.loads(
        (OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    assert len(selection) == len(THRESHOLDS) == 13
    np.testing.assert_allclose(selection["threshold"], THRESHOLDS)
    gate_columns = [column for column in selection if column.startswith("gate_")]
    assert len(gate_columns) == 7
    assert np.array_equal(
        selection[gate_columns].all(axis=1), selection["all_individual_gates_pass"]
    )
    decision = integrity["decision"]
    if decision["platform_found"]:
        selected = selection[selection["in_selected_band"]]
        assert len(selected) >= 3
        np.testing.assert_allclose(np.diff(selected["threshold"]), 0.05)
        assert selection["is_design_center"].sum() == 1
    else:
        assert not selection["in_selected_band"].any()
        assert not selection["is_design_center"].any()


def test_pre_registered_convention_pairing() -> None:
    sensitivity = pd.read_csv(OUTPUT / "convention_sensitivity.csv")
    assert len(sensitivity) == len(THRESHOLDS)
    np.testing.assert_allclose(
        sensitivity["paired_mid_threshold"], sensitivity["threshold"] - 0.50
    )
    for column in (
        "full_monthly_jaccard",
        "recent10_monthly_jaccard",
        "full_daily_jaccard",
        "recent10_daily_jaccard",
    ):
        assert sensitivity[column].between(0, 1).all()
    assert sensitivity["recent10_monthly_ratio_abs_diff"].between(0, 1).all()


def test_historical_vintage_formula_is_exactly_invariant() -> None:
    vintage = pd.read_csv(
        OUTPUT / "vintage_formula_invariance.csv",
        parse_dates=["vintage_date", "history_start", "history_end"],
    )
    assert len(vintage) == 11
    assert (vintage["history_end"] <= vintage["vintage_date"]).all()
    assert not vintage["future_rows_used"].any()
    assert vintage["all_threshold_states_match"].all()
    assert vintage["knot_score_max_abs_error"].max() <= 1e-12
    assert vintage["mid_score_max_abs_error"].max() <= 1e-12


def test_diagnostics_are_complete() -> None:
    economic = pd.read_csv(OUTPUT / "economic_boundary_map.csv")
    clipping = pd.read_csv(OUTPUT / "component_clipping_summary.csv")
    old = pd.read_csv(OUTPUT / "old_fixed_diagnostic.csv")
    current = pd.read_csv(OUTPUT / "current_fixed_state.csv")
    assert len(economic) == 13
    assert economic["local_months"].ge(0).all()
    assert len(clipping) == 2 * 2 * 2 * 3
    assert (
        clipping[
            [
                "clipped_at_zero_ratio",
                "clipped_at_two_ratio",
                "strictly_continuous_ratio",
            ]
        ]
        .apply(lambda column: column.between(0, 1).all())
        .all()
    )
    assert len(old) == len(OLD_THRESHOLDS) * 4
    assert set(old["old_fixed_threshold"]) == set(OLD_THRESHOLDS)
    assert len(current) == 1
    assert np.isclose(
        current["fixed_equal3_knot"].iloc[0],
        current[["pb_pressure_knot", "erp_pressure_knot", "dividend_pressure_knot"]]
        .iloc[0]
        .mean(),
    )


def test_scan_protocol_artifacts_and_manifest() -> None:
    long = pd.read_csv(OUTPUT / "threshold_scan_summary.csv")
    wide = pd.read_csv(OUTPUT / "threshold_window_metrics.csv")
    scan_long = pd.read_csv(SCAN / "scan_summary.csv")
    scan_wide = pd.read_csv(SCAN / "window_metrics.csv")
    integrity = json.loads(
        (OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    assert long["candidate"].nunique() == 13
    assert len(long) == 65
    assert long.groupby("candidate")["segment"].nunique().eq(5).all()
    assert long.groupby("segment")["ann_return"].nunique().eq(1).all()
    assert long.groupby("segment")["max_dd"].nunique().eq(1).all()
    assert (
        long["metric_semantics"]
        .eq("underlying_price_index_context_only_no_strategy_return")
        .all()
    )
    assert set(long["candidate"]) == set(wide["candidate"])
    assert set(scan_long["candidate"]) == set(scan_wide["candidate"])
    assert integrity["status"] == "passed"
    assert not integrity["strategy_returns_used_for_selection"]
    for name in (
        "threshold_structure.png",
        "convention_sensitivity.png",
        "economic_boundary_map.png",
        "fixed_score_history.png",
    ):
        assert (OUTPUT / name).stat().st_size > 20_000
    manifest = json.loads((OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, expected_hash in manifest.items():
        assert digest(OUTPUT / name) == expected_hash
