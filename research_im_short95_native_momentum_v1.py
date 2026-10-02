from pathlib import Path
import inspect,json,hashlib
import numpy as np
import pandas as pd
import im_mainline_v1_3 as native
import research_im_short_put_recovery_atm_real_v1 as original
import research_im_short_put_recovery_atm_full_model_v1 as full
from im_put_maturity_valuation_tiers_v3 import prepare_options,actual_expiry_map,metrics
OUT=Path('quant_param_scan_runs/20260914_im_short95_native_momentum_v1')
SOURCE=Path('quant_param_scan_runs/20260914_im_original_core_put_vs_short95_v2/daily.csv.gz')
WEIGHTS=Path('quant_param_scan_runs/20260908_im_put_revalidation_layer1_v1/momentum_weights.csv.gz')

def main():
    (OUT/'spec.json').write_text(json.dumps(dict(strike=.95,gate='native momentum_execution_weight > 0 (already T+1 shifted)',position='binary 1x per active cycle, not native fractional momentum sizing',allocation=.5,hold='expiry then IM monthly until cycle trading P&L net costs breakeven; gates only new entries',filters='momentum leg no valuation/MOM120; retained baseline valuation0/1 AND MOM120>=0',cutoff='2026-08-14'),indent=2),encoding='utf-8')
    weights=pd.read_csv(WEIGHTS,parse_dates=['date'])
    weights=weights[weights.date<=pd.Timestamp('2026-08-14')].reset_index(drop=True)
    assert np.array_equal(weights.momentum_execution_weight.iloc[1:],weights.momentum_signal_target.iloc[:-1])
    rebuilt=native.build_momentum_schedule(pd.read_csv(native.CSI1000_OHLCV_PATH,parse_dates=['date']))
    chk=weights.merge(rebuilt[['date','momentum_execution_weight']],on='date',suffixes=('_saved','_rebuilt'),validate='one_to_one')
    assert len(chk)==len(weights)
    assert np.array_equal(chk.momentum_execution_weight_saved,chk.momentum_execution_weight_rebuilt)
    allowed=weights.set_index('date').momentum_execution_weight.gt(0)
    weights['new_entry_allowed']=weights.momentum_execution_weight.gt(0)
    weights.to_csv(OUT/'momentum_permissions.csv',index=False)
    s=inspect.getsource(original.run).replace('def run(base, options, futures, ratio):','def gated_run(base, options, futures, ratio):')
    s=s.replace("if state == 'idle' and i > 0 and action == '':","if state == 'idle' and i > 0 and action == '' and bool(allowed.get(day, False)):")
    (OUT/'gated_state_machine.py').write_text(s,encoding='utf-8')
    market,mb,mo,mf,checks,basis=full.build_inputs()
    rb=pd.read_csv(original.BASE,parse_dates=['date']);rf=pd.read_csv(original.FU,parse_dates=['date'])
    raw=pd.read_csv(original.OP,parse_dates=['date']);raw['contract_month']=pd.to_datetime('20'+raw.contract.str[2:6],format='%Y%m');ro=prepare_options(raw,actual_expiry_map(raw,rb))
    prior=pd.read_csv(SOURCE,parse_dates=['date'])
    ds=[];cs=[];es=[];audit={};summary=[];wide=[];exposure=[];unavailable={}
    for area,b,o,f in [('real',rb,ro,rf),('model',mb,mo,mf)]:
        ns=dict(vars(original));ns['allowed']=allowed;exec(compile(s,str(OUT/'gated_state_machine.py'),'exec'),ns)
        d,e,c=ns['gated_run'](b,o,f,.95);label=area+'_native_momentum'
        assert c.entry_date.map(pd.Timestamp).map(allowed).fillna(False).all()
        err=abs((d.pnl-d.cost).sum()-c.realized_pnl.sum());assert err<1e-12
        # Ungated replay reconciles unchanged implementation to saved historical control.
        control,_,_=original.run(b,o,f,.95)
        saved=pd.read_csv((original.OUT if area=='real' else full.OUT)/'daily.csv')
        saved=saved[saved.candidate=='put_0.95'].reset_index(drop=True)
        parity=float(abs(control.return_net-saved.return_net).max());assert parity<1e-12
        audit[area]=dict(ledger_error=float(err),ungated_parity=parity,native_signal_rebuild_exact=True)
        d['candidate']=label;c['candidate']=label;e['candidate']=label;cs.append(c);es.append(e)
        base=prior[prior.candidate==area+'_short95_le1_mom'][['date','return_net']].reset_index(drop=True)
        assert d.date.reset_index(drop=True).equals(base.date)
        baseline=base.copy();baseline['candidate']=area+'_valuation_baseline'
        combined=base.copy();combined['return_net']=.5*base.return_net+.5*d.return_net;combined['candidate']=area+'_combined50'
        initial=base.copy();n=.5*(1+base.return_net).cumprod()+.5*(1+d.return_net).cumprod();initial['return_net']=n/np.r_[1.,n.iloc[:-1]]-1;initial['candidate']=area+'_combined50_initial'
        ds.extend([d,baseline,combined,initial])
        exposure.append(dict(candidate=label,assignments=int(c.assignment_date.fillna('').ne('').sum()),im_days=int((d.state=='future').sum()),put_days=int((d.state=='put').sum()),cycles=len(c),unclosed=int((~c.closed).sum()),longest_closed_im_days=float(c.recovery_days.max()) if c.recovery_days.notna().any() else 0,correlation_with_baseline=float(np.corrcoef(d.return_net,base.return_net)[0,1])))
    daily=pd.concat(ds,ignore_index=True)
    for label,g in daily.groupby('candidate'):
        w={'candidate':label}
        for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1),('common_real_period',0)]:
            start=pd.Timestamp('2022-07-22') if years==0 else g.date.max()-pd.DateOffset(years=years) if years else g.date.min()
            sub=g[g.date>=start];available=start>=g.date.min()
            m=metrics(sub.return_net) if available else {k:'N/A' for k in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            if not available:unavailable.setdefault(label,{})[segment]='Real history insufficient'
            summary.append(dict(candidate=label,segment=segment,start=str(start.date()),end=str(g.date.max().date()),rows=len(sub) if available else 0,**m))
            for k,v in m.items():w[k+'_'+segment]=v
        wide.append(w)
    daily.to_csv(OUT/'daily.csv.gz',index=False);pd.concat(cs).to_csv(OUT/'cycles.csv',index=False);pd.concat(es).to_csv(OUT/'events.csv',index=False);pd.DataFrame(exposure).to_csv(OUT/'exposure.csv',index=False)
    result=pd.DataFrame(summary);result.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(data_snapshot={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [SOURCE,WEIGHTS,native.CSI1000_OHLCV_PATH,original.BASE,original.OP,original.FU,Path(__file__),Path(original.__file__),Path(full.__file__)]},audit=audit,basis_calibration=basis,cost_model=dict(one_way_notional=.0001,reserve=.3,cash_annual=.03),unavailable_segments=unavailable,baseline={'candidate':'real_valuation_baseline'},limitations='Model ex-post calibrated carry, BS proxy, synthetic liquidity, expiry-close proxy, sigma_open timing uncertainty. No bidask, margin liquidation, integer contracts or additional daily allocation costs. No production change. Binary entry permission not native graded futures exposure.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record='''# 原1.3动量开仓许可卖95%Put及50/50组合
## Data Snapshot
真实2022-07-22至2026-08-14；模拟2015-04-16至2026-08-14。非最新信号；共同期及标准近窗见CSV，真实5/10年不足N/A。
## Implementation Anchor
原im_mainline_v1_3.build_momentum_schedule含bias动量、20日绝对动量、量能和过热处理；重新计算与已验收schedule逐日完全一致。execution_weight已经T+1移位，不再移位；>0仅决定是否新开卖Put，不复刻原动量分档仓位。独立动量腿无估值、无额外MOM120约束。
卖下一月95%OTM Put，前收盘参考次日开盘执行；已卖Put不因信号关闭而平仓。实值现金结算次日开盘转IM，每月滚动，整轮交易盈亏含费用、不含利息达到回本后次日开盘退出；不买保护Put。无网格/Call。
## Cost and Execution
沿用原状态机单边1bp、每1倍期货30%缓冲、现金年化3%。无盘口、追保/强平、整数合约。50/50为两套独立净收益资金组合，主表每日恢复权重、initial对照初始分配之后漂移；无额外资金调整费。估值腿仍为0/1档且MOM120>=0基准，不是纯估值腿。
## Stability
固定95%和50/50，不优化参数；模型含事后平均贴水校准、代理期权及开盘sigma时点局限，不为独立OOS。完整逐轮及未平腿对账、未过滤旧基线复现、原动量重建与入场许可校验通过；用户已有改动及生产保持不变。
## Decision
research_only_no_promotion；上一OTM提前滚仓路线按用户决定停止。
## Commands
python -X utf8 research_im_short95_native_momentum_v1.py
## Results
'''+result[result.segment=='full'].to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_im_short95_native_momentum_v1.py\n')
    print(result[result.segment=='full'].to_string(index=False));print(pd.DataFrame(exposure).to_string(index=False))
if __name__=='__main__':main()
