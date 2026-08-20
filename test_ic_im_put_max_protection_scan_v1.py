from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ic_im_put_max_protection_scan_v1 as scan


def test_preregistered_spec_hash_and_candidate_grid_are_fixed():
    assert scan.sha256(scan.SPEC) == scan.SPEC_SHA256
    assert scan.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == scan.SPEC_SHA256
    labels = [item["candidate"] for item in (*scan.IC_CANDIDATES, *scan.IM_CANDIDATES)]
    assert len(labels) == len(set(labels)) == 11
    assert "IC_baseline_075" in labels
    assert "IM_baseline_3" in labels


def _ic_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_delta": [0.25, 0.50, 0.75, 0.75],
            "unbounded_median_knot": [1.95, 2.05, 2.12, 2.25],
            "binary_target_fraction": [0.25, 0.50, 0.75, 0.75],
            "three_tier_target_fraction": [0.25, 0.50, 0.75, 0.75],
            "risk_tier": [1, 2, 3, 3],
            "signal_variant": ["l190_mom25"] * 4,
        }
    )


def test_ic_direct_top_upgrade_and_new_fourth_tier_differ():
    direct = scan.build_ic_schedule(_ic_schedule(), scan.IC_CANDIDATES[1])
    added = scan.build_ic_schedule(_ic_schedule(), scan.IC_CANDIDATES[3])
    assert direct["target_delta"].tolist() == [0.25, 0.50, 1.0, 1.0]
    assert added["target_delta"].tolist() == [0.25, 0.50, 0.75, 1.0]
    assert direct["risk_tier"].tolist() == [1, 2, 4, 4]
    assert added["risk_tier"].tolist() == [1, 2, 3, 4]


def _im_schedule() -> pd.DataFrame:
    dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"])
    return pd.DataFrame(
        {
            "eval_date": dates,
            "binary_target_qty": [1, 3, 3, 2],
            "three_tier_target_qty": [1, 3, 3, 2],
            "valuation_tier": [1, 3, 2, 2],
            "mom120_active": [False, False, True, False],
            "candidate": ["valmom_center_floor3"] * 4,
            "schedule_candidate": ["valmom_center_floor3"] * 4,
        }
    )


def _im_state() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]),
            "score_state": [2.0, 2.5, 2.68, 2.71],
            "percentile_state": [0.7, 0.9, 0.95, 1.0],
        }
    )


def test_im_direct_top_momentum_floor_and_new_fourth_tier_differ():
    direct = scan.build_im_schedule(_im_schedule(), scan.IM_CANDIDATES[1], _im_state())
    momentum = scan.build_im_schedule(_im_schedule(), scan.IM_CANDIDATES[2], _im_state())
    added = scan.build_im_schedule(_im_schedule(), scan.IM_CANDIDATES[4], _im_state())
    assert direct["binary_target_qty"].tolist() == [1, 4, 4, 2]
    assert momentum["binary_target_qty"].tolist() == [1, 3, 4, 2]
    assert added["binary_target_qty"].tolist() == [1, 3, 3, 4]


def test_metric_formula_uses_cash_return_and_daily_drawdown():
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"]),
            "cash_ret": [0.10, -0.20, 0.05],
            "put_cost_rate": [0.001, 0.0, 0.001],
            "put_mark_fraction": [0.01, 0.02, 0.01],
            "cash_weight_raw": [0.69, 0.68, 0.69],
        }
    )
    row = scan.metric_row(
        "IC_baseline_075", "IC", "full", frame, scan.IC_CANDIDATES[0]
    )
    nav = np.prod([1.10, 0.80, 1.05])
    assert row["ann_return"] == pytest.approx(nav ** (252 / 3) - 1)
    assert row["max_dd"] == pytest.approx(-0.20)
    assert row["put_cost_total"] == pytest.approx(0.002)
