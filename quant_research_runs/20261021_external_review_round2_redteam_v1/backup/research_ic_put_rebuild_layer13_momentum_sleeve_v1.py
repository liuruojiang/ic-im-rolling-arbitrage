"""Rebuild IC Put research, layer 13: route the momentum sleeve into short Put."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_put_rebuild_layer9_short_quantity_delta_v1 as layer9
import research_ic_put_rebuild_layer12_short_strike_atm_vs95_v1 as layer12
import research_ic_v13_momentum_short95_full_joint_v1 as old_momentum
import research_ic_v13_short95_quantity_delta_scan_v1 as quantity
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l13_momentum_sleeve_short_put_none_fixed_momentum_both_qd05"
LAYER12 = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l12_3x_qd05_strike_95pct_versus_atm"
SPEC = ROOT / "docs" / "ic_put_rebuild_layer13_momentum_sleeve_v1_spec.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose_dual_sized(active: pd.DataFrame, fixed_router: pd.DataFrame,
                       fixed_routed: bool, weights: pd.Series,
                       momentum_router: pd.DataFrame, momentum_scale: pd.Series,
                       momentum_routed: bool, pnl_scale: pd.Series,
                       reserve_scale: pd.Series, grid: pd.DataFrame,
                       total_put: pd.DataFrame, candidate: str) -> pd.DataFrame:
    prior = base.prior
    dates = active.date.reset_index(drop=True)
    if not dates.equals(fixed_router.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} fixed router dates differ")
    if not dates.equals(momentum_router.date.reset_index(drop=True)):
        raise RuntimeError(f"{candidate} momentum router dates differ")
    weight = active.date.map(weights).astype(float).reset_index(drop=True)
    gross = active.ic_gross_ret.astype(float).reset_index(drop=True)
    roll = active.roll_event.astype(float).reset_index(drop=True)
    scale = pnl_scale.reset_index(drop=True).astype(float)
    reserve = reserve_scale.reset_index(drop=True).astype(float)

    fixed_non_cash = (
        fixed_router.return_net.astype(float).reset_index(drop=True)
        - fixed_router.cash_weight.astype(float).reset_index(drop=True) * prior.CASH
    )
    if fixed_routed:
        fixed_contribution = 0.5 * scale * fixed_non_cash
        fixed_reserve = 0.15 * reserve
    else:
        fixed_contribution = 0.5 * fixed_non_cash
        fixed_reserve = pd.Series(0.15, index=dates.index, dtype=float)

    if not momentum_routed:
        turnover = weight.diff().abs(); turnover.iloc[0] = abs(float(weight.iloc[0]))
        cost = prior.FUTURES_ONE_WAY * turnover + 2.0 * prior.FUTURES_ONE_WAY * weight * roll
        mom_net = (1.0 + weight * gross) * (1.0 - cost) - 1.0
        momentum_contribution = 0.5 * mom_net
        momentum_reserve = 0.15 * weight
        effective_route_scale = pd.Series(0.0, index=dates.index)
    else:
        q = momentum_scale.reset_index(drop=True).astype(float)
        route_active = q.gt(0)
        normal_weight = weight.where(~route_active, 0.0)
        turnover = normal_weight.diff().abs(); turnover.iloc[0] = abs(float(normal_weight.iloc[0]))
        transitions = route_active.ne(route_active.shift(fill_value=False))
        turnover = turnover.where(~transitions, 0.0)
        cost = prior.FUTURES_ONE_WAY * turnover + 2.0 * prior.FUTURES_ONE_WAY * normal_weight * roll
        normal_net = (1.0 + normal_weight * gross) * (1.0 - cost) - 1.0
        route_non_cash = (
            momentum_router.return_net.astype(float).reset_index(drop=True)
            - momentum_router.cash_weight.astype(float).reset_index(drop=True) * prior.CASH
        )
        effective_route_scale = q * scale
        momentum_contribution = 0.5 * normal_net + effective_route_scale * route_non_cash
        momentum_reserve = (
            0.15 * normal_weight
            + effective_route_scale * (1.0 - momentum_router.cash_weight.astype(float).reset_index(drop=True))
        )

    cash = (
        1.0 - fixed_reserve - momentum_reserve
        - 0.3 * grid.grid_units.astype(float).reset_index(drop=True)
        - total_put.put_mark_fraction.astype(float).reset_index(drop=True)
    )
    if cash.min() < -1e-12:
        raise RuntimeError(f"{candidate} negative cash weight: {cash.min()}")
    ret = (
        (1.0 + fixed_contribution + momentum_contribution
         + total_put.put_pnl_ret.astype(float).reset_index(drop=True))
        * (1.0 - total_put.put_cost_rate.astype(float).reset_index(drop=True)) - 1.0
        + grid.grid_net_increment.astype(float).reset_index(drop=True)
        + cash.clip(lower=0.0) * prior.CASH
    )
    if not np.isfinite(ret).all() or ret.le(-1.0).any():
        raise RuntimeError(f"{candidate} invalid returns")
    out = pd.DataFrame({
        "date": dates, "candidate": candidate, "return_net": ret,
        "cash_weight": cash, "momentum_weight": weight,
        "momentum_route_scale": effective_route_scale,
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


def run_scope(scope: str, prepared: dict[str, object], selected: pd.DataFrame,
              futures: pd.DataFrame, weights: pd.Series,
              option_market: pd.DataFrame, model_market: pd.DataFrame,
              real_short, model_short, model_profit, real_profit):
    prior = base.prior
    active = prepared["active"]
    signal = layer9.build_signal(prepared)
    runner = real_short if scope == "real" else model_short
    isolated, events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), None, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    normal = prior.normal_router(active)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    sizing = layer12.cycle_sizing(scope, active, model_market, cycles, 0.95, "strike95")
    pnl_scale, reserve_scale = quantity.quantity_paths(
        active, routed, sizing, "q_delta05"
    )
    momentum_scale, scale_audit = old_momentum.route_scale(routed, active, weights)
    route_active = momentum_scale.gt(0)

    core_schedule = prepared["core_schedule"]
    momentum_schedule = prior.sleeve.build_schedule(selected, "momentum")
    core_always = prior.router_base.mask_schedule(core_schedule, scope, active.date)
    core_masked = prior.router_base.mask_schedule(
        core_schedule, scope, routed.loc[routed.state.eq("ic"), "date"]
    )
    momentum_always = prior.router_base.mask_schedule(momentum_schedule, scope, active.date)
    momentum_masked = prior.router_base.mask_schedule(
        momentum_schedule, scope, active.loc[~route_active, "date"]
    )

    variants = {
        "profit3x_no_short": (False, False),
        "fixed_only_qd05": (True, False),
        "momentum_only_qd05": (False, True),
        "both_qd05": (True, True),
    }
    daily_parts, trade_parts, audits = [], [], {}
    for variant, (use_fixed, use_momentum) in variants.items():
        label = f"{scope}_{variant}"
        core_put, core_trades = old_momentum.run_put(
            scope, prepared["qic"], prepared["qframes"], option_market,
            core_masked if use_fixed else core_always,
            label + "_core", prepared["put_roll_dates"], model_profit, real_profit,
            exits=route_dates if use_fixed else frozenset(), multiple=3.0,
        )
        momentum_put, momentum_trades = old_momentum.run_put(
            scope, prepared["qic"], prepared["qframes"], option_market,
            momentum_masked if use_momentum else momentum_always,
            label + "_momentum", prepared["put_roll_dates"], model_profit, real_profit,
            exits=route_dates if use_momentum else frozenset(), multiple=None,
        )
        core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
        momentum_put = momentum_put[momentum_put.date.isin(active.date)].reset_index(drop=True)
        total_put = prior.combine_puts(core_put, momentum_put)
        daily = compose_dual_sized(
            active, routed if use_fixed else normal, use_fixed, weights,
            routed if use_momentum else normal, momentum_scale, use_momentum,
            pnl_scale, reserve_scale, prepared["grid"], total_put, label,
        )
        daily["scope"] = scope; daily["variant"] = variant
        daily_parts.append(daily)
        trade_parts.extend([
            core_trades.assign(scope=scope, sleeve="core", candidate=label),
            momentum_trades.assign(scope=scope, sleeve="momentum", candidate=label),
        ])
        core_exits = set(pd.to_datetime(
            core_trades.loc[core_trades.action.eq("route_open_exit"), "actual_execution_date"]
        ))
        momentum_exits = set(pd.to_datetime(
            momentum_trades.loc[momentum_trades.action.eq("route_open_exit"), "actual_execution_date"]
        ))
        allowed = set(pd.to_datetime(list(route_dates)))
        core_outside = core_exits - allowed
        momentum_outside = momentum_exits - allowed
        if core_outside or momentum_outside:
            raise RuntimeError(f"{label} Put route exit outside route dates")
        fixed_mask = routed.state.ne("ic").to_numpy(dtype=bool)
        momentum_mask = route_active.to_numpy(dtype=bool)
        core_mark = float(core_put.loc[fixed_mask, "put_mark_fraction"].abs().max()) if use_fixed and fixed_mask.any() else 0.0
        momentum_mark = float(momentum_put.loc[momentum_mask, "put_mark_fraction"].abs().max()) if use_momentum and momentum_mask.any() else 0.0
        if core_mark > 1e-12 or momentum_mark > 1e-12:
            raise RuntimeError(f"{label} retained routed Put position")
        audits[variant] = {
            "route_cycles": int(len(cycles)) if (use_fixed or use_momentum) else 0,
            "momentum_route_entries": scale_audit["entry_count"] if use_momentum else 0,
            "momentum_route_active_days": scale_audit["active_days"] if use_momentum else 0,
            "momentum_boundary_weight_mismatch": scale_audit["max_entry_or_restore_weight_mismatch"] if use_momentum else 0.0,
            "core_put_route_exits": len(core_exits),
            "momentum_put_route_exits": len(momentum_exits),
            "core_put_route_exits_outside_route": len(core_outside),
            "momentum_put_route_exits_outside_route": len(momentum_outside),
            "core_put_mark_during_route_max": core_mark,
            "momentum_put_mark_during_route_max": momentum_mark,
            "core_profit_restrikes": int(core_trades.action.eq("close_profit_restrike").sum()),
            "min_cash_weight": float(daily.cash_weight.min()),
            "negative_cash_days": int(daily.cash_weight.lt(-1e-12).sum()),
        }
    return (
        pd.concat(daily_parts, ignore_index=True),
        pd.concat(trade_parts, ignore_index=True),
        signal.assign(scope=scope), events.assign(scope=scope),
        cycles.assign(scope=scope), sizing,
        {"short_router": short_audit, "momentum_scale": scale_audit, "variants": audits},
    )


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-13 run")
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
            scope, path, futures, weights, selected, grid, frames,
            option_market, model_market, corrected_real,
        )
        for scope in ("real", "model")
    }
    prepared["model"]["base_signal"] = prior.maturity.maturity_model_signals(
        prepared["model"]["active"], model_market, "m1"
    )
    old_threshold = layer9.IV_THRESHOLD
    layer9.IV_THRESHOLD = 0.30
    try:
        results = {
            scope: run_scope(
                scope, prepared[scope], selected, futures, weights,
                option_market, model_market, real_short, model_short,
                model_profit, real_profit,
            )
            for scope in ("real", "model")
        }
    finally:
        layer9.IV_THRESHOLD = old_threshold

    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    sizing = pd.concat([results[x][5] for x in ("real", "model")], ignore_index=True)
    audits = {x: results[x][6] for x in ("real", "model")}
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()

    paired_rows = []
    passed = True
    for scope in ("real", "model"):
        fixed = full[full.candidate.eq(f"{scope}_fixed_only_qd05")].iloc[0]
        both = full[full.candidate.eq(f"{scope}_both_qd05")].iloc[0]
        ann_diff = both.ann_return - fixed.ann_return
        sharpe_diff = both.sharpe_repo - fixed.sharpe_repo
        dd_worse = abs(both.max_dd) - abs(fixed.max_dd)
        integrity = (
            audits[scope]["variants"]["both_qd05"]["negative_cash_days"] == 0
            and audits[scope]["variants"]["both_qd05"]["core_put_route_exits_outside_route"] == 0
            and audits[scope]["variants"]["both_qd05"]["momentum_put_route_exits_outside_route"] == 0
            and audits[scope]["variants"]["both_qd05"]["core_put_mark_during_route_max"] <= 1e-12
            and audits[scope]["variants"]["both_qd05"]["momentum_put_mark_during_route_max"] <= 1e-12
        )
        gate = bool(
            ann_diff >= -1e-12 and sharpe_diff >= -0.05 - 1e-12
            and dd_worse <= 0.01 + 1e-12 and integrity
        )
        passed = passed and gate
        paired_rows.append({
            "scope": scope,
            "fixed_only_ann_return": fixed.ann_return,
            "both_ann_return": both.ann_return,
            "both_minus_fixed_ann_pp": 100.0 * ann_diff,
            "fixed_only_sharpe": fixed.sharpe_repo,
            "both_sharpe": both.sharpe_repo,
            "both_minus_fixed_sharpe": sharpe_diff,
            "fixed_only_max_dd": fixed.max_dd,
            "both_max_dd": both.max_dd,
            "both_mdd_abs_worsening_pp": 100.0 * dd_worse,
            "integrity_gate": integrity, "gate": gate,
        })
    paired = pd.DataFrame(paired_rows)
    decision = (
        "retain_momentum_sleeve_short_put_both_qdelta05_awaiting_user_confirmation"
        if passed else
        "reject_momentum_sleeve_short_put_keep_fixed_only_qdelta05_awaiting_user_confirmation"
    )
    stability = (
        "momentum_sleeve_cross_layer_gate_passed"
        if passed else "momentum_sleeve_cross_layer_gate_failed"
    )

    reference = pd.read_csv(LAYER12 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, dict[str, float]] = {}
    for scope in ("real", "model"):
        parity[scope] = {}
        for variant, old_candidate in (
            ("profit3x_no_short", f"{scope}_noseller_profit3x_cost5bp"),
            ("fixed_only_qd05", f"{scope}_strike95_qdelta05_profit3x"),
        ):
            got = daily[daily.candidate.eq(f"{scope}_{variant}")].sort_values("date")
            old = reference[reference.candidate.eq(old_candidate)].sort_values("date")
            if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
                raise RuntimeError(f"Layer12 parity dates failed {scope} {variant}")
            error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"Layer12 parity returns failed {scope} {variant}: {error}")
            parity[scope][variant] = error

    annual = (
        daily.assign(year=daily.date.dt.year)
        .groupby(["candidate", "scope", "variant", "year"], as_index=False)
        .agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    )
    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "router_events.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    sizing.to_csv(out / "quantity_sizing.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(
        short_source + "\n\n" + profit_source, encoding="utf-8"
    )
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "momentum_sleeve_short_put_none_fixed_momentum_both_qdelta05",
        "baseline": {"candidate": "fixed_only_qd05", "layer12_parity_max_abs": parity},
        "candidate_grid": [{"variant": x} for x in (
            "profit3x_no_short", "fixed_only_qd05",
            "momentum_only_qd05", "both_qd05",
        )],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"]},
        "cost_model": {"all_510500_put_one_way_bp": 5, "ic_one_way_bp": 1, "short_put_quantity": "target aggregate entry Delta 0.5 per 1x routed IC", "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "fixed_policy": {"maturity": "M+1", "strike_target": 0.95, "iv_threshold": 0.30, "premium_decay": None, "core_put_profit_multiple": 3, "seller_mom120": False, "call": "excluded"},
        "momentum_route": {"entry_size": "0.5 times current execution weight, then scaled by 0.5 divided by contract abs Delta", "size_during_cycle": "fixed", "momentum_put": "force exit on route open and zero target during route"},
        "audit": audits,
        "paired_gate": paired.to_dict("records"),
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "old_momentum_engine": sha256(ROOT / "research_ic_v13_momentum_short95_full_joint_v1.py"), "layer12_daily": sha256(LAYER12 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "events": str(out / "router_events.csv"), "cycles": str(out / "cycles.csv"), "sizing": str(out / "quantity_sizing.csv"), "paired": str(RUN / "paired_comparison.csv"), "annual": str(RUN / "annual_attribution.csv")},
        "warnings": ["Research only; no production, email, ledger, registry or order change.", "Real listed history and momentum route events are sparse.", "Model options are theoretical proxy.", "Continuous sizing ignores integer contracts, dynamic margin, forced liquidation, tax and explicit bid-ask impact.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=old_momentum.json_default) + "\n", encoding="utf-8")
    record = "\n".join([
        "# IC Put 污染后重建：第十三层动量腿同步卖 Put", "",
        "## Run Metadata", "", "研究专用；固定95%虚值、总Delta 0.5、IV30%、3倍兑现和持有到期；未修改生产。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Gate", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Verification", "", f"无卖Put与固定核心腿路径对第十二层逐日误差：{parity}。", "",
        "## Stability Classification", "", f"`{stability}`。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入最终闸门。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(paired.to_string(index=False))


if __name__ == "__main__":
    main()
