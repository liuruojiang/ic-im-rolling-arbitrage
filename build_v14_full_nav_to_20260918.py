"""Extend the verified v1.4 counterfactual replay to 2026-09-18.

The frozen v1.4 research panels end on 2026-08-14.  This isolated runner
continues their *v1.4* end states with official daily marks; it does not use
the return tail of any earlier strategy version.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import poe_ic_im_mainline_v1_4_bot as v14


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "v14_full_nav_refresh_20260920_final"
END = date(2026, 9, 18)
CUTOFF = date(2026, 8, 14)
START = date(2026, 8, 17)
CASH = 1.03 ** (1 / 252) - 1
FUT_COST = 0.0001
MO_PUT_COST = 0.0005

IC_BASE = ROOT / "outputs" / "v1_4_fix_20260917" / "historical_rerun" / "ic" / "daily_outputs" / "daily.csv.gz"
IM_BASE = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_4_r1_full_joint_im_core_and_momentum_long_put_mom120_floor_2_vs_3" / "daily.csv.gz"
SIGNALS = ROOT / "outputs" / "nav_versioned_refresh_20260920_v14_corrected_final2" / "historical_signals.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def q(marks: pd.DataFrame, day: date, contract: str) -> pd.Series:
    try:
        return marks.loc[(pd.Timestamp(day), contract)]
    except KeyError as exc:
        raise RuntimeError(f"missing official mark: {day} {contract}") from exc


def select_mo_put(day: date, spot: float) -> tuple[str, float]:
    clock = datetime(day.year, day.month, day.day, 16, 0, tzinfo=v14.BEIJING)
    frame = v14._cffex_historical_quote_frame("MO", day, clock)
    old_effective = v14.im_put_policy.EFFECTIVE_DATE
    try:
        v14.im_put_policy.EFFECTIVE_DATE = date(1900, 1, 1)
        selected = v14.select_independent_im_put_for_reset(frame, day, spot)
    finally:
        v14.im_put_policy.EFFECTIVE_DATE = old_effective
    return str(selected["instrument"]), float(selected["lastprice"])


def main() -> None:
    if OUT.exists() and any(OUT.iterdir()):
        raise FileExistsError(OUT)
    OUT.mkdir(parents=True)

    raw = json.loads(SIGNALS.read_text(encoding="utf-8"))
    days = [date.fromisoformat(x) for x in sorted(raw) if START <= date.fromisoformat(x) <= END]
    if not days or days[-1] != END:
        raise RuntimeError("signal driver history does not reach 2026-09-18")

    # The signals file contributes only inherited v1.4 drivers (futures chain,
    # momentum, grid, valuation quantities and Call).  Long-Put contracts and
    # the IC fixed-core router are rebuilt below from the v1.4 8/14 state.
    contracts: set[str] = {"MO2609-P-7700", "MO2610-P-7800", "MO2608-C-8800"}
    for day in days:
        for product in ("IC", "IM"):
            s = raw[day.isoformat()][product]
            for key in (
                "core_current", "core_eod_contract", "call_current_contract", "call_target_contract",
            ):
                if s.get(key):
                    contracts.add(str(s[key]))

    # Discover every v1.4 102% MO contract needed by monthly resets and
    # zero-to-positive momentum-Put entries before downloading the final mark panel.
    mo_plan: dict[date, str] = {}
    mom_active = 0.75
    for day in days:
        s = raw[day.isoformat()]["IM"]
        target_mom = 1.5 * float(s["momentum_next_weight"]) if float(s["momentum_120"]) < 0 else 0.0
        needs_pick = bool(s.get("option_monthly_reset_due")) or (mom_active == 0 and target_mom > 0)
        if needs_pick:
            future_contract = str(s["core_eod_contract"])
            fm = v14.fetch_cffex_daily_marks([future_contract], day, day)
            spot = float(fm.loc[(pd.Timestamp(day), future_contract), "settle"])
            contract, _ = select_mo_put(day, spot)
            mo_plan[day] = contract
            contracts.add(contract)
        mom_active = target_mom

    marks = v14.fetch_cffex_daily_marks(sorted(contracts), CUTOFF, END)
    sse = {
        "510500P2609M07750": v14.fetch_option_closes("10011016"),
        "510500P2609M07250": v14.fetch_option_closes("10012080"),
        "510500P2612M07500": v14.fetch_option_closes("10012099"),
    }

    # ----- IC v1.4 state at the verified 2026-08-14 counterfactual boundary.
    short_contract = "510500P2609M07750"
    short_strike = 7.75
    short_entry_moneyness = 0.9623742704582142
    short_contracts = 1.8633317093237504e-05
    short_mark = float(sse[short_contract].loc[pd.Timestamp(CUTOFF)])
    short_equity = short_contracts * (short_strike / short_entry_moneyness) * 10_000
    # Include the already-observed 8/14 mark and one day of cash carry.
    short_equity += short_contracts * 10_000 * (0.1532 - short_mark) + short_equity * 0.7 * CASH
    ic_prev_weight = 0.25
    ic_mom_contract: str | None = "510500P2609M07250"
    ic_mom_qty = 8.0
    ic_mom_mark = float(sse[ic_mom_contract].loc[pd.Timestamp(CUTOFF)])
    ic_rows: list[dict[str, object]] = []

    # ----- IM v1.4 state at the same boundary: independent 102% sleeves.
    im_prev_weight = 0.5
    im_core_contract = "MO2609-P-7700"
    im_core_qty = 1.5
    im_core_mark = float(q(marks, CUTOFF, im_core_contract)["settle"])
    im_mom_contract: str | None = "MO2610-P-7800"
    im_mom_qty = 0.75
    im_mom_mark = float(q(marks, CUTOFF, im_mom_contract)["settle"])
    im_call_contract: str | None = "MO2608-C-8800"
    im_call_qty = -1.0
    im_call_mark = float(q(marks, CUTOFF, im_call_contract)["settle"])
    im_rows: list[dict[str, object]] = []

    for day in days:
        # IC: fixed core remains in its v1.4 short-Put cycle; the admission gate
        # blocks the 9/18 early roll, so no older-version core futures are inserted.
        s = raw[day.isoformat()]["IC"]
        current = str(s["core_current"]); eod = str(s["core_eod_contract"])
        current_quote = q(marks, day, current)
        pre = float(current_quote["pre_settle"]); settle = float(q(marks, day, eod)["settle"])
        # On a roll day, mark the old contract from pre-settle to its own
        # settlement.  The new contract becomes the next session's holding;
        # the near/far spread is not a daily trading profit or loss.
        unit_ret = float(current_quote["settle"]) / pre - 1
        weight = float(s["momentum_current_weight"]); next_weight = float(s["momentum_next_weight"])
        grid = float(s.get("grid_current", 0.0)); next_grid = float(s.get("grid_target", grid))
        roll = current != eod
        mom_fut = 0.5 * weight * unit_ret
        mom_cost = FUT_COST * (0.5 * abs(weight - ic_prev_weight) + weight * float(roll))
        grid_fut = grid * unit_ret
        grid_cost = FUT_COST * (abs(next_grid - grid) + 2 * grid * float(roll))

        now_short = float(sse[short_contract].loc[pd.Timestamp(day)])
        local_pnl = short_contracts * 10_000 * (short_mark - now_short)
        router_ret = local_pnl / short_equity + 0.7 * CASH
        short_equity += local_pnl + short_equity * 0.7 * CASH
        short_mark = now_short

        target_qty = float(s.get("put_target_momentum_qty", 0.0))
        target_contract = str(s.get("put_target_contract") or ic_mom_contract or "510500P2609M07250") if target_qty > 0 else None
        if ic_mom_contract and ic_mom_qty > 0:
            mark = float(sse[ic_mom_contract].loc[pd.Timestamp(day)])
            put_pnl = ic_mom_qty * 10_000 * (mark - ic_mom_mark) / (pre * 200)
        else:
            mark = 0.0; put_pnl = 0.0
        if target_contract and target_contract not in sse:
            raise RuntimeError(f"unmapped IC momentum Put {target_contract}")
        target_mark = float(sse[target_contract].loc[pd.Timestamp(day)]) if target_contract else 0.0
        put_cost = (ic_mom_qty + target_qty) / 20 * FUT_COST if target_contract != ic_mom_contract else abs(target_qty - ic_mom_qty) / 20 * FUT_COST
        put_fraction = target_qty * 10_000 * target_mark / (settle * 200)
        cash = max(0.0, 0.5 * (1 - 0.3 * next_weight) - put_fraction - 0.3 * next_grid)
        ret = 0.5 * router_ret + mom_fut + grid_fut + put_pnl - mom_cost - grid_cost - put_cost + cash * CASH
        ic_rows.append(dict(date=pd.Timestamp(day), ret=ret, router_ret=router_ret, momentum_weight=weight,
                            grid_units=grid, put_pnl_ret=put_pnl, put_cost_rate=put_cost,
                            short_put_contract=short_contract, momentum_put_contract=target_contract,
                            momentum_put_qty=target_qty, data_layer="v14_counterfactual_official_marks"))
        ic_prev_weight = next_weight; ic_mom_contract = target_contract; ic_mom_qty = target_qty; ic_mom_mark = target_mark

        # IM: use current v1.4 102% selection on every reset/entry, independent
        # core and momentum sleeves, plus the inherited v1.4 futures/grid/Call drivers.
        s = raw[day.isoformat()]["IM"]
        current = str(s["core_current"]); eod = str(s["core_eod_contract"])
        current_quote = q(marks, day, current)
        pre = float(current_quote["pre_settle"]); settle = float(q(marks, day, eod)["settle"])
        unit_ret = float(current_quote["settle"]) / pre - 1; roll = current != eod
        weight = float(s["momentum_current_weight"]); next_weight = float(s["momentum_next_weight"])
        grid = float(s.get("grid_current", 0.0)); next_grid = float(s.get("grid_target", grid))
        units = 0.5 + 0.5 * weight + grid
        futures_ret = units * unit_ret
        futures_cost = FUT_COST * (0.5 * abs(next_weight - im_prev_weight) + abs(next_grid-grid) + 2 * units * float(roll))

        core_mark = float(q(marks, day, im_core_contract)["settle"])
        core_pnl = 0.5 * im_core_qty * (core_mark - im_core_mark) / pre
        mom_mark = float(q(marks, day, im_mom_contract)["settle"]) if im_mom_contract and im_mom_qty > 0 else 0.0
        mom_pnl = 0.5 * im_mom_qty * (mom_mark - im_mom_mark) / pre if im_mom_contract and im_mom_qty > 0 else 0.0

        core_target_qty = float(s.get("core_put_target_qty_normalized", 1.5))
        mom_target_qty = 1.5 * next_weight if float(s["momentum_120"]) < 0 else 0.0
        reset = bool(s.get("option_monthly_reset_due"))
        selected = mo_plan.get(day)
        core_target_contract = selected if reset else (im_core_contract if core_target_qty > 0 else None)
        if mom_target_qty > 0:
            mom_target_contract = selected if (reset or im_mom_qty == 0) else im_mom_contract
        else:
            mom_target_contract = None
        core_target_mark = float(q(marks, day, core_target_contract)["settle"]) if core_target_contract else 0.0
        mom_target_mark = float(q(marks, day, mom_target_contract)["settle"]) if mom_target_contract else 0.0
        core_turn = (im_core_qty + core_target_qty) if core_target_contract != im_core_contract else abs(core_target_qty-im_core_qty)
        mom_turn = (im_mom_qty + mom_target_qty) if mom_target_contract != im_mom_contract else abs(mom_target_qty-im_mom_qty)
        put_cost = 0.5 * (core_turn + mom_turn) * MO_PUT_COST
        put_fraction = 0.5 * (core_target_qty * core_target_mark + mom_target_qty * mom_target_mark) / settle

        call_current = str(s.get("call_current_contract") or im_call_contract) if (s.get("call_current_contract") or im_call_contract) else None
        call_target = str(s.get("call_target_contract")) if s.get("call_target_contract") else None
        call_target_qty = float(s.get("call_target_qty_normalized", 0.0))
        if call_current and im_call_contract == call_current and im_call_qty:
            call_mark = float(q(marks, day, call_current)["settle"])
            call_pnl = 0.5 * im_call_qty * (call_mark - im_call_mark) / pre
        else:
            call_pnl = 0.0
        call_target_mark = float(q(marks, day, call_target)["settle"]) if call_target and call_target_qty else 0.0
        call_cost = 0.5 * abs(call_target_qty - im_call_qty) * FUT_COST if call_target != im_call_contract or call_target_qty != im_call_qty else 0.0
        call_margin = float(s.get("call_margin_fraction", 0.0) or 0.0)
        cash = max(0.0, 1 - 0.3 * (0.5 + 0.5 * next_weight + next_grid) - put_fraction - call_margin)
        ret = futures_ret + core_pnl + mom_pnl + call_pnl - futures_cost - put_cost - call_cost + cash * CASH
        im_rows.append(dict(date=pd.Timestamp(day), ret=ret, momentum_weight=weight, grid_units=grid,
                            futures_gross_ret=futures_ret, put_pnl_ret=core_pnl+mom_pnl,
                            put_cost_rate=put_cost, core_put_contract=core_target_contract,
                            momentum_put_contract=mom_target_contract, core_put_qty=core_target_qty,
                            momentum_put_qty=mom_target_qty, call_contract=call_target,
                            data_layer="v14_counterfactual_official_marks"))
        im_prev_weight = next_weight
        im_core_contract = core_target_contract; im_core_qty = core_target_qty; im_core_mark = core_target_mark
        im_mom_contract = mom_target_contract; im_mom_qty = mom_target_qty; im_mom_mark = mom_target_mark
        im_call_contract = call_target; im_call_qty = call_target_qty; im_call_mark = call_target_mark

    ic_tail = pd.DataFrame(ic_rows); im_tail = pd.DataFrame(im_rows)
    if not ic_tail.date.equals(im_tail.date) or not np.isfinite(ic_tail.ret).all() or not np.isfinite(im_tail.ret).all():
        raise RuntimeError("tail alignment/finite check failed")

    ic0 = pd.read_csv(IC_BASE, compression="gzip", parse_dates=["date"])
    ic0 = ic0[(ic0.scope == "real") & (ic0.variant == "final_joint")][["date", "return_net"]].rename(columns={"return_net":"ret"})
    im0 = pd.read_csv(IM_BASE, compression="gzip", parse_dates=["date"])
    im0 = im0[(im0.scope == "real") & (im0.candidate == "real_floor3")][["date", "ret"]]
    ic = pd.concat([ic0, ic_tail[["date","ret"]]], ignore_index=True).sort_values("date")
    im = pd.concat([im0, im_tail[["date","ret"]]], ignore_index=True).sort_values("date")
    one_year_start = pd.Timestamp(END) - pd.DateOffset(years=1)
    ic = ic[ic.date >= one_year_start].copy(); im = im[im.date >= one_year_start].copy()
    common = ic.merge(im, on="date", suffixes=("_IC","_IM"), validate="one_to_one")
    common["IC_nav"] = (1 + common.ret_IC).cumprod(); common["IM_nav"] = (1 + common.ret_IM).cumprod()
    common.to_csv(OUT / "nav_1y.csv", index=False, encoding="utf-8-sig")
    ic_tail.to_csv(OUT / "ic_tail_daily.csv", index=False, encoding="utf-8-sig")
    im_tail.to_csv(OUT / "im_tail_daily.csv", index=False, encoding="utf-8-sig")

    metrics = {}
    for p in ("IC", "IM"):
        nav = common[f"{p}_nav"]; dd = nav/nav.cummax()-1
        metrics[p] = {"return": float(nav.iloc[-1]-1), "max_drawdown": float(dd.min()), "final_nav": float(nav.iloc[-1])}
    fig, ax = plt.subplots(figsize=(12,7), facecolor="#f8fafc"); ax.set_facecolor("#f8fafc")
    for p,c in (("IC","#2563a8"),("IM","#d97716")):
        ax.plot(common.date, common[f"{p}_nav"], lw=2.5, color=c, label=f"{p} v1.4  |  {metrics[p]['return']:+.1%}")
    ax.axhline(1,color="#94a3b8",ls="--",lw=1); ax.grid(axis="y",alpha=.35); ax.legend(frameon=False,loc="upper left")
    ax.set_ylabel("NAV (start = 1)"); ax.set_title("IC / IM v1.4 unified-rule NAV — latest actual data",weight="bold",fontsize=16)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2)); ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.text(.09,.035,"Current v1.4 rules over the full window; real listed-options layer; official marks through 2026-09-18. Research only.",fontsize=9,color="#475569")
    fig.subplots_adjust(left=.09,right=.96,top=.88,bottom=.14); fig.savefig(OUT/"nav_1y.png",dpi=180,facecolor=fig.get_facecolor()); plt.close(fig)

    verification = {
        "status":"v1_4_single_rule_counterfactual_latest_actual_data",
        "end":str(END), "frozen_v14_end":str(CUTOFF), "tail_start":str(START),
        "tail_sessions":len(days), "metrics":metrics, "mo_102_selection_events":{str(k):v for k,v in mo_plan.items()},
        "source_sha256":{str(p.relative_to(ROOT)):sha(p) for p in (IC_BASE,IM_BASE,SIGNALS)},
        "checks":{"tail_dates_match":True,"tail_finite":True,"no_old_version_return_tail":True,
                  "im_momentum_put_formula":"1.5 * next momentum weight when MOM120 < 0",
                  "im_put_moneyness":1.02,"data_end":str(common.date.max().date())},
    }
    (OUT/"verification.json").write_text(json.dumps(verification,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (OUT/"record.md").write_text(
        "# IC / IM v1.4 统一规则最新净值\n\n"
        f"- 数据截止：{END}。\n- 全窗口统一使用 v1.4 反事实规则；未拼接旧版本收益尾段。\n"
        "- 2026-08-14以前采用已核验 v1.4 真实挂牌层；之后从该日 v1.4 状态逐日按官方行情续接。\n"
        "- IM核心与动量Put均按102%选约；动量Put按MOM120<0和动量权重独立配置；网格不配Put。\n"
        f"- 最近一年：IC {metrics['IC']['return']:.2%}，IM {metrics['IM']['return']:.2%}。\n",
        encoding="utf-8")
    print(json.dumps({"output":str(OUT),"metrics":metrics,"selections":verification["mo_102_selection_events"]},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
