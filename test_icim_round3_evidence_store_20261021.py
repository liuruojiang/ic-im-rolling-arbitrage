"""Storage integrity/typing counterexamples; no strategy ledger or live network."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import ic_im_signal_evidence_v1 as archive

DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 15, 20, tzinfo=timezone(timedelta(hours=8)))


def capture(root: Path, *, value=None, role="chain", metadata=None, at=CLOCK):
    with archive.capture_session(root, DAY, at, metadata or {"product": "IC"}) as session:
        session.call(role, {"month": "2611"}, lambda: value if value is not None else {"price": 1.5})
    return session


def mutate_manifest(path: Path, update):
    envelope = json.loads(path.read_text(encoding="utf-8"))
    update(envelope["body"])
    envelope["sha256"] = hashlib.sha256(archive._dumps(envelope["body"])).hexdigest()
    path.write_bytes(archive._dumps(envelope))


def test_roundtrip_dataframe_series_attributes(tmp_path):
    index = pd.date_range("2026-10-20", periods=2, tz="Asia/Shanghai", name="trading_day")
    frame = pd.DataFrame({"price": [7.1, 7.2], "flag": [True, False], "count": [2, 3]}, index=index)
    frame["nullable"] = pd.Series([1, pd.NA], index=index, dtype="Int64")
    frame["contract"] = pd.Series(["510500P2611M07000", "510500P2611M07200"], index=index, dtype="string")
    frame.attrs.update(source_date=DAY, source_stamp=pd.Timestamp(CLOCK), raw=b"source bytes", count=np.int64(2))
    session = capture(tmp_path, value=(frame, {"date": "20261021", "time": "150000"}))
    reader = archive.load_session(tmp_path, DAY, required_roles=["chain"], metadata_filter={"product": "IC"})
    actual, stamp = reader.call("chain", {"month": "2611"})
    pd.testing.assert_frame_equal(actual, frame)
    assert actual.attrs == frame.attrs and type(actual.attrs["count"]) is np.int64
    assert stamp["time"] == "150000" and reader.manifest_path == session.manifest_path
    assert reader.decision_at == CLOCK and reader.metadata == {"product": "IC"}


@pytest.mark.parametrize("value", [
    None, True, False, 1, 1.0, -0.0, "1", b"\x00\xff", DAY, CLOCK, pd.Timestamp(CLOCK),
    np.int64(7), np.uint32(7), np.bool_(True), np.float32(1.5), pd.NA, pd.NaT,
    ("a", 1), [False, 2], {"t": "datetime", "v": "not a tag"},
])
def test_scalar_types_not_coerced(value):
    actual = archive.decode(archive._loads(archive._dumps(archive.encode(value))))
    if value is pd.NA or value is pd.NaT:
        assert actual is value
    else:
        assert type(actual) is type(value)
        assert actual == value


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_is_explicit_float_not_json_constant(value):
    encoded = archive._dumps(archive.encode(value))
    actual = archive.decode(archive._loads(encoded))
    assert type(actual) is float
    assert np.isnan(actual) if np.isnan(value) else actual == value
    assert b"NaN" not in encoded and b"Infinity" not in encoded


@pytest.mark.parametrize("value", [
    pd.Series([1.0, np.nan], name="last", index=pd.Index(["a", "b"], name="contract")),
    pd.Series(pd.Categorical(["a", None, "b"], categories=["b", "a"], ordered=True)),
    pd.Series(pd.date_range("2026-10-21", periods=2, tz="Asia/Shanghai")),
    pd.Series([], dtype="float64"),
    pd.DataFrame(index=pd.Index(["a", "b"], name="row")),
    pd.DataFrame([[1, 2]], columns=["same", "same"]),
    pd.DataFrame({"x": [1, 2]}, index=pd.MultiIndex.from_tuples([("a", 1), ("b", 2)], names=["one", "two"])),
])
def test_pandas_boundary_types(value):
    actual = archive.decode(archive.encode(value))
    if isinstance(value, pd.Series):
        pd.testing.assert_series_equal(actual, value)
    else:
        pd.testing.assert_frame_equal(actual, value)


def test_cache_does_not_repeat_io_or_allow_mutation(tmp_path):
    seen = []
    with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        original = session.call("chain", {"b": 2, "a": 1}, lambda: seen.append(1) or {"price": [1.5]})
        original["price"][0] = 100
        copied = session.call("chain", {"a": 1, "b": 2}, lambda: pytest.fail("must use original evidence"))
        assert copied == {"price": [1.5]}
    assert seen == [1]


def test_acquisition_uses_real_clock_separate_from_replay_clock(tmp_path):
    before = datetime.now(timezone.utc)
    session = capture(tmp_path)
    after = datetime.now(timezone.utc)
    body = session.context
    observed = datetime.fromisoformat(next(iter(body["entries"].values()))["fetched_at"])
    assert before <= observed <= after
    assert body["decision_at"] == CLOCK.isoformat()
    assert observed.isoformat() != CLOCK.isoformat()


def test_raw_bytes_source_clock_and_no_cookie(tmp_path):
    response = SimpleNamespace(content=b"exact\x00wire", status_code=200, encoding="gbk",
                               headers={"Date": "source clock", "Content-Type": "text/plain", "Set-Cookie": "secret"})
    with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.raw_response("https://example.test/quotes", {"month": "2611"}, response)
        session.call("chain", {}, lambda: [1.0])
    reader = archive.load_session(tmp_path, DAY)
    raw = reader.context["raw"][0]
    assert archive._verified_blob(reader.root, raw) == response.content
    assert raw["headers"] == {"Date": "source clock", "Content-Type": "text/plain"}
    assert archive.decode(raw["params"]) == {"month": "2611"}


def test_failure_manifest_not_silently_complete(tmp_path):
    with pytest.raises(ConnectionError, match="transport"):
        with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
            session.call("chain", {}, lambda: (_ for _ in ()).throw(ConnectionError("transport")))
    assert session.manifest_path.exists()
    assert session.context["complete"] is False
    assert session.context["failures"][0]["error_type"] == "ConnectionError"
    assert len(session.context["failures"]) == 1
    with pytest.raises(archive.EvidenceUnavailable, match="incomplete"):
        archive.load_session(tmp_path, DAY)
    assert archive.current_session.get() is None


def test_external_validator_failure_after_stored_blob_marks_context_partial(tmp_path):
    with pytest.raises(ValueError, match="wrong ETF date"):
        with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
            session.call("etf", {}, lambda: {"price": 7.1, "date": DAY - timedelta(days=1)})
            raise ValueError("wrong ETF date")
    assert len(session.entries) == 1
    assert session.context["complete"] is False
    assert session.context["failures"][0]["role"] == "capture_context"
    assert session.context["failures"][0]["error_type"] == "ValueError"
    with pytest.raises(archive.EvidenceUnavailable, match="incomplete"):
        archive.load_session(tmp_path, DAY, required_roles={"etf"})


def test_external_base_exception_is_preserved_and_records_context_failure(tmp_path):
    error = KeyboardInterrupt("cancelled capture")
    with pytest.raises(KeyboardInterrupt) as caught:
        with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
            session.call("chain", {}, lambda: 1)
            raise error
    assert caught.value is error
    assert session.context["complete"] is False
    assert session.context["failures"][0]["role"] == "capture_context"
    assert archive.current_session.get() is None


def test_resolved_retry_does_not_discard_complete_inputs(tmp_path):
    with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        with pytest.raises(TimeoutError):
            session.call("chain", {}, lambda: (_ for _ in ()).throw(TimeoutError()))
        session.call("chain", {}, lambda: 7)
    reader = archive.load_session(tmp_path, DAY)
    assert reader.call("chain", {}) == 7
    assert reader.context["failures"][0]["resolved"] is True


def test_partial_capture_cannot_satisfy_required_roles(tmp_path):
    capture(tmp_path)
    with pytest.raises(archive.EvidenceUnavailable, match="missing original evidence roles=etf"):
        archive.load_session(tmp_path, DAY, required_roles={"chain", "etf"})
    reader = archive.load_session(tmp_path, DAY)
    with pytest.raises(archive.EvidenceUnavailable, match="missing role=etf"):
        reader.call("etf", {})


def test_wrong_day_never_falls_back_to_current_archive(tmp_path):
    capture(tmp_path)
    with pytest.raises(archive.EvidenceUnavailable, match="2026-10-22"):
        archive.load_session(tmp_path, DAY + timedelta(days=1))
    with pytest.raises(archive.EvidenceUnavailable, match="belong"):
        archive.CaptureSession(tmp_path, DAY + timedelta(days=1), CLOCK, {})


def test_metadata_and_decision_cutoff(tmp_path):
    capture(tmp_path, metadata={"product": "IM"}, at=CLOCK + timedelta(minutes=1))
    earlier = capture(tmp_path, at=CLOCK, value=7)
    later = capture(tmp_path, at=CLOCK + timedelta(minutes=2), value=9)
    assert archive.load_session(tmp_path, DAY, metadata_filter={"product": "IC"}, decision_not_after=CLOCK).manifest_path == earlier.manifest_path
    assert archive.load_session(tmp_path, DAY, metadata_filter={"product": "IC"}).manifest_path == later.manifest_path
    with pytest.raises(archive.EvidenceUnavailable):
        archive.load_session(tmp_path, DAY, metadata_filter={"product": "absent"})


def test_tamper_manifest_rejected(tmp_path):
    session = capture(tmp_path)
    data = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    data["body"]["decision_at"] = (CLOCK + timedelta(minutes=5)).isoformat()
    session.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(archive.EvidenceUnavailable, match="manifest hash"):
        archive.load_session(tmp_path, DAY)


def test_tamper_blob_rejected_even_after_reader_selected(tmp_path):
    session = capture(tmp_path)
    reader = archive.load_session(tmp_path, DAY)
    entry = next(iter(session.entries.values()))
    blob = archive._blob_path(tmp_path.resolve(), entry)
    blob.write_bytes(b"tampered")
    with pytest.raises(archive.EvidenceUnavailable, match="hash or length"):
        reader.call("chain", {"month": "2611"})
    with pytest.raises(archive.EvidenceUnavailable):
        archive.load_session(tmp_path, DAY)


@pytest.mark.parametrize("field,value", [
    ("day", "2026-10-22"), ("complete", False), ("total_bytes", True),
    ("closed_at", "2026-01-01T00:00:00+00:00"), ("decision_at", "2026-10-21T15:20:00"),
])
def test_rehashed_but_invalid_manifest_rejected(tmp_path, field, value):
    session = capture(tmp_path)
    mutate_manifest(session.manifest_path, lambda body: body.__setitem__(field, value))
    with pytest.raises(archive.EvidenceUnavailable):
        archive.load_session(tmp_path, DAY)


def test_path_traversal_blob_reference_rejected(tmp_path):
    session = capture(tmp_path)
    mutate_manifest(session.manifest_path, lambda body: next(iter(body["entries"].values())).__setitem__("sha256", "../../escape"))
    with pytest.raises(archive.EvidenceUnavailable, match="blob reference"):
        archive.load_session(tmp_path, DAY)


def test_entry_role_key_mismatch_rejected(tmp_path):
    session = capture(tmp_path)
    mutate_manifest(session.manifest_path, lambda body: next(iter(body["entries"].values())).__setitem__("role", "different"))
    with pytest.raises(archive.EvidenceUnavailable, match="signature"):
        archive.load_session(tmp_path, DAY)


def test_item_and_session_limits_leave_explicit_incomplete_manifest(tmp_path):
    with pytest.raises(archive.EvidenceUnavailable, match="budget"):
        with archive.capture_session(tmp_path, DAY, CLOCK, {}, max_item_bytes=100, max_total_bytes=100) as session:
            session.call("large", {}, lambda: "x" * 200)
    assert session.context["complete"] is False
    assert list(tmp_path.glob("blobs/*")) == []


def test_total_budget_is_checked_before_writing_additional_blob(tmp_path):
    with pytest.raises(archive.EvidenceUnavailable, match="budget"):
        with archive.capture_session(tmp_path, DAY, CLOCK, {}, max_item_bytes=100, max_total_bytes=150) as session:
            session.call("one", {}, lambda: "a" * 80)
            session.call("two", {}, lambda: "b" * 80)
    assert len(session.entries) == 1 and session.context["complete"] is False
    assert len(list(tmp_path.glob("blobs/*.json"))) == 1


def test_missing_blob_is_explicit_evidence_failure(tmp_path):
    session = capture(tmp_path)
    entry = next(iter(session.entries.values()))
    archive._blob_path(tmp_path.resolve(), entry).unlink()
    with pytest.raises(archive.EvidenceUnavailable, match="missing"):
        archive.load_session(tmp_path, DAY)


def test_forged_acquisition_time_cannot_be_backdated_outside_capture(tmp_path):
    session = capture(tmp_path)
    mutate_manifest(session.manifest_path, lambda body: next(iter(body["entries"].values())).__setitem__("fetched_at", CLOCK.isoformat()))
    with pytest.raises(archive.EvidenceUnavailable, match="acquisition"):
        archive.load_session(tmp_path, DAY)


def test_incomplete_atomic_temporary_file_cannot_be_loaded(tmp_path):
    directory = tmp_path / "sessions" / DAY.isoformat()
    directory.mkdir(parents=True)
    (directory / ".pending-partial").write_bytes(b'{"body":')
    with pytest.raises(archive.EvidenceUnavailable, match="no complete session"):
        archive.load_session(tmp_path, DAY)


def test_concurrent_writers_are_append_only(tmp_path):
    def writer(index):
        return capture(tmp_path, value={"price": 1.5}).manifest_path
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(writer, range(16)))
    assert len(set(paths)) == 16
    assert len(list(tmp_path.glob("blobs/*.json"))) == 1
    assert len(list(tmp_path.glob("sessions/*/*.json"))) == 16
    reader = archive.load_session(tmp_path, DAY)
    assert reader.call("chain", {"month": "2611"}) == {"price": 1.5}
    assert list(tmp_path.rglob(".pending-*")) == []


def test_finish_is_idempotent_and_cannot_overwrite(tmp_path):
    session = capture(tmp_path)
    before = session.manifest_path.read_bytes()
    assert session.finish() == session.manifest_path
    assert session.manifest_path.read_bytes() == before
    with pytest.raises(archive.EvidenceUnavailable, match="closed"):
        session.call("chain", {}, lambda: 1)


def test_source_context_dispatch_and_cleanup(tmp_path):
    calls = []
    @archive.source("quote", lambda product: {"product": product})
    def fetch(product):
        calls.append(product)
        return {"price": 7.1, "date": DAY}
    assert fetch("IC")["price"] == 7.1
    with archive.capture_session(tmp_path, DAY, CLOCK, {}):
        fetch("IC")
    reader = archive.load_session(tmp_path, DAY)
    with archive.replay_session(reader):
        assert fetch("IC") == {"price": 7.1, "date": DAY}
        with pytest.raises(archive.EvidenceUnavailable, match="missing"):
            fetch("IM")
        with pytest.raises(archive.EvidenceUnavailable, match="nested"):
            with archive.capture_session(tmp_path, DAY, CLOCK, {}):
                pass
    assert calls == ["IC", "IC"]
    assert archive.current_reader.get() is None


@pytest.mark.parametrize("bad", ["../chain", "a/b", "", "a"*161])
def test_role_path_syntax_rejected(tmp_path, bad):
    with archive.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        with pytest.raises(archive.EvidenceUnavailable, match="role"):
            session.call(bad, {}, lambda: 1)


def test_duplicate_json_keys_and_executable_tags_rejected():
    with pytest.raises(archive.EvidenceUnavailable, match="duplicate"):
        archive._loads(b'{"x":1,"x":2}')
    with pytest.raises(archive.EvidenceUnavailable, match="unknown"):
        archive.decode({"t": "pickle", "v": "arbitrary"})

