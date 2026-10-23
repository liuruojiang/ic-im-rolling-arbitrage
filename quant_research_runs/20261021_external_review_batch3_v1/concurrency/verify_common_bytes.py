"""Compare all independent bindings honestly with the accepted cumulative run."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUDIT = Path(__file__).resolve().parents[1]
OWN = AUDIT/'concurrency/final_20261021_1'
ACCEPTED = AUDIT/'accepted_batch3_2'
OUT = AUDIT/'concurrency/shared_bytes_accepted_batch3_2.json'
assert not OUT.exists(), 'must not overwrite previous shared-byte evidence'
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('shared_bytes_gate',AUDIT/'evidence_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
own_counts = gate.validate_receipt(OWN)
accepted_counts = gate.validate_receipt(ACCEPTED)
own = json.loads((OWN/'source_after.json').read_text(encoding='utf-8'))
accepted = json.loads((ACCEPTED/'source_after.json').read_text(encoding='utf-8'))
rows = []
for name, expected in own.items():
    current = sha(ROOT/name)
    assert current == expected, 'independent final no longer matches current: '+name
    included = name in accepted
    if included:
        assert accepted[name] == expected, 'cumulative source mismatch: '+name
    rows.append(dict(path=name,independent_sha256=expected,current_sha256=current,
                     cumulative_includes=included,cumulative_sha256=accepted.get(name),
                     independent_current_equal=True,
                     cumulative_equal=accepted[name]==expected if included else None))
missing = [row['path'] for row in rows if not row['cumulative_includes']]
assert missing == [AUDIT.relative_to(ROOT).as_posix()+'/concurrency/final_reverify.py']
result = dict(independent_stage='concurrency/final_20261021_1',
              accepted_stage='accepted_batch3_2',rejected_stage_not_used='accepted_batch3',
              independent_counts=own_counts,accepted_counts=accepted_counts,
              independent_sources=len(own),cumulative_sources=len(accepted),
              shared_sources=len(own)-len(missing),all_shared_sources_equal=True,
              all_independent_sources_match_current=True,
              standalone_wrapper_not_in_cumulative=missing,
              strict_receipts_valid=True,
              evidence_sha256={
                  'independent_receipt':sha(OWN/'receipt.json'),
                  'independent_source_after':sha(OWN/'source_after.json'),
                  'accepted_receipt':sha(ACCEPTED/'receipt.json'),
                  'accepted_source_after':sha(ACCEPTED/'source_after.json'),
              },rows=rows)
OUT.write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['rows','evidence_sha256']}))
