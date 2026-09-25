from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

import ic_im_v1_4_policy as policy


def _signal(product: str, **overrides):
    signal = {
        "product": product,
        "market_date": date(2026, 9, 18),
        "next_trade_date": date(2026, 9, 21),
        "close_confirmed": True,
        "valuation_tier": 1,
        "valuation_puts_per_full_core": 1,
        "momentum_120": 0.02,
        "momentum_next_weight": 1.0,
        "option_monthly_reset_due": False,
        "core_units_current": 0.5,
        "core_units_target": 0.5,
        "total_units_current": 1.0,
        "total_units_target": 1.0,
        "momentum_put_target_delta": 0.25,
        "put_target_momentum_qty": 4,
        "momentum_put_target_qty_normalized": 1.5,
        "call_current_contract": "MO2610-C-9000" if product == "IM" else None,
    }
    signal.update(overrides)
    return signal


def _candidate(product: str, **overrides):
    candidate = {
        "tradable": True,
        "contract": "510500P2610M07500" if product == "IC" else "MO2610-P-7200",
        "security_id": "10019999" if product == "IC" else None,
        "expiry": date(2026, 10, 28) if product == "IC" else date(2026, 10, 16),
        "premium": 0.12 if product == "IC" else 80.0,
        "iv": 0.40,
        "qty_normalized": 9.8 if product == "IC" else 1.5,
    }
    candidate.update(overrides)
    return candidate


def test_producer_identity_is_date_aware_for_append_only_history():
    assert policy.identity_for_signal_day(date(2026, 9, 17)) == (
        policy.PREVIOUS_BUILD_ID,
        policy.PREVIOUS_RULE_REVISION,
    )
    assert policy.identity_for_signal_day(date(2026, 9, 18)) == (
        policy.FIX3_BUILD_ID,
        policy.FIX3_RULE_REVISION,
    )
    assert policy.identity_for_signal_day(date(2026, 9, 23)) == (
        policy.FIX3_BUILD_ID,
        policy.FIX3_RULE_REVISION,
    )
    assert policy.identity_for_signal_day(date(2026, 9, 24)) == (
        policy.FIX4_BUILD_ID,
        policy.FIX4_RULE_REVISION,
    )
    assert policy.identity_for_signal_day(date(2026, 9, 25)) == (
        policy.FIX4_BUILD_ID,
        policy.FIX4_RULE_REVISION,
    )
    assert policy.identity_for_signal_day(date(2026, 9, 26)) == (
        policy.FIX6_BUILD_ID,
        policy.FIX6_RULE_REVISION,
    )
    assert policy.identity_for_signal_day(date(2026, 9, 28)) == (
        policy.BUILD_ID,
        policy.RULE_REVISION,
    )


def test_replay_before_fix3_keeps_original_producer_identity():
    result = policy.apply_policy(
        "IC",
        _signal("IC", market_date=date(2026, 9, 17)),
        policy.default_extension("IC"),
        candidate={"tradable": False},
    )
    assert result["v14_build_id"] == policy.PREVIOUS_BUILD_ID
    assert result["v14_rule_revision"] == policy.PREVIOUS_RULE_REVISION


@pytest.mark.parametrize("day,expected_action", [
    (date(2026, 9, 25), "OPEN_CALL"),
    (date(2026, 9, 26), "HOLD"),
])
def test_no_call_boundary_preserves_old_signal_and_blocks_new_open(day, expected_action):
    signal = _signal("IM", market_date=day, next_trade_date=date(2026, 9, 28),
                     call_current_contract=None, call_action="OPEN_CALL",
                     call_target_contract="MO2610-C-9000", call_target_qty_normalized=-1.0,
                     call_target_expiry=date(2026, 10, 16), call_target_strike=9000.0)
    result = policy.apply_policy("IM", signal, policy.default_extension("IM"),
                                 candidate={"tradable": False})
    assert result["call_action"] == expected_action
    if day >= policy.NO_CALL_EFFECTIVE_SIGNAL_DATE:
        assert result["call_target_qty_normalized"] == 0.0
        assert result["call_target_contract"] is None


def test_no_call_closes_existing_position_without_reopening():
    signal = _signal("IM", market_date=date(2026, 9, 28), next_trade_date=date(2026, 9, 29),
                     call_action="RESCUE_NEXT_LISTED", call_target_contract="MO2611-C-9500",
                     call_target_qty_normalized=-1.0)
    result = policy.apply_policy("IM", signal, policy.default_extension("IM"),
                                 candidate={"tradable": False})
    assert result["call_action"] == "CLOSE_CALL"
    assert result["call_target_contract"] is None
    assert result["call_target_qty_normalized"] == 0.0


