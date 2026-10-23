from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_absolute_valuation_stress_v5 as v5
import ic_510500_put_persistent_stress_hold3m_v7 as v7
import ic_510500_put_proxy_validation_v1 as proxy
import ic_510500_put_v4_monthly_tenor_rerun_v6 as v6


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_extreme_valuation_gate_v9"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "f32adb7d2c3af9765f718fe5d646c41d63cd2e1032316f5cb0a7c49bc9d27a31"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260817_ic_510500_put_extreme_valuation_gate_v9"

V7_PATH = Path(v7.__file__).resolve()
V7_SHA256 = "467f7e8993cab2f4b7ab5ddd7e9253b1b515b63550d324f79104d08ff380bee0"
V5_PATH = Path(v5.__file__).resolve()
V5_SHA256 = v7.V5_SHA256
V6_PATH = Path(v6.__file__).resolve()
V6_SHA256 = v7.V6_SHA256
PROXY_PATH = Path(proxy.__file__).resolve()
PROXY_SHA256 = v7.PROXY_SHA256

FIXED_THRESHOLDS = {"fixed_150": 1.50, "fixed_175": 1.75, "fixed_200": 2.00}
DYNAMIC_THRESHOLDS = {"dynamic_075": 0.75, "dynamic_080": 0.80, "dynamic_085": 0.85}
SIGNAL_VARIANTS = [*FIXED_THRESHOLDS, *DYNAMIC_THRESHOLDS]
GRID_VARIANTS = [
    "no_put",
    "v7_stress_latch_front",
    "v7_stress_latch_hold3m",
    "hold3m_always_100",
    *[f"hold3m_{signal}" for signal in SIGNAL_VARIANTS],
]
REQUIRED_SEGMENTS = list(v7.core.REQUIRED_WINDOWS)
EXTRA_WINDOWS = list(v5.EXTRA_WINDOWS)
PAYOUT_WINDOWS = dict(v7.PAYOUT_WINDOWS)
DYNAMIC_YEARS = 8


