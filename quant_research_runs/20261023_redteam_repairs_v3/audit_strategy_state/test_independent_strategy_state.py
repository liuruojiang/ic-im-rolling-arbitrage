"""Independent post-repair audit by the evidence implementer, offline only."""
from copy import deepcopy
from datetime import date

import pandas as pd
import pytest
import requests

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_round3_policy_repairs_20261021 import short_anchor, signal


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: pytest.fail('independent audit network forbidden'))


def confirmed_pair(tmp_path, monkeypatch):
    """Public builder + actual StateStore; synthetic holding, saved real SSE identity."""
    import test_icim_round2_audit_lifecycle_20261021 as fixture
    for module in [fixture.state, bot, bot.fear_grid]:
        monkeypatch.setattr(module, 'datetime', fixture.SyntheticCloseClock)
    store, head = fixture.recent_store(tmp_path / 'ledger')
    anchors = fixture.state.anchors_from_record(head)
    held = anchors['IC']
    fixture.ledger._empty_core(held, 'IC')
    held.update(v14_route_state='short_put', v14_cycle_id='independent-held-cycle',
                v14_short_put_contract=fixture.real.M, v14_short_put_security_id='10012099',
                v14_short_put_qty_normalized=5., v14_short_put_entry_premium=.4,
                v14_short_put_expiry=date(2026, 12, 23),
                v14_short_put_cost_basis='confirmed_dated_open',
                v14_short_put_entry_price_day=fixture.PREVIOUS,
                v14_last_event_id='IC:2026-10-19:2026-10-20:ENTER_SHORT_PUT:' + fixture.real.M,
                verified_momentum_put_delta=0., verified_momentum_put_qty=0)
    head['products']['IC'] = fixture.state._jsonable(held)
    head['digest'] = fixture.state._digest(head)
    fixture.state._validate_record(head)
    store._atomic_write(store.journal_dir / '000000-2026-10-20.json', head)
    store._atomic_write(store.latest_path, head)
    head = store.load_latest()
    anchors = fixture.state.anchors_from_record(head)
    chain, stamp = fixture.real.wire(monkeypatch)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', anchors)
    fixture.builder_inputs(monkeypatch, anchors['IC'], chain, stamp)
    monkeypatch.setattr(bot, 'fetch_sse_510500_quote', lambda: dict(
        date='20261021', time='150000', last=7.8, prev_close=7.8))
    live = bot.live_proxy('IC', fixture.CLOCK)
    live.update(momentum_120=.1, valuation_tier=1)
    monkeypatch.setattr(bot, 'live_proxy', lambda *a: deepcopy(live))
    monkeypatch.setattr(bot, 'fetch_option_closes', lambda *a: pd.Series(
        [.4], index=[pd.Timestamp(fixture.DAY)]))
    result = bot.build_live_trade_signal('IC', fixture.CLOCK, 'close')
    assert result['v14_action'] == 'HOLD', result.get('diagnostic_error')
    fixture.state.require_persistence_eligible(result, 'IC')
    other = fixture.close_evidence(fixture.ledger._signal('IM', anchors['IM'], fixture.DAY))
    other['v14_opening_events'] = []
    return fixture, store, head, {'IC': result, 'IM': other}


def test_valid_bound_current_mark_really_appends_and_reopens(tmp_path, monkeypatch):
    fixture, store, head, pair = confirmed_pair(tmp_path, monkeypatch)
    result = store.append_confirmed_signals(head, pair)
    reopened = fixture.state.StateStore(store.root).load_latest()
    assert reopened['sequence'] == head['sequence'] + 1
    assert reopened['digest'] == result['digest']


@pytest.mark.parametrize('bypass', ['pending', 'cost_basis', 'expiry', 'route', 'contract',
                                  'early_roll', 'opening_pending', 'before_effective_date',
                                  'fake_enter', 'fake_roll'])
