import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_call_grid_v1"


def test_formal_validation_and_parity() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    assert validation["checks"]["all_checks_passed"] is True
    assert validation["checks"]["independent_grid_parity_max_abs"] <= 1e-12
    assert validation["daily_audit"]["no_grid_baseline_parity_max_abs"] <= 1e-12
    assert validation["daily_audit"]["momentum_t_plus_1_alignment_max_abs"] == 0.0


def test_grid_state_cash_and_event_counts() -> None:
    daily = pd.read_csv(OUTPUT / "daily_nav.csv.gz", parse_dates=["date"])
    assert daily["date"].is_monotonic_increasing
    assert not daily["date"].duplicated().any()
    assert int(daily["grid_independent_overlay_buy"].sum()) == 2
    assert int(daily["grid_momentum_guided_overlay_buy"].sum()) == 1
    assert int(daily["grid_independent_overlay_held_eod"].sum()) == 10
    assert int(daily["grid_momentum_guided_overlay_held_eod"].sum()) == 7
    for strategy in ("no_grid", "grid_independent", "grid_momentum_guided"):
        assert daily[f"{strategy}_cash_weight"].min() >= 0.0
        assert np.isfinite(daily[f"{strategy}_ret"]).all()
        assert daily[f"{strategy}_ret"].min() > -1.0


def test_independent_grid_historical_result_exceeds_guided() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    pivot = metrics.pivot(index="window", columns="strategy", values="ann_return")
    for window in ("full", "10y", "5y", "3y"):
        assert pivot.loc[window, "grid_independent"] > pivot.loc[window, "grid_momentum_guided"]
    assert pivot.loc["1y", "grid_independent"] == pivot.loc["1y", "grid_momentum_guided"]
