from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_absolute_momentum_protection_tool_v12 as v12


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_absolute_momentum_protection_tool_v13"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "655b45fa516a84b74b1007582c68e77f43f358c92ec20e474051ad5b022d9602"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260817_ic_510500_put_absolute_momentum_protection_tool_v13"

V10 = v12.V10
V10_PATH = v12.V10_PATH
V10_SHA256 = v12.V10_SHA256
V10_MANIFEST = v12.V10_MANIFEST
V12_PATH = Path(v12.__file__).resolve()
V12_SHA256 = "bffc76685c04eb2f7fe944a8a07d21373b45668d226ef8d9a8dffc7b3daf1f76"

EXECUTIONS = ["front_exit", "2m_monthly_exit", "3m_monthly_exit", "3cycle_hold_expiry"]
MONEYNESS = list(v12.MONEYNESS)
LEGACY_VARIANT = v12.LEGACY_VARIANT
ECONOMIC_VARIANTS = [
    f"{execution}_m{int(round(moneyness * 100))}"
    for execution in EXECUTIONS for moneyness in MONEYNESS
]
GRID_VARIANTS = ["no_put", LEGACY_VARIANT, *ECONOMIC_VARIANTS]
REQUIRED_SEGMENTS = list(v12.REQUIRED_SEGMENTS)
EXTRA_WINDOWS = list(v12.EXTRA_WINDOWS)
PAYOUT_WINDOWS = dict(v12.PAYOUT_WINDOWS)
SIGNAL = v12.SIGNAL

v11 = v12.v11
proxy = v12.proxy
v6 = v12.v6
v7 = v12.v7
core = v12.core


