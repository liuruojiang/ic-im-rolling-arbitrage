"""Tencent index OHLCV pagination stays same-source and fails closed."""

import hashlib
import json
from datetime import date, datetime

import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot


HISTORY_START = date(2019, 1, 2)
EXPECTED_DAY = date(2023, 7, 3)
CLOCK = datetime(2023, 7, 3, 16, 0, tzinfo=bot.BEIJING)


def _row(day: str, *, close: str = "100.01", volume: str = "12345.67"):
    return [day, "99.99", close, "101.00", "98.00", volume]


def _payload(rows, *, code=0, msg=""):
    return {"code": code, "msg": msg, "data": {"sh000905": {"day": rows}}}


class _Response:
    def __init__(self, payload=None, *, raw=None, chunks=None, status_code=200, headers=None):
        self.payload = payload
        self.content = (
            bytes(raw)
            if raw is not None
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        self._chunks = list(chunks) if chunks is not None else [self.content]
        self.status_code = status_code
        self.headers = {"Content-Length": str(len(self.content))}
        if headers:
            self.headers.update(headers)
        self.url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        self.closed = False
        self.iterated = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, *, chunk_size):
        assert chunk_size == 64 * 1024
        self.iterated = True
        yield from self._chunks

    def close(self):
        self.closed = True


def _configure(monkeypatch, pages):
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)
    calls = []

    def get(url, *, params, timeout, headers, stream=False):
        response = _Response(pages[len(calls)])
        calls.append(
            {
                "url": url,
                "params": params,
                "timeout": timeout,
                "headers": headers,
                "stream": stream,
                "response": response,
            }
        )
        return response

    monkeypatch.setattr(bot.requests, "get", get)
    validated = []

    def validate(product, frame, clock):
        validated.append((product, frame.copy(), clock))
        return frame

    monkeypatch.setattr(bot, "_validate_v13_ohlcv", validate)
    return calls, validated


def test_newfqkline_pages_merge_and_apply_fixed_volume_scale(monkeypatch):
    page1 = _payload([_row("2019-01-02"), _row("2021-12-31", close="101.01")])
    page2 = _payload(
        [
            _row("2021-12-31", close="101.01"),
            _row("2023-07-03", close="102.01"),
        ]
    )
    calls, validated = _configure(monkeypatch, [page1, page2])

    result = bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert len(calls) == 2
    assert calls[0]["params"]["param"] == (
        "sh000905,day,2019-01-02,2022-01-31,1000,qfq"
    )
    assert calls[1]["params"]["param"] == (
        "sh000905,day,2022-01-01,2023-07-03,1000,qfq"
    )
    assert all(call["timeout"] == bot.TENCENT_OHLCV_TIMEOUT_SECONDS for call in calls)
    assert all(call["stream"] is True for call in calls)
    assert all(call["response"].closed for call in calls)
    assert list(result.index.strftime("%Y-%m-%d")) == [
        "2019-01-02",
        "2021-12-31",
        "2023-07-03",
    ]
    assert result.loc["2019-01-02", "open"] == 99.99
    assert result.loc["2019-01-02", "volume"] == pytest.approx(1_234_567.0)
    assert result.attrs["source_volume_scale"] == 100.0
    assert result.attrs["source"] == "Tencent"
    assert result.attrs["source_page_manifest"][0]["response_sha256"] == hashlib.sha256(
        _Response(page1).content
    ).hexdigest()
    assert len(result.attrs["source_page_manifest"]) == 2
    assert len(validated) == 1
    assert validated[0][0] == "IC"
    assert validated[0][2] == CLOCK
    assert validated[0][1].loc["2023-07-03", "volume"] == pytest.approx(1_234_567.0)


