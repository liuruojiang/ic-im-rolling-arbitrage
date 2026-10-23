"""Unfiltered IC-related short-Put research using physically settled 510500 ETF Puts.

This is the closest executable analogue to the IM cash-settled MO short-Put
study.  It deliberately does not pretend that ETF exercise becomes an IC
future: ITM exercise creates a 510500 ETF holding at the strike.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import ic_roll_momentum_stage2_put_v2 as ic_put


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quant_param_scan_runs" / "20260914_ic_short95_cash_secured_put_v5"
RATIO = 0.95
MULTIPLIER = 10_000.0
ONE_WAY = 0.0001
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
START = pd.Timestamp("2022-09-19")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fourth_wednesday(month: pd.Timestamp, dates: pd.DatetimeIndex) -> pd.Timestamp:
    calendar_days = pd.date_range(month.replace(day=1), month + pd.offsets.MonthEnd(0), freq="D")
    expiry = pd.Timestamp(calendar_days[calendar_days.weekday == 2][3])
    if expiry not in dates:
        raise RuntimeError(f"Calendar fourth Wednesday is not a trading session: {expiry.date()}")
    return expiry


def metrics(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window, years in (("full", None), ("10y", 10), ("5y", 5), ("3y", 3), ("1y", 1)):
        if years in (5, 10):
            rows.append({"window": window, "available": False, "reason": "real 510500 option history shorter than requested window", "rows": 0})
            continue
        start = frame.date.min() if years is None else frame.date.max() - pd.DateOffset(years=years)
        sample = frame[frame.date.ge(start)]
        nav = (1.0 + sample.return_net).cumprod()
        rows.append({"window": window, "available": True, "reason": "", "start": str(sample.date.min().date()), "end": str(sample.date.max().date()), "rows": len(sample), "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0), "ann_vol": float(sample.return_net.std(ddof=1) * np.sqrt(252.0)), "max_dd": float((nav / nav.cummax() - 1.0).min()), "total_return": float(nav.iloc[-1] - 1.0)})
    return pd.DataFrame(rows)


def run() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    frames, _, _, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    ic = frames["ic"].copy()
    dates = pd.DatetimeIndex(ic.date)
    etf = frames["etf500"].set_index("date")
    snapshots = frames["snapshots"].copy()
    histories = frames["histories"].copy()
    option_lookup = histories.set_index(["security_id", "date"])
    expiry_by_security = histories.groupby("security_id").date.max()
    daily_chain = {pd.Timestamp(day): group for day, group in snapshots.groupby("date", sort=False)}
    ic = ic[ic.date.ge(START)].reset_index(drop=True)
    if etf.reindex(ic.date)[["open", "close"]].isna().any().any():
        raise RuntimeError("Missing 510500 ETF open/close on an IC session")

    equity = 1.0
    state = "idle"  # idle, short_put, assigned_etf
    position: dict[str, object] | None = None
    cycle: dict[str, object] | None = None
    pending_exit = False
    rows: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    cycles: list[dict[str, object]] = []
    skipped = 0

    for i, row in ic.iterrows():
        day = pd.Timestamp(row.date)
        prev_equity = equity
        etf_open = float(etf.loc[day, "open"])
        etf_close = float(etf.loc[day, "close"])
        pnl = cost = 0.0
        action = ""

        if state == "short_put":
            assert position is not None and cycle is not None
            quote = option_lookup.loc[(position["security_id"], day)]
            if not (float(quote.close) > 0 and float(quote.volume) > 0):
                raise RuntimeError(f"Unmarkable active option {position['contract']} on {day.date()}")
            pnl += float(position["contracts"]) * MULTIPLIER * (float(position["mark"]) - float(quote.close))
            position["mark"] = float(quote.close)
            if day == position["expiry"]:
                intrinsic = max(float(position["strike"]) - etf_close, 0.0)
                # Replace the final close mark with physical exercise intrinsic value.
                pnl += float(position["contracts"]) * MULTIPLIER * (float(quote.close) - intrinsic)
                if intrinsic > 0:
                    position = {"shares": float(position["contracts"]) * MULTIPLIER, "mark": etf_close}
                    state = "assigned_etf"
                    cycle["assignment_date"] = str(day.date())
                    cycle["assignment_strike"] = float(cycle["strike"])
                    action = "physical_assignment_to_510500"
                else:
                    cycle["realized_pnl"] = float(cycle.get("realized_pnl", 0.0)) + pnl - cost
                    cycle["exit_date"] = str(day.date())
                    cycle["exit_reason"] = "worthless_expiry"
                    cycle["closed"] = True
                    cycles.append(cycle.copy())
                    cycle = None
                    position = None
                    state = "idle"
                    action = "worthless_expiry"

        elif state == "assigned_etf":
            assert position is not None and cycle is not None
            shares = float(position["shares"])
            if pending_exit:
                pnl += shares * (etf_open - float(position["mark"]))
                cost += shares * etf_open * ONE_WAY
                cycle["realized_pnl"] = float(cycle.get("realized_pnl", 0.0)) + pnl - cost
                cycle["exit_date"] = str(day.date())
                cycle["exit_reason"] = "recovery_exit_next_open"
                cycle["recovery_days"] = int((day - pd.Timestamp(cycle["assignment_date"])).days)
                cycle["closed"] = True
                cycles.append(cycle.copy())
                cycle = None
                position = None
                state = "idle"
                pending_exit = False
                action = "recovery_exit_next_open"
            else:
                pnl += shares * (etf_close - float(position["mark"]))
                position["mark"] = etf_close

        # Sell next-calendar-month 95% Put at the open, using only the prior ETF close.
        if state == "idle" and i > 0 and not action:
            prior_spot = float(etf.loc[pd.Timestamp(ic.loc[i - 1, "date"]), "close"])
            month = day.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
            chain = daily_chain.get(day, snapshots.iloc[:0])
            chain = chain[chain.contract_month.eq(month)].copy()
            if len(chain):
                chain["target_error"] = (chain.strike.astype(float) - prior_spot * RATIO).abs()
                selected = chain.sort_values(["target_error", "strike", "contract_id"]).iloc[0]
                quote = option_lookup.loc[(selected.security_id, day)]
                if float(quote.open) > 0 and float(quote.volume) > 0:
                    contracts = prev_equity / (prior_spot * MULTIPLIER)
                    # The stored quote history encodes exchange holiday adjustments
                    # (for example, lunar-new-year fourth-Wednesday expiries).
                    expiry = pd.Timestamp(expiry_by_security.loc[selected.security_id])
                    position = {"security_id": str(selected.security_id), "contract": str(selected.contract_id), "strike": float(selected.strike), "contracts": contracts, "mark": float(quote.open), "expiry": expiry}
                    cycle = {"entry_date": str(day.date()), "contract": str(selected.contract_id), "strike": float(selected.strike), "prior_etf_close": prior_spot, "entry_moneyness": float(selected.strike) / prior_spot, "contracts": contracts, "closed": False, "assignment_date": ""}
                    cost += contracts * MULTIPLIER * prior_spot * ONE_WAY
                    state = "short_put"
                    action = "sell_next_month_95_put_open"
                else:
                    skipped += 1
                    action = "skip_untradable_nearest_strike"
            else:
                skipped += 1
                action = "skip_missing_next_month_chain"

        if cycle is not None:
            cycle["realized_pnl"] = float(cycle.get("realized_pnl", 0.0)) + pnl - cost
        # Physical ETF assignment is cash-secured: strike cash is reserved while short,
        # and no cash is available while the assigned ETF is held.
        if state == "short_put":
            assert position is not None
            cash_weight = max(0.0, 1.0 - float(position["contracts"]) * MULTIPLIER * float(position["strike"]) / prev_equity)
        elif state == "assigned_etf":
            cash_weight = 0.0
        else:
            cash_weight = 1.0
        cash = prev_equity * cash_weight * CASH_DAILY
        equity += pnl - cost + cash
        if not np.isfinite(equity) or equity <= 0:
            raise RuntimeError(f"Non-positive equity on {day.date()}")
        if state == "assigned_etf" and cycle is not None:
            # Recovery test mirrors IM: all option/underlying P&L and costs, not cash interest.
            exit_cost = float(position["shares"]) * etf_close * ONE_WAY
            if float(cycle["realized_pnl"]) >= exit_cost:
                pending_exit = True
        record = {"date": day, "candidate": "ic_cash_secured_short95", "return_net": equity / prev_equity - 1.0, "nav": equity, "pnl": pnl, "cost": cost, "cash": cash, "cash_weight": cash_weight, "state": state, "action": action, "put_contract": "" if state != "short_put" else str(position["contract"]), "etf_shares": 0.0 if state != "assigned_etf" else float(position["shares"])}
        rows.append(record)
        if action:
            events.append(record.copy())

    if cycle is not None:
        cycle["mark_date"] = str(pd.Timestamp(ic.date.iloc[-1]).date())
        cycle["open_cycle_pnl"] = float(cycle.get("realized_pnl", 0.0))
        cycles.append(cycle.copy())
    daily = pd.DataFrame(rows)
    cycles_frame = pd.DataFrame(cycles)
    ledger_total = float(daily.pnl.sub(daily.cost).sum())
    cycle_total = float(
        cycles_frame.loc[cycles_frame.closed.fillna(False), "realized_pnl"].fillna(0.0).sum()
        + cycles_frame.loc[~cycles_frame.closed.fillna(False), "open_cycle_pnl"].fillna(0.0).sum()
    )
    ledger_error = abs(ledger_total - cycle_total)
    if ledger_error > 1e-12:
        raise RuntimeError(f"Cycle ledger does not reconcile: {ledger_error}")
    audit = {"input_rows": len(ic), "entry_skips": skipped, "events": len(events), "cycles": len(cycles_frame), "closed_cycles": int(cycles_frame.closed.fillna(False).sum()), "cycle_ledger_max_abs_error": ledger_error, "min_cash_weight": float(daily.cash_weight.min()), "min_nav": float(daily.nav.min()), "max_single_day_loss": float(daily.return_net.min()), "open_position_at_end": bool(state != "idle"), "state_at_end": state}
    return daily, pd.DataFrame(events), cycles_frame, audit


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"Immutable research output exists: {OUT}")
    OUT.mkdir(parents=True)
    daily, events, cycles, audit = run()
    window = metrics(daily)
    daily.to_csv(OUT / "daily.csv.gz", index=False, compression="gzip")
    events.to_csv(OUT / "events.csv", index=False)
    cycles.to_csv(OUT / "cycles.csv", index=False)
    window.to_csv(OUT / "window_metrics.csv", index=False)
    meta = {"status": "research_only_no_production_or_trade_change", "strategy": "unfiltered next-month 95% cash-secured 510500 ETF short Put; physical assignment then ETF recovery exit", "comparison_to_IM": "economic analogue only: IM MO Put is cash-settled and converts to IM futures; 510500 ETF Put is physically settled, so this test holds ETF after exercise rather than synthesizing an IC future", "parameters": {"moneyness": RATIO, "filters": "none", "entry": "prior ETF close selection, next session option open", "cost_per_side": ONE_WAY, "cash_yield": 0.03}, "data_cutoff": str(daily.date.max().date()), "source_hashes": {"script": sha256(Path(__file__)), "ic_put_engine": sha256(Path(ic_put.__file__))}, "audit": audit, "limitations": "Historical open/close proxy, no bid-ask/impact, no option or ETF financing/borrow tax treatment, no capacity, and no independent OOS. It is not a directly executable IC-futures short-Put strategy because the available 510500 ETF Put physically settles."}
    (OUT / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC 相关 510500 ETF 卖 95% Put：无过滤基线\n\n"
    record += "状态：研究测试，未批准实盘；不修改 IC 买 Put 主线、v1.3-r7 信号或任何账本。\n\n"
    record += "这不是把 ETF Put 伪装成 IC 指数 Put：510500 Put 为实物交割。到期实值后，策略按行权价取得 510500 ETF，持有到该轮累计期权/ETF损益覆盖下一次 ETF 卖出成本，再于次日开盘退出。\n\n"
    record += "## 结果\n\n" + window.to_string(index=False) + "\n\n## 审计\n\n" + json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    (OUT / "command_log.txt").write_text("python -X utf8 research_ic_short95_cash_secured_put_v1.py\n", encoding="utf-8")
    print(window.to_string(index=False))
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
