"""Independent display/cache red-team. Synthetic diagnostics, no trading advice."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import date, datetime
from unittest.mock import Mock

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_put_execution_repairs_20261016 as fixture

CORE = '510500P2612M07500'
MOM = '510500P2611M06500'
DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 13, 30, tzinfo=bot.BEIJING)


def split_anchor():
    anchor = deepcopy(bot.LIVE_CONTINUATION_ANCHOR['IC'])
    anchor.update(fixture.anchor('IC'))
    anchor.update(verified_momentum_weight=1., verified_next_momentum_weight=1.,
                  verified_grid_units=0., verified_next_grid_units=0.,
                  verified_call_contracts_normalized=0., verified_core_put_qty=5,
                  verified_momentum_put_qty=3, verified_momentum_put_delta=.1,
                  post_put_contract=MOM, post_put_security_id='10013000',
                  post_put_qty=8, verified_put_qty_normalized=8)
    return anchor


def split_signal(**changes):
    signal = fixture.signal('IC', DAY, close_confirmed=False, next_trade_date=date(2026, 10, 22),
        etf_price=7.8, future_last=7800., put_current_contract=MOM,
        put_target_contract=MOM, put_target_security_id='10013000',
        put_current_core_qty=5, put_current_momentum_qty=3, put_current_total_qty=8,
        put_target_core_qty=0, put_target_momentum_qty=3, put_target_total_qty=3,
        core_put_target_delta=.25, momentum_put_target_delta=.1,
        total_put_current_delta=.35, total_put_target_delta=.35,
        momentum_next_weight=1., put_action='HOLD')
    signal.update(changes)
    return signal


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', Mock(side_effect=AssertionError('network forbidden')))
    monkeypatch.setattr(bot, '_SIGNAL_OBSERVER', None)


def test_final_text_distinct_legs_and_zero_residual_identity():
    signal = split_signal(v14_core_put_current_contract=CORE,
                          v14_core_put_contract=CORE, put_target_core_qty=5,
                          put_target_momentum_qty=0, put_target_total_qty=5)
    before = deepcopy(signal)
    bot._refresh_ic_put_text(signal)
    assert CORE in signal['put_current'] and MOM in signal['put_current']
    assert CORE in signal['put_target'] and MOM not in signal['put_target']
    assert {k: v for k, v in signal.items() if k not in {'put_current','put_target'}} == before


@pytest.mark.parametrize('route', ['short_put', 'assigned_etf', 'cash_wait', 'recovery_future'])
def test_nonfuture_zero_core_does_not_show_residual_contract(route):
    signal = split_signal(v14_route_state=route, put_current_core_qty=0,
        put_target_core_qty=0, put_target_momentum_qty=3, v14_core_put_contract=None,
        v14_core_put_current_contract=CORE)
    bot._refresh_ic_put_text(signal)
    assert CORE not in signal['put_target']
    assert CORE not in signal['put_current']
    assert MOM in signal['put_target']


def test_bootstrap_snapshot_retains_known_single_core_identity(monkeypatch, record_property):
    record = state.bootstrap_record()
    anchors = state.anchors_from_record(record)
    anchor = anchors['IC']
    assert anchor.get('v14_core_put_contract') is None
    assert bot._ic_current_quantity_breakdown(anchor)['core'] > 0
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    msg = Mock()
    bot._write_last_verified_snapshot(msg, 'IC')
    rendered = msg.write.call_args.args[0]
    record_property('anchor_core_contract', anchor.get('v14_core_put_contract'))
    record_property('anchor_shared_contract', anchor['post_put_contract'])
    record_property('rendered', rendered)
    assert anchor['post_put_contract'] in rendered, 'Known bootstrap economic core identity lost in revised snapshot'


def test_core_overlay_failure_cannot_label_momentum_as_current_core(monkeypatch, record_property):
    anchor = split_anchor()
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
                        Mock(side_effect=RuntimeError('dedicated core quote unavailable')))
    out = bot._apply_v14_live_policy('IC', split_signal())
    assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
    bot._refresh_ic_put_text(out, bot.LIVE_CONTINUATION_ANCHOR['IC'])
    record_property('rendered_current', out['put_current'])
    record_property('held_core', anchor['v14_core_put_contract'])
    record_property('held_momentum', anchor['post_put_contract'])
    record_property('candidate_status', out.get('v14_candidate_data_status'))
    # A failed mark does not make the held, independently verified identity unknown.
    assert CORE in out['put_current'], 'Core quote failure silently attaches momentum contract to held core quantity'


def test_successful_core_overlay_corrects_split_before_render(monkeypatch):
    anchor = split_anchor()
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    chain = pd.DataFrame([dict(contract=CORE, last=.8, strike=7.5,
                               security_id='10012000', source_stamp=CLOCK)])
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
        lambda month: (chain, dict(date='20261021',time='133000',source='synthetic')))
    signal = split_signal()
    bot._v14_ic_core_overlay(signal, anchor)
    bot._refresh_ic_put_text(signal, anchor)
    assert signal['put_target_core_qty'] == 5
    assert CORE in signal['put_current'] and CORE in signal['put_target']
    assert MOM in signal['put_current'] and MOM in signal['put_target']


def test_core_overlay_failure_cannot_publish_momentum_iv_as_core_monitor(monkeypatch, record_property):
    anchor = split_anchor()
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
                        Mock(side_effect=RuntimeError('dedicated core quote unavailable')))
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *args: .02)
    pricing = Mock(return_value=.45)
    monkeypatch.setattr(bot, '_implied_volatility', pricing)
    signal = split_signal(iv_monitor_option_price=.15, sse_time='133000',
                          etf_quote_date='20261021', etf_quote_time='133000')
    out = bot._apply_v14_live_policy('IC', signal)
    warning = bot.build_iv_warning('IC', out, {}, None, CLOCK)
    record_property('warning', str(warning))
    record_property('candidate_status', out.get('v14_candidate_data_status'))
    assert warning['iv'] is None, 'Missing dedicated-core evidence published another sleeve finite IV'
    pricing.assert_not_called()


def test_failed_core_overlay_cannot_schedule_close_plan_or_mutate_anchor(monkeypatch):
    anchor = split_anchor()
    before = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
                        Mock(side_effect=RuntimeError('dedicated core quote unavailable')))
    signal = split_signal(close_confirmed=True)
    out = bot._apply_v14_live_policy('IC', signal)
    bot._v14_schedule_ordinary_put('IC', out, anchor, None)
    assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert out['v14_ordinary_put_pending'] is None
    assert bot.LIVE_CONTINUATION_ANCHOR['IC'] == before
    build, rule = bot.v14_policy.identity_for_signal_day(DAY)
    out.update(strategy_version=state.STRATEGY_VERSION, strategy_revision=state.STRATEGY_REVISION,
               v14_build_id=build, v14_rule_revision=rule,
               v14_execution_revision=bot.put_execution.REVISION)
    with pytest.raises(RuntimeError) as rejected:
        state.validate_v14_new_signal(out, 'IC')
    assert '\u76ee\u6807' in str(rejected.value), str(rejected.value)


@pytest.mark.parametrize('new_delta, expected_qty', [(0., 0), (.1, 4)])
def test_profit_open_current_identity_survives_later_zero_or_resized_target(monkeypatch, new_delta, expected_qty):
    anchor = split_anchor()
    new_core = '510500P2701M06800'
    anchor.update(v14_profit_pending=True, v14_profit_trigger_day=date(2026,10,20),
        v14_profit_execution_day=DAY, v14_profit_old_contract=CORE,
        v14_profit_old_security_id='10012000', v14_profit_old_qty=5.,
        v14_profit_reentry_contract=new_core, v14_profit_reentry_security_id='10014000',
        v14_profit_reentry_qty=5.)
    plan = bot.put_execution.profit_plan('IC', anchor, DAY)
    event = dict(kind='profit3x', plan=plan, status='confirmed_open_research_price',
                 prices=fixture.prices(plan, day=DAY))
    held = bot.put_execution.project_profit_open('IC', anchor, event, DAY)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', held)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    chain = pd.DataFrame([dict(contract=new_core, last=.8, strike=6.8,
                               security_id='10014000', source_stamp=CLOCK)])
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
        lambda month: (chain, dict(date='20261021',time='133000',source='synthetic')))
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *args: .02)
    monkeypatch.setattr(bot, '_implied_volatility', lambda *args: .3)
    monkeypatch.setattr(bot, '_bs_price_delta', lambda *args: (.8, -.5))
    monkeypatch.setattr(bot, '_v14_ic_short_put_candidate', lambda *args, **kwargs: {'tradable':False})
    monkeypatch.setattr(bot, '_v14_option_mark', lambda *args: (.8, .8))
    out = bot._apply_v14_live_policy('IC', split_signal(close_confirmed=True,
                                 core_put_target_delta=new_delta))
    bot._v14_schedule_ordinary_put('IC', out, held, None)
    bot._refresh_ic_put_text(out, held)
    assert out['put_current_core_qty'] == 5
    assert new_core in out['put_current'] and CORE not in out['put_current']
    assert out['put_target_core_qty'] == expected_qty
    if expected_qty:
        assert new_core in out['put_target']
    else:
        assert new_core not in out['put_target']
    leg = out['v14_ordinary_put_pending']['legs']['core']
    assert leg['old_qty'] == 5 and leg['old_contract'] == new_core
    assert leg['new_qty'] == expected_qty


def test_im_snapshot_zero_core_does_not_show_residual_contract(monkeypatch, record_property):
    anchor = deepcopy(bot.LIVE_CONTINUATION_ANCHOR['IM'])
    anchor.update(fixture.anchor('IM'))
    anchor.update(v14_route_state='cash_wait', verified_momentum_weight=1.,
                  verified_grid_units=0., verified_call_contracts_normalized=0.,
                  verified_core_put_qty_normalized=0., verified_momentum_put_qty_normalized=.5,
                  verified_put_qty_normalized=.5, post_core_put_contract='MO2612-P-7600',
                  post_momentum_put_contract='MO2611-P-6500')
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IM', anchor)
    message = Mock()
    bot._write_last_verified_snapshot(message, 'IM')
    text = message.write.call_args.args[0]
    record_property('rendered', text)
    assert 'MO2612-P-7600' not in text, 'Zero-quantity residual core identity remains in snapshot'
    assert 'MO2611-P-6500' in text


def test_observation_mode_day_and_copy_boundaries():
    fear = bot.fear_grid
    evidence = dict(fear_greed_index=20, fear_data_date='2026-10-21', fear_data_status='synthetic')
    with fear.observation_batch():
        fear._batch_observation(('baifenwei',DAY,'intraday'), lambda: evidence)
        assert fear.observed_in_batch('baifenwei', DAY, 'close') is None
        assert fear.observed_in_batch('baifenwei', date(2026,10,20), 'intraday') is None
        observed = fear.observed_in_batch('baifenwei', DAY, 'intraday')
        observed['fear_greed_index'] = 90
        assert fear.observed_in_batch('baifenwei', DAY, 'intraday')['fear_greed_index'] == 20
    assert fear.observed_in_batch('baifenwei', DAY, 'intraday') is None
    with fear.observation_batch():
        assert fear.observed_in_batch('baifenwei', DAY, 'intraday') is None


def test_failed_multi_product_preserves_independent_snapshot_and_shared_observation(monkeypatch):
    fear = bot.fear_grid
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, '_sm', lambda: nullcontext(message))
    message = Mock()
    def fail(product, mode, budget):
        if product == 'IC':
            fear._batch_observation(('baifenwei',DAY,mode), lambda: dict(
                fear_greed_index=23, fear_data_date=str(DAY), fear_data_status='synthetic'))
        raise RuntimeError(product + '_source_failed')
    monkeypatch.setattr(bot, 'build_live_signal_with_transport_retry', fail)
    snapshots = []
    def snapshot(msg, product):
        snapshots.append(product)
        if product == 'IC':
            raise ValueError('IC_only_snapshot_failed')
        msg.write('IM_independent_snapshot')
    monkeypatch.setattr(bot, '_write_last_verified_snapshot', snapshot)
    network = Mock(side_effect=AssertionError('fallback network'))
    monkeypatch.setattr(fear, 'fetch_score', network)
    monkeypatch.setattr(fear, 'fetch_jiu_reference', network)
    bot.ICIMMainlinesBot()._handle_signal(('IC','IM'), 'intraday')
    rendered = ''.join(call.args[0] for call in message.write.call_args_list)
    assert snapshots == ['IC','IM']
    assert 'IM_independent_snapshot' in rendered
    assert 'IC_source_failed' in rendered and 'IM_source_failed' in rendered
    assert rendered.count('23') >= 2
    network.assert_not_called()


def test_failure_does_not_reuse_previous_report_observation(monkeypatch):
    fear = bot.fear_grid
    with fear.observation_batch():
        fear._batch_observation(('baifenwei',DAY,'intraday'), lambda: dict(
            fear_greed_index=19.123456, fear_data_date=str(DAY), fear_data_status='old_report'))
    message = Mock()
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, '_sm', lambda: nullcontext(message))
    monkeypatch.setattr(bot, 'build_live_signal_with_transport_retry', Mock(side_effect=RuntimeError('offline')))
    monkeypatch.setattr(bot, '_write_last_verified_snapshot', lambda *args: None)
    bot.ICIMMainlinesBot()._handle_signal(('IC',),'intraday')
    rendered = ''.join(call.args[0] for call in message.write.call_args_list)
    assert '19.123456' not in rendered and 'old_report' not in rendered
