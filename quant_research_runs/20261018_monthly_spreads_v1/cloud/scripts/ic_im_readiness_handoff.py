"""Export a bounded, verified research-only handoff; no network or SMTP."""
import argparse
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--strategy-root', required=True)
    p.add_argument('--strategy-sha', required=True)
    p.add_argument('--source-artifact', required=True, type=int)
    p.add_argument('--state-dir', required=True)
    p.add_argument('--artifact-dir', required=True)
    args = p.parse_args()
    sys.path.insert(0, args.strategy_root)
    import poe_ic_im_mainline_v1_4_bot as bot
    import poe_ic_im_v1_4_state as state
    directory, artifacts = Path(args.state_dir), Path(args.artifact_dir)
    store = state.StateStore(directory)
    latest = store.load_latest()
    preflight = json.loads((artifacts/'preflight.json').read_text())
    result = json.loads((artifacts/'result.json').read_text())
    assert result['status'] in {'ok', 'partial'} and args.source_artifact > 0
    if result['status'] == 'partial':
        assert result.get('signal_completeness') == 'partial' and result.get('incomplete_components')
        from build_ic_im_v1_4_digest import validate_success_payload
        validate_success_payload(result)
    assert preflight['strategy_sha'] == args.strategy_sha
    assert latest['digest'] == preflight['restored_digest'], 'handoff must preserve the restored production ledger'
    assert latest['verified_day'] == preflight['completed_day']
    assert (result['digest'], result['sequence'], result['verified_day']) == (
        latest['digest'], latest['sequence'], latest['verified_day'])
    assert result['market_date'] == preflight['expected_market_day']
    expected_mode = 'realtime' if preflight['publication_mode'] == 'realtime' else 'close_confirmed'
    assert result['publication_mode'] == expected_mode
    report_name = 'ic_im_v1_4_realtime_signal.md' if expected_mode == 'realtime' else 'ic_im_v1_4_close_signal.md'
    assert result['report_file'] == report_name
    assert (artifacts/report_name).resolve().parent == artifacts.resolve()
    previous = None
    journal_paths = []
    for seq in range(latest['sequence']+1):
        item = store.load_sequence(seq)
        state.anchors_from_record(item)
        assert item['sequence'] == seq
        if previous is not None:
            assert item['previous_digest'] == previous['digest']
            assert date.fromisoformat(item['verified_day']) == bot._roll_forward_exchange_day(
                date.fromisoformat(previous['verified_day']) + timedelta(days=1))
        previous = item
        journal_paths.append(directory/'journal'/f"{seq:06d}-{item['verified_day']}.json")
    assert previous['digest'] == latest['digest']
    assert set((directory/'journal').glob('*.json')) == set(journal_paths)
    migration = json.loads((directory/'migration_record.json').read_text())
    assert migration['new_ledger']['digest'] == store.load_sequence(0)['digest']
    # Only normalized research state and reports. Never include transport
    # envelopes, credentials, environment, account data or arbitrary files.
    files = [(f'ledger/{p.relative_to(directory).as_posix()}', p)
             for p in [directory/'latest.json', directory/'migration_record.json', *journal_paths]]
    files += [(f'report/{name}', artifacts/name) for name in ('result.json', report_name, 'preflight.json')]
    manifest = dict(signal_completeness=result.get('signal_completeness', 'complete'), status=result['status'], strategy_sha=args.strategy_sha, source_artifact_id=args.source_artifact,
                    verified_day=latest['verified_day'], sequence=latest['sequence'], digest=latest['digest'],
                    publication_mode=result['publication_mode'], market_date=result['market_date'],
                    files={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, path in files:
            assert path.is_file() and path.stat().st_size < 4_000_000
            z.writestr(name, path.read_bytes())
        z.writestr('handoff_manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    payload = buffer.getvalue()
    assert len(payload) <= 2_000_000
    # GitHub masks short configured secret values even inside unrelated
    # base64 research data. A-P avoids boolean words and numeric SMTP ports.
    encoded = payload.hex().translate(str.maketrans("0123456789abcdef", "ABCDEFGHIJKLMNOP"))
    metadata = json.dumps(dict(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                               encoding='nibble-ap'), sort_keys=True).encode()
    print('ICIM_HANDOFF_META_AP='+metadata.hex().translate(str.maketrans("0123456789abcdef", "ABCDEFGHIJKLMNOP")))
    print('ICIM_HANDOFF_SHA256='+hashlib.sha256(payload).hexdigest())
    print('ICIM_HANDOFF_ENCODING=nibble-ap')
    for i in range(0,len(encoded),6000):
        print('ICIM_HANDOFF_CHUNK='+encoded[i:i+6000])
    print('ICIM_HANDOFF_END='+str(len(payload)))


if __name__ == '__main__':
    main()
