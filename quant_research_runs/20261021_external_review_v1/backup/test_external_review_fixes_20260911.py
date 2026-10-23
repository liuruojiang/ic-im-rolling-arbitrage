"""Boundary regressions; synthetic inputs below are diagnostic, not market evidence."""
from copy import deepcopy
from datetime import date, datetime

import pandas as pd
import pytest
import requests
import json
from pathlib import Path

import poe_ic_im_mainline_v1_3_bot as bot
import poe_ic_im_v1_3_state as state


def clock(hour, minute=0):
    return datetime(2026, 9, 11, hour, minute, tzinfo=bot.BEIJING)


@pytest.mark.parametrize("delta", [0.36, 0.32, 0.25])
def test_resize_keeps_verified_current_quantity(monkeypatch, delta):
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    anchors["IC"].update(post_put_qty=14, verified_core_put_qty=10,
                         verified_momentum_put_qty=4)
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", anchors)
    monkeypatch.setattr(bot, "_implied_volatility", lambda *a: .3)
    monkeypatch.setattr(bot, "_bs_price_delta", lambda *a: (1., -delta))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    result = bot._size_existing_ic_put("510500P2612M07500", pd.Series({"last": .1}),
                                       date(2026,9,11), 7., 7000., 14, .25, .1, .5, .1)
    assert (result["put_current_total_qty"], result["put_current_core_qty"],
            result["put_current_momentum_qty"]) == (14, 10, 4)


def test_ambiguous_legacy_split_is_not_guessed():
    anchor = dict(post_put_qty=14, verified_core_put_delta=.25, verified_momentum_put_delta=.1)
    with pytest.raises(RuntimeError, match="禁止.*猜算"):
        bot._ic_current_quantity_breakdown(anchor)


def test_hash_verified_signal_restores_exact_split_without_mutation():
    record = state.bootstrap_record()
    # Anchor-only deterministic recovery is possible for the bootstrap's single sleeve.
    before = deepcopy(record)
    anchors = state.anchors_from_record(record)
    assert bot._ic_current_quantity_breakdown(anchors["IC"])["total"] == 14
    assert record == before


def test_real_local_ledger_restores_recorded_quantities():
    path = Path(__file__).parent / "runtime/ic_im_v1_3_r7/latest.json"
    if not path.exists():
        path = Path(__file__).parent / "tests/fixtures/icim_adversarial/confirmed.json"
    raw = path.read_bytes()
    record = json.loads(raw)
    anchors = state.anchors_from_record(record)
    split = bot._ic_current_quantity_breakdown(anchors["IC"])
    signal = record["signals"]["IC"]
    assert split["core"] == signal["put_target_core_qty"]
    assert split["momentum"] == signal["put_target_momentum_qty"]
    assert path.read_bytes() == raw


def test_real_completed_index_history_is_not_overwritten(monkeypatch):
    import ic_mainline_v1_3 as ic
    frame = pd.read_csv(ic.CSI500_OHLCV_PATH, parse_dates=["date"]).set_index("date")
    end = bot.DATA_CUTOFF
    frame = frame.loc[:str(end)]
    expected = float(frame["close"].iloc[-1])
    monkeypatch.setattr(bot, "fetch_ohlcv_history", lambda *a: frame.copy())
    monkeypatch.setattr(bot, "fetch_live_price_quote", lambda *a: pytest.fail("completed close must not fetch latest quote"))
    result = bot.live_proxy("IC", datetime(end.year,end.month,end.day,16,tzinfo=bot.BEIJING))
    assert result["price"] == expected
    assert result["history_date"] == end


@pytest.mark.parametrize("data_error", [False, True])
def test_ohlcv_aggregate_preserves_transport_but_not_validation(monkeypatch, data_error):
    def request(*a, **kw):
        raise requests.Timeout("offline")
    def fallback(*a, **kw):
        if data_error:
            raise ValueError("invalid data")
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(bot.requests, "get", request)
    monkeypatch.setattr(bot, "_request_json", fallback)
    with pytest.raises(RuntimeError) as exc:
        bot.fetch_ohlcv_history("IC")
    assert isinstance(exc.value, requests.ConnectionError) is not data_error


@pytest.mark.parametrize("hour,minute", [(9,0),(9,25),(10,0),(12,30),(14,59)])
def test_unclosed_query_stops_before_fetch(monkeypatch, hour, minute):
    monkeypatch.setattr(bot, "live_proxy", lambda *a: pytest.fail("must not fetch"))
    with pytest.raises(RuntimeError, match="尚未收盘"):
        bot.build_live_trade_signal("IC", clock(hour,minute), "close")


def test_clock_is_scoped_for_full_build(monkeypatch):
    original = bot._RUNTIME_CLOCK.get()
    monkeypatch.setattr(bot, "_build_live_trade_signal", lambda *a: bot._now_beijing())
    assert bot.build_live_trade_signal("IC", clock(16), "close") == clock(16)
    assert bot._RUNTIME_CLOCK.get() is original


def test_confirmed_previous_close_keeps_expiry_preview():
    assert bot._is_pre_expiry_close(date(2026,9,17), date(2026,9,18), True)
    assert not bot._is_pre_expiry_close(date(2026,9,17), date(2026,9,18), False)


def test_same_day_and_next_session_keep_distinct_execution_state():
    a = bot.LIVE_CONTINUATION_ANCHOR["IC"]
    same = bot._apply_next_unverified_session_anchor("IC", {}, next_session=False)
    next_day = bot._apply_next_unverified_session_anchor("IC", {})
    assert same["momentum_current_weight"] == a["verified_momentum_weight"]
    assert next_day["momentum_current_weight"] == a["verified_next_momentum_weight"]
    assert same["v13_current_core_put_delta"] == a["verified_core_put_delta"]


def test_retry_reuses_deadline(monkeypatch):
    ticks = [100.]
    deadlines = []
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])
    def build(*a, **kw):
        deadlines.append(bot._NETWORK_DEADLINE.get())
        if len(deadlines) == 1:
            ticks[0] += 30
            raise requests.Timeout("transport only")
        return {}
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    bot.build_live_signal_with_transport_retry("IC", "close", 45)
    assert deadlines == [145., 145.]


def test_expired_budget_does_not_retry(monkeypatch):
    ticks, calls = [100.], []
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: ticks[0])
    def build(*a, **kw):
        calls.append(1)
        ticks[0] = 146.
        raise requests.Timeout("transport only")
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(RuntimeError, match="总时间预算"):
        bot.build_live_signal_with_transport_retry("IC", "close", 45)
    assert len(calls) == 1


@pytest.mark.parametrize("hour,minute,valid", [(12,30,True),(13,19,True),(13,21,False)])
def test_lunch_is_not_active_quote_age(hour, minute, valid):
    args = (date(2026,9,11), clock(11,30), clock(11,30), clock(hour,minute), False)
    if valid:
        bot._validate_iv_monitor_times(*args)
    else:
        with pytest.raises(ValueError, match="20分钟"):
            bot._validate_iv_monitor_times(*args)
