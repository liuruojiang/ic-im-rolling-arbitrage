from pathlib import Path
import json, hashlib
import numpy as np
import pandas as pd
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, prepare_options, metrics

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'quant_param_scan_runs/20260914_icim_im_short_put_recovery_v1_standalone_moneyness'
BASE = ROOT / 'outputs/im_monthly_roll_3m_lowest_put_v1/daily_nav.csv'
OP = ROOT / 'data/im_monthly_roll_3m_lowest_put_v1/cffex_mo_puts.csv'
FU = ROOT / 'data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv'
CASH = 1.03 ** (1/252)-1

def run(base, options, futures, ratio):
    ol = options.set_index(['contract','date'])
    fl = futures.set_index(['contract','date'])
    equity = 1.0
    state = 'idle'
    pos = None
    pending = ''
    rows, events, cycles = [], [], []
    cycle = None
    for i, b in enumerate(base.itertuples(index=False)):
        day = b.date
        prev_equity = equity
        pnl = cost = 0.0
        action = ''
        if state == 'put':
            q = ol.loc[(pos['contract'],day)]
            # Official expiry settlement is intrinsic, not index daily close.
            pnl += pos['units']*200*(pos['mark']-q.settle)
            pos['mark'] = float(q.settle)
        elif state == 'future':
            q = fl.loc[(pos['contract'],day)]
            if pending == 'exit':
                assert q.open > 0 and q.volume > 0
                pnl += pos['units']*200*(q.open-pos['mark'])
                cost += pos['units']*200*q.open*0.0001
                action = 'recovery_exit_next_open'
                cycle['exit_date'] = str(day.date())
                cycle['recovery_days'] = (day-pd.Timestamp(cycle['assignment_date'])).days
                cycle['realized_pnl'] += pnl-cost
                cycle['exit_cycle_pnl'] = cycle['realized_pnl']
                cycle['closed'] = True
                cycles.append(cycle.copy())
                cycle = None
                state, pos, pending = 'idle', None, ''
            else:
                if str(b.roll_to) not in ('nan','') and str(b.roll_to) != pos['contract']:
                    # Inherit monthly roll calendar; execute at close, mark old leg first.
                    pnl += pos['units']*200*(q.close-pos['mark'])
                    nq = fl.loc[(b.roll_to,day)]
                    assert q.volume > 0 and nq.volume > 0
                    cost += pos['units']*200*(q.close+nq.close)*0.0001
                    pnl += pos['units']*200*(nq.settle-nq.close)
                    pos['contract'], pos['mark'] = b.roll_to, float(nq.settle)
                    action = 'monthly_roll_close'
                else:
                    pnl += pos['units']*200*(q.settle-pos['mark'])
                    pos['mark'] = float(q.settle)
        if pending == 'assign':
            q = fl.loc[(b.contract,day)]
            assert q.open > 0 and q.volume > 0
            pos = {'contract':b.contract,'units':cycle['units'],'mark':float(q.settle)}
            pnl += pos['units']*200*(q.settle-q.open)
            cost += pos['units']*200*q.open*0.0001
            state, pending, action = 'future', '', 'assignment_buy_next_open'
            cycle['assignment_date'] = str(day.date())
        # Wait one session after an exit; a new entry uses only previous close for strike.
        if state == 'idle' and i > 0 and action == '':
            spot = float(base.iloc[i-1].csi1000_price_close)
            month = day.to_period('M').to_timestamp()+pd.offsets.MonthBegin(1)
            chain = options[(options.date==day)&(options.contract_month==month)&(options.strike<spot)].copy()
            if not chain.empty:
                chain['dist'] = abs(chain.strike-spot*ratio)
                q = chain.sort_values(['dist','strike']).iloc[0]
                if q.open>0 and q.volume>0 and q.open_interest>0:
                    units = equity/(spot*200)
                    pos = {'contract':q.contract,'mark':float(q.settle),'units':units,'expiry':q.actual_expiry}
                    pnl += units*200*(q.open-q.settle)
                    cost += units*200*spot*0.0001
                    cycle = {'candidate':f'put_{ratio:.2f}','entry_date':str(day.date()),'put_contract':q.contract,
                             'strike':q.strike,'spot_reference':spot,'actual_moneyness':q.strike/spot,
                             'premium_points':q.open,'units':units,'realized_pnl':0.0,'closed':False,'assignment_date':''}
                    state, action = 'put','sell_next_month_put_open'
                else:
                    action = 'skip_illiquid_nearest_strike'
        if cycle is not None:
            cycle['realized_pnl'] += pnl-cost
        if state == 'put' and day == pos['expiry']:
            cycle['settlement_points'] = pos['mark']
            cycle['expiry_date'] = str(day.date())
            if pos['mark'] > 0:
                pending = 'assign'
                state, pos, action = 'idle',None,'itm_cash_settlement'
            else:
                cycle['exit_date'] = str(day.date())
                cycle['exit_cycle_pnl'] = cycle['realized_pnl']
                cycle['closed'] = True
                cycles.append(cycle.copy())
                cycle = None
                state, pos, action = 'idle',None,'worthless_expiry'
        if state == 'future':
            exit_cost = pos['units']*200*pos['mark']*0.0001
            if cycle['realized_pnl'] >= exit_cost:
                pending = 'exit'
        # Conservative constant 30% reserve in either risk state; idle earns full cash yield.
        cash = prev_equity*(0.7 if state!='idle' or pending=='assign' else 1.0)*CASH
        equity += pnl-cost+cash
        assert np.isfinite(equity) and equity>0
        rows.append({'date':day,'candidate':f'put_{ratio:.2f}','return_net':equity/prev_equity-1,
                     'nav':equity,'pnl':pnl,'cost':cost,'cash':cash,'state':state,'action':action})
        if action:
            events.append({**rows[-1], 'contract': '' if pos is None else pos['contract']})
    if cycle is not None:
        cycle['mark_date'] = str(day.date())
        cycle['open_cycle_pnl'] = cycle['realized_pnl']
        cycles.append(cycle.copy())
    return pd.DataFrame(rows),pd.DataFrame(events),pd.DataFrame(cycles)

