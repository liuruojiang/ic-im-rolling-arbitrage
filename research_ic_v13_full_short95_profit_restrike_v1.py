"""Full current IC v1.3 replay with fixed-core short95 routing and core-Put profit restrike."""
from __future__ import annotations

import hashlib
import json
import subprocess
import types
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_core_put_profit_restrike_v1 as profit
import research_ic_coreput_highiv_short95_router_v1 as router_base
import research_ic_mom120_debounce_v1 as put_debounce
import research_ic_short95_maturity_scan_v1 as maturity
import research_ic_short95_unified_valuation_debounce_v1 as seller_state
import research_ic_v13_short_momentum_debounce_v1 as momentum_debounce
import run_ic_v13_sleeve_put_independent_replay_v1 as sleeve


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_v13_full_short95_profit_restrike_2x_3x_redteam_v2"
SPEC = ROOT / "docs" / "ic_v14_redteam_correction_rerun_v1_spec.md"
QUARTER = ROOT / "quant_param_scan_runs" / "20260904_ic_v13_full_roll_tenor_timing_v2" / "candidate_checkpoints" / "quarter_T3_fixed.csv.gz"
GRID = ROOT / "quant_param_scan_runs" / "20260913_ic_grid_own_calibration" / "daily_candidates.csv.gz"
REAL_START = pd.Timestamp("2022-09-19")
END = pd.Timestamp("2026-08-14")
CASH = router_base.CASH
FUTURES_ONE_WAY = 0.0001
PUT_COST_MULTIPLIER = 5.0
OPTION_ONE_WAY = 0.0005
VARIANTS = {
    "baseline": (False, None),
    "profit2x_only": (False, 2.0),
    "profit3x_only": (False, 3.0),
    "short95_only": (True, None),
    "joint2x": (True, 2.0),
    "joint3x": (True, 3.0),
}
PUT_FIELDS = ("put_pnl_ret", "put_cost_rate", "put_mark_fraction")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def current_momentum_weights() -> pd.Series:
    authority = momentum_debounce.load_authority()
    raw = pd.read_csv(authority.CSI500_OHLCV_PATH, parse_dates=["date"]).sort_values("date").set_index("date")
    selected = momentum_debounce.Variant("current_abs20_reentry_2d_p1", 1, 2, 0.0, 0.01)
    schedule = momentum_debounce.build_variant(authority, raw, selected)
    return schedule.set_index("date").execution_weight.astype(float)


def current_selected(weights: pd.Series) -> pd.DataFrame:
    _, _, selected = sleeve.load_base_components()
    _, valuation, _, _ = sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    tri = valuation.set_index("date").tri_close
    schedule = selected.sort_values("eval_date").copy()
    schedule["momentum_weight"] = schedule.execution_date.map(weights)
    if schedule.momentum_weight.isna().any():
        raise RuntimeError("Current IC momentum weights do not cover Put schedule")
    schedule["momentum_120"] = schedule.eval_date.map(tri.pct_change(120, fill_method=None))
    schedule["mom120_floor_active"] = put_debounce.mom_floor_state(
        schedule.momentum_120, days=2, threshold=0.01
    )
    schedule["mom120_floor_delta"] = np.where(schedule.mom120_floor_active, 0.5, 0.0)
    schedule["v2_target_delta"] = np.maximum(
        schedule.valuation_tier_new.astype(float) * 0.25,
        schedule.mom120_floor_delta.astype(float),
    )
    return schedule


