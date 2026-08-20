#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Scan IC valuation-overlay entry and exit thresholds on frozen real paths."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_valuation_overlay_put_sync_v1 as v1

ROOT = Path(__file__).resolve().parent
ENTRYPOINT_FILE = Path(__file__).resolve()
VERSION = "ic_valuation_overlay_entry_exit_scan_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "c8f081595f5db1b11e8292cd989cf6cd6c44bc9813a66ef684c6a5b23d25aefa"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT / "quant_param_scan_runs" / "20260818_ic_valuation_overlay_entry_exit_scan_v2"
)
V1_OUTPUT = ROOT / "outputs" / "ic_valuation_overlay_put_sync_v1"
V1_DAILY = V1_OUTPUT / "daily_candidates.csv.gz"
V1_MANIFEST = V1_OUTPUT / "output_manifest.json"
V1_INTEGRITY = V1_OUTPUT / "integrity_checks.json"
V21_DAILY = (
    ROOT
    / "outputs"
    / "ic_510500_put_mom120_delta_floor_v21"
    / "daily_candidates.csv.gz"
)
SCORE_FILE = (
    ROOT
    / "outputs"
    / "ic_fixed_valuation_unbounded_score_v6"
    / "daily_unbounded_fixed_scores.csv.gz"
)

ENTRY_THRESHOLDS = tuple(round(value * 0.125, 3) for value in range(13))
EXIT_THRESHOLDS = tuple(round(1.25 + value * 0.125, 3) for value in range(9))
MIN_GAP = 0.50
CURRENT_PAIR = (1.00, 2.00)
MODEL_START = v1.MODEL_START
END = v1.END
WINDOWS = v1.WINDOWS

INPUT_HASHES = {
    ROOT
    / "ic_valuation_overlay_put_sync_v1.py": "e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36",
    ROOT
    / "docs"
    / "ic_valuation_overlay_put_sync_v1_spec.md": "7cf83eea40fb8d4aafb6c05a955be010e8b0ad26898c589033fb87a42b6935c3",
    V1_MANIFEST: "2167faf26135d5f23bd53421826381b30e3229924fac6df6860468a881ab04ae",
    V1_DAILY: "0423f4f7d9abf1a8de5b15bdb4264cbd46227c4408d32133a3730921c0bb0f18",
    V1_INTEGRITY: "12c89231e566b2afd30d9765e7ed1aa7f4a44143bc34c1c89f8b2d8b3a3a70d5",
    V21_DAILY: "11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a",
    v1.IC_RAW: "4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104",
    v1.IC_DAILY: "bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d",
    SCORE_FILE: "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77",
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


def grid() -> list[tuple[float, float]]:
    return [
        (low, high)
        for low in ENTRY_THRESHOLDS
        for high in EXIT_THRESHOLDS
        if high - low >= MIN_GAP - 1e-12
    ]


def label(low: float, high: float) -> str:
    return f"L{low:.3f}_H{high:.3f}"


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen specification sidecar mismatch")
    mismatches = []
    for path, expected in INPUT_HASHES.items():
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "expected": expected,
                    "actual": actual,
                }
            )
    if mismatches:
        raise RuntimeError(f"Frozen inputs changed: {mismatches}")
    if OUTPUT.exists():
        raise FileExistsError(
            f"Formal output exists and cannot be overwritten: {OUTPUT}"
        )
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {SCAN}")
    if len(grid()) != 96 or CURRENT_PAIR not in grid():
        raise RuntimeError("Preregistered grid mismatch")
    return {"frozen_input_count": len(INPUT_HASHES), "grid_candidates": len(grid())}


