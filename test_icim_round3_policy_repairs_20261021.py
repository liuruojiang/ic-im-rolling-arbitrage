"""Candidate consumption and required-evidence retry; all quotes are synthetic."""
from copy import deepcopy
from datetime import date, datetime

import pandas as pd
import pytest
import requests

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
from test_ic_im_v1_4_policy import _candidate, _signal


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network forbidden"))


def signal(product, **overrides):
    return _signal(product, market_date=date(2026, 12, 1), next_trade_date=date(2026, 12, 2),
                   core_eod_contract=product + "2612", **overrides)


def short_anchor(product, **overrides):
    candidate = _candidate(product)
    state = policy.default_extension(product)
    state.update(v14_route_state="short_put", v14_cycle_id="synthetic-verified-cycle",
                 v14_short_put_contract=candidate["contract"],
                 v14_short_put_security_id=candidate["security_id"],
                 v14_short_put_qty_normalized=candidate["qty_normalized"],
                 v14_short_put_entry_premium=1.0,
                 v14_short_put_expiry=date(2026, 12, 23 if product == "IC" else 18),
                 v14_short_put_entry_price_day=date(2026, 11, 19),
                 v14_short_put_cost_basis="confirmed_dated_open")
    state.update(overrides)
    policy.validate_extension(product, state)
    return state


def wire_adapter(monkeypatch, product, anchor=None, mark=1.0):
    anchor = deepcopy(anchor or policy.default_extension(product))
    anchor["post_core_contract"] = product + "2612"
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    anchors[product] = anchor
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", anchors)
    monkeypatch.setattr(bot, "_v14_ic_core_overlay", lambda *a: None)
    monkeypatch.setattr(bot, "_v14_prepare_lifecycle_evidence", lambda *a: None)
    monkeypatch.setattr(bot, "_v14_option_mark", lambda *a: (None, None))
    monkeypatch.setattr(bot, "_v14_prepare_profit_open_plan", lambda *a: None)
    monkeypatch.setattr(bot, "_v14_require_held_ic_short_identity", lambda *a: None)
    monkeypatch.setattr(bot, "fetch_option_closes", lambda *a: pd.Series(
        [mark], index=[pd.Timestamp(date(2026, 12, 1))]))
    frame = pd.DataFrame([dict(instrument=anchor.get("v14_short_put_contract") or "MO2612-P-7200",
                               lastprice=mark)])
    return anchor, frame


