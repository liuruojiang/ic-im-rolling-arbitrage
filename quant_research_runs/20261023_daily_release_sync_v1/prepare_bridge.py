"""Produce bounded bridge payloads for the connected GitHub publisher."""
from pathlib import Path
import base64
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
STAGE = Path('D:/workspace/icim_release_20261023/strategy')
manifest = json.loads((STAGE / 'docs/ic_im_daily_release_20261023_manifest.json').read_text(encoding='utf-8'))
names = set(manifest['files']) | set(manifest['fixture_sha256']) | set(manifest['required_local_tests']) | set(manifest['required_test_helpers'])
names |= {'run_ic_im_v1_4_delivery_tests.py', '_icim_delivery_test_worker.py', 'icim_delivery_test_evidence.py',
          'docs/ic_im_daily_release_20261023_manifest.json', 'docs/icim_delivery_pytest.ini',
          'docs/icim_delivery_legacy_fixtures_20261023.json', 'docs/icim_historical_deselections_20261023.json',
          'notes/mainline_registry.md', 'docs/ic_im_v1_4_github_gmail_runbook.md',
          'docs/ic_im_daily_release_sync_20261023_v1_spec.md', 'docs/ic_im_daily_release_sync_20261023_v1_spec.md.sha256',
          'docs/ic_im_multiround_adversarial_20261023_v1_spec.md', 'docs/ic_im_multiround_adversarial_20261023_v1_spec.md.sha256',
          'docs/ic_im_multiround_audit_20261023.md'}
folder = RUN / 'bridge'
folder.mkdir(exist_ok=True)
entries = []
for index, name in enumerate(sorted(names)):
    raw = (STAGE / name).read_bytes()
    encoded = base64.b64encode(raw).decode('ascii')
    pieces = [encoded[x:x+40000] for x in range(0, len(encoded), 40000)] or ['']
    parts = []
    blob_sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    for offset, text in enumerate(pieces):
        path = folder / f'{index:03d}_{offset:03d}.json'
        path.write_text(json.dumps({'path': name, 'part': offset, 'parts': len(pieces),
                                   'git_blob_sha': blob_sha, 'content': text}), encoding='utf-8')
        parts.append(path.relative_to(ROOT).as_posix())
    entries.append({'path': name, 'git_blob_sha': blob_sha, 'sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes': len(raw), 'parts': parts})
(RUN / 'strategy_publish_inventory.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'files': len(entries), 'bytes': sum(e['bytes'] for e in entries), 'parts': sum(len(e['parts']) for e in entries)}))
