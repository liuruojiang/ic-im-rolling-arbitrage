from copy import deepcopy
from contextlib import contextmanager
from datetime import date,datetime, timedelta
from unittest.mock import Mock, patch
import pandas as pd
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_v1_4_policy as policy
import poe_ic_im_v1_4_state as state


def test_ic_candidate_rejects_stale_chain():
    signal={'market_date':date(2026,9,18),'next_trade_date':date(2026,9,21)}
    with bot.runtime_clock(datetime(2026,9,18,16,tzinfo=bot.BEIJING)), patch.object(bot,'fetch_510500_chain_with_failover',return_value=(pd.DataFrame(),{'date':'20260901','time':'150000'})):
        with pytest.raises(RuntimeError):
            bot._v14_ic_short_put_candidate(signal)


def test_fix8_ic_monthly_preview_is_not_a_completed_reset():
    with patch.object(bot, '_is_pre_expiry_close', return_value=True):
        assert bot._v14_monthly_option_calendar('IC', date(2026, 9, 28), date(2026, 9, 29), True) == (True, False)
        assert bot._v14_monthly_option_calendar('IC', date(2026, 9, 29), date(2026, 9, 30), True) == (False, True)
        assert bot._v14_monthly_option_calendar('IC', date(2026, 9, 30), date(2026, 9, 30), True) == (True, False)


def test_monthly_calendar_uses_holiday_adjusted_actual_expiry():
    assert bot._third_friday(2026, 6) == date(2026, 6, 22)
    assert bot._is_pre_expiry_close(date(2026, 6, 18), date(2026, 6, 22), True)
    assert bot._v14_monthly_option_calendar('IC', date(2026, 6, 22), date(2026, 6, 22), True) == (True, False)


def test_fix8_im_ordinary_plan_needs_exact_next_day_open_and_closes_on_missing_price():
    anchor = policy.default_extension('IM')
    anchor.update(post_core_put_contract='MO2612-P-7500',
                  post_momentum_put_contract='MO2612-P-7300',
                  verified_core_put_qty_normalized=1.0,
                  verified_momentum_put_qty_normalized=.5)
    signal = {'market_date': date(2026, 9, 29), 'next_trade_date': date(2026, 9, 30),
              'close_confirmed': True, 'option_monthly_reset_due': False,
              'v14_route_state': 'future', 'v14_profit_pending': False,
              'v14_action': 'HOLD', 'core_put_target_contract': 'MO2612-P-7500',
              'core_put_target_qty_normalized': 1.0,
              'momentum_put_target_contract': 'MO2612-P-7200',
              'momentum_put_target_qty_normalized': .5,
              'v13_parent_puts_per_full_core': 2}
    bot._v14_schedule_ordinary_put('IM', signal, anchor, None)
    plan = signal['v14_ordinary_put_pending']
    assert signal['v14_ordinary_put_plan_status'] == 'scheduled_t_plus_1_open'
    assert plan['legs']['core']['changed'] is False
    assert plan['legs']['momentum']['changed'] is True
    anchor['v14_ordinary_put_pending'] = plan
    chain = pd.DataFrame([
        {'instrument': 'MO2612-P-7300', 'openprice': 20.0},
        {'instrument': 'MO2612-P-7200', 'openprice': 11.0},
    ])
    chain.attrs.update(source='中金所', source_date=date(2026, 9, 30))
    original = bot.LIVE_CONTINUATION_ANCHOR['IM']
    bot.LIVE_CONTINUATION_ANCHOR['IM'] = anchor
    try:
        quote_cache = {}
        with patch.object(bot, 'fetch_cffex_quotes', return_value=chain):
            evidence = bot._v14_ordinary_open_evidence('IM', date(2026, 9, 30), datetime(2026, 9, 30, 16), quote_cache)
        assert evidence['status'] == 'confirmed_open_research_price'
        assert quote_cache['MO'] is chain
        assert {x['contract'] for x in evidence['prices']} == {'MO2612-P-7300', 'MO2612-P-7200'}
        missing = chain.drop(columns='openprice')
        missing.attrs.update(chain.attrs)
        with patch.object(bot, 'fetch_cffex_quotes', return_value=missing):
            closed = bot._v14_ordinary_open_evidence('IM', date(2026, 9, 30), datetime(2026, 9, 30, 16))
        assert closed['status'] == 'closed_missing_open_price'
        assert 'openprice' in closed['reason']
    finally:
        bot.LIVE_CONTINUATION_ANCHOR['IM'] = original


def test_fix8_ic_momentum_open_uses_frozen_security_id_without_touching_core_basis():
    anchor = policy.default_extension('IC')
    anchor.update(v14_core_put_contract='510500P2612M07500',
                  v14_core_put_security_id='10012000', v14_core_put_qty=2,
                  v14_core_put_entry_premium=.20, v14_core_put_profit3x_eligible=True,
                  post_put_contract='510500P2612M07400', post_put_security_id='10012001',
                  post_put_qty=2, verified_core_put_qty=2,
                  verified_momentum_put_qty=0)
    signal = {'market_date': date(2026, 9, 29), 'next_trade_date': date(2026, 9, 30),
              'close_confirmed': True, 'option_monthly_reset_due': False,
              'v14_route_state': 'future', 'v14_profit_pending': False, 'v14_action': 'HOLD',
              'v14_core_put_contract': '510500P2612M07500',
              'v14_core_put_security_id': '10012000', 'put_target_core_qty': 2,
              'put_target_contract': '510500P2612M07300',
              'put_target_security_id': '10012002', 'put_target_momentum_qty': 3,
              'core_put_target_delta': .25, 'momentum_put_target_delta': .25,
              'core_put_driver': 'valuation', 'momentum_put_driver': 'momentum'}
    bot._v14_schedule_ordinary_put('IC', signal, anchor, None)
    plan = signal['v14_ordinary_put_pending']
    assert plan['legs']['core']['changed'] is False
    anchor['v14_ordinary_put_pending'] = plan
    original = bot.LIVE_CONTINUATION_ANCHOR['IC']
    bot.LIVE_CONTINUATION_ANCHOR['IC'] = anchor
    try:
        with patch.object(bot, 'fetch_option_dated_open', return_value=(.15, 'Sina:dated_open')) as fetch:
            evidence = bot._v14_ordinary_open_evidence('IC', date(2026, 9, 30), datetime(2026, 9, 30, 16))
        assert evidence['status'] == 'confirmed_open_research_price'
        fetch.assert_called_once_with('10012002', date(2026, 9, 30))
        projected = policy.project_ordinary_open('IC', anchor, plan)
        assert projected['v14_core_put_entry_premium'] == .20
        assert projected['post_put_security_id'] == '10012002'
        assert projected['verified_momentum_put_qty'] == 3
    finally:
        bot.LIVE_CONTINUATION_ANCHOR['IC'] = original


