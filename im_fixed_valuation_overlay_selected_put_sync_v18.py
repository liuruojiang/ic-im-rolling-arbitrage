#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas", "requests", "tabulate"]
# ///
"""Compare core-only versus synchronized mainline Put on three frozen IM overlay lines."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_overlay_lower_boundary_scan_v17 as v17
import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_selected_put_sync_v18"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "830f35c9e245687868840872ea078d69921be1c5871b1b52521c2639a701e099"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_fixed_valuation_overlay_selected_put_sync_v18"

V17_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "daily_candidates.csv.gz"
V17_TRADES = ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "overlay_trade_audit.csv"
V14_SCHEDULE = ROOT / "outputs" / "im_mo_reconstructed_floor_selection_v14" / "signal_schedules.csv.gz"
V12_SCHEDULE = ROOT / "outputs" / "im_mo_adaptive_valuation_mom120_floor_v12" / "signal_schedules.csv.gz"

INPUT_HASHES = {
    ROOT / "im_fixed_valuation_overlay_lower_boundary_scan_v17.py": "603cc89f7e3cc2139b540e38211a0f331cc61f5036a44d0f98cfe760dc8e86a8",
    ROOT / "docs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17_spec.md": "2130d4617dca14d386a0060079b5d49dd9c4c0f9e033ab921f998c4394803ccb",
    V17_DAILY: "626d585ef131078fe720fe3368fbf1e31d2ad1ca9fa6ea1b3ca9f41046c7af70",
    ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "candidate_decisions.csv": "9ae5fabd005132742944171a26fdb5f387017fb6223cba8d4532d8a8b3a28ba4",
    ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "integrity_checks.json": "7a0e7093e6cb820c27f236d0eacd200f5d361acebac96b609d79f7f89f5f28da",
    V14_SCHEDULE: "9ab345c4e13e42b1d3040c09bdc8466dfabdabd7d6da227a5b623a23edab1549",
    V12_SCHEDULE: "f4928e0175cc6ca698ccbc7c31dfc471c66d13fb1f0030cf3c6bf3f8d6c29ef4",
    ROOT / "im_mo_close_execution_v8.py": "4ac38a47dac471bcaea77e817f6d74a5fe8ccb65484aa79a4844c80b2226eace",
    ROOT / "im_mo_csi1000_put_protection_battery_v6.py": "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d",
    ROOT / "im_valuation_frequency_tenor_scan_v4.py": "c654aa7c30c4a89954f8c7db7d352664ab3ac0c5455c2b26248c5aca75476461",
    ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1" / "daily_nav.csv": "0a3719ade254a32eaf1886dc7d00e9d84aa93498e9a2fecf2868cbefefb60b99",
    ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_mo_puts.csv": "cf7be9a5218c361961641c6e6a05745d581f87875ac67702fa95d3f4dbe71596",
    ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_im_contracts.csv": "6f19f04824026e3cf7e4fc7ebfeb20f60637e53bfc3caebc616fae47794f3cc0",
    ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv": "e42b94ad52a39687a5a0d92fe7f3c28481f34420bac6ac0d0c62ffcdf0e68bf9",
    ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_H00852.csv": "6483caa2cba5c2bf7e300c949380ddc8ffeaf7877152679e3754a99d841ae40a",
    ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "chinabond_government_10y.csv": "f70dc82a18da9e69176393066467f68666fe451c3f659a0a36b42a351c833d39",
    ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "sina_sh000852_index.csv": "9d3995a7189137fee79e5aaa2a58aced57101a1329f1236aca8a0adc86babe74",
    ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "data_manifest.json": "719b9b9fe5fbbad4a15769a18238208953af7ceadc4ec88853750fb5f201fb59",
}

LINES = ((0.85, 1.25), (0.90, 1.40), (1.00, 1.40))
MODES = ("core_put_only", "synchronized_mainline_put")


def line_label(low: float, high: float) -> str:
    return v17.fixed_label(low, high)


def candidate_label(low: float, high: float, mode: str) -> str:
    return f"{line_label(low, high)}__{mode}"


def verify_inputs(*, require_fresh_output: bool) -> dict[str, Any]:
    if v17.b.sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v18 specification mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_HASH:
        raise RuntimeError("Frozen v18 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        actual = v17.b.sha256(path) if path.exists() else "missing"
        if actual != expected:
            raise RuntimeError(f"Frozen v18 input changed: {path.relative_to(ROOT)}: {actual}")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v18 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v18 parameter-scan directory is missing")
    v17_checks = json.loads(
        (ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "integrity_checks.json").read_text(encoding="utf-8")
    )
    if not v17_checks.get("all_checks_passed"):
        raise RuntimeError("v17 upstream integrity is not passed")
    return {"spec_sha256": SPEC_HASH, "frozen_input_count": len(INPUT_HASHES)}


def load_base_schedules() -> dict[str, pd.DataFrame]:
    model = pd.read_csv(V14_SCHEDULE, parse_dates=["eval_date", "execution_date"])
    model = model[model["floor_qty"].eq(3)].copy()
    real = pd.read_csv(V12_SCHEDULE, parse_dates=["eval_date", "execution_date"])
    real = real[real["layer"].eq("real") & real["candidate"].eq("valmom_center_floor3")].copy()
    if len(model) != 2756 or len(real) != 986:
        raise RuntimeError(f"Unexpected floor3 schedules: model={len(model)}, real={len(real)}")
    return {"model": model, "real": real}


def scaled_schedule(
    schedule: pd.DataFrame,
    path: pd.DataFrame,
    layer: str,
    low: float,
    high: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    units = path[["date", "total_im_units"]].rename(columns={"date": "execution_date"})
    result = schedule.merge(units, on="execution_date", how="left", validate="one_to_one")
    if result["total_im_units"].isna().any():
        raise RuntimeError(f"Missing IM units in {layer} schedule")
    result["base_target_qty"] = result["binary_target_qty"].astype(int)
    result["binary_target_qty"] = (
        result["base_target_qty"] * result["total_im_units"].round().astype(int)
    ).astype(int)
    result["three_tier_target_qty"] = result["binary_target_qty"]
    result["candidate"] = candidate_label(low, high, "synchronized_mainline_put")
    result["schedule_candidate"] = result["candidate"]
    result["put_mode"] = "synchronized_mainline_put"
    formula_error = int(
        result["binary_target_qty"].ne(
            result["base_target_qty"] * result["total_im_units"].round().astype(int)
        ).sum()
    )
    return result, {
        "layer": layer,
        "line": line_label(low, high),
        "rows": int(len(result)),
        "formula_errors": formula_error,
        "max_target_qty": int(result["binary_target_qty"].max()),
        "scaled_rows": int(result["total_im_units"].eq(2).sum()),
    }


def assemble_sync(source: pd.DataFrame, put: pd.DataFrame, label: str) -> pd.DataFrame:
    replacement = put.rename(
        columns={
            "put_pnl_ret": "sync_put_pnl_ret",
            "put_cost_rate": "sync_put_cost_rate",
            "put_mark_fraction": "sync_put_mark_fraction",
            "put_fraction": "sync_put_fraction",
            "put_contract": "sync_put_contract",
        }
    )
    frame = source.merge(replacement, on="date", validate="one_to_one")
    frame["candidate"] = label
    frame["family"] = "selected_put_sync"
    frame["put_mode"] = "synchronized_mainline_put"
    frame["put_pnl_ret"] = frame["sync_put_pnl_ret"]
    frame["put_cost_rate"] = frame["sync_put_cost_rate"]
    frame["put_mark_fraction"] = frame["sync_put_mark_fraction"]
    frame["put_fraction"] = frame["sync_put_fraction"]
    frame["put_contract"] = frame["sync_put_contract"]
    frame["combined_gross_before_cost"] = (
        frame["gross_ret"] + frame["overlay_gross_ret"] + frame["put_pnl_ret"]
    )
    frame["ret"] = (
        (1.0 + frame["combined_gross_before_cost"])
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        - 1.0
    )
    frame["cash_weight_before_put"] = (
        1.0 - v17.b.MARGIN_RATE * frame["total_im_units"]
    ).clip(lower=0.0)
    frame["cash_weight"] = (
        frame["cash_weight_before_put"] - frame["put_mark_fraction"]
    ).clip(lower=0.0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * v17.b.CASH_DAILY
    return frame.drop(
        columns=[
            "sync_put_pnl_ret", "sync_put_cost_rate", "sync_put_mark_fraction",
            "sync_put_fraction", "sync_put_contract",
        ]
    )


def assemble_core(source: pd.DataFrame, label: str) -> pd.DataFrame:
    frame = source.copy()
    frame["candidate"] = label
    frame["family"] = "selected_put_sync"
    frame["put_mode"] = "core_put_only"
    return frame


def put_parity(
    generated: dict[str, pd.DataFrame], v17_daily: pd.DataFrame
) -> dict[str, float]:
    checks: dict[str, float] = {}
    for layer, put in generated.items():
        reference = v17_daily[
            v17_daily["layer"].eq(layer) & v17_daily["candidate"].eq("base_core_put")
        ][["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]]
        joined = put.merge(reference, on="date", suffixes=("_new", "_old"), validate="one_to_one")
        errors = []
        for column in ("put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"):
            errors.append(float((joined[f"{column}_new"] - joined[f"{column}_old"]).abs().max()))
        checks[f"{layer}_base_put_max_abs"] = max(errors)
    return checks


def generic_price_integrity(
    trades: pd.DataFrame, raw_options: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Audit every real trade leg without v8's legacy-candidate special-case assertion."""
    real = trades[trades["layer"].eq("real")].copy()
    lookup = raw_options.set_index(["contract", "date"])
    rows: list[dict[str, Any]] = []
    for row in real.itertuples(index=False):
        day = pd.Timestamp(row.actual_execution_date)
        for leg, contract_col, price_col in (
            ("old", "old_contract", "old_trade_price"),
            ("new", "new_contract", "new_trade_price"),
        ):
            contract = getattr(row, contract_col, "")
            used = getattr(row, price_col, np.nan)
            if not isinstance(contract, str) or not contract or pd.isna(used):
                continue
            quote = lookup.loc[(contract, day)]
            if isinstance(quote, pd.DataFrame):
                raise RuntimeError("Duplicate MO quote during generic close-price audit")
            rows.append(
                {
                    "candidate": row.candidate,
                    "date": day,
                    "leg": leg,
                    "contract": contract,
                    "used_price": float(used),
                    "raw_close": float(quote["close"]),
                    "raw_settle": float(quote["settle"]),
                    "volume": float(quote["volume"]),
                    "open_interest": float(quote["open_interest"]),
                    "abs_close_error": abs(float(used) - float(quote["close"])),
                }
            )
    audit = pd.DataFrame(rows)
    if audit.empty:
        raise RuntimeError("No real MO trade legs in generic price audit")
    stats = {
        "trade_legs": int(len(audit)),
        "max_close_price_error": float(audit["abs_close_error"].max()),
        "nonpositive_close_rows": int(audit["raw_close"].le(0).sum()),
        "nonpositive_volume_rows": int(audit["volume"].le(0).sum()),
        "new_leg_nonpositive_oi_rows": int(
            audit[audit["leg"].eq("new")]["open_interest"].le(0).sum()
        ),
    }
    if stats["max_close_price_error"] > 1e-14:
        raise RuntimeError(f"Close execution price mismatch: {stats}")
    if any(
        stats[key] != 0
        for key in (
            "nonpositive_close_rows",
            "nonpositive_volume_rows",
            "new_leg_nonpositive_oi_rows",
        )
    ):
        raise RuntimeError(f"Non-executable MO trade leg: {stats}")
    return audit, stats


