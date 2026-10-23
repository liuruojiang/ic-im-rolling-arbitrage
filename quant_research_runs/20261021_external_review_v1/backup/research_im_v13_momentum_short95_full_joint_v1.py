"""Full IM v1.3 test of routing the momentum sleeve into high-IV short Put."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v13_full_short95_profit3x_joint_v1 as prior
import research_im_v13_short95_quantity_delta_scan_v1 as quantity


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_momentum_short95_full_joint_q3_redteam_v3"
SPEC = ROOT / "docs" / "im_v14_redteam_correction_rerun_v1_spec.md"
REFERENCE = quantity.RUN
SHORT_SCALE = 1.5
PUT_COST_MULTIPLIER = 5.0
FUTURES_ONE_WAY = prior.FUTURES_ONE_WAY
PUT_FIELDS = prior.PUT_FIELDS


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
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def route_scale(router: pd.DataFrame, base: pd.DataFrame) -> tuple[pd.Series, dict]:
    weights = base.set_index("date").momentum_weight.astype(float)
    values = []
    active = 0.0
    entries = []
    boundary_errors = []
    previous_units = 0.5 * base.momentum_weight.astype(float)
    for i, row in enumerate(router.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        if str(row.route) == "high_iv_permitted_short_put":
            active = 0.5 * float(weights.loc[day])
            if active <= 0:
                raise RuntimeError(f"Momentum route opened without positive weight {day}")
            prior_units = float(previous_units.iloc[i - 1]) if i else 0.0
            boundary_errors.append(abs(prior_units - active))
            entries.append({"date": day, "entry_scale": active, "prior_momentum_units": prior_units})
        values.append(active)
        if active > 0 and str(row.state) == "imc" and str(row.action) == "cash_to_imc_open":
            next_units = float(previous_units.iloc[i + 1]) if i + 1 < len(previous_units) else active
            boundary_errors.append(abs(next_units - active))
            active = 0.0
    series = pd.Series(values, index=router.index, dtype=float)
    return series, {
        "entries": entries,
        "max_entry_or_restore_weight_mismatch": max(boundary_errors) if boundary_errors else 0.0,
        "active_days": int(series.gt(0).sum()),
        "entry_count": len(entries),
    }


def momentum_put_masked(scope: str, base: pd.DataFrame, route_active: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    portfolio = prior.portfolio
    full = portfolio.full
    template = prior.read(portfolio.ARTIFACT / f"{scope}_combined_mom_schedule.csv.gz", ("eval_date", "execution_date"))
    template = template[template.execution_date.le(portfolio.END)].reset_index(drop=True)
    state = prior.read(full.BASE / "valuation_state_through_last_required_eval.csv.gz").set_index("date")
    mom120 = template.eval_date.map(state.momentum_120)
    parent = np.where(mom120.lt(0), 3, 0)
    target = parent * 2.0 * base.momentum_weight.to_numpy(dtype=float) * 4.0
    schedule = template.copy()
    schedule["binary_target_qty"] = np.rint(target).astype(int)
    active_by_date = pd.Series(route_active.to_numpy(bool), index=pd.DatetimeIndex(base.date))
    blocked = schedule.execution_date.map(active_by_date).fillna(False).astype(bool)
    schedule.loc[blocked, "binary_target_qty"] = 0
    schedule["three_tier_target_qty"] = schedule.binary_target_qty
    schedule["momentum_120"] = mom120.to_numpy(dtype=float)
    schedule["mom120_active"] = mom120.lt(0).to_numpy(dtype=bool)
    schedule["put_buy_allowed"] = True
    if scope == "model":
        market = prior.read(full.BASE / "model_market.csv.gz")
        market = market[market.date.le(portfolio.END)].reset_index(drop=True)
        put, trades, _ = full.engine.run_model_monthly_close(
            market, schedule, "3m", 1.02, f"{scope}_momentum_put102_route_masked",
            reset_dates=full.engine.monthly_dates(base.date),
        )
    else:
        upstream = prior.read(full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.le(portfolio.END)].reset_index(drop=True)
        active = prior.read(full.BASE / "real_active.csv.gz")
        active = active[active.date.le(portfolio.END)].reset_index(drop=True)
        options = full.engine.with_execution_prices(prior.read(
            full.BASE / "real_options.csv.gz", ("date", "contract_month", "rule_expiry", "actual_expiry")
        ))
        options = options[options.date.le(portfolio.END)].reset_index(drop=True)
        put, trades, _ = full.engine.run_real_monthly_close(
            upstream, options, active, schedule, "3m", 1.02,
            f"{scope}_momentum_put102_route_masked", reset_dates=full.engine.monthly_dates(base.date),
        )
    put[PUT_FIELDS] = put[PUT_FIELDS] * (0.25 / 4.0)
    return put, trades


def compose_dual(base: pd.DataFrame, fixed_router: pd.DataFrame | None,
                 momentum_router: pd.DataFrame | None, momentum_scale: pd.Series,
                 total_put: pd.DataFrame, grid: pd.DataFrame, call: pd.DataFrame) -> pd.DataFrame:
    d = base.copy()
    unit_gross = d.base_futures_gross.astype(float) / d.base_units.astype(float)
    original_mom_units = 0.5 * d.momentum_weight.astype(float)

    if fixed_router is None:
        fixed_units = pd.Series(0.5, index=d.index)
        fixed_gross = unit_gross * fixed_units
        fixed_cost = FUTURES_ONE_WAY * (
            fixed_units.diff().fillna(fixed_units).abs() + 2.0 * fixed_units * d.roll_event.astype(float)
        )
        fixed_reserve = pd.Series(0.15, index=d.index)
    else:
        state = fixed_router.state.astype(str)
        fixed_gross = 0.5 * fixed_router.router_pnl_ret.astype(float)
        fixed_cost = 0.5 * fixed_router.router_cost_rate.astype(float)
        fixed_units = pd.Series(np.where(state.eq("imc"), 0.5, np.where(state.eq("recovery_im"), 0.5 * SHORT_SCALE, 0.0)), index=d.index)
        fixed_reserve = pd.Series(np.where(state.eq("imc"), 0.15, 0.15 * SHORT_SCALE * fixed_router.router_reserve_active.astype(float)), index=d.index)

    if momentum_router is None:
        mom_units = original_mom_units
        mom_gross = unit_gross * mom_units
        mom_cost = FUTURES_ONE_WAY * (
            mom_units.diff().fillna(mom_units).abs() + 2.0 * mom_units * d.roll_event.astype(float)
        )
        mom_reserve = 0.3 * mom_units
    else:
        q = momentum_scale.reset_index(drop=True).astype(float)
        active = q.gt(0)
        normal_units = original_mom_units.where(~active, 0.0)
        normal_turnover = normal_units.diff().fillna(normal_units).abs()
        transitions = active.ne(active.shift(fill_value=False))
        normal_turnover = normal_turnover.where(~transitions, 0.0)
        normal_cost = FUTURES_ONE_WAY * (normal_turnover + 2.0 * normal_units * d.roll_event.astype(float))
        mom_gross = unit_gross * normal_units + q * momentum_router.router_pnl_ret.astype(float)
        mom_cost = normal_cost + q * momentum_router.router_cost_rate.astype(float)
        rstate = momentum_router.state.astype(str)
        routed_units = np.where(rstate.eq("imc"), q, np.where(rstate.eq("recovery_im"), q * SHORT_SCALE, 0.0))
        mom_units = normal_units + routed_units
        route_reserve = np.where(rstate.eq("imc"), 0.3 * q, 0.3 * q * SHORT_SCALE * momentum_router.router_reserve_active.astype(float))
        mom_reserve = 0.3 * normal_units + route_reserve

    for field in PUT_FIELDS:
        d[field] = total_put[field].to_numpy(dtype=float)
    for field in prior.portfolio.full.comp.CALL_FIELDS:
        d[field] = call[field].to_numpy(dtype=float)
    for col in grid.columns:
        if col.startswith("overlay_") or col == "grid_carry":
            d[col] = grid[col].to_numpy()
    d["fixed_router_state"] = "normal" if fixed_router is None else fixed_router.state.to_numpy()
    d["momentum_router_state"] = "normal" if momentum_router is None else momentum_router.state.to_numpy()
    d["momentum_route_scale"] = momentum_scale.to_numpy(dtype=float)
    d["base_units"] = fixed_units.to_numpy(dtype=float) + np.asarray(mom_units, dtype=float)
    d["total_units"] = d.base_units + d.overlay_held_eod
    d["futures_gross_ret"] = fixed_gross.to_numpy(dtype=float) + np.asarray(mom_gross, dtype=float) + d.overlay_gross_ret
    d["futures_cost_rate"] = fixed_cost.to_numpy(dtype=float) + np.asarray(mom_cost, dtype=float) + d.overlay_cost_rate
    d["cash_weight"] = 1.0 - fixed_reserve.to_numpy(dtype=float) - np.asarray(mom_reserve, dtype=float) - 0.3 * d.overlay_held_eod - d.put_mark_fraction - d.call_margin_fraction
    if d.cash_weight.min() < -1e-12:
        raise RuntimeError(f"Negative cash weight {d.cash_weight.min()}")
    d["ret"] = (
        (1 + d.futures_gross_ret + d.put_pnl_ret + d.call_pnl_ret)
        * (1 - d.futures_cost_rate) * (1 - d.put_cost_rate) * (1 - d.call_cost_rate)
        - 1 + d.cash_weight * prior.CASH_DAILY
    )
    if not np.isfinite(d.ret).all() or d.ret.le(-1).any():
        raise RuntimeError("Invalid dual-router returns")
    d["nav"] = (1 + d.ret).cumprod()
    d["drawdown"] = d.nav / d.nav.cummax() - 1
    return d


def run_layer(scope: str, weights: pd.Series, router_fn, real_engine, model_engine, call_dir: Path):
    base, base_audit = prior.portfolio.rebuild_base(scope, weights)
    grid = prior.portfolio.half_grid(scope)
    market, router_base, options, options_for_put, futures, quarter_error = prior.quarterly_router_inputs(scope, base)
    signal = prior.maturity.prepare_signal(router_base, options, "m1")
    signal["momentum_weight"] = signal.execution_date.map(base.set_index("date").momentum_weight)
    momentum_signal = signal.copy()
    momentum_signal["short_put_permission"] = momentum_signal.short_put_permission.astype(bool) & momentum_signal.momentum_weight.gt(0)

    fixed_router, fixed_events, fixed_cycles = router_fn(router_base, options, futures, signal, 0.35, prior.common.FALLBACK, 0.60, SHORT_SCALE)
    momentum_router, momentum_events, momentum_cycles = router_fn(router_base, options, futures, momentum_signal, 0.35, prior.common.FALLBACK, 0.60, SHORT_SCALE)
    fixed_router["date"] = pd.to_datetime(fixed_router.date); momentum_router["date"] = pd.to_datetime(momentum_router.date)
    momentum_scale, scale_audit = route_scale(momentum_router, base)
    momentum_active = momentum_scale.gt(0)
    fixed_active = fixed_router.state.ne("imc")
    fixed_dates = set(fixed_router.loc[fixed_router.route.eq("high_iv_permitted_short_put"), "date"])

    always = pd.Series(True, index=pd.DatetimeIndex(base.date))
    fixed_mask = fixed_router.set_index("date").state.eq("imc")
    momentum_put_normal, momentum_trades_normal = prior.portfolio.momentum_put(scope, base)
    momentum_put_mask, momentum_trades_mask = momentum_put_masked(scope, base, momentum_active)
    prefix = "r" if scope == "real" else "m"

    variants = {
        "profit3x_no_short": (False, False),
        "fixed_only_q3": (True, False),
        "momentum_only_q3": (False, True),
        "both_q3": (True, True),
    }
    daily_parts, trade_parts, audits = [], [], {}
    for variant, (use_fixed, use_momentum) in variants.items():
        any_active = (fixed_active if use_fixed else pd.Series(False, index=base.index)) | (momentum_active if use_momentum else pd.Series(False, index=base.index))
        call_daily, call_trades, _ = prior.maturity.call_inputs(scope, base.date, (~any_active).astype(float), prefix + variant[:2], call_dir)
        schedule = prior.valuation.corrected_core_schedule(base.date, scope, fixed_mask if use_fixed else always)
        exits = fixed_dates if use_fixed else frozenset()
        core_1bp, core_trades = prior.run_core(scope, base, market, options_for_put, schedule, real_engine, model_engine, f"{scope}_{variant}", 3.0, exits, 1.0)
        core = core_1bp.copy(); core["put_cost_rate"] *= PUT_COST_MULTIPLIER
        momentum_put = momentum_put_mask if use_momentum else momentum_put_normal
        total = prior.combine_puts(core, momentum_put, PUT_COST_MULTIPLIER)
        daily = compose_dual(
            base, fixed_router if use_fixed else None,
            momentum_router if use_momentum else None,
            momentum_scale if use_momentum else pd.Series(0.0, index=base.index),
            total, grid, call_daily,
        )
        daily["candidate"] = f"{scope}_{variant}"; daily["scope"] = scope; daily["variant"] = variant
        daily_parts.append(daily)
        trade_parts.append(core_trades.assign(scope=scope, sleeve="core", candidate=f"{scope}_{variant}"))
        trade_parts.append((momentum_trades_mask if use_momentum else momentum_trades_normal).assign(scope=scope, sleeve="momentum", candidate=f"{scope}_{variant}"))
        audits[variant] = {
            "fixed_route_cycles": int(len(fixed_cycles)) if use_fixed else 0,
            "momentum_route_cycles": int(len(momentum_cycles)) if use_momentum else 0,
            "momentum_route_entries": scale_audit["entry_count"] if use_momentum else 0,
            "momentum_route_active_days": scale_audit["active_days"] if use_momentum else 0,
            "momentum_boundary_weight_mismatch": scale_audit["max_entry_or_restore_weight_mismatch"] if use_momentum else 0.0,
            "min_cash_weight": float(daily.cash_weight.min()),
            "call_trade_events": int(len(call_trades)),
            "core_route_exits": int(core_trades.action.eq("route_open_exit").sum()),
            "core_profit_restrikes": int(core_trades.action.eq("close_profit_restrike").sum()),
        }

    reference = prior.read(REFERENCE / "daily_outputs" / "daily.csv.gz")
    fixed = pd.concat(daily_parts, ignore_index=True)
    current = fixed[fixed.candidate.eq(f"{scope}_fixed_only_q3")].sort_values("date")
    ref = reference[reference.candidate.eq(f"{scope}_joint_q3_delta05")].sort_values("date")
    parity = float(np.max(np.abs(current.ret.to_numpy() - ref.ret.to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"Existing q3 parity failed {scope}: {parity}")
    return fixed, pd.concat(trade_parts, ignore_index=True), signal, momentum_signal, fixed_cycles, momentum_cycles, {
        "base": base_audit, "quarter_unit_gross_error": quarter_error,
        "existing_fixed_q3_parity_error": parity, "momentum_scale": scale_audit,
        "variants": audits,
    }, fixed_events, momentum_events


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    weights = prior.portfolio.current_momentum_weights()
    router_fn, router_source = quantity.sized_router_factory()
    real_engine, model_engine, engine_source = prior.component.profit_route_engines()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    daily_parts, trade_parts, signal_parts, cycle_parts, event_parts, audits = [], [], [], [], [], {}
    for scope in ("real", "model"):
        result = run_layer(scope, weights, router_fn, real_engine, model_engine, call_dir)
        daily_parts.append(result[0]); trade_parts.append(result[1])
        signal_parts.append(result[2].assign(scope=scope, signal_type="fixed"))
        signal_parts.append(result[3].assign(scope=scope, signal_type="momentum"))
        if len(result[4]): cycle_parts.append(result[4].assign(scope=scope, sleeve="fixed"))
        if len(result[5]): cycle_parts.append(result[5].assign(scope=scope, sleeve="momentum"))
        if len(result[7]): event_parts.append(result[7].assign(scope=scope, sleeve="fixed"))
        if len(result[8]): event_parts.append(result[8].assign(scope=scope, sleeve="momentum"))
        audits[scope] = result[6]
    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True)
    signals = pd.concat(signal_parts, ignore_index=True)
    cycles = pd.concat(cycle_parts, ignore_index=True) if cycle_parts else pd.DataFrame()
    events = pd.concat(event_parts, ignore_index=True) if event_parts else pd.DataFrame()
    summary, wide, unavailable = prior.portfolio.metric_rows(daily)
    full = summary[summary.segment.eq("full")].copy()

    paired = []
    passed = True
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        fixed = block.loc[f"{scope}_fixed_only_q3"]
        both = block.loc[f"{scope}_both_q3"]
        ann_diff = float(both.ann_return - fixed.ann_return)
        sharpe_diff = float(both.sharpe_repo - fixed.sharpe_repo)
        dd_worse = float(abs(both.max_dd) - abs(fixed.max_dd))
        gate = ann_diff >= -1e-12 and sharpe_diff >= -0.05 - 1e-12 and dd_worse <= 0.01 + 1e-12
        passed = passed and gate
        paired.append({"scope": scope, "fixed_only_ann_return": float(fixed.ann_return), "both_ann_return": float(both.ann_return),
                       "both_minus_fixed_ann_pp": 100.0 * ann_diff, "both_minus_fixed_sharpe": sharpe_diff,
                       "both_mdd_abs_worsening_pp": 100.0 * dd_worse, "gate": gate})
    paired = pd.DataFrame(paired)
    integrity = all(audits[s]["existing_fixed_q3_parity_error"] <= 1e-12 and
                    audits[s]["variants"]["both_q3"]["min_cash_weight"] >= -1e-12
                    for s in ("real", "model"))
    passed = passed and integrity
    decision = "retain_momentum_short95_both_q3_as_research_candidate_no_production_change" if passed else "reject_momentum_short95_keep_fixed_only_q3_no_production_change"
    stability = "cross_layer_gate_passed_real_momentum_route_events_sparse" if passed else "momentum_short95_cross_layer_gate_failed"

    annual = daily.assign(year=daily.date.dt.year).groupby(["candidate", "scope", "variant", "year"], as_index=False).agg(annual_return=("ret", lambda x: float((1 + x).prod() - 1)))
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv", index=False)
    signals.to_csv(out / "signals.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    events.to_csv(out / "router_events.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + engine_source, encoding="utf-8")
    meta.update({
        "phase": "complete", "scan_type": "IM_v1_3_momentum_short95_full_joint_q3",
        "baseline": {"candidate": "fixed_only_q3", "reference_run": str(REFERENCE), "daily_parity": {s: audits[s]["existing_fixed_q3_parity_error"] for s in audits}},
        "candidate_grid": [{"variant": x} for x in ("profit3x_no_short", "fixed_only_q3", "momentum_only_q3", "both_q3")],
        "data_snapshot": {"real": "2022-07-22..2026-08-14 listed MO", "model": "2015-04-16..2026-08-14 theoretical MO extension"},
        "cost_model": {"all_MO_put_one_way_bp": 5, "futures_one_way_bp": 1, "short_put_quantity": "3 MO per 1 IM", "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "momentum_route": {"entry_size": "0.5 times current execution weight", "size_during_cycle": "fixed", "momentum_put": "zero target during route", "call": "paused while either route active"},
        "audit": audits, "paired_gate": paired.to_dict("records"), "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv"), "signals": str(out / "signals.csv"), "cycles": str(out / "cycles.csv"), "events": str(out / "router_events.csv"), "paired": str(RUN / "paired_comparison.csv"), "annual": str(RUN / "annual_attribution.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "reference_daily": sha(REFERENCE / "daily_outputs" / "daily.csv.gz")},
        "warnings": ["Research-only; production unchanged.", "Real momentum-route sample is expected to be very sparse.", "Model MO is theoretical.", "No dynamic margin, forced liquidation, tax, capacity, integer sizing, or explicit fill delay."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=json_default) + "\n", encoding="utf-8")
    record = (
        "# IM v1.3动量腿高IV转卖Put完整组合测试\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-07-22至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Paired Gate\n\n" + paired.to_markdown(index=False) +
        "\n\n## Audit\n\n```json\n" + json.dumps(audits, ensure_ascii=False, indent=2, default=json_default) +
        "\n```\n\n## Decision\n\n" + decision + "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(paired.to_string(index=False)); print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
