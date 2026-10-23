"""Bounded anonymous public-feed survey; no production source/ledger edits."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BEIJING = ZoneInfo('Asia/Shanghai')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    assert not p.exists(), p
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def ref_params(name, function):
    tree = ast.parse((HERE/'reference'/name).read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==function)
    assignment = next(n for n in node.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='params' for x in n.targets))
    return ast.literal_eval(assignment.value)


def specs(day):
    fields = 'f1,f2,f3,f4,f5,f6,f12,f13,f14,f17,f18,f59,f86,f124,f161,f298,f299,f301,f302,f303,f330,f331,f332,f333,f334,f335,f336'
    em = ref_params('option_em.py.txt','option_current_em')
    em.update(fs='m:10',pz='1000',pn='1',fid='f12',po='0',fields=fields)
    master = ref_params('option_current_sse.py.txt','option_current_day_sse')
    risk = ref_params('option_risk_analysis_em.py.txt','option_risk_analysis_em')
    risk.update(pz='1000',fields=fields)
    quote = {'fields':'f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86,f301,f302,f330,f331,f332,f333,f334,f335,f336','invt':'2','fltt':'2'}
    return [
        ('em_option_list','Eastmoney','https://23.push2.eastmoney.com/api/qt/clist/get',em),
        ('em_option_risk','Eastmoney','https://push2.eastmoney.com/api/qt/clist/get',risk),
        ('em_core_quote','Eastmoney','https://push2.eastmoney.com/api/qt/stock/get',{**quote,'secid':'10.10012149'}),
        ('em_etf_quote','Eastmoney','https://push2.eastmoney.com/api/qt/stock/get',{**quote,'secid':'1.510500'}),
        ('sse_master_https','SSE','https://query.sse.com.cn/commonQuery.do',master),
        ('sse_master_documented_http','SSE','http://query.sse.com.cn/commonQuery.do',master),
        ('tencent_etf','Tencent','https://qt.gtimg.cn/q=sh510500',None),
        ('sina_etf','Sina','https://hq.sinajs.cn/',{'list':'sh510500'}),
        ('sina_core_single','Sina','https://hq.sinajs.cn/',{'list':'CON_OP_10012149'}),
        ('sina_core_path','Sina','https://hq.sinajs.cn/list=CON_OP_10012149',None),
        ('sina_core_identity','Sina','https://hq.sinajs.cn/list=CON_SO_10012149',None),
        ('sina_december_put_ids','Sina','https://hq.sinajs.cn/',{'list':'OP_DOWN_5105002612'}),
        ('sina_listed_months','Sina','https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName',{'exchange':'null','cate':'500ETF'}),
        ('sohu_etf_history','Sohu','https://q.stock.sohu.com/hisHq',{'code':'cn_510500','start':day.strftime('%Y%m%d'),'end':day.strftime('%Y%m%d'),'stat':'1','order':'D','period':'d'}),
        ('ths_option_page','THS','https://q.10jqka.com.cn/option/',None),
        ('sse_chain_primary','SSE','https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12',{'select':'contractid,last,chg_rate,presetpx,exepx,code','order':'contractid,ase'}),
        ('sse_chain_documented_http','SSE','http://yunhq.sse.com.cn:32041/v1/sho/list/tstyle/510500_12',{'select':'contractid,last,chg_rate,presetpx,exepx,code','order':'contractid,ase'}),
    ]


def summarize(name, body, encoding):
    txt = body.decode(encoding or 'utf-8',errors='replace')
    try:
        data = json.loads(txt)
    except ValueError:
        if name.startswith(('sina_','tencent_')):
            match = re.search(r'="([^"]*)"',body.decode('gb18030',errors='replace'))
            fields = match.group(1).split('~' if name=='tencent_etf' else ',') if match else []
            return {'format':'vendor_text','field_count':len(fields),'fields':fields,
                    'listed_security_ids':re.findall(r'CON_OP_([0-9]{8})',txt)}
        return {'format':'non_json','prefix':txt[:200]}
    result = {'format':'json','type':type(data).__name__}
    if isinstance(data,dict):
        result['top_keys'] = list(data)
        nested = data.get('data')
        if isinstance(nested,dict):
            result['data_keys'] = list(nested)
            if 'diff' in nested:
                rows = nested.get('diff') or []
                if isinstance(rows,dict):
                    rows = list(rows.values())
                result.update(total=nested.get('total'),returned=len(rows),first_rows=rows[:2],
                              held_rows=[r for r in rows if str(r.get('f12'))=='10012149'])
            else:
                result['data'] = nested
        rows = data.get('result')
        if isinstance(rows,list):
            ours = [r for r in rows if isinstance(r,dict) and str(r.get('CONTRACT_ID','')).replace(' ','').startswith('510500')]
            result.update(total_rows=len(rows),underlying_rows=len(ours),first_rows=ours[:2],
                held_rows=[r for r in ours if str(r.get('SECURITY_ID'))=='10012149'])
        if name=='sina_listed_months':
            result['payload'] = data
        if name.startswith('sse_chain'):
            result['date'],result['time'] = data.get('date'),data.get('time')
            result['returned'] = len(data.get('list') or [])
            result['first_rows'] = (data.get('list') or [])[:2]
    else:
        result['preview'] = data[:1] if isinstance(data,list) else data
    return result


def probe(run, spec):
    name,family,url,params = spec
    started = time.monotonic()
    row = {'name':name,'family':family,'url':url,'params':params,'started_at':datetime.now(BEIJING).isoformat()}
    response = None
    try:
        response = requests.get(url,params=params,headers={
            'User-Agent':'Mozilla/5.0 ICIM-free-source-research/1.0',
            'Referer':'https://www.sse.com.cn/' if family=='SSE' else 'https://stock.finance.sina.com.cn/' if family=='Sina' else 'https://quote.eastmoney.com/' if family=='Eastmoney' else 'https://stockapp.finance.qq.com/',
        },timeout=(4,6),stream=True)
        chunks,total = [],0
        for chunk in response.iter_content(65536):
            if time.monotonic()-started>15:
                raise TimeoutError('survey total request time limit')
            total += len(chunk)
            if total>12*1024*1024:
                raise ValueError('survey response exceeds 12 MiB')
            chunks.append(chunk)
        body = b''.join(chunks)
        raw = run/'raw'/f'{name}.bin'
        raw.write_bytes(body)
        row.update(status='RECEIVED',http_status=response.status_code,bytes=len(body),
            sha256=sha(raw),encoding=response.encoding,
            headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers},
            summary=summarize(name,body,response.encoding))
    except Exception as exc:
        row.update(status='FAILED',error_type=type(exc).__name__)
    finally:
        if response is not None:
            response.close()
    row['seconds'] = round(time.monotonic()-started,3)
    save(run/(name+'.json'),row)
    print(json.dumps({k:row[k] for k in ['name','status','seconds']}|{'http':row.get('http_status'),'error':row.get('error_type')},ensure_ascii=False),flush=True)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage',default='probe_01')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+',args.stage):
        raise ValueError('invalid stage')
    run = HERE/args.stage
    run.mkdir(exist_ok=False)
    (run/'raw').mkdir()
    before = {p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'runtime').rglob('*') if p.is_file()}
    sources = {p.name:sha(p) for p in [ROOT/'poe_ic_im_mainline_v1_4_bot.py',ROOT/'ic_im_evidence_adapter_v1.py',ROOT/'ic_im_signal_evidence_v1.py']}
    spec = ROOT/'docs/ic_im_free_source_survey_20261022_v1_spec.md'
    spec_sha = spec.with_suffix('.md.sha256')
    if not spec_sha.exists():
        spec_sha.write_text(sha(spec)+'  '+spec.name+'\n',encoding='utf-8')
    save(run/'baseline.json',{'runtime':before,'sources':sources,'spec_sha256':sha(spec),'probe_source_sha256':sha(Path(__file__))})
    now = datetime.now(BEIJING)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(probe,run,s) for s in specs(now.date())]
        rows = [p.result() for p in as_completed(pending)]
    after = {p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'runtime').rglob('*') if p.is_file()}
    assert before==after
    assert all(sha(ROOT/n)==d for n,d in sources.items())
    save(run/'receipt.json',{'actual_day':str(now.date()),'requests':len(rows),'families':sorted({r['family'] for r in rows}),
        'received':sum(r['status']=='RECEIVED' for r in rows),'rows':sorted(rows,key=lambda r:r['name']),
        'formal_runtime_unchanged':True,'production_sources_unchanged':True,
        'warning':'A received response is not by itself an admissible production quote or complete chain.'})


if __name__=='__main__':
    main()
