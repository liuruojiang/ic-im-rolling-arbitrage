"""Offline synthetic quote-time faults through the actual current feed router."""
from datetime import date, datetime
from email.utils import format_datetime

import pandas as pd
import pytest
import requests

import ic_im_quote_snapshot_guard as guard
import poe_ic_im_mainline_v1_4_bot as bot


DAY = date(2026, 10, 12)
CLOSE_CLOCK = datetime(2026, 10, 12, 20, tzinfo=bot.BEIJING)


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-12T{value}+08:00")


def _future_frame(time_value: str, source: str = "新浪财经") -> pd.DataFrame:
    return bot._validate_quote_frame(pd.DataFrame([
        dict(instrument=contract, lastprice=7000.0, volume=10.0, position=20.0,
             source_date=DAY, source_time=time_value)
        for contract in sorted(bot._expected_future_contracts("IM", DAY))
    ]), "IM", CLOSE_CLOCK, source)


class _OfficialResponse:
    encoding = "utf-8"
    status_code = 200

    def __init__(self, time_value: str):
        rows = [dict(instrument=contract, lastprice=7000.0, volume=10.0, position=20.0)
                for contract in sorted(bot._expected_future_contracts("IM", DAY))]
        self.content = pd.DataFrame(rows).to_csv(index=False).encode()
        self.headers = {"Last-Modified": format_datetime(_stamp(time_value))}

    def raise_for_status(self):
        return None


@pytest.mark.parametrize("bad_time,reason", [("09:31:00", "尚未到收盘"), ("23:59:59", "晚于采集")])
def test_official_guard_rejects_invalid_whole_frame_time(bad_time, reason):
    with pytest.raises(RuntimeError, match=reason):
        guard.official_snapshot_evidence(_stamp(bad_time), CLOSE_CLOCK, DAY, "test official")


