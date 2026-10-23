#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas"]
# ///
"""Add the audited average IM basis to the frozen v20 model-grid scan."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_overlay_entry_exit_scan_v15 as grid
import im_fixed_valuation_overlay_model2015_high_entry_scan_v20 as v20


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_model2015_avg_basis_scan_v21"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_SIDECAR = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "2136c3e482e7ac6581ec0d7191c14e092c71a5bf54cd9554e44d76bc579dd2b5"
V20_SCRIPT = ROOT / "im_fixed_valuation_overlay_model2015_high_entry_scan_v20.py"
V20_SCRIPT_HASH = "a0fa23758a7d3b881141402250cb5f9571353efee7bf5ac6a2eb886811b4a7c5"
V20_DAILY = ROOT / "outputs" / v20.VERSION / "daily_candidates.csv.gz"
V20_DAILY_HASH = "964789ca374a9b4d2c4639a2fe8967a6fba4c06d51082c8fcf5c395b06e3c574"
V20_SUMMARY = ROOT / "outputs" / v20.VERSION / "scan_summary.csv"
V20_SUMMARY_HASH = "b2048c1a0dc0ac035e1200553fb88079c30041cf600cf1fc1fc4627265a335ae"
BASIS_MANIFEST = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_proxy_v1" / "data_manifest.json"
BASIS_MANIFEST_HASH = "f9ece6cc5cba6fe4ebaf7228656d5e9f6b88756139e1127fcfb90579c8bad9b7"
FORMAL_DAILY = v20.FORMAL_DAILY
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260823_im_model2015_avg_basis_scan_v21"


def verify_inputs() -> tuple[dict[str, str], dict[str, Any]]:
    actual = {
        "spec_sha256": grid.sha256(SPEC),
        "spec_sidecar_sha256": SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower(),
        "v20_script_sha256": grid.sha256(V20_SCRIPT),
        "v20_daily_sha256": grid.sha256(V20_DAILY),
        "v20_summary_sha256": grid.sha256(V20_SUMMARY),
        "basis_manifest_sha256": grid.sha256(BASIS_MANIFEST),
    }
    expected = {
        "spec_sha256": SPEC_HASH,
        "spec_sidecar_sha256": SPEC_HASH,
        "v20_script_sha256": V20_SCRIPT_HASH,
        "v20_daily_sha256": V20_DAILY_HASH,
        "v20_summary_sha256": V20_SUMMARY_HASH,
        "basis_manifest_sha256": BASIS_MANIFEST_HASH,
    }
    failures = {key: [actual[key], value] for key, value in expected.items() if actual[key] != value}
    if failures:
        raise RuntimeError(f"Frozen v21 input mismatch: {failures}")
    basis = json.loads(BASIS_MANIFEST.read_text(encoding="utf-8"))["proxy_assumption"]
    if basis["rows"] != 991 or abs(float(basis["daily_geometric"]) - 0.00038985993765572324) > 1e-15:
        raise RuntimeError(f"Unexpected audited basis assumption: {basis}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal v21 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v21 parameter-scan folder is missing")
    grid.verify_inputs(require_fresh_output=False)
    return actual, basis


def recompose_with_basis(
    core: pd.DataFrame,
    overlay: pd.DataFrame,
    candidate: str,
    daily_basis: float,
) -> pd.DataFrame:
    columns = [
        "date", "tri_close", "base_gross_ret", "futures_cost_rate",
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction", "put_contract",
        "call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction",
        "call_coverage", "call_delta", "call_contract", "call_strike", "call_expiry",
        "threat_roll_count", "threat_entry_blocked",
    ]
    frame = core[columns].rename(columns={"futures_cost_rate": "base_futures_cost_rate"}).merge(
        overlay, on="date", validate="one_to_one"
    )
    zero = [
        "base_futures_cost_rate", "overlay_gross_ret", "overlay_cost_rate",
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction",
        "call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction",
    ]
    frame[zero] = frame[zero].fillna(0.0)
    frame["average_basis_daily"] = daily_basis
    frame["base_basis_ret"] = (1.0 + frame["base_gross_ret"]) * (1.0 + daily_basis) - 1.0 - frame["base_gross_ret"]
    frame["overlay_basis_ret"] = np.where(
        frame["overlay_held_before"].eq(1),
        (1.0 + frame["overlay_gross_ret"]) * (1.0 + daily_basis) - 1.0 - frame["overlay_gross_ret"],
        0.0,
    )
    frame["basis_carry_ret"] = frame["base_basis_ret"] + frame["overlay_basis_ret"]
    frame["futures_cost_rate"] = frame["base_futures_cost_rate"] + frame["overlay_cost_rate"]
    frame["gross_before_cost"] = (
        frame["base_gross_ret"] + frame["overlay_gross_ret"] + frame["basis_carry_ret"]
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
    frame["layer"] = "model_avg_basis_scenario"
    return frame


def metrics_by_window(
    daily: pd.DataFrame,
    cycles: pd.DataFrame,
    no_basis: pd.DataFrame,
) -> pd.DataFrame:
    result = v20.metrics_by_window(daily, cycles)
    prior = no_basis[["candidate", "segment", "ann_return", "max_dd"]].rename(
        columns={"ann_return": "ann_return_no_basis", "max_dd": "max_dd_no_basis"}
    )
    result = result.merge(prior, on=["candidate", "segment"], validate="one_to_one")
    result["ann_return_delta_vs_no_basis"] = result["ann_return"] - result["ann_return_no_basis"]
    result["max_dd_delta_vs_no_basis"] = result["max_dd"] - result["max_dd_no_basis"]
    basis_totals: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        end = pd.Timestamp(group["date"].max())
        full_start = pd.Timestamp(group["date"].min())
        for segment in v20.WINDOWS:
            years = v20.WINDOW_YEARS[segment]
            start = full_start if years is None else max(full_start, end - pd.DateOffset(years=years))
            sample = group[group["date"].ge(start)]
            basis_totals.append({
                "candidate": candidate,
                "segment": segment,
                "basis_carry_sum": float(sample["basis_carry_ret"].sum()),
                "avg_basis_exposure_units": float((1.0 + sample["overlay_held_before"]).mean()),
            })
    return result.merge(pd.DataFrame(basis_totals), on=["candidate", "segment"], validate="one_to_one")


def wide_metrics(long: pd.DataFrame) -> pd.DataFrame:
    wide = v20.wide_metrics(long)
    for metric in (
        "ann_return_no_basis", "max_dd_no_basis", "ann_return_delta_vs_no_basis",
        "max_dd_delta_vs_no_basis", "basis_carry_sum", "avg_basis_exposure_units",
    ):
        pivot = long.pivot(index="candidate", columns="segment", values=metric)
        pivot.columns = [f"{metric}_{segment}" for segment in pivot.columns]
        wide = wide.merge(pivot.reset_index(), on="candidate", validate="one_to_one")
    return wide


def build_record(long: pd.DataFrame, cycles: pd.DataFrame, basis: dict[str, Any], checks: dict[str, Any]) -> str:
    full = long[long["segment"].eq("full")].set_index("candidate")
    cycle_lookup = cycles.set_index("candidate")
    order = [
        "no_grid", "current_L0.85_H1.25",
        *[v20.candidate_label(entry, exit_) for entry, exit_ in v20.candidate_grid()],
    ]
    lines = [
        "# IM固定估值网格2015模型代理平均贴水扫描 v21", "",
        "状态：固定平均贴水情景研究；未批准实盘；冻结主线未修改。", "",
        "## Run Metadata", "",
        "- Run id: `20260823_im_model2015_avg_basis_scan_v21`",
        "- Run date/timezone: 2026-08-23 / Asia/Shanghai",
        "- Scan type: `single_parameter`; source-change rule: `research_only_no_source_change`.", "",
        "## Research Question", "",
        "- 在v20不计贴水路径上，给固定底仓与网格仓加入同一平均贴水后重跑全部候选。",
        "- Baseline: v21 `no_grid`; diagnostic control: same candidate in v20 without basis.", "",
        "## Implementation Anchor", "",
        "- Grid state and option components: frozen v20 model path and v15 state machine.",
        "- Basis source: `im_roll50_momentum50_fullcycle_proxy_v1` post-listing geometric mean.", "",
        "## Data Snapshot", "",
        "- Model sample: 2015-04-16—2026-08-14, 2,756 rows per candidate.",
        f"- Basis calibration: {int(basis['rows'])} post-listing rows; daily {float(basis['daily_geometric']):.12%}; annual {float(basis['annual_geometric']):.6%}.",
        "- The fixed average is applied throughout the model sample and therefore contains look-ahead information.", "",
        "## Cost and Execution Assumptions", "",
        "- T close signal; T+1 modeled open; grid one-way 1bp and roll round trip 2bp.",
        "- 30% buffer per IM unit; residual cash net 3%; Put/Call not scaled with grid.", "",
        "## Runtime Override Plan", "",
        "- No constants were edited; average basis was injected only in this independent harness.",
        f"- v20 unchanged-component parity max abs: `{checks['v20_unchanged_component_max_abs']:.3e}`.",
        f"- v20 no-basis reconstruction parity max abs: `{checks['v20_no_basis_cash_ret_parity_max_abs']:.3e}`.", "",
        "## Commands", "", "```powershell", f"python {VERSION}.py", "```", "",
        "## Output Files", "",
        "- `scan_summary.csv`, `window_metrics.csv`, `daily_candidates.csv.gz`.",
        "- `overlay_trade_audit.csv`, `overlay_cycle_summary.csv`, `integrity_checks.json`.", "",
        "## Full-Sample Results", "",
        "| candidate | CAGR含贴水 | CAGR不含贴水 | 增量 | MaxDD含贴水 | MaxDD不含贴水 | 周期 | 持有比例 | EOD穿透 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for candidate in order:
        row = full.loc[candidate]
        cycle = cycle_lookup.loc[candidate]
        lines.append(
            f"| {candidate} | {row['ann_return']:.2%} | {row['ann_return_no_basis']:.2%} | "
            f"{row['ann_return_delta_vs_no_basis']:+.2%} | {row['max_dd']:.2%} | "
            f"{row['max_dd_no_basis']:.2%} | {int(cycle['completed_cycles'])} | "
            f"{float(cycle['holding_ratio']):.2%} | {int(cycle['eod_capital_breach_rows'])} |"
        )
    lines += [
        "", "## Window Results", "",
        "完整 full/10Y/5Y/3Y/1Y 指标及相对不计贴水差值见 `window_metrics.csv`。", "",
        "## Stability Classification", "",
        "- Label: `data_sensitive`.",
        "- 平均贴水来自上市后样本并被平滑、回填，结果高度依赖该情景。", "",
        "## Decision", "",
        "- Decision: `keep_default`.",
        "- 本结果只作贴水敏感性证据，不修改当前V2参数。", "",
        "## User-Facing Summary", "",
        "平均贴水同时计入固定底仓和网格仓；与v20不计贴水路径同表对照。", "",
    ]
    return "\n".join(lines)


def main() -> None:
    started = datetime.now().astimezone()
    git_before = grid.git_status()
    source_hashes, basis = verify_inputs()
    daily_basis = float(basis["daily_geometric"])

    formal = pd.read_csv(FORMAL_DAILY, parse_dates=["date"], low_memory=False)
    formal = formal[formal["layer"].eq("model")].copy()
    core = formal[formal["candidate"].eq("core_put_call_d10_threat5")].sort_values("date").reset_index(drop=True)
    base, score, percentile = grid.load_sources()
    market, market_checks = grid.build_model_market(base, score, percentile)
    history = score[["date", "unbounded_median_knot"]].copy()
    if not core["date"].equals(market["date"]):
        raise RuntimeError("Formal model components and model market do not align")

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []
    flat, flat_cycle = grid.flat_overlay(market, "model")
    flat["candidate"] = "no_grid"
    flat["family"] = "no_grid"
    flat_cycle.update({"candidate": "no_grid", "family": "no_grid"})
    daily_parts.append(recompose_with_basis(core, flat, "no_grid", daily_basis))
    cycle_rows.append(flat_cycle)

    configurations = [
        (v20.CURRENT_ENTRY, v20.CURRENT_EXIT, "current_L0.85_H1.25", "current_grid"),
        *[(entry, exit_, v20.candidate_label(entry, exit_), "model2015_avg_basis") for entry, exit_ in v20.candidate_grid()],
    ]
    for entry, exit_, candidate, family in configurations:
        overlay, trades, cycle = grid.simulate_overlay(
            market, history, "unbounded_median_knot", entry, exit_, candidate, family, "model"
        )
        daily_parts.append(recompose_with_basis(core, overlay, candidate, daily_basis))
        trade_parts.append(trades)
        cycle_rows.append(cycle)

    order = ["no_grid", "current_L0.85_H1.25", *[v20.candidate_label(*pair) for pair in v20.candidate_grid()]]
    order_map = {candidate: index for index, candidate in enumerate(order)}
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"])
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["candidate", "execution_date"])
    cycles = v20.add_capital_audit(daily, pd.DataFrame(cycle_rows))
    cycles["sort_order"] = cycles["candidate"].map(order_map)
    cycles = cycles.sort_values("sort_order").drop(columns="sort_order")

    no_basis_summary = pd.read_csv(V20_SUMMARY)
    long = metrics_by_window(daily, cycles, no_basis_summary)
    long["sort_order"] = long["candidate"].map(order_map)
    long["segment_order"] = long["segment"].map({value: index for index, value in enumerate(v20.WINDOWS)})
    long = long.sort_values(["sort_order", "segment_order"]).drop(columns=["sort_order", "segment_order"])
    wide = wide_metrics(long)
    wide["sort_order"] = wide["candidate"].map(order_map)
    wide = wide.sort_values("sort_order").drop(columns="sort_order")

    prior_daily = pd.read_csv(V20_DAILY, parse_dates=["date"], low_memory=False)
    unchanged = [
        "base_gross_ret", "overlay_gross_ret", "futures_cost_rate", "put_pnl_ret", "put_cost_rate",
        "call_pnl_ret", "call_cost_rate", "total_im_units", "overlay_held_before", "overlay_held_eod",
        "overlay_buy", "overlay_sell", "cash_weight_raw", "cash_weight",
    ]
    comparison = daily[["candidate", "date", *unchanged]].merge(
        prior_daily[["candidate", "date", *unchanged]], on=["candidate", "date"],
        suffixes=("_v21", "_v20"), validate="one_to_one",
    )
    component_parity = max(
        float((comparison[f"{column}_v21"] - comparison[f"{column}_v20"]).abs().max())
        for column in unchanged
    )
    no_basis_gross = daily["gross_before_cost"] - daily["basis_carry_ret"]
    no_basis_ret = (
        (1.0 + no_basis_gross) * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"]) * (1.0 - daily["call_cost_rate"]) - 1.0
    )
    reconstructed_no_basis_cash = no_basis_ret + daily["cash_interest_ret"]
    prior_cash = daily[["candidate", "date"]].merge(
        prior_daily[["candidate", "date", "cash_ret"]], on=["candidate", "date"], validate="one_to_one"
    )["cash_ret"]
    base_basis_expected = (1.0 + daily["base_gross_ret"]) * (1.0 + daily_basis) - 1.0 - daily["base_gross_ret"]
    overlay_basis_expected = np.where(
        daily["overlay_held_before"].eq(1),
        (1.0 + daily["overlay_gross_ret"]) * (1.0 + daily_basis) - 1.0 - daily["overlay_gross_ret"], 0.0,
    )
    expected_ret = (
        (1.0 + daily["gross_before_cost"]) * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"]) * (1.0 - daily["call_cost_rate"]) - 1.0
    )
    nav_check = daily.groupby("candidate", sort=False)["cash_ret"].transform(lambda x: (1.0 + x).cumprod())
    causal = trades[~trades["execution_reason"].eq("history_carry")]
    checks = {
        **source_hashes,
        "basis_rows": int(basis["rows"]),
        "basis_daily_geometric": daily_basis,
        "basis_annual_geometric": float(basis["annual_geometric"]),
        "v20_unchanged_component_max_abs": component_parity,
        "v20_no_basis_cash_ret_parity_max_abs": float((reconstructed_no_basis_cash.reset_index(drop=True) - prior_cash.reset_index(drop=True)).abs().max()),
        "base_basis_identity_max_abs": float((daily["base_basis_ret"] - base_basis_expected).abs().max()),
        "overlay_basis_identity_max_abs": float((daily["overlay_basis_ret"] - overlay_basis_expected).abs().max()),
        "basis_sum_identity_max_abs": float((daily["basis_carry_ret"] - daily["base_basis_ret"] - daily["overlay_basis_ret"]).abs().max()),
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - daily["ret"] - daily["cash_weight"] * grid.CASH_DAILY).abs().max()),
        "nav_recomposition_max_abs": float((daily["nav"] - nav_check).abs().max()),
        "candidate_count": int(daily["candidate"].nunique()),
        "candidate_set_exact": set(daily["candidate"].unique()) == set(order),
        "rows_per_candidate_min": int(daily.groupby("candidate").size().min()),
        "rows_per_candidate_max": int(daily.groupby("candidate").size().max()),
        "duplicate_candidate_dates": int(daily.duplicated(["candidate", "date"]).sum()),
        "causality_failures": int((pd.to_datetime(causal["execution_date"]) <= pd.to_datetime(causal["signal_date"])).sum()),
        "invalid_total_im_units": int((~daily["total_im_units"].isin([1.0, 2.0])).sum()),
        "invalid_return_rows": int(daily[["ret", "cash_ret", "nav", "drawdown"]].isna().sum().sum() + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()),
        "pending_orders": int(cycles["pending_order_end"].sum()),
        "capital_breach_candidate_count": int(cycles["eod_capital_breach_rows"].gt(0).sum()),
        "morning_breach_candidate_count": int(cycles["morning_capital_breach_rows"].gt(0).sum()),
        "market": market_checks,
    }
    checks["all_checks_passed"] = bool(
        checks["v20_unchanged_component_max_abs"] <= 1e-12
        and checks["v20_no_basis_cash_ret_parity_max_abs"] <= 1e-12
        and checks["base_basis_identity_max_abs"] <= 1e-12
        and checks["overlay_basis_identity_max_abs"] <= 1e-12
        and checks["basis_sum_identity_max_abs"] <= 1e-12
        and checks["return_identity_max_abs"] <= 1e-12
        and checks["cash_identity_max_abs"] <= 1e-12
        and checks["nav_recomposition_max_abs"] <= 1e-12
        and checks["candidate_count"] == 18 and checks["candidate_set_exact"]
        and checks["rows_per_candidate_min"] == checks["rows_per_candidate_max"] == 2756
        and checks["duplicate_candidate_dates"] == checks["causality_failures"] == 0
        and checks["invalid_total_im_units"] == checks["invalid_return_rows"] == checks["pending_orders"] == 0
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v21 integrity checks failed: {checks}")

    record = build_record(long, cycles, basis, checks)
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "overlay_trade_audit.csv", index=False)
    cycles.to_csv(STAGING / "overlay_cycle_summary.csv", index=False)
    long.to_csv(STAGING / "scan_summary.csv", index=False)
    wide.to_csv(STAGING / "window_metrics.csv", index=False)
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "integrity_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    command = f"python {Path(__file__).name}"
    (STAGING / "command_log.txt").write_text(command + "\n", encoding="utf-8")

    long.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update({
        "scan_type": "single_parameter",
        "baseline": {"candidate": "no_grid", "diagnostic_control": "same candidate in v20 without basis"},
        "candidate_grid": [{"candidate": v20.candidate_label(a, b), "entry": a, "exit": b} for a, b in v20.candidate_grid()],
        "data_snapshot": {"source": "frozen v20 model components plus audited post-listing average basis", "start": str(core["date"].min().date()), "end": str(core["date"].max().date()), "rows_per_candidate": len(core), "timezone": "Asia/Shanghai"},
        "cost_model": {"average_basis_daily": daily_basis, "average_basis_annual": float(basis["annual_geometric"]), "basis_applied_to": "fixed core and overlay held-before exposure", "overlay_one_way": grid.ONE_WAY_COST, "overlay_roll_round_trip": 2.0 * grid.ONE_WAY_COST, "margin_buffer_per_im_unit": grid.MARGIN_RATE, "cash_annual_return": 0.03},
        "source_hashes": source_hashes,
        "parity_check": {"v20_unchanged_component_max_abs": component_parity, "v20_no_basis_cash_ret_max_abs": checks["v20_no_basis_cash_ret_parity_max_abs"]},
        "decision": "keep_default",
        "stability_label": "data_sensitive",
        "warnings": ["Average basis is calibrated post-listing and backfilled through 2015", "Fixed basis smooths real basis volatility", "Model Put cap is three, not current V2 four", "Research only; no mainline or live approval"],
        "elapsed_sec": (datetime.now().astimezone() - started).total_seconds(),
        "git_status_after": grid.git_status(),
    })
    (SCAN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    shutil.copy2(SCAN / "scan_meta.json", STAGING / "scan_meta.json")
    manifest = {
        "version": VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH, "script_sha256": grid.sha256(Path(__file__)), "source_hashes": source_hashes,
        "basis_assumption": basis, "sample": [str(core["date"].min().date()), str(core["date"].max().date())],
        "candidate_count": len(order), "integrity": checks, "git_status_before": git_before,
        "git_status_after": grid.git_status(), "research_status": "AVG_BASIS_MODEL_SCENARIO_ONLY_NOT_LIVE_APPROVED",
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    output_hashes = {path.name: grid.sha256(path) for path in sorted(STAGING.iterdir()) if path.is_file()}
    (STAGING / "output_manifest.json").write_text(json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STAGING.replace(OUTPUT)
    print(long[long["segment"].eq("full")][["candidate", "ann_return", "max_dd", "ann_return_no_basis", "max_dd_no_basis", "ann_return_delta_vs_no_basis", "completed_cycles", "holding_day_ratio", "eod_capital_breach_rows"]].to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
