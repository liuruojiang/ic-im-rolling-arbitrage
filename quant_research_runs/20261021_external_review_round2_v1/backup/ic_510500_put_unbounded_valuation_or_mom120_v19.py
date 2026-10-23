#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Test v6 unbounded valuation OR MOM120 on the frozen IC Put close path."""

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

import ic_510500_put_unbounded_valuation_gate_v18 as v18

ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_unbounded_valuation_or_mom120_v19"
OUTPUT = ROOT / "outputs" / VERSION
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "1d34809c4e24b75e7b9f8e7d3cbea7a63b7b97b08a2869826f9f6189b4f24145"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_510500etf_put_ic_510500_put_unbounded_valuation_or_mom120_v19_"
    "ic_3m_monthly_exit_m95_close_v6_unbounded_valuation_or_mom120_replacement"
)

V18_OUTPUT = (
    ROOT / "outputs" / "ic_510500_put_unbounded_valuation_gate_v18_formal_retry1"
)
V18_DAILY = V18_OUTPUT / "daily_candidates.csv.gz"
V18_OUTPUT_MANIFEST = V18_OUTPUT / "output_manifest.json"
V18_DATA_MANIFEST = V18_OUTPUT / "data_manifest.json"
SCORE_FILE = v18.SCORE_FILE
V17_COMPONENT_DAILY = v18.V17_COMPONENT_DAILY

MODEL_START = v18.MODEL_START
REAL_START = v18.REAL_START
END = v18.END
EXECUTION_STRUCTURE = v18.EXECUTION_STRUCTURE
MONEYNESS = v18.MONEYNESS
WINDOWS = v18.WINDOWS
THRESHOLDS = v18.THRESHOLDS
VALUATION_FAMILIES = v18.VALUATION_FAMILIES

VALUATION_VARIANTS = tuple(
    f"{family}{round(threshold * 100):03d}_or_mom120"
    for family in VALUATION_FAMILIES
    for threshold in THRESHOLDS
)
REFERENCE_VARIANTS = ("mom120_only", "old_fixed175_or_mom120")
VARIANTS = ("no_put", *REFERENCE_VARIANTS, *VALUATION_VARIANTS)

