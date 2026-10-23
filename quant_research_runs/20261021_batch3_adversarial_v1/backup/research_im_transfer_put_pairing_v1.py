from pathlib import Path
import inspect, json, hashlib
import numpy as np
import pandas as pd
import im_mainline_v1_1 as policy
import research_im_short_put_recovery_atm_real_v1 as original
import research_im_short_put_recovery_atm_full_model_v1 as full
from research_im_short95_then_icm_put102_audit_v2 import Protection
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, prepare_options, metrics

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'quant_param_scan_runs/20260914_icim_im_transfer_put_pairing_v1_transfer_recovery_protection_timing'

class FloorProtection(Protection):
    """Immediate insurance: after transfer, retain at least three MO per IM."""
    def step(self, day, state, im_pos, exit_open=False):
        if state == 'future' and not exit_open:
            original_target = int(self.schedule.loc[day, 'put_execution_target_qty'])
            self.schedule.loc[day, 'put_execution_target_qty'] = max(original_target, 3)
            try:
                return super().step(day, state, im_pos, exit_open)
            finally:
                self.schedule.loc[day, 'put_execution_target_qty'] = original_target
        return super().step(day, state, im_pos, exit_open)

def paired_run(base, options, futures, helper, fixed_exits):
    """Validated short-Put state machine, with exits locked to the no-Put path."""
    source = inspect.getsource(original.run)
    source = source.replace('def run(base, options, futures, ratio):', 'def adapted(base, options, futures, ratio):')
    source = source.replace("        action = ''", "        action = ''\n        protection_pnl=protection_cost=protection_capital=0.0\n        if state=='future' and cycle is not None and fixed_exits.get(str(cycle['entry_date'])) == day:\n            pending='exit'")
    source = source.replace("            if pending == 'exit':", "            if pending == 'exit':\n                protection_pnl,protection_cost,protection_capital=helper.step(day,state,pos,True)\n                pnl+=protection_pnl;cost+=protection_cost")
    source = source.replace("        if cycle is not None:\n            cycle['realized_pnl'] += pnl-cost", "        if state=='future' and pending!='exit':\n            protection_pnl,protection_cost,protection_capital=helper.step(day,state,pos)\n            pnl+=protection_pnl;cost+=protection_cost\n        if cycle is not None:\n            cycle['realized_pnl'] += pnl-cost")
    # Do not allow modified P/L to determine exits; fixed_exits controls the state boundary.
    start = source.index("        if state == 'future':\n            exit_cost")
    end = source.index("        # Conservative constant", start)
    source = source[:start] + source[end:]
    source = source.replace("        equity += pnl-cost+cash", "        if helper.enabled:\n            cash-=protection_capital*original.CASH\n        equity += pnl-cost+cash")
    source = source.replace("'state':state,'action':action", "'state':state,'action':action,'protection_pnl':protection_pnl,'protection_cost':protection_cost,'protection_capital':protection_capital")
    namespace = dict(vars(original)); namespace.update(helper=helper, fixed_exits=fixed_exits, original=original)
    exec(compile(source, str(OUT/'paired_state_machine.py'), 'exec'), namespace)
    (OUT/'paired_state_machine.py').write_text(source, encoding='utf-8')
    return namespace['adapted'](base, options, futures, .95)

def get_inputs(scope):
    if scope == 'real':
        base = pd.read_csv(original.BASE, parse_dates=['date'])
        raw = pd.read_csv(original.OP, parse_dates=['date'])
        raw['contract_month'] = pd.to_datetime('20' + raw.contract.str[2:6], format='%Y%m')
        return base, prepare_options(raw, actual_expiry_map(raw, base)), pd.read_csv(original.FU, parse_dates=['date']), None
    market, base, options, futures, _, _ = full.build_inputs()
    return base, options, futures, market

def evaluate(label, daily):
    rows=[]
    for segment, years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
        cut = daily.date.min() if years is None else daily.date.max()-pd.DateOffset(years=years)
        ok = cut >= daily.date.min(); sub=daily[daily.date>=cut] if ok else daily.iloc[:0]
        m=metrics(sub.return_net) if ok else {x:'N/A' for x in ('ann_return','ann_vol','sharpe_repo','max_dd')}
        rows.append(dict(candidate=label,segment=segment,start=str(cut.date()),end=str(daily.date.max().date()),rows=len(sub),**m))
    return rows

