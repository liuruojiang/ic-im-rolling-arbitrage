"""Independent concurrency rerun; immutable stage and strict source-bound receipt."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
AUDIT = Path(__file__).resolve().parents[1]
STAGE = sys.argv[1]
FINAL = AUDIT / 'concurrency' / STAGE
FINAL.mkdir(exist_ok=False)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

production = [
    'poe_ic_im_mainline_v1_4_bot.py', 'poe_ic_im_v1_4_server.py',
    'ic_im_v1_4_policy.py', 'ic_im_adjusted_put_migration_v1.py',
    'ic_im_sse_contract_identity_v1.py', 'ic_im_put_execution_v1.py',
    'poe_ic_im_v1_4_state.py', 'run_ic_im_v1_4_github_digest.py',
    'ic_im_fear_grid_v1.py',
]
relative_audit = AUDIT.relative_to(ROOT).as_posix()
paths = production + [
    'test_icim_batch3_concurrency_20261021.py',
    'test_ic_im_ordinary_put_route_fix_20261013.py',
    'test_icim_round2_audit_lifecycle_20261021.py',
    'docs/ic_im_external_review_batch3_20261021_v1_spec.md',
    relative_audit+'/run_checks.py', relative_audit+'/evidence_gate.py',
    relative_audit+'/concurrency/final_reverify.py',
]
def sources():
    return {name:sha(ROOT/name) for name in paths}

spec = importlib.util.spec_from_file_location('concurrency_evidence_gate',AUDIT/'evidence_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
baseline = json.loads((AUDIT/'baseline.json').read_text(encoding='utf-8'))
old_binding = {}
for name in production:
    expected = baseline['backup'][name]
    actual = sha(AUDIT/'backup'/name)
    assert actual == expected, 'baseline backup mismatch: '+name
    old_binding[name] = actual
(FINAL/'baseline_binding.json').write_text(json.dumps(old_binding,indent=2),encoding='utf-8')
before = sources()
(FINAL/'source_before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
started = datetime.now(timezone.utc).isoformat()
env = dict(os.environ,ICIM_AUDIT_STAGE='concurrency/'+STAGE,PYTHONIOENCODING='utf-8')
process = subprocess.run([sys.executable,str(AUDIT/'run_checks.py'),
                          'test_icim_batch3_concurrency_20261021.py'],cwd=ROOT,env=env,timeout=90)
after = sources()
(FINAL/'source_after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
counts = gate.validate_run(FINAL/'regression.xml',process_exit_code=process.returncode,
                           source_before=before,source_after=after)
receipt = dict(started_at=started,finished_at=datetime.now(timezone.utc).isoformat(),
               exit_code=process.returncode,source_before_equals_after=before==after,
               source_files=len(before),counts=counts,
               artifacts={name:sha(FINAL/name) for name in ['regression.xml','regression.txt',
                           'source_before.json','source_after.json','test_run.json']})
(FINAL/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
gate.validate_receipt(FINAL)
print(json.dumps(dict(stage=str(FINAL),counts=counts,source_files=len(before))))
