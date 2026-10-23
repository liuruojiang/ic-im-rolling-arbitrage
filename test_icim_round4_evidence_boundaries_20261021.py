"""Fourth review regressions; all injected HTTP and anchors are synthetic."""
from copy import deepcopy
from datetime import date, datetime, timezone
import importlib
import json
from pathlib import Path

import pytest
import pandas as pd
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_quarter_roll_v1_3 as quarter_roll
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot


DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 16, tzinfo=bot.BEIJING)


class Response:
    status_code = 200
    encoding = "utf-8"
    def __init__(self, chunks, headers=None, advance=None):
        self.chunks, self.headers = chunks, headers or {}
        self.advance, self.closed = advance, False
        self.eager_reads = 0
        self._content = None
        self._content_consumed = False
    @property
    def content(self):
        if self._content is None:
            self.eager_reads += 1
            return b"".join(self.chunks)
        return self._content
    def iter_content(self, chunk_size=65536):
        if self._content_consumed:
            yield self._content
            return
        for chunk in self.chunks:
            if self.advance:
                self.advance()
            yield chunk
    def raise_for_status(self):
        return None
    def close(self):
        self.closed = True


@pytest.mark.parametrize("url,limit_name", [
    ("https://www.cffex.com.cn/sj/historysj/202610/zip/202610.zip", "MAX_CFFEX_DOWNLOAD_BYTES"),
    ("https://web.ifzq.gtimg.cn/page", "MAX_QUOTE_RESPONSE_BYTES"),
])
def test_stream_recording_obeys_size_before_content_access(tmp_path, monkeypatch, url, limit_name):
    monkeypatch.setattr(bot, limit_name, 4)
    response = Response([b"123", b"456"])
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with pytest.raises(RuntimeError, match="上限|大小"):
        with evidence.capture_session(tmp_path, DAY, CLOCK, {"purpose": "synthetic bounded stream"}) as session:
            bot._evidence_http_get(url, stream=True)
    assert response.eager_reads == 0
    assert response.closed
    assert session.context["complete"] is False


def test_stream_recording_preserves_shared_read_budget(tmp_path, monkeypatch):
    elapsed = [0.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: elapsed[0])
    response = Response([b"PK", b"rest"], advance=lambda: elapsed.__setitem__(0, elapsed[0] + 1.))
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with pytest.raises(RuntimeError, match="预算"):
        with evidence.capture_session(tmp_path, DAY, CLOCK, {}), bot._network_budget(.5):
            bot._evidence_http_get("https://www.cffex.com.cn/sj/historysj/202610/zip/202610.zip", stream=True)
    assert response.eager_reads == 0
    assert response.closed


def test_stream_complete_body_is_recorded_without_eager_content(tmp_path, monkeypatch):
    response = Response([b"PK", b"synthetic"], headers={"Content-Length": "11"})
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("probe", {}, lambda: True)
        returned = bot._evidence_http_get("https://www.cffex.com.cn/sj/historysj/202610/zip/202610.zip", stream=True)
        assert b"".join(returned.iter_content()) == b"PKsynthetic"
    assert response.eager_reads == 0
    reader = evidence.load_session(tmp_path, DAY)
    assert reader.manifest["raw"][0]["bytes"] == 11


def test_unrecorded_replay_http_never_calls_requests(tmp_path, monkeypatch):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("probe", {}, lambda: True)
    reader = evidence.load_session(tmp_path, DAY)
    calls = []
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: calls.append(a) or Response([b"current"]))
    with evidence.replay_session(reader):
        with pytest.raises(evidence.EvidenceUnavailable):
            bot._evidence_http_get("https://unrecorded.invalid/quote", params={"day": str(DAY)})
    assert calls == []


def test_recorded_http_replays_exact_bytes_without_network(tmp_path, monkeypatch):
    response = Response([b"original bytes"])
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("probe", {}, lambda: True)
        bot._evidence_http_get("https://recorded.invalid/quote", params={"symbol": "one"})
    reader = evidence.load_session(tmp_path, DAY)
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("unrecorded network"))
    with evidence.replay_session(reader):
        returned = bot._evidence_http_get("https://recorded.invalid/quote", params={"symbol": "one"})
    assert returned.content == b"original bytes"
    assert returned.status_code == 200


@pytest.mark.parametrize("complete", [True, False])
def test_original_presence_is_per_product_even_for_incomplete_capture(tmp_path, complete):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {"product": "IC", "original_close": True}) as session:
        session.call("probe", {}, lambda: True)
        if not complete:
            session.failures.append({"role": "synthetic", "resolved": False})
    if complete:
        assert adapter.has_original(tmp_path, "IC", DAY)
    else:
        with pytest.raises(evidence.EvidenceUnavailable, match="incomplete"):
            adapter.has_original(tmp_path, "IC", DAY)
    assert not adapter.has_original(tmp_path, "IM", DAY)


def test_other_product_archive_does_not_block_native_dated_im_replay(tmp_path, monkeypatch):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {"product": "IC", "original_close": True}) as session:
        session.call("probe", {}, lambda: True)
    monkeypatch.setattr(bot, "_build_live_trade_signal", lambda *a, **k: {"marker": "native_dated_provider", "v14_action": "HOLD"})
    with adapter.evidence_location(tmp_path), bot.historical_replay(DAY):
        result = bot.build_live_trade_signal("IM", CLOCK, "close")
    assert result.get("marker") == "native_dated_provider"


def coordinator(tmp_path, monkeypatch):
    from test_icim_b3adv_state_20261021 import prepare
    store, current, signals = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "import_only"))
    server = importlib.import_module("poe_ic_im_v1_4_server")
    return server.LedgerCoordinator(store), signals


