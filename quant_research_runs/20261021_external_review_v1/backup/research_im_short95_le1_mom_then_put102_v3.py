from pathlib import Path
import inspect, json, hashlib, importlib.util
import pandas as pd
import numpy as np
import im_mainline_v1_1 as policy
import im_mo_csi1000_put_protection_battery_v6 as model
import research_im_short_put_recovery_atm_real_v1 as original
import research_im_short_put_recovery_atm_full_model_v1 as full
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map,prepare_options,metrics

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_le1_mom_then_put102_v3_protection_enabled'

class Protection:
    def __init__(self,scope,base,options,futures,market,schedule,enabled):
        self.scope,self.enabled=scope,enabled
        self.market=market.set_index('date') if market is not None else None
        self.schedule=schedule.set_index('date')
        self.options={d:g for d,g in options.groupby('date')}
        self.lookup=options.set_index(['contract','date'])
        self.futures=futures.set_index(['contract','date'])
        self.dates=pd.DatetimeIndex(base.date)
        self.resets={model.third_friday(m,self.dates) for m in pd.date_range(base.date.min().to_period('M').to_timestamp(),base.date.max(),freq='MS')}
        self.pos=None;self.events=[]
    def quote(self,pos,day,when='settle'):
        if self.scope=='model':
            b=self.market.loc[day]
            t=max((pos['expiry']-day).days/365,0)
            spot=b.spot_open if when=='open' else b.spot_close
            rate=b.rate_open if when=='open' else b.rate_close
            dividend=b.dividend_open if when=='open' else b.dividend_close
            sigma=b.sigma_open if when=='open' else b.sigma_close
            return model.proxy.bs_put(spot,pos['strike'],rate,dividend,sigma,t)
        q=self.lookup.loc[(pos['contract'],day)]
        if when=='open':return float(q.open) if q.volume>0 and q.open>0 else float(q.settle)
        if when=='close':return float(q.close) if q.volume>0 and q.close>0 else float(q.settle)
        return float(q.settle)
    def select(self,day,contract,units,qty):
        reference=float(self.futures.loc[(contract,day),'close'])
        target=day+pd.DateOffset(months=3)
        if self.scope=='model':
            month=model.model_month(day,target,self.dates)
            expiry=model.third_friday(month,self.dates)
            strike=reference*1.02
            # Existing model engine uses a continuous target strike; no invented observed chain.
            p=dict(contract='MODEL_P'+month.strftime('%y%m')+'_'+str(round(strike,3)),strike=strike,expiry=expiry,units=units,qty=qty)
        else:
            chain=self.options[day]
            months=chain[chain.actual_expiry>day][['contract_month','actual_expiry']].drop_duplicates().copy()
            months['distance']=(months.actual_expiry-target).abs().dt.days
            month=months.sort_values(['distance','actual_expiry'],ascending=[True,False]).iloc[0].contract_month
            ch=chain[chain.contract_month==month].copy()
            ch['distance']=(ch.strike/reference-1.02).abs().round(12)
            q=ch.sort_values(['distance','strike','contract']).iloc[0]
            assert q.settle>0 and np.isfinite(q.settle)
            p=dict(contract=q.contract,strike=float(q.strike),expiry=q.actual_expiry,units=units,qty=qty)
        p['mark']=self.quote(p,day)
        return p
    def step(self,day,state,im_pos,exit_open=False):
        if not self.enabled:return 0.,0.,0.
        pnl=cost=0.
        old=self.pos
        if old is not None:
            mark=self.quote(old,day,'open' if exit_open else 'settle')
            pnl+=old['units']*old['qty']*100*(mark-old['mark'])
            old['mark']=mark
        target=int(self.schedule.loc[day,'put_execution_target_qty']) if state=='future' and not exit_open else 0
        reset=day in self.resets
        oldqty=0 if old is None else old['qty']
        if old is not None and (target==0 or reset or target!=oldqty):
            fill=self.quote(old,day,'open' if exit_open else 'close')
            # Resize keeps the same contract unless monthly reset or entering from zero.
            sell=oldqty if target==0 or reset else max(oldqty-target,0)
            if sell:
                pnl+=old['units']*sell*100*(fill-old['mark'])
                cost+=old['units']*sell*100*float(self.futures.loc[(im_pos['contract'],day),'open' if exit_open else 'close'])*.0001
                self.events.append(dict(date=day,action='sell_protection',qty=sell,contract=old['contract'],fill=fill,scope=self.scope))
            if target==0 or reset:self.pos=None
            else:
                old['qty']=min(target,oldqty)
        if target>0:
            if self.pos is None:
                self.pos=self.select(day,im_pos['contract'],im_pos['units'],target)
                buy=target
            else:buy=max(target-self.pos['qty'],0)
            if buy:
                p=self.pos;fill=self.quote(p,day,'close')
                pnl+=p['units']*buy*100*(p['mark']-fill)
                cost+=p['units']*buy*100*float(self.futures.loc[(im_pos['contract'],day),'close'])*.0001
                p['qty']=target
                self.events.append(dict(date=day,action='buy_protection',qty=buy,contract=p['contract'],fill=fill,scope=self.scope,strike=p['strike']))
        capital=0 if self.pos is None else self.pos['units']*self.pos['qty']*100*self.pos['mark']
        return pnl,cost,capital