def test_ic_seller_uses_v13_execution_permission_not_removed_mom120_gate():
    signal = _signal("IC", momentum_120=-0.10, momentum_next_weight=0.5)
    allowed, reason = policy.seller_permission("IC", signal, _candidate("IC"))
    assert allowed is True
    assert reason == "allowed"
    blocked, reason = policy.seller_permission(
        "IC", _signal("IC", momentum_next_weight=0.0), _candidate("IC")
    )
    assert blocked is False
    assert reason == "ic_execution_momentum_not_permitted"


def test_im_seller_requires_nonnegative_mom120_and_q3_enters_atomically():
    denied, reason = policy.seller_permission(
        "IM", _signal("IM", momentum_120=-0.001), _candidate("IM")
    )
    assert denied is False
    assert reason == "mom120_negative"

    result = policy.apply_policy(
        "IM", _signal("IM"), policy.default_extension("IM"), candidate=_candidate("IM")
    )
    assert result["v14_action"] == "ENTER_SHORT_PUT"
    assert result["v14_route_state"] == "short_put"
    assert result["v14_short_put_qty_normalized"] == 1.5
    assert result["core_units_target"] == 0.0
    assert result["core_put_target_qty_normalized"] == 0.0
    assert result["call_target_qty_normalized"] == 0.0
    assert result["call_action"] == "CLOSE_CALL"


def test_roll_wait_is_persistent_and_only_success_clears_it():
    anchor = policy.default_extension("IM")
    anchor.update(
        v14_route_state="short_put",
        v14_cycle_id="cycle",
        v14_short_put_contract="MO2610-P-7200",
        v14_short_put_qty_normalized=1.5,
        v14_short_put_entry_premium=100.0,
        v14_short_put_expiry=date(2026, 10, 16),
    )
    blocked = _candidate("IM", tradable=False)
    first = policy.apply_policy(
        "IM", _signal("IM", v14_short_put_mark=35.0), anchor,
        candidate=blocked, roll_candidate=blocked,
    )
    assert first["v14_action"] == "WAIT_SHORT_PUT_ROLL"
    assert first["v14_roll_pending"] is True
    trigger = first["v14_roll_trigger_day"]

    second_anchor = {key: first[key] for key in policy.default_extension("IM")}
    second = policy.apply_policy(
        "IM", _signal("IM", market_date=date(2026, 9, 21), next_trade_date=date(2026, 9, 22), v14_short_put_mark=34.0),
        second_anchor, candidate=blocked, roll_candidate=blocked,
    )
    assert second["v14_roll_trigger_day"] == trigger
    assert second["v14_roll_pending"] is True

    replacement = _candidate("IM", contract="MO2611-P-7200", expiry=date(2026, 11, 20), premium=75.0)
    third_anchor = {key: second[key] for key in policy.default_extension("IM")}
    third = policy.apply_policy(
        "IM", _signal("IM", market_date=date(2026, 9, 22), next_trade_date=date(2026, 9, 23), v14_short_put_mark=33.0),
        third_anchor, candidate=replacement, roll_candidate=replacement,
    )
    assert third["v14_action"] == "ROLL_SHORT_PUT"
    assert third["v14_early_roll_used"] is True
    assert third["v14_roll_pending"] is False


def test_migrated_put_cannot_trigger_3x_until_fresh_monthly_reset():
    migrated = policy.migrated_extension("IC")
    assert migrated["v14_core_put_profit3x_eligible"] is False
    first = policy.apply_policy(
        "IC", _signal("IC", v14_core_put_mark=99.0), migrated,
        candidate=_candidate("IC", iv=0.20),
    )
    assert first["v14_action"] == "HOLD"

    reset_anchor = {key: first[key] for key in policy.default_extension("IC")}
    reset = policy.apply_policy(
        "IC",
        _signal("IC", option_monthly_reset_due=True, v14_core_put_target_entry_premium=10.0),
        reset_anchor,
        candidate=_candidate("IC", iv=0.20),
    )
    assert reset["v14_core_put_profit3x_eligible"] is True
    next_anchor = {key: reset[key] for key in policy.default_extension("IC")}
    hit = policy.apply_policy(
        "IC",
        _signal("IC", market_date=date(2026, 9, 21), next_trade_date=date(2026, 9, 22), v14_core_put_mark=30.0),
        next_anchor,
        candidate=_candidate("IC", iv=0.20),
    )
    assert hit["v14_action"] == "CORE_PUT_PROFIT3X_REENTER"
    assert hit["v14_profit_pending"] is True


