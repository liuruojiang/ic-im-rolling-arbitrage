"""Read-only reconciliation of official adjustment event, quotes, master and local checkpoint."""
from pathlib import Path
from datetime import date, datetime, timezone
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import ic_im_sse_contract_identity_v1 as identity

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
raw = HERE / 'raw'
quote = json.loads((raw / 'sse_december_identity.json').read_bytes())
events = identity.parse_adjustment_events(json.loads((raw / 'sse_adjustment_20260715.json').read_bytes()),
                                          effective_date=date(2026, 7, 15))
master = json.loads((raw / 'sse_contract_master.json').read_bytes())['result']
day = date.fromisoformat(str(quote['date']))
reconciled = []
for row in quote['list']:
    if not row[0].startswith('510500P2612A'):
        continue
    result = identity.resolve_adjusted_holding(events, quote, row[5], row[0], day)
    matches = [item for item in master if item['SECURITY_ID'] == row[5]]
    assert len(matches) == 1
    item = matches[0]
    assert (result['contract'], result['strike'], result['contract_unit']) == (
        item['CONTRACT_ID'], float(item['EXERCISE_PRICE']), int(item['CONTRACT_UNIT']))
    result['master_data_day'] = item['TIMESAVE']
    reconciled.append(result)
checkpoint = ROOT / 'runtime/ic_im_v1_4_r1/latest.json'
record = json.loads(checkpoint.read_text(encoding='utf-8-sig'))
anchor = record['products']['IC']
current = []
for code_key, id_key in [('v14_core_put_contract', 'v14_core_put_security_id'),
                          ('post_put_contract', 'post_put_security_id')]:
    contract, security = anchor.get(code_key), anchor.get(id_key)
    found = [row for row in quote['list'] if row[5] == security]
    current.append(dict(field=code_key, contract=contract, security_id=security,
                        snapshot_match=len(found) == 1 and found[0][0] == contract))
index = {p.name: dict(sha256=sha(p), size=p.stat().st_size,
                      filesystem_saved_at=datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat())
         for p in raw.iterdir() if p.is_file()}
result = dict(created_at=datetime.now(timezone.utc).isoformat(),
              official_adjustment_event_count=len(events),
              quote_day=day, quote_time=quote['time'], quote_rows=len(quote['list']),
              live_adjusted_puts=reconciled,
              local_checkpoint=dict(verified_day=record['verified_day'], sequence=record['sequence'],
                                    sha256=sha(checkpoint), legs=current),
              source_files=index,
              source_urls=dict(official_notice='https://www.sse.com.cn/assortment/options/disclo/update/c/c_20260714_10825473.shtml',
                               quote='https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12?select=contractid,last,chg_rate,presetpx,exepx,code&order=contractid,ase',
                               adjustment_and_master='https://query.sse.com.cn/commonQuery.do; exact params in raw/master_capture.json'),
              limits=['Read-only local checkpoint, not broker/cloud account proof',
                      'Master TIMESAVE is 2026-10-20; quote date is 2026-10-21',
                      'A resolution is diagnostic only, not engine/ledger migration',
                      'filesystem_saved_at is file metadata, not reconstructed HTTP request-start time'])
target = HERE / 'real_identity_verification.json'
if target.exists():
    raise SystemExit('Refusing to overwrite real-data verification')
target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(json.dumps(dict(events=len(events), live_adjusted_puts=len(reconciled), local_legs=current)))
