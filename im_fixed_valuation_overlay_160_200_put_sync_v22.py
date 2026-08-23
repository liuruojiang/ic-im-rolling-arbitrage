#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas", "requests", "tabulate"]
# ///
"""Compare core-only versus synchronized V2 Put on the 1.60/2.00 IM grid."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_overlay_entry_exit_scan_v15 as grid
import im_fixed_valuation_overlay_selected_put_sync_v18 as v18
import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_160_200_put_sync_v22"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_SIDECAR = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "7be50b669ad955cb2058055236067ca8048e891b8ff3e1da05b8e50132b659da"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260823_im_grid160_put_sync_v22"

MODEL_SCHEDULE = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v1" / "theoretical_put_schedule.csv.gz"
V2_FULL_DAILY = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v2" / "daily_nav.csv.gz"
REAL_SCHEDULE = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "target_schedules.csv.gz"
V2_MAINLINE_DAILY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
V20_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_model2015_high_entry_scan_v20" / "daily_candidates.csv.gz"
V21_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_model2015_avg_basis_scan_v21" / "daily_candidates.csv.gz"
V19_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_high_entry_diagonal_scan_v19" / "daily_candidates.csv.gz"
BASIS_MANIFEST = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_proxy_v1" / "data_manifest.json"

PINNED = {
    MODEL_SCHEDULE: "9b565d7ddc2976a652946da788899275e4f26b66e4bf6577ed0c66b635e1c628",
    V2_FULL_DAILY: "670e21d6e8350b64aea9e729a9ca49ea30c19ab901e2d771358e3f67dc84b4a4",
    REAL_SCHEDULE: "4a7612d230882e7061da2b61d1016d9480ea11de7c4acf945980aa46a8b1d501",
    V2_MAINLINE_DAILY: "6cbf2a441515087ac6d6ce98b03de8c54d87334e8288e0b0b9e8720155c8da35",
    V20_DAILY: "964789ca374a9b4d2c4639a2fe8967a6fba4c06d51082c8fcf5c395b06e3c574",
    V21_DAILY: "847326e3b28d62d676e3ce1d755538df8021535d9840cc8ca94e029ad37dd38c",
    V19_DAILY: "c4f8be8f77ae71d1beff9b860fcfa10e50da145c46b1fbc1569d110be0f53889",
    BASIS_MANIFEST: "f9ece6cc5cba6fe4ebaf7228656d5e9f6b88756139e1127fcfb90579c8bad9b7",
    ROOT / "im_mo_close_execution_v8.py": "4ac38a47dac471bcaea77e817f6d74a5fe8ccb65484aa79a4844c80b2226eace",
    ROOT / "im_mo_csi1000_put_protection_battery_v6.py": "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d",
    ROOT / "im_valuation_frequency_tenor_scan_v4.py": "c654aa7c30c4a89954f8c7db7d352664ab3ac0c5455c2b26248c5aca75476461",
}

ENTRY = 1.60
EXIT = 2.00
SOURCE_GRID = "grid_L1.60_H2.00"
SCENARIOS = ("model_no_basis", "model_avg_basis", "real_actual_basis")
VARIANTS = ("no_grid_core_put", "grid_core_put", "grid_sync_put")
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
WINDOW_YEARS = {"full": None, "last_10y": 10, "last_5y": 5, "last_3y": 3, "last_1y": 1}


def verify_inputs() -> tuple[dict[str, str], dict[str, Any]]:
    actual = {str(path.relative_to(ROOT)): grid.sha256(path) for path in PINNED}
    failures = {
        str(path.relative_to(ROOT)): [actual[str(path.relative_to(ROOT))], expected]
        for path, expected in PINNED.items()
        if actual[str(path.relative_to(ROOT))] != expected
    }
    if grid.sha256(SPEC) != SPEC_HASH or SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower() != SPEC_HASH:
        failures["spec"] = [grid.sha256(SPEC), SPEC_HASH]
    if failures:
        raise RuntimeError(f"Frozen v22 input mismatch: {failures}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal v22 output or staging exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v22 scan folder is missing")
    basis = json.loads(BASIS_MANIFEST.read_text(encoding="utf-8"))["proxy_assumption"]
    if int(basis["rows"]) != 991 or abs(float(basis["daily_geometric"]) - 0.00038985993765572324) > 1e-15:
        raise RuntimeError(f"Unexpected basis assumption: {basis}")
    return actual, basis


def candidate(scenario: str, variant: str) -> str:
    return f"{scenario}__{variant}"


def load_source(path: Path, name: str) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"], low_memory=False)
    result = frame[frame["candidate"].eq(name)].sort_values("date").reset_index(drop=True)
    if result.empty:
        raise RuntimeError(f"Missing source candidate {name} in {path}")
    return result


def scale_schedule(schedule: pd.DataFrame, path: pd.DataFrame, label: str, layer: str) -> pd.DataFrame:
    units = path[["date", "total_im_units"]].rename(columns={"date": "execution_date"})
    result = schedule.merge(units, on="execution_date", validate="one_to_one")
    result["base_target_qty"] = result["binary_target_qty"].astype(int)
    result["binary_target_qty"] = (
        result["base_target_qty"] * result["total_im_units"].round().astype(int)
    ).astype(int)
    result["three_tier_target_qty"] = result["binary_target_qty"]
    result["candidate"] = label
    result["schedule_candidate"] = label
    result["layer"] = layer
    result["put_mode"] = "synchronized_v2_put"
    return result


def put_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction", "put_contract"]].copy()


def compose(
    source: pd.DataFrame,
    put: pd.DataFrame,
    scenario: str,
    variant: str,
    daily_basis: float,
) -> pd.DataFrame:
    replacement = put_columns(put).rename(columns={
        "put_pnl_ret": "new_put_pnl_ret",
        "put_cost_rate": "new_put_cost_rate",
        "put_mark_fraction": "new_put_mark_fraction",
        "put_fraction": "new_put_fraction",
        "put_contract": "new_put_contract",
    })
    frame = source.merge(replacement, on="date", validate="one_to_one")
    for old, new in (
        ("put_pnl_ret", "new_put_pnl_ret"), ("put_cost_rate", "new_put_cost_rate"),
        ("put_mark_fraction", "new_put_mark_fraction"), ("put_fraction", "new_put_fraction"),
        ("put_contract", "new_put_contract"),
    ):
        frame[old] = frame[new]
    zero = [
        "base_gross_ret", "overlay_gross_ret", "base_futures_cost_rate", "overlay_cost_rate",
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction",
        "call_pnl_ret", "call_cost_rate", "call_margin_fraction",
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
    frame["put_qty"] = 2.0 * frame["put_fraction"]
    frame["scenario"] = scenario
    frame["variant"] = variant
    frame["candidate"] = candidate(scenario, variant)
    frame["put_mode"] = "synchronized_v2_put" if variant == "grid_sync_put" else "core_v2_put_only"
    frame["layer"] = "real" if scenario == "real_actual_basis" else "model"
    return frame.drop(columns=[column for column in frame.columns if column.startswith("new_put_")])


def build_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        start_available = pd.Timestamp(group["date"].min())
        end = pd.Timestamp(group["date"].max())
        first = group.iloc[0]
        for segment in WINDOWS:
            years = WINDOW_YEARS[segment]
            requested = start_available if years is None else end - pd.DateOffset(years=years)
            start = max(start_available, requested)
            sample = group[group["date"].ge(start)]
            metric = grid.metrics(sample["cash_ret"])
            rows.append({
                "candidate": label, "scenario": first["scenario"], "variant": first["variant"],
                "segment": segment, "start": sample["date"].min(), "end": end, "rows": len(sample),
                "coverage_complete": bool(years is None or start_available <= requested + pd.Timedelta(days=7)),
                "ann_return": metric["ann_return"], "ann_vol": metric["ann_vol"],
                "sharpe_repo": metric["sharpe_repo"], "max_dd": metric["max_dd"],
                "holding_days": int(sample["overlay_held_eod"].sum()),
                "holding_day_ratio": float(sample["overlay_held_eod"].mean()),
                "put_cost_total": float(sample["put_cost_rate"].sum()),
                "basis_carry_sum": float(sample["basis_carry_ret"].sum()),
            })
    result = pd.DataFrame(rows)
    baselines = result[result["variant"].eq("grid_core_put")].set_index(["scenario", "segment"])
    result["ann_return_delta_vs_grid_core"] = [
        row.ann_return - float(baselines.loc[(row.scenario, row.segment), "ann_return"])
        for row in result.itertuples(index=False)
    ]
    result["max_dd_delta_vs_grid_core"] = [
        row.max_dd - float(baselines.loc[(row.scenario, row.segment), "max_dd"])
        for row in result.itertuples(index=False)
    ]
    return result


def wide_metrics(long: pd.DataFrame) -> pd.DataFrame:
    first = long.groupby("candidate", sort=False)[["scenario", "variant", "holding_days", "holding_day_ratio"]].first()
    parts = [first]
    for metric in (
        "ann_return", "max_dd", "ann_vol", "sharpe_repo", "ann_return_delta_vs_grid_core",
        "max_dd_delta_vs_grid_core", "put_cost_total", "basis_carry_sum",
    ):
        pivot = long.pivot(index="candidate", columns="segment", values=metric)
        pivot.columns = [f"{metric}_{segment}" for segment in pivot.columns]
        parts.append(pivot)
    return pd.concat(parts, axis=1).reset_index()


def cycle_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        source = daily[daily["candidate"].eq(candidate(scenario, "grid_core_put"))].sort_values("date")
        buys = source[source["overlay_buy"].eq(1)]["date"].tolist()
        sells = source[source["overlay_sell"].eq(1)]["date"].tolist()
        if len(buys) != len(sells):
            raise RuntimeError(f"Incomplete cycles for {scenario}: {len(buys)} / {len(sells)}")
        for cycle_id, (start, end) in enumerate(zip(buys, sells), start=1):
            for variant in ("grid_core_put", "grid_sync_put"):
                sample = daily[
                    daily["candidate"].eq(candidate(scenario, variant))
                    & daily["date"].between(start, end)
                ].sort_values("date")
                metric = grid.metrics(sample["cash_ret"])
                rows.append({
                    "scenario": scenario, "variant": variant, "cycle_id": cycle_id,
                    "entry_date": start, "exit_date": end, "rows": len(sample),
                    "ann_return": metric["ann_return"], "max_dd": metric["max_dd"],
                    "put_pnl_sum": float(sample["put_pnl_ret"].sum()),
                    "put_cost_sum": float(sample["put_cost_rate"].sum()),
                    "max_put_qty": float(sample["put_qty"].max()),
                    "max_put_mark_fraction": float(sample["put_mark_fraction"].max()),
                })
    return pd.DataFrame(rows)


def capital_audit(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    trade_keys = set(
        zip(trades["scenario"], trades["variant"], pd.to_datetime(trades["actual_execution_date"]))
    )
    rows: list[dict[str, Any]] = []
    for label, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date").copy()
        scenario, variant = str(group.iloc[0]["scenario"]), str(group.iloc[0]["variant"])
        prior_put = group["put_mark_fraction"].shift(1).fillna(group["put_mark_fraction"])
        prior_call = group["call_margin_fraction"].shift(1).fillna(group["call_margin_fraction"])
        morning_required = np.where(
            group["overlay_buy"].eq(1), 2.0 * grid.MARGIN_RATE + prior_put + prior_call, np.nan
        )
        put_trade_day = group["date"].map(lambda day: (scenario, variant, pd.Timestamp(day)) in trade_keys)
        rows.append({
            "candidate": label, "scenario": scenario, "variant": variant,
            "eod_capital_breach_rows": int(group["cash_weight_raw"].lt(-1e-12).sum()),
            "min_cash_weight_raw": float(group["cash_weight_raw"].min()),
            "morning_capital_breach_rows": int((pd.Series(morning_required).dropna() > 1.0 + 1e-12).sum()),
            "max_morning_capital_required": float(pd.Series(morning_required).dropna().max()) if group["overlay_buy"].any() else np.nan,
            "put_execution_breach_rows": int((put_trade_day & group["cash_weight_raw"].lt(-1e-12)).sum()),
            "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
            "max_put_qty": float(group["put_qty"].max()),
        })
    return pd.DataFrame(rows)


def decision_table(metrics: pd.DataFrame, cycles: pd.DataFrame, capital: pd.DataFrame, price_ok: bool) -> tuple[pd.DataFrame, dict[str, Any]]:
    m = metrics.set_index(["scenario", "variant", "segment"])
    c = capital.set_index(["scenario", "variant"])
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        headline = max(
            float(m.loc[(scenario, "grid_sync_put", window), "max_dd"] - m.loc[(scenario, "grid_core_put", window), "max_dd"])
            for window in ("full", "last_3y")
        )
        subset = cycles[cycles["scenario"].eq(scenario)]
        worst = subset.groupby("variant")["max_dd"].min()
        local = float(worst["grid_sync_put"] - worst["grid_core_put"])
        windows = ("full", "last_5y", "last_3y") if scenario.startswith("model") else ("full", "last_3y")
        max_loss = max(
            float(m.loc[(scenario, "grid_core_put", window), "ann_return"] - m.loc[(scenario, "grid_sync_put", window), "ann_return"])
            for window in windows
        )
        cap = c.loc[(scenario, "grid_sync_put")]
        rows.append({
            "scenario": scenario, "headline_dd_improvement": headline,
            "worst_cycle_dd_improvement": local, "max_cagr_loss": max_loss,
            "eod_capital_breach_rows": int(cap["eod_capital_breach_rows"]),
            "put_execution_breach_rows": int(cap["put_execution_breach_rows"]),
            "headline_gate": headline >= 0.01 - 1e-12,
            "local_gate": local >= 0.02 - 1e-12,
            "return_gate": max_loss <= 0.015 + 1e-12,
            "capital_gate": int(cap["put_execution_breach_rows"]) == 0 and int(cap["eod_capital_breach_rows"]) == 0,
            "liquidity_gate": bool(price_ok if scenario == "real_actual_basis" else True),
        })
    table = pd.DataFrame(rows)
    gates = ["headline_gate", "local_gate", "return_gate", "capital_gate", "liquidity_gate"]
    table["put_sync_pass"] = table[gates].all(axis=1)
    overall = bool(table["put_sync_pass"].all())
    return table, {
        "decision": "grid_sync_put_supported" if overall else "retain_grid_core_put_only",
        "stability_label": "selected_bundle_pass" if overall else "reject",
        "passed_scenarios": int(table["put_sync_pass"].sum()), "tested_scenarios": len(table),
        "research_only": True,
    }


def build_record(metrics: pd.DataFrame, decisions: pd.DataFrame, capital: pd.DataFrame, summary: dict[str, Any], checks: dict[str, Any], basis: dict[str, Any]) -> str:
    focus = metrics[metrics["segment"].isin(["full", "last_5y", "last_3y", "last_1y"])][
        ["scenario", "variant", "segment", "ann_return", "max_dd", "coverage_complete"]
    ]
    return "\n".join([
        "# IM 1.60/2.00 网格同步 V2 Put 对照 v22", "",
        "状态：研究完成；未批准实盘；旧V2冻结主线未修改。", "",
        "## Run Metadata", "",
        "- Run id: `20260823_im_grid160_put_sync_v22`; timezone: Asia/Shanghai.",
        "- Scan type: candidate bundle; source-change rule: research_only_no_source_change.", "",
        "## Research Question", "",
        "- 固定1.60进入/2.00退出，比较网格不加Put与同步V2 Put至最高8张。",
        "- 同批保留无网格核心Put基准；Call只覆盖固定核心。", "",
        "## Implementation Anchor", "",
        "- Model V2 Put: full-cycle theoretical schedule and v8 close-execution engine.",
        "- Real V2 Put: current mainline target schedule, official CFFEX MO close/settlement.",
        "- Grid states: frozen v20 model and v19 real 1.60/2.00 candidates.", "",
        "## Data Snapshot", "",
        "- Model: 2015-04-16—2026-08-14; Real: 2022-07-22—2026-08-14.",
        f"- Average basis scenario: daily {float(basis['daily_geometric']):.12%}, annual {float(basis['annual_geometric']):.6%}; look-ahead proxy.", "",
        "## Cost and Execution Assumptions", "",
        "- Grid T+1 open, 1bp one-way and 2bp roll; Put T+1 close with frozen costs.",
        "- 30% buffer/IM unit; Put mark and Call margin deducted; only positive cash earns net 3%.", "",
        "## Runtime Override Plan", "",
        f"- Model/real base V2 Put parity max abs: {checks['model_base_put_parity_max_abs']:.3e} / {checks['real_base_put_parity_max_abs']:.3e}.",
        "- No frozen constants were edited; synchronized quantities were supplied through independent schedules.", "",
        "## Commands", "", "```powershell", f"python {VERSION}.py", "```", "",
        "## Output Files", "",
        "- `scan_summary.csv`, `window_metrics.csv`, `daily_candidates.csv.gz`.",
        "- `scenario_decisions.csv`, `cycle_metrics.csv`, `capital_audit.csv`, Put schedules/trades and integrity files.", "",
        "## Full-Sample Results", "", focus.to_markdown(index=False, floatfmt=".6f"), "",
        "## Window Results", "", "完整窗口见 `window_metrics.csv`；真实10Y/5Y覆盖不足已标记。", "",
        "## Stability Classification", "",
        f"- Stability: `{summary['stability_label']}`; scenarios passed {summary['passed_scenarios']} / {summary['tested_scenarios']}.",
        decisions.to_markdown(index=False, floatfmt=".6f"), "",
        "## Decision", "", f"- Decision: `{summary['decision']}`.",
        "- 不自动修改主线；若同步Put未通过，继续只保护固定核心IM。", "",
        "## User-Facing Summary", "",
        "本版直接回答1.60/2.00网格加Put是否改善回撤，并完整保留收益损失和资金穿透。", "",
        "## Capital Audit", "", capital.to_markdown(index=False, floatfmt=".6f"), "",
    ])


def main() -> None:
    started = datetime.now().astimezone()
    git_before = grid.git_status()
    source_hashes, basis = verify_inputs()
    basis_daily = float(basis["daily_geometric"])

    model_schedule = pd.read_csv(MODEL_SCHEDULE, parse_dates=["eval_date", "execution_date"], low_memory=False)
    real_schedule = pd.read_csv(REAL_SCHEDULE, parse_dates=["eval_date", "execution_date"], low_memory=False)
    real_schedule = real_schedule[real_schedule["product"].eq("IM")].copy()
    model_market, model_market_checks = v6.model_market()
    model_base_put, model_base_trades, _ = v8.run_model_normal_close(model_market, model_schedule, "3m", 0.95, "v22_model_base")

    upstream, _, _, _, _, raw_options = v4.load_inputs()
    active_im = v8.active_im_closes(upstream)
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    real_base_put, real_base_trades, _ = v8.run_real_normal_close(upstream, options, active_im, real_schedule, "3m", 0.95, "v22_real_base")

    model_no_grid = load_source(V20_DAILY, "no_grid")
    model_grid = load_source(V20_DAILY, SOURCE_GRID)
    real_no_grid = load_source(V19_DAILY, "no_grid")
    real_grid = load_source(V19_DAILY, SOURCE_GRID)
    model_sync_schedule = scale_schedule(model_schedule, model_grid, "v22_model_grid_sync", "model")
    real_sync_schedule = scale_schedule(real_schedule, real_grid, "v22_real_grid_sync", "real")
    model_sync_put, model_sync_trades, model_lives = v8.run_model_normal_close(model_market, model_sync_schedule, "3m", 0.95, "v22_model_grid_sync")
    real_sync_put, real_sync_trades, real_lives = v8.run_real_normal_close(upstream, options, active_im, real_sync_schedule, "3m", 0.95, "v22_real_grid_sync")

    daily_parts = [
        compose(model_no_grid, model_base_put, "model_no_basis", "no_grid_core_put", 0.0),
        compose(model_grid, model_base_put, "model_no_basis", "grid_core_put", 0.0),
        compose(model_grid, model_sync_put, "model_no_basis", "grid_sync_put", 0.0),
        compose(model_no_grid, model_base_put, "model_avg_basis", "no_grid_core_put", basis_daily),
        compose(model_grid, model_base_put, "model_avg_basis", "grid_core_put", basis_daily),
        compose(model_grid, model_sync_put, "model_avg_basis", "grid_sync_put", basis_daily),
        compose(real_no_grid, real_base_put, "real_actual_basis", "no_grid_core_put", 0.0),
        compose(real_grid, real_base_put, "real_actual_basis", "grid_core_put", 0.0),
        compose(real_grid, real_sync_put, "real_actual_basis", "grid_sync_put", 0.0),
    ]
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["scenario", "variant", "date"])

    trades_parts = []
    for frame, scenario, variant in (
        (model_sync_trades, "model_no_basis", "grid_sync_put"),
        (model_sync_trades, "model_avg_basis", "grid_sync_put"),
        (real_sync_trades, "real_actual_basis", "grid_sync_put"),
    ):
        trades_parts.append(frame.assign(scenario=scenario, variant=variant))
    trades = pd.concat(trades_parts, ignore_index=True, sort=False)
    metrics = build_metrics(daily)
    wide = wide_metrics(metrics)
    cycles = cycle_metrics(daily)
    capital = capital_audit(daily, trades)
    price_audit, price_stats = v18.generic_price_integrity(
        real_sync_trades.assign(layer="real"), raw_options
    )
    decisions, summary = decision_table(metrics, cycles, capital, price_stats["trade_legs"] > 0 and all(
        price_stats[key] == 0 for key in ("nonpositive_close_rows", "nonpositive_volume_rows", "new_leg_nonpositive_oi_rows")
    ))

    v2_full = pd.read_csv(V2_FULL_DAILY, parse_dates=["date"], low_memory=False)
    v2_real = pd.read_csv(V2_MAINLINE_DAILY, parse_dates=["date"], low_memory=False)
    v2_real = v2_real[v2_real["product"].eq("IM")].copy()
    parity_cols = ["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]
    def parity(left: pd.DataFrame, right: pd.DataFrame) -> float:
        joined = left[["date", *parity_cols]].merge(right[["date", *parity_cols]], on="date", suffixes=("_l", "_r"), validate="one_to_one")
        return max(float((joined[f"{col}_l"] - joined[f"{col}_r"]).abs().max()) for col in parity_cols)

    theoretical_reference = v2_full[v2_full["put_source"].eq("theoretical_csi1000_put")].copy()
    model_component_cols = ["put_pnl_ret", "put_mark_fraction", "put_fraction"]
    model_component_join = model_base_put[["date", *model_component_cols]].merge(
        theoretical_reference[["date", *model_component_cols]], on="date",
        suffixes=("_generated", "_frozen"), validate="one_to_one",
    )
    model_component_parity = max(
        float((model_component_join[f"{col}_generated"] - model_component_join[f"{col}_frozen"]).abs().max())
        for col in model_component_cols
    )
    # The frozen hybrid source closes its theoretical Put on 2022-07-21 before
    # switching to real MO.  The pure model scenario intentionally continues
    # the theoretical contract, so audit cost parity before that cutover day.
    cutover_date = pd.Timestamp(theoretical_reference["date"].max())
    model_cost_join = model_base_put[model_base_put["date"].lt(cutover_date)][["date", "put_cost_rate"]].merge(
        theoretical_reference[theoretical_reference["date"].lt(cutover_date)][["date", "put_cost_rate"]],
        on="date", suffixes=("_generated", "_frozen"), validate="one_to_one",
    )
    model_cost_parity = float((model_cost_join["put_cost_rate_generated"] - model_cost_join["put_cost_rate_frozen"]).abs().max())
    model_cutover_cost_difference = float(
        abs(
            model_base_put.loc[model_base_put["date"].eq(cutover_date), "put_cost_rate"].iloc[0]
            - theoretical_reference.loc[theoretical_reference["date"].eq(cutover_date), "put_cost_rate"].iloc[0]
        )
    )
    model_parity = max(model_component_parity, model_cost_parity)
    real_parity = parity(real_base_put, v2_real)
    expected_ret = (
        (1.0 + daily["gross_before_cost"]) * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"]) * (1.0 - daily["call_cost_rate"]) - 1.0
    )
    expected_cash = daily["ret"] + daily["cash_weight"] * grid.CASH_DAILY
    nav_check = daily.groupby("candidate", sort=False)["cash_ret"].transform(lambda x: (1.0 + x).cumprod())
    target_error = int((model_sync_schedule["binary_target_qty"] != model_sync_schedule["base_target_qty"] * model_sync_schedule["total_im_units"].astype(int)).sum() + (real_sync_schedule["binary_target_qty"] != real_sync_schedule["base_target_qty"] * real_sync_schedule["total_im_units"].astype(int)).sum())
    checks = {
        "source_hashes": source_hashes, "spec_sha256": SPEC_HASH,
        "model_base_put_parity_max_abs": model_parity,
        "model_prelisting_component_parity_max_abs": model_component_parity,
        "model_prelisting_cost_parity_ex_cutover_max_abs": model_cost_parity,
        "model_theoretical_to_real_cutover_cost_difference": model_cutover_cost_difference,
        "real_base_put_parity_max_abs": real_parity,
        "candidate_count": int(daily["candidate"].nunique()),
        "duplicate_candidate_dates": int(daily.duplicated(["candidate", "date"]).sum()),
        "model_rows_per_candidate": sorted(daily[daily["scenario"].str.startswith("model")].groupby("candidate").size().unique().tolist()),
        "real_rows_per_candidate": sorted(daily[daily["scenario"].eq("real_actual_basis")].groupby("candidate").size().unique().tolist()),
        "sync_target_formula_errors": target_error,
        "max_sync_target_qty": int(max(model_sync_schedule["binary_target_qty"].max(), real_sync_schedule["binary_target_qty"].max())),
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - expected_cash).abs().max()),
        "nav_recomposition_max_abs": float((daily["nav"] - nav_check).abs().max()),
        "invalid_return_rows": int(daily[["ret", "cash_ret", "nav", "drawdown"]].isna().sum().sum() + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()),
        "real_price_integrity": price_stats, "model_market": model_market_checks,
        "capital_breach_candidates": int(capital["eod_capital_breach_rows"].gt(0).sum()),
        "decision": summary,
    }
    checks["all_checks_passed"] = bool(
        model_parity <= 1e-12 and real_parity <= 1e-12 and checks["candidate_count"] == 9
        and checks["duplicate_candidate_dates"] == 0
        and checks["model_rows_per_candidate"] == [2756] and checks["real_rows_per_candidate"] == [986]
        and target_error == 0 and checks["max_sync_target_qty"] == 8
        and checks["return_identity_max_abs"] <= 1e-12 and checks["cash_identity_max_abs"] <= 1e-12
        and checks["nav_recomposition_max_abs"] <= 1e-12 and checks["invalid_return_rows"] == 0
        and price_stats["max_close_price_error"] <= 1e-14
        and all(price_stats[key] == 0 for key in ("nonpositive_close_rows", "nonpositive_volume_rows", "new_leg_nonpositive_oi_rows"))
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v22 integrity failed: {checks}")

    record = build_record(metrics, decisions, capital, summary, checks, basis)
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics.to_csv(STAGING / "scan_summary.csv", index=False)
    wide.to_csv(STAGING / "window_metrics.csv", index=False)
    decisions.to_csv(STAGING / "scenario_decisions.csv", index=False)
    cycles.to_csv(STAGING / "cycle_metrics.csv", index=False)
    capital.to_csv(STAGING / "capital_audit.csv", index=False)
    pd.concat([model_sync_schedule, real_sync_schedule], ignore_index=True, sort=False).to_csv(STAGING / "put_target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "put_trades.csv.gz", index=False, compression="gzip")
    pd.concat([model_lives.assign(layer="model"), real_lives.assign(layer="real")], ignore_index=True, sort=False).to_csv(STAGING / "put_lifecycles.csv.gz", index=False, compression="gzip")
    price_audit.to_csv(STAGING / "real_put_price_audit.csv", index=False)
    (STAGING / "integrity_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    command = f"python {Path(__file__).name}"
    (STAGING / "command_log.txt").write_text(command + "\n", encoding="utf-8")

    metrics.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update({
        "scan_type": "candidate_bundle", "baseline": {"variant": "grid_core_put", "grid": "1.60/2.00"},
        "candidate_grid": [{"scenario": s, "variant": v} for s in SCENARIOS for v in VARIANTS],
        "data_snapshot": {"model": "2015-04-16/2026-08-14 theoretical MO", "real": "2022-07-22/2026-08-14 official CFFEX IM/MO", "timezone": "Asia/Shanghai"},
        "cost_model": {"grid_one_way": grid.ONE_WAY_COST, "grid_roll_round_trip": 2 * grid.ONE_WAY_COST, "margin_buffer_per_im": grid.MARGIN_RATE, "cash_annual": 0.03, "put_execution": "T+1 close", "max_sync_put_qty": 8, "average_basis_annual": float(basis["annual_geometric"])},
        "source_hashes": source_hashes, "parity_check": {"model_put": model_parity, "real_put": real_parity},
        "decision": summary["decision"], "stability_label": summary["stability_label"],
        "warnings": ["Model options before 2022 are theoretical", "Average basis uses future post-listing mean", "Real history under five years", "Research only; not live approved"],
        "elapsed_sec": (datetime.now().astimezone() - started).total_seconds(), "git_status_after": grid.git_status(),
    })
    (SCAN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    shutil.copy2(SCAN / "scan_meta.json", STAGING / "scan_meta.json")
    manifest = {
        "version": VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH, "script_sha256": grid.sha256(Path(__file__)), "source_hashes": source_hashes,
        "basis_assumption": basis, "decision": summary, "integrity": checks,
        "git_status_before": git_before, "git_status_after": grid.git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    hashes = {path.name: grid.sha256(path) for path in sorted(STAGING.iterdir()) if path.is_file()}
    (STAGING / "output_manifest.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STAGING.replace(OUTPUT)
    print(metrics[metrics["segment"].eq("full")][["scenario", "variant", "ann_return", "max_dd", "ann_return_delta_vs_grid_core", "max_dd_delta_vs_grid_core"]].to_json(orient="records", force_ascii=False, indent=2))
    print(capital[capital["variant"].eq("grid_sync_put")].to_json(orient="records", force_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
