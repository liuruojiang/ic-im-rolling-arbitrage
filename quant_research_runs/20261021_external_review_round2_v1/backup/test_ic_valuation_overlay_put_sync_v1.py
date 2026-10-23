from __future__ import annotations

import json

import pandas as pd
import pytest

import ic_valuation_overlay_put_sync_v1 as strategy


def toy_chain() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]),
            "contract": ["IC2601"] * 4,
            "open": [100.0, 102.0, 104.0, 103.0],
            "settle": [101.0, 103.0, 102.0, 104.0],
            "pre_settle": [100.0, 101.0, 103.0, 102.0],
            "raw_volume": [1000.0] * 4,
            "unbounded_median_knot": [0.50, 1.20, 2.10, 1.50],
            "roll_event": [False] * 4,
        }
    )


def test_overlay_uses_next_open_and_hysteresis() -> None:
    daily, trades, audit = strategy.simulate_overlay(toy_chain(), 1.0, 2.0)
    assert trades["action"].tolist() == ["buy", "sell"]
    assert trades["signal_date"].tolist() == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-07")]
    assert trades["execution_date"].tolist() == [pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-08")]
    assert daily["overlay_held_eod"].tolist() == [0, 1, 1, 0]
    assert daily.loc[1, "overlay_gross_ret"] == pytest.approx(103.0 / 102.0 - 1.0)
    assert daily.loc[2, "overlay_gross_ret"] == pytest.approx(102.0 / 103.0 - 1.0)
    assert daily.loc[3, "overlay_gross_ret"] == pytest.approx(103.0 / 102.0 - 1.0)
    assert audit["completed_cycles"] == 1


def test_sync_schedule_scales_only_when_overlay_is_on() -> None:
    base = pd.DataFrame(
        {
            "layer": ["model", "model"],
            "signal_variant": ["l190_mom25", "l190_mom25"],
            "eval_date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "execution_date": pd.to_datetime(["2026-01-06", "2026-01-07"]),
            "target_delta": [0.25, 0.50],
            "binary_target_fraction": [0.25, 0.50],
            "three_tier_target_fraction": [0.25, 0.50],
            "initial_exception": [False, False],
        }
    )
    overlay = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-06", "2026-01-07"]),
            "total_ic_units": [1.0, 2.0],
            "overlay_held_eod": [0, 1],
        }
    )
    sync = strategy.build_candidate_schedule(
        base, overlay, "model", "candidate", "sync_put_total_ic", 1.0, 2.0
    )
    core = strategy.build_candidate_schedule(
        base, overlay, "model", "candidate", "core_put_only", 1.0, 2.0
    )
    assert sync["target_delta"].tolist() == [0.25, 1.00]
    assert core["target_delta"].tolist() == [0.25, 0.50]


def test_formal_output_integrity_and_baseline_parity() -> None:
    integrity_path = strategy.OUTPUT / "integrity_checks.json"
    if not integrity_path.exists():
        pytest.skip("formal run has not been executed yet")
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    assert integrity["all_checks_passed"] is True
    assert integrity["mainline_parity_max_abs"] <= 1e-14
    assert integrity["core_only_put_parity_max_abs"] <= 1e-14
    assert integrity["return_identity_max_abs"] <= 1e-14
    assert integrity["cash_identity_max_abs"] <= 1e-14
