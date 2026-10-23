"""Scan same-expiry catastrophe Puts on the corrected IC M+1 short-95% router."""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_coreput_highiv_short95_router_v1 as base
import research_ic_short95_maturity_scan_v1 as maturity


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_ic_v1_3_m1_short95_catastrophe_protection_put_ratio"
SPEC = ROOT / "docs" / "ic_short95_catastrophe_put_scan_v1_spec.md"
MATURITY_RUN = maturity.RUN
RATIOS: tuple[float | None, ...] = (None, 0.90, 0.85, 0.80)
IV_THRESHOLD = 0.375
DECAY = 0.60
OPTION_ONE_WAY = 0.0005


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def choose_tail_real(chain: pd.DataFrame, prior_spot: float, month: pd.Timestamp,
                     short_strike: float, ratio: float | None):
    if ratio is None:
        return None
    eligible = chain[(chain.contract_month.eq(month)) & (chain.strike < short_strike)].copy()
    if eligible.empty:
        return None
    eligible["distance"] = (eligible.strike - prior_spot * ratio).abs()
    return eligible.sort_values(["distance", "strike", "security_id"]).iloc[0]


def protected_short_runners():
    """Patch the audited IC state machines without changing assignment/recovery semantics."""
    short = base.short
    real_src = inspect.getsource(short.run_real)
    real_src = real_src.replace(
        "def run_real(admission: pd.Series, threshold: float | None):",
        "def run_real_tail(admission: pd.Series, threshold: float | None, maturity_name: str = 'm1', option_one_way: float = ONE_WAY, catastrophe_ratio: float | None = None):",
        1,
    )
    real_src = real_src.replace(
        'label = "real_hold_to_expiry" if threshold is None else f"real_decay_{int(threshold * 100)}"',
        'label = f"real_{maturity_name}_decay_{int(threshold * 100)}_tail{\'none\' if catastrophe_ratio is None else int(catastrophe_ratio * 100)}"',
        1,
    )
    real_src = real_src.replace(
        "rows, events, cycles, skips = [], [], [], 0",
        "rows, events, cycles, skips = [], [], [], 0\n    tail_skips = 0\n    tail_stale_marks = 0",
        1,
    )
    old_entry = 'prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"]); month = day.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1); selected = choose_real(chains.get(day, pd.DataFrame()), prior_spot, month)'
    new_entry = 'prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"]); chain_today = chains.get(day, pd.DataFrame()); month = maturity.target_month(day, maturity_name); selected = choose_real(chain_today, prior_spot, month); tail_selected = choose_tail_real(chain_today, prior_spot, month, float(selected.strike), catastrophe_ratio) if selected is not None else None'
    if old_entry not in real_src:
        raise RuntimeError("Real entry anchor changed")
    real_src = real_src.replace(old_entry, new_entry, 1)
    real_src = real_src.replace(
        'if selected is None: skips += 1; action = "skip_missing_next_month_chain"\n            else:',
        'if selected is None: skips += 1; action = "skip_missing_next_month_chain"\n            elif catastrophe_ratio is not None and tail_selected is None: skips += 1; tail_skips += 1; action = "skip_missing_catastrophe_put"\n            else:',
        1,
    )
    real_src = real_src.replace(
        'q = options.loc[(selected.security_id, day)]\n                if q.open > 0 and q.volume > 0:',
        'q = options.loc[(selected.security_id, day), :]; tail_key = (str(tail_selected.security_id), day) if catastrophe_ratio is not None else None; tq = options.loc[tail_key, :] if tail_key is not None and tail_key in options.index else None\n                if q.open > 0 and q.volume > 0 and (catastrophe_ratio is None or (tq is not None and tq.open > 0 and tq.volume > 0)):',
        1,
    )
    real_src = real_src.replace(
        '"entry_premium": float(q.open), "rolled": False}; cycle = {',
        '"entry_premium": float(q.open), "rolled": False, "tail_security_id": (str(tail_selected.security_id) if catastrophe_ratio is not None else ""), "tail_strike": (float(tail_selected.strike) if catastrophe_ratio is not None else np.nan), "tail_mark": (float(tq.open) if catastrophe_ratio is not None else np.nan)}; cycle = {',
        1,
    )
    real_src = real_src.replace(
        '"early_rolls": 0, "last_roll_date": ""}; cost += contracts * real_source.ETF_MULTIPLIER * prior_spot * ONE_WAY; state = "short_put";',
        '"early_rolls": 0, "last_roll_date": "", "catastrophe_ratio": catastrophe_ratio, "tail_expiry_payoff": 0.0}; cost += contracts * real_source.ETF_MULTIPLIER * prior_spot * option_one_way * (2 if catastrophe_ratio is not None else 1); state = "short_put";',
        1,
    )
    real_src = real_src.replace(
        'pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (pos["mark"] - float(q.close)); pos["mark"] = float(q.close)\n            if pending_roll:',
        'pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (pos["mark"] - float(q.close)); pos["mark"] = float(q.close)\n            tq = None\n            if catastrophe_ratio is not None:\n                tail_key = (pos["tail_security_id"], day)\n                tq = options.loc[tail_key, :] if tail_key in options.index else None\n                if tq is not None and tq.close > 0:\n                    pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(tq.close) - pos["tail_mark"]); pos["tail_mark"] = float(tq.close)\n                else: tail_stale_marks += 1\n            if pending_roll:',
        1,
    )
    real_src = real_src.replace(
        'selected = choose_real(chains.get(day, pd.DataFrame()), prior_spot, next_month) if bool(admission.get(day, False)) else None\n                if selected is not None:',
        'chain_today = chains.get(day, pd.DataFrame()); selected = choose_real(chain_today, prior_spot, next_month) if bool(admission.get(day, False)) else None\n                next_tail = choose_tail_real(chain_today, prior_spot, next_month, float(selected.strike), catastrophe_ratio) if selected is not None else None\n                if selected is not None and (catastrophe_ratio is None or next_tail is not None):',
        1,
    )
    real_src = real_src.replace(
        'nq = options.loc[(selected.security_id, day)]\n                    if nq.open > 0 and nq.close > 0 and nq.volume > 0:',
        'nq = options.loc[(selected.security_id, day), :]; next_tail_key = (str(next_tail.security_id), day) if catastrophe_ratio is not None else None; ntq = options.loc[next_tail_key, :] if next_tail_key is not None and next_tail_key in options.index else None\n                    if nq.open > 0 and nq.close > 0 and nq.volume > 0 and (catastrophe_ratio is None or (tq is not None and tq.open > 0 and ntq is not None and ntq.open > 0 and ntq.close > 0 and ntq.volume > 0)):',
        1,
    )
    real_src = real_src.replace(
        'cost += pos["contracts"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * ONE_WAY)\n                        pos.update(',
        'cost += pos["contracts"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * option_one_way)\n                        if catastrophe_ratio is not None:\n                            pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(tq.open) - float(tq.close))\n                            pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(ntq.close) - float(ntq.open))\n                            cost += pos["contracts"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * option_one_way)\n                            pos.update(tail_security_id=str(next_tail.security_id), tail_strike=float(next_tail.strike), tail_mark=float(ntq.close))\n                        pos.update(',
        1,
    )
    real_src = real_src.replace(
        'if day == pos["expiry"]:\n                intrinsic = max(pos["strike"] - etf_close, 0.0);',
        'if day == pos["expiry"]:\n                if catastrophe_ratio is not None:\n                    tail_intrinsic = max(pos["tail_strike"] - etf_close, 0.0); pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (tail_intrinsic - pos["tail_mark"]); cycle["tail_expiry_payoff"] += pos["contracts"] * real_source.ETF_MULTIPLIER * tail_intrinsic\n                intrinsic = max(pos["strike"] - etf_close, 0.0);',
        1,
    )
    real_src = real_src.replace(
        '"entry_skips": skips}',
        '"entry_skips": skips, "tail_entry_skips": tail_skips, "tail_stale_mark_days": tail_stale_marks, "tail_positive_expiries": int((cycles.tail_expiry_payoff.fillna(0) > 0).sum())}',
        1,
    )
    real_src = real_src.replace(
        'daily, cycles = pd.DataFrame(rows), pd.DataFrame(cycles)',
        'daily, cycles = pd.DataFrame(rows), pd.DataFrame(cycles)\n    if "physical_assignment_date" not in cycles: cycles["physical_assignment_date"] = pd.NaT\n    if "tail_expiry_payoff" not in cycles: cycles["tail_expiry_payoff"] = 0.0',
        1,
    )

    model_src = inspect.getsource(short.run_model)
    model_src = model_src.replace(
        "def run_model(admission: pd.Series, threshold: float | None):",
        "def run_model_tail(admission: pd.Series, threshold: float | None, maturity_name: str = 'm1', option_one_way: float = ONE_WAY, catastrophe_ratio: float | None = None):",
        1,
    )
    model_src = model_src.replace(
        'label = "model_hold_to_expiry" if threshold is None else f"model_decay_{int(threshold * 100)}"',
        'label = f"model_{maturity_name}_decay_{int(threshold * 100)}_tail{\'none\' if catastrophe_ratio is None else int(catastrophe_ratio * 100)}"',
        1,
    )
    model_src = model_src.replace(
        'month=day.to_period("M").to_timestamp()+pd.offsets.MonthBegin(1);ex=expiry(month);strike=float(market.iloc[i-1].spot_close)*.95;op=price(m,strike,ex,"open");pos={',
        'month=maturity.target_month(day,maturity_name);ex=expiry(month);strike=float(market.iloc[i-1].spot_close)*.95;op=price(m,strike,ex,"open");tail_strike=float(market.iloc[i-1].spot_close)*catastrophe_ratio if catastrophe_ratio is not None else np.nan;tail_open=price(m,tail_strike,ex,"open") if catastrophe_ratio is not None else np.nan;pos={',
        1,
    )
    model_src = model_src.replace(
        '"entry_premium":op,"rolled":False};cycle={',
        '"entry_premium":op,"rolled":False,"tail_strike":tail_strike,"tail_mark":tail_open};cycle={',
        1,
    )
    model_src = model_src.replace(
        '"early_rolls":0,"last_roll_date":""};cost += pos["units"]*float(market.iloc[i-1].spot_close)*ONE_WAY;',
        '"early_rolls":0,"last_roll_date":"","catastrophe_ratio":catastrophe_ratio,"tail_expiry_payoff":0.0};cost += pos["units"]*float(market.iloc[i-1].spot_close)*option_one_way*(2 if catastrophe_ratio is not None else 1);',
        1,
    )
    model_src = model_src.replace(
        'mark = price(m, pos["strike"], pos["expiry"], "close"); pnl += pos["units"] * (pos["mark"] - mark); pos["mark"] = mark\n            if pending_roll:',
        'mark = price(m, pos["strike"], pos["expiry"], "close"); pnl += pos["units"] * (pos["mark"] - mark); pos["mark"] = mark\n            tail_mark = None\n            if catastrophe_ratio is not None:\n                tail_mark = price(m, pos["tail_strike"], pos["expiry"], "close"); pnl += pos["units"] * (tail_mark - pos["tail_mark"]); pos["tail_mark"] = tail_mark\n            if pending_roll:',
        1,
    )
    model_src = model_src.replace(
        'pnl += pos["units"] * (mark - old_open) + pos["units"] * (new_open - new_close); cost += pos["units"] * float(market.iloc[i-1].spot_close) * 2 * ONE_WAY; pos.update(',
        'pnl += pos["units"] * (mark - old_open) + pos["units"] * (new_open - new_close); cost += pos["units"] * float(market.iloc[i-1].spot_close) * 2 * option_one_way\n                    if catastrophe_ratio is not None:\n                        old_tail_open=price(m,pos["tail_strike"],pos["expiry"],"open");new_tail_strike=float(market.iloc[i-1].spot_close)*catastrophe_ratio;new_tail_open=price(m,new_tail_strike,ex,"open");new_tail_close=price(m,new_tail_strike,ex,"close");pnl += pos["units"]*(old_tail_open-tail_mark)+pos["units"]*(new_tail_close-new_tail_open);cost += pos["units"]*float(market.iloc[i-1].spot_close)*2*option_one_way;pos.update(tail_strike=new_tail_strike,tail_mark=new_tail_close)\n                    pos.update(',
        1,
    )
    model_src = model_src.replace(
        'if day == pos["expiry"]:\n                intrinsic = max(pos["strike"] - float(m.spot_close), 0.0);',
        'if day == pos["expiry"]:\n                if catastrophe_ratio is not None:\n                    tail_intrinsic=max(pos["tail_strike"]-float(m.spot_close),0.0);pnl += pos["units"]*(tail_intrinsic-pos["tail_mark"]);cycle["tail_expiry_payoff"] += pos["units"]*tail_intrinsic\n                intrinsic = max(pos["strike"] - float(m.spot_close), 0.0);',
        1,
    )
    model_src = model_src.replace(
        '"ledger_max_abs_error":float(ledger)}',
        '"ledger_max_abs_error":float(ledger),"entry_skips":0,"tail_entry_skips":0,"tail_positive_expiries":int((cycles.tail_expiry_payoff.fillna(0)>0).sum())}',
        1,
    )
    model_src = model_src.replace(
        'daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)',
        'daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)\n    if "physical_assignment_date" not in cycles:cycles["physical_assignment_date"]=pd.NaT\n    if "tail_expiry_payoff" not in cycles:cycles["tail_expiry_payoff"]=0.0',
        1,
    )
    namespace = dict(vars(short))
    namespace.update(maturity=maturity, choose_tail_real=choose_tail_real)
    exec(compile(real_src, str(Path(__file__)), "exec"), namespace)
    exec(compile(model_src, str(Path(__file__)), "exec"), namespace)
    return namespace["run_real_tail"], namespace["run_model_tail"], real_src + "\n\n" + model_src


