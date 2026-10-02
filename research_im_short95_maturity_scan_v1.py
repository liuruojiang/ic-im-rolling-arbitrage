from __future__ import annotations

import hashlib, inspect, json, math, subprocess
from pathlib import Path
import numpy as np
import pandas as pd

import im_mainline_v1_1 as policy
import im_mo_csi1000_put_protection_battery_v6 as mkt
import research_im_short_put_recovery_atm_full_model_v1 as model_source
import research_im_short_put_recovery_atm_real_v1 as original
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, metrics, prepare_options

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'quant_param_scan_runs/20260916_ic_im_rolling_arbitrage_im_short95_maturity_scan_v1_short_put_recovery_put_contract_month_ahead'
SPEC=ROOT/'docs/im_short95_maturity_scan_v1_spec.md'
MONTHS=(1,2,3)

def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*a): return subprocess.run(['git',*a],cwd=ROOT,text=True,capture_output=True,check=False).stdout.strip()

def allowed_series():
    state,_=policy.load_authoritative_local_state(); state=state.set_index('date')
    # Early reconstructed state can carry a default tier without a valid economic
    # score.  Treat it as unavailable, matching the established M+1 model gate.
    return (state.valuation_score.notna()&state.valuation_tier.le(1)&state.momentum_120.ge(0)).shift(1,fill_value=False)

def run_month(base,options,futures,months_ahead):
    allowed=allowed_series()
    source=inspect.getsource(original.run).replace('def run(base, options, futures, ratio):','def maturity_run(base, options, futures, ratio, months_ahead):')
    source=source.replace("if state == 'idle' and i > 0 and action == '':", "if state == 'idle' and i > 0 and action == '' and bool(allowed.get(day, False)):")
    source=source.replace("+pd.offsets.MonthBegin(1)", "+pd.offsets.MonthBegin(months_ahead)")
    ns=dict(vars(original)); ns['allowed']=allowed
    exec(compile(source,str(RUN/'maturity_state_machine.py'),'exec'),ns)
    d,e,c=ns['maturity_run'](base,options,futures,.95,months_ahead)
    assert c.entry_date.map(pd.Timestamp).map(allowed).fillna(False).all()
    assert abs((d.pnl-d.cost).sum()-c.realized_pnl.sum())<1e-12
    d['months_ahead']=months_ahead; e['months_ahead']=months_ahead; c['months_ahead']=months_ahead
    return d,e,c

def extended_model_inputs():
    market,base,_,futures,checks,basis=model_source.build_inputs()
    dates=pd.DatetimeIndex(market.date); expiries={x:mkt.third_friday(x,dates) for x in pd.date_range(market.date.min().to_period('M').to_timestamp(),market.date.max()+pd.DateOffset(months=4),freq='MS')}
    keys=set()
    for i,row in enumerate(market.itertuples(index=False)):
        if i==0: continue
        spot=market.iloc[i-1].spot_close; step=25 if spot<=2500 else 50 if spot<=5000 else 100 if spot<=10000 else 200
        for ahead in MONTHS: keys.add((row.date.to_period('M').to_timestamp()+pd.offsets.MonthBegin(ahead),math.floor(spot*.95/step+.5)*step))
    rows=[]
    for month,strike in keys:
        expiry=expiries[month]
        for row in market[(market.date>=month-pd.DateOffset(months=3))&(market.date<=expiry)].itertuples(index=False):
            t=max((expiry-row.date).days/365,0); op=mkt.proxy.bs_put(row.spot_open,strike,row.rate_open,row.dividend_open,row.sigma_open,t); close=mkt.proxy.bs_put(row.spot_close,strike,row.rate_close,row.dividend_close,row.sigma_close,t)
            rows.append({'date':row.date,'contract':'MO'+month.strftime('%y%m')+'-P-'+str(strike),'contract_month':month,'actual_expiry':expiry,'strike':strike,'open':op,'settle':close,'close':close,'volume':1,'open_interest':1})
    return market,base,pd.DataFrame(rows),futures,checks,basis

