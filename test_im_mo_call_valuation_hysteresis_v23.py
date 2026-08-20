import json

import pandas as pd

import im_mo_call_valuation_hysteresis_v23 as v23


def test_frozen_spec_and_inputs_are_unchanged() -> None:
    assert v23.sha256(v23.SPEC) == v23.SPEC_SHA256
    assert v23.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v23.SPEC_SHA256
    for path, expected in v23.FROZEN_HASHES.items():
        assert v23.sha256(path) == expected


def test_pe_percentiles_are_causal_and_anchor_states_match() -> None:
    formal = v23.build_pe_states(v23.FORMAL_HISTORY_START, "postpublication_formal")
    backfill = v23.build_pe_states(
        v23.BACKFILL_HISTORY_START, "prepublication_backfill_diagnostic"
    )
    assert formal["history_start"].min() == v23.FORMAL_HISTORY_START
    assert (formal["history_end"] <= formal["date"]).all()
    assert backfill["history_start"].min() == v23.BACKFILL_HISTORY_START

    formal_lookup = formal.set_index("date")
    backfill_lookup = backfill.set_index("date")
    assert formal_lookup.loc[pd.Timestamp("2022-07-22"), "valuation_state"] == "low_recovery"
    assert backfill_lookup.loc[pd.Timestamp("2022-07-22"), "valuation_state"] == "normal"
    assert formal_lookup.loc[pd.Timestamp("2024-09-26"), "valuation_state"] == "low_recovery"
    assert backfill_lookup.loc[pd.Timestamp("2024-09-26"), "valuation_state"] == "low_recovery"
    assert formal_lookup.loc[pd.Timestamp("2025-08-11"), "valuation_state"] == "normal"
    assert abs(float(formal_lookup.loc[pd.Timestamp("2024-09-26"), "pe_percentile_10y"]) - 0.12381345439537763) < 1e-15


def test_formal_outputs_pass_integrity_and_keep_diagnostic_separate() -> None:
    audit = json.loads((v23.OUTPUT / "audit_summary.json").read_text(encoding="utf-8"))
    decision = json.loads(
        (v23.OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    assert audit["all_pass"] is True
    assert audit["formal_prepublication_rows"] == 0
    assert decision["conclusion"] == "valuation_hysteresis_not_material"
    assert decision["selected_candidate"] == v23.CONTROL
    assert decision["prepublication_history_sensitive"] is True


def test_2024_rebound_skip_and_close_execution() -> None:
    signals = pd.read_csv(v23.OUTPUT / "signals.csv", parse_dates=["eval_date"])
    row = signals[
        signals["layer"].eq("real")
        & signals["candidate"].eq(v23.FORMAL)
        & signals["eval_date"].eq(pd.Timestamp("2024-09-25"))
    ].iloc[0]
    assert row["valuation_state"] == "low_recovery"
    assert row["action"] == "skip"
    assert pd.isna(row["contract"])

    trades = pd.read_csv(
        v23.OUTPUT / "call_trades.csv", parse_dates=["eval_date", "actual_execution_date"]
    )
    control = trades[
        trades["layer"].eq("real")
        & trades["candidate"].eq(v23.CONTROL)
        & trades["eval_date"].eq(pd.Timestamp("2024-09-25"))
    ].iloc[0]
    assert control["new_contract"] == "MO2412-C-5800"
    assert control["actual_execution_date"] == pd.Timestamp("2024-09-26")
