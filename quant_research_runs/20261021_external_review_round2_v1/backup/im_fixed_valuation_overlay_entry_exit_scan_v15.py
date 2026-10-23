#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas"]
# ///
"""Scan a one-unit IM valuation overlay on the frozen floor-3 Put core."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_entry_exit_scan_v15"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "2dcef9e72b50e486b72caf542114b8a65d4f68ae3667a079b560c2d46b47782e"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_fixed_valuation_overlay_entry_exit_scan_v15"

V14_OUTPUT = ROOT / "outputs" / "im_mo_reconstructed_floor_selection_v14"
V14_DAILY = V14_OUTPUT / "daily_candidates.csv.gz"
V3_SCORE = ROOT / "outputs" / "im_fixed_valuation_tier_relationship_v3" / "daily_tier_states.csv.gz"
V7_PERCENTILE = ROOT / "outputs" / "im_valuation_window_ladder_scan_v7" / "daily_window_percentiles.csv.gz"
IM_DAILY = ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1" / "daily_nav.csv"
IM_QUOTES = ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_im_contracts.csv"
MODEL_OHLC = ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "sina_sh000852_index.csv"
PRICE_INDEX = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv"
TOTAL_RETURN_INDEX = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_H00852.csv"

MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-08-14")
TRADING_DAYS = 252
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0

ENTRY_THRESHOLDS = tuple(round(1.40 + 0.05 * index, 2) for index in range(15))
EXIT_THRESHOLDS = tuple(round(2.10 + 0.05 * index, 2) for index in range(11))
RELATIVE_DIAGNOSTICS = ((0.25, 0.75), (0.30, 0.70))
WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}

INPUT_HASHES = {
    ROOT / "im_mo_reconstructed_floor_selection_v14.py": "55c27b4b4bcdbf814f2f7edb3636d9f2ffa2149b20141e9eeefa773747d796d6",
    ROOT / "docs" / "im_mo_reconstructed_floor_selection_v14_spec.md": "7a0bcbc15019a75b1527c06c15be9cd0a57f6b660ec5ce1114b09de5356c9bc0",
    ROOT / "docs" / "im_mo_reconstructed_floor_selection_v14_postrun_audit.md": "f67041096d088bba0f27e74c265c62b8d3cab701e9be23f5d76d7e9a68da88e1",
    V14_DAILY: "c013e2ffdbe5435ae87601af319a3e263850e7d55f31e25fa3eee8a7ebb56614",
    V14_OUTPUT / "data_manifest.json": "d6caa2000d4706a3da1b3ad0c6f6207b56df428c0808b051012fb7d36c1c9212",
    V14_OUTPUT / "decision_summary.json": "c5a13a8ce868ffd49c2148f0682decdf8a2f2a157febabe4c076a4e60bb0e878",
    ROOT / "docs" / "im_mo_put_research_mainline_v1.md": "0caafc8a48518babd68108e067d3b61e4cda4694b7ac2b3c90dfda8718330738",
    ROOT / "im_valuation_window_ladder_scan_v7.py": "29d54597690115710020cdcc1bd0d84d57e1bdbb3f281d88f5b90912b6015d1a",
    ROOT / "docs" / "im_valuation_window_ladder_scan_v7_spec.md": "2a92ef1f1708d6930e8d56d9d0ed84f5de3c2bf5c57288d8e44ff6b4e21cde6f",
    V7_PERCENTILE: "844839dbc1cc704aa4e88ead12f617044a9e3e7c05c0338692488f5982d8cdd9",
    ROOT / "outputs" / "im_valuation_window_ladder_scan_v7" / "output_manifest.json": "c043559b301605a002139312bf47e5e82d9bb2ec9f8b88e73aee2f0a47a6c1c9",
    ROOT / "im_fixed_valuation_tier_relationship_v3.py": "4e5c36ab2dcc5ec9d8e6d3ba3c8dd4ee9e2bf705c54c620390326efab967fe4d",
    ROOT / "docs" / "im_fixed_valuation_tier_relationship_v3_spec.md": "dbc096f7dfbbfec2724f6889e0000564b283c8b52dc00e73da18e430ba3759c5",
    V3_SCORE: "dd91b80172553a1dbe53e79bdc5870ca32af7e7ed5171c001e356ad28c9e3912",
    ROOT / "outputs" / "im_fixed_valuation_tier_relationship_v3" / "output_manifest.json": "d428a21b4d8e40ab4c9a4146f5607aaaac64ae382df2d94fb6e3594878c75dbf",
    IM_DAILY: "0a3719ade254a32eaf1886dc7d00e9d84aa93498e9a2fecf2868cbefefb60b99",
    IM_QUOTES: "6f19f04824026e3cf7e4fc7ebfeb20f60637e53bfc3caebc616fae47794f3cc0",
    MODEL_OHLC: "9d3995a7189137fee79e5aaa2a58aced57101a1329f1236aca8a0adc86babe74",
    PRICE_INDEX: "e42b94ad52a39687a5a0d92fe7f3c28481f34420bac6ac0d0c62ffcdf0e68bf9",
    TOTAL_RETURN_INDEX: "6483caa2cba5c2bf7e300c949380ddc8ffeaf7877152679e3754a99d841ae40a",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def fixed_grid() -> list[tuple[float, float]]:
    return [
        (low, high)
        for low in ENTRY_THRESHOLDS
        for high in EXIT_THRESHOLDS
        if high - low >= 0.30 - 1e-12
    ]


def fixed_label(low: float, high: float) -> str:
    return f"fixed_L{low:.2f}_H{high:.2f}"


def relative_label(low: float, high: float) -> str:
    return f"relative_P{int(round(low * 100)):02d}_P{int(round(high * 100)):02d}_diag"


def verify_inputs(*, require_fresh_output: bool) -> dict[str, Any]:
    if sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v15 specification mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_HASH:
        raise RuntimeError("Frozen v15 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            raise RuntimeError(f"Frozen v15 input changed: {path.relative_to(ROOT)}: {actual}")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v15 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v15 parameter-scan directory is missing")
    if len(fixed_grid()) != 144:
        raise RuntimeError(f"Frozen fixed grid count changed: {len(fixed_grid())}")
    decision = json.loads((V14_OUTPUT / "decision_summary.json").read_text(encoding="utf-8"))
    if decision.get("defensive_stress_candidate") != "reconstructed_valmom_floor3":
        raise RuntimeError("Frozen floor-3 core is not present in v14 decision evidence")
    return {
        "spec_sha256": SPEC_HASH,
        "frozen_input_count": len(INPUT_HASHES),
        "fixed_candidate_count": len(fixed_grid()),
        "relative_diagnostic_count": len(RELATIVE_DIAGNOSTICS),
    }


def load_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frozen = pd.read_csv(V14_DAILY, parse_dates=["date"])
    base = frozen[frozen["candidate"].eq("reconstructed_valmom_floor3")].copy()
    if set(base["layer"]) != {"model", "real"}:
        raise RuntimeError("Frozen floor-3 core lacks model/real layers")
    score = pd.read_csv(V3_SCORE, parse_dates=["date"])[
        [
            "date",
            "unbounded_median_knot",
            "pb_aggregate",
            "erp",
            "trailing_dividend_contribution",
        ]
    ].sort_values("date")
    percentile = pd.read_csv(V7_PERCENTILE, parse_dates=["date"])
    percentile = percentile[
        percentile["window_months"].eq(57) & percentile["calibrated"].astype(bool)
    ][["date", "rolling_percentile"]].sort_values("date")
    if score["date"].duplicated().any() or percentile["date"].duplicated().any():
        raise RuntimeError("Duplicate valuation state dates")
    return base, score, percentile


def build_model_market(
    base: pd.DataFrame, score: pd.DataFrame, percentile: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    core = base[base["layer"].eq("model")][["date", "gross_ret", "cost_rate"]].copy()
    official = pd.read_csv(PRICE_INDEX, parse_dates=["date"])[["date", "close"]].rename(
        columns={"close": "spot_close"}
    )
    tri = pd.read_csv(TOTAL_RETURN_INDEX, parse_dates=["date"])[["date", "close"]].rename(
        columns={"close": "tri_close"}
    )
    ohlc = pd.read_csv(MODEL_OHLC, parse_dates=["date"])[["date", "open", "close"]].rename(
        columns={"open": "spot_open", "close": "sina_close"}
    )
    market = core.merge(official, on="date", validate="one_to_one").merge(
        tri, on="date", validate="one_to_one"
    ).merge(ohlc, on="date", validate="one_to_one")
    market = market.sort_values("date").reset_index(drop=True)
    market["prior_spot_close"] = market["spot_close"].shift(1)
    market["prior_tri_close"] = market["tri_close"].shift(1)
    market.loc[0, "prior_spot_close"] = market.loc[0, "spot_close"]
    market.loc[0, "prior_tri_close"] = market.loc[0, "tri_close"]
    market["open_unit"] = (
        market["prior_tri_close"] * market["spot_open"] / market["prior_spot_close"]
    )
    market["settle_unit"] = market["tri_close"]
    market["pre_settle_unit"] = market["prior_tri_close"]
    market["contract"] = "CSI1000_TRI_PROXY"
    market["execution_volume"] = np.nan
    market["roll_event"] = market["cost_rate"].ge(0.0002 - 1e-12)
    market = market.merge(score, on="date", how="left", validate="one_to_one").merge(
        percentile, on="date", how="left", validate="one_to_one"
    )
    close_error = (market["sina_close"] / market["spot_close"] - 1.0).abs()
    gross_expected = market["tri_close"] / market["prior_tri_close"] - 1.0
    gross_error = float((market.loc[market.index[1:], "gross_ret"] - gross_expected.iloc[1:]).abs().max())
    if (
        market["date"].min() != MODEL_START
        or market["date"].max() != END
        or market[["open_unit", "settle_unit", "pre_settle_unit"]].isna().any().any()
        or (market[["open_unit", "settle_unit", "pre_settle_unit"]] <= 0).any().any()
        or close_error.max() > 0.005
        or gross_error > 1e-12
    ):
        raise RuntimeError("Invalid model execution market")
    return market, {
        "rows": len(market),
        "start": str(market["date"].min().date()),
        "end": str(market["date"].max().date()),
        "close_median_relative_error": float(close_error.median()),
        "close_max_relative_error": float(close_error.max()),
        "gross_return_parity_max_abs": gross_error,
        "roll_events": int(market["roll_event"].sum()),
    }


def build_real_market(
    base: pd.DataFrame, score: pd.DataFrame, percentile: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    core = base[base["layer"].eq("real")][["date", "gross_ret", "cost_rate"]].copy()
    upstream = pd.read_csv(IM_DAILY, parse_dates=["date"])[
        ["date", "contract", "settle", "im_gross_ret", "cost_rate", "roll_to"]
    ].rename(columns={"cost_rate": "upstream_cost_rate"})
    quotes = pd.read_csv(IM_QUOTES, parse_dates=["date"])[
        ["date", "contract", "open", "settle", "pre_settle", "volume"]
    ].rename(columns={"settle": "raw_settle", "volume": "execution_volume"})
    market = core.merge(upstream, on="date", validate="one_to_one").merge(
        quotes, on=["date", "contract"], validate="one_to_one"
    )
    market = market.sort_values("date").reset_index(drop=True)
    market["open_unit"] = market["open"]
    market["settle_unit"] = market["settle"]
    market["pre_settle_unit"] = market["pre_settle"]
    market["roll_event"] = market["roll_to"].fillna("").astype(str).ne("")
    market = market.merge(score, on="date", how="left", validate="one_to_one").merge(
        percentile, on="date", how="left", validate="one_to_one"
    )
    gross_expected = market["settle"] / market["pre_settle"] - 1.0
    gross_error = float((market.loc[market.index[1:], "gross_ret"] - gross_expected.iloc[1:]).abs().max())
    errors = {
        "settle": float((market["settle"] - market["raw_settle"]).abs().max()),
        "base_vs_upstream_gross": float((market["gross_ret"] - market["im_gross_ret"]).abs().max()),
        "base_vs_raw_gross": gross_error,
        "cost": float((market["cost_rate"] - market["upstream_cost_rate"]).abs().max()),
    }
    if (
        market["date"].min() != REAL_START
        or market["date"].max() != END
        or market[["open_unit", "settle_unit", "pre_settle_unit", "execution_volume"]].isna().any().any()
        or (market[["open_unit", "settle_unit", "pre_settle_unit", "execution_volume"]] <= 0).any().any()
        or max(errors.values()) > 1e-12
    ):
        raise RuntimeError(f"Invalid real IM execution market: {errors}")
    return market, {
        "rows": len(market),
        "start": str(market["date"].min().date()),
        "end": str(market["date"].max().date()),
        "roll_events": int(market["roll_event"].sum()),
        **{f"{key}_parity_max_abs": value for key, value in errors.items()},
    }


def state_before_start(
    history: pd.DataFrame, signal_column: str, low: float, high: float, start: pd.Timestamp
) -> tuple[bool, pd.Timestamp | pd.NaT, float]:
    state = False
    last_date: pd.Timestamp | pd.NaT = pd.NaT
    last_value = np.nan
    for row in history[history["date"].lt(start)].sort_values("date").itertuples(index=False):
        value = getattr(row, signal_column)
        if pd.isna(value):
            continue
        numeric = float(value)
        if not state and numeric <= low + 1e-12:
            state, last_date, last_value = True, pd.Timestamp(row.date), numeric
        elif state and numeric >= high - 1e-12:
            state, last_date, last_value = False, pd.Timestamp(row.date), numeric
    return state, last_date, last_value


def simulate_overlay(
    market: pd.DataFrame,
    history: pd.DataFrame,
    signal_column: str,
    low: float,
    high: float,
    candidate: str,
    family: str,
    layer: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    start = pd.Timestamp(market["date"].min())
    carry, carry_date, carry_value = state_before_start(history, signal_column, low, high, start)
    state = False
    pending: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    dates = list(pd.DatetimeIndex(market["date"]))
    for index, row in enumerate(market.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        held_before = state
        buy = False
        sell = False
        execution_reason = ""
        signal_date: pd.Timestamp | pd.NaT = pd.NaT
        signal_value = np.nan
        if index == 0 and carry:
            state = True
            buy = True
            execution_reason = "initial_listing_carry" if layer == "real" else "history_carry"
            signal_date, signal_value = carry_date, carry_value
        elif pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            signal_date = pd.Timestamp(pending["signal_date"])
            signal_value = float(pending["signal_value"])
            execution_reason = "t_plus_1_open"
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError("Duplicate overlay buy")
                state, buy = True, True
            else:
                if not state:
                    raise RuntimeError("Overlay sell while flat")
                state, sell = False, True
            pending = None

        held_eod = state
        if held_before and held_eod:
            gross = float(row.settle_unit) / float(row.pre_settle_unit) - 1.0
        elif not held_before and held_eod:
            gross = float(row.settle_unit) / float(row.open_unit) - 1.0
        elif held_before and not held_eod:
            gross = float(row.open_unit) / float(row.pre_settle_unit) - 1.0
        else:
            gross = 0.0
        trade_cost = ONE_WAY_COST * (int(buy) + int(sell))
        roll_cost = 2.0 * ONE_WAY_COST if held_eod and bool(row.roll_event) else 0.0
        if buy or sell:
            trades.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "family": family,
                    "low_threshold": low,
                    "high_threshold": high,
                    "action": "buy" if buy else "sell",
                    "signal_date": signal_date,
                    "signal_value": signal_value,
                    "execution_date": day,
                    "execution_reason": execution_reason,
                    "execution_contract": row.contract,
                    "execution_open": float(row.open_unit),
                    "execution_volume": row.execution_volume,
                }
            )
        value = getattr(row, signal_column)
        rows.append(
            {
                "date": day,
                "candidate": candidate,
                "family": family,
                "low_threshold": low,
                "high_threshold": high,
                "signal_value": value,
                "overlay_held_before": int(held_before),
                "overlay_held_eod": int(held_eod),
                "overlay_buy": int(buy),
                "overlay_sell": int(sell),
                "overlay_gross_ret": gross,
                "overlay_trade_cost_rate": trade_cost,
                "overlay_roll_cost_rate": roll_cost,
                "overlay_cost_rate": trade_cost + roll_cost,
                "total_im_units": 1.0 + float(held_eod),
                "roll_event": bool(row.roll_event),
                "signal_date_executed": signal_date,
                "signal_value_executed": signal_value,
            }
        )

        if pending is None and not pd.isna(value):
            numeric = float(value)
            action = "buy" if (not state and numeric <= low + 1e-12) else None
            if state and numeric >= high - 1e-12:
                action = "sell"
            if action is not None:
                pending = {
                    "action": action,
                    "signal_date": day,
                    "signal_value": numeric,
                    "execution_date": dates[index + 1] if index + 1 < len(dates) else pd.NaT,
                }

    daily = pd.DataFrame(rows)
    trade_frame = pd.DataFrame(trades)
    if trade_frame.empty:
        trade_frame = pd.DataFrame(
            columns=[
                "layer", "candidate", "family", "low_threshold", "high_threshold", "action",
                "signal_date", "signal_value", "execution_date", "execution_reason",
                "execution_contract", "execution_open", "execution_volume",
            ]
        )
    entries = int(trade_frame["action"].eq("buy").sum())
    exits = int(trade_frame["action"].eq("sell").sum())
    entry_years = int(trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"].dt.year.nunique())
    cycle = {
        "layer": layer,
        "candidate": candidate,
        "family": family,
        "low_threshold": low,
        "high_threshold": high,
        "entries": entries,
        "exits": exits,
        "completed_cycles": min(entries, exits),
        "entry_years": entry_years,
        "holding_days": int(daily["overlay_held_eod"].sum()),
        "holding_ratio": float(daily["overlay_held_eod"].mean()),
        "ending_state": int(state),
        "initial_carry": int(carry),
        "pending_order_end": int(pending is not None),
    }
    return daily, trade_frame, cycle


def flat_overlay(market: pd.DataFrame, layer: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = market[["date"]].copy()
    frame["candidate"] = "base_core_put"
    frame["family"] = "core"
    frame["low_threshold"] = np.nan
    frame["high_threshold"] = np.nan
    frame["signal_value"] = np.nan
    for column in ("overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell"):
        frame[column] = 0
    for column in (
        "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate", "overlay_cost_rate"
    ):
        frame[column] = 0.0
    frame["total_im_units"] = 1.0
    frame["roll_event"] = market["roll_event"].astype(bool).to_numpy()
    frame["signal_date_executed"] = pd.NaT
    frame["signal_value_executed"] = np.nan
    cycle = {
        "layer": layer,
        "candidate": "base_core_put",
        "family": "core",
        "low_threshold": np.nan,
        "high_threshold": np.nan,
        "entries": 0,
        "exits": 0,
        "completed_cycles": 0,
        "entry_years": 0,
        "holding_days": 0,
        "holding_ratio": 0.0,
        "ending_state": 0,
        "initial_carry": 0,
        "pending_order_end": 0,
    }
    return frame, cycle


def assemble_candidate(
    base: pd.DataFrame, overlay: pd.DataFrame, candidate: str, family: str
) -> pd.DataFrame:
    layer = str(base["layer"].iloc[0])
    core = base.drop(columns=["candidate", "ret", "cash_ret", "nav", "cash_nav", "cash_drawdown"], errors="ignore")
    result = core.merge(overlay, on="date", validate="one_to_one")
    result["candidate"] = candidate
    result["family"] = family
    result["layer"] = layer
    result["combined_gross_before_cost"] = (
        result["gross_ret"] + result["overlay_gross_ret"] + result["put_pnl_ret"]
    )
    result["futures_cost_rate"] = result["cost_rate"] + result["overlay_cost_rate"]
    result["ret"] = (
        (1.0 + result["combined_gross_before_cost"])
        * (1.0 - result["futures_cost_rate"])
        * (1.0 - result["put_cost_rate"])
        - 1.0
    )
    result["cash_weight_before_put"] = (
        1.0 - MARGIN_RATE * result["total_im_units"]
    ).clip(lower=0.0)
    result["cash_weight"] = (
        result["cash_weight_before_put"] - result["put_mark_fraction"]
    ).clip(lower=0.0)
    result["cash_ret"] = result["ret"] + result["cash_weight"] * CASH_DAILY
    result["cash_nav"] = (1.0 + result["cash_ret"]).cumprod()
    result["cash_drawdown"] = result["cash_nav"] / result["cash_nav"].cummax() - 1.0
    return result


def metrics(returns: pd.Series) -> dict[str, float]:
    values = returns.astype(float).dropna().reset_index(drop=True)
    nav = pd.concat([pd.Series([1.0]), (1.0 + values).cumprod()], ignore_index=True)
    vol = float(values.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(values) > 1 else np.nan
    ann = float(nav.iloc[-1] ** (TRADING_DAYS / len(values)) - 1.0)
    max_dd = float((nav / nav.cummax() - 1.0).min())
    return {
        "total_return": float(nav.iloc[-1] - 1.0),
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe_repo": float(values.mean() / values.std(ddof=1) * math.sqrt(TRADING_DAYS))
        if len(values) > 1 and values.std(ddof=1) > 0
        else np.nan,
        "max_dd": max_dd,
        "calmar": ann / abs(max_dd) if max_dd < 0 else np.nan,
    }


def metrics_by_window(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"], sort=True):
        group = group.sort_values("date")
        start, end = pd.Timestamp(group["date"].min()), pd.Timestamp(group["date"].max())
        meta = group.iloc[0]
        for window, offset in WINDOWS.items():
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"].ge(requested)] if available else group.iloc[0:0]
            row: dict[str, Any] = {
                "layer": layer,
                "candidate": candidate,
                "family": meta["family"],
                "low_threshold": meta["low_threshold"],
                "high_threshold": meta["high_threshold"],
                "window": window,
                "available": bool(available),
                "requested_start": requested,
                "actual_start": sample["date"].min() if available else pd.NaT,
                "end": end,
                "rows": len(sample),
            }
            row.update(metrics(sample["cash_ret"]) if available else {
                "total_return": np.nan,
                "ann_return": np.nan,
                "ann_vol": np.nan,
                "sharpe_repo": np.nan,
                "max_dd": np.nan,
                "calmar": np.nan,
            })
            rows.append(row)
    result = pd.DataFrame(rows)
    base = result[result["candidate"].eq("base_core_put")].set_index(["layer", "window"])
    result["base_ann_return"] = [base.loc[(r.layer, r.window), "ann_return"] for r in result.itertuples()]
    result["base_max_dd"] = [base.loc[(r.layer, r.window), "max_dd"] for r in result.itertuples()]
    result["ann_return_delta_vs_core"] = result["ann_return"] - result["base_ann_return"]
    result["max_dd_improvement_vs_core"] = result["max_dd"] - result["base_max_dd"]
    return result


def wide_metrics(table: pd.DataFrame) -> pd.DataFrame:
    values = ["ann_return", "ann_vol", "sharpe_repo", "max_dd", "calmar"]
    return table.pivot_table(
        index=["candidate", "family", "low_threshold", "high_threshold"],
        columns=["layer", "window"], values=values, aggfunc="first", dropna=False
    ).reset_index()


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate, year), group in daily.groupby(
        ["layer", "candidate", daily["date"].dt.year], sort=True
    ):
        rows.append({"layer": layer, "candidate": candidate, "year": int(year), **metrics(group["cash_ret"])})
    return pd.DataFrame(rows)


def drawdown_details(group: pd.DataFrame) -> dict[str, Any]:
    ordered = group.sort_values("date")
    nav = (1.0 + ordered["cash_ret"]).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    trough_index = drawdown.idxmin()
    trough_date = pd.Timestamp(ordered.loc[trough_index, "date"])
    prior = nav.loc[:trough_index]
    peak_index = prior.idxmax()
    return {
        "peak_date": pd.Timestamp(ordered.loc[peak_index, "date"]),
        "trough_date": trough_date,
        "max_dd": float(drawdown.loc[trough_index]),
    }


def drawdown_audit(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"], sort=True):
        own = drawdown_details(group)
        base = daily[(daily["layer"].eq(layer)) & daily["candidate"].eq("base_core_put")]
        base_dd = drawdown_details(base)
        peak = group[group["date"].eq(base_dd["peak_date"])].iloc[0]
        bear = group[group["date"].between(base_dd["peak_date"], base_dd["trough_date"])]
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "base_peak_date": base_dd["peak_date"],
                "base_trough_date": base_dd["trough_date"],
                "base_max_dd": base_dd["max_dd"],
                "overlay_held_at_base_peak": int(peak["overlay_held_eod"]),
                "overlay_holding_days_during_base_drawdown": int(bear["overlay_held_eod"].sum()),
                "candidate_peak_date": own["peak_date"],
                "candidate_trough_date": own["trough_date"],
                "candidate_max_dd": own["max_dd"],
            }
        )
    return pd.DataFrame(rows)


def capital_audit(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"], sort=True):
        above = group["put_mark_fraction"] > group["cash_weight_before_put"] + 1e-12
        entry_breach = above & group["overlay_buy"].eq(1)
        put_trade_breach = above & group["put_cost_rate"].gt(0) & group["overlay_held_eod"].eq(1)
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "daily_put_mark_above_pre_put_cash_rows": int(above.sum()),
                "overlay_entry_cash_breach_rows": int(entry_breach.sum()),
                "put_trade_cash_breach_rows": int(put_trade_breach.sum()),
                "capital_execution_breach_rows": int((entry_breach | put_trade_breach).sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "min_cash_weight_before_put": float(group["cash_weight_before_put"].min()),
            }
        )
    return pd.DataFrame(rows)


def decide(
    metric_table: pd.DataFrame, cycles: pd.DataFrame, capital: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    lookup = metric_table.set_index(["layer", "candidate", "window"])
    base_model = metric_table[(metric_table["layer"].eq("model")) & metric_table["candidate"].eq("base_core_put")].set_index("window")
    base_real = metric_table[(metric_table["layer"].eq("real")) & metric_table["candidate"].eq("base_core_put")].set_index("window")
    cycle_lookup = cycles.set_index(["layer", "candidate"])
    capital_lookup = capital.set_index(["layer", "candidate"])
    rows: list[dict[str, Any]] = []
    for low, high in fixed_grid():
        candidate = fixed_label(low, high)
        model = metric_table[(metric_table["layer"].eq("model")) & metric_table["candidate"].eq(candidate)].set_index("window")
        real = metric_table[(metric_table["layer"].eq("real")) & metric_table["candidate"].eq(candidate)].set_index("window")
        model_cycle = cycle_lookup.loc[("model", candidate)]
        real_cycle = cycle_lookup.loc[("real", candidate)]
        model_capital = capital_lookup.loc[("model", candidate)]
        real_capital = capital_lookup.loc[("real", candidate)]
        capital_gate = bool(
            model_capital["capital_execution_breach_rows"] == 0
            and real_capital["capital_execution_breach_rows"] == 0
        )
        event_gate = bool(
            model_cycle["completed_cycles"] >= 2
            and model_cycle["entry_years"] >= 2
            and model_cycle["holding_ratio"] <= 0.70 + 1e-12
            and real_cycle["completed_cycles"] >= 1
            and model_cycle["pending_order_end"] == 0
            and real_cycle["pending_order_end"] == 0
        )
        model_return_gate = bool(
            model.loc["full", "ann_return"] >= base_model.loc["full", "ann_return"] + 0.015 - 1e-12
            and model.loc["last_10y", "ann_return"] >= base_model.loc["last_10y", "ann_return"] + 0.010 - 1e-12
            and model.loc["last_5y", "ann_return"] >= base_model.loc["last_5y", "ann_return"] + 0.005 - 1e-12
            and model.loc["last_3y", "ann_return"] >= base_model.loc["last_3y", "ann_return"] - 0.010 - 1e-12
            and model.loc["last_1y", "ann_return"] >= base_model.loc["last_1y", "ann_return"] - 0.010 - 1e-12
        )
        model_risk_gate = bool(
            all(
                model.loc[window, "max_dd"] >= base_model.loc[window, "max_dd"] - 0.030 - 1e-12
                for window in ("full", "last_10y", "last_5y")
            )
            and model.loc["full", "max_dd"] >= -0.40 - 1e-12
            and model.loc["full", "calmar"] > base_model.loc["full", "calmar"] + 1e-12
        )
        real_gate = bool(
            real.loc["full", "ann_return"] >= base_real.loc["full", "ann_return"] - 0.010 - 1e-12
            and real.loc["last_3y", "ann_return"] >= base_real.loc["last_3y", "ann_return"] - 0.010 - 1e-12
            and real.loc["full", "max_dd"] >= base_real.loc["full", "max_dd"] - 0.030 - 1e-12
            and real.loc["last_3y", "max_dd"] >= base_real.loc["last_3y", "max_dd"] - 0.030 - 1e-12
        )
        hard = capital_gate and event_gate and model_return_gate and model_risk_gate and real_gate
        rows.append(
            {
                "candidate": candidate,
                "low_threshold": low,
                "high_threshold": high,
                "model_completed_cycles": int(model_cycle["completed_cycles"]),
                "real_completed_cycles": int(real_cycle["completed_cycles"]),
                "model_entry_years": int(model_cycle["entry_years"]),
                "model_holding_ratio": float(model_cycle["holding_ratio"]),
                "model_full_ann_return": float(model.loc["full", "ann_return"]),
                "model_full_max_dd": float(model.loc["full", "max_dd"]),
                "model_full_calmar": float(model.loc["full", "calmar"]),
                "real_full_ann_return": float(real.loc["full", "ann_return"]),
                "real_full_max_dd": float(real.loc["full", "max_dd"]),
                "capital_execution_breach_rows": int(
                    model_capital["capital_execution_breach_rows"]
                    + real_capital["capital_execution_breach_rows"]
                ),
                "capital_gate": capital_gate,
                "event_gate": event_gate,
                "model_return_gate": model_return_gate,
                "model_risk_gate": model_risk_gate,
                "real_cross_gate": real_gate,
                "hard_gate_pass": hard,
            }
        )
    decisions = pd.DataFrame(rows)
    decision_lookup = decisions.set_index(["low_threshold", "high_threshold"])
    grid_set = set(fixed_grid())
    ridge_rows: list[dict[str, Any]] = []
    width_flags: dict[str, bool] = {}
    for row in decisions.itertuples(index=False):
        center = float(row.model_full_calmar)
        neighbors = {
            "low_down": (round(row.low_threshold - 0.05, 2), row.high_threshold),
            "low_up": (round(row.low_threshold + 0.05, 2), row.high_threshold),
            "high_down": (row.low_threshold, round(row.high_threshold - 0.05, 2)),
            "high_up": (row.low_threshold, round(row.high_threshold + 0.05, 2)),
        }
        neighbor_passes: list[bool] = []
        for direction, key in neighbors.items():
            exists = key in grid_set
            if exists:
                neighbor = decision_lookup.loc[key]
                retention = float(neighbor["model_full_calmar"] / center) if center != 0 else np.nan
                passed = bool(neighbor["hard_gate_pass"] and retention >= 0.80 - 1e-12)
            else:
                retention, passed = np.nan, False
            neighbor_passes.append(passed)
            ridge_rows.append(
                {
                    "candidate": row.candidate,
                    "direction": direction,
                    "neighbor_low": key[0],
                    "neighbor_high": key[1],
                    "neighbor_exists": exists,
                    "neighbor_hard_gate_pass": bool(decision_lookup.loc[key, "hard_gate_pass"]) if exists else False,
                    "calmar_retention": retention,
                    "neighbor_width_pass": passed,
                }
            )
        width_flags[row.candidate] = bool(row.hard_gate_pass and all(neighbor_passes))
    decisions["width_supported"] = decisions["candidate"].map(width_flags)
    wide = decisions[decisions["width_supported"]].sort_values(
        ["model_full_calmar", "model_completed_cycles", "model_holding_ratio", "low_threshold", "high_threshold"],
        ascending=[False, False, True, True, True],
    )
    hard = decisions[decisions["hard_gate_pass"]].sort_values("model_full_calmar", ascending=False)
    selected = str(wide.iloc[0]["candidate"]) if len(wide) else None
    raw = str(hard.iloc[0]["candidate"]) if len(hard) else None
    if selected is not None:
        decision, status = "freeze_wide_stable_fixed_threshold_candidate", "wide_stable"
    elif raw is not None:
        decision, status = "watchlist_peak_or_ridge", "peak_or_ridge_only"
    else:
        decision, status = "no_fixed_threshold_candidate", "reject"
    summary = {
        "decision": decision,
        "robustness_status": status,
        "selected_candidate": selected,
        "raw_hard_gate_winner": raw,
        "hard_gate_pass_count": int(decisions["hard_gate_pass"].sum()),
        "width_supported_count": int(decisions["width_supported"].sum()),
        "relative_diagnostics_eligible_for_selection": False,
        "live_approved": False,
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return decisions, pd.DataFrame(ridge_rows), summary


def fmt_pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value):.2%}"


def build_record(
    metrics_table: pd.DataFrame,
    decisions: pd.DataFrame,
    cycles: pd.DataFrame,
    summary: dict[str, Any],
    checks: dict[str, Any],
) -> str:
    lines = [
        "# IM固定经济估值增仓开仓/退出阈值扫描 v15",
        "",
        "状态：研究结果；未批准实盘。",
        "",
        "## 结论",
        "",
        f"- 正式决定：`{summary['decision']}`；稳健性：`{summary['robustness_status']}`。",
        f"- 硬门槛通过 {summary['hard_gate_pass_count']} / 144；严格四邻宽度通过 {summary['width_supported_count']} / 144。",
        f"- 机械入选：`{summary['selected_candidate']}`；硬门槛原始最高：`{summary['raw_hard_gate_winner']}`。",
        "- 57个月相对分位只有一个历史周期，两条相对线仅作诊断，不参与选择。",
        "",
        "## 模型层主要路径",
        "",
        "| 路径 | 窗口 | CAGR | MaxDD | Calmar | 相对底仓CAGR | 相对底仓回撤 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    candidates = ["base_core_put"]
    for value in (summary["selected_candidate"], summary["raw_hard_gate_winner"]):
        if value and value not in candidates:
            candidates.append(value)
    top = decisions.sort_values("model_full_calmar", ascending=False).head(5)["candidate"].tolist()
    candidates.extend(value for value in top if value not in candidates)
    show = metrics_table[
        metrics_table["layer"].eq("model") & metrics_table["candidate"].isin(candidates)
    ]
    for row in show.sort_values(["candidate", "window"]).itertuples(index=False):
        lines.append(
            f"| {row.candidate} | {row.window} | {fmt_pct(row.ann_return)} | {fmt_pct(row.max_dd)} | "
            f"{row.calmar:.3f} | {fmt_pct(row.ann_return_delta_vs_core)} | {fmt_pct(row.max_dd_improvement_vs_core)} |"
        )
    lines.extend(
        [
            "",
            "## 事件与执行",
            "",
            "| 层 | 路径 | 完成周期 | 开仓年份数 | 持有比例 | 初始承接 | 期末持仓 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    cycle_show = cycles[cycles["candidate"].isin(candidates)]
    for row in cycle_show.sort_values(["candidate", "layer"]).itertuples(index=False):
        lines.append(
            f"| {row.layer} | {row.candidate} | {row.completed_cycles} | {row.entry_years} | "
            f"{row.holding_ratio:.2%} | {row.initial_carry} | {row.ending_state} |"
        )
    lines.extend(
        [
            "",
            "## 审计边界",
            "",
            f"- 固定候选/相对诊断：{checks['fixed_candidate_count']} / {checks['relative_diagnostic_count']}。",
            f"- 底仓逐日奇偶最大误差：{checks['base_parity_max_abs']:.3e}。",
            f"- 收益/现金恒等式最大误差：{checks['return_identity_max_abs']:.3e} / {checks['cash_identity_max_abs']:.3e}。",
            f"- T信号/T+1执行因果失败：{checks['causality_failures']}；真实非正开盘或成交量：{checks['invalid_real_execution_quotes']}。",
            f"- 新增仓开仓或Put换约时资本穿透共 {checks['capital_execution_breach_rows']} 行，涉及 {checks['capital_breach_fixed_candidates']} 条固定候选；这些候选自动不通过资本门槛。普通持有日Put升值超过静态现金仅记录、不误判为追加现金。",
            "- 模型层上市前收益是中证1000全收益指数代理，不包含上市前IM真实贴水；真实层从2022-07-22开始。",
            "- 官方开盘与历史成交量不等于保证成交或容量；结果不是交易建议。",
            "",
        ]
    )
    return "\n".join(lines)


def output_manifest(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): sha256(path)
        for path in sorted(folder.rglob("*"))
        if path.is_file() and path.name != "output_manifest.json"
    }


def main() -> None:
    git_before = git_status()
    upstream_checks = verify_inputs(require_fresh_output=True)
    base, score, percentile = load_sources()
    model_market, model_market_checks = build_model_market(base, score, percentile)
    real_market, real_market_checks = build_real_market(base, score, percentile)
    fixed_history = score[["date", "unbounded_median_knot"]].copy()
    relative_history = percentile[["date", "rolling_percentile"]].copy()

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []
    base_recomputed: dict[str, pd.DataFrame] = {}
    for layer, market in (("model", model_market), ("real", real_market)):
        layer_base = base[base["layer"].eq(layer)].sort_values("date").reset_index(drop=True)
        flat, flat_cycle = flat_overlay(market, layer)
        recomputed = assemble_candidate(layer_base, flat, "base_core_put", "core")
        base_recomputed[layer] = recomputed
        daily_parts.append(recomputed)
        cycle_rows.append(flat_cycle)
        for low, high in fixed_grid():
            candidate = fixed_label(low, high)
            overlay, trades, cycle = simulate_overlay(
                market,
                fixed_history,
                "unbounded_median_knot",
                low,
                high,
                candidate,
                "fixed_score",
                layer,
            )
            daily_parts.append(assemble_candidate(layer_base, overlay, candidate, "fixed_score"))
            trade_parts.append(trades)
            cycle_rows.append(cycle)
        for low, high in RELATIVE_DIAGNOSTICS:
            candidate = relative_label(low, high)
            overlay, trades, cycle = simulate_overlay(
                market,
                relative_history,
                "rolling_percentile",
                low,
                high,
                candidate,
                "relative_diagnostic",
                layer,
            )
            daily_parts.append(
                assemble_candidate(layer_base, overlay, candidate, "relative_diagnostic")
            )
            trade_parts.append(trades)
            cycle_rows.append(cycle)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"])
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(
        ["layer", "candidate", "execution_date"]
    )
    cycles = pd.DataFrame(cycle_rows).sort_values(["layer", "candidate"])
    metric_table = metrics_by_window(daily)
    wide = wide_metrics(metric_table)
    annual = annual_metrics(daily)
    drawdowns = drawdown_audit(daily)
    capital = capital_audit(daily)
    decisions, ridge, summary = decide(metric_table, cycles, capital)
    scan_surface = decisions.merge(
        metric_table[(metric_table["layer"].eq("model")) & metric_table["window"].eq("full")][
            ["candidate", "ann_return_delta_vs_core", "max_dd_improvement_vs_core"]
        ],
        on="candidate",
        validate="one_to_one",
    )

    parity_errors: list[float] = []
    for layer in ("model", "real"):
        original = base[base["layer"].eq(layer)][["date", "cash_ret"]]
        rebuilt = base_recomputed[layer][["date", "cash_ret"]]
        joined = rebuilt.merge(original, on="date", suffixes=("_new", "_v14"), validate="one_to_one")
        parity_errors.append(float((joined["cash_ret_new"] - joined["cash_ret_v14"]).abs().max()))
    return_expected = (
        (1.0 + daily["combined_gross_before_cost"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        - 1.0
    )
    causal = trades[~trades["execution_reason"].eq("initial_listing_carry")].copy()
    checks = {
        **upstream_checks,
        "candidate_count_per_layer": int(daily.groupby("layer")["candidate"].nunique().min()),
        "expected_candidate_count_per_layer": 1 + len(fixed_grid()) + len(RELATIVE_DIAGNOSTICS),
        "duplicate_candidate_dates": int(daily.duplicated(["layer", "candidate", "date"]).sum()),
        "base_parity_max_abs": max(parity_errors),
        "return_identity_max_abs": float((daily["ret"] - return_expected).abs().max()),
        "cash_identity_max_abs": float(
            (daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * CASH_DAILY)).abs().max()
        ),
        "causality_failures": int(
            (pd.to_datetime(causal["execution_date"]) <= pd.to_datetime(causal["signal_date"])).sum()
        ),
        "invalid_real_execution_quotes": int(
            (
                trades["layer"].eq("real")
                & (trades["execution_open"].le(0) | trades["execution_volume"].le(0))
            ).sum()
        ),
        "invalid_total_im_units": int((~daily["total_im_units"].isin([1.0, 2.0])).sum()),
        "negative_cash_weight": int(daily["cash_weight"].lt(-1e-12).sum()),
        "daily_put_mark_above_pre_put_cash_rows": int(
            (daily["put_mark_fraction"] > daily["cash_weight_before_put"] + 1e-12).sum()
        ),
        "capital_execution_breach_rows": int(capital["capital_execution_breach_rows"].sum()),
        "capital_breach_fixed_candidates": int(
            capital[
                capital["candidate"].str.startswith("fixed_")
                & capital["capital_execution_breach_rows"].gt(0)
            ]["candidate"].nunique()
        ),
        "hard_gate_capital_failures": int(
            decisions[decisions["hard_gate_pass"]]["capital_gate"].eq(False).sum()
        ),
        "invalid_return_rows": int(
            daily[["ret", "cash_ret"]].isna().sum().sum()
            + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()
        ),
        "pending_fixed_orders": int(
            cycles[cycles["family"].eq("fixed_score")]["pending_order_end"].sum()
        ),
        "fixed_state_geometry_at_least_two_cycles": int(
            (
                (cycles["layer"].eq("model"))
                & cycles["family"].eq("fixed_score")
                & cycles["completed_cycles"].ge(2)
            ).sum()
        ),
        "model_market": model_market_checks,
        "real_market": real_market_checks,
    }
    checks["all_checks_passed"] = bool(
        checks["fixed_candidate_count"] == 144
        and checks["relative_diagnostic_count"] == 2
        and checks["candidate_count_per_layer"] == checks["expected_candidate_count_per_layer"] == 147
        and checks["duplicate_candidate_dates"] == 0
        and checks["base_parity_max_abs"] <= 1e-14
        and checks["return_identity_max_abs"] <= 1e-14
        and checks["cash_identity_max_abs"] <= 1e-14
        and checks["causality_failures"] == 0
        and checks["invalid_real_execution_quotes"] == 0
        and checks["invalid_total_im_units"] == 0
        and checks["negative_cash_weight"] == 0
        and checks["hard_gate_capital_failures"] == 0
        and checks["invalid_return_rows"] == 0
        and checks["pending_fixed_orders"] == 0
        and checks["fixed_state_geometry_at_least_two_cycles"] == 72
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v15 integrity checks failed: {checks}")

    record = build_record(metric_table, decisions, cycles, summary, checks)
    if STAGING.exists():
        raise FileExistsError(STAGING)
    STAGING.mkdir(parents=True)
    record_path = STAGING / "record.md"
    record_path.write_text(record, encoding="utf-8")
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    metric_table.to_csv(STAGING / "metrics_by_window.csv", index=False)
    wide.to_csv(STAGING / "window_metrics_wide.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    scan_surface.to_csv(STAGING / "scan_surface.csv", index=False)
    decisions.to_csv(STAGING / "candidate_decisions.csv", index=False)
    ridge.to_csv(STAGING / "ridge_width.csv", index=False)
    trades.to_csv(STAGING / "overlay_trade_audit.csv", index=False)
    cycles.to_csv(STAGING / "overlay_cycle_summary.csv", index=False)
    drawdowns.to_csv(STAGING / "drawdown_audit.csv", index=False)
    capital.to_csv(STAGING / "capital_audit.csv", index=False)
    cycles.to_csv(STAGING / "state_geometry_audit.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()},
        "samples": {
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "grid": {
            "entry_thresholds": list(ENTRY_THRESHOLDS),
            "exit_thresholds": list(EXIT_THRESHOLDS),
            "minimum_gap": 0.30,
            "fixed_candidates": len(fixed_grid()),
            "relative_diagnostics": [relative_label(*pair) for pair in RELATIVE_DIAGNOSTICS],
        },
        "execution": {
            "signal": "T close valuation",
            "model": "T+1 CSI1000 modeled total-return open unit",
            "real": "T+1 active IM official open",
            "one_way_cost": ONE_WAY_COST,
            "overlay_roll_round_trip_cost": 2 * ONE_WAY_COST,
            "margin_buffer_per_im_unit": MARGIN_RATE,
            "cash_annual_return": 0.03,
            "overlay_put": "none; frozen core Put unchanged",
        },
        "decision": summary,
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "limitations": [
            "Pre-IM model uses CSI1000 total-return index and does not contain real IM discount",
            "Real IM/MO sample starts 2022-07-22",
            "Official open and volume do not guarantee fill or capacity",
            "Relative 57-month diagnostics have only one completed historical cycle",
            "Research only; not live approved",
        ],
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (STAGING / "command_log.txt").write_text(
        "uv run im_fixed_valuation_overlay_entry_exit_scan_v15.py\n", encoding="utf-8"
    )
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_manifest(STAGING), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    STAGING.replace(OUTPUT)

    decisions.to_csv(SCAN / "scan_summary.csv", index=False)
    metric_table[metric_table["family"].eq("fixed_score")].to_csv(
        SCAN / "window_metrics.csv", index=False
    )
    shutil.copy2(OUTPUT / "record.md", SCAN / "record.md")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("uv run im_fixed_valuation_overlay_entry_exit_scan_v15.py\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
