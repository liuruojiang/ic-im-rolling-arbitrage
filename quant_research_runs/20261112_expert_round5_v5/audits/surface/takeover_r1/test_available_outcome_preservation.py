"""Audit the source outcome through standalone and public capture boundaries."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest
import requests

from test_surface_v5_r1 import bot, evidence, adapter, wire_sources, DAY, CLOCK, LISTED

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
OBS = []


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    pin_path = Path(os.environ["ICIM_SURFACE_AUDIT_PIN"])
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    assert hashlib.sha256((ROOT / "poe_ic_im_mainline_v1_4_bot.py").read_bytes()).hexdigest() == pin["main_sha256"]
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("external HTTP forbidden"))
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    monkeypatch.setattr(adapter, "configured_root", lambda: None)
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK)
    with bot.runtime_clock(CLOCK):
        yield


@pytest.mark.parametrize("scope", ["standalone", "public_query"])
@pytest.mark.parametrize("primary_failure", ["transport", "structure", "forbidden"])
def test_available_sina_result_retains_primary_failure_through_capture_and_replay(monkeypatch, tmp_path, scope, primary_failure):
    calls = wire_sources(monkeypatch, "sina")
    def sse():
        calls.append("listing:SSE")
        if primary_failure == "transport":
            raise requests.Timeout("controlled SSE timeout")
        if primary_failure == "structure":
            raise RuntimeError("controlled malformed SSE list")
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError("controlled SSE403", response=response)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", sse)
    observed_cache, results = [], []
    def builder(*a, **kw):
        months = bot.fetch_510500_expiries_with_failover()
        observed_cache.append(deepcopy(bot._IC_LISTING_QUERY_CACHE.get()))
        results.append(deepcopy(months))
        return {"product": "IC", "market_date": DAY, "months": months}
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    outer = None if scope == "standalone" else {"outer": "must remain unchanged"}
    token = bot._IC_LISTING_QUERY_CACHE.set(outer)
    try:
        with evidence.capture_session(tmp_path / "archive", DAY, CLOCK, {"scope": scope}) as session:
            captured = (bot.fetch_510500_expiries_with_failover() if scope == "standalone"
                        else bot.build_live_trade_signal("IC", CLOCK, "close")["months"])
        assert bot._IC_LISTING_QUERY_CACHE.get() is outer
        if outer is not None:
            assert outer == {"outer": "must remain unchanged"}
        reader = evidence.load_session(tmp_path / "archive", DAY)
        stored = reader.call("ic.months", {})
        expected_error = {"transport": "Timeout", "structure": "RuntimeError", "forbidden": "HTTPError"}[primary_failure]
        assert captured == stored["months"] == LISTED
        assert stored["status"] == "available" and stored["signal_day"] == DAY.isoformat()
        assert len(stored["failures"]) == 1
        failure = stored["failures"][0]
        assert failure["source"] == "SSE" and failure["error_type"] == expected_error
        assert failure["kind"] == ("transport" if primary_failure == "transport" else "unverified")
        if scope == "public_query":
            assert observed_cache[0]["listing"]["failures"] == stored["failures"]
        wire_before = list(calls)
        with evidence.replay_session(reader):
            replayed = (bot.fetch_510500_expiries_with_failover() if scope == "standalone"
                        else bot.build_live_trade_signal("IC", CLOCK, "close")["months"])
        assert replayed == captured and calls == wire_before and not reader.missing_inputs
        assert session.context["complete"] and not session.context["failures"]
        assert bot._IC_LISTING_QUERY_CACHE.get() is outer
        OBS.append({"case": f"{scope}-{primary_failure}", "stored": stored, "calls": calls,
                    "session_complete": session.context["complete"], "source_day_is_synthetic": True})
    finally:
        bot._IC_LISTING_QUERY_CACHE.reset(token)


@pytest.mark.parametrize("error", [RuntimeError, bot.CalendarCoverageError, evidence.EvidenceUnavailable])
def test_standalone_capture_restores_absent_cache_on_any_error(monkeypatch, tmp_path, error):
    wire_sources(monkeypatch, "available")
    def fail():
        raise error("controlled standalone source error")
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", fail)
    monkeypatch.setattr(bot, "fetch_sina_510500_expiries", fail)
    token = bot._IC_LISTING_QUERY_CACHE.set(None)
    try:
        with evidence.capture_session(tmp_path / "archive", DAY, CLOCK, {}):
            with pytest.raises((error, adapter.ListingProbeUnavailable)):
                bot.fetch_510500_expiries_with_failover()
            assert bot._IC_LISTING_QUERY_CACHE.get() is None
    finally:
        bot._IC_LISTING_QUERY_CACHE.reset(token)


def teardown_module():
    (HERE / "available_outcome_observations.json").write_text(json.dumps(OBS, ensure_ascii=False, indent=2), encoding="utf-8")
