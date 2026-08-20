from __future__ import annotations

import json

import pandas as pd

import im_valuation_window_ladder_scan_v7 as model


def test_frozen_inputs_and_spec_are_unchanged() -> None:
    verified = model.verify_inputs(require_fresh_output=False)
    assert len(verified) == len(model.INPUT_HASHES)
    assert len(model.candidate_definitions()) == model.EXPECTED_CANDIDATES


def test_expanded_threshold_grid_is_finite_and_causal() -> None:
    inputs = model.load_inputs()
    samples = model.build_samples(inputs["daily"], inputs["monthly"])
    thresholds = model.rolling_thresholds(samples)
    available = thresholds[thresholds["available"]]
    assert len(thresholds) == (
        model.EXPECTED_MONTHLY_ROWS * len(model.WINDOW_MONTHS) * len(model.LADDERS)
    )
    assert (available["sample_months"] == available["window_months"]).all()
    assert available["strictly_increasing"].all()
    assert int(thresholds["future_rows_used"].sum()) == 0
    assert (
        pd.to_datetime(available["max_input_date"])
        < pd.to_datetime(available["effective_month"])
    ).all()


def test_formal_dual_states_obey_max_identity() -> None:
    states = pd.read_csv(
        model.OUTPUT / "daily_candidate_states.csv.gz",
        usecols=["candidate", "absolute_tier", "relative_tier", "final_tier"],
    )
    dual = states[states["candidate"].str.startswith("dual_")]
    expected = dual[["absolute_tier", "relative_tier"]].max(axis=1)
    assert dual["final_tier"].equals(expected)


def test_selected_center_has_required_connected_support() -> None:
    gates = pd.read_csv(model.OUTPUT / "candidate_gate_grid.csv")
    components = pd.read_csv(model.OUTPUT / "platform_components.csv")
    selected = gates[gates["selected_center"]]
    assert len(selected) == 1
    assert selected.iloc[0]["candidate"] == "dual_w57_q750_850_950"
    assert bool(selected.iloc[0]["candidate_pass"])
    assert int(selected.iloc[0]["passing_neighbor_count"]) == 4
    assert len(components) == 1
    assert bool(components.iloc[0]["qualifies"])
    assert int(components.iloc[0]["cell_count"]) == 10


def test_formal_output_is_integral_and_research_only() -> None:
    integrity = json.loads(
        (model.OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    decision = json.loads(
        (model.OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (model.OUTPUT / "data_manifest.json").read_text(encoding="utf-8")
    )
    assert integrity["integrity_pass"] is True
    assert integrity["threshold_future_rows_used"] == 0
    assert integrity["vintage_threshold_max_abs_error"] == 0.0
    assert integrity["unexpected_monthly_roll_rows"] == 0
    assert manifest["script_sha256"] == model.sha256(model.ROOT / f"{model.VERSION}.py")
    assert decision["selected_candidate"] == "dual_w57_q750_850_950"
    assert decision["selection_uses_strategy_outcomes"] is False
    assert decision["semantic_calibration_is_independent_oos"] is False
    assert decision["live_approved"] is False