def sha256(path: Path) -> str:
    return v7.sha256(path)


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v9 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v9 specification sidecar mismatch")
    for path, expected in [
        (V7_PATH, V7_SHA256),
        (V5_PATH, V5_SHA256),
        (V6_PATH, V6_SHA256),
        (PROXY_PATH, PROXY_SHA256),
    ]:
        if sha256(path) != expected:
            raise RuntimeError(f"Frozen dependency changed: {path.name}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads((v7.OUTPUT / "data_manifest.json").read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V7_SHA256 or manifest["spec_sha256"] != v7.SPEC_SHA256:
        raise RuntimeError("v7 formal manifest dependency mismatch")
    for relative, expected in manifest["source_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"v7 frozen input changed: {relative}")
    return manifest


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {
            "execution_mode": "none",
            "signal_variant": "no_put",
            "valuation_family": "baseline",
            "threshold": np.nan,
        }
    controls = {
        "v7_stress_latch_front": ("front_original", "stress_latch", "v7_benchmark", np.nan),
        "v7_stress_latch_hold3m": ("3m_hold_expiry", "stress_latch", "v7_benchmark", np.nan),
        "hold3m_always_100": ("3m_hold_expiry", "always_100", "engine_control", np.nan),
    }
    if grid_variant in controls:
        execution, signal, family, threshold = controls[grid_variant]
        return {
            "execution_mode": execution,
            "signal_variant": signal,
            "valuation_family": family,
            "threshold": threshold,
        }
    if grid_variant.startswith("hold3m_"):
        signal = grid_variant[len("hold3m_") :]
        if signal in FIXED_THRESHOLDS:
            return {
                "execution_mode": "3m_hold_expiry",
                "signal_variant": signal,
                "valuation_family": "fixed_absolute",
                "threshold": FIXED_THRESHOLDS[signal],
            }
        if signal in DYNAMIC_THRESHOLDS:
            return {
                "execution_mode": "3m_hold_expiry",
                "signal_variant": signal,
                "valuation_family": "dynamic_8y",
                "threshold": DYNAMIC_THRESHOLDS[signal],
            }
    raise ValueError(f"Unknown v9 grid variant: {grid_variant}")


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, grid_variant = candidate.split("_", 1)
    return {"layer": layer, "grid_variant": grid_variant, **variant_parameters(grid_variant)}


def configure_metrics() -> None:
    core = v7.core
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.VARIANTS = [value for value in GRID_VARIANTS if value != "no_put"]
    core.ALL_VARIANTS = GRID_VARIANTS
    core.ECON_VARIANTS = [f"hold3m_{value}" for value in SIGNAL_VARIANTS]
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.segment_slice = v5.segment_slice
    core.v2.candidate_parts = candidate_parts


def rolling_dynamic_score(daily: pd.DataFrame, years: int = DYNAMIC_YEARS) -> pd.Series:
    frame = daily.sort_values("date").reset_index(drop=True)
    dates = frame["date"].to_numpy(dtype="datetime64[ns]")
    pb = frame["pb_aggregate"].to_numpy(float)
    erp = frame["erp"].to_numpy(float)
    dividend = frame["trailing_dividend_contribution"].to_numpy(float)
    scores = np.full(len(frame), np.nan)
    for i, day in enumerate(frame["date"]):
        lower_day = np.datetime64(day - pd.DateOffset(years=years))
        left = int(np.searchsorted(dates, lower_day, side="left"))
        count = i - left + 1
        pb_risk = np.count_nonzero(pb[left : i + 1] <= pb[i]) / count
        erp_risk = np.count_nonzero(erp[left : i + 1] >= erp[i]) / count
        dividend_risk = np.count_nonzero(dividend[left : i + 1] >= dividend[i]) / count
        scores[i] = 0.25 * pb_risk + 0.50 * erp_risk + 0.25 * dividend_risk
    return pd.Series(scores, index=frame.index)


def valuation_score_frame(daily_valuation: pd.DataFrame) -> pd.DataFrame:
    frame = daily_valuation.sort_values("date").reset_index(drop=True).copy()
    states = frame.apply(v5.absolute_state, axis=1, result_type="expand")
    frame["fixed_risk"] = states["absolute_risk"].astype(float)
    frame["dynamic_risk"] = rolling_dynamic_score(frame)
    return frame


def signal_target(signal: str, fixed_risk: float, dynamic_risk: float) -> float:
    if signal in FIXED_THRESHOLDS:
        return float(fixed_risk + 1e-12 >= FIXED_THRESHOLDS[signal])
    if signal in DYNAMIC_THRESHOLDS:
        return float(dynamic_risk + 1e-12 >= DYNAMIC_THRESHOLDS[signal])
    raise ValueError(signal)


def build_signal_panel(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = valuation_score_frame(daily_valuation).set_index("date")
    trade_dates = pd.DatetimeIndex(ic["date"])
    evals = {
        "model": proxy.evaluation_dates("daily", v7.core.MODEL_START, v7.core.END, trade_dates, daily_valuation),
        "real": proxy.evaluation_dates("daily", v7.core.REAL_START, v7.core.END, trade_dates, daily_valuation),
    }
    unique_days = sorted(set(evals["model"]) | set(evals["real"]) | {v7.core.END})
    signal_rows: list[dict[str, object]] = []
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {signal: {} for signal in SIGNAL_VARIANTS}
    for day in unique_days:
        row = frame.loc[day]
        for signal in SIGNAL_VARIANTS:
            target = signal_target(signal, float(row["fixed_risk"]), float(row["dynamic_risk"]))
            target_lookup[signal][day] = target
            signal_rows.append(
                {
                    "signal_variant": signal,
                    "eval_date": day,
                    "valuation_family": "fixed_absolute" if signal in FIXED_THRESHOLDS else "dynamic_8y",
                    "threshold": FIXED_THRESHOLDS.get(signal, DYNAMIC_THRESHOLDS.get(signal)),
                    "pe_aggregate_ttm": float(row["pe_aggregate_ttm"]),
                    "pb_aggregate": float(row["pb_aggregate"]),
                    "erp": float(row["erp"]),
                    "trailing_dividend_contribution": float(row["trailing_dividend_contribution"]),
                    "fixed_risk": float(row["fixed_risk"]),
                    "dynamic_risk": float(row["dynamic_risk"]),
                    "target_fraction": target,
                    "current_state_only": bool(day == v7.core.END),
                }
            )
    schedule_rows: list[dict[str, object]] = []
    for layer, evaluations in evals.items():
        start = v7.core.MODEL_START if layer == "model" else v7.core.REAL_START
        for signal in SIGNAL_VARIANTS:
            for sequence, day in enumerate(evaluations):
                execution, initial = proxy.next_execution(day, start, trade_dates)
                target = target_lookup[signal][day]
                schedule_rows.append(
                    {
                        "layer": layer,
                        "frequency": "daily",
                        "signal_variant": signal,
                        "sequence": sequence,
                        "eval_date": day,
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": target,
                        "three_tier_target_fraction": target,
                        "fixed_risk": float(frame.loc[day, "fixed_risk"]),
                        "dynamic_risk": float(frame.loc[day, "dynamic_risk"]),
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    ).reset_index(drop=True)
    signals = pd.DataFrame(signal_rows).sort_values(["signal_variant", "eval_date"]).reset_index(drop=True)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v9 signal execution")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v9 signal execution leakage")
    state_summary = signal_state_summary(schedule)
    current = signals[signals["current_state_only"]].copy()
    return schedule, signals, state_summary, current


def signal_state_summary(schedule: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (layer, signal), group in schedule.groupby(["layer", "signal_variant"], sort=False):
        target = group.sort_values("eval_date")["three_tier_target_fraction"].astype(float)
        starts = target.eq(1.0) & ~target.shift(fill_value=0.0).eq(1.0)
        rows.append(
            {
                "layer": layer,
                "signal_variant": signal,
                "evaluations": len(group),
                "active_evaluations": int(target.eq(1.0).sum()),
                "active_ratio": float(target.mean()),
                "activation_episodes": int(starts.sum()),
                "first_active_eval": group.loc[target.eq(1.0), "eval_date"].min() if target.eq(1.0).any() else pd.NaT,
                "last_active_eval": group.loc[target.eq(1.0), "eval_date"].max() if target.eq(1.0).any() else pd.NaT,
            }
        )
    return pd.DataFrame(rows)


def _append_candidate(
    daily_parts: list[pd.DataFrame],
    trade_parts: list[pd.DataFrame],
    lifecycle_parts: list[pd.DataFrame],
    overlay: pd.DataFrame,
    trades: pd.DataFrame,
    lifecycles: pd.DataFrame | None,
    ic: pd.DataFrame,
) -> None:
    if "signal_target_fraction" not in overlay:
        overlay["signal_target_fraction"] = overlay["target_fraction"]
    daily_parts.append(proxy.assemble_candidate(overlay, ic))
    if not trades.empty:
        trade_parts.append(trades)
    if lifecycles is not None and not lifecycles.empty:
        lifecycle_parts.append(lifecycles)


def run_all_candidates(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
    v7_schedule: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    roll_dates = v6.forced_roll_dates(frames["ic"])
    daily_parts: list[pd.DataFrame] = [
        proxy.no_put_rows(frames["ic"], v7.core.MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], v7.core.REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    lifecycle_parts: list[pd.DataFrame] = []
    for layer in ["model", "real"]:
        stress = v7_schedule[
            v7_schedule["layer"].eq(layer) & v7_schedule["signal_variant"].eq("stress_latch")
        ]
        front_label = f"{layer}_v7_stress_latch_front"
        if layer == "model":
            overlay, trades = proxy.run_model_candidate(
                frames["ic"], stress, market, "daily", "front", "three_tier", 0.85, front_label
            )
        else:
            overlay, trades = proxy.run_real_candidate(
                frames["ic"], stress, frames["snapshots"], frames["histories"], frames["etf500"],
                "daily", "front", "three_tier", front_label,
            )
        _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, None, frames["ic"])

        hold_label = f"{layer}_v7_stress_latch_hold3m"
        if layer == "model":
            overlay, trades, life = v7.run_model_hold_expiry(
                frames["ic"], stress, market, hold_label, roll_dates
            )
        else:
            overlay, trades, life = v7.run_real_hold_expiry(
                frames["ic"], stress, frames["snapshots"], frames["histories"], frames["etf500"],
                hold_label, roll_dates,
            )
        _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])

        permanent = v7_schedule[
            v7_schedule["layer"].eq(layer) & v7_schedule["signal_variant"].eq("always_100")
        ]
        permanent_label = f"{layer}_hold3m_always_100"
        if layer == "model":
            overlay, trades, life = v7.run_model_hold_expiry(
                frames["ic"], permanent, market, permanent_label, roll_dates
            )
        else:
            overlay, trades, life = v7.run_real_hold_expiry(
                frames["ic"], permanent, frames["snapshots"], frames["histories"], frames["etf500"],
                permanent_label, roll_dates,
            )
        _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])

        for signal in SIGNAL_VARIANTS:
            candidate_schedule = schedule[
                schedule["layer"].eq(layer) & schedule["signal_variant"].eq(signal)
            ]
            label = f"{layer}_hold3m_{signal}"
            if layer == "model":
                overlay, trades, life = v7.run_model_hold_expiry(
                    frames["ic"], candidate_schedule, market, label, roll_dates
                )
            else:
                overlay, trades, life = v7.run_real_hold_expiry(
                    frames["ic"], candidate_schedule, frames["snapshots"], frames["histories"], frames["etf500"],
                    label, roll_dates,
                )
            _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])

    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    ).reset_index(drop=True)
    daily["signal_target_fraction"] = daily["signal_target_fraction"].fillna(daily["target_fraction"])
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(lifecycle_parts, ignore_index=True, sort=False)
    return daily, trades, lifecycles


