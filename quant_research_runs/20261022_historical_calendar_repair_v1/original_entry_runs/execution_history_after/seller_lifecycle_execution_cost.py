"""Replay the current formal seller FSM on newly reconstructed dated signals.

T close decides, T+1 close is the model fill. Daily exchange volume is never
an admission or quantity constraint. This is an event/state stage, not NAV.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd

from native_fix4_historical_sources_v1 import HERE, ROOT, formal, sha256
from native_fix4_historical_sources_v2 import DatedSources
from native_fix4_risk_signals_v1 import OHLCV

VERSION = "v1"


def positive_quote(source: DatedSources, product: str, day: date, contract: str | None,
                   *, phase: str = "close") -> float | None:
    if not contract:
        return None
    if product == "IC":
        _, chain = source.ic_on(day)
        found = chain.loc[chain.contract.eq(contract), "open" if phase == "open" else "last"]
    else:
        chain = source.im_mo_on(day)
        found = chain.loc[chain.instrument.eq(contract), "openprice" if phase == "open" else "lastprice"]
    if len(found) != 1:
        return None
    value = float(found.iloc[0])
    return value if value > 0 else None


def candidate_from_row(row) -> dict:
    if not row.candidate_tradable:
        return {"tradable": False, "reason": str(row.candidate_reason)}
    security = None if pd.isna(row.candidate_security_id) else str(int(row.candidate_security_id))
    return {"tradable": True, "contract": row.candidate_contract, "security_id": security,
            "expiry": date.fromisoformat(row.candidate_expiry), "strike": float(row.candidate_strike),
            "premium": float(row.candidate_premium), "iv": float(row.candidate_iv),
            "qty_normalized": float(row.candidate_qty_normalized)}


def roll_candidate(source: DatedSources, product: str, day: date, execution: date,
                   expiry: date, risk_row, target_row, gov: pd.Series, spot: pd.Series) -> dict:
    futures = source.futures_on(product, day)
    matched = futures.loc[futures.contract.eq(target_row.quarter_core_eod), "close"]
    if len(matched) != 1:
        raise RuntimeError(f"{product} T future roll reference missing: {day}")
    signal = {"market_date": day, "next_trade_date": execution,
              "future_last": float(matched.iloc[0]), "momentum_next_weight": float(risk_row.momentum_target_weight),
              "momentum_120": float(risk_row.momentum_120), "valuation_tier": int(risk_row.valuation_tier),
              "valuation_puts_per_full_core": int(risk_row.valuation_tier)}
    rate = float(gov.asof(pd.Timestamp(day)))
    clock = datetime(day.year, day.month, day.day, 16, tzinfo=formal.BEIJING)
    if product == "IC":
        etf, chain = source.ic_on(day)
        signal["etf_price"] = etf
        securities = chain.set_index("contract").security_id.astype(str).to_dict()
        def fetch_month(ym: str):
            selected = chain.loc[chain.contract_month.dt.strftime("%y%m").eq(ym)].copy()
            if selected.empty:
                raise RuntimeError("有效510500 Put月份未挂牌")
            return selected, {"date": day.isoformat(), "time": "150000", "source": "dated_model_quote"}
        with formal.runtime_clock(clock), \
             patch.object(formal, "fetch_510500_chain_with_failover", side_effect=fetch_month), \
             patch.object(formal, "fetch_sina_510500_security_id", side_effect=lambda c: securities[c]), \
             patch.object(formal, "_gov10y_for_day", return_value=rate), \
             patch.object(formal, "_validate_chain_stamp_matches", return_value=None):
            return formal._v14_ic_short_put_candidate(signal, roll_from_expiry=expiry)
    signal["index_price"] = float(spot.loc[pd.Timestamp(day)])
    quotes = source.im_mo_on(day).copy()
    valid = quotes.lastprice.gt(0)
    quotes.loc[valid, "volume"] = quotes.loc[valid, "volume"].clip(lower=1)
    quotes.loc[valid, "position"] = quotes.loc[valid, "position"].clip(lower=1)
    original = formal._third_friday
    def actual_expiry(year: int, month: int) -> date:
        return date(2024, 2, 19) if (year, month) == (2024, 2) else original(year, month)
    with formal.runtime_clock(clock), \
         patch.object(formal, "_gov10y_for_day", return_value=rate), \
         patch.object(formal, "_third_friday", side_effect=actual_expiry):
        return formal._v14_im_short_put_candidate(signal, quotes, roll_from_expiry=expiry)


def run(product: str, arm: str, source: DatedSources, gov: pd.Series, spot: pd.Series) -> tuple[pd.DataFrame, dict]:
    candidate_rows = pd.read_csv(HERE / "native_fix4_seller_candidates_v1.csv.gz")
    candidate_rows = candidate_rows.loc[candidate_rows["product"].eq(product)].reset_index(drop=True)
    risk = pd.read_csv(HERE / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz")
    targets = pd.read_csv(HERE / f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz")
    if not (len(candidate_rows) == len(risk) - 1 == len(targets) - 1):
        raise RuntimeError("native seller lifecycle calendars disagree")
    if not candidate_rows.signal_date.equals(risk.signal_date.iloc[:-1].reset_index(drop=True)):
        raise RuntimeError("native seller candidate/risk dates disagree")
    anchor = formal.v14_policy.default_extension(product)
    pending = None
    recovery_pnl = 0.0
    recovery_price = None
    cycle_pnl = 0.0
    rows = []
    original_effective = formal.v14_policy.EFFECTIVE_SIGNAL_DATE
    try:
        formal.v14_policy.EFFECTIVE_SIGNAL_DATE = date(1900, 1, 1)
        for ordinal, row in enumerate(candidate_rows.itertuples(index=False)):
            day = date.fromisoformat(row.signal_date)
            execution = date.fromisoformat(row.execution_date)
            r = risk.iloc[ordinal]
            t = targets.iloc[ordinal]
            if pending is not None:
                if pending["execution_date"] != day:
                    raise RuntimeError("pending execution date differs from next signal day")
                anchor = pending["anchor"]
                pending = None
            route_before = anchor["v14_route_state"]
            held_contract = anchor["v14_short_put_contract"]
            held_mark = positive_quote(source, product, day, held_contract)
            candidate = {"tradable": False, "reason": "no_seller_baseline"} if arm == "no_seller" else candidate_from_row(row)
            roll = None
            if arm != "no_seller" and route_before == "short_put":
                expiry = date.fromisoformat(str(anchor["v14_short_put_expiry"])[:10])
                if day < expiry and not anchor["v14_early_roll_used"]:
                    roll = roll_candidate(source, product, day, execution, expiry, r, t, gov, spot)
            signal = {"market_date": day, "next_trade_date": execution, "close_confirmed": True,
                      "valuation_tier": int(r.valuation_tier), "valuation_puts_per_full_core": int(r.valuation_tier),
                      "momentum_next_weight": float(r.momentum_target_weight), "momentum_120": float(r.momentum_120),
                      "option_monthly_reset_due": bool(t.monthly_reset_due),
                      "core_units_current": .5, "core_units_target": .5,
                      "total_units_current": .5, "total_units_target": .5,
                      "momentum_put_target_qty_normalized": 0.0,
                      "v14_short_put_mark": held_mark}
            settlement = None
            settlement_value = None
            if route_before == "short_put":
                expiry = date.fromisoformat(str(anchor["v14_short_put_expiry"])[:10])
                if day >= expiry:
                    strike = float(str(held_contract).split("-")[-1]) if product == "IM" else float(str(held_contract).split("M")[-1]) / 1000
                    underlying = float(spot.loc[pd.Timestamp(day)]) if product == "IM" else source.ic_on(day)[0]
                    intrinsic = max(strike - underlying, 0.0)
                    settlement_value = intrinsic
                    if VERSION == "v4" and product == "IM":
                        official = source.im_mo_on(day)
                        matched = official.loc[official.instrument.eq(held_contract), "settle"]
                        if len(matched) != 1 or not pd.notna(matched.iloc[0]) or float(matched.iloc[0]) < 0:
                            raise RuntimeError(f"IM official settlement unavailable: {day} {held_contract}")
                        settlement_value = float(matched.iloc[0])
                    settlement = "assigned" if settlement_value > 0 else "expired_worthless"
                    signal.update(v14_model_settlement_confirmed=True, v14_model_settlement_outcome=settlement)
                    if settlement == "assigned":
                        multiplier = 100 if product == "IM" else 10_000
                        recovery_pnl = cycle_pnl - settlement_value * float(anchor["v14_short_put_qty_normalized"]) * multiplier
                        signal["v14_recovery_net_pnl_mark"] = recovery_pnl
            elif route_before in {"assigned_etf", "cash_wait", "recovery_future"}:
                current = float(spot.loc[pd.Timestamp(day)]) if product == "IM" else source.ic_on(day)[0]
                if recovery_price is not None:
                    recovery_pnl += (current - recovery_price) * (.5 * 200 if product == "IM" else .5 * 200 / 10_000 * 10_000)
                recovery_price = current
                signal["v14_recovery_net_pnl_mark"] = recovery_pnl
                if route_before in {"assigned_etf", "cash_wait"}:
                    signal["v14_model_recovery_future_confirmed"] = True
            if arm == "repeat_roll" and anchor["v14_early_roll_used"]:
                anchor = {**anchor, "v14_early_roll_used": False}
            out = formal.v14_policy.apply_policy(product, signal, anchor, candidate=candidate, roll_candidate=roll)
            action = out["v14_action"]
            next_anchor = {k: out[k] for k in formal.v14_policy.default_extension(product)}
            fill_old = None
            fill_new = None
            if action in {"ENTER_SHORT_PUT", "ROLL_SHORT_PUT"}:
                new_contract = next_anchor["v14_short_put_contract"]
                fill_new = positive_quote(source, product, execution, new_contract,
                                          phase="open" if VERSION in {"v2", "v3", "v4"} else "close")
                if action == "ROLL_SHORT_PUT":
                    fill_old = positive_quote(source, product, execution, held_contract,
                                              phase="open" if VERSION in {"v2", "v3", "v4"} else "close")
                if fill_new is None or (action == "ROLL_SHORT_PUT" and fill_old is None):
                    raise RuntimeError(f"{product} {arm} {day} {action}: T+1 model quote absent")
                multiplier = 100 if product == "IM" else 10_000
                if action == "ENTER_SHORT_PUT":
                    cycle_pnl = float(next_anchor["v14_short_put_qty_normalized"]) * fill_new * multiplier
                else:
                    cycle_pnl += (float(next_anchor["v14_short_put_qty_normalized"]) * fill_new
                                  - float(anchor["v14_short_put_qty_normalized"]) * fill_old) * multiplier
                if True:  # execution-cost repair: every new/rolled leg
                    next_anchor["v14_short_put_entry_premium"] = fill_new
            if action in {"SHORT_PUT_EXPIRE_WORTHLESS", "EXIT_RECOVERY_AT_BREAKEVEN"}:
                cycle_pnl = 0.0
                recovery_pnl = 0.0
                recovery_price = None
            if action == "SHORT_PUT_ASSIGNED":
                recovery_price = float(spot.loc[pd.Timestamp(day)]) if product == "IM" else source.ic_on(day)[0]
            pending = {"execution_date": execution, "anchor": next_anchor}
            rows.append({"product": product, "arm": arm, "signal_date": day.isoformat(),
                         "execution_date": execution.isoformat(), "route_before": route_before,
                         "route_after_execution": next_anchor["v14_route_state"], "action": action,
                         "action_reason": out["v14_action_reason"], "held_contract": held_contract,
                         "held_mark_t": held_mark, "candidate_contract": candidate.get("contract"),
                         "candidate_iv": candidate.get("iv"), "candidate_premium_t": candidate.get("premium"),
                         "roll_candidate_contract": None if roll is None else roll.get("contract"),
                         "roll_candidate_iv": None if roll is None else roll.get("iv"),
                         "roll_candidate_premium_t": None if roll is None else roll.get("premium"),
                         "active_contract_after": next_anchor["v14_short_put_contract"],
                         "entry_premium_state_after": next_anchor["v14_short_put_entry_premium"],
                         "early_roll_used_after": next_anchor["v14_early_roll_used"],
                         "settlement": settlement, "fill_old_t1": fill_old, "fill_new_t1": fill_new,
                         "settlement_value": settlement_value,
                         "cycle_pnl_reference": cycle_pnl, "recovery_pnl_reference": recovery_pnl})
    finally:
        formal.v14_policy.EFFECTIVE_SIGNAL_DATE = original_effective
    frame = pd.DataFrame(rows)
    summary = {"product": product, "arm": arm, "rows": len(frame),
               "actions": frame.action.value_counts().to_dict(),
               "model_fill_assumption": ("T+1 dated positive open with market maker; no daily volume limit" if VERSION in {"v2", "v3", "v4"}
                                         else "T+1 dated positive close with market maker; no daily volume limit"),
               "entry_basis": "T close premium for formal decay; T+1 open for cash" if VERSION in {"v2", "v3", "v4"} else "T+1 close premium for decay",
               "scope": "formal seller lifecycle state events only, not 3x or account NAV",
               "formal_sha256": sha256(ROOT / "poe_ic_im_mainline_v1_4_bot.py")}
    return frame, summary


def main() -> None:
    output = HERE / f"native_fix4_seller_lifecycle_{VERSION}.csv.gz"
    summary_path = HERE / f"native_fix4_seller_lifecycle_{VERSION}.json"
    if output.exists() or summary_path.exists():
        raise FileExistsError(output)
    source = DatedSources()
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv", parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    frames, summaries = [], []
    for product in ("IC", "IM"):
        for arm in ("no_seller", "one_roll", "repeat_roll"):
            frame, summary = run(product, arm, source, gov, spot)
            frames.append(frame)
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    pd.concat(frames, ignore_index=True).to_csv(output, index=False, compression="gzip")
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
