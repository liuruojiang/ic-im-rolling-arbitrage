"""IC current-core-Put to high-IV short95 router: initial maturity scan."""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_coreput_highiv_short95_router_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_ic_v1_3_current_core_put_plus_high_iv_short95_router_ic_short95_maturity_front10_m1_m2_m3"
SPEC = ROOT / "docs" / "ic_short95_maturity_scan_v1_spec.md"
REFERENCE = base.RUN
MATURITIES = ("front10", "m1", "m2", "m3")
IV_THRESHOLD = 0.375
DECAY = 0.60
MIN_FRONT_SESSIONS = 10
OPTION_ONE_WAY = 0.0005


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def target_month(execution: pd.Timestamp, maturity: str) -> pd.Timestamp:
    return execution.to_period("M").to_timestamp() + pd.offsets.MonthBegin({"m1": 1, "m2": 2, "m3": 3}[maturity])


def real_entry_month(day, chain, expiries, dates, maturity):
    day = pd.Timestamp(day)
    if maturity != "front10":
        return target_month(day, maturity)
    month0 = day.to_period("M").to_timestamp()
    front = chain[chain.contract_month.eq(month0)] if len(chain) else chain
    if not front.empty:
        expiry = pd.Timestamp(expiries.loc[str(front.iloc[0].security_id)])
        remaining = int(((dates >= day) & (dates <= expiry)).sum())
        if remaining >= MIN_FRONT_SESSIONS:
            return month0
    return month0 + pd.offsets.MonthBegin(1)


def model_entry_month(day, dates, expiry_fn, maturity):
    day = pd.Timestamp(day)
    if maturity != "front10":
        return target_month(day, maturity)
    month0 = day.to_period("M").to_timestamp()
    expiry = expiry_fn(month0)
    remaining = int(((dates >= day) & (dates <= expiry)).sum())
    return month0 if remaining >= MIN_FRONT_SESSIONS else month0 + pd.offsets.MonthBegin(1)


def patched_short_runners():
    short = base.short
    real_src = inspect.getsource(short.run_real)
    real_src = real_src.replace(
        "def run_real(admission: pd.Series, threshold: float | None):",
        "def run_real_maturity(admission: pd.Series, threshold: float | None, maturity: str = 'm1', option_one_way: float = ONE_WAY):",
        1,
    )
    real_src = real_src.replace(
        'label = "real_hold_to_expiry" if threshold is None else f"real_decay_{int(threshold * 100)}"',
        'label = f"real_{maturity}_hold" if threshold is None else f"real_{maturity}_decay_{int(threshold * 100)}"',
        1,
    )
    old = 'prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"]); month = day.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1); selected = choose_real(chains.get(day, pd.DataFrame()), prior_spot, month)'
    new = 'prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"]); chain_today = chains.get(day, pd.DataFrame()); month = real_entry_month(day, chain_today, expiries, pd.DatetimeIndex(active.date), maturity); selected = choose_real(chain_today, prior_spot, month)'
    if old not in real_src:
        raise RuntimeError("Real maturity entry hook changed")
    real_src = real_src.replace(old, new, 1)
    real_src = real_src.replace(
        "cost += pos[\"contracts\"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * ONE_WAY)",
        "cost += pos[\"contracts\"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * option_one_way)",
        1,
    )
    real_src = real_src.replace(
        "cost += contracts * real_source.ETF_MULTIPLIER * prior_spot * ONE_WAY; state = \"short_put\"",
        "cost += contracts * real_source.ETF_MULTIPLIER * prior_spot * option_one_way; state = \"short_put\"",
        1,
    )
    real_src = real_src.replace(
        '"contracts": contracts, "closed": False',
        '"contracts": contracts, "entry_contract_month": str(month.date()), "maturity": maturity, "closed": False',
        1,
    )
    real_src = real_src.replace(
        'daily, cycles = pd.DataFrame(rows), pd.DataFrame(cycles)',
        'daily, cycles = pd.DataFrame(rows), pd.DataFrame(cycles)\n    if "physical_assignment_date" not in cycles: cycles["physical_assignment_date"] = pd.NaT\n    if "early_rolls" not in cycles: cycles["early_rolls"] = 0',
        1,
    )

    model_src = inspect.getsource(short.run_model)
    model_src = model_src.replace(
        "def run_model(admission: pd.Series, threshold: float | None):",
        "def run_model_maturity(admission: pd.Series, threshold: float | None, maturity: str = 'm1', option_one_way: float = ONE_WAY):",
        1,
    )
    model_src = model_src.replace(
        'label = "model_hold_to_expiry" if threshold is None else f"model_decay_{int(threshold * 100)}"',
        'label = f"model_{maturity}_hold" if threshold is None else f"model_{maturity}_decay_{int(threshold * 100)}"',
        1,
    )
    old = 'month=day.to_period("M").to_timestamp()+pd.offsets.MonthBegin(1);ex=expiry(month);strike=float(market.iloc[i-1].spot_close)*.95;op=price(m,strike,ex,"open")'
    new = 'month=model_entry_month(day,dates,expiry,maturity);ex=expiry(month);strike=float(market.iloc[i-1].spot_close)*.95;op=price(m,strike,ex,"open")'
    if old not in model_src:
        raise RuntimeError("Model maturity entry hook changed")
    model_src = model_src.replace(old, new, 1)
    model_src = model_src.replace(
        "cost += pos[\"units\"] * float(market.iloc[i-1].spot_close) * 2 * ONE_WAY",
        "cost += pos[\"units\"] * float(market.iloc[i-1].spot_close) * 2 * option_one_way",
        1,
    )
    model_src = model_src.replace(
        'cost += pos["units"]*float(market.iloc[i-1].spot_close)*ONE_WAY;state="short_put"',
        'cost += pos["units"]*float(market.iloc[i-1].spot_close)*option_one_way;state="short_put"',
        1,
    )
    model_src = model_src.replace(
        '"candidate":label,"entry_date":str(day.date()),"closed":False',
        '"candidate":label,"entry_date":str(day.date()),"entry_contract_month":str(month.date()),"maturity":maturity,"closed":False',
        1,
    )
    model_src = model_src.replace(
        'daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)',
        'daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)\n    if "physical_assignment_date" not in cycles:cycles["physical_assignment_date"]=pd.NaT\n    if "early_rolls" not in cycles:cycles["early_rolls"]=0',
        1,
    )
    namespace = dict(vars(short))
    namespace.update(real_entry_month=real_entry_month, model_entry_month=model_entry_month)
    exec(compile(real_src, str(Path(__file__)), "exec"), namespace)
    exec(compile(model_src, str(Path(__file__)), "exec"), namespace)
    return namespace["run_real_maturity"], namespace["run_model_maturity"], real_src + "\n\n" + model_src


