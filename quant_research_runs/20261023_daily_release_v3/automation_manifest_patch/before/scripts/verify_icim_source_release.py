"""Verify the checked-out immutable ICIM release before running a producer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


def verify(root, expected):
    root = Path(root).resolve(strict=True)
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('expected strategy SHA must be immutable')
    observed = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if observed != expected:
        raise ValueError('strategy checkout does not match the production pin')
    relative = 'docs/ic_im_daily_release_20261023_manifest.json'
    raw = (root / relative).read_bytes()
    committed = subprocess.check_output(['git', '-C', str(root), 'show', expected + ':' + relative])
    if raw != committed:
        raise ValueError('release manifest differs from the pinned commit')
    manifest = json.loads(raw)
    files = manifest.get('files')
    if (manifest.get('schema') != 'icim_audited_daily_release_v1' or not isinstance(files, dict)
            or len(files) != 29 or 'run_ic_im_v1_4_github_digest.py' not in files
            or 'ic_im_signal_evidence_v1.py' not in files or 'ic_im_evidence_adapter_v1.py' not in files):
        raise ValueError('incomplete execution inventory')
    actual = {}
    for name, digest in files.items():
        path = Path(name)
        if (path.is_absolute() or '..' in path.parts or path.suffix != '.py'
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('invalid source inventory entry')
        resolved = (root / path).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError('source path escapes the strategy checkout')
        actual[name] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual[name] != digest:
            raise ValueError('source fingerprint mismatch: ' + name)
    return {'status': 'verified', 'release': manifest['release'], 'strategy_sha': expected,
            'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': actual,
            'verified_at': datetime.now(timezone.utc).isoformat()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--strategy-root', required=True)
    p.add_argument('--expected-strategy-sha', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    proof = verify(args.strategy_root, args.expected_strategy_sha)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': proof['status'], 'release': proof['release'],
                      'strategy_sha': proof['strategy_sha'], 'files': len(proof['source_sha256'])}))


if __name__ == '__main__':
    main()

