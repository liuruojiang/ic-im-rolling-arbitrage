from datetime import date,datetime
from unittest.mock import patch
import pandas as pd
import pytest
import poe_ic_im_mainline_v1_4_bot as bot


def test_ic_candidate_rejects_stale_chain():
    signal={'market_date':date(2026,9,18),'next_trade_date':date(2026,9,21)}
    with bot.runtime_clock(datetime(2026,9,18,16,tzinfo=bot.BEIJING)), patch.object(bot,'fetch_510500_chain_with_failover',return_value=(pd.DataFrame(),{'date':'20260901','time':'150000'})):
        with pytest.raises(RuntimeError):
            bot._v14_ic_short_put_candidate(signal)


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