def test_newfqkline_overlapping_page_conflict_fails_closed(monkeypatch):
    page1 = _payload([_row("2019-01-02"), _row("2021-12-31", close="101.01")])
    page2 = _payload([_row("2021-12-31", close="101.02"), _row("2023-07-03")])
    calls, _validated = _configure(monkeypatch, [page1, page2])

    with pytest.raises(RuntimeError, match="分页重叠行冲突.*2021-12-31"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert len(calls) == 2


def test_newfqkline_param_error_is_not_mislabeled_as_short_history(monkeypatch):
    calls, _validated = _configure(
        monkeypatch,
        [_payload([], code=0, msg="param error")],
    )

    with pytest.raises(RuntimeError, match="API拒绝请求.*param error") as exc_info:
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert "不足180行" not in str(exc_info.value)
    assert len(calls) == 1
    assert calls[0]["response"].closed


def test_newfqkline_empty_page_fails_closed(monkeypatch):
    calls, _validated = _configure(monkeypatch, [_payload([])])

    with pytest.raises(RuntimeError, match="分页无日线数据"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert len(calls) == 1


def test_newfqkline_future_date_fails_closed(monkeypatch):
    calls, _validated = _configure(
        monkeypatch,
        [_payload([_row("2019-01-02"), _row("2023-07-04")])],
    )

    with pytest.raises(RuntimeError, match="含未来日期"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert len(calls) == 1
    assert calls[0]["response"].closed


def test_fetch_ohlcv_history_uses_paged_tencent_after_other_sources_fail(monkeypatch):
    pages = [
        _payload([_row("2019-01-02"), _row("2021-12-31", close="101.01")]),
        _payload(
            [
                _row("2021-12-31", close="101.01"),
                _row("2023-07-03", close="102.01"),
            ]
        ),
    ]
    _calls, validated = _configure(monkeypatch, pages)
    tencent_calls = []

    class SinaResponse:
        status_code = 200
        content = b"[]"

        def raise_for_status(self):
            return None

        def json(self):
            return []

    def get(url, *, params=None, timeout, headers, stream=False):
        if "money.finance.sina.com.cn" in url:
            return SinaResponse()
        tencent_calls.append(params)
        return _Response(pages[len(tencent_calls) - 1])

    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(
        bot,
        "_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Eastmoney fixture unavailable")
        ),
    )
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK)

    result = bot.fetch_ohlcv_history("IC")

    assert result.attrs["source"] == "Tencent"
    assert result.attrs["source_volume_scale"] == 100.0
    assert result.attrs["source_failures"] == [
        "Sina: RuntimeError: Sina OHLCV不足180行",
        "Eastmoney: RuntimeError: Eastmoney fixture unavailable",
    ]
    assert len(result.attrs["source_page_manifest"]) == 2
    assert len(tencent_calls) == 2
    assert len(validated) == 1


def test_newfqkline_respects_shared_timeout_budget_and_does_not_retry(monkeypatch):
    calls = []

    def budget_exhausted(_cap):
        raise RuntimeError("联网请求超过总时间预算，已停止继续取数")

    def should_not_request(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("network request started after shared budget exhaustion")

    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)
    monkeypatch.setattr(bot, "_bounded_timeout", budget_exhausted)
    monkeypatch.setattr(bot.requests, "get", should_not_request)

    with pytest.raises(RuntimeError, match="超过总时间预算"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)
    assert calls == []

    calls.clear()
    monkeypatch.setattr(bot, "_bounded_timeout", lambda _cap: 8.0)

    def timeout_once(*_args, **_kwargs):
        calls.append(True)
        raise requests.Timeout("fixture timeout")

    monkeypatch.setattr(bot.requests, "get", timeout_once)
    with pytest.raises(requests.Timeout, match="fixture timeout"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)
    assert calls == [True]


