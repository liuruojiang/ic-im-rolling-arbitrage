"""Research-only IC/IM core-admission replay: no grid and no short Call.

The fixed core is 0.5x.  A 5% annualised discount uses the close observed on
T for a T+1 admission.  Existing core contracts are rolled while held.  In
the profit candidate, a core cycle is closed after its cumulative net futures
P&L becomes positive; a later >=5% observation may start a new cycle.
"""
from __future__ import annotations

import hashlib, json, math, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quant_param_scan_runs" / "20260915_core_admission_discount_profit"
THRESHOLD = .05
ONE_WAY = .0001

import run_ic_v13_sleeve_put_independent_replay_v1 as ic
import research_ic_put_discount_gate_5pct_v1 as ic_discount

IM_SOURCE = ROOT / "quant_param_scan_runs" / "20260908_im_mom120_put102_combined_v1"
sys.path.insert(0, str(IM_SOURCE))
import run_combined as im
import research_im_put_discount_gate_5pct_v1 as im_discount


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def state_machine(dates, rolls, gate, unit_gross, variant):
    """Return start/eod 0.5x-core states and net P&L by independently tradable cycle."""
    dates = pd.DatetimeIndex(dates)
    gate = pd.Series(gate, index=range(len(dates))).fillna(False).astype(bool)
    admit = gate.shift(1, fill_value=True)  # T close -> next session
    active = False
    cycle_pnl = 0.0
    start, eod, pnl, costs, exits = [], [], [], [], []
    for i in range(len(dates)):
        cost = 0.0
        if not active and admit.iat[i]:
            active, cycle_pnl = True, 0.0
            cost += .5 * ONE_WAY
        held = active
        gross = .5 * float(unit_gross[i]) if held else 0.0
        if held and bool(rolls[i]):
            cost += .5 * 2 * ONE_WAY
        net = gross - cost
        if held:
            cycle_pnl += net
        close = False
        if held and variant == "discount_roll_admission" and bool(rolls[i]) and not gate.iat[i]:
            close = True
        if held and variant == "profit_exit_reentry" and cycle_pnl > 0:
            close = True
        # Exit happens at T close, so model its one-way cost and make T+1 flat.
        if close:
            cost += .5 * ONE_WAY
            net -= .5 * ONE_WAY
            active = False
        start.append(.5 if held else 0.0); eod.append(.5 if active else 0.0)
        pnl.append(net); costs.append(cost); exits.append(close)
    return pd.DataFrame({"date": dates, "core_units_start": start, "core_units_eod": eod,
                         "core_net_pnl": pnl, "core_cost": costs, "core_exit": exits,
                         "admission_gate": admit.to_numpy(), "discount_gate": gate.to_numpy()})


def metrics(frame, candidate, product):
    rows=[]; end=frame.date.max()
    for segment, years in (("full",None),("last_10y",10),("last_5y",5),("last_3y",3),("last_1y",1)):
        start=frame.date.min() if years is None else end-pd.DateOffset(years=years)
        if years is not None and start < frame.date.min():
            rows.append(dict(product=product,candidate=f'{product}_{candidate}',segment=segment,start='N/A',end=str(end.date()),rows=0,ann_return='N/A',ann_vol='N/A',sharpe_repo='N/A',max_dd='N/A',holding_day_ratio='N/A',avg_core_units='N/A',cost_total='N/A'))
            continue
        z=frame[frame.date.ge(start)].copy()
        nav=(1+z.ret).cumprod(); dd=nav/nav.cummax()-1
        rows.append(dict(product=product,candidate=f'{product}_{candidate}',segment=segment,start=str(z.date.min().date()),end=str(z.date.max().date()),rows=len(z),ann_return=float(nav.iat[-1]**(252/len(z))-1),ann_vol=float(z.ret.std(ddof=0)*math.sqrt(252)),sharpe_repo=float((nav.iat[-1]**(252/len(z))-1)/(z.ret.std(ddof=0)*math.sqrt(252))),max_dd=float(dd.min()),holding_day_ratio=float(z.core_units_start.gt(0).mean()),avg_core_units=float(z.core_units_start.mean()),cost_total=float(z.futures_cost_rate.sum()+z.put_cost_rate.sum())))
    return rows


