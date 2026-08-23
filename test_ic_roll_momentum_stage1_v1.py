import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "ic_roll_momentum_stage1_v1"


def test_validation_and_frozen_bare_parity() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    assert validation["all_checks_passed"] is True
    assert validation["audit"]["bare_frozen_parity_max_abs"] <= 1e-12
    assert validation["audit"]["signal_t_plus_1_alignment_max_abs"] == 0.0


def test_position_cash_and_flat_state() -> None:
    daily = pd.read_csv(OUTPUT / "daily_nav.csv.gz", parse_dates=["date"])
    assert daily["date"].is_monotonic_increasing
    assert not daily["date"].duplicated().any()
    assert set(daily["momentum_gated_ic_units"].unique()) == {0.0, 0.5, 1.0}
    assert set(daily["roll50_momentum50_ic_units"].unique()) == {0.5, 0.75, 1.0}
    flat = daily["momentum_gated_ic_units"].eq(0.0)
    assert daily.loc[flat, "momentum_gated_ic_futures_gross_ret"].abs().max() == 0.0
    assert daily.loc[flat, "momentum_roll_cost_rate"].abs().max() == 0.0
    assert (daily.loc[flat, "momentum_gated_ic_cash_weight"] - 1.0).abs().max() == 0.0
    for strategy in ("bare_roll_ic", "momentum_gated_ic", "roll50_momentum50_ic"):
        assert np.isfinite(daily[f"{strategy}_ret"]).all()
        assert daily[f"{strategy}_ret"].min() > -1.0


def test_window_results_have_expected_tradeoff() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    assert set(metrics["window"]) == {"full", "10y", "5y", "3y", "1y"}
    pivot_return = metrics.pivot(index="window", columns="strategy", values="ann_return")
    pivot_dd = metrics.pivot(index="window", columns="strategy", values="max_dd")
    assert pivot_return.loc["full", "roll50_momentum50_ic"] > pivot_return.loc["full", "bare_roll_ic"]
    assert pivot_dd.loc["full", "roll50_momentum50_ic"] > pivot_dd.loc["full", "bare_roll_ic"]
    assert pivot_return.loc["10y", "roll50_momentum50_ic"] < pivot_return.loc["10y", "bare_roll_ic"]
