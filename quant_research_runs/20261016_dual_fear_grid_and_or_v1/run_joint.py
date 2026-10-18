"""Offline preregistered dual-source grid replay on the repaired account baseline."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,shutil,sys,time
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
OUT=HERE/'outputs';OUT.mkdir(exist_ok=True)
INPUT=HERE/'inputs';INPUT.mkdir(exist_ok=True)
SCRATCH=HERE/'scratch';SCRATCH.mkdir(exist_ok=True)
PRIOR=ROOT/'quant_research_runs/20261016_jiu_fear_grid_threshold_v1'
REPAIR=ROOT/'quant_research_runs/20261016_put_execution_repairs_v1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
def module(name,p):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

sys.path.insert(0,str(ROOT))
policy_path=REPAIR/'backups/ic_im_v1_4_policy.py'
assert sha(policy_path)=='e658610a4259ac6e00eca9495589e7e78c47ab87437d89090038d7a0b49e202f'
policy=module('ic_im_v1_4_policy',policy_path)
reference=module('dual_fear_reference_study',PRIOR/'run_study.py')
reference.SCRATCH=SCRATCH;reference.listed.SCRATCH_DIR=SCRATCH

def prepare():
    listed=reference.listed
    source,candidates,gov,spot,_,_,*_=listed.load_account_inputs()
    risks={p:pd.read_csv(REPAIR/f'inputs/{p.lower()}/native_fix4_{p.lower()}_risk_signals_v1.csv.gz',dtype={'signal_date':str,'execution_date':str}) for p in ('IC','IM')}
    targets={p:REPAIR/f'inputs/{p.lower()}/native_fix4_{p.lower()}_noseller_targets_{"v2" if p=="IC" else "v3"}.csv.gz' for p in ('IC','IM')}
    lives={p:pd.read_csv(REPAIR/f'{p.lower()}_seller_lifecycle_fixed.csv.gz') for p in ('IC','IM')}
    jp=PRIOR/'inputs/jiu_fear_greed.csv';bp=listed.FEAR_FILE
    j=pd.read_csv(jp).set_index('date').fear_greed_index;b=pd.read_csv(bp).set_index('date').fear_greed_index
    expected=pd.read_csv(REPAIR/'daily_paths.csv.gz')
    expected={p:expected.loc[expected['product'].eq(p)&expected.arm.eq('both')].reset_index(drop=True) for p in ('IC','IM')}
    calendar=pd.Index(sorted(spot.index.strftime('%Y-%m-%d').unique()))
    fears={'same_day':(j,b),'lag1':(j.reindex(calendar).shift(1),b.reindex(calendar).shift(1))}
    files=[jp,bp,REPAIR/'daily_paths.csv.gz',policy_path,REPAIR/'history_manifest.json']+list(targets.values())
    files += [REPAIR/f'{p.lower()}_seller_lifecycle_fixed.csv.gz' for p in ('IC','IM')]
    files += [REPAIR/f'inputs/{p.lower()}/native_fix4_{p.lower()}_risk_signals_v1.csv.gz' for p in ('IC','IM')]
    files += [HERE/'preregistered_spec.md',Path(__file__)]
    paths={str(p.relative_to(ROOT)):sha(p) for p in files}
    if (INPUT/'input_hashes.json').exists():
        frozen=json.loads((INPUT/'input_hashes.json').read_text(encoding='utf-8'))
        assert frozen.keys()==paths.keys()
        changes={k:{'original':frozen[k],'current':v} for k,v in paths.items() if frozen[k]!=v}
        assert set(changes).issubset({str(Path(__file__).relative_to(ROOT))}),'Frozen data changed'
        if changes:dump(INPUT/'driver_revision_after_probe.json',{'changes':changes,'reason':'Count terminal open positions from executed holdings, not a pending last-day target; original driver retained in frozen_inputs and backups'})
    else:
        dump(INPUT/'input_hashes.json',paths)
        for p in files:
            dest=INPUT/'frozen_inputs'/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    return source,candidates,gov,spot,risks,targets,lives,fears,expected

def build(risk,j,b,product,mode):
    z=risk.copy();state=False;val_origin=False;fear_origins=frozenset();rows=[];trace=[];targets=[]
    ve,vx=(.5,1.) if product=='IC' else (1.6,2.)
    for i,r in enumerate(z.itertuples(index=False)):
        date=str(r.signal_date);v=float(r.score);values={'jiu':float(j.get(date,np.nan)),'bfw':float(b.get(date,np.nan))}
        fired={k for k,threshold in [('jiu',5),('bfw',25)] if np.isfinite(values[k]) and values[k]<=threshold}
        before=state;old_val=val_origin;old_fear=fear_origins
        if state:
            recovered={k for k in fear_origins if np.isfinite(values[k]) and values[k]>=50}
            fear_exit=bool(fear_origins) and (recovered==set(fear_origins) if mode.endswith('_ALL_EXIT') else bool(recovered))
            val_exit=val_origin and np.isfinite(v) and v>=vx
            if fear_exit or val_exit:state=False;val_origin=False;fear_origins=frozenset()
        else:
            if mode=='BFW':active=fired&{'bfw'}
            elif mode=='JIU':active=fired&{'jiu'}
            elif mode.startswith('AND'):active=fired if fired=={'jiu','bfw'} else set()
            elif mode.startswith('OR'):active=fired
            else:raise ValueError(mode)
            val_origin=bool(np.isfinite(v) and v<=ve);fear_origins=frozenset(active)
            state=bool(val_origin or fear_origins)
        targets.append(.5 if state else 0.)
        assert not state or val_origin or fear_origins
        if before and state:assert (old_val,old_fear)==(val_origin,fear_origins),'entry origins changed while held'
        if state!=before:
            vo,fo=(val_origin,fear_origins) if state else (old_val,old_fear)
            origin='both' if vo and fo else ('valuation_only' if vo else 'fear_only')
            rows.append(dict(signal_date=date,expected_execution_date='' if pd.isna(r.execution_date) else str(r.execution_date),
                action='BUY_NEXT_OPEN' if state else 'SELL_NEXT_OPEN',fear_greed_index=values['bfw'] if mode=='BFW' else values['jiu'],
                grid_target_before=.5 if before else 0.,grid_target_after=.5 if state else 0.,terminal_unexecuted=i==len(z)-1,
                valuation_score=v,fear_entry_cause=origin,fear_entry_sources='|'.join(sorted(fo)),jiu_index=values['jiu'],bfw_index=values['bfw']))
        trace.append(dict(signal_date=date,grid_target_units=targets[-1],valuation_entry_active=val_origin,
            frozen_fear_origins='|'.join(sorted(fear_origins)),jiu_index=values['jiu'],bfw_index=values['bfw']))
    z['grid_target_units']=targets
    columns=['signal_date','expected_execution_date','action','fear_greed_index','grid_target_before','grid_target_after','terminal_unexecuted','valuation_score','fear_entry_cause','fear_entry_sources','jiu_index','bfw_index']
    return z,pd.DataFrame(rows,columns=columns),pd.DataFrame(trace)

def independent_oracle(risk,j,b,product,mode):
    # Bitmask expression independent of build's source-set state machine.
    held=0;out=[];ve,vx=(.5,1.) if product=='IC' else (1.6,2.)
    for date,v in zip(risk.signal_date.astype(str),risk.score):
        jf=j.get(date,np.nan);bf=b.get(date,np.nan)
        if held:
            jr=bool(held&2) and np.isfinite(jf) and jf>=50
            br=bool(held&4) and np.isfinite(bf) and bf>=50
            fx=((not held&2 or jr) and (not held&4 or br) and bool(held&6)) if mode.endswith('_ALL_EXIT') else (jr or br)
            if (bool(held&1) and np.isfinite(v) and v>=vx) or fx:held=0
        else:
            jq=np.isfinite(jf) and jf<=5;bq=np.isfinite(bf) and bf<=25
            if mode=='BFW':jq=False
            elif mode=='JIU':bq=False
            elif mode.startswith('AND') and not (jq and bq):jq=False;bq=False
            held=(1 if np.isfinite(v) and v<=ve else 0)+(2 if jq else 0)+(4 if bq else 0)
        out.append(.5 if held else 0.)
    return np.array(out)

def runone(p,mode,timing,ctx):
    source,candidates,gov,spot,risks,targets,lives,fears,expected=ctx
    j,b=fears[timing];label=f'{mode}_{timing}'
    risk,sig,trace=build(risks[p],j,b,p,mode)
    assert np.array_equal(risk.grid_target_units.to_numpy(),independent_oracle(risks[p],j,b,p,mode))
    if mode in ('BFW','JIU'):
        old,_=reference.build(risks[p],b if mode=='BFW' else j,p,25 if mode=='BFW' else 5,50)
        assert np.array_equal(old.grid_target_units.to_numpy(),risk.grid_target_units.to_numpy())
    dp=OUT/f'{p}_{label}_daily.csv.gz';ep=OUT/f'{p}_{label}_events.csv.gz';sp=OUT/f'{p}_{label}_signals.csv'
    if dp.exists():
        d=pd.read_csv(dp);e=pd.read_csv(ep,low_memory=False);saved=pd.read_csv(sp,keep_default_na=False)
        actual=sig.copy();cached=saved.copy()
        for f in (actual,cached):
            for c in ['expected_execution_date','fear_entry_sources']:f[c]=f[c].fillna('')
        for c in sig.columns:
            if pd.api.types.is_numeric_dtype(sig[c]):cached[c]=pd.to_numeric(cached[c],errors='coerce')
        pd.testing.assert_frame_equal(actual,cached,check_dtype=False,check_exact=False,rtol=1e-12,atol=1e-12)
    else:
        t=time.monotonic();d,e,audit=reference.replay_isolated(p,label,risk,targets[p],lives[p],source,candidates,gov,spot)
        d['grid_held_units']=np.r_[0.,risk.grid_target_units.to_numpy(float)[:-1]]
        fills=e.loc[e.event.eq('trade')&e.symbol.fillna('').str.startswith('future|grid|')]
        execution=reference.listed.audit_grid_execution(sig,fills,label)
        assert np.max(np.abs(np.cumprod(1+d.return_net.to_numpy(float))-d.nav.to_numpy(float)))<1e-10
        d.to_csv(dp,index=False,compression='gzip');e.to_csv(ep,index=False,compression='gzip');sig.to_csv(sp,index=False)
        trace.to_csv(OUT/f'{p}_{label}_state_trace.csv',index=False)
        dump(OUT/f'{p}_{label}_audit.json',{'replay':audit,'execution':execution,'state_oracle':True,'elapsed':time.monotonic()-t})
        print(json.dumps({'product':p,'case':label,'seconds':round(time.monotonic()-t,2)},ensure_ascii=False),flush=True)
    entries=sig.loc[sig.action.eq('BUY_NEXT_OPEN')&sig.expected_execution_date.ne('')&sig.expected_execution_date.le(str(d.date.iloc[-1]))]
    m=[]
    for window in reference.SPLITS:
        metric=reference.metrics(d,sig,e,window)
        z=d.loc[reference.splitmask(d,window)];ent=entries.loc[entries.expected_execution_date.between(str(z.date.iloc[0]),str(z.date.iloc[-1]))]
        metric.update(product=p,mode=mode,timing=timing,label=label,grid_entries=len(ent),fear_entries=int(ent.fear_entry_sources.ne('').sum()),
            entry_both_fears=int(ent.fear_entry_sources.eq('bfw|jiu').sum()),terminal_open=int(d.grid_held_units.iloc[-1]>.0) if window=='full' else None)
        m.append(metric)
    return d,e,sig,m

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');args=parser.parse_args()
    if (OUT/'completion.json').exists():raise FileExistsError('Completed first output is immutable')
    spec=HERE/'preregistered_spec.md';assert sha(spec)==(HERE/'preregistered_spec.md.sha256').read_text().strip()
    ctx=prepare();guard={Path(m.__file__).resolve() for m in tuple(sys.modules.values()) if getattr(m,'__file__',None) and Path(m.__file__).suffix=='.py' and Path(m.__file__).resolve().is_relative_to(ROOT)}
    start={str(p):sha(p) for p in guard};dump(OUT/'start_source_hashes.json',start)
    rows=[];parities={}
    for p in ('IC','IM'):
        d,e,sig,metrics=runone(p,'BFW','same_day',ctx);rows.extend(metrics)
        parities[p]=reference.parity(d,ctx[-1][p],p)
    dump(OUT/'repaired_baseline_parity.json',parities)
    if args.probe:return
    for p in ('IC','IM'):
        cases=[(m,'same_day') for m in ('JIU','AND','OR','AND_ALL_EXIT','OR_ALL_EXIT')]+[(m,'lag1') for m in ('BFW','JIU','AND','OR')]
        for mode,timing in cases:
            _,_,_,metrics=runone(p,mode,timing,ctx);rows.extend(metrics)
            pd.DataFrame(rows).to_csv(OUT/'metrics_partial.csv',index=False)
    assert all(sha(p)==h for p,h in start.items()),'A loaded source changed on disk during the run'
    pd.DataFrame(rows).to_csv(OUT/'all_metrics.csv',index=False)
    # Retain the exact runtime source bytes without altering original modules.
    backups={}
    for p,h in start.items():
        path=Path(p);dest=INPUT/'runtime_source_backups'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        backups[str(path.relative_to(ROOT))]=h
    dump(INPUT/'runtime_source_hashes.json',backups)
    dump(OUT/'completion.json',{'cases':len(rows)//4,'metric_rows':len(rows),'spec_sha256':sha(spec),'sources_unchanged':True,'scope':'research_only'})
    print('COMPLETED',len(rows)//4,'account paths',flush=True)

if __name__=='__main__':main()
