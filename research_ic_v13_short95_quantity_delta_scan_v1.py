"""IC v1.3 full-portfolio scan of short-95 Put quantity and entry Delta."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_current_full_composition_fixed_core_short95_quantity_and_entry_delta_q1_notional_q_delta05_q_delta10_stress"
SPEC = ROOT / "docs" / "ic_v13_short95_quantity_delta_scan_v1_spec.md"
REFERENCE = prior.RUN
MODES = {
    "q1_notional": None,
    "q_delta05": 0.50,
    "q_delta10_stress": 1.00,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def bs_put_abs_delta(spot: float, strike: float, rate: float, dividend: float,
                     sigma: float, years: float) -> float:
    if not (spot > 0 and strike > 0 and sigma > 0 and years > 0):
        raise RuntimeError("Invalid Black-Scholes Delta input")
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * sigma * sigma) * years) / (
        sigma * math.sqrt(years)
    )
    return math.exp(-dividend * years) * 0.5 * math.erfc(d1 / math.sqrt(2.0))


def cycle_sizing(scope: str, active: pd.DataFrame, market: pd.DataFrame,
                 cycles: pd.DataFrame) -> pd.DataFrame:
    rows = []
    active_dates = list(pd.to_datetime(active.date))
    date_pos = {day: i for i, day in enumerate(active_dates)}
    if scope == "real":
        _, etf, chains, _, _, _ = prior.router_base.short.real_inputs()
        etf = etf.sort_index()
    else:
        market_idx = market.set_index("date").sort_index()
        proxy = prior.router_base.short.model_source.proxy

    for cycle_id, cycle in cycles.reset_index(drop=True).iterrows():
        entry = pd.Timestamp(cycle.entry_date)
        if entry not in date_pos or date_pos[entry] == 0:
            raise RuntimeError(f"Invalid {scope} cycle entry {entry}")
        previous_day = active_dates[date_pos[entry] - 1]
        if scope == "real":
            chain = chains.get(previous_day, pd.DataFrame())
            hit = chain[chain.contract_id.astype(str).eq(str(cycle.put_contract))]
            if len(hit) != 1:
                raise RuntimeError(f"Missing decision-known Delta for {cycle.put_contract} at {previous_day.date()}")
            abs_delta = abs(float(hit.iloc[0].delta))
            previous_active = active.iloc[date_pos[entry] - 1]
            etf_close = float(etf.loc[previous_day, "close"])
            contracts_per_1_ic = (
                float(previous_active.settle) * prior.router_base.short.real_source.IC_MULTIPLIER
                / (etf_close * prior.router_base.short.real_source.ETF_MULTIPLIER)
            )
            delta_source = "previous_close_listed_chain"
        else:
            previous_market = market_idx.loc[previous_day]
            month = pd.Timestamp(cycle.entry_contract_month)
            expiry = proxy.fourth_wednesday(month, pd.DatetimeIndex(active.date))
            spot = float(previous_market.spot_close)
            strike = 0.95 * spot
            years = (expiry - previous_day).days / 365.0
            abs_delta = bs_put_abs_delta(
                spot, strike, float(previous_market.rate_close),
                float(previous_market.dividend_close), float(previous_market.sigma_close), years,
            )
            contracts_per_1_ic = np.nan
            delta_source = "previous_close_model_qvix"
        if not (np.isfinite(abs_delta) and abs_delta > 1e-8):
            raise RuntimeError(f"Invalid decision-known Delta at {entry.date()}: {abs_delta}")
        end_value = cycle.exit_date if pd.notna(cycle.get("exit_date", np.nan)) else cycle.get("mark_date", active.date.iloc[-1])
        end = pd.Timestamp(end_value)
        for mode, target in MODES.items():
            scale = 1.0 if target is None else float(target) / abs_delta
            rows.append({
                "scope": scope, "cycle_id": int(cycle_id), "entry_date": entry,
                "end_date": end, "put_contract": getattr(cycle, "put_contract", ""),
                "quantity_mode": mode, "target_delta_per_1_ic": abs_delta if target is None else target,
                "decision_known_entry_abs_delta": abs_delta, "quantity_scale": scale,
                "effective_entry_delta_per_1_ic": abs_delta * scale,
                "contracts_per_1_ic": contracts_per_1_ic * scale,
                "contracts_for_fixed_core_0_5x": 0.5 * contracts_per_1_ic * scale,
                "delta_source": delta_source,
                "early_rolls": int(getattr(cycle, "early_rolls", 0)),
            })
    return pd.DataFrame(rows)


def quantity_paths(active: pd.DataFrame, router: pd.DataFrame,
                   sizing: pd.DataFrame, mode: str) -> tuple[pd.Series, pd.Series]:
    dates = pd.to_datetime(active.date)
    pnl_scale = pd.Series(1.0, index=active.index)
    cycle_scale = pd.Series(np.nan, index=active.index)
    selected = sizing[sizing.quantity_mode.eq(mode)]
    for row in selected.itertuples(index=False):
        mask = dates.ge(row.entry_date) & dates.le(row.end_date)
        if cycle_scale.loc[mask].notna().any():
            raise RuntimeError(f"Overlapping short-Put cycles in {row.scope}")
        pnl_scale.loc[mask] = float(row.quantity_scale)
        cycle_scale.loc[mask] = float(row.quantity_scale)
    states = router.state.astype(str)
    reserve_scale = pd.Series(
        np.where(states.eq("ic"), 1.0, np.where(states.eq("idle"), 0.0, cycle_scale)),
        index=active.index, dtype=float,
    )
    if reserve_scale.isna().any():
        missing = active.loc[reserve_scale.isna(), ["date"]].head().to_dict("records")
        raise RuntimeError(f"Missing quantity scale for routed state: {missing}")
    return pnl_scale.astype(float), reserve_scale.astype(float)


def compose_sized(active: pd.DataFrame, router: pd.DataFrame, weights: pd.Series,
                  grid: pd.DataFrame, total_put: pd.DataFrame, candidate: str,
                  sizing: pd.DataFrame, mode: str) -> pd.DataFrame:
    weight = active.date.map(weights).astype(float)
    if weight.isna().any():
        raise RuntimeError(f"{candidate} missing momentum weights")
    turnover = weight.diff().abs(); turnover.iloc[0] = abs(float(weight.iloc[0]))
    mom_cost = prior.FUTURES_ONE_WAY * turnover + 2.0 * prior.FUTURES_ONE_WAY * weight * active.roll_event.astype(float)
    mom_net = (1.0 + weight * active.ic_gross_ret.astype(float)) * (1.0 - mom_cost) - 1.0
    pnl_scale, reserve_scale = quantity_paths(active, router, sizing, mode)
    fixed_non_cash = router.return_net.astype(float) - router.cash_weight.astype(float) * prior.CASH
    base_non_cash = 0.5 * pnl_scale * fixed_non_cash + 0.5 * mom_net
    fixed_reserve = 0.15 * reserve_scale
    cash = (
        1.0 - fixed_reserve - 0.15 * weight - 0.3 * grid.grid_units.astype(float)
        - total_put.put_mark_fraction.astype(float)
    )
    ret = (
        (1.0 + base_non_cash + total_put.put_pnl_ret.astype(float))
        * (1.0 - total_put.put_cost_rate.astype(float)) - 1.0
        + grid.grid_net_increment.astype(float) + cash.clip(lower=0.0) * prior.CASH
    )
    if not np.isfinite(ret).all() or ret.le(-1.0).any():
        raise RuntimeError(f"{candidate} invalid returns")
    out = pd.DataFrame({
        "date": active.date, "candidate": candidate, "return_net": ret,
        "cash_weight": cash, "quantity_mode": mode, "short_pnl_scale": pnl_scale,
        "fixed_reserve_scale": reserve_scale, "momentum_weight": weight,
        "grid_units": grid.grid_units, "put_pnl_ret": total_put.put_pnl_ret,
        "put_cost_rate": total_put.put_cost_rate, "put_mark_fraction": total_put.put_mark_fraction,
        "fixed_router_state": router.state, "fixed_router_action": router.action,
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
    current_combined = prior.router_base.current_schedule()
    signal = prior.seller_state.signal_variant(
        base_signal, current_combined, scope, "instant", prior.seller_state.momentum_permission()
    )
    isolated, short_events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), 0.60, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    core_masked = prior.router_base.mask_schedule(core_schedule, scope, routed.loc[routed.state.eq("ic"), "date"])
    core_always = prior.router_base.mask_schedule(core_schedule, scope, active.date)
    momentum_scope = prior.router_base.mask_schedule(momentum_schedule, scope, active.date)
    sizing = cycle_sizing(scope, active, market, cycles)

    engine = prior.sleeve.ic_put.v1.put_engine
    if scope == "real":
        mom_put, mom_trades = engine.run_real_delta(
            qic, momentum_scope, qframes, option_market, f"{scope}_momentum", put_roll_dates
        )
    else:
        mom_put, mom_trades = engine.run_model_delta(
            qic, momentum_scope, option_market, f"{scope}_momentum", put_roll_dates
        )
    mom_put = mom_put[mom_put.date.isin(active.date)].reset_index(drop=True)
    mom_put = prior.maturity.costed_core(mom_put, prior.PUT_COST_MULTIPLIER)

    reference = pd.read_csv(REFERENCE / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    daily_parts = []
    trade_parts = [mom_trades.assign(scope=scope, sleeve="momentum", candidate=f"{scope}_all")]
    audit = {"short_router": short_audit, "route_switches": len(route_dates), "variants": {}}
    normal = prior.normal_router(active)

    for variant, schedule, exits, selected_router, multiple in (
        ("baseline", core_always, frozenset(), normal, None),
        ("profit3x_only", core_always, frozenset(), normal, 3.0),
    ):
        label = f"{scope}_{variant}"
        if scope == "real":
            core_put, core_trades = real_profit(qic, schedule, qframes, option_market, label, put_roll_dates,
                                                open_exit_dates=exits, profit_multiple=multiple)
        else:
            core_put, core_trades = model_profit(qic, schedule, option_market, label, put_roll_dates,
                                                 open_exit_dates=exits, profit_multiple=multiple)
        core_put = prior.maturity.costed_core(
            core_put[core_put.date.isin(active.date)].reset_index(drop=True), prior.PUT_COST_MULTIPLIER
        )
        total_put = prior.combine_puts(core_put, mom_put)
        daily = prior.compose(active, selected_router, weights, grid, total_put, label)
        daily["scope"] = scope; daily["variant"] = variant; daily["quantity_mode"] = "none"
        daily_parts.append(daily)
        trade_parts.append(core_trades.assign(scope=scope, sleeve="core", candidate=label))
        ref = reference[reference.candidate.eq(label)].sort_values("date")
        parity = float(np.max(np.abs(daily.return_net.to_numpy() - ref.return_net.to_numpy())))
        if parity > 1e-12:
            raise RuntimeError(f"Reference parity failed {label}: {parity}")
        audit["variants"][variant] = {"reference_parity": parity, "min_cash_weight": float(daily.cash_weight.min())}

    for mode in MODES:
        for variant, multiple in (("short95_only", None), ("joint3x", 3.0)):
            label = f"{scope}_{variant}_{mode}"
            if scope == "real":
                core_put, core_trades = real_profit(
                    qic, core_masked, qframes, option_market, label, put_roll_dates,
                    open_exit_dates=route_dates, profit_multiple=multiple,
                )
            else:
                core_put, core_trades = model_profit(
                    qic, core_masked, option_market, label, put_roll_dates,
                    open_exit_dates=route_dates, profit_multiple=multiple,
                )
            core_put = prior.maturity.costed_core(
                core_put[core_put.date.isin(active.date)].reset_index(drop=True), prior.PUT_COST_MULTIPLIER
            )
            total_put = prior.combine_puts(core_put, mom_put)
            daily = compose_sized(active, routed, weights, grid, total_put, label, sizing, mode)
            daily["scope"] = scope; daily["variant"] = variant
            daily_parts.append(daily)
            trade_parts.append(core_trades.assign(scope=scope, sleeve="core", candidate=label))
            route_exits = core_trades[core_trades.action.eq("route_open_exit")]
            profits = core_trades[core_trades.action.eq("close_profit_restrike")]
            duplicates = set(route_exits.actual_execution_date) & set(profits.actual_execution_date)
            if duplicates:
                raise RuntimeError(f"Duplicate route/profit events {label}: {duplicates}")
            parity = None
            if mode == "q1_notional":
                old_label = f"{scope}_{variant}"
                ref = reference[reference.candidate.eq(old_label)].sort_values("date")
                parity = float(np.max(np.abs(daily.return_net.to_numpy() - ref.return_net.to_numpy())))
                if parity > 1e-12:
                    raise RuntimeError(f"q1 reference parity failed {label}: {parity}")
            sized = sizing[sizing.quantity_mode.eq(mode)]
            audit["variants"][f"{variant}_{mode}"] = {
                "q1_reference_parity": parity, "profit_restrikes": int(len(profits)),
                "core_put_route_exits": int(len(route_exits)), "duplicate_route_profit_days": int(len(duplicates)),
                "min_cash_weight": float(daily.cash_weight.min()),
                "negative_cash_days": int(daily.cash_weight.lt(-1e-12).sum()),
                "capital_feasible": bool(daily.cash_weight.ge(-1e-12).all()),
                "quantity_scale_min": float(sized.quantity_scale.min()),
                "quantity_scale_median": float(sized.quantity_scale.median()),
                "quantity_scale_max": float(sized.quantity_scale.max()),
            }
    return (
        pd.concat(daily_parts, ignore_index=True), pd.concat(trade_parts, ignore_index=True),
        signal.assign(scope=scope), short_events.assign(scope=scope),
        cycles.assign(scope=scope), sizing, audit,
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
    results = {
        scope: run_layer(scope, path, futures, market, weights, selected, grid,
                         real_short, model_short, model_profit, real_profit)
        for scope in ("real", "model")
    }
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    sizing = pd.concat([results[x][5] for x in ("real", "model")], ignore_index=True)
    audits = {x: results[x][6] for x in ("real", "model")}
    summary, wide, unavailable = prior.router_base.summarize(daily)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]
    full = summary[summary.segment.eq("full")].copy()

    comparison_rows = []
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        for mode in MODES:
            for variant in ("short95_only", "joint3x"):
                label = f"{scope}_{variant}_{mode}"
                q1 = f"{scope}_{variant}_q1_notional"
                audit = audits[scope]["variants"][f"{variant}_{mode}"]
                comparison_rows.append({
                    "scope": scope, "variant": variant, "quantity_mode": mode,
                    "ann_return": float(block.loc[label].ann_return),
                    "ann_return_minus_q1": float(block.loc[label].ann_return - block.loc[q1].ann_return),
                    "sharpe_repo": float(block.loc[label].sharpe_repo),
                    "sharpe_minus_q1": float(block.loc[label].sharpe_repo - block.loc[q1].sharpe_repo),
                    "max_dd": float(block.loc[label].max_dd),
                    "max_dd_abs_worsening_vs_q1": float(abs(block.loc[label].max_dd) - abs(block.loc[q1].max_dd)),
                    "min_cash_weight": audit["min_cash_weight"],
                    "negative_cash_days": audit["negative_cash_days"],
                    "capital_feasible": audit["capital_feasible"],
                    "quantity_scale_median": audit["quantity_scale_median"],
                    "quantity_scale_max": audit["quantity_scale_max"],
                })
    comparison = pd.DataFrame(comparison_rows)
    q05 = comparison[(comparison.variant.eq("joint3x")) & (comparison.quantity_mode.eq("q_delta05"))]
    promote_q05 = bool(
        len(q05) == 2 and q05.capital_feasible.all()
        and q05.ann_return_minus_q1.ge(-1e-12).all()
        and q05.sharpe_minus_q1.ge(-0.05 - 1e-12).all()
        and q05.max_dd_abs_worsening_vs_q1.le(0.01 + 1e-12).all()
    )
    decision = (
        "retain_q_delta05_as_research_candidate_no_production_change"
        if promote_q05 else "keep_q1_notional_reject_delta_scaled_quantity_no_production_change"
    )
    stability = (
        "q_delta05_cross_layer_gate_passed_real_events_sparse"
        if promote_q05 else "delta_scaled_quantity_failed_cross_layer_risk_or_capital_gate"
    )
    annual = (
        daily.assign(year=daily.date.dt.year)
        .groupby(["candidate", "scope", "variant", "quantity_mode", "year"], as_index=False)
        .agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    )

    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "short_put_events.csv", index=False)
    cycles.to_csv(out / "short_put_cycles.csv", index=False)
    sizing.to_csv(out / "quantity_sizing.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "quantity_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    real_sizing = sizing[sizing.scope.eq("real")]
    meta.update({
        "phase": "complete", "scan_type": "IC_short95_quantity_by_decision_known_entry_delta",
        "baseline": {"reference": str(REFERENCE), "q1_candidates": ["real_joint3x_q1_notional", "model_joint3x_q1_notional"],
                     "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [{"quantity_mode": mode, "target_delta_per_1_ic": target} for mode, target in MODES.items()],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 actual listed 510500 Put and IC",
                          "model": "2015-04-16..2026-08-14 theoretical 510500 Put proxy plus historical IC"},
        "delta_timing": {"real": "previous-close listed chain Delta", "model": "previous-close Black-Scholes Delta",
                         "early_roll": "quantity fixed from initial cycle entry; new leg does not resize"},
        "cost_model": {"all_510500_put_one_way": prior.OPTION_ONE_WAY, "put_round_trip": 2 * prior.OPTION_ONE_WAY,
                       "IC_one_way": prior.FUTURES_ONE_WAY, "futures_buffer_per_1x": 0.30,
                       "fixed_core_reserve": "15% times cycle quantity_scale", "cash_annual": 0.03},
        "audit": audits, "quantity_comparison": comparison.to_dict("records"),
        "real_entry_delta": {"count": int(real_sizing[real_sizing.quantity_mode.eq("q1_notional")].shape[0]),
                             "min": float(real_sizing[real_sizing.quantity_mode.eq("q1_notional")].decision_known_entry_abs_delta.min()),
                             "median": float(real_sizing[real_sizing.quantity_mode.eq("q1_notional")].decision_known_entry_abs_delta.median()),
                             "max": float(real_sizing[real_sizing.quantity_mode.eq("q1_notional")].decision_known_entry_abs_delta.max())},
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"),
                    "signals": str(out / "signals.csv.gz"), "events": str(out / "short_put_events.csv"),
                    "cycles": str(out / "short_put_cycles.csv"), "sizing": str(out / "quantity_sizing.csv"),
                    "comparison": str(RUN / "quantity_comparison.csv"), "annual": str(RUN / "annual_attribution.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC),
                          "reference_daily": sha(REFERENCE / "daily_outputs" / "daily.csv.gz")},
        "warnings": ["Research-only counterfactual; production unchanged.",
                     "Model Put and Delta before listed history are theoretical proxies.",
                     "Real route events are sparse.",
                     "Delta-scaled quantities are continuous and use no integer account sizing.",
                     "Negative cash paths are diagnostics without forced liquidation, borrowing cost, or dynamic option margin."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IC v1.3卖95% Put张数与初始Delta口径补测\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Quantity Definition\n\n等名义、前收盘Delta反推每1倍IC初始0.5 Delta、初始1.0 Delta压力档；完整组合固定核心为0.5倍。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Quantity Comparison\n\n" + comparison.to_markdown(index=False) +
        "\n\n## Real Entry Sizing\n\n" + real_sizing.to_markdown(index=False) +
        "\n\n## Verification\n\nq1逐日复现第六层；真实Delta仅用前收盘挂牌字段；全部Put单边5BP；负现金单独标记。\n\n"
        "## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(comparison.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
