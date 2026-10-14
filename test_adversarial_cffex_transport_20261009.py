"""Offline transport counterexamples; bytes/errors/clocks are synthetic, not quotes.

The real requests.Response decoder and the real v1.4 archive/retry functions
are exercised.  No production files are modified and no network is used.
"""

import contextvars
import io
import zipfile
from datetime import date, datetime
from math import nextafter

import pandas as pd
import pytest
import requests
from urllib3.exceptions import DecodeError, ProtocolError, ReadTimeoutError

import poe_ic_im_mainline_v1_4_bot as bot


def _latest_close_fallback_setup(monkeypatch, *, product="IC", source_day=date(2026,10,14), archive_error=None, count_override=None):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026,10,15,1,40,tzinfo=tz)
    monkeypatch.setattr(bot,"datetime",Clock)
    calls=[]
    error=archive_error or requests.ReadTimeout("synthetic monthly archive timeout")
    def historical(*args):
        calls.append("archive")
        raise error
    monkeypatch.setattr(bot,"_cffex_historical_quote_frame",historical)
    monkeypatch.setattr(bot,"_fetch_cffex_official_quotes",lambda *a: (_ for _ in ()).throw(requests.ReadTimeout("synthetic official endpoint timeout")))
    rows=[dict(instrument=contract,openprice=7000.,highest=7100.,lowest=6900.,lastprice=7000.,
               volume=10.,position=20.,source_date=source_day,source_time="15:00:00")
          for contract in sorted(bot._expected_future_contracts(product,date(2026,10,14)))] if product!="MO" else [
              dict(instrument=f"{month}-{kind}-{strike}",lastprice=800.,volume=10.,position=20.,
                   source_date=source_day,source_time="15:20:00",bprice=790.,sprice=810.)
              for month in sorted(bot._expected_mo_months(date(2026,10,14)))
              for kind in ("C","P") for strike in (7400,7600,7800)]
    if product=="MO":
        next(row for row in rows if row["instrument"]=="MO2612-P-7800")["volume"]=0.
        rows.append(dict(instrument="MO2612-P-8200",lastprice=900.,volume=0.,position=20.,
                         source_date=source_day,source_time="15:20:00",bprice=0.,sprice=0.))
    frame=pd.DataFrame(rows)
    if count_override is not None:
        field, value = count_override
        target = frame.index[frame.instrument.eq("MO2612-P-7600")][0] if product == "MO" else frame.index[0]
        frame.loc[target, field] = value
    frame.attrs.update(source="新浪财经" if product!="MO" else "东方财富",source_date=source_day,source_failures=[],source_audit=[])
    def vendor(*args):
        calls.append("vendor")
        quotes=bot._live_mo_put_quote_basis(frame) if product=="MO" else frame.copy()
        return bot._validate_quote_frame(quotes,product,args[-1],frame.attrs["source"])
    monkeypatch.setattr(bot,"_fetch_sina_future_quotes",vendor)
    monkeypatch.setattr(bot,"_fetch_eastmoney_mo_quotes",vendor)
    monkeypatch.setattr(bot,"_fetch_eastmoney_future_quotes",lambda *a: (_ for _ in ()).throw(RuntimeError("synthetic unused second fallback")))
    return calls


def test_recent_completed_replay_can_use_qualified_same_day_close_snapshot_after_transport_failure(monkeypatch):
    calls=_latest_close_fallback_setup(monkeypatch)
    with bot.historical_replay(date(2026,10,14)):
        frame=bot.fetch_cffex_quotes("IC",datetime(2026,10,14,15,40,tzinfo=bot.BEIJING))
    assert calls==["archive","vendor"]
    assert frame.attrs["historical_replay_source"]=="latest_completed_close_snapshot"
    assert set(frame.source_date)=={date(2026,10,14)}
    assert any("历史包" in text for text in frame.attrs["source_failures"])


def test_older_missed_replay_cannot_use_latest_snapshot(monkeypatch):
    calls=_latest_close_fallback_setup(monkeypatch)
    with bot.historical_replay(date(2026,10,13)),pytest.raises(requests.ReadTimeout):
        bot.fetch_cffex_quotes("IC",datetime(2026,10,13,15,40,tzinfo=bot.BEIJING))
    assert calls==["archive"]


