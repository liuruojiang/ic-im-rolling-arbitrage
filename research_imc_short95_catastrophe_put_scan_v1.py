"""Scan same-expiry long catastrophe Puts on the corrected M+1 short-95% IM router."""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_csi1000_put_protection_battery_v6 as mkt
import research_imc_short95_maturity_corrected_v1 as maturity
import research_imc_short95_maturity_corrected_v2 as maturity_v2
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as parity
import research_imc_current_core_put_short95_earlyvaluation_v4 as v4

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "docs" / "im_short95_catastrophe_put_scan_v1_spec.md"
RATIOS: tuple[float | None, ...] = (None, 0.90, 0.85, 0.80)
IV_THRESHOLD = 0.35
DECAY = 0.60

JOINT = ROOT / "quant_param_scan_runs" / "20260908_im_full_combination_joint_iv_derisk_v1"
import sys
sys.path.insert(0, str(JOINT))
import joint_components as joint_comp  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def protected_router() -> tuple[Any, str]:
    """Add a synchronous long Put leg to the parity-fixed decay router."""
    _, source = parity.parity_fixed_runner()
    source = source.replace(
        "early_roll_threshold: float | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:",
        "early_roll_threshold: float | None = None, catastrophe_ratio: float | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:",
    )
    source = source.replace(
        'candidate = route_label(threshold, fallback) + ("" if early_roll_threshold is None else f"__decay{int(early_roll_threshold * 100)}")',
        'candidate = route_label(threshold, fallback) + ("" if early_roll_threshold is None else f"__decay{int(early_roll_threshold * 100)}") + ("__tailnone" if catastrophe_ratio is None else f"__tail{int(catastrophe_ratio * 100)}")',
    )
    helper_anchor = '    decisions = signal.set_index("execution_date")\n'
    helper = '''    decisions = signal.set_index("execution_date")
    def select_tail(day: pd.Timestamp, month: pd.Timestamp, prior_spot: float, short_strike: float):
        if catastrophe_ratio is None:
            return None
        chain = options_by_day.get(day, options.iloc[:0]).copy()
        chain = chain[(chain.contract_month == month) & (chain.strike < short_strike)].copy()
        if chain.empty:
            raise RuntimeError(f"No listed catastrophe Put on {day.date()} ratio={catastrophe_ratio}")
        chain["distance"] = (chain.strike - prior_spot * catastrophe_ratio).abs()
        selected = chain.sort_values(["distance", "strike", "contract"]).iloc[0]
        if not bool(selected.open > 0 and selected.volume > 0 and selected.open_interest > 0):
            raise RuntimeError(f"Unexecutable catastrophe Put on {day.date()} ratio={catastrophe_ratio}")
        return selected
'''
    if helper_anchor not in source:
        raise RuntimeError("Router decisions anchor changed")
    source = source.replace(helper_anchor, helper, 1)

    entry_anchor = '                pnl += units * 200 * (float(oq.open) - float(oq.settle))\n                cost += units * 200 * float(base.iloc[i - 1].csi1000_price_close) * ONE_WAY_COST\n                cycle = {"entry_date": str(day.date()), "put_contract": str(decision.execution_contract), "units": units, "realized_pnl": 0.0, "closed": False}\n'
    entry_replacement = '''                pnl += units * 200 * (float(oq.open) - float(oq.settle))
                cost += units * 200 * float(base.iloc[i - 1].csi1000_price_close) * ONE_WAY_COST
                tail = select_tail(day, pd.Timestamp(oq.contract_month), float(base.iloc[i - 1].csi1000_price_close), float(oq.strike))
                tail_contract = ""
                if tail is not None:
                    tail_contract = str(tail.contract)
                    pnl += units * 200 * (float(tail.settle) - float(tail.open))
                    cost += units * 200 * float(base.iloc[i - 1].csi1000_price_close) * ONE_WAY_COST
                    position.update(tail_contract=tail_contract, tail_mark=float(tail.settle), tail_entry_premium=float(tail.open))
                cycle = {"entry_date": str(day.date()), "put_contract": str(decision.execution_contract), "tail_contract": tail_contract, "catastrophe_ratio": catastrophe_ratio, "units": units, "realized_pnl": 0.0, "closed": False}
'''
    if source.count(entry_anchor) != 2:
        raise RuntimeError(f"Expected two entry anchors, found {source.count(entry_anchor)}")
    source = source.replace(entry_anchor, entry_replacement)

    mark_anchor = '''            pnl += position["units"] * 200 * (position["mark"] - float(q.settle))
            position["mark"] = float(q.settle)
            if pending_roll:
'''
    mark_replacement = '''            pnl += position["units"] * 200 * (position["mark"] - float(q.settle))
            position["mark"] = float(q.settle)
            hq = None
            if catastrophe_ratio is not None:
                hq = option_lookup.loc[(position["tail_contract"], day)]
                pnl += position["units"] * 200 * (float(hq.settle) - position["tail_mark"])
                position["tail_mark"] = float(hq.settle)
            if pending_roll:
'''
    if mark_anchor not in source:
        raise RuntimeError("Put mark anchor changed")
    source = source.replace(mark_anchor, mark_replacement, 1)

    valid_anchor = '''                    valid = bool(selected.open > 0 and selected.volume > 0 and selected.open_interest > 0)
                    if valid:
'''
    valid_replacement = '''                    valid = bool(selected.open > 0 and selected.volume > 0 and selected.open_interest > 0)
                    next_tail = None
                    if valid and catastrophe_ratio is not None:
                        try:
                            next_tail = select_tail(day, target_month, prior_spot, float(selected.strike))
                        except RuntimeError:
                            valid = False
                    if valid:
'''
    if valid_anchor not in source:
        raise RuntimeError("Roll validity anchor changed")
    source = source.replace(valid_anchor, valid_replacement, 1)

    update_anchor = '''                        cost += position["units"] * 200 * prior_spot * (2 * ONE_WAY_COST)
                        position.update(contract=str(selected.contract), mark=float(selected.settle), expiry=pd.Timestamp(selected.actual_expiry), contract_month=pd.Timestamp(selected.contract_month), entry_premium=float(selected.open), rolled=True)
'''
    update_replacement = '''                        cost += position["units"] * 200 * prior_spot * (2 * ONE_WAY_COST)
                        if catastrophe_ratio is not None:
                            pnl += position["units"] * 200 * (float(hq.open) - float(hq.settle))
                            pnl += position["units"] * 200 * (float(next_tail.settle) - float(next_tail.open))
                            cost += position["units"] * 200 * prior_spot * (2 * ONE_WAY_COST)
                            position.update(tail_contract=str(next_tail.contract), tail_mark=float(next_tail.settle), tail_entry_premium=float(next_tail.open))
                            cycle["tail_contract"] = str(next_tail.contract)
                        position.update(contract=str(selected.contract), mark=float(selected.settle), expiry=pd.Timestamp(selected.actual_expiry), contract_month=pd.Timestamp(selected.contract_month), entry_premium=float(selected.open), rolled=True)
'''
    if update_anchor not in source:
        raise RuntimeError("Roll update anchor changed")
    source = source.replace(update_anchor, update_replacement, 1)

    item_anchor = '"decision_reason": decision_reason, "pending_roll": pending_roll}'
    item_replacement = '"decision_reason": decision_reason, "pending_roll": pending_roll, "catastrophe_ratio": catastrophe_ratio, "tail_contract": (position.get("tail_contract", "") if state == "put" else "")}'
    if item_anchor not in source:
        raise RuntimeError("Daily item anchor changed")
    source = source.replace(item_anchor, item_replacement, 1)
    ns = dict(vars(maturity.common.router))
    exec(compile(source, str(Path(__file__)), "exec"), ns)
    return ns["run_router_decay"], source


