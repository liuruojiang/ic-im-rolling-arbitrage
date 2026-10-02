"""IM v1.3 full-portfolio scan of short-95 Put quantity (2/3/6 MO per IM)."""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v13_full_short95_profit3x_joint_v1 as prior
import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_short95_quantity_redteam_v2"
SPEC = ROOT / "docs" / "im_v14_redteam_correction_rerun_v1_spec.md"
REFERENCE = prior.RUN
PUT_FIELDS = prior.PUT_FIELDS
PUT_COST_MULTIPLIER = prior.PUT_COST_MULTIPLIER
CASH_DAILY = prior.CASH_DAILY
FUTURES_ONE_WAY = prior.FUTURES_ONE_WAY
SCALES = {
    "q2_notional": 1.0,
    "q3_delta05": 1.5,
    "q6_delta10_stress": 3.0,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def sized_router_factory():
    base_router, base_source = prior.audited_router()
    del base_router
    source = base_source
    old_sig = "def run_router_decay(base: pd.DataFrame, options: pd.DataFrame, futures: pd.DataFrame, signal: pd.DataFrame, threshold: float, fallback: str, early_roll_threshold: float | None = None)"
    new_sig = old_sig[:-1] + ", short_scale: float = 1.0)"
    if old_sig not in source:
        raise RuntimeError("Router signature changed")
    source = source.replace(old_sig, new_sig, 1)
    unit_line = "units = previous_equity / (float(base.iloc[i - 1].csi1000_price_close) * 200)"
    if source.count(unit_line) != 2:
        raise RuntimeError("Router short-Put sizing hooks changed")
    source = source.replace(unit_line, "units = short_scale * previous_equity / (float(base.iloc[i - 1].csi1000_price_close) * 200)")
    namespace = dict(vars(prior.common.router))
    namespace["OPTION_ONE_WAY_COST"] = prior.component.OPTION_ONE_WAY_COST
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    raw = namespace["run_router_decay"]

    def wrapped(base, options, futures, signal, threshold, fallback, decay, short_scale=1.0):
        daily, events, cycles = raw(base, options, futures, signal, threshold, fallback, decay, short_scale)
        daily["early_roll_executed"] = daily.action.eq("put_early_roll60_buyback_and_sell_next_open")
        return daily, events, cycles

    return wrapped, source


def compose_sized(base, router, total_put, grid, call, short_scale):
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
    state = router.state.astype(str).to_numpy()
    d["fixed_router_state"] = state
    d["short_scale"] = short_scale
    reserve_active = router.router_reserve_active.astype(float).to_numpy()
    # Normal 0.5x fixed-core IM always keeps its original 15% buffer.  The
    # quantity multiplier applies only after routing into the short-Put / pending
    # assignment / enlarged recovery-IM path.
    d["fixed_router_reserve"] = np.where(
        state == "imc", 0.15, 0.15 * short_scale * reserve_active
    )
    d["fixed_router_futures_units"] = np.where(
        state == "imc", 0.5, np.where(state == "recovery_im", 0.5 * short_scale, 0.0)
    )
    d["base_units"] = mom_units + d.fixed_router_futures_units
    d["total_units"] = d.base_units + d.overlay_held_eod
    d["futures_gross_ret"] = mom_gross + fixed_gross + d.overlay_gross_ret
    d["futures_cost_rate"] = mom_cost + fixed_cost + d.overlay_cost_rate
    d["cash_weight"] = (
        1.0 - 0.3 * (mom_units + d.overlay_held_eod)
        - d.fixed_router_reserve - d.put_mark_fraction - d.call_margin_fraction
    )
    d["ret"] = (
        (1 + d.futures_gross_ret + d.put_pnl_ret + d.call_pnl_ret)
        * (1 - d.futures_cost_rate) * (1 - d.put_cost_rate) * (1 - d.call_cost_rate)
        - 1 + d.cash_weight * CASH_DAILY
    )
    if not np.isfinite(d.ret).all() or d.ret.le(-1).any():
        raise RuntimeError("Invalid sized full-portfolio return")
    d["nav"] = (1 + d.ret).cumprod()
    d["drawdown"] = d.nav / d.nav.cummax() - 1
    d["capital_feasible"] = d.cash_weight >= -1e-12
    return d


def run_layer(scope, weights, router_fn, real_engine, model_engine, call_dir):
    base, base_audit = portfolio.rebuild_base(scope, weights)
    grid = portfolio.half_grid(scope)
    momentum_put, momentum_trades = portfolio.momentum_put(scope, base)
    market, router_base, options, options_for_put, futures, quarter_error = prior.quarterly_router_inputs(scope, base)
    signal = maturity.prepare_signal(router_base, options, "m1")
    no_route, _, _ = router_fn(router_base, options, futures, signal, 9.99, prior.common.FALLBACK, 0.60, 1.0)
    no_route_error = float(np.max(np.abs(no_route.return_net.to_numpy() - router_base.baseline_plus_cash_ret.to_numpy())))
    if no_route_error > 1e-12:
        raise RuntimeError(f"Quarterly no-route parity failed {scope}: {no_route_error}")

    prefix = "r" if scope == "real" else "m"
    call_normal, call_normal_trades, _ = maturity.call_inputs(
        scope, base.date, pd.Series(1.0, index=base.index), prefix + "qn", call_dir
    )
    always = pd.Series(True, index=pd.DatetimeIndex(base.date))
    daily_parts = []
    trade_parts = [momentum_trades.assign(scope=scope, sleeve="momentum", candidate=f"{scope}_all")]
    cycle_parts = []
    audits = {}
    reference = prior.read(REFERENCE / "daily_outputs" / "daily.csv.gz")

    for variant, multiple in (("baseline", None), ("profit3x_only", 3.0)):
        schedule = valuation.corrected_core_schedule(base.date, scope, always)
        label = f"{scope}_{variant}"
        core_1bp, trades = prior.run_core(
            scope, base, market, options_for_put, schedule, real_engine, model_engine,
            label, multiple, frozenset(), 1.0,
        )
        core = core_1bp.copy(); core["put_cost_rate"] *= PUT_COST_MULTIPLIER
        total = prior.combine_puts(core, momentum_put, PUT_COST_MULTIPLIER)
        daily = portfolio.full.comp.compose(base, total, grid, call_normal)
        daily["candidate"] = label; daily["scope"] = scope; daily["variant"] = variant
        daily["quantity_label"] = "none"; daily["short_scale"] = 0.0; daily["capital_feasible"] = True
        ref = reference[reference.candidate.eq(label)].sort_values("date")
        parity = float(np.max(np.abs(daily.ret.to_numpy() - ref.ret.to_numpy())))
        if parity > 1e-12:
            raise RuntimeError(f"Reference parity failed {label}: {parity}")
        daily_parts.append(daily)
        trade_parts.append(trades.assign(scope=scope, sleeve="core", candidate=label))
        audits[variant] = {
            "reference_daily_parity_error": parity,
            "min_cash_weight": float(daily.cash_weight.min()),
            "capital_feasible": True,
            "profit_restrikes": int(trades.action.eq("close_profit_restrike").sum()),
            "call_trade_events": int(len(call_normal_trades)),
        }

    call_route = None
    call_route_trades = None
    for quantity_label, short_scale in SCALES.items():
        routed, _, cycles = router_fn(
            router_base, options, futures, signal, 0.35, prior.common.FALLBACK, 0.60, short_scale
        )
        routed["date"] = pd.to_datetime(routed.date)
        route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
        imc_mask = routed.set_index("date").state.eq("imc")
        if call_route is None:
            call_route, call_route_trades, _ = maturity.call_inputs(
                scope, base.date, routed.state.eq("imc").astype(float), prefix + "qr", call_dir
            )
        for variant, multiple in (("short95_only", None), ("joint", 3.0)):
            schedule = valuation.corrected_core_schedule(base.date, scope, imc_mask)
            label = f"{scope}_{variant}_{quantity_label}"
            core_1bp, trades = prior.run_core(
                scope, base, market, options_for_put, schedule, real_engine, model_engine,
                label, multiple, route_dates, 1.0,
            )
            core = core_1bp.copy(); core["put_cost_rate"] *= PUT_COST_MULTIPLIER
            total = prior.combine_puts(core, momentum_put, PUT_COST_MULTIPLIER)
            daily = compose_sized(base, routed, total, grid, call_route, short_scale)
            daily["candidate"] = label; daily["scope"] = scope; daily["variant"] = variant
            daily["quantity_label"] = quantity_label
            parity = None
            if quantity_label == "q2_notional":
                old_label = f"{scope}_{variant}"
                ref = reference[reference.candidate.eq(old_label)].sort_values("date")
                parity = float(np.max(np.abs(daily.ret.to_numpy() - ref.ret.to_numpy())))
                if parity > 1e-12:
                    raise RuntimeError(f"q2 parity failed {label}: {parity}")
            route_exits = trades[trades.action.eq("route_open_exit")]
            profits = trades[trades.action.eq("close_profit_restrike")]
            duplicates = set(route_exits.actual_execution_date) & set(profits.actual_execution_date)
            if duplicates:
                raise RuntimeError(f"Duplicate route/profit exits {label}: {duplicates}")
            daily_parts.append(daily)
            trade_parts.append(trades.assign(scope=scope, sleeve="core", candidate=label))
            audits[f"{variant}_{quantity_label}"] = {
                "q2_reference_daily_parity_error": parity,
                "route_switches": len(route_dates),
                "short_put_cycles": len(cycles),
                "profit_restrikes": int(len(profits)),
                "core_put_route_exits": int(len(route_exits)),
                "duplicate_route_profit_days": int(len(duplicates)),
                "min_cash_weight": float(daily.cash_weight.min()),
                "negative_cash_days": int((daily.cash_weight < -1e-12).sum()),
                "capital_feasible": bool(daily.capital_feasible.all()),
                "call_trade_events": int(len(call_route_trades)),
                "max_total_futures_units": float(daily.total_units.max()),
            }
        if len(cycles):
            cycle_parts.append(cycles.assign(scope=scope, quantity_label=quantity_label, short_scale=short_scale))

    return (
        pd.concat(daily_parts, ignore_index=True), pd.concat(trade_parts, ignore_index=True),
        signal, pd.concat(cycle_parts, ignore_index=True) if cycle_parts else pd.DataFrame(),
        {"base": base_audit, "quarter_unit_gross_error": quarter_error,
         "quarter_no_route_parity_error": no_route_error, "variants": audits},
    )


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    weights = portfolio.current_momentum_weights()
    router_fn, router_source = sized_router_factory()
    real_engine, model_engine, engine_source = prior.component.profit_route_engines()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    daily_parts = []; trade_parts = []; signal_parts = []; cycle_parts = []; audits = {}
    for scope in ("real", "model"):
        daily, trades, signal, cycles, audit = run_layer(
            scope, weights, router_fn, real_engine, model_engine, call_dir
        )
        daily_parts.append(daily); trade_parts.append(trades); signal_parts.append(signal.assign(scope=scope))
        if len(cycles): cycle_parts.append(cycles)
        audits[scope] = audit
    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True)
    cycles = pd.concat(cycle_parts, ignore_index=True)
    summary, wide, unavailable = portfolio.metric_rows(daily)
    full = summary[summary.segment.eq("full")].copy()

    comparison_rows = []
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        for quantity_label, short_scale in SCALES.items():
            for variant in ("short95_only", "joint"):
                label = f"{scope}_{variant}_{quantity_label}"
                q2 = f"{scope}_{variant}_q2_notional"
                audit = audits[scope]["variants"][f"{variant}_{quantity_label}"]
                comparison_rows.append({
                    "scope": scope, "variant": variant, "quantity_label": quantity_label,
                    "mo_per_1_im": int(round(2 * short_scale)),
                    "ann_return": float(block.loc[label].ann_return),
                    "ann_return_minus_q2": float(block.loc[label].ann_return - block.loc[q2].ann_return),
                    "max_dd": float(block.loc[label].max_dd),
                    "max_dd_minus_q2": float(block.loc[label].max_dd - block.loc[q2].max_dd),
                    "min_cash_weight": audit["min_cash_weight"],
                    "negative_cash_days": audit["negative_cash_days"],
                    "capital_feasible": audit["capital_feasible"],
                })
    comparison = pd.DataFrame(comparison_rows)
    annual_rows = []
    for (candidate, year), group in daily.groupby(["candidate", daily.date.dt.year], sort=True):
        annual_rows.append({"candidate": candidate, "scope": group.scope.iloc[0],
                            "variant": group.variant.iloc[0], "quantity_label": group.quantity_label.iloc[0],
                            "year": int(year), "annual_return": float((1 + group.ret).prod() - 1)})
    annual = pd.DataFrame(annual_rows)

    feasible = comparison[comparison.capital_feasible]
    q3_joint = feasible[(feasible.quantity_label == "q3_delta05") & (feasible.variant == "joint")]
    q2_joint = feasible[(feasible.quantity_label == "q2_notional") & (feasible.variant == "joint")]
    promote_q3 = (
        len(q3_joint) == 2 and len(q2_joint) == 2
        and (q3_joint.ann_return_minus_q2 > 0).all()
        and (q3_joint.max_dd_minus_q2 >= -0.01).all()
    )
    decision = "retain_q3_as_research_candidate_no_production_change" if promote_q3 else "keep_q2_notional_reject_higher_quantity_no_production_change"
    stability = "cross_layer_q3_feasible_and_incremental" if promote_q3 else "higher_quantity_not_cross_layer_robust_or_capital_feasible"

    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    pd.concat(signal_parts, ignore_index=True).to_csv(out / "signals.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "quantity_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + engine_source, encoding="utf-8")
    meta.update({
        "scan_type": "candidate_bundle",
        "baseline": {"candidate": ["real_baseline", "model_baseline"], "reference": str(REFERENCE)},
        "candidate_grid": [{"quantity_label": k, "short_scale": v, "mo_per_1_im": 2 * v} for k, v in SCALES.items()],
        "data_snapshot": {"real_start": "2022-07-22", "real_end": "2026-08-14", "model_start": "2015-04-16", "model_end": "2026-08-14"},
        "cost_model": {"all_mo_put_one_way": 0.0005, "put_round_trip": 0.001,
                       "futures_one_way": FUTURES_ONE_WAY, "cash_annual": 0.03,
                       "fixed_short_put_reserve": "15% times short_scale",
                       "futures_buffer_per_1x": 0.30},
        "audit": audits, "quantity_comparison": comparison.to_dict("records"),
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"),
                    "trades": str(out / "trades.csv"), "cycles": str(out / "cycles.csv"),
                    "signals": str(out / "signals.csv"), "comparison": str(RUN / "quantity_comparison.csv"),
                    "annual": str(RUN / "annual_attribution.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC),
                          "reference_daily": sha(REFERENCE / "daily_outputs" / "daily.csv.gz")},
        "warnings": ["Research-only counterfactual replay; production unchanged.",
                     "Model layer uses theoretical option/recovery proxies.",
                     "Real short-Put route events are sparse.",
                     "q3 and q6 use continuous quantities; no integer account sizing.",
                     "Negative cash candidates are unconstrained diagnostics without forced liquidation."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.3高IV卖Put张数与初始Delta口径补测\n\n"
        "## Run Metadata\n\n研究专用；生产与账本未修改。\n\n"
        "## Research Question\n\n比较每1张IM卖2/3/6张MO Put。\n\n"
        "## Implementation Anchor\n\n完整组合复算脚本与上一轮逐日结果双重校验。\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-07-22至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Cost and Execution Assumptions\n\nMO Put单边5BP；期货单边1BP；资金缓冲随卖Put张数线性增加。\n\n"
        "## Runtime Override Plan\n\n只在研究路由器运行时覆盖卖Put数量，不修改生产源码。\n\n"
        "## Commands\n\n见command_log.txt。\n\n"
        "## Output Files\n\n见scan_meta.json。\n\n"
        "## Full-Sample Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Quantity Comparison\n\n" + comparison.to_markdown(index=False) +
        "\n\n## Window Results\n\n详见scan_summary.csv与window_metrics.csv。\n\n"
        "## Annual Attribution\n\n" + annual.to_markdown(index=False) +
        "\n\n## Stability Classification\n\n" + stability +
        "\n\n## Decision\n\n" + decision +
        "\n\n## User-Facing Summary\n\n张数提高是否值得，以真实/理论两层、回撤和资金可行性共同判断。\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(comparison.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
