"""IC short-Put valuation-only entry permission expansion (no MOM120 gate)."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import research_ic_short95_put_entry_gates_v1 as prior
import research_ic_short95_put_to_ic_recovery_v1 as real
import research_ic_short95_put_to_ic_recovery_full_model_v1 as model

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_ic_im_ic_short_95_put_to_ic_recovery_ic_short_put_valuation_only_entry_valuation_permission_tier_expansion_without_momentum_gate'
GATES=('baseline','valuation_lt195','valuation_lt200','valuation_lt205')

def permissions():
    x=prior.gates().copy()
    for suffix,level in (('195',1.95),('200',2.00),('205',2.05)):
        x[f'valuation_lt{suffix}']=(x.unbounded_median_knot.notna() & x.unbounded_median_knot.lt(level)).shift(1,fill_value=False)
    x['baseline']=True
    return x

def main():
    prior.OUT=OUT;p=permissions();p.to_csv(OUT/'entry_permissions.csv',index=False);allow={g:p.set_index('date')[g] for g in GATES}
    active,market,futures=model.model_inputs();summary=[];wide=[];all_daily=[];audit={}
    for layer,module,args in [('real',real,()),('model',model,(active,market,futures))]:
        for gate in GATES:
            fn=prior.gated_function(module,allow[gate]);d,e,c,a=fn() if layer=='real' else fn('post_assignment_ic','ic_future',*args)
            label=f'{layer}_{gate}';d['candidate']=label;e['candidate']=label;c['candidate']=label
            entries=pd.to_datetime(c.entry_date)
            if not entries.map(allow[gate]).fillna(False).all():raise RuntimeError('entry permission '+label)
            closed=c.closed.fillna(False);op=c.open_cycle_pnl.fillna(0) if 'open_cycle_pnl' in c else pd.Series(0.,index=c.index)
            err=abs(float((d.pnl-d.cost).sum()-(c.loc[closed,'realized_pnl'].sum()+op.loc[~closed].sum())))
            if err>1e-12:raise RuntimeError('ledger '+label)
            if gate=='baseline':
                ref=pd.read_csv(real.OUT/'daily.csv.gz' if layer=='real' else model.OUT/'post_assignment_ic_daily.csv.gz')
                parity=float(abs(d.return_net.to_numpy()-ref.return_net.to_numpy()).max())
                if parity>1e-12:raise RuntimeError('parity '+label)
                audit[layer+'_baseline_parity']=parity
            d.to_csv(OUT/(label+'_daily.csv.gz'),index=False,compression='gzip');e.to_csv(OUT/(label+'_events.csv'),index=False);c.to_csv(OUT/(label+'_cycles.csv'),index=False)
            summary+=prior.window(label,layer,d);all_daily.append(d);acol='physical_assignment_date' if 'physical_assignment_date' in c else 'assignment_date';audit[label]={'ledger_error':err,'cycles':len(c),'assignments':int(c[acol].notna().sum()),'ic_days':int(d.state.eq('ic_future').sum()),'idle_days':int(d.state.eq('idle').sum())}
    s=pd.DataFrame(summary);s.to_csv(OUT/'scan_summary.csv',index=False)
    for label,g in s.groupby('candidate'):
        row={'candidate':label}
        for r in g.itertuples(index=False): row['ann_return_'+r.segment]=r.ann_return;row['max_dd_'+r.segment]=r.max_dd;row['sharpe_repo_'+r.segment]=r.sharpe_repo
        wide.append(row)
    pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False);pd.concat(all_daily).to_csv(OUT/'daily.csv.gz',index=False,compression='gzip')
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(scan_type='preselected_valuation_only_tier_expansion',baseline={'real':'real_baseline','model':'model_baseline'},candidate_grid=[f'{l}_{g}' for l in ('real','model') for g in GATES],data_snapshot={'real':'2022-09-19 to 2026-08-14 actual 510500 option/ETF','model':'2015-04-16 to 2026-08-14 theoretical Put proxy plus historical IC'},cost_model={'one_way_notional':.0001,'cash_annual':.03,'risk_reserve':.30},entry_permission='previous-session valuation score only; no momentum gate; existing positions unchanged',audit=audit,unavailable_segments={f'real_{g}':{'last_10y':'real option history starts 2022-09-19','last_5y':'real option history starts 2022-09-19'} for g in GATES},decision='research_only_no_promotion',stability_label='four_preselected_valuation_permissions_no_oos')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    record='# IC卖95% Put：仅估值入场许可扩展\n\n## Data\n\n真实期权与理论Put模型严格分层，均无MOM120入场限制。\n\n## Definition\n\nbaseline无估值限制；其余仅以前一日IC无界中位风险分数允许0–1档(<1.95)、0–2档(<2.00)、0–3档(<2.05)。第4档不新卖Put。已持仓不因评分变化提前退出。\n\n## Results\n\n'+s.to_string(index=False)+'\n\n## Decision\n\nresearch_only_no_promotion\nStability: four_preselected_valuation_permissions_no_oos\n'
    (OUT/'record.md').write_text(record,encoding='utf-8');
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_ic_short95_put_valuation_tier_expansion_v1.py\n')
    print(s.to_string(index=False));print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