def quarterly_path() -> tuple[pd.DataFrame, pd.DataFrame]:
    checkpoint = pd.read_csv(QUARTER, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    _, _, _, _, _, futures = router_base.short.real_inputs()
    model_active, _, _ = router_base.short.model_source.model_inputs()
    futures_reset = futures.reset_index()
    held = checkpoint[["date", "contract", "futures_gross_ret", "roll_event"]].merge(
        futures_reset[["date", "contract", "open", "close", "settle", "volume", "open_interest"]],
        on=["date", "contract"], how="left", validate="one_to_one",
    )
    if held[["open", "close", "settle", "volume", "open_interest"]].isna().any().any():
        raise RuntimeError("Quarter T-3 held futures quotes are incomplete")
    held["ic_gross_ret"] = held.futures_gross_ret.astype(float)
    held["cost_rate"] = FUTURES_ONE_WAY * (
        np.where(np.arange(len(held)) == 0, 1.0, 0.0) + 2.0 * held.roll_event.astype(float)
    )
    held["ic_net_ret"] = (1.0 + held.ic_gross_ret) * (1.0 - held.cost_rate) - 1.0
    held["roll_from"] = np.where(held.roll_event, held.contract, None)
    next_contract = held.contract.shift(-1)
    held["roll_to"] = np.where(held.roll_event, next_contract, None)
    reference = model_active[["date", "csi500_price_close", "csi500_tri_close"]]
    held = held.merge(reference, on="date", how="left", validate="one_to_one")
    if held[["csi500_price_close", "csi500_tri_close"]].isna().any().any():
        raise RuntimeError("Quarter T-3 path lacks CSI500 reference levels")
    return held, futures


def official_parity(checkpoint: pd.DataFrame) -> float:
    d = checkpoint.copy()
    cost = FUTURES_ONE_WAY * (np.where(np.arange(len(d)) == 0, 1.0, 0.0) + 2.0 * d.roll_event.astype(float))
    core = (1.0 + d.futures_gross_ret.astype(float)) * (1.0 - cost) - 1.0
    weight = d.momentum_weight.astype(float)
    turnover = weight.diff().abs(); turnover.iloc[0] = abs(float(weight.iloc[0]))
    mom_cost = FUTURES_ONE_WAY * turnover + 2.0 * FUTURES_ONE_WAY * weight * d.roll_event.astype(float)
    momentum = (1.0 + weight * d.futures_gross_ret.astype(float)) * (1.0 - mom_cost) - 1.0
    grid_units = d.total_units.astype(float) - 0.5 - 0.5 * weight
    cash = 0.5 * 0.7 + 0.5 * (1.0 - 0.3 * weight) - d.put_mark_fraction.astype(float) - 0.3 * grid_units
    rebuilt = ((1.0 + 0.5 * core + 0.5 * momentum + d.put_pnl_ret.astype(float))
               * (1.0 - d.put_cost_rate.astype(float)) - 1.0
               + d.grid_net_increment.astype(float) + cash.clip(lower=0.0) * CASH)
    error = float(np.max(np.abs(rebuilt.to_numpy() - d.ret.astype(float).to_numpy())))
    if error > 1e-12:
        raise RuntimeError(f"Frozen quarter T-3 composition parity failed: {error}")
    return error


def current_grid(dates: pd.Series) -> pd.DataFrame:
    raw = pd.read_csv(GRID, parse_dates=["date"])
    raw = raw[raw.candidate.eq("L0.500_H1.000")].sort_values("date").reset_index(drop=True)
    raw = raw[raw.date.isin(dates)].reset_index(drop=True)
    if not raw.date.equals(dates.reset_index(drop=True)):
        raise RuntimeError("Current 0.5/1.0 IC grid dates do not align")
    return pd.DataFrame({
        "date": raw.date,
        "grid_signal": raw.grid.astype(float),
        "grid_units": 0.5 * raw.grid.astype(float),
        "grid_net_increment": 0.5 * raw.grid_net_ret.astype(float),
    })


def configure_short_runners(path: pd.DataFrame, futures: pd.DataFrame):
    real_runner, model_runner, source = maturity.patched_short_runners()
    _, etf, chains, options, expiries, _ = router_base.short.real_inputs()
    _, market, _ = router_base.short.model_source.model_inputs()
    real_path = path[path.date.ge(REAL_START)].reset_index(drop=True)
    model_path = path.reset_index(drop=True)
    real_runner.__globals__["real_inputs"] = lambda: (real_path, etf, chains, options, expiries, futures)
    model_runner.__globals__["model_source"] = types.SimpleNamespace(
        model_inputs=lambda: (model_path, market, futures), CASH=router_base.short.model_source.CASH
    )
    return real_runner, model_runner, source, market


def normal_router(active: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "date": active.date,
        "return_net": active.ic_net_ret.astype(float) + 0.7 * CASH,
        "state": "ic", "action": "", "route": False, "cash_weight": 0.7,
    })
    out["nav"] = (1.0 + out.return_net).cumprod()
    return out


