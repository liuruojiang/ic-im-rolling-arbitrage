"""Corrected unified valuation debounce for both long core Put and short-Put admission."""
from __future__ import annotations

import hashlib, json, subprocess
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

import research_imc_short95_maturity_corrected_v1 as maturity
import research_imc_short95_maturity_corrected_v2 as maturity_v2
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as parity
import research_imc_current_core_put_short95_earlyvaluation_v4 as v4

ROOT=Path(__file__).resolve().parent
SPEC=ROOT/'docs'/'im_short95_unified_valuation_debounce_v2_spec.md'
RUN=ROOT/'quant_param_scan_runs'/'20260916_ic_im_im_v1_3_corrected_mixed_router_unified_long_and_short_put_valuation_debounce_confirmation_days'
PRIOR=ROOT/'quant_param_scan_runs'/'20260916_ic_im_im_v1_3_corrected_mixed_router_m_1_short95_valuation_admission_debounce_valuation_debounce_rule'
RULES=('instant','confirm2','confirm3'); IV=.35; DECAY=.60

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def git_status()->str:return subprocess.run(['git','status','--short'],cwd=ROOT,text=True,capture_output=True).stdout.strip()

def debounce_tier(raw:pd.Series, rule:str)->pd.Series:
    values=raw.fillna(4).astype(int).to_numpy(); out=np.zeros(len(values),dtype=int)
    state=int(values[0]); pending=None; count=0
    n=1 if rule=='instant' else 2 if rule=='confirm2' else 3
    for i,x in enumerate(values):
        x=int(x)
        if x>=state:
            state=x; pending=None; count=0
        elif n==1:
            state=x
        else:
            if pending!=x: pending=x; count=1
            else: count+=1
            if count>=n: state=x; pending=None; count=0
        out[i]=state
    return pd.Series(out,index=raw.index)

def valuation_inputs(dates:pd.Series,scope:str):
    if scope=='model':
        frozen=pd.read_csv(v4.EARLY_SCHEDULE,parse_dates=['eval_date','execution_date'])
        frozen=frozen[frozen.schedule_candidate.eq('reconstructed_valmom_floor3') & frozen.execution_date.isin(pd.DatetimeIndex(dates))].sort_values('execution_date')
        if len(frozen)!=len(dates): raise RuntimeError('model valuation schedule mismatch')
        raw=pd.Series(np.where(frozen.reconstructed_certified_tier.to_numpy(float)>0,frozen.reconstructed_certified_tier.to_numpy(float),frozen.valuation_tier.to_numpy(float)),index=pd.DatetimeIndex(frozen.eval_date))
        mom=pd.Series(frozen.momentum_120.to_numpy(float),index=raw.index)
        execution=pd.DatetimeIndex(frozen.execution_date)
    else:
        state=v4.effective_state().set_index('date').sort_index(); evaluation=pd.DatetimeIndex(dates.iloc[:-1]); execution=pd.DatetimeIndex(dates.iloc[1:])
        raw=state.reindex(evaluation).effective_valuation_tier.fillna(4); mom=state.reindex(evaluation).momentum_120
    return raw,mom,execution

def unified_schedule(dates:pd.Series,scope:str,rule:str,imc_mask:pd.Series|None=None):
    raw,mom,execution=valuation_inputs(dates,scope); deb=debounce_tier(raw,rule)
    floor=maturity.common.mom_floor_state(mom); parent=np.maximum(deb.to_numpy(float),np.where(floor,3,0)); factor=4 if scope=='model' else 8; target=parent*factor
    if imc_mask is not None: target=np.where(imc_mask.reindex(execution,fill_value=False).to_numpy(bool),target,0)
    return pd.DataFrame({'eval_date':raw.index,'execution_date':execution,'binary_target_qty':target.astype(int),'valuation_tier':deb.to_numpy(int),'raw_valuation_tier':raw.to_numpy(int),'momentum_120':mom.to_numpy(float),'mom120_floor_active':floor,'put_buy_allowed':True})

