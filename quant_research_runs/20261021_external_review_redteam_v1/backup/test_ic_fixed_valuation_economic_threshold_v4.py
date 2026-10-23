from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ic_fixed_valuation_economic_threshold_v4 import (
    EXPECTED_HASHES,
    HALF_LIVES,
    OLD_THRESHOLDS,
    THRESHOLDS,
    component_col,
    old_fixed_risk,
    raw_col,
)


ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_economic_threshold_v4"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_economic_threshold_v4_valuation_body_equal3_raw_risk_threshold"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_spec_and_inputs() -> None:
    spec = ROOT / "docs" / f"{VERSION}_spec.md"
    expected = (ROOT / "docs" / f"{VERSION}_spec.md.sha256").read_text(encoding="utf-8").split()[0]
    assert digest(spec) == expected
    for path, expected_hash in EXPECTED_HASHES.items():
        assert digest(path) == expected_hash


def test_equal3_formula_parity_and_old_fixed_score() -> None:
    monthly = pd.read_csv(OUTPUT / "monthly_equal3_threshold_scores.csv")
    daily = pd.read_csv(OUTPUT / "daily_equal3_threshold_scores.csv.gz")
    upstream = pd.read_csv(
        ROOT
        / "outputs"
        / "ic_fixed_valuation_factor_structure_v3_1"
        / "monthly_factor_structure_scores.csv"
    )
    assert len(monthly) == 236
    assert len(daily) == 4_761
    for half_label in HALF_LIVES:
        expected = sum(
            monthly[component_col(factor, half_label)]
            for factor in ("pb", "erp", "dividend")
        ) / 3.0
        np.testing.assert_allclose(monthly[raw_col("equal3", half_label)], expected, atol=1e-12)
        np.testing.assert_allclose(
            monthly[raw_col("equal3", half_label)],
            upstream[raw_col("equal3", half_label)],
            atol=1e-12,
        )
    np.testing.assert_allclose(monthly["old_fixed_risk"], old_fixed_risk(monthly), atol=1e-12)
    np.testing.assert_allclose(daily["old_fixed_risk"], old_fixed_risk(daily), atol=1e-12)


def test_economic_boundary_grid_and_balance() -> None:
    economic = pd.read_csv(OUTPUT / "economic_boundary_map.csv")
    assert len(economic) == 3 * len(THRESHOLDS)
    assert set(economic["half_label"]) == set(HALF_LIVES)
    assert set(np.round(economic["threshold"], 2)) == set(THRESHOLDS)
    assert economic["local_months"].ge(0).all()
    for column in (
        "active_share_at_least2_components_ge_050",
        "active_share_at_least2_components_ge_060",
        "active_share_all3_components_ge_050",
    ):
        assert economic[column].dropna().between(0, 1).all()


def test_threshold_selection_follows_frozen_gates() -> None:
    selection = pd.read_csv(OUTPUT / "threshold_selection.csv")
    integrity = json.loads((OUTPUT / "integrity_checks.json").read_text(encoding="utf-8"))
    gate_columns = [column for column in selection if column.startswith("gate_")]
    assert len(selection) == len(THRESHOLDS)
    assert len(gate_columns) == 8
    assert np.array_equal(selection[gate_columns].all(axis=1), selection["all_individual_gates_pass"])
    decision = integrity["decision"]
    if decision["platform_found"]:
        selected = selection[selection["in_selected_band"]]
        assert len(selected) >= 3
        assert np.allclose(np.diff(selected["threshold"]), 0.01)
        assert selection["is_design_center"].sum() == 1
    else:
        assert not selection["in_selected_band"].any()
        assert not selection["is_design_center"].any()
        assert decision["design_center_threshold"] is None


def test_vintage_audit_is_causal_and_complete() -> None:
    vintage = pd.read_csv(
        OUTPUT / "vintage_threshold_stability.csv",
        parse_dates=["vintage_date", "history_end"],
    )
    assert len(vintage) == 11 * len(THRESHOLDS)
    assert vintage["vintage_date"].nunique() == 11
    assert (vintage["history_end"] <= vintage["vintage_date"]).all()
    assert vintage["causal_vs_final_jaccard"].between(0, 1).all()


def test_old_fixed_crosswalk_complete() -> None:
    crosswalk = pd.read_csv(OUTPUT / "old_fixed_coverage_crosswalk.csv")
    assert len(crosswalk) == len(OLD_THRESHOLDS) * 4
    assert set(crosswalk["old_fixed_threshold"]) == set(OLD_THRESHOLDS)
    assert set(crosswalk["scope"]) == {
        "full_monthly",
        "full_daily",
        "recent10_monthly",
        "recent10_daily",
    }
    assert crosswalk["closest_equal3_raw_threshold"].between(0.50, 0.90).all()
    assert crosswalk["absolute_activation_difference"].ge(0).all()


def test_scan_windows_have_context_only_metrics() -> None:
    scan = pd.read_csv(OUTPUT / "threshold_scan_summary.csv")
    summary = pd.read_csv(SCAN / "scan_summary.csv")
    wide = pd.read_csv(SCAN / "window_metrics.csv")
    assert scan["candidate"].nunique() == 123
    assert len(scan) == 615
    assert scan.groupby("candidate")["segment"].nunique().eq(5).all()
    assert scan.groupby("segment")["ann_return"].nunique().eq(1).all()
    assert scan.groupby("segment")["max_dd"].nunique().eq(1).all()
    assert scan["metric_semantics"].eq(
        "underlying_price_index_context_only_no_strategy_return"
    ).all()
    assert set(summary["candidate"]) == set(wide["candidate"])


def test_current_state_and_artifacts() -> None:
    current = pd.read_csv(OUTPUT / "current_threshold_state.csv")
    assert len(current) == 3
    expected = (current["pb_risk"] + current["erp_risk"] + current["dividend_risk"]) / 3
    np.testing.assert_allclose(current["equal3_raw"], expected, atol=1e-12)
    integrity = json.loads((OUTPUT / "integrity_checks.json").read_text(encoding="utf-8"))
    assert integrity["status"] == "passed"
    assert not integrity["strategy_returns_used_for_selection"]
    assert integrity["candidate_count"] == 123
    assert integrity["scan_rows"] == 615
    for name in (
        "threshold_stability.png",
        "economic_boundary_map.png",
        "vintage_threshold_jaccard.png",
        "old_fixed_coverage_crosswalk.png",
    ):
        assert (OUTPUT / name).stat().st_size > 25_000
    manifest = json.loads((OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, expected_hash in manifest.items():
        assert digest(OUTPUT / name) == expected_hash
