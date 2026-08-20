#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Backtest v6 unbounded valuation gates on the frozen IC + 510500 Put close path."""

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

import ic_510500_put_absolute_momentum_protection_tool_v11 as v11
import ic_510500_put_absolute_momentum_protection_tool_v13 as v13
import ic_510500_put_close_execution_full_retest_v17 as v17
import ic_510500_put_extreme_valuation_absolute_momentum_v10 as v10

ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_unbounded_valuation_gate_v18"
OUTPUT = ROOT / "outputs" / f"{VERSION}_formal_retry1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "f494ce96ee085dc1b14c7f0ada0d6fd4b0db15fd1da736883c5a39fa106a0c53"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_510500etf_put_ic_510500_put_unbounded_valuation_gate_v18_"
    "ic_3m_monthly_exit_m95_close_unbounded_valuation_family_threshold"
)

SCORE_FILE = (
    ROOT
    / "outputs"
    / "ic_fixed_valuation_unbounded_score_v6"
    / "daily_unbounded_fixed_scores.csv.gz"
)
V17_MANIFEST = (
    ROOT
    / "outputs"
    / "ic_510500_put_close_execution_full_retest_v17"
    / "data_manifest.json"
)
V17_COMPONENT = (
    ROOT
    / "outputs"
    / "ic_510500_put_close_execution_full_retest_v17"
    / "components"
    / "ic_510500_put_absolute_momentum_protection_tool_v13"
)
V17_COMPONENT_DAILY = V17_COMPONENT / "daily_candidates.csv.gz"
V17_COMPONENT_MANIFEST = V17_COMPONENT / "data_manifest.json"

MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-09-19")
END = pd.Timestamp("2026-08-14")
EXECUTION_STRUCTURE = "3m_monthly_exit"
MONEYNESS = 0.95

WINDOWS: dict[str, pd.DateOffset | None] = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}

VALUATION_FAMILIES = ("mean", "median", "intersection")
THRESHOLDS = (1.90, 2.00, 2.10)
VALUATION_VARIANTS = tuple(
    f"{family}_{round(threshold * 100):03d}"
    for family in VALUATION_FAMILIES
    for threshold in THRESHOLDS
)
REFERENCE_VARIANTS = ("old_fixed175_only", "paper_fixed175_or_mom120")
VARIANTS = ("no_put", *REFERENCE_VARIANTS, *VALUATION_VARIANTS)

