"""Three-arm isolated current-rule listed-history full-account comparison."""
from pathlib import Path
import importlib.util
import hashlib
import json
import sys
import time
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
REPAIRS=ROOT/'quant_research_runs/20261016_put_execution_repairs_v1'
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
STUDY=ROOT/'quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness'
sys.path[:0]=[str(HERE),str(ROOT),str(STUDY),str(NATIVE)]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def patched_engine():
    text=(HERE/'backups/native_account.py').read_text(encoding='utf-8')
    patches=[
        ('REENTRY_SELECTOR = None','REENTRY_SELECTOR = None\nMONDAY_FACTORY = None\nLAST_CONTROLLER = None'),
        ('    history = []','    history = []\n    global LAST_CONTROLLER\n    ctl = MONDAY_FACTORY(product, risk, targets, market, gov) if MONDAY_FACTORY else None\n    LAST_CONTROLLER = ctl'),
        ('            route = l.route_after_execution','            route = l.route_after_execution\n            if ctl:\n                t = ctl.target_for_open(day, signal_day, r, t)'),
        ('            split_core = product == "IC" and FIXED_SELLER_CORE_UNITS is not None',
         '            if ctl and ctl.today_open_reset:\n                previous_target.pop("core_put", None)\n                previous_target.pop("momentum_put", None)\n            split_core = product == "IC" and FIXED_SELLER_CORE_UNITS is not None'),
        ('            names = sorted(set(account.positions) | set(target_positions))',
         '            if ctl:\n                ctl.enforce_pending(day, target_positions, account, route)\n            names = sorted(set(account.positions) | set(target_positions))'),
        ('            if PROFIT_EXECUTION_PHASE == "open" and profit3x_trigger == day and route == "future":',
         '            if ctl and profit3x_trigger == day and route == "future" and not ctl.profit_open_available(day, account, profit3x_preselected):\n                profit3x_trigger = None\n                profit3x_preselected = None\n            if PROFIT_EXECUTION_PHASE == "open" and profit3x_trigger == day and route == "future":'),
        ('            if maintenance_today:\n                # Today\'s formally selected target',
         '            if ctl and maintenance_today:\n                sequence = ctl.schedule_close(day, account, spot_price, sequence)\n            if maintenance_today and ctl is None:\n                # Today\'s formally selected target'),
        ('and not bool(targets.iloc[ordinal].monthly_reset_due):',
         'and not bool(targets.iloc[ordinal].monthly_reset_due) and not (ctl and ctl.pending is not None):'),
        ('            extra_profit3x_trigger = None\n            extra_profit3x_preselected = None\n            profit3x_trigger = None',
         '            if ctl and ctl.today_open_reset and route == "future":\n                maintained_core = next((n for n in account.positions if n.startswith("option|core_put|")), None)\n                core_entry = market.quote(day, current_contract(maintained_core), "option", phase="open") if maintained_core else None\n            extra_profit3x_trigger = None\n            extra_profit3x_preselected = None\n            profit3x_trigger = None'),
    ]
    for old,new in patches:
        count=text.count(old)
        assert count == (2 if old.startswith('and not bool') else 1),(old,count)
        text=text.replace(old,new)
    out=HERE/'account_monday_isolated.py'
    out.write_text(text,encoding='utf-8')
    return load('monday_isolated_account',out)


def metrics(frame,start=None):
    z=frame if start is None else frame.loc[pd.to_datetime(frame.date)>=start]
    r=z.return_net.to_numpy(float)
    nav=np.r_[1.,np.cumprod(1+r)]
    return dict(start=str(z.date.iloc[0]),end=str(z.date.iloc[-1]),rows=len(z),
        cagr=float(nav[-1]**(252/len(r))-1),max_dd=float((nav/np.maximum.accumulate(nav)-1).min()),
        sharpe=float(np.mean(r)/np.std(r,ddof=1)*np.sqrt(252)),final_nav=float(nav[-1]))


