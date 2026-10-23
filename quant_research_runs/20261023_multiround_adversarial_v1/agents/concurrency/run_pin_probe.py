from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
stage = sys.argv[1]
target = HERE / stage
target.mkdir(exist_ok=False)
pin = json.loads((ROOT / 'quant_research_runs/20261023_production_provenance_v1/delivery/capture_source_pin.json').read_text(encoding='utf-8'))
test = 'test_icim_ma23_pin_publication_20261023.py'
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def source():
    return {n: sha(ROOT / n) for n in [*pin['files'], test]}
def runtime():
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / 'runtime').rglob('*') if p.is_file()}
before, state = source(), runtime()
(target / 'source_before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(HERE / 'run_agent.py'), test],
                        cwd=ROOT, env={**os.environ, 'ICIM_AUDIT_STAGE': stage, 'PYTHONIOENCODING':'utf-8'},
                        capture_output=True, encoding='utf-8')
after = source()
(target / 'source_after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
(target / 'runner_stdout.txt').write_text(result.stdout, encoding='utf-8')
(target / 'runner_stderr.txt').write_text(result.stderr, encoding='utf-8')
root = ET.parse(target / 'regression.xml').getroot()
suites = [root] if root.tag == 'testsuite' else list(root)
counts = {name:sum(int(s.get(name,0)) for s in suites) for name in ['tests','failures','errors','skipped']}
receipt = {'exit_code':result.returncode,'counts':counts,'source_files':len(before),
           'source_before_equals_after':before==after,'runtime_files':len(state),'formal_runtime_unchanged':state==runtime()}
(target / 'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
print(result.stdout[-15000:])
print(json.dumps(receipt))
raise SystemExit(result.returncode)
