from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_absolute_momentum_protection_tool_v13 as v13


def test_frozen_spec_and_dependencies() -> None:
    assert v13.sha256(v13.SPEC) == v13.SPEC_SHA256
    assert v13.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v13.SPEC_SHA256
    assert v13.sha256(v13.V10_PATH) == v13.V10_SHA256
    assert v13.sha256(v13.V12_PATH) == v13.V12_SHA256


def test_grid_has_legacy_and_strict_three_cycle_separately() -> None:
    assert len(v13.GRID_VARIANTS) == 14
    assert len(set(v13.GRID_VARIANTS)) == 14
    assert v13.LEGACY_VARIANT in v13.GRID_VARIANTS
    assert "3cycle_hold_expiry_m85" in v13.GRID_VARIANTS
    assert "3m_hold_expiry_m85" not in v13.GRID_VARIANTS
    assert len(v13.ECONOMIC_VARIANTS) == 12


def test_candidate_mapping_is_explicit() -> None:
    legacy = v13.candidate_parts(f"real_{v13.LEGACY_VARIANT}")
    strict = v13.candidate_parts("real_3cycle_hold_expiry_m85")
    assert legacy["execution_structure"] == "3m_hold_expiry_legacy"
    assert strict["execution_structure"] == "3cycle_hold_expiry"
    assert legacy["contract_mapping"] != strict["contract_mapping"]


def test_third_cycle_month_covers_exactly_three_historical_rolls() -> None:
    frames = v13.core.v2.load_inputs()
    roll_dates = v13.v6.forced_roll_dates(frames["ic"])
    sample = pd.to_datetime([
        "2019-08-20", "2020-03-17", "2022-01-26", "2023-05-15",
        "2024-01-05", "2025-10-20",
    ])
    trade_dates = pd.DatetimeIndex(frames["ic"]["date"])
    for entry in sample:
        month = v13._third_cycle_month(entry, roll_dates)
        expiry = v13.proxy.fourth_wednesday(month, trade_dates)
        assert v13.v7._rolls_covered(entry, expiry, roll_dates) == 3


def test_primary_schedule_exactly_reuses_v10_signal() -> None:
    frames = v13.core.v2.load_inputs()
    daily, _ = v13.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    current_schedule, _, _ = v13.primary_schedule(frames["ic"], daily)
    frozen = pd.read_csv(
        v13.V10.OUTPUT / "evaluation_schedule.csv.gz",
        parse_dates=["eval_date", "execution_date"],
    )
    frozen = frozen[frozen["signal_variant"].eq(v13.SIGNAL)]
    joined = current_schedule.merge(
        frozen[["layer", "eval_date", "execution_date", "three_tier_target_fraction"]],
        on=["layer", "eval_date", "execution_date"], suffixes=("_v13", "_v10"),
        validate="one_to_one",
    )
    assert len(joined) == len(current_schedule) == len(frozen)
    assert np.allclose(joined["three_tier_target_fraction_v13"],
                       joined["three_tier_target_fraction_v10"], atol=0.0, rtol=0.0)
