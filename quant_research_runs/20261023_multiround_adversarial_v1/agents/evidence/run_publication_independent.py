"""Bind the unchanged evidence tests, new gate cases and 28 production files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1'
HERE = Path(__file__).resolve().parent
pin = json.loads((ROOT / 'quant_research_runs/20261023_production_provenance_v1/delivery/capture_source_pin.json').read_text(encoding='utf-8'))
tests = ['test_icim_ma23_evidence_20261023.py', 'test_icim_ma23_publication_guard_20261023.py']
names = [*pin['files'], *tests]
def binding():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
stage = sys.argv[1]
target = HERE / stage
target.mkdir(exist_ok=False)
before = binding()
(target / 'source_before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
env = {**os.environ, 'ICIM_AUDIT_STAGE': 'evidence_' + stage, 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
command = [sys.executable, '-X', 'utf8', '-B', str(RUN / 'run_checks.py'), *tests]
result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, encoding='utf-8')
after = binding()
(target / 'source_after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
(target / 'stdout.txt').write_text(result.stdout, encoding='utf-8')
(target / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
receipt = {'exit_code': result.returncode, 'production_and_own_tests_unchanged': before == after,
           'bound_files': len(before), 'test_stage': 'evidence_' + stage,
           'source_changed': [name for name in before if before[name] != after[name]]}
(target / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
print(result.stdout[-12000:])
print(result.stderr[-2000:])
print(json.dumps(receipt))
raise SystemExit(result.returncode or (0 if before == after else 2))