def parity_audit(daily: pd.DataFrame) -> pd.DataFrame:
    frozen = pd.read_csv(v7.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    mapping = {
        "no_put": "no_put",
        "v7_stress_latch_front": "front_original_stress_latch",
        "v7_stress_latch_hold3m": "3m_hold_expiry_stress_latch",
        "hold3m_always_100": "3m_hold_expiry_always_100",
    }
    columns = ["put_pnl_ret", "put_cost_rate", "target_fraction", "ret", "cash_ret"]
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        for current_variant, prior_variant in mapping.items():
            current_label = f"{layer}_{current_variant}"
            prior_label = f"{layer}_{prior_variant}"
            left = daily[daily["candidate"].eq(current_label)][["date", *columns]]
            right = frozen[frozen["candidate"].eq(prior_label)][["date", *columns]]
            joined = left.merge(right, on="date", suffixes=("_v9", "_v7"), validate="one_to_one")
            row: dict[str, object] = {
                "current_candidate": current_label,
                "prior_candidate": prior_label,
                "rows": len(joined),
            }
            for column in columns:
                row[f"max_abs_{column}_diff"] = float(
                    (joined[f"{column}_v9"] - joined[f"{column}_v7"]).abs().max()
                )
            rows.append(row)
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].to_numpy().max() > 1e-14:
        raise RuntimeError("v9/v7 baseline parity failed")
    return table


