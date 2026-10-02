from pathlib import Path
import inspect,json,hashlib
import numpy as np,pandas as pd
import research_im_short_put_recovery_atm_real_v1 as original
import research_im_short_put_recovery_atm_full_model_v1 as full
from im_put_maturity_valuation_tiers_v3 import prepare_options,actual_expiry_map,metrics
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_im_short95_percentile_entry_v1'
PRIOR=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_entry_valuation_0123_v2_entry_valuation_tier'
DIAG=ROOT/'outputs/im_valuation_entry_semantics_audit_20260914_v1/historical_continuous_percentiles.csv'
CORE=ROOT/'quant_param_scan_runs/20260914_im_original_core_put_vs_short95_v2/daily.csv.gz'
CASH=original.CASH
def main():
    state=pd.read_csv(PRIOR/'entry_valuation_state.csv',parse_dates=['date']);diag=pd.read_csv(DIAG,parse_dates=['date']);perm=pd.read_csv(PRIOR/'entry_permissions.csv',parse_dates=['date']).set_index('date')
    assert diag.date.is_unique and (pd.to_datetime(diag.sample_max_date)<diag.date.dt.to_period('M').dt.to_timestamp()).all()
    rank=state.date.map(diag.set_index('date').percentile)
    gates={'baseline_le1_mom':perm.val_le1_mom}
    for threshold in [.5,.6,.7]:
        signal=rank.notna()&rank.le(threshold)&state.momentum_120.ge(0)
        gates['pct'+str(round(threshold*100))+'_mom']=pd.Series(signal.shift(1,fill_value=False).to_numpy(),index=state.date)
    pd.DataFrame(gates).rename_axis('date').reset_index().to_csv(OUT/'entry_permissions.csv',index=False)
    calibration_start=diag.date.min();common_start=state.loc[(state.date>calibration_start),'date'].min()
    print('Shared input construction; first causal calibrated entry '+str(common_start.date()),flush=True)
    market,mb,mo,mf,model_checks,basis=full.build_inputs()
    rb=pd.read_csv(original.BASE,parse_dates=['date']);rf=pd.read_csv(original.FU,parse_dates=['date']);raw=pd.read_csv(original.OP,parse_dates=['date'])
    raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m');ro=prepare_options(raw,actual_expiry_map(raw,rb))
    source=inspect.getsource(original.run).replace('def run(base, options, futures, ratio):','def gated_run(base, options, futures, ratio):')
    source=source.replace("if state == 'idle' and i > 0 and action == '':","if state == 'idle' and i > 0 and action == '' and bool(allowed.get(day,False)):")
    (OUT/'gated_state_machine.py').write_text(source,encoding='utf-8')
    reference=pd.read_csv(PRIOR/'daily.csv',parse_dates=['date']);core=pd.read_csv(CORE,parse_dates=['date'],low_memory=False)
    ds=[];cs=[];es=[];audit={};ex=[]
    for scope,b,o,f in [('real',rb,ro,rf),('model',mb,mo,mf)]:
        for gate,allowed in gates.items():
            ns=dict(vars(original));ns['allowed']=allowed;exec(compile(source,str(OUT/'gated_state_machine.py'),'exec'),ns)
            d,e,c=ns['gated_run'](b,o,f,.95);key=scope+'_'+gate
            assert c.entry_date.map(pd.Timestamp).map(allowed).fillna(False).all()
            ledger=float(abs((d.pnl-d.cost).sum()-c.realized_pnl.sum()));assert ledger<1e-12
            assert np.allclose(d.nav,(1+d.return_net).cumprod(),atol=1e-12)
            audit[key]={'ledger_error':ledger,'all_entries_prior_permission':True,'NAV_recomposition':True}
            if gate.startswith('baseline'):
                old=reference[reference.candidate==scope+'_val_le1_mom'].reset_index(drop=True);err=float(abs(d.return_net-old.return_net).max());assert err<1e-12;audit[key]['baseline_parity']=err
            d['candidate']=key;c['candidate']=key;e['candidate']=key;ds.append(d);cs.append(c);es.append(e)
            half=d.copy();half.return_net=.5*d.return_net+.5*CASH;half.nav=(1+half.return_net).cumprod();half['candidate']=key+'_half';ds.append(half)
            assigned=c[c.assignment_date.notna() & c.assignment_date.astype(str).str.strip().ne('')]
            ex.append(dict(candidate=key,cycles=len(c),assignments=len(assigned),IM_days=int(d.state.eq('future').sum()),IM_fraction=float(d.state.eq('future').mean()),idle_fraction=float(d.state.eq('idle').mean()),max_recovery_days=float(c.recovery_days.max()) if 'recovery_days' in c else 0,unclosed_cycles=int((~c.closed).sum()),permission_fraction=float(allowed.reindex(b.date).fillna(False).mean())))
            print(key,metrics(d.return_net),flush=True)
        d=core[core.candidate==scope+'_original_roll_im_core_put102'].copy();d['candidate']=scope+'_original_core_put102_half';ds.append(d)
    daily=pd.concat(ds,ignore_index=True);daily.to_csv(OUT/'daily.csv.gz',index=False);pd.concat(cs).to_csv(OUT/'cycles.csv',index=False);pd.concat(es).to_csv(OUT/'events.csv',index=False);pd.DataFrame(ex).to_csv(OUT/'exposure.csv',index=False)
    summary=[];wide=[];unavailable={}
    for key,d in daily.groupby('candidate'):
        d=d.sort_values('date');w=dict(candidate=key)
        windows=[('full',d.date.min())]+[(name,d.date.max()-pd.DateOffset(years=y)) for name,y in [('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]]
        windows += [('calibrated_common',max(common_start,d.date.min())),('postlisting_common',max(pd.Timestamp('2022-07-22'),d.date.min()))]
        for segment,cut in windows:
            ok=cut>=d.date.min();z=d[d.date>=cut] if ok else d.iloc[:0]
            met=metrics(z.return_net) if ok else {k:'N/A' for k in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            if not ok:unavailable.setdefault(key,{})[segment]='Real IM/MO begins2022-07-22; no model substitute'
            summary.append(dict(candidate=key,segment=segment,start=str(cut.date()),end=str(d.date.max().date()),rows=len(z),**met))
            for k in ['ann_return','max_dd','sharpe_repo']:w[k+'_'+segment]=met[k]
        wide.append(w)
    ss=pd.DataFrame(summary);ss.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    # Calendar halves provide diagnostics only, not independent out-of-sample validation.
    split=[]
    for key,d in daily.groupby('candidate'):
        if 'original_core' in key:continue
        for name,start,end in [('early_calibrated','2020-07-02','2023-08-13'),('late','2023-08-14','2026-08-14')]:
            z=d[d.date.between(pd.Timestamp(start),pd.Timestamp(end))]
            if len(z):split.append(dict(candidate=key,segment=name,start=str(z.date.min().date()),end=str(z.date.max().date()),rows=len(z),**metrics(z.return_net)))
    pd.DataFrame(split).to_csv(OUT/'calendar_split_diagnostics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    paths=[Path(__file__),OUT/'spec.md',DIAG,PRIOR/'entry_valuation_state.csv',PRIOR/'entry_permissions.csv',PRIOR/'daily.csv',CORE,original.BASE,original.OP,original.FU,Path(original.__file__),Path(full.__file__)]
    meta.update(scan_type='preselected_percentile_entry_scan',baseline={'candidate':'real_baseline_le1_mom'},candidate_grid=sorted(daily.candidate.unique()),parameters=[.5,.6,.7],data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},cost_model={'reserve':.3,'cash_annual':.03,'one_way_notional':.0001,'half_allocation':'50% full strategy daily return+50% cash, daily blend, no half-unit cycle rerun'},unavailable_segments=unavailable,audit=audit,model_checks=model_checks,basis_calibration=basis,calibration_first_signal_date=str(calibration_start.date()),calibrated_common_first_execution=str(common_start.date()),assumptions='Percentile is weakECDF against57 prior month-end economic scores. Continuous rank<=50/60/70%, TRI MOM120>=0. Signal shifted on full state BEFORE real slice. Rank missing beforeJuly2020 disables entries. Current0/1 tier baseline unchanged. Gate only initial shortPut, existing Put/IM never exited because gate changed. No longPut/Call/grid for shortPut strategies. Original core Put unchanged, separate native model and quarter-roll comparison.',limitations='Cold start2015-2020 cash makes full-window parameter comparison confounded; calibrated_common resolves calendar availability, never resets open positions. Preselected coarse neighboring thresholds, no fine optimum; calendar splits not independentOOS because thresholds chosen after viewing historical semantic diagnostics. Full model theoreticalBS/synthetic availability/expiry-close proxy/ex-post10.33%carry; originalcore TRI+0.0003dailycarry and quarterly roll, no matched-price attribution. No bidask/dynamicmargin/liquidation. Native real costs and liquidity inherited. Historical cutoffAug14, not current signal. No cache writes or frozen/production modification.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'record.md').write_text('# IM卖95%Put：连续估值分位入场扫描\n\n## Data Snapshot\nReal2022-07-22..2026-08-14,986days;uniformModel2015-04-16..2026-08-14,2756days.\n\n## Implementation\n'+meta['assumptions']+'\n\n## Cost\n'+json.dumps(meta['cost_model'],ensure_ascii=False)+'\n\n## Stability\n'+meta['limitations']+'\n\n## Decision\nresearch_only_pending_result_assessment\n\n## Audit\n'+json.dumps(audit,indent=2)+'\n\n## Results\n'+ss.to_string(index=False)+'\n\n## Exposure\n'+pd.DataFrame(ex).to_string(index=False),encoding='utf-8')
    (OUT/'command_log.txt').open('a',encoding='utf-8').write('\npython -X utf8 research_im_short95_percentile_entry_v1.py\n')
    print(ss[ss.segment.isin(['full','calibrated_common'])].to_string(index=False),flush=True)
if __name__=='__main__':main()
