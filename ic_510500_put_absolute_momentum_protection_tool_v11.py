from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

import ic_510500_put_extreme_valuation_absolute_momentum_v10 as v10


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_absolute_momentum_protection_tool_v11"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "3666c8cf8bebcdc25c2e618eaa36d0abaf63fa059145c402035501a9e8ffbbd0"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260817_ic_510500_put_absolute_momentum_protection_tool_v11"

V10_PATH = Path(v10.__file__).resolve()
V10_SHA256 = "026edd9ae292fad3fb6ccaba2d31bd1400920ae91f5c6013f3cdd01fbfc737f4"
V10_MANIFEST = v10.OUTPUT / "data_manifest.json"

EXECUTIONS = ["front_exit", "2m_monthly_exit", "3m_monthly_exit", "3m_hold_expiry"]
MONEYNESS = [0.85, 0.90, 0.95]
GRID_VARIANTS = [
    "no_put",
    *[f"{execution}_m{int(round(moneyness * 100))}" for execution in EXECUTIONS for moneyness in MONEYNESS],
]
REQUIRED_SEGMENTS = list(v10.REQUIRED_SEGMENTS)
EXTRA_WINDOWS = list(v10.EXTRA_WINDOWS)
PAYOUT_WINDOWS = dict(v10.PAYOUT_WINDOWS)
SIGNAL = "or_mom120_000"
BASELINE_VARIANT = "3m_hold_expiry_m85"

proxy = v10.v9.proxy
v6 = v10.v9.v6
v7 = v10.v9.v7
core = v10.v9.v7.core