@pytest.mark.parametrize("source_day",[date(2026,10,13),date(2026,10,15)])
def test_latest_completed_replay_rejects_wrong_day_snapshot(monkeypatch,source_day):
    _latest_close_fallback_setup(monkeypatch,source_day=source_day)
    with bot.historical_replay(date(2026,10,14)),pytest.raises(RuntimeError,match="同一交易日|过期|晚于"):
        bot.fetch_cffex_quotes("IC",datetime(2026,10,14,15,40,tzinfo=bot.BEIJING))


def test_malformed_historical_archive_cannot_enable_snapshot_fallback(monkeypatch):
    calls=_latest_close_fallback_setup(monkeypatch,archive_error=RuntimeError("synthetic corrupt ZIP"))
    with bot.historical_replay(date(2026,10,14)),pytest.raises(RuntimeError,match="corrupt ZIP"):
        bot.fetch_cffex_quotes("IC",datetime(2026,10,14,15,40,tzinfo=bot.BEIJING))
    assert calls==["archive"]


def test_historical_zero_volume_put_cannot_use_vendor_midpoint_as_official_settlement(monkeypatch):
    _latest_close_fallback_setup(monkeypatch,product="MO")
    with bot.historical_replay(date(2026,10,14)):
        frame=bot.fetch_cffex_quotes("MO",datetime(2026,10,14,15,40,tzinfo=bot.BEIJING))
    assert "MO2612-P-7600" in set(frame.instrument)
    assert "MO2612-P-7800" not in set(frame.instrument)
    assert "MO2612-P-7800" in frame.attrs["listed_instruments"]
    assert "MO2612-P-8200" not in set(frame.instrument)
    assert "MO2612-P-8200" in frame.attrs["listed_instruments"]


@pytest.mark.parametrize("product", ["IC", "IM", "MO"])
@pytest.mark.parametrize("field", ["volume", "position"])
@pytest.mark.parametrize("value", [0.1, 0.5, nextafter(0.0, 1.0)])
def test_latest_close_router_rejects_fractional_contract_counts(monkeypatch, product, field, value):
    """A positive fractional MO volume must not evade the zero-trade Put rule.

    The source adapter invokes the real live-price basis and shared validator
    before returning to the real latest-completed-session replay fallback.
    All rows are synthetic transmission counterexamples, never market data.
    """
    calls = _latest_close_fallback_setup(
        monkeypatch, product=product, count_override=(field, value)
    )
    with bot.historical_replay(date(2026, 10, 14)):
        with pytest.raises(RuntimeError, match="整数张数"):
            bot.fetch_cffex_quotes(product, datetime(2026, 10, 14, 15, 40, tzinfo=bot.BEIJING))
        assert bot._HISTORICAL_REPLAY_DAY.get() == date(2026, 10, 14)
    assert calls == ["archive", "vendor"]
    assert bot._HISTORICAL_REPLAY_DAY.get() is None


@pytest.mark.parametrize("product", ["IC", "IM", "MO"])
@pytest.mark.parametrize("field", ["volume", "position"])
@pytest.mark.parametrize("value", [0.0, 1.0, 100.0])
def test_latest_close_router_preserves_integer_valued_float_contract_counts(monkeypatch, product, field, value):
    _latest_close_fallback_setup(monkeypatch, product=product, count_override=(field, value))
    with bot.historical_replay(date(2026, 10, 14)):
        frame = bot.fetch_cffex_quotes(product, datetime(2026, 10, 14, 15, 40, tzinfo=bot.BEIJING))
    assert frame.attrs["historical_replay_source"] == "latest_completed_close_snapshot"
    assert set(frame.source_date) == {date(2026, 10, 14)}
    if product == "MO":
        assert "MO2612-P-7600" in frame.attrs["listed_instruments"]
        if field == "volume" and value == 0:
            assert "MO2612-P-7600" not in set(frame.instrument)
        else:
            row = frame.loc[frame.instrument.eq("MO2612-P-7600")].iloc[0]
            assert row[field] == value
        for contract in ("MO2612-P-7800", "MO2612-P-8200"):
            assert contract in frame.attrs["listed_instruments"]
            assert contract not in set(frame.instrument)
    else:
        assert frame.iloc[0][field] == value


