"""Normalize IC v1.3 Put-scope replay metrics to the official repo convention."""

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


ROOT = Path(__file__).resolve().parent
VERSION = "ic_v13_put_scope_final_metrics_v3"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = ROOT / "quant_param_scan_runs" / f"20260903_{VERSION}"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "8f98bda34541085673ce26cfc5fa4e01a9762ab933d57b27271eec948337f4cb"
V1 = ROOT / "outputs" / "ic_v13_sleeve_put_independent_replay_v1"
V2 = ROOT / "outputs" / "ic_v13_grid_put_operational_combined_replay_v2"
V1_DAILY = V1 / "daily_candidates.csv.gz"
V2_DAILY = V2 / "daily_candidates.csv.gz"
OFFICIAL_METRICS = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "metrics_by_window.csv"
END = pd.Timestamp("2026-08-14")
REAL_START = pd.Timestamp("2022-09-19")
CANDIDATES = (
    "no_put",
    "core_only_single_ledger",
    "current_core_momentum_combined",
    "current_plus_grid_combined",
)
WINDOW_STARTS = {
    "full": pd.Timestamp("2015-04-16"),
    "last_10y": END - pd.DateOffset(years=10),
    "last_5y": END - pd.DateOffset(years=5),
    "last_3y": END - pd.DateOffset(years=3),
    "last_1y": END - pd.DateOffset(years=1),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(["git", "status", "--short"], cwd=ROOT, check=False, capture_output=True, text=True)
    return result.stdout.strip()


def verify_manifest(folder: Path) -> None:
    manifest = json.loads((folder / "output_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        path = folder / name
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen replay artifact changed: {path}")


def verify() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH_FILE, V1_DAILY, V2_DAILY, OFFICIAL_METRICS):
        if not path.exists():
            raise FileNotFoundError(path)
    actual = sha256(SPEC)
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if actual != SPEC_SHA256 or sidecar != SPEC_SHA256:
        raise RuntimeError("v3 specification hash mismatch")
    verify_manifest(V1)
    verify_manifest(V2)
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("v3 output or staging exists")
    if not SCAN.exists():
        raise FileNotFoundError(SCAN)
    return {str(path.relative_to(ROOT)): sha256(path) for path in (SPEC, V1_DAILY, V2_DAILY, OFFICIAL_METRICS)}


def load_daily() -> pd.DataFrame:
    v1 = pd.read_csv(V1_DAILY, parse_dates=["date"], low_memory=False)
    v2 = pd.read_csv(V2_DAILY, parse_dates=["date"], low_memory=False)
    selected = pd.concat(
        [
            v1[v1["candidate"].isin(["no_put", "independent_core_only"])],
            v2[v2["candidate"].isin(["authoritative_current_combined", "operational_current_plus_grid_combined"])],
        ],
        ignore_index=True,
    )
    selected["candidate"] = selected["candidate"].map(
        {
            "no_put": "no_put",
            "independent_core_only": "core_only_single_ledger",
            "authoritative_current_combined": "current_core_momentum_combined",
            "operational_current_plus_grid_combined": "current_plus_grid_combined",
        }
    )
    if selected["candidate"].isna().any():
        raise RuntimeError("Candidate mapping failure")
    for candidate, frame in selected.groupby("candidate"):
        frame = frame.sort_values("date")
        if len(frame) != 2756 or frame["date"].duplicated().any() or frame["date"].iloc[0] != pd.Timestamp("2015-04-16") or frame["date"].iloc[-1] != END:
            raise RuntimeError(f"{candidate} daily integrity failure")
    return selected.sort_values(["candidate", "date"]).reset_index(drop=True)


def metric(sample: pd.DataFrame, include_initial_return: bool) -> dict[str, Any]:
    sample = sample.sort_values("date").copy()
    if not include_initial_return:
        sample = sample.iloc[1:].copy()
    ret = sample["ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1))
    trough_index = dd.idxmin()
    peak_index = nav.loc[:trough_index].idxmax()
    return {
        "available": True,
        "start": sample["date"].iloc[0].date().isoformat(),
        "end": sample["date"].iloc[-1].date().isoformat(),
        "rows": int(len(sample)),
        "total_return": float(nav.iloc[-1] - 1.0),
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0),
        "ann_vol": std * math.sqrt(252.0),
        "sharpe_repo": float(ret.mean()) / std * math.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(dd.min()),
        "dd_peak_date": sample.loc[peak_index, "date"].date().isoformat(),
        "dd_trough_date": sample.loc[trough_index, "date"].date().isoformat(),
        "final_nav": float(nav.iloc[-1]),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "max_put_mark_fraction": float(sample["put_mark_fraction"].max()),
        "min_cash_weight": float(sample["cash_weight"].min()),
    }


def unavailable() -> dict[str, Any]:
    return {
        "available": False, "start": "", "end": "", "rows": 0,
        "total_return": np.nan, "ann_return": np.nan, "ann_vol": np.nan,
        "sharpe_repo": np.nan, "max_dd": np.nan, "dd_peak_date": "",
        "dd_trough_date": "", "final_nav": np.nan, "put_cost_total": np.nan,
        "max_put_mark_fraction": np.nan, "min_cash_weight": np.nan,
    }


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    mixed_rows: list[dict[str, Any]] = []
    real_rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        frame = daily[daily["candidate"].eq(candidate)].sort_values("date")
        for segment, start in WINDOW_STARTS.items():
            mixed_rows.append({"candidate": candidate, "segment": segment, **metric(frame[frame["date"].ge(start)], segment == "full")})
        real_frame = frame[frame["date"].ge(REAL_START)]
        for segment, start in (
            ("real_full", REAL_START), ("real_10y", None), ("real_5y", None),
            ("real_3y", END - pd.DateOffset(years=3)),
            ("real_1y", END - pd.DateOffset(years=1)),
        ):
            if start is None:
                row = unavailable()
                row["unavailable_reason"] = "real_510500_put_history_shorter_than_requested_window"
            else:
                row = metric(real_frame[real_frame["date"].ge(start)], False)
                row["unavailable_reason"] = ""
            real_rows.append({"candidate": candidate, "segment": segment, **row})
    mixed = pd.DataFrame(mixed_rows)
    real = pd.DataFrame(real_rows)
    wide_rows = []
    for candidate, block in mixed.groupby("candidate", sort=False):
        row: dict[str, Any] = {"candidate": candidate}
        for value in block.itertuples(index=False):
            row[f"ann_return_{value.segment}"] = value.ann_return
            row[f"max_dd_{value.segment}"] = value.max_dd
            row[f"sharpe_repo_{value.segment}"] = value.sharpe_repo
        wide_rows.append(row)
    return mixed, pd.DataFrame(wide_rows), real


def build_comparisons(mixed: pd.DataFrame, real: pd.DataFrame) -> pd.DataFrame:
    rows = []
    pairs = (
        ("momentum_put_marginal", "core_only_single_ledger", "current_core_momentum_combined"),
        ("grid_put_marginal", "current_core_momentum_combined", "current_plus_grid_combined"),
    )
    for layer, source in (("mixed", mixed), ("real", real)):
        for name, baseline, candidate in pairs:
            for segment in source["segment"].unique():
                b = source[(source["candidate"].eq(baseline)) & source["segment"].eq(segment)].iloc[0]
                c = source[(source["candidate"].eq(candidate)) & source["segment"].eq(segment)].iloc[0]
                available = bool(b["available"] and c["available"])
                rows.append({
                    "layer": layer, "comparison": name, "segment": segment,
                    "baseline": baseline, "candidate": candidate, "available": available,
                    "ann_return_delta": float(c["ann_return"] - b["ann_return"]) if available else np.nan,
                    "ann_vol_delta": float(c["ann_vol"] - b["ann_vol"]) if available else np.nan,
                    "sharpe_delta": float(c["sharpe_repo"] - b["sharpe_repo"]) if available else np.nan,
                    "max_dd_improvement": float(c["max_dd"] - b["max_dd"]) if available else np.nan,
                    "put_cost_delta": float(c["put_cost_total"] - b["put_cost_total"]) if available else np.nan,
                })
    return pd.DataFrame(rows)


def value(comparisons: pd.DataFrame, layer: str, name: str, segment: str, field: str) -> float:
    return float(comparisons[
        comparisons["layer"].eq(layer) & comparisons["comparison"].eq(name) & comparisons["segment"].eq(segment)
    ].iloc[0][field])


def pct(x: float) -> str:
    return "N/A" if not np.isfinite(x) else f"{x:.2%}"


def write_record(folder: Path, mixed: pd.DataFrame, real: pd.DataFrame, comparisons: pd.DataFrame, audit: dict[str, Any]) -> None:
    labels = {
        "no_put": "无Put",
        "core_only_single_ledger": "仅核心Put",
        "current_core_momentum_combined": "现行核心+动量Put",
        "current_plus_grid_combined": "现行+网格Put",
    }
    real_table = ["|路径|真实Full CAGR/MaxDD/Sharpe|真实3Y|真实1Y|", "|---|---:|---:|---:|"]
    for candidate in CANDIDATES:
        block = real[real["candidate"].eq(candidate)].set_index("segment")
        cells = []
        for segment in ("real_full", "real_3y", "real_1y"):
            row = block.loc[segment]
            cells.append(f"{pct(float(row.ann_return))} / {pct(float(row.max_dd))} / {float(row.sharpe_repo):.3f}")
        real_table.append(f"|{labels[candidate]}|{'|'.join(cells)}|")
    mixed_table = ["|路径|Full|10Y|5Y|3Y|1Y|", "|---|---:|---:|---:|---:|---:|"]
    for candidate in CANDIDATES:
        block = mixed[mixed["candidate"].eq(candidate)].set_index("segment")
        cells = [f"{pct(float(block.loc[s,'ann_return']))} / {pct(float(block.loc[s,'max_dd']))}" for s in WINDOW_STARTS]
        mixed_table.append(f"|{labels[candidate]}|{'|'.join(cells)}|")
    mom_r = value(comparisons, "real", "momentum_put_marginal", "real_full", "ann_return_delta")
    mom_d = value(comparisons, "real", "momentum_put_marginal", "real_full", "max_dd_improvement")
    mom_s = value(comparisons, "real", "momentum_put_marginal", "real_full", "sharpe_delta")
    grid_r = value(comparisons, "real", "grid_put_marginal", "real_full", "ann_return_delta")
    grid_d = value(comparisons, "real", "grid_put_marginal", "real_full", "max_dd_improvement")
    grid_s = value(comparisons, "real", "grid_put_marginal", "real_full", "sharpe_delta")
    text = f"""# IC v1.3 Put 覆盖范围最终正式指标汇总 v3

## Run Metadata

- 状态：正式研究指标汇总完成；未批准实盘。
- 决定：`keep_current_momentum_put_keep_grid_unprotected`。
- 稳定性：动量 `supported_in_observed_real_period`；网格 `reject_return_drag`。
- 数据截止 2026-08-14；Source-change rule：`research_only_no_source_change`。

## Research Question

IC 动量仓与网格仓是否值得配置 Put；交易账本来自 v1/v2 原始 510500 Put 完整重放，本版只纠正到仓库正式绩效口径。

## Implementation Anchor

- 现行核心+动量路径与 fixed-performance v5：收益、CAGR、波动、Sharpe、MaxDD 最大误差均不超过 `{audit['official_metric_parity_max_abs']:.3e}`。
- 动量边际：现行单账本核心+动量 vs 单账本仅核心；网格边际：现行+网格合并目标 vs 现行。

## Data Snapshot

- 混合参考段 2015-04-16—2026-08-14；2022-09-19 前 Put 为理论代理。
- 真实期权段 2022-09-19—2026-08-14，共 945 个用于绩效的日收益；真实 5Y/10Y 为 N/A。

## Cost and Execution Assumptions

- 510500 ETF 约 3 个月、95% Put；T 收盘目标、T+1 收盘执行；每边 1bp。
- 网格期货同日开盘；每 1 倍 IC 30%保证金/缓冲，剩余现金年化 3%；IC 无 Call。

## Runtime Override Plan

- 不再运行新参数；只按 fixed-performance v5 的窗口、样本标准差和日均收益 Sharpe 口径重算。

## Commands

见 `command_log.txt`。

## Output Files

- `scan_summary.csv`、`window_metrics.csv`、`real_option_metrics.csv`、`comparisons.csv`、`annual_metrics.csv`、`integrity_checks.json`。

## Full-Sample Results

- 动量 Put 真实 Full 边际：CAGR `{pct(mom_r)}`，MaxDD 改善 `{pct(mom_d)}`，Sharpe 变化 `{mom_s:+.3f}`。
- 网格 Put 真实 Full 边际：CAGR `{pct(grid_r)}`，MaxDD 改善 `{pct(grid_d)}`，Sharpe 变化 `{grid_s:+.3f}`。

## Window Results

混合参考段，每格 CAGR / MaxDD：

{chr(10).join(mixed_table)}

真实期权段，每格 CAGR / MaxDD / Sharpe：

{chr(10).join(real_table)}

## Stability Classification

- 动量 Put：真实 Full、3Y、1Y 均提高 CAGR 和 Sharpe；真实 Full/3Y 最大回撤与仅核心相同，1Y略改善。它更像择时增益和波动压低，不是 Full 回撤进一步下降的来源。
- 网格 Put：真实 Full 回撤改善约 1pp，但 CAGR 损失约 4pp，Sharpe下降；过去1年网格未持仓，所以完全无差异。收益容忍门槛失败。

## Decision

- `keep_current_momentum_put_keep_grid_unprotected`。
- 不修改 IC v1.3、冻结 V2、Poe、日报或实盘权限。

## User-Facing Summary

IC 已经给动量仓配纯估值 Put，独立重放支持保留；网格仓不应加完整 V2 Put，保护有效但价格太贵。
"""
    (folder / "record.md").write_text(text, encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    inputs = verify()
    before = git_status()
    daily = load_daily()
    mixed, wide, real = build_metrics(daily)
    comparisons = build_comparisons(mixed, real)

    official = pd.read_csv(OFFICIAL_METRICS)
    official = official[official["product"].eq("IC")].copy()
    current = mixed[mixed["candidate"].eq("current_core_momentum_combined")].copy()
    map_segment = {"full": "full", "10y": "last_10y", "5y": "last_5y", "3y": "last_3y", "1y": "last_1y"}
    errors = []
    for row in official.itertuples(index=False):
        trial = current[current["segment"].eq(map_segment[row.window])].iloc[0]
        errors.extend([
            abs(float(trial.ann_return) - float(row.ann_return)),
            abs(float(trial.ann_vol) - float(row.ann_vol)),
            abs(float(trial.sharpe_repo) - float(row.sharpe)),
            abs(float(trial.max_dd) - float(row.max_dd)),
        ])
    parity = max(errors)
    if parity > 1e-12:
        raise RuntimeError(f"Official metric parity failure: {parity}")

    grid_gate = (
        value(comparisons, "real", "grid_put_marginal", "real_full", "max_dd_improvement") >= 0.01
        and value(comparisons, "real", "grid_put_marginal", "real_full", "ann_return_delta") >= -0.01
        and value(comparisons, "real", "grid_put_marginal", "real_3y", "max_dd_improvement") >= -0.01
        and value(comparisons, "mixed", "grid_put_marginal", "full", "max_dd_improvement") > -0.01
        and value(comparisons, "mixed", "grid_put_marginal", "last_5y", "max_dd_improvement") > -0.01
    )
    if grid_gate:
        raise RuntimeError("Unexpected grid gate pass; decision text must be reviewed")

    annual_rows = []
    for (candidate, year), frame in daily.groupby(["candidate", daily["date"].dt.year]):
        annual_rows.append({"candidate": candidate, "year": int(year), **metric(frame, True)})
    annual = pd.DataFrame(annual_rows)
    audit = {
        "official_metric_parity_max_abs": parity,
        "grid_gate_pass": False,
        "decision": "keep_current_momentum_put_keep_grid_unprotected",
        "candidate_rows": {k: int(v) for k, v in daily.groupby("candidate").size().items()},
        "min_cash_weight": float(daily["cash_weight"].min()),
        "max_put_mark_fraction": float(daily["put_mark_fraction"].max()),
        "all_returns_finite": bool(np.isfinite(daily["ret"]).all()),
    }

    STAGING.mkdir(parents=True, exist_ok=False)
    mixed.to_csv(STAGING / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(STAGING / "window_metrics.csv", index=False, encoding="utf-8-sig")
    real.to_csv(STAGING / "real_option_metrics.csv", index=False, encoding="utf-8-sig")
    comparisons.to_csv(STAGING / "comparisons.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(STAGING / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    (STAGING / "integrity_checks.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "data_manifest.json").write_text(json.dumps({"generated_at": datetime.now().astimezone().isoformat(), "inputs": inputs}, ensure_ascii=False, indent=2), encoding="utf-8")
    elapsed = time.perf_counter() - started
    command = f"{Path(sys.executable).name} -X utf8 {Path(__file__).name}"
    (STAGING / "command_log.txt").write_text(f"cwd={ROOT}\n{command}\nelapsed_sec={elapsed:.3f}\n", encoding="utf-8")
    write_record(STAGING, mixed, real, comparisons, audit)
    (STAGING / "output_manifest.json").write_text(json.dumps({p.name: sha256(p) for p in STAGING.iterdir() if p.is_file()}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(STAGING, OUTPUT)

    for name in ("record.md", "scan_summary.csv", "window_metrics.csv", "command_log.txt"):
        shutil.copy2(OUTPUT / name, SCAN / name)
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update({
        "scan_type": "artifact_normalization",
        "baseline": {"candidate": "current_core_momentum_combined"},
        "candidate_grid": list(CANDIDATES),
        "data_snapshot": {"start": "2015-04-16", "end": "2026-08-14", "real_option_start": "2022-09-19"},
        "cost_model": {"put_side_cost": 0.0001, "margin_buffer_per_ic_unit": 0.30, "cash_annual": 0.03},
        "decision": audit["decision"],
        "stability_label": "mixed_momentum_supported_grid_reject",
        "parity_check": {"official_metric_max_abs": parity},
        "warnings": ["2022-09-19前Put为理论代理", "真实5Y和10Y不可用"],
        "git_status_before": before,
        "git_status_after": git_status(),
    })
    (SCAN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "audit": audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
