"""Adversarial time and provenance checks for the forward fix10 Fear feed."""

from datetime import date, datetime

import pytest

import ic_im_fear_grid_v1 as fear_grid
import poe_ic_im_v1_4_state as state


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 8, 15, 30, tzinfo=tz)


def _feed(monkeypatch, latest="2026-10-08", updated="2026-10-08 15:01"):
    csv_data = f"date,fear_greed_index\n{latest},40.00\n".encode()
    page = (
        f'<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">{latest}</p>'
        '<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value fg-fear">40.00</p>'
        f'<p class="fg-update">数据日 {latest} · 更新 {updated}</p>'
    ).encode()
    monkeypatch.setattr(fear_grid, "datetime", _Clock)
    monkeypatch.setattr(
        fear_grid, "_bounded_get",
        lambda url, maximum: csv_data if url == fear_grid.CSV_URL else page,
    )


def test_same_day_requires_strictly_post_close_page(monkeypatch):
    _feed(monkeypatch, updated="2026-10-08 15:00")
    with pytest.raises(RuntimeError, match="收盘后更新"):
        fear_grid.fetch_score(date(2026, 10, 8), "close")
    _feed(monkeypatch, updated="2026-10-08 15:01")
    assert fear_grid.fetch_score(date(2026, 10, 8), "close")["fear_data_status"] == "same_day_post_close"


def test_future_source_cannot_be_disguised_as_retrospective(monkeypatch):
    _feed(monkeypatch, latest="2026-10-09", updated="2026-10-09 15:01")
    with pytest.raises(RuntimeError, match="未来日期"):
        fear_grid.fetch_score(date(2026, 10, 8), "close")


def _signal(page_updated: str, retrieved: str = "2026-10-08T15:30:00+08:00"):
    return {
        "market_date": "2026-10-08", "close_confirmed": True,
        "grid_policy_revision": "ic_im_or_fear25_paired_exit50_20261008_v1",
        "fear_data_status": "same_day_post_close", "fear_greed_index": 40.0,
        "fear_data_date": "2026-10-08", "fear_retrieved_at": retrieved,
        "fear_page_updated_at": page_updated, "fear_csv_sha256": "a" * 64,
        "fear_source_url": fear_grid.CSV_URL, "grid_current": 0.0,
        "grid_target": 0.0, "grid_action": "HOLD", "score": 0.8,
        "grid_entry_source_current": "none", "grid_entry_source_target": "none",
        "grid_transition_reason": "hold_flat",
    }


def test_ledger_independently_rejects_false_close_provenance():
    state.validate_fear_grid_signal(_signal("2026-10-08T15:01:00+08:00"), "IC")
    for page_updated in (
        "2026-10-07T10:00:00+08:00",
        "2026-10-08T15:00:00+08:00",
        "2026-10-09T15:01:00+08:00",
    ):
        with pytest.raises(RuntimeError):
            state.validate_fear_grid_signal(_signal(page_updated), "IC")
