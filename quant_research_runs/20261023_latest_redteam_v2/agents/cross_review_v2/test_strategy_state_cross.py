from pathlib import Path
import json
from copy import deepcopy
from datetime import date
import pandas as pd
import pytest
import requests
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_v1_4_policy as policy
from test_icim_round3_policy_repairs_20261021 import wire_adapter, short_anchor, signal

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("network forbidden"))

def test_full_ic_missing_short_mark_passes_state_boundary(tmp_path,monkeypatch):
    import test_icim_round2_audit_lifecycle_20261021 as prior
    for module in [prior.state,bot,bot.fear_grid]:
        monkeypatch.setattr(module,"datetime",prior.SyntheticCloseClock)
    store,record=prior.recent_store(tmp_path/"ledger")
    anchors=prior.state.anchors_from_record(record); a=anchors["IC"]
    prior.ledger._empty_core(a,"IC")
    a.update(v14_route_state="short_put",v14_cycle_id="synthetic-existing-short",
        v14_short_put_contract=prior.real.M,v14_short_put_security_id="10012099",
        v14_short_put_qty_normalized=5.,v14_short_put_entry_premium=.4,
        v14_short_put_expiry=date(2026,12,23),v14_short_put_cost_basis="confirmed_dated_open",
        v14_short_put_entry_price_day=prior.PREVIOUS,
        v14_last_event_id="IC:2026-10-19:2026-10-20:ENTER_SHORT_PUT:"+prior.real.M,
        verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
    record['products']['IC']=prior.state._jsonable(a)
    record['digest']=prior.state._digest(record)
    prior.state._validate_record(record)
    store._atomic_write(store.journal_dir/'000000-2026-10-20.json',record)
    store._atomic_write(store.latest_path,record)
    record=store.load_latest()
    anchors=prior.state.anchors_from_record(record);a=anchors['IC']
    chain,stamp=prior.real.wire(monkeypatch)
    monkeypatch.setattr(bot,"LIVE_CONTINUATION_ANCHOR",anchors)
    prior.builder_inputs(monkeypatch,a,chain,stamp)
    monkeypatch.setattr(bot,"fetch_sse_510500_quote",lambda:dict(date="20261021",time="150000",last=7.8,prev_close=7.8))
    live=bot.live_proxy("IC",prior.CLOCK);live.update(momentum_120=.1,valuation_tier=1)
    monkeypatch.setattr(bot,"live_proxy",lambda *a:deepcopy(live))
    # Real-format prior-session historical closes, synthetic accepted holding;
    # actual identity checks consume the preserved SSE fixture.
    monkeypatch.setattr(bot,"fetch_option_closes",lambda *a:pd.Series([.4],index=[pd.Timestamp(prior.PREVIOUS)]))
    out=bot.build_live_trade_signal("IC",prior.CLOCK,"close")
    facts={k:out.get(k) for k in ['close_confirmed','ledger_eligible','diagnostic_only','calendar_provisional','v14_action','v14_short_put_mark','v14_candidate_data_status']}
    prior.state.require_persistence_eligible(out,'IC')
    facts['require_persistence_eligible']='passed'
    other=prior.close_evidence(prior.ledger._signal('IM',anchors['IM'],prior.DAY))
    other['v14_opening_events']=[]
    try:
        result=store.append_confirmed_signals(record,{'IC':out,'IM':other})
        reopened=prior.state.StateStore(store.root).load_latest()
        facts.update(append_accepted=True,sequence=reopened['sequence'],digest_matches=result['digest']==reopened['digest'])
    except Exception as exc:
        facts.update(append_accepted=False,append_error=f'{type(exc).__name__}: {exc}')
    (Path(__file__).parent/'state_reachability.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
    assert facts['append_accepted'],facts
