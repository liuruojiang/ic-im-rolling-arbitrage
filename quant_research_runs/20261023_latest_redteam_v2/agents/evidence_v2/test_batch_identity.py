from datetime import date, datetime
from pathlib import Path
import json
import pandas as pd
import pytest
import requests
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

ROOT=Path(__file__).resolve().parents[4]
DAY=date(2026,10,22)
CLOCK=datetime(2026,10,22,18,tzinfo=bot.BEIJING)
ORACLE=ROOT/'quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin'
URL='https://hq.sinajs.cn/etag.php'

def response(body):
    r=requests.Response();r.status_code=200;r.encoding='gb18030';r._content=body;r._content_consumed=True
    return r

def packet():
    body=ORACLE.read_bytes()
    first=next(v.strip() for v in body.split(b';') if v.strip())+b';'
    assert b'hq_str_P_OP_mo2611P6000=' in first
    price=float(first.split(b'=')[1].strip(b';').strip(b'"').split(b',')[2])
    return first,price

def batch(prefix=b''):
    first,price=packet()
    # The first leg is a saved real MO2611-P-6000 quote. The second leg's ID
    # alone is changed to force the production batched projection path.
    second=first.replace(b'P_OP_mo2611P6000',b'P_OP_mo2611P6100')
    body=first+second
    return body.replace(b'hq_str_',prefix+b'hq_str_'),price

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k: pytest.fail('unmocked network'))
    monkeypatch.setattr(evidence,'_now',lambda:CLOCK)

@pytest.mark.parametrize('prefix',[b'x',b'not_'])
def test_direct_consumer_rejects_wrong_variable_prefix(monkeypatch,prefix):
    first,price=packet()
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k:response(first.replace(b'hq_str_',prefix+b'hq_str_')))
    with bot.runtime_clock(CLOCK),pytest.raises(RuntimeError):
        bot.verify_sina_option_quote('MO2611-P-6000',DAY,price,CLOCK)

@pytest.mark.parametrize('prefix',[b'x',b'not_'])
def test_prefetch_must_reject_wrong_variable_prefix(monkeypatch,tmp_path,prefix):
    body,_=batch(prefix)
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k:response(body))
    frame=pd.DataFrame({'instrument':['MO2611-P-6000','MO2611-P-6100']})
    with bot.runtime_clock(CLOCK),pytest.raises(RuntimeError):
        with evidence.capture_session(tmp_path,DAY,CLOCK,{'synthetic_mutation':True}) as session:
            bot._prefetch_mo_replay_verification(frame,CLOCK)

@pytest.mark.parametrize('prefix',[b'x',b'not_'])
def test_prefetch_seal_replay_must_not_launder_wrong_prefix(monkeypatch,tmp_path,prefix):
    body,price=batch(prefix)
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k:response(body))
    frame=pd.DataFrame({'instrument':['MO2611-P-6000','MO2611-P-6100']})
    with bot.runtime_clock(CLOCK),evidence.capture_session(tmp_path,DAY,CLOCK,{'synthetic_mutation':True}) as session:
        result=bot._prefetch_mo_replay_verification(frame,CLOCK)
        # Actual adapter ordinarily supplies this named provider role.
        if not session.entries: session.call('mo.replay_verification',{'day':DAY,'product':'MO'},lambda:result)
    assert session.context['complete'] is True
    reader=evidence.load_session(tmp_path,DAY)
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k:pytest.fail('replay escaped to network'))
    with bot.runtime_clock(CLOCK),evidence.replay_session(reader),pytest.raises(RuntimeError):
        bot.verify_sina_option_quote('MO2611-P-6000',DAY,price,CLOCK)


def test_legal_batch_seal_replay_is_unchanged(monkeypatch,tmp_path):
    body,price=batch()
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k:response(body))
    frame=pd.DataFrame({'instrument':['MO2611-P-6000','MO2611-P-6100']})
    with bot.runtime_clock(CLOCK),evidence.capture_session(tmp_path,DAY,CLOCK,{'synthetic_mutation':False}) as session:
        result=bot._prefetch_mo_replay_verification(frame,CLOCK)
        if not session.entries:session.call('mo.replay_verification',{'day':DAY,'product':'MO'},lambda:result)
    reader=evidence.load_session(tmp_path,DAY)
    monkeypatch.setattr(bot.requests,'get',lambda *a,**k:pytest.fail('replay escaped to network'))
    with bot.runtime_clock(CLOCK),evidence.replay_session(reader):
        result=bot.verify_sina_option_quote('MO2611-P-6000',DAY,price,CLOCK)
    assert result['lastprice']==price
    assert not reader.missing_inputs
