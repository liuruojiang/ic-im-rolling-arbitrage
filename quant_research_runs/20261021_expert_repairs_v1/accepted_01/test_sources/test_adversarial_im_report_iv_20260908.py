"""Fault injection against deployed IV/report functions; no network or state writes.

Failures are audit findings, not backtest observations.
"""
from datetime import date, datetime
import math

import pandas as pd
import pytest

import im_put_policy as policy
import poe_ic_im_mainline_v1_3_bot as bot

DAY = date(2026, 9, 8)
CLOCK = datetime(2026, 9, 8, 14, 30, tzinfo=bot.BEIJING)


@pytest.mark.parametrize("iv,level", [(.399, "normal"), (.4, "normal"), (.401, "high"), (.5, "high"), (.501, "critical"), (math.nan, "unavailable")])
def test_direct_iv_boundaries(iv, level):
    assert policy.iv_warning(iv, source="fault-injection", market_date=DAY, snapshot_time="14:30:00")["level"] == level


def test_ic_hold_nan_option_quote_must_not_be_normal(monkeypatch):
    contract = "510500P2612M07500"
    # Real loader shape and actual HOLD validator; replace only transport/rate lookup.
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a: {
        "date": "20260908", "time": "143000",
        "list": [[contract, None, 0, .25, 7.5]],
    })
    chain, stamp = bot.fetch_sse_510500_chain("2612")
    row = bot._quote_row(chain, contract)
    bot._require_existing_leg_quote("IC Put", contract, row, "HOLD")
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    signal = dict(market_date=DAY, put_current_contract=contract,
                  iv_monitor_option_price=float(row["last"]), etf_price=7.5,
                  sse_time=f"{stamp['date']} {stamp['time']}")
    result = bot.build_iv_warning("IC", signal, {}, None, CLOCK)
    assert result["level"] == "unavailable", result


@pytest.mark.parametrize("quote_time", ["09:31:00", "15:01:00"])
def test_im_monitor_must_not_claim_unqualified_current_iv_without_time_alignment(monkeypatch, quote_time):
    expiry = date(2026, 12, 18)
    spot = 8000.
    strike = 7600.
    price = bot._bs_price_delta("P", spot, strike, .02, bot.FROZEN["IM"]["dividend"], .51, (expiry-DAY).days/365.)[0]
    # Exercise the production frame validator too: complete listed structure,
    # valid current source dates, but no validation of row source_time.
    rows = [dict(instrument=f"{month}-{kind}-{k}", lastprice=price,
                 volume=0, position=0, source_date=DAY, source_time=quote_time)
            for month in sorted(bot._expected_mo_months(DAY))
            for kind in ("P", "C") for k in (7400, 7600, 7800)]
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda *a: date(2026, 9, 7))
    quotes = bot._validate_quote_frame(pd.DataFrame(rows), "MO", CLOCK, "explicit fault injection")
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    result = bot.build_iv_warning("IM", dict(market_date=DAY, put_reference_price=8000.),
                                 dict(price=spot, live_price_source_date=DAY, live_price_source_time="14:30:00"), quotes, CLOCK)
    assert result["level"] == "unavailable", result


@pytest.mark.parametrize("parameter", range(6))
@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf, None])
def test_iv_inversion_rejects_each_nonfinite_input(parameter, invalid):
    inputs = [300., 8000., 7600., .02, .015, .25]
    inputs[parameter] = invalid
    assert bot._implied_volatility("P", *inputs) is None


@pytest.mark.parametrize("quote_time,expected", [("14:15:00", "critical"), ("14:10:00", "critical"), ("14:09:59", "unavailable"), ("14:30:01", "unavailable"), (None, "unavailable")])
def test_im_monitor_delayed_quotes_have_explicit_twenty_minute_boundary(monkeypatch, quote_time, expected):
    quotes = pd.DataFrame([dict(instrument="MO2612-P-7600", lastprice=500., volume=0, position=0, source_time=quote_time)])
    quotes.attrs.update(source="test", source_date=DAY)
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    monkeypatch.setattr(bot, "_implied_volatility", lambda *a: .51)
    signal = dict(market_date=DAY, put_reference_price=8000.)
    original = signal.copy()
    result = bot.build_iv_warning("IM", signal, dict(price=8000., live_price_source_date=DAY, live_price_source_time="14:30:00"), quotes, CLOCK)
    assert result["level"] == expected, result
    assert result["position_effect"] == "none"
    assert signal == original


