"""Offline synthetic publication-gate cases; no real runtime or source packages."""
import contextvars
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

import pytest

import ic_im_signal_evidence_v1 as evidence


class SourcePinError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import requests
    def blocked(*args, **kwargs):
        raise AssertionError("publication tests must remain offline")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def capture(root, signal=None):
    clock = datetime.now(timezone.utc)
    with evidence.capture_session(root, clock.date(), clock, {"product": "IC"}) as session:
        session.call("synthetic", {"day": clock.date()}, lambda: {"value": 1})
        if signal is not None:
            session.bind_signal(signal)
    return session


def test_publication_guard_rejects_before_any_complete_manifest_is_visible(tmp_path, monkeypatch):
    error = SourcePinError("executed dependency was not pinned")
    observed = []
    publish = evidence._publish
    def checked_publish(root, path, data):
        if path.parent.parent.name == "sessions":
            assert json.loads(data)["body"]["complete"] is False
            observed.append("incomplete_only")
            publish(root, path, data)
            # Simulates a reader winning the race immediately after publication.
            with pytest.raises(evidence.EvidenceUnavailable):
                evidence._load_one(root, path, datetime.now(timezone.utc).date())
        else:
            publish(root, path, data)
    monkeypatch.setattr(evidence, "_publish", checked_publish)
    def reject():
        assert not list((tmp_path / "sessions").glob("**/*.json"))
        observed.append("guard_before_publish")
        raise error
    signal = {"persistence_eligible": True}
    with evidence.publication_guard(reject):
        session = capture(tmp_path, signal)
    assert observed == ["guard_before_publish", "incomplete_only"]
    assert session.publication_error is error
    assert session.context["complete"] is False
    assert session.context["failures"][0]["role"] == "publication_guard"
    assert session.context["failures"][0]["resolved"] is False
    assert signal["input_evidence_status"] == "incomplete"
    assert signal["persistence_eligible"] is False
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.has_session(tmp_path, session.day, {"product": "IC"})


def test_publication_guard_fresh_retry_scope_is_not_poisoned(tmp_path):
    def reject():
        raise SourcePinError("first attempt")
    with evidence.publication_guard(reject):
        failed = capture(tmp_path)
    signal = {"persistence_eligible": True}
    with evidence.publication_guard(lambda: None):
        retry = capture(tmp_path, signal)
    assert failed.context["complete"] is False
    assert retry.context["complete"] is True
    assert retry.publication_error is None
    assert retry.manifest_path != failed.manifest_path
    assert signal["input_evidence_status"] == "complete"
    assert signal["persistence_eligible"] is True
    assert evidence._load_one(tmp_path, retry.manifest_path, retry.day).context["complete"]
    assert capture(tmp_path).context["complete"] is True


def test_publication_guard_nested_scopes_restore_outer_callback(tmp_path):
    calls = []
    with evidence.publication_guard(lambda: calls.append("outer")):
        with evidence.publication_guard(lambda: calls.append("inner")):
            capture(tmp_path)
        capture(tmp_path)
    capture(tmp_path)
    assert calls == ["inner", "outer"]


def test_publication_guard_copy_context_worker_inherits_callback(tmp_path):
    error = SourcePinError("worker source drift")
    def reject():
        raise error
    with evidence.publication_guard(reject):
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            session = pool.submit(context.run, capture, tmp_path).result(timeout=10)
    assert session.publication_error is error
    assert session.context["complete"] is False


def test_publication_guard_retained_when_session_outlives_callback_scope(tmp_path):
    clock = datetime.now(timezone.utc)
    def reject():
        raise SourcePinError("creation scope must be retained")
    with evidence.publication_guard(reject):
        session = evidence.CaptureSession(tmp_path, clock.date(), clock, {})
        session.call("synthetic", {}, lambda: 1)
    session.finish()
    assert session.context["complete"] is False
    assert isinstance(session.publication_error, SourcePinError)


def test_publication_guard_does_not_mask_original_computation_exception(tmp_path):
    clock = datetime.now(timezone.utc)
    error = ValueError("original calculation failed")
    def reject():
        raise SourcePinError("drift despite failed calculation")
    with evidence.publication_guard(reject):
        with pytest.raises(ValueError) as raised:
            with evidence.capture_session(tmp_path, clock.date(), clock, {}) as session:
                session.call("synthetic", {}, lambda: 1)
                raise error
    assert raised.value is error
    assert session.context["complete"] is False
    assert {failure["role"] for failure in session.failures} == {"capture_context", "publication_guard"}


def test_publication_guard_invalid_callback_does_not_replace_active_scope(tmp_path):
    calls = []
    with evidence.publication_guard(lambda: calls.append("active")):
        with pytest.raises(TypeError):
            with evidence.publication_guard(None):
                pass
        capture(tmp_path)
    assert calls == ["active"]
