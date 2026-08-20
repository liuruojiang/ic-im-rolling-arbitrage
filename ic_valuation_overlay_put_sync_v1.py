#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""IC valuation overlay with and without synchronous Put protection."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_510500_put_mom120_delta_floor_v21 as v21


ROOT = Path(__file__).resolve().parent
VERSION = "ic_valuation_overlay_put_sync_v1"
END = pd.Timestamp("2026-08-14")
MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-09-19")
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "7cf83eea40fb8d4aafb6c05a955be010e8b0ad26898c589033fb87a42b6935c3"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260818_ic_valuation_overlay_put_sync_v1"

V21_OUTPUT = ROOT / "outputs" / "ic_510500_put_mom120_delta_floor_v21"
V21_DAILY = V21_OUTPUT / "daily_candidates.csv.gz"
V21_SCHEDULE = V21_OUTPUT / "evaluation_schedule.csv.gz"
V21_TRADES = V21_OUTPUT / "trade_audit.csv"
V21_MANIFEST = V21_OUTPUT / "output_manifest.json"
IC_RAW = ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv"
IC_DAILY = ROOT / "outputs" / "ic_monthly_discount_roll_v1" / "daily_nav.csv"
SCORE_FILE = (
    ROOT
    / "outputs"
    / "ic_fixed_valuation_unbounded_score_v6"
    / "daily_unbounded_fixed_scores.csv.gz"
)
MAINLINE = ROOT / "docs" / "ic_510500_put_research_mainline_v1.md"

TRADING_DAYS = 252
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0
LOW_THRESHOLDS = (0.75, 1.00, 1.25)
HIGH_THRESHOLDS = (1.90, 2.00, 2.10)
PRIMARY_PAIR = (1.00, 2.00)
PUT_MODES = ("core_put_only", "sync_put_total_ic")
WINDOWS = v21.WINDOWS

