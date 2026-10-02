from pathlib import Path
import json, hashlib, math
import numpy as np
import pandas as pd
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, prepare_options, metrics
import im_mo_csi1000_put_protection_battery_v6 as model

OUT=Path('quant_param_scan_runs/20260914_im_short_otm_roll_v1')
BASE=Path('outputs/im_monthly_roll_3m_lowest_put_v1/daily_nav.csv')
OP=Path('data/im_monthly_roll_3m_lowest_put_v1/cffex_mo_puts.csv')
MARKET=Path('quant_param_scan_runs/20260914_icim_im_short_put_recovery_atm_full_model_v1_standalone_moneyness/model_market.csv')
CASH=1.03**(1/252)-1
RATIOS=[.90,.925,.95,.975]
TRIGGERS=[.98,.99,1.,None]

class Prices:
    def __init__(self, area):
        self.area=area
        if area=='real':
            self.base=pd.read_csv(BASE,parse_dates=['date'])
            raw=pd.read_csv(OP,parse_dates=['date'])
            raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m')
            self.options=prepare_options(raw,actual_expiry_map(raw,self.base))
            self.lookup=self.options.set_index(['contract','date'])
            self.chains={d:g for d,g in self.options.groupby('date')}
        else:
            self.market=pd.read_csv(MARKET,parse_dates=['date'])
            self.lookup={b.date:b for b in self.market.itertuples(index=False)}
            self.base=self.market.rename(columns={'spot_close':'csi1000_price_close'})
    def select(self, prior, day, month, ratio):
        spot=float(self.base.loc[self.base.date.eq(prior),'csi1000_price_close'].iloc[0])
        if self.area=='real':
            chain=self.chains.get(prior,self.options.iloc[:0])
            chain=chain[(chain.contract_month==month)&(chain.strike<spot)&(chain.actual_expiry>day)].copy()
            if chain.empty:return None
            chain['dist']=abs(chain.strike-spot*ratio)
            q=chain.sort_values(['dist','strike']).iloc[0]
            if q.volume<=0 or q.open_interest<=0:return None
            if (q.contract,day) not in self.lookup.index:return None
            live=self.lookup.loc[(q.contract,day)]
            if live.open<=0 or live.volume<=0:return None
            return dict(contract=q.contract,strike=float(q.strike),month=month,expiry=q.actual_expiry,spot=spot)
        # MO has three near months and three subsequent quarter months (not IM's four months).
        start=prior.to_period('M').to_timestamp()
        future=[start+pd.DateOffset(months=n) for n in range(18)]
        future=[m for m in future if model.third_friday(m,pd.DatetimeIndex(self.base.date))>prior]
        near=future[:3]
        listed=near+[m for m in future[3:] if m.month in {3,6,9,12}][:3]
        if month not in listed:return None
        step=25 if spot<=2500 else 50 if spot<=5000 else 100 if spot<=10000 else 200
        if month not in near:step*=2
        strike=math.floor(spot*ratio/step+.5)*step
        if strike>=spot:strike=math.floor((spot-1e-8)/step)*step
        expiry=model.third_friday(month,pd.DatetimeIndex(self.base.date))
        return dict(contract='MO'+month.strftime('%y%m')+'-P-'+str(strike),strike=strike,month=month,expiry=expiry,spot=spot)
    def quote(self,pos,day):
        if self.area=='real':
            q=self.lookup.loc[(pos['contract'],day)]
            return float(q.open),float(q.settle),q.volume>0 and q.open>0
        b=self.lookup[day]; t=max((pos['expiry']-day).days/365,0)
        op=model.proxy.bs_put(b.spot_open,pos['strike'],b.rate_open,b.dividend_open,b.sigma_open,t)
        mark=model.proxy.bs_put(b.spot_close,pos['strike'],b.rate_close,b.dividend_close,b.sigma_close,t)
        return op,mark,True

