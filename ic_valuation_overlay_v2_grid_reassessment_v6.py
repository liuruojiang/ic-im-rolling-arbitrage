from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import freeze_ic_im_system_mainlines_v2 as frozen_v2
import ic_im_put_max_protection_scan_v1 as put_base
import ic_put_four_tier_mom120_floor_scan_v3 as put_v2
import ic_valuation_overlay_entry_exit_scan_v2 as old_scan
import ic_valuation_overlay_put_sync_v1 as overlay_impl


ROOT = Path(__file__).resolve().parent
VERSION = "ic_valuation_overlay_v2_grid_reassessment_v6"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "f6b569f5654bed8d73665aa9165953bf838c0d12830c90741234d648b057c5c9"
RUN = ROOT / "quant_param_scan_runs" / "20260823_ic_v2_grid_reassessment_v6"
DAILY_DIR = RUN / "daily_outputs"
OFFICIAL_V2 = ROOT / "outputs" / "ic_im_system_mainlines_v2"
OFFICIAL_DAILY = OFFICIAL_V2 / "daily_candidates.csv.gz"
OFFICIAL_MANIFEST = OFFICIAL_V2 / "output_manifest.json"

MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-09-19")
END = pd.Timestamp("2026-08-14")
CURRENT_PAIR = (0.375, 1.000)
ENTRY_THRESHOLDS = tuple(round(0.375 + index * 0.125, 3) for index in range(10))
EXIT_THRESHOLDS = tuple(round(1.000 + index * 0.125, 3) for index in range(9))
MIN_GAP = 0.375
WINDOWS = put_base.WINDOWS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return result.stdout.strip()


def label(low: float, high: float) -> str:
    return f"L{low:.3f}_H{high:.3f}"


def grid() -> list[tuple[float, float]]:
    return [
        (low, high)
        for low in ENTRY_THRESHOLDS
        for high in EXIT_THRESHOLDS
        if high - low >= MIN_GAP - 1e-12
    ]


def verify_preregistration() -> dict[str, Any]:
    actual = sha256(SPEC)
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if actual != SPEC_SHA256 or sidecar != SPEC_SHA256:
        raise RuntimeError(f"Preregistered specification mismatch: {actual} / {sidecar}")
    if len(grid()) != 62 or CURRENT_PAIR not in grid():
        raise RuntimeError("Preregistered grid mismatch")
    if not RUN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {RUN}")
    for output in (RUN / "scan_summary.csv", RUN / "window_metrics.csv"):
        if output.exists():
            raise FileExistsError(f"Formal scan output already exists: {output}")
    manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    mismatches: list[dict[str, str]] = []
    for name, item in manifest.items():
        expected = item["sha256"] if isinstance(item, dict) else item
        path = OFFICIAL_V2 / name
        actual_hash = sha256(path) if path.exists() else "missing"
        if actual_hash != expected:
            mismatches.append({"file": name, "expected": expected, "actual": actual_hash})
    if mismatches:
        raise RuntimeError(f"Official V2 output manifest mismatch: {mismatches}")
    return {"grid_candidates": len(grid()), "official_manifest_files": len(manifest)}