def main():
    base = pd.read_csv(BASE,parse_dates=['date'])
    raw = pd.read_csv(OP,parse_dates=['date'])
    raw['contract_month'] = pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m')
    options = prepare_options(raw,actual_expiry_map(raw,base))
    futures = pd.read_csv(FU,parse_dates=['date'])
    assert not futures.duplicated(['date','contract']).any()
    # Unchanged authoritative monthly IM baseline, including its costs and cash.
    parity = float(abs((1+base.baseline_plus_cash_ret).cumprod()-base.nav_baseline_plus_cash).max())
    assert parity < 1e-12
    daily, events, cycles = [],[],[]
    for ratio in (.95,.90):
        d,e,c = run(base,options,futures,ratio)
        daily.append(d); events.append(e); cycles.append(c)
    daily.append(pd.DataFrame({'date':base.date,'candidate':'rolling_im','return_net':base.baseline_plus_cash_ret,
                               'nav':base.nav_baseline_plus_cash,'state':'future'}))
    d = pd.concat(daily,ignore_index=True)
    c = pd.concat(cycles,ignore_index=True)
    ledger_errors = {}
    for candidate,g in d[d.candidate!='rolling_im'].groupby('candidate'):
        error = abs((g.pnl-g.cost).sum()-c[c.candidate==candidate].realized_pnl.sum())
        assert error < 1e-12
        ledger_errors[candidate] = float(error)
    d.to_csv(OUT/'daily.csv',index=False)
    c.to_csv(OUT/'cycles.csv',index=False)
    pd.concat(events).to_csv(OUT/'events.csv',index=False)
    summary,wide=[],[]
    for candidate,g in d.groupby('candidate'):
        w={'candidate':candidate}
        for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            cutoff = g.date.min() if years is None else g.date.max()-pd.DateOffset(years=years)
            available = cutoff>=g.date.min()
            sub=g[g.date>=cutoff] if available else g.iloc[:0]
            m=metrics(sub.return_net) if available else {k:'N/A' for k in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            summary.append({'candidate':candidate,'segment':segment,'start':str(cutoff.date()),'end':str(g.date.max().date()),
                            'rows':len(sub),'available':available,'unavailable_reason':'' if available else 'Real IM/MO history begins 2022-07-22',**m})
            for k in ['ann_return','max_dd','sharpe_repo']:
                w[f'{k}_{segment}']=m[k]
        wide.append(w)
    s=pd.DataFrame(summary)
    s.to_csv(OUT/'scan_summary.csv',index=False)
    pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    meta.update({'data_snapshot':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (BASE,OP,FU)},
      'baseline_parity_max_abs':parity,'parameters':[.95,.90],
      'ledger_recomposition_errors':ledger_errors,
      'cost_model':{'one_way_notional_cost':0.0001,'reserve':0.30,'cash_annual':0.03,'bid_ask_model':'unavailable'},
      'unavailable_segments':{k:{'last_10y':'Real history begins 2022-07-22','last_5y':'Real history begins 2022-07-22'} for k in d.candidate.unique()},
      'execution_assumptions':'Previous index close strike; nearest listed OTM strike without liquidity fallback; next calendar month; open executions; settlement marks; actual monthly IM roll at close; expiry official option settlement; two MO per IM; fractional normalized units fixed per cycle; 30% reserve, cash 3%; 1bp notional per side; recovery excludes cash and includes all cycle costs; next-open exits may slip below breakeven.',
      'limitations':'Diagnostic historical close/open proxies, no bid/ask or intraday margin/liquidation model; no current production parity claimed; cutoff 2026-08-14; no proxy pre-listing data; existing dirty worktree preserved; no cache writes.'})
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record='''# IM 卖 Put 转滚动期货回本测试 v1\n\n研究诊断，未晋级。预设参数95%、90%，无新增择优参数。真实中金所缓存2022-07-22至2026-08-14，不代表当前行情。\n\n执行及资金口径见scan_meta.json。2张MO对应1张IM点值；按上一交易日指数收盘选择最近虚值行权价，下一月到期；最近行权价无流动性则跳过，不改选另一档。到期官方结算值大于零时现金结算，下一交易日开盘买IM；回本包含当轮权利金、期权亏损、期货累计滚动损益和成本，不含现金利息。收盘触发后次日开盘退出，实际退出可能再亏；之后下一交易日卖新Put。未回本头寸保留盯市。每轮固定单位，下一轮按权益重新缩放。\n\n持续滚IM基准复用冻结baseline_plus_cash_ret，NAV逐日parity误差小于1e-12；这是新策略诊断，不是现行v1.3绩效比较。期货展期日复用既有月度链，在收盘执行。1bp单边名义成本，风险状态固定30%缓冲、70%现金计息3%，空仓全额现金计息。卖Put保证金未做逐日交易所/经纪商重放，因此不宣称资金生存或可成交收益。\n\n5Y/10Y N/A：真实历史不足。全样本/3Y/1Y使用共同日期窗口。仅两档预设测试，不证明参数邻域或独立OOS稳定性。旧文件、冻结规格及生产不修改，无缓存写入；原工作区未提交改动保留。\n\n复现：python -X utf8 research_im_short_put_recovery_v1.py\n\n数据路径与SHA256见scan_meta.json；逐日损益见daily.csv；操作见events.csv；每轮结算与回本见cycles.csv。\n\n'''
    record+='\n## Data Snapshot\n真实缓存及哈希见scan_meta.json。\n## Stability\n仅两档预设参数，未做独立OOS，不判断全局最优。\n## Decision\nresearch_only_no_promotion\n\n'
    record+=s.to_string(index=False)+'\n\n'+c.to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_im_short_put_recovery_v1.py\n')
    print(s[s.available].to_string(index=False))
    print(c.groupby('candidate').agg(cycles=('closed','size'),closed=('closed','sum')).to_string())
    print('baseline parity',parity)

if __name__=='__main__':main()
