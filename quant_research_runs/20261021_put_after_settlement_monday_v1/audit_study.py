"""Independent ledger arithmetic, actual-open and information-time audit."""
from pathlib import Path
from datetime import date,timedelta
import importlib.util
import hashlib
import json
import sys
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
REPAIRS=ROOT/'quant_research_runs/20261016_put_execution_repairs_v1'
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
sys.path[:0]=[str(ROOT),str(NATIVE),str(HERE)]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m)
    return m


def main():
    done=json.loads((HERE/'completed.json').read_text())
    daily=pd.read_csv(HERE/'daily_paths.csv.gz')
    events=pd.read_csv(HERE/'events.csv.gz')
    stats=pd.read_csv(HERE/'window_metrics.csv')
    plans=pd.read_csv(HERE/'maintenance_calendar.csv')
    selections=pd.read_csv(HERE/'open_selections.csv')
    audits=json.loads((HERE/'account_audit.json').read_text())
    controller=load('audit_monday_controller',HERE/'monday_controller.py')
    engine=load('audit_monday_engine',HERE/'account_monday_isolated.py')
    from native_fix4_historical_sources_v2 import DatedSources
    source=DatedSources()
    gov=pd.read_csv(ROOT/'data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv',
                   parse_dates=['date']).set_index('date').gov10y_yield
    summary={'ledger':{},'actual_open_fills':{},'calendar':{},'causality':{},'monthly_effect':{}}
    trades=events.loc[events.event.eq('trade')].copy()
    actual_selections=[]
    for (product,arm),z in daily.groupby(['product','arm'],sort=False):
        z=z.reset_index(drop=True)
        ev=events.loc[events['product'].eq(product)&events.arm.eq(arm)]
        initial=audits[product][arm]['initial_capital']
        # Rebuild capital from the ledger, not from return_net or saved nav.
        movement=ev.groupby('date')[['equity_change','cash_change']].sum().reindex(z.date,fill_value=0)
        equity=initial+movement.equity_change.cumsum().to_numpy()
        cash=initial+movement.cash_change.cumsum().to_numpy()
        eqerr=float(abs(equity-z.equity_close.to_numpy()).max())
        caerr=float(abs(cash-z.cash_close.to_numpy()).max())
        assert max(eqerr,caerr)<2e-6,(product,arm,eqerr,caerr)
        nav=equity/initial
        reconstructed_returns=np.diff(np.r_[1.,nav])/np.r_[1.,nav][:-1]
        assert np.max(abs(reconstructed_returns-z.return_net))<1e-13
        fees=ev.loc[ev.event.eq('trade')].groupby('date').fee.sum().reindex(z.date,fill_value=0).cumsum()
        assert np.max(abs(fees.to_numpy()-z.fees_cumulative))<1e-6
        for m in stats.loc[stats['product'].eq(product)&stats.arm.eq(arm)].itertuples():
            r=reconstructed_returns[pd.to_datetime(z.date)>=pd.Timestamp(m.start)]
            path=np.r_[1.,np.cumprod(1+r)]
            cagr=path[-1]**(252/len(r))-1
            dd=(path/np.maximum.accumulate(path)-1).min()
            assert abs(cagr-m.cagr)<1e-12 and abs(dd-m.max_dd)<1e-12
        assert not trades.loc[trades['product'].eq(product)&trades.arm.eq(arm),'symbol'].str.startswith('option|call|').any()
        summary['ledger'][f'{product}_{arm}']=dict(rows=len(z),equity_error=eqerr,cash_error=caerr)
    for product in ('IC','IM'):
        risk=pd.read_csv(REPAIRS/'inputs'/product.lower()/f'native_fix4_{product.lower()}_risk_signals_v1.csv.gz')
        targets=pd.read_csv(REPAIRS/'inputs'/product.lower()/f'native_fix4_{product.lower()}_noseller_targets_{"v2" if product=="IC" else "v3"}.csv.gz')
        calendar=pd.to_datetime(risk.signal_date).dt.date.tolist()
        market=engine.Market(source,product)
        for row in plans.loc[plans['product'].eq(product)].itertuples():
            settle=date.fromisoformat(row.settlement)
            cursor=settle+timedelta(days=1)
            while cursor.weekday()!=0:
                cursor+=timedelta(days=1)
            intended=next((d for d in calendar if d>=cursor),None)
            actual=None if pd.isna(row.due) else date.fromisoformat(row.due)
            assert intended==actual
            if pd.notna(row.executed):
                assert date.fromisoformat(row.executed)>=actual
        summary['calendar'][product]=dict(events=int(plans['product'].eq(product).sum()))
        selected=selections.loc[selections['product'].eq(product)]
        checks=0
        poison_checks=0
        for row in selected.itertuples():
            ev=trades.loc[trades['product'].eq(product)&trades.arm.eq(row.arm)&trades.date.eq(row.date)&
                          trades.phase.eq('open')&trades.symbol.str.endswith('|'+row.contract)&trades.quantity_change.gt(0)]
            if ev.empty:
                continue
            day=date.fromisoformat(row.date)
            chain=market.option_chain(day)
            n='contract' if product=='IC' else 'instrument'
            col='open' if product=='IC' else 'openprice'
            raw=chain.loc[chain[n].eq(row.contract),col]
            assert len(raw)==1 and float(raw.iloc[0])>0
            assert np.allclose(ev.price.to_numpy(),float(raw.iloc[0]),atol=0,rtol=0)
            assert float(row.price)==float(raw.iloc[0])
            checks+=len(ev)
            # Link the actual filled selection to the last settlement event.
            plan=plans.loc[plans['product'].eq(product)&plans.arm.eq(row.arm)&plans.executed.eq(row.date)].iloc[0]
            base_events=audits[product]['baseline']['maintenance_events']
            sleeve='core_put' if row.sleeve in ('shared','core') else 'momentum_put'
            before=[x for x in base_events if x['date']==plan.settlement and x['sleeve']==sleeve]
            old=before[0].get('contract') if before else None
            old_expiry=engine.option_expiry(product,old) if isinstance(old,str) else None
            actual_selections.append(dict(product=product,arm=row.arm,date=row.date,
                settlement=plan.settlement,sleeve=row.sleeve,contract=row.contract,
                baseline_contract=old,expiry=row.expiry,baseline_expiry=str(old_expiry) if old_expiry else None,
                month_changed=old_expiry is not None and str(old_expiry)!=row.expiry,
                newly_listed_month=row.newly_listed_month,remaining_days=row.remaining_days,
                target_error_days=row.target_error_days,
                baseline_remaining_days=(old_expiry-date.fromisoformat(plan.settlement)).days if old_expiry else None))
            # Real inputs: poison today's close/settlement/volume/OI. The result
            # must be identical to the observable-open selector.
            if poison_checks<5:
                idx=risk.index[risk.signal_date.eq(row.signal_date)][0]
                r=risk.iloc[idx];t=targets.iloc[idx]
                ctl=controller.Controller(engine,row.arm,product,risk,targets,market,gov)
                ordinary=ctl.select(day,date.fromisoformat(row.signal_date),r,t,'open',row.sleeve)
                class Poison:
                    def option_chain(self,d):
                        panel=market.option_chain(d).copy()
                        if d==day:
                            for c in ('last','lastprice','close','settle','volume','position'):
                                if c in panel: panel[c]=np.nan
                        return panel
                    def spot(self,d):
                        assert d<day,'current close accessed'
                        return market.spot(d)
                    def spot_open(self,d): return market.spot_open(d)
                    def quote(self,d,c,k,**kw):
                        assert kw.get('phase')=='open','future close accessed'
                        return market.quote(d,c,k,**kw)
                ctl.market=Poison()
                polluted=ctl.select(day,date.fromisoformat(row.signal_date),r,t,'open',row.sleeve)
                assert ordinary==polluted
                poison_checks+=1
        summary['actual_open_fills'][product]=checks
        summary['causality'][product]=dict(real_poison_cases=poison_checks,status='PASS')
    actual=pd.DataFrame(actual_selections)
    actual.to_csv(HERE/'actual_monthly_filled_selections.csv',index=False)
    for (product,arm),z in actual.groupby(['product','arm']):
        # A shared IC selection may generate fills for both sleeves; count it once.
        summary['monthly_effect'][f'{product}_{arm}']=dict(selections=len(z),
            changed_months=int(z.month_changed.sum()),newly_listed=int(z.newly_listed_month.sum()),
            mean_remaining_days=float(z.remaining_days.mean()),
            mean_target_error=float(z.target_error_days.mean()))
    protected=json.loads((HERE/'protected_manifest.json').read_text())
    for p,h in protected.items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    for p,h in done['sources'].items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    assert hashlib.sha256((ROOT/'docs/ic_im_put_after_settlement_monday_20261021_v1_spec.md').read_bytes()).hexdigest()==done['spec_sha256']
    summary['protected_files_unchanged']=len(protected)
    summary['status']='PASS'
    (HERE/'independent_audit.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
