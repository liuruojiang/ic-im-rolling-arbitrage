"""Independent dual-consumer listing-cache and real query lexer audit."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_evidence_adapter_v1 as adapter
import ic_im_expiry_evidence_v1 as expiry
import ic_im_signal_evidence_v1 as evidence

ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
PIN = json.loads((HERE.parents[2] / "candidate_source_pin_r1.json").read_text(encoding="utf-8"))
BASE = ROOT / "tests/fixtures/icim_external_review_round2_20261021"
DAY = date(2026, 11, 13)  # explicitly artificial new-policy day, not current quotes
CLOCK = datetime(2026, 11, 13, 18, tzinfo=bot.BEIJING)
LISTED = ["202611", "202612", "202703", "202706"]
OBS = []


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    assert hashlib.sha256((ROOT / "poe_ic_im_mainline_v1_4_bot.py").read_bytes()).hexdigest() == PIN["main_sha256"]
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("external HTTP forbidden"))
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    monkeypatch.setattr(adapter, "configured_root", lambda: None)
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK)
    with bot.runtime_clock(CLOCK):
        yield


def month_wire(month):
    master = json.loads((BASE / "sse_contract_master.json").read_text(encoding="utf-8"))
    quote = json.loads((BASE / "sse_december_identity.json").read_text(encoding="utf-8"))
    expiry_day = bot._fourth_wednesday(2000+int(month[:2]), int(month[2:]))
    ids = {}
    for index, row in enumerate(master["result"]):
        original = row["SECURITY_ID"]
        ids[original] = str(22000000+int(month)*100+index)
        row["SECURITY_ID"] = ids[original]
        row["CONTRACT_ID"] = row["CONTRACT_ID"][:7]+month+row["CONTRACT_ID"][11:]
        row["TIMESAVE"] = DAY.isoformat()
        for key in ["EXPIRE_DATE", "EXERCISE_DATE", "END_DATE"]:
            row[key] = expiry_day.strftime("%Y%m%d")
    for row in quote["list"]:
        row[0] = row[0][:7]+month+row[0][11:]
        row[5] = ids[row[5]]
        if row[0] == f"510500P{month}M06500":
            row[1] = .35
    quote.update(date=20261113, time=150000)
    return master, quote


def wire_sources(monkeypatch, mode="available"):
    calls = []
    months = {m: month_wire(m) for m in ["2612", "2703"]}
    def sse():
        calls.append("listing:SSE")
        if mode in {"transport", "unverified", "sina"}:
            raise requests.Timeout("controlled SSE timeout")
        if mode == "forbidden":
            response = requests.Response(); response.status_code = 403
            raise requests.HTTPError("controlled403", response=response)
        return list(LISTED)
    def sina():
        calls.append("listing:Sina")
        if mode == "sina":
            return list(LISTED)
        if mode == "transport":
            raise requests.ConnectionError("controlled Sina transport")
        raise RuntimeError("controlled malformed listing")
    def quote(path, params):
        month = "2612" if path.endswith("_12") else "2703"
        calls.append("chain:"+month)
        return deepcopy(months[month][1])
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", sse)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", sina)
    monkeypatch.setattr(bot, "_sse_quote_json", quote)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda month, day: deepcopy(months[month][0]))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    return calls


def dual_consumers(monkeypatch, mode="available", reverse=False):
    outputs = []
    def builder(product, now=None, query_mode="close"):
        signal = dict(market_date=DAY, next_trade_date=date(2026, 11, 16), etf_price=6.871, future_last=6600.)
        def candidate():
            result = bot._v14_ic_short_put_candidate(signal)
            assert result["tradable"] is (mode in {"available", "sina", "transport"})
            if result["tradable"]:
                assert result["contract"] == "510500P2612M06500"
            else:
                assert result["reason"] == "listing_unverified"
            return result
        def reset():
            if mode in {"available", "sina"}:
                result = bot.select_ic_put_for_reset(DAY, 6.871, 6600., .25)
                assert result["contract"] == "510500P2703M06500" and result["qty"] > 0
                return {"contract": result["contract"], "qty": result["qty"]}
            expected = adapter.ListingProbeTransportUnavailable if mode == "transport" else adapter.ListingProbeUnavailable
            with pytest.raises(expected) as caught:
                bot.select_ic_put_for_reset(DAY, 6.871, 6600., .25)
            assert caught.value.outcome["status"] == ("transport_unavailable" if mode == "transport" else "unverified")
            return {"error": type(caught.value).__name__}
        if reverse:
            reset_result, candidate_result = reset(), candidate()
        else:
            candidate_result, reset_result = candidate(), reset()
        outputs.append({"candidate": candidate_result, "reset": reset_result, "signal": signal})
        return {"product": product, "market_date": DAY, "persistence_eligible": False}
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    return outputs


@pytest.mark.parametrize("mode", ["available", "sina"])
@pytest.mark.parametrize("reverse", [False, True])
def test_actual_candidate_and_reset_share_one_listing_per_public_query(monkeypatch, mode, reverse):
    calls = wire_sources(monkeypatch, mode)
    outputs = dual_consumers(monkeypatch, mode, reverse)
    outer = {"outside": "sentinel"}
    token = bot._IC_LISTING_QUERY_CACHE.set(outer)
    try:
        for count in [1, 2]:
            bot.build_live_trade_signal("IC", CLOCK, "close")
            assert calls.count("listing:SSE") == count
            assert calls.count("listing:Sina") == (count if mode == "sina" else 0)
            assert bot._IC_LISTING_QUERY_CACHE.get() is outer
        assert len(outputs) == 2 and calls.count("chain:2612") == calls.count("chain:2703") == 2
    finally:
        bot._IC_LISTING_QUERY_CACHE.reset(token)
    OBS.append({"case": f"dual-{mode}-reverse{reverse}", "calls": calls, "outputs": outputs})


@pytest.mark.parametrize("mode", ["transport", "unverified", "forbidden"])
def test_unavailable_listing_is_shared_by_real_candidate_and_blocked_core_reset(monkeypatch, mode):
    calls = wire_sources(monkeypatch, mode)
    outputs = dual_consumers(monkeypatch, mode)
    for count in [1, 2]:
        bot.build_live_trade_signal("IC", CLOCK, "close")
        assert calls.count("listing:SSE") == calls.count("listing:Sina") == count
    assert calls.count("chain:2703") == 0
    assert calls.count("chain:2612") == (2 if mode == "transport" else 0)
    OBS.append({"case": "dual-unavailable-"+mode, "calls": calls, "outputs": outputs})


@pytest.mark.parametrize("mode", ["available", "transport", "unverified"])
def test_same_capture_role_merges_two_public_queries_and_replay_dual_consumers(monkeypatch, tmp_path, mode):
    calls = wire_sources(monkeypatch, mode)
    outputs = dual_consumers(monkeypatch, mode)
    with evidence.capture_session(tmp_path / "archive", DAY, CLOCK,
                                   {"scope": "artificial dual-consumer audit"}) as session:
        for _ in range(2):
            bot.build_live_trade_signal("IC", CLOCK, "close")
    assert session.context["complete"] and not session.context["failures"]
    assert calls.count("listing:SSE") == 1
    assert calls.count("listing:Sina") == (0 if mode == "available" else 1)
    assert sum(row["role"] == "ic.months" for row in session.entries.values()) == 1
    reader = evidence.load_session(tmp_path / "archive", DAY)
    envelope = reader.call("ic.months", {})
    assert envelope["signal_day"] == DAY.isoformat()
    assert envelope["status"] == ("transport_unavailable" if mode == "transport" else mode)
    before = list(calls)
    with evidence.replay_session(reader):
        bot.build_live_trade_signal("IC", CLOCK, "close")
    assert calls == before and not reader.missing_inputs
    assert outputs[0] == outputs[1] == outputs[2]
    OBS.append({"case": "capture-dual-"+mode, "calls": calls, "outputs": outputs,
                "envelope": envelope, "entries": list(session.entries.values())})


@pytest.mark.parametrize("mode", ["available", "transport", "unverified"])
def test_returned_list_or_unavailability_exception_is_not_the_cache_object(monkeypatch, mode):
    calls = wire_sources(monkeypatch, mode)
    def builder(*a, **kw):
        for index in range(3):
            if mode == "available":
                value = bot.fetch_510500_expiries_with_failover()
                assert value == LISTED
                value[:] = ["MUTATED"]
            else:
                with pytest.raises(adapter.ListingProbeUnavailable) as caught:
                    bot.fetch_510500_expiries_with_failover()
                assert caught.value.outcome["months"] == []
                assert caught.value.outcome["failures"][0]["source"] == "SSE"
                caught.value.outcome["months"].append("MUTATED")
                caught.value.outcome["failures"][0]["source"] = "MUTATED"
        return {"product": "IC", "market_date": DAY}
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    bot.build_live_trade_signal("IC", CLOCK, "close")
    assert calls.count("listing:SSE") == 1 and calls.count("listing:Sina") == (0 if mode == "available" else 1)


@pytest.mark.parametrize("error", [RuntimeError, expiry.ExpiryEvidenceConflict,
                                    evidence.EvidenceUnavailable, bot.CalendarCoverageError])
def test_listing_query_cache_restored_after_every_exception_class(monkeypatch, error):
    calls = wire_sources(monkeypatch)
    outer = {"outer": "must stay untouched"}
    token = bot._IC_LISTING_QUERY_CACHE.set(outer)
    def builder(*a, **kw):
        assert bot.fetch_510500_expiries_with_failover() == LISTED
        raise error("controlled post-cache failure")
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    try:
        for count in [1, 2]:
            if error is RuntimeError:
                with pytest.raises(RuntimeError): bot.build_live_trade_signal("IC", CLOCK, "close")
            else:
                result = bot.build_live_trade_signal("IC", CLOCK, "close")
                assert result["diagnostic_only"] and not result["persistence_eligible"]
            assert bot._IC_LISTING_QUERY_CACHE.get() is outer and outer == {"outer": "must stay untouched"}
            assert calls.count("listing:SSE") == count
    finally:
        bot._IC_LISTING_QUERY_CACHE.reset(token)


@pytest.mark.parametrize("error", [expiry.ExpiryEvidenceConflict, evidence.EvidenceUnavailable, bot.CalendarCoverageError])
def test_hard_source_error_is_not_cached_as_an_available_listing(monkeypatch, error):
    calls, caches = [], []
    def source():
        calls.append(1)
        if len(calls) == 1: raise error("controlled hard source failure")
        return list(LISTED)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", source)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", lambda: pytest.fail("hard conflict fallback"))
    def builder(*a, **kw):
        try:
            value = bot.fetch_510500_expiries_with_failover()
        finally:
            caches.append(deepcopy(bot._IC_LISTING_QUERY_CACHE.get()))
        assert value == LISTED
        return {"product": "IC", "market_date": DAY}
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    result = bot.build_live_trade_signal("IC", CLOCK, "close")
    assert result["diagnostic_only"] and caches[0] == {}
    bot.build_live_trade_signal("IC", CLOCK, "close")
    assert len(calls) == 2 and caches[1]["listing"]["status"] == "available"


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_warm_query_cannot_hide_missing_or_changed_original_listing_blob(monkeypatch, tmp_path, damage):
    value = {"revision": adapter.LISTING_OUTCOME_REVISION, "signal_day": DAY.isoformat(),
             "status": "available", "months": list(LISTED), "failures": []}
    with evidence.capture_session(tmp_path / "archive", DAY, CLOCK, {}) as session:
        session.call("ic.months", {}, lambda: value)
    reader = evidence.load_session(tmp_path / "archive", DAY)
    descriptor = next(iter(session.entries.values()))
    blob = tmp_path / "archive/blobs" / (descriptor["sha256"]+descriptor["suffix"])
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: pytest.fail("original blob network fallback"))
    def builder(*a, **kw):
        assert bot.fetch_510500_expiries_with_failover() == LISTED
        if damage == "missing": blob.unlink()
        else: blob.write_bytes(b"changed raw input")
        bot.fetch_510500_expiries_with_failover()
        pytest.fail("damaged original listing was cached as available")
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    with evidence.replay_session(reader):
        result = bot.build_live_trade_signal("IC", CLOCK, "close")
    assert result["diagnostic_status"] == "original_evidence_unavailable"
    assert not result["persistence_eligible"]


@pytest.mark.parametrize("instrument,products", [("IC", ("IC",)), ("中证500", ("IC",)),
    ("中证1000", ("IM",)), ("510500", ("IC",)), ("IM2612", ("IM",)), ("MO2612-P-6400", ("IM",))])
@pytest.mark.parametrize("month", ["12月份合约", "12月的合约", "12月份的合约"])
def test_new_month_variants_dispatch_real_snapshot_route(monkeypatch, instrument, products, month):
    calls = []
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=f"查询{instrument} {month}"))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_snapshot", lambda self, selected: calls.append(selected))
    bot.ICIMMainlinesBot()._run_impl()
    assert calls == [products]


@pytest.mark.parametrize("text", ["查询中证1000 13月份合约", "查询IC 1.2月的合约", "查询IC 1. 2月份的合约",
    "查询510500 12026年12月份合约", "查询IM2612 12月份合约 2026-02-30",
    "查询MO2612-P-6400 12月的合约 2026-13表现", "查询IC 2026年13月份合约",
    "查询IC 12月份的合约 2026-03-311"])
def test_new_variants_never_hide_invalid_true_dates_or_decimal_months(monkeypatch, text):
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=text))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "_handle_snapshot", lambda *a: pytest.fail("bad date snapshot"))
    with pytest.raises(ValueError): bot.ICIMMainlinesBot()._run_impl()


@pytest.mark.parametrize("text,start,end,products", [
    ("2026-01-05 IM2612 12月份合约", date(2026,1,5), date(2026,1,5), ("IM",)),
    ("MO2612-P-6400 12月的合约 2026-03-01", date(2026,3,1), date(2026,3,1), ("IM",)),
    ("2026年3月至今 查询中证1000 12月份的合约", date(2026,3,1), DAY, ("IM",)),
    ("查询510500 2026年12月份的合约 2026年3月至今", date(2026,3,1), DAY, ("IC",))])
def test_front_or_back_true_date_survives_contract_reference(monkeypatch,text,start,end,products):
    called = []
    monkeypatch.setattr(bot.poe,"query",SimpleNamespace(text=text))
    monkeypatch.setattr(bot.ICIMMainlinesBot,"_handle_performance",lambda self,intent:called.append(intent))
    bot.ICIMMainlinesBot()._run_impl()
    assert len(called)==1 and (called[0].products,called[0].start,called[0].end)==(products,start,end)


@pytest.mark.parametrize("text,route", [("IC 12月份合约 参数", "params"),
    ("IC 12月的合约 信号", "close_signal"), ("IM 12月份的合约 实时信号", "intraday_signal"),
    ("IC 12月份的合约 2026-01-05表现 信号", "performance")])
def test_registered_explicit_intent_priority_is_preserved(text,route):
    assert bot.classify_query(text,DAY).route==route


def teardown_module():
    (HERE / "dual_consumer_observations.json").write_text(json.dumps(OBS,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