def coupled_run(base,options,futures,helper):
    # Isolated extension of the validated original state machine; original files remain untouched.
    s=inspect.getsource(original.run)
    s=s.replace('def run(base, options, futures, ratio):','def adapted(base, options, futures, ratio):')
    s=s.replace("if state == 'idle' and i > 0 and action == '':","if state == 'idle' and i > 0 and action == '' and bool(ENTRY_PERMISSION.get(day,False)):")
    s=s.replace("        action = ''", "        action = ''\n        protection_pnl=protection_cost=protection_capital=0.0")
    s=s.replace("            if pending == 'exit':", "            if pending == 'exit':\n                protection_pnl,protection_cost,protection_capital=helper.step(day,state,pos,True)\n                pnl+=protection_pnl;cost+=protection_cost")
    s=s.replace("        if cycle is not None:\n            cycle['realized_pnl'] += pnl-cost", "        if state=='future' and pending!='exit':\n            protection_pnl,protection_cost,protection_capital=helper.step(day,state,pos)\n            pnl+=protection_pnl;cost+=protection_cost\n        if cycle is not None:\n            cycle['realized_pnl'] += pnl-cost")
    s=s.replace("            if cycle['realized_pnl'] >= exit_cost:", "            if helper.pos is not None:\n                exit_cost+=helper.pos['units']*helper.pos['qty']*100*pos['mark']*.0001\n            if cycle['realized_pnl'] >= exit_cost:")
    s=s.replace("        equity += pnl-cost+cash", "        if helper.enabled:\n            cash-=protection_capital*CASH\n        equity += pnl-cost+cash")
    s=s.replace("'state':state,'action':action", "'state':state,'action':action,'protection_pnl':protection_pnl,'protection_cost':protection_cost,'protection_capital':protection_capital")
    namespace=dict(vars(original));namespace['helper']=helper;namespace['ENTRY_PERMISSION']=ENTRY_PERMISSION
    exec(compile(s,str(OUT/'coupled_state_machine.py'),'exec'),namespace)
    (OUT/'coupled_state_machine.py').write_text(s,encoding='utf-8')
    return namespace['adapted'](base,options,futures,.95)

