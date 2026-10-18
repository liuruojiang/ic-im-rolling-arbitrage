"""Independent cache-scope and live handoff consistency audit, no ledger I/O."""
import asyncio
import copy
import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
import pytest
import ic_im_fear_grid_v1 as feed
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
DAY=date(2026,10,16)
results=[]
def result(name,condition,detail=None):
 results.append(dict(name=name,ok=bool(condition),detail=detail))
with pytest.MonkeyPatch.context() as mp:
 calls=[]
 def raw(d,m):
  calls.append(("bfw",str(d),m))
  return {"serial":len(calls),"nested":{"value":20}}
 def jiu(d):
  calls.append(("jiu",str(d)))
  return {"serial":len(calls),"nested":{"value":2.28}}
 mp.setattr(feed,"_fetch_score_uncached",raw)
 mp.setattr(feed,"_fetch_jiu_reference_uncached",jiu)
 a=feed.fetch_score(DAY,"close");b=feed.fetch_score(DAY,"close")
 result("no_cache_outside_report",a["serial"]!=b["serial"])
 with feed.observation_batch():
  first=feed.fetch_score(DAY,"close")
  with feed.observation_batch():
   second=feed.fetch_score(DAY,"close")
  result("nested_batch_reuses",first==second)
  first["nested"]["value"]=999
  result("deepcopy_prevents_pollution",feed.fetch_score(DAY,"close")["nested"]["value"]==20)
  intraday=feed.fetch_score(DAY,"intraday")
  otherday=feed.fetch_score(DAY-timedelta(days=1),"close")
  result("mode_and_date_are_distinct_keys",len({second["serial"],intraday["serial"],otherday["serial"]})==3)
  j1=feed.fetch_jiu_reference(DAY);j2=feed.fetch_jiu_reference(DAY)
  result("jiu_cached_per_report",j1==j2)
 after=feed.fetch_score(DAY,"close")
 result("outer_exit_releases_snapshot",after["serial"]!=second["serial"] and feed._OBSERVATION_BATCH.get() is None)
 try:
  with feed.observation_batch():
   errored=feed.fetch_score(DAY,"close")
   raise RuntimeError("injected report exception")
 except RuntimeError:
  pass
 result("exception_releases_context",feed._OBSERVATION_BATCH.get() is None and feed.fetch_score(DAY,"close")["serial"]!=errored["serial"])
 with bot.collection_clock(datetime(2026,10,16,13,0,tzinfo=feed.BEIJING)):
  a=feed.fetch_score(DAY,"intraday")
  with bot.collection_clock(datetime(2026,10,16,13,1,tzinfo=feed.BEIJING)),feed.observation_batch():
   b=feed.fetch_score(DAY,"intraday")
  result("nested_collection_clock_and_poe_batch_reuse",a==b)
 result("collection_exit_releases_context",feed._OBSERVATION_BATCH.get() is None)
 async def job():
  with feed.observation_batch():
   a=feed.fetch_score(DAY,"close")
   await asyncio.sleep(0)
   b=feed.fetch_score(DAY,"close")
   return a,b
 async def run():
  return await asyncio.gather(job(),job())
 (a,b),(c,d)=asyncio.run(run())
 result("separate_async_reports_isolated",a==b and c==d and a["serial"]!=c["serial"])

payload_path=ROOT/"quant_research_runs/20261016_fear_delivery_adversarial_v2/coordinator/handoff-38856549173/report/result.json"
payload=json.loads(payload_path.read_text(encoding="utf-8"))
ic,im=payload["signals"]["IC"],payload["signals"]["IM"]
fear_keys=[k for k in ic if k.startswith("fear_") or k.startswith("jiu_")]
diff={k:[ic.get(k),im.get(k)] for k in fear_keys if ic.get(k)!=im.get(k)}
result("real_handoff_all_fear_and_jiu_evidence_equal",not diff,diff)
for product,signal in payload["signals"].items():
 try:
  state.validate_fear_grid_signal(signal,product)
  ok=True;detail=None
 except Exception as exc:
  ok=False;detail=f"{type(exc).__name__}: {exc}"
 result("real_handoff_consumer_"+product,ok,detail)
out={"cases":results,"source_sha256":{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ["ic_im_fear_grid_v1.py","poe_ic_im_mainline_v1_4_bot.py","poe_ic_im_v1_4_state.py"]},
     "real_handoff_sha256":hashlib.sha256(payload_path.read_bytes()).hexdigest(),"real_handoff_fields":{k:ic.get(k) for k in fear_keys}}
(HERE/"report_batch_results.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"cases":len(results),"pass":sum(x["ok"] for x in results),"failures":[x for x in results if not x["ok"]]},ensure_ascii=True))
