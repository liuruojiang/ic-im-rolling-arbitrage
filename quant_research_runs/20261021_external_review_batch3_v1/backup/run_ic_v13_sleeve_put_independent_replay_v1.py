"""Independent 510500 Put ledgers for IC v1.3 core, momentum and grid sleeves."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_roll_momentum_stage1_v1 as ic_stage1
import ic_roll_momentum_stage2_put_v2 as ic_put
import ic_roll_momentum_stage3_grid_v1 as ic_grid


ROOT = Path(__file__).resolve().parent
VERSION = "ic_v13_sleeve_put_independent_replay_v1"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = ROOT / "quant_param_scan_runs" / f"20260903_{VERSION}"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "69ff6cec46000b372e548106024120390fc055ef02d71748351d3218a8c7900b"

BASE_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage1_v1" / "daily_nav.csv.gz"
GRID_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage3_grid_v1" / "daily_nav.csv.gz"
TARGET_DAILY = ROOT / "outputs" / "ic_mainline_v1_3" / "target_schedule.csv.gz"
OFFICIAL_DAILY = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "ic_daily.csv.gz"
REAL_START = pd.Timestamp("2022-09-19")
END = pd.Timestamp("2026-08-14")

SLEEVES = ("combined_current", "core", "momentum", "grid")
CANDIDATES: dict[str, tuple[str, ...]] = {
    "no_put": (),
    "independent_core_only": ("core",),
    "independent_core_momentum": ("core", "momentum"),
    "independent_core_grid": ("core", "grid"),
    "independent_all": ("core", "momentum", "grid"),
    "authoritative_current_combined": ("combined_current",),
}
WINDOWS: tuple[tuple[str, pd.DateOffset | None], ...] = (
    ("full", None),
    ("last_10y", pd.DateOffset(years=10)),
    ("last_5y", pd.DateOffset(years=5)),
    ("last_3y", pd.DateOffset(years=3)),
    ("last_1y", pd.DateOffset(years=1)),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_text(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return result.stdout.strip()


def verify_preregistration() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH_FILE, BASE_DAILY, GRID_DAILY, TARGET_DAILY, OFFICIAL_DAILY):
        if not path.exists():
            raise FileNotFoundError(path)
    actual = sha256(SPEC)
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if actual != SPEC_SHA256 or sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen specification hash mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal output or staging folder already exists")
    if not SCAN.exists():
        raise FileNotFoundError(SCAN)
    return {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (SPEC, BASE_DAILY, GRID_DAILY, TARGET_DAILY, OFFICIAL_DAILY)
    }


def load_base_components() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = pd.read_csv(BASE_DAILY, parse_dates=["date"], low_memory=False)
    grid = pd.read_csv(
        GRID_DAILY,
        parse_dates=["date"],
        usecols=["date", "grid_overlay_held_eod", "grid_net_increment"],
    )
    target = pd.read_csv(TARGET_DAILY, parse_dates=["date"], low_memory=False)
    for label, frame in (("base", base), ("grid", grid), ("target", target)):
        if frame.empty or frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
            raise RuntimeError(f"{label} date integrity failure")
    required = [
        "momentum_execution_weight", "grid_held_eod", "total_ic_units",
        "core_put_target_delta", "momentum_put_target_delta", "grid_put_target_delta",
        "total_put_target_delta",
    ]
    if not set(required).issubset(target.columns):
        raise RuntimeError("IC v1.3 target columns missing")
    frame = base.merge(
        target[["date", *required]], on="date", validate="one_to_one"
    ).merge(grid, on="date", validate="one_to_one")
    if len(frame) != 2756 or frame["date"].iloc[0] != pd.Timestamp("2015-04-16") or frame["date"].iloc[-1] != END:
        raise RuntimeError("Unexpected IC fixed sample")
    if (frame["grid_held_eod"] - frame["grid_overlay_held_eod"]).abs().max() > 1e-12:
        raise RuntimeError("Grid state parity failure")

    weight = frame["momentum_execution_weight"].astype(float)
    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    momentum_cost = (
        ic_stage1.ONE_WAY_COST * turnover
        + 2.0 * ic_stage1.ONE_WAY_COST * weight * frame["roll_event"].astype(float)
    )
    momentum_gross = weight * frame["ic_gross_ret"].astype(float)
    momentum_net = (1.0 + momentum_gross) * (1.0 - momentum_cost) - 1.0
    momentum_cash = 1.0 - ic_grid.MARGIN_RATE * weight
    momentum_ret = momentum_net + momentum_cash * ic_grid.CASH_DAILY
    blend_cash = 0.5 * frame["bare_roll_ic_cash_weight"].astype(float) + 0.5 * momentum_cash
    blend_ret = 0.5 * frame["bare_roll_ic_ret"].astype(float) + 0.5 * momentum_ret
    frame["base_non_cash_ret"] = blend_ret - blend_cash * ic_grid.CASH_DAILY
    frame["pre_put_cash_weight"] = blend_cash - ic_grid.MARGIN_RATE * frame["grid_held_eod"].astype(float)
    frame["momentum_turnover"] = turnover
    frame["momentum_cost_rate"] = 0.5 * momentum_cost
    if frame["pre_put_cash_weight"].lt(-1e-12).any():
        raise RuntimeError("Negative pre-Put cash")

    engine_base = base.copy()
    engine_base["momentum_weight"] = weight.to_numpy()
    selected = ic_put.v1.build_v2_schedule(engine_base)
    selected = selected.merge(
        target[["date", "grid_held_eod"]].rename(columns={"date": "execution_date"}),
        on="execution_date",
        how="left",
        validate="many_to_one",
    )
    if selected["grid_held_eod"].isna().any():
        raise RuntimeError("Grid schedule alignment failure")
    return frame, engine_base, selected


def build_schedule(selected: pd.DataFrame, sleeve: str) -> pd.DataFrame:
    schedule = selected.copy()
    full = schedule["v2_target_delta"].astype(float)
    valuation = schedule["valuation_tier_new"].astype(float) * 0.25
    weight = schedule["momentum_weight"].astype(float)
    grid = schedule["grid_held_eod"].astype(float)
    targets = {
        "core": 0.5 * full,
        "momentum": 0.5 * weight * valuation,
        "grid": grid * full,
    }
    targets["combined_current"] = targets["core"] + targets["momentum"]
    target = targets[sleeve]
    if target.lt(-1e-12).any() or target.gt(1.0 + 1e-12).any():
        raise RuntimeError(f"{sleeve} target outside 0..1")
    schedule["target_delta"] = target
    schedule["target_fraction"] = target
    schedule["binary_target_fraction"] = target
    schedule["three_tier_target_fraction"] = target
    schedule["signal_variant"] = f"independent_{sleeve}"
    schedule["candidate"] = f"independent_{sleeve}"
    schedule["schedule_candidate"] = f"independent_{sleeve}"
    return schedule


def run_ledger(
    sleeve: str,
    schedule: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    engine = ic_put.v1.put_engine
    model, model_trades = engine.run_model_delta(
        frames["ic"], schedule, market, f"independent_{sleeve}", roll_dates
    )
    real, real_trades = engine.run_real_delta(
        frames["ic"], schedule, frames, market, f"independent_{sleeve}", roll_dates
    )
    overlay = pd.concat(
        [model[model["date"].lt(REAL_START)].assign(layer="model"),
         real[real["date"].ge(REAL_START)].assign(layer="real")],
        ignore_index=True,
    ).sort_values("date").reset_index(drop=True)
    trades = pd.concat(
        [model_trades.assign(layer="model", sleeve=sleeve),
         real_trades.assign(layer="real", sleeve=sleeve)],
        ignore_index=True,
    )
    trades["actual_execution_date"] = pd.to_datetime(trades["actual_execution_date"])
    trades = pd.concat(
        [trades[trades["layer"].eq("model") & trades["actual_execution_date"].lt(REAL_START)],
         trades[trades["layer"].eq("real") & trades["actual_execution_date"].ge(REAL_START)]],
        ignore_index=True,
    ).sort_values(["actual_execution_date", "sleeve"]).reset_index(drop=True)
    return overlay, trades


def combine_candidate(
    frame: pd.DataFrame,
    ledgers: dict[str, pd.DataFrame],
    candidate: str,
    sleeves: tuple[str, ...],
) -> pd.DataFrame:
    result = frame[[
        "date", "momentum_execution_weight", "grid_held_eod", "total_ic_units",
        "base_non_cash_ret", "grid_net_increment", "pre_put_cash_weight",
        "momentum_turnover", "momentum_cost_rate",
    ]].copy()
    pnl = np.zeros(len(frame))
    mark = np.zeros(len(frame))
    target = np.zeros(len(frame))
    cost_factor = np.ones(len(frame))
    for sleeve in sleeves:
        overlay = ledgers[sleeve]
        if len(overlay) != len(frame) or not overlay["date"].equals(frame["date"]):
            raise RuntimeError(f"{sleeve} overlay date mismatch")
        pnl += overlay["put_pnl_ret"].astype(float).to_numpy()
        mark += overlay["put_mark_fraction"].astype(float).to_numpy()
        target += overlay["target_delta"].astype(float).to_numpy()
        cost_factor *= 1.0 - overlay["put_cost_rate"].astype(float).to_numpy()
    cash = result["pre_put_cash_weight"].astype(float).to_numpy() - mark
    if cash.min() < -1e-12:
        raise RuntimeError(f"{candidate} negative cash: {cash.min()}")
    result["candidate"] = candidate
    result["put_pnl_ret"] = pnl
    result["put_cost_rate"] = 1.0 - cost_factor
    result["put_mark_fraction"] = mark
    result["put_target_delta"] = target
    result["cash_weight"] = np.clip(cash, 0.0, None)
    result["ret"] = (
        (1.0 + result["base_non_cash_ret"].astype(float).to_numpy() + pnl) * cost_factor
        - 1.0
        + result["grid_net_increment"].astype(float).to_numpy()
        + result["cash_weight"].astype(float).to_numpy() * ic_grid.CASH_DAILY
    )
    if not np.isfinite(result["ret"]).all() or result["ret"].le(-1.0).any():
        raise RuntimeError(f"{candidate} invalid returns")
    result["nav"] = (1.0 + result["ret"]).cumprod()
    result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
    return result


def metric_row(sample: pd.DataFrame) -> dict[str, Any]:
    if sample.empty:
        return {
            "available": False, "start": "", "end": "", "rows": 0,
            "total_return": np.nan, "ann_return": np.nan, "ann_vol": np.nan,
            "sharpe_repo": np.nan, "max_dd": np.nan, "final_nav": np.nan,
            "put_cost_total": np.nan, "max_put_mark_fraction": np.nan,
            "min_cash_weight": np.nan,
        }
    ret = sample["ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    ann_return = float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0)
    ann_vol = float(ret.std(ddof=0) * math.sqrt(252.0))
    dd = nav / nav.cummax() - 1.0
    return {
        "available": True,
        "start": sample["date"].iloc[0].date().isoformat(),
        "end": sample["date"].iloc[-1].date().isoformat(),
        "rows": int(len(sample)),
        "total_return": float(nav.iloc[-1] - 1.0),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe_repo": ann_return / ann_vol if ann_vol > 1e-12 else 0.0,
        "max_dd": float(dd.min()),
        "final_nav": float(nav.iloc[-1]),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "max_put_mark_fraction": float(sample["put_mark_fraction"].max()),
        "min_cash_weight": float(sample["cash_weight"].min()),
    }


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    end = daily["date"].max()
    mixed_rows: list[dict[str, Any]] = []
    real_rows: list[dict[str, Any]] = []
    for candidate, frame in daily.groupby("candidate", sort=False):
        frame = frame.sort_values("date")
        for segment, offset in WINDOWS:
            sample = frame if offset is None else frame[frame["date"].ge(end - offset)]
            mixed_rows.append({"candidate": candidate, "segment": segment, **metric_row(sample)})
        real = frame[frame["date"].ge(REAL_START)]
        for segment, years in (("real_full", None), ("real_10y", 10), ("real_5y", 5), ("real_3y", 3), ("real_1y", 1)):
            if years in (10, 5):
                row = metric_row(real.iloc[0:0])
                row["unavailable_reason"] = "real_510500_put_history_shorter_than_requested_window"
            else:
                sample = real if years is None else real[real["date"].ge(end - pd.DateOffset(years=years))]
                row = metric_row(sample)
                row["unavailable_reason"] = ""
            real_rows.append({"candidate": candidate, "segment": segment, **row})
    mixed = pd.DataFrame(mixed_rows)
    real_metrics = pd.DataFrame(real_rows)
    wide_rows = []
    for candidate, block in mixed.groupby("candidate", sort=False):
        values: dict[str, Any] = {"candidate": candidate}
        for row in block.itertuples(index=False):
            values[f"ann_return_{row.segment}"] = row.ann_return
            values[f"max_dd_{row.segment}"] = row.max_dd
            values[f"sharpe_repo_{row.segment}"] = row.sharpe_repo
        wide_rows.append(values)
    return mixed, pd.DataFrame(wide_rows), real_metrics


def comparison(metrics: pd.DataFrame, real_metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pairs = (
        ("momentum_put", "independent_core_only", "independent_core_momentum"),
        ("grid_put_with_momentum", "independent_core_momentum", "independent_all"),
        ("grid_put_without_momentum", "independent_core_only", "independent_core_grid"),
        ("split_vs_current_implementation", "authoritative_current_combined", "independent_core_momentum"),
    )
    for layer, source in (("mixed", metrics), ("real", real_metrics)):
        for comparison_name, baseline, candidate in pairs:
            for segment in source["segment"].unique():
                b = source[(source["candidate"].eq(baseline)) & (source["segment"].eq(segment))]
                c = source[(source["candidate"].eq(candidate)) & (source["segment"].eq(segment))]
                if len(b) != 1 or len(c) != 1:
                    continue
                br, cr = b.iloc[0], c.iloc[0]
                available = bool(br["available"] and cr["available"])
                rows.append({
                    "layer": layer, "comparison": comparison_name, "segment": segment,
                    "baseline": baseline, "candidate": candidate, "available": available,
                    "ann_return_delta": float(cr["ann_return"] - br["ann_return"]) if available else np.nan,
                    "ann_vol_delta": float(cr["ann_vol"] - br["ann_vol"]) if available else np.nan,
                    "sharpe_delta": float(cr["sharpe_repo"] - br["sharpe_repo"]) if available else np.nan,
                    "max_dd_improvement": float(cr["max_dd"] - br["max_dd"]) if available else np.nan,
                    "put_cost_delta": float(cr["put_cost_total"] - br["put_cost_total"]) if available else np.nan,
                })
    return pd.DataFrame(rows)


def pct(value: float) -> str:
    return "N/A" if not np.isfinite(value) else f"{100.0 * value:.2f}%"


def write_record(
    folder: Path,
    mixed: pd.DataFrame,
    real: pd.DataFrame,
    comparisons: pd.DataFrame,
    decision: str,
    stability: str,
    audit: dict[str, Any],
    git_before: str,
    git_after: str,
) -> None:
    order = list(CANDIDATES)
    labels = {
        "no_put": "无Put诊断",
        "independent_core_only": "仅核心Put（独立账本）",
        "independent_core_momentum": "核心+动量Put（独立账本）",
        "independent_core_grid": "核心+网格Put（独立账本）",
        "independent_all": "核心+动量+网格Put（独立账本）",
        "authoritative_current_combined": "现行核心+动量合并账本",
    }
    real_table = real[real["segment"].isin(["real_full", "real_3y", "real_1y"])].copy()
    lines = ["|路径|真实Full|真实3Y|真实1Y|", "|---|---:|---:|---:|"]
    for candidate in order:
        block = real_table[real_table["candidate"].eq(candidate)].set_index("segment")
        cells = [f"{pct(block.loc[s, 'ann_return'])} / {pct(block.loc[s, 'max_dd'])}" for s in ("real_full", "real_3y", "real_1y")]
        lines.append(f"|{labels[candidate]}|{'|'.join(cells)}|")
    mixed_full = mixed[mixed["segment"].eq("full")].set_index("candidate")
    grid_cmp = comparisons[(comparisons["layer"].eq("real")) & (comparisons["comparison"].eq("grid_put_with_momentum")) & (comparisons["segment"].eq("real_full"))].iloc[0]
    mom_cmp = comparisons[(comparisons["layer"].eq("real")) & (comparisons["comparison"].eq("momentum_put")) & (comparisons["segment"].eq("real_full"))].iloc[0]
    text = f"""# IC v1.3 动量仓与网格仓 Put 独立账本重放 v1