def test_active_ic_short_put_requires_security_id():
    state = policy.default_extension("IC")
    state.update(
        v14_route_state="short_put",
        v14_short_put_contract="510500P2610M07500",
        v14_short_put_qty_normalized=10.0,
        v14_short_put_entry_premium=0.1,
        v14_short_put_expiry=date(2026, 10, 28),
    )
    with pytest.raises(RuntimeError, match="security id"):
        policy.validate_extension("IC", state)


def test_zero_core_target_clears_stale_profit_basis_before_trigger():
    anchor = policy.default_extension("IC")
    anchor.update(
        v14_core_put_entry_premium=10.0,
        v14_core_put_contract="510500P2612M07500",
        v14_core_put_security_id="10012099",
        v14_core_put_qty=5,
        v14_core_put_profit3x_eligible=True,
    )
    result = policy.apply_policy(
        "IC",
        _signal(
            "IC",
            v14_core_put_mark=31.0,
            v14_core_put_contract=None,
            v14_core_put_security_id=None,
            v14_core_put_qty=0,
        ),
        anchor,
        candidate=_candidate("IC", iv=0.20),
    )
    assert result["v14_action"] == "HOLD"
    assert result["v14_core_put_entry_premium"] is None
    assert result["v14_core_put_profit3x_eligible"] is False
    assert result["v14_profit_pending"] is False


def test_expiry_publishes_model_branches_without_account_confirmation():
    anchor = policy.default_extension("IM")
    anchor.update(
        v14_route_state="short_put",
        v14_cycle_id="cycle",
        v14_short_put_contract="MO2610-P-7200",
        v14_short_put_qty_normalized=1.5,
        v14_short_put_entry_premium=100.0,
        v14_short_put_expiry=date(2026, 9, 18),
    )
    waiting = policy.apply_policy("IM", _signal("IM"), anchor, candidate={"tradable": False})
    assert waiting["v14_action"] == "PUBLISH_SHORT_PUT_EXPIRY_BRANCHES"
    assert waiting["v14_route_state"] == "short_put"
    assert waiting["v14_settlement_pending"] is True
    assert waiting["v14_signal_scope"] == "research_model_signal_only"
    assert waiting["v14_account_execution_status"] == "not_observed_out_of_scope"
    assert set(waiting["v14_expiry_conditional_signal"]) == {
        "expired_worthless", "assigned_or_cash_settled_itm", "basis"
    }

    waiting_anchor = {key: waiting[key] for key in policy.default_extension("IM")}
    expired = policy.apply_policy(
        "IM",
        _signal("IM", market_date=date(2026, 9, 21), next_trade_date=date(2026, 9, 22),
                v14_model_settlement_confirmed=True,
                v14_model_settlement_outcome="expired_worthless"),
        waiting_anchor,
        candidate={"tradable": False},
    )
    assert expired["v14_action"] == "SHORT_PUT_EXPIRE_WORTHLESS"
    assert expired["v14_route_state"] == "future"


def test_assignment_conversion_keeps_recovery_future_exposure_until_breakeven():
    anchor = policy.default_extension("IM")
    anchor.update(
        v14_route_state="short_put",
        v14_cycle_id="cycle",
        v14_short_put_contract="MO2610-P-7200",
        v14_short_put_qty_normalized=1.5,
        v14_short_put_entry_premium=100.0,
        v14_short_put_expiry=date(2026, 9, 18),
    )
    assigned = policy.apply_policy(
        "IM",
        _signal("IM", v14_model_settlement_confirmed=True, v14_model_settlement_outcome="assigned",
                v14_recovery_net_pnl_mark=-0.08),
        anchor,
        candidate={"tradable": False},
    )
    assert assigned["v14_route_state"] == "cash_wait"
    assert assigned["v14_cycle_id"] == "cycle"

    assigned_anchor = {key: assigned[key] for key in policy.default_extension("IM")}
    recovery = policy.apply_policy(
        "IM",
        _signal("IM", market_date=date(2026, 9, 21), next_trade_date=date(2026, 9, 22),
                 v14_model_recovery_future_confirmed=True, v14_recovery_net_pnl_mark=-0.06),
        assigned_anchor,
        candidate={"tradable": False},
    )
    assert recovery["v14_route_state"] == "recovery_future"
    assert recovery["core_units_target"] == 0.5
    assert recovery["core_put_target_qty_normalized"] == 0.0


