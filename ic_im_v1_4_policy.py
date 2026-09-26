"""Pure policy/state contract for the IC/IM v1.4 research signal.

This module contains no network or filesystem access.  The live producer must
provide a validated M+1 short-Put candidate and current marks; the durable
ledger persists every state returned here.
"""
from __future__ import annotations

import math
from copy import deepcopy
from datetime import date
from typing import Any


BUILD_ID = "v1.4-20260929-r1-ordinaryput-open-ic-seller-mom120-fix9"
RULE_REVISION = "ic_im_v1_4_ordinaryput_open_ic_seller_mom120_20260929_v1"
FIX8_BUILD_ID = "v1.4-20260929-r1-ordinaryput-open-fix8"
FIX8_RULE_REVISION = "ic_im_v1_4_ordinary_put_t1_open_20260929_v1"
FIX7_BUILD_ID = "v1.4-20260928-r1-coreput3x-open-fix7-nocall-repeatroll-iciv30-qdelta05"
FIX7_RULE_REVISION = "ic_im_v1_4_coreput3x_t1_open_20260928_v1"
EFFECTIVE_SIGNAL_DATE = date(2026, 9, 18)
# The r1 ledger is append-only. Each producer/rule change is forward-only;
# September 25 and earlier signals retain their original producer identity.
BUILD_EFFECTIVE_SIGNAL_DATE = date(2026, 9, 29)
IC_SELLER_MOM120_EFFECTIVE_SIGNAL_DATE = BUILD_EFFECTIVE_SIGNAL_DATE
ORDINARY_PUT_OPEN_EFFECTIVE_SIGNAL_DATE = BUILD_EFFECTIVE_SIGNAL_DATE
PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE = date(2026, 9, 28)
FIX6_BUILD_EFFECTIVE_SIGNAL_DATE = date(2026, 9, 26)
FIX6_BUILD_ID = "v1.4-20260926-r1-coreput3x-fixedshort95-fix6-nocall-repeatroll-iciv30-qdelta05"
FIX6_RULE_REVISION = "ic_im_v1_4_no_im_call_repeat_short_put_roll_20260926_v1"
REPEAT_ROLL_EFFECTIVE_SIGNAL_DATE = FIX6_BUILD_EFFECTIVE_SIGNAL_DATE
NO_CALL_EFFECTIVE_SIGNAL_DATE = FIX6_BUILD_EFFECTIVE_SIGNAL_DATE
FIX4_BUILD_EFFECTIVE_SIGNAL_DATE = date(2026, 9, 24)
FIX4_BUILD_ID = "v1.4-20260924-r1-coreput3x-fixedshort95-fix4-integrated-iciv30-qdelta05"
FIX4_RULE_REVISION = "ic_im_v1_4_iciv30_qdelta05_20260918_v1"
FIX3_BUILD_ID = "v1.4-20260918-r1-coreput3x-fixedshort95-fix3-iciv30-qdelta05"
FIX3_RULE_REVISION = FIX4_RULE_REVISION
PREVIOUS_BUILD_ID = "v1.4-20260917-r1-coreput3x-fixedshort95-fix2"
PREVIOUS_RULE_REVISION = "ic_im_v1_4_coreput3x_fixed_short95_20260917_v1"
PROFIT_MULTIPLE = 3.0
VALID_STATES = {
    "future",
    "short_put",
    "assigned_etf",
    "cash_wait",
    "recovery_future",
}


def identity_for_signal_day(value: date | str) -> tuple[str, str]:
    """Return the producer identity that was in force on the signal date."""
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if day < EFFECTIVE_SIGNAL_DATE:
        return PREVIOUS_BUILD_ID, PREVIOUS_RULE_REVISION
    if day < FIX4_BUILD_EFFECTIVE_SIGNAL_DATE:
        return FIX3_BUILD_ID, FIX3_RULE_REVISION
    if day < PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE:
        if day >= FIX6_BUILD_EFFECTIVE_SIGNAL_DATE:
            return FIX6_BUILD_ID, FIX6_RULE_REVISION
        return FIX4_BUILD_ID, FIX4_RULE_REVISION
    if day < BUILD_EFFECTIVE_SIGNAL_DATE:
        return FIX7_BUILD_ID, FIX7_RULE_REVISION
    return BUILD_ID, RULE_REVISION