def costed_core(put: pd.DataFrame) -> pd.DataFrame:
    return maturity.costed_core(put, 5.0)


def run_layer(scope, schedule, model_engine, real_engine, real_short, model_short):
    frames, _, market, _ = base.ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll = base.ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    real_active, _, real_chains, _, _, real_futures = base.short.real_inputs()
    model_active, model_market, model_futures = base.short.model_source.model_inputs()
    active, futures, marketx = (real_active, real_futures, None) if scope == "real" else (model_active, model_futures, model_market)
    pure = base.baseline(active, scope)
    always = base.mask_schedule(schedule, scope, pure.date)
    put, trades = (real_engine(frames["ic"], always, frames, market, f"{scope}_core", roll)
                   if scope == "real" else model_engine(frames["ic"], always, market, f"{scope}_core", roll))
    put = put[put.date.isin(pure.date)].reset_index(drop=True)
    protected = base.add_core(pure, costed_core(put), f"{scope}_rolling_ic_current_core_put_5bp")
    parts = [pure, protected]; signals = []; cycles_all = []; events_all = []
    audits = {"pure_parity": float(abs(pure.return_net - (active.ic_net_ret + .7 * base.CASH)).max()),
              "core_put_trade_events": len(trades), "core_put_cost_multiplier": 5.0}
    signal = (maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
              if scope == "real" else maturity.maturity_model_signals(active, marketx, "m1"))
    runner = real_short if scope == "real" else model_short
    reference = pd.read_csv(MATURITY_RUN / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    for ratio in RATIOS:
        isolated, events, cycles, short_audit = runner(base.entry_series(signal), DECAY, "m1", OPTION_ONE_WAY, ratio)
        routed = base.stitched_router(scope, active, futures, isolated, signal)
        ic_dates = routed.loc[routed.state.eq("ic"), "date"]
        masked = base.mask_schedule(schedule, scope, ic_dates)
        exits = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
        suffix = "none" if ratio is None else str(int(ratio * 100))
        p, ptrades = (real_engine(frames["ic"], masked, frames, market, f"{scope}_tail{suffix}", roll, exits)
                      if scope == "real" else model_engine(frames["ic"], masked, market, f"{scope}_tail{suffix}", roll, exits))
        p = p[p.date.isin(routed.date)].reset_index(drop=True)
        label = f"{scope}_m1_iv375_short95_decay60_tail{suffix}_5bp"
        combined = base.add_core(routed, costed_core(p), label)
        parts.append(combined); signals.append(signal.assign(scope=scope, catastrophe_ratio=suffix))
        if len(cycles): cycles_all.append(cycles.assign(scope=scope, candidate=label))
        if len(events): events_all.append(events.assign(scope=scope, candidate=label))
        simultaneous = set(ptrades.loc[ptrades.action.eq("route_open_exit"), "actual_execution_date"])
        if simultaneous - exits:
            raise RuntimeError(f"Core Put route exit mismatch {label}")
        parity = None
        if ratio is None:
            ref_label = f"{scope}_m1_iv375_coreput_to_short95_decay60_5bp"
            ref = reference[reference.candidate.eq(ref_label)].sort_values("date")
            parity = float(np.max(np.abs(combined.sort_values("date").return_net.to_numpy() - ref.return_net.to_numpy())))
            if parity > 1e-12:
                raise RuntimeError(f"M1 naked parity failed {scope}: {parity}")
        closed = cycles.closed.fillna(False) if len(cycles) else pd.Series(dtype=bool)
        worst_cycle = float(cycles.loc[closed, "realized_pnl"].min()) if bool(closed.any()) else np.nan
        audits[label] = {**short_audit, "route_switches": len(exits),
                         "core_put_simultaneous_exits": len(simultaneous),
                         "routes_without_active_core_put": len(exits - simultaneous),
                         "naked_m1_daily_parity_error": parity, "worst_closed_cycle_pnl": worst_cycle}
    return (pd.concat(parts, ignore_index=True), pd.concat(signals, ignore_index=True),
            pd.concat(cycles_all, ignore_index=True) if cycles_all else pd.DataFrame(),
            pd.concat(events_all, ignore_index=True) if events_all else pd.DataFrame(), audits)


def classify(full: pd.DataFrame, audits: dict) -> tuple[str, str, dict]:
    checks = {}
    retained = []
    for ratio in (90, 85, 80):
        key = f"tail{ratio}_5bp"
        layer_checks = []
        worst_improved = False
        tail_identified = False
        for scope in ("real", "model"):
            naked = full[full.candidate.eq(f"{scope}_m1_iv375_short95_decay60_tailnone_5bp")].iloc[0]
            cand = full[full.candidate.eq(f"{scope}_m1_iv375_short95_decay60_{key}")].iloc[0]
            cagr_loss_pp = 100.0 * float(naked.ann_return - cand.ann_return)
            mdd_worse_pp = 100.0 * max(0.0, abs(float(cand.max_dd)) - abs(float(naked.max_dd)))
            na = audits[scope][f"{scope}_m1_iv375_short95_decay60_tailnone_5bp"]
            ca = audits[scope][f"{scope}_m1_iv375_short95_decay60_{key}"]
            worst_improved |= bool(ca["worst_closed_cycle_pnl"] > na["worst_closed_cycle_pnl"])
            tail_identified |= bool(ca["tail_positive_expiries"] > 0)
            layer_checks.append({"scope": scope, "cagr_loss_pp": cagr_loss_pp,
                                 "mdd_worse_pp": mdd_worse_pp,
                                 "return_gate": cagr_loss_pp <= 1.50,
                                 "drawdown_gate": mdd_worse_pp <= 0.50})
        passed = all(x["return_gate"] and x["drawdown_gate"] for x in layer_checks) and worst_improved and tail_identified
        checks[str(ratio)] = {"layers": layer_checks, "worst_cycle_improved_any_layer": worst_improved,
                              "tail_effect_identified": tail_identified, "individual_gate_pass": passed}
        if passed: retained.append(ratio)
    adjacent = any(a in retained and b in retained for a, b in ((90, 85), (85, 80)))
    decision = ("retain_adjacent_catastrophe_ratios_for_research_no_production_change" if adjacent
                else "reject_all_catastrophe_ratios_pre_registered_gate_failed_no_production_change")
    stability = ("catastrophe_layer_has_adjacent_cross_layer_candidates" if adjacent
                 else "catastrophe_layer_no_adjacent_candidate_and_or_tail_effect_unidentified")
    checks["adjacent_gate"] = adjacent
    return decision, stability, checks


def main():
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    schedule = base.current_schedule(); model_engine, real_engine, core_source = base.patched_engines()
    real_short, model_short, short_source = protected_short_runners()
    results = {scope: run_layer(scope, schedule, model_engine, real_engine, real_short, model_short)
               for scope in ("real", "model")}
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    summary, wide, unavailable = base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()
    audit = {"real": results["real"][4], "model": results["model"][4]}
    decision, stability, decision_checks = classify(full, audit)
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv", index=False); cycles.to_csv(out / "cycles.csv", index=False); events.to_csv(out / "events.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + core_source, encoding="utf-8")
    meta.update({
        "scan_type": "candidate_bundle", "baseline": {"pure": "*_pure_rolling_ic", "protected": "*_rolling_ic_current_core_put_5bp", "naked": "*_tailnone_5bp"},
        "candidate_grid": [{"catastrophe_ratio": x, "maturity": "m1", "iv_threshold": IV_THRESHOLD, "decay": DECAY} for x in RATIOS],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 actual 510500 Put/ETF and IC", "model": "2015-04-16..2026-08-14 theoretical 510500 Put plus historical IC"},
        "cost_model": {"510500_put_one_way": OPTION_ONE_WAY, "put_round_trip": 2 * OPTION_ONE_WAY,
                       "ETF_and_IC_one_way": base.ONE_WAY, "risk_buffer": 0.30, "cash_annual": 0.03},
        "audit": audit, "decision_checks": decision_checks, "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signals.csv"),
                    "cycles": str(out / "cycles.csv"), "events": str(out / "events.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "maturity_run_meta": sha(MATURITY_RUN / "scan_meta.json")},
        "warnings": ["Research only; IC has no Call.", "Real listed option history is short.",
                     "Model 510500 Put is theoretical and not executable history.",
                     "No bid-ask, dynamic margin, forced liquidation, tax, capacity or integer sizing."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = ("# IC M+1卖95% Put灾难保护分层补测\n\n## Run Metadata\n\n研究专用；IC无Call；生产未修改。\n\n"
              "## Research Question\n\n固定M+1、IV37.5%、衰减60%，比较无保护及同到期等数量90%/85%/80%保护Put。\n\n"
              "## Implementation Anchor\n\n沿用现行IC准入、核心Put同步退出、实物交割ETF转IC及回本退出；裸卖M+1逐日复现上一层。\n\n"
              "## Data Snapshot\n\n真实2022-09-19至2026-08-14；理论2015-04-16至2026-08-14，严格分层。\n\n"
              "## Cost and Execution Assumptions\n\n每条510500 Put腿每次买卖单边5BP；ETF与IC单边1BP；T收盘信号、T+1开盘同步执行。\n\n"
              "## Commands\n\n见command_log.txt。\n\n## Output Files\n\n见scan_meta.json。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) +
              "\n\n## Window Results\n\n见scan_summary.csv与window_metrics.csv。\n\n## Audit\n\n```json\n" + json.dumps(audit, ensure_ascii=False, indent=2) +
              "\n```\n\n## Decision Gates\n\n```json\n" + json.dumps(decision_checks, ensure_ascii=False, indent=2) +
              "\n```\n\n## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision +
              "\n\n## User-Facing Summary\n\n本层完成后暂停，由用户确认是否进入相对IV层。\n")
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(decision_checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
