from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "ic_roll_momentum_stage4_grid_guidance_v1"


def test_formal_validation_passes() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    assert validation["all_checks_passed"] is True
    assert validation["guided_held_when_momentum_off"] == 0
    assert validation["guided_state_values"] == [0.0, 1.0]


def test_expected_strategies_and_windows() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    assert len(metrics) == 54
    assert set(metrics["mode"]) == {"no_grid", "independent", "guided"}
    assert set(metrics["base"]) == {"no_put", "bare_put", "both_put"}
    assert set(metrics["window"]) == {"full", "10y", "5y", "3y", "1y", "real_put_period"}


def test_independent_grid_parity_and_cash() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    for base in ("no_put", "bare_put", "both_put"):
        assert validation[f"{base}_independent_stage3_parity_max_abs"] <= 1e-15
    assert validation["independent_grid_component_parity_max_abs"] <= 1e-15
    assert min(validation["min_cash_weight"].values()) >= 0