def test_unknown_year_intraday_header_keeps_two_verified_snapshots(tmp_path, monkeypatch):
    owner, _ = coordinator(tmp_path, monkeypatch)
    before = owner.store.load_latest()["digest"]
    text, _, observed = owner.execute_query("实时信号", datetime(2027, 1, 4, 10, tzinfo=bot.BEIJING), persist_confirmed=False)
    assert "查询失败" not in text
    assert text.count("最后逐腿核验快照") == 2
    assert owner.store.load_latest()["digest"] == before


def test_reminder_exception_preserves_both_calculated_products(tmp_path, monkeypatch):
    owner, signals = coordinator(tmp_path, monkeypatch)
    # State-validation seeds omit display-only fields. Reuse the retained full
    # report layout, with the current synthetic state fields taking precedence.
    template = json.loads((Path(__file__).resolve().parent / "tests/fixtures/icim_adversarial/legacy_close_record_20261013.json").read_text(encoding="utf-8"))["signals"]
    signals = {p: {**template[p], **signal} for p, signal in signals.items()}
    monkeypatch.setattr(bot, "build_live_signal_with_transport_retry", lambda product, *a: deepcopy(signals[product]))
    monkeypatch.setattr(bot.option_maintenance_notice, "reminder", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic reminder failed")))
    text, _, observed = owner.execute_query("信号", CLOCK, persist_confirmed=False)
    assert "查询失败" not in text
    assert text.count("提醒无法核验") == 2
    assert set(observed) == {"IC", "IM"}


def test_header_calendar_callback_failure_is_isolated(tmp_path, monkeypatch):
    owner, _ = coordinator(tmp_path, monkeypatch)
    monkeypatch.setattr(bot.option_maintenance_notice, "_valid_maintenance_day", lambda *a: (_ for _ in ()).throw(bot.CalendarCoverageError("synthetic header gap")))
    text, _, _ = owner.execute_query("实时信号", datetime(2027, 1, 4, 10, tzinfo=bot.BEIJING), persist_confirmed=False)
    assert "查询失败" not in text
    assert "月度维护日无法核验" in text
    assert text.count("最后逐腿核验快照") == 2


@pytest.mark.parametrize("status", [None, False, "200", 600])
def test_batch_projection_rejects_invalid_recorded_http_status(tmp_path, status):
    response = Response([b'var hq_str_P_OP_mo2612P7200="original";'])
    response.status_code = status
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("probe", {}, lambda: True)
        session.raw_response("https://hq.sinajs.cn/etag.php",
            {"list": "P_OP_mo2612P7200,P_OP_mo2612P7300"}, response)
    # HTTP metadata is now rejected while sealing/loading, before projection.
    assert session.context["complete"] is False
    assert any(failure["role"] == "raw_http_metadata" for failure in session.failures)
    with pytest.raises(evidence.EvidenceUnavailable, match="status invalid|incomplete"):
        reader = evidence.load_session(tmp_path, DAY)
        reader.http_response("https://hq.sinajs.cn/etag.php", {"list": "P_OP_mo2612P7200"})


def test_original_scope_corruption_does_not_enable_current_data_fallback(tmp_path, monkeypatch):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {"product": "IC", "original_close": True}) as session:
        session.call("probe", {}, lambda: True)
    envelope = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    envelope["body"]["metadata"] = evidence.encode({"product": "IM", "original_close": True})
    session.manifest_path.write_text(json.dumps(envelope), encoding="utf-8")
    monkeypatch.setattr(bot, "_build_live_trade_signal", lambda *a, **k: pytest.fail("corrupt scope must not bypass original archive"))
    with adapter.evidence_location(tmp_path), bot.historical_replay(DAY):
        result = bot.build_live_trade_signal("IM", CLOCK, "close")
    assert result["diagnostic_status"] == "original_evidence_unavailable"
    assert result["persistence_eligible"] is False


def test_original_scope_enumeration_failure_is_classified(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise OSError("synthetic archive permission failure")
    monkeypatch.setattr(Path, "glob", fail)
    with pytest.raises(evidence.EvidenceUnavailable, match="cannot enumerate"):
        adapter.has_original(tmp_path, "IM", DAY)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_future_maintenance_reference_is_only_pure_comparison_exemption(product):
    frame = pd.DataFrame({"instrument": [product + "2701", product + "2703"], "lastprice": [8000., 8001.]})
    context = {"expiries": [], "failures": []}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        reference = bot.select_active_future(product, frame, date(2026, 12, 21), purpose="maintenance_reference")
        assert reference["instrument"] == product + "2701" and context["expiries"] == []
        bot.select_active_future(product, frame, date(2026, 12, 21))
        assert context["expiries"] and context["expiries"][0]["date"].startswith("2027-")
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
    with pytest.raises(bot.CalendarCoverageError):
        bot.select_active_future(product, frame, date(2026, 12, 21))


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_unknown_year_formal_core_target_remains_strict(product):
    with pytest.raises(bot.CalendarCoverageError):
        bot.require_signal_calendar(product, {"market_date": date(2026, 12, 21),
            "next_trade_date": date(2026, 12, 22), "core_target": product + "2703"})


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_unknown_year_quarter_execution_is_not_proven_by_expiry_lower_bound(product):
    def expiry(contract):
        return bot._third_friday(2000 + int(contract[2:4]), int(contract[-2:]))
    def covered(day):
        bot._require_official_exchange_calendar(day, "quarter execution")
        return bot._is_exchange_trading_day(day)
    with pytest.raises(bot.CalendarCoverageError):
        quarter_roll.roll_state(product, product + "2703", [product + "2703", product + "2706"],
            date(2026, 12, 21), True, expiry, covered)
