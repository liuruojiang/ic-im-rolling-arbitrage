#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Preregistered 1.80-start valuation ladder observation for IC Put protection."""

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
VERSION = "ic_510500_put_ladder180_observation_v22"
OUTPUT = ROOT / "outputs" / VERSION
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "b7a00c54ffc8c499f12ca2e4c3fde51d72f5188c4be4ea907695eec06eea5bba"
SCAN = ROOT / "quant_param_scan_runs" / "20260818_ic_ladder180_observation_v22"

V21_OUTPUT = ROOT / "outputs" / "ic_510500_put_mom120_delta_floor_v21"
V21_DAILY = V21_OUTPUT / "daily_candidates.csv.gz"
V21_SCHEDULE = V21_OUTPUT / "evaluation_schedule.csv.gz"
V21_TRADES = V21_OUTPUT / "trade_audit.csv"
V21_OUTPUT_MANIFEST = V21_OUTPUT / "output_manifest.json"
V21_DATA_MANIFEST = V21_OUTPUT / "data_manifest.json"

MODEL_START = v21.MODEL_START
REAL_START = v21.REAL_START
END = v21.END
WINDOWS = v21.WINDOWS
MONEYNESS = v21.MONEYNESS
PUT_SIDE_COST = v21.PUT_SIDE_COST

VARIANTS = (
    "no_put",
    "current_fixed1",
    "c200_mom25",
    "l190_mom25",
    "l180_mom25",
)
DELTA_VARIANTS = {"c200_mom25", "l190_mom25", "l180_mom25"}
LADDERS = {
    "l180": (1.80, 1.90, 2.00),
    "l190": (1.90, 2.00, 2.10),
    "c200": (2.00, 2.10, 2.15),
}

INPUT_HASHES = {
    ROOT / "ic_510500_put_mom120_delta_floor_v21.py": (
        "e43a80085d3030d8ec87a6c89ad3be73331cf83f18226a9c88dfe7ea2299106e"
    ),
    ROOT / "docs" / "ic_510500_put_mom120_delta_floor_v21_spec.md": (
        "a928a8f8b6d03d42cb4156c861653974aaccaae1953d9bbd23153f2e4e28c329"
    ),
    V21_OUTPUT_MANIFEST: (
        "0d7fa231586d31aa0d0c093f4ca5624ae8fb6dd43c7bb794ae5b2310d699cef6"
    ),
    V21_DATA_MANIFEST: (
        "50b1218a91372059f5d687f63e78959e75d4c6b89be19350fd9c876b0bbe358e"
    ),
    V21_DAILY: "11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a",
    V21_SCHEDULE: "dba99b2aa67a52c9b17a25e03e89325207aae6614bc651052b99168575a38d7a",
    V21_TRADES: "fb692bb0388018680891027ef3328c7b99abab86e9cac4f0a8b61d8e5437c22e",
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


def variant_parameters(variant: str) -> dict[str, Any]:
    if variant == "no_put":
        return {
            "signal_shape": "baseline",
            "sizing_method": "none",
            "valuation_ladder": "none",
            "mom_delta_floor": 0.0,
        }
    if variant == "current_fixed1":
        return {
            "signal_shape": "binary_current_control",
            "sizing_method": "notional",
            "valuation_ladder": "c200",
            "mom_delta_floor": math.nan,
        }
    ladder = variant.split("_", 1)[0]
    return {
        "signal_shape": "valuation_plus_momentum",
        "sizing_method": "delta",
        "valuation_ladder": ladder,
        "mom_delta_floor": 0.25,
    }


def candidate_parts(candidate: str) -> dict[str, Any]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "variant": variant, **variant_parameters(variant)}


def valuation_tier(score: float, ladder: str) -> int:
    low, middle, high = LADDERS[ladder]
    if score + 1e-12 >= high:
        return 3
    if score + 1e-12 >= middle:
        return 2
    if score + 1e-12 >= low:
        return 1
    return 0


def targets_for_variant(
    variant: str, score: float, momentum_120: float
) -> tuple[int, int, float, float]:
    ladder = str(variant_parameters(variant)["valuation_ladder"])
    value_tier = valuation_tier(score, ladder)
    momentum_on = momentum_120 <= 1e-12
    if variant == "current_fixed1":
        risk_tier = max(value_tier, int(momentum_on))
        return value_tier, risk_tier, float(risk_tier > 0), math.nan
    delta_target = max(0.25 * value_tier, 0.25 * int(momentum_on))
    risk_tier = round(delta_target / 0.25)
    return value_tier, risk_tier, math.nan, delta_target