def test_official_close_snapshot_remains_valid_in_evening():
    evidence = guard.official_snapshot_evidence(_stamp("15:00:00"), CLOSE_CLOCK, DAY, "test official")
    assert evidence["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"
    assert evidence["source_snapshot_kind"] == "http_last_modified_whole_frame"


def test_intraday_guard_keeps_before_close_quote_and_rejects_future():
    clock = _stamp("14:30:00")
    completed = date(2026, 10, 9)
    guard.official_snapshot_evidence(_stamp("09:31:00"), clock, completed, "test official")
    with pytest.raises(RuntimeError, match="晚于采集"):
        guard.official_snapshot_evidence(_stamp("14:30:01"), clock, completed, "test official")


@pytest.mark.parametrize("times", [["09:31:00", "09:32:00"], [None, "09:31:00"], [None, None]])
def test_vendor_all_early_or_unknown_times_cannot_prove_close(times):
    with pytest.raises(RuntimeError, match="收盘"):
        guard.vendor_snapshot_evidence([dict(source_date=DAY, source_time=value) for value in times],
                                       CLOSE_CLOCK, DAY, "test vendor")


def test_vendor_chain_close_does_not_require_every_contract_to_trade_at_close():
    result = guard.vendor_snapshot_evidence([
        dict(source_date=DAY, source_time="09:31:00"),
        dict(source_date=DAY, source_time="15:00:00"),
        dict(source_date=DAY, source_time=None),
    ], CLOSE_CLOCK, DAY, "test vendor")
    assert result["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"
    assert result["source_snapshot_kind"] == "latest_available_contract_update"
    assert result["source_snapshot_missing_times"] == 1


def test_vendor_any_future_update_invalidates_source():
    with pytest.raises(RuntimeError, match="晚于采集"):
        guard.vendor_snapshot_evidence([
            dict(source_date=DAY, source_time="15:00:00"),
            dict(source_date=DAY, source_time="23:59:59"),
        ], CLOSE_CLOCK, DAY, "test vendor")


@pytest.mark.parametrize("value", ["15:00:00", "150000", 150000])
def test_vendor_supported_time_formats(value):
    result = guard.vendor_snapshot_evidence([dict(source_date=DAY, source_time=value)],
                                           CLOSE_CLOCK, DAY, "test vendor")
    assert result["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"


@pytest.mark.parametrize("value", ["15:00", "15000", "25:00:00", "15:99:00", "invalid"])
def test_vendor_malformed_time_is_rejected_even_with_another_good_row(value):
    with pytest.raises(RuntimeError, match="非法行情更新时间"):
        guard.vendor_snapshot_evidence([
            dict(source_date=DAY, source_time="15:00:00"),
            dict(source_date=DAY, source_time=value),
        ], CLOSE_CLOCK, DAY, "test vendor")


def test_vendor_intraday_unknown_time_retains_previous_behavior():
    result = guard.vendor_snapshot_evidence([
        dict(source_date=DAY, source_time=pd.NA),
        dict(source_date=DAY, source_time=None),
    ], _stamp("14:30:00"), date(2026, 10, 9), "test vendor")
    assert result["source_snapshot_at"] is None
    assert result["source_snapshot_missing_times"] == 2


@pytest.mark.parametrize("time_value", ["09:31:00", "23:59:59"])
def test_actual_official_adapter_rejects_whole_frame_before_builder(monkeypatch, time_value):
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: _OfficialResponse(time_value))
    with pytest.raises(RuntimeError, match="行情更新时间"):
        bot._fetch_cffex_official_quotes("IM", CLOSE_CLOCK)


def test_actual_official_adapter_preserves_snapshot_evidence(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: _OfficialResponse("15:00:00"))
    result = bot._fetch_cffex_official_quotes("IM", CLOSE_CLOCK)
    assert result.attrs["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"
    assert result.attrs["source_snapshot_kind"] == "http_last_modified_whole_frame"


@pytest.mark.parametrize("header", [None, "not-a-date"])
def test_actual_official_adapter_requires_valid_last_modified(monkeypatch, header):
    response = _OfficialResponse("15:00:00")
    response.headers = {} if header is None else {"Last-Modified": header}
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)
    with pytest.raises(RuntimeError, match="Last-Modified"):
        bot._fetch_cffex_official_quotes("IM", CLOSE_CLOCK)


def test_actual_official_current_intraday_and_prior_close_are_allowed(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: _OfficialResponse("09:31:00"))
    intraday = bot._fetch_cffex_official_quotes("IM", _stamp("14:30:00"))
    assert intraday.attrs["source_date"] == DAY
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: _OfficialResponse("15:00:00"))
    prior_close = bot._fetch_cffex_official_quotes(
        "IM", datetime(2026, 10, 13, 10, tzinfo=bot.BEIJING))
    assert prior_close.attrs["source_date"] == DAY
    assert prior_close.attrs["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"


def test_actual_vendor_current_intraday_and_prior_close_are_allowed():
    rows = [dict(instrument=contract, lastprice=7000.0, volume=10.0, position=20.0,
                 source_date=DAY, source_time="09:31:00")
            for contract in sorted(bot._expected_future_contracts("IM", DAY))]
    intraday = bot._validate_quote_frame(pd.DataFrame(rows), "IM", _stamp("14:30:00"), "新浪财经")
    assert intraday.attrs["source_date"] == DAY
    for row in rows:
        row["source_time"] = "15:00:00"
    prior_close = bot._validate_quote_frame(pd.DataFrame(rows), "IM",
                                           datetime(2026, 10, 13, 10, tzinfo=bot.BEIJING), "新浪财经")
    assert prior_close.attrs["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"


def test_actual_vendor_future_time_in_invalid_price_row_cannot_disappear():
    rows = [dict(instrument=contract, lastprice=7000.0, volume=10.0, position=20.0,
                 source_date=DAY, source_time="15:00:00")
            for contract in sorted(bot._expected_future_contracts("IM", DAY))]
    rows[-1].update(lastprice=0.0, source_time="23:59:59")
    with pytest.raises(RuntimeError, match="晚于采集"):
        bot._validate_quote_frame(pd.DataFrame(rows), "IM", CLOSE_CLOCK, "新浪财经")


def _unavailable(*args, **kwargs):
    raise requests.ConnectionError("synthetic unavailable fallback")


def test_stale_official_routes_to_fresh_sina_fallback(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: _OfficialResponse("09:31:00"))
    monkeypatch.setattr(bot, "_fetch_sina_future_quotes", lambda *_args: _future_frame("15:00:00"))
    monkeypatch.setattr(bot, "_fetch_eastmoney_future_quotes", _unavailable)
    result = bot.fetch_cffex_quotes("IM", CLOSE_CLOCK)
    assert result.attrs["source"] == "新浪财经"
    assert result.attrs["source_snapshot_at"] == "2026-10-12T15:00:00+08:00"
    assert any("尚未到收盘" in reason for reason in result.attrs["source_failures"])


def test_stale_official_cannot_route_to_stale_vendor_chain(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: _OfficialResponse("09:31:00"))
    monkeypatch.setattr(bot, "_fetch_sina_future_quotes", lambda *_args: _future_frame("09:31:00"))
    monkeypatch.setattr(bot, "_fetch_eastmoney_future_quotes", lambda *_args: _future_frame("09:31:00", "东方财富"))
    with pytest.raises(RuntimeError, match="所有行情源失败"):
        bot.fetch_cffex_quotes("IM", CLOSE_CLOCK)


def test_archive_replay_does_not_invoke_live_snapshot_guard(monkeypatch):
    expected = pd.DataFrame([dict(instrument="IM2612", lastprice=7000.0)])
    monkeypatch.setattr(bot, "_cffex_historical_quote_frame", lambda *_args: expected)
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: pytest.fail("live request during archive replay"))
    monkeypatch.setattr(guard, "official_snapshot_evidence", lambda *_args: pytest.fail("live guard during archive replay"))
    with bot.historical_replay(DAY):
        actual = bot.fetch_cffex_quotes("IM", CLOSE_CLOCK)
    assert actual is expected
