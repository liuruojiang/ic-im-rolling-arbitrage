"""Independent original-settlement decision-date and ledger/price audit."""
from pathlib import Path
from datetime import date
import importlib.util,sys,json,hashlib
import pandas as pd
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
sys.path[:0]=[str(ROOT),str(NATIVE)]


def main():
    d=pd.read_csv(HERE/'daily_paths.csv.gz');ev=pd.read_csv(HERE/'events.csv.gz',low_memory=False)
    metrics=pd.read_csv(HERE/'window_metrics.csv');plans=pd.read_csv(HERE/'maintenance_calendar.csv')
    sels=pd.read_csv(HERE/'selections.csv');a=json.loads((HERE/'account_audit.json').read_text())
    out={'ledger':{},'settlement_decision_date_checks':{},'actual_close_fill_checks':{}}
    for (p,arm),z in d.groupby(['product','arm'],sort=False):
        e=ev.loc[ev['product'].eq(p)&ev.arm.eq(arm)]
        initial=a[p][arm]['initial_capital']
        changes=e.groupby('date')[['equity_change','cash_change']].sum().reindex(z.date,fill_value=0).cumsum()
        eq=initial+changes.equity_change.to_numpy();cash=initial+changes.cash_change.to_numpy()
        error=max(float(abs(eq-z.equity_close.to_numpy()).max()),float(abs(cash-z.cash_close.to_numpy()).max()))
        assert error<2e-6
        nav=eq/initial;r=np.diff(np.r_[1.,nav])/np.r_[1.,nav][:-1]
        assert abs(r-z.return_net.to_numpy()).max()<1e-13
        for m in metrics.loc[metrics['product'].eq(p)&metrics.arm.eq(arm)].itertuples():
            rs=r[pd.to_datetime(z.date)>=pd.Timestamp(m.start)];path=np.r_[1.,np.cumprod(1+rs)]
            assert abs(path[-1]**(252/len(rs))-1-m.cagr)<1e-12
            assert abs((path/np.maximum.accumulate(path)-1).min()-m.max_dd)<1e-12
        fees=e.loc[e.event.eq('trade')].groupby('date').fee.sum().reindex(z.date,fill_value=0).cumsum()
        assert abs(fees.to_numpy()-z.fees_cumulative.to_numpy()).max()<1e-6
        out['ledger'][f'{p}_{arm}']={'rows':len(z),'money_error':error}
    from native_fix4_historical_sources_v2 import DatedSources
    source=DatedSources()
    changed=[]
    for p in ['IC','IM']:
        z=sels.loc[sels['product'].eq(p)&sels.arm.eq('settlement_frozen')]
        mapping=plans.loc[plans['product'].eq(p)&plans.arm.eq('settlement_frozen')&plans.executed.notna()].set_index('executed').settlement
        assert not mapping.index.duplicated().any()
        assert (z.signal_date==z.date.map(mapping)).all()
        out['settlement_decision_date_checks'][p]=len(z)
        # Show the old previous-session decision discrepancies separately.
        old=sels.loc[sels['product'].eq(p)&sels.arm.eq('close_frozen')].copy()
        oldmap=plans.loc[plans['product'].eq(p)&plans.arm.eq('close_frozen')&plans.executed.notna()].set_index('executed').settlement
        old['settlement']=old.date.map(oldmap)
        changed+=old.loc[old.signal_date.ne(old.settlement),['product','date','signal_date','settlement','sleeve','contract']].to_dict('records')
        e=ev.loc[ev['product'].eq(p)&ev.arm.eq('settlement_frozen')&ev.event.eq('trade')&
                 ev.price_basis.eq('Monday_monthly_maintenance_actual_close')]
        assert (e.phase=='close').all()
        assert (e.signal_date==e.date.map(mapping)).all()
        assert not ev.loc[ev['product'].eq(p)&ev.arm.eq('settlement_frozen')&ev.event.eq('trade'),'symbol'].astype(str).str.startswith('option|call|').any()
        for f in e.itertuples():
            day=date.fromisoformat(f.date);c=f.symbol.split('|')[-1]
            if p=='IC':
                spot,chain=source.ic_on(day);name='contract';price='last'
            else:
                chain=source.im_mo_on(day);name='instrument';price='lastprice'
            raw=chain.loc[chain[name].eq(c),price]
            assert len(raw)==1 and raw.iloc[0]>0 and float(raw.iloc[0])==f.price
        out['actual_close_fill_checks'][p]=len(e)
    pd.DataFrame(changed).to_csv(HERE/'old_decision_day_discrepancies.csv',index=False)
    completed=json.loads((HERE/'completed.json').read_text())
    for p,h in json.loads((HERE/'protected_manifest.json').read_text()).items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    for p,h in completed['sources'].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    assert hashlib.sha256((ROOT/'docs/ic_im_put_monday_settlement_frozen_20261021_v1_spec.md').read_bytes()).hexdigest()==completed['spec_sha256']
    out.update(status='PASS',protected_files_unchanged=completed['protected_files_unchanged'],
               old_selection_rows_on_wrong_information_day=len(changed))
    (HERE/'independent_audit.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))


if __name__=='__main__':main()