def simulate_index_proxy(
    scores: pd.DataFrame, low: float, high: float
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    state = False
    pending: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    dates = list(pd.DatetimeIndex(scores["date"]))
    previous_close = np.nan
    for index, row in enumerate(scores.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        held_before = state
        buy = False
        sell = False
        if pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError("Index proxy duplicate buy")
                state = True
                buy = True
            else:
                if not state:
                    raise RuntimeError("Index proxy sell while flat")
                state = False
                sell = True
            trades.append(
                {
                    "sample": "index_proxy_2007",
                    "pair": label(low, high),
                    "low_threshold": low,
                    "high_threshold": high,
                    "action": pending["action"],
                    "signal_date": pending["signal_date"],
                    "signal_score": pending["signal_score"],
                    "execution_date": day,
                    "execution_close": float(row.price_close),
                }
            )
            pending = None
        held_eod = state
        index_ret = (
            0.0
            if not np.isfinite(previous_close)
            else float(row.price_close) / previous_close - 1.0
        )
        gross = index_ret if held_before else 0.0
        trade_cost = v1.ONE_WAY_COST * (int(buy) + int(sell))
        cash_weight = 1.0 - v1.MARGIN_RATE * float(held_eod)
        cash_ret = gross - trade_cost + cash_weight * v1.CASH_DAILY
        rows.append(
            {
                "date": day,
                "candidate": label(low, high),
                "low_threshold": low,
                "high_threshold": high,
                "valuation_score": float(row.unbounded_median_knot),
                "price_close": float(row.price_close),
                "index_ret": index_ret,
                "overlay_held_before": int(held_before),
                "overlay_held_eod": int(held_eod),
                "overlay_buy": int(buy),
                "overlay_sell": int(sell),
                "trade_cost_rate": trade_cost,
                "cash_weight": cash_weight,
                "cash_ret": cash_ret,
            }
        )
        score = float(row.unbounded_median_knot)
        action = None
        if pending is None:
            if not state and score <= low + 1e-12:
                action = "buy"
            elif state and score + 1e-12 >= high:
                action = "sell"
        if action is not None:
            pending = {
                "action": action,
                "signal_date": day,
                "signal_score": score,
                "execution_date": dates[index + 1]
                if index + 1 < len(dates)
                else pd.NaT,
            }
        previous_close = float(row.price_close)
    daily = pd.DataFrame(rows)
    daily["nav"] = (1.0 + daily["cash_ret"]).cumprod()
    daily["drawdown"] = daily["nav"] / daily["nav"].cummax() - 1.0
    trade_frame = pd.DataFrame(trades)
    entries = int(trade_frame["action"].eq("buy").sum()) if len(trade_frame) else 0
    exits = int(trade_frame["action"].eq("sell").sum()) if len(trade_frame) else 0
    audit = {
        "sample": "index_proxy_2007",
        "pair": label(low, high),
        "low_threshold": low,
        "high_threshold": high,
        "entries": entries,
        "exits": exits,
        "completed_cycles": min(entries, exits),
        "holding_days": int(daily["overlay_held_eod"].sum()),
        "holding_ratio": float(daily["overlay_held_eod"].mean()),
        "ending_state": int(state),
        "pending_order_end": int(pending is not None),
    }
    return daily, trade_frame, audit


def metric_rows(daily: pd.DataFrame, return_col: str, sample: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        first = group.iloc[0]
        end = group["date"].max()
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else end - offset
            available = bool(offset is None or group["date"].min() <= requested)
            subset = group if offset is None else group[group["date"] >= requested]
            values = (
                v1.metrics(subset[return_col])
                if available
                else {
                    "total_return": np.nan,
                    "ann_return": np.nan,
                    "ann_vol": np.nan,
                    "sharpe_repo": np.nan,
                    "max_dd": np.nan,
                }
            )
            rows.append(
                {
                    "candidate": candidate,
                    "sample": sample,
                    "low_threshold": first.get("low_threshold", np.nan),
                    "high_threshold": first.get("high_threshold", np.nan),
                    "window": window,
                    "available": available,
                    "requested_start": requested,
                    "actual_start": subset["date"].min() if available else pd.NaT,
                    "end": subset["date"].max() if available else pd.NaT,
                    "rows": len(subset) if available else 0,
                    **values,
                }
            )
    return pd.DataFrame(rows)


def wide_metrics(metrics_table: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in metrics_table.groupby("candidate", sort=True):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "low_threshold": first["low_threshold"],
            "high_threshold": first["high_threshold"],
        }
        for item in group.itertuples(index=False):
            for field in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{field}_{item.window}"] = getattr(item, field)
        rows.append(row)
    return pd.DataFrame(rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (candidate, year), group in daily.groupby(
        ["candidate", daily["date"].dt.year], sort=True
    ):
        first = group.iloc[0]
        rows.append(
            {
                "candidate": candidate,
                "low_threshold": first["low_threshold"],
                "high_threshold": first["high_threshold"],
                "year": int(year),
                "rows": len(group),
                **v1.metrics(group.sort_values("date")["cash_ret"]),
            }
        )
    return pd.DataFrame(rows)


def max_drawdown_dates(group: pd.DataFrame, return_col: str) -> dict[str, Any]:
    ordered = group.sort_values("date").reset_index(drop=True)
    nav = (1.0 + ordered[return_col]).cumprod()
    running = nav.cummax()
    dd = nav / running - 1.0
    trough_index = int(dd.idxmin())
    peak_value = float(running.iloc[trough_index])
    peak_indexes = nav.iloc[: trough_index + 1][
        np.isclose(nav.iloc[: trough_index + 1], peak_value, rtol=0, atol=1e-12)
    ].index
    peak_index = int(peak_indexes[-1])
    return {
        "peak_date": pd.Timestamp(ordered.loc[peak_index, "date"]),
        "trough_date": pd.Timestamp(ordered.loc[trough_index, "date"]),
        "max_dd": float(dd.iloc[trough_index]),
    }


def build_drawdown_audit(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    base = daily[daily["candidate"].eq("base_core_put")].copy()
    base_dates = max_drawdown_dates(base, "cash_ret")
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        own = max_drawdown_dates(group, "cash_ret")
        peak_row = group[group["date"].eq(base_dates["peak_date"])].iloc[0]
        bear = group[
            group["date"].between(base_dates["peak_date"], base_dates["trough_date"])
        ]
        rows.append(
            {
                "candidate": candidate,
                "low_threshold": peak_row["low_threshold"],
                "high_threshold": peak_row["high_threshold"],
                "base_peak_date": base_dates["peak_date"],
                "base_trough_date": base_dates["trough_date"],
                "base_max_dd": base_dates["max_dd"],
                "overlay_held_at_base_peak": int(peak_row["overlay_held_eod"]),
                "overlay_holding_days_during_base_drawdown": int(
                    bear["overlay_held_eod"].sum()
                ),
                "overlay_holding_ratio_during_base_drawdown": float(
                    bear["overlay_held_eod"].mean()
                ),
                "candidate_peak_date": own["peak_date"],
                "candidate_trough_date": own["trough_date"],
                "candidate_max_dd": own["max_dd"],
            }
        )
    return pd.DataFrame(rows), base_dates


def decide(
    metrics_table: pd.DataFrame,
    cycles: pd.DataFrame,
    drawdowns: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    current = label(*CURRENT_PAIR)
    current_metrics = metrics_table[metrics_table["candidate"].eq(current)].set_index(
        "window"
    )
    base_metrics = metrics_table[
        metrics_table["candidate"].eq("base_core_put")
    ].set_index("window")
    actual_cycles = cycles[cycles["sample"].eq("actual_ic_2015")].set_index("pair")
    proxy_cycles = cycles[cycles["sample"].eq("index_proxy_2007")].set_index("pair")
    dd = drawdowns.set_index("candidate")
    rows: list[dict[str, Any]] = []
    for low, high in grid():
        candidate = label(low, high)
        sample = metrics_table[metrics_table["candidate"].eq(candidate)].set_index(
            "window"
        )
        full_dd_improvement = float(
            sample.loc["full", "max_dd"] - current_metrics.loc["full", "max_dd"]
        )
        dd_improved_windows = int(
            (sample["max_dd"] - current_metrics["max_dd"] > 1e-12).sum()
        )
        return_tolerance = True
        for window in WINDOWS:
            tolerance = -0.03 if window in {"full", "last_10y", "last_5y"} else -0.06
            return_tolerance &= bool(
                sample.loc[window, "ann_return"]
                - current_metrics.loc[window, "ann_return"]
                >= tolerance - 1e-12
            )
        above_core_long = all(
            sample.loc[window, "ann_return"]
            > base_metrics.loc[window, "ann_return"] + 1e-12
            for window in ("full", "last_10y", "last_5y")
        )
        peak_off = int(dd.loc[candidate, "overlay_held_at_base_peak"]) == 0
        actual_completed = int(actual_cycles.loc[candidate, "completed_cycles"])
        proxy_completed = int(proxy_cycles.loc[candidate, "completed_cycles"])
        hard_pass = bool(
            full_dd_improvement >= 0.10 - 1e-12
            and dd_improved_windows >= 3
            and return_tolerance
            and above_core_long
            and peak_off
            and actual_completed >= 2
            and proxy_completed >= 3
        )
        row: dict[str, Any] = {
            "candidate": candidate,
            "low_threshold": low,
            "high_threshold": high,
            "full_dd_improvement_vs_current": full_dd_improvement,
            "dd_improved_window_count": dd_improved_windows,
            "return_tolerance_pass": return_tolerance,
            "full_10y_5y_above_core_pass": above_core_long,
            "overlay_off_at_core_dd_peak": peak_off,
            "actual_completed_cycles": actual_completed,
            "index_proxy_completed_cycles": proxy_completed,
            "hard_gate_pass": hard_pass,
        }
        for window in WINDOWS:
            row[f"ann_return_{window}"] = float(sample.loc[window, "ann_return"])
            row[f"max_dd_{window}"] = float(sample.loc[window, "max_dd"])
            row[f"ann_return_delta_vs_current_{window}"] = float(
                sample.loc[window, "ann_return"]
                - current_metrics.loc[window, "ann_return"]
            )
            row[f"max_dd_improvement_vs_current_{window}"] = float(
                sample.loc[window, "max_dd"] - current_metrics.loc[window, "max_dd"]
            )
        rows.append(row)
    decisions = pd.DataFrame(rows)
    hard = decisions[decisions["hard_gate_pass"]].sort_values(
        ["ann_return_full", "full_dd_improvement_vs_current"], ascending=False
    )
    raw_selected = None if hard.empty else str(hard.iloc[0]["candidate"])

    width_rows: list[dict[str, Any]] = []
    lookup = decisions.set_index(["low_threshold", "high_threshold"])
    for row in decisions.itertuples(index=False):
        neighbors = {
            "entry_lower": (round(row.low_threshold - 0.125, 3), row.high_threshold),
            "entry_upper": (round(row.low_threshold + 0.125, 3), row.high_threshold),
            "exit_lower": (row.low_threshold, round(row.high_threshold - 0.125, 3)),
            "exit_upper": (row.low_threshold, round(row.high_threshold + 0.125, 3)),
        }
        required = []
        for side, key in neighbors.items():
            exists = key in lookup.index
            neighbor_improvement = (
                float(lookup.loc[key, "full_dd_improvement_vs_current"])
                if exists
                else np.nan
            )
            retention = (
                neighbor_improvement / row.full_dd_improvement_vs_current
                if exists and row.full_dd_improvement_vs_current > 1e-12
                else np.nan
            )
            support = bool(
                exists
                and retention >= 0.80 - 1e-12
                and lookup.loc[key, "return_tolerance_pass"]
                and lookup.loc[key, "overlay_off_at_core_dd_peak"]
            )
            required.append(support)
            width_rows.append(
                {
                    "candidate": row.candidate,
                    "side": side,
                    "neighbor_candidate": label(*key) if exists else None,
                    "neighbor_exists": exists,
                    "center_full_dd_improvement": row.full_dd_improvement_vs_current,
                    "neighbor_full_dd_improvement": neighbor_improvement,
                    "retention_ratio": retention,
                    "neighbor_support": support,
                }
            )
        decisions.loc[decisions["candidate"].eq(row.candidate), "width_supported"] = (
            all(required)
        )
    width = pd.DataFrame(width_rows)
    width_pass = bool(
        raw_selected is not None
        and decisions.set_index("candidate").loc[raw_selected, "width_supported"]
    )
    selected = raw_selected if width_pass else None
    if selected is not None:
        decision = "watchlist_threshold_platform_for_next_put_layer"
        stability = "wide_stable"
    elif raw_selected is not None:
        decision = "no_selection_thin_hard_gate_winner"
        stability = "peak_only"
    else:
        decision = "no_threshold_pair_passed_preregistered_gates"
        stability = "reject"
    pareto = decisions[
        decisions["overlay_off_at_core_dd_peak"] & decisions["return_tolerance_pass"]
    ].sort_values(
        ["full_dd_improvement_vs_current", "ann_return_full"], ascending=False
    )
    summary = {
        "decision": decision,
        "stability_label": stability,
        "selected_candidate": selected,
        "raw_hard_gate_winner": raw_selected,
        "hard_gate_pass_count": int(decisions["hard_gate_pass"].sum()),
        "width_supported_pass_count": int(
            (decisions["hard_gate_pass"] & decisions["width_supported"]).sum()
        ),
        "current_pair": label(*CURRENT_PAIR),
        "top_diagnostic_candidates": pareto.head(10)["candidate"].tolist(),
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return decisions, width, summary


def fmt_pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value) * 100:.2f}%"


def build_record(
    metrics_table: pd.DataFrame,
    decisions: pd.DataFrame,
    cycles: pd.DataFrame,
    drawdowns: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    current = label(*CURRENT_PAIR)
    candidates = ["base_core_put", current]
    if summary["raw_hard_gate_winner"]:
        candidates.append(summary["raw_hard_gate_winner"])
    diagnostic = decisions.sort_values(
        ["full_dd_improvement_vs_current", "ann_return_full"], ascending=False
    ).head(5)
    candidates.extend(diagnostic["candidate"].tolist())
    candidates = list(dict.fromkeys(candidates))
    show = metrics_table[metrics_table["candidate"].isin(candidates)].copy()
    lines = [
        f"# {VERSION} 正式记录",
        "",
        "## Run Metadata",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}。",
        "- 状态：研究回测，未批准实盘。",
        f"- 决定：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
        "- Source-change rule: `research_only_no_source_change`。",
        "",
        "## Research Question",
        "",
        "扫描新增1倍IC的固定估值开仓/退出阈值，检验能否在底仓主要回撤前退出；底仓及其Put完全冻结。",
        "",
        "## Implementation Anchor",
        "",
        "- 正式路径复用 `ic_valuation_overlay_put_sync_v1.py` 的真实活跃IC、T+1官方开盘、滚动和组合收益函数。",
        "- 底仓Put复用v21冻结的 `model_l190_mom25`；新增仓本层不叠加Put。",
        "- 当前替换基线为 `L1.000_H2.000`，同批重跑并与v1逐日校验。",
        "",
        "## Data Snapshot",
        "",
        "- 正式组合：2015-04-16至2026-08-14，实际CFFEX IC活跃合约；模型Put。",
        "- 长历史诊断：2007-01-15至2026-08-17，中证500价格指数与固定经济单位二取三分数；无上市前贴水与Put。",
        "- 数据均为本地冻结真实文件；没有下载或缓存写入。",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- 估值T收盘确认，新增IC在T+1活跃合约官方开盘成交；每边1bp、持仓滚动双边2bp。",
        "- 每1倍IC占30%保证金/缓冲；余额年化3%。官方开盘不等于容量或保证成交证明。",
        "",
        "## Runtime Override Plan",
        "",
        "运行时遍历96组阈值；不修改既有策略默认值、冻结规格、生产或实盘配置。",
        "",
        "## Commands",
        "",
        "见 `command_log.txt`。",
        "",
        "## Output Files",
        "",
        "完整窗口、日线、交易、周期、回撤重合、决策与宽度文件均保存在本目录；参数扫描标准工件保存在对应 `quant_param_scan_runs` 目录。",
        "",
        "## Full-Sample Results",
        "",
        diagnostic[
            [
                "candidate",
                "ann_return_full",
                "max_dd_full",
                "full_dd_improvement_vs_current",
                "overlay_off_at_core_dd_peak",
                "actual_completed_cycles",
                "index_proxy_completed_cycles",
                "hard_gate_pass",
                "width_supported",
            ]
        ].to_markdown(index=False),
        "",
        "## Window Results",
        "",
        "| 候选 | 窗口 | CAGR | MaxDD | 相对当前CAGR | 相对当前回撤改善 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    current_metrics = metrics_table[metrics_table["candidate"].eq(current)].set_index(
        "window"
    )
    for row in show.sort_values(["candidate", "window"]).itertuples(index=False):
        cagr_delta = row.ann_return - current_metrics.loc[row.window, "ann_return"]
        dd_delta = row.max_dd - current_metrics.loc[row.window, "max_dd"]
        lines.append(
            f"| {row.candidate} | {row.window} | {fmt_pct(row.ann_return)} | {fmt_pct(row.max_dd)} | {fmt_pct(cagr_delta)} | {fmt_pct(dd_delta)} |"
        )
    current_cycle = cycles[cycles["pair"].eq(current)]
    current_dd = drawdowns[drawdowns["candidate"].eq(current)].iloc[0]
    lines.extend(
        [
            "",
            "## Stability Classification",
            "",
            f"- 分类：`{summary['stability_label']}`；硬门槛通过 {summary['hard_gate_pass_count']} 组，兼具四向80%宽度支持 {summary['width_supported_pass_count']} 组。",
            f"- 当前规则在底仓回撤峰值 {pd.Timestamp(current_dd['base_peak_date']).date()} 是否仍持新增仓：{bool(current_dd['overlay_held_at_base_peak'])}。",
            f"- 当前规则正式/2007诊断已完成周期：{current_cycle[['sample', 'completed_cycles']].to_dict('records')}。",
            "",
            "## Decision",
            "",
            f"- `selected_candidate={summary['selected_candidate']}`；`raw_hard_gate_winner={summary['raw_hard_gate_winner']}`。",
            "- 本版不构成实盘授权；若存在稳定平台，下一层才能独立测试新增仓Put管理。",
            "",
            "## User-Facing Summary",
            "",
            "结果首先检验‘低估增仓为何没有躲开回撤’：关键不是低估买入本身，而是退出阈值是否在2021年以前被触发。详见强制窗口和回撤重合审计。",
            "",
            "## Integrity",
            "",
            f"- 冻结底仓奇偶误差：{integrity['base_parity_max_abs']:.3e}。",
            f"- 当前1.00/2.00逐日奇偶误差：{integrity['current_parity_max_abs']:.3e}。",
            f"- 收益恒等式最大误差：{integrity['return_identity_max_abs']:.3e}。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    git_before = git_status()
    upstream = verify_inputs()
    frames, _, _, market_checks = v1.v21.v20.v19.v18.load_close_inputs()
    chain, chain_audit = v1.load_active_chain(frames)
    frozen_v21 = pd.read_csv(V21_DAILY, parse_dates=["date"])
    frozen_v1 = pd.read_csv(V1_DAILY, parse_dates=["date"])
    base_put = v1.mainline_put_rows(frozen_v21, "model")

    flat = v1.flat_overlay(chain)
    base = v1.assemble_candidate(
        chain, flat, base_put, "model", "base_core_put", "base_core_put", None, None
    )
    base["low_threshold"] = np.nan
    base["high_threshold"] = np.nan
    daily_parts = [base]
    trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []
    actual_overlays: dict[str, pd.DataFrame] = {}
    for low, high in grid():
        candidate = label(low, high)
        overlay, trades, cycle = v1.simulate_overlay(chain, low, high)
        actual_overlays[candidate] = overlay
        combined = v1.assemble_candidate(
            chain, overlay, base_put, "model", candidate, "core_put_only", low, high
        )
        daily_parts.append(combined)
        if len(trades):
            trades = trades.copy()
            trades["sample"] = "actual_ic_2015"
            trades["candidate"] = candidate
            trade_parts.append(trades)
        cycle_rows.append(
            {
                **cycle,
                "sample": "actual_ic_2015",
                "pair": candidate,
            }
        )
    daily = (
        pd.concat(daily_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "date"])
        .reset_index(drop=True)
    )
    trades_actual = pd.concat(trade_parts, ignore_index=True, sort=False)

    scores = (
        pd.read_csv(
            SCORE_FILE,
            parse_dates=["date"],
            usecols=["date", "price_close", "unbounded_median_knot"],
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    proxy_daily_parts: list[pd.DataFrame] = []
    proxy_trade_parts: list[pd.DataFrame] = []
    for low, high in grid():
        proxy_daily, proxy_trades, proxy_cycle = simulate_index_proxy(scores, low, high)
        proxy_daily_parts.append(proxy_daily)
        if len(proxy_trades):
            proxy_trade_parts.append(proxy_trades)
        cycle_rows.append(proxy_cycle)
    index_daily = (
        pd.concat(proxy_daily_parts, ignore_index=True)
        .sort_values(["candidate", "date"])
        .reset_index(drop=True)
    )
    trades_proxy = pd.concat(proxy_trade_parts, ignore_index=True, sort=False)
    trades = pd.concat([trades_actual, trades_proxy], ignore_index=True, sort=False)
    cycles = pd.DataFrame(cycle_rows)

    metrics_table = metric_rows(daily, "cash_ret", "actual_ic_model_put")
    metrics_table["base_ann_return"] = metrics_table["window"].map(
        metrics_table[metrics_table["candidate"].eq("base_core_put")].set_index(
            "window"
        )["ann_return"]
    )
    metrics_table["base_max_dd"] = metrics_table["window"].map(
        metrics_table[metrics_table["candidate"].eq("base_core_put")].set_index(
            "window"
        )["max_dd"]
    )
    metrics_table["ann_return_delta_vs_core"] = (
        metrics_table["ann_return"] - metrics_table["base_ann_return"]
    )
    metrics_table["max_dd_improvement_vs_core"] = (
        metrics_table["max_dd"] - metrics_table["base_max_dd"]
    )
    wide = wide_metrics(metrics_table)
    proxy_metrics = metric_rows(index_daily, "cash_ret", "index_proxy_2007")
    drawdowns, base_dd_dates = build_drawdown_audit(daily)
    decisions, ridge_width, summary = decide(metrics_table, cycles, drawdowns)
    annual = annual_metrics(daily)

    scan_surface = decisions.merge(
        cycles[cycles["sample"].eq("actual_ic_2015")][
            [
                "pair",
                "entries",
                "exits",
                "holding_days",
                "holding_ratio",
                "ending_state",
            ]
        ].rename(columns={"pair": "candidate"}),
        on="candidate",
        validate="one_to_one",
    )

    old_base = frozen_v1[frozen_v1["candidate"].eq("model__base_core_put")]
    base_join = base[["date", "cash_ret"]].merge(
        old_base[["date", "cash_ret"]],
        on="date",
        suffixes=("_new", "_v1"),
        validate="one_to_one",
    )
    base_parity = float(
        (base_join["cash_ret_new"] - base_join["cash_ret_v1"]).abs().max()
    )
    current = label(*CURRENT_PAIR)
    old_current = frozen_v1[
        frozen_v1["candidate"].eq("model__L1.00_H2.00__core_put_only")
    ]
    new_current = daily[daily["candidate"].eq(current)]
    current_join = new_current[["date", "cash_ret"]].merge(
        old_current[["date", "cash_ret"]],
        on="date",
        suffixes=("_new", "_v1"),
        validate="one_to_one",
    )
    current_parity = float(
        (current_join["cash_ret_new"] - current_join["cash_ret_v1"]).abs().max()
    )
    return_identity = (1.0 + daily["gross_ret"]) * (
        1.0 - daily["futures_cost_rate"]
    ) * (1.0 - daily["put_cost_rate"]) - 1.0
    return_error = float((daily["ret"] - return_identity).abs().max())
    cash_error = float(
        (daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * v1.CASH_DAILY))
        .abs()
        .max()
    )
    causality_failures = int((trades["execution_date"] <= trades["signal_date"]).sum())
    integrity = {
        "candidate_count_actual_including_core": int(daily["candidate"].nunique()),
        "candidate_count_index_proxy": int(index_daily["candidate"].nunique()),
        "daily_rows_actual": len(daily),
        "daily_rows_index_proxy": len(index_daily),
        "duplicate_actual_candidate_dates": int(
            daily.duplicated(["candidate", "date"]).sum()
        ),
        "duplicate_proxy_candidate_dates": int(
            index_daily.duplicated(["candidate", "date"]).sum()
        ),
        "base_parity_max_abs": base_parity,
        "current_parity_max_abs": current_parity,
        "return_identity_max_abs": return_error,
        "cash_identity_max_abs": cash_error,
        "signal_execution_causality_failures": causality_failures,
        "base_drawdown_dates": {
            key: str(value) for key, value in base_dd_dates.items()
        },
        "chain": chain_audit,
        "all_checks_passed": bool(
            daily["candidate"].nunique() == 97
            and index_daily["candidate"].nunique() == 96
            and not daily.duplicated(["candidate", "date"]).any()
            and not index_daily.duplicated(["candidate", "date"]).any()
            and base_parity <= 1e-14
            and current_parity <= 1e-14
            and return_error <= 1e-14
            and cash_error <= 1e-14
            and causality_failures == 0
        ),
    }
    if not integrity["all_checks_passed"]:
        raise RuntimeError(f"Integrity checks failed: {integrity}")

    record = build_record(
        metrics_table, decisions, cycles, drawdowns, summary, integrity
    )
    OUTPUT.mkdir(parents=False, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics_table.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    scan_surface.to_csv(OUTPUT / "scan_surface.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    ridge_width.to_csv(OUTPUT / "ridge_width.csv", index=False)
    trades.to_csv(OUTPUT / "overlay_trade_audit.csv", index=False)
    cycles.to_csv(OUTPUT / "overlay_cycle_summary.csv", index=False)
    proxy_metrics.to_csv(OUTPUT / "index_proxy_metrics_by_window.csv", index=False)
    index_daily.to_csv(
        OUTPUT / "index_proxy_daily.csv.gz", index=False, compression="gzip"
    )
    drawdowns.to_csv(OUTPUT / "drawdown_overlap_audit.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    command = "uv run ic_valuation_overlay_entry_exit_scan_v2.py"
    (OUTPUT / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_sha256": sha256(ENTRYPOINT_FILE),
        "spec_sha256": SPEC_SHA256,
        "input_hashes": {
            str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()
        },
        "upstream": upstream,
        "market_checks": market_checks,
        "sample": {
            "actual_ic_model_put": [str(MODEL_START.date()), str(END.date())],
            "index_proxy": [
                str(scores["date"].min().date()),
                str(scores["date"].max().date()),
            ],
        },
        "candidate_grid": {
            "entry_thresholds": list(ENTRY_THRESHOLDS),
            "exit_thresholds": list(EXIT_THRESHOLDS),
            "minimum_gap": MIN_GAP,
            "valid_pairs": len(grid()),
            "current_pair": list(CURRENT_PAIR),
        },
        "execution": {
            "valuation_signal": "T official close",
            "actual_overlay": "T+1 active IC official open",
            "index_proxy": "T+1 next index official close",
            "put": "frozen v21 model_l190_mom25 for core only",
        },
        "cost_model": {
            "one_way_futures": v1.ONE_WAY_COST,
            "overlay_roll_round_trip": 2 * v1.ONE_WAY_COST,
            "margin_buffer_per_ic_unit": v1.MARGIN_RATE,
            "cash_annual": 0.03,
        },
        "decision": summary,
        "integrity": integrity,
        "warnings": [
            "No independent OOS",
            "Pre-2015 index layer has no IC discount or Put and is diagnostic only",
            "Model Put is theoretical before listed option history",
            "Official open is not guaranteed fill or capacity evidence",
            "Normalized 1x extra IC doubles futures notional while active",
            "Research output is not a trade instruction",
        ],
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    output_hashes = {
        path.name: sha256(path)
        for path in sorted(OUTPUT.iterdir())
        if path.name != "output_manifest.json"
    }
    (OUTPUT / "output_manifest.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    scan_summary = metrics_table.rename(
        columns={"window": "segment", "actual_start": "start"}
    )
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "two_parameter_grid",
            "baseline": {
                "candidate": current,
                "same_run": True,
                "core_candidate": "base_core_put",
            },
            "candidate_grid": manifest["candidate_grid"],
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["cost_model"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": {"base": base_parity, "current": current_parity},
            "formal_output": str(OUTPUT.relative_to(ROOT)),
            "decision": summary["decision"],
            "stability_label": summary["stability_label"],
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
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
