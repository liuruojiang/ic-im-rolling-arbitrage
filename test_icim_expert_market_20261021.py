"""Offline adversarial market-boundary checks; injected daily prices are synthetic."""
from datetime import date, datetime
import json

import numpy as np
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot


DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 14, tzinfo=bot.BEIJING)
SID = "10012099"


class Response:
    def __init__(self, text="", *, content=None, declared=None, error=None):
        self.content = text.encode("utf-8") if content is None else content
        self.headers = {} if declared is None else {"Content-Length": str(declared)}
        self.error, self.closed, self.text_reads = error, False, 0

    @property
    def text(self):
        self.text_reads += 1
        return self.content.decode("utf-8")

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def iter_content(self, chunk_size=65536):
        yield self.content

    def close(self):
        self.closed = True


def payload_for(kind, stamp):
    if kind == "quote":
        return {"date": "20261021", "time": stamp,
                "list": [["510500", "ETF", 7., 0, 0, 0, 10, 10, 6.9]]}
    return {"date": "20261021", "time": stamp, "total": 1, "begin": 0, "end": 1,
            "list": [["510500P2612M07000", .2, 0, .2, 7., SID]]}


def fetch_stamp(kind):
    if kind == "quote":
        return bot.fetch_sse_510500_quote()["time"]
    return bot.fetch_sse_510500_chain("202612")[1]["time"]


@pytest.mark.parametrize("kind", ["quote", "chain"])
@pytest.mark.parametrize("bad", [None, "", "   ", True, np.bool_(True), "000000", "091459", "24:00:00", 93000.0])
def test_sse_missing_or_non_session_time_is_not_invented(monkeypatch, kind, bad):
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a: payload_for(kind, bad))
    with pytest.raises(RuntimeError, match="时间"):
        fetch_stamp(kind)


@pytest.mark.parametrize("kind", ["quote", "chain"])
@pytest.mark.parametrize("value,expected", [(91500, "091500"), ("93000", "093000"), ("150000", "150000")])
def test_sse_supplied_valid_time_can_be_zero_padded(monkeypatch, kind, value, expected):
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a: payload_for(kind, value))
    assert fetch_stamp(kind) == expected


@pytest.mark.parametrize("stamp", ["000000", "090000", "091459"])
def test_general_stamp_gate_rejects_pre_session_time(stamp):
    with pytest.raises(RuntimeError, match="09:15"):
        bot._validate_chain_stamp_matches("injected", {"date": "20261021", "time": stamp}, DAY, CLOCK)


def test_general_stamp_gate_keeps_future_timestamp_rejection():
    with pytest.raises(RuntimeError, match="晚于"):
        bot._validate_chain_stamp_matches("injected", {"date": "20261021", "time": "150000"}, DAY, CLOCK)


def wire_open(monkeypatch, rows):
    response = Response("callback(" + json.dumps(rows) + ");")
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(bot, "_request_json", lambda *a: {"data": {"code": SID, "market": 10, "klines": []}})
    return response


@pytest.mark.parametrize("source_day", ["2026-10-21", "20261021", 20261021])
def test_dated_open_uses_the_same_exact_date_parser_as_closes(monkeypatch, source_day):
    response = wire_open(monkeypatch, [{"d": source_day, "o": ".23", "c": ".9"}])
    assert bot.fetch_option_dated_open(SID, DAY) == (.23, "Sina:dated_open")
    assert response.closed


@pytest.mark.parametrize("bad", [True, False, "NaN", "Infinity", 0, -1])
def test_invalid_open_does_not_turn_into_execution_evidence(monkeypatch, bad):
    response = wire_open(monkeypatch, [{"d": "20261021", "o": bad, "c": ".9"}])
    with pytest.raises(RuntimeError, match="开盘价不可核验"):
        bot.fetch_option_dated_open(SID, DAY)
    assert response.closed


def test_normalized_duplicate_opening_days_cannot_hide_conflicting_marks(monkeypatch):
    wire_open(monkeypatch, [{"d": "2026-10-21", "o": .2}, {"d": 20261021, "o": .3}])
    with pytest.raises(RuntimeError, match="相互冲突"):
        bot.fetch_option_dated_open(SID, DAY)


