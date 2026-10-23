"""Round-three display repairs: real public routing with offline observations.

The concurrency test controls only private computation's projection window;
the public builder lock/finally and public snapshot route are exercised intact.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import json
from threading import Event, Thread
from types import SimpleNamespace

import numpy as np
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import test_icim_batch3_old_call_20261021 as seed


class Output:
    def __init__(self):
        self.text = []
        self.files = []

    def write(self, value):
        self.text.append(str(value))

    def attach_file(self, **value):
        self.files.append(value)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("unexpected network in display repair verification")

    monkeypatch.setattr(bot.requests, "get", reject)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)


def capture_public(monkeypatch, query, clock):
    output = Output()

    @contextmanager
    def capture():
        yield output

    monkeypatch.setattr(bot, "_sm", capture)
    monkeypatch.setattr(bot, "_now_beijing", lambda: clock)
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text=query), raising=False)
    bot.ICIMMainlinesBot().run()
    return output


@pytest.mark.parametrize("query", ["查询", "信号", "实时信号"])
def test_unknown_calendar_public_run_keeps_both_verified_snapshots(monkeypatch, query):
    clock = datetime(2027, 1, 4, 16, tzinfo=bot.BEIJING)
    output = capture_public(monkeypatch, query, clock)
    text = "".join(output.text)
    assert "查询失败" not in text
    assert "calendar_unverified" in text
    assert "IC 1.4" in text and "IM 1.4" in text
    assert text.count("最后逐腿核验快照") == 2
    assert "完整目标：N/A" in text
    assert len(output.files) == 2
    for item in output.files:
        signal = json.loads(item["contents"])
        assert signal["persistence_eligible"] is False
        assert signal["diagnostic_only"] is True
    if query != "实时信号":
        assert "构建 未核验" in text


@pytest.mark.parametrize("mode", ["intraday", "close"])
def test_known_calendar_report_identity_is_unchanged(mode):
    clock = datetime(2026, 10, 21, 16, tzinfo=bot.BEIJING)
    assert bot._report_build_id(mode, clock) == bot.v14_policy.identity_for_signal_day(clock.date())[0]


def test_invalid_report_mode_still_rejected():
    with pytest.raises(ValueError, match="unsupported signal report mode"):
        bot._report_build_id("unknown")


def test_diagnostic_references_are_explicit_and_do_not_copy_targets_or_plans():
    dirty = {
        "market_date": date(2026, 10, 21),
        "momentum_next_weight": np.float64(.5),
        "grid_target": .5,
        "core_action": "ROLL",
        "roll_execution_date": date(2027, 3, 16),
        "roll_preview": np.bool_(True),
        "roll_confirmed": False,
        "core_target": "IM2703",
        "put_target_contract": "MO2703-P-7200",
        "v14_short_put_open_pending": {"execution_day": "2026-10-22"},
        "v14_profit_pending": True,
        "v14_ordinary_put_open_plan": {"new_contract": "forbidden"},
    }
    before = deepcopy(dirty)
    signal = bot.make_diagnostic_signal("IM", seed.CLOCK, "synthetic calendar gap", source_signal=dirty)
    refs = signal["diagnostic_model"]["unconfirmed_references"]
    assert refs["confirmed"] is False and refs["label"] == "参考计算值（未确认）"
    assert refs["momentum_weight_reference"] == .5
    assert refs["grid_units_reference"] == .5
    assert refs["quarter_action_reference"] == "ROLL"
    assert refs["quarter_execution_date_reference"] == date(2027, 3, 16)
    assert refs["quarter_preview_reference"] == np.bool_(True)
    assert refs["quarter_model_roll_reference"] is False
    assert signal["persistence_eligible"] is False and signal["signal_complete"] is False
    assert dirty == before
    for key in ("core_target", "grid_target", "put_target_contract", "v14_short_put_open_pending",
                "v14_profit_pending", "v14_ordinary_put_open_plan"):
        assert key not in signal
        assert key not in refs
    output = Output()
    bot._write_diagnostic_signal(output, "IM", signal)
    text = "".join(output.text)
    assert "参考计算值（未确认；不作为正式目标或执行安排）" in text
    assert "季度执行日期参考（仍需日历核验）" in text
    exported = json.loads(output.files[0]["contents"])
    assert "IM2703" not in json.dumps(exported)
    assert "forbidden" not in json.dumps(exported)


def test_missing_diagnostic_inputs_do_not_invent_reference_results():
    signal = bot.make_diagnostic_signal("IM", seed.CLOCK, "missing everything", source_signal={})
    assert "unconfirmed_references" not in signal["diagnostic_model"]


def test_nonfinite_or_nested_reference_input_is_not_published():
    signal = bot.make_diagnostic_signal("IM", seed.CLOCK, "invalid", source_signal={
        "momentum_next_weight": np.nan,
        "grid_target": np.inf,
        "core_action": {"v14_profit_pending": True},
    })
    assert "unconfirmed_references" not in signal["diagnostic_model"]


@pytest.mark.parametrize("value,expected,kind", [
    (np.int64(7), 7, int),
    (np.int32(-3), -3, int),
    (np.uint64(9), 9, int),
    (np.bool_(True), True, bool),
    (np.bool_(False), False, bool),
])
def test_diagnostic_json_keeps_numpy_scalar_types(value, expected, kind):
    signal = bot.make_diagnostic_signal("IM", seed.CLOCK, "synthetic")
    signal["diagnostic_model"]["numpy_value"] = value
    output = Output()
    bot._write_diagnostic_signal(output, "IM", signal)
    actual = json.loads(output.files[0]["contents"])["diagnostic_model"]["numpy_value"]
    assert actual == expected
    assert type(actual) is kind


def test_public_snapshot_waits_for_projection_and_reads_original_anchor(monkeypatch):
    seed.wire(monkeypatch, held=False)
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    ready, release, reader_done = Event(), Event(), Event()
    errors = []
    output = Output()

    def compute(product, *args):
        projected = deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product])
        projected["post_core_contract"] = "IM_UNVERIFIED_PROJECTION"
        bot.LIVE_CONTINUATION_ANCHOR[product] = projected
        ready.set()
        if not release.wait(5):
            raise AssertionError("release timeout")
        return {"v14_action": "HOLD"}

    @contextmanager
    def capture():
        yield output

    monkeypatch.setattr(bot, "_build_live_trade_signal", compute)
    monkeypatch.setattr(bot, "_sm", capture)
    monkeypatch.setattr(bot.poe, "query", SimpleNamespace(text="查询IM"), raising=False)
    monkeypatch.setattr(bot, "_now_beijing", lambda: datetime(2026, 10, 21, 14, tzinfo=bot.BEIJING))

    def build():
        try:
            bot.build_live_trade_signal("IM", seed.CLOCK, "close")
        except BaseException as exc:
            errors.append(exc)

    def read():
        try:
            bot.ICIMMainlinesBot().run()
        except BaseException as exc:
            errors.append(exc)
        finally:
            reader_done.set()

    builder = Thread(target=build)
    reader = Thread(target=read)
    builder.start()
    try:
        assert ready.wait(3)
        reader.start()
        assert not reader_done.wait(.15), "snapshot read escaped the builder's lock"
    finally:
        release.set()
        builder.join(3)
        if reader.ident is not None:
            reader.join(3)
    assert not errors and not builder.is_alive() and not reader.is_alive()
    text = "".join(output.text)
    assert "最后逐腿核验快照" in text
    assert original["IM"]["post_core_contract"] in text
    assert "IM_UNVERIFIED_PROJECTION" not in text
    assert bot.LIVE_CONTINUATION_ANCHOR == original


def test_snapshot_renders_after_releasing_its_own_read_lock(monkeypatch):
    seed.wire(monkeypatch, held=False)
    original = deepcopy(bot.LIVE_CONTINUATION_ANCHOR["IM"])

    class CheckOutput(Output):
        def write(self, value):
            # Direct helper call has no coordinator's enclosing lock.
            assert not bot.STRATEGY_QUERY_LOCK._is_owned()
            bot.LIVE_CONTINUATION_ANCHOR["IM"]["post_core_contract"] = "changed_after_copy"
            super().write(value)

    output = CheckOutput()
    bot._write_last_verified_snapshot(output, "IM")
    text = "".join(output.text)
    assert original["post_core_contract"] in text and "changed_after_copy" not in text
