from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
RUN = HERE.parents[1]
Q = ROOT / 'quant_research_runs'
source = Q / '20261023_production_provenance_v1/accepted_01'
artifacts = ['receipt.json','regression.xml','regression.txt','source_before.json','source_after.json','test_run.json','collection.json']
old_gates = [Q / n / 'evidence_gate.py' for n in ['20261022_free_source_integration_v1',
    '20261022_evidence_conflict_review_v1','20261022_evidence_http_date_v1','20261023_production_provenance_v1']]
paths = [source/n for n in artifacts] + old_gates
def binding():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before = binding()
spec = importlib.util.spec_from_file_location('_history_readonly_gate',RUN/'evidence_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
counts = gate.validate_receipt(source)
after = binding()
assert counts == {'tests':2982,'failures':0,'errors':0,'skipped':0}
assert before == after
old_gate_sha = hashlib.sha256((HERE/'backup/evidence_gate.py').read_bytes()).hexdigest()
assert all(hashlib.sha256(p.read_bytes()).hexdigest() == old_gate_sha for p in old_gates)
result = {'status':'PASS','time':datetime.now().astimezone().isoformat(),
    'validated_original_receipt':str(source),'counts':counts,'read_only_before':before,'read_only_after':after,
    'old_receipt_and_tools_unchanged':True,'new_gate_sha256':hashlib.sha256((RUN/'evidence_gate.py').read_bytes()).hexdigest()}
(HERE/'readonly_2982_verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'status':result['status'],'counts':counts,'old_receipt_and_tools_unchanged':True}))