INPUT_HASHES = {
    ROOT / "ic_510500_put_unbounded_valuation_gate_v18.py": (
        "f7a9b7ccc8812bf448d36170e75bf7a661aec15c2af1ded99cd9162d6845d121"
    ),
    V18_OUTPUT_MANIFEST: "acd627e1ed399d9d900bf8996c68968ff06846b403d0fdcc7d2511017b4275fc",
    V18_DATA_MANIFEST: "4c1329bc2edcdf9d91054f004b3e8b52f31ecda106bae27cd087849f5b962772",
    V18_DAILY: "a2ad0df8a22d006dd0189b8b603ebf54b69bbfaa0470086d3b38a7dd1f91feb9",
    SCORE_FILE: "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77",
    ROOT / "ic_510500_put_close_execution_full_retest_v17.py": (
        "24c1702082e08f6cdf1538a879586ac684480dba8890d9ea3649c34a36150629"
    ),
    V17_COMPONENT_DAILY: (
        "6ad15a734b60860004a1d96e0fb8fdd8a8658657b6e6a20bb4abef25f2bd1f04"
    ),
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


def split_valuation_variant(variant: str) -> tuple[str, float]:
    suffix = "_or_mom120"
    if not variant.endswith(suffix):
        raise ValueError(variant)
    core = variant[: -len(suffix)]
    for family in VALUATION_FAMILIES:
        if core.startswith(family):
            return family, int(core[len(family) :]) / 100.0
    raise ValueError(variant)


def variant_parameters(variant: str) -> dict[str, Any]:
    if variant == "no_put":
        return {"family": "baseline", "threshold": np.nan, "signal_kind": "no_put"}
    if variant == "mom120_only":
        return {
            "family": "momentum_control",
            "threshold": 0.0,
            "signal_kind": "mom120_only",
        }
    if variant == "old_fixed175_or_mom120":
        return {
            "family": "old_paper_reference",
            "threshold": 1.75,
            "signal_kind": "old_fixed_or_mom120",
        }
    family, threshold = split_valuation_variant(variant)
    if threshold not in THRESHOLDS:
        raise ValueError(variant)
    return {"family": family, "threshold": threshold, "signal_kind": "v6_or_mom120"}


def candidate_parts(candidate: str) -> dict[str, Any]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "variant": variant, **variant_parameters(variant)}


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v19 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v19 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v19 input changed: {path.relative_to(ROOT)}")
    if OUTPUT.exists():
        raise FileExistsError(
            f"Formal output exists and cannot be overwritten: {OUTPUT}"
        )
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")

    output_manifest = json.loads(V18_OUTPUT_MANIFEST.read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in output_manifest.items():
        path = V18_OUTPUT / name
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append({"file": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"v18 output manifest mismatch: {mismatches}")
    return {
        "v18_output_manifest_files": len(output_manifest),
        "v18_output_manifest_match": True,
    }


def signal_target(variant: str, row: pd.Series) -> float:
    momentum_on = float(row["momentum_120"]) <= 1e-12
    if variant == "mom120_only":
        return float(momentum_on)
    if variant == "old_fixed175_or_mom120":
        fixed_on = float(row["old_fixed_risk"]) + 1e-12 >= 1.75
        return float(fixed_on or momentum_on)
    family, threshold = split_valuation_variant(variant)
    mean_on = float(row["unbounded_mean_knot"]) + 1e-12 >= threshold
    median_on = float(row["unbounded_median_knot"]) + 1e-12 >= threshold
    if family == "mean":
        valuation_on = mean_on
    elif family == "median":
        valuation_on = median_on
    else:
        valuation_on = mean_on and median_on
    return float(valuation_on or momentum_on)


def valuation_only_target(variant: str, row: pd.Series) -> float:
    family, threshold = split_valuation_variant(variant)
    mean_on = float(row["unbounded_mean_knot"]) + 1e-12 >= threshold
    median_on = float(row["unbounded_median_knot"]) + 1e-12 >= threshold
    if family == "mean":
        return float(mean_on)
    if family == "median":
        return float(median_on)
    return float(mean_on and median_on)


def build_schedules(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    signal_inputs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = signal_inputs.set_index("date")
    trade_dates = pd.DatetimeIndex(ic["date"])
    evals = {
        "model": v18.v13.proxy.evaluation_dates(
            "daily", MODEL_START, END, trade_dates, daily_valuation
        ),
        "real": v18.v13.proxy.evaluation_dates(
            "daily", REAL_START, END, trade_dates, daily_valuation
        ),
    }
    unique_evals = sorted(set(evals["model"]) | set(evals["real"]))
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {
        variant: {} for variant in VARIANTS if variant != "no_put"
    }
    signal_rows: list[dict[str, Any]] = []
    identity_rows: list[dict[str, Any]] = []
    for day in unique_evals:
        row = frame.loc[day]
        momentum_target = float(float(row["momentum_120"]) <= 1e-12)
        for variant, target_history in target_lookup.items():
            target = signal_target(variant, row)
            target_history[day] = target
            valuation_target = (
                valuation_only_target(variant, row)
                if variant in VALUATION_VARIANTS
                else np.nan
            )
            if variant in VALUATION_VARIANTS:
                expected = max(valuation_target, momentum_target)
                identity_rows.append(
                    {
                        "signal_variant": variant,
                        "eval_date": day,
                        "valuation_target": valuation_target,
                        "momentum_target": momentum_target,
                        "actual_or_target": target,
                        "expected_or_target": expected,
                        "absolute_error": abs(target - expected),
                    }
                )
            signal_rows.append(
                {
                    "signal_variant": variant,
                    **variant_parameters(variant),
                    "eval_date": day,
                    "target_fraction": target,
                    "valuation_only_target": valuation_target,
                    "momentum_target": momentum_target,
                    "pe_aggregate_ttm": float(row["pe_aggregate_ttm"]),
                    "pb_aggregate": float(row["pb_aggregate"]),
                    "erp": float(row["erp"]),
                    "trailing_dividend_contribution": float(
                        row["trailing_dividend_contribution"]
                    ),
                    "old_fixed_risk": float(row["old_fixed_risk"]),
                    "unbounded_mean_knot": float(row["unbounded_mean_knot"]),
                    "unbounded_median_knot": float(row["unbounded_median_knot"]),
                    "momentum_120": float(row["momentum_120"]),
                }
            )

    schedule_rows: list[dict[str, Any]] = []
    for layer, evaluation_dates in evals.items():
        start = MODEL_START if layer == "model" else REAL_START
        for variant in target_lookup:
            for sequence, day in enumerate(evaluation_dates):
                execution, initial = v18.v13.proxy.next_execution(
                    day, start, trade_dates
                )
                row = frame.loc[day]
                target = target_lookup[variant][day]
                schedule_rows.append(
                    {
                        "layer": layer,
                        "frequency": "daily",
                        "signal_variant": variant,
                        "signal_family": variant_parameters(variant)["family"],
                        "sequence": sequence,
                        "eval_date": day,
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": target,
                        "three_tier_target_fraction": target,
                        "old_fixed_risk": float(row["old_fixed_risk"]),
                        "unbounded_mean_knot": float(row["unbounded_mean_knot"]),
                        "unbounded_median_knot": float(row["unbounded_median_knot"]),
                        "momentum_120": float(row["momentum_120"]),
                    }
                )
    schedule = (
        pd.DataFrame(schedule_rows)
        .sort_values(["layer", "signal_variant", "execution_date"])
        .reset_index(drop=True)
    )
    signals = (
        pd.DataFrame(signal_rows)
        .sort_values(["signal_variant", "eval_date"])
        .reset_index(drop=True)
    )
    identity = pd.DataFrame(identity_rows)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v19 execution event")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v19 signal/execution leakage")
    if identity.empty or identity["absolute_error"].max() > 1e-12:
        raise RuntimeError("v19 OR identity failed")
    return schedule, signals, identity


def run_candidates(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame, schedule: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    roll_dates = v18.v13.v6.forced_roll_dates(frames["ic"])
    daily_parts = [
        v18.v13.proxy.no_put_rows(frames["ic"], MODEL_START, "model_no_put"),
        v18.v13.proxy.no_put_rows(frames["ic"], REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    for layer in ("model", "real"):
        for variant in VARIANTS:
            if variant == "no_put":
                continue
            candidate_schedule = schedule[
                schedule["layer"].eq(layer) & schedule["signal_variant"].eq(variant)
            ]
            label = f"{layer}_{variant}"
            if layer == "model":
                overlay, trades, _ = v18.v11.run_model_tool(
                    frames,
                    market,
                    candidate_schedule,
                    EXECUTION_STRUCTURE,
                    MONEYNESS,
                    label,
                    roll_dates,
                )
            else:
                overlay, trades, _ = v18.v11.run_real_tool(
                    frames,
                    candidate_schedule,
                    EXECUTION_STRUCTURE,
                    MONEYNESS,
                    label,
                    roll_dates,
                )
            if "signal_target_fraction" not in overlay:
                overlay["signal_target_fraction"] = overlay["target_fraction"]
            daily_parts.append(v18.v13.proxy.assemble_candidate(overlay, frames["ic"]))
            if not trades.empty:
                trade_parts.append(trades)

    daily = (
        pd.concat(daily_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "date"])
        .reset_index(drop=True)
    )
    daily["signal_target_fraction"] = daily["signal_target_fraction"].fillna(
        daily["target_fraction"]
    )
    daily["cash_nav"] = daily.groupby("candidate", sort=False)["cash_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    daily["cash_drawdown"] = daily.groupby("candidate", sort=False)[
        "cash_nav"
    ].transform(lambda values: values / values.cummax() - 1.0)
    return daily, pd.concat(trade_parts, ignore_index=True, sort=False)


def parity_audit(daily: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    frozen = pd.read_csv(V18_DAILY, parse_dates=["date"])
    mappings = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        "model_old_fixed175_or_mom120": "model_paper_fixed175_or_mom120",
        "real_old_fixed175_or_mom120": "real_paper_fixed175_or_mom120",
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
    for candidate, prior in mappings.items():
        left = daily[daily["candidate"].eq(candidate)][["date", *columns]]
        right = frozen[frozen["candidate"].eq(prior)][["date", *columns]]
        joined = left.merge(
            right, on="date", suffixes=("_v19", "_v18"), validate="one_to_one"
        )
        result: dict[str, Any] = {
            "audit": "daily_path",
            "candidate": candidate,
            "prior_candidate": prior,
            "rows": len(joined),
        }
        for column in columns:
            result[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v19"] - joined[f"{column}_v18"]).abs().max()
            )
        rows.append(result)

    old_schedule = pd.read_csv(
        V18_OUTPUT / "evaluation_schedule.csv.gz", parse_dates=["eval_date"]
    )
    old_schedule = old_schedule[
        old_schedule["signal_variant"].eq("paper_fixed175_or_mom120")
    ]
    new_schedule = schedule[schedule["signal_variant"].eq("old_fixed175_or_mom120")]
    joined = new_schedule.merge(
        old_schedule[["layer", "eval_date", "three_tier_target_fraction"]],
        on=["layer", "eval_date"],
        suffixes=("_v19", "_v18"),
        validate="one_to_one",
    )
    target_diff = float(
        (
            joined["three_tier_target_fraction_v19"]
            - joined["three_tier_target_fraction_v18"]
        )
        .abs()
        .max()
    )
    rows.append(
        {
            "audit": "old_reference_signal",
            "candidate": "old_fixed175_or_mom120",
            "prior_candidate": "paper_fixed175_or_mom120",
            "rows": len(joined),
            "max_abs_target_diff": target_diff,
        }
    )
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].fillna(0.0).to_numpy().max() > 1e-14:
        raise RuntimeError("v19/v18 baseline parity failed")
    return table


def metrics(returns: pd.Series) -> dict[str, float]:
    return v18.metrics(returns)


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
    for baseline_variant, prefix in [
        ("no_put", "no_put"),
        ("mom120_only", "mom120"),
        ("old_fixed175_or_mom120", "old_reference"),
    ]:
        baseline = table[table["variant"].eq(baseline_variant)][
            ["layer", "window", "ann_return", "max_dd"]
        ].rename(
            columns={
                "ann_return": f"{prefix}_ann_return",
                "max_dd": f"{prefix}_max_dd",
            }
        )
        table = table.merge(baseline, on=["layer", "window"], validate="many_to_one")
        table[f"ann_return_delta_vs_{prefix}"] = (
            table["ann_return"] - table[f"{prefix}_ann_return"]
        )
        table[f"max_dd_improvement_vs_{prefix}"] = (
            table["max_dd"] - table[f"{prefix}_max_dd"]
        )

    wide_rows: list[dict[str, Any]] = []
    for candidate, group in table.groupby("candidate", sort=True):
        row = {"candidate": candidate, **candidate_parts(candidate)}
        for metric_row in group.itertuples(index=False):
            window = metric_row.window
            row[f"ann_return_{window}"] = metric_row.ann_return
            row[f"max_dd_{window}"] = metric_row.max_dd
            row[f"ann_return_delta_vs_old_reference_{window}"] = (
                metric_row.ann_return_delta_vs_old_reference
            )
            row[f"max_dd_improvement_vs_old_reference_{window}"] = (
                metric_row.max_dd_improvement_vs_old_reference
            )
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


def exposure_summary(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    opening_actions = {"open_buy", "open_roll", "open_roll_monthly", "open_renewal"}
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        parts = candidate_parts(candidate)
        trade = trades[trades["candidate"].eq(candidate)]
        if parts["layer"] == "model":
            entries = trade[
                trade["action"].isin(opening_actions) & trade["new_month"].notna()
            ]
        else:
            entries = trade[
                trade["action"].isin(opening_actions)
                & trade["new_contract"].fillna("").ne("")
            ]
        rows.append(
            {
                "candidate": candidate,
                **parts,
                "protected_days": int(group["target_fraction"].gt(0).sum()),
                "protected_day_ratio": float(group["target_fraction"].gt(0).mean()),
                "average_put_mark_fraction": float(group["put_mark_fraction"].mean()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "trade_events": len(trade),
                "entry_or_roll_events": len(entries),
                "deferred_days": int(group["deferred_adjustment"].sum()),
                "carried_mark_days": int(group["carried_mark"].sum()),
                "max_mark_stale_days": int(group["mark_stale_days"].max()),
                "average_entry_moneyness": (
                    float(entries["new_entry_moneyness"].mean())
                    if len(entries)
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def signal_diagnostics(schedule: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, variant), group in schedule.groupby(
        ["layer", "signal_variant"], sort=True
    ):
        group = group.sort_values("execution_date")
        active = group["three_tier_target_fraction"].gt(0)
        starts = active & ~active.shift(fill_value=False)
        rows.append(
            {
                "layer": layer,
                "variant": variant,
                **variant_parameters(variant),
                "evaluation_count": len(group),
                "signal_on_count": int(active.sum()),
                "signal_on_ratio": float(active.mean()),
                "independent_start_episodes": int(starts.sum()),
                "first_signal_on_eval": group.loc[active, "eval_date"].min()
                if active.any()
                else pd.NaT,
                "last_signal_on_eval": group.loc[active, "eval_date"].max()
                if active.any()
                else pd.NaT,
                "final_target_fraction": float(
                    group.iloc[-1]["three_tier_target_fraction"]
                ),
            }
        )
    return pd.DataFrame(rows)


def momentum_incremental_attribution(
    metrics_table: pd.DataFrame, exposure: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for layer in ("model", "real"):
        mom_exposure = exposure[
            exposure["layer"].eq(layer) & exposure["variant"].eq("mom120_only")
        ].iloc[0]
        for variant in ("old_fixed175_or_mom120", *VALUATION_VARIANTS):
            candidate_exposure = exposure[
                exposure["layer"].eq(layer) & exposure["variant"].eq(variant)
            ].iloc[0]
            part = metrics_table[
                metrics_table["layer"].eq(layer) & metrics_table["variant"].eq(variant)
            ]
            for metric_row in part.itertuples(index=False):
                rows.append(
                    {
                        "layer": layer,
                        "variant": variant,
                        **variant_parameters(variant),
                        "window": metric_row.window,
                        "available": metric_row.available,
                        "ann_return_delta_vs_mom120": metric_row.ann_return_delta_vs_mom120,
                        "max_dd_improvement_vs_mom120": (
                            metric_row.max_dd_improvement_vs_mom120
                        ),
                        "incremental_protected_days": int(
                            candidate_exposure["protected_days"]
                            - mom_exposure["protected_days"]
                        ),
                        "incremental_trade_events": int(
                            candidate_exposure["trade_events"]
                            - mom_exposure["trade_events"]
                        ),
                        "incremental_put_cost_sum": float(
                            candidate_exposure["put_cost_sum"]
                            - mom_exposure["put_cost_sum"]
                        ),
                    }
                )
    return pd.DataFrame(rows)


def candidate_decisions(
    metrics_table: pd.DataFrame, exposure: pd.DataFrame
) -> pd.DataFrame:
    return_tolerance = {
        "full": 0.01,
        "last_10y": 0.01,
        "last_5y": 0.01,
        "last_3y": 0.03,
        "last_1y": 0.03,
    }
    rows: list[dict[str, Any]] = []
    for variant in VALUATION_VARIANTS:
        result: dict[str, Any] = {"variant": variant, **variant_parameters(variant)}
        for layer in ("model", "real"):
            part = metrics_table[
                metrics_table["layer"].eq(layer) & metrics_table["variant"].eq(variant)
            ].set_index("window")
            windows = (
                list(WINDOWS) if layer == "model" else ["full", "last_3y", "last_1y"]
            )
            no_put_return = all(
                float(part.loc[window, "ann_return_delta_vs_no_put"])
                >= -return_tolerance[window]
                for window in windows
            )
            no_put_dd_count = sum(
                float(part.loc[window, "max_dd_improvement_vs_no_put"]) > 1e-12
                for window in windows
            )
            no_put_defense = bool(
                no_put_return
                and float(part.loc["full", "max_dd_improvement_vs_no_put"]) > 1e-12
                and no_put_dd_count >= (3 if layer == "model" else 2)
            )
            old_return_tolerance = all(
                float(part.loc[window, "ann_return_delta_vs_old_reference"])
                >= -return_tolerance[window]
                for window in windows
            )
            old_dd_floor = all(
                float(part.loc[window, "max_dd_improvement_vs_old_reference"])
                >= -0.01 - 1e-12
                for window in windows
            )
            material = bool(
                float(part.loc["full", "ann_return_delta_vs_old_reference"])
                >= 0.005 - 1e-12
                or float(part.loc["full", "max_dd_improvement_vs_old_reference"])
                >= 0.01 - 1e-12
            )
            return_wins = sum(
                float(part.loc[window, "ann_return_delta_vs_old_reference"]) > 1e-12
                for window in windows
            )
            exp = exposure[
                exposure["layer"].eq(layer) & exposure["variant"].eq(variant)
            ].iloc[0]
            activity = bool(
                int(exp["protected_days"]) >= 20
                and int(exp["entry_or_roll_events"]) >= 1
            )
            replacement = bool(
                old_return_tolerance
                and old_dd_floor
                and material
                and return_wins >= (3 if layer == "model" else 2)
                and activity
            )
            result[f"{layer}_no_put_defense_pass"] = no_put_defense
            result[f"{layer}_old_return_tolerance_pass"] = old_return_tolerance
            result[f"{layer}_old_dd_floor_pass"] = old_dd_floor
            result[f"{layer}_material_full_improvement_pass"] = material
            result[f"{layer}_return_windows_won_vs_old"] = return_wins
            result[f"{layer}_activity_pass"] = activity
            result[f"{layer}_replacement_pass"] = replacement
        result["both_layers_single_pass"] = bool(
            result["model_no_put_defense_pass"]
            and result["real_no_put_defense_pass"]
            and result["model_replacement_pass"]
            and result["real_replacement_pass"]
        )
        rows.append(result)
    table = pd.DataFrame(rows)
    lookup = table.set_index("variant")["both_layers_single_pass"].to_dict()
    supports: list[bool] = []
    texts: list[str] = []
    for row in table.itertuples(index=False):
        neighbors = [
            f"{row.family}{round(value * 100):03d}_or_mom120"
            for value in THRESHOLDS
            if math.isclose(abs(value - float(row.threshold)), 0.10, abs_tol=1e-12)
        ]
        passing = [
            neighbor for neighbor in neighbors if bool(lookup.get(neighbor, False))
        ]
        supports.append(bool(passing))
        texts.append(";".join(passing))
    table["adjacent_threshold_support"] = supports
    table["passing_adjacent_thresholds"] = texts
    table["final_pass"] = (
        table["both_layers_single_pass"] & table["adjacent_threshold_support"]
    )
    return table


def decision_summary(decisions: pd.DataFrame) -> dict[str, Any]:
    final = decisions.loc[decisions["final_pass"], "variant"].tolist()
    singles = decisions.loc[decisions["both_layers_single_pass"], "variant"].tolist()
    if (
        len(final) >= 6
        and decisions.loc[decisions["final_pass"], "family"].nunique() >= 2
    ):
        stability = "wide_stable"
    elif final:
        stability = "narrow_stable"
    elif singles:
        stability = "peak_only"
    else:
        stability = "reject"
    return {
        "decision": "watchlist" if final or singles else "keep_default",
        "stability_label": stability,
        "passing_with_neighbor_support": final,
        "single_path_passing_candidates": singles,
        "selected_variant": None,
        "carried_baseline": "old_fixed175_or_mom120",
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "sample_reuse": "not_independent_oos",
    }


def check_core_integrity(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    identity: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    exposure: pd.DataFrame,
) -> dict[str, Any]:
    expected = {
        f"{layer}_{variant}" for layer in ("model", "real") for variant in VARIANTS
    }
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v19 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v19 candidate/date")
    if daily[["ret", "cash_ret"]].isna().any().any():
        raise RuntimeError("Missing v19 return")
    if (daily[["ret", "cash_ret"]] <= -1.0).any().any():
        raise RuntimeError("Invalid v19 return <= -100%")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes request")
    max_delay = int(trades["delay_trading_days"].fillna(0).max())
    if max_delay > 5:
        raise RuntimeError("Real execution delay exceeded five trading days")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Signal execution leakage")
    parity_columns = [column for column in parity if column.startswith("max_abs_")]
    parity_max = float(parity[parity_columns].fillna(0.0).to_numpy().max())
    if parity_max > 1e-14:
        raise RuntimeError("Baseline parity failed")
    model_activity = exposure[
        exposure["layer"].eq("model") & exposure["variant"].isin(VALUATION_VARIANTS)
    ]
    if (model_activity["entry_or_roll_events"] <= 0).any():
        raise RuntimeError("v19 model activity count regression")
    return {
        "candidate_count": len(expected),
        "daily_rows": len(daily),
        "trade_rows": len(trades),
        "max_delay_trading_days": max_delay,
        "parity_max_abs": parity_max,
        "or_identity_max_abs": float(identity["absolute_error"].max()),
        "real_contract_selection_pass": bool(
            contract_audit["nearest_contract_match"].all()
        ),
        "close_execution_legs": len(close_audit),
        "close_execution_pass": bool(close_audit["passed"].all()),
        "future_signal_rows": int(
            (regular["execution_date"] <= regular["eval_date"]).sum()
        ),
        "model_activity_min_entries": int(model_activity["entry_or_roll_events"].min()),
    }


def _fmt(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value):.2%}"


def _metric_table(metrics_table: pd.DataFrame, layer: str) -> str:
    labels = {
        "no_put": "no-Put",
        "mom120_only": "纯MOM120",
        "old_fixed175_or_mom120": "旧固定1.75 OR MOM120",
    }
    lines = [
        "| 候选 | 全样本 | 最近10年 | 最近5年 | 最近3年 | 最近1年 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant in VARIANTS:
        part = metrics_table[
            metrics_table["layer"].eq(layer) & metrics_table["variant"].eq(variant)
        ].set_index("window")
        cells = [
            f"{_fmt(part.loc[window, 'ann_return'])} / {_fmt(part.loc[window, 'max_dd'])}"
            for window in WINDOWS
        ]
        lines.append(f"| {labels.get(variant, variant)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_record(
    metrics_table: pd.DataFrame,
    exposure: pd.DataFrame,
    incremental: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    exposure_view = exposure[
        exposure["variant"].isin(
            ("mom120_only", "old_fixed175_or_mom120", *VALUATION_VARIANTS)
        )
    ][
        [
            "layer",
            "variant",
            "protected_days",
            "trade_events",
            "entry_or_roll_events",
            "put_cost_sum",
        ]
    ].to_markdown(index=False)
    incremental_full = incremental[incremental["window"].eq("full")].to_markdown(
        index=False
    )
    passing = ", ".join(summary["passing_with_neighbor_support"]) or "无"
    return f"""# IC + 510500 ETF Put 无界固定估值 OR MOM120 v19

## Decision

- Decision：`{summary["decision"]}`；Stability：`{summary["stability_label"]}`；相邻支持通过线：{passing}。
- Data：模型{MODEL_START.date()}—{END.date()}；真实510500 ETF Put {REAL_START.date()}—{END.date()}。
- 主替换基准：`old_fixed175_or_mom120`；纯MOM120仅作归因控制。
- 状态：`RESEARCH_ONLY_NOT_LIVE_APPROVED`。

## 模型层

{_metric_table(metrics_table, "model")}

## 真实510500 ETF Put层

{_metric_table(metrics_table, "real")}

真实层10年和5年为N/A，因为可执行历史不足5年。

## 暴露、交易与成本

{exposure_view}

## 相对纯MOM120的全样本增量

{incremental_full}

## 机械判定

{decisions.to_markdown(index=False)}

## 完整性

- 24条路径、{integrity["daily_rows"]:,}条日线、{integrity["trade_rows"]:,}条交易；
- v18/v17基准逐日最大误差`{integrity["parity_max_abs"]:.3e}`；OR恒等式误差`{integrity["or_identity_max_abs"]:.3e}`；
- 真实收盘交易腿{integrity["close_execution_legs"]:,}条全部通过，最大顺延{integrity["max_delay_trading_days"]}个交易日；
- 模型新候选最少{integrity["model_activity_min_entries"]}次建仓/月换，已修复v18活动计数字段。

## 证据边界

模型Put不是历史可成交价；真实期不足5年且已被多轮研究复用；日线close不是收盘集合竞价盘口或容量保证。本结果不是交易指令。
"""


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    signals: pd.DataFrame,
    identity: pd.DataFrame,
    metrics_table: pd.DataFrame,
    wide: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    signal_stats: pd.DataFrame,
    incremental: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
    upstream: dict[str, Any],
    checks: dict[str, Any],
    signal_checks: dict[str, float],
    git_before: str,
) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(
        OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip"
    )
    signals.to_csv(OUTPUT / "signal_history.csv.gz", index=False, compression="gzip")
    identity.to_csv(
        OUTPUT / "or_identity_audit.csv.gz", index=False, compression="gzip"
    )
    metrics_table.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    signal_stats.to_csv(OUTPUT / "signal_diagnostics.csv", index=False)
    incremental.to_csv(OUTPUT / "momentum_incremental_attribution.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    contract_audit.to_csv(OUTPUT / "real_contract_selection_audit.csv", index=False)
    close_audit.to_csv(OUTPUT / "close_price_integrity_audit.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    checks["qvix_table"].to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record = build_record(
        metrics_table, exposure, incremental, decisions, summary, integrity
    )
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    commands = (
        "uv run --with pytest --with numpy --with pandas pytest -q "
        "test_ic_510500_put_unbounded_valuation_or_mom120_v19.py\n"
        "uv run --with ruff ruff format --check "
        "ic_510500_put_unbounded_valuation_or_mom120_v19.py "
        "test_ic_510500_put_unbounded_valuation_or_mom120_v19.py\n"
        "uv run --with ruff ruff check ic_510500_put_unbounded_valuation_or_mom120_v19.py "
        "test_ic_510500_put_unbounded_valuation_or_mom120_v19.py\n"
        "uv run ic_510500_put_unbounded_valuation_or_mom120_v19.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(commands, encoding="utf-8")

    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "sample": {
            "valuation_start": "2007-01-15",
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "execution": "signal T close; Put transaction T+1 common trading-day close",
        "tool": {
            "execution_structure": EXECUTION_STRUCTURE,
            "moneyness": MONEYNESS,
            "protection_fraction": 1.0,
        },
        "capital_and_cost": {
            "ic_notional": 1.0,
            "margin_and_buffer": 0.30,
            "cash_weight_before_put_premium": v18.v13.proxy.CASH_WEIGHT,
            "cash_yield": 0.03,
            "ic_and_put_side_cost": v18.v13.proxy.PUT_FULL_SIDE_COST,
        },
        "candidate_grid": list(VARIANTS),
        "integrity": integrity,
        "signal_checks": signal_checks,
        "valuation_checks": checks["valuation"],
        "market_checks": checks["market"],
        "qvix_checks": checks["qvix"],
        "upstream": upstream,
        "input_hashes": {
            str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()
        },
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "warnings": [
            "No independent OOS",
            "Model Put is theoretical",
            "Daily close is not a closing-auction fill or capacity guarantee",
            "Real option sample is shorter than five years",
            "Research state is not an order",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    output_hashes = {
        path.name: sha256(path)
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "output_manifest.json"
    }
    (OUTPUT / "output_manifest.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    model_scan = metrics_table[metrics_table["layer"].eq("model")].copy()
    scan_summary = model_scan.rename(
        columns={"window": "segment", "actual_start": "start"}
    )[
        [
            "candidate",
            "variant",
            "family",
            "threshold",
            "segment",
            "start",
            "end",
            "rows",
            "ann_return",
            "ann_vol",
            "sharpe_repo",
            "max_dd",
        ]
    ]
    scan_wide = wide[wide["layer"].eq("model")].copy()
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\n" + commands)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "candidate_bundle",
            "baseline": {"candidate": "model_old_fixed175_or_mom120", "same_run": True},
            "candidate_grid": list(VARIANTS),
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["capital_and_cost"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": integrity["parity_max_abs"],
            "formal_output": str(OUTPUT.relative_to(ROOT)),
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
    frames, daily_valuation, market, checks = v18.load_close_inputs()
    signal_inputs, signal_checks = v18.build_signal_inputs(daily_valuation)
    schedule, signals, identity = build_schedules(
        frames["ic"], daily_valuation, signal_inputs
    )
    daily, trades = run_candidates(frames, market, schedule)
    parity = parity_audit(daily, schedule)
    contract_audit = v18.contract_selection_audit(trades, frames)
    close_audit = v18.close_price_audit(trades, frames)
    metrics_table, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_summary(daily, trades)
    signal_stats = signal_diagnostics(schedule)
    incremental = momentum_incremental_attribution(metrics_table, exposure)
    decisions = candidate_decisions(metrics_table, exposure)
    summary = decision_summary(decisions)
    integrity = check_core_integrity(
        daily,
        trades,
        schedule,
        identity,
        parity,
        contract_audit,
        close_audit,
        exposure,
    )
    write_outputs(
        daily,
        trades,
        schedule,
        signals,
        identity,
        metrics_table,
        wide,
        annual,
        exposure,
        signal_stats,
        incremental,
        parity,
        contract_audit,
        close_audit,
        decisions,
        summary,
        integrity,
        upstream,
        checks,
        signal_checks,
        git_before,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
