"""Verify all saved joint-grid paths independently and produce the first report."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

HERE=Path(__file__).resolve().parent;OUT=HERE/'outputs'
NAMES={'BFW':'百分位单独','JIU':'韭圈单独','AND':'同时满足 AND','OR':'任一满足 OR','AND_ALL_EXIT':'AND 等两者恢复','OR_ALL_EXIT':'OR 等初始两者恢复'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def pct(x):return f'{100*x:.2f}%'
def table(h,rows):return '\n'.join(['| '+' | '.join(h)+' |','| '+' | '.join(['---']*len(h))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def pick(m,p,mode,t,w):return m.loc[m['product'].eq(p)&m['mode'].eq(mode)&m.timing.eq(t)&m.window.eq(w)].iloc[0]
def mask(d,w):
    if w=='train':return d.date.le('2024-08-13')
    if w=='holdout':return d.date.ge('2024-08-14')
    if w=='last1y':return pd.to_datetime(d.date).ge(pd.Timestamp(d.date.iloc[-1])-pd.DateOffset(years=1))
    return np.ones(len(d),dtype=bool)

def main():
    if (HERE/'record.md').exists():raise FileExistsError('First report is immutable')
    assert read(OUT/'completion.json')['cases']==20
    spec=HERE/'preregistered_spec.md';assert sha(spec)==(HERE/'preregistered_spec.md.sha256').read_text().strip()
    for relative,digest in read(HERE/'inputs/input_hashes.json').items():
        assert sha(HERE/'inputs/frozen_inputs'/relative)==digest
    for relative,digest in read(HERE/'inputs/runtime_source_hashes.json').items():
        assert sha(HERE/'inputs/runtime_source_backups'/relative)==digest
    m=pd.read_csv(OUT/'all_metrics.csv');checks=[];cycles=[];eligibility=[];statistics=[]
    for (p,mode,timing,label),group in m.groupby(['product','mode','timing','label'],sort=False):
        d=pd.read_csv(OUT/f'{p}_{label}_daily.csv.gz',dtype={'date':str})
        e=pd.read_csv(OUT/f'{p}_{label}_events.csv.gz',low_memory=False)
        s=pd.read_csv(OUT/f'{p}_{label}_signals.csv',keep_default_na=False)
        tr=pd.read_csv(OUT/f'{p}_{label}_state_trace.csv')
        a=read(OUT/f'{p}_{label}_audit.json');initial=float(a['replay']['initial_capital'])
        nav=np.r_[1.,np.cumprod(1+d.return_net.to_numpy(float))]
        assert np.max(abs(nav[1:]-d.nav))<1e-10
        assert np.max(abs(d.equity_close/initial-d.nav))<1e-10
        assert np.isfinite(d.nav).all() and d.nav.gt(0).all() and (d.cash_close-d.margin_reserved).min()>=-1e-7
        assert set(tr.grid_target_units.unique()).issubset({0.,.5})
        assert not e.loc[e.event.eq('trade'),'symbol'].fillna('').str.startswith('option|call|').any()
        assert abs(e.fee.fillna(0).sum()-d.fees_cumulative.iloc[-1])<1e-5
        ge=e.loc[e.symbol.fillna('').str.startswith('future|grid|')];gf=ge.loc[ge.event.eq('trade')]
        executed=s.loc[s.expected_execution_date.ne('')&s.expected_execution_date.le(d.date.iloc[-1])]
        ent=executed.loc[executed.action.eq('BUY_NEXT_OPEN')];ex=executed.loc[executed.action.eq('SELL_NEXT_OPEN')]
        assert len(ent)-len(ex)==int(d.grid_held_units.iloc[-1]>.0)
        dates=d.date.tolist();position={date:i for i,date in enumerate(dates)}
        active=None
        for x in executed.itertuples(index=False):
            i=position[x.signal_date];assert i+1<len(dates) and dates[i+1]==x.expected_execution_date
            fills=gf.loc[gf.signal_date.eq(x.signal_date)&gf.date.eq(x.expected_execution_date)&gf.phase.eq('open')]
            assert len(fills)>0
            if x.action=='BUY_NEXT_OPEN':assert active is None;active=x
            else:
                assert active is not None
                add_cycle(cycles,p,mode,timing,label,active,x.expected_execution_date,True,d,ge,initial);active=None
        if active is not None:add_cycle(cycles,p,mode,timing,label,active,d.date.iloc[-1],False,d,ge,initial)
        for window in group.window:
            z=d.loc[mask(d,window)];rr=z.return_net.to_numpy(float)
            vv=np.r_[1.,np.exp(np.cumsum(np.log1p(rr)))];dd=vv/np.maximum.accumulate(vv)-1
            ann=float(np.expm1(np.log1p(rr).sum()*252/len(rr)));mm=pick(m,p,mode,timing,window)
            assert abs(mm.cagr-ann)<1e-10 and abs(mm.max_drawdown-dd.min())<1e-10
            assert abs(mm.calmar-ann/-dd.min())<1e-9
            assert int(mm.grid_entries)==int(ent.expected_execution_date.between(z.date.iloc[0],z.date.iloc[-1]).sum())
            assert int(mm.completed_cycles)==int(ex.expected_execution_date.between(z.date.iloc[0],z.date.iloc[-1]).sum())
        full=pick(m,p,mode,timing,'full')
        assert int(full.terminal_open)==len(ent)-len(ex)
        statistics.append(dict(product=p,mode=mode,timing=timing,label=label,grid_entries=len(ent),completed_cycles=len(ex),terminal_open=int(full.terminal_open),
            fear_entries=int(ent.fear_entry_sources.ne('').sum()),jiu_only=int(ent.fear_entry_sources.eq('jiu').sum()),bfw_only=int(ent.fear_entry_sources.eq('bfw').sum()),
            joint_fears=int(ent.fear_entry_sources.eq('bfw|jiu').sum()),valuation_only=int(ent.fear_entry_sources.eq('').sum()),
            grid_net_cash_pnl=float(ge.equity_change.sum()),grid_fees=float(gf.fee.sum()),grid_exposure=float(d.grid_held_units.gt(0).mean())))
        if mode=='BFW':
            jq=tr.jiu_index.le(5);bq=tr.bfw_index.le(25)
            eligibility.append(dict(product=p,timing=timing,rows=len(tr),jiu_low_days=int(jq.sum()),bfw_low_days=int(bq.sum()),both_low_days=int((jq&bq).sum()),either_low_days=int((jq|bq).sum()),jiu_missing=int(tr.jiu_index.isna().sum()),bfw_missing=int(tr.bfw_index.isna().sum())))
        checks.append(dict(product=p,label=label,nav_identity=True,fees_identity=True,metrics_recomputed=True,next_actual_open=True,no_call=True,entry_exit_count_identity=True))
    stats=pd.DataFrame(statistics);cycle_frame=pd.DataFrame(cycles)
    for a in stats.itertuples(index=False):
        c=cycle_frame.loc[cycle_frame['product'].eq(a.product)&cycle_frame.label.eq(a.label)]
        assert len(c)==a.grid_entries and int(c.complete.sum())==a.completed_cycles
        assert abs(c.grid_net_cash_pnl.sum()-a.grid_net_cash_pnl)<1e-5
    pd.DataFrame(checks).to_csv(OUT/'independent_validation.csv',index=False)
    cycle_frame.to_csv(OUT/'grid_cycles.csv',index=False);stats.to_csv(OUT/'cycle_statistics.csv',index=False)
    pd.DataFrame(eligibility).to_csv(OUT/'index_condition_day_counts.csv',index=False)
    deltas=[]
    for row in m.to_dict('records'):
        b=pick(m,row['product'],'BFW',row['timing'],row['window'])
        row.update(cagr_delta_pp=(row['cagr']-b.cagr)*100,dd_depth_delta_pp=(abs(row['max_drawdown'])-abs(b.max_drawdown))*100,calmar_delta=row['calmar']-b.calmar)
        deltas.append(row)
    pd.DataFrame(deltas).to_csv(OUT/'metrics_vs_bfw.csv',index=False)
    figures(m)
    lead=[]
    for p in ('IC','IM'):
        b=pick(m,p,'BFW','same_day','full');a=pick(m,p,'AND','same_day','full');o=pick(m,p,'OR','same_day','full');j=pick(m,p,'JIU','same_day','full')
        lead += [f'{p} 当日口径：百分位单独年化{pct(b.cagr)}、回撤{pct(abs(b.max_drawdown))}；AND为{pct(a.cagr)}、{pct(abs(a.max_drawdown))}；OR为{pct(o.cagr)}、{pct(abs(o.max_drawdown))}；韭圈单独为{pct(j.cagr)}、{pct(abs(j.max_drawdown))}。','']
    lines=['# 韭圈与百分位同时或任一触发的网格历史回测','',*lead,
      '2026年10月16日。全部方案统一使用已验证的Put成本修复后账户基准，IC FIX11和IM其余参数保持一致。本研究未修改生产、日报、账本及交易权限。','',
      '## 规则','',
      '- 韭圈≤5、百分位≤25作为恐慌低位；对应恢复线均为≥50。',
      '- 单来源对照分别只看一个指数；AND要求同日两个均处于低位，OR只要求一个处于低位。原估值入场仍独立有效，因此条件是“估值 OR（双恐慌条件）”。',
      '- 一次只开0.5倍，两个指数同时触发不叠加。入场时冻结实际触发的来源，持仓中不新增来源；仅一个恐慌来源触发则等该来源恢复，两个均触发则任一恢复即满足恐慌退出。估值对应退出独立保留。',
      '- 主比较使用信号日指数，下一实际交易日开盘模拟执行。另将两来源均严格滞后一实际交易日，仍在信号后的下一交易日开盘模拟执行。缺失值不前填、不向更早日追溯。','',
      '## 全样本当日指数比较','',metrics_table(m,'same_day','full'),' ',
      '最大回撤为正的损失幅度。上述年化及Calmar是完整账户指标，不是网格独立收益率。基准修复使数值与前轮旧成本结果略有变化，不能直接拼比。','',
      '## 留出段当日指数比较','',metrics_table(m,'same_day','holdout'),' ',
      '## 两来源均用前一实际交易日','',metrics_table(m,'lag1','full'),' ',
      '前一交易日口径的留出段：','',metrics_table(m,'lag1','holdout'),' ',
      '## 退出规则敏感性','',
      '下表只改变“两个初始恐慌来源都触发时”的恐慌退出：从任一来源恢复改为同日两个都恢复。只有一个初始恐慌来源仍只看该来源；估值退出继续独立有效。','',sensitivity_table(m),' ',
      '## 网格直接损益和来源','',stats_table(stats),' ',
      '网格净损益含逐日盯市及手续费，未平仓周期按期末盯市；期初模型规模IC2324.08万元、IM2760.48万元，不代表用户实盘资金。季度展期交易不作为新网格入场。恐慌次数包括与估值同时触发，不等同指数处于低位的天数。','',
      '## 回撤曲线','', f'![回撤比较](sandbox:/{(OUT/"drawdown_comparison.png").as_posix()})','',
      '## 验证与边界','',
      '- IC：2022-09-19至2026-08-13，945个交易日；IM：2022-07-22至2026-08-14，986个交易日。训练截至2024-08-13，留出自2024-08-14；账户连续重放，分段收益重置为1。',
      '- 绩效按每1倍期货30%保证金/缓冲、现金年息3%、252交易日年化；15%仅为继承的用户资金假设，未经经纪商独立验证。',
      '- 修复后百分位基准逐日复现已保存的Put修复研究both账户，NAV/收益误差≤1e-10，金额误差≤1e-5；单来源信号逐日等于前轮算法。',
      f'- 两套独立状态机、{len(checks)}条账户路径、{len(m)}条分段指标、下一实际交易日开盘成交模型、费用、初始NAV回撤、来源冻结及开平仓数均通过核验。',
      '- 恐慌历史为后下载序列，未认证历史首次发布和修订；留出段是回溯伪样本外。IM普通买Put仍继承少量sticky-IV开盘代理；真实成交容量等边界并未被本研究消除。',
      '- 本轮固定阈值，不根据结果再寻优，也不要求IC/IM必须共同胜出。AND/OR及退出敏感性均完整披露。','',
      '## 文件','',
      '- preregistered_spec.md及SHA-256：运行前固定规则。',
      '- outputs/all_metrics.csv、metrics_vs_bfw.csv：全样本、训练、留出、末一年。',
      '- outputs/cycle_statistics.csv、grid_cycles.csv：次数、来源及逐周期损益。',
      '- outputs中每个方案的daily、events、signals、state_trace、audit：逐日追溯。',
      '- outputs/repaired_baseline_parity.json、independent_validation.csv、completion.json；inputs中保留输入和运行源码副本及SHA。','']
    (HERE/'record.md').write_text('\n'.join(lines),encoding='utf-8')
    files=[p for p in HERE.iterdir() if p.is_file()]
    for name in ('outputs','inputs','backups'):files.extend(p for p in (HERE/name).rglob('*') if p.is_file())
    manifest={str(p.relative_to(HERE)):sha(p) for p in files if p.name!='audit_manifest.json' and '__pycache__' not in p.parts}
    (OUT/'audit_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'validated_paths':len(checks),'metrics':len(m),'cycles':len(cycles),'report':str(HERE/'record.md')},ensure_ascii=False))

def add_cycle(rows,p,mode,timing,label,s,end,complete,d,ge,initial):
    start=s.expected_execution_date;g=ge.loc[ge.date.between(start,end)]
    before=d.loc[d.date.lt(start),'equity_close'];equity=float(before.iloc[-1]) if len(before) else initial
    final=float(d.loc[d.date.eq(end),'equity_close'].iloc[0])
    rows.append(dict(product=p,mode=mode,timing=timing,label=label,entry_signal_date=s.signal_date,entry_execution_date=start,
        exit_or_last_mark_date=end,complete=complete,entry_origin=s.fear_entry_cause,fear_entry_sources=s.fear_entry_sources,
        grid_net_cash_pnl=float(g.equity_change.sum()),grid_fees=float(g.loc[g.event.eq('trade'),'fee'].sum()),whole_account_return_during_cycle=final/equity-1))

def metrics_table(m,t,w):
    rows=[]
    for p in ('IC','IM'):
      for mode in ('BFW','JIU','AND','OR'):
        a=pick(m,p,mode,t,w);rows.append([p,NAMES[mode],pct(a.cagr),pct(abs(a.max_drawdown)),f'{a.calmar:.2f}',int(a.grid_entries),int(a.completed_cycles),pct(a.grid_exposure)])
    return table(['账户','恐慌来源','年化收益','最大回撤','Calmar','开仓次数','完成周期','网格持仓天数占比'],rows)
def sensitivity_table(m):
    rows=[]
    for p in ('IC','IM'):
      for mode in ('AND','AND_ALL_EXIT','OR','OR_ALL_EXIT'):
        a=pick(m,p,mode,'same_day','full');rows.append([p,NAMES[mode],pct(a.cagr),pct(abs(a.max_drawdown)),f'{a.calmar:.2f}',int(a.grid_entries),int(a.completed_cycles)])
    return table(['账户','退出口径','年化收益','最大回撤','Calmar','开仓次数','完成周期'],rows)
def stats_table(s):
    rows=[]
    for p in ('IC','IM'):
      for mode in ('BFW','JIU','AND','OR'):
        a=s.loc[s['product'].eq(p)&s['mode'].eq(mode)&s.timing.eq('same_day')].iloc[0]
        rows.append([p,NAMES[mode],int(a.grid_entries),int(a.fear_entries),int(a.joint_fears),int(a.terminal_open),f'{a.grid_net_cash_pnl/10000:.2f}',f'{a.grid_fees/10000:.2f}'])
    return table(['账户','方案','总开仓','恐慌参与','双指数同时触发','末期未平','网格净损益万元','网格费用万元'],rows)
def figures(m):
    fig,axes=plt.subplots(2,2,figsize=(13,8),sharex=True,constrained_layout=True)
    colors={'BFW':'#333333','JIU':'#2879b0','AND':'#21885a','OR':'#db762b'}
    for i,p in enumerate(('IC','IM')):
      for k,timing in enumerate(('same_day','lag1')):
        ax=axes[i,k]
        for mode in ('BFW','JIU','AND','OR'):
            d=pd.read_csv(OUT/f'{p}_{mode}_{timing}_daily.csv.gz');v=np.r_[1.,np.cumprod(1+d.return_net.to_numpy(float))]
            ax.plot(pd.to_datetime(d.date),v[1:]/np.maximum.accumulate(v)[1:]-1,color=colors[mode],label=mode,lw=1.2)
        ax.set_title(f'{p} - '+('same-day indices' if timing=='same_day' else 'prior-session indices'))
        ax.yaxis.set_major_formatter(PercentFormatter(1));ax.grid(alpha=.18);ax.legend(fontsize=8,ncol=4,loc='lower left')
    fig.savefig(OUT/'drawdown_comparison.png',dpi=170);plt.close(fig)

if __name__=='__main__':main()
