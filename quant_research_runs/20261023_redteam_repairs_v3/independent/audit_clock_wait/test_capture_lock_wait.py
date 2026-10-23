from datetime import timedelta
from pathlib import Path
import json,os
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_evidence_adapter_v1 as adapter
from test_icim_expert_state_20261021 import _coordinator
from test_icim_round2_audit_lifecycle_20261021 import snapshot


def test_valid_snapshot_after_shared_lock_wait_is_not_future(monkeypatch,tmp_path):
    module,owner,old,signals,clock=_coordinator(tmp_path,monkeypatch)
    monkeypatch.delenv('ICIM_DISABLE_EVIDENCE_CAPTURE',raising=False)
    monkeypatch.setattr(bot.requests.sessions.Session,'request',lambda *a,**k:pytest.fail('external network forbidden'))
    ticks=[100.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda:ticks[0])
    actual_lock=owner.lock
    class ContendedLock:
        def __enter__(self):
            ticks[0]+=2.
            return actual_lock.__enter__()
        def __exit__(self,*args):return actual_lock.__exit__(*args)
    owner.lock=ContendedLock()
    facts={};before=snapshot(owner.store.root)
    def capture(strategy,root,now):
        observed=strategy._iv_observation_clock(now)
        stamp=clock+timedelta(seconds=1)
        facts.update(request_clock=clock.isoformat(),elapsed_before_snapshot=ticks[0]-100.,
                     actual_receive_clock=(clock+timedelta(seconds=ticks[0]-100.)).isoformat(),
                     validator_observation_clock=observed.isoformat(),source_stamp=stamp.isoformat())
        strategy.quote_snapshot_guard.official_snapshot_evidence(stamp,observed,now.date(),'valid frame after shared-lock contention')
        return {'status':'captured','day':str(now.date()),'products':{}}
    monkeypatch.setattr(adapter,'capture_daily',capture)
    result=owner.capture_current_inputs(clock)
    facts.update(result=result,ledger_bytes_unchanged=snapshot(owner.store.root)==before)
    (Path(os.environ['ICIM_TEST_OUTPUT_DIR'])/'observation.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
    assert result['status']=='captured',facts
    assert snapshot(owner.store.root)==before