def forbid_candidate(*args, **kwargs):
    pytest.fail("unused M+1 candidate must not consume calendar or quotes")


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("day", [date(2026, 9, 28), date(2026, 9, 29), date(2026, 12, 1)])
@pytest.mark.parametrize("tier", [0, 1, 2, None])
@pytest.mark.parametrize("momentum", [-.001, 0.0, .01, None])
@pytest.mark.parametrize("close", [False, True])
def test_prerequisites_share_exact_permission_checks(product, day, tier, momentum, close):
    source = _signal(product, market_date=day, valuation_tier=tier,
                     valuation_puts_per_full_core=tier, momentum_120=momentum,
                     close_confirmed=close)
    before = deepcopy(source)
    prerequisite = policy.seller_prerequisites(product, source)
    assert prerequisite == policy.seller_permission(product, source, _candidate(product, iv=.99))
    required = policy.short_put_candidate_requirements(product, source, policy.default_extension(product))
    assert required["entry"] is prerequisite[0]
    assert required["roll"] is False
    assert source == before


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("route", ["assigned_etf", "cash_wait", "recovery_future"])
def test_recovery_routes_never_probe_entry_or_roll(product, route):
    anchor = {**policy.default_extension(product), "v14_route_state": route}
    before = deepcopy(anchor)
    required = policy.short_put_candidate_requirements(product, signal(product), anchor)
    assert required["entry"] is required["roll"] is False
    assert anchor == before


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("mark,pending,cost,early,day,expected", [
    (1.0, False, "confirmed_dated_open", False, date(2026, 12, 1), False),
    (.20, False, "confirmed_dated_open", False, date(2026, 12, 1), True),
    (1.0, True, "confirmed_dated_open", False, date(2026, 12, 1), True),
    (.20, False, None, False, date(2026, 12, 1), False),
    (.20, False, "confirmed_dated_open", True, date(2026, 9, 25), False),
    (.20, False, "confirmed_dated_open", True, date(2026, 9, 26), True),
])
def test_roll_candidate_only_after_shared_decay_trigger(product, mark, pending, cost, early, day, expected):
    anchor = short_anchor(product, v14_short_put_cost_basis=cost,
                          v14_short_put_entry_price_day=date(2026, 9, 21) if cost else None,
                          v14_roll_pending=pending, v14_roll_trigger_day=day if pending else None,
                          v14_early_roll_used=early)
    source = _signal(product, market_date=day, next_trade_date=date(2026, 12, 2),
                     v14_short_put_mark=mark)
    required = policy.short_put_candidate_requirements(product, source, anchor)
    assert required["entry"] is False
    assert required["roll"] is expected


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_expiry_event_takes_priority_over_candidate(product):
    anchor = short_anchor(product, v14_short_put_expiry=date(2026, 12, 1))
    required = policy.short_put_candidate_requirements(product,
        signal(product, v14_short_put_mark=.01), anchor)
    assert required == dict(entry=False, roll=False, reason="settlement_has_priority")


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_committed_profit_open_precedes_new_entry_candidate(product):
    candidate = _candidate(product)
    anchor = policy.default_extension(product)
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 11, 30),
                  v14_profit_execution_day=date(2026, 12, 1),
                  v14_profit_old_contract=candidate["contract"],
                  v14_profit_old_security_id=candidate["security_id"],
                  v14_profit_old_qty=1.,
                  v14_profit_reentry_contract=candidate["contract"],
                  v14_profit_reentry_security_id=candidate["security_id"],
                  v14_profit_reentry_qty=1.)
    required = policy.short_put_candidate_requirements(product, signal(product), anchor)
    assert required == dict(entry=False, roll=False, reason="committed_profit_open_has_priority")
    # Existing maintenance priority is retained, rather than adding a new
    # blanket seller prohibition whenever a profit plan exists.
    monthly = policy.short_put_candidate_requirements(product,
        signal(product, option_monthly_reset_due=True), anchor)
    assert monthly["entry"] is True


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pending_short_open_precedes_any_candidate(product):
    source = signal(product)
    source["core_eod_contract"] = product + "2612"
    anchor = {**policy.default_extension(product), "post_core_contract": product + "2612"}
    opened = policy.apply_policy(product, source, anchor, candidate=_candidate(product))
    assert opened["v14_short_put_open_pending"]
    required = policy.short_put_candidate_requirements(product, source, opened)
    assert required == dict(entry=False, roll=False, reason="opening_evidence_not_confirmed")


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("tier,momentum,close", [(2, .1, True), (0, -.1, True), (0, .1, False)])
def test_unused_candidate_does_not_taint_other_sleeves_or_calendar(monkeypatch, product, tier, momentum, close):
    anchor, frame = wire_adapter(monkeypatch, product)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", forbid_candidate)
    source = signal(product, valuation_tier=tier, valuation_puts_per_full_core=tier,
                    momentum_120=momentum, close_confirmed=close,
                    core_target=product + "2612", momentum_next_weight=.5, grid_target=.5,
                    put_target_contract="unchanged-protective-contract")
    context = dict(expiries=[], failures=[], signal={}, future={})
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        out = bot._apply_v14_live_policy(product, source, mo_quotes=frame)
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
    assert out["v14_action"] == "HOLD"
    for field in ["core_target", "momentum_next_weight", "grid_target", "put_target_contract"]:
        assert out[field] == source[field]
    assert context["expiries"] == context["failures"] == []
    assert out["v14_candidate_data_status"].startswith("not_required:")


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_unchanged_short_does_not_probe_entry_or_roll(monkeypatch, product):
    anchor, frame = wire_adapter(monkeypatch, product, short_anchor(product), mark=.8)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", forbid_candidate)
    out = bot._apply_v14_live_policy(product, signal(product), mo_quotes=frame)
    assert out["v14_action"] == "HOLD"
    assert out["v14_short_put_contract"] == anchor["v14_short_put_contract"]
    assert out["v14_short_put_entry_premium"] == 1.0


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_triggered_roll_consumes_only_replacement_candidate(monkeypatch, product):
    anchor, frame = wire_adapter(monkeypatch, product, short_anchor(product), mark=.2)
    calls = []
    replacement = _candidate(product, expiry=date(2027, 1, 27),
        contract="510500P2701M07500" if product == "IC" else "MO2701-P-7200")
    def candidate(*args, roll_from_expiry=None):
        calls.append(roll_from_expiry)
        assert roll_from_expiry == anchor["v14_short_put_expiry"]
        return replacement
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", candidate)
    out = bot._apply_v14_live_policy(product, signal(product), mo_quotes=frame)
    assert calls == [anchor["v14_short_put_expiry"]]
    assert out["v14_action"] == "ROLL_SHORT_PUT"
    assert out["v14_short_put_contract"] == replacement["contract"]


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pending_roll_with_failed_prerequisite_waits_without_lookup(monkeypatch, product):
    anchor = short_anchor(product, v14_roll_pending=True,
                          v14_roll_trigger_day=date(2026, 11, 30))
    anchor, frame = wire_adapter(monkeypatch, product, anchor)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", forbid_candidate)
    out = bot._apply_v14_live_policy(product, signal(product, momentum_120=-.01), mo_quotes=frame)
    assert out["v14_action"] == "WAIT_SHORT_PUT_ROLL"
    assert out["v14_roll_pending"] is True
    assert out["v14_roll_trigger_day"] == date(2026, 11, 30)
    assert out["v14_short_put_contract"] == anchor["v14_short_put_contract"]


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_required_unknown_calendar_remains_unresolved(monkeypatch, product):
    _, frame = wire_adapter(monkeypatch, product)
    def candidate(*args, **kwargs):
        bot._require_official_exchange_calendar(date(2027, 1, 15), "required candidate expiry")
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", candidate)
    context = dict(expiries=[], failures=[], signal={}, future={})
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        out = bot._apply_v14_live_policy(product, signal(product), mo_quotes=frame)
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
    assert out["v14_action"] == "HOLD_FAIL_CLOSED"
    assert context["failures"]
    assert out["v14_short_put_contract"] is None
    assert out["v14_short_put_open_pending"] is None


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_required_candidate_data_unknown_is_not_unlisted(monkeypatch, product):
    _, frame = wire_adapter(monkeypatch, product)
    def candidate(*args, **kwargs):
        raise RuntimeError("source payload unavailable")
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", candidate)
    out = bot._apply_v14_live_policy(product, signal(product), mo_quotes=frame)
    assert out["v14_action"] == "HOLD_FAIL_CLOSED"
    assert "unavailable" in out["v14_candidate_data_status"]
    assert out["v14_short_put_open_pending"] is None


