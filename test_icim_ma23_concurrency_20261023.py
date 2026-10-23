"""Independent concurrency adversaries; real threads, offline synthetic inputs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import contextvars
import threading

import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)


def packet(body=b'offline-synthetic'):
    value = requests.Response()
    value.status_code, value.encoding = 200, 'utf-8'
    value._content, value._content_consumed = body, True
    return value


@pytest.mark.parametrize('shutdown_seconds', [44., 46.])
def test_independent_four_worker_collector_cannot_seal_success_after_shutdown_deadline(
        monkeypatch, tmp_path, shutdown_seconds):
    from test_icim_free_sources_parallel_capture_20261022 import fake_strategy
    strategy, observed = fake_strategy(monkeypatch)
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: observed.tick)
    base_pool = adapter.ThreadPoolExecutor
    shutdowns = []
    class ShutdownTailPool(base_pool):
        def __exit__(self, *args):
            deadline = bot._NETWORK_DEADLINE.get()
            result = super().__exit__(*args)
            observed.tick = deadline - 45. + shutdown_seconds
            shutdowns.append({'deadline': deadline, 'completed_at': observed.tick})
            return result
    monkeypatch.setattr(adapter, 'ThreadPoolExecutor', ShutdownTailPool)
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert len(shutdowns) == 2 and not observed.active
    if shutdown_seconds < 45:
        assert result['status'] == 'captured', result
    else:
        assert result['status'] == 'incomplete', result
        for product in ['IC', 'IM']:
            assert result['products'][product]['status'] == 'incomplete'
            assert '预算' in result['products'][product]['reason']


def test_parallel_same_role_fetch_is_single_and_each_consumer_gets_private_value(tmp_path):
    calls, started, release = [], threading.Event(), threading.Event()
    lock = threading.Lock()
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        def source():
            with lock:
                calls.append(threading.get_ident())
            started.set()
            assert release.wait(3)
            return {'rows': [1, 2], 'synthetic': True}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(contextvars.copy_context().run,
                                   session.call, 'shared', {'day': DAY}, source) for _ in range(4)]
            assert started.wait(3)
            with pytest.raises(evidence.EvidenceUnavailable, match='active'):
                session.finish()
            release.set()
            values = [f.result(timeout=3) for f in futures]
        assert len(calls) == 1 and len(session.entries) == 1
        values[0]['rows'][0] = 999
        assert all(v['rows'] == [1, 2] for v in values[1:])
    assert session.context['complete'] is True
    reader = evidence.load_session(tmp_path, DAY)
    assert reader.call('shared', {'day': DAY})['rows'] == [1, 2]


@pytest.mark.parametrize('failure', [KeyboardInterrupt, SystemExit])
def test_baseexception_waits_for_real_active_writer_before_context_seal(monkeypatch, tmp_path, failure):
    first, failed, release = threading.Event(), threading.Event(), threading.Event()
    active, completed, seals = set(), [], []
    original_finish = evidence.CaptureSession.finish
    def finish(session):
        seals.append(len(active))
        assert not active, 'writer outlived executor failure cleanup'
        return original_finish(session)
    monkeypatch.setattr(evidence.CaptureSession, 'finish', finish)
    def fetch(index):
        active.add(index)
        try:
            assert evidence.current_session.get() is not None
            assert bot._NETWORK_DEADLINE.get() is not None
            if index == 0:
                first.set()
                assert release.wait(3)
                evidence.current_session.get().raw_response(
                    'https://example.invalid/late-writer', {'index': index}, packet())
                completed.append(index)
                return index
            assert first.wait(3)
            failed.set()
            raise failure('synthetic worker cancellation')
        finally:
            active.remove(index)
    def unblock():
        try:
            assert failed.wait(3)
        finally:
            release.set()
    observer = threading.Thread(target=unblock)
    observer.start()
    try:
        with pytest.raises(failure):
            with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
                session.call('scope', {}, lambda: True)
                with bot._network_budget(45):
                    bot._collect_quote_batches(fetch, list(range(6)))
    finally:
        observer.join(3)
    assert completed == [0] and seals == [0] and not active
    assert len(session.context['raw']) == 1
    assert session.context['complete'] is False


def test_concurrent_raw_byte_budget_is_atomic_and_unsealed_result_never_promoted(tmp_path):
    barrier = threading.Barrier(4)
    good, bad = [], []
    signal = {'persistence_eligible': True}
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}, max_item_bytes=64, max_total_bytes=64) as session:
        session.call('scope', {}, lambda: True)  # four JSON bytes.
        session.bind_signal(signal)
        def write(index):
            barrier.wait(3)
            try:
                session.raw_response('https://example.invalid/budget/' + str(index), None, packet(b'x' * 28))
                good.append(index)
            except evidence.EvidenceUnavailable:
                bad.append(index)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(contextvars.copy_context().run, write, i) for i in range(4)]
            for future in futures:
                future.result(timeout=3)
        assert len(good) == len(bad) == 2
        assert session.total_bytes == 60
        assert signal['persistence_eligible'] is False
    assert session.context['total_bytes'] == sum(row['bytes'] for row in session.context['entries'].values()) + sum(row['bytes'] for row in session.context['raw'])
    assert session.context['complete'] is False and signal['persistence_eligible'] is False


def test_sina_batch_retry_inherits_parent_deadline_and_cannot_start_after_it(monkeypatch):
    tick, deadlines, calls = [0.], [], []
    barrier = threading.Barrier(2)
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    def get(url, **kwargs):
        calls.append(url)
        deadlines.append(bot._NETWORK_DEADLINE.get())
        barrier.wait(3)
        tick[0] = 9.9
        raise requests.Timeout('synthetic transport finishes after retry cutoff')
    monkeypatch.setattr(bot.requests, 'get', get)
    with bot._network_budget(10), pytest.raises(RuntimeError, match='预算'):
        bot._sina_put_detail_batches([str(10000000 + i) for i in range(24)])
    assert len(calls) == 2 and set(deadlines) == {10.}
    assert bot._NETWORK_DEADLINE.get() is None


def test_thread_batch_context_and_result_order_do_not_follow_completion_order(monkeypatch, tmp_path):
    first_waits, second_done = threading.Event(), threading.Event()
    completions, seen_deadlines = [], []
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('scope', {}, lambda: True)
        def fetch(index):
            assert evidence.current_session.get() is session
            assert bot._RUNTIME_CLOCK.get() == CLOCK
            seen_deadlines.append(bot._NETWORK_DEADLINE.get())
            if index == 0:
                first_waits.set()
                assert second_done.wait(3)
            elif index == 1:
                assert first_waits.wait(3)
            session.raw_response('https://example.invalid/reordered/' + str(index), {'batch': index}, packet(str(index).encode()))
            completions.append(index)
            if index == 1:
                second_done.set()
            return index
        with bot.runtime_clock(CLOCK), bot._network_budget(45):
            parent_deadline = bot._NETWORK_DEADLINE.get()
            assert bot._collect_quote_batches(fetch, list(range(6))) == list(range(6))
    assert completions.index(1) < completions.index(0)
    assert set(seen_deadlines) == {parent_deadline}
    reader = evidence.load_session(tmp_path, DAY)
    for index in reversed(range(6)):
        assert reader.http_response('https://example.invalid/reordered/' + str(index), {'batch': index})['body'] == str(index).encode()


def test_nested_same_thread_role_wrapper_counts_one_blob_and_no_orphan_bytes(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        value = session.call('same', {}, lambda: session.call('same', {}, lambda: {'value': 1}))
        assert value == {'value': 1} and len(session.entries) == 1
        assert session.total_bytes == next(iter(session.entries.values()))['bytes']
    assert session.context['complete'] is True
    assert evidence.load_session(tmp_path, DAY).call('same', {}) == {'value': 1}
