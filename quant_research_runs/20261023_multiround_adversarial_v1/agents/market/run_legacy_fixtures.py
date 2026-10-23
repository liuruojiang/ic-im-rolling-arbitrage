"""Verify the explicitly declared legacy fixture-only contract updates."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1'
pin = json.loads((ROOT / 'quant_research_runs/20261023_production_provenance_v1/delivery/capture_source_pin.json').read_text(encoding='utf-8'))['files']
tests = ['test_icim_round3_source_validation_20261021.py',
         'test_icim_round5_recording_semantics_20261021.py',
         'test_icim_expert_followup_replay_20261022.py']
files = [*pin, *tests]


def binding():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}


before = binding()
started = datetime.now().astimezone().isoformat()
stage = sys.argv[1]
env = {**os.environ, 'ICIM_AUDIT_STAGE': stage, 'PYTHONIOENCODING': 'utf-8'}
result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(RUN / 'run_checks.py'), *tests],
                        cwd=ROOT, env=env, capture_output=True, encoding='utf-8')
after = binding()
target = RUN / stage
tree = ET.parse(target / 'regression.xml').getroot()
suites = [tree] if tree.tag == 'testsuite' else list(tree)
counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ['tests', 'failures', 'errors', 'skipped']}
receipt = {'started_at': started, 'finished_at': datetime.now().astimezone().isoformat(),
    'exit_code': result.returncode, 'source_before': before, 'source_after': after,
    'source_before_equals_after': before == after, 'production_modules': len(pin),
    'test_files': tests, 'counts': counts, 'status': 'PASS' if result.returncode == 0 else 'FAIL'}
(target / 'legacy_market_fixture_binding_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(result.stdout[-9000:])
print(json.dumps({k:v for k,v in receipt.items() if k not in ['source_before', 'source_after']}, ensure_ascii=False))
assert before == after, 'source drift during verification'
raise SystemExit(result.returncode)