def ic_run(variant):
    frame, engine_base, selected=ic.load_base_components()
    frames,_,market,_=ic.ic_put.v1.put_engine.v19.v18.load_close_inputs(); rolls=ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames['ic'])
    discount=ic_discount.annualized_discount(frames['ic'])[['date','put_allowed']]
    f=frame.merge(discount,on='date',how='left'); gate=f.put_allowed.fillna(True)
    if variant=='baseline_no_grid_call': st=pd.DataFrame({'date':f.date,'core_units_start':.5,'core_units_eod':.5,'core_net_pnl':.5*(f.bare_roll_ic_ret-f.bare_roll_ic_cash_weight*ic.ic_grid.CASH_DAILY),'core_cost':0.,'core_exit':False,'admission_gate':True,'discount_gate':gate})
    else: st=state_machine(f.date,f.roll_event,gate,f.ic_gross_ret,variant)
    # Recompose two futures sleeves, no grid.  Core Put target disappears exactly when core is flat at execution EOD.
    w=f.momentum_execution_weight.astype(float); mom_turn=w.diff().abs(); mom_turn.iat[0]=abs(w.iat[0])
    mom_cost=ONE_WAY*mom_turn+2*ONE_WAY*w*f.roll_event.astype(float)
    mom_pnl=.5*((1+w*f.ic_gross_ret)*(1-mom_cost)-1)
    mom_cash=.5*(1-.30*w)*ic.ic_grid.CASH_DAILY
    f['base_non_cash_ret']=st.core_net_pnl.to_numpy()+mom_pnl.to_numpy()
    f['pre_put_cash_weight']=(1-.30*(st.core_units_eod+.5*w)).to_numpy()
    f['grid_held_eod']=0.; f['grid_net_increment']=0.; f['total_ic_units']=(st.core_units_eod+.5*w).to_numpy(); f['momentum_turnover']=mom_turn; f['momentum_cost_rate']=.5*mom_cost
    schedules={}
    for sleeve in ('core','momentum'):
        s=ic.build_schedule(selected,sleeve)
        active=pd.Series(st.core_units_eod.to_numpy(),index=f.date).reindex(pd.DatetimeIndex(s.execution_date)).fillna(0).to_numpy()>0
        if sleeve=='core':
            for c in ('target_delta','target_fraction','binary_target_fraction','three_tier_target_fraction'): s.loc[~active,c]=0.
        schedules[sleeve]=s
    ledgers={}; trades=[]
    for sleeve,s in schedules.items():
        ledgers[sleeve],t=ic.run_ledger(sleeve,s,frames,market,rolls); trades.append(t)
    d=ic.combine_candidate(f,ledgers,variant,('core','momentum'))
    d['core_units_start']=st.core_units_start; d['core_units_eod']=st.core_units_eod; d['core_exit']=st.core_exit; d['discount_gate']=st.discount_gate; d['futures_cost_rate']=st.core_cost.to_numpy()+.5*mom_cost.to_numpy()
    return d, st, pd.concat(trades,ignore_index=True), discount


def im_run(variant):
    base=im.pd.read_csv(im.JOINT/'real_baseline_base.csv.gz',parse_dates=['date'])
    state=im.pd.read_csv(im.BASE/'valuation_state_through_last_required_eval.csv.gz',parse_dates=['date']).set_index('date')
    up=im.pd.read_csv(im.BASE/'real_upstream.csv.gz',parse_dates=['date']); active=im.pd.read_csv(im.BASE/'real_active.csv.gz',parse_dates=['date'])
    raw=im.pd.read_csv(im.BASE/'real_options.csv.gz',parse_dates=['date','contract_month','rule_expiry','actual_expiry']); options=im.engine.with_execution_prices(raw)
    ds=im_discount.discount_states()[['date','put_allowed']]; b=base.merge(ds,on='date',how='left'); gate=b.put_allowed.fillna(True)
    unit=b.base_futures_gross/b.base_units
    if variant=='baseline_no_grid_call': st=pd.DataFrame({'date':b.date,'core_units_start':.5,'core_units_eod':.5,'core_net_pnl':.5*unit,'core_cost':.5*b.base_futures_cost,'core_exit':False,'admission_gate':True,'discount_gate':gate})
    else: st=state_machine(b.date,b.roll_event,gate,unit,variant)
    w=b.momentum_weight.astype(float); turn=w.diff().abs(); turn.iat[0]=abs(w.iat[0]); momcost=.5*(ONE_WAY*turn+2*ONE_WAY*w*b.roll_event.astype(float))
    b['base_units']=st.core_units_eod.to_numpy()+.5*w.to_numpy(); b['base_futures_gross']=st.core_net_pnl.to_numpy()+.5*w.to_numpy()*unit.to_numpy(); b['base_futures_cost']=st.core_cost.to_numpy()+momcost.to_numpy()
    schedules={sl:im.pd.read_csv(im.FULL/f'real_dual_{sl}_schedule.csv.gz',parse_dates=['eval_date','execution_date']) for sl in ['core','mom']}
    legs=[]; trades=[]
    for sleeve,s in schedules.items():
        s=im.target_schedule(s,state,b.momentum_weight,'real',sleeve,'dual'); s.binary_target_qty*=4; s['three_tier_target_qty']=s.binary_target_qty
        if sleeve=='core':
            eod=pd.Series(st.core_units_eod.to_numpy(),index=b.date).reindex(im.pd.DatetimeIndex(s.execution_date)).fillna(0).to_numpy()>0; s.loc[~eod,['binary_target_qty','three_tier_target_qty']]=0
        p,t,_=im.engine.run_real_monthly_close(up,options,active,s,'3m',1.02,variant+'_'+sleeve,reset_dates=im.engine.monthly_dates(b.date)); p[im.first.FIELDS]*=.25/4; legs.append(p); trades.append(t.assign(sleeve=sleeve))
    put=sum(p[im.first.FIELDS] for p in legs); put['date']=b.date
    g=im.pd.read_csv(im.JOINT/'real_baseline_grid.csv.gz',parse_dates=['date']);
    for c in g.columns:
        if c.startswith('overlay_') or c=='grid_carry': g[c]=0.
    d=im.comp.compose(b,put,g,None); d['core_units_start']=st.core_units_start; d['core_units_eod']=st.core_units_eod; d['core_exit']=st.core_exit; d['discount_gate']=st.discount_gate
    return d,st,im.pd.concat(trades,ignore_index=True),ds