def run(prices,ratio,trigger,fee=.0001):
    label=f'{prices.area}_k{ratio:g}_t{trigger if trigger is not None else "expiry"}'
    equity=1.; pos=None; pending=False; target=None
    rows=[]; trades=[]; events=[]
    for i,b in enumerate(prices.base.itertuples(index=False)):
        day=b.date; prev=equity; pnl=cost=0.; action=''; prior=prices.base.iloc[i-1].date if i else None
        selected=None
        if pending and pos is not None:
            target=pos['month']+pd.offsets.MonthBegin(1)
            selected=prices.select(prior,day,target,ratio)
            op,mark,liquid=prices.quote(pos,day)
            if selected is not None and liquid:
                pnl+=pos['units']*200*(pos['mark']-op)
                c=pos['units']*200*float(prices.base.iloc[i-1].csi1000_price_close)*fee; cost+=c
                trades.append(dict(candidate=label,entry_date=pos['entry'],exit_date=day,reason='threshold_roll',strike=pos['strike'],entry_moneyness=pos['strike']/pos['spot'],premium=pos['sale'],buyback=op,trade_pnl=pos['units']*200*(pos['sale']-op)-pos['entry_cost']-c,holding_days=(day-pos['entry']).days,signal_date=pos['signal'],expiry=pos['expiry'],new_month=target))
                pos=None; pending=False; action='threshold_roll'
            else:
                action='roll_unavailable_keep_old'
        if pos is not None:
            op,mark,_=prices.quote(pos,day)
            pnl+=pos['units']*200*(pos['mark']-mark);pos['mark']=mark
        if pos is None and i>0:
            month=target if target is not None else day.to_period('M').to_timestamp()+pd.offsets.MonthBegin(1)
            q=selected if selected is not None else prices.select(prior,day,month,ratio)
            if q is not None:
                op,mark,_=prices.quote(q,day)
                # Same-open roll sizes new exposure using equity after old-leg realized P&L.
                units=(prev+pnl-cost)/(q['spot']*200)
                c=units*200*q['spot']*fee;cost+=c
                pos={**q,'units':units,'mark':mark,'sale':op,'entry':day,'entry_cost':c}
                pnl+=units*200*(op-mark)
                action=action or 'entry';target=None
            else:action=action or 'entry_unavailable'
        if pos is not None and day==pos['expiry']:
            trades.append(dict(candidate=label,entry_date=pos['entry'],exit_date=day,reason='itm_settlement' if pos['mark']>0 else 'worthless_expiry',strike=pos['strike'],entry_moneyness=pos['strike']/pos['spot'],premium=pos['sale'],buyback=pos['mark'],trade_pnl=pos['units']*200*(pos['sale']-pos['mark'])-pos['entry_cost'],holding_days=(day-pos['entry']).days,expiry=pos['expiry']))
            pos=None;pending=False;target=None;action='expiry'
        if pos is not None and trigger is not None and pos['strike']/b.csi1000_price_close>=trigger:
            pending=True;pos['signal']=day
        cash=prev*(.7 if pos is not None else 1.)*CASH
        equity+=pnl-cost+cash
        assert equity>0 and np.isfinite(equity)
        rows.append(dict(candidate=label,date=day,return_net=equity/prev-1,nav=equity,pnl=pnl,cost=cost,cash=cash,state='put' if pos else 'idle',action=action,contract=pos['contract'] if pos else '',expiry=pos['expiry'] if pos else pd.NaT))
        if action:events.append(rows[-1].copy())
    if pos:
        trades.append(dict(candidate=label,entry_date=pos['entry'],exit_date=day,reason='open_mark',strike=pos['strike'],entry_moneyness=pos['strike']/pos['spot'],premium=pos['sale'],buyback=pos['mark'],trade_pnl=pos['units']*200*(pos['sale']-pos['mark'])-pos['entry_cost'],holding_days=(day-pos['entry']).days,expiry=pos['expiry']))
    d=pd.DataFrame(rows);c=pd.DataFrame(trades)
    error=abs((d.pnl-d.cost).sum()-c.trade_pnl.sum())
    assert error<1e-10
    assert np.max(abs(d.nav-(1+d.return_net).cumprod()))<1e-10
    return d,c,pd.DataFrame(events),error

