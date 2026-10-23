"""Four-arm same-data close-maintenance comparison, isolated from production."""
from pathlib import Path
import importlib.util
import hashlib
import json
import sys
import time
from functools import lru_cache
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
PREV=ROOT/'quant_research_runs/20261021_put_after_settlement_monday_v2'
REPAIRS=ROOT/'quant_research_runs/20261016_put_execution_repairs_v1'
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
STUDY=ROOT/'quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness'
sys.path[:0]=[str(ROOT),str(STUDY),str(NATIVE)]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return m


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def engine():
    text=(HERE/'backups/account_monday_isolated.py').read_text(encoding='utf-8')
    old='            # 3x is a separate current-rule buyer reset, never a seller fill.'
    new='''            if ctl and getattr(ctl, "close_mode", False):
                sequence, close_signatures = ctl.execute_close(day, risk.iloc[ordinal], today_target, account, route, sequence)
                maintenance_signatures.update(close_signatures)
                if ctl.today_close_reset:
                    core_override = None
                    core_entry = ctl.close_cost
            # 3x is a separate current-rule buyer reset, never a seller fill.'''
    assert text.count(old)==1
    text=text.replace(old,new)
    old='            extra_profit3x_trigger = None\n            extra_profit3x_preselected = None\n            profit3x_trigger = None'
    new='''            if ctl and getattr(ctl, "today_close_reset", False):
                core_entry = ctl.close_cost if route == "future" else None
            extra_profit3x_trigger = None
            extra_profit3x_preselected = None
            profit3x_trigger = None'''
    assert text.count(old)==1
    text=text.replace(old,new)
    path=HERE/'account_close_isolated.py'
    path.write_text(text,encoding='utf-8')
    return load('close_isolated_account',path)


def metrics(frame,start=None):
    z=frame if start is None else frame.loc[pd.to_datetime(frame.date)>=start]
    r=z.return_net.to_numpy(float);nav=np.r_[1.,np.cumprod(1+r)]
    return dict(start=str(z.date.iloc[0]),end=str(z.date.iloc[-1]),rows=len(z),
        cagr=float(nav[-1]**(252/len(r))-1),max_dd=float((nav/np.maximum.accumulate(nav)-1).min()),
        sharpe=float(np.mean(r)/np.std(r,ddof=1)*np.sqrt(252)),final_nav=float(nav[-1]))


