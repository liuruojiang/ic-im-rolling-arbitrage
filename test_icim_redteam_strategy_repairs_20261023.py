"""STRAT-01 repairs: synthetic fault injection; preserved SSE identity fixture for full builder."""
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

@pytest.mark.parametrize("fault", ["missing_mark", "forged_pending", "forged_enter", "forged_roll", "wrong_day", "wrong_contract"])
def test_full_ic_missing_mark_cannot_advance_state(tmp_path,monkeypatch,fault):
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
    price_day=prior.PREVIOUS if fault=="missing_mark" else prior.DAY
    monkeypatch.setattr(bot,"fetch_option_closes",lambda *a:pd.Series([.4],index=[pd.Timestamp(price_day)]))
    out=bot.build_live_trade_signal("IC",prior.CLOCK,"close")
    if fault=="missing_mark":
        assert out.get("diagnostic_only") or out.get("v14_action") == "HOLD_FAIL_CLOSED"
        with pytest.raises(RuntimeError):
            prior.state.require_persistence_eligible(out, "IC")
    else:
        assert out["v14_action"]=="HOLD"
        prior.state.require_persistence_eligible(out,"IC")
        if fault.startswith("forged_"):
            out["v14_short_put_mark"]=None
            if fault=="forged_pending":
                out.update(v14_action="WAIT_SHORT_PUT_ROLL",v14_roll_pending=True,
                           v14_roll_trigger_day=prior.DAY,v14_roll_wait_reason="fixture")
            else:
                out["v14_action"]="ENTER_SHORT_PUT" if fault=="forged_enter" else "ROLL_SHORT_PUT"
            # The signal-only check cannot infer the old pending state. The
            # independent append check below must use the durable old anchor.
            prior.state.require_persistence_eligible(out,"IC")
        else:
            out["v14_short_put_mark_day" if fault=="wrong_day" else "v14_short_put_mark_contract"]=(
                prior.PREVIOUS if fault=="wrong_day" else "WRONG")
    other = prior.close_evidence(prior.ledger._signal("IM", anchors["IM"], prior.DAY))
    other["v14_opening_events"] = []
    with pytest.raises(RuntimeError, match="HOLD_FAIL_CLOSED|未确认|诊断|held short Put"):
        store.append_confirmed_signals(record, {"IC": out, "IM": other})
    reopened = prior.state.StateStore(store.root).load_latest()
    assert reopened["digest"] == record["digest"]
    assert reopened["sequence"] == record["sequence"]


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("mark", [None, float("nan"), float("inf"), -1, 0, True, "bad"])
def test_pure_policy_rejects_unknown_or_invalid_held_mark(product, mark):
    a = short_anchor(product)
    s = signal(product, v14_short_put_mark=mark)
    with pytest.raises(RuntimeError, match="held short Put"):
        policy.apply_policy(product, s, a, candidate=None)
    with pytest.raises(RuntimeError, match="held short Put"):
        policy.short_put_candidate_requirements(product, s, a)

@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("field,value", [("v14_short_put_mark_day", date(2026,11,30)),
                                         ("v14_short_put_mark_contract", "WRONG")])
def test_pure_policy_rejects_wrong_explicit_binding(product, field, value):
    s = signal(product, v14_short_put_mark=.8, **{field:value})
    with pytest.raises(RuntimeError, match="bound"):
        policy.apply_policy(product, s, short_anchor(product), candidate=None)

@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("special", ["pending", "expiry", "legacy_cost", "unconfirmed"])
def test_required_mark_does_not_change_existing_non_decay_paths(product, special):
    a = short_anchor(product)
    s = signal(product)
    if special == "pending":
        a.update(v14_roll_pending=True, v14_roll_trigger_day=date(2026,11,30))
    elif special == "expiry":
        a["v14_short_put_expiry"] = date(2026,12,1)
    elif special == "legacy_cost":
        a.update(v14_short_put_cost_basis=None, v14_short_put_entry_price_day=None)
    else:
        s["close_confirmed"] = False
    assert policy.short_put_mark_required(product, s, a) is False
    out = policy.apply_policy(product, s, a, candidate=None)
    assert out["v14_action"] == {"pending":"WAIT_SHORT_PUT_ROLL", "expiry":"PUBLISH_SHORT_PUT_EXPIRY_BRANCHES"}.get(special,"HOLD")


def test_required_day_falls_back_from_lagging_primary(monkeypatch):
    calls=[]
    def primary(sid):
        calls.append(("primary",sid))
        return pd.Series([.9],index=[pd.Timestamp("2026-11-30")])
    def backup(sid):
        calls.append(("backup",sid))
        return pd.Series([.8],index=[pd.Timestamp("2026-12-01")])
    monkeypatch.setattr(bot,"fetch_sina_option_closes",primary)
    monkeypatch.setattr(bot,"fetch_eastmoney_option_closes",backup)
    out=bot.fetch_option_closes("10012099",date(2026,12,1))
    assert calls == [("primary","10012099"),("backup","10012099")]
    assert out.iloc[0] == .8 and out.attrs["source"] == "Eastmoney"
    assert "2026-12-01" in out.attrs["source_failures"][0]


