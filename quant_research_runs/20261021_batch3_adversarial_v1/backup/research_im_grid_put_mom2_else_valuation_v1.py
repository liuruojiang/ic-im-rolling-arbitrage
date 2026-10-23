"""Research-only: independent IM grid Put, MOM120<0 => 2; else valuation tier."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_put102_mom_floor_quantity_v1 as q
from research_im_put102_model_tclose_selection_v1 import selection_series, tclose_model_runner
from research_im_put102_tclose_selection_open_execution_v1 import build_tclose_active

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_3_r7_put102_im_grid_independent_put_mom120_2_else_valuation_grid_put_v3_half_grid"
TARGET = 1.02


def half_grid(grid: pd.DataFrame) -> pd.DataFrame:
    """Apply the specified 0.5x grid notional while preserving its state for Put eligibility."""
    out = grid.copy()
    for col in out.columns:
        if col.startswith("overlay_") or col == "grid_carry":
            out[col] = out[col] * 0.5
    return out


def grid_schedule(template: pd.DataFrame, state: pd.DataFrame, grid: pd.DataFrame, scope: str) -> pd.DataFrame:
    out = template.copy()
    valuation = out.eval_date.map(state.valuation_tier)
    momentum = out.eval_date.map(state.momentum_120)
    held = out.execution_date.map(grid.set_index("date").overlay_held_eod)
    if valuation.isna().any() or momentum.isna().any() or held.isna().any():
        raise RuntimeError("missing grid/valuation/momentum state")
    desired = np.where(momentum.to_numpy(float) < 0, 2, valuation.to_numpy(int)) * held.to_numpy(int)
    # Same normalization as a 1x independent IM sleeve: model raw=4*q, real raw=8*q.
    raw = desired * (4 if scope == "model" else 8)
    out["binary_target_qty"] = raw.astype(int)
    out["three_tier_target_qty"] = raw.astype(int)
    out["original_qty"] = desired.astype(int)
    out["grid_held_target"] = held.astype(int)
    out["valuation_tier"] = valuation.astype(int)
    out["mom120_active"] = momentum.lt(0)
    out["mom120_floor_qty"] = np.where(momentum.lt(0), 2, 0)
    out["put_buy_allowed"] = True
    return out


def metrics(daily: pd.DataFrame, candidate: str, scope: str) -> list[dict]:
    rows = []
    for segment, years in q.run.first.WINDOWS:
        start = daily.date.min() if years is None else q.run.END - pd.DateOffset(years=years)
        if start < daily.date.min():
            rows.append(dict(candidate=candidate, scope=scope, segment=segment, start="N/A", end=str(q.run.END.date()), rows=0, ann_return="N/A", ann_vol="N/A", sharpe_repo="N/A", max_dd="N/A"))
        else:
            x = q.run.first.metrics(daily, start)
            x.update(candidate=candidate, scope=scope, segment=segment)
            rows.append(x)
    return rows


def main() -> None:
    if (RUN / "scan_summary.csv").exists():
        raise RuntimeError("Refusing to overwrite completed scan")
    state = pd.read_csv(q.run.BASE / "valuation_state_through_last_required_eval.csv.gz", parse_dates=["date"]).set_index("date")
    market = pd.read_csv(q.run.BASE / "model_market.csv.gz", parse_dates=["date"])
    upstream = pd.read_csv(q.run.BASE / "real_upstream.csv.gz", parse_dates=["date"])
    active = pd.read_csv(q.run.BASE / "real_active.csv.gz", parse_dates=["date"])
    raw_options = pd.read_csv(q.run.BASE / "real_options.csv.gz", parse_dates=["date", "contract_month", "rule_expiry", "actual_expiry"])
    options = q.run.engine.with_execution_prices(raw_options)
    templates = {scope: {s: pd.read_csv(q.run.FULL / f"{scope}_dual_{s}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"]) for s in ("core", "mom")} for scope in ("model", "real")}
    all_daily, all_trades, rows, schedule_rows = [], [], [], []
    checks: dict[str, object] = {}
    for scope in ("model", "real"):
        base = pd.read_csv(q.run.JOINT / f"{scope}_baseline_base.csv.gz", parse_dates=["date"])
        grid = pd.read_csv(q.run.JOINT / f"{scope}_baseline_grid.csv.gz", parse_dates=["date"])
        grid_leg = half_grid(grid)
        all_templates = {**templates[scope], "grid": templates[scope]["core"]}
        if scope == "model":
            selection, exceptions = selection_series(market, all_templates)
            runner = tclose_model_runner(selection)
        else:
            selected_active, exceptions = build_tclose_active(active, all_templates)
        reset_dates = q.run.engine.monthly_dates(base.date)
        core_mom = []
        for sleeve in ("core", "mom"):
            schedule = q.schedule(templates[scope][sleeve], state, base.momentum_weight, scope, sleeve, 3)
            label = f"{scope}_base_{sleeve}"
            if scope == "model":
                put, trade, _ = runner(market, schedule, "3m", TARGET, label, reset_dates=reset_dates)
                scale = (.5 if sleeve == "core" else .25) / 4
            else:
                put, trade, _ = q.run.engine.run_real_monthly_close(upstream, options, selected_active, schedule, "3m", TARGET, label, reset_dates=reset_dates)
                scale = .25 / 4
            put[q.run.first.FIELDS] *= scale
            core_mom.append((put, trade.assign(sleeve=sleeve, leg="base")))
        gs = grid_schedule(templates[scope]["core"], state, grid, scope)
        schedule_rows.append(gs.assign(scope=scope))
        label = f"{scope}_grid_put"
        if scope == "model":
            gp, gt, _ = runner(market, gs, "3m", TARGET, label, reset_dates=reset_dates)
            gscale = .5 / 4
        else:
            gp, gt, _ = q.run.engine.run_real_monthly_close(upstream, options, selected_active, gs, "3m", TARGET, label, reset_dates=reset_dates)
            gscale = .25 / 4
        gp[q.run.first.FIELDS] *= gscale
        base_put = sum(x[0][q.run.first.FIELDS] for x in core_mom)
        for base_candidate, put, trades in [("grid_put_off", base_put, [x[1] for x in core_mom]), ("grid_put_mom2_else_valuation", base_put + gp[q.run.first.FIELDS], [x[1] for x in core_mom] + [gt.assign(sleeve="grid", leg="grid")])]:
            # Candidate IDs are global within a scan; keep model and real rows distinct for strict audit.
            candidate = f"{scope}_{base_candidate}"
            put = put.copy(); put["date"] = base.date
            daily = q.run.comp.compose(base, put, grid_leg, None)
            daily["candidate"], daily["scope"] = candidate, scope
            all_daily.append(daily)
            tr = pd.concat(trades, ignore_index=True); tr["candidate"], tr["scope"] = candidate, scope
            all_trades.append(tr)
            rows.extend(metrics(daily, candidate, scope))
        checks[f"{scope}_initial_selection_exceptions"] = exceptions
    daily = pd.concat(all_daily, ignore_index=True)
    trades = pd.concat(all_trades, ignore_index=True)
    schedules = pd.concat(schedule_rows, ignore_index=True)
    summary = pd.DataFrame(rows)
    wide = summary.pivot(index=["candidate", "scope"], columns="segment", values=["ann_return", "ann_vol", "sharpe_repo", "max_dd"]).reset_index()
    wide.columns = ["_".join(c).rstrip("_") if isinstance(c, tuple) else c for c in wide.columns]
    checks.update({
        "call_zero": bool(daily[["call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction", "call_coverage"]].abs().to_numpy().max() == 0),
        "grid_put_off_is_zero": bool(schedules.original_qty.ge(0).all()),
        "grid_put_nonzero_only_when_grid_held": bool((schedules.loc[schedules.original_qty.gt(0), "grid_held_target"] == 1).all()),
        "negative_mom_grid_target_is_two": bool((schedules.loc[(schedules.grid_held_target.eq(1)) & (schedules.mom120_active), "original_qty"] == 2).all()),
        "nonnegative_mom_grid_target_equals_valuation": bool((schedules.loc[(schedules.grid_held_target.eq(1)) & (~schedules.mom120_active), "original_qty"] == schedules.loc[(schedules.grid_held_target.eq(1)) & (~schedules.mom120_active), "valuation_tier"]).all()),
        "signal_precedes_execution": bool(trades.signal_eval_date.le(trades.actual_execution_date).all()),
    })
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    daily.to_csv(RUN / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(RUN / "trades.csv", index=False)
    schedules.to_csv(RUN / "grid_put_schedule.csv", index=False)
    (RUN / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    unavailable = {f"real_{name}": {"last_10y": "真实MO期不足10年", "last_5y": "真实MO期不足5年"} for name in ("grid_put_off", "grid_put_mom2_else_valuation")}
    meta.update(phase="complete", scan_type="two_candidate_grid_put_ablation", baseline={"candidate":"model_grid_put_off / real_grid_put_off","definition":"0.5x grid retained; core/momentum Put floor3; Call off"}, candidate_grid=[{"candidate":"*_grid_put_off","grid_put":0},{"candidate":"*_grid_put_mom2_else_valuation","mom120_negative":2,"otherwise":"valuation_tier"}], unavailable_segments=unavailable, data_snapshot={"model":[str(market.date.min().date()),str(market.date.max().date())],"real":[str(upstream.date.min().date()),str(upstream.date.max().date())]}, cost_model={"put_execution":"T-close selection/T+1 close","moneyness":1.02,"futures_buffer":.30,"cash_annual":.03,"call":"off","grid_futures_notional":0.5}, outputs={**meta["outputs"],"daily":str((RUN/"daily_candidates.csv.gz").relative_to(ROOT)),"trades":str((RUN/"trades.csv").relative_to(ROOT)),"grid_schedule":str((RUN/"grid_put_schedule.csv").relative_to(ROOT)),"checks":str((RUN/"checks.json").relative_to(ROOT))}, decision="pending_research_judgment", stability_label="grid_put_first_layer")
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    full = summary.loc[summary.segment.eq("full"),["scope","candidate","ann_return","sharpe_repo","max_dd"]].to_string(index=False)
    (RUN / "record.md").write_text(f"# IM 网格独立Put：MOM120负两张、其他估值\n\n## Data\n\n- 模型和真实MO期；T收盘选约、T+1收盘成交，102%目标行权价；核心/动量Put floor3，Call关闭。\n\n## Decision\n\n- pending_research_judgment。\n\n## Stability\n\n- grid_put_first_layer。\n\n## Full\n\n```text\n{full}\n```\n",encoding="utf-8")
    with (RUN/"command_log.txt").open("a",encoding="utf-8") as f:f.write("python -X utf8 research_im_grid_put_mom2_else_valuation_v1.py\n")
    print(full);print(json.dumps(checks,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