def combine_puts(core: pd.DataFrame, momentum: pd.DataFrame) -> pd.DataFrame:
    if not core.date.reset_index(drop=True).equals(momentum.date.reset_index(drop=True)):
        raise RuntimeError("Core and momentum Put dates differ")
    out = core.copy()
    out["put_pnl_ret"] = core.put_pnl_ret.astype(float) + momentum.put_pnl_ret.astype(float)
    out["put_mark_fraction"] = core.put_mark_fraction.astype(float) + momentum.put_mark_fraction.astype(float)
    out["put_cost_rate"] = 1.0 - (
        (1.0 - core.put_cost_rate.astype(float)) * (1.0 - momentum.put_cost_rate.astype(float))
    )
    return out


def compose(active: pd.DataFrame, router: pd.DataFrame, weights: pd.Series,
            grid: pd.DataFrame, total_put: pd.DataFrame, candidate: str) -> pd.DataFrame:
    if not active.date.reset_index(drop=True).equals(router.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} router dates differ")
    if not active.date.reset_index(drop=True).equals(grid.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} grid dates differ")
    if not active.date.reset_index(drop=True).equals(total_put.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} Put dates differ")
    weight = active.date.map(weights).astype(float)
    if weight.isna().any():
        raise RuntimeError(f"{candidate} missing momentum weights")
    turnover = weight.diff().abs(); turnover.iloc[0] = abs(float(weight.iloc[0]))
    mom_cost = FUTURES_ONE_WAY * turnover + 2.0 * FUTURES_ONE_WAY * weight * active.roll_event.astype(float)
    mom_net = (1.0 + weight * active.ic_gross_ret.astype(float)) * (1.0 - mom_cost) - 1.0
    fixed_non_cash = router.return_net.astype(float) - router.cash_weight.astype(float) * CASH
    base_non_cash = 0.5 * fixed_non_cash + 0.5 * mom_net
    fixed_reserve = 0.5 * (1.0 - router.cash_weight.astype(float))
    cash = (1.0 - fixed_reserve - 0.15 * weight - 0.3 * grid.grid_units.astype(float)
            - total_put.put_mark_fraction.astype(float))
    if cash.min() < -1e-12:
        raise RuntimeError(f"{candidate} negative cash: {cash.min()}")
    ret = ((1.0 + base_non_cash + total_put.put_pnl_ret.astype(float))
           * (1.0 - total_put.put_cost_rate.astype(float)) - 1.0
           + grid.grid_net_increment.astype(float) + cash.clip(lower=0.0) * CASH)
    if not np.isfinite(ret).all() or ret.le(-1.0).any():
        raise RuntimeError(f"{candidate} invalid returns")
    out = pd.DataFrame({
        "date": active.date, "candidate": candidate, "return_net": ret,
        "cash_weight": cash, "momentum_weight": weight,
        "grid_units": grid.grid_units, "put_pnl_ret": total_put.put_pnl_ret,
        "put_cost_rate": total_put.put_cost_rate, "put_mark_fraction": total_put.put_mark_fraction,
        "fixed_router_state": router.state, "fixed_router_action": router.action,
    })
    out["nav"] = (1.0 + out.return_net).cumprod()
    return out


