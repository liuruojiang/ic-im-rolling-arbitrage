"""IC core Put profit realization/restrike, standalone and with current short-Put router."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_coreput_highiv_short95_router_v1 as base
import research_ic_short95_maturity_scan_v1 as maturity
import research_ic_short95_unified_valuation_debounce_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_core_put_profit_restrike_2x_3x"
SPEC = ROOT / "docs" / "ic_core_put_profit_restrike_v1_spec.md"
PRIOR = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_m1_short95_unified_valuation_debounce"
MULTIPLES = (None, 2.0, 3.0)
IV_THRESHOLD = 0.375
DECAY = 0.60
OPTION_ONE_WAY = 0.0005


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def patched_profit_engines():
    """Add causal T-close trigger/T+1-close restrike to the route-aware IC engines."""
    _, _, source = base.patched_engines()
    marker = "\ndef run_real_delta("
    if marker not in source:
        raise RuntimeError("Unable to split patched IC engines")
    model_src, real_tail = source.split(marker, 1)
    real_src = "def run_real_delta(" + real_tail

    model_src = model_src.replace(
        "roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(),\n)",
        "roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(), profit_multiple=None,\n)", 1)
    model_src = model_src.replace(
        "trades: list[dict[str, Any]] = []",
        "trades: list[dict[str, Any]] = []\n    entry_premium = np.nan\n    pending_profit_since = None", 1)
    model_src = model_src.replace(
        "monthly_request = bool(day in roll_dates and active is not None and latest_target > 0)",
        "monthly_request = bool(day in roll_dates and active is not None and latest_target > 0)\n        profit_request = bool(active is not None and pending_profit_since is not None and not monthly_request and not changed and not route_open_exit)", 1)
    model_src = model_src.replace(
        "action = \"close_buy\"",
        "action = \"close_buy\"\n            entry_premium = float(active.prior_mark)\n            pending_profit_since = None", 1)
    model_src = model_src.replace(
        "active = None\n            action = \"route_open_exit\" if route_open_exit else \"close_exit\"",
        "active = None\n            entry_premium = np.nan\n            pending_profit_since = None\n            action = \"route_open_exit\" if route_open_exit else \"close_exit\"", 1)
    model_src = model_src.replace(
        "active = new_position\n            action = \"close_roll_monthly\"",
        "active = new_position\n            entry_premium = float(active.prior_mark)\n            pending_profit_since = None\n            action = \"close_roll_monthly\"", 1)
    anchor = """        elif active is not None and changed:
            price, delta = model_price_and_delta(active, row)"""
    replacement = """        elif active is not None and profit_request:
            old_price, _ = model_price_and_delta(active, row)
            pnl += active.units * (old_price - active.prior_mark) / denominator
            new_position, entry_delta, target_error = open_position(row, latest_target)
            cost += (active.notional_fraction + new_position.notional_fraction) * PUT_SIDE_COST
            active = new_position
            entry_premium = float(active.prior_mark)
            pending_profit_since = None
            action = "close_profit_restrike"
        elif active is not None and changed:
            price, delta = model_price_and_delta(active, row)"""
    if anchor not in model_src:
        raise RuntimeError("Model profit branch hook changed")
    model_src = model_src.replace(anchor, replacement, 1)
    model_src = model_src.replace(
        "        if action:\n            trades.append(",
        """        if active is not None and profit_multiple is not None and action != "close_profit_restrike" and np.isfinite(entry_premium) and active.prior_mark >= entry_premium * float(profit_multiple):
            pending_profit_since = pending_profit_since or day

        if action:
            trades.append(""", 1)
    model_src = model_src.replace(
        "            active = None\n        mark_fraction = 0.0",
        "            active = None\n            entry_premium = np.nan\n            pending_profit_since = None\n        mark_fraction = 0.0", 1)

    real_src = real_src.replace(
        "roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(),\n)",
        "roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(), profit_multiple=None,\n)", 1)
    real_src = real_src.replace(
        "trades: list[dict[str, Any]] = []",
        "trades: list[dict[str, Any]] = []\n    entry_premium = np.nan\n    pending_profit_since = None", 1)
    real_src = real_src.replace(
        "                active = None\n                action = \"route_open_exit\" if route_open_exit else \"close_exit\"",
        "                active = None\n                entry_premium = np.nan\n                pending_profit_since = None\n                action = \"route_open_exit\" if route_open_exit else \"close_exit\"", 1)
    real_src = real_src.replace(
        "                active = new_position\n                action = \"close_roll_monthly\"",
        "                active = new_position\n                entry_premium = float(active.prior_mark)\n                pending_profit_since = None\n                action = \"close_roll_monthly\"", 1)
    real_src = real_src.replace(
        "                action = \"close_buy\"\n                pending_action_since = None",
        "                action = \"close_buy\"\n                entry_premium = float(active.prior_mark)\n                pending_profit_since = None\n                pending_action_since = None", 1)
    anchor = """        if not action and active is not None:
            mark, stale_days, carried = proxy.real_mark("""
    replacement = """        elif active is not None and pending_profit_since is not None:
            selected = select_contract(day, row)
            old_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if selected is not None and old_quote is not None and float(old_quote["close"]) > 0 and float(old_quote["volume"]) > 0:
                master, new_quote = selected
                new_position, entry_delta, target_error, delta_source = sized_position(day, row, master, new_quote, latest_target)
                pnl += active.qty * OPTION_MULTIPLIER * (float(old_quote["close"]) - active.prior_mark) / denominator
                cost += (active.notional_fraction + new_position.notional_fraction) * PUT_SIDE_COST
                active = new_position
                entry_premium = float(active.prior_mark)
                action = "close_profit_restrike"
                pending_profit_since = None

        if not action and active is not None:
            mark, stale_days, carried = proxy.real_mark("""
    if anchor not in real_src:
        raise RuntimeError("Real profit branch hook changed")
    real_src = real_src.replace(anchor, replacement, 1)
    real_src = real_src.replace(
        "        if action:\n            actual_request = request_date",
        """        if active is not None and profit_multiple is not None and action != "close_profit_restrike" and np.isfinite(entry_premium):
            trigger_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if trigger_quote is not None and float(trigger_quote["close"]) > 0 and float(trigger_quote["volume"]) > 0 and float(trigger_quote["close"]) >= entry_premium * float(profit_multiple):
                pending_profit_since = pending_profit_since or day

        if action:
            actual_request = pending_profit_since if action == "close_profit_restrike" and pending_profit_since is not None else request_date""", 1)
    # The branch clears pending_profit_since after execution, so preserve the causal request date separately.
    real_src = real_src.replace(
        "        elif active is not None and pending_profit_since is not None:\n            selected = select_contract(day, row)",
        "        elif active is not None and pending_profit_since is not None:\n            profit_request_date = pending_profit_since\n            selected = select_contract(day, row)", 1)
    real_src = real_src.replace(
        "            actual_request = pending_profit_since if action == \"close_profit_restrike\" and pending_profit_since is not None else request_date",
        "            actual_request = profit_request_date if action == \"close_profit_restrike\" else request_date", 1)
    real_src = real_src.replace(
        "            active = None\n            pending_action_since = day",
        "            active = None\n            entry_premium = np.nan\n            pending_profit_since = None\n            pending_action_since = day", 1)

    ns = dict(vars(base.ic.ic_put.v1.put_engine))
    exec(compile(model_src, str(Path(__file__)), "exec"), ns)
    exec(compile(real_src, str(Path(__file__)), "exec"), ns)
    return ns["run_model_delta"], ns["run_real_delta"], model_src + "\n\n" + real_src


def tag(multiple: float | None) -> str:
    return "baseline" if multiple is None else f"profit{int(multiple)}x"


def run_layer(scope, schedule, model_engine, real_engine, real_short, model_short, mom_permission):
    frames, _, market, _ = base.ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll = base.ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    real_active, _, real_chains, _, _, real_futures = base.short.real_inputs()
    model_active, model_market, model_futures = base.short.model_source.model_inputs()
    active, futures, marketx = (real_active, real_futures, None) if scope == "real" else (model_active, model_futures, model_market)
    pure = base.baseline(active, scope)
    always = base.mask_schedule(schedule, scope, pure.date)
    base_signal = (maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
                   if scope == "real" else maturity.maturity_model_signals(active, marketx, "m1"))
    signal = prior.signal_variant(base_signal, schedule, scope, "instant", mom_permission)
    runner = real_short if scope == "real" else model_short
    isolated, events, cycles, short_audit = runner(base.entry_series(signal), DECAY, "m1", OPTION_ONE_WAY)
    routed = base.stitched_router(scope, active, futures, isolated, signal)
    ic_dates = routed.loc[routed.state.eq("ic"), "date"]
    masked = base.mask_schedule(schedule, scope, ic_dates)
    exits = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])

    parts = [pure]
    trades_all = []
    audit = {"short_router": short_audit, "route_switches": len(exits), "profit": {}}
    prior_daily = pd.read_csv(PRIOR / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    for multiple in MULTIPLES:
        suffix = tag(multiple)
        standalone_label = f"{scope}_rolling_ic_coreput_{suffix}_5bp"
        joint_label = f"{scope}_m1_iv375_decay60_coreput_{suffix}_5bp"
        if scope == "real":
            standalone_put, standalone_trades = real_engine(frames["ic"], always, frames, market, standalone_label, roll, profit_multiple=multiple)
            joint_put, joint_trades = real_engine(frames["ic"], masked, frames, market, joint_label, roll, exits, multiple)
        else:
            standalone_put, standalone_trades = model_engine(frames["ic"], always, market, standalone_label, roll, profit_multiple=multiple)
            joint_put, joint_trades = model_engine(frames["ic"], masked, market, joint_label, roll, exits, multiple)
        standalone_put = standalone_put[standalone_put.date.isin(pure.date)].reset_index(drop=True)
        joint_put = joint_put[joint_put.date.isin(routed.date)].reset_index(drop=True)
        standalone_costed = maturity.costed_core(standalone_put, 5.0)
        joint_costed = maturity.costed_core(joint_put, 5.0)
        standalone = base.add_core(pure, standalone_costed, standalone_label)
        joint = base.add_core(routed, joint_costed, joint_label)
        parts.extend([standalone, joint])
        trades_all.extend([standalone_trades.assign(scope=scope, portfolio="standalone", candidate=standalone_label),
                           joint_trades.assign(scope=scope, portfolio="joint", candidate=joint_label)])
        for portfolio, label, trades, costed in (("standalone", standalone_label, standalone_trades, standalone_costed),
                                                  ("joint", joint_label, joint_trades, joint_costed)):
            profit_trades = trades[trades.action.eq("close_profit_restrike")]
            sim = set(trades.loc[trades.action.eq("route_open_exit"), "actual_execution_date"])
            if portfolio == "joint" and sim - exits:
                raise RuntimeError(f"Route exit mismatch: {label}")
            audit["profit"][label] = {
                "trade_events": len(trades),
                "profit_restrikes": len(profit_trades),
                "profit_restrike_dates": [str(pd.Timestamp(x).date()) for x in profit_trades.actual_execution_date],
                "route_open_exits": len(sim),
                "put_cost_rate_sum_5bp": float(costed.put_cost_rate.sum()),
            }
        if multiple is None:
            standalone_ref = prior_daily[prior_daily.candidate.eq(f"{scope}_rolling_ic_current_core_put_5bp")].sort_values("date").reset_index(drop=True)
            joint_ref = prior_daily[prior_daily.candidate.eq(f"{scope}_m1_iv375_decay60_val_instant_5bp")].sort_values("date").reset_index(drop=True)
            audit["standalone_baseline_parity"] = float(np.max(np.abs(standalone_ref.return_net.to_numpy() - standalone.sort_values("date").return_net.to_numpy())))
            audit["joint_baseline_parity"] = float(np.max(np.abs(joint_ref.return_net.to_numpy() - joint.sort_values("date").return_net.to_numpy())))
            if audit["standalone_baseline_parity"] > 1e-12 or audit["joint_baseline_parity"] > 1e-12:
                raise RuntimeError(f"{scope} baseline parity failed")
    return pd.concat(parts, ignore_index=True), pd.concat(trades_all, ignore_index=True), signal, cycles, events, audit


def classify(full: pd.DataFrame, audit: dict):
    checks = {}
    for multiple in (2, 3):
        layers = []
        for scope in ("real", "model"):
            for portfolio in ("standalone", "joint"):
                prefix = "rolling_ic_coreput" if portfolio == "standalone" else "m1_iv375_decay60_coreput"
                baseline = full[full.candidate.eq(f"{scope}_{prefix}_baseline_5bp")].iloc[0]
                candidate = full[full.candidate.eq(f"{scope}_{prefix}_profit{multiple}x_5bp")].iloc[0]
                events = audit[scope]["profit"][f"{scope}_{prefix}_profit{multiple}x_5bp"]["profit_restrikes"]
                layers.append({"scope": scope, "portfolio": portfolio,
                               "cagr_diff_pp": 100 * float(candidate.ann_return - baseline.ann_return),
                               "sharpe_diff": float(candidate.sharpe_repo - baseline.sharpe_repo),
                               "mdd_diff_pp": 100 * float(abs(candidate.max_dd) - abs(baseline.max_dd)),
                               "profit_restrikes": events,
                               "metric_gate": bool(candidate.ann_return >= baseline.ann_return and candidate.sharpe_repo >= baseline.sharpe_repo and abs(candidate.max_dd) - abs(baseline.max_dd) <= .005)})
        real_events = min(x["profit_restrikes"] for x in layers if x["scope"] == "real")
        checks[f"profit{multiple}x"] = {"layers": layers, "real_event_floor": real_events,
                                        "individual_gate_pass": all(x["metric_gate"] for x in layers) and real_events >= 2}
    direction_consistent = all(checks[x]["individual_gate_pass"] for x in ("profit2x", "profit3x"))
    checks["direction_consistent"] = direction_consistent
    if direction_consistent:
        return "retain_profit_restrike_for_next_layer_no_production_change", "cross_layer_direction_consistent", checks
    return "retain_baseline_reject_profit_restrike_grid_no_production_change", "no_profit_threshold_cross_layer_pass", checks


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    schedule = base.current_schedule()
    mom_permission = prior.momentum_permission()
    model_engine, real_engine, engine_source = patched_profit_engines()
    real_short, model_short, short_source = maturity.patched_short_runners()
    results = {scope: run_layer(scope, schedule, model_engine, real_engine, real_short, model_short, mom_permission)
               for scope in ("real", "model")}
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2].assign(scope=x) for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][3].assign(scope=x) for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][4].assign(scope=x) for x in ("real", "model")], ignore_index=True)
    audit = {x: results[x][5] for x in ("real", "model")}
    summary, wide, unavailable = base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()
    decision, stability, checks = classify(full, audit)
    early = schedule[(schedule.layer.eq("model")) & (schedule.execution_date.lt(pd.Timestamp("2015-10-19")))]
    early_audit = {"rows": len(early), "missing_tier_rows": int(early.valuation_tier_new.isna().sum()),
                   "tier_min": int(early.valuation_tier_new.min()), "tier_max": int(early.valuation_tier_new.max()),
                   "tier_0_1_rows": int(early.valuation_tier_new.le(1).sum()),
                   "interpretation": "early model valuation is explicit and not defaulted to tier 0"}
    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "core_put_trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signal_audit.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "short_put_cycles.csv", index=False)
    events.to_csv(out / "short_put_events.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + engine_source, encoding="utf-8")
    meta.update({
        "phase": "complete", "scan_type": "ic_core_put_profit_restrike_standalone_and_joint_v1",
        "candidate_grid": [{"profit_multiple": x} for x in ("none", 2, 3)],
        "baseline": {"standalone": "*_rolling_ic_coreput_baseline_5bp", "joint": "*_m1_iv375_decay60_coreput_baseline_5bp",
                     "prior_run": str(PRIOR), "parity": {x: {"standalone": audit[x]["standalone_baseline_parity"], "joint": audit[x]["joint_baseline_parity"]} for x in ("real", "model")}},
        "data_snapshot": {"real": "2022-09-19..2026-08-14 actual 510500 Put/ETF and IC", "model": "2015-04-16..2026-08-14 theoretical 510500 Put plus historical IC"},
        "policy": {"core_put": "current IC 3m 95% target, Delta-sized, valuation tiers plus MOM120/debounce", "short_put": "M+1 95%, absolute IV>37.5%, decay60 one early roll, current admission", "execution_priority": "route open exit; target zero/monthly/resize; profit T+1 close"},
        "cost_model": {"510500_put_one_way": OPTION_ONE_WAY, "put_round_trip": 2 * OPTION_ONE_WAY, "ETF_and_IC_one_way": base.ONE_WAY, "risk_buffer": .30, "cash_annual": .03},
        "early_valuation_audit": early_audit, "audit": audit, "decision_checks": checks,
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "core_put_trades.csv.gz"), "signals": str(out / "signal_audit.csv.gz"), "cycles": str(out / "short_put_cycles.csv"), "events": str(out / "short_put_events.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "prior_meta": sha(PRIOR / "scan_meta.json")},
        "warnings": ["Research only; IC has no Call.", "Real listed option history is short.", "Model Put history is theoretical and not executable.", "5BP is one-way notional friction, not percent of premium."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = ("# IC 核心 Put 盈利兑现与重新建仓\n\n真实挂牌与理论延展分开；同时报告独立保护组合与当前卖 Put 混合组合；不改生产。\n\n"
              "## Data Snapshot\n\n真实挂牌层为 2022-09-19 至 2026-08-14；理论延展层为 2015-04-16 至 2026-08-14。\n\n"
              "## Full Results\n\n" + full.to_markdown(index=False) + "\n\n## Decision Gates\n\n```json\n" + json.dumps(checks, ensure_ascii=False, indent=2) +
              "\n```\n\n## Early Valuation Audit\n\n```json\n" + json.dumps(early_audit, ensure_ascii=False, indent=2) +
              "\n```\n\n## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision + "\n")
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
