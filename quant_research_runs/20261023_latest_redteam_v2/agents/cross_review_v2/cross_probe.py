from pathlib import Path
import sys,os,json,hashlib,importlib.util,subprocess
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];sys.path.insert(0,str(ROOT))
TEMP=HERE/'probe_temp';TEMP.mkdir(exist_ok=False)
os.environ.update(ICIM_STATE_DIR=str(TEMP/'state'),ICIM_REQUIRE_MIGRATION='0',ICIM_DISABLE_EVIDENCE_CAPTURE='1',ICIM_DISABLE_INTERNAL_REFRESH='1',PYTHONDONTWRITEBYTECODE='1',TEMP=str(TEMP),TMP=str(TEMP))
orig=os.mkdir
os.mkdir=lambda p,mode=0o777,**kw:orig(p,0o777 if mode==0o700 else mode,**kw)
from datetime import date,datetime
from copy import deepcopy
import pandas as pd,requests,pytest
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import ic_im_signal_evidence_v1 as evidence
manifest=json.loads((ROOT/'docs/ic_im_daily_release_20261023_manifest.json').read_text('utf-8'))
def hashes():return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in manifest['files']}
before=hashes();results=[]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
market=load('cross_market_helpers',HERE.parent/'market_v2/test_market_v2.py')
# Keep helper observation outputs out of the original author's directory.
market.HERE=HERE

def deny(*a,**k):raise AssertionError('cross-review network forbidden')
def response(raw):
 r=requests.Response();r.status_code=200;r.encoding='gb18030';r._content=raw;r._content_consumed=True;return r
clock=datetime(2026,10,22,14,30,tzinfo=bot.BEIJING)
with pytest.MonkeyPatch.context() as mp:
 market.vendor(mp,market.qt(symbol='sh000852',code='000852',price=3000.))
 out=bot.live_proxy('IC',clock)
 assert out['price']==3000.
 results.append({'id':'index_identity','wrong_index_accepted':True,'computed_momentum_120':out['momentum_120'],'computed_momentum_score':out['momentum_score'],'scope':'live_proxy; no state append'})
with pytest.MonkeyPatch.context() as mp:
 market.vendor(mp,market.qt(stamp='20261022150000'))
 out=bot.live_proxy('IC',clock)
 assert out['live_price_source_time']=='15:00:00'
 results.append({'id':'index_future','observation_clock':clock.isoformat(),'accepted_quote_time':out['live_price_source_time'],'scope':'live_proxy; no state append'})
with pytest.MonkeyPatch.context() as mp:
 mp.setattr(requests.sessions.Session,'request',deny)
 stamp=datetime(2026,10,22,18,tzinfo=bot.BEIJING);mp.setattr(evidence,'_now',lambda:stamp)
 raw=(ROOT/'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin').read_bytes()
 first=next(x.strip() for x in raw.split(b';') if x.strip())+b';'
 price=float(first.split(b'=')[1].strip(b';').strip(b'"').split(b',')[2])
 broken=first.replace(b'hq_str_',b'unrelated_hq_str_')
 mp.setattr(bot.requests,'get',lambda *a,**k:response(broken))
 direct_rejected=False
 try:
  with bot.runtime_clock(stamp):bot.verify_sina_option_quote('MO2611-P-6000',stamp.date(),price,stamp)
 except RuntimeError:direct_rejected=True
 assert direct_rejected
 batch=broken+broken.replace(b'P_OP_mo2611P6000',b'P_OP_mo2611P6100')
 mp.setattr(bot.requests,'get',lambda *a,**k:response(batch))
 archive=TEMP/'archive'
 with bot.runtime_clock(stamp),evidence.capture_session(archive,stamp.date(),stamp,{'synthetic_mutation':True}) as capture:
  result=bot._prefetch_mo_replay_verification(pd.DataFrame({'instrument':['MO2611-P-6000','MO2611-P-6100']}),stamp)
  capture.call('mo.replay_verification',{'day':stamp.date(),'product':'MO'},lambda:result)
 reader=evidence.load_session(archive,stamp.date());mp.setattr(bot.requests,'get',deny)
 with bot.runtime_clock(stamp),evidence.replay_session(reader):
  accepted=bot.verify_sina_option_quote('MO2611-P-6000',stamp.date(),price,stamp)
 assert accepted['lastprice']==price
 results.append({'id':'batch_projection','direct_rejected':direct_rejected,'replay_accepted':True,'original_response_sha256':hashlib.sha256(raw).hexdigest(),'scope':'real capture/reader/verification consumer, not full original-day adapter qualification or ledger append'})
with pytest.MonkeyPatch.context() as mp:
 mp.setattr(requests.sessions.Session,'request',deny)
 import test_icim_round2_audit_lifecycle_20261021 as prior
 store,record=prior.recent_store(TEMP/'short_ledger');anchors=prior.state.anchors_from_record(record);a=anchors['IC'];prior.ledger._empty_core(a,'IC')
 a.update(v14_route_state='short_put',v14_cycle_id='synthetic-existing-short',v14_short_put_contract=prior.real.M,v14_short_put_security_id='10012099',v14_short_put_qty_normalized=5.,v14_short_put_entry_premium=.4,v14_short_put_expiry=date(2026,12,23),v14_short_put_cost_basis='confirmed_dated_open',v14_short_put_entry_price_day=prior.PREVIOUS,verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
 chain,stamp=prior.real.wire(mp);mp.setattr(bot,'LIVE_CONTINUATION_ANCHOR',anchors);prior.builder_inputs(mp,a,chain,stamp)
 mp.setattr(bot,'fetch_sse_510500_quote',lambda:dict(date='20261021',time='150000',last=7.8,prev_close=7.8))
 live=bot.live_proxy('IC',prior.CLOCK);live.update(momentum_120=.1,valuation_tier=1);mp.setattr(bot,'live_proxy',lambda *a:deepcopy(live))
 mp.setattr(bot,'fetch_option_closes',lambda *a:pd.Series([.4],index=[pd.Timestamp(prior.PREVIOUS)]))
 out=bot.build_live_trade_signal('IC',prior.CLOCK,'close')
 assert out['v14_action']=='HOLD' and out['v14_short_put_mark'] is None and not out.get('diagnostic_only')
 try:state.require_persistence_eligible(out,'IC');eligible=True;error=None
 except Exception as exc:eligible=False;error=str(exc)
 results.append({'id':'missing_short_mark','signal':{k:out.get(k) for k in ['v14_action','v14_short_put_mark','close_confirmed','diagnostic_only','v14_candidate_data_status']},'persistence_eligibility_passed':eligible,'eligibility_error':error,'scope':'public IC builder plus persistence gate; synthetic holding and missing history, no full append'})
report={'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'results':results,'source_before':before,'source_after':hashes(),'source_unchanged':before==hashes(),'network':'all forbidden; only patched vendor packets'}
(HERE/'cross_probe_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'results':results,'source_unchanged':report['source_unchanged']},ensure_ascii=False,indent=2,default=str))
