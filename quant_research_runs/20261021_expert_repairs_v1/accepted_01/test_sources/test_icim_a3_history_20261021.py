"""Independent adversarial historical-identity audit; no network or ledger writes."""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import ic_im_adjusted_put_migration_v1 as migration
import poe_ic_im_mainline_v1_4_bot as bot

FIXTURE = Path(__file__).parent / 'tests/fixtures/icim_external_review_round2_20261021'
DAY = date(2026, 10, 20)
CONTRACT, SID = '510500P2612M07500', '10012099'


def payload():
    return json.loads((FIXTURE/'sse_contract_master.json').read_text(encoding='utf-8'))


def selected(body):
    return next(row for row in body['result'] if row['SECURITY_ID'] == SID)


class Response:
    def __init__(self, body, *, declared=None, text=None):
        self.body, self.closed, self.json_called = body, False, False
        self.text = text if text is not None else json.dumps(body, ensure_ascii=False)
        self.content = self.text.encode('utf-8')
        self.headers = {'Content-Length': str(len(self.content)) if declared is None else declared}
    def raise_for_status(self): pass
    def json(self):
        self.json_called = True
        return deepcopy(self.body)
    def close(self): self.closed = True
    def iter_content(self,chunk_size=65536):
        for start in range(0,len(self.content),chunk_size):
            yield self.content[start:start+chunk_size]


def bind_sources(monkeypatch, body=None, *, prices=None):
    response = Response(body if body is not None else payload())
    calls = []
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: response)
    def source(security):
        calls.append(security)
        return prices if prices is not None else pd.Series([.25],index=pd.to_datetime([DAY]))
    monkeypatch.setattr(bot,'fetch_option_closes',source)
    return response,calls


def test_real_master_supports_independent_id_and_strike(monkeypatch):
    response,calls = bind_sources(monkeypatch)
    frame,stamp = bot.fetch_sse_existing_put_historical_quote(CONTRACT,SID,DAY)
    assert response.closed and calls == [SID]
    assert frame.iloc[0]['security_id'] == SID
    assert frame.iloc[0]['strike'] == 7.5
    assert frame.iloc[0]['contract_unit'] == 10000
    assert stamp['identity_date'] == DAY.isoformat()


@pytest.mark.parametrize('year',[1926,2126])
def test_code_year_is_20yy_not_a_century_alias(year):
    body = payload()
    row = selected(body)
    for key in ['EXPIRE_DATE','EXERCISE_DATE','END_DATE']:
        row[key] = f'{year}1223'
    with pytest.raises(RuntimeError):
        migration.master_terms(body,as_of=DAY)


def test_consistent_wrong_expiry_is_rejected_before_a_price_is_consumed(monkeypatch):
    body = payload()
    row = selected(body)
    for key in ['EXPIRE_DATE','EXERCISE_DATE','END_DATE']:
        row[key] = '20261224'
    _,calls = bind_sources(monkeypatch,body)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(CONTRACT,SID,DAY)
    assert calls == []


@pytest.mark.parametrize('field,value',[
    ('SECURITY_ID',10012099),('SECURITY_ID','１００１２０９９'),
    ('CONTRACT_ID','510500P２６１２M07500'),('CONTRACT_UNIT',False),
    ('CONTRACT_UNIT','9999'),('EXERCISE_PRICE','7.499'),
    ('EXERCISE_PRICE','NaN'),('TIMESAVE','2026-10-19'),
    ('START_DATE','20261021'),('DELISTFLAG','是'),
])
def test_relevant_master_fields_cannot_be_weakened(field,value):
    body = payload()
    selected(body)[field] = value
    with pytest.raises(RuntimeError):
        migration.master_terms(body,as_of=DAY)


def test_old_m_adjusted_security_is_rejected_before_price_fetch(monkeypatch):
    _,calls = bind_sources(monkeypatch)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(CONTRACT,'10011470',DAY)
    assert calls == []


@pytest.mark.parametrize('value',[np.bool_(True),True,float('nan'),float('inf'),-.1])
def test_historical_scalar_price_must_be_real_nonnegative_number(monkeypatch,value):
    series = pd.Series([value],index=pd.to_datetime([DAY]))
    bind_sources(monkeypatch,prices=series)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(CONTRACT,SID,DAY)


def test_duplicate_dated_prices_cannot_be_accepted(monkeypatch):
    values = pd.Series([.2,.9],index=pd.to_datetime([DAY,DAY]))
    bind_sources(monkeypatch,prices=values)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(CONTRACT,SID,DAY)


