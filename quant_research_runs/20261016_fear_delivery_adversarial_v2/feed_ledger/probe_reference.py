"""Reference-only adversarial payloads; never reads production ledger."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import pytest
import ic_im_fear_grid_v1 as feed
import poe_ic_im_v1_4_state as state
from test_ic_im_fear_source_delivery_20261016 import DAY, setup_feed, signal_for

cases={
 "zero":{"date":"2026-10-16","value":0},
 "hundred":{"date":"2026-10-16","value":100},
 "previous":{"date":"2026-10-15","value":2.28},
 "negative":{"date":"2026-10-16","value":-1},
 "over_100":{"date":"2026-10-16","value":101},
 "bool":{"date":"2026-10-16","value":True},
 "nan":{"date":"2026-10-16","value":float("nan")},
 "future":{"date":"2026-10-17","value":2.28},
 "duplicate_conflict":{"date":"2026-10-16","value":10,"history":[{"date":"2026-10-16","value":11}]},
 "invalid_history":{"date":"2026-10-16","value":10,"history":"invalid"},
 "empty":{},
}
results=[]
for name,payload in cases.items():
 with pytest.MonkeyPatch.context() as mp:
  setup_feed(mp)
  before=signal_for(feed.fetch_score(DAY,"close"))
  mp.setattr(feed,"_bounded_get",lambda *args:json.dumps(payload).encode())
  reference=feed.fetch_jiu_reference(DAY)
  after={**before,**reference}
  state.validate_fear_grid_signal(after,"IC")
  rendered=feed.reference_disclosure(after)
  unchanged=all(before.get(x)==after.get(x) for x in ("fear_greed_index","grid_target","grid_entry_source_target","grid_transition_reason"))
  expected_valid=name in {"zero","hundred","previous"}
  ok=unchanged and ((reference["jiu_fear_greed_index"] is not None)==expected_valid)
  results.append(dict(name=name,ok=ok,reference=reference,rendered=rendered,grid_target=after["grid_target"]))
Path(__file__).with_name("reference_results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"cases":len(results),"pass":sum(x["ok"] for x in results),"failures":[x for x in results if not x["ok"]]},ensure_ascii=True))