def summaries(daily):
    rows=[]; wide=[]
    for candidate,g in daily.groupby('candidate',sort=False):
        g=g.sort_values('date'); w={'candidate':candidate}
        for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            start=g.date.min() if years is None else g.date.max()-pd.DateOffset(years=years); ok=years is None or g.date.min()<=start; sub=g[g.date>=start] if ok else g.iloc[:0]
            z=metrics(sub.return_net) if ok else {k:'N/A' for k in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            rows.append({'candidate':candidate,'segment':segment,'start':str(start.date()),'end':str(g.date.max().date()),'rows':len(sub),**z})
            for k in z:w[k+'_'+segment]=z[k]
        wide.append(w)
    return pd.DataFrame(rows),pd.DataFrame(wide)

def main():
    meta=json.loads((RUN/'scan_meta.json').read_text(encoding='utf-8'))
    if meta.get('phase')!='init': raise RuntimeError('scan already started')
    rb=pd.read_csv(original.BASE,parse_dates=['date']); raw=pd.read_csv(original.OP,parse_dates=['date']); raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m'); ro=prepare_options(raw,actual_expiry_map(raw,rb)); rf=pd.read_csv(original.FU,parse_dates=['date'])
    market,mb,mo,mf,checks,basis=extended_model_inputs()
    all_d=[]; all_e=[]; all_c=[]; source_paths=[original.BASE,original.OP,original.FU,Path(__file__),SPEC]
    for layer,b,o,f in [('real',rb,ro,rf),('model',mb,mo,mf)]:
        for ahead in MONTHS:
            d,e,c=run_month(b,o,f,ahead); label=f'{layer}_m{ahead}'; d['candidate']=label; e['candidate']=label; c['candidate']=label; all_d.append(d);all_e.append(e);all_c.append(c)
    daily=pd.concat(all_d,ignore_index=True); summary,wide=summaries(daily)
    daily_dir=RUN/'daily_outputs'; daily_dir.mkdir(exist_ok=False); daily.to_csv(daily_dir/'daily.csv.gz',index=False,compression='gzip');pd.concat(all_e).to_csv(daily_dir/'events.csv',index=False);pd.concat(all_c).to_csv(daily_dir/'cycles.csv',index=False)
    summary.to_csv(RUN/'scan_summary.csv',index=False,encoding='utf-8-sig');wide.to_csv(RUN/'window_metrics.csv',index=False,encoding='utf-8-sig')
    unavailable={f'real_m{x}':{'last_10y':'Real IM/MO starts 2022-07-22.','last_5y':'Real IM/MO starts 2022-07-22.'} for x in MONTHS}
    meta.update(scan_type='three_month_maturity_scan',baseline={'candidate':'real_m1','definition':'next calendar month 95% Put'},candidate_grid=[{'months_ahead':x} for x in MONTHS],data_snapshot={'real_start':str(rb.date.min().date()),'real_end':str(rb.date.max().date()),'model_start':str(mb.date.min().date()),'model_end':str(mb.date.max().date()),'model_checks':checks,'basis_calibration':basis},cost_model={'one_way_notional':.0001,'reserve':.30,'cash_annual':.03},unavailable_segments=unavailable,outputs={**meta['outputs'],'daily':str(daily_dir/'daily.csv.gz'),'events':str(daily_dir/'events.csv'),'cycles':str(daily_dir/'cycles.csv')},decision='research_only_pending_interpretation',stability_label='unclassified_maturity_scan',source_hashes={str(p):sha(p) for p in source_paths},warnings=['Model results are theoretical and include ex-post basis calibration; do not combine with real results.','No bid-ask, capacity, dynamic margin, forced liquidation or integer contracts.'],git_status_after=git('status','--short'))
    (RUN/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    text='# IM卖95%Put期限扫描 v1\n\n## Run Metadata\n\n- 研究专用；真实与理论模型分层。\n\n## Research Question\n\n- 固定所有规则，仅比较M+1/M+2/M+3。\n\n## Implementation Anchor\n\n- 复用既有卖Put转IM回本状态机；每笔新入场均校验前日许可。\n\n## Data Snapshot\n\n- 真实2022-07-22至2026-08-14；模型2015-04-16至2026-08-14。\n\n## Cost and Execution Assumptions\n\n- 前收盘许可、次开盘成交、单边1bp、30%缓冲、现金3%。\n\n## Runtime Override Plan\n\n- 无生产或主线文件修改。\n\n## Commands\n\n```powershell\npython -X utf8 research_im_short95_maturity_scan_v1.py\n```\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`、`daily_outputs/`。\n\n## Full-Sample Results\n\n'+summary[summary.segment.eq('full')].to_markdown(index=False)+'\n\n## Window Results\n\n- 见CSV。\n\n## Stability Classification\n\n- `unclassified_maturity_scan`。\n\n## Decision\n\n- `research_only_pending_interpretation`。\n\n## User-Facing Summary\n\n- 已完成M+1/M+2/M+3的真实与模型扫描。\n'
    (RUN/'record.md').write_text(text,encoding='utf-8');(RUN/'command_log.txt').open('a',encoding='utf-8').write(f'cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n');print(summary[summary.segment.eq('full')].to_string(index=False))
if __name__=='__main__':main()
