"""Live zero-volume Put quote regression; explicit injected payloads, no network."""
from datetime import datetime, date
from types import SimpleNamespace
import pandas as pd
import pytest
import poe_ic_im_mainline_v1_3_bot as bot

DAY = date(2026, 9, 8)
CLOCK = datetime(2026, 9, 8, 14, 30, tzinfo=bot.BEIJING)
BASIS = "live_bid_ask_mid_estimate_zero_volume"


def test_string_extension_dtype_can_receive_numeric_midpoint():
    raw = pd.DataFrame([dict(instrument="MO2611-P-8000", lastprice="-", volume=0., bprice=653., sprice=661.8)])
    raw["lastprice"] = raw["lastprice"].astype("string")
    result = bot._live_mo_put_quote_basis(raw)
    assert result.lastprice.iloc[0] == pytest.approx(657.4)
    assert result.raw_lastprice.iloc[0] == "-"
    assert pd.api.types.is_numeric_dtype(result.lastprice)


@pytest.mark.parametrize("last", ["-", 0., 730.8])
def test_zero_volume_put_uses_verified_two_sided_estimate_not_raw_last(last):
    raw = pd.DataFrame([dict(instrument="MO2611-P-8000", lastprice=last, volume=0., bprice=653., sprice=661.8, position=6.)])
    row = bot._live_mo_put_quote_basis(raw).iloc[0]
    assert row.lastprice == pytest.approx(657.4)
    assert row.raw_lastprice == last
    assert row.pricing_basis == BASIS
    assert row.bprice == 653. and row.sprice == 661.8
    assert "双边中价估计" in bot._format_market(row.instrument, row)
    assert "非成交价" in bot._format_market(row.instrument, row)


@pytest.mark.parametrize("bid,ask", [(0,661.8),(670,661.8),(float("nan"),661.8),(653,float("inf"))])
def test_zero_volume_bad_two_sided_quote_cannot_fall_back_to_old_last(bid, ask):
    row = bot._live_mo_put_quote_basis(pd.DataFrame([dict(instrument="MO2611-P-8000", lastprice=730.8, volume=0, bprice=bid, sprice=ask, settle=700.)])).iloc[0]
    assert pd.isna(row.lastprice)
    assert row.pricing_basis.startswith("unavailable")


def test_call_and_traded_put_prices_are_unchanged():
    rows = pd.DataFrame([dict(instrument="MO2611-C-8000", lastprice=200., volume=0, bprice=210., sprice=220.),
                         dict(instrument="MO2611-P-8000", lastprice=730.8, volume=10, bprice=653., sprice=661.8)])
    assert bot._live_mo_put_quote_basis(rows).lastprice.tolist() == [200.,730.8]


def _mock_sina(monkeypatch, stamp="2026-09-08 14:15:00", bid="653", ask="661.8", last="-"):
    fields = ["0"] * 52
    fields[1],fields[2],fields[3],fields[5],fields[32],fields[40] = bid,last,ask,"6",stamp,"-"
    content = ('var q="' + ','.join(fields) + '";').encode("gbk")
    response = SimpleNamespace(content=content, headers={}, raise_for_status=lambda:None)
    monkeypatch.setattr(bot.requests,"get",lambda *a,**k:response)


def test_sina_confirms_midpoint_without_requiring_untraded_last(monkeypatch):
    _mock_sina(monkeypatch)
    result = bot.verify_sina_option_quote("MO2611-P-8000", DAY, 657.4, CLOCK, pricing_basis=BASIS)
    assert result["lastprice"] == pytest.approx(657.4)
    assert result["raw_lastprice"] is None
    assert result["pricing_basis"] == BASIS


@pytest.mark.parametrize("kwargs", [dict(stamp="2026-09-02 15:00:00"), dict(stamp="2026-09-08 09:31:00"), dict(stamp="2026-09-08 14:31:00"), dict(bid="670"), dict(ask="0")])
def test_sina_midpoint_refuses_invalid_or_stale_confirmation(monkeypatch, kwargs):
    _mock_sina(monkeypatch,**kwargs)
    with pytest.raises(RuntimeError):
        bot.verify_sina_option_quote("MO2611-P-8000", DAY, 657.4, CLOCK, pricing_basis=BASIS)


def test_iv_reports_zero_volume_midpoint_as_estimate(monkeypatch):
    quotes = bot._live_mo_put_quote_basis(pd.DataFrame([dict(instrument="MO2612-P-7600", lastprice="-", volume=0, bprice=350., sprice=360., source_time="14:15:00")]))
    quotes.attrs.update(source="explicit injection", source_date=DAY)
    monkeypatch.setattr(bot,"_gov10y_for_day",lambda *a:.02)
    result = bot.build_iv_warning("IM",dict(market_date=DAY,put_reference_price=8000.),
                                 dict(price=8000.,live_price_source_date=DAY,live_price_source_time="14:30:00"),quotes,CLOCK)
    assert result["iv"] is not None
    assert result["pricing_basis"] == BASIS
    assert "双边中价估算IV，非成交报价" in result["text"]
