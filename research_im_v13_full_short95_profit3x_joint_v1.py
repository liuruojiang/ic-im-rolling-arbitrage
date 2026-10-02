"""Full IM v1.3 replay with fixed-core short95 routing and 3x core-Put restrike."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_short95_profit3x_joint_v1 as component
import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity
import research_imc_short95_maturity_corrected_v2 as maturity_v2


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_full_short95_profit3x_joint_redteam_v2"
SPEC = ROOT / "docs" / "im_v14_redteam_correction_rerun_v1_spec.md"
CHAIN = ROOT / "quant_param_scan_runs" / "20260908_im_full_combination_momentum_put_signal_v1"
PRIOR = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_current_counterfactual_corrected_full_portfolio_core_put_profit_restrike_robustness_2_5x_3x_3_5x_t1_t2"
PUT_COST_MULTIPLIER = 5.0
FUTURES_ONE_WAY = 0.0001
CASH_DAILY = common.CASH_DAILY
PUT_FIELDS = list(common.PUT_FIELDS)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path, dates=("date",)) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=list(dates), low_memory=False)


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def audited_router():
    run, source = component.router_with_option_cost_5bp()
    needle = "        rows.append(item)"
    replacement = (
        "        item.update(router_pnl_ret=pnl/previous_equity,router_cost_rate=cost/previous_equity,"
        "router_cash_weight=(0.7 if state in {'imc','put','recovery_im'} or pending=='assign' else 1.0),"
        "router_reserve_active=bool(state in {'imc','put','recovery_im'} or pending=='assign'),"
        "router_futures_active=bool(state in {'imc','recovery_im'}))\n"
        "        rows.append(item)"
    )
    if needle not in source:
        raise RuntimeError("Router output hook changed")
    source = source.replace(needle, replacement, 1)
    namespace = dict(vars(common.router))
    namespace["OPTION_ONE_WAY_COST"] = component.OPTION_ONE_WAY_COST
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    raw = namespace["run_router_decay"]

    def wrapped(base, options, futures, signal, threshold, fallback, decay):
        daily, events, cycles = raw(base, options, futures, signal, threshold, fallback, decay)
        daily["early_roll_executed"] = daily.action.eq("put_early_roll60_buyback_and_sell_next_open")
        return daily, events, cycles

    return wrapped, source


def quarterly_router_inputs(scope: str, full_base: pd.DataFrame):
    if scope == "real":
        _, _, options, options_for_put, _ = maturity.load_layer("real")
        base = read(CHAIN / "quarter1_bridged_upstream.csv.gz")
        base = base[base.date.isin(full_base.date)].reset_index(drop=True)
        futures = read(common.router.FUTURES)
        market = None
        if not base.date.equals(full_base.date):
            raise RuntimeError("Quarterly real chain/date mismatch")
        unit_gross = full_base.base_futures_gross.astype(float) / full_base.base_units.astype(float)
        gross_error = float(np.max(np.abs(unit_gross.to_numpy() - base.im_gross_ret.to_numpy(dtype=float))))
        if gross_error > 1e-12:
            raise RuntimeError(f"Quarterly real unit-gross parity failed: {gross_error}")
    else:
        market, source_base, options, _ = maturity_v2.extended_model_inputs()
        options_for_put = None
        options = options.copy(); options["close"] = options["settle"]
        market = market[market.date.isin(full_base.date)].reset_index(drop=True)
        source_base = source_base[source_base.date.isin(full_base.date)].reset_index(drop=True)
        if not market.date.equals(full_base.date) or not source_base.date.equals(full_base.date):
            raise RuntimeError("Model router/full date mismatch")
        base = source_base.copy()
        base["contract"] = "MODEL_QUARTER_PROXY"
        base["roll_from"] = ""; base["roll_to"] = ""
        futures = pd.DataFrame({
            "date": market.date, "contract": "MODEL_QUARTER_PROXY",
            "open": market.spot_open.astype(float), "close": market.spot_close.astype(float),
            "settle": market.spot_close.astype(float),
        })
        unit_gross = full_base.base_futures_gross.astype(float) / full_base.base_units.astype(float)
        gross_error = 0.0

    unit_cost = FUTURES_ONE_WAY * (
        np.where(np.arange(len(full_base)) == 0, 1.0, 0.0)
        + 2.0 * full_base.roll_event.astype(float).to_numpy()
    )
    base["baseline_plus_cash_ret"] = (
        (1.0 + unit_gross.to_numpy(dtype=float)) * (1.0 - unit_cost) - 1.0 + 0.7 * CASH_DAILY
    )
    return market, base, options, options_for_put, futures, gross_error


def scale_core_half(put: pd.DataFrame, scope: str, cost_mult: float) -> pd.DataFrame:
    out = common.scale_put(put, scope)
    out[PUT_FIELDS] = 0.5 * out[PUT_FIELDS]
    out["put_cost_rate"] = out.put_cost_rate.astype(float) * cost_mult
    return out


def run_core(scope, base, market, options_for_put, schedule, real_engine, model_engine, label, multiple, route_dates, cost_mult):
    kwargs = dict(
        reset_dates=common.engine.monthly_dates(base.date), profit_multiple=multiple,
        profit_delay_sessions=1, open_exit_dates=route_dates,
    )
    if scope == "real":
        upstream = read(portfolio.full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.isin(base.date)].reset_index(drop=True)
        active = read(portfolio.full.BASE / "real_active.csv.gz")
        active = active[active.date.isin(base.date)].reset_index(drop=True)
        official_options = portfolio.full.engine.with_execution_prices(read(
            portfolio.full.BASE / "real_options.csv.gz", ("date", "contract_month", "rule_expiry", "actual_expiry")
        ))
        official_options = official_options[official_options.date.isin(base.date)].reset_index(drop=True)
        put, trades, _ = real_engine(upstream, official_options, active, schedule, "3m", 1.02, label, market=None, **kwargs)
    else:
        put, trades, _ = model_engine(market, schedule, "3m", 1.02, label, **kwargs)
    return scale_core_half(put, scope, cost_mult), trades


def combine_puts(core: pd.DataFrame, momentum: pd.DataFrame, cost_mult: float) -> pd.DataFrame:
    mom = momentum.copy()
    mom["put_cost_rate"] = mom.put_cost_rate.astype(float) * cost_mult
    total = core.copy()
    for field in PUT_FIELDS:
        total[field] = core[field].astype(float) + mom[field].astype(float)
    return total


def compose_routed(base, router, total_put, grid, call):
    d = base.copy()
    mom_units = 0.5 * d.momentum_weight.astype(float)
    unit_gross = d.base_futures_gross.astype(float) / d.base_units.astype(float)
    mom_gross = unit_gross * mom_units
    mom_cost = FUTURES_ONE_WAY * (
        mom_units.diff().fillna(mom_units).abs() + 2.0 * mom_units * d.roll_event.astype(float)
    )
    fixed_gross = 0.5 * router.router_pnl_ret.astype(float)
    fixed_cost = 0.5 * router.router_cost_rate.astype(float)

    for field in PUT_FIELDS:
        d[field] = total_put[field].to_numpy(dtype=float)
    for field in portfolio.full.comp.CALL_FIELDS:
        d[field] = call[field].to_numpy(dtype=float)
    for col in grid.columns:
        if col.startswith("overlay_") or col == "grid_carry":
            d[col] = grid[col].to_numpy()

    d["fixed_router_state"] = router.state.to_numpy()
    d["fixed_router_reserve"] = 0.15 * router.router_reserve_active.astype(float).to_numpy()
    d["fixed_router_futures_units"] = 0.5 * router.router_futures_active.astype(float).to_numpy()
    d["base_units"] = mom_units + d.fixed_router_futures_units
    d["total_units"] = d.base_units + d.overlay_held_eod
    d["futures_gross_ret"] = mom_gross + fixed_gross + d.overlay_gross_ret
    d["futures_cost_rate"] = mom_cost + fixed_cost + d.overlay_cost_rate
    d["cash_weight"] = (
        1.0 - 0.3 * (mom_units + d.overlay_held_eod)
        - d.fixed_router_reserve - d.put_mark_fraction - d.call_margin_fraction
    )
    if d.cash_weight.min() < -1e-12:
        raise RuntimeError(f"Negative routed cash weight: {d.cash_weight.min()}")
    d["ret"] = (
        (1 + d.futures_gross_ret + d.put_pnl_ret + d.call_pnl_ret)
        * (1 - d.futures_cost_rate) * (1 - d.put_cost_rate) * (1 - d.call_cost_rate)
        - 1 + d.cash_weight * CASH_DAILY
    )
    if not np.isfinite(d.ret).all() or d.ret.le(-1).any():
        raise RuntimeError("Invalid routed full-portfolio return")
    d["nav"] = (1 + d.ret).cumprod(); d["drawdown"] = d.nav / d.nav.cummax() - 1
    return d


def run_layer(scope, weights, router_fn, real_engine, model_engine, call_dir):
    base, base_audit = portfolio.rebuild_base(scope, weights)
    grid = portfolio.half_grid(scope)
    momentum_put, momentum_trades = portfolio.momentum_put(scope, base)
    market, router_base, options, options_for_put, futures, quarter_error = quarterly_router_inputs(scope, base)
    signal = maturity.prepare_signal(router_base, options, "m1")

    no_route, _, _ = router_fn(router_base, options, futures, signal, 9.99, common.FALLBACK, 0.60)
    no_route_error = float(np.max(np.abs(no_route.return_net.to_numpy() - router_base.baseline_plus_cash_ret.to_numpy())))
    if no_route_error > 1e-12:
        raise RuntimeError(f"Quarterly no-route parity failed {scope}: {no_route_error}")

    routed, route_events, cycles = router_fn(router_base, options, futures, signal, 0.35, common.FALLBACK, 0.60)
    routed["date"] = pd.to_datetime(routed.date)
    route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
    imc_mask = routed.set_index("date").state.eq("imc")
    always = pd.Series(True, index=pd.DatetimeIndex(base.date))

    prefix = "r" if scope == "real" else "m"
    call_normal, call_normal_trades, _ = maturity.call_inputs(scope, base.date, pd.Series(1.0, index=base.index), prefix + "fn", call_dir)
    call_route, call_route_trades, _ = maturity.call_inputs(scope, base.date, routed.state.eq("imc").astype(float), prefix + "fr", call_dir)

    variants = {
        "baseline": (always, None, frozenset(), False, call_normal),
        "profit3x_only": (always, 3.0, frozenset(), False, call_normal),
        "short95_only": (imc_mask, None, route_dates, True, call_route),
        "joint": (imc_mask, 3.0, route_dates, True, call_route),
    }
    daily_parts = []; trade_parts = [momentum_trades.assign(scope=scope, sleeve="momentum", candidate=f"{scope}_all")]
    audits = {}
    prior_daily = read(PRIOR / "daily_outputs" / "daily.csv.gz")
    for variant, (mask, multiple, exits, use_route, call_daily) in variants.items():
        schedule = valuation.corrected_core_schedule(base.date, scope, mask)
        label = f"{scope}_{variant}"
        core_1bp, trades = run_core(scope, base, market, options_for_put, schedule, real_engine, model_engine, label, multiple, exits, 1.0)
        if variant == "baseline":
            total_1bp = combine_puts(core_1bp, momentum_put, 1.0)
            parity_daily = portfolio.full.comp.compose(base, total_1bp, grid, call_normal)
            reference = prior_daily[prior_daily.candidate.eq(f"{scope}_baseline")].sort_values("date")
            parity_error = float(np.max(np.abs(parity_daily.ret.to_numpy() - reference.ret.to_numpy())))
            if parity_error > 1e-12:
                raise RuntimeError(f"Full baseline parity failed {scope}: {parity_error}")
        else:
            parity_error = None
        core = core_1bp.copy(); core["put_cost_rate"] *= PUT_COST_MULTIPLIER
        total = combine_puts(core, momentum_put, PUT_COST_MULTIPLIER)
        if use_route:
            daily = compose_routed(base, routed, total, grid, call_daily)
        else:
            daily = portfolio.full.comp.compose(base, total, grid, call_daily)
        daily["candidate"] = label; daily["scope"] = scope; daily["variant"] = variant
        daily_parts.append(daily); trade_parts.append(trades.assign(scope=scope, sleeve="core", candidate=label))
        route_exits = trades[trades.action.eq("route_open_exit")]
        profits = trades[trades.action.eq("close_profit_restrike")]
        duplicates = set(route_exits.actual_execution_date) & set(profits.actual_execution_date)
        if duplicates:
            raise RuntimeError(f"Duplicate route/profit exits {label}: {duplicates}")
        audits[variant] = {
            "full_baseline_1bp_parity_error": parity_error,
            "route_switches": len(route_dates) if use_route else 0,
            "short_put_cycles": len(cycles) if use_route else 0,
            "profit_restrikes": int(len(profits)),
            "core_put_route_exits": int(len(route_exits)),
            "duplicate_route_profit_days": int(len(duplicates)),
            "min_cash_weight": float(daily.cash_weight.min()),
            "call_trade_events": int(len(call_route_trades if use_route else call_normal_trades)),
        }
    layer_audit = {
        "base": base_audit, "quarter_unit_gross_error": quarter_error,
        "quarter_no_route_parity_error": no_route_error, "variants": audits,
    }
    return pd.concat(daily_parts, ignore_index=True), pd.concat(trade_parts, ignore_index=True), signal, cycles, layer_audit


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    weights = portfolio.current_momentum_weights()
    router_fn, router_source = audited_router()
    real_engine, model_engine, engine_source = component.profit_route_engines()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    daily_parts = []; trade_parts = []; signal_parts = []; cycle_parts = []; audits = {}
    for scope in ("real", "model"):
        daily, trades, signal, cycles, audit = run_layer(scope, weights, router_fn, real_engine, model_engine, call_dir)
        daily_parts.append(daily); trade_parts.append(trades); signal_parts.append(signal.assign(scope=scope)); audits[scope] = audit
        if len(cycles): cycle_parts.append(cycles.assign(scope=scope))
    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True)
    summary, wide, unavailable = portfolio.metric_rows(daily)
    full = summary[summary.segment.eq("full")].copy()

    paired = []; passed = True
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        rows = {v: block.loc[f"{scope}_{v}"] for v in ("baseline", "profit3x_only", "short95_only", "joint")}
        best_single = max(rows["profit3x_only"].ann_return, rows["short95_only"].ann_return)
        worst_dd = min(rows["profit3x_only"].max_dd, rows["short95_only"].max_dd)
        return_gate = rows["joint"].ann_return >= best_single - 1e-12
        dd_gate = rows["joint"].max_dd >= worst_dd - 0.01 - 1e-12
        passed = passed and return_gate and dd_gate
        paired.append({
            "scope": scope, "joint_ann_return": rows["joint"].ann_return,
            "best_single_ann_return": best_single, "joint_minus_best_single": rows["joint"].ann_return - best_single,
            "joint_max_dd": rows["joint"].max_dd, "worst_single_max_dd": worst_dd,
            "return_gate": return_gate, "drawdown_gate": dd_gate,
        })
    paired = pd.DataFrame(paired)
    integrity = all(
        audits[s]["quarter_no_route_parity_error"] <= 1e-12
        and audits[s]["variants"]["baseline"]["full_baseline_1bp_parity_error"] <= 1e-12
        and audits[s]["variants"]["joint"]["duplicate_route_profit_days"] == 0
        for s in ("real", "model")
    )
    passed = passed and integrity
    decision = "retain_full_joint_as_research_promotion_candidate_no_production_change" if passed else "do_not_promote_full_joint_keep_best_single_no_production_change"
    stability = "full_portfolio_cross_layer_gate_passed_real_events_sparse" if passed else "full_portfolio_joint_gate_failed"

    annual_rows = []
    for (candidate, year), group in daily.groupby(["candidate", daily.date.dt.year], sort=True):
        annual_rows.append({"candidate": candidate, "scope": group.scope.iloc[0], "variant": group.variant.iloc[0], "year": int(year), "annual_return": float((1 + group.ret).prod() - 1)})
    annual = pd.DataFrame(annual_rows)

    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv", index=False)
    pd.concat(signal_parts, ignore_index=True).to_csv(out / "signals.csv", index=False)
    (pd.concat(cycle_parts, ignore_index=True) if cycle_parts else pd.DataFrame()).to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False)
    annual.to_csv(RUN / "annual_attribution.csv", index=False)
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + engine_source, encoding="utf-8")

    meta.update({
        "scan_type": "full_IM_v1_3_fixed_core_short95_m1_iv35_decay60_plus_profit3x",
        "baseline": {"candidate": ["real_baseline", "model_baseline"], "parity": "prior corrected full portfolio at original 1bp before 5bp cost reset"},
        "candidate_grid": [{"variant": x} for x in ("baseline", "profit3x_only", "short95_only", "joint")],
        "data_snapshot": {"real_start": "2022-07-22", "real_end": "2026-08-14", "model_start": "2015-04-16", "model_end": "2026-08-14"},
        "cost_model": {"all_mo_put_one_way": 0.0005, "put_round_trip": 0.001, "futures_one_way": FUTURES_ONE_WAY, "call": "existing validated cost", "futures_buffer_per_1x": 0.30, "fixed_short_put_reserve": 0.15, "cash_annual": 0.03},
        "component_scope": {"routed": "fixed core 0.5x only", "unchanged": ["momentum futures", "momentum Put", "0.5x 1.6/2.0 grid"], "call": "fixed-core Call paused outside IMC"},
        "audit": audits, "paired_gate": paired.to_dict("records"), "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv"), "signals": str(out / "signals.csv"), "cycles": str(out / "cycles.csv"), "paired": str(RUN / "paired_comparison.csv"), "annual": str(RUN / "annual_attribution.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "quarter_chain": sha(CHAIN / "quarter1_bridged_upstream.csv.gz"), "official_futures": sha(common.router.FUTURES)},
        "warnings": ["Research-only counterfactual replay; production ledgers unchanged.", "Model MO options and recovery futures are theoretical proxies, not executable listed history.", "Real route/profit events are sparse.", "No dynamic margin, forced liquidation, tax, capacity, bid-ask depth, or integer account sizing."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.3完整组合：高IV卖Put＋核心Put三倍兑现\n\n"
        "## Data\n\n真实挂牌层2022-07-22至2026-08-14；理论延展层2015-04-16至2026-08-14。完整组合使用季度T-1链，真实与理论严格分层。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Joint Gate\n\n" + paired.to_markdown(index=False) +
        "\n\n## Audit\n\n```json\n" + json.dumps(audits, ensure_ascii=False, indent=2) +
        "\n```\n\n## Annual Attribution\n\n" + annual.to_markdown(index=False) +
        "\n\n## Decision\n\n" + decision + "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(paired.to_string(index=False)); print(json.dumps(audits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
