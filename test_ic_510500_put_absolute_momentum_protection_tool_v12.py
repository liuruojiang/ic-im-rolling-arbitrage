from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_absolute_momentum_protection_tool_v12 as v12


def test_frozen_spec_and_dependencies() -> None:
    assert v12.sha256(v12.SPEC) == v12.SPEC_SHA256
    assert v12.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v12.SPEC_SHA256
    assert v12.sha256(v12.V10_PATH) == v12.V10_SHA256
    assert v12.sha256(v12.V11_PATH) == v12.V11_SHA256


def test_grid_separates_legacy_and_target_tools() -> None:
    assert len(v12.GRID_VARIANTS) == 14
    assert len(set(v12.GRID_VARIANTS)) == 14
    assert v12.LEGACY_VARIANT in v12.GRID_VARIANTS
    assert "3m_hold_expiry_m85" in v12.GRID_VARIANTS
    assert v12.LEGACY_VARIANT != "3m_hold_expiry_m85"
    assert len(v12.ECONOMIC_VARIANTS) == 12


def test_candidate_metadata_marks_contract_mapping() -> None:
    legacy = v12.candidate_parts(f"real_{v12.LEGACY_VARIANT}")
    economic = v12.candidate_parts("real_3m_hold_expiry_m85")
    assert legacy["contract_mapping"] == "v10_legacy_lowest_real_strike"
    assert economic["contract_mapping"] == "target_nearest_executable"
    assert np.isclose(float(economic["moneyness_target"]), 0.85)


def test_all_economic_variants_parse() -> None:
    for execution in v12.EXECUTIONS:
        for moneyness in v12.MONEYNESS:
            variant = f"{execution}_m{int(round(moneyness * 100))}"
            assert v12.split_variant(variant) == (execution, moneyness)


def test_primary_schedule_exactly_reuses_v10_signal() -> None:
    frames = v12.core.v2.load_inputs()
    daily, _ = v12.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    current_schedule, _, _ = v12.primary_schedule(frames["ic"], daily)
    frozen = pd.read_csv(
        v12.V10.OUTPUT / "evaluation_schedule.csv.gz",
        parse_dates=["eval_date", "execution_date"],
    )
    frozen = frozen[frozen["signal_variant"].eq(v12.SIGNAL)]
    joined = current_schedule.merge(
        frozen[["layer", "eval_date", "execution_date", "three_tier_target_fraction"]],
        on=["layer", "eval_date", "execution_date"],
        suffixes=("_v12", "_v10"),
        validate="one_to_one",
    )
    assert len(joined) == len(current_schedule) == len(frozen)
    assert np.allclose(
        joined["three_tier_target_fraction_v12"],
        joined["three_tier_target_fraction_v10"],
        atol=0.0,
        rtol=0.0,
    )
