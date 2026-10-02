from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

OUT = Path('outputs/im_core_short95_portfolio_20260914_v1')
SRC = Path('quant_param_scan_runs/20260914_im_original_core_put_vs_short95_v2/daily.csv.gz')
WEIGHTS = [0, .1, .2, .3, .5, 1]

def metric(r):
    r = np.asarray(r, float)
    nav = np.r_[1., np.cumprod(1+r)]
    return dict(annual_return=nav[-1]**(252/len(r))-1,
                annual_volatility=np.std(r, ddof=1)*np.sqrt(252),
                max_drawdown=np.min(nav/np.maximum.accumulate(nav)-1))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    spec = dict(weights=WEIGHTS, primary='daily constant capital weights',
                sensitivity='initial capital allocation with drifting weights',
                source=str(SRC), cutoff='2026-08-14',
                rules='Native strategy returns including existing costs and cash; no leverage added; no additional capital transfer costs; no strategy logic changed',
                decision='research_only_no_promotion')
    (OUT/'spec.json').write_text(json.dumps(spec, indent=2), encoding='utf-8')
    d = pd.read_csv(SRC, parse_dates=['date'])
    rows, daily, audit = [], [], {}
    for area in ['real', 'model']:
        a = d[d.candidate.eq(area+'_original_roll_im_core_put102')][['date','return_net']].sort_values('date').reset_index(drop=True)
        b = d[d.candidate.eq(area+'_short95_le1_mom')][['date','return_net']].sort_values('date').reset_index(drop=True)
        assert a.date.equals(b.date) and not a.date.duplicated().any()
        x = a.return_net.to_numpy(); y = b.return_net.to_numpy()
        assert np.isfinite(x).all() and np.isfinite(y).all()
        av = np.cumprod(1+x); bv = np.cumprod(1+y)
        audit[area] = dict(rows=len(a), start=str(a.date.min().date()), end=str(a.date.max().date()), endpoint_error=0., variance_error=0.)
        for w in WEIGHTS:
            for mode in ['daily_rebalanced', 'initial_allocation']:
                if mode == 'daily_rebalanced':
                    r = (1-w)*x+w*y
                    predicted = (1-w)**2*np.var(x,ddof=1)+w*w*np.var(y,ddof=1)+2*w*(1-w)*np.cov(x,y,ddof=1)[0,1]
                    err = abs(np.var(r,ddof=1)-predicted)
                    assert err < 1e-12
                    audit[area]['variance_error'] = max(audit[area]['variance_error'],err)
                else:
                    n = np.r_[1., (1-w)*av+w*bv]
                    r = n[1:]/n[:-1]-1
                if w in [0,1]:
                    err = np.max(np.abs(r-(x if w==0 else y)))
                    assert err < 1e-12
                    audit[area]['endpoint_error'] = max(audit[area]['endpoint_error'],err)
                daily.append(pd.DataFrame(dict(date=a.date, area=area, mode=mode, short_weight=w, return_net=r)))
                windows = [('full', None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1),('common_real_period',None)]
                for window, years in windows:
                    start = pd.Timestamp('2022-07-22') if window=='common_real_period' else (a.date.max()-pd.DateOffset(years=years) if years else a.date.min())
                    if years and a.date.min()>start:
                        rows.append(dict(area=area,mode=mode,short_weight=w,window=window,status='N/A insufficient history'))
                        continue
                    mask = (a.date>=start).to_numpy()
                    m = metric(r[mask]); base = metric(x[mask])
                    rows.append(dict(area=area, mode=mode, short_weight=w, window=window,status='available',start=str(a.date[mask].min().date()),end=str(a.date[mask].max().date()),rows=int(mask.sum()),correlation=np.corrcoef(x[mask],y[mask])[0,1],vol_reduction=1-m['annual_volatility']/base['annual_volatility'],return_change=m['annual_return']-base['annual_return'],**m))
    result=pd.DataFrame(rows)
    result.to_csv(OUT/'metrics.csv',index=False)
    pd.concat(daily).to_csv(OUT/'daily.csv.gz',index=False)
    audit['source_sha256']=hashlib.sha256(SRC.read_bytes()).hexdigest()
    (OUT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    report = '''# IM核心Put与卖95%Put资金组合研究

基线：原版0.5倍纯滚IM＋原版102%核心Put；卖Put：估值0/1档且前一日MOM120>=0允许新入场，持有中不因条件失效提前退出，不买保护Put。
权重为资金权重，合计100%。主表每日恢复资金权重；敏感性表仅初始分配、此后权重漂移。已有各策略交易成本、30%期货缓冲和现金计息包含在净收益，未新增资金调整费用、买卖价差、整数合约约束或强平模拟。
真实区间2022-07-22至2026-08-14；模拟2015-04-16至2026-08-14。真实不足5年/10年标N/A。近窗及共同真实期见metrics.csv。
模型两套原生期货价格/贴水口径不同：核心为全收益指数＋每日0.0003贴水及季度滚动，卖Put为事后校准约10.33%年贴水的合成期货及月滚动，期权为代理定价。模拟用于敏感性，不能当独立样本外实证。
已验证日期一一对应、收益有限、0%/100%端点复现、方差协方差恒等式。没有改策略交易规则或生产配置。
决定：研究结果，不自动晋级；降低波动需同时看收益牺牲、共同下跌风险及窗口稳定性。
完整结果、卖Put规则及候选决策见[项目研究记录](../../notes/im_short95_core_put_portfolio_decisions_20260914.md)。记录结果不表示批准采用组合权重。
'''
    (OUT/'record.md').write_text(report,encoding='utf-8')
    print(result[(result.window.eq('full')) & result['mode'].eq('daily_rebalanced')].to_string(index=False))
    print('\nSENSITIVITY\n', result[(result.window.eq('full')) & result['mode'].eq('initial_allocation')][['area','short_weight','annual_return','annual_volatility','max_drawdown']].to_string(index=False))

if __name__=='__main__':
    main()