INPUT_HASHES = {
    ROOT / "ic_fixed_valuation_unbounded_score_v6.py": (
        "f0b615d097fde668bb6896a9dd0b884f7bcf164091d332f9d9b44c4993e9a825"
    ),
    SCORE_FILE: "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77",
    ROOT / "ic_510500_put_close_execution_full_retest_v17.py": (
        "24c1702082e08f6cdf1538a879586ac684480dba8890d9ea3649c34a36150629"
    ),
    V17_MANIFEST: "c8b48171674bf25323bd809509e0d92e680aad61238515a0155e3ae1a0bf6bbc",
    ROOT / "ic_510500_put_absolute_momentum_protection_tool_v13.py": (
        "8e8514e9c9d2985b2b77b35ec7469e6ea243be1a477e40e052e0a852216ae058"
    ),
    ROOT / "ic_510500_put_absolute_momentum_protection_tool_v11.py": (
        "2149d52637304bf09a2d1be674ff3c761d8d56033a9391a2ed46f3387ed3d4f7"
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


def variant_parameters(variant: str) -> dict[str, Any]:
    if variant == "no_put":
        return {"family": "baseline", "threshold": np.nan, "signal_kind": "no_put"}
    if variant == "old_fixed175_only":
        return {
            "family": "old_fixed_reference",
            "threshold": 1.75,
            "signal_kind": "old_fixed_only",
        }
    if variant == "paper_fixed175_or_mom120":
        return {
            "family": "paper_reference",
            "threshold": 1.75,
            "signal_kind": "old_fixed_or_mom120",
        }
    family, threshold_text = variant.rsplit("_", 1)
    threshold = int(threshold_text) / 100.0
    if family not in VALUATION_FAMILIES or threshold not in THRESHOLDS:
        raise ValueError(f"Unknown v18 candidate: {variant}")
    return {"family": family, "threshold": threshold, "signal_kind": "v6_unbounded"}


def candidate_parts(candidate: str) -> dict[str, Any]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "variant": variant, **variant_parameters(variant)}


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v18 specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen v18 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v18 input changed: {path.relative_to(ROOT)}")
    if OUTPUT.exists():
        raise FileExistsError(
            f"Formal output exists and cannot be overwritten: {OUTPUT}"
        )
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")

    v17_manifest = json.loads(V17_MANIFEST.read_text(encoding="utf-8"))
    if v17_manifest["script_sha256"] != INPUT_HASHES[ROOT / v17.__file__]:
        raise RuntimeError("v17 manifest/script mismatch")
    component_manifest = json.loads(V17_COMPONENT_MANIFEST.read_text(encoding="utf-8"))
    source_manifest = component_manifest["source_manifest"]
    if source_manifest["script_sha256"] != INPUT_HASHES[ROOT / v13.__file__]:
        raise RuntimeError("v13 component manifest/script mismatch")
    for relative, expected in source_manifest["source_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v13 source changed: {relative}")
    return {
        "v17_manifest": v17_manifest,
        "v13_source_manifest": source_manifest,
    }


def signal_target(variant: str, row: pd.Series) -> float:
    if variant == "old_fixed175_only":
        return float(float(row["old_fixed_risk"]) + 1e-12 >= 1.75)
    if variant == "paper_fixed175_or_mom120":
        return float(
            float(row["old_fixed_risk"]) + 1e-12 >= 1.75
            or float(row["momentum_120"]) <= 1e-12
        )
    params = variant_parameters(variant)
    threshold = float(params["threshold"])
    mean_on = float(row["unbounded_mean_knot"]) + 1e-12 >= threshold
    median_on = float(row["unbounded_median_knot"]) + 1e-12 >= threshold
    family = str(params["family"])
    if family == "mean":
        return float(mean_on)
    if family == "median":
        return float(median_on)
    if family == "intersection":
        return float(mean_on and median_on)
    raise ValueError(variant)


def load_close_inputs() -> tuple[
    dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, dict[str, Any]
]:
    frames = v13.core.v2.load_inputs()
    frames = v17.transformed_frames(frames)
    daily_valuation, valuation_checks = v13.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    daily_valuation = daily_valuation[daily_valuation["date"] <= END].copy()
    market, market_checks = v13.proxy.prepare_model_market(
        frames["ic"],
        daily_valuation,
        frames["q50"],
        frames["etf50"],
        frames["index_sina"],
    )
    market, market_checks = v17.transformed_market((market, market_checks))
    qvix_table, qvix_stats = v13.proxy.qvix_validation(market, frames["q500"])
    if not qvix_stats["passed"]:
        raise RuntimeError("Frozen QVIX proxy validation failed")
    checks = {
        "valuation": valuation_checks,
        "market": market_checks,
        "qvix": qvix_stats,
        "qvix_table": qvix_table,
    }
    return frames, daily_valuation, market, checks


def build_signal_inputs(
    daily_valuation: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, float]]:
    scores = pd.read_csv(SCORE_FILE, parse_dates=["date"])
    needed = [
        "date",
        "pe_aggregate_ttm",
        "pb_aggregate",
        "erp",
        "trailing_dividend_contribution",
        "old_fixed_risk",
        "unbounded_mean_knot",
        "unbounded_median_knot",
    ]
    scores = scores.loc[scores["date"] <= END, needed].copy()
    anchor = v10.momentum_score_frame(daily_valuation)[
        [
            "date",
            "tri_close",
            "fixed_risk",
            "momentum_60",
            "momentum_120",
            "momentum_240",
        ]
    ].copy()
    joined = scores.merge(anchor, on="date", validate="one_to_one")
    if (
        joined["date"].min() != pd.Timestamp("2007-01-15")
        or joined["date"].max() != END
    ):
        raise RuntimeError("Unexpected v18 signal-input range")
    fixed_parity = float((joined["old_fixed_risk"] - joined["fixed_risk"]).abs().max())
    if fixed_parity > 1e-12:
        raise RuntimeError(
            "v6 old fixed score no longer matches the frozen Put signal path"
        )
    return joined.sort_values("date").reset_index(drop=True), {
        "old_fixed_max_abs": fixed_parity
    }