## Run Metadata

- 状态：研究重放完成；未批准实盘。
- 决定：`{decision}`；稳定性：`{stability}`。
- 工作区：`{ROOT}`；数据截止：2026-08-14；真实 510500 Put 起点：2022-09-19。
- Source-change rule：`research_only_no_source_change`；没有修改 IC v1.3、冻结 V2、Poe 或交易配置。
- Git 状态（前/后）：`{git_before or 'clean'}` / `{git_after or 'clean'}`。

## Research Question

在 IC v1.3 期货、动量和网格全部不变时，分别用独立 510500 Put 账本检验动量仓与网格仓保护。

## Implementation Anchor

- 正式链：`ic_roll_momentum_stage1_v1` + `ic_roll_momentum_stage2_put_v2` + `ic_roll_momentum_stage3_grid_v1` + `ic_mainline_v1_3`。
- 核心目标 `0.5×完整V2`；动量目标 `0.5×执行权重×纯估值档`；网格目标 `网格状态×完整V2`。
- 现行合并账本收益奇偶最大误差：`{audit['official_ret_parity_max_abs']:.3e}`；现金奇偶最大误差：`{audit['official_cash_parity_max_abs']:.3e}`。

## Data Snapshot

- 混合参考段 2015-04-16—2026-08-14：2022-09-19 前理论 Put，之后真实 Put。
- 真实期权段 2022-09-19—2026-08-14，共 {audit['real_option_days']} 个交易日；真实原始期权历史 {audit['raw_option_history_rows']} 行、{audit['raw_option_contracts']} 个证券代码。
- 真实 5Y/10Y 为 N/A，因为可执行 510500 Put 历史不足。

