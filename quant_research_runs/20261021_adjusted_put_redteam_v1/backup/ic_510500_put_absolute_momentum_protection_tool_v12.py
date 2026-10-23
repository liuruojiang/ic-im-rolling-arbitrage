from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_absolute_momentum_protection_tool_v11 as v11


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_absolute_momentum_protection_tool_v12"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "f966122dd9b584b7c56a2448cb6cd83a8d5b3a1d680128083cc8c9131720343c"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260817_ic_510500_put_absolute_momentum_protection_tool_v12"

V10 = v11.v10
V10_PATH = Path(V10.__file__).resolve()
V10_SHA256 = v11.V10_SHA256
V10_MANIFEST = V10.OUTPUT / "data_manifest.json"
V11_PATH = Path(v11.__file__).resolve()
V11_SHA256 = "2149d52637304bf09a2d1be674ff3c761d8d56033a9391a2ed46f3387ed3d4f7"

EXECUTIONS = list(v11.EXECUTIONS)
MONEYNESS = list(v11.MONEYNESS)
ECONOMIC_VARIANTS = [
    f"{execution}_m{int(round(moneyness * 100))}"
    for execution in EXECUTIONS
    for moneyness in MONEYNESS
]
LEGACY_VARIANT = "v10_legacy_hold3m_m85"
GRID_VARIANTS = ["no_put", LEGACY_VARIANT, *ECONOMIC_VARIANTS]
REQUIRED_SEGMENTS = list(v11.REQUIRED_SEGMENTS)
EXTRA_WINDOWS = list(v11.EXTRA_WINDOWS)
PAYOUT_WINDOWS = dict(v11.PAYOUT_WINDOWS)
SIGNAL = v11.SIGNAL

proxy = v11.proxy
v6 = v11.v6
v7 = v11.v7
core = v11.core


