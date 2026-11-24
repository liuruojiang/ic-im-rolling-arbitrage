"""Shared r7 calendar policy and same-snapshot quarterly spread diagnostics."""
from __future__ import annotations

import math
import re
from numbers import Real
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
            p = _spread_number(rows.iloc[0].lastprice, f"quote: {c}")
            if p <= 0:
                raise ValueError(f"invalid quote: {c}")
            prices.append(p)
        gap = (expiry(far)-expiry(near)).days
        spread = prices[0]-prices[1]
        ratio = spread/prices[0]
        annualized = ratio*365/gap
        if not all(math.isfinite(x) for x in (spread, ratio, annualized)):
            raise ValueError("nonfinite derived spread")
        result.update(status="ok", near_contract=near, far_contract=far,
                      near_price=prices[0], far_price=prices[1], points=spread,
                      ratio=ratio, annualized=annualized,
                      expiry_gap_days=gap, near_expiry=expiry(near).isoformat(),
                      far_expiry=expiry(far).isoformat())
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as exc:
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

# New report-only diagnostics. Preserve archived signal fields and renderings.
TENOR_SPREAD_EFFECTIVE = date(2026, 11, 20)
TENOR_SPREAD_LABELS = {
    "near_far_month_spread": "近月－远月",
    "near_near_quarter_spread": "近月－近季",
    "near_far_quarter_spread": "近月－远季",
}

INDEX_BASIS_EFFECTIVE = date(2026, 11, 25)
INDEX_BASIS_LABELS = ("近月－指数", "远月－指数", "近季度－指数", "远季度－指数")
INDEX_NAMES = {"IC": "中证500", "IM": "中证1000"}
CLOSE_CURVE_EFFECTIVE = date(2026, 11, 25)
CLOSE_CURVE_ROLES = ("near", "far", "quarter_near", "quarter_far")
CLOSE_CURVE_LABELS = ("近月相对指数", "远月相对指数", "近季度相对指数", "远季度相对指数")
CLOSE_CURVE_NOTE = "贴水口径：正值为贴水，负值为升水。指数为当日收盘；期货为同日最后报价（个别可能早于15:00），不是结算价。"


def _tenor_months(front):
    """Four distinct roles: front, next, and two quarters strictly after next."""
    next_month = front + 1
    first_quarter = next_month + 1 + (2 - (next_month + 1) % 3) % 3
    return (front, next_month, first_quarter, first_quarter + 3)


def _contract_at(product, ordinal):
    return f"{product}{ordinal // 12 % 100:02d}{ordinal % 12 + 1:02d}"


def _validate_tenor_dates(value, label, day, current, near, far):
    if (not isinstance(day, (date, str)) or str(day) != value["source_date"]
            or any(not isinstance(value.get(k), str) for k in
                   ("source_date", "near_contract", "far_contract", "current_month_expiry", "near_expiry", "far_expiry"))
            or value.get("label") != label or value.get("convention") != "near_minus_far"
            or value.get("price_type") != "same_snapshot_lastprice"):
        raise ValueError("spread date or quotation convention mismatch")
    from poe_ic_im_mainline_v1_4_bot import _accepted_calendar_covers, _CALENDAR_POLICY_DAY, _is_exchange_trading_day, _third_friday
    source_day = date.fromisoformat(value["source_date"])
    token = _CALENDAR_POLICY_DAY.set(source_day)
    try:
        if not _accepted_calendar_covers(source_day) or not _is_exchange_trading_day(source_day):
            raise ValueError("spread source date is not an authorized trading session")
        for ordinal, field in ((current, "current_month_expiry"), (near, "near_expiry"), (far, "far_expiry")):
            year, month = ordinal // 12, ordinal % 12 + 1
            actual = _third_friday(year, month)
            if not _accepted_calendar_covers(actual) or value[field] != actual.isoformat():
                raise ValueError("spread expiry does not match authorized calendar")
    finally:
        _CALENDAR_POLICY_DAY.reset(token)


