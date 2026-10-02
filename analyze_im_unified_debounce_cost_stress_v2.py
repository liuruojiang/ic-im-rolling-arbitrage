"""Post-run core-Put cost sensitivity for unified valuation debounce v2."""
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'quant_param_scan_runs'/'20260916_ic_im_im_v1_3_corrected_mixed_router_unified_long_and_short_put_valuation_debounce_confirmation_days'
BPS=(1,5,10,20,25,30,40)

def metrics(ret):
    ret=pd.Series(ret,dtype=float); nav=(1+ret).cumprod(); years=len(ret)/252
    return {'ann_return':float(nav.iloc[-1]**(1/years)-1),'ann_vol':float(ret.std(ddof=1)*np.sqrt(252)),'sharpe_repo':float(ret.mean()/ret.std(ddof=1)*np.sqrt(252)),'max_dd':float((nav/nav.cummax()-1).min()),'total_return':float(nav.iloc[-1]-1)}

d=pd.read_csv(RUN/'daily_outputs'/'daily.csv.gz')
rows=[]
for candidate,g in d.groupby('candidate',sort=False):
    for bps in BPS:
        adjusted=g.return_net.astype(float)-g.core_put_cost_rate.astype(float)*(bps-1)
        rows.append({'candidate':candidate,'layer':candidate.split('_',1)[0],'core_put_one_way_bps':bps,**metrics(adjusted)})
out=pd.DataFrame(rows); out.to_csv(RUN/'core_put_cost_stress.csv',index=False,encoding='utf-8-sig')
print(out[['candidate','core_put_one_way_bps','ann_return','sharpe_repo','max_dd']].to_string(index=False))
