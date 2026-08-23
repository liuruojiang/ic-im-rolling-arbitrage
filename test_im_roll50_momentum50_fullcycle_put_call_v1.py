import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_call_v1"


def test_constant_one_call_reconstruction_parity() -> None:
    validation = json.loads((OUTPUT / "validation.json").read_text(encoding="utf-8"))
    parity = validation["constant_one_call_reconstruction"]
    assert parity["pass"] is True
    assert parity["overall_max_abs_diff"] <= 1e-12
    assert validation["daily_audit"]["no_call_baseline_parity_max_abs"] <= 1e-12


def test_sleeve_scales_cash_and_return_paths() -> None:
    daily = pd.read_csv(OUTPUT / "daily_nav.csv.gz", parse_dates=["date"])
    assert daily["date"].is_monotonic_increasing
    assert not daily["date"].duplicated().any()
    assert set(daily["momentum_weight"].unique()) == {0.0, 0.5, 1.0}
    active_bare = daily.loc[daily["call_bare_only_call_target_scale"].gt(0), "call_bare_only_call_target_scale"]
    assert set(active_bare.unique()) == {0.5}
    for strategy in ("no_call", "call_bare_only", "call_both_sleeves"):
        assert daily[f"{strategy}_cash_weight"].min() >= 0.0
        assert np.isfinite(daily[f"{strategy}_ret"]).all()
        assert daily[f"{strategy}_ret"].min() > -1.0


def test_report_windows_and_expected_ranking() -> None:
    metrics = pd.read_csv(OUTPUT / "metrics_by_window.csv")
    assert set(metrics["window"]) == {"full", "10y", "5y", "3y", "1y"}
    pivot = metrics.pivot(index="window", columns="strategy", values="ann_return")
    assert (pivot["call_bare_only"] > pivot["call_both_sleeves"]).all()
