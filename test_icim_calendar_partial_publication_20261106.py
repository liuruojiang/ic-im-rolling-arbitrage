"""Authorized new-Put calendar partial publication; synthetic inputs, no live I/O."""
from copy import deepcopy
import pytest
import poe_ic_im_v1_4_state as state
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_new_put_calendar_partial_20261106 import wire_new_momentum, CLOCK


def partial(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network forbidden"))
    wire_new_momentum(monkeypatch)
    live = bot.live_proxy
    def aligned(*args):
        result = live(*args)
        result.update(momentum_abs=-.1, momentum_abs20=-.1, momentum_abs_days=20,
                      momentum_abs_on=False, momentum_abs20_reentry_confirmed=False,
                      momentum_abs_debounce_active=True)
        return result
    monkeypatch.setattr(bot, "live_proxy", aligned)
    return bot.build_live_trade_signal("IM", CLOCK, "close")


def test_calendar_partial_publication_is_not_persistence_and_never_mutates(monkeypatch):
    signal = partial(monkeypatch)
    original = deepcopy(signal)
    state.validate_calendar_partial_publication(signal, "IM")
    state.validate_delivery_values(signal, "IM", require_complete=False)
    assert signal == original
    assert signal["momentum_put_target_contract"] is None
    assert signal["momentum_put_target_qty_normalized"] is None
    assert signal["total_put_target_qty_normalized"] is None
    with pytest.raises(RuntimeError):
        state.validate_delivery_values(signal, "IM")
    with pytest.raises(RuntimeError):
        state.require_persistence_eligible(signal, "IM")


@pytest.mark.parametrize("field,value", [
    ("calendar_partial_revision", "unknown"), ("calendar_provisional", False),
    ("signal_complete", True), ("persistence_eligible", True),
    ("futures_target_confirmed", False), ("diagnostic_only", True),
    ("unconfirmed_put_legs", ["core", "momentum"]),
    ("momentum_put_target_qty_normalized", 0), ("momentum_put_target_contract", "MO2612-P-7200"),
    ("total_put_target_qty_normalized", 1.5),
    ("momentum_put_current_qty_normalized", .75), ("momentum_put_current_contract", "MO2612-P-7200"),
    ("core_put_target_contract", "MO2701-P-8200"), ("core_put_target_qty_normalized", 0),
    ("core_target", "IM2703"), ("v14_route_state", "short_put"),
    ("option_monthly_reset_due", True), ("roll_confirmed", True),
    ("v14_profit_pending", True), ("v14_profit_open_executed", True),
    ("v14_ordinary_put_pending", {"legs": {}}), ("v14_short_put_open_pending", {}),
    ("v14_opening_events", [{"leg": "momentum", "status": "confirmed"}]),
    ("momentum_put_required_qty_normalized", 0), ("momentum_put_required_qty_normalized", 1.5),
    ("calendar_diagnostics", []), ("momentum_next_weight", 1),
    ("momentum_score", -1), ("momentum_volume_ratio", .5),
    ("momentum_units_target", .5), ("total_units_target", 5),
    ("strategy_version", "1.3"), ("im_execution_fix_revision", "old"),
    ("next_trade_date", "2026-11-10"), ("signal_generated_at", "2026-11-06T15:10:00"),
])
def test_calendar_partial_rejects_out_of_scope_or_forged_fields(monkeypatch, field, value):
    signal = partial(monkeypatch)
    signal[field] = value
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        state.validate_calendar_partial_publication(signal, "IM")


def test_wrong_product_and_unknown_calendar_evidence_fail_closed(monkeypatch):
    signal = partial(monkeypatch)
    with pytest.raises(RuntimeError):
        state.validate_calendar_partial_publication(signal, "IC")
    for evidence in ([{"date": "2026-12-18", "purpose": "independent IM Put expiry", "officially_verified": False}],
                     [{"date": "2027-01-15", "purpose": "held IM core Put maintenance", "officially_verified": False}],
                     [{"date": "2027-01-15", "purpose": "independent IM Put expiry", "officially_verified": True}]):
        signal["calendar_diagnostics"] = evidence
        with pytest.raises(RuntimeError):
            state.validate_calendar_partial_publication(signal, "IM")


def test_normal_complete_financial_signal_still_uses_original_validator(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network forbidden"))
    wire_new_momentum(monkeypatch, unknown=False)
    signal = bot.build_live_trade_signal("IM", CLOCK, "close")
    before = deepcopy(signal)
    state.validate_delivery_values(signal, "IM", require_complete=False)
    assert signal == before and signal.get("calendar_partial_revision") is None


def test_intraday_volume_placeholder_keeps_prior_verified_ratio_rule(monkeypatch):
    signal = partial(monkeypatch)
    signal["momentum_volume_placeholder"] = True
    original = deepcopy(signal)
    state.validate_calendar_partial_publication(signal, "IM")
    assert signal == original
    signal["momentum_volume_ratio"] = .2
    with pytest.raises(RuntimeError):
        state.validate_calendar_partial_publication(signal, "IM")


@pytest.mark.parametrize("changes", [
    {"v14_opening_events": [{"kind": "ordinary", "plan": {"legs": {"momentum": {}}}}]},
    {"momentum_put_action": "BUY"}, {"put_action": "HOLD"},
    {"momentum_abs_on": True},
    {"momentum_abs_days": 40}, {"momentum_abs_debounce_active": False},
])
def test_calendar_partial_rejects_execution_or_abs_factor_conflicts(monkeypatch, changes):
    signal = partial(monkeypatch)
    signal.update(changes)
    with pytest.raises(RuntimeError):
        state.validate_calendar_partial_publication(signal, "IM")


def test_real_intraday_builder_partial_validates_without_becoming_ledger(monkeypatch):
    from test_icim_new_put_calendar_partial_20261106 import INTRADAY_CLOCK
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network forbidden"))
    wire_new_momentum(monkeypatch, intraday=True)
    signal = bot.build_live_trade_signal("IM", INTRADAY_CLOCK, "intraday")
    original = deepcopy(signal)
    assert signal["close_confirmed"] is False and signal["market_phase"] == "盘中"
    state.validate_delivery_values(signal, "IM", require_complete=False)
    assert signal == original
    with pytest.raises(RuntimeError):
        state.require_persistence_eligible(signal, "IM")
