"""Independent repair audit: real shared-lock contention, disposable ledgers."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import threading

import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import ic_im_evidence_adapter_v1 as adapter
from test_icim_batch3_concurrency_20261021 import server, store
from test_icim_round2_audit_lifecycle_20261021 import snapshot


OUT = Path(os.environ['ICIM_TEST_OUTPUT_DIR'])
CLOCK = datetime(2026, 10, 21, 16, tzinfo=bot.BEIJING)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv('ICIM_DISABLE_EVIDENCE_CAPTURE', raising=False)
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('network forbidden'))
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))


def owner(monkeypatch, tmp_path, label='A'):
    module = server(monkeypatch, tmp_path)
    ledger, record = store(tmp_path / label, label)
    return module, module.LedgerCoordinator(ledger), record


def test_capture_observation_includes_real_shared_lock_queue_wait(monkeypatch, tmp_path):
    module, coordinator, record = owner(monkeypatch, tmp_path)
    tick = [100.]
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    before = snapshot(coordinator.store.root)
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    reached_lock = threading.Event()
    actual_lock = coordinator.lock
    result = {}

    class ObserveActualLock:
        def __enter__(self):
            reached_lock.set()
            return actual_lock.__enter__()

        def __exit__(self, *args):
            return actual_lock.__exit__(*args)

    def capture(strategy, root, clock):
        observed = strategy._iv_observation_clock(clock)
        result['observed_at'] = observed.isoformat()
        strategy.quote_snapshot_guard.official_snapshot_evidence(
            clock + timedelta(seconds=1), observed, clock.date(), 'received while queued')
        return {'day': str(clock.date()), 'status': 'captured'}

    def run():
        try:
            result['capture'] = coordinator.capture_current_inputs(CLOCK)
        except BaseException as exc:
            result['uncaught'] = repr(exc)

    monkeypatch.setattr(coordinator, 'lock', ObserveActualLock())
    monkeypatch.setattr(adapter, 'capture_daily', capture)
    # This is the actual strategy RLock, held by another thread exactly as a
    # foreground query holds it. Two seconds of deterministic queue time pass.
    with actual_lock:
        thread = threading.Thread(target=run, name='independent-capture')
        thread.start()
        assert reached_lock.wait(4)
        tick[0] = 102.
    thread.join(4)
    assert not thread.is_alive()
    (OUT / 'lock_queue_observation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert snapshot(coordinator.store.root) == before
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors
    assert result.get('capture', {}).get('status') == 'captured', result
    assert result['observed_at'] == (CLOCK + timedelta(seconds=2)).isoformat()


def test_capture_head_read_failure_restores_every_anchor_and_skips_collection(monkeypatch, tmp_path):
    module, coordinator, record = owner(monkeypatch, tmp_path)
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    identity = bot.LIVE_CONTINUATION_ANCHOR
    before = snapshot(coordinator.store.root)
    monkeypatch.setattr(coordinator.store, 'load_latest', lambda: (_ for _ in ()).throw(OSError('controlled unreadable head')))
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: pytest.fail('capture after head read failure'))
    result = coordinator.capture_current_inputs(CLOCK)
    assert result['status'] == 'incomplete'
    assert coordinator._evidence_capture_bucket is None
    assert bot.LIVE_CONTINUATION_ANCHOR is identity
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors
    assert bot._COLLECTION_CLOCK_ANCHOR.get() is None
    assert snapshot(coordinator.store.root) == before


def test_partial_anchor_install_failure_restores_original(monkeypatch, tmp_path):
    module, coordinator, record = owner(monkeypatch, tmp_path)
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    identity = bot.LIVE_CONTINUATION_ANCHOR

    def fail_install(value):
        bot.LIVE_CONTINUATION_ANCHOR['IC'] = {'partial': 'must not escape'}
        raise RuntimeError('injected installer failure')

    monkeypatch.setattr(bot, 'install_runtime_anchors', fail_install)
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: pytest.fail('capture after install failure'))
    result = coordinator.capture_current_inputs(CLOCK)
    assert result['status'] == 'incomplete'
    assert bot.LIVE_CONTINUATION_ANCHOR is identity
    assert bot.LIVE_CONTINUATION_ANCHOR == anchors


def test_concurrent_distinct_query_heads_and_cancelled_display_context_isolated(monkeypatch, tmp_path):
    module, first, first_record = owner(monkeypatch, tmp_path, 'A')
    other_store, other_record = store(tmp_path / 'B', 'B')
    second = module.LedgerCoordinator(other_store)
    first_entered = threading.Event()
    release_first = threading.Event()
    seen, results = [], {}
    original_poe = bot.poe
    before = [snapshot(first.store.root), snapshot(second.store.root)]

    def body(_self):
        label = threading.current_thread().name
        display = bot._VERIFIED_DISPLAY_ANCHORS.get()
        seen.append((label, display['IC']['audit_context_label'], bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label']))
        if label == 'A':
            first_entered.set()
            assert release_first.wait(4)
            raise asyncio.CancelledError('injected first-query cancellation')
        with bot.poe.start_message() as message:
            bot._write_last_verified_snapshot(message, 'IC')

    def query(label, coordinator):
        try:
            results[label] = coordinator.execute_query('当前持仓', CLOCK, persist_confirmed=False)
        except BaseException as exc:
            results[label] = type(exc).__name__
        finally:
            results[label + '_display_after'] = bot._VERIFIED_DISPLAY_ANCHORS.get()

    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', body)
    a = threading.Thread(target=query, args=('A', first), name='A')
    b = threading.Thread(target=query, args=('B', second), name='B')
    a.start()
    assert first_entered.wait(4)
    b.start()
    release_first.set()
    a.join(4)
    b.join(4)
    assert not a.is_alive() and not b.is_alive()
    assert results['A'] == 'CancelledError'
    assert seen == [('A', 'A', 'A'), ('B', 'B', 'B')]
    assert '最后逐腿核验快照（2026-10-20' in results['B'][0]
    assert results['A_display_after'] is results['B_display_after'] is None
    assert bot.poe is original_poe and bot._SIGNAL_OBSERVER is None
    assert [snapshot(first.store.root), snapshot(second.store.root)] == before


def test_nested_display_context_restored_after_inner_cancel(monkeypatch, tmp_path):
    module, coordinator, record = owner(monkeypatch, tmp_path)
    outer = state.anchors_from_record(record)
    outer['IC']['audit_context_label'] = 'outer'

    def body(_self):
        assert bot._VERIFIED_DISPLAY_ANCHORS.get()['IC']['audit_context_label'] == 'A'
        raise asyncio.CancelledError

    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', body)
    with bot.verified_display_anchors(outer):
        with pytest.raises(asyncio.CancelledError):
            coordinator.execute_query('当前持仓', CLOCK, persist_confirmed=False)
        assert bot._VERIFIED_DISPLAY_ANCHORS.get() == outer
    assert bot._VERIFIED_DISPLAY_ANCHORS.get() is None


def test_refresh_after_crossday_catchup_does_not_seal_unfinished_new_session(monkeypatch, tmp_path):
    module, coordinator, record = owner(monkeypatch, tmp_path)
    start = CLOCK.replace(hour=23, minute=59)
    end = start + timedelta(minutes=2)
    current = [start]
    seen = []
    before = snapshot(coordinator.store.root)
    original_capture = coordinator.capture_current_inputs

    def catch_up(now):
        assert now == start
        current[0] = end

    def capture(now=None):
        value = original_capture(now)
        seen.append(value)
        return value

    async def invoke(fn, *args):
        return fn(*args)

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(module, 'coordinator', coordinator)
    monkeypatch.setattr(coordinator, 'catch_up_once', catch_up)
    monkeypatch.setattr(coordinator, 'capture_current_inputs', capture)
    monkeypatch.setattr(bot, '_now_beijing', lambda: current[0])
    monkeypatch.setattr(module.asyncio, 'to_thread', invoke)
    monkeypatch.setattr(module.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._refresh_loop())
    assert seen == [{'day': '2026-10-22', 'status': 'waiting_for_close', 'products': {}}]
    assert coordinator._evidence_capture_bucket is None
    assert snapshot(coordinator.store.root) == before


def test_refresh_catchup_cancel_does_not_start_capture(monkeypatch, tmp_path):
    module, coordinator, record = owner(monkeypatch, tmp_path)

    def cancelled(_now):
        raise asyncio.CancelledError

    async def invoke(fn, *args):
        return fn(*args)

    monkeypatch.setattr(module, 'coordinator', coordinator)
    monkeypatch.setattr(coordinator, 'catch_up_once', cancelled)
    monkeypatch.setattr(coordinator, 'capture_current_inputs', lambda *a: pytest.fail('capture after cancellation'))
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(module.asyncio, 'to_thread', invoke)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module._refresh_loop())