def listed_tenor_spreads(product, quotes, day, expiry):
    """Determine intended roles from the market day, never shift on missing quotes."""
    results = {key: dict(status="unavailable", label=label, convention="near_minus_far",
                         source=str(quotes.attrs.get("source", "unknown")),
                         source_date=str(quotes.attrs.get("source_date", "")),
                         price_type="same_snapshot_lastprice")
               for key, label in TENOR_SPREAD_LABELS.items()}
    try:
        if product not in {"IC", "IM"} or any(v["source_date"] != day.isoformat() for v in results.values()):
            raise ValueError("product or quote date mismatch")
        current = day.year * 12 + day.month - 1
        current_expiry = expiry(_contract_at(product, current))
        front = current + (day > current_expiry)
        contracts = tuple(_contract_at(product, m) for m in _tenor_months(front))
        listed = set(quotes.attrs.get("listed_instruments", quotes.instrument.tolist()))
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError) as exc:
        for value in results.values():
            value["reason"] = str(exc)
        return results
    near = contracts[0]
    for value, far in zip(results.values(), contracts[1:]):
        value.update(near_contract=near, far_contract=far, current_month_expiry=current_expiry.isoformat())
        try:
            prices = []
            for contract in (near, far):
                if contract not in listed:
                    raise ValueError(f"intended listed leg missing: {contract}")
                rows = quotes.loc[quotes.instrument.eq(contract)]
                if len(rows) != 1:
                    raise ValueError(f"missing or duplicate quote: {contract}")
                p = _spread_number(rows.iloc[0].lastprice, f"quote: {contract}")
                if p <= 0:
                    raise ValueError(f"invalid quote: {contract}")
                prices.append(p)
            near_expiry, far_expiry = expiry(near), expiry(far)
            gap = (far_expiry - near_expiry).days
            if near_expiry < day or gap <= 0:
                raise ValueError("invalid expiry interval")
            points = prices[0] - prices[1]
            ratio = points / prices[0]
            annualized = ratio * 365 / gap
            if not all(math.isfinite(x) for x in (points, ratio, annualized, ratio * 100, annualized * 100)):
                raise ValueError("nonfinite derived spread")
            value.update(status="ok", near_price=prices[0], far_price=prices[1],
                         points=points, ratio=ratio, annualized=annualized,
                         same_contract=False, expiry_gap_days=gap,
                         near_expiry=near_expiry.isoformat(), far_expiry=far_expiry.isoformat())
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError, ZeroDivisionError) as exc:
            value["reason"] = str(exc)
    return results


def _spread_number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a real number, not boolean/text")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _validated_month_spread(value, label, product=None, day=None):
    """Validate optional display metadata without mutating a persisted signal."""
    if not isinstance(value, dict) or value.get("status") != "ok":
        raise ValueError(str((value if isinstance(value, dict) else {}).get("reason", "未提供同日完整报价")))
    required = {"source", "source_date", "near_contract", "far_contract", "near_price", "far_price",
                "points", "ratio", "annualized", "same_contract", "near_expiry", "far_expiry", "expiry_gap_days"}
    if required.difference(value):
        raise ValueError("missing spread display fields: " + ",".join(sorted(required.difference(value))))
    if not isinstance(value["source"], str) or not value["source"].strip():
        raise ValueError("spread source must be nonempty text")
    near = re.fullmatch(r"(IC|IM)(\d{2})(\d{2})", str(value.get("near_contract", "")))
    far = re.fullmatch(r"(IC|IM)(\d{2})(\d{2})", str(value.get("far_contract", "")))
    if not near or not far or near[1] != far[1] or (product and near[1] != product):
        raise ValueError("spread contract product mismatch")
    near_month = (2000 + int(near[2])) * 12 + int(near[3]) - 1
    far_month = (2000 + int(far[2])) * 12 + int(far[3]) - 1
    if not (1 <= int(near[3]) <= 12 and 1 <= int(far[3]) <= 12):
        raise ValueError("invalid contract month")
    if label in TENOR_SPREAD_LABELS.values():
        expected_far = _tenor_months(near_month)[list(TENOR_SPREAD_LABELS.values()).index(label) + 1]
    else:
        expected_far = near_month + 1 if label == "当月－次月" else near_month + (3 - int(near[3]) % 3) % 3
    if far_month != expected_far:
        raise ValueError("spread tenor mismatch")
    source_day = date.fromisoformat(str(value["source_date"]))
    if day is not None and source_day != date.fromisoformat(str(day)[:10]):
        raise ValueError("spread source date mismatch")
    near_expiry = date.fromisoformat(str(value["near_expiry"]))
    far_expiry = date.fromisoformat(str(value["far_expiry"]))
    if label in TENOR_SPREAD_LABELS.values():
        current = source_day.year * 12 + source_day.month - 1
        _validate_tenor_dates(value, label, day, current, near_month, far_month)
        current_expiry = date.fromisoformat(str(value.get("current_month_expiry")))
        if (current_expiry.year * 12 + current_expiry.month - 1 != current
                or near_month != current + (source_day > current_expiry)
                or (near_month == current and near_expiry != current_expiry)):
            raise ValueError("spread front-month identity mismatch")
    gap = (far_expiry - near_expiry).days
    if (near_expiry < source_day or near_expiry.year * 12 + near_expiry.month - 1 != near_month
            or far_expiry.year * 12 + far_expiry.month - 1 != far_month or gap < 0
            or type(value.get("expiry_gap_days")) is not int or value["expiry_gap_days"] != gap):
        raise ValueError("spread expiry interval mismatch")
    numbers = {key: _spread_number(value[key], key)
               for key in ("near_price", "far_price", "points", "ratio")}
    if min(numbers["near_price"], numbers["far_price"]) <= 0:
        raise ValueError("spread prices must be positive")
    points = numbers["near_price"] - numbers["far_price"]
    ratio = points / numbers["near_price"]
    if (not math.isfinite(ratio)
            or not math.isclose(numbers["points"], points, rel_tol=1e-10, abs_tol=1e-9)
            or not math.isclose(numbers["ratio"], ratio, rel_tol=1e-10, abs_tol=1e-12)):
        raise ValueError("spread arithmetic mismatch")
    same = value["near_contract"] == value["far_contract"]
    if type(value.get("same_contract")) is not bool or value["same_contract"] != same:
        raise ValueError("spread same-contract flag mismatch")
    if same:
        if gap != 0 or value.get("annualized") is not None or points != 0:
            raise ValueError("same contract cannot have an annualized spread")
    else:
        if gap <= 0:
            raise ValueError("different contracts require a positive expiry interval")
        annualized = _spread_number(value["annualized"], "annualized")
        expected = ratio * 365 / gap
        if not math.isfinite(expected) or not math.isclose(annualized, expected, rel_tol=1e-10, abs_tol=1e-12):
            raise ValueError("spread annualized arithmetic mismatch")
        if label in TENOR_SPREAD_LABELS.values() and not all(math.isfinite(x) for x in (ratio * 100, annualized * 100)):
            raise ValueError("nonfinite percentage display")
    return value


