"""Fault-injected vendor packets; saved real index OHLCV, no network or state."""
from datetime import datetime
from pathlib import Path
import json
from unittest.mock import patch
import pytest
import requests
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
HERE = Path(__import__('os').environ['ICIM_TEST_OUTPUT_DIR'])
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
    out=bot.fetch_live_price_quote('IC');assert out['price']==pytest.approx(7435.164);summarize('correct_'+provider,out)

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
    future=CLOCK.replace(hour=15,minute=0)
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
    with pytest.raises(RuntimeError):
        bot.live_proxy('IC',CLOCK)

@pytest.mark.parametrize('prefix',[b'x',b'_',b'$',b'not_'])
def test_exact_index_identity_cannot_be_suffix_of_another_identifier(monkeypatch,prefix):
    vendor(monkeypatch,prefix+qt())
    with pytest.raises(RuntimeError):bot.fetch_live_price_quote('IC')

@pytest.mark.parametrize('stamp',['20261021143000','20261023143000','20261022150000','20261022000000'])
def test_invalid_primary_timestamp_may_fallback_to_valid_secondary(monkeypatch,stamp):
    vendor(monkeypatch,qt(stamp=stamp),em_quote())
    out=bot.fetch_live_price_quote('IC')
    assert out['price']==pytest.approx(7435.164)
    assert '东' in out['source'] or 'eastmoney' in out['source'].lower()


def test_close_market_capture_and_replay_never_consume_realtime_index_packet(monkeypatch,tmp_path):
    import ic_im_signal_evidence_v1 as evidence
    close_clock=CLOCK.replace(hour=18,minute=0)
    vendor(monkeypatch,qt(symbol='sh000852',code='000852',price=3000.))
    history=json.loads(REAL_RAW.read_bytes().decode('gbk'))
    seen=[]
    def daily_only(url,**kw):
        seen.append(url)
        if 'CN_MarketData' in url:return response(payload=history)
        raise AssertionError('close calculation unexpectedly requested realtime quote: '+url)
    monkeypatch.setattr(bot,'_evidence_http_get',daily_only)
    monkeypatch.setattr(evidence,'_now',lambda:close_clock)
    with bot.runtime_clock(close_clock),evidence.capture_session(tmp_path,CLOCK.date(),close_clock,{'kind':'isolated_close_market_control'}) as session:
        value=bot.live_proxy('IC',close_clock)
    assert value['live_price_source']=='指数公开日线历史收盘'
    assert len(seen)==1 and 'CN_MarketData' in seen[0]
    reader=evidence.load_session(tmp_path,CLOCK.date())
    with bot.runtime_clock(close_clock),evidence.replay_session(reader):
        replayed=bot.live_proxy('IC',close_clock)
    assert evidence.encode(replayed)==evidence.encode(value)
    assert len(seen)==1 and not reader.missing_inputs
