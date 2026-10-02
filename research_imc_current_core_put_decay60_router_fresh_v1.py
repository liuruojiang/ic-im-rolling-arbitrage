"""Fresh matched replay: monthly IMC, current core Put, and high-IV short95 decay60 router.

Research only.  All candidates are rebuilt from the same base, option chain,
futures chain, dates, costs, and cash convention.  Saved performance outputs
from prior studies are not used as result inputs.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import im_mainline_v1_1 as policy
import research_im_short_put_recovery_atm_full_model_v1 as model_source
import research_imc_high_iv_short95_router_v1 as router
import research_imc_high_iv_short95_router_decay60_v1 as decay_router
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, metrics, prepare_options

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_current_core_put_high_iv_short95_decay60_fresh_v1"
THRESHOLDS = router.THRESHOLDS
FALLBACK = "continue_imc_when_ineligible"
DECAY = 0.60
CASH_DAILY = router.CASH_DAILY

# Reuse executable engine code, never its saved performance outputs.
ENGINE_PARENT = ROOT / "quant_param_scan_runs" / "20260908_im_full_combination_joint_iv_derisk_v1"
sys.path.insert(0, str(ENGINE_PARENT))
import run_joint as engine_parent  # noqa: E402

engine = engine_parent.engine
PUT_FIELDS = engine_parent.first.FIELDS


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def mom_floor_state(values: pd.Series) -> np.ndarray:
    """Current asymmetric rule: enter <0; release after two consecutive >+1%."""
    active = False
    streak = 0
    result: list[bool] = []
    for raw in values:
        if not np.isfinite(raw):
            active, streak = True, 0
        elif float(raw) < 0.0:
            active, streak = True, 0
        elif active:
            streak = streak + 1 if float(raw) > 0.01 else 0
            if streak >= 2:
                active, streak = False, 0
        result.append(active)
    return np.asarray(result, dtype=bool)


def core_schedule(dates: pd.Series, scope: str, imc_mask: pd.Series | None = None) -> pd.DataFrame:
    state, _ = policy.load_authoritative_local_state()
    state = state.set_index("date").sort_index()
    evaluation = pd.DatetimeIndex(dates.iloc[:-1])
    execution = pd.DatetimeIndex(dates.iloc[1:])
    valuation = state.reindex(evaluation).valuation_tier
    mom = state.reindex(evaluation).momentum_120
    if valuation.isna().any() or mom.isna().any():
        raise RuntimeError("Current core Put state does not cover the replay dates")
    floor = mom_floor_state(mom)
    parent_target = np.maximum(valuation.astype(int).to_numpy(), np.where(floor, 3, 0))
    factor = 4 if scope == "model" else 8
    target = parent_target * factor
    if imc_mask is not None:
        allowed = imc_mask.reindex(execution, fill_value=False).to_numpy(dtype=bool)
        target = np.where(allowed, target, 0)
    return pd.DataFrame({
        "eval_date": evaluation,
        "execution_date": execution,
        "binary_target_qty": target.astype(int),
        "valuation_tier": valuation.to_numpy(dtype=int),
        "momentum_120": mom.to_numpy(dtype=float),
        "mom120_floor_active": floor,
        "put_buy_allowed": True,
    })


def patched_put_engines():
    """Patch only route exits from close to the same T+1 open."""
    real_src = inspect.getsource(engine.run_real_monthly_close)
    real_src = real_src.replace(
        "def run_real_monthly_close(upstream,options,active_im,schedule,tenor,moneyness,label,*,reset_dates,max_delay=5,anchor=None,market=None):",
        "def run_real_route(upstream,options,active_im,schedule,tenor,moneyness,label,*,reset_dates,max_delay=5,anchor=None,market=None,open_exit_dates=frozenset()):",
    )
    real_src = real_src.replace(
        "day=pd.Timestamp(r.date);event=events.get(day)",
        "day=pd.Timestamp(r.date);route_open_exit=day in open_exit_dates;event=events.get(day)",
    )
    old_real = """elif active is not None and target==0 and legacy.executable_close(oldq):
            sell=old.qty;old_price=float(oldq['close']);points+=old.qty*.5*(old_price-old.prior_settle)
            active=None;action='close_exit'"""
    new_real = """elif active is not None and target==0 and ((route_open_exit and float(oldq['open'])>0 and float(oldq['volume'])>0 and float(oldq['open_interest'])>0) or (not route_open_exit and legacy.executable_close(oldq))):
            sell=old.qty;old_price=float(oldq['open'] if route_open_exit else oldq['close']);points+=old.qty*.5*(old_price-old.prior_settle)
            active=None;action='route_open_exit' if route_open_exit else 'close_exit'"""
    if old_real not in real_src:
        raise RuntimeError("Real core Put exit hook changed")
    real_src = real_src.replace(old_real, new_real)
    real_src = real_src.replace("execution_timing='close',action=action", "execution_timing='open' if action=='route_open_exit' else 'close',action=action")

    model_src = inspect.getsource(engine.run_model_monthly_close)
    model_src = model_src.replace(
        "def run_model_monthly_close(market, schedule, tenor, moneyness, label, *, reset_dates, anchor=None):",
        "def run_model_route(market, schedule, tenor, moneyness, label, *, reset_dates, anchor=None, open_exit_dates=frozenset()):",
    )
    model_src = model_src.replace(
        "day=pd.Timestamp(r.date); event=events.get(day)",
        "day=pd.Timestamp(r.date); route_open_exit=day in open_exit_dates; event=events.get(day)",
    )
    model_src = model_src.replace(
        "old_price=legacy.v6.option_price(active,r,'close')",
        "old_price=legacy.v6.option_price(active,r,'open' if route_open_exit and target==0 else 'close')",
    )
    model_src = model_src.replace(
        "cost+=active.fraction*legacy.PUT_SIDE_COST;active=None;action='close_exit'",
        "cost+=active.fraction*legacy.PUT_SIDE_COST;active=None;action='route_open_exit' if route_open_exit else 'close_exit'",
    )
    model_src = model_src.replace("execution_timing='close',", "execution_timing='open' if action=='route_open_exit' else 'close',")
    ns = dict(vars(engine))
    exec(compile(real_src, str(Path(__file__)), "exec"), ns)
    exec(compile(model_src, str(Path(__file__)), "exec"), ns)
    return ns["run_real_route"], ns["run_model_route"], real_src + "\n\n" + model_src


def apply_core_put(router_daily: pd.DataFrame, put: pd.DataFrame) -> pd.DataFrame:
    if not router_daily.date.reset_index(drop=True).equals(put.date.reset_index(drop=True)):
        raise RuntimeError("Router/core-Put date mismatch")
    out = router_daily.copy()
    out["core_put_pnl_ret"] = put.put_pnl_ret.to_numpy(dtype=float)
    out["core_put_cost_rate"] = put.put_cost_rate.to_numpy(dtype=float)
    out["core_put_mark_fraction"] = put.put_mark_fraction.to_numpy(dtype=float)
    # Router already credits 70% cash in IM/Put/recovery states.  A long core
    # Put consumes its marked premium capital, so remove that cash interest.
    out["return_net"] = out.return_net + out.core_put_pnl_ret - out.core_put_cost_rate - out.core_put_mark_fraction * CASH_DAILY
    out["nav"] = (1 + out.return_net).cumprod()
    if not np.isfinite(out.return_net).all() or (1 + out.return_net).le(0).any():
        raise RuntimeError("Invalid combined return")
    return out


def scale_put(put: pd.DataFrame, scope: str) -> pd.DataFrame:
    # Validated engine normalization for the existing 0.5 core is real=.0625,
    # model=.125.  Double it to protect the requested full 1x IM core.
    scale = 0.125 if scope == "real" else 0.25
    result = put.copy()
    result[PUT_FIELDS] = result[PUT_FIELDS] * scale
    return result


def run_layer(scope: str, run_router_decay, real_put_engine, model_put_engine):
    if scope == "real":
        base = pd.read_csv(router.BASE, parse_dates=["date"])
        raw = pd.read_csv(router.OPTIONS, parse_dates=["date"])
        raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
        options = prepare_options(raw, actual_expiry_map(raw, base))
        options_for_put = engine.with_execution_prices(options.copy())
        futures = pd.read_csv(router.FUTURES, parse_dates=["date"])
        signal = router.prepare_signal(base, options)
        baseline_return = base.baseline_plus_cash_ret.astype(float)
        baseline_nav_reference = base.nav_baseline_plus_cash.astype(float)
    else:
        market, base, options, futures, _, _ = model_source.build_inputs()
        options = options.copy(); options["close"] = options["settle"]
        options_for_put = None
        signal = router.prepare_signal(base, options)
        baseline_return = base.baseline_plus_cash_ret.astype(float)
        baseline_nav_reference = (1 + baseline_return).cumprod()

    parity = float(np.max(np.abs((1 + baseline_return).cumprod().to_numpy() - baseline_nav_reference.to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"{scope} bare baseline parity failed: {parity}")

    bare = pd.DataFrame({"date": base.date, "candidate": f"{scope}_bare_monthly_imc", "return_net": baseline_return})
    bare["nav"] = (1 + bare.return_net).cumprod(); bare["state"] = "imc"; bare["action"] = ""

    always_imc = pd.Series(True, index=pd.DatetimeIndex(base.date))
    schedule = core_schedule(base.date, scope, always_imc)
    if scope == "real":
        put, put_trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=engine.monthly_dates(base.date), market=None)
    else:
        put, put_trades, _ = model_put_engine(market, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=engine.monthly_dates(base.date))
    put = scale_put(put, scope)
    protected = apply_core_put(bare, put); protected["candidate"] = f"{scope}_monthly_imc_current_core_put102"

    candidates = [bare, protected]
    audits: dict[str, object] = {"bare_nav_parity": parity, "core_put_trade_events": len(put_trades)}
    trade_parts = [put_trades.assign(candidate=f"{scope}_monthly_imc_current_core_put102")]
    for threshold in THRESHOLDS:
        routed, short_events, short_cycles = run_router_decay(base, options, futures, signal, threshold, FALLBACK, DECAY)
        routed["date"] = pd.to_datetime(routed.date)
        imc_mask = routed.set_index("date").state.eq("imc")
        # `action` can be superseded by a same-close 60% decay signal on a
        # newly opened short Put.  `route` is the stable transition identity.
        route_exit_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
        schedule = core_schedule(base.date, scope, imc_mask)
        label = f"{scope}_iv{int(threshold*1000):03d}_core_put_to_short95_decay60"
        if scope == "real":
            put, trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, label, reset_dates=engine.monthly_dates(base.date), market=None, open_exit_dates=route_exit_dates)
        else:
            put, trades, _ = model_put_engine(market, schedule, "3m", 1.02, label, reset_dates=engine.monthly_dates(base.date), open_exit_dates=route_exit_dates)
        put = scale_put(put, scope)
        combined = apply_core_put(routed, put); combined["candidate"] = label
        candidates.append(combined); trade_parts.append(trades.assign(candidate=label))
        simultaneous = set(trades.loc[trades.action.eq("route_open_exit"), "actual_execution_date"])
        if not simultaneous.issubset(route_exit_dates):
            raise RuntimeError(f"{label} core Put exited outside route switches: {simultaneous - route_exit_dates}")
        audits[label] = {"route_switches": len(route_exit_dates), "simultaneous_core_put_open_exits": len(simultaneous), "route_switches_without_active_core_put": len(route_exit_dates - simultaneous), "short_put_early_rolls": int(routed.action.eq("put_early_roll60_buyback_and_sell_next_open").sum())}
    return pd.concat(candidates, ignore_index=True), pd.concat(trade_parts, ignore_index=True), signal, audits


def window_tables(daily: pd.DataFrame):
    rows, wide_rows, unavailable = [], [], {}
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        wide = {"candidate": candidate}
        for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
            start = group.date.min() if years is None else group.date.max() - pd.DateOffset(years=years)
            valid = years is None or group.date.min() <= start
            if valid:
                sample = group[group.date >= start]; values = metrics(sample.return_net)
            else:
                sample = group.iloc[:0]; values = {k: "N/A" for k in ("ann_return", "ann_vol", "sharpe_repo", "max_dd")}
                unavailable.setdefault(candidate, {})[segment] = "history shorter than requested window"
            rows.append({"candidate": candidate, "segment": segment, "start": str(start.date()), "end": str(group.date.max().date()), "rows": len(sample), **values})
            for key, value in values.items(): wide[f"{key}_{segment}"] = value
        wide_rows.append(wide)
    return pd.DataFrame(rows), pd.DataFrame(wide_rows), unavailable


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    run_router_decay, router_source = decay_router.runner()
    real_put_engine, model_put_engine, put_source = patched_put_engines()
    real_daily, real_trades, real_signal, real_audit = run_layer("real", run_router_decay, real_put_engine, model_put_engine)
    model_daily, model_trades, model_signal, model_audit = run_layer("model", run_router_decay, real_put_engine, model_put_engine)
    daily = pd.concat([real_daily, model_daily], ignore_index=True)
    summary, wide, unavailable = window_tables(daily)
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([real_trades, model_trades], ignore_index=True).to_csv(out / "core_put_trades.csv", index=False)
    real_signal.assign(layer="real").to_csv(out / "real_signal_audit.csv", index=False)
    model_signal.assign(layer="model").to_csv(out / "model_signal_audit.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + put_source, encoding="utf-8")
    meta.update(scan_type="fresh_matched_core_put_high_iv_router_decay60", baseline={"candidate": "*_bare_monthly_imc", "source": str(router.BASE)}, candidate_grid=[{"iv_threshold": x, "fallback": FALLBACK, "short_put_decay": DECAY} for x in THRESHOLDS], data_snapshot={"real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()), "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date()), "base_sha256": sha(router.BASE), "options_sha256": sha(router.OPTIONS), "futures_sha256": sha(router.FUTURES)}, cost_model={"one_way_notional": router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03, "core_put_target": "full-1x scaled current core valuation/MOM120 debounce; 102%; about 3m; monthly close maintenance", "route_switch": "same T+1 open: close IM, close core Put, sell short95 Put", "short_put_early_roll": "60% premium decay at close; next open cover old and sell immediately-next month; one roll; re-admission required"}, audit={"real": real_audit, "model": model_audit}, unavailable_segments=unavailable, outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "core_put_trades": str(out / "core_put_trades.csv"), "real_signal_audit": str(out / "real_signal_audit.csv"), "model_signal_audit": str(out / "model_signal_audit.csv"), "executed_state_machines": str(RUN / "executed_state_machines.py")}, source_hashes={"script": sha(Path(__file__)), "router": sha(ROOT / "research_imc_high_iv_short95_router_v1.py"), "decay_router": sha(ROOT / "research_imc_high_iv_short95_router_decay60_v1.py")}, warnings=["Historical 102% and MOM120 debounce replay is counterfactual; live effective dates are not rewritten.", "Model layer is theoretical/proxy and not executable history.", "No bid-ask, impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing."], decision="research_only_pending_interpretation", stability_label="fresh_matched_replay_pending_review", git_status_after=git_status())
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    record = "# 月度IMC＋当前核心Put＋高IV卖Put60%提前换月：统一重算\n\n本轮从同一原始月度IM/MO数据与统一模型重新计算；不读取旧绩效CSV作为结果输入。历史102%与防抖属于反事实回放。\n\n## Full Results\n\n" + full.to_markdown(index=False) + "\n\n## Audit\n\n" + json.dumps(meta["audit"], ensure_ascii=False, indent=2) + "\n\n## Decision\n\nresearch_only_pending_interpretation\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle: handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(meta["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
