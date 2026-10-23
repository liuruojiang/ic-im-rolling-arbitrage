"""Independent delivery checks and display for causal Put opening evidence.

This validates a producer snapshot; only the strategy ledger binds a plan to
the prior persisted record.  No quotes, fills, account actions or IO occur here.
"""
from datetime import date
import math
import re
from typing import Any

REVISION = "put_execution_order_and_short_basis_20261016_v1"
EFFECTIVE_DATE = date(2026, 10, 16)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} requires a positive finite number")
    return float(value)


def _day(value: Any, label: str, is_session) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} requires an ISO date")
    result = date.fromisoformat(value)
    if not is_session(result):
        raise ValueError(f"{label} is not a common exchange session")
    return result


def _identity(product: str, row: Any) -> tuple:
    if not isinstance(row, dict) or row.get("kind") not in {"option", "future"}:
        raise ValueError("opening quote identity requires an option/future kind")
    contract, security, kind = row.get("contract"), row.get("security_id"), row["kind"]
    if not isinstance(contract, str) or not contract:
        raise ValueError("opening quote lacks a contract")
    pattern = (rf"{product}\d{{4}}" if kind == "future" else
               r"510500P\d{4}M\d{5}" if product == "IC" else r"MO\d{4}-P-\d+")
    if not re.fullmatch(pattern, contract):
        raise ValueError("opening contract is not the product's Put/future")
    if kind == "option" and product == "IC":
        if not isinstance(security, str) or not security.strip():
            raise ValueError("IC option opening identity lacks a security id")
    elif security is not None:
        raise ValueError("future/IM opening identity must not invent an ETF security id")
    return contract, security, kind


def _plan(product: str, plan: Any, kind: str, day: date, is_session, adjacent_session) -> tuple:
    if not isinstance(plan, dict) or plan.get("product") != product:
        raise ValueError("opening plan lacks its product identity")
    signal = _day(plan.get("signal_day"), "opening signal day", is_session)
    execution = _day(plan.get("execution_day"), "opening execution day", is_session)
    if signal > day or execution != adjacent_session(signal):
        raise ValueError("opening plan is not committed for the next actual session")
    new_qty = _number(plan.get("new_qty"), "opening new quantity")
    if product == "IC" and not new_qty.is_integer():
        raise ValueError("IC opening quantity must be integral")
    new = _identity(product, dict(contract=plan.get("new_contract"),
                                security_id=plan.get("new_security_id"), kind="option"))
    expected = {new}
    if kind == "short_put":
        if plan.get("execution_revision") != REVISION:
            raise ValueError("short-Put opening plan revision mismatch")
        _number(plan.get("signal_premium"), "short-Put candidate premium")
        previous = plan.get("previous_anchor")
        if not isinstance(previous, dict) or previous.get("v14_short_put_open_pending") is not None:
            raise ValueError("short-Put opening plan lacks a non-nested prior state")
        action = plan.get("action")
        if action == "ROLL_SHORT_PUT":
            if previous.get("v14_route_state") != "short_put":
                raise ValueError("short-Put roll must start from the short-Put route")
            _number(previous.get("v14_short_put_qty_normalized"), "prior short-Put quantity")
            _number(previous.get("v14_short_put_entry_premium"), "prior short-Put entry cost")
            expected.add(_identity(product, dict(contract=previous.get("v14_short_put_contract"),
                                                 security_id=previous.get("v14_short_put_security_id"), kind="option")))
        elif action == "ENTER_SHORT_PUT":
            if previous.get("v14_route_state") != "future":
                raise ValueError("short-Put entry must start from the fixed future")
            expected.add(_identity(product, dict(contract=previous.get("post_core_contract"),
                                                 security_id=None, kind="future")))
            qty = previous.get("v14_core_put_qty", 0) if product == "IC" else previous.get("verified_core_put_qty_normalized", 0)
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty < 0:
                raise ValueError("prior protective Put quantity is invalid")
            if qty > 0:
                expected.add(_identity(product, dict(contract=previous.get("v14_core_put_contract") if product == "IC" else previous.get("post_core_put_contract"),
                                                     security_id=previous.get("v14_core_put_security_id") if product == "IC" else None, kind="option")))
        else:
            raise ValueError("short-Put opening action is invalid")
    elif kind == "profit3x":
        old_qty = _number(plan.get("old_qty"), "three-times old quantity")
        if product == "IC" and not old_qty.is_integer():
            raise ValueError("IC prior opening quantity must be integral")
        expected.add(_identity(product, dict(contract=plan.get("old_contract"),
                                             security_id=plan.get("old_security_id"), kind="option")))
    else:
        raise ValueError("unknown Put opening event kind")
    quotes = plan.get("required_quotes")
    if not isinstance(quotes, list) or not quotes:
        raise ValueError("opening plan lacks required quote identities")
    identities = [_identity(product, row) for row in quotes]
    if len(identities) != len(set(identities)) or set(identities) != expected:
        raise ValueError("opening plan does not bind exactly every required leg")
    return execution, expected, new, new_qty


