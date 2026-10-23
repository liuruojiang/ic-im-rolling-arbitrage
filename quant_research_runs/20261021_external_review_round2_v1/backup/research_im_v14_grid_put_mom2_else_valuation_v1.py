"""Research-only v1.4-r1 full-joint ablation for an independent 0.5x-grid MO Put."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v14_put_monthly_roll_timing_v1 as timing

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_4_r1_full_joint_im_0_5x_grid_independent_put_mom120_negative_2_else_valuation"
SPEC = ROOT / "docs" / "im_v14_grid_put_mom2_else_valuation_v1_spec.md"
BASELINE = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_4_r1_current_joint_im_core_and_momentum_long_put_monthly_maintenance_sync_put_with_im_quarter_roll_else_t0"


def grid_schedule(scope: str, base: pd.DataFrame, grid: pd.DataFrame) -> pd.DataFrame:
    """Use the v1.4 effective valuation/MOM state and the actual 0.5x grid state."""
    template = timing.valuation.corrected_core_schedule(base.date, scope, None).copy()
    held = template.execution_date.map(grid.set_index("date").overlay_held_eod).fillna(0.0).gt(0)
    desired = np.where(template.momentum_120.to_numpy(float) < 0, 2, template.valuation_tier.to_numpy(int))
    desired = desired * held.to_numpy(int)
    factor = 4 if scope == "model" else 8
    template["binary_target_qty"] = (desired * factor).astype(int)
    template["three_tier_target_qty"] = template.binary_target_qty
    template["grid_held"] = held.to_numpy(bool)
    template["original_qty"] = desired.astype(int)
    template["mom120_negative"] = template.momentum_120.to_numpy(float) < 0
    template["put_buy_allowed"] = True
    return template


def run_grid_put(scope: str, base: pd.DataFrame, grid: pd.DataFrame, market: pd.DataFrame | None, resets: set[pd.Timestamp]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    schedule = grid_schedule(scope, base, grid)
    if scope == "model":
        if market is None:
            raise RuntimeError("model market missing")
        put, trades, _ = timing.portfolio.full.engine.run_model_monthly_close(market, schedule, "3m", 1.02, "model_grid_put", reset_dates=resets)
        scale = 0.125  # same 0.5x sleeve normalization as v1.4 core Put
    else:
        upstream = timing.read(timing.portfolio.full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.isin(base.date)].reset_index(drop=True)
        active = timing.read(timing.portfolio.full.BASE / "real_active.csv.gz")
        active = active[active.date.isin(base.date)].reset_index(drop=True)
        options = timing.portfolio.full.engine.with_execution_prices(timing.read(timing.portfolio.full.BASE / "real_options.csv.gz", ("date", "contract_month", "rule_expiry", "actual_expiry")))
        options = options[options.date.isin(base.date)].reset_index(drop=True)
        put, trades, _ = timing.portfolio.full.engine.run_real_monthly_close(upstream, options, active, schedule, "3m", 1.02, "real_grid_put", reset_dates=resets)
        scale = 0.0625  # v1.4 0.5x IM sleeve
    put[timing.PUT_FIELDS] = put[timing.PUT_FIELDS] * scale
    put["put_cost_rate"] = put.put_cost_rate.astype(float) * timing.PUT_COST_MULTIPLIER
    return put, trades, schedule


def build_full(scope: str, weights: pd.Series, router_fn, real_engine, model_engine, call_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base, _ = timing.portfolio.rebuild_base(scope, weights)
    grid = timing.portfolio.half_grid(scope)
    market, router_base, options, _, futures, _ = timing.joint.quarterly_router_inputs(scope, base)
    signal = timing.maturity.prepare_signal(router_base, options, "m1")
    routed, _, _ = router_fn(router_base, options, futures, signal, 0.35, timing.common.FALLBACK, 0.60)
    routed["date"] = pd.to_datetime(routed.date)
    route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
    imc_mask = routed.set_index("date").state.eq("imc")
    resets, _ = timing.shifted_reset_dates(base.date, 0)
    momentum, mom_trades = timing.rerun_momentum_put(scope, base, resets)
    core, core_trades = timing.rerun_core_put(scope, base, market, resets, route_dates, imc_mask, real_engine, model_engine)
    baseline_put = timing.joint.combine_puts(core, momentum, timing.PUT_COST_MULTIPLIER)
    call, _, _ = timing.maturity.call_inputs(scope, base.date, routed.state.eq("imc").astype(float), f"{scope}_gridput", call_dir)
    baseline = timing.joint.compose_routed(base, routed, baseline_put, grid, call)
    grid_put, grid_trades, schedule = run_grid_put(scope, base, grid, market, resets)
    candidate_put = baseline_put.copy()
    for field in timing.PUT_FIELDS:
        candidate_put[field] = baseline_put[field].astype(float) + grid_put[field].astype(float)
    candidate = timing.joint.compose_routed(base, routed, candidate_put, grid, call)
    trades = pd.concat([core_trades.assign(sleeve="core"), mom_trades.assign(sleeve="momentum"), grid_trades.assign(sleeve="grid")], ignore_index=True, sort=False)
    return baseline, candidate, schedule, trades


def main() -> None:
    if (RUN / "scan_summary.csv").exists():
        raise RuntimeError("Refusing to overwrite completed scan")
    real_engine, model_engine, _ = timing.joint.component.profit_route_engines()
    router_fn, _ = timing.joint.audited_router()
    weights = timing.portfolio.current_momentum_weights()
    parts: list[pd.DataFrame] = []; schedules: list[pd.DataFrame] = []; trades: list[pd.DataFrame] = []; checks: dict[str, object] = {}
    reference = timing.read(BASELINE / "daily_outputs" / "daily.csv.gz")
    for scope in ("model", "real"):
        baseline, candidate, schedule, tr = build_full(scope, weights, router_fn, real_engine, model_engine, RUN / "call_artifacts")
        saved = reference[reference.candidate.eq(f"{scope}_current_T0")].sort_values("date").reset_index(drop=True)
        parity = float(np.max(np.abs(baseline.ret.to_numpy() - saved.ret.to_numpy())))
        if parity > 1e-12:
            raise RuntimeError(f"{scope} v1.4 full-joint baseline parity failed: {parity}")
        for name, daily in (("grid_put_off", baseline), ("grid_put_mom2_else_valuation", candidate)):
            daily = daily.copy(); daily["candidate"] = f"{scope}_{name}"; daily["scope"] = scope; daily["sessions_before"] = 0; parts.append(daily)
        schedules.append(schedule.assign(scope=scope)); trades.append(tr.assign(scope=scope))
        checks[f"{scope}_baseline_parity_error"] = parity
    daily = pd.concat(parts, ignore_index=True); schedule = pd.concat(schedules, ignore_index=True); trades = pd.concat(trades, ignore_index=True)
    checks.update({
        "half_grid_only": bool(daily.overlay_held_eod.dropna().isin([0.0, 0.5]).all()),
        "call_preserved": bool(daily.call_pnl_ret.notna().all()),
        "grid_put_only_when_grid_held": bool((schedule.loc[schedule.original_qty.gt(0), "grid_held"] == True).all()),
        "negative_mom_is_two": bool((schedule.loc[schedule.grid_held & schedule.mom120_negative, "original_qty"] == 2).all()),
        "nonnegative_mom_equals_valuation": bool((schedule.loc[schedule.grid_held & ~schedule.mom120_negative, "original_qty"] == schedule.loc[schedule.grid_held & ~schedule.mom120_negative, "valuation_tier"]).all()),
    })
    if not all(value is True or (isinstance(value, float) and value <= 1e-12) for value in checks.values()):
        raise RuntimeError(f"invariant failure: {checks}")
    summary, wide, unavailable = timing.build_metrics(daily)
    summary.to_csv(RUN / "scan_summary.csv", index=False); wide.to_csv(RUN / "window_metrics.csv", index=False); daily.to_csv(RUN / "daily.csv.gz", index=False, compression="gzip"); schedule.to_csv(RUN / "grid_put_schedule.csv", index=False); trades.to_csv(RUN / "trades.csv.gz", index=False, compression="gzip")
    (RUN / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update(phase="complete", baseline={"candidate":"*_grid_put_off", "definition":"v1.4-r1 full joint; 0.5x grid"}, candidate_grid=[{"candidate":"*_grid_put_off","grid_put":0},{"candidate":"*_grid_put_mom2_else_valuation","mom_negative":2,"else":"valuation_tier"}], unavailable_segments=unavailable, cost_model={"grid_futures_units":0.5,"put_execution":"T-close selection/T+1 close","moneyness":1.02,"put_cost_multiplier":timing.PUT_COST_MULTIPLIER,"futures_buffer":0.30,"cash_annual":0.03}, outputs={**meta["outputs"],"daily":str(RUN / "daily.csv.gz"),"schedule":str(RUN / "grid_put_schedule.csv"),"trades":str(RUN / "trades.csv.gz"),"checks":str(RUN / "checks.json")}, decision="pending_research_judgment", stability_label="v14_grid_put_first_layer")
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")][["scope","candidate","ann_return","sharpe_repo","max_dd"]].to_string(index=False)
    (RUN / "record.md").write_text(f"# IM v1.4-r1 网格Put首层测试\n\n## Decision\n\n- pending_research_judgment。\n\n## Full\n\n```text\n{full}\n```\n", encoding="utf-8")
    print(full); print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
