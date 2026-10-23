from pathlib import Path
import sys,json,hashlib
import pandas as pd
import numpy as np
from im_put_maturity_valuation_tiers_v3 import metrics
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'quant_param_scan_runs/20260908_im_mom120_put102_combined_v1'
OUT=ROOT/'quant_param_scan_runs/20260914_im_original_core_put_vs_short95_v2'
SHORT=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_le1_mom_then_put102_v3_protection_enabled'
sys.path.insert(0,str(SOURCE));import run_combined as original
END=pd.Timestamp('2026-08-14');CASH=1.03**(1/252)-1
HASHES={}
def read(p,dates=('date',)):
    HASHES[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return pd.read_csv(p,parse_dates=list(dates),low_memory=False)
def main():
    assert json.loads((SOURCE/'verification.json').read_text())['passed']
    engine=original.engine;market=read(original.BASE/'model_market.csv.gz');up=read(original.BASE/'real_upstream.csv.gz');active=read(original.BASE/'real_active.csv.gz')
    raw=read(original.BASE/'real_options.csv.gz',('date','contract_month','rule_expiry','actual_expiry'));options=engine.with_execution_prices(raw)
    ds=[];audit={};trades=[]
    for scope in ['model','real']:
        b=read(SOURCE/(scope+'_fixed_base.csv.gz'));saved=read(SOURCE/(scope+'_combined_core_put.csv.gz'));s=read(SOURCE/(scope+'_combined_core_schedule.csv.gz'),('eval_date','execution_date'))
        if scope=='model':p,t,_=engine.run_model_monthly_close(market,s,'3m',1.02,'original_core',reset_dates=engine.monthly_dates(b.date));norm=.5/4
        else:p,t,_=engine.run_real_monthly_close(up,options,active,s,'3m',1.02,'original_core',reset_dates=engine.monthly_dates(b.date));norm=.25/4
        p[original.first.FIELDS]*=norm
        assert p.date.equals(saved.date)
        errors={f:float((p[f]-saved[f]).abs().max()) for f in original.first.FIELDS};assert max(errors.values())<1e-12
        assert p.put_contract.fillna('').equals(saved.put_contract.fillna(''));audit[scope+'_original_core_parity']=errors
        # Original fixed core has 0.5 capital weight. Scale BOTH original core futures
        # and its unchanged Put transactions by 2 for 1x core; never select/reprice a new policy.
        pure=b.copy();pure['base_futures_gross']=b.base_futures_gross/b.base_units
        pure['base_futures_gross']*=.5
        pure['base_futures_cost']=.0001*b.roll_event.astype(float);pure.loc[pure.index[0],'base_futures_cost']=.00005
        pure['base_units']=.5;pure['momentum_weight']=0.
        pp=p.copy()
        d=original.first.compose(pure,pp);d['return_net']=d.ret;d['candidate']=scope+'_original_roll_im_core_put102';d=d[d.date<=END].copy();ds.append(d)
        zero=pp.copy();zero[original.first.FIELDS]=0
        bare=original.first.compose(pure,zero);bare['return_net']=bare.ret;bare['candidate']=scope+'_original_bare_roll_im';ds.append(bare[bare.date<=END].copy())
        sh=read(SHORT/(scope+'_unprotected_daily.csv'));sh['candidate']=scope+'_short95_le1_mom';assert sh.date.equals(d.date);ds.append(sh)
        half=sh.copy();half['return_net']=.5*sh.return_net+.5*CASH;half['candidate']=scope+'_short95_half_allocation';ds.append(half)
        # Reconstruct original full base from its 0.5 fixed and 0.5 momentum units.
        assert np.allclose(pure.base_futures_gross*2*b.base_units,b.base_futures_gross,atol=1e-12)
        audit[scope+'_one_x_min_cash']=float((.7-2*p.put_mark_fraction).min())
        t['scope']=scope;t['original_core_capital_scale']=norm;t['comparison_scale']=1;trades.append(t)
        p.to_csv(OUT/(scope+'_original_core_reproduced.csv.gz'),index=False)
    daily=pd.concat(ds,ignore_index=True);daily.to_csv(OUT/'daily.csv.gz',index=False);pd.concat(trades).to_csv(OUT/'original_core_trades.csv.gz',index=False)
    rows=[];wide=[];unavailable={}
    for k,d in daily.groupby('candidate'):
        w=dict(candidate=k)
        for seg,y in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            cutoff=d.date.min() if y is None else END-pd.DateOffset(years=y);ok=cutoff>=d.date.min();z=d[d.date>=cutoff] if ok else d.iloc[:0]
            met=metrics(z.return_net) if ok else {f:'N/A' for f in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            if not ok:unavailable.setdefault(k,{})[seg]='Genuine IM/MO history starts2022-07-22'
            rows.append(dict(candidate=k,segment=seg,start=str(cutoff.date()),end=str(END.date()),rows=len(z),**met))
            for f in ['ann_return','max_dd','sharpe_repo']:w[f+'_'+seg]=met[f]
        wide.append(w)
    ss=pd.DataFrame(rows);ss.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(scan_type='original_engine_replay_parity_comparison',baseline={'candidate':'real_short95_le1_mom'},candidate_grid=sorted(daily.candidate.unique()),audit=audit,data_snapshot=HASHES,cost_model={'reserve':.3,'cash_annual':.03,'futures_one_way':.0001,'Put':'original exact cost factor and premium ledger, both core futures/Put scaled from0.5 to1x'},unavailable_segments=unavailable,limitations='Original corrected102% research engine reused, original saved core matches all fields/contracts within1e-12. Original roll quarterly, short95 monthly. Original model futures TRI+0.0003daily carry, short95 calibrated10.33% annual carry: distinct native model assumptions, not matched-price attribution. Real zero-volume protection official settlement estimates. Theoretical model not historical executable. No forced liquidation. No frozen/live files changed.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'record.md').write_text('# 原版滚IM核心Put与卖95%Put比较 V2\n\n## Data Snapshot\n2015-04-16..2026-08-14 model;2022-07-22..2026-08-14 real. Original102%core replay only; valuation/MOM120 floor3 retained; no grid, momentum futures, independent momentumPut orCall.\n\n## Implementation\n原引擎重新计算全核心Put，逐字段与合约parity通过后才将原0.5核心期货和Put同比放大2至1x；未改变Put数量条件、选约或执行。保留原版季度滚动和原成本公式。统一指标含首日收益，原研究报告丢弃首日，因此可能小幅不同。\n\n## Cost\n'+json.dumps(meta['cost_model'],ensure_ascii=False)+'\n\n## Stability\n'+meta['limitations']+'\n\n## Decision\nresearch_only_original_policy_restored_no_promotion\n\n## Audit\n'+json.dumps(audit,indent=2)+'\n\n## Results\n'+ss.to_string(index=False),encoding='utf-8')
    (OUT/'command_log.txt').open('a',encoding='utf-8').write('\npython -X utf8 research_im_original_core_put_vs_short95_v2.py\n')
    print(ss.to_string(index=False),flush=True)
if __name__=='__main__':main()
