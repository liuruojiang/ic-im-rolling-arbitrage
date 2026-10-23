from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
from im_put_maturity_valuation_tiers_v3 import metrics
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_im_short95_remove_mom_v1'
SOURCE=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_entry_valuation_0123_v2_entry_valuation_tier'
CORE=ROOT/'quant_param_scan_runs/20260914_im_original_core_put_vs_short95_v2'
CASH=1.03**(1/252)-1
def main():
    all_d=pd.read_csv(SOURCE/'daily.csv',parse_dates=['date']);all_c=pd.read_csv(SOURCE/'cycles.csv');permission=pd.read_csv(SOURCE/'entry_permissions.csv',parse_dates=['date']).set_index('date');core=pd.read_csv(CORE/'daily.csv.gz',parse_dates=['date'],low_memory=False)
    ds=[];cs=[];audit={};ex=[]
    for scope in ['real','model']:
        for gate in ['val_le1_mom','val_le1']:
            key=scope+'_'+gate;d=all_d[all_d.candidate==key].copy();c=all_c[all_c.candidate==key].copy()
            assert c.entry_date.map(pd.Timestamp).map(permission[gate]).fillna(False).all()
            err=float(abs((d.pnl-d.cost).sum()-c.realized_pnl.sum()));assert err<1e-12;audit[key+'_ledger']=err
            assert np.allclose(d.nav,(1+d.return_net).cumprod(),atol=1e-12)
            ds.append(d);cs.append(c);h=d.copy();h.return_net=.5*d.return_net+.5*CASH;h['candidate']=key+'_half';h.nav=(1+h.return_net).cumprod();ds.append(h)
            assigned=c[c.assignment_date.notna() & c.assignment_date.astype(str).str.strip().ne('')]
            longest=assigned.sort_values('recovery_days',ascending=False).iloc[0]
            ex.append(dict(candidate=key,assignments=len(assigned),IM_days=int(d.state.eq('future').sum()),max_recovery_days=float(longest.recovery_days),assignment_date=longest.assignment_date,exit_date=longest.exit_date))
        b=core[core.candidate==scope+'_original_roll_im_core_put102'].copy();assert np.array_equal(b.date.to_numpy(),ds[-1].date.to_numpy());ds.append(b)
    daily=pd.concat(ds,ignore_index=True);daily.to_csv(OUT/'daily.csv.gz',index=False);pd.concat(cs).to_csv(OUT/'cycles.csv',index=False);pd.DataFrame(ex).to_csv(OUT/'exposure.csv',index=False)
    rows=[];wide=[];unavailable={}
    for k,d in daily.groupby('candidate'):
        w=dict(candidate=k)
        for seg,y in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            cut=d.date.min() if y is None else d.date.max()-pd.DateOffset(years=y);ok=cut>=d.date.min();z=d[d.date>=cut] if ok else d.iloc[:0]
            met=metrics(z.return_net) if ok else {f:'N/A' for f in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            if not ok:unavailable.setdefault(k,{})[seg]='Genuine real IM/MO begins2022-07-22'
            rows.append(dict(candidate=k,segment=seg,start=str(cut.date()),end=str(d.date.max().date()),rows=len(z),**met))
            for f in ['ann_return','max_dd','sharpe_repo']:w[f+'_'+seg]=met[f]
        wide.append(w)
    s=pd.DataFrame(rows);s.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    m=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));m.update(scan_type='validated_full_ledger_artifact_normalization',baseline={'candidate':'real_val_le1_mom'},candidate_grid=sorted(daily.candidate.unique()),data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [SOURCE/'daily.csv',SOURCE/'cycles.csv',SOURCE/'entry_permissions.csv',SOURCE/'scan_meta.json',CORE/'daily.csv.gz',Path(__file__)]},cost_model={'reserve':.3,'cash_annual':.03,'short_one_way_notional':.0001,'half_allocation':'50% validated strategy daily return+50% cash daily return, daily allocation, not a rerun half-unit cycle'},unavailable_segments=unavailable,audit=audit,definition='Only remove shortPut entry MOM120>=0, retain effective valuation<=1 previous-close gate and all existing recovery/cost logic. Original core protection conditions unchanged.',limitations='Reuses certified full scan dated20260914, no new price model or engine. Model short uses ex-post10.33%carry and theoreticalBS; original core native model TRI+0.0003dailycarry, quarterly roll vs short monthly. No same-price model attribution. Genuine real has no bidask/margin liquidation. Research only.')
    (OUT/'scan_meta.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'record.md').write_text('# 卖95%Put去掉绝对动量入场条件\n\n## Data Snapshot\nCommon cutoff2026-08-14; real986/model2756days. Full previous scan validated path normalization.\n\n## Implementation\n'+m['definition']+'\n\n## Cost\n'+json.dumps(m['cost_model'],ensure_ascii=False)+'\n\n## Stability\n'+m['limitations']+'\n\n## Decision\nresearch_only_keep_momentum_filter_pending_user_decision\n\n## Audit\n'+json.dumps(audit)+'\n\n## Results\n'+s.to_string(index=False)+'\n\n## Exposure\n'+pd.DataFrame(ex).to_string(index=False),encoding='utf-8')
    (OUT/'command_log.txt').open('a',encoding='utf-8').write('\npython -X utf8 research_im_short95_remove_mom_v1.py\n')
    print(s[s.segment=='full'].to_string(index=False));print(pd.DataFrame(ex).to_string(index=False))
if __name__=='__main__':main()
