"""Pure expiry comparisons never authorize unknown-year selection or execution."""
from copy import deepcopy
from datetime import date

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import test_icim_batch3_old_call_20261021 as seed


@pytest.fixture
def diagnostics():
    value = {"expiries": [], "failures": [], "signal": {}, "future": {}}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(value)
    try:
        yield value
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)


@pytest.mark.parametrize("reference", [date(2026, 10, 21), date(2026, 12, 21), date(2026, 12, 31)])
@pytest.mark.parametrize("expiry", [date(2027, 1, 15), date(2027, 3, 19)])
def test_future_lower_bound_proves_comparison_without_provisional_record(reference, expiry, diagnostics):
    assert bot._expiry_definitely_after_reference(expiry, reference) is True
    assert diagnostics["expiries"] == diagnostics["failures"] == []


@pytest.mark.parametrize("expiry,reference", [
    (date(2026, 12, 18), date(2026, 10, 21)),  # covered exact date uses ordinary guard
    (date(2026, 10, 16), date(2026, 10, 21)),
    (date(2025, 12, 19), date(2026, 10, 21)),  # unknown earlier year is not this proof
])
def test_helper_does_not_claim_other_cases_are_verified(expiry, reference):
    assert bot._expiry_definitely_after_reference(expiry, reference) is False


def test_unknown_reference_is_not_accepted_from_its_weekday(diagnostics):
    with pytest.raises(bot.CalendarCoverageError, match="2027"):
        bot._expiry_definitely_after_reference(date(2028, 1, 21), date(2027, 12, 30))
    assert diagnostics["failures"] and not diagnostics["expiries"]


@pytest.mark.parametrize("today", [date(2026, 12, 21), date(2026, 12, 30)])
@pytest.mark.parametrize("close_confirmed", [False, True])
def test_pre_expiry_false_is_proven_against_official_next_session(today, close_confirmed, diagnostics):
    assert bot._is_pre_expiry_close(today, date(2027, 1, 15), close_confirmed) is False
    assert diagnostics["expiries"] == diagnostics["failures"] == []


def test_pre_expiry_does_not_guess_next_session_across_year(diagnostics):
    with pytest.raises(bot.CalendarCoverageError, match="2027"):
        bot._is_pre_expiry_close(date(2026, 12, 31), date(2027, 1, 15), True)
    assert diagnostics["failures"]


@pytest.mark.parametrize("close_confirmed,expected", [(False, False), (True, True)])
def test_covered_pre_expiry_behavior_is_preserved(close_confirmed, expected, diagnostics):
    assert bot._is_pre_expiry_close(date(2026, 12, 17), date(2026, 12, 18), close_confirmed) is expected
    assert diagnostics["expiries"] == diagnostics["failures"] == []


def test_model_use_and_formal_target_are_still_strict(diagnostics):
    expiry = date(2027, 3, 19)
    assert bot._expiry_definitely_after_reference(expiry, seed.DAY)
    bot._require_model_expiry_calendar(expiry, "quantity model remains unverified")
    assert diagnostics["expiries"][0]["purpose"] == "quantity model remains unverified"
    with pytest.raises(bot.CalendarCoverageError, match="2027"):
        bot.require_signal_calendar("IM", dict(
            market_date=seed.DAY, next_trade_date=date(2026, 10, 22),
            core_target="IM2612", core_current="IM2612",
            put_target_contract="MO2703-P-7200", core_put_target_contract="MO2703-P-7200",
            core_put_target_qty_normalized=1.5,
        ))


@pytest.mark.parametrize("with_momentum", [False, True])
def test_public_held_maintenance_comparisons_do_not_record_estimates_but_delivery_stays_strict(
        monkeypatch, with_momentum):
    anchors, quotes = seed.wire(monkeypatch, held=False)
    contract = "MO2703-P-7200"
    anchor = bot.LIVE_CONTINUATION_ANCHOR["IM"]
    anchor.update(post_put_contract=contract, post_core_put_contract=contract)
    quotes.loc[quotes.instrument.eq("MO2612-P-7200"), "instrument"] = contract
    quotes.attrs["listed_instruments"] = quotes.instrument.tolist()
    if with_momentum:
        anchor.update(post_momentum_put_contract=contract,
                      verified_momentum_weight=0.5, verified_next_momentum_weight=0.5,
                      verified_momentum_put_qty_normalized=0.75,
                      verified_total_put_qty_normalized=2.25, verified_put_qty_normalized=2.25,
                      post_momentum_put_equivalent_units=0.375, post_put_equivalent_units=1.125)
        live = bot.live_proxy("IM", seed.CLOCK)
        live.update(momentum_current_weight=0.5, momentum_next_weight=0.5)
        monkeypatch.setattr(bot, "live_proxy", lambda *_args: deepcopy(live))
    def no_network(*_args, **_kwargs):
        raise AssertionError("unexpected network")
    monkeypatch.setattr(bot.requests, "get", no_network)
    signal = bot.build_live_trade_signal("IM", seed.CLOCK, "close")
    # The comparison no longer contaminates model-expiry diagnostics. Separate
    # delivery checks still reject this unverified-year formal target.
    assert signal["diagnostic_only"] is True
    assert signal["persistence_eligible"] is False
    purposes = [row["purpose"] for row in signal["calendar_diagnostics"]]
    assert "held IM core Put maintenance" not in purposes
    assert "held IM momentum Put maintenance" not in purposes
    assert any("put_target" in purpose for purpose in purposes)
