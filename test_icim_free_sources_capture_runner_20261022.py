"""Capture-only CLI audit: temporary migrated ledgers, synthetic adapter outcomes."""
from copy import deepcopy
from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import capture_ic_im_v1_4_inputs as runner
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_round3_recovery_audit_20261021 as seed

ROOT = Path(__file__).resolve().parent
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('collector runner unit test attempted network'))
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))


def migrated(tmp_path):
    store, record = seed.temporary_store(tmp_path / 'state', date(2026, 10, 21))
    proof = {key: record[key] for key in ['digest', 'schema_version', 'sequence', 'strategy_revision', 'strategy_version']}
    (store.root / 'migration_record.json').write_text(json.dumps({'new_ledger': proof}), encoding='utf-8')
    return store, record


def fixture_runner(tmp_path):
    store, record = migrated(tmp_path)
    owner = runner.CaptureRunner(store.root, tmp_path / 'evidence', workspace=tmp_path)
    return owner, store, record


def test_capture_only_loads_real_readonly_chain_and_never_calls_writers(tmp_path, monkeypatch):
    owner, store, record = fixture_runner(tmp_path)
    before = runner.ledger_hashes(store.root)
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    called = []
    def forbidden(*a, **k):
        pytest.fail('capture-only entry called a ledger writer')
    monkeypatch.setattr(state.StateStore, 'initialize', forbidden)
    monkeypatch.setattr(state.StateStore, 'append_confirmed_signals', forbidden)
    monkeypatch.setattr(state.StateStore, '_atomic_write', forbidden)
    def capture(strategy, output, clock):
        assert clock == CLOCK and output == owner.evidence_dir
        assert strategy.LIVE_CONTINUATION_ANCHOR['IC']['post_put_security_id'] == record['products']['IC']['post_put_security_id']
        assert strategy.LIVE_CONTINUATION_ANCHOR['IC']['last_verified_day'] == date(2026, 10, 21)
        called.append(clock)
        return {'status': 'captured', 'day': str(clock.date()), 'products': {}}
    monkeypatch.setattr(adapter, 'capture_daily', capture)
    result = owner.cycle(CLOCK)
    assert result['status'] == 'captured'
    assert result['ledger_paths_and_bytes_unchanged'] is True
    assert result['ledger_write_requested'] is False and result['message_send_requested'] is False
    assert result['ledger']['digest'] == record['digest']
    assert runner.ledger_hashes(store.root) == before
    assert bot.LIVE_CONTINUATION_ANCHOR == original
    assert called == [CLOCK]
    path = owner.write_receipt(result)
    assert json.loads(path.read_text(encoding='utf-8')) == result


def test_successful_bucket_is_reused_but_next_rate_bucket_is_collected(tmp_path, monkeypatch):
    owner, _, _ = fixture_runner(tmp_path)
    calls = []
    monkeypatch.setattr(adapter, 'capture_daily', lambda s, p, c: calls.append(c) or {'status': 'captured'})
    first = CLOCK.replace(hour=16)
    assert owner.cycle(first)['status'] == 'captured'
    assert owner.cycle(first.replace(minute=5))['status'] == 'already_captured'
    assert owner.cycle(CLOCK)['status'] == 'captured'
    assert calls == [first, CLOCK]


def test_incomplete_capture_retries_instead_of_deduplicating(tmp_path, monkeypatch):
    owner, _, _ = fixture_runner(tmp_path)
    statuses = iter(['incomplete', 'captured'])
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: {'status': next(statuses)})
    assert owner.cycle(CLOCK)['status'] == 'incomplete'
    assert owner.cycle(CLOCK)['status'] == 'captured'


@pytest.mark.parametrize('clock,expected', [
    (CLOCK.replace(hour=14), 'waiting_for_close'),
    (CLOCK.replace(day=24), 'waiting_non_trading_day'),
    (CLOCK.replace(year=2027, month=1, day=4), 'waiting_for_official_calendar'),
])
def test_non_sessions_and_calendar_gaps_are_recorded_without_network(tmp_path, monkeypatch, clock, expected):
    owner, _, _ = fixture_runner(tmp_path)
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: pytest.fail('not a covered completed session'))
    result = owner.cycle(clock)
    assert result['status'] == expected
    assert result['ledger_paths_and_bytes_unchanged'] is True


@pytest.mark.parametrize('missing', ['latest.json', 'migration_record.json'])
def test_missing_ledger_or_migration_does_not_bootstrap(tmp_path, monkeypatch, missing):
    owner, store, _ = fixture_runner(tmp_path)
    (store.root / missing).unlink()
    before = runner.ledger_hashes(store.root)
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: pytest.fail('invalid ledger cannot capture'))
    assert owner.cycle(CLOCK)['status'] == 'incomplete'
    assert runner.ledger_hashes(store.root) == before
    assert not (store.root / missing).exists()


def test_wrong_migration_genesis_blocks_capture(tmp_path, monkeypatch):
    owner, store, _ = fixture_runner(tmp_path)
    path = store.root / 'migration_record.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['new_ledger']['digest'] = 'a' * 64
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: pytest.fail('wrong migration cannot capture'))
    result = owner.cycle(CLOCK)
    assert result['status'] == 'incomplete'
    assert 'migration proof' in result['error']


def pin(tmp_path):
    source = tmp_path / 'module.py'
    source.write_text('value = 1\n', encoding='utf-8')
    path = tmp_path / 'pin.json'
    path.write_text(json.dumps({'schema': runner.PIN_SCHEMA, 'files': {'module.py': runner.sha(source)}}), encoding='utf-8')
    return source, path