def validate_execution(product: str, signal: dict, *, is_session, adjacent_session) -> None:
    day = date.fromisoformat(signal["market_date"])
    fields = {"v14_execution_revision", "v14_opening_events", "v14_short_put_open_pending",
              "v14_short_put_cost_basis", "v14_short_put_entry_price_day", "v14_short_put_entry_premium"}
    if day < EFFECTIVE_DATE and not any(signal.get(field) is not None for field in fields - {"v14_short_put_entry_premium"}):
        return
    if signal.get("v14_execution_revision") != REVISION or fields - signal.keys():
        raise ValueError("Put opening revision/state missing from current producer")
    events = signal["v14_opening_events"]
    if not isinstance(events, list):
        raise ValueError("Put opening events must be a list")
    seen = set()
    confirmed_short = []
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in {"short_put", "profit3x"}:
            raise ValueError("Put opening event lacks its kind")
        kind = event["kind"]
        if kind in seen:
            raise ValueError("duplicate Put opening event kind")
        seen.add(kind)
        execution, expected, new, qty = _plan(product, event.get("plan"), kind, day, is_session, adjacent_session)
        status = event.get("status")
        if status == "confirmed_open_research_price":
            if execution != day:
                raise ValueError("confirmed opening event is not on its execution day")
            prices = event.get("prices")
            if not isinstance(prices, list):
                raise ValueError("confirmed opening event lacks price evidence")
            observed = {}
            for row in prices:
                key = _identity(product, row)
                if key in observed or _day(row.get("price_day"), "opening price day", is_session) != execution:
                    raise ValueError("opening prices are duplicate or on the wrong day")
                if not isinstance(row.get("source"), str) or not row["source"].strip():
                    raise ValueError("opening price lacks its source")
                observed[key] = _number(row.get("openprice"), "dated opening price")
            if set(observed) != expected:
                raise ValueError("opening prices do not match every committed identity")
            if kind == "short_put":
                confirmed_short.append((new, observed[new]))
        elif status == "awaiting_execution_day":
            if kind != "short_put" or day >= execution or event.get("prices"):
                raise ValueError("awaiting opening event has the wrong date/state")
        elif status == "closed_missing_open_price":
            if execution != day or not isinstance(event.get("reason"), str) or not event["reason"].strip():
                raise ValueError("missing opening evidence requires its execution day and reason")
        elif status == "closed_missed_execution_day":
            if kind != "short_put" or day <= execution or event.get("prices"):
                raise ValueError("missed opening event has the wrong date/state")
        else:
            raise ValueError("unknown Put opening status")
    pending = signal["v14_short_put_open_pending"]
    basis = signal["v14_short_put_cost_basis"]
    premium = signal.get("v14_short_put_entry_premium")
    price_day = signal["v14_short_put_entry_price_day"]
    if basis not in {None, "pending_dated_open", "confirmed_dated_open"}:
        raise ValueError("unknown short-Put cost basis")
    if signal.get("v14_route_state") == "short_put":
        _number(signal.get("v14_short_put_qty_normalized"), "held short-Put quantity")
        _identity(product, dict(contract=signal.get("v14_short_put_contract"),
                                security_id=signal.get("v14_short_put_security_id"), kind="option"))
        if pending is None:
            _number(premium, "held short-Put cost")
    if pending is not None:
        execution, _, new, qty = _plan(product, pending, "short_put", day, is_session, adjacent_session)
        held = (signal.get("v14_short_put_contract"), signal.get("v14_short_put_security_id"), "option")
        if (execution <= day or signal.get("v14_route_state") != "short_put" or basis != "pending_dated_open"
                or premium is not None or price_day is not None or held != new
                or signal.get("v14_short_put_qty_normalized") != qty):
            raise ValueError("pending short-Put candidate cannot be a confirmed entry cost")
    elif basis == "pending_dated_open":
        raise ValueError("pending short-Put cost lacks its opening plan")
    elif basis == "confirmed_dated_open":
        entry = _number(premium, "confirmed short-Put entry cost")
        entry_day = _day(price_day, "short-Put entry price day", is_session)
        if signal.get("v14_route_state") != "short_put" or entry_day > day:
            raise ValueError("confirmed short-Put cost has the wrong route/day")
        held = _identity(product, dict(contract=signal.get("v14_short_put_contract"),
                                      security_id=signal.get("v14_short_put_security_id"), kind="option"))
        if entry_day == day and (held, entry) not in confirmed_short:
            raise ValueError("new confirmed short-Put cost lacks its matching opening event")
    elif price_day is not None:
        raise ValueError("unverified short-Put cost cannot carry a confirmed price day")


