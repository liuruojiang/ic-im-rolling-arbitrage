"""Postprocess frozen v2 IM/MO collar outputs; no strategy replay or source edits."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs/20260925_causal_multi_tier"
IM = ROOT / "data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv"


def analyze_tier(tier: str, cycles: pd.DataFrame, daily: pd.DataFrame, im: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    selected = cycles[(cycles["tier"] == tier) & (cycles["status"] == "selected")].copy()
    collar_name = f"{tier}__matched_collar"
    base_name = f"{tier}__matched_im"
    put_name = f"{tier}__matched_put"
    call_name = f"{tier}__matched_call"
    rows: list[dict[str, object]] = []
    for cycle in selected.itertuples(index=False):
        marks = im[(im["contract"] == cycle.future_contract) & (im["date"] == cycle.expiry_date)]
        if len(marks) != 1:
            raise RuntimeError(f"Missing unique IM final settlement: {cycle.future_contract}")
        f_exit = float(marks.iloc[0]["settle"])
        collar_daily = daily[(daily["candidate"] == collar_name) & (daily["future_contract"] == cycle.future_contract) & daily["active"]]
        base_daily = daily[(daily["candidate"] == base_name) & (daily["future_contract"] == cycle.future_contract) & daily["active"]]
        if collar_daily.empty or len(collar_daily) != len(base_daily):
            raise RuntimeError(f"Cycle path misalignment {cycle.future_contract}")
        n = float(collar_daily.iloc[0]["notional"])
        future_points = f_exit - float(cycle.future_entry_open)
        basis_t = float(cycle.spot_signal) - float(cycle.future_signal_close)
        entry_price_adjustment = float(cycle.future_signal_close) - float(cycle.future_entry_open)
        index_move = f_exit - float(cycle.spot_signal)
        if not math.isclose(future_points, basis_t + entry_price_adjustment + index_move, abs_tol=1e-9):
            raise RuntimeError(f"Basis identity mismatch {cycle.future_contract}")
        f_pnl = float(collar_daily["future_pnl"].sum())
        p_pnl = float(collar_daily["put_pnl"].sum())
        c_pnl = float(collar_daily["call_pnl"].sum())
        interest = float(collar_daily["cash_interest"].sum())
        costs = float(collar_daily[["future_cost", "put_cost", "call_cost"]].sum().sum())
        base_interest = float(base_daily["cash_interest"].sum())
        base_costs = float(base_daily[["future_cost", "put_cost", "call_cost"]].sum().sum())
        if not math.isclose(f_pnl, 200.0 * future_points, abs_tol=1e-6):
            raise RuntimeError(f"Future P&L mismatch {cycle.future_contract}")
        if not math.isclose(f_pnl + p_pnl + c_pnl + interest - costs, float(collar_daily["total_pnl"].sum()), abs_tol=1e-6):
            raise RuntimeError(f"Collar P&L mismatch {cycle.future_contract}")
        put_payoff = max(float(cycle.put_strike) - f_exit, 0.0)
        call_payoff = max(f_exit - float(cycle.call_strike), 0.0)
        if not math.isclose(p_pnl / 200.0, put_payoff - float(cycle.put_entry_open), abs_tol=1e-8):
            raise RuntimeError(f"Put terminal identity mismatch {cycle.future_contract}")
        if not math.isclose(c_pnl / 200.0, float(cycle.call_entry_open) - call_payoff, abs_tol=1e-8):
            raise RuntimeError(f"Call terminal identity mismatch {cycle.future_contract}")
        rows.append(dict(
            tier=tier, contract=cycle.future_contract, entry_date=cycle.entry_date, expiry_date=cycle.expiry_date,
            notional=n, spot_signal=float(cycle.spot_signal), future_signal_close=float(cycle.future_signal_close),
            future_entry_open=float(cycle.future_entry_open), future_exit_settle=f_exit,
            signal_basis_points=basis_t, entry_price_adjustment_points=entry_price_adjustment,
            index_move_t_close_to_expiry_settle_points=index_move, future_total_points=future_points,
            signal_basis_ratio=basis_t/float(cycle.future_entry_open),
            entry_price_adjustment_ratio=entry_price_adjustment/float(cycle.future_entry_open),
            index_move_ratio=index_move/float(cycle.future_entry_open),
            put_strike=float(cycle.put_strike), call_strike=float(cycle.call_strike),
            put_otm_signal_ratio=1.0-float(cycle.put_strike)/float(cycle.spot_signal),
            call_otm_signal_ratio=float(cycle.call_strike)/float(cycle.spot_signal)-1.0,
            put_entry_open=float(cycle.put_entry_open), call_entry_open=float(cycle.call_entry_open),
            actual_premium_gap_points=float(cycle.call_entry_open - cycle.put_entry_open),
            actual_premium_gap_ratio=(float(cycle.call_entry_open)-float(cycle.put_entry_open))/float(cycle.future_entry_open),
            put_expiry_payoff_points=put_payoff, call_expiry_payoff_points=call_payoff,
            put_expiry_payoff_ratio=put_payoff/float(cycle.future_entry_open),
            call_expiry_payoff_ratio=call_payoff/float(cycle.future_entry_open),
            future_pnl_ratio=f_pnl/n, put_pnl_ratio=p_pnl/n, call_pnl_ratio=c_pnl/n,
            cash_interest_ratio=interest/n, all_cost_ratio=costs/n,
            collar_cycle_total_ratio=float(collar_daily.iloc[-1]["cycle_cum_return"]),
            matched_im_cycle_total_ratio=float(base_daily.iloc[-1]["cycle_cum_return"]),
            incremental_cash_interest_ratio=(interest-base_interest)/n,
            incremental_option_cost_ratio=(costs-base_costs)/n,
            put_below_future_entry_floor_points=float(cycle.put_strike-cycle.future_entry_open+cycle.call_entry_open-cycle.put_entry_open),
            call_itm_at_expiry=call_payoff>0, put_itm_at_expiry=put_payoff>0,
        ))
    out = pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError(f"No selected cycles in {tier}")
    final_nav = daily.groupby("candidate", sort=False).tail(1).set_index("candidate")["nav"]
    li, lp, lc, lcol = [math.log(float(final_nav[name])) for name in (base_name, put_name, call_name, collar_name)]
    n_days = int(daily[daily["candidate"] == collar_name].shape[0])
    put_log = 0.5 * ((lp - li) + (lcol - lc))
    call_log = 0.5 * ((lc - li) + (lcol - lp))
    if not math.isclose(put_log + call_log, lcol - li, abs_tol=1e-12):
        raise RuntimeError("Shapley decomposition mismatch")
    grouped = out[["signal_basis_points", "entry_price_adjustment_points", "index_move_t_close_to_expiry_settle_points",
                   "signal_basis_ratio", "entry_price_adjustment_ratio", "index_move_ratio", "actual_premium_gap_ratio",
                   "put_expiry_payoff_ratio", "call_expiry_payoff_ratio",
                   "future_pnl_ratio", "put_pnl_ratio", "call_pnl_ratio", "cash_interest_ratio", "all_cost_ratio",
                   "collar_cycle_total_ratio", "matched_im_cycle_total_ratio", "incremental_cash_interest_ratio", "incremental_option_cost_ratio"]].sum()
    summary: dict[str, object] = dict(
        tier=tier, selected_cycles=len(out), call_itm_cycles=int(out["call_itm_at_expiry"].sum()),
        put_itm_cycles=int(out["put_itm_at_expiry"].sum()),
        all_floors_below_zero=bool((out["put_below_future_entry_floor_points"]<0).all()),
        median_put_otm_signal_ratio=float(out["put_otm_signal_ratio"].median()),
        median_call_otm_signal_ratio=float(out["call_otm_signal_ratio"].median()),
        median_abs_actual_premium_gap_ratio=float(out["actual_premium_gap_ratio"].abs().median()),
        negative_net_premium_after_option_entry_cost_cycles=int((out["actual_premium_gap_ratio"]-0.001<0).sum()),
        sum_cycle_components={key:float(value) for key,value in grouped.items()},
        exact_log_nav_gap=float(lcol-li),
        annualized_log_gap=float((lcol-li)*252.0/n_days),
        put_shapley_log_gap=float(put_log), call_shapley_log_gap=float(call_log),
        top_call_drag_cycles=out.nsmallest(5,"call_pnl_ratio")[["contract","call_pnl_ratio","future_pnl_ratio","signal_basis_points","index_move_t_close_to_expiry_settle_points"]].to_dict("records"),
    )
    return out, summary


def main() -> None:
    cycles = pd.read_csv(RUN/"cycles.csv", parse_dates=["signal_date","entry_date","expiry_date"])
    daily = pd.read_csv(RUN/"daily.csv.gz", parse_dates=["date"])
    im = pd.read_csv(IM, parse_dates=["date"])
    details=[]
    summaries=[]
    for tier in ("gap_10bp", "gap_no_gate"):
        detail, summary = analyze_tier(tier, cycles, daily, im)
        details.append(detail)
        summaries.append(summary)
    output = pd.concat(details, ignore_index=True)
    output.to_csv(RUN/"attribution_cycles.csv", index=False, encoding="utf-8-sig")
    (RUN/"attribution_summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