def lifecycle_audit(lifecycles: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        candidate = f"{layer}_hold3m_always_100"
        life = lifecycles[lifecycles["candidate"].eq(candidate)].copy()
        complete = life[life["completed"].astype(bool)].copy()
        evaluated = complete.copy()
        if layer == "model":
            evaluated = evaluated[pd.to_datetime(evaluated["entry_date"]) > v7.core.MODEL_START]
        coverage = float(evaluated["ic_rolls_covered"].eq(3).mean()) if len(evaluated) else 0.0
        trade = trades[trades["candidate"].eq(candidate)].copy()
        renewal_mask = trade["renewal"].fillna(False).astype(bool) if len(trade) else pd.Series(dtype=bool)
        renewals = trade[renewal_mask] if len(trade) else trade
        max_delay = int(renewals["delay_trading_days"].fillna(0).max()) if len(renewals) else 0
        early = int(life["early_exit"].fillna(False).sum()) if len(life) else 0
        passed = bool(
            len(evaluated)
            and early == 0
            and max_delay <= 5
            and (math.isclose(coverage, 1.0, abs_tol=1e-12) if layer == "model" else coverage >= 0.90)
        )
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "lifecycles": len(life),
                "completed_lifecycles": len(complete),
                "evaluated_completed_lifecycles": len(evaluated),
                "three_ic_roll_ratio": coverage,
                "renewal_trades": len(renewals),
                "max_renewal_delay_trading_days": max_delay,
                "early_exits": early,
                "passed": passed,
            }
        )
    return pd.DataFrame(rows)


def period_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        baseline = daily[daily["candidate"].eq(f"{layer}_no_put")][["date", "cash_ret"]].rename(
            columns={"cash_ret": "baseline_cash_ret"}
        )
        candidates = [
            value for value in daily["candidate"].unique()
            if value.startswith(f"{layer}_") and value != f"{layer}_no_put"
        ]
        for candidate in candidates:
            path = daily[daily["candidate"].eq(candidate)][
                ["date", "cash_ret", "put_pnl_ret", "put_cost_rate", "signal_target_fraction", "target_fraction"]
            ]
            joined = path.merge(baseline, on="date", validate="one_to_one")
            for period, (start, end) in PAYOUT_WINDOWS.items():
                sample = joined[joined["date"].between(start, end)].copy()
                relative = np.log1p(sample["cash_ret"]) - np.log1p(sample["baseline_cash_ret"])
                rows.append(
                    {
                        "candidate": candidate,
                        **candidate_parts(candidate),
                        "period": period,
                        "rows": len(sample),
                        "relative_terminal_return": float(np.expm1(relative.sum())) if len(sample) else np.nan,
                        "put_pnl_ret_sum": float(sample["put_pnl_ret"].sum()) if len(sample) else np.nan,
                        "put_cost_rate_sum": float(sample["put_cost_rate"].sum()) if len(sample) else np.nan,
                        "average_signal_target": float(sample["signal_target_fraction"].mean()) if len(sample) else np.nan,
                        "average_executed_target": float(sample["target_fraction"].mean()) if len(sample) else np.nan,
                    }
                )
    return pd.DataFrame(rows)


