"""Independent integration adversaries; production state never opened."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import json

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as im
from test_icim_b3adv_state_20261021 import prepare
from test_icim_batch3_concurrency_20261021 import server
from test_icim_round2_audit_lifecycle_20261021 import snapshot
import test_ic_im_external_review_20261021 as plans


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network forbidden"))


def profit_pending(anchor, execution=date(2026, 10, 20)):
    anchor.update(v14_profit_pending=True,
                  v14_profit_trigger_day=date(2026, 10, 19),
                  v14_profit_execution_day=execution,
                  v14_profit_old_contract=anchor["post_core_put_contract"],
                  v14_profit_old_security_id=None,
                  v14_profit_old_qty=float(anchor["verified_core_put_qty_normalized"]),
                  v14_profit_reentry_contract="MO2612-P-7600",
                  v14_profit_reentry_security_id=None,
                  v14_profit_reentry_qty=1.5)
    bot.v14_policy.validate_extension("IM", anchor)


def test_real_overdue_profit_builder_waits_without_later_day_quotes(monkeypatch):
    im.wire(monkeypatch, held=False)
    anchor = bot.LIVE_CONTINUATION_ANCHOR["IM"]
    profit_pending(anchor)
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    original_plan = bot.put_execution.profit_plan("IM", anchor, date(2026, 10, 20))
    def forbidden(*a, **k):
        pytest.fail("an overdue plan must not fetch a later day's execution price")
    monkeypatch.setattr(bot, "fetch_cffex_quotes", forbidden)
    out = bot.build_live_trade_signal("IM", im.CLOCK, "close")
    assert out["diagnostic_status"] == "awaiting_opening_evidence"
    assert out["retry_original_plans"] == [state._jsonable(original_plan)]
    assert "2026-10-20" in out["diagnostic_reason"] and "historical_replay" in out["diagnostic_reason"]
    assert out["persistence_eligible"] is False
    assert bot.LIVE_CONTINUATION_ANCHOR == original


def test_real_multiple_due_plans_are_all_disclosed_without_projecting(monkeypatch):
    im.wire(monkeypatch, held=False)
    anchor = bot.LIVE_CONTINUATION_ANCHOR["IM"]
    ordinary = plans.ordinary_anchor("IM")["v14_ordinary_put_pending"]
    ordinary = deepcopy(ordinary)
    ordinary.update(signal_day=date(2026, 10, 19), execution_day=date(2026, 10, 20))
    # The ordinary plan can change only momentum while 3x owns the core.
    ordinary["legs"]["core"]["changed"] = False
    ordinary["legs"]["core"].update(old_contract=anchor["post_core_put_contract"],
                                     new_contract=anchor["post_core_put_contract"],
                                     old_qty=anchor["verified_core_put_qty_normalized"],
                                     new_qty=anchor["verified_core_put_qty_normalized"])
    ordinary["legs"]["momentum"].update(changed=True, new_contract="MO2612-P-7200", new_qty=.5)
    anchor["v14_ordinary_put_pending"] = ordinary
    profit_pending(anchor)
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    def forbidden(*a, **k):
        pytest.fail("overdue plans must not fetch new execution quotes")
    monkeypatch.setattr(bot, "fetch_cffex_quotes", forbidden)
    out = bot.build_live_trade_signal("IM", im.CLOCK, "close")
    assert out["diagnostic_status"] == "awaiting_opening_evidence"
    assert len(out["retry_original_plans"]) == 2
    assert state._jsonable(ordinary) in out["retry_original_plans"]
    expected_profit = bot.put_execution.profit_plan("IM", anchor, date(2026, 10, 20))
    assert state._jsonable(expected_profit) in out["retry_original_plans"]
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def test_install_rejects_missing_consumed_ic_total_delta_atomically(monkeypatch):
    anchors = state.anchors_from_record(state.bootstrap_record())
    del anchors["IC"]["verified_total_put_delta"]
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    with pytest.raises(ValueError, match="verified_total_put_delta"):
        bot.install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == before


@pytest.mark.parametrize("value", [None, True, "0.25", float("nan"), float("inf"), -.01, 1.1])
def test_install_rejects_invalid_consumed_ic_total_delta_atomically(monkeypatch, value):
    anchors = state.anchors_from_record(state.bootstrap_record())
    anchors["IC"]["verified_total_put_delta"] = value
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    with pytest.raises(ValueError, match="verified_total_put_delta"):
        bot.install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == before


def test_install_rejects_ic_total_delta_inconsistent_with_verified_sleeves(monkeypatch):
    anchors = state.anchors_from_record(state.bootstrap_record())
    anchors["IC"]["verified_total_put_delta"] = .5
    before = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    with pytest.raises(ValueError, match="Delta|delta"):
        bot.install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == before


@pytest.mark.parametrize("marker", ["calendar_provisional", "diagnostic_only", "signal_complete", "persistence_eligible"])
@pytest.mark.parametrize("value", [None, [], {}])
def test_direct_store_rejects_all_nonboolean_new_eligibility_markers(tmp_path, monkeypatch, marker, value):
    store, current, signals = prepare(tmp_path, monkeypatch)
    signals["IM"][marker] = value
    before = snapshot(store.root)
    with pytest.raises(RuntimeError, match="禁止持久化"):
        store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before


def test_two_coordinators_see_failed_write_then_clean_success_not_false_progress(tmp_path, monkeypatch):
    store, current, signals = prepare(tmp_path, monkeypatch)
    module = server(monkeypatch, tmp_path)
    a, b = module.LedgerCoordinator(store), module.LedgerCoordinator(store)
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    for coordinator in [a, b]:
        monkeypatch.setattr(coordinator, "_record_for_display", lambda _clock: current)
    def body(_self):
        with bot.poe.start_message() as msg:
            msg.write("verified-public-result")
        for product, signal in signals.items():
            bot._SIGNAL_OBSERVER(product, deepcopy(signal))
    monkeypatch.setattr(bot.ICIMMainlinesBot, "run", body)
    actual_append = store._append_locked
    attempts = []
    def fails_once(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("independent simulated transient storage error")
        return actual_append(*args)
    monkeypatch.setattr(store, "_append_locked", fails_once)
    clock = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)
    before = snapshot(store.root)
    assert a.catch_up_once(clock) is False
    assert snapshot(store.root) == before
    assert "verified-public-result" in a.last_refresh_diagnostic
    assert "持久化失败" in a.last_refresh_diagnostic
    assert b.catch_up_once(clock) is True
    assert store.load_latest()["sequence"] == current["sequence"] + 1
    assert a.lock is b.lock is bot.STRATEGY_QUERY_LOCK


@pytest.mark.parametrize("value", [False, True, "", None, "000000", "091459"])
def test_stamp_validator_cannot_accept_missing_or_premarket_time(value):
    clock = datetime(2026, 10, 21, 14, 0, tzinfo=bot.BEIJING)
    with pytest.raises(RuntimeError):
        bot._validate_chain_stamp_matches("independent", {"date": "20261021", "time": value}, date(2026, 10, 21), clock)


def test_mixed_waiting_page_preserves_nonwaiting_failure_and_exports_no_target(monkeypatch):
    class Capture:
        def __init__(self): self.text=[]; self.files=[]
        def write(self, text): self.text.append(str(text))
        def attach_file(self, **kwargs): self.files.append(kwargs)
    output = Capture()
    @contextmanager
    def capture():
        yield output
    monkeypatch.setattr(bot, "_sm", capture)
    monkeypatch.setattr(bot, "_now_beijing", lambda: im.CLOCK)
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *a: None)
    error = bot.DailyOHLCVSourcesUnavailable("Sina: waiting; Eastmoney: malformed; Tencent: waiting")
    error.waiting_sources = ["Sina", "Tencent"]
    def fail(*a): raise error
    monkeypatch.setattr(bot, "build_live_signal_with_transport_retry", fail)
    bot.ICIMMainlinesBot()._handle_signal(("IM",), "close")
    text = "".join(output.text)
    assert "其他来源也未通过校验" in text and "malformed" in text
    assert "下一交易日确认目标" not in text and not output.files