def build_schedules(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    signal_inputs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = signal_inputs.set_index("date")
    trade_dates = pd.DatetimeIndex(ic["date"])
    evals = {
        "model": v13.proxy.evaluation_dates(
            "daily", MODEL_START, END, trade_dates, daily_valuation
        ),
        "real": v13.proxy.evaluation_dates(
            "daily", REAL_START, END, trade_dates, daily_valuation
        ),
    }
    unique_evals = sorted(set(evals["model"]) | set(evals["real"]))
    signal_rows: list[dict[str, Any]] = []
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {
        variant: {} for variant in VARIANTS if variant != "no_put"
    }
    for day in unique_evals:
        row = frame.loc[day]
        for variant, target_history in target_lookup.items():
            target = signal_target(variant, row)
            target_history[day] = target
            signal_rows.append(
                {
                    "signal_variant": variant,
                    **variant_parameters(variant),
                    "eval_date": day,
                    "target_fraction": target,
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
                execution, initial = v13.proxy.next_execution(day, start, trade_dates)
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
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v18 execution event")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v18 signal/execution leakage")
    return schedule, signals


def run_candidates(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame, schedule: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    roll_dates = v13.v6.forced_roll_dates(frames["ic"])
    daily_parts = [
        v13.proxy.no_put_rows(frames["ic"], MODEL_START, "model_no_put"),
        v13.proxy.no_put_rows(frames["ic"], REAL_START, "real_no_put"),
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
                overlay, trades, _ = v11.run_model_tool(
                    frames,
                    market,
                    candidate_schedule,
                    EXECUTION_STRUCTURE,
                    MONEYNESS,
                    label,
                    roll_dates,
                )
            else:
                overlay, trades, _ = v11.run_real_tool(
                    frames,
                    candidate_schedule,
                    EXECUTION_STRUCTURE,
                    MONEYNESS,
                    label,
                    roll_dates,
                )
            if "signal_target_fraction" not in overlay:
                overlay["signal_target_fraction"] = overlay["target_fraction"]
            daily_parts.append(v13.proxy.assemble_candidate(overlay, frames["ic"]))
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
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    return daily, trades


def parity_audit(daily: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    frozen = pd.read_csv(V17_COMPONENT_DAILY, parse_dates=["date"])
    mappings = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        "model_paper_fixed175_or_mom120": "model_3m_monthly_exit_m95",
        "real_paper_fixed175_or_mom120": "real_3m_monthly_exit_m95",
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
            right, on="date", suffixes=("_v18", "_v17"), validate="one_to_one"
        )
        result: dict[str, Any] = {
            "audit": "daily_path",
            "candidate": candidate,
            "prior_candidate": prior,
            "rows": len(joined),
        }
        for column in columns:
            result[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v18"] - joined[f"{column}_v17"]).abs().max()
            )
        rows.append(result)

    old_schedule = pd.read_csv(
        V17_COMPONENT / "evaluation_schedule.csv.gz", parse_dates=["eval_date"]
    )
    old_schedule = old_schedule[old_schedule["signal_variant"].eq("or_mom120_000")]
    new_schedule = schedule[schedule["signal_variant"].eq("paper_fixed175_or_mom120")]
    schedule_join = new_schedule.merge(
        old_schedule[["layer", "eval_date", "three_tier_target_fraction"]],
        on=["layer", "eval_date"],
        suffixes=("_v18", "_v17"),
        validate="one_to_one",
    )
    schedule_diff = float(
        (
            schedule_join["three_tier_target_fraction_v18"]
            - schedule_join["three_tier_target_fraction_v17"]
        )
        .abs()
        .max()
    )
    rows.append(
        {
            "audit": "paper_signal_target",
            "candidate": "paper_fixed175_or_mom120",
            "prior_candidate": "or_mom120_000",
            "rows": len(schedule_join),
            "max_abs_target_diff": schedule_diff,
        }
    )
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].fillna(0.0).to_numpy().max() > 1e-14:
        raise RuntimeError("v18/v17 baseline parity failed")
    return table


def contract_selection_audit(
    trades: pd.DataFrame, frames: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    snapshots = frames["snapshots"]
    histories = frames["histories"]
    history_lookup = histories.set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    opening = {"open_buy", "open_roll", "open_roll_monthly", "open_renewal"}
    selected = trades[
        trades["candidate"].str.startswith("real_")
        & trades["action"].isin(opening)
        & trades["new_contract"].fillna("").ne("")
    ]
    rows: list[dict[str, Any]] = []
    for trade in selected.itertuples(index=False):
        day = pd.Timestamp(trade.actual_execution_date)
        month = pd.Timestamp(trade.new_month)
        choice = v11.select_real_contract_target(
            snapshots,
            history_lookup,
            day,
            month,
            float(etf.loc[day, "open"]),
            MONEYNESS,
        )
        expected_contract = str(choice[0]["contract_id"]) if choice is not None else ""
        expected_security = str(choice[0]["security_id"]) if choice is not None else ""
        actual = str(trade.new_contract)
        rows.append(
            {
                "candidate": trade.candidate,
                "actual_execution_date": day,
                "action": trade.action,
                "target_moneyness": MONEYNESS,
                "actual_moneyness": float(trade.new_entry_moneyness),
                "absolute_target_error": abs(
                    float(trade.new_entry_moneyness) - MONEYNESS
                ),
                "expected_contract_id": expected_contract,
                "expected_security_id": expected_security,
                "actual_contract": actual,
                "nearest_contract_match": actual
                in {expected_contract, expected_security},
            }
        )
    table = pd.DataFrame(rows)
    if table.empty or not table["nearest_contract_match"].all():
        raise RuntimeError("v18 real contract selection audit failed")
    return table


def close_price_audit(
    trades: pd.DataFrame, frames: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    snapshots = frames["snapshots"].copy()
    histories = frames["histories"].copy()
    contract_map = (
        snapshots[["contract_id", "security_id"]]
        .drop_duplicates("contract_id")
        .set_index("contract_id")["security_id"]
        .astype(str)
        .to_dict()
    )
    lookup = histories.set_index(["security_id", "date"])
    rows: list[dict[str, Any]] = []
    for trade in trades[trades["candidate"].str.startswith("real_")].itertuples(
        index=False
    ):
        day = pd.Timestamp(trade.actual_execution_date)
        legs: list[tuple[str, str]] = []
        if str(getattr(trade, "old_contract", "")):
            legs.append(("sell_or_resize_old", str(trade.old_contract)))
        if str(getattr(trade, "new_contract", "")):
            legs.append(("buy_or_resize_new", str(trade.new_contract)))
        for leg, contract in legs:
            security = contract_map.get(contract, contract)
            quote = None
            if (security, day) in lookup.index:
                quote = lookup.loc[(security, day)]
                if isinstance(quote, pd.DataFrame):
                    quote = quote.iloc[0]
            close = float(quote["close"]) if quote is not None else np.nan
            volume = float(quote["volume"]) if quote is not None else np.nan
            engine_open = float(quote["open"]) if quote is not None else np.nan
            rows.append(
                {
                    "candidate": trade.candidate,
                    "action": trade.action,
                    "leg": leg,
                    "actual_execution_date": day,
                    "contract_id": contract,
                    "security_id": security,
                    "source_close": close,
                    "source_volume": volume,
                    "engine_open_after_v17_transform": engine_open,
                    "open_equals_close": bool(
                        np.isfinite(close)
                        and np.isfinite(engine_open)
                        and math.isclose(close, engine_open, abs_tol=1e-12)
                    ),
                    "passed": bool(
                        np.isfinite(close)
                        and close > 0
                        and np.isfinite(volume)
                        and volume > 0
                        and math.isclose(close, engine_open, abs_tol=1e-12)
                    ),
                }
            )
    table = pd.DataFrame(rows)
    if table.empty or not table["passed"].all():
        raise RuntimeError("v18 close-price execution audit failed")
    return table


def metrics(returns: pd.Series) -> dict[str, float]:
    return v13.proxy.metrics(returns)


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
            if available:
                row.update(metrics(subset["cash_ret"]))
            else:
                row.update(
                    {
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
        ("old_fixed175_only", "old_fixed175"),
        ("paper_fixed175_or_mom120", "paper_reference"),
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
            row[f"ann_return_delta_vs_no_put_{window}"] = (
                metric_row.ann_return_delta_vs_no_put
            )
            row[f"max_dd_improvement_vs_no_put_{window}"] = (
                metric_row.max_dd_improvement_vs_no_put
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
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        trade = (
            trades[trades["candidate"].eq(candidate)]
            if candidate
            not in {
                "model_no_put",
                "real_no_put",
            }
            else trades.iloc[0:0]
        )
        entries = (
            trade[
                trade["action"].isin(
                    ["open_buy", "open_roll", "open_roll_monthly", "open_renewal"]
                )
                & trade["new_contract"].fillna("").ne("")
            ]
            if len(trade) and "new_contract" in trade
            else trade.iloc[0:0]
        )
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
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


def candidate_decisions(
    metrics_table: pd.DataFrame, exposure: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    tolerances = {
        "full": 0.01,
        "last_10y": 0.01,
        "last_5y": 0.01,
        "last_3y": 0.03,
        "last_1y": 0.03,
    }
    for variant in VALUATION_VARIANTS:
        row: dict[str, Any] = {"variant": variant, **variant_parameters(variant)}
        for layer in ("model", "real"):
            part = metrics_table[
                metrics_table["layer"].eq(layer) & metrics_table["variant"].eq(variant)
            ].set_index("window")
            required = (
                list(WINDOWS) if layer == "model" else ["full", "last_3y", "last_1y"]
            )
            return_pass = all(
                float(part.loc[window, "ann_return_delta_vs_no_put"])
                >= -tolerances[window]
                for window in required
            )
            dd_count = int(
                sum(
                    float(part.loc[window, "max_dd_improvement_vs_no_put"]) > 1e-12
                    for window in required
                )
            )
            dd_pass = bool(
                float(part.loc["full", "max_dd_improvement_vs_no_put"]) > 1e-12
                and dd_count >= (3 if layer == "model" else 2)
            )
            exp = exposure[
                exposure["layer"].eq(layer) & exposure["variant"].eq(variant)
            ].iloc[0]
            activity_pass = bool(
                int(exp["protected_days"]) >= 20
                and int(exp["entry_or_roll_events"]) >= 1
            )
            row[f"{layer}_return_tolerance_pass"] = return_pass
            row[f"{layer}_dd_windows_improved"] = dd_count
            row[f"{layer}_drawdown_pass"] = dd_pass
            row[f"{layer}_activity_pass"] = activity_pass
            row[f"{layer}_single_pass"] = bool(
                return_pass and dd_pass and activity_pass
            )
        row["both_layers_single_pass"] = bool(
            row["model_single_pass"] and row["real_single_pass"]
        )
        rows.append(row)
    table = pd.DataFrame(rows)
    pass_lookup = table.set_index("variant")["both_layers_single_pass"].to_dict()
    supports: list[bool] = []
    neighbors_text: list[str] = []
    for record in table.itertuples(index=False):
        family = record.family
        threshold = float(record.threshold)
        neighbors = [
            f"{family}_{round(value * 100):03d}"
            for value in THRESHOLDS
            if math.isclose(abs(value - threshold), 0.10, abs_tol=1e-12)
        ]
        passing = [
            neighbor for neighbor in neighbors if bool(pass_lookup.get(neighbor, False))
        ]
        supports.append(bool(passing))
        neighbors_text.append(";".join(passing))
    table["adjacent_threshold_support"] = supports
    table["passing_adjacent_thresholds"] = neighbors_text
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
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "sample_reuse": "not_independent_oos",
    }


def check_core_integrity(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
) -> dict[str, Any]:
    expected = {
        f"{layer}_{variant}" for layer in ("model", "real") for variant in VARIANTS
    }
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v18 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v18 candidate/date")
    if daily[["ret", "cash_ret"]].isna().any().any():
        raise RuntimeError("Missing v18 return")
    if (daily[["ret", "cash_ret"]] <= -1.0).any().any():
        raise RuntimeError("Invalid v18 return <= -100%")
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
    return {
        "candidate_count": len(expected),
        "daily_rows": len(daily),
        "trade_rows": len(trades),
        "max_delay_trading_days": max_delay,
        "parity_max_abs": parity_max,
        "real_contract_selection_pass": bool(
            contract_audit["nearest_contract_match"].all()
        ),
        "close_execution_legs": len(close_audit),
        "close_execution_pass": bool(close_audit["passed"].all()),
        "future_signal_rows": int(
            (regular["execution_date"] <= regular["eval_date"]).sum()
        ),
    }


def _fmt(value: Any) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{float(value):.2%}"


def _metric_table(metrics_table: pd.DataFrame, layer: str) -> str:
    columns = ["full", "last_10y", "last_5y", "last_3y", "last_1y"]
    lines = [
        "| 候选 | 全样本 | 最近10年 | 最近5年 | 最近3年 | 最近1年 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "no_put": "no-Put",
        "old_fixed175_only": "旧固定1.75（仅估值）",
        "paper_fixed175_or_mom120": "纸面固定1.75 OR MOM120",
    }
    for variant in VARIANTS:
        part = metrics_table[
            metrics_table["layer"].eq(layer) & metrics_table["variant"].eq(variant)
        ].set_index("window")
        cells = [
            f"{_fmt(part.loc[window, 'ann_return'])} / {_fmt(part.loc[window, 'max_dd'])}"
            for window in columns
        ]
        lines.append(f"| {labels.get(variant, variant)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_record(
    metrics_table: pd.DataFrame,
    exposure: pd.DataFrame,
    signal_stats: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    passing = ", ".join(summary["passing_with_neighbor_support"]) or "无"
    current = signal_stats[
        signal_stats["layer"].eq("model")
        & signal_stats["variant"].isin(VALUATION_VARIANTS)
    ][["variant", "final_target_fraction"]]
    current_text = ", ".join(
        f"{row.variant}={'ON' if row.final_target_fraction > 0 else 'OFF'}"
        for row in current.itertuples(index=False)
    )
    protected = exposure[exposure["variant"].isin(VALUATION_VARIANTS)][
        [
            "layer",
            "variant",
            "protected_days",
            "protected_day_ratio",
            "trade_events",
            "put_cost_sum",
        ]
    ].to_markdown(index=False)
    decision_table = decisions.to_markdown(index=False)
    return f"""# IC + 510500 ETF Put 无界固定估值门控 v18

## 结论

- 决定：`{summary["decision"]}`；稳定性：`{summary["stability_label"]}`；有相邻阈值支持的通过线：{passing}。
- 本层只改变估值信号，Put工具固定为三个月95%月换，T收盘确认、T+1共同交易日收盘执行。
- 模型Put不是历史可成交价；真实510500 ETF Put只有2022-09-19以后约4年样本，且已被多轮研究复用。
- 状态：`RESEARCH_ONLY_NOT_LIVE_APPROVED`。

## 模型层（含30%保证金/缓冲、70%现金年化3%）

{_metric_table(metrics_table, "model")}

## 真实510500 ETF Put层

{_metric_table(metrics_table, "real")}

真实层10年和5年显示N/A，因为可执行510500 ETF Put数据不足5年。

## 暴露与成本

{protected}

## 机械判定

{decision_table}

## 当前冻结状态

2026-08-14候选状态：{current_text}。

## 完整性

- 24条模型/真实路径、{integrity["daily_rows"]:,}条日线、{integrity["trade_rows"]:,}条交易记录；
- v17 no-Put及纸面固定1.75 OR MOM120参照逐日最大误差`{integrity["parity_max_abs"]:.3e}`；
- 真实合约选择与收盘价成交审计通过，共{integrity["close_execution_legs"]:,}条交易腿；
- 最大执行顺延{integrity["max_delay_trading_days"]}个交易日，未来信号行{integrity["future_signal_rows"]}。

## 证据边界

本结果不证明收盘集合竞价容量，也不是交易指令。若有候选通过，仍只能进入纸面观察；实盘晋升另需盘口、容量、实时合约映射、保证金和现金管理审计。
"""


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    signals: pd.DataFrame,
    metrics_table: pd.DataFrame,
    wide: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    signal_stats: pd.DataFrame,
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
    metrics_table.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    signal_stats.to_csv(OUTPUT / "signal_diagnostics.csv", index=False)
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
        metrics_table, exposure, signal_stats, decisions, summary, integrity
    )
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    commands = (
        "uv run --with pytest pytest -q test_ic_510500_put_unbounded_valuation_gate_v18.py\n"
        "uv run --with ruff ruff format --check ic_510500_put_unbounded_valuation_gate_v18.py "
        "test_ic_510500_put_unbounded_valuation_gate_v18.py\n"
        "uv run --with ruff ruff check ic_510500_put_unbounded_valuation_gate_v18.py "
        "test_ic_510500_put_unbounded_valuation_gate_v18.py\n"
        "uv run ic_510500_put_unbounded_valuation_gate_v18.py\n"
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
            "cash_weight_before_put_premium": v13.proxy.CASH_WEIGHT,
            "cash_yield": 0.03,
            "ic_and_put_side_cost": v13.proxy.PUT_FULL_SIDE_COST,
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
            "baseline": {"candidate": "model_no_put", "same_run": True},
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
    frames, daily_valuation, market, checks = load_close_inputs()
    signal_inputs, signal_checks = build_signal_inputs(daily_valuation)
    schedule, signals = build_schedules(frames["ic"], daily_valuation, signal_inputs)
    daily, trades = run_candidates(frames, market, schedule)
    parity = parity_audit(daily, schedule)
    contract_audit = contract_selection_audit(trades, frames)
    close_audit = close_price_audit(trades, frames)
    metrics_table, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_summary(daily, trades)
    signal_stats = signal_diagnostics(schedule)
    decisions = candidate_decisions(metrics_table, exposure)
    summary = decision_summary(decisions)
    integrity = check_core_integrity(
        daily, trades, schedule, parity, contract_audit, close_audit
    )
    write_outputs(
        daily,
        trades,
        schedule,
        signals,
        metrics_table,
        wide,
        annual,
        exposure,
        signal_stats,
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
