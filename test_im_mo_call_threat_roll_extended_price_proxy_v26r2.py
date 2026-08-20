import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_mo_call_threat_roll_extended_price_proxy_v26r2"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_spec_and_output_script_hashes() -> None:
    spec = ROOT / "docs" / "im_mo_call_threat_roll_extended_price_proxy_v26r2_spec.md"
    expected = (spec.parent / f"{spec.name}.sha256").read_text(encoding="utf-8").split()[0]
    assert sha256(spec) == expected
    manifest = json.loads((OUTPUT / "data_manifest.json").read_text(encoding="utf-8"))
    assert manifest["script_sha256"] == sha256(
        ROOT / "im_mo_call_threat_roll_extended_price_proxy_v26r2.py"
    )
    assert "price index" in manifest["proxy_scope"]


def test_audit_and_price_proxy_decision() -> None:
    audit = json.loads((OUTPUT / "audit_summary.json").read_text(encoding="utf-8"))
    decision = json.loads(
        (OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    assert audit["all_pass"] is True
    assert audit["threat_rule_errors"] == 0
    assert audit["causality_errors"] == 0
    assert audit["post_qivx_sigma_parity_max_abs"] == 0
    assert decision["conclusion"] == "extended_price_proxy_axis_dependent"
    assert decision["normal_axis_pass"] is True
    assert decision["pe20_60_axis_pass"] is False
    assert decision["live_approved"] is False


def test_all_candidates_have_mandatory_windows() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    expected = {"full", "last_10y", "last_5y", "last_3y", "last_1y"}
    assert metrics.groupby("candidate")["window"].apply(set).eq(expected).all()
    assert metrics["available"].astype(bool).all()
    assert metrics[["ann_return", "max_dd"]].notna().all().all()


def test_price_proxy_pairwise_risk_gates() -> None:
    pairs = pd.read_csv(OUTPUT / "pair_comparison.csv")
    assert len(pairs) == 6
    assert (pairs["max_call_delta_improvement"] >= 0.10).all()
    assert (pairs["threat_rolls"] >= 5).all()
    assert pairs[pairs["axis"].eq("normal")]["hard_pass"].astype(bool).all()
    assert not pairs[pairs["axis"].eq("pe20_60")]["hard_pass"].astype(bool).any()


def test_all_rescues_move_strike_up_and_expiry_out() -> None:
    trades = pd.read_csv(
        OUTPUT / "call_trades.csv", parse_dates=["old_expiry", "new_expiry"]
    )
    rolls = trades[trades["reason"].eq("threat_roll")].copy()
    assert len(rolls) == 344
    assert (rolls["new_expiry"] > rolls["old_expiry"]).all()
    old_strike = rolls["old_contract"].str.rsplit("_", n=1).str[-1].astype(float)
    new_strike = rolls["new_contract"].str.rsplit("_", n=1).str[-1].astype(float)
    assert (rolls["target_strike"] >= old_strike * 1.05 - 1e-6).all()
    assert (new_strike >= rolls["target_strike"] - 1e-6).all()
