"""Pre-registered IC short-95-Put entry gates: valuation, MOM120, and both."""
from __future__ import annotations
import inspect, json
from pathlib import Path
import numpy as np
import pandas as pd
import ic_roll_momentum_stage2_put_v2 as ic_put
import ic_510500_put_proxy_validation_v1 as proxy
import research_ic_short95_put_to_ic_recovery_v1 as real
import research_ic_short95_put_to_ic_recovery_full_model_v1 as model

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_ic_im_ic_short_95_put_to_ic_recovery_ic_short_put_entry_permission_valuation_risk_and_mom120_entry_gates'
SCORE=ROOT/'outputs/ic_fixed_valuation_unbounded_score_v6/daily_unbounded_fixed_scores.csv.gz'
GATES=('baseline','valuation_le195','mom120_nonnegative','both')

def gated_function(source_module, allowed):
    source=inspect.getsource(source_module.run)
    old='if state == "idle" and i > 0 and not action:'
    new='if state == "idle" and i > 0 and not action and bool(allowed.get(day, False)):'
    if old not in source: raise RuntimeError('Entry hook missing')
    source=source.replace(old,new)
    source=source.replace('daily, cycle_frame = pd.DataFrame(rows), pd.DataFrame(cycles)', 'daily, cycle_frame = pd.DataFrame(rows), pd.DataFrame(cycles)\n    if "open_cycle_pnl" not in cycle_frame:\n        cycle_frame["open_cycle_pnl"] = np.nan')
    namespace=dict(vars(source_module)); namespace['allowed']=allowed
    exec(compile(source,str(OUT/'gated_state_machine.py'),'exec'),namespace)
    (OUT/'gated_state_machine.py').write_text(source,encoding='utf-8')
    return namespace['run']

def gates() -> pd.DataFrame:
    frames,*_=ic_put.v1.put_engine.v19.v18.load_close_inputs()
    ic=frames['ic'][['date','csi500_tri_close']].copy()
    score=pd.read_csv(SCORE,parse_dates=['date'])[['date','unbounded_median_knot']]
    x=ic.merge(score,on='date',how='left',validate='one_to_one')
    x['mom120']=x.csi500_tri_close.pct_change(120)
    x['baseline']=True
    # IC score tiers are bounded by 1.90/1.95/2.00/2.05.  <=1.95 is
    # the low-risk 0/1-tier analogue of IM's retained entry permission.
    x['valuation_le195']=x.unbounded_median_knot.notna() & x.unbounded_median_knot.le(1.95)
    x['mom120_nonnegative']=x.mom120.notna() & x.mom120.ge(0)
    x['both']=x.valuation_le195 & x.mom120_nonnegative
    x.loc[:,list(GATES)]=x.loc[:,list(GATES)].shift(1,fill_value=False)
    return x

def window(candidate, layer, daily):
    rows=[]
    for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
        cut=daily.date.min() if years is None else daily.date.max()-pd.DateOffset(years=years)
        ok=years is None or daily.date.min()<=cut
        sub=daily[daily.date>=cut] if ok else daily.iloc[:0]
        m=proxy.metrics(sub.return_net) if ok else {k:'N/A' for k in ('total_return','ann_return','ann_vol','sharpe_repo','max_dd')}
        rows.append(dict(candidate=candidate,layer=layer,segment=segment,start=str(sub.date.min().date()) if ok else '',end=str(sub.date.max().date()) if ok else '',rows=len(sub),**m))
    return rows

