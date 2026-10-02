"""T-close price proxy companion for the grid Put first-layer scan (real MO only)."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import research_im_put102_mom_floor_quantity_v1 as q
from research_im_grid_put_mom2_else_valuation_v1 import RUN, TARGET, grid_schedule, half_grid, metrics
from research_im_put102_tclose_selection_open_execution_v1 import build_tclose_active, priced_engine, timing_options

ROOT = Path(__file__).resolve().parent

def main():
    if (RUN / "tclose_proxy_real_metrics.csv").exists(): raise RuntimeError("proxy output exists")
    state=pd.read_csv(q.run.BASE/'valuation_state_through_last_required_eval.csv.gz',parse_dates=['date']).set_index('date')
    upstream=pd.read_csv(q.run.BASE/'real_upstream.csv.gz',parse_dates=['date'])
    active=pd.read_csv(q.run.BASE/'real_active.csv.gz',parse_dates=['date'])
    raw=pd.read_csv(q.run.BASE/'real_options.csv.gz',parse_dates=['date','contract_month','rule_expiry','actual_expiry'])
    options=timing_options(raw); base=pd.read_csv(q.run.JOINT/'real_baseline_base.csv.gz',parse_dates=['date']); grid=pd.read_csv(q.run.JOINT/'real_baseline_grid.csv.gz',parse_dates=['date']); grid_leg=half_grid(grid)
    templates={s:pd.read_csv(q.run.FULL/f'real_dual_{s}_schedule.csv.gz',parse_dates=['eval_date','execution_date']) for s in ('core','mom')}
    selected,exceptions=build_tclose_active(active,{**templates,'grid':templates['core']}); runner=priced_engine('tclose_proxy_price','run_real_grid_tclose_proxy'); reset=q.run.engine.monthly_dates(base.date)
    legs=[]; trades=[]
    for sleeve in ('core','mom'):
        s=q.schedule(templates[sleeve],state,base.momentum_weight,'real',sleeve,3);p,t,_=runner(upstream,options,selected,s,'3m',TARGET,'proxy_'+sleeve,reset_dates=reset);p[q.run.first.FIELDS]*=.25/4;legs.append(p);trades.append(t.assign(sleeve=sleeve,leg='base'))
    s=grid_schedule(templates['core'],state,grid,'real');p,t,_=runner(upstream,options,selected,s,'3m',TARGET,'proxy_grid',reset_dates=reset);p[q.run.first.FIELDS]*=.25/4;gp=p;trades_grid=t.assign(sleeve='grid',leg='grid')
    out=[];trs=[];rows=[]
    for name,put,tt in [('grid_put_off',sum(x[q.run.first.FIELDS] for x in legs),trades),('grid_put_mom2_else_valuation',sum(x[q.run.first.FIELDS] for x in legs)+gp[q.run.first.FIELDS],trades+[trades_grid])]:
        put=put.copy();put['date']=base.date;d=q.run.comp.compose(base,put,grid_leg,None);d['candidate']=name;d['scope']='real_tclose_price_proxy';out.append(d);tr=pd.concat(tt,ignore_index=True);tr['candidate']=name;trs.append(tr);rows.extend(metrics(d,name,'real_tclose_price_proxy'))
    pd.concat(out,ignore_index=True).to_csv(RUN/'tclose_proxy_real_daily.csv.gz',index=False,compression='gzip');pd.concat(trs,ignore_index=True).to_csv(RUN/'tclose_proxy_real_trades.csv',index=False);pd.DataFrame(rows).to_csv(RUN/'tclose_proxy_real_metrics.csv',index=False)
    check={'initial_exceptions':exceptions,'call_zero':bool(pd.concat(out)[['call_pnl_ret','call_cost_rate','call_mark_fraction','call_margin_fraction','call_coverage']].abs().to_numpy().max()==0)}
    (RUN/'tclose_proxy_real_checks.json').write_text(json.dumps(check,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(pd.DataFrame(rows).query("segment=='full'").to_string(index=False));print(check)
if __name__=='__main__': main()
