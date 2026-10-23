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

import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6
import im_mo_front95_fixed_dynamic_momentum_validation_v5 as v5
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_adaptive_valuation_tier_put_v10"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "b6f75cbc256c250643eef38af3b9ff8c7e54b95d4dadd0cd6b6ebd5b8b7216da"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / (
    "20260819_im_mo_adaptive_valuation_tier_put_v10_im_mo_put_protection_"
    "v7_window_ladder_neighbors_tier_quantity"
)
V7_STATES = (
    ROOT
    / "outputs"
    / "im_valuation_window_ladder_scan_v7"
    / "daily_candidate_states.csv.gz"
)
V7_MANIFEST = (
    ROOT / "outputs" / "im_valuation_window_ladder_scan_v7" / "data_manifest.json"
)
V9_DAILY = (
    ROOT
    / "outputs"
    / "im_mo_close_execution_full_battery_v9"
    / "daily_candidates.csv.gz"
)
V9_MANIFEST = (
    ROOT / "outputs" / "im_mo_close_execution_full_battery_v9" / "data_manifest.json"
)

V7_STATES_SHA256 = "1cddeec9ecaf52aaec216590854e570e29f27e3c61964269943eac22575aaf3b"
V9_DAILY_SHA256 = "bde8a072ba7ed91f765bd32a9c6bb8c1a8f4b9c6c45fd3947faa2746b403eeae"

PRIMARY = "v7_dual_w57_q750_850_950"
LEGACY = "legacy_fixed175_or_mom120"
LEGACY_V9 = "fixed175_or_mom120_3m_monthly_exit_m95"
WINDOW_NEIGHBORS = [
    "v7_dual_w54_q750_850_950",
    "v7_dual_w60_q750_850_950",
]
LADDER_NEIGHBORS = [
    "v7_dual_w57_q725_825_925",
    "v7_dual_w57_q775_875_975",
]
V7_SOURCE_MAP = {
    PRIMARY: "dual_w57_q750_850_950",
    WINDOW_NEIGHBORS[0]: "dual_w54_q750_850_950",
    WINDOW_NEIGHBORS[1]: "dual_w60_q750_850_950",
    LADDER_NEIGHBORS[0]: "dual_w57_q725_825_925",
    LADDER_NEIGHBORS[1]: "dual_w57_q775_875_975",
    "v7_relative_w57_q750_850_950": "relative_w57_q750_850_950",
    "v7_absolute_v3": "absolute_v3",
}
COST_MULTIPLIERS = (1.0, 2.0, 5.0)
WINDOWS = v6.WINDOWS


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
    expected = {
        SPEC: SPEC_SHA256,
        V7_STATES: V7_STATES_SHA256,
        V9_DAILY: V9_DAILY_SHA256,
    }
    hashes = {str(path.relative_to(ROOT)): sha256(path) for path in expected}
    for path, expected_hash in expected.items():
        if hashes[str(path.relative_to(ROOT))] != expected_hash:
            raise RuntimeError(f"Frozen input hash mismatch: {path}")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen specification sidecar mismatch")
    for path in [V7_MANIFEST, V9_MANIFEST, v4.OPTIONS, v5.IM_QUOTES, v4.UPSTREAM]:
        if not Path(path).exists():
            raise FileNotFoundError(path)
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {SCAN}")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError(f"Formal or staging output already exists: {OUTPUT} / {STAGING}")
    return hashes


def candidate_definitions() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate, source in V7_SOURCE_MAP.items():
        neighbor_type = "primary"
        if candidate in WINDOW_NEIGHBORS:
            neighbor_type = "window"
        elif candidate in LADDER_NEIGHBORS:
            neighbor_type = "ladder"
        elif "relative" in candidate or "absolute" in candidate:
            neighbor_type = "decomposition"
        rows.append(
            {
                "candidate": candidate,
                "source_state": source,
                "group": "v7_valuation_tier",
                "neighbor_type": neighbor_type,
                "structure": "3m_monthly_exit",
                "tenor": "3m",
                "moneyness": 0.95,
                "quantity_rule": "v7_final_tier_0_1_2_3",
                "execution": "t_plus_1_close",
                "selection_role": "formal" if neighbor_type != "decomposition" else "diagnostic",
            }
        )
    rows.append(
        {
            "candidate": LEGACY,
            "source_state": "fixed175_or_mom120",
            "group": "legacy_reference",
            "neighbor_type": "reference",
            "structure": "3m_monthly_exit",
            "tenor": "3m",
            "moneyness": 0.95,
            "quantity_rule": "binary_0_or_2",
            "execution": "t_plus_1_close",
            "selection_role": "reference_only",
        }
    )
    result = pd.DataFrame(rows)
    if len(result) != 8 or result["candidate"].nunique() != 8:
        raise RuntimeError("Unexpected candidate definition count")
    return result


