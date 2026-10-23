"""Calendar liveness/commit-boundary tests; future observations are synthetic.

The daily matrix exercises selectors, not a continuous future ledger replay.
Public-builder scenarios use restored synthetic inputs and no production writes.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import test_icim_batch3_old_call_20261021 as legacy


def assert_diagnostic_only(signal):
    assert signal['diagnostic_only'] is True
    assert signal['signal_complete'] is False
    assert signal['persistence_eligible'] is False
    assert signal['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert signal['diagnostic_future']['formal_target_available'] is False
    forbidden = {'core_target', 'core_eod_contract', 'put_target_contract',
                 'core_put_target_contract', 'momentum_put_target_contract',
                 'put_target_total_qty', 'v14_short_put_contract',
                 'v14_ordinary_put_pending', 'v14_ordinary_put_open_plan',
                 'v14_short_put_open_pending', 'v14_profit_pending'}
    assert not forbidden.intersection(signal)


@pytest.mark.parametrize('month',['2701','2703'])
def test_public_unknown_held_im_expiry_keeps_quotes_without_targets(monkeypatch,month):
    legacy.wire(monkeypatch, held=False)
    contract='MO'+month+'-P-7200'
    bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_put_contract'] = contract
    frame = bot.fetch_cffex_quotes('MO')
    frame.loc[frame.instrument.eq('MO2612-P-7200'), 'instrument'] = contract
    frame.attrs['listed_instruments'] = list(frame.instrument)
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    def forbidden(*args, **kwargs):
        raise AssertionError('Unverified dates must stop before route/plans are generated')
    monkeypatch.setattr(bot, '_apply_v14_live_policy', forbidden)
    monkeypatch.setattr(bot, '_v14_schedule_ordinary_put', forbidden)
    result = bot.build_live_trade_signal('IM', now=legacy.CLOCK, mode='close')
    assert_diagnostic_only(result)
    assert result['calendar_provisional'] is True
    assert any(r['date'].startswith('2027-') for r in result['calendar_diagnostics'])
    assert result['diagnostic_future']['quote_status'] == 'signal_date_matched'
    assert {r['contract'] for r in result['diagnostic_future']['quotes']} == {'IM2611','IM2612','IM2703','IM2706'}
    assert result['mode'] == 'close'
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def test_public_unknown_positive_momentum_held_expiry_is_diagnostic_not_target(monkeypatch):
    legacy.wire(monkeypatch,held=False)
    contract='MO2703-P-7000'
    bot.LIVE_CONTINUATION_ANCHOR['IM'].update(
        post_momentum_put_contract=contract,verified_momentum_put_qty_normalized=.5)
    frame=bot.fetch_cffex_quotes('MO')
    frame.loc[len(frame)]=dict(instrument=contract,lastprice=100.,bprice=99.,sprice=101.,
                              volume=10.,position=20.)
    frame.attrs['listed_instruments']=list(frame.instrument)
    original=bot.live_proxy
    def positive_momentum(*args):
        live=original(*args)
        live.update(momentum_next_weight=.5,momentum_current_weight=.5)
        return live
    monkeypatch.setattr(bot,'live_proxy',positive_momentum)
    before=deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    result=bot.build_live_trade_signal('IM',legacy.CLOCK,'close')
    assert_diagnostic_only(result)
    assert result['calendar_provisional'] is True
    assert any(row['purpose']=='held IM momentum Put maintenance' for row in result['calendar_diagnostics'])
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def test_public_im_from_zero_selects_cross_year_only_as_diagnostic(monkeypatch):
    legacy.wire(monkeypatch, held=False)
    bot.LIVE_CONTINUATION_ANCHOR['IM']['verified_core_put_qty_normalized'] = 0.
    frame = bot.fetch_cffex_quotes('MO')
    frame.loc[frame.instrument.eq('MO2612-P-7200'), 'instrument'] = 'MO2701-P-7200'
    frame.attrs['listed_instruments'] = list(frame.instrument)
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    result = bot.build_live_trade_signal('IM', now=legacy.CLOCK, mode='close')
    assert_diagnostic_only(result)
    assert any(r['purpose'] == 'selected IM Put expiry' for r in result['calendar_diagnostics'])
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def test_direct_selector_outside_diagnostic_context_stays_strict():
    rows = pd.DataFrame([dict(instrument='MO2701-P-7200',lastprice=100.,volume=10.,position=10.)])
    with pytest.raises(bot.CalendarCoverageError, match='2027'):
        bot.select_im_put_for_reset(rows, date(2026,10,21), 8000.)


def test_execution_date_never_uses_the_model_expiry_fallback(monkeypatch):
    def needs_next_day(*args):
        bot._roll_forward_exchange_day(date(2027,1,1), require_official=True)
        raise AssertionError('Scheduling must remain strict')
    monkeypatch.setattr(bot, '_build_live_trade_signal', needs_next_day)
    result = bot.build_live_trade_signal('IM', datetime(2026,12,31,16,tzinfo=bot.BEIJING),'close')
    assert_diagnostic_only(result)
    assert result['diagnostic_status'] == 'calendar_unverified'
    assert '2027' in result['diagnostic_reason']
    assert bot._SIGNAL_CALENDAR_DIAGNOSTICS.get() is None


def test_caught_policy_failure_does_not_leave_any_new_plan(monkeypatch):
    def failed_policy(*args):
        return dict(product='IM',market_date=date(2026,10,21),close_confirmed=True,
            v14_action='HOLD_FAIL_CLOSED',core_target='IM2612',
            v14_ordinary_put_pending={'execution_day':'2026-10-22'},
            v14_short_put_open_pending={'new_contract':'MO2611-P-7000'},
            v14_profit_pending=True)
    monkeypatch.setattr(bot, '_build_live_trade_signal', failed_policy)
    result = bot.build_live_trade_signal('IM', legacy.CLOCK, 'close')
    assert_diagnostic_only(result)
    assert result['calendar_provisional'] is False
    assert result['diagnostic_status'] == 'put_fail_closed'


def test_model_warning_does_not_taint_known_expiry_normal_delivery(monkeypatch):
    legacy.wire(monkeypatch, held=False)
    result = bot.build_live_trade_signal('IM', legacy.CLOCK, 'close')
    assert result.get('diagnostic_only') is not True
    assert result['calendar_provisional'] is False
    assert result['signal_complete'] is True
    assert result['persistence_eligible'] is True
    assert result['core_target'] == 'IM2612'
    assert '2027' in result['calendar_coverage_warning']


@pytest.mark.parametrize('day, warning', [(date(2026,9,1),False),(date(2026,10,3),True),
                                         (date(2026,10,21),True),(date(2026,12,31),True)])
def test_year_end_warning_is_early_without_authorizing_dates(day, warning):
    note = bot._calendar_coverage_warning(day)
    assert bool(note) is warning
    if warning:
        assert '2027' in note and '须核验' in note


def test_diagnostic_payload_whitelists_observations_not_formal_fields():
    result = bot.make_diagnostic_signal('IC',legacy.CLOCK,'quantity unresolved',status='put_quantity_unresolved',
        source_signal=dict(mode='close',close_confirmed=True,put_target_contract='510500P2612M07000',
            core_target='IC2703',v14_ordinary_put_pending={'legs':[]},index_price=7000.,
            put_sizing_iv=.3,put_sizing_expiry=date(2027,3,24)))
    assert_diagnostic_only(result)
    assert result['calendar_provisional'] is False
    assert result['diagnostic_model']['put_sizing_iv'] == .3


def test_ic_real_selector_can_calculate_estimated_march_only_in_diagnostic_context(monkeypatch):
    day=date(2026,11,9)
    clock=datetime(2026,11,9,16,tzinfo=bot.BEIJING)
    monkeypatch.setattr(bot,'fetch_sse_510500_expiries',lambda:['202611','202612','202703','202706'])
    def chain(month, requested_day):
        assert month == '2703' and requested_day == day
        rows=pd.DataFrame([dict(contract='510500P2703M07000',security_id='10012099',
            last=.2,strike=7.,source_stamp=clock.replace(hour=15))])
        return rows,dict(date='20261109',time='150000',source='synthetic_march_quote')
    monkeypatch.setattr(bot,'_fetch_510500_chain_for_day',chain)
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *a:.02)
    context={'expiries':[],'failures':[],'signal':{},'future':{}}
    token=bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        with bot.runtime_clock(clock):
            selected=bot.select_ic_put_for_reset(day,7.,8000.,.25)
        assert selected['contract'] == '510500P2703M07000'
        assert selected['iv'] is not None and selected['qty'] > 0
        assert context['expiries'][0]['date'] == '2027-03-24'
        result=bot.make_diagnostic_signal('IC',clock,'estimated expiry',source_signal={
            'put_sizing_iv':selected['iv'],'put_sizing_expiry':selected['expiry'],
            'put_target_contract':selected['contract'],'put_target_total_qty':selected['qty']})
        assert_diagnostic_only(result)
        assert result['calendar_provisional'] is True
        assert result['diagnostic_model']['put_sizing_iv'] == selected['iv']
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)


@pytest.mark.parametrize('day,previous',[(date(2026,12,21),date(2026,12,18)),
                                       (date(2026,12,31),date(2026,12,30))])
def test_public_december_builder_returns_quotes_with_strict_schedule_block(monkeypatch,day,previous):
    monkeypatch.setattr(legacy,'DAY',day)
    monkeypatch.setattr(legacy,'PREVIOUS',previous)
    clock=datetime.combine(day,datetime.min.time()).replace(hour=16,tzinfo=bot.BEIJING)
    legacy.wire(monkeypatch,held=False)
    bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_contract']='IM2703'
    futures=bot.fetch_cffex_quotes('IM')
    futures.loc[futures.instrument.eq('IM2611'),'instrument']='IM2701'
    futures.loc[futures.instrument.eq('IM2612'),'instrument']='IM2702'
    futures.attrs['listed_instruments']=list(futures.instrument)
    before=deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    result=bot.build_live_trade_signal('IM',clock,'close')
    assert_diagnostic_only(result)
    assert result['calendar_provisional'] is True
    assert result['diagnostic_future']['quote_date'] == day.isoformat()
    assert {row['contract'] for row in result['diagnostic_future']['quotes']} == {'IM2701','IM2702','IM2703','IM2706'}
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def month_after(year,month,offset):
    index=year*12+month-1+offset
    return index//12,index%12+1


def synthetic_mo(day):
    year,month=day.year,day.month
    if bot._third_friday(year,month)<day:
        year,month=month_after(year,month,1)
    months=[month_after(year,month,i) for i in range(3)]
    cursor=months[-1]
    for _ in range(3):
        cursor=month_after(*cursor,1)
        while cursor[1] not in (3,6,9,12): cursor=month_after(*cursor,1)
        months.append(cursor)
    return pd.DataFrame([dict(instrument=f'MO{year%100:02d}{month:02d}-P-7200',
                             lastprice=100.,volume=10.,position=10.) for year,month in months])


SCAN_DAYS=[stamp.date() for stamp in pd.date_range('2026-10-15','2026-12-31')
           if bot._is_exchange_trading_day(stamp.date())]


@pytest.mark.parametrize('day',SCAN_DAYS,ids=str)
def test_synthetic_daily_selector_matrix_marks_only_consumed_unknown_expiries(day):
    context={'expiries':[],'failures':[],'signal':{},'future':{}}
    token=bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        selected=bot.select_im_put_for_reset(synthetic_mo(day),day,8000.)
        expiry=bot._third_friday(*bot._contract_month(str(selected['instrument'])))
        assert bool(context['expiries']) is (expiry.year not in bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS)
        if context['expiries']:
            result=bot.make_diagnostic_signal('IM',datetime.combine(day,datetime.min.time()).replace(hour=16,tzinfo=bot.BEIJING),'synthetic scan')
            assert_diagnostic_only(result)
            assert all(r['officially_verified'] is False for r in result['calendar_diagnostics'])
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
