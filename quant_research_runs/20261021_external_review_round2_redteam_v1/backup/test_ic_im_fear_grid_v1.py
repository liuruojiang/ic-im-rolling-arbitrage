"""Adversarial time and provenance checks for the forward fix10 Fear feed."""

from datetime import date, datetime

import pytest
import copy
import json
from pathlib import Path

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


def _saved_unpublished_signal():
    fixture = Path(__file__).parent / "tests/fixtures/icim_fear_unpublished_20261014.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["signals"]["IC"]


@pytest.mark.parametrize(
    "score,status,source_day",
    [
        (None, "intraday_unpublished", None),
        (None, "intraday_provisional", "2026-10-14"),
        (float("nan"), "intraday_provisional", "2026-10-14"),
        (float("inf"), "intraday_provisional", "2026-10-14"),
        (40.0, "intraday_provisional", "2026-10-13"),
        (40.0, "intraday_unpublished", "2026-10-14"),
        (None, "missing_published_history", None),
    ],
)
def test_unconfirmed_fear_cannot_publish_zero_hold_as_complete(score, status, source_day):
    signal = _saved_unpublished_signal()
    signal.update(fear_greed_index=score, fear_data_status=status, fear_data_date=source_day)
    original = copy.deepcopy(signal)
    view = fear_grid.publication_view(signal)
    assert view["signal_completeness"] == "partial"
    assert view["grid_target"] is None and view["total_units_target"] is None
    assert view["total_units_change"] is None and view["grid_action"] == "UNCONFIRMED"
    assert view["reference_grid_target"] == 0 and view["reference_total_units_target"] == 0.5
    assert view["core_units_target"] == signal["core_units_target"] == 0.5
    assert view["put_target_core_qty"] == signal["put_target_core_qty"] == 10
    # NaN does not compare equal to itself; compare the unchanged scalar identity.
    assert signal["fear_greed_index"] is score
    assert signal["grid_action"] == original["grid_action"] == "HOLD"
    assert signal["grid_target"] == original["grid_target"] == 0


@pytest.mark.parametrize("score", [0.0, 25.0, 50.0, 100.0])
def test_valid_same_day_fear_preserves_all_existing_signal_values(score):
    signal = _saved_unpublished_signal()
    signal.update(fear_greed_index=score, fear_data_status="intraday_provisional",
                  fear_data_date=signal["market_date"])
    assert fear_grid.publication_view(signal) == signal


def test_legacy_and_proven_published_history_hole_keep_original_policy():
    signal = _saved_unpublished_signal()
    signal["market_date"] = "2026-09-29"
    assert fear_grid.publication_view(signal) == signal
    signal.update(market_date="2026-10-08", fear_data_status="missing_published_history",
                  fear_latest_available_date="2026-10-13")
    assert fear_grid.publication_view(signal) == signal


def test_fetch_reports_data_day_separately_from_page_update_day(monkeypatch):
    _feed(monkeypatch)
    evidence = fear_grid.fetch_score(date(2026, 10, 8), "close")
    assert evidence["fear_latest_available_date"] == evidence["fear_page_data_date"] == "2026-10-08"


def test_partial_publication_is_rejected_at_the_ledger_value_boundary():
    view = fear_grid.publication_view(_saved_unpublished_signal())
    with pytest.raises(RuntimeError, match="total_units_target.*数值"):
        state.validate_delivery_values(view, "IC")


def _intraday_feed(monkeypatch, rows, *, day=date(2026, 10, 14), hour=14, minute=30,
                   page_day=None, page_score=None, page_updated=None):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)

    latest, score = rows[-1]
    page_day = page_day or latest
    page_score = score if page_score is None else page_score
    page_updated = page_updated or f"{page_day} 15:03"
    raw = ("date,fear_greed_index\n" + "".join(f"{d},{s}\n" for d, s in rows)).encode()
    page = (
        f'<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">{page_day}</p>'
        f'<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value fg-fear">{page_score}</p>'
        f'<p class="fg-update">数据日 {page_day} · 更新 {page_updated}</p>'
    ).encode()
    monkeypatch.setattr(fear_grid, "datetime", Clock)
    monkeypatch.setattr(fear_grid, "_bounded_get", lambda url, _: raw if url == fear_grid.CSV_URL else page)
    # The collection clock and independent consumer clock have separate roles.
    # Synthetic future Monday fixtures must state their validation clock too.
    monkeypatch.setattr(fear_grid, "_validation_clock", lambda: datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=fear_grid.BEIJING))


