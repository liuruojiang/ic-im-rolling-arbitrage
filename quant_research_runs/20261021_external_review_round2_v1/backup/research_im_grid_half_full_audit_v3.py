"""Full-component grid comparison on the audited Sep7 snapshot; no live writes."""
from pathlib import Path
import sys, json, hashlib, inspect, types
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RUN = ROOT/'quant_param_scan_runs/20260914_im_grid_half_full_audit_v3'
SOURCE = ROOT/'quant_param_scan_runs/20260908_im_mom120_put102_combined_v1'
sys.path.insert(0,str(SOURCE))
import run_combined as original
full = original.previous.prev.full
comp = original.comp
END = pd.Timestamp('2026-09-07')
DEFS = {'original160_half':[(1.6,2.,.5)],'current090_half':[(.9,1.7,.5)],'no_grid':[]}
HASHES, CHECKS = {}, []

def read(p,dates=('date',)):
    HASHES[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return pd.read_csv(p,parse_dates=list(dates),low_memory=False)

def eq(name,a,b):
    a,b=np.asarray(a,float),np.asarray(b,float)
    assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all(),name
    err=float(np.abs(a-b).max()) if a.size else 0.
    assert err<1e-12,(name,err)
    CHECKS.append(dict(check=name,max_abs_error=err,passed=True))

def metrics(d,start):
    z=d.loc[d.date.ge(start)].copy()
    return dict(**original.first.metrics(d,start),
                grid_days=int(z.overlay_held_eod.gt(0).sum()),grid_fraction=float(z.overlay_held_eod.gt(0).mean()),
                grid_fee=float(z.overlay_cost_rate.sum()),min_cash=float(z.cash_weight.min()))

def main():
    assert json.loads((SOURCE/'verification.json').read_text())['passed']
    spec=dict(candidates=DEFS,control='saved original160 1x complete combined',end=str(END.date()),
              rule='valuation only; close signal next trading open; half notional; no new grid options',
              unchanged='quarter T-1 futures, 0.5 core+0.5 momentum, dual102 Put, mom Put MOM120 only, D10 IV26 rescue Call',
              scope='historical counterfactual not forward ledger; no new optimization')
    (RUN/'preregister.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    # Fix an empty-trade diagnostic dtype in an isolated function only.
    engine=full.grids.grid_engine
    src=inspect.getsource(engine.simulate_overlay).replace('trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"].dt.year',
          'pd.to_datetime(trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"]).dt.year')
    ns=dict(engine.__dict__);exec(src,ns);full.grids.grid_engine=types.SimpleNamespace(**{**engine.__dict__,'simulate_overlay':ns['simulate_overlay']})
    (RUN/'executed_simulate_overlay.py').write_text(src,encoding='utf-8')
    full.RUN=RUN;full.GRID_DEFS={**DEFS,'control160_one':[(1.6,2.,1.)]}
    state=read(original.BASE/'valuation_state_through_last_required_eval.csv.gz')
    market=read(original.BASE/'model_market.csv.gz')
    up=read(original.BASE/'real_upstream.csv.gz')
    quotes=read(original.FULL/'official_im_quotes.csv.gz')
    bridges=read(original.FULL/'quarter1_close_bridges.csv.gz')
    data=dict(market=market,state=state,quotes=quotes)
    chain=dict(name='quarter1',up=up,bridges=bridges)
    summary, daily, events, episodes, annual, stress, carry_stress=[],[],[],[],[],[],[]
    outputs={}
    for scope in ['model','real']:
        b=read(SOURCE/f'{scope}_fixed_base.csv.gz')
        put=read(SOURCE/f'{scope}_combined_put.csv.gz')
        call=read(SOURCE/f'{scope}_fixed_call.csv.gz')
        savedgrid=read(SOURCE/f'{scope}_fixed_grid.csv.gz')
        ref=read(SOURCE/f'{scope}_combined_daily.csv.gz')
        control,t=full.grid_component(data,scope,chain,b,'control160_one')
        for col in ['overlay_gross_ret','overlay_cost_rate','overlay_held_eod','grid_carry']:eq(scope+' grid official replay '+col,control[col],savedgrid[col])
        reconstructed=comp.compose(b,put,control,call)
        for col in ['ret','cash_weight','futures_gross_ret','futures_cost_rate']:eq(scope+' saved full baseline '+col,reconstructed[col],ref[col])
        for name in DEFS:
            g,t=full.grid_component(data,scope,chain,b,name)
            d=comp.compose(b,put,g,call)
            assert d.date.equals(b.date) and d.date.is_unique and d.date.is_monotonic_increasing
            assert d.cash_weight.ge(0).all() and d.overlay_held_eod.isin([0,.5]).all()
            for col in original.first.FIELDS+comp.CALL_FIELDS:eq(scope+name+' unchanged '+col,d[col],reconstructed[col])
            eq(scope+name+' futures ledger',d.futures_gross_ret,d.base_futures_gross+d.overlay_gross_ret)
            eq(scope+name+' independent accounting',d.ret,(1+d.futures_gross_ret+d.put_pnl_ret+d.call_pnl_ret)*(1-d.futures_cost_rate)*(1-d.put_cost_rate)*(1-d.call_cost_rate)-1+(1-.3*d.total_units-d.put_mark_fraction-d.call_margin_fraction)*full.grids.CASH)
            d['candidate']=name;d['scope']=scope
            outputs[(scope,name)]=d
            d.to_csv(RUN/f'{scope}_{name}_daily.csv.gz',index=False)
            daily.append(d)
            if not t.empty:
                assert t.signal_date.lt(t.execution_date).all()
                t=t.assign(candidate=name,scope=scope);events.append(t)
                buys=t[t.action.eq('buy')];sells=t[t.action.eq('sell')]
                for buy in buys.itertuples():
                    later=sells[sells.execution_date.gt(buy.execution_date)]
                    sell=later.execution_date.iloc[0] if len(later) else pd.NaT
                    z=d[d.date.ge(buy.execution_date)&(d.date.lt(sell) if pd.notna(sell) else True)]
                    episodes.append(dict(scope=scope,candidate=name,buy_signal=buy.signal_date,buy=buy.execution_date,
                                         sell=sell,holding_days=len(z),calendar_days=(sell-buy.execution_date).days if pd.notna(sell) else None,
                                         contract=buy.execution_contract,execution_volume=buy.execution_volume,
                                         grid_gross_sum=float(z.overlay_gross_ret.sum())))
            for window,y in original.first.WINDOWS:
                start=d.date.min() if y is None else END-pd.DateOffset(years=y)
                row=dict(candidate=f'{scope}_{name}',scope=scope,grid=name,segment=window)
                if start<d.date.min():row.update(rows=0,start='N/A',end=str(END.date()),ann_return='N/A',ann_vol='N/A',sharpe_repo='N/A',max_dd='N/A',total_return='N/A')
                else:row.update(metrics(d,start))
                summary.append(row)
            for year in d.date.dt.year.unique():
                z=d[d.date.dt.year.eq(year)]
                r=z.ret.to_numpy(float);r=r[1:] if z.index[0]==0 else r
                nav=np.r_[1.,np.cumprod(1+r)]
                annual.append(dict(scope=scope,candidate=name,year=int(year),total_return=nav[-1]-1,max_dd=(nav/np.maximum.accumulate(nav)-1).min(),grid_days=int(z.overlay_held_eod.gt(0).sum())))
            for fee in [1,2,5]:
                x=comp.compose(b,put,g,call,fee)
                assert x.nav.le(d.nav+1e-12).all()
                row=dict(scope=scope,candidate=name,fee_multiplier=fee,**metrics(x,x.date.min()))
                row['grid_fee']*=fee;stress.append(row)
            if scope=='model':
                for carry_scale in [0.,.5,1.]:
                    bc=b.copy();gc=g.copy()
                    bc.base_futures_gross-=(1-carry_scale)*bc.long_carry
                    gc.overlay_gross_ret-=(1-carry_scale)*gc.grid_carry
                    x=comp.compose(bc,put,gc,call)
                    carry_stress.append(dict(candidate=name,carry_daily=.0003*carry_scale,**metrics(x,x.date.min())))
                pre=d[d.date.lt(pd.Timestamp('2022-07-22'))]
                summary.append(dict(candidate=f'{scope}_{name}',scope=scope,grid=name,segment='prelisting_model',**metrics(pre,pre.date.min())))
            print(scope,name,metrics(d,d.date.min()),flush=True)
    for name in DEFS:
        m=outputs[('model',name)];r=outputs[('real',name)]
        x=pd.concat([m[m.date.lt(r.date.min())],r],ignore_index=True)
        assert x.date.is_unique
        x.to_csv(RUN/f'continuous_{name}_daily.csv.gz',index=False)
        for window,y in original.first.WINDOWS:
            start=x.date.min() if y is None else END-pd.DateOffset(years=y)
            summary.append(dict(candidate=f'continuous_{name}',scope='continuous',grid=name,segment=window,**metrics(x,start)))
    sf=pd.DataFrame(summary);sf.to_csv(RUN/'scan_summary.csv',index=False)
    wide=sf.pivot(index='candidate',columns='segment',values=['ann_return','ann_vol','max_dd']);wide.columns=['_'.join(c) for c in wide.columns];wide.reset_index().to_csv(RUN/'window_metrics.csv',index=False)
    pd.concat(events,ignore_index=True).to_csv(RUN/'events.csv',index=False)
    pd.DataFrame(episodes).to_csv(RUN/'holding_episodes.csv',index=False)
    pd.DataFrame(annual).dropna(subset=['total_return']).to_csv(RUN/'annual.csv',index=False)
    pd.DataFrame(stress).to_csv(RUN/'cost_stress.csv',index=False)
    pd.DataFrame(carry_stress).to_csv(RUN/'model_carry_stress.csv',index=False)
    pd.DataFrame(CHECKS).to_csv(RUN/'parity_checks.csv',index=False)
    thresholds=[]
    for layer,start,end in [('prelisting_model',market.date.min(),pd.Timestamp('2022-07-21')),('real',up.date.min(),END),('full',market.date.min(),END)]:
        z=state[state.date.between(start,end)]
        for low in [.9,1.6]:thresholds.append(dict(scope=layer,low=low,eval_days=len(z),cheap_days=int(z.score.le(low+1e-12).sum())))
    pd.DataFrame(thresholds).to_csv(RUN/'threshold_coverage.csv',index=False)
    for mod in list(sys.modules.values()):
        p=getattr(mod,'__file__',None)
        if p and Path(p).suffix=='.py' and Path(p).resolve().is_relative_to(ROOT):
            p=Path(p).resolve();HASHES[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (RUN/'source_hashes.json').write_text(json.dumps(HASHES,ensure_ascii=False,indent=2),encoding='utf-8')
    meta=json.loads((RUN/'scan_meta.json').read_text());meta.update(scan_type='full_component_historical_grid_pair_audit',baseline={'candidate':'current090_half'},candidate_grid=DEFS,data_snapshot=dict(end=str(END.date()),source=str(SOURCE.relative_to(ROOT))),cost_model=dict(buffer=.3,cash_annual=.03,one_way=.0001,model_long_daily_carry=.0003,real_extra_carry=0,option_costs='unchanged original; all fees1/2/5x'),audit=dict(passed=True,checks=len(CHECKS),full_one_x_baseline_parity=True,production_modified=False),unavailable_segments={f'real_{n}':{w:'Actual IM/MO begins2022-07-22, insufficient5/10Y history' for w in ['last_10y','last_5y']} for n in DEFS})
    (RUN/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    record=['# IM完整组合：1.6/2.0与0.9/1.7半仓核实','',
      'Decision: research_validated_160_half_prior_candidate_no_promotion。1.6/2.0半仓是更充分参与历史的原定参数候选，不证明最优；未改生产。Stability: four_cycles_two_real_cycles_no_oos。只有四个不同历史周期，其中真实两轮，无独立样本外验证。Data: audited Sep7 snapshot。',
      '完整模型1.6/2.0半仓年化24.05%、波动15.04%、回撤16.21%；0.9/1.7为21.45%、13.97%、16.21%。上市前模型年化19.16%对15.27%，最大回撤均10.66%，持仓110天对0天。连续模型接真实年化25.05%对22.34%、最大回撤均16.23%。',
      '真实期1.6/2.0半仓年化36.22%、波动19.40%、回撤16.23%；0.9/1.7为35.95%、17.53%、16.23%。两组最差回撤同在2025-03-18至2025-04-08，当时均有网格，因此上一轮固定旧组件17.58%对15.66%的回撤差不能沿用。近3年年化47.84%对52.23%；近1年38.06%对26.41%，并非跨窗口一致占优。',
      '原阈值1.6历史低估信号340天，0.9仅6天；连续路径分别四轮670持仓交易日、两轮254持仓交易日。模型与真实同日期的周期不能重复计作独立样本。1.6最长一轮2024-01-18买入至2026-01-12卖出，478持仓交易日、725日历日，占真实网格持仓478/560=85.36%。仍有周期集中与小样本问题。',
      '将模型每日多头补偿由3bp降至0，1.6/2.0与0.9/1.7年化16.90%对15.10%、最大回撤均16.54%，收益差仍在但缩小，绝对收益明显依赖模型补偿假设。1/2/5倍手续费下两者全期收益排序不变，真实最大回撤仍相同。',
      '研究反事实；不改变生产。快照截至2026-09-07，不冒充9月14日最新数据。模型2015-04-16锚点，真实2022-07-22锚点。',
      '复用已审计102%双Put完整组合：0.5核心、0.5现行动量，季度T-1收盘展期，核心估值/负MOM120取高，动量Put只负MOM120，D10/IV26/5%救援Call。网格只看估值、额外0.5倍，无额外Put/Call。旧1倍完整组合逐日复现误差小于1e-12。',
      '完整模型期含合成IM、理论期权和每天每倍日内多头3bp补偿；真实层使用实际IM/MO、季度收盘桥接，无额外贴水补偿。连续层为上市前模型接上市后真实，各层单独报告，不能视为全期真实。',
      '原期货/Put/Call乘法费用顺序、每倍30%缓冲、现金3%保持；执行T收盘信号/T+1开盘，零量期权官方结算估计。未加入盘口冲击、动态保证金、强平、整数账户。未优化邻域；四轮或两轮均不足以证明样本外稳定。',
      '产物：逐日完整分腿、events、holding_episodes、年度压力、1/2/5倍费用、threshold_coverage、source_hashes、parity_checks。','',sf.to_string(index=False)]
    (RUN/'record.md').write_text('\n\n'.join(record),encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=len(CHECKS),thresholds=thresholds),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
