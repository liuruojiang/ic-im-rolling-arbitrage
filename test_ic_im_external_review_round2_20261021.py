"""Second-round failures: actual official identities; synthetic stale anchors/mutations."""
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import importlib
import json
from unittest.mock import Mock

import pandas as pd
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import test_ic_im_put_execution_repairs_20261016 as fixture

FIXTURE = Path(__file__).parent / 'tests/fixtures/icim_external_review_round2_20261021'
DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 14, 15, tzinfo=bot.BEIJING)
M = '510500P2612M07500'
A = '510500P2612A07500'

def payload():
    return json.loads((FIXTURE / 'sse_december_identity.json').read_bytes())

def wire(monkeypatch, body=None):
    body = body or payload()
    def request(path, params):
        result = deepcopy(body)
        # Emulate the source's actual select projection, not an invented schema.
        if 'code' not in params['select'].split(','):
            result['list'] = [row[:5] for row in result['list']]
        return result
    monkeypatch.setattr(bot, '_sse_quote_json', request)
    return bot.fetch_sse_510500_chain('2612')

def core(monkeypatch, chain, stamp, security='10011470'):
    anchor = fixture.anchor('IC')
    anchor.update(v14_core_put_contract=M, v14_core_put_security_id=security,
                  verified_core_put_delta=.25)
    signal = fixture.signal('IC', DAY, close_confirmed=False,
                            core_put_target_delta=.25, etf_price=7.8)
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', lambda month: (chain, stamp))
    return signal, anchor

def test_real_sse_chain_retains_security_id(monkeypatch):
    chain, _ = wire(monkeypatch)
    row = chain[chain.contract.eq(M)].iloc[0]
    assert row['security_id'] == '10012099'
    assert chain.loc[chain.contract.eq(A), 'security_id'].item() == '10011470'
    assert chain.loc[chain.contract.eq(A), 'strike'].item() == 7.367

def test_real_relisted_m_cannot_mark_synthetic_pre_adjustment_holding(monkeypatch):
    chain, stamp = wire(monkeypatch)
    signal, anchor = core(monkeypatch, chain, stamp)
    before = deepcopy(anchor)
    with pytest.raises(RuntimeError, match='证券|identity'):
        bot._v14_ic_core_overlay(signal, anchor)
    assert anchor == before
    assert 'iv_monitor_option_price' not in signal

@pytest.mark.parametrize('security', [None, '', '123', '10012099.0'])
def test_current_core_missing_or_malformed_security_proof_is_rejected(monkeypatch, security):
    chain, stamp = wire(monkeypatch)
    if security is None:
        chain = chain.drop(columns='security_id', errors='ignore')
    else:
        chain['security_id'] = security
    signal, anchor = core(monkeypatch, chain, stamp, '10012099')
    with pytest.raises(RuntimeError):
        bot._v14_ic_core_overlay(signal, anchor)

def test_real_standard_core_same_security_continues(monkeypatch):
    chain, stamp = wire(monkeypatch)
    signal, anchor = core(monkeypatch, chain, stamp, '10012099')
    bot._v14_ic_core_overlay(signal, anchor)
    assert signal['iv_monitor_option_price'] == chain.loc[chain.contract.eq(M), 'last'].item()
    assert signal['iv_monitor_evidence']['security_id'] == '10012099'
    assert signal['v14_core_put_qty'] == anchor['v14_core_put_qty']

@pytest.mark.parametrize('mutation', ['duplicate_id', 'duplicate_contract', 'missing_id',
    'wrong_underlying', 'wrong_year', 'standard_wrong_strike', 'truncated', 'bad_total',
    'nan_strike', 'nan_last', 'negative_last'])
def test_sse_identity_and_completeness_mutations_fail_closed(monkeypatch, mutation):
    body = payload()
    row = next(r for r in body['list'] if r[0] == M)
    if mutation == 'duplicate_id': body['list'][0][5] = row[5]
    elif mutation == 'duplicate_contract': body['list'][0][0] = M
    elif mutation == 'missing_id': row[5] = ''
    elif mutation == 'wrong_underlying': row[0] = row[0].replace('510500', '510300')
    elif mutation == 'wrong_year': row[0] = row[0].replace('2612', '2712')
    elif mutation == 'standard_wrong_strike': row[4] = 7.367
    elif mutation == 'truncated': body['list'].pop()
    elif mutation == 'bad_total': body['total'] = 59
    elif mutation == 'nan_strike': row[4] = 'nan'
    elif mutation == 'nan_last': row[1] = 'nan'
    elif mutation == 'negative_last': row[1] = -.1
    with pytest.raises(RuntimeError):
        wire(monkeypatch, body)

def test_overlay_identity_failure_cannot_admit_new_plan(monkeypatch):
    chain, stamp = wire(monkeypatch)
    signal, anchor = core(monkeypatch, chain, stamp)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    monkeypatch.setattr(bot, '_v14_ic_short_put_candidate', lambda *a, **k: {'tradable': False})
    before = deepcopy(anchor)
    out = bot._apply_v14_live_policy('IC', signal)
    assert out['v14_action'] == 'HOLD_FAIL_CLOSED'
    assert out['v14_candidate_data_status'].startswith('unavailable:')
    assert anchor == before
    bot._v14_schedule_ordinary_put('IC', out, anchor, None)
    assert not out.get('v14_ordinary_put_pending')

