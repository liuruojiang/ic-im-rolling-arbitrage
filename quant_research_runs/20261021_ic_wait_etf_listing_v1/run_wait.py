"""Four-arm IC actual ETF new-month observation/research replay."""
from pathlib import Path
import importlib.util,sys,json,hashlib,time
from functools import lru_cache
import pandas as pd
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
PREV=ROOT/'quant_research_runs/20261021_put_monday_settlement_frozen_v1'
REPAIRS=ROOT/'quant_research_runs/20261016_put_execution_repairs_v1'
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
STUDY=ROOT/'quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness'
sys.path[:0]=[str(ROOT),str(STUDY),str(NATIVE)]


def load(n,p):
    s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s)
    sys.modules[n]=m;s.loader.exec_module(m);return m


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def metrics(d,start=None):
    z=d if start is None else d.loc[pd.to_datetime(d.date)>=start]
    r=z.return_net.to_numpy(float);nav=np.r_[1.,np.cumprod(1+r)]
    return dict(start=str(z.date.iloc[0]),end=str(z.date.iloc[-1]),rows=len(z),
        cagr=float(nav[-1]**(252/len(r))-1),max_dd=float((nav/np.maximum.accumulate(nav)-1).min()),
        sharpe=float(np.mean(r)/np.std(r,ddof=1)*np.sqrt(252)),final_nav=float(nav[-1]))


def main():
    if (HERE/'completed.json').exists():raise FileExistsError('Completed output immutable')
    begun=time.time()
    orig=load('wait_prior_repair',REPAIRS/'run_history.py')
    paired=load('wait_paired',orig.PAIRED/'run_scan.py');listed=paired.listed
    source,candidates,gov,spot,*_=listed.load_account_inputs()
    for n in ('im_mo_on','ic_on','futures_on'):setattr(source,n,lru_cache(maxsize=None)(getattr(source,n)))
    acc=load('wait_account',HERE/'backups/account_close_isolated.py')
    base=load('wait_base',HERE/'backups/monday_controller.py')
    close=load('wait_close',HERE/'backups/close_controller.py')
    listing=load('wait_listing',HERE/'listing_controller.py')
    Frozen,Wait=listing.make_classes(close.make_close_class(base))
    olddaily=pd.read_csv(PREV/'daily_paths.csv.gz');oldevents=pd.read_csv(PREV/'events.csv.gz',low_memory=False)
    guarded=list((ROOT/'runtime').glob('**/*.json'))
    guarded+=[ROOT/'poe_ic_im_mainline_v1_4_bot.py',ROOT/'notes/mainline_registry.md',
        ROOT/'docs/ic_im_system_mainlines_v1_spec.md',ROOT/'outputs/ic_im_system_mainlines_v1/record.md']
    guarded+=list(PREV.glob('*.py'))+list(PREV.glob('*.gz'))+list(PREV.glob('*.json'))
    hashes={str(p.relative_to(ROOT)):sha(p) for p in guarded}
    (HERE/'protected_manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    ds=[];es=[];ms=[];plans=[];sels=[];fails=[];waits=[];audits={}
    listed.grid_scan.configure_account(REPAIRS/'inputs/ic');config=listed.grid_scan.account_stage
    for k in ['VERSION','REENTRY_SELECTOR','MARGIN_MODEL','EXECUTION_MODEL','LIFECYCLE_VERSION',
        'QUARTER_ROLL_AT_SIGNAL_CLOSE','PROFIT_RESTRIKE_MULTIPLE','PROFIT_EXECUTION_PHASE',
        'DISABLED_MODULES','BUYER_OPEN_SLEEVES','BUYER_OPEN_PROXY_MULTIPLIER','CORE_UNITS',
        'CORE_BUYER_SCALE','MOMENTUM_SCALE','SELLER_SCALE','SELLER_NAV_BUFFER_RATE']:
        setattr(acc,k,getattr(config,k))
    acc.HERE=REPAIRS/'inputs/ic';life=pd.read_csv(REPAIRS/'ic_seller_lifecycle_fixed.csv.gz')
    for arm in ('baseline','settlement_frozen','wait_nearest','wait_min3'):
        C=Frozen if arm=='settlement_frozen' else Wait
        acc.MONDAY_FACTORY=None if arm=='baseline' else lambda p,r,t,m,g,C=C,arm=arm:C(acc,arm,p,r,t,m,g)
        d,e,a=acc.replay('IC','repeat_roll',source,life,candidates,gov,spot)
        assert np.isfinite(d.nav).all() and (d.nav>0).all()
        assert (d.cash_close-d.margin_reserved).min()>-1e-7
        if arm in ('baseline','settlement_frozen'):
            ref=olddaily.loc[olddaily['product'].eq('IC')&olddaily.arm.eq(arm)].reset_index(drop=True)
            a['prior_saved_daily_parity']=orig.compare(d,ref)
            ref_e=oldevents.loc[oldevents['product'].eq('IC')&oldevents.arm.eq(arm)].reset_index(drop=True)
            assert len(e)==len(ref_e)
            for k in ('date','event','phase','symbol','signal_date','price_basis'):
                assert e[k].fillna('').astype(str).reset_index(drop=True).equals(ref_e[k].fillna('').astype(str)),k
        audits[arm]=a
        ctl=acc.LAST_CONTROLLER
        if ctl:plans+=ctl.plans;sels+=ctl.selections;fails+=ctl.failures;waits+=getattr(ctl,'wait_rows',[])
        d['arm']=arm;e['arm']=arm;ds.append(d);es.append(e);end=pd.Timestamp(d.date.iloc[-1])
        for window,y in [('full',None),('3Y',3),('1Y',1)]:
            ms.append(dict(arm=arm,window=window,**metrics(d,None if y is None else end-pd.DateOffset(years=y))))
        print(json.dumps(dict(arm=arm,**metrics(d))),flush=True)
        pd.concat(ds,ignore_index=True).to_csv(HERE/'daily_paths.csv.gz',index=False)
        pd.concat(es,ignore_index=True).to_csv(HERE/'events.csv.gz',index=False)
        pd.DataFrame(ms).to_csv(HERE/'window_metrics.csv',index=False)
        pd.DataFrame(plans).to_csv(HERE/'maintenance_calendar.csv',index=False)
        pd.DataFrame(sels).to_csv(HERE/'selections.csv',index=False)
        pd.DataFrame(fails).to_csv(HERE/'missing_quotes.csv',index=False)
        pd.DataFrame(waits).to_csv(HERE/'listing_observations.csv',index=False)
        (HERE/'account_audit.json').write_text(json.dumps(audits,indent=2,default=str),encoding='utf-8')
    assert hashes=={str(p.relative_to(ROOT)):sha(p) for p in guarded}
    (HERE/'completed.json').write_text(json.dumps(dict(elapsed_seconds=time.time()-begun,
        spec_sha256=sha(ROOT/'docs/ic_put_wait_etf_listing_20261021_v1_spec.md'),protected_files_unchanged=len(guarded),
        sources={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),HERE/'listing_controller.py',*list((HERE/'backups').glob('*.py'))]}),indent=2),encoding='utf-8')


if __name__=='__main__':main()
