"""Verify the final repair evidence, unchanged archives and explicit test scope."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import icim_delivery_test_evidence as gate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--daily', required=True)
    parser.add_argument('--cumulative', required=True)
    parser.add_argument('--manifest', required=True)
    opts = parser.parse_args()
    manifest = json.loads((ROOT / opts.manifest).read_text('utf-8'))
    source = {name: sha(ROOT / name) for name in manifest['files']}
    assert source == manifest['files']
    results = {}
    for kind, name in [('daily', opts.daily), ('cumulative', opts.cumulative)]:
        folder = HERE / name
        gate.validate_receipt(folder)
        receipt = json.loads((folder / 'receipt.json').read_text('utf-8'))
        bindings = json.loads((folder / 'source_after.json').read_text('utf-8'))
        assert all(bindings.get(n) == h for n, h in source.items())
        assert all(sha(ROOT / n) == h for n, h in bindings.items())
        collection = json.loads((folder / 'collection.json').read_text('utf-8'))
        excluded = json.loads((ROOT / 'docs/icim_historical_deselections_20261023.json').read_text('utf-8'))
        expected = set(excluded) if kind == 'cumulative' else set(manifest['daily_gate_deselections'])
        assert set(collection['deselected']) == expected
        results[kind] = {'run': name, 'counts': receipt['counts'], 'deselected': len(expected),
                         'receipt_sha256': sha(folder / 'receipt.json'), 'all_bound_files_still_match': True}
    baseline = json.loads((HERE / 'baseline.json').read_text('utf-8'))
    changed = [n for n, h in baseline['files'].items() if not (ROOT / n).is_file() or sha(ROOT / n) != h]
    # No changes to frozen documents, formal outputs/ledgers, or earlier audit.
    forbidden = [n for n in changed if n.startswith(('runtime/', 'outputs/', 'docs/', 'quant_research_runs/20261023_latest_redteam_v2/'))]
    assert not forbidden, forbidden
    original_paths = set(baseline['files'])
    additions = {d: [p.relative_to(ROOT).as_posix() for p in (ROOT / d).rglob('*')
                     if p.is_file() and p.relative_to(ROOT).as_posix() not in original_paths]
                 for d in ['runtime', 'outputs']}
    assert not any(additions.values()), additions
    result = {'status': 'PASS', 'baseline_commit': baseline['head'], 'current_head': subprocess.check_output(
              ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'source_sha256': source,
              'test_results': results, 'protected_original_count': len(baseline['files']),
              'intentional_modified_originals': changed, 'protected_data_or_audit_changes': forbidden,
              'new_runtime_or_output_files': additions, 'manifest': opts.manifest, 'manifest_sha256': sha(ROOT / opts.manifest)}
    (HERE / 'final_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'tests': results, 'changed_originals': changed}, ensure_ascii=False))


if __name__ == '__main__':
    main()
