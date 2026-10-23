"""Real SSE Date-only variability must not conceal any market-input conflict."""
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import pytest
import requests

import ic_im_signal_evidence_v1 as evidence

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / 'quant_research_runs/20261022_evidence_conflict_review_v1/delivery/time_probe_02'
URL = 'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12'
DAY = date(2026, 10, 22)
CLOCK = datetime.fromisoformat('2026-10-22T21:00:00+08:00')


def reply(body=b'{"date":20261022,"time":162906,"price":7.8}', *, headers=None,
          status=200, encoding='utf-8'):
    value = requests.Response()
    value.status_code, value.encoding, value._content = status, encoding, body
    value.headers.update(headers or {})
    value._content_consumed = True
    return value


def raw_pair():
    receipt = json.loads((PROBE / 'receipt.json').read_text(encoding='utf-8'))
    path = Path(receipt['manifest'])
    packet = json.loads(path.read_text(encoding='utf-8'))
    assert hashlib.sha256(evidence._dumps(packet['body'])).hexdigest() == packet['sha256']
    rows = packet['body']['raw']
    assert len(rows) == 2
    assert rows[0]['sha256'] == rows[1]['sha256']
    return [(row, evidence._verified_blob(PROBE / 'evidence', row)) for row in rows]


def test_real_sse_identical_market_inputs_with_different_http_dates_seal_and_replay(tmp_path):
    pair = raw_pair()
    assert pair[0][0]['headers']['Date'] != pair[1][0]['headers']['Date']
    signal = {'persistence_eligible': True}
    with evidence.capture_session(tmp_path, DAY, CLOCK, {'scope': 'real_Date_pair'}) as session:
        session.call('probe', {}, lambda: True)
        session.bind_signal(signal)
        for row, body in pair:
            session.raw_response(row['url'], evidence.decode(row['params']),
                                 reply(body, headers=row['headers'], status=row['status_code'], encoding=row['encoding']))
            assert not session.failures
    assert session.context['complete'] and signal['persistence_eligible']
    reader = evidence.load_session(tmp_path, DAY)
    observed = reader.http_response(URL, evidence.decode(pair[0][0]['params']))
    assert observed['body'] == pair[0][1] == pair[1][1]
    assert observed['matching_recordings'] == 2
    assert observed['headers'] == pair[-1][0]['headers']
    assert [row['headers'] for row in reader.context['raw']] == [r['headers'] for r, _ in pair]
    assert reader.missing_inputs == []


@pytest.mark.parametrize('change', ['price', 'market_time', 'market_date', 'status', 'encoding',
                                   'Last-Modified', 'ETag', 'Content-Type'])
def test_date_exemption_preserves_all_market_and_representation_conflicts(tmp_path, change):
    original = {'Date': 'Thu, 22 Oct 2026 13:00:00 GMT',
                'Last-Modified': 'Thu, 22 Oct 2026 07:00:00 GMT', 'ETag': 'a', 'Content-Type': 'application/json'}
    later = {**original, 'Date': 'Thu, 22 Oct 2026 13:00:01 GMT'}
    options = {'headers': later}
    if change in {'Last-Modified', 'ETag', 'Content-Type'}:
        later[change] = 'changed'
    elif change in {'status', 'encoding'}:
        options[change] = 503 if change == 'status' else 'gbk'
    else:
        values = {'price': b'{"date":20261022,"time":162906,"price":7.9}',
                  'market_time': b'{"date":20261022,"time":162907,"price":7.8}',
                  'market_date': b'{"date":20261023,"time":162906,"price":7.8}'}
        options['body'] = values[change]
    signal = {'persistence_eligible': True}
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('probe', {}, lambda: True)
        session.bind_signal(signal)
        session.raw_response(URL, None, reply(headers=original))
        session.raw_response(URL, None, reply(**options))
        assert any(f['role'] == 'raw_http_conflict' for f in session.failures)
    assert not session.context['complete'] and not signal['persistence_eligible']
    with pytest.raises(evidence.EvidenceUnavailable, match='incomplete'):
        evidence.load_session(tmp_path, DAY)


@pytest.mark.parametrize('url', [
    'https://example.invalid/tstyle/510500_12',
    'http://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12',
    'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_13',
    'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_00',
    'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12/extra',
    'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12?different=1',
    'https://yunhq.sse.com.cn:32042/v1/sho/list/other/510500_12'])
def test_other_endpoints_keep_strict_date_identity(tmp_path, url):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('probe', {}, lambda: True)
        for value in ['Thu, 22 Oct 2026 13:00:00 GMT', 'Thu, 22 Oct 2026 13:00:01 GMT']:
            session.raw_response(url, None, reply(headers={'Date': value}))
    assert not session.context['complete']


def test_previous_date_conflict_archive_is_not_retroactively_promoted():
    with pytest.raises(evidence.EvidenceUnavailable, match='incomplete'):
        evidence.load_session(PROBE / 'evidence', DAY)


def test_reader_rejects_market_conflict_even_if_forged_manifest_claims_complete(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('probe', {}, lambda: True)
        session.raw_response(URL, None, reply(headers={'Date': 'a'}))
        session.raw_response(URL, None, reply(b'changed market data', headers={'Date': 'b'}))
    packet = json.loads(session.manifest_path.read_text(encoding='utf-8'))
    packet['body'].update(complete=True, failures=[])
    packet['sha256'] = hashlib.sha256(evidence._dumps(packet['body'])).hexdigest()
    session.manifest_path.write_bytes(evidence._dumps(packet))
    with pytest.raises(evidence.EvidenceUnavailable, match='ambiguous'):
        evidence.load_session(tmp_path, DAY)