def test_new_master_source_rejects_declared_oversize_before_json_parse(monkeypatch):
    response = Response(payload(),declared=str(bot.MAX_QUOTE_RESPONSE_BYTES+1))
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: response)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_510500_dated_master(DAY)
    assert response.closed and not response.json_called


def test_new_master_source_rejects_a_complete_oversize_json_body(monkeypatch):
    body = payload()
    body['synthetic_ignored_field'] = 'x'*(bot.MAX_QUOTE_RESPONSE_BYTES+1)
    response = Response(body)
    assert len(response.content) > bot.MAX_QUOTE_RESPONSE_BYTES
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: response)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_510500_dated_master(DAY)
    assert response.closed


def test_new_master_source_cannot_finish_after_shared_budget_expired(monkeypatch):
    response = Response(payload())
    now = [0.]
    monkeypatch.setattr(bot.time_module,'monotonic',lambda: now[0])
    def get(*a,**k):
        assert k['timeout'] <= 1.
        now[0] = 2.
        return response
    monkeypatch.setattr(bot.requests,'get',get)
    token = bot._NETWORK_DEADLINE.set(1.)
    try:
        with pytest.raises(RuntimeError):
            bot.fetch_sse_510500_dated_master(DAY)
    finally:
        bot._NETWORK_DEADLINE.reset(token)
    assert response.closed


def test_duplicate_raw_sina_dates_cannot_disappear_before_identity_consumer(monkeypatch):
    master = Response(payload())
    sina = Response({},text='callback([{"d":"2026-10-20","c":"0.2"},{"d":"2026-10-20","c":"0.9"}]);')
    def request(url,**kwargs):
        if url == 'https://query.sse.com.cn/commonQuery.do': return master
        if 'StockOptionDaylineService.getSymbolInfo' in url: return sina
        raise RuntimeError('synthetic backup unavailable')
    monkeypatch.setattr(bot.requests,'get',request)
    # Real public history consumers execute; neither identity nor price loader
    # is replaced. Only the HTTP transport is synthetic and local.
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(CONTRACT,SID,DAY)


def test_bad_sina_source_does_not_block_independent_valid_backup(monkeypatch):
    master = Response(payload())
    sina = Response({},text='callback([{"d":"2026-10-20","c":"0.2"},{"d":"2026-10-20","c":"0.9"}]);')
    eastmoney = Response({'data':dict(code=SID,market=10,klines=['2026-10-20,0.1,0.3'])})
    def request(url,**kwargs):
        if url == 'https://query.sse.com.cn/commonQuery.do': return master
        if 'StockOptionDaylineService.getSymbolInfo' in url: return sina
        if 'push2his.eastmoney.com' in url: return eastmoney
        raise AssertionError('unexpected source')
    monkeypatch.setattr(bot.requests,'get',request)
    frame,stamp = bot.fetch_sse_existing_put_historical_quote(CONTRACT,SID,DAY)
    assert frame.iloc[0]['last'] == .3
    assert stamp['source'] == 'Eastmoney'
    assert any('Sina' in failure for failure in stamp['source_failures'])


def test_selected_2027_expiry_requires_official_coverage_before_price(monkeypatch):
    body = payload()
    row = selected(body)
    row['CONTRACT_ID'] = '510500P2703M07500'
    for key in ['EXPIRE_DATE','EXERCISE_DATE','END_DATE']:
        row[key] = '20270324'
    _,calls = bind_sources(monkeypatch,body)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(row['CONTRACT_ID'],SID,DAY)
    assert calls == []


def test_actual_core_overlay_succeeds_with_same_day_independent_master(monkeypatch):
    bind_sources(monkeypatch)
    anchor = dict(v14_core_put_contract=CONTRACT,v14_core_put_security_id=SID,
                  v14_core_put_qty=5,verified_core_put_delta=.25)
    signal = dict(market_date=DAY,core_put_target_delta=.25,option_monthly_reset_due=False,
                  put_target_momentum_qty=0)
    monkeypatch.setattr(bot,'_now_beijing',lambda: datetime(2026,10,20,15,40,tzinfo=bot.BEIJING))
    with bot.historical_replay(DAY):
        bot._v14_ic_core_overlay(signal,anchor)
    assert signal['iv_monitor_evidence']['security_id'] == SID
    assert signal['iv_monitor_option_price'] == .25
