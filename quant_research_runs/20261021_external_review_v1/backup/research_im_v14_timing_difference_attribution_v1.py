"""Attribute the difference between saved v1.4 close replay and T-signal replay."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_im_v13_full_short95_profit3x_joint_v1 as joint
import research_im_v14_mom_floor_2_vs_3_tsignal_execution_timing_v1 as x
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'quant_param_scan_runs'/'20260919_ic_im_im_v1_4_r1_im_core_and_momentum_long_put_t_close_selection_versus_t_1_close_selection_t_signal_versus_t_1_core_route_gate_floor_2_vs_3'
PUT_FIELDS=list(joint.PUT_FIELDS); FLOORS=(2,3)

def old_gate_schedule(base_dates,routed,floor):
    mask=routed.set_index('date').state.eq('imc')
    s=valuation.corrected_core_schedule(base_dates,'real',mask).copy()
    desired=np.maximum(s.valuation_tier.to_numpy(int),np.where(s.mom120_floor_active.to_numpy(bool),floor,0))
    allowed=mask.reindex(pd.DatetimeIndex(s.execution_date),fill_value=False).to_numpy(bool)
    s['binary_target_qty']=np.where(allowed,desired,0)*8
    return s

def table(daily):
    rows=[]
    for c,g in daily.groupby('candidate',sort=False):
        g=g.sort_values('date'); values=x.metrics(g.ret)
        rows.append({'candidate':c,'scope':'real','segment':'full','start':str(g.date.min().date()),'end':str(g.date.max().date()),'rows':len(g),**values})
    return pd.DataFrame(rows)

def main():
    meta_path=RUN/'scan_meta.json'; meta=json.loads(meta_path.read_text(encoding='utf-8'))
    if meta['phase']!='init': raise RuntimeError('non-init')
    weights=portfolio.current_momentum_weights(); base, base_audit=portfolio.rebuild_base('real',weights); grid=portfolio.half_grid('real')
    market,router_base,raw_options,_,futures,quarter_error=joint.quarterly_router_inputs('real',base)
    router_fn,router_source=joint.audited_router(); signal=maturity.prepare_signal(router_base,raw_options,'m1')
    routed,_,cycles=router_fn(router_base,raw_options,futures,signal,.35,common.FALLBACK,.60); routed['date']=pd.to_datetime(routed.date)
    route_dates=set(pd.to_datetime(routed.loc[routed.route.eq('high_iv_permitted_short_put'),'date']))
    call,call_trades,_=maturity.call_inputs('real',base.date,routed.state.eq('imc').astype(float),'attribution',RUN/'call_artifacts')
    upstream=x.read(portfolio.full.BASE/'real_upstream.csv.gz'); upstream=upstream[upstream.date.isin(base.date)].reset_index(drop=True)
    active=x.read(portfolio.full.BASE/'real_active.csv.gz'); active=active[active.date.isin(base.date)].reset_index(drop=True)
    raw=x.read(portfolio.full.BASE/'real_options.csv.gz',('date','contract_month','rule_expiry','actual_expiry')); options=portfolio.full.engine.with_execution_prices(raw[raw.date.isin(base.date)].reset_index(drop=True))
    resets=portfolio.full.engine.monthly_dates(base.date); real_engine,_,executed=joint.component.profit_route_engines()
    parts=[]; trades=[]; audit=[]
    for floor in FLOORS:
      for select_t, gate_t in ((False,False),(True,False),(False,True),(True,True)):
        core=x.causal_core_schedule(base.date,'real',routed,floor) if gate_t else old_gate_schedule(base.date,routed,floor)
        mom=x.momentum_schedule(base,'real',floor)
        use_active, exceptions=x.active_with_tclose_reference(active,[core,mom]) if select_t else (active,[])
        key=f"select_{'T' if select_t else 'T1'}_gate_{'T' if gate_t else 'T1'}_floor{floor}"
        cp,ct,_=real_engine(upstream,options,use_active,core,'3m',1.02,key+'_core',reset_dates=resets,profit_multiple=3.,profit_delay_sessions=1,open_exit_dates=route_dates,market=None)
        mp,mt,_=portfolio.full.engine.run_real_monthly_close(upstream,options,use_active,mom,'3m',1.02,key+'_mom',reset_dates=resets)
        cp=common.scale_put(cp,'real'); cp[PUT_FIELDS]*=.5; cp['put_cost_rate']*=joint.PUT_COST_MULTIPLIER; mp[PUT_FIELDS]*=.25/4
        daily=joint.compose_routed(base,routed,joint.combine_puts(cp,mp,joint.PUT_COST_MULTIPLIER),grid,call); daily['candidate']=key; parts.append(daily)
        t=pd.concat([ct.assign(sleeve='core'),mt.assign(sleeve='momentum')],ignore_index=True); t['candidate']=key; trades.append(t)
        audit.append({'candidate':key,'select_tclose':select_t,'core_gate_tsignal':gate_t,'initial_reference_exceptions':';'.join(exceptions),'signal_precedes_execution':bool(t.signal_eval_date.lt(t.actual_execution_date).all())})
    daily=pd.concat(parts,ignore_index=True); trade=pd.concat(trades,ignore_index=True); summary=table(daily); wide=summary.copy()
    rows=[]
    for floor in FLOORS:
      base_ret=daily[daily.candidate.eq(f'select_T1_gate_T1_floor{floor}')].set_index('date').ret
      for s,g in ((True,False),(False,True),(True,True)):
       k=f"select_{'T' if s else 'T1'}_gate_{'T' if g else 'T1'}_floor{floor}"; d=daily[daily.candidate.eq(k)].set_index('date').ret-base_ret
       rows.append({'floor':floor,'candidate':k,'vs':'select_T1_gate_T1','nonzero_days':int(d.abs().gt(1e-15).sum()),'cum_arithmetic_difference':float(d.sum()),'largest_abs_difference_date':str(d.abs().idxmax().date()),'largest_daily_difference':float(d.loc[d.abs().idxmax()])})
    out=RUN/'daily_outputs'; out.mkdir(); daily.to_csv(out/'daily.csv.gz',index=False,compression='gzip'); trade.to_csv(out/'trades.csv.gz',index=False,compression='gzip'); summary.to_csv(RUN/'scan_summary.csv',index=False,encoding='utf-8-sig'); wide.to_csv(RUN/'window_metrics.csv',index=False,encoding='utf-8-sig'); pd.DataFrame(rows).to_csv(RUN/'paired_differences.csv',index=False); pd.DataFrame(audit).to_csv(RUN/'timing_checks.csv',index=False)
    (RUN/'executed_profit_engines.py').write_text(executed,encoding='utf-8'); (RUN/'executed_router.py').write_text(router_source,encoding='utf-8')
    meta.update({'scan_type':'attribution','decision':'diagnostic_only_no_parameter_selection','stability_label':'not_applicable','data_snapshot':{'real':[str(daily.date.min().date()),str(daily.date.max().date())]},'outputs':{**meta['outputs'],'daily':str(out/'daily.csv.gz'),'trades':str(out/'trades.csv.gz'),'paired_differences':str(RUN/'paired_differences.csv'),'timing_checks':str(RUN/'timing_checks.csv')},'warnings':['T+1 close remains the transaction price in every candidate. This isolates strike reference and core route-gate date only.']})
    meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (RUN/'record.md').write_text('# T+1 收盘差异归因\n\n## Data\n\n真实上市 IM/MO 数据。\n\n## Decision\n\n`diagnostic_only_no_parameter_selection`。\n\n## Stability\n\n`not_applicable`。\n\n'+summary.to_markdown(index=False,floatfmt='.6f')+'\n',encoding='utf-8')
    (RUN/'command_log.txt').write_text('python -X utf8 research_im_v14_timing_difference_attribution_v1.py\n',encoding='utf-8')
    print(summary.to_string(index=False))
if __name__=='__main__': main()