@pytest.fixture(autouse=True)
def offline_transport_isolation(monkeypatch):
    def forbidden_network(*args, **kwargs):
        pytest.fail("offline transport audit attempted a real network call")

    monkeypatch.setattr(requests, "get", forbidden_network)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_network)
    monkeypatch.setattr(bot, "_CFFEX_MONTH_CACHE", {})
    monkeypatch.setattr(
        bot, "_NETWORK_DEADLINE", contextvars.ContextVar("audit_deadline", default=None)
    )


def _current_month():
    return pd.Timestamp(bot.datetime.now(bot.BEIJING).date().replace(day=1))


def _synthetic_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("transport-only.txt", "synthetic transmission fixture")
    return buffer.getvalue()


def _response(stream, headers=None):
    class Raw:
        closed = False
        released = False

        def stream(self, chunk_size, decode_content):
            assert chunk_size == 64 * 1024
            assert decode_content is True
            yield from stream()

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    response = requests.Response()
    response.status_code = 200
    response.headers.update(headers or {})
    response.raw = Raw()
    response.audit_close_count = 0
    original_close = response.close

    def close():
        response.audit_close_count += 1
        original_close()

    response.close = close
    return response


def _chunks(*parts):
    def stream():
        for part in parts:
            if isinstance(part, BaseException):
                raise part
            yield part

    return stream


@pytest.mark.parametrize(
    "fault",
    [
        requests.ConnectionError("synthetic connect abort"),
        requests.ConnectTimeout("synthetic connect timeout"),
        requests.ReadTimeout("synthetic header timeout"),
    ],
)
def test_header_transport_fault_preserves_type_and_second_failure(monkeypatch, fault):
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs)
        raise fault

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(
        bot, "build_live_trade_signal",
        lambda product, mode: {"archive": bot._cffex_month_archive(_current_month())},
    )
    with pytest.raises(type(fault), match="HTTPS传输失败") as caught:
        bot.build_live_signal_with_transport_retry("IC", "close", 45.0)
    assert len(calls) == 2
    assert caught.value.__cause__ is fault
    assert bot._CFFEX_MONTH_CACHE == {}
    assert bot._NETWORK_DEADLINE.get() is None


def test_real_requests_stream_read_timeout_retries_and_discards_partial(monkeypatch):
    archive = _synthetic_archive()
    calls = []
    responses = [
        _response(_chunks(b"PKpartial", ReadTimeoutError(None, None, "synthetic timeout"))),
        _response(_chunks(archive)),
    ]

    def get(*args, **kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(
        bot, "build_live_trade_signal",
        lambda product, mode: {"archive": bot._cffex_month_archive(_current_month())},
    )
    result = bot.build_live_signal_with_transport_retry("IC", "close", 45.0)
    assert len(calls) == 2
    assert result["archive"] == archive
    assert len(result["data_notes"]) == 1
    assert bot._CFFEX_MONTH_CACHE == {}


def test_real_requests_chunked_disconnect_retries_once(monkeypatch):
    """ProtocolError during requests streaming is a real broken connection."""
    archive = _synthetic_archive()
    responses = [
        _response(_chunks(b"PKpartial", ProtocolError("synthetic incomplete read"))),
        _response(_chunks(archive)),
    ]
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(
        bot, "build_live_trade_signal",
        lambda product, mode: {"archive": bot._cffex_month_archive(_current_month())},
    )
    result = bot.build_live_signal_with_transport_retry("IC", "close", 45.0)
    assert len(calls) == 2
    assert result["archive"] == archive
    assert bot._CFFEX_MONTH_CACHE == {}


def test_streaming_chunks_cannot_outlive_shared_network_deadline(monkeypatch):
    """Read timeout is per socket read; bounded chunks need a total budget too."""
    virtual_clock = [0.0]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: virtual_clock[0])

    def slow_drip():
        virtual_clock[0] += 0.4
        yield b"PK"
        virtual_clock[0] += 0.4
        yield b"synthetic"
        virtual_clock[0] += 0.4
        yield b"archive"

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _response(slow_drip))
    with bot._network_budget(1.0):
        with pytest.raises(RuntimeError, match="预算"):
            bot._cffex_month_archive(_current_month())
    assert bot._CFFEX_MONTH_CACHE == {}


