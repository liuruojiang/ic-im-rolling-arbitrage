from __future__ import annotations

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
POST = HERE / 'fresh_sources/post_20261105_20261118'
RAW = POST / 'raw_close'
EXT = HERE / 'fresh_sources/extended_inputs'
DAY = pd.Timestamp('2026-11-18')
TZ = ZoneInfo('Asia/Shanghai')
HEAD = {'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn','Cache-Control':'no-cache'}
sys.path.insert(0,str(ROOT))
import poe_ic_im_mainline_v1_4_bot as formal

def dump(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def save(path, data):
    data.to_csv(path,index=False,compression='gzip' if str(path).endswith('.gz') else None,float_format='%.12g')

def raw_get(name,url,**kwargs):
    r=requests.get(url,headers=HEAD,timeout=(6,16),**kwargs)
    r.raise_for_status()
    (RAW/name).write_bytes(r.content)
    dump(RAW/(name+'.headers.json'),{'retrieved_at':datetime.now(TZ).isoformat(),'headers':dict(r.headers),'url':r.url})
    return r

def fetch_spot():
    r=raw_get('sina_indices_etf.txt','https://hq.sinajs.cn/list=sh000905,sh000852,sh510500')
    values=dict(re.findall(r'hq_str_([A-Za-z0-9_]+)="([^"]*)"',r.content.decode('gbk',errors='replace')))
    rows=[]
    for symbol in ('sh000905','sh000852','sh510500'):
        f=values[symbol].split(',')
        if len(f)<32 or f[30]!='2026-11-18' or not '15:00:00'<=f[31]<'17:00:00':
            raise RuntimeError(f'not valid morning spot timestamp {symbol}: {f[30:32]}')
        row={'symbol':symbol,'date':DAY,'open':float(f[1]),'high':float(f[4]),'low':float(f[5]),
             'close':float(f[3]),'volume':float(f[8]),'amount':float(f[9]),'previous_close':float(f[2]),
             'source_time':f[31]}
        rows.append(row)
    frame=pd.DataFrame(rows);save(POST/'sina_indices_etf_20261118_close.csv',frame)
    return {'rows':len(frame),'quotes':rows}

def fetch_futures():
    r=raw_get('eastmoney_futures.json','https://futsseapi.eastmoney.com/list/220',params={
        'orderBy':'dm','sort':'asc','pageSize':200,'pageIndex':0,'token':formal.EASTMONEY_FUTURES_TOKEN})
    rows=[]
    for x in r.json().get('list',[]):
        if not re.fullmatch(r'(IC|IM)\d{4}',str(x.get('dm','')).upper()):continue
        t=datetime.fromtimestamp(float(x['utime']),tz=TZ)
        if t.date()!=DAY.date() or not '14:55:00'<=t.strftime('%H:%M:%S')<='15:15:00':
            raise RuntimeError(f'not noon future {x.get("dm")}: {t}')
        rows.append({'contract':str(x['dm']).upper(),'date':DAY,'open':float(x['o']),'high':float(x['h']),
                     'low':float(x['l']),'volume':float(x['vol']),'turnover':np.nan,'open_interest':float(x['ccl']),
                     'close':float(x['p']),'settle':np.nan,'pre_settle':np.nan,'source_time':t.strftime('%H:%M:%S')})
    frame=pd.DataFrame(rows)
    for product in ('IC','IM'):
        data=frame.loc[frame.contract.str.startswith(product)].copy()
        if len(data)!=4 or data.contract.duplicated().any():raise RuntimeError(f'incomplete {product} noon')
        save(POST/f'eastmoney_{product.lower()}_futures_20261118_1500.csv',data)
    return {'rows':len(frame),'quotes':frame.to_dict('records')}

def fetch_mo():
    r=raw_get('eastmoney_mo.json','https://futsseapi.eastmoney.com/list/option/221',params={
        'orderBy':'dm','sort':'asc','pageSize':20000,'pageIndex':0,'token':formal.EASTMONEY_FUTURES_TOKEN})
    rows=[]
    for x in r.json().get('list',[]):
        instrument=str(x.get('dm','')).upper()
        if not re.fullmatch(r'MO\d{4}-P-\d+',instrument):continue
        t=datetime.fromtimestamp(float(x['utime']),tz=TZ)
        if t.date()!=DAY.date() or t.strftime('%H:%M:%S')>'15:15:00':raise RuntimeError(f'not closing MO: {instrument} {t}')
        rows.append({'instrument':instrument,'openprice':x.get('o'),'lastprice':x.get('p'),
                     'bprice':x.get('mrj'),'sprice':x.get('mcj'),'volume':x.get('vol'),'position':x.get('ccl'),
                     'source_date':t.date(),'source_time':t.strftime('%H:%M:%S')})
    data=pd.DataFrame(rows)
    data=formal._live_mo_put_quote_basis(data)
    data['date']=DAY;data['settle']=np.nan;data['raw_close']=data['raw_lastprice'];data['highest']=np.nan;data['lowest']=np.nan
    if data.instrument.duplicated().any():raise RuntimeError('MO duplicate')
    save(POST/'eastmoney_mo_puts_20261118_1500.csv',data)
    save(EXT/'eastmoney_mo_puts_close.csv',data)
    return {'rows':len(data),'months':sorted(data.instrument.str[2:6].unique()),'missing_last_rows':int(data.lastprice.isna().sum())}

def fetch_etf_options():
    listed=[]
    for ym in ('2611','2612','2703','2706'):
        r=raw_get(f'sina_510500_listing_{ym}.txt','https://hq.sinajs.cn/',params={'list':f'OP_DOWN_510500{ym}'})
        listed.extend(re.findall(r'CON_OP_(\d+)',r.content.decode('gbk',errors='replace')))
    ids=sorted(set(listed))
    if not ids:raise RuntimeError('empty option listings')
    rows=[];master=[];greeks=[];missing=[]
    for offset in range(0,len(ids),25):
        batch=ids[offset:offset+25]
        r=raw_get(f'sina_510500_details_{offset:03d}.txt','https://hq.sinajs.cn/list='+','.join(prefix+s for s in batch for prefix in ('CON_OP_','CON_SO_')))
        values=dict(re.findall(r'hq_str_([A-Za-z0-9_]+)="([^"]*)"',r.content.decode('gbk',errors='replace')))
        for sid in batch:
            f=values.get('CON_OP_'+sid,'').split(',');g=values.get('CON_SO_'+sid,'').split(',')
            if len(f)<=46 or len(g)<=16:missing.append(sid);continue
            if f[43]!='M' or f[45]!='P':continue
            stamp=pd.Timestamp(f[32]);expiry=pd.Timestamp(f[46]);strike=float(f[7]);last=float(f[2]);volume=float(f[41])
            contract=f'510500P{expiry:%y%m}M{int(round(strike*1000)):05d}'
            if str(g[12])!=contract or float(g[13])!=strike:raise RuntimeError(f'Greek identity mismatch {sid}')
            fresh = (
                stamp.date() == DAY.date()
                and time(9, 15) <= stamp.time() <= time(15, 15)
                and volume > 0
            )
            matched=float(g[4])==volume and abs(float(g[14])-last)<=1e-12
            delta=float(g[5]) if fresh and matched else np.nan
            iv=float(g[9]) if fresh and matched else np.nan
            row={'security_id':sid,'date':DAY,'open':float(f[9]) if fresh and float(f[9])>0 else np.nan,
                 'high':float(f[39]) if fresh else np.nan,'low':float(f[40]) if fresh else np.nan,
                 'close':last if fresh and last>0 else np.nan,'volume':volume,'raw_close':last,
                 'source_stamp':stamp.isoformat(),'source':'Sina CON_OP current quote'}
            rows.append(row)
            master.append({'date':DAY,'security_id':sid,'contract_id':contract,'contract_month':expiry.replace(day=1),
                           'strike':strike,'delta':delta,'implied_volatility':iv})
            greeks.append({'security_id':sid,'contract_id':contract,'source_stamp':stamp.isoformat(),'same_day_positive_volume_closing_print':fresh,
                           'same_response_greeks_price_and_volume_match':matched,'delta':delta,'implied_volatility':iv,'raw_last':last})
    frame=pd.DataFrame(rows);m=pd.DataFrame(master)
    if frame.empty or frame.security_id.duplicated().any():raise RuntimeError('empty/duplicate ETF option marks')
    save(POST/'sina_510500_puts_20261118_close.csv.gz',frame)
    save(POST/'sina_510500_puts_20261118_close_greeks.csv',pd.DataFrame(greeks))
    save(POST/'sina_510500_puts_20261118_close_master.csv.gz',m)
    save(EXT/'sina_510500_puts_close.csv.gz',frame)
    return {'rows':len(frame),'fresh_rows':int(frame.close.notna().sum()),'greek_available_rows':int(m.delta.notna().sum()),'missing_ids':missing,
            'policy':'CON_OP[2] last print; same-day positive volume and timestamp in 09:15:00–15:15:00 inclusive; matching CON_SO identity/last/volume only'}

def main():
    RAW.mkdir(parents=True,exist_ok=True);EXT.mkdir(parents=True,exist_ok=True)
    manifest={'requested_mark_date':'2026-11-18','classification':'completed_session_closing_marks','sources':{},'failures':{},'started_at':datetime.now(TZ).isoformat()}
    jobs={'spot':fetch_spot,'futures':fetch_futures,'MO':fetch_mo,'ETF_options':fetch_etf_options}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks={pool.submit(fn):name for name,fn in jobs.items()}
        for task in as_completed(tasks):
            name=tasks[task]
            try:manifest['sources'][name]=task.result();print('SAVED',name,json.dumps(manifest['sources'][name],ensure_ascii=False,default=str),flush=True)
            except Exception as exc:manifest['failures'][name]=f'{type(exc).__name__}: {exc}';print('FAILED',name,manifest['failures'][name],flush=True)
            dump(POST/'noon_capture_manifest.json',manifest)
    manifest['finished_at']=datetime.now(TZ).isoformat()
    manifest['raw_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in RAW.iterdir() if p.is_file()}
    dump(POST/'noon_capture_manifest.json',manifest)
    if manifest['failures']:sys.exit(2)

if __name__=='__main__':main()
