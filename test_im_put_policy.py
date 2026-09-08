from datetime import date
import math
import pandas as pd
import pytest
import im_put_policy as policy
import poe_ic_im_mainline_v1_3_bot as bot


@pytest.mark.parametrize("mom,weight,core,expected", [(0.1,1,2,0), (0,1,2,0), (-0.1,1,2,1.5), (-0.1,.5,0,0.75), (-0.1,0,2,0)])
def test_only_mom120_drives_momentum_put(mom,weight,core,expected):
    assert policy.momentum_quantity(date(2026,9,8),mom,weight,core)==expected
    assert policy.momentum_quantity(date(2026,9,7),mom,weight,core)==core*weight


@pytest.mark.parametrize("iv,level", [(None,"unavailable"),(math.nan,"unavailable"),(.4,"normal"),(.40001,"high"),(.5,"high"),(.50001,"critical")])
def test_strict_iv_warning_boundaries(iv,level):
    result=policy.iv_warning(iv,source="fixture",market_date="2026-09-08",snapshot_time="13:30:00")
    assert result['level']==level
    assert result['position_effect']=='none'


@pytest.mark.parametrize("selector",[bot.select_im_put_for_reset,bot.select_independent_im_put_for_reset])
def test_102_nearest_listed_even_zero_volume_and_history_95(selector):
    quotes=pd.DataFrame([dict(instrument=f"MO2612-P-{k}",lastprice=350.,volume=0 if k==8200 else 10,position=0 if k==8200 else 20) for k in [7600,8000,8200,8400]])
    assert selector(quotes,date(2026,9,8),8000).instrument=="MO2612-P-8200"
    assert selector(quotes,date(2026,9,7),8000).instrument=="MO2612-P-7600"
    quotes.attrs['listed_instruments']=quotes.instrument.tolist()+['MO2612-P-8150']
    with pytest.raises(RuntimeError,match="禁止跳月或跳行权价"):
        selector(quotes,date(2026,9,8),8000)


def test_iv_monitor_uses_fixed_95_reference_and_does_not_mutate_signal(monkeypatch):
    quotes=pd.DataFrame([dict(instrument=f"MO2612-P-{k}",lastprice=250.,volume=0,position=0,source_time="13:30:00") for k in [7600,8000,8200]])
    quotes.attrs.update(source="test",source_date=date(2026,9,8))
    signal=dict(market_date=date(2026,9,8),put_reference_price=8000.)
    original=signal.copy()
    monkeypatch.setattr(bot,'_implied_volatility',lambda *a: .51)
    monkeypatch.setattr(bot,'_gov10y_for_day',lambda *a: .02)
    result=bot.build_iv_warning('IM',signal,dict(price=8100.),quotes,bot._now_beijing())
    assert result['level']=='critical'
    assert result['contract']=='MO2612-P-7600'
    assert signal==original
    quotes.attrs['source_date']=date(2026,9,7)
    assert bot.build_iv_warning('IM',signal,dict(price=8100.),quotes,bot._now_beijing())['level']=='unavailable'
