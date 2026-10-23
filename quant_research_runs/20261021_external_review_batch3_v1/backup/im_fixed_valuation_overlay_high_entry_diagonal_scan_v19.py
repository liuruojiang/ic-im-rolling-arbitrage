#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas"]
# ///
"""Scan higher IM valuation-grid entries on the frozen current V2 real path."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import freeze_ic_im_system_mainlines_v2 as mainline
import ic_im_put_max_protection_scan_v1 as metric_base
import im_fixed_valuation_overlay_entry_exit_scan_v15 as grid


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_high_entry_diagonal_scan_v19"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_SIDECAR = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "16a1cadd150e07632956f108a82c38c7eac3727c08cf623b74e9d00adc8159d8"
MAINLINE_SCRIPT = ROOT / "freeze_ic_im_system_mainlines_v2.py"
MAINLINE_SCRIPT_HASH = "e6dcf46ca442c4a5941b7d8f8f4042aed687de8f9c5b6f3810898d92ced44fe3"
GRID_SCRIPT = ROOT / "im_fixed_valuation_overlay_entry_exit_scan_v15.py"
GRID_SCRIPT_HASH = "d80e7286de1d6571c59b79f965f78a733e7a8243fc3724d581d792d31b3a3aa0"
OFFICIAL_V2_DAILY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
OFFICIAL_V2_DAILY_HASH = "6cbf2a441515087ac6d6ce98b03de8c54d87334e8288e0b0b9e8720155c8da35"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260823_im_high_entry_diagonal_scan_v19"

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


def verify_inputs() -> dict[str, Any]:
    checks = {
        "spec_sha256": grid.sha256(SPEC),
        "spec_sidecar_sha256": SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower(),
        "mainline_script_sha256": grid.sha256(MAINLINE_SCRIPT),
        "grid_script_sha256": grid.sha256(GRID_SCRIPT),
        "official_v2_daily_sha256": grid.sha256(OFFICIAL_V2_DAILY),
    }
    expected = {
        "spec_sha256": SPEC_HASH,
        "spec_sidecar_sha256": SPEC_HASH,
        "mainline_script_sha256": MAINLINE_SCRIPT_HASH,
        "grid_script_sha256": GRID_SCRIPT_HASH,
        "official_v2_daily_sha256": OFFICIAL_V2_DAILY_HASH,
    }
    failures = {key: [checks[key], value] for key, value in expected.items() if checks[key] != value}
    if failures:
        raise RuntimeError(f"Frozen v19 input mismatch: {failures}")
    if len(candidate_grid()) != 16 or candidate_grid()[0] != (1.35, 1.75) or candidate_grid()[-1] != (2.10, 2.50):
        raise RuntimeError(f"Unexpected candidate grid: {candidate_grid()}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal v19 output or staging folder already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v19 parameter-scan folder is missing")
    grid.verify_inputs(require_fresh_output=False)
    return checks


def recompose_v2(
    core: pd.DataFrame,
    market: pd.DataFrame,
    overlay: pd.DataFrame,
    candidate: str,
) -> pd.DataFrame:
    component_columns = [
        "date",
        "base_gross_ret",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_fraction",
        "put_contract",
        "call_pnl_ret",
        "call_cost_rate",
        "call_mark_fraction",
        "call_margin_fraction",
        "call_coverage",
        "call_delta",
        "call_contract",
        "call_strike",
        "call_expiry",
        "threat_roll_count",
        "threat_entry_blocked",
    ]
    components = core[component_columns].copy()
    base_cost = market[["date", "cost_rate"]].rename(columns={"cost_rate": "base_futures_cost_rate"})
    frame = components.merge(base_cost, on="date", validate="one_to_one").merge(
        overlay, on="date", validate="one_to_one"
    )
    numeric_zero = [
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction",
        "call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction",
        "base_futures_cost_rate", "overlay_gross_ret", "overlay_cost_rate",
    ]
    frame[numeric_zero] = frame[numeric_zero].fillna(0.0)
    frame["futures_cost_rate"] = frame["base_futures_cost_rate"] + frame["overlay_cost_rate"]
    frame["gross_before_cost"] = (
        frame["base_gross_ret"]
        + frame["overlay_gross_ret"]
        + frame["put_pnl_ret"]
        + frame["call_pnl_ret"]
    )
    frame["ret"] = (
        (1.0 + frame["gross_before_cost"])
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
    )
    frame["cash_weight_raw"] = (
        1.0
        - grid.MARGIN_RATE * frame["total_im_units"]
        - frame["put_mark_fraction"]
        - frame["call_margin_fraction"]
    )
    frame["cash_weight"] = frame["cash_weight_raw"].clip(lower=0.0)
    frame["cash_interest_ret"] = frame["cash_weight"] * grid.CASH_DAILY
    frame["cash_ret"] = frame["ret"] + frame["cash_interest_ret"]
    frame["nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["drawdown"] = frame["nav"] / frame["nav"].cummax() - 1.0
    frame["candidate"] = candidate
    frame["product"] = "IM"
    frame["layer"] = "real"
    return frame


def metrics_by_window(daily: pd.DataFrame, cycles: pd.DataFrame) -> pd.DataFrame:
    cycle_lookup = cycles.set_index("candidate")
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        full_start = pd.Timestamp(group["date"].min())
        end = pd.Timestamp(group["date"].max())
        cycle = cycle_lookup.loc[candidate]
        for segment in WINDOWS:
            years = WINDOW_YEARS[segment]
            requested = full_start if years is None else end - pd.DateOffset(years=years)
            start = max(full_start, requested)
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
                    "capital_breach_rows": int(cycle["capital_breach_rows"]),
                    "overlay_cost_total": float(group["overlay_cost_rate"].sum()),
                }
            )
    result = pd.DataFrame(rows)
    base = result[result["candidate"].eq("no_grid")].set_index("segment")
    result["ann_return_delta_vs_no_grid"] = [
        row.ann_return - float(base.loc[row.segment, "ann_return"])
        for row in result.itertuples(index=False)
    ]
    result["max_dd_delta_vs_no_grid"] = [
        row.max_dd - float(base.loc[row.segment, "max_dd"])
        for row in result.itertuples(index=False)
    ]
    return result


def wide_metrics(long: pd.DataFrame) -> pd.DataFrame:
    first = long.groupby("candidate", sort=False)[
        [
            "entry_threshold", "exit_threshold", "completed_cycles", "entry_years",
            "holding_days", "holding_day_ratio", "capital_breach_rows", "overlay_cost_total",
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


def build_record(
    long: pd.DataFrame,
    wide: pd.DataFrame,
    cycles: pd.DataFrame,
    checks: dict[str, Any],
) -> str:
    full = long[long["segment"].eq("full")].set_index("candidate")
    baseline = full.loc["no_grid"]
    candidates = [candidate_label(low, high) for low, high in candidate_grid()]
    best_return = max(candidates, key=lambda value: float(full.loc[value, "ann_return"]))
    shallowest_dd = max(candidates, key=lambda value: float(full.loc[value, "max_dd"]))
    order = ["no_grid", "current_L0.85_H1.25", *candidates]
    lines = [
        "# IM固定估值网格高入场阈值对角扫描 v19",
        "",
        "状态：研究结果；未批准实盘；冻结主线未修改。",
        "",
        "## Run Metadata",
        "",
        "- Run id: `20260823_im_high_entry_diagonal_scan_v19`",
        "- Run date/timezone: 2026-08-23 / Asia/Shanghai",
        "- Project: IC / IM rolling arbitrage",
        "- Strategy: current IM V2 components with alternative valuation-grid overlays",
        "- Scan type: `single_parameter` with derived exit = entry + 0.40",
        f"- Working tree status before: dirty with unrelated pre-existing untracked files; see `scan_meta.json`.",
        "",
        "## Research Question",
        "",
        "- Baseline: current IM V2 with grid removed (`no_grid`).",
        "- Current reference: `0.85/1.25` recomputed in the same run.",
        "- Candidate grid: entry 1.35—2.10 by 0.05; exit = entry + 0.40.",
        "- Decision target: descriptive comparison only; no automatic promotion.",
        "- Source-change rule: `research_only_no_source_change`.",
        "- Required windows: full, 10Y, 5Y, 3Y, 1Y; 10Y/5Y collapse to the available real sample because history starts in 2022.",
        "",
        "## Implementation Anchor",
        "",
        "- Official entrypoint: `freeze_ic_im_system_mainlines_v2.run_im`.",
        "- Function chain: current V2 Put/Call recomputation plus v15 `load_sources -> build_real_market -> simulate_overlay`.",
        "- Existing metrics reused: repository 252-day CAGR/volatility/Sharpe/MaxDD convention.",
        "- Current default grid: entry 0.85, exit 1.25.",
        "",
        "## Data Snapshot",
        "",
        "- Real IM/MO sample: 2022-07-22—2026-08-14.",
        "- Data: local frozen CFFEX IM/MO, index valuation state, current V2 schedules and formal daily artifact.",
        "- Adjustment/alignment: official IM open/settle/pre-settle and active-contract roll path; Asia/Shanghai trading dates.",
        "- Cache write risk: no remote refresh; only new v19 output and scan artifacts are written.",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- Signal at T close; execution at T+1 active IM official open.",
        "- Overlay open/close one-way cost 1bp; overlay roll round-trip cost 2bp.",
        "- 30% margin/buffer per IM unit; remaining cash earns net 3% annualized after current V2 Put mark and Call margin.",
        "- Grid adds no Put or Call coverage; V2 Put/Call components remain fixed to the one-unit core.",
        "",
        "## Runtime Override Plan",
        "",
        "- Candidate parameters are passed to the existing simulator; no strategy constants are edited.",
        "- Current grid and no-grid controls are recomputed in the same process.",
        f"- Official V2 cash-return parity max abs: `{checks['official_v2_recompute_cash_ret_max_abs']:.3e}`.",
        f"- Current grid component parity max abs: `{checks['current_grid_component_max_abs']:.3e}`.",
        "",
        "## Commands",
        "",
        "```powershell",
        "python im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py",
        "```",
        "",
        "## Output Files",
        "",
        "- `scan_summary.csv`: long-form real-sample metrics.",
        "- `window_metrics.csv`: wide comparison table.",
        "- `daily_candidates.csv.gz`: daily recomposed paths.",
        "- `overlay_trade_audit.csv`, `overlay_cycle_summary.csv`, `integrity_checks.json`.",
        "",
        "## Full-Sample Results",
        "",
        "| candidate | entry | exit | CAGR | MaxDD | CAGR差 vs无网格 | MaxDD差 vs无网格 | 周期 | 持有比例 | 资金穿透日 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    cycle_lookup = cycles.set_index("candidate")
    for candidate in order:
        row = full.loc[candidate]
        cycle = cycle_lookup.loc[candidate]
        entry = "—" if pd.isna(cycle["low_threshold"]) else f"{float(cycle['low_threshold']):.2f}"
        exit_ = "—" if pd.isna(cycle["high_threshold"]) else f"{float(cycle['high_threshold']):.2f}"
        lines.append(
            f"| {candidate} | {entry} | {exit_} | {row['ann_return']:.2%} | {row['max_dd']:.2%} | "
            f"{row['ann_return_delta_vs_no_grid']:+.2%} | {row['max_dd_delta_vs_no_grid']:+.2%} | "
            f"{int(cycle['completed_cycles'])} | {float(cycle['holding_ratio']):.2%} | "
            f"{int(cycle['capital_breach_rows'])} |"
        )
    lines.extend(
        [
            "",
            "## Window Results",
            "",
            "完整逐候选 full/10Y/5Y/3Y/1Y 指标见 `window_metrics.csv`。10Y和5Y并非独立长样本，均从2022-07-22开始。",
            "",
            "## Stability Classification",
            "",
            "- Label: `data_sensitive`.",
            f"- 高阈值候选中 full CAGR 最高：`{best_return}`；full MaxDD 最浅：`{shallowest_dd}`。",
            "- 只有约4年真实样本，且阈值变化会显著改变持有比例；不能把相邻点相似直接解释为跨周期稳健性。",
            "",
            "## Decision",
            "",
            "- Decision: `keep_current_research_mainline`.",
            "- 本版只展示高阈值回报/回撤权衡；任何参数替换必须由用户另行确认，并新版本冻结。",
            "",
            "## User-Facing Summary",
            "",
            f"不加网格 full CAGR/MaxDD 为 {baseline['ann_return']:.2%}/{baseline['max_dd']:.2%}；详细候选差异见上表。结果来自当前 V2 真实调用链，不是下单建议。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    started = datetime.now().astimezone()
    git_before = grid.git_status()
    source_checks = verify_inputs()

    im_daily, _schedules, _put_trades, _thresholds, _baseline_parity = mainline.run_im()
    core = im_daily[im_daily["candidate"].eq(mainline.IM_SELECTED)].sort_values("date").reset_index(drop=True)
    official = pd.read_csv(OFFICIAL_V2_DAILY, parse_dates=["date"], low_memory=False)
    official = official[
        official["product"].eq("IM") & official["candidate"].eq(mainline.IM_SELECTED)
    ].sort_values("date").reset_index(drop=True)
    official_join = core[["date", "cash_ret"]].merge(
        official[["date", "cash_ret"]], on="date", suffixes=("_new", "_official"), validate="one_to_one"
    )
    official_parity = float(
        (official_join["cash_ret_new"] - official_join["cash_ret_official"]).abs().max()
    )

    base, score, percentile = grid.load_sources()
    market, market_checks = grid.build_real_market(base, score, percentile)
    history = score[["date", "unbounded_median_knot"]].copy()
    if not core["date"].equals(market["date"]):
        raise RuntimeError("Current V2 and v15 real-market dates do not align")

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []

    flat, flat_cycle = grid.flat_overlay(market, "real")
    flat["candidate"] = "no_grid"
    flat["family"] = "no_grid"
    flat_cycle.update({"candidate": "no_grid", "family": "no_grid"})
    daily_parts.append(recompose_v2(core, market, flat, "no_grid"))
    cycle_rows.append(flat_cycle)

    current_overlay, current_trades, current_cycle = grid.simulate_overlay(
        market, history, "unbounded_median_knot", CURRENT_ENTRY, CURRENT_EXIT,
        "current_L0.85_H1.25", "current_grid", "real",
    )
    daily_parts.append(recompose_v2(core, market, current_overlay, "current_L0.85_H1.25"))
    trade_parts.append(current_trades)
    cycle_rows.append(current_cycle)

    for entry, exit_ in candidate_grid():
        candidate = candidate_label(entry, exit_)
        overlay, trades, cycle = grid.simulate_overlay(
            market, history, "unbounded_median_knot", entry, exit_, candidate,
            "high_entry_diagonal", "real",
        )
        daily_parts.append(recompose_v2(core, market, overlay, candidate))
        trade_parts.append(trades)
        cycle_rows.append(cycle)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"])
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["candidate", "execution_date"])
    cycles = pd.DataFrame(cycle_rows)
    order = [
        "no_grid", "current_L0.85_H1.25",
        *[candidate_label(entry, exit_) for entry, exit_ in candidate_grid()],
    ]
    order_map = {candidate: index for index, candidate in enumerate(order)}
    cycles["sort_order"] = cycles["candidate"].map(order_map)
    capital_breaches = (
        daily.assign(capital_breach=daily["cash_weight_raw"].lt(-1e-12))
        .groupby("candidate", as_index=False)["capital_breach"]
        .sum()
        .rename(columns={"capital_breach": "capital_breach_rows"})
    )
    cycles = cycles.merge(capital_breaches, on="candidate", validate="one_to_one")
    cycles = cycles.sort_values("sort_order").drop(columns="sort_order")

    long = metrics_by_window(daily, cycles)
    long["sort_order"] = long["candidate"].map(order_map)
    long["segment_order"] = long["segment"].map({value: index for index, value in enumerate(WINDOWS)})
    long = long.sort_values(["sort_order", "segment_order"]).drop(columns=["sort_order", "segment_order"])
    wide = wide_metrics(long)
    wide["sort_order"] = wide["candidate"].map(order_map)
    wide = wide.sort_values("sort_order").drop(columns="sort_order")

    current_daily = daily[daily["candidate"].eq("current_L0.85_H1.25")].sort_values("date")
    current_component_columns = [
        "overlay_gross_ret", "futures_cost_rate", "total_im_units",
        "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell", "cash_ret",
    ]
    current_compare = current_daily[["date", *current_component_columns]].merge(
        official[["date", *current_component_columns]], on="date",
        suffixes=("_scan", "_official"), validate="one_to_one",
    )
    current_grid_error = max(
        float((current_compare[f"{column}_scan"] - current_compare[f"{column}_official"]).abs().max())
        for column in current_component_columns
    )
    no_grid = daily[daily["candidate"].eq("no_grid")]
    causal = trades[~trades["execution_reason"].eq("initial_listing_carry")]
    expected_ret = (
        (1.0 + daily["gross_before_cost"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        * (1.0 - daily["call_cost_rate"])
        - 1.0
    )
    expected_cash = daily["ret"] + daily["cash_weight"] * grid.CASH_DAILY
    nav_check = daily.groupby("candidate", sort=False)["cash_ret"].transform(
        lambda value: (1.0 + value).cumprod()
    )
    expected_candidates = set(order)
    checks = {
        **source_checks,
        "official_v2_recompute_cash_ret_max_abs": official_parity,
        "current_grid_component_max_abs": current_grid_error,
        "candidate_count": int(daily["candidate"].nunique()),
        "expected_candidate_count": len(order),
        "candidate_set_exact": set(daily["candidate"].unique()) == expected_candidates,
        "duplicate_candidate_dates": int(daily.duplicated(["candidate", "date"]).sum()),
        "no_grid_held_rows": int(no_grid["overlay_held_eod"].sum()),
        "no_grid_overlay_abs_max": float(no_grid[["overlay_gross_ret", "overlay_cost_rate"]].abs().to_numpy().max()),
        "no_grid_invalid_units": int(no_grid["total_im_units"].ne(1.0).sum()),
        "causality_failures": int(
            (pd.to_datetime(causal["execution_date"]) <= pd.to_datetime(causal["signal_date"])).sum()
        ),
        "invalid_real_execution_quotes": int(
            (trades["execution_open"].le(0) | trades["execution_volume"].le(0)).sum()
        ),
        "invalid_total_im_units": int((~daily["total_im_units"].isin([1.0, 2.0])).sum()),
        "negative_cash_weight_rows": int(daily["cash_weight_raw"].lt(-1e-12).sum()),
        "capital_breach_candidate_count": int(
            cycles["capital_breach_rows"].gt(0).sum()
        ),
        "baseline_and_current_capital_breach_rows": int(
            cycles[
                cycles["candidate"].isin(["no_grid", "current_L0.85_H1.25"])
            ]["capital_breach_rows"].sum()
        ),
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - expected_cash).abs().max()),
        "nav_recomposition_max_abs": float((daily["nav"] - nav_check).abs().max()),
        "invalid_return_rows": int(
            daily[["ret", "cash_ret", "nav", "drawdown"]].isna().sum().sum()
            + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()
        ),
        "pending_orders": int(cycles["pending_order_end"].sum()),
        "market": market_checks,
    }
    checks["all_checks_passed"] = bool(
        checks["official_v2_recompute_cash_ret_max_abs"] <= 1e-12
        and checks["current_grid_component_max_abs"] <= 1e-12
        and checks["candidate_count"] == checks["expected_candidate_count"] == 18
        and checks["candidate_set_exact"]
        and checks["duplicate_candidate_dates"] == 0
        and checks["no_grid_held_rows"] == 0
        and checks["no_grid_overlay_abs_max"] == 0.0
        and checks["no_grid_invalid_units"] == 0
        and checks["causality_failures"] == 0
        and checks["invalid_real_execution_quotes"] == 0
        and checks["invalid_total_im_units"] == 0
        and checks["baseline_and_current_capital_breach_rows"] == 0
        and checks["return_identity_max_abs"] <= 1e-12
        and checks["cash_identity_max_abs"] <= 1e-12
        and checks["nav_recomposition_max_abs"] <= 1e-12
        and checks["invalid_return_rows"] == 0
        and checks["pending_orders"] == 0
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v19 integrity checks failed: {checks}")

    record = build_record(long, wide, cycles, checks)
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "overlay_trade_audit.csv", index=False)
    cycles.to_csv(STAGING / "overlay_cycle_summary.csv", index=False)
    long.to_csv(STAGING / "scan_summary.csv", index=False)
    wide.to_csv(STAGING / "window_metrics.csv", index=False)
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "uv run im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py  # failed: isolated runtime missing requests\n"
        "python im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py  # first pass stopped on candidate capital-breach audit\n"
        "python im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py\n",
        encoding="utf-8",
    )

    long.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("uv run im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py  # failed: isolated runtime missing requests\n")
        handle.write("python im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py  # first pass stopped on candidate capital-breach audit\n")
        handle.write("python im_fixed_valuation_overlay_high_entry_diagonal_scan_v19.py\n")
    scan_meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    scan_meta.update(
        {
            "scan_type": "single_parameter",
            "baseline": {
                "candidate": "no_grid",
                "definition": "current IM V2 Put and Call components with zero grid overlay",
            },
            "candidate_grid": [
                {"candidate": candidate_label(entry, exit_), "entry": entry, "exit": exit_}
                for entry, exit_ in candidate_grid()
            ],
            "data_snapshot": {
                "source": "local frozen CFFEX IM/MO and current V2 formal inputs",
                "start": str(pd.Timestamp(core["date"].min()).date()),
                "end": str(pd.Timestamp(core["date"].max()).date()),
                "rows_per_candidate": len(core),
                "timezone": "Asia/Shanghai",
                "ten_year_and_five_year_windows": "truncated to full available real sample",
            },
            "cost_model": {
                "overlay_one_way": grid.ONE_WAY_COST,
                "overlay_roll_round_trip": 2.0 * grid.ONE_WAY_COST,
                "margin_buffer_per_im_unit": grid.MARGIN_RATE,
                "cash_annual_return": 0.03,
                "execution": "T close signal, T+1 active IM official open",
                "put_call_scope": "current V2 one-unit core only; grid adds no option coverage",
            },
            "source_hashes": source_checks,
            "parity_check": {
                "official_v2_cash_ret_max_abs": official_parity,
                "current_grid_component_max_abs": current_grid_error,
            },
            "decision": "keep_current_research_mainline",
            "stability_label": "data_sensitive",
            "warnings": [
                "Real sample begins 2022-07-22 and is shorter than five years",
                "10Y and 5Y windows equal the full available real sample",
                "Research only; no live approval or mainline change",
            ],
            "elapsed_sec": (datetime.now().astimezone() - started).total_seconds(),
            "git_status_after": grid.git_status(),
        }
    )
    (SCAN / "scan_meta.json").write_text(
        json.dumps(scan_meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    shutil.copy2(SCAN / "scan_meta.json", STAGING / "scan_meta.json")

    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "script_sha256": grid.sha256(Path(__file__)),
        "source_hashes": source_checks,
        "sample": [str(pd.Timestamp(core["date"].min()).date()), str(pd.Timestamp(core["date"].max()).date())],
        "candidate_count": len(order),
        "grid": {"entries": list(ENTRY_THRESHOLDS), "exit_rule": "entry_plus_0.40"},
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": grid.git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    output_hashes = {
        path.name: grid.sha256(path)
        for path in sorted(STAGING.iterdir())
        if path.is_file()
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    STAGING.replace(OUTPUT)

    full = long[long["segment"].eq("full")][
        [
            "candidate", "entry_threshold", "exit_threshold", "ann_return", "max_dd",
            "ann_return_delta_vs_no_grid", "max_dd_delta_vs_no_grid", "holding_day_ratio",
        ]
    ]
    print(full.to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
