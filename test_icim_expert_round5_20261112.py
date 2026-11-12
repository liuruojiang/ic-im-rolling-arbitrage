"""Fifth review: controlled market dates, actual capture/reader/policy contracts."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_evidence_adapter_v1 as adapter
import ic_im_expiry_evidence_v1 as expiry
import ic_im_signal_evidence_v1 as evidence

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 11, 13)
CLOCK = datetime(2026, 11, 13, 18, tzinfo=bot.BEIJING)
LISTED = ["202611", "202612", "202703", "202706"]
spec = importlib.util.spec_from_file_location("v4_listing_control_shapes", ROOT /
    "quant_research_runs/20261112_expert_round4_v4/audits/ledger/test_listing_candidate_routes.py")
shapes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shapes)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *a, **k: pytest.fail("review tests prohibit external HTTP"))
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    monkeypatch.setattr(adapter, "configured_root", lambda: None)
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK)
    with bot.runtime_clock(CLOCK):
        yield


def wire_policy(monkeypatch, mode, route="entry"):
    anchor, signal, held, clock, observations = shapes.configure_policy(monkeypatch, DAY, route)
    signal["next_trade_date"] = date(2026, 11, 16)
    target = shapes.market_inputs(DAY, 12)
    packets = {"2612": target}
    if held is not None:
        for row in held[3]["list"]:
            if row[0] == anchor["v14_short_put_contract"]:
                row[1] = .1
        packets["2611"] = held
    calls = []
    def sse():
        calls.append("listing:SSE")
        if mode in {"transport", "unverified"}:
            raise requests.Timeout("controlled SSE timeout")
        return list(LISTED)
    def sina():
        calls.append("listing:Sina")
        if mode == "transport":
            raise requests.ConnectionError("controlled Sina connection failure")
        raise RuntimeError("controlled invalid month collection")
    def quote(path, params):
        month = "26" + path.rsplit("_", 1)[-1]
        calls.append("chain:" + month)
        return deepcopy(packets[month][3])
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", sse)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", sina)
    monkeypatch.setattr(bot, "_sse_quote_json", quote)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master",
                        lambda month, day: deepcopy(packets[month][2]["payload"]))
    original_closes = bot.fetch_option_closes
    if held is not None:
        monkeypatch.setattr(bot, "fetch_option_closes", lambda *a, **k:
                            pd.Series([.1], index=pd.to_datetime([DAY])))
    def builder(product, clock, mode):
        result = bot._apply_v14_live_policy(product, deepcopy(signal))
        # This is a controlled input-qualification flag, not a full-market
        # ledger certification. Actual entries/chains/expiry/policy remain real.
        result["persistence_eligible"] = True
        return result
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    return anchor, signal, calls, original_closes


@pytest.mark.parametrize("route", ["entry", "roll"])
@pytest.mark.parametrize("mode", ["transport", "unverified", "available"])
def test_capture_replay_listing_outcome_preserves_actual_policy_branch(monkeypatch, tmp_path, route, mode):
    anchor, signal, calls, original_closes = wire_policy(monkeypatch, mode, route)
    before = deepcopy(anchor)
    archive = tmp_path / "inputs"
    with evidence.capture_session(archive, DAY, CLOCK, {"product": "IC", "original_close": True,
            "input_revision": adapter.INPUT_REVISION, "test_scope": "controlled policy and consumed inputs"}) as session:
        live = bot.build_live_trade_signal("IC", CLOCK, "close")
    assert session.context["complete"] is True, session.context["failures"]
    assert not session.context["failures"]
    assert live["input_evidence_status"] == "complete"
    reader = evidence.load_session(archive, DAY)
    recorded = reader.call("ic.months", {})
    assert recorded["signal_day"] == DAY.isoformat() and recorded["status"] == (
        "transport_unavailable" if mode == "transport" else mode)
    assert len([x for x in session.entries.values() if x["role"] == "ic.months"]) == 1
    if mode == "unverified":
        assert live["v14_action"] == ("HOLD" if route == "entry" else "WAIT_SHORT_PUT_ROLL")
        assert "listing_unverified" in live["v14_candidate_data_status"]
        assert "chain:2612" not in calls
    else:
        assert live["v14_action"] == ("ENTER_SHORT_PUT" if route == "entry" else "ROLL_SHORT_PUT")
        assert "chain:2612" in calls
    if mode == "transport":
        assert "listing_transport_unavailable" in live["v14_candidate_data_status"]
        assert any("已退回直接取链" in x for x in live["data_notes"])
    assert live["momentum_next_weight"] == signal["momentum_next_weight"]
    if mode == "unverified":
        reference = deepcopy(signal)
        if route == "roll":
            reference.update(v14_short_put_mark=.1, v14_short_put_mark_day=DAY,
                             v14_short_put_mark_contract=anchor["v14_short_put_contract"])
        expected = bot.v14_policy.apply_policy("IC", reference, before,
            candidate={"tradable": False}, roll_candidate={"tradable": False})
        for field in ("core_units_current", "core_units_target", "total_units_target"):
            assert live[field] == expected[field]
        assert not live.get("diagnostic_only")
    if route == "roll":
        monkeypatch.setattr(bot, "fetch_option_closes", original_closes)
    calls_before_replay = list(calls)
    token = bot._HISTORICAL_REPLAY_DAY.set(DAY)
    try:
        with evidence.replay_session(reader):
            replay = bot.build_live_trade_signal("IC", CLOCK, "close")
    finally:
        bot._HISTORICAL_REPLAY_DAY.reset(token)
    for key in live:
        if key != "input_evidence_status":
            assert replay[key] == live[key], key
    assert calls == calls_before_replay and not reader.missing_inputs and anchor == before


def outcome(status="transport_unavailable"):
    failures = [dict(source="SSE", kind="transport", error_type="Timeout", error="timeout"),
                dict(source="Sina", kind="transport", error_type="ConnectionError", error="connection")]
    return {"revision": adapter.LISTING_OUTCOME_REVISION, "signal_day": DAY.isoformat(),
            "status": status, "months": [], "failures": failures}


@pytest.mark.parametrize("change", ["revision", "day", "months", "transport_kind", "hard_error", "legacy_list"])
def test_bad_listing_envelope_never_becomes_transport_fallback(monkeypatch, tmp_path, change):
    value = outcome()
    if change == "revision": value["revision"] = "untrusted"
    elif change == "day": value["signal_day"] = "2026-11-12"
    elif change == "months": value["months"] = ["202612"]
    elif change == "transport_kind": value["failures"][0]["error_type"] = "RuntimeError"
    elif change == "hard_error": value["failures"][0].update(kind="unverified", error_type="ExpiryEvidenceConflict")
    else: value = list(LISTED)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("ic.months", {}, lambda: value)
    reader = evidence.load_session(tmp_path, DAY)
    signal = dict(market_date=DAY, next_trade_date=date(2026, 11, 16))
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda *a: pytest.fail("bad envelope used direct chain"))
    with evidence.replay_session(reader), pytest.raises(evidence.EvidenceUnavailable):
        bot._v14_ic_short_put_candidate(signal)


def test_missing_listing_role_is_not_a_recorded_transport_outcome(monkeypatch, tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("control", {}, lambda: 1)
    reader = evidence.load_session(tmp_path, DAY)
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda *a: pytest.fail("missing input fallback"))
    with evidence.replay_session(reader), pytest.raises(evidence.EvidenceUnavailable):
        bot._v14_ic_short_put_candidate(dict(market_date=DAY, next_trade_date=date(2026, 11, 16)))


@pytest.mark.parametrize("legacy_day", [date(2026, 11, 11), date(2026, 11, 12)])
def test_pre_adoption_candidate_does_not_request_listing(monkeypatch, legacy_day):
    from test_icim_expert_round4_20261112 import candidate_inputs
    signal, calls = candidate_inputs(monkeypatch, legacy_day)
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", lambda: pytest.fail("old-day new probe"))
    clock = datetime.combine(legacy_day, CLOCK.timetz())
    with bot.runtime_clock(clock):
        result = bot._v14_ic_short_put_candidate(signal)
    assert result["tradable"] and len(calls) == 1


@pytest.mark.parametrize("mode", ["available", "transport", "unverified"])
def test_listing_query_cache_is_shared_and_restored(monkeypatch, mode):
    wire_policy(monkeypatch, mode)
    calls = []
    def sse():
        calls.append("SSE")
        if mode == "available": return list(LISTED)
        raise requests.Timeout("controlled")
    def sina():
        calls.append("Sina")
        if mode == "transport": raise requests.ConnectionError("controlled")
        raise RuntimeError("invalid listed collection")
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", sse)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", sina)
    observed = []
    def builder(*args):
        for _ in range(3):
            try:
                months = bot.fetch_510500_expiries_with_failover()
                assert months == LISTED
                months.clear()
            except adapter.ListingProbeUnavailable as exc:
                observed.append(exc.outcome["status"])
        return {"diagnostic_only": True}
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    outer = {"outer": "untouched"}
    token = bot._IC_LISTING_QUERY_CACHE.set(outer)
    try:
        for _ in range(2):
            bot.build_live_trade_signal("IC", CLOCK, "close")
            assert bot._IC_LISTING_QUERY_CACHE.get() is outer
    finally:
        bot._IC_LISTING_QUERY_CACHE.reset(token)
    assert calls == (["SSE", "SSE"] if mode == "available" else ["SSE", "Sina"]*2)
    if mode != "available": assert len(observed) == 6


def test_listing_cache_resets_on_unexpected_query_error(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: calls.append(1) or list(LISTED))
    def fail(*args):
        bot.fetch_510500_expiries_with_failover()
        raise RuntimeError("controlled query failure")
    monkeypatch.setattr(bot, "_build_live_trade_signal", fail)
    before = bot._IC_LISTING_QUERY_CACHE.get()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="controlled query failure"):
            bot.build_live_trade_signal("IC", CLOCK, "close")
        assert bot._IC_LISTING_QUERY_CACHE.get() is before
    assert calls == [1, 1]


def test_hard_listing_conflict_cannot_be_resolved_by_a_later_success(monkeypatch, tmp_path):
    def fail(): raise expiry.ExpiryEvidenceConflict("controlled positive conflict")
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", fail)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", lambda: pytest.fail("conflict fallback"))
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        with pytest.raises(expiry.ExpiryEvidenceConflict):
            bot.fetch_510500_expiries_with_failover()
        monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: list(LISTED))
        assert bot.fetch_510500_expiries_with_failover() == LISTED
    assert not session.context["complete"]
    assert any(x["role"] == "ic.months.conflict" and not x["resolved"] for x in session.context["failures"])


def master_case(snapshot):
    frame, stamp, proof, raw = shapes.market_inputs(DAY, 12)
    for row in proof["payload"]["result"]: row["TIMESAVE"] = snapshot.isoformat()
    return frame, proof["payload"]


@pytest.mark.parametrize("observed", [datetime(2026, 11, 14, 12, tzinfo=bot.BEIJING),
                                     datetime(2026, 11, 16, 8, tzinfo=bot.BEIJING)])
def test_current_master_cannot_prove_previous_close_but_does_not_contradict_it(observed):
    frame, payload = master_case(observed.date())
    with pytest.raises(expiry.ExpiryMasterUnavailable):
        expiry.verify_sse_chain(frame, payload, as_of=DAY, month="202612",
            model_expiry=date(2026, 12, 23), observation_clock=observed)


@pytest.mark.parametrize("bad", ["mixed", "future", "expiry", "identity"])
def test_inapplicable_master_still_rejects_real_contradictions(bad):
    observed = datetime(2026, 11, 14, 12, tzinfo=bot.BEIJING)
    frame, payload = master_case(observed.date())
    if bad == "mixed": payload["result"][0]["TIMESAVE"] = DAY.isoformat()
    elif bad == "future":
        for row in payload["result"]: row["TIMESAVE"] = "2026-11-15"
    elif bad == "expiry": payload["result"][0]["EXPIRE_DATE"] = "20261224"
    else: payload["result"][0]["CONTRACT_ID"] = "wrong"
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_sse_chain(frame, payload, as_of=DAY, month="202612",
            model_expiry=date(2026, 12, 23), observation_clock=observed)


def test_unavailable_and_duplicate_key_types_preserve_fallback_contract():
    assert issubclass(expiry.ExpiryMasterUnavailable, RuntimeError)
    assert not issubclass(expiry.ExpiryMasterUnavailable, expiry.ExpiryEvidenceConflict)
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        json.loads('{"result":[],"result":[]}', object_pairs_hook=expiry.unique_object)


@pytest.mark.parametrize("text", ["查询IC 12月份合约", "查询IC 12月的合约", "查询中证1000 3月份合约",
                                  "查询510500 2026年12月的合约", "查询IM2612 12月份的合约"])
def test_month_reference_variants_use_snapshot(text):
    assert bot.classify_query(text, DAY).route == "snapshot"


@pytest.mark.parametrize("text", ["查询IC 13月份合约", "查询IC 1.2月的合约",
                                  "查询IC 12月份合约 2026-13", "查询IC 12月的合约 2026-02-30表现"])
def test_month_variants_never_hide_real_date_errors(text):
    with pytest.raises(ValueError): bot.classify_query(text, DAY)


def test_separate_real_date_and_month_variant_keep_performance():
    value = bot.classify_query("2026-01-05 IM2612 12月份合约", DAY)
    assert (value.route, value.products, value.start, value.end) == (
        "performance", ("IM",), date(2026, 1, 5), date(2026, 1, 5))


@pytest.mark.parametrize("through_public_query", [False, True])
def test_successful_secondary_listing_capture_preserves_primary_failure(
        monkeypatch, tmp_path, through_public_query):
    calls = []

    def sse():
        calls.append("SSE")
        raise requests.Timeout("controlled primary timeout")

    def sina():
        calls.append("Sina")
        return list(LISTED)

    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", sse)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", sina)
    monkeypatch.setattr(bot, "_build_live_trade_signal", lambda *a: {
        "months": bot.fetch_510500_expiries_with_failover()})

    def consume():
        if through_public_query:
            return bot.build_live_trade_signal("IC", CLOCK, "close")["months"]
        return bot.fetch_510500_expiries_with_failover()

    outer_cache = bot._IC_LISTING_QUERY_CACHE.get()
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        assert consume() == LISTED
    assert session.context["complete"]
    assert bot._IC_LISTING_QUERY_CACHE.get() is outer_cache
    reader = evidence.load_session(tmp_path, DAY)
    stored = reader.call("ic.months", {})
    assert stored["status"] == "available" and stored["months"] == LISTED
    assert stored["failures"] == [{"source": "SSE", "kind": "transport",
        "error_type": "Timeout", "error": "controlled primary timeout"}]
    with evidence.replay_session(reader):
        assert consume() == LISTED
    assert calls == ["SSE", "Sina"] and not reader.missing_inputs
    assert bot._IC_LISTING_QUERY_CACHE.get() is outer_cache
