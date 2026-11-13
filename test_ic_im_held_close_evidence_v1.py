"""Narrow held-close roles preserve raw provenance and full-package gates."""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_held_put_close_v1 as close
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

FIXTURE = Path(__file__).resolve().parent / 'quant_research_runs/20261113_ic_held_close_v1/fixtures'
DAY = date(2026, 11, 13)
OBSERVED = datetime.fromisoformat('2026-11-13T20:41:00+08:00')
CLOSED = datetime.fromisoformat('2026-11-13T20:42:00+08:00')


def actual_proof():
    return dict(revision=close.REVISION,
        anchor=dict(last_verified_day=date(2026, 11, 12), v14_route_state='future',
                    v14_core_put_contract='510500P2612M06500', v14_core_put_security_id='10012149',
                    v14_core_put_qty=15),
        signal_day=DAY, expected_previous_day=date(2026, 11, 12), current_ledger_day=date(2026, 11, 12),
        expected_expiry=date(2026, 12, 23), observed_at=OBSERVED, monthly_reset=False,
        detail_raw=(FIXTURE/'detail.bin').read_bytes(), identity_raw=(FIXTURE/'identity.bin').read_bytes(),
        sina_daily_raw=(FIXTURE/'sina_daily.bin').read_bytes(),
        eastmoney_daily_raw=(FIXTURE/'eastmoney_daily.bin').read_bytes(),
        official_master=json.loads((FIXTURE/'official_master.json').read_text(encoding='utf-8')))


def value_for(proof):
    checked = close.validate_held_put_close(**{k:v for k,v in proof.items() if k != 'revision'})
    frame = pd.DataFrame([checked['row']])
    frame.attrs['held_close_evidence'] = checked['evidence']
    stamp = dict(date=DAY.isoformat(), time=None, source=checked['evidence']['source'],
                 quote_kind='daily_close', observed_at=proof['observed_at'].isoformat())
    return frame, stamp, deepcopy(proof)


def strategy_for(proof):
    return SimpleNamespace(BEIJING=bot.BEIJING, _now_beijing=lambda: CLOSED,
                           _roll_forward_exchange_day=bot._roll_forward_exchange_day,
                           _fourth_wednesday=bot._fourth_wednesday,
                           LIVE_CONTINUATION_ANCHOR={'IC':deepcopy(proof['anchor'])})


REQUESTS = {
    'detail_raw': ('https://hq.sinajs.cn/', {'list':'CON_OP_10012149'}),
    'identity_raw': ('https://hq.sinajs.cn/list=CON_SO_10012149', None),
    'sina_daily_raw': ('https://stock.finance.sina.com.cn/futures/api/jsonp_v2.php//StockOptionDaylineService.getSymbolInfo',
                       {'symbol':'10012149'}),
    'eastmoney_daily_raw': ('https://push2his.eastmoney.com/api/qt/stock/kline/get',
                            {'secid':'10.10012149','klt':'101','fqt':'0',
                             'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57'}),
}


def capture_actual(tmp_path, monkeypatch, mutate_raw=None):
    proof = actual_proof()
    value = value_for(proof)
    strategy = strategy_for(proof)
    key = adapter.held_close_key('510500P2612M06500', '10012149', DAY, proof['anchor'])
    monkeypatch.setattr(evidence, '_now', lambda: datetime.fromisoformat('2026-11-13T20:40:00+08:00'))
    metadata = dict(product='IC', input_revision=adapter.INPUT_REVISION, original_close=True)
    with evidence.capture_session(tmp_path, DAY, datetime.fromisoformat('2026-11-13T20:39:00+08:00'), metadata) as session:
        for kind, (url, params) in REQUESTS.items():
            response = requests.Response()
            response.status_code, response._content = 200, proof[kind]
            session.raw_response(url, params, response)
        if mutate_raw is not None:
            mutate_raw(session.raw)
        checked = adapter.validate_held_close(strategy, value, key, DAY, CLOSED)
        session.call('ic.held_close', key, lambda: checked)
        monkeypatch.setattr(evidence, '_now', lambda: CLOSED)
    reader = evidence.load_session(tmp_path, DAY, metadata_filter={'product':'IC'})
    return reader, strategy, key, value


