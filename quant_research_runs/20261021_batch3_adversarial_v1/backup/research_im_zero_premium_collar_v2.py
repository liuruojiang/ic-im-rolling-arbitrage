from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_zero_premium_collar_v1 as v1


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "docs/im_zero_premium_collar_v2_spec.md"
OUTPUT = ROOT / "quant_param_scan_runs/20260925_causal_multi_tier"
CAPS = {"gap_05bp": 0.0005, "gap_10bp": 0.0010, "gap_20bp": 0.0020, "gap_30bp": 0.0030, "gap_no_gate": float("inf")}
COMPONENTS = ("matched_im", "matched_put", "matched_call", "matched_collar")
ALL_IM = "all_im"
FUTURE_SIDE_COST = 0.0001
OPTION_SIDE_COST = 0.0005
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0


def base_cycle(data: dict[str, pd.DataFrame], signal: pd.Timestamp, entry: pd.Timestamp, expiry: pd.Timestamp, fcode: str) -> dict[str, object]:
    fsignal, fentry = v1.quote(data["im"], signal, fcode), v1.quote(data["im"], entry, fcode)
    if not v1.liquid(fsignal) or fentry is None or not np.isfinite(fentry["open"]) or fentry["open"] <= 0:
        raise RuntimeError(f"Missing liquid IM signal/open for {fcode} {signal.date()}")
    spot_signal = float(data["spot"].loc[signal, "close"])
    spot_entry_close = float(data["spot"].loc[entry, "close"])
    result: dict[str, object] = dict(future_contract=fcode, signal_date=signal, entry_date=entry, expiry_date=expiry,
                                     spot_signal=spot_signal, spot_entry_close=spot_entry_close,
                                     future_signal_close=float(fsignal["close"]), future_entry_open=float(fentry["open"]),
                                     signal_discount_points=spot_signal-float(fsignal["close"]),
                                     entry_close_spot_minus_future_open=spot_entry_close-float(fentry["open"]))
    month = fcode[2:]
    puts = data["put"].loc[signal].reset_index(drop=True)
    puts = puts[puts["contract"].str.startswith(f"MO{month}-P-") & puts["strike"].lt(spot_signal)
                & puts["close"].gt(0) & puts["volume"].gt(0) & puts["open_interest"].gt(0)].copy()
    calls = data["call"].loc[signal].reset_index(drop=True)
    calls = calls[calls["contract"].str.startswith(f"MO{month}-C-") & calls["strike"].gt(spot_signal)
                  & calls["close"].gt(0) & calls["volume"].gt(0) & calls["open_interest"].gt(0)].copy()
    if puts.empty or calls.empty:
        result["signal_status"] = "missing_liquid_otm_signal_pair"
        return result
    puts["error"] = (puts["strike"] - 0.95*spot_signal).abs()
    put = puts.sort_values(["error", "strike", "contract"], ascending=[True, False, True]).iloc[0]
    calls["error"] = (calls["close"] - float(put["close"])).abs()
    call = calls.sort_values(["error", "strike", "contract"], ascending=[True, False, True]).iloc[0]
    pcode, ccode = str(put["contract"]), str(call["contract"])
    pentry, centry = v1.quote(data["put"], entry, pcode), v1.quote(data["call"], entry, ccode)
    if pentry is None or centry is None or not np.isfinite(pentry["open"]) or not np.isfinite(centry["open"]) or pentry["open"] <= 0 or centry["open"] <= 0:
        raise RuntimeError(f"Missing MO preselected opening quote {entry.date()} {pcode} {ccode}")
    signal_gap = (float(call["close"])-float(put["close"]))/float(fsignal["close"])
    entry_gap = (float(centry["open"])-float(pentry["open"]))/float(fentry["open"])
    result.update(signal_status="ready", put_contract=pcode, call_contract=ccode, put_strike=float(put["strike"]),
                  call_strike=float(call["strike"]), put_signal_close=float(put["close"]), call_signal_close=float(call["close"]),
                  put_entry_open=float(pentry["open"]), call_entry_open=float(centry["open"]),
                  put_entry_volume=int(pentry["volume"]), call_entry_volume=int(centry["volume"]),
                  put_entry_open_interest=int(pentry["open_interest"]), call_entry_open_interest=int(centry["open_interest"]),
                  signal_premium_gap_fraction=signal_gap, entry_premium_gap_fraction=entry_gap,
                  entry_net_option_cash_after_entry_cost_fraction=entry_gap-2*OPTION_SIDE_COST,
                  both_otm_at_entry_close=bool(float(put["strike"]) < spot_entry_close < float(call["strike"])))
    return result


