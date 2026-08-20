#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Compare core-only and synchronously scaled Put protection on three IC overlays."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import ic_valuation_overlay_put_sync_v1 as v1

ROOT = Path(__file__).resolve().parent
VERSION = "ic_valuation_overlay_selected_put_sync_v5"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "ac7e22fad927e11421e9f8c0f4817de52c5ec325e6b27b8a79c69234218ca8b2"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_ic_valuation_overlay_selected_put_sync_v5"
)
V4_OUTPUT = ROOT / "outputs" / "ic_valuation_overlay_exit_boundary_scan_v4"
V4_DAILY = V4_OUTPUT / "daily_candidates.csv.gz"
V4_MANIFEST = V4_OUTPUT / "output_manifest.json"
V4_DECISIONS = V4_OUTPUT / "candidate_decisions.csv"

PAIRS = ((0.375, 1.000), (0.500, 1.000), (0.375, 0.875))
PRIMARY_PAIR = (0.375, 1.000)
PUT_MODES = ("core_put_only", "sync_put_total_ic")

INPUT_HASHES = {
    ROOT
    / "ic_valuation_overlay_exit_boundary_scan_v4.py": "c9839805cc60710fbcbcbbb2045f40322082eb90710bab3cda0c4bb535c9c16d",
    ROOT
    / "docs"
    / "ic_valuation_overlay_exit_boundary_scan_v4_spec.md": "49721edc580711da6106fed3c691defae7eb93c8cdfe621550ed484a6973dfda",
    V4_MANIFEST: "c81ea42d2d0ba6834aece7dbbb87df633e14ec360a6d8ade4555475e3b7e1d3d",
    V4_DAILY: "7bc9673cc010b9fedd977077a3d68535b67bf9927ae4a6faed0f9e2d7fbcfad9",
    V4_DECISIONS: "25186963ee4b4b53b659a85904ca36c2ac99ff64ccde39ee19b76a97bea13c25",
    ROOT
    / "ic_valuation_overlay_put_sync_v1.py": "e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36",
    ROOT
    / "ic_510500_put_mom120_delta_floor_v21.py": "e43a80085d3030d8ec87a6c89ad3be73331cf83f18226a9c88dfe7ea2299106e",
    ROOT
    / "docs"
    / "ic_510500_put_mom120_delta_floor_v21_spec.md": "a928a8f8b6d03d42cb4156c861653974aaccaae1953d9bbd23153f2e4e28c329",
    v1.V21_MANIFEST: "0d7fa231586d31aa0d0c093f4ca5624ae8fb6dd43c7bb794ae5b2310d699cef6",
    v1.V21_DAILY: "11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a",
    v1.V21_SCHEDULE: "dba99b2aa67a52c9b17a25e03e89325207aae6614bc651052b99168575a38d7a",
    v1.V21_TRADES: "fb692bb0388018680891027ef3328c7b99abab86e9cac4f0a8b61d8e5437c22e",
    v1.IC_RAW: "4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104",
    v1.IC_DAILY: "bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d",
    v1.SCORE_FILE: "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77",
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


def pair_label(low: float, high: float) -> str:
    return v1.pair_label(low, high)


def candidate_label(layer: str, low: float, high: float, mode: str) -> str:
    return v1.candidate_label(layer, low, high, mode)


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
    if len(PAIRS) != 3 or len(set(PAIRS)) != 3 or PRIMARY_PAIR not in PAIRS:
        raise RuntimeError("Frozen pair bundle mismatch")
    return {"frozen_input_count": len(INPUT_HASHES), "pair_count": len(PAIRS)}


def decide(
    pairwise: pd.DataFrame,
    exposure: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for low, high in PAIRS:
        pair = pair_label(low, high)
        sample = pairwise[pairwise["pair"].eq(pair) & pairwise["available"]]
        model = sample[sample["layer"].eq("model")].set_index("window")
        real = sample[sample["layer"].eq("real")].set_index("window")
        model_full_dd = (
            float(model.loc["full", "max_dd_improvement_sync_minus_core"])
            >= 0.01 - 1e-12
        )
        model_dd_count = int(
            (model["max_dd_improvement_sync_minus_core"] > 1e-12).sum()
        )
        model_no_breach = bool(
            (model["max_dd_improvement_sync_minus_core"] >= -0.01 - 1e-12).all()
        )
        model_return = True
        for window, item in model.iterrows():
            tolerance = -0.01 if window in {"full", "last_10y", "last_5y"} else -0.03
            model_return &= bool(
                float(item["ann_return_delta_sync_minus_core"]) >= tolerance - 1e-12
            )

        real_full_dd = (
            float(real.loc["full", "max_dd_improvement_sync_minus_core"])
            >= 0.01 - 1e-12
        )
        real_dd_count = int((real["max_dd_improvement_sync_minus_core"] > 1e-12).sum())
        real_no_breach = bool(
            (real["max_dd_improvement_sync_minus_core"] >= -0.01 - 1e-12).all()
        )
        real_return = True
        for window, item in real.iterrows():
            tolerance = -0.01 if window == "full" else -0.03
            real_return &= bool(
                float(item["ann_return_delta_sync_minus_core"]) >= tolerance - 1e-12
            )

        diag = exposure[
            exposure["candidate"].isin(
                [
                    candidate_label("model", low, high, "sync_put_total_ic"),
                    candidate_label("real", low, high, "sync_put_total_ic"),
                ]
            )
        ]
        capital = bool(
            (
                diag["max_post_trade_put_mark_fraction"]
                <= diag["min_post_trade_cash_before_put"] + 1e-12
            ).all()
        )
        delta = bool(
            (
                diag.loc[diag["layer"].eq("model"), "max_target_delta_error"] <= 1e-12
            ).all()
            and (
                diag.loc[diag["layer"].eq("real"), "max_target_delta_error"]
                <= 0.02 + 1e-12
            ).all()
        )
        passed = bool(
            model_full_dd
            and model_dd_count >= 3
            and model_no_breach
            and model_return
            and real_full_dd
            and real_dd_count >= 2
            and real_no_breach
            and real_return
            and capital
            and delta
        )
        rows.append(
            {
                "pair": pair,
                "low_threshold": low,
                "high_threshold": high,
                "model_full_dd_gate": model_full_dd,
                "model_dd_windows_improved": model_dd_count,
                "model_no_dd_breach": model_no_breach,
                "model_return_tolerance_pass": model_return,
                "real_full_dd_gate": real_full_dd,
                "real_dd_windows_improved": real_dd_count,
                "real_no_dd_breach": real_no_breach,
                "real_return_tolerance_pass": real_return,
                "capital_pass": capital,
                "delta_sizing_pass": delta,
                "preregistered_gate_pass": passed,
            }
        )
    decisions = pd.DataFrame(rows)
    primary = decisions[
        decisions["low_threshold"].eq(PRIMARY_PAIR[0])
        & decisions["high_threshold"].eq(PRIMARY_PAIR[1])
    ].iloc[0]
    confirmation_count = int(
        decisions[
            ~(
                decisions["low_threshold"].eq(PRIMARY_PAIR[0])
                & decisions["high_threshold"].eq(PRIMARY_PAIR[1])
            )
        ]["preregistered_gate_pass"].sum()
    )
    layer_pass = bool(primary["preregistered_gate_pass"] and confirmation_count >= 1)
    if layer_pass:
        decision = "prefer_sync_put_on_selected_overlay_lines"
        stability = "cross_pair_confirmed"
    elif bool(primary["preregistered_gate_pass"]):
        decision = "watchlist_sync_put_primary_only"
        stability = "peak_only"
    else:
        decision = "retain_core_put_only_for_overlay"
        stability = "preregistered_gate_not_met"
    summary = {
        "decision": decision,
        "stability_label": stability,
        "primary_pair": pair_label(*PRIMARY_PAIR),
        "primary_pair_pass": bool(primary["preregistered_gate_pass"]),
        "confirmation_pass_count": confirmation_count,
        "passing_pairs": decisions.loc[
            decisions["preregistered_gate_pass"], "pair"
        ].tolist(),
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return decisions, summary


def timing_audit(
    schedules: pd.DataFrame,
    overlay_trades: pd.DataFrame,
    put_trades: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trade in overlay_trades[
        overlay_trades["put_mode"].eq("sync_put_total_ic")
    ].itertuples(index=False):
        candidate = str(trade.candidate)
        day = pd.Timestamp(trade.execution_date)
        schedule = schedules[
            schedules["candidate"].eq(candidate) & schedules["execution_date"].eq(day)
        ]
        if len(schedule) != 1:
            raise RuntimeError(f"Missing unique sync schedule row: {candidate} {day}")
        item = schedule.iloc[0]
        expected_units = 2.0 if trade.action == "buy" else 1.0
        expected_target = float(item["core_target_delta"]) * expected_units
        target_change = abs(
            float(item["target_delta"]) - float(item["core_target_delta"])
        )
        same_day_put = put_trades[
            put_trades["candidate"].eq(candidate)
            & put_trades["actual_execution_date"].eq(day)
        ]
        rows.append(
            {
                "candidate": candidate,
                "layer": trade.layer,
                "pair": trade.pair,
                "action": trade.action,
                "overlay_signal_date": trade.signal_date,
                "overlay_execution_date": day,
                "overlay_execution_open": trade.execution_open,
                "core_target_delta": float(item["core_target_delta"]),
                "sync_target_delta": float(item["target_delta"]),
                "expected_sync_target_delta": expected_target,
                "target_formula_error": abs(
                    float(item["target_delta"]) - expected_target
                ),
                "target_differs_from_core": target_change > 1e-12,
                "same_day_put_trade_events": len(same_day_put),
                "same_day_trade_required": target_change > 1e-12,
                "same_day_trade_pass": bool(
                    target_change <= 1e-12 or len(same_day_put) >= 1
                ),
            }
        )
    return pd.DataFrame(rows)


def fmt_pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value) * 100:.2f}%"


def build_record(
    metrics: pd.DataFrame,
    pairwise: pd.DataFrame,
    decisions: pd.DataFrame,
    exposure: pd.DataFrame,
    timing: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    primary = metrics[
        metrics["pair"].eq(pair_label(*PRIMARY_PAIR))
        | metrics["put_mode"].eq("base_core_put")
    ]
    full_pairs = pairwise[pairwise["window"].eq("full")].copy()
    show_full = full_pairs[
        [
            "layer",
            "pair",
            "core_ann_return",
            "core_max_dd",
            "sync_ann_return",
            "sync_max_dd",
            "ann_return_delta_sync_minus_core",
            "max_dd_improvement_sync_minus_core",
        ]
    ].copy()
    for column in [
        "core_ann_return",
        "core_max_dd",
        "sync_ann_return",
        "sync_max_dd",
        "ann_return_delta_sync_minus_core",
        "max_dd_improvement_sync_minus_core",
    ]:
        show_full[column] = show_full[column].map(fmt_pct)
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
        "固定v4三条估值线，比较新增IC不加Put与按总IC同步主线Put。",
        "",
        "## Implementation Anchor",
        "",
        "- 复用v1的真实活跃IC、主线Put日程、模型/真实Put引擎和组合收益函数。",
        "- 新增IC T+1官方开盘；同步Put在同日真实/模型期权收盘调整目标。",
        "",
        "## Data Snapshot",
        "",
        "- 模型：2015-04-16至2026-08-14。",
        "- 真实510500 Put：2022-09-19至2026-08-14；10Y/5Y不可用。",
        "- 本轮只读本地冻结数据，没有下载或缓存写入。",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- IC/Put每边1bp，新增IC滚动双边2bp；30%保证金/IC单位，余额3%现金收益。",
        "- 3个月95% Put月换，不每日Delta再平衡；真实期权按收盘、成交量和整数张数。",
        "",
        "## Runtime Override Plan",
        "",
        "三条固定估值线×两种Put管理，模型和真实层同批运行；不修改生产或冻结文件。",
        "",
        "## Commands",
        "",
        "见 `command_log.txt`。",
        "",
        "## Output Files",
        "",
        "窗口、日线、期货与Put交易、日程、Delta/资本、同步时点、判定和完整性文件均在本目录。",
        "",
        "## Full-Sample Results",
        "",
        show_full.to_markdown(index=False),
        "",
        "## Window Results",
        "",
        "| 层 | 规则 | 窗口 | CAGR | MaxDD | 相对同线不同步CAGR | 相对同线不同步回撤 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    pairwise_lookup = pairwise.set_index(["layer", "pair", "window"])
    for row in primary.sort_values(["layer", "put_mode", "window"]).itertuples(
        index=False
    ):
        if not row.available:
            lines.append(
                f"| {row.layer} | {row.put_mode} | {row.window} | N/A | N/A | N/A | N/A |"
            )
            continue
        if row.put_mode == "base_core_put":
            cagr_delta = 0.0
            dd_delta = 0.0
        else:
            comparison = pairwise_lookup.loc[(row.layer, row.pair, row.window)]
            cagr_delta = (
                comparison["ann_return_delta_sync_minus_core"]
                if row.put_mode == "sync_put_total_ic"
                else 0.0
            )
            dd_delta = (
                comparison["max_dd_improvement_sync_minus_core"]
                if row.put_mode == "sync_put_total_ic"
                else 0.0
            )
        lines.append(
            f"| {row.layer} | {row.put_mode} | {row.window} | {fmt_pct(row.ann_return)} | {fmt_pct(row.max_dd)} | {fmt_pct(cagr_delta)} | {fmt_pct(dd_delta)} |"
        )
    primary_exposure = exposure[
        exposure["pair"].eq(pair_label(*PRIMARY_PAIR))
        | exposure["put_mode"].eq("base_core_put")
    ][
        [
            "layer",
            "pair",
            "put_mode",
            "overlay_holding_days",
            "put_trade_events",
            "put_cost_sum",
            "max_post_trade_put_mark_fraction",
            "max_actual_notional_fraction",
            "max_effective_delta_total",
            "max_target_delta_error",
        ]
    ]
    lines.extend(
        [
            "",
            "## Stability Classification",
            "",
            f"- 主观察线通过：{summary['primary_pair_pass']}；确认通过数：{summary['confirmation_pass_count']}。",
            f"- 通过线：{summary['passing_pairs']}。",
            "",
            "## Decision",
            "",
            f"- `{summary['decision']}`；本版仍未批准实盘。",
            "",
            "## User-Facing Summary",
            "",
            "同步Put是否值得采用，只按同一估值线的收益损失、回撤改善及模型/真实交叉确认判定。",
            "",
            "## Exposure and Timing",
            "",
            primary_exposure.to_markdown(index=False),
            "",
            f"- 新增IC开平仓同步时点审计行：{len(timing)}；失败：{int((~timing['same_day_trade_pass']).sum())}。",
            "",
            "## Integrity",
            "",
            f"- v4核心模式奇偶误差：{integrity['v4_core_only_parity_max_abs']:.3e}。",
            f"- v21底仓奇偶误差：{integrity['v21_base_parity_max_abs']:.3e}。",
            f"- 收益/现金恒等式误差：{max(integrity['return_identity_max_abs'], integrity['cash_identity_max_abs']):.3e}。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    git_before = git_status()
    upstream = verify_inputs()
    frames, _, market, market_checks = v1.v21.v20.v19.v18.load_close_inputs()
    chain, chain_audit = v1.load_active_chain(frames)
    frozen_daily = pd.read_csv(v1.V21_DAILY, parse_dates=["date"])
    frozen_schedule = pd.read_csv(
        v1.V21_SCHEDULE, parse_dates=["eval_date", "execution_date"]
    )
    frozen_trades = pd.read_csv(
        v1.V21_TRADES,
        parse_dates=[
            "signal_eval_date",
            "scheduled_execution_date",
            "actual_execution_date",
            "roll_request_date",
        ],
    )
    frozen_v4 = pd.read_csv(V4_DAILY, parse_dates=["date"])

    overlay_paths: dict[tuple[float, float], pd.DataFrame] = {}
    overlay_trade_paths: dict[tuple[float, float], pd.DataFrame] = {}
    for low, high in PAIRS:
        overlay, trades, _ = v1.simulate_overlay(chain, low, high)
        overlay_paths[(low, high)] = overlay
        overlay_trade_paths[(low, high)] = trades
    flat = v1.flat_overlay(chain)

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    put_trade_parts: list[pd.DataFrame] = []
    overlay_trade_parts: list[pd.DataFrame] = []
    for layer, start in (("model", v1.MODEL_START), ("real", v1.REAL_START)):
        base_put = v1.mainline_put_rows(frozen_daily, layer)
        base_candidate = f"{layer}__base_core_put"
        base_daily = v1.assemble_candidate(
            chain, flat, base_put, layer, base_candidate, "base_core_put", None, None
        )
        daily_parts.append(base_daily)
        base_schedule = v1.build_candidate_schedule(
            frozen_schedule, flat, layer, base_candidate, "core_put_only", None, None
        )
        base_schedule["put_mode"] = "base_core_put"
        schedule_parts.append(base_schedule)
        source_base_trade = frozen_trades[
            frozen_trades["candidate"].eq(f"{layer}_l190_mom25")
        ].copy()
        source_base_trade["candidate"] = base_candidate
        source_base_trade["layer"] = layer
        source_base_trade["pair"] = "base"
        source_base_trade["put_mode"] = "base_core_put"
        source_base_trade["source"] = "frozen_v21_core"
        put_trade_parts.append(source_base_trade)

        for low, high in PAIRS:
            pair = pair_label(low, high)
            overlay = overlay_paths[(low, high)]
            raw_overlay_trades = overlay_trade_paths[(low, high)]
            core_candidate = candidate_label(layer, low, high, "core_put_only")
            core_daily = v1.assemble_candidate(
                chain,
                overlay,
                base_put,
                layer,
                core_candidate,
                "core_put_only",
                low,
                high,
            )
            daily_parts.append(core_daily)
            core_schedule = v1.build_candidate_schedule(
                frozen_schedule,
                overlay,
                layer,
                core_candidate,
                "core_put_only",
                low,
                high,
            )
            schedule_parts.append(core_schedule)
            core_put_trades = source_base_trade.copy()
            core_put_trades["candidate"] = core_candidate
            core_put_trades["pair"] = pair
            core_put_trades["put_mode"] = "core_put_only"
            put_trade_parts.append(core_put_trades)

            sync_candidate = candidate_label(layer, low, high, "sync_put_total_ic")
            sync_schedule = v1.build_candidate_schedule(
                frozen_schedule,
                overlay,
                layer,
                sync_candidate,
                "sync_put_total_ic",
                low,
                high,
            )
            sync_put, sync_trades = v1.run_sync_put(
                frames, market, sync_schedule, layer, sync_candidate
            )
            sync_daily = v1.assemble_candidate(
                chain,
                overlay,
                sync_put,
                layer,
                sync_candidate,
                "sync_put_total_ic",
                low,
                high,
            )
            daily_parts.append(sync_daily)
            schedule_parts.append(sync_schedule)
            sync_trades["layer"] = layer
            sync_trades["pair"] = pair
            sync_trades["put_mode"] = "sync_put_total_ic"
            sync_trades["source"] = "rerun_scaled_total_ic_delta"
            put_trade_parts.append(sync_trades)

            for candidate, mode in (
                (core_candidate, "core_put_only"),
                (sync_candidate, "sync_put_total_ic"),
            ):
                copy = raw_overlay_trades[
                    raw_overlay_trades["execution_date"] >= start
                ].copy()
                copy["candidate"] = candidate
                copy["layer"] = layer
                copy["put_mode"] = mode
                overlay_trade_parts.append(copy)

    daily = (
        pd.concat(daily_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "date"])
        .reset_index(drop=True)
    )
    schedules = (
        pd.concat(schedule_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "execution_date"])
        .reset_index(drop=True)
    )
    put_trades = (
        pd.concat(put_trade_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "actual_execution_date"])
        .reset_index(drop=True)
    )
    overlay_trades = (
        pd.concat(overlay_trade_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "execution_date"])
        .reset_index(drop=True)
    )

    metrics, wide = v1.build_metrics(daily)
    pairwise = v1.pairwise_put_management(metrics)
    annual = v1.annual_metrics(daily)
    exposure = v1.exposure_diagnostics(daily, overlay_trades, put_trades)
    timing = timing_audit(schedules, overlay_trades, put_trades)
    decisions, summary = decide(pairwise, exposure)

    parity_errors: list[float] = []
    for low, high in PAIRS:
        new = daily[
            daily["candidate"].eq(candidate_label("model", low, high, "core_put_only"))
        ][["date", "cash_ret"]]
        old = frozen_v4[frozen_v4["candidate"].eq(f"L{low:.3f}_H{high:.3f}")][
            ["date", "cash_ret"]
        ]
        joined = new.merge(
            old, on="date", suffixes=("_v5", "_v4"), validate="one_to_one"
        )
        parity_errors.append(
            float((joined["cash_ret_v5"] - joined["cash_ret_v4"]).abs().max())
        )
    v4_parity = max(parity_errors)
    base_errors: list[float] = []
    for layer in ("model", "real"):
        new = daily[daily["candidate"].eq(f"{layer}__base_core_put")][
            ["date", "cash_ret"]
        ]
        old = frozen_daily[frozen_daily["candidate"].eq(f"{layer}_l190_mom25")][
            ["date", "cash_ret"]
        ]
        joined = new.merge(
            old, on="date", suffixes=("_v5", "_v21"), validate="one_to_one"
        )
        base_errors.append(
            float((joined["cash_ret_v5"] - joined["cash_ret_v21"]).abs().max())
        )
    v21_parity = max(base_errors)
    return_identity = (1.0 + daily["gross_ret"]) * (
        1.0 - daily["futures_cost_rate"]
    ) * (1.0 - daily["put_cost_rate"]) - 1.0
    return_error = float((daily["ret"] - return_identity).abs().max())
    cash_error = float(
        (daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * v1.CASH_DAILY))
        .abs()
        .max()
    )
    regular = schedules[~schedules["initial_exception"]]
    schedule_causality_failures = int(
        (regular["execution_date"] <= regular["eval_date"]).sum()
    )
    overlay_causality_failures = int(
        (overlay_trades["execution_date"] <= overlay_trades["signal_date"]).sum()
    )
    model_delta_error = float(
        exposure.loc[exposure["layer"].eq("model"), "max_target_delta_error"].max()
    )
    real_delta_error = float(
        exposure.loc[exposure["layer"].eq("real"), "max_target_delta_error"].max()
    )
    capital_failures = int(
        (
            exposure["max_post_trade_put_mark_fraction"]
            > exposure["min_post_trade_cash_before_put"] + 1e-12
        ).sum()
    )
    timing_formula_error = float(timing["target_formula_error"].max())
    timing_trade_failures = int((~timing["same_day_trade_pass"]).sum())
    integrity = {
        "candidate_count": int(daily["candidate"].nunique()),
        "daily_rows": len(daily),
        "schedule_rows": len(schedules),
        "overlay_trade_rows": len(overlay_trades),
        "put_trade_rows": len(put_trades),
        "duplicate_candidate_dates": int(daily.duplicated(["candidate", "date"]).sum()),
        "v4_core_only_parity_max_abs": v4_parity,
        "v21_base_parity_max_abs": v21_parity,
        "return_identity_max_abs": return_error,
        "cash_identity_max_abs": cash_error,
        "schedule_causality_failures": schedule_causality_failures,
        "overlay_causality_failures": overlay_causality_failures,
        "model_max_target_delta_error": model_delta_error,
        "real_max_target_delta_error": real_delta_error,
        "capital_failures": capital_failures,
        "timing_target_formula_max_abs": timing_formula_error,
        "timing_same_day_trade_failures": timing_trade_failures,
        "chain": chain_audit,
        "all_checks_passed": bool(
            daily["candidate"].nunique() == 14
            and not daily.duplicated(["candidate", "date"]).any()
            and v4_parity <= 1e-14
            and v21_parity <= 1e-14
            and return_error <= 1e-14
            and cash_error <= 1e-14
            and schedule_causality_failures == 0
            and overlay_causality_failures == 0
            and model_delta_error <= 1e-12
            and real_delta_error <= 0.02 + 1e-12
            and capital_failures == 0
            and timing_formula_error <= 1e-12
            and timing_trade_failures == 0
        ),
    }
    if not integrity["all_checks_passed"]:
        raise RuntimeError(f"Integrity checks failed: {integrity}")

    record = build_record(
        metrics, pairwise, decisions, exposure, timing, summary, integrity
    )
    OUTPUT.mkdir(parents=False, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    pairwise.to_csv(OUTPUT / "pairwise_put_management.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    overlay_trades.to_csv(OUTPUT / "overlay_trade_audit.csv", index=False)
    put_trades.to_csv(OUTPUT / "put_trade_audit.csv", index=False)
    schedules.to_csv(
        OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip"
    )
    exposure.to_csv(OUTPUT / "exposure_cost_delta.csv", index=False)
    timing.to_csv(OUTPUT / "timing_sync_audit.csv", index=False)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    command = "uv run ic_valuation_overlay_selected_put_sync_v5.py"
    (OUTPUT / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_sha256": sha256(Path(__file__)),
        "spec_sha256": SPEC_SHA256,
        "input_hashes": {
            str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()
        },
        "upstream": upstream,
        "market_checks": market_checks,
        "sample": {
            "model": [str(v1.MODEL_START.date()), str(v1.END.date())],
            "real": [str(v1.REAL_START.date()), str(v1.END.date())],
        },
        "candidate_bundle": {
            "pairs": [list(pair) for pair in PAIRS],
            "primary_pair": list(PRIMARY_PAIR),
            "put_modes": list(PUT_MODES),
        },
        "execution": {
            "valuation_signal": "T official close",
            "overlay_ic": "T+1 active IC official open",
            "sync_put": "same T+1 common close; core target multiplied by total IC units",
            "put_contract": "3m 95%; monthly replace; no daily Delta rebalance",
        },
        "cost_model": {
            "one_way_ic_and_put": v1.ONE_WAY_COST,
            "overlay_roll_round_trip": 2 * v1.ONE_WAY_COST,
            "margin_buffer_per_ic_unit": v1.MARGIN_RATE,
            "cash_annual": 0.03,
        },
        "decision": summary,
        "integrity": integrity,
        "warnings": [
            "No independent OOS",
            "Model Put is theoretical",
            "Real Put starts 2022-09-19; 10Y and 5Y unavailable",
            "Official open/close is not guaranteed fill or capacity evidence",
            "Normalized 1x extra IC; no integer futures account",
            "Sync scales only a nonzero core target; it is not permanent extra-sleeve insurance",
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

    model_metrics = metrics[metrics["layer"].eq("model")].rename(
        columns={"window": "segment", "actual_start": "start"}
    )
    model_metrics.to_csv(SCAN / "scan_summary.csv", index=False)
    wide[wide["layer"].eq("model")].to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "candidate_bundle",
            "baseline": {"mode": "core_put_only", "same_run": True},
            "candidate_grid": manifest["candidate_bundle"],
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["cost_model"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": {"v4": v4_parity, "v21": v21_parity},
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