def formula_self_tests() -> None:
    assert valuation_tier(1.799999, "l180") == 0
    assert valuation_tier(1.80, "l180") == 1
    assert valuation_tier(1.90, "l180") == 2
    assert valuation_tier(2.00, "l180") == 3
    assert targets_for_variant("l180_mom25", 1.85, 0.01)[3] == 0.25
    assert targets_for_variant("l180_mom25", 1.95, 0.01)[3] == 0.50
    assert targets_for_variant("l180_mom25", 2.05, 0.01)[3] == 0.75
    assert targets_for_variant("l180_mom25", -9.0, -0.01)[3] == 0.25
    assert targets_for_variant("l190_mom25", 1.85, 0.01)[3] == 0.0


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v22 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v22 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v22 input changed: {path.relative_to(ROOT)}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    upstream_manifest = json.loads(V21_OUTPUT_MANIFEST.read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in upstream_manifest.items():
        path = V21_OUTPUT / name
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append({"file": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"v21 output manifest mismatch: {mismatches}")
    return {
        "v21_output_manifest_files": len(upstream_manifest),
        "v21_output_manifest_match": True,
    }


def build_schedules(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    signal_inputs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v21.v20.v19.v18.v13.proxy
    frame = signal_inputs.set_index("date")
    trade_dates = pd.DatetimeIndex(ic["date"])
    evaluations = {
        "model": proxy.evaluation_dates(
            "daily", MODEL_START, END, trade_dates, daily_valuation
        ),
        "real": proxy.evaluation_dates(
            "daily", REAL_START, END, trade_dates, daily_valuation
        ),
    }
    signal_rows: list[dict[str, Any]] = []
    schedule_rows: list[dict[str, Any]] = []
    for layer, dates in evaluations.items():
        start = MODEL_START if layer == "model" else REAL_START
        for variant in VARIANTS:
            if variant == "no_put":
                continue
            params = variant_parameters(variant)
            for sequence, day in enumerate(dates):
                row = frame.loc[day]
                score = float(row["unbounded_median_knot"])
                momentum = float(row["momentum_120"])
                value_tier, risk_tier, notional_target, delta_target = (
                    targets_for_variant(variant, score, momentum)
                )
                execution, initial = proxy.next_execution(day, start, trade_dates)
                common = {
                    "layer": layer,
                    "signal_variant": variant,
                    "sequence": sequence,
                    "eval_date": day,
                    "risk_tier": risk_tier,
                    "valuation_tier": value_tier,
                    "valuation_ladder": params["valuation_ladder"],
                    "mom_delta_floor": params["mom_delta_floor"],
                    "momentum_floor_on": bool(momentum <= 1e-12),
                    "target_notional_fraction": notional_target,
                    "target_delta": delta_target,
                    "unbounded_median_knot": score,
                    "momentum_120": momentum,
                    "old_fixed_risk": float(row["old_fixed_risk"]),
                    "pe_aggregate_ttm": float(row["pe_aggregate_ttm"]),
                    "pb_aggregate": float(row["pb_aggregate"]),
                    "erp": float(row["erp"]),
                }
                signal_rows.append(common)
                target = (
                    notional_target if np.isfinite(notional_target) else delta_target
                )
                schedule_rows.append(
                    {
                        **common,
                        "frequency": "daily",
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": target,
                        "three_tier_target_fraction": target,
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    )
    signals = pd.DataFrame(signal_rows).sort_values(
        ["layer", "signal_variant", "eval_date"]
    )
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v22 execution schedule row")
    return schedule.reset_index(drop=True), signals.reset_index(drop=True)


def run_candidates(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v21.v20.v19.v18.v13.proxy
    roll_dates = v21.v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    daily_parts = [
        proxy.no_put_rows(frames["ic"], MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    for layer in ("model", "real"):
        for variant in VARIANTS:
            if variant == "no_put":
                continue
            candidate_schedule = schedule[
                schedule["layer"].eq(layer)
                & schedule["signal_variant"].eq(variant)
            ].copy()
            label = f"{layer}_{variant}"
            if variant == "current_fixed1":
                if layer == "model":
                    overlay, trades, _ = v21.v20.v19.v18.v11.run_model_tool(
                        frames,
                        market,
                        candidate_schedule,
                        v21.v20.v19.EXECUTION_STRUCTURE,
                        MONEYNESS,
                        label,
                        roll_dates,
                    )
                else:
                    overlay, trades, _ = v21.v20.v19.v18.v11.run_real_tool(
                        frames,
                        candidate_schedule,
                        v21.v20.v19.EXECUTION_STRUCTURE,
                        MONEYNESS,
                        label,
                        roll_dates,
                    )
                overlay = v21.v20._attach_risk_tier(overlay, candidate_schedule)
                overlay = v21.v20.decorate_fixed_overlay(overlay, layer, frames, market)
                trades = v21.v20.normalize_fixed_trades(trades, candidate_schedule)
            elif layer == "model":
                overlay, trades = v21.v20.run_model_delta(
                    frames["ic"], candidate_schedule, market, label, roll_dates
                )
            else:
                overlay, trades = v21.v20.run_real_delta(
                    frames["ic"],
                    candidate_schedule,
                    frames,
                    market,
                    label,
                    roll_dates,
                )
            daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
            if not trades.empty:
                trade_parts.append(trades)
    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    )
    for column, default in [
        ("signal_target_fraction", 0.0),
        ("target_delta", np.nan),
        ("risk_tier", 0),
        ("actual_notional_fraction", 0.0),
        ("abs_put_delta", np.nan),
        ("effective_delta_hedge_ratio", 0.0),
        ("delta_source", ""),
    ]:
        if column not in daily:
            daily[column] = default
        if not pd.isna(default):
            daily[column] = daily[column].fillna(default)
    daily["cash_nav"] = daily.groupby("candidate", sort=False)["cash_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    daily["cash_drawdown"] = daily.groupby("candidate", sort=False)[
        "cash_nav"
    ].transform(lambda values: values / values.cummax() - 1.0)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    return daily.reset_index(drop=True), trades.reset_index(drop=True)


def baseline_parity(
    daily: pd.DataFrame, schedule: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    frozen_daily = pd.read_csv(V21_DAILY, parse_dates=["date"])
    mappings = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        "model_current_fixed1": "model_current_fixed1",
        "real_current_fixed1": "real_current_fixed1",
        "model_c200_mom25": "model_c200_mom25",
        "real_c200_mom25": "real_c200_mom25",
        "model_l190_mom25": "model_l190_mom25",
        "real_l190_mom25": "real_l190_mom25",
    }
    columns = [
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "target_fraction",
        "ret",
        "cash_ret",
    ]
    rows: list[dict[str, Any]] = []
    for current, prior in mappings.items():
        left = daily[daily["candidate"].eq(current)][["date", *columns]]
        right = frozen_daily[frozen_daily["candidate"].eq(prior)][["date", *columns]]
        joined = left.merge(
            right, on="date", suffixes=("_v22", "_v21"), validate="one_to_one"
        )
        row: dict[str, Any] = {
            "check_type": "daily",
            "current_candidate": current,
            "prior_candidate": prior,
            "rows": len(joined),
        }
        for column in columns:
            row[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v22"] - joined[f"{column}_v21"]).abs().max()
            )
        rows.append(row)

    frozen_schedule = pd.read_csv(V21_SCHEDULE, parse_dates=["execution_date"])
    for layer in ("model", "real"):
        for variant in ("current_fixed1", "c200_mom25", "l190_mom25"):
            left = schedule[
                schedule["layer"].eq(layer)
                & schedule["signal_variant"].eq(variant)
            ][["execution_date", "risk_tier", "three_tier_target_fraction"]]
            right = frozen_schedule[
                frozen_schedule["layer"].eq(layer)
                & frozen_schedule["signal_variant"].eq(variant)
            ][["execution_date", "risk_tier", "three_tier_target_fraction"]]
            joined = left.merge(
                right,
                on="execution_date",
                suffixes=("_v22", "_v21"),
                validate="one_to_one",
            )
            rows.append(
                {
                    "check_type": "schedule",
                    "current_candidate": f"{layer}_{variant}",
                    "prior_candidate": f"{layer}_{variant}",
                    "rows": len(joined),
                    "max_abs_risk_tier_diff": float(
                        (joined["risk_tier_v22"] - joined["risk_tier_v21"]).abs().max()
                    ),
                    "max_abs_target_fraction_diff": float(
                        (
                            joined["three_tier_target_fraction_v22"]
                            - joined["three_tier_target_fraction_v21"]
                        ).abs().max()
                    ),
                }
            )

    frozen_trades = pd.read_csv(
        V21_TRADES, parse_dates=["actual_execution_date"]
    )
    numeric = [
        "target_fraction",
        "target_delta",
        "target_delta_error",
        "entry_abs_delta",
        "new_notional_fraction",
    ]
    for layer in ("model", "real"):
        for variant in ("current_fixed1", "c200_mom25", "l190_mom25"):
            candidate = f"{layer}_{variant}"
            left = trades[trades["candidate"].eq(candidate)].copy()
            right = frozen_trades[frozen_trades["candidate"].eq(candidate)].copy()
            key = ["actual_execution_date", "action"]
            joined = left[key + numeric].merge(
                right[key + numeric],
                on=key,
                suffixes=("_v22", "_v21"),
                validate="one_to_one",
            )
            row = {
                "check_type": "trade",
                "current_candidate": candidate,
                "prior_candidate": candidate,
                "rows": len(joined),
                "max_abs_trade_count_diff": float(abs(len(left) - len(right))),
            }
            for column in numeric:
                row[f"max_abs_{column}_diff"] = float(
                    (
                        joined[f"{column}_v22"].fillna(0.0)
                        - joined[f"{column}_v21"].fillna(0.0)
                    ).abs().max()
                )
            rows.append(row)
    table = pd.DataFrame(rows)
    numeric_columns = [column for column in table if column.startswith("max_abs_")]
    if table[numeric_columns].fillna(0.0).to_numpy().max() > 1e-14:
        raise RuntimeError("v22/v21 baseline parity failed")
    return table


def metrics(returns: pd.Series) -> dict[str, float]:
    return v21.metrics(returns)


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else END - offset
            available = bool(offset is None or group["date"].min() <= requested)
            subset = group if offset is None else group[group["date"] >= requested]
            row: dict[str, Any] = {
                "candidate": candidate,
                **candidate_parts(candidate),
                "window": window,
                "available": available,
                "requested_start": requested,
                "actual_start": subset["date"].min() if available else pd.NaT,
                "end": subset["date"].max() if available else pd.NaT,
                "rows": len(subset) if available else 0,
            }
            row.update(
                metrics(subset["cash_ret"])
                if available
                else {
                    "total_return": np.nan,
                    "ann_return": np.nan,
                    "ann_vol": np.nan,
                    "sharpe_repo": np.nan,
                    "max_dd": np.nan,
                }
            )
            rows.append(row)
    table = pd.DataFrame(rows)
    baseline = table[table["variant"].eq("l190_mom25")][
        ["layer", "window", "ann_return", "max_dd"]
    ].rename(
        columns={
            "ann_return": "l190_ann_return",
            "max_dd": "l190_max_dd",
        }
    )
    table = table.merge(baseline, on=["layer", "window"], validate="many_to_one")
    table["ann_return_delta_vs_l190"] = (
        table["ann_return"] - table["l190_ann_return"]
    )
    table["max_dd_improvement_vs_l190"] = (
        table["max_dd"] - table["l190_max_dd"]
    )
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in table.groupby("candidate", sort=True):
        row = {"candidate": candidate, **candidate_parts(candidate)}
        for metric_row in group.itertuples(index=False):
            for field in ["ann_return", "ann_vol", "sharpe_repo", "max_dd"]:
                row[f"{field}_{metric_row.window}"] = getattr(metric_row, field)
        wide_rows.append(row)
    return table, pd.DataFrame(wide_rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (candidate, year), group in daily.groupby(
        ["candidate", daily["date"].dt.year], sort=True
    ):
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "year": int(year),
                "rows": len(group),
                **metrics(group.sort_values("date")["cash_ret"]),
            }
        )
    return pd.DataFrame(rows)


def annual_attribution(annual: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for layer in ("model", "real"):
        for candidate, control in [
            ("l180_mom25", "l190_mom25"),
            ("l190_mom25", "c200_mom25"),
        ]:
            left = annual[
                annual["layer"].eq(layer) & annual["variant"].eq(candidate)
            ].set_index("year")
            right = annual[
                annual["layer"].eq(layer) & annual["variant"].eq(control)
            ].set_index("year")
            for year in left.index.intersection(right.index):
                rows.append(
                    {
                        "layer": layer,
                        "candidate": candidate,
                        "control": control,
                        "year": int(year),
                        "candidate_ann_return": float(left.loc[year, "ann_return"]),
                        "control_ann_return": float(right.loc[year, "ann_return"]),
                        "ann_return_delta": float(
                            left.loc[year, "ann_return"] - right.loc[year, "ann_return"]
                        ),
                        "candidate_max_dd": float(left.loc[year, "max_dd"]),
                        "control_max_dd": float(right.loc[year, "max_dd"]),
                        "max_dd_improvement": float(
                            left.loc[year, "max_dd"] - right.loc[year, "max_dd"]
                        ),
                    }
                )
    return pd.DataFrame(rows)


def pairwise_metrics(metric_table: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for layer in ("model", "real"):
        for candidate, control in [
            ("l180_mom25", "l190_mom25"),
            ("l190_mom25", "c200_mom25"),
        ]:
            left = metric_table[
                metric_table["layer"].eq(layer)
                & metric_table["variant"].eq(candidate)
            ].set_index("window")
            right = metric_table[
                metric_table["layer"].eq(layer)
                & metric_table["variant"].eq(control)
            ].set_index("window")
            for window in WINDOWS:
                if not bool(left.loc[window, "available"]):
                    continue
                rows.append(
                    {
                        "layer": layer,
                        "candidate": candidate,
                        "control": control,
                        "window": window,
                        "candidate_ann_return": float(left.loc[window, "ann_return"]),
                        "control_ann_return": float(right.loc[window, "ann_return"]),
                        "ann_return_delta": float(
                            left.loc[window, "ann_return"]
                            - right.loc[window, "ann_return"]
                        ),
                        "candidate_max_dd": float(left.loc[window, "max_dd"]),
                        "control_max_dd": float(right.loc[window, "max_dd"]),
                        "max_dd_improvement": float(
                            left.loc[window, "max_dd"] - right.loc[window, "max_dd"]
                        ),
                    }
                )
    return pd.DataFrame(rows)


def ladder_activity_difference(schedule: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for layer in ("model", "real"):
        left = schedule[
            schedule["layer"].eq(layer)
            & schedule["signal_variant"].eq("l180_mom25")
        ][["eval_date", "execution_date", "target_delta"]].rename(
            columns={"target_delta": "l180_target_delta"}
        )
        right = schedule[
            schedule["layer"].eq(layer)
            & schedule["signal_variant"].eq("l190_mom25")
        ][["eval_date", "execution_date", "target_delta"]].rename(
            columns={"target_delta": "l190_target_delta"}
        )
        joined = left.merge(
            right,
            on=["eval_date", "execution_date"],
            validate="one_to_one",
        )
        joined["year"] = joined["eval_date"].dt.year
        joined["new_protection"] = joined["l180_target_delta"].gt(0) & joined[
            "l190_target_delta"
        ].eq(0)
        joined["intensity_upgrade"] = joined["l180_target_delta"].gt(
            joined["l190_target_delta"]
        ) & joined["l190_target_delta"].gt(0)
        joined["target_changed"] = joined["l180_target_delta"].ne(
            joined["l190_target_delta"]
        )
        for year, group in joined.groupby("year", sort=True):
            rows.append(
                {
                    "layer": layer,
                    "year": int(year),
                    "evaluation_days": len(group),
                    "new_protection_days": int(group["new_protection"].sum()),
                    "intensity_upgrade_days": int(group["intensity_upgrade"].sum()),
                    "target_changed_days": int(group["target_changed"].sum()),
                    "average_delta_increment": float(
                        (group["l180_target_delta"] - group["l190_target_delta"]).mean()
                    ),
                    "max_delta_increment": float(
                        (group["l180_target_delta"] - group["l190_target_delta"]).max()
                    ),
                }
            )
    return pd.DataFrame(rows)


def exposure_diagnostics(
    daily: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        trade = trades[trades["candidate"].eq(candidate)]
        active = group[group["put_qty"].astype(float).gt(0)]
        trade_dates = pd.DatetimeIndex(trade["actual_execution_date"].dropna().unique())
        post_trade = active[active["date"].isin(trade_dates)]
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "protected_days": len(active),
                "protected_day_ratio": float(len(active) / len(group)),
                "trade_events": len(trade),
                "resize_events": int(trade["action"].eq("close_resize").sum()),
                "monthly_roll_events": int(
                    trade["action"].eq("close_roll_monthly").sum()
                ),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "max_post_trade_put_mark_fraction": float(
                    post_trade["put_mark_fraction"].max()
                )
                if len(post_trade)
                else 0.0,
                "max_actual_notional_fraction": float(
                    group["actual_notional_fraction"].max()
                ),
                "max_effective_delta_hedge_ratio": float(
                    group["effective_delta_hedge_ratio"].max()
                ),
                "days_effective_delta_over_100pct": int(
                    group["effective_delta_hedge_ratio"].gt(1.0).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def delta_trade_diagnostics(trades: pd.DataFrame) -> pd.DataFrame:
    selected = trades[trades["target_delta"].fillna(0).gt(0)].copy()
    columns = [
        "candidate",
        "actual_execution_date",
        "action",
        "risk_tier",
        "target_delta",
        "entry_abs_delta",
        "target_delta_error",
        "old_notional_fraction",
        "new_notional_fraction",
        "delta_source",
        "new_contract",
        "new_strike",
        "new_entry_moneyness",
    ]
    return selected[columns].reset_index(drop=True)


def observation_decision(
    pairwise: pd.DataFrame,
    annual_diff: pd.DataFrame,
    exposure: pd.DataFrame,
    delta_trades: pd.DataFrame,
    daily: pd.DataFrame,
) -> dict[str, Any]:
    layer_checks: dict[str, Any] = {}
    full_dominance = True
    tolerance_pass_all = True
    for layer in ("model", "real"):
        part = pairwise[
            pairwise["layer"].eq(layer)
            & pairwise["candidate"].eq("l180_mom25")
            & pairwise["control"].eq("l190_mom25")
        ].set_index("window")
        windows = list(WINDOWS) if layer == "model" else ["full", "last_3y", "last_1y"]
        full_return = float(part.loc["full", "ann_return_delta"]) >= -1e-12
        full_dd = float(part.loc["full", "max_dd_improvement"]) >= -1e-12
        dd_floor = all(
            float(part.loc[window, "max_dd_improvement"]) >= -0.01 - 1e-12
            for window in windows
        )
        return_floor = all(
            float(part.loc[window, "ann_return_delta"])
            >= -(0.01 if window in {"full", "last_10y", "last_5y"} else 0.03)
            - 1e-12
            for window in windows
        )
        exp = exposure[
            exposure["layer"].eq(layer)
            & exposure["variant"].eq("l180_mom25")
        ].iloc[0]
        capital = bool(
            float(exp["max_post_trade_put_mark_fraction"]) <= 0.70 + 1e-12
        )
        trade = delta_trades[
            delta_trades["candidate"].eq(f"{layer}_l180_mom25")
        ]
        delta_error = bool(
            len(trade)
            and float(trade["target_delta_error"].max())
            <= (1e-12 if layer == "model" else 0.02) + 1e-12
        )
        fallback_ratio = float(
            trade["delta_source"].eq("qvix_proxy_fallback").mean()
        )
        fallback = bool(layer == "model" or fallback_ratio <= 0.05 + 1e-12)
        returns_valid = bool(
            daily[daily["candidate"].eq(f"{layer}_l180_mom25")]["cash_ret"].min()
            > -1.0
        )
        full_dominance &= full_return and full_dd
        tolerance_pass_all &= (
            dd_floor and return_floor and capital and delta_error and fallback and returns_valid
        )
        layer_checks[layer] = {
            "full_return_noninferior": full_return,
            "full_dd_noninferior": full_dd,
            "window_dd_floor_pass": dd_floor,
            "window_return_floor_pass": return_floor,
            "capital_pass": capital,
            "delta_error_pass": delta_error,
            "iv_fallback_ratio": fallback_ratio,
            "iv_fallback_pass": fallback,
            "returns_valid": returns_valid,
            "full_ann_return_delta": float(part.loc["full", "ann_return_delta"]),
            "full_max_dd_improvement": float(
                part.loc["full", "max_dd_improvement"]
            ),
        }
    changed = annual_diff[
        annual_diff["candidate"].eq("l180_mom25")
        & annual_diff["ann_return_delta"].abs().gt(1e-12)
    ]
    changed_years = {
        layer: sorted(changed.loc[changed["layer"].eq(layer), "year"].astype(int).tolist())
        for layer in ("model", "real")
    }
    multi_episode = len(changed_years["model"]) >= 2 and len(changed_years["real"]) >= 2
    if full_dominance and tolerance_pass_all:
        decision = "return_heavy_observation"
    else:
        decision = "reject_observation"
    stability = "multi_episode_support" if multi_episode else "event_driven"
    return {
        "decision": decision,
        "stability_label": stability,
        "candidate": "l180_mom25",
        "control": "l190_mom25",
        "layer_checks": layer_checks,
        "changed_years": changed_years,
        "selected_variant": None,
        "structural_platform_status": "below_v6_common_lower_bound_1p85",
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "sample_reuse": "not_independent_oos",
    }


def check_signal_formula(schedule: pd.DataFrame) -> dict[str, Any]:
    max_error = 0.0
    for row in schedule.itertuples(index=False):
        value_tier, risk_tier, notional, delta = targets_for_variant(
            row.signal_variant,
            float(row.unbounded_median_knot),
            float(row.momentum_120),
        )
        max_error = max(
            max_error,
            abs(value_tier - int(row.valuation_tier)),
            abs(risk_tier - int(row.risk_tier)),
        )
        if np.isfinite(notional):
            max_error = max(
                max_error, abs(notional - float(row.target_notional_fraction))
            )
        if np.isfinite(delta):
            max_error = max(max_error, abs(delta - float(row.target_delta)))
    if max_error > 1e-14:
        raise RuntimeError("v22 signal formula identity failed")
    return {"max_formula_error": max_error, "all_checks_passed": True}


def check_integrity(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    delta_trades: pd.DataFrame,
    signal_checks: dict[str, Any],
) -> dict[str, Any]:
    expected = {
        f"{layer}_{variant}" for layer in ("model", "real") for variant in VARIANTS
    }
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v22 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v22 candidate/date")
    if daily[["ret", "cash_ret"]].isna().any().any():
        raise RuntimeError("Missing v22 return")
    if (daily[["ret", "cash_ret"]] <= -1.0).any().any():
        raise RuntimeError("Invalid v22 return <= -100%")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes request")
    max_delay = int(trades["delay_trading_days"].fillna(0).max())
    if max_delay > 5:
        raise RuntimeError("Execution delay exceeded five trading days")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Signal execution leakage")
    parity_columns = [column for column in parity if column.startswith("max_abs_")]
    parity_max = float(parity[parity_columns].fillna(0.0).to_numpy().max())
    if parity_max > 1e-14:
        raise RuntimeError("Baseline parity exceeded tolerance")
    if contract_audit.empty or not contract_audit["nearest_contract_match"].all():
        raise RuntimeError("Contract selection audit failed")
    if close_audit.empty or not close_audit["passed"].all():
        raise RuntimeError("Close execution audit failed")
    model_delta = delta_trades[delta_trades["candidate"].str.startswith("model_")]
    real_delta = delta_trades[delta_trades["candidate"].str.startswith("real_")]
    model_error = float(model_delta["target_delta_error"].max())
    real_error = float(real_delta["target_delta_error"].max())
    if model_error > 1e-12:
        raise RuntimeError("Model Delta sizing identity failed")
    if real_error > 0.02 + 1e-12:
        raise RuntimeError("Real Delta integer-sizing tolerance failed")
    return {
        "candidate_count": len(expected),
        "daily_rows": len(daily),
        "trade_rows": len(trades),
        "schedule_rows": len(schedule),
        "parity_max_abs": parity_max,
        "max_execution_delay_trading_days": max_delay,
        "model_max_target_delta_error": model_error,
        "real_max_target_delta_error": real_error,
        "real_delta_iv_fallback_ratio": float(
            real_delta["delta_source"].eq("qvix_proxy_fallback").mean()
        ),
        "max_put_mark_fraction": float(daily["put_mark_fraction"].max()),
        "max_actual_notional_fraction": float(
            daily["actual_notional_fraction"].max()
        ),
        "max_effective_delta_hedge_ratio": float(
            daily["effective_delta_hedge_ratio"].max()
        ),
        "signal_formula": signal_checks,
        "all_checks_passed": True,
    }


def _fmt(value: Any) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def build_record(
    metric_table: pd.DataFrame,
    pairwise: pd.DataFrame,
    annual_diff: pd.DataFrame,
    activity_diff: pd.DataFrame,
    exposure: pd.DataFrame,
    decision: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    full = metric_table[metric_table["available"]][
        ["layer", "variant", "window", "ann_return", "max_dd"]
    ].copy()
    full["ann_return"] = full["ann_return"].map(_fmt)
    full["max_dd"] = full["max_dd"].map(_fmt)
    return f"""# Run Metadata

- Version: `{VERSION}`
- Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}
- Status: research observation only; not live approved.
- Dirty-worktree caveat: pre-existing untracked `README.md` and `notes/` were not strategy inputs.

# Research Question

Does a 1.80/1.90/2.00 valuation ladder improve the carried 1.90/2.00/2.10 ladder when MOM120 keeps a 25% transaction-time Delta floor?

# Implementation Anchor

- Entrypoint: `ic_510500_put_ladder180_observation_v22.py`
- Frozen spec: `docs/ic_510500_put_ladder180_observation_v22_spec.md`
- Upstream: v21; baseline parity max absolute error `{integrity['parity_max_abs']:.3e}`.
- Source-change rule: any change to signal, sizing, execution, data, costs, or risk logic requires a new version and new output directory.

# Data Snapshot

- Model: 2015-04-16 through 2026-08-14.
- Real 510500 ETF Put: 2022-09-19 through 2026-08-14.
- Causal valuation/TRI warm-up: from 2007-01-15.

# Cost and Execution Assumptions

- One IC notional; 30% margin/buffer; 70% cash before Put premium; 3% cash yield.
- Three-month target 95% Put, monthly roll; T-close evaluation and next common trading-day close execution.
- IC and Put side cost 1 bp; Delta resized at entry, monthly roll, or target change only.

# Runtime Override Plan

No runtime parameter override or cache mutation. Candidate definitions came only from the frozen v22 specification.

# Commands

- `uv run --script ic_510500_put_ladder180_observation_v22.py`

# Output Files

- Formal directory: `outputs/{VERSION}/`
- Full metrics: `metrics_by_window.csv`; comparisons: `pairwise_metrics.csv`; annual attribution: `annual_attribution.csv`; activity attribution: `ladder_activity_difference.csv`.

# Full-Sample Results

{full[full['window'].eq('full')].to_markdown(index=False)}

# Window Results

{full.to_markdown(index=False)}

# Stability Classification

`{decision['stability_label']}`. Changed years: `{decision['changed_years']}`. The 1.80 point lies below the v6 common structural lower bound 1.85.

# Decision

`{decision['decision']}`; selected variant remains `None`; promotion status `{decision['promotion_status']}`.

# User-Facing Summary

Compare l180 with l190 using `pairwise_metrics.csv`. Attribution by year and by new-protection versus intensity-upgrade days is in the dedicated CSVs. Results reuse the same historical sample and are not a trading instruction.

# Detailed Diagnostics

## Pairwise metrics

{pairwise.to_markdown(index=False)}

## Annual attribution

{annual_diff.to_markdown(index=False)}

## Activity attribution

{activity_diff.to_markdown(index=False)}

## Exposure

{exposure.to_markdown(index=False)}
"""


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    signals: pd.DataFrame,
    metric_table: pd.DataFrame,
    wide: pd.DataFrame,
    annual: pd.DataFrame,
    pairwise: pd.DataFrame,
    annual_diff: pd.DataFrame,
    activity_diff: pd.DataFrame,
    exposure: pd.DataFrame,
    delta_trades: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    decision: dict[str, Any],
    integrity: dict[str, Any],
    upstream: dict[str, Any],
    market_checks: dict[str, Any],
    upstream_signal_checks: dict[str, Any],
    git_before: str,
) -> None:
    OUTPUT.mkdir(parents=False, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "signal_history.csv.gz", index=False, compression="gzip")
    metric_table.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    pairwise.to_csv(OUTPUT / "pairwise_metrics.csv", index=False)
    annual_diff.to_csv(OUTPUT / "annual_attribution.csv", index=False)
    activity_diff.to_csv(OUTPUT / "ladder_activity_difference.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_delta.csv", index=False)
    delta_trades.to_csv(OUTPUT / "delta_trade_diagnostics.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    contract_audit.to_csv(OUTPUT / "real_contract_selection_audit.csv", index=False)
    close_audit.to_csv(OUTPUT / "close_price_integrity_audit.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    record = build_record(
        metric_table,
        pairwise,
        annual_diff,
        activity_diff,
        exposure,
        decision,
        integrity,
    )
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    command = "uv run --script ic_510500_put_ladder180_observation_v22.py"
    (OUTPUT / "command_log.txt").write_text(command + "\n", encoding="utf-8")

    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_sha256": sha256(Path(__file__)),
        "spec_sha256": SPEC_SHA256,
        "input_hashes": {
            str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()
        },
        "upstream_verification": upstream,
        "sample": {
            "valuation_start": "2007-01-15",
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "execution": {
            "signal": "T close",
            "trade": "next common trading day close",
            "put": "3m monthly exit, target 95% strike/spot",
            "delta_rebalance": "entry, monthly roll, or target change only",
        },
        "capital_and_cost": {
            "ic_notional": 1.0,
            "margin_and_buffer": 0.3,
            "cash_weight_before_put_premium": 0.7,
            "cash_yield": 0.03,
            "ic_and_put_side_cost": PUT_SIDE_COST,
        },
        "valuation_ladders": {key: list(value) for key, value in LADDERS.items()},
        "mom120_delta_floor": 0.25,
        "candidates": list(VARIANTS),
        "checks": {
            "upstream_market": market_checks,
            "upstream_signal_inputs": upstream_signal_checks,
            "integrity": integrity,
        },
        "decision": decision,
        "warnings": [
            "No independent OOS",
            "1.80 is below the v6 common structural lower bound 1.85",
            "Model Put is theoretical",
            "Real sample begins 2022-09-19",
            "Daily close is not a closing-auction fill or capacity guarantee",
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

    model_long = metric_table[
        metric_table["layer"].eq("model") & metric_table["available"]
    ].copy()
    model_long = model_long.rename(
        columns={"window": "segment", "actual_start": "start"}
    )
    model_long.to_csv(SCAN / "scan_summary.csv", index=False)
    wide[wide["layer"].eq("model")].to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "candidate_bundle",
            "baseline": {"candidate": "model_l190_mom25", "same_run": True},
            "candidate_grid": list(VARIANTS),
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["capital_and_cost"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": integrity["parity_max_abs"],
            "formal_output": str(OUTPUT.relative_to(ROOT)),
            "decision": decision["decision"],
            "stability_label": decision["stability_label"],
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
    formula_self_tests()
    git_before = git_status()
    upstream = verify_inputs()
    frames, daily_valuation, market, market_checks = v21.v20.v19.v18.load_close_inputs()
    signal_inputs, upstream_signal_checks = v21.v20.v19.v18.build_signal_inputs(
        daily_valuation
    )
    schedule, signals = build_schedules(frames["ic"], daily_valuation, signal_inputs)
    signal_checks = check_signal_formula(schedule)
    daily, trades = run_candidates(frames, market, schedule)
    parity = baseline_parity(daily, schedule, trades)
    contract_audit = v21.v20.contract_selection_audit(trades, frames)
    close_audit = v21.v20.v19.v18.close_price_audit(trades, frames)
    metric_table, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    pairwise = pairwise_metrics(metric_table)
    annual_diff = annual_attribution(annual)
    activity_diff = ladder_activity_difference(schedule)
    exposure = exposure_diagnostics(daily, trades)
    delta_trades = delta_trade_diagnostics(trades)
    decision = observation_decision(
        pairwise, annual_diff, exposure, delta_trades, daily
    )
    integrity = check_integrity(
        daily,
        trades,
        schedule,
        parity,
        contract_audit,
        close_audit,
        delta_trades,
        signal_checks,
    )
    write_outputs(
        daily,
        trades,
        schedule,
        signals,
        metric_table,
        wide,
        annual,
        pairwise,
        annual_diff,
        activity_diff,
        exposure,
        delta_trades,
        parity,
        contract_audit,
        close_audit,
        decision,
        integrity,
        upstream,
        market_checks,
        upstream_signal_checks,
        git_before,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
