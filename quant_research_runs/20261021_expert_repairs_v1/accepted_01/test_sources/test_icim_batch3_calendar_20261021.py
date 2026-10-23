"""Calendar coverage is required for consumed dates, not unrelated far-month quotes."""
from datetime import date
import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state


def mo():
    return pd.DataFrame([
        dict(instrument='MO2612-P-7000',lastprice=100.,volume=100,position=100),
        dict(instrument='MO2703-P-7000',lastprice=100.,volume=100,position=100)])


def test_cross_year_next_session_is_not_assumed_to_be_january_first():
    with pytest.raises(RuntimeError,match='2027'):
        bot._is_pre_expiry_close(date(2026,12,31),date(2027,1,15),True)


def test_selected_monthly_maintenance_date_requires_covered_year():
    with pytest.raises(RuntimeError,match='2027'):
        bot._v14_monthly_option_calendar('IM',date(2026,12,21),date(2027,1,15),False)


@pytest.mark.parametrize('selector',[bot.select_im_put_for_reset,bot.select_independent_im_put_for_reset])
def test_selected_unknown_year_put_expiry_cannot_be_used_for_reset(selector):
    with pytest.raises(RuntimeError,match='2027'):
        selector(mo(),date(2026,11,20),7000.)


@pytest.mark.parametrize('selector',[bot.select_im_put_for_reset,bot.select_independent_im_put_for_reset])
def test_unused_unknown_far_month_does_not_block_covered_selected_put(selector):
    selected=selector(mo(),date(2026,10,21),7000.)
    assert selected['instrument']=='MO2612-P-7000'


def test_selected_unknown_year_ic_put_is_stopped_before_quote_fetch(monkeypatch):
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202612','202703'])
    def forbidden(*args,**kwargs):
        raise AssertionError('unverified selected expiry must stop before quote consumption')
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',forbidden)
    with pytest.raises(RuntimeError,match='2027'):
        bot.select_ic_put_for_reset(date(2026,11,20),7.,7000.,.25)


def test_short_ic_candidate_unknown_expiry_stops_before_market_consumption(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('unverified short expiry must stop before quotes')
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',forbidden)
    signal=dict(market_date=date(2026,12,17),next_trade_date=date(2026,12,18))
    with pytest.raises(RuntimeError,match='2027'):
        bot._v14_ic_short_put_candidate(signal)


def test_strict_forward_roll_checks_its_starting_day():
    with pytest.raises(RuntimeError,match='2027'):
        bot._roll_forward_exchange_day(date(2027,1,1),require_official=True)


def test_strict_backward_roll_checks_its_starting_day():
    with pytest.raises(RuntimeError,match='2027'):
        bot._roll_backward_exchange_day(date(2027,1,1),require_official=True)


def test_calendar_warning_never_calls_weekday_fallback_an_approved_expiry():
    text=bot._calendar_coverage_note([date(2027,3,19)])
    assert '2027' in text and '未核验' in text
    assert '暂按周一至周五回退' not in text


def test_prepared_new_signal_requires_the_next_official_session_year():
    signal=dict(market_date=date(2026,12,31),next_trade_date=date(2027,1,1))
    with pytest.raises(RuntimeError,match='2027'):
        bot.require_signal_calendar('IC',signal)


def test_prepared_new_short_expiry_is_independently_rejected():
    signal=dict(market_date=date(2026,12,17),next_trade_date=date(2026,12,18),
                v14_action='ENTER_SHORT_PUT',v14_short_put_contract='MO2701-P-7000',
                v14_short_put_expiry=date(2027,1,15))
    with pytest.raises(RuntimeError,match='2027'):
        bot.require_signal_calendar('IM',signal)


def test_current_signal_with_unused_far_month_diagnostic_remains_valid():
    signal=dict(market_date=date(2026,10,21),next_trade_date=date(2026,10,22),
                core_current='IC2612',core_target='IC2612',next_core='IC2703')
    bot.require_signal_calendar('IC',signal)
