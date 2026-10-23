"""Prospective collection failure isolation; all market inputs are synthetic."""
import asyncio
from copy import deepcopy
import importlib
import json
import runpy
from datetime import date
from types import SimpleNamespace

import pytest

import ic_im_signal_evidence_v1 as evidence
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot
import test_icim_round3_recovery_audit_20261021 as recovery
import test_icim_batch3_old_call_20261021 as legacy


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.delenv("ICIM_EVIDENCE_DIR", raising=False)


def bodies(root):
    return [json.loads(p.read_text(encoding="utf-8"))["body"]
            for p in (root / "sessions").rglob("*.json")]


def test_index_failure_still_preserves_perishable_original_chains(tmp_path, monkeypatch):
    fake, now = recovery.independent_capture_strategy(monkeypatch)
    def missing_index(product, clock):
        raise RuntimeError("dated index bar not yet available")
    fake.live_proxy = evidence.source("market.live", lambda product, clock: {"product": product})(missing_index)
    result = adapter.capture_daily(fake, tmp_path, now)
    assert result["status"] == "incomplete"
    ic = next(body for body in bodies(tmp_path) if evidence.decode(body["metadata"])["product"] == "IC")
    assert {"ic.chain", "ic.months", "ic.etf"} <= {e["role"] for e in ic["entries"].values()}
    assert ic["complete"] is False
    assert any(f["role"] == "market.live" and f["resolved"] is False for f in ic["failures"])
    with pytest.raises(evidence.EvidenceUnavailable):
        adapter.load_original(bot, tmp_path, "IC", now.date())


def test_one_chain_failure_preserves_other_months_and_other_product(tmp_path, monkeypatch):
    fake, now = recovery.independent_capture_strategy(monkeypatch)
    original = fake._fetch_510500_chain_for_day
    first = fake.fetch_sse_510500_expiries()[0][-4:]
    def partial(month, day):
        if month == first:
            raise RuntimeError("one original chain unavailable")
        return original(month, day)
    fake._fetch_510500_chain_for_day = partial
    result = adapter.capture_daily(fake, tmp_path, now)
    assert result["products"]["IC"]["status"] == "incomplete"
    assert result["products"]["IM"]["status"] == "captured"
    ic = next(body for body in bodies(tmp_path) if evidence.decode(body["metadata"])["product"] == "IC")
    assert any(e["role"] == "ic.chain" for e in ic["entries"].values())
    assert any(f["role"] == "collector" for f in ic["failures"])


def test_source_has_no_key_adapter_side_effect_without_evidence_context():
    owner = SimpleNamespace(fetch=lambda **kw: kw)
    def forbidden_key(*a, **kw):
        raise AssertionError("key must not be evaluated outside an evidence context")
    adapter._keyed(bot, owner, "fetch", "test.input", forbidden_key)
    assert owner.fetch(arbitrary_keyword=7) == {"arbitrary_keyword": 7}


def test_native_script_survives_removed_module_registration_and_keeps_shared_providers():
    fear_source, rate_source = bot.fear_grid.fetch_score, bot.chinabond.resolve
    script = runpy.run_path(bot.__file__, run_name="icim_native_evidence_probe")
    result = script["build_live_trade_signal"]("IC", recovery.clock(date(2027, 1, 4)), "close")
    assert result["diagnostic_only"] is True and result["diagnostic_status"] == "calendar_unverified"
    assert bot.fear_grid.fetch_score is fear_source and bot.chinabond.resolve is rate_source


def test_rate_keyword_interface_is_preserved(monkeypatch):
    monkeypatch.delenv(bot.chinabond.FILE_ENV, raising=False)
    result = bot._resolve_gov10y_evidence(market_day=date(2026, 10, 21),
        clock=recovery.clock(date(2026, 10, 21)), frozen_rate=.016, anchor_day=bot.DATA_CUTOFF)
    assert result["mode"] == "frozen_fallback" and result["yield_decimal"] == .016


def test_health_retains_verified_head_when_calendar_is_missing(tmp_path, monkeypatch):
    store, initial = recovery.temporary_store(tmp_path / "ledger")
    service = recovery.coordinator(monkeypatch, tmp_path, store, tmp_path / "evidence")
    result = service.health(recovery.clock(date(2027, 1, 4)))
    assert result["status"] == "degraded" and result["completed_day"] is None
    assert "2027" in result["refresh_error"]
    assert result["verified_day"] == initial["verified_day"]
    assert store.load_latest()["digest"] == initial["digest"]


def test_capture_retries_partial_then_refreshes_after_rate_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("ICIM_DISABLE_EVIDENCE_CAPTURE", "0")
    store, initial = recovery.temporary_store(tmp_path / "ledger")
    service = recovery.coordinator(monkeypatch, tmp_path, store, tmp_path / "evidence")
    calls = []
    def collect(strategy, root, clock):
        calls.append(clock)
        return {"day": str(clock.date()), "status": "incomplete" if len(calls) == 1 else "captured"}
    monkeypatch.setattr(adapter, "capture_daily", collect)
    early = recovery.clock(recovery.DAY, 15, 20)
    assert service.capture_current_inputs(early)["status"] == "incomplete"
    assert service.health(early)["evidence_capture"]["status"] == "incomplete"
    assert service.capture_current_inputs(early)["status"] == "captured"
    assert service.capture_current_inputs(early)["status"] == "captured"
    assert len(calls) == 2
    assert service.capture_current_inputs(recovery.clock(recovery.DAY, 18))["status"] == "captured"
    assert len(calls) == 3
    assert store.load_latest()["digest"] == initial["digest"]


