"""Archive concurrency tests use only temporary blobs and explicit barriers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import threading
from types import SimpleNamespace

import pytest

import ic_im_signal_evidence_v1 as evidence

DAY = date(2026, 10, 22)
CLOCK = datetime.fromisoformat('2026-10-22T18:00:00+08:00')


def test_independent_role_fetches_overlap_without_archive_lock(tmp_path):
    gate = threading.Barrier(2)
    session = evidence.CaptureSession(tmp_path, DAY, CLOCK, {})
    def fetch(value):
        gate.wait(timeout=3)
        return {'value': value}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(session.call, role, {}, lambda v=role: fetch(v)) for role in ['one','two']]
        assert [job.result(timeout=5) for job in jobs] == [{'value':'one'},{'value':'two'}]
    session.finish()
    assert session.context['complete'] and len(session.entries) == 2


def test_same_role_fetch_coalesced_and_each_result_is_independent(tmp_path):
    started, release = threading.Event(), threading.Event()
    session = evidence.CaptureSession(tmp_path, DAY, CLOCK, {})
    calls = []
    def fetch():
        calls.append(1)
        started.set()
        assert release.wait(timeout=3)
        return {'value': [1]}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(session.call, 'same', {}, fetch) for _ in range(3)]
        assert started.wait(timeout=3)
        release.set()
        results = [job.result(timeout=5) for job in jobs]
    assert calls == [1]
    results[0]['value'].append(999)
    assert results[1:] == [{'value':[1]}, {'value':[1]}]
    session.finish()


def test_parallel_raw_byte_budget_is_atomic_and_closed_manifest_truthful(tmp_path):
    session = evidence.CaptureSession(tmp_path, DAY, CLOCK, {}, max_item_bytes=10, max_total_bytes=10)
    response = SimpleNamespace(headers={}, status_code=200, encoding='ascii', content=b'1234567')
    gate = threading.Barrier(2)
    def write(url):
        gate.wait(timeout=3)
        try:
            session.raw_response(url, None, response)
            return 'ok'
        except evidence.EvidenceUnavailable:
            return 'budget_rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(write, name) for name in ['one','two']]
        assert sorted(job.result(timeout=5) for job in jobs) == ['budget_rejected','ok']
    assert session.total_bytes == 7 and len(session.raw) == 1
    session.finish()
    assert session.context['complete'] is False


def test_finish_cannot_certify_an_inflight_role(tmp_path):
    session = evidence.CaptureSession(tmp_path, DAY, CLOCK, {})
    started, release = threading.Event(), threading.Event()
    def fetch():
        started.set()
        assert release.wait(timeout=3)
        return 'verified'
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(session.call, 'one', {}, fetch)
        assert started.wait(timeout=3)
        try:
            with pytest.raises(evidence.EvidenceUnavailable, match='active'):
                session.finish()
        finally:
            release.set()
        assert job.result(timeout=5) == 'verified'
    session.finish()
    assert session.context['complete'] is True


def test_failed_role_can_be_retried_without_hiding_unrelated_failures(tmp_path):
    session = evidence.CaptureSession(tmp_path, DAY, CLOCK, {})
    def fail():
        raise RuntimeError('injected')
    with pytest.raises(RuntimeError):
        session.call('recoverable', {}, fail)
    with pytest.raises(RuntimeError):
        session.call('unrelated', {}, fail)
    session.call('recoverable', {}, lambda: 1)
    session.finish()
    assert session.context['complete'] is False
    assert {f['role']: f['resolved'] for f in session.failures} == {'recoverable': True, 'unrelated': False}


def test_nested_legacy_wrapper_records_one_outer_value_and_exact_byte_total(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        def outer():
            value = session.call('ic.etf', {}, lambda: {'last': 6.684})
            return {**value, 'source': 'verified_outer_provider'}
        result = session.call('ic.etf', {}, outer)
    reader = evidence.load_session(tmp_path, DAY, required_roles={'ic.etf'})
    assert reader.call('ic.etf', {}) == result
    assert len(session.entries) == 1
    assert session.total_bytes == next(iter(session.entries.values()))['bytes']
