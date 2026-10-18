import sys
sys.dont_write_bytecode=True
from pathlib import Path
import copy,json
from datetime import date
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'quant_research_runs/20261018_monthly_spreads_v1/cloud/scripts'))
if len(sys.argv)>2:
    sys.path.insert(0,str(ROOT/sys.argv[2]/'scripts'))
import pandas as pd
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
import build_ic_im_v1_4_digest as cloud
def expiry(c): return bot._third_friday(*bot._contract_month(c))
frame=pd.DataFrame({'instrument':['IC2610','IC2611','IC2612','IC2703'],'lastprice':[6100.,6075.,6000.,5900.]})
frame.attrs.update(source='saved-snapshot synthetic prices',source_date='2026-10-15')
base={'product':'IC','market_date':'2026-10-15',**q.monthly_spreads('IC',frame,date(2026,10,15),expiry)}
mutations=[('normal',lambda v:None)]
for field in ('ratio','annualized','points','near_contract','far_price'):
    mutations.append(('missing_'+field,lambda v,f=field:v.pop(f)))
for field,value in [('points',999.),('source_date','2026-10-14'),('near_contract','IM2610'),('annualized',None),('ratio',True),('same_contract',True),('expiry_gap_days',100),('far_expiry','2026-12-18')]:
    mutations.append(('wrong_'+field,lambda v,f=field,x=value:v.__setitem__(f,x)))
results=[]
def run(f):
    try:
        value=f()
        return {'no_exception':True,'first_group_na':'N/A' in value[1 if len(value)==3 else 0], 'lines':value}
    except Exception as e:return {'no_exception':False,'error':type(e).__name__+': '+str(e)}
for name,mutate in mutations:
    signal=copy.deepcopy(base)
    mutate(signal['month_next_spread'])
    before=copy.deepcopy(signal)
    results.append({'case':name,'local':run(lambda:q.format_spreads(signal)), 'cloud':run(lambda:cloud.month_spread_reasons(signal)), 'unchanged':before==signal})
output=HERE/(sys.argv[1] if len(sys.argv)>1 else 'consumer_boundary.json')
output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{'case':r['case'],'local':{k:v for k,v in r['local'].items() if k!='lines'},'cloud':{k:v for k,v in r['cloud'].items() if k!='lines'}} for r in results],ensure_ascii=True))
