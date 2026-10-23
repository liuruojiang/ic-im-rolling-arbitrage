#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pandas"]
# ///
"""Scan lower IM fixed-valuation entry and exit boundaries on the frozen floor-3 core."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_overlay_entry_exit_scan_v15 as b


ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_overlay_lower_boundary_scan_v17"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "2130d4617dca14d386a0060079b5d49dd9c4c0f9e033ab921f998c4394803ccb"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_fixed_valuation_overlay_lower_boundary_scan_v17"
V15_SCRIPT = ROOT / "im_fixed_valuation_overlay_entry_exit_scan_v15.py"
V15_SCRIPT_HASH = "d80e7286de1d6571c59b79f965f78a733e7a8243fc3724d581d792d31b3a3aa0"
V15_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_entry_exit_scan_v15" / "daily_candidates.csv.gz"
V15_DAILY_HASH = "4f990c65eff6f19ae4f102c2e7e6a742c3f0c3cc3527ca0ffa91f83ed9df2af9"

ENTRY_THRESHOLDS = tuple(round(0.80 + 0.05 * index, 2) for index in range(13))
EXIT_THRESHOLDS = tuple(round(1.20 + 0.05 * index, 2) for index in range(19))
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")


def fixed_grid() -> list[tuple[float, float]]:
    return [
        (low, high)
        for low in ENTRY_THRESHOLDS
        for high in EXIT_THRESHOLDS
        if high - low >= 0.30 - 1e-12
    ]


def fixed_label(low: float, high: float) -> str:
    return b.fixed_label(low, high)


def verify_inputs(*, require_fresh_output: bool) -> dict[str, Any]:
    if b.sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v17 specification mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_HASH:
        raise RuntimeError("Frozen v17 specification sidecar mismatch")
    if b.sha256(V15_SCRIPT) != V15_SCRIPT_HASH:
        raise RuntimeError("Frozen v15 implementation changed")
    if b.sha256(V15_DAILY) != V15_DAILY_HASH:
        raise RuntimeError("Frozen v15 parity artifact changed")
    for path, expected in b.INPUT_HASHES.items():
        actual = b.sha256(path) if path.exists() else "missing"
        if actual != expected:
            raise RuntimeError(f"Frozen upstream input changed: {path.relative_to(ROOT)}: {actual}")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v17 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v17 parameter-scan directory is missing")
    if len(fixed_grid()) != 192:
        raise RuntimeError(f"Frozen v17 grid count changed: {len(fixed_grid())}")
    return {
        "spec_sha256": SPEC_HASH,
        "frozen_input_count": len(b.INPUT_HASHES) + 2,
        "fixed_candidate_count": len(fixed_grid()),
    }


def metric_slice(table: pd.DataFrame, layer: str, candidate: str) -> pd.DataFrame:
    return table[(table["layer"].eq(layer)) & table["candidate"].eq(candidate)].set_index("window")


def decide(
    metric_table: pd.DataFrame, cycles: pd.DataFrame, capital: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    base_model = metric_slice(metric_table, "model", "base_core_put")
    base_real = metric_slice(metric_table, "real", "base_core_put")
    boundary_model = metric_slice(metric_table, "model", fixed_label(1.40, 2.10))
    boundary_real = metric_slice(metric_table, "real", fixed_label(1.40, 2.10))
    cycle_lookup = cycles.set_index(["layer", "candidate"])
    capital_lookup = capital.set_index(["layer", "candidate"])
    rows: list[dict[str, Any]] = []
    for low, high in fixed_grid():
        candidate = fixed_label(low, high)
        model = metric_slice(metric_table, "model", candidate)
        real = metric_slice(metric_table, "real", candidate)
        mc = cycle_lookup.loc[("model", candidate)]
        rc = cycle_lookup.loc[("real", candidate)]
        mcap = capital_lookup.loc[("model", candidate)]
        rcap = capital_lookup.loc[("real", candidate)]
        capital_gate = bool(
            mcap["capital_execution_breach_rows"] == 0
            and rcap["capital_execution_breach_rows"] == 0
        )
        event_gate = bool(
            mc["completed_cycles"] >= 1
            and rc["completed_cycles"] >= 1
            and mc["holding_ratio"] <= 0.35 + 1e-12
            and mc["pending_order_end"] == 0
            and rc["pending_order_end"] == 0
        )
        model_return_gate = bool(
            all(
                model.loc[w, "ann_return"] >= base_model.loc[w, "ann_return"] - 0.010 - 1e-12
                for w in WINDOWS
            )
        )
        real_return_gate = bool(
            all(
                real.loc[w, "ann_return"] >= base_real.loc[w, "ann_return"] - 0.010 - 1e-12
                for w in ("full", "last_3y", "last_1y")
            )
        )
        model_risk_windows = ("full", "last_10y", "last_5y", "last_3y")
        real_risk_windows = ("full", "last_3y")
        model_deteriorations = [
            float(base_model.loc[w, "max_dd"] - model.loc[w, "max_dd"])
            for w in model_risk_windows
        ]
        real_deteriorations = [
            float(base_real.loc[w, "max_dd"] - real.loc[w, "max_dd"])
            for w in real_risk_windows
        ]
        worst_core_dd_deterioration = max(model_deteriorations + real_deteriorations)
        core_risk_gate = bool(worst_core_dd_deterioration <= 0.030 + 1e-12)
        real_dd_improvement_vs_boundary = float(
            real.loc["full", "max_dd"] - boundary_real.loc["full", "max_dd"]
        )
        boundary_repair_gate = bool(real_dd_improvement_vs_boundary >= 0.050 - 1e-12)
        hard = bool(
            capital_gate
            and event_gate
            and model_return_gate
            and real_return_gate
            and core_risk_gate
            and boundary_repair_gate
        )
        rows.append(
            {
                "candidate": candidate,
                "low_threshold": low,
                "high_threshold": high,
                "model_completed_cycles": int(mc["completed_cycles"]),
                "real_completed_cycles": int(rc["completed_cycles"]),
                "model_holding_ratio": float(mc["holding_ratio"]),
                "model_full_ann_return": float(model.loc["full", "ann_return"]),
                "model_full_max_dd": float(model.loc["full", "max_dd"]),
                "model_5y_max_dd": float(model.loc["last_5y", "max_dd"]),
                "real_full_ann_return": float(real.loc["full", "ann_return"]),
                "real_full_max_dd": float(real.loc["full", "max_dd"]),
                "real_dd_improvement_vs_boundary": real_dd_improvement_vs_boundary,
                "worst_core_dd_deterioration": worst_core_dd_deterioration,
                "capital_execution_breach_rows": int(
                    mcap["capital_execution_breach_rows"] + rcap["capital_execution_breach_rows"]
                ),
                "capital_gate": capital_gate,
                "event_gate": event_gate,
                "model_return_gate": model_return_gate,
                "real_return_gate": real_return_gate,
                "core_risk_gate": core_risk_gate,
                "boundary_repair_gate": boundary_repair_gate,
                "hard_gate_pass": hard,
                "boundary_model_full_max_dd": float(boundary_model.loc["full", "max_dd"]),
                "boundary_real_full_max_dd": float(boundary_real.loc["full", "max_dd"]),
            }
        )
    decisions = pd.DataFrame(rows)
    lookup = decisions.set_index(["low_threshold", "high_threshold"])
    grid = set(fixed_grid())
    ridge_rows: list[dict[str, Any]] = []
    width: dict[str, bool] = {}
    for row in decisions.itertuples(index=False):
        neighbors = {
            "low_down": (round(row.low_threshold - 0.05, 2), row.high_threshold),
            "low_up": (round(row.low_threshold + 0.05, 2), row.high_threshold),
            "high_down": (row.low_threshold, round(row.high_threshold - 0.05, 2)),
            "high_up": (row.low_threshold, round(row.high_threshold + 0.05, 2)),
        }
        passes: list[bool] = []
        for direction, key in neighbors.items():
            exists = key in grid
            if exists:
                neighbor = lookup.loc[key]
                risk_delta = float(
                    neighbor["worst_core_dd_deterioration"] - row.worst_core_dd_deterioration
                )
                passed = bool(neighbor["hard_gate_pass"] and risk_delta <= 0.020 + 1e-12)
            else:
                risk_delta, passed = np.nan, False
            passes.append(passed)
            ridge_rows.append(
                {
                    "candidate": row.candidate,
                    "direction": direction,
                    "neighbor_low": key[0],
                    "neighbor_high": key[1],
                    "neighbor_exists": exists,
                    "neighbor_hard_gate_pass": bool(lookup.loc[key, "hard_gate_pass"]) if exists else False,
                    "neighbor_worst_risk_delta": risk_delta,
                    "neighbor_width_pass": passed,
                }
            )
        width[row.candidate] = bool(row.hard_gate_pass and all(passes))
    decisions["width_supported"] = decisions["candidate"].map(width)
    order_cols = [
        "worst_core_dd_deterioration",
        "real_full_max_dd",
        "model_5y_max_dd",
        "model_completed_cycles",
        "low_threshold",
        "high_threshold",
    ]
    ascending = [True, False, False, False, True, True]
    wide = decisions[decisions["width_supported"]].sort_values(order_cols, ascending=ascending)
    hard = decisions[decisions["hard_gate_pass"]].sort_values(order_cols, ascending=ascending)
    selected = str(wide.iloc[0]["candidate"]) if len(wide) else None
    raw = str(hard.iloc[0]["candidate"]) if len(hard) else None
    if selected:
        decision, stability = "defensive_watchlist_lower_boundary", "wide_stable"
    elif raw:
        decision, stability = "watchlist_peak_or_ridge", "peak_or_ridge_only"
    else:
        decision, stability = "lower_boundary_not_supported", "reject"
    summary = {
        "decision": decision,
        "robustness_status": stability,
        "selected_candidate": selected,
        "raw_hard_gate_winner": raw,
        "hard_gate_pass_count": int(decisions["hard_gate_pass"].sum()),
        "width_supported_count": int(decisions["width_supported"].sum()),
        "maximum_status": "defensive_watchlist_only",
        "eligible_for_put_sync_followup": bool(decisions["hard_gate_pass"].any()),
        "live_approved": False,
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return decisions, pd.DataFrame(ridge_rows), summary


def scan_tables(metric_table: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    wanted = metric_table[metric_table["layer"].eq("model")].copy()
    wanted = wanted[wanted["candidate"].eq("base_core_put") | wanted["family"].eq("fixed_score")]
    long = wanted.rename(columns={"window": "segment", "actual_start": "start"})[
        [
            "candidate", "segment", "start", "end", "rows", "ann_return", "ann_vol",
            "sharpe_repo", "max_dd", "low_threshold", "high_threshold",
        ]
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
    metric_table: pd.DataFrame,
    decisions: pd.DataFrame,
    cycles: pd.DataFrame,
    summary: dict[str, Any],
    checks: dict[str, Any],
) -> str:
    candidates = ["base_core_put", fixed_label(1.40, 2.10)]
    for value in (summary["selected_candidate"], summary["raw_hard_gate_winner"]):
        if value and value not in candidates:
            candidates.append(value)
    top = decisions.sort_values(
        ["worst_core_dd_deterioration", "real_full_max_dd"], ascending=[True, False]
    ).head(5)["candidate"].tolist()
    candidates.extend(value for value in top if value not in candidates)
    lines = [
        "# IM固定估值增仓更低边界扫描 v17",
        "",
        "状态：研究结果；未批准实盘。",
        "",
        "## 结论",
        "",
        f"- 决定：`{summary['decision']}`；稳健性：`{summary['robustness_status']}`。",
        f"- 硬门槛通过 {summary['hard_gate_pass_count']} / 192；严格四邻宽度通过 {summary['width_supported_count']} / 192。",
        f"- 机械宽平台候选：`{summary['selected_candidate']}`；硬门槛风险优先候选：`{summary['raw_hard_gate_winner']}`。",
        "- 开仓阈值不高于1.30的信号高度集中在2024；最高只能形成防御观察线。",
        "",
        "## 代表窗口",
        "",
        "| 层 | 候选 | 窗口 | CAGR | MaxDD | Calmar |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    show = metric_table[metric_table["candidate"].isin(candidates)]
    for row in show.sort_values(["layer", "candidate", "window"]).itertuples(index=False):
        lines.append(
            f"| {row.layer} | {row.candidate} | {row.window} | {row.ann_return:.2%} | {row.max_dd:.2%} | {row.calmar:.3f} |"
        )
    lines.extend(["", "## 周期", "", "| 层 | 候选 | 完成周期 | 持有比例 |", "| --- | --- | ---: | ---: |"]) 
    for row in cycles[cycles["candidate"].isin(candidates)].sort_values(["layer", "candidate"]).itertuples(index=False):
        lines.append(f"| {row.layer} | {row.candidate} | {row.completed_cycles} | {row.holding_ratio:.2%} |")
    lines.extend(
        [
            "",
            "## 完整性与限制",
            "",
            f"- 底仓逐日奇偶最大误差：{checks['base_parity_max_abs']:.3e}；v15边界路径奇偶最大误差：{checks['v15_boundary_parity_max_abs']:.3e}。",
            f"- 收益/现金恒等式最大误差：{checks['return_identity_max_abs']:.3e} / {checks['cash_identity_max_abs']:.3e}；因果失败：{checks['causality_failures']}。",
            "- 模型层不是上市前IM贴水的复制；真实IM/MO仅从2022-07-22开始。",
            "- 2024事件集中意味着漂亮结果可能只是单次低估修复，不能解释为已获准实盘。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    git_before = b.git_status()
    upstream_checks = verify_inputs(require_fresh_output=True)
    base, score, percentile = b.load_sources()
    model_market, model_market_checks = b.build_model_market(base, score, percentile)
    real_market, real_market_checks = b.build_real_market(base, score, percentile)
    fixed_history = score[["date", "unbounded_median_knot"]].copy()

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    cycle_rows: list[dict[str, Any]] = []
    base_recomputed: dict[str, pd.DataFrame] = {}
    for layer, market in (("model", model_market), ("real", real_market)):
        layer_base = base[base["layer"].eq(layer)].sort_values("date").reset_index(drop=True)
        flat, flat_cycle = b.flat_overlay(market, layer)
        recomputed = b.assemble_candidate(layer_base, flat, "base_core_put", "core")
        base_recomputed[layer] = recomputed
        daily_parts.append(recomputed)
        cycle_rows.append(flat_cycle)
        for low, high in fixed_grid():
            candidate = fixed_label(low, high)
            overlay, trades, cycle = b.simulate_overlay(
                market, fixed_history, "unbounded_median_knot", low, high,
                candidate, "fixed_score", layer,
            )
            daily_parts.append(b.assemble_candidate(layer_base, overlay, candidate, "fixed_score"))
            trade_parts.append(trades)
            cycle_rows.append(cycle)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"])
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["layer", "candidate", "execution_date"])
    cycles = pd.DataFrame(cycle_rows).sort_values(["layer", "candidate"])
    metric_table = b.metrics_by_window(daily)
    annual = b.annual_metrics(daily)
    drawdowns = b.drawdown_audit(daily)
    capital = b.capital_audit(daily)
    decisions, ridge, summary = decide(metric_table, cycles, capital)
    scan_surface = decisions.merge(
        metric_table[(metric_table["layer"].eq("model")) & metric_table["window"].eq("full")][
            ["candidate", "ann_return_delta_vs_core", "max_dd_improvement_vs_core"]
        ], on="candidate", validate="one_to_one",
    )
    scan_long, scan_wide = scan_tables(metric_table)

    parity_errors = []
    for layer in ("model", "real"):
        original = base[base["layer"].eq(layer)][["date", "cash_ret"]]
        rebuilt = base_recomputed[layer][["date", "cash_ret"]]
        joined = rebuilt.merge(original, on="date", suffixes=("_new", "_v14"), validate="one_to_one")
        parity_errors.append(float((joined["cash_ret_new"] - joined["cash_ret_v14"]).abs().max()))
    v15 = pd.read_csv(V15_DAILY, parse_dates=["date"])
    v15_boundary = v15[v15["candidate"].eq(fixed_label(1.40, 2.10))][["layer", "date", "cash_ret"]]
    v17_boundary = daily[daily["candidate"].eq(fixed_label(1.40, 2.10))][["layer", "date", "cash_ret"]]
    boundary_join = v17_boundary.merge(v15_boundary, on=["layer", "date"], suffixes=("_v17", "_v15"), validate="one_to_one")
    return_expected = (
        (1.0 + daily["combined_gross_before_cost"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        - 1.0
    )
    causal = trades[~trades["execution_reason"].eq("initial_listing_carry")]
    checks = {
        **upstream_checks,
        "candidate_count_per_layer": int(daily.groupby("layer")["candidate"].nunique().min()),
        "expected_candidate_count_per_layer": 1 + len(fixed_grid()),
        "duplicate_candidate_dates": int(daily.duplicated(["layer", "candidate", "date"]).sum()),
        "base_parity_max_abs": max(parity_errors),
        "v15_boundary_parity_max_abs": float((boundary_join["cash_ret_v17"] - boundary_join["cash_ret_v15"]).abs().max()),
        "v15_boundary_row_difference": int(len(v17_boundary) - len(v15_boundary)),
        "return_identity_max_abs": float((daily["ret"] - return_expected).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - (daily["ret"] + daily["cash_weight"] * b.CASH_DAILY)).abs().max()),
        "causality_failures": int((pd.to_datetime(causal["execution_date"]) <= pd.to_datetime(causal["signal_date"])).sum()),
        "invalid_real_execution_quotes": int((trades["layer"].eq("real") & (trades["execution_open"].le(0) | trades["execution_volume"].le(0))).sum()),
        "invalid_total_im_units": int((~daily["total_im_units"].isin([1.0, 2.0])).sum()),
        "negative_cash_weight": int(daily["cash_weight"].lt(-1e-12).sum()),
        "hard_gate_capital_failures": int(decisions[decisions["hard_gate_pass"]]["capital_gate"].eq(False).sum()),
        "invalid_return_rows": int(daily[["ret", "cash_ret"]].isna().sum().sum() + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()),
        "pending_fixed_orders": int(cycles[cycles["family"].eq("fixed_score")]["pending_order_end"].sum()),
        "model_market": model_market_checks,
        "real_market": real_market_checks,
    }
    checks["all_checks_passed"] = bool(
        checks["fixed_candidate_count"] == 192
        and checks["candidate_count_per_layer"] == checks["expected_candidate_count_per_layer"] == 193
        and checks["duplicate_candidate_dates"] == 0
        and checks["base_parity_max_abs"] <= 1e-14
        and checks["v15_boundary_parity_max_abs"] <= 1e-14
        and checks["v15_boundary_row_difference"] == 0
        and checks["return_identity_max_abs"] <= 1e-14
        and checks["cash_identity_max_abs"] <= 1e-14
        and checks["causality_failures"] == 0
        and checks["invalid_real_execution_quotes"] == 0
        and checks["invalid_total_im_units"] == 0
        and checks["negative_cash_weight"] == 0
        and checks["hard_gate_capital_failures"] == 0
        and checks["invalid_return_rows"] == 0
        and checks["pending_fixed_orders"] == 0
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"v17 integrity checks failed: {checks}")

    record = build_record(metric_table, decisions, cycles, summary, checks)
    STAGING.mkdir(parents=True)
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    metric_table.to_csv(STAGING / "metrics_by_window.csv", index=False)
    scan_wide.to_csv(STAGING / "window_metrics_wide.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    scan_surface.to_csv(STAGING / "scan_surface.csv", index=False)
    decisions.to_csv(STAGING / "candidate_decisions.csv", index=False)
    ridge.to_csv(STAGING / "ridge_width.csv", index=False)
    trades.to_csv(STAGING / "overlay_trade_audit.csv", index=False)
    cycles.to_csv(STAGING / "overlay_cycle_summary.csv", index=False)
    drawdowns.to_csv(STAGING / "drawdown_audit.csv", index=False)
    capital.to_csv(STAGING / "capital_audit.csv", index=False)
    cycles.to_csv(STAGING / "state_geometry_audit.csv", index=False)
    (STAGING / "decision_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (STAGING / "integrity_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "script_sha256": b.sha256(Path(__file__)),
        "source_hashes": {
            str(path.relative_to(ROOT)): value for path, value in b.INPUT_HASHES.items()
        } | {
            str(V15_SCRIPT.relative_to(ROOT)): V15_SCRIPT_HASH,
            str(V15_DAILY.relative_to(ROOT)): V15_DAILY_HASH,
        },
        "samples": {"model": [str(b.MODEL_START.date()), str(b.END.date())], "real": [str(b.REAL_START.date()), str(b.END.date())]},
        "grid": {"entry_thresholds": list(ENTRY_THRESHOLDS), "exit_thresholds": list(EXIT_THRESHOLDS), "minimum_gap": 0.30, "fixed_candidates": len(fixed_grid())},
        "execution": {"signal": "T close valuation", "model": "T+1 modeled index open", "real": "T+1 active IM official open", "one_way_cost": b.ONE_WAY_COST, "margin_buffer_per_im_unit": b.MARGIN_RATE, "cash_annual_return": 0.03, "overlay_put": "none; frozen core Put unchanged"},
        "decision": summary,
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": b.git_status(),
        "limitations": ["Signals below 1.30 are concentrated in 2024", "Pre-IM model does not contain real IM basis", "Research only; not live approved"],
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (STAGING / "command_log.txt").write_text("uv run im_fixed_valuation_overlay_lower_boundary_scan_v17.py\n", encoding="utf-8")
    (STAGING / "output_manifest.json").write_text(json.dumps(b.output_manifest(STAGING), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STAGING.replace(OUTPUT)

    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    shutil.copy2(OUTPUT / "record.md", SCAN / "record.md")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("uv run im_fixed_valuation_overlay_lower_boundary_scan_v17.py\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
