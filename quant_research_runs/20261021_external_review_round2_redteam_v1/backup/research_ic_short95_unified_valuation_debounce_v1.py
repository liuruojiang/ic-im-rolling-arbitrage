"""IC short-Put admission and unified long/short Put valuation debounce scan."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_coreput_highiv_short95_router_v1 as base
import research_ic_short95_maturity_scan_v1 as maturity


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_m1_short95_unified_valuation_debounce"
SPEC = ROOT / "docs" / "ic_short95_unified_valuation_debounce_v1_spec.md"
PRIOR = ROOT / "quant_param_scan_runs" / "20260916_ic_im_ic_v1_3_m1_short95_relative_iv_window_percentile"
VARIANTS = ("instant", "sell_confirm2", "sell_confirm3", "unified_instant", "unified_confirm2", "unified_confirm3")
DECAY = 0.60
IV_THRESHOLD = 0.375
OPTION_ONE_WAY = 0.0005


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def debounce_eligible(raw_tier: pd.Series, days: int) -> tuple[pd.Series, pd.Series]:
    active = False
    streak = 0
    out, streaks = [], []
    for value in raw_tier:
        eligible = bool(pd.notna(value) and float(value) <= 1)
        if not eligible:
            active, streak = False, 0
        else:
            streak += 1
            active = active or streak >= days
        out.append(active); streaks.append(streak)
    return pd.Series(out, index=raw_tier.index, dtype=bool), pd.Series(streaks, index=raw_tier.index, dtype=int)


def debounce_tier(raw_tier: pd.Series, days: int) -> pd.Series:
    values = pd.to_numeric(raw_tier, errors="coerce").fillna(4).astype(int).to_numpy()
    state = int(values[0]); pending = None; count = 0; out = np.zeros(len(values), dtype=int)
    for i, value in enumerate(values):
        value = int(value)
        if value >= state:
            state, pending, count = value, None, 0
        else:
            if pending != value:
                pending, count = value, 1
            else:
                count += 1
            if count >= days:
                state, pending, count = value, None, 0
        out[i] = state
    return pd.Series(out, index=raw_tier.index, dtype=int)


def momentum_permission() -> pd.Series:
    ohlcv = pd.read_csv(base.short.native.CSI500_OHLCV_PATH, parse_dates=["date"])
    return (base.short.native.build_momentum_schedule(ohlcv).set_index("date")
            .momentum_execution_weight.gt(0).rename("momentum_permission"))


def schedule_variant(current: pd.DataFrame, variant: str) -> pd.DataFrame:
    if not variant.startswith("unified_") or variant == "unified_instant":
        return current.copy()
    days = 2 if variant.endswith("2") else 3
    parts = []
    for _, group in current.groupby("layer", sort=False):
        part = group.sort_values("execution_date").copy()
        raw = part.valuation_tier_new.astype(int)
        part["raw_valuation_tier"] = raw.to_numpy()
        part["valuation_tier_new"] = debounce_tier(raw, days).to_numpy()
        part["v2_target_delta"] = np.maximum(part.valuation_tier_new.astype(float) * 0.25,
                                              part.mom120_floor_delta.astype(float))
        parts.append(part)
    rebuilt = base.ic.build_schedule(pd.concat(parts, ignore_index=True), "combined_current")
    return rebuilt.sort_values(["layer", "execution_date"]).reset_index(drop=True)


def signal_variant(base_signal: pd.DataFrame, current: pd.DataFrame, scope: str,
                   variant: str, mom_permission: pd.Series) -> pd.DataFrame:
    out = base_signal.sort_values("execution_date").reset_index(drop=True).copy()
    state = current[current.layer.eq(scope)].sort_values("execution_date").set_index("execution_date")
    unified_raw = out.execution_date.map(state.valuation_tier_new).astype(float)
    if unified_raw.isna().any():
        raise RuntimeError(f"{scope} valuation tier missing")
    score = pd.read_csv(ROOT / "outputs" / "ic_fixed_valuation_unbounded_score_v6" /
                        "daily_unbounded_fixed_scores.csv.gz", parse_dates=["date"]).set_index("date").unbounded_median_knot
    seller_raw = out.execution_date.map(score).astype(float)
    if seller_raw.isna().any():
        raise RuntimeError(f"{scope} seller valuation score missing")
    if variant == "instant":
        debounced_tier = unified_raw.astype(int)
        permission = seller_raw.lt(1.95)
        streak = pd.Series(np.where(permission, 1, 0), index=out.index)
    elif variant.startswith("sell_confirm"):
        days = 2 if variant.endswith("2") else 3
        seller_tier = pd.Series(np.where(seller_raw.lt(1.95), 1, 2), index=out.index)
        permission, streak = debounce_eligible(seller_tier, days)
        debounced_tier = unified_raw.astype(int)
    elif variant == "unified_instant":
        debounced_tier = unified_raw.astype(int)
        permission = debounced_tier.le(1)
        streak = pd.Series(np.where(permission, 1, 0), index=out.index)
    else:
        days = 2 if variant.endswith("2") else 3
        debounced_tier = debounce_tier(unified_raw, days)
        permission = debounced_tier.le(1)
        streak = pd.Series(0, index=out.index, dtype=int)
    momentum = out.execution_date.map(mom_permission).fillna(False).astype(bool)
    out["raw_valuation_tier"] = unified_raw.astype(int)
    out["seller_raw_valuation_score"] = seller_raw
    out["debounced_valuation_tier"] = debounced_tier.astype(int)
    out["valuation_permission"] = permission.astype(bool)
    out["valuation_eligible_streak"] = streak.astype(int)
    out["momentum_permission"] = momentum
    out["valuation_debounce_variant"] = variant
    out["route"] = (permission.to_numpy(bool) & momentum.to_numpy(bool)
                    & out.execution_open_valid.to_numpy(bool)
                    & np.isfinite(out.iv.to_numpy(float)) & out.iv.to_numpy(float).__gt__(IV_THRESHOLD))
    if variant == "instant" and not out.route.equals(base_signal.sort_values("execution_date").reset_index(drop=True).route):
        mismatch = int(out.route.ne(base_signal.sort_values("execution_date").reset_index(drop=True).route).sum())
        raise RuntimeError(f"{scope} instant permission parity mismatch rows={mismatch}")
    return out


def option_side_count(events: pd.DataFrame) -> int:
    if events.empty:
        return 0
    action = events.action.fillna("").astype(str)
    entries = int(action.str.startswith(("sell_next_month_95_put_open", "sell_model_next_month_95_put_open")).sum())
    rolls = int(action.eq("early_roll_buyback_and_sell_next_open").sum())
    return entries + 2 * rolls


def transitions(series: pd.Series) -> int:
    return int(series.ne(series.shift()).sum() - 1) if len(series) else 0


def run_layer(scope, current, model_engine, real_engine, real_short, model_short, mom_permission):
    frames, _, market, _ = base.ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll = base.ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    real_active, _, real_chains, _, _, real_futures = base.short.real_inputs()
    model_active, model_market, model_futures = base.short.model_source.model_inputs()
    active, futures, marketx = (real_active, real_futures, None) if scope == "real" else (model_active, model_futures, model_market)
    pure = base.baseline(active, scope)
    always = base.mask_schedule(current, scope, pure.date)
    core, core_trades = (real_engine(frames["ic"], always, frames, market, f"{scope}_core", roll)
                         if scope == "real" else model_engine(frames["ic"], always, market, f"{scope}_core", roll))
    core = core[core.date.isin(pure.date)].reset_index(drop=True)
    protected = base.add_core(pure, maturity.costed_core(core, 5.0), f"{scope}_rolling_ic_current_core_put_5bp")
    base_signal = (maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
                   if scope == "real" else maturity.maturity_model_signals(active, marketx, "m1"))
    runner = real_short if scope == "real" else model_short
    reference = pd.read_csv(PRIOR / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parts = [pure, protected]; signals = []; cycles_all = []; events_all = []; trades_all = []
    audits = {"pure_parity": float(abs(pure.return_net - (active.ic_net_ret + .7 * base.CASH)).max()),
              "core_put_trade_events_without_short_router": len(core_trades)}
    for variant in VARIANTS:
        signal = signal_variant(base_signal, current, scope, variant, mom_permission)
        isolated, events, cycles, short_audit = runner(base.entry_series(signal), DECAY, "m1", OPTION_ONE_WAY)
        routed = base.stitched_router(scope, active, futures, isolated, signal)
        ic_dates = routed.loc[routed.state.eq("ic"), "date"]
        candidate_schedule = schedule_variant(current, variant)
        masked = base.mask_schedule(candidate_schedule, scope, ic_dates)
        exits = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
        p, ptrades = (real_engine(frames["ic"], masked, frames, market, f"{scope}_{variant}", roll, exits)
                      if scope == "real" else model_engine(frames["ic"], masked, market, f"{scope}_{variant}", roll, exits))
        p = p[p.date.isin(routed.date)].reset_index(drop=True)
        costed = maturity.costed_core(p, 5.0)
        label = f"{scope}_m1_iv375_decay60_val_{variant}_5bp"
        combined = base.add_core(routed, costed, label)
        parts.append(combined); signals.append(signal.assign(scope=scope, candidate=label))
        if len(cycles): cycles_all.append(cycles.assign(scope=scope, candidate=label))
        if len(events): events_all.append(events.assign(scope=scope, candidate=label))
        if len(ptrades): trades_all.append(ptrades.assign(scope=scope, candidate=label))
        simultaneous = set(ptrades.loc[ptrades.action.eq("route_open_exit"), "actual_execution_date"])
        if simultaneous - exits:
            raise RuntimeError(f"Core Put route exit mismatch {label}")
        parity = None
        if variant == "instant":
            ref_label = f"{scope}_m1_short95_decay60_abs375_5bp"
            ref = reference[reference.candidate.eq(ref_label)].sort_values("date").reset_index(drop=True)
            got = combined.sort_values("date").reset_index(drop=True)
            if not ref.date.equals(got.date): raise RuntimeError(f"{scope} prior dates mismatch")
            parity = float(np.max(np.abs(ref.return_net.to_numpy() - got.return_net.to_numpy())))
            if parity > 1e-12: raise RuntimeError(f"{scope} instant parity failed {parity}")
        raw_state = current[current.layer.eq(scope)].sort_values("execution_date")
        candidate_state = candidate_schedule[candidate_schedule.layer.eq(scope)].sort_values("execution_date")
        valuation_permission = signal.valuation_permission.astype(bool)
        short_sides = option_side_count(events)
        audits[label] = {**short_audit,
                         "valuation_permission_transitions": transitions(valuation_permission),
                         "raw_valuation_tier_transitions": transitions(raw_state.valuation_tier_new.astype(int)),
                         "debounced_valuation_tier_transitions": transitions(candidate_state.valuation_tier_new.astype(int)),
                         "target_delta_transitions_pre_route": transitions(candidate_state.target_delta.astype(float)),
                         "target_delta_transitions_after_route_mask": transitions(masked[masked.layer.eq(scope)].sort_values("execution_date").target_delta.astype(float)),
                         "core_put_trade_events": len(ptrades),
                         "core_put_cost_rate_sum_5bp": float(costed.put_cost_rate.sum()),
                         "short_put_option_transaction_sides": short_sides,
                         "total_put_event_proxy": len(ptrades) + short_sides,
                         "route_switches": len(exits), "closed_cycles": int(cycles.closed.fillna(False).sum()),
                         "core_put_simultaneous_exits": len(simultaneous),
                         "routes_without_active_core_put": len(exits - simultaneous),
                         "instant_prior_daily_parity_error": parity}
    return (pd.concat(parts, ignore_index=True), pd.concat(signals, ignore_index=True),
            pd.concat(cycles_all, ignore_index=True), pd.concat(events_all, ignore_index=True),
            pd.concat(trades_all, ignore_index=True), audits)


def metric_gates(full, audit, scope, variant):
    base_row = full[full.candidate.eq(f"{scope}_m1_iv375_decay60_val_instant_5bp")].iloc[0]
    row = full[full.candidate.eq(f"{scope}_m1_iv375_decay60_val_{variant}_5bp")].iloc[0]
    return {"scope": scope, "cagr_diff_pp": 100.0 * float(row.ann_return - base_row.ann_return),
            "sharpe_diff": float(row.sharpe_repo - base_row.sharpe_repo),
            "mdd_worse_pp": 100.0 * max(0.0, abs(float(row.max_dd)) - abs(float(base_row.max_dd))),
            "cagr_gate": float(row.ann_return - base_row.ann_return) >= -0.005,
            "sharpe_gate": float(row.sharpe_repo - base_row.sharpe_repo) >= -0.02,
            "drawdown_gate": abs(float(row.max_dd)) - abs(float(base_row.max_dd)) <= 0.005,
            "cycles": int(audit[scope][f"{scope}_m1_iv375_decay60_val_{variant}_5bp"]["cycles"])}


def classify(full: pd.DataFrame, audit: dict) -> tuple[str, str, dict]:
    checks = {"seller_only": {}, "unified": {}}
    for variant in ("sell_confirm2", "sell_confirm3"):
        layers = [metric_gates(full, audit, scope, variant) for scope in ("real", "model")]
        reductions = {}
        for scope in ("real", "model"):
            baseline = audit[scope][f"{scope}_m1_iv375_decay60_val_instant_5bp"]
            candidate = audit[scope][f"{scope}_m1_iv375_decay60_val_{variant}_5bp"]
            reductions[scope] = 1 - candidate["valuation_permission_transitions"] / max(baseline["valuation_permission_transitions"], 1)
        passed = all(x["cagr_gate"] and x["sharpe_gate"] and x["drawdown_gate"] for x in layers) and all(v >= .15 for v in reductions.values())
        checks["seller_only"][variant] = {"layers": layers, "permission_transition_reduction": reductions,
                                                    "actual_cycles_changed": any(x["cycles"] != metric_gates(full, audit, x["scope"], "instant")["cycles"] for x in layers),
                                                    "individual_gate_pass": passed}
    for variant in ("unified_confirm2", "unified_confirm3"):
        layers = [metric_gates(full, audit, scope, variant) for scope in ("real", "model")]
        reductions = {}; transactions = {}
        for scope in ("real", "model"):
            baseline = audit[scope][f"{scope}_m1_iv375_decay60_val_instant_5bp"]
            candidate = audit[scope][f"{scope}_m1_iv375_decay60_val_{variant}_5bp"]
            reductions[scope] = 1 - candidate["debounced_valuation_tier_transitions"] / max(baseline["raw_valuation_tier_transitions"], 1)
            transactions[scope] = {
                "core_event_reduction": 1 - candidate["core_put_trade_events"] / max(baseline["core_put_trade_events"], 1),
                "total_event_proxy_reduction": 1 - candidate["total_put_event_proxy"] / max(baseline["total_put_event_proxy"], 1),
                "core_cost_reduction": baseline["core_put_cost_rate_sum_5bp"] - candidate["core_put_cost_rate_sum_5bp"],
            }
        real_tx = transactions["real"]
        execution_gate = ((real_tx["core_event_reduction"] >= .20 or real_tx["total_event_proxy_reduction"] >= .20)
                          and real_tx["core_cost_reduction"] > 0)
        passed = (all(x["cagr_gate"] and x["sharpe_gate"] and x["drawdown_gate"] for x in layers)
                  and all(v >= .20 for v in reductions.values()) and execution_gate)
        checks["unified"][variant] = {"layers": layers, "valuation_tier_transition_reduction": reductions,
                                      "transaction_reduction": transactions, "real_execution_gate": execution_gate,
                                      "individual_gate_pass": passed}
    seller_consistent = all(checks["seller_only"][x]["individual_gate_pass"] for x in ("sell_confirm2", "sell_confirm3"))
    unified_consistent = all(checks["unified"][x]["individual_gate_pass"] for x in ("unified_confirm2", "unified_confirm3"))
    checks["seller_direction_consistent"] = seller_consistent
    checks["unified_direction_consistent"] = unified_consistent
    if unified_consistent:
        return "retain_unified_confirm2_for_research_no_production_change", "unified_debounce_cross_layer_costed_gate_passed", checks
    if seller_consistent:
        changed = any(checks["seller_only"][x]["actual_cycles_changed"] for x in ("sell_confirm2", "sell_confirm3"))
        decision = ("retain_seller_debounce_for_research_no_production_change" if changed
                    else "retain_seller_debounce_operational_only_performance_unidentified")
        return decision, "seller_only_debounce_passed_unified_failed", checks
    return "retain_instant_reject_valuation_debounce_grid_no_production_change", "valuation_debounce_no_consistent_candidate_passed", checks


def main():
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    current = base.current_schedule(); mom_permission = momentum_permission()
    model_engine, real_engine, core_source = base.patched_engines()
    real_short, model_short, short_source = maturity.patched_short_runners()
    results = {scope: run_layer(scope, current, model_engine, real_engine, real_short, model_short, mom_permission)
               for scope in ("real", "model")}
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    audit = {"real": results["real"][5], "model": results["model"][5]}
    summary, wide, unavailable = base.summarize(daily); full = summary[summary.segment.eq("full")].copy()
    decision, stability, decision_checks = classify(full, audit)
    cycle_diag = (cycles.groupby(["scope", "candidate"], as_index=False)
                  .agg(cycles=("entry_date", "count"), closed_cycles=("closed", "sum"),
                       assignments=("physical_assignment_date", lambda x: x.notna().sum()),
                       early_rolls=("early_rolls", "sum"), worst_cycle_pnl=("realized_pnl", "min")))
    model_early = current[(current.layer.eq("model")) & (current.execution_date.lt(pd.Timestamp("2015-10-19")))]
    early_audit = {"rows": len(model_early), "missing_tier_rows": int(model_early.valuation_tier_new.isna().sum()),
                   "tier_min": int(model_early.valuation_tier_new.min()), "tier_max": int(model_early.valuation_tier_new.max()),
                   "tier_0_1_rows": int(model_early.valuation_tier_new.le(1).sum()),
                   "interpretation": "early model valuation is explicit and not defaulted to tier 0"}
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signal_audit.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False); events.to_csv(out / "events.csv", index=False)
    trades.to_csv(out / "core_put_trades.csv.gz", index=False, compression="gzip")
    cycle_diag.to_csv(out / "cycle_diagnostics.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + core_source, encoding="utf-8")
    meta.update({
        "scan_type": "ic_m1_short95_seller_and_unified_valuation_debounce_v1",
        "baseline": {"candidate": "*_val_instant_5bp", "prior_run": str(PRIOR),
                     "daily_parity_max_abs_error": {scope: audit[scope][f"{scope}_m1_iv375_decay60_val_instant_5bp"]["instant_prior_daily_parity_error"] for scope in ("real", "model")}},
        "candidate_grid": [{"variant": x} for x in VARIANTS],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 actual 510500 Put/ETF and IC",
                          "model": "2015-04-16..2026-08-14 theoretical 510500 Put plus historical IC"},
        "cost_model": {"510500_put_one_way": OPTION_ONE_WAY, "put_round_trip": 2 * OPTION_ONE_WAY,
                       "ETF_and_IC_one_way": base.ONE_WAY, "risk_buffer": .30, "cash_annual": .03,
                       "core_put_cost_recomputed": True, "short_put_cost_recomputed": True},
        "early_valuation_audit": early_audit, "audit": audit, "decision_checks": decision_checks,
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signal_audit.csv.gz"),
                    "cycles": str(out / "cycles.csv"), "events": str(out / "events.csv"),
                    "core_put_trades": str(out / "core_put_trades.csv.gz"),
                    "cycle_diagnostics": str(out / "cycle_diagnostics.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "prior_meta": sha(PRIOR / "scan_meta.json")},
        "warnings": ["Research only; IC has no Call.", "Real listed option history is short.",
                     "Model 510500 Put is theoretical and not executable history.",
                     "5bp is a notional one-way friction assumption, not an option-premium percentage."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = ("# IC买Put与卖Put估值防抖扫描\n\n## Run Metadata\n\n研究专用；IC无Call；生产未修改。\n\n"
              "## Research Question\n\n分别测试仅卖Put准入防抖，以及买卖Put统一估值防抖。\n\n"
              "## Implementation Anchor\n\n固定M+1、IV37.5%、衰减60%、原IC执行动量许可和现行MOM120买Put防抖。\n\n"
              "## Data Snapshot\n\n真实2022-09-19至2026-08-14；理论2015-04-16至2026-08-14。\n\n"
              "## Cost and Execution Assumptions\n\n所有510500 Put单边5BP；ETF与IC单边1BP；T收盘、T+1开盘；30%缓冲、3%现金。\n\n"
              "## Commands\n\n见command_log.txt。\n\n## Output Files\n\n见scan_meta.json。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) +
              "\n\n## Cycle Diagnostics\n\n" + cycle_diag.to_markdown(index=False) +
              "\n\n## Early Valuation Audit\n\n```json\n" + json.dumps(early_audit, ensure_ascii=False, indent=2) +
              "\n```\n\n## Decision Gates\n\n```json\n" + json.dumps(decision_checks, ensure_ascii=False, indent=2) +
              "\n```\n\n## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision +
              "\n\n## User-Facing Summary\n\n本层完成后暂停，由用户确认是否进入核心买Put盈利兑现与重新建仓层。\n")
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(cycle_diag.to_string(index=False)); print(json.dumps(decision_checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
