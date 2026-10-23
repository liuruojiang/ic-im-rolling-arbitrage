"""Independent integration probes; all state is disposable and all quotes synthetic."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import pytest
import requests

import ic_im_put_execution_v1 as execution
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
from poe_ic_im_v1_4_server import LedgerCoordinator
import run_ic_im_v1_4_github_digest as digest
import test_ic_im_external_review_20261021 as fixtures
import test_ic_im_ordinary_put_route_fix_20261013 as ledger_fixture

ROOT = Path(__file__).resolve().parent
OLD = ROOT / "quant_research_runs/20261021_external_review_v1"
DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.json")}


def recent_store(root):
    # Synthetic current-date checkpoint, validated and persisted through the
    # real StateStore. It is not a reconstruction of historical holdings.
    record = ledger_fixture._record()
    record["verified_day"] = "2026-10-20"
    for anchor in record["products"].values():
        anchor["last_verified_day"] = "2026-10-20"
        anchor.update(verified_grid_entry_source="none", verified_next_grid_entry_source="none")
    record["products"]["IC"]["v14_ordinary_put_pending"] = state._jsonable(
        fixtures.ordinary_anchor("IC")["v14_ordinary_put_pending"])
    record["digest"] = state._digest(record)
    store = state.StateStore(root)
    store._atomic_write(store.journal_dir / "000000-2026-10-20.json", record)
    store._atomic_write(store.latest_path, record)
    return store, store.load_latest()


def test_frozen_source_receipt_matches_current_bytes():
    receipt = json.loads((OLD / "final_verification.json").read_text(encoding="utf-8"))
    for relative, expected in receipt["source_sha256"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected, relative


def test_new_spec_sha_files_match():
    for stem in ["ic_im_external_review_repairs_20261021_v1_spec.md",
                 "ic_im_external_review_display_cache_20261021_v1_spec.md"]:
        path = ROOT / "docs" / stem
        expected = path.with_suffix(path.suffix + ".sha256").read_text().split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_actual_forward_boundary_uses_signal_day_not_wall_clock(monkeypatch, product):
    anchor = fixtures.ordinary_anchor(product)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    def unavailable(*args, **kwargs):
        raise requests.Timeout("synthetic independent outage")
    monkeypatch.setattr(bot, "fetch_option_dated_open", unavailable)
    monkeypatch.setattr(bot, "fetch_cffex_quotes", unavailable)
    event = bot._v14_ordinary_open_evidence(product, DAY, CLOCK, {})
    assert event["status"] == execution.RETRY_OPEN_STATUS
    execution.require_due_openings(product, anchor,
        {"v14_ordinary_put_open_status": "closed_missing_open_price"}, date(2026, 10, 20), False)
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        execution.require_due_openings(product, anchor,
            {"v14_ordinary_put_open_status": "closed_missing_open_price"}, DAY, False)


@pytest.mark.parametrize("status", [None, "closed_missing_open_price", execution.RETRY_OPEN_STATUS])
def test_actual_date_persistence_rejects_due_plan_without_lowering_cutoff(tmp_path, status):
    store, record = recent_store(tmp_path / "state")
    before = snapshot(store.root)
    signals = {product: {"market_date": DAY, "v14_opening_events": []}
               for product in state.PRODUCTS}
    signals["IC"]["v14_ordinary_put_open_status"] = status
    # The due-plan gate precedes unrelated quote/fear/delivery validation.
    # Thus the exact error, not merely rejection, is the asserted boundary.
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        store.append_confirmed_signals(record, signals)
    assert snapshot(store.root) == before


def test_real_coordinator_retry_failure_has_no_commit_or_anchor_leak(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    store, record = recent_store(tmp_path / "state")
    before = snapshot(store.root)
    coordinator = LedgerCoordinator(store)
    called = []
    def collect(product, clock, mode):
        called.append(product)
        if product == "IC":
            opening = bot._v14_ordinary_open_evidence(product, DAY, clock, {})
            execution.require_resolved_opening_evidence({"v14_ordinary_put_open_status": opening["status"]})
        raise RuntimeError("synthetic unavailable other product")
    def unavailable(*args, **kwargs):
        raise requests.Timeout("independent opening outage")
    monkeypatch.setattr(bot, "_build_live_trade_signal", collect)
    monkeypatch.setattr(bot, "fetch_option_dated_open", unavailable)
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *args: None)
    monkeypatch.setattr(bot.fear_grid, "fetch_score", lambda *args: pytest.fail("fallback performed network IO"))
    monkeypatch.setattr(bot.fear_grid, "fetch_jiu_reference", lambda *args: pytest.fail("fallback performed network IO"))
    text, _, observed = coordinator.execute_query("信号", CLOCK)
    assert called == ["IC", "IM"]
    assert not observed
    assert "unresolved opening evidence" in text
    assert snapshot(store.root) == before
    assert bot.LIVE_CONTINUATION_ANCHOR["IC"] == state.anchors_from_record(record)["IC"]


def test_real_runner_catchup_failure_cannot_create_success_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    store, _ = recent_store(tmp_path / "state")
    before = snapshot(store.root)
    def collect(product, clock, mode):
        if product == "IC":
            execution.require_resolved_opening_evidence(
                {"v14_ordinary_put_open_status": execution.RETRY_OPEN_STATUS})
        raise RuntimeError("synthetic unavailable other product")
    monkeypatch.setattr(bot, "_build_live_trade_signal", collect)
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *args: None)
    with pytest.raises(digest._ArtifactBuildError) as error:
        digest.build_artifacts(state_dir=store.root, out_dir=tmp_path / "out",
                               clock=CLOCK, max_sessions=1, expected_market_date=DAY.isoformat())
    assert "unresolved opening evidence" in str(error.value.diagnostic_report)
    assert snapshot(store.root) == before
    assert not (tmp_path / "out/result.json").exists()
