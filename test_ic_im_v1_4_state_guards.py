from copy import deepcopy
from datetime import date
import pytest
import ic_im_v1_4_policy as policy
import poe_ic_im_v1_4_state as state
from test_poe_ic_im_v1_3_state import _signals


def new_signal():
    signal = _signals(date(2026, 8, 25))['IC']
    signal.update(policy.default_extension('IC'))
    signal.update(market_date=date(2026, 9, 24), strategy_version='1.4', strategy_revision='r1',
                  v14_build_id=policy.BUILD_ID, v14_rule_revision=policy.RULE_REVISION)
    return signal


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