def test_fix8_ic_first_ordinary_core_open_uses_shared_target_identity():
    anchor = policy.default_extension('IC')
    anchor.update(post_put_contract='510500P2612M07500',
                  post_put_security_id='10012099', post_put_qty=0,
                  verified_core_put_qty=0, verified_momentum_put_qty=0)
    signal = {'market_date': date(2026, 9, 29), 'next_trade_date': date(2026, 9, 30),
              'close_confirmed': True, 'option_monthly_reset_due': False,
              'v14_route_state': 'future', 'v14_profit_pending': False, 'v14_action': 'HOLD',
              'v14_core_put_contract': None, 'v14_core_put_security_id': None,
              'put_target_core_qty': 10, 'put_target_contract': '510500P2612M07500',
              'put_target_security_id': '10012099', 'put_target_momentum_qty': 0,
              'core_put_target_delta': .25, 'momentum_put_target_delta': 0.0,
              'core_put_driver': 'MOM120负动量下限',
              'momentum_put_driver': '动量袖空仓，按规则Put归零'}

    bot._v14_schedule_ordinary_put('IC', signal, anchor, None)

    plan = signal['v14_ordinary_put_pending']
    assert plan['legs']['core']['new_contract'] == '510500P2612M07500'
    assert plan['legs']['core']['new_security_id'] == '10012099'
    assert plan['legs']['core']['new_qty'] == 10
    pending = dict(anchor, v14_ordinary_put_pending=plan)
    policy.validate_extension('IC', pending)
    state.validate_ordinary_put_plan('IC', anchor, signal, date(2026, 9, 29))


def test_ic_replay_does_not_fetch_current_chain():
    with bot.historical_replay(date(2026,9,18)), patch.object(bot,'fetch_510500_chain_with_failover',side_effect=AssertionError('must not fetch current')):
        assert bot._v14_ic_short_put_candidate({})['tradable'] is False


def test_ic_monthly_cost_uses_selected_new_contract():
    signal={'iv_monitor_option_price':.1,'v14_selected_core_entry_premium':.7,'option_monthly_reset_due':True,'close_confirmed':True,'put_target_core_qty':5}
    assert bot._v14_option_mark('IC',signal,None)==(.1,.7)
    signal['close_confirmed']=False
    assert bot._v14_option_mark('IC',signal,None)==(.1,None)


def test_ic_monthly_momentum_only_does_not_create_core_basis_or_identity():
    signal = {
        'iv_monitor_option_price': .1,
        'v14_selected_core_entry_premium': .7,
        'option_monthly_reset_due': True,
        'close_confirmed': True,
        'put_target_core_qty': 0,
        'put_target_contract': '510500P2612M07500',
        'put_target_security_id': '10012099',
    }
    bot._v14_ic_core_overlay(signal, {})
    assert bot._v14_option_mark('IC', signal, None) == (.1, None)
    assert signal['v14_core_put_contract'] is None
    assert signal['v14_core_put_security_id'] is None
    assert signal['v14_core_put_qty'] == 0


def test_profit_reentry_only_confirmed_execution_day_and_core_only():
    anchor={'v14_profit_pending':True,'v14_profit_execution_day':date(2026,9,21),'v14_route_state':'future'}
    signal={'market_date':date(2026,9,18),'close_confirmed':True,'put_reference_price':8000.,'momentum_put_target_contract':'MO2612-P-7000'}
    selected=pd.Series({'instrument':'MO2612-P-8200','lastprice':99.,'volume':1})
    with patch.object(bot,'select_im_put_for_reset',return_value=selected) as select:
        bot._v14_prepare_lifecycle_evidence('IM',signal,anchor,pd.DataFrame())
        select.assert_not_called()
        signal['market_date']=date(2026,9,21);signal['close_confirmed']=False
        bot._v14_prepare_lifecycle_evidence('IM',signal,anchor,pd.DataFrame())
        select.assert_not_called()
        signal['close_confirmed']=True
        bot._v14_prepare_lifecycle_evidence('IM',signal,anchor,pd.DataFrame())
        assert signal['v14_profit_reentry_entry_premium']==99.
        assert signal['core_put_target_contract']=='MO2612-P-8200'
        assert signal['momentum_put_target_contract']=='MO2612-P-7000'


