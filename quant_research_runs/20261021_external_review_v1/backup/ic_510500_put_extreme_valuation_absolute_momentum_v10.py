from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_extreme_valuation_gate_v9 as v9


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_extreme_valuation_absolute_momentum_v10"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "c41e97e4ebb15749ecf39702b162818a75410f42101aefed7bb7e88dc360c969"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260817_ic_510500_put_extreme_valuation_absolute_momentum_v10"

V9_PATH = Path(v9.__file__).resolve()
V9_SHA256 = "c871e72343bca8996fdcc285d9b50970b8c74dc4f8fd2fc29407a7a0abc4025d"
V9_MANIFEST = v9.OUTPUT / "data_manifest.json"

FIXED_THRESHOLD = 1.75
ECONOMIC_VARIANTS: dict[str, tuple[int, float]] = {
    "or_mom60_000": (60, 0.00),
    "or_mom120_m050": (120, -0.05),
    "or_mom120_000": (120, 0.00),
    "or_mom120_p050": (120, 0.05),
    "or_mom240_000": (240, 0.00),
}
ATTRIBUTION_VARIANTS = ["fixed175_only", "mom120_only"]
SIGNAL_VARIANTS = [*ATTRIBUTION_VARIANTS, *ECONOMIC_VARIANTS]
GRID_VARIANTS = [
    "no_put",
    "v7_stress_latch_front",
    "v7_stress_latch_hold3m",
    "hold3m_always_100",
    *[f"hold3m_{signal}" for signal in SIGNAL_VARIANTS],
]
REQUIRED_SEGMENTS = list(v9.REQUIRED_SEGMENTS)
EXTRA_WINDOWS = list(v9.EXTRA_WINDOWS)
PAYOUT_WINDOWS = dict(v9.PAYOUT_WINDOWS)
PRIMARY = "or_mom120_000"