def maturity_real_signals(active, chains, histories, maturity):
    admission = base.permission()
    history = histories.set_index(["security_id", "date"])
    _, _, _, _, expiries, _ = base.short.real_inputs()
    dates = pd.DatetimeIndex(active.date)
    rows = []
    for i in range(1, len(active)):
        evaluation = pd.Timestamp(active.loc[i - 1, "date"])
        execution = pd.Timestamp(active.loc[i, "date"])
        spot = float(active.loc[i - 1, "csi500_price_close"])
        chain = chains.get(evaluation, pd.DataFrame())
        month = real_entry_month(execution, chain, expiries, dates, maturity)
        fallback = maturity == "front10" and month != execution.to_period("M").to_timestamp()
        selected = base.short.choose_real(chain, spot, month)
        iv = np.nan; valid = False; contract = ""
        if selected is not None:
            iv = float(selected.implied_volatility); contract = str(selected.security_id)
            q = history.loc[(contract, execution)] if (contract, execution) in history.index else None
            valid = bool(q is not None and float(q.open) > 0 and float(q.volume) > 0)
        route = bool(admission.get(execution, False) and valid and np.isfinite(iv) and iv > IV_THRESHOLD)
        rows.append({"eval_date": evaluation, "execution_date": execution, "maturity": maturity,
                     "selected_contract_month": month, "execution_contract": contract,
                     "iv": iv, "admission": bool(admission.get(execution, False)),
                     "execution_open_valid": valid, "front_fallback_to_m1": fallback, "route": route})
    return pd.DataFrame(rows)


def maturity_model_signals(active, market, maturity):
    admission = base.permission(); indexed = market.set_index("date"); dates = pd.DatetimeIndex(active.date)
    expiry_fn = lambda month: base.proxy.fourth_wednesday(month, dates)
    rows = []
    for i in range(1, len(active)):
        evaluation = pd.Timestamp(active.loc[i - 1, "date"]); execution = pd.Timestamp(active.loc[i, "date"])
        month = model_entry_month(execution, dates, expiry_fn, maturity)
        fallback = maturity == "front10" and month != execution.to_period("M").to_timestamp()
        iv = float(indexed.loc[evaluation, "sigma_close"])
        rows.append({"eval_date": evaluation, "execution_date": execution, "maturity": maturity,
                     "selected_contract_month": month, "execution_contract": "THEORETICAL",
                     "iv": iv, "admission": bool(admission.get(execution, False)),
                     "execution_open_valid": True, "front_fallback_to_m1": fallback,
                     "route": bool(admission.get(execution, False) and iv > IV_THRESHOLD)})
    return pd.DataFrame(rows)