def sha256(path: Path) -> str:
    return v11.sha256(path)


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v12 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v12 specification sidecar mismatch")
    if sha256(V10_PATH) != V10_SHA256:
        raise RuntimeError("Frozen v10 dependency changed")
    if sha256(V11_PATH) != V11_SHA256:
        raise RuntimeError("Frozen v11 helper dependency changed")
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
    return v11.split_variant(grid_variant)


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {
            "execution_structure": "none",
            "moneyness_target": np.nan,
            "signal_variant": "no_put",
            "contract_mapping": "none",
        }
    if grid_variant == LEGACY_VARIANT:
        return {
            "execution_structure": "3m_hold_expiry_legacy",
            "moneyness_target": 0.85,
            "signal_variant": SIGNAL,
            "contract_mapping": "v10_legacy_lowest_real_strike",
        }
    execution, moneyness = split_variant(grid_variant)
    return {
        "execution_structure": execution,
        "moneyness_target": moneyness,
        "signal_variant": SIGNAL,
        "contract_mapping": "target_nearest_executable",
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
    core.VARIANTS = GRID_VARIANTS[1:]
    core.ALL_VARIANTS = GRID_VARIANTS
    core.ECON_VARIANTS = ECONOMIC_VARIANTS
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.segment_slice = V10.v9.v5.segment_slice
    core.v2.candidate_parts = candidate_parts


def primary_schedule(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return v11.primary_schedule(ic, daily_valuation)


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

    label = f"model_{LEGACY_VARIANT}"
    overlay, trades, lifecycles = v7.run_model_hold_expiry(
        frames["ic"], schedule, market, label, roll_dates
    )
    v11._append_candidate(
        daily_parts, trade_parts, lifecycle_parts, overlay, trades, lifecycles, frames["ic"]
    )
    label = f"real_{LEGACY_VARIANT}"
    overlay, trades, lifecycles = v7.run_real_hold_expiry(
        frames["ic"], schedule, frames["snapshots"], frames["histories"], frames["etf500"],
        label, roll_dates,
    )
    v11._append_candidate(
        daily_parts, trade_parts, lifecycle_parts, overlay, trades, lifecycles, frames["ic"]
    )

    for execution in EXECUTIONS:
        for moneyness in MONEYNESS:
            suffix = f"{execution}_m{int(round(moneyness * 100))}"
            label = f"model_{suffix}"
            overlay, trades, lifecycles = v11.run_model_tool(
                frames, market, schedule, execution, moneyness, label, roll_dates
            )
            v11._append_candidate(
                daily_parts, trade_parts, lifecycle_parts, overlay, trades, lifecycles, frames["ic"]
            )
            label = f"real_{suffix}"
            overlay, trades, lifecycles = v11.run_real_tool(
                frames, schedule, execution, moneyness, label, roll_dates
            )
            v11._append_candidate(
                daily_parts, trade_parts, lifecycle_parts, overlay, trades, lifecycles, frames["ic"]
            )

    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    ).reset_index(drop=True)
    daily["signal_target_fraction"] = daily["signal_target_fraction"].fillna(
        daily["target_fraction"]
    )
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(lifecycle_parts, ignore_index=True, sort=False)
    return daily, trades, lifecycles


def parity_audit(daily: pd.DataFrame) -> pd.DataFrame:
    frozen = pd.read_csv(V10.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    mapping = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        f"model_{LEGACY_VARIANT}": "model_hold3m_or_mom120_000",
        f"real_{LEGACY_VARIANT}": "real_hold3m_or_mom120_000",
    }
    columns = ["put_pnl_ret", "put_cost_rate", "target_fraction", "ret", "cash_ret"]
    rows: list[dict[str, object]] = []
    for current_label, prior_label in mapping.items():
        left = daily[daily["candidate"].eq(current_label)][["date", *columns]]
        right = frozen[frozen["candidate"].eq(prior_label)][["date", *columns]]
        joined = left.merge(right, on="date", suffixes=("_v12", "_v10"), validate="one_to_one")
        row: dict[str, object] = {
            "current_candidate": current_label,
            "prior_candidate": prior_label,
            "rows": len(joined),
        }
        for column in columns:
            row[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v12"] - joined[f"{column}_v10"]).abs().max()
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].to_numpy().max() > 1e-14:
        raise RuntimeError("v12/v10 legacy baseline parity failed")
    return table


def economic_contract_audit(
    trades: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    snapshots = frames["snapshots"]
    history_lookup = frames["histories"].set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    opening_actions = {"open_buy", "open_roll", "open_roll_monthly", "open_renewal"}
    economic = trades[
        trades["candidate"].isin([f"real_{variant}" for variant in ECONOMIC_VARIANTS])
        & trades["action"].isin(opening_actions)
        & trades["new_contract"].fillna("").ne("")
    ].copy()
    rows: list[dict[str, object]] = []
    for trade in economic.itertuples(index=False):
        parts = candidate_parts(str(trade.candidate))
        day = pd.Timestamp(trade.actual_execution_date)
        actual_contract = str(trade.new_contract)
        month = pd.Timestamp(trade.new_month)
        if pd.isna(month):
            actual_master = snapshots[
                snapshots["date"].eq(day)
                & (
                    snapshots["contract_id"].astype(str).eq(actual_contract)
                    | snapshots["security_id"].astype(str).eq(actual_contract)
                )
            ]
            if not actual_master.empty:
                month = pd.Timestamp(actual_master.iloc[0]["contract_month"])
        target = float(parts["moneyness_target"])
        selected = None if pd.isna(month) else v11.select_real_contract_target(
            snapshots, history_lookup, day, month, float(etf.loc[day, "open"]), target
        )
        expected_contract_id = str(selected[0]["contract_id"]) if selected is not None else ""
        expected_security_id = str(selected[0]["security_id"]) if selected is not None else ""
        actual_moneyness = float(trade.new_entry_moneyness)
        match = actual_contract in {expected_contract_id, expected_security_id}
        rows.append({
            "candidate": trade.candidate,
            "actual_execution_date": day,
            "contract_month": month,
            "action": trade.action,
            "target_moneyness": target,
            "actual_moneyness": actual_moneyness,
            "absolute_target_error": abs(actual_moneyness - target),
            "expected_contract_id": expected_contract_id,
            "expected_security_id": expected_security_id,
            "actual_contract": actual_contract,
            "nearest_contract_match": match,
        })
    table = pd.DataFrame(rows)
    if table.empty:
        raise RuntimeError("Real target-moneyness contract selection audit has no rows")
    return table


def legacy_execution_rows(
    trades: pd.DataFrame,
    lifecycles: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        candidate = f"{layer}_{LEGACY_VARIANT}"
        trade = trades[trades["candidate"].eq(candidate)].copy()
        entry = trade[trade["new_entry_moneyness"].notna()].copy()
        life = lifecycles[lifecycles["candidate"].eq(candidate)].copy()
        complete = life[life["completed"].astype(bool)].copy()
        if layer == "model":
            complete = complete[pd.to_datetime(complete["entry_date"]) > core.MODEL_START]
        coverage = float(complete["ic_rolls_covered"].eq(3).mean()) if len(complete) else 0.0
        early_exits = int(life["early_exit"].fillna(False).sum()) if len(life) else 0
        max_delay = int(trade["delay_trading_days"].fillna(0).max()) if len(trade) else 0
        passed = bool(
            len(entry)
            and max_delay <= 5
            and early_exits == 0
            and len(complete)
            and (math.isclose(coverage, 1.0, abs_tol=1e-12) if layer == "model" else coverage >= 0.90)
        )
        rows.append({
            "candidate": candidate,
            **candidate_parts(candidate),
            "entry_trades": len(entry),
            "exit_trades": int(trade["action"].eq("open_exit").sum()) if len(trade) else 0,
            "monthly_rolls": int(trade["action"].eq("open_roll_monthly").sum()) if len(trade) else 0,
            "max_delay_trading_days": max_delay,
            "average_entry_moneyness": float(entry["new_entry_moneyness"].mean()),
            "min_entry_moneyness": float(entry["new_entry_moneyness"].min()),
            "max_entry_moneyness": float(entry["new_entry_moneyness"].max()),
            "mean_abs_target_error": np.nan,
            "three_ic_roll_ratio": coverage,
            "early_exits": early_exits,
            "passed": passed,
        })
    return pd.DataFrame(rows)


def execution_audit(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    lifecycles: pd.DataFrame,
) -> pd.DataFrame:
    econ_candidates = {
        f"{layer}_{variant}" for layer in ["model", "real"] for variant in ECONOMIC_VARIANTS
    }
    econ_daily = daily[daily["candidate"].isin(econ_candidates)].copy()
    econ_trades = trades[trades["candidate"].isin(econ_candidates)].copy()
    econ_lifecycles = lifecycles[lifecycles["candidate"].isin(econ_candidates)].copy()
    economic_rows = v11.execution_audit(econ_daily, econ_trades, econ_lifecycles)
    legacy_rows = legacy_execution_rows(trades, lifecycles)
    return pd.concat([legacy_rows, economic_rows], ignore_index=True, sort=False)


def period_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    old_baseline = v11.BASELINE_VARIANT
    old_parts = v11.candidate_parts
    v11.BASELINE_VARIANT = LEGACY_VARIANT
    v11.candidate_parts = candidate_parts
    try:
        return v11.period_attribution(daily)
    finally:
        v11.BASELINE_VARIANT = old_baseline
        v11.candidate_parts = old_parts


def decision_outputs(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    execution: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    old_baseline = v11.BASELINE_VARIANT
    old_grid = v11.GRID_VARIANTS
    v11.BASELINE_VARIANT = LEGACY_VARIANT
    v11.GRID_VARIANTS = ["no_put", *ECONOMIC_VARIANTS]
    try:
        return v11.decision_outputs(formal, exposure, execution)
    finally:
        v11.BASELINE_VARIANT = old_baseline
        v11.GRID_VARIANTS = old_grid


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
    metrics = v11.display_metrics(formal)
    decision_columns = [
        "grid_variant", "execution_structure", "moneyness_target", "full_cagr_delta",
        "full_dd_improvement", "revision_cagr_delta", "revision_dd_improvement",
        "v10_revision_cagr_delta", "v10_revision_dd_improvement", "real_cagr_delta",
        "real_dd_improvement", "single_candidate_pass", "tenor_neighbor_pass",
        "moneyness_neighbor_pass", "all_preregistered_pass",
    ]
    return "\n".join([
        "# IC + 510500 Put 绝对动量保护工具扫描 v12", "",
        "> 研究回测；未获准实盘。v10信号冻结；legacy基线与目标比例网格分离。", "",
        "## 决定", "",
        f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。", "",
        "## 模型层强制窗口（含70%现金）", "",
        metrics[metrics["layer"].eq("model")].to_markdown(index=False, floatfmt=".4f"), "",
        "## 真实Put层强制窗口", "",
        metrics[metrics["layer"].eq("real")].to_markdown(index=False, floatfmt=".4f"), "",
        "## 预注册判断", "",
        decisions[decision_columns].to_markdown(index=False, floatfmt=".4f"), "",
        "## 执行审计", "",
        execution.to_markdown(index=False, floatfmt=".4f"), "",
        "## 样本末研究信号", "",
        current.to_markdown(index=False, floatfmt=".6f"), "",
        "## 限制", "",
        "- 2015—2022是理论模型Put，真实层自2022-09-19且为第三方日线。",
        "- 前月/月滚信号关闭即退出；持有到期路径不退出，差异不只是期限。",
        "- 全历史多次复用，没有独立OOS；当前状态不是订单。", "",
    ])


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

- Run id: `20260817_ic_510500_put_absolute_momentum_protection_tool_v12`
- Run date: 2026-08-17; timezone: Asia/Shanghai
- Project: IC + 510500 ETF Put
- Version: `{VERSION}`
- Scan type: two_parameter_grid
- Working tree before: `{git_before}`
- Working tree after: `{git_after}`

## Research Question

- Frozen signal: fixed1.75 OR MOM120<=0%, T+1 open, 100% protection.
- Economic grid: front/2m monthly/3m monthly/3m hold-expiry × 85%/90%/95%.
- Baselines: same-run no Put and exact `v10_legacy_hold3m_m85`.
- Real economic mapping: nearest executable strike/ETF-open ratio; legacy retains v10 lowest strike.

## Data and Cost

- Model: 2015-04-16—2026-08-14; real options: 2022-09-19—2026-08-14.
- 100% IC notional; 30% margin/buffer; 70% cash earns 3%; Put value reduces interest-bearing cash.
- Frozen IC cost and 1bp per Put side. Model Put is theoretical; real bars are not quote proof.

## Full-Sample Results

{wide[columns[:3]].to_markdown(index=False, floatfmt='.4f')}

## Window Results

{wide[columns].to_markdown(index=False, floatfmt='.4f')}

## Decision

- Decision: `{summary['decision']}`.
- Stability: `{summary['stability_label']}`.
- Selected research line: `{summary['selected_variant']}`.
- No production change; no independent OOS.
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
        raise RuntimeError("v12 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v12 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid v12 daily return")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes scheduled execution")
    if not contract_audit["nearest_contract_match"].all():
        failed_contracts = contract_audit.loc[
            ~contract_audit["nearest_contract_match"],
            ["candidate", "actual_execution_date", "contract_month", "target_moneyness",
             "actual_contract", "expected_contract_id", "expected_security_id"],
        ]
        raise RuntimeError(
            "v12 nearest-contract audit failed:\n" + failed_contracts.head(20).to_string(index=False)
        )
    if not execution["passed"].all():
        failed = execution.loc[~execution["passed"], "candidate"].tolist()
        raise RuntimeError(f"v12 execution audit failed: {failed}")
    economic_exposure = exposure[exposure["candidate"].isin(
        [f"{layer}_{variant}" for layer in ["model", "real"] for variant in ECONOMIC_VARIANTS]
    )]
    if (economic_exposure["trade_events"] <= 0).any():
        raise RuntimeError("Empty v12 economic path")

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
            "v11_tool_helpers": {"path": str(V11_PATH.relative_to(ROOT)), "sha256": V11_SHA256},
        },
        "source_hashes": v10_manifest["source_hashes"],
        "git_status": core.git_status(),
        "warnings": [
            "Full history reused; not independent OOS.",
            "Legacy real baseline uses lowest strike; economic real grid uses nearest target strike.",
            "Exit paths and hold-expiry paths differ in both tenor and exit behavior.",
            "Model Put is theoretical; actual bars are not executable quote proof.",
            "Current signal is research-only and not an order.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    commands = (
        "python.exe -m pytest test_ic_510500_put_absolute_momentum_protection_tool_v12.py -q\n"
        "python.exe ic_510500_put_absolute_momentum_protection_tool_v12.py\n"
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
    meta.update({
        "phase": "run_complete_pending_audit",
        "scan_type": "two_parameter_grid",
        "baseline": {
            "candidate": "model_no_put",
            "same_run": True,
            "v10_legacy_baseline": f"model_{LEGACY_VARIANT}",
        },
        "candidate_grid": [
            {"execution_structure": execution_name, "moneyness": moneyness}
            for execution_name in EXECUTIONS for moneyness in MONEYNESS
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
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(decision_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