def monthly_spreads(product, quotes, day, expiry):
    """Front listed unexpired month; exact next month and nearest quarter.

    Include the expiry session itself. After expiry the next listed month becomes
    the front month. Missing intended legs never fall back to a different tenor.
    These are report diagnostics only, independent of the held futures contract.
    """
    if day >= TENOR_SPREAD_EFFECTIVE:
        return listed_tenor_spreads(product, quotes, day, expiry)
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
                price = _spread_number(rows.iloc[0].lastprice, f"quote: {contract}")
                if price <= 0:
                    raise ValueError(f"invalid quote: {contract}")
                prices.append(price)
            gap = (expiry(far) - expiry(near)).days
            spread = prices[0] - prices[1]
            ratio = spread / prices[0]
            annualized = ratio * 365 / gap if gap else None
            if not all(math.isfinite(x) for x in (spread, ratio)) or (annualized is not None and not math.isfinite(annualized)):
                raise ValueError("nonfinite derived spread")
            value.update(status="ok", near_price=prices[0], far_price=prices[1],
                         points=spread, ratio=ratio, annualized=annualized,
                         same_contract=near == far, expiry_gap_days=gap,
                         near_expiry=expiry(near).isoformat(), far_expiry=expiry(far).isoformat())
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as exc:
            value["reason"] = str(exc)
        results[key] = value
    return results


def format_month_spread(value, label, *, product=None, day=None):
    prefix = "期限贴水" if label in TENOR_SPREAD_LABELS.values() else "月度价差"
    try:
        value = _validated_month_spread(value, label, product, day)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError, ZeroDivisionError) as exc:
        return f"{prefix}（{label}）：N/A（{exc}）"
    annual = ("同一合约，年化不适用" if value.get("same_contract") else
              f"按到期间隔折算{value['annualized']:+.2%}/年，非保证收益")
    return (f"{prefix}（{label}）：{value['near_contract']} {value['near_price']:.2f}"
            f" − {value['far_contract']} {value['far_price']:.2f} = {value['points']:+.2f}点"
            f"（{value['ratio']:+.2%}；{annual}）；行情 {value['source_date']}，{value['source']}")