def test_sole_refresh_loop_collects_today_even_when_old_catchup_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "boot"))
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "0")
    server = importlib.import_module("poe_ic_im_v1_4_server")
    calls = []
    now = recovery.clock(recovery.NEXT)
    fake = SimpleNamespace(last_refresh_error=None)
    def fail(clock):
        calls.append("catchup")
        raise RuntimeError("old gap unresolved")
    def collect(clock=None):
        # Capture now resolves its own start after executor queuing.
        assert clock is None
        calls.append("capture_today")
    async def cancelled_sleep(seconds):
        assert seconds == 300
        raise asyncio.CancelledError
    fake.catch_up_once, fake.capture_current_inputs = fail, collect
    monkeypatch.setattr(server, "coordinator", fake)
    monkeypatch.setattr(bot, "_now_beijing", lambda: now)
    monkeypatch.setattr(server.asyncio, "sleep", cancelled_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server._refresh_loop())
    assert calls == ["catchup", "capture_today"]
    assert "old gap unresolved" in fake.last_refresh_error


def test_default_evidence_root_stays_inside_the_mounted_state_directory(tmp_path, monkeypatch):
    store, _ = recovery.temporary_store(tmp_path / "mounted_state")
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "unused_boot"))
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "0")
    monkeypatch.delenv("ICIM_EVIDENCE_DIR", raising=False)
    server = importlib.import_module("poe_ic_im_v1_4_server")
    service = server.LedgerCoordinator(store)
    assert service.evidence_root == store.root / "input_evidence"
    assert service.evidence_root.is_relative_to(store.root)
    assert service.evidence_root != store.journal_dir


@pytest.mark.parametrize("bad", [(False, "Sina:dated_open"), (float("nan"), "Eastmoney:dated_open"), (.4, "Sina:close")])
def test_invalid_open_supplement_never_publishes_complete_manifest(tmp_path, monkeypatch, bad):
    recovery.archive(monkeypatch, tmp_path, "IC", recovery.DAY)
    reader, runtime = adapter.load_original(bot, tmp_path, "IC", recovery.DAY)
    recovery.fake_wall(monkeypatch, date(2026, 11, 24), 8)
    opening = adapter._dated_open_source(bot, lambda *a: bad)
    with evidence.replay_session(reader), bot.runtime_clock(runtime):
        with pytest.raises(evidence.EvidenceUnavailable):
            opening("10012099", recovery.DAY)
    supplements = [b for b in bodies(tmp_path) if evidence.decode(b["metadata"]).get("original_close") is False]
    assert supplements and all(b["complete"] is False for b in supplements)
    assert adapter.load_original(bot, tmp_path, "IC", recovery.DAY)[0].manifest_sha256 == reader.manifest_sha256


def test_early_calendar_failure_retains_already_computed_model_references(monkeypatch):
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    legacy.wire(monkeypatch, held=False)
    expected = bot.live_proxy("IM", legacy.CLOCK)["momentum_next_weight"]
    def calendar_failure(*a, **k):
        raise bot.CalendarCoverageError("synthetic future schedule calendar unavailable")
    monkeypatch.setattr(bot, "select_active_future", calendar_failure)
    result = bot.build_live_trade_signal("IM", legacy.CLOCK, "close")
    assert result["diagnostic_only"] is True
    references = result["diagnostic_model"]["unconfirmed_references"]
    assert references["confirmed"] is False and references["momentum_weight_reference"] == expected
    assert "momentum_next_weight" not in result and "core_target" not in result


def test_original_close_replay_discloses_archive_identity_and_actual_acquisition(tmp_path, monkeypatch):
    recovery.archive(monkeypatch, tmp_path, "IC", recovery.DAY)
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    bot.install_runtime_anchors(recovery.state.anchors_from_record(recovery.prepared_record()))
    monkeypatch.setattr(bot, "_OFFICIAL_EXCHANGE_CALENDAR_YEARS", set(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS) | {2027})
    monkeypatch.setattr(bot, "build_iv_warning", lambda *a, **k: {"status": "audit_monitor_omitted"})
    monkeypatch.setattr(bot.fear_grid, "fetch_jiu_reference", lambda *a, **k: {})
    with adapter.evidence_location(tmp_path), bot.historical_replay(recovery.DAY):
        signal = bot.build_live_trade_signal("IC", recovery.clock(recovery.DAY, 15, 20), "close")
    assert not signal.get("diagnostic_only"), signal
    note = signal["data_notes"][0]
    assert "原日存证重放" in note and "原日实际采集完成" in note
    assert signal["original_evidence"]["manifest_sha256"] in note
    assert "本次请求重新抓取" not in note