def test_profit_trigger_is_idempotent_until_reentry_price_is_confirmed():
    anchor = policy.default_extension("IC")
    anchor.update(v14_core_put_entry_premium=10.0, v14_core_put_profit3x_eligible=True)
    hit = policy.apply_policy(
        "IC", _signal("IC", v14_core_put_mark=30.0), anchor,
        candidate=_candidate("IC", iv=0.20),
    )
    pending_anchor = {key: hit[key] for key in policy.default_extension("IC")}
    waiting = policy.apply_policy(
        "IC",
        _signal("IC", market_date=date(2026, 9, 21), next_trade_date=date(2026, 9, 22), v14_core_put_mark=31.0),
        pending_anchor,
        candidate=_candidate("IC", iv=0.20),
    )
    assert waiting["v14_action"] == "WAIT_CORE_PUT_PROFIT3X_REENTER"
    assert waiting["v14_profit_trigger_day"] == date(2026, 9, 18)
    executed_anchor = {key: waiting[key] for key in policy.default_extension("IC")}
    executed = policy.apply_policy(
        "IC",
        _signal("IC", market_date=date(2026, 9, 21), next_trade_date=date(2026, 9, 22),
                v14_profit_reentry_entry_premium=12.0),
        executed_anchor,
        candidate=_candidate("IC", iv=0.20),
    )
    assert executed["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    assert executed["v14_profit_pending"] is False
    assert executed["v14_core_put_entry_premium"] == 12.0


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_recovery_hold_does_not_invent_additional_futures(product):
    anchor = policy.default_extension(product)
    anchor.update(v14_route_state="recovery_future", v14_recovery_net_pnl=-0.1)
    result = policy.apply_policy(product, _signal(product), anchor, candidate={"tradable": False})
    assert result["v14_action"] == "HOLD_RECOVERY"
    assert result["core_units_current"] == result["core_units_target"] == 0.5
    assert result["total_units_change"] == 0.0


def test_monthly_maintenance_preempts_pending_profit_and_uses_monthly_cost():
    anchor = policy.default_extension("IC")
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 9, 17),
                  v14_profit_execution_day=date(2026, 9, 18))
    result = policy.apply_policy("IC", _signal("IC", option_monthly_reset_due=True,
        v14_core_put_target_entry_premium=20., v14_profit_reentry_entry_premium=12.),
        anchor, candidate={"tradable": False})
    assert result["v14_action"] == "HOLD"
    assert result["v14_core_put_entry_premium"] == 20.
    assert result["v14_profit_pending"] is False
    assert result["v14_profit_execution_day"] is None


def test_intraday_profit_hit_does_not_create_event():
    anchor = policy.default_extension("IC")
    anchor.update(v14_core_put_entry_premium=10., v14_core_put_profit3x_eligible=True)
    result = policy.apply_policy("IC", _signal("IC", close_confirmed=False,
        v14_core_put_mark=30.), anchor, candidate={"tradable": False})
    assert result["v14_action"] == "HOLD"
    assert result["v14_profit_pending"] is False


