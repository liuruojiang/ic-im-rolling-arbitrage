from pathlib import Path
from datetime import datetime
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
D=Path(__file__).resolve().parent/'time_probe'
D.mkdir(exist_ok=False)
clock=datetime.now(bot.BEIJING)
reader,_=adapter.load_original(bot,ROOT/'data/icim_v14_original_inputs','IC',clock.date())
request=next(r for r in reader.context['raw'] if r['url'].endswith('tstyle/510500_12'))
params=evidence.decode(request['params']);observations=[];errors=[]
with evidence.capture_session(D/'evidence',clock.date(),clock,{'capture_kind':'postclose_repeat_http_probe','original_close':False}) as session:
 session.call('probe.scope',{},lambda:{'purpose':'time/header observation only; no signal'})
 with bot._network_budget(28):
  for attempt in range(4):
   try:
    began=datetime.now(bot.BEIJING)
    r=bot._evidence_http_get(request['url'],params=params,headers=bot.HEADERS,timeout=bot._bounded_timeout(7))
    r.raise_for_status();payload=r.json();body=r.content
    observations.append({'attempt':attempt+1,'started_at':began.isoformat(),'received_at':datetime.now(bot.BEIJING).isoformat(),'date':payload.get('date'),'time':payload.get('time'),'body_sha256':hashlib.sha256(body).hexdigest(),'headers':{k:r.headers[k] for k in ['Date','ETag','Last-Modified'] if k in r.headers}})
    r.close()
    if len(observations)==2:break
   except Exception as exc:errors.append({'attempt':attempt+1,'type':type(exc).__name__,'error':str(exc)[:700]})
 session.annotate('time_probe',{'observations':observations,'errors':errors})
result={'status':'OBSERVED_TWO_RESPONSES' if len(observations)==2 else 'INSUFFICIENT_RESPONSES','observations':observations,'errors':errors,'manifest':str(session.manifest_path),'manifest_body_sha256':session.manifest_sha256,'complete':session.context['complete'],'conflicts':[f for f in session.failures if f['role']=='raw_http_conflict'],'formal_signal_generated':False,'limitation':'one post-close sample, not a multi-day stability oracle'}
(D/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
