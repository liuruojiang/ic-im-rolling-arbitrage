"""Independently verify ledger, close fills, calendars and information phases."""
from pathlib import Path
from datetime import date,timedelta
import importlib.util
import hashlib
import json
import sys
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
PREV=ROOT/'quant_research_runs/20261021_put_after_settlement_monday_v2'
REPAIRS=ROOT/'quant_research_runs/20261016_put_execution_repairs_v1'
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
sys.path[:0]=[str(ROOT),str(NATIVE)]


def load(name,p):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


def main():
    daily=pd.read_csv(HERE/'daily_paths.csv.gz');events=pd.read_csv(HERE/'events.csv.gz',low_memory=False)
    stats=pd.read_csv(HERE/'window_metrics.csv');plans=pd.read_csv(HERE/'maintenance_calendar.csv')
    selected=pd.read_csv(HERE/'selections.csv');audits=json.loads((HERE/'account_audit.json').read_text())
    eng=load('close_audit_engine',HERE/'account_close_isolated.py')
    base=load('close_audit_base',HERE/'backups/monday_controller.py')
    mod=load('close_audit_controller',HERE/'close_controller.py');Close=mod.make_close_class(base)
    from native_fix4_historical_sources_v2 import DatedSources
    source=DatedSources()
    gov=pd.read_csv(ROOT/'data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv',
        parse_dates=['date']).set_index('date').gov10y_yield
    report={'ledger':{},'fills':{},'causality':{},'price_evidence':[]}
    trades=events.loc[events.event.eq('trade')].copy()
    for (product,arm),z in daily.groupby(['product','arm'],sort=False):
        z=z.reset_index(drop=True)
        ev=events.loc[events['product'].eq(product)&events.arm.eq(arm)]
        initial=audits[product][arm]['initial_capital']
        movement=ev.groupby('date')[['equity_change','cash_change']].sum().reindex(z.date,fill_value=0)
        equity=initial+movement.equity_change.cumsum().to_numpy();cash=initial+movement.cash_change.cumsum().to_numpy()
        error=max(float(abs(equity-z.equity_close.to_numpy()).max()),float(abs(cash-z.cash_close.to_numpy()).max()))
        assert error<2e-6,(product,arm,error)
        nav=equity/initial;ret=np.diff(np.r_[1.,nav])/np.r_[1.,nav][:-1]
        assert np.max(abs(ret-z.return_net))<1e-13
        fees=ev.loc[ev.event.eq('trade')].groupby('date').fee.sum().reindex(z.date,fill_value=0).cumsum().to_numpy()
        assert np.max(abs(fees-z.fees_cumulative))<1e-6
        for m in stats.loc[stats['product'].eq(product)&stats.arm.eq(arm)].itertuples():
            r=ret[pd.to_datetime(z.date)>=pd.Timestamp(m.start)]
            path=np.r_[1.,np.cumprod(1+r)]
            assert abs(path[-1]**(252/len(r))-1-m.cagr)<1e-12
            assert abs((path/np.maximum.accumulate(path)-1).min()-m.max_dd)<1e-12
        report['ledger'][f'{product}_{arm}']={'rows':len(z),'max_money_error':error}
    for product in ('IC','IM'):
        risk=pd.read_csv(REPAIRS/'inputs'/product.lower()/f'native_fix4_{product.lower()}_risk_signals_v1.csv.gz')
        target=pd.read_csv(REPAIRS/'inputs'/product.lower()/f'native_fix4_{product.lower()}_noseller_targets_{"v2" if product=="IC" else "v3"}.csv.gz')
        calendar=pd.to_datetime(risk.signal_date).dt.date.tolist()
        market=eng.Market(source,product)
        for p in plans.loc[plans['product'].eq(product)].itertuples():
            d=date.fromisoformat(p.settlement)+timedelta(days=1)
            while d.weekday()!=0:d+=timedelta(days=1)
            intended=next((x for x in calendar if x>=d),None)
            actual=date.fromisoformat(p.due) if pd.notna(p.due) else None
            assert intended==actual
        counter=0
        for r in trades.loc[trades['product'].eq(product)&trades.price_basis.eq('Monday_monthly_maintenance_actual_close')].itertuples():
            assert r.arm in ('close_current','close_frozen') and r.phase=='close'
            day=date.fromisoformat(r.date);contract=r.symbol.split('|')[-1]
            chain=market.option_chain(day);key='contract' if product=='IC' else 'instrument'
            price='last' if product=='IC' else 'lastprice'
            found=chain.loc[chain[key].eq(contract),price]
            assert len(found)==1 and found.iloc[0]>0 and float(found.iloc[0])==r.price
            expected=abs(r.quantity_change)*(10000 if product=='IC' else 100)*market.spot(day)*.0005
            assert abs(expected-r.fee)<1e-7
            assert not trades.loc[trades['product'].eq(product)&trades.arm.eq(r.arm)&
                trades.date.eq(r.date)&trades.symbol.str.startswith('option|')&
                trades.price_basis.eq('Monday_monthly_maintenance_actual_close'),'phase'].eq('open').any()
            report['price_evidence'].append(dict(product=product,arm=r.arm,date=r.date,
                contract=contract,signal_date=r.signal_date,quantity_change=r.quantity_change,actual_close=r.price))
            counter+=1
        report['fills'][product]=counter
        poison_count=0;frozen_count=0
        for z in selected.loc[selected['product'].eq(product)&selected.arm.isin(['close_current','close_frozen'])].itertuples():
            if poison_count>=10:break
            day=date.fromisoformat(z.date);decision=date.fromisoformat(z.signal_date)
            idx=risk.index[risk.signal_date.eq(z.signal_date)][0]
            rr=risk.iloc[idx];tt=target.iloc[idx].copy()
            tt['quarter_core_eod']=target.loc[target.signal_date.eq(z.date),'quarter_core_eod'].iloc[0]
            ctl=Close(eng,z.arm,product,risk,target,market,gov)
            result=ctl.select(day,decision,rr,tt,'close',z.sleeve)
            assert result['contract']==z.contract and result['price']==z.price
            class Poison:
                def option_chain(self,d):
                    panel=market.option_chain(d).copy()
                    if d==day:
                        for c in ('open','openprice','volume','position','settle'):
                            if c in panel:panel[c]=np.nan
                    return panel
                def spot(self,d):return market.spot(d)
                def spot_open(self,d):raise AssertionError('open accessed in close maintenance')
                def quote(self,d,c,k,**kw):
                    assert kw.get('phase')=='close'
                    return market.quote(d,c,k,**kw)
            ctl.market=Poison()
            changed=ctl.select(day,decision,rr,tt,'close',z.sleeve)
            assert result==changed
            poison_count+=1
        # Frozen strike identity must not depend on current underlying close.
        for z in selected.loc[selected['product'].eq(product)&selected.arm.eq('close_frozen')].itertuples():
            if frozen_count>=5:break
            day=date.fromisoformat(z.date);decision=date.fromisoformat(z.signal_date)
            assert decision<day
            idx=risk.index[risk.signal_date.eq(z.signal_date)][0]
            rr=risk.iloc[idx];tt=target.iloc[idx].copy()
            ctl=Close(eng,z.arm,product,risk,target,market,gov)
            original=ctl.select(day,decision,rr,tt,'close',z.sleeve)
            # For IM no Delta sizing consumes current spot; IC's quantity can
            # change with close Delta, while strike identity remains frozen.
            original_spot=market.spot
            class SpotShift:
                def option_chain(self,d):return market.option_chain(d)
                def spot(self,d):return original_spot(d)*(1.0001 if d==day else 1.)
                def quote(self,*args,**kw):return market.quote(*args,**kw)
            ctl.market=SpotShift()
            shifted=ctl.select(day,decision,rr,tt,'close',z.sleeve)
            assert original['contract']==shifted['contract']
            frozen_count+=1
        report['causality'][product]=dict(close_ignores_open_volume_cases=poison_count,
            frozen_strike_independent_of_current_spot_cases=frozen_count)
    done=json.loads((HERE/'completed.json').read_text())
    for p,h in json.loads((HERE/'protected_manifest.json').read_text()).items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    for p,h in done['sources'].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    assert hashlib.sha256((ROOT/'docs/ic_im_put_monday_close_20261021_v1_spec.md').read_bytes()).hexdigest()==done['spec_sha256']
    # Exact original data files are rehashed, not merely copied from metadata.
    data=json.loads((PREV/'data_manifest.json').read_text())
    for p,h in data.items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    (HERE/'data_manifest.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    pd.DataFrame(report.pop('price_evidence')).to_csv(HERE/'verified_close_fills.csv',index=False)
    report.update(status='PASS',protected_files_unchanged=done['protected_files_unchanged'],data_files_unchanged=len(data))
    (HERE/'independent_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