@pytest.mark.parametrize("day,confirmed", [(date(2026, 9, 18), True), (date(2026, 9, 21), False), (date(2026, 9, 22), True)])
def test_profit_execution_requires_scheduled_common_session_close(day, confirmed):
    anchor = policy.default_extension("IC")
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 9, 18),
                  v14_profit_execution_day=date(2026, 9, 21))
    result = policy.apply_policy("IC", _signal("IC", market_date=day, close_confirmed=confirmed,
        v14_profit_reentry_entry_premium=12.), anchor, candidate={"tradable": False})
    assert result["v14_profit_pending"] is True
    assert result["v14_action"] != "EXECUTE_CORE_PUT_PROFIT3X_REENTER"


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_fix7_profit_plan_selects_at_t_and_confirms_only_t1_open(product):
    anchor = policy.default_extension(product)
    anchor.update(v14_core_put_entry_premium=10.0, v14_core_put_profit3x_eligible=True)
    fields = {
        "v14_profit_plan_contract": "510500P2612M07000" if product == "IC" else "MO2612-P-7000",
        "v14_profit_plan_qty": 5 if product == "IC" else 1.5,
        "v14_profit_plan_security_id": "10012001" if product == "IC" else None,
        "v14_core_put_mark": 30.0,
    }
    if product == "IC":
        anchor.update(v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012000", v14_core_put_qty=5)
        fields.update(v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012000", v14_core_put_qty=5)
    else:
        fields.update(core_put_current_contract="MO2612-P-7500", core_put_current_qty_normalized=1.5)
    trigger = policy.apply_policy(product, _signal(product, market_date=date(2026, 9, 28),
        next_trade_date=date(2026, 9, 29), **fields), anchor, candidate={"tradable": False})
    assert trigger["v14_action"] == "CORE_PUT_PROFIT3X_REENTER"
    assert trigger["v14_profit_reentry_status"] == "scheduled_t_plus_1_open"
    assert trigger["v14_profit_reentry_contract"] == fields["v14_profit_plan_contract"]
    pending = {key: trigger[key] for key in policy.default_extension(product)}
    policy.validate_extension(product, pending)
    waiting = policy.apply_policy(product, _signal(product, market_date=date(2026, 9, 29),
        next_trade_date=date(2026, 9, 30), v14_profit_reentry_entry_premium=12.0),
        pending, candidate={"tradable": False})
    assert waiting["v14_action"] == "WAIT_CORE_PUT_PROFIT3X_REENTER"
    assert waiting["v14_profit_pending"] is True
    executed = policy.apply_policy(product, _signal(product, market_date=date(2026, 9, 29),
        next_trade_date=date(2026, 9, 30), v14_profit_reentry_status="confirmed_open_research_price",
        v14_profit_reentry_contract=fields["v14_profit_plan_contract"],
        v14_profit_reentry_entry_premium=12.0, v14_profit_exit_open_price=30.0,
        v14_profit_open_executed_qty=fields["v14_profit_plan_qty"],
        v14_profit_open_price_day=date(2026, 9, 29)), pending, candidate={"tradable": False})
    assert executed["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    assert executed["v14_core_put_entry_premium"] == 12.0
    assert executed["v14_profit_pending"] is False
    competing_route = policy.apply_policy(product, _signal(product, market_date=date(2026, 9, 29),
        next_trade_date=date(2026, 9, 30), v14_profit_reentry_status="confirmed_open_research_price",
        v14_profit_reentry_contract=fields["v14_profit_plan_contract"],
        v14_profit_reentry_entry_premium=12.0, v14_profit_exit_open_price=30.0,
        v14_profit_open_executed_qty=fields["v14_profit_plan_qty"],
        v14_profit_open_price_day=date(2026, 9, 29)), pending, candidate=_candidate(product))
    assert competing_route["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    assert competing_route["v14_route_state"] == "future"


def test_fix7_no_t_close_preselection_does_not_create_a_fake_open_order():
    anchor = policy.default_extension("IC")
    anchor.update(v14_core_put_entry_premium=10.0, v14_core_put_profit3x_eligible=True,
                  v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012000", v14_core_put_qty=5)
    result = policy.apply_policy("IC", _signal("IC", market_date=date(2026, 9, 28),
        next_trade_date=date(2026, 9, 29), v14_core_put_mark=30.0,
        v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012000", v14_core_put_qty=5),
        anchor, candidate={"tradable": False})
    assert result["v14_action"] == "HOLD"
    assert result["v14_profit_pending"] is False
    assert result["v14_action_reason"] == "profit3x_t_close_preselection_unavailable"


def test_fix7_monthly_reset_cancels_pending_open_execution():
    anchor = policy.default_extension("IC")
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 9, 28),
                  v14_profit_execution_day=date(2026, 9, 29),
                  v14_profit_old_contract="510500P2612M07500", v14_profit_old_security_id="10012000",
                  v14_profit_old_qty=5, v14_profit_reentry_contract="510500P2612M07000",
                  v14_profit_reentry_security_id="10012001", v14_profit_reentry_qty=5)
    result = policy.apply_policy("IC", _signal("IC", market_date=date(2026, 9, 29),
        option_monthly_reset_due=True, v14_core_put_target_entry_premium=20.0),
        anchor, candidate={"tradable": False})
    assert result["v14_action"] != "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    assert result["v14_profit_pending"] is False
    assert result["v14_profit_reentry_contract"] is None


def test_fix6_pending_crossing_fix7_boundary_keeps_old_close_semantics():
    anchor = policy.default_extension("IC")
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 9, 25),
                  v14_profit_execution_day=date(2026, 9, 28))
    result = policy.apply_policy("IC", _signal("IC", market_date=date(2026, 9, 28),
        v14_profit_reentry_entry_premium=12.0), anchor, candidate={"tradable": False})
    assert result["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"


@pytest.mark.parametrize("product,field", [("IC", "momentum_next_weight"), ("IM", "momentum_120")])
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf")])
def test_nonfinite_momentum_cannot_admit_seller(product, field, value):
    allowed, _ = policy.seller_permission(product, _signal(product, **{field: value}), _candidate(product))
    assert allowed is False


@pytest.mark.parametrize("premium", [float("nan"), float("inf"), -1., 0.])
def test_active_short_premium_must_be_finite_positive(premium):
    with pytest.raises(RuntimeError, match="incomplete"):
        policy.apply_policy("IM", _signal("IM"), policy.default_extension("IM"),
                            candidate=_candidate("IM", premium=premium))


def test_im_route_closes_core_put_without_changing_momentum_leg():
    result = policy.apply_policy("IM", _signal("IM", core_put_current_contract="MO2612-P-7200",
        core_put_target_contract="MO2612-P-7200", core_put_current_qty_normalized=0.5,
        core_put_action="HOLD", put_action="HOLD", momentum_put_action="HOLD",
        momentum_put_target_contract="MO2612-P-7000"), policy.default_extension("IM"), candidate=_candidate("IM"))
    assert result["core_put_target_contract"] is None
    assert result["put_target_contract"] is None
    assert result["core_put_action"] == result["put_action"] == "RESIZE_OR_ROLL"
    assert result["momentum_put_target_contract"] == "MO2612-P-7000"
    assert result["momentum_put_target_qty_normalized"] == 1.5


@pytest.mark.parametrize("monthly, expected", [(False, "HOLD"), (True, "RESIZE_OR_ROLL")])
def test_ic_route_preserves_momentum_put_monthly_maintenance(monthly, expected):
    anchor = policy.default_extension("IC")
    anchor.update(v14_route_state="recovery_future", v14_recovery_net_pnl=-0.1)
    result = policy.apply_policy("IC", _signal("IC", total_put_current_delta=0.25,
        option_monthly_reset_due=monthly, put_current_contract="510500P2612M07500",
        put_target_contract="510500P2612M07500"), anchor, candidate={"tradable": False})
    assert result["put_target_contract"] == "510500P2612M07500"
    assert result["total_put_target_delta"] == 0.25
    assert result["put_action"] == expected


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_intraday_settlement_confirmation_does_not_advance_route(product):
    entry = policy.apply_policy(product, _signal(product), policy.default_extension(product), candidate=_candidate(product))
    anchor = {key: entry[key] for key in policy.default_extension(product)}
    anchor["v14_short_put_expiry"] = date(2026, 9, 18)
    result = policy.apply_policy(product, _signal(product, close_confirmed=False,
        v14_settlement_confirmed=True, v14_settlement_outcome="expired_worthless"),
        anchor, candidate={"tradable": False})
    assert result["v14_route_state"] == "short_put"
    assert result["v14_action"] == "HOLD"


@pytest.mark.parametrize("tier", [-1., 0.5, float("nan"), float("inf")])
@pytest.mark.parametrize("product,field", [("IC", "valuation_tier"), ("IM", "valuation_puts_per_full_core")])
def test_seller_requires_exact_valid_valuation_tier(product, field, tier):
    allowed, _ = policy.seller_permission(product, _signal(product, **{field: tier}), _candidate(product))
    assert allowed is False


def test_independent_core_contract_fields_survive_producer_and_clear_on_route():
    signal = _signal("IC", v14_core_put_contract="510500P2612M07500",
        v14_core_put_security_id="10019999", v14_core_put_qty=3.)
    ordinary = policy.apply_policy("IC", signal, policy.default_extension("IC"), candidate={"tradable": False})
    assert ordinary["v14_core_put_contract"] == signal["v14_core_put_contract"]
    assert ordinary["v14_core_put_security_id"] == signal["v14_core_put_security_id"]
    assert ordinary["v14_core_put_qty"] == 3.
    routed = policy.apply_policy("IC", signal, ordinary, candidate=_candidate("IC"))
    assert routed["v14_core_put_contract"] is None
    assert routed["v14_core_put_security_id"] is None
    assert routed["v14_core_put_qty"] == 0.


@pytest.mark.parametrize("qty", [float("nan"), float("inf"), -1.])
def test_independent_core_quantity_requires_finite_nonnegative(qty):
    with pytest.raises(RuntimeError, match="core-Put quantity"):
        policy.apply_policy("IC", _signal("IC", v14_core_put_qty=qty),
            policy.default_extension("IC"), candidate={"tradable": False})
