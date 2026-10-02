from pathlib import Path
import json, hashlib, math
import pandas as pd
import numpy as np
import im_mo_csi1000_put_protection_battery_v6 as model
import im_roll50_momentum50_fullcycle_proxy_v1 as carry_model
from research_im_short_put_recovery_v1 import run, CASH
from im_put_maturity_valuation_tiers_v3 import metrics

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_icim_im_short_put_recovery_full_model_v1_standalone_moneyness'

def build_inputs():
    market,checks=model.model_market()
    dates=pd.DatetimeIndex(market.date)
    real=pd.read_csv(carry_model.REAL_UPSTREAM_PATH)
    real=real[real.date<=str(market.date.max().date())].copy()
    basis=carry_model.post_listing_basis(real)
    annual_log=math.log1p(basis['annual_geometric'])
    months=pd.date_range(market.date.min().to_period('M').to_timestamp(),market.date.max()+pd.DateOffset(months=2),freq='MS')
    expiries={m:model.third_friday(m,dates) for m in months}
    frows=[]
    for b in market.itertuples(index=False):
        for month in months:
            expiry=expiries[month]
            if month>b.date.to_period('M').to_timestamp()+pd.DateOffset(months=2) or expiry<b.date:
                continue
            t=max((expiry-b.date).days/365,0)
            factor=math.exp(-annual_log*t)
            frows.append({'date':b.date,'contract':'IM'+month.strftime('%y%m'),'open':b.spot_open*factor,
                          'close':b.spot_close*factor,'settle':b.spot_close*factor,'volume':1})
    futures=pd.DataFrame(frows)
    fl=futures.set_index(['contract','date'])
    # Collect strikes that can be selected using previous spot close, then price their whole lives.
    keys=set()
    for i,b in enumerate(market.itertuples(index=False)):
        if i==0:continue
        spot=market.iloc[i-1].spot_close
        month=b.date.to_period('M').to_timestamp()+pd.offsets.MonthBegin(1)
        step=25 if spot<=2500 else 50 if spot<=5000 else 100 if spot<=10000 else 200
        for ratio in (.95,.90):
            strike=math.floor(spot*ratio/step+0.5)*step
            keys.add((month,strike))
    orows=[]
    for month,strike in sorted(keys):
        expiry=expiries[month]
        for b in market[(market.date>=month-pd.DateOffset(months=1))&(market.date<=expiry)].itertuples(index=False):
            t=max((expiry-b.date).days/365,0)
            op=model.proxy.bs_put(b.spot_open,strike,b.rate_open,b.dividend_open,b.sigma_open,t)
            mark=model.proxy.bs_put(b.spot_close,strike,b.rate_close,b.dividend_close,b.sigma_close,t)
            orows.append({'date':b.date,'contract':'MO'+month.strftime('%y%m')+'-P-'+str(strike),
                          'contract_month':month,'actual_expiry':expiry,'strike':strike,'open':op,
                          'settle':mark,'volume':1,'open_interest':1})
    options=pd.DataFrame(orows)
    # All liquidity flags are synthetic availability, never observed trades.
    brows=[]
    active=None
    previous=None
    for b in market.itertuples(index=False):
        month=b.date.to_period('M').to_timestamp()
        if b.date>expiries[month]:month+=pd.offsets.MonthBegin(1)
        contract='IM'+month.strftime('%y%m')
        old=active if active is not None else contract
        q=fl.loc[(old,b.date)]
        gross=0.0 if previous is None else q.settle/previous-1
        roll=b.date==expiries[month]
        new='IM'+(month+pd.offsets.MonthBegin(1)).strftime('%y%m') if roll else contract
        nq=fl.loc[(new,b.date)]
        cost=.0001 if previous is None else (.0002 if roll else 0.0)
        ret=(1+gross)*(1-cost)-1+.7*CASH
        brows.append({'date':b.date,'contract':new,'roll_to':new if roll else '',
                      'csi1000_price_close':b.spot_close,'baseline_plus_cash_ret':ret})
        active,previous=new,float(nq.settle)
    base=pd.DataFrame(brows)
    return market,base,options,futures,checks,basis