def main():
    permission=gates(); permission.to_csv(OUT/'entry_permissions.csv',index=False)
    bydate={gate:permission.set_index('date')[gate] for gate in GATES}
    active,market,futures=model.model_inputs()
    outputs=[]; audit={}; summary=[]; wide=[]
    for layer, module, args in [('real',real,()),('model',model,(active,market,futures))]:
        for gate in GATES:
            fn=gated_function(module,bydate[gate])
            if layer=='real': d,e,c,a=fn()
            else: d,e,c,a=fn('post_assignment_ic', 'ic_future', *args)
            label=f'{layer}_{gate}'; d['candidate']=label; e['candidate']=label; c['candidate']=label
            entries=pd.to_datetime(c.entry_date)
            if not entries.map(bydate[gate]).fillna(False).all(): raise RuntimeError(f'entry permission failed {label}')
            error=abs(float((d.pnl-d.cost).sum()-(c.loc[c.closed.fillna(False),'realized_pnl'].sum()+c.loc[~c.closed.fillna(False),'open_cycle_pnl'].fillna(0).sum())))
            if error>1e-12: raise RuntimeError(f'ledger {label} {error}')
            if gate=='baseline':
                ref=pd.read_csv(real.OUT/'daily.csv.gz' if layer=='real' else model.OUT/'post_assignment_ic_daily.csv.gz')
                parity=float(abs(d.return_net.to_numpy()-ref.return_net.to_numpy()).max())
                if parity>1e-12: raise RuntimeError(f'baseline parity {layer} {parity}')
                audit[layer+'_baseline_parity']=parity
            d.to_csv(OUT/(label+'_daily.csv.gz'),index=False,compression='gzip');e.to_csv(OUT/(label+'_events.csv'),index=False);c.to_csv(OUT/(label+'_cycles.csv'),index=False)
            assignment_col='assignment_date' if 'assignment_date' in c else 'physical_assignment_date'
            summary+=window(label,layer,d); audit[label]={'ledger_error':error,'cycles':len(c),'assignments':int(c[assignment_col].notna().sum()),'entry_cycles':len(c),'ic_days':int(d.state.eq('ic_future').sum()),'idle_days':int(d.state.eq('idle').sum())};outputs.append(d)
    s=pd.DataFrame(summary);s.to_csv(OUT/'scan_summary.csv',index=False)
    for label,g in s.groupby('candidate'):
        item={'candidate':label}
        for r in g.itertuples(index=False):
            item['ann_return_'+r.segment]=r.ann_return;item['max_dd_'+r.segment]=r.max_dd;item['sharpe_repo_'+r.segment]=r.sharpe_repo
        wide.append(item)
    pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False);pd.concat(outputs).to_csv(OUT/'daily.csv.gz',index=False,compression='gzip')
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    meta.update(scan_type='preselected_entry_gate_ablation',baseline={'real':'real_baseline','model':'model_baseline'},candidate_grid=[f'{x}_{g}' for x in ('real','model') for g in GATES],data_snapshot={'real':'2022-09-19 to 2026-08-14 actual 510500 Put/ETF','model':'2015-04-16 to 2026-08-14 theoretical Put/ETF proxy plus historical IC','score':'unbounded_median_knot daily score; MOM120 from CSI500 TRI'},cost_model={'one_way_notional':.0001,'cash_annual':.03,'risk_reserve':.30},audit=audit,entry_permission='previous-session signal; only new Put entry gated, existing positions do not exit on gate change',unavailable_segments={f'real_{g}':{'last_10y':'real 510500 option history starts 2022-09-19','last_5y':'real 510500 option history starts 2022-09-19'} for g in GATES},decision='research_only_no_promotion',stability_label='four_preselected_gates_no_oos')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    record='# IC卖95% Put入场估值与动量门控\n\n## Data\n\n真实层2022-09-19至2026-08-14；模型层2015-04-16至2026-08-14。模型Put/ETF为代理，不与真实混合。\n\n## Definition\n\n仅新卖Put受前一日许可控制：baseline无筛选；valuation_le195为IC无界中位估值风险分数≤1.95，即低风险0/1档；mom120_nonnegative为中证500全收益MOM120≥0；both两者同时满足。高估值风险分或负动量均不新卖，已持有的Put、交割后IC和回本退出规则不变。\n\n## Results\n\n'+s.to_string(index=False)+'\n\n## Decision\n\nresearch_only_no_promotion\nStability: four_preselected_gates_no_oos\n'
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_ic_short95_put_entry_gates_v1.py\n')
    print(s.to_string(index=False));print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
