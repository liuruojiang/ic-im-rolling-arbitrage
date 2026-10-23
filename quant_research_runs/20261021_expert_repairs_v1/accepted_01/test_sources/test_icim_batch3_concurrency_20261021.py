"""Actual coordinator/ledger entry points; all stores and subprocesses are temporary."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_icim_round2_audit_lifecycle_20261021 as lifecycle

DAY = date(2026, 10, 21)
CLOCK = lifecycle.CLOCK
ROOT = Path(__file__).resolve().parent


def server(monkeypatch,tmp_path):
    # The module creates its default coordinator at import. Ensure even that
    # otherwise-unused singleton can only initialize a disposable test store.
    monkeypatch.setenv('ICIM_STATE_DIR',str(tmp_path/'module_default'))
    monkeypatch.setenv('ICIM_REQUIRE_MIGRATION','0')
    monkeypatch.setenv('ICIM_DISABLE_INTERNAL_REFRESH','1')
    return importlib.import_module('poe_ic_im_v1_4_server')


def store(tmp_path,label='A'):
    value = ledger._record()
    value.update(verified_day='2026-10-20',signals={},source='synthetic_concurrency_inventory')
    for anchor in value['products'].values():
        anchor.update(last_verified_day='2026-10-20',verified_grid_entry_source='none',
                      verified_next_grid_entry_source='none',audit_context_label=label)
    value['digest'] = state._digest(value)
    state._validate_record(value)
    result = state.StateStore(tmp_path)
    result._atomic_write(result.journal_dir/'000000-2026-10-20.json',value)
    result._atomic_write(result.latest_path,value)
    return result,result.load_latest()


def signals(record):
    anchors = state.anchors_from_record(record)
    prepared = {product:lifecycle.close_evidence(ledger._signal(product,anchors[product],DAY))
                for product in state.PRODUCTS}
    for signal in prepared.values():signal['v14_opening_events'] = []
    return prepared


@pytest.mark.parametrize('relationship',['one_coordinator','two_same_store','two_distinct_stores'])
def test_actual_execute_query_context_isolated_across_public_coordinators(tmp_path,monkeypatch,relationship):
    module = server(monkeypatch,tmp_path)
    first_store,_ = store(tmp_path/'first','A')
    first = module.LedgerCoordinator(first_store)
    if relationship == 'one_coordinator':
        second = first
    elif relationship == 'two_same_store':
        second = module.LedgerCoordinator(state.StateStore(first_store.root))
    else:
        second_store,_ = store(tmp_path/'second','B')
        second = module.LedgerCoordinator(second_store)
    baseline_capture = module._CaptureOutput('baseline')
    monkeypatch.setattr(bot,'poe',baseline_capture)
    monkeypatch.setattr(bot,'_SIGNAL_OBSERVER',None)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    a_entered,b_entered,release_a,a_done = [threading.Event() for _ in range(4)]
    errors,outputs = {},{}
    before_first = lifecycle.snapshot(first_store.root)
    before_second = lifecycle.snapshot(second.store.root)

    def run_fake(self):
        # Synthetic business body only: real execute_query still installs all
        # anchors, capture and observer and performs its real finally cleanup.
        label = threading.current_thread().name
        if label == 'A':
            a_entered.set()
            assert release_a.wait(4),'main test did not release first query'
        else:
            b_entered.set()
            assert a_done.wait(4),'first query did not complete synthetic body'
        actual = bot.LIVE_CONTINUATION_ANCHOR['IC']['audit_context_label']
        output = bot.poe
        if isinstance(output,module._CaptureOutput):
            with output.start_message() as message:
                message.write(label+':'+actual)
        observer = bot._SIGNAL_OBSERVER
        if observer is not None:
            observer('IC',dict(request_label=label,anchor_label=actual,market_date=DAY,close_confirmed=False))
        if label == 'A':
            a_done.set()

    monkeypatch.setattr(bot.ICIMMainlinesBot,'run',run_fake)
    def query(label,coordinator):
        try:
            outputs[label] = coordinator.execute_query('实时信号',CLOCK,persist_confirmed=False)
        except BaseException as exc:
            errors[label] = repr(exc)
            if label == 'A':a_done.set()
    a = threading.Thread(target=query,args=('A',first),name='A')
    b = threading.Thread(target=query,args=('B',second),name='B')
    a.start()
    assert a_entered.wait(4)
    b.start()
    overlapped = b_entered.wait(.2)
    release_a.set()
    a.join(6);b.join(6)
    assert not a.is_alive() and not b.is_alive(), 'query thread did not terminate'
    assert not errors
    assert lifecycle.snapshot(first_store.root) == before_first
    assert lifecycle.snapshot(second.store.root) == before_second
    expected_b = 'B' if relationship == 'two_distinct_stores' else 'A'
    assert outputs['A'][0] == 'A:A',dict(overlapped=overlapped,outputs=outputs)
    assert outputs['B'][0] == 'B:'+expected_b,dict(overlapped=overlapped,outputs=outputs)
    assert outputs['A'][2]['IC']['request_label'] == 'A'
    assert outputs['A'][2]['IC']['anchor_label'] == 'A'
    assert outputs['B'][2]['IC']['request_label'] == 'B'
    assert outputs['B'][2]['IC']['anchor_label'] == expected_b
    assert bot.poe is baseline_capture
    assert bot._SIGNAL_OBSERVER is None
    assert not overlapped


def test_actual_execute_query_restores_globals_after_business_exception(tmp_path,monkeypatch):
    module = server(monkeypatch,tmp_path)
    isolated,_ = store(tmp_path/'state')
    coordinator = module.LedgerCoordinator(isolated)
    baseline_capture = module._CaptureOutput('baseline')
    monkeypatch.setattr(bot,'poe',baseline_capture)
    monkeypatch.setattr(bot,'_SIGNAL_OBSERVER',None)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    before = lifecycle.snapshot(isolated.root)
    def fail(self):raise RuntimeError('synthetic business exception')
    monkeypatch.setattr(bot.ICIMMainlinesBot,'run',fail)
    with pytest.raises(RuntimeError,match='synthetic business'):
        coordinator.execute_query('实时信号',CLOCK,persist_confirmed=False)
    assert bot.poe is baseline_capture and bot._SIGNAL_OBSERVER is None
    assert lifecycle.snapshot(isolated.root) == before


def test_nonpersistent_query_cannot_write_even_two_complete_observations(tmp_path,monkeypatch):
    module = server(monkeypatch,tmp_path)
    isolated,_ = store(tmp_path/'state')
    coordinator = module.LedgerCoordinator(isolated)
    monkeypatch.setattr(bot,'LIVE_CONTINUATION_ANCHOR',deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    before = lifecycle.snapshot(isolated.root)
    def fake(self):
        for product in ['IC','IM']:
            bot._SIGNAL_OBSERVER(product,dict(product=product,market_date=DAY,close_confirmed=True))
    monkeypatch.setattr(bot.ICIMMainlinesBot,'run',fake)
    _,_,observed = coordinator.execute_query('实时信号',CLOCK,persist_confirmed=False)
    assert set(observed) == {'IC','IM'}
    assert lifecycle.snapshot(isolated.root) == before


def test_actual_concurrent_store_objects_append_exactly_once(tmp_path):
    isolated,current = store(tmp_path/'state')
    prepared = signals(current)
    ready = threading.Barrier(2)
    def append():
        local = state.StateStore(isolated.root)
        ready.wait(timeout=3)
        try:
            local.append_confirmed_signals(current,deepcopy(prepared))
            return 'appended'
        except RuntimeError as exc:
            assert '另一请求推进' in str(exc)
            return 'stale_rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _:append(),range(2)))
    assert sorted(result) == ['appended','stale_rejected']
    latest = isolated.load_latest()
    assert latest['sequence'] == current['sequence']+1
    assert latest['previous_digest'] == current['digest']
    assert len(list(isolated.journal_dir.glob('000001-*.json'))) == 1


@pytest.mark.parametrize('scenario',['cold_first','cold_second','cold_third','recover_pending_journal'])
def test_cross_process_append_serializes_header_compare_and_write(tmp_path,monkeypatch,scenario):
    isolated,current = store(tmp_path/'state')
    pending_bytes = None
    if scenario == 'recover_pending_journal':
        original = isolated._atomic_write
        def interrupted(path,record):
            if path == isolated.latest_path and record['sequence']==1:
                raise RuntimeError('synthetic interrupted writer before head replacement')
            original(path,record)
        with monkeypatch.context() as patch:
            patch.setattr(isolated,'_atomic_write',interrupted)
            with pytest.raises(RuntimeError,match='synthetic interrupted writer'):
                isolated.append_confirmed_signals(current,signals(current))
        pending_bytes = next(isolated.journal_dir.glob('000001-*.json')).read_bytes()
        assert isolated.load_latest()['digest'] == current['digest']
        # Restore payloads contain journal/head; the disposable OS lock may be
        # absent after process/artifact restoration. It holds no ledger data.
        isolated.lock_path.unlink()
    assert not (isolated.root/'.ledger.lock').exists()
    # Both children load the SAME head before receiving GO. Their imports and
    # all ledger writes are restricted to this test's explicitly supplied root.
    script = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_icim_round2_audit_lifecycle_20261021 as lifecycle
from datetime import date
store=state.StateStore(Path(sys.argv[2]))
current=store.load_latest()
anchors=state.anchors_from_record(current)
signals={p:lifecycle.close_evidence(ledger._signal(p,anchors[p],date(2026,10,21))) for p in state.PRODUCTS}
for signal in signals.values():signal['v14_opening_events']=[]
print('READY',flush=True)
assert sys.stdin.readline().strip()=='GO'
try:
 result=store.append_confirmed_signals(current,signals)
 print(json.dumps({'outcome':'appended','sequence':result['sequence']}),flush=True)
except RuntimeError as exc:
 print(json.dumps({'outcome':'rejected','reason':str(exc)},ensure_ascii=False),flush=True)
'''
    env = dict(os.environ,ICIM_STATE_DIR=str(tmp_path/'unused_child_default'),
               ICIM_REQUIRE_MIGRATION='0',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    children = [subprocess.Popen([sys.executable,'-c',script,str(ROOT),str(isolated.root)],
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                text=True,encoding='utf-8',env=env,cwd=ROOT) for _ in range(2)]
    try:
        for child in children:
            assert child.stdout.readline().strip() == 'READY'
        for child in children:
            child.stdin.write('GO\n');child.stdin.flush()
        result = []
        for child in children:
            stdout,stderr = child.communicate(timeout=20)
            assert child.returncode == 0,stderr
            result.append(json.loads(stdout.strip()))
        assert sorted(row['outcome'] for row in result) == ['appended','rejected']
        rejected = next(row for row in result if row['outcome']=='rejected')
        assert '另一请求推进' in rejected['reason']
        latest = isolated.load_latest()
        assert latest['sequence'] == 1 and latest['previous_digest'] == current['digest']
        assert len(list(isolated.journal_dir.glob('000001-*.json'))) == 1
        if pending_bytes is not None:
            assert next(isolated.journal_dir.glob('000001-*.json')).read_bytes() == pending_bytes
            assert isolated.latest_path.read_bytes() == pending_bytes
    finally:
        for child in children:
            if child.poll() is None:child.kill();child.wait(timeout=5)


def test_child_waits_for_held_store_lock_without_prelock_write_failure(tmp_path):
    isolated,_ = store(tmp_path/'state')
    script = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import poe_ic_im_v1_4_state as state
root=Path(sys.argv[2])
(root/'child_write_probe.txt').write_text('child file access works',encoding='utf-8')
print('READY',flush=True)
with state.StateStore(root)._exclusive_lock():
 print('ACQUIRED',flush=True)
'''
    env = dict(os.environ,ICIM_STATE_DIR=str(tmp_path/'unused_child_default'),
               ICIM_REQUIRE_MIGRATION='0',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    child = None
    try:
        with isolated._exclusive_lock():
            child = subprocess.Popen([sys.executable,'-c',script,str(ROOT),str(isolated.root)],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                                     encoding='utf-8',env=env,cwd=ROOT)
            assert child.stdout.readline().strip() == 'READY'
            assert (isolated.root/'child_write_probe.txt').read_text(encoding='utf-8') == 'child file access works'
            threading.Event().wait(.2)
            early_exit = child.poll()
        stdout,stderr = child.communicate(timeout=15)
        assert early_exit is None,dict(exit_code=early_exit,stderr=stderr)
        assert child.returncode == 0,stderr
        assert stdout.strip() == 'ACQUIRED'
    finally:
        if child is not None and child.poll() is None:child.kill();child.wait(timeout=5)


def test_unacquired_file_lock_is_not_unlocked_or_original_failure_masked(tmp_path,monkeypatch):
    isolated,_ = store(tmp_path/'state')
    calls = []
    if os.name == 'nt':
        import msvcrt
        acquire,release = msvcrt.LK_LOCK,msvcrt.LK_UNLCK
        def fail(_fd,operation,_count):
            calls.append(operation)
            if operation == acquire:raise OSError('synthetic acquisition failed')
            raise AssertionError('attempted to unlock an unacquired file lock')
        monkeypatch.setattr(msvcrt,'locking',fail)
    else:
        import fcntl
        acquire,release = fcntl.LOCK_EX,fcntl.LOCK_UN
        def fail(_fd,operation):
            calls.append(operation)
            if operation == acquire:raise OSError('synthetic acquisition failed')
            raise AssertionError('attempted to unlock an unacquired file lock')
        monkeypatch.setattr(fcntl,'flock',fail)
    with pytest.raises(OSError,match='synthetic acquisition failed'):
        with isolated._exclusive_lock():
            raise AssertionError('must not enter protected work')
    assert calls == [acquire]


def test_native_lock_can_coordinate_empty_file_without_prelock_writes(tmp_path):
    lock = tmp_path/'empty.lock'
    stream = lock.open('a+b')
    def native(operation):
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK if operation=='lock' else msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX if operation=='lock' else fcntl.LOCK_UN)
    script = r'''
import os,sys
stream=open(sys.argv[1],'a+b')
print('READY',flush=True)
stream.seek(0)
if os.name=='nt':
 import msvcrt
 msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
else:
 import fcntl
 fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
print('ACQUIRED',flush=True)
stream.seek(0)
if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
stream.close()
'''
    child = None
    held = False
    try:
        native('lock');held=True
        child = subprocess.Popen([sys.executable,'-c',script,str(lock)],stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True,encoding='utf-8')
        assert child.stdout.readline().strip() == 'READY'
        threading.Event().wait(.2)
        assert child.poll() is None
        native('unlock');held=False
        stdout,stderr = child.communicate(timeout=15)
        assert child.returncode == 0,stderr
        assert stdout.strip() == 'ACQUIRED'
        assert lock.stat().st_size == 0
    finally:
        if held:native('unlock')
        stream.close()
        if child is not None and child.poll() is None:child.kill();child.wait(timeout=5)


def test_reader_sees_valid_old_head_while_new_journal_precedes_atomic_head(tmp_path,monkeypatch):
    isolated,current = store(tmp_path/'state')
    prepared = signals(current)
    journal_written,release_head = threading.Event(),threading.Event()
    original = isolated._atomic_write
    errors = []
    def paused(path,record):
        original(path,record)
        if path.parent == isolated.journal_dir and record['sequence']==1:
            journal_written.set()
            assert release_head.wait(4)
    monkeypatch.setattr(isolated,'_atomic_write',paused)
    def append():
        try:isolated.append_confirmed_signals(current,prepared)
        except BaseException as exc:errors.append(repr(exc))
    thread = threading.Thread(target=append)
    thread.start()
    try:
        assert journal_written.wait(4)
        for _ in range(3):
            assert state.StateStore(isolated.root).load_latest()['digest'] == current['digest']
    finally:
        release_head.set();thread.join(6)
    assert not errors and not thread.is_alive()
    assert isolated.load_latest()['sequence'] == 1


def test_crash_after_journal_write_recovers_under_append_lock_without_duplicate(tmp_path,monkeypatch):
    isolated,current = store(tmp_path/'state')
    prepared = signals(current)
    original = isolated._atomic_write
    def crash(path,record):
        if path == isolated.latest_path and record['sequence']==1:
            raise RuntimeError('synthetic crash before atomic head')
        original(path,record)
    monkeypatch.setattr(isolated,'_atomic_write',crash)
    with pytest.raises(RuntimeError,match='synthetic crash'):
        isolated.append_confirmed_signals(current,prepared)
    assert isolated.load_latest()['digest'] == current['digest']
    journal = next(isolated.journal_dir.glob('000001-*.json'))
    saved = journal.read_bytes()
    restarted = state.StateStore(isolated.root)
    recovered = restarted.append_confirmed_signals(current,prepared)
    assert recovered['sequence'] == 1
    assert journal.read_bytes() == saved
    assert restarted.latest_path.read_bytes() == journal.read_bytes()
    assert len(list(isolated.journal_dir.glob('000001-*.json'))) == 1


def test_concurrent_initialize_bootstraps_once_with_shared_file_lock(tmp_path,monkeypatch):
    _,fixture = store(tmp_path/'fixture')
    isolated = state.StateStore(tmp_path/'empty')
    bootstrap_calls = []
    ready = threading.Barrier(2)
    def bootstrap():
        bootstrap_calls.append('called')
        return deepcopy(fixture)
    monkeypatch.setattr(state,'bootstrap_record',bootstrap)
    def initialize():
        ready.wait(timeout=3)
        return state.StateStore(isolated.root).initialize()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _:initialize(),range(2)))
    assert bootstrap_calls == ['called']
    assert [row['digest'] for row in outputs] == [fixture['digest']]*2
    assert isolated.load_latest()['digest'] == fixture['digest']
    assert len(list(isolated.journal_dir.glob('*.json'))) == 1


def test_initialize_refuses_restored_journal_without_head_and_preserves_payloads(tmp_path,monkeypatch):
    isolated,_ = store(tmp_path/'restored')
    isolated.latest_path.unlink()
    before = lifecycle.snapshot(isolated.root)
    def forbidden_bootstrap():
        raise AssertionError('must not replace restored journal with a fresh bootstrap')
    monkeypatch.setattr(state,'bootstrap_record',forbidden_bootstrap)
    with pytest.raises(RuntimeError):
        isolated.initialize()
    assert lifecycle.snapshot(isolated.root) == before
    assert not isolated.latest_path.exists()