@pytest.mark.parametrize("declared", [None, 101])
def test_sina_closes_size_bound_precedes_text_parsing_and_closes_response(monkeypatch, declared):
    response = Response(content=b"x" * 101, declared=declared)
    monkeypatch.setattr(bot, "MAX_QUOTE_RESPONSE_BYTES", 100)
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with pytest.raises(RuntimeError, match="超过"):
        bot.fetch_sina_option_closes(SID)
    assert response.closed and response.text_reads == 0


def test_bounded_sina_closes_preserve_valid_daily_marks(monkeypatch):
    response = Response('callback([{"d":"20261020","c":"0.2"},{"d":"2026-10-21","c":"0.3"}]);')
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    actual = bot.fetch_sina_option_closes(SID)
    assert list(actual) == [.2, .3]
    assert response.closed


@pytest.mark.parametrize("error", [requests.Timeout("injected timeout"), requests.ConnectionError("injected reset"),
                                   requests.exceptions.ChunkedEncodingError("injected truncated body")])
def test_dated_master_request_failure_keeps_transport_classification(monkeypatch, error):
    def unavailable(*a, **k):
        raise error
    monkeypatch.setattr(bot.requests, "get", unavailable)
    with pytest.raises(bot.SignalTransportError, match="逐日身份来源不可用") as caught:
        bot.fetch_sse_510500_dated_master(DAY)
    assert caught.value.__cause__ is error


def test_dated_master_body_transport_failure_remains_retryable_and_is_closed(monkeypatch):
    response = Response()
    error = requests.exceptions.ChunkedEncodingError("injected body disconnect")
    def broken_stream(**kwargs):
        raise error
    response.iter_content = broken_stream
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with pytest.raises(bot.SignalTransportError) as caught:
        bot.fetch_sse_510500_dated_master(DAY)
    assert caught.value.__cause__ is error and response.closed


@pytest.mark.parametrize("error", [requests.HTTPError("injected HTTP 400"), ValueError("injected invalid JSON")])
def test_dated_master_validation_failure_is_not_transport_retry(monkeypatch, error):
    response = Response("not JSON", error=error if isinstance(error, requests.HTTPError) else None)
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: response)
    with pytest.raises(RuntimeError) as caught:
        bot.fetch_sse_510500_dated_master(DAY)
    assert not isinstance(caught.value, requests.ConnectionError)
    assert response.closed


@pytest.mark.parametrize("kind", ["chain", "closes", "dated_open"])
@pytest.mark.parametrize("mixed", [False, True])
def test_source_aggregation_retries_only_when_all_failures_are_transport(monkeypatch, kind, mixed):
    first = ValueError("injected invalid data") if mixed else requests.Timeout("injected timeout")
    second = requests.ConnectionError("injected reset")
    def fail(error):
        def fetch(*args, **kwargs):
            raise error
        return fetch
    if kind == "chain":
        monkeypatch.setattr(bot, "fetch_sse_510500_chain", fail(first))
        monkeypatch.setattr(bot, "fetch_sina_510500_chain", fail(second))
        fetch = lambda: bot.fetch_510500_chain_with_failover("202612")
    elif kind == "closes":
        monkeypatch.setattr(bot, "fetch_sina_option_closes", fail(first))
        monkeypatch.setattr(bot, "fetch_eastmoney_option_closes", fail(second))
        fetch = lambda: bot.fetch_option_closes(SID)
    else:
        monkeypatch.setattr(bot.requests, "get", fail(first))
        monkeypatch.setattr(bot, "_request_json", fail(second))
        fetch = lambda: bot.fetch_option_dated_open(SID, DAY)
    with pytest.raises(RuntimeError) as caught:
        fetch()
    assert isinstance(caught.value, bot.SignalTransportError) is (not mixed)


def test_existing_product_retry_recognizes_wrapped_master_transport_failure(monkeypatch):
    calls = []
    def get(*args, **kwargs):
        raise requests.Timeout("injected one-shot timeout")
    def build(product, mode):
        calls.append((product, mode))
        if len(calls) == 1:
            bot.fetch_sse_510500_dated_master(DAY)
        return {"product": product, "data_notes": []}
    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    actual = bot.build_live_signal_with_transport_retry("IC", "close", 10)
    assert calls == [("IC", "close"), ("IC", "close")]
    assert "重试一次成功" in actual["data_notes"][0]
