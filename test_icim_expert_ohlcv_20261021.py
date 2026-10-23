"""OHLCV waiting evidence and bounded third-source freshness checks, offline."""
from datetime import date, datetime
import hashlib
import json

import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot


CLOCK = datetime(2026, 10, 21, 15, 5, tzinfo=bot.BEIJING)


def row(day, close="100.01", volume="20000"):
    return [day, "100", close, "101", "99", volume]


class StreamResponse:
    def __init__(self, rows):
        self.content = json.dumps({"code": 0, "msg": "", "data": {
            "sh000905": {"day": rows}}}).encode()
        self.headers = {"Content-Length": str(len(self.content))}
        self.url = bot.TENCENT_OHLCV_HISTORY_URL
        self.status_code = 200
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        self.closed = True


def configured_pages(monkeypatch, pages):
    dates = pd.to_datetime(["2026-10-19", "2026-10-20"])
    digest = hashlib.sha256(("\n".join(dates.strftime("%Y-%m-%d")) + "\n").encode()).hexdigest()
    monkeypatch.setattr(bot, "DATA_CUTOFF", date(2026, 10, 20))
    monkeypatch.setitem(bot.V13_HISTORY_DATE_INDEX, "IC", (date(2026, 10, 19), 2, digest))
    calls = []

    def get(url, *, params, timeout, headers, stream):
        response = StreamResponse(pages[len(calls)])
        calls.append((params, response))
        return response

    monkeypatch.setattr(bot.requests, "get", get)
    return calls


def probe_fetch():
    token = bot._OHLCV_FRESHNESS_PROBE.set(True)
    try:
        return bot._fetch_tencent_ohlcv_history("IC", CLOCK)
    finally:
        bot._OHLCV_FRESHNESS_PROBE.reset(token)


def test_stale_third_source_stops_after_one_recent_request(monkeypatch):
    calls = configured_pages(monkeypatch, [[row("2026-10-20")]])
    with pytest.raises(bot.DailyOHLCVNotReady, match="最新日线尚未更新"):
        probe_fetch()
    assert len(calls) == 1
    assert calls[0][0]["param"].startswith("sh000905,day,2026-10-19,2026-10-21,")
    assert calls[0][1].closed


def test_fresh_third_source_fetches_and_validates_full_history(monkeypatch):
    calls = configured_pages(monkeypatch, [
        [row("2026-10-20"), row("2026-10-21")],
        [row("2026-10-19"), row("2026-10-20"), row("2026-10-21")],
    ])
    result = probe_fetch()
    assert len(calls) == 2
    assert all(response.closed for _, response in calls)
    assert result.index.strftime("%Y-%m-%d").tolist() == ["2026-10-19", "2026-10-20", "2026-10-21"]
    assert result.volume.tolist() == [2_000_000.] * 3
    assert [p["purpose"] for p in result.attrs["source_page_manifest"]] == ["freshness_probe", "history_page"]


def test_recent_page_does_not_substitute_for_missing_warmup(monkeypatch):
    calls = configured_pages(monkeypatch, [[row("2026-10-21")], [row("2026-10-20"), row("2026-10-21")]])
    with pytest.raises(RuntimeError, match="交易日索引不完整"):
        probe_fetch()
    assert len(calls) == 2


def test_probe_and_full_page_conflict_cannot_be_silently_overwritten(monkeypatch):
    configured_pages(monkeypatch, [
        [row("2026-10-21")],
        [row("2026-10-19"), row("2026-10-20"), row("2026-10-21", close="100.02")],
    ])
    with pytest.raises(RuntimeError, match="分页重叠行冲突"):
        probe_fetch()


@pytest.mark.parametrize("bad_row", [row("2026-10-20", volume="0"), row("2026-10-20", close="999"),
                                         row("2026-10-20", close="NaN"), row("2026-10-20", volume="True")])
def test_malformed_stale_probe_is_not_mislabeled_waiting(monkeypatch, bad_row):
    configured_pages(monkeypatch, [[bad_row]])
    with pytest.raises((RuntimeError, ValueError)) as caught:
        probe_fetch()
    assert not isinstance(caught.value, bot.DailyOHLCVNotReady)


