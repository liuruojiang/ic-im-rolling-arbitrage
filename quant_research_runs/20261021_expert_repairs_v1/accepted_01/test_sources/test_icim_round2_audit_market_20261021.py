"""Independent round-two market audit. Official fixture bytes stay immutable.

Only timestamps/ASCII identity syntax are independently corrupted; no invented
fully consistent authoritative identity or unsupported A cost migration is used.
"""
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import pandas as pd
import poe_ic_im_mainline_v1_4_bot as bot
import test_ic_im_put_execution_repairs_20261016 as held_fixture

FIXTURE = Path(__file__).parent / 'tests/fixtures/icim_external_review_round2_20261021/sse_december_identity.json'
DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 14, 15, tzinfo=bot.BEIJING)
CONTRACT = '510500P2612M07500'
SECURITY = '10012099'


def payload():
    return json.loads(FIXTURE.read_bytes())


def wire(monkeypatch, body):
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **kw: deepcopy(body))
    return bot.fetch_sse_510500_chain('2612')


def signal_anchor(monkeypatch):
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    anchor = held_fixture.anchor('IC')
    anchor.update(v14_core_put_contract=CONTRACT, v14_core_put_security_id=SECURITY,
                  post_put_contract=CONTRACT, post_put_security_id=SECURITY)
    signal = held_fixture.signal('IC', DAY, close_confirmed=False,
                                 etf_price=7.8, future_last=7800)
    return signal, anchor


def test_baseline_real_58_rows_and_held_core_keep_same_security(monkeypatch):
    chain, stamp = wire(monkeypatch, payload())
    signal, anchor = signal_anchor(monkeypatch)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month: (chain, stamp))
    bot._v14_ic_core_overlay(signal, anchor)
    assert len(chain) == 58
    assert signal['iv_monitor_evidence']['security_id'] == SECURITY
    assert signal['iv_monitor_option_price'] == .75


@pytest.mark.parametrize('bad_date', [20261020, 20261022, 'invalid'])
def test_unusable_primary_date_uses_available_fresh_backup_for_held_core(monkeypatch, bad_date):
    good_chain, good_stamp = wire(monkeypatch, payload())
    fallback = Mock(return_value=(good_chain, dict(good_stamp, source='synthetic_fresh_backup')))
    body = payload()
    body['date'] = bad_date
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **kw: deepcopy(body))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', fallback)
    signal, anchor = signal_anchor(monkeypatch)
    bot._v14_ic_core_overlay(signal, anchor)
    fallback.assert_called_once_with('2612')
    assert signal['iv_monitor_evidence']['source'] == 'synthetic_fresh_backup'


@pytest.mark.parametrize('consumer', ['core', 'short'])
@pytest.mark.parametrize('bad_time', [235959, 246001, 'invalid', None])
def test_future_or_invalid_primary_time_cannot_be_consumed(monkeypatch, consumer, bad_time):
    body = payload()
    body['time'] = bad_time
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **kw: deepcopy(body))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', Mock(side_effect=RuntimeError('synthetic unavailable backup')))
    signal, anchor = signal_anchor(monkeypatch)
    signal['next_trade_date'] = date(2026, 10, 22)
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *a: .02)
    with pytest.raises(RuntimeError):
        if consumer == 'core':
            bot._v14_ic_core_overlay(signal, anchor)
        else:
            result = bot._v14_ic_short_put_candidate(signal, roll_from_expiry=date(2026, 11, 25))
            assert result['tradable'] is True