def main():
    # This folder was initialized for this in-progress run; reruns overwrite
    # only its own derived research files before finalization.
    allrows=[]; notes=[]
    for product, runner in [('IC',ic_run),('IM',im_run)]:
        for variant in ('baseline_no_grid_call','discount_roll_admission','profit_exit_reentry'):
            d,st,tr,discount=runner(variant); d.to_csv(OUT/f'{product.lower()}_{variant}_daily.csv.gz',index=False,compression='gzip'); st.to_csv(OUT/f'{product.lower()}_{variant}_core_state.csv.gz',index=False,compression='gzip'); tr.to_csv(OUT/f'{product.lower()}_{variant}_put_trades.csv.gz',index=False,compression='gzip')
            allrows.extend(metrics(d,variant,product)); notes.append(dict(product=product,candidate=variant,core_exit_events=int(st.core_exit.sum()),core_entry_events=int(st.core_units_start.gt(st.core_units_start.shift().fillna(0)).sum()),low_discount_fraction=float((~st.discount_gate).mean()),put_trade_events=len(tr)))
    summary=pd.DataFrame(allrows); summary.to_csv(OUT/'scan_summary.csv',index=False)
    wide=summary.pivot(index=['product','candidate'],columns='segment',values=['ann_return','max_dd','ann_vol','sharpe_repo']); wide.columns=['_'.join(x) for x in wide.columns]; wide.reset_index().to_csv(OUT/'window_metrics.csv',index=False)
    pd.DataFrame(notes).to_csv(OUT/'activity_summary.csv',index=False)
    unavailable={f'IM_{name}': {'last_10y':'Actual IM/MO history starts 2022-07-22; fewer than 10 years.', 'last_5y':'Actual IM/MO history starts 2022-07-22; fewer than 5 years.'} for name in ['baseline_no_grid_call','discount_roll_admission','profit_exit_reentry']}
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8')); meta.update(phase='analysis_complete_pending_finalization',scan_type='two_predeclared_core-admission rules, no grid/no short Call, independent core/momentum Put ledgers',baseline={'candidate':'IC_baseline_no_grid_call and IM_baseline_no_grid_call'},candidate_grid=sorted(summary.candidate.unique().tolist()),data_snapshot={'IC':'2015-04-16..2026-08-14','IM':'2022-07-22..2026-09-07'},cost_model={'futures_one_way_notional':ONE_WAY,'margin_buffer':.30,'cash_annual':.03,'Put':'existing independent monthly engines and original fee model'},unavailable_segments=unavailable,outputs={**meta['outputs'],'activity':'quant_param_scan_runs/20260915_core_admission_discount_profit/activity_summary.csv'},warnings=['IC baseline uses the validated frozen historical engine through 2026-08-14; IM uses verified real history only through 2026-09-07. This is research only.'])
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(summary.to_string(index=False)); print(pd.DataFrame(notes).to_string(index=False))
if __name__=='__main__': main()
