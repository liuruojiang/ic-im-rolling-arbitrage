"""Rebuild IC Put research, layer 9: short-Put quantity and entry Delta."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_v13_short95_quantity_delta_scan_v1 as quantity
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l9_short_put_sizing_q1_qd05_qd10"
LAYER8 = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_pollution_rebuild_layer8_iv30_no_mom_m1_short95_hold_naked_absolute_iv_"
    "instant_valuation_router_core_long_put_profit_take_and_restrike_none_2x_3x"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer9_short_quantity_delta_v1_spec.md"
IV_THRESHOLD = 0.30
MODES = quantity.MODES
PROFIT_LINES: tuple[tuple[str, float | None], ...] = (("none", None), ("profit3x", 3.0))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_signal(prepared: dict[str, object]) -> pd.DataFrame:
    prior = base.prior
    scope = str(prepared["scope"])
    signal = prepared["base_signal"].copy()
    signal["route"] = (
        signal.admission.astype(bool)
        & signal.execution_open_valid.astype(bool)
        & np.isfinite(signal.iv.astype(float))
        & signal.iv.astype(float).gt(IV_THRESHOLD)
    )
    old_maturity = prior.maturity.IV_THRESHOLD
    old_seller = prior.seller_state.IV_THRESHOLD
    try:
        prior.maturity.IV_THRESHOLD = IV_THRESHOLD
        prior.seller_state.IV_THRESHOLD = IV_THRESHOLD
        signal = prior.seller_state.signal_variant(
            signal, prepared["current_combined"], scope, "instant",
            prior.seller_state.momentum_permission(),
        )
    finally:
        prior.maturity.IV_THRESHOLD = old_maturity
        prior.seller_state.IV_THRESHOLD = old_seller
    state = prepared["current_combined"]
    state = state[state.layer.eq(scope)].set_index("execution_date")
    signal["momentum_120"] = signal.execution_date.map(state.momentum_120).astype(float)
    if signal.momentum_120.isna().any():
        raise RuntimeError(f"Missing MOM120 audit field for {scope}")
    return signal


def run_scope(scope: str, prepared: dict[str, object], futures: pd.DataFrame,
              weights: pd.Series, option_market: pd.DataFrame, model_market: pd.DataFrame,
              real_short, model_short, model_profit, real_profit):
    prior = base.prior
    active = prepared["active"]
    signal = build_signal(prepared)
    runner = real_short if scope == "real" else model_short
    isolated, short_events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), None, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    core_masked = prior.router_base.mask_schedule(
        prepared["core_schedule"], scope, routed.loc[routed.state.eq("ic"), "date"]
    )
    sizing = quantity.cycle_sizing(scope, active, model_market, cycles)
    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []
    for profit_line, multiple in PROFIT_LINES:
        label0 = f"{scope}_m1_iv300_nomom_hold_coreprofit_{profit_line}"
        if scope == "real":
            core_put, core_trades = real_profit(
                prepared["qic"], core_masked, prepared["qframes"], option_market,
                label0, prepared["put_roll_dates"], open_exit_dates=route_dates,
                profit_multiple=multiple,
            )
        else:
            core_put, core_trades = model_profit(
                prepared["qic"], core_masked, option_market, label0,
                prepared["put_roll_dates"], open_exit_dates=route_dates,
                profit_multiple=multiple,
            )
        core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
        core_put = prior.maturity.costed_core(core_put, base.PUT_COST_MULTIPLIER)
        total_put = prior.combine_puts(core_put, prepared["mom_put"])
        profit_events = int(core_trades.action.eq("close_profit_restrike").sum())
        trade_parts.append(core_trades.assign(
            scope=scope, sleeve="core", profit_line=profit_line,
            candidate=label0,
        ))
        for mode in MODES:
            candidate = f"{label0}_{mode}"
            daily = quantity.compose_sized(
                active, routed, weights, prepared["grid"], total_put,
                candidate, sizing, mode,
            )
            daily["scope"] = scope
            daily["profit_line"] = profit_line
            daily["variant"] = candidate.removeprefix(scope + "_")
            daily_parts.append(daily)
            sized = sizing[sizing.quantity_mode.eq(mode)]
            audit_rows.append({
                "candidate": candidate,
                "scope": scope,
                "profit_line": profit_line,
                "quantity_mode": mode,
                "cycles": len(cycles),
                "short_put_days": int(routed.state.eq("short_put").sum()),
                "short_put_share": float(routed.state.eq("short_put").mean()),
                "route_switches": len(route_dates),
                "core_profit_restrikes": profit_events,
                "quantity_scale_min": float(sized.quantity_scale.min()),
                "quantity_scale_median": float(sized.quantity_scale.median()),
                "quantity_scale_max": float(sized.quantity_scale.max()),
                "min_cash_weight": float(daily.cash_weight.min()),
                "negative_cash_days": int(daily.cash_weight.lt(-1e-12).sum()),
                "capital_feasible": bool(daily.cash_weight.ge(-1e-12).all()),
                "short_ledger_error": float(short_audit["ledger_max_abs_error"]),
            })
    trade_parts.append(prepared["mom_trades"].assign(
        scope=scope, sleeve="momentum", profit_line="shared", candidate=f"{scope}_shared_momentum"
    ))
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    return (
        pd.concat(daily_parts, ignore_index=True, sort=False), trades,
        signal.assign(scope=scope), short_events.assign(scope=scope),
        cycles.assign(scope=scope), sizing, pd.DataFrame(audit_rows),
    )


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-9 run")

    prior = base.prior
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, model_market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, _, _, expiries, _ = prior.router_base.short.real_inputs()
    active_real = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    corrected_real = layer3.corrected_real_signal_for_maturity(
        active_real, frames, option_market, expiries, "m1"
    )
    prepared = {
        scope: base.prepare_scope(
            scope, path, futures, weights, selected, grid, frames, option_market,
            model_market, corrected_real,
        )
        for scope in ("real", "model")
    }
    prepared["model"]["base_signal"] = prior.maturity.maturity_model_signals(
        prepared["model"]["active"], model_market, "m1"
    )
    results = {
        scope: run_scope(
            scope, prepared[scope], futures, weights, option_market, model_market,
            real_short, model_short, model_profit, real_profit,
        )
        for scope in ("real", "model")
    }
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    short_events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    sizing = pd.concat([results[x][5] for x in ("real", "model")], ignore_index=True)
    exposure = pd.concat([results[x][6] for x in ("real", "model")], ignore_index=True)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()

    layer8_daily = pd.read_csv(LAYER8 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, dict[str, float]] = {}
    for scope in ("real", "model"):
        parity[scope] = {}
        for profit_line in ("none", "profit3x"):
            got = daily[daily.candidate.eq(
                f"{scope}_m1_iv300_nomom_hold_coreprofit_{profit_line}_q1_notional"
            )].sort_values("date")
            old = layer8_daily[layer8_daily.candidate.eq(
                f"{scope}_m1_iv300_nomom_val_instant_hold_coreprofit_{profit_line}"
            )].sort_values("date")
            if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
                raise RuntimeError(f"{scope} {profit_line} q1 layer-8 date parity failed")
            error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"{scope} {profit_line} q1 layer-8 return parity failed: {error}")
            parity[scope][profit_line] = error

    comparison_rows: list[dict[str, object]] = []
    for scope in ("real", "model"):
        for profit_line, _ in PROFIT_LINES:
            baseline = full[full.candidate.eq(
                f"{scope}_m1_iv300_nomom_hold_coreprofit_{profit_line}_q1_notional"
            )].iloc[0]
            for mode in MODES:
                candidate = full[full.candidate.eq(
                    f"{scope}_m1_iv300_nomom_hold_coreprofit_{profit_line}_{mode}"
                )].iloc[0]
                audit = exposure[exposure.candidate.eq(candidate.candidate)].iloc[0]
                comparison_rows.append({
                    "scope": scope,
                    "profit_line": profit_line,
                    "quantity_mode": mode,
                    "ann_return": candidate.ann_return,
                    "ann_return_delta_vs_q1": candidate.ann_return - baseline.ann_return,
                    "sharpe": candidate.sharpe_repo,
                    "sharpe_delta_vs_q1": candidate.sharpe_repo - baseline.sharpe_repo,
                    "max_dd": candidate.max_dd,
                    "max_dd_abs_worsening_vs_q1": abs(candidate.max_dd) - abs(baseline.max_dd),
                    "min_cash_weight": audit.min_cash_weight,
                    "negative_cash_days": int(audit.negative_cash_days),
                    "capital_feasible": bool(audit.capital_feasible),
                    "core_profit_restrikes": int(audit.core_profit_restrikes),
                    "quantity_scale_median": audit.quantity_scale_median,
                    "quantity_scale_max": audit.quantity_scale_max,
                    "short_put_days": int(audit.short_put_days),
                })
    comparison = pd.DataFrame(comparison_rows)
    q05 = comparison[comparison.quantity_mode.eq("q_delta05")]
    gate_rows = []
    for row in q05.itertuples(index=False):
        gate_rows.append({
            "scope": row.scope, "profit_line": row.profit_line,
            "cagr_diff_pp": 100.0 * row.ann_return_delta_vs_q1,
            "sharpe_diff": row.sharpe_delta_vs_q1,
            "mdd_worse_pp": 100.0 * max(0.0, row.max_dd_abs_worsening_vs_q1),
            "min_cash_weight": row.min_cash_weight,
            "negative_cash_days": row.negative_cash_days,
            "cagr_gate": bool(row.ann_return_delta_vs_q1 >= -1e-12),
            "sharpe_gate": bool(row.sharpe_delta_vs_q1 >= -0.05 - 1e-12),
            "drawdown_gate": bool(row.max_dd_abs_worsening_vs_q1 <= 0.01 + 1e-12),
            "capital_gate": bool(row.capital_feasible),
        })
    promote_q05 = all(
        all(row[key] for key in ("cagr_gate", "sharpe_gate", "drawdown_gate", "capital_gate"))
        for row in gate_rows
    )
    decision_checks = {
        "q_delta05_layers": gate_rows,
        "q_delta05_cross_scope_and_profit_line_pass": promote_q05,
        "q_delta10_is_stress_only": True,
    }
    decision = (
        "retain_q_delta05_as_research_candidate_awaiting_user_confirmation"
        if promote_q05 else
        "reject_delta_scaled_quantity_keep_q1_notional_awaiting_user_confirmation"
    )
    stability = (
        "q_delta05_cross_layer_gate_passed"
        if promote_q05 else "delta_scaled_quantity_failed_cross_layer_risk_or_capital_gate"
    )

    annual = (
        daily.assign(year=daily.date.dt.year)
        .groupby(["candidate", "scope", "quantity_mode", "year"], as_index=False)
        .agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    )
    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    short_events.to_csv(out / "short_put_events.csv", index=False)
    cycles.to_csv(out / "short_put_cycles.csv", index=False)
    sizing.to_csv(out / "quantity_sizing.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(RUN / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "quantity_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(
        short_source + "\n\n" + profit_source, encoding="utf-8"
    )

    real_q1 = sizing[(sizing.scope.eq("real")) & sizing.quantity_mode.eq("q1_notional")]
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "short_put_quantity_q1_notional_vs_entry_delta05_and_delta10_stress_with_core_profit_none_and_3x_lines",
        "baseline": {"quantity_mode": "q1_notional", "core_profit_primary": "none", "core_profit_comparison": "profit3x", "layer8_parity_max_abs": parity},
        "candidate_grid": [{"profit_line": line, "quantity_mode": mode, "target_delta_per_1_ic": target} for line, _ in PROFIT_LINES for mode, target in MODES.items()],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit seller IV and previous-close listed Delta"},
        "delta_timing": {"real": "previous-close listed chain Delta", "model": "previous-close Black-Scholes Delta", "quantity_reset": "initial cycle entry only; no early roll"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_buffer_per_1x": 0.30, "fixed_core_reserve": "15% times cycle quantity scale", "cash_annual": 0.03},
        "fixed_path": "absolute IV>30%; no seller MOM120; M+1 95%; instant valuation; naked; hold to expiry; core profit none primary plus 3x comparison line",
        "user_continuation_boundary": "Preserve layer-8 profit3x as a downstream comparison line; do not reinterpret it as promoted and do not carry failed profit2x.",
        "real_entry_delta": {"count": len(real_q1), "min": float(real_q1.decision_known_entry_abs_delta.min()), "median": float(real_q1.decision_known_entry_abs_delta.median()), "max": float(real_q1.decision_known_entry_abs_delta.max())},
        "decision_checks": decision_checks,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "quantity_engine": sha256(ROOT / "research_ic_v13_short95_quantity_delta_scan_v1.py"), "layer8_daily": sha256(LAYER8 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "events": str(out / "short_put_events.csv"), "cycles": str(out / "short_put_cycles.csv"), "sizing": str(out / "quantity_sizing.csv"), "comparison": str(RUN / "quantity_comparison.csv"), "annual": str(RUN / "annual_attribution.csv")},
        "warnings": ["Research only; no production, email, ledger, registry or order change.", "Real listed history is shorter than five years.", "Model option history and Delta are theoretical proxy.", "Delta-scaled quantities are continuous and ignore integer account sizing.", "Negative-cash diagnostics omit forced liquidation, borrowing cost and dynamic option margin.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第九层卖 Put 张数与入场 Delta", "",
        "## Run Metadata", "", "研究专用；完成后等待用户确认；生产、日报、账本、登记表和交易接口未修改。", "",
        "## Research Question", "", "固定前八层最终路径，在核心Put不提前兑现主线与3倍提前兑现比较线上，分别比较等名义q1、初始Delta 0.5及初始Delta 1.0压力档。", "",
        "## User Continuation Boundary", "", "按用户确认保留第八层3倍提前兑现作为向后比较线；不回写第八层冻结结果，不把3倍解释为已晋级，也不携带失败的2倍。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；两条q1与第八层对应路径逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "510500 Put单边5bp；IC单边1bp；按数量倍数保留期货缓冲；Delta仅用入场前一收盘可知值。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus q1", "", comparison.to_markdown(index=False, floatfmt=".6f"), "",
        "## Real Entry Sizing", "", real_q1.to_markdown(index=False, floatfmt=".6f"), "",
        "## Decision Gates", "", "```json", json.dumps(decision_checks, ensure_ascii=False, indent=2), "```", "",
        "## Stability Classification", "", f"`{stability}`。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第十层。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(json.dumps(decision_checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