def format_spreads(signal, *, product=None):
    if uses_close_curve(signal):
        return format_close_curve(signal, product=product)
    if any(key in signal for key in TENOR_SPREAD_LABELS) or str(signal.get("market_date", ""))[:10] >= TENOR_SPREAD_EFFECTIVE.isoformat():
        lines = format_tenor_spreads(signal, product=product)
        if (signal.get("mode") != "intraday" and signal.get("close_confirmed") is True
                and str(signal.get("market_date", "")) >= INDEX_BASIS_EFFECTIVE.isoformat()):
            lines.extend(format_index_basis(signal, product=product))
        return lines
    lines = [format_spread(signal.get("quarter_spread"))]
    # Archived signals without these diagnostics retain their exact old render.
    if any(key in signal for key in MONTH_SPREAD_LABELS) or str(signal.get("market_date", ""))[:10] >= "2026-10-18":
        lines.extend(format_month_spread(signal.get(key), label, product=product or signal.get("product"),
                                        day=signal.get("market_date")) for key, label in MONTH_SPREAD_LABELS.items())
    return lines


def uses_close_curve(signal):
    return (signal.get("mode") == "close" and signal.get("close_confirmed") is True
            and str(signal.get("market_date", "")) >= CLOSE_CURVE_EFFECTIVE.isoformat())


def _report_price(value, label):
    number = _spread_number(value, label)
    if number <= 0 or len(f"{number:.2f}") > 24 or f"{number:.2f}" == "0.00":
        raise ValueError(f"{label} 报价异常或无法可靠显示")
    return number


def build_close_curve(product, quotes, day, expiry, *, spot, spot_date, spot_source):
    """Report-only independent quote evidence; on delivery day exclude the expiring month."""
    source = quotes.attrs.get("source")
    source_date = str(quotes.attrs.get("source_date", ""))
    curve = dict(schema="close_curve_v2", product=product, market_date=day.isoformat(),
                 source=source, source_date=source_date, price_type="same_snapshot_lastprice",
                 listed_contracts=[], legs={}, index={"status": "unavailable"})
    try:
        if (str(spot_date) != day.isoformat() or not isinstance(spot_source, str)
                or not spot_source.strip()):
            raise ValueError("指数收盘日期或来源未核验")
        curve["index"] = dict(status="ok", price=_report_price(spot, "指数"),
                              source_date=str(spot_date), source=spot_source)
    except (ValueError, TypeError, OverflowError) as exc:
        curve["index"]["reason"] = str(exc)
    try:
        if product not in INDEX_NAMES or source_date != day.isoformat() or not isinstance(source, str) or not source.strip():
            raise ValueError("期货品种、日期或来源未核验")
        current = day.year * 12 + day.month - 1
        current_expiry = expiry(_contract_at(product, current))
        curve["current_month_expiry"] = current_expiry.isoformat()
        front = current + (day >= current_expiry)
        listed = list(quotes.attrs.get("listed_instruments", quotes.instrument.tolist()))
        if not all(isinstance(c, str) for c in listed) or len(listed) != len(set(listed)):
            raise ValueError("挂牌清单异常")
        curve["listed_contracts"] = sorted(listed)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError) as exc:
        curve["reason"] = str(exc)
        return curve
    for role, ordinal in zip(CLOSE_CURVE_ROLES, _tenor_months(front)):
        contract = _contract_at(product, ordinal)
        leg = curve["legs"][role] = dict(status="unavailable", contract=contract)
        try:
            if contract not in listed:
                raise ValueError("目标合约尚未实际挂牌")
            rows = quotes.loc[quotes.instrument.eq(contract)]
            if len(rows) != 1:
                raise ValueError("缺失或重复同日报价")
            price = _report_price(rows.iloc[0].lastprice, contract)
            expiration = expiry(contract)
            if expiration <= day:
                raise ValueError("合约未严格晚于收盘日")
            leg.update(status="ok", price=price, expiry=expiration.isoformat())
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError) as exc:
            leg["reason"] = str(exc)
    return curve


def _report_calendar_expiry(product, day, contract):
    from poe_ic_im_mainline_v1_4_bot import _accepted_calendar_covers, _CALENDAR_POLICY_DAY, _SIGNAL_CALENDAR_DIAGNOSTICS, _is_exchange_trading_day, _third_friday
    token = _CALENDAR_POLICY_DAY.set(day)
    diagnostic_token = _SIGNAL_CALENDAR_DIAGNOSTICS.set(None)
    try:
        if not _accepted_calendar_covers(day) or not _is_exchange_trading_day(day):
            raise ValueError("行情日未获交易日历授权")
        expiration = _third_friday(2000 + int(contract[2:4]), int(contract[-2:]))
        if not _accepted_calendar_covers(expiration):
            raise ValueError("到期日历未获授权")
        return expiration
    finally:
        _SIGNAL_CALENDAR_DIAGNOSTICS.reset(diagnostic_token)
        _CALENDAR_POLICY_DAY.reset(token)


