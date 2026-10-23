"""Uniform-model extension of the IC 95% short Put assignment recovery test.

The option and assigned ETF are CSI500-price-index proxies.  IC futures quotes
remain the historical CFFEX series from the frozen IC daily input.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_proxy_validation_v1 as proxy
import ic_roll_momentum_stage2_put_v2 as ic_put

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quant_param_scan_runs" / (
    "20260914_ic_im_ic_short_95_put_etf_to_ic_recovery_model_extension_"
    "ic_short_put_physical_assignment_recovery_post_assignment_recovery_instrument"
)
START, END = proxy.MODEL_START, proxy.END
ONE_WAY, FUT_MULT, CASH = 0.0001, 200.0, proxy.CASH_DAILY


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expiry(month: pd.Timestamp, dates: pd.DatetimeIndex) -> pd.Timestamp:
    return proxy.fourth_wednesday(month, dates)


def model_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frames, _, _, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    valuation, _ = proxy.build_daily_valuation()
    market, checks = proxy.prepare_model_market(
        frames["ic"], valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    active = frames["ic"].query("date >= @START and date <= @END").copy().reset_index(drop=True)
    market = market.set_index("date").loc[active.date].reset_index()
    if market[["spot_open", "spot_close", "sigma_open", "sigma_close", "rate_open", "rate_close", "dividend_open", "dividend_close"]].isna().any().any():
        raise RuntimeError("Incomplete uniform model market")
    raw = pd.read_csv(ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv", parse_dates=["date"])
    futures = raw.set_index(["contract", "date"])
    for row in active.itertuples(index=False):
        if (str(row.contract), pd.Timestamp(row.date)) not in futures.index:
            raise RuntimeError(f"Missing active IC quote: {row.contract} {row.date}")
    return active, market, futures


def run(candidate: str, recovery: str, active: pd.DataFrame, market: pd.DataFrame, futures: pd.DataFrame, fixed_exits: dict[str, pd.Timestamp] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if recovery not in {"etf_proxy", "ic_future"}:
        raise ValueError(recovery)
    dates = pd.DatetimeIndex(active.date)
    equity, state, pos, pending, cycle = 1.0, "idle", None, False, None
    rows, events, cycles = [], [], []
    for i, b in active.iterrows():
        day = pd.Timestamp(b.date)
        m = market.iloc[i]
        prior_equity, pnl, cost, action = equity, 0.0, 0.0, ""
        if state == "short_put":
            years = max((pos["expiry"] - day).days / 365.0, 0.0)
            mark = proxy.bs_put(float(m.spot_close), pos["strike"], float(m.rate_close), float(m.dividend_close), float(m.sigma_close), years)
            pnl += pos["units"] * (pos["mark"] - mark)
            pos["mark"] = mark
            if day == pos["expiry"]:
                intrinsic = max(pos["strike"] - float(m.spot_close), 0.0)
                pnl += pos["units"] * (mark - intrinsic)
                if intrinsic <= 0:
                    cycle["realized_pnl"] += pnl - cost
                    cycle.update(closed=True, exit_date=str(day.date()), exit_reason="worthless_expiry")
                    cycles.append(cycle.copy())
                    state, pos, cycle, action = "idle", None, None, "worthless_expiry"
                else:
                    pos = {"shares": pos["units"], "mark": float(m.spot_close)}
                    state, action = "assigned_etf", "physical_assignment"
                    cycle["assignment_date"] = str(day.date())
        elif state == "assigned_etf":
            if recovery == "ic_future":
                shares = pos["shares"]
                pnl += shares * (float(m.spot_open) - pos["mark"])
                cost += shares * float(m.spot_open) * ONE_WAY
                quote = futures.loc[(str(b.contract), day)]
                units = shares * float(m.spot_open) / (float(quote.open) * FUT_MULT)
                cost += units * FUT_MULT * float(quote.open) * ONE_WAY
                pnl += units * FUT_MULT * (float(quote.settle) - float(quote.open))
                pos = {"contract": str(b.contract), "units": units, "mark": float(quote.settle)}
                state, action = "ic_future", "assignment_sell_etf_buy_ic_next_open"
                cycle.update(conversion_date=str(day.date()), ic_units=units)
            else:
                pnl += pos["shares"] * (float(m.spot_close) - pos["mark"])
                pos["mark"] = float(m.spot_close)
                state, action = "etf_proxy", "assignment_hold_etf_proxy"
        elif state == "etf_proxy":
            locked_exit = fixed_exits is not None and fixed_exits.get(cycle["entry_date"]) == day
            if pending or locked_exit:
                pnl += pos["shares"] * (float(m.spot_open) - pos["mark"])
                cost += pos["shares"] * float(m.spot_open) * ONE_WAY
                cycle["realized_pnl"] += pnl - cost
                cycle.update(closed=True, exit_date=str(day.date()), exit_reason="locked_ic_exit_etf_next_open" if locked_exit else "recovery_exit_etf_next_open", recovery_days=int((day - pd.Timestamp(cycle["assignment_date"])).days))
                cycles.append(cycle.copy())
                state, pos, cycle, pending, action = "idle", None, None, False, "recovery_exit_etf_next_open"
            else:
                pnl += pos["shares"] * (float(m.spot_close) - pos["mark"])
                pos["mark"] = float(m.spot_close)
        elif state == "ic_future":
            quote = futures.loc[(pos["contract"], day)]
            if pending:
                pnl += pos["units"] * FUT_MULT * (float(quote.open) - pos["mark"])
                cost += pos["units"] * FUT_MULT * float(quote.open) * ONE_WAY
                cycle["realized_pnl"] += pnl - cost
                cycle.update(closed=True, exit_date=str(day.date()), exit_reason="recovery_exit_ic_next_open", recovery_days=int((day - pd.Timestamp(cycle["conversion_date"])).days))
                cycles.append(cycle.copy())
                state, pos, cycle, pending, action = "idle", None, None, False, "recovery_exit_ic_next_open"
            elif pd.notna(b.roll_to) and str(b.roll_to) != pos["contract"]:
                nxt = futures.loc[(str(b.roll_to), day)]
                pnl += pos["units"] * FUT_MULT * (float(quote.close) - pos["mark"])
                cost += pos["units"] * FUT_MULT * (float(quote.close) + float(nxt.close)) * ONE_WAY
                pnl += pos["units"] * FUT_MULT * (float(nxt.settle) - float(nxt.close))
                pos.update(contract=str(b.roll_to), mark=float(nxt.settle))
                action = "ic_monthly_roll_close"
            else:
                pnl += pos["units"] * FUT_MULT * (float(quote.settle) - pos["mark"])
                pos["mark"] = float(quote.settle)
        if state == "idle" and i > 0 and not action:
            previous_spot = float(market.iloc[i - 1].spot_close)
            month = day.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
            ex = expiry(month, dates)
            if ex <= day:
                raise RuntimeError("Model expiry is not forward")
            strike = previous_spot * 0.95
            years = (ex - day).days / 365.0
            premium = proxy.bs_put(float(m.spot_open), strike, float(m.rate_open), float(m.dividend_open), float(m.sigma_open), years)
            units = prior_equity / previous_spot
            pos = {"units": units, "strike": strike, "expiry": ex, "mark": premium}
            cycle = {"candidate": candidate, "entry_date": str(day.date()), "expiry": str(ex.date()), "strike": strike, "closed": False, "realized_pnl": 0.0}
            cost += units * previous_spot * ONE_WAY
            state, action = "short_put", "sell_model_next_month_95_put_open"
        if cycle is not None:
            cycle["realized_pnl"] += pnl - cost
        cash_weight = 1.0 if state == "idle" else 0.70
        cash = prior_equity * cash_weight * CASH
        equity += pnl - cost + cash
        if equity <= 0 or not np.isfinite(equity):
            raise RuntimeError(f"Invalid NAV {day}")
        if state == "etf_proxy" and cycle is not None and fixed_exits is None:
            if cycle["realized_pnl"] >= pos["shares"] * pos["mark"] * ONE_WAY:
                pending = True
        if state == "ic_future" and cycle is not None:
            if cycle["realized_pnl"] >= pos["units"] * FUT_MULT * pos["mark"] * ONE_WAY:
                pending = True
        row = {"date": day, "candidate": candidate, "return_net": equity / prior_equity - 1.0, "nav": equity, "pnl": pnl, "cost": cost, "cash": cash, "cash_weight": cash_weight, "state": state, "action": action}
        rows.append(row)
        if action:
            events.append(row.copy())
    if cycle is not None:
        cycle.update(mark_date=str(dates[-1].date()), open_cycle_pnl=cycle["realized_pnl"])
        cycles.append(cycle.copy())
    daily, cycle_frame = pd.DataFrame(rows), pd.DataFrame(cycles)
    closed = cycle_frame.closed.fillna(False)
    cycle_total = cycle_frame.loc[closed, "realized_pnl"].sum() + cycle_frame.loc[~closed, "open_cycle_pnl"].sum()
    ledger_error = abs(float((daily.pnl - daily.cost).sum() - cycle_total))
    if ledger_error > 1e-12:
        raise RuntimeError(f"Ledger error {ledger_error}")
    audit = {"rows": len(daily), "cycles": len(cycle_frame), "assignments": int(cycle_frame.assignment_date.notna().sum()), "closed_cycles": int(closed.sum()), "ic_days": int(daily.state.eq("ic_future").sum()), "etf_proxy_days": int(daily.state.eq("etf_proxy").sum()), "ledger_max_abs_error": ledger_error, "open_state": state}
    return daily, pd.DataFrame(events), cycle_frame, audit


def window_rows(candidate: str, daily: pd.DataFrame) -> list[dict]:
    rows = []
    for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
        start = daily.date.min() if years is None else daily.date.max() - pd.DateOffset(years=years)
        sub = daily[daily.date >= start]
        rows.append({"candidate": candidate, "recovery_instrument": "IC" if candidate.endswith("ic") else "ETF_price_proxy", "segment": segment, "start": str(sub.date.min().date()), "end": str(sub.date.max().date()), "rows": len(sub), **proxy.metrics(sub.return_net), "holding_day_ratio": float(sub.state.isin(["ic_future", "etf_proxy"]).mean()), "cost_total": float(sub.cost.sum())})
    return rows


def main() -> None:
    active, market, futures = model_inputs()
    outputs, audits = [], {}
    daily, events, cycles, audit = run("post_assignment_ic", "ic_future", active, market, futures)
    outputs.append(("post_assignment_ic", daily)); audits["post_assignment_ic"] = audit
    daily.to_csv(OUT / "post_assignment_ic_daily.csv.gz", index=False, compression="gzip")
    events.to_csv(OUT / "post_assignment_ic_events.csv", index=False)
    cycles.to_csv(OUT / "post_assignment_ic_cycles.csv", index=False)
    fixed_exits = {str(row.entry_date): pd.Timestamp(row.exit_date) for row in cycles.itertuples(index=False) if pd.notna(row.assignment_date) and pd.notna(row.exit_date)}
    for candidate, recovery, locks in (("post_assignment_etf", "etf_proxy", None), ("post_assignment_etf_locked_ic_exit", "etf_proxy", fixed_exits)):
        daily, events, cycles, audit = run(candidate, recovery, active, market, futures, locks)
        daily.to_csv(OUT / f"{candidate}_daily.csv.gz", index=False, compression="gzip")
        events.to_csv(OUT / f"{candidate}_events.csv", index=False)
        cycles.to_csv(OUT / f"{candidate}_cycles.csv", index=False)
        outputs.append((candidate, daily)); audits[candidate] = audit
    summary = pd.DataFrame([item for candidate, daily in outputs for item in window_rows(candidate, daily)])
    summary.to_csv(OUT / "scan_summary.csv", index=False)
    wide = summary.pivot(index=["candidate", "recovery_instrument"], columns="segment", values=["ann_return", "max_dd", "ann_vol", "sharpe_repo", "holding_day_ratio", "cost_total"])
    wide.columns = [f"{metric}_{segment}" for metric, segment in wide.columns]
    wide.reset_index().to_csv(OUT / "window_metrics.csv", index=False)
    meta_path = OUT / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({"scan_type": "paired_model_extension", "baseline": {"candidate": "post_assignment_etf", "definition": "same model 95% short Put and physical ETF-price-proxy recovery"}, "candidate_grid": ["post_assignment_etf", "post_assignment_ic", "post_assignment_etf_locked_ic_exit"], "fixed_exit_counterfactual": "The locked ETF path exits each assigned cycle on the IC path's next-open exit date. It isolates the recovery-instrument return over matching cycle boundaries, but is not a self-financing recovery rule.", "data_snapshot": {"start": str(START.date()), "end": str(END.date()), "rows": len(active), "futures": "historical CFFEX IC OHLC/settlement", "option": "Black-Scholes theoretical 510500 proxy based on CSI500 price index"}, "cost_model": {"one_way_notional": ONE_WAY, "cash_annual": 0.03, "risk_reserve": 0.30}, "audit": audits, "source_hashes": {"script": digest(Path(__file__)), "proxy": digest(Path(proxy.__file__)), "real_IC_recovery_script": digest(ROOT / "research_ic_short95_put_to_ic_recovery_v1.py")}, "limitations": "The 2015-2022 Put and ETF assignment are a theoretical CSI500-price-index proxy. 510500 ETF tracking, actual option strike availability, liquidity, bid-ask, exercise settlement, taxes, margin and liquidation are not modeled. Model volatility is QVIX50 scaled by realized-vol ratio. The IC leg uses historical CFFEX quotes, but neither candidate is investable historical evidence.", "decision": "research_only_model_extension_no_promotion", "stability_label": "model_proxy_sensitive_no_independent_oos"})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC卖95% Put交割后ETF或IC回本：统一模型扩展\n\n## Data Snapshot\n\n"
    record += "模型期2015-04-16至2026-08-14，IC是实际历史期货报价；Put、ETF价格及可成交性为理论代理，不与2022年后的真实510500 Put回测拼接。\n\n## Method\n\n两候选使用相同的理论95% Put、前收盘选行权价/当日开盘卖出、到期日现货收盘内在价值、1bp单边成本、30%风险缓冲和3%现金。ETF候选以中证500价格指数作为510500实物交割后的价格代理；IC候选下一开盘卖代理ETF并按等名义买入历史CFFEX活跃IC，按历史月度链滚动，均在本轮含期权损益覆盖退出成本后下一开盘退出。\n\n"
    record += "另设ETF锁定退出反事实：每笔交割ETF按IC路径同一退出日于下一开盘退出，用于隔离相同持有边界的工具收益；它不代表ETF自身可以回本，因此不与自洽策略混同。\n\n## Results\n\n" + summary.to_string(index=False) + "\n\n## Audit\n\n" + json.dumps(audits, ensure_ascii=False, indent=2) + "\n\n## Decision\n\nDecision: research_only_model_extension_no_promotion\nStability: model_proxy_sensitive_no_independent_oos\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    with (OUT / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python -X utf8 research_ic_short95_put_to_ic_recovery_full_model_v1.py\n")
    print(summary.to_string(index=False))
    print(json.dumps(audits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