def aggregate_sources(monkeypatch, sina, eastmoney, tencent):
    rows = [dict(day=f"2026-{month:02d}-{day:02d}", open="100", high="101", low="99", close="100", volume="2000000")
            for month in range(1, 10) for day in range(1, 21)]

    class SinaResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return rows

    def get(*args, **kwargs):
        if isinstance(sina, (requests.ConnectionError, requests.Timeout)):
            raise sina
        return SinaResponse()

    def east(*args, **kwargs):
        if isinstance(eastmoney, (requests.ConnectionError, requests.Timeout)):
            raise eastmoney
        return {"data": {"klines": [f"{r['day']},100,100,101,99,2000000" for r in rows]}}

    def validate(product, frame, clock):
        error = sina if frame.attrs["source"] == "Sina" else eastmoney
        raise error

    observed_probe = []

    def third(product, clock):
        observed_probe.append(bot._OHLCV_FRESHNESS_PROBE.get())
        if isinstance(tencent, Exception):
            raise tencent
        return tencent.copy()

    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "_request_json", east)
    monkeypatch.setattr(bot, "_validate_v13_ohlcv", validate)
    monkeypatch.setattr(bot, "_fetch_tencent_ohlcv_history", third)
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK)
    return observed_probe


def test_mixed_failures_preserve_waiting_and_bad_data_reasons(monkeypatch):
    observed = aggregate_sources(monkeypatch, bot.DailyOHLCVNotReady("Sina daily lag"),
        RuntimeError("Eastmoney malformed bar"), requests.Timeout("Tencent connection"))
    with pytest.raises(bot.DailyOHLCVSourcesUnavailable) as caught:
        bot.fetch_ohlcv_history("IC")
    exc = caught.value
    assert exc.status == "daily_ohlcv_sources_unavailable"
    assert exc.waiting_sources == ["Sina"]
    assert [(r["source"], r["type"]) for r in exc.source_failures] == [
        ("Sina", "DailyOHLCVNotReady"), ("Eastmoney", "RuntimeError"), ("Tencent", "Timeout")]
    assert "malformed bar" in exc.source_failures[1]["reason"]
    assert observed == [True]
    assert bot._OHLCV_FRESHNESS_PROBE.get() is False


def test_all_waiting_keeps_special_wait_status(monkeypatch):
    aggregate_sources(monkeypatch, *(bot.DailyOHLCVNotReady(name) for name in ("s", "e", "t")))
    with pytest.raises(bot.DailyOHLCVNotReady) as caught:
        bot.fetch_ohlcv_history("IC")
    assert caught.value.waiting_sources == ["Sina", "Eastmoney", "Tencent"]
    assert len(caught.value.source_failures) == 3


def test_all_transport_remains_retryable_transport(monkeypatch):
    observed = aggregate_sources(monkeypatch, requests.Timeout("s"), requests.ConnectionError("e"), requests.Timeout("t"))
    with pytest.raises(bot.SignalTransportError) as caught:
        bot.fetch_ohlcv_history("IC")
    assert caught.value.waiting_sources == []
    assert observed == [False]


def test_unavailable_without_wait_evidence_is_not_called_waiting(monkeypatch):
    aggregate_sources(monkeypatch, RuntimeError("s malformed"), RuntimeError("e missing"), RuntimeError("t bad"))
    with pytest.raises(bot.DailyOHLCVSourcesUnavailable) as caught:
        bot.fetch_ohlcv_history("IC")
    assert caught.value.waiting_sources == []


def test_two_waiting_sources_do_not_skip_valid_third_source(monkeypatch):
    expected = pd.DataFrame({"close": [100.]}, index=pd.to_datetime(["2026-10-21"]))
    observed = aggregate_sources(monkeypatch, bot.DailyOHLCVNotReady("s"), bot.DailyOHLCVNotReady("e"), expected)
    result = bot.fetch_ohlcv_history("IC")
    pd.testing.assert_frame_equal(result, expected)
    assert observed == [True]
    assert len(result.attrs["source_failures"]) == 2


def test_public_fetch_stale_real_validation_uses_only_one_tencent_page(monkeypatch):
    # All three source adapters and the actual freshness validators run.
    # The 180 synthetic daily rows are not a historical/calendar oracle.
    days = pd.date_range(end="2026-10-20", periods=180, freq="B")
    stale = [dict(day=stamp.strftime("%Y-%m-%d"), open="100", high="101",
                  low="99", close="100.01", volume="2000000") for stamp in days]
    calls = []

    class SinaResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return stale

    def get(url, **kwargs):
        if "money.finance.sina.com.cn" in url:
            return SinaResponse()
        response = StreamResponse([row("2026-10-20")])
        calls.append((kwargs["params"], response))
        return response

    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "_request_json", lambda *a, **kw: {"data": {"klines": [
        f"{r['day']},100,100.01,101,99,2000000" for r in stale]}})
    with bot.runtime_clock(CLOCK), pytest.raises(bot.DailyOHLCVNotReady) as caught:
        bot.fetch_ohlcv_history("IC")
    assert caught.value.waiting_sources == ["Sina", "Eastmoney", "Tencent"]
    assert len(calls) == 1
    assert ",2026-09-21,2026-10-21," in calls[0][0]["param"]
    assert calls[0][1].closed
    assert bot._OHLCV_FRESHNESS_PROBE.get() is False