def test_actual_raw_capture_replay_and_midnight_detail_stays_non_iv(tmp_path, monkeypatch):
    reader, strategy, key, value = capture_actual(tmp_path, monkeypatch)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('replay attempted network'))
    with evidence.replay_session(reader):
        result = adapter._recorded_held_close(strategy, '10012149', DAY, '510500P2612M06500')
    assert result[0].iloc[0]['last'] == .2703
    assert result[1]['time'] is None and result[1]['quote_kind'] == 'daily_close'
    assert result[0].attrs['held_close_evidence']['iv_monitor_eligible'] is False
    assert result[0].attrs['held_close_proof']['detail_raw'] == actual_proof()['detail_raw']
    assert reader.missing_inputs == []


def test_narrow_role_never_replaces_complete_chain_or_expiry_package(tmp_path, monkeypatch):
    reader, _, _, _ = capture_actual(tmp_path, monkeypatch)
    with pytest.raises(evidence.EvidenceUnavailable, match='missing original evidence roles'):
        adapter._require_original_reader(reader, 'IC', DAY, bot.BEIJING)
    assert {'ic.chain', 'ic.expiry'} <= adapter._original_required_roles('IC', DAY)


@pytest.mark.parametrize('field,bad', [
    ('url', 'https://example.com/forged'),
    ('params', evidence.encode({'symbol':'10012148'})),
    ('status_code', 403),
    ('fetched_at','2026-11-12T20:40:00+08:00'),
    ('fetched_at','2026-11-13T15:29:59+08:00'),
    ('fetched_at','2026-11-13T20:41:01+08:00'),
    ('sha256','0'*64),
])
def test_identical_price_body_with_wrong_request_or_acquisition_is_rejected(tmp_path, monkeypatch, field, bad):
    def mutate(raw):
        raw[2][field] = bad
    with pytest.raises(evidence.EvidenceUnavailable):
        capture_actual(tmp_path, monkeypatch, mutate)


@pytest.mark.parametrize('kind', ['row_price','row_identity','stamp_time','stamp_day','wrong_key','wrong_anchor','wrong_expiry'])
def test_recorded_display_or_scope_drift_never_overrides_raw_proof(tmp_path, monkeypatch, kind):
    reader, strategy, key, value = capture_actual(tmp_path, monkeypatch)
    value = deepcopy(value)
    if kind == 'row_price': value[0].loc[0,'last'] = .3
    elif kind == 'row_identity': value[0].loc[0,'security_id'] = '10012148'
    elif kind == 'stamp_time': value[1]['time'] = '150000'
    elif kind == 'stamp_day': value[1]['date'] = '2026-11-12'
    elif kind == 'wrong_key': key['quantity'] = 14
    elif kind == 'wrong_anchor': value[2]['anchor']['last_verified_day'] = date(2026,11,10)
    else: value[2]['expected_expiry'] = date(2026,12,24)
    with pytest.raises(evidence.EvidenceUnavailable):
        adapter.validate_held_close(strategy, value, key, DAY, CLOSED, reader=reader)


def test_replay_current_ledger_quantity_must_match_recorded_anchor(tmp_path, monkeypatch):
    reader, strategy, _, _ = capture_actual(tmp_path, monkeypatch)
    strategy.LIVE_CONTINUATION_ANCHOR['IC']['v14_core_put_qty'] = 14
    with evidence.replay_session(reader), pytest.raises(evidence.EvidenceUnavailable, match='逐日账本锚点'):
        adapter._recorded_held_close(strategy, '10012149', DAY)


def test_missing_archived_http_body_is_not_an_online_fallback(tmp_path, monkeypatch):
    reader, strategy, key, value = capture_actual(tmp_path, monkeypatch)
    target = reader.root/'blobs'/(reader.context['raw'][2]['sha256']+'.bin')
    target.write_bytes(b'corrupt')
    with pytest.raises(evidence.EvidenceUnavailable):
        adapter.validate_held_close(strategy, value, key, DAY, CLOSED, reader=reader)