def sha256(path: Path) -> str:
    return v12.sha256(path)


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v13 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v13 specification sidecar mismatch")
    if sha256(V10_PATH) != V10_SHA256:
        raise RuntimeError("Frozen v10 dependency changed")
    if sha256(V12_PATH) != V12_SHA256:
        raise RuntimeError("Frozen v12 helper dependency changed")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads(V10_MANIFEST.read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V10_SHA256 or manifest["spec_sha256"] != V10.SPEC_SHA256:
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
            return execution, int(grid_variant[len(prefix):]) / 100.0
    raise ValueError(grid_variant)


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {"execution_structure": "none", "moneyness_target": np.nan,
                "signal_variant": "no_put", "contract_mapping": "none"}
    if grid_variant == LEGACY_VARIANT:
        return {"execution_structure": "3m_hold_expiry_legacy", "moneyness_target": 0.85,
                "signal_variant": SIGNAL, "contract_mapping": "v10_legacy_lowest_real_strike"}
    execution, moneyness = split_variant(grid_variant)
    return {"execution_structure": execution, "moneyness_target": moneyness,
            "signal_variant": SIGNAL, "contract_mapping": "target_nearest_executable"}


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
    core.VARIANTS = GRID_VARIANTS[1:]
    core.ALL_VARIANTS = GRID_VARIANTS
    core.ECON_VARIANTS = ECONOMIC_VARIANTS
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.segment_slice = V10.v9.v5.segment_slice
    core.v2.candidate_parts = candidate_parts


def primary_schedule(
    ic: pd.DataFrame, daily_valuation: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return v12.primary_schedule(ic, daily_valuation)


def _third_cycle_month(day: pd.Timestamp, roll_dates: set[pd.Timestamp]) -> pd.Timestamp:
    day = pd.Timestamp(day)
    upcoming = sorted(value for value in roll_dates if value >= day)
    if len(upcoming) >= 3:
        third = upcoming[2]
        return pd.Timestamp(year=third.year, month=third.month, day=1)
    month = day.to_period("M").to_timestamp()
    third_friday = month + pd.offsets.WeekOfMonth(week=2, weekday=4)
    first_month = month if day <= third_friday else month + pd.DateOffset(months=1)
    return pd.Timestamp(first_month + pd.DateOffset(months=2))


def run_three_cycle_model(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame, schedule: pd.DataFrame,
    moneyness: float, label: str, roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    original = v6.desired_model_month

    def desired(day: pd.Timestamp, tenor: str, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
        return _third_cycle_month(pd.Timestamp(day), roll_dates)

    v6.desired_model_month = desired
    try:
        overlay, trades, lifecycles = v11.run_model_tool(
            frames, market, schedule, "3m_hold_expiry", moneyness, label, roll_dates
        )
    finally:
        v6.desired_model_month = original
    return overlay, trades, lifecycles if lifecycles is not None else pd.DataFrame()


def run_three_cycle_real(
    frames: dict[str, pd.DataFrame], schedule: pd.DataFrame, moneyness: float,
    label: str, roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    original = v6.desired_real_month

    def desired(
        snapshots: pd.DataFrame, day: pd.Timestamp, tenor: str, trade_dates: pd.DatetimeIndex
    ) -> pd.Timestamp:
        return _third_cycle_month(pd.Timestamp(day), roll_dates)

    v6.desired_real_month = desired
    try:
        overlay, trades, lifecycles = v11.run_real_tool(
            frames, schedule, "3m_hold_expiry", moneyness, label, roll_dates
        )
    finally:
        v6.desired_real_month = original
    return overlay, trades, lifecycles if lifecycles is not None else pd.DataFrame()


def run_all_candidates(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame, schedule: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    roll_dates = v6.forced_roll_dates(frames["ic"])
    daily_parts: list[pd.DataFrame] = [
        proxy.no_put_rows(frames["ic"], core.MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], core.REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    lifecycle_parts: list[pd.DataFrame] = []

    overlay, trades, life = v7.run_model_hold_expiry(
        frames["ic"], schedule, market, f"model_{LEGACY_VARIANT}", roll_dates
    )
    v11._append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])
    overlay, trades, life = v7.run_real_hold_expiry(
        frames["ic"], schedule, frames["snapshots"], frames["histories"], frames["etf500"],
        f"real_{LEGACY_VARIANT}", roll_dates,
    )
    v11._append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])

    for execution in EXECUTIONS:
        for moneyness in MONEYNESS:
            suffix = f"{execution}_m{int(round(moneyness * 100))}"
            if execution == "3cycle_hold_expiry":
                overlay, trades, life = run_three_cycle_model(
                    frames, market, schedule, moneyness, f"model_{suffix}", roll_dates
                )
            else:
                overlay, trades, life = v11.run_model_tool(
                    frames, market, schedule, execution, moneyness, f"model_{suffix}", roll_dates
                )
            v11._append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])
            if execution == "3cycle_hold_expiry":
                overlay, trades, life = run_three_cycle_real(
                    frames, schedule, moneyness, f"real_{suffix}", roll_dates
                )
            else:
                overlay, trades, life = v11.run_real_tool(
                    frames, schedule, execution, moneyness, f"real_{suffix}", roll_dates
                )
            v11._append_candidate(daily_parts, trade_parts, lifecycle_parts, overlay, trades, life, frames["ic"])

    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    ).reset_index(drop=True)
    daily["signal_target_fraction"] = daily["signal_target_fraction"].fillna(daily["target_fraction"])
    return (
        daily,
        pd.concat(trade_parts, ignore_index=True, sort=False),
        pd.concat(lifecycle_parts, ignore_index=True, sort=False),
    )


def parity_audit(daily: pd.DataFrame) -> pd.DataFrame:
    return v12.parity_audit(daily)


