"""Verify a GitHub no-send research handoff before any local restoration."""
import argparse
import base64
from datetime import datetime, date
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    if not __debug__:
        raise RuntimeError('verification requires Python without optimization')
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True)
    p.add_argument('--run', required=True, type=int)
    p.add_argument('--artifact', required=True, type=int)
    p.add_argument('--strategy-sha', required=True)
    p.add_argument('--mode', choices=['close_confirmed', 'realtime'], required=True)
    p.add_argument('--install', action='store_true')
    args = p.parse_args()
    wire = json.loads(Path(args.input).read_text(encoding='utf-8'))
    assert wire['encoding'] == 'nibble-ap'
    encoded = wire['encoded']
    assert re.fullmatch('[A-P]+', encoded) and len(encoded) % 2 == 0
    raw = bytes.fromhex(encoded.translate(str.maketrans('ABCDEFGHIJKLMNOP', '0123456789abcdef')))
    assert len(raw) == wire['bytes'] <= 2_000_000 and sha(raw) == wire['sha256']
    destination = HERE / f'handoff-{args.run}'
    assert not destination.exists(), 'do not overwrite prior handoff evidence'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items = archive.infolist()
        names = [x.filename for x in items]
        assert len(names) == len(set(names)) and len(items) < 1000
        assert sum(item.file_size for item in items) < 16_000_000
        for item in items:
            path = PurePosixPath(item.filename)
            assert not path.is_absolute() and '..' not in path.parts and '\\' not in item.filename
            assert item.file_size < 4_000_000 and not item.is_dir()
            assert (item.external_attr >> 16) & 0o170000 != 0o120000
        content = {item.filename: archive.read(item) for item in items}
    manifest = json.loads(content.pop('handoff_manifest.json'))
    assert set(content) == set(manifest['files'])
    assert re.fullmatch('[0-9a-f]{40}', args.strategy_sha)
    assert manifest['strategy_sha'] == args.strategy_sha
    assert manifest['source_artifact_id'] == args.artifact
    assert manifest['publication_mode'] == args.mode
    for name, data in content.items():
        assert sha(data) == manifest['files'][name]
        assert re.fullmatch(r'ledger/(latest.json|migration_record.json|journal/\d{6}-\d{4}-\d{2}-\d{2}.json)|report/(result.json|preflight.json|ic_im_v1_4_(close|realtime)_signal.md)', name)
    destination.mkdir()
    (destination/'handoff.zip').write_bytes(raw)
    (destination/'handoff_manifest.json').write_text(json.dumps(manifest, indent=2))
    for name, data in content.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    os.environ.update(ICIM_STATE_DIR=str(destination/'ledger'), ICIM_REQUIRE_MIGRATION='1')
    formal = HERE/'formal_runtime'
    release = json.loads((formal/'release_manifest.json').read_text(encoding='utf-8'))
    assert release['strategy_sha'] == args.strategy_sha
    for name, expected in release['files'].items():
        assert '/' not in name and '\\' not in name and name.endswith('.py')
        assert sha((formal/name).read_bytes()) == expected
    sys.path.insert(0, str(formal))
    import poe_ic_im_mainline_v1_4_bot as bot
    import poe_ic_im_v1_4_state as state
    store = state.StateStore(destination/'ledger')
    latest = store.load_latest()
    expected_journals = set()
    for sequence in range(latest['sequence']+1):
        record = store.load_sequence(sequence)
        state.anchors_from_record(record)
        expected_journals.add(f"{sequence:06d}-{record['verified_day']}.json")
    assert {p.name for p in (destination/'ledger/journal').iterdir()} == expected_journals
    proof = json.loads((destination/'ledger/migration_record.json').read_text())
    assert proof['new_ledger']['digest'] == store.load_sequence(0)['digest']
    assert proof['new_ledger']['strategy_revision'] == 'r1'
    assert proof['new_ledger']['schema_version'] == 4
    assert (latest['digest'], latest['sequence'], latest['verified_day']) == (
        manifest['digest'], manifest['sequence'], manifest['verified_day'])
    now = datetime.now(bot.BEIJING)
    assert latest['verified_day'] == bot._latest_completed_exchange_day(now).isoformat()
    result = json.loads(content['report/result.json'])
    preflight = json.loads(content['report/preflight.json'])
    assert preflight['strategy_sha'] == manifest['strategy_sha']
    assert preflight['restored_digest'] == latest['digest']
    assert preflight['completed_day'] == latest['verified_day']
    assert preflight['expected_market_day'] == result['market_date']
    assert preflight['publication_mode'] == ('realtime' if args.mode == 'realtime' else 'close')
    assert result['status'] in {'ok', 'partial'} and result['digest'] == latest['digest']
    if result['status'] == 'partial':
        assert result.get('signal_completeness') == 'partial' and result.get('incomplete_components')
        for signal in result['signals'].values():
            if signal.get('grid_action') == 'UNCONFIRMED':
                assert signal.get('fear_greed_index') is None
                assert signal.get('grid_target') is None and signal.get('total_units_target') is None
    assert result['market_date'] == manifest['market_date'] and result['publication_mode'] == args.mode
    if args.mode == 'realtime':
        assert result['market_date'] == now.date().isoformat() and bot._market_phase(now) == '\u76d8\u4e2d'
        assert set(result['signals']) == {'IC', 'IM'}
        assert all(str(s['market_date'])[:10] == now.date().isoformat() for s in result['signals'].values())
    receipt = dict(run_id=args.run, source_artifact_id=args.artifact, zip_sha256=sha(raw),
                   status=result['status'], signal_completeness=result.get('signal_completeness','complete'),
                   strategy_sha=manifest['strategy_sha'], digest=latest['digest'], sequence=latest['sequence'],
                   verified_day=latest['verified_day'], market_date=result['market_date'],
                   publication_mode=args.mode, full_chain_and_migration_verified=True,
                   source='official production ledger restored in isolated no-send cloud workflow', installed=False)
    if args.install:
        live = (ROOT/'runtime/ic_im_v1_4_r1').resolve()
        assert live.parent == (ROOT/'runtime').resolve() and live.name == 'ic_im_v1_4_r1'
        backup = HERE/'backups'/f'local_ledger_before_restore_{args.run}'
        assert not backup.exists()
        staging = live.parent/f'ic_im_v1_4_r1.verified-{args.run}'
        retired = live.parent/f'ic_im_v1_4_r1.retired-{args.run}'
        assert not staging.exists() and not retired.exists()
        shutil.copytree(destination/'ledger', staging)
        assert state.StateStore(staging).load_latest()['digest'] == latest['digest']
        # Hold the production writer's actual lock throughout backup and whole
        # chain replacement. Keep that lock at its original path on Windows.
        with state.StateStore(live)._exclusive_lock():
            old = state.StateStore(live).load_latest()
            assert date.fromisoformat(old['verified_day']) <= date.fromisoformat(latest['verified_day'])
            shutil.copytree(live, backup, ignore=shutil.ignore_patterns('.ledger.lock'))
            old_files = {x.relative_to(live).as_posix():sha(x.read_bytes()) for x in live.rglob('*.json')}
            assert old_files == {x.relative_to(backup).as_posix():sha(x.read_bytes()) for x in backup.rglob('*.json')}
            retired.mkdir()
            members = ['journal', 'latest.json', 'migration_record.json']
            moved_old, moved_new = [], []
            try:
                for name in members:
                    (live/name).rename(retired/name)
                    moved_old.append(name)
                for name in members:
                    (staging/name).rename(live/name)
                    moved_new.append(name)
                assert state.StateStore(live).load_latest()['digest'] == latest['digest']
            except BaseException:
                for name in reversed(moved_new):
                    (live/name).rename(staging/name)
                for name in reversed(moved_old):
                    (retired/name).rename(live/name)
                raise
        receipt.update(installed=True, backup=str(backup), retired=str(retired),
                       previous_digest=old['digest'], production_writer_lock_held=True)
    (destination/'verification.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