def decision_outputs(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    state_summary: pd.DataFrame,
    lifecycle: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model_base = formal[formal["candidate"].eq("model_no_put")].set_index("segment")
    real_base = formal[formal["candidate"].eq("real_no_put")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    signal_lookup = state_summary.set_index(["layer", "signal_variant"])
    lifecycle_ok = bool(lifecycle["passed"].all())
    rows: list[dict[str, object]] = []
    for signal in SIGNAL_VARIANTS:
        model_candidate = f"model_hold3m_{signal}"
        real_candidate = f"real_hold3m_{signal}"
        model_rows = formal[formal["candidate"].eq(model_candidate)].set_index("segment")
        real_rows = formal[formal["candidate"].eq(real_candidate)].set_index("segment")
        cagr_delta = {
            segment: float(model_rows.loc[segment, "cash_ann_return"] - model_base.loc[segment, "cash_ann_return"])
            for segment in REQUIRED_SEGMENTS
        }
        dd_improvement = {
            segment: float(model_rows.loc[segment, "cash_max_dd"] - model_base.loc[segment, "cash_max_dd"])
            for segment in REQUIRED_SEGMENTS
        }
        return_pass = all(
            cagr_delta[segment] >= (-0.01 if segment in {"full", "last_10y", "last_5y"} else -0.03)
            for segment in REQUIRED_SEGMENTS
        )
        segment_cagr_pass = True
        segment_dd_pass = True
        segment_deltas: dict[str, float] = {}
        for segment in EXTRA_WINDOWS:
            cagr = float(
                model_rows.loc[segment, "cash_ann_return"] - model_base.loc[segment, "cash_ann_return"]
            )
            dd = float(
                model_rows.loc[segment, "cash_max_dd"] - model_base.loc[segment, "cash_max_dd"]
            )
            segment_deltas[f"{segment}_cagr_delta"] = cagr
            segment_deltas[f"{segment}_dd_improvement"] = dd
            segment_cagr_pass &= cagr >= -0.01
            segment_dd_pass &= dd >= -0.01
        real_cagr = float(real_rows.loc["full", "cash_ann_return"] - real_base.loc["full", "cash_ann_return"])
        real_dd = float(real_rows.loc["full", "cash_max_dd"] - real_base.loc["full", "cash_max_dd"])
        model_days = int(exposure_lookup.loc[model_candidate, "protected_days"])
        real_days = int(exposure_lookup.loc[real_candidate, "protected_days"])
        episodes = int(signal_lookup.loc[("model", signal), "activation_episodes"])
        real_identified = bool(real_days >= 20)
        single = bool(
            dd_improvement["full"] >= 0.03
            and cagr_delta["full"] >= -0.01
            and sum(value > 1e-12 for value in dd_improvement.values()) >= 3
            and return_pass
            and segment_cagr_pass
            and segment_dd_pass
            and model_days >= 20
            and episodes >= 2
            and real_identified
            and real_dd >= 0.005
            and real_cagr >= -0.01
        )
        rows.append(
            {
                "signal_variant": signal,
                "valuation_family": "fixed_absolute" if signal in FIXED_THRESHOLDS else "dynamic_8y",
                "threshold": FIXED_THRESHOLDS.get(signal, DYNAMIC_THRESHOLDS.get(signal)),
                "full_cagr_delta": cagr_delta["full"],
                "full_dd_improvement": dd_improvement["full"],
                "improved_required_windows": sum(value > 1e-12 for value in dd_improvement.values()),
                "return_tolerance_pass": return_pass,
                **segment_deltas,
                "segment_cagr_pass": segment_cagr_pass,
                "segment_dd_pass": segment_dd_pass,
                "model_protected_days": model_days,
                "model_activation_episodes": episodes,
                "real_protected_days": real_days,
                "real_identified": real_identified,
                "real_cagr_delta": real_cagr,
                "real_dd_improvement": real_dd,
                "single_candidate_pass": single,
            }
        )
    decisions = pd.DataFrame(rows)
    pass_lookup = decisions.set_index("signal_variant")["single_candidate_pass"].to_dict()
    neighbors = {
        "fixed_150": ["fixed_175"],
        "fixed_175": ["fixed_150", "fixed_200"],
        "fixed_200": ["fixed_175"],
        "dynamic_075": ["dynamic_080"],
        "dynamic_080": ["dynamic_075", "dynamic_085"],
        "dynamic_085": ["dynamic_080"],
    }
    support_rows = []
    for signal in SIGNAL_VARIANTS:
        supporting = [value for value in neighbors[signal] if pass_lookup.get(value, False)]
        support_rows.append(
            {
                "signal_variant": signal,
                "supporting_neighbors": ";".join(supporting),
                "neighbor_pass": bool(supporting),
                "all_preregistered_pass": bool(pass_lookup.get(signal, False) and supporting and lifecycle_ok),
            }
        )
    decisions = decisions.merge(pd.DataFrame(support_rows), on="signal_variant", validate="one_to_one")
    passed = decisions[decisions["all_preregistered_pass"]].copy()
    if not lifecycle_ok:
        summary = {
            "decision": "rerun_required",
            "stability_label": "data_sensitive",
            "selected_variant": None,
            "passing_candidates": [],
            "sample_reuse": "not_independent_oos",
        }
    elif passed.empty:
        summary = {
            "decision": "keep_default",
            "stability_label": "reject",
            "selected_variant": None,
            "passing_candidates": [],
            "sample_reuse": "not_independent_oos",
        }
    else:
        selected = str(
            passed.sort_values(["real_protected_days", "model_protected_days", "threshold"], ascending=[True, True, False])
            .iloc[0]["signal_variant"]
        )
        family_counts = passed.groupby("valuation_family").size()
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if int(family_counts.max()) == 3 else "narrow_stable",
            "selected_variant": selected,
            "passing_candidates": passed["signal_variant"].tolist(),
            "sample_reuse": "not_independent_oos",
        }
    return decisions, summary


def display_metrics(formal: pd.DataFrame) -> pd.DataFrame:
    table = formal[formal["segment"].isin(REQUIRED_SEGMENTS)].copy()
    table["cash_cagr"] = table["cash_ann_return"].where(table["available"].astype(bool))
    table["cash_max_dd_display"] = table["cash_max_dd"].where(table["available"].astype(bool))
    return table[
        ["candidate", "layer", "grid_variant", "segment", "available", "cash_cagr", "cash_max_dd_display"]
    ]


def build_record(
    formal: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, object],
    state_summary: pd.DataFrame,
    current: pd.DataFrame,
    lifecycle: pd.DataFrame,
) -> str:
    metrics = display_metrics(formal)
    focus_variants = [
        "no_put",
        "v7_stress_latch_front",
        "v7_stress_latch_hold3m",
        *[f"hold3m_{signal}" for signal in SIGNAL_VARIANTS],
    ]
    model = metrics[metrics["layer"].eq("model") & metrics["grid_variant"].isin(focus_variants)]
    real = metrics[
        metrics["layer"].eq("real")
        & metrics["grid_variant"].isin(["no_put", *[f"hold3m_{signal}" for signal in SIGNAL_VARIANTS]])
    ]
    decision_cols = [
        "signal_variant",
        "valuation_family",
        "threshold",
        "full_cagr_delta",
        "full_dd_improvement",
        "improved_required_windows",
        "real_cagr_delta",
        "real_dd_improvement",
        "real_identified",
        "single_candidate_pass",
        "neighbor_pass",
        "all_preregistered_pass",
    ]
    current_cols = [
        "signal_variant",
        "valuation_family",
        "threshold",
        "pe_aggregate_ttm",
        "pb_aggregate",
        "erp",
        "trailing_dividend_contribution",
        "fixed_risk",
        "dynamic_risk",
        "target_fraction",
    ]
    lines = [
        "# IC + 510500 Put 固定/动态极高估门控 v9",
        "",
        "> 研究回测；未获准实盘；只有极高估开关与3m持有到期，没有压力、概率或Put价格模型。",
        "",
        "## 决定",
        "",
        f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。",
        "",
        "## 模型层强制窗口（含70%现金）",
        "",
        model.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 真实Put层强制窗口（不足窗口为N/A）",
        "",
        real.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 预注册判断",
        "",
        decisions[decision_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 触发与实际保护",
        "",
        state_summary.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 2026-08-14研究状态",
        "",
        current[current_cols].to_markdown(index=False, floatfmt=".6f"),
        "",
        "## 生命周期审计",
        "",
        lifecycle.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 限制",
        "",
        "- 固定风险分数离散，阈值提高不等于保护天数线性下降。",
        "- 动态分数会随8年窗口移动；它解决绝对尺度问题，但仍有制度/窗口依赖。",
        "- 2015—2022为模型Put；真实层是第三方日线，不是可成交盘口证明。",
        "- 当前状态仅用于研究审计，不是订单。",
    ]
    return "\n".join(lines) + "\n"


def build_scan_record(
    summary: dict[str, object],
    wide: pd.DataFrame,
    git_before: str,
    git_after: str,
) -> str:
    focus = wide[wide["candidate"].str.contains("fixed_|dynamic_|no_put")].copy()
    columns = [
        "candidate",
        "cash_ann_return_full",
        "cash_max_dd_full",
        "cash_ann_return_last_10y",
        "cash_max_dd_last_10y",
        "cash_ann_return_last_5y",
        "cash_max_dd_last_5y",
        "cash_ann_return_last_3y",
        "cash_max_dd_last_3y",
        "cash_ann_return_last_1y",
        "cash_max_dd_last_1y",
    ]
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `20260817_ic_510500_put_extreme_valuation_gate_v9`
- Run date: 2026-08-17
- Timezone: Asia/Shanghai
- Operator: Codex
- Project: IC + 510500 ETF Put
- Repo or workspace path: `{ROOT}`
- Version or strategy family: `{VERSION}`
- Sleeve or subsystem: 3m hold-to-expiry extreme valuation gate
- Parameter group: fixed and dynamic extreme valuation thresholds
- Scan type: two_family_candidate_bundle
- Target entrypoint: `{Path(__file__).name}`
- Git branch/commit: local research workspace
- Working tree status before: `{git_before}`
- Working tree status after: `{git_after}`

## Research Question

- Baseline: same-run no Put; exact v7 and permanent Put controls.
- Candidate grid: fixed 1.50/1.75/2.00 and dynamic 8Y 0.75/0.80/0.85.
- Decision target: keep_default / watchlist / rerun_required.
- Source-change rule: research_only_no_production_change.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.
- Required metrics: CAGR, annual volatility, repository Sharpe, maximum drawdown, exposure, costs.
- Promotion threshold: frozen economic gates plus one same-family neighbor.
- Rerun triggers: hash, parity, causality, candidate-set or lifecycle failure.

## Implementation Anchor

- Official entrypoint: `{Path(__file__).name}`.
- Function path: daily valuation score -> T+1 open -> frozen v7 hold-to-expiry engine.
- Existing loaders/metrics: frozen v7/v5/v3/v2/proxy paths.
- Default values: 100% protection, 3m hold-to-expiry, no early exit.

## Data Snapshot

- Run timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}
- Raw data start/end: 2007-01-15 / 2026-08-14.
- Metrics start/end: 2015-04-16 / 2026-08-14.
- Real option start: 2022-09-19.
- Data sources: frozen local index valuation, IC roll, QVIX proxy, 510500 and option daily bars.
- Cache write risk: none.
- Missing/stale data: real 10Y/5Y unavailable; third-party option bars are not executable quotes.
- Alignment: T close signal, T+1 open execution; Asia/Shanghai IC calendar.

