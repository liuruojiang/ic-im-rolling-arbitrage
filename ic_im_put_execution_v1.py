"""Causal opening projections; no filesystem, network or account execution."""
from __future__ import annotations

import math
import re
from copy import deepcopy
from datetime import date
from typing import Any

REVISION = "put_execution_order_and_short_basis_20261016_v1"
EFFECTIVE_DATE = date(2026, 10, 16)
OPEN_EVIDENCE_REVISION = "put_open_evidence_retry_20261021_v1"
OPEN_EVIDENCE_EFFECTIVE_DATE = date(2026, 10, 21)
RETRY_OPEN_STATUS = "awaiting_retry_open_evidence"


def require_resolved_held_identity(product: str, value: dict) -> None:
    """A known failed holding-identity check cannot become a new target/write."""
    if product == "IC" and value.get("v14_held_put_identity_status") == "unverified":
        raise RuntimeError("unresolved held Put identity; preserve anchor and retry verified evidence")


def require_supported_ic_contracts(product: str, value: dict) -> None:
    """Do not infer adjusted strike/contract unit from a trading-code suffix.

    Standard new openings remain supported. A holdings need official dated
    contract master data before the current M-only sizing engine can use them.
    Frozen records remain readable; this gate is only for new calculations.
    """
    if product != "IC":
        return
    for key in ("v14_core_put_contract", "post_put_contract", "put_current_contract",
                "put_target_contract", "v14_short_put_contract", "v14_profit_old_contract",
                "v14_profit_reentry_contract"):
        if re.fullmatch(r"510500P\d{4}A\d{5}", str(value.get(key) or "")):
            raise RuntimeError("IC adjusted contract master and multiplier unavailable; preserve held A identity")
    for name in ("v14_ordinary_put_pending", "v14_short_put_open_pending"):
        plan = value.get(name)
        if not isinstance(plan, dict):
            continue
        contracts = [plan.get("new_contract")]
        for leg in (plan.get("legs") or {}).values():
            contracts.extend([leg.get("old_contract"), leg.get("new_contract")])
        contracts.extend(row.get("contract") for row in plan.get("required_quotes") or [])
        if any(re.fullmatch(r"510500P\d{4}A\d{5}", str(c or "")) for c in contracts):
            raise RuntimeError("IC adjusted contract master and multiplier unavailable; preserve original plan")


def require_resolved_opening_evidence(signal: dict) -> None:
    """Unverified inputs cannot become new targets or a persisted close.

    This guard concerns new computation, not reading frozen ledger records.
    A transient failure is not an authoritative no-fill decision.
    """
    statuses = [signal.get("v14_ordinary_put_open_status")]
    for event in signal.get("v14_opening_events") or []:
        statuses.append(event.get("status"))
    if RETRY_OPEN_STATUS in statuses:
        raise RuntimeError("unresolved opening evidence: preserve the exact original plan and retry")


def require_due_openings(product: str, anchor: dict, signal: dict, day: date,
                         monthly_priority: bool) -> None:
    """Independent new-write check, including omitted/forged closed outcomes."""
    require_resolved_opening_evidence(signal)
    if day < OPEN_EVIDENCE_EFFECTIVE_DATE:
        return
    ordinary = anchor.get("v14_ordinary_put_pending")
    if ordinary and as_day(ordinary["execution_day"]) <= day and not monthly_priority:
        if (signal.get("v14_ordinary_put_open_status") != "confirmed_open_research_price"
                or not same_plan(signal.get("v14_ordinary_put_open_plan"), ordinary)):
            raise RuntimeError(f"{product} unresolved opening evidence: ordinary Put plan is still due")
    due = {}
    if (anchor.get("v14_profit_pending") and anchor.get("v14_profit_execution_day")
            and as_day(anchor["v14_profit_execution_day"]) < day):
        raise RuntimeError(f"{product} unresolved opening evidence: replay the original 3x execution day")
    short = anchor.get("v14_short_put_open_pending")
    if short and as_day(short["execution_day"]) <= day:
        due["short_put"] = short
    profit = profit_plan(product, anchor, day)
    if profit and not monthly_priority:
        due["profit3x"] = profit
    events = signal.get("v14_opening_events") or []
    for kind, plan in due.items():
        matches = [event for event in events if event.get("kind") == kind]
        if (len(matches) != 1 or matches[0].get("status") != "confirmed_open_research_price"
                or not same_plan(matches[0].get("plan"), plan)):
            raise RuntimeError(f"{product} unresolved opening evidence: {kind} plan is still due")


