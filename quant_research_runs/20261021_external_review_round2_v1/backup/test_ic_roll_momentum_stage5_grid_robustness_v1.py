from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260823_ic_roll_momentum_50_50_ic_roll_momentum_stage5_grid_robustness_v1_"
    "ic_valuation_grid_entry_exit_thresholds_and_grid_guidance"
)


def test_standard_scan_artifacts_and_candidates() -> None:
    summary = pd.read_csv(RUN / "scan_summary.csv")
    wide = pd.read_csv(RUN / "window_metrics.csv")
    assert summary["candidate"].nunique() == 19
    assert len(wide) == 19
    assert set(summary["segment"]) >= {"full", "last_10y", "last_5y", "last_3y", "last_1y"}


def test_default_paths_reproduce_stage4() -> None:
    parity = pd.read_csv(RUN / "parity_checks.csv")
    assert parity["pass"].all()
    assert parity["cash_ret_max_abs"].max() <= 1e-12


def test_cycle_and_leave_one_outputs() -> None:
    cycles = pd.read_csv(RUN / "cycle_attribution.csv")
    loo = pd.read_csv(RUN / "leave_one_cycle_out.csv")
    assert set(cycles["mode"]) == {"independent", "guided"}
    assert cycles["cycle_id"].nunique() == 3
    assert len(loo) == 6


def test_decision_is_machine_readable() -> None:
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    checks = json.loads((RUN / "integrity_checks.json").read_text(encoding="utf-8"))
    assert meta["decision"] in {"keep_default", "watchlist"}
    assert meta["stability_label"] in {"wide_stable", "data_sensitive"}
    assert checks["all_daily_finite"] is True
    assert checks["min_cash_weight"] >= 0