def test_forged_signal_cannot_skip_durable_held_leg_mark_requirement(tmp_path, monkeypatch, bypass):
    fixture, store, head, pair = confirmed_pair(tmp_path, monkeypatch)
    current = pair['IC']
    current['v14_short_put_mark'] = None
    if bypass == 'pending':
        current.update(v14_action='WAIT_SHORT_PUT_ROLL', v14_roll_pending=True,
                       v14_roll_trigger_day=fixture.DAY)
    elif bypass == 'cost_basis':
        current.update(v14_short_put_cost_basis=None, v14_short_put_entry_price_day=None)
    elif bypass == 'expiry':
        current['v14_short_put_expiry'] = fixture.DAY
    elif bypass == 'route':
        current.update(policy.default_extension('IC'))
    elif bypass == 'contract':
        current['v14_short_put_contract'] = None
    elif bypass == 'early_roll':
        current['v14_early_roll_used'] = True
    elif bypass == 'opening_pending':
        current.update(v14_short_put_open_pending={'forged': True},
                       v14_short_put_cost_basis='pending_dated_open')
    elif bypass in {'fake_enter', 'fake_roll'}:
        current['v14_action'] = 'ENTER_SHORT_PUT' if bypass == 'fake_enter' else 'ROLL_SHORT_PUT'
    else:
        current['market_date'] = date(2026, 8, 1)
    with pytest.raises((RuntimeError, KeyError, ValueError, TypeError)):
        store.append_confirmed_signals(head, pair)
    assert fixture.state.StateStore(store.root).load_latest()['digest'] == head['digest']


@pytest.mark.parametrize('mark', [None, 0, -0.1, float('nan'), float('inf'), -float('inf'), True, 'invalid'])
def test_invalid_mark_cannot_be_laundered_by_complete_flags(tmp_path, monkeypatch, mark):
    fixture, store, head, pair = confirmed_pair(tmp_path, monkeypatch)
    pair['IC'].update(v14_short_put_mark=mark, persistence_eligible=True, signal_complete=True)
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(head, pair)
    assert fixture.state.StateStore(store.root).load_latest()['digest'] == head['digest']


@pytest.mark.parametrize('field,change', [('v14_short_put_mark_day', 'missing'),
                                         ('v14_short_put_mark_contract', 'missing'),
                                         ('v14_short_put_mark_day', date(2026, 10, 20)),
                                         ('v14_short_put_mark_contract', '510500P2612M06000')])
def test_durable_append_rejects_missing_or_wrong_mark_binding(tmp_path, monkeypatch, field, change):
    fixture, store, head, pair = confirmed_pair(tmp_path, monkeypatch)
    if change == 'missing':
        pair['IC'].pop(field)
    else:
        pair['IC'][field] = change
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(head, pair)
    assert fixture.state.StateStore(store.root).load_latest()['digest'] == head['digest']


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('bad_entry', [None, 0, float('nan')])
def test_valid_mark_cannot_replace_missing_confirmed_cost(product, bad_entry):
    held = short_anchor(product)
    held['v14_short_put_entry_premium'] = bad_entry
    with pytest.raises(RuntimeError):
        policy.apply_policy(product, signal(product, v14_short_put_mark=.8), held, candidate=None)


@pytest.mark.parametrize('fault', ['lagging', 'duplicate', 'nan', 'unsorted'])
def test_required_date_failover_rejects_bad_primary_without_consuming_its_value(monkeypatch, fault):
    day = date(2026, 12, 1)
    if fault == 'lagging':
        primary = pd.Series([.1], index=[pd.Timestamp('2026-11-30')])
    elif fault == 'duplicate':
        primary = pd.Series([.1, .2], index=[pd.Timestamp(day), pd.Timestamp(day)])
    elif fault == 'nan':
        primary = pd.Series([float('nan')], index=[pd.Timestamp(day)])
    else:
        primary = pd.Series([.1, .2], index=[pd.Timestamp(day), pd.Timestamp('2026-11-30')])
    monkeypatch.setattr(bot, 'fetch_sina_option_closes', lambda sid: primary)
    monkeypatch.setattr(bot, 'fetch_eastmoney_option_closes', lambda sid: pd.Series([.9], index=[pd.Timestamp(day)]))
    result = bot.fetch_option_closes('10012099', day)
    assert result.loc[pd.Timestamp(day)] == .9
    assert result.attrs['source'] == 'Eastmoney'
    assert result.attrs['source_failures']


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_new_pre_opening_revision_entry_does_not_invent_prior_held_mark_requirement(product):
    """A new target is not an already held short; retain pre-10/16 date semantics."""
    import poe_ic_im_v1_4_state as state
    from test_ic_im_v1_4_policy import _candidate, _signal
    candidate = _candidate(product, iv=.99)
    source = _signal(product, market_date=date(2026, 10, 14),
                     next_trade_date=date(2026, 10, 15), momentum_120=.1,
                     valuation_tier=1, valuation_puts_per_full_core=1)
    result = policy.apply_policy(product, source, policy.default_extension(product), candidate=candidate)
    assert result['v14_action'] == 'ENTER_SHORT_PUT'
    assert result.get('v14_short_put_mark') is None
    state.require_persistence_eligible(result, product, check_grid=False)
