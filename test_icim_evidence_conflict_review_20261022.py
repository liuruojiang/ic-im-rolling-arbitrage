"""Conflict responses remain displayable, but no unsealed decision may persist."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import importlib
import json

import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state

DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 16, tzinfo=bot.BEIJING)
URL = "https://example.invalid/tstyle/510500_12"
META = {"product": "IC", "original_close": True, "input_revision": adapter.INPUT_REVISION}


def response(body=b"A", status=200, encoding="utf-8", headers=None):
    r = requests.Response()
    r._content, r.status_code, r.encoding = body, status, encoding
    r._content_consumed = True
    r.headers.update(headers or {})
    return r


def minimum(session):
    session.call("probe", {}, lambda: True)


@pytest.mark.parametrize("difference", ["body", "status", "encoding", "headers"])
def test_raw_conflict_marks_immediately_without_raising(tmp_path, difference):
    other = {"body": b"B"} if difference == "body" else {"status": 503} if difference == "status" else {"encoding": "gbk"} if difference == "encoding" else {"headers": {"ETag": "changed"}}
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, {"month": "2612"}, response())
        session.raw_response(URL, {"month": "2612"}, response(**other))
        conflicts = [f for f in session.failures if f["role"] == "raw_http_conflict"]
        assert len(conflicts) == 1 and conflicts[0]["resolved"] is False
    assert session.context["complete"] is False
    with pytest.raises(evidence.EvidenceUnavailable, match="incomplete"):
        adapter.has_original(tmp_path, "IC", DAY)


def test_http_wrapper_still_returns_both_conflicting_responses(tmp_path, monkeypatch):
    replies = iter([response(b"A"), response(b"B")])
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: next(replies))
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        assert bot._evidence_http_get(URL).content == b"A"
        assert bot._evidence_http_get(URL).content == b"B"
        assert any(f["role"] == "raw_http_conflict" for f in session.failures)


def test_parameter_key_order_cannot_hide_same_request_conflict(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, {"select": "x", "begin": 0}, response())
        session.raw_response(URL, {"begin": 0, "select": "x"}, response(b"B"))
        assert any(f["role"] == "raw_http_conflict" for f in session.failures)


def test_normal_http_query_never_opens_capture(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response())
    monkeypatch.setattr(evidence.CaptureSession, "raw_response", lambda *a, **k: pytest.fail("normal query entered capture"))
    assert evidence.current_session.get() is None
    assert bot._evidence_http_get(URL).content == b"A"


@pytest.mark.parametrize("difference", ["identical", "body"])
def test_public_builder_binds_signal_until_capture_seal(monkeypatch, tmp_path, difference):
    import test_icim_calendar_capture_review_20261022 as fixtures
    clock = fixtures.changing_raw_providers(monkeypatch, tmp_path, difference)
    with evidence.capture_session(tmp_path / "evidence", fixtures.DAY, clock, META) as session:
        signal = bot.build_live_trade_signal("IC", clock, "close")
        assert signal["persistence_eligible"] is False
        assert signal["input_evidence_status"] in {"recording", "inconsistent"}
    assert signal["persistence_eligible"] is (difference == "identical")
    assert signal["input_evidence_status"] == ("complete" if difference == "identical" else "inconsistent")


@pytest.mark.parametrize("late", [False, True])
def test_conflicted_bound_signal_and_early_copy_cannot_persist(tmp_path, late):
    signal = {"close_confirmed": True, "signal_complete": True, "persistence_eligible": True}
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, None, response())
        if not late:
            session.raw_response(URL, None, response(b"B"))
        session.bind_signal(signal)
        copied = deepcopy(signal)
        assert signal["persistence_eligible"] is False
        if late:
            session.raw_response(URL, None, response(b"B"))
        for value in [signal, copied]:
            with pytest.raises(RuntimeError, match="采集|封存"):
                state.require_persistence_eligible(value, "IC", check_grid=False)
    assert signal["input_evidence_status"] == "inconsistent" and not signal["persistence_eligible"]
    assert any("不落账" in x for x in signal["data_notes"])
    for value in [signal, copied]:
        with pytest.raises(RuntimeError, match="采集|封存"):
            state.require_persistence_eligible(value, "IC", check_grid=False)


@pytest.mark.parametrize("initial_eligible", [True, False])
def test_only_successfully_sealed_originally_eligible_signal_is_promoted(tmp_path, initial_eligible):
    signal = {"signal_complete": True, "persistence_eligible": initial_eligible}
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.bind_signal(signal)
        early_copy = deepcopy(signal)
        assert signal["input_evidence_status"] == "recording" and not signal["persistence_eligible"]
    assert signal["input_evidence_status"] == "complete"
    assert signal["persistence_eligible"] is initial_eligible
    with pytest.raises(RuntimeError, match="采集|封存"):
        state.require_persistence_eligible(early_copy, "IC", check_grid=False)
    if initial_eligible:
        state.require_persistence_eligible(signal, "IC", check_grid=False)


def test_store_boundary_rejects_unbound_signal_inside_active_capture(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        with pytest.raises(RuntimeError, match="采集"):
            state.require_persistence_eligible({"persistence_eligible": True}, "IC", check_grid=False)


def test_repeated_consistent_observation_does_not_repair_conflicted_session(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as first:
        minimum(first)
        first.raw_response(URL, None, response())
        first.raw_response(URL, None, response(b"B"))
        first.raw_response(URL, None, response())
        first.call("probe", {}, lambda: True)
    assert first.context["complete"] is False
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as second:
        minimum(second)
        second.raw_response(URL, None, response(b"C"))
    assert first.manifest_path != second.manifest_path
    assert first.manifest_path.is_file() and first.context["complete"] is False
    assert adapter.has_original(tmp_path, "IC", DAY)
    assert evidence.load_session(tmp_path, DAY).manifest_sha256 == second.manifest_sha256


@pytest.mark.parametrize("same_parameters", [True, False])
def test_identical_response_or_different_parameters_is_not_a_conflict(tmp_path, same_parameters):
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, {"month": "2612"}, response())
        session.raw_response(URL, {"month": "2612" if same_parameters else "2703"}, response(b"A" if same_parameters else b"B"))
    assert session.context["complete"] is True
    assert evidence.load_session(tmp_path, DAY)


def test_publishing_failure_does_not_promote_signal(tmp_path, monkeypatch):
    signal = {"persistence_eligible": True}
    session = evidence.CaptureSession(tmp_path, DAY, CLOCK, META)
    minimum(session)
    session.bind_signal(signal)
    monkeypatch.setattr(evidence, "_publish", lambda *a, **k: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError):
        session.finish()
    assert not signal["persistence_eligible"] and signal["input_evidence_status"] == "recording"


def test_context_failure_keeps_bound_signal_ineligible(tmp_path):
    signal = {"persistence_eligible": True}
    with pytest.raises(ValueError, match="downstream"):
        with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
            minimum(session)
            session.bind_signal(signal)
            raise ValueError("downstream validation failed")
    assert signal["input_evidence_status"] == "incomplete"
    assert signal["persistence_eligible"] is False


def test_legacy_ambiguous_pack_claiming_complete_still_cannot_load(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, None, response())
        session.raw_response(URL, None, response(b"B"))
    envelope = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    envelope["body"]["complete"] = True
    envelope["body"]["failures"] = []
    envelope["sha256"] = hashlib.sha256(evidence._dumps(envelope["body"])).hexdigest()
    session.manifest_path.write_bytes(evidence._dumps(envelope))
    with pytest.raises(evidence.EvidenceUnavailable, match="ambiguous"):
        evidence.load_session(tmp_path, DAY)
    with pytest.raises(evidence.EvidenceUnavailable, match="incomplete"):
        adapter.has_original(tmp_path, "IC", DAY)


def test_sealed_attempt_statistics_count_conflicts_not_polling_receipts(tmp_path, monkeypatch):
    # These synthetic sessions test counting, not real market data. Give them
    # the original-day seal time and role inventory required for readiness.
    monkeypatch.setattr(evidence, "_now", lambda: CLOCK)
    for product, bodies in [("IC", [b"A", b"B"]), ("IC", [b"C"]), ("IM", [b"D"])]:
        with evidence.capture_session(tmp_path, DAY, CLOCK, {**META, "product": product}) as session:
            minimum(session)
            roles = {"market.live", "cffex.quotes", "rate.resolve", "fear.score"}
            if product == "IC":
                roles |= {"ic.months", "ic.etf", "ic.chain"}
            for role in sorted(roles):
                session.call(role, {}, lambda: True)
            for body in bodies:
                session.raw_response(URL, None, response(body))
    polling = tmp_path / "capture_receipts" / str(DAY)
    polling.mkdir(parents=True)
    (polling / "poll.json").write_text('{"status":"already_captured"}', encoding="utf-8")
    stats = adapter.capture_day_status(tmp_path, DAY)
    assert stats["status"] == "captured"
    ic = stats["products"]["IC"]
    assert ic["attempts"] == 2 and ic["conflict_attempts"] == 1 and ic["conflict_frequency"] == 0.5
    assert stats["products"]["IM"]["attempts"] == 1


def test_capture_failure_warning_identifies_missing_original_inputs(tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, None, response())
        session.raw_response(URL, None, response(b"B"))
    stats = adapter.capture_day_status(tmp_path, DAY)
    assert stats["status"] == "incomplete"
    assert "存证缺失" in stats["recovery_warning"] and "人工锚定" in stats["recovery_warning"]


def test_server_page_displays_incomplete_original_archive(monkeypatch, tmp_path):
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "0")
    monkeypatch.setenv("ICIM_EVIDENCE_DIR", str(tmp_path / "evidence"))
    with evidence.capture_session(tmp_path / "evidence", DAY, CLOCK, META) as session:
        minimum(session)
        session.raw_response(URL, None, response())
        session.raw_response(URL, None, response(b"B"))
    server = importlib.import_module("poe_ic_im_v1_4_server")
    owner = server.LedgerCoordinator(state.StateStore(tmp_path / "ledger"))
    monkeypatch.setattr(bot, "ICIMMainlinesBot", lambda: type("Empty", (), {"run": lambda self: None})())
    monkeypatch.setattr(owner, "_record_for_display", lambda clock: owner.store.load_latest())
    monkeypatch.setattr(bot, "install_runtime_anchors", lambda value: None)
    monkeypatch.setattr(bot, "_roll_forward_exchange_day", lambda day: DAY)
    text, _, _ = owner.execute_query("信号", CLOCK, persist_confirmed=False)
    assert "存证缺失" in text and "另行补证或人工锚定" in text
