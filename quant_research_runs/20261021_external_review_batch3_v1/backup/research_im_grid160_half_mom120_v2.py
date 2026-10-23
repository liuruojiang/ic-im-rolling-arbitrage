from pathlib import Path
import inspect,json,hashlib
import pandas as pd
import numpy as np
from im_put_maturity_valuation_tiers_v3 import metrics
import research_ic_im_grid_mom120_entry_v1 as parent
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_im_grid160_half_mom120_v2'

def main():
    spec=dict(candidates=['IM_original160_half','IM_original160_half_mom120','IM_current090_half'],entry=[1.6,1.6,.9],exit=[2.,2.,1.7],size=.5,gate='MOM120 strictly>0 only new buy; close signal next open; valuation exit unchanged',cutoff='2026-08-14',scope='fixed non-grid components, research not latest allrules')
    (OUT/'preregister.json').write_text(json.dumps(spec,indent=2),encoding='utf-8')
    source=inspect.getsource(parent.im_paths)
    source=source.replace("[(1.6,2.,False,1.,'old_parity'),(.9,1.7,False,.5,'valuation'),(.9,1.7,True,.5,'valuation_mom120')]","[(1.6,2.,False,1.,'old_parity'),(1.6,2.,False,.5,'original160_half'),(1.6,2.,True,.5,'original160_half_mom120'),(.9,1.7,False,.5,'current090_half')]")
    source=source.replace("t=pd.concat([tm,tr[tr.execution_date>='2022-07-22']],ignore_index=True)","t=pd.concat([tm[tm.execution_date<'2022-07-22'],tr[tr.execution_date>='2022-07-22']],ignore_index=True)")
    source=source.replace("ref=ref[ref.candidate=='0.90/1.70/0.5'].reset_index(drop=True)","ref=ref[ref.candidate==('1.60/2.00/0.5' if name=='original160_half' else '0.90/1.70/0.5')].reset_index(drop=True)")
    source=source.replace("AUDIT['IM_current_parameter_parity']=err","AUDIT[name+'_saved_half_parity']=err")
    (OUT/'executed_im_paths.py').write_text(source,encoding='utf-8')
    ns=dict(vars(parent));ns.update(OUT=OUT,AUDIT={},HASHES={})
    exec(compile(source,str(OUT/'executed_im_paths.py'),'exec'),ns)
    frames,events=ns['im_paths']();daily=pd.concat(frames,ignore_index=True);ev=pd.concat(events,ignore_index=True)
    daily.to_csv(OUT/'daily.csv.gz',index=False);ev.to_csv(OUT/'events.csv',index=False)
    assert pd.to_datetime(ev.execution_date).gt(pd.to_datetime(ev.signal_date)).all()
    for _,d in daily.groupby('candidate'):
        assert not d.date.duplicated().any() and len(d)==2756
        assert np.isfinite(d.return_net).all() and (d.return_net>-1).all()
    rows=[];wide=[];episodes=[]
    for name,d in daily.groupby('candidate'):
        w={'candidate':name};end=d.date.max()
        for seg,y in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1),('real_period',0),('model_period',-1)]:
            start=pd.Timestamp('2022-07-22') if y==0 else end-pd.DateOffset(years=y) if y and y>0 else d.date.min()
            z=d[d.date>=start];z=z[z.date<'2022-07-22'] if y==-1 else z
            m=metrics(z.return_net);active=z.grid_units.gt(0)
            rows.append(dict(candidate=name,segment=seg,start=str(z.date.min().date()),end=str(z.date.max().date()),rows=len(z),grid_days=int(active.sum()),grid_fraction=float(active.mean()),grid_fee_sum=float(z.grid_cost.sum()),average_cash=float(z.cash_weight.mean()),**m))
            for k,v in m.items():w[k+'_'+seg]=v
        wide.append(w)
        active=d.grid_units.gt(0);blocks=active.ne(active.shift(fill_value=False)).cumsum()
        for _,z in d[active].groupby(blocks):
            episodes.append(dict(candidate=name,start=str(z.date.min().date()),last_held=str(z.date.max().date()),trading_days=len(z),open_at_cutoff=z.date.max()==end,grid_period_total_return=float((1+z.return_net).prod()-1)))
    result=pd.DataFrame(rows);result.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False);pd.DataFrame(episodes).to_csv(OUT/'holding_episodes.csv',index=False)
    paired=[]
    for seg,g in result.groupby('segment'):
        a=g[g.candidate=='IM_original160_half'].iloc[0];b=g[g.candidate=='IM_original160_half_mom120'].iloc[0]
        paired.append(dict(segment=seg,return_change=b.ann_return-a.ann_return,vol_change=b.ann_vol-a.ann_vol,drawdown_change=b.max_dd-a.max_dd,grid_days_change=b.grid_days-a.grid_days))
    pd.DataFrame(paired).to_csv(OUT/'paired_comparison.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));hashes=ns['HASHES'];hashes[str(Path(__file__).relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();hashes[str(Path(parent.__file__).relative_to(ROOT))]=hashlib.sha256(Path(parent.__file__).read_bytes()).hexdigest()
    meta.update(data_snapshot=hashes,audit=ns['AUDIT'],baseline={'candidate':'IM_original160_half'},cost_model={'one_way_notional':.0001,'grid_size':.5,'buffer':.3,'cash_annual':.03,'nongrid':'original gross/Put/Call/costs fixed','model_grid_carry_daily':.00038985993765572324},limitations='Frozen historical non-grid execution and Put/Call version, not latest allrules. Model IM synthetic futures and ex-post average basis; real July22 onward actual quotes. No bidask, margin calls, liquidation or integer contracts. Entry filter only, no production changes.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    record='''# IM原1.6/2.0半仓网格加入正MOM120

## Data Snapshot
固定历史2015-04-16至2026-08-14共2756交易日，模型段2015-04-16至2022-07-21，真实段2022-07-22至2026-08-14。Full/10Y/5Y/3Y/1Y为原机制完整连续历史；模型与真实独立段另列，不把全期当全部真实数据。
## Implementation Anchor
两主候选1.6买/2.0卖、0.5倍网格，唯一差异为新买入是否要求中证1000全收益MOM120>0；收盘评估下一交易日开盘执行，已有仓不因动量转负退出，原估值上界退出。第三条当前0.9/1.7半仓仅参考。非网格底仓、动量、原Put/Call路径及费用保持固定，网格不加保险。
复用research_ic_im_grid_mom120_entry_v1.im_paths及原simulate_overlay；隔离harness保存于executed_im_paths.py。正动量在历史carry及当期买入均生效；来源市场和历史评分同样门控。修正父输出事件的模型/真实跨段重复，仅保存实际采用层的事件，不改变收益路径。
## Cost and Execution
gross及网格单边1bp/展期费用先按0.5缩放再进入原乘法净收益公式；每倍期货30%缓冲，现金3%。模型网格延续事后平均日贴水0.00038985993765572324，仅为情景非可交易OOS。无盘口、整数手数、追保/强平重放。固定老版本非网格组件不能冒称当前最新全部规则完整回测。
## Verification
原1.6/2.0一倍旧组合逐日复现及1.6/2.0半仓、0.9/1.7半仓父结果复现均<1e-12。所有真实采用的买入信号严格正动量，执行日严格晚于信号日，各候选日期一一对应，有限净收益、现金非负。详见scan_meta.audit。
## Stability
固定三条预设路径，尚无独立OOS；真实/模型、近窗和网格暴露改变见CSV，不按收益单指标晋级。
## Decision
research_only_no_promotion
## Commands
python -X utf8 research_im_grid160_half_mom120_v2.py
## Output Files
daily.csv.gz / events.csv / holding_episodes.csv / paired_comparison.csv / scan_summary.csv / window_metrics.csv / scan_meta.json。
## Results
'''+result.to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_im_grid160_half_mom120_v2.py\n')
    print(result.to_string(index=False));print(pd.DataFrame(episodes).to_string(index=False))
if __name__=='__main__':main()
