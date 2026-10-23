"""Synthetic boundary fixtures; live source acceptance is recorded separately."""
import json
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

import ic_im_chinabond as c
import ic_im_daily_valuation as v


HTML = """<table><tr><th>2026-09-08(%)</th><th>30年</th><th>10<!--年-->年</th></tr>
<tr><td>中债商业银行普通债收益率曲线(AAA)</td><td>3</td><td>4</td></tr>
<tr><td>中债国债收益率曲线</td><td>2.1325</td><td>1.6798</td></tr></table>"""


def snapshot(**updates):
    return {"revision": c.REVISION, "status": "ok", "source": c.SOURCE,
            "expected_date": "2026-09-08", "source_date": "2026-09-08",
            "yield_decimal": .016798, "fetched_at": "2026-09-08T18:00:00+08:00",
            "page_sha256": "a" * 64, **updates}


def install(monkeypatch, tmp_path, value):
    target = tmp_path / "bond.json"
    target.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv(c.FILE_ENV, str(target))


def chosen(clock=None, day=date(2026, 9, 8)):
    return c.resolve(day, clock or datetime(2026, 9, 8, 18, 1, tzinfo=c.BEIJING),
                     .016964, date(2026, 8, 14))


def test_parser_uses_named_tenor_and_exact_government_curve():
    value = c.parse_official_html(HTML)
    assert value == {"source_date": "2026-09-08", "yield_decimal": .016798}


@pytest.mark.parametrize("bad", [
    HTML.replace("10<!--年-->年", "7年"), HTML.replace("(%)", ""),
    HTML.replace("1.6798", "NaN"), HTML.replace("1.6798", "100"),
    HTML + HTML, HTML.replace("中债国债收益率曲线", "中债进出口行债收益率曲线"),
    HTML.replace("<td>1.6798</td>", ""),
])
def test_bad_units_columns_or_duplicate_curves_rejected(bad):
    with pytest.raises(ValueError):
        c.parse_official_html(bad)


@pytest.mark.parametrize("changes", [
    {"status": "unavailable"}, {"source_date": "2026-09-07"},
    {"source_date": "2026-09-09"}, {"expected_date": "2026-09-07"},
    {"yield_decimal": float("inf")}, {"yield_decimal": True},
    {"yield_decimal": 1.6798}, {"page_sha256": "bad"},
    {"fetched_at": "2026-09-08T18:00:00"},
    {"fetched_at": "2026-09-08T17:00:00+08:00"},
    {"fetched_at": "2026-09-09T18:00:00+08:00"},
])
def test_invalid_or_stale_snapshot_keeps_original_rate_and_discloses(monkeypatch, tmp_path, changes):
    install(monkeypatch, tmp_path, snapshot(**changes))
    result = chosen()
    assert result["mode"] == "frozen_fallback"
    assert result["yield_decimal"] == .016964
    assert "非当日利率" in c.disclosure(result)
    assert "2026-08-14" in c.disclosure(result)


def test_actual_and_no_intraday_lookahead(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, snapshot())
    result = chosen()
    assert result["mode"] == "official_actual"
    assert result["yield_decimal"] == .016798
    assert "1.6798%" in c.disclosure(result)
    intraday = chosen(datetime(2026, 9, 8, 14, 30, tzinfo=c.BEIJING))
    assert intraday["mode"] == "frozen_fallback"


def test_missing_corrupt_and_unset_file(monkeypatch, tmp_path):
    monkeypatch.delenv(c.FILE_ENV, raising=False)
    assert chosen()["mode"] == "frozen_fallback"
    monkeypatch.setenv(c.FILE_ENV, str(tmp_path / "absent"))
    assert chosen()["mode"] == "frozen_fallback"
    (tmp_path / "absent").write_text("not json", encoding="utf-8")
    assert chosen()["mode"] == "frozen_fallback"


def test_fetch_retries_transport_and_stale_page(monkeypatch):
    calls = []
    class Response:
        content = HTML.encode()
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): pass
    def get(*args, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise requests.Timeout()
        return Response()
    monkeypatch.setattr(c.requests, "get", get)
    monkeypatch.setattr(c.time_module, "sleep", lambda _: None)
    result = c.fetch_daily(date(2026, 9, 8))
    assert result["status"] == "ok" and len(calls) == 2
    calls.clear()
    stale = c.fetch_daily(date(2026, 9, 9))
    assert stale["status"] == "unavailable" and len(calls) == 3
    assert stale["source_date"] == "2026-09-08"


def test_erp_and_option_pricing_read_same_rate(monkeypatch, tmp_path):
    import poe_ic_im_mainline_v1_3_bot as b
    from test_poe_ic_im_mainline_v1_3_bot import _full_ic_history, _as_ohlcv
    install(monkeypatch, tmp_path, snapshot())
    monkeypatch.setenv(v.ENV, "")
    monkeypatch.setenv(v.FILE_ENV, str(tmp_path / "no-vip"))
    frame = _as_ohlcv(_full_ic_history("2026-09-08"))
    monkeypatch.setattr(b, "fetch_ohlcv_history", lambda _: frame.copy())
    monkeypatch.setattr(b, "fetch_live_price_quote", lambda _: {
        "price": float(frame.close.iloc[-1]), "source": "unit-test-fixture",
        "source_date": date(2026, 9, 8), "source_time": "15:00:00"})
    inputs = []
    original = b.valuation_score
    def capture(pb, erp, dividend):
        inputs.append(erp)
        return original(pb, erp, dividend)
    monkeypatch.setattr(b, "valuation_score", capture)
    clock = datetime(2026, 9, 8, 18, 1, tzinfo=c.BEIJING)
    with b.runtime_clock(clock):
        live = b.live_proxy("IC", clock)
        pricing_rate = b._gov10y_for_day("IC", date(2026, 9, 8))
    assert pricing_rate == .016798
    assert 1 / live["proxy_pe"] - pricing_rate in inputs
    assert live["valuation_provenance"]["gov10y"]["yield_decimal"] == pricing_rate
    assert "国债利率：中债官方10年期" in v.disclosure(live["valuation_provenance"])


def test_all_option_rate_consumers_use_date_checked_selector():
    import inspect
    import poe_ic_im_mainline_v1_3_bot as b
    for function in (b._size_existing_ic_put, b.select_ic_put_for_reset,
                     b.select_im_call_d10, b.select_im_call_rescue):
        assert "_gov10y_for_day" in inspect.getsource(function)
        assert '["gov10y"]' not in inspect.getsource(function)