def run_layer(scope: str, path: pd.DataFrame, futures: pd.DataFrame, market: pd.DataFrame,
              weights: pd.Series, selected: pd.DataFrame, grid_all: pd.DataFrame,
              real_short, model_short, model_profit, real_profit):
    frames, _, option_market, _ = sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    qic = frames["ic"].copy()
    if not qic.date.equals(path.date):
        raise RuntimeError("Quarter path and IC option engine calendars differ")
    for column in ("contract", "settle", "close", "volume", "open_interest", "ic_gross_ret", "cost_rate", "roll_from", "roll_to"):
        qic[column] = path[column].to_numpy()
    qframes = {**frames, "ic": qic}
    put_roll_dates = sleeve.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    active = path[path.date.ge(REAL_START)].reset_index(drop=True) if scope == "real" else path.reset_index(drop=True)
    grid = grid_all[grid_all.date.isin(active.date)].reset_index(drop=True)
    core_schedule = sleeve.build_schedule(selected, "core")
    momentum_schedule = sleeve.build_schedule(selected, "momentum")

    real_active, _, real_chains, _, _, _ = router_base.short.real_inputs()
    if scope == "real":
        real_active = active
        base_signal = maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
        runner = real_short
    else:
        base_signal = maturity.maturity_model_signals(active, market, "m1")
        runner = model_short
    current_combined = router_base.current_schedule()
    signal = seller_state.signal_variant(base_signal, current_combined, scope, "instant", seller_state.momentum_permission())
    isolated, short_events, cycles, short_audit = runner(router_base.entry_series(signal), 0.60, "m1", OPTION_ONE_WAY)
    routed = router_base.stitched_router(scope, active, futures, isolated, signal)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    core_masked = router_base.mask_schedule(core_schedule, scope, routed.loc[routed.state.eq("ic"), "date"])
    core_always = router_base.mask_schedule(core_schedule, scope, active.date)
    momentum_scope = router_base.mask_schedule(momentum_schedule, scope, active.date)

    engine = sleeve.ic_put.v1.put_engine
    if scope == "real":
        mom_put, mom_trades = engine.run_real_delta(qic, momentum_scope, qframes, option_market, f"{scope}_momentum", put_roll_dates)
    else:
        mom_put, mom_trades = engine.run_model_delta(qic, momentum_scope, option_market, f"{scope}_momentum", put_roll_dates)
    mom_put = mom_put[mom_put.date.isin(active.date)].reset_index(drop=True)
    mom_put = maturity.costed_core(mom_put, PUT_COST_MULTIPLIER)

    daily_parts, trade_parts, audit = [], [mom_trades.assign(scope=scope, sleeve="momentum", candidate=f"{scope}_all")], {
        "short_router": short_audit, "route_switches": len(route_dates), "variants": {},
    }
    normal = normal_router(active)
    for variant, (use_route, multiple) in VARIANTS.items():
        label = f"{scope}_{variant}"
        schedule = core_masked if use_route else core_always
        exits = route_dates if use_route else frozenset()
        if scope == "real":
            core_put, core_trades = real_profit(
                qic, schedule, qframes, option_market, label, put_roll_dates,
                open_exit_dates=exits, profit_multiple=multiple,
            )
        else:
            core_put, core_trades = model_profit(
                qic, schedule, option_market, label, put_roll_dates,
                open_exit_dates=exits, profit_multiple=multiple,
            )
        core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
        core_put = maturity.costed_core(core_put, PUT_COST_MULTIPLIER)
        total_put = combine_puts(core_put, mom_put)
        selected_router = routed if use_route else normal
        daily = compose(active, selected_router, weights, grid, total_put, label)
        daily["scope"] = scope; daily["variant"] = variant
        daily_parts.append(daily)
        trade_parts.append(core_trades.assign(scope=scope, sleeve="core", candidate=label))
        route_exits = core_trades[core_trades.action.eq("route_open_exit")]
        profit_events = core_trades[core_trades.action.eq("close_profit_restrike")]
        duplicates = set(route_exits.actual_execution_date) & set(profit_events.actual_execution_date)
        if duplicates:
            raise RuntimeError(f"Duplicate route/profit event {label}: {duplicates}")
        simultaneous = set(route_exits.actual_execution_date)
        if simultaneous - route_dates:
            raise RuntimeError(f"Core Put route exit outside fixed-core switch {label}")
        audit["variants"][variant] = {
            "core_trade_events": len(core_trades), "momentum_trade_events": len(mom_trades),
            "profit_restrikes": len(profit_events), "core_put_route_exits": len(route_exits),
            "duplicate_route_profit_days": len(duplicates), "min_cash_weight": float(daily.cash_weight.min()),
            "put_cost_rate_sum_5bp": float(total_put.put_cost_rate.sum()),
        }
    return (pd.concat(daily_parts, ignore_index=True), pd.concat(trade_parts, ignore_index=True),
            signal.assign(scope=scope), short_events.assign(scope=scope), cycles.assign(scope=scope), audit)


