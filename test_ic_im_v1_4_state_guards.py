from copy import deepcopy
from datetime import date, datetime
from unittest.mock import patch
import pandas as pd
import pytest
import ic_im_v1_4_policy as policy
import poe_ic_im_v1_4_state as state
import poe_ic_im_mainline_v1_4_bot as bot
from test_poe_ic_im_v1_3_state import _signals


def new_signal():
    signal = _signals(date(2026, 8, 25))['IC']
    signal.update(policy.default_extension('IC'))
    signal.update(market_date=date(2026, 9, 28), strategy_version='1.4', strategy_revision='r1',
                  v14_build_id=policy.FIX7_BUILD_ID, v14_rule_revision=policy.FIX7_RULE_REVISION)
    return signal


def test_fix8_ordinary_target_requires_matching_pending_plan_before_ledger_write():
    day = date(2026, 9, 29)
    anchor = {'post_core_put_contract': 'MO2612-P-7500',
              'post_momentum_put_contract': None,
              'verified_core_put_qty_normalized': 1.0,
              'verified_momentum_put_qty_normalized': 0.0}
    signal = {'v14_route_state': 'future', 'option_monthly_reset_due': False,
              'v14_profit_pending': False, 'v14_action': 'HOLD',
              'core_put_target_contract': 'MO2612-P-7500',
              'core_put_target_qty_normalized': 1.0,
              'momentum_put_target_contract': 'MO2612-P-7200',
              'momentum_put_target_qty_normalized': .5}
    with pytest.raises(RuntimeError, match='缺少T收盘预选计划'):
        state.validate_ordinary_put_plan('IM', anchor, signal, day)
    signal['v14_ordinary_put_plan_status'] = 'scheduled_t_plus_1_open'
    signal['v14_ordinary_put_pending'] = {
        'legs': {
            'core': {'old_contract': 'MO2612-P-7500', 'old_qty': 1.0,
                     'new_contract': 'MO2612-P-7500', 'new_qty': 1.0},
            'momentum': {'old_contract': None, 'old_qty': 0.0,
                         'new_contract': 'MO2612-P-7200', 'new_qty': .5},
        }
    }
    state.validate_ordinary_put_plan('IM', anchor, signal, day)
    signal['v14_ordinary_put_pending']['legs']['momentum']['new_contract'] = 'MO2612-P-7100'
    with pytest.raises(RuntimeError, match='预选身份'):
        state.validate_ordinary_put_plan('IM', anchor, signal, day)


def test_fix8_pending_survives_hash_chain_restart_and_missing_open_closes_old_plan(tmp_path):
    # Synthetic one-record ledger isolates persistence and missing-price closure.
    record = state.bootstrap_record()
    record['verified_day'] = '2026-09-29'
    for product in state.PRODUCTS:
        record['products'][product]['last_verified_day'] = '2026-09-29'
    core_contract = record['products']['IM']['post_core_put_contract']
    core_qty = record['products']['IM']['verified_core_put_qty_normalized']
    plan = {'product': 'IM', 'signal_day': '2026-09-29', 'execution_day': '2026-09-30',
            'parent_puts': 3,
            'legs': {
                'core': {'old_contract': core_contract, 'old_security_id': None,
                         'old_qty': core_qty, 'new_contract': core_contract,
                         'new_security_id': None, 'new_qty': core_qty, 'changed': False},
                'momentum': {'old_contract': None, 'old_security_id': None,
                             'old_qty': 0.0, 'new_contract': 'MO2612-P-7000',
                             'new_security_id': None, 'new_qty': .5, 'changed': True},
            }}
    record['products']['IM']['v14_ordinary_put_pending'] = plan
    record['digest'] = state._digest(record)
    store = state.StateStore(tmp_path)
    store._atomic_write(store.journal_dir / '000000-2026-09-29.json', record)
    store._atomic_write(store.latest_path, record)
    reopened = state.StateStore(tmp_path).load_latest()
    assert reopened['digest'] == record['digest']
    recovered = state.anchors_from_record(reopened)['IM']
    assert recovered['v14_ordinary_put_pending'] == plan
    missing_open = pd.DataFrame([
        {'instrument': core_contract, 'lastprice': 18.0},
        {'instrument': 'MO2612-P-7000', 'lastprice': 11.0},
    ])
    missing_open.attrs.update(source='东方财富', source_date=date(2026, 9, 30))
    original = bot.LIVE_CONTINUATION_ANCHOR['IM']
    bot.LIVE_CONTINUATION_ANCHOR['IM'] = recovered
    try:
        with patch.object(bot, 'fetch_cffex_quotes', return_value=missing_open):
            opening = bot._v14_ordinary_open_evidence('IM', date(2026, 9, 30), datetime(2026, 9, 30, 16))
        assert opening['status'] == 'closed_missing_open_price'
        assert 'openprice' in opening['reason']
        assert recovered['verified_momentum_put_qty_normalized'] == 0.0
        signal = {'market_date': date(2026, 9, 30),
                  'next_trade_date': bot._roll_forward_exchange_day(date(2026, 10, 1)),
                  'close_confirmed': True, 'option_monthly_reset_due': False,
                  'v14_route_state': 'future', 'v14_profit_pending': False, 'v14_action': 'HOLD',
                  'core_put_target_contract': core_contract,
                  'core_put_target_qty_normalized': core_qty,
                  'momentum_put_target_contract': 'MO2612-P-7000',
                  'momentum_put_target_qty_normalized': .5,
                  'v13_parent_puts_per_full_core': 3}
        bot._v14_schedule_ordinary_put('IM', signal, recovered, opening)
        assert signal['v14_ordinary_put_open_status'] == 'closed_missing_open_price'
        assert signal['v14_ordinary_put_open_plan'] == plan
        assert signal['v14_ordinary_put_pending']['signal_day'] == date(2026, 9, 30)
        assert signal['v14_ordinary_put_pending']['execution_day'] != plan['execution_day']
    finally:
        bot.LIVE_CONTINUATION_ANCHOR['IM'] = original


