#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas"]
# ///
"""Scan higher IM grid entries on the frozen 2015 model proxy path."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_overlay_entry_exit_scan_v15 as grid


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_model2015_high_entry_scan_v20"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_SIDECAR = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "88bf35586e5bc95812eab88a6a4b47040f3ee329fb68d310c7b77c5d3d8f7548"
GRID_SCRIPT = ROOT / "im_fixed_valuation_overlay_entry_exit_scan_v15.py"
GRID_SCRIPT_HASH = "d80e7286de1d6571c59b79f965f78a733e7a8243fc3724d581d792d31b3a3aa0"
FORMAL_SCRIPT = ROOT / "im_put_grid_call_final_audit_v1.py"
FORMAL_SCRIPT_HASH = "5c565c8a7e0fa27c8877cd146d29af326989d7a8c917c59e7343454f3ae14168"
FORMAL_DAILY = ROOT / "outputs" / "im_put_grid_call_final_audit_v1" / "daily_candidates.csv.gz"
FORMAL_DAILY_HASH = "21fa70cf2ca9df2e5a9b9c9ed7b255cce8a7b430fc9a62725706a71d9422837a"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260823_im_model2015_high_entry_scan_v20"

ENTRY_THRESHOLDS = tuple(round(1.35 + 0.05 * index, 2) for index in range(16))
HYSTERESIS_WIDTH = 0.40
CURRENT_ENTRY = 0.85
CURRENT_EXIT = 1.25
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
WINDOW_YEARS = {"full": None, "last_10y": 10, "last_5y": 5, "last_3y": 3, "last_1y": 1}


def candidate_grid() -> list[tuple[float, float]]:
    return [(entry, round(entry + HYSTERESIS_WIDTH, 2)) for entry in ENTRY_THRESHOLDS]


def candidate_label(entry: float, exit_: float) -> str:
    return f"grid_L{entry:.2f}_H{exit_:.2f}"


def verify_inputs() -> dict[str, str]:
    actual = {
        "spec_sha256": grid.sha256(SPEC),
        "spec_sidecar_sha256": SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower(),
        "grid_script_sha256": grid.sha256(GRID_SCRIPT),
        "formal_script_sha256": grid.sha256(FORMAL_SCRIPT),
        "formal_daily_sha256": grid.sha256(FORMAL_DAILY),
    }
    expected = {
        "spec_sha256": SPEC_HASH,
        "spec_sidecar_sha256": SPEC_HASH,
        "grid_script_sha256": GRID_SCRIPT_HASH,
        "formal_script_sha256": FORMAL_SCRIPT_HASH,
        "formal_daily_sha256": FORMAL_DAILY_HASH,
    }
    failures = {key: [actual[key], value] for key, value in expected.items() if actual[key] != value}
    if failures:
        raise RuntimeError(f"Frozen v20 input mismatch: {failures}")
    if len(candidate_grid()) != 16 or candidate_grid()[0] != (1.35, 1.75) or candidate_grid()[-1] != (2.10, 2.50):
        raise RuntimeError(f"Unexpected v20 grid: {candidate_grid()}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal v20 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v20 parameter-scan folder is missing")
    grid.verify_inputs(require_fresh_output=False)
    return actual


def recompose_model(
    core: pd.DataFrame,
    overlay: pd.DataFrame,
    candidate: str,
) -> pd.DataFrame:
    component_columns = [
        "date", "tri_close", "base_gross_ret", "futures_cost_rate",
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction", "put_contract",
        "call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction",
        "call_coverage", "call_delta", "call_contract", "call_strike", "call_expiry",
        "threat_roll_count", "threat_entry_blocked",
    ]
    frame = core[component_columns].rename(
        columns={"futures_cost_rate": "base_futures_cost_rate"}
    ).merge(overlay, on="date", validate="one_to_one")
    zero_columns = [
        "base_futures_cost_rate", "overlay_gross_ret", "overlay_cost_rate",
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction",
        "call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction",
    ]
    frame[zero_columns] = frame[zero_columns].fillna(0.0)
    frame["futures_cost_rate"] = frame["base_futures_cost_rate"] + frame["overlay_cost_rate"]
    frame["gross_before_cost"] = (
        frame["base_gross_ret"] + frame["overlay_gross_ret"]
        + frame["put_pnl_ret"] + frame["call_pnl_ret"]
    )
    frame["ret"] = (
        (1.0 + frame["gross_before_cost"])
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
    )
    frame["cash_weight_raw"] = (
        1.0 - grid.MARGIN_RATE * frame["total_im_units"]
        - frame["put_mark_fraction"] - frame["call_margin_fraction"]
    )
    frame["cash_weight"] = frame["cash_weight_raw"].clip(lower=0.0)
    frame["cash_interest_ret"] = frame["cash_weight"] * grid.CASH_DAILY
    frame["cash_ret"] = frame["ret"] + frame["cash_interest_ret"]
    frame["nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["drawdown"] = frame["nav"] / frame["nav"].cummax() - 1.0
    frame["candidate"] = candidate
    frame["layer"] = "model"
    return frame


def add_capital_audit(daily: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    audit_rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date").copy()
        prior_put = group["put_mark_fraction"].shift(1).fillna(group["put_mark_fraction"])
        prior_call = group["call_margin_fraction"].shift(1).fillna(group["call_margin_fraction"])
        morning = np.where(
            group["overlay_buy"].eq(1),
            2.0 * grid.MARGIN_RATE + prior_put + prior_call,
            np.nan,
        )
        finite = pd.Series(morning).dropna()
        audit_rows.append(
            {
                "candidate": candidate,
                "eod_capital_breach_rows": int(group["cash_weight_raw"].lt(-1e-12).sum()),
                "min_cash_weight_raw": float(group["cash_weight_raw"].min()),
                "morning_capital_breach_rows": int((finite > 1.0 + 1e-12).sum()),
                "max_morning_capital_proxy": float(finite.max()) if len(finite) else np.nan,
            }
        )
    return cycles.merge(pd.DataFrame(audit_rows), on="candidate", validate="one_to_one")


def metrics_by_window(daily: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    lookup = cycles.set_index("candidate")
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        full_start = pd.Timestamp(group["date"].min())
        end = pd.Timestamp(group["date"].max())
        cycle = lookup.loc[candidate]
        for segment in WINDOWS:
            years = WINDOW_YEARS[segment]
            start = full_start if years is None else max(full_start, end - pd.DateOffset(years=years))
            sample = group[group["date"].ge(start)]
            values = grid.metrics(sample["cash_ret"])
            rows.append(
                {
                    "candidate": candidate,
                    "segment": segment,
                    "start": sample["date"].min(),
                    "end": end,
                    "rows": len(sample),
                    "ann_return": values["ann_return"],
                    "ann_vol": values["ann_vol"],
                    "sharpe_repo": values["sharpe_repo"],
                    "max_dd": values["max_dd"],
                    "entry_threshold": cycle["low_threshold"],
                    "exit_threshold": cycle["high_threshold"],
                    "completed_cycles": int(cycle["completed_cycles"]),
                    "entry_years": int(cycle["entry_years"]),
                    "holding_days": int(cycle["holding_days"]),
                    "holding_day_ratio": float(cycle["holding_ratio"]),
                    "eod_capital_breach_rows": int(cycle["eod_capital_breach_rows"]),
                    "morning_capital_breach_rows": int(cycle["morning_capital_breach_rows"]),
                    "overlay_cost_total": float(group["overlay_cost_rate"].sum()),
                }
            )
    result = pd.DataFrame(rows)
    baseline = result[result["candidate"].eq("no_grid")].set_index("segment")
    result["ann_return_delta_vs_no_grid"] = [
        row.ann_return - float(baseline.loc[row.segment, "ann_return"])
        for row in result.itertuples(index=False)
    ]
    result["max_dd_delta_vs_no_grid"] = [
        row.max_dd - float(baseline.loc[row.segment, "max_dd"])
        for row in result.itertuples(index=False)
    ]
    return result


def wide_metrics(long: pd.DataFrame) -> pd.DataFrame:
    first = long.groupby("candidate", sort=False)[
        [
            "entry_threshold", "exit_threshold", "completed_cycles", "entry_years",
            "holding_days", "holding_day_ratio", "eod_capital_breach_rows",
            "morning_capital_breach_rows", "overlay_cost_total",
        ]
    ].first()
    parts = [first]
    for metric in (
        "ann_return", "max_dd", "ann_vol", "sharpe_repo",
        "ann_return_delta_vs_no_grid", "max_dd_delta_vs_no_grid",
    ):
        pivot = long.pivot(index="candidate", columns="segment", values=metric)
        pivot.columns = [f"{metric}_{segment}" for segment in pivot.columns]
        parts.append(pivot)
    return pd.concat(parts, axis=1).reset_index()


def build_record(long: pd.DataFrame, cycles: pd.DataFrame, checks: dict[str, Any]) -> str:
    full = long[long["segment"].eq("full")].set_index("candidate")
    order = [
        "no_grid", "current_L0.85_H1.25",
        *[candidate_label(entry, exit_) for entry, exit_ in candidate_grid()],
    ]
    cycle_lookup = cycles.set_index("candidate")
    high = order[2:]
    best_return = max(high, key=lambda value: float(full.loc[value, "ann_return"]))
    shallowest_dd = max(high, key=lambda value: float(full.loc[value, "max_dd"]))
    lines = [
        "# IM固定估值网格2015模型代理高入场阈值扫描 v20",
        "",
        "状态：模型代理研究；未批准实盘；冻结主线未修改。",
        "",
        "## Run Metadata",
        "",
        "- Run id: `20260823_im_model2015_high_entry_scan_v20`",
        "- Run date/timezone: 2026-08-23 / Asia/Shanghai",
        "- Scan type: `single_parameter` with exit = entry + 0.40",
        "- Working tree was already dirty with unrelated untracked research files; see `scan_meta.json`.",
        "",
        "## Research Question",
        "",
        "- Baseline: formal model `core_put_call_d10_threat5` with no grid.",
        "- Current reference: formal model `full_put_grid_call` at 0.85/1.25.",
        "- Candidates: entry 1.35—2.10 by 0.05; exit = entry + 0.40.",
        "- Decision target: descriptive model sensitivity only; no promotion.",
        "- Source-change rule: `research_only_no_source_change`.",
        "",
        "## Implementation Anchor",
        "",
        "- Formal components: `outputs/im_put_grid_call_final_audit_v1/daily_candidates.csv.gz` model layer.",
        "- Grid chain: v15 `load_sources -> build_model_market -> simulate_overlay`.",
        "- Put scope: V1 model three-Put cap, not current V2 four-Put cap.",
        "",
        "## Data Snapshot",
        "",
        "- Model sample: 2015-04-16—2026-08-14, 2,756 trading rows per candidate.",
        "- Pre-2022 IM/MO uses index and theoretical-option proxies; it is not executable historical IM/MO data.",
        "- Calendar/timezone: A-share trading dates / Asia/Shanghai.",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- T close valuation signal; T+1 modeled index open execution.",
        "- Grid open/close one-way 1bp; grid roll round trip 2bp.",
        "- 30% margin/buffer per IM unit; residual cash earns net 3% after model Put mark and Call margin.",
        "- No bid-ask spread, impact, capacity, limit non-fill, dynamic margin hike or tax.",
        "",
        "## Runtime Override Plan",
        "",
        "- Parameters are passed to the frozen simulator; no strategy constants are edited.",
        f"- No-grid cash-return parity max abs: `{checks['no_grid_cash_ret_parity_max_abs']:.3e}`.",
        f"- Current-grid component parity max abs: `{checks['current_grid_component_max_abs']:.3e}`.",
        "",
        "## Commands",
        "",
        "```powershell",
        "python im_fixed_valuation_overlay_model2015_high_entry_scan_v20.py",
        "```",
        "",
        "## Output Files",
        "",
        "- `scan_summary.csv`, `window_metrics.csv`, `daily_candidates.csv.gz`.",
        "- `overlay_trade_audit.csv`, `overlay_cycle_summary.csv`, `integrity_checks.json`.",
        "",
        "## Full-Sample Results",
        "",
        "| candidate | entry | exit | CAGR | MaxDD | CAGR差 | MaxDD差 | 周期 | 持有比例 | EOD穿透 | 早盘穿透 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for candidate in order:
        row = full.loc[candidate]
        cycle = cycle_lookup.loc[candidate]
        entry = "—" if pd.isna(cycle["low_threshold"]) else f"{float(cycle['low_threshold']):.2f}"
        exit_ = "—" if pd.isna(cycle["high_threshold"]) else f"{float(cycle['high_threshold']):.2f}"
        lines.append(
            f"| {candidate} | {entry} | {exit_} | {row['ann_return']:.2%} | {row['max_dd']:.2%} | "
            f"{row['ann_return_delta_vs_no_grid']:+.2%} | {row['max_dd_delta_vs_no_grid']:+.2%} | "
            f"{int(cycle['completed_cycles'])} | {float(cycle['holding_ratio']):.2%} | "
            f"{int(cycle['eod_capital_breach_rows'])} | {int(cycle['morning_capital_breach_rows'])} |"
        )
    lines.extend(
        [
            "",
            "## Window Results",
            "",
            "完整 full/10Y/5Y/3Y/1Y 指标见 `window_metrics.csv`。",
            "",
            "## Stability Classification",
            "",
            "- Label: `data_sensitive`.",
            f"- 高阈值候选 full CAGR 最高：`{best_return}`；full MaxDD 最浅：`{shallowest_dd}`。",
            "- 2015—2022段依赖模型IM和理论期权，不能作为真实可交易样本验证。",
            "",
            "## Decision",
            "",
            "- Decision: `keep_default`.",
            "- 只把结果作为网格阈值的长周期模型敏感性证据；不修改当前V2。",
            "",
            "## User-Facing Summary",
            "",
            "本次回答2015模型代理下提高网格阈值的回报、回撤与持仓周期变化；所有结论均为研究用途。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    started = datetime.now().astimezone()
    git_before = grid.git_status()
    source_hashes = verify_inputs()

    formal = pd.read_csv(FORMAL_DAILY, parse_dates=["date"], low_memory=False)
    formal = formal[formal["layer"].eq("model")].copy()
    core = formal[formal["candidate"].eq("core_put_call_d10_threat5")].sort_values("date").reset_index(drop=True)
    official_current = formal[formal["candidate"].eq("full_put_grid_call")].sort_values("date").reset_index(drop=True)

    base, score, percentile = grid.load_sources()
    market, market_checks = grid.build_model_market(base, score, percentile)
    history = score[["date", "unbounded_median_knot"]].copy()
    if not core["date"].equals(market["date"]) or not official_current["date"].equals(market["date"]):
        raise RuntimeError("Formal model components and v15 model market do not align")

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []

    flat, flat_cycle = grid.flat_overlay(market, "model")
    flat["candidate"] = "no_grid"
    flat["family"] = "no_grid"
    flat_cycle.update({"candidate": "no_grid", "family": "no_grid"})
    daily_parts.append(recompose_model(core, flat, "no_grid"))
    cycle_rows.append(flat_cycle)

    current_overlay, current_trades, current_cycle = grid.simulate_overlay(
        market, history, "unbounded_median_knot", CURRENT_ENTRY, CURRENT_EXIT,
        "current_L0.85_H1.25", "current_grid", "model",
    )
    daily_parts.append(recompose_model(core, current_overlay, "current_L0.85_H1.25"))
    trade_parts.append(current_trades)
    cycle_rows.append(current_cycle)

    for entry, exit_ in candidate_grid():
        candidate = candidate_label(entry, exit_)
        overlay, trades, cycle = grid.simulate_overlay(
            market, history, "unbounded_median_knot", entry, exit_, candidate,
            "model2015_high_entry", "model",
        )
        daily_parts.append(recompose_model(core, overlay, candidate))
        trade_parts.append(trades)
        cycle_rows.append(cycle)

    order = [
        "no_grid", "current_L0.85_H1.25",
        *[candidate_label(entry, exit_) for entry, exit_ in candidate_grid()],
    ]
    order_map = {candidate: index for index, candidate in enumerate(order)}
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"])
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["candidate", "execution_date"])
    cycles = pd.DataFrame(cycle_rows)
    cycles = add_capital_audit(daily, cycles)
    cycles["sort_order"] = cycles["candidate"].map(order_map)
    cycles = cycles.sort_values("sort_order").drop(columns="sort_order")

    long = metrics_by_window(daily, cycles)
    long["sort_order"] = long["candidate"].map(order_map)
    long["segment_order"] = long["segment"].map({value: index for index, value in enumerate(WINDOWS)})
    long = long.sort_values(["sort_order", "segment_order"]).drop(columns=["sort_order", "segment_order"])
    wide = wide_metrics(long)
    wide["sort_order"] = wide["candidate"].map(order_map)
    wide = wide.sort_values("sort_order").drop(columns="sort_order")

    no_grid = daily[daily["candidate"].eq("no_grid")].sort_values("date")
    no_grid_join = no_grid[["date", "cash_ret"]].merge(
        core[["date", "cash_ret"]], on="date", suffixes=("_scan", "_formal"), validate="one_to_one"
    )
    no_grid_parity = float((no_grid_join["cash_ret_scan"] - no_grid_join["cash_ret_formal"]).abs().max())
    current = daily[daily["candidate"].eq("current_L0.85_H1.25")].sort_values("date")
    compare_columns = [
        "overlay_gross_ret", "futures_cost_rate", "total_im_units", "overlay_held_before",
        "overlay_held_eod", "overlay_buy", "overlay_sell", "cash_ret",
    ]
    current_join = current[["date", *compare_columns]].merge(
        official_current[["date", *compare_columns]], on="date",
        suffixes=("_scan", "_formal"), validate="one_to_one",
    )
    current_parity = max(
        float((current_join[f"{column}_scan"] - current_join[f"{column}_formal"]).abs().max())
        for column in compare_columns
    )
    causal = trades[~trades["execution_reason"].eq("history_carry")]
    expected_ret = (
        (1.0 + daily["gross_before_cost"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        * (1.0 - daily["call_cost_rate"])
        - 1.0
    )
    expected_cash = daily["ret"] + daily["cash_weight"] * grid.CASH_DAILY
    nav_check = daily.groupby("candidate", sort=False)["cash_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    checks = {
        **source_hashes,
        "no_grid_cash_ret_parity_max_abs": no_grid_parity,
        "current_grid_component_max_abs": current_parity,
        "candidate_count": int(daily["candidate"].nunique()),
        "candidate_set_exact": set(daily["candidate"].unique()) == set(order),
        "rows_per_candidate_min": int(daily.groupby("candidate").size().min()),
        "rows_per_candidate_max": int(daily.groupby("candidate").size().max()),
        "duplicate_candidate_dates": int(daily.duplicated(["candidate", "date"]).sum()),
        "causality_failures": int(
            (pd.to_datetime(causal["execution_date"]) <= pd.to_datetime(causal["signal_date"])).sum()
        ),
        "invalid_model_units": int(
            market[["open_unit", "settle_unit", "pre_settle_unit"]].le(0).sum().sum()
        ),
        "invalid_total_im_units": int((~daily["total_im_units"].isin([1.0, 2.0])).sum()),
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - expected_cash).abs().max()),
        "nav_recomposition_max_abs": float((daily["nav"] - nav_check).abs().max()),
        "invalid_return_rows": int(
            daily[["ret", "cash_ret", "nav", "drawdown"]].isna().sum().sum()
            + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()
        ),
        "pending_orders": int(cycles["pending_order_end"].sum()),
        "capital_breach_candidate_count": int(cycles["eod_capital_breach_rows"].gt(0).sum()),
        "morning_breach_candidate_count": int(cycles["morning_capital_breach_rows"].gt(0).sum()),
        "market": market_checks,
    }
    checks["all_checks_passed"] = bool(
        checks["no_grid_cash_ret_parity_max_abs"] <= 1e-12
        and checks["current_grid_component_max_abs"] <= 1e-12
        and checks["candidate_count"] == 18
        and checks["candidate_set_exact"]
        and checks["rows_per_candidate_min"] == checks["rows_per_candidate_max"] == 2756
        and checks["duplicate_candidate_dates"] == 0
        and checks["causality_failures"] == 0
        and checks["invalid_model_units"] == 0
        and checks["invalid_total_im_units"] == 0
        and checks["return_identity_max_abs"] <= 1e-12
        and checks["cash_identity_max_abs"] <= 1e-12
        and checks["nav_recomposition_max_abs"] <= 1e-12
        and checks["invalid_return_rows"] == 0
        and checks["pending_orders"] == 0
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v20 integrity checks failed: {checks}")

    record = build_record(long, cycles, checks)
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "overlay_trade_audit.csv", index=False)
    cycles.to_csv(STAGING / "overlay_cycle_summary.csv", index=False)
    long.to_csv(STAGING / "scan_summary.csv", index=False)
    wide.to_csv(STAGING / "window_metrics.csv", index=False)
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (STAGING / "command_log.txt").write_text(
        "python im_fixed_valuation_overlay_model2015_high_entry_scan_v20.py\n", encoding="utf-8"
    )

    long.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python im_fixed_valuation_overlay_model2015_high_entry_scan_v20.py\n")
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "single_parameter",
            "baseline": {
                "candidate": "no_grid",
                "formal_source": "model core_put_call_d10_threat5",
            },
            "candidate_grid": [
                {"candidate": candidate_label(entry, exit_), "entry": entry, "exit": exit_}
                for entry, exit_ in candidate_grid()
            ],
            "data_snapshot": {
                "source": "formal 2015 model proxy components and local valuation/index data",
                "start": str(pd.Timestamp(core["date"].min()).date()),
                "end": str(pd.Timestamp(core["date"].max()).date()),
                "rows_per_candidate": len(core),
                "timezone": "Asia/Shanghai",
                "pre_real_period": "model IM and theoretical MO; not executable historical data",
            },
            "cost_model": {
                "overlay_one_way": grid.ONE_WAY_COST,
                "overlay_roll_round_trip": 2.0 * grid.ONE_WAY_COST,
                "margin_buffer_per_im_unit": grid.MARGIN_RATE,
                "cash_annual_return": 0.03,
                "execution": "T close signal, T+1 modeled index open",
                "put_call_scope": "formal V1 model one-unit core only; grid adds no option coverage",
            },
            "source_hashes": source_hashes,
            "parity_check": {
                "no_grid_cash_ret_max_abs": no_grid_parity,
                "current_grid_component_max_abs": current_parity,
            },
            "decision": "keep_default",
            "stability_label": "data_sensitive",
            "warnings": [
                "2015-2022 segment is a model IM/theoretical MO proxy",
                "Model Put cap is three, not current V2 four",
                "Research only; no mainline or live approval",
            ],
            "elapsed_sec": (datetime.now().astimezone() - started).total_seconds(),
            "git_status_after": grid.git_status(),
        }
    )
    (SCAN / "scan_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    shutil.copy2(SCAN / "scan_meta.json", STAGING / "scan_meta.json")

    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "script_sha256": grid.sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "sample": [str(pd.Timestamp(core["date"].min()).date()), str(pd.Timestamp(core["date"].max()).date())],
        "candidate_count": len(order),
        "grid": {"entries": list(ENTRY_THRESHOLDS), "exit_rule": "entry_plus_0.40"},
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": grid.git_status(),
        "research_status": "MODEL_PROXY_RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    output_hashes = {
        path.name: grid.sha256(path) for path in sorted(STAGING.iterdir()) if path.is_file()
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    STAGING.replace(OUTPUT)

    full = long[long["segment"].eq("full")][
        [
            "candidate", "entry_threshold", "exit_threshold", "ann_return", "max_dd",
            "ann_return_delta_vs_no_grid", "max_dd_delta_vs_no_grid", "holding_day_ratio",
            "completed_cycles", "eod_capital_breach_rows", "morning_capital_breach_rows",
        ]
    ]
    print(full.to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
