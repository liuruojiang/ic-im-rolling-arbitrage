from pathlib import Path
import inspect,json,hashlib
import pandas as pd
import numpy as np
import ic_mainline_v1_3 as native
import research_ic_short95_put_to_ic_recovery_v1 as real
import research_ic_short95_put_to_ic_recovery_full_model_v1 as model
import ic_510500_put_proxy_validation_v1 as proxy

OUT=Path('quant_param_scan_runs/20260914_ic_short95_native_momentum_v1')

def gated(module,allowed):
    s=inspect.getsource(module.run)
    old='if state == "idle" and i > 0 and not action:'
    assert old in s
    s=s.replace(old,old[:-1]+' and bool(allowed.get(day, False)):')
    s=s.replace('daily, cycle_frame = pd.DataFrame(rows), pd.DataFrame(cycles)','daily, cycle_frame = pd.DataFrame(rows), pd.DataFrame(cycles)\n    if "open_cycle_pnl" not in cycle_frame:\n        cycle_frame["open_cycle_pnl"] = np.nan')
    (OUT/(module.__name__+'_gated.py')).write_text(s,encoding='utf-8')
    ns=dict(vars(module));ns['allowed']=allowed;exec(compile(s,str(OUT/'gated.py'),'exec'),ns)
    return ns['run']

def main():
    (OUT/'spec.json').write_text(json.dumps(dict(ratio=.95,gate='IC v1.3 execution_weight > 0, already shifted T+1',no_valuation=True,position='binary full sleeve, not graded momentum exposure',recovery='physical ETF assignment then next open conversion to IC; monthly roll until full cycle net trading breakeven; gate new entry only'),indent=2),encoding='utf-8')
    weights=native.build_momentum_schedule(pd.read_csv(native.CSI500_OHLCV_PATH,parse_dates=['date']))
    assert np.array_equal(weights.momentum_execution_weight.iloc[1:],weights.momentum_signal_target.iloc[:-1])
    weights.to_csv(OUT/'native_momentum_schedule.csv.gz',index=False)
    allowed=weights.set_index('date').momentum_execution_weight.gt(0)
    active,market,futures=model.model_inputs()
    ds=[];cs=[];es=[];summary=[];wide=[];audit={};unavailable={};exposure=[]
    for area,module,args in [('real',real,()),('model',model,('post_assignment_ic','ic_future',active,market,futures))]:
        for name,permission in [('native_momentum',allowed),('ungated',pd.Series(True,index=allowed.index))]:
            d,e,c,a=gated(module,permission)(*args)
            label=area+'_'+name
            assert pd.to_datetime(c.entry_date).map(permission).fillna(False).all()
            closed=c.closed.fillna(False)
            error=abs((d.pnl-d.cost).sum()-c.loc[closed,'realized_pnl'].sum()-c.loc[~closed,'open_cycle_pnl'].fillna(0).sum());assert error<1e-12
            assert np.max(abs(d.nav-(1+d.return_net).cumprod()))<1e-12
            if name=='ungated':
                saved=pd.read_csv(real.OUT/'daily.csv.gz' if area=='real' else model.OUT/'post_assignment_ic_daily.csv.gz',parse_dates=['date'])
                assert d.date.reset_index(drop=True).equals(saved.date.reset_index(drop=True))
                parity=float(np.max(abs(d.return_net.to_numpy()-saved.return_net.to_numpy())));assert parity<1e-12
                a['ungated_parity']=parity
            a['ledger_error']=float(error);audit[label]=a
            d['candidate']=label;e['candidate']=label;c['candidate']=label;ds.append(d);cs.append(c);es.append(e)
            assigned='physical_assignment_date' if 'physical_assignment_date' in c else 'assignment_date'
            exposure.append(dict(candidate=label,assignments=int(c[assigned].notna().sum()),ic_days=int((d.state=='ic_future').sum()),put_days=int((d.state=='short_put').sum()),cycles=len(c),unclosed=int((~closed).sum()),longest_closed_ic_days=float(c.recovery_days.max()) if 'recovery_days' in c and c.recovery_days.notna().any() else 0))
            w={'candidate':label}
            for segment,years in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
                start=d.date.max()-pd.DateOffset(years=years) if years else d.date.min();ok=start>=d.date.min();sub=d[d.date>=start]
                m=proxy.metrics(sub.return_net) if ok else {k:'N/A' for k in ['total_return','ann_return','ann_vol','sharpe_repo','max_dd']}
                if not ok:unavailable.setdefault(label,{})[segment]='Insufficient real history'
                summary.append(dict(candidate=label,segment=segment,start=str(start.date()),end=str(d.date.max().date()),rows=len(sub) if ok else 0,**m))
                for k,v in m.items():w[k+'_'+segment]=v
            wide.append(w)
    pd.concat(ds).to_csv(OUT/'daily.csv.gz',index=False);pd.concat(cs).to_csv(OUT/'cycles.csv',index=False);pd.concat(es).to_csv(OUT/'events.csv',index=False);pd.DataFrame(exposure).to_csv(OUT/'exposure.csv',index=False)
    s=pd.DataFrame(summary);s.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(audit=audit,unavailable_segments=unavailable,baseline={'candidate':'real_ungated'},cost_model=dict(one_way_notional=.0001,reserve=.3,cash_annual=.03),data_snapshot={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [Path(__file__),Path(real.__file__),Path(model.__file__),Path(native.__file__),native.CSI500_OHLCV_PATH]},limitations='Real ETF Put to ETF physical assignment to IC differs from cash-settled MO. Synthetic ETF/index option pricing for model; historical IC prices retained. Existing strike/snapshot/expiry and liquidity assumptions retained. No forced liquidation, bidask or integer constraints. Not exact same common sample as IM; no direct ranking.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record='''# IC原1.3动量开仓许可卖95%Put
## Data Snapshot
日期/行数见结果；模型使用理论Put及指数ETF代理与真实IC期货，不混同全部真实行情。真实510500 Put历史较短，5/10年N/A。
## Implementation Anchor
ic_mainline_v1_3.build_momentum_schedule从2007完整历史生成，bias110/24、20日绝对动量分档、基础NAV回撤防御，IC不加IM量能/过热规则。execution_weight>0为二元新入场许可，已移位T+1不重复移位，不复刻分档仓位。95%最近OTM下月510500 Put；信号关闭不平已有Put/IC；行权取得ETF，下一开盘转换IC，按既有月展期，整轮扣费交易盈亏回本次日开盘退出、不含利息。
## Cost and Execution
复用已有IC真实及模型状态机，交易费用单边1bp、30%缓冲、现金3%；分数手数。保留ETF交割/转换风险、最近档选约和不可成交跳过、期权代理及历史快照限制。未核验逐日追保/强平、盘口、整数手数。与IM日期和交割方式不同，不直接作高低排名。
## Verification
未过滤基线与已有结果逐日复现<1e-12；入场许可、独立逐轮损益、NAV复现通过。源代码和OHLC哈希见scan_meta，原始数据来源沿用父状态机，不修改旧输出及生产。
## Stability
固定95%，不优化参数，无独立OOS。
## Decision
research_only_no_promotion
## Commands
python -X utf8 research_ic_short95_native_momentum_v1.py
## Results
'''+s.to_string(index=False)+'\n\n'+pd.DataFrame(exposure).to_string(index=False)
    (OUT/'record.md').write_text(record,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_ic_short95_native_momentum_v1.py\n')
    print(s.to_string(index=False));print(pd.DataFrame(exposure).to_string(index=False))
if __name__=='__main__':main()
