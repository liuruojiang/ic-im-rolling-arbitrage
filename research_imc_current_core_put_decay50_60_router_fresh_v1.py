"""Fresh matched replay of 50% versus 60% short-Put premium decay.

Research only.  Both decay settings are rebuilt in one process from the same
raw futures/options inputs, current counterfactual core-Put rules, costs, and
execution timing.  Prior performance output files are never read.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_imc_current_core_put_decay60_router_fresh_v1 as common

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_current_core_put_high_iv_short95_decay50_60_fresh_v2"
DECAYS = (0.50, 0.60)


def run_layer(scope, run_router_decay, real_put_engine, model_put_engine):
    router = common.router
    engine = common.engine
    if scope == "real":
        base = pd.read_csv(router.BASE, parse_dates=["date"])
        raw = pd.read_csv(router.OPTIONS, parse_dates=["date"])
        raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
        options = common.prepare_options(raw, common.actual_expiry_map(raw, base))
        options_for_put = engine.with_execution_prices(options.copy())
        futures = pd.read_csv(router.FUTURES, parse_dates=["date"])
        signal = router.prepare_signal(base, options)
        baseline_return = base.baseline_plus_cash_ret.astype(float)
        baseline_nav_reference = base.nav_baseline_plus_cash.astype(float)
    else:
        market, base, options, futures, _, _ = common.model_source.build_inputs()
        options = options.copy()
        options["close"] = options["settle"]
        options_for_put = None
        signal = router.prepare_signal(base, options)
        baseline_return = base.baseline_plus_cash_ret.astype(float)
        baseline_nav_reference = (1 + baseline_return).cumprod()

    parity = float(np.max(np.abs((1 + baseline_return).cumprod().to_numpy() - baseline_nav_reference.to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"{scope} bare baseline parity failed: {parity}")

    bare = pd.DataFrame({"date": base.date, "candidate": f"{scope}_bare_monthly_imc", "return_net": baseline_return})
    bare["nav"] = (1 + bare.return_net).cumprod()
    bare["state"] = "imc"
    bare["action"] = ""

    always_imc = pd.Series(True, index=pd.DatetimeIndex(base.date))
    schedule = common.core_schedule(base.date, scope, always_imc)
    if scope == "real":
        put, put_trades, _ = real_put_engine(
            base, options_for_put, base, schedule, "3m", 1.02, f"{scope}_core_put",
            reset_dates=engine.monthly_dates(base.date), market=None,
        )
    else:
        put, put_trades, _ = model_put_engine(
            market, schedule, "3m", 1.02, f"{scope}_core_put",
            reset_dates=engine.monthly_dates(base.date),
        )
    put = common.scale_put(put, scope)
    protected = common.apply_core_put(bare, put)
    protected["candidate"] = f"{scope}_monthly_imc_current_core_put102"

    candidates = [bare, protected]
    trade_parts = [put_trades.assign(candidate=f"{scope}_monthly_imc_current_core_put102")]
    audits = {"bare_nav_parity": parity, "core_put_trade_events": len(put_trades)}

    for decay in DECAYS:
        decay_tag = int(round(decay * 100))
        for threshold in common.THRESHOLDS:
            routed, short_events, short_cycles = run_router_decay(
                base, options, futures, signal, threshold, common.FALLBACK, decay
            )
            routed["date"] = pd.to_datetime(routed.date)
            imc_mask = routed.set_index("date").state.eq("imc")
            route_exit_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
            schedule = common.core_schedule(base.date, scope, imc_mask)
            label = f"{scope}_iv{int(threshold * 1000):03d}_core_put_to_short95_decay{decay_tag}"
            if scope == "real":
                put, trades, _ = real_put_engine(
                    base, options_for_put, base, schedule, "3m", 1.02, label,
                    reset_dates=engine.monthly_dates(base.date), market=None,
                    open_exit_dates=route_exit_dates,
                )
            else:
                put, trades, _ = model_put_engine(
                    market, schedule, "3m", 1.02, label,
                    reset_dates=engine.monthly_dates(base.date), open_exit_dates=route_exit_dates,
                )
            put = common.scale_put(put, scope)
            combined = common.apply_core_put(routed, put)
            combined["candidate"] = label
            candidates.append(combined)
            trade_parts.append(trades.assign(candidate=label))
            simultaneous = set(trades.loc[trades.action.eq("route_open_exit"), "actual_execution_date"])
            if not simultaneous.issubset(route_exit_dates):
                raise RuntimeError(f"{label} core Put exited outside route switches: {simultaneous - route_exit_dates}")
            actions = routed.action.astype(str)
            early_rolls = int(actions.eq("put_early_roll60_buyback_and_sell_next_open").sum())
            audits[label] = {
                "route_switches": len(route_exit_dates),
                "simultaneous_core_put_open_exits": len(simultaneous),
                "route_switches_without_active_core_put": len(route_exit_dates - simultaneous),
                "short_put_early_rolls": early_rolls,
                "early_roll_signals": int(actions.eq(f"put_premium_decay_{decay_tag}_signal_close").sum()),
                "early_roll_blocked_re_admission": int(actions.eq("put_early_roll_blocked_re_admission").sum()),
                "early_roll_blocked_untradable": int(actions.eq("put_early_roll_blocked_untradable_new_leg").sum()),
                "short_put_events": len(short_events),
                "short_put_cycles": len(short_cycles),
            }
    return pd.concat(candidates, ignore_index=True), pd.concat(trade_parts, ignore_index=True), signal, audits


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    run_router_decay, router_source = common.decay_router.runner()
    real_put_engine, model_put_engine, put_source = common.patched_put_engines()
    real_daily, real_trades, real_signal, real_audit = run_layer(
        "real", run_router_decay, real_put_engine, model_put_engine
    )
    model_daily, model_trades, model_signal, model_audit = run_layer(
        "model", run_router_decay, real_put_engine, model_put_engine
    )
    daily = pd.concat([real_daily, model_daily], ignore_index=True)
    summary, wide, unavailable = common.window_tables(daily)

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([real_trades, model_trades], ignore_index=True).to_csv(out / "core_put_trades.csv", index=False)
    real_signal.assign(layer="real").to_csv(out / "real_signal_audit.csv", index=False)
    model_signal.assign(layer="model").to_csv(out / "model_signal_audit.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + put_source, encoding="utf-8")

    meta.update(
        scan_type="fresh_matched_core_put_high_iv_router_decay50_60",
        baseline={"candidate": "*_bare_monthly_imc", "source": str(common.router.BASE)},
        candidate_grid=[
            {"iv_threshold": threshold, "fallback": common.FALLBACK, "short_put_decay": decay}
            for decay in DECAYS for threshold in common.THRESHOLDS
        ],
        data_snapshot={
            "real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()),
            "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date()),
            "base_sha256": common.sha(common.router.BASE),
            "options_sha256": common.sha(common.router.OPTIONS),
            "futures_sha256": common.sha(common.router.FUTURES),
        },
        cost_model={
            "one_way_notional": common.router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03,
            "core_put_target": "full-1x scaled current core valuation/MOM120 debounce; 102%; about 3m; monthly close maintenance",
            "route_switch": "same T+1 open: close IM, close core Put, sell short95 Put",
            "short_put_early_roll": "50% or 60% premium decay at close; next open cover old and sell immediately-next month; one roll; re-admission required",
        },
        audit={"real": real_audit, "model": model_audit},
        unavailable_segments=unavailable,
        outputs={
            **meta["outputs"], "daily": str(out / "daily.csv.gz"),
            "core_put_trades": str(out / "core_put_trades.csv"),
            "real_signal_audit": str(out / "real_signal_audit.csv"),
            "model_signal_audit": str(out / "model_signal_audit.csv"),
            "executed_state_machines": str(RUN / "executed_state_machines.py"),
        },
        source_hashes={
            "script": common.sha(Path(__file__)),
            "common_fresh_engine": common.sha(ROOT / "research_imc_current_core_put_decay60_router_fresh_v1.py"),
            "router": common.sha(ROOT / "research_imc_high_iv_short95_router_v1.py"),
            "decay_router": common.sha(ROOT / "research_imc_high_iv_short95_router_decay60_v1.py"),
        },
        warnings=[
            "Historical 102% and MOM120 debounce replay is counterfactual; live effective dates are not rewritten.",
            "Model layer is theoretical/proxy and not executable history.",
            "No bid-ask, impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing.",
        ],
        decision="research_only_pending_interpretation",
        stability_label="fresh_matched_replay_pending_review",
        git_status_after=common.git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    record = (
        "# 月度IMC＋当前核心Put＋高IV卖Put：50% vs 60%统一重算\n\n"
        "本轮从同一原始月度IM/MO数据与统一模型重新计算；不读取旧绩效CSV作为结果输入。"
        "历史102%与MOM120防抖属于反事实回放。\n\n"
        "## Data\n\n真实挂牌与理论延展分层输出；完整范围和哈希见 `scan_meta.json`。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) + "\n\n"
        "## Audit\n\n```json\n" + json.dumps(meta["audit"], ensure_ascii=False, indent=2) + "\n```\n\n"
        "## Stability\n\n待完成同窗比较后定级。\n\n"
        "## Decision\n\nresearch_only_pending_interpretation\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(json.dumps(meta["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
