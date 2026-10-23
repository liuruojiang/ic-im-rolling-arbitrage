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

import im_mo_adaptive_valuation_tier_put_v10 as v10
import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6
import im_mo_front95_fixed_dynamic_momentum_validation_v5 as v5
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_adaptive_valuation_mom120_floor_v12"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "262193bf10bf22dd6cc6dcf7a3902a196c955048a8a86440a2f862badf5571bb"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / (
    "20260819_im_mo_adaptive_valuation_mom120_floor_v12_im_mo_put_protection_"
    "mom120_floor_1_2_3_with_v7_valuation_neighbors"
)
V11_DAILY = (
    ROOT
    / "outputs"
    / "im_mo_adaptive_valuation_tier_put_v11"
    / "daily_candidates.csv.gz"
)
V10_SHA256 = "4d13b669a73a3782e089d6d35e0a3b7be68e11b61c8f66895cc62a1911e7a894"
V11_DAILY_SHA256 = "f2f6afceeb3d94be4ebd793f5e3c50d7cf43330a0eefd4952073eac3dda1b473"

MIN_DD_PP = 0.01
FLOORS = (1, 2, 3)
COST_MULTIPLIERS = v10.COST_MULTIPLIERS
WINDOWS = v6.WINDOWS
VALUATION_SOURCES = {
    "center": "dual_w57_q750_850_950",
    "w54": "dual_w54_q750_850_950",
    "w60": "dual_w60_q750_850_950",
    "q725": "dual_w57_q725_825_925",
    "q775": "dual_w57_q775_875_975",
}
WINDOW_NEIGHBORS = ("w54", "w60")
LADDER_NEIGHBORS = ("q725", "q775")
V11_PARITY_NAMES = {
    "valuation_center": "v7_dual_w57_q750_850_950",
    "valuation_w54": "v7_dual_w54_q750_850_950",
    "valuation_w60": "v7_dual_w60_q750_850_950",
    "valuation_q725": "v7_dual_w57_q725_825_925",
    "valuation_q775": "v7_dual_w57_q775_875_975",
    "legacy_fixed175_or_mom120": "legacy_fixed175_or_mom120",
    "no_put": "no_put",
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
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip()


def verify_inputs(*, require_fresh_output: bool) -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v12 specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen v12 specification sidecar mismatch")
    if sha256(Path(v10.__file__)) != V10_SHA256:
        raise RuntimeError("Pinned v10 utility script hash mismatch")
    if sha256(V11_DAILY) != V11_DAILY_SHA256:
        raise RuntimeError("Pinned v11 daily baseline hash mismatch")
    v10.verify_inputs(require_fresh_output=False)
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized v12 scan folder missing: {SCAN}")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError(f"v12 output already exists: {OUTPUT} / {STAGING}")
    return {
        str(SPEC.relative_to(ROOT)): SPEC_SHA256,
        str(Path(v10.__file__).relative_to(ROOT)): V10_SHA256,
        str(V11_DAILY.relative_to(ROOT)): V11_DAILY_SHA256,
    }


def candidate_definitions() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for key, source in VALUATION_SOURCES.items():
        neighbor_type = (
            "primary"
            if key == "center"
            else ("window" if key in WINDOW_NEIGHBORS else "ladder")
        )
        rows.append(
            {
                "candidate": f"valuation_{key}",
                "family": "valuation_only",
                "valuation_key": key,
                "source_state": source,
                "floor_qty": 0,
                "neighbor_type": neighbor_type,
                "structure": "3m_monthly_exit",
                "tenor": "3m",
                "moneyness": 0.95,
                "quantity_rule": "valuation_tier_0_1_2_3",
                "execution": "t_plus_1_close",
                "selection_role": "baseline",
            }
        )
    for floor in FLOORS:
        rows.append(
            {
                "candidate": f"mom120_floor{floor}",
                "family": "mom_only",
                "valuation_key": "none",
                "source_state": "mom120",
                "floor_qty": floor,
                "neighbor_type": "floor",
                "structure": "3m_monthly_exit",
                "tenor": "3m",
                "moneyness": 0.95,
                "quantity_rule": f"mom120_negative_{floor}_else_0",
                "execution": "t_plus_1_close",
                "selection_role": "formal",
            }
        )
        for key, source in VALUATION_SOURCES.items():
            neighbor_type = (
                "primary"
                if key == "center"
                else ("window" if key in WINDOW_NEIGHBORS else "ladder")
            )
            rows.append(
                {
                    "candidate": f"valmom_{key}_floor{floor}",
                    "family": "combined",
                    "valuation_key": key,
                    "source_state": source,
                    "floor_qty": floor,
                    "neighbor_type": neighbor_type,
                    "structure": "3m_monthly_exit",
                    "tenor": "3m",
                    "moneyness": 0.95,
                    "quantity_rule": f"max_valuation_tier_mom120_floor{floor}",
                    "execution": "t_plus_1_close",
                    "selection_role": "formal" if key == "center" else "width",
                }
            )
    rows.append(
        {
            "candidate": "legacy_fixed175_or_mom120",
            "family": "legacy_reference",
            "valuation_key": "legacy",
            "source_state": "fixed175_or_mom120",
            "floor_qty": 2,
            "neighbor_type": "reference",
            "structure": "3m_monthly_exit",
            "tenor": "3m",
            "moneyness": 0.95,
            "quantity_rule": "legacy_binary_0_or_2",
            "execution": "t_plus_1_close",
            "selection_role": "reference_only",
        }
    )
    result = pd.DataFrame(rows)
    if len(result) != 24 or result["candidate"].nunique() != 24:
        raise RuntimeError("v12 candidate bundle must contain 24 protection candidates")
    return result


def build_momentum_state(
    legacy_state: pd.DataFrame,
    valuation_states: pd.DataFrame,
    source_state: str | None,
    floor_qty: int,
    family: str,
) -> pd.DataFrame:
    columns = ["date", "tri_close_all", "momentum_120"]
    frame = legacy_state[columns].copy().sort_values("date")
    frame["mom120_active"] = frame["momentum_120"].le(1e-12) & frame[
        "momentum_120"
    ].notna()
    frame["mom120_floor_qty"] = np.where(frame["mom120_active"], floor_qty, 0).astype(int)
    frame["valuation_tier"] = 0
    if source_state is not None:
        source = valuation_states[valuation_states["candidate"].eq(source_state)][
            ["date", "final_tier"]
        ].rename(columns={"final_tier": "source_valuation_tier"})
        frame = frame.merge(source, on="date", how="left", validate="one_to_one")
        frame["valuation_tier"] = frame["source_valuation_tier"].fillna(0).astype(int)
        frame = frame.drop(columns="source_valuation_tier")
    if family == "mom_only":
        frame["target_qty"] = frame["mom120_floor_qty"]
    elif family == "combined":
        frame["target_qty"] = frame[["valuation_tier", "mom120_floor_qty"]].max(axis=1)
    else:
        raise ValueError(f"Unsupported momentum family: {family}")
    if not frame["target_qty"].between(0, 3).all():
        raise RuntimeError("MOM120/valuation target outside 0..3")
    return frame


def build_momentum_schedule(
    state: pd.DataFrame,
    label: str,
    trade_dates: pd.DatetimeIndex,
    source_state: str,
) -> pd.DataFrame:
    lookup = state.set_index("date")
    initial = lookup.index[lookup.index < trade_dates[0]]
    if not len(initial):
        raise RuntimeError(f"No initial momentum state for {label}")
    evaluations = [
        pd.Timestamp(initial.max()),
        *[pd.Timestamp(value) for value in trade_dates[:-1]],
    ]
    rows: list[dict[str, object]] = []
    for sequence, eval_date in enumerate(evaluations):
        execution = pd.Timestamp(
            trade_dates[0]
            if eval_date < trade_dates[0]
            else trade_dates[trade_dates > eval_date][0]
        )
        item = lookup.loc[eval_date]
        rows.append(
            {
                "candidate": label,
                "source_state": source_state,
                "frequency": "daily",
                "sequence": sequence,
                "eval_date": eval_date,
                "execution_date": execution,
                "initial_listing_exception": bool(eval_date < trade_dates[0]),
                "binary_target_qty": int(item["target_qty"]),
                "three_tier_target_qty": int(item["target_qty"]),
                "valuation_tier": int(item["valuation_tier"]),
                "mom120_floor_qty": int(item["mom120_floor_qty"]),
                "mom120_active": bool(item["mom120_active"]),
                "momentum_120": item["momentum_120"],
                "tri_close_all": item["tri_close_all"],
            }
        )
    result = pd.DataFrame(rows)
    if not result["execution_date"].gt(result["eval_date"]).all():
        raise RuntimeError(f"Non-causal MOM120 schedule: {label}")
    return result


def build_schedules(
    definitions: pd.DataFrame,
    valuation_states: pd.DataFrame,
    legacy_state: pd.DataFrame,
    model_dates: pd.DatetimeIndex,
    real_dates: pd.DatetimeIndex,
) -> dict[tuple[str, str], pd.DataFrame]:
    schedules: dict[tuple[str, str], pd.DataFrame] = {}
    for item in definitions.itertuples(index=False):
        for layer, dates in [("model", model_dates), ("real", real_dates)]:
            if item.family == "valuation_only":
                schedule = v10.build_v7_schedule(
                    valuation_states, item.source_state, item.candidate, dates
                )
            elif item.family in {"mom_only", "combined"}:
                source = item.source_state if item.family == "combined" else None
                state = build_momentum_state(
                    legacy_state, valuation_states, source, int(item.floor_qty), item.family
                )
                schedule = build_momentum_schedule(
                    state, item.candidate, dates, item.source_state
                )
            elif item.family == "legacy_reference":
                schedule = v10.build_legacy_schedule(dates, legacy_state)
                schedule["candidate"] = item.candidate
            else:
                raise ValueError(item.family)
            schedules[(layer, item.candidate)] = schedule
    return schedules


def pairwise_comparisons(
    metrics: pd.DataFrame, definitions: pd.DataFrame
) -> pd.DataFrame:
    benchmark_map: dict[str, list[str]] = {}
    for item in definitions.itertuples(index=False):
        benchmarks = ["no_put"]
        if item.family in {"mom_only", "combined"}:
            benchmarks.append("valuation_center")
        if item.family == "combined" and item.valuation_key == "center":
            benchmarks.append(f"mom120_floor{int(item.floor_qty)}")
        benchmark_map[item.candidate] = benchmarks
    rows: list[pd.DataFrame] = []
    for candidate, benchmarks in benchmark_map.items():
        candidate_rows = metrics[metrics["candidate"].eq(candidate)]
        for benchmark in benchmarks:
            base = metrics[metrics["candidate"].eq(benchmark)][
                ["layer", "window", "ann_return", "max_dd"]
            ].rename(
                columns={
                    "ann_return": "benchmark_ann_return",
                    "max_dd": "benchmark_max_dd",
                }
            )
            joined = candidate_rows.merge(base, on=["layer", "window"], validate="one_to_one")
            joined["benchmark"] = benchmark
            joined["ann_return_delta_pp"] = (
                joined["ann_return"] - joined["benchmark_ann_return"]
            ) * 100.0
            joined["max_dd_improvement_pp"] = (
                joined["max_dd"] - joined["benchmark_max_dd"]
            ) * 100.0
            joined["meaningful_dd_improvement"] = joined[
                "max_dd_improvement_pp"
            ].ge(MIN_DD_PP - 1e-12)
            rows.append(joined)
    return pd.concat(rows, ignore_index=True)


def annual_comparison(annual: pd.DataFrame) -> pd.DataFrame:
    baseline = annual[annual["candidate"].eq("no_put")][
        ["layer", "year", "ann_return", "max_dd"]
    ].rename(
        columns={"ann_return": "baseline_ann_return", "max_dd": "baseline_max_dd"}
    )
    result = annual.merge(baseline, on=["layer", "year"], validate="many_to_one")
    result["ann_return_delta_pp"] = (
        result["ann_return"] - result["baseline_ann_return"]
    ) * 100.0
    result["max_dd_improvement_pp"] = (
        result["max_dd"] - result["baseline_max_dd"]
    ) * 100.0
    result["meaningful_dd_improvement"] = result["max_dd_improvement_pp"].ge(
        MIN_DD_PP - 1e-12
    )
    return result


def parity_checks(daily: pd.DataFrame) -> dict[str, float]:
    old = pd.read_csv(V11_DAILY, parse_dates=["date"])
    checks: dict[str, float] = {}
    for new_candidate, old_candidate in V11_PARITY_NAMES.items():
        for layer in ["model", "real"]:
            new = daily[
                daily["layer"].eq(layer) & daily["candidate"].eq(new_candidate)
            ].sort_values("date")
            reference = old[
                old["layer"].eq(layer) & old["candidate"].eq(old_candidate)
            ].sort_values("date")
            joined = new.merge(
                reference,
                on="date",
                suffixes=("_new", "_old"),
                validate="one_to_one",
            )
            if len(joined) != len(new) or len(joined) != len(reference):
                raise RuntimeError(f"v11 parity date mismatch: {layer}/{new_candidate}")
            error = 0.0
            for column in ["ret", "cash_ret", "put_pnl_ret", "put_cost_rate", "put_fraction"]:
                error = max(
                    error,
                    float(
                        np.abs(
                            joined[f"{column}_new"].to_numpy()
                            - joined[f"{column}_old"].to_numpy()
                        ).max()
                    ),
                )
            checks[f"{layer}_{new_candidate}"] = error
            if error > 1e-14:
                raise RuntimeError(f"v11 path parity failure: {layer}/{new_candidate}={error}")
    return checks


def schedule_integrity(
    schedules: dict[tuple[str, str], pd.DataFrame],
    definitions: pd.DataFrame,
    legacy_state: pd.DataFrame,
) -> dict[str, float | int]:
    max_identity_errors = 0
    causality_errors = 0
    momentum_formula_error = 0.0
    tri = legacy_state[["date", "tri_close_all", "momentum_120"]].copy()
    recomputed = tri["tri_close_all"] / tri["tri_close_all"].shift(120) - 1.0
    valid = tri["momentum_120"].notna() & recomputed.notna()
    momentum_formula_error = float(
        np.abs(tri.loc[valid, "momentum_120"] - recomputed.loc[valid]).max()
    )
    family = definitions.set_index("candidate")["family"].to_dict()
    for (layer, candidate), schedule in schedules.items():
        causality_errors += int(
            (~schedule["execution_date"].gt(schedule["eval_date"])).sum()
        )
        if family[candidate] == "combined":
            expected = schedule[["valuation_tier", "mom120_floor_qty"]].max(axis=1)
            max_identity_errors += int(
                schedule["binary_target_qty"].ne(expected.astype(int)).sum()
            )
    if causality_errors or max_identity_errors or momentum_formula_error > 1e-14:
        raise RuntimeError(
            "Signal integrity failed: "
            f"causality={causality_errors}, max_identity={max_identity_errors}, "
            f"mom_error={momentum_formula_error}"
        )
    return {
        "causality_errors": causality_errors,
        "combined_max_identity_errors": max_identity_errors,
        "momentum_120_max_abs_error": momentum_formula_error,
    }


def signal_audit(
    schedules: dict[tuple[str, str], pd.DataFrame], daily: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (layer, candidate), schedule in schedules.items():
        end = daily[
            daily["layer"].eq(layer) & daily["candidate"].eq("no_put")
        ]["date"].max()
        periods = {
            "full": schedule,
            "last_1y": schedule[schedule["execution_date"] >= end - pd.DateOffset(years=1)],
            "2026": schedule[schedule["execution_date"].dt.year.eq(2026)],
        }
        for period, subset in periods.items():
            if subset.empty:
                continue
            ordered = subset.sort_values("execution_date")
            target = ordered["binary_target_qty"].astype(int)
            prior = target.shift(1).fillna(0).astype(int)
            changes = target.ne(prior)
            rows.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "period": period,
                    "schedule_rows": int(len(ordered)),
                    "target_changes": int(changes.sum()),
                    "upgrades": int((target.gt(prior) & changes).sum()),
                    "downgrades": int((target.lt(prior) & changes).sum()),
                    "protection_episodes": int((target.gt(0) & prior.eq(0)).sum()),
                    "mom_active_rows": int(
                        ordered.get("mom120_active", pd.Series(False, index=ordered.index)).fillna(False).sum()
                    ),
                    "valuation_override_rows": int(
                        (
                            ordered.get("valuation_tier", pd.Series(0, index=ordered.index)).fillna(0)
                            > ordered.get("mom120_floor_qty", pd.Series(0, index=ordered.index)).fillna(0)
                        ).sum()
                    ),
                    "tier0_rows": int(target.eq(0).sum()),
                    "tier1_rows": int(target.eq(1).sum()),
                    "tier2_rows": int(target.eq(2).sum()),
                    "tier3_rows": int(target.eq(3).sum()),
                    "annualized_target_changes": float(changes.mean() * 252.0),
                }
            )
    return pd.DataFrame(rows)