def sha256(path: Path) -> str:
    return v10.sha256(path)


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v11 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v11 specification sidecar mismatch")
    if sha256(V10_PATH) != V10_SHA256:
        raise RuntimeError("Frozen v10 dependency changed")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads(V10_MANIFEST.read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V10_SHA256 or manifest["spec_sha256"] != v10.SPEC_SHA256:
        raise RuntimeError("v10 formal manifest dependency mismatch")
    for relative, expected in manifest["source_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"v10 frozen input changed: {relative}")
    return manifest


def split_variant(grid_variant: str) -> tuple[str, float]:
    for execution in EXECUTIONS:
        prefix = f"{execution}_m"
        if grid_variant.startswith(prefix):
            return execution, int(grid_variant[len(prefix) :]) / 100.0
    raise ValueError(grid_variant)


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {
            "execution_structure": "none",
            "moneyness_target": np.nan,
            "signal_variant": "no_put",
        }
    execution, moneyness = split_variant(grid_variant)
    return {
        "execution_structure": execution,
        "moneyness_target": moneyness,
        "signal_variant": SIGNAL,
    }


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, grid_variant = candidate.split("_", 1)
    return {"layer": layer, "grid_variant": grid_variant, **variant_parameters(grid_variant)}


def configure_metrics() -> None:
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.VARIANTS = [value for value in GRID_VARIANTS if value != "no_put"]
    core.ALL_VARIANTS = GRID_VARIANTS
    core.ECON_VARIANTS = core.VARIANTS
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.segment_slice = v10.v9.v5.segment_slice
    core.v2.candidate_parts = candidate_parts


def primary_schedule(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    schedule, signals, _, current = v10.build_signal_panel(ic, daily_valuation)
    schedule = schedule[schedule["signal_variant"].eq(SIGNAL)].copy().reset_index(drop=True)
    signals = signals[signals["signal_variant"].eq(SIGNAL)].copy().reset_index(drop=True)
    current = current[current["signal_variant"].eq(SIGNAL)].copy().reset_index(drop=True)
    return schedule, signals, current


def select_real_contract_target(
    snapshots: pd.DataFrame,
    history_lookup: pd.DataFrame,
    day: pd.Timestamp,
    month: pd.Timestamp,
    etf_open: float,
    target: float,
) -> tuple[pd.Series, pd.Series] | None:
    chain = snapshots[(snapshots["date"] == day) & (snapshots["contract_month"] == month)]
    choices: list[tuple[float, float, str, pd.Series, pd.Series]] = []
    for master in chain.itertuples(index=False):
        quote = proxy.history_exact(history_lookup, str(master.security_id), day)
        if quote is None or float(quote["open"]) <= 0 or float(quote["volume"]) <= 0:
            continue
        master_row = pd.Series(master._asdict())
        strike = float(master_row["strike"])
        choices.append((round(abs(strike / etf_open - target), 12), strike, str(master.security_id), master_row, quote))
    if not choices:
        return None
    choices.sort(key=lambda value: (value[0], value[1], value[2]))
    return choices[0][3], choices[0][4]


def _model_monthly_open(
    moneyness: float,
) -> Callable[[object, str, float, pd.DatetimeIndex], tuple[proxy.ModelPosition, float, float]]:
    def open_position(
        row: object,
        tenor: str,
        fraction: float,
        trade_dates: pd.DatetimeIndex,
    ) -> tuple[proxy.ModelPosition, float, float]:
        day = pd.Timestamp(row.date)
        month = v6.desired_model_month(day, tenor, trade_dates)
        expiry = proxy.fourth_wednesday(month, trade_dates)
        strike = float(row.spot_open) * moneyness
        units = float(row.settle) * 200.0 / float(row.spot_open) * fraction
        position = proxy.ModelPosition(month, expiry, strike, units, fraction, 0.0)
        open_price = proxy.option_price(position, row, "open")
        close_price = proxy.option_price(position, row, "close")
        position.prior_mark = close_price
        return position, open_price, close_price

    return open_position


def _model_hold_open(
    moneyness: float,
) -> Callable[[object, float, pd.DatetimeIndex], tuple[proxy.ModelPosition, float, float]]:
    def open_position(
        row: object,
        fraction: float,
        trade_dates: pd.DatetimeIndex,
    ) -> tuple[proxy.ModelPosition, float, float]:
        day = pd.Timestamp(row.date)
        month = v6.desired_model_month(day, "3m_monthly", trade_dates)
        expiry = proxy.fourth_wednesday(month, trade_dates)
        strike = float(row.spot_open) * moneyness
        units = float(row.settle) * 200.0 / float(row.spot_open) * fraction
        position = proxy.ModelPosition(month, expiry, strike, units, fraction, 0.0)
        open_price = proxy.option_price(position, row, "open")
        close_price = proxy.option_price(position, row, "close")
        position.prior_mark = close_price
        return position, open_price, close_price

    return open_position


def _fix_model_moneyness(
    overlay: pd.DataFrame,
    trades: pd.DataFrame,
    moneyness: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    overlay = overlay.copy()
    trades = trades.copy()
    if "entry_moneyness_mark" in overlay:
        overlay.loc[overlay["entry_moneyness_mark"].notna(), "entry_moneyness_mark"] = moneyness
    if not trades.empty and "new_entry_moneyness" in trades:
        trades.loc[trades["new_entry_moneyness"].notna(), "new_entry_moneyness"] = moneyness
    return overlay, trades


def run_model_tool(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
    execution: str,
    moneyness: float,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    if execution == "front_exit":
        overlay, trades = proxy.run_model_candidate(
            frames["ic"], schedule, market, "daily", "front", "three_tier", moneyness, label
        )
        return overlay, trades, None
    if execution in {"2m_monthly_exit", "3m_monthly_exit"}:
        tenor = "2m_monthly" if execution.startswith("2m") else "3m_monthly"
        original = v6._model_open_position
        v6._model_open_position = _model_monthly_open(moneyness)
        try:
            overlay, trades = v6.run_model_monthly_tenor(
                frames["ic"], schedule, market, tenor, label, roll_dates
            )
        finally:
            v6._model_open_position = original
        overlay, trades = _fix_model_moneyness(overlay, trades, moneyness)
        return overlay, trades, None
    if execution == "3m_hold_expiry":
        original = v7._model_open_hold
        v7._model_open_hold = _model_hold_open(moneyness)
        try:
            overlay, trades, lifecycles = v7.run_model_hold_expiry(
                frames["ic"], schedule, market, label, roll_dates
            )
        finally:
            v7._model_open_hold = original
        overlay, trades = _fix_model_moneyness(overlay, trades, moneyness)
        return overlay, trades, lifecycles
    raise ValueError(execution)


def run_real_tool(
    frames: dict[str, pd.DataFrame],
    schedule: pd.DataFrame,
    execution: str,
    moneyness: float,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    etf = frames["etf500"].set_index("date")
    original = proxy.select_real_contract

    def selector(
        snapshots: pd.DataFrame,
        history_lookup: pd.DataFrame,
        day: pd.Timestamp,
        month: pd.Timestamp,
    ) -> tuple[pd.Series, pd.Series] | None:
        return select_real_contract_target(
            snapshots, history_lookup, day, month, float(etf.loc[day, "open"]), moneyness
        )

    proxy.select_real_contract = selector
    try:
        if execution == "front_exit":
            overlay, trades = proxy.run_real_candidate(
                frames["ic"], schedule, frames["snapshots"], frames["histories"], frames["etf500"],
                "daily", "front", "three_tier", label,
            )
            return overlay, trades, None
        if execution in {"2m_monthly_exit", "3m_monthly_exit"}:
            tenor = "2m_monthly" if execution.startswith("2m") else "3m_monthly"
            overlay, trades = v6.run_real_monthly_tenor(
                frames["ic"], schedule, frames["snapshots"], frames["histories"], frames["etf500"],
                tenor, label, roll_dates,
            )
            return overlay, trades, None
        if execution == "3m_hold_expiry":
            overlay, trades, lifecycles = v7.run_real_hold_expiry(
                frames["ic"], schedule, frames["snapshots"], frames["histories"], frames["etf500"],
                label, roll_dates,
            )
            return overlay, trades, lifecycles
    finally:
        proxy.select_real_contract = original
    raise ValueError(execution)


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
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    roll_dates = v6.forced_roll_dates(frames["ic"])
    daily_parts: list[pd.DataFrame] = [
        proxy.no_put_rows(frames["ic"], core.MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], core.REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    lifecycle_parts: list[pd.DataFrame] = []
    for execution in EXECUTIONS:
        for moneyness in MONEYNESS:
            suffix = f"{execution}_m{int(round(moneyness * 100))}"
            model_label = f"model_{suffix}"
            overlay, trades, life = run_model_tool(
                frames, market, schedule, execution, moneyness, model_label, roll_dates
            )
            _append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])
            real_label = f"real_{suffix}"
            overlay, trades, life = run_real_tool(
                frames, schedule, execution, moneyness, real_label, roll_dates
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
    frozen = pd.read_csv(v10.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    mapping = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        "model_3m_hold_expiry_m85": "model_hold3m_or_mom120_000",
        "real_3m_hold_expiry_m85": "real_hold3m_or_mom120_000",
    }
    columns = ["put_pnl_ret", "put_cost_rate", "target_fraction", "ret", "cash_ret"]
    rows: list[dict[str, object]] = []
    for current_label, prior_label in mapping.items():
        left = daily[daily["candidate"].eq(current_label)][["date", *columns]]
        right = frozen[frozen["candidate"].eq(prior_label)][["date", *columns]]
        joined = left.merge(right, on="date", suffixes=("_v11", "_v10"), validate="one_to_one")
        row: dict[str, object] = {
            "current_candidate": current_label,
            "prior_candidate": prior_label,
            "rows": len(joined),
        }
        for column in columns:
            row[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v11"] - joined[f"{column}_v10"]).abs().max()
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].to_numpy().max() > 1e-14:
        raise RuntimeError("v11/v10 baseline parity failed")
    return table


def contract_selection_audit(
    trades: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    snapshots = frames["snapshots"]
    history_lookup = frames["histories"].set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    rows: list[dict[str, object]] = []
    opening_actions = {"open_buy", "open_roll", "open_roll_monthly", "open_renewal"}
    selected_trades = trades[
        trades["candidate"].str.startswith("real_")
        & trades["action"].isin(opening_actions)
        & trades["new_contract"].fillna("").ne("")
    ]
    for trade in selected_trades.itertuples(index=False):
        parts = candidate_parts(str(trade.candidate))
        day = pd.Timestamp(trade.actual_execution_date)
        month = pd.Timestamp(trade.new_month)
        target = float(parts["moneyness_target"])
        selected = select_real_contract_target(
            snapshots, history_lookup, day, month, float(etf.loc[day, "open"]), target
        )
        expected_contract = str(selected[0]["contract_id"]) if selected is not None else ""
        actual_contract = str(trade.new_contract)
        actual_moneyness = float(trade.new_entry_moneyness)
        rows.append(
            {
                "candidate": trade.candidate,
                "actual_execution_date": day,
                "action": trade.action,
                "target_moneyness": target,
                "actual_moneyness": actual_moneyness,
                "absolute_target_error": abs(actual_moneyness - target),
                "expected_contract": expected_contract,
                "actual_contract": actual_contract,
                "nearest_contract_match": actual_contract == expected_contract,
            }
        )
    table = pd.DataFrame(rows)
    if table.empty or not table["nearest_contract_match"].all():
        raise RuntimeError("Real target-moneyness contract selection audit failed")
    return table


def execution_audit(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    lifecycles: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate in sorted(value for value in daily["candidate"].unique() if not value.endswith("no_put")):
        parts = candidate_parts(candidate)
        execution = str(parts["execution_structure"])
        layer = str(parts["layer"])
        target = float(parts["moneyness_target"])
        trade = trades[trades["candidate"].eq(candidate)].copy()
        entry = trade[trade["new_entry_moneyness"].notna()].copy()
        max_delay = int(trade["delay_trading_days"].fillna(0).max()) if len(trade) and "delay_trading_days" in trade else 0
        exits = int(trade["action"].eq("open_exit").sum()) if len(trade) else 0
        monthly_rolls = int(trade["action"].eq("open_roll_monthly").sum()) if len(trade) else 0
        passed = bool(len(entry) and max_delay <= 5)
        coverage = np.nan
        early_exits = np.nan
        if layer == "model":
            passed &= bool(np.allclose(entry["new_entry_moneyness"].astype(float), target, atol=1e-12, rtol=0.0))
        if execution in {"front_exit", "2m_monthly_exit", "3m_monthly_exit"}:
            passed &= exits > 0
            if execution.endswith("monthly_exit"):
                passed &= monthly_rolls > 0
        else:
            life = lifecycles[lifecycles["candidate"].eq(candidate)].copy()
            complete = life[life["completed"].astype(bool)].copy()
            if layer == "model":
                complete = complete[pd.to_datetime(complete["entry_date"]) > core.MODEL_START]
            coverage = float(complete["ic_rolls_covered"].eq(3).mean()) if len(complete) else 0.0
            early_exits = int(life["early_exit"].fillna(False).sum()) if len(life) else 0
            passed &= bool(
                len(complete)
                and early_exits == 0
                and (math.isclose(coverage, 1.0, abs_tol=1e-12) if layer == "model" else coverage >= 0.90)
            )
        rows.append(
            {
                "candidate": candidate,
                **parts,
                "entry_trades": len(entry),
                "exit_trades": exits,
                "monthly_rolls": monthly_rolls,
                "max_delay_trading_days": max_delay,
                "average_entry_moneyness": float(entry["new_entry_moneyness"].mean()),
                "min_entry_moneyness": float(entry["new_entry_moneyness"].min()),
                "max_entry_moneyness": float(entry["new_entry_moneyness"].max()),
                "mean_abs_target_error": float((entry["new_entry_moneyness"].astype(float) - target).abs().mean()),
                "three_ic_roll_ratio": coverage,
                "early_exits": early_exits,
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
        v10_base = daily[daily["candidate"].eq(f"{layer}_{BASELINE_VARIANT}")][["date", "cash_ret"]].rename(
            columns={"cash_ret": "v10_cash_ret"}
        )
        for candidate in sorted(
            value for value in daily["candidate"].unique()
            if value.startswith(f"{layer}_") and value != f"{layer}_no_put"
        ):
            path = daily[daily["candidate"].eq(candidate)][
                ["date", "cash_ret", "put_pnl_ret", "put_cost_rate", "signal_target_fraction", "target_fraction"]
            ]
            joined = path.merge(baseline, on="date", validate="one_to_one").merge(
                v10_base, on="date", validate="one_to_one"
            )
            for period, (start, end) in PAYOUT_WINDOWS.items():
                sample = joined[joined["date"].between(start, end)].copy()
                relative_no_put = np.log1p(sample["cash_ret"]) - np.log1p(sample["baseline_cash_ret"])
                relative_v10 = np.log1p(sample["cash_ret"]) - np.log1p(sample["v10_cash_ret"])
                rows.append(
                    {
                        "candidate": candidate,
                        **candidate_parts(candidate),
                        "period": period,
                        "rows": len(sample),
                        "relative_no_put_terminal_return": float(np.expm1(relative_no_put.sum())) if len(sample) else np.nan,
                        "relative_v10_terminal_return": float(np.expm1(relative_v10.sum())) if len(sample) else np.nan,
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
    execution: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model_base = formal[formal["candidate"].eq("model_no_put")].set_index("segment")
    real_base = formal[formal["candidate"].eq("real_no_put")].set_index("segment")
    v10_base = formal[formal["candidate"].eq(f"model_{BASELINE_VARIANT}")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    execution_lookup = execution.set_index("candidate")
    rows: list[dict[str, object]] = []
    for variant in GRID_VARIANTS[1:]:
        execution_structure, moneyness = split_variant(variant)
        model_candidate = f"model_{variant}"
        real_candidate = f"real_{variant}"
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
        v10_revision_cagr = float(
            model_rows.loc["revision_validation", "cash_ann_return"]
            - v10_base.loc["revision_validation", "cash_ann_return"]
        )
        v10_revision_dd = float(
            model_rows.loc["revision_validation", "cash_max_dd"]
            - v10_base.loc["revision_validation", "cash_max_dd"]
        )
        real_cagr = float(real_rows.loc["full", "cash_ann_return"] - real_base.loc["full", "cash_ann_return"])
        real_dd = float(real_rows.loc["full", "cash_max_dd"] - real_base.loc["full", "cash_max_dd"])
        model_days = int(exposure_lookup.loc[model_candidate, "protected_days"])
        real_days = int(exposure_lookup.loc[real_candidate, "protected_days"])
        audit_pass = bool(
            execution_lookup.loc[model_candidate, "passed"] and execution_lookup.loc[real_candidate, "passed"]
        )
        single = bool(
            dd_improvement["full"] >= 0.03
            and cagr_delta["full"] >= -0.01
            and revision_pass
            and v10_revision_dd >= 0.02
            and v10_revision_cagr >= -0.01
            and sum(value > 1e-12 for value in dd_improvement.values()) >= 3
            and return_pass
            and development_recent_pass
            and model_days >= 20
            and real_days >= 20
            and real_dd >= 0.005
            and real_cagr >= -0.01
            and audit_pass
        )
        rows.append(
            {
                "grid_variant": variant,
                "execution_structure": execution_structure,
                "moneyness_target": moneyness,
                "full_cagr_delta": cagr_delta["full"],
                "full_dd_improvement": dd_improvement["full"],
                "revision_cagr_delta": extra["revision_validation_cagr_delta"],
                "revision_dd_improvement": extra["revision_validation_dd_improvement"],
                "v10_revision_cagr_delta": v10_revision_cagr,
                "v10_revision_dd_improvement": v10_revision_dd,
                "improved_required_windows": sum(value > 1e-12 for value in dd_improvement.values()),
                "return_tolerance_pass": return_pass,
                **extra,
                "development_recent_pass": development_recent_pass,
                "model_protected_days": model_days,
                "real_protected_days": real_days,
                "real_cagr_delta": real_cagr,
                "real_dd_improvement": real_dd,
                "execution_audit_pass": audit_pass,
                "single_candidate_pass": single,
            }
        )
    decisions = pd.DataFrame(rows)
    lookup = decisions.set_index(["execution_structure", "moneyness_target"])["single_candidate_pass"].to_dict()
    support_rows: list[dict[str, object]] = []
    for row in decisions.itertuples(index=False):
        execution_index = EXECUTIONS.index(row.execution_structure)
        moneyness_index = MONEYNESS.index(float(row.moneyness_target))
        tenor_neighbors = []
        if execution_index > 0:
            tenor_neighbors.append(EXECUTIONS[execution_index - 1])
        if execution_index < len(EXECUTIONS) - 1:
            tenor_neighbors.append(EXECUTIONS[execution_index + 1])
        moneyness_neighbors = []
        if moneyness_index > 0:
            moneyness_neighbors.append(MONEYNESS[moneyness_index - 1])
        if moneyness_index < len(MONEYNESS) - 1:
            moneyness_neighbors.append(MONEYNESS[moneyness_index + 1])
        tenor_supporting = [
            value for value in tenor_neighbors if lookup.get((value, float(row.moneyness_target)), False)
        ]
        moneyness_supporting = [
            value for value in moneyness_neighbors if lookup.get((row.execution_structure, value), False)
        ]
        support_rows.append(
            {
                "grid_variant": row.grid_variant,
                "supporting_tenors": ";".join(tenor_supporting),
                "supporting_moneyness": ";".join(f"{value:.2f}" for value in moneyness_supporting),
                "tenor_neighbor_pass": bool(tenor_supporting),
                "moneyness_neighbor_pass": bool(moneyness_supporting),
                "all_preregistered_pass": bool(
                    row.single_candidate_pass and tenor_supporting and moneyness_supporting
                ),
            }
        )
    decisions = decisions.merge(pd.DataFrame(support_rows), on="grid_variant", validate="one_to_one")
    passed = decisions[decisions["all_preregistered_pass"]].copy()
    single_passed = decisions[decisions["single_candidate_pass"]].copy()
    if passed.empty and single_passed.empty:
        summary = {
            "decision": "keep_default",
            "stability_label": "reject",
            "selected_variant": None,
            "passing_candidates": [],
            "single_passing_candidates": [],
            "sample_reuse": "not_independent_oos",
        }
    elif passed.empty:
        selected = str(
            single_passed.sort_values(
                ["revision_dd_improvement", "real_dd_improvement"], ascending=False
            ).iloc[0]["grid_variant"]
        )
        summary = {
            "decision": "watchlist",
            "stability_label": "peak_only",
            "selected_variant": selected,
            "passing_candidates": [],
            "single_passing_candidates": single_passed["grid_variant"].tolist(),
            "sample_reuse": "not_independent_oos",
        }
    else:
        selected = str(
            passed.sort_values(
                ["revision_dd_improvement", "real_dd_improvement"], ascending=False
            ).iloc[0]["grid_variant"]
        )
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if len(passed) >= 6 else "narrow_stable",
            "selected_variant": selected,
            "passing_candidates": passed["grid_variant"].tolist(),
            "single_passing_candidates": single_passed["grid_variant"].tolist(),
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


def build_tool_comparison(formal: pd.DataFrame, exposure: pd.DataFrame) -> pd.DataFrame:
    full = formal[formal["segment"].isin(REQUIRED_SEGMENTS)].copy()
    wide = full.pivot(index="candidate", columns="segment", values=["cash_ann_return", "cash_max_dd"])
    wide.columns = [f"{metric}_{segment}" for metric, segment in wide.columns]
    wide = wide.reset_index().merge(
        exposure[["candidate", "protected_day_ratio", "put_cost_sum", "trade_events", "average_entry_moneyness"]],
        on="candidate", how="left", validate="one_to_one",
    )
    parts = pd.DataFrame([{"candidate": value, **candidate_parts(value)} for value in wide["candidate"]])
    return parts.merge(wide, on="candidate", validate="one_to_one")


def build_record(
    formal: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, object],
    execution: pd.DataFrame,
    current: pd.DataFrame,
) -> str:
    metrics = display_metrics(formal)
    model = metrics[metrics["layer"].eq("model")]
    real = metrics[metrics["layer"].eq("real")]
    decision_cols = [
        "grid_variant", "execution_structure", "moneyness_target", "full_cagr_delta",
        "full_dd_improvement", "revision_cagr_delta", "revision_dd_improvement",
        "v10_revision_cagr_delta", "v10_revision_dd_improvement", "improved_required_windows",
        "real_cagr_delta", "real_dd_improvement", "single_candidate_pass",
        "tenor_neighbor_pass", "moneyness_neighbor_pass", "all_preregistered_pass",
    ]
    lines = [
        "# IC + 510500 Put 绝对动量保护工具扫描 v11", "",
        "> 研究回测；未获准实盘；v10信号冻结，仅扫描期限/退出结构与虚值度。", "",
        "## 决定", "",
        f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。", "",
        "## 模型层强制窗口（含70%现金）", "",
        model.to_markdown(index=False, floatfmt=".4f"), "",
        "## 真实Put层强制窗口（不足窗口为N/A）", "",
        real.to_markdown(index=False, floatfmt=".4f"), "",
        "## 预注册判断", "",
        decisions[decision_cols].to_markdown(index=False, floatfmt=".4f"), "",
        "## 执行与行权价审计", "",
        execution.to_markdown(index=False, floatfmt=".4f"), "",
        "## 2026-08-14冻结信号", "",
        current.to_markdown(index=False, floatfmt=".6f"), "",
        "## 限制", "",
        "- 前月/2m/3m月滚在信号关闭时退出；3m持有到期不退出，结构差异不只是期限。",
        "- 真实合约按目标比例最近的当日正开盘、正成交量合约选择，不等于模型精确比例。",
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
    columns = [
        "candidate", "cash_ann_return_full", "cash_max_dd_full",
        "cash_ann_return_last_10y", "cash_max_dd_last_10y",
        "cash_ann_return_last_5y", "cash_max_dd_last_5y",
        "cash_ann_return_last_3y", "cash_max_dd_last_3y",
        "cash_ann_return_last_1y", "cash_max_dd_last_1y",
    ]
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `20260817_ic_510500_put_absolute_momentum_protection_tool_v11`
- Run date: 2026-08-17
- Timezone: Asia/Shanghai
- Operator: Codex
- Project: IC + 510500 ETF Put
- Repo or workspace path: `{ROOT}`
- Version or strategy family: `{VERSION}`
- Sleeve or subsystem: absolute-momentum Put protection tool
- Parameter group: tenor/exit structure x moneyness
- Scan type: two_parameter_grid
- Target entrypoint: `{Path(__file__).name}`
- Working tree status before: `{git_before}`
- Working tree status after: `{git_after}`

## Research Question

- Signal: frozen v10 fixed1.75 OR MOM120<=0%.
- Grid: front/2m monthly/3m monthly/3m hold-expiry x 85%/90%/95% strike ratio.
- Baselines: same-run no Put and exact v10 3m hold-expiry 85%.
- Decision target: keep_default / watchlist / rerun_required.
- Source-change rule: research_only_no_production_change.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.

## Implementation Anchor

- Official entrypoint: `{Path(__file__).name}`.
- Function path: frozen v10 daily signal -> T+1 open -> frozen proxy/v6/v7 engines with runtime strike target.
- Real contract mapping: nearest executable strike/ETF-open ratio to target.
- No production constants or source files edited.

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
- Leverage: 100% IC notional, 30% margin/buffer, no 3.33x amplification.
- Monthly rolls sell old/buy new at the same open; IC baseline rolls by frozen settlement convention.

## Commands

```powershell
python -m pytest test_ic_510500_put_absolute_momentum_protection_tool_v11.py -q
python ic_510500_put_absolute_momentum_protection_tool_v11.py
```

## Full-Sample Results

{wide[columns[:3]].to_markdown(index=False, floatfmt='.4f')}

## Window Results

{wide[columns].to_markdown(index=False, floatfmt='.4f')}

## Stability Classification

- Label: `{summary['stability_label']}`.
- Width rule: one adjacent execution structure and one adjacent moneyness must also pass.
- Cost/data caveat: model Put theoretical; real layer short and third-party.

## Decision

- Decision: `{summary['decision']}`.
- Recommended next action: no production change without explicit approval and independent evidence.
"""


def main() -> None:
    git_before = core.git_status()
    v10_manifest = verify_inputs()
    frames = core.v2.load_inputs()
    daily_valuation, valuation_checks = core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, current = primary_schedule(frames["ic"], daily_valuation)
    market, market_checks = proxy.prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = proxy.qvix_validation(market, frames["q500"])
    if not qvix_stats["passed"]:
        raise RuntimeError("QVIX proxy validation failed")
    daily, trades, lifecycles = run_all_candidates(frames, market, schedule)
    parity = parity_audit(daily)
    contract_audit = contract_selection_audit(trades, frames)
    execution = execution_audit(daily, trades, lifecycles)

    configure_metrics()
    formal, scan_summary, wide = core.metric_outputs(daily)
    annual = core.annual_metrics(daily)
    exposure = core.v2.exposure_summary(daily, trades)
    cross_table, cross_stats = core.real_model_validation(daily)
    concentration = core.event_concentration(daily)
    attribution = period_attribution(daily)
    tool_comparison = build_tool_comparison(formal, exposure)
    decisions, decision_summary = decision_outputs(formal, exposure, execution)

    expected = {f"{layer}_{variant}" for layer in ["model", "real"] for variant in GRID_VARIANTS}
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v11 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v11 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid v11 daily return")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes scheduled execution")
    if not execution["passed"].all():
        failed = execution.loc[~execution["passed"], "candidate"].tolist()
        raise RuntimeError(f"v11 execution audit failed: {failed}")
    if (exposure.loc[~exposure["candidate"].str.endswith("no_put"), "trade_events"] <= 0).any():
        raise RuntimeError("Empty v11 economic path")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    lifecycles.to_csv(OUTPUT / "hold_expiry_lifecycles.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "frozen_signal_history.csv.gz", index=False, compression="gzip")
    current.to_csv(OUTPUT / "current_research_signal.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_segment.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    contract_audit.to_csv(OUTPUT / "real_contract_selection_audit.csv", index=False)
    execution.to_csv(OUTPUT / "execution_integrity_audit.csv", index=False)
    attribution.to_csv(OUTPUT / "period_attribution.csv", index=False)
    tool_comparison.to_csv(OUTPUT / "tool_comparison.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "record.md").write_text(
        build_record(formal, decisions, decision_summary, execution, current), encoding="utf-8"
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
            "valuation_and_tri_history": ["2007-01-15", str(core.END.date())],
            "model": [str(core.MODEL_START.date()), str(core.END.date())],
            "real": [str(core.REAL_START.date()), str(core.END.date())],
        },
        "valuation_checks": valuation_checks,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "baseline_parity_max_abs": float(
            parity[[column for column in parity if column.startswith("max_abs_")]].to_numpy().max()
        ),
        "real_contract_selection_pass": bool(contract_audit["nearest_contract_match"].all()),
        "execution_audit_pass": bool(execution["passed"].all()),
        "decision_summary": decision_summary,
        "dependencies": {
            "v10_signal_and_baseline": {"path": str(V10_PATH.relative_to(ROOT)), "sha256": V10_SHA256},
        },
        "source_hashes": v10_manifest["source_hashes"],
        "git_status": core.git_status(),
        "warnings": [
            "The full history has been reused and is not independent OOS.",
            "Front/monthly paths exit on signal-off; hold-expiry does not, so differences are not pure tenor effects.",
            "Model Put is theoretical; actual bars are not executable quote proof.",
            "Current signal is research-only and not an order.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    commands = (
        "python.exe -m pytest test_ic_510500_put_absolute_momentum_protection_tool_v11.py -q\n"
        "python.exe ic_510500_put_absolute_momentum_protection_tool_v11.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(commands, encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\n" + commands)
    git_after = core.git_status()
    (SCAN / "record.md").write_text(
        build_scan_record(decision_summary, wide, git_before, git_after), encoding="utf-8"
    )
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "two_parameter_grid",
            "baseline": {
                "candidate": "model_no_put",
                "same_run": True,
                "v10_baseline": f"model_{BASELINE_VARIANT}",
            },
            "candidate_grid": [
                {"execution_structure": execution, "moneyness": moneyness}
                for execution in EXECUTIONS for moneyness in MONEYNESS
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