def costed_core(put, multiplier):
    out = put.copy(); out["put_cost_rate"] = out.put_cost_rate.astype(float) * multiplier; return out


def run_layer(scope, schedule, model_engine, real_engine, real_short, model_short):
    frames, _, market, _ = base.ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll = base.ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    real_active, _, real_chains, _, _, real_futures = base.short.real_inputs()
    model_active, model_market, model_futures = base.short.model_source.model_inputs()
    if scope == "real":
        active, futures, marketx = real_active, real_futures, None
    else:
        active, futures, marketx = model_active, model_futures, model_market
    pure = base.baseline(active, scope)
    always = base.mask_schedule(schedule, scope, pure.date)
    put, trades = (real_engine(frames["ic"], always, frames, market, f"{scope}_core", roll)
                   if scope == "real" else model_engine(frames["ic"], always, market, f"{scope}_core", roll))
    put = put[put.date.isin(pure.date)].reset_index(drop=True)
    protected = base.add_core(pure, costed_core(put, 5.0), f"{scope}_rolling_ic_current_core_put_5bp")
    parts = [pure, protected]; signals = []; cycles_all = []; events_all = []
    audits = {"pure_parity": float(abs(pure.return_net - (active.ic_net_ret + .7 * base.CASH)).max()),
              "core_put_trade_events": len(trades), "core_put_cost_multiplier": 5.0}
    reference = pd.read_csv(REFERENCE / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    for maturity in MATURITIES:
        signal = (maturity_real_signals(active, real_chains, frames["histories"], maturity)
                  if scope == "real" else maturity_model_signals(active, marketx, maturity))
        runner = real_short if scope == "real" else model_short
        isolated, events, cycles, short_audit = runner(base.entry_series(signal), DECAY, maturity, OPTION_ONE_WAY)
        routed = base.stitched_router(scope, active, futures, isolated, signal)
        ic_dates = routed.loc[routed.state.eq("ic"), "date"]
        masked = base.mask_schedule(schedule, scope, ic_dates)
        exits = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
        p, ptrades = (real_engine(frames["ic"], masked, frames, market, f"{scope}_{maturity}", roll, exits)
                      if scope == "real" else model_engine(frames["ic"], masked, market, f"{scope}_{maturity}", roll, exits))
        p = p[p.date.isin(routed.date)].reset_index(drop=True)
        label = f"{scope}_{maturity}_iv375_coreput_to_short95_decay60_5bp"
        combined = base.add_core(routed, costed_core(p, 5.0), label)
        parts.append(combined); signals.append(signal.assign(scope=scope))
        if len(cycles): cycles_all.append(cycles.assign(scope=scope, maturity=maturity, candidate=label))
        if len(events): events_all.append(events.assign(scope=scope, maturity=maturity, candidate=label))
        simultaneous = set(ptrades.loc[ptrades.action.eq("route_open_exit"), "actual_execution_date"])
        if simultaneous - exits:
            raise RuntimeError(f"Core Put route exit mismatch {label}")
        parity = None
        if maturity == "m1":
            isolated_1bp, _, _, _ = runner(base.entry_series(signal), DECAY, maturity, base.ONE_WAY)
            routed_1bp = base.stitched_router(scope, active, futures, isolated_1bp, signal)
            ic_dates_1bp = routed_1bp.loc[routed_1bp.state.eq("ic"), "date"]
            masked_1bp = base.mask_schedule(schedule, scope, ic_dates_1bp)
            exits_1bp = set(routed_1bp.loc[routed_1bp.action.eq("ic_to_short_put_open"), "date"])
            p1, _ = (real_engine(frames["ic"], masked_1bp, frames, market, f"{scope}_m1p", roll, exits_1bp)
                     if scope == "real" else model_engine(frames["ic"], masked_1bp, market, f"{scope}_m1p", roll, exits_1bp))
            p1 = p1[p1.date.isin(routed_1bp.date)].reset_index(drop=True)
            old = base.add_core(routed_1bp, p1, "parity")
            ref_label = f"{scope}_iv375_coreput_to_short95_decay60"
            ref = reference[reference.candidate.eq(ref_label)].sort_values("date")
            parity = float(np.max(np.abs(old.return_net.to_numpy() - ref.return_net.to_numpy())))
            if parity > 1e-12:
                raise RuntimeError(f"M1 old-cost parity failed {scope}: {parity}")
        audits[label] = {**short_audit, "route_switches": len(exits),
                         "core_put_simultaneous_exits": len(simultaneous),
                         "routes_without_active_core_put": len(exits - simultaneous),
                         "front_fallback_days": int(signal.front_fallback_to_m1.sum()),
                         "old_1bp_m1_daily_parity_error": parity}
    return (pd.concat(parts, ignore_index=True), pd.concat(signals, ignore_index=True),
            pd.concat(cycles_all, ignore_index=True) if cycles_all else pd.DataFrame(),
            pd.concat(events_all, ignore_index=True) if events_all else pd.DataFrame(), audits)


def main():
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    schedule = base.current_schedule(); model_engine, real_engine, core_source = base.patched_engines()
    real_short, model_short, short_source = patched_short_runners()
    results = {}
    for scope in ("real", "model"):
        results[scope] = run_layer(scope, schedule, model_engine, real_engine, real_short, model_short)
    daily = pd.concat([results["real"][0], results["model"][0]], ignore_index=True)
    signals = pd.concat([results["real"][1], results["model"][1]], ignore_index=True)
    cycles = pd.concat([results["real"][2], results["model"][2]], ignore_index=True)
    events = pd.concat([results["real"][3], results["model"][3]], ignore_index=True)
    summary, wide, unavailable = base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv", index=False); cycles.to_csv(out / "cycles.csv", index=False); events.to_csv(out / "events.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + core_source, encoding="utf-8")
    audit = {"real": results["real"][4], "model": results["model"][4]}
    routed_full = full[full.candidate.str.contains("front10|_m1_|_m2_|_m3_", regex=True)].copy()
    winners = routed_full.sort_values(["candidate"]).to_dict("records")
    decision = "pending_layer_review_no_production_change"
    stability = "maturity_layer_complete_pending_user_confirmation"
    meta.update({
        "scan_type": "candidate_bundle", "baseline": {"pure": "*_pure_rolling_ic", "protected": "*_rolling_ic_current_core_put_5bp"},
        "candidate_grid": [{"maturity": x, "iv_threshold": IV_THRESHOLD, "decay": DECAY} for x in MATURITIES],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 actual 510500 Put/ETF and IC", "model": "2015-04-16..2026-08-14 theoretical 510500 Put plus historical IC"},
        "cost_model": {"510500_put_one_way": OPTION_ONE_WAY, "put_round_trip": 2 * OPTION_ONE_WAY,
                       "ETF_and_IC_one_way": base.ONE_WAY, "risk_buffer": 0.30, "cash_annual": 0.03},
        "audit": audit, "unavailable_segments": unavailable, "full_results": winners,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signals.csv"),
                    "cycles": str(out / "cycles.csv"), "events": str(out / "events.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC),
                          "real_510500_loader": sha(Path(base.short.__file__)),
                          "ic_futures": sha(base.short.real_source.ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv")},
        "warnings": ["Research only; IC has no Call.", "Real listed option history is short.",
                     "Model 510500 Put is theoretical and not executable history.",
                     "No bid-ask, dynamic margin, forced liquidation, tax, capacity or integer sizing."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = ("# IC高IV卖95% Put期限分层补测\n\n## Run Metadata\n\n研究专用；IC无Call；生产未修改。\n\n"
              "## Research Question\n\n在固定IV37.5%和衰减60%下比较front10、M+1、M+2、M+3。\n\n"
              "## Implementation Anchor\n\n沿用现行IC准入、核心Put、实物交割ETF转IC及回本退出状态机；M+1旧1BP逐日复现。\n\n"
              "## Data Snapshot\n\n真实2022-09-19至2026-08-14；理论2015-04-16至2026-08-14，严格分层。\n\n"
              "## Cost and Execution Assumptions\n\n510500 Put单边5BP；ETF与IC单边1BP；T收盘信号、T+1开盘执行。\n\n"
              "## Runtime Override Plan\n\n仅研究时覆盖初始期限，不改生产。\n\n## Commands\n\n见command_log.txt。\n\n"
              "## Output Files\n\n见scan_meta.json。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) +
              "\n\n## Window Results\n\n见scan_summary.csv与window_metrics.csv。\n\n## Audit\n\n```json\n" + json.dumps(audit, ensure_ascii=False, indent=2) +
              "\n```\n\n## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision +
              "\n\n## User-Facing Summary\n\n本层完成后暂停，由用户确认是否进入下一层。\n")
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