def check_daily_marks(data: dict[str, pd.DataFrame], cycle: dict[str, object], days: pd.DatetimeIndex, with_options: bool) -> None:
    codes = [("im", str(cycle["future_contract"]))]
    if with_options:
        codes += [("put", str(cycle["put_contract"])), ("call", str(cycle["call_contract"]))]
    for day in days:
        for name, code in codes:
            row = v1.quote(data[name], day, code)
            if row is None or not np.isfinite(row["settle"]) or row["settle"] < 0 or (name == "im" and row["settle"] <= 0):
                raise RuntimeError(f"Missing/invalid future observation: {name} {code} {day.date()}")


def cycle_daily(data: dict[str, pd.DataFrame], cycle: dict[str, object], days: pd.DatetimeIndex, candidate: str, has_put: bool, has_call: bool) -> list[dict[str, object]]:
    entry, expiry = pd.Timestamp(cycle["entry_date"]), pd.Timestamp(cycle["expiry_date"])
    holding = days[(days >= entry) & (days <= expiry)]
    check_daily_marks(data, cycle, holding, has_put or has_call)
    fcode = str(cycle["future_contract"])
    pcode = str(cycle.get("put_contract", ""))
    ccode = str(cycle.get("call_contract", ""))
    fprev = float(cycle["future_entry_open"])
    pprev = float(cycle["put_entry_open"]) if has_put else 0.0
    cprev = float(cycle["call_entry_open"]) if has_call else 0.0
    notional = 200.0*fprev
    margin_deposit = 0.30*notional
    put_premium = 200.0*pprev
    call_premium = 200.0*cprev
    cash = 0.70*notional-put_premium+call_premium
    equity_prev = notional
    prior_spot = float(cycle["spot_signal"])
    rows: list[dict[str, object]] = []
    for day in holding:
        is_entry, is_expiry = day == entry, day == expiry
        future_cost = notional*FUTURE_SIDE_COST*(int(is_entry)+int(is_expiry))
        put_cost = notional*OPTION_SIDE_COST*(int(is_entry)+int(is_expiry)) if has_put else 0.0
        call_cost = notional*OPTION_SIDE_COST*(int(is_entry)+int(is_expiry)) if has_call else 0.0
        cost = future_cost+put_cost+call_cost
        if is_entry:
            cash -= cost
        margin_start = v1.call_margin(cprev, prior_spot, float(cycle["call_strike"])) if has_call else 0.0
        free_cash_start = cash-margin_start
        interest = free_cash_start*CASH_DAILY
        fmark = float(v1.quote(data["im"], day, fcode)["settle"])
        pmark = float(v1.quote(data["put"], day, pcode)["settle"]) if has_put else 0.0
        cmark = float(v1.quote(data["call"], day, ccode)["settle"]) if has_call else 0.0
        future_pnl = 200.0*(fmark-fprev)
        put_pnl = 200.0*(pmark-pprev) if has_put else 0.0
        call_pnl = -200.0*(cmark-cprev) if has_call else 0.0
        cash += interest+future_pnl
        if is_expiry:
            cash += 200.0*pmark if has_put else 0.0
            cash -= 200.0*cmark if has_call else 0.0
            if not is_entry:
                cash -= cost
            put_asset, call_liability, margin_end = 0.0, 0.0, 0.0
        else:
            put_asset = 200.0*pmark if has_put else 0.0
            call_liability = 200.0*cmark if has_call else 0.0
            margin_end = v1.call_margin(cmark, float(data["spot"].loc[day, "close"]), float(cycle["call_strike"])) if has_call else 0.0
        equity = margin_deposit+cash+put_asset-call_liability
        pnl = future_pnl+put_pnl+call_pnl+interest-cost
        if not np.isclose(equity-equity_prev, pnl, atol=1e-6):
            raise RuntimeError(f"Account P&L mismatch {candidate} {day.date()} {equity-equity_prev} {pnl}")
        day_return = equity/equity_prev-1.0
        rows.append(dict(date=day, candidate=candidate, active=True, future_contract=fcode,
                         put_contract=pcode if has_put else "", call_contract=ccode if has_call else "",
                         future_pnl=future_pnl, put_pnl=put_pnl, call_pnl=call_pnl,
                         cash_interest=interest, future_cost=future_cost, put_cost=put_cost, call_cost=call_cost,
                         total_pnl=pnl, cash_balance=cash, put_asset=put_asset, call_liability=call_liability,
                         call_margin_start=margin_start, call_margin_end=margin_end,
                         free_cash_start=free_cash_start, free_cash_end=cash-margin_end,
                         notional=notional, equity=equity, return_net=day_return, cycle_cum_return=equity/notional-1.0))
        fprev, pprev, cprev, prior_spot, equity_prev = fmark, pmark, cmark, float(data["spot"].loc[day, "close"]), equity
    return rows


