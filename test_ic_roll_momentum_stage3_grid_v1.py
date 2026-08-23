from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "ic_roll_momentum_stage3_grid_v1"


def test_formal_validation_passes() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    assert validation["all_checks_passed"] is True
    assert validation["grid_model_real_parity_max_abs"] <= 1e-12
    assert validation["grid_entries"] == validation["grid_exits"] == 3
    assert validation["grid_holding_days"] == 130


def test_expected_pairs_and_windows() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    assert len(metrics) == 36
    assert set(metrics["window"]) == {"full", "10y", "5y", "3y", "1y", "real_put_period"}
    pairwise = pd.read_csv(OUTPUT / "grid_increment_by_window.csv")
    assert len(pairwise) == 18
    assert set(pairwise["base"]) == {"no_put", "bare_put", "both_put"}


def test_no_grid_parity_and_cash() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    for base in ("no_put", "bare_put", "both_put"):
        assert validation[f"{base}_no_grid_stage2_parity_max_abs"] <= 1e-15
    assert min(validation["min_cash_weight"].values()) >= 0
