"""Preserve precise validation reasons while enforcing new grid completeness."""
from datetime import date, datetime

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
from test_icim_b3adv_state_20261021 import prepare
from test_icim_round2_audit_lifecycle_20261021 import snapshot
from test_icim_round3_grid_persistence_20261021 import unavailable_fear


@pytest.mark.parametrize("boundary", ["producer", "delivery", "append"])
@pytest.mark.parametrize("problem,reason,product", [
    ("opening", "unresolved opening evidence", "IC"),
    ("opening", "unresolved opening evidence", "IM"),
    ("identity", "unresolved held Put identity", "IC"),
])
def test_required_put_evidence_is_reported_before_grid_completeness(
        tmp_path, monkeypatch, product, boundary, problem, reason):
    store, current, signals = prepare(tmp_path, monkeypatch)
    for signal in signals.values():
        unavailable_fear(signal)
    signal = signals[product]
    if problem == "opening":
        signal["v14_ordinary_put_open_status"] = bot.put_execution.RETRY_OPEN_STATUS
    else:
        signal["v14_held_put_identity_status"] = "unverified"
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match=reason):
        if boundary == "producer":
            state.validate_v14_new_signal(signal, product)
        elif boundary == "delivery":
            state.validate_delivery_values(signal, product)
        else:
            store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before


def test_calendar_error_retains_priority_over_unconfirmed_grid(tmp_path, monkeypatch):
    _store, _current, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals["IM"])
    signal.update(core_put_target_contract="MO2703-P-7200", put_target_contract="MO2703-P-7200")
    with pytest.raises(bot.CalendarCoverageError, match="2027"):
        state.validate_delivery_values(signal, "IM")


@pytest.mark.parametrize("boundary", ["fear", "delivery"])
def test_invalid_source_is_reported_before_grid_completeness(tmp_path, monkeypatch, boundary):
    _store, _current, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals["IC"])
    signal["fear_page_updated_at"] = "2026-10-21T15:00:00+08:00"
    with pytest.raises(RuntimeError, match="不可虚构网页发布时间"):
        if boundary == "fear":
            state.validate_fear_grid_signal(signal, "IC")
        else:
            state.validate_delivery_values(signal, "IC")


def test_partial_model_delivery_stays_valid_but_cannot_be_appended(tmp_path, monkeypatch):
    store, current, signals = prepare(tmp_path, monkeypatch)
    for signal in signals.values():
        unavailable_fear(signal).update(signal_complete=False, persistence_eligible=False,
                                       calendar_provisional=False, diagnostic_only=False)
    state.validate_delivery_values(signals["IC"], "IC", require_complete=False)
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match="未确认"):
        store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before


@pytest.mark.parametrize("change,reason", [
    ({"calendar_provisional": True}, "calendar_provisional"),
    ({"diagnostic_only": True}, "diagnostic_only"),
    ({"signal_complete": "false"}, "signal_complete"),
    ({"v14_action": "HOLD_FAIL_CLOSED"}, "目标未确认"),
])
def test_partial_publication_is_not_a_diagnostic_or_bad_marker_bypass(tmp_path, monkeypatch, change, reason):
    _store, _current, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals["IC"])
    signal.update(change)
    with pytest.raises(RuntimeError, match=reason):
        state.validate_delivery_values(signal, "IC", require_complete=False)


def test_old_both_missing_fixture_is_valid_partial_model_but_not_complete_new_signal(monkeypatch):
    # Replaces the old assumption that a valid unavailable-source transition is
    # sufficient for the default new-write validator. Preserve the same fixture.
    import test_ic_im_fear_source_delivery_20261016 as old
    old.setup_feed(monkeypatch, csv_ok=False, page_ok=False)
    signal = old.signal_for(bot.fear_grid.fetch_score(old.DAY, "close"))
    state.validate_fear_grid_signal(signal, "IC", check_grid=False)
    signal.update(signal_complete=True, persistence_eligible=True)
    for gate in (state.require_persistence_eligible, state.validate_fear_grid_signal):
        with pytest.raises(RuntimeError, match="网格目标未确认"):
            gate(signal, "IC")
    view = bot.fear_grid.publication_view(signal)
    assert view["grid_target"] is None and view["total_units_target"] is None
    assert view["core_units_target"] == view["momentum_units_target"] == 0.5


def test_old_short_expiry_fixture_needs_grid_confirmation_without_becoming_new_short_open(monkeypatch):
    # Keep the original expiry-branch semantics; add missing input confirmation.
    day = date(2026, 10, 16)
    anchor = ledger._record()["products"]["IM"]
    ledger._route(anchor, "IM", "short_put")
    signal = ledger._signal("IM", anchor, day)
    expected_action = signal["v14_action"]
    assert expected_action == "PUBLISH_SHORT_PUT_EXPIRY_BRANCHES"
    with pytest.raises(RuntimeError, match="网格目标未确认"):
        state.validate_v14_new_signal(signal, "IM")
    signal.update(
        fear_greed_index=40.0, fear_data_date=day, fear_data_status="same_day_post_close",
        fear_source_url=bot.fear_grid.CSV_URL, fear_csv_sha256="a" * 64,
        fear_page_updated_at="2026-10-16T15:01:00+08:00",
        fear_retrieved_at="2026-10-16T15:30:00+08:00",
        signal_generated_at="2026-10-16T15:40:00+08:00",
        signal_generation_timing="close_confirmed", signal_complete=True, persistence_eligible=True,
    )
    monkeypatch.setattr(bot.fear_grid, "_validation_clock", lambda: datetime(
        2026, 10, 16, 16, 0, tzinfo=bot.BEIJING))
    state.validate_v14_new_signal(signal, "IM")
    assert signal["v14_action"] == expected_action
    assert bot.fear_grid.publication_view(signal).get("grid_target_confirmed", True) is True
