from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd

OUT=Path('outputs/ic_im_valuation_duration_20260914_v1')
IM=Path('quant_param_scan_runs/20260914_icim_im_short95_entry_valuation_0123_v2_entry_valuation_tier/entry_valuation_state.csv')
IC=Path('quant_param_scan_runs/20260914_ic_im_ic_short_95_put_to_ic_recovery_ic_short_put_mom120_positive_entry_valuation_permission_tier_expansion/entry_permissions.csv')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    im=pd.read_csv(IM,parse_dates=['date']);ic=pd.read_csv(IC,parse_dates=['date'])
    im=im[['date','entry_valuation_tier','momentum_120','entry_valuation_source']].rename(columns={'entry_valuation_tier':'tier','momentum_120':'mom120','entry_valuation_source':'source'})
    ic['tier']=np.searchsorted([1.90,1.95,2.,2.05],ic.unbounded_median_knot,side='right').astype(float)
    ic.loc[ic.unbounded_median_knot.isna(),'tier']=np.nan;ic['source']='fixed_unbounded_score'
    ic=ic[['date','tier','mom120','source']]
    rows=[];daily=[];joint=[]
    for instrument,x in [('IM',im),('IC',ic)]:
        x=x[x.date.between('2015-04-16','2026-08-14')].copy();assert not x.date.duplicated().any()
        for area,start in [('model','2015-04-16'),('real','2022-07-22' if instrument=='IM' else '2022-09-19'),('common_real','2022-09-19')]:
            g=x[x.date>=start].copy();n=len(g)
            for tier in [0,1,2,3,4,'missing']:
                count=int(g.tier.isna().sum()) if tier=='missing' else int((g.tier==tier).sum())
                rows.append(dict(instrument=instrument,area=area,category='valuation',state=str(tier),days=count,fraction=count/n,denominator=n,start=str(g.date.min().date()),end=str(g.date.max().date())))
            for name,mask in [('positive',g.mom120>0),('zero',g.mom120==0),('negative',g.mom120<0),('missing',g.mom120.isna())]:
                rows.append(dict(instrument=instrument,area=area,category='mom120',state=name,days=int(mask.sum()),fraction=float(mask.mean()),denominator=n,start=str(g.date.min().date()),end=str(g.date.max().date())))
            for tier in range(5):
                z=g[g.tier==tier];joint.append(dict(instrument=instrument,area=area,tier=tier,tier_days=len(z),mom_positive_days=int((z.mom120>0).sum()),positive_fraction_within_tier=float((z.mom120>0).mean()) if len(z) else None))
            g['instrument']=instrument;g['area']=area;daily.append(g)
        assert len(x)==2756
    r=pd.DataFrame(rows);r.to_csv(OUT/'duration.csv',index=False);pd.DataFrame(joint).to_csv(OUT/'joint_by_tier.csv',index=False);pd.concat(daily).to_csv(OUT/'daily_states.csv.gz',index=False)
    for _,g in r.groupby(['instrument','area','category']):assert g.days.sum()==g.denominator.iloc[0]
    (OUT/'source_hashes.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [IM,IC,Path(__file__)]},indent=2),encoding='utf-8')
    text='''# IC/IM估值档与MOM120历史时间占比

统计对象为此前卖Put测试快照，截止2026-08-14，不是当日最新信号。按收盘状态统计交易日，而不是移位后的交易许可、自然日、成交量或资金仓位。真实期IM2022-07-22起，IC2022-09-19起；共同真实期另列。模拟期均2015-04-16起，共2756日，指模型回测覆盖期的历史状态分布，不代表上市前真实期权存在。
IC四条边界1.90/1.95/2.00/2.05，等于边界进入较高档，0—4五档；当前IC卖Put低估值许可为<1.95。IM沿用entry_valuation_tier，包含既有relative/absolute合成口径及早期certified重建，不重新定义估值档。无评分/动量缺失独立列，分母始终为完整窗口交易日，不删缺失日。
MOM120为各自全收益指数120交易日收益率，正为严格>0，零/负/缺失单列。IC该条件已取消，本次仅描述历史分布，不加入策略。IM早期来源标记保留于daily_states。
比例计数总和与交易日分母一致校验通过；不重新跑策略、不改变交易或生产逻辑。来源及哈希见source_hashes.json。完整比例见duration.csv；各档内正动量比例见joint_by_tier.csv。

'''+r.to_string(index=False)
    (OUT/'record.md').write_text(text,encoding='utf-8')
    print(r[r.area.isin(['real','model'])].to_string(index=False))
if __name__=='__main__':main()
