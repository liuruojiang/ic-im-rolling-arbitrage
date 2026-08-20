#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Preregistered MOM120 Delta-floor scan for IC + 510500 ETF Put."""

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

import ic_510500_put_tiered_notional_delta_v20 as v20

ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_mom120_delta_floor_v21"
OUTPUT = ROOT / "outputs" / VERSION
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "a928a8f8b6d03d42cb4156c861653974aaccaae1953d9bbd23153f2e4e28c329"
SCAN = ROOT / "quant_param_scan_runs" / "20260818_mom120_delta_floor_25_50_75"

V20_OUTPUT = ROOT / "outputs" / "ic_510500_put_tiered_notional_delta_v20"
V20_DAILY = V20_OUTPUT / "daily_candidates.csv.gz"
V20_SCHEDULE = V20_OUTPUT / "evaluation_schedule.csv.gz"
V20_TRADES = V20_OUTPUT / "trade_audit.csv"
V20_OUTPUT_MANIFEST = V20_OUTPUT / "output_manifest.json"
V20_DATA_MANIFEST = V20_OUTPUT / "data_manifest.json"

MODEL_START = v20.MODEL_START
REAL_START = v20.REAL_START
END = v20.END
WINDOWS = v20.WINDOWS
MONEYNESS = v20.MONEYNESS
PUT_SIDE_COST = v20.PUT_SIDE_COST

VARIANTS = (
    "no_put",
    "current_fixed1",
    "mom_only_d25",
    "mom_only_d50",
    "mom_only_d75",
    "l190_mom25",
    "l190_mom50",
    "l190_mom75",
    "c200_mom25",
    "c200_mom50",
    "c200_mom75",
)
DELTA_VARIANTS = tuple(
    variant for variant in VARIANTS if variant not in {"no_put", "current_fixed1"}
)
LADDERS = {
    "l190": (1.90, 2.00, 2.10),
    "c200": (2.00, 2.10, 2.15),
}
FLOORS = {"25": 0.25, "50": 0.50, "75": 0.75}

INPUT_HASHES = {
    ROOT / "ic_510500_put_tiered_notional_delta_v20.py": (
        "52349adf6fc62a15e0412f655e190890917c7a93b05171571d4653453857c556"
    ),
    ROOT / "docs" / "ic_510500_put_tiered_notional_delta_v20_spec.md": (
        "87cf67abf960d5935bf5211b958f74a470fb4c18c6ebd58a4b49eac73bc1c874"
    ),
    V20_OUTPUT_MANIFEST: (
        "9ac6c5f5f1c803009f0c8f44beffb39f90dd2caf9cc711197552a672957a9260"
    ),
    V20_DATA_MANIFEST: (
        "95f223b04e78e8900dc57156557ec7cfee5bba415029b7b5f8c5e6a466618f0f"
    ),
    V20_DAILY: "c1f0e3be619ee0cb5b17fd020e3ee3b1b831741928fae0ed658d26b94fffd488",
    V20_SCHEDULE: "4449b6b2035b7f4a8895dee063d9a095e3cd7ab38e58dd96807fce83d57b1066",
    V20_TRADES: "c912ad2165aa8934f45954f63682df4a379d80a8f15316693aa2ceb6b5b1f070",
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
    if variant.startswith("mom_only_d"):
        floor = FLOORS[variant.removeprefix("mom_only_d")]
        return {
            "signal_shape": "momentum_only",
            "sizing_method": "delta",
            "valuation_ladder": "none",
            "mom_delta_floor": floor,
        }
    ladder, floor_name = variant.split("_mom", 1)
    return {
        "signal_shape": "valuation_plus_momentum",
        "sizing_method": "delta",
        "valuation_ladder": ladder,
        "mom_delta_floor": FLOORS[floor_name],
    }


def candidate_parts(candidate: str) -> dict[str, Any]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "variant": variant, **variant_parameters(variant)}