def _curve_arithmetic(anchor, future, days):
    points = anchor - future
    ratio = points / anchor
    annual = ratio * 365 / days
    if (days <= 0 or not all(math.isfinite(x) for x in (points, ratio * 100, annual * 100))
            or any(len(x) > 24 for x in (f"{points:+.2f}", f"{ratio:+.2%}", f"{annual:+.2%}"))):
        raise ValueError("贴水数值或期间异常，无法可靠显示")
    return points, ratio, annual


def format_close_curve(signal, *, product=None):
    """Independently revalidate each leg; a missing index or leg cannot hide other available legs."""
    product = product or signal.get("product")
    labels = tuple(TENOR_SPREAD_LABELS.values()) + CLOSE_CURVE_LABELS
    def na(index, reason):
        prefix = "期限贴水" if index < 3 else "期现贴水"
        return f"{prefix}（{labels[index]}）：N/A（{reason}）"
    curve = signal.get("close_report_curve")
    try:
        if not uses_close_curve(signal) or not isinstance(curve, dict) or curve.get("schema") != "close_curve_v2":
            raise ValueError("未提供已核验收盘期限结构")
        day = date.fromisoformat(str(signal.get("market_date")))
        if (product not in INDEX_NAMES or product != signal.get("product") or product != curve.get("product")
                or curve.get("market_date") != day.isoformat() or curve.get("source_date") != day.isoformat()
                or str(signal.get("future_quote_date")) != day.isoformat() or curve.get("source") != signal.get("future_quote_source")
                or not isinstance(curve.get("source"), str) or not curve["source"].strip()
                or curve.get("price_type") != "same_snapshot_lastprice"):
            raise ValueError("收盘期限结构的日期、品种或来源不一致")
        current = day.year * 12 + day.month - 1
        current_expiry = _report_calendar_expiry(product, day, _contract_at(product, current))
        if curve.get("current_month_expiry") != current_expiry.isoformat():
            raise ValueError("当月实际交割日不一致")
        contracts = [_contract_at(product, m) for m in _tenor_months(current + (day >= current_expiry))]
        listed = curve.get("listed_contracts")
        if not isinstance(listed, list) or not all(isinstance(c, str) for c in listed) or len(listed) != len(set(listed)):
            raise ValueError("挂牌清单异常")
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError) as exc:
        return [na(i, exc) for i in range(7)] + [CLOSE_CURVE_NOTE]
    legs, errors = {}, {}
    for i, (role, contract) in enumerate(zip(CLOSE_CURVE_ROLES, contracts)):
        try:
            value = curve.get("legs", {}).get(role)
            if not isinstance(value, dict) or value.get("contract") != contract:
                raise ValueError("目标合约身份缺失或不一致")
            if contract not in listed:
                raise ValueError("目标合约尚未实际挂牌")
            if value.get("status") != "ok":
                raise ValueError(str(value.get("reason", "无可验证报价")))
            expiration = _report_calendar_expiry(product, day, contract)
            if value.get("expiry") != expiration.isoformat() or expiration <= day:
                raise ValueError("到期日历不一致或合约已交割")
            legs[i] = (_report_price(value.get("price"), contract), expiration)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError) as exc:
            errors[i] = f"{contract}：{exc}"
    try:
        value = curve.get("index")
        if not isinstance(value, dict) or value.get("status") != "ok" or value.get("source_date") != day.isoformat() or not isinstance(value.get("source"), str) or not value["source"].strip():
            raise ValueError("指数收盘日期、来源或价格未核验")
        spot = _report_price(value.get("price"), "指数")
        if spot != _report_price(signal.get("index_price"), "指数"):
            raise ValueError("指数收盘价格与信号不一致")
        index_error = None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        index_error = str(exc)
    lines = []
    for i in range(7):
        leg = i + 1 if i < 3 else i - 3
        try:
            if leg not in legs:
                raise ValueError(errors[leg])
            future, expiration = legs[leg]
            if i < 3:
                if 0 not in legs:
                    raise ValueError(errors[0])
                anchor, anchor_expiry = legs[0]
                name = contracts[0]
                days = (expiration - anchor_expiry).days
                prefix = "期限贴水"
            else:
                if index_error:
                    raise ValueError(index_error)
                anchor, name, days = spot, INDEX_NAMES[product], (expiration - day).days
                prefix = "期现贴水"
            points, ratio, annual = _curve_arithmetic(anchor, future, days)
            duration = "到期间隔" if i < 3 else "距到期"
            lines.append(f"{prefix}（{labels[i]}）：{name} {anchor:.2f} − {contracts[leg]} {future:.2f} = {points:+.2f}点"
                         f"（{ratio:+.2%}；{duration} {days}天；年化 {annual:+.2%}/年，非保证收益）；"
                         f"行情 {day}，期货报价 {curve['source']}")
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError) as exc:
            lines.append(na(i, exc))
    return lines + [CLOSE_CURVE_NOTE]


