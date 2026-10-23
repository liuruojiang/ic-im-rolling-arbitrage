"""Freeze this round's inputs before modifying production code."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
PREVIOUS = ROOT / 'quant_research_runs/20261022_evidence_conflict_review_v1'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def hashes(folder):
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in folder.rglob('*')
            if p.is_file() and p.name != '.capture.lock'}


assert not (RUN / 'baseline.json').exists()
backup = RUN / 'backup'
backup.mkdir(exist_ok=False)
names = ['ic_im_signal_evidence_v1.py', 'notes/mainline_registry.md']
for name in names:
    target = backup / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)
for name in ['run_bound.py', 'run_checks.py', 'evidence_gate.py', 'pytest.ini',
             'legacy_fixture_adaptations.json', 'exclusions.json']:
    shutil.copyfile(PREVIOUS / name, RUN / name)
spec = ROOT / 'docs/ic_im_evidence_http_date_20261022_v1_spec.md'
Path(str(spec) + '.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
pin = json.loads((PREVIOUS / 'delivery/capture_source_pin.json').read_text(encoding='utf-8'))
baseline = {'created_at': datetime.now().astimezone().isoformat(),
            'backups': {n: sha(backup / n) for n in names},
            'runtime': hashes(ROOT / 'runtime'),
            'evidence': hashes(ROOT / 'data/icim_v14_original_inputs'),
            'production': {n: sha(ROOT / n) for n in pin['files']},
            'previous_tests': json.loads((PREVIOUS / 'accepted_03/test_run.json').read_text(encoding='utf-8'))['files'],
            'previous_bindings': json.loads((PREVIOUS / 'accepted_03/source_after.json').read_text(encoding='utf-8')),
            'previous_spec_sha256': sha(ROOT / 'docs/ic_im_evidence_conflict_review_20261022_v1_spec.md'),
            'spec_sha256': sha(spec)}
(RUN / 'baseline.json').write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'backups': names, 'runtime_files': len(baseline['runtime']),
                  'evidence_files': len(baseline['evidence']), 'source_pin_files': len(pin['files'])}))
