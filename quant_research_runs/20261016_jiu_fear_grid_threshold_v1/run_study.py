"""Isolated, offline IC FIX11 / IM fear-grid full-account threshold study."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, shutil, socket, sys, time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OUT=HERE/'outputs';OUT.mkdir(exist_ok=True)
SCRATCH=HERE/'scratch';SCRATCH.mkdir(exist_ok=True)
TRAIN_END='2024-08-13'
ENTRY=[5,10,15,20]; EXIT=[40,50,60,70]
SPLITS=['full','train','holdout','last1y']

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,obj):Path(p).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
def no_network(*a,**k):raise RuntimeError('network forbidden during frozen historical account replay')
socket.create_connection=no_network
socket.socket.connect=no_network
paired_path=ROOT/'quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2/run_scan.py'
paired_spec=importlib.util.spec_from_file_location('jiu_reference_paired_scan',paired_path)
paired=importlib.util.module_from_spec(paired_spec)
paired_spec.loader.exec_module(paired)
listed=paired.listed
listed.SCRATCH_DIR=SCRATCH

def build(risk,fear,product,entry,exit):
    z=risk.copy();state=0.;origin='none';rows=[];targets=[];origins=[]
    ve,vx=(.5,1.) if product=='IC' else (1.6,2.)
    for i,r in enumerate(z.itertuples(index=False)):
        v=float(r.score);f=float(fear.get(str(r.signal_date),np.nan));before=state;old=origin;reason='hold'
        if state:
            vf=np.isfinite(v) and v>=vx
            ff=exit is not None and np.isfinite(f) and f>=exit
            if (vf and origin in ('valuation_only','both')) or (ff and origin in ('fear_only','both')):
                state=0.;origin='none';reason='exit'
        else:
            vf=np.isfinite(v) and v<=ve
            ff=entry is not None and np.isfinite(f) and f<=entry
            if vf or ff:
                state=.5;origin='both' if vf and ff else ('valuation_only' if vf else 'fear_only');reason='entry'
        targets.append(state);origins.append(origin)
        if state!=before:
            rows.append(dict(signal_date=str(r.signal_date),expected_execution_date=str(r.execution_date),
                action='BUY_NEXT_OPEN' if state else 'SELL_NEXT_OPEN',fear_greed_index=f,
                grid_target_before=before,grid_target_after=state,terminal_unexecuted=i==len(z)-1,
                valuation_score=v,fear_entry_cause=origin if state else old))
    z['grid_target_units']=targets
    return z,pd.DataFrame(rows,columns=['signal_date','expected_execution_date','action','fear_greed_index',
        'grid_target_before','grid_target_after','terminal_unexecuted','valuation_score','fear_entry_cause'])

def independent_targets(risk,fear,product,entry,exit):
    # Separately expressed set-of-triggers oracle, without calling build().
    states=[];active=set();held=False;ve,vx=(.5,1.) if product=='IC' else (1.6,2.)
    for d,v in zip(risk.signal_date.astype(str),risk.score):
        f=fear.get(d,np.nan)
        if held:
            exits=set()
            if np.isfinite(v) and v>=vx:exits.add('valuation')
            if exit is not None and np.isfinite(f) and f>=exit:exits.add('fear')
            if active.intersection(exits):held=False;active=set()
        else:
            if np.isfinite(v) and v<=ve:active.add('valuation')
            if entry is not None and np.isfinite(f) and f<=entry:active.add('fear')
            held=bool(active)
        states.append(.5 if held else 0.)
    return np.array(states)

def splitmask(d,split):
    dates=d.date.astype(str)
    if split=='train':return dates<=TRAIN_END
    if split=='holdout':return dates>TRAIN_END
    if split=='last1y':return pd.to_datetime(dates)>=(pd.Timestamp(dates.iloc[-1])-pd.DateOffset(years=1))
    return np.ones(len(d),dtype=bool)

def metrics(d,signals,events,split):
    z=d.loc[splitmask(d,split)].reset_index(drop=True)
    assert len(z)>0
    r=z.return_net.to_numpy(float);nav=np.r_[1.,np.cumprod(1+r)]
    assert np.isfinite(nav).all() and min(nav)>0
    dd=nav/np.maximum.accumulate(nav)-1;trough=int(dd.argmin());peak=int(nav[:trough+1].argmax())
    dates=['window_start']+z.date.astype(str).tolist(); recovery=next((dates[j] for j in range(trough+1,len(nav)) if nav[j]>=nav[peak]),None)
    cagr=nav[-1]**(252/len(r))-1;vol=r.std(ddof=1)*252**.5
    sig=signals.loc[(signals.expected_execution_date>=str(z.date.iloc[0]))&(signals.expected_execution_date<=str(z.date.iloc[-1]))]
    closed=int((sig.action=='SELL_NEXT_OPEN').sum())
    ev=events.loc[(events.date.astype(str)>=str(z.date.iloc[0]))&(events.date.astype(str)<=str(z.date.iloc[-1]))]
    ge=ev.loc[ev.event.eq('trade')&ev.symbol.fillna('').str.startswith('future|grid|')]
    fees=float(ge.fee.sum()) if 'fee' in ge else None
    heldcol='grid_held_units'
    held=float(z[heldcol].gt(0).mean()) if heldcol in z else None
    return dict(window=split,start=str(z.date.iloc[0]),end=str(z.date.iloc[-1]),rows=len(z),cagr=float(cagr),
        max_drawdown=float(dd.min()),calmar=float(cagr/-dd.min()) if dd.min()<0 else None,
        annual_vol=float(vol),sharpe=float(r.mean()/r.std(ddof=1)*252**.5) if r.std(ddof=1)>0 else None,
        peak_date=dates[peak],trough_date=dates[trough],recovery_date=recovery,
        total_return=float(nav[-1]-1),completed_cycles=closed,grid_trade_events=len(ge),grid_fees=fees,grid_exposure=held)

def parity(d,saved,product):
    assert len(d)==len(saved) and d.date.astype(str).tolist()==saved.date.astype(str).tolist()
    errors={}
    for c in ['nav','return_net','equity_close','cash_close','margin_reserved','option_value','fees_cumulative']:
        if c not in d or c not in saved:continue
        a=pd.to_numeric(d[c],errors='raise').to_numpy(float);b=pd.to_numeric(saved[c],errors='raise').to_numpy(float)
        assert np.array_equal(np.isnan(a),np.isnan(b)),c
        err=float(np.max(np.abs(a-b)))
        assert err<=(1e-10 if c in ('nav','return_net') else 1e-5),(product,c,err)
        errors[c]=err
    return errors

def replay_isolated(product,label,risk,target_path,lifecycle,source,candidates,gov,spot):
    # Keep the original engine configuration, while avoiding Windows Python
    # TemporaryDirectory ACLs. Each reversible local run keeps its own inputs.
    folder=SCRATCH/f'{product}_{label}';folder.mkdir(parents=True,exist_ok=True)
    risk_path=listed.grid_scan.risk_file(folder,product)
    risk_path.parent.mkdir(parents=True,exist_ok=True)
    risk.to_csv(risk_path,index=False,compression='gzip')
    shutil.copy2(target_path,folder/Path(target_path).name)
    listed.grid_scan.configure_account(folder)
    daily,events,audit=listed.grid_scan.account_stage.replay(product,'repeat_roll',source,lifecycle,candidates,gov,spot)
    daily['date']=daily.date.astype(str);events['date']=events.date.astype(str)
    assert daily.date.tolist()==risk.signal_date.astype(str).tolist()
    assert np.isfinite(daily.nav).all() and daily.nav.gt(0).all()
    assert (daily.cash_close-daily.margin_reserved).min()>=-1e-7
    trades=events.loc[events.event.eq('trade'),'symbol'].fillna('').astype(str)
    assert not trades.str.startswith('option|call|').any()
    return daily,events,audit

def load():
    source,candidates,gov,spot,lives,folders,*_=listed.load_account_inputs()
    ic=ROOT/'quant_research_runs/20261002_fix10_vs_fix11_full_history/account_inputs/FIX11_full'
    risks={p:pd.read_csv((ic if p=='IC' else folders[p])/f'native_fix4_{p.lower()}_risk_signals_v1.csv.gz',dtype={'signal_date':str,'execution_date':str}) for p in ('IC','IM')}
    targets={p:(ic if p=='IC' else folders[p])/f'native_fix4_{p.lower()}_noseller_targets_{"v2" if p=="IC" else "v3"}.csv.gz' for p in ('IC','IM')}
    j=pd.read_csv(HERE/'inputs/jiu_fear_greed.csv').set_index('date').fear_greed_index
    b=pd.read_csv(listed.FEAR_FILE).set_index('date').fear_greed_index
    savedic=pd.read_csv(ROOT/'quant_research_runs/20261002_fix10_vs_fix11_full_history/account_daily_paths.csv.gz')
    savedim=pd.read_csv(ROOT/'quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2/outputs/fix9_full_account_daily_nav.csv.gz')
    expected={'IC':savedic.loc[savedic.arm.eq('FIX11_full')].reset_index(drop=True),
        'IM':savedim.loc[savedim.candidate.eq('IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9')].reset_index(drop=True)}
    cov=[]
    for p,r in risks.items():
        assert r.signal_date.is_unique and r.signal_date.is_monotonic_increasing
        jm=[d for d in r.signal_date if pd.isna(j.get(d,np.nan))];bm=[d for d in r.signal_date if pd.isna(b.get(d,np.nan))]
        cov.append(dict(product=p,rows=len(r),start=r.signal_date.iloc[0],end=r.signal_date.iloc[-1],jiu_missing=jm,baifenwei_missing=bm))
        if jm:raise RuntimeError(f'JIU coverage incomplete; follow preregistration new version before payoff: {p}, {jm[:15]}')
    dump(OUT/'coverage.json',cov)
    return source,candidates,gov,spot,lives,risks,targets,j,b,expected

def runone(product,label,entry,exit,fear,ctx):
    source,candidates,gov,spot,lives,risks,targets,*_=ctx
    rp=OUT/f'{product}_{label}_daily.csv.gz';ep=OUT/f'{product}_{label}_events.csv.gz';sp=OUT/f'{product}_{label}_signals.csv'
    risk,sig=build(risks[product],fear,product,entry,exit)
    oracle=independent_targets(risks[product],fear,product,entry,exit)
    assert np.array_equal(oracle,risk.grid_target_units.to_numpy())
    if rp.exists():
        d=pd.read_csv(rp);events=pd.read_csv(ep,low_memory=False);saved_sig=pd.read_csv(sp)
        actual=sig.copy();cached=saved_sig.copy()
        for frame in (actual,cached):
            frame['expected_execution_date']=frame.expected_execution_date.fillna('').replace('nan','')
            assert frame.loc[~frame.terminal_unexecuted,'expected_execution_date'].ne('').all()
        pd.testing.assert_frame_equal(actual,cached,check_dtype=False,check_exact=False,rtol=1e-12,atol=1e-12)
    else:
        t=time.monotonic();d,events,audit=replay_isolated(product,label,risk,targets[product],lives[product],source,candidates,gov,spot)
        d['grid_held_units']=np.r_[0.,risk.grid_target_units.to_numpy(float)[:-1]]
        assert np.max(np.abs(np.cumprod(1+d.return_net.to_numpy())-d.nav.to_numpy()))<1e-10
        fills=events.loc[events.event.eq('trade')&events.symbol.fillna('').astype(str).str.startswith('future|grid|')].copy()
        execution=listed.audit_grid_execution(sig,fills,label)
        d.to_csv(rp,index=False,compression='gzip');events.to_csv(ep,index=False,compression='gzip');sig.to_csv(sp,index=False)
        dump(OUT/f'{product}_{label}_audit.json',{'replay':audit,'execution':execution,'elapsed':time.monotonic()-t})
        print(json.dumps({'product':product,'label':label,'elapsed':round(time.monotonic()-t,2),'events':len(events)},ensure_ascii=False),flush=True)
    return d,events,sig

def select(summary):
    tr=summary.loc[summary.window.eq('train')].copy();rows=[]
    for en in ENTRY:
      for ex in EXIT:
        label=f'JIU_e{en}_x{ex}';eligible=True
        for p in ('IC','IM'):
            a=tr.loc[tr['product'].eq(p)&tr.label.eq(label)].iloc[0];b=tr.loc[tr['product'].eq(p)&tr.label.eq('BFW25_50')].iloc[0]
            eligible &= bool(a.completed_cycles>=2 and a.cagr>=b.cagr-.02)
        neigh=[(e,x) for e in ENTRY for x in EXIT if (e==en and abs(x-ex)<=10) or (x==ex and abs(e-en)<=5)]
        penalties=[];calmars=[];trades=[]
        for e,x in neigh:
          for p in ('IC','IM'):
            a=tr.loc[tr['product'].eq(p)&tr.label.eq(f'JIU_e{e}_x{x}')].iloc[0];b=tr.loc[tr['product'].eq(p)&tr.label.eq('BFW25_50')].iloc[0]
            penalties.append(abs(a.max_drawdown)-abs(b.max_drawdown));calmars.append(a.calmar);trades.append(a.grid_trade_events)
        rows.append(dict(entry=en,exit=ex,label=label,eligible=eligible,worst_neighbor_dd_delta=max(penalties),mean_neighbor_calmar=float(np.nanmean(calmars)),mean_trades=float(np.mean(trades))))
    rank=pd.DataFrame(rows).sort_values(['eligible','worst_neighbor_dd_delta','mean_neighbor_calmar','mean_trades','entry','exit'],ascending=[False,True,False,True,True,True])
    rank.to_csv(OUT/'training_selection.csv',index=False)
    chosen=rank.loc[rank.eligible].head(1)
    return None if chosen.empty else chosen.iloc[0].to_dict()

def main():
    args=argparse.ArgumentParser();args.add_argument('--probe',action='store_true');a=args.parse_args()
    spec=HERE/'preregistered_spec.md';assert sha(spec)==(HERE/'preregistered_spec.md.sha256').read_text().strip()
    ctx=load();j,b=ctx[-3:-1];rows=[];parities={}
    guarded={Path(m.__file__).resolve() for m in tuple(sys.modules.values()) if getattr(m,'__file__',None) and Path(m.__file__).suffix=='.py' and Path(m.__file__).resolve().is_relative_to(ROOT)}
    guarded.update(ctx[6].values());guarded.update([HERE/'inputs/jiu_fear_greed.csv',listed.FEAR_FILE,spec])
    initial_hashes={str(p):sha(p) for p in guarded}
    dump(OUT/'start_source_hashes.json',initial_hashes)
    for p in ('IC','IM'):
        d,events,sig=runone(p,'BFW25_50',25,50,b,ctx)
        parities[p]=parity(d,ctx[-1][p],p)
        for split in SPLITS:rows.append(dict(product=p,label='BFW25_50',entry=25,exit=50,**metrics(d,sig,events,split)))
    dump(OUT/'baseline_parity.json',parities)
    if a.probe:return
    for p in ('IC','IM'):
        arms=[('VALUATION_ONLY',None,None,b),('JIU25_50_DIAGNOSTIC',25,50,j)]+[(f'JIU_e{en}_x{ex}',en,ex,j) for en in ENTRY for ex in EXIT]
        for label,en,ex,fear in arms:
            d,events,sig=runone(p,label,en,ex,fear,ctx)
            for split in SPLITS:rows.append(dict(product=p,label=label,entry=en,exit=ex,**metrics(d,sig,events,split)))
            pd.DataFrame(rows).to_csv(OUT/'metrics_partial.csv',index=False)
    summary=pd.DataFrame(rows);summary.to_csv(OUT/'all_metrics.csv',index=False)
    chosen=select(summary);dump(OUT/'selected_threshold.json',{'selected':chosen,'selection_scope':'training only; retrospective pseudo-OOS'})
    if chosen:
        for p in ('IC','IM'):
            dates=ctx[5][p].signal_date.astype(str).tolist()
            # Strict prior actual session on the full source calendar, not shift on an inner join.
            shifted=j.shift(1)
            d,events,sig=runone(p,'SELECTED_LAG1',int(chosen['entry']),int(chosen['exit']),shifted,ctx)
            for split in SPLITS:rows.append(dict(product=p,label='SELECTED_LAG1',entry=chosen['entry'],exit=chosen['exit'],**metrics(d,sig,events,split)))
        summary=pd.DataFrame(rows);summary.to_csv(OUT/'all_metrics.csv',index=False)
    modules={str(Path(m.__file__).resolve().relative_to(ROOT)):sha(m.__file__) for m in tuple(sys.modules.values()) if getattr(m,'__file__',None) and Path(m.__file__).suffix=='.py' and Path(m.__file__).resolve().is_relative_to(ROOT)}
    assert all(sha(p)==v for p,v in initial_hashes.items()),'Source/input changed during replay'
    dump(OUT/'runtime_source_hashes.json',modules)
    # Do not traverse the abandoned Windows TemporaryDirectory with restrictive ACLs.
    artifacts=[p for p in HERE.iterdir() if p.is_file()]
    for directory in ('inputs','outputs','backups'):
        artifacts.extend(p for p in (HERE/directory).rglob('*') if p.is_file())
    dump(OUT/'result_manifest.json',{str(p.relative_to(HERE)):sha(p) for p in artifacts if p.name!='result_manifest.json'})
    print('COMPLETED',json.dumps(chosen,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
