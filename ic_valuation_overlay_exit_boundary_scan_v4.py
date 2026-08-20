#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Focused lower-exit-boundary scan for the IC valuation overlay."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_valuation_overlay_entry_exit_scan_v2 as impl

ROOT = Path(__file__).resolve().parent
VERSION = "ic_valuation_overlay_exit_boundary_scan_v4"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "49721edc580711da6106fed3c691defae7eb93c8cdfe621550ed484a6973dfda"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_ic_valuation_overlay_exit_boundary_scan_v4"
)
V3_OUTPUT = ROOT / "outputs" / "ic_valuation_overlay_entry_exit_scan_v3"
V3_DAILY = V3_OUTPUT / "daily_candidates.csv.gz"
V3_MANIFEST = V3_OUTPUT / "output_manifest.json"
V3_INTEGRITY = V3_OUTPUT / "integrity_checks.json"

ENTRY_THRESHOLDS = (0.000, 0.125, 0.250, 0.375, 0.500, 0.625)
EXIT_THRESHOLDS = (0.875, 1.000, 1.125, 1.250, 1.375)
MIN_GAP = 0.500
OLD_PAIR = (1.000, 2.000)

INPUT_HASHES = {
    ROOT
    / "ic_valuation_overlay_entry_exit_scan_v2.py": "71e4253a439bfb8e5bc6c5a0d598a0efab4560fa6fe7f6ad4d864ee0c82ef259",
    ROOT
    / "ic_valuation_overlay_entry_exit_scan_v3.py": "6d66613b0c250992d8da870308737aec399eb2bdd521f66b4a48d270846590c6",
    ROOT
    / "docs"
    / "ic_valuation_overlay_entry_exit_scan_v3_spec.md": "01106713f0d347dda18a44714bf2c828ea9e5de7ebb5821f21605bd80efe74d7",
    V3_MANIFEST: "0ac668adca879f06502b9822209438a1b10625435e18fbba2838ac985ab1a7a9",
    V3_DAILY: "00c6fb2dd7f1344e74aa276d84f31c230d4ed579d943f4069e3b3f54f7e4a78b",
    V3_INTEGRITY: "db162d48e10977120fdf5d7ede3da5aae38e1d3b749e3bf2fc863f109f2fea97",
    ROOT
    / "ic_valuation_overlay_put_sync_v1.py": "e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36",
    impl.V21_DAILY: "11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a",
    impl.v1.IC_RAW: "4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104",
    impl.v1.IC_DAILY: "bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d",
    impl.SCORE_FILE: "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77",
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


def focused_grid() -> list[tuple[float, float]]:
    return [
        (low, high)
        for low in ENTRY_THRESHOLDS
        for high in EXIT_THRESHOLDS
        if high - low >= MIN_GAP - 1e-12
    ]


def all_pairs() -> list[tuple[float, float]]:
    return [*focused_grid(), OLD_PAIR]


def label(low: float, high: float) -> str:
    return impl.label(low, high)


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
    if len(focused_grid()) != 27 or len(all_pairs()) != 28:
        raise RuntimeError("Preregistered grid mismatch")
    if OLD_PAIR in focused_grid():
        raise RuntimeError("Old pair must be an anchor, not a selection candidate")
    return {"frozen_input_count": len(INPUT_HASHES), "focused_candidates": 27}


def metric_table(daily: pd.DataFrame, return_col: str, sample: str) -> pd.DataFrame:
    table = impl.metric_rows(daily, return_col, sample)
    table["calmar"] = table["ann_return"] / table["max_dd"].abs()
    return table


def add_comparisons(metrics: pd.DataFrame) -> pd.DataFrame:
    result = metrics.copy()
    base = result[result["candidate"].eq("base_core_put")].set_index("window")
    old = result[result["candidate"].eq(label(*OLD_PAIR))].set_index("window")
    for prefix, reference in (("core", base), ("old", old)):
        result[f"{prefix}_ann_return"] = result["window"].map(reference["ann_return"])
        result[f"{prefix}_max_dd"] = result["window"].map(reference["max_dd"])
        result[f"ann_return_delta_vs_{prefix}"] = (
            result["ann_return"] - result[f"{prefix}_ann_return"]
        )
        result[f"max_dd_improvement_vs_{prefix}"] = (
            result["max_dd"] - result[f"{prefix}_max_dd"]
        )
    return result


def decide(
    metrics: pd.DataFrame,
    cycles: pd.DataFrame,
    drawdowns: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    base = metrics[metrics["candidate"].eq("base_core_put")].set_index("window")
    old = metrics[metrics["candidate"].eq(label(*OLD_PAIR))].set_index("window")
    actual_cycles = cycles[cycles["sample"].eq("actual_ic_2015")].set_index("pair")
    proxy_cycles = cycles[cycles["sample"].eq("index_proxy_2007")].set_index("pair")
    dd = drawdowns.set_index("candidate")
    rows: list[dict[str, Any]] = []
    for low, high in focused_grid():
        candidate = label(low, high)
        sample = metrics[metrics["candidate"].eq(candidate)].set_index("window")
        actual_completed = int(actual_cycles.loc[candidate, "completed_cycles"])
        proxy_completed = int(proxy_cycles.loc[candidate, "completed_cycles"])
        cycle_gate = actual_completed >= 2 and proxy_completed >= 3
        core_peak_off = int(dd.loc[candidate, "overlay_held_at_base_peak"]) == 0
        long_return_gate = all(
            sample.loc[window, "ann_return"] - base.loc[window, "ann_return"]
            >= 0.02 - 1e-12
            for window in ("full", "last_10y", "last_5y")
        )
        short_return_floor = all(
            sample.loc[window, "ann_return"] - base.loc[window, "ann_return"]
            >= -0.01 - 1e-12
            for window in ("last_3y", "last_1y")
        )
        long_dd_cap = all(
            sample.loc[window, "max_dd"] >= -0.35 - 1e-12
            for window in ("full", "last_10y", "last_5y")
        )
        old_dd_all_windows = all(
            sample.loc[window, "max_dd"] > old.loc[window, "max_dd"] + 1e-12
            for window in impl.WINDOWS
        )
        full_dd_improvement_vs_old = float(
            sample.loc["full", "max_dd"] - old.loc["full", "max_dd"]
        )
        full_calmar = float(sample.loc["full", "calmar"])
        calmar_above_core = full_calmar > float(base.loc["full", "calmar"]) + 1e-12
        risk_gate = bool(
            cycle_gate
            and core_peak_off
            and long_return_gate
            and short_return_floor
            and long_dd_cap
            and old_dd_all_windows
            and full_dd_improvement_vs_old >= 0.20 - 1e-12
            and calmar_above_core
        )
        row: dict[str, Any] = {
            "candidate": candidate,
            "low_threshold": low,
            "high_threshold": high,
            "actual_completed_cycles": actual_completed,
            "index_proxy_completed_cycles": proxy_completed,
            "cycle_gate_pass": cycle_gate,
            "overlay_off_at_core_dd_peak": core_peak_off,
            "long_return_gate_pass": long_return_gate,
            "short_return_floor_pass": short_return_floor,
            "long_dd_cap_pass": long_dd_cap,
            "old_dd_all_windows_pass": old_dd_all_windows,
            "full_dd_improvement_vs_old": full_dd_improvement_vs_old,
            "full_calmar": full_calmar,
            "core_full_calmar": float(base.loc["full", "calmar"]),
            "calmar_above_core_pass": calmar_above_core,
            "risk_gate_pass": risk_gate,
        }
        for window in impl.WINDOWS:
            row[f"ann_return_{window}"] = float(sample.loc[window, "ann_return"])
            row[f"max_dd_{window}"] = float(sample.loc[window, "max_dd"])
            row[f"ann_return_delta_vs_core_{window}"] = float(
                sample.loc[window, "ann_return"] - base.loc[window, "ann_return"]
            )
            row[f"max_dd_improvement_vs_core_{window}"] = float(
                sample.loc[window, "max_dd"] - base.loc[window, "max_dd"]
            )
            row[f"ann_return_delta_vs_old_{window}"] = float(
                sample.loc[window, "ann_return"] - old.loc[window, "ann_return"]
            )
            row[f"max_dd_improvement_vs_old_{window}"] = float(
                sample.loc[window, "max_dd"] - old.loc[window, "max_dd"]
            )
        rows.append(row)
    decisions = pd.DataFrame(rows)
    passed = decisions[decisions["risk_gate_pass"]].sort_values(
        ["full_calmar", "ann_return_full"], ascending=False
    )
    selected = None if passed.empty else str(passed.iloc[0]["candidate"])

    lookup = decisions.set_index(["low_threshold", "high_threshold"])
    width_rows: list[dict[str, Any]] = []
    for row in decisions.itertuples(index=False):
        neighbors = {
            "entry_lower": (round(row.low_threshold - 0.125, 3), row.high_threshold),
            "entry_upper": (round(row.low_threshold + 0.125, 3), row.high_threshold),
            "exit_lower": (row.low_threshold, round(row.high_threshold - 0.125, 3)),
            "exit_upper": (row.low_threshold, round(row.high_threshold + 0.125, 3)),
        }
        supports: list[bool] = []
        for side, key in neighbors.items():
            exists = key in lookup.index
            neighbor_calmar = (
                float(lookup.loc[key, "full_calmar"]) if exists else np.nan
            )
            retention = (
                neighbor_calmar / row.full_calmar
                if exists and row.full_calmar > 1e-12
                else np.nan
            )
            support = bool(
                exists
                and lookup.loc[key, "risk_gate_pass"]
                and retention >= 0.80 - 1e-12
            )
            supports.append(support)
            width_rows.append(
                {
                    "candidate": row.candidate,
                    "side": side,
                    "neighbor_candidate": label(*key) if exists else None,
                    "neighbor_exists": exists,
                    "center_full_calmar": row.full_calmar,
                    "neighbor_full_calmar": neighbor_calmar,
                    "retention_ratio": retention,
                    "neighbor_risk_gate_pass": bool(lookup.loc[key, "risk_gate_pass"])
                    if exists
                    else False,
                    "neighbor_support": support,
                }
            )
        decisions.loc[decisions["candidate"].eq(row.candidate), "width_supported"] = (
            all(supports)
        )
        decisions.loc[
            decisions["candidate"].eq(row.candidate), "supporting_neighbor_count"
        ] = sum(supports)
    width = pd.DataFrame(width_rows)
    selected_width = bool(
        selected is not None
        and decisions.set_index("candidate").loc[selected, "width_supported"]
    )
    if selected is None:
        decision = "no_focused_pair_passed_risk_gates"
        stability = "reject"
    elif selected_width:
        decision = "watchlist_risk_first_candidate_width_supported"
        stability = "wide_stable"
    else:
        decision = "watchlist_risk_first_candidate_edge_or_peak"
        stability = "peak_only"
    summary = {
        "decision": decision,
        "stability_label": stability,
        "selected_candidate": selected,
        "selected_width_supported": selected_width,
        "risk_gate_pass_count": int(decisions["risk_gate_pass"].sum()),
        "width_supported_pass_count": int(
            (decisions["risk_gate_pass"] & decisions["width_supported"]).sum()
        ),
        "risk_gate_candidates": passed["candidate"].tolist(),
        "old_pair": label(*OLD_PAIR),
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return decisions, width, summary


def fmt_pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value) * 100:.2f}%"


def build_record(
    metrics: pd.DataFrame,
    decisions: pd.DataFrame,
    cycles: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    selected = summary["selected_candidate"]
    ranked = decisions.sort_values(["risk_gate_pass", "full_calmar"], ascending=False)
    show_candidates = ["base_core_put", label(*OLD_PAIR)]
    if selected is not None:
        show_candidates.append(selected)
    show_candidates.extend(ranked.head(5)["candidate"].tolist())
    show_candidates = list(dict.fromkeys(show_candidates))
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
        "把退出阈值从v3下边界1.250继续向0.875扩展，寻找低估增仓的风险优先平台。",
        "",
        "## Implementation Anchor",
        "",
        "- 复用v1/v2真实活跃IC、T+1官方开盘、实际滚动、现金与收益函数。",
        "- 底仓Put固定为v21 `model_l190_mom25`；新增仓仍不增加Put。",
        "- 旧1.000/2.000、固定底仓及v3重合候选均同批重跑并做逐日奇偶校验。",
        "",
        "## Data Snapshot",
        "",
        "- 正式：2015-04-16至2026-08-14，CFFEX IC实际活跃链及模型Put。",
        "- 诊断：2007-01-15至2026-08-17，中证500价格指数和固定经济分数；无上市前贴水与Put。",
        "- 本轮没有下载或缓存写入；本地冻结文件哈希均通过。",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- 估值T收盘确认，T+1活跃IC官方开盘成交；每边1bp，月滚双边2bp。",
        "- 每1倍IC占30%保证金/缓冲，余额年化3%；官方开盘不等于保证成交或容量证明。",
        "",
        "## Runtime Override Plan",
        "",
        "只运行27组扩边候选及旧规则锚点；不修改生产、实盘或既有冻结文件。",
        "",
        "## Commands",
        "",
        "见 `command_log.txt`。",
        "",
        "## Output Files",
        "",
        "强制窗口、日线、交易、周期、宽度、回撤重合、决策和完整性文件均保存在本目录；标准参数工件保存在对应scan目录。",
        "",
        "## Full-Sample Results",
        "",
        ranked[
            [
                "candidate",
                "ann_return_full",
                "max_dd_full",
                "full_calmar",
                "actual_completed_cycles",
                "index_proxy_completed_cycles",
                "risk_gate_pass",
                "width_supported",
            ]
        ]
        .head(10)
        .to_markdown(index=False),
        "",
        "## Window Results",
        "",
        "| 候选 | 窗口 | CAGR | MaxDD | 相对底仓CAGR | 相对底仓回撤 | 相对旧规则CAGR | 相对旧规则回撤 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    display = metrics[metrics["candidate"].isin(show_candidates)].sort_values(
        ["candidate", "window"]
    )
    for row in display.itertuples(index=False):
        lines.append(
            f"| {row.candidate} | {row.window} | {fmt_pct(row.ann_return)} | {fmt_pct(row.max_dd)} | {fmt_pct(row.ann_return_delta_vs_core)} | {fmt_pct(row.max_dd_improvement_vs_core)} | {fmt_pct(row.ann_return_delta_vs_old)} | {fmt_pct(row.max_dd_improvement_vs_old)} |"
        )
    selected_cycles = (
        cycles[cycles["pair"].eq(selected)][["sample", "completed_cycles"]].to_dict(
            "records"
        )
        if selected is not None
        else []
    )
    lines.extend(
        [
            "",
            "## Stability Classification",
            "",
            f"- 风险门槛通过 {summary['risk_gate_pass_count']} 组；四向80%宽度通过 {summary['width_supported_pass_count']} 组。",
            f"- 机械最高Calmar：`{selected}`；四向宽度支持：{summary['selected_width_supported']}。",
            f"- 机械最高点正式/2007周期：{selected_cycles}。",
            "",
            "## Decision",
            "",
            f"- `{summary['decision']}`；`selected_candidate={selected}`。",
            "- 本版只确定风险优先阈值观察线；不构成实盘授权。下一层Put同步与否必须另版测试。",
            "",
            "## User-Facing Summary",
            "",
            "本轮回答1.250以下是否还有更好的退出点，并把低回撤、长期超额、事件数和四向宽度同时纳入。",
            "",
            "## Integrity",
            "",
            f"- v3重合路径逐日最大误差：{integrity['v3_overlap_parity_max_abs']:.3e}。",
            f"- 收益/现金恒等式最大误差：{max(integrity['return_identity_max_abs'], integrity['cash_identity_max_abs']):.3e}。",
            f"- 因果失败：{integrity['signal_execution_causality_failures']}。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    git_before = git_status()
    upstream = verify_inputs()
    frames, _, _, market_checks = impl.v1.v21.v20.v19.v18.load_close_inputs()
    chain, chain_audit = impl.v1.load_active_chain(frames)
    frozen_v21 = pd.read_csv(impl.V21_DAILY, parse_dates=["date"])
    frozen_v3 = pd.read_csv(V3_DAILY, parse_dates=["date"])
    base_put = impl.v1.mainline_put_rows(frozen_v21, "model")

    flat = impl.v1.flat_overlay(chain)
    base = impl.v1.assemble_candidate(
        chain, flat, base_put, "model", "base_core_put", "base_core_put", None, None
    )
    base["low_threshold"] = np.nan
    base["high_threshold"] = np.nan
    daily_parts = [base]
    actual_trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []
    for low, high in all_pairs():
        candidate = label(low, high)
        overlay, trades, cycle = impl.v1.simulate_overlay(chain, low, high)
        combined = impl.v1.assemble_candidate(
            chain, overlay, base_put, "model", candidate, "core_put_only", low, high
        )
        daily_parts.append(combined)
        if len(trades):
            trades = trades.copy()
            trades["sample"] = "actual_ic_2015"
            trades["candidate"] = candidate
            actual_trade_parts.append(trades)
        cycle_rows.append({**cycle, "sample": "actual_ic_2015", "pair": candidate})
    daily = (
        pd.concat(daily_parts, ignore_index=True, sort=False)
        .sort_values(["candidate", "date"])
        .reset_index(drop=True)
    )
    actual_trades = pd.concat(actual_trade_parts, ignore_index=True, sort=False)

    scores = (
        pd.read_csv(
            impl.SCORE_FILE,
            parse_dates=["date"],
            usecols=["date", "price_close", "unbounded_median_knot"],
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    proxy_daily_parts: list[pd.DataFrame] = []
    proxy_trade_parts: list[pd.DataFrame] = []
    for low, high in all_pairs():
        proxy_daily, proxy_trades, proxy_cycle = impl.simulate_index_proxy(
            scores, low, high
        )
        proxy_daily_parts.append(proxy_daily)
        if len(proxy_trades):
            proxy_trade_parts.append(proxy_trades)
        cycle_rows.append(proxy_cycle)
    index_daily = (
        pd.concat(proxy_daily_parts, ignore_index=True)
        .sort_values(["candidate", "date"])
        .reset_index(drop=True)
    )
    proxy_trades = pd.concat(proxy_trade_parts, ignore_index=True, sort=False)
    trades = pd.concat([actual_trades, proxy_trades], ignore_index=True, sort=False)
    cycles = pd.DataFrame(cycle_rows)

    metrics = add_comparisons(metric_table(daily, "cash_ret", "actual_ic_model_put"))
    wide = impl.wide_metrics(metrics)
    proxy_metrics = metric_table(index_daily, "cash_ret", "index_proxy_2007")
    drawdowns, base_dd_dates = impl.build_drawdown_audit(daily)
    decisions, ridge_width, summary = decide(metrics, cycles, drawdowns)
    annual = impl.annual_metrics(daily)
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

    overlap_candidates = sorted(set(daily["candidate"]) & set(frozen_v3["candidate"]))
    parity_errors: list[float] = []
    for candidate in overlap_candidates:
        new = daily[daily["candidate"].eq(candidate)][["date", "cash_ret"]]
        old = frozen_v3[frozen_v3["candidate"].eq(candidate)][["date", "cash_ret"]]
        joined = new.merge(
            old, on="date", suffixes=("_v4", "_v3"), validate="one_to_one"
        )
        parity_errors.append(
            float((joined["cash_ret_v4"] - joined["cash_ret_v3"]).abs().max())
        )
    parity_max = max(parity_errors)
    return_identity = (1.0 + daily["gross_ret"]) * (
        1.0 - daily["futures_cost_rate"]
    ) * (1.0 - daily["put_cost_rate"]) - 1.0
    return_error = float((daily["ret"] - return_identity).abs().max())
    cash_error = float(
        (daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * impl.v1.CASH_DAILY))
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
        "v3_overlap_candidate_count": len(overlap_candidates),
        "v3_overlap_parity_max_abs": parity_max,
        "return_identity_max_abs": return_error,
        "cash_identity_max_abs": cash_error,
        "signal_execution_causality_failures": causality_failures,
        "base_drawdown_dates": {
            key: str(value) for key, value in base_dd_dates.items()
        },
        "chain": chain_audit,
        "all_checks_passed": bool(
            daily["candidate"].nunique() == 29
            and index_daily["candidate"].nunique() == 28
            and not daily.duplicated(["candidate", "date"]).any()
            and not index_daily.duplicated(["candidate", "date"]).any()
            and len(overlap_candidates) == 14
            and parity_max <= 1e-14
            and return_error <= 1e-14
            and cash_error <= 1e-14
            and causality_failures == 0
        ),
    }
    if not integrity["all_checks_passed"]:
        raise RuntimeError(f"Integrity checks failed: {integrity}")

    record = build_record(metrics, decisions, cycles, summary, integrity)
    OUTPUT.mkdir(parents=False, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
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
    command = "uv run ic_valuation_overlay_exit_boundary_scan_v4.py"
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
            "actual_ic_model_put": [str(impl.MODEL_START.date()), str(impl.END.date())],
            "index_proxy": [
                str(scores["date"].min().date()),
                str(scores["date"].max().date()),
            ],
        },
        "candidate_grid": {
            "entry_thresholds": list(ENTRY_THRESHOLDS),
            "exit_thresholds": list(EXIT_THRESHOLDS),
            "minimum_gap": MIN_GAP,
            "focused_pairs": len(focused_grid()),
            "old_anchor": list(OLD_PAIR),
        },
        "selection": {
            "objective": "highest full Calmar among preregistered risk-gate passes",
            "width": "four axis neighbors each pass risk gates and retain 80% full Calmar",
        },
        "execution": {
            "valuation_signal": "T official close",
            "actual_overlay": "T+1 active IC official open",
            "index_proxy": "T+1 next index official close",
            "put": "frozen v21 model_l190_mom25 for core only",
        },
        "cost_model": {
            "one_way_futures": impl.v1.ONE_WAY_COST,
            "overlay_roll_round_trip": 2 * impl.v1.ONE_WAY_COST,
            "margin_buffer_per_ic_unit": impl.v1.MARGIN_RATE,
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
            "Risk-first selection does not require recent return parity with old 2x exposure",
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

    scan_summary = metrics.rename(
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
                "risk_seeking_anchor": label(*OLD_PAIR),
                "capital_baseline": "base_core_put",
                "same_run": True,
            },
            "candidate_grid": manifest["candidate_grid"],
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["cost_model"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": parity_max,
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