PRODUCT_RULES = {
    "IC": {
        "iv_threshold": 0.30,
        "premium_decay": 0.50,
        "quantity": "q_delta05",
        "seller_mom120": True,
    },
    "IM": {
        "iv_threshold": 0.35,
        "premium_decay": 0.60,
        "quantity": "q3_delta05",
        "seller_mom120": True,
    },
}


def default_extension(product: str) -> dict[str, Any]:
    if product not in PRODUCT_RULES:
        raise ValueError(f"unsupported product: {product}")
    return {
        "v14_route_state": "future",
        "v14_cycle_id": None,
        "v14_short_put_contract": None,
        "v14_short_put_security_id": None,
        "v14_short_put_qty_normalized": 0.0,
        "v14_short_put_entry_premium": None,
        "v14_short_put_expiry": None,
        "v14_settlement_pending": False,
        "v14_settlement_trigger_day": None,
        "v14_early_roll_used": False,
        "v14_roll_pending": False,
        "v14_roll_trigger_day": None,
        "v14_roll_wait_reason": None,
        "v14_recovery_net_pnl": 0.0,
        "v14_core_put_entry_premium": None,
        "v14_core_put_contract": None,
        "v14_core_put_security_id": None,
        "v14_core_put_qty": 0.0,
        "v14_core_put_profit3x_eligible": False,
        "v14_profit_pending": False,
        "v14_profit_trigger_day": None,
        "v14_profit_execution_day": None,
        "v14_profit_old_contract": None,
        "v14_profit_old_security_id": None,
        "v14_profit_old_qty": 0.0,
        "v14_profit_reentry_contract": None,
        "v14_profit_reentry_security_id": None,
        "v14_profit_reentry_qty": 0.0,
        "v14_ordinary_put_pending": None,
        "v14_last_event_id": None,
    }


def migrated_extension(product: str) -> dict[str, Any]:
    """Existing v1.3 Put lacks a reconstructible entry premium.

    It becomes 3x-eligible only after the next ordinary monthly reset records a
    fresh price.  This prevents the migration date mark from masquerading as
    the historical cost basis.
    """
    return default_extension(product)


