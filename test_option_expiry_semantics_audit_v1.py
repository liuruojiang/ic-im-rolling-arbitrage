from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs/option_expiry_semantics_audit_v1"


def test_all_audit_checks_pass() -> None:
    data = json.loads((OUTPUT / "audit_checks.json").read_text(encoding="utf-8"))
    assert data["audit_complete"] is True
    assert all(data["checks"].values())


def test_fixed_dte_bands_and_rank_exception() -> None:
    data = pd.read_csv(OUTPUT / "observed_dte_summary.csv")
    v12 = data[(data["scope"] == "IC Call v12") & (data["metric"] == "signal_dte")].iloc[0]
    v10 = data[(data["scope"] == "IC Call v10") & (data["metric"] == "signal_dte")].iloc[0]
    assert 45 <= v12["min"] <= v12["max"] <= 75
    assert v10["max"] >= 121


def test_rescue_and_strict_cycle_findings() -> None:
    data = pd.read_csv(OUTPUT / "observed_dte_summary.csv")
    rescue = data[(data["scope"] == "IM Call current") & (data["metric"] == "rescue_signal_dte")].iloc[0]
    strict = data[(data["scope"] == "IC Put strict") & (data["metric"] == "ic_rolls_covered")]
    assert rescue["max"] >= 300
    assert (strict["min"] == 3).all() and (strict["max"] == 3).all()


def test_output_manifest_hashes() -> None:
    import hashlib

    manifest = json.loads((OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, metadata in manifest["files"].items():
        payload = (OUTPUT / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]
        assert len(payload) == metadata["bytes"]

