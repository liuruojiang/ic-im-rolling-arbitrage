"""Research-only IM MOM120 floor hysteresis scan using the validated Put engine."""
from pathlib import Path
import sys, json, hashlib
import numpy as np
import pandas as pd
from im_put_maturity_valuation_tiers_v3 import metrics

ROOT=Path(__file__).resolve().parent
ARTIFACT=ROOT/'quant_param_scan_runs/20260908_im_mom120_put102_combined_v1'
BASE=ARTIFACT
OUT=ROOT/'quant_param_scan_runs/20260915_ic_im_im_v1_3_r7_core_put_mom120_floor_im_core_put_120_day_absolute_momentum_floor_mom120_asymmetric_exit_hysteresis'
sys.path.insert(0,str(BASE)); import run_combined as im
BASE=im.BASE
END=pd.Timestamp('2026-08-14')
VARIANTS={'baseline_neg0':(0.0,0.0,1),'release_pos0_2d':(0.0,0.0,2),'release_pos1_2d':(0.0,.01,2),'release_pos1_3d':(0.0,.01,3)}

def gate(mom, enter, release, days):
    # Protection enters immediately at <= enter.  It only releases after N
    # consecutive observations strictly above the positive release boundary.
    active=False; streak=0; out=[]
    for x in mom:
        if not np.isfinite(x): active=True; streak=0
        elif x<=enter: active=True; streak=0
        elif active:
            streak=streak+1 if x>release else 0
            if streak>=days: active=False; streak=0
        out.append(active)
    return np.asarray(out,bool)

def read(path,dates=('date',)):
    return pd.read_csv(path,parse_dates=list(dates),low_memory=False)

def main():
    market=read(BASE/'model_market.csv.gz'); up=read(BASE/'real_upstream.csv.gz'); active=read(BASE/'real_active.csv.gz')
    options=im.engine.with_execution_prices(read(BASE/'real_options.csv.gz',('date','contract_month','rule_expiry','actual_expiry')))
    state=read(BASE/'valuation_state_through_last_required_eval.csv.gz').set_index('date'); mom=state.tri_close.pct_change(120,fill_method=None)
    sources=[ARTIFACT/'model_combined_core_schedule.csv.gz',ARTIFACT/'real_combined_core_schedule.csv.gz',ARTIFACT/'model_combined_core_put.csv.gz',ARTIFACT/'real_combined_core_put.csv.gz']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    rows=[]; wide=[]; daily=[]; parity={}
    for name,(enter,release,days) in VARIANTS.items():
      w={'candidate':name,'enter_threshold':enter,'release_threshold':release,'confirmation_days':days}
      for scope in ['model','real']:
        b=read(ARTIFACT/f'{scope}_fixed_base.csv.gz'); schedule=read(ARTIFACT/f'{scope}_combined_core_schedule.csv.gz',('eval_date','execution_date'))
        signal=schedule.eval_date.map(mom); on=gate(signal,enter,release,days)
        parent=np.maximum(schedule.eval_date.map(state.valuation_tier).fillna(0).to_numpy(),np.where(on,3,0))
        factor=1 if scope=='model' else 2; schedule['binary_target_qty']=(parent*factor*4).astype(int); schedule['three_tier_target_qty']=schedule.binary_target_qty
        schedule['mom120']=signal; schedule['mom120_floor_active']=on
        label=f'{scope}_{name}'
        if scope=='model': put,_,_=im.engine.run_model_monthly_close(market,schedule,'3m',1.02,label,reset_dates=im.engine.monthly_dates(b.date)); norm=.5/4
        else: put,_,_=im.engine.run_real_monthly_close(up,options,active,schedule,'3m',1.02,label,reset_dates=im.engine.monthly_dates(b.date)); norm=.25/4
        put[im.first.FIELDS]*=norm
        # The independent momentum Put and non-Put legs remain identical; only
        # the core floor quantity is varied.
        momput=read(ARTIFACT/f'{scope}_combined_mom_put.csv.gz'); grid=read(ARTIFACT/f'{scope}_fixed_grid.csv.gz'); call=read(ARTIFACT/f'{scope}_fixed_call.csv.gz')
        total=put.copy()
        for col in im.first.FIELDS: total[col]+=momput[col]
        full=im.comp.compose(b,total,grid,call); full['candidate']=label; full=full[full.date<=END].copy(); daily.append(full)
        if name=='baseline_neg0':
            # Validate the actually changed core-Put leg against the source
            # artifact.  The surrounding grid/Call composition is retained
            # only as a diagnostic because its historical snapshot version is
            # not a parity-qualified current-r7 portfolio replay.
            ref=read(ARTIFACT/f'{scope}_combined_core_put.csv.gz'); q=put.merge(ref[['date',*im.first.FIELDS]],on='date',suffixes=('_new','_old'),validate='one_to_one')
            parity[scope]=max(float(abs(q[f'{field}_new']-q[f'{field}_old']).max()) for field in im.first.FIELDS)
            if parity[scope]>1e-12: raise RuntimeError(f'{scope} baseline parity {parity[scope]}')
        for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            start=full.date.min() if years is None else END-pd.DateOffset(years=years); sub=full[full.date>=start]
            if scope=='real' and start<full.date.min(): m={'ann_return':'N/A','ann_vol':'N/A','sharpe_repo':'N/A','max_dd':'N/A'}; n=0
            else: m=metrics(sub.ret); n=len(sub)
            row={'candidate':name,'scope':scope,'segment':segment,'start':str(start.date()),'end':str(END.date()),'rows':n,**m,'floor_switches':int(pd.Series(on).ne(pd.Series(on).shift()).sum()-1)}; rows.append(row)
            for k,v in m.items(): w[f'{scope}_{k}_{segment}']=v
      wide.append(w)
    result=pd.DataFrame(rows); result.to_csv(OUT/'scan_summary.csv',index=False); pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False); pd.concat(daily).to_csv(OUT/'daily_candidates.csv.gz',index=False,compression='gzip')
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8')); meta.update(phase='complete',scan_type='IM_MOM120_core_put_floor_asymmetric_release_scan',baseline={'candidate':'baseline_neg0','rule':'MOM120<0 gives core floor 3'},candidate_grid=VARIANTS,data_snapshot={'end':str(END.date()),'source_hashes':hashes},parity=parity,cost_model={'unchanged':'validated model/real Put engine, monthly close, 102% target, existing fees/cash/reserve'},limitations='Full IM v1.3 r7 performance is not claimed: historical fixed grid/call and independent momentum Put are retained from validated snapshot; 2026-08-15 onward and live ledger are not replayed.',decision='research_only_no_parameter_promotion',stability_label='pending_review')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'record.md').write_text('# IM MOM120核心Put下限防抖扫描\n\n## Data\n截至2026-08-14；模型与真实IM/MO分开。\n\n## Decision\nresearch_only_no_parameter_promotion\n\n## Stability\npending_review\n\n## Results\n\n'+result.to_markdown(index=False)+'\n',encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('python -X utf8 research_im_mom120_debounce_v1.py\n')
    print(result[result.segment.eq('full')].to_string(index=False))
if __name__=='__main__':main()