def format_tenor_spreads(signal, *, product=None):
    checks = {}
    for key, label in TENOR_SPREAD_LABELS.items():
        try:
            checks[key] = _validated_month_spread(signal.get(key), label, product or signal.get("product"), signal.get("market_date"))
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError, ZeroDivisionError):
            continue
    anchors = {tuple(v[k] for k in ("near_contract", "near_price", "near_expiry", "current_month_expiry", "source", "source_date")) for v in checks.values()}
    if len(anchors) > 1:
        return [f"期限贴水（{label}）：N/A（近月基准或行情来源不一致）" for label in TENOR_SPREAD_LABELS.values()]
    return [format_month_spread(signal.get(key), label, product=product or signal.get("product"), day=signal.get("market_date"))
            for key, label in TENOR_SPREAD_LABELS.items()]


def format_index_basis(signal, *, product=None):
    """Close-only display derived from existing, validated same-day signal evidence.

    Do not mutate the signal or infer missing quotations from other contracts.
    """
    def unavailable(label, reason):
        return f"期现贴水（{label}）：N/A（{reason}）"

    product = product or signal.get("product")
    try:
        if product not in INDEX_NAMES or signal.get("product") != product or signal.get("close_confirmed") is not True:
            raise ValueError("缺少对应品种的收盘确认")
        day = date.fromisoformat(str(signal.get("market_date")))
        if str(signal.get("future_quote_date")) != day.isoformat():
            raise ValueError("期货行情日与指数收盘日不一致")
        spot = _spread_number(signal.get("index_price"), "index_price")
        if spot <= 0:
            raise ValueError("指数价格必须为正")
    except (ValueError, TypeError, OverflowError) as exc:
        return [unavailable(label, exc) for label in INDEX_BASIS_LABELS]

    checks = {}
    for index, (key, label) in enumerate(TENOR_SPREAD_LABELS.items(), 1):
        try:
            checks[index] = _validated_month_spread(signal.get(key), label, product, day)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError, ZeroDivisionError):
            continue
    anchors = {tuple(v[k] for k in ("near_contract", "near_price", "near_expiry", "current_month_expiry", "source", "source_date"))
               for v in checks.values()}
    if len(anchors) > 1 or any(v["source"] != signal.get("future_quote_source") for v in checks.values()):
        return [unavailable(label, "近月基准或行情来源不一致") for label in INDEX_BASIS_LABELS]

    lines = []
    for index, label in enumerate(INDEX_BASIS_LABELS):
        value = next(iter(checks.values()), None) if index == 0 else checks.get(index)
        if value is None:
            lines.append(unavailable(label, "未提供可核验的同日合约报价"))
            continue
        leg = "near" if index == 0 else "far"
        contract = value[f"{leg}_contract"]
        future = value[f"{leg}_price"]
        days = (date.fromisoformat(value[f"{leg}_expiry"]) - day).days
        points = spot - future
        ratio = points / spot
        annualized = ratio * 365 / days if days else None
        if (days < 0 or not all(math.isfinite(x) for x in (points, ratio, ratio * 100))
                or (annualized is not None and not math.isfinite(annualized * 100))):
            lines.append(unavailable(label, "贴水或到期日无效"))
            continue
        annual = "年化 N/A（交割当日）" if annualized is None else f"年化 {annualized:+.2%}/年"
        lines.append(f"期现贴水（{label}）：{INDEX_NAMES[product]} {spot:.2f} − {contract} {future:.2f}"
                     f" = {points:+.2f}点（{ratio:+.2%}；距到期 {days}天；{annual}，非保证收益）；"
                     f"行情 {day}，期货报价 {value['source']}")
    return lines
