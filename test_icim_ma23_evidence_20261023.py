"""Independent evidence adversaries; offline, temporary archives only.

Mutation fixtures are deliberately synthetic. Real packets are read unchanged
only for the byte-integrity verification case; no production archive is edited.
"""
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path

import pytest
import requests

import ic_im_signal_evidence_v1 as evidence


DAY = date(2026, 10, 23)
CLOCK = datetime(2026, 10, 23, 9, tzinfo=timezone(timedelta(hours=8)))
URL = 'https://hq.sinajs.cn/etag.php'
ONE, TWO, THREE = 'P_OP_mo2612P7000', 'P_OP_mo2612P7100', 'P_OP_mo2612P7200'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request',
                        lambda *a, **k: pytest.fail('evidence audit attempted external HTTP'))


def response(body=b'original', status=200, encoding='utf-8', headers=None):
    result = requests.Response()
    result.status_code, result.encoding = status, encoding
    result._content, result._content_consumed = body, True
    result.headers.update(headers or {})
    return result


def capture(tmp_path, records):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {'scope': 'synthetic evidence adversary'}) as session:
        session.call('probe', {'day': DAY}, lambda: {'value': 1})
        for url, params, result in records:
            session.raw_response(url, params, result)
    return session


def rewrite_manifest(session, mutate):
    envelope = json.loads(session.manifest_path.read_text(encoding='utf-8'))
    mutate(envelope['body'])
    envelope['sha256'] = hashlib.sha256(evidence._dumps(envelope['body'])).hexdigest()
    session.manifest_path.write_bytes(evidence._dumps(envelope))


@pytest.mark.parametrize('field,value', [('status', None), ('status', True), ('status', 200.0),
                                       ('status', 99), ('status', 600), ('encoding', 42)])
def test_raw_http_metadata_must_be_valid_before_complete_loader_acceptance(tmp_path, field, value):
    """A normal capture API can currently seal malformed HTTP metadata.

    This is a format-consistency test, not a claim requests normally emits it.
    Typed-role-only replay otherwise never reaches http_response's validation.
    """
    packet = response(**{field: value})
    session = capture(tmp_path, [(URL, {'list': ONE}, packet)])
    # No forged SHA or mutation: the production recorder produced this archive.
    with pytest.raises(evidence.EvidenceUnavailable, match='invalid|status|encoding|incomplete'):
        evidence.load_session(tmp_path, DAY)


@pytest.mark.parametrize('status', [100, 200, 302, 404, 503, 599])
def test_valid_original_http_error_status_remains_recordable_for_fallback_audit(tmp_path, status):
    session = capture(tmp_path, [(URL, {'list': ONE}, response(status=status))])
    reader = evidence.load_session(tmp_path, DAY)
    assert reader.http_response(URL, {'list': ONE})['status_code'] == status
    assert session.context['complete']


@pytest.mark.parametrize('mutation', ['typed_blob', 'raw_blob', 'manifest_hash', 'total', 'role', 'fetched_at'])
def test_rehashed_structural_or_blob_corruption_is_rejected(tmp_path, mutation):
    session = capture(tmp_path, [(URL, {'list': ONE}, response())])
    if mutation in {'typed_blob', 'raw_blob'}:
        row = next(iter(session.context['entries'].values())) if mutation == 'typed_blob' else session.context['raw'][0]
        evidence._blob_path(session.root, row).write_bytes(b'deliberate mutation in temporary test archive')
    elif mutation == 'manifest_hash':
        envelope = json.loads(session.manifest_path.read_text(encoding='utf-8'))
        envelope['body']['metadata'] = evidence.encode({'changed': True})
        session.manifest_path.write_bytes(evidence._dumps(envelope))
    else:
        def mutate(body):
            if mutation == 'total':
                body['total_bytes'] += 1
            elif mutation == 'role':
                next(iter(body['entries'].values()))['role'] = 'wrong_role'
            else:
                body['raw'][0]['fetched_at'] = (datetime.fromisoformat(body['closed_at']) + timedelta(seconds=1)).isoformat()
        rewrite_manifest(session, mutate)
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.load_session(tmp_path, DAY)


