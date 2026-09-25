"""Regression-first tests for the user-approved IC/IM repeat short-Put roll."""
from datetime import date

import pytest

import ic_im_v1_4_policy as policy
from test_ic_im_v1_4_policy import _candidate, _signal


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_repeat_roll_can_succeed_twice_in_same_cycle_after_new_effective_date(product):
    anchor = policy.default_extension(product)
    first = _candidate(product)
    anchor.update(v14_route_state="short_put", v14_cycle_id="retained-cycle",
                  v14_short_put_contract=first["contract"],
                  v14_short_put_security_id=first["security_id"],
                  v14_short_put_qty_normalized=first["qty_normalized"],
                  v14_short_put_entry_premium=first["premium"],
                  v14_short_put_expiry=first["expiry"],
                  v14_early_roll_used=True)
    second = _candidate(product, contract=("510500P2611M07500" if product == "IC" else "MO2611-P-7200"),
                        expiry=date(2026, 11, 25) if product == "IC" else date(2026, 11, 20),
                        premium=.10 if product == "IC" else 75.0)
    mark = .05 if product == "IC" else 25.0
    once = policy.apply_policy(product,
        _signal(product, market_date=date(2026, 9, 28), next_trade_date=date(2026, 9, 29), v14_short_put_mark=mark),
        anchor, candidate=second, roll_candidate=second)
    assert once["v14_action"] == "ROLL_SHORT_PUT"
    assert once["v14_early_roll_used"] is False
    assert once["v14_cycle_id"] == "retained-cycle"
    third = _candidate(product, contract=("510500P2612M07500" if product == "IC" else "MO2612-P-7200"),
                       expiry=date(2026, 12, 23) if product == "IC" else date(2026, 12, 18),
                       premium=.08 if product == "IC" else 70.0)
    next_anchor = {key: once[key] for key in policy.default_extension(product)}
    again = policy.apply_policy(product,
        _signal(product, market_date=date(2026, 9, 29), next_trade_date=date(2026, 9, 30),
                v14_short_put_mark=.04 if product == "IC" else 20.0),
        next_anchor, candidate=third, roll_candidate=third)
    assert again["v14_action"] == "ROLL_SHORT_PUT"
    assert again["v14_short_put_contract"] == third["contract"]
    assert again["v14_early_roll_used"] is False
    assert again["v14_cycle_id"] == "retained-cycle"


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_old_signal_day_keeps_one_successful_roll_limit(product):
    first = _candidate(product)
    anchor = policy.default_extension(product)
    anchor.update(v14_route_state="short_put", v14_cycle_id="old-cycle",
                  v14_short_put_contract=first["contract"],
                  v14_short_put_security_id=first["security_id"],
                  v14_short_put_qty_normalized=first["qty_normalized"],
                  v14_short_put_entry_premium=first["premium"],
                  v14_short_put_expiry=first["expiry"], v14_early_roll_used=True)
    next_leg = _candidate(product, contract=("510500P2611M07500" if product == "IC" else "MO2611-P-7200"),
                          expiry=date(2026, 11, 25) if product == "IC" else date(2026, 11, 20))
    out = policy.apply_policy(product,
        _signal(product, market_date=date(2026, 9, 25), next_trade_date=date(2026, 9, 28),
                v14_short_put_mark=.05 if product == "IC" else 25.0),
        anchor, candidate=next_leg, roll_candidate=next_leg)
    assert out["v14_action"] != "ROLL_SHORT_PUT"
    assert out["v14_early_roll_used"] is True


def test_repeat_roll_would_be_fail_closed_until_new_leg_iv_passes():
    first = _candidate("IM")
    anchor = policy.default_extension("IM")
    anchor.update(v14_route_state="short_put", v14_cycle_id="wait-cycle",
                  v14_short_put_contract=first["contract"],
                  v14_short_put_qty_normalized=1.5,
                  v14_short_put_entry_premium=100.0,
                  v14_short_put_expiry=first["expiry"], v14_early_roll_used=True)
    blocked = _candidate("IM", tradable=False, iv=.30)
    waiting = policy.apply_policy("IM", _signal("IM", market_date=date(2026, 9, 28),
        next_trade_date=date(2026, 9, 29), v14_short_put_mark=30.0),
        anchor, candidate=blocked, roll_candidate=blocked)
    assert waiting["v14_action"] == "WAIT_SHORT_PUT_ROLL"
    assert waiting["v14_roll_pending"] is True
    eligible = _candidate("IM", contract="MO2611-P-7200", expiry=date(2026, 11, 20), iv=.40)
    resumed = policy.apply_policy("IM", _signal("IM", market_date=date(2026, 9, 29),
        next_trade_date=date(2026, 9, 30), v14_short_put_mark=85.0),
        {key: waiting[key] for key in policy.default_extension("IM")},
        candidate=eligible, roll_candidate=eligible)
    assert resumed["v14_action"] == "ROLL_SHORT_PUT"
    assert resumed["v14_roll_pending"] is False


def test_new_build_preserves_fix3_and_fix4_date_identity():
    assert policy.identity_for_signal_day(date(2026, 9, 23)) == (policy.FIX3_BUILD_ID, policy.FIX3_RULE_REVISION)
    assert policy.identity_for_signal_day(date(2026, 9, 24)) == (policy.FIX4_BUILD_ID, policy.FIX4_RULE_REVISION)
    assert policy.identity_for_signal_day(date(2026, 9, 25)) == (policy.FIX4_BUILD_ID, policy.FIX4_RULE_REVISION)
    assert policy.identity_for_signal_day(date(2026, 9, 26)) == (policy.BUILD_ID, policy.RULE_REVISION)

