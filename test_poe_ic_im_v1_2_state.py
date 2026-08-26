from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import date, datetime

import pytest

import poe_ic_im_mainline_v1_2_bot as strategy
from poe_ic_im_v1_2_state import StateStore, anchors_from_record


# Importing the ASGI module creates its bootstrap ledger.  Keep that collection-
# time side effect outside the repository and never reuse a caller's live state.
_SERVER_STATE_DIR = tempfile.TemporaryDirectory(prefix="ic_im_v1_2_test_")
_PREVIOUS_STATE_DIR = os.environ.get("ICIM_STATE_DIR")
os.environ["ICIM_STATE_DIR"] = _SERVER_STATE_DIR.name
try:
    import poe_ic_im_v1_2_server as server
finally:
    if _PREVIOUS_STATE_DIR is None:
        os.environ.pop("ICIM_STATE_DIR", None)
    else:
        os.environ["ICIM_STATE_DIR"] = _PREVIOUS_STATE_DIR


@pytest.fixture(autouse=True)
def _restore_strategy_runtime_state():
    anchors = deepcopy(strategy.LIVE_CONTINUATION_ANCHOR)
    observer = strategy._SIGNAL_OBSERVER
    poe_runtime = strategy.poe
    yield
    strategy.install_runtime_anchors(anchors)
    strategy.install_signal_observer(observer)
    strategy.poe = poe_runtime


def _signals(day: date) -> dict[str, dict[str, object]]:
    common = {
        "market_date": day,
        "close_confirmed": True,
        "momentum_current_weight": 0.5,
        "momentum_next_weight": 1.0,
        "grid_current": 0,
        "grid_target": 0,
    }
    return {
        "IC": {
            **common,
            "core_target": "IC2609",
            "put_target_contract": "510500P2612M07500",
            "put_target_security_id": "10012099",
            "put_target_total_qty": 14,
            "core_put_target_delta": 0.25,
            "momentum_put_target_delta": 0.0,
            "total_put_target_delta": 0.25,
            "core_put_driver": "MOM120负动量下限",
            "momentum_put_driver": "估值基础档",
        },
        "IM": {
            **common,
            "core_target": "IM2609",
            "put_target_contract": "MO2612-P-7200",
            "core_put_target_qty_normalized": 1.5,
            "v12_parent_puts_per_full_core": 3,
            "call_target_qty_normalized": 0.0,
            "call_target_contract": None,
            "call_target_expiry": None,
            "call_target_strike": None,
            "call_target_threat_roll_count": 0,
        },
    }


def test_state_survives_restart_and_advances_with_hash_chain(tmp_path):
    first_process = StateStore(tmp_path)
    day0 = first_process.initialize()
    day1 = first_process.append_confirmed_signals(day0, _signals(date(2026, 8, 25)))

    second_process = StateStore(tmp_path)
    reloaded = second_process.load_latest()
    assert reloaded["digest"] == day1["digest"]
    assert reloaded["previous_digest"] == day0["digest"]
    assert reloaded["verified_day"] == "2026-08-25"
    assert reloaded["sequence"] == 1
    anchors = anchors_from_record(reloaded)
    assert anchors["IC"]["last_verified_day"] == date(2026, 8, 25)
    assert anchors["IC"]["verified_next_momentum_weight"] == 1.0
    assert anchors["IC"]["post_put_security_id"] == "10012099"
    assert anchors["IM"]["verified_call_contract"] is None

    day2 = second_process.append_confirmed_signals(
        reloaded, _signals(date(2026, 8, 26))
    )
    assert day2["sequence"] == 2
    assert day2["previous_digest"] == day1["digest"]


def test_partial_product_or_skipped_day_never_changes_latest(tmp_path):
    store = StateStore(tmp_path)
    initial = store.initialize()
    partial = {"IC": _signals(date(2026, 8, 25))["IC"]}
    with pytest.raises(RuntimeError, match="IC/IM均完整成功"):
        store.append_confirmed_signals(initial, partial)
    assert store.load_latest()["digest"] == initial["digest"]

    with pytest.raises(RuntimeError, match="逐交易日推进"):
        store.append_confirmed_signals(initial, _signals(date(2026, 8, 26)))
    assert store.load_latest()["digest"] == initial["digest"]


