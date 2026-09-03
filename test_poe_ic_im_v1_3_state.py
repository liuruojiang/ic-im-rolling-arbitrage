from __future__ import annotations

import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

import poe_ic_im_mainline_v1_3_bot as strategy
import poe_ic_im_v1_3_state as state_module
from poe_ic_im_v1_3_state import StateStore, anchors_from_record


# Importing the ASGI module creates its bootstrap ledger.  Keep that collection-
# time side effect outside the repository and never reuse a caller's live state.
_SERVER_STATE_DIR = tempfile.TemporaryDirectory(prefix="ic_im_v1_3_test_")
_PREVIOUS_STATE_DIR = os.environ.get("ICIM_STATE_DIR")
os.environ["ICIM_STATE_DIR"] = _SERVER_STATE_DIR.name
try:
    import poe_ic_im_v1_3_server as server
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
    previous = strategy._roll_backward_exchange_day(day - timedelta(days=1))
    next_day = strategy._roll_forward_exchange_day(day + timedelta(days=1))
    ic_current = 0.25 if day == date(2026, 8, 25) else 1.0
    im_current = 0.0 if day == date(2026, 8, 25) else 1.0
    im_momentum_put_current = 0.0 if day == date(2026, 8, 25) else 1.5
    im_momentum_put_contract = (
        None if im_momentum_put_current == 0.0 else "MO2612-P-7200"
    )
    common = {
        "market_date": day,
        "close_confirmed": True,
        "momentum_next_weight": 1.0,
        "grid_current": 0,
        "grid_target": 0,
        "market_phase": "收盘后",
        "state_anchor_day": previous,
        "next_trade_date": next_day,
    }
    return {
        "IC": {
            **common,
            "product": "IC",
            "momentum_current_weight": ic_current,
            "core_target": "IC2609",
            "put_target_contract": "510500P2612M07500",
            "put_target_security_id": "10012099",
            "put_target_total_qty": 14,
            "core_put_target_delta": 0.25,
            "momentum_put_target_delta": 0.0,
            "total_put_target_delta": 0.25,
            "core_put_driver": "MOM120负动量下限",
            "momentum_put_driver": "估值基础档",
            "call_target_qty_normalized": 0.0,
        },
        "IM": {
            **common,
            "product": "IM",
            "momentum_current_weight": im_current,
            "core_target": "IM2609",
            "put_target_contract": "MO2612-P-7200",
            "core_put_current_contract": "MO2612-P-7200",
            "core_put_target_contract": "MO2612-P-7200",
            "momentum_put_current_contract": im_momentum_put_contract,
            "momentum_put_target_contract": "MO2612-P-7200",
            "core_put_current_qty_normalized": 1.5,
            "core_put_target_qty_normalized": 1.5,
            "momentum_put_current_qty_normalized": im_momentum_put_current,
            "momentum_put_target_qty_normalized": 1.5,
            "total_put_current_qty_normalized": 1.5 + im_momentum_put_current,
            "total_put_target_qty_normalized": 3.0,
            "v13_parent_puts_per_full_core": 3,
            "call_target_qty_normalized": 0.0,
            "call_target_contract": None,
            "call_target_expiry": None,
            "call_target_strike": None,
            "call_target_threat_roll_count": 0,
        },
    }


def test_deployment_mode_refuses_empty_volume(tmp_path, monkeypatch):
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "1")

    with pytest.raises(RuntimeError, match="缺少已迁移"):
        server.LedgerCoordinator(StateStore(tmp_path))

    assert not (tmp_path / "latest.json").exists()


def test_v13_ledger_has_independent_schema_and_recomputed_im_genesis(tmp_path):
    record = StateStore(tmp_path).initialize()
    assert record["schema_version"] == 3
    assert record["strategy_version"] == "1.3"
    assert record["strategy_revision"] == "r6"
    assert record["genesis"]["copied_parent_momentum_anchor"] is False
    assert record["products"]["IM"]["verified_momentum_weight"] == 0.0
    assert record["products"]["IM"]["verified_next_momentum_weight"] == 0.0
    assert record["products"]["IC"]["verified_momentum_weight"] == 0.5
    assert record["products"]["IC"]["verified_next_momentum_weight"] == 0.25
    assert record["products"]["IM"]["verified_core_put_qty_normalized"] == 1.5
    assert record["products"]["IM"]["verified_momentum_put_qty_normalized"] == 0.0


def test_v13_state_validator_rejects_v12_record_even_with_matching_shape(tmp_path):
    record = StateStore(tmp_path).initialize()
    record["strategy_version"] = "1.2"
    with pytest.raises(RuntimeError, match="禁止续写旧账本"):
        state_module._validate_record(record)


def test_v13_state_validator_rejects_incomplete_r1_record(tmp_path):
    record = StateStore(tmp_path).initialize()
    record["strategy_revision"] = "r1"
    record["digest"] = state_module._digest(record)
    with pytest.raises(RuntimeError, match="strategy_revision不是r6"):
        state_module._validate_record(record)


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
    assert anchors["IM"]["verified_momentum_put_qty_normalized"] == 1.5
    assert anchors["IM"]["verified_total_put_qty_normalized"] == 3.0

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


