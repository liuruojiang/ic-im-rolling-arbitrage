"""Independent E/F adversaries; synthetic rows, actual caches/readers/threads.

No external data is fetched and no formal runtime path is opened. These tests
exercise fresh/stale consumed rows rather than dropping stale chain members.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import contextvars
import threading

import pandas as pd
import pytest

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 14, 58, tzinfo=bot.BEIJING)
SINA = '新浪财经期权详报价（上交所回退）'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('clock audit attempted HTTP'))


def chain(month='2612', observed=CLOCK, *, include_stamp=True):
    row = {'contract': f'510500P{month}M06500', 'security_id': '10012149',
           'last': .25, 'strike': 6.5, 'pre_settle': .24, 'chg_rate': 0.}
    if include_stamp:
        row['source_stamp'] = observed
    frame = pd.DataFrame([row])
    return frame, {'date': DAY.strftime('%Y%m%d'), 'time': CLOCK.strftime('%H%M%S'), 'source': SINA}


def short_signal():
    return {'market_date': DAY, 'next_trade_date': date(2026,10,23),
            'etf_price': 6.8, 'future_last': 6500.}


def pricing(monkeypatch):
    # These constants isolate acceptance of market evidence, not financial accuracy.
    monkeypatch.setattr(bot, '_implied_volatility', lambda *a, **k: .25)
    monkeypatch.setattr(bot, '_bs_price_delta', lambda *a, **k: (.25, -.2))
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *a, **k: .02)


def test_confirmed_sina_candidate_cannot_omit_consumed_row_timestamp_in_capture(monkeypatch, tmp_path):
    """Wrong/old producer, not a claim the current Sina wire parser drops this column."""
    pricing(monkeypatch)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
                        lambda month: chain('2611', include_stamp=False))
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {}):
        with pytest.raises((RuntimeError, evidence.EvidenceUnavailable), match='时间|timestamp|stamp'):
            bot._v14_ic_short_put_candidate(short_signal())


def test_confirmed_sina_candidate_cannot_omit_consumed_row_timestamp_in_typed_replay(monkeypatch, tmp_path):
    """A hash-valid old producer bundle must still meet today's semantic gate."""
    pricing(monkeypatch)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('ic.chain', {'month':'2611','day':DAY}, lambda: chain('2611', include_stamp=False))
    reader = evidence.load_session(tmp_path, DAY)
    with bot.runtime_clock(CLOCK), bot.historical_replay(DAY), evidence.replay_session(reader):
        with pytest.raises((RuntimeError, evidence.EvidenceUnavailable), match='时间|timestamp|stamp'):
            bot._v14_ic_short_put_candidate(short_signal())


def test_sse_candidate_without_row_timestamp_keeps_aggregate_source_contract(monkeypatch):
    pricing(monkeypatch)
    def sse(month):
        frame, stamp = chain('2611', include_stamp=False)
        stamp['source'] = '上交所行情接口'
        return frame, stamp
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', sse)
    with bot.runtime_clock(CLOCK):
        result = bot._v14_ic_short_put_candidate(short_signal())
    assert result['tradable'] and result['security_id'] == '10012149'


def test_known_stale_unconsumed_row_does_not_invalidate_entire_chain(monkeypatch, tmp_path):
    pricing(monkeypatch)
    frame, stamp = chain('2611')
    stale = deepcopy(frame.iloc[0].to_dict())
    stale.update(contract='510500P2611M07000', security_id='10012150', strike=7.,
                 source_stamp=CLOCK-timedelta(hours=3))
    frame = pd.concat([frame,pd.DataFrame([stale])],ignore_index=True)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month:(frame,stamp))
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {}):
        result = bot._v14_ic_short_put_candidate(short_signal())
    assert result['tradable'] and result['contract']=='510500P2611M06500'


def test_stale_nearest_row_is_not_replaced_by_other_fresh_strike(monkeypatch):
    pricing(monkeypatch)
    frame, stamp = chain('2611', CLOCK-timedelta(hours=3))
    farther = deepcopy(frame.iloc[0].to_dict())
    farther.update(contract='510500P2611M07000', security_id='10012150',strike=7.,source_stamp=CLOCK)
    frame = pd.concat([frame,pd.DataFrame([farther])],ignore_index=True)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month:(frame,stamp))
    with bot.runtime_clock(CLOCK), pytest.raises(RuntimeError,match='20分钟'):
        bot._v14_ic_short_put_candidate(short_signal())


def test_cached_raw_chain_rechecks_source_date_and_does_not_expose_mutable_state(monkeypatch):
    calls=[]
    frame,stamp=chain()
    stamp['source']='上交所行情接口'
    frame.attrs['nested']={'ids':['original']}
    monkeypatch.setattr(bot,'fetch_510500_chain_with_failover',lambda month:(calls.append(month) or frame,stamp))
    token=bot._IC_QUOTE_QUERY_CACHE.set({})
    try:
        with bot.runtime_clock(CLOCK):
            first=bot._fetch_510500_chain_for_day('2612',DAY)
            first[0].attrs['nested']['ids'].append('caller mutation')
            first[1]['date']='20261021'
            second=bot._fetch_510500_chain_for_day('202612',DAY)
            assert second[1]['date']=='20261022'
            assert second[0].attrs['nested']['ids']==['original']
            cache=bot._IC_QUOTE_QUERY_CACHE.get()
            next(iter(cache.values()))[1]['date']='20261021'
            with pytest.raises(RuntimeError,match='过期|不一致'):
                bot._fetch_510500_chain_for_day('2612',DAY)
    finally:
        bot._IC_QUOTE_QUERY_CACHE.reset(token)
    assert calls==['2612']


