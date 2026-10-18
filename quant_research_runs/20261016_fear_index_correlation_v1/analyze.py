"""Read-only correlation study of frozen source snapshots; no strategy changes."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def corr(x,y):
    z=pd.DataFrame({'x':x,'y':y}).dropna();a=z.x.to_numpy(float);b=z.y.to_numpy(float)
    a=a-a.mean();b=b-b.mean();v=float(a@b/np.sqrt((a@a)*(b@b)))
    assert abs(v-z.x.corr(z.y))<1e-12
    return v,len(z)
def table(h,r):return '\n'.join(['| '+' | '.join(h)+' |','| '+' | '.join(['---']*len(h))+' |']+['| '+' | '.join(map(str,x))+' |' for x in r])
def main():
    if (HERE/'record.md').exists():raise FileExistsError('First result cannot be overwritten')
    jp=ROOT/'quant_research_runs/20261016_jiu_fear_grid_threshold_v1/inputs/jiu_fear_greed.csv'
    bp=ROOT/'quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv'
    j=pd.read_csv(jp).rename(columns={'fear_greed_index':'jiu'});b=pd.read_csv(bp).rename(columns={'fear_greed_index':'bfw'})
    assert j.date.is_unique and b.date.is_unique
    lo=max(j.date.min(),b.date.min());hi=min(j.date.max(),b.date.max())
    # Preserve all observed source-calendar dates. A missing observation does not
    # turn two sessions into a one-session change or compress a rolling window.
    f=j.merge(b,on='date',how='outer').sort_values('date').loc[lambda z:z.date.between(lo,hi)].reset_index(drop=True)
    d=f.dropna(subset=['jiu','bfw']).copy()
    assert d[['jiu','bfw']].apply(lambda x:np.isfinite(x).all()).all()
    pearson,n=corr(d.jiu,d.bfw);spearman,nr=corr(d.jiu.rank(),d.bfw.rank())
    assert abs(spearman-d.jiu.corr(d.bfw,method='spearman'))<1e-12
    f['jiu_change']=f.jiu.diff();f['bfw_change']=f.bfw.diff()
    change,nchange=corr(f.jiu_change,f.bfw_change)
    f['rolling60']=f.jiu.rolling(60,min_periods=60).corr(f.bfw)
    roll=f.rolling60.dropna()
    yearly=[]
    for year,z in d.groupby(d.date.str[:4]):
        r,nn=corr(z.jiu,z.bfw);s,_=corr(z.jiu.rank(),z.bfw.rank());yearly.append(dict(year=year,n=nn,pearson=r,spearman=s))
    jq=d.jiu.le(5);bq=d.bfw.le(25);both=int((jq&bq).sum());either=int((jq|bq).sum())
    tails={'jiu_le5_days':int(jq.sum()),'bfw_le25_days':int(bq.sum()),'both_days':both,'either_days':either,
        'bfw_low_given_jiu_low':both/int(jq.sum()),'jiu_low_given_bfw_low':both/int(bq.sum()),'jaccard':both/either,
        'jiu_low_frequency':float(jq.mean()),'bfw_low_frequency':float(bq.mean())}
    windows=[]
    for name,start,end in [('IC_ACCOUNT','2022-09-19','2026-08-13'),('IM_ACCOUNT','2022-07-22','2026-08-14')]:
        z=d.loc[d.date.between(start,end)];r,nn=corr(z.jiu,z.bfw)
        windows.append(dict(window=name,start=start,end=end,matched_rows=nn,pearson=r))
    result={'start':lo,'end':hi,'source_calendar_rows':len(f),'matched_rows':n,'pearson':pearson,'spearman':spearman,
        'daily_point_change_pearson':change,'daily_point_change_rows':nchange,
        'rolling60':{'valid_windows':len(roll),'min':float(roll.min()),'median':float(roll.median()),'max':float(roll.max())},
        'low_signal_overlap':tails,'source_std':{'jiu':float(d.jiu.std()),'bfw':float(d.bfw.std())},
        'missing_bfw_dates':f.loc[f.bfw.isna(),'date'].tolist(),'missing_jiu_dates':f.loc[f.jiu.isna(),'date'].tolist(),
        'yearly':yearly,'account_windows':windows,
        'scope':'Frozen downloaded histories at different retrieval dates; descriptive association, not release-time or point-in-time certification'}
    d.to_csv(HERE/'matched_levels.csv',index=False);f.to_csv(HERE/'aligned_calendar_and_changes.csv',index=False)
    pd.DataFrame(yearly).to_csv(HERE/'yearly_correlations.csv',index=False)
    (HERE/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    fig,axes=plt.subplots(1,2,figsize=(12,4.4),constrained_layout=True)
    ax=axes[0];colors=np.where(d.date.str.startswith('2023'),'#d77c34','#3c80a6')
    ax.scatter(d.jiu,d.bfw,c=colors,s=8,alpha=.32,edgecolors='none');ax.set(xlabel='Jiu fear / greed',ylabel='Baifenwei fear / greed',xlim=(0,100),ylim=(0,100),title=f'Same-date levels: r = {pearson:.3f}')
    ax.text(.03,.97,'Orange: 2023; blue: other years',transform=ax.transAxes,va='top',fontsize=9);ax.grid(alpha=.18)
    ax=axes[1];ax.plot(pd.to_datetime(f.date),f.rolling60,color='#3c80a6',lw=1.1);ax.axhline(0,color='#777',lw=.7);ax.axhline(pearson,color='#d77c34',ls='--',lw=.9,label='Full-sample correlation')
    ax.set(ylim=(-1,1),ylabel='Pearson correlation',title='60-session rolling correlation');ax.legend(fontsize=8);ax.grid(alpha=.18)
    fig.savefig(HERE/'correlation.png',dpi=170);plt.close(fig)
    yearly_rows=[[x['year']+('（部分年份）' if x['year'] in ('2020','2026') else ''),x['n'],f'{x["pearson"]:.3f}'] for x in yearly]
    lines=['# 韭圈与百分位恐慌贪婪指数相关性','',
        f'共同日期 {lo} 至 {hi}，{n} 组有效日度水平观测。Pearson {pearson:.4f}、Spearman {spearman:.4f}，每日点数变化相关 {change:.4f}（{nchange}组）。整体为中等正相关，年度和低位信号的一致性差别较大。','',
        '## 指数水平与每日变化','',
        table(['项目','相关系数','样本数'],[['水平 Pearson',f'{pearson:.4f}',n],['排序 Spearman',f'{spearman:.4f}',nr],['每日点数变化 Pearson',f'{change:.4f}',nchange]]),'',
        '每日变化是指数点数的一阶差分，不是股票收益率或百分比变化。按两份序列日期并集对齐后再差分；缺失日及其后一日的无法配对变化不计入，不用删除缺日后的跨日变化冒充一日变化。','',
        '## 分年度','',table(['年份','配对天数','Pearson'],yearly_rows),'',
        '2023年水平和排序相关均接近零，不能仅凭全样本相关假设两者始终同步。现有版本与首次发布证据不足，不能单凭这一差异判定某一来源错误或公式发生更改。','',
        f'60个来源交易日滚动相关的中位数为{roll.median():.3f}，范围{roll.min():.3f}至{roll.max():.3f}。包含缺失值的窗口不压缩，要求60个完整配对值才计算。','',
        '## 当前研究阈值的低位重合','',
        table(['条件','共同样本中的天数'],[['韭圈≤5',int(jq.sum())],['百分位≤25',int(bq.sum())],['同时满足 AND',both],['任一满足 OR',either]]),'',
        f'韭圈≤5的日期中，{tails["bfw_low_given_jiu_low"]:.1%}也满足百分位≤25；百分位≤25的日期中，{tails["jiu_low_given_bfw_low"]:.1%}也满足韭圈≤5。这是指数低位天数，不是实际网格开仓次数；估值触发、持仓状态和退出规则会继续改变开仓次数。','',
        f'两阈值在本样本的出现频率分别为{jq.mean():.2%}和{bq.mean():.2%}，频率接近，发生日期重合有限。不能直接互换原始分数或把一个指数缺失时用另一个数值补齐。','',
        '## 与账户回测窗口对应','',table(['窗口','配对天数','Pearson'],[[x['window'],x['matched_rows'],f'{x["pearson"]:.4f}'] for x in windows]),'',
        '## 图','',f'![相关性散点与滚动相关](sandbox:/{(HERE/"correlation.png").as_posix()})','',
        '## 数据边界','',
        '韭圈使用今早保存的1502行历史，百分位使用此前保存的8016行CSV，比较只截至较早序列终点2026-09-24。百分位2026-07-27缺失，水平计算删除该配对，差分及滚动保留日历缺口；不前填、不把序列错开日期配对。两份快照下载时间不同，缺历史首次发布和修订认证。本次仅做描述性统计，未修改策略、参数、账本、日报或任何原数据。','',
        '输入 SHA-256、结果JSON、逐日配对和分年表见本目录；Pearson通过独立中心化点积与pandas交叉核验，Spearman通过平均秩相关与库函数交叉核验。','']
    (HERE/'record.md').write_text('\n'.join(lines),encoding='utf-8')
    (HERE/'input_hashes.json').write_text(json.dumps({str(p.relative_to(ROOT)):sha(p) for p in (jp,bp)},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=[p for p in HERE.iterdir() if p.is_file() and p.name!='manifest.json']
    (HERE/'manifest.json').write_text(json.dumps({p.name:sha(p) for p in files},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
