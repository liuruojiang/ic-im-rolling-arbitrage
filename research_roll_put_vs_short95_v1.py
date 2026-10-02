from pathlib import Path
import json, hashlib
import pandas as pd
import numpy as np
import research_im_short95_le1_mom_then_put102_v3 as im
import ic_roll_momentum_stage2_put_v1 as ic
from im_put_maturity_valuation_tiers_v3 import metrics

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_20260914_roll_put_vs_short95_v1'
REF=im.OUT
CASH=im.original.CASH

def im_roll(scope,b,o,f,m,s):
    h=im.Protection(scope,b,o,f,m,s,True)
    fl=f.set_index(['contract','date']);prev=None;rows=[]
    for q in b.itertuples(index=False):
        day=q.date;price=float(fl.loc[(q.contract,day),'settle'])
        units=1/(200*(price if prev is None else prev))
        # Same daily notional normalization as the existing rolling-futures benchmark.
        if h.pos is not None:h.pos['units']=units
        p,c,a=h.step(day,'future',{'contract':q.contract,'units':units})
        ret=q.baseline_plus_cash_ret+p-c-a*CASH
        rows.append(dict(date=day,return_net=ret,protection_pnl=p,protection_cost=c,protection_capital=a,protection_qty=0 if h.pos is None else h.pos['qty']))
        prev=price
    d=pd.DataFrame(rows);d['candidate']=scope+'_roll_im_valuation_put102'
    pd.DataFrame(h.events).to_csv(OUT/(scope+'_im_trades.csv'),index=False)
    return d

