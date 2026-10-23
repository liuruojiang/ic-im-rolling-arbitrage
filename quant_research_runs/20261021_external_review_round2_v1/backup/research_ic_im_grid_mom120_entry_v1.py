from pathlib import Path
import json,hashlib,inspect
import pandas as pd
import numpy as np
import ic_valuation_overlay_put_sync_v1 as ice
import im_fixed_valuation_overlay_entry_exit_scan_v15 as ime
import analyze_no_grid_annual_20260912 as source
from im_put_maturity_valuation_tiers_v3 import metrics
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'quant_param_scan_runs/20260914_ic_im_grid_mom120_entry_v1'
ICBASE=source.IC_FORMAL;ICSCORE=ice.SCORE_FILE
IMSCORE=ROOT/'quant_param_scan_runs/20260913_im_reconstruct_exit_cliff/reconstructed_valuation_panel.csv.gz'
AUDIT={};HASHES={}

# Existing helper's empty-trade frame has object dates; normalize only its diagnostic dates.
ims=inspect.getsource(ime.simulate_overlay)
ims=ims.replace('trade_frame = pd.DataFrame(trades)', 'trade_frame = pd.DataFrame(trades)')
ims=ims.replace('trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"].dt.year', 'pd.to_datetime(trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"]).dt.year')
imns=dict(vars(ime));exec(compile(ims,'im_empty_trade_diagnostic_fix','exec'),imns)
imsim=imns['simulate_overlay']

