"""Read-only expert-feedback probes; future listing/quotes are synthetic scenarios."""
from pathlib import Path
from datetime import date, datetime, timedelta
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
temp_root = Path(tempfile.gettempdir()) / ('icim_expert_' + uuid.uuid4().hex[:10])
temp_root.mkdir(mode=0o777)
os.environ.update(ICIM_STATE_DIR=str(temp_root/'state'), ICIM_REQUIRE_MIGRATION='0',
                  ICIM_DISABLE_INTERNAL_REFRESH='1', PYTHONDONTWRITEBYTECODE='1')
import pandas as pd
import pytest
import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_put_execution_v1 as execution

FILES = [ROOT/name for name in ['poe_ic_im_mainline_v1_4_bot.py','poe_ic_im_v1_4_server.py',
    'poe_ic_im_v1_4_state.py','ic_im_put_execution_v1.py','ic_im_v1_4_policy.py',
    'ic_im_option_maintenance_notice.py']]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
before = {p.relative_to(ROOT).as_posix():sha(p) for p in FILES}

def month_after(y,m,n=1):
    index=y*12+m-1+n
    return index//12,index%12+1

def months(day,kind):
    y,m=day.year,day.month
    rule=bot._fourth_wednesday if kind=='IC_put' else bot._third_friday
    if rule(y,m)<day:y,m=month_after(y,m)
    n=3 if kind=='IM_put' else 2
    result=[month_after(y,m,i) for i in range(n)]
    cursor=result[-1]
    for _ in range(3 if kind=='IM_put' else 2):
        cursor=month_after(*cursor)
        while cursor[1] not in (3,6,9,12):cursor=month_after(*cursor)
        result.append(cursor)
    return result

def outcome(func,*args,**kwargs):
    try:
        value=func(*args,**kwargs)
        if isinstance(value,pd.Series):value=value.get('instrument',value.get('contract'))
        elif isinstance(value,dict):value=value.get('contract',value)
        return dict(status='ok',value=str(value))
    except Exception as exc:return dict(status='blocked',type=type(exc).__name__,reason=str(exc))

timeline=[]
for stamp in pd.date_range('2026-10-15','2026-12-31',freq='D'):
    day=stamp.date()
    if not bot._is_exchange_trading_day(day):continue
    clock=datetime.combine(day,datetime.min.time()).replace(hour=16,tzinfo=bot.BEIJING)
    with bot.runtime_clock(clock):
        mo=pd.DataFrame([dict(instrument=f'MO{y%100:02d}{m:02d}-P-7000',lastprice=100.,volume=10,position=10)
                         for y,m in months(day,'IM_put')])
        future=pd.DataFrame([dict(instrument=f'IM{y%100:02d}{m:02d}',lastprice=7000.)
                             for y,m in months(day,'future')])
        icmonths=[f'{y:04d}{m:02d}' for y,m in months(day,'IC_put')]
        def chain(ym,_day):
            return pd.DataFrame([dict(contract=f'510500P{ym}M07000',security_id='10012099',
                last=.2,strike=7.,source_stamp=clock.replace(hour=15))]),dict(date=day.strftime('%Y%m%d'),time='150000',source='synthetic')
        with patch.object(bot,'fetch_sse_510500_expiries',return_value=icmonths), \
             patch.object(bot,'_fetch_510500_chain_for_day',side_effect=chain), \
             patch.object(bot,'_gov10y_for_day',return_value=.02):
            row=dict(day=day.isoformat(),synthetic_listed_mo=mo.instrument.tolist(),synthetic_ic_months=icmonths,
                im_reset=outcome(bot.select_im_put_for_reset,mo,day,7000.),
                im_independent=outcome(bot.select_independent_im_put_for_reset,mo,day,7000.),
                ic_reset=outcome(bot.select_ic_put_for_reset,day,7.,7000.,.25),
                nearest_future=outcome(bot.select_active_future,'IM',future,day),
                next_trade=outcome(bot._roll_forward_exchange_day,day+timedelta(days=1),require_official=True))
        timeline.append(row)
first={key:next((r['day'] for r in timeline if r[key]['status']=='blocked'),None)
       for key in ['im_reset','im_independent','ic_reset','nearest_future','next_trade']}

clock=datetime(2026,10,21,14,tzinfo=bot.BEIJING)
with patch.object(bot,'_sse_quote_json',return_value=dict(date='20261021',time='',
     list=[['510500','ETF',7.,0.,0.,0.,0.,0.,7.]])):
    quote=bot.fetch_sse_510500_quote()
    empty_time=outcome(bot._validate_chain_stamp_matches,'SSE empty-time',quote,date(2026,10,21),clock)
    empty_time['normalized']=quote['time']

fixture=ROOT/'tests/fixtures/icim_external_review_20261021'
raw=(fixture/'sina_december_quotes.bin').read_bytes().decode('gbk')
standard=[]
for sid,body in re.findall(r'hq_str_(\w+)="([^"\r\n]*)"',raw):
    fields=body.split(',')
    if len(fields)>46 and 'A' not in fields[37]:
        bid,last,ask=map(float,[fields[1],fields[2],fields[3]])
        standard.append(dict(id=sid,bid=bid,last=last,ask=ask,inside_current_spread=bid<=last<=ask,
                             underlying=fields[36],expiry=fields[46]))

# Actual public builder; all market rows and recovered state below are synthetic.
import test_icim_batch3_old_call_20261021 as legacy
with pytest.MonkeyPatch.context() as mp:
    anchors,quotes=legacy.wire(mp,held=False)
    bot.LIVE_CONTINUATION_ANCHOR['IM']['post_core_put_contract']='MO2701-P-7200'
    quotes.loc[quotes.instrument.eq('MO2612-P-7200'),'instrument']='MO2701-P-7200'
    quotes.attrs['listed_instruments']=list(quotes.instrument)
    public_im=outcome(bot.build_live_trade_signal,'IM',now=legacy.CLOCK,mode='close')

result=dict(scope='read-only diagnosis, not repaired-code acceptance',
    future_data='Listing sets generated from basic month rules; all future prices/rows synthetic, not actual future reports or a full ledger replay.',
    days=len(timeline),first_block_by_component=first,timeline=timeline,
    missing_sse_time=empty_time,real_sina_standard_count=len(standard),
    real_sina_last_outside_current_spread=[r for r in standard if not r['inside_current_spread']],
    real_sina_field_checks=standard,
    retry_guard=outcome(execution.require_resolved_opening_evidence,
        dict(v14_ordinary_put_open_status=execution.RETRY_OPEN_STATUS)),
    public_im_unknown_held_month=public_im,
    source_before=before,source_after={p.relative_to(ROOT).as_posix():sha(p) for p in FILES})
assert result['source_before']==result['source_after']
(HERE/'observations.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
print(json.dumps({k:v for k,v in result.items() if k not in ['timeline','real_sina_field_checks','source_before','source_after']},ensure_ascii=False,indent=2))
