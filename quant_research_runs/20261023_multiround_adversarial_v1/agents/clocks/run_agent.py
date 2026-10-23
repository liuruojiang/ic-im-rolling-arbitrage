"""Independent immutable binding: production files and this agent's test only."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[4]
RUN=ROOT/'quant_research_runs/20261023_multiround_adversarial_v1'
HERE=Path(__file__).resolve().parent
stage=sys.argv[1]
target=HERE/stage
target.mkdir(exist_ok=False)
pin=json.loads((ROOT/'quant_research_runs/20261023_production_provenance_v1/delivery/capture_source_pin.json').read_text(encoding='utf-8'))
files=[*pin['files'],'test_icim_ma23_clocks_20261023.py']
def hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
before=hashes()
(target/'source_before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
env={**os.environ,'ICIM_AUDIT_STAGE':'clocks_'+stage,'PYTHONIOENCODING':'utf-8'}
result=subprocess.run([sys.executable,'-X','utf8','-B',str(RUN/'run_checks.py'),'test_icim_ma23_clocks_20261023.py'],cwd=ROOT,env=env,capture_output=True,encoding='utf-8')
after=hashes()
(target/'source_after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
(target/'output.txt').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
receipt={'stage':stage,'exit_code':result.returncode,'source_before_equals_after':before==after,'bound_files':len(files),
         'finished_at':datetime.now().astimezone().isoformat(),'test_output':str(RUN/('clocks_'+stage))}
(target/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(result.stdout[-14000:])
print(json.dumps(receipt))
raise SystemExit(result.returncode)
