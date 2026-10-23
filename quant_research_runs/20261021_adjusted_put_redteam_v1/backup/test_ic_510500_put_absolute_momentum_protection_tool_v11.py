from __future__ import annotations

import numpy as np
import pandas as pd

import ic_510500_put_absolute_momentum_protection_tool_v11 as v11


def test_frozen_spec_and_v10_dependency() -> None:
    assert v11.sha256(v11.SPEC) == v11.SPEC_SHA256
    assert v11.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v11.SPEC_SHA256
    assert v11.sha256(v11.V10_PATH) == v11.V10_SHA256


def test_grid_is_complete() -> None:
    assert len(v11.GRID_VARIANTS) == 13
    assert len(set(v11.GRID_VARIANTS)) == 13
    assert v11.BASELINE_VARIANT in v11.GRID_VARIANTS
    for execution in v11.EXECUTIONS:
        for moneyness in v11.MONEYNESS:
            variant = f"{execution}_m{int(round(moneyness * 100))}"
            assert v11.split_variant(variant) == (execution, moneyness)


def test_model_open_functions_use_exact_target() -> None:
    frames = v11.core.v2.load_inputs()
    daily, _ = v11.core.v2.build_daily_valuation_full(frames["states_full"], frames["states_legacy"])
    market, _ = v11.proxy.prepare_model_market(
        frames["ic"], daily, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    row = frames["ic"].merge(market.drop(columns=["settle"]), on="date").iloc[500]
    row_obj = next(pd.DataFrame([row]).itertuples(index=False))
    dates = pd.DatetimeIndex(frames["ic"]["date"])
    for target in v11.MONEYNESS:
        monthly, _, _ = v11._model_monthly_open(target)(row_obj, "2m_monthly", 1.0, dates)
        hold, _, _ = v11._model_hold_open(target)(row_obj, 1.0, dates)
        assert math_isclose(monthly.strike / float(row_obj.spot_open), target)
        assert math_isclose(hold.strike / float(row_obj.spot_open), target)


def math_isclose(left: float, right: float) -> bool:
    return bool(np.isclose(left, right, atol=1e-12, rtol=0.0))


def test_real_selector_chooses_nearest_then_lower_strike() -> None:
    day = pd.Timestamp("2026-01-05")
    month = pd.Timestamp("2026-03-01")
    snapshots = pd.DataFrame(
        {
            "date": [day, day, day],
            "contract_month": [month, month, month],
            "strike": [8.9, 9.1, 9.5],
            "security_id": ["A", "B", "C"],
            "contract_id": ["CA", "CB", "CC"],
        }
    )
    history = pd.DataFrame(
        {
            "security_id": ["A", "B", "C"],
            "date": [day, day, day],
            "open": [0.1, 0.1, 0.1],
            "close": [0.1, 0.1, 0.1],
            "volume": [10, 10, 10],
        }
    ).set_index(["security_id", "date"])
    selected = v11.select_real_contract_target(snapshots, history, day, month, 10.0, 0.90)
    assert selected is not None
    assert selected[0]["security_id"] == "A"


def test_primary_schedule_exactly_reuses_v10_signal() -> None:
    frames = v11.core.v2.load_inputs()
    daily, _ = v11.core.v2.build_daily_valuation_full(frames["states_full"], frames["states_legacy"])
    left, _, current = v11.primary_schedule(frames["ic"], daily)
    right = pd.read_csv(v11.v10.OUTPUT / "evaluation_schedule.csv.gz", parse_dates=["eval_date", "execution_date"])
    right = right[right["signal_variant"].eq(v11.SIGNAL)]
    joined = left.merge(
        right[["layer", "eval_date", "execution_date", "three_tier_target_fraction"]],
        on=["layer", "eval_date", "execution_date"], suffixes=("_v11", "_v10"), validate="one_to_one",
    )
    assert len(joined) == len(left) == len(right)
    assert np.allclose(
        joined["three_tier_target_fraction_v11"], joined["three_tier_target_fraction_v10"],
        atol=0.0, rtol=0.0,
    )
    assert float(current.iloc[0]["target_fraction"]) == 1.0