def as_day(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def same_plan(left: Any, right: Any) -> bool:
    def normalize(value):
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value
    return normalize(left) == normalize(right)


def active(day: Any) -> bool:
    return as_day(day) >= EFFECTIVE_DATE


def checked_prices(plan: dict, prices: list[dict], day: date) -> dict[tuple, float]:
    if as_day(plan["execution_day"]) != day:
        raise RuntimeError("Put opening evidence is not on the committed execution day")
    expected = {(r["contract"], r.get("security_id"), r.get("kind", "option"))
                for r in plan["required_quotes"]}
    result = {}
    for row in prices:
        key = (row["contract"], row.get("security_id"), row.get("kind", "option"))
        price = float(row["openprice"])
        if (key in result or as_day(row["price_day"]) != day
                or not math.isfinite(price) or price <= 0
                or not str(row.get("source") or "").strip()):
            raise RuntimeError("Put opening evidence is duplicate, undated or invalid")
        result[key] = price
    if set(result) != expected:
        raise RuntimeError("Put opening evidence does not match every committed leg")
    return result


def short_plan(product: str, signal: dict, anchor: dict, target: dict, action: str) -> dict:
    contract = target["v14_short_put_contract"]
    # A calendar roll completed at T close changes the future exited at T+1
    # open. Keep the pre-close snapshot intact for independent ledger binding.
    close_core = signal.get("core_eod_contract") or anchor.get("post_core_contract") or signal.get("core_current")
    required = [dict(contract=contract, security_id=target.get("v14_short_put_security_id"), kind="option")]
    if action == "ROLL_SHORT_PUT":
        required.append(dict(contract=anchor["v14_short_put_contract"],
                             security_id=anchor.get("v14_short_put_security_id"), kind="option"))
    else:
        # The fixed future and its protective Put leave together with entry.
        future = close_core
        if not future:
            raise RuntimeError("Short-Put opening plan lacks the held fixed future")
        required.append(dict(contract=future, security_id=None, kind="future"))
        qty = float(anchor.get("v14_core_put_qty", 0) if product == "IC" else
                    anchor.get("verified_core_put_qty_normalized", 0))
        core = anchor.get("v14_core_put_contract") if product == "IC" else anchor.get("post_core_put_contract")
        if qty > 0:
            if not core:
                raise RuntimeError("Short-Put plan lacks the protective core identity")
            required.append(dict(contract=core, security_id=anchor.get("v14_core_put_security_id")
                                 if product == "IC" else None, kind="option"))
    required = list({(r["contract"], r["security_id"], r["kind"]): r for r in required}.values())
    previous = deepcopy(anchor)
    previous["v14_short_put_open_pending"] = None
    ordinary = previous.get("v14_ordinary_put_pending")
    if ordinary and as_day(ordinary["execution_day"]) <= as_day(signal["market_date"]):
        previous["v14_ordinary_put_pending"] = None
    return dict(product=product, signal_day=signal["market_date"], execution_day=signal["next_trade_date"],
                core_eod_contract=close_core,
                action=action, new_contract=contract, new_security_id=target.get("v14_short_put_security_id"),
                new_qty=float(target["v14_short_put_qty_normalized"]),
                signal_premium=float(target["v14_short_put_entry_premium"]),
                required_quotes=required, previous_anchor=previous, execution_revision=REVISION)


def project_short_open(product: str, anchor: dict, event: dict, day: date) -> dict:
    plan = anchor.get("v14_short_put_open_pending")
    if not plan or not same_plan(event.get("plan"), plan) or plan.get("product") != product:
        raise RuntimeError("Short-Put opening event differs from the persisted plan")
    if event["status"] == RETRY_OPEN_STATUS:
        if day < as_day(plan["execution_day"]):
            raise RuntimeError("Pending opening evidence has the wrong execution day")
        return deepcopy(anchor)
    if event["status"] == "awaiting_execution_day":
        if day >= as_day(plan["execution_day"]):
            raise RuntimeError("An opening due today cannot be marked as awaiting a future day")
        return deepcopy(anchor)
    if event["status"] != "confirmed_open_research_price":
        if event["status"] not in {"closed_missing_open_price", "closed_missed_execution_day"}:
            raise RuntimeError("Unknown short-Put opening outcome")
        if ((event["status"] == "closed_missing_open_price" and day != as_day(plan["execution_day"]))
                or (event["status"] == "closed_missed_execution_day" and day <= as_day(plan["execution_day"]))):
            raise RuntimeError("Short-Put closed opening status has the wrong day")
        result = deepcopy(plan["previous_anchor"])
        # Preserve an independently confirmed ordinary momentum opening.
        for key in anchor:
            if (("momentum" in key or "grid" in key) and not key.startswith("v14_")) or key in {"last_verified_day", "post_core_contract"}:
                result[key] = deepcopy(anchor[key])
        result["v14_short_put_open_pending"] = None
        for key, value in list(result.items()):
            if (key.endswith("_day") or key.endswith("_expiry")) and isinstance(value, str) and value:
                result[key] = as_day(value)
        if product == "IC":
            total = float(result.get("verified_core_put_qty", 0)) + float(result.get("verified_momentum_put_qty", 0))
            result.update(post_put_qty=total, verified_put_qty_normalized=total,
                          verified_total_put_delta=float(result.get("verified_core_put_delta", 0))+
                          float(result.get("verified_momentum_put_delta", 0)))
        else:
            total = float(result.get("verified_core_put_qty_normalized", 0)) + float(result.get("verified_momentum_put_qty_normalized", 0))
            result.update(verified_total_put_qty_normalized=total, verified_put_qty_normalized=total,
                          post_put_equivalent_units=.5*total)
        return result
    prices = checked_prices(plan, event["prices"], day)
    result = deepcopy(anchor)
    result["v14_short_put_entry_premium"] = prices[(plan["new_contract"], plan.get("new_security_id"), "option")]
    result["v14_short_put_entry_price_day"] = day
    result["v14_short_put_cost_basis"] = "confirmed_dated_open"
    result["v14_short_put_open_pending"] = None
    return result


def profit_plan(product: str, anchor: dict, day: date) -> dict | None:
    if not anchor.get("v14_profit_pending") or not anchor.get("v14_profit_execution_day"):
        return None
    if as_day(anchor["v14_profit_execution_day"]) != day:
        return None
    trigger = as_day(anchor["v14_profit_trigger_day"])
    if trigger < date(2026, 9, 28):
        return None
    required = [dict(contract=anchor[f"v14_profit_{side}_contract"],
                     security_id=anchor.get(f"v14_profit_{side}_security_id") if product == "IC" else None,
                     kind="option") for side in ("old", "reentry")]
    required = list({(r["contract"], r["security_id"], r["kind"]): r for r in required}.values())
    return dict(product=product, signal_day=trigger, execution_day=day,
                old_contract=anchor["v14_profit_old_contract"], old_qty=float(anchor["v14_profit_old_qty"]),
                new_contract=anchor["v14_profit_reentry_contract"],
                new_security_id=anchor.get("v14_profit_reentry_security_id") if product == "IC" else None,
                old_security_id=anchor.get("v14_profit_old_security_id") if product == "IC" else None,
                new_qty=float(anchor["v14_profit_reentry_qty"]), required_quotes=required)


def project_profit_open(product: str, anchor: dict, event: dict, day: date) -> dict:
    plan = profit_plan(product, anchor, day)
    if plan is None or not same_plan(plan, event.get("plan")) or event.get("status") != "confirmed_open_research_price":
        raise RuntimeError("3x opening event is not the prior close's committed plan")
    prices = checked_prices(plan, event["prices"], day)
    result = deepcopy(anchor)
    qty = plan["new_qty"]
    if not math.isfinite(qty) or qty <= 0 or (product == "IC" and not qty.is_integer()):
        raise RuntimeError("3x committed quantity is invalid")
    if product == "IC":
        mom = float(result.get("verified_momentum_put_qty", 0))
        result.update(v14_core_put_contract=plan["new_contract"],
                      v14_core_put_security_id=plan["new_security_id"], v14_core_put_qty=int(qty),
                      verified_core_put_qty=int(qty), post_put_qty=qty+mom, verified_put_qty_normalized=qty+mom)
    else:
        mom = float(result.get("verified_momentum_put_qty_normalized", 0))
        result.update(post_put_contract=plan["new_contract"], post_core_put_contract=plan["new_contract"],
                      verified_core_put_qty_normalized=qty, verified_put_qty_normalized=qty+mom,
                      verified_total_put_qty_normalized=qty+mom, verified_parent_puts=int(2*qty),
                      post_core_put_equivalent_units=.5*qty, post_put_equivalent_units=.5*(qty+mom))
    result["v14_core_put_entry_premium"] = prices[(plan["new_contract"], plan.get("new_security_id"), "option")]
    result["v14_core_put_profit3x_eligible"] = True
    result["v14_profit_pending"] = False
    for key in ("v14_profit_trigger_day", "v14_profit_execution_day", "v14_profit_old_contract",
                "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
        result[key] = None
    result["v14_profit_old_qty"] = result["v14_profit_reentry_qty"] = 0.0
    return result