@pytest.mark.parametrize("error", [bot.SignalTransportError("transient"),
                                  requests.ConnectionError("transient"), requests.Timeout("transient")])
def test_required_core_transport_reaches_outer_retry(monkeypatch, error):
    _, frame = wire_adapter(monkeypatch, "IC")
    def overlay(*args):
        raise error
    monkeypatch.setattr(bot, "_v14_ic_core_overlay", overlay)
    with pytest.raises(type(error), match="transient"):
        bot._apply_v14_live_policy("IC", signal("IC", valuation_tier=2), mo_quotes=frame)


def test_required_held_mark_transport_reaches_outer_retry_even_if_candidate_unused(monkeypatch):
    _, frame = wire_adapter(monkeypatch, "IC", short_anchor("IC"))
    def failed(*args):
        raise requests.Timeout("held mark transport")
    monkeypatch.setattr(bot, "fetch_option_closes", failed)
    with pytest.raises(requests.Timeout, match="held mark transport"):
        bot._apply_v14_live_policy("IC", signal("IC", valuation_tier=2), mo_quotes=frame)


def test_unused_candidate_does_not_bypass_held_identity(monkeypatch):
    _, frame = wire_adapter(monkeypatch, "IC", short_anchor("IC"))
    def failed(*args):
        raise RuntimeError("held identity mismatch")
    monkeypatch.setattr(bot, "_v14_require_held_ic_short_identity", failed)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    out = bot._apply_v14_live_policy("IC", signal("IC", valuation_tier=2), mo_quotes=frame)
    assert out["v14_action"] == "HOLD_FAIL_CLOSED"
    assert out["v14_held_put_identity_status"] == "unverified"