def test_fix7_ic_t_close_preselection_and_t1_open_confirmation_are_separate():
    signal={'market_date':date(2026,9,28),'close_confirmed':True,
            'option_monthly_reset_due':False,'put_target_core_qty':5,
            'etf_price':8.,'future_last':8000.,'core_put_target_delta':.25,
            'v14_core_put_mark':.31}
    anchor={'v14_route_state':'future','v14_core_put_profit3x_eligible':True,
            'v14_core_put_entry_premium':.10}
    selected={'contract':'510500P2612M07000','security_id':'10012001','qty':5,
              'quote':{'last':.12},'stamp':{'date':'20260928','time':'150000'}}
    with patch.object(bot,'select_ic_put_for_reset',return_value=selected) as choose,\
         patch.object(bot,'_validate_chain_stamp_matches'):
        bot._v14_prepare_profit_open_plan('IC',signal,anchor,None)
    choose.assert_called_once_with(date(2026,9,28),8.,8000.,.25)
    assert signal['v14_profit_plan_contract']==selected['contract']
    assert signal['v14_profit_reentry_status']=='t_close_preselected_for_next_open'
    tomorrow={'market_date':date(2026,9,29),'close_confirmed':True,
              'option_monthly_reset_due':False,'put_target_core_qty':5,
              'put_target_momentum_qty':3}
    pending={'v14_route_state':'future','v14_profit_pending':True,
             'v14_profit_trigger_day':date(2026,9,28),
             'v14_profit_execution_day':date(2026,9,29),
             'v14_profit_old_contract':'510500P2612M07500',
             'v14_profit_old_security_id':'10012000',
             'v14_profit_reentry_contract':selected['contract'],
             'v14_profit_reentry_security_id':selected['security_id'],
             'v14_profit_reentry_qty':5}
    with patch.object(bot,'fetch_option_dated_open',side_effect=[(.30,'Sina:dated_open'),(.12,'Sina:dated_open')]) as fetch,\
         patch.object(bot,'select_ic_put_for_reset',side_effect=AssertionError('T+1 close reselection forbidden')):
        bot._v14_prepare_lifecycle_evidence('IC',tomorrow,pending,None)
    assert fetch.call_count==2
    assert tomorrow['v14_profit_reentry_entry_premium']==.12
    assert tomorrow['v14_profit_exit_open_price']==.30
    assert tomorrow['v14_profit_open_price_day']==date(2026,9,29)
    assert tomorrow['put_target_total_qty']==8


def test_fix7_im_open_missing_leg_fails_instead_of_falling_back_to_close():
    anchor={'v14_route_state':'future','v14_profit_pending':True,
            'v14_profit_trigger_day':date(2026,9,28),
            'v14_profit_execution_day':date(2026,9,29),
            'v14_profit_old_contract':'MO2612-P-7500',
            'v14_profit_reentry_contract':'MO2612-P-7000','v14_profit_reentry_qty':1.5}
    signal={'market_date':date(2026,9,29),'close_confirmed':True,
            'option_monthly_reset_due':False,'core_put_target_qty_normalized':1.5}
    chain=pd.DataFrame([{'instrument':'MO2612-P-7500','openprice':.0,'lastprice':30.},
                        {'instrument':'MO2612-P-7000','openprice':12.,'lastprice':11.}])
    with pytest.raises(RuntimeError,match='正开盘价'):
        bot._v14_prepare_lifecycle_evidence('IM',signal,anchor,chain)


def test_fix7_ic_open_source_never_uses_a_close_as_fallback():
    class Response:
        text='callback([{"d":"2026-09-29","o":"0","c":"0.30"}]);'
        def raise_for_status(self):
            pass
        content=b'ok'
    with patch.object(bot.requests,'get',return_value=Response()),\
         patch.object(bot,'_response_with_size_limit'),\
         patch.object(bot,'_request_json',return_value={'data':{'code':'10012000','market':10,
             'klines':['2026-09-29,0,0.30,0.31,0.29,10,100']}}):
        with pytest.raises(RuntimeError,match='开盘价不可核验'):
            bot.fetch_option_dated_open('10012000',date(2026,9,29))


def test_fix7_ic_open_source_accepts_identical_vendor_duplicate_only():
    class Response:
        text='callback([{"d":"2026-09-29","o":"0.12","c":"0.30"},'\
             '{"d":"2026-09-29","o":"0.12","c":"0.30"}]);'
        def raise_for_status(self):
            pass
        content=b'ok'
    with patch.object(bot.requests,'get',return_value=Response()),patch.object(bot,'_response_with_size_limit'):
        assert bot.fetch_option_dated_open('10012000',date(2026,9,29))==(.12,'Sina:dated_open')


def test_signal_scope_does_not_require_account_execution_evidence():
    signal={'market_date': date(2026, 9, 18)}
    anchor={'v14_route_state':'short_put','v14_short_put_expiry':date(2026, 9, 18)}
    bot._v14_prepare_lifecycle_evidence('IM',signal,anchor,None)
    assert signal['v14_lifecycle_evidence_status']=='waiting_model_settlement_reference'
    assert signal['v14_signal_scope']=='research_model_signal_only'
    assert signal['v14_account_execution_status']=='not_observed_out_of_scope'
    assert '账户成交、行权或交割回执' in signal['v14_lifecycle_evidence_note']
    assert 'v14_settlement_confirmed' not in signal


def test_active_short_put_is_not_reported_as_blocked_before_expiry():
    signal={'market_date': date(2026, 9, 17)}
    anchor={'v14_route_state':'short_put','v14_short_put_expiry':date(2026, 9, 18)}
    bot._v14_prepare_lifecycle_evidence('IC',signal,anchor,None)
    assert signal['v14_lifecycle_evidence_status']=='model_short_put_active'


def test_expiry_branches_survive_unavailable_new_candidate_data(monkeypatch):
    anchor=bot.v14_policy.default_extension('IM')
    anchor.update(v14_route_state='short_put',v14_cycle_id='cycle',
                  v14_short_put_contract='MO2609-P-7000',
                  v14_short_put_qty_normalized=1.5,
                  v14_short_put_entry_premium=100.,
                  v14_short_put_expiry=date(2026,9,18))
    signal={'market_date':date(2026,9,18),'next_trade_date':date(2026,9,21),
            'close_confirmed':True,'option_monthly_reset_due':False,
            'total_units_current':.5,'total_units_target':.5,
            'momentum_put_target_qty_normalized':0.,'momentum_put_action':'HOLD'}
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,'IM',anchor)
    result=bot._apply_v14_live_policy('IM',signal,mo_quotes=None)
    assert result['v14_action']=='PUBLISH_SHORT_PUT_EXPIRY_BRANCHES'
    assert result['v14_candidate_data_status'].startswith('unavailable:')