def local_cycle_metrics(daily: pd.DataFrame, v17_trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for low, high in LINES:
        base_label = line_label(low, high)
        for layer in ("model", "real"):
            actions = v17_trades[
                v17_trades["layer"].eq(layer) & v17_trades["candidate"].eq(base_label)
            ].sort_values("execution_date")
            buys = actions[actions["action"].eq("buy")].reset_index(drop=True)
            sells = actions[actions["action"].eq("sell")].reset_index(drop=True)
            if len(buys) != len(sells):
                raise RuntimeError(f"Incomplete v17 cycles: {layer}/{base_label}")
            for cycle_id, (buy, sell) in enumerate(zip(buys.itertuples(), sells.itertuples()), start=1):
                start, end = pd.Timestamp(buy.execution_date), pd.Timestamp(sell.execution_date)
                for mode in MODES:
                    label = candidate_label(low, high, mode)
                    sample = daily[
                        daily["layer"].eq(layer)
                        & daily["candidate"].eq(label)
                        & daily["date"].between(start, end)
                    ].sort_values("date")
                    stats = v17.b.metrics(sample["cash_ret"])
                    rows.append(
                        {
                            "layer": layer,
                            "line": base_label,
                            "candidate": label,
                            "put_mode": mode,
                            "cycle_id": cycle_id,
                            "entry_date": start,
                            "exit_date": end,
                            "rows": len(sample),
                            "ann_return": stats["ann_return"],
                            "max_dd": stats["max_dd"],
                            "put_pnl_sum": float(sample["put_pnl_ret"].sum()),
                            "put_cost_sum": float(sample["put_cost_rate"].sum()),
                            "max_put_mark_fraction": float(sample["put_mark_fraction"].max()),
                            "max_put_fraction": float(sample["put_fraction"].max()),
                        }
                    )
    return pd.DataFrame(rows)


def capital_audit(
    daily: pd.DataFrame, schedules: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    trade_dates = {
        (str(row.layer), str(row.candidate), pd.Timestamp(row.actual_execution_date))
        for row in trades.itertuples(index=False)
    }
    for (layer, candidate), group in daily[
        daily["put_mode"].eq("synchronized_mainline_put")
    ].groupby(["layer", "candidate"]):
        execution = group.apply(
            lambda row: (layer, candidate, pd.Timestamp(row["date"])) in trade_dates, axis=1
        )
        breach = group["put_mark_fraction"] > group["cash_weight_before_put"] + 1e-12
        schedule = schedules[
            schedules["layer"].eq(layer) & schedules["candidate"].eq(candidate)
        ]
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "put_trade_days": int(execution.sum()),
                "execution_capital_breach_rows": int((execution & breach).sum()),
                "daily_mark_above_cash_rows": int(breach.sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "min_static_cash_after_put": float(
                    (group["cash_weight_before_put"] - group["put_mark_fraction"]).min()
                ),
                "max_target_qty": int(schedule["binary_target_qty"].max()),
            }
        )
    return pd.DataFrame(rows)


def decide(
    metrics: pd.DataFrame,
    local: pd.DataFrame,
    capital: pd.DataFrame,
    price_stats: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    metric_lookup = metrics.set_index(["layer", "candidate", "window"])
    capital_lookup = capital.set_index(["layer", "candidate"])
    rows: list[dict[str, Any]] = []
    for low, high in LINES:
        core = candidate_label(low, high, "core_put_only")
        sync = candidate_label(low, high, "synchronized_mainline_put")
        model_headline = max(
            float(metric_lookup.loc[("model", sync, w), "max_dd"] - metric_lookup.loc[("model", core, w), "max_dd"])
            for w in ("last_5y", "last_3y")
        )
        real_headline = max(
            float(metric_lookup.loc[("real", sync, w), "max_dd"] - metric_lookup.loc[("real", core, w), "max_dd"])
            for w in ("full", "last_3y")
        )
        local_improvements = {}
        for layer in ("model", "real"):
            subset = local[local["layer"].eq(layer) & local["line"].eq(line_label(low, high))]
            worst = subset.groupby("put_mode")["max_dd"].min()
            local_improvements[layer] = float(
                worst["synchronized_mainline_put"] - worst["core_put_only"]
            )
        model_return_losses = [
            float(metric_lookup.loc[("model", core, w), "ann_return"] - metric_lookup.loc[("model", sync, w), "ann_return"])
            for w in ("full", "last_5y", "last_3y")
        ]
        real_return_losses = [
            float(metric_lookup.loc[("real", core, w), "ann_return"] - metric_lookup.loc[("real", sync, w), "ann_return"])
            for w in ("full", "last_3y")
        ]
        cap_rows = [capital_lookup.loc[(layer, sync)] for layer in ("model", "real")]
        capital_gate = bool(all(int(row["execution_capital_breach_rows"]) == 0 for row in cap_rows))
        headline_gate = bool(model_headline >= 0.010 - 1e-12 and real_headline >= 0.010 - 1e-12)
        local_gate = bool(
            local_improvements["model"] >= 0.020 - 1e-12
            and local_improvements["real"] >= 0.020 - 1e-12
        )
        return_gate = bool(
            max(model_return_losses) <= 0.015 + 1e-12
            and max(real_return_losses) <= 0.015 + 1e-12
        )
        liquidity_gate = bool(
            all(int(row["max_target_qty"]) <= 6 for row in cap_rows)
            and price_stats["nonpositive_close_rows"] == 0
            and price_stats["nonpositive_volume_rows"] == 0
            and price_stats["new_leg_nonpositive_oi_rows"] == 0
        )
        passed = bool(capital_gate and headline_gate and local_gate and return_gate and liquidity_gate)
        rows.append(
            {
                "line": line_label(low, high),
                "core_candidate": core,
                "sync_candidate": sync,
                "model_headline_dd_improvement": model_headline,
                "real_headline_dd_improvement": real_headline,
                "model_worst_cycle_dd_improvement": local_improvements["model"],
                "real_worst_cycle_dd_improvement": local_improvements["real"],
                "max_model_cagr_loss": max(model_return_losses),
                "max_real_cagr_loss": max(real_return_losses),
                "capital_gate": capital_gate,
                "headline_risk_gate": headline_gate,
                "local_risk_gate": local_gate,
                "return_tolerance_gate": return_gate,
                "liquidity_gate": liquidity_gate,
                "put_sync_pass": passed,
            }
        )
    decisions = pd.DataFrame(rows)
    supported = decisions.loc[decisions["put_sync_pass"], "line"].tolist()
    summary = {
        "decision": "selected_line_put_sync_supported" if supported else "retain_core_put_only_for_lower_boundary_overlay",
        "supported_lines": supported,
        "passed_count": len(supported),
        "tested_line_count": len(LINES),
        "stability_label": "selected_bundle_pass" if supported else "reject",
        "live_approved": False,
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return decisions, summary


def scan_tables(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    model = metrics[metrics["layer"].eq("model") & ~metrics["candidate"].eq("base_core_put")].copy()
    long = model.rename(columns={"window": "segment", "actual_start": "start"})[
        ["candidate", "segment", "start", "end", "rows", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "low_threshold", "high_threshold"]
    ]
    parts = []
    for metric in ("ann_return", "max_dd"):
        pivot = long.pivot(index="candidate", columns="segment", values=metric)
        pivot.columns = [f"{metric}_{column}" for column in pivot.columns]
        parts.append(pivot)
    params = long.groupby("candidate", as_index=True)[["low_threshold", "high_threshold"]].first()
    wide = params.join(parts).reset_index()
    return long, wide


def build_record(
    metrics: pd.DataFrame,
    decisions: pd.DataFrame,
    local: pd.DataFrame,
    summary: dict[str, Any],
    checks: dict[str, Any],
) -> str:
    focus = metrics[
        ~metrics["candidate"].eq("base_core_put")
        & (
            metrics["layer"].eq("model")
            | metrics["window"].isin(["full", "last_3y", "last_1y"])
        )
    ][["layer", "candidate", "window", "ann_return", "max_dd", "calmar"]]
    return "\n".join(
        [
            "# IM更低估值增仓三条观察线Put同步对照 v18",
            "",
            "状态：研究结果；未批准实盘。",
            "",
            "## Decision / Stability / Data",
            "",
            f"- Decision: `{summary['decision']}`; passed {summary['passed_count']} / 3.",
            f"- Stability: `{summary['stability_label']}`.",
            "- Data: model 2015-04-16—2026-08-14; real IM/MO 2022-07-22—2026-08-14.",
            "",
            "## Put同步门槛",
            "",
            decisions.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 窗口指标",
            "",
            focus.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 增仓周期局部风险",
            "",
            local.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 完整性与限制",
            "",
            f"- Base Put parity max abs: `{checks['base_put_parity_max_abs']:.3e}`; core-only v17 parity: `{checks['core_only_v17_parity_max_abs']:.3e}`.",
            f"- Target formula errors: `{checks['target_formula_errors']}`; sync-date missing: `{checks['sync_date_missing']}`; return/cash identity: `{checks['return_identity_max_abs']:.3e}` / `{checks['cash_identity_max_abs']:.3e}`.",
            "- 三条线都只含2024年的两轮极端修复，Put结论不具备独立长周期验证。",
            "- 官方收盘价和历史成交量不保证未来容量；结果不是交易建议。",
            "",
        ]
    )


def main() -> None:
    git_before = v17.b.git_status()
    upstream_checks = verify_inputs(require_fresh_output=True)
    v17_daily = pd.read_csv(V17_DAILY, parse_dates=["date"])
    v17_trades = pd.read_csv(V17_TRADES, parse_dates=["signal_date", "execution_date"])
    base_schedules = load_base_schedules()

    model_market, model_market_checks = v6.model_market()
    upstream, _, _, _, _, raw_options = v4.load_inputs()
    active_im = v8.active_im_closes(upstream)
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)

    base_put: dict[str, pd.DataFrame] = {}
    base_trades: list[pd.DataFrame] = []
    base_put["model"], mt, _ = v8.run_model_normal_close(
        model_market, base_schedules["model"], "3m", 0.95, "base_floor3_parity"
    )
    base_put["real"], rt, _ = v8.run_real_normal_close(
        upstream, options, active_im, base_schedules["real"], "3m", 0.95, "base_floor3_parity"
    )
    base_trades.extend([mt, rt])
    parity = put_parity(base_put, v17_daily)

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    life_parts: list[pd.DataFrame] = []
    schedule_audits: list[dict[str, Any]] = []
    for layer in ("model", "real"):
        base_context = v17_daily[
            v17_daily["layer"].eq(layer) & v17_daily["candidate"].eq("base_core_put")
        ].copy()
        base_context["put_mode"] = "base_context"
        daily_parts.append(base_context)
        for low, high in LINES:
            source = v17_daily[
                v17_daily["layer"].eq(layer) & v17_daily["candidate"].eq(line_label(low, high))
            ].copy()
            core_label = candidate_label(low, high, "core_put_only")
            sync_label = candidate_label(low, high, "synchronized_mainline_put")
            daily_parts.append(assemble_core(source, core_label))
            schedule, audit = scaled_schedule(base_schedules[layer], source, layer, low, high)
            schedule["layer"] = layer
            schedule_parts.append(schedule)
            schedule_audits.append(audit)
            if layer == "model":
                put, trades, lives = v8.run_model_normal_close(
                    model_market, schedule, "3m", 0.95, sync_label
                )
            else:
                put, trades, lives = v8.run_real_normal_close(
                    upstream, options, active_im, schedule, "3m", 0.95, sync_label
                )
            daily_parts.append(assemble_sync(source, put, sync_label))
            if len(trades):
                trade_parts.append(trades)
            if len(lives):
                lives = lives.copy()
                lives["layer"] = layer
                life_parts.append(lives)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"])
    daily["cash_nav"] = daily.groupby(["layer", "candidate"])["cash_ret"].transform(lambda values: (1.0 + values).cumprod())
    daily["cash_drawdown"] = daily.groupby(["layer", "candidate"])["cash_nav"].transform(lambda values: values / values.cummax() - 1.0)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False)
    metrics = v17.b.metrics_by_window(daily)
    annual = v17.b.annual_metrics(daily)
    local = local_cycle_metrics(daily, v17_trades)
    capital = capital_audit(daily, schedules, trades)
    price_audit, price_stats = generic_price_integrity(trades, raw_options)
    decisions, summary = decide(metrics, local, capital, price_stats)
    scan_long, scan_wide = scan_tables(metrics)

    core_errors = []
    for low, high in LINES:
        for layer in ("model", "real"):
            old = v17_daily[v17_daily["layer"].eq(layer) & v17_daily["candidate"].eq(line_label(low, high))][["date", "cash_ret"]]
            new = daily[daily["layer"].eq(layer) & daily["candidate"].eq(candidate_label(low, high, "core_put_only"))][["date", "cash_ret"]]
            joined = new.merge(old, on="date", suffixes=("_new", "_old"), validate="one_to_one")
            core_errors.append(float((joined["cash_ret_new"] - joined["cash_ret_old"]).abs().max()))
    expected_return = (
        (1.0 + daily["combined_gross_before_cost"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        - 1.0
    )
    sync_daily = daily[daily["put_mode"].eq("synchronized_mainline_put")]
    sync_exec = set(zip(schedules["layer"], schedules["candidate"], pd.to_datetime(schedules["execution_date"])))
    unit_changes = sync_daily.groupby(["layer", "candidate"])["total_im_units"].diff().fillna(0).ne(0)
    changed = sync_daily[unit_changes]
    missing_sync = sum((row.layer, row.candidate, pd.Timestamp(row.date)) not in sync_exec for row in changed.itertuples())
    checks = {
        **upstream_checks,
        "candidate_count_per_layer": int(daily.groupby("layer")["candidate"].nunique().min()),
        "expected_candidate_count_per_layer": 7,
        "duplicate_candidate_dates": int(daily.duplicated(["layer", "candidate", "date"]).sum()),
        "base_put_parity_max_abs": max(parity.values()),
        "core_only_v17_parity_max_abs": max(core_errors),
        "target_formula_errors": int(sum(item["formula_errors"] for item in schedule_audits)),
        "target_max_qty": int(schedules["binary_target_qty"].max()),
        "sync_date_missing": int(missing_sync),
        "return_identity_max_abs": float((daily["ret"] - expected_return).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * v17.b.CASH_DAILY)).abs().max()),
        "invalid_return_rows": int(daily[["ret", "cash_ret"]].isna().sum().sum() + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()),
        "price_integrity": price_stats,
        "model_market": model_market_checks,
        "capital_execution_breach_rows": int(capital["execution_capital_breach_rows"].sum()),
    }
    checks["all_checks_passed"] = bool(
        checks["candidate_count_per_layer"] == checks["expected_candidate_count_per_layer"] == 7
        and checks["duplicate_candidate_dates"] == 0
        and checks["base_put_parity_max_abs"] <= 1e-14
        and checks["core_only_v17_parity_max_abs"] <= 1e-14
        and checks["target_formula_errors"] == 0
        and checks["target_max_qty"] <= 6
        and checks["sync_date_missing"] == 0
        and checks["return_identity_max_abs"] <= 1e-14
        and checks["cash_identity_max_abs"] <= 1e-14
        and checks["invalid_return_rows"] == 0
        and price_stats["max_close_price_error"] <= 1e-14
        and price_stats["nonpositive_close_rows"] == 0
        and price_stats["nonpositive_volume_rows"] == 0
        and price_stats["new_leg_nonpositive_oi_rows"] == 0
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v18 integrity checks failed: {checks}")

    record = build_record(metrics, decisions, local, summary, checks)
    STAGING.mkdir(parents=True)
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    metrics.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    decisions.to_csv(STAGING / "candidate_decisions.csv", index=False)
    local.to_csv(STAGING / "overlay_cycle_local_risk.csv", index=False)
    schedules.to_csv(STAGING / "put_target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "put_trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(STAGING / "put_lifecycle_audit.csv", index=False)
    capital.to_csv(STAGING / "capital_audit.csv", index=False)
    price_audit.to_csv(STAGING / "close_price_integrity_audit.csv", index=False)
    pd.DataFrame(schedule_audits).to_csv(STAGING / "put_target_sync_audit.csv", index=False)
    (STAGING / "decision_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (STAGING / "integrity_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "script_sha256": v17.b.sha256(Path(__file__)),
        "source_hashes": {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()},
        "selected_lines": [line_label(*pair) for pair in LINES],
        "modes": list(MODES),
        "put_rule": {"tenor": "3m", "moneyness": 0.95, "execution": "T+1 close", "quantity": "base floor3 target times total IM units"},
        "decision": summary,
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": v17.b.git_status(),
        "limitations": ["All selected-line overlay events are in 2024", "Model options are theoretical", "Research only; not live approved"],
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (STAGING / "command_log.txt").write_text("uv run im_fixed_valuation_overlay_selected_put_sync_v18.py\n", encoding="utf-8")
    (STAGING / "output_manifest.json").write_text(json.dumps(v17.b.output_manifest(STAGING), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STAGING.replace(OUTPUT)

    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    shutil.copy2(OUTPUT / "record.md", SCAN / "record.md")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("uv run im_fixed_valuation_overlay_selected_put_sync_v18.py\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