def test_same_day_close_catchup_uses_current_complete_market_path(
    tmp_path, monkeypatch
):
    coordinator = server.LedgerCoordinator(StateStore(tmp_path))
    seen: list[tuple[date | None, date]] = []

    def fake_run(_self):
        replay_day = strategy._HISTORICAL_REPLAY_DAY.get()
        runtime_day = strategy._now_beijing().date()
        seen.append((replay_day, runtime_day))
        assert replay_day is None
        assert runtime_day == date(2026, 8, 25)
        for product, signal in _signals(runtime_day).items():
            strategy._SIGNAL_OBSERVER(product, deepcopy(signal))
        with strategy.poe.start_message() as message:
            message.write("same-day-close-confirmed")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_run)
    advanced = coordinator.catch_up_once(
        datetime(2026, 8, 25, 15, 30, tzinfo=strategy.BEIJING)
    )
    assert advanced is True
    assert seen == [(None, date(2026, 8, 25))]
    assert coordinator.store.load_latest()["verified_day"] == "2026-08-25"


def test_catch_up_until_current_advances_each_missing_session_in_order(
    tmp_path, monkeypatch
):
    coordinator = server.LedgerCoordinator(StateStore(tmp_path))
    seen: list[date | None] = []

    def fake_run(_self):
        replay_day = strategy._HISTORICAL_REPLAY_DAY.get()
        seen.append(replay_day)
        signal_day = replay_day or strategy._now_beijing().date()
        for product, signal in _signals(signal_day).items():
            strategy._SIGNAL_OBSERVER(product, deepcopy(signal))
        with strategy.poe.start_message() as message:
            message.write("confirmed")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_run)
    count = coordinator.catch_up_until_current(
        datetime(2026, 8, 26, 16, 0, tzinfo=strategy.BEIJING), max_sessions=4
    )
    assert count == 2
    assert seen == [date(2026, 8, 25), None]
    assert coordinator.store.load_latest()["verified_day"] == "2026-08-26"


def test_stale_writer_cannot_overwrite_a_newer_sequence(tmp_path):
    store = StateStore(tmp_path)
    stale = store.initialize()
    store.append_confirmed_signals(stale, _signals(date(2026, 8, 25)))
    with pytest.raises(RuntimeError, match="另一请求推进"):
        store.append_confirmed_signals(stale, _signals(date(2026, 8, 25)))


@pytest.mark.parametrize(
    ("product", "field", "value", "message"),
    [
        ("IC", "market_phase", "盘中", "仅允许收盘后"),
        ("IM", "product", "IC", "产品标签不一致"),
        ("IC", "momentum_current_weight", 999.0, "合法域"),
        ("IM", "core_put_target_qty_normalized", -1.0, "合法域"),
    ],
)
def test_adversarial_signal_payloads_fail_closed(tmp_path, product, field, value, message):
    store = StateStore(tmp_path)
    current = store.initialize()
    signals = _signals(date(2026, 8, 25))
    signals[product][field] = value
    with pytest.raises(RuntimeError, match=message):
        store.append_confirmed_signals(current, signals)
    assert store.load_latest()["sequence"] == 0


def test_top_level_verified_day_must_match_both_product_anchors(tmp_path):
    record = StateStore(tmp_path).initialize()
    record["verified_day"] = "2026-08-25"
    record["digest"] = state_module._digest(record)
    with pytest.raises(RuntimeError, match="顶层verified_day"):
        state_module._validate_record(record)


def test_two_writers_cannot_both_advance_same_sequence(tmp_path):
    first = StateStore(tmp_path)
    current = first.initialize()
    stores = [StateStore(tmp_path), StateStore(tmp_path)]

    def append(store):
        try:
            return ("ok", store.append_confirmed_signals(current, _signals(date(2026, 8, 25))))
        except RuntimeError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, stores))
    assert sorted(status for status, _ in outcomes) == ["error", "ok"]
    assert first.load_latest()["sequence"] == 1


def test_stale_complete_observation_is_not_reported_as_catchup(tmp_path, monkeypatch):
    coordinator = server.LedgerCoordinator(StateStore(tmp_path))

    def fake_run(_self):
        for product, signal in _signals(date(2026, 8, 24)).items():
            strategy._SIGNAL_OBSERVER(product, deepcopy(signal))
        with strategy.poe.start_message() as message:
            message.write("stale")

    monkeypatch.setattr(strategy.ICIMMainlinesBot, "run", fake_run)
    assert coordinator.catch_up_once(
        datetime(2026, 8, 26, 16, 0, tzinfo=strategy.BEIJING)
    ) is False
    assert "未使账本恰好推进" in str(coordinator.last_refresh_error)
    health = coordinator.health(datetime(2026, 8, 26, 16, 0, tzinfo=strategy.BEIJING))
    assert health["status"] == "degraded"
