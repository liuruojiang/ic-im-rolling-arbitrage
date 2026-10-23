"""Unfiltered 510500 short 95% Put -> physical ETF -> IC recovery study."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import ic_roll_momentum_stage2_put_v2 as ic_put

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quant_param_scan_runs" / "20260914_ic_short95_put_to_ic_recovery_v1"
START = pd.Timestamp("2022-09-19")
RATIO, ETF_MULTIPLIER, IC_MULTIPLIER, ONE_WAY = 0.95, 10_000.0, 200.0, 0.0001
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def window_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window, years in (("full", None), ("10y", 10), ("5y", 5), ("3y", 3), ("1y", 1)):
        if years in (5, 10):
            rows.append({"window": window, "available": False, "reason": "real 510500 Put history shorter than requested window", "rows": 0})
            continue
        start = daily.date.min() if years is None else daily.date.max() - pd.DateOffset(years=years)
        sample = daily[daily.date.ge(start)]
        nav = (1.0 + sample.return_net).cumprod()
        rows.append({"window": window, "available": True, "reason": "", "start": str(sample.date.min().date()), "end": str(sample.date.max().date()), "rows": len(sample), "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0), "ann_vol": float(sample.return_net.std(ddof=1) * np.sqrt(252.0)), "max_dd": float((nav / nav.cummax() - 1.0).min()), "total_return": float(nav.iloc[-1] - 1.0)})
    return pd.DataFrame(rows)


def run() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    frames, _, _, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    active = frames["ic"].copy()
    all_dates = pd.DatetimeIndex(active.date)
    active = active[active.date.ge(START)].reset_index(drop=True)
    etf = frames["etf500"].set_index("date")
    snapshots, histories = frames["snapshots"].copy(), frames["histories"].copy()
    options = histories.set_index(["security_id", "date"])
    expiry_by_security = histories.groupby("security_id").date.max()
    chains = {pd.Timestamp(day): group for day, group in snapshots.groupby("date", sort=False)}
    future_raw = pd.read_csv(ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv", parse_dates=["date"])
    futures = future_raw.set_index(["contract", "date"])
    if etf.reindex(active.date)[["open", "close"]].isna().any().any():
        raise RuntimeError("Missing 510500 mark on an IC session")

    equity, state, pos, pending_exit, cycle = 1.0, "idle", None, False, None
    rows: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    cycles: list[dict[str, object]] = []
    skips = 0

    for i, b in active.iterrows():
        day = pd.Timestamp(b.date)
        prev_equity, pnl, cost, action = equity, 0.0, 0.0, ""
        etf_open, etf_close = float(etf.loc[day, "open"]), float(etf.loc[day, "close"])

        if state == "short_put":
            assert pos is not None and cycle is not None
            quote = options.loc[(pos["security_id"], day)]
            if not (quote.close > 0 and quote.volume > 0):
                raise RuntimeError(f"Unmarkable active option {pos['contract']} at {day.date()}")
            pnl += pos["contracts"] * ETF_MULTIPLIER * (pos["mark"] - float(quote.close))
            pos["mark"] = float(quote.close)
            if day == pos["expiry"]:
                intrinsic = max(pos["strike"] - etf_close, 0.0)
                pnl += pos["contracts"] * ETF_MULTIPLIER * (float(quote.close) - intrinsic)
                if intrinsic > 0:
                    pos = {"shares": pos["contracts"] * ETF_MULTIPLIER, "etf_mark": etf_close}
                    state, action = "pending_etf_to_ic", "physical_assignment_pending_etf_to_ic"
                    cycle["physical_assignment_date"] = str(day.date())
                else:
                    cycle["realized_pnl"] = cycle.get("realized_pnl", 0.0) + pnl - cost
                    cycle.update(exit_date=str(day.date()), exit_reason="worthless_expiry", closed=True)
                    cycles.append(cycle.copy())
                    cycle, pos, state, action = None, None, "idle", "worthless_expiry"

        elif state == "pending_etf_to_ic":
            assert pos is not None and cycle is not None
            shares = pos["shares"]
            pnl += shares * (etf_open - pos["etf_mark"])
            cost += shares * etf_open * ONE_WAY  # sell assigned ETF at the open
            open_ic = float(futures.loc[(b.contract, day), "open"])
            settle_ic = float(futures.loc[(b.contract, day), "settle"])
            if not (open_ic > 0 and settle_ic > 0):
                raise RuntimeError(f"Untradable IC conversion at {day.date()}")
            units = shares * etf_open / (open_ic * IC_MULTIPLIER)
            cost += units * IC_MULTIPLIER * open_ic * ONE_WAY
            pnl += units * IC_MULTIPLIER * (settle_ic - open_ic)
            pos = {"contract": str(b.contract), "units": units, "mark": settle_ic}
            state, action = "ic_future", "assignment_sell_etf_buy_ic_next_open"
            cycle["ic_conversion_date"] = str(day.date())
            cycle["ic_units_after_conversion"] = units

        elif state == "ic_future":
            assert pos is not None and cycle is not None
            current = futures.loc[(pos["contract"], day)]
            if pending_exit:
                pnl += pos["units"] * IC_MULTIPLIER * (float(current.open) - pos["mark"])
                cost += pos["units"] * IC_MULTIPLIER * float(current.open) * ONE_WAY
                cycle["realized_pnl"] = cycle.get("realized_pnl", 0.0) + pnl - cost
                cycle.update(exit_date=str(day.date()), exit_reason="recovery_exit_ic_next_open", closed=True, recovery_days=int((day - pd.Timestamp(cycle["ic_conversion_date"])).days))
                cycles.append(cycle.copy())
                cycle, pos, state, pending_exit, action = None, None, "idle", False, "recovery_exit_ic_next_open"
            elif pd.notna(b.roll_to) and str(b.roll_to) != pos["contract"]:
                nxt = futures.loc[(str(b.roll_to), day)]
                pnl += pos["units"] * IC_MULTIPLIER * (float(current.close) - pos["mark"])
                cost += pos["units"] * IC_MULTIPLIER * (float(current.close) + float(nxt.close)) * ONE_WAY
                pnl += pos["units"] * IC_MULTIPLIER * (float(nxt.settle) - float(nxt.close))
                pos["contract"], pos["mark"] = str(b.roll_to), float(nxt.settle)
                action = "ic_monthly_roll_close"
            else:
                pnl += pos["units"] * IC_MULTIPLIER * (float(current.settle) - pos["mark"])
                pos["mark"] = float(current.settle)

        if state == "idle" and i > 0 and not action:
            prior = float(etf.loc[pd.Timestamp(active.loc[i - 1, "date"]), "close"])
            month = day.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
            chain = chains.get(day, snapshots.iloc[:0])
            chain = chain[chain.contract_month.eq(month)].copy()
            if len(chain):
                chain["error"] = (chain.strike.astype(float) - prior * RATIO).abs()
                selected = chain.sort_values(["error", "strike", "contract_id"]).iloc[0]
                quote = options.loc[(selected.security_id, day)]
                if quote.open > 0 and quote.volume > 0:
                    contracts = prev_equity / (prior * ETF_MULTIPLIER)
                    pos = {"security_id": str(selected.security_id), "contract": str(selected.contract_id), "strike": float(selected.strike), "contracts": contracts, "mark": float(quote.open), "expiry": pd.Timestamp(expiry_by_security.loc[selected.security_id])}
                    cycle = {"entry_date": str(day.date()), "put_contract": str(selected.contract_id), "strike": float(selected.strike), "entry_moneyness": float(selected.strike) / prior, "contracts": contracts, "closed": False}
                    cost += contracts * ETF_MULTIPLIER * prior * ONE_WAY
                    state, action = "short_put", "sell_next_month_95_put_open"
                else:
                    skips += 1
                    action = "skip_untradable_nearest_strike"
            else:
                skips += 1
                action = "skip_missing_next_month_chain"

        if cycle is not None:
            cycle["realized_pnl"] = cycle.get("realized_pnl", 0.0) + pnl - cost
        # Mirrors IM's 30% futures/option risk buffer after the ETF leg is
        # converted; actual short-ETF-Put margin is intentionally not assumed.
        cash_weight = 1.0 if state == "idle" else 0.70
        cash = prev_equity * cash_weight * CASH_DAILY
        equity += pnl - cost + cash
        if not np.isfinite(equity) or equity <= 0:
            raise RuntimeError(f"Non-positive NAV at {day.date()}")
        if state == "ic_future" and cycle is not None:
            exit_cost = pos["units"] * IC_MULTIPLIER * pos["mark"] * ONE_WAY
            if cycle["realized_pnl"] >= exit_cost:
                pending_exit = True
        record = {"date": day, "candidate": "ic_short95_put_to_ic", "return_net": equity / prev_equity - 1.0, "nav": equity, "pnl": pnl, "cost": cost, "cash": cash, "cash_weight": cash_weight, "state": state, "action": action, "ic_contract": "" if state != "ic_future" else pos["contract"], "ic_units": 0.0 if state != "ic_future" else pos["units"]}
        rows.append(record)
        if action:
            events.append(record.copy())

    if cycle is not None:
        cycle.update(mark_date=str(pd.Timestamp(active.date.iloc[-1]).date()), open_cycle_pnl=cycle.get("realized_pnl", 0.0))
        cycles.append(cycle.copy())
    daily, cycle_frame = pd.DataFrame(rows), pd.DataFrame(cycles)
    ledger = float(daily.pnl.sub(daily.cost).sum())
    closed = cycle_frame.closed.fillna(False)
    cycle_total = float(cycle_frame.loc[closed, "realized_pnl"].fillna(0.0).sum() + cycle_frame.loc[~closed, "open_cycle_pnl"].fillna(0.0).sum())
    error = abs(ledger - cycle_total)
    if error > 1e-12:
        raise RuntimeError(f"Cycle ledger mismatch: {error}")
    audit = {"rows": len(daily), "entry_skips": skips, "cycles": len(cycle_frame), "closed_cycles": int(closed.sum()), "assignments": int(cycle_frame.physical_assignment_date.notna().sum()), "ledger_max_abs_error": error, "min_cash_weight": float(daily.cash_weight.min()), "max_single_day_loss": float(daily.return_net.min()), "open_state": state}
    return daily, pd.DataFrame(events), cycle_frame, audit


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"Immutable research output exists: {OUT}")
    OUT.mkdir(parents=True)
    daily, events, cycles, audit = run()
    metrics = window_metrics(daily)
    daily.to_csv(OUT / "daily.csv.gz", index=False, compression="gzip")
    events.to_csv(OUT / "events.csv", index=False)
    cycles.to_csv(OUT / "cycles.csv", index=False)
    metrics.to_csv(OUT / "window_metrics.csv", index=False)
    meta = {"status": "research_only_no_signal_or_order_change", "strategy": "unfiltered next-month 510500 95% short Put; physical ETF assignment is sold next open and proceeds converted into active IC futures; exit IC next open after cycle PnL recovers its exit cost", "execution": {"put_entry": "prior ETF close / current option open", "assignment_conversion": "next IC/ETF session open", "IC_roll": "existing active-contract close roll", "cost": "1bp per option-equivalent, ETF, and IC side", "cash": "3% annual; 30% risk buffer in short-Put and IC states"}, "audit": audit, "source_hashes": {"script": sha256(Path(__file__)), "ic_put_engine": sha256(Path(ic_put.__file__))}, "limitations": "510500 and IC are different instruments; conversion is nominal-value matched at the next open, not a hedge-ratio guarantee. ETF Put physical exercise, option margin, ETF financing/taxes, bid-ask, impact, capacity and broker margin are not modeled. No independent OOS; research only."}
    (OUT / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC：卖 95% 510500 Put，交割后换 IC，无过滤\n\n状态：研究测试，不改生产、研究信号或订单。\n\n"
    record += "期权实值交割后，下一共同交易日开盘卖出取得的510500 ETF、等名义买入活跃IC；随后按原IM状态机，IC累计损益覆盖退出成本后次开盘退出。\n\n"
    record += "## 结果\n\n" + metrics.to_string(index=False) + "\n\n## 审计\n\n" + json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    (OUT / "command_log.txt").write_text("python -X utf8 research_ic_short95_put_to_ic_recovery_v1.py\n", encoding="utf-8")
    print(metrics.to_string(index=False))
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
