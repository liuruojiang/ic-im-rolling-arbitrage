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


MONTH_SPREAD_LABELS = {
    "month_next_spread": "当月－次月",
    "month_quarter_spread": "当月－当季",
}


def monthly_spreads(product, quotes, day, expiry):
    """Front listed unexpired month; exact next month and nearest quarter.

    Include the expiry session itself. After expiry the next listed month becomes
    the front month. Missing intended legs never fall back to a different tenor.
    These are report diagnostics only, independent of the held futures contract.
    """
    results = {}
    for key, label in MONTH_SPREAD_LABELS.items():
        value = dict(status="unavailable", convention="near_minus_far", label=label,
                     source=str(quotes.attrs.get("source", "unknown")),
                     source_date=str(quotes.attrs.get("source_date", "")),
                     price_type="same_snapshot_lastprice")
        try:
            if value["source_date"][:10] != day.isoformat():
                raise ValueError("quote date mismatch")
            listed = quotes.attrs.get("listed_instruments", quotes.instrument.tolist())
            listed = sorted({str(c) for c in listed
                             if re.fullmatch(rf"{product}\d{{4}}", str(c))
                             and 1 <= int(str(c)[-2:]) <= 12
                             and expiry(str(c)) >= day}, key=expiry)
            if not listed:
                raise ValueError("unexpired listed front month required")
            near = listed[0]
            if key == "month_next_spread":
                year, month = 2000 + int(near[2:4]), int(near[-2:])
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
                far = f"{product}{year % 100:02d}{month:02d}"
            else:
                quarters = quarter_contracts(product, listed)
                if not quarters:
                    raise ValueError("unexpired listed current quarter required")
                far = quarters[0]
                if (expiry(far).year * 12 + expiry(far).month) - (expiry(near).year * 12 + expiry(near).month) > 2:
                    raise ValueError("current quarter missing; cannot skip")
            value.update(near_contract=near, far_contract=far)
            if far not in listed:
                raise ValueError(f"intended listed leg missing: {far}")
            prices = []
            for contract in (near, far):
                rows = quotes.loc[quotes.instrument.eq(contract)]
                if len(rows) != 1:
                    raise ValueError(f"missing or duplicate quote: {contract}")
                price = float(rows.iloc[0].lastprice)
                if not math.isfinite(price) or price <= 0:
                    raise ValueError(f"invalid quote: {contract}")
                prices.append(price)
            gap = (expiry(far) - expiry(near)).days
            spread = prices[0] - prices[1]
            value.update(status="ok", near_price=prices[0], far_price=prices[1],
                         points=spread, ratio=spread / prices[0],
                         annualized=spread / prices[0] * 365 / gap if gap else None,
                         same_contract=near == far, expiry_gap_days=gap,
                         near_expiry=expiry(near).isoformat(), far_expiry=expiry(far).isoformat())
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            value["reason"] = str(exc)
        results[key] = value
    return results


def format_month_spread(value, label):
    if not value or value.get("status") != "ok":
        return f"月度价差（{label}）：N/A（" + str((value or {}).get("reason", "未提供同日完整报价")) + "）"
    annual = ("同一合约，年化不适用" if value.get("same_contract") else
              f"按到期间隔折算{value['annualized']:+.2%}/年，非保证收益")
    return (f"月度价差（{label}）：{value['near_contract']} {value['near_price']:.2f}"
            f" − {value['far_contract']} {value['far_price']:.2f} = {value['points']:+.2f}点"
            f"（{value['ratio']:+.2%}；{annual}）；行情 {value['source_date']}，{value['source']}")


def format_spreads(signal):
    return [format_spread(signal.get("quarter_spread")),
            *[format_month_spread(signal.get(key), label) for key, label in MONTH_SPREAD_LABELS.items()]]