def trade_churn_audit(trades: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    real = trades[trades["layer"].eq("real")].copy()
    end = daily[daily["layer"].eq("real")]["date"].max()
    rows: list[dict[str, object]] = []
    for candidate, group in real.groupby("candidate"):
        execution = pd.to_datetime(group["actual_execution_date"])
        periods = {
            "full": group,
            "last_1y": group[execution >= end - pd.DateOffset(years=1)],
            "2026": group[execution.dt.year.eq(2026)],
        }
        for period, subset in periods.items():
            if period == "full":
                covered_days = int(
                    daily[
                        daily["layer"].eq("real") & daily["candidate"].eq(candidate)
                    ].shape[0]
                )
            elif period == "last_1y":
                covered_days = int(
                    daily[
                        daily["layer"].eq("real")
                        & daily["candidate"].eq(candidate)
                        & daily["date"].ge(end - pd.DateOffset(years=1))
                    ].shape[0]
                )
            else:
                covered_days = int(
                    daily[
                        daily["layer"].eq("real")
                        & daily["candidate"].eq(candidate)
                        & daily["date"].dt.year.eq(2026)
                    ].shape[0]
                )
            signal_events = subset[~subset["action"].eq("close_roll")]
            rows.append(
                {
                    "candidate": candidate,
                    "period": period,
                    "trade_events": int(len(subset)),
                    "signal_adjustment_events": int(len(signal_events)),
                    "roll_events": int(subset["action"].eq("close_roll").sum()),
                    "buy_contracts": int(subset.get("buy_qty", pd.Series(dtype=float)).fillna(0).sum()),
                    "sell_contracts": int(subset.get("sell_qty", pd.Series(dtype=float)).fillna(0).sum()),
                    "covered_trade_days": covered_days,
                    "annualized_signal_adjustments": (
                        len(signal_events) / covered_days * 252.0
                        if covered_days
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def allowed_return_lag(
    model_full_dd_pp: float, real_full_dd_pp: float
) -> dict[str, float]:
    if model_full_dd_pp >= 8.0 and real_full_dd_pp >= 8.0:
        return {
            "full": -2.0,
            "last_10y": -2.0,
            "last_5y": -2.0,
            "last_3y": -4.0,
            "last_1y": -4.0,
        }
    return {
        "full": -1.0,
        "last_10y": -1.0,
        "last_5y": -1.0,
        "last_3y": -3.0,
        "last_1y": -3.0,
    }


def return_pass(rows: pd.DataFrame, tolerance: dict[str, float]) -> bool:
    available = rows[rows["available"]].copy()
    return bool(
        all(
            row.ann_return_delta_pp >= tolerance[row.window] - 1e-12
            for row in available.itertuples(index=False)
        )
    )


def full_value(
    pairwise: pd.DataFrame,
    candidate: str,
    benchmark: str,
    layer: str,
    column: str,
) -> float:
    row = pairwise[
        pairwise["candidate"].eq(candidate)
        & pairwise["benchmark"].eq(benchmark)
        & pairwise["layer"].eq(layer)
        & pairwise["window"].eq("full")
    ]
    return float(row[column].iloc[0])


def valuation_incremental_pass(
    pairwise: pd.DataFrame, candidate: str, floor: int
) -> tuple[bool, dict[str, float | bool]]:
    benchmark = f"mom120_floor{floor}"
    rows = pairwise[
        pairwise["candidate"].eq(candidate) & pairwise["benchmark"].eq(benchmark)
    ]
    full = rows[rows["window"].eq("full")].set_index("layer")
    model_dd = float(full.loc["model", "max_dd_improvement_pp"])
    real_dd = float(full.loc["real", "max_dd_improvement_pp"])
    model_ann = float(full.loc["model", "ann_return_delta_pp"])
    real_ann = float(full.loc["real", "ann_return_delta_pp"])
    no_material_harm = (
        model_dd >= -0.50
        and real_dd >= -0.50
        and model_ann >= -1.0
        and real_ann >= -1.0
    )
    focus = rows[rows["window"].isin(["full", "last_1y"]) & rows["available"]]
    dd_value = bool(focus["max_dd_improvement_pp"].ge(0.50 - 1e-12).any())
    return_value = bool(
        (
            focus["ann_return_delta_pp"].ge(0.50 - 1e-12)
            & focus["max_dd_improvement_pp"].ge(-0.50 - 1e-12)
        ).any()
    )
    result = no_material_harm and (dd_value or return_value)
    return result, {
        "model_full_dd_increment_pp": model_dd,
        "real_full_dd_increment_pp": real_dd,
        "model_full_ann_increment_pp": model_ann,
        "real_full_ann_increment_pp": real_ann,
        "no_material_harm": no_material_harm,
        "dd_value": dd_value,
        "return_value": return_value,
        "incremental_pass": result,
    }


def make_decision(
    pairwise: pd.DataFrame,
    exposure: pd.DataFrame,
    signal: pd.DataFrame,
    churn: pd.DataFrame,
    stress: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for family in ["mom_only", "combined"]:
        for floor in FLOORS:
            candidate = (
                f"mom120_floor{floor}"
                if family == "mom_only"
                else f"valmom_center_floor{floor}"
            )
            no = pairwise[
                pairwise["candidate"].eq(candidate)
                & pairwise["benchmark"].eq("no_put")
            ]
            model = no[no["layer"].eq("model")]
            real = no[no["layer"].eq("real")]
            model_full_dd = full_value(
                pairwise, candidate, "no_put", "model", "max_dd_improvement_pp"
            )
            real_full_dd = full_value(
                pairwise, candidate, "no_put", "real", "max_dd_improvement_pp"
            )
            model_dd_count = int(
                (model["available"] & model["meaningful_dd_improvement"]).sum()
            )
            real_required = real[real["window"].isin(["full", "last_3y", "last_1y"])]
            real_dd_count = int(
                (
                    real_required["available"]
                    & real_required["meaningful_dd_improvement"]
                ).sum()
            )
            tolerance = allowed_return_lag(model_full_dd, real_full_dd)
            no_put_return_ok = return_pass(no, tolerance)
            valuation = pairwise[
                pairwise["candidate"].eq(candidate)
                & pairwise["benchmark"].eq("valuation_center")
            ]
            val_model_full_dd = float(
                valuation[
                    valuation["layer"].eq("model") & valuation["window"].eq("full")
                ]["max_dd_improvement_pp"].iloc[0]
            )
            val_real_full_dd = float(
                valuation[
                    valuation["layer"].eq("real") & valuation["window"].eq("full")
                ]["max_dd_improvement_pp"].iloc[0]
            )
            valuation_increment_ok = (
                val_model_full_dd >= MIN_DD_PP
                and val_real_full_dd >= MIN_DD_PP
                and return_pass(
                    valuation,
                    {
                        "full": -1.0,
                        "last_10y": -1.0,
                        "last_5y": -1.0,
                        "last_3y": -3.0,
                        "last_1y": -3.0,
                    },
                )
            )
            exp = exposure[
                exposure["layer"].eq("real") & exposure["candidate"].eq(candidate)
            ].iloc[0]
            sig = signal[
                signal["layer"].eq("real")
                & signal["candidate"].eq(candidate)
                & signal["period"].eq("full")
            ].iloc[0]
            activity_ok = bool(
                exp["protected_days"] > 0 and sig["protection_episodes"] >= 2
            )
            churn_row = churn[
                churn["candidate"].eq(candidate) & churn["period"].eq("last_1y")
            ].iloc[0]
            churn_ok = bool(churn_row["annualized_signal_adjustments"] <= 24.0 + 1e-12)
            cost = stress[
                stress["layer"].eq("real")
                & stress["candidate"].isin(["no_put", candidate])
                & stress["window"].eq("full")
                & stress["cost_multiplier"].isin([1.0, 2.0])
            ].pivot(index="cost_multiplier", columns="candidate", values="ann_return")
            delta_1x = float(cost.loc[1.0, candidate] - cost.loc[1.0, "no_put"])
            delta_2x = float(cost.loc[2.0, candidate] - cost.loc[2.0, "no_put"])
            cost_ok = not bool(delta_1x >= 0 and delta_2x < 0)
            core_economic = bool(
                model_full_dd >= MIN_DD_PP
                and model_dd_count >= 3
                and real_full_dd >= MIN_DD_PP
                and real_dd_count >= 2
                and no_put_return_ok
                and valuation_increment_ok
                and activity_ok
                and cost_ok
            )
            risk_pass = bool(model_full_dd >= 3.0 and real_full_dd >= 8.0)
            incremental = False
            incremental_detail: dict[str, float | bool] = {}
            if family == "combined":
                incremental, incremental_detail = valuation_incremental_pass(
                    pairwise, candidate, floor
                )
            rows.append(
                {
                    "family": family,
                    "floor_qty": floor,
                    "candidate": candidate,
                    "model_full_dd_improvement_pp": model_full_dd,
                    "model_dd_improvement_windows": model_dd_count,
                    "real_full_dd_improvement_pp": real_full_dd,
                    "real_dd_improvement_windows": real_dd_count,
                    "no_put_return_tolerance_pass": no_put_return_ok,
                    "valuation_model_full_dd_increment_pp": val_model_full_dd,
                    "valuation_real_full_dd_increment_pp": val_real_full_dd,
                    "valuation_baseline_increment_pass": valuation_increment_ok,
                    "real_protection_episodes": int(sig["protection_episodes"]),
                    "real_protected_days": int(exp["protected_days"]),
                    "real_average_put_fraction": float(exp["average_fraction"]),
                    "real_last1y_signal_adjustments_ann": float(
                        churn_row["annualized_signal_adjustments"]
                    ),
                    "churn_pass": churn_ok,
                    "real_full_ann_delta_1x_pp": delta_1x * 100.0,
                    "real_full_ann_delta_2x_pp": delta_2x * 100.0,
                    "cost_pass": cost_ok,
                    "core_economic_pass": core_economic,
                    "risk_priority_pass": risk_pass,
                    "valuation_vs_mom_incremental_pass": incremental
                    if family == "combined"
                    else np.nan,
                    **{f"incremental_{key}": value for key, value in incremental_detail.items()},
                }
            )
    table = pd.DataFrame(rows)

    for idx, row in table.iterrows():
        adjacent = [value for value in FLOORS if abs(value - int(row["floor_qty"])) == 1]
        adjacent_pass = table[
            table["family"].eq(row["family"])
            & table["floor_qty"].isin(adjacent)
        ]["core_economic_pass"].any()
        table.loc[idx, "floor_width_pass"] = bool(adjacent_pass)

    neighbor_rows: list[dict[str, object]] = []
    for floor in FLOORS:
        for key in [*WINDOW_NEIGHBORS, *LADDER_NEIGHBORS]:
            candidate = f"valmom_{key}_floor{floor}"
            model_dd = full_value(
                pairwise, candidate, "no_put", "model", "max_dd_improvement_pp"
            )
            real_dd = full_value(
                pairwise, candidate, "no_put", "real", "max_dd_improvement_pp"
            )
            neighbor_rows.append(
                {
                    "floor_qty": floor,
                    "candidate": candidate,
                    "neighbor_type": "window" if key in WINDOW_NEIGHBORS else "ladder",
                    "model_full_dd_improvement_pp": model_dd,
                    "real_full_dd_improvement_pp": real_dd,
                    "direction_support": bool(
                        model_dd >= MIN_DD_PP and real_dd >= MIN_DD_PP
                    ),
                }
            )
    neighbor = pd.DataFrame(neighbor_rows)
    for idx, row in table.iterrows():
        if row["family"] == "combined":
            subset = neighbor[neighbor["floor_qty"].eq(row["floor_qty"])]
            val_width = bool(
                subset[subset["neighbor_type"].eq("window")]["direction_support"].any()
                and subset[subset["neighbor_type"].eq("ladder")]["direction_support"].any()
            )
        else:
            val_width = True
        table.loc[idx, "valuation_neighbor_width_pass"] = val_width
        incremental_ok = (
            True
            if row["family"] == "mom_only"
            else bool(row["valuation_vs_mom_incremental_pass"])
        )
        table.loc[idx, "selection_eligible"] = bool(
            row["core_economic_pass"]
            and row["risk_priority_pass"]
            and row["churn_pass"]
            and table.loc[idx, "floor_width_pass"]
            and val_width
            and incremental_ok
        )

    eligible = table[table["selection_eligible"]].copy()
    selected_candidate: str | None = None
    selected_floor: int | None = None
    if len(eligible):
        selected_floor = int(eligible["floor_qty"].min())
        at_floor = eligible[eligible["floor_qty"].eq(selected_floor)]
        combined = at_floor[at_floor["family"].eq("combined")]
        selected = combined.iloc[0] if len(combined) else at_floor.iloc[0]
        selected_candidate = str(selected["candidate"])
        conclusion = "carry_candidate_to_research_review"
        same_family_pass = table[
            table["family"].eq(selected["family"])
            & table["core_economic_pass"]
        ]["floor_qty"].nunique()
        stability = "wide_stable" if same_family_pass == 3 else "narrow_stable"
    elif table["core_economic_pass"].any():
        conclusion = "watchlist_mom120_floor"
        stability = "peak_only"
    else:
        conclusion = "no_mom120_floor_candidate"
        stability = "reject"
    summary: dict[str, Any] = {
        "conclusion": conclusion,
        "stability_label": stability,
        "selected_candidate": selected_candidate,
        "selected_floor_qty": selected_floor,
        "core_economic_pass_candidates": table.loc[
            table["core_economic_pass"], "candidate"
        ].tolist(),
        "selection_eligible_candidates": table.loc[
            table["selection_eligible"], "candidate"
        ].tolist(),
        "min_meaningful_dd_improvement_pp": MIN_DD_PP,
        "live_approved": False,
        "next_action": "user review before any later Put, grid, or Call layer",
    }
    return table, neighbor, summary


def current_state_table(
    schedules: dict[tuple[str, str], pd.DataFrame], definitions: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for item in definitions.itertuples(index=False):
        schedule = schedules[("real", item.candidate)].sort_values("eval_date")
        row = schedule.iloc[-1]
        rows.append(
            {
                "candidate": item.candidate,
                "family": item.family,
                "floor_qty": item.floor_qty,
                "eval_date": row["eval_date"],
                "last_available_execution_date": row["execution_date"],
                "target_qty": row["binary_target_qty"],
                "valuation_tier": row.get("valuation_tier", np.nan),
                "mom120_active": row.get("mom120_active", np.nan),
                "momentum_120": row.get("momentum_120", np.nan),
            }
        )
    return pd.DataFrame(rows)


def make_record(
    formal: pd.DataFrame,
    pairwise: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    decision_table: pd.DataFrame,
    neighbor: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
    price_stats: dict[str, object],
) -> str:
    main_candidates = [
        "no_put",
        "valuation_center",
        *[f"mom120_floor{floor}" for floor in FLOORS],
        *[f"valmom_center_floor{floor}" for floor in FLOORS],
        "legacy_fixed175_or_mom120",
    ]
    windows = formal[formal["candidate"].isin(main_candidates)][
        ["layer", "candidate", "window", "available", "ann_return", "max_dd"]
    ]
    annual_real = annual[
        annual["layer"].eq("real") & annual["candidate"].isin(main_candidates)
    ][
        [
            "candidate",
            "year",
            "ann_return",
            "max_dd",
            "ann_return_delta_pp",
            "max_dd_improvement_pp",
        ]
    ]
    exposure_real = exposure[
        exposure["layer"].eq("real") & exposure["candidate"].isin(main_candidates[1:])
    ]
    return "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            "> 本版只扫描MOM120转负时最低1/2/3张MO；估值和三个月95%月换Put保持冻结。未批准实盘。",
            "",
            "## Decision",
            "",
            f"- Decision: `{summary['conclusion']}`.",
            f"- Stability: `{summary['stability_label']}`.",
            f"- Selected: `{summary['selected_candidate']}`; floor `{summary['selected_floor_qty']}`.",
            f"- Core pass: `{summary['core_economic_pass_candidates']}`.",
            f"- Selection eligible: `{summary['selection_eligible_candidates']}`.",
            "",
            "## Data And Execution",
            "",
            "- Model data: 2015-04-16 to 2026-08-14, CSI1000 TRI plus theoretical Put; not pre-listing IM/MO.",
            "- Real data: 2022-07-22 to 2026-08-14, official IM/MO daily close/settle chain.",
            "- Signal T close; transaction next common trading-day close; 30% margin/buffer and 70% cash at 3% net annual return.",
            "- Put fee included; bid/ask, close VWAP and market impact excluded.",
            "",
            "## Mandatory Windows",
            "",
            windows.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Floor Decision Table",
            "",
            decision_table.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Valuation Neighbor Width",
            "",
            neighbor.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Real Annual Results",
            "",
            annual_real.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Real Exposure",
            "",
            exposure_real.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Integrity",
            "",
            f"- Signal checks: `{json.dumps(integrity, ensure_ascii=False)}`.",
            f"- Real MO trade legs: {price_stats['trade_legs']}; max close error {price_stats['max_close_price_error']:.3e}.",
            "- Formal pairwise comparisons are stored in `pairwise_comparisons.csv`; no result-driven grid extension was performed.",
            "",
        ]
    )


def update_scan(
    scan_summary: pd.DataFrame,
    window_metrics: pd.DataFrame,
    definitions: pd.DataFrame,
    summary: dict[str, Any],
    source_hashes: dict[str, str],
    record: str,
) -> None:
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    command_text = (
        "python -m pytest test_im_mo_adaptive_valuation_mom120_floor_v12.py -q\n"
        "python im_mo_adaptive_valuation_mom120_floor_v12.py\n"
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command_text)
    path = SCAN / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {
                "primary": "valuation_center",
                "context": "no_put",
                "same_floor": "mom120_floor1/2/3",
            },
            "candidate_grid": definitions.to_dict("records"),
            "data_snapshot": {
                "model": [str(v6.MODEL_START.date()), str(v6.END.date())],
                "real": [str(v6.REAL_START.date()), str(v6.END.date())],
                "timezone": "Asia/Shanghai",
                "im_source": str(Path(v5.IM_QUOTES).relative_to(ROOT)),
                "mo_source": str(Path(v4.OPTIONS).relative_to(ROOT)),
                "valuation_source": str(v10.V7_STATES.relative_to(ROOT)),
            },
            "cost_model": {
                "mo_per_contract_side": v4.MO_CONTRACT_SIDE_COST,
                "cost_multipliers": list(COST_MULTIPLIERS),
                "cash_weight": v6.CASH_WEIGHT,
                "cash_annual_return": 0.03,
                "signal": "T close",
                "put_execution": "T+1 official daily close",
                "slippage_bid_ask_impact": "excluded",
            },
            "source_hashes": source_hashes,
            "research_conclusion": summary,
            "warnings": [
                "model layer is theoretical and not pre-listing IM/MO",
                "real 10y/5y scan rows are clipped placeholders; formal output is N/A",
                "daily close is not bid/ask or close VWAP",
                "not live approved",
            ],
        }
    )
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    pinned = verify_inputs(require_fresh_output=True)
    definitions = candidate_definitions()
    valuation_states = v10.load_v7_states()
    market, market_checks = v6.model_market()
    model_base = v6.model_baseline(market)
    upstream, _, _, _, _, raw_options = v4.load_inputs()
    daily_valuation, feature_diffs = v4.build_daily_valuation()
    if max(feature_diffs.values()) > 1e-14:
        raise RuntimeError(f"Frozen valuation feature parity failed: {feature_diffs}")
    legacy_state = v6.signal_state(daily_valuation)
    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    schedules = build_schedules(
        definitions, valuation_states, legacy_state, model_dates, real_dates
    )
    signal_integrity = schedule_integrity(schedules, definitions, legacy_state)

    active_im = v8.active_im_closes(upstream)
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    model_overlays: dict[str, pd.DataFrame] = {}
    real_overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    life_parts: list[pd.DataFrame] = []
    for item in definitions.itertuples(index=False):
        model_overlay, model_trades, model_lives = v8.run_model_normal_close(
            market,
            schedules[("model", item.candidate)],
            "3m",
            0.95,
            item.candidate,
        )
        real_overlay, real_trades, real_lives = v8.run_real_normal_close(
            upstream,
            options,
            active_im,
            schedules[("real", item.candidate)],
            "3m",
            0.95,
            item.candidate,
        )
        model_overlays[item.candidate] = model_overlay
        real_overlays[item.candidate] = real_overlay
        for frame in [model_trades, real_trades]:
            if len(frame):
                trade_parts.append(frame)
        for layer, frame in [("model", model_lives), ("real", real_lives)]:
            if len(frame):
                copy = frame.copy()
                copy["layer"] = layer
                life_parts.append(copy)

    real_base = upstream[["date", "im_gross_ret", "cost_rate", "im_net_ret"]].rename(
        columns={"im_gross_ret": "gross_ret", "im_net_ret": "net_ret"}
    )
    model_daily = v6.assemble_layer("model", model_base, model_overlays)
    real_daily = v6.assemble_layer("real", real_base, real_overlays)
    daily = v10.add_nav(pd.concat([model_daily, real_daily], ignore_index=True))
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False)
    schedules_frame = pd.concat(
        [
            frame.assign(layer=layer, schedule_candidate=candidate)
            for (layer, candidate), frame in schedules.items()
        ],
        ignore_index=True,
        sort=False,
    )
    expected = set(definitions["candidate"]) | {"no_put"}
    for layer in ["model", "real"]:
        subset = daily[daily["layer"].eq(layer)]
        if set(subset["candidate"]) != expected:
            raise RuntimeError(f"Incomplete v12 {layer} candidate set")
        if subset.duplicated(["candidate", "date"]).any():
            raise RuntimeError(f"Duplicate v12 {layer} candidate/date")
        if subset[["ret", "cash_ret", "cash_nav"]].isna().any().any():
            raise RuntimeError(f"Missing v12 {layer} returns")

    parity = parity_checks(daily)
    price_audit, price_stats = v10.price_integrity(trades, raw_options)
    formal, annual = v6.metrics_tables(daily)
    pairwise = pairwise_comparisons(formal, definitions)
    annual_compare = annual_comparison(annual)
    exposure = v6.exposure_table(daily, trades)
    stress_daily, stress_metrics = v10.cost_sensitivity(daily)
    signal = signal_audit(schedules, daily)
    churn = trade_churn_audit(trades, daily)
    decision_table, neighbor, summary = make_decision(
        pairwise, exposure, signal, churn, stress_metrics
    )
    current = current_state_table(schedules, definitions)
    scan_summary, window_metrics = v8.scan_tables(daily, definitions)
    integrity: dict[str, Any] = {
        **signal_integrity,
        "parity_max_abs_error": max(parity.values()),
        "candidate_count": int(len(definitions)),
        "candidate_count_with_no_put_per_layer": int(len(expected)),
    }
    record = make_record(
        formal,
        pairwise,
        annual_compare,
        exposure,
        decision_table,
        neighbor,
        summary,
        integrity,
        price_stats,
    )

    source_paths = [
        SPEC,
        Path(__file__),
        Path(v10.__file__),
        Path(v8.__file__),
        Path(v6.__file__),
        Path(v4.__file__),
        Path(v5.__file__),
        V11_DAILY,
        v10.V7_STATES,
        Path(v4.OPTIONS),
        Path(v5.IM_QUOTES),
        Path(v4.UPSTREAM),
    ]
    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path) for path in source_paths
    }
    source_hashes.update(pinned)

    STAGING.mkdir(parents=True, exist_ok=False)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    pairwise.to_csv(STAGING / "pairwise_comparisons.csv", index=False)
    annual_compare.to_csv(STAGING / "annual_metrics.csv", index=False)
    exposure.to_csv(STAGING / "exposure_cost.csv", index=False)
    definitions.to_csv(STAGING / "candidate_definitions.csv", index=False)
    schedules_frame.to_csv(STAGING / "signal_schedules.csv.gz", index=False, compression="gzip")
    signal.to_csv(STAGING / "signal_switch_audit.csv", index=False)
    churn.to_csv(STAGING / "trade_churn_audit.csv", index=False)
    trades.to_csv(STAGING / "trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(STAGING / "lifecycle_audit.csv", index=False)
    price_audit.to_csv(STAGING / "close_price_integrity_audit.csv", index=False)
    stress_daily.to_csv(STAGING / "cost_stress_daily.csv.gz", index=False, compression="gzip")
    stress_metrics.to_csv(STAGING / "cost_stress_metrics.csv", index=False)
    decision_table.to_csv(STAGING / "floor_decision_table.csv", index=False)
    neighbor.to_csv(STAGING / "valuation_neighbor_width.csv", index=False)
    current.to_csv(STAGING / "current_state.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    command_text = (
        "python -m pytest test_im_mo_adaptive_valuation_mom120_floor_v12.py -q\n"
        "python im_mo_adaptive_valuation_mom120_floor_v12.py\n"
    )
    (STAGING / "command_log.txt").write_text(command_text, encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "samples": {
            "model": [str(v6.MODEL_START.date()), str(v6.END.date())],
            "real": [str(v6.REAL_START.date()), str(v6.END.date())],
        },
        "execution": {
            "mom120": "CSI1000 TRI 120-trading-day absolute return <=0",
            "signal": "T close",
            "put_transaction": "T+1 official daily close",
            "structure": "3m monthly exit 95% moneyness",
            "quantity": "floor 1/2/3; combined=max(valuation tier, MOM floor)",
            "slippage_bid_ask_impact": "excluded",
        },
        "market_checks": market_checks,
        "valuation_feature_checks": feature_diffs,
        "integrity": integrity,
        "price_integrity": price_stats,
        "decision": summary,
        "research_status": "research_only_not_live_approved",
        "git_status": git_status(),
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    STAGING.rename(OUTPUT)
    update_scan(
        scan_summary,
        window_metrics,
        definitions,
        summary,
        source_hashes,
        record,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
