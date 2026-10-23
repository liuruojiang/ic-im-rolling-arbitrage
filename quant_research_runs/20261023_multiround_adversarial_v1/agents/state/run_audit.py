"""Own immutable invocation receipt, excluding other agents' test mutations."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1'
OUT = Path(__file__).resolve().parent
stage = sys.argv[1]
pin = json.loads((ROOT / 'quant_research_runs/20261023_production_provenance_v1/delivery/capture_source_pin.json').read_text(encoding='utf-8'))
names = sorted([*pin['files'], 'test_icim_ma23_state_20261023.py'])
def bindings():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
before = bindings()
directory = OUT / stage
directory.mkdir(exist_ok=False)
(directory / 'source_before.json').write_text(json.dumps(before, indent=2)+'\n', encoding='utf-8')
result = subprocess.run([sys.executable, '-X','utf8','-B', str(RUN / 'run_checks.py'),
                         'test_icim_ma23_state_20261023.py'], cwd=ROOT,
                        env={**os.environ, 'ICIM_AUDIT_STAGE': stage, 'PYTHONIOENCODING':'utf-8'},
                        capture_output=True, encoding='utf-8')
after = bindings()
(directory / 'source_after.json').write_text(json.dumps(after, indent=2)+'\n', encoding='utf-8')
(directory / 'runner_stdout.txt').write_text(result.stdout, encoding='utf-8')
(directory / 'runner_stderr.txt').write_text(result.stderr, encoding='utf-8')
xml = ET.parse(RUN / stage / 'regression.xml').getroot()
counts = {key: sum(int(s.get(key,0)) for s in xml.findall('testsuite')) for key in ['tests','failures','errors','skipped']}
report = {'exit_code':result.returncode, 'stage':stage, 'counts':counts,
          'source_before_equals_after': before == after, 'bound_files':len(before),
          'formal_runtime_write_requested':False,'external_network_requested':False}
(directory / 'receipt.json').write_text(json.dumps(report,indent=2)+'\n', encoding='utf-8')
print(json.dumps(report))
raise SystemExit(result.returncode)