def main():
    schedule, policy_audit = policy.load_authoritative_local_state()
    output=[]; all_daily=[]; all_cycles=[]; checks={}; events=[]
    for scope in ('real','model'):
        base, options, futures, market = get_inputs(scope)
        source_folder = original.OUT if scope=='real' else full.OUT
        fixed = pd.read_csv(source_folder/'cycles.csv')
        fixed = fixed[(fixed.candidate=='put_0.95') & fixed.assignment_date.notna() & fixed.exit_date.notna()]
        exit_map = dict(zip(fixed.entry_date.astype(str), pd.to_datetime(fixed.exit_date)))
        for label, cls, enabled in [('no_put', Protection, False), ('icm_gated', Protection, True), ('immediate_floor3', FloorProtection, True)]:
            helper = cls(scope, base, options, futures, market, schedule.copy(), enabled)
            daily, event, cycles = paired_run(base, options, futures, helper, exit_map)
            key=scope+'_'+label
            daily['candidate']=key; cycles['candidate']=key
            # Every completed recovery episode must now exit on the unchanged baseline exit date.
            assigned = cycles.assignment_date.astype(str).str.strip().ne('') & cycles.assignment_date.notna()
            complete=cycles[cycles.closed & assigned]
            expected=complete.entry_date.map(exit_map)
            assert expected.notna().all()
            assert (pd.to_datetime(complete.exit_date).to_numpy()==expected.to_numpy()).all()
            error=float(abs((daily.pnl-daily.cost).sum()-cycles.realized_pnl.sum()))
            assert error<1e-12; checks[key+'_ledger_error']=error
            output.extend(evaluate(key,daily)); all_daily.append(daily); all_cycles.append(cycles)
            events.extend(helper.events)
            daily.to_csv(OUT/(key+'_daily.csv'),index=False); cycles.to_csv(OUT/(key+'_cycles.csv'),index=False); event.to_csv(OUT/(key+'_events.csv'),index=False)
    summary=pd.DataFrame(output); summary.to_csv(OUT/'scan_summary.csv',index=False)
    wide=summary.pivot(index='candidate',columns='segment',values=['ann_return','max_dd']); wide.columns=['_'.join(c) for c in wide.columns]; wide.reset_index().to_csv(OUT/'window_metrics.csv',index=False)
    all_daily=pd.concat(all_daily); all_cycles=pd.concat(all_cycles)
    all_daily.to_csv(OUT/'daily.csv',index=False); all_cycles.to_csv(OUT/'cycles.csv',index=False); pd.DataFrame(events).to_csv(OUT/'protection_trades.csv',index=False)
    # Cohort-only drawdown: start each completed assigned episode at 1, so history before assignment cannot obscure the hedge result.
    cohorts=[]
    for key,d in all_daily.groupby('candidate'):
        scope=key.split('_',1)[0]; assigned=all_cycles.assignment_date.astype(str).str.strip().ne('') & all_cycles.assignment_date.notna(); c=all_cycles[(all_cycles.candidate==key)&assigned&all_cycles.exit_date.notna()]
        for r in c.itertuples(index=False):
            z=d[(d.date>=pd.Timestamp(r.assignment_date))&(d.date<=pd.Timestamp(r.exit_date))]
            nav=(1+z.return_net).cumprod(); cohorts.append(dict(candidate=key,entry_date=r.assignment_date,exit_date=r.exit_date,days=len(z),total_return=nav.iloc[-1]-1,max_dd=(nav/nav.cummax()-1).min()))
    cohort=pd.DataFrame(cohorts); cohort.to_csv(OUT/'cohort_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    source_paths=[Path(__file__),Path(policy.__file__),Path(original.__file__),Path(full.__file__),original.BASE,original.OP,original.FU]
    meta.update(policy_audit=policy_audit,fixed_exit_rule='Each completed assigned cycle uses exactly the no-Put exit date from the earlier 95% recovery run; Put P/L never changes it.',checks=checks,immediate_floor_rule='At and after transfer use max(ICM core execution quantity, 3) MO per IM; this is a deliberately forced-insurance counterfactual, not the current ICM policy.',gated_rule='Current ICM core quantity, including legitimate zero target; no momentum Put or grid/Call leg.',data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},limitations='Model uses theoretical MO and ex-post carry as prior runs; real history begins 2022-07-22. This is diagnostic and does not change production.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record='''# 转入IM后的固定路径Put保护配对测试 v1

每轮IM转入及退出日期锁定为前次95%卖Put恢复策略的无Put路径。无Put、ICM条件触发、转入即最低3张MO三条路径的IM交易日、IM合约、IM数量和退出日一致；因此Put不会通过改变回本退出或下一轮卖Put改变比较样本。ICM条件版本严格允许0张；即时版本是强制保险反事实，不是现行生产规则。两者Put均按102%目标、约3个月、月度重置。

真正检验的是转入后的同一IM持有期收益与最大回撤，不把持仓前的短Put损失混入cohort_metrics.csv。真实段期权从2022-07-22开始，零成交边使用结算估计；模型为理论期权及贴水代理。所有逐日/逐轮账本重组与锁定退出日检查通过。

Decision: research_only_no_promotion
Stability: paired_path_answer_not_general_oos
'''
    record += '\n\n'+summary.to_string(index=False)+'\n\n'+cohort.to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_im_transfer_put_pairing_v1.py\n')
    print(summary.to_string(index=False)); print(cohort.groupby('candidate')[['total_return','max_dd']].mean().to_string())

if __name__=='__main__': main()
