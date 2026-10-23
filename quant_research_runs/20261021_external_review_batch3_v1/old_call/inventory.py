"""Read-only IM Call inventory: never initialize or append a StateStore."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import poe_ic_im_v1_4_state as state
import ic_im_v1_4_policy as policy

RUNTIME = ROOT/'runtime/ic_im_v1_4_r1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    return {p.relative_to(ROOT).as_posix():sha(p) for p in RUNTIME.rglob('*.json') if p.is_file()}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


before = snapshot()
rows,active,previous = [],[],None
for path in sorted((RUNTIME/'journal').glob('*.json')):
    value = read(path)
    state._validate_record(value)
    assert state._digest(value) == value['digest']
    if previous:
        assert value['sequence'] == previous['sequence']+1
        assert value['previous_digest'] == previous['digest']
    a,s = value['products']['IM'],value.get('signals',{}).get('IM',{})
    row = dict(file=path.name,sequence=value['sequence'],verified_day=value['verified_day'],
               verified_contract=a.get('verified_call_contract'),
               verified_qty=a.get('verified_call_contracts_normalized'),
               verified_expiry=a.get('verified_call_expiry'),
               signal_current=s.get('call_current_contract'),signal_target=s.get('call_target_contract'),
               signal_action=s.get('call_action'),signal_has_position=s.get('call_has_position'),
               legacy_baseline_contract=a.get('call_contract'),
               legacy_baseline_units=a.get('call_equivalent_units'))
    rows.append(row)
    if row['verified_contract'] or float(row['verified_qty']) != 0:
        active.append(row)
    previous = value
assert read(RUNTIME/'latest.json') == previous
after = snapshot()
if before != after:
    raise RuntimeError('Concurrent runtime change: refuse stable inventory assertion')
result = dict(observed_at=datetime.now(timezone.utc).isoformat(),record_count=len(rows),
              latest_sequence=previous['sequence'],latest_day=previous['verified_day'],
              no_new_call_effective_date=policy.NO_CALL_EFFECTIVE_SIGNAL_DATE.isoformat(),
              active_record_count=len(active),active_records=active,records=rows,
              runtime_before=before,runtime_after=after,runtime_unchanged=True,
              scope='Local research ledger only; not broker or remote holdings')
target = HERE/'local_inventory.json'
if target.exists():
    raise RuntimeError('Refuse to overwrite earlier inventory evidence')
target.write_bytes(json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8'))
print(json.dumps({k:result[k] for k in ['record_count','latest_sequence','latest_day','active_record_count','runtime_unchanged']}))
