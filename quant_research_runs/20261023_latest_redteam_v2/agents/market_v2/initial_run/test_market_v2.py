"""Fault-injected vendor packets; saved real index OHLCV, no network or state."""
from datetime import datetime
from pathlib import Path
import json
from unittest.mock import patch
import pytest
import requests
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
CLOCK = datetime(2026,10,22,14,30,tzinfo=bot.BEIJING)
REAL_RAW = ROOT/'quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle/blobs/7155726823743f87af08fae1ce90ef44b6565ef8589cb71199194f8540e42453.bin'
OBS=[]

def qt(symbol='sh000905', code='000905', price=7435.164, stamp='20261022143000'):
    fields=['']*31
    fields[0]='1';fields[1]='synthetic-index';fields[2]=code;fields[3]=str(price);fields[30]=stamp
    return ('v_'+symbol+'="'+'~'.join(fields)+'";').encode('gbk')

def response(raw=None,payload=None):
    r=requests.Response();r.status_code=200;r.encoding='gbk';r._content_consumed=True
    r._content=raw if raw is not None else json.dumps(payload).encode('utf-8')
    return r

def vendor(monkeypatch, packet=None, em=None):
    raw_history=json.loads(REAL_RAW.read_bytes().decode('gbk'))
    history=[r for r in raw_history if r['day'][:10]<'2026-10-22']
    def get(url,**kw):
        if 'CN_MarketData' in url:return response(payload=history)
        if 'qt.gtimg.cn' in url:
            if packet is None:raise requests.Timeout('synthetic Tencent outage')
            return response(raw=packet)
        if 'push2.eastmoney.com' in url:
            if em is None:raise requests.Timeout('synthetic EastMoney outage')
            return response(payload={'data':em})
        raise AssertionError('Network forbidden: '+url)
    monkeypatch.setattr(bot,'_evidence_http_get',get)
    monkeypatch.setattr(bot.requests.sessions.Session,'request',lambda *a,**k:pytest.fail('external network forbidden'))
    monkeypatch.setattr(bot,'_now_beijing',lambda:CLOCK)
    monkeypatch.setattr(bot,'_resolve_gov10y_evidence',lambda d,c,rate,anchor:{'yield_decimal':rate,'source':'isolated frozen rate'})
    # No local valuation cache or user snapshot affects the comparison.
    monkeypatch.setattr(bot.daily_valuation,'resolve',lambda p,d,c,price,frozen,anchor_day:{
        'pe':float(frozen['pe'])*price/float(frozen['price']),
        'pb':float(frozen['pb'])*price/float(frozen['price']),
        'provenance':{'mode':'proxy','reason':'isolated frozen proxy'}})

def em_quote(code='000905',stamp=None):
    return {'f43':743516.4,'f57':code,'f58':'synthetic-index','f86':(stamp or CLOCK).timestamp()}

def summarize(name,value):
    keys=['price','source','source_date','source_time','live_price_source_time','history_date','momentum_120','momentum_score','momentum_next_weight','v13_core_put_delta']
    OBS.append({'case':name,'observed':{k:value[k] for k in keys if k in value}})
    (HERE/'observations.json').write_text(json.dumps(OBS,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

@pytest.mark.parametrize('provider',['qt','em'])
def test_correct_index_packet_is_accepted(monkeypatch,provider):
    vendor(monkeypatch,qt() if provider=='qt' else None,em_quote() if provider=='em' else None)
    out=bot.fetch_live_price_quote('IC');assert out['price']==7435.164;summarize('correct_'+provider,out)

@pytest.mark.parametrize('mutation',['variable','body_code','duplicate','eastmoney_code'])
def test_wrong_index_identity_must_be_rejected(monkeypatch,mutation):
    packet=qt();em=None
    if mutation=='variable':packet=qt(symbol='sh000852',price=3000.)
    if mutation=='body_code':packet=qt(code='000852',price=3000.)
    if mutation=='duplicate':packet=qt(price=3000.)+qt(price=7435.164)
    if mutation=='eastmoney_code':packet=None;em=em_quote('000852')
    vendor(monkeypatch,packet,em)
    with pytest.raises(RuntimeError):
        out=bot.fetch_live_price_quote('IC');summarize(mutation,out)

@pytest.mark.parametrize('provider',['qt','em'])
def test_future_intraday_quote_must_be_rejected_before_strategy(monkeypatch,provider):
    future=CLOCK.replace(hour=15)
    vendor(monkeypatch,qt(stamp='20261022150000') if provider=='qt' else None,em_quote(stamp=future) if provider=='em' else None)
    with pytest.raises(RuntimeError):
        out=bot.live_proxy('IC',CLOCK);summarize('future_'+provider,out)


def test_midnight_same_day_tick_must_not_drive_intraday_strategy(monkeypatch):
    vendor(monkeypatch,qt(stamp='20261022000000'))
    with pytest.raises(RuntimeError):
        out=bot.live_proxy('IC',CLOCK);summarize('midnight_quote',out)


def test_wrong_index_identity_changes_actual_momentum_path(monkeypatch):
    vendor(monkeypatch,qt())
    normal=bot.live_proxy('IC',CLOCK);summarize('live_proxy_control',normal)
    vendor(monkeypatch,qt(symbol='sh000852',code='000852',price=3000.))
    wrong=bot.live_proxy('IC',CLOCK);summarize('live_proxy_wrong_index',wrong)
    assert wrong['price']==3000.
    assert wrong['momentum_120']!=normal['momentum_120']
    assert wrong['momentum_score']!=normal['momentum_score']