def main():
    market,base,options,futures,checks,basis=build_inputs()
    ds,cs,es=[],[],[]
    for ratio in (.95,.90):
        d,e,c=run(base,options,futures,ratio)
        ds.append(d);cs.append(c);es.append(e)
    ds.append(pd.DataFrame({'date':base.date,'candidate':'rolling_im','return_net':base.baseline_plus_cash_ret,
                            'nav':(1+base.baseline_plus_cash_ret).cumprod(),'state':'future'}))
    daily=pd.concat(ds,ignore_index=True)
    cycles=pd.concat(cs,ignore_index=True)
    errors={}
    for k,g in daily[daily.candidate!='rolling_im'].groupby('candidate'):
        errors[k]=float(abs((g.pnl-g.cost).sum()-cycles[cycles.candidate==k].realized_pnl.sum()))
        assert errors[k]<1e-12
        assert np.allclose(g.nav,(1+g.return_net).cumprod(),atol=1e-12)
    daily['data_layer']='uniform_theoretical_model'
    daily.to_csv(OUT/'daily.csv',index=False)
    cycles.to_csv(OUT/'cycles.csv',index=False)
    pd.concat(es).to_csv(OUT/'events.csv',index=False)
    market.to_csv(OUT/'model_market.csv',index=False)
    summary,wide=[],[]
    for k,g in daily.groupby('candidate'):
        w={'candidate':k}
        for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1),('prelisting',-1),('postlisting_model',-2)]:
            if years==-1:sub=g[g.date<'2022-07-22']
            elif years==-2:sub=g[g.date>='2022-07-22']
            else:sub=g if years is None else g[g.date>=g.date.max()-pd.DateOffset(years=years)]
            m=metrics(sub.return_net)
            summary.append({'candidate':k,'segment':segment,'start':str(sub.date.min().date()),'end':str(sub.date.max().date()),'rows':len(sub),**m})
            for field in ('ann_return','max_dd','sharpe_repo'):w[field+'_'+segment]=m[field]
        wide.append(w)
    s=pd.DataFrame(summary)
    s.to_csv(OUT/'scan_summary.csv',index=False)
    pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    sources=[model.PRICE,model.TRI,model.OHLC,model.ETF50,model.Q50,model.GOV10Y,carry_model.REAL_UPSTREAM_PATH,Path(__file__),ROOT/'research_im_short_put_recovery_v1.py']
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    meta.update({'data_snapshot':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},'model_checks':checks,
                 'cost_model':{'one_way_notional':.0001,'cash_annual':.03,'risk_reserve':.30},
                 'basis_calibration':basis,'ledger_errors':errors,'parameters':[.95,.90],
                 'execution_assumptions':'Same short-put recovery state machine as original real test. Futures theoretical F=S*exp(-log(1+calibrated annual carry)*T); monthly expiry roll; option BS with existing QVIX50 x RV1000/RV50 sigma model. Uniform modeled prices even postlisting. No state reset. Prior-close strike, next open execution; expiry uses spot close proxy. Synthetic strike availability and liquidity. Cycle costs included in recovery, cash excluded.',
                 'limitations':'Ex-post average 2022-2026 carry applied to past has calibration lookahead; theoretical premiums and expiry-close proxy; no observed liquidity, bid/ask or margin liquidation. Not investable historical evidence. Same-day sigma_open model uses opening QVIX proxy; timing uncertainty. No cache writes. Original dirty worktree preserved. cutoff 2026-08-14.'})
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    text='''# IM 卖Put转期货回本：全期统一模型 v1\n\n## Data Snapshot\n2015-04-16至2026-08-14，共2756个模型交易日。全期均为理论模型，不拼接真实MO收益。真实合约独立测试仍保留，不能把两者的近3年/1年数字混用。共同窗口输出Full/10Y/5Y/3Y/1Y，以及上市前/上市后模型段；仓位跨段连续，不重置。\n\n## Implementation Anchor\n复用im_mo_csi1000_put_protection_battery_v6.model_market和原research_im_short_put_recovery_v1.run；相同行权价规则、两MO对一IM点值、现金和成本。按指数上一收盘选最近模型挂牌档，不提前行权；到期实值次日开盘转IM，持有期间按月展期；本轮全部交易盈亏含权利金和成本达到零后次日开盘退出，再下一交易日卖下月Put。不能保证次日成交还在回本价。\n\n模型IM期限价格F=S exp(-cT)，c来自既有post_listing_basis；上市后平均贴水事后校准历史，存在校准前视，不作为可交易回测。BS权利金使用既有QVIX50乘60日RV比例波动率代理、国债利率及既有股息代理。sigma_open沿用开盘QVIX，存在成交时点不确定。模型到期以指数日收盘为内在价值，不是实际两小时交割均价。合约档位25/50/100/200点，挂牌范围和流动性为模型可用假设。\n\n## Cost and Execution\n单边1bp名义成本，风险阶段30%缓冲、70%现金按3%计息，空仓全额计息；每轮单位固定、下一轮按权益缩放。回本不含现金利息。无盘口/保证金追缴/强平模拟。逐日NAV和逐轮损益重组均通过1e-12校验；没有现行生产基准parity声明。数据及代码SHA256见scan_meta.json。无缓存写入，旧研究输出和工作区用户改动保留。\n\n## Commands\npython -X utf8 research_im_short_put_recovery_full_model_v1.py\n\n## Stability\n仅两档预设参数，未做独立OOS；结果对波动率代理和事后平均贴水敏感。\n\n## Decision\nresearch_only_model_extension_no_promotion\n\n## Results\n'''
    text+=s.to_string(index=False)+'\n\n## Cycles\n'+cycles.to_string(index=False)
    (OUT/'record.md').write_text(text,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_im_short_put_recovery_full_model_v1.py\n')
    print(s.to_string(index=False))
    print('ledger errors',errors)
    print(cycles[cycles.assignment_date.fillna('').ne('')].groupby('candidate').agg(assignments=('closed','size'),exits=('closed','sum'),longest_closed_calendar_days=('recovery_days','max')).to_string())

if __name__=='__main__':main()