def unified_signal(base_signal:pd.DataFrame,dates:pd.Series,scope:str,rule:str):
    raw,mom,_=valuation_inputs(dates,scope); deb=debounce_tier(raw,rule); out=base_signal.copy(); aligned_raw=raw.reindex(pd.DatetimeIndex(out.eval_date)); aligned_deb=deb.reindex(pd.DatetimeIndex(out.eval_date)); aligned_mom=mom.reindex(pd.DatetimeIndex(out.eval_date))
    permission=aligned_deb.le(1)&aligned_mom.ge(0)&aligned_mom.notna(); out['raw_valuation_tier']=aligned_raw.to_numpy(int); out['debounced_valuation_tier']=aligned_deb.to_numpy(int); out['valuation_debounce_rule']=rule; out['short_put_permission']=permission.to_numpy(bool); out['permission_reason']=np.where(permission,'allowed','unified_valuation_debounce_or_mom120_failed'); return out

def load(scope):
    if scope=='real':return maturity.load_layer(scope)
    market,base,options,futures=maturity_v2.extended_model_inputs(); return market,base,options,None,futures

def run_layer(scope,router_fn,real_engine,model_engine,call_dir):
    market,base,options,options_for_put,futures=load(scope); parts=[]; signals=[]; cycles_all=[]; trades_all=[]; audits={}; instant=None
    base_signal=maturity.prepare_signal(base,options,'m1')
    for rule in RULES:
        signal=unified_signal(base_signal,base.date,scope,rule); routed,events,cycles=router_fn(base,options,futures,signal,IV,maturity.common.FALLBACK,DECAY); routed['date']=pd.to_datetime(routed.date); imc=routed.set_index('date').state.eq('imc'); route_dates=set(routed.loc[routed.route.eq('high_iv_permitted_short_put'),'date']); schedule=unified_schedule(base.date,scope,rule,imc)
        label=f'{scope}_m1_short95_iv35_unified_val_{rule}'
        if scope=='real': put,put_trades,_=real_engine(base,options_for_put,base,schedule,'3m',1.02,label,reset_dates=maturity.common.engine.monthly_dates(base.date),market=None,open_exit_dates=route_dates)
        else: put,put_trades,_=model_engine(market,schedule,'3m',1.02,label,reset_dates=maturity.common.engine.monthly_dates(base.date),open_exit_dates=route_dates)
        combined=maturity.common.apply_core_put(routed,maturity.common.scale_put(put,scope)); call_scale=routed.state.eq('imc').astype(float); call_daily,call_trades,_=maturity.call_inputs(scope,base.date,call_scale,f'{scope[0]}u{RULES.index(rule)}',call_dir); combined=maturity.add_call(combined,call_daily); combined['candidate']=label; parts.append(combined); signals.append(signal.assign(layer=scope,candidate=label));
        if len(cycles):cycles_all.append(cycles.assign(layer=scope,candidate=label))
        if len(put_trades):trades_all.append(put_trades.assign(layer=scope,candidate=label))
        deb=pd.Series(schedule.valuation_tier); raw=pd.Series(schedule.raw_valuation_tier); target=pd.Series(schedule.binary_target_qty)
        audits[label]={'valuation_tier_transitions':int(deb.ne(deb.shift()).sum()-1),'raw_tier_transitions':int(raw.ne(raw.shift()).sum()-1),'target_qty_transitions':int(target.ne(target.shift()).sum()-1),'core_put_trade_events':len(put_trades),'core_put_cost_rate_sum':float(combined.core_put_cost_rate.sum()),'route_switches':len(route_dates),'short_put_cycles':len(cycles),'early_rolls':int(routed.action.eq('put_early_roll60_buyback_and_sell_next_open').sum()),'call_trade_events':len(call_trades),'call_cost_rate_sum':float(combined.call_cost_rate.sum())}
        if rule=='instant':instant=combined[['date','return_net']].copy()
    return pd.concat(parts,ignore_index=True),pd.concat(signals,ignore_index=True),pd.concat(cycles_all,ignore_index=True),pd.concat(trades_all,ignore_index=True),audits,instant

def parity_prior(scope,current):
    p=pd.read_csv(PRIOR/'daily_outputs'/'daily.csv.gz',parse_dates=['date']); label=f'{scope}_m1_short95_decay60_iv35_val_instant_le1_callpaused'; p=p[p.candidate.eq(label)][['date','return_net']].sort_values('date').reset_index(drop=True); g=current.sort_values('date').reset_index(drop=True); err=float(np.max(np.abs(p.return_net.to_numpy()-g.return_net.to_numpy()))); assert p.date.equals(g.date) and err<=1e-12; return err