def _fallback_signal(monkeypatch, score=39.32):
    _intraday_feed(monkeypatch, [("2026-10-13", score)])
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "intraday")
    signal = _saved_unpublished_signal()
    signal.update(evidence)
    units, source, reason = fear_grid.transition(
        "IC", signal["grid_current"], signal["grid_entry_source_current"], signal["score"], score
    )
    signal.update(grid_target=units, grid_entry_source_target=source, grid_transition_reason=reason,
                  grid_action="ADD_GRID" if units > signal["grid_current"] else "HOLD",
                  total_units_target=signal["core_units_target"] + signal["momentum_units_target"] + units)
    return signal


def test_intraday_prefers_today_over_previous_session(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-13", 60), ("2026-10-14", 20)],
                   page_updated="2026-10-14 14:20")
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "intraday")
    assert evidence["fear_greed_index"] == 20
    assert evidence["fear_data_date"] == "2026-10-14"
    assert evidence["fear_data_status"] == "intraday_provisional"
    assert evidence["fear_fallback_applied"] is False


def test_previous_session_value_is_usable_by_both_publication_and_state(monkeypatch):
    signal = _fallback_signal(monkeypatch)
    assert signal["fear_greed_index"] == 39.32
    assert signal["fear_data_status"] == fear_grid.INTRADAY_FALLBACK_STATUS
    assert signal["fear_data_date"] == signal["fear_previous_exchange_date"] == "2026-10-13"
    assert fear_grid.publication_view(signal) == signal
    state.validate_delivery_values(signal, "IC")


@pytest.mark.parametrize("day,previous", [
    (date(2026, 10, 19), date(2026, 10, 16)),
    (date(2026, 10, 8), date(2026, 9, 30)),
])
def test_previous_session_uses_official_calendar_across_weekend_and_long_holiday(day, previous):
    assert fear_grid.previous_exchange_session(day) == previous


def test_monday_fallback_uses_friday_not_sunday(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-16", 25)], day=date(2026, 10, 19))
    evidence = fear_grid.fetch_score(date(2026, 10, 19), "intraday")
    assert evidence["fear_data_date"] == "2026-10-16"
    assert evidence["fear_fallback_applied"] is True


def test_missing_previous_session_never_carries_an_older_value(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-12", 20)])
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "intraday")
    assert evidence["fear_greed_index"] is None and evidence["fear_data_date"] is None
    assert "2026-10-13" in evidence["fear_unavailable_reason"]
    signal = _saved_unpublished_signal()
    signal.update(evidence)
    assert fear_grid.publication_view(signal)["grid_target"] is None


@pytest.mark.parametrize("hour,minute", [(15, 0), (18, 0)])
def test_after_close_intraday_fetch_cannot_create_a_fallback(monkeypatch, hour, minute):
    _intraday_feed(monkeypatch, [("2026-10-13", 20)], hour=hour, minute=minute)
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "intraday")
    assert evidence["fear_greed_index"] is None
    assert evidence.get("fear_fallback_applied") is not True


def test_close_fetch_uses_newly_approved_previous_session(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-13", 20)], hour=18)
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "close")
    assert evidence["fear_data_date"] == "2026-10-13"
    assert evidence["fear_data_status"] == fear_grid.CLOSE_FALLBACK_STATUS
    assert evidence["fear_fallback_timing"] == "same_day_post_close"