def test_newfqkline_slow_stream_stops_when_shared_budget_expires(monkeypatch):
    response = _Response(
        raw=b'{"code":0,"msg":"","data":{"sh000905":{"day":[]}}}',
        chunks=[b"{", b"\"code\":0}"],
    )
    request_calls = []
    checks = []

    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)

    def budget(cap):
        checks.append(cap)
        # Request start, response receipt, first pre-read all pass; the
        # post-read check sees that the streamed response exceeded the budget.
        if len(checks) == 4:
            raise RuntimeError("联网请求超过总时间预算，已停止继续取数")
        return min(cap, 8.0)

    def get(*_args, **_kwargs):
        request_calls.append(True)
        return response

    monkeypatch.setattr(bot, "_bounded_timeout", budget)
    monkeypatch.setattr(bot.requests, "get", get)

    with pytest.raises(RuntimeError, match="超过总时间预算"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert request_calls == [True]
    assert checks == [bot.TENCENT_OHLCV_TIMEOUT_SECONDS] * 4
    assert response.closed


def test_newfqkline_declared_oversize_response_closes_without_streaming(monkeypatch):
    calls, _validated = _configure(monkeypatch, [_payload([_row("2019-01-02")])])
    response = calls[0]["response"] if calls else None
    # Replace the fixture response with an oversized declared response.
    response = _Response(
        raw=b"{}",
        headers={"Content-Length": str(bot.MAX_QUOTE_RESPONSE_BYTES + 1)},
    )
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="声明大小超过"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert response.closed
    assert response.iterated is False


def test_newfqkline_stream_size_cap_allows_decoded_body_over_compressed_length(monkeypatch):
    payload = _payload([_row("2019-01-02")])
    response = _Response(payload, headers={"Content-Length": "8", "Content-Encoding": "gzip"})
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(bot, "_validate_v13_ohlcv", lambda _product, frame, _clock: frame)

    result = bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert result.attrs["source_page_manifest"][0]["response_bytes"] == len(response.content)
    assert result.attrs["source_page_manifest"][0]["response_bytes"] > 8
    assert response.closed


def test_newfqkline_cumulative_stream_size_is_bounded_and_closes(monkeypatch):
    monkeypatch.setattr(bot, "MAX_QUOTE_RESPONSE_BYTES", 16)
    response = _Response(
        raw=b"unused",
        chunks=[b"a" * 10, b"b" * 7],
        headers={"Content-Length": ""},
    )
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="累计大小超过16字节"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert response.closed


def test_newfqkline_http_error_closes_response(monkeypatch):
    response = _Response(_payload([]), status_code=503)
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: response)

    with pytest.raises(RuntimeError, match="Tencent分页HTTP失败"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert response.closed
    assert response.iterated is False


def test_newfqkline_chunked_encoding_error_normalizes_for_single_outer_retry(monkeypatch):
    class BrokenStream(_Response):
        def iter_content(self, *, chunk_size):
            self.iterated = True
            yield b'{"code":'
            raise requests.exceptions.ChunkedEncodingError("broken stream")

    response = BrokenStream(_payload([_row("2019-01-02")]))
    calls = []
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)
    monkeypatch.setattr(bot.requests, "get", lambda *_args, **_kwargs: calls.append(True) or response)

    with pytest.raises(requests.ConnectionError, match="流连接中断"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)

    assert calls == [True]
    assert response.closed


def test_chunked_stream_uses_exactly_one_existing_outer_product_retry(monkeypatch):
    class BrokenStream(_Response):
        def iter_content(self, *, chunk_size):
            self.iterated = True
            yield b'{"code":'
            raise requests.exceptions.ChunkedEncodingError("broken stream")

    responses = [
        BrokenStream(_payload([_row("2019-01-02")])),
        _Response(_payload([_row("2019-01-02")])),
    ]
    get_calls = []
    build_calls = []
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: HISTORY_START)
    monkeypatch.setattr(bot, "_validate_v13_ohlcv", lambda _product, frame, _clock: frame)

    def get(*_args, **_kwargs):
        response = responses[len(get_calls)]
        get_calls.append(response)
        return response

    def build(product, mode):
        build_calls.append((product, mode))
        return {"source": bot._fetch_tencent_ohlcv_history(product, CLOCK).attrs["source"]}

    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "build_live_trade_signal", build)

    result = bot.build_live_signal_with_transport_retry("IC", "close", 5.0)

    assert result["source"] == "Tencent"
    assert len(build_calls) == 2
    assert build_calls == [("IC", "close"), ("IC", "close")]
    assert len(get_calls) == 2
    assert all(response.closed for response in get_calls)
    assert result["data_notes"] == [
        "本品种首次连接中断/超时后在剩余预算内重试一次成功；未重复其他品种"
    ]


def test_newfqkline_http_and_api_failures_do_not_retry_or_leave_response_open(monkeypatch):
    responses = [
        _Response(_payload([], code=0, msg="param error")),
        _Response(_payload([]), status_code=503),
    ]
    calls = []
    monkeypatch.setitem(
        bot.V13_HISTORY_DATE_INDEX,
        "IC",
        (HISTORY_START, 2, "fixture-history-hash"),
    )
    monkeypatch.setattr(bot, "_latest_completed_exchange_day", lambda _clock: EXPECTED_DAY)

    def get(*_args, **_kwargs):
        response = responses[len(calls)]
        calls.append(response)
        return response

    monkeypatch.setattr(bot.requests, "get", get)
    with pytest.raises(RuntimeError, match="API拒绝请求"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)
    assert len(calls) == 1
    assert calls[0].closed

    with pytest.raises(RuntimeError, match="HTTP失败"):
        bot._fetch_tencent_ohlcv_history("IC", CLOCK)
    assert len(calls) == 2
    assert calls[1].closed
