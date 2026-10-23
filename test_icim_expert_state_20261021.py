"""New-write eligibility and shared-anchor entry points; disposable stores only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import threading

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
from test_icim_b3adv_state_20261021 import prepare
from test_icim_batch3_concurrency_20261021 import server
from test_icim_round2_audit_lifecycle_20261021 import snapshot


DISQUALIFIED = [
    {"calendar_provisional": True},
    {"diagnostic_only": True},
    {"signal_complete": False},
    {"persistence_eligible": False},
    {"v14_action": "HOLD_FAIL_CLOSED"},
]


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("markers", DISQUALIFIED)
def test_direct_store_rejects_diagnostic_or_incomplete_signal_without_any_write(
        tmp_path, monkeypatch, product, markers):
    store, current, signals = prepare(tmp_path, monkeypatch)
    signals[product].update(markers)
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match="禁止持久化"):
        store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before


@pytest.mark.parametrize("marker,value", [
    ("calendar_provisional", "false"), ("diagnostic_only", 0),
    ("signal_complete", 1), ("persistence_eligible", "true"),
])
def test_nonboolean_eligibility_markers_cannot_bypass_new_write_gate(
        tmp_path, monkeypatch, marker, value):
    store, current, signals = prepare(tmp_path, monkeypatch)
    signals["IC"][marker] = value
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match="禁止持久化"):
        store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before


def test_complete_official_pair_commits_and_older_record_validation_stays_compatible(tmp_path, monkeypatch):
    store, current, signals = prepare(tmp_path, monkeypatch)
    for signal in signals.values():
        signal.update(calendar_provisional=False, diagnostic_only=False,
                      signal_complete=True, persistence_eligible=True)
    written = store.append_confirmed_signals(current, signals)
    assert written["sequence"] == current["sequence"] + 1
    # This synthetic old-format read isolates compatibility from new writes.
    # No such record is written: the public append was tested to reject it.
    historical = deepcopy(written)
    historical["signals"]["IC"].update(diagnostic_only=True, signal_complete=False)
    historical["digest"] = state._digest(historical)
    state._validate_record(historical)
    assert store.load_latest()["digest"] == written["digest"]


def _coordinator(tmp_path, monkeypatch, markers=None, *, intraday=False):
    store, current, signals = prepare(tmp_path, monkeypatch)
    module = server(monkeypatch, tmp_path)
    coordinator = module.LedgerCoordinator(store)
    monkeypatch.setattr(coordinator, "_record_for_display", lambda _clock: current)
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    if markers:
        signals["IC"].update(markers)
    if intraday:
        for signal in signals.values():
            signal["close_confirmed"] = False
    def body(_self):
        for product in ("IC", "IM"):
            with bot.poe.start_message() as message:
                message.write("calculated-" + product + "\n")
            bot._SIGNAL_OBSERVER(product, deepcopy(signals[product]))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "run", body)
    clock = bot.datetime.combine(date(2026, 10, 21), bot.time(15, 40), tzinfo=bot.BEIJING)
    return module, coordinator, current, signals, clock


@pytest.mark.parametrize("markers", DISQUALIFIED)
def test_coordinator_independently_gates_writes_and_preserves_calculated_output(
        tmp_path, monkeypatch, markers):
    _module, coordinator, current, signals, clock = _coordinator(tmp_path, monkeypatch, markers)
    attempted = []
    monkeypatch.setattr(coordinator.store, "append_confirmed_signals",
                        lambda *_args: attempted.append(True))
    text, _, observed = coordinator.execute_query("信号", clock)
    assert "calculated-IC" in text and "calculated-IM" in text
    assert observed == signals
    assert "尚不具备持久化资格" in text
    assert not attempted
    assert coordinator.store.load_latest()["digest"] == current["digest"]
    assert coordinator.last_refresh_error


def test_intraday_diagnostic_capture_is_preserved_without_attempting_a_commit(tmp_path, monkeypatch):
    _module, coordinator, current, signals, clock = _coordinator(
        tmp_path, monkeypatch, {"calendar_provisional": True}, intraday=True)
    attempted = []
    monkeypatch.setattr(coordinator.store, "append_confirmed_signals",
                        lambda *_args: attempted.append(True))
    text, _, observed = coordinator.execute_query("实时信号", clock)
    assert "calculated-IC" in text and set(observed) == {"IC", "IM"}
    assert observed == signals and not attempted
    assert coordinator.store.load_latest()["digest"] == current["digest"]


def test_append_failure_keeps_signals_visible_and_catchup_cannot_claim_progress(tmp_path, monkeypatch):
    _module, coordinator, current, signals, clock = _coordinator(tmp_path, monkeypatch)
    def fail(_current, _signals):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(coordinator.store, "_append_locked", fail)
    before = snapshot(coordinator.store.root)
    text, _, observed = coordinator.execute_query("信号", clock)
    assert observed == signals and "calculated-IC" in text
    assert "信号已生成，但持久化失败" in text and "synthetic disk failure" in text
    assert "OSError" in coordinator.last_refresh_error
    assert snapshot(coordinator.store.root) == before
    assert coordinator.catch_up_once(clock) is False
    assert coordinator.last_refresh_error
    assert "synthetic disk failure" in coordinator.last_refresh_diagnostic
    assert coordinator.store.load_latest()["digest"] == current["digest"]


@pytest.mark.parametrize("field", ["post_put_qty", "verified_core_put_delta", "verified_momentum_put_delta"])
def test_missing_ic_anchor_fields_raise_valueerror_and_install_neither_product(monkeypatch, field):
    anchors = state.anchors_from_record(state.bootstrap_record())
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    anchors["IM"]["audit_context_label"] = "must-not-install"
    del anchors["IC"][field]
    with pytest.raises(ValueError, match=field):
        bot.install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == before


@pytest.mark.parametrize("field,value", [
    ("post_put_qty", True), ("post_put_qty", float("inf")),
    ("verified_core_put_delta", float("nan")), ("verified_momentum_put_delta", "0.5"),
    ("verified_core_put_delta", -0.1), ("verified_momentum_put_delta", 1.1),
])
def test_invalid_ic_quantity_or_delta_is_rejected_atomically(monkeypatch, field, value):
    anchors = state.anchors_from_record(state.bootstrap_record())
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    anchors["IC"][field] = value
    with pytest.raises(ValueError, match=field):
        bot.install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == before


@pytest.mark.parametrize("entry", ["install", "project"])
def test_public_anchor_mutation_entry_blocks_on_strategy_shared_lock(monkeypatch, entry):
    anchors = state.anchors_from_record(state.bootstrap_record())
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    started, finished = threading.Event(), threading.Event()
    if entry == "project":
        monkeypatch.setattr(bot.put_execution, "active", lambda _day: False)
    def call():
        started.set()
        if entry == "install":
            bot.install_runtime_anchors(anchors)
        else:
            bot._v14_project_committed_opens("IM", date(2026, 10, 21),
                                           bot._now_beijing(), {})
        finished.set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with bot.STRATEGY_QUERY_LOCK:
            future = executor.submit(call)
            assert started.wait(3)
            assert not finished.wait(0.1)
        future.result(timeout=5)
    assert finished.is_set()


def test_coordinators_use_the_same_lock_as_public_strategy_entries(tmp_path, monkeypatch):
    _module, coordinator, _current, _signals, _clock = _coordinator(tmp_path, monkeypatch)
    assert coordinator.lock is bot.STRATEGY_QUERY_LOCK
