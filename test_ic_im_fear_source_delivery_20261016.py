"""Delivery continuity, evidence boundaries and isolation of the 9圈 reference."""
import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest

import ic_im_fear_grid_v1 as feed
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as runner


DAY = date(2026, 10, 16)


def setup_feed(monkeypatch, *, csv_ok=True, page_ok=True, page_value=20, rows=None, hour=18):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 16, hour, 30, tzinfo=tz)
    monkeypatch.setattr(feed, "datetime", Clock)
    monkeypatch.setattr(feed, "_validation_clock", lambda: Clock.now(feed.BEIJING))
    rows = rows or [("2026-10-15", 39.32), ("2026-10-16", 20)]
    csv = ("date,fear_greed_index\n" + "".join(f"{d},{v}\n" for d, v in rows)).encode()
    page = (f'<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">2026-10-16</p>'
            f'<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value">{page_value}</p>'
            '<p class="fg-update">数据日 2026-10-16 · 更新 2026-10-16 15:03</p>').encode()
    def get(url, maximum):
        if url == feed.CSV_URL and csv_ok:
            return csv
        if url == feed.PAGE_URL and page_ok:
            return page
        raise RuntimeError("injected unavailable")
    monkeypatch.setattr(feed, "_bounded_get", get)


def signal_for(evidence):
    current, source = 0.0, "none"
    units, target_source, reason = feed.transition("IC", current, source, .8, evidence["fear_greed_index"])
    if evidence["fear_greed_index"] is None:
        units, target_source, reason = current, source, "fear_unavailable"
    return {**evidence, "market_date": DAY.isoformat(), "close_confirmed": True,
            "grid_policy_revision": runner.strategy.grid_policy_revision(DAY),
            "grid_current": current, "grid_entry_source_current": source,
            "grid_target": units, "grid_entry_source_target": target_source,
            "grid_transition_reason": reason, "grid_action": "ADD_GRID" if units else "HOLD",
            "score": .8, "total_units_target": 1 + units, "total_units_change": units,
            "core_units_target": .5, "momentum_units_target": .5}


@pytest.mark.parametrize("csv_ok,page_ok,quality,selected", [
    (True, True, "matched", "csv"), (True, False, "single_source", "csv"),
    (False, True, "single_source", "page"),
])
def test_each_usable_source_reaches_state_and_publication(monkeypatch, csv_ok, page_ok, quality, selected):
    setup_feed(monkeypatch, csv_ok=csv_ok, page_ok=page_ok)
    evidence = feed.fetch_score(DAY, "close")
    assert evidence["fear_cross_validation"] == quality
    assert evidence["fear_selected_source"] == selected
    signal = signal_for(evidence)
    state.validate_fear_grid_signal(signal, "IC")
    assert feed.publication_view(signal)["grid_target"] == .5
    if quality == "single_source":
        assert "未完成双侧验证" in feed.source_disclosure(signal)
    if not page_ok:
        assert evidence["fear_page_updated_at"] is None
        assert "收盘后发布证明" in feed.source_disclosure(signal)


def test_conflict_is_disclosed_and_csv_remains_signal_input(monkeypatch):
    setup_feed(monkeypatch, page_value=80)
    evidence = feed.fetch_score(DAY, "close")
    assert evidence["fear_greed_index"] == 20 and evidence["fear_cross_validation"] == "mismatch"
    assert "不一致" in feed.source_disclosure(evidence)
    state.validate_fear_grid_signal(signal_for(evidence), "IC")


def test_both_missing_preserve_other_modules_and_never_publish_zero_hold(monkeypatch):
    setup_feed(monkeypatch, csv_ok=False, page_ok=False)
    evidence = feed.fetch_score(DAY, "close")
    signal = signal_for(evidence)
    state.validate_fear_grid_signal(signal, "IC")
    view = feed.publication_view(signal)
    assert view["grid_target"] is None and view["total_units_target"] is None
    assert view["core_units_target"] == .5 and view["momentum_units_target"] == .5
    assert view["grid_action"] == "UNCONFIRMED"


def test_one_bad_source_does_not_veto_the_other(monkeypatch):
    setup_feed(monkeypatch, rows=[("2026-10-16", float("nan"))])
    evidence = feed.fetch_score(DAY, "close")
    assert evidence["fear_selected_source"] == "page"
    assert evidence["fear_available_sources"] == ["page"]


def test_stale_csv_is_not_used_as_today(monkeypatch):
    setup_feed(monkeypatch, rows=[("2026-10-14", 5)], page_ok=False)
    evidence = feed.fetch_score(DAY, "close")
    assert evidence["fear_greed_index"] is None and evidence["fear_data_date"] is None
    assert "上一交易日" in evidence["fear_unavailable_reason"]


def test_csv_only_can_carry_exact_previous_exchange_session(monkeypatch):
    setup_feed(monkeypatch, rows=[("2026-10-15", 20)], page_ok=False)
    evidence = feed.fetch_score(DAY, "close")
    assert evidence["fear_data_date"] == "2026-10-15"
    state.validate_fear_grid_signal(signal_for(evidence), "IC")


@pytest.mark.parametrize("field,value", [
    ("fear_source_url", feed.JIU_URL), ("fear_csv_sha256", ""),
    ("fear_cross_validation", "matched"), ("fear_source_warning", ""),
    ("fear_data_date", "2026-10-14"), ("fear_page_updated_at", "2026-10-16T15:30:00+08:00"),
])
def test_single_source_does_not_relax_identity_or_invent_clocks(monkeypatch, field, value):
    setup_feed(monkeypatch, page_ok=False)
    signal = signal_for(feed.fetch_score(DAY, "close"))
    signal[field] = value
    with pytest.raises((ValueError, RuntimeError)):
        state.validate_fear_grid_signal(signal, "IC")
    assert feed.publication_view(signal)["grid_target"] is None


def test_jiu_snapshot_is_reference_only_and_cannot_change_grid(monkeypatch):
    setup_feed(monkeypatch)
    evidence = feed.fetch_score(DAY, "close")
    before = signal_for(evidence)
    for value in [0, 100]:
        monkeypatch.setattr(feed, "_bounded_get", lambda *args: json.dumps(
            {"date": "2026-10-16", "value": value,
             "history": [{"date": "2026-10-15", "value": 2.28}]}).encode())
        reference = feed.fetch_jiu_reference(DAY)
        after = {**before, **reference}
        state.validate_fear_grid_signal(after, "IC")
        assert after["grid_target"] == before["grid_target"] == .5
        assert "仅供参考，不参与网格信号" in feed.reference_disclosure(after)


def test_jiu_missing_and_date_mismatch_are_visible(monkeypatch):
    setup_feed(monkeypatch)
    assert feed.fetch_jiu_reference(DAY)["jiu_status"] == "unavailable"
    monkeypatch.setattr(feed, "_bounded_get", lambda *args: b'{"date":"2026-10-15","value":2.28}')
    reference = feed.fetch_jiu_reference(DAY)
    assert reference["jiu_data_date"] == "2026-10-15"
    assert "数据日不同" in feed.reference_disclosure({**reference, "fear_data_date": "2026-10-16"})


def test_archived_real_series_parse_without_modification():
    root = Path(__file__).parent
    csv = root / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
    if not csv.exists():
        pytest.skip("research archive is intentionally outside production checkout")
    values = feed._parse_csv(csv.read_bytes())
    assert len(values) == 8016 and max(values) == date(2026, 9, 24)
