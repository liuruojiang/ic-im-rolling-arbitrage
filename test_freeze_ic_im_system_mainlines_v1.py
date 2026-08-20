from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs/ic_im_system_mainlines_v1"


def test_integrity_checks_all_pass() -> None:
    data = json.loads((OUTPUT / "integrity_checks.json").read_text(encoding="utf-8"))
    assert all(data["checks"].values())
    assert all(data["manifest_pass"].values())


def test_ic_excludes_call_and_im_includes_call() -> None:
    state = json.loads((OUTPUT / "mainline_state.json").read_text(encoding="utf-8"))
    assert state["ic"]["call"] == "excluded"
    assert "sell_call_d10_iv26_threat5" in state["im"]["components"]
    assert state["im"]["call_scope"] == "fixed_core_only"


def test_im_operational_capital_under_user_15pct_bound() -> None:
    data = pd.read_csv(OUTPUT / "im_operational_capital_15pct.csv")
    assert not data["operational_breach"].astype(bool).any()
    assert data["operational_morning_capital"].max() <= 1.0


def test_output_manifest() -> None:
    manifest = json.loads((OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, metadata in manifest.items():
        payload = (OUTPUT / name).read_bytes()
        assert len(payload) == metadata["size"]
        assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]