INPUT_HASHES = {
    ROOT / "ic_510500_put_mom120_delta_floor_v21.py": "e43a80085d3030d8ec87a6c89ad3be73331cf83f18226a9c88dfe7ea2299106e",
    ROOT / "docs" / "ic_510500_put_mom120_delta_floor_v21_spec.md": "a928a8f8b6d03d42cb4156c861653974aaccaae1953d9bbd23153f2e4e28c329",
    V21_MANIFEST: "0d7fa231586d31aa0d0c093f4ca5624ae8fb6dd43c7bb794ae5b2310d699cef6",
    V21_DAILY: "11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a",
    V21_SCHEDULE: "dba99b2aa67a52c9b17a25e03e89325207aae6614bc651052b99168575a38d7a",
    V21_TRADES: "fb692bb0388018680891027ef3328c7b99abab86e9cac4f0a8b61d8e5437c22e",
    IC_RAW: "4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104",
    IC_DAILY: "bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d",
    SCORE_FILE: "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77",
    MAINLINE: "6da92d886f184277cffcdbbbd706d43ee057c7e1d4502410b8c7b12cde8eb4b5",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def pair_label(low: float, high: float) -> str:
    return f"L{low:.2f}_H{high:.2f}"


def candidate_label(layer: str, low: float, high: float, mode: str) -> str:
    return f"{layer}__{pair_label(low, high)}__{mode}"


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            raise RuntimeError(f"Frozen input changed: {path.relative_to(ROOT)}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads(V21_MANIFEST.read_text(encoding="utf-8"))
    mismatches: list[dict[str, str]] = []
    for name, expected in manifest.items():
        path = V21_OUTPUT / name
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append({"file": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"v21 output manifest mismatch: {mismatches}")
    return {"v21_manifest_files": len(manifest), "v21_manifest_match": True}


def load_active_chain(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[str, Any]]:
    core = frames["ic"][
        ["date", "contract", "settle", "ic_gross_ret", "cost_rate", "ic_net_ret", "roll_to"]
    ].copy()
    raw = pd.read_csv(IC_RAW, parse_dates=["date"])[
        ["date", "contract", "open", "settle", "pre_settle", "volume"]
    ].rename(columns={"settle": "raw_settle", "volume": "raw_volume"})
    scores = pd.read_csv(SCORE_FILE, parse_dates=["date"])[
        [
            "date",
            "unbounded_median_knot",
            "pb_aggregate",
            "erp",
            "trailing_dividend_contribution",
        ]
    ]
    chain = core.merge(raw, on=["date", "contract"], validate="one_to_one").merge(
        scores, on="date", validate="one_to_one"
    )
    chain = chain.sort_values("date").reset_index(drop=True)
    if chain["date"].min() != MODEL_START or chain["date"].max() != END:
        raise RuntimeError("Unexpected IC active-chain range")
    if chain[["open", "settle", "raw_settle", "pre_settle", "raw_volume"]].isna().any().any():
        raise RuntimeError("Missing active IC quote")
    if (chain[["open", "settle", "raw_settle", "pre_settle", "raw_volume"]].le(0)).any().any():
        raise RuntimeError("Non-positive active IC quote")
    settlement_error = float((chain["settle"] - chain["raw_settle"]).abs().max())
    gross_expected = chain["settle"] / chain["pre_settle"] - 1.0
    gross_error = float((chain.loc[chain.index[1:], "ic_gross_ret"] - gross_expected.iloc[1:]).abs().max())
    if settlement_error > 1e-12 or gross_error > 1e-12:
        raise RuntimeError(
            f"IC active chain parity failed: settle={settlement_error}, gross={gross_error}"
        )
    chain["roll_event"] = chain["roll_to"].fillna("").astype(str).ne("")
    return chain, {
        "rows": len(chain),
        "start": str(chain["date"].min().date()),
        "end": str(chain["date"].max().date()),
        "roll_events": int(chain["roll_event"].sum()),
        "settlement_parity_max_abs": settlement_error,
        "gross_return_parity_max_abs": gross_error,
    }


def simulate_overlay(
    chain: pd.DataFrame, low: float, high: float
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    state = False
    pending: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    dates = list(pd.DatetimeIndex(chain["date"]))
    for index, row in enumerate(chain.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        held_before = state
        buy = False
        sell = False
        signal_date = pd.NaT
        signal_score = np.nan
        if pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            signal_date = pd.Timestamp(pending["signal_date"])
            signal_score = float(pending["signal_score"])
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError("Overlay duplicate buy")
                state = True
                buy = True
            else:
                if not state:
                    raise RuntimeError("Overlay sell while flat")
                state = False
                sell = True
            trades.append(
                {
                    "pair": pair_label(low, high),
                    "low_threshold": low,
                    "high_threshold": high,
                    "action": pending["action"],
                    "signal_date": signal_date,
                    "signal_score": signal_score,
                    "execution_date": day,
                    "execution_contract": row.contract,
                    "execution_open": float(row.open),
                    "execution_volume": float(row.raw_volume),
                }
            )
            pending = None

        held_eod = state
        if held_before and held_eod:
            gross = float(row.settle) / float(row.pre_settle) - 1.0
        elif not held_before and held_eod:
            gross = float(row.settle) / float(row.open) - 1.0
        elif held_before and not held_eod:
            gross = float(row.open) / float(row.pre_settle) - 1.0
        else:
            gross = 0.0
        trade_cost = ONE_WAY_COST * (int(buy) + int(sell))
        roll_cost = 2.0 * ONE_WAY_COST if held_eod and bool(row.roll_event) else 0.0
        rows.append(
            {
                "date": day,
                "pair": pair_label(low, high),
                "low_threshold": low,
                "high_threshold": high,
                "overlay_held_before": int(held_before),
                "overlay_held_eod": int(held_eod),
                "overlay_buy": int(buy),
                "overlay_sell": int(sell),
                "overlay_gross_ret": gross,
                "overlay_trade_cost_rate": trade_cost,
                "overlay_roll_cost_rate": roll_cost,
                "overlay_cost_rate": trade_cost + roll_cost,
                "total_ic_units": 1.0 + float(held_eod),
                "valuation_score": float(row.unbounded_median_knot),
                "roll_event": bool(row.roll_event),
                "signal_date_executed": signal_date,
                "signal_score_executed": signal_score,
            }
        )

        score = float(row.unbounded_median_knot)
        if pending is None:
            action = "buy" if (not state and score <= low + 1e-12) else None
            if state and score + 1e-12 >= high:
                action = "sell"
            if action is not None:
                if index + 1 < len(dates):
                    pending = {
                        "action": action,
                        "signal_date": day,
                        "signal_score": score,
                        "execution_date": dates[index + 1],
                    }
                else:
                    pending = {
                        "action": action,
                        "signal_date": day,
                        "signal_score": score,
                        "execution_date": pd.NaT,
                    }

    daily = pd.DataFrame(rows)
    trade_frame = pd.DataFrame(trades)
    entries = int(trade_frame["action"].eq("buy").sum()) if len(trade_frame) else 0
    exits = int(trade_frame["action"].eq("sell").sum()) if len(trade_frame) else 0
    audit = {
        "pair": pair_label(low, high),
        "entries": entries,
        "exits": exits,
        "completed_cycles": min(entries, exits),
        "holding_days": int(daily["overlay_held_eod"].sum()),
        "holding_ratio": float(daily["overlay_held_eod"].mean()),
        "ending_state": int(state),
        "pending_order_end": int(pending is not None),
    }
    return daily, trade_frame, audit


def flat_overlay(chain: pd.DataFrame) -> pd.DataFrame:
    result = chain[["date"]].copy()
    for column in [
        "overlay_held_before",
        "overlay_held_eod",
        "overlay_buy",
        "overlay_sell",
    ]:
        result[column] = 0
    for column in [
        "overlay_gross_ret",
        "overlay_trade_cost_rate",
        "overlay_roll_cost_rate",
        "overlay_cost_rate",
    ]:
        result[column] = 0.0
    result["total_ic_units"] = 1.0
    result["valuation_score"] = np.nan
    result["roll_event"] = False
    result["signal_date_executed"] = pd.NaT
    result["signal_score_executed"] = np.nan
    return result


def build_candidate_schedule(
    base_schedule: pd.DataFrame,
    overlay: pd.DataFrame,
    layer: str,
    candidate: str,
    mode: str,
    low: float | None,
    high: float | None,
) -> pd.DataFrame:
    schedule = base_schedule[
        base_schedule["layer"].eq(layer)
        & base_schedule["signal_variant"].eq("l190_mom25")
    ].copy()
    state = overlay[["date", "total_ic_units", "overlay_held_eod"]].rename(
        columns={"date": "execution_date"}
    )
    schedule = schedule.merge(state, on="execution_date", validate="one_to_one")
    schedule["core_target_delta"] = schedule["target_delta"].astype(float)
    scale = schedule["total_ic_units"] if mode == "sync_put_total_ic" else 1.0
    schedule["target_delta"] = schedule["core_target_delta"] * scale
    schedule["binary_target_fraction"] = schedule["target_delta"]
    schedule["three_tier_target_fraction"] = schedule["target_delta"]
    schedule["signal_variant"] = candidate
    schedule["candidate"] = candidate
    schedule["put_mode"] = mode
    schedule["low_threshold"] = low
    schedule["high_threshold"] = high
    return schedule.sort_values("execution_date").reset_index(drop=True)


def mainline_put_rows(frozen_daily: pd.DataFrame, layer: str) -> pd.DataFrame:
    candidate = f"{layer}_l190_mom25"
    result = frozen_daily[frozen_daily["candidate"].eq(candidate)].copy()
    if result.empty:
        raise RuntimeError(f"Missing frozen mainline candidate: {candidate}")
    return result.reset_index(drop=True)


def run_sync_put(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
    layer: str,
    candidate: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    roll_dates = v21.v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    if layer == "model":
        return v21.v20.run_model_delta(
            frames["ic"], schedule, market, candidate, roll_dates
        )
    return v21.v20.run_real_delta(
        frames["ic"], schedule, frames, market, candidate, roll_dates
    )


def assemble_candidate(
    chain: pd.DataFrame,
    overlay: pd.DataFrame,
    put_rows: pd.DataFrame,
    layer: str,
    candidate: str,
    mode: str,
    low: float | None,
    high: float | None,
) -> pd.DataFrame:
    start = MODEL_START if layer == "model" else REAL_START
    core = chain[chain["date"] >= start][
        ["date", "contract", "open", "settle", "pre_settle", "ic_gross_ret", "cost_rate", "ic_net_ret"]
    ].copy()
    extra = overlay[overlay["date"] >= start].copy()
    drop_put = [column for column in ["ic_gross_ret", "cost_rate", "ic_net_ret", "gross_ret", "ret", "cash_weight", "cash_ret", "cash_nav", "cash_drawdown"] if column in put_rows]
    puts = put_rows[put_rows["date"] >= start].drop(columns=drop_put).copy()
    if "candidate" in puts:
        puts = puts.drop(columns="candidate")
    result = core.merge(extra, on="date", validate="one_to_one").merge(
        puts, on="date", validate="one_to_one"
    )
    result["candidate"] = candidate
    result["layer"] = layer
    result["put_mode"] = mode
    result["low_threshold"] = low
    result["high_threshold"] = high
    result["pair"] = "base" if low is None else pair_label(float(low), float(high))
    result["gross_ret"] = (
        result["ic_gross_ret"]
        + result["overlay_gross_ret"]
        + result["put_pnl_ret"]
    )
    result["futures_cost_rate"] = result["cost_rate"] + result["overlay_cost_rate"]
    result["ret"] = (
        (1.0 + result["gross_ret"])
        * (1.0 - result["futures_cost_rate"])
        * (1.0 - result["put_cost_rate"])
        - 1.0
    )
    result["cash_weight_before_put"] = (
        1.0 - MARGIN_RATE * result["total_ic_units"]
    ).clip(lower=0.0)
    result["cash_weight"] = (
        result["cash_weight_before_put"] - result["put_mark_fraction"]
    ).clip(lower=0.0)
    result["cash_ret"] = result["ret"] + result["cash_weight"] * CASH_DAILY
    result["effective_delta_per_total_ic"] = (
        result["effective_delta_hedge_ratio"] / result["total_ic_units"]
    )
    result["cash_nav"] = (1.0 + result["cash_ret"]).cumprod()
    result["cash_drawdown"] = result["cash_nav"] / result["cash_nav"].cummax() - 1.0
    return result


def metrics(returns: pd.Series) -> dict[str, float]:
    return v21.metrics(returns)


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        first = group.iloc[0]
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else END - offset
            available = bool(offset is None or group["date"].min() <= requested)
            subset = group if offset is None else group[group["date"] >= requested]
            values = metrics(subset["cash_ret"]) if available else {
                "total_return": np.nan,
                "ann_return": np.nan,
                "ann_vol": np.nan,
                "sharpe_repo": np.nan,
                "max_dd": np.nan,
            }
            rows.append(
                {
                    "candidate": candidate,
                    "layer": first["layer"],
                    "pair": first["pair"],
                    "put_mode": first["put_mode"],
                    "low_threshold": first["low_threshold"],
                    "high_threshold": first["high_threshold"],
                    "window": window,
                    "available": available,
                    "requested_start": requested,
                    "actual_start": subset["date"].min() if available else pd.NaT,
                    "end": subset["date"].max() if available else pd.NaT,
                    "rows": len(subset) if available else 0,
                    **values,
                }
            )
    table = pd.DataFrame(rows)
    baseline = table[table["put_mode"].eq("base_core_put")][
        ["layer", "window", "ann_return", "max_dd"]
    ].rename(columns={"ann_return": "base_ann_return", "max_dd": "base_max_dd"})
    table = table.merge(baseline, on=["layer", "window"], validate="many_to_one")
    table["ann_return_delta_vs_base"] = table["ann_return"] - table["base_ann_return"]
    table["max_dd_improvement_vs_base"] = table["max_dd"] - table["base_max_dd"]
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in table.groupby("candidate", sort=True):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "layer": first["layer"],
            "pair": first["pair"],
            "put_mode": first["put_mode"],
            "low_threshold": first["low_threshold"],
            "high_threshold": first["high_threshold"],
        }
        for item in group.itertuples(index=False):
            for field in ["ann_return", "max_dd", "ann_vol", "sharpe_repo"]:
                row[f"{field}_{item.window}"] = getattr(item, field)
        wide_rows.append(row)
    return table, pd.DataFrame(wide_rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (candidate, year), group in daily.groupby(
        ["candidate", daily["date"].dt.year], sort=True
    ):
        first = group.iloc[0]
        rows.append(
            {
                "candidate": candidate,
                "layer": first["layer"],
                "pair": first["pair"],
                "put_mode": first["put_mode"],
                "year": int(year),
                "rows": len(group),
                **metrics(group.sort_values("date")["cash_ret"]),
            }
        )
    return pd.DataFrame(rows)


def pairwise_put_management(metric_table: pd.DataFrame) -> pd.DataFrame:
    tested = metric_table[metric_table["put_mode"].isin(PUT_MODES)].copy()
    left = tested[tested["put_mode"].eq("sync_put_total_ic")].copy()
    right = tested[tested["put_mode"].eq("core_put_only")].copy()
    keys = ["layer", "pair", "low_threshold", "high_threshold", "window"]
    joined = left.merge(right, on=keys, suffixes=("_sync", "_core"), validate="one_to_one")
    return pd.DataFrame(
        {
            **{key: joined[key] for key in keys},
            "available": joined["available_sync"] & joined["available_core"],
            "sync_candidate": joined["candidate_sync"],
            "core_candidate": joined["candidate_core"],
            "sync_ann_return": joined["ann_return_sync"],
            "core_ann_return": joined["ann_return_core"],
            "ann_return_delta_sync_minus_core": joined["ann_return_sync"] - joined["ann_return_core"],
            "sync_max_dd": joined["max_dd_sync"],
            "core_max_dd": joined["max_dd_core"],
            "max_dd_improvement_sync_minus_core": joined["max_dd_sync"] - joined["max_dd_core"],
        }
    )


def exposure_diagnostics(
    daily: pd.DataFrame, overlay_trades: pd.DataFrame, put_trades: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        first = group.iloc[0]
        otrade = overlay_trades[overlay_trades["candidate"].eq(candidate)] if len(overlay_trades) else pd.DataFrame()
        ptrade = put_trades[put_trades["candidate"].eq(candidate)] if len(put_trades) else pd.DataFrame()
        active = group[group["put_qty"].astype(float).gt(0)]
        trade_dates = set(pd.to_datetime(ptrade["actual_execution_date"]).dropna()) if len(ptrade) else set()
        post_trade = group[group["date"].isin(trade_dates)]
        target_errors = pd.to_numeric(ptrade.get("target_delta_error", pd.Series(dtype=float)), errors="coerce").dropna()
        rows.append(
            {
                "candidate": candidate,
                "layer": first["layer"],
                "pair": first["pair"],
                "put_mode": first["put_mode"],
                "overlay_holding_days": int(group["overlay_held_eod"].sum()),
                "overlay_holding_ratio": float(group["overlay_held_eod"].mean()),
                "overlay_entries": int(otrade["action"].eq("buy").sum()) if len(otrade) else 0,
                "overlay_exits": int(otrade["action"].eq("sell").sum()) if len(otrade) else 0,
                "overlay_roll_events": int((group["overlay_held_eod"].eq(1) & group["roll_event"]).sum()),
                "overlay_cost_sum": float(group["overlay_cost_rate"].sum()),
                "put_protected_days": len(active),
                "put_trade_events": len(ptrade),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "max_post_trade_put_mark_fraction": float(post_trade["put_mark_fraction"].max()) if len(post_trade) else 0.0,
                "min_post_trade_cash_before_put": float(post_trade["cash_weight_before_put"].min()) if len(post_trade) else float(group["cash_weight_before_put"].min()),
                "max_actual_notional_fraction": float(group["actual_notional_fraction"].max()),
                "max_effective_delta_total": float(group["effective_delta_hedge_ratio"].max()),
                "max_effective_delta_per_total_ic": float(group["effective_delta_per_total_ic"].max()),
                "max_target_delta_error": float(target_errors.max()) if len(target_errors) else 0.0,
                "deferred_days": int(group["deferred_adjustment"].sum()),
            }
        )
    return pd.DataFrame(rows)


def decide(pairwise: pd.DataFrame, exposure: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for low in LOW_THRESHOLDS:
        for high in HIGH_THRESHOLDS:
            pair = pair_label(low, high)
            sample = pairwise[pairwise["pair"].eq(pair) & pairwise["available"]]
            model = sample[sample["layer"].eq("model")].set_index("window")
            real = sample[sample["layer"].eq("real")].set_index("window")
            full_dd = (
                float(model.loc["full", "max_dd_improvement_sync_minus_core"]) >= 0.01 - 1e-12
                and float(real.loc["full", "max_dd_improvement_sync_minus_core"]) >= 0.01 - 1e-12
            )
            model_dd_count = int((model["max_dd_improvement_sync_minus_core"] > 1e-12).sum())
            real_dd_count = int((real["max_dd_improvement_sync_minus_core"] > 1e-12).sum())
            no_dd_breach = bool(
                (sample["max_dd_improvement_sync_minus_core"] >= -0.01 - 1e-12).all()
            )
            return_ok = True
            for window, row in model.iterrows():
                tolerance = -0.01 if window in {"full", "last_10y", "last_5y"} else -0.03
                return_ok &= float(row["ann_return_delta_sync_minus_core"]) >= tolerance - 1e-12
            for window, row in real.iterrows():
                tolerance = -0.01 if window == "full" else -0.03
                return_ok &= float(row["ann_return_delta_sync_minus_core"]) >= tolerance - 1e-12
            candidate = candidate_label("model", low, high, "sync_put_total_ic")
            real_candidate = candidate_label("real", low, high, "sync_put_total_ic")
            diag = exposure[exposure["candidate"].isin([candidate, real_candidate])]
            capital_ok = bool(
                (diag["max_post_trade_put_mark_fraction"] <= diag["min_post_trade_cash_before_put"] + 1e-12).all()
            )
            delta_ok = bool(
                (diag.loc[diag["layer"].eq("model"), "max_target_delta_error"] <= 1e-12).all()
                and (diag.loc[diag["layer"].eq("real"), "max_target_delta_error"] <= 0.02 + 1e-12).all()
            )
            passed = bool(
                full_dd
                and model_dd_count >= 3
                and real_dd_count >= 2
                and no_dd_breach
                and return_ok
                and capital_ok
                and delta_ok
            )
            rows.append(
                {
                    "pair": pair,
                    "low_threshold": low,
                    "high_threshold": high,
                    "full_dd_gate": full_dd,
                    "model_dd_windows_improved": model_dd_count,
                    "real_dd_windows_improved": real_dd_count,
                    "no_dd_breach": no_dd_breach,
                    "return_tolerance_pass": return_ok,
                    "capital_pass": capital_ok,
                    "delta_sizing_pass": delta_ok,
                    "preregistered_gate_pass": passed,
                }
            )
    decisions = pd.DataFrame(rows)
    primary = decisions[
        decisions["low_threshold"].eq(PRIMARY_PAIR[0])
        & decisions["high_threshold"].eq(PRIMARY_PAIR[1])
    ].iloc[0]
    neighbor_support = int(decisions["preregistered_gate_pass"].sum())
    primary_pass = bool(primary["preregistered_gate_pass"] and neighbor_support >= 3)
    summary = {
        "decision": "keep_sync_put_overlay_watchlist" if primary_pass else "do_not_prefer_sync_put_overlay",
        "stability_label": "width_supported" if primary_pass else "preregistered_gate_not_met",
        "primary_pair": pair_label(*PRIMARY_PAIR),
        "primary_pair_raw_gate_pass": bool(primary["preregistered_gate_pass"]),
        "passing_pair_count": neighbor_support,
        "passing_pairs": decisions.loc[decisions["preregistered_gate_pass"], "pair"].tolist(),
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "sample_reuse": "not_independent_oos",
    }
    return decisions, summary


def check_integrity(
    daily: pd.DataFrame,
    schedules: pd.DataFrame,
    overlay_trades: pd.DataFrame,
    put_trades: pd.DataFrame,
    frozen_daily: pd.DataFrame,
    chain_audit: dict[str, Any],
    exposure: pd.DataFrame,
) -> dict[str, Any]:
    expected_count = 2 * (1 + len(LOW_THRESHOLDS) * len(HIGH_THRESHOLDS) * len(PUT_MODES))
    if daily["candidate"].nunique() != expected_count:
        raise RuntimeError("Candidate count mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate candidate/date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid candidate return")
    regular = schedules[~schedules["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Put signal/execution leakage")
    if len(overlay_trades) and (overlay_trades["execution_date"] <= overlay_trades["signal_date"]).any():
        raise RuntimeError("Overlay signal/execution leakage")
    if len(overlay_trades) and (overlay_trades["execution_open"] <= 0).any():
        raise RuntimeError("Invalid overlay execution open")
    parity_rows = []
    for layer in ("model", "real"):
        new = daily[daily["candidate"].eq(f"{layer}__base_core_put")][
            ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "ret", "cash_ret"]
        ]
        old = frozen_daily[frozen_daily["candidate"].eq(f"{layer}_l190_mom25")][
            ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "ret", "cash_ret"]
        ]
        joined = new.merge(old, on="date", suffixes=("_new", "_v21"), validate="one_to_one")
        for column in ["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "ret", "cash_ret"]:
            parity_rows.append(float((joined[f"{column}_new"] - joined[f"{column}_v21"]).abs().max()))
    parity_max = max(parity_rows)
    if parity_max > 1e-14:
        raise RuntimeError(f"Mainline baseline parity failed: {parity_max}")
    core_only = daily[daily["put_mode"].eq("core_put_only")]
    core_parity_max = 0.0
    for (layer, _), group in core_only.groupby(["layer", "pair"]):
        base = daily[daily["candidate"].eq(f"{layer}__base_core_put")]
        joined = group[["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "target_delta"]].merge(
            base[["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "target_delta"]],
            on="date",
            suffixes=("_candidate", "_base"),
            validate="one_to_one",
        )
        for column in ["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "target_delta"]:
            core_parity_max = max(
                core_parity_max,
                float((joined[f"{column}_candidate"] - joined[f"{column}_base"]).abs().max()),
            )
    if core_parity_max > 1e-14:
        raise RuntimeError("Core-only Put path diverged from mainline")
    return_identity = (
        (1 + daily["gross_ret"])
        * (1 - daily["futures_cost_rate"])
        * (1 - daily["put_cost_rate"])
        - 1
    )
    return_error = float((daily["ret"] - return_identity).abs().max())
    cash_error = float((daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * CASH_DAILY)).abs().max())
    if max(return_error, cash_error) > 1e-14:
        raise RuntimeError("Return identity failed")
    model_error = float(exposure.loc[exposure["layer"].eq("model"), "max_target_delta_error"].max())
    real_error = float(exposure.loc[exposure["layer"].eq("real"), "max_target_delta_error"].max())
    if model_error > 1e-12 or real_error > 0.02 + 1e-12:
        raise RuntimeError("Delta sizing tolerance failed")
    return {
        "candidate_count": expected_count,
        "daily_rows": len(daily),
        "schedule_rows": len(schedules),
        "overlay_trade_rows": len(overlay_trades),
        "put_trade_rows": len(put_trades),
        "duplicate_candidate_dates": 0,
        "mainline_parity_max_abs": parity_max,
        "core_only_put_parity_max_abs": core_parity_max,
        "return_identity_max_abs": return_error,
        "cash_identity_max_abs": cash_error,
        "model_max_target_delta_error": model_error,
        "real_max_target_delta_error": real_error,
        "chain": chain_audit,
        "all_checks_passed": True,
    }


def fmt_pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value) * 100:.2f}%"


def build_record(
    metrics_table: pd.DataFrame,
    pairwise: pd.DataFrame,
    decisions: pd.DataFrame,
    exposure: pd.DataFrame,
    summary: dict[str, Any],
) -> str:
    primary = metrics_table[
        metrics_table["pair"].eq(pair_label(*PRIMARY_PAIR))
        | metrics_table["put_mode"].eq("base_core_put")
    ]
    lines = [
        f"# {VERSION} 正式记录",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "- 状态：研究回测，未批准实盘。",
        "- 底仓：1倍滚IC，沿用1.90/2.00/2.10估值阶梯与MOM120最低25% Delta的3个月95% Put主线。",
        "- 新增仓：低估买1倍IC、高估卖；期货T+1开盘，Put T+1共同收盘。",
        "- 对照：新增仓不加Put vs 同步把主线Put目标按总IC从1倍扩大到2倍。",
        "",
        "## 主参数与基线的强制窗口",
        "",
        "| 层 | 规则 | 窗口 | CAGR | MaxDD | 相对底仓CAGR | 相对底仓回撤改善 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in primary.sort_values(["layer", "put_mode", "window"]).itertuples(index=False):
        if not row.available:
            lines.append(f"| {row.layer} | {row.put_mode} | {row.window} | N/A | N/A | N/A | N/A |")
        else:
            lines.append(
                f"| {row.layer} | {row.put_mode} | {row.window} | {fmt_pct(row.ann_return)} | {fmt_pct(row.max_dd)} | {fmt_pct(row.ann_return_delta_vs_base)} | {fmt_pct(row.max_dd_improvement_vs_base)} |"
            )
    full_pairs = pairwise[pairwise["window"].eq("full")].copy()
    full_pairs["sync_ann_return"] = full_pairs["sync_ann_return"].map(fmt_pct)
    full_pairs["core_ann_return"] = full_pairs["core_ann_return"].map(fmt_pct)
    full_pairs["ann_return_delta_sync_minus_core"] = full_pairs["ann_return_delta_sync_minus_core"].map(fmt_pct)
    full_pairs["sync_max_dd"] = full_pairs["sync_max_dd"].map(fmt_pct)
    full_pairs["core_max_dd"] = full_pairs["core_max_dd"].map(fmt_pct)
    full_pairs["max_dd_improvement_sync_minus_core"] = full_pairs["max_dd_improvement_sync_minus_core"].map(fmt_pct)
    show = full_pairs[[
        "layer",
        "pair",
        "core_ann_return",
        "core_max_dd",
        "sync_ann_return",
        "sync_max_dd",
        "ann_return_delta_sync_minus_core",
        "max_dd_improvement_sync_minus_core",
    ]]
    primary_exposure = exposure[
        exposure["pair"].eq(pair_label(*PRIMARY_PAIR))
        | exposure["put_mode"].eq("base_core_put")
    ][[
        "layer",
        "pair",
        "put_mode",
        "overlay_holding_days",
        "overlay_entries",
        "overlay_exits",
        "put_trade_events",
        "put_cost_sum",
        "max_post_trade_put_mark_fraction",
        "max_effective_delta_per_total_ic",
    ]]
    lines.extend(
        [
            "",
            "## 九组阈值：同步Put相对不同步的全样本增量",
            "",
            show.to_markdown(index=False),
            "",
            "## 预注册判定",
            "",
            decisions.to_markdown(index=False),
            "",
            f"- 结论：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
            f"- 通过原始门槛的阈值对：{summary['passing_pairs']}。",
            "- 该结论重复使用既有样本，不是独立样本外验证，也不是交易指令。",
            "",
            "## 主参数持仓与Put诊断",
            "",
            primary_exposure.to_markdown(index=False),
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    daily: pd.DataFrame,
    metrics_table: pd.DataFrame,
    wide: pd.DataFrame,
    pairwise: pd.DataFrame,
    annual: pd.DataFrame,
    overlay_trades: pd.DataFrame,
    put_trades: pd.DataFrame,
    schedules: pd.DataFrame,
    exposure: pd.DataFrame,
    overlay_cycles: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
    upstream: dict[str, Any],
    market_checks: dict[str, Any],
    git_before: str,
) -> None:
    OUTPUT.mkdir(parents=False, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics_table.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    pairwise.to_csv(OUTPUT / "pairwise_put_management.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    overlay_trades.to_csv(OUTPUT / "overlay_trade_audit.csv", index=False)
    put_trades.to_csv(OUTPUT / "put_trade_audit.csv", index=False)
    schedules.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    exposure.to_csv(OUTPUT / "exposure_cost_delta.csv", index=False)
    overlay_cycles.to_csv(OUTPUT / "overlay_cycle_summary.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    record = build_record(metrics_table, pairwise, decisions, exposure, summary)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    command = "uv run ic_valuation_overlay_put_sync_v1.py"
    (OUTPUT / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_sha256": sha256(Path(__file__)),
        "spec_sha256": SPEC_SHA256,
        "input_hashes": {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()},
        "upstream_verification": upstream,
        "market_checks": market_checks,
        "sample": {
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "candidate_grid": {
            "low_thresholds": list(LOW_THRESHOLDS),
            "high_thresholds": list(HIGH_THRESHOLDS),
            "put_modes": list(PUT_MODES),
            "primary_pair": list(PRIMARY_PAIR),
        },
        "execution": {
            "valuation_signal": "T close",
            "overlay_ic": "T+1 active IC official open",
            "overlay_ic_roll": "old and next natural-month official settlement",
            "put": "T+1 common close; 3m 95%; monthly replace",
        },
        "capital_and_cost": {
            "core_ic_units": 1.0,
            "overlay_ic_units": 1.0,
            "margin_buffer_per_ic_unit": MARGIN_RATE,
            "cash_annual": 0.03,
            "ic_and_put_side_cost": ONE_WAY_COST,
        },
        "decision": summary,
        "integrity": integrity,
        "warnings": [
            "No independent OOS",
            "Model Put is theoretical",
            "Real Put sample starts 2022-09-19",
            "Official daily open/close are not guaranteed fills or capacity evidence",
            "Normalized 1x IC notional; not an integer-contract account",
            "Research state is not an order",
        ],
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    output_hashes = {
        path.name: sha256(path)
        for path in sorted(OUTPUT.iterdir())
        if path.name != "output_manifest.json"
    }
    (OUTPUT / "output_manifest.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    model_metrics = metrics_table[metrics_table["layer"].eq("model")].copy()
    model_metrics = model_metrics.rename(columns={"window": "segment"})
    model_metrics.to_csv(SCAN / "scan_summary.csv", index=False)
    wide[wide["layer"].eq("model")].to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "preregistered_grid",
            "baseline": {"candidate": "model__base_core_put", "same_run": True},
            "candidate_grid": manifest["candidate_grid"],
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["capital_and_cost"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": integrity["mainline_parity_max_abs"],
            "formal_output": str(OUTPUT.relative_to(ROOT)),
            "decision": summary["decision"],
            "stability_label": summary["stability_label"],
            "outputs": {
                "record": str((SCAN / "record.md").resolve()),
                "scan_summary": str((SCAN / "scan_summary.csv").resolve()),
                "window_metrics": str((SCAN / "window_metrics.csv").resolve()),
                "scan_meta": str(meta_path.resolve()),
                "command_log": str((SCAN / "command_log.txt").resolve()),
            },
            "git_status_before": git_before,
            "git_status_after": git_status(),
            "warnings": manifest["warnings"],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    git_before = git_status()
    upstream = verify_inputs()
    frames, _, market, market_checks = v21.v20.v19.v18.load_close_inputs()
    chain, chain_audit = load_active_chain(frames)
    frozen_daily = pd.read_csv(V21_DAILY, parse_dates=["date"])
    frozen_schedule = pd.read_csv(
        V21_SCHEDULE, parse_dates=["eval_date", "execution_date"]
    )
    frozen_trades = pd.read_csv(
        V21_TRADES,
        parse_dates=[
            "signal_eval_date",
            "scheduled_execution_date",
            "actual_execution_date",
            "roll_request_date",
        ],
    )

    overlay_paths: dict[tuple[float, float], pd.DataFrame] = {}
    overlay_trade_paths: dict[tuple[float, float], pd.DataFrame] = {}
    cycle_rows: list[dict[str, Any]] = []
    for low in LOW_THRESHOLDS:
        for high in HIGH_THRESHOLDS:
            daily_overlay, trades_overlay, cycle = simulate_overlay(chain, low, high)
            overlay_paths[(low, high)] = daily_overlay
            overlay_trade_paths[(low, high)] = trades_overlay
            cycle_rows.append(cycle)
    flat = flat_overlay(chain)

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    put_trade_parts: list[pd.DataFrame] = []
    overlay_trade_parts: list[pd.DataFrame] = []
    for layer, start in (("model", MODEL_START), ("real", REAL_START)):
        base_put = mainline_put_rows(frozen_daily, layer)
        base_candidate = f"{layer}__base_core_put"
        base_daily = assemble_candidate(
            chain, flat, base_put, layer, base_candidate, "base_core_put", None, None
        )
        daily_parts.append(base_daily)
        base_schedule = build_candidate_schedule(
            frozen_schedule, flat, layer, base_candidate, "core_put_only", None, None
        )
        base_schedule["put_mode"] = "base_core_put"
        schedule_parts.append(base_schedule)
        source_base_trade = frozen_trades[
            frozen_trades["candidate"].eq(f"{layer}_l190_mom25")
        ].copy()
        source_base_trade["candidate"] = base_candidate
        source_base_trade["layer"] = layer
        source_base_trade["pair"] = "base"
        source_base_trade["put_mode"] = "base_core_put"
        source_base_trade["source"] = "frozen_v21_core"
        put_trade_parts.append(source_base_trade)

        for low in LOW_THRESHOLDS:
            for high in HIGH_THRESHOLDS:
                overlay = overlay_paths[(low, high)]
                raw_overlay_trades = overlay_trade_paths[(low, high)]
                core_candidate = candidate_label(layer, low, high, "core_put_only")
                core_daily = assemble_candidate(
                    chain,
                    overlay,
                    base_put,
                    layer,
                    core_candidate,
                    "core_put_only",
                    low,
                    high,
                )
                daily_parts.append(core_daily)
                core_schedule = build_candidate_schedule(
                    frozen_schedule,
                    overlay,
                    layer,
                    core_candidate,
                    "core_put_only",
                    low,
                    high,
                )
                schedule_parts.append(core_schedule)
                core_put_trades = source_base_trade.copy()
                core_put_trades["candidate"] = core_candidate
                core_put_trades["pair"] = pair_label(low, high)
                core_put_trades["put_mode"] = "core_put_only"
                put_trade_parts.append(core_put_trades)

                sync_candidate = candidate_label(layer, low, high, "sync_put_total_ic")
                sync_schedule = build_candidate_schedule(
                    frozen_schedule,
                    overlay,
                    layer,
                    sync_candidate,
                    "sync_put_total_ic",
                    low,
                    high,
                )
                sync_put, sync_trades = run_sync_put(
                    frames, market, sync_schedule, layer, sync_candidate
                )
                sync_daily = assemble_candidate(
                    chain,
                    overlay,
                    sync_put,
                    layer,
                    sync_candidate,
                    "sync_put_total_ic",
                    low,
                    high,
                )
                daily_parts.append(sync_daily)
                schedule_parts.append(sync_schedule)
                sync_trades["layer"] = layer
                sync_trades["pair"] = pair_label(low, high)
                sync_trades["put_mode"] = "sync_put_total_ic"
                sync_trades["source"] = "rerun_scaled_total_ic_delta"
                put_trade_parts.append(sync_trades)

                for candidate, mode in (
                    (core_candidate, "core_put_only"),
                    (sync_candidate, "sync_put_total_ic"),
                ):
                    trades_copy = raw_overlay_trades[
                        raw_overlay_trades["execution_date"] >= start
                    ].copy()
                    trades_copy["candidate"] = candidate
                    trades_copy["layer"] = layer
                    trades_copy["put_mode"] = mode
                    overlay_trade_parts.append(trades_copy)

    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    ).reset_index(drop=True)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "execution_date"]
    ).reset_index(drop=True)
    put_trades = pd.concat(put_trade_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "actual_execution_date"]
    ).reset_index(drop=True)
    overlay_trades = pd.concat(overlay_trade_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "execution_date"]
    ).reset_index(drop=True)
    cycles = pd.DataFrame(cycle_rows)

    metrics_table, wide = build_metrics(daily)
    pairwise = pairwise_put_management(metrics_table)
    annual = annual_metrics(daily)
    exposure = exposure_diagnostics(daily, overlay_trades, put_trades)
    decisions, summary = decide(pairwise, exposure)
    integrity = check_integrity(
        daily,
        schedules,
        overlay_trades,
        put_trades,
        frozen_daily,
        chain_audit,
        exposure,
    )
    write_outputs(
        daily,
        metrics_table,
        wide,
        pairwise,
        annual,
        overlay_trades,
        put_trades,
        schedules,
        exposure,
        cycles,
        decisions,
        summary,
        integrity,
        upstream,
        market_checks,
        git_before,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
