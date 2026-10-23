"""Final audit run with explicit scope exceptions and independently tested strict gate."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys

from evidence_gate import validate_run, validate_receipt

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STAGE = os.environ.get('ICIM_FINAL_STAGE', 'accepted_final')
FINAL = HERE / STAGE
FINAL.mkdir(exist_ok=False)

def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()

def sources():
    paths = list(ROOT.glob('*.py')) + list(HERE.glob('*.py'))
    paths += [HERE/'pytest.ini']
    paths += list((ROOT / 'docs').glob('*_spec.md')) + list((ROOT / 'docs').glob('*_spec.md.sha256'))
    for folder in ['icim_adversarial', 'icim_external_review_20261021', 'icim_external_review_round2_20261021']:
        paths += [p for p in (ROOT / 'tests/fixtures' / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(paths))}

excluded = {
    'test_icim_redteam_integration_20261021.py::test_frozen_source_receipt_matches_current_bytes':
        'Historical source equality is checked against its pre-repair backup; old receipt is not rewritten.',
    'test_icim_round2_audit_lifecycle_20261021.py::test_historical_adapter_retains_id_already_bound_to_requested_series':
        'Capability probe only: a price series by ID does not establish historical code/strike/unit; reject without dated identity.',
    'test_icim_round2_audit_lifecycle_20261021.py::test_legal_standard_core_replay_crosses_strict_boundary':
        'Same missing-dated-identity capability probe; unsafe ID copying is deliberately not implemented.',
}
before = sources()
(FINAL / 'source_before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
args = [arg for node in excluded for arg in ['--deselect', node]]
env = dict(os.environ, ICIM_AUDIT_STAGE=STAGE, PYTHONIOENCODING='utf-8')
started = datetime.now(timezone.utc).isoformat()
process = subprocess.run([sys.executable, str(HERE / 'run_all.py'), *args], cwd=ROOT, env=env, timeout=240)
after = sources()
(FINAL / 'source_after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
try:
    counts = validate_run(FINAL / 'regression.xml', process_exit_code=process.returncode,
                          source_before=before, source_after=after)
except RuntimeError as exc:
    (FINAL / 'rejected.json').write_text(json.dumps(dict(reason=str(exc), exit_code=process.returncode), indent=2), encoding='utf-8')
    raise
receipt = dict(started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
               exit_code=process.returncode, source_before_equals_after=before == after,
               source_files=len(before), counts=counts, deselected=excluded,
               artifacts={name: sha(FINAL / name) for name in ['regression.xml', 'regression.txt',
                           'source_before.json', 'source_after.json', 'test_run.json', 'collection.json']})
(FINAL / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
try:
    validate_receipt(FINAL)
except RuntimeError as exc:
    (FINAL/'receipt.json').rename(FINAL/'rejected_receipt.json')
    (FINAL/'rejected.json').write_bytes((json.dumps(dict(reason=str(exc), exit_code=process.returncode),indent=2)+'\n').encode())
    raise
print(json.dumps(receipt, indent=2))
