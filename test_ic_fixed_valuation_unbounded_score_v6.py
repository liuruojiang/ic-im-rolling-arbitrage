from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ic_fixed_valuation_unbounded_score_v6 import (
    EXPECTED_HASHES,
    FAMILIES,
    THRESHOLDS,
    raw_threshold_map,
    unbounded_components,
)

ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_unbounded_score_v6"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_unbounded_score_v6_valuation_body_unbounded_mean_median_threshold"
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


def test_unbounded_anchors_direction_shift_and_two_of_three_equivalence() -> None:
    frame = pd.DataFrame(
        {
            "pb_aggregate": [1.0, 1.5, 2.0, 2.5, 3.0],
            "erp": [0.060, 0.045, 0.030, 0.015, 0.000],
            "trailing_dividend_contribution": [0.040, 0.030, 0.020, 0.010, 0.000],
        }
    )
    knot = unbounded_components(frame, "knot")
    mid = unbounded_components(frame, "mid")
    expected = [-1, 0, 1, 2, 3]
    for name in ("pb", "erp", "dividend"):
        np.testing.assert_allclose(knot[f"unbounded_{name}_pressure_knot"], expected)
        np.testing.assert_allclose(
            knot[f"unbounded_{name}_pressure_knot"]
            - mid[f"unbounded_{name}_pressure_mid"],
            0.50,
        )
    np.testing.assert_allclose(knot["unbounded_mean_knot"], expected)
    np.testing.assert_allclose(knot["unbounded_median_knot"], expected)
    threshold_map = raw_threshold_map()
    for row in threshold_map.itertuples(index=False):
        raw_count = (
            frame["pb_aggregate"].ge(row.pb_at_least).astype(int)
            + frame["erp"].le(row.erp_at_most).astype(int)
            + frame["trailing_dividend_contribution"]
            .le(row.dividend_at_most)
            .astype(int)
        )
        assert np.array_equal(
            knot["unbounded_median_knot"].ge(row.threshold), raw_count.ge(2)
        )


def test_formal_score_formula_shift_and_v5_parity() -> None:
    monthly = pd.read_csv(OUTPUT / "monthly_unbounded_fixed_scores.csv")
    daily = pd.read_csv(OUTPUT / "daily_unbounded_fixed_scores.csv.gz")
    assert len(monthly) == 236
    assert len(daily) == 4_761
    for frame in (monthly, daily):
        for convention in ("knot", "mid"):
            columns = [
                f"unbounded_{name}_pressure_{convention}"
                for name in ("pb", "erp", "dividend")
            ]
            np.testing.assert_allclose(
                frame[f"unbounded_mean_{convention}"], frame[columns].mean(axis=1)
            )
            np.testing.assert_allclose(
                frame[f"unbounded_median_{convention}"], frame[columns].median(axis=1)
            )
        for name in ("pb", "erp", "dividend"):
            np.testing.assert_allclose(
                frame[f"unbounded_{name}_pressure_knot"]
                - frame[f"unbounded_{name}_pressure_mid"],
                0.50,
                atol=1e-12,
            )
        np.testing.assert_allclose(
            frame["unbounded_mean_knot"] - frame["unbounded_mean_mid"],
            0.50,
            atol=1e-12,
        )
        np.testing.assert_allclose(
            frame["unbounded_median_knot"] - frame["unbounded_median_mid"],
            0.50,
            atol=1e-12,
        )
    parity = pd.read_csv(OUTPUT / "v5_clipped_formula_parity.csv")
    assert len(parity) == 2 * 2 * 3
    assert parity["max_abs_error"].max() <= 1e-12