def valuation_tier(score: float, ladder: str) -> int:
    if ladder == "none":
        return 0
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
    """Return valuation tier, risk tier, notional target, and Delta target."""
    params = variant_parameters(variant)
    ladder = str(params["valuation_ladder"])
    value_tier = valuation_tier(score, ladder)
    momentum_on = momentum_120 <= 1e-12
    if variant == "current_fixed1":
        risk_tier = max(value_tier, int(momentum_on))
        return value_tier, risk_tier, float(risk_tier > 0), math.nan
    floor = float(params["mom_delta_floor"])
    momentum_target = floor if momentum_on else 0.0
    valuation_target = 0.25 * value_tier
    delta_target = max(valuation_target, momentum_target)
    risk_tier = round(delta_target / 0.25)
    return value_tier, risk_tier, math.nan, delta_target


def formula_self_tests() -> None:
    assert valuation_tier(1.899999, "l190") == 0
    assert valuation_tier(1.90, "l190") == 1
    assert valuation_tier(2.00, "l190") == 2
    assert valuation_tier(2.10, "l190") == 3
    assert valuation_tier(2.00, "c200") == 1
    assert valuation_tier(2.10, "c200") == 2
    assert valuation_tier(2.15, "c200") == 3
    assert targets_for_variant("c200_mom75", 2.00, -0.01)[3] == 0.75
    assert targets_for_variant("c200_mom75", 2.10, 0.01)[3] == 0.50
    assert targets_for_variant("l190_mom50", 2.10, -0.01)[3] == 0.75
    assert targets_for_variant("mom_only_d50", 99.0, 0.01)[3] == 0.0
    assert targets_for_variant("mom_only_d50", -99.0, 0.0)[3] == 0.50


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v21 specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen v21 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v21 input changed: {path.relative_to(ROOT)}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    upstream_manifest = json.loads(V20_OUTPUT_MANIFEST.read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in upstream_manifest.items():
        path = V20_OUTPUT / name
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append({"file": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"v20 output manifest mismatch: {mismatches}")
    return {
        "v20_output_manifest_files": len(upstream_manifest),
        "v20_output_manifest_match": True,
    }


def build_schedules(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    signal_inputs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v20.v19.v18.v13.proxy
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
                execution, initial = proxy.next_execution(
                    day, start, trade_dates
                )
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
                    notional_target
                    if np.isfinite(notional_target)
                    else delta_target
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
        raise RuntimeError("Duplicate v21 execution schedule row")
    return schedule.reset_index(drop=True), signals.reset_index(drop=True)


def run_candidates(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v20.v19.v18.v13.proxy
    roll_dates = v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
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
                    overlay, trades, _ = v20.v19.v18.v11.run_model_tool(
                        frames,
                        market,
                        candidate_schedule,
                        v20.v19.EXECUTION_STRUCTURE,
                        MONEYNESS,
                        label,
                        roll_dates,
                    )
                else:
                    overlay, trades, _ = v20.v19.v18.v11.run_real_tool(
                        frames,
                        candidate_schedule,
                        v20.v19.EXECUTION_STRUCTURE,
                        MONEYNESS,
                        label,
                        roll_dates,
                    )
                overlay = v20._attach_risk_tier(overlay, candidate_schedule)
                overlay = v20.decorate_fixed_overlay(overlay, layer, frames, market)
                trades = v20.normalize_fixed_trades(trades, candidate_schedule)
            elif layer == "model":
                overlay, trades = v20.run_model_delta(
                    frames["ic"], candidate_schedule, market, label, roll_dates
                )
            else:
                overlay, trades = v20.run_real_delta(
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
    frozen_daily = pd.read_csv(V20_DAILY, parse_dates=["date"])
    mappings = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        "model_current_fixed1": "model_binary_notional1x",
        "real_current_fixed1": "real_binary_notional1x",
        "model_c200_mom25": "model_tier_delta255075",
        "real_c200_mom25": "real_tier_delta255075",
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
            right, on="date", suffixes=("_v21", "_v20"), validate="one_to_one"
        )
        row: dict[str, Any] = {
            "check_type": "daily",
            "current_candidate": current,
            "prior_candidate": prior,
            "rows": len(joined),
        }
        for column in columns:
            row[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v21"] - joined[f"{column}_v20"]).abs().max()
            )
        rows.append(row)

    frozen_schedule = pd.read_csv(
        V20_SCHEDULE, parse_dates=["execution_date", "eval_date"]
    )
    schedule_mappings = {
        "current_fixed1": "binary_notional1x",
        "c200_mom25": "tier_delta255075",
    }
    for layer in ("model", "real"):
        for current, prior in schedule_mappings.items():
            left = schedule[
                schedule["layer"].eq(layer)
                & schedule["signal_variant"].eq(current)
            ][["execution_date", "risk_tier", "three_tier_target_fraction"]]
            right = frozen_schedule[
                frozen_schedule["layer"].eq(layer)
                & frozen_schedule["signal_variant"].eq(prior)
            ][["execution_date", "risk_tier", "three_tier_target_fraction"]]
            joined = left.merge(
                right,
                on="execution_date",
                suffixes=("_v21", "_v20"),
                validate="one_to_one",
            )
            rows.append(
                {
                    "check_type": "schedule",
                    "current_candidate": f"{layer}_{current}",
                    "prior_candidate": f"{layer}_{prior}",
                    "rows": len(joined),
                    "max_abs_risk_tier_diff": float(
                        (joined["risk_tier_v21"] - joined["risk_tier_v20"]).abs().max()
                    ),
                    "max_abs_target_fraction_diff": float(
                        (
                            joined["three_tier_target_fraction_v21"]
                            - joined["three_tier_target_fraction_v20"]
                        ).abs().max()
                    ),
                }
            )

    frozen_trades = pd.read_csv(
        V20_TRADES,
        parse_dates=["actual_execution_date", "scheduled_execution_date"],
    )
    for layer in ("model", "real"):
        left = trades[trades["candidate"].eq(f"{layer}_c200_mom25")].copy()
        right = frozen_trades[
            frozen_trades["candidate"].eq(f"{layer}_tier_delta255075")
        ].copy()
        key = ["actual_execution_date", "action"]
        numeric = [
            "target_delta",
            "target_delta_error",
            "entry_abs_delta",
            "new_notional_fraction",
        ]
        joined = left[key + numeric].merge(
            right[key + numeric],
            on=key,
            suffixes=("_v21", "_v20"),
            validate="one_to_one",
        )
        row = {
            "check_type": "trade",
            "current_candidate": f"{layer}_c200_mom25",
            "prior_candidate": f"{layer}_tier_delta255075",
            "rows": len(joined),
            "max_abs_trade_count_diff": float(abs(len(left) - len(right))),
        }
        for column in numeric:
            row[f"max_abs_{column}_diff"] = float(
                (
                    joined[f"{column}_v21"].fillna(0.0)
                    - joined[f"{column}_v20"].fillna(0.0)
                ).abs().max()
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    numeric_columns = [column for column in table if column.startswith("max_abs_")]
    if table[numeric_columns].fillna(0.0).to_numpy().max() > 1e-14:
        raise RuntimeError("v21/v20 baseline parity failed")
    return table


def metrics(returns: pd.Series) -> dict[str, float]:
    return v20.metrics(returns)


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        parts = candidate_parts(candidate)
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else END - offset
            available = bool(offset is None or group["date"].min() <= requested)
            subset = group if offset is None else group[group["date"] >= requested]
            row: dict[str, Any] = {
                "candidate": candidate,
                **parts,
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
    baseline = table[table["variant"].eq("no_put")][
        ["layer", "window", "ann_return", "max_dd"]
    ].rename(columns={"ann_return": "no_put_ann_return", "max_dd": "no_put_max_dd"})
    table = table.merge(baseline, on=["layer", "window"], validate="many_to_one")
    table["ann_return_delta_vs_no_put"] = (
        table["ann_return"] - table["no_put_ann_return"]
    )
    table["max_dd_improvement_vs_no_put"] = (
        table["max_dd"] - table["no_put_max_dd"]
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
                "tier1_days": int(group["risk_tier"].eq(1).sum()),
                "tier2_days": int(group["risk_tier"].eq(2).sum()),
                "tier3_days": int(group["risk_tier"].eq(3).sum()),
                "trade_events": len(trade),
                "resize_events": int(trade["action"].eq("close_resize").sum()),
                "monthly_roll_events": int(
                    trade["action"].eq("close_roll_monthly").sum()
                ),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "average_put_mark_fraction": float(group["put_mark_fraction"].mean()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "max_post_trade_put_mark_fraction": float(
                    post_trade["put_mark_fraction"].max()
                )
                if len(post_trade)
                else 0.0,
                "average_actual_notional_when_active": float(
                    active["actual_notional_fraction"].mean()
                )
                if len(active)
                else 0.0,
                "max_actual_notional_fraction": float(
                    group["actual_notional_fraction"].max()
                ),
                "average_effective_delta_when_active": float(
                    active["effective_delta_hedge_ratio"].mean()
                )
                if len(active)
                else 0.0,
                "max_effective_delta_hedge_ratio": float(
                    group["effective_delta_hedge_ratio"].max()
                ),
                "days_effective_delta_over_100pct": int(
                    group["effective_delta_hedge_ratio"].gt(1.0).sum()
                ),
                "deferred_days": int(group["deferred_adjustment"].sum()),
                "carried_mark_days": int(group["carried_mark"].sum()),
                "max_mark_stale_days": int(group["mark_stale_days"].max()),
            }
        )
    return pd.DataFrame(rows)


def signal_activity(schedule: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, variant), group in schedule.groupby(
        ["layer", "signal_variant"], sort=True
    ):
        group = group.sort_values("execution_date")
        transitions = group["risk_tier"].ne(group["risk_tier"].shift())
        for tier in range(4):
            subset = group[group["risk_tier"].eq(tier)]
            rows.append(
                {
                    "layer": layer,
                    "variant": variant,
                    "risk_tier": tier,
                    "evaluation_days": len(subset),
                    "evaluation_ratio": float(len(subset) / len(group)),
                    "transition_entries": int(
                        (transitions & group["risk_tier"].eq(tier)).sum()
                    ),
                    "momentum_on_days": int(group["momentum_floor_on"].sum()),
                    "first_eval_date": subset["eval_date"].min()
                    if len(subset)
                    else pd.NaT,
                    "last_eval_date": subset["eval_date"].max()
                    if len(subset)
                    else pd.NaT,
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


def pairwise_metrics(metric_table: pd.DataFrame) -> pd.DataFrame:
    pairs = []
    for family in ("mom_only_d", "l190_mom", "c200_mom"):
        for floor in ("50", "75"):
            pairs.append((f"{family}{floor}", f"{family}25", "floor_upgrade"))
    for family in ("mom_only_d", "l190_mom", "c200_mom"):
        pairs.append((f"{family}75", f"{family}50", "floor_75_vs_50"))
    for floor in ("25", "50", "75"):
        pairs.extend(
            [
                (f"l190_mom{floor}", f"mom_only_d{floor}", "valuation_increment"),
                (f"c200_mom{floor}", f"mom_only_d{floor}", "valuation_increment"),
                (f"l190_mom{floor}", f"c200_mom{floor}", "ladder_l190_vs_c200"),
            ]
        )
    rows: list[dict[str, Any]] = []
    for candidate, control, comparison in pairs:
        for layer in ("model", "real"):
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
                        "comparison": comparison,
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


def candidate_decisions(
    pairwise: pd.DataFrame,
    metric_table: pd.DataFrame,
    exposure: pd.DataFrame,
    delta_trades: pd.DataFrame,
    schedule: pd.DataFrame,
    daily: pd.DataFrame,
) -> pd.DataFrame:
    candidates = [
        "mom_only_d50",
        "mom_only_d75",
        "l190_mom50",
        "l190_mom75",
        "c200_mom50",
        "c200_mom75",
    ]
    return_tolerance = {
        "full": 0.01,
        "last_10y": 0.01,
        "last_5y": 0.01,
        "last_3y": 0.03,
        "last_1y": 0.03,
    }
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        control = candidate[:-2] + "25"
        row: dict[str, Any] = {"candidate": candidate, "control": control}
        for layer in ("model", "real"):
            pair = pairwise[
                pairwise["comparison"].eq("floor_upgrade")
                & pairwise["layer"].eq(layer)
                & pairwise["candidate"].eq(candidate)
            ].set_index("window")
            windows = list(WINDOWS) if layer == "model" else ["full", "last_3y", "last_1y"]
            full_dd = float(pair.loc["full", "max_dd_improvement"]) >= 0.01 - 1e-12
            dd_count = sum(
                float(pair.loc[window, "max_dd_improvement"]) > 1e-12
                for window in windows
            )
            dd_breadth = dd_count >= (3 if layer == "model" else 2)
            dd_floor = all(
                float(pair.loc[window, "max_dd_improvement"]) >= -0.01 - 1e-12
                for window in windows
            )
            return_pass = all(
                float(pair.loc[window, "ann_return_delta"])
                >= -return_tolerance[window] - 1e-12
                for window in windows
            )
            metric = metric_table[
                metric_table["layer"].eq(layer)
                & metric_table["variant"].eq(candidate)
            ].set_index("window")
            no_put_dd = float(metric.loc["full", "max_dd_improvement_vs_no_put"]) > 1e-12
            no_put_return = all(
                float(metric.loc[window, "ann_return_delta_vs_no_put"])
                >= -return_tolerance[window] - 1e-12
                for window in windows
            )
            exp = exposure[
                exposure["layer"].eq(layer) & exposure["variant"].eq(candidate)
            ].iloc[0]
            capital = bool(
                float(exp["max_post_trade_put_mark_fraction"]) <= 0.70 + 1e-12
            )
            trade = delta_trades[
                delta_trades["candidate"].eq(f"{layer}_{candidate}")
            ]
            delta_error = bool(
                len(trade)
                and float(trade["target_delta_error"].max())
                <= (1e-12 if layer == "model" else 0.02) + 1e-12
            )
            fallback_ratio = float(
                trade["delta_source"].eq("qvix_proxy_fallback").mean()
            )
            fallback_pass = bool(layer == "model" or fallback_ratio <= 0.05 + 1e-12)
            signal = schedule[
                schedule["layer"].eq(layer)
                & schedule["signal_variant"].eq(candidate)
            ]
            momentum_days = int(signal["momentum_floor_on"].sum())
            activity = momentum_days >= (20 if layer == "model" else 10)
            returns_valid = bool(
                daily[daily["candidate"].eq(f"{layer}_{candidate}")]["cash_ret"].min()
                > -1.0
            )
            method_pass = bool(
                full_dd
                and dd_breadth
                and dd_floor
                and return_pass
                and no_put_dd
                and no_put_return
                and capital
                and delta_error
                and fallback_pass
                and activity
                and returns_valid
            )
            row.update(
                {
                    f"{layer}_full_dd_1pp_pass": full_dd,
                    f"{layer}_dd_windows_improved": dd_count,
                    f"{layer}_dd_breadth_pass": dd_breadth,
                    f"{layer}_dd_floor_pass": dd_floor,
                    f"{layer}_return_tolerance_pass": return_pass,
                    f"{layer}_no_put_dd_pass": no_put_dd,
                    f"{layer}_no_put_return_pass": no_put_return,
                    f"{layer}_capital_pass": capital,
                    f"{layer}_delta_error_pass": delta_error,
                    f"{layer}_iv_fallback_ratio": fallback_ratio,
                    f"{layer}_iv_fallback_pass": fallback_pass,
                    f"{layer}_momentum_days": momentum_days,
                    f"{layer}_activity_pass": activity,
                    f"{layer}_returns_valid": returns_valid,
                    f"{layer}_method_pass": method_pass,
                    f"{layer}_full_cagr_delta_vs_control": float(
                        pair.loc["full", "ann_return_delta"]
                    ),
                    f"{layer}_full_dd_improvement_vs_control": float(
                        pair.loc["full", "max_dd_improvement"]
                    ),
                }
            )
        row["both_layers_pass"] = bool(
            row["model_method_pass"] and row["real_method_pass"]
        )
        rows.append(row)
    return pd.DataFrame(rows)


def decision_summary(
    decisions: pd.DataFrame, pairwise: pd.DataFrame
) -> dict[str, Any]:
    passing = decisions.loc[decisions["both_layers_pass"], "candidate"].tolist()
    ladder_choices: dict[str, str | None] = {}
    for family in ("l190_mom", "c200_mom"):
        pass50 = f"{family}50" in passing
        pass75 = f"{family}75" in passing
        choice: str | None = None
        if pass50:
            choice = f"{family}50"
        elif pass75:
            choice = f"{family}75"
        if pass50 and pass75:
            incremental = pairwise[
                pairwise["comparison"].eq("floor_75_vs_50")
                & pairwise["candidate"].eq(f"{family}75")
                & pairwise["window"].eq("full")
            ].set_index("layer")
            dd_extra = all(
                float(incremental.loc[layer, "max_dd_improvement"]) >= 0.01 - 1e-12
                for layer in ("model", "real")
            )
            return_ok = all(
                float(incremental.loc[layer, "ann_return_delta"]) >= -0.01 - 1e-12
                for layer in ("model", "real")
            )
            if dd_extra and return_ok:
                choice = f"{family}75"
        ladder_choices[family] = choice
    if any(ladder_choices.values()):
        decision = "mom120_stronger_delta_floor_watchlist"
        stability = "preregistered_gate_pass"
    else:
        decision = "keep_mom120_delta_floor_25"
        stability = "no_stronger_floor_pass"
    return {
        "decision": decision,
        "stability_label": stability,
        "passing_candidates": passing,
        "preferred_by_ladder": ladder_choices,
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "sample_reuse": "not_independent_oos",
    }


def check_signal_formula(schedule: pd.DataFrame) -> dict[str, Any]:
    max_tier_error = 0.0
    max_notional_error = 0.0
    max_delta_error = 0.0
    for row in schedule.itertuples(index=False):
        value_tier, risk_tier, notional, delta = targets_for_variant(
            row.signal_variant,
            float(row.unbounded_median_knot),
            float(row.momentum_120),
        )
        max_tier_error = max(
            max_tier_error,
            abs(value_tier - int(row.valuation_tier)),
            abs(risk_tier - int(row.risk_tier)),
        )
        if np.isfinite(notional):
            max_notional_error = max(
                max_notional_error,
                abs(notional - float(row.target_notional_fraction)),
            )
        if np.isfinite(delta):
            max_delta_error = max(
                max_delta_error, abs(delta - float(row.target_delta))
            )
    if max(max_tier_error, max_notional_error, max_delta_error) > 1e-14:
        raise RuntimeError("v21 signal formula identity failed")
    return {
        "max_tier_error": max_tier_error,
        "max_notional_error": max_notional_error,
        "max_delta_error": max_delta_error,
        "all_checks_passed": True,
    }


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
        raise RuntimeError("v21 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v21 candidate/date")
    if daily[["ret", "cash_ret"]].isna().any().any():
        raise RuntimeError("Missing v21 return")
    if (daily[["ret", "cash_ret"]] <= -1.0).any().any():
        raise RuntimeError("Invalid v21 return <= -100%")
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
    active = daily[daily["put_qty"].astype(float).gt(0)]
    required = [
        "actual_notional_fraction",
        "abs_put_delta",
        "effective_delta_hedge_ratio",
    ]
    if active[required].isna().any().any():
        raise RuntimeError("Missing active-position Delta diagnostic")
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
    exposure: pd.DataFrame,
    pairwise: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    lines = [
        f"# {VERSION} 正式记录",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "- 状态：研究回测，未批准实盘。",
        "- MOM120：中证500全收益指数120交易日绝对收益，不是MA120均线交叉。",
        "- 目标：MOM120<=0时，交易时Delta保护下限分别为25%/50%/75%。",
        "- 叠加：估值档与MOM下限取较大值；不做算术相加。",
        "- 估值阶梯：1.90/2.00/2.10与2.00/2.10/2.15并列。",
        "- 工具：3个月、95% Put，随IC月换；T收盘评估、T+1共同交易日收盘执行。",
        "",
        "## 窗口结果",
        "",
        "| 层 | 候选 | 窗口 | CAGR | MaxDD |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in metric_table[metric_table["available"]].itertuples(index=False):
        lines.append(
            f"| {row.layer} | `{row.variant}` | {row.window} | {_fmt(row.ann_return)} | {_fmt(row.max_dd)} |"
        )
    full_pairs = pairwise[
        pairwise["comparison"].eq("floor_upgrade")
        & pairwise["window"].eq("full")
    ]
    lines.extend(
        [
            "",
            "## MOM下限升级：全样本增量",
            "",
            full_pairs.to_markdown(index=False),
            "",
            "## 预注册判定",
            "",
            decisions.to_markdown(index=False),
            "",
            "## 资本与Delta诊断",
            "",
            exposure.to_markdown(index=False),
            "",
            "## 结论",
            "",
            f"- 判定：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
            f"- 通过候选：{summary['passing_candidates']}。",
            f"- 两套阶梯的事前选择：{summary['preferred_by_ladder']}。",
            f"- 全局最大Put持仓市值：{_fmt(integrity['max_put_mark_fraction'])}；最大所需名义倍数：{integrity['max_actual_notional_fraction']:.2f}。",
            "- 结果重复使用既有样本，不是独立样本外验证，不是交易指令。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    signals: pd.DataFrame,
    metric_table: pd.DataFrame,
    wide: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    activity: pd.DataFrame,
    delta_trades: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    pairwise: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
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
    exposure.to_csv(OUTPUT / "exposure_cost_delta.csv", index=False)
    activity.to_csv(OUTPUT / "signal_activity.csv", index=False)
    delta_trades.to_csv(OUTPUT / "delta_trade_diagnostics.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    contract_audit.to_csv(OUTPUT / "real_contract_selection_audit.csv", index=False)
    close_audit.to_csv(OUTPUT / "close_price_integrity_audit.csv", index=False)
    pairwise.to_csv(OUTPUT / "pairwise_metrics.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    record = build_record(metric_table, exposure, pairwise, decisions, summary, integrity)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    command = "uv run ic_510500_put_mom120_delta_floor_v21.py"
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
        "mom120": {
            "definition": "TRI_close(T)/TRI_close(T-120 trading days)-1",
            "trigger": "<=0",
            "delta_floors": [0.25, 0.50, 0.75],
            "combine": "max(valuation_target, mom_floor)",
        },
        "valuation_ladders": {key: list(value) for key, value in LADDERS.items()},
        "candidates": list(VARIANTS),
        "checks": {
            "upstream_market": market_checks,
            "upstream_signal_inputs": upstream_signal_checks,
            "integrity": integrity,
        },
        "decision": summary,
        "warnings": [
            "No independent OOS",
            "MOM120 is a 120-day absolute TRI return, not a 120-day moving average cross",
            "Model Put is theoretical",
            "Daily close is not a closing-auction fill or capacity guarantee",
            "Real sample begins 2022-09-19",
            "Uncapped Delta target can require large Put notional",
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
    model_long = model_long.rename(columns={"window": "segment"})
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
            "baseline": {"candidate": "model_c200_mom25", "same_run": True},
            "candidate_grid": list(VARIANTS),
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["capital_and_cost"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": integrity["parity_max_abs"],
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
    formula_self_tests()
    git_before = git_status()
    upstream = verify_inputs()
    frames, daily_valuation, market, market_checks = v20.v19.v18.load_close_inputs()
    signal_inputs, upstream_signal_checks = v20.v19.v18.build_signal_inputs(
        daily_valuation
    )
    schedule, signals = build_schedules(frames["ic"], daily_valuation, signal_inputs)
    signal_checks = check_signal_formula(schedule)
    daily, trades = run_candidates(frames, market, schedule)
    parity = baseline_parity(daily, schedule, trades)
    contract_audit = v20.contract_selection_audit(trades, frames)
    close_audit = v20.v19.v18.close_price_audit(trades, frames)
    metric_table, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_diagnostics(daily, trades)
    activity = signal_activity(schedule)
    delta_trades = delta_trade_diagnostics(trades)
    pairwise = pairwise_metrics(metric_table)
    decisions = candidate_decisions(
        pairwise, metric_table, exposure, delta_trades, schedule, daily
    )
    summary = decision_summary(decisions, pairwise)
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
        exposure,
        activity,
        delta_trades,
        parity,
        contract_audit,
        close_audit,
        pairwise,
        decisions,
        summary,
        integrity,
        upstream,
        market_checks,
        upstream_signal_checks,
        git_before,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