@pytest.mark.parametrize("field,value", [
    ("close_confirmed", True),
    ("fear_data_date", "2026-10-12"),
    ("fear_previous_exchange_date", "2026-10-12"),
    ("fear_latest_available_date", "2026-10-14"),
    ("fear_page_data_date", "2026-10-14"),
    ("fear_intraday_policy_revision", "unapproved"),
    ("fear_fallback_applied", False),
    ("fear_fallback_reason", "stale_cache"),
    ("fear_source_url", "https://example.invalid/old.csv"),
    ("fear_csv_sha256", ""),
    ("fear_retrieved_at", "2026-10-14T15:00:00+08:00"),
    ("fear_retrieved_at", "2026-10-13T14:30:00+08:00"),
    ("fear_retrieved_at", "2026-10-14T14:30:00"),
    ("fear_page_updated_at", "2026-10-14T14:31:00+08:00"),
    ("fear_page_updated_at", "2026-10-13T09:00:00+08:00"),
    ("fear_page_updated_at", "2026-10-13T15:00:00+08:00"),
    ("fear_greed_index", None),
    ("fear_greed_index", float("nan")),
    ("fear_greed_index", float("inf")),
    ("fear_greed_index", True),
    ("fear_greed_index", -1),
    ("fear_greed_index", 101),
])
def test_fallback_requires_complete_evidence_at_state_and_publication_boundaries(monkeypatch, field, value):
    signal = _fallback_signal(monkeypatch)
    signal[field] = value
    with pytest.raises((RuntimeError, TypeError, ValueError)):
        state.validate_fear_grid_signal(signal, "IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None


@pytest.mark.parametrize("day", [date(2027, 1, 4), date(2026, 10, 18)])
def test_fallback_calendar_does_not_assume_uncovered_year_or_non_session(day):
    with pytest.raises(RuntimeError):
        fear_grid.previous_exchange_session(day)


@pytest.mark.parametrize("kwargs", [
    {"page_day": "2026-10-12"},
    {"page_score": 41},
    {"page_updated": "2026-10-14 14:31"},
])
def test_fallback_does_not_relax_source_pair_or_future_time_guards(monkeypatch, kwargs):
    _intraday_feed(monkeypatch, [("2026-10-13", 39.32)], **kwargs)
    with pytest.raises(RuntimeError):
        fear_grid.fetch_score(date(2026, 10, 14), "intraday")


def test_fallback_uses_completed_retrieval_time_when_http_crosses_close(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-13", 39.32)])
    clocks = iter([
        datetime(2026, 10, 14, 14, 59, 59, tzinfo=fear_grid.BEIJING),
        datetime(2026, 10, 14, 15, 0, 1, tzinfo=fear_grid.BEIJING),
    ])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(clocks)
    monkeypatch.setattr(fear_grid, "datetime", Clock)
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "intraday")
    assert evidence["fear_fetch_started_at"].startswith("2026-10-14T14:59:59")
    assert evidence["fear_retrieved_at"].startswith("2026-10-14T15:00:01")
    assert evidence["fear_greed_index"] is None
    assert evidence.get("fear_fallback_applied") is not True


@pytest.mark.parametrize("current,source,valuation,score,expected", [
    (0, "none", 1.575, 25, (0.5, "fear_only", "enter_fear_only")),
    (0, "none", 0.5, 25, (0.5, "both", "enter_both")),
    (0.5, "fear_only", 0.1, 50, (0, "none", "exit_fear")),
    (0.5, "both", 0.1, 50, (0, "none", "exit_fear")),
    (0.5, "valuation_only", 0.8, 50, (0.5, "valuation_only", "hold_grid")),
])
def test_previous_session_fear_drives_existing_grid_thresholds_without_changing_them(
    monkeypatch, current, source, valuation, score, expected
):
    signal = _fallback_signal(monkeypatch, score)
    units, target_source, reason = fear_grid.transition("IC", current, source, valuation, signal["fear_greed_index"])
    assert (units, target_source, reason) == expected
    signal.update(grid_current=current, grid_entry_source_current=source, score=valuation,
                  grid_target=units, grid_entry_source_target=target_source, grid_transition_reason=reason,
                  grid_action="ADD_GRID" if units > current else "EXIT_GRID" if units < current else "HOLD")
    state.validate_fear_grid_signal(signal, "IC")


def _close_fallback_signal(monkeypatch, *, collected_day=date(2026, 10, 15), hour=1):
    _intraday_feed(monkeypatch, [("2026-10-13", 39.32)], day=collected_day, hour=hour)
    evidence = fear_grid.fetch_score(date(2026, 10, 14), "close")
    signal = _saved_unpublished_signal()
    signal.update(evidence, close_confirmed=True,
                  signal_generated_at=evidence["fear_retrieved_at"],
                  signal_generation_timing="retrospective_replay" if collected_day > date(2026,10,14) else "close_confirmed")
    return signal


@pytest.mark.parametrize("collected_day,hour,timing", [
    (date(2026,10,14), 20, "same_day_post_close"),
    (date(2026,10,15), 1, "retrospective_replay"),
])
def test_approved_close_fallback_is_valid_for_delivery_and_ledger(monkeypatch, collected_day, hour, timing):
    signal = _close_fallback_signal(monkeypatch, collected_day=collected_day, hour=hour)
    assert signal["fear_fallback_timing"] == timing
    state.validate_delivery_values(signal, "IC")
    assert fear_grid.publication_view(signal) == signal


def test_close_still_prefers_qualified_today_value(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-13", 20), ("2026-10-14", 60)], hour=20,
                   page_updated="2026-10-14 15:01")
    evidence = fear_grid.fetch_score(date(2026,10,14), "close")
    assert evidence["fear_greed_index"] == 60 and evidence["fear_data_date"] == "2026-10-14"
    assert evidence["fear_data_status"] == "same_day_post_close"
    assert evidence.get("fear_fallback_applied") is not True


@pytest.mark.parametrize("day,hour", [(date(2026,10,14),14), (date(2026,10,14),15)])
def test_close_fallback_cannot_be_confirmed_before_or_at_close(monkeypatch, day, hour):
    _intraday_feed(monkeypatch, [("2026-10-13",39.32)], day=day, hour=hour, minute=0)
    with pytest.raises(RuntimeError, match="真实采集证据"):
        fear_grid.fetch_score(date(2026,10,14), "close")


def test_close_never_chains_yesterdays_already_carried_value(monkeypatch):
    _intraday_feed(monkeypatch, [("2026-10-13",39.32)], day=date(2026,10,15), hour=20)
    with pytest.raises(RuntimeError, match="无合格上一实际交易日"):
        fear_grid.fetch_score(date(2026,10,15), "close")


@pytest.mark.parametrize("field,value", [
    ("close_confirmed", False),
    ("fear_data_date", "2026-10-14"),
    ("fear_data_date", "2026-10-12"),
    ("fear_previous_exchange_date", "2026-10-12"),
    ("fear_latest_available_date", "2026-10-14"),
    ("fear_page_data_date", "2026-10-12"),
    ("fear_close_policy_revision", "unapproved"),
    ("fear_fallback_applied", False),
    ("fear_fallback_reason", "stale_cache"),
    ("fear_fallback_timing", "same_day_post_close"),
    ("signal_generation_timing", "close_confirmed"),
    ("signal_generated_at", "2026-10-14T20:00:00+08:00"),
    ("signal_generated_at", "2026-10-15T01:30:00"),
    ("fear_source_url", "https://example.invalid/fear.csv"),
    ("fear_csv_sha256", ""),
    ("fear_greed_index", float("nan")),
    ("fear_greed_index", True),
    ("fear_page_updated_at", "2026-10-13T15:00:00+08:00"),
    ("fear_page_updated_at", "2026-10-15T02:00:00+08:00"),
    ("fear_retrieved_at", "2026-10-13T20:00:00+08:00"),
])
def test_close_fallback_cannot_bypass_state_or_publication_provenance(monkeypatch, field, value):
    signal = _close_fallback_signal(monkeypatch)
    signal[field] = value
    with pytest.raises((RuntimeError, TypeError, ValueError)):
        state.validate_fear_grid_signal(signal, "IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None


def test_close_fallback_cannot_relabel_itself_as_same_day(monkeypatch):
    signal = _close_fallback_signal(monkeypatch)
    signal["fear_data_status"] = "same_day_post_close"
    with pytest.raises(RuntimeError):
        state.validate_fear_grid_signal(signal, "IC")


@pytest.mark.parametrize("collected_day,hour", [(date(2026,10,14),20), (date(2026,10,15),1)])
def test_unconfirmed_T_intraday_point_never_becomes_a_close_value_just_by_crossing_midnight(
    monkeypatch, collected_day, hour
):
    _intraday_feed(monkeypatch, [("2026-10-13",60), ("2026-10-14",20)],
                   day=collected_day, hour=hour, page_updated="2026-10-14 14:30")
    evidence = fear_grid.fetch_score(date(2026,10,14), "close")
    assert evidence["fear_greed_index"] == 60 and evidence["fear_data_date"] == "2026-10-13"
    assert evidence["fear_latest_available_date"] == evidence["fear_page_data_date"] == "2026-10-14"
    assert evidence["fear_fallback_reason"] == "same_day_not_close_confirmed"
    signal = _saved_unpublished_signal()
    signal.update(evidence, close_confirmed=True, signal_generated_at=evidence["fear_retrieved_at"],
                  signal_generation_timing="retrospective_replay" if collected_day > date(2026,10,14) else "close_confirmed")
    state.validate_fear_grid_signal(signal,"IC")
    assert fear_grid.publication_view(signal) == signal


@pytest.mark.parametrize("created", [None, "2026-10-14T20:00:00+08:00", "2026-10-15T01:30:00", "2026-10-15T02:00:00+08:00"])
def test_replayed_plan_creation_cannot_be_removed_or_forged(monkeypatch, created):
    signal=_close_fallback_signal(monkeypatch)
    signal["v14_ordinary_put_pending"]={"signal_day":"2026-10-14", "created_at":created,
        "creation_timing":"retrospective_replay", "research_scope":"retrospective_model_plan"}
    with pytest.raises((RuntimeError,ValueError)):
        state.validate_replay_plan_evidence(signal)


# Independent audit regressions: synthetic bytes/clocks, never market data.
def _adversarial_fear_timeline(monkeypatch, *, rows, started, completed, updated,
                               validated=None, mode="intraday"):
    observed = [datetime.fromisoformat(started), datetime.fromisoformat(completed)]
    class Clock(datetime):
        calls = 0
        @classmethod
        def now(cls, tz=None):
            value = observed[min(cls.calls, 1)]
            cls.calls += 1
            return value.astimezone(tz or fear_grid.BEIJING)
    latest, score = rows[-1]
    raw = ("date,fear_greed_index\n" + "".join(f"{day},{value}\n" for day,value in rows)).encode()
    page = (
        f'<p class="fg-stat-label">最新日期</p><p class="fg-stat-value">{latest}</p>'
        f'<p class="fg-stat-label">最新指数值</p><p class="fg-stat-value fg-fear">{score}</p>'
        f'<p class="fg-update">数据日 {latest} · 更新 {updated}</p>'
    ).encode()
    monkeypatch.setattr(fear_grid, "datetime", Clock)
    monkeypatch.setattr(fear_grid, "_bounded_get", lambda url,_: raw if url==fear_grid.CSV_URL else page)
    checked = datetime.fromisoformat(validated or completed)
    monkeypatch.setattr(fear_grid, "_validation_clock", lambda: checked)
    return fear_grid.fetch_score(date(2026,10,14), mode)


def _adversarial_same_day_signal(monkeypatch):
    evidence = _adversarial_fear_timeline(monkeypatch,
        rows=[("2026-10-13",60),("2026-10-14",20)],
        started="2026-10-14T14:59:58+08:00", completed="2026-10-14T14:59:59+08:00",
        validated="2026-10-14T15:00:02+08:00", updated="2026-10-14 14:59")
    signal = _saved_unpublished_signal()
    signal.update(evidence, grid_target=0.5, grid_action="ADD_GRID",
        grid_entry_source_target="fear_only", grid_transition_reason="enter_fear_only",
        total_units_target=1.0, total_units_change=0.5,
        signal_generated_at="2026-10-14T15:00:02+08:00",
        signal_generation_timing="intraday_hypothetical_if_now_close")
    return signal


def _adversarial_runner_signals(ic_signal):
    payload=json.loads((Path(__file__).parent/"tests/fixtures/icim_fear_unpublished_20261014.json").read_text(encoding="utf-8"))
    observed=copy.deepcopy(payload["signals"])
    observed["IC"]=copy.deepcopy(ic_signal)
    for field in (
        "fear_greed_index","fear_data_date","fear_data_status","fear_retrieved_at",
        "fear_fetch_started_at","fear_page_updated_at","fear_csv_sha256","fear_source_url",
        "fear_latest_available_date","fear_page_data_date","fear_intraday_policy_revision",
        "fear_fallback_applied","signal_generated_at","signal_generation_timing",
        "grid_target","grid_action","grid_entry_source_target","grid_transition_reason",
        "total_units_target","total_units_change",
    ):
        if field in ic_signal: observed["IM"][field]=ic_signal[field]
    latest={key:payload[key] for key in ("sequence","digest","verified_day")}
    return observed,latest


def test_adversarial_T_value_collection_crossing_1500_is_not_publishable(monkeypatch):
    evidence=_adversarial_fear_timeline(monkeypatch,
        rows=[("2026-10-13",60),("2026-10-14",20)],
        started="2026-10-14T14:59:59+08:00",completed="2026-10-14T15:00:01+08:00",
        updated="2026-10-14 14:59")
    assert evidence["fear_fetch_started_at"]=="2026-10-14T14:59:59+08:00"
    assert evidence["fear_retrieved_at"]=="2026-10-14T15:00:01+08:00"
    assert evidence["fear_greed_index"] is None and evidence["fear_data_date"] is None
    signal=_saved_unpublished_signal()
    signal.update(evidence)
    view=fear_grid.publication_view(signal)
    assert view["grid_target"] is None and view["total_units_target"] is None


def test_adversarial_runner_rejects_forged_T_evidence_completed_after_1500(monkeypatch):
    import run_ic_im_v1_4_github_digest as digest
    signal=_adversarial_same_day_signal(monkeypatch)
    # Preserve the old vulnerable payload's valid T value and full financial schema.
    # A producer status change alone cannot protect a consumer of an older payload.
    signal["fear_retrieved_at"]="2026-10-14T15:00:01+08:00"
    observed,latest=_adversarial_runner_signals(signal)
    with pytest.raises(RuntimeError,match="15:00"):
        digest.validate_realtime_artifact(clock=datetime(2026,10,14,14,59,58,tzinfo=fear_grid.BEIJING),
            completed_day=date(2026,10,13),before=latest,after=latest,observed=observed)
    assert fear_grid.publication_view(signal)["grid_target"] is None


def test_adversarial_before_1500_observation_can_finish_report_later(monkeypatch):
    import run_ic_im_v1_4_github_digest as digest
    signal=_adversarial_same_day_signal(monkeypatch)
    observed,latest=_adversarial_runner_signals(signal)
    digest.validate_realtime_artifact(clock=datetime(2026,10,14,14,59,58,tzinfo=fear_grid.BEIJING),
        completed_day=date(2026,10,13),before=latest,after=latest,observed=observed)
    assert fear_grid.publication_view(signal)["grid_target"]==0.5


@pytest.mark.parametrize("change",["missing","inverted","naive"])
def test_adversarial_fallback_start_proof_cannot_be_missing_inverted_or_naive(monkeypatch,change):
    signal=_close_fallback_signal(monkeypatch)
    if change=="missing":signal.pop("fear_fetch_started_at")
    elif change=="inverted":signal["fear_fetch_started_at"]="2026-10-15T01:30:01+08:00"
    else:signal["fear_fetch_started_at"]="2026-10-15T01:30:00"
    with pytest.raises((RuntimeError,ValueError)):
        state.validate_delivery_values(signal,"IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None


@pytest.mark.parametrize("generated",["2099-01-01T00:00:00+08:00","2026-10-15T01:29:59+08:00"])
def test_adversarial_generated_clock_has_trusted_upper_and_collection_lower_bound(monkeypatch,generated):
    signal=_close_fallback_signal(monkeypatch)
    signal["signal_generated_at"]=generated
    with pytest.raises(RuntimeError):state.validate_delivery_values(signal,"IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None


def test_adversarial_equal_completed_generated_validated_clocks_are_legal(monkeypatch):
    signal=_close_fallback_signal(monkeypatch)
    assert signal["fear_retrieved_at"]==signal["signal_generated_at"]
    state.validate_delivery_values(signal,"IC")
    assert fear_grid.publication_view(signal)==signal


def _adversarial_nonfallback_replay_signal(monkeypatch):
    evidence=_adversarial_fear_timeline(monkeypatch,
        rows=[("2026-10-13",60),("2026-10-14",20)],
        started="2026-10-15T01:29:59+08:00",completed="2026-10-15T01:30:00+08:00",
        updated="2026-10-14 15:01",mode="close")
    signal=_saved_unpublished_signal()
    signal.update(evidence,close_confirmed=True,signal_generated_at=evidence["fear_retrieved_at"],
        signal_generation_timing="retrospective_replay",grid_target=0.5,grid_action="ADD_GRID",
        grid_entry_source_target="fear_only",grid_transition_reason="enter_fear_only",
        total_units_target=1.0,total_units_change=0.5)
    return signal


@pytest.mark.parametrize("field,value",[
    ("fear_fallback_applied",True),("fear_fallback_applied","true"),
    ("fear_fallback_reason","same_day_not_published"),
    ("fear_fallback_timing","retrospective_replay"),
])
def test_adversarial_nonfallback_cannot_carry_fallback_claim(monkeypatch,field,value):
    signal=_adversarial_nonfallback_replay_signal(monkeypatch)
    state.validate_delivery_values(signal,"IC")
    signal[field]=value
    with pytest.raises(RuntimeError):state.validate_delivery_values(signal,"IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None


def test_adversarial_deleting_replay_label_does_not_bypass_created_evidence(monkeypatch):
    signal=_adversarial_nonfallback_replay_signal(monkeypatch)
    # A real financial fixture surrounds the isolated pending-plan proof challenge.
    signal["v14_ordinary_put_pending"]={"signal_day":"2026-10-14",
        "created_at":"2026-10-14T20:00:00+08:00"}
    signal.pop("signal_generation_timing")
    with pytest.raises(RuntimeError,match="回放标识"):
        state.validate_replay_plan_evidence(signal)
    with pytest.raises(RuntimeError):state.validate_delivery_values(signal,"IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None


@pytest.mark.parametrize("created",["2026-10-15T10:00:00+08:00","2026-10-16T10:00:00+08:00"])
def test_adversarial_late_retrospective_model_plan_remains_allowed(monkeypatch,created):
    monkeypatch.setattr(fear_grid,"_validation_clock",lambda: datetime.fromisoformat(created))
    signal={"market_date":"2026-10-14","fear_retrieved_at":created,
        "signal_generated_at":created,"signal_generation_timing":"retrospective_replay",
        "v14_ordinary_put_pending":{"signal_day":"2026-10-14","created_at":created,
            "creation_timing":"retrospective_replay","research_scope":"retrospective_model_plan"}}
    state.validate_replay_plan_evidence(signal)


@pytest.mark.parametrize("page_updated",[
    "2099-01-01T00:00:00+08:00",
    "2026-10-15T01:30:01+08:00",
    "2026-10-14T15:01:00",
])
def test_adversarial_nonfallback_page_clock_cannot_be_future_late_or_naive(monkeypatch,page_updated):
    signal=_adversarial_nonfallback_replay_signal(monkeypatch)
    # The baseline is a qualified T value, not a carried T-1 value.
    assert signal["fear_data_status"]=="retrospective_replay"
    assert signal.get("fear_fallback_applied") is not True
    state.validate_delivery_values(signal,"IC")
    signal["fear_page_updated_at"]=page_updated
    with pytest.raises((RuntimeError,ValueError)):
        state.validate_delivery_values(signal,"IC")
    assert fear_grid.publication_view(signal)["grid_target"] is None
    assert fear_grid.publication_view(signal)["total_units_target"] is None


@pytest.mark.parametrize("generated",[
    "2026-10-14T23:00:00Z",
    "2026-10-15T07:00:00+08:00",
])
def test_adversarial_utc_generation_boundary_cannot_delete_replay_identity(monkeypatch,generated):
    # Both strings describe the same actual next-Beijing-day generation instant.
    signal=_adversarial_nonfallback_replay_signal(monkeypatch)
    signal.update(fear_data_status="same_day_post_close",
        fear_fetch_started_at="2026-10-14T15:59:59+08:00",
        fear_retrieved_at="2026-10-14T16:00:00+08:00",
        signal_generated_at=generated,
        v14_ordinary_put_pending={"signal_day":"2026-10-14",
            "created_at":"2026-10-14T20:00:00+08:00"})
    signal.pop("signal_generation_timing")
    monkeypatch.setattr(fear_grid,"_validation_clock",lambda: datetime(
        2026,10,15,7,0,tzinfo=fear_grid.BEIJING))
    with pytest.raises(RuntimeError,match="回放"):
        state.validate_replay_plan_evidence(signal)