def test_fix6_im_delivery_rejects_reintroduced_call_and_wrong_exit_action():
    signal = _signals(date(2026, 9, 28))['IM']
    signal.update(call_current_contract=None, call_has_position=False, call_action='HOLD')
    state.validate_im_option_values(signal)
    signal.update(call_target_qty_normalized=-1.0, call_target_contract='MO2610-C-9000',
                  call_target_expiry=date(2026, 10, 16), call_target_strike=9000.0,
                  call_action='OPEN_CALL')
    with pytest.raises(RuntimeError, match='不得卖Call'):
        state.validate_im_option_values(signal)
    signal.update(call_target_qty_normalized=0.0, call_target_contract=None,
                  call_target_expiry=None, call_target_strike=None,
                  call_current_contract='MO2610-C-9000', call_has_position=True,
                  call_action='RESCUE_NEXT_LISTED')
    with pytest.raises(RuntimeError, match='退场动作'):
        state.validate_im_option_values(signal)
    signal['call_action'] = 'CLOSE_CALL'
    state.validate_im_option_values(signal)


def short_signal():
    signal = new_signal()
    signal.update(v14_route_state='short_put', v14_cycle_id='IC-2026-09-21-510500P2610M07500',
                  v14_last_event_id='IC:2026-09-18:2026-09-21:ENTER:510500P2610M07500',
                  v14_short_put_contract='510500P2610M07500', v14_short_put_security_id='10019999',
                  v14_short_put_qty_normalized=9.8, v14_short_put_entry_premium=.12,
                  v14_short_put_expiry=date(2026, 10, 28), core_put_target_delta=0., put_target_core_qty=0)
    return signal


@pytest.mark.parametrize('field', ['strategy_version', 'strategy_revision', 'v14_build_id', 'v14_rule_revision'])
def test_reject_obsolete_producer_on_effective_date(field):
    signal = new_signal()
    signal[field] = 'obsolete'
    with pytest.raises(RuntimeError, match='生产器身份'):
        state.validate_delivery_values(signal, 'IC')
    state.validate_delivery_values(signal, 'IC', historical_record=True)


def test_migrated_core_put_allows_legacy_target_only_before_effective_date():
    signal = new_signal()
    signal.update(
        market_date=date(2026, 9, 17),
        v14_build_id=policy.PREVIOUS_BUILD_ID,
        v14_rule_revision=policy.PREVIOUS_RULE_REVISION,
        v14_core_put_qty=14,
        v14_core_put_contract='510500P2612M07500',
        v14_core_put_security_id='10012099',
        put_target_core_qty=0,
    )
    state.validate_delivery_values(signal, 'IC')
    signal.update(
        market_date=date(2026, 9, 18),
        v14_build_id=policy.FIX3_BUILD_ID,
        v14_rule_revision=policy.FIX3_RULE_REVISION,
    )
    with pytest.raises(RuntimeError, match='核心Put数量与核心目标不一致'):
        state.validate_delivery_values(signal, 'IC')


def test_intraday_core_put_target_may_differ_until_close_confirmation():
    signal = new_signal()
    signal.update(
        close_confirmed=False,
        v14_core_put_qty=12,
        v14_core_put_contract='510500P2612M07500',
        v14_core_put_security_id='10012099',
        put_target_core_qty=14,
    )
    state.validate_delivery_values(signal, 'IC')
    signal['close_confirmed'] = True
    with pytest.raises(RuntimeError, match='核心Put数量与核心目标不一致'):
        state.validate_delivery_values(signal, 'IC')


def test_pre_fix3_replay_accepts_its_original_producer_identity():
    signal = new_signal()
    signal.update(
        market_date=date(2026, 9, 17),
        v14_build_id=policy.PREVIOUS_BUILD_ID,
        v14_rule_revision=policy.PREVIOUS_RULE_REVISION,
    )
    state.validate_delivery_values(signal, 'IC')


def test_fix3_window_replay_accepts_its_original_producer_identity():
    signal = new_signal()
    signal.update(
        market_date=date(2026, 9, 23),
        v14_build_id=policy.FIX3_BUILD_ID,
        v14_rule_revision=policy.FIX3_RULE_REVISION,
    )
    state.validate_delivery_values(signal, 'IC')


@pytest.mark.parametrize('changes', [
    {'v14_short_put_contract':'THIS_IS_NOT_A_PUT'},
    {'v14_short_put_expiry':None},
    {'v14_short_put_expiry':date(2026,11,25)},
    {'v14_short_put_expiry':date(2026,10,27)},
    {'v14_cycle_id':None}, {'v14_last_event_id':None},
    {'v14_short_put_security_id':'invalid'},
    {'v14_short_put_entry_premium':float('inf')},
    {'core_put_target_delta':.25}, {'put_target_core_qty':1},
    {'v14_roll_pending':'false'}, {'v14_recovery_net_pnl':float('nan')},
    {'v14_core_put_qty':1, 'v14_core_put_contract':'510500P2612M07500',
     'v14_core_put_security_id':'10012099', 'put_target_core_qty':1},
    {'v14_core_put_qty':0, 'v14_core_put_contract':'510500P2612M07500'},
    {'v14_core_put_qty':float('nan')},
])
def test_reject_corrupt_short_state_before_hash_write(changes):
    signal = short_signal()
    state.validate_delivery_values(signal, 'IC')
    signal.update(changes)
    with pytest.raises(RuntimeError):
        state.validate_delivery_values(signal, 'IC')