@pytest.mark.parametrize('consumer', ['core', 'short', 'reset'])
def test_valid_real_sse_identity_survives_primary_transport_failure(monkeypatch, consumer):
    chain, stamp = wire(monkeypatch, payload())
    monkeypatch.setattr(bot, 'fetch_sse_510500_chain', Mock(side_effect=RuntimeError('synthetic primary transport failure')))
    fallback = Mock(return_value=(chain, stamp))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', fallback)
    signal, anchor = signal_anchor(monkeypatch)
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *a: .02)
    if consumer == 'core':
        bot._v14_ic_core_overlay(signal, anchor)
        assert signal['iv_monitor_evidence']['security_id'] == SECURITY
    elif consumer == 'short':
        result = bot._v14_ic_short_put_candidate(signal, roll_from_expiry=date(2026, 11, 25))
        assert result['security_id'] == SECURITY
        assert result['tradable'] is True
    else:
        monkeypatch.setattr(bot, 'fetch_sse_510500_expiries', lambda: ['202612'])
        result = bot.select_ic_put_for_reset(DAY, 7.8, 7800, .25)
        assert result['security_id'] == SECURITY
    fallback.assert_called_once()


@pytest.mark.parametrize('field', ['security', 'strike_digits'])
def test_non_ascii_identity_syntax_is_not_a_real_exchange_code(monkeypatch, field):
    body = payload()
    row = next(row for row in body['list'] if row[0] == CONTRACT)
    if field == 'security':
        row[5] = ''.join(chr(0xFF10 + int(c)) for c in row[5])
    else:
        row[0] = row[0][:-5] + ''.join(chr(0xFF10 + int(c)) for c in row[0][-5:])
    with pytest.raises(RuntimeError):
        wire(monkeypatch, body)


@pytest.mark.parametrize('field,value', [('security_id','10011470'), ('contract','510500P2612M07250'), ('strike',7.367)])
@pytest.mark.parametrize('consumer', ['identity', 'sizing', 'core'])
def test_bound_holding_rejects_independent_identity_corruption_before_pricing(monkeypatch, field, value, consumer):
    chain, stamp = wire(monkeypatch, payload())
    row = chain.loc[chain.contract.eq(CONTRACT)].iloc[0].copy()
    row[field] = value
    signal, anchor = signal_anchor(monkeypatch)
    pricing = Mock(side_effect=AssertionError('pricing reached before identity rejection'))
    monkeypatch.setattr(bot, '_implied_volatility', pricing)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    before = deepcopy(anchor)
    with pytest.raises(RuntimeError):
        if consumer == 'identity':
            bot._require_held_ic_put_identity(CONTRACT, SECURITY, row, DAY)
        elif consumer == 'sizing':
            bot._size_existing_ic_put(CONTRACT, row, DAY, 7.8, 7800, 5, .25, 0, .5, 0)
        else:
            broken = chain.copy()
            index = broken.index[broken.contract.eq(CONTRACT)][0]
            broken.loc[index, field] = value
            monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month: (broken, stamp))
            bot._v14_ic_core_overlay(signal, anchor)
    assert anchor == before
    pricing.assert_not_called()


@pytest.mark.parametrize('mutation', ['last_boolean', 'strike_boolean', 'fractional_total', 'overflow_last', 'dict_row'])
def test_additional_structural_corruptions_cannot_silently_reduce_universe(monkeypatch, mutation):
    body = payload()
    row = next(row for row in body['list'] if row[0] == CONTRACT)
    if mutation == 'last_boolean': row[1] = True
    elif mutation == 'strike_boolean': row[4] = True
    elif mutation == 'fractional_total': body['total'] = 58.0
    elif mutation == 'overflow_last': row[1] = '1e999'
    elif mutation == 'dict_row': body['list'][0] = dict(enumerate(body['list'][0]))
    with pytest.raises(RuntimeError):
        wire(monkeypatch, body)


