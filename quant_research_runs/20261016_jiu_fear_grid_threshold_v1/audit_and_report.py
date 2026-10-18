"""Independent saved-output verification and research record; no account mutations."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

HERE=Path(__file__).resolve().parent;OUT=HERE/'outputs'
def readjson(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def pct(x):return f'{x*100:.2f}%'
def get(m,p,l,w):return m.loc[m['product'].eq(p)&m.label.eq(l)&m.window.eq(w)].iloc[0]
def datesmask(d,w):
    if w=='train':return d.date.le('2024-08-13')
    if w=='holdout':return d.date.gt('2024-08-13')
    if w=='last1y':return pd.to_datetime(d.date).ge(pd.Timestamp(d.date.iloc[-1])-pd.DateOffset(years=1))
    return np.ones(len(d),dtype=bool)

def main():
    assert (OUT/'result_manifest.json').exists()
    m=pd.read_csv(OUT/'all_metrics.csv')
    timing=pd.read_csv(OUT/'timing_v2_metrics.csv');m=pd.concat([m,timing],ignore_index=True)
    assert not m.duplicated(['product','label','window']).any()
    chosen=readjson(OUT/'selected_threshold.json')['selected']
    selected=None if chosen is None else chosen['label']
    checks=[];cycles=[]
    for (p,label),group in m.groupby(['product','label'],sort=False):
        d=pd.read_csv(OUT/f'{p}_{label}_daily.csv.gz',dtype={'date':str})
        e=pd.read_csv(OUT/f'{p}_{label}_events.csv.gz',low_memory=False)
        sig=pd.read_csv(OUT/f'{p}_{label}_signals.csv')
        r=d.return_net.to_numpy(float);nav=np.r_[1.,np.cumprod(1+r)]
        assert np.allclose(nav[1:],d.nav,rtol=0,atol=1e-10)
        assert np.allclose(d.equity_close/d.equity_close.iloc[0]*d.nav.iloc[0],d.nav,rtol=0,atol=1e-10)
        assert (d.cash_close-d.margin_reserved).min()>=-1e-7
        assert not e.loc[e.event.eq('trade'),'symbol'].fillna('').str.startswith('option|call|').any()
        a=readjson(OUT/f'{p}_{label}_audit.json')['execution']
        assert not a['missing_t1_open_fills'] and not a['wrong_phase_or_date'] and a.get('non_roll_same_day_close_fills',0)==0
        for w in group.window:
            rr=d.loc[datesmask(d,w),'return_net'].to_numpy(float)
            vv=np.r_[1.,np.exp(np.cumsum(np.log1p(rr)))];dd=vv/np.maximum.accumulate(vv)-1
            cagr=float(np.expm1(np.log1p(rr).sum()*252/len(rr)))
            row=get(m,p,label,w)
            assert abs(row.max_drawdown-dd.min())<1e-10 and abs(row.cagr-cagr)<1e-10
        checks.append(dict(product=p,label=label,days=len(d),t1_signals=a['executed_signals'],nav_identity=True,metrics_recomputed=True,no_call=True,nonnegative_free_cash=True))
        g=e.loc[e.symbol.fillna('').str.startswith('future|grid|')].copy()
        active=None
        for s in sig.itertuples(index=False):
            date=str(s.expected_execution_date)
            if date>d.date.iloc[-1]:continue
            if s.action=='BUY_NEXT_OPEN':
                assert active is None;active=s
            else:
                assert active is not None
                addcycle(cycles,p,label,active,date,True,d,g);active=None
        if active is not None:addcycle(cycles,p,label,active,d.date.iloc[-1],False,d,g)
    pd.DataFrame(checks).to_csv(OUT/'independent_validation.csv',index=False)
    pd.DataFrame(cycles).to_csv(OUT/'grid_cycles.csv',index=False)
    m.to_csv(OUT/'metrics_with_timing_v2.csv',index=False)
    comp=[]
    for p in ('IC','IM'):
      for en in (5,10,15,20):
       for ex in (40,50,60,70):
        lab=f'JIU_e{en}_x{ex}';r={'product':p,'entry':en,'exit':ex}
        for w in ('train','holdout','full','last1y'):
            a=get(m,p,lab,w);b=get(m,p,'BFW25_50',w)
            for field in ('cagr','max_drawdown','calmar','completed_cycles','grid_exposure'):r[f'{w}_{field}']=a[field]
            r[f'{w}_dd_depth_delta_pp']=(abs(a.max_drawdown)-abs(b.max_drawdown))*100
        comp.append(r)
    pd.DataFrame(comp).to_csv(OUT/'threshold_comparison.csv',index=False)
    timing_jiu='SELECTED_LAG1' if selected else 'JIU_5_40_LAG1_DIAGNOSTIC'
    focus=['VALUATION_ONLY','BFW25_50','JIU25_50_DIAGNOSTIC','JIU_e5_x40']+([selected] if selected and selected!='JIU_e5_x40' else [])+['BFW_LAG1_25_50',timing_jiu]
    exposure=[]
    for p in ('IC','IM'):
        for anchor,interval in [('BFW25_50','baseline_worst_drawdown'),('JIU_e5_x40','jiu_5_40_worst_drawdown')]:
          b=get(m,p,anchor,'full');start=b.peak_date;end=b.trough_date
          for lab in focus:
            d=pd.read_csv(OUT/f'{p}_{lab}_daily.csv.gz');z=d.loc[d.date.between(start,end)]
            exposure.append(dict(product=p,label=lab,interval=interval,start=start,end=end,grid_exposure=float(z.grid_held_units.gt(0).mean()),interval_return=float(np.prod(1+z.return_net)-1)))
    pd.DataFrame(exposure).to_csv(OUT/'baseline_drawdown_interval_exposure.csv',index=False)
    plots(m,selected)
    lines=['# 韭圈恐慌贪婪阈值对 IC 和 IM 账户回撤的影响','',
        '2026年10月16日。研究回放；现有策略、账本、日报及实盘权限均未修改。','']
    if selected:
        lines += [f'训练段选出统一阈值：韭圈指数 ≤{int(chosen["entry"])} 触发恐慌入场，≥{int(chosen["exit"])} 触发对应退出。这只是预先固定筛选标准下的候选，是否值得替换要看留出段和相同发布时间口径。','']
    else:
        lines += ['16 组中没有阈值同时满足两产品训练段至少两次完整周期、年化收益不落后百分位基准超过2个百分点的要求。本轮没有可据此选用的统一阈值。','']
        lines += ['在当日数据的全样本比较中，16组对 IC 和 IM 均未降低最大回撤。5/40 是参数网格的保守边界参考，下面保留其数值供评估代价，不称其为入选阈值。两来源均滞后一天的5/40补测，是看到扫描结果后登记的时点诊断。','']
    lines += ['## 比较范围','',
      '- IC：2022-09-19 至 2026-08-13，945个交易日；IM：2022-07-22 至 2026-08-14，986个交易日。行情截至8月，不包含10月当前这轮恐慌。两账户分别比较，未假设合并资金比例。',
      '- 训练段截至2024-08-13，留出段从2024-08-14开始；账户连续重放，分段收益重新归一，持仓状态不断开。年化用252交易日；回撤从初始净值1计入。',
      '- 保留 IC FIX11 和 IM 原有底仓、动量、Put、估值、实际挂牌合约和执行模型；无 Call。每1倍期货使用30%保证金/缓冲，现金年息3%，费用随真实账户交易重算。15%仅为原框架的资金可行性假设，未经经纪商独立核实。',
      '- 网格为单档0.5倍。估值或恐慌满足其一入场；首次来源冻结，估值来源按原估值退出，恐慌来源按对应指数退出；双来源任一对应退出即可。持仓不叠加，退出信号当天不重新入场，网格无额外Put。',
      '- 16组为入场5/10/15/20与退出40/50/60/70的组合。25/50仅用于照搬诊断。训练筛选最小化相邻参数及两账户的最差回撤增量，同时设周期数与收益下限；未用留出段调阈值。','',
      '## 全样本','',metric_table(m,focus,'full',selected),'',
      '最大回撤列展示正的损失幅度；相对基准差为百分点，正值代表回撤变大。每日当日值与前一交易日值分开比较。','',
      '## 留出段','',metric_table(m,focus,'holdout',selected),'']
    if True:
        lines += ['## 相同前一交易日口径','',
            '日报目前使用严格前一实际交易日值。下面同时让百分位和韭圈滞后一天，再在下一实际交易日开盘模拟执行。'+('阈值沿用训练段结果，没有重新选择。' if selected else '韭圈固定为5/40诊断参考；没有通过训练筛选，也没有扫描16组的完整滞后版本。'),' ',
            timing_table(m,timing_jiu),'']
        for p in ('IC','IM'):
            a=get(m,p,timing_jiu,'full');b=get(m,p,'BFW_LAG1_25_50','full');h=get(m,p,timing_jiu,'holdout');bh=get(m,p,'BFW_LAG1_25_50','holdout')
            lines += [f'{p}：全样本回撤损失幅度 {pct(abs(b.max_drawdown))} → {pct(abs(a.max_drawdown))}，变化 {(abs(a.max_drawdown)-abs(b.max_drawdown))*100:+.2f} 个百分点；留出段 {pct(abs(bh.max_drawdown))} → {pct(abs(h.max_drawdown))}。全样本年化收益 {pct(b.cagr)} → {pct(a.cagr)}。','']
        if selected:lines += [f'训练选择的邻域最坏回撤增量为 {chosen["worst_neighbor_dd_delta"]*100:+.2f} 个百分点。该数值仅来自训练段；没有声称其所有邻域或未来回撤均改善。','']
    icref=get(m,'IC','JIU_e5_x40','full');ict=get(m,'IC','JIU_e5_x40','train');ich=get(m,'IC','JIU_e5_x40','holdout')
    lines += ['## 为什么局部改善仍不能认定降回撤','',
      f'IC 5/40 的全样本最大回撤从 {icref.peak_date} 的高点到 {icref.trough_date} 的低点，跨过训练与留出切点。训练段回撤 {pct(abs(ict.max_drawdown))}、留出段回撤 {pct(abs(ich.max_drawdown))}，分开重置高点都会漏掉这段跨界回撤；完整账户回撤实际是 {pct(abs(icref.max_drawdown))}。', '',
      '同日口径的 IC 5/40 在2024-06-11指数1.32时发出恐慌买入信号，下一交易日执行，直到2024-08-30指数44.81才发出对应退出信号。极低指数仍可能出现在一段持续下跌的中途，恢复阈值决定这段额外仓位持有多久。完整信号与周期明细可以追溯这一区间。','']
    lines += ['## 回撤发生时间','',
        table(['账户','方案','高点','低点','恢复前高'],[[p,n,get(m,p,l,'full').peak_date,get(m,p,l,'full').trough_date,str(get(m,p,l,'full').recovery_date)] for p in ('IC','IM') for l,n in ([('BFW25_50','百分位25/50'),('JIU_e5_x40','韭圈5/40参考')]+([(selected,'韭圈训练选择')] if selected else []))]),'',
        '图中的实线使用当日指数，虚线使用前一交易日指数。参数热图展示所有候选相对百分位25/50的回撤增量，正值更差。','',
        '![回撤曲线](outputs/drawdown_comparison.png)','',
        '![所有阈值回撤增量](outputs/threshold_drawdown_heatmap.png)','',
        '## 数据与验证边界','',
        '- 韭圈数据来自页面 https://jiucaishuo.com/indicators?tab=fg 实际调用的 https://ai2.jiucaishuo.com/api/fear-greed 的 history，1502行，2020-08-03至2026-10-15。保存的是无损日期/数值提取及来源元数据，非完整原始HTTP响应。',
        '- 回测信号日韭圈无缺口；百分位2026-07-27缺失，当日不触发恐慌。前一日口径先对齐完整实际行情日历，2026-07-28也不以更早值替补。',
        '- 当前可下载历史不是历史每日首次发布快照，无法认证公式版本、修订与当时可得性。因此后段是回溯伪样本外，滞后一天也不能替代逐时点数据认证。',
        '- IC/IM 百分位基准逐日复现原保存账户：NAV及收益误差≤1e-10，金额误差≤1e-5。所有账户重新核对净值复利、现金与保证金、独立信号状态机、下一实际交易日开盘执行及无Call。',
        f'- 独立复核 {len(checks)} 条账户路径和 {len(m)} 条分段指标全部通过。网格周期明细含网格本身现金盈亏及同期全账户收益，二者分别标注，不把网格增量当作账户总收益。',
        '- 同工作区的另一项工作在首次运行后修改了策略依赖。此研究恢复了与首次导入SHA完全一致的只读副本，并以该副本完成缓存核验和滞后对照；未回滚或修改另一项工作。变更证据见 concurrent_dependency_change.json。','',
        '## 证据文件','',
        '- 预注册：preregistered_spec.md、timing_sensitivity_v2_spec.md、timing_diagnostic_v3_spec.md 及对应 SHA-256；v3明确记录已经看到v1结果。',
        '- 数值：outputs/metrics_with_timing_v2.csv、threshold_comparison.csv、training_selection.csv。',
        '- 逐日账户、信号与事件：outputs 中每一方案对应 daily、signals、events 文件。',
        '- 核验：baseline_parity.json、independent_validation.csv、timing_v2_checks.json、source_manifest.json、audit_manifest.json。','']
    (HERE/'record.md').write_text('\n'.join(lines),encoding='utf-8')
    artifacts=[p for p in HERE.iterdir() if p.is_file()]
    for f in ('inputs','outputs','backups'):artifacts.extend(p for p in (HERE/f).rglob('*') if p.is_file())
    manifest={str(p.relative_to(HERE)):sha(p) for p in artifacts if p.name not in ('audit_manifest.json',)}
    (OUT/'audit_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'validated_paths':len(checks),'metrics':len(m),'selected':chosen,'record':str(HERE/'record.md')},ensure_ascii=False))

def addcycle(rows,p,l,s,end,complete,d,g):
    start=str(s.expected_execution_date);z=g.loc[g.date.between(start,end)]
    before=d.loc[d.date.lt(start),'equity_close'];initial=float(before.iloc[-1]) if len(before) else float(d.equity_close.iloc[0]/d.nav.iloc[0])
    final=float(d.loc[d.date.eq(end),'equity_close'].iloc[0]);pnl=float(z.equity_change.sum())
    rows.append(dict(product=p,label=l,entry_signal_date=s.signal_date,entry_execution_date=start,exit_execution_date=end,complete=complete,entry_origin=s.fear_entry_cause,grid_net_cash_pnl=pnl,grid_pnl_over_entry_account_equity=pnl/initial,whole_account_return_during_cycle=final/initial-1,grid_trade_fees=float(z.loc[z.event.eq('trade'),'fee'].sum())))

def labelname(l,selected):
    return {'VALUATION_ONLY':'仅估值网格','BFW25_50':'百分位25/50 当日','JIU25_50_DIAGNOSTIC':'韭圈25/50 照搬诊断','JIU_e5_x40':'韭圈5/40 保守参考','BFW_LAG1_25_50':'百分位25/50 前一日','SELECTED_LAG1':'韭圈训练选择 前一日','JIU_5_40_LAG1_DIAGNOSTIC':'韭圈5/40 前一日诊断'}.get(l,'韭圈训练选择 当日' if l==selected else l)
def metric_table(m,labels,w,selected):
    rows=[]
    for p in ('IC','IM'):
      for l in labels:
        a=get(m,p,l,w);b=get(m,p,'BFW_LAG1_25_50' if l in ('SELECTED_LAG1','BFW_LAG1_25_50','JIU_5_40_LAG1_DIAGNOSTIC') else 'BFW25_50',w)
        rows.append([p,labelname(l,selected),pct(a.cagr),pct(abs(a.max_drawdown)),f'{(abs(a.max_drawdown)-abs(b.max_drawdown))*100:+.2f}',f'{a.calmar:.2f}',int(a.completed_cycles),pct(a.grid_exposure)])
    return table(['账户','方案','年化收益','最大回撤','回撤差 pp','Calmar','完整周期','网格持有日占比'],rows)
def timing_table(m,jiu):
    rows=[]
    for p in ('IC','IM'):
      for w,name in [('full','全样本'),('train','训练'),('holdout','留出'),('last1y','末一年')]:
        b=get(m,p,'BFW_LAG1_25_50',w);a=get(m,p,jiu,w)
        rows.append([p,name,pct(abs(b.max_drawdown)),pct(abs(a.max_drawdown)),f'{(abs(a.max_drawdown)-abs(b.max_drawdown))*100:+.2f}',pct(b.cagr),pct(a.cagr)])
    return table(['账户','窗口','百分位回撤','韭圈回撤','回撤差 pp','百分位年化','韭圈年化'],rows)

def plots(m,selected):
    fig,axes=plt.subplots(2,1,figsize=(12,7.5),sharex=True,constrained_layout=True)
    for ax,p in zip(axes,('IC','IM')):
        styles=[('BFW25_50','BFW 25/50 same-day','#c24d42','-'),('BFW_LAG1_25_50','BFW 25/50 prior session','#c24d42','--')]
        if selected:styles += [(selected,'Jiu selected same-day','#2674a5','-'),('SELECTED_LAG1','Jiu selected prior session','#2674a5','--')]
        else:styles += [('JIU_e5_x40','Jiu 5/40 diagnostic same-day','#2674a5','-'),('JIU_5_40_LAG1_DIAGNOSTIC','Jiu 5/40 diagnostic prior session','#2674a5','--')]
        for lab,name,color,ls in styles:
            d=pd.read_csv(OUT/f'{p}_{lab}_daily.csv.gz');v=np.r_[1.,np.cumprod(1+d.return_net.to_numpy(float))];dd=v[1:]/np.maximum.accumulate(v)[1:]-1
            ax.plot(pd.to_datetime(d.date),dd,label=name,color=color,ls=ls,lw=1.25)
        ax.set_title(p+' full-account drawdown');ax.yaxis.set_major_formatter(PercentFormatter(1));ax.grid(alpha=.18);ax.legend(loc='lower left',fontsize=8,ncol=2)
    fig.savefig(OUT/'drawdown_comparison.png',dpi=170);plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11,7),constrained_layout=True)
    for i,p in enumerate(('IC','IM')):
      for k,w in enumerate(('full','holdout')):
        ax=axes[i,k];b=get(m,p,'BFW25_50',w)
        arr=np.array([[(abs(get(m,p,f'JIU_e{en}_x{ex}',w).max_drawdown)-abs(b.max_drawdown))*100 for ex in (40,50,60,70)] for en in (5,10,15,20)])
        lim=max(abs(arr).max(),.1);ax.imshow(arr,cmap='RdBu_r',vmin=-lim,vmax=lim)
        for ii in range(4):
          for jj in range(4):ax.text(jj,ii,f'{arr[ii,jj]:+.2f}',ha='center',va='center',color='white' if abs(arr[ii,jj])>.65*lim else '#222')
        ax.set_xticks(range(4),[40,50,60,70]);ax.set_yticks(range(4),[5,10,15,20]);ax.set_xlabel('Exit >=');ax.set_ylabel('Entry <=');ax.set_title(f'{p} {w}: drawdown depth delta (pp)')
    fig.savefig(OUT/'threshold_drawdown_heatmap.png',dpi=170);plt.close(fig)

if __name__=='__main__':main()
