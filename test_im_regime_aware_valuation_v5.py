from __future__ import annotations

import json

import pandas as pd

import im_regime_aware_valuation_v5 as model


def test_frozen_inputs_and_spec_are_unchanged() -> None:
    verified = model.verify_inputs(require_fresh_output=False)
    assert len(verified) == len(model.INPUT_HASHES)


def test_weighted_quantile_is_deterministic() -> None:
    values = pd.Series([4.0, 1.0, 3.0, 2.0])
    weights = pd.Series([1.0, 1.0, 1.0, 1.0])
    assert model.weighted_quantile(values, weights, 0.25) == 1.0
    assert model.weighted_quantile(values, weights, 0.50) == 2.0
    assert model.weighted_quantile(values, weights, 0.75) == 3.0


def test_annual_calibration_is_strictly_causal() -> None:
    monthly = model.load_inputs()["monthly"]
    thresholds = model.annual_thresholds(monthly)
    available = thresholds[thresholds["available"]]
    assert int(available["year"].min()) == 2019
    assert available["strictly_increasing"].all()
    assert int(thresholds["future_rows_used"].sum()) == 0
    assert (
        pd.to_datetime(available["max_input_date"])
        <= pd.to_datetime(available["as_of"])
    ).all()


def test_dual_state_is_maximum_of_absolute_and_relative() -> None:
    inputs = model.load_inputs()
    definitions = model.candidate_definitions()
    thresholds = model.annual_thresholds(inputs["monthly"])
    daily, monthly = model.build_states(
        inputs["daily"], inputs["monthly"], thresholds, definitions
    )
    for frame in (daily, monthly):
        dual = frame[frame["candidate"].str.startswith("dual_")]
        expected = dual[["absolute_tier", "relative_tier"]].max(axis=1)
        assert dual["final_tier"].equals(expected)


def test_formal_output_passes_integrity_and_is_research_only() -> None:
    integrity = json.loads(
        (model.OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    decision = json.loads(
        (model.OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    assert integrity["integrity_pass"] is True
    assert integrity["threshold_future_rows_used"] == 0
    assert integrity["vintage_threshold_max_abs_error"] == 0.0
    assert decision["selection_uses_strategy_outcomes"] is False
    assert decision["live_approved"] is False
    assert decision["research_status"] == "RESEARCH_ONLY_NOT_LIVE_APPROVED"