@pytest.mark.parametrize("run_day", [DAY, date(2026, 9, 9)])
def test_im_confirmed_close_uses_session_time_not_evening_or_next_day_runtime(monkeypatch, run_day):
    quotes = pd.DataFrame([dict(instrument="MO2612-P-7600", lastprice=500., volume=0, position=0, source_time="15:15:00")])
    quotes.attrs.update(source="historical close", source_date=DAY)
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    monkeypatch.setattr(bot, "_implied_volatility", lambda *a: .51)
    result = bot.build_iv_warning("IM", dict(market_date=DAY, put_reference_price=8000., close_confirmed=True),
                                 dict(price=8000., live_price_source_date=DAY, live_price_source_time="15:00:00"), quotes,
                                 datetime(run_day.year, run_day.month, run_day.day, 18, tzinfo=bot.BEIJING))
    assert result["level"] == "critical", result


@pytest.mark.parametrize("stamp", ["20260908 143000", "2026-09-08 143000", "2026-09-08 14:30:00"])
def test_ic_accepts_real_sse_timestamp_formats(monkeypatch, stamp):
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    signal = dict(market_date=DAY, put_current_contract="510500P2612M07500", iv_monitor_option_price=.5,
                  etf_price=7.5, etf_quote_date="20260908", etf_quote_time="143000", sse_time=stamp)
    result = bot.build_iv_warning("IC", signal, {}, None, CLOCK)
    assert result["iv"] is not None, result


def test_ic_option_and_etf_must_both_be_fresh(monkeypatch):
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    signal = dict(market_date=DAY, put_current_contract="510500P2612M07500", iv_monitor_option_price=.5,
                  etf_price=7.5, etf_quote_date="20260908", etf_quote_time="093100", sse_time="20260908 143000")
    assert bot.build_iv_warning("IC", signal, {}, None, CLOCK)["level"] == "unavailable"


def test_real_collection_uses_completed_monotonic_time_without_moving_strategy_clock(monkeypatch):
    started = datetime(2026, 9, 8, 14, 36, 28, tzinfo=bot.BEIJING)
    tick = [100.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: tick[0])
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    signal = dict(market_date=DAY, put_current_contract="510500P2612M07500", iv_monitor_option_price=.5,
                  etf_price=7.5, etf_quote_date="20260908", etf_quote_time="143643", sse_time="20260908 143643")
    with bot.collection_clock(started), bot.runtime_clock(started):
        tick[0] = 115.
        # Nested per-query scope must retain the entire entrypoint timer.
        with bot.collection_clock(started):
            result = bot.build_iv_warning("IC", signal, {}, None, started)
        assert result["iv"] is not None, result
        assert result["validation_clock"] == "2026-09-08T14:36:43+08:00"
        assert bot._now_beijing() == started


def test_fixed_replay_clock_does_not_advance_with_wall_time(monkeypatch):
    tick = [100.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: tick[0])
    with bot.collection_clock(CLOCK), bot.historical_replay(DAY):
        tick[0] = 4000.
        assert bot._iv_observation_clock(CLOCK) == CLOCK


def test_collection_elapsed_does_not_accept_actual_future_quote(monkeypatch):
    tick = [100.]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: tick[0])
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    signal = dict(market_date=DAY, put_current_contract="510500P2612M07500", iv_monitor_option_price=.5,
                  etf_price=7.5, etf_quote_date="20260908", etf_quote_time="150100", sse_time="20260908 150100")
    with bot.collection_clock(CLOCK):
        tick[0] = 115.
        result = bot.build_iv_warning("IC", signal, {}, None, CLOCK)
    assert result["level"] == "unavailable"
    assert "晚于当前" in result["text"]


def test_intraday_collection_crossing_close_is_unavailable_without_retiming_positions():
    with pytest.raises(ValueError, match="越过信号盘中时段"):
        bot._validate_iv_monitor_times(DAY, datetime(2026,9,8,15,tzinfo=bot.BEIJING),
                                      datetime(2026,9,8,15,tzinfo=bot.BEIJING),
                                      datetime(2026,9,8,15,1,tzinfo=bot.BEIJING),False)