def main():
    if (HERE/'completed.json').exists():
        raise FileExistsError('Completed first output cannot be overwritten')
    started=time.time()
    # Import with a unique name: the existing run_study module is a dependency.
    sys.path.remove(str(HERE))
    original=load('monday_previous_repair_driver',REPAIRS/'run_history.py')
    paired=load('monday_paired',original.PAIRED/'run_scan.py')
    listed=paired.listed
    source,candidates,gov,spot,lives,folders,candidate_path,im_life_path,ic_audit=listed.load_account_inputs()
    from functools import lru_cache
    source.im_mo_on=lru_cache(maxsize=None)(source.im_mo_on)
    source.ic_on=lru_cache(maxsize=None)(source.ic_on)
    source.futures_on=lru_cache(maxsize=None)(source.futures_on)
    acc=patched_engine()
    sys.path.insert(0,str(HERE))
    from monday_controller import Controller
    results,events,summaries,plans,selections,failures=[],[],[],[],[],[]
    audits={}
    protected=[ROOT/'poe_ic_im_mainline_v1_4_bot.py',ROOT/'notes/mainline_registry.md',
        ROOT/'docs/ic_im_system_mainlines_v1_spec.md',ROOT/'outputs/ic_im_system_mainlines_v1/record.md',
        STUDY/'native_account.py',REPAIRS/'daily_paths.csv.gz']
    protected+=list((ROOT/'runtime').glob('**/*.json'))
    protection={str(p.relative_to(ROOT)):sha(p) for p in protected}
    (HERE/'protected_manifest.json').write_text(json.dumps(protection,indent=2),encoding='utf-8')
    ref=pd.read_csv(REPAIRS/'daily_paths.csv.gz')
    for product in ('IC','IM'):
        # Configure the cloned engine exactly as the certified current replay.
        listed.grid_scan.configure_account(REPAIRS/'inputs'/product.lower())
        configured=listed.grid_scan.account_stage
        settings=['VERSION','REENTRY_SELECTOR','MARGIN_MODEL','EXECUTION_MODEL','LIFECYCLE_VERSION',
            'QUARTER_ROLL_AT_SIGNAL_CLOSE','PROFIT_RESTRIKE_MULTIPLE','PROFIT_EXECUTION_PHASE',
            'DISABLED_MODULES','BUYER_OPEN_SLEEVES','BUYER_OPEN_PROXY_MULTIPLIER','CORE_UNITS',
            'CORE_BUYER_SCALE','MOMENTUM_SCALE','SELLER_SCALE','SELLER_NAV_BUFFER_RATE']
        for key in settings:
            setattr(acc,key,getattr(configured,key))
        acc.HERE=REPAIRS/'inputs'/product.lower()
        lifecycle=pd.read_csv(REPAIRS/f'{product.lower()}_seller_lifecycle_fixed.csv.gz')
        audits[product]={}
        for arm in ('baseline','buy_only','carry_old'):
            acc.MONDAY_FACTORY=None if arm=='baseline' else lambda p,r,t,m,g,arm=arm: Controller(acc,arm,p,r,t,m,g)
            daily,ev,audit=acc.replay(product,'repeat_roll',source,lifecycle,candidates,gov,spot)
            assert np.isfinite(daily.nav).all() and (daily.nav>0).all()
            if arm=='baseline':
                saved=ref.loc[ref['product'].eq(product)&ref.arm.eq('both')].reset_index(drop=True)
                errors=original.compare(daily,saved)
                audits[product]['saved_baseline_parity']=errors
            assert (daily.cash_close-daily.margin_reserved).min()>-1e-7
            assert not ev.loc[ev.event.eq('trade'),'symbol'].astype(str).str.startswith('option|call|').any()
            audit['min_free_cash']=float((daily.cash_close-daily.margin_reserved).min())
            audits[product][arm]=audit
            ctl=acc.LAST_CONTROLLER
            if ctl:
                plans.extend(ctl.plans);selections.extend(ctl.selections);failures.extend(ctl.failures)
                audits[product][arm]['pending_gap_sessions']=ctl.gap_sessions
            daily['product']=product;daily['arm']=arm
            ev['product']=product;ev['arm']=arm
            results.append(daily);events.append(ev)
            end=pd.Timestamp(daily.date.iloc[-1])
            for window,years in [('full',None),('3Y',3),('1Y',1)]:
                summaries.append(dict(product=product,arm=arm,window=window,
                    **metrics(daily,None if years is None else end-pd.DateOffset(years=years))))
            print(json.dumps(dict(product=product,arm=arm,**metrics(daily)),ensure_ascii=False),flush=True)
            # Incremental checkpoints do not touch frozen baseline outputs.
            pd.concat(results,ignore_index=True).to_csv(HERE/'daily_paths.csv.gz',index=False)
            pd.concat(events,ignore_index=True).to_csv(HERE/'events.csv.gz',index=False)
            pd.DataFrame(summaries).to_csv(HERE/'window_metrics.csv',index=False)
            pd.DataFrame(plans).to_csv(HERE/'maintenance_calendar.csv',index=False)
            pd.DataFrame(selections).to_csv(HERE/'open_selections.csv',index=False)
            pd.DataFrame(failures).to_csv(HERE/'missing_opens.csv',index=False)
            (HERE/'account_audit.json').write_text(json.dumps(audits,indent=2,default=str),encoding='utf-8')
    assert protection=={str(p.relative_to(ROOT)):sha(p) for p in protected}
    manifest=dict(elapsed_seconds=time.time()-started,spec_sha256=sha(ROOT/'docs/ic_im_put_after_settlement_monday_20261021_v2_spec.md'),
        scope='Listed history theoretical account, IC fix11 full-history counterfactual; no deployment',
        sources={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),HERE/'monday_controller.py',HERE/'account_monday_isolated.py',
                 candidate_path,REPAIRS/'ic_seller_lifecycle_fixed.csv.gz',REPAIRS/'im_seller_lifecycle_fixed.csv.gz']},
        protected_files_unchanged=len(protected))
    (HERE/'completed.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