## Cost and Execution Assumptions

- IC 与 Put 每边 1bp；每 1 倍 IC 使用 30%保证金/缓冲；剩余现金年化 3%。
- 约 3 个月、95% Put；T 收盘目标、T+1 共同交易日收盘执行；网格期货同日开盘成交。
- 每个袖独立选约、整数张取整、换月、成交顺延和计费；未按父 Put 收益比例缩放。

## Runtime Override Plan

- 只创建新研究规格、脚本和产物；现行默认同批重跑并逐日对账。

## Commands

见 `command_log.txt`。

## Output Files

- 标准表：`scan_summary.csv`、`window_metrics.csv`、`real_option_metrics.csv`。
- 审计：`daily_candidates.csv.gz`、`put_target_schedules.csv.gz`、`put_ledgers.csv.gz`、`put_trades.csv.gz`、`comparisons.csv`、`integrity_checks.json`。

## Full-Sample Results

- 混合 Full 现行路径：CAGR {pct(float(mixed_full.loc['authoritative_current_combined','ann_return']))}，最大回撤 {pct(float(mixed_full.loc['authoritative_current_combined','max_dd']))}。
- 真实 Full 动量 Put 边际：CAGR {pct(float(mom_cmp['ann_return_delta']))}，回撤改善 {pct(float(mom_cmp['max_dd_improvement']))}。
- 真实 Full 网格 Put 边际（在核心+动量之上）：CAGR {pct(float(grid_cmp['ann_return_delta']))}，回撤改善 {pct(float(grid_cmp['max_dd_improvement']))}。