def load_v7_states() -> pd.DataFrame:
    columns = [
        "date",
        "candidate",
        "unbounded_median_knot",
        "absolute_tier",
        "relative_tier",
        "final_tier",
        "calibrated",
        "rolling_percentile",
        "threshold_1",
        "threshold_2",
        "threshold_3",
    ]
    states = pd.read_csv(V7_STATES, usecols=columns, parse_dates=["date"])
    states = states[states["candidate"].isin(V7_SOURCE_MAP.values())].copy()
    states = states[states["date"] <= v6.END].sort_values(["candidate", "date"])
    if states.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v7 candidate/date state")
    if not states["final_tier"].between(0, 3).all():
        raise RuntimeError("v7 tier outside 0..3")
    missing = set(V7_SOURCE_MAP.values()) - set(states["candidate"])
    if missing:
        raise RuntimeError(f"Missing v7 state candidates: {sorted(missing)}")
    return states


def build_v7_schedule(
    states: pd.DataFrame,
    source_state: str,
    label: str,
    trade_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    source = states[states["candidate"].eq(source_state)].sort_values("date").copy()
    rows: list[dict[str, object]] = []
    for item in source.itertuples(index=False):
        eval_date = pd.Timestamp(item.date)
        pos = int(trade_dates.searchsorted(eval_date, side="right"))
        if pos >= len(trade_dates):
            continue
        execution_date = pd.Timestamp(trade_dates[pos])
        rows.append(
            {
                "candidate": label,
                "source_state": source_state,
                "frequency": "daily",
                "eval_date": eval_date,
                "execution_date": execution_date,
                "binary_target_qty": int(item.final_tier),
                "three_tier_target_qty": int(item.final_tier),
                "unbounded_median_knot": float(item.unbounded_median_knot),
                "absolute_tier": int(item.absolute_tier),
                "relative_tier": int(item.relative_tier),
                "final_tier": int(item.final_tier),
                "rolling_percentile": item.rolling_percentile,
                "threshold_1": item.threshold_1,
                "threshold_2": item.threshold_2,
                "threshold_3": item.threshold_3,
            }
        )
    schedule = pd.DataFrame(rows)
    if schedule.empty:
        raise RuntimeError(f"Empty schedule: {label}")
    schedule = schedule.sort_values(["execution_date", "eval_date"]).drop_duplicates(
        "execution_date", keep="last"
    )
    schedule = schedule.reset_index(drop=True)
    schedule["sequence"] = np.arange(len(schedule))
    if not schedule["execution_date"].gt(schedule["eval_date"]).all():
        raise RuntimeError(f"Non-causal v7 schedule: {label}")
    if not schedule["binary_target_qty"].between(0, 3).all():
        raise RuntimeError(f"Invalid target quantity: {label}")
    return schedule


def build_legacy_schedule(
    trade_dates: pd.DatetimeIndex, state: pd.DataFrame
) -> pd.DataFrame:
    schedule = v6.daily_signal_schedule(
        "fixed175_or_mom120", "fixed175_or_mom120", trade_dates, state
    ).copy()
    schedule["candidate"] = LEGACY
    schedule["source_state"] = "fixed175_or_mom120"
    if not schedule["execution_date"].gt(schedule["eval_date"]).all():
        initial = schedule["initial_listing_exception"]
        if not schedule.loc[~initial, "execution_date"].gt(
            schedule.loc[~initial, "eval_date"]
        ).all():
            raise RuntimeError("Non-causal legacy schedule")
    return schedule


def add_nav(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.sort_values(["layer", "candidate", "date"]).copy()
    frame["nav"] = frame.groupby(["layer", "candidate"])["ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    frame["cash_nav"] = frame.groupby(["layer", "candidate"])["cash_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    frame["cash_drawdown"] = frame.groupby(["layer", "candidate"])["cash_nav"].transform(
        lambda values: values / values.cummax() - 1.0
    )
    return frame


def baseline_comparison(formal: pd.DataFrame) -> pd.DataFrame:
    baseline = formal[formal["candidate"].eq("no_put")][
        ["layer", "window", "ann_return", "max_dd"]
    ].rename(
        columns={"ann_return": "baseline_ann_return", "max_dd": "baseline_max_dd"}
    )
    result = formal.merge(baseline, on=["layer", "window"], validate="many_to_one")
    result["ann_return_delta_pp"] = (
        result["ann_return"] - result["baseline_ann_return"]
    ) * 100.0
    result["max_dd_improvement_pp"] = (
        result["max_dd"] - result["baseline_max_dd"]
    ) * 100.0
    return result


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
    return result


def cost_sensitivity(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_parts: list[pd.DataFrame] = []
    metric_parts: list[pd.DataFrame] = []
    for multiplier in COST_MULTIPLIERS:
        frame = daily.drop(columns=["nav", "cash_nav", "cash_drawdown"], errors="ignore").copy()
        frame["cost_multiplier"] = multiplier
        frame["ret_stress"] = (
            (1.0 + frame["gross_ret"] + frame["put_pnl_ret"])
            * (1.0 - frame["cost_rate"])
            * (1.0 - multiplier * frame["put_cost_rate"])
            - 1.0
        )
        frame["cash_ret_stress"] = frame["ret_stress"] + (
            v6.CASH_WEIGHT - frame["put_mark_fraction"]
        ).clip(lower=0.0) * v6.CASH_DAILY
        daily_parts.append(
            frame[
                [
                    "layer",
                    "candidate",
                    "date",
                    "cost_multiplier",
                    "ret_stress",
                    "cash_ret_stress",
                ]
            ]
        )
        renamed = frame.drop(columns=["ret", "cash_ret"]).rename(
            columns={"ret_stress": "ret", "cash_ret_stress": "cash_ret"}
        )
        metrics, _ = v6.metrics_tables(renamed)
        metrics["cost_multiplier"] = multiplier
        metric_parts.append(metrics)
    return pd.concat(daily_parts, ignore_index=True), pd.concat(metric_parts, ignore_index=True)


def price_integrity(
    trades: pd.DataFrame, raw_options: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, object]]:
    real = trades[trades["layer"].eq("real")].copy()
    lookup = raw_options.set_index(["contract", "date"])
    rows: list[dict[str, object]] = []
    for item in real.itertuples(index=False):
        day = pd.Timestamp(item.actual_execution_date)
        for leg, contract_col, price_col in [
            ("old", "old_contract", "old_trade_price"),
            ("new", "new_contract", "new_trade_price"),
        ]:
            contract = getattr(item, contract_col, "")
            used = getattr(item, price_col, np.nan)
            if not isinstance(contract, str) or not contract or pd.isna(used):
                continue
            quote = lookup.loc[(contract, day)]
            if isinstance(quote, pd.DataFrame):
                raise RuntimeError("Duplicate MO quote during close-price audit")
            rows.append(
                {
                    "candidate": item.candidate,
                    "date": day,
                    "leg": leg,
                    "contract": contract,
                    "used_price": float(used),
                    "raw_close": float(quote["close"]),
                    "raw_settle": float(quote["settle"]),
                    "volume": float(quote["volume"]),
                    "open_interest": float(quote["open_interest"]),
                    "abs_close_error": abs(float(used) - float(quote["close"])),
                }
            )
    audit = pd.DataFrame(rows)
    if audit.empty:
        raise RuntimeError("No real MO trade legs to audit")
    stats: dict[str, object] = {
        "trade_legs": int(len(audit)),
        "max_close_price_error": float(audit["abs_close_error"].max()),
        "nonpositive_close_rows": int(audit["raw_close"].le(0).sum()),
        "nonpositive_volume_rows": int(audit["volume"].le(0).sum()),
        "new_leg_nonpositive_oi_rows": int(
            audit[audit["leg"].eq("new")]["open_interest"].le(0).sum()
        ),
    }
    if stats["max_close_price_error"] > 1e-14:
        raise RuntimeError(f"Close execution price mismatch: {stats}")
    if any(
        int(stats[key]) != 0
        for key in [
            "nonpositive_close_rows",
            "nonpositive_volume_rows",
            "new_leg_nonpositive_oi_rows",
        ]
    ):
        raise RuntimeError(f"Non-executable MO trade leg: {stats}")
    return audit, stats


def parity_checks(daily: pd.DataFrame) -> dict[str, float]:
    old = pd.read_csv(V9_DAILY, parse_dates=["date"])
    checks: dict[str, float] = {}
    for layer in ["model", "real"]:
        for new_candidate, old_candidate, label in [
            ("no_put", "no_put", "no_put"),
            (LEGACY, LEGACY_V9, "legacy"),
        ]:
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
                raise RuntimeError(f"Parity date mismatch: {layer}/{label}")
            errors = []
            for column in ["ret", "cash_ret", "put_pnl_ret", "put_cost_rate", "put_fraction"]:
                errors.append(
                    float(
                        np.abs(
                            joined[f"{column}_new"].to_numpy()
                            - joined[f"{column}_old"].to_numpy()
                        ).max()
                    )
                )
            error = max(errors)
            checks[f"{layer}_{label}_max_abs_error"] = error
            if error > 1e-14:
                raise RuntimeError(f"v9 parity failure: {layer}/{label}={error}")
    return checks


def state_switch_audit(
    schedules: dict[tuple[str, str], pd.DataFrame],
    daily: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (layer, candidate), schedule in schedules.items():
        layer_days = daily[
            daily["layer"].eq(layer) & daily["candidate"].eq("no_put")
        ]["date"]
        for period, subset in [
            ("full", schedule),
            ("2026", schedule[schedule["execution_date"].dt.year.eq(2026)]),
        ]:
            ordered = subset.sort_values("execution_date")
            if ordered.empty:
                continue
            target = ordered["binary_target_qty"].astype(int)
            prior = target.shift(1).fillna(0).astype(int)
            changes = target.ne(prior)
            episodes = target.gt(0) & prior.eq(0)
            covered_days = (
                layer_days.dt.year.eq(2026).sum()
                if period == "2026"
                else layer_days.size
            )
            rows.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "period": period,
                    "schedule_rows": int(len(ordered)),
                    "target_changes": int(changes.sum()),
                    "upgrades": int((target.gt(prior) & changes).sum()),
                    "downgrades": int((target.lt(prior) & changes).sum()),
                    "protection_episodes": int(episodes.sum()),
                    "tier0_rows": int(target.eq(0).sum()),
                    "tier1_rows": int(target.eq(1).sum()),
                    "tier2_rows": int(target.eq(2).sum()),
                    "tier3_rows": int(target.eq(3).sum()),
                    "covered_trade_days": int(covered_days),
                    "annualized_target_changes": (
                        float(changes.sum()) / covered_days * 252.0 if covered_days else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def trade_churn_audit(trades: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    real = trades[trades["layer"].eq("real")].copy()
    rows: list[dict[str, object]] = []
    for candidate, group in real.groupby("candidate"):
        for period, subset in [
            ("full", group),
            ("2026", group[pd.to_datetime(group["actual_execution_date"]).dt.year.eq(2026)]),
        ]:
            days = daily[
                daily["layer"].eq("real") & daily["candidate"].eq(candidate)
            ]["date"]
            covered_days = int(days.dt.year.eq(2026).sum()) if period == "2026" else len(days)
            signal_adjustments = subset[~subset["action"].eq("close_roll")]
            rows.append(
                {
                    "candidate": candidate,
                    "period": period,
                    "trade_events": int(len(subset)),
                    "signal_adjustment_events": int(len(signal_adjustments)),
                    "roll_events": int(subset["action"].eq("close_roll").sum()),
                    "buy_contracts": int(subset.get("buy_qty", pd.Series(dtype=float)).fillna(0).sum()),
                    "sell_contracts": int(subset.get("sell_qty", pd.Series(dtype=float)).fillna(0).sum()),
                    "close_buy": int(subset["action"].eq("close_buy").sum()),
                    "close_exit": int(subset["action"].eq("close_exit").sum()),
                    "close_increase": int(subset["action"].eq("close_increase").sum()),
                    "close_reduce": int(subset["action"].eq("close_reduce").sum()),
                    "covered_trade_days": covered_days,
                    "annualized_signal_adjustments": (
                        len(signal_adjustments) / covered_days * 252.0
                        if covered_days
                        else np.nan
                    ),
                }
            )
    return pd.DataFrame(rows)


def return_tolerance_pass(rows: pd.DataFrame) -> bool:
    tolerances = {
        "full": -1.0,
        "last_10y": -1.0,
        "last_5y": -1.0,
        "last_3y": -3.0,
        "last_1y": -3.0,
    }
    available = rows[rows["available"]].copy()
    return bool(
        all(
            row.ann_return_delta_pp >= tolerances[row.window] - 1e-12
            for row in available.itertuples(index=False)
        )
    )


def make_decision(
    comparison: pd.DataFrame,
    exposure: pd.DataFrame,
    switches: pd.DataFrame,
    churn: pd.DataFrame,
    stress_metrics: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    primary = comparison[comparison["candidate"].eq(PRIMARY)]
    model = primary[primary["layer"].eq("model")]
    real = primary[primary["layer"].eq("real")]
    model_dd_count = int((model["available"] & model["max_dd_improvement_pp"].gt(0)).sum())
    real_required = real[real["window"].isin(["full", "last_3y", "last_1y"])]
    real_dd_count = int(
        (real_required["available"] & real_required["max_dd_improvement_pp"].gt(0)).sum()
    )
    model_full_dd = float(
        model.loc[model["window"].eq("full"), "max_dd_improvement_pp"].iloc[0]
    )
    real_full_dd = float(
        real.loc[real["window"].eq("full"), "max_dd_improvement_pp"].iloc[0]
    )
    model_return_ok = return_tolerance_pass(model)
    real_return_ok = return_tolerance_pass(real)

    full = comparison[comparison["window"].eq("full")]
    neighbor_rows: list[dict[str, object]] = []
    for candidate in [*WINDOW_NEIGHBORS, *LADDER_NEIGHBORS]:
        values = full[full["candidate"].eq(candidate)].set_index("layer")
        neighbor_rows.append(
            {
                "candidate": candidate,
                "neighbor_type": "window" if candidate in WINDOW_NEIGHBORS else "ladder",
                "model_full_dd_improvement_pp": float(
                    values.loc["model", "max_dd_improvement_pp"]
                ),
                "real_full_dd_improvement_pp": float(
                    values.loc["real", "max_dd_improvement_pp"]
                ),
                "direction_support": bool(
                    values.loc["model", "max_dd_improvement_pp"] > 0
                    and values.loc["real", "max_dd_improvement_pp"] > 0
                ),
            }
        )
    width = pd.DataFrame(neighbor_rows)
    window_width_ok = bool(
        width[width["neighbor_type"].eq("window")]["direction_support"].any()
    )
    ladder_width_ok = bool(
        width[width["neighbor_type"].eq("ladder")]["direction_support"].any()
    )

    real_exposure = exposure[
        exposure["layer"].eq("real") & exposure["candidate"].eq(PRIMARY)
    ].iloc[0]
    real_switch = switches[
        switches["layer"].eq("real")
        & switches["candidate"].eq(PRIMARY)
        & switches["period"].eq("full")
    ].iloc[0]
    activity_ok = bool(
        real_exposure["protected_days"] > 0 and real_switch["protection_episodes"] >= 2
    )
    churn_2026 = churn[
        churn["candidate"].eq(PRIMARY) & churn["period"].eq("2026")
    ].iloc[0]
    churn_ok = bool(churn_2026["annualized_signal_adjustments"] <= 24.0 + 1e-12)

    stress = stress_metrics[
        stress_metrics["layer"].eq("real")
        & stress_metrics["candidate"].isin(["no_put", PRIMARY])
        & stress_metrics["window"].eq("full")
        & stress_metrics["cost_multiplier"].isin([1.0, 2.0])
    ]
    pivot = stress.pivot(index="cost_multiplier", columns="candidate", values="ann_return")
    delta_1x = float(pivot.loc[1.0, PRIMARY] - pivot.loc[1.0, "no_put"])
    delta_2x = float(pivot.loc[2.0, PRIMARY] - pivot.loc[2.0, "no_put"])
    cost_reversal = bool(delta_1x >= 0 and delta_2x < 0)
    cost_ok = not cost_reversal

    gates = {
        "model_full_dd_improves": model_full_dd > 0,
        "model_dd_improves_3_of_5": model_dd_count >= 3,
        "real_full_dd_improves": real_full_dd > 0,
        "real_dd_improves_2_of_3": real_dd_count >= 2,
        "model_return_tolerance": model_return_ok,
        "real_return_tolerance": real_return_ok,
        "window_neighbor_support": window_width_ok,
        "ladder_neighbor_support": ladder_width_ok,
        "real_activity_two_episodes": activity_ok,
        "annualized_adjustments_le_24": churn_ok,
        "two_x_cost_no_return_sign_reversal": cost_ok,
    }
    passed = all(gates.values())
    if passed:
        conclusion = "carry_to_momentum_layer"
        stability = "wide_stable"
    elif not activity_ok:
        conclusion = "not_testable"
        stability = "data_sensitive"
    else:
        conclusion = "valuation_only_not_sufficient"
        stability = "cost_sensitive" if not churn_ok or not cost_ok else "reject"
    summary: dict[str, object] = {
        "primary_candidate": PRIMARY,
        "conclusion": conclusion,
        "stability_label": stability,
        "all_gates_pass": passed,
        "gates": gates,
        "model_dd_improvement_window_count": model_dd_count,
        "real_dd_improvement_window_count": real_dd_count,
        "model_full_dd_improvement_pp": model_full_dd,
        "real_full_dd_improvement_pp": real_full_dd,
        "real_2026_annualized_signal_adjustments": float(
            churn_2026["annualized_signal_adjustments"]
        ),
        "real_full_ann_return_delta_1x_pp": delta_1x * 100.0,
        "real_full_ann_return_delta_2x_pp": delta_2x * 100.0,
        "live_approved": False,
        "next_layer": "MOM120 overlay only after user review",
    }
    return width, summary


def make_record(
    comparison: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    switches: pd.DataFrame,
    churn: pd.DataFrame,
    stress: pd.DataFrame,
    width: pd.DataFrame,
    summary: dict[str, object],
    price_stats: dict[str, object],
    parity: dict[str, float],
) -> str:
    key = comparison[
        comparison["candidate"].isin(["no_put", PRIMARY, LEGACY])
        & comparison["window"].isin(WINDOWS)
    ][
        [
            "layer",
            "candidate",
            "window",
            "available",
            "ann_return",
            "max_dd",
            "ann_return_delta_pp",
            "max_dd_improvement_pp",
        ]
    ]
    annual_real = annual[
        annual["layer"].eq("real")
        & annual["candidate"].isin(["no_put", PRIMARY, LEGACY])
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
    real_exposure = exposure[
        exposure["layer"].eq("real")
        & exposure["candidate"].isin([PRIMARY, *WINDOW_NEIGHBORS, *LADDER_NEIGHBORS, LEGACY])
    ]
    primary_switch = switches[
        switches["candidate"].eq(PRIMARY) & switches["layer"].eq("real")
    ]
    primary_churn = churn[churn["candidate"].eq(PRIMARY)]
    primary_stress = stress[
        stress["layer"].eq("real")
        & stress["candidate"].isin(["no_put", PRIMARY])
        & stress["window"].isin(["full", "last_3y", "last_1y"])
    ][["candidate", "window", "cost_multiplier", "ann_return", "max_dd"]]
    return "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            "> 本版只测试v7新估值档位直接映射三个月95%月换MO Put；不含MOM120、网格或卖Call。未批准实盘。",
            "",
            "## 结论",
            "",
            f"- 决定：`{summary['conclusion']}`；稳定性：`{summary['stability_label']}`。",
            f"- 门槛：`{json.dumps(summary['gates'], ensure_ascii=False)}`。",
            f"- 模型/真实全样本MaxDD改善：{summary['model_full_dd_improvement_pp']:.4f}pp / {summary['real_full_dd_improvement_pp']:.4f}pp。",
            f"- 真实2026信号调整年化：{summary['real_2026_annualized_signal_adjustments']:.2f}次。",
            f"- 真实全样本相对no-Put年化：1倍成本{summary['real_full_ann_return_delta_1x_pp']:.4f}pp，2倍成本{summary['real_full_ann_return_delta_2x_pp']:.4f}pp。",
            "",
            "## 强制窗口",
            "",
            key.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 真实逐年",
            "",
            annual_real.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 暴露与宽度",
            "",
            real_exposure.to_markdown(index=False, floatfmt=".6f"),
            "",
            width.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 2026切换与真实交易",
            "",
            primary_switch.to_markdown(index=False, floatfmt=".6f"),
            "",
            primary_churn.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 成本敏感性",
            "",
            primary_stress.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 完整性",
            "",
            f"- 真实MO成交腿：{price_stats['trade_legs']}；收盘价最大误差：{price_stats['max_close_price_error']:.3e}。",
            f"- v9 no-Put/旧规则逐日奇偶：`{json.dumps(parity, ensure_ascii=False)}`。",
            "- T日收盘估值、T+1共同交易日收盘成交；真实MO日终按官方结算价盯市。",
            "- 模型层2015年起为中证1000全收益+理论Put，不是上市前IM/MO；真实层始于2022-07-22，10年/5年N/A。",
            "- close不是买卖价、close VWAP或容量证明；无bid/ask和盘口冲击数据。",
            "",
        ]
    )


def update_scan_artifacts(
    scan_summary: pd.DataFrame,
    window_metrics: pd.DataFrame,
    definitions: pd.DataFrame,
    summary: dict[str, object],
    source_hashes: dict[str, str],
    record: str,
) -> None:
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    command_text = (
        "python -m pytest test_im_mo_adaptive_valuation_tier_put_v10.py -q\n"
        "python im_mo_adaptive_valuation_tier_put_v10.py\n"
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command_text)
    path = SCAN / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"candidate": "no_put", "source": "v9 same-path parity"},
            "candidate_grid": definitions.to_dict("records"),
            "data_snapshot": {
                "model": [str(v6.MODEL_START.date()), str(v6.END.date())],
                "real": [str(v6.REAL_START.date()), str(v6.END.date())],
                "valuation": ["2015-10-19", str(v6.END.date())],
                "timezone": "Asia/Shanghai",
                "im_source": str(Path(v5.IM_QUOTES).relative_to(ROOT)),
                "mo_source": str(Path(v4.OPTIONS).relative_to(ROOT)),
                "valuation_source": str(V7_STATES.relative_to(ROOT)),
            },
            "cost_model": {
                "mo_per_contract_side": v4.MO_CONTRACT_SIDE_COST,
                "cost_multipliers": list(COST_MULTIPLIERS),
                "cash_weight": v6.CASH_WEIGHT,
                "cash_annual_return": 0.03,
                "signal": "T close",
                "put_execution": "T+1 official daily close",
                "mark": "official settle",
                "slippage_and_bid_ask": "excluded; 2x/5x fee sensitivity only",
            },
            "source_hashes": source_hashes,
            "research_conclusion": summary,
            "warnings": [
                "model layer is CSI1000 TRI plus theoretical Put, not pre-listing IM/MO",
                "real 10y/5y scan rows are clipped numeric placeholders; formal metrics are N/A",
                "daily close is not bid/ask or close VWAP",
                "2022-2024 v7 valuation tier is intentionally zero",
                "not live approved",
            ],
        }
    )
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    pinned_hashes = verify_inputs(require_fresh_output=True)
    definitions = candidate_definitions()
    states = load_v7_states()

    market, market_checks = v6.model_market()
    model_base = v6.model_baseline(market)
    upstream, _, _, valuation_states, tri, raw_options = v4.load_inputs()
    daily_valuation, feature_diffs = v4.build_daily_valuation()
    if max(feature_diffs.values()) > 1e-14:
        raise RuntimeError(f"Frozen valuation feature parity failed: {feature_diffs}")
    legacy_state = v6.signal_state(daily_valuation)

    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    schedules: dict[tuple[str, str], pd.DataFrame] = {}
    for label, source in V7_SOURCE_MAP.items():
        schedules[("model", label)] = build_v7_schedule(states, source, label, model_dates)
        schedules[("real", label)] = build_v7_schedule(states, source, label, real_dates)
    schedules[("model", LEGACY)] = build_legacy_schedule(model_dates, legacy_state)
    schedules[("real", LEGACY)] = build_legacy_schedule(real_dates, legacy_state)

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
    daily = add_nav(pd.concat([model_daily, real_daily], ignore_index=True))
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False)
    schedules_frame = pd.concat(
        [frame.assign(layer=layer, schedule_candidate=candidate) for (layer, candidate), frame in schedules.items()],
        ignore_index=True,
        sort=False,
    )

    expected = set(definitions["candidate"]) | {"no_put"}
    for layer in ["model", "real"]:
        subset = daily[daily["layer"].eq(layer)]
        if set(subset["candidate"]) != expected:
            raise RuntimeError(f"Incomplete {layer} candidate set")
        if subset.duplicated(["candidate", "date"]).any():
            raise RuntimeError(f"Duplicate {layer} candidate/date")
        if subset[["ret", "cash_ret", "cash_nav"]].isna().any().any():
            raise RuntimeError(f"Missing {layer} return or NAV")

    parity = parity_checks(daily)
    price_audit, price_stats = price_integrity(trades, raw_options)
    formal, annual = v6.metrics_tables(daily)
    comparison = baseline_comparison(formal)
    annual_compare = annual_comparison(annual)
    exposure = v6.exposure_table(daily, trades)
    stress_daily, stress_metrics = cost_sensitivity(daily)
    switches = state_switch_audit(schedules, daily)
    churn = trade_churn_audit(trades, daily)
    width, decision = make_decision(
        comparison, exposure, switches, churn, stress_metrics
    )
    scan_summary, window_metrics = v8.scan_tables(daily, definitions)

    record = make_record(
        comparison,
        annual_compare,
        exposure,
        switches,
        churn,
        stress_metrics,
        width,
        decision,
        price_stats,
        parity,
    )

    source_paths = [
        SPEC,
        Path(__file__),
        Path(v8.__file__),
        Path(v6.__file__),
        Path(v4.__file__),
        Path(v5.__file__),
        V7_STATES,
        V7_MANIFEST,
        V9_DAILY,
        V9_MANIFEST,
        Path(v4.OPTIONS),
        Path(v5.IM_QUOTES),
        Path(v4.UPSTREAM),
    ]
    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path) for path in source_paths
    }
    source_hashes.update(pinned_hashes)

    STAGING.mkdir(parents=True, exist_ok=False)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    comparison.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual_compare.to_csv(STAGING / "annual_metrics.csv", index=False)
    exposure.to_csv(STAGING / "exposure_cost.csv", index=False)
    definitions.to_csv(STAGING / "candidate_definitions.csv", index=False)
    schedules_frame.to_csv(STAGING / "signal_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(STAGING / "lifecycle_audit.csv", index=False)
    price_audit.to_csv(STAGING / "close_price_integrity_audit.csv", index=False)
    switches.to_csv(STAGING / "state_switch_audit.csv", index=False)
    churn.to_csv(STAGING / "trade_churn_audit.csv", index=False)
    stress_daily.to_csv(STAGING / "cost_stress_daily.csv.gz", index=False, compression="gzip")
    stress_metrics.to_csv(STAGING / "cost_stress_metrics.csv", index=False)
    width.to_csv(STAGING / "neighbor_width_audit.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    command_text = (
        "python -m pytest test_im_mo_adaptive_valuation_tier_put_v10.py -q\n"
        "python im_mo_adaptive_valuation_tier_put_v10.py\n"
    )
    (STAGING / "command_log.txt").write_text(command_text, encoding="utf-8")
    manifest: dict[str, Any] = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "samples": {
            "model": [str(v6.MODEL_START.date()), str(v6.END.date())],
            "real": [str(v6.REAL_START.date()), str(v6.END.date())],
            "valuation": [str(states["date"].min().date()), str(states["date"].max().date())],
        },
        "candidate_count": int(len(definitions)),
        "candidate_count_with_baseline_per_layer": int(len(expected)),
        "execution": {
            "signal": "T close",
            "put_transaction": "T+1 official daily close",
            "put_structure": "3m monthly exit 95% moneyness",
            "mark": "official settle",
            "tier_quantity": "0/1/2/3 MO contracts per 1 IM",
            "slippage_and_bid_ask": "excluded",
        },
        "market_checks": market_checks,
        "valuation_feature_checks": feature_diffs,
        "parity": parity,
        "price_integrity": price_stats,
        "decision": decision,
        "research_status": "research_only_not_live_approved",
        "git_status": git_status(),
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    STAGING.rename(OUTPUT)
    update_scan_artifacts(
        scan_summary,
        window_metrics,
        definitions,
        decision,
        source_hashes,
        record,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
