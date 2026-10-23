import sys
sys.dont_write_bytecode=True
from pathlib import Path
import copy,json,hashlib
from datetime import date
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'quant_research_runs/20261018_monthly_spreads_v1/cloud/scripts'))
sys.path.insert(0,str(ROOT/'quant_research_runs/20261018_monthly_spreads_adversarial_v1/cloud/scripts'))
import pandas as pd
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
import build_ic_im_v1_4_digest as cloud
def expiry(c):return bot._third_friday(*bot._contract_month(c))
KEYS=('status','near_contract','far_contract','near_price','far_price','points','ratio','annualized','same_contract','source_date','source','near_expiry','far_expiry','expiry_gap_days')
VALUES=[None,True,False,0,1,0.0,1.0,'',' ','0','2026-10-15',[],{},['x'],{'a':'x'}]
rows=[]
def render(f):
    try:
        s=f()
        return {'exception':None,'na':'N/A' in s,'text':s}
    except Exception as e:return {'exception':type(e).__name__,'message':str(e),'na':False}
for product in ('IC','IM'):
  for day_s,months,spread_key in [('2026-10-15',['2610','2611','2612','2703'],'month_next_spread'),('2026-10-15',['2610','2611','2612','2703'],'month_quarter_spread'),('2026-12-10',['2612','2701','2703','2706'],'month_quarter_spread')]:
    day=date.fromisoformat(day_s)
    frame=pd.DataFrame({'instrument':[product+m for m in months],'lastprice':[6100.,6075.,6000.,5900.]})
    frame.attrs.update(source='audit fixture',source_date=day_s)
    original=q.monthly_spreads(product,frame,day,expiry)[spread_key]
    label=q.MONTH_SPREAD_LABELS[spread_key]
    for key in KEYS:
      for name, replacement in [('delete',None)]+[(f'type_{type(v).__name__}_{i}',v) for i,v in enumerate(VALUES)]:
        value=copy.deepcopy(original)
        if name=='delete':value.pop(key)
        else:value[key]=replacement
        signal={'product':product,'market_date':day_s,spread_key:value}
        cloud_index=0 if spread_key=='month_next_spread' else 1
        local=render(lambda:q.format_month_spread(value,label,product=product,day=day_s))
        remote=render(lambda:cloud.month_spread_reasons(signal,product)[cloud_index])
        rows.append({'product':product,'day':day_s,'pair':spread_key,'key':key,'mutation':name,'replacement':replacement,'local':local,'cloud':remote})
    for i,value in enumerate(VALUES):
        signal={'product':product,'market_date':day_s,spread_key:value}
        cloud_index=0 if spread_key=='month_next_spread' else 1
        local=render(lambda:q.format_month_spread(value,label,product=product,day=day_s))
        remote=render(lambda:cloud.month_spread_reasons(signal,product)[cloud_index])
        rows.append({'product':product,'day':day_s,'pair':spread_key,'key':'__whole_value__','mutation':f'type_{type(value).__name__}_{i}','replacement':value,'local':local,'cloud':remote})
out={'cases':len(rows),'exceptions':[r for r in rows if r['local']['exception'] or r['cloud']['exception']],
     'deleted_but_not_na':[r for r in rows if r['mutation']=='delete' and (not r['local']['na'] or not r['cloud']['na'])],
     'source_nontext_accepted':[r for r in rows if r['key']=='source' and r['mutation']!='delete' and (not isinstance(r['replacement'],str) or not r['replacement'].strip()) and (not r['local']['na'] or not r['cloud']['na'])],
     'different_outcome':[r for r in rows if r['local']['exception']!=r['cloud']['exception'] or r['local']['na']!=r['cloud']['na']],
     'source_sha':{str(Path(m.__file__).relative_to(ROOT)):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (q,cloud)},'rows':rows}
path=HERE/(sys.argv[1] if len(sys.argv)>1 else 'formatter_key_type_matrix.json')
path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in out.items() if k!='rows'},ensure_ascii=True))