def sha256(path: Path) -> str:
    return v9.sha256(path)


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v10 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v10 specification sidecar mismatch")
    if sha256(V9_PATH) != V9_SHA256:
        raise RuntimeError("Frozen v9 dependency changed")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads(V9_MANIFEST.read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V9_SHA256 or manifest["spec_sha256"] != v9.SPEC_SHA256:
        raise RuntimeError("v9 formal manifest dependency mismatch")
    for relative, expected in manifest["source_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"v9 frozen input changed: {relative}")
    return manifest


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {
            "execution_mode": "none",
            "signal_variant": "no_put",
            "signal_family": "baseline",
            "momentum_horizon": np.nan,
            "momentum_threshold": np.nan,
        }
    controls = {
        "v7_stress_latch_front": ("front_original", "stress_latch", "v7_benchmark"),
        "v7_stress_latch_hold3m": ("3m_hold_expiry", "stress_latch", "v7_benchmark"),
        "hold3m_always_100": ("3m_hold_expiry", "always_100", "engine_control"),
    }
    if grid_variant in controls:
        execution, signal, family = controls[grid_variant]
        return {
            "execution_mode": execution,
            "signal_variant": signal,
            "signal_family": family,
            "momentum_horizon": np.nan,
            "momentum_threshold": np.nan,
        }
    if not grid_variant.startswith("hold3m_"):
        raise ValueError(f"Unknown v10 grid variant: {grid_variant}")
    signal = grid_variant[len("hold3m_") :]
    if signal == "fixed175_only":
        family, horizon, threshold = "fixed_control", np.nan, np.nan
    elif signal == "mom120_only":
        family, horizon, threshold = "momentum_control", 120, 0.0
    elif signal in ECONOMIC_VARIANTS:
        horizon, threshold = ECONOMIC_VARIANTS[signal]
        family = "fixed_or_absolute_momentum"
    else:
        raise ValueError(f"Unknown v10 signal: {signal}")
    return {
        "execution_mode": "3m_hold_expiry",
        "signal_variant": signal,
        "signal_family": family,
        "momentum_horizon": horizon,
        "momentum_threshold": threshold,
    }


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, grid_variant = candidate.split("_", 1)
    return {"layer": layer, "grid_variant": grid_variant, **variant_parameters(grid_variant)}


def configure_metrics() -> None:
    core = v9.v7.core
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.VARIANTS = [value for value in GRID_VARIANTS if value != "no_put"]
    core.ALL_VARIANTS = GRID_VARIANTS
    core.ECON_VARIANTS = [f"hold3m_{value}" for value in ECONOMIC_VARIANTS]
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.segment_slice = v9.v5.segment_slice
    core.v2.candidate_parts = candidate_parts


def momentum_score_frame(daily_valuation: pd.DataFrame) -> pd.DataFrame:
    frame = v9.valuation_score_frame(daily_valuation)
    for horizon in sorted({value[0] for value in ECONOMIC_VARIANTS.values()}):
        frame[f"momentum_{horizon}"] = frame["tri_close"] / frame["tri_close"].shift(horizon) - 1.0
    return frame


def signal_target(signal: str, fixed_risk: float, momentum: dict[int, float]) -> float:
    fixed = fixed_risk + 1e-12 >= FIXED_THRESHOLD
    if signal == "fixed175_only":
        return float(fixed)
    if signal == "mom120_only":
        return float(momentum[120] <= 0.0 + 1e-12)
    horizon, threshold = ECONOMIC_VARIANTS[signal]
    return float(fixed or momentum[horizon] <= threshold + 1e-12)


def build_signal_panel(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = momentum_score_frame(daily_valuation).set_index("date")
    trade_dates = pd.DatetimeIndex(ic["date"])
    evals = {
        "model": v9.proxy.evaluation_dates(
            "daily", v9.v7.core.MODEL_START, v9.v7.core.END, trade_dates, daily_valuation
        ),
        "real": v9.proxy.evaluation_dates(
            "daily", v9.v7.core.REAL_START, v9.v7.core.END, trade_dates, daily_valuation
        ),
    }
    unique_days = sorted(set(evals["model"]) | set(evals["real"]) | {v9.v7.core.END})
    signal_rows: list[dict[str, object]] = []
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {signal: {} for signal in SIGNAL_VARIANTS}
    for day in unique_days:
        row = frame.loc[day]
        momentum = {horizon: float(row[f"momentum_{horizon}"]) for horizon in [60, 120, 240]}
        for signal in SIGNAL_VARIANTS:
            target = signal_target(signal, float(row["fixed_risk"]), momentum)
            target_lookup[signal][day] = target
            params = variant_parameters(f"hold3m_{signal}")
            signal_rows.append(
                {
                    "signal_variant": signal,
                    "eval_date": day,
                    "signal_family": params["signal_family"],
                    "momentum_horizon": params["momentum_horizon"],
                    "momentum_threshold": params["momentum_threshold"],
                    "pe_aggregate_ttm": float(row["pe_aggregate_ttm"]),
                    "pb_aggregate": float(row["pb_aggregate"]),
                    "erp": float(row["erp"]),
                    "trailing_dividend_contribution": float(row["trailing_dividend_contribution"]),
                    "fixed_risk": float(row["fixed_risk"]),
                    "momentum_60": momentum[60],
                    "momentum_120": momentum[120],
                    "momentum_240": momentum[240],
                    "target_fraction": target,
                    "current_state_only": bool(day == v9.v7.core.END),
                }
            )
    schedule_rows: list[dict[str, object]] = []
    for layer, evaluations in evals.items():
        start = v9.v7.core.MODEL_START if layer == "model" else v9.v7.core.REAL_START
        for signal in SIGNAL_VARIANTS:
            for sequence, day in enumerate(evaluations):
                execution, initial = v9.proxy.next_execution(day, start, trade_dates)
                params = variant_parameters(f"hold3m_{signal}")
                schedule_rows.append(
                    {
                        "layer": layer,
                        "frequency": "daily",
                        "signal_variant": signal,
                        "signal_family": params["signal_family"],
                        "sequence": sequence,
                        "eval_date": day,
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": target_lookup[signal][day],
                        "three_tier_target_fraction": target_lookup[signal][day],
                        "fixed_risk": float(frame.loc[day, "fixed_risk"]),
                        "momentum_60": float(frame.loc[day, "momentum_60"]),
                        "momentum_120": float(frame.loc[day, "momentum_120"]),
                        "momentum_240": float(frame.loc[day, "momentum_240"]),
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    ).reset_index(drop=True)
    signals = pd.DataFrame(signal_rows).sort_values(["signal_variant", "eval_date"]).reset_index(drop=True)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v10 signal execution")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v10 signal execution leakage")
    state_summary = v9.signal_state_summary(schedule)
    current = signals[signals["current_state_only"]].copy()
    return schedule, signals, state_summary, current


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
    daily_parts.append(v9.proxy.assemble_candidate(overlay, ic))
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
    roll_dates = v9.v6.forced_roll_dates(frames["ic"])
    daily_parts: list[pd.DataFrame] = [
        v9.proxy.no_put_rows(frames["ic"], v9.v7.core.MODEL_START, "model_no_put"),
        v9.proxy.no_put_rows(frames["ic"], v9.v7.core.REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    lifecycle_parts: list[pd.DataFrame] = []
    for layer in ["model", "real"]:
        stress = v7_schedule[
            v7_schedule["layer"].eq(layer) & v7_schedule["signal_variant"].eq("stress_latch")
        ]
        front_label = f"{layer}_v7_stress_latch_front"
        if layer == "model":
            overlay, trades = v9.proxy.run_model_candidate(
                frames["ic"], stress, market, "daily", "front", "three_tier", 0.85, front_label
            )
        else:
            overlay, trades = v9.proxy.run_real_candidate(
                frames["ic"], stress, frames["snapshots"], frames["histories"], frames["etf500"],
                "daily", "front", "three_tier", front_label,
            )
        _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, None, frames["ic"])

        hold_label = f"{layer}_v7_stress_latch_hold3m"
        if layer == "model":
            overlay, trades, life = v9.v7.run_model_hold_expiry(
                frames["ic"], stress, market, hold_label, roll_dates
            )
        else:
            overlay, trades, life = v9.v7.run_real_hold_expiry(
                frames["ic"], stress, frames["snapshots"], frames["histories"], frames["etf500"],
                hold_label, roll_dates,
            )
        _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])

        permanent = v7_schedule[
            v7_schedule["layer"].eq(layer) & v7_schedule["signal_variant"].eq("always_100")
        ]
        permanent_label = f"{layer}_hold3m_always_100"
        if layer == "model":
            overlay, trades, life = v9.v7.run_model_hold_expiry(
                frames["ic"], permanent, market, permanent_label, roll_dates
            )
        else:
            overlay, trades, life = v9.v7.run_real_hold_expiry(
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
                overlay, trades, life = v9.v7.run_model_hold_expiry(
                    frames["ic"], candidate_schedule, market, label, roll_dates
                )
            else:
                overlay, trades, life = v9.v7.run_real_hold_expiry(
                    frames["ic"], candidate_schedule, frames["snapshots"], frames["histories"],
                    frames["etf500"], label, roll_dates,
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
    frozen = pd.read_csv(v9.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    mapping = {
        "no_put": "no_put",
        "v7_stress_latch_front": "v7_stress_latch_front",
        "v7_stress_latch_hold3m": "v7_stress_latch_hold3m",
        "hold3m_always_100": "hold3m_always_100",
        "hold3m_fixed175_only": "hold3m_fixed_175",
    }
    columns = ["put_pnl_ret", "put_cost_rate", "target_fraction", "ret", "cash_ret"]
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        for current_variant, prior_variant in mapping.items():
            current_label = f"{layer}_{current_variant}"
            prior_label = f"{layer}_{prior_variant}"
            left = daily[daily["candidate"].eq(current_label)][["date", *columns]]
            right = frozen[frozen["candidate"].eq(prior_label)][["date", *columns]]
            joined = left.merge(right, on="date", suffixes=("_v10", "_v9"), validate="one_to_one")
            row: dict[str, object] = {
                "current_candidate": current_label,
                "prior_candidate": prior_label,
                "rows": len(joined),
            }
            for column in columns:
                row[f"max_abs_{column}_diff"] = float(
                    (joined[f"{column}_v10"] - joined[f"{column}_v9"]).abs().max()
                )
            rows.append(row)
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].to_numpy().max() > 1e-14:
        raise RuntimeError("v10/v9 baseline parity failed")
    return table


def lifecycle_audit(lifecycles: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    return v9.lifecycle_audit(lifecycles, trades)


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


def incremental_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    periods = {"full": (v9.v7.core.MODEL_START, v9.v7.core.END), **PAYOUT_WINDOWS}
    for layer in ["model", "real"]:
        starts = v9.v7.core.MODEL_START if layer == "model" else v9.v7.core.REAL_START
        controls = {
            "fixed175_only": f"{layer}_hold3m_fixed175_only",
            "mom120_only": f"{layer}_hold3m_mom120_only",
        }
        for signal in ECONOMIC_VARIANTS:
            candidate = f"{layer}_hold3m_{signal}"
            left = daily[daily["candidate"].eq(candidate)][["date", "cash_ret"]]
            for control_name, control_candidate in controls.items():
                right = daily[daily["candidate"].eq(control_candidate)][["date", "cash_ret"]].rename(
                    columns={"cash_ret": "control_cash_ret"}
                )
                joined = left.merge(right, on="date", validate="one_to_one")
                for period, (start, end) in periods.items():
                    sample = joined[joined["date"].between(max(start, starts), end)]
                    relative = np.log1p(sample["cash_ret"]) - np.log1p(sample["control_cash_ret"])
                    rows.append(
                        {
                            "layer": layer,
                            "signal_variant": signal,
                            "candidate": candidate,
                            "control": control_name,
                            "period": period,
                            "rows": len(sample),
                            "relative_terminal_return": float(np.expm1(relative.sum())) if len(sample) else np.nan,
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
    fixed_base = formal[formal["candidate"].eq("model_hold3m_fixed175_only")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    signal_lookup = state_summary.set_index(["layer", "signal_variant"])
    lifecycle_ok = bool(lifecycle["passed"].all())
    rows: list[dict[str, object]] = []
    for signal, (horizon, threshold) in ECONOMIC_VARIANTS.items():
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
        extra: dict[str, float] = {}
        for segment in EXTRA_WINDOWS:
            extra[f"{segment}_cagr_delta"] = float(
                model_rows.loc[segment, "cash_ann_return"] - model_base.loc[segment, "cash_ann_return"]
            )
            extra[f"{segment}_dd_improvement"] = float(
                model_rows.loc[segment, "cash_max_dd"] - model_base.loc[segment, "cash_max_dd"]
            )
        development_recent_pass = bool(
            extra["development_cagr_delta"] >= -0.01
            and extra["development_dd_improvement"] >= -0.01
            and extra["recent_expansion_cagr_delta"] >= -0.01
            and extra["recent_expansion_dd_improvement"] >= -0.01
        )
        revision_pass = bool(
            extra["revision_validation_cagr_delta"] >= -0.01
            and extra["revision_validation_dd_improvement"] >= 0.03
        )
        fixed_revision_cagr = float(
            model_rows.loc["revision_validation", "cash_ann_return"]
            - fixed_base.loc["revision_validation", "cash_ann_return"]
        )
        fixed_revision_dd = float(
            model_rows.loc["revision_validation", "cash_max_dd"]
            - fixed_base.loc["revision_validation", "cash_max_dd"]
        )
        real_cagr = float(real_rows.loc["full", "cash_ann_return"] - real_base.loc["full", "cash_ann_return"])
        real_dd = float(real_rows.loc["full", "cash_max_dd"] - real_base.loc["full", "cash_max_dd"])
        model_days = int(exposure_lookup.loc[model_candidate, "protected_days"])
        real_days = int(exposure_lookup.loc[real_candidate, "protected_days"])
        episodes = int(signal_lookup.loc[("model", signal), "activation_episodes"])
        known_signal = float(
            signal_lookup.loc[("model", signal), "known_drawdown_signal_ratio"]
        )
        single = bool(
            dd_improvement["full"] >= 0.03
            and cagr_delta["full"] >= -0.01
            and revision_pass
            and sum(value > 1e-12 for value in dd_improvement.values()) >= 3
            and return_pass
            and development_recent_pass
            and fixed_revision_dd >= 0.03
            and fixed_revision_cagr >= -0.01
            and model_days >= 20
            and episodes >= 2
            and known_signal >= 0.50
            and real_days >= 20
            and real_dd >= 0.005
            and real_cagr >= -0.01
        )
        rows.append(
            {
                "signal_variant": signal,
                "momentum_horizon": horizon,
                "momentum_threshold": threshold,
                "full_cagr_delta": cagr_delta["full"],
                "full_dd_improvement": dd_improvement["full"],
                "revision_cagr_delta": extra["revision_validation_cagr_delta"],
                "revision_dd_improvement": extra["revision_validation_dd_improvement"],
                "fixed_revision_cagr_delta": fixed_revision_cagr,
                "fixed_revision_dd_improvement": fixed_revision_dd,
                "improved_required_windows": sum(value > 1e-12 for value in dd_improvement.values()),
                "return_tolerance_pass": return_pass,
                **extra,
                "development_recent_pass": development_recent_pass,
                "model_protected_days": model_days,
                "model_activation_episodes": episodes,
                "known_drawdown_signal_ratio": known_signal,
                "real_protected_days": real_days,
                "real_cagr_delta": real_cagr,
                "real_dd_improvement": real_dd,
                "single_candidate_pass": single,
            }
        )
    decisions = pd.DataFrame(rows)
    pass_lookup = decisions.set_index("signal_variant")["single_candidate_pass"].to_dict()
    neighbors = {
        "or_mom60_000": ["or_mom120_000"],
        "or_mom120_m050": ["or_mom120_000"],
        "or_mom120_000": ["or_mom60_000", "or_mom120_m050", "or_mom120_p050", "or_mom240_000"],
        "or_mom120_p050": ["or_mom120_000"],
        "or_mom240_000": ["or_mom120_000"],
    }
    support_rows = []
    for signal in ECONOMIC_VARIANTS:
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
        selected = PRIMARY if PRIMARY in set(passed["signal_variant"]) else str(
            passed.sort_values(["real_protected_days", "model_protected_days"]).iloc[0]["signal_variant"]
        )
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if len(passed) >= 3 else "narrow_stable",
            "selected_variant": selected,
            "passing_candidates": passed["signal_variant"].tolist(),
            "sample_reuse": "not_independent_oos",
        }
    return decisions, summary


def enrich_state_summary(schedule: pd.DataFrame, state_summary: pd.DataFrame) -> pd.DataFrame:
    start, end = PAYOUT_WINDOWS["known_drawdown"]
    ratios = (
        schedule[schedule["eval_date"].between(start, end)]
        .groupby(["layer", "signal_variant"])["three_tier_target_fraction"]
        .mean()
        .rename("known_drawdown_signal_ratio")
        .reset_index()
    )
    return state_summary.merge(ratios, on=["layer", "signal_variant"], how="left", validate="one_to_one")


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
        "signal_variant", "momentum_horizon", "momentum_threshold", "full_cagr_delta",
        "full_dd_improvement", "revision_cagr_delta", "revision_dd_improvement",
        "fixed_revision_cagr_delta", "fixed_revision_dd_improvement",
        "improved_required_windows", "known_drawdown_signal_ratio", "real_cagr_delta",
        "real_dd_improvement", "single_candidate_pass", "neighbor_pass", "all_preregistered_pass",
    ]
    current_cols = [
        "signal_variant", "signal_family", "momentum_horizon", "momentum_threshold",
        "pe_aggregate_ttm", "pb_aggregate", "erp", "trailing_dividend_contribution",
        "fixed_risk", "momentum_60", "momentum_120", "momentum_240", "target_fraction",
    ]
    lines = [
        "# IC + 510500 Put 极高估或绝对动量保护 v10",
        "",
        "> 研究回测；未获准实盘；固定极高估1.75或绝对动量触发，3m Put持有到期。",
        "",
        "## 决定", "",
        f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。",
        "",
        "## 模型层强制窗口（含70%现金）", "",
        model.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 真实Put层强制窗口（不足窗口为N/A）", "",
        real.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 预注册判断", "",
        decisions[decision_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 触发与实际保护", "",
        state_summary.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 2026-08-14研究状态", "",
        current[current_cols].to_markdown(index=False, floatfmt=".6f"),
        "",
        "## 生命周期审计", "",
        lifecycle.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 限制", "",
        "- 绝对动量是价格状态识别，不是估值预测；它只能在趋势已经转弱后启动。",
        "- 3m持有到期会把短暂触发放大为较长实际保护，信号比例不等于持仓比例。",
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
    focus = wide[wide["candidate"].str.contains("fixed175|mom|no_put")].copy()
    columns = [
        "candidate", "cash_ann_return_full", "cash_max_dd_full",
        "cash_ann_return_last_10y", "cash_max_dd_last_10y",
        "cash_ann_return_last_5y", "cash_max_dd_last_5y",
        "cash_ann_return_last_3y", "cash_max_dd_last_3y",
        "cash_ann_return_last_1y", "cash_max_dd_last_1y",
    ]
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `20260817_ic_510500_put_extreme_valuation_absolute_momentum_v10`
- Run date: 2026-08-17
- Timezone: Asia/Shanghai
- Operator: Codex
- Project: IC + 510500 ETF Put
- Repo or workspace path: `{ROOT}`
- Version or strategy family: `{VERSION}`
- Sleeve or subsystem: 3m hold-to-expiry extreme valuation or absolute momentum
- Parameter group: absolute momentum horizon and threshold
- Scan type: two_parameter_grid_with_controls
- Target entrypoint: `{Path(__file__).name}`
- Working tree status before: `{git_before}`
- Working tree status after: `{git_after}`

## Research Question

- Baseline: same-run no Put; fixed1.75-only and momentum120-only attribution controls.
- Candidate grid: 60/120/240-day absolute momentum and 120-day -5%/0%/+5% thresholds.
- Primary: fixed1.75 OR 120-day absolute momentum <= 0%.
- Decision target: keep_default / watchlist / rerun_required.
- Source-change rule: research_only_no_production_change.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.
- Promotion threshold: frozen economic gates plus one predefined neighbor; no direct promotion due sample reuse.

## Implementation Anchor

- Official entrypoint: `{Path(__file__).name}`.
- Function path: daily fixed valuation and TRI absolute momentum -> T+1 open -> frozen v7 hold-to-expiry engine.
- Existing loaders/metrics: frozen v9/v7/v5/proxy paths.
- Default values: 100% protection, one 3m batch held to expiry, no early sell/downsize.

## Data Snapshot

- Run timestamp: {datetime.now().astimezone().isoformat(timespec='seconds')}
- Raw valuation/TRI start/end: 2007-01-15 / 2026-08-14.
- Metrics start/end: 2015-04-16 / 2026-08-14.
- Real option start: 2022-09-19.
- Data sources: frozen local index valuation/TRI, IC roll, QVIX proxy, 510500 and option daily bars.
- Cache write risk: none.
- Missing/stale data: real 10Y/5Y unavailable; third-party option bars are not executable quotes.
- Alignment: T close signal, T+1 open execution; Asia/Shanghai IC calendar.

## Cost and Execution Assumptions

- Commission/slippage: frozen IC cost and 1bp per Put side.
- Financing: 70% cash earns 3%; Put mark reduces interest-bearing cash.
- Rebalance: daily signal, one 3m batch held to expiry, no early sell/downsize.
- Leverage: 100% IC notional, 30% margin/buffer, no 3.33x amplification.

## Commands

```powershell
python -m pytest test_ic_510500_put_extreme_valuation_absolute_momentum_v10.py -q
python ic_510500_put_extreme_valuation_absolute_momentum_v10.py
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
- Neighbor rule: predeclared horizon or 120-day threshold adjacency only.
- Cost/data caveat: model Put theoretical; real layer short and third-party.
- Leverage caveat: no 3.33x amplification.

## Decision

- Decision: `{summary['decision']}`.
- Recommended next action: no production change without explicit approval and independent evidence.
"""


def main() -> None:
    git_before = v9.v7.core.git_status()
    v9_manifest = verify_inputs()
    frames = v9.v7.core.v2.load_inputs()
    daily_valuation, valuation_checks = v9.v7.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, state_summary, current = build_signal_panel(frames["ic"], daily_valuation)
    state_summary = enrich_state_summary(schedule, state_summary)
    v7_schedule, _, _, _, _ = v9.v7.build_signal_panel(frames["ic"], daily_valuation)
    market, market_checks = v9.proxy.prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = v9.proxy.qvix_validation(market, frames["q500"])
    if not qvix_stats["passed"]:
        raise RuntimeError("QVIX proxy validation failed")
    daily, trades, lifecycles = run_all_candidates(frames, market, schedule, v7_schedule)
    parity = parity_audit(daily)

    configure_metrics()
    formal, scan_summary, wide = v9.v7.core.metric_outputs(daily)
    annual = v9.v7.core.annual_metrics(daily)
    exposure = v9.v7.core.v2.exposure_summary(daily, trades)
    cross_table, cross_stats = v9.v7.core.real_model_validation(daily)
    concentration = v9.v7.core.event_concentration(daily)
    lifecycle = lifecycle_audit(lifecycles, trades)
    attribution = period_attribution(daily)
    incremental = incremental_attribution(daily)
    decisions, decision_summary = decision_outputs(formal, exposure, state_summary, lifecycle)

    expected = {f"{layer}_{variant}" for layer in ["model", "real"] for variant in GRID_VARIANTS}
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v10 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v10 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid v10 daily return")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes scheduled execution")
    hold_trades = trades[trades["candidate"].str.contains("hold3m")]
    if hold_trades["action"].isin(["open_exit", "open_resize", "open_roll"]).any():
        raise RuntimeError("Hold-to-expiry path sold, downsized, or rolled early")
    economic_schedule = schedule[schedule["signal_variant"].isin(ECONOMIC_VARIANTS)]
    if not economic_schedule["three_tier_target_fraction"].isin([0.0, 1.0]).all():
        raise RuntimeError("v10 economic signal is not binary")
    permanent = exposure[exposure["candidate"].str.endswith("hold3m_always_100")]
    if (permanent["trade_events"] <= 0).any() or (permanent["average_put_mark_fraction"] <= 0).any():
        raise RuntimeError("Permanent Put control is empty")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    lifecycles.to_csv(OUTPUT / "hold_expiry_lifecycles.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "signal_history.csv.gz", index=False, compression="gzip")
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
    incremental.to_csv(OUTPUT / "incremental_attribution.csv", index=False)
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
            "valuation_and_tri_history": ["2007-01-15", str(v9.v7.core.END.date())],
            "model": [str(v9.v7.core.MODEL_START.date()), str(v9.v7.core.END.date())],
            "real": [str(v9.v7.core.REAL_START.date()), str(v9.v7.core.END.date())],
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
            "v9_gate": {"path": str(V9_PATH.relative_to(ROOT)), "sha256": V9_SHA256},
        },
        "source_hashes": v9_manifest["source_hashes"],
        "git_status": v9.v7.core.git_status(),
        "warnings": [
            "The full history has been reused and is not independent OOS.",
            "Absolute momentum reacts after price deterioration and does not predict the peak.",
            "Model Put is theoretical; actual bars are not executable quote proof.",
            "Current signal is research-only and not an order.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    commands = (
        "python.exe -m pytest test_ic_510500_put_extreme_valuation_absolute_momentum_v10.py -q\n"
        "python.exe ic_510500_put_extreme_valuation_absolute_momentum_v10.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(commands, encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\n" + commands)
    git_after = v9.v7.core.git_status()
    (SCAN / "record.md").write_text(
        build_scan_record(decision_summary, wide, git_before, git_after), encoding="utf-8"
    )
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "two_parameter_grid_with_controls",
            "baseline": {
                "candidate": "model_no_put",
                "same_run": True,
                "layer_baseline": "model_hold3m_fixed175_only",
            },
            "candidate_grid": [
                {"signal": signal, "horizon": horizon, "threshold": threshold}
                for signal, (horizon, threshold) in ECONOMIC_VARIANTS.items()
            ],
            "data_snapshot": manifest["sample"],
            "cost_model": {
                "put_side_cost": v9.proxy.PUT_FULL_SIDE_COST,
                "cash_weight": v9.proxy.CASH_WEIGHT,
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
