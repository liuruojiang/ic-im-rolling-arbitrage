"""Build deterministic IC/IM v1.3 fixed reference-return curves.

This is a read-only research recomposition for the Poe historical-performance
surface.  It does not alter target schedules, frozen V2 artifacts, or orders.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import tempfile
import types
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_roll_momentum_stage3_grid_v1 as ic_grid
import ic_roll_momentum_stage2_put_v2 as ic_put
import ic_roll_momentum_stage1_v1 as ic_stage1
import ic_mainline_v1_3 as ic_target_module
import im_mainline_v1_3 as im_target_module
import im_roll50_momentum50_fullcycle_proxy_v1 as im_proxy


ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_mainline_v1_3_fixed_performance_v5"
STATUS = "research_only_fixed_reference_not_live_authority"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
PRIOR_OUTPUT = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v4"
SPEC = ROOT / "docs" / "ic_im_mainline_v1_3_fixed_performance_v5_spec.md"
SPEC_HASH = ROOT / "docs" / "ic_im_mainline_v1_3_fixed_performance_v5_spec.md.sha256"
REAL_IM_START = pd.Timestamp("2022-07-22")
PRELISTING_BASIS_DAILY = 0.00038985993765572324
PRELISTING_BASIS_ANNUAL_PCT = 10.321159572014937
PRELISTING_BASIS_POSTLISTING_OBSERVATIONS = 991

IC_STAGE1_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage1_v1" / "daily_nav.csv.gz"
IC_GRID_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage3_grid_v1" / "daily_nav.csv.gz"
IC_TARGET = ROOT / "outputs" / "ic_mainline_v1_3" / "target_schedule.csv.gz"
IM_COMPONENTS = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_im_grid160_put_carry_scan_v23"
    / "daily_outputs"
    / "daily_candidates.csv.gz"
)
IM_TARGET = ROOT / "outputs" / "im_mainline_v1_3" / "target_schedule.csv.gz"
IM_REAL_PUT_AUDIT = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_im_grid160_put_carry_scan_v23"
    / "real_put_price_audit.csv"
)
BENCHMARK_PRICES = {
    "IC": ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000905.csv",
    "IM": ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv",
}
POE_BOT = ROOT / "poe_ic_im_mainline_v1_3_bot.py"


def prelisting_basis_disclosure_lines() -> list[str]:
    """Disclosure that must accompany every future fixed-curve record artifact."""

    return [
        "IM上市前 model_avg_basis 使用上市后991个交易日均值回填："
        "daily=0.00038985993765572324，annual=10.321159572014937%。",
        "该回填含前视，仅为参考情景；不是样本外结果，也不是实盘依据。",
    ]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def reproducibility_sources() -> tuple[dict[str, str], dict[str, Any]]:
    """Hash every local code source and record return-affecting constants."""

    code_paths: dict[str, Path] = {}
    queue = [ic_grid, ic_put, ic_stage1, im_proxy, ic_target_module, im_target_module]
    seen: set[int] = set()
    while queue:
        module = queue.pop()
        if id(module) in seen:
            continue
        seen.add(id(module))
        module_file = getattr(module, "__file__", None)
        if module_file:
            path = Path(module_file).resolve()
            if path.is_file() and ROOT in path.parents:
                code_paths[module.__name__] = path
                for value in vars(module).values():
                    if isinstance(value, types.ModuleType):
                        queue.append(value)
    code_paths["builder"] = Path(__file__).resolve()
    code_hashes = {
        str(path.relative_to(ROOT)): sha256(path) for path in code_paths.values()
    }
    constants: dict[str, Any] = {
        "real_im_start": REAL_IM_START.date().isoformat(),
        "ic_cash_daily": float(ic_grid.CASH_DAILY),
        "ic_margin_rate": float(ic_grid.MARGIN_RATE),
        "im_one_way_cost": float(im_proxy.ONE_WAY_COST),
        "im_margin_buffer_rate": float(im_proxy.MARGIN_BUFFER_RATE),
        "im_cash_daily_return": float(im_proxy.CASH_DAILY_RETURN),
        "trading_days": 252,
        "fixed_start": "2015-04-16",
        "fixed_end": "2026-08-14",
        "fixed_rows": 2756,
        "im_prelisting_scenario": "model_avg_basis",
        "im_prelisting_basis_daily": PRELISTING_BASIS_DAILY,
        "im_prelisting_basis_annual_pct": PRELISTING_BASIS_ANNUAL_PCT,
        "im_prelisting_basis_postlisting_observations": (
            PRELISTING_BASIS_POSTLISTING_OBSERVATIONS
        ),
        "im_prelisting_basis_lookahead": True,
        "im_prelisting_basis_usage": (
            "reference_only_not_out_of_sample_not_live_authority"
        ),
        "im_real_scenario": "real_actual_basis",
        "im_variant": "current_4tier_mom3",
        "builder_version": VERSION,
        "supersedes_builder_output": PRIOR_OUTPUT.name,
        "prior_output_policy": "immutable_read_only",
    }
    return code_hashes, constants


def all_fixed_inputs() -> list[Path]:
    """Return direct and declared transitive local inputs used by the builder."""

    paths = {
        IC_STAGE1_DAILY, IC_GRID_DAILY, IC_TARGET, IM_COMPONENTS, IM_TARGET,
        IM_REAL_PUT_AUDIT, SPEC, SPEC_HASH, *BENCHMARK_PRICES.values(),
    }
    modules = [
        ic_grid, ic_put, ic_put.v1, ic_put.v1.put_engine,
        ic_put.v1.put_engine.v19, ic_put.v1.put_engine.v19.v18,
        ic_put.v1.put_engine.v19.v18.v13,
        ic_put.v1.put_engine.v19.v18.v13.v6,
        ic_stage1, im_proxy, ic_target_module, im_target_module,
    ]
    for module in modules:
        for value in vars(module).values():
            candidates: list[Any] = []
            if isinstance(value, Path):
                candidates = [value]
            elif isinstance(value, dict):
                candidates = [item for item in value.keys() if isinstance(item, Path)]
            elif isinstance(value, (tuple, list, set)):
                candidates = [item for item in value if isinstance(item, Path)]
            for candidate in candidates:
                path = candidate.resolve()
                if path.is_file() and ROOT in path.parents and path.suffix != ".py":
                    paths.add(path)
    return sorted(paths, key=lambda path: str(path).lower())


def _require_finite_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise RuntimeError(f"{label} missing required columns: {missing}")
    numeric = frame[columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise RuntimeError(f"{label} contains non-finite authoritative targets")


def classify_im_put_timing(
    frame: pd.DataFrame,
) -> dict[str, Any]:
    """Reconcile close-execution targets with executable actual Put holdings.

    The real engine can keep the old position through the target close when that
    close is not executable, then close it on the next verified official close.
    Such a row is accepted only when the next-day quantity reaches the target and
    the real-price audit contains the old-leg execution.  Every other mismatch
    fails closed.
    """

    actual = 0.5 * frame["put_qty"].astype(float)
    target = frame["core_put_execution_qty_normalized"].astype(float)
    mismatches = (actual - target).abs().gt(1e-12)
    if not mismatches.any():
        return {
            "same_day_target_max_abs": float((actual - target).abs().max()),
            "execution_lag_rows": 0,
            "execution_lag_dates": [],
            "execution_lag_details": [],
            "unexplained_mismatch_rows": 0,
        }
    audit = pd.read_csv(IM_REAL_PUT_AUDIT, parse_dates=["date"])
    audit = audit[audit["candidate"].eq("current_4tier_mom3")].copy()
    lag_details: list[dict[str, Any]] = []
    unexplained: list[str] = []
    for index in frame.index[mismatches]:
        position = int(frame.index.get_loc(index))
        day = pd.Timestamp(frame.at[index, "date"])
        if position == 0 or position + 1 >= len(frame):
            unexplained.append(day.date().isoformat())
            continue
        previous_target = float(target.iloc[position - 1])
        next_actual = float(actual.iloc[position + 1])
        next_day = pd.Timestamp(frame["date"].iloc[position + 1])
        old_leg = audit[
            audit["date"].eq(next_day) & audit["leg"].eq("old")
        ]
        verified = (
            np.isclose(float(actual.iloc[position]), previous_target, atol=1e-12, rtol=0.0)
            and np.isclose(next_actual, float(target.iloc[position]), atol=1e-12, rtol=0.0)
            and not old_leg.empty
            and pd.to_numeric(old_leg["used_price"], errors="coerce").gt(0.0).all()
            and pd.to_numeric(old_leg["abs_close_error"], errors="coerce").le(1e-12).all()
        )
        if not verified:
            unexplained.append(day.date().isoformat())
            continue
        lag_details.append(
            {
                "target_date": day.date().isoformat(),
                "actual_execution_date": next_day.date().isoformat(),
                "target_qty_normalized": float(target.iloc[position]),
                "held_through_target_close_qty_normalized": float(actual.iloc[position]),
                "verified_old_contracts": sorted(old_leg["contract"].astype(str).unique().tolist()),
                "reason": "pending_until_verified_official_close_and_liquidity",
            }
        )
    if unexplained:
        raise RuntimeError(f"Unexplained IM Put target/actual mismatches: {unexplained}")
    return {
        "same_day_target_max_abs": float((actual - target).abs().max()),
        "execution_lag_rows": len(lag_details),
        "execution_lag_dates": [item["target_date"] for item in lag_details],
        "execution_lag_details": lag_details,
        "unexplained_mismatch_rows": 0,
    }


def _validate_dates(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values("date").reset_index(drop=True)
    if result.empty or result["date"].duplicated().any():
        raise RuntimeError(f"{label} dates are empty or duplicated")
    if len(result) != 2756:
        raise RuntimeError(f"{label} expected 2756 rows, got {len(result)}")
    if result["date"].min() != pd.Timestamp("2015-04-16"):
        raise RuntimeError(f"{label} unexpected start date")
    if result["date"].max() != pd.Timestamp("2026-08-14"):
        raise RuntimeError(f"{label} unexpected end date")
    return result


def build_ic() -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:
    base = pd.read_csv(IC_STAGE1_DAILY, parse_dates=["date"], low_memory=False)
    base = _validate_dates(base, "IC stage1")
    grid = pd.read_csv(
        IC_GRID_DAILY,
        parse_dates=["date"],
        usecols=[
            "date",
            "grid_overlay_held_eod",
            "grid_net_increment",
        ],
    )
    target = _validate_dates(pd.read_csv(IC_TARGET, parse_dates=["date"]), "IC target")
    _require_finite_columns(
        target,
        ["momentum_execution_weight", "grid_held_eod", "total_ic_units", "total_put_target_delta"],
        "IC target",
    )
    frame = base.merge(
        target[["date", "momentum_execution_weight", "grid_held_eod", "total_ic_units", "total_put_target_delta"]],
        on="date",
        validate="one_to_one",
    ).merge(grid, on="date", validate="one_to_one")
    grid_state_error = float(
        (frame["grid_overlay_held_eod"] - frame["grid_held_eod"]).abs().max()
    )
    if grid_state_error > 1e-12:
        raise RuntimeError(f"IC grid state parity failed: {grid_state_error}")

    weight = frame["momentum_execution_weight"].astype(float)
    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    momentum_trade_cost = ic_stage1.ONE_WAY_COST * turnover
    momentum_roll_cost = (
        2.0 * ic_stage1.ONE_WAY_COST
        * weight
        * frame["roll_event"].astype(float)
    )
    momentum_cost = momentum_trade_cost + momentum_roll_cost
    momentum_gross = weight * frame["ic_gross_ret"].astype(float)
    momentum_net = (1.0 + momentum_gross) * (1.0 - momentum_cost) - 1.0
    momentum_cash = 1.0 - ic_grid.MARGIN_RATE * weight
    momentum_ret = momentum_net + momentum_cash * ic_grid.CASH_DAILY
    blend_cash = 0.5 * frame["bare_roll_ic_cash_weight"].astype(float) + 0.5 * momentum_cash
    blend_ret = 0.5 * frame["bare_roll_ic_ret"].astype(float) + 0.5 * momentum_ret

    engine_base = base.copy()
    engine_base["momentum_weight"] = weight.to_numpy()
    selected = ic_put.v1.build_v2_schedule(engine_base)
    put_schedule = ic_put.build_new_schedule(selected)
    overlay, trades, engine_audit = ic_put.run_new_overlay(engine_base, put_schedule)
    executable_schedule = pd.concat(
        [
            put_schedule[
                put_schedule["layer"].eq("model")
                & put_schedule["execution_date"].lt(ic_put.v1.REAL_START)
            ],
            put_schedule[
                put_schedule["layer"].eq("real")
                & put_schedule["execution_date"].ge(ic_put.v1.REAL_START)
            ],
        ],
        ignore_index=True,
    ).sort_values("execution_date")
    if len(executable_schedule) != len(frame) or executable_schedule["execution_date"].duplicated().any():
        raise RuntimeError("IC executable Put schedule is not one row per fixed-curve day")
    trades = trades.copy()
    trades["actual_execution_date"] = pd.to_datetime(trades["actual_execution_date"])
    trades = pd.concat(
        [
            trades[trades["layer"].eq("model") & trades["actual_execution_date"].lt(ic_put.v1.REAL_START)],
            trades[trades["layer"].eq("real") & trades["actual_execution_date"].ge(ic_put.v1.REAL_START)],
        ],
        ignore_index=True,
    ).sort_values("actual_execution_date").reset_index(drop=True)
    engine_audit["model_trade_events"] = int(trades["layer"].eq("model").sum())
    engine_audit["real_trade_events"] = int(trades["layer"].eq("real").sum())
    expected = target[["date", "total_put_target_delta"]].merge(
        executable_schedule[["execution_date", "target_delta"]],
        left_on="date",
        right_on="execution_date",
        how="left",
        validate="one_to_one",
    )
    put_target_error = float(
        (expected["total_put_target_delta"] - expected["target_delta"]).abs().max()
    )
    if expected["target_delta"].isna().any() or put_target_error > 1e-12:
        raise RuntimeError(f"IC Put target parity failed: {put_target_error}")

    put_pre_cash = (
        (1.0 + (blend_ret - blend_cash * ic_grid.CASH_DAILY) + overlay["put_pnl_ret"].astype(float))
        * (1.0 - overlay["put_cost_rate"].astype(float))
        - 1.0
    )
    put_cash = blend_cash - overlay["put_mark_fraction"].astype(float)
    cash = put_cash - ic_grid.MARGIN_RATE * frame["grid_held_eod"].astype(float)
    if cash.lt(-1e-12).any():
        raise RuntimeError(f"IC fixed curve has negative cash: {cash.min()}")
    ret = (
        put_pre_cash
        + frame["grid_net_increment"].astype(float)
        + cash.clip(lower=0.0) * ic_grid.CASH_DAILY
    )
    if not np.isfinite(ret).all() or ret.le(-1.0).any():
        raise RuntimeError("IC fixed curve contains invalid returns")
    result = pd.DataFrame(
        {
            "date": frame["date"],
            "ret": ret,
            "cash_weight": cash.clip(lower=0.0),
            "total_units": frame["total_ic_units"].astype(float),
            "put_target": frame["total_put_target_delta"].astype(float),
            "data_layer": overlay["layer"].astype(str),
            "momentum_weight": weight,
            "momentum_turnover": turnover,
            "momentum_cost_rate": 0.5 * momentum_cost,
        }
    )
    result["nav"] = (1.0 + result["ret"]).cumprod()
    result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
    audit = {
        "rows": len(result),
        "start": result["date"].min().date().isoformat(),
        "end": result["date"].max().date().isoformat(),
        "grid_state_parity_max_abs": grid_state_error,
        "put_target_parity_max_abs": put_target_error,
        "put_model_trade_events": engine_audit["model_trade_events"],
        "put_real_trade_events": engine_audit["real_trade_events"],
        "min_cash_weight": float(result["cash_weight"].min()),
        "real_option_start": "2022-09-19",
        "formula": "IC v1.3 NAV-defense momentum weights with rerun futures costs, Put execution, cash, and independent grid",
    }
    return result, audit, executable_schedule, trades


def _load_im_components() -> pd.DataFrame:
    raw = pd.read_csv(IM_COMPONENTS, parse_dates=["date"], low_memory=False)
    chosen = raw[raw["variant"].eq("current_4tier_mom3")].copy()
    model = chosen[
        chosen["scenario"].eq("model_avg_basis") & chosen["date"].lt(REAL_IM_START)
    ]
    real = chosen[
        chosen["scenario"].eq("real_actual_basis") & chosen["date"].ge(REAL_IM_START)
    ]
    return _validate_dates(pd.concat([model, real], ignore_index=True), "IM components")


def build_im() -> tuple[pd.DataFrame, dict[str, Any]]:
    components = _load_im_components()
    target = pd.read_csv(IM_TARGET, parse_dates=["date"])
    target = _validate_dates(target, "IM target")
    target_columns = [
        "date",
        "momentum_execution_weight",
        "grid_held_eod",
        "total_im_units",
        "core_put_execution_qty_normalized",
    ]
    _require_finite_columns(target, target_columns[1:], "IM target")
    frame = components.merge(
        target[target_columns],
        on="date",
        validate="one_to_one",
    )
    weight = frame["momentum_execution_weight"].astype(float)
    grid_held = frame["grid_held_eod"].astype(float)
    grid_state_error = float((grid_held - frame["overlay_held_eod"].astype(float)).abs().max())
    units = 0.5 + 0.5 * weight + grid_held
    units_error = float((units - frame["total_im_units_y"].astype(float)).abs().max())
    for label, value in {
        "grid state": grid_state_error,
        "total units": units_error,
    }.items():
        if value > 1e-12:
            raise RuntimeError(f"IM {label} parity failed: {value}")
    put_timing = classify_im_put_timing(frame)
    component_call_active = frame["call_contract"].fillna("").astype(str).str.strip().ne("")
    actual_call_target = component_call_active.astype(float)
    authoritative_schedule, _authoritative_audit = (
        im_target_module.load_authoritative_local_state()
    )
    authoritative_call = authoritative_schedule[
        [
            "date",
            "call_active",
            "core_call_target_contracts_normalized",
            "core_call_coverage_capacity_contracts_normalized",
        ]
    ].copy()
    call_parity = frame[["date"]].merge(
        authoritative_call, on="date", validate="one_to_one"
    )
    target_call_active = call_parity["call_active"].astype(bool)
    if not component_call_active.equals(target_call_active):
        mismatch_dates = frame.loc[
            component_call_active.ne(target_call_active), "date"
        ].dt.strftime("%Y-%m-%d").head(3).tolist()
        raise RuntimeError(f"IM actual Call state parity failed: {mismatch_dates}")
    if not np.allclose(
        call_parity["core_call_target_contracts_normalized"].astype(float),
        actual_call_target,
        atol=1e-12,
        rtol=0.0,
    ):
        raise RuntimeError("IM actual Call target quantity parity failed")
    if not call_parity["core_call_coverage_capacity_contracts_normalized"].eq(1.0).all():
        raise RuntimeError("IM Call coverage capacity is not one normalized contract")

    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    real_rows = frame.index[frame["date"].eq(REAL_IM_START)]
    if len(real_rows) != 1:
        raise RuntimeError("IM real start date is missing or duplicated")
    turnover.loc[int(real_rows[0])] = abs(float(weight.loc[int(real_rows[0])]))
    momentum_cost_full = (
        im_proxy.ONE_WAY_COST * turnover
        + 2.0
        * im_proxy.ONE_WAY_COST
        * weight
        * frame["roll_event"].astype(float)
    )

    base_gross = (
        frame["base_gross_ret"].astype(float)
        + frame["base_basis_ret"].astype(float)
    )
    overlay_gross = (
        frame["overlay_gross_ret"].astype(float)
        + frame["overlay_basis_ret"].astype(float)
    )
    futures_gross = (0.5 + 0.5 * weight) * base_gross + overlay_gross
    futures_cost = (
        0.5 * frame["base_futures_cost_rate"].astype(float)
        + 0.5 * momentum_cost_full
        + frame["overlay_cost_rate"].astype(float)
    )
    put_pnl = 0.5 * frame["put_pnl_ret"].astype(float)
    call_pnl = 0.5 * frame["call_pnl_ret"].astype(float)
    put_cost = 0.5 * frame["put_cost_rate"].astype(float)
    call_cost = 0.5 * frame["call_cost_rate"].astype(float)
    pre_cash_ret = (
        (1.0 + futures_gross + put_pnl + call_pnl)
        * (1.0 - futures_cost)
        * (1.0 - put_cost)
        * (1.0 - call_cost)
        - 1.0
    )
    cash_raw = (
        1.0
        - im_proxy.MARGIN_BUFFER_RATE * units
        - 0.5 * frame["put_mark_fraction"].astype(float)
        - 0.5 * frame["call_margin_fraction"].astype(float)
    )
    if cash_raw.lt(-1e-12).any():
        raise RuntimeError(f"IM fixed curve has negative cash: {cash_raw.min()}")
    cash = cash_raw.clip(lower=0.0)
    ret = pre_cash_ret + cash * im_proxy.CASH_DAILY_RETURN
    if not np.isfinite(ret).all() or ret.le(-1.0).any():
        raise RuntimeError("IM fixed curve contains invalid returns")
    result = pd.DataFrame(
        {
            "date": frame["date"],
            "ret": ret,
            "cash_weight": cash,
            "total_units": units,
            "put_qty_normalized": 0.5 * frame["put_qty"].astype(float),
            "call_contracts_normalized": actual_call_target,
            "data_layer": np.where(frame["date"].lt(REAL_IM_START), "model", "real"),
            "momentum_weight": weight,
            "momentum_turnover": turnover,
            "momentum_cost_rate": 0.5 * momentum_cost_full,
        }
    )
    result["nav"] = (1.0 + result["ret"]).cumprod()
    result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
    audit = {
        "rows": len(result),
        "start": result["date"].min().date().isoformat(),
        "end": result["date"].max().date().isoformat(),
        "grid_state_parity_max_abs": grid_state_error,
        "total_units_parity_max_abs": units_error,
        "put_target_vs_executable_actual_timing": put_timing,
        "call_actual_active_rows": int(component_call_active.sum()),
        "call_actual_flat_rows": int((~component_call_active).sum()),
        "call_actual_state_parity": True,
        "call_actual_state_source": "fixed_component_call_contract",
        "min_cash_weight": float(result["cash_weight"].min()),
        "real_market_start": REAL_IM_START.date().isoformat(),
        "formula": "0.5 core plus 0.5 momentum, v1.1 Put/Call scaled to core, independent 1.60/2.00 grid",
    }
    return result, audit


def metrics(
    frame: pd.DataFrame, start: pd.Timestamp, *, include_initial_return: bool = False
) -> dict[str, Any]:
    sample = frame[frame["date"].ge(start)].copy()
    if not include_initial_return:
        sample = sample.iloc[1:].copy()
    ret = sample["ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1))
    result = {
        "start": sample["date"].min().date().isoformat(),
        "end": sample["date"].max().date().isoformat(),
        "rows": len(sample),
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0),
        "ann_vol": std * np.sqrt(252.0),
        "sharpe": float(ret.mean()) / std * np.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(dd.min()),
        "final_nav": float(nav.iloc[-1]),
    }
    for column in ("momentum_weight", "momentum_turnover", "momentum_cost_rate"):
        result[f"avg_{column}"] = (
            float(sample[column].astype(float).mean()) if column in sample.columns else np.nan
        )
    return result


def build_metrics(curves: dict[str, pd.DataFrame]) -> pd.DataFrame:
    end = pd.Timestamp("2026-08-14")
    windows = {
        "full": pd.Timestamp("2015-04-16"),
        "10y": end - pd.DateOffset(years=10),
        "5y": end - pd.DateOffset(years=5),
        "3y": end - pd.DateOffset(years=3),
        "1y": end - pd.DateOffset(years=1),
    }
    rows: list[dict[str, Any]] = []
    for product, frame in curves.items():
        for window, start in windows.items():
            rows.append({
                "product": product,
                "window": window,
                **metrics(frame, start, include_initial_return=(window == "full")),
            })
        if product == "IM":
            rows.append(
                {"product": product, "window": "real_im_mo", **metrics(frame, REAL_IM_START)}
            )
    return pd.DataFrame(rows)


def _encoded_curve(frame: pd.DataFrame) -> tuple[str, str]:
    compact = frame[["date", "ret"]].copy()
    compact["date"] = compact["date"].dt.strftime("%Y-%m-%d")
    raw = compact.to_csv(
        index=False, float_format="%.17g", lineterminator="\n"
    ).encode("utf-8")
    return (
        base64.b64encode(zlib.compress(raw, level=9)).decode("ascii"),
        hashlib.sha256(raw).hexdigest(),
    )


def _encoded_benchmark(product: str, dates: pd.Series) -> tuple[str, str]:
    prices = pd.read_csv(
        BENCHMARK_PRICES[product], parse_dates=["date"], usecols=["date", "close"]
    )
    prices = dates.to_frame().merge(prices, on="date", how="left", validate="one_to_one")
    if prices["close"].isna().any():
        missing = prices.loc[prices["close"].isna(), "date"].iloc[0]
        raise RuntimeError(f"{product} benchmark is missing {missing.date()}")
    cents = np.rint(prices["close"].to_numpy(dtype=float) * 100.0).astype("<i4")
    deltas = np.empty_like(cents)
    deltas[0] = cents[0]
    deltas[1:] = cents[1:] - cents[:-1]
    raw = deltas.tobytes()
    return (
        base64.b64encode(zlib.compress(raw, level=9)).decode("ascii"),
        hashlib.sha256(raw).hexdigest(),
    )


def patch_poe_constants(curves: dict[str, pd.DataFrame]) -> None:
    """Back up Poe, then atomically replace fixed return/benchmark constants."""

    if not POE_BOT.is_file():
        raise FileNotFoundError(POE_BOT)
    blobs: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for product, frame in curves.items():
        blobs[product], hashes[product] = _encoded_curve(frame)
    source = POE_BOT.read_text(encoding="utf-8")
    start = source.index("_RETURN_BLOBS = {")
    end = source.index("\n\n# Official CSI price-index closes", start)
    replacement = (
        "_RETURN_BLOBS = "
        + json.dumps(blobs, ensure_ascii=True, indent=4)
        + "\n_RETURN_SHA256 = "
        + json.dumps(hashes, ensure_ascii=True, indent=4)
    )
    source = source[:start] + replacement + source[end:]

    benchmark_blobs: dict[str, str] = {}
    benchmark_hashes: dict[str, str] = {}
    for product, frame in curves.items():
        benchmark_blobs[product], benchmark_hashes[product] = _encoded_benchmark(
            product, frame["date"]
        )
    start = source.index("_BENCHMARK_PRICE_BLOBS = {")
    end = source.index("\n\nFROZEN = {", start)
    replacement = (
        "_BENCHMARK_PRICE_BLOBS = "
        + json.dumps(benchmark_blobs, ensure_ascii=True, indent=4)
        + "\n_BENCHMARK_PRICE_SHA256 = "
        + json.dumps(benchmark_hashes, ensure_ascii=True, indent=4)
    )
    updated = source[:start] + replacement + source[end:]
    backup_dir = ROOT / ".codex_backups" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = backup_dir / POE_BOT.name
    shutil.copy2(POE_BOT, backup_path)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{POE_BOT.name}.", suffix=".tmp", dir=POE_BOT.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, POE_BOT)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    print(f"Poe backup: {backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="disabled: first formal versioned outputs are immutable",
    )
    parser.add_argument(
        "--patch-poe",
        action="store_true",
        help="mechanically embed the rebuilt returns and aligned benchmark prices in Poe",
    )
    args = parser.parse_args()
    if args.force:
        parser.error("--force is disabled; create a new version instead of overwriting output")
    inputs = all_fixed_inputs()
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing fixed-performance inputs: {missing}")
    if OUTPUT.exists():
        raise FileExistsError(f"Immutable output exists: {OUTPUT}; create a new version")
    if STAGING.exists():
        raise FileExistsError(f"Staging path already exists: {STAGING}; inspect it manually")
    STAGING.mkdir(parents=True)
    try:
        ic, ic_audit, ic_put_schedule, ic_put_trades = build_ic()
        im, im_audit = build_im()
        if not ic["date"].equals(im["date"]):
            raise RuntimeError("IC and IM fixed curves do not share an identical date index")
        curves = {"IC": ic, "IM": im}
        for product, frame in curves.items():
            frame.to_csv(STAGING / f"{product.lower()}_daily.csv.gz", index=False, compression="gzip")
        ic_put_schedule.to_csv(STAGING / "ic_put_target_schedule.csv.gz", index=False, compression="gzip")
        ic_put_trades.to_csv(STAGING / "ic_put_trades.csv.gz", index=False, compression="gzip")
        table = build_metrics(curves)
        table.to_csv(STAGING / "metrics_by_window.csv", index=False)
        validation = {
            "version": VERSION,
            "status": STATUS,
            "date_index_parity": True,
            "IC": ic_audit,
            "IM": im_audit,
        }
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        lines = [
            "# IC/IM v1.3 定值参考曲线 v5（语义一致性修订）",
            "",
            f"状态：`{STATUS}`；不生成订单，不改变冻结V2、v1.2目标日程或既有v1.2定值输出。",
            "",
            "|品种|窗口|年化收益|最大回撤|年化波动|Sharpe|",
            "|---|---|---:|---:|---:|---:|",
        ]
        for row in table.itertuples(index=False):
            lines.append(
                f"|{row.product}|{row.window}|{row.ann_return:.2%}|{row.max_dd:.2%}|"
                f"{row.ann_vol:.2%}|{row.sharpe:.2f}|"
            )
        lines.extend(
            [
                "",
                "IC按A股中证500袖的6%基础NAV回撤减半规则生成新权重，并重跑期货成本、Put成交、现金与独立网格；IM使用0.5核心+0.5倍v1.3动量、"
                "v1.1三张负动量下限Put/原Call及1.60/2.00独立网格重组。",
                "2015年至期权上市前包含理论/代理期；2022年后的对应区间使用真实期货/期权组件。",
                *prelisting_basis_disclosure_lines(),
            ]
        )
        (STAGING / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        code_sources, return_constants = reproducibility_sources()
        constants_bytes = json.dumps(
            return_constants, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        manifest = {
            "version": VERSION,
            "status": STATUS,
            "created_at": datetime.now().astimezone().isoformat(),
            "inputs": {str(path.relative_to(ROOT)): sha256(path) for path in inputs},
            "code_sources": code_sources,
            "return_affecting_constants": return_constants,
            "return_affecting_constants_sha256": hashlib.sha256(constants_bytes).hexdigest(),
        }
        (STAGING / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        STAGING.rename(OUTPUT)
        if args.patch_poe:
            patch_poe_constants(curves)
        print(table.to_string(index=False))
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        print(f"Output: {OUTPUT}")
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


if __name__ == "__main__":
    main()