def test_required_day_missing_from_both_sources_rejects(monkeypatch):
    monkeypatch.setattr(bot,"fetch_sina_option_closes",lambda sid:pd.Series([.9],index=[pd.Timestamp("2026-11-30")]))
    monkeypatch.setattr(bot,"fetch_eastmoney_option_closes",lambda sid:pd.Series([.8],index=[pd.Timestamp("2026-11-30")]))
    with pytest.raises(RuntimeError,match="2026-12-01"):
        bot.fetch_option_closes("10012099",date(2026,12,1))


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("field,value", [("v14_short_put_mark",None), ("v14_short_put_mark_day",None),
                                         ("v14_short_put_mark_day",date(2026,11,30)),
                                         ("v14_short_put_mark_contract","WRONG")])
def test_new_write_guard_recomputes_mark_eligibility(product,field,value):
    import poe_ic_im_v1_4_state as state
    s = signal(product)
    s.update(short_anchor(product))
    s.update(v14_action="HOLD",v14_short_put_mark=.8,v14_short_put_mark_day=s["market_date"],
             v14_short_put_mark_contract=s["v14_short_put_contract"], persistence_eligible=True)
    s[field]=value
    with pytest.raises(RuntimeError, match="held short Put"):
        state.require_persistence_eligible(s,product,check_grid=False)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_valid_adapter_binds_mark_and_low_mark_preserves_wait(monkeypatch,product):
    a,frame=wire_adapter(monkeypatch,product,short_anchor(product),mark=.1)
    monkeypatch.setattr(bot,"_v14_ic_short_put_candidate",lambda *a,**k:{"tradable":False,"reason":"fixture"})
    monkeypatch.setattr(bot,"_v14_im_short_put_candidate",lambda *a,**k:{"tradable":False,"reason":"fixture"})
    out=bot._apply_v14_live_policy(product,signal(product),mo_quotes=frame)
    assert out["v14_action"]=="WAIT_SHORT_PUT_ROLL"
    assert out["v14_short_put_mark_day"] == date(2026,12,1)
    assert out["v14_short_put_mark_contract"] == a["v14_short_put_contract"]


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pre_repeat_roll_used_does_not_need_new_decay_mark(product):
    a=short_anchor(product, v14_early_roll_used=True,
                   v14_short_put_entry_price_day=date(2026,9,21))
    s=signal(product)
    s["market_date"]=date(2026,9,25)
    assert policy.short_put_mark_required(product,s,a) is False
    assert policy.apply_policy(product,s,a,candidate=None)["v14_action"]=="HOLD"


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_reentry_blocked_still_needs_observed_decay(product):
    a=short_anchor(product)
    s=signal(product,valuation_tier=2,valuation_puts_per_full_core=2,momentum_120=-.1)
    assert policy.seller_prerequisites(product,s)[0] is False
    with pytest.raises(RuntimeError,match="held short Put"):
        policy.apply_policy(product,s,a,candidate=None)
    s["v14_short_put_mark"]=.1
    assert policy.apply_policy(product,s,a,candidate=None)["v14_action"]=="WAIT_SHORT_PUT_ROLL"


def test_required_day_uses_unchanged_real_original_chain_keys(monkeypatch):
    from pathlib import Path
    import ic_im_evidence_adapter_v1 as adapter
    import ic_im_signal_evidence_v1 as evidence
    saved=Path(__file__).parent/'quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle'
    day=date(2026,10,22)
    reader,clock=adapter.load_original(bot,saved,"IC",day)
    calls=[]
    original=evidence.EvidenceReader.call
    def trace(self,role,key):
        calls.append((role,deepcopy(key)))
        return original(self,role,key)
    monkeypatch.setattr(evidence.EvidenceReader,"call",trace)
    with bot.runtime_clock(clock), evidence.replay_session(reader):
        old=bot.fetch_option_closes("10012149")
        baseline=list(calls);calls.clear()
        required=bot.fetch_option_closes("10012149",day)
        assert calls==baseline
        pd.testing.assert_series_equal(old,required)
        assert required.loc[pd.Timestamp(day)]==pytest.approx(.2614)
        with pytest.raises(evidence.EvidenceUnavailable,match="required option close day"):
            bot.fetch_option_closes("10012149",date(2026,10,21))


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pre_execution_entry_target_does_not_need_nonexistent_held_mark(product):
    from test_ic_im_v1_4_policy import _signal, _candidate
    import poe_ic_im_v1_4_state as state
    source=_signal(product,market_date=date(2026,9,21),next_trade_date=date(2026,9,22))
    result=policy.apply_policy(product,source,policy.default_extension(product),candidate=_candidate(product))
    assert result["v14_action"]=="ENTER_SHORT_PUT"
    assert result.get("v14_short_put_mark") is None
    state.require_persistence_eligible(result,product,check_grid=False)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_pre_execution_roll_mark_binds_old_held_leg_not_new_target(product):
    from test_ic_im_v1_4_policy import _signal, _candidate
    import poe_ic_im_v1_4_state as state
    day=date(2026,9,21)
    anchor=short_anchor(product,v14_short_put_entry_price_day=date(2026,9,18))
    new=_candidate(product,contract="510500P2701M07500" if product=="IC" else "MO2701-P-7200",
                   expiry=date(2027,1,27 if product=="IC" else 15))
    source=_signal(product,market_date=day,next_trade_date=date(2026,9,22),v14_short_put_mark=.1,
                   v14_short_put_mark_day=day,v14_short_put_mark_contract=anchor["v14_short_put_contract"])
    result=policy.apply_policy(product,source,anchor,candidate=new,roll_candidate=new)
    assert result["v14_action"]=="ROLL_SHORT_PUT"
    assert result["v14_short_put_mark_contract"]!=result["v14_short_put_contract"]
    state.require_persistence_eligible(result,product,check_grid=False)
