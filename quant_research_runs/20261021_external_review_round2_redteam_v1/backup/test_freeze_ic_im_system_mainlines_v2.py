from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "ic_im_system_mainlines_v2"


def test_all_v2_integrity_checks_pass() -> None:
    data = json.loads((OUTPUT / "integrity_checks.json").read_text(encoding="utf-8"))
    assert data["all_checks_passed"] is True
    assert all(data["checks"].values())
    assert data["ic_v1_baseline_cash_ret_max_abs"] <= 1e-12
    assert data["im_v1_baseline_cash_ret_max_abs"] <= 1e-12


def test_v2_state_contains_selected_four_tier_rules() -> None:
    state = json.loads((OUTPUT / "mainline_state.json").read_text(encoding="utf-8"))
    assert state["ic"]["put_valuation_thresholds"] == [1.90, 1.95, 2.00, 2.05]
    assert state["ic"]["put_target_delta"] == [0.25, 0.50, 0.75, 1.00]
    assert state["ic"]["mom120_negative_floor_delta"] == 0.50
    assert state["ic"]["call"] == "excluded"
    assert state["im"]["put_relative_quantiles"] == [0.75, 0.85, 0.90, 0.925]
    assert state["im"]["mom120_negative_floor_puts"] == 4
    assert state["im"]["max_puts_per_core_im"] == 4
    assert state["im"]["rescue_expiry"] == "rescue_next_listed"


def test_current_state_is_ic_50pct_and_im_four_puts() -> None:
    current = pd.read_csv(OUTPUT / "current_state.csv")
    ic = current[current["product"].eq("IC")].iloc[0]
    im = current[current["product"].eq("IM")].iloc[0]
    assert ic["target"] == pytest.approx(0.50)
    assert int(ic["put_qty"]) == 26
    assert ic["put_contract"] == "510500P2609M07250"
    assert int(im["target"]) == 4
    assert int(im["put_qty"]) == 4
    assert im["put_contract"] == "MO2610-P-6600"


def test_v2_full_metrics_match_formal_record() -> None:
    metrics = pd.read_csv(OUTPUT / "mainline_metrics.csv")
    full = metrics[metrics["window"].eq("full")].set_index("product")
    assert full.loc["IC", "ann_return"] == pytest.approx(0.3397479107427816)
    assert full.loc["IC", "max_dd"] == pytest.approx(-0.1579485748983755)
    assert full.loc["IM", "ann_return"] == pytest.approx(0.3178445915615868)
    assert full.loc["IM", "max_dd"] == pytest.approx(-0.1498462179185934)


def test_im_operational_15pct_eod_capital_is_below_100pct() -> None:
    data = pd.read_csv(OUTPUT / "im_operational_capital_15pct.csv")
    assert data["operational_eod_capital_15pct"].max() == pytest.approx(
        0.5983929923799728
    )
    assert data["operational_eod_capital_15pct"].max() <= 1.0


def test_v2_output_manifest() -> None:
    manifest = json.loads((OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, metadata in manifest.items():
        payload = (OUTPUT / name).read_bytes()
        assert len(payload) == metadata["size"]
        assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]

