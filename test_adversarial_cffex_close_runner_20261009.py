"""Offline adversarial delivery tests; all transport faults/data are synthetic.

The close runner, coordinator, historical routing, retry wrapper, and month
archive downloader are real.  No test reads or writes a production ledger.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import os

import pandas as pd
import pytest
import requests

assert os.environ.get("ICIM_REQUIRE_MIGRATION") == "0"
assert os.environ.get("ICIM_STATE_DIR")
assert "runtime" not in os.environ["ICIM_STATE_DIR"].lower()

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as runner


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    observer, poe = bot._SIGNAL_OBSERVER, bot.poe
    cache = dict(bot._CFFEX_MONTH_CACHE)
    monkeypatch.setattr(
        bot.fear_grid, "fetch_score",
        lambda *_args, **_kw: (_ for _ in ()).throw(
            RuntimeError("synthetic offline fear source unavailable")
        ),
    )
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *_args: None)
    yield
    # At first import the embedded pre-v1.4 anchor intentionally lacks the
    # route extension; restore exact globals without revalidating that fixture.
    bot.LIVE_CONTINUATION_ANCHOR.clear()
    bot.LIVE_CONTINUATION_ANCHOR.update(anchors)
    bot.install_signal_observer(observer)
    bot.poe = poe
    bot._CFFEX_MONTH_CACHE.clear()
    bot._CFFEX_MONTH_CACHE.update(cache)


def synthetic_sep30_store(root):
    """Explicit synthetic freeze of a valid test genesis, never production."""
    record = state.bootstrap_record()
    record["verified_day"] = "2026-09-30"
    for product in state.PRODUCTS:
        record["products"][product]["last_verified_day"] = "2026-09-30"
    record["source"] = "synthetic_cffex_offline_close_runner_audit"
    record["digest"] = state._digest(record)
    state._validate_record(record)
    store = state.StateStore(root)
    store._atomic_write(store.journal_dir / "000000-2026-09-30.json", record)
    store._atomic_write(store.latest_path, record)
    return store


def payload_hashes(store):
    return {
        path.relative_to(store.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [store.latest_path, *sorted(store.journal_dir.glob("*.json"))]
    }


def test_formal_close_catchup_retries_each_failed_product_once_and_writes_no_partial_day(
    monkeypatch, tmp_path
):
    store = synthetic_sep30_store(tmp_path / "state")
    before = payload_hashes(store)
    calls, gets = [], []

    def get(url, **kwargs):
        gets.append((url, kwargs["timeout"]))
        raise requests.ReadTimeout("synthetic CFFEX month archive timeout")

    def build(product, mode):
        calls.append((product, mode, bot._now_beijing().date(), bot._HISTORICAL_REPLAY_DAY.get()))
        return bot.fetch_cffex_quotes(product)

    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(RuntimeError, match="自动补账未同时得到IC/IM完整信号"):
        runner.build_artifacts(
            state_dir=store.root, out_dir=tmp_path / "report",
            clock=datetime(2026, 10, 9, 16, 19, tzinfo=bot.BEIJING),
            max_sessions=20, mode="close", expected_market_date="2026-10-09",
        )
    assert [x[0] for x in calls] == ["IC", "IC", "IM", "IM"]
    assert all(x[1:] == ("close", date(2026, 10, 8), date(2026, 10, 8)) for x in calls)
    assert len(gets) == 4
    assert all("/202610/zip/202610.zip" in x[0] and 0 < x[1] <= 5 for x in gets)
    assert payload_hashes(store) == before
    assert store.load_latest()["verified_day"] == "2026-09-30"
    assert not (tmp_path / "report" / "ic_im_v1_4_close_signal.md").exists()


def test_formal_close_partial_success_does_not_repeat_good_product_or_append(
    monkeypatch, tmp_path
):
    store = synthetic_sep30_store(tmp_path / "state")
    before = payload_hashes(store)
    calls = []

    def build(product, mode):
        calls.append(product)
        if product == "IC":
            raise requests.ConnectionError("synthetic failed IC transport")
        # This intentionally incomplete synthetic successful transport payload
        # is observed before report rendering; it cannot be a formal signal.
        return {"product": "IM", "close_confirmed": True, "market_date": date(2026, 10, 8)}

    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(RuntimeError, match="自动补账未同时得到IC/IM完整信号"):
        runner.build_artifacts(
            state_dir=store.root, out_dir=tmp_path / "report",
            clock=datetime(2026, 10, 9, 16, 19, tzinfo=bot.BEIJING),
            max_sessions=20, mode="close", expected_market_date="2026-10-09",
        )
    assert calls == ["IC", "IC", "IM"]
    assert payload_hashes(store) == before


def test_formal_close_invalid_data_not_retried_and_no_partial_chain(monkeypatch, tmp_path):
    store = synthetic_sep30_store(tmp_path / "state")
    before = payload_hashes(store)
    calls = []

    def build(product, mode):
        calls.append(product)
        raise ValueError("synthetic wrong-day data")

    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(RuntimeError, match="自动补账未同时得到IC/IM完整信号"):
        runner.build_artifacts(
            state_dir=store.root, out_dir=tmp_path / "report",
            clock=datetime(2026, 10, 9, 16, 19, tzinfo=bot.BEIJING),
            max_sessions=20, mode="close", expected_market_date="2026-10-09",
        )
    assert calls == ["IC", "IM"]
    assert payload_hashes(store) == before


def test_cffex_stream_cannot_return_after_total_network_budget(monkeypatch):
    """A fast deterministic clock-jump counterexample to trickle HTTP data."""
    ticks = [100.0]
    month = pd.Timestamp(bot.datetime.now(bot.BEIJING).date().replace(day=1))

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            ticks[0] += 2.0  # each read could be within socket timeout, total is not
            yield b"PK\x03\x04synthetic-stream"

        def close(self):
            return None

    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(bot.requests, "get", lambda *_a, **_kw: Response())
    with pytest.raises(RuntimeError, match="预算"):
        with bot._network_budget(1.0):
            bot._cffex_month_archive(month)


def test_cffex_current_wall_month_never_uses_existing_cached_archive(monkeypatch):
    month = pd.Timestamp(bot.datetime.now(bot.BEIJING).date().replace(day=1))
    key = month.strftime("%Y%m")
    bot._CFFEX_MONTH_CACHE[key] = b"PK\x03\x04stale-current-month"
    calls = []

    def get(*args, **kwargs):
        calls.append(args)
        raise requests.ReadTimeout("synthetic fresh download failed")

    monkeypatch.setattr(bot.requests, "get", get)
    with pytest.raises(requests.ReadTimeout):
        bot._cffex_month_archive(month)
    assert len(calls) == 1
    assert bot._CFFEX_MONTH_CACHE[key] == b"PK\x03\x04stale-current-month"


def test_exhausted_total_budget_prevents_second_transport_attempt(monkeypatch):
    ticks = [100.0]
    calls = []

    def build(product, mode):
        calls.append(product)
        ticks[0] += 2.0
        raise requests.ReadTimeout("synthetic timeout beyond remaining budget")

    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(RuntimeError, match="预算"):
        bot.build_live_signal_with_transport_retry("IC", "close", 1.0)
    assert calls == ["IC"]


@pytest.mark.parametrize("old_diagnostic_exists", [False, True])
def test_formal_main_close_failure_preserves_current_query_and_replay_day_only(
    monkeypatch, tmp_path, old_diagnostic_exists
):
    """Use the actual CLI main, catch-up, handler, retry and failure writer."""
    store = synthetic_sep30_store(tmp_path / "state")
    before = payload_hashes(store)
    out = tmp_path / "report"
    out.mkdir()
    old_marker = "STALE_DIAGNOSTIC_FROM_PREVIOUS_RUN_20260930"
    if old_diagnostic_exists:
        (out / "diagnostic_report.md").write_text(old_marker, encoding="utf-8")
    queries, calls = [], []
    original_execute = runner.LedgerCoordinator.execute_query

    def capture_execute(self, query, now=None, **kwargs):
        result = original_execute(self, query, now, **kwargs)
        queries.append((kwargs.get("replay_day"), result[0]))
        return result

    def get(*args, **kwargs):
        raise requests.ReadTimeout("synthetic current-run CFFEX timeout")

    def build(product, mode):
        calls.append((product, bot._HISTORICAL_REPLAY_DAY.get()))
        return bot.fetch_cffex_quotes(product)

    monkeypatch.setattr(runner.LedgerCoordinator, "execute_query", capture_execute)
    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    monkeypatch.setattr(runner.sys, "argv", [
        "run_ic_im_v1_4_github_digest.py", "--state-dir", str(store.root),
        "--out-dir", str(out), "--now", "2026-10-09T16:19:00+08:00",
        "--mode", "close", "--expected-market-date", "2026-10-09",
    ])

    assert runner.main() == 1
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["market_date"] == "2026-10-09"
    assert (out / "failure.txt").is_file()
    assert len(queries) == 1 and queries[0][0] == date(2026, 10, 8)
    assert calls == [("IC", date(2026, 10, 8))] * 2 + [("IM", date(2026, 10, 8))] * 2
    diagnostic_path = out / "diagnostic_report.md"
    assert diagnostic_path.is_file(), "official close failure must save this run's complete diagnostic"
    diagnostic = diagnostic_path.read_text(encoding="utf-8")
    assert queries[0][1] in diagnostic, "must preserve execute_query's exact IC/IM diagnostic text"
    assert "2026-10-08" in diagnostic
    assert "synthetic current-run CFFEX timeout" in diagnostic
    assert old_marker not in diagnostic
    assert payload_hashes(store) == before


def test_formal_main_prequery_failure_replaces_old_diagnostic_with_current_summary(
    monkeypatch, tmp_path
):
    out = tmp_path / "report"
    out.mkdir()
    old_marker = "STALE_SUCCESSFUL_QUERY_FROM_ANOTHER_DAY"
    (out / "diagnostic_report.md").write_text(old_marker, encoding="utf-8")

    def fail_before_query(**kwargs):
        raise RuntimeError("synthetic current pre-query failure")

    monkeypatch.setattr(runner, "build_artifacts", fail_before_query)
    monkeypatch.setattr(runner.sys, "argv", [
        "run_ic_im_v1_4_github_digest.py", "--state-dir", str(tmp_path / "state"),
        "--out-dir", str(out), "--now", "2026-10-09T16:19:00+08:00",
        "--mode", "close", "--expected-market-date", "2026-10-09",
    ])
    assert runner.main() == 1
    diagnostic = (out / "diagnostic_report.md").read_text(encoding="utf-8")
    assert old_marker not in diagnostic
    assert "synthetic current pre-query failure" in diagnostic
    assert "2026-10-09" in diagnostic


def test_reused_coordinator_clears_previous_query_before_new_collection_failure(
    monkeypatch, tmp_path
):
    store = synthetic_sep30_store(tmp_path / "state")
    coordinator = runner.LedgerCoordinator(store)
    clock = datetime(2026, 10, 9, 16, 19, tzinfo=bot.BEIJING)

    def first_query(*args, **kwargs):
        return "synthetic earlier IC/IM query diagnostic", [], {}

    monkeypatch.setattr(coordinator, "execute_query", first_query)
    assert coordinator.catch_up_once(clock) is False
    assert "synthetic earlier" in getattr(coordinator, "last_refresh_diagnostic", "")

    def collection_failure(*args, **kwargs):
        raise RuntimeError("synthetic new query failed before capture")

    monkeypatch.setattr(coordinator, "execute_query", collection_failure)
    assert coordinator.catch_up_once(clock) is False
    assert not getattr(coordinator, "last_refresh_diagnostic", "")
    assert "synthetic new query failed before capture" in coordinator.last_refresh_error
