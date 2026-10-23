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

@pytest.mark.parametrize("product", ["IC", "IM"])
def test_missing_active_short_mark_must_not_publish_normal_hold(monkeypatch, product):
    a, frame=wire_adapter(monkeypatch,product,short_anchor(product),mark=.8)
    if product=="IC":
        monkeypatch.setattr(bot,"fetch_option_closes",lambda *a:pd.Series([.8],index=[pd.Timestamp("2026-11-30")]))
    else:
        frame=frame.iloc[0:0].copy()
    out=bot._apply_v14_live_policy(product,signal(product),mo_quotes=frame)
    assert out["v14_action"]=="HOLD_FAIL_CLOSED", {k:out.get(k) for k in ["v14_action","v14_short_put_mark","v14_candidate_data_status"]}

@pytest.mark.parametrize("product", ["IC", "IM"])
def test_valid_active_short_mark_may_hold(monkeypatch, product):
    a,frame=wire_adapter(monkeypatch,product,short_anchor(product),mark=.8)
    out=bot._apply_v14_live_policy(product,signal(product),mo_quotes=frame)
    assert out["v14_action"]=="HOLD"
    assert out["v14_short_put_mark"]==.8

def test_full_ic_builder_missing_history_day_must_be_diagnostic(tmp_path,monkeypatch):
    import test_icim_round2_audit_lifecycle_20261021 as prior
    store,record=prior.recent_store(tmp_path/"ledger")
    anchors=prior.state.anchors_from_record(record); a=anchors["IC"]
    prior.ledger._empty_core(a,"IC")
    a.update(v14_route_state="short_put",v14_cycle_id="synthetic-existing-short",
        v14_short_put_contract=prior.real.M,v14_short_put_security_id="10012099",
        v14_short_put_qty_normalized=5.,v14_short_put_entry_premium=.4,
        v14_short_put_expiry=date(2026,12,23),v14_short_put_cost_basis="confirmed_dated_open",
        v14_short_put_entry_price_day=prior.PREVIOUS,
        verified_momentum_put_delta=0.,verified_momentum_put_qty=0)
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
    assert out.get("diagnostic_only") or out.get("v14_action")=="HOLD_FAIL_CLOSED", {k:out.get(k) for k in ["close_confirmed","ledger_eligible","v14_action","v14_short_put_mark","v14_candidate_data_status"]}