@pytest.mark.parametrize(
    "response",
    [
        _response(_chunks(b"not-a-zip")),
        _response(_chunks(b"PK"), {"Content-Length": "invalid"}),
        _response(_chunks(b"PK"), {"Content-Length": str(bot.MAX_CFFEX_DOWNLOAD_BYTES + 1)}),
        _response(_chunks(b"PK", DecodeError("synthetic decoder corruption"))),
    ],
)
def test_invalid_archive_or_encoding_is_not_transport_retried(monkeypatch, response):
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(
        bot, "build_live_trade_signal",
        lambda product, mode: {"archive": bot._cffex_month_archive(_current_month())},
    )
    with pytest.raises(RuntimeError):
        bot.build_live_signal_with_transport_retry("IC", "close", 45.0)
    assert len(calls) == 1
    assert bot._CFFEX_MONTH_CACHE == {}


def test_http_status_failure_is_not_transport_retried(monkeypatch):
    response = _response(_chunks(b"PK"))
    response.status_code = 403
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(
        bot, "build_live_trade_signal",
        lambda product, mode: {"archive": bot._cffex_month_archive(_current_month())},
    )
    with pytest.raises(RuntimeError, match="HTTPError"):
        bot.build_live_signal_with_transport_retry("IM", "close", 45.0)
    assert len(calls) == 1


def test_current_month_failure_never_uses_existing_cache(monkeypatch):
    month = _current_month()
    bot._CFFEX_MONTH_CACHE[month.strftime("%Y%m")] = b"PKprevious-download"

    def get(*args, **kwargs):
        raise requests.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.ReadTimeout):
        bot._cffex_month_archive(month)


def test_historical_completed_archive_cache_prevents_refetch(monkeypatch):
    month = _current_month() - pd.offsets.MonthBegin(1)
    archive = _synthetic_archive()
    bot._CFFEX_MONTH_CACHE[month.strftime("%Y%m")] = archive
    assert bot._cffex_month_archive(month) == archive


def test_budget_exhaustion_blocks_second_attempt_and_restores_parent(monkeypatch):
    virtual_clock = [0.0]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: virtual_clock[0])
    calls = []

    def build(product, mode):
        calls.append(product)
        virtual_clock[0] = 0.8
        raise requests.ReadTimeout("synthetic remaining budget exhausted")

    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with pytest.raises(RuntimeError, match="预算"):
        bot.build_live_signal_with_transport_retry("IM", "close", 1.0)
    assert calls == ["IM"]
    assert bot._NETWORK_DEADLINE.get() is None


def test_nested_retry_budget_never_extends_parent(monkeypatch):
    virtual_clock = [10.0]
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: virtual_clock[0])
    deadlines = []

    def build(product, mode):
        deadlines.append(bot._NETWORK_DEADLINE.get())
        if len(deadlines) == 1:
            raise requests.ReadTimeout("synthetic first timeout")
        return {"data_notes": []}

    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    with bot._network_budget(2.0):
        bot.build_live_signal_with_transport_retry("IC", "close", 45.0)
        assert bot._NETWORK_DEADLINE.get() == 12.0
    assert deadlines == [12.0, 12.0]
    assert bot._NETWORK_DEADLINE.get() is None


@pytest.mark.parametrize("case", ["read_timeout", "protocol_disconnect", "oversize", "success"])
def test_real_response_is_closed_on_every_archive_exit(monkeypatch, case):
    """Partial streams must close; consumed streams may release to the pool."""
    archive = _synthetic_archive()
    if case == "read_timeout":
        response = _response(
            _chunks(b"PKpartial", ReadTimeoutError(None, None, "synthetic timeout"))
        )
    elif case == "protocol_disconnect":
        response = _response(
            _chunks(b"PKpartial", ProtocolError("synthetic incomplete read"))
        )
    elif case == "oversize":
        response = _response(
            _chunks(archive),
            {"Content-Length": str(bot.MAX_CFFEX_DOWNLOAD_BYTES + 1)},
        )
    else:
        response = _response(_chunks(archive))

    monkeypatch.setattr(requests, "get", lambda *a, **kw: response)
    if case == "success":
        assert bot._cffex_month_archive(_current_month()) == archive
    else:
        with pytest.raises((requests.RequestException, RuntimeError)):
            bot._cffex_month_archive(_current_month())

    assert response.audit_close_count == 1
    assert response.raw.released is True
    if case != "success":
        assert response.raw.closed is True
