from pathlib import Path
import inspect,json,hashlib
import pandas as pd
import numpy as np
import im_mainline_v1_1 as policy
import research_im_short_put_recovery_atm_real_v1 as original
import research_im_short_put_recovery_atm_full_model_v1 as full
from im_put_maturity_valuation_tiers_v3 import metrics,prepare_options,actual_expiry_map
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_entry_gate_v1_entry_permission'

def main():
    state,audit=policy.load_authoritative_local_state()
    permission=pd.DataFrame({'date':state.date,'baseline':True,
      'val0':state.valuation_score.notna() & state.valuation_tier.eq(0),'mom120_nonnegative':state.momentum_120.ge(0)})
    permission['both']=permission.val0 & permission.mom120_nonnegative
    permission.iloc[:,1:]=permission.iloc[:,1:].shift(1,fill_value=False)
    permission.to_csv(OUT/'entry_permissions.csv',index=False)
    permission=permission.set_index('date')
    market,mb,mo,mf,checks,basis=full.build_inputs()
    rb=pd.read_csv(original.BASE,parse_dates=['date']);rf=pd.read_csv(original.FU,parse_dates=['date'])
    raw=pd.read_csv(original.OP,parse_dates=['date']);raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m')
    ro=prepare_options(raw,actual_expiry_map(raw,rb))
    s=inspect.getsource(original.run).replace('def run(base, options, futures, ratio):','def gated_run(base, options, futures, ratio):')
    s=s.replace("if state == 'idle' and i > 0 and action == '':", "if state == 'idle' and i > 0 and action == '' and bool(allowed.get(day, False)):")
    (OUT/'gated_state_machine.py').write_text(s,encoding='utf-8')
    summaries=[];wides=[];daily=[];cycles=[];parity={};ledger={};exposure=[]
    for scope,b,o,f in [('real',rb,ro,rf),('model',mb,mo,mf)]:
        for gate in ['baseline','val0','mom120_nonnegative','both']:
            allowed=permission[gate]
            namespace=dict(vars(original));namespace['allowed']=allowed
            exec(compile(s,str(OUT/'gated_state_machine.py'),'exec'),namespace)
            d,e,c=namespace['gated_run'](b,o,f,.95)
            label=scope+'_'+gate
            if gate=='baseline':
                ref=pd.read_csv((original.OUT if scope=='real' else full.OUT)/'daily.csv')
                ref=ref[ref.candidate=='put_0.95'].reset_index(drop=True)
                parity[scope]=float(abs(d.return_net-ref.return_net).max());assert parity[scope]<1e-12
            entries=c.entry_date.map(pd.Timestamp)
            assert entries.map(allowed).fillna(False).all()
            ledger[label]=float(abs((d.pnl-d.cost).sum()-c.realized_pnl.sum()));assert ledger[label]<1e-12
            d['candidate']=label;c['candidate']=label;e['candidate']=label
            daily.append(d);cycles.append(c)
            e.to_csv(OUT/(label+'_events.csv'),index=False)
            assigned=c.assignment_date.fillna('').ne('')
            exposure.append(dict(candidate=label,cycles=len(c),assignments=int(assigned.sum()),im_holding_days=int(d.state.eq('future').sum()),im_time_fraction=float(d.state.eq('future').mean()),idle_fraction=float(d.state.eq('idle').mean()),longest_closed_im_calendar_days=float(c.recovery_days.max()) if 'recovery_days' in c and c.recovery_days.notna().any() else 0,unclosed_cycles=int((~c.closed).sum())))
            w={'candidate':label}
            for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
                cutoff=d.date.min() if years is None else d.date.max()-pd.DateOffset(years=years)
                sub=d[d.date>=cutoff] if cutoff>=d.date.min() else d.iloc[:0]
                m=metrics(sub.return_net) if len(sub) else {k:'N/A' for k in ('ann_return','ann_vol','sharpe_repo','max_dd')}
                summaries.append(dict(candidate=label,segment=segment,start=str(cutoff.date()),end=str(d.date.max().date()),rows=len(sub),**m))
                for k in ('ann_return','max_dd','sharpe_repo'):w[k+'_'+segment]=m[k]
            wides.append(w)
            print(label,metrics(d.return_net),flush=True)
    pd.DataFrame(summaries).to_csv(OUT/'scan_summary.csv',index=False)
    pd.DataFrame(wides).to_csv(OUT/'window_metrics.csv',index=False)
    pd.DataFrame(exposure).to_csv(OUT/'exposure.csv',index=False)
    pd.concat(daily).to_csv(OUT/'daily.csv',index=False)
    pd.concat(cycles).to_csv(OUT/'cycles.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'))
    paths=[Path(__file__),OUT/'spec.md',Path(policy.__file__),original.BASE,original.OP,original.FU,Path(original.__file__),Path(full.__file__)]
    meta.update(data_snapshot={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},quantity_state_audit=audit,model_checks=checks,basis_calibration=basis,
      baseline_parity=parity,ledger_errors=ledger,entry_permission_assertion='All actual entries match previous-session permission',
      cost_model={'one_way_notional':.0001,'cash_annual':.03,'risk_buffer':.30},
      unavailable_segments={label:{'last_10y':'Real IM/MO starts2022-07-22','last_5y':'Real IM/MO starts2022-07-22'} for label in ['real_baseline','real_val0','real_mom120_nonnegative','real_both']},
      assumptions='Only new entries gated; existing puts/futures unchanged. Signals shifted before slicing real interval; valuation tier0 AND nonmissing valuation_score, TRI MOM120>=0. Missing early valuation defaults to tier0 in original policy; here fail closed, never treat it as cheap. No protective Put, no Call/grid. Cycle normalized units fixed, monthly roll and breakeven exclude cash as original.',
      missing_valuation_days=int(state.valuation_score.isna().sum()),first_valid_valuation=str(state.loc[state.valuation_score.notna(),'date'].min().date()),
      limitations='Model ex-post10.33% carry, BS sigma proxy, synthetic liquidity, expiry daily-close proxy. Real open/close proxies; no bidask or dynamic margin/forced-liquidation. cutoff2026-08-14, historical not current signal. Three preselected gates, no OOS/neighbor parameter validation. No cache writes, original files and dirty user changes retained.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record='''# 95%卖Put入场许可研究 v1

## Data Snapshot
真实2022-07-22至2026-08-14；全期统一模型2015-04-16至2026-08-14。非当前信号，不混合模型/真实。sources和SHA256、状态生成审计见scan_meta.json。

## Implementation Anchor
沿用上次无保护95%卖Put状态机，仅新开仓许可加入上一交易日ICM估值/MOM120：baseline无过滤；val0=两估值轴最大档0；mom120_nonnegative=MOM120>=0；both同时满足。原长期回本持有、期货月展期及点值全部保留，无额外买Put。全收益MOM120并非当前多因子短期动量仓的全部规则。估值档0比仅暂停高档严格，这是预注册研究假设，非最优参数。已持Put/IM不因新信号关闭。信号缺失失败关闭。

## Cost and Execution
前收盘许可/指数95%目标、次日开盘卖下月Put；实值现金结算次日开盘转IM，收盘回本次日开盘退出。单边1bp，30%缓冲，其余现金3%计息，空仓全额。回本含完整交易损益/成本、不含现金。成交缺口、模型挂牌/期限/定价限制沿用原版。基线逐日parity与逐日/逐轮损益均<1e-12，每个实际入场与前一日许可逐笔校验通过。

## Stability
四条预注册路径，未扫阈值、不称全局最佳，未做独立OOS。禁止研究自动晋级；收益改善不能替代资本占用及账户生存验证。无追保/强平/容量模型；真实5Y/10Y不足N/A。无缓存写入或旧代码修改。

## Decision
research_only_no_promotion

## Commands
python -X utf8 research_im_short95_entry_gate_v1.py

## Results
'''
    record+='\n估值门控额外要求valuation_score非缺失；2015早期目标表默认0不当作合理估值。首次有效估值为2015-10-19，此前val0/both关闭。因此避免早期交易含数据缺失拒入贡献，不全部归因于有效估值择时。现有早期重建支持2015高档，但本版不另接入该重建改变原估值生成流程。\n\n'
    record+=pd.DataFrame(summaries).to_string(index=False)+'\n\n'+pd.DataFrame(exposure).to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as log:log.write('\npython -X utf8 research_im_short95_entry_gate_v1.py\n')
    print(pd.DataFrame(exposure).to_string(index=False),flush=True)

if __name__=='__main__':main()
