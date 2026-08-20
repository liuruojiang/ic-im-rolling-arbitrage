import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_mo_call_valuation_threat_roll_v25r2"
FORMAL = "article_pe20_60_hysteresis_iv26_daily_threat5_up5_next1_max5"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_specs_match_sidecars() -> None:
    for version in [
        "im_mo_call_valuation_threat_roll_v25",
        "im_mo_call_valuation_threat_roll_v25r1",
        "im_mo_call_valuation_threat_roll_v25r2",
    ]:
        spec = ROOT / "docs" / f"{version}_spec.md"
        expected = (ROOT / "docs" / f"{version}_spec.md.sha256").read_text(
            encoding="utf-8"
        ).split()[0]
        assert sha256(spec) == expected


def test_final_audit_and_decision_pass() -> None:
    audit = json.loads((OUTPUT / "audit_summary.json").read_text(encoding="utf-8"))
    decision = json.loads(
        (OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    assert audit["all_pass"] is True
    assert audit["formal_state_errors"] == 0
    assert audit["telemetry_repair_parity_pass"] is True
    assert decision["conclusion"] == "valuation_threat_roll_supported_real_short_sample"
    assert decision["valuation_threat_pass"] is True
    assert decision["live_approved"] is False


def test_formal_real_threat_events_are_exact() -> None:
    trades = pd.read_csv(OUTPUT / "threat_trade_audit.csv")
    actual = trades[(trades["layer"] == "real") & (trades["candidate"] == FORMAL)]
    assert list(actual["reason"]) == [
        "threat_stop_no_contract",
        "threat_roll",
        "threat_roll",
    ]
    rolls = actual[actual["reason"] == "threat_roll"]
    assert list(rolls["old_contract"]) == ["MO2605-C-8600", "MO2606-C-9200"]
    assert list(rolls["new_contract"]) == ["MO2606-C-9200", "MO2607-C-9700"]
    assert (rolls["new_expiry"] > rolls["old_expiry"]).all()
    assert (rolls["target_strike"] <= rolls["new_contract"].str.extract(r"C-(\d+)")[0].astype(float)).all()


def test_formal_exposure_reduction_and_return_gate() -> None:
    decision = pd.read_csv(OUTPUT / "decision_table.csv")
    row = decision[decision["candidate"] == FORMAL].iloc[0]
    assert bool(row["return_gate"])
    assert bool(row["exposure_gate"])
    assert row["max_call_delta_improvement"] >= 0.05
    assert row["max_margin_improvement"] >= 0.02
    assert row["real_threat_rolls"] == 2