def economic_contract_audit(
    trades: pd.DataFrame, frames: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    snapshots = frames["snapshots"]
    history_lookup = frames["histories"].set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    opening_actions = {"open_buy", "open_roll", "open_roll_monthly", "open_renewal"}
    selected_trades = trades[
        trades["candidate"].isin([f"real_{variant}" for variant in ECONOMIC_VARIANTS])
        & trades["action"].isin(opening_actions)
        & trades["new_contract"].fillna("").ne("")
    ]
    rows: list[dict[str, object]] = []
    for trade in selected_trades.itertuples(index=False):
        parts = candidate_parts(str(trade.candidate))
        day = pd.Timestamp(trade.actual_execution_date)
        actual = str(trade.new_contract)
        month = pd.Timestamp(trade.new_month)
        if pd.isna(month):
            master = snapshots[
                snapshots["date"].eq(day)
                & (snapshots["contract_id"].astype(str).eq(actual)
                   | snapshots["security_id"].astype(str).eq(actual))
            ]
            if not master.empty:
                month = pd.Timestamp(master.iloc[0]["contract_month"])
        target = float(parts["moneyness_target"])
        selected = None if pd.isna(month) else v11.select_real_contract_target(
            snapshots, history_lookup, day, month, float(etf.loc[day, "open"]), target
        )
        expected_id = str(selected[0]["contract_id"]) if selected is not None else ""
        expected_security = str(selected[0]["security_id"]) if selected is not None else ""
        actual_moneyness = float(trade.new_entry_moneyness)
        rows.append({
            "candidate": trade.candidate, "actual_execution_date": day, "contract_month": month,
            "action": trade.action, "target_moneyness": target,
            "actual_moneyness": actual_moneyness,
            "absolute_target_error": abs(actual_moneyness - target),
            "expected_contract_id": expected_id, "expected_security_id": expected_security,
            "actual_contract": actual,
            "nearest_contract_match": actual in {expected_id, expected_security},
        })
    table = pd.DataFrame(rows)
    if table.empty or not table["nearest_contract_match"].all():
        raise RuntimeError("v13 real nearest-target contract audit failed")
    return table


def execution_audit(
    daily: pd.DataFrame, trades: pd.DataFrame, lifecycles: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate in sorted(value for value in daily["candidate"].unique() if not value.endswith("no_put")):
        parts = candidate_parts(candidate)
        layer = str(parts["layer"])
        structure = str(parts["execution_structure"])
        target = float(parts["moneyness_target"])
        trade = trades[trades["candidate"].eq(candidate)].copy()
        entry = trade[trade["new_entry_moneyness"].notna()].copy()
        max_delay = int(trade["delay_trading_days"].fillna(0).max()) if len(trade) else 0
        exits = int(trade["action"].eq("open_exit").sum()) if len(trade) else 0
        monthly_rolls = int(trade["action"].eq("open_roll_monthly").sum()) if len(trade) else 0
        life = lifecycles[lifecycles["candidate"].eq(candidate)].copy()
        complete = life[life["completed"].astype(bool)].copy() if len(life) else life
        if layer == "model" and len(complete):
            complete = complete[pd.to_datetime(complete["entry_date"]) > core.MODEL_START]
        coverage = float(complete["ic_rolls_covered"].eq(3).mean()) if len(complete) else np.nan
        distribution = (
            ";".join(f"{int(key)}:{int(value)}" for key, value in
                     complete["ic_rolls_covered"].value_counts().sort_index().items())
            if len(complete) else ""
        )
        early = int(life["early_exit"].fillna(False).sum()) if len(life) else 0
        passed = bool(len(entry) and max_delay <= 5)
        if layer == "model" and structure not in {"3m_hold_expiry_legacy"}:
            passed &= bool(np.allclose(entry["new_entry_moneyness"].astype(float), target,
                                       atol=1e-12, rtol=0.0))
        if structure in {"front_exit", "2m_monthly_exit", "3m_monthly_exit"}:
            passed &= exits > 0
            if structure.endswith("monthly_exit"):
                passed &= monthly_rolls > 0
        elif structure == "3cycle_hold_expiry":
            passed &= bool(len(complete) and early == 0 and
                           (math.isclose(float(coverage), 1.0, abs_tol=1e-12)
                            if layer == "model" else float(coverage) >= 0.90))
        else:
            passed &= bool(len(complete) and early == 0)
        rows.append({
            "candidate": candidate, **parts, "entry_trades": len(entry),
            "exit_trades": exits, "monthly_rolls": monthly_rolls,
            "max_delay_trading_days": max_delay,
            "average_entry_moneyness": float(entry["new_entry_moneyness"].mean()),
            "min_entry_moneyness": float(entry["new_entry_moneyness"].min()),
            "max_entry_moneyness": float(entry["new_entry_moneyness"].max()),
            "mean_abs_target_error": (np.nan if structure == "3m_hold_expiry_legacy" else
                float((entry["new_entry_moneyness"].astype(float) - target).abs().mean())),
            "completed_lifecycles": len(complete), "ic_roll_distribution": distribution,
            "three_ic_roll_ratio": coverage, "early_exits": early, "passed": passed,
        })
    return pd.DataFrame(rows)


def period_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    old_baseline, old_parts = v11.BASELINE_VARIANT, v11.candidate_parts
    v11.BASELINE_VARIANT, v11.candidate_parts = LEGACY_VARIANT, candidate_parts
    try:
        return v11.period_attribution(daily)
    finally:
        v11.BASELINE_VARIANT, v11.candidate_parts = old_baseline, old_parts


def decision_outputs(
    formal: pd.DataFrame, exposure: pd.DataFrame, execution: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, object]]:
    saved = (v11.BASELINE_VARIANT, v11.GRID_VARIANTS, v11.EXECUTIONS,
             v11.MONEYNESS, v11.split_variant)
    v11.BASELINE_VARIANT = LEGACY_VARIANT
    v11.GRID_VARIANTS = ["no_put", *ECONOMIC_VARIANTS]
    v11.EXECUTIONS = EXECUTIONS
    v11.MONEYNESS = MONEYNESS
    v11.split_variant = split_variant
    try:
        return v11.decision_outputs(formal, exposure, execution)
    finally:
        (v11.BASELINE_VARIANT, v11.GRID_VARIANTS, v11.EXECUTIONS,
         v11.MONEYNESS, v11.split_variant) = saved


def build_tool_comparison(formal: pd.DataFrame, exposure: pd.DataFrame) -> pd.DataFrame:
    full = formal[formal["segment"].isin(REQUIRED_SEGMENTS)].copy()
    wide = full.pivot(index="candidate", columns="segment", values=["cash_ann_return", "cash_max_dd"])
    wide.columns = [f"{metric}_{segment}" for metric, segment in wide.columns]
    wide = wide.reset_index().merge(
        exposure[["candidate", "protected_day_ratio", "put_cost_sum", "trade_events",
                  "average_entry_moneyness"]], on="candidate", how="left", validate="one_to_one"
    )
    parts = pd.DataFrame([{"candidate": value, **candidate_parts(value)} for value in wide["candidate"]])
    return parts.merge(wide, on="candidate", validate="one_to_one")


def build_record(
    formal: pd.DataFrame, decisions: pd.DataFrame, summary: dict[str, object],
    execution: pd.DataFrame, current: pd.DataFrame,
) -> str:
    metrics = v11.display_metrics(formal)
    decision_columns = [
        "grid_variant", "execution_structure", "moneyness_target", "full_cagr_delta",
        "full_dd_improvement", "revision_cagr_delta", "revision_dd_improvement",
        "v10_revision_cagr_delta", "v10_revision_dd_improvement", "real_cagr_delta",
        "real_dd_improvement", "single_candidate_pass", "tenor_neighbor_pass",
        "moneyness_neighbor_pass", "all_preregistered_pass",
    ]
    return "\n".join([
        "# IC + 510500 Put 严格三周期保护工具扫描 v13", "",
        "> 研究回测；未获准实盘。legacy与严格三周期工具分离。", "",
        "## 决定", "", f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。", "",
        "## 模型层强制窗口（含70%现金）", "",
        metrics[metrics["layer"].eq("model")].to_markdown(index=False, floatfmt=".4f"), "",
        "## 真实Put层强制窗口", "",
        metrics[metrics["layer"].eq("real")].to_markdown(index=False, floatfmt=".4f"), "",
        "## 预注册判断", "", decisions[decision_columns].to_markdown(index=False, floatfmt=".4f"), "",
        "## 执行及生命周期", "", execution.to_markdown(index=False, floatfmt=".4f"), "",
        "## 样本末研究信号", "", current.to_markdown(index=False, floatfmt=".6f"), "",
        "## 限制", "", "- 全历史多次复用，无独立OOS。",
        "- 模型Put为理论价格；真实第三方日线不是成交保证。",
        "- 样本末状态仅为审计证据，不是订单。", "",
    ])


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
    contract_audit = economic_contract_audit(trades, frames)
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
        raise RuntimeError("v13 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v13 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid v13 daily return")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes schedule")
    if not execution["passed"].all():
        failed = execution.loc[~execution["passed"],
            ["candidate", "ic_roll_distribution", "three_ic_roll_ratio", "max_delay_trading_days"]]
        raise RuntimeError("v13 execution audit failed:\n" + failed.to_string(index=False))
    econ_exposure = exposure[exposure["candidate"].isin(
        [f"{layer}_{variant}" for layer in ["model", "real"] for variant in ECONOMIC_VARIANTS]
    )]
    if (econ_exposure["trade_events"] <= 0).any():
        raise RuntimeError("Empty v13 economic path")

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
        "version": VERSION, "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256, "script_sha256": sha256(Path(__file__)),
        "candidate_count": len(expected), "candidate_grid": sorted(expected),
        "sample": {"valuation_and_tri_history": ["2007-01-15", str(core.END.date())],
                   "model": [str(core.MODEL_START.date()), str(core.END.date())],
                   "real": [str(core.REAL_START.date()), str(core.END.date())]},
        "valuation_checks": valuation_checks, "market_checks": market_checks,
        "qvix_proxy": qvix_stats, "real_model_cross_validation": cross_stats,
        "baseline_parity_max_abs": float(parity[[c for c in parity if c.startswith("max_abs_")]].to_numpy().max()),
        "real_contract_selection_pass": bool(contract_audit["nearest_contract_match"].all()),
        "execution_audit_pass": bool(execution["passed"].all()),
        "decision_summary": decision_summary,
        "dependencies": {
            "v10_signal_and_legacy": {"path": str(V10_PATH.relative_to(ROOT)), "sha256": V10_SHA256},
            "v12_helpers": {"path": str(V12_PATH.relative_to(ROOT)), "sha256": V12_SHA256},
        },
        "source_hashes": v10_manifest["source_hashes"], "git_status": core.git_status(),
        "warnings": ["No independent OOS", "Legacy is not strict three-cycle",
                     "Model Put theoretical; daily bars are not quote proof", "Research signal is not an order"],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    commands = ("python.exe -m pytest test_ic_510500_put_absolute_momentum_protection_tool_v13.py -q\n"
                "python.exe ic_510500_put_absolute_momentum_protection_tool_v13.py\n")
    (OUTPUT / "command_log.txt").write_text(commands, encoding="utf-8")
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\n" + commands)
    git_after = core.git_status()
    (SCAN / "record.md").write_text(build_record(formal, decisions, decision_summary,
                                                  execution, current), encoding="utf-8")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({
        "phase": "run_complete_pending_audit", "scan_type": "two_parameter_grid",
        "baseline": {"candidate": "model_no_put", "same_run": True,
                     "v10_legacy_baseline": f"model_{LEGACY_VARIANT}"},
        "candidate_grid": [{"execution_structure": e, "moneyness": m}
                           for e in EXECUTIONS for m in MONEYNESS],
        "data_snapshot": manifest["sample"],
        "cost_model": {"put_side_cost": proxy.PUT_FULL_SIDE_COST,
                       "cash_weight": proxy.CASH_WEIGHT, "cash_yield": 0.03, "ic_notional": 1.0},
        "source_hashes": manifest["source_hashes"],
        "parity_check": manifest["baseline_parity_max_abs"],
        "formal_output": str(OUTPUT.relative_to(ROOT)),
        "outputs": {"record": str((SCAN / "record.md").resolve()),
                    "scan_summary": str((SCAN / "scan_summary.csv").resolve()),
                    "window_metrics": str((SCAN / "window_metrics.csv").resolve()),
                    "scan_meta": str(meta_path.resolve()),
                    "command_log": str((SCAN / "command_log.txt").resolve())},
        "git_status_before": git_before, "git_status_after": git_after,
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(decision_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
