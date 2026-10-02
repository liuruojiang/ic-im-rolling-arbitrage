"""Joint state-machine test: IM high-IV short95 route plus 3x core-Put restrike."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v13_core_put_profit_restrike_robustness_v2 as profit
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as parity
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity
import research_imc_short95_maturity_corrected_v2 as maturity_v2


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_fixed_core_high_iv_short_put_plus_core_put_short95_m1_iv35_decay60_x_profit3x_four_way_joint"
SPEC = ROOT / "docs" / "im_short95_profit3x_joint_v1_spec.md"
EXECUTED_PROFIT = (
    ROOT / "quant_param_scan_runs" /
    "20260916_ic_im_im_v1_3_current_counterfactual_corrected_full_portfolio_core_put_profit_restrike_robustness_2_5x_3x_3_5x_t1_t2" /
    "executed_corrected_profit_engines.py"
)
PUT_COST_MULTIPLIER = 5.0
OPTION_ONE_WAY_COST = 0.0005


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def router_with_option_cost_5bp():
    _, source = parity.parity_fixed_runner()
    entry_cost = "float(base.iloc[i - 1].csi1000_price_close) * ONE_WAY_COST"
    if source.count(entry_cost) != 2:
        raise RuntimeError("Expected exactly two short-Put entry-cost hooks")
    source = source.replace(
        entry_cost,
        "float(base.iloc[i - 1].csi1000_price_close) * OPTION_ONE_WAY_COST",
    )
    source = source.replace(
        "prior_spot * (2 * ONE_WAY_COST)",
        "prior_spot * (2 * OPTION_ONE_WAY_COST)",
    )
    if "prior_spot * (2 * OPTION_ONE_WAY_COST)" not in source:
        raise RuntimeError("Short-Put early-roll cost hook was not upgraded to 5BP")
    namespace = dict(vars(common.router))
    namespace["OPTION_ONE_WAY_COST"] = OPTION_ONE_WAY_COST
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    raw = namespace["run_router_decay"]

    def run(base, options, futures, signal, threshold, fallback, decay):
        daily, events, cycles = raw(base, options, futures, signal, threshold, fallback, decay)
        daily["early_roll_executed"] = daily.action.eq("put_early_roll60_buyback_and_sell_next_open")
        daily["early_roll_threshold"] = decay
        return daily, events, cycles

    return run, source


def profit_route_engines():
    source = EXECUTED_PROFIT.read_text(encoding="utf-8")
    marker = "\ndef run_model_profit_restrike"
    pos = source.index(marker)
    real_src, model_src = source[:pos], source[pos + 1:]

    real_src = real_src.replace(
        "profit_delay_sessions=1):",
        "profit_delay_sessions=1,open_exit_dates=frozenset()):",
        1,
    ).replace(
        "day=pd.Timestamp(r.date);event=events.get(day)",
        "day=pd.Timestamp(r.date);route_open_exit=day in open_exit_dates;event=events.get(day)",
        1,
    )
    old_real = """elif active is not None and target==0 and legacy.executable_close(oldq):
            sell=old.qty;old_price=float(oldq['close']);points+=old.qty*.5*(old_price-old.prior_settle)
            active=None;action='close_exit'"""
    new_real = """elif active is not None and target==0 and ((route_open_exit and float(oldq['open'])>0 and float(oldq['volume'])>0 and float(oldq['open_interest'])>0) or (not route_open_exit and legacy.executable_close(oldq))):
            sell=old.qty;old_price=float(oldq['open'] if route_open_exit else oldq['close']);points+=old.qty*.5*(old_price-old.prior_settle)
            active=None;action='route_open_exit' if route_open_exit else 'close_exit'"""
    if old_real not in real_src:
        raise RuntimeError("Real route hook changed")
    real_src = real_src.replace(old_real, new_real, 1).replace(
        "execution_timing='close',action=action",
        "execution_timing='open' if action=='route_open_exit' else 'close',action=action",
        1,
    )

    model_src = model_src.replace(
        "profit_delay_sessions=1):",
        "profit_delay_sessions=1,open_exit_dates=frozenset()):",
        1,
    ).replace(
        "day=pd.Timestamp(r.date); event=events.get(day)",
        "day=pd.Timestamp(r.date); route_open_exit=day in open_exit_dates; event=events.get(day)",
        1,
    ).replace(
        "old_price=legacy.v6.option_price(active,r,'close')",
        "old_price=legacy.v6.option_price(active,r,'open' if route_open_exit and target==0 else 'close')",
        1,
    ).replace(
        "cost+=active.fraction*legacy.PUT_SIDE_COST;active=None;action='close_exit'",
        "cost+=active.fraction*legacy.PUT_SIDE_COST;active=None;action='route_open_exit' if route_open_exit else 'close_exit'",
        1,
    ).replace(
        "scheduled_execution_date=day,actual_execution_date=day,execution_timing='close',",
        "scheduled_execution_date=day,actual_execution_date=day,execution_timing='open' if action=='route_open_exit' else 'close',",
        1,
    )
    real_ns = dict(vars(common.engine)); model_ns = dict(vars(common.engine))
    exec(compile(real_src, str(Path(__file__)), "exec"), real_ns)
    exec(compile(model_src, str(Path(__file__)), "exec"), model_ns)
    return real_ns["run_real_profit_restrike"], model_ns["run_model_profit_restrike"], real_src + "\n\n" + model_src


def add_call(combined: pd.DataFrame, call_daily: pd.DataFrame) -> pd.DataFrame:
    return maturity.add_call(combined, call_daily)


def core_leg(scope, base, market, options_for_put, schedule, real_engine, model_engine, label, multiple, route_dates):
    kwargs = dict(
        reset_dates=common.engine.monthly_dates(base.date), profit_multiple=multiple,
        profit_delay_sessions=1, open_exit_dates=route_dates,
    )
    if scope == "real":
        put, trades, _ = real_engine(base, options_for_put, base, schedule, "3m", 1.02, label, market=None, **kwargs)
    else:
        put, trades, _ = model_engine(market, schedule, "3m", 1.02, label, **kwargs)
    put = common.scale_put(put, scope)
    put["put_cost_rate"] = put.put_cost_rate.astype(float) * PUT_COST_MULTIPLIER
    return put, trades


def run_layer(scope, router_fn, real_engine, model_engine, call_dir):
    if scope == "real":
        market, base, options, options_for_put, futures = maturity.load_layer(scope)
    else:
        market, base, options, futures = maturity_v2.extended_model_inputs()
        options = options.copy(); options["close"] = options["settle"]
        options_for_put = None

    bare = pd.DataFrame({"date": base.date, "return_net": base.baseline_plus_cash_ret.astype(float)})
    bare["nav"] = (1 + bare.return_net).cumprod(); bare["state"] = "imc"; bare["action"] = ""
    signal = maturity.prepare_signal(base, options, "m1")
    routed, route_events, cycles = router_fn(base, options, futures, signal, 0.35, common.FALLBACK, 0.60)
    routed["date"] = pd.to_datetime(routed.date)
    route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
    imc_mask = routed.set_index("date").state.eq("imc")
    always = pd.Series(True, index=pd.DatetimeIndex(base.date))

    call_scale_normal = pd.Series(1.0, index=base.index)
    call_scale_route = routed.state.eq("imc").astype(float)
    prefix = "r" if scope == "real" else "m"
    call_normal, call_normal_trades, _ = maturity.call_inputs(scope, base.date, call_scale_normal, prefix + "jn", call_dir)
    call_route, call_route_trades, _ = maturity.call_inputs(scope, base.date, call_scale_route, prefix + "jr", call_dir)

    results = []
    all_trades = []
    audit = {}
    configs = {
        "baseline": (bare, always, None, frozenset(), call_normal),
        "profit3x_only": (bare, always, 3.0, frozenset(), call_normal),
        "short95_only": (routed, imc_mask, None, route_dates, call_route),
        "joint": (routed, imc_mask, 3.0, route_dates, call_route),
    }
    for variant, (foundation, mask, multiple, exits, call_daily) in configs.items():
        schedule = valuation.corrected_core_schedule(base.date, scope, mask)
        label = f"{scope}_{variant}"
        put, trades = core_leg(scope, base, market, options_for_put, schedule, real_engine, model_engine, label, multiple, exits)
        combined = common.apply_core_put(foundation, put)
        combined = add_call(combined, call_daily)
        combined["candidate"] = label; combined["scope"] = scope; combined["variant"] = variant
        results.append(combined)
        all_trades.append(trades.assign(scope=scope, candidate=label))
        route_exits = trades[trades.action.eq("route_open_exit")]
        profit_trades = trades[trades.action.eq("close_profit_restrike")]
        duplicate_days = set(route_exits.actual_execution_date) & set(profit_trades.actual_execution_date)
        if duplicate_days:
            raise RuntimeError(f"Duplicate route/profit actions: {label} {duplicate_days}")
        if variant in ("short95_only", "joint") and not set(route_exits.actual_execution_date).issubset(route_dates):
            raise RuntimeError(f"Route exit mismatch: {label}")
        audit[variant] = {
            "route_switches": len(route_dates) if variant in ("short95_only", "joint") else 0,
            "short_put_cycles": len(cycles) if variant in ("short95_only", "joint") else 0,
            "profit_restrikes": int(len(profit_trades)),
            "core_put_route_exits": int(len(route_exits)),
            "route_profit_duplicate_days": int(len(duplicate_days)),
            "core_put_trade_events": int(len(trades)),
            "call_trade_events": int(len(call_route_trades if variant in ("short95_only", "joint") else call_normal_trades)),
        }
    return pd.concat(results, ignore_index=True), pd.concat(all_trades, ignore_index=True), signal, cycles, audit


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    router_fn, router_source = router_with_option_cost_5bp()
    real_engine, model_engine, engine_source = profit_route_engines()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    layers = {}
    daily_parts = []; trade_parts = []; signal_parts = []; cycle_parts = []
    for scope in ("real", "model"):
        daily, trades, signal, cycles, audit = run_layer(scope, router_fn, real_engine, model_engine, call_dir)
        layers[scope] = audit; daily_parts.append(daily); trade_parts.append(trades)
        signal_parts.append(signal.assign(scope=scope))
        if len(cycles): cycle_parts.append(cycles.assign(scope=scope))
    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True)
    summary, wide, unavailable = common.window_tables(daily)
    if "scope" not in summary.columns:
        summary["scope"] = summary.candidate.str.split("_", n=1).str[0]
    full = summary[summary.segment.eq("full")].copy()

    paired = []
    passed = True
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        rows = {v: block.loc[f"{scope}_{v}"] for v in ("baseline", "short95_only", "profit3x_only", "joint")}
        best_single = max(rows["short95_only"].ann_return, rows["profit3x_only"].ann_return)
        worst_single_dd = min(rows["short95_only"].max_dd, rows["profit3x_only"].max_dd)
        return_pass = rows["joint"].ann_return >= best_single - 1e-12
        dd_pass = rows["joint"].max_dd >= worst_single_dd - 0.01 - 1e-12
        passed = passed and return_pass and dd_pass
        paired.append({
            "scope": scope, "joint_ann_return": rows["joint"].ann_return,
            "best_single_ann_return": best_single,
            "joint_minus_best_single": rows["joint"].ann_return - best_single,
            "joint_max_dd": rows["joint"].max_dd, "worst_single_max_dd": worst_single_dd,
            "return_gate": return_pass, "drawdown_gate": dd_pass,
        })
    paired = pd.DataFrame(paired)
    conflict_pass = all(v["joint"]["route_profit_duplicate_days"] == 0 for v in layers.values())
    passed = passed and conflict_pass
    decision = "retain_joint_as_next_stage_research_candidate_no_production_change" if passed else "do_not_promote_joint_keep_best_single_no_production_change"
    stability = "cross_layer_joint_dominates_best_single" if passed else "joint_fails_cross_layer_dominance_or_conflict_gate"

    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "core_put_trades.csv", index=False)
    pd.concat(signal_parts, ignore_index=True).to_csv(out / "signal_audit.csv", index=False)
    (pd.concat(cycle_parts, ignore_index=True) if cycle_parts else pd.DataFrame()).to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False)
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + engine_source, encoding="utf-8")

    meta.update({
        "scan_type": "joint_im_short95_m1_iv35_decay60_and_core_put_profit3x",
        "baseline": {"candidate": ["real_baseline", "model_baseline"]},
        "candidate_grid": [{"variant": x} for x in ("baseline", "short95_only", "profit3x_only", "joint")],
        "data_snapshot": {
            "real_start": str(daily.loc[daily.scope.eq("real"), "date"].min().date()),
            "real_end": str(daily.loc[daily.scope.eq("real"), "date"].max().date()),
            "model_start": str(daily.loc[daily.scope.eq("model"), "date"].min().date()),
            "model_end": str(daily.loc[daily.scope.eq("model"), "date"].max().date()),
        },
        "cost_model": {
            "mo_put_one_way": OPTION_ONE_WAY_COST, "mo_put_round_trip": 2 * OPTION_ONE_WAY_COST,
            "futures_one_way": common.router.ONE_WAY_COST, "call": "existing validated component cost",
            "reserve": 0.30, "cash_annual": 0.03,
        },
        "audit": layers, "paired_gate": paired.to_dict("records"),
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "core_put_trades.csv"), "signals": str(out / "signal_audit.csv"), "cycles": str(out / "cycles.csv"), "paired": str(RUN / "paired_comparison.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "corrected_profit_engine": sha(EXECUTED_PROFIT)},
        "warnings": [
            "This is the exact fixed-core joint state-machine gate, not a production change.",
            "The model layer is theoretical/proxy and not executable listed history.",
            "Real route and profit event counts are small; event concentration controls interpretation.",
            "No dynamic margin, capacity, forced liquidation, tax, or integer account sizing replay.",
        ],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM高IV卖Put＋核心Put三倍兑现联合复测\n\n"
        "同一逐日状态机四组对照；MO Put单边5BP，高IV路由优先于同日盈利兑现。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Joint Gate\n\n" + paired.to_markdown(index=False) +
        "\n\n## Event Audit\n\n```json\n" + json.dumps(layers, ensure_ascii=False, indent=2) +
        "\n```\n\n## Decision\n\n" + decision + "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(paired.to_string(index=False)); print(json.dumps(layers, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