def main():
    if (HERE/'completed.json').exists():raise FileExistsError('Completed outputs immutable')
    start=time.time()
    original=load('close_previous_repair',REPAIRS/'run_history.py')
    paired=load('close_paired',original.PAIRED/'run_scan.py')
    listed=paired.listed
    source,candidates,gov,spot,lives,folders,candidate_path,im_life_path,_=listed.load_account_inputs()
    for method in ('im_mo_on','ic_on','futures_on'):
        setattr(source,method,lru_cache(maxsize=None)(getattr(source,method)))
    acc=engine()
    base=load('close_base_controller',HERE/'backups/monday_controller.py')
    closemod=load('close_controller',HERE/'close_controller.py')
    Close=closemod.make_close_class(base)
    olddaily=pd.read_csv(PREV/'daily_paths.csv.gz')
    oldevents=pd.read_csv(PREV/'events.csv.gz',low_memory=False)
    protected=[ROOT/'poe_ic_im_mainline_v1_4_bot.py',ROOT/'notes/mainline_registry.md',
        ROOT/'docs/ic_im_system_mainlines_v1_spec.md',ROOT/'outputs/ic_im_system_mainlines_v1/record.md']
    protected+=list((ROOT/'runtime').glob('**/*.json'))
    protected+=list(PREV.glob('*.py'))+list(PREV.glob('*.gz'))+list(PREV.glob('*.json'))
    protection={str(p.relative_to(ROOT)):sha(p) for p in protected}
    (HERE/'protected_manifest.json').write_text(json.dumps(protection,indent=2),encoding='utf-8')
    outputs=[];evs=[];rows=[];plans=[];selections=[];failures=[];audits={}
    for product in ('IC','IM'):
        listed.grid_scan.configure_account(REPAIRS/'inputs'/product.lower())
        configured=listed.grid_scan.account_stage
        for key in ['VERSION','REENTRY_SELECTOR','MARGIN_MODEL','EXECUTION_MODEL','LIFECYCLE_VERSION',
            'QUARTER_ROLL_AT_SIGNAL_CLOSE','PROFIT_RESTRIKE_MULTIPLE','PROFIT_EXECUTION_PHASE',
            'DISABLED_MODULES','BUYER_OPEN_SLEEVES','BUYER_OPEN_PROXY_MULTIPLIER','CORE_UNITS',
            'CORE_BUYER_SCALE','MOMENTUM_SCALE','SELLER_SCALE','SELLER_NAV_BUFFER_RATE']:
            setattr(acc,key,getattr(configured,key))
        acc.HERE=REPAIRS/'inputs'/product.lower()
        life=pd.read_csv(REPAIRS/f'{product.lower()}_seller_lifecycle_fixed.csv.gz')
        audits[product]={}
        for arm in ('baseline','carry_old','close_current','close_frozen'):
            if arm=='baseline':acc.MONDAY_FACTORY=None
            elif arm=='carry_old':acc.MONDAY_FACTORY=lambda p,r,t,m,g:base.Controller(acc,'carry_old',p,r,t,m,g)
            else:acc.MONDAY_FACTORY=lambda p,r,t,m,g,arm=arm:Close(acc,arm,p,r,t,m,g)
            daily,events,audit=acc.replay(product,'repeat_roll',source,life,candidates,gov,spot)
            assert np.isfinite(daily.nav).all() and (daily.nav>0).all()
            assert (daily.cash_close-daily.margin_reserved).min()>-1e-7
            assert not events.loc[events.event.eq('trade'),'symbol'].astype(str).str.startswith('option|call|').any()
            if arm in ('baseline','carry_old'):
                ref=olddaily.loc[olddaily['product'].eq(product)&olddaily.arm.eq(arm)].reset_index(drop=True)
                audit['prior_saved_daily_parity']=original.compare(daily,ref)
                ref_e=oldevents.loc[oldevents['product'].eq(product)&oldevents.arm.eq(arm)].reset_index(drop=True)
                assert len(ref_e)==len(events)
                for c in ('date','event','symbol','phase','signal_date','price_basis'):
                    assert events[c].fillna('').astype(str).reset_index(drop=True).equals(ref_e[c].fillna('').astype(str)),c
                for c in ('cash_change','equity_change','quantity_change','price','fee'):
                    assert np.allclose(events[c].fillna(0),ref_e[c].fillna(0),atol=1e-7,rtol=0)
            audits[product][arm]=audit
            ctl=acc.LAST_CONTROLLER
            if ctl:
                plans.extend(ctl.plans);selections.extend(ctl.selections);failures.extend(ctl.failures)
            daily['product']=product;daily['arm']=arm;events['product']=product;events['arm']=arm
            outputs.append(daily);evs.append(events)
            end=pd.Timestamp(daily.date.iloc[-1])
            for window,years in [('full',None),('3Y',3),('1Y',1)]:
                rows.append(dict(product=product,arm=arm,window=window,
                    **metrics(daily,None if years is None else end-pd.DateOffset(years=years))))
            print(json.dumps(dict(product=product,arm=arm,**metrics(daily))),flush=True)
            pd.concat(outputs,ignore_index=True).to_csv(HERE/'daily_paths.csv.gz',index=False)
            pd.concat(evs,ignore_index=True).to_csv(HERE/'events.csv.gz',index=False)
            pd.DataFrame(rows).to_csv(HERE/'window_metrics.csv',index=False)
            pd.DataFrame(plans).to_csv(HERE/'maintenance_calendar.csv',index=False)
            pd.DataFrame(selections).to_csv(HERE/'selections.csv',index=False)
            pd.DataFrame(failures).to_csv(HERE/'missing_quotes.csv',index=False)
            (HERE/'account_audit.json').write_text(json.dumps(audits,indent=2,default=str),encoding='utf-8')
    assert protection=={str(p.relative_to(ROOT)):sha(p) for p in protected}
    manifest=dict(elapsed_seconds=time.time()-start,protected_files_unchanged=len(protected),
        spec_sha256=sha(ROOT/'docs/ic_im_put_monday_close_20261021_v1_spec.md'),
        sources={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),HERE/'close_controller.py',
            HERE/'account_close_isolated.py',HERE/'backups/monday_controller.py']})
    (HERE/'completed.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__=='__main__':main()
