"""Independent source-membership, tenor, real-close and ledger checks."""
from pathlib import Path
from datetime import date,timedelta
import sys,json,hashlib,re,calendar
import pandas as pd
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
NATIVE=ROOT/'outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925'
sys.path[:0]=[str(ROOT),str(NATIVE)]


def expiry(contract):
    m=re.search(r'P(\d{2})(\d{2})M',contract);y=2000+int(m[1]);mo=int(m[2])
    return date(y,mo,[d for d in range(1,calendar.monthrange(y,mo)[1]+1) if date(y,mo,d).weekday()==2][3])


def main():
    from native_fix4_historical_sources_v2 import DatedSources
    source=DatedSources();trading=source.ic_etf.index.date.tolist()
    dates=sorted(set(trading));master=source.ic_master
    memberships=master.groupby('date').contract_month.agg(lambda x:set(x.dt.strftime('%Y%m'))).to_dict()
    daily=pd.read_csv(HERE/'daily_paths.csv.gz');ev=pd.read_csv(HERE/'events.csv.gz',low_memory=False)
    stats=pd.read_csv(HERE/'window_metrics.csv');plans=pd.read_csv(HERE/'maintenance_calendar.csv')
    sels=pd.read_csv(HERE/'selections.csv');audit=json.loads((HERE/'account_audit.json').read_text())
    out={'ledger':{},'calendar':{},'tenor':{},'holding':{}}
    calendar_rows=[];holding_rows=[];real_fills=[]
    for arm,z in daily.groupby('arm',sort=False):
        e=ev.loc[ev.arm.eq(arm)];initial=audit[arm]['initial_capital']
        movement=e.groupby('date')[['equity_change','cash_change']].sum().reindex(z.date,fill_value=0).cumsum()
        equity=initial+movement.equity_change.to_numpy();cash=initial+movement.cash_change.to_numpy()
        error=max(float(abs(equity-z.equity_close.to_numpy()).max()),float(abs(cash-z.cash_close.to_numpy()).max()))
        assert error<2e-6
        path=equity/initial;returns=np.diff(np.r_[1.,path])/np.r_[1.,path][:-1]
        assert np.max(abs(returns-z.return_net))<1e-13
        for m in stats.loc[stats.arm.eq(arm)].itertuples():
            r=returns[pd.to_datetime(z.date)>=pd.Timestamp(m.start)];nav=np.r_[1.,np.cumprod(1+r)]
            assert abs(nav[-1]**(252/len(r))-1-m.cagr)<1e-12
            assert abs((nav/np.maximum.accumulate(nav)-1).min()-m.max_dd)<1e-12
        fees=e.loc[e.event.eq('trade')].groupby('date').fee.sum().reindex(z.date,fill_value=0).cumsum()
        assert np.max(abs(fees.to_numpy()-z.fees_cumulative))<1e-6
        assert not e.loc[e.event.eq('trade'),'symbol'].astype(str).str.startswith('option|call|').any()
        out['ledger'][arm]=dict(rows=len(z),max_money_error=error)
        # Reconstruct held EOD quantities to distinguish purchase tenor from
        # the passage of time while a Put is held.
        buyers=e.loc[e.symbol.astype(str).str.startswith(('option|core_put|','option|momentum_put|'))]
        q=buyers.pivot_table(index='date',columns='symbol',values='quantity_change',aggfunc='sum',fill_value=0)
        q=q.reindex(z.date,fill_value=0).cumsum()
        assert q.min().min()>-1e-5
        positive_days=0;all_3m_days=0;min_held=10000
        for day,row in q.iterrows():
            held=[c.split('|')[-1] for c,v in row.items() if v>1e-5]
            if not held:continue
            d=date.fromisoformat(day);floor=(pd.Timestamp(d)+pd.DateOffset(months=3)).date()
            ex=[expiry(c) for c in held];minimum=min((x-d).days for x in ex)
            good=all(x>=floor for x in ex)
            positive_days+=1;all_3m_days+=int(good);min_held=min(min_held,minimum)
            holding_rows.append(dict(arm=arm,date=day,put_contracts=';'.join(held),min_remaining_days=minimum,all_at_least_3m=good))
        out['holding'][arm]=dict(put_held_sessions=positive_days,all_held_at_least_3m_sessions=all_3m_days,
            all_at_least_3m_fraction=all_3m_days/positive_days,min_held_days=min_held)
    for arm in ('wait_nearest','wait_min3'):
        pp=plans.loc[plans.arm.eq(arm)]
        for r in pp.itertuples():
            anchor=memberships[pd.Timestamp(r.settlement)]
            following=[d for d in sorted(memberships) if d>pd.Timestamp(r.settlement)]
            first=next((d for d in following if memberships[d]-anchor),None)
            observed=pd.Timestamp(r.listing_observed) if pd.notna(r.listing_observed) else None
            assert first==observed,(r.settlement,first,observed)
            # Official monthly node: fourth Wednesday, holiday-forward to the
            # next observed actual trading day; new month starts next session.
            t=date.fromisoformat(r.settlement)
            nominal=date(t.year,t.month,[d for d in range(1,calendar.monthrange(t.year,t.month)[1]+1)
                        if date(t.year,t.month,d).weekday()==2][3])
            actual_exp=next((d for d in dates if d>=nominal),None)
            new_session=next((d for d in dates if actual_exp and d>actual_exp),None)
            calendar_rows.append(dict(arm=arm,settlement=r.settlement,nominal_expiry=str(nominal),
                actual_expiry=str(actual_exp),rule_new_month_day=str(new_session),
                observed_new_month_day=str(observed.date()) if observed else None,
                days_difference=(observed.date()-new_session).days if observed is not None and new_session else None))
        ss=sels.loc[sels.arm.eq(arm)].copy()
        mapping=pp.loc[pp.executed.notna()].set_index('executed').settlement
        assert (ss.signal_date==ss.date.map(mapping)).all()
        ss['under3']=pd.to_datetime(ss.expiry)<pd.to_datetime(ss.target_expiry)
        if arm=='wait_min3':assert not ss.under3.any()
        out['tenor'][arm]=dict(selections=len(ss),under3=int(ss.under3.sum()),min_days=int(ss.remaining_days.min()),
            max_days=int(ss.remaining_days.max()),mean_days=float(ss.remaining_days.mean()))
        fills=ev.loc[ev.arm.eq(arm)&ev.event.eq('trade')&ev.price_basis.eq('Monday_monthly_maintenance_actual_close')]
        assert (fills.phase=='close').all()
        assert (fills.signal_date==fills.date.map(mapping)).all()
        for f in fills.itertuples():
            d=date.fromisoformat(f.date);spot,c=source.ic_on(d);contract=f.symbol.split('|')[-1]
            quote=c.loc[c.contract.eq(contract),'last']
            assert len(quote)==1 and quote.iloc[0]>0 and float(quote.iloc[0])==f.price
            fee=abs(f.quantity_change)*10000*spot*.0005
            assert abs(fee-f.fee)<1e-7
            real_fills.append(dict(arm=arm,date=f.date,signal_date=f.signal_date,contract=contract,
                quantity_change=f.quantity_change,price=f.price))
        out['calendar'][arm]=dict(maintenance_events=len(pp),real_close_trade_sides=len(fills))
    cal=pd.DataFrame(calendar_rows);cal.to_csv(HERE/'calendar_rule_crosscheck.csv',index=False)
    out['listing_calendar_mismatches']=cal.loc[cal.days_difference.fillna(0).ne(0)].to_dict('records')
    pd.DataFrame(holding_rows).to_csv(HERE/'daily_protection_tenor.csv.gz',index=False)
    pd.DataFrame(real_fills).to_csv(HERE/'verified_close_fills.csv',index=False)
    done=json.loads((HERE/'completed.json').read_text())
    for p,h in json.loads((HERE/'protected_manifest.json').read_text()).items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    for p,h in done['sources'].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    assert hashlib.sha256((ROOT/'docs/ic_put_wait_etf_listing_20261021_v1_spec.md').read_bytes()).hexdigest()==done['spec_sha256']
    data=json.loads((ROOT/'quant_research_runs/20261021_put_monday_close_v1/data_manifest.json').read_text())
    for p,h in data.items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h,p
    (HERE/'data_manifest.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    out.update(status='PASS',protected_files_unchanged=done['protected_files_unchanged'],data_files_unchanged=len(data))
    (HERE/'independent_audit.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,indent=2))


if __name__=='__main__':main()
