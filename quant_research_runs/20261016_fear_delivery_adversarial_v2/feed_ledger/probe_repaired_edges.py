"""Second-round edges against the repaired snapshot binding."""
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import pytest
import ic_im_fear_grid_v1 as feed
import poe_ic_im_v1_4_state as state
from test_ic_im_fear_source_delivery_20261016 import DAY, setup_feed, signal_for
results=[]
def check(name,evidence,should_accept):
 signal=signal_for(evidence)
 try:
  state.validate_fear_grid_signal(signal,"IC")
  accepted=True;error=None
 except Exception as exc:
  accepted=False;error=f"{type(exc).__name__}: {exc}"
 results.append(dict(name=name,ok=accepted==should_accept,accepted=accepted,expected=should_accept,error=error,signal=signal))
with pytest.MonkeyPatch.context() as mp:
 setup_feed(mp,page_ok=False)
 evidence=feed.fetch_score(DAY,"close")
 carried=copy.deepcopy(evidence)
 carried.update(fear_greed_index=39.32,fear_fallback_applied=True,fear_previous_exchange_date="2026-10-15",
                fear_data_date="2026-10-15",fear_data_status=feed.CLOSE_FALLBACK_STATUS,
                fear_fallback_reason="same_day_not_published",fear_fallback_timing="same_day_post_close")
 check("T1_value_correct_but_T_exists",carried,False)
 missing_T=copy.deepcopy(carried)
 missing_T["fear_csv_observations"].pop("2026-10-16")
 check("deleted_T_map_still_csv_latest_T",missing_T,False)
 for name,delta in [
  ("missing_csv_map",dict(fear_csv_observations=None)),
  ("changed_T_map_vs_latest",dict(fear_csv_observations={"2026-10-15":39.32,"2026-10-16":80})),
  ("boolean_T_map",dict(fear_csv_observations={"2026-10-16":True})),
  ("future_map",dict(fear_csv_observations={"2026-10-17":20})),
 ]:
  changed={**copy.deepcopy(evidence),**delta}
  check(name,changed,False)
 # Existing authorized pre-close page exception must survive strict T priority.
 setup_feed(mp)
 mp.setattr(feed,"_parse_page",lambda raw:(DAY,20.,datetime(2026,10,16,13,0,tzinfo=feed.BEIJING)))
 check("legitimate_preclose_page_T1_carry",feed.fetch_score(DAY,"close"),True)

Path(__file__).with_name("repaired_edges_results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps([{k:v for k,v in r.items() if k!="signal"} for r in results],ensure_ascii=True,indent=2))