def test_ic_reentry_does_not_overwrite_shared_momentum_contract():
    signal={'market_date':date(2026,9,21),'close_confirmed':True,'put_target_contract':'old',
            'etf_price':8.,'future_last':8000.,'core_put_target_delta':.25,'put_target_momentum_qty':3}
    anchor={'v14_profit_pending':True,'v14_profit_execution_day':date(2026,9,21)}
    selected={'contract':'510500P2612M07500','security_id':'123','qty':5,'quote':{'last':.7},'stamp':{}}
    with patch.object(bot,'select_ic_put_for_reset',return_value=selected),patch.object(bot,'_validate_chain_stamp_matches'):
        bot._v14_prepare_lifecycle_evidence('IC',signal,anchor,None)
    assert signal['put_target_contract']=='old'
    assert signal['v14_profit_reentry_entry_premium']==.7
    assert signal['v14_core_put_contract']=='510500P2612M07500'
    assert signal['put_target_total_qty']==8

def test_ic_dedicated_core_next_day_mark_and_quantity():
    anchor={'v14_core_put_contract':'510500P2612M07500','v14_core_put_security_id':'123','v14_core_put_qty':5,'verified_core_put_delta':.25}
    signal={'market_date':date(2026,9,22),'iv_monitor_option_price':.1,'core_put_target_delta':.25,'put_target_momentum_qty':3,'put_target_contract':'old'}
    chain=pd.DataFrame([{'contract':'510500P2612M07500','last':.8}])
    with patch.object(bot,'fetch_510500_chain_with_failover',return_value=(chain,{})),patch.object(bot,'_validate_chain_stamp_matches'):
        bot._v14_ic_core_overlay(signal,anchor)
    assert signal['iv_monitor_option_price']==.8
    assert signal['put_target_core_qty']==5
    assert signal['put_target_total_qty']==8
    assert signal['put_target_contract']=='old'


def test_ic_dedicated_core_historical_query_uses_core_security_id():
    anchor={'v14_core_put_contract':'510500P2612M07500','v14_core_put_security_id':'123','v14_core_put_qty':5,'verified_core_put_delta':.25}
    signal={'market_date':date(2026,9,22),'core_put_target_delta':.25,'put_target_momentum_qty':3}
    chain=pd.DataFrame([{'contract':'510500P2612M07500','last':.8}])
    with bot.historical_replay(date(2026,9,22)),patch.object(bot,'fetch_sse_existing_put_historical_quote',return_value=(chain,{})) as fetch,patch.object(bot,'_validate_chain_stamp_matches'):
        bot._v14_ic_core_overlay(signal,anchor)
    fetch.assert_called_once_with('510500P2612M07500','123',date(2026,9,22))


def test_ic_core_target_zero_clears_identity_but_keeps_held_mark():
    anchor={'v14_core_put_contract':'510500P2612M07500','v14_core_put_security_id':'10012099',
            'v14_core_put_qty':14,'verified_core_put_delta':.25}
    signal={'market_date':date(2026,9,22),'core_put_target_delta':0.,
            'put_target_momentum_qty':0,'option_monthly_reset_due':False}
    chain=pd.DataFrame([{'contract':'510500P2612M07500','last':.08}])
    with bot.historical_replay(date(2026,9,22)),patch.object(bot,'fetch_sse_existing_put_historical_quote',return_value=(chain,{})),patch.object(bot,'_validate_chain_stamp_matches'):
        bot._v14_ic_core_overlay(signal,anchor)
    assert signal['iv_monitor_option_price']==.08
    assert signal['put_current_core_qty']==14
    assert signal['put_target_core_qty']==0
    assert signal['put_target_total_qty']==0
    assert signal['v14_core_put_qty']==0
    assert signal['v14_core_put_contract'] is None
    assert signal['v14_core_put_security_id'] is None


def test_ic_candidate_falls_back_to_sina_when_sse_times_out(monkeypatch):
    signal={'market_date':date(2026,9,18),'next_trade_date':date(2026,9,21),
            'etf_price':7.83,'future_last':7641.0}
    chain=pd.DataFrame([{'contract':'510500P2610M07500','last':.1237,'strike':7.5}])
    monkeypatch.setattr(bot, 'fetch_sse_510500_chain', lambda _month: (_ for _ in ()).throw(TimeoutError('SSE timeout')))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', lambda _month: (chain, {'date':'20260918','time':'150000','source':'新浪财经期权详报价（上交所回退）'}))
    monkeypatch.setattr(bot, 'fetch_sina_510500_security_id', lambda _contract: '10012357')
    # This is an offline historical fixture.  Bind the validation clock to the
    # fixture's market day so the test cannot drift when the CI calendar moves.
    with bot.runtime_clock(datetime(2026,9,18,16,tzinfo=bot.BEIJING)):
        candidate=bot._v14_ic_short_put_candidate(signal)
    assert candidate['tradable'] is True
    assert candidate['contract']=='510500P2610M07500'
    assert candidate['quote_source']=='新浪财经期权详报价（上交所回退）'


def test_ic_chain_failover_refuses_all_unavailable_sources(monkeypatch):
    monkeypatch.setattr(bot, 'fetch_sse_510500_chain', lambda _month: (_ for _ in ()).throw(TimeoutError('SSE timeout')))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', lambda _month: (_ for _ in ()).throw(TimeoutError('Sina timeout')))
    with pytest.raises(RuntimeError, match='所有来源均不可用'):
        bot.fetch_510500_chain_with_failover('2610')


def test_mom120_positive_but_debounce_active_is_labeled_as_pending_release():
    ic = bot.ic_targets(1.0, 0.0125, mom120_floor_active=True)
    im = bot.im_targets(1.0, 0.0125, mom120_floor_active=True)
    expected = 'MOM120防抖保护未解除（需连续两日均严格>+1%）'
    assert ic['put_driver'] == expected
    assert im['put_driver'] == expected


