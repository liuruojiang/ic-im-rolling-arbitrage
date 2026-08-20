from __future__ import annotations

import json

import pandas as pd

import im_finite_window_valuation_v6 as model


def test_frozen_inputs_and_spec_are_unchanged() -> None:
    verified = model.verify_inputs(require_fresh_output=False)
    assert len(verified) == len(model.INPUT_HASHES)


def test_rolling_thresholds_are_finite_and_causal() -> None:
    inputs = model.load_inputs()
    thresholds = model.rolling_thresholds(inputs["daily"], inputs["monthly"])
    available = thresholds[thresholds["available"]]
    assert (available["sample_months"] == available["window_months"]).all()
    assert available["strictly_increasing"].all()
    assert int(thresholds["future_rows_used"].sum()) == 0
    assert (
        pd.to_datetime(available["max_input_date"])
        < pd.to_datetime(available["effective_month"])
    ).all()
    earliest = {
        int(window): str(group["effective_month"].min().date())
        for window, group in available.groupby("window_months")
    }
    assert earliest == {
        48: "2019-10-01",
        60: "2020-10-01",
        72: "2021-10-01",
    }


def test_monthly_windows_roll_by_one_observation() -> None:
    inputs = model.load_inputs()
    thresholds = model.rolling_thresholds(inputs["daily"], inputs["monthly"])
    audit = model.rolling_window_audit(inputs["monthly"], thresholds)
    assert audit["roll_is_expected"].all()


def test_dual_state_is_maximum_of_absolute_and_relative() -> None:
    inputs = model.load_inputs()
    definitions = model.candidate_definitions()
    thresholds = model.rolling_thresholds(inputs["daily"], inputs["monthly"])
    daily, monthly = model.build_states(
        inputs["daily"], inputs["monthly"], thresholds, definitions
    )
    for frame in (daily, monthly):
        dual = frame[frame["candidate"].str.startswith("dual_")]
        expected = dual[["absolute_tier", "relative_tier"]].max(axis=1)
        assert dual["final_tier"].equals(expected)


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
    assert decision["selection_uses_strategy_outcomes"] is False
    assert decision["live_approved"] is False
    assert decision["research_status"] == "RESEARCH_ONLY_NOT_LIVE_APPROVED"
