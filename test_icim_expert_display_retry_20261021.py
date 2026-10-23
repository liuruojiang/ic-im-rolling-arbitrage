"""Public waiting/display integration; synthetic state, saved real SSE identity."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
import json

import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_batch3_old_call_20261021 as im_seed
import test_icim_round2_audit_lifecycle_20261021 as ic_seed
import test_ic_im_external_review_round2_20261021 as identity


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("unexpected network"))
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)


@pytest.mark.parametrize("kind", ["ordinary", "short_put", "profit3x"])
@pytest.mark.parametrize("execution", [date(2026, 10, 20), date(2026, 10, 21)])
def test_retry_public_builder_keeps_original_plan_and_returns_diagnostic(monkeypatch, kind, execution):
    im_seed.wire(monkeypatch, held=False)
    plan = {"product": "IM", "execution_day": execution, "legs": {}, "required_quotes": []}
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    event = {"kind": kind, "status": bot.put_execution.RETRY_OPEN_STATUS,
             "plan": plan, "reason": "synthetic original-day opening unavailable"}
    monkeypatch.setattr(bot, "_v14_ordinary_open_evidence", lambda *a: event if kind == "ordinary" else None)
    monkeypatch.setattr(bot, "_v14_project_committed_opens", lambda *a: [] if kind == "ordinary" else [event])
    out = bot.build_live_trade_signal("IM", im_seed.CLOCK, "close")
    assert out["diagnostic_only"] and out["signal_complete"] is False
    assert out["persistence_eligible"] is False and out["v14_action"] == "HOLD_FAIL_CLOSED"
    assert out["diagnostic_status"] == "awaiting_opening_evidence"
    assert out["retry_original_plans"][0]["execution_day"] == execution.isoformat()
    assert execution.isoformat() in out["diagnostic_reason"]
    assert "historical_replay" in out["diagnostic_reason"]
    assert not any(key in out for key in ("core_target", "put_target_contract", "v14_ordinary_put_pending",
                                           "v14_short_put_open_pending", "v14_profit_pending"))
    assert bot.LIVE_CONTINUATION_ANCHOR == original
    with pytest.raises(RuntimeError):
        state.require_persistence_eligible(out, "IM")


def test_ic_public_monthly_reset_with_missing_quantity_is_diagnostic(tmp_path, monkeypatch):
    store, record = ic_seed.recent_store(tmp_path / "state")
    anchors = state.anchors_from_record(record)
    anchor = anchors["IC"]
    chain, stamp = identity.wire(monkeypatch)
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", anchors)
    ic_seed.builder_inputs(monkeypatch, anchor, chain, stamp)
    monkeypatch.setattr(bot, "_v14_monthly_option_calendar", lambda *a: (True, False))
    reset = dict(contract=identity.M, security_id="10012099", quote=chain.iloc[0], stamp=stamp,
                 qty=None, absolute_delta=None, iv=None, expiry=date(2026, 12, 23), strike=7.5)
    monkeypatch.setattr(bot, "select_ic_put_for_reset", lambda *a: reset)
    before, disk = deepcopy(anchors), ic_seed.snapshot(store.root)
    out = bot.build_live_trade_signal("IC", ic_seed.CLOCK, "close")
    assert out["diagnostic_status"] == "put_quantity_unresolved"
    assert out["persistence_eligible"] is False and out["v14_action"] == "HOLD_FAIL_CLOSED"
    assert "put_target_total_qty" not in out and "put_target_contract" not in out
    assert bot.LIVE_CONTINUATION_ANCHOR == before
    assert ic_seed.snapshot(store.root) == disk


class Output:
    def __init__(self):
        self.text = []
        self.files = []
    def write(self, text):
        self.text.append(str(text))
    def attach_file(self, **file):
        self.files.append(file)


def display(monkeypatch, signal, observer=None):
    output = Output()
    @contextmanager
    def capture():
        yield output
    monkeypatch.setattr(bot, "_sm", capture)
    monkeypatch.setattr(bot, "_now_beijing", lambda: im_seed.CLOCK)
    monkeypatch.setattr(bot, "build_live_signal_with_transport_retry", lambda *a: signal)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", observer)
    bot.ICIMMainlinesBot()._handle_signal(("IM",), "close")
    return output


def test_observer_failure_does_not_hide_calculated_valid_signal(monkeypatch):
    im_seed.wire(monkeypatch, held=False)
    signal = bot.build_live_trade_signal("IM", im_seed.CLOCK, "close")
    def fails(*a):
        raise RuntimeError("synthetic collector outage")
    out = display(monkeypatch, signal, fails)
    text = "".join(out.text)
    assert "结构化结果采集失败" in text and "信号已生成" in text
    assert "下一交易日确认目标" in text and "当日信号警告" not in text
    assert str(signal["core_target"]) in text


def test_collector_cannot_mutate_nested_calculated_payload(monkeypatch):
    im_seed.wire(monkeypatch, held=False)
    signal = bot.build_live_trade_signal("IM", im_seed.CLOCK, "close")
    before = deepcopy(signal)
    def mutates(product, observed):
        observed["data_notes"].clear()
        observed["v14_action"] = "UNTRUSTED_MUTATION"
    display(monkeypatch, signal, mutates)
    assert signal == before


def test_diagnostic_page_and_json_have_no_formal_targets_or_pending(monkeypatch):
    dirty = {"core_target": "IM2703", "put_target_contract": "MO2701-P-7000",
             "v14_ordinary_put_pending": {"execution_day": date(2026, 10, 22)}}
    signal = bot.make_diagnostic_signal("IM", im_seed.CLOCK, "待核验", source_signal=dirty)
    collected = []
    out = display(monkeypatch, signal, lambda product, observed: collected.append(observed))
    text = "".join(out.text)
    assert "完整目标：N/A" in text and "不写入正式账本" in text
    assert "下一交易日确认目标" not in text
    assert len(collected) == len(out.files) == 1
    exported = json.loads(out.files[0]["contents"])
    assert exported["diagnostic_only"] and exported["persistence_eligible"] is False
    assert not any(key in exported for key in dirty)


def test_daily_ohlcv_wait_status_is_visible(monkeypatch):
    output = Output()
    @contextmanager
    def capture():
        yield output
    monkeypatch.setattr(bot, "_sm", capture)
    monkeypatch.setattr(bot, "_now_beijing", lambda: im_seed.CLOCK)
    def waits(*a):
        raise bot.DailyOHLCVNotReady("synthetic close bar not yet released")
    monkeypatch.setattr(bot, "build_live_signal_with_transport_retry", waits)
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *a: None)
    bot.ICIMMainlinesBot()._handle_signal(("IM",), "close")
    text = "".join(output.text)
    assert "指数日线尚未更新，请稍后重试" in text
    assert bot.DailyOHLCVNotReady.status in text


def test_momentum_only_price_cannot_be_a_core_profit_mark():
    signal = dict(put_current_core_qty=0, put_current_momentum_qty=3,
                  put_current_contract="510500P2611M06500", iv_monitor_option_price=.9)
    assert bot._v14_option_mark("IC", signal, None)[0] is None


def test_mismatched_sleeve_price_cannot_be_a_core_profit_mark():
    signal = dict(put_current_core_qty=5, put_current_momentum_qty=3,
                  put_current_contract="510500P2611M06500",
                  v14_core_put_current_contract="510500P2612M07500", iv_monitor_option_price=.9,
                  iv_monitor_evidence=dict(contract="510500P2611M06500", sleeve="momentum", option_price=.9))
    assert bot._v14_option_mark("IC", signal, None)[0] is None


def test_core_mark_uses_identity_bound_price_instead_of_generic_monitor():
    signal = dict(put_current_core_qty=5, put_current_momentum_qty=3,
                  put_current_contract="510500P2611M06500",
                  v14_core_put_current_contract="510500P2612M07500", iv_monitor_option_price=.9,
                  iv_monitor_evidence=dict(contract="510500P2612M07500", sleeve="core", option_price=.2))
    assert bot._v14_option_mark("IC", signal, None)[0] == .2


def test_new_action_code_remains_visible_instead_of_breaking_render(monkeypatch):
    im_seed.wire(monkeypatch, held=False)
    signal = bot.build_live_trade_signal("IM", im_seed.CLOCK, "close")
    signal["put_action"] = "UNKNOWN_ACTION_V99"
    output = display(monkeypatch, signal)
    assert "UNKNOWN_ACTION_V99" in "".join(output.text)
