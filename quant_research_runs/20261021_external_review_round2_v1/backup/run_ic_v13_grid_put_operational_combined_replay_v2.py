"""Operational one-ledger replay for adding grid Put to IC v1.3."""

from __future__ import annotations

import hashlib
import json
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

import ic_roll_momentum_stage2_put_v2 as ic_put
import run_ic_v13_sleeve_put_independent_replay_v1 as v1


ROOT = Path(__file__).resolve().parent
VERSION = "ic_v13_grid_put_operational_combined_replay_v2"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = ROOT / "quant_param_scan_runs" / f"20260903_{VERSION}"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "678928e74c737ede9188d5b5b505734678650f5b1a04bef5701774960a4850d2"
CANDIDATES = (
    "authoritative_current_combined",
    "operational_current_plus_grid_combined",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=False,
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def verify() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH_FILE, v1.SPEC, v1.OUTPUT / "output_manifest.json"):
        if not path.exists():
            raise FileNotFoundError(path)
    actual = sha256(SPEC)
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if actual != SPEC_SHA256 or sidecar != SPEC_SHA256:
        raise RuntimeError("v2 specification hash mismatch")
    manifest = json.loads((v1.OUTPUT / "output_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        path = v1.OUTPUT / name
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"v1 output changed: {name}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("v2 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError(SCAN)
    return {
        str(SPEC.relative_to(ROOT)): actual,
        str(v1.SPEC.relative_to(ROOT)): sha256(v1.SPEC),
        str((v1.OUTPUT / "output_manifest.json").relative_to(ROOT)): sha256(v1.OUTPUT / "output_manifest.json"),
    }


def operational_schedule(selected: pd.DataFrame) -> pd.DataFrame:
    schedule = v1.build_schedule(selected, "combined_current")
    grid_target = (
        schedule["grid_held_eod"].astype(float)
        * schedule["v2_target_delta"].astype(float)
    )
    schedule["grid_increment_target_delta"] = grid_target
    schedule["target_delta"] = schedule["target_delta"].astype(float) + grid_target
    schedule["target_fraction"] = schedule["target_delta"]
    schedule["binary_target_fraction"] = schedule["target_delta"]
    schedule["three_tier_target_fraction"] = schedule["target_delta"]
    schedule["signal_variant"] = "operational_current_plus_grid_combined"
    schedule["candidate"] = "operational_current_plus_grid_combined"
    schedule["schedule_candidate"] = "operational_current_plus_grid_combined"
    if schedule["target_delta"].lt(-1e-12).any() or schedule["target_delta"].gt(2.0 + 1e-12).any():
        raise RuntimeError("Operational combined target outside 0..2")
    return schedule


def metrics_diff(mixed: pd.DataFrame, real: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for layer, source in (("mixed", mixed), ("real", real)):
        for segment in source["segment"].unique():
            base = source[(source["candidate"].eq(CANDIDATES[0])) & source["segment"].eq(segment)]
            trial = source[(source["candidate"].eq(CANDIDATES[1])) & source["segment"].eq(segment)]
            if len(base) != 1 or len(trial) != 1:
                continue
            b, t = base.iloc[0], trial.iloc[0]
            available = bool(b["available"] and t["available"])
            rows.append({
                "layer": layer, "segment": segment, "available": available,
                "ann_return_delta": float(t["ann_return"] - b["ann_return"]) if available else np.nan,
                "ann_vol_delta": float(t["ann_vol"] - b["ann_vol"]) if available else np.nan,
                "sharpe_delta": float(t["sharpe_repo"] - b["sharpe_repo"]) if available else np.nan,
                "max_dd_improvement": float(t["max_dd"] - b["max_dd"]) if available else np.nan,
                "put_cost_delta": float(t["put_cost_total"] - b["put_cost_total"]) if available else np.nan,
            })
    return pd.DataFrame(rows)


def get_diff(diff: pd.DataFrame, layer: str, segment: str, field: str) -> float:
    return float(diff[(diff["layer"].eq(layer)) & (diff["segment"].eq(segment))].iloc[0][field])


def pct(value: float) -> str:
    return "N/A" if not np.isfinite(value) else f"{100 * value:.2f}%"


def write_record(
    folder: Path,
    mixed: pd.DataFrame,
    real: pd.DataFrame,
    diff: pd.DataFrame,
    decision: str,
    audit: dict[str, Any],
) -> None:
    lines = ["|路径|真实Full|真实3Y|真实1Y|", "|---|---:|---:|---:|"]
    labels = {CANDIDATES[0]: "现行核心+动量合并Put", CANDIDATES[1]: "现行+网格Put合并目标"}
    for candidate in CANDIDATES:
        block = real[real["candidate"].eq(candidate)].set_index("segment")
        cells = [f"{pct(float(block.loc[s,'ann_return']))} / {pct(float(block.loc[s,'max_dd']))}" for s in ("real_full", "real_3y", "real_1y")]
        lines.append(f"|{labels[candidate]}|{'|'.join(cells)}|")
    real_full_return = get_diff(diff, "real", "real_full", "ann_return_delta")
    real_full_dd = get_diff(diff, "real", "real_full", "max_dd_improvement")
    real_3y_return = get_diff(diff, "real", "real_3y", "ann_return_delta")
    real_3y_dd = get_diff(diff, "real", "real_3y", "max_dd_improvement")
    text = f"""# IC v1.3 网格 Put 合并目标交易账本重放 v2

## Run Metadata

- 状态：研究完成；未批准实盘。
- 决定：`{decision}`；稳定性：`reject`。
- 数据截止 2026-08-14；真实 510500 Put 自 2022-09-19。
- Source-change rule：`research_only_no_source_change`。

## Research Question

在实际单一 Put 账本中，把网格仓完整 V2 目标加到现行核心+动量合并目标，是否值得。

## Implementation Anchor

- 两条路径都由原始 510500 Put 数据重跑选约、整数张、盯市、换月、延期与成本。
- 现行路径对正式日收益最大误差：`{audit['official_ret_parity_max_abs']:.3e}`。
- 网格增量目标恒等式最大误差：`{audit['grid_target_identity_max_abs']:.3e}`。

## Data Snapshot

- 混合参考段：2015-04-16—2026-08-14，2022-09-19 前为理论 Put。
- 真实期权段：2022-09-19—2026-08-14；5Y/10Y 为 N/A。

## Cost and Execution Assumptions

- 约 3 个月、95% Put；T 收盘目标、T+1 收盘执行；每边 1bp。
- 网格期货 T+1 开盘，Put 同日收盘；每 1 倍 IC 占 30%保证金/缓冲，剩余现金年化 3%。

## Runtime Override Plan

- 只增加合并网格目标候选；信号、仓位和生产文件不变。

## Commands

见 `command_log.txt`。

## Output Files

标准窗口表、真实窗口表、逐日组合、Put 账本、交易和目标日程均随输出保存。

## Full-Sample Results

- 真实 Full：网格 Put 令 CAGR 变化 {pct(real_full_return)}，最大回撤改善 {pct(real_full_dd)}。
- 真实 3Y：CAGR 变化 {pct(real_3y_return)}，最大回撤改善 {pct(real_3y_dd)}。

## Window Results

每格为 CAGR / MaxDD。

{chr(10).join(lines)}

## Stability Classification

- `reject`：按预注册的收益容忍度与回撤门槛判断。
- 真实额外 Put 成本累计 {pct(get_diff(diff, 'real', 'real_full', 'put_cost_delta'))}；最大 Put 市值占用 {audit['max_put_mark_fraction_candidate']:.2%}；最低现金 {audit['min_cash_weight_candidate']:.2%}。

## Decision

- `{decision}`。保持 IC v1.3 网格仓不加 Put；现行动量 Put 不变。

## User-Facing Summary

IC 动量仓已经有纯估值 Put；给网格仓再加完整 V2 Put，在实际合并账本中仍未达到值得采用的风险收益交换。
"""
    (folder / "record.md").write_text(text, encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    input_hashes = verify()
    git_before = git_status()
    frame, _engine_base, selected = v1.load_base_components()
    frames, _valuation, market, market_checks = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll_dates = ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])

    current_schedule = v1.build_schedule(selected, "combined_current")
    grid_schedule = operational_schedule(selected)
    current_ledger, current_trades = v1.run_ledger("combined_current", current_schedule, frames, market, roll_dates)
    grid_ledger, grid_trades = v1.run_ledger("operational_all_combined", grid_schedule, frames, market, roll_dates)
    ledgers = {"combined_current": current_ledger, "operational_all_combined": grid_ledger}
    current = v1.combine_candidate(frame, ledgers, CANDIDATES[0], ("combined_current",))
    trial = v1.combine_candidate(frame, ledgers, CANDIDATES[1], ("operational_all_combined",))
    daily = pd.concat([current, trial], ignore_index=True)

    official = pd.read_csv(v1.OFFICIAL_DAILY, parse_dates=["date"])
    ret_error = float(np.max(np.abs(current["ret"].to_numpy() - official["ret"].to_numpy())))
    cash_error = float(np.max(np.abs(current["cash_weight"].to_numpy() - official["cash_weight"].to_numpy())))
    if max(ret_error, cash_error) > 1e-12:
        raise RuntimeError("Current path parity failure")
    expected_grid = grid_schedule["grid_held_eod"].astype(float) * grid_schedule["v2_target_delta"].astype(float)
    current_target = current_schedule["target_delta"].astype(float)
    identity_error = float(np.max(np.abs(grid_schedule["target_delta"].astype(float) - current_target - expected_grid)))
    if identity_error > 1e-12:
        raise RuntimeError("Grid target identity failure")

    mixed, wide, real = v1.build_metrics(daily)
    diff = metrics_diff(mixed, real)
    gate = (
        get_diff(diff, "real", "real_full", "max_dd_improvement") >= 0.01
        and get_diff(diff, "real", "real_full", "ann_return_delta") >= -0.01
        and get_diff(diff, "real", "real_3y", "max_dd_improvement") >= -0.01
        and get_diff(diff, "mixed", "full", "max_dd_improvement") > -0.01
        and get_diff(diff, "mixed", "last_5y", "max_dd_improvement") > -0.01
    )
    decision = "watchlist_grid_put_research_only" if gate else "keep_default_grid_unprotected"
    audit = {
        "official_ret_parity_max_abs": ret_error,
        "official_cash_parity_max_abs": cash_error,
        "grid_target_identity_max_abs": identity_error,
        "grid_gate_pass": bool(gate),
        "decision": decision,
        "real_trade_events_current": int((current_trades["layer"].eq("real")).sum()),
        "real_trade_events_candidate": int((grid_trades["layer"].eq("real")).sum()),
        "max_real_delay_candidate": int(grid_trades.loc[grid_trades["layer"].eq("real"), "delay_trading_days"].max()),
        "max_put_mark_fraction_candidate": float(trial["put_mark_fraction"].max()),
        "min_cash_weight_candidate": float(trial["cash_weight"].min()),
        "market_check_keys": sorted(str(key) for key in market_checks),
    }

    STAGING.mkdir(parents=True, exist_ok=False)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    pd.concat([current_ledger.assign(path=CANDIDATES[0]), grid_ledger.assign(path=CANDIDATES[1])], ignore_index=True).to_csv(STAGING / "put_ledgers.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    pd.concat([current_schedule.assign(path=CANDIDATES[0]), grid_schedule.assign(path=CANDIDATES[1])], ignore_index=True).to_csv(STAGING / "put_target_schedules.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    pd.concat([current_trades.assign(path=CANDIDATES[0]), grid_trades.assign(path=CANDIDATES[1])], ignore_index=True).to_csv(STAGING / "put_trades.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    mixed.to_csv(STAGING / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(STAGING / "window_metrics.csv", index=False, encoding="utf-8-sig")
    real.to_csv(STAGING / "real_option_metrics.csv", index=False, encoding="utf-8-sig")
    diff.to_csv(STAGING / "comparisons.csv", index=False, encoding="utf-8-sig")
    (STAGING / "integrity_checks.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "data_manifest.json").write_text(json.dumps({"generated_at": datetime.now().astimezone().isoformat(), "input_hashes": input_hashes, "market_checks": audit["market_check_keys"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    elapsed = time.perf_counter() - started
    command = f"{Path(sys.executable).name} -X utf8 {Path(__file__).name}"
    (STAGING / "command_log.txt").write_text(f"cwd={ROOT}\n{command}\nelapsed_sec={elapsed:.3f}\n", encoding="utf-8")
    write_record(STAGING, mixed, real, diff, decision, audit)
    (STAGING / "output_manifest.json").write_text(json.dumps({p.name: sha256(p) for p in STAGING.iterdir() if p.is_file()}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(STAGING, OUTPUT)

    for name in ("record.md", "scan_summary.csv", "window_metrics.csv", "command_log.txt"):
        shutil.copy2(OUTPUT / name, SCAN / name)
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update({
        "scan_type": "overlay_study",
        "baseline": {"candidate": CANDIDATES[0]},
        "candidate_grid": list(CANDIDATES),
        "data_snapshot": {"start": "2015-04-16", "end": "2026-08-14", "real_option_start": "2022-09-19"},
        "cost_model": {"put_side_cost": 0.0001, "margin_buffer_per_ic_unit": 0.30, "cash_annual": 0.03},
        "decision": decision,
        "stability_label": "reject" if not gate else "data_sensitive",
        "parity_check": {"ret_max_abs": ret_error, "cash_max_abs": cash_error},
        "warnings": ["2022-09-19前Put为理论代理", "真实5Y和10Y不可用"],
        "git_status_before": git_before,
        "git_status_after": git_status(),
    })
    (SCAN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "audit": audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
