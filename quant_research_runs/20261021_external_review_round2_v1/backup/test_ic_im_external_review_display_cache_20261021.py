"""Counterexamples for same-leg IV, archive recovery and split holdings."""
from datetime import date, datetime
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import nullcontext
import io
import zipfile

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import test_ic_im_put_execution_repairs_20261016 as fixture


def test_ic_warning_uses_core_identity_and_its_own_timestamp(monkeypatch):
    clock = datetime(2026, 10, 21, 13, 30, tzinfo=bot.BEIJING)
    anchor = fixture.anchor("IC")
    signal = fixture.signal("IC", clock.date(), close_confirmed=False, etf_price=7.8,
        put_current_contract="510500P2611M06500", iv_monitor_option_price=.1,
        sse_time="090000", etf_quote_date="20261021", etf_quote_time="132900")
    chain = pd.DataFrame([dict(contract=anchor["v14_core_put_contract"], last=.8, strike=7.5,
                               source_stamp=clock.replace(minute=29))])
    monkeypatch.setattr(bot,"fetch_510500_chain_with_failover",lambda month:(chain,dict(date="20261021",time="132900",source="synthetic_core")))
    monkeypatch.setattr(bot,"_now_beijing",lambda:clock)
    monkeypatch.setattr(bot,"_gov10y_for_day",lambda *args:.02)
    pricing = Mock(return_value=.35)
    monkeypatch.setattr(bot,"_implied_volatility",pricing)
    bot._v14_ic_core_overlay(signal,anchor)
    warning = bot.build_iv_warning("IC",signal,{},None,clock)
    assert warning["contract"] == anchor["v14_core_put_contract"]
    assert pricing.call_args.args[1] == .8
    assert pricing.call_args.args[3] == 7.5
    assert warning["source"] == "synthetic_core"
    assert warning["contract_unit"] == 10000


def test_incomplete_bound_iv_evidence_cannot_fall_back_to_another_leg(monkeypatch):
    clock = datetime(2026,10,21,13,30,tzinfo=bot.BEIJING)
    signal = dict(market_date=clock.date(),close_confirmed=False,etf_price=7.8,
                  put_current_contract="510500P2611M06500",iv_monitor_option_price=.8,
                  iv_monitor_evidence={"contract":"510500P2612M07500"})
    pricing=Mock(return_value=.4)
    monkeypatch.setattr(bot,"_implied_volatility",pricing)
    warning=bot.build_iv_warning("IC",signal,{},None,clock)
    assert warning["contract"]=="510500P2612M07500"
    assert warning["iv"] is None
    pricing.assert_not_called()


def archive(day):
    output=io.BytesIO()
    content="合约代码,今开盘,最高价,最低价,成交量,持仓量,今收盘,今结算\nIC2612,8000,8100,7900,12,123,8050,8040\n"
    with zipfile.ZipFile(output,"w") as z:
        z.writestr(day+"_1.csv",content.encode("gbk"))
    return output.getvalue()


def test_cached_archive_missing_day_is_invalidated_and_refetched_once(monkeypatch):
    monkeypatch.setitem(bot._CFFEX_MONTH_CACHE,"202609",archive("20260929"))
    good=archive("20260930")
    response=SimpleNamespace(headers={},raise_for_status=lambda:None,
                             iter_content=lambda **kwargs:iter([good]),close=lambda:None)
    get=Mock(return_value=response)
    monkeypatch.setattr(bot.requests,"get",get)
    monkeypatch.setattr(bot,"_validate_quote_frame",lambda frame,*args:frame)
    result=bot._cffex_historical_quote_frame("IC",date(2026,9,30),datetime(2026,10,21,16,tzinfo=bot.BEIJING))
    assert result.iloc[0]["lastprice"]==8050
    assert get.call_count==1
    assert bot._CFFEX_MONTH_CACHE["202609"]==good


def test_archive_second_missing_response_is_not_cached_or_retried_forever(monkeypatch):
    bad=archive("20260929")
    monkeypatch.setitem(bot._CFFEX_MONTH_CACHE,"202609",bad)
    response=SimpleNamespace(headers={},raise_for_status=lambda:None,
                             iter_content=lambda **kwargs:iter([bad]),close=lambda:None)
    get=Mock(return_value=response)
    monkeypatch.setattr(bot.requests,"get",get)
    with pytest.raises(RuntimeError,match="缺少唯一日文件"):
        bot._cffex_historical_quote_frame("IC",date(2026,9,30),datetime(2026,10,21,16,tzinfo=bot.BEIJING))
    assert get.call_count==1
    assert "202609" not in bot._CFFEX_MONTH_CACHE


