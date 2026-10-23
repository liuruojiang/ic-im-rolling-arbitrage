import sys
sys.dont_write_bytecode=True
from pathlib import Path
import importlib.util,json,hashlib,copy
HERE=Path(__file__).resolve().parent
path=HERE/'baseline_b82c29d_quarter_roll.py'
spec=importlib.util.spec_from_file_location('source_guard_old_baseline',path)
baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'quant_research_runs/20261018_monthly_spreads_adversarial_v1/cloud/scripts'))
cloud_path=HERE/'baseline_0480d446_cloud_build.py'
cloud_spec=importlib.util.spec_from_file_location('source_guard_old_cloud',cloud_path)
cloud=importlib.util.module_from_spec(cloud_spec);cloud_spec.loader.exec_module(cloud)
value=dict(status='ok',near_contract='IC2610',far_contract='IC2611',near_price=6100.,far_price=6075.,points=25.,ratio=25/6100.,annualized=25/6100.*365/35,same_contract=False,near_expiry='2026-10-16',far_expiry='2026-11-20',expiry_gap_days=35,source_date='2026-10-15',source='audit fixture')
rows=[]
for name,v in [('deleted',None),('null',None),('object',{}),('empty','')]:
    x=copy.deepcopy(value)
    if name=='deleted':x.pop('source')
    else:x['source']=v
    try:
        text=baseline.format_month_spread(x,'当月－次月',product='IC',day='2026-10-15')
        rows.append({'case':name,'exception':None,'na':'N/A' in text,'text':text})
    except Exception as e:rows.append({'case':name,'exception':type(e).__name__,'message':str(e)})
    try:
        cloud_text=cloud.month_spread_reasons({'product':'IC','market_date':'2026-10-15','month_next_spread':x},'IC')[0]
        rows[-1]['cloud']={'exception':None,'na':'N/A' in cloud_text,'text':cloud_text}
    except Exception as e:rows[-1]['cloud']={'exception':type(e).__name__,'message':str(e)}
out={'immutable_strategy_ref':'b82c29d','source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'immutable_cloud_ref':'0480d446207966b3e3cc358a90b8c70953b982cf','cloud_source_sha256':hashlib.sha256(cloud_path.read_bytes()).hexdigest(),'cloud_pre_not_independently_obtained':False,'cases':rows}
(HERE/'source_guard_pre_b82c29d.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=True))
