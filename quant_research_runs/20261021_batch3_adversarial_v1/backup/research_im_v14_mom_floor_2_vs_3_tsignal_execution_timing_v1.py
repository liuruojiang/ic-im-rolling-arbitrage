"""Research-only v1.4 IM Put timing sensitivity.

All variants select the 102% Put with the signal-day (T) IM close.  They then
compare T+1 official open with a same-contract T-close proxy.  The T+1-close
path is retained solely as a pricing reference.  Grid, Call and router stay
on the v1.4 replay; only core/momentum long-Put selection and transaction
price are adapted in memory.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_im_v13_full_short95_profit3x_joint_v1 as joint
import research_im_v14_put_monthly_roll_timing_v1 as timing
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity
from im_put_maturity_valuation_tiers_v3 import metrics


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_4_r1_im_core_and_momentum_long_put_t_signal_strike_selection_and_execution_price_timing_negative_mom120_floor_2_vs_3"
SPEC = ROOT / "docs" / "im_v14_mom_floor_2_vs_3_tsignal_execution_timing_v1_spec.md"
PUT_FIELDS = list(joint.PUT_FIELDS)
FLOORS = (2, 3)
MODES = ("tclose_select_t1_close", "tclose_select_t1_open", "tclose_select_tclose_proxy")


def read(path: Path, dates=("date",)) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=list(dates), low_memory=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def timing_options(raw: pd.DataFrame) -> pd.DataFrame:
    out = portfolio.full.engine.with_execution_prices(raw).sort_values(["contract", "date"]).copy()
    usable_open = out.open.notna() & np.isfinite(out.open) & out.open.gt(0)
    out["execution_open_price"] = np.where(usable_open, out.open, out.close)
    out["execution_open_source"] = np.where(usable_open, "official_open", "close_fallback_open_missing")
    prior_close = out.groupby("contract", sort=False).close.shift()
    usable_prior = prior_close.notna() & np.isfinite(prior_close) & prior_close.gt(0)
    out["tclose_proxy_price"] = np.where(usable_prior, prior_close, out.execution_open_price)
    out["tclose_proxy_source"] = np.where(usable_prior, "prior_session_official_close", "open_or_close_fallback_prior_close_missing")
    return out


def active_with_tclose_reference(active: pd.DataFrame, schedules: list[pd.DataFrame]) -> tuple[pd.DataFrame, list[str]]:
    original = active.set_index("date").sort_index()
    result = original.copy()
    exceptions: list[str] = []
    refs: dict[pd.Timestamp, pd.Timestamp] = {}
    for schedule in schedules:
        refs.update({pd.Timestamp(r.execution_date): pd.Timestamp(r.eval_date) for r in schedule.itertuples(index=False)})
    for execution, evaluation in refs.items():
        if evaluation in original.index:
            result.loc[execution, "close"] = original.loc[evaluation, "close"]
        else:
            exceptions.append(f"{execution.date()}<-{evaluation.date()}")
    return result.reset_index(), exceptions


def causal_core_schedule(base_dates: pd.Series, scope: str, routed: pd.DataFrame, floor: int) -> pd.DataFrame:
    # v1.4's stored helper reindexed IMC state at execution_date.  This run
    # deliberately gates it at eval_date so the stated T signal is causal.
    schedule = valuation.corrected_core_schedule(base_dates, scope, None).copy()
    state = valuation.effective_state().set_index("date")
    mom = state.reindex(pd.DatetimeIndex(schedule.eval_date)).momentum_120
    if mom.isna().any():
        raise RuntimeError("Missing T-day MOM120 for core schedule")
    valuation_tier = schedule.valuation_tier.to_numpy(dtype=int)
    parent = np.maximum(valuation_tier, np.where(mom.lt(0), floor, 0))
    factor = 8 if scope == "real" else 4
    allowed = routed.set_index("date").state.eq("imc").reindex(pd.DatetimeIndex(schedule.eval_date), fill_value=False)
    schedule["binary_target_qty"] = np.where(allowed.to_numpy(), parent * factor, 0).astype(int)
    schedule["momentum_120"] = mom.to_numpy(dtype=float)
    schedule["mom120_floor_active"] = mom.lt(0).to_numpy(dtype=bool)
    schedule["put_buy_allowed"] = True
    return schedule


def momentum_schedule(base: pd.DataFrame, scope: str, floor: int) -> pd.DataFrame:
    template = read(portfolio.ARTIFACT / f"{scope}_combined_mom_schedule.csv.gz", ("eval_date", "execution_date"))
    template = template[template.execution_date.le(portfolio.END)].reset_index(drop=True)
    state = read(portfolio.full.BASE / "valuation_state_through_last_required_eval.csv.gz").set_index("date")
    mom = template.eval_date.map(state.momentum_120)
    if mom.isna().any():
        raise RuntimeError("Missing T-day MOM120 for momentum schedule")
    target = np.where(mom.lt(0), floor, 0) * 2.0 * base.momentum_weight.to_numpy(dtype=float) * 4.0
    if not np.allclose(target, np.rint(target)):
        raise RuntimeError("Momentum target must be integral")
    template["binary_target_qty"] = np.rint(target).astype(int)
    template["three_tier_target_qty"] = template.binary_target_qty
    template["momentum_120"] = mom.to_numpy(dtype=float)
    template["mom120_active"] = mom.lt(0).to_numpy(dtype=bool)
    template["put_buy_allowed"] = True
    return template


def timed_engine(source_fn, price_column: str, method_name: str, source_text: str | None = None):
    """Build a scoped copy of the existing engine, replacing only trade price."""
    source = inspect.getsource(source_fn) if source_text is None else source_text
    # The core engine is created by exec and its runtime __name__ may be a
    # short alias, while inspect exposes the original def line.
    source = source.lstrip()
    first, remainder = source.split("\n", 1)
    source = "def " + method_name + "(" + first.split("(", 1)[1] + "\n" + remainder
    source = source.replace("legacy.select_close_contract", "legacy.select_timed_contract")
    source = source.replace("legacy.executable_close", "legacy.executable_timed")
    source = source.replace("float(selected['close'])", f"float(selected['{price_column}'])")
    source = source.replace("float(oldq['close'])", f"float(oldq['{price_column}'])")
    source = source.replace("float(oldq['open'] if route_open_exit else oldq['close'])", f"float(oldq['{price_column}'])")
    source = source.replace("((route_open_exit and float(oldq['open'])>0 and float(oldq['volume'])>0 and float(oldq['open_interest'])>0) or (not route_open_exit and legacy.executable_timed(oldq)))", "legacy.executable_timed(oldq)")
    globs = dict(source_fn.__globals__)
    legacy = SimpleNamespace(**vars(globs["legacy"]))

    def executable_timed(row: pd.Series) -> bool:
        return bool(pd.notna(row[price_column]) and np.isfinite(float(row[price_column])) and float(row[price_column]) > 0)

    def select_timed_contract(options: pd.DataFrame, im_close: pd.Series, day: pd.Timestamp, month: pd.Timestamp, moneyness: float) -> pd.Series:
        chain = options.loc[options.date.eq(day) & options.contract_month.eq(month)].copy()
        if chain.empty:
            raise RuntimeError(f"Missing chain: {day.date()} {month.date()}")
        chain["entry_moneyness"] = chain.strike / float(im_close.loc[day])
        chain["target_error"] = (chain.entry_moneyness - moneyness).abs().round(12)
        selected = chain.sort_values(["target_error", "strike", "contract"]).iloc[0].copy()
        if not executable_timed(selected):
            raise RuntimeError(f"Selected contract lacks {price_column}: {day.date()} {selected.contract}")
        return selected

    legacy.executable_timed = executable_timed
    legacy.select_timed_contract = select_timed_contract
    globs["legacy"] = legacy
    globs.update({"pd": pd, "np": np, "math": math})
    exec(source, globs)
    return globs[method_name]


def run_engine(mode: str, engines: dict[str, object], upstream, options, active, schedule, label, *, reset_dates, core: bool, route_dates=frozenset()):
    if core:
        kwargs = {"reset_dates": reset_dates, "profit_multiple": 3.0, "profit_delay_sessions": 1, "open_exit_dates": route_dates, "market": None}
        if mode == "tclose_select_t1_close":
            real_engine, _, _ = joint.component.profit_route_engines()
            return real_engine(upstream, options, active, schedule, "3m", 1.02, label, **kwargs)
        return engines[mode + "_core"](upstream, options, active, schedule, "3m", 1.02, label, **kwargs)
    if mode == "tclose_select_t1_close":
        return portfolio.full.engine.run_real_monthly_close(upstream, options, active, schedule, "3m", 1.02, label, reset_dates=reset_dates)
    return engines[mode + "_momentum"](upstream, options, active, schedule, "3m", 1.02, label, reset_dates=reset_dates)


def metric_rows(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rows = []; wide = []; unavailable = {}
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        item = {"candidate": candidate, "scope": "real"}
        for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
            start = group.date.min() if years is None else group.date.max() - pd.DateOffset(years=years)
            if years is not None and group.date.min() > start:
                values = {k: "N/A" for k in ("ann_return", "ann_vol", "sharpe_repo", "max_dd")}
                unavailable.setdefault(candidate, {})[segment] = f"history starts {group.date.min().date()}"
                sample = group.iloc[:0]
            else:
                sample = group[group.date.ge(start)]; values = metrics(sample.ret)
            rows.append({"candidate": candidate, "segment": segment, "start": str(start.date()), "end": str(group.date.max().date()), "rows": len(sample), "scope": "real", **values})
            item.update({f"{k}_{segment}": v for k, v in values.items()})
        wide.append(item)
    return pd.DataFrame(rows), pd.DataFrame(wide), unavailable


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init scan")
    weights = portfolio.current_momentum_weights()
    base, base_audit = portfolio.rebuild_base("real", weights)
    grid = portfolio.half_grid("real")
    market, router_base, raw_options, _, futures, quarter_error = joint.quarterly_router_inputs("real", base)
    router_fn, router_source = joint.audited_router()
    signal = maturity.prepare_signal(router_base, raw_options, "m1")
    routed, _, cycles = router_fn(router_base, raw_options, futures, signal, 0.35, common.FALLBACK, 0.60)
    routed["date"] = pd.to_datetime(routed.date)
    route_dates = set(pd.to_datetime(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"]))
    call, call_trades, _ = maturity.call_inputs("real", base.date, routed.state.eq("imc").astype(float), "timing", RUN / "call_artifacts")
    upstream = read(portfolio.full.BASE / "real_upstream.csv.gz")
    upstream = upstream[upstream.date.isin(base.date)].reset_index(drop=True)
    active = read(portfolio.full.BASE / "real_active.csv.gz")
    active = active[active.date.isin(base.date)].reset_index(drop=True)
    raw = read(portfolio.full.BASE / "real_options.csv.gz", ("date", "contract_month", "rule_expiry", "actual_expiry"))
    options = timing_options(raw[raw.date.isin(base.date)].reset_index(drop=True))
    core_source, _, executed = joint.component.profit_route_engines()
    momentum_source = portfolio.full.engine.run_real_monthly_close
    engines = {
        "tclose_select_t1_open_core": timed_engine(core_source, "execution_open_price", "run_core_t1_open", executed.split("\n\n\ndef run_model_profit_restrike", 1)[0]),
        "tclose_select_tclose_proxy_core": timed_engine(core_source, "tclose_proxy_price", "run_core_tclose_proxy", executed.split("\n\n\ndef run_model_profit_restrike", 1)[0]),
        "tclose_select_t1_open_momentum": timed_engine(momentum_source, "execution_open_price", "run_momentum_t1_open"),
        "tclose_select_tclose_proxy_momentum": timed_engine(momentum_source, "tclose_proxy_price", "run_momentum_tclose_proxy"),
    }
    daily_parts=[]; trade_parts=[]; audit_parts=[]; checks=[]
    reset_dates = portfolio.full.engine.monthly_dates(base.date)
    for floor in FLOORS:
        core_schedule = causal_core_schedule(base.date, "real", routed, floor)
        mom_schedule = momentum_schedule(base, "real", floor)
        tactive, exceptions = active_with_tclose_reference(active, [core_schedule, mom_schedule])
        for mode in MODES:
            core, core_trades, _ = run_engine(mode, engines, upstream, options, tactive, core_schedule, f"{mode}_floor{floor}_core", reset_dates=reset_dates, core=True, route_dates=route_dates)
            momentum, mom_trades, _ = run_engine(mode, engines, upstream, options, tactive, mom_schedule, f"{mode}_floor{floor}_momentum", reset_dates=reset_dates, core=False)
            core = common.scale_put(core, "real"); core[PUT_FIELDS] *= 0.5; core["put_cost_rate"] *= joint.PUT_COST_MULTIPLIER
            momentum[PUT_FIELDS] *= 0.25 / 4.0
            total = joint.combine_puts(core, momentum, joint.PUT_COST_MULTIPLIER)
            daily = joint.compose_routed(base, routed, total, grid, call)
            candidate=f"{mode}_floor{floor}"; daily["candidate"]=candidate; daily["floor"]=floor; daily["mode"]=mode
            daily_parts.append(daily)
            trades = pd.concat([core_trades.assign(sleeve="core"), mom_trades.assign(sleeve="momentum")], ignore_index=True)
            trades["candidate"]=candidate; trades["floor"]=floor; trades["mode"]=mode
            trade_parts.append(trades)
            entries = trades[trades.action.isin(["close_buy", "close_roll_monthly", "close_expiry_replace", "close_profit_restrike"])].copy()
            if not entries.empty:
                quotes = entries.merge(options[["date","contract","execution_open_source","tclose_proxy_source"]], how="left", left_on=["actual_execution_date","new_contract"], right_on=["date","contract"])
                quotes["execution_price_basis"] = {"tclose_select_t1_close":"T+1 official close", "tclose_select_t1_open":"T+1 official open", "tclose_select_tclose_proxy":"T official close proxy"}[mode]
                audit_parts.append(quotes)
            checks.append({"candidate":candidate, "signal_before_execution": bool(trades.signal_eval_date.lt(trades.actual_execution_date).all()), "initial_reference_exceptions":";".join(exceptions), "open_price_fallbacks": int((entries.new_trade_price.isna()).sum())})
    daily = pd.concat(daily_parts, ignore_index=True); trades=pd.concat(trade_parts,ignore_index=True); execution_audit=pd.concat(audit_parts,ignore_index=True)
    summary, wide, unavailable = metric_rows(daily)
    paired=[]
    for floor in FLOORS:
        close=daily[daily.candidate.eq(f"tclose_select_t1_close_floor{floor}")].set_index("date").ret
        for mode in MODES[1:]:
            other=daily[daily.candidate.eq(f"{mode}_floor{floor}")].set_index("date").ret
            d=other-close
            paired.append({"floor":floor,"candidate":f"{mode}_floor{floor}","vs":"tclose_select_t1_close","nonzero_days":int(d.abs().gt(1e-15).sum()),"cum_arithmetic_diff":float(d.sum()),"largest_abs_date":str(d.abs().idxmax().date()),"largest_daily_diff":float(d.loc[d.abs().idxmax()])})
    out=RUN/"daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out/"daily.csv.gz",index=False,compression="gzip"); trades.to_csv(out/"trades.csv.gz",index=False,compression="gzip")
    execution_audit.to_csv(RUN/"execution_audit.csv",index=False); pd.DataFrame(checks).to_csv(RUN/"timing_checks.csv",index=False); pd.DataFrame(paired).to_csv(RUN/"paired_differences.csv",index=False)
    summary.to_csv(RUN/"scan_summary.csv",index=False,encoding="utf-8-sig"); wide.to_csv(RUN/"window_metrics.csv",index=False,encoding="utf-8-sig")
    (RUN/"executed_profit_engines.py").write_text(executed,encoding="utf-8"); (RUN/"executed_router.py").write_text(router_source,encoding="utf-8")
    meta.update({"scan_type":"timing sensitivity with fixed quantity candidates", "candidate_grid":[{"mode":m,"mom120_floor_qty":f} for m in MODES for f in FLOORS], "baseline":{"definition":"T signal selects strike; T+1 close reference; causal core route gate at T signal"}, "data_snapshot":{"real":[str(daily.date.min().date()),str(daily.date.max().date())],"source":"frozen CFFEX official IM/MO daily data in v1.4 replay","timezone":"Asia/Shanghai"}, "cost_model":{"execution":"T+1 official open or same-contract T-close proxy; fixed existing costs; no bid-ask/impact","put_side_cost_multiplier":joint.PUT_COST_MULTIPLIER}, "audits":{"base":base_audit,"quarter_unit_gross_error":quarter_error,"short_put_cycles":len(cycles),"call_trade_events":len(call_trades)}, "unavailable_segments":unavailable, "decision":"research_only_pending_timing_and_robustness_review", "stability_label":"not_for_parameter_selection", "source_hashes":{"script":sha256(Path(__file__)),"spec":sha256(SPEC)}, "outputs":{**meta["outputs"],"daily":str(out/"daily.csv.gz"),"trades":str(out/"trades.csv.gz"),"execution_audit":str(RUN/"execution_audit.csv"),"timing_checks":str(RUN/"timing_checks.csv"),"paired_differences":str(RUN/"paired_differences.csv")}, "warnings":["T-close proxy is not executable and is a timing sensitivity only.","T+1 open has no bid/ask, market-impact or order-book evidence.","This run fixes the core IMC gate to T signal date; it is not daily-return parity with the saved v1.4 artifact.","Listed real history is short; do not use this to select floor 2 or 3 without robustness evidence."], "git_status_after":subprocess.run(["git","status","--short"],cwd=ROOT,capture_output=True,text=True).stdout.strip()})
    meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    display=wide[["candidate","ann_return_full","sharpe_repo_full","max_dd_full","ann_return_last_3y","max_dd_last_3y"]]
    (RUN/"record.md").write_text("# IM v1.4 Put：T日选约、成交时点敏感性\n\n研究用途，不改正式主线或账本。\n\n"+display.to_markdown(index=False,floatfmt=".6f")+"\n\n逐笔选约和成交来源：`execution_audit.csv`；完整窗口指标：`scan_summary.csv`。\n",encoding="utf-8")
    (RUN/"command_log.txt").write_text("python -X utf8 research_im_v14_mom_floor_2_vs_3_tsignal_execution_timing_v1.py\n",encoding="utf-8")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