def test_date_range_does_not_expand_one_day_or_drop_last_month():
    assert bot.parse_date_range('2024-03-15') == (date(2024, 3, 15), date(2024, 3, 15))
    assert bot.parse_date_range('2024年到2025年3月') == (date(2024, 1, 1), date(2025, 3, 31))
    with pytest.raises(ValueError, match='起点晚于终点'):
        bot.parse_date_range('2025年到2024年3月')


def test_old_im_call_is_not_displayed_as_empty_during_no_new_call_period():
    text = bot._im_call_current_text('MO2610-C-10000', True, False)
    assert 'MO2610-C-10000' in text
    assert '规范化空1张' in text
    assert '规范化0张' not in text


def test_im_call_expiry_uses_signal_day_not_collection_day():
    expiry = date(2026, 9, 29)
    assert bot._im_call_finished('MO2609-C-9000', expiry, date(2026, 9, 28), True) is False
    assert bot._im_call_finished('MO2609-C-9000', expiry, expiry, False) is False
    assert bot._im_call_finished('MO2609-C-9000', expiry, expiry, True) is True


def test_runtime_anchor_install_rejects_schema_drift_atomically():
    anchors = state.anchors_from_record(state.bootstrap_record())
    anchors['IC']['last_verified_day'] += timedelta(days=1)
    del anchors['IM']['post_core_contract']
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    with pytest.raises(ValueError, match='IM运行时账本缺少post_core_contract'):
        bot.install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def test_runtime_anchor_install_normalizes_only_unambiguous_legacy_ic_split():
    anchors = state.anchors_from_record(state.bootstrap_record())
    assert 'verified_core_put_qty' not in anchors['IC']
    before = dict(bot.LIVE_CONTINUATION_ANCHOR)
    try:
        bot.install_runtime_anchors(anchors)
        ic = bot.LIVE_CONTINUATION_ANCHOR['IC']
        assert ic['verified_core_put_qty'] == 14
        assert ic['verified_momentum_put_qty'] == 0
        assert 'verified_core_put_qty' not in anchors['IC']
    finally:
        bot.LIVE_CONTINUATION_ANCHOR.update(before)


def test_ic_ordinary_plan_refuses_unidentified_held_core():
    anchor = policy.default_extension('IC')
    anchor.update(post_put_qty=2, verified_core_put_qty=2,
                  verified_momentum_put_qty=0, v14_core_put_qty=2)
    signal = {'market_date': date(2026, 9, 29), 'next_trade_date': date(2026, 9, 30),
              'close_confirmed': True, 'option_monthly_reset_due': False,
              'v14_route_state': 'future', 'v14_profit_pending': False, 'v14_action': 'HOLD'}
    with pytest.raises(RuntimeError, match='缺少独立合约身份'):
        bot._v14_schedule_ordinary_put('IC', signal, anchor, None)


def test_bootstrap_ic_cannot_create_a_post_effective_buy_only_plan():
    anchor = state.anchors_from_record(state.bootstrap_record())['IC']
    signal = {'market_date': date(2026, 9, 29), 'next_trade_date': date(2026, 9, 30),
              'close_confirmed': True, 'option_monthly_reset_due': False,
              'v14_route_state': 'future', 'v14_profit_pending': False, 'v14_action': 'HOLD'}
    with pytest.raises(RuntimeError, match='数量与独立持仓明细不一致'):
        bot._v14_schedule_ordinary_put('IC', signal, anchor, None)
    assert signal['v14_ordinary_put_pending'] is None