def main():
    s=pd.read_csv(REF/'icm_quantity_schedule.csv',parse_dates=['date'])
    s['put_execution_target_qty']=s.protection_effective_valuation_tier.fillna(4).shift(1,fill_value=0).astype(int)
    assert s.put_execution_target_qty.between(0,4).all()
    s.to_csv(OUT/'im_valuation_only_schedule.csv',index=False)
    print('Building shared IM inputs',flush=True)
    m,mb,mo,mf,checks,basis=im.full.build_inputs()
    rb=pd.read_csv(im.original.BASE,parse_dates=['date']);rf=pd.read_csv(im.original.FU,parse_dates=['date'])
    raw=pd.read_csv(im.original.OP,parse_dates=['date']);raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m')
    ro=im.prepare_options(raw,im.actual_expiry_map(raw,rb))
    ds=[]
    for scope,b,o,f,market in [('real',rb,ro,rf,None),('model',mb,mo,mf,m)]:
        ds.append(im_roll(scope,b,o,f,market,s))
        d=pd.read_csv(REF/(scope+'_unprotected_daily.csv'),parse_dates=['date'])
        d['candidate']=scope+'_short95_le1_mom';ds.append(d)
        ds.append(pd.DataFrame(dict(date=b.date,return_net=b.baseline_plus_cash_ret,candidate=scope+'_bare_roll_im')))
    print('Building valuation-only IC Put ledgers',flush=True)
    base=pd.read_csv(ic.STAGE1_DAILY,parse_dates=['date'])
    schedule=ic.build_v2_schedule(base)
    val=schedule.valuation_tier_new.astype(float)*.25
    for col in ['target_delta','target_fraction','binary_target_fraction','three_tier_target_fraction']:schedule[col]=val
    schedule['risk_tier']=schedule.valuation_tier_new.astype(int)
    schedule.to_csv(OUT/'ic_valuation_only_schedule.csv',index=False)
    eng=ic.put_engine
    frames,_,market,checks_ic=eng.v19.v18.load_close_inputs()
    rolls=eng.v19.v18.v13.v6.forced_roll_dates(frames['ic'])
    for scope in ['model','real']:
        overlay,trades=(eng.run_model_delta(frames['ic'],schedule,market,'valuation_only',rolls) if scope=='model' else eng.run_real_delta(frames['ic'],schedule,frames,market,'valuation_only',rolls))
        d=base[['date','bare_roll_ic_ret']].merge(overlay,on='date',validate='one_to_one')
        d['return_net']=(1+d.bare_roll_ic_ret-.7*CASH+d.put_pnl_ret)*(1-d.put_cost_rate)-1+(.7-d.put_mark_fraction)*CASH
        if scope=='real':d=d[d.date>=ic.REAL_START].copy()
        d['candidate']=scope+'_roll_ic_valuation_put95';ds.append(d)
        trades.to_csv(OUT/(scope+'_ic_trades.csv'),index=False)
    for scope in ['real','model']:
        a=next(x for x in ds if x.candidate.iloc[0]==scope+'_roll_ic_valuation_put95')
        b=next(x for x in ds if x.candidate.iloc[0]==scope+'_roll_im_valuation_put102')
        z=a[['date','return_net']].merge(b[['date','return_net']],on='date',suffixes=('_ic','_im'),validate='one_to_one')
        z['return_net']=.5*z.return_net_ic+.5*z.return_net_im;z['candidate']=scope+'_roll_icim_50_50_valuation_put';ds.append(z)
        short=next(x for x in ds if x.candidate.iloc[0]==scope+'_short95_le1_mom').copy()
        short=short[short.date.isin(z.date)].copy();short['candidate']=scope+'_short95_common_icim';ds.append(short)
    daily=pd.concat(ds,ignore_index=True);summary=[];wide=[];unavailable={}
    for k,d in daily.groupby('candidate'):
        d=d.sort_values('date');assert not d.date.duplicated().any();assert np.isfinite(d.return_net).all() and d.return_net.gt(-1).all()
        w=dict(candidate=k)
        for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            cutoff=d.date.min() if years is None else d.date.max()-pd.DateOffset(years=years)
            available=cutoff>=d.date.min();z=d[d.date>=cutoff] if available else d.iloc[:0]
            met=metrics(z.return_net) if available else {key:'N/A' for key in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            if not available:unavailable.setdefault(k,{})[segment]='Insufficient genuine contract history for this window'
            summary.append(dict(candidate=k,segment=segment,start=str(cutoff.date()),end=str(d.date.max().date()),rows=len(z),**met))
            for key in ['ann_return','max_dd','sharpe_repo']:w[key+'_'+segment]=met[key]
        wide.append(w)
    daily.to_csv(OUT/'daily.csv',index=False);ss=pd.DataFrame(summary);ss.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(cost_model={'futures_reserve':.3,'cash_annual':.03,'IM_one_way_notional':.0001,'IC_Put':'existing official delta ledger cost factors retained'},unavailable_segments=unavailable,basis_calibration=basis,definition='Valuation-only protection: remove both momentum futures and momentum hedge floors. IC1x, IM1x, ICIM50:50 daily capital blend. Current short95 baseline retains its specified entry momentum.',limitations='Historical research through2026-08-14; monthly futures roll for matched IM comparison, not current quarterly r7 replay. IM normalized daily1x same as rolling benchmark; real zero-volume protection uses settlement estimate. IC futures actual since2015, model IC options theoretical throughout; IM full uniform theoretical with ex-post carry. No bidask/margin liquidation. No production changes.',data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),REF/'real_unprotected_daily.csv',REF/'model_unprotected_daily.csv',REF/'icm_quantity_schedule.csv',ic.STAGE1_DAILY]})
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'record.md').write_text('# 纯滚IC/IM＋估值Put，对比卖95%Put\n\n## Definition\n'+meta['definition']+'\n\n## Cost and Execution\n'+json.dumps(meta['cost_model'],ensure_ascii=False)+'\n\n## Limitations\n'+meta['limitations']+'\n\n## Results\n'+ss.to_string(index=False)+'\n',encoding='utf-8')
    (OUT/'command_log.txt').open('a',encoding='utf-8').write('\npython -X utf8 research_roll_put_vs_short95_v1.py\n')
    print(ss[ss.segment=='full'].to_string(index=False),flush=True)

if __name__=='__main__':main()