def main():
    schedule,audit=policy.load_authoritative_local_state()
    early_path=ROOT/'outputs/im_mo_2015_valuation_reconstruction_v13/early_valuation_reconstruction.csv'
    early=pd.read_csv(early_path,parse_dates=['date']).set_index('date')
    assert early.certified_tier.eq(3).all() and not early.future_gov_row.any()
    effective=schedule.valuation_tier.where(schedule.valuation_score.notna(),schedule.date.map(early.certified_tier))
    valid=effective.notna() & schedule.momentum_120.notna()
    allowed_signal=valid & effective.le(1) & schedule.momentum_120.ge(0)
    global ENTRY_PERMISSION
    ENTRY_PERMISSION=pd.Series(allowed_signal.shift(1,fill_value=False).to_numpy(),index=schedule.date)
    schedule['protection_effective_valuation_tier']=effective
    target=np.maximum(effective.fillna(4).to_numpy(),schedule.mom120_floor_qty.to_numpy()).astype(int)
    schedule['put_execution_target_qty']=pd.Series(target).shift(1,fill_value=0).to_numpy()
    schedule['entry_permission']=ENTRY_PERMISSION.to_numpy()
    schedule.to_csv(OUT/'icm_quantity_schedule.csv',index=False)
    market,mb,mo,mf,checks,basis=full.build_inputs()
    rb=pd.read_csv(original.BASE,parse_dates=['date'])
    raw=pd.read_csv(original.OP,parse_dates=['date'])
    raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m')
    ro=prepare_options(raw,actual_expiry_map(raw,rb))
    rf=pd.read_csv(original.FU,parse_dates=['date'])
    summary=[];wide=[];parity={};ledger={};allc=[];alld=[];pe=[]
    for scope,b,o,f,m in [('real',rb,ro,rf,None),('model',mb,mo,mf,market)]:
        for enabled in (False,True):
            label=scope+('_icm_put102' if enabled else '_unprotected')
            h=Protection(scope,b,ro if scope=='real' else o,f,m,schedule,enabled)
            d,e,c=coupled_run(b,o,f,h)
            if not enabled:
                oldfolder=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_entry_valuation_0123_v2_entry_valuation_tier'
                ref=pd.read_csv(oldfolder/'daily.csv',parse_dates=['date'])
                ref=ref[ref.candidate==scope+'_val_le1_mom'].reset_index(drop=True)
                parity[scope]=float(abs(d.return_net-ref.return_net).max());assert parity[scope]<1e-12
            ledger[label]=float(abs((d.pnl-d.cost).sum()-c.realized_pnl.sum()));assert ledger[label]<1e-12
            d['candidate']=label;c['candidate']=label
            alld.append(d);allc.append(c);pe+=h.events
            d.to_csv(OUT/(label+'_daily.csv'),index=False)
            c.to_csv(OUT/(label+'_cycles.csv'),index=False)
            e.to_csv(OUT/(label+'_events.csv'),index=False)
            w={'candidate':label}
            for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
                cutoff=d.date.min() if years is None else d.date.max()-pd.DateOffset(years=years)
                available=cutoff>=d.date.min()
                sub=d[d.date>=cutoff] if available else d.iloc[:0]
                met=metrics(sub.return_net) if available else {k:'N/A' for k in ('ann_return','ann_vol','sharpe_repo','max_dd')}
                summary.append(dict(candidate=label,segment=segment,start=str(cutoff.date()),end=str(d.date.max().date()),rows=len(sub),**met))
                for key in ('ann_return','max_dd','sharpe_repo'):w[key+'_'+segment]=met[key]
            wide.append(w)
            print(label,metrics(d.return_net),flush=True)
    pd.DataFrame(summary).to_csv(OUT/'scan_summary.csv',index=False)
    pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    pd.DataFrame(pe).to_csv(OUT/'protection_trades.csv',index=False)
    pd.concat(alld).to_csv(OUT/'daily.csv',index=False)
    pd.concat(allc).to_csv(OUT/'cycles.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    paths=[early_path,Path(__file__),Path(policy.__file__),ROOT/'im_put_policy.py',original.BASE,original.OP,original.FU,Path(full.__file__),Path(original.__file__)]
    meta.update(data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},quantity_audit=audit,baseline_parity=parity,ledger_errors=ledger,
      cost_model={'one_way_notional':.0001,'cash_annual':.03,'risk_buffer':.30,'long_put_capital':'deduct premium mark from cash'},
      unavailable_segments={k:{'last_10y':'Real IM/MO starts 2022-07-22','last_5y':'Real IM/MO starts 2022-07-22'} for k in ('real_unprotected','real_icm_put102')},
      protection_policy='Current core per IM unit max(valuation tier, strict MOM120<0 floor3), max4. Current102% policy applied retrospectively, not historical forward ledger. No momentum Put. Enter/rebalance at close using previous signal, monthly third-Friday reset. Reference actual held IM close; nearest listed expiry to execution+3 calendar months. No quantity cap to1x.',
      limitations='Real zero-volume options traded at official settlement estimate; full model BS continuous protection strike, expiry-close proxy and ex-post10.33% carry. IM enters next open, protection first close: overnight/intraday gap not protected. Protective exit at IM next-open exit, no intraday margins/forced liquidation. Original files and caches unchanged; dirty worktree preserved.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record='''# 95%卖Put实值转IM后移植当前ICM核心Put v1

## Data Snapshot
模型2015-04-16至2026-08-14、真实2022-07-22至2026-08-14。当前102%规则回溯研究，不代表历史真实账本。原始模型与真实≤1加MOM120非负入场路径分别重放，逐日parity<1e-12；逐轮/逐日损益重组<1e-12。

## Implementation Anchor
冻结基础状态机保留；新文件以独立适配加入耦合保护。每1张接入IM配置max(估值档,MOM120<0则3)张MO，上限4；原0.5核心仓系数按实际接入IM同比例换算，不额外叠加动量Put/网格/Call。买Put条件来自im_mainline_v1_1.load_authoritative_local_state及build_target_schedule，T信号次日收盘执行。接入当日开盘买IM，首日收盘才买保护。月度第三周五维护，期限执行+3月最近挂牌到期日，K=1.02*所持IM收盘；非月度只调量，零进入/重置重新选约。真实最近行权价不做流动性跳档；零成交估计按结算价。模型按既有理论引擎连续目标K定价。

## Cost and Execution
原成本、期货月展期、现金3%保留；新增保护单边1bp名义成本，期权持有市值从现金扣除。回本计入该轮卖Put、IM、买Put累计损益与全部交易成本，排除现金利息；收盘触发、次日开盘一起卖IM及保护Put。真正退出成交可能低于回本价。保护不得预先作用于最初卖Put阶段，也不得改变原卖Put规则。

## Stability
仅一个既有ICM政策移植，不挑参数。真实无动态保证金/盘口容量，估计成交披露；模型贴水事后校准、权利金代理，不视为可执行收益。5Y/10Y真实段N/A。保护数量最多4MO/IM，可产生超额保险及反弹拖累；不自动限量改变原规则。

## Decision
research_only_no_promotion

## Commands
python -X utf8 research_im_short95_le1_mom_then_put102_v3.py

## Results
'''
    record+='\nV2审计修复：模型开盘退出保护Put按spot_open/rate_open/dividend_open/sigma_open计算，V1误用了收盘输入；旧输出保留，仅作已知缺陷历史证据。\n\n'
    record+='\nV3预注册：仅估值档≤1且MOM120>=0才允许新开95%卖Put。开仓条件取前收盘；已持有不因许可改变退出。转IM后移植当前核心0..4MO/IM、102%保护。早期估值使用已认证v13档3，未知目标保守4档，仅研究标记；入场缺值关闭。基线为前次real/model_val_le1_mom。绝对动量明确为全收益MOM120，非多因子短期动量。\n\n'
    record+=pd.DataFrame(summary).to_string(index=False)+'\n\n'+pd.concat(allc).to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as log:log.write('\npython -X utf8 research_im_short95_le1_mom_then_put102_v3.py\n')
    print('parity',parity,'ledger',ledger,flush=True)

if __name__=='__main__':main()
