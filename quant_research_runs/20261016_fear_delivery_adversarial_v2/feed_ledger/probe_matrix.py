"""Producer/consumer contract matrix; no network and no ledger I/O."""
import itertools
import json
import sys
from datetime import date, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pytest
import ic_im_fear_grid_v1 as feed
import poe_ic_im_v1_4_state as state
from test_ic_im_fear_source_delivery_20261016 import signal_for

DAY=date(2026,10,16)
results=[]
for mode, csv_age, page_age, score in itertools.product(["close","intraday"], [None,0,1,2], [None,0,1,2], [0.,25.,50.,100.]):
    with pytest.MonkeyPatch.context() as mp:
        hour=18 if mode=="close" else 13
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):
                return datetime(2026,10,16,hour,30,tzinfo=tz)
        mp.setattr(feed,"datetime",Clock)
        mp.setattr(feed,"_validation_clock",lambda:Clock.now(feed.BEIJING))
        def get(url,maximum):
            age=csv_age if url==feed.CSV_URL else page_age
            if age is None:
                raise RuntimeError("injected request failure")
            d=f"2026-10-{16-age:02d}"
            if url==feed.CSV_URL:
                return f"date,fear_greed_index\n{d},{score}\n".encode()
            return b"page"
        mp.setattr(feed,"_bounded_get",get)
        def parse_page(raw):
            d=date(2026,10,16-page_age)
            update_hour=12 if page_age==0 and mode=="intraday" else 16
            return d,score,datetime(2026,10,d.day,update_hour,0,tzinfo=feed.BEIJING)
        mp.setattr(feed,"_parse_page",parse_page)
        evidence=feed.fetch_score(DAY,mode)
        signal=signal_for(evidence)
        signal["close_confirmed"]=mode=="close"
        try:
            state.validate_fear_grid_signal(signal,"IC")
            validated=True
            error=None
        except Exception as exc:
            validated=False
            error=f"{type(exc).__name__}: {exc}"
        view=feed.publication_view(signal)
        expected_available=(0 in [csv_age,page_age] or 1 in [csv_age,page_age])
        actual_available=evidence["fear_greed_index"] is not None
        ok=validated and expected_available==actual_available and (actual_available or view.get("grid_action")=="UNCONFIRMED")
        results.append(dict(mode=mode,csv_age=csv_age,page_age=page_age,score=score,ok=ok,
                            validated=validated,error=error,actual_available=actual_available,
                            expected_available=expected_available,selected=evidence["fear_selected_source"],status=evidence["fear_data_status"]))
Path(__file__).with_name("matrix_results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"cases":len(results),"pass":sum(x["ok"] for x in results),"failures":[x for x in results if not x["ok"]]},ensure_ascii=True,indent=2))
