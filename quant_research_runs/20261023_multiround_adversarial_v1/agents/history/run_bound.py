from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
RUN = HERE.parents[1]
stage = sys.argv[1]
target = HERE / stage
target.mkdir(exist_ok=False)
pin = json.loads((ROOT / 'quant_research_runs/20261023_production_provenance_v1/delivery/capture_source_pin.json').read_text(encoding='utf-8'))['files']
paths = [ROOT/n for n in pin] + [ROOT/'test_icim_ma23_history_20261023.py',RUN/'evidence_gate.py',RUN/'run_checks.py',Path(__file__)]
def binding():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before = binding()
env = dict(os.environ, ICIM_AUDIT_STAGE='history_' + stage, PYTHONIOENCODING='utf-8')
command = [sys.executable,'-X','utf8','-B',str(RUN/'run_checks.py'),'test_icim_ma23_history_20261023.py']
result = subprocess.run(command,cwd=ROOT,env=env,capture_output=True,encoding='utf-8')
after = binding()
(target/'stdout.txt').write_text(result.stdout,encoding='utf-8')
(target/'stderr.txt').write_text(result.stderr,encoding='utf-8')
xml = RUN / ('history_' + stage) / 'regression.xml'
suite = ET.parse(xml).getroot()
counts = {k:sum(int(s.get(k,0)) for s in suite.findall('testsuite')) for k in ['tests','failures','errors','skipped']}
value = {'time':datetime.now().astimezone().isoformat(),'exit_code':result.returncode,'counts':counts,
         'before':before,'after':after,'production_and_own_test_binding_unchanged':before==after,
         'test_output':str(xml),'stage':stage,'synthetic_market_data':False,'formal_state_write':False}
(target/'receipt.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:value[k] for k in ['exit_code','counts','production_and_own_test_binding_unchanged','test_output']}))
print(result.stdout[-3000:])
sys.exit(result.returncode)
