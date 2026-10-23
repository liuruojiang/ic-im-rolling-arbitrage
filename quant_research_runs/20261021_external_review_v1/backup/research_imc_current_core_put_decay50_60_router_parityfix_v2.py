"""Adversarial rerun with exact naked-IMC parity before any IV route.

The prior router held a fixed number of futures units between rolls while its
baseline compounded a constant 1x daily futures return.  This version resets
the synthetic units to 1x equity at each session without charging a rebalance
fee, exactly matching the established baseline convention.  It then reruns
the same 50%/60% decay grid.  Research only.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_imc_current_core_put_decay50_60_router_fresh_v1 as pair
import research_imc_current_core_put_decay60_router_fresh_v1 as common

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_coreput_highiv_short95_decay50_60_adversarial_v3"


def parity_fixed_runner():
    _, source = common.decay_router.runner()
    needle = '''        previous_equity = equity
        pnl, cost, action, decision_reason = 0.0, 0.0, "", ""'''
    replacement = '''        previous_equity = equity
        # Match baseline_plus_cash_ret's constant-1x daily compounding.  This
        # synthetic resize is an accounting normalization, not a traded event.
        if i > 0 and state == "imc":
            position["units"] = previous_equity / (position["mark"] * 200)
        pnl, cost, action, decision_reason = 0.0, 0.0, "", ""'''
    if needle not in source:
        raise RuntimeError("Router equity hook changed")
    source = source.replace(needle, replacement)
    # Entry occurs at T+1 open, so sizing and notional costs may only use the
    # previous completed close, never the execution day's later close.
    source = source.replace(
        "float(b.csi1000_price_close)",
        "float(base.iloc[i - 1].csi1000_price_close)",
    )
    old_imc = '''        if state == "imc":
            q = future_lookup.loc[(position["contract"], day)]
            if i == 0:
                cost += position["units"] * 200 * float(q.settle) * ONE_WAY_COST
            elif str(b.roll_to) not in ("nan", "") and str(b.roll_to) != position["contract"]:
                pnl += position["units"] * 200 * (float(q.close) - position["mark"])
                nq = future_lookup.loc[(b.roll_to, day)]
                cost += position["units"] * 200 * (float(q.close) + float(nq.close)) * ONE_WAY_COST
                pnl += position["units"] * 200 * (float(nq.settle) - float(nq.close))
                position.update(contract=b.roll_to, mark=float(nq.settle))
                action = action or "imc_monthly_roll_close"
            elif not (i > 0 and action.startswith("imc_to_")):
                pnl += position["units"] * 200 * (float(q.settle) - position["mark"])
                position["mark"] = float(q.settle)'''
    new_imc = '''        if state == "imc":
            q = future_lookup.loc[(position["contract"], day)]
            if i == 0:
                cost += position["units"] * 200 * float(q.settle) * ONE_WAY_COST
            elif action == "":
                # Use the established baseline's exact daily gross return and
                # roll-cost series.  This is the required same-baseline path.
                # Preserve the baseline's own multiplicative cost convention
                # exactly; cash is added below by the router as the same 70%.
                pnl += previous_equity * (float(b.baseline_plus_cash_ret) - 0.7 * CASH_DAILY)
                if str(b.roll_to) not in ("nan", "") and str(b.roll_to) != position["contract"]:
                    nq = future_lookup.loc[(b.roll_to, day)]
                    position.update(contract=b.roll_to, mark=float(nq.settle))
                    action = "imc_monthly_roll_close"
                else:
                    position["mark"] = float(q.settle)'''
    if old_imc not in source:
        raise RuntimeError("Router IMC accounting block changed")
    source = source.replace(old_imc, new_imc)
    # Cash-settled assignment is known only at expiry settlement.  Buy IM at
    # the next session's open and block other cash-state entries meanwhile.
    source = source.replace(
        'if i > 0 and state == "cash" and action == "" and decision is not None:',
        'if i > 0 and state == "cash" and action == "" and decision is not None and pending != "assign":',
    )
    source = source.replace(
        '        if pending == "assign":\n',
        '        if pending == "assign" and day > pd.Timestamp(cycle["expiry_date"]):\n',
    )
    # The entry-day P&L is accumulated once at the end of the loop.  Starting
    # the cycle with pnl-cost would count that same amount twice for recovery.
    source = source.replace('"realized_pnl": pnl - cost', '"realized_pnl": 0.0')
    namespace = dict(vars(common.router))
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    raw_fn = namespace["run_router_decay"]

    def audited_fn(base, options, futures, signal, threshold, fallback, early_roll_threshold=None):
        daily, events, cycles = raw_fn(
            base, options, futures, signal, threshold, fallback, early_roll_threshold
        )
        executed = daily.action.eq("put_early_roll60_buyback_and_sell_next_open")
        daily["early_roll_executed"] = executed
        daily["early_roll_threshold"] = early_roll_threshold
        return daily, events, cycles

    return audited_fn, source


def no_route_parity(scope: str, fn) -> dict[str, float]:
    router = common.router
    if scope == "real":
        base = pd.read_csv(router.BASE, parse_dates=["date"])
        raw = pd.read_csv(router.OPTIONS, parse_dates=["date"])
        raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
        options = common.prepare_options(raw, common.actual_expiry_map(raw, base))
        futures = pd.read_csv(router.FUTURES, parse_dates=["date"])
    else:
        _, base, options, futures, _, _ = common.model_source.build_inputs()
        options = options.copy()
        options["close"] = options["settle"]
    signal = router.prepare_signal(base, options)
    got, _, _ = fn(base, options, futures, signal, 9.99, common.FALLBACK, None)
    daily_error = float(np.max(np.abs(got.return_net.to_numpy() - base.baseline_plus_cash_ret.to_numpy())))
    nav_error = float(np.max(np.abs(got.nav.to_numpy() - (1 + base.baseline_plus_cash_ret).cumprod().to_numpy())))
    if daily_error > 1e-12 or nav_error > 1e-12:
        raise RuntimeError(f"{scope} no-route parity failed: daily={daily_error}, nav={nav_error}")
    return {"max_daily_return_error": daily_error, "max_nav_error": nav_error}


def main():
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    router_fn, router_source = parity_fixed_runner()
    parity = {scope: no_route_parity(scope, router_fn) for scope in ("real", "model")}
    real_put_engine, model_put_engine, put_source = common.patched_put_engines()
    real_daily, real_trades, real_signal, real_audit = pair.run_layer(
        "real", router_fn, real_put_engine, model_put_engine
    )
    model_daily, model_trades, model_signal, model_audit = pair.run_layer(
        "model", router_fn, real_put_engine, model_put_engine
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
        scan_type="adversarial_parity_fixed_core_put_high_iv_router_decay50_60",
        baseline={"candidate": "*_bare_monthly_imc", "source": str(common.router.BASE)},
        candidate_grid=[
            {"iv_threshold": threshold, "fallback": common.FALLBACK, "short_put_decay": decay}
            for decay in pair.DECAYS for threshold in common.THRESHOLDS
        ],
        data_snapshot={
            "real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()),
            "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date()),
            "base_sha256": common.sha(common.router.BASE), "options_sha256": common.sha(common.router.OPTIONS),
            "futures_sha256": common.sha(common.router.FUTURES),
        },
        cost_model={
            "one_way_notional": common.router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03,
            "imc_accounting": "constant 1x daily compounding; exact no-route parity required",
            "route_switch": "same T+1 open: close IM, close core Put, sell short95 Put",
            "short_put_early_roll": "50% or 60% premium decay; next-open cover/re-sell; one roll; re-admission required",
        },
        audit={"no_route_parity": parity, "real": real_audit, "model": model_audit},
        unavailable_segments=unavailable,
        outputs={
            **meta["outputs"], "daily": str(out / "daily.csv.gz"),
            "core_put_trades": str(out / "core_put_trades.csv"),
            "real_signal_audit": str(out / "real_signal_audit.csv"),
            "model_signal_audit": str(out / "model_signal_audit.csv"),
            "executed_state_machines": str(RUN / "executed_state_machines.py"),
        },
        source_hashes={
            "script": common.sha(Path(__file__)), "pair_harness": common.sha(Path(pair.__file__)),
            "common_fresh_engine": common.sha(Path(common.__file__)),
            "router": common.sha(ROOT / "research_imc_high_iv_short95_router_v1.py"),
            "decay_router": common.sha(ROOT / "research_imc_high_iv_short95_router_decay60_v1.py"),
        },
        warnings=[
            "Historical 102% and MOM120 debounce replay is counterfactual; live effective dates are not rewritten.",
            "Real route sample has only three IV35 cycles and no assignment/recovery event.",
            "Model layer is theoretical/proxy, uses calibrated carry, and is not executable history.",
            "No bid-ask, impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing.",
        ],
        decision="research_only_pending_adversarial_review",
        stability_label="parity_fixed_pending_review",
        git_status_after=common.git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    record = (
        "# 高IV卖Put路由对抗复算：IMC基线parity修正版\n\n"
        "旧路由在首次高IV事件前未复现恒定1倍IMC基线，本版按同一收益会计修正，并强制无路由逐日/NAV误差不超过1e-12。\n\n"
        "## Data\n\n真实挂牌与理论延展严格分层；路径、日期和哈希见 `scan_meta.json`。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) + "\n\n"
        "## Audit\n\n```json\n" + json.dumps(meta["audit"], ensure_ascii=False, indent=2) + "\n```\n\n"
        "## Stability\n\n待完成多智能体对抗审计。\n\n"
        "## Decision\n\nresearch_only_pending_adversarial_review\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(json.dumps(meta["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
