"""Permanent regressions discovered by independent delivery reviewers."""
import json
from datetime import datetime

import pytest

import ic_im_fear_grid_v1 as feed
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as runner
from test_ic_im_fear_source_delivery_20261016 import DAY, setup_feed, signal_for


def test_selected_csv_scalar_cannot_disagree_with_source_snapshot(monkeypatch):
    setup_feed(monkeypatch, page_ok=False)
    evidence = feed.fetch_score(DAY, "close")
    evidence["fear_greed_index"] = 80.
    signal = signal_for(evidence)
    with pytest.raises(RuntimeError, match="来源快照"):
        state.validate_fear_grid_signal(signal, "IC")
    assert feed.publication_view(signal)["grid_target"] is None


def test_selected_page_intraday_scalar_is_bound_to_page_value(monkeypatch):
    setup_feed(monkeypatch, csv_ok=False, hour=14)
    get = feed._bounded_get
    monkeypatch.setattr(feed, "_bounded_get", lambda *args: get(*args).replace(b"15:03", b"14:03"))
    evidence = feed.fetch_score(DAY, "intraday")
    signal = {**signal_for(evidence), "close_confirmed": False}
    state.validate_fear_grid_signal(signal, "IC")
    signal["fear_greed_index"] = 80.
    with pytest.raises(RuntimeError, match="来源快照"):
        state.validate_fear_grid_signal(signal, "IC")


def test_today_observed_cannot_be_labeled_unpublished_to_force_prior(monkeypatch):
    setup_feed(monkeypatch, page_ok=False)
    evidence = feed.fetch_score(DAY, "close")
    evidence.update(fear_greed_index=39.32, fear_data_date="2026-10-15",
                    fear_data_status=feed.CLOSE_FALLBACK_STATUS, fear_fallback_applied=True,
                    fear_previous_exchange_date="2026-10-15", fear_fallback_reason="same_day_not_published",
                    fear_fallback_timing="same_day_post_close")
    with pytest.raises(RuntimeError, match="已有当日值"):
        state.validate_fear_grid_signal(signal_for(evidence), "IC")


def test_generation_crossing_midnight_requires_replay_even_without_pending(monkeypatch):
    setup_feed(monkeypatch)
    signal = signal_for(feed.fetch_score(DAY, "close"))
    signal.update(signal_generated_at="2026-10-17T00:00:01+08:00", signal_generation_timing="close_confirmed")
    monkeypatch.setattr(feed, "_validation_clock", lambda: datetime(2026, 10, 17, 1, tzinfo=feed.BEIJING))
    with pytest.raises(RuntimeError, match="跨日补录"):
        state.validate_fear_grid_signal(signal, "IC")
    assert feed.publication_view(signal)["grid_target"] is None
    signal["signal_generation_timing"] = "retrospective_replay"
    state.validate_fear_grid_signal(signal, "IC")


def test_preclose_page_allows_approved_prior_session_carry(monkeypatch):
    setup_feed(monkeypatch)
    get = feed._bounded_get
    monkeypatch.setattr(feed, "_bounded_get", lambda *args: get(*args).replace(b"15:03", b"14:03"))
    evidence = feed.fetch_score(DAY, "close")
    assert evidence["fear_data_date"] == "2026-10-15"
    assert evidence["fear_fallback_reason"] == "same_day_not_close_confirmed"
    state.validate_fear_grid_signal(signal_for(evidence), "IC")


def test_failure_result_keeps_jiu_reference_when_other_market_data_fail(monkeypatch, tmp_path):
    setup_feed(monkeypatch, page_ok=False)
    monkeypatch.setattr(feed, "fetch_jiu_reference", lambda _: {
        "jiu_fear_greed_index": 2.28, "jiu_data_date": "2026-10-15", "jiu_reference_only": True})
    runner.write_failure(tmp_path, datetime(2026, 10, 16, 18, 30, tzinfo=feed.BEIJING),
                         RuntimeError("injected futures feed failure"), mode="close", expected_market_date=DAY.isoformat())
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed" and "signals" not in result
    assert result["jiu_fear_greed_index"] == 2.28
    assert "未完成双侧验证" in result["fear_source_warning"]


def test_latest_csv_today_cannot_delete_today_observation(monkeypatch):
    setup_feed(monkeypatch, page_ok=False)
    evidence = feed.fetch_score(DAY, "close")
    del evidence["fear_csv_observations"][DAY.isoformat()]
    evidence.update(fear_greed_index=39.32, fear_data_date="2026-10-15",
                    fear_data_status=feed.CLOSE_FALLBACK_STATUS, fear_fallback_applied=True,
                    fear_previous_exchange_date="2026-10-15", fear_fallback_reason="same_day_not_published",
                    fear_fallback_timing="same_day_post_close")
    with pytest.raises(RuntimeError, match="观察快照删除"):
        state.validate_fear_grid_signal(signal_for(evidence), "IC")


def test_report_batch_shares_snapshot_without_cross_report_staleness(monkeypatch):
    setup_feed(monkeypatch)
    get = feed._bounded_get
    calls = []
    def changing_page(url, maximum):
        calls.append(url)
        return get(url, maximum) + (f"<!-- dynamic {len(calls)} -->".encode() if url == feed.PAGE_URL else b"")
    monkeypatch.setattr(feed, "_bounded_get", changing_page)
    with runner.strategy.collection_clock(datetime(2026, 10, 16, 18, 30, tzinfo=feed.BEIJING)):
        first = feed.fetch_score(DAY, "close")
        with feed.observation_batch():
            second = feed.fetch_score(DAY, "close")
        assert first == second and len(calls) == 2
        first["fear_csv_observations"][DAY.isoformat()] = 99.
        assert feed.fetch_score(DAY, "close")["fear_csv_observations"][DAY.isoformat()] == 20.
    third = feed.fetch_score(DAY, "close")
    assert len(calls) == 4 and third["fear_page_sha256"] != second["fear_page_sha256"]
    assert third["fear_greed_index"] == second["fear_greed_index"]