def validate_extension(product: str, state: dict[str, Any]) -> None:
    if product not in PRODUCT_RULES:
        raise RuntimeError("v1.4 product is invalid")
    route = state.get("v14_route_state")
    if route not in VALID_STATES:
        raise RuntimeError(f"{product} v1.4 route state is invalid")
    qty = float(state.get("v14_short_put_qty_normalized", math.nan))
    if not math.isfinite(qty) or qty < 0:
        raise RuntimeError(f"{product} v1.4 short-Put quantity is invalid")
    contract = state.get("v14_short_put_contract")
    premium = state.get("v14_short_put_entry_premium")
    if route == "short_put":
        if not contract or qty <= 0 or premium is None or not math.isfinite(float(premium)) or float(premium) <= 0:
            raise RuntimeError(f"{product} active short-Put state is incomplete")
        if product == "IC" and not state.get("v14_short_put_security_id"):
            raise RuntimeError("IC active short-Put state lacks security id")
    elif contract not in (None, "") or qty != 0.0:
        raise RuntimeError(f"{product} inactive short-Put state retains a position")
    if bool(state.get("v14_roll_pending")):
        if route != "short_put" or not state.get("v14_roll_trigger_day"):
            raise RuntimeError(f"{product} pending roll lacks a persistent trigger")
    if bool(state.get("v14_settlement_pending")):
        if route != "short_put" or not state.get("v14_settlement_trigger_day"):
            raise RuntimeError(f"{product} pending settlement lacks a persistent trigger")
    if route == "short_put" and product == "IM" and not math.isclose(qty, 1.5, abs_tol=1e-12):
        raise RuntimeError("IM active short-Put quantity must equal q3 delta05 (1.5)")
    if bool(state.get("v14_profit_pending")) and not state.get("v14_profit_trigger_day"):
        raise RuntimeError(f"{product} profit pending lacks a trigger day")
    plan = state.get("v14_ordinary_put_pending")
    if plan is not None:
        if route != "future" or bool(state.get("v14_profit_pending")):
            raise RuntimeError(f"{product} ordinary Put plan conflicts with core route or 3x plan")
        if not isinstance(plan, dict) or plan.get("product") != product or not isinstance(plan.get("legs"), dict):
            raise RuntimeError(f"{product} ordinary Put plan is malformed")
        trigger = date.fromisoformat(str(plan.get("signal_day"))[:10])
        execution = date.fromisoformat(str(plan.get("execution_day"))[:10])
        if trigger < ORDINARY_PUT_OPEN_EFFECTIVE_SIGNAL_DATE or execution <= trigger:
            raise RuntimeError(f"{product} ordinary Put plan date is invalid")
        for name in ("core", "momentum"):
            leg = plan["legs"].get(name)
            if not isinstance(leg, dict):
                raise RuntimeError(f"{product} ordinary {name} Put leg is missing")
            expected_change = (leg.get("old_contract") != leg.get("new_contract") or
                               not math.isclose(float(leg.get("old_qty", math.nan)),
                                                float(leg.get("new_qty", math.nan)), abs_tol=1e-12))
            if type(leg.get("changed")) is not bool or leg["changed"] != expected_change:
                raise RuntimeError(f"{product} ordinary {name} Put change flag is invalid")
            for prefix in ("old", "new"):
                qty = float(leg.get(f"{prefix}_qty", math.nan))
                if not math.isfinite(qty) or qty < 0 or (qty > 0 and not leg.get(f"{prefix}_contract")):
                    raise RuntimeError(f"{product} ordinary {name} Put {prefix} identity is invalid")
                if product == "IC" and qty > 0 and not leg.get(f"{prefix}_security_id"):
                    raise RuntimeError("IC ordinary Put plan lacks security id")
    if state.get("v14_profit_execution_day") is not None:
        trigger = state.get("v14_profit_trigger_day")
        if not state.get("v14_profit_pending") or trigger is None:
            raise RuntimeError(f"{product} profit execution date lacks a pending trigger")
        if date.fromisoformat(str(state["v14_profit_execution_day"])[:10]) <= date.fromisoformat(str(trigger)[:10]):
            raise RuntimeError(f"{product} profit execution must follow trigger day")
        trigger_day = date.fromisoformat(str(trigger)[:10])
        if trigger_day >= PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE:
            if not state.get("v14_profit_old_contract") or not state.get("v14_profit_reentry_contract"):
                raise RuntimeError(f"{product} open profit plan lacks old/new contracts")
            if float(state.get("v14_profit_old_qty") or 0) <= 0 or float(state.get("v14_profit_reentry_qty") or 0) <= 0:
                raise RuntimeError(f"{product} open profit plan lacks old/new quantities")
            if product == "IC" and (not state.get("v14_profit_old_security_id") or not state.get("v14_profit_reentry_security_id")):
                raise RuntimeError("IC open profit plan lacks old/new security ids")
    entry = state.get("v14_core_put_entry_premium")
    core_qty = float(state.get("v14_core_put_qty", 0.0))
    if not math.isfinite(core_qty) or core_qty < 0:
        raise RuntimeError(f"{product} v1.4 core-Put quantity is invalid")
    eligible = bool(state.get("v14_core_put_profit3x_eligible"))
    if eligible and (entry is None or not math.isfinite(float(entry)) or float(entry) <= 0):
        raise RuntimeError(f"{product} 3x eligibility lacks an entry premium")