def main():
    (OUT/'preregister.json').write_text(json.dumps(dict(ratios=RATIOS,triggers=TRIGGERS,signal='close K/spot >= threshold, next open roll',new_expiry='old month plus one calendar month',filters=False,assignment=False,cutoff='2026-08-14'),indent=2),encoding='utf-8')
    daily=[];trades=[];events=[];summ=[];wide=[];activity=[];errors={};unavailable={}
    for area in ['real','model']:
        prices=Prices(area)
        for ratio in RATIOS:
            for trigger in TRIGGERS:
                d,c,e,err=run(prices,ratio,trigger)
                daily.append(d);trades.append(c);events.append(e);label=d.candidate.iloc[0];errors[label]=err
                w={'candidate':label,'strike_ratio':ratio,'trigger':trigger}
                for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1),('common_real_period',0)]:
                    start=pd.Timestamp('2022-07-22') if years==0 else d.date.max()-pd.DateOffset(years=years) if years else d.date.min()
                    available=start>=d.date.min();sub=d[d.date>=start]
                    m=metrics(sub.return_net) if available else {k:'N/A' for k in ['ann_return','ann_vol','max_dd','sharpe_repo']}
                    if not available:unavailable.setdefault(label,{})[segment]='Insufficient real history'
                    summ.append(dict(candidate=label,segment=segment,start=str(start.date()),end=str(d.date.max().date()),rows=len(sub) if available else 0,**m))
                    for k,v in m.items():w[k+'_'+segment]=v
                wide.append(w)
                closed=c[c.reason!='open_mark']
                activity.append(dict(candidate=label,rolls=int((c.reason=='threshold_roll').sum()),itm_settlements=int((c.reason=='itm_settlement').sum()),worthless=int((c.reason=='worthless_expiry').sum()),negative_closed_pnl=float(closed.loc[closed.trade_pnl<0,'trade_pnl'].sum()),positive_closed_pnl=float(closed.loc[closed.trade_pnl>0,'trade_pnl'].sum()),total_cost=float(d.cost.sum()),put_days=int((d.state=='put').sum()),max_holding_days=int(c.holding_days.max())))
                # Matched fee sensitivity: costs can change subsequent fractional sizes.
                d2,_,_,_=run(prices,ratio,trigger,fee=.0002)
                activity[-1].update({k+'_2xfee':v for k,v in metrics(d2.return_net).items()})
    pd.concat(daily).to_csv(OUT/'daily.csv.gz',index=False);pd.concat(trades).to_csv(OUT/'trades.csv',index=False);pd.concat(events).to_csv(OUT/'events.csv.gz',index=False)
    s=pd.DataFrame(summ);s.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False);pd.DataFrame(activity).to_csv(OUT/'activity.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    meta.update(data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [BASE,OP,MARKET,Path(__file__)]},cost_model=dict(one_way_notional=.0001,cash_annual=.03,reserve=.30,sensitivity_fee=.0002),unavailable_segments=unavailable,ledger_errors=errors,baseline={'candidate':'real_k0.95_texpiry'},execution='Prior close listed chain and spot select nearest OTM strike; previous volume/OI positive and execution open/volume positive; no nearest strike fallback. If new month unavailable keep old, retry. Close signal next open. No IM. Fractional units reset each entry. Model synthetic availability, BS proxy, expiry close proxy.',limitations='No exchange margin replay, forced liquidation, bidask, integer contracts. Historical snapshot cutoff Aug14. Model sigma_open timing uncertainty. No production baseline claim: new research hypothesis with native expiry-only controls. No cache writes; existing dirty worktree preserved.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'verification.json').write_text(json.dumps(errors,indent=2),encoding='utf-8')
    record='''# IM持续卖OTM Put提前滚仓 v1

## Data Snapshot
真实2022-07-22至2026-08-14；统一模型2015-04-16至2026-08-14。真实不足5/10年标N/A。模型全期均为BS代理价格，不拼接真实收益。共同真实期另列。模型输入复用既有冻结model_market快照，sigma_open存在时点不确定，到期收盘内在价值不是实际交割均价。
## Implementation Anchor
research_im_short_otm_roll_v1.py；复用既有prepare_options、actual_expiry_map、metrics、BS代理。新策略，不含估值、动量过滤，不转IM。四档90/92.5/95/97.5%，三阈值98/99/100%，以及各档不提前滚仓、到期现金结算对照。无原正式策略替换声明。
## Cost and Execution
前一收盘K/指数≥阈值，下一开盘买回旧仓并卖旧到期月之后一个日历月的新仓；新K按前一收盘指数选择最近OTM。开平单边1bp名义成本，另测2倍费用。每腿固定分数合约数量，新开按扣除旧仓平仓损益及成本后的权益缩放。风险状态30%缓冲，其余现金年化3%；空仓全额计息。未逐日重放卖Put保证金、强平、盘口或整数手数，不能宣称资金生存。
真实选约仅使用前一交易日挂牌链与量/OI；模型按MO三个近月及随后三个季月、远季月双倍行权价间距处理，来源https://www.cffex.com.cn/cn/zz1000gzqq.html 。不使用IM期货四个月份规则。最近档不符合或指定新月未挂牌则保留旧仓/等待，不改选另一档。实际开盘及当日成交量用于成交可用性判断，不据此优化选约。旧仓无法滚出仍可能实值现金结算；不转IM。新仓可能因多次连续下跌被滚到较远月，记录实际expiry及持仓时长，不能把这个解释为固定1个月到期策略。
## Verification
独立每腿开卖减平买减费用与逐日损益重组误差小于1e-10，NAV累乘复现；无未来收盘触发当日开盘交易。不能用研究模型证明真实可成交。
## Stability
预设网格、近窗与共同期及2倍费敏感性。无独立OOS；不按历史最优点自动晋级。
## Decision
research_only_no_promotion
## Commands
python -X utf8 research_im_short_otm_roll_v1.py
## Output Files
scan_summary.csv/window_metrics.csv各窗口；activity.csv滚仓次数、实值结算、已平亏损及费用；trades.csv独立腿账本；daily.csv.gz每日净损益；events.csv.gz执行事件。来源哈希和误差见scan_meta.json。
'''
    record+='\n## Full-Sample Results\n\n```text\n'+s[s.segment=='full'].to_string(index=False)+'\n```\n'
    record+='\n## Window Results\n\n完整标准窗口与共同真实期见scan_summary.csv及window_metrics.csv。\n'
    record+='\n研究判断：未加过滤的连续滚仓没有一致改善真实期收益和最大回撤；97.5%/99%仅为本轮收益较高的观察项，不晋级。95%/98%真实年化3.80%、最大回撤21.57%，相同95%持有到期现金结算对照5.19%/19.36%。97.5%/99%真实6.27%/21.95%，相同97.5%到期对照6.05%/22.20%；近3年收益仍低于到期对照，不能认定稳定占优。\n'
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_im_short_otm_roll_v1.py\n')
    print(s[s.segment=='full'].to_string(index=False))

if __name__=='__main__':main()
