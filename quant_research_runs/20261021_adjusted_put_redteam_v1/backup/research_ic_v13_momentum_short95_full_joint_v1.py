"""Full IC v1.3 test of routing the momentum sleeve into the retained short-Put policy."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior
import research_ic_v13_decay50_vs60_cost_cycle_final_v1 as final_decay


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_momentum_short95_full_joint_q1_v3"
SPEC = ROOT / "docs" / "ic_v13_momentum_short95_full_joint_v1_spec.md"
REFERENCE = final_decay.RUN
DECAY = 0.50
IV = 0.375
MULTIPLE = 3.0
PUT_COST_MULTIPLIER = 5.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_default(value):
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def route_scale(router: pd.DataFrame, active: pd.DataFrame, weights: pd.Series) -> tuple[pd.Series, dict]:
    current = active.date.map(weights).astype(float)
    values = []
    scale = 0.0
    entries = []
    mismatches = []
    original_units = 0.5 * current
    for i, row in enumerate(router.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        if str(row.action) == "ic_to_short_put_open":
            scale = float(original_units.iloc[i])
            if scale <= 0:
                raise RuntimeError(f"Momentum route opened without positive IC momentum weight: {day}")
            prior_units = float(original_units.iloc[i - 1]) if i else 0.0
            mismatches.append(abs(prior_units - scale))
            entries.append({"date": day, "entry_scale": scale, "prior_momentum_units": prior_units})
        values.append(scale)
        if scale > 0 and str(row.state) == "ic" and str(row.action) in {
            "resume_ic_open", "recovery_exit_ic_next_open", "cash_to_ic_open",
        }:
            next_units = float(original_units.iloc[i + 1]) if i + 1 < len(original_units) else scale
            mismatches.append(abs(next_units - scale))
            scale = 0.0
    out = pd.Series(values, index=router.index, dtype=float)
    return out, {
        "entries": entries,
        "entry_count": len(entries),
        "active_days": int(out.gt(0).sum()),
        "max_entry_or_restore_weight_mismatch": max(mismatches) if mismatches else 0.0,
    }


def run_put(scope: str, qic: pd.DataFrame, qframes: dict, option_market: pd.DataFrame,
            schedule: pd.DataFrame, label: str, roll_dates: set[pd.Timestamp],
            model_profit, real_profit, exits=frozenset(), multiple=None):
    if scope == "real":
        put, trades = real_profit(
            qic, schedule, qframes, option_market, label, roll_dates,
            open_exit_dates=exits, profit_multiple=multiple,
        )
    else:
        put, trades = model_profit(
            qic, schedule, option_market, label, roll_dates,
            open_exit_dates=exits, profit_multiple=multiple,
        )
    return prior.maturity.costed_core(put, PUT_COST_MULTIPLIER), trades


def compose_dual(active: pd.DataFrame, fixed_router: pd.DataFrame, fixed_routed: bool,
                 weights: pd.Series, momentum_router: pd.DataFrame, momentum_scale: pd.Series,
                 momentum_routed: bool, grid: pd.DataFrame, total_put: pd.DataFrame,
                 candidate: str) -> pd.DataFrame:
    dates = active.date.reset_index(drop=True)
    if not dates.equals(fixed_router.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} fixed router dates differ")
    if not dates.equals(momentum_router.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} momentum router dates differ")
    weight = active.date.map(weights).astype(float).reset_index(drop=True)
    gross = active.ic_gross_ret.astype(float).reset_index(drop=True)
    roll = active.roll_event.astype(float).reset_index(drop=True)

    fixed_non_cash = fixed_router.return_net.astype(float).reset_index(drop=True) - fixed_router.cash_weight.astype(float).reset_index(drop=True) * prior.CASH
    fixed_contribution = 0.5 * fixed_non_cash
    fixed_reserve = 0.5 * (1.0 - fixed_router.cash_weight.astype(float).reset_index(drop=True))

    if not momentum_routed:
        turnover = weight.diff().abs(); turnover.iloc[0] = abs(float(weight.iloc[0]))
        cost = prior.FUTURES_ONE_WAY * turnover + 2.0 * prior.FUTURES_ONE_WAY * weight * roll
        mom_net = (1.0 + weight * gross) * (1.0 - cost) - 1.0
        momentum_contribution = 0.5 * mom_net
        momentum_reserve = 0.15 * weight
    else:
        q = momentum_scale.reset_index(drop=True).astype(float)
        route_active = q.gt(0)
        normal_weight = weight.where(~route_active, 0.0)
        turnover = normal_weight.diff().abs(); turnover.iloc[0] = abs(float(normal_weight.iloc[0]))
        transitions = route_active.ne(route_active.shift(fill_value=False))
        turnover = turnover.where(~transitions, 0.0)
        cost = prior.FUTURES_ONE_WAY * turnover + 2.0 * prior.FUTURES_ONE_WAY * normal_weight * roll
        normal_net = (1.0 + normal_weight * gross) * (1.0 - cost) - 1.0
        route_non_cash = momentum_router.return_net.astype(float).reset_index(drop=True) - momentum_router.cash_weight.astype(float).reset_index(drop=True) * prior.CASH
        momentum_contribution = 0.5 * normal_net + q * route_non_cash
        momentum_reserve = 0.15 * normal_weight + q * (1.0 - momentum_router.cash_weight.astype(float).reset_index(drop=True))

    cash = (
        1.0 - fixed_reserve - momentum_reserve
        - 0.3 * grid.grid_units.astype(float).reset_index(drop=True)
        - total_put.put_mark_fraction.astype(float).reset_index(drop=True)
    )
    if cash.min() < -1e-12:
        raise RuntimeError(f"{candidate} negative cash weight: {cash.min()}")
    ret = (
        (1.0 + fixed_contribution + momentum_contribution + total_put.put_pnl_ret.astype(float).reset_index(drop=True))
        * (1.0 - total_put.put_cost_rate.astype(float).reset_index(drop=True)) - 1.0
        + grid.grid_net_increment.astype(float).reset_index(drop=True) + cash.clip(lower=0.0) * prior.CASH
    )
    if not np.isfinite(ret).all() or ret.le(-1.0).any():
        raise RuntimeError(f"{candidate} invalid returns")
    out = pd.DataFrame({
        "date": dates, "candidate": candidate, "return_net": ret,
        "cash_weight": cash, "momentum_weight": weight,
        "momentum_route_scale": momentum_scale.to_numpy(dtype=float) if momentum_routed else 0.0,
        "grid_units": grid.grid_units.to_numpy(dtype=float),
        "put_pnl_ret": total_put.put_pnl_ret.to_numpy(dtype=float),
        "put_cost_rate": total_put.put_cost_rate.to_numpy(dtype=float),
        "put_mark_fraction": total_put.put_mark_fraction.to_numpy(dtype=float),
        "fixed_router_state": fixed_router.state.to_numpy(),
        "fixed_router_action": fixed_router.action.to_numpy(),
        "momentum_router_state": momentum_router.state.to_numpy(),
        "momentum_router_action": momentum_router.action.to_numpy(),
    })
    out["nav"] = (1.0 + out.return_net).cumprod()
    return out


def run_layer(scope: str, path: pd.DataFrame, futures: pd.DataFrame, market: pd.DataFrame,
              weights: pd.Series, selected: pd.DataFrame, grid_all: pd.DataFrame,
              real_short, model_short, model_profit, real_profit):
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    qic = frames["ic"].copy()
    if not qic.date.equals(path.date):
        raise RuntimeError("Quarter path and IC option engine calendars differ")
    for column in ("contract", "settle", "close", "volume", "open_interest", "ic_gross_ret", "cost_rate", "roll_from", "roll_to"):
        qic[column] = path[column].to_numpy()
    qframes = {**frames, "ic": qic}
    put_roll_dates = prior.sleeve.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    active = path[path.date.ge(prior.REAL_START)].reset_index(drop=True) if scope == "real" else path.reset_index(drop=True)
    grid = grid_all[grid_all.date.isin(active.date)].reset_index(drop=True)
    core_schedule = prior.sleeve.build_schedule(selected, "core")
    momentum_schedule = prior.sleeve.build_schedule(selected, "momentum")

    _, _, real_chains, _, _, _ = prior.router_base.short.real_inputs()
    if scope == "real":
        base_signal = prior.maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
        runner = real_short
    else:
        base_signal = prior.maturity.maturity_model_signals(active, market, "m1")
        runner = model_short
    signal = prior.seller_state.signal_variant(
        base_signal, prior.router_base.current_schedule(), scope, "instant",
        prior.seller_state.momentum_permission(),
    )
    isolated, events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), DECAY, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    normal = prior.normal_router(active)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    momentum_scale, scale_audit = route_scale(routed, active, weights)
    route_active = momentum_scale.gt(0)

    core_always = prior.router_base.mask_schedule(core_schedule, scope, active.date)
    core_masked = prior.router_base.mask_schedule(core_schedule, scope, routed.loc[routed.state.eq("ic"), "date"])
    momentum_always = prior.router_base.mask_schedule(momentum_schedule, scope, active.date)
    momentum_allowed_dates = active.loc[~route_active, "date"]
    momentum_masked = prior.router_base.mask_schedule(momentum_schedule, scope, momentum_allowed_dates)

    variants = {
        "profit3x_no_short": (False, False),
        "fixed_only_q1": (True, False),
        "momentum_only_q1": (False, True),
        "both_q1": (True, True),
    }
    daily_parts, trade_parts, audits = [], [], {}
    for variant, (use_fixed, use_momentum) in variants.items():
        label = f"{scope}_{variant}"
        core_put, core_trades = run_put(
            scope, qic, qframes, option_market,
            core_masked if use_fixed else core_always,
            label + "_core", put_roll_dates, model_profit, real_profit,
            exits=route_dates if use_fixed else frozenset(), multiple=MULTIPLE,
        )
        momentum_put, momentum_trades = run_put(
            scope, qic, qframes, option_market,
            momentum_masked if use_momentum else momentum_always,
            label + "_momentum", put_roll_dates, model_profit, real_profit,
            exits=route_dates if use_momentum else frozenset(), multiple=None,
        )
        core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
        momentum_put = momentum_put[momentum_put.date.isin(active.date)].reset_index(drop=True)
        total_put = prior.combine_puts(core_put, momentum_put)
        selected_fixed = routed if use_fixed else normal
        selected_momentum = routed if use_momentum else normal
        daily = compose_dual(
            active, selected_fixed, use_fixed, weights,
            selected_momentum, momentum_scale, use_momentum,
            grid, total_put, label,
        )
        daily["scope"] = scope; daily["variant"] = variant
        daily_parts.append(daily)
        trade_parts.append(core_trades.assign(scope=scope, sleeve="core", candidate=label))
        trade_parts.append(momentum_trades.assign(scope=scope, sleeve="momentum", candidate=label))
        core_exit_dates = set(pd.to_datetime(
            core_trades.loc[core_trades.action.eq("route_open_exit"), "actual_execution_date"]
        ))
        momentum_exit_dates = set(pd.to_datetime(
            momentum_trades.loc[momentum_trades.action.eq("route_open_exit"), "actual_execution_date"]
        ))
        core_outside = core_exit_dates - set(pd.to_datetime(list(route_dates)))
        momentum_outside = momentum_exit_dates - set(pd.to_datetime(list(route_dates)))
        if core_outside or momentum_outside:
            raise RuntimeError(f"{label} Put route exit outside route-open dates")
        route_mask = route_active.to_numpy(dtype=bool)
        fixed_blocked_mask = routed.state.ne("ic").to_numpy(dtype=bool)
        core_mark_during_route = float(core_put.loc[fixed_blocked_mask, "put_mark_fraction"].abs().max()) if use_fixed and fixed_blocked_mask.any() else 0.0
        momentum_mark_during_route = float(momentum_put.loc[route_mask, "put_mark_fraction"].abs().max()) if use_momentum and route_mask.any() else 0.0
        if core_mark_during_route > 1e-12 or momentum_mark_during_route > 1e-12:
            raise RuntimeError(f"{label} retained a routed Put position")
        audits[variant] = {
            "route_cycles": int(len(cycles)) if (use_fixed or use_momentum) else 0,
            "momentum_route_entries": scale_audit["entry_count"] if use_momentum else 0,
            "momentum_route_active_days": scale_audit["active_days"] if use_momentum else 0,
            "momentum_boundary_weight_mismatch": scale_audit["max_entry_or_restore_weight_mismatch"] if use_momentum else 0.0,
            "core_put_route_exits": len(core_exit_dates),
            "momentum_put_route_exits": len(momentum_exit_dates),
            "core_put_route_exits_outside_route": len(core_outside),
            "momentum_put_route_exits_outside_route": len(momentum_outside),
            "core_put_mark_during_route_max": core_mark_during_route,
            "momentum_put_mark_during_route_max": momentum_mark_during_route,
            "core_profit_restrikes": int(core_trades.action.eq("close_profit_restrike").sum()),
            "min_cash_weight": float(daily.cash_weight.min()),
        }

    daily = pd.concat(daily_parts, ignore_index=True)
    reference = pd.read_csv(REFERENCE / "daily_outputs" / "cost_daily.csv.gz", parse_dates=["date"])
    parity = {}
    for variant, old in (("profit3x_no_short", "noseller"), ("fixed_only_q1", "joint")):
        current = daily[daily.candidate.eq(f"{scope}_{variant}")].sort_values("date")
        old_daily = reference[reference.candidate.eq(f"{scope}_decay50_{old}_cost5bp")].sort_values("date")
        error = float(np.max(np.abs(current.return_net.to_numpy() - old_daily.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"Final decay50 parity failed {scope} {variant}: {error}")
        parity[variant] = error
    return (
        daily, pd.concat(trade_parts, ignore_index=True), signal.assign(scope=scope),
        events.assign(scope=scope), cycles.assign(scope=scope),
        {"short_router": short_audit, "momentum_scale": scale_audit,
         "final_decay50_parity": parity, "variants": audits},
    )


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    checkpoint = pd.read_csv(prior.QUARTER, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    formal_parity = prior.official_parity(checkpoint)
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()

    old_maturity_iv = prior.maturity.IV_THRESHOLD
    old_seller_iv = prior.seller_state.IV_THRESHOLD
    try:
        prior.maturity.IV_THRESHOLD = IV
        prior.seller_state.IV_THRESHOLD = IV
        results = {
            scope: run_layer(scope, path, futures, market, weights, selected, grid,
                             real_short, model_short, model_profit, real_profit)
            for scope in ("real", "model")
        }
    finally:
        prior.maturity.IV_THRESHOLD = old_maturity_iv
        prior.seller_state.IV_THRESHOLD = old_seller_iv

    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    audits = {x: results[x][5] for x in ("real", "model")}
    summary, wide, unavailable = prior.router_base.summarize(daily)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]
    full = summary[summary.segment.eq("full")].copy()

    paired = []
    passed = True
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        fixed = block.loc[f"{scope}_fixed_only_q1"]
        both = block.loc[f"{scope}_both_q1"]
        ann_diff = float(both.ann_return - fixed.ann_return)
        sharpe_diff = float(both.sharpe_repo - fixed.sharpe_repo)
        dd_worse = float(abs(both.max_dd) - abs(fixed.max_dd))
        gate = ann_diff >= -1e-12 and sharpe_diff >= -0.05 - 1e-12 and dd_worse <= 0.01 + 1e-12
        passed = passed and gate
        paired.append({
            "scope": scope, "fixed_only_ann_return": float(fixed.ann_return),
            "both_ann_return": float(both.ann_return),
            "both_minus_fixed_ann_pp": 100.0 * ann_diff,
            "both_minus_fixed_sharpe": sharpe_diff,
            "both_mdd_abs_worsening_pp": 100.0 * dd_worse, "gate": gate,
        })
    paired = pd.DataFrame(paired)
    integrity = all(
        max(audits[s]["final_decay50_parity"].values()) <= 1e-12
        and audits[s]["variants"]["both_q1"]["min_cash_weight"] >= -1e-12
        and audits[s]["variants"]["both_q1"]["core_put_route_exits_outside_route"] == 0
        and audits[s]["variants"]["both_q1"]["momentum_put_route_exits_outside_route"] == 0
        for s in ("real", "model")
    )
    passed = passed and integrity
    decision = (
        "retain_ic_momentum_short95_both_q1_as_research_candidate_no_production_change"
        if passed else "reject_ic_momentum_short95_keep_fixed_only_q1_no_production_change"
    )
    stability = (
        "ic_momentum_short95_cross_layer_gate_passed"
        if passed else "ic_momentum_short95_cross_layer_gate_failed"
    )

    annual = daily.assign(year=daily.date.dt.year).groupby(
        ["candidate", "scope", "variant", "year"], as_index=False
    ).agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv", index=False)
    signals.to_csv(out / "signals.csv", index=False)
    events.to_csv(out / "router_events.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")
    meta.update({
        "phase": "complete", "scan_type": "IC_v1_3_momentum_short95_full_joint_q1",
        "baseline": {"candidate": "fixed_only_q1", "reference_run": str(REFERENCE),
                     "daily_parity": {s: audits[s]["final_decay50_parity"] for s in audits},
                     "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [{"variant": x} for x in (
            "profit3x_no_short", "fixed_only_q1", "momentum_only_q1", "both_q1")],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 listed 510500 options",
                          "model": "2015-04-16..2026-08-14 theoretical extension"},
        "cost_model": {"all_510500_put_one_way_bp": 5, "IC_one_way_bp": 1,
                       "short_put_quantity": "q1 equal notional per routed IC notional",
                       "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "fixed_policy": {"maturity": "M+1", "iv_threshold": IV, "premium_decay": DECAY,
                         "quantity": "q1 equal notional", "core_put_profit_multiple": MULTIPLE,
                         "seller_admission": "valuation tier 0/1 plus original IC 1.3 execution momentum permission; no seller MOM120",
                         "call": "excluded"},
        "momentum_route": {"entry_size": "0.5 times current execution weight",
                           "size_during_cycle": "fixed", "momentum_put": "force exit on route open and zero target during route"},
        "audit": audits, "paired_gate": paired.to_dict("records"),
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"),
                    "trades": str(out / "trades.csv"), "signals": str(out / "signals.csv"),
                    "events": str(out / "router_events.csv"), "cycles": str(out / "cycles.csv"),
                    "paired": str(RUN / "paired_comparison.csv"),
                    "annual": str(RUN / "annual_attribution.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC),
                          "reference_daily": sha(REFERENCE / "daily_outputs" / "cost_daily.csv.gz"),
                          "quarter": sha(prior.QUARTER), "grid": sha(prior.GRID)},
        "warnings": ["Research-only; production unchanged.",
                     "Model 510500 Put is theoretical.",
                     "No dynamic margin, forced liquidation, tax, capacity, integer sizing, or explicit fill delay."],
        "decision": decision, "stability_label": stability,
        "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")
    record = (
        "# IC v1.3动量腿高IV转卖Put完整组合测试\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Paired Gate\n\n" + paired.to_markdown(index=False) +
        "\n\n## Audit\n\n```json\n" + json.dumps(audits, ensure_ascii=False, indent=2, default=json_default) +
        "\n```\n\n## Decision\n\n" + decision +
        "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(paired.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