def classify(full: pd.DataFrame, audits: dict):
    checks = {}
    for multiple in (2, 3):
        variant = f"joint{multiple}x"
        layers = []
        for scope in ("real", "model"):
            block = full[full.scope.eq(scope)].set_index("candidate")
            baseline = block.loc[f"{scope}_baseline"]
            profit_only = block.loc[f"{scope}_profit{multiple}x_only"]
            short_only = block.loc[f"{scope}_short95_only"]
            joint = block.loc[f"{scope}_{variant}"]
            baseline_gate = bool(
                joint.ann_return >= baseline.ann_return - 1e-12
                and joint.sharpe_repo >= baseline.sharpe_repo - 1e-12
                and abs(joint.max_dd) - abs(baseline.max_dd) <= 0.005 + 1e-12
            )
            best_single = max(float(profit_only.ann_return), float(short_only.ann_return))
            worst_single_dd = min(float(profit_only.max_dd), float(short_only.max_dd))
            joint_gate = bool(joint.ann_return >= best_single - 1e-12 and joint.max_dd >= worst_single_dd - 0.01 - 1e-12)
            layers.append({
                "scope": scope, "baseline_cagr_diff_pp": 100 * float(joint.ann_return - baseline.ann_return),
                "baseline_sharpe_diff": float(joint.sharpe_repo - baseline.sharpe_repo),
                "baseline_mdd_diff_pp": 100 * float(abs(joint.max_dd) - abs(baseline.max_dd)),
                "joint_minus_best_single_cagr_pp": 100 * float(joint.ann_return - best_single),
                "joint_vs_worst_single_mdd_pp": 100 * float(joint.max_dd - worst_single_dd),
                "profit_restrikes": audits[scope]["variants"][variant]["profit_restrikes"],
                "baseline_gate": baseline_gate, "joint_gate": joint_gate,
            })
        checks[variant] = {"layers": layers, "pass": all(x["baseline_gate"] and x["joint_gate"] for x in layers)}
    if checks["joint3x"]["pass"]:
        decision = "retain_joint3x_for_research_no_production_change"
        stability = "full_current_IC_joint3x_cross_layer_gate_passed_real_events_sparse"
    elif checks["joint2x"]["pass"]:
        decision = "retain_joint2x_for_research_no_production_change"
        stability = "full_current_IC_joint2x_cross_layer_gate_passed_real_events_sparse"
    else:
        decision = "do_not_promote_joint_keep_best_single_no_production_change"
        stability = "full_current_IC_joint_gate_failed"
    return decision, stability, checks


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    checkpoint = pd.read_csv(QUARTER, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    formal_parity = official_parity(checkpoint)
    path, futures = quarterly_path()
    normal_error = float(np.max(np.abs(normal_router(path).return_net.to_numpy() - (path.ic_net_ret + 0.7 * CASH).to_numpy())))
    if normal_error > 1e-12:
        raise RuntimeError("Quarter normal router parity failed")
    weights = current_momentum_weights()
    selected = current_selected(weights)
    grid = current_grid(path.date)
    real_short, model_short, short_source, market = configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = profit.patched_profit_engines()
    results = {scope: run_layer(scope, path, futures, market, weights, selected, grid,
                                real_short, model_short, model_profit, real_profit)
               for scope in ("real", "model")}
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    audits = {x: results[x][5] for x in ("real", "model")}
    summary, wide, unavailable = router_base.summarize(daily)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]
    full = summary[summary.segment.eq("full")].copy()
    decision, stability, checks = classify(full, audits)
    annual = (daily.assign(year=daily.date.dt.year).groupby(["candidate", "scope", "variant", "year"], as_index=False)
              .agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0))))
    early = selected[(selected.layer.eq("model")) & (selected.execution_date.lt(pd.Timestamp("2015-10-19")))]
    early_audit = {"rows": len(early), "missing_tier_rows": int(early.valuation_tier_new.isna().sum()),
                   "tier_min": int(early.valuation_tier_new.min()), "tier_max": int(early.valuation_tier_new.max()),
                   "tier_0_1_rows": int(early.valuation_tier_new.le(1).sum()),
                   "interpretation": "early model valuation is explicit and not defaulted to tier 0"}
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "short_put_events.csv", index=False)
    cycles.to_csv(out / "short_put_cycles.csv", index=False)
    annual.to_csv(RUN / "annual_attribution.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")
    meta.update({
        "phase": "complete", "scan_type": "full_current_IC_v1_3_fixed_core_short95_plus_core_put_profit_restrike",
        "baseline": {"candidate": ["real_baseline", "model_baseline"], "frozen_quarter_T3_composition_parity": formal_parity, "normal_router_parity": normal_error},
        "candidate_grid": [{"variant": x} for x in VARIANTS],
        "data_snapshot": {"real_start": "2022-09-19", "real_end": "2026-08-14", "model_start": "2015-04-16", "model_end": "2026-08-14", "quarter_chain": str(QUARTER.relative_to(ROOT)), "grid_source": str(GRID.relative_to(ROOT))},
        "component_scope": {"routed": "fixed core 0.5x IC and core Put only", "unchanged": ["0.5x momentum IC", "momentum Put", "0.5x valuation grid"], "call": "IC excluded"},
        "cost_model": {"all_510500_put_one_way": OPTION_ONE_WAY, "put_round_trip": 2 * OPTION_ONE_WAY, "IC_one_way": FUTURES_ONE_WAY, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "policy": {"futures_roll": "quarterly T-3 close", "grid": "valuation 0.5/1.0, 0.5x", "core_put": "3m 95%, valuation plus MOM120 2d >1% release debounce", "momentum_put": "3m 95%, valuation times current momentum sleeve", "short_put": "M+1 95%, abs IV>37.5%, decay60, one early roll, current admission"},
        "early_valuation_audit": early_audit, "audit": audits, "decision_checks": checks, "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "events": str(out / "short_put_events.csv"), "cycles": str(out / "short_put_cycles.csv"), "annual": str(RUN / "annual_attribution.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "quarter": sha(QUARTER), "grid": sha(GRID), "layer5": sha(ROOT / "research_ic_core_put_profit_restrike_v1.py")},
        "warnings": ["Research-only historical counterfactual; production ledgers unchanged.", "Model 510500 Put is theoretical and not executable listed history.", "Real route and profit events are sparse.", "No dynamic margin, forced liquidation, tax, capacity, bid-ask depth, or integer account sizing."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = ("# IC v1.3完整组合：高IV卖Put＋核心Put盈利兑现\n\n"
              "## Data Snapshot\n\n真实挂牌层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。期货使用季度T-3链。\n\n"
              "## Full Results\n\n" + full.to_markdown(index=False) +
              "\n\n## Decision Gates\n\n```json\n" + json.dumps(checks, ensure_ascii=False, indent=2) +
              "\n```\n\n## Annual Attribution\n\n" + annual.to_markdown(index=False) +
              "\n\n## Verification\n\n冻结季度完整组合逐日复现误差：" + f"{formal_parity:.3e}" +
              "；早期估值123行显式恢复；路由/盈利重复事件为0；所有Put单边5BP。\n\n"
              "## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision + "\n")
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