def test_sizing_current_shared_put_cannot_use_relisted_identity(monkeypatch):
    chain, _ = wire(monkeypatch)
    row = chain[chain.contract.eq(M)].iloc[0]
    anchor = fixture.anchor('IC')
    anchor.update(post_put_contract=M, post_put_security_id='10011470')
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, 'IC', anchor)
    pricing = Mock(return_value=.3)
    monkeypatch.setattr(bot, '_gov10y_for_day', lambda *a: .02)
    monkeypatch.setattr(bot, '_implied_volatility', pricing)
    with pytest.raises(RuntimeError, match='证券|identity'):
        bot._size_existing_ic_put(M, row, DAY, 7.8, 7800, 5, .25, 0, .5, 0)
    pricing.assert_not_called()

def test_official_adjustment_resolves_unchanged_security_without_fabricated_suffix():
    identity = importlib.import_module('ic_im_sse_contract_identity_v1')
    events = identity.parse_adjustment_events(json.loads((FIXTURE / 'sse_adjustment_20260715.json').read_bytes()),
                                               effective_date=date(2026, 7, 15))
    resolved = identity.resolve_adjusted_holding(events, payload(), '10011470', M, DAY)
    assert resolved['contract'] == A
    assert resolved['security_id'] == '10011470'
    assert resolved['strike'] == 7.367
    assert resolved['contract_unit'] == 10180
    assert resolved['previous_unit'] == 10000
    assert resolved['effective_date'] == date(2026, 7, 15)
    assert resolved['expiry'] == date(2026, 12, 23)
    assert resolved['migration_required'] is True
    assert resolved['engine_supported'] is False

@pytest.mark.parametrize('mutation', ['wrong_id', 'wrong_day', 'expired', 'future_effective', 'wrong_strike', 'wrong_code'])
def test_adjusted_resolver_rejects_unproved_alias_or_date(mutation):
    identity = importlib.import_module('ic_im_sse_contract_identity_v1')
    events = identity.parse_adjustment_events(json.loads((FIXTURE / 'sse_adjustment_20260715.json').read_bytes()),
                                               effective_date=date(2026, 7, 15))
    body, security, held, day = payload(), '10011470', M, DAY
    if mutation == 'wrong_id': security = '10012099'
    elif mutation == 'wrong_day': body['date'] = 20261020
    elif mutation == 'expired': day = date(2026, 12, 24); body['date'] = 20261224
    elif mutation == 'future_effective': day = date(2026, 7, 14); body['date'] = 20260714
    elif mutation == 'wrong_strike': next(r for r in body['list'] if r[5] == security)[4] = 7.5
    elif mutation == 'wrong_code': held = '510500P2612M07367'
    with pytest.raises(RuntimeError):
        identity.resolve_adjusted_holding(events, body, security, held, day)


@pytest.mark.parametrize('field,value', [
    ('SECURITY_ID', '123'), ('ORG_CONTRACT_ID', '510500P2612M07367'),
    ('CONTRACT_ID', '510500P2612A07367'), ('UNDERLYING_SECURITY_ID', '510300'),
    ('ORG_EXERCISE_PRICE', '7.367'), ('EXERCISE_PRICE', 'nan'),
    ('ORG_CONTRACT_UNIT', '10180'), ('CONTRACT_UNIT', '0'),
    ('CONTRACT_UNIT', 'nan'), ('CONTRACT_UNIT', '10180.5'),
    ('ADJUST_DATE', '20260716'), ('EXPIRE_DATE', '20261201'),
    ('EXPIRE_MONTH', '202611'), ('CALL_OR_PUT', '认购')])
def test_official_adjustment_schema_rejects_corrupt_identity_or_unit(field, value):
    identity = importlib.import_module('ic_im_sse_contract_identity_v1')
    body = json.loads((FIXTURE / 'sse_adjustment_20260715.json').read_bytes())
    next(r for r in body['result'] if r['SECURITY_ID'] == '10011470')[field] = value
    with pytest.raises(RuntimeError):
        identity.parse_adjustment_events(body, effective_date=date(2026, 7, 15))


def test_all_real_live_adjusted_puts_match_current_official_master():
    identity = importlib.import_module('ic_im_sse_contract_identity_v1')
    events = identity.parse_adjustment_events(json.loads((FIXTURE / 'sse_adjustment_20260715.json').read_bytes()),
                                               effective_date=date(2026, 7, 15))
    current = json.loads((FIXTURE / 'sse_contract_master.json').read_bytes())['result']
    adjusted = [row for row in payload()['list'] if row[0].startswith('510500P2612A')]
    assert len(adjusted) == 14
    for row in adjusted:
        result = identity.resolve_adjusted_holding(events, payload(), row[5], row[0], DAY)
        official = [item for item in current if item['SECURITY_ID'] == row[5]]
        assert len(official) == 1
        master = official[0]
        assert result['contract'] == master['CONTRACT_ID']
        assert result['strike'] == float(master['EXERCISE_PRICE'])
        assert result['contract_unit'] == int(master['CONTRACT_UNIT'])
        assert result['expiry'].strftime('%Y%m%d') == master['EXPIRE_DATE']
        # Master carries its actual data timestamp; no claim it was updated today.
        assert master['TIMESAVE'] == '2026-10-20'
        assert result['engine_supported'] is False
