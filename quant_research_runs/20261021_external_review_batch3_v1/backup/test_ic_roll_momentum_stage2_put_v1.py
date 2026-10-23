from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "ic_roll_momentum_stage2_put_v1"


def test_formal_validation_passes() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    assert validation["all_checks_passed"] is True
    assert validation["no_put_stage1_parity_max_abs"] <= 1e-15
    assert validation["frozen_v2_real_put_component_parity_max_abs"] <= 1e-12


def test_expected_windows_and_strategies() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    assert set(metrics["window"]) == {"full", "10y", "5y", "3y", "1y", "real_put_period"}
    assert set(metrics["strategy"]) == {
        "roll50_momentum50_no_put", "put_bare50_only", "put_both_sleeves"
    }
    assert len(metrics) == 18


def test_put_layer_splice_and_cash() -> None:
    daily = pd.read_csv(OUTPUT / "daily_nav.csv.gz", parse_dates=["date"])
    before = daily[daily["date"].lt("2022-09-19")]
    after = daily[daily["date"].ge("2022-09-19")]
    assert set(before["put_data_layer"]) == {"model"}
    assert set(after["put_data_layer"]) == {"real"}
    assert daily["put_bare50_only_cash_weight"].min() >= 0
    assert daily["put_both_sleeves_cash_weight"].min() >= 0
