"""Descriptive re-evaluation of already observed, frozen historical replays."""
from pathlib import Path
import hashlib,json
import pandas as pd
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
SOURCE=ROOT/'quant_research_runs/20261016_jiu_fear_grid_threshold_v1'
OUT=HERE/'outputs';OUT.mkdir(exist_ok=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def pct(x):return f'{x*100:.2f}%'
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])
def pick(m,p,l,w):return m.loc[m['product'].eq(p)&m.label.eq(l)&m.window.eq(w)].iloc[0]

def main():
    spec=HERE/'evaluation_spec.md'
    assert sha(spec)==(HERE/'evaluation_spec.md.sha256').read_text().strip()
    original=json.loads((SOURCE/'outputs/audit_manifest.json').read_text(encoding='utf-8'))
    assert all((SOURCE/f).exists() and sha(SOURCE/f)==h for f,h in original.items())
    metric_path=SOURCE/'outputs/metrics_with_timing_v2.csv';cycle_path=SOURCE/'outputs/grid_cycles.csv'
    m=pd.read_csv(metric_path);cycles=pd.read_csv(cycle_path)
    paths={str(metric_path.relative_to(ROOT)):sha(metric_path),str(cycle_path.relative_to(ROOT)):sha(cycle_path),str((SOURCE/'outputs/audit_manifest.json').relative_to(ROOT)):sha(SOURCE/'outputs/audit_manifest.json')}
    comparison=[];contribution=[];concentration=[]
    for p in ('IC','IM'):
      labels=['BFW25_50','JIU_e5_x40','JIU_e5_x50','JIU_e5_x60','JIU_e5_x70','JIU_e10_x40','BFW_LAG1_25_50','JIU_5_40_LAG1_DIAGNOSTIC']
      for label in labels:
        base='BFW_LAG1_25_50' if label in ('BFW_LAG1_25_50','JIU_5_40_LAG1_DIAGNOSTIC') else 'BFW25_50'
        for w in ('full','train','holdout','last1y'):
            a=pick(m,p,label,w);b=pick(m,p,base,w);row=a.to_dict()
            row.update(baseline_label=base,cagr_delta_pp=(a.cagr-b.cagr)*100,drawdown_depth_delta_pp=(abs(a.max_drawdown)-abs(b.max_drawdown))*100,calmar_delta=a.calmar-b.calmar)
            comparison.append(row)
        dp=SOURCE/f'outputs/{p}_{label}_daily.csv.gz';ep=SOURCE/f'outputs/{p}_{label}_events.csv.gz'
        paths[str(dp.relative_to(ROOT))]=sha(dp);paths[str(ep.relative_to(ROOT))]=sha(ep)
        d=pd.read_csv(dp);e=pd.read_csv(ep,low_memory=False)
        g=e.loc[e.symbol.fillna('').str.startswith('future|grid|')]
        c=cycles.loc[cycles['product'].eq(p)&cycles.label.eq(label)]
        capital=float(d.equity_close.iloc[0]/d.nav.iloc[0])
        pnl=float(g.equity_change.sum());fee=float(g.loc[g.event.eq('trade'),'fee'].sum())
        assert abs(pnl-c.grid_net_cash_pnl.sum())<1e-5
        assert abs(fee-c.grid_trade_fees.sum())<1e-5
        contribution.append(dict(product=p,label=label,initial_account_capital=capital,grid_net_cash_pnl=pnl,grid_pnl_over_initial_account_capital=pnl/capital,grid_fees=fee,completed_cycles=int(c.complete.sum()),open_cycles=int((~c.complete).sum()),wins_closed=int(c.loc[c.complete,'grid_net_cash_pnl'].gt(0).sum())))
        latest=g.loc[g.date.ge('2026-07-01')]
        concentration.append(dict(product=p,label=label,start='2026-07-01',end=d.date.iloc[-1],full_grid_net_cash_pnl=pnl,last_interval_grid_net_cash_pnl=float(latest.equity_change.sum()),before_interval_grid_net_cash_pnl=float(g.loc[g.date.lt('2026-07-01'),'equity_change'].sum())))
    comp=pd.DataFrame(comparison);attr=pd.DataFrame(contribution);conc=pd.DataFrame(concentration)
    for idx,a in conc.iterrows():
        base='BFW_LAG1_25_50' if a.label in ('BFW_LAG1_25_50','JIU_5_40_LAG1_DIAGNOSTIC') else 'BFW25_50'
        b=conc.loc[conc['product'].eq(a['product'])&conc.label.eq(base)].iloc[0]
        delta=a.full_grid_net_cash_pnl-b.full_grid_net_cash_pnl;last=a.last_interval_grid_net_cash_pnl-b.last_interval_grid_net_cash_pnl
        conc.loc[idx,'grid_pnl_advantage_vs_baseline']=delta
        conc.loc[idx,'last_interval_advantage_vs_baseline']=last
        conc.loc[idx,'last_interval_share_of_advantage']=last/delta if abs(delta)>1e-7 else np.nan
    comp.to_csv(OUT/'risk_return_comparison.csv',index=False);attr.to_csv(OUT/'direct_grid_contribution.csv',index=False);conc.to_csv(OUT/'concentration_diagnostic.csv',index=False)
    # These are full-account metrics. Direct sleeve P&L is shown separately below.
    rows=[]
    for p in ('IC','IM'):
      for label,name in [('BFW25_50','百分位25/50'),('JIU_e5_x40','韭圈5/40'),('JIU_e5_x50','韭圈5/50'),('JIU_e5_x60','韭圈5/60'),('JIU_e5_x70','韭圈5/70')]:
        a=pick(m,p,label,'full');rows.append([p,name,pct(a.cagr),pct(abs(a.max_drawdown)),f'{a.calmar:.2f}'])
    segmented=[]
    for w,name in [('train','训练段'),('holdout','留出段')]:
      for label,lname in [('BFW25_50','百分位25/50'),('JIU_e5_x40','韭圈5/40'),('JIU_e5_x50','韭圈5/50'),('JIU_e5_x70','韭圈5/70')]:
        a=pick(m,'IM',label,w);segmented.append([name,lname,pct(a.cagr),pct(abs(a.max_drawdown)),f'{a.calmar:.2f}',int(a.completed_cycles)])
    cashrows=[]
    for label,name in [('BFW25_50','百分位25/50'),('JIU_e5_x40','韭圈5/40'),('JIU_e5_x50','韭圈5/50'),('JIU_e5_x70','韭圈5/70')]:
        a=attr.loc[attr['product'].eq('IM')&attr.label.eq(label)].iloc[0]
        c=conc.loc[conc['product'].eq('IM')&conc.label.eq(label)].iloc[0]
        cashrows.append([name,f'{a.grid_net_cash_pnl/10000:.2f}',f'{a.grid_fees/10000:.2f}',int(a.completed_cycles),int(a.open_cycles),'—' if label=='BFW25_50' else pct(c.last_interval_share_of_advantage)])
    text=['# 韭圈能否构建更好的历史网格','',
      '结论：相对当前百分位25/50网格，IM 已有收益更高、回撤近似、Calmar更高的韭圈候选；IC 暂不支持替换。上一轮把降低最大回撤作为主要目标，并要求两产品共同通过筛选，不能据其无入选者推断 IM 没有更好的历史候选。','',
      '本次复评既有冻结结果，未重跑账户，未新增阈值，未改策略、日报或账本。各指标均为完整账户表现，不能称为网格独立收益率。','',
      '## 同日口径的风险收益','',table(['账户','网格','年化收益','最大回撤损失幅度','Calmar'],rows),'',
      'Calmar为年化收益除以最大回撤损失幅度。IM 入场5、退出40—70的整个邻域均高于当前基准的全样本Calmar；IC相同邻域均较低。因此不能给IC/IM相同的源替换结论。','',
      '## IM 分段表现','',table(['窗口','网格','年化收益','最大回撤','Calmar','完整周期'],segmented),'',
      '训练段截至2024-08-13；留出段从2024-08-14开始。5/40与5/50在两个分段的收益、Calmar均高于当前基准。5/70全样本收益较高，训练段却弱于5/40和5/50，不能只凭全样本最高收益把它称为最优。','',
      '## 网格直接贡献与集中度','',
      'IM 所有方案期初账户资本均为2760.48万元。下表累计现金损益含期货盯市和网格手续费，包含期末未平仓的盯市结果；不是年化、不是独立网格资金收益率。','',
      table(['网格','累计网格净损益 万元','网格费用 万元','完整周期','期末未平周期','7月至末期贡献占损益优势'],cashrows),'',
      '7月至末期使用双方共同的2026-07-01至2026-08-14日历区间。5/40相对基准的直接净损益优势超过全部来自此尾部区间；5/50约八成来自该区间。这个归因说明改善集中，并不等于剔除区间后重放了另一条策略。四次完整周期加一次未平周期，证据仍偏少。','',
      '## 前一实际交易日敏感性','',
      '已经完成的双方前一交易日5/40对照：IM年化29.91%升至32.21%，最大回撤12.711%至12.713%，Calmar2.35升至2.53；训练与留出Calmar也提高。这支持收益风险比改善的研究方向，不意味着回撤降低。5/50等阈值尚未做同样完整的滞后测试。','',
      'IC前一交易日5/40的年化34.00%至34.31%，回撤10.19%增至11.11%，Calmar3.34降至3.09，尚未发现综合优势。','',
      '## 研究取舍','',
      '- IM：保留韭圈入场≤5、退出≥40或≥50作为候选区间；5/40已有滞后敏感性，5/50保留为邻域参考。不能据此宣称参数已选定或已充分验证。',
      '- IC：现有16组没有展示优于当前百分位25/50的全样本Calmar，暂无换源依据。',
      '- 比较对象为当前百分位25/50。未对两个来源进行相同预算的分别寻优，不能断言韭圈来源本身必然更优。',
      '- 这些结果在本复评前已被观察；后续参数解释属于回溯分析。原指数缺历史首次发布时间与修订快照，不能称真正样本外。',
      '- IC样本2022-09-19至2026-08-13，IM样本2022-07-22至2026-08-14。采用原30%绩效保证金/缓冲、无Call与原冻结Put执行模型；并行中的Put修复没有被纳入本结果。','',
      '## 证据','',
      '原始冻结研究：../20261016_jiu_fear_grid_threshold_v1/record.md。复评表格：outputs/risk_return_comparison.csv、direct_grid_contribution.csv、concentration_diagnostic.csv。输入哈希见outputs/input_hashes.json；本目录清单见outputs/review_manifest.json。','']
    (HERE/'record.md').write_text('\n'.join(text),encoding='utf-8')
    (OUT/'input_hashes.json').write_text(json.dumps(paths,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=[p for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='review_manifest.json']
    (OUT/'review_manifest.json').write_text(json.dumps({str(p.relative_to(HERE)):sha(p) for p in files},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'comparison_rows':len(comp),'attribution_rows':len(attr),'source_artifacts_verified':len(original),'report':str(HERE/'record.md')},ensure_ascii=False))
    print(conc.loc[conc['product'].eq('IM')&conc.label.isin(['JIU_e5_x40','JIU_e5_x50','JIU_e5_x70']),['label','grid_pnl_advantage_vs_baseline','last_interval_advantage_vs_baseline','last_interval_share_of_advantage']].to_string(index=False))

if __name__=='__main__':main()