def test_collection_elapsed_is_applied_once_even_through_adapter_and_validator(monkeypatch, tmp_path):
    clock=CLOCK.replace(hour=10,minute=0)
    tick=[100.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:tick[0])
    frame,stamp=chain(observed=clock)
    stamp.update(time='100017',source='上交所行情接口')
    with bot.runtime_clock(clock),bot.collection_clock(clock),evidence.capture_session(tmp_path,DAY,clock,{}):
        tick[0]=117.
        observed=bot._iv_observation_clock(clock)
        assert observed==clock+timedelta(seconds=17)
        # _validate_value advances once and _validate_chain_stamp_matches calls
        # the helper again; anchor-based semantics must not add another 17 sec.
        value=adapter._validate_value(bot,'ic.chain',{'month':'202612','day':DAY},(frame,stamp))
        assert value[1]['time']=='100017'
        assert bot._iv_observation_clock(observed)==observed


@pytest.mark.parametrize('offset,accepted',[(19*60+59,True),(20*60,True),(20*60+1,False)])
def test_selected_row_active_minutes_have_exact_twenty_minute_boundary(offset,accepted):
    clock=CLOCK.replace(hour=10,minute=30,second=0)
    row=chain(observed=clock-timedelta(seconds=offset))[0].iloc[0]
    with bot.runtime_clock(clock):
        if accepted:
            bot._require_selected_510500_quote_time(row,clock,DAY)
        else:
            with pytest.raises(RuntimeError,match='20分钟'):
                bot._require_selected_510500_quote_time(row,clock,DAY)


def test_lunch_break_does_not_age_quote_as_traded_minutes():
    clock=CLOCK.replace(hour=13,minute=10)
    row=chain(observed=CLOCK.replace(hour=11,minute=20))[0].iloc[0]
    with bot.runtime_clock(clock):
        bot._require_selected_510500_quote_time(row,clock,DAY)
        row['source_stamp']=row['source_stamp']-timedelta(seconds=1)
        with pytest.raises(RuntimeError,match='20分钟'):
            bot._require_selected_510500_quote_time(row,clock,DAY)


def test_observation_clock_worker_context_advances_shared_anchor_without_changing_signal_clock(monkeypatch):
    tick=[0.]
    clock=CLOCK.replace(hour=10,minute=0)
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:tick[0])
    with bot.runtime_clock(clock),bot.collection_clock(clock):
        copied=contextvars.copy_context()
        tick[0]=30.
        with ThreadPoolExecutor(max_workers=1) as pool:
            strategy_clock,observed=pool.submit(copied.run,lambda:(bot._now_beijing(),bot._iv_observation_clock(bot._now_beijing()))).result()
        assert strategy_clock==clock
        assert observed==clock+timedelta(seconds=30)
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None


def test_replay_clock_is_immutable_while_collection_anchor_advances(monkeypatch):
    tick=[0.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:tick[0])
    with bot.collection_clock(CLOCK),bot.historical_replay(DAY):
        tick[0]=1000.
        assert bot._iv_observation_clock(CLOCK)==CLOCK
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None


def test_nested_collection_scope_restores_outer_fear_and_anchor_after_exception(monkeypatch):
    tick=[0.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:tick[0])
    fear=bot.fear_grid
    with fear.observation_batch():
        outer=fear._OBSERVATION_BATCH.get()
        outer[('baifenwei',DAY,'close')]={'fear_greed_index':30.,'source':'synthetic outer evidence'}
        with bot.collection_clock(CLOCK):
            anchor=bot._COLLECTION_CLOCK_ANCHOR.get()
            tick[0]=3.
            with pytest.raises(RuntimeError,match='synthetic inner failure'):
                with bot.collection_clock(CLOCK+timedelta(hours=1)),fear.observation_batch():
                    assert bot._COLLECTION_CLOCK_ANCHOR.get() is anchor
                    assert fear._OBSERVATION_BATCH.get() is outer
                    raise RuntimeError('synthetic inner failure')
            assert bot._iv_observation_clock(CLOCK)==CLOCK+timedelta(seconds=3)
            assert fear.observed_in_batch('baifenwei',DAY,'close')['fear_greed_index']==30.
        assert fear._OBSERVATION_BATCH.get() is outer
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None
    assert fear._OBSERVATION_BATCH.get() is None


@pytest.mark.parametrize('raw_time',['','000000','091459','145801','235959'])
def test_chain_stamp_invalid_or_future_time_cannot_hide_behind_current_market_date(raw_time):
    with bot.runtime_clock(CLOCK),pytest.raises(RuntimeError):
        bot._validate_chain_stamp_matches('synthetic',{'date':'20261022','time':raw_time},DAY,CLOCK)