## Cost and Execution Assumptions

- Commission/slippage: frozen IC cost and 1bp per Put side.
- Financing: 70% cash earns 3%; Put mark reduces interest-bearing cash.
- Rebalance: daily valuation, one 3m batch held to expiry, no early sell/downsize.
- Leverage: 100% IC notional, 30% margin/buffer, no 3.33x amplification.

## Runtime Override Plan

- Override mechanism: new research harness importing frozen dependencies by hash.
- Values restored after each candidate: yes; no production constants edited.
- Default included in same run: yes.
- Parity against official output: exact v7 daily paths.

## Commands

```powershell
python -m pytest test_ic_510500_put_extreme_valuation_gate_v9.py -q
python ic_510500_put_extreme_valuation_gate_v9.py
```

## Output Files

- `record.md`, `scan_summary.csv`, `window_metrics.csv`, `scan_meta.json`, `command_log.txt`.
- Additional artifacts: `outputs/{VERSION}/`.

## Full-Sample Results

{focus[columns[:3]].to_markdown(index=False, floatfmt='.4f')}

## Window Results

{focus[columns].to_markdown(index=False, floatfmt='.4f')}

## Stability Classification

- Label: `{summary['stability_label']}`.
- Neighbor rule: adjacency only within fixed or dynamic family.
- Cost/data caveat: model Put theoretical; real layer short and third-party.
- Leverage caveat: no 3.33x amplification.