def test_corrupt_latest_digest_fails_closed(tmp_path):
    store = StateStore(tmp_path)
    record = store.initialize()
    broken = deepcopy(record)
    broken["products"]["IM"]["verified_next_grid_units"] = 1.0
    store.latest_path.write_text(
        json.dumps(broken, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="SHA-256校验失败"):
        store.load_latest()


def test_missing_journal_or_broken_previous_digest_fails_closed(tmp_path):
    store = StateStore(tmp_path)
    initial = store.initialize()
    store.append_confirmed_signals(initial, _signals(date(2026, 8, 25)))
    sequence0 = next(store.journal_dir.glob("000000-*.json"))
    sequence0.unlink()
    with pytest.raises(RuntimeError, match="不存在或重复"):
        store.load_latest()


def test_orphan_journal_is_recovered_into_latest(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    initial = store.initialize()
    original_write = store._atomic_write
    calls = {"count": 0}

    def fail_latest_once(path, record):
        if path == store.latest_path and int(record.get("sequence", 0)) == 1:
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("simulated crash before latest replace")
        return original_write(path, record)

    monkeypatch.setattr(store, "_atomic_write", fail_latest_once)
    with pytest.raises(OSError, match="simulated crash"):
        store.append_confirmed_signals(initial, _signals(date(2026, 8, 25)))
    assert store.load_latest()["sequence"] == 0

    healed = store.append_confirmed_signals(initial, _signals(date(2026, 8, 25)))
    assert healed["sequence"] == 1
    assert store.load_latest()["digest"] == healed["digest"]


def test_server_close_observer_commits_and_next_process_loads_new_anchor(
    tmp_path, monkeypatch
):
    coordinator = server.LedgerCoordinator(StateStore(tmp_path))

    def fake_run(_self):
        for product, signal in _signals(date(2026, 8, 25)).items():
            strategy._SIGNAL_OBSERVER(product, deepcopy(signal))
        with strategy.poe.start_message() as message:
            message.write("confirmed")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_run)
    text, _, observed = coordinator.execute_query(
        "信号", datetime(2026, 8, 25, 15, 30, tzinfo=strategy.BEIJING)
    )
    assert text == "confirmed"
    assert set(observed) == {"IC", "IM"}
    assert coordinator.store.load_latest()["verified_day"] == "2026-08-25"

    restarted = server.LedgerCoordinator(StateStore(tmp_path))
    latest = restarted.store.load_latest()
    strategy.install_runtime_anchors(anchors_from_record(latest))
    assert strategy.LIVE_CONTINUATION_ANCHOR["IC"]["last_verified_day"] == date(
        2026, 8, 25
    )

    seen: dict[str, date] = {}

    def fake_next_day_run(_self):
        seen["IC"] = strategy.LIVE_CONTINUATION_ANCHOR["IC"]["last_verified_day"]
        seen["IM"] = strategy.LIVE_CONTINUATION_ANCHOR["IM"]["last_verified_day"]
        with strategy.poe.start_message() as message:
            message.write("next-day-ok")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_next_day_run)
    text, _, _ = restarted.execute_query(
        "实时信号", datetime(2026, 8, 26, 9, 42, tzinfo=strategy.BEIJING)
    )
    assert text == "next-day-ok"
    assert seen == {"IC": date(2026, 8, 25), "IM": date(2026, 8, 25)}


def test_missed_close_catchup_sets_explicit_historical_replay_day(
    tmp_path, monkeypatch
):
    coordinator = server.LedgerCoordinator(StateStore(tmp_path))
    seen: list[date | None] = []

    def fake_run(_self):
        replay_day = strategy._HISTORICAL_REPLAY_DAY.get()
        seen.append(replay_day)
        assert replay_day == date(2026, 8, 25)
        for product, signal in _signals(replay_day).items():
            strategy._SIGNAL_OBSERVER(product, deepcopy(signal))
        with strategy.poe.start_message() as message:
            message.write("historical-close-confirmed")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_run)
    advanced = coordinator.catch_up_once(
        datetime(2026, 8, 26, 14, 5, tzinfo=strategy.BEIJING)
    )
    assert advanced is True
    assert seen == [date(2026, 8, 25)]
    assert coordinator.store.load_latest()["verified_day"] == "2026-08-25"


def test_catch_up_until_current_advances_each_missing_session_in_order(
    tmp_path, monkeypatch
):
    coordinator = server.LedgerCoordinator(StateStore(tmp_path))
    seen: list[date] = []

    def fake_run(_self):
        replay_day = strategy._HISTORICAL_REPLAY_DAY.get()
        assert replay_day is not None
        seen.append(replay_day)
        for product, signal in _signals(replay_day).items():
            strategy._SIGNAL_OBSERVER(product, deepcopy(signal))
        with strategy.poe.start_message() as message:
            message.write("confirmed")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_run)
    count = coordinator.catch_up_until_current(
        datetime(2026, 8, 26, 16, 0, tzinfo=strategy.BEIJING), max_sessions=4
    )
    assert count == 2
    assert seen == [date(2026, 8, 25), date(2026, 8, 26)]
    assert coordinator.store.load_latest()["verified_day"] == "2026-08-26"


def test_stale_writer_cannot_overwrite_a_newer_sequence(tmp_path):
    store = StateStore(tmp_path)
    stale = store.initialize()
    store.append_confirmed_signals(stale, _signals(date(2026, 8, 25)))
    with pytest.raises(RuntimeError, match="另一请求推进"):
        store.append_confirmed_signals(stale, _signals(date(2026, 8, 25)))
