from pathlib import Path
from datetime import date,datetime
from collections import Counter
import json,sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
root=ROOT/'data/icim_v14_original_inputs'
reports=[]
for folder in sorted((root/'sessions').iterdir()):
 try:day=date.fromisoformat(folder.name)
 except ValueError:continue
 if day<date(2026,10,22):continue
 stats=adapter.capture_day_status(root,day)
 comparable={p:0 for p in ['IC','IM']}
 for path in folder.glob('*.json'):
  envelope=evidence._loads(evidence._read(path,evidence.MAX_MANIFEST_BYTES));body=envelope['body'];meta=evidence.decode(body['metadata'])
  if meta.get('original_close') is not True or meta.get('product') not in comparable:continue
  counts=Counter((r['url'],evidence._dumps(r['params'])) for r in body['raw'])
  if any(c>1 for c in counts.values()):comparable[meta['product']]+=1
 stats['sessions_with_repeated_identical_request']=comparable
 reports.append(stats)
result={'generated_at':datetime.now().astimezone().isoformat(),'observed_session_days':len(reports),'requested_initial_observation_days':5,'days':reports,'automatic_recording':'collector consistency_statistics in every capture_receipt; query polls not counted as acquisition attempts','limitation':'Only sealed original captures count. Single-use URLs cannot establish that SSE post-close time/headers are stable. Fewer than five observed days is not a five-day oracle.'}
p=Path(__file__).resolve().parent/'daily_consistency_summary.json'
p.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