## Window Results

每格为 CAGR / MaxDD。

{chr(10).join(lines)}

完整 Full/10Y/5Y/3Y/1Y 和真实期权窗口见 CSV；真实 5Y/10Y 显式保留 N/A。

## Stability Classification

- `{stability}`：网格 Put 按预注册门槛判定；动量 Put 同时报告独立账本相对仅核心 Put 的边际。
- 最大真实成交顺延：{audit['max_real_trade_delay_days']} 个交易日；最低现金：{audit['min_cash_weight_all_candidates']:.2%}。

## Decision

- `{decision}`。
- 本结果只支持研究判断，不构成交易指令，也不自动修改登记主线。

## User-Facing Summary

IC 当前已经保护动量仓；网格仓是否值得增加 Put，以 `independent_all` 相对 `independent_core_momentum` 的真实期权结果为主。
"""
    (folder / "record.md").write_text(text, encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    input_hashes = verify_preregistration()
    git_before = git_text("status", "--short")
    frame, _engine_base, selected = load_base_components()
    frames, _valuation, market, market_checks = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll_dates = ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])

    ledgers: dict[str, pd.DataFrame] = {}
    schedules: list[pd.DataFrame] = []
    trades: list[pd.DataFrame] = []
    for sleeve in SLEEVES:
        schedule = build_schedule(selected, sleeve)
        overlay, ledger_trades = run_ledger(sleeve, schedule, frames, market, roll_dates)
        ledgers[sleeve] = overlay
        schedules.append(schedule.assign(sleeve=sleeve))
        trades.append(ledger_trades)

    candidate_frames = [
        combine_candidate(frame, ledgers, candidate, sleeves)
        for candidate, sleeves in CANDIDATES.items()
    ]
    daily = pd.concat(candidate_frames, ignore_index=True)
    official = pd.read_csv(OFFICIAL_DAILY, parse_dates=["date"])
    current = daily[daily["candidate"].eq("authoritative_current_combined")].sort_values("date")
    if not current["date"].reset_index(drop=True).equals(official["date"].reset_index(drop=True)):
        raise RuntimeError("Official IC date parity failure")
    ret_error = float(np.max(np.abs(current["ret"].to_numpy() - official["ret"].to_numpy())))
    cash_error = float(np.max(np.abs(current["cash_weight"].to_numpy() - official["cash_weight"].to_numpy())))
    if max(ret_error, cash_error) > 1e-12:
        raise RuntimeError(f"Official current parity failure: ret={ret_error}, cash={cash_error}")

    mixed, wide, real_metrics = build_metrics(daily)
    comparisons = comparison(mixed, real_metrics)
    ledger_daily = pd.concat(
        [ledger.assign(sleeve=sleeve) for sleeve, ledger in ledgers.items()],
        ignore_index=True,
    )
    all_trades = pd.concat(trades, ignore_index=True)
    all_schedules = pd.concat(schedules, ignore_index=True)

    def cmp_value(name: str, segment: str, field: str) -> float:
        row = comparisons[
            comparisons["layer"].eq("real")
            & comparisons["comparison"].eq(name)
            & comparisons["segment"].eq(segment)
        ]
        return float(row.iloc[0][field])

    grid_gate = (
        cmp_value("grid_put_with_momentum", "real_full", "max_dd_improvement") >= 0.01
        and cmp_value("grid_put_with_momentum", "real_full", "ann_return_delta") >= -0.01
        and cmp_value("grid_put_with_momentum", "real_3y", "max_dd_improvement") >= -0.01
        and float(comparisons[
            comparisons["layer"].eq("mixed")
            & comparisons["comparison"].eq("grid_put_with_momentum")
            & comparisons["segment"].eq("full")
        ].iloc[0]["max_dd_improvement"]) > -0.01
        and float(comparisons[
            comparisons["layer"].eq("mixed")
            & comparisons["comparison"].eq("grid_put_with_momentum")
            & comparisons["segment"].eq("last_5y")
        ].iloc[0]["max_dd_improvement"]) > -0.01
    )
    decision = "watchlist_grid_put_research_only" if grid_gate else "keep_default_grid_unprotected"
    stability = "data_sensitive" if grid_gate else "reject"

    real_trades = all_trades[all_trades["layer"].eq("real")]
    target_identity = {
        "core": 0.5 * all_schedules["v2_target_delta"].astype(float),
        "momentum": (
            0.5 * all_schedules["momentum_weight"].astype(float)
            * all_schedules["valuation_tier_new"].astype(float) * 0.25
        ),
        "grid": (
            all_schedules["grid_held_eod"].astype(float)
            * all_schedules["v2_target_delta"].astype(float)
        ),
    }
    target_errors = {}
    for sleeve, expected in target_identity.items():
        rows = all_schedules[all_schedules["sleeve"].eq(sleeve)]
        target_errors[sleeve] = float(
            np.max(np.abs(rows["target_delta"].to_numpy() - expected.loc[rows.index].to_numpy()))
        )

    histories = frames["histories"]
    audit = {
        "official_ret_parity_max_abs": ret_error,
        "official_cash_parity_max_abs": cash_error,
        "target_identity_max_abs_by_sleeve": target_errors,
        "candidate_rows": {k: int(len(v)) for k, v in daily.groupby("candidate")},
        "real_option_days": int(frame["date"].ge(REAL_START).sum()),
        "raw_option_history_rows": int(len(histories)),
        "raw_option_contracts": int(histories["security_id"].nunique()),
        "raw_option_history_start": pd.Timestamp(histories["date"].min()).date().isoformat(),
        "raw_option_history_end": pd.Timestamp(histories["date"].max()).date().isoformat(),
        "real_trade_events_by_sleeve": {
            k: int(v) for k, v in real_trades.groupby("sleeve").size().items()
        },
        "max_real_trade_delay_days": int(real_trades["delay_trading_days"].max()) if len(real_trades) else 0,
        "min_cash_weight_all_candidates": float(daily["cash_weight"].min()),
        "max_put_mark_fraction_all_candidates": float(daily["put_mark_fraction"].max()),
        "market_check_keys": sorted(str(key) for key in market_checks),
        "grid_gate_pass": bool(grid_gate),
        "decision": decision,
        "stability_label": stability,
    }

    STAGING.mkdir(parents=True, exist_ok=False)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    ledger_daily.to_csv(STAGING / "put_ledgers.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    all_schedules.to_csv(STAGING / "put_target_schedules.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    all_trades.to_csv(STAGING / "put_trades.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    mixed.to_csv(STAGING / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(STAGING / "window_metrics.csv", index=False, encoding="utf-8-sig")
    real_metrics.to_csv(STAGING / "real_option_metrics.csv", index=False, encoding="utf-8-sig")
    comparisons.to_csv(STAGING / "comparisons.csv", index=False, encoding="utf-8-sig")
    (STAGING / "integrity_checks.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    data_manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "input_hashes": input_hashes,
        "raw_option_data": {
            "history_rows": audit["raw_option_history_rows"],
            "contracts": audit["raw_option_contracts"],
            "start": audit["raw_option_history_start"],
            "end": audit["raw_option_history_end"],
        },
        "official_loader_market_checks": audit["market_check_keys"],
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(data_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    command = f"{Path(sys.executable).name} -X utf8 {Path(__file__).name}"
    elapsed = time.perf_counter() - started
    (STAGING / "command_log.txt").write_text(f"cwd={ROOT}\n{command}\nelapsed_sec={elapsed:.3f}\n", encoding="utf-8")
    git_after = git_text("status", "--short")
    write_record(STAGING, mixed, real_metrics, comparisons, decision, stability, audit, git_before, git_after)
    output_hashes = {
        path.name: sha256(path) for path in STAGING.iterdir() if path.is_file()
    }
    (STAGING / "output_manifest.json").write_text(json.dumps(output_hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(STAGING, OUTPUT)

    for name in ("scan_summary.csv", "window_metrics.csv", "record.md", "command_log.txt"):
        shutil.copy2(OUTPUT / name, SCAN / name)
    scan_meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    scan_meta.update({
        "scan_type": "overlay_study",
        "baseline": {"candidate": "authoritative_current_combined"},
        "candidate_grid": list(CANDIDATES),
        "data_snapshot": {"start": "2015-04-16", "end": "2026-08-14", "real_option_start": "2022-09-19"},
        "cost_model": {"put_side_cost": 0.0001, "margin_buffer_per_ic_unit": 0.30, "cash_annual": 0.03},
        "source_hashes": input_hashes,
        "parity_check": {"ret_max_abs": ret_error, "cash_max_abs": cash_error},
        "decision": decision,
        "stability_label": stability,
        "warnings": ["2022-09-19前Put为理论代理", "真实5Y和10Y不可用"],
    })
    (SCAN / "scan_meta.json").write_text(json.dumps(scan_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "decision": decision, "audit": audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
