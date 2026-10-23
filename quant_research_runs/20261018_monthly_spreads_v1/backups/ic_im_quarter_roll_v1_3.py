"""Shared r7 calendar policy and same-snapshot quarterly spread diagnostics."""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

REVISION = "r7"
EFFECTIVE_DATE = date(2026, 9, 4)
ROLL_DAYS = {"IC": 3, "IM": 1}


def policy(product):
    return dict(tenor="strict_quarter", trading_days_before_expiry=ROLL_DAYS[product],
                execution="close", effective_date=EFFECTIVE_DATE.isoformat(),
                options="original_independent_monthly_maintenance")


def shift_session(day, count, is_session):
    step = 1 if count >= 0 else -1
    for _ in range(abs(count)):
        day += timedelta(days=step)
        for _ in range(370):
            if is_session(day):
                break
            day += timedelta(days=step)
        else:
            raise RuntimeError("No covered exchange session")
    return day


def quarter_contracts(product, listed):
    return sorted({str(c) for c in listed
                   if re.fullmatch(rf"{product}\d{{4}}", str(c))
                   and int(str(c)[-2:]) in (3, 6, 9, 12)})


def next_quarter(product, listed, held, expiry):
    later = [c for c in quarter_contracts(product, listed) if expiry(c) > expiry(held)]
    if not later:
        raise RuntimeError(f"{product}: no later listed quarter after {held}")
    held_month = int(held[2:4])*12+int(held[-2:])
    target_month = (held_month//3+1)*3
    if int(later[0][2:4])*12+int(later[0][-2:]) != target_month:
        raise RuntimeError(f"{product}: nearest next quarter missing after {held}; cannot skip")
    return later[0]


def roll_state(product, held, listed, day, close_confirmed, expiry, is_session):
    execution = shift_session(expiry(held), -ROLL_DAYS[product], is_session)
    preview = shift_session(execution, -1, is_session)
    if day > execution:
        raise RuntimeError(f"{product} ledger missed quarter roll {held} at {execution}; replay required")
    destination = next_quarter(product, listed, held, expiry)
    due = day == execution or (day == preview and close_confirmed)
    completed = day == execution and close_confirmed
    return dict(core_current=held, core_target=destination if due else held,
                core_eod_contract=destination if completed else held,
                core_action="ROLL" if due else "HOLD", next_core=destination,
                roll_execution_date=execution, roll_date=expiry(held),
                roll_signal_due=due, roll_confirmed=completed,
                roll_policy=policy(product))


def quarter_spread(product, quotes, day, expiry):
    """No fallback to a different month or stale snapshot when the pair is absent."""
    result = dict(status="unavailable", convention="near_minus_far",
                  source=str(quotes.attrs.get("source", "unknown")),
                  source_date=str(quotes.attrs.get("source_date", "")),
                  price_type="same_snapshot_lastprice")
    try:
        if result["source_date"][:10] != day.isoformat():
            raise ValueError("quote date mismatch")
        listed = quotes.attrs.get("listed_instruments", quotes.instrument.tolist())
        pair = [c for c in quarter_contracts(product, listed) if expiry(c) >= day][:2]
        if len(pair) != 2:
            raise ValueError("two unexpired listed quarters required")
        near, far = pair
        if (int(far[2:4])*12+int(far[-2:]))-(int(near[2:4])*12+int(near[-2:])) != 3:
            raise ValueError("adjacent quarterly leg missing")
        prices = []
        for c in pair:
            rows = quotes.loc[quotes.instrument.eq(c)]
            if len(rows) != 1:
                raise ValueError(f"missing or duplicate quote: {c}")
            p = float(rows.iloc[0].lastprice)
            if not math.isfinite(p) or p <= 0:
                raise ValueError(f"invalid quote: {c}")
            prices.append(p)
        gap = (expiry(far)-expiry(near)).days
        spread = prices[0]-prices[1]
        result.update(status="ok", near_contract=near, far_contract=far,
                      near_price=prices[0], far_price=prices[1], points=spread,
                      ratio=spread/prices[0], annualized=spread/prices[0]*365/gap,
                      expiry_gap_days=gap, near_expiry=expiry(near).isoformat(),
                      far_expiry=expiry(far).isoformat())
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        result["reason"] = str(exc)
    return result


def format_spread(value):
    if not value or value.get("status") != "ok":
        return "季月价差：N/A（" + str((value or {}).get("reason", "未提供同日完整报价")) + "）"
    return (f"季月价差（近季－远季）：{value['near_contract']} {value['near_price']:.2f}"
            f" − {value['far_contract']} {value['far_price']:.2f} = {value['points']:+.2f}点"
            f"（{value['ratio']:+.2%}；按到期间隔折算{value['annualized']:+.2%}/年，非保证收益）；"
            f"行情 {value['source_date']}，{value['source']}")
