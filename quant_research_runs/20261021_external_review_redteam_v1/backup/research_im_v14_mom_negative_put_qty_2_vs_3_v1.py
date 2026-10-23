"""v1.4-r1 full-joint scan: MOM120 long-Put floor 2 vs 3."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_im_v14_put_monthly_roll_timing_v1 as t

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'quant_param_scan_runs'/'20260919_ic_im_im_v1_4_r1_full_joint_im_core_and_momentum_long_put_mom120_floor_2_vs_3'
BASE=ROOT/'quant_param_scan_runs'/'20260917_ic_im_im_v1_4_r1_current_joint_im_core_and_momentum_long_put_monthly_maintenance_sync_put_with_im_quarter_roll_else_t0'

def core(scope,base,market,resets,route,mask,re,me,floor):
 s=t.valuation.corrected_core_schedule(base.date,scope,mask).copy(); active=s.mom120_floor_active.to_numpy(bool)
 desired=np.maximum(s.valuation_tier.to_numpy(int),np.where(active,floor,0)); allowed=mask.reindex(pd.DatetimeIndex(s.execution_date),fill_value=False).to_numpy(bool); desired=np.where(allowed,desired,0); s.binary_target_qty=desired*(4 if scope=='model' else 8)
 kw=dict(reset_dates=resets,profit_multiple=3.,profit_delay_sessions=1,open_exit_dates=route)
 if scope=='real':
  up=t.read(t.portfolio.full.BASE/'real_upstream.csv.gz'); up=up[up.date.isin(base.date)].reset_index(drop=True); ac=t.read(t.portfolio.full.BASE/'real_active.csv.gz'); ac=ac[ac.date.isin(base.date)].reset_index(drop=True); op=t.portfolio.full.engine.with_execution_prices(t.read(t.portfolio.full.BASE/'real_options.csv.gz',('date','contract_month','rule_expiry','actual_expiry')));op=op[op.date.isin(base.date)].reset_index(drop=True); p,tr,_=re(up,op,ac,s,'3m',1.02,f'{scope}_core_f{floor}',market=None,**kw)
 else:p,tr,_=me(market,s,'3m',1.02,f'{scope}_core_f{floor}',**kw)
 p=t.common.scale_put(p,scope);p[t.PUT_FIELDS]*=.5;p.put_cost_rate*=t.PUT_COST_MULTIPLIER;return p,tr
def mom(scope,base,resets,floor):
 s=t.read(t.portfolio.ARTIFACT/f'{scope}_combined_mom_schedule.csv.gz',('eval_date','execution_date'));s=s[s.execution_date.le(t.portfolio.END)].reset_index(drop=True);state=t.read(t.portfolio.full.BASE/'valuation_state_through_last_required_eval.csv.gz').set_index('date');m=s.eval_date.map(state.momentum_120);q=np.where(m.lt(0),floor,0)*2*base.momentum_weight.to_numpy(float)*(4);s.binary_target_qty=np.rint(q).astype(int);s.three_tier_target_qty=s.binary_target_qty;s.put_buy_allowed=True
 if scope=='model': market=t.read(t.portfolio.full.BASE/'model_market.csv.gz');market=market[market.date.le(t.portfolio.END)].reset_index(drop=True);p,tr,_=t.portfolio.full.engine.run_model_monthly_close(market,s,'3m',1.02,f'{scope}_mom_f{floor}',reset_dates=resets);scale=.25/4
 else: up=t.read(t.portfolio.full.BASE/'real_upstream.csv.gz');up=up[up.date.isin(base.date)].reset_index(drop=True);ac=t.read(t.portfolio.full.BASE/'real_active.csv.gz');ac=ac[ac.date.isin(base.date)].reset_index(drop=True);op=t.portfolio.full.engine.with_execution_prices(t.read(t.portfolio.full.BASE/'real_options.csv.gz',('date','contract_month','rule_expiry','actual_expiry')));op=op[op.date.isin(base.date)].reset_index(drop=True);p,tr,_=t.portfolio.full.engine.run_real_monthly_close(up,op,ac,s,'3m',1.02,f'{scope}_mom_f{floor}',reset_dates=resets);scale=.25/4
 p[t.PUT_FIELDS]*=scale;return p,tr
def main():
 if (RUN/'scan_summary.csv').exists():raise RuntimeError('exists')
 re,me,_=t.joint.component.profit_route_engines();rf,_=t.joint.audited_router();w=t.portfolio.current_momentum_weights();ref=t.read(BASE/'daily_outputs'/'daily.csv.gz');all=[];checks={}
 for scope in ('model','real'):
  b,_=t.portfolio.rebuild_base(scope,w);g=t.portfolio.half_grid(scope);market,rb,op,_,fu,_=t.joint.quarterly_router_inputs(scope,b);sig=t.maturity.prepare_signal(rb,op,'m1');r,_,_=rf(rb,op,fu,sig,.35,t.common.FALLBACK,.60);r.date=pd.to_datetime(r.date);route=set(r.loc[r.route.eq('high_iv_permitted_short_put'),'date']);mask=r.set_index('date').state.eq('imc');resets,_=t.shifted_reset_dates(b.date,0);call,_,_=t.maturity.call_inputs(scope,b.date,r.state.eq('imc').astype(float),f'{scope}floor',RUN/'call_artifacts')
  for f in (2,3):
   c,_=core(scope,b,market,resets,route,mask,re,me,f);m,_=mom(scope,b,resets,f);total=t.joint.combine_puts(c,m,t.PUT_COST_MULTIPLIER);d=t.joint.compose_routed(b,r,total,g,call);d['candidate']=f'{scope}_floor{f}';d['scope']=scope;d['sessions_before']=0;all.append(d)
   if f==3:
    saved=ref[ref.candidate.eq(f'{scope}_current_T0')].sort_values('date');checks[f'{scope}_floor3_v14_parity']=float(np.max(np.abs(d.ret.to_numpy()-saved.ret.to_numpy())))
 daily=pd.concat(all,ignore_index=True);summary,wide,un=t.build_metrics(daily);summary.to_csv(RUN/'scan_summary.csv',index=False);wide.to_csv(RUN/'window_metrics.csv',index=False);daily.to_csv(RUN/'daily.csv.gz',index=False,compression='gzip');(RUN/'checks.json').write_text(json.dumps(checks,indent=2),encoding='utf8');meta=json.loads((RUN/'scan_meta.json').read_text(encoding='utf8'));meta.update(phase='complete',cost_model={'baseline':'v1.4-r1 full joint','moneyness':1.02},unavailable_segments=un,decision='pending_research_judgment',stability_label='v14_mom_floor_2_vs_3');(RUN/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8');print(summary[summary.segment.eq('full')][['scope','candidate','ann_return','sharpe_repo','max_dd']].to_string(index=False))
if __name__=='__main__':main()
