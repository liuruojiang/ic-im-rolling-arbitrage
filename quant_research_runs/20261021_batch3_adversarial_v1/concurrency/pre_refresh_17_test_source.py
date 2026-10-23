"""Independent scoped-query and atomic-ledger probes; synthetic temporary stores only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
from test_icim_batch3_concurrency_20261021 import store, signals, server, CLOCK

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 10, 21)
AUDIT = ROOT/'quant_research_runs/20261021_batch3_adversarial_v1/concurrency'


def record_capability(name, payload):
    stage = os.environ.get('ICIM_AUDIT_STAGE', 'manual').replace('/', '_')
    directory = AUDIT/'capability_observations'/stage
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/(name+'.json')).open('x', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)


@pytest.mark.parametrize('inner_raises', [False, True])
@pytest.mark.parametrize('relationship', ['same', 'distinct'])
def test_unsupported_reentry_is_recorded_as_a_capability_limit(tmp_path, monkeypatch,
                                                            inner_raises, relationship):
    # No current server/digest/provider path performs this recursive call.
    # This diagnoses the unsupported API composition; it does NOT assert that
    # nested callbacks are supported or that a production defect was repaired.
    module = server(monkeypatch, tmp_path)
    outer_store, _ = store(tmp_path/'outer', 'A')
    outer = module.LedgerCoordinator(outer_store)
    inner_store, _ = store(tmp_path/'inner', 'B')
    inner = outer if relationship == 'same' else module.LedgerCoordinator(inner_store)
    expected_inner = 'A' if relationship == 'same' else 'B'
    original_poe = module._CaptureOutput('outside')
    original_observer = lambda *_args: None
    original_anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    monkeypatch.setattr(bot, 'poe', original_poe)
    monkeypatch.setattr(bot, '_SIGNAL_OBSERVER', original_observer)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(original_anchors))
    nested_results = []

    def body(_self):
        query = bot.poe.query.text
        label = bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label']
        if query == 'outer':
            with bot.poe.start_message() as message:
                message.write('before:'+label+'|')
            if inner_raises:
                with pytest.raises(RuntimeError, match='inner body failure'):
                    inner.execute_query('inner', CLOCK, persist_confirmed=False)
            else:
                nested_results.append(inner.execute_query('inner', CLOCK, persist_confirmed=False))
            label = bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label']
            with bot.poe.start_message() as message:
                message.write('after:'+label)
            if bot._SIGNAL_OBSERVER is not None:
                bot._SIGNAL_OBSERVER('IC', dict(anchor_label=label, market_date=DAY,
                                                 close_confirmed=False))
        else:
            with bot.poe.start_message() as message:
                message.write('inner:'+label)
            if inner_raises:
                raise RuntimeError('inner body failure')
            bot._SIGNAL_OBSERVER('IC', dict(anchor_label=label, market_date=DAY,
                                             close_confirmed=False))

    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', body)
    outer_head = outer_store.load_latest()['digest']
    inner_head = inner_store.load_latest()['digest']
    text, _, observed = outer.execute_query('outer', CLOCK, persist_confirmed=False)
    record_capability(f'reentry_{relationship}_{inner_raises}', dict(
        unsupported_recursive_body=True, text=text, outer_observed=observed,
        anchor_after=bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label'],
        observer_cleared=bot._SIGNAL_OBSERVER is None,
        production_reachability_established=False))
    assert text == 'before:A|after:'+expected_inner
    assert observed == {}  # Explicit known limitation, not claimed support.
    if not inner_raises:
        assert nested_results[0][0] == 'inner:'+expected_inner
        assert nested_results[0][2]['IC']['anchor_label'] == expected_inner
    assert bot.poe is original_poe
    assert bot._SIGNAL_OBSERVER is None
    assert bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label'] == expected_inner
    assert outer_store.load_latest()['digest'] == outer_head
    assert inner_store.load_latest()['digest'] == inner_head


@pytest.mark.parametrize('raises', [False, True])
def test_coordinator_owned_observer_is_cleared_and_poe_restored(tmp_path, monkeypatch, raises):
    # An arbitrary preinstalled callback is not part of the coordinator API.
    # Record ownership semantics rather than demanding an unsupported stack.
    module = server(monkeypatch, tmp_path)
    isolated, _ = store(tmp_path/'query', 'isolated')
    coordinator = module.LedgerCoordinator(isolated)
    marker = lambda *_args: None
    original_poe = module._CaptureOutput('outside')
    monkeypatch.setattr(bot, '_SIGNAL_OBSERVER', marker)
    monkeypatch.setattr(bot, 'poe', original_poe)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    original_anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    def body(_self):
        if raises:
            raise RuntimeError('query failure')
    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', body)
    if raises:
        with pytest.raises(RuntimeError, match='query failure'):
            coordinator.execute_query('query', CLOCK, persist_confirmed=False)
    else:
        coordinator.execute_query('query', CLOCK, persist_confirmed=False)
    assert bot.poe is original_poe
    assert bot._SIGNAL_OBSERVER is None
    assert bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label'] == 'isolated'
    record_capability(f'owned_observer_{raises}', dict(
        preinstalled_external_observer_restored=bot._SIGNAL_OBSERVER is marker,
        coordinator_clears_owned_observer=True, query_raises=raises,
        production_reachability_of_nested_callback_established=False))


def test_failed_query_releases_shared_lock_for_next_thread(tmp_path, monkeypatch):
    module = server(monkeypatch, tmp_path)
    first_store, _ = store(tmp_path/'first', 'A')
    second_store, _ = store(tmp_path/'second', 'B')
    first, second = module.LedgerCoordinator(first_store), module.LedgerCoordinator(second_store)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(bot, '_SIGNAL_OBSERVER', None)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    original = bot.poe
    def body(_self):
        label = bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label']
        if bot.poe.query.text == 'fail':
            entered.set()
            assert release.wait(5)
            raise RuntimeError('first query failed')
        with bot.poe.start_message() as message:
            message.write(label)
        bot._SIGNAL_OBSERVER('IC', dict(label=label, market_date=DAY, close_confirmed=False))
    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', body)
    with ThreadPoolExecutor(max_workers=2) as executor:
        failed = executor.submit(first.execute_query, 'fail', CLOCK, persist_confirmed=False)
        assert entered.wait(5)
        following = executor.submit(second.execute_query, 'next', CLOCK, persist_confirmed=False)
        release.set()
        with pytest.raises(RuntimeError, match='first query failed'):
            failed.result(timeout=5)
        text, _, observed = following.result(timeout=5)
    assert text == 'B' and observed['IC']['label'] == 'B'
    assert bot.poe is original and bot._SIGNAL_OBSERVER is None


def test_append_body_failure_releases_file_lock_and_keeps_original_head(tmp_path, monkeypatch):
    isolated, current = store(tmp_path/'state')
    prepared = signals(current)
    original = state.derive_next_anchors
    monkeypatch.setattr(state, 'derive_next_anchors', lambda *_args: (_ for _ in ()).throw(
        RuntimeError('synthetic validation failed')))
    with pytest.raises(RuntimeError, match='synthetic validation failed'):
        isolated.append_confirmed_signals(current, prepared)
    assert isolated.load_latest()['digest'] == current['digest']
    assert len(list(isolated.journal_dir.glob('*.json'))) == 1
    monkeypatch.setattr(state, 'derive_next_anchors', original)
    restarted = state.StateStore(isolated.root)
    assert restarted.append_confirmed_signals(current, prepared)['sequence'] == 1


def test_acquisition_failure_can_be_retried_without_partial_write(tmp_path, monkeypatch):
    isolated, current = store(tmp_path/'state')
    prepared = signals(current)
    if os.name == 'nt':
        import msvcrt
        real = msvcrt.locking
        def fail_once(fd, operation, count):
            if operation == msvcrt.LK_LOCK:
                raise OSError('synthetic contention exhaustion')
            raise AssertionError('release without acquisition')
        monkeypatch.setattr(msvcrt, 'locking', fail_once)
    else:
        import fcntl
        real = fcntl.flock
        def fail_once(fd, operation):
            if operation == fcntl.LOCK_EX:
                raise OSError('synthetic contention exhaustion')
            raise AssertionError('release without acquisition')
        monkeypatch.setattr(fcntl, 'flock', fail_once)
    with pytest.raises(OSError, match='synthetic contention exhaustion'):
        isolated.append_confirmed_signals(current, prepared)
    assert isolated.load_latest()['digest'] == current['digest']
    assert len(list(isolated.journal_dir.glob('*.json'))) == 1
    if os.name == 'nt':
        monkeypatch.setattr(msvcrt, 'locking', real)
    else:
        monkeypatch.setattr(fcntl, 'flock', real)
    assert state.StateStore(isolated.root).append_confirmed_signals(current, prepared)['sequence'] == 1


def test_separate_process_reader_gets_valid_old_then_new_atomic_head(tmp_path, monkeypatch):
    isolated, current = store(tmp_path/'state')
    prepared = signals(current)
    journal_written, release_head = threading.Event(), threading.Event()
    original = isolated._atomic_write
    def paused(path, record):
        original(path, record)
        if path.parent == isolated.journal_dir and record['sequence'] == 1:
            journal_written.set()
            assert release_head.wait(20), 'test failed to release atomic head'
    monkeypatch.setattr(isolated, '_atomic_write', paused)
    child_script = r'''
import sys,json
sys.path.insert(0,sys.argv[1])
import poe_ic_im_v1_4_state as state
store=state.StateStore(sys.argv[2])
print('READY',flush=True)
for _ in range(2):
 assert sys.stdin.readline().strip()=='READ'
 record=store.load_latest()
 print(json.dumps(dict(sequence=record['sequence'],digest=record['digest'])),flush=True)
'''
    env = dict(os.environ, ICIM_STATE_DIR=str(tmp_path/'unused_default'),
               ICIM_REQUIRE_MIGRATION='0', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.Popen([sys.executable,'-c',child_script,str(ROOT),str(isolated.root)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', env=env, cwd=ROOT)
    errors = []
    def append():
        try:
            isolated.append_confirmed_signals(current, prepared)
        except BaseException as exc:
            errors.append(repr(exc))
    thread = threading.Thread(target=append)
    try:
        assert child.stdout.readline().strip() == 'READY'
        thread.start()
        assert journal_written.wait(5)
        child.stdin.write('READ\n'); child.stdin.flush()
        old = json.loads(child.stdout.readline())
        assert old == dict(sequence=0, digest=current['digest'])
        release_head.set(); thread.join(5)
        assert not errors and not thread.is_alive()
        child.stdin.write('READ\n'); child.stdin.flush()
        new = json.loads(child.stdout.readline())
        assert new == dict(sequence=1, digest=isolated.load_latest()['digest'])
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, stderr
    finally:
        release_head.set()
        if thread.ident is not None:
            thread.join(5)
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)


def test_conflicting_pending_journal_is_never_overwritten_by_retry(tmp_path, monkeypatch):
    isolated, current = store(tmp_path/'state')
    prepared = signals(current)
    original = isolated._atomic_write
    def interrupted(path, record):
        if path == isolated.latest_path and record['sequence'] == 1:
            raise RuntimeError('head swap interrupted')
        original(path, record)
    monkeypatch.setattr(isolated, '_atomic_write', interrupted)
    with pytest.raises(RuntimeError, match='head swap interrupted'):
        isolated.append_confirmed_signals(current, prepared)
    journal = next(isolated.journal_dir.glob('000001-*.json'))
    pending = json.loads(journal.read_text(encoding='utf-8'))
    pending['previous_digest'] = 'a'*64
    pending['digest'] = state._digest(pending)
    original(journal, pending)
    before = journal.read_bytes()
    with pytest.raises(RuntimeError, match='内容冲突'):
        state.StateStore(isolated.root).append_confirmed_signals(current, prepared)
    assert journal.read_bytes() == before
    assert isolated.load_latest()['digest'] == current['digest']


@pytest.mark.parametrize('raises', [False, True])
@pytest.mark.parametrize('replay', [False, True])
def test_query_restores_context_clocks_on_success_and_exception(tmp_path, monkeypatch,
                                                              raises, replay):
    module = server(monkeypatch, tmp_path)
    isolated, _ = store(tmp_path/'state')
    coordinator = module.LedgerCoordinator(isolated)
    outside_clock = datetime(2026, 9, 10, 9, tzinfo=bot.BEIJING)
    outside_replay = date(2026, 9, 9)
    request_replay = date(2026, 10, 20) if replay else None
    original_poe = bot.poe
    monkeypatch.setattr(bot, '_SIGNAL_OBSERVER', None)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    def body(_self):
        assert bot._now_beijing() == CLOCK
        assert bot._HISTORICAL_REPLAY_DAY.get() == request_replay
        if raises:
            raise RuntimeError('body context failure')
    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', body)
    with bot.runtime_clock(outside_clock), bot.historical_replay(outside_replay):
        if raises:
            with pytest.raises(RuntimeError, match='body context failure'):
                coordinator.execute_query('query', CLOCK, persist_confirmed=False,
                                          replay_day=request_replay)
        else:
            coordinator.execute_query('query', CLOCK, persist_confirmed=False,
                                      replay_day=request_replay)
        assert bot._now_beijing() == outside_clock
        assert bot._HISTORICAL_REPLAY_DAY.get() == outside_replay
    assert bot.poe is original_poe and bot._SIGNAL_OBSERVER is None


@pytest.mark.parametrize('journal_before_death', [False, True])
def test_process_death_releases_lock_and_recovers_only_one_journal(tmp_path,
                                                                journal_before_death):
    isolated, current = store(tmp_path/'state')
    prepared = signals(current)
    child_script = r'''
import os,sys
sys.path.insert(0,sys.argv[1])
import poe_ic_im_v1_4_state as state
from test_icim_batch3_concurrency_20261021 import signals
store=state.StateStore(sys.argv[2])
if sys.argv[3]=='journal':
 current=store.load_latest()
 original=store._atomic_write
 def pause(path,record):
  original(path,record)
  if path.parent==store.journal_dir and record['sequence']==1:
   print('HELD',flush=True)
   assert sys.stdin.readline().strip()=='CONTINUE'
 store._atomic_write=pause
 store.append_confirmed_signals(current,signals(current))
else:
 with store._exclusive_lock():
  print('HELD',flush=True)
  assert sys.stdin.readline().strip()=='CONTINUE'
'''
    env = dict(os.environ, ICIM_STATE_DIR=str(tmp_path/'unused_default'),
               ICIM_REQUIRE_MIGRATION='0', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.Popen([sys.executable,'-c',child_script,str(ROOT),str(isolated.root),
                              'journal' if journal_before_death else 'empty'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding='utf-8', env=env, cwd=ROOT)
    try:
        marker = child.stdout.readline().strip()
        if marker != 'HELD':
            stdout, stderr = child.communicate(timeout=3)
            pytest.fail(dict(marker=marker, stdout=stdout, stderr=stderr))
        pending = list(isolated.journal_dir.glob('000001-*.json'))
        assert len(pending) == int(journal_before_death)
        pending_bytes = pending[0].read_bytes() if pending else None
        assert isolated.load_latest()['digest'] == current['digest']
        child.kill(); child.wait(timeout=5)
        restarted = state.StateStore(isolated.root)
        recovered = restarted.append_confirmed_signals(current, prepared)
        assert recovered['sequence'] == 1 and recovered['previous_digest'] == current['digest']
        journals = list(isolated.journal_dir.glob('000001-*.json'))
        assert len(journals) == 1
        if pending_bytes is not None:
            assert journals[0].read_bytes() == pending_bytes
            assert restarted.latest_path.read_bytes() == pending_bytes
        with pytest.raises(RuntimeError, match='另一请求推进'):
            restarted.append_confirmed_signals(current, prepared)
    finally:
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)