def test_source_pin_refuses_changed_module(tmp_path):
    source, path = pin(tmp_path)
    pinned = runner.SourcePin(path, tmp_path)
    source.write_text('value = 2\n', encoding='utf-8')
    with pytest.raises(runner.SourcePinError, match='module drift'):
        pinned.verify()


def test_source_pin_cannot_be_silently_replaced_in_running_process(tmp_path):
    _, path = pin(tmp_path)
    pinned = runner.SourcePin(path, tmp_path)
    path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    with pytest.raises(runner.SourcePinError, match='manifest changed'):
        pinned.verify()


def test_pin_must_cover_all_loaded_runtime_modules(tmp_path):
    _, path = pin(tmp_path)
    with pytest.raises(runner.SourcePinError, match='does not cover'):
        runner.SourcePin(path, tmp_path).verify({'unlisted.py': 'a' * 64})


def test_deleted_manifest_is_a_fatal_pin_error(tmp_path):
    _, path = pin(tmp_path)
    pinned = runner.SourcePin(path, tmp_path)
    path.unlink()
    with pytest.raises(runner.SourcePinError, match='unreadable'):
        pinned.verify()


def test_unreadable_loaded_source_is_a_fatal_pin_error(tmp_path, monkeypatch):
    owner, _, _ = fixture_runner(tmp_path)
    def unreadable():
        raise FileNotFoundError('injected module deletion')
    monkeypatch.setattr(runner, 'loaded_sources', unreadable)
    with pytest.raises(runner.SourcePinError, match='unreadable'):
        owner.cycle(CLOCK)


@pytest.mark.parametrize('name', ['../escape.py', 'module.txt', '/absolute.py'])
def test_source_pin_rejects_untrusted_paths(tmp_path, name):
    path = tmp_path / 'pin.json'
    path.write_text(json.dumps({'schema': runner.PIN_SCHEMA, 'files': {name: 'a' * 64}}), encoding='utf-8')
    with pytest.raises(runner.SourcePinError):
        runner.SourcePin(path, tmp_path)


def test_duplicate_pin_keys_are_rejected(tmp_path):
    path = tmp_path / 'pin.json'
    path.write_text('{"schema":"' + runner.PIN_SCHEMA + '","files":{"a.py":"' + 'a'*64 + '","a.py":"' + 'b'*64 + '"}}', encoding='utf-8')
    with pytest.raises(runner.SourcePinError, match='duplicate'):
        runner.SourcePin(path, tmp_path)


def test_loaded_source_drift_stops_before_next_collection(tmp_path, monkeypatch):
    owner, _, _ = fixture_runner(tmp_path)
    values = {'runtime.py': 'a' * 64}
    monkeypatch.setattr(runner, 'loaded_sources', lambda: dict(values))
    monkeypatch.setattr(adapter, 'capture_daily', lambda *a: {'status': 'captured'})
    assert owner.cycle(CLOCK)['status'] == 'captured'
    values['runtime.py'] = 'b' * 64
    with pytest.raises(runner.SourcePinError):
        owner.cycle(CLOCK)


def test_loop_retries_expected_waits_and_is_finite_when_requested(tmp_path, monkeypatch):
    owner, _, _ = fixture_runner(tmp_path)
    statuses = iter(['waiting_for_official_calendar', 'incomplete', 'captured'])
    monkeypatch.setattr(owner, 'cycle', lambda: {'status': next(statuses), 'started_at': CLOCK.isoformat()})
    waits = []
    assert owner.run(loop=True, max_rounds=3, wait=waits.append) == 0
    assert waits == [300, 300]
    receipts = list(owner.evidence_dir.glob('capture_receipts/*/*.json'))
    assert len(receipts) == 3


def test_source_pin_mismatch_ends_daemon_and_writes_machine_receipt(tmp_path, monkeypatch):
    owner, _, _ = fixture_runner(tmp_path)
    def drift():
        raise runner.SourcePinError('injected source drift')
    monkeypatch.setattr(owner, 'cycle', drift)
    assert owner.run(loop=True, wait=lambda *a: pytest.fail('drifting source must stop')) == 2
    receipts = list(owner.evidence_dir.glob('capture_receipts/*/*.json'))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding='utf-8'))['status'] == 'source_pin_mismatch'


def test_evidence_and_ledger_trees_cannot_overlap(tmp_path):
    store, _ = migrated(tmp_path)
    for output in [store.root, store.root / 'input_evidence', tmp_path]:
        with pytest.raises(RuntimeError):
            runner.CaptureRunner(store.root, output, workspace=tmp_path)
    assert not (store.root / 'input_evidence').exists()


def test_nonexistent_state_is_not_created(tmp_path):
    path = tmp_path / 'missing_state'
    with pytest.raises(OSError):
        runner.CaptureRunner(path, tmp_path / 'evidence', workspace=tmp_path)
    assert not path.exists()


def test_process_mutex_rejects_another_process_and_releases_after_exit(tmp_path):
    target = tmp_path / 'evidence'
    code = ('from pathlib import Path; import sys; import capture_ic_im_v1_4_inputs as r; '
            'lock=r.capture_lock(Path(sys.argv[1])); '
            '\ntry:\n lock.__enter__()\nexcept r.CaptureAlreadyRunning:\n sys.exit(3)\nelse:\n lock.__exit__(None,None,None)\n sys.exit(0)')
    with runner.capture_lock(target):
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code, str(target)],
                                cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=15)
        assert result.returncode == 3, result.stderr
    with runner.capture_lock(target):
        pass


def test_help_has_no_state_or_server_side_effects(tmp_path):
    state_path = tmp_path / 'must_not_exist'
    env = dict(os.environ, ICIM_STATE_DIR=str(state_path))
    result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(ROOT / 'capture_ic_im_v1_4_inputs.py'), '--help'],
                            cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0 and '--source-pin' in result.stdout
    assert not state_path.exists()