@pytest.mark.parametrize('held_security', ['10011470', SECURITY])
def test_held_short_put_cannot_bypass_relisted_m_identity_gate(monkeypatch, held_security):
    chain, stamp = wire(monkeypatch, payload())
    signal, anchor = signal_anchor(monkeypatch)
    anchor.update(v14_route_state='short_put', v14_cycle_id='synthetic_stale_pre_adjustment_short',
                  v14_core_put_contract=None, v14_core_put_security_id=None, v14_core_put_qty=0,
                  v14_short_put_contract=CONTRACT, v14_short_put_security_id=held_security,
                  v14_short_put_qty_normalized=5., v14_short_put_entry_premium=1.5,
                  v14_short_put_expiry=date(2026, 12, 23),
                  v14_short_put_cost_basis='confirmed_dated_open',
                  v14_short_put_entry_price_day=date(2026, 7, 1),
                  post_put_qty=0, verified_core_put_qty=0)
    signal.update(close_confirmed=True, put_target_core_qty=0, core_put_target_delta=0.,
                  next_trade_date=date(2026, 10, 22))
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month: (chain, stamp))
    # Candidate availability is independent from proving the EXISTING held leg.
    monkeypatch.setattr(bot, '_v14_ic_short_put_candidate', lambda *a, **kw: {'tradable': False})
    raw = next(row for row in payload()['list'] if row[5] == held_security)
    closes = Mock(return_value=pd.Series([raw[1]], index=pd.to_datetime([DAY])))
    monkeypatch.setattr(bot, 'fetch_option_closes', closes)
    before = deepcopy(anchor)
    out = bot._apply_v14_live_policy('IC', signal)
    assert anchor == before
    if held_security == SECURITY:
        assert out['v14_short_put_mark'] == .75
    else:
        assert raw[0] == '510500P2612A07500'
        assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
        assert 'v14_short_put_mark' not in out


@pytest.mark.parametrize('consumer', ['reset', 'short'])
def test_new_candidate_never_publishes_unicode_security_id(monkeypatch, consumer):
    body = payload()
    row = next(row for row in body['list'] if row[0] == CONTRACT)
    row[5] = ''.join(chr(0xFF10 + int(c)) for c in row[5])
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **kw: deepcopy(body))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', Mock(side_effect=RuntimeError('synthetic unavailable backup')))
    signal, _ = signal_anchor(monkeypatch)
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *a: .02)
    monkeypatch.setattr(bot, 'fetch_sse_510500_expiries', lambda: ['202612'])
    with pytest.raises(RuntimeError):
        if consumer == 'reset':
            bot.select_ic_put_for_reset(DAY, 7.8, 7800, .25)
        else:
            result = bot._v14_ic_short_put_candidate(signal, roll_from_expiry=date(2026, 11, 25))
            assert result['tradable'] is True


@pytest.mark.parametrize('primary_fault', ['security_id', 'strike', 'last'])
def test_structural_primary_failure_preserves_fresh_backup_identity(monkeypatch, primary_fault):
    good, stamp = wire(monkeypatch, payload())
    body = payload()
    row = next(row for row in body['list'] if row[0] == CONTRACT)
    row[{'security_id': 5, 'strike': 4, 'last': 1}[primary_fault]] = {
        'security_id': '', 'strike': 7.367, 'last': 'nan'}[primary_fault]
    monkeypatch.setattr(bot, '_sse_quote_json', lambda *a, **kw: deepcopy(body))
    backup = Mock(return_value=(good, stamp))
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', backup)
    signal, anchor = signal_anchor(monkeypatch)
    bot._v14_ic_core_overlay(signal, anchor)
    assert signal['iv_monitor_evidence']['security_id'] == SECURITY
    assert signal['iv_monitor_option_price'] == .75
    backup.assert_called_once_with('2612')


@pytest.mark.parametrize('entry', ['selected', 'held_security', 'held_contract'])
def test_direct_identity_guards_require_ascii_even_without_source_parser(monkeypatch, entry):
    chain, _ = wire(monkeypatch, payload())
    row = chain.loc[chain.contract.eq(CONTRACT)].iloc[0].copy()
    fullwidth = ''.join(chr(0xFF10 + int(c)) for c in SECURITY)
    contract, security = CONTRACT, SECURITY
    if entry in ('selected', 'held_security'):
        security = row['security_id'] = fullwidth
    else:
        contract = row['contract'] = CONTRACT[:-5] + '０７５００'
    with pytest.raises(RuntimeError):
        if entry == 'selected':
            bot._ic_put_selected_security_id(row)
        else:
            bot._require_held_ic_put_identity(contract, security, row, DAY)


