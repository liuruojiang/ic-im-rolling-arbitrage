"""IC short-95% Put: one premium-decay early roll with current re-admission.

This is an isolated research harness.  It keeps the existing physical
510500-ETF assignment -> IC conversion and IC-cycle breakeven exit intact.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import ic_mainline_v1_3 as native
import ic_roll_momentum_stage2_put_v2 as ic_put
import ic_510500_put_proxy_validation_v1 as proxy
import research_ic_short95_put_to_ic_recovery_v1 as real_source
import research_ic_short95_put_to_ic_recovery_full_model_v1 as model_source

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260916_ic_im_ic_short95_recovery_current_admissions_nearest_month_"
    "short_put_one_step_early_roll_premium_decay_50_80_with_ic_re_admission"
)
THRESHOLDS = (None, 0.50, 0.60, 0.70, 0.80)
ONE_WAY = 0.0001


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def permissions() -> pd.Series:
    """Prior-close IC 0/1 valuation tier and original v1.3 execution permit."""
    frames, _, _, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    score = pd.read_csv(
        ROOT / "outputs" / "ic_fixed_valuation_unbounded_score_v6" /
        "daily_unbounded_fixed_scores.csv.gz", parse_dates=["date"]
    )[["date", "unbounded_median_knot"]]
    valuation = frames["ic"][["date"]].merge(score, on="date", how="left", validate="one_to_one")
    valuation = valuation.set_index("date").unbounded_median_knot.lt(1.95)
    schedule = native.build_momentum_schedule(
        pd.read_csv(native.CSI500_OHLCV_PATH, parse_dates=["date"])
    ).set_index("date").momentum_execution_weight.gt(0)
    # Both values already implement the T-close -> next-session execution
    # convention.  No extra shift and no MOM120 condition is applied here.
    return (valuation & schedule.reindex(valuation.index, fill_value=False)).rename("admission")


def choose_real(chain: pd.DataFrame, prior_spot: float, month: pd.Timestamp):
    chain = chain[chain.contract_month.eq(month)].copy()
    if chain.empty:
        return None
    chain["distance"] = (chain.strike.astype(float) - prior_spot * 0.95).abs()
    return chain.sort_values(["distance", "strike", "contract_id"]).iloc[0]


def real_inputs():
    frames, _, _, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    active = frames["ic"].copy()
    active = active[active.date.ge(real_source.START)].reset_index(drop=True)
    etf = frames["etf500"].set_index("date")
    snapshots, histories = frames["snapshots"].copy(), frames["histories"].copy()
    options = histories.set_index(["security_id", "date"])
    # The last observed quote is not the listed expiry.  In particular, an
    # open contract at the sample boundary must remain marked as open instead
    # of being force-settled on the final data row.  Recover the contract month
    # from the daily chain master and derive the SSE fourth-Wednesday expiry.
    contract_months = (
        snapshots[["security_id", "contract_month"]]
        .drop_duplicates()
        .set_index("security_id")["contract_month"]
    )
    if contract_months.index.has_duplicates:
        raise RuntimeError("A 510500 option security_id maps to multiple contract months")
    trade_dates = pd.DatetimeIndex(active.date)
    expiries = contract_months.map(
        lambda month: proxy.fourth_wednesday(pd.Timestamp(month), trade_dates)
    )
    chains = {pd.Timestamp(d): g for d, g in snapshots.groupby("date", sort=False)}
    futures = pd.read_csv(ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv", parse_dates=["date"]).set_index(["contract", "date"])
    return active, etf, chains, options, expiries, futures


def run_real(admission: pd.Series, threshold: float | None):
    active, etf, chains, options, expiries, futures = real_inputs()
    equity, state, pos, pending_exit, pending_roll, cycle = 1.0, "idle", None, False, False, None
    rows, events, cycles, skips = [], [], [], 0
    label = "real_hold_to_expiry" if threshold is None else f"real_decay_{int(threshold * 100)}"
    for i, b in active.iterrows():
        day = pd.Timestamp(b.date); previous = equity; pnl = cost = 0.0; action = ""
        etf_open, etf_close = float(etf.loc[day, "open"]), float(etf.loc[day, "close"])
        if state == "short_put":
            q = options.loc[(pos["security_id"], day)]
            if not (q.close > 0 and q.volume > 0): raise RuntimeError(f"Unmarkable option {day.date()}")
            pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (pos["mark"] - float(q.close)); pos["mark"] = float(q.close)
            if pending_roll:
                prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"])
                next_month = pd.Timestamp(pos["contract_month"]) + pd.offsets.MonthBegin(1)
                selected = choose_real(chains.get(day, pd.DataFrame()), prior_spot, next_month) if bool(admission.get(day, False)) else None
                if selected is not None:
                    nq = options.loc[(selected.security_id, day)]
                    if nq.open > 0 and nq.close > 0 and nq.volume > 0:
                        # Marked old/new legs at close, then execute both sides at open.
                        pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(q.close) - float(q.open))
                        pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (float(nq.open) - float(nq.close))
                        cost += pos["contracts"] * real_source.ETF_MULTIPLIER * prior_spot * (2 * ONE_WAY)
                        pos.update(security_id=str(selected.security_id), contract=str(selected.contract_id), strike=float(selected.strike), mark=float(nq.close), expiry=pd.Timestamp(expiries.loc[selected.security_id]), contract_month=pd.Timestamp(selected.contract_month), entry_premium=float(nq.open), rolled=True, roll_wait_reason="")
                        cycle["early_rolls"] += 1; cycle["last_roll_date"] = str(day.date()); action = "early_roll_buyback_and_sell_next_open"
                        pending_roll = False
                    else:
                        reason = "early_roll_untradable_new_leg"
                        if pos.get("roll_wait_reason") != reason: action = reason
                        pos["roll_wait_reason"] = reason
                else:
                    reason = "early_roll_blocked_re_admission"
                    if pos.get("roll_wait_reason") != reason: action = reason
                    pos["roll_wait_reason"] = reason
            if day == pos["expiry"]:
                intrinsic = max(pos["strike"] - etf_close, 0.0); pnl += pos["contracts"] * real_source.ETF_MULTIPLIER * (pos["mark"] - intrinsic)
                if intrinsic > 0:
                    pos = {"shares": pos["contracts"] * real_source.ETF_MULTIPLIER, "etf_mark": etf_close}; state = "pending_etf_to_ic"; action = "physical_assignment_pending_etf_to_ic"; cycle["physical_assignment_date"] = str(day.date())
                else:
                    cycle["realized_pnl"] += pnl - cost; cycle.update(exit_date=str(day.date()), exit_reason="worthless_expiry", closed=True); cycles.append(cycle.copy()); cycle = pos = None; state = "idle"; action = "worthless_expiry"
        elif state == "pending_etf_to_ic":
            shares = pos["shares"]; pnl += shares * (etf_open - pos["etf_mark"]); cost += shares * etf_open * ONE_WAY
            fq = futures.loc[(b.contract, day)]; units = shares * etf_open / (float(fq.open) * real_source.IC_MULTIPLIER); cost += units * real_source.IC_MULTIPLIER * float(fq.open) * ONE_WAY; pnl += units * real_source.IC_MULTIPLIER * (float(fq.settle) - float(fq.open))
            pos = {"contract": str(b.contract), "units": units, "mark": float(fq.settle)}; state = "ic_future"; action = "assignment_sell_etf_buy_ic_next_open"; cycle["ic_conversion_date"] = str(day.date())
        elif state == "ic_future":
            fq = futures.loc[(pos["contract"], day)]
            if pending_exit:
                pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.open) - pos["mark"]); cost += pos["units"] * real_source.IC_MULTIPLIER * float(fq.open) * ONE_WAY; cycle["realized_pnl"] += pnl - cost; cycle.update(exit_date=str(day.date()), exit_reason="recovery_exit_ic_next_open", closed=True, recovery_days=int((day - pd.Timestamp(cycle["ic_conversion_date"])).days)); cycles.append(cycle.copy()); cycle = pos = None; state = "idle"; pending_exit = False; action = "recovery_exit_ic_next_open"
            elif pd.notna(b.roll_to) and str(b.roll_to) != pos["contract"]:
                nq = futures.loc[(str(b.roll_to), day)]; pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.close) - pos["mark"]); cost += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.close) + float(nq.close)) * ONE_WAY; pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(nq.settle) - float(nq.close)); pos.update(contract=str(b.roll_to), mark=float(nq.settle)); action = "ic_monthly_roll_close"
            else: pnl += pos["units"] * real_source.IC_MULTIPLIER * (float(fq.settle) - pos["mark"]); pos["mark"] = float(fq.settle)
        if state == "idle" and i > 0 and not action and bool(admission.get(day, False)):
            prior_spot = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"]); month = day.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1); selected = choose_real(chains.get(day, pd.DataFrame()), prior_spot, month)
            if selected is None: skips += 1; action = "skip_missing_next_month_chain"
            else:
                q = options.loc[(selected.security_id, day)]
                if q.open > 0 and q.volume > 0:
                    contracts = previous / (prior_spot * real_source.ETF_MULTIPLIER); pos = {"security_id": str(selected.security_id), "contract": str(selected.contract_id), "strike": float(selected.strike), "contracts": contracts, "mark": float(q.open), "expiry": pd.Timestamp(expiries.loc[selected.security_id]), "contract_month": pd.Timestamp(selected.contract_month), "entry_premium": float(q.open), "rolled": False, "roll_wait_reason": ""}; cycle = {"candidate": label, "entry_date": str(day.date()), "put_contract": str(selected.contract_id), "entry_moneyness": float(selected.strike) / prior_spot, "contracts": contracts, "closed": False, "realized_pnl": 0.0, "early_rolls": 0, "last_roll_date": ""}; cost += contracts * real_source.ETF_MULTIPLIER * prior_spot * ONE_WAY; state = "short_put"; action = "sell_next_month_95_put_open"
                else: skips += 1; action = "skip_untradable_nearest_strike"
        if cycle is not None: cycle["realized_pnl"] += pnl - cost
        cash_weight = 1.0 if state == "idle" else 0.70; cash = previous * cash_weight * real_source.CASH_DAILY; equity += pnl - cost + cash
        if not np.isfinite(equity) or equity <= 0: raise RuntimeError(f"Invalid NAV {day}")
        if state == "short_put" and threshold is not None and not pos["rolled"] and not pending_roll and not action and pos["mark"] <= pos["entry_premium"] * (1 - threshold): pending_roll = True; pos["roll_wait_reason"] = ""; action = f"premium_decay_{int(threshold*100)}_signal_close"
        if state == "ic_future" and cycle["realized_pnl"] >= pos["units"] * real_source.IC_MULTIPLIER * pos["mark"] * ONE_WAY: pending_exit = True
        row = {"date": day, "candidate": label, "return_net": equity / previous - 1, "nav": equity, "pnl": pnl, "cost": cost, "cash": cash, "cash_weight": cash_weight, "state": state, "action": action, "pending_roll": pending_roll}
        rows.append(row)
        if action: events.append(row.copy())
    if cycle is not None: cycle.update(mark_date=str(active.date.iloc[-1].date()), open_cycle_pnl=cycle["realized_pnl"]); cycles.append(cycle.copy())
    daily, cycles = pd.DataFrame(rows), pd.DataFrame(cycles)
    if "open_cycle_pnl" not in cycles: cycles["open_cycle_pnl"] = np.nan
    closed = cycles.closed.fillna(False); ledger = abs((daily.pnl - daily.cost).sum() - cycles.loc[closed, "realized_pnl"].sum() - cycles.loc[~closed, "open_cycle_pnl"].fillna(0).sum())
    if ledger > 1e-11: raise RuntimeError(f"Real ledger {ledger}")
    return daily, pd.DataFrame(events), cycles, {"rows": len(daily), "cycles": len(cycles), "assignments": int(cycles.physical_assignment_date.notna().sum()), "early_rolls": int(cycles.early_rolls.sum()), "ledger_max_abs_error": float(ledger), "entry_skips": skips}


def run_model(admission: pd.Series, threshold: float | None):
    active, market, futures = model_source.model_inputs()
    market = market.set_index("date").loc[active.date].reset_index()
    dates = pd.DatetimeIndex(active.date); equity, state, pos, pending_exit, pending_roll, cycle = 1.0, "idle", None, False, False, None
    rows, events, cycles = [], [], []
    label = "model_hold_to_expiry" if threshold is None else f"model_decay_{int(threshold * 100)}"
    def expiry(month): return proxy.fourth_wednesday(month, dates)
    def price(row, strike, ex, field):
        years = max((ex - pd.Timestamp(row.date)).days / 365.0, 0.0)
        return proxy.bs_put(float(getattr(row, f"spot_{field}")), strike, float(getattr(row, f"rate_{field}")), float(getattr(row, f"dividend_{field}")), float(getattr(row, f"sigma_{field}")), years)
    for i, b in active.iterrows():
        day = pd.Timestamp(b.date); m = market.iloc[i]; previous = equity; pnl = cost = 0.0; action = ""
        if state == "short_put":
            mark = price(m, pos["strike"], pos["expiry"], "close"); pnl += pos["units"] * (pos["mark"] - mark); pos["mark"] = mark
            if pending_roll:
                if bool(admission.get(day, False)):
                    month = pos["contract_month"] + pd.offsets.MonthBegin(1); ex = expiry(month); strike = float(market.iloc[i-1].spot_close) * .95; old_open = price(m, pos["strike"], pos["expiry"], "open"); new_open = price(m, strike, ex, "open"); new_close = price(m, strike, ex, "close")
                    pnl += pos["units"] * (mark - old_open) + pos["units"] * (new_open - new_close); cost += pos["units"] * float(market.iloc[i-1].spot_close) * 2 * ONE_WAY; pos.update(strike=strike, expiry=ex, contract_month=month, mark=new_close, entry_premium=new_open, rolled=True, roll_wait_reason=""); cycle["early_rolls"] += 1; cycle["last_roll_date"] = str(day.date()); action = "early_roll_buyback_and_sell_next_open"; pending_roll = False
                else:
                    reason = "early_roll_blocked_re_admission"
                    if pos.get("roll_wait_reason") != reason: action = reason
                    pos["roll_wait_reason"] = reason
            if day == pos["expiry"]:
                intrinsic = max(pos["strike"] - float(m.spot_close), 0.0); pnl += pos["units"] * (pos["mark"] - intrinsic)
                if intrinsic > 0: pos = {"shares": pos["units"], "mark": float(m.spot_close)}; state = "assigned_etf"; cycle["physical_assignment_date"] = str(day.date()); action = "physical_assignment_proxy"
                else: cycle["realized_pnl"] += pnl-cost; cycle.update(exit_date=str(day.date()), exit_reason="worthless_expiry", closed=True); cycles.append(cycle.copy()); cycle = pos = None; state = "idle"; action = "worthless_expiry"
        elif state == "assigned_etf":
            shares = pos["shares"]; pnl += shares * (float(m.spot_open) - pos["mark"]); cost += shares * float(m.spot_open) * ONE_WAY; fq = futures.loc[(str(b.contract), day)]; units = shares * float(m.spot_open) / (float(fq.open) * real_source.IC_MULTIPLIER); cost += units * real_source.IC_MULTIPLIER * float(fq.open) * ONE_WAY; pnl += units * real_source.IC_MULTIPLIER * (float(fq.settle)-float(fq.open)); pos={"contract":str(b.contract),"units":units,"mark":float(fq.settle)};state="ic_future";cycle["ic_conversion_date"]=str(day.date());action="assignment_sell_proxy_etf_buy_ic_next_open"
        elif state == "ic_future":
            fq=futures.loc[(pos["contract"],day)]
            if pending_exit:
                pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.open)-pos["mark"]);cost += pos["units"]*real_source.IC_MULTIPLIER*float(fq.open)*ONE_WAY;cycle["realized_pnl"] += pnl-cost;cycle.update(exit_date=str(day.date()),exit_reason="recovery_exit_ic_next_open",closed=True,recovery_days=int((day-pd.Timestamp(cycle["ic_conversion_date"])).days));cycles.append(cycle.copy());cycle=pos=None;state="idle";pending_exit=False;action="recovery_exit_ic_next_open"
            elif pd.notna(b.roll_to) and str(b.roll_to)!=pos["contract"]:
                nq=futures.loc[(str(b.roll_to),day)];pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.close)-pos["mark"]);cost += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.close)+float(nq.close))*ONE_WAY;pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(nq.settle)-float(nq.close));pos.update(contract=str(b.roll_to),mark=float(nq.settle));action="ic_monthly_roll_close"
            else: pnl += pos["units"]*real_source.IC_MULTIPLIER*(float(fq.settle)-pos["mark"]);pos["mark"]=float(fq.settle)
        if state == "idle" and i > 0 and not action and bool(admission.get(day,False)):
            month=day.to_period("M").to_timestamp()+pd.offsets.MonthBegin(1);ex=expiry(month);strike=float(market.iloc[i-1].spot_close)*.95;op=price(m,strike,ex,"open");pos={"units":previous/float(market.iloc[i-1].spot_close),"strike":strike,"expiry":ex,"contract_month":month,"mark":op,"entry_premium":op,"rolled":False,"roll_wait_reason":""};cycle={"candidate":label,"entry_date":str(day.date()),"closed":False,"realized_pnl":0.0,"early_rolls":0,"last_roll_date":""};cost += pos["units"]*float(market.iloc[i-1].spot_close)*ONE_WAY;state="short_put";action="sell_model_next_month_95_put_open"
        if cycle is not None: cycle["realized_pnl"] += pnl-cost
        cash_weight=1.0 if state=="idle" else .70;cash=previous*cash_weight*model_source.CASH;equity += pnl-cost+cash
        if not np.isfinite(equity) or equity<=0: raise RuntimeError(f"Invalid model NAV {day}")
        if state=="short_put" and threshold is not None and not pos["rolled"] and not pending_roll and not action and pos["mark"] <= pos["entry_premium"]*(1-threshold):pending_roll=True;pos["roll_wait_reason"]="";action=f"premium_decay_{int(threshold*100)}_signal_close"
        if state=="ic_future" and cycle["realized_pnl"] >= pos["units"]*real_source.IC_MULTIPLIER*pos["mark"]*ONE_WAY:pending_exit=True
        row={"date":day,"candidate":label,"return_net":equity/previous-1,"nav":equity,"pnl":pnl,"cost":cost,"cash":cash,"cash_weight":cash_weight,"state":state,"action":action,"pending_roll":pending_roll};rows.append(row)
        if action:events.append(row.copy())
    if cycle is not None: cycle.update(mark_date=str(dates[-1].date()),open_cycle_pnl=cycle["realized_pnl"]);cycles.append(cycle.copy())
    daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)
    if "open_cycle_pnl" not in cycles:cycles["open_cycle_pnl"]=np.nan
    closed=cycles.closed.fillna(False);ledger=abs((daily.pnl-daily.cost).sum()-cycles.loc[closed,"realized_pnl"].sum()-cycles.loc[~closed,"open_cycle_pnl"].fillna(0).sum())
    if ledger>1e-11:raise RuntimeError(f"Model ledger {ledger}")
    return daily,pd.DataFrame(events),cycles,{"rows":len(daily),"cycles":len(cycles),"assignments":int(cycles.physical_assignment_date.notna().sum()),"early_rolls":int(cycles.early_rolls.sum()),"ledger_max_abs_error":float(ledger)}


def summarize(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, wide = [], []
    for candidate, g in daily.groupby("candidate", sort=False):
        item = {"candidate": candidate}
        for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
            start = g.date.min() if years is None else g.date.max() - pd.DateOffset(years=years); ok = years is None or g.date.min() <= start; sub = g[g.date >= start] if ok else g.iloc[:0]; m = proxy.metrics(sub.return_net) if ok else {k: "N/A" for k in ("total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd")}; rows.append({"candidate": candidate, "segment": segment, "start": str(sub.date.min().date()) if ok else "", "end": str(sub.date.max().date()) if ok else "", "rows": len(sub), "available": ok, **m}); item.update({f"{k}_{segment}": v for k, v in m.items()})
        wide.append(item)
    return pd.DataFrame(rows), pd.DataFrame(wide)


def main() -> None:
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("immutable run already started")
    admission = permissions(); admission.to_csv(RUN / "admission_permissions.csv", index_label="date")
    daily_frames = []; events_frames = []; cycles_frames = []; audits = {}
    for threshold in THRESHOLDS:
        for runner in (run_real, run_model):
            d, e, c, a = runner(admission, threshold); daily_frames.append(d); events_frames.append(e); cycles_frames.append(c); audits[d.candidate.iloc[0]] = a
    daily, events, cycles = pd.concat(daily_frames, ignore_index=True), pd.concat(events_frames, ignore_index=True), pd.concat(cycles_frames, ignore_index=True); summary, wide = summarize(daily)
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False); daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip"); events.to_csv(out / "events.csv", index=False); cycles.to_csv(out / "cycles.csv", index=False); summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig"); wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    roll = cycles.groupby("candidate").agg(cycles=("closed", "size"), completed=("closed", "sum"), early_rolls=("early_rolls", "sum")).reset_index(); roll.to_csv(RUN / "roll_diagnostics.csv", index=False, encoding="utf-8-sig")
    meta.update(phase="complete", scan_type="premium_decay_one_step_early_roll", baseline={"real": "real_hold_to_expiry", "model": "model_hold_to_expiry"}, candidate_grid=[{"premium_decay": x} for x in THRESHOLDS], data_snapshot={"real_510500_put": "2022-09-19 to 2026-08-14", "model": "2015-04-16 to 2026-08-14; Black-Scholes 510500/CSI500 proxy with historical IC futures"}, cost_model={"one_way_notional": ONE_WAY, "early_roll_two_sides": 2 * ONE_WAY, "cash_annual": 0.03, "risk_reserve": 0.30}, admission="previous-close IC score <1.95 (0/1 tier) AND original IC v1.3 momentum_execution_weight >0; MOM120 excluded", audit=audits, outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "events": str(out / "events.csv"), "cycles": str(out / "cycles.csv"), "roll_diagnostics": str(RUN / "roll_diagnostics.csv")}, source_hashes={str(p): sha(p) for p in (Path(__file__), real_source.ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv", native.CSI500_OHLCV_PATH)}, decision="research_only_no_promotion", stability_label="real_short_sample_model_proxy_no_oos", git_status_after=subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip())
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC卖95% Put：权利金衰减提前换月\n\n研究专用：真实510500 Put与理论延展严格分层，均不构成生产或订单变更。\n\n## 准入\n\n前收盘估值风险分数<1.95（0/1档）且原IC 1.3 `momentum_execution_weight>0`；不使用MOM120。已有Put、实物交割ETF→IC转换、IC月展期和回本退出保持原状态机。提前换月T收盘触发、次开盘双边执行，仅允许一次；新腿须重新通过同一准入。\n\n## 结果\n\n" + summary.to_markdown(index=False) + "\n\n## 提前换月诊断\n\n" + roll.to_markdown(index=False) + "\n\n## 状态\n\nresearch_only_no_promotion\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as f: f.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(summary[summary.segment.eq("full")].to_string(index=False)); print(roll.to_string(index=False))


if __name__ == "__main__": main()