def needs_attention(signal: dict) -> bool:
    return bool(signal.get("v14_short_put_open_pending") or
                (signal.get("v14_execution_revision") == REVISION and
                 signal.get("v14_route_state") == "short_put" and
                 signal.get("v14_short_put_cost_basis") != "confirmed_dated_open") or
                any(event.get("status") != "confirmed_open_research_price"
                    for event in signal.get("v14_opening_events", [])))


def display_lines(product: str, signal: dict) -> list[str]:
    lines = []
    for event in signal.get("v14_opening_events", []):
        title = "核心买Put三倍兑现" if event["kind"] == "profit3x" else "卖Put成本"
        plan, status = event["plan"], event["status"]
        text = f"{product} {title}独立开盘事件：{plan['execution_day']}；{status}。"
        if status == "confirmed_open_research_price":
            text += "指定日开盘证据：" + "、".join(
                f"{row['contract']} @ {row['openprice']}（{row['price_day']}，{row['source']}）" for row in event["prices"]) + "。"
        else:
            text += f"未形成已核验开盘成交；{event.get('reason', '仍待指定执行日')}。"
        text += "开盘事件先于本日收盘新目标；仅研究核验，不是账户成交。"
        lines.append(text)
    pending = signal.get("v14_short_put_open_pending")
    if pending:
        lines.append(f"{product} 卖Put开盘计划待核验：{pending['execution_day']}，新腿{pending['new_contract']}，"
                     f"数量{pending['new_qty']:g}；候选收盘权利金{pending['signal_premium']:g}不是入场成本；"
                     "成本尚未确认，不能启用权利金衰减展期。")
    elif signal.get("v14_short_put_cost_basis") == "confirmed_dated_open":
        lines.append(f"{product} 卖Put入场成本：{signal['v14_short_put_entry_premium']:g}，"
                     f"证据日期{signal['v14_short_put_entry_price_day']}，confirmed_dated_open。")
    elif signal.get("v14_route_state") == "short_put" and signal.get("v14_execution_revision") == REVISION:
        lines.append(f"{product} 卖Put历史入场成本尚未取得指定日开盘验证，不能据候选收盘价启用衰减展期。")
    return lines
