import json

import pandas as pd

import im_mo_call_valuation_profit_roll_v24 as v24


def test_frozen_spec_and_inputs_are_unchanged() -> None:
    assert v24.sha256(v24.SPEC) == v24.SPEC_SHA256
    assert v24.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v24.SPEC_SHA256
    for path, expected in v24.FROZEN_HASHES.items():
        assert v24.sha256(path) == expected


def test_formal_outputs_pass_integrity_and_incremental_gates() -> None:
    audit = json.loads((v24.OUTPUT / "audit_summary.json").read_text(encoding="utf-8"))
    decision = json.loads(
        (v24.OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    assert audit["all_pass"] is True
    assert audit["tp80_rule_errors"] == 0
    assert audit["tp80_ratio_max_abs_error"] == 0.0
    assert decision["conclusion"] == "valuation_tp80_supported_real_short_sample"
    assert decision["control_tp80_pass"] is True
    assert decision["valuation_tp80_pass"] is True


def test_real_tp80_rolls_use_farther_expiry_and_iv26() -> None:
    trades = pd.read_csv(
        v24.OUTPUT / "call_trades.csv",
        parse_dates=["old_expiry", "new_expiry"],
    )
    tp = trades[trades["layer"].eq("real") & trades["reason"].eq("tp80")]
    assert len(tp[tp["candidate"].eq(v24.CONTROL_1)]) == 7
    assert len(tp[tp["candidate"].eq(v24.FORMAL_1)]) == 6
    assert (tp["remaining_price_ratio"] <= v24.TP_REMAINING_RATIO + 1e-12).all()
    assert (tp["new_expiry"] > tp["old_expiry"]).all()
    assert (tp["gate_iv"] >= v24.IV_THRESHOLD - 1e-12).all()


def test_valuation_tp80_preserves_2024_rebound_and_marks_open_end_position() -> None:
    stress = pd.read_csv(v24.OUTPUT / "stress_period_metrics.csv")
    no_tp = v24.stress_value(
        stress, v24.FORMAL_0, "rebound_2024_0918_1008", "total_return"
    )
    tp = v24.stress_value(
        stress, v24.FORMAL_1, "rebound_2024_0918_1008", "total_return"
    )
    assert abs(tp - no_tp) < 1e-15

    daily = pd.read_csv(
        v24.OUTPUT / "daily_candidates.csv.gz",
        parse_dates=["date", "call_expiry"],
    )
    last = daily[
        daily["layer"].eq("real") & daily["candidate"].eq(v24.FORMAL_1)
    ].sort_values("date").iloc[-1]
    assert last["date"] == pd.Timestamp("2026-08-14")
    assert last["call_contract"] == "MO2609-C-8400"
    assert last["call_expiry"] == pd.Timestamp("2026-09-18")
