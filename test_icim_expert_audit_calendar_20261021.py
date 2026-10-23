"""Independent calendar red team; future market rows are synthetic scenarios."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import threading

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as legacy
import test_icim_round2_audit_lifecycle_20261021 as ic_seed
import test_ic_im_external_review_round2_20261021 as identity
from test_icim_b3adv_state_20261021 import prepare
from test_icim_round2_audit_lifecycle_20261021 import snapshot


def no_target(signal):
    assert signal['diagnostic_only'] is True
    assert signal['signal_complete'] is signal['persistence_eligible'] is False
    assert signal['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert signal['diagnostic_future']['formal_target_available'] is False
    assert not {'core_target', 'put_target_contract', 'core_put_target_contract',
                'momentum_put_target_contract', 'v14_ordinary_put_pending',
                'v14_short_put_open_pending', 'v14_profit_pending'}.intersection(signal)


@pytest.fixture(autouse=True)
def isolated_network(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('network not authorized'))


def at_day(monkeypatch, day):
    previous = bot._roll_backward_exchange_day(day-timedelta(days=1))
    clock = datetime.combine(day, datetime.min.time()).replace(hour=15, minute=40, tzinfo=bot.BEIJING)
    monkeypatch.setattr(legacy, 'DAY', day)
    monkeypatch.setattr(legacy, 'PREVIOUS', previous)
    monkeypatch.setattr(legacy, 'CLOCK', clock)
    legacy.wire(monkeypatch, held=False)
    if day > date(2026, 12, 17):
        bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_contract'] = 'IM2703'
    frame = bot.fetch_cffex_quotes('IM')
    if day > date(2026, 12, 18):
        frame['instrument'] = ['IM2701', 'IM2702', 'IM2703', 'IM2706']
        frame.attrs['listed_instruments'] = frame.instrument.tolist()
    return clock


@pytest.mark.parametrize('day,diagnostic', [
    (date(2026,12,15),False),
    (date(2026,12,16),True),
    (date(2026,12,17),True),
    (date(2026,12,21),True),
    (date(2026,12,31),True),
])
def test_actual_builder_causal_december_states_have_no_false_formal_targets(monkeypatch, day, diagnostic):
    clock = at_day(monkeypatch, day)
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    out = bot.build_live_trade_signal('IM', clock, 'close')
    if diagnostic:
        no_target(out)
        assert out['calendar_provisional'] is True
        assert out['diagnostic_future']['quotes']
        assert out['diagnostic_future']['quote_date'][:10] == str(day)
    else:
        assert not out.get('diagnostic_only')
        assert out['core_target'] == 'IM2612'
        assert out['calendar_provisional'] is False
    assert bot.LIVE_CONTINUATION_ANCHOR == before
    assert bot._SIGNAL_CALENDAR_DIAGNOSTICS.get() is None


def test_thread_with_active_model_diagnostic_does_not_relax_another_threads_selector(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    rows = pd.DataFrame([dict(instrument='MO2701-P-7200',lastprice=100.,volume=10.,position=20.)])
    def build(*a):
        bot.select_im_put_for_reset(rows, date(2026,10,21), 8000.)
        entered.set()
        assert release.wait(5)
        return {'product':'IM','market_date':date(2026,10,21)}
    monkeypatch.setattr(bot, '_build_live_trade_signal', build)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(bot.build_live_trade_signal, 'IM', legacy.CLOCK, 'close')
        try:
            assert entered.wait(5)
            with pytest.raises(bot.CalendarCoverageError):
                bot.select_im_put_for_reset(rows,date(2026,10,21),8000.)
            assert bot._SIGNAL_CALENDAR_DIAGNOSTICS.get() is None
        finally:
            release.set()
        no_target(future.result(timeout=5))


@pytest.mark.parametrize('day', [date(2026,12,14), date(2026,12,15)])
def test_actual_ic_quarter_roll_preview_and_close_remain_diagnostic(tmp_path,monkeypatch,day):
    previous=bot._roll_backward_exchange_day(day-timedelta(days=1))
    clock=datetime.combine(day,datetime.min.time()).replace(hour=15,minute=40,tzinfo=bot.BEIJING)
    monkeypatch.setattr(ic_seed,'DAY',day)
    monkeypatch.setattr(ic_seed,'PREVIOUS',previous)
    monkeypatch.setattr(ic_seed,'CLOCK',clock)
    store,current=ic_seed.recent_store(tmp_path/'ledger')
    anchors=state.anchors_from_record(current)
    chain,stamp=identity.wire(monkeypatch)
    chain['source_stamp']=clock.replace(hour=15,minute=0)
    stamp=dict(stamp,date=day.strftime('%Y%m%d'),time='150000',source='synthetic_date_shift_of_saved_identity')
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',anchors)
    ic_seed.builder_inputs(monkeypatch,anchors['IC'],chain,stamp)
    monkeypatch.setattr(bot,'fetch_sse_510500_quote',lambda:dict(date=day.isoformat(),time='150000',last=7.8))
    before,disk=deepcopy(anchors),snapshot(store.root)
    out=bot.build_live_trade_signal('IC',clock,'close')
    no_target(out)
    assert out['calendar_provisional'] is True
    assert any('2027' in row['date'] for row in out['calendar_diagnostics'])
    assert bot.LIVE_CONTINUATION_ANCHOR == before
    assert snapshot(store.root) == disk


def test_actual_policy_failure_reason_is_kept_in_diagnostic_and_no_plan_is_scheduled(monkeypatch):
    legacy.wire(monkeypatch,held=False)
    def fails(product,signal,**kwargs):
        return dict(signal,v14_action='HOLD_FAIL_CLOSED',v14_action_reason='specific-original-market-failure')
    monkeypatch.setattr(bot,'_apply_v14_live_policy',fails)
    monkeypatch.setattr(bot,'_v14_schedule_ordinary_put',lambda *a:pytest.fail('failed policy must not schedule'))
    out=bot.build_live_trade_signal('IM',legacy.CLOCK,'close')
    no_target(out)
    assert 'specific-original-market-failure' in out['diagnostic_reason']


def test_unexpected_failure_restores_callers_context_and_original_anchor(monkeypatch):
    original_anchor = bot.LIVE_CONTINUATION_ANCHOR['IM']
    parent = {'expiries':[], 'failures':[], 'signal':{}, 'future':{}}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(parent)
    def fails(*a):
        bot._require_model_expiry_calendar(date(2027,1,15),'synthetic uncertain expiry')
        bot.LIVE_CONTINUATION_ANCHOR['IM'] = {'unexpected_temporary_projection':True}
        raise ValueError('synthetic downstream failure')
    monkeypatch.setattr(bot, '_build_live_trade_signal', fails)
    try:
        with pytest.raises(ValueError,match='downstream failure'):
            bot.build_live_trade_signal('IM',legacy.CLOCK,'close')
        assert bot._SIGNAL_CALENDAR_DIAGNOSTICS.get() is parent
        assert parent['expiries'] == []
        assert bot.LIVE_CONTINUATION_ANCHOR['IM'] is original_anchor
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)


@pytest.mark.parametrize('target,contract', [
    ('core_target','IM2703'),
    ('core_put_target_contract','MO2701-P-7200'),
    ('put_target_contract','MO2701-P-7200'),
    ('momentum_put_target_contract','MO2701-P-7200'),
])
def test_stripping_new_flags_cannot_bypass_existing_adopted_target_calendar_gate(tmp_path,monkeypatch,target,contract):
    store,current,signals = prepare(tmp_path,monkeypatch)
    signal = signals['IM']
    for key in ('calendar_provisional','diagnostic_only','signal_complete','persistence_eligible'):
        signal.pop(key,None)
    signal[target] = contract
    if target == 'momentum_put_target_contract':
        signal['momentum_put_target_qty_normalized'] = 1.5
    before = snapshot(store.root)
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(current,signals)
    assert snapshot(store.root) == before


@pytest.mark.parametrize('field', ['calendar_provisional','diagnostic_only','signal_complete','persistence_eligible'])
@pytest.mark.parametrize('value', [None,0,1,'false',[],{}])
def test_direct_store_rejects_nonboolean_eligibility_even_with_valid_formal_targets(tmp_path,monkeypatch,field,value):
    store,current,signals = prepare(tmp_path,monkeypatch)
    signals['IM'][field] = value
    before = snapshot(store.root)
    with pytest.raises(RuntimeError,match='禁止持久化'):
        store.append_confirmed_signals(current,signals)
    assert snapshot(store.root) == before


def test_public_diagnostic_quotes_match_display_and_json(monkeypatch):
    legacy.wire(monkeypatch,held=False)
    bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_put_contract'] = 'MO2701-P-7200'
    mo = bot.fetch_cffex_quotes('MO')
    mo.loc[mo.instrument.eq('MO2612-P-7200'),'instrument'] = 'MO2701-P-7200'
    mo.attrs['listed_instruments'] = mo.instrument.tolist()
    signal = bot.build_live_trade_signal('IM',legacy.CLOCK,'close')
    no_target(signal)
    text,files=[],[]
    class Output:
        def write(self,value):text.append(str(value))
        def attach_file(self,**value):files.append(value)
    bot._write_diagnostic_signal(Output(),'IM',signal)
    rendered=''.join(text)
    assert '8000.0' in rendered
    assert '：None' not in rendered
    exported=json.loads(files[0]['contents'])
    assert exported['diagnostic_future']['quotes'] == json.loads(json.dumps(signal['diagnostic_future']['quotes']))
    assert '下一交易日确认目标' not in rendered
    no_target(exported)


def test_zero_exposure_residual_unknown_year_identity_is_not_a_consumed_target(monkeypatch):
    legacy.wire(monkeypatch,held=False)
    record=legacy.record(held=False)
    anchor=record['products']['IM']
    anchor.update(post_put_contract='MO2701-P-7200',post_core_put_contract='MO2701-P-7200',
                  verified_core_put_qty_normalized=0.,verified_put_qty_normalized=0.,
                  verified_total_put_qty_normalized=0.,verified_parent_puts=0,
                  post_core_put_equivalent_units=0.,post_put_equivalent_units=0.)
    record['digest']=state._digest(record)
    state._validate_record(record)
    restored=state.anchors_from_record(record)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',restored)
    live=bot.live_proxy('IM',legacy.CLOCK)
    live.update(v13_parent_puts_per_full_core=0,v13_current_parent_puts=0,
                v13_put_target_qty_normalized=0.,momentum_120=.1,
                valuation_puts_per_full_core=0,mom120_floor_puts_per_full_core=0)
    monkeypatch.setattr(bot,'live_proxy',lambda *a:deepcopy(live))
    mo=bot.fetch_cffex_quotes('MO')
    mo.loc[mo.instrument.eq('MO2612-P-7200'),'instrument']='MO2701-P-7200'
    mo.attrs['listed_instruments']=mo.instrument.tolist()
    before=deepcopy(restored)
    out=bot.build_live_trade_signal('IM',legacy.CLOCK,'close')
    assert not out.get('diagnostic_only'),out.get('diagnostic_reason')
    assert out['core_put_target_qty_normalized'] == 0
    assert out['calendar_provisional'] is False
    assert bot.LIVE_CONTINUATION_ANCHOR == before