def test_threshold_selection_has_frozen_eight_gates_and_mechanical_platform() -> None:
    selection = pd.read_csv(OUTPUT / "threshold_selection.csv")
    integrity = json.loads(
        (OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    assert len(selection) == len(THRESHOLDS) == 31
    np.testing.assert_allclose(selection["threshold"], THRESHOLDS)
    gate_columns = [column for column in selection if column.startswith("gate_")]
    assert len(gate_columns) == 8
    assert np.array_equal(
        selection[gate_columns].all(axis=1), selection["all_individual_gates_pass"]
    )
    assert np.array_equal(
        selection[
            [
                "gate_mean_episode_count",
                "gate_mean_recent_tail_coverage",
                "gate_mean_local_boundary_sample",
                "gate_mean_broad_factor_evidence",
            ]
        ].all(axis=1),
        selection["mean_core_pass"],
    )
    assert np.array_equal(
        selection[
            [
                "gate_median_episode_count",
                "gate_median_recent_tail_coverage",
                "gate_median_local_boundary_sample",
            ]
        ].all(axis=1),
        selection["median_core_pass"],
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


def test_economic_threshold_map_and_boundaries_are_complete() -> None:
    threshold_map = pd.read_csv(OUTPUT / "raw_two_of_three_threshold_map.csv")
    economic = pd.read_csv(OUTPUT / "economic_boundary_map.csv")
    assert len(threshold_map) == 31
    np.testing.assert_allclose(threshold_map["pb_at_least"], 1.50 + 0.50 * THRESHOLDS)
    np.testing.assert_allclose(threshold_map["erp_at_most"], 0.045 - 0.015 * THRESHOLDS)
    np.testing.assert_allclose(
        threshold_map["dividend_at_most"], 0.030 - 0.010 * THRESHOLDS
    )
    assert len(economic) == 62
    assert set(economic["family"]) == set(FAMILIES)
    assert economic["local_months"].ge(0).all()
    assert economic["active_share_median_ge_1"].dropna().between(0, 1).all()


def test_historical_vintage_is_invariant_and_causal() -> None:
    vintage = pd.read_csv(
        OUTPUT / "vintage_formula_invariance.csv",
        parse_dates=["vintage_date", "history_start", "history_end"],
    )
    assert len(vintage) == 11
    assert (vintage["history_end"] <= vintage["vintage_date"]).all()
    assert not vintage["future_rows_used"].any()
    assert vintage["all_family_threshold_states_match"].all()
    error_columns = [column for column in vintage if column.endswith("max_abs_error")]
    assert vintage[error_columns].max().max() <= 1e-12


def test_gap_overlap_and_current_diagnostics_are_complete() -> None:
    gap = pd.read_csv(OUTPUT / "mean_median_gap_summary.csv")
    extremes = pd.read_csv(OUTPUT / "mean_median_extreme_months.csv")
    overlap = pd.read_csv(OUTPUT / "v5_overlap_coverage_diagnostic.csv")
    current = pd.read_csv(OUTPUT / "current_unbounded_state.csv")
    assert len(gap) == 4
    assert gap["mean_median_correlation"].between(-1, 1).all()
    assert gap["absolute_gap_max"].ge(gap["absolute_gap_p90"]).all()
    assert len(extremes) == 20
    assert extremes["absolute_mean_median_gap"].is_monotonic_decreasing
    assert len(overlap) == 2 * 2 * 11
    assert overlap["state_jaccard"].between(0, 1).all()
    assert len(current) == 1
    assert np.isclose(
        current["unbounded_mean_knot"].iloc[0] - current["unbounded_mean_mid"].iloc[0],
        0.50,
    )


def test_scan_protocol_artifacts_and_manifest() -> None:
    long = pd.read_csv(OUTPUT / "threshold_scan_summary.csv")
    wide = pd.read_csv(OUTPUT / "threshold_window_metrics.csv")
    scan_long = pd.read_csv(SCAN / "scan_summary.csv")
    scan_wide = pd.read_csv(SCAN / "window_metrics.csv")
    integrity = json.loads(
        (OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    assert long["candidate"].nunique() == 62
    assert len(long) == 310
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
        "joint_confirmation.png",
        "unbounded_score_history.png",
        "raw_threshold_map.png",
        "mean_median_gap.png",
    ):
        assert (OUTPUT / name).stat().st_size > 20_000
    manifest = json.loads((OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, expected_hash in manifest.items():
        assert digest(OUTPUT / name) == expected_hash
