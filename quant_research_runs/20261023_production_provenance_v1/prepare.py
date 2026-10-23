from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
PREV = ROOT / 'quant_research_runs/20261022_evidence_http_date_v1'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def hashes(folder):
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in folder.rglob('*')
            if p.is_file() and p.name != '.capture.lock'}


assert not (RUN / 'baseline.json').exists()
names = ['poe_ic_im_mainline_v1_4_bot.py', 'ic_im_evidence_adapter_v1.py', 'notes/mainline_registry.md']
for name in names:
    dest = RUN / 'backup' / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, dest)
for name in ['run_bound.py', 'run_checks.py', 'evidence_gate.py', 'pytest.ini',
             'legacy_fixture_adaptations.json', 'exclusions.json']:
    shutil.copyfile(PREV / name, RUN / name)
spec = ROOT / 'docs/ic_im_production_provenance_20261023_v1_spec.md'
Path(str(spec) + '.sha256').write_text(sha(spec) + '  ' + spec.name + '\n', encoding='utf-8')
pin = json.loads((PREV / 'delivery/capture_source_pin.json').read_text(encoding='utf-8'))
baseline = {'created_at': datetime.now().astimezone().isoformat(),
    'backups': {n: sha(RUN / 'backup' / n) for n in names},
    'runtime': hashes(ROOT / 'runtime'), 'evidence': hashes(ROOT / 'data/icim_v14_original_inputs'),
    'production': {n: sha(ROOT / n) for n in pin['files']},
    'previous_tests': json.loads((PREV / 'accepted_02/test_run.json').read_text(encoding='utf-8'))['files'],
    'previous_bindings': json.loads((PREV / 'accepted_02/source_after.json').read_text(encoding='utf-8')),
    'spec_sha256': sha(spec), 'collector_preflight': 'previous PID21280 absent; pin matches current sources'}
(RUN / 'baseline.json').write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(RUN / 'delivery').mkdir(exist_ok=True)
print(json.dumps({'runtime_files': len(baseline['runtime']), 'archive_files': len(baseline['evidence']),
                  'pinned_modules': len(pin['files'])}))