def extended_model_inputs():
    market, base, _, futures, _, _ = maturity.common.model_source.build_inputs()
    dates = pd.DatetimeIndex(market.date)
    months = pd.date_range(market.date.min().to_period("M").to_timestamp(), market.date.max() + pd.DateOffset(months=3), freq="MS")
    expiries = {month: mkt.third_friday(month, dates) for month in months}
    keys: set[tuple[pd.Timestamp, int]] = set()
    for i, row in enumerate(market.itertuples(index=False)):
        if i == 0:
            continue
        spot = float(market.iloc[i - 1].spot_close)
        step = 25 if spot <= 2500 else 50 if spot <= 5000 else 100 if spot <= 10000 else 200
        for ahead in (1, 2):
            month = row.date.to_period("M").to_timestamp() + pd.offsets.MonthBegin(ahead)
            for ratio in (0.95, 0.90, 0.85, 0.80):
                keys.add((month, math.floor(spot * ratio / step + 0.5) * step))
    rows: list[dict[str, Any]] = []
    for month, strike in sorted(keys):
        if month not in expiries:
            continue
        expiry = expiries[month]
        sample = market[(market.date >= month - pd.DateOffset(months=3)) & (market.date <= expiry)]
        for row in sample.itertuples(index=False):
            years = max((expiry - row.date).days / 365, 0)
            rows.append({
                "date": row.date, "contract": "MO" + month.strftime("%y%m") + "-P-" + str(strike),
                "contract_month": month, "actual_expiry": expiry, "strike": strike,
                "open": mkt.proxy.bs_put(row.spot_open, strike, row.rate_open, row.dividend_open, row.sigma_open, years),
                "settle": mkt.proxy.bs_put(row.spot_close, strike, row.rate_close, row.dividend_close, row.sigma_close, years),
                "volume": 1, "open_interest": 1,
            })
    options = pd.DataFrame(rows).drop_duplicates(["contract", "date"]).sort_values(["date", "contract"])
    options["close"] = options["settle"]
    return market, base, options, futures