def test_exact_request_retains_its_observation_over_different_batch_prefetch(tmp_path):
    """Different logical requests are not ambiguous when exact original exists."""
    exact = f'var hq_str_{ONE}="exact-original";'.encode()
    batch = f'var hq_str_{ONE}="other-prefetch";var hq_str_{TWO}="second";'.encode()
    session = capture(tmp_path, [(URL, {'list': ONE}, response(exact)),
                                 (URL, {'list': ONE + ',' + TWO}, response(batch))])
    assert session.context['complete']
    reader = evidence.load_session(tmp_path, DAY)
    assert reader.http_response(URL, {'list': ONE})['body'] == exact
    assert reader.http_response(URL, {'list': ONE + ',' + TWO})['body'] == batch


def test_missing_exact_request_requires_all_possible_projections_to_agree(tmp_path):
    batch_a = f'var hq_str_{ONE}="first";var hq_str_{TWO}="two";'.encode()
    batch_b = f'var hq_str_{ONE}="different";var hq_str_{THREE}="three";'.encode()
    capture(tmp_path, [(URL, {'list': ONE + ',' + TWO}, response(batch_a)),
                       (URL, {'list': ONE + ',' + THREE}, response(batch_b))])
    reader = evidence.load_session(tmp_path, DAY)
    with pytest.raises(evidence.EvidenceUnavailable, match='ambiguous'):
        reader.http_response(URL, {'list': ONE})


@pytest.mark.parametrize('parameters', [{'list': [ONE]}, {'list': ONE, 'different': True},
                                       {'list': THREE}, {'list': ONE + ',' + THREE}])
def test_reader_does_not_alias_parameter_types_or_fabricate_missing_batches(tmp_path, parameters):
    capture(tmp_path, [(URL, {'list': ONE + ',' + TWO}, response(
        f'var hq_str_{ONE}="one";var hq_str_{TWO}="two";'.encode()))])
    reader = evidence.load_session(tmp_path, DAY)
    with pytest.raises(evidence.EvidenceUnavailable):
        reader.http_response(URL, parameters)


def test_query_order_is_irrelevant_but_original_body_is_not(tmp_path):
    rows = [(URL, {'list': ONE}, response(b'one')),
            (URL, {'list': TWO}, response(b'two'))]
    capture(tmp_path, rows)
    reader = evidence.load_session(tmp_path, DAY)
    for symbol, body in [(TWO, b'two'), (ONE, b'one'), (TWO, b'two')]:
        assert reader.http_response(URL, {'list': symbol})['body'] == body
    assert not reader.missing_inputs


def test_sealed_archive_cannot_be_extended_or_reannotated(tmp_path):
    session = capture(tmp_path, [(URL, {'list': ONE}, response())])
    original_manifest = session.manifest_path.read_bytes()
    with pytest.raises(evidence.EvidenceUnavailable, match='closed'):
        session.raw_response(URL, {'list': TWO}, response(b'late'))
    with pytest.raises(evidence.EvidenceUnavailable, match='closed'):
        session.annotate('late', {'value': 1})
    assert session.manifest_path.read_bytes() == original_manifest


def test_conflicted_session_stays_unusable_when_same_body_is_seen_again(tmp_path):
    first = capture(tmp_path, [(URL, {'list': ONE}, response(b'first')),
                               (URL, {'list': ONE}, response(b'second')),
                               (URL, {'list': ONE}, response(b'first'))])
    assert not first.context['complete']
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.load_session(tmp_path, DAY)


def test_real_accepted_pack_verifies_all_original_blob_bytes_without_http():
    root = Path(__file__).resolve().parent
    receipt = json.loads((root / 'quant_research_runs/20261023_production_provenance_v1/real_recovery_01/receipt.json').read_text(encoding='utf-8'))
    archive = root / 'data/icim_v14_original_inputs'
    from datetime import date
    for product in ['IC', 'IM']:
        selected = receipt['selected_packages'][product]
        path = archive / Path(selected['manifest']).relative_to(root / 'quant_research_runs/20261023_production_provenance_v1/selected_original_bundle')
        reader = evidence._load_one(archive, path, date(2026, 10, 22))
        assert reader.manifest_sha256 == selected['sha256']
        for entry in [*reader.context['entries'].values(), *reader.context['raw']]:
            body = evidence._verified_blob(archive, entry)
            assert hashlib.sha256(body).hexdigest() == entry['sha256']