def test_ic_snapshot_lists_each_contract_instead_of_total_on_momentum(monkeypatch):
    anchor=deepcopy(bot.LIVE_CONTINUATION_ANCHOR["IC"])
    anchor.update(verified_core_put_qty=5,verified_momentum_put_qty=3,
                  v14_core_put_qty=5,v14_core_put_contract="510500P2612M07500",
                  post_put_contract="510500P2611M06500",verified_put_qty_normalized=8,post_put_qty=8)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR,"IC",anchor)
    message=Mock()
    bot._write_last_verified_snapshot(message,"IC")
    text=message.write.call_args.args[0]
    assert "核心 5张 `510500P2612M07500`" in text
    assert "动量 3张 `510500P2611M06500`" in text
    assert "合计 8张" in text
    assert "8张 `510500P2611M06500`" not in text


def test_malformed_content_length_has_diagnostic_error():
    response=SimpleNamespace(headers={"Content-Length":"not-a-number"},content=b"data")
    with pytest.raises(RuntimeError,match="长度标头非法"):
        bot._response_with_size_limit(response,"test")


def test_final_put_text_replaces_stale_zero_core_with_final_split():
    signal=dict(put_current_core_qty=5,put_current_momentum_qty=3,put_target_core_qty=7,
                put_target_momentum_qty=0,put_target_total_qty=7,
                v14_core_put_current_contract="510500P2612M07500",v14_core_put_contract="510500P2612M07500",
                put_current_contract="510500P2611M06500",put_target_contract=None,put_target="核心袖0张")
    bot._refresh_ic_put_text(signal)
    assert "核心 7张 `510500P2612M07500`" in signal["put_target"]
    assert "动量 0张" in signal["put_target"]
    assert "合计 7张" in signal["put_target"]
    assert "核心 5张" in signal["put_current"]
    assert "动量 3张" in signal["put_current"]


def test_failed_signal_does_not_start_more_fear_network_requests(monkeypatch):
    clock=datetime(2026,10,21,13,30,tzinfo=bot.BEIJING)
    monkeypatch.setattr(bot,"_now_beijing",lambda:clock)
    monkeypatch.setattr(bot,"build_live_signal_with_transport_retry",Mock(side_effect=RuntimeError("budget exhausted")))
    monkeypatch.setattr(bot.fear_grid,"fetch_score",Mock(side_effect=AssertionError("unexpected network")))
    monkeypatch.setattr(bot.fear_grid,"fetch_jiu_reference",Mock(side_effect=AssertionError("unexpected network")))
    monkeypatch.setattr(bot,"_write_last_verified_snapshot",lambda *args:None)
    message=Mock()
    monkeypatch.setattr(bot,"_sm",lambda:nullcontext(message))
    bot.ICIMMainlinesBot()._handle_signal(("IC",),"intraday")
    text="".join(call.args[0] for call in message.write.call_args_list)
    assert "新目标尚未确认" in text
    assert "9圈" in text
    bot.fear_grid.fetch_score.assert_not_called()
    bot.fear_grid.fetch_jiu_reference.assert_not_called()


@pytest.mark.parametrize("bad", ["encrypted", "duplicate"])
def test_unsafe_archive_is_not_accepted_or_retained(monkeypatch, bad):
    raw=archive("20260930")
    if bad=="encrypted":
        body=bytearray(raw)
        position=body.index(b"PK\x01\x02")
        body[position+8] |= 1
        raw=bytes(body)
    else:
        output=io.BytesIO(raw)
        with pytest.warns(UserWarning),zipfile.ZipFile(output,"a") as zipped:
            zipped.writestr("20260930_1.csv","duplicate")
        raw=output.getvalue()
    monkeypatch.setitem(bot._CFFEX_MONTH_CACHE,"202609",raw)
    with pytest.raises(RuntimeError,match="加密|重复"):
        bot._cffex_historical_quote_frame("IC",date(2026,9,30),datetime(2026,10,21,16,tzinfo=bot.BEIJING))
    assert "202609" not in bot._CFFEX_MONTH_CACHE


@pytest.mark.parametrize("field,value", [("strike",6.5),("expiry",date(2026,11,25)),
                                         ("source",None),("contract_unit",10180)])
def test_mixed_bound_monitor_metadata_is_unavailable(monkeypatch,field,value):
    clock=datetime(2026,10,21,13,30,tzinfo=bot.BEIJING)
    evidence=dict(contract="510500P2612M07500",security_id="10012099",option_price=.8,
                  strike=7.5,expiry=date(2026,12,23),source="synthetic_core",contract_unit=10000,
                  quote_date=clock.date(),quote_time="132900")
    evidence[field]=value
    pricing=Mock(return_value=.4)
    monkeypatch.setattr(bot,"_implied_volatility",pricing)
    signal=dict(market_date=clock.date(),iv_monitor_evidence=evidence)
    warning=bot.build_iv_warning("IC",signal,{},None,clock)
    assert warning["iv"] is None
    pricing.assert_not_called()