def load_layer(scope: str):
    if scope == "real":
        return maturity.load_layer(scope)
    market, base, options, futures = extended_model_inputs()
    return market, base, options, None, futures


def add_tail_execution_gate(signal: pd.DataFrame, options: pd.DataFrame, ratio: float | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = signal.copy()
    audit_rows = []
    if ratio is None:
        out["tail_execution_valid"] = True
        out["tail_contract"] = ""
        return out, pd.DataFrame(columns=["execution_date", "catastrophe_ratio", "tail_contract", "tail_execution_valid"])
    by_day = {day: part for day, part in options.groupby("date", sort=False)}
    for idx, row in out.iterrows():
        valid, contract, strike = False, "", np.nan
        if bool(row.execution_open_valid) and str(row.execution_contract):
            day = pd.Timestamp(row.execution_date)
            short = options[(options.date == day) & (options.contract == str(row.execution_contract))]
            if len(short) == 1:
                sq = short.iloc[0]
                prior = options[(options.date == pd.Timestamp(row.eval_date)) & (options.contract == str(row.iv_contract))]
                if len(prior) == 1 and float(row.signal_moneyness) > 0:
                    prior_spot = float(prior.iloc[0].strike) / float(row.signal_moneyness)
                    chain = by_day.get(day, options.iloc[:0]).copy()
                    chain = chain[(chain.contract_month == pd.Timestamp(sq.contract_month)) & (chain.strike < float(sq.strike))].copy()
                    if not chain.empty:
                        chain["distance"] = (chain.strike - prior_spot * ratio).abs()
                        tq = chain.sort_values(["distance", "strike", "contract"]).iloc[0]
                        valid = bool(tq.open > 0 and tq.volume > 0 and tq.open_interest > 0)
                        contract, strike = str(tq.contract), float(tq.strike)
        out.loc[idx, "tail_execution_valid"] = valid
        out.loc[idx, "tail_contract"] = contract
        if bool(row.execution_open_valid) and not valid:
            out.loc[idx, "execution_open_valid"] = False
        audit_rows.append({"eval_date": row.eval_date, "execution_date": row.execution_date, "catastrophe_ratio": ratio, "short_contract": row.execution_contract, "tail_contract": contract, "tail_strike": strike, "tail_execution_valid": valid})
    return out, pd.DataFrame(audit_rows)


def run_layer(scope: str, router_fn, real_put_engine, model_put_engine, call_dir: Path):
    market, base, options, options_for_put, futures = load_layer(scope)
    bare = pd.DataFrame({"date": base.date, "candidate": f"{scope}_bare_monthly_imc", "return_net": base.baseline_plus_cash_ret.astype(float)})
    bare["nav"] = (1 + bare.return_net).cumprod(); bare["state"] = "imc"; bare["action"] = ""
    always = pd.Series(True, index=pd.DatetimeIndex(base.date))
    schedule = v4.corrected_core_schedule(base.date, scope, always)
    if scope == "real":
        put, base_put_trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=maturity.common.engine.monthly_dates(base.date), market=None)
    else:
        put, base_put_trades, _ = model_put_engine(market, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=maturity.common.engine.monthly_dates(base.date))
    protected = maturity.common.apply_core_put(bare, maturity.common.scale_put(put, scope))
    call_base, call_base_trades, _ = maturity.call_inputs(scope, base.date, pd.Series(1.0, index=base.index), f"{scope[0]}cb", call_dir)
    protected = maturity.add_call(protected, call_base); protected["candidate"] = f"{scope}_imc_coreput_call_baseline"

    candidates = [protected]; signals = []; cycles_all = []; tail_audits = []; audits: dict[str, Any] = {}
    base_signal = maturity.prepare_signal(base, options, "m1")
    for ratio in RATIOS:
        signal, tail_audit = add_tail_execution_gate(base_signal, options, ratio)
        routed, events, cycles = router_fn(base, options, futures, signal, IV_THRESHOLD, maturity.common.FALLBACK, DECAY, ratio)
        routed["date"] = pd.to_datetime(routed.date)
        imc_mask = routed.set_index("date").state.eq("imc")
        route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
        schedule = v4.corrected_core_schedule(base.date, scope, imc_mask)
        suffix = "none" if ratio is None else str(int(ratio * 100))
        label = f"{scope}_m1_iv35_short95_decay60_tail{suffix}_callpaused"
        if scope == "real":
            put, put_trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, label, reset_dates=maturity.common.engine.monthly_dates(base.date), market=None, open_exit_dates=route_dates)
        else:
            put, put_trades, _ = model_put_engine(market, schedule, "3m", 1.02, label, reset_dates=maturity.common.engine.monthly_dates(base.date), open_exit_dates=route_dates)
        combined = maturity.common.apply_core_put(routed, maturity.common.scale_put(put, scope))
        call_scale = routed.state.eq("imc").astype(float)
        call_daily, call_trades, _ = maturity.call_inputs(scope, base.date, call_scale, f"{scope[0]}c{suffix}", call_dir)
        combined = maturity.add_call(combined, call_daily); combined["candidate"] = label
        candidates.append(combined)
        signals.append(signal.assign(layer=scope, catastrophe_ratio="none" if ratio is None else ratio))
        if len(tail_audit): tail_audits.append(tail_audit.assign(layer=scope))
        if len(cycles): cycles_all.append(cycles.assign(layer=scope, candidate=label))
        simultaneous = set(put_trades.loc[put_trades.action.eq("route_open_exit"), "actual_execution_date"])
        if not simultaneous.issubset(route_dates):
            raise RuntimeError(f"{label}: core Put exit mismatch")
        if not call_daily.loc[routed.route.eq("high_iv_permitted_short_put"), "call_coverage"].eq(0).all():
            raise RuntimeError(f"{label}: Call not paused")
        audits[label] = {
            "route_switches": len(route_dates), "short_put_cycles": len(cycles),
            "tail_gate_rejections": int((tail_audit.tail_execution_valid.eq(False)).sum()) if len(tail_audit) else 0,
            "assignments": int(cycles.assignment_date.fillna("").astype(str).ne("").sum()) if len(cycles) and "assignment_date" in cycles else 0,
            "early_rolls": int(routed.action.eq("put_early_roll60_buyback_and_sell_next_open").sum()),
            "core_put_simultaneous_exits": len(simultaneous), "call_paused_days": int(call_scale.eq(0).sum()),
            "call_trade_events": len(call_trades),
        }
    audits[f"{scope}_baseline"] = {"core_put_trade_events": len(base_put_trades), "call_trade_events": len(call_base_trades)}
    return pd.concat(candidates, ignore_index=True), pd.concat(signals, ignore_index=True), (pd.concat(cycles_all, ignore_index=True) if cycles_all else pd.DataFrame()), (pd.concat(tail_audits, ignore_index=True) if tail_audits else pd.DataFrame()), audits