def test_real_sina_packets_reject_unicode_security_syntax(monkeypatch):
    raw = (FIXTURE.parents[1] / 'icim_external_review_20261021/sina_quotes_literal.bin').read_bytes().decode('gbk')
    ids = re.findall(r'var hq_str_CON_OP_([0-9]+)=', raw)
    malformed = ''.join(chr(0xFF10 + int(c)) for c in ids[0])
    raw = raw.replace('CON_OP_' + ids[0], 'CON_OP_' + malformed)
    ids[0] = malformed
    listing = 'var hq_str_OP_DOWN_5105002611="' + ','.join('CON_OP_' + x for x in ids) + '";'
    def response(body):
        return SimpleNamespace(content=body.encode('gbk'), headers={}, raise_for_status=lambda: None)
    monkeypatch.setattr(bot.requests, 'get', Mock(side_effect=[response(listing), response(raw)]))
    with pytest.raises(RuntimeError):
        bot.fetch_sina_510500_chain('2611')


@pytest.mark.parametrize('scenario', ['expiry', 'preloaded_mark', 'expiry_candidate_unavailable'])
def test_failed_short_identity_cannot_survive_through_lifecycle_fallback(monkeypatch, scenario):
    chain, stamp = wire(monkeypatch, payload())
    signal, anchor = signal_anchor(monkeypatch)
    anchor.update(v14_route_state='short_put', v14_cycle_id='synthetic_stale_pre_adjustment_short',
                  v14_core_put_contract=None, v14_core_put_security_id=None, v14_core_put_qty=0,
                  v14_short_put_contract=CONTRACT, v14_short_put_security_id='10011470',
                  v14_short_put_qty_normalized=5., v14_short_put_entry_premium=1.5,
                  v14_short_put_expiry=date(2026, 12, 23),
                  v14_short_put_cost_basis='confirmed_dated_open',
                  v14_short_put_entry_price_day=date(2026, 7, 1),
                  post_put_qty=0, verified_core_put_qty=0)
    signal.update(close_confirmed=True, put_target_core_qty=0, core_put_target_delta=0.,
                  next_trade_date=date(2026, 10, 22))
    if scenario.startswith('expiry'):
        # Synthetic future date, with real unchanged identity fixture: this is
        # lifecycle error injection, not a claim of a December market capture.
        expiry = date(2026, 12, 23)
        signal.update(market_date=expiry, next_trade_date=date(2026, 12, 24))
        stamp = dict(stamp, date='20261223', time='150000')
        monkeypatch.setattr(bot, '_now_beijing', lambda: datetime(2026, 12, 23, 15, 30, tzinfo=bot.BEIJING))
    else:
        signal['v14_short_put_mark'] = .01
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month: (chain, stamp))
    candidate = Mock(side_effect=RuntimeError('synthetic unrelated candidate transport failure')) if scenario == 'expiry_candidate_unavailable' else lambda *a, **kw: {'tradable': False}
    monkeypatch.setattr(bot, '_v14_ic_short_put_candidate', candidate)
    closes = Mock(return_value=pd.Series([.67], index=pd.to_datetime([signal['market_date']])))
    monkeypatch.setattr(bot, 'fetch_option_closes', closes)
    before = deepcopy(anchor)
    out = bot._apply_v14_live_policy('IC', signal)
    assert anchor == before
    assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert out['v14_roll_pending'] is False
    assert out['v14_settlement_pending'] is False
    assert 'v14_expiry_conditional_signal' not in out
    assert 'v14_short_put_mark' not in out