def read(p,**kwargs):
    HASHES[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return pd.read_csv(p,parse_dates=['date'],**kwargs)

def block(frame,mom,low,high,on):
    f=frame.copy();f['mom120']=f.date.map(mom)
    # Mid-band substitution only suppresses flat-state buy; original high exits stay unchanged.
    if on:f.loc[f.unbounded_median_knot.le(low+1e-12)&~f.mom120.gt(0),'unbounded_median_knot']=(low+high)/2
    return f

def ic_paths():
    b=read(ICBASE);scores=read(ICSCORE)
    raw=read(ice.IC_RAW,usecols=['date','contract','open','settle','pre_settle','volume']).rename(columns={'volume':'raw_volume'})
    chain=b[['date','contract','roll_event']].merge(raw,on=['date','contract'],validate='one_to_one').merge(scores[['date','unbounded_median_knot']],on='date',validate='one_to_one')
    tri=read(ROOT/'data/ic_im_valuation_risk_premium_forecast_v3/csindex_H00905.csv').set_index('date').close
    mom=tri.pct_change(120,fill_method=None)
    old=read(source.IC_TARGET,usecols=['date','grid_held_eod']);b=b.merge(old,on='date',validate='one_to_one')
    results=[];events=[]
    for low,high,on,size,name in [(.375,1.,False,1.,'old_parity'),(.5,1.,False,.5,'valuation'),(.5,1.,True,.5,'valuation_mom120')]:
        g,t,a=ice.simulate_overlay(block(chain,mom,low,high,on),low,high)
        held=chain.roll_event & g.overlay_held_before.eq(1)&g.overlay_held_eod.eq(1)
        g.loc[held,'overlay_gross_ret']=b.loc[held,'futures_gross_ret'].to_numpy()
        gross=size*g.overlay_gross_ret;cost=size*g.overlay_cost_rate
        net=(1+gross)*(1-cost)-1
        # Historical IC grid additive contribution was linearly scaled in the grid-half study.
        net=size*((1+g.overlay_gross_ret)*(1-g.overlay_cost_rate)-1)
        cash=b.cash_weight+.3*(b.grid_held_eod-size*g.overlay_held_eod)
        ret=b.ret-b.grid_net_increment+net+(cash-b.cash_weight)*ice.CASH_DAILY
        if name=='old_parity':
            err=float(abs(ret-b.ret).max());assert err<1e-12;assert np.array_equal(g.overlay_held_eod,b.grid_held_eod);AUDIT['IC_old_baseline_parity']=err;continue
        if on and len(t):assert t.loc[t.action=='buy','signal_date'].map(mom).gt(0).all()
        d=pd.DataFrame(dict(date=b.date,candidate='IC_'+name,return_net=ret,grid_units=size*g.overlay_held_eod,cash_weight=cash,grid_cost=cost))
        assert cash.ge(0).all();results.append(d);t['candidate']='IC_'+name;events.append(t)
        if not on:
            ref=read(ROOT/'quant_param_scan_runs/20260913_ic_balanced_half_grid/daily_candidates.csv.gz')
            ref=ref[ref.candidate=='L0.500_H1.000'].reset_index(drop=True)
            assert len(ref)==len(d);err=float(abs(d.return_net-ref.ret).max());assert err<1e-12;AUDIT['IC_current_parameter_parity']=err
    return results,events

def im_paths():
    source.REFRESH=ROOT/'outputs/nav_r7_complete_refresh_20260912'
    source.IM_FULL=source.REFRESH/'im_full_daily.csv.gz';source.IM_TAIL=source.REFRESH/'im_tail_daily.csv';source.HISTORICAL_SIGNALS=source.REFRESH/'historical_signals.json'
    b,a=source.prepare_im();b=b[b.date<='2026-08-14'].reset_index(drop=True);AUDIT['IM_source']=a
    old,_,percentile=ime.load_sources();scores=read(IMSCORE)[['date','unbounded_median_knot']]
    model,ma=ime.build_model_market(old,scores,percentile);real,ra=ime.build_real_market(old,scores,percentile)
    tri=read(ROOT/'data/ic_im_valuation_risk_premium_forecast_v3/csindex_H00852.csv').set_index('date').close;mom=tri.pct_change(120,fill_method=None)
    ng=b.futures_gross_ret-b.grid_gross_component;nc=b.futures_cost_rate-b.overlay_cost_rate;cash0=b.cash_weight+.3*b.grid_units
    results=[];events=[];basis=.00038985993765572324
    for low,high,on,size,name in [(1.6,2.,False,1.,'old_parity'),(.9,1.7,False,.5,'valuation'),(.9,1.7,True,.5,'valuation_mom120')]:
        m=block(model,mom,low,high,on);r=block(real,mom,low,high,on);h=block(scores,mom,low,high,on)
        gm,tm,am=imsim(m,h,'unbounded_median_knot',low,high,name,'fixed','model')
        gr,tr,ar=imsim(r,h,'unbounded_median_knot',low,high,name,'fixed','real')
        gm['basis']=(1+gm.overlay_gross_ret)*basis*gm.overlay_held_before;gr['basis']=0.
        g=pd.concat([gm[gm.date<'2022-07-22'],gr]).sort_values('date').reset_index(drop=True)
        assert b.date.equals(g.date)
        cash=cash0-.3*size*g.overlay_held_eod;cost=nc+size*g.overlay_cost_rate
        ret=(1+ng+size*(g.overlay_gross_ret+g.basis)+b.put_pnl_ret+b.call_pnl_ret)*(1-cost)*(1-b.put_cost_rate)*(1-b.call_cost_rate)-1+cash*source.CASH_DAILY
        if name=='old_parity':
            err=float(abs(ret-b.ret).max());assert err<1e-12;AUDIT['IM_old_baseline_parity']=err;continue
        t=pd.concat([tm,tr[tr.execution_date>='2022-07-22']],ignore_index=True)
        if on and len(t):assert t.loc[t.action=='buy','signal_date'].map(mom).gt(0).all()
        d=pd.DataFrame(dict(date=b.date,candidate='IM_'+name,return_net=ret,grid_units=size*g.overlay_held_eod,cash_weight=cash,grid_cost=size*g.overlay_cost_rate));assert cash.ge(0).all();results.append(d);t['candidate']='IM_'+name;events.append(t)
        if not on:
            ref=read(ROOT/'quant_param_scan_runs/20260913_im_balanced_half_grid/daily_candidates.csv.gz');ref=ref[ref.candidate=='0.90/1.70/0.5'].reset_index(drop=True)
            assert len(ref)==len(d);err=float(abs(d.return_net-ref.ret_variant).max());assert err<1e-12;AUDIT['IM_current_parameter_parity']=err
    return results,events

def main():
    (OUT/'preregister.json').write_text(json.dumps(dict(IC=[.5,1.,.5],IM=[.9,1.7,.5],gate='strict MOM120>0 on new buy only, T close to next open; missing fail closed',scope='fixed non-grid components, current thresholds retrospectively applied; not latest allrules',cutoff='2026-08-14'),indent=2),encoding='utf-8')
    a,e=ic_paths();b,f=im_paths();daily=pd.concat(a+b);events=pd.concat(e+f);daily.to_csv(OUT/'daily.csv.gz',index=False);events.to_csv(OUT/'events.csv',index=False)
    rows=[];wide=[];duration=[]
    for name,d in daily.groupby('candidate'):
        w={'candidate':name};realstart=pd.Timestamp('2022-09-19' if name.startswith('IC') else '2022-07-22');end=d.date.max()
        for seg,y in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1),('real_period',0),('model_period',-1)]:
            start=realstart if y==0 else end-pd.DateOffset(years=y) if y and y>0 else d.date.min()
            z=d[d.date>=start];z=z[z.date<realstart] if y==-1 else z
            m=metrics(z.return_net);held=z.grid_units.gt(0)
            rows.append(dict(candidate=name,segment=seg,start=str(z.date.min().date()),end=str(z.date.max().date()),rows=len(z),grid_days=int(held.sum()),grid_fraction=float(held.mean()),grid_fee_sum=float(z.grid_cost.sum()),**m))
            for k,v in m.items():w[k+'_'+seg]=v
        wide.append(w)
    s=pd.DataFrame(rows);s.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(audit=AUDIT,data_snapshot=HASHES,baseline={'candidate':'IC_valuation'},cost_model={'original_fees':'retained; grid one-way1bp scaled0.5','buffer':.3,'cash_annual':.03},limitations='Fixed older non-grid Put/Call/futures paths; current thresholds and0.5 grid applied historically for comparative research. Prelisting IM modeled with ex-post mean carry. IC early Put proxy but IC futures actual. No current allrules claim. No production change.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    record='''# 网格买入增加正MOM120条件
## Data Snapshot
截止2026-08-14，完整历史2015-04-16起；real_period IC从2022-09-19、IM从2022-07-22，model_period为之前历史。IC早期仅期权为模型，期货真实；IM上市前期货也为模型。
## Implementation Anchor
当前估值网格IC0.5/1.0，IM0.9/1.7，新增各0.5倍。仅新买入要求MOM120>0，已持不因负动量退出，仍按估值上界退出。完整滚动状态含初始carry按相同新门控重放。估值、非网格动量、Put/Call均保留固定组件，不额外给网格加保险。
## Cost and Execution
T收盘判断次日开盘执行；原季度链及roll成本保留，单边名义1bp、0.5倍缩放、每倍期货30%缓冲及现金3%。IC沿原已验收网格半仓研究线性缩放净增量；IM先缩放gross/cost再进入乘法净收益公式。无盘口、整数手数、追保或强平。
## Verification
旧估值网格和原组合复现、当前阈值半仓父结果复现、实际买入正动量及日期对齐校验通过，误差<1e-12。仅门控新买入改变，所有退出边界保留。缺失动量不买。源哈希见scan_meta。
## Stability
固定两组当前阈值，非调参；无独立OOS。历史期权等组件并非最新全部规则，结果为固定路径机制比较，不能冒称正式当前完整绩效。
## Decision
research_only_no_promotion
## Commands
python -X utf8 research_ic_im_grid_mom120_entry_v1.py
## Results
'''+s.to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_ic_im_grid_mom120_entry_v1.py\n')
    print(s[s.segment.isin(['real_period','model_period'])].to_string(index=False))
if __name__=='__main__':main()