def main() -> None:
    run = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_corrected_mixed_router_m_1_short95_catastrophe_protection_catastrophe_put_ratio"
    meta_path = run / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    router_fn, executed = protected_router()
    real_put_engine, model_put_engine, put_source = maturity.common.patched_put_engines()
    out = run / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    real_daily, real_signal, real_cycles, real_tail, real_audit = run_layer("real", router_fn, real_put_engine, model_put_engine, call_dir)
    model_daily, model_signal, model_cycles, model_tail, model_audit = run_layer("model", router_fn, real_put_engine, model_put_engine, call_dir)
    daily = pd.concat([real_daily, model_daily], ignore_index=True)
    summary, wide, unavailable = maturity.common.window_tables(daily)
    cycles = pd.concat([real_cycles, model_cycles], ignore_index=True)
    cycle_diag = cycles.groupby(["layer", "candidate"], as_index=False).agg(cycles=("entry_date", "count"), worst_cycle_pnl=("exit_cycle_pnl", "min"), mean_cycle_pnl=("exit_cycle_pnl", "mean"))
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([real_signal, model_signal], ignore_index=True).to_csv(out / "signal_audit.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    pd.concat([real_tail, model_tail], ignore_index=True).to_csv(out / "tail_selection_audit.csv", index=False)
    cycle_diag.to_csv(out / "cycle_diagnostics.csv", index=False)
    summary.to_csv(run / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(run / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (run / "executed_state_machines.py").write_text(executed + "\n\n" + put_source, encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    meta.update(
        scan_type="im_m1_short95_same_expiry_catastrophe_put_scan_v1",
        baseline={"candidate": "*_m1_iv35_short95_decay60_tailnone_callpaused", "portfolio_baseline": "*_imc_coreput_call_baseline"},
        candidate_grid=[{"catastrophe_ratio": x} for x in ("none", 0.90, 0.85, 0.80)],
        data_snapshot={"real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()), "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date()), "options_sha256": sha(maturity.common.router.OPTIONS), "futures_sha256": sha(maturity.common.router.FUTURES)},
        cost_model={"one_way_notional": maturity.common.router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03, "tail_put": "same expiry and units; each open/close charged one-way notional cost"},
        audit={"real": real_audit, "model": model_audit}, unavailable_segments=unavailable,
        outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signal_audit.csv"), "cycles": str(out / "cycles.csv"), "tail_selection": str(out / "tail_selection_audit.csv"), "cycle_diagnostics": str(out / "cycle_diagnostics.csv"), "executed_state_machines": str(run / "executed_state_machines.py")},
        source_hashes={"script": sha(Path(__file__)), "spec": sha(SPEC), "parity_engine": sha(Path(parity.__file__)), "early_valuation": sha(v4.EARLY), "call_component": sha(Path(joint_comp.__file__))},
        warnings=["Historical 102% Put and MOM120 debounce are counterfactual replay.", "Model options are Black-Scholes proxies, not executable history.", "Real routed cycles are few and may not overlap a crash.", "No bid-ask, impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing."],
        decision="research_only_pending_interpretation", stability_label="catastrophe_put_scan_pending_review", git_status_after=git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IM M+1卖95% Put保留灾难保护扫描\n\n## Run Metadata\n\n- 研究层：真实IM/MO与理论延展分开；不改生产。\n\n## Research Question\n\n- 同到期、等数量的深度虚值多头Put能否改善卖Put尾部风险。\n\n## Implementation Anchor\n\n- 固定M+1、IV>35%、60%衰减滚动、当前估值/MOM120防抖和Call暂停。\n\n## Data Snapshot\n\n- 真实与理论区间见 `scan_meta.json`。\n\n## Cost and Execution Assumptions\n\n- T收盘信号、T+1开盘；每条期权腿单边1bp标的名义成本。\n\n## Runtime Override Plan\n\n- 独立研究脚本；未改冻结主线。\n\n## Commands\n\n```powershell\npython -X utf8 research_imc_short95_catastrophe_put_scan_v1.py\n```\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`及`daily_outputs/`。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) + "\n\n## Cycle Diagnostics\n\n" + cycle_diag.to_markdown(index=False) + "\n\n## Stability Classification\n\n- 待按预注册门槛解释。\n\n## Decision\n\n- research_only_pending_interpretation\n"
    (run / "record.md").write_text(record, encoding="utf-8")
    with (run / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(cycle_diag.to_string(index=False)); print(json.dumps({"real": real_audit, "model": model_audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
