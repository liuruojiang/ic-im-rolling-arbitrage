"""Price-basis guards; generated boundary cases are not performance data."""
from datetime import date, datetime
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

import poe_ic_im_mainline_v1_3_bot as bot


def official_frame(monkeypatch, *, product="MO", contract="MO2612-P-8600", volume=0, settle=1323.6):
    # Price values preserve the audited 2026-09-03 CFFEX observation. Other
    # fields and variants below are synthetic adapter boundary fixtures.
    row = {"合约代码": contract, "今开盘": 0, "最高价": 0, "最低价": 0,
           "成交量": volume, "持仓量": 2, "今收盘": 1215.0, "今结算": settle}
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("20260903_1.csv", pd.DataFrame([row]).to_csv(index=False).encode("gbk"))
    monkeypatch.setattr(bot, "_cffex_month_archive", lambda _: archive.getvalue())
    monkeypatch.setattr(bot, "_validate_complete_mo_chain", lambda *args: None)
    monkeypatch.setattr(bot, "_validate_complete_future_structure", lambda *args, **kwargs: None)
    return bot._cffex_historical_quote_frame(product, date(2026, 9, 3), datetime(2026, 9, 3, 18, tzinfo=bot.BEIJING))


def test_zero_volume_preserves_raw_and_discloses_estimate(monkeypatch):
    row = official_frame(monkeypatch).iloc[0]
    assert row.lastprice == row.settle == 1323.6
    assert row.raw_close == 1215.0
    assert row.pricing_basis == "official_settlement_estimate_zero_volume"
    assert "非成交价" in bot._format_market(row.instrument, row)


@pytest.mark.parametrize("settle", [np.nan, np.inf, 0, -1])
def test_missing_settlement_never_reuses_stale_close(monkeypatch, settle):
    with pytest.raises(RuntimeError, match="没有有限正价行情"):
        official_frame(monkeypatch, settle=settle)


@pytest.mark.parametrize("product,contract,volume", [("MO", "MO2612-P-8600", 1), ("MO", "MO2612-C-8600", 0), ("IM", "IM2612", 0)])
def test_traded_put_and_call_and_future_keep_close(monkeypatch, product, contract, volume):
    row = official_frame(monkeypatch, product=product, contract=contract, volume=volume).iloc[0]
    assert row.lastprice == row.raw_close == 1215.0
    assert row.pricing_basis == "official_close"


@pytest.mark.parametrize("bid,ask,oi", [(351,349,0), (0,351,0), (349,np.nan,0), (349,351,-1)])
def test_zero_interest_does_not_bypass_quote_validity(bid, ask, oi):
    row = pd.Series(dict(lastprice=350., volume=0, position=oi, bprice=bid, sprice=ask))
    with pytest.raises(RuntimeError):
        bot._require_existing_leg_quote("IM核心Put", "MO2612-P-8200", row, "ROLL")


@pytest.mark.parametrize("settle", [np.nan, np.inf, 0.0, -1.0])
@pytest.mark.parametrize("day", [date(2026, 9, 3), date(2026, 9, 8)])
def test_invalid_row_does_not_poison_chain_or_allow_nearest_strike_substitution(monkeypatch, settle, day):
    """Multi-row official-shape fault injection, not a historical observation.

    One unavailable target and one unrelated expired unavailable Put coexist
    with a valid quote. The listing universe must survive price-row filtering.
    """
    common = {"今开盘": 0, "最高价": 0, "最低价": 0, "持仓量": 2, "今收盘": 1215.0}
    rows = [
        {**common, "合约代码": "MO2612-P-8400", "成交量": 10, "今结算": 1230.0},
        {**common, "合约代码": "MO2612-P-8600", "成交量": 0, "今结算": settle},
        {**common, "合约代码": "MO2608-P-7200", "成交量": 0, "今结算": 0.0},
    ]
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(f"{day:%Y%m%d}_1.csv", pd.DataFrame(rows).to_csv(index=False).encode("gbk"))
    monkeypatch.setattr(bot, "_cffex_month_archive", lambda _: archive.getvalue())
    monkeypatch.setattr(bot, "_validate_complete_mo_chain", lambda *args: None)
    frame = bot._cffex_historical_quote_frame(
        "MO", day, datetime.combine(day, datetime.min.time(), tzinfo=bot.BEIJING).replace(hour=18)
    )
    assert frame.instrument.tolist() == ["MO2612-P-8400"]
    assert set(frame.attrs["listed_instruments"]) == {item["合约代码"] for item in rows}
    for selector in (bot.select_im_put_for_reset, bot.select_independent_im_put_for_reset):
        ratio = bot.im_put_policy.moneyness(day)
        selected = selector(frame, day, 8400.0 / ratio)
        assert selected.instrument == "MO2612-P-8400"
        assert selected.lastprice == 1215.0
        with pytest.raises(RuntimeError, match="MO2612-P-8600.*禁止跳月或跳行权价"):
            selector(frame, day, 8600.0 / ratio)
