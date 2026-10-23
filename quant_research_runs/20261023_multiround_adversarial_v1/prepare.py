from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
PREV = ROOT / 'quant_research_runs/20261023_production_provenance_v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(folder):
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in folder.rglob('*')
            if p.is_file() and p.name != '.capture.lock'}


assert not (RUN / 'baseline.json').exists()
pin = json.loads((PREV / 'delivery/capture_source_pin.json').read_text(encoding='utf-8'))
for name, digest in pin['files'].items():
    assert sha(ROOT / name) == digest, name
    dest = RUN / 'backup' / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, dest)
dest = RUN / 'backup/notes/mainline_registry.md'
dest.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(ROOT / 'notes/mainline_registry.md', dest)
for name in ['run_bound.py', 'run_checks.py', 'evidence_gate.py', 'pytest.ini',
             'legacy_fixture_adaptations.json', 'exclusions.json']:
    shutil.copyfile(PREV / name, RUN / name)
spec = ROOT / 'docs/ic_im_multiround_adversarial_20261023_v1_spec.md'
Path(str(spec) + '.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
baseline = {'created_at': datetime.now().astimezone().isoformat(),
    'production': pin['files'], 'runtime': snapshot(ROOT / 'runtime'),
    'original_evidence': snapshot(ROOT / 'data/icim_v14_original_inputs'),
    'previous_tests': json.loads((PREV / 'accepted_01/test_run.json').read_text(encoding='utf-8'))['files'],
    'previous_binding': json.loads((PREV / 'accepted_01/source_after.json').read_text(encoding='utf-8')),
    'frozen_outputs': snapshot(ROOT / 'outputs'), 'spec_sha256': sha(spec),
    'registry_before_sha256': sha(ROOT / 'notes/mainline_registry.md')}
(RUN / 'baseline.json').write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: len(baseline[k]) for k in ['production','runtime','original_evidence','frozen_outputs']}))