def main():
    meta_path=RUN/'scan_meta.json'; meta=json.loads(meta_path.read_text(encoding='utf-8')); assert meta['phase']=='init'; router_fn,router_source=parity.parity_fixed_runner(); real_engine,model_engine,put_source=maturity.common.patched_put_engines(); out=RUN/'daily_outputs'; out.mkdir(exist_ok=False); call_dir=out/'call_artifacts'
    rd,rs,rc,rt,ra,ri=run_layer('real',router_fn,real_engine,model_engine,call_dir); md,ms,mc,mt,ma,mi=run_layer('model',router_fn,real_engine,model_engine,call_dir); parity_err={'real':parity_prior('real',ri),'model':parity_prior('model',mi)}; daily=pd.concat([rd,md],ignore_index=True); summary,wide,unavailable=maturity.common.window_tables(daily); cycles=pd.concat([rc,mc],ignore_index=True); trades=pd.concat([rt,mt],ignore_index=True)
    daily.to_csv(out/'daily.csv.gz',index=False,compression='gzip'); pd.concat([rs,ms],ignore_index=True).to_csv(out/'signal_audit.csv.gz',index=False,compression='gzip'); cycles.to_csv(out/'cycles.csv',index=False); trades.to_csv(out/'core_put_trades.csv.gz',index=False,compression='gzip'); summary.to_csv(RUN/'scan_summary.csv',index=False,encoding='utf-8-sig'); wide.to_csv(RUN/'window_metrics.csv',index=False,encoding='utf-8-sig'); (RUN/'executed_state_machines.py').write_text(router_source+'\n\n'+put_source,encoding='utf-8'); full=summary[summary.segment.eq('full')]
    meta.update(scan_type='unified_long_short_put_valuation_debounce_v2',baseline={'candidate':'*_unified_val_instant','prior_parity':parity_err},candidate_grid=[{'rule':r} for r in RULES],data_snapshot={'real_start':str(rd.date.min().date()),'real_end':str(rd.date.max().date()),'model_start':str(md.date.min().date()),'model_end':str(md.date.max().date())},cost_model={'one_way_notional':maturity.common.router.ONE_WAY_COST,'reserve':.30,'cash_annual':.03,'core_put_cost_observed':True,'call_cost_observed':True},audit={'real':ra,'model':ma},unavailable_segments=unavailable,outputs={**meta['outputs'],'daily':str(out/'daily.csv.gz'),'signals':str(out/'signal_audit.csv.gz'),'cycles':str(out/'cycles.csv'),'core_put_trades':str(out/'core_put_trades.csv.gz'),'executed_state_machines':str(RUN/'executed_state_machines.py')},source_hashes={'script':sha(Path(__file__)),'spec':sha(SPEC),'early_valuation':sha(v4.EARLY)},warnings=['v1 seller-only debounce is superseded for promotion analysis.','Model layer uses theoretical options and calibrated carry.','No bid-ask, market impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing.'],decision='research_only_pending_interpretation',stability_label='unified_debounce_pending_review',git_status_after=git_status()); meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (RUN/'record.md').write_text('# IM买Put与卖Put统一估值防抖v2\n\n## Run Metadata\n\n- 纠正v1只测卖Put准入的遗漏。\n\n## Research Question\n\n- 同一估值防抖同时作用于核心买Put和卖Put准入。\n\n## Implementation Anchor\n\n- 风险升档立即、降档连续2/3日确认。\n\n## Data Snapshot\n\n- 真实与理论分层，见scan_meta。\n\n## Cost and Execution Assumptions\n\n- 单边1bp、30%缓冲、3%现金；核心Put和Call成本均重算。\n\n## Runtime Override Plan\n\n- 独立研究脚本，不改生产。\n\n## Commands\n\n```powershell\npython -X utf8 research_imc_short95_unified_valuation_debounce_v2.py\n```\n\n## Output Files\n\n- 标准参数工件及逐日、信号、周期、核心Put交易。\n\n## Full-Sample Results\n\n'+full.to_markdown(index=False)+'\n\n## Stability Classification\n\n- 待解释。\n\n## Decision\n\n- research_only_pending_interpretation\n',encoding='utf-8'); (RUN/'command_log.txt').open('a',encoding='utf-8').write(f'cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n'); print(full.to_string(index=False)); print(json.dumps({'parity':parity_err,'real':ra,'model':ma},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