## Decision

- Decision: `{summary['decision']}`.
- Recommended next action: do not promote without explicit approval; do not retune v9 after results.

## User-Facing Summary

Decision, mandatory windows, fixed/dynamic comparison, caveats and next action are in the formal record.
"""


def main() -> None:
    git_before = v7.core.git_status()
    v7_manifest = verify_inputs()
    frames = v7.core.v2.load_inputs()
    daily_valuation, valuation_checks = v7.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, state_summary, current = build_signal_panel(frames["ic"], daily_valuation)
    v7_schedule, _, _, _, _ = v7.build_signal_panel(frames["ic"], daily_valuation)
    market, market_checks = proxy.prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = proxy.qvix_validation(market, frames["q500"])
    if not qvix_stats["passed"]:
        raise RuntimeError("QVIX proxy validation failed")
    daily, trades, lifecycles = run_all_candidates(frames, market, schedule, v7_schedule)
    parity = parity_audit(daily)

    configure_metrics()
    formal, scan_summary, wide = v7.core.metric_outputs(daily)
    annual = v7.core.annual_metrics(daily)
    exposure = v7.core.v2.exposure_summary(daily, trades)
    cross_table, cross_stats = v7.core.real_model_validation(daily)
    concentration = v7.core.event_concentration(daily)
    lifecycle = lifecycle_audit(lifecycles, trades)
    attribution = period_attribution(daily)
    decisions, decision_summary = decision_outputs(formal, exposure, state_summary, lifecycle)

    expected = {f"{layer}_{variant}" for layer in ["model", "real"] for variant in GRID_VARIANTS}
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v9 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v9 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid v9 daily return")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes scheduled execution")
    hold_trades = trades[trades["candidate"].str.contains("hold3m")]
    if hold_trades["action"].isin(["open_exit", "open_resize", "open_roll"]).any():
        raise RuntimeError("Hold-to-expiry path sold, downsized, or rolled early")
    economic_schedule = schedule[schedule["signal_variant"].isin(SIGNAL_VARIANTS)]
    if not economic_schedule["three_tier_target_fraction"].isin([0.0, 1.0]).all():
        raise RuntimeError("v9 economic signal is not binary")
    permanent = exposure[exposure["candidate"].str.endswith("hold3m_always_100")]
    if (permanent["trade_events"] <= 0).any() or (permanent["average_put_mark_fraction"] <= 0).any():
        raise RuntimeError("Permanent Put control is empty")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    lifecycles.to_csv(OUTPUT / "hold_expiry_lifecycles.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "valuation_signals.csv.gz", index=False, compression="gzip")
    state_summary.to_csv(OUTPUT / "signal_state_summary.csv", index=False)
    current.to_csv(OUTPUT / "current_research_signals.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_segment.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    lifecycle.to_csv(OUTPUT / "hold_expiry_lifecycle_audit.csv", index=False)
    attribution.to_csv(OUTPUT / "period_attribution.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "record.md").write_text(
        build_record(formal, decisions, decision_summary, state_summary, current, lifecycle), encoding="utf-8"
    )
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "candidate_count": len(expected),
        "candidate_grid": sorted(expected),
        "sample": {
            "valuation_history": ["2007-01-15", str(v7.core.END.date())],
            "model": [str(v7.core.MODEL_START.date()), str(v7.core.END.date())],
            "real": [str(v7.core.REAL_START.date()), str(v7.core.END.date())],
        },
        "valuation_checks": valuation_checks,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "baseline_parity_max_abs": float(
            parity[[column for column in parity if column.startswith("max_abs_")]].to_numpy().max()
        ),
        "lifecycle_audit_pass": bool(lifecycle["passed"].all()),
        "decision_summary": decision_summary,
        "dependencies": {
            "v7_engine": {"path": str(V7_PATH.relative_to(ROOT)), "sha256": V7_SHA256},
            "v5_valuation": {"path": str(V5_PATH.relative_to(ROOT)), "sha256": V5_SHA256},
            "v6_tenor": {"path": str(V6_PATH.relative_to(ROOT)), "sha256": V6_SHA256},
            "proxy_engine": {"path": str(PROXY_PATH.relative_to(ROOT)), "sha256": PROXY_SHA256},
        },
        "source_hashes": v7_manifest["source_hashes"],
        "git_status": v7.core.git_status(),
        "warnings": [
            "The full history has been reused and is not independent OOS.",
            "Dynamic threshold depends on a fixed eight-year trailing window.",
            "Model Put is theoretical; actual bars are not executable quote proof.",
            "Current signal is research-only and not an order.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    commands = (
        "python.exe -m pytest test_ic_510500_put_extreme_valuation_gate_v9.py -q\n"
        "python.exe ic_510500_put_extreme_valuation_gate_v9.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(commands, encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\n" + commands)
    git_after = v7.core.git_status()
    (SCAN / "record.md").write_text(
        build_scan_record(decision_summary, wide, git_before, git_after), encoding="utf-8"
    )
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "two_family_candidate_bundle",
            "baseline": {"candidate": "model_no_put", "same_run": True},
            "candidate_grid": [
                {"signal": signal, "family": "fixed_absolute", "threshold": value}
                for signal, value in FIXED_THRESHOLDS.items()
            ]
            + [
                {"signal": signal, "family": "dynamic_8y", "threshold": value}
                for signal, value in DYNAMIC_THRESHOLDS.items()
            ],
            "data_snapshot": manifest["sample"],
            "cost_model": {
                "put_side_cost": proxy.PUT_FULL_SIDE_COST,
                "cash_weight": proxy.CASH_WEIGHT,
                "cash_yield": 0.03,
                "ic_notional": 1.0,
            },
            "source_hashes": manifest["source_hashes"],
            "parity_check": manifest["baseline_parity_max_abs"],
            "formal_output": str(OUTPUT.relative_to(ROOT)),
            "outputs": {
                "record": str((SCAN / "record.md").resolve()),
                "scan_summary": str((SCAN / "scan_summary.csv").resolve()),
                "window_metrics": str((SCAN / "window_metrics.csv").resolve()),
                "scan_meta": str((SCAN / "scan_meta.json").resolve()),
                "command_log": str((SCAN / "command_log.txt").resolve()),
            },
            "git_status_before": git_before,
            "git_status_after": git_after,
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(decision_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