def build_v2_put(
    frames: dict[str, pd.DataFrame], market: pd.DataFrame, layer: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    source = pd.read_csv(
        put_base.IC_SCHEDULE, parse_dates=["eval_date", "execution_date"], low_memory=False
    )
    source = source[
        source["layer"].eq(layer) & source["signal_variant"].eq("l190_mom25")
    ].copy()
    schedule = put_v2.build_schedule(source, frozen_v2.IC_DEFINITION)
    roll_dates = put_base.ic_v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    candidate = f"IC_v2_{layer}_wide4_mom050"
    if layer == "model":
        put, trades = put_base.ic_v20.run_model_delta(
            frames["ic"], schedule, market, candidate, roll_dates
        )
    elif layer == "real":
        put, trades = put_base.ic_v20.run_real_delta(
            frames["ic"], schedule, frames, market, candidate, roll_dates
        )
    else:
        raise ValueError(layer)
    return put, trades, schedule


def metric_values(returns: pd.Series) -> dict[str, float]:
    return overlay_impl.metrics(returns)


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        first = group.iloc[0]
        end = group["date"].max()
        for segment in WINDOWS:
            if segment == "full":
                start = group["date"].min()
            else:
                years = int(segment.removeprefix("last_").removesuffix("y"))
                start = max(group["date"].min(), end - pd.DateOffset(years=years))
            sample = group[group["date"].ge(start)]
            values = metric_values(sample["cash_ret"])
            rows.append(
                {
                    "candidate": candidate,
                    "segment": segment,
                    "start": sample["date"].min(),
                    "end": sample["date"].max(),
                    "rows": len(sample),
                    "ann_return": values["ann_return"],
                    "ann_vol": values["ann_vol"],
                    "sharpe_repo": values["sharpe_repo"],
                    "max_dd": values["max_dd"],
                    "low_threshold": first["low_threshold"],
                    "high_threshold": first["high_threshold"],
                    "avg_total_ic_units": float(sample["total_ic_units"].mean()),
                    "holding_day_ratio": float(sample["overlay_held_eod"].mean()),
                    "futures_cost_total": float(sample["futures_cost_rate"].sum()),
                    "put_cost_total": float(sample["put_cost_rate"].sum()),
                    "min_cash_weight_raw": float(
                        (1.0 - 0.30 * sample["total_ic_units"] - sample["put_mark_fraction"]).min()
                    ),
                }
            )
    long = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in long.groupby("candidate", sort=True):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "low_threshold": first["low_threshold"],
            "high_threshold": first["high_threshold"],
            "avg_total_ic_units_full": first["avg_total_ic_units"],
            "holding_day_ratio_full": first["holding_day_ratio"],
            "futures_cost_total_full": first["futures_cost_total"],
            "put_cost_total_full": first["put_cost_total"],
            "min_cash_weight_raw_full": first["min_cash_weight_raw"],
        }
        for item in group.itertuples(index=False):
            for metric in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def enrich_and_decide(
    wide: pd.DataFrame, cycles: pd.DataFrame
) -> tuple[pd.DataFrame, str, str, str | None]:
    actual = cycles[cycles["sample"].eq("actual_ic_2015")][
        ["candidate", "entries", "exits", "completed_cycles", "holding_days", "holding_ratio", "ending_state"]
    ].rename(
        columns={
            "entries": "actual_entries",
            "exits": "actual_exits",
            "completed_cycles": "actual_completed_cycles",
            "holding_days": "actual_holding_days",
            "holding_ratio": "actual_holding_ratio",
            "ending_state": "actual_ending_state",
        }
    )
    proxy = cycles[cycles["sample"].eq("index_proxy_2007")][
        ["candidate", "completed_cycles"]
    ].rename(columns={"completed_cycles": "index_proxy_completed_cycles"})
    result = wide.merge(actual, on="candidate", how="left").merge(proxy, on="candidate", how="left")
    baseline_name = label(*CURRENT_PAIR)
    baseline = result[result["candidate"].eq(baseline_name)].iloc[0]
    result["ann_return_full_delta_pp"] = (
        result["ann_return_full"] - baseline["ann_return_full"]
    ) * 100.0
    result["max_dd_full_delta_pp"] = (
        result["max_dd_full"] - baseline["max_dd_full"]
    ) * 100.0
    result["ann_return_last_10y_delta_pp"] = (
        result["ann_return_last_10y"] - baseline["ann_return_last_10y"]
    ) * 100.0
    result["base_gate_pass"] = False
    is_grid = result["low_threshold"].notna()
    result.loc[is_grid, "base_gate_pass"] = (
        (result.loc[is_grid, "ann_return_full_delta_pp"] >= 0.75 - 1e-12)
        & (result.loc[is_grid, "max_dd_full_delta_pp"] >= -3.0 - 1e-12)
        & (result.loc[is_grid, "ann_return_last_10y"] >= baseline["ann_return_last_10y"] - 1e-12)
        & ~(
            (result.loc[is_grid, "ann_return_last_5y"] < baseline["ann_return_last_5y"] - 1e-12)
            & (result.loc[is_grid, "ann_return_last_3y"] < baseline["ann_return_last_3y"] - 1e-12)
        )
        & (result.loc[is_grid, "actual_completed_cycles"] >= 3)
        & (result.loc[is_grid, "index_proxy_completed_cycles"] >= 4)
        & (result.loc[is_grid, "min_cash_weight_raw_full"] >= -1e-12)
    )
    result["neighbor_support"] = False
    for idx, row in result[is_grid].iterrows():
        neighbors = result[
            is_grid
            & (
                (
                    np.isclose(result["low_threshold"], row["low_threshold"], atol=1e-12)
                    & np.isclose(
                        (result["high_threshold"] - row["high_threshold"]).abs(), 0.125, atol=1e-12
                    )
                )
                | (
                    np.isclose(result["high_threshold"], row["high_threshold"], atol=1e-12)
                    & np.isclose(
                        (result["low_threshold"] - row["low_threshold"]).abs(), 0.125, atol=1e-12
                    )
                )
            )
        ]
        supported = bool(
            (
                (neighbors["ann_return_full_delta_pp"] >= 0.50 - 1e-12)
                & (neighbors["max_dd_full_delta_pp"] >= -4.0 - 1e-12)
            ).any()
        )
        result.loc[idx, "neighbor_support"] = supported
    result["replacement_gate_pass"] = result["base_gate_pass"] & result["neighbor_support"]
    result["decision_hint"] = np.where(
        result["candidate"].eq(baseline_name),
        "current_baseline",
        np.where(result["replacement_gate_pass"], "watchlist_candidate", "retain_baseline"),
    )
    eligible = result[result["replacement_gate_pass"]].sort_values(
        ["ann_return_full", "max_dd_full"], ascending=[False, False]
    )
    if len(eligible):
        selected = str(eligible.iloc[0]["candidate"])
        decision = "watchlist"
        stability = "wide_stable" if len(eligible) >= 4 else "narrow_stable"
    else:
        selected = None
        decision = "keep_default"
        stability = "reject"
    return result, decision, stability, selected


