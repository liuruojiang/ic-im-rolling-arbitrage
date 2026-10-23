import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_mo_call_threat_roll_extended_proxy_v26r1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_specs_match_sidecars() -> None:
    for version in [
        "im_mo_call_threat_roll_extended_proxy_v26",
        "im_mo_call_threat_roll_extended_proxy_v26r1",
    ]:
        spec = ROOT / "docs" / f"{version}_spec.md"
        expected = (ROOT / "docs" / f"{version}_spec.md.sha256").read_text(
            encoding="utf-8"
        ).split()[0]
        assert sha256(spec) == expected


def test_audit_passes_and_decision_remains_diagnostic() -> None:
    audit = json.loads((OUTPUT / "audit_summary.json").read_text(encoding="utf-8"))
    decision = json.loads(
        (OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    assert audit["all_pass"] is True
    assert audit["threat_rule_errors"] == 0
    assert audit["causality_errors"] == 0
    assert audit["pe_future_errors"] == 0
    assert audit["post_qivx_sigma_parity_max_abs"] == 0
    assert decision["conclusion"] == "extended_proxy_axis_dependent"
    assert decision["normal_axis_pass"] is True
    assert decision["pe20_60_axis_pass"] is False
    assert decision["live_approved"] is False


def test_all_candidates_have_mandatory_windows() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    expected_windows = {"full", "last_10y", "last_5y", "last_3y", "last_1y"}
    assert metrics.groupby("candidate")["window"].apply(set).eq(expected_windows).all()
    assert metrics["available"].astype(bool).all()
    assert metrics[["ann_return", "max_dd"]].notna().all().all()


def test_long_proxy_threat_rolls_move_up_and_out() -> None:
    trades = pd.read_csv(
        OUTPUT / "call_trades.csv",
        parse_dates=["old_expiry", "new_expiry"],
    )
    rolls = trades[trades["reason"].eq("threat_roll")].copy()
    assert len(rolls) >= 200
    assert (rolls["new_expiry"] > rolls["old_expiry"]).all()
    assert (rolls["target_strike"] > 0).all()
    old_strike = rolls["old_contract"].str.rsplit("_", n=1).str[-1].astype(float)
    new_strike = rolls["new_contract"].str.rsplit("_", n=1).str[-1].astype(float)
    # Contract labels serialize model strikes to six decimals.
    assert (rolls["target_strike"] >= old_strike * 1.05 - 1e-6).all()
    assert (new_strike >= rolls["target_strike"] - 1e-6).all()


def test_rescue_reduces_max_call_delta_in_all_scenarios_and_axes() -> None:
    pairs = pd.read_csv(OUTPUT / "pair_comparison.csv")
    assert len(pairs) == 6
    assert (pairs["max_call_delta_improvement"] >= 0.10).all()
    assert (pairs["threat_rolls"] >= 5).all()
    assert pairs[pairs["axis"].eq("normal")]["hard_pass"].astype(bool).all()
    assert not pairs[pairs["axis"].eq("pe20_60")]["hard_pass"].astype(bool).any()