def test_bounded_transport_retry_retries_adapter_then_preserves_result(monkeypatch):
    _, frame = wire_adapter(monkeypatch, "IC")
    attempts = []
    def overlay(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise bot.SignalTransportError("one transient")
    monkeypatch.setattr(bot, "_v14_ic_core_overlay", overlay)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    monkeypatch.setattr(bot, "build_live_trade_signal", lambda *a, **k:
        bot._apply_v14_live_policy("IC", signal("IC", valuation_tier=2), mo_quotes=frame))
    out = bot.build_live_signal_with_transport_retry("IC", "close", 45.)
    assert len(attempts) == 2
    assert out["v14_action"] == "HOLD"
    assert any("重试一次成功" in note for note in out["data_notes"])


def profit_ready(product):
    held = _candidate(product)
    anchor = policy.default_extension(product)
    anchor.update(v14_core_put_entry_premium=1., v14_core_put_profit3x_eligible=True,
                  v14_core_put_contract=held["contract"],
                  v14_core_put_security_id=held["security_id"], v14_core_put_qty=2.)
    source = signal(product, v14_core_put_contract=held["contract"],
                    v14_core_put_security_id=held["security_id"], v14_core_put_qty=2.,
                    put_target_core_qty=2, core_put_target_delta=.25, etf_price=7., future_last=8000.,
                    put_reference_price=8000., core_put_current_contract=held["contract"],
                    core_put_current_qty_normalized=1.5, core_put_target_qty_normalized=1.5)
    return anchor, source


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_admission_priority_skips_unconsumed_profit_preselection(monkeypatch, product):
    # Independent audit_p1 counterexample: a simultaneous 3x trigger must not
    # estimate a future expiry when the actual policy enters a seller route.
    prepare_profit = bot._v14_prepare_profit_open_plan
    anchor, source = profit_ready(product)
    _, frame = wire_adapter(monkeypatch, product, anchor)
    monkeypatch.setattr(bot, "_v14_prepare_profit_open_plan", prepare_profit)
    monkeypatch.setattr(bot, "_v14_option_mark", lambda *a: (3., None))
    calls = []
    def unused_selection(*args, **kwargs):
        calls.append(True)
        bot._require_model_expiry_calendar(date(2027, 3, 19), "unused_profit_selection")
        raise RuntimeError("this new 3x selection is not consumed")
    monkeypatch.setattr(bot, "select_ic_put_for_reset", unused_selection)
    monkeypatch.setattr(bot, "select_im_put_for_reset", unused_selection)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", lambda *a, **k: _candidate(product))
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", lambda *a, **k: _candidate(product))
    context = dict(expiries=[], failures=[], signal={}, future={})
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        out = bot._apply_v14_live_policy(product, source, mo_quotes=frame)
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
    assert out["v14_action"] == "ENTER_SHORT_PUT"
    assert not out["v14_profit_pending"]
    assert calls == context["expiries"] == context["failures"] == []


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_consumed_new_profit_trigger_preselects_once_after_route_decision(monkeypatch, product):
    prepare_profit = bot._v14_prepare_profit_open_plan
    anchor, source = profit_ready(product)
    source["momentum_120"] = -.01  # Seller admission cannot consume a candidate.
    _, frame = wire_adapter(monkeypatch, product, anchor)
    monkeypatch.setattr(bot, "_v14_prepare_profit_open_plan", prepare_profit)
    monkeypatch.setattr(bot, "_v14_option_mark", lambda *a: (3., None))
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", forbid_candidate)
    calls = []
    def selected(*args, **kwargs):
        calls.append(True)
        if product == "IC":
            return dict(contract="510500P2612M07500", security_id="10019998", qty=2,
                        quote=pd.Series(dict(last=.1)),
                        stamp=dict(date="20261201", time="150000", source="synthetic"))
        return pd.Series(dict(instrument="MO2612-P-7600"))
    monkeypatch.setattr(bot, "select_ic_put_for_reset", selected)
    monkeypatch.setattr(bot, "select_im_put_for_reset", selected)
    with bot.runtime_clock(datetime(2026, 12, 1, 15, 40, tzinfo=bot.BEIJING)):
        out = bot._apply_v14_live_policy(product, source, mo_quotes=frame)
    assert calls == [True]
    assert out["v14_action"] == "CORE_PUT_PROFIT3X_REENTER"
    assert out["v14_profit_pending"] is True


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_committed_profit_does_not_repeat_preselection(monkeypatch, product):
    anchor, source = profit_ready(product)
    held = _candidate(product)
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026, 11, 30),
                  v14_profit_execution_day=date(2026, 12, 1),
                  v14_profit_old_contract=held["contract"],
                  v14_profit_old_security_id=held["security_id"], v14_profit_old_qty=2.,
                  v14_profit_reentry_contract=held["contract"],
                  v14_profit_reentry_security_id=held["security_id"], v14_profit_reentry_qty=2.)
    _, frame = wire_adapter(monkeypatch, product, anchor)
    monkeypatch.setattr(bot, "_v14_prepare_profit_open_plan", forbid_candidate)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", forbid_candidate)
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", forbid_candidate)
    out = bot._apply_v14_live_policy(product, source, mo_quotes=frame)
    assert out["v14_action"] == "WAIT_CORE_PUT_PROFIT3X_REENTER"


@pytest.mark.parametrize("error_type", [bot.SignalTransportError, requests.ConnectionError, requests.Timeout])
def test_consumed_profit_preselection_transport_reaches_retry(monkeypatch, error_type):
    prepare_profit = bot._v14_prepare_profit_open_plan
    anchor, source = profit_ready("IC")
    source["momentum_120"] = -.01
    _, frame = wire_adapter(monkeypatch, "IC", anchor)
    monkeypatch.setattr(bot, "_v14_prepare_profit_open_plan", prepare_profit)
    monkeypatch.setattr(bot, "_v14_option_mark", lambda *a: (3., None))
    def selection(*args, **kwargs):
        raise error_type("required profit preselection transport")
    monkeypatch.setattr(bot, "select_ic_put_for_reset", selection)
    with pytest.raises(error_type, match="required profit preselection transport"):
        bot._apply_v14_live_policy("IC", source, mo_quotes=frame)