def stats(frame: pd.DataFrame) -> dict[str, float]:
    returns = frame["return_net"].to_numpy(float)
    nav = np.cumprod(1.0+returns)
    peaks = np.maximum.accumulate(np.r_[1.0, nav])[1:]
    vol = float(np.std(returns, ddof=1)*math.sqrt(252.0))
    return dict(ann_return=float(nav[-1]**(252.0/len(nav))-1.0), ann_vol=vol,
                sharpe_repo=float(np.mean(returns)*math.sqrt(252.0)/np.std(returns, ddof=1)) if vol>0 else 0.0,
                max_dd=float(np.min(nav/peaks-1.0)))


def main() -> None:
    if (OUTPUT/"scan_summary.csv").exists():
        raise FileExistsError("v2 scan already generated")
    data, schedule = v1.load()
    source_hashes = {name:v1.sha256(path) for name,path in v1.SOURCES.items()}
    dates = pd.DatetimeIndex(sorted(data["spot"].index))
    if not dates.equals(pd.DatetimeIndex(sorted(set(data["im"].index.get_level_values("date"))))):
        raise RuntimeError("IM and spot date mismatch")
    completed = schedule[schedule["complete"].eq(True)].sort_values("exit_date")
    dates = dates[dates <= pd.Timestamp(completed["exit_date"].max())]
    bases: list[dict[str, object]] = []
    prior_expiry: pd.Timestamp | None = None
    for item in completed.itertuples(index=False):
        signal = dates[0] if prior_expiry is None else prior_expiry
        future_dates = dates[dates > signal]
        entry, expiry = pd.Timestamp(future_dates[0]), pd.Timestamp(item.exit_date)
        if entry >= expiry:
            raise RuntimeError(f"No holding interval for {item.contract}")
        bases.append(base_cycle(data, signal, entry, expiry, str(item.contract)))
        prior_expiry = expiry
    if len(bases) != 48:
        raise RuntimeError(f"Expected 48 complete IM monthly cycles, got {len(bases)}")

    active: dict[tuple[pd.Timestamp,str],dict[str,object]] = {}
    cycle_records: list[dict[str,object]] = []
    for cycle in bases:
        for row in cycle_daily(data, cycle, dates, ALL_IM, False, False):
            key = (pd.Timestamp(row["date"]), ALL_IM)
            if key in active:
                raise RuntimeError("Overlapping all_im cycles")
            active[key] = row
        for tier, cap in CAPS.items():
            basis_pass = float(cycle["signal_discount_points"]) > 0
            signal_gap = float(cycle.get("signal_premium_gap_fraction", np.nan))
            eligible = cycle["signal_status"] == "ready" and basis_pass and abs(signal_gap) <= cap
            reason = "selected" if eligible else ("no_discount_signal" if not basis_pass else
                      cycle["signal_status"] if cycle["signal_status"] != "ready" else "signal_premium_gap_over_cap")
            detail = dict(cycle)
            detail.update(tier=tier, cap_fraction=cap if np.isfinite(cap) else "none", status=reason)
            cycle_records.append(detail)
            if not eligible:
                continue
            for component in COMPONENTS:
                name = f"{tier}__{component}"
                for row in cycle_daily(data, cycle, dates, name, component in ("matched_put","matched_collar"), component in ("matched_call","matched_collar")):
                    key = (pd.Timestamp(row["date"]), name)
                    if key in active:
                        raise RuntimeError(f"Overlapping cycle {key}")
                    active[key] = row

    candidates = [ALL_IM] + [f"{tier}__{component}" for tier in CAPS for component in COMPONENTS]
    navs = {name:1.0 for name in candidates}
    daily_records: list[dict[str,object]] = []
    for day in dates:
        for name in candidates:
            row = active.get((pd.Timestamp(day), name))
            if row is None:
                row = dict(date=day, candidate=name, active=False, future_contract="", put_contract="", call_contract="",
                           future_pnl=0.0, put_pnl=0.0, call_pnl=0.0, cash_interest=CASH_DAILY,
                           future_cost=0.0, put_cost=0.0, call_cost=0.0, total_pnl=CASH_DAILY,
                           cash_balance=1.0, put_asset=0.0, call_liability=0.0,
                           call_margin_start=0.0, call_margin_end=0.0, free_cash_start=1.0, free_cash_end=1.0,
                           notional=1.0, equity=1.0+CASH_DAILY, return_net=CASH_DAILY, cycle_cum_return=np.nan)
            navs[name] *= 1.0+float(row["return_net"])
            row["nav"] = navs[name]
            daily_records.append(row)
    daily = pd.DataFrame(daily_records)
    cycles = pd.DataFrame(cycle_records)
    summary_rows: list[dict[str,object]] = []
    wide_rows: list[dict[str,object]] = []
    unavailable: dict[str,dict[str,str]] = {}
    for name, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        wide: dict[str,object] = dict(candidate=name, signal_premium_gap_cap=name.split("__")[0] if name != ALL_IM else "none",
                                     holding_days=int(group["active"].sum()), holding_day_ratio=float(group["active"].mean()))
        for segment, years in (("full",None),("last_10y",10),("last_5y",5),("last_3y",3),("last_1y",1)):
            if years is not None and group["date"].min() > group["date"].max()-pd.DateOffset(years=years):
                reason=f"Real IM/MO full-cycle history begins {group['date'].min().date()}; fewer than {years} full years."
                unavailable.setdefault(name,{})[segment]=reason
                row=dict(candidate=name, segment=segment, start="N/A", end="N/A", rows=0,
                         ann_return="N/A", ann_vol="N/A", sharpe_repo="N/A", max_dd="N/A")
                wide[f"ann_return_{segment}"]=wide[f"max_dd_{segment}"]="N/A"
            else:
                part=group if years is None else group[group["date"] >= group["date"].max()-pd.DateOffset(years=years)]
                value=stats(part)
                row=dict(candidate=name, segment=segment, start=part["date"].min().date().isoformat(),
                         end=part["date"].max().date().isoformat(), rows=len(part), **value)
                wide[f"ann_return_{segment}"]=value["ann_return"]
                wide[f"max_dd_{segment}"]=value["max_dd"]
            summary_rows.append(row)
        wide_rows.append(wide)

    pd.DataFrame(summary_rows).to_csv(OUTPUT/"scan_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(wide_rows).to_csv(OUTPUT/"window_metrics.csv", index=False, encoding="utf-8-sig")
    cycles.to_csv(OUTPUT/"cycles.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(OUTPUT/"daily.csv.gz", index=False, compression="gzip")
    end_cycles=daily[daily["active"] & daily["cycle_cum_return"].notna()].groupby(["candidate","future_contract"],sort=False).tail(1)
    end_cycles[["candidate","future_contract","date","cycle_cum_return"]].to_csv(OUTPUT/"cycle_returns.csv", index=False, encoding="utf-8-sig")
    annual=daily.groupby(["candidate",daily["date"].dt.year]).apply(lambda g:pd.Series(stats(g)),include_groups=False).reset_index(names=["candidate","year"])
    annual.to_csv(OUTPUT/"annual_metrics.csv",index=False,encoding="utf-8-sig")

    meta_path=OUTPUT/"scan_meta.json"
    meta=json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(scan_type="harness_research", parameter_group="signal_premium_gap_cap",
                baseline={"all_im":"same monthly clock continuous IM", "matched_im":"tier-specific same exposure baseline", "v1_default_cap":0.001},
                candidate_grid=[{"tier":key,"cap_fraction":value if np.isfinite(value) else "none"} for key,value in CAPS.items()],
                data_snapshot={"source":"CFFEX official contract-level history; CSI 1000 official price index", "start":dates[0].date().isoformat(),"end":dates[-1].date().isoformat(),"source_hashes":source_hashes},
                cost_model={"future_one_way_fraction":FUTURE_SIDE_COST,"option_each_leg_one_way_fraction":OPTION_SIDE_COST,
                            "cash_annual":0.03,"future_margin_buffer":0.30,"fill":"official_open_settle_proxy_not_executable_fill"},
                unavailable_segments=unavailable, source_change_rule="research_only_no_source_change")
    meta["outputs"].update({"cycles":str(OUTPUT/"cycles.csv"),"daily":str(OUTPUT/"daily.csv.gz"),
                            "cycle_returns":str(OUTPUT/"cycle_returns.csv"),"annual_metrics":str(OUTPUT/"annual_metrics.csv")})
    meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    run=dict(status="research_diagnostic_pending_multi_agent_audit",execution="official_open_settle_proxy_not_executable_fill",
             source_hashes=source_hashes, v1_script_sha256=v1.sha256(ROOT/"research_im_zero_premium_collar_v1.py"),
             script_sha256=v1.sha256(Path(__file__)),spec_sha256=v1.sha256(SPEC),
             complete_cycles=len(bases), selected_by_tier={tier:int(cycles[(cycles.tier==tier)&(cycles.status=="selected")].shape[0]) for tier in CAPS},
             active_days_by_tier={tier:int(daily[daily.candidate==f"{tier}__matched_collar"]["active"].sum()) for tier in CAPS},
             start=dates[0].date().isoformat(),end=dates[-1].date().isoformat())
    (OUTPUT/"run.json").write_text(json.dumps(run,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"selected_by_tier":run["selected_by_tier"],"active_days_by_tier":run["active_days_by_tier"],
                      "full":pd.DataFrame(summary_rows).query("segment == 'full'")[["candidate","ann_return","ann_vol","max_dd"]].to_dict("records")},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
