"""Independent state/server adversaries; synthetic inputs and disposable stores.

No production ledger, provider or original package is modified by this file.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
import threading
from datetime import timedelta

import pytest

import ic_im_signal_evidence_v1 as evidence
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
from test_icim_b3adv_state_20261021 import prepare
from test_icim_expert_state_20261021 import _coordinator
from test_icim_round2_audit_lifecycle_20261021 import snapshot


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('field,value', [
    ('input_evidence_status', 'recording'),
    ('input_evidence_status', 'inconsistent'),
    ('persistence_eligible', False),
    ('diagnostic_only', True),
])
def test_bad_retry_cannot_recover_previously_written_journal(monkeypatch, tmp_path, product, field, value):
    store, current, signals = prepare(tmp_path, monkeypatch)
    original = store._atomic_write
    def fail_head(path, record):
        if path == store.latest_path:
            raise OSError('synthetic crash after journal publication')
        return original(path, record)
    monkeypatch.setattr(store, '_atomic_write', fail_head)
    with pytest.raises(OSError, match='after journal'):
        store.append_confirmed_signals(current, signals)
    monkeypatch.setattr(store, '_atomic_write', original)
    assert store.load_latest()['digest'] == current['digest']
    before = snapshot(store.root)
    broken = deepcopy(signals)
    broken[product][field] = value
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(current, broken)
    assert snapshot(store.root) == before
    assert store.load_latest()['verified_day'] == current['verified_day']


def test_safe_retry_recovers_first_published_journal_and_stale_repeat_cannot_append(monkeypatch, tmp_path):
    store, current, signals = prepare(tmp_path, monkeypatch)
    original = store._atomic_write
    def fail_head(path, record):
        if path == store.latest_path:
            raise OSError('synthetic crash after journal publication')
        return original(path, record)
    monkeypatch.setattr(store, '_atomic_write', fail_head)
    with pytest.raises(OSError):
        store.append_confirmed_signals(current, signals)
    monkeypatch.setattr(store, '_atomic_write', original)
    journals = sorted(store.journal_dir.glob('000001-*.json'))
    assert len(journals) == 1
    first = journals[0].read_bytes()
    repaired = store.append_confirmed_signals(current, signals)
    assert repaired['sequence'] == current['sequence'] + 1
    assert journals[0].read_bytes() == first
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match='另一请求'):
        store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before
    assert state.StateStore(store.root).load_latest()['digest'] == repaired['digest']


@pytest.mark.parametrize('product', ['IC', 'IM'])
def test_bound_early_copy_is_rejected_even_on_thread_without_capture_context(monkeypatch, tmp_path, product):
    store, current, signals = prepare(tmp_path, monkeypatch)
    signal = signals[product]
    signal['persistence_eligible'] = True
    clock = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)
    before = snapshot(store.root)
    def write_from_other_thread(payload):
        assert evidence.current_session.get() is None
        with pytest.raises(RuntimeError, match='采集|封存'):
            store.append_confirmed_signals(current, payload)
    with evidence.capture_session(tmp_path / 'evidence', clock.date(), clock, {}) as session:
        session.call('scope', {}, lambda: True)
        session.bind_signal(signal)
        copied = deepcopy(signals)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(write_from_other_thread, copied).result(timeout=5)
    assert signal['input_evidence_status'] == 'complete'
    assert copied[product]['input_evidence_status'] == 'recording'
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(write_from_other_thread, copied).result(timeout=5)
    assert snapshot(store.root) == before


@pytest.mark.parametrize('bad_product', ['IC','IM'])
def test_coordinator_never_partially_persists_pair_with_one_bad_capture(monkeypatch, tmp_path, bad_product):
    module, owner, current, signals, clock = _coordinator(tmp_path, monkeypatch)
    signals[bad_product]['input_evidence_status'] = 'incomplete'
    signals[bad_product]['persistence_eligible'] = True  # Independent marker must still win.
    before = snapshot(owner.store.root)
    text, _, observed = owner.execute_query('信号', clock)
    assert set(observed) == {'IC','IM'}
    assert 'calculated-IC' in text and 'calculated-IM' in text
    assert '尚不具备持久化资格' in text
    assert snapshot(owner.store.root) == before
    assert owner.store.load_latest()['digest'] == current['digest']


def test_failed_capture_receipt_without_manifest_must_show_original_archive_missing(monkeypatch, tmp_path):
    """A collector setup/write failure must not silently hide missing evidence."""
    _module, owner, current, _signals, clock = _coordinator(tmp_path, monkeypatch)
    root = tmp_path / 'never_sealed_evidence'
    owner.evidence_root = root
    folder = root / 'capture_receipts' / clock.date().isoformat()
    folder.mkdir(parents=True)
    (folder / 'failed-before-seal.json').write_text(json.dumps({
        'schema': 'icim_capture_runner_receipt_v1', 'started_at': clock.isoformat(),
        'finished_at': clock.isoformat(), 'status': 'incomplete',
        'capture': {'day': str(clock.date()), 'status': 'incomplete',
                    'error_type': 'OSError', 'error': 'synthetic no manifest could be published'},
        'ledger_paths_and_bytes_unchanged': True,
    }), encoding='utf-8')
    assert not (root / 'sessions').exists()
    before = snapshot(owner.store.root)
    text, _, _ = owner.execute_query('信号', clock, persist_confirmed=False)
    assert '存证缺失' in text and ('补证' in text or '人工锚定' in text)
    assert snapshot(owner.store.root) == before


def test_server_capture_exception_without_manifest_must_show_original_archive_missing(monkeypatch, tmp_path):
    _module, owner, _current, _signals, clock = _coordinator(tmp_path, monkeypatch)
    owner.evidence_root = tmp_path / 'server_capture_failed_before_seal'
    monkeypatch.setenv('ICIM_DISABLE_EVIDENCE_CAPTURE', '0')
    def fail(*_args, **_kwargs):
        raise OSError('synthetic input archive permission failure')
    monkeypatch.setattr(bot.evidence_adapter, 'capture_daily', fail)
    result = owner.capture_current_inputs(clock)
    assert result['status'] == 'incomplete' and 'permission' in result['reason']
    assert not (owner.evidence_root / 'sessions').exists()
    before = snapshot(owner.store.root)
    text, _, _ = owner.execute_query('信号', clock, persist_confirmed=False)
    assert '存证缺失' in text and ('补证' in text or '人工锚定' in text)
    assert snapshot(owner.store.root) == before


def test_waiting_for_close_receipt_is_not_missing_evidence_warning(monkeypatch, tmp_path):
    _module, owner, _current, _signals, clock = _coordinator(tmp_path, monkeypatch)
    clock = clock.replace(hour=10)
    root = tmp_path / 'waiting_original_archive'
    owner.evidence_root = root
    folder = root / 'capture_receipts' / clock.date().isoformat()
    folder.mkdir(parents=True)
    (folder / 'waiting.json').write_text(json.dumps({
        'schema': 'icim_capture_runner_receipt_v1', 'started_at': clock.isoformat(),
        'finished_at': clock.isoformat(), 'status': 'waiting_for_close',
        'ledger_paths_and_bytes_unchanged': True,
    }), encoding='utf-8')
    text, _, _ = owner.execute_query('信号', clock, persist_confirmed=False)
    assert '存证缺失' not in text


@pytest.mark.parametrize('defect', ['old_revision', 'missing_role', 'crossed_midnight'])
def test_daily_archive_status_does_not_claim_recovery_ready_for_unloadable_packets(monkeypatch, tmp_path, defect):
    """Legitimate capture API, deliberately incomplete synthetic role payloads.

    These are packet-routing tests, not claims of valid market facts or fills.
    The production loader rejects before consuming any role payload.
    """
    day = bot.date(2026, 10, 22)
    clock = datetime(2026, 10, 22, 23, 59, 50, tzinfo=bot.BEIJING)
    current = [clock]
    monkeypatch.setattr(evidence, '_now', lambda: current[0])
    for product in ['IC', 'IM']:
        current[0] = clock
        roles = {'market.live', 'cffex.quotes', 'rate.resolve', 'fear.score'}
        if product == 'IC':
            roles |= {'ic.months', 'ic.etf', 'ic.chain'}
        if defect == 'missing_role':
            roles.remove('market.live')
        metadata = {'product': product, 'original_close': True,
                    'input_revision': 'old-unreadable-revision' if defect == 'old_revision' else adapter.INPUT_REVISION}
        with evidence.capture_session(tmp_path, day, clock, metadata) as session:
            for role in sorted(roles):
                session.call(role, {}, lambda: {'synthetic_scope_only': True})
            if defect == 'crossed_midnight':
                current[0] = clock + timedelta(seconds=20)
        assert session.context['complete'] is True
        with pytest.raises(evidence.EvidenceUnavailable):
            adapter.load_original(bot, tmp_path, product, day)
    result = adapter.capture_day_status(tmp_path, day)
    assert result['status'] != 'captured'
    assert set(result['missing_products']) == {'IC', 'IM'}
    assert result['recovery_warning'] and '存证缺失' in result['recovery_warning']


def test_observer_exception_restores_global_output_and_observer(monkeypatch, tmp_path):
    _module, owner, current, _signals, clock = _coordinator(tmp_path, monkeypatch)
    output = bot.poe
    def bad_run(_self):
        raise RuntimeError('synthetic renderer crash')
    monkeypatch.setattr(bot.ICIMMainlinesBot, 'run', bad_run)
    before = snapshot(owner.store.root)
    with pytest.raises(RuntimeError, match='renderer crash'):
        owner.execute_query('信号', clock)
    assert bot.poe is output
    assert bot._SIGNAL_OBSERVER is None
    assert snapshot(owner.store.root) == before


def test_snapshot_waits_for_temporary_anchor_projection_and_reads_restored_identity(monkeypatch, tmp_path):
    store, current, _signals = prepare(tmp_path, monkeypatch)
    anchors = state.anchors_from_record(current)
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(anchors))
    ready, done = threading.Event(), threading.Event()
    rendered = []
    class Message:
        def write(self, value):
            rendered.append(value)
    def read():
        ready.set()
        bot._write_last_verified_snapshot(Message(), 'IM')
        done.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with bot.STRATEGY_QUERY_LOCK:
            bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_contract'] = 'UNVERIFIED_PROJECTION'
            future = pool.submit(read)
            assert ready.wait(3) and not done.wait(.05)
            bot.LIVE_CONTINUATION_ANCHOR['IM'] = deepcopy(anchors['IM'])
        future.result(timeout=5)
    assert rendered and 'UNVERIFIED_PROJECTION' not in ''.join(rendered)
    assert anchors['IM']['post_core_contract'] in ''.join(rendered)
