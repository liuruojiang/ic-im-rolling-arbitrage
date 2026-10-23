"""Independent calendar red team: consumer grammar and isolated real ledger API."""
from datetime import date, datetime
from pathlib import Path

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as seed


TARGETS = [
    'MO2703-P-8200',
    'MO２７０３-P-8200',
    'MO٢٧٠٣-P-8200',
    'MO2703-P-８２００',
    'MO2703-P-8200.0',
]


def known_signal(contract):
    return dict(product='IM', market_date=date(2026, 9, 30),
                next_trade_date=date(2026, 10, 8),
                core_current='IM2612', core_target='IM2612',
                momentum_put_current_contract=None,
                momentum_put_target_contract=contract,
                momentum_put_target_qty_normalized=1.5)


@pytest.mark.parametrize('contract', TARGETS)
def test_unknown_year_target_cannot_hide_behind_a_second_accepted_grammar(contract):
    # Rejection may be unknown year or invalid contract syntax. Neither a
    # Unicode digit nor decimal strike should bypass an adopted-target gate.
    with pytest.raises((RuntimeError, ValueError)):
        bot.require_signal_calendar('IM', known_signal(contract))


@pytest.mark.parametrize('contract', TARGETS)
def test_pending_target_with_alternate_numeric_grammar_is_rejected_before_write(tmp_path, contract):
    record = seed._record()
    store, record = seed._persist(tmp_path/'ledger', record)
    anchors = state.anchors_from_record(record)
    signals = {
        'IC': seed._signal('IC', anchors['IC']),
        'IM': seed._signal('IM', anchors['IM'], momentum=True,
                           extra={'momentum_put_target_contract': contract}),
    }
    assert signals['IM']['v14_ordinary_put_pending']['legs']['momentum']['new_contract'] == contract
    before = {p.name: p.read_bytes() for p in store.journal_dir.glob('*.json')}
    head = store.latest_path.read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        store.append_confirmed_signals(record, signals)
    assert store.latest_path.read_bytes() == head
    assert {p.name: p.read_bytes() for p in store.journal_dir.glob('*.json')} == before


def test_covered_target_still_roundtrips_through_real_state_store(tmp_path):
    record = seed._record()
    store, record = seed._persist(tmp_path/'ledger', record)
    anchors = state.anchors_from_record(record)
    signals = {'IC': seed._signal('IC', anchors['IC']),
               'IM': seed._signal('IM', anchors['IM'], momentum=True)}
    result = store.append_confirmed_signals(record, signals)
    reopened = state.StateStore(store.root).load_latest()
    assert reopened['digest'] == result['digest']
    assert reopened['products']['IM']['v14_ordinary_put_pending']['legs']['momentum']['new_contract'] == 'MO2612-P-8200'


@pytest.mark.parametrize('value', TARGETS)
def test_unadopted_far_diagnostic_remains_ignored(value):
    signal = known_signal('MO2612-P-8200')
    signal['candidate_diagnostics'] = {'unselected_contract': value}
    signal['next_core'] = 'IM2703'
    bot.require_signal_calendar('IM', signal)


@pytest.mark.parametrize('start, expected', [
    (date(2026, 1, 1), date(2026, 1, 5)),
    (date(2026, 2, 15), date(2026, 2, 24)),
    (date(2026, 6, 19), date(2026, 6, 22)),
    (date(2026, 10, 1), date(2026, 10, 8)),
])
def test_strict_roll_follows_actual_loaded_holidays(start, expected):
    assert bot._roll_forward_exchange_day(start, require_official=True) == expected
    assert bot._is_exchange_trading_day(expected)


@pytest.mark.parametrize('clock, expected', [
    (datetime(2026, 6, 19, 18, tzinfo=bot.BEIJING), date(2026, 6, 18)),
    (datetime(2026, 10, 7, 18, tzinfo=bot.BEIJING), date(2026, 9, 30)),
    (datetime(2026, 10, 8, 14, tzinfo=bot.BEIJING), date(2026, 9, 30)),
    (datetime(2026, 10, 8, 15, 1, tzinfo=bot.BEIJING), date(2026, 10, 8)),
])
def test_completed_day_is_causal_across_real_holiday_clock(clock, expected):
    assert bot._latest_completed_exchange_day(clock) == expected