def test_lifecycle_evidence_error_becomes_hold_fail_closed():
    with (patch.object(bot, '_v14_prepare_lifecycle_evidence', side_effect=RuntimeError('missing openprice')),
          patch.object(policy, 'apply_policy', return_value={'v14_action': 'HOLD'}) as apply):
        result = bot._apply_v14_live_policy('IM', {'market_date': date(2026, 9, 29)}, mo_quotes=pd.DataFrame())
    assert result['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert 'missing openprice' in result['v14_action_reason']
    assert apply.call_count == 1


@pytest.mark.parametrize('vendor_open', ['omitted', None, 0])
def test_im_profit_open_missing_price_cannot_be_recorded_as_executed(vendor_open):
    anchor = policy.default_extension('IM')
    anchor.update(v14_profit_pending=True,
                  v14_profit_trigger_day=date(2026, 9, 28),
                  v14_profit_execution_day=date(2026, 9, 29),
                  v14_profit_old_contract='MO2612-P-7500', v14_profit_old_qty=1.5,
                  v14_profit_reentry_contract='MO2612-P-7000', v14_profit_reentry_qty=1.5,
                  v14_core_put_entry_premium=10.0, v14_core_put_profit3x_eligible=True)
    signal = {
        'product': 'IM', 'market_date': date(2026, 9, 29),
        'next_trade_date': date(2026, 9, 30), 'close_confirmed': True,
        'valuation_tier': 2, 'momentum_120': -0.1, 'momentum_next_weight': 1.0,
        'option_monthly_reset_due': False, 'core_units_current': 0.5,
        'core_units_target': 0.5, 'total_units_current': 1.0,
        'total_units_target': 1.0, 'core_put_target_qty_normalized': 1.5,
    }
    chain = pd.DataFrame([
        {'instrument': 'MO2612-P-7500', 'lastprice': 30.0},
        {'instrument': 'MO2612-P-7000', 'lastprice': 12.0},
    ])
    if vendor_open != 'omitted':
        chain['openprice'] = vendor_open
    if vendor_open == 0:
        with pytest.raises(RuntimeError, match='正开盘价'):
            bot._v14_prepare_lifecycle_evidence('IM', signal, anchor, chain)
        assert 'core_put_target_contract' not in signal
        assert 'put_action' not in signal
        assert 'v14_profit_open_executed_qty' not in signal
    original = bot.LIVE_CONTINUATION_ANCHOR['IM']
    bot.LIVE_CONTINUATION_ANCHOR['IM'] = anchor
    try:
        result = bot._apply_v14_live_policy('IM', signal, mo_quotes=chain)
    finally:
        bot.LIVE_CONTINUATION_ANCHOR['IM'] = original
    assert result['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert result['v14_profit_pending'] is True
    assert result.get('v14_profit_reentry_status') != 'confirmed_open_research_price'
    assert result.get('v14_profit_open_executed_qty') is None
    if vendor_open == 0:
        assert result.get('core_put_target_contract') != 'MO2612-P-7000'


def test_im_profit_open_on_eastmoney_needs_both_sina_open_checks():
    anchor = policy.default_extension('IM')
    anchor.update(v14_profit_pending=True,
                  v14_profit_trigger_day=date(2026, 9, 28),
                  v14_profit_execution_day=date(2026, 9, 29),
                  v14_profit_old_contract='MO2612-P-7500', v14_profit_old_qty=1.5,
                  v14_profit_reentry_contract='MO2612-P-7000', v14_profit_reentry_qty=1.5,
                  v14_core_put_entry_premium=10.0, v14_core_put_profit3x_eligible=True)
    signal = {
        'product': 'IM', 'market_date': date(2026, 9, 29),
        'next_trade_date': date(2026, 9, 30), 'close_confirmed': True,
        'valuation_tier': 2, 'momentum_120': -0.1, 'momentum_next_weight': 1.0,
        'option_monthly_reset_due': False, 'core_units_current': 0.5,
        'core_units_target': 0.5, 'total_units_current': 1.0,
        'total_units_target': 1.0, 'core_put_target_qty_normalized': 1.5,
    }
    chain = pd.DataFrame([
        {'instrument': 'MO2612-P-7500', 'lastprice': 30.0, 'openprice': 29.0},
        {'instrument': 'MO2612-P-7000', 'lastprice': 12.0, 'openprice': 11.0},
    ])
    chain.attrs['source'] = '东方财富'
    clock = datetime(2026, 9, 29, 16, tzinfo=bot.BEIJING)
    with (patch.object(bot, '_now_beijing', return_value=clock),
          patch.object(bot, 'verify_sina_option_open', return_value={'source': '新浪'}) as verify):
        confirmed = dict(signal)
        bot._v14_prepare_lifecycle_evidence('IM', confirmed, anchor, chain)
    assert confirmed['v14_profit_reentry_status'] == 'confirmed_open_research_price'
    assert '新浪' in confirmed['v14_profit_open_old_source']
    assert verify.call_count == 2
    original = bot.LIVE_CONTINUATION_ANCHOR['IM']
    bot.LIVE_CONTINUATION_ANCHOR['IM'] = anchor
    try:
        with patch.object(bot, 'verify_sina_option_open', side_effect=RuntimeError('Sina open conflict')):
            blocked = bot._apply_v14_live_policy('IM', dict(signal), mo_quotes=chain)
    finally:
        bot.LIVE_CONTINUATION_ANCHOR['IM'] = original
    assert blocked['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert blocked.get('v14_profit_open_executed_qty') is None


def test_eastmoney_intraday_fallback_requires_vendor_timestamp():
    stamp = datetime(2026, 9, 30, 11, 15, tzinfo=bot.BEIJING)
    with (patch.object(bot.requests, 'get', side_effect=bot.requests.RequestException('Tencent unavailable')),
          patch.object(bot, '_request_json', return_value={'data': {'f43': 785432, 'f86': int(stamp.timestamp())}})):
        quote = bot.fetch_live_price_quote('IC')
    assert quote['source_date'] == stamp.date()
    assert quote['source_time'] == '11:15:00'
    with (patch.object(bot.requests, 'get', side_effect=bot.requests.RequestException('Tencent unavailable')),
          patch.object(bot, '_request_json', return_value={'data': {'f43': 785432}})):
        with pytest.raises(RuntimeError, match='缺少有效供应商更新时间'):
            bot.fetch_live_price_quote('IC')


def test_eastmoney_mo_quote_preserves_vendor_open_when_present():
    stamp = datetime(2026, 9, 30, 15, 0, tzinfo=bot.BEIJING)
    response = Mock(headers={}, content=b'{}')
    response.json.return_value = {'list': [{
        'dm': 'MO2612-P-7500', 'utime': int(stamp.timestamp()), 'o': 21.5,
        'p': 22.0, 'mrj': 21.0, 'mcj': 23.0, 'vol': 1, 'ccl': 50,
    }]}
    with (patch.object(bot.requests, 'get', return_value=response),
          patch.object(bot, '_validate_quote_frame', side_effect=lambda frame, *_: frame)):
        quotes = bot._fetch_eastmoney_mo_quotes(stamp)
    assert quotes.iloc[0]['openprice'] == 21.5


def test_sina_mo_open_check_requires_same_day_contract_and_matching_open():
    clock = datetime(2026, 9, 30, 13, 50, tzinfo=bot.BEIJING)
    fields = [''] * 52
    # 2026-09-30 live cross-check: MO2610-P-7000 had Eastmoney o=39.0,
    # Sina [8]=46.4 and [9]=39.0; a previous-close off-by-one must fail.
    fields[7], fields[8], fields[9], fields[32] = (
        '7000', '46.400', '39.000', '2026-09-30 13:41:09'
    )
    response = Mock(headers={}, content=(
        'var hq_str_P_OP_mo2610P7000="' + ','.join(fields) + '";'
    ).encode('gbk'))
    with patch.object(bot.requests, 'get', return_value=response):
        checked = bot.verify_sina_option_open('MO2610-P-7000', clock.date(), 39.0, clock)
        assert checked['openprice'] == 39.0
        with pytest.raises(RuntimeError, match='源冲突'):
            bot.verify_sina_option_open('MO2610-P-7000', clock.date(), 46.4, clock)
        with pytest.raises(RuntimeError, match='源冲突'):
            bot.verify_sina_option_open('MO2610-P-7000', clock.date(), 40.0, clock)
        with pytest.raises(RuntimeError, match='日期不匹配'):
            bot.verify_sina_option_open('MO2610-P-7000', date(2026, 9, 29), 39.0, clock)
        fields[32] = '2026-09-30 00:00:00'
        response.content = ('var hq_str_P_OP_mo2610P7000="' + ','.join(fields) + '";').encode('gbk')
        with pytest.raises(RuntimeError, match='日期不匹配'):
            bot.verify_sina_option_open('MO2610-P-7000', clock.date(), 39.0, clock)
        fields[32] = '2026-09-30 09:29:30'
        response.content = ('var hq_str_P_OP_mo2610P7000="' + ','.join(fields) + '";').encode('gbk')
        assert bot.verify_sina_option_open('MO2610-P-7000', clock.date(), 39.0, clock)['openprice'] == 39.0
        fields[32] = '2026-09-30 09:28:59'
        response.content = ('var hq_str_P_OP_mo2610P7000="' + ','.join(fields) + '";').encode('gbk')
        with pytest.raises(RuntimeError, match='日期不匹配'):
            bot.verify_sina_option_open('MO2610-P-7000', clock.date(), 39.0, clock)


def test_im_ordinary_open_on_eastmoney_requires_independent_sina_check():
    anchor = policy.default_extension('IM')
    anchor['v14_ordinary_put_pending'] = {
        'product': 'IM', 'signal_day': '2026-09-29', 'execution_day': '2026-09-30',
        'parent_puts': 3,
        'legs': {'core': {'old_contract': None, 'old_security_id': None,
                          'old_qty': 0.0, 'new_contract': 'MO2610-P-7000',
                          'new_security_id': None, 'new_qty': 1.5, 'changed': True},
                 'momentum': {'old_contract': None, 'old_security_id': None,
                              'old_qty': 0.0, 'new_contract': None,
                              'new_security_id': None, 'new_qty': 0.0,
                              'changed': False}},
    }
    chain = pd.DataFrame([{'instrument': 'MO2610-P-7000', 'openprice': 39.0}])
    chain.attrs.update(source='东方财富', source_date=date(2026, 9, 30))
    clock = datetime(2026, 9, 30, 13, 50, tzinfo=bot.BEIJING)
    original = bot.LIVE_CONTINUATION_ANCHOR['IM']
    bot.LIVE_CONTINUATION_ANCHOR['IM'] = anchor
    try:
        with (patch.object(bot, 'fetch_cffex_quotes', return_value=chain),
              patch.object(bot, 'verify_sina_option_open', return_value={'openprice': 39.0}) as verify):
            result = bot._v14_ordinary_open_evidence('IM', clock.date(), clock)
        assert result['status'] == 'confirmed_open_research_price'
        assert '新浪' in result['prices'][0]['source']
        verify.assert_called_once_with('MO2610-P-7000', clock.date(), 39.0, clock)
        with (patch.object(bot, 'fetch_cffex_quotes', return_value=chain),
              patch.object(bot, 'verify_sina_option_open', side_effect=RuntimeError('Sina unavailable'))):
            closed = bot._v14_ordinary_open_evidence('IM', clock.date(), clock)
        assert closed['status'] == 'closed_missing_open_price'
        assert 'Sina unavailable' in closed['reason']
    finally:
        bot.LIVE_CONTINUATION_ANCHOR['IM'] = original


def test_sina_chain_marks_one_yesterday_row_without_reselecting_another_strike():
    def payload(strike, observed):
        fields = [''] * 47
        fields[3], fields[7], fields[32] = '0.25', str(strike), observed
        fields[40], fields[43], fields[45], fields[46] = '0.24', 'M', 'P', '2026-12-23'
        return ','.join(fields)

    listing = Mock(headers={}, content=b'CON_OP_1001,CON_OP_1002,CON_OP_1003')
    details = ('var hq_str_CON_OP_1001="' + payload(7.25, '2026-09-30 13:10:00') + '";'
               'var hq_str_CON_OP_1002="' + payload(7.50, '2026-09-29 15:00:00') + '";'
               'var hq_str_CON_OP_1003="' + payload(7.75, '2026-09-30 00:00:00') + '";')
    quotes_response = Mock(headers={}, content=details.encode('gbk'))
    clock = datetime(2026, 9, 30, 13, 20, tzinfo=bot.BEIJING)
    with patch.object(bot.requests, 'get', side_effect=[listing, quotes_response]):
        chain, stamp = bot.fetch_sina_510500_chain('2612')
    assert len(chain) == 3
    assert stamp['date'] == '20260930' and stamp['time'] == '131000'
    with bot.runtime_clock(clock):
        assert bot._quote_row(chain, '510500P2612M07250', clock.date()) is not None
        assert bot._quote_row(chain, '510500P2612M07500', clock.date()) is None
        assert bot._quote_row(chain, '510500P2612M07750', clock.date()) is None
        with pytest.raises(RuntimeError, match='报价日期与信号日'):
            stale = chain[chain.contract.eq('510500P2612M07500')].iloc[0]
            bot._require_selected_510500_quote_time(stale, clock, clock.date())


def test_ic_core_overlay_rejects_yesterday_row_in_otherwise_today_sina_chain():
    clock = datetime(2026, 9, 30, 15, 10, tzinfo=bot.BEIJING)
    held = '510500P2612M07500'
    chain = pd.DataFrame([
        {'contract': held, 'last': 0.75,
         'source_stamp': datetime(2026, 9, 29, 15, 0, tzinfo=bot.BEIJING)},
        {'contract': '510500P2612M07250', 'last': 0.25,
         'source_stamp': datetime(2026, 9, 30, 15, 0, tzinfo=bot.BEIJING)},
    ])
    stamp = {'date': '20260930', 'time': '150000'}
    anchor = {'v14_core_put_contract': held, 'v14_core_put_security_id': 'held-id',
              'v14_core_put_qty': 1}
    signal = {'market_date': clock.date(), 'option_monthly_reset_due': True,
              'close_confirmed': False}
    original = bot.LIVE_CONTINUATION_ANCHOR['IC']
    with (bot.runtime_clock(clock),
          patch.object(bot, 'fetch_510500_chain_with_failover', return_value=(chain, stamp))):
        with pytest.raises(RuntimeError, match='缺少有效当日报价'):
            bot._v14_ic_core_overlay(signal, anchor)
        assert signal.get('iv_monitor_option_price') is None
        assert signal.get('v14_core_put_mark') is None
        with pytest.raises(RuntimeError, match='缺少信号日'):
            bot._require_selected_510500_quote_time(chain.iloc[0], clock, None)
        bot.LIVE_CONTINUATION_ANCHOR['IC'] = anchor
        try:
            with patch.object(policy, 'apply_policy', return_value={'v14_action': 'HOLD'}):
                blocked = bot._apply_v14_live_policy('IC', dict(signal))
        finally:
            bot.LIVE_CONTINUATION_ANCHOR['IC'] = original
        assert blocked['v14_action'] == 'HOLD_FAIL_CLOSED'
        assert blocked.get('v14_core_put_mark') is None

        chain.loc[0, 'source_stamp'] = datetime(2026, 9, 30, 15, 0, tzinfo=bot.BEIJING)
        bot._v14_ic_core_overlay(signal, anchor)
    assert signal['iv_monitor_option_price'] == 0.75
    assert signal['v14_core_put_current_contract'] == held


def test_sina_held_put_1430_stale_quote_is_hold_missing_not_global_failure():
    clock = datetime(2026, 9, 30, 15, 10, tzinfo=bot.BEIJING)
    contract = '510500P2612M07500'
    chain = pd.DataFrame([{'contract': contract, 'last': .25,
                           'source_stamp': datetime(2026, 9, 30, 14, 30,
                                                    tzinfo=bot.BEIJING)}])
    with bot.runtime_clock(clock):
        row = bot._quote_row(chain, contract, clock.date())
    assert row is None
    bot._require_existing_leg_quote('IC Put', contract, row, 'HOLD')
    with pytest.raises(RuntimeError, match='旧腿.*缺少有效报价'):
        bot._require_existing_leg_quote('IC Put', contract, row, 'RESIZE_OR_ROLL')


def test_current_month_cffex_archive_does_not_reuse_stale_cache(monkeypatch):
    month = pd.Timestamp(datetime.now(bot.BEIJING).date())
    key = month.strftime('%Y%m')
    monkeypatch.setitem(bot._CFFEX_MONTH_CACHE, key, b'PKstale')
    with patch.object(bot.requests, 'get', side_effect=bot.requests.ConnectionError('HTTPS unavailable')) as fetch:
        with pytest.raises(bot.requests.ConnectionError, match='HTTPS传输失败'):
            bot._cffex_month_archive(month)
    assert fetch.call_count == 1
    assert fetch.call_args.args[0].startswith('https://')


def test_generic_query_uses_verified_snapshot_during_settlement_window():
    output = []

    @contextmanager
    def message():
        yield type('Sink', (), {'write': lambda _self, value: output.append(value)})()

    clock = datetime(2026, 9, 30, 15, 10, tzinfo=bot.BEIJING)
    with (patch.object(bot, '_now_beijing', return_value=clock),
          patch.object(bot, '_sm', message),
          patch.object(bot, '_write_last_verified_snapshot', side_effect=lambda msg, product: msg.write(product)),
          patch.object(bot.ICIMMainlinesBot, '_handle_signal', side_effect=AssertionError('unverified close'))):
        bot.ICIMMainlinesBot()._handle_snapshot(('IC', 'IM'))
    assert '完整收盘数据尚未核验' in ''.join(output)
    assert 'ICIM' in ''.join(output)


def test_explicit_close_signal_and_generic_query_have_distinct_settlement_gates():
    clock = datetime(2026, 9, 30, 15, 10, tzinfo=bot.BEIJING)
    with patch.object(bot, '_build_live_trade_signal', return_value={'stage': 'validated'}) as build:
        assert bot.build_live_trade_signal('IC', now=clock, mode='close')['stage'] == 'validated'
    build.assert_called_once_with('IC', clock, 'close')
    with patch.object(bot, '_build_live_trade_signal') as build:
        with pytest.raises(RuntimeError, match='尚未收盘'):
            bot.build_live_trade_signal('IC', now=clock.replace(hour=14, minute=59), mode='close')
    build.assert_not_called()
    with (patch.object(bot, '_now_beijing', return_value=clock.replace(minute=31)),
          patch.object(bot.ICIMMainlinesBot, '_handle_signal') as handle):
        bot.ICIMMainlinesBot()._handle_snapshot(('IC',))
    handle.assert_called_once_with(('IC',), mode='close')


def test_failed_product_and_failed_snapshot_do_not_discard_other_product():
    output = []

    @contextmanager
    def message():
        yield type('Sink', (), {'write': lambda _self, value: output.append(value)})()

    clock = datetime(2026, 9, 30, 16, 0, tzinfo=bot.BEIJING)
    with (patch.object(bot, '_now_beijing', return_value=clock),
          patch.object(bot, '_sm', message),
          patch.object(bot, 'build_live_trade_signal', side_effect=RuntimeError('source missing')),
          patch.object(bot, '_write_last_verified_snapshot', side_effect=KeyError('broken anchor'))):
        bot.ICIMMainlinesBot()._handle_signal(('IC', 'IM'), mode='close')
    report = ''.join(output)
    assert report.count('最后已核验快照也无法安全渲染') == 2
    assert bot.PRODUCT_NAMES['IC'] in report and bot.PRODUCT_NAMES['IM'] in report
