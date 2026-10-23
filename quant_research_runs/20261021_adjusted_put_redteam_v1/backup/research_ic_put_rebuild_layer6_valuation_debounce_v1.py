"""Rebuild IC short-Put research, layer 6: valuation-permission debounce."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_short95_unified_valuation_debounce_v1 as debounce
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer6_iv30_no_mom_m1_short95_hold_naked_absolute_iv_router_"
    "seller_only_and_unified_valuation_debounce_instant_confirm2_confirm3"
)
LAYER5 = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer5_iv30_no_mom_m1_short95_hold_naked_router_"
    "causal_relative_iv_replace_and_overlay_w126_w252_w504_q70_q80_q90"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer6_valuation_debounce_v1_spec.md"
VARIANTS = (
    "instant", "sell_confirm2", "sell_confirm3",
    "unified_instant", "unified_confirm2", "unified_confirm3",
)
IV_THRESHOLD = 0.30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_variant(
    prepared: dict[str, object], variant: str, futures: pd.DataFrame,
    weights: pd.Series, option_market: pd.DataFrame, real_short, model_short,
    model_profit, real_profit, mom_permission: pd.Series,
):
    prior = base.prior
    scope = str(prepared["scope"])
    active = prepared["active"]
    signal_base = prepared["base_signal"].copy()
    signal_base["route"] = (
        signal_base.admission.astype(bool)
        & signal_base.execution_open_valid.astype(bool)
        & np.isfinite(signal_base.iv.astype(float))
        & signal_base.iv.astype(float).gt(IV_THRESHOLD)
    )
    old_debounce_threshold = debounce.IV_THRESHOLD
    try:
        debounce.IV_THRESHOLD = IV_THRESHOLD
        signal = debounce.signal_variant(
            signal_base, prepared["current_combined"], scope, variant, mom_permission
        )
    finally:
        debounce.IV_THRESHOLD = old_debounce_threshold

    runner = real_short if scope == "real" else model_short
    isolated, short_events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), None, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    if variant in ("instant", "sell_confirm2", "sell_confirm3"):
        candidate_schedule = prepared["core_schedule"].copy()
    else:
        candidate_schedule = debounce.schedule_variant(prepared["current_combined"], variant)
    core_masked = prior.router_base.mask_schedule(
        candidate_schedule, scope, routed.loc[routed.state.eq("ic"), "date"]
    )
    candidate = f"{scope}_m1_iv300_nomom_val_{variant}"
    if scope == "real":
        core_put, core_trades = real_profit(
            prepared["qic"], core_masked, prepared["qframes"], option_market,
            candidate, prepared["put_roll_dates"], open_exit_dates=route_dates,
            profit_multiple=3.0,
        )
    else:
        core_put, core_trades = model_profit(
            prepared["qic"], core_masked, option_market, candidate,
            prepared["put_roll_dates"], open_exit_dates=route_dates,
            profit_multiple=3.0,
        )
    core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
    core_put = prior.maturity.costed_core(core_put, base.PUT_COST_MULTIPLIER)
    total_put = prior.combine_puts(core_put, prepared["mom_put"])
    daily = prior.compose(active, routed, weights, prepared["grid"], total_put, candidate)
    daily["scope"] = scope
    daily["variant"] = f"m1_iv300_nomom_val_{variant}"
    trades = pd.concat([
        core_trades.assign(scope=scope, sleeve="core", candidate=candidate),
        prepared["mom_trades"].assign(scope=scope, sleeve="momentum", candidate=candidate),
    ], ignore_index=True, sort=False)
    signal = signal.assign(scope=scope, candidate=candidate)
    cycles = cycles.assign(scope=scope, candidate=candidate) if len(cycles) else pd.DataFrame()
    short_events = short_events.assign(scope=scope, candidate=candidate) if len(short_events) else pd.DataFrame()

    raw_state = prepared["current_combined"][
        prepared["current_combined"].layer.eq(scope)
    ].sort_values("execution_date")
    candidate_state = candidate_schedule[
        candidate_schedule.layer.eq(scope)
    ].sort_values("execution_date")
    simultaneous = set(core_trades.loc[
        core_trades.action.eq("route_open_exit"), "actual_execution_date"
    ])
    if simultaneous - route_dates:
        raise RuntimeError(f"{candidate} core Put route exit mismatch")
    audit = {
        "candidate": candidate,
        "scope": scope,
        "valuation_variant": variant,
        "eligible_days": int(signal.route.astype(bool).sum()),
        "cycles": int(len(cycles)),
        "closed_cycles": int(cycles.closed.fillna(False).sum()) if len(cycles) else 0,
        "short_put_days": int(routed.state.eq("short_put").sum()),
        "short_put_share": float(routed.state.eq("short_put").mean()),
        "assignment_or_recovery_days": int(routed.state.isin(["pending_etf_to_ic", "ic_future"]).sum()),
        "early_rolls": int(cycles.early_rolls.fillna(0).sum()) if len(cycles) and "early_rolls" in cycles else 0,
        "valuation_permission_transitions": debounce.transitions(signal.valuation_permission.astype(bool)),
        "raw_valuation_tier_transitions": debounce.transitions(raw_state.valuation_tier_new.astype(int)),
        "debounced_valuation_tier_transitions": debounce.transitions(candidate_state.valuation_tier_new.astype(int)),
        "target_delta_transitions_pre_route": debounce.transitions(candidate_state.target_delta.astype(float)),
        "target_delta_transitions_after_route_mask": debounce.transitions(
            core_masked[core_masked.layer.eq(scope)].sort_values("execution_date").target_delta.astype(float)
        ),
        "core_put_trade_events": int(len(core_trades)),
        "core_put_cost_rate_sum_5bp": float(core_put.put_cost_rate.sum()),
        "short_put_option_transaction_sides": int(debounce.option_side_count(short_events)),
        "total_put_event_proxy": int(len(core_trades) + debounce.option_side_count(short_events)),
        "route_switches": int(len(route_dates)),
        "core_put_simultaneous_exits": int(len(simultaneous)),
        "routes_without_active_core_put": int(len(route_dates - simultaneous)),
        "min_cash_weight": float(daily.cash_weight.min()),
        "short_ledger_error": float(short_audit["ledger_max_abs_error"]),
    }
    return daily, trades, signal, cycles, short_events, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-6 run")

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
    mom_permission = debounce.momentum_permission()

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    event_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    for variant in VARIANTS:
        for scope in ("real", "model"):
            d, t, s, c, e, a = run_variant(
                prepared[scope], variant, futures, weights, option_market,
                real_short, model_short, model_profit, real_profit, mom_permission,
            )
            daily_parts.append(d); trade_parts.append(t); signal_parts.append(s)
            if len(c): cycle_parts.append(c)
            if len(e): event_parts.append(e)
            audits.append(a)

    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    signals = pd.concat(signal_parts, ignore_index=True, sort=False)
    cycles = pd.concat(cycle_parts, ignore_index=True, sort=False)
    events = pd.concat(event_parts, ignore_index=True, sort=False) if event_parts else pd.DataFrame()
    exposure = pd.DataFrame(audits)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    params = exposure.set_index("candidate")[["valuation_variant"]]
    for table in (summary, wide):
        table["valuation_variant"] = table.candidate.map(params.valuation_variant)

    cycles["cycle_pnl"] = cycles.realized_pnl.fillna(0.0) + cycles.open_cycle_pnl.fillna(0.0)
    concentration = (cycles.groupby(["candidate", "scope"], as_index=False)
                     .agg(cycles=("entry_date", "count"),
                          closed_cycles=("closed", "sum"),
                          positive_cycles=("cycle_pnl", lambda x: int(x.gt(0).sum())),
                          negative_cycles=("cycle_pnl", lambda x: int(x.lt(0).sum())),
                          worst_cycle_pnl=("cycle_pnl", "min"),
                          cycle_pnl_total=("cycle_pnl", "sum")))

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    for variant in VARIANTS:
        for scope in ("real", "model"):
            baseline = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant")].iloc[0]
            row = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_val_{variant}")].iloc[0]
            audit = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "valuation_variant": variant,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_instant": row.ann_return - baseline.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_instant": row.sharpe_repo - baseline.sharpe_repo,
                "max_dd": row.max_dd,
                "max_dd_delta_vs_instant": row.max_dd - baseline.max_dd,
                "cycles": int(audit.cycles),
                "closed_cycles": int(audit.closed_cycles),
                "permission_transitions": int(audit.valuation_permission_transitions),
                "valuation_tier_transitions": int(audit.debounced_valuation_tier_transitions),
                "core_put_trade_events": int(audit.core_put_trade_events),
                "total_put_event_proxy": int(audit.total_put_event_proxy),
                "core_put_cost_rate_sum_5bp": audit.core_put_cost_rate_sum_5bp,
            })
    paired = pd.DataFrame(paired_rows)

    checks: dict[str, object] = {"seller_only": {}, "unified": {}}
    for variant in ("sell_confirm2", "sell_confirm3"):
        layers = []
        reductions: dict[str, float] = {}
        changed = False
        for scope in ("real", "model"):
            baseline = paired[(paired.scope.eq(scope)) & paired.valuation_variant.eq("instant")].iloc[0]
            candidate = paired[(paired.scope.eq(scope)) & paired.valuation_variant.eq(variant)].iloc[0]
            cagr_diff_pp = 100.0 * (candidate.ann_return - baseline.ann_return)
            sharpe_diff = candidate.sharpe - baseline.sharpe
            mdd_worse_pp = 100.0 * max(0.0, abs(candidate.max_dd) - abs(baseline.max_dd))
            reductions[scope] = 1.0 - candidate.permission_transitions / max(baseline.permission_transitions, 1)
            changed |= bool(candidate.cycles != baseline.cycles)
            layers.append({
                "scope": scope, "cagr_diff_pp": float(cagr_diff_pp),
                "sharpe_diff": float(sharpe_diff), "mdd_worse_pp": float(mdd_worse_pp),
                "cycles": int(candidate.cycles),
                "cagr_gate": bool(cagr_diff_pp >= -0.50),
                "sharpe_gate": bool(sharpe_diff >= -0.02),
                "drawdown_gate": bool(mdd_worse_pp <= 0.50),
            })
        passed = all(x["cagr_gate"] and x["sharpe_gate"] and x["drawdown_gate"] for x in layers) and all(v >= 0.15 for v in reductions.values())
        checks["seller_only"][variant] = {
            "layers": layers, "permission_transition_reduction": reductions,
            "actual_cycles_changed": changed, "individual_gate_pass": passed,
        }

    for variant in ("unified_confirm2", "unified_confirm3"):
        layers = []
        reductions: dict[str, float] = {}
        transactions: dict[str, object] = {}
        for scope in ("real", "model"):
            baseline = paired[(paired.scope.eq(scope)) & paired.valuation_variant.eq("instant")].iloc[0]
            candidate = paired[(paired.scope.eq(scope)) & paired.valuation_variant.eq(variant)].iloc[0]
            cagr_diff_pp = 100.0 * (candidate.ann_return - baseline.ann_return)
            sharpe_diff = candidate.sharpe - baseline.sharpe
            mdd_worse_pp = 100.0 * max(0.0, abs(candidate.max_dd) - abs(baseline.max_dd))
            raw = exposure[exposure.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant")].iloc[0]
            candidate_audit = exposure[exposure.candidate.eq(f"{scope}_m1_iv300_nomom_val_{variant}")].iloc[0]
            reductions[scope] = 1.0 - candidate_audit.debounced_valuation_tier_transitions / max(raw.raw_valuation_tier_transitions, 1)
            transactions[scope] = {
                "core_event_reduction": float(1.0 - candidate.core_put_trade_events / max(baseline.core_put_trade_events, 1)),
                "total_event_proxy_reduction": float(1.0 - candidate.total_put_event_proxy / max(baseline.total_put_event_proxy, 1)),
                "core_cost_reduction": float(baseline.core_put_cost_rate_sum_5bp - candidate.core_put_cost_rate_sum_5bp),
            }
            layers.append({
                "scope": scope, "cagr_diff_pp": float(cagr_diff_pp),
                "sharpe_diff": float(sharpe_diff), "mdd_worse_pp": float(mdd_worse_pp),
                "cycles": int(candidate.cycles),
                "cagr_gate": bool(cagr_diff_pp >= -0.50),
                "sharpe_gate": bool(sharpe_diff >= -0.02),
                "drawdown_gate": bool(mdd_worse_pp <= 0.50),
            })
        real_tx = transactions["real"]
        execution_gate = bool(
            (real_tx["core_event_reduction"] >= 0.20 or real_tx["total_event_proxy_reduction"] >= 0.20)
            and real_tx["core_cost_reduction"] > 0
        )
        passed = all(x["cagr_gate"] and x["sharpe_gate"] and x["drawdown_gate"] for x in layers) and all(v >= 0.20 for v in reductions.values()) and execution_gate
        checks["unified"][variant] = {
            "layers": layers, "valuation_tier_transition_reduction": reductions,
            "transaction_reduction": transactions, "real_execution_gate": execution_gate,
            "individual_gate_pass": passed,
        }
    checks["seller_direction_consistent"] = all(
        checks["seller_only"][v]["individual_gate_pass"] for v in ("sell_confirm2", "sell_confirm3")
    )
    checks["unified_direction_consistent"] = all(
        checks["unified"][v]["individual_gate_pass"] for v in ("unified_confirm2", "unified_confirm3")
    )

    layer5_daily = pd.read_csv(LAYER5 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant")].sort_values("date")
        old = layer5_daily[layer5_daily.candidate.eq(f"{scope}_m1_iv300_nomom_abs30")].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"{scope} instant layer-5 date parity failed")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"{scope} instant layer-5 return parity failed: {error}")
        parity[scope] = error

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False)
    events.to_csv(out / "events.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(RUN / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "valuation_debounce_paired_vs_instant.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer6_complete_awaiting_user_confirmation"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "seller_only_and_unified_valuation_debounce_at_corrected_abs_iv300_no_mom120_m1_hold_naked",
        "baseline": {"variant": "instant", "layer5_parity_max_abs": parity},
        "candidate_grid": [{"variant": variant} for variant in VARIANTS],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit 95% M+1 IV"},
        "cost_model": {"each_510500_put_leg_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03, "core_put_and_short_put_recomputed": True},
        "execution": "T close causal valuation state; T+1 open; hold short Put to expiry; risk increase immediate and recovery optionally confirmed",
        "excluded_layers": ["premium_decay_early_roll", "relative_iv", "seller_mom120", "maturity_change", "catastrophe_put"],
        "decision_checks": checks,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "layer5_daily": sha256(LAYER5 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "events": str(out / "events.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "valuation_debounce_paired_vs_instant.csv"), "concentration": str(RUN / "cycle_concentration.csv")},
        "warnings": ["Research only; no production or ledger change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "5bp is a notional one-way friction assumption, not an option-premium percentage.", "No bid-ask, impact, capacity, tax, dynamic margin or integer sizing.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第六层估值许可防抖", "",
        "## Run Metadata", "", "研究专用；第六层完成后等待用户确认；生产、日报、账本和交易接口未修改。", "",
        "## Research Question", "", "固定修正后IV>30%、不加MOM120、M+1、95% Put、q1、持有到期、无灾难保护和相对IV；比较即时许可、仅卖方2/3日确认、统一估值即时及2/3日确认。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；即时许可与第五层绝对IV30逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "每条510500 Put单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘因果状态、T+1开盘；卖Put持有到期。", "",
        "## Runtime Override Plan", "", "只改变估值许可的恢复确认；风险升档即时；重新执行核心买Put与卖Put成本；不改生产源码。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Output Files", "", "完整窗口、逐日路径、估值状态切换、交易事件、周期集中度及相对即时许可的配对差见本目录CSV。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus Instant", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Decision Gates", "", "```json", json.dumps(checks, ensure_ascii=False, indent=2), "```", "",
        "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。", "",
        "## Stability Classification", "", f"`{stability}`，待结果解释后在最终化时更新。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第七层。", "",
        "## User-Facing Summary", "", "本层只归因估值许可防抖，不推导后续机制。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