def main() -> None:
    started = datetime.now().astimezone()
    git_before = git_status()
    prereg = verify_preregistration()
    frames, _valuation, market, market_checks = put_base.ic_v20.v19.v18.load_close_inputs()
    chain, chain_audit = overlay_impl.load_active_chain(frames)
    if chain["date"].min() != MODEL_START or chain["date"].max() != END:
        raise RuntimeError("Unexpected model sample")

    model_put, model_put_trades, model_schedule = build_v2_put(frames, market, "model")
    real_put, real_put_trades, real_schedule = build_v2_put(frames, market, "real")

    current_overlay, _, _ = overlay_impl.simulate_overlay(chain, *CURRENT_PAIR)
    reconstructed_real = overlay_impl.assemble_candidate(
        chain,
        current_overlay,
        real_put,
        "real",
        frozen_v2.IC_SELECTED,
        "core_put_only",
        *CURRENT_PAIR,
    ).sort_values("date")
    official = pd.read_csv(OFFICIAL_DAILY, parse_dates=["date"], low_memory=False)
    official = official[
        official["product"].eq("IC") & official["candidate"].eq(frozen_v2.IC_SELECTED)
    ].sort_values("date")
    parity = float(
        np.max(np.abs(reconstructed_real["cash_ret"].to_numpy() - official["cash_ret"].to_numpy()))
    )
    if parity > 1e-12:
        raise RuntimeError(f"Official IC V2 parity failed: {parity}")

    daily_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []
    actual_trade_parts: list[pd.DataFrame] = []
    flat = overlay_impl.flat_overlay(chain)
    no_grid = overlay_impl.assemble_candidate(
        chain, flat, model_put, "model", "no_grid", "core_put_only", None, None
    )
    no_grid["low_threshold"] = np.nan
    no_grid["high_threshold"] = np.nan
    daily_parts.append(no_grid)
    for low, high in grid():
        candidate = label(low, high)
        overlay, trades, cycle = overlay_impl.simulate_overlay(chain, low, high)
        combined = overlay_impl.assemble_candidate(
            chain, overlay, model_put, "model", candidate, "core_put_only", low, high
        )
        daily_parts.append(combined)
        if len(trades):
            actual_trade_parts.append(trades.assign(sample="actual_ic_2015", candidate=candidate))
        cycle_rows.append({**cycle, "sample": "actual_ic_2015", "candidate": candidate})
    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    )

    scores = pd.read_csv(
        old_scan.SCORE_FILE,
        parse_dates=["date"],
        usecols=["date", "price_close", "unbounded_median_knot"],
    ).sort_values("date")
    proxy_trade_parts: list[pd.DataFrame] = []
    for low, high in grid():
        _proxy_daily, proxy_trades, proxy_cycle = old_scan.simulate_index_proxy(scores, low, high)
        candidate = label(low, high)
        if len(proxy_trades):
            proxy_trade_parts.append(proxy_trades.assign(candidate=candidate))
        cycle_rows.append({**proxy_cycle, "candidate": candidate})
    cycles = pd.DataFrame(cycle_rows)
    actual_trades = pd.concat(actual_trade_parts, ignore_index=True, sort=False)
    proxy_trades = pd.concat(proxy_trade_parts, ignore_index=True, sort=False)

    long, wide = build_metrics(daily)
    wide, decision, stability, selected = enrich_and_decide(wide, cycles)

    current_name = label(*CURRENT_PAIR)
    current_daily = daily[daily["candidate"].eq(current_name)].sort_values("date")
    return_identity = float(
        (
            current_daily["cash_ret"]
            - (current_daily["ret"] + current_daily["cash_weight"] * overlay_impl.CASH_DAILY)
        ).abs().max()
    )
    if return_identity > 1e-12:
        raise RuntimeError(f"Return identity failed: {return_identity}")

    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    model_schedule.to_csv(DAILY_DIR / "model_put_schedule.csv.gz", index=False, compression="gzip")
    real_schedule.to_csv(DAILY_DIR / "real_put_schedule.csv.gz", index=False, compression="gzip")
    model_put_trades.to_csv(DAILY_DIR / "model_put_trades.csv.gz", index=False, compression="gzip")
    real_put_trades.to_csv(DAILY_DIR / "real_put_trades.csv.gz", index=False, compression="gzip")
    actual_trades.to_csv(DAILY_DIR / "actual_grid_trades.csv", index=False)
    proxy_trades.to_csv(DAILY_DIR / "proxy_grid_trades.csv", index=False)
    long.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    cycles.to_csv(RUN / "overlay_cycle_summary.csv", index=False)
    pd.DataFrame(
        [
            {"check": "official_v2_real_cash_ret_max_abs", "value": parity, "tolerance": 1e-12},
            {"check": "cash_return_identity_max_abs", "value": return_identity, "tolerance": 1e-12},
        ]
    ).to_csv(RUN / "parity_checks.csv", index=False)

    git_after = git_status()
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "two_parameter_grid",
            "baseline": {"candidate": current_name, "same_run": True, "no_grid_control": "no_grid"},
            "candidate_grid": [
                {"candidate": label(low, high), "entry_threshold": low, "exit_threshold": high}
                for low, high in grid()
            ],
            "data_snapshot": {
                "formal_model": [str(MODEL_START.date()), str(END.date())],
                "real_put_parity": [str(REAL_START.date()), str(END.date())],
                "ic_futures": "official CFFEX active IC chain; realized basis and roll effects included",
                "put": "model 510500 Put before listing; official real 510500 Put parity from 2022-09-19",
                "valuation": str(old_scan.SCORE_FILE.relative_to(ROOT)),
                "market_checks": market_checks,
                "chain_audit": chain_audit,
            },
            "cost_model": {
                "future_one_way": overlay_impl.ONE_WAY_COST,
                "held_grid_roll_round_trip": 2 * overlay_impl.ONE_WAY_COST,
                "margin_buffer_per_ic_unit": overlay_impl.MARGIN_RATE,
                "cash_annual": 0.03,
                "put": "official inherited model and real V2 paths",
                "basis": "actual IC futures prices; no average-basis add-on",
            },
            "execution": "valuation signal at T close; grid execution at next IC session open; Put official T close/T+1 common close",
            "parity_check": {"official_v2_real_cash_ret_max_abs": parity, "tolerance": 1e-12},
            "preregistration": prereg,
            "decision": decision,
            "stability_label": stability,
            "selected_watchlist_candidate": selected,
            "git_status_before": git_before,
            "git_status_after": git_after,
            "outputs": {
                "record": str((RUN / "record.md").resolve()),
                "scan_summary": str((RUN / "scan_summary.csv").resolve()),
                "window_metrics": str((RUN / "window_metrics.csv").resolve()),
                "scan_meta": str(meta_path.resolve()),
                "command_log": str((RUN / "command_log.txt").resolve()),
            },
            "warnings": [
                "2015-04-16 to 2022-09-16 Put history is theoretical model output",
                "2007 index proxy excludes futures basis and Put and is cycle-count evidence only",
                "bid-ask spread, close impact, capacity, non-fill, margin hikes and tax excluded",
                "research only; frozen V2 mainline unchanged",
            ],
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): SPEC_SHA256,
                str(OFFICIAL_MANIFEST.relative_to(ROOT)): sha256(OFFICIAL_MANIFEST),
                str(put_base.IC_SCHEDULE.relative_to(ROOT)): sha256(put_base.IC_SCHEDULE),
                str(overlay_impl.IC_RAW.relative_to(ROOT)): sha256(overlay_impl.IC_RAW),
                str(old_scan.SCORE_FILE.relative_to(ROOT)): sha256(old_scan.SCORE_FILE),
            },
            "elapsed_sec": (datetime.now().astimezone() - started).total_seconds(),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    current = wide[wide["candidate"].eq(current_name)].iloc[0]
    no_grid_row = wide[wide["candidate"].eq("no_grid")].iloc[0]
    if selected:
        chosen = wide[wide["candidate"].eq(selected)].iloc[0]
        chosen_line = (
            f"- Watchlist: `{selected}`，full CAGR {chosen['ann_return_full']:.2%}，"
            f"MaxDD {chosen['max_dd_full']:.2%}，完成周期 {int(chosen['actual_completed_cycles'])}。"
        )
    else:
        chosen_line = "- 没有候选通过预注册替代门槛。"
    top = wide[wide["low_threshold"].notna()].sort_values("ann_return_full", ascending=False).head(10)
    record = f"""# IC V2 估值网格宽域复评 v6

## Run Metadata

- Run id: `20260823_ic_v2_grid_reassessment_v6`
- Run timestamp: {started.isoformat()}
- Workspace: `{ROOT}`
- Entrypoint: `{Path(__file__).name}`
- Scan type: `two_parameter_grid`
- Source-change rule: `research_only_no_source_change`

## Research Question

- Baseline: `{current_name}`，进入0.375、退出1.000。
- Candidate grid: 62组，进入0.375至1.500、退出1.000至2.000、步长0.125、最小滞回0.375。
- Control: `no_grid`。
- Decision threshold: 预注册门槛见冻结 spec；本轮即使通过也只进入 watchlist。

## Implementation Anchor

- 真实 IC 活跃链：`{overlay_impl.IC_RAW.relative_to(ROOT)}`；`load_active_chain -> simulate_overlay -> assemble_candidate`。
- V2 Put：`IC_wide4_mom050`，四档1.90/1.95/2.00/2.05，MOM120<0最低50%。
- IC不含Call；网格层不加Put。
- 当前 V2 真实样本逐日收益复现最大误差：{parity:.3e}。

## Data Snapshot

- 正式绩效：2015-04-16至2026-08-14，真实IC期货链，历史贴水与换月损益自然包含。
- Put：2015-04-16至2022-09-16为理论模型段；2022-09-19起以真实510500 Put校验。
- 2007指数代理只统计周期，不用于正式绩效。

## Cost and Execution Assumptions

- IC/网格单边1bp；持有网格换月双边2bp；每1倍期货30%保证金/缓冲；现金年化3%。
- 网格T收盘信号、下一IC交易日开盘执行；Put沿用官方T收盘/T+1共同收盘路径。
- 未计点差、冲击、容量、无法成交、动态保证金上调和税费。

## Full-Sample Results

- No-grid: CAGR {no_grid_row['ann_return_full']:.2%}，MaxDD {no_grid_row['max_dd_full']:.2%}。
- Current `{current_name}`: CAGR {current['ann_return_full']:.2%}，MaxDD {current['max_dd_full']:.2%}，完成周期 {int(current['actual_completed_cycles'])}。
{chosen_line}

```text
{top[['candidate','ann_return_full','max_dd_full','sharpe_repo_full','actual_completed_cycles','index_proxy_completed_cycles','replacement_gate_pass']].to_string(index=False)}
```

## Window Results

完整结果见 `scan_summary.csv` 与 `window_metrics.csv`；包含 full/10Y/5Y/3Y/1Y。

## Stability Classification

- Stability: `{stability}`。
- Decision: `{decision}`。
- Selected watchlist candidate: `{selected}`。

## Decision

- 冻结 V2 主线未修改；如需改变默认阈值，须用户另行批准。
- Working tree已有大量与本研究无关的未跟踪文件，本研究未改动它们。

## User-Facing Summary

以同一当前V2 Put重跑2015年以来真实IC期货链；实际贴水已经包含，上市前Put为理论模型。结果以上述Decision为准。
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -m pytest -q test_ic_valuation_overlay_v2_grid_reassessment_v6.py\n")
        handle.write("python ic_valuation_overlay_v2_grid_reassessment_v6.py\n")
    print(wide.sort_values("ann_return_full", ascending=False).head(15).to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability, "selected": selected, "parity": parity}, ensure_ascii=False))


if __name__ == "__main__":
    main()