def test_later_replay_uses_recorded_observation_without_relabeling_it(tmp_path, monkeypatch):
    reader, strategy, key, value = capture_actual(tmp_path, monkeypatch)
    strategy._now_beijing = lambda: datetime.fromisoformat('2026-11-14T08:00:00+08:00')
    with evidence.replay_session(reader):
        result = adapter._recorded_held_close(strategy, '10012149', DAY)
    assert result[1]['observed_at'] == OBSERVED.isoformat()
    assert result[2]['observed_at'] == OBSERVED


def test_legacy_day_does_not_acquire_narrow_close_policy():
    with pytest.raises(evidence.EvidenceUnavailable):
        adapter.validate_held_close(None, value_for(actual_proof()), {}, date(2026,11,12), CLOSED)


def test_public_getter_is_recorded_once_and_replayed_without_provider_calls(tmp_path, monkeypatch):
    proof = actual_proof()
    strategy = strategy_for(proof)
    calls = []
    def getter(contract, security_id, day, anchor):
        calls.append((contract,security_id,day))
        session = evidence.current_session.get()
        for kind, (url, params) in REQUESTS.items():
            response = requests.Response()
            response.status_code, response._content = 200, proof[kind]
            session.raw_response(url, params, response)
        return value_for(proof)
    strategy._fetch_held_ic_put_daily_close_for_day = getter
    adapter._keyed(strategy, strategy, '_fetch_held_ic_put_daily_close_for_day', 'ic.held_close', adapter.held_close_key)
    monkeypatch.setattr(evidence, '_now', lambda: datetime.fromisoformat('2026-11-13T20:40:00+08:00'))
    args = ('510500P2612M06500','10012149',DAY,proof['anchor'])
    with evidence.capture_session(tmp_path, DAY, datetime.fromisoformat('2026-11-13T20:39:00+08:00'),
                                  dict(product='IC',input_revision=adapter.INPUT_REVISION,original_close=True)):
        result = strategy._fetch_held_ic_put_daily_close_for_day(*args)
        assert result[0].iloc[0]['last'] == .2703
        monkeypatch.setattr(evidence, '_now', lambda: CLOSED)
    reader = evidence.load_session(tmp_path,DAY,metadata_filter={'product':'IC'})
    with evidence.replay_session(reader):
        repeated = strategy._fetch_held_ic_put_daily_close_for_day(*args)
    assert len(calls) == 1
    assert repeated[1] == result[1]


@pytest.mark.parametrize('kind,changes', [
    ('detail_raw', {'list':'CON_OP_10012148'}),
    ('identity_raw', {'list':'CON_SO_10012148'}),
    ('eastmoney_daily_raw', {'secid':'10.10012148','klt':'101','fqt':'0'}),
    ('eastmoney_daily_raw', {'secid':'10.10012149','klt':'101','fqt':'1'}),
    ('eastmoney_daily_raw', {'secid':'10.10012149','klt':'1','fqt':'0'}),
])
def test_each_provider_request_must_match_exact_security_and_daily_unadjusted_mode(tmp_path, monkeypatch, kind, changes):
    index = list(REQUESTS).index(kind)
    def mutate(raw):
        raw[index]['url'] = 'https://hq.sinajs.cn/' if kind == 'identity_raw' else raw[index]['url']
        raw[index]['params'] = evidence.encode(changes)
    with pytest.raises(evidence.EvidenceUnavailable):
        capture_actual(tmp_path,monkeypatch,mutate)


@pytest.mark.parametrize('change', ['missing','wrong','duplicated','fields1'])
def test_eastmoney_column_semantics_cannot_relabel_another_field_as_close(tmp_path, monkeypatch, change):
    def mutate(raw):
        params = deepcopy(REQUESTS['eastmoney_daily_raw'][1])
        if change == 'missing': params.pop('fields2')
        elif change == 'wrong': params['fields2'] = 'f51,f52,f54,f53,f55,f56,f57'
        elif change == 'fields1': params['fields1'] = 'f1,f2'
        else: raw[3]['url'] += '?fields2=f51,f52,f53,f54,f55,f56,f57'
        raw[3]['params'] = evidence.encode(params)
    with pytest.raises(evidence.EvidenceUnavailable):
        capture_actual(tmp_path,monkeypatch,mutate)