def project_ordinary_open(product: str, anchor: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Project a verified opening plan into a transient continuation anchor."""
    result = deepcopy(anchor)
    core, momentum = plan["legs"]["core"], plan["legs"]["momentum"]
    core_qty, momentum_qty = float(core["new_qty"]), float(momentum["new_qty"])
    if product == "IC":
        result.update(
            post_put_contract=momentum["new_contract"] or result.get("post_put_contract"),
            post_put_security_id=momentum["new_security_id"] or result.get("post_put_security_id"),
            post_put_qty=core_qty + momentum_qty,
            verified_put_qty_normalized=core_qty + momentum_qty,
            verified_core_put_qty=int(core_qty),
            verified_momentum_put_qty=int(momentum_qty),
            verified_core_put_delta=float(plan["core_delta"]),
            verified_momentum_put_delta=float(plan["momentum_delta"]),
            verified_total_put_delta=float(plan["core_delta"]) + float(plan["momentum_delta"]),
            verified_core_put_driver=plan["core_driver"],
            verified_momentum_put_driver=plan["momentum_driver"],
            v14_core_put_contract=core["new_contract"],
            v14_core_put_security_id=core["new_security_id"],
            v14_core_put_qty=int(core_qty),
        )
    else:
        result.update(
            post_put_contract=core["new_contract"],
            post_core_put_contract=core["new_contract"],
            post_momentum_put_contract=momentum["new_contract"],
            post_put_equivalent_units=0.5 * (core_qty + momentum_qty),
            post_core_put_equivalent_units=0.5 * core_qty,
            post_momentum_put_equivalent_units=0.5 * momentum_qty,
            verified_put_qty_normalized=core_qty + momentum_qty,
            verified_core_put_qty_normalized=core_qty,
            verified_momentum_put_qty_normalized=momentum_qty,
            verified_total_put_qty_normalized=core_qty + momentum_qty,
            verified_parent_puts=int(plan["parent_puts"]),
        )
    return result


def seller_permission(product: str, signal: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, str]:
    if not bool(signal.get("close_confirmed")):
        return False, "close_not_confirmed"
    if not bool(candidate.get("tradable")):
        return False, "candidate_untradable"
    iv = candidate.get("iv")
    if iv is None or not math.isfinite(float(iv)):
        return False, "candidate_iv_unavailable"
    if float(iv) <= float(PRODUCT_RULES[product]["iv_threshold"]):
        return False, "candidate_iv_below_threshold"
    if product == "IC":
        tier = signal.get("valuation_tier")
        if tier is None or not math.isfinite(float(tier)) or float(tier) not in (0.0, 1.0):
            return False, "valuation_not_0_or_1"
        day = date.fromisoformat(str(signal["market_date"])[:10])
        if day >= IC_SELLER_MOM120_EFFECTIVE_SIGNAL_DATE:
            momentum = signal.get("momentum_120")
            if momentum is None or not math.isfinite(float(momentum)):
                return False, "mom120_unavailable"
            if float(momentum) < 0.0:
                return False, "mom120_negative"
        else:
            # Historical signals retain their original execution-momentum permit.
            momentum = signal.get("momentum_next_weight")
            if momentum is None or not math.isfinite(float(momentum)) or float(momentum) <= 0.0:
                return False, "ic_execution_momentum_not_permitted"
    else:
        tier = signal.get("valuation_puts_per_full_core")
        if tier is None or not math.isfinite(float(tier)) or float(tier) not in (0.0, 1.0):
            return False, "valuation_not_0_or_1"
        momentum = signal.get("momentum_120")
        if momentum is None or not math.isfinite(float(momentum)) or float(momentum) < 0.0:
            return False, "mom120_negative"
    return True, "allowed"


def _event_id(product: str, signal: dict[str, Any], action: str, contract: Any) -> str:
    return f"{product}:{signal['market_date']}:{signal['next_trade_date']}:{action}:{contract or '-'}"


def apply_policy(
    product: str,
    signal: dict[str, Any],
    anchor: dict[str, Any],
    *,
    candidate: dict[str, Any] | None,
    roll_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a v1.4 signal without mutating the v1.3 input or ledger anchor."""
    out = deepcopy(signal)
    state = {**default_extension(product), **{
        key: deepcopy(value) for key, value in anchor.items() if key.startswith("v14_")
    }}
    validate_extension(product, state)
    day = signal["market_date"]
    if not isinstance(day, date):
        day = date.fromisoformat(str(day)[:10])
    build_id, rule_revision = identity_for_signal_day(day)
    out.update(
        strategy_version="1.4",
        strategy_revision="r1",
        v14_build_id=build_id,
        v14_rule_revision=rule_revision,
        v14_effective_signal_date=EFFECTIVE_SIGNAL_DATE,
    )
    if day < EFFECTIVE_SIGNAL_DATE:
        out.update(state)
        out["v14_action"] = "LEGACY_V13_FORWARD_ONLY"
        out["v14_action_reason"] = "before_effective_signal_date"
        return out

    action, reason = "HOLD", "state_unchanged"
    target = deepcopy(state)
    candidate = candidate or {"tradable": False}
    allowed, permission_reason = seller_permission(product, signal, candidate)
    route = state["v14_route_state"]
    close_confirmed = bool(signal.get("close_confirmed"))
    trigger_value = state.get("v14_profit_trigger_day")
    trigger_day = date.fromisoformat(str(trigger_value)[:10]) if trigger_value else None
    pending_open_due = bool(
        state.get("v14_profit_pending")
        and trigger_day is not None
        and trigger_day >= PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE
        and str(state.get("v14_profit_execution_day"))[:10] == day.isoformat()
        and not signal.get("option_monthly_reset_due")
    )

    # The prior close's committed open plan precedes a new route signal that
    # can only be confirmed at today's close. Earlier versions keep priority.
    expiry = state.get("v14_short_put_expiry")
    expiry_day = date.fromisoformat(str(expiry)[:10]) if expiry else None
    if not close_confirmed:
        reason = "close_not_confirmed"
    elif route == "short_put" and expiry_day is not None and day >= expiry_day:
        # This ledger is a research-model ledger, not a broker/account ledger.
        # Prefer explicit model-reference fields; retain the fix1 names only as
        # backward-compatible inputs for an already prepared signal artifact.
        outcome = signal.get("v14_model_settlement_outcome", signal.get("v14_settlement_outcome"))
        confirmed = bool(
            signal.get("v14_model_settlement_confirmed", signal.get("v14_settlement_confirmed"))
        )
        if confirmed and outcome == "expired_worthless":
            action, reason = "SHORT_PUT_EXPIRE_WORTHLESS", "settlement_confirmed"
            target.update(default_extension(product))
        elif confirmed and outcome == "assigned":
            action, reason = "SHORT_PUT_ASSIGNED", "settlement_confirmed"
            cycle_id = state.get("v14_cycle_id")
            recovery_pnl = signal.get("v14_recovery_net_pnl_mark")
            if recovery_pnl is None or not math.isfinite(float(recovery_pnl)):
                raise RuntimeError(f"{product} assignment lacks finite cycle net PnL")
            target.update(default_extension(product))
            target["v14_route_state"] = "assigned_etf" if product == "IC" else "cash_wait"
            target["v14_cycle_id"] = cycle_id
            target["v14_recovery_net_pnl"] = float(recovery_pnl)
        else:
            action, reason = "PUBLISH_SHORT_PUT_EXPIRY_BRANCHES", "model_settlement_reference_not_available"
            target["v14_settlement_pending"] = True
            target["v14_settlement_trigger_day"] = state.get("v14_settlement_trigger_day") or day
    elif route == "short_put":
        mark = signal.get("v14_short_put_mark")
        entry = float(state["v14_short_put_entry_premium"])
        decay_hit = (
            mark is not None
            and math.isfinite(float(mark))
            and float(mark) <= entry * (1.0 - float(PRODUCT_RULES[product]["premium_decay"]))
        )
        pending = bool(state["v14_roll_pending"])
        repeat_roll_enabled = day >= REPEAT_ROLL_EFFECTIVE_SIGNAL_DATE
        if (decay_hit or pending) and (repeat_roll_enabled or not bool(state["v14_early_roll_used"])):
            replacement = roll_candidate or candidate
            roll_allowed, roll_reason = seller_permission(product, signal, replacement)
            if roll_allowed:
                action, reason = "ROLL_SHORT_PUT", "premium_decay_and_reentry_passed"
                target.update(
                    v14_short_put_contract=replacement["contract"],
                    v14_short_put_security_id=replacement.get("security_id"),
                    v14_short_put_qty_normalized=float(replacement["qty_normalized"]),
                    v14_short_put_entry_premium=float(replacement["premium"]),
                    v14_short_put_expiry=replacement["expiry"],
                    # In the new forward rule the next leg has its own entry
                    # premium and may roll again when it independently qualifies.
                    # Retain the old one-roll flag on historical signal dates.
                    v14_early_roll_used=not repeat_roll_enabled,
                    v14_roll_pending=False,
                    v14_roll_trigger_day=None,
                    v14_roll_wait_reason=None,
                )
            else:
                action, reason = "WAIT_SHORT_PUT_ROLL", roll_reason
                target["v14_roll_pending"] = True
                target["v14_roll_trigger_day"] = state.get("v14_roll_trigger_day") or day
                target["v14_roll_wait_reason"] = roll_reason
    elif route == "future" and allowed and not pending_open_due:
        action, reason = "ENTER_SHORT_PUT", "high_iv_fixed_core_route"
        target.update(
            v14_route_state="short_put",
            v14_cycle_id=f"{product}-{signal['next_trade_date']}-{candidate['contract']}",
            v14_short_put_contract=candidate["contract"],
            v14_short_put_security_id=candidate.get("security_id"),
            v14_short_put_qty_normalized=float(candidate["qty_normalized"]),
            v14_short_put_entry_premium=float(candidate["premium"]),
            v14_short_put_expiry=candidate["expiry"],
            v14_early_roll_used=False,
            v14_roll_pending=False,
            v14_roll_trigger_day=None,
            v14_roll_wait_reason=None,
        )
    elif route in {"assigned_etf", "cash_wait", "recovery_future"}:
        recovery_mark = signal.get("v14_recovery_net_pnl_mark")
        if recovery_mark is not None:
            if not math.isfinite(float(recovery_mark)):
                raise RuntimeError(f"{product} recovery PnL mark is not finite")
            target["v14_recovery_net_pnl"] = float(recovery_mark)
        recovery_reference_confirmed = bool(
            signal.get(
                "v14_model_recovery_future_confirmed",
                signal.get("v14_recovery_future_confirmed"),
            )
        )
        if route in {"assigned_etf", "cash_wait"} and recovery_reference_confirmed:
            action, reason = "ENTER_RECOVERY_FUTURE", "model_execution_reference_confirmed"
            target["v14_route_state"] = "recovery_future"
        elif float(target.get("v14_recovery_net_pnl", 0.0)) >= 0.0 and route == "recovery_future":
            action, reason = "EXIT_RECOVERY_AT_BREAKEVEN", "cycle_net_pnl_nonnegative"
            target.update(default_extension(product))
        else:
            action, reason = "HOLD_RECOVERY", route
    else:
        reason = permission_reason

    if close_confirmed and route == "future":
        # The adapter owns the independently selected core leg.  Absorb its
        # identity before evaluating 3x so a zero core target cannot trigger
        # on a momentum-leg mark or on stale core cost basis.
        for key in ("v14_core_put_contract", "v14_core_put_security_id", "v14_core_put_qty"):
            if key in signal:
                target[key] = deepcopy(signal[key])
        if "v14_core_put_qty" in signal and float(target.get("v14_core_put_qty", 0.0)) == 0.0:
            target["v14_core_put_entry_premium"] = None
            target["v14_core_put_profit3x_eligible"] = False
            target["v14_profit_pending"] = False
            target["v14_profit_trigger_day"] = None
            target["v14_profit_execution_day"] = None
            for key in ("v14_profit_old_contract", "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
                target[key] = None
            target["v14_profit_old_qty"] = target["v14_profit_reentry_qty"] = 0.0

    monthly_priority = bool(signal.get("option_monthly_reset_due"))
    if day >= PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE and bool(target.get("v14_profit_pending")):
        core_target = (signal.get("put_target_core_qty") if product == "IC"
                       else signal.get("core_put_target_qty_normalized"))
        if core_target is not None and float(core_target) <= 0:
            target["v14_profit_pending"] = False
            target["v14_profit_trigger_day"] = None
            target["v14_profit_execution_day"] = None
            for key in ("v14_profit_old_contract", "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
                target[key] = None
            target["v14_profit_old_qty"] = target["v14_profit_reentry_qty"] = 0.0
    mark = signal.get("v14_core_put_mark")
    entry = target.get("v14_core_put_entry_premium")
    profit_hit = (
        route == "future"
        and close_confirmed
        and bool(target.get("v14_core_put_profit3x_eligible"))
        and entry is not None and mark is not None
        and math.isfinite(float(mark))
        and float(mark) >= PROFIT_MULTIPLE * float(entry)
    )
    if action == "HOLD" and close_confirmed and not monthly_priority and bool(target.get("v14_profit_pending")):
        reentry_premium = signal.get("v14_profit_reentry_entry_premium")
        execution_value = state.get("v14_profit_execution_day")
        execution_day = date.fromisoformat(str(execution_value)[:10]) if execution_value else None
        trigger_value = state.get("v14_profit_trigger_day")
        trigger_day = date.fromisoformat(str(trigger_value)[:10]) if trigger_value else None
        opening_rule = trigger_day is not None and trigger_day >= PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE
        open_confirmed = (
            signal.get("v14_profit_reentry_status") == "confirmed_open_research_price"
            and signal.get("v14_profit_reentry_contract") == state.get("v14_profit_reentry_contract")
            and signal.get("v14_profit_open_price_day") == day
            and math.isclose(float(signal.get("v14_profit_open_executed_qty") or 0.0),
                             float(state.get("v14_profit_reentry_qty") or 0.0), abs_tol=1e-12)
            and signal.get("v14_profit_exit_open_price") is not None
            and math.isfinite(float(signal["v14_profit_exit_open_price"]))
            and float(signal["v14_profit_exit_open_price"]) > 0
        ) if opening_rule else True
        if execution_day == day and open_confirmed and reentry_premium is not None and math.isfinite(float(reentry_premium)) and float(reentry_premium) > 0:
            action, reason = "EXECUTE_CORE_PUT_PROFIT3X_REENTER", "confirmed_reentry_premium"
            target["v14_core_put_entry_premium"] = float(reentry_premium)
            target["v14_core_put_profit3x_eligible"] = True
            target["v14_profit_pending"] = False
            target["v14_profit_trigger_day"] = None
            target["v14_profit_execution_day"] = None
            for key in ("v14_profit_old_contract", "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
                target[key] = None
            target["v14_profit_old_qty"] = target["v14_profit_reentry_qty"] = 0.0
        else:
            action, reason = "WAIT_CORE_PUT_PROFIT3X_REENTER", "reentry_not_confirmed"
    elif action == "HOLD" and profit_hit and not monthly_priority:
        opening_rule = day >= PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE
        selected = signal.get("v14_profit_plan_contract")
        selected_qty = signal.get("v14_profit_plan_qty")
        old_contract = signal.get("v14_core_put_contract") if product == "IC" else signal.get("core_put_current_contract")
        old_qty = signal.get("v14_core_put_qty") if product == "IC" else signal.get("core_put_current_qty_normalized")
        ready = (not opening_rule or (selected and selected_qty is not None and float(selected_qty) > 0
                 and old_contract and old_qty is not None and float(old_qty) > 0
                 and (product != "IC" or (signal.get("v14_profit_plan_security_id") and signal.get("v14_core_put_security_id")))))
        if ready:
            action, reason = "CORE_PUT_PROFIT3X_REENTER", "three_times_entry_premium"
            target["v14_profit_pending"] = True
            target["v14_profit_trigger_day"] = day
            target["v14_profit_execution_day"] = signal["next_trade_date"]
            if opening_rule:
                target.update(v14_profit_old_contract=old_contract,
                              v14_profit_old_security_id=signal.get("v14_core_put_security_id") if product == "IC" else None,
                              v14_profit_old_qty=float(old_qty),
                              v14_profit_reentry_contract=str(selected),
                              v14_profit_reentry_security_id=signal.get("v14_profit_plan_security_id") if product == "IC" else None,
                              v14_profit_reentry_qty=float(selected_qty))
                out["v14_profit_reentry_status"] = "scheduled_t_plus_1_open"
        else:
            reason = "profit3x_t_close_preselection_unavailable"
    if close_confirmed and monthly_priority and target["v14_route_state"] == "future" and signal.get("v14_core_put_target_entry_premium"):
        target["v14_core_put_entry_premium"] = float(signal["v14_core_put_target_entry_premium"])
        target["v14_core_put_profit3x_eligible"] = True
        target["v14_profit_pending"] = False
        target["v14_profit_trigger_day"] = None
        target["v14_profit_execution_day"] = None
        for key in ("v14_profit_old_contract", "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
            target[key] = None
        target["v14_profit_old_qty"] = target["v14_profit_reentry_qty"] = 0.0

    if action in {"ENTER_SHORT_PUT", "ROLL_SHORT_PUT"}:
        target["v14_profit_pending"] = False
        target["v14_profit_trigger_day"] = None
        target["v14_profit_execution_day"] = None
        for key in ("v14_profit_old_contract", "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
            target[key] = None
        target["v14_profit_old_qty"] = target["v14_profit_reentry_qty"] = 0.0
    if target["v14_route_state"] != "future":
        if day >= PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE:
            target["v14_profit_pending"] = False
            target["v14_profit_trigger_day"] = None
            target["v14_profit_execution_day"] = None
            for key in ("v14_profit_old_contract", "v14_profit_old_security_id", "v14_profit_reentry_contract", "v14_profit_reentry_security_id"):
                target[key] = None
            target["v14_profit_old_qty"] = target["v14_profit_reentry_qty"] = 0.0
        target.update(v14_core_put_contract=None, v14_core_put_security_id=None, v14_core_put_qty=0.0)
    if target["v14_route_state"] != "future" or target.get("v14_profit_pending"):
        target["v14_ordinary_put_pending"] = None
    event_id = _event_id(product, signal, action, target.get("v14_short_put_contract"))
    if action not in {"HOLD", "WAIT_SHORT_PUT_ROLL", "HOLD_RECOVERY"}:
        if event_id == state.get("v14_last_event_id"):
            raise RuntimeError(f"{product} duplicate v1.4 event: {event_id}")
        target["v14_last_event_id"] = event_id

    if route not in {"future", "recovery_future"}:
        out["core_units_current"] = 0.0
        out["total_units_current"] = max(
            0.0, float(out.get("total_units_current", 0.0)) - 0.5
        )
    if target["v14_route_state"] != "future":
        recovery_future = target["v14_route_state"] == "recovery_future"
        out["core_units_target"] = 0.5 if recovery_future else 0.0
        if product == "IC":
            out.update(
                core_put_target_delta=0.0,
                total_put_target_delta=float(out.get("momentum_put_target_delta", 0.0)),
                put_target_core_qty=0,
                put_target_total_qty=int(out.get("put_target_momentum_qty", 0)),
            )
            if out["total_put_target_delta"] == 0.0:
                out["put_target_contract"] = None
                out["put_target_security_id"] = None
            out["put_action"] = (
                "HOLD" if math.isclose(float(out.get("total_put_current_delta", 0.0)), out["total_put_target_delta"], abs_tol=1e-12)
                and out.get("put_current_contract") == out.get("put_target_contract")
                and not (monthly_priority and out["total_put_target_delta"] > 0.0)
                else "RESIZE_OR_ROLL"
            )
        else:
            momentum_qty = float(out.get("momentum_put_target_qty_normalized", 0.0))
            out.update(
                core_put_target_qty_normalized=0.0,
                core_put_target_contract=None,
                put_target_contract=None,
                total_put_target_qty_normalized=momentum_qty,
                call_target_qty_normalized=0.0,
                call_target_contract=None,
                call_target_expiry=None,
                call_target_strike=None,
                call_action="CLOSE_CALL" if out.get("call_current_contract") else "HOLD",
            )
            out["core_put_action"] = (
                "RESIZE_OR_ROLL" if float(out.get("core_put_current_qty_normalized", 0.0)) > 0.0 or out.get("core_put_current_contract")
                else "HOLD"
            )
            out["put_action"] = (
                "HOLD" if out["core_put_action"] == "HOLD" and out.get("momentum_put_action", "HOLD") == "HOLD"
                else "RESIZE_OR_ROLL"
            )
        if not recovery_future:
            out["total_units_target"] = max(
                0.0, float(out.get("total_units_target", 0.0)) - 0.5
            )
    out["total_units_change"] = (
        float(out.get("total_units_target", 0.0))
        - float(out.get("total_units_current", 0.0))
    )
    if product == "IM" and day >= NO_CALL_EFFECTIVE_SIGNAL_DATE:
        has_call = bool(out.get("call_current_contract")) and bool(out.get("call_has_position", True))
        out.update(
            call_target_qty_normalized=0.0,
            call_target_contract=None,
            call_target_expiry=None,
            call_target_strike=None,
            call_target_threat_roll_count=0,
            call_action="CLOSE_CALL" if has_call else "HOLD",
            call_target="目标空仓（fix6停止卖Call）",
        )
    out.update(target)
    out["v14_action"] = action
    out["v14_action_reason"] = reason
    out["v14_signal_scope"] = "research_model_signal_only"
    out["v14_account_execution_status"] = "not_observed_out_of_scope"
    if action == "PUBLISH_SHORT_PUT_EXPIRY_BRANCHES":
        assigned_route = "model_assigned_etf" if product == "IC" else "model_cash_settlement_recovery"
        out["v14_expiry_conditional_signal"] = {
            "expired_worthless": "end_short_put_cycle_and_return_to_future_route",
            "assigned_or_cash_settled_itm": assigned_route,
            "basis": "apply_the_branch_supported_by_the_verified_model_settlement_reference",
        }
    out["v14_seller_permission"] = allowed
    out["v14_seller_permission_reason"] = permission_reason
    out["v14_short_put_candidate"] = candidate
    validate_extension(product, target)
    return out
