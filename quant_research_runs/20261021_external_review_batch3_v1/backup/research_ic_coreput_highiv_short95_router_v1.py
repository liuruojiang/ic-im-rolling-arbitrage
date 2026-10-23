"""Matched IC + current core Put -> high-IV short95 Put router scan."""
from __future__ import annotations

import hashlib, inspect, json, math, subprocess
from pathlib import Path
import numpy as np
import pandas as pd

import ic_510500_put_proxy_validation_v1 as proxy
import research_ic_mom120_debounce_v1 as debounce
import research_ic_short95_premium_decay_reentry_scan_v1 as short
import run_ic_v13_sleeve_put_independent_replay_v1 as ic

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'quant_param_scan_runs'/'20260916_ic_im_ic_put_iv_95_510500put_ic_coreput_short95_router_iv20_22_24_26_28_30_32_5_35_37_5_40_decay60'
SPEC=ROOT/'docs'/'ic_coreput_highiv_short95_router_v1_spec.md'
THRESHOLDS=(.20,.22,.24,.26,.28,.30,.325,.35,.375,.40)
CASH=1.03**(1/252)-1
ONE_WAY=.0001

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def git_status(): return subprocess.run(['git','status','--short'],cwd=ROOT,text=True,capture_output=True).stdout.strip()

def current_schedule():
    _,_,selected=ic.load_base_components()
    _,valuation,_,_=ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    tri=valuation.set_index('date').tri_close
    schedule=debounce.build_schedule(selected,tri,debounce.VARIANTS[2])
    first=schedule.sort_values(['layer','execution_date']).groupby('layer').first()
    if first.loc['model','execution_date']!=pd.Timestamp('2015-04-16') or first.loc['real','execution_date']!=pd.Timestamp('2022-09-19'): raise RuntimeError('IC initial core-Put schedule missing')
    if schedule[['valuation_tier_new','momentum_120','target_delta']].isna().any().any(): raise RuntimeError('IC core schedule has missing state')
    return schedule

def permission(): return short.permissions()

def real_signals(active,chains,histories,threshold):
    adm=permission(); hist=histories.set_index(['security_id','date']); rows=[]
    for i in range(1,len(active)):
        ev=pd.Timestamp(active.loc[i-1,'date']); ex=pd.Timestamp(active.loc[i,'date']); spot=float(active.loc[i-1,'csi500_price_close'])
        month=ex.to_period('M').to_timestamp()+pd.offsets.MonthBegin(1); pick=short.choose_real(chains.get(ev,pd.DataFrame()),spot,month)
        iv=np.nan; valid=False
        if pick is not None:
            iv=float(pick.implied_volatility); q=hist.loc[(str(pick.security_id),ex)] if (str(pick.security_id),ex) in hist.index else None
            valid=bool(q is not None and float(q.open)>0 and float(q.volume)>0)
        ok=bool(adm.get(ex,False) and valid and np.isfinite(iv) and iv>threshold)
        rows.append({'eval_date':ev,'execution_date':ex,'iv':iv,'admission':bool(adm.get(ex,False)),'execution_open_valid':valid,'route':ok})
    return pd.DataFrame(rows)

def model_signals(active,market,threshold):
    adm=permission(); m=market.set_index('date'); rows=[]
    for i in range(1,len(active)):
        ev=pd.Timestamp(active.loc[i-1,'date']); ex=pd.Timestamp(active.loc[i,'date']); iv=float(m.loc[ev,'sigma_close'])
        rows.append({'eval_date':ev,'execution_date':ex,'iv':iv,'admission':bool(adm.get(ex,False)),'execution_open_valid':True,'route':bool(adm.get(ex,False) and iv>threshold)})
    return pd.DataFrame(rows)

def entry_series(signal): return signal.set_index('execution_date').route.rename('admission')

def stitched_router(scope, active, futures, isolated, signal):
    """Replace isolated idle cash with constant-1x rolling IC, preserving its cycle state machine."""
    fut=futures if list(futures.index.names)==['contract','date'] else futures.set_index(['contract','date']); sig=signal.set_index('execution_date'); out=[]; normal=True
    for i,row in isolated.reset_index(drop=True).iterrows():
        day=pd.Timestamp(row.date); b=active.iloc[i]; prev=active.iloc[i-1] if i else b
        base=float(b.ic_net_ret)+.7*CASH; action=str(row.action); ret=float(row.return_net); transition=''
        if normal and action.startswith(('sell_next_month_95_put_open','sell_model_next_month_95_put_open')):
            q=fut.loc[(str(b.contract),day)]; prior_q=fut.loc[(str(b.contract),pd.Timestamp(prev.date))]
            overnight=float(q.open)/float(prior_q.settle)-1-ONE_WAY
            ret += overnight
            normal=False; transition='ic_to_short_put_open'
        elif normal:
            ret=base
        elif str(row.state)=='idle' and action=='':
            q=fut.loc[(str(b.contract),day)]
            ret=float(q.settle)/float(q.open)-1-ONE_WAY+.7*CASH
            normal=True; transition='resume_ic_open'
        out.append({'date':day,'return_net':ret,'state':'ic' if normal else str(row.state),'action':transition or action,'route':bool(sig.loc[day].route) if day in sig.index else False,'iv':float(sig.loc[day].iv) if day in sig.index else np.nan,'cash_weight':.7 if normal or str(row.state)!='idle' else 1.0})
    d=pd.DataFrame(out); d['nav']=(1+d.return_net).cumprod(); return d

def patched_engines():
    eng=ic.ic_put.v1.put_engine
    ms=inspect.getsource(eng.run_model_delta)
    ms=ms.replace('roll_dates: set[pd.Timestamp],\n) -> tuple[pd.DataFrame, pd.DataFrame]:','roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(),\n) -> tuple[pd.DataFrame, pd.DataFrame]:')
    ms=ms.replace('day = pd.Timestamp(row.date)\n        event = events.get(day)','day = pd.Timestamp(row.date)\n        route_open_exit = day in open_exit_dates\n        event = events.get(day)')
    old='''elif active is not None and latest_target == 0:\n            price, _ = model_price_and_delta(active, row)'''
    new='''elif active is not None and latest_target == 0:\n            if route_open_exit:\n                years=max((active.expiry-day).days,0)/365.0\n                price=v19.v18.v13.proxy.bs_put(float(row.spot_open),active.strike,float(row.rate_open),float(row.dividend_open),float(row.sigma_open),years)\n            else:\n                price, _ = model_price_and_delta(active, row)'''
    if old not in ms: raise RuntimeError('model core exit hook changed')
    ms=ms.replace(old,new).replace('action = "close_exit"','action = "route_open_exit" if route_open_exit else "close_exit"',1)
    rs=inspect.getsource(eng.run_real_delta)
    rs=rs.replace('roll_dates: set[pd.Timestamp],\n) -> tuple[pd.DataFrame, pd.DataFrame]:','roll_dates: set[pd.Timestamp], open_exit_dates=frozenset(),\n) -> tuple[pd.DataFrame, pd.DataFrame]:')
    rs=rs.replace('day = pd.Timestamp(row.date)\n        event = events.get(day)','day = pd.Timestamp(row.date)\n        route_open_exit = day in open_exit_dates\n        event = events.get(day)')
    old='''if active is not None and latest_target == 0:\n            quote = proxy.history_exact(history_lookup, active.security_id, day)\n            if quote is not None and float(quote["close"]) > 0 and float(quote["volume"]) > 0:\n                pnl += (\n                    active.qty\n                    * OPTION_MULTIPLIER\n                    * (float(quote["close"]) - active.prior_mark)\n                    / denominator\n                )'''
    new='''if active is not None and latest_target == 0:\n            quote = proxy.history_exact(history_lookup, active.security_id, day)\n            exit_price = float(quote["open"] if route_open_exit else quote["close"]) if quote is not None else math.nan\n            if quote is not None and exit_price > 0 and float(quote["volume"]) > 0:\n                pnl += (\n                    active.qty\n                    * OPTION_MULTIPLIER\n                    * (exit_price - active.prior_mark)\n                    / denominator\n                )'''
    if old not in rs: raise RuntimeError('real core exit hook changed')
    rs=rs.replace(old,new).replace('action = "close_exit"','action = "route_open_exit" if route_open_exit else "close_exit"',1)
    ns=dict(vars(eng)); exec(compile(ms,str(Path(__file__)),'exec'),ns); exec(compile(rs,str(Path(__file__)),'exec'),ns)
    return ns['run_model_delta'],ns['run_real_delta'],ms+'\n\n'+rs

def safe_model_runner():
    src=inspect.getsource(short.run_model)
    old='''daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)'''
    new='''daily,cycles=pd.DataFrame(rows),pd.DataFrame(cycles)\n    if "physical_assignment_date" not in cycles: cycles["physical_assignment_date"]=pd.NaT\n    if "early_rolls" not in cycles: cycles["early_rolls"]=0'''
    if old not in src: raise RuntimeError('model short-Put audit hook changed')
    src=src.replace(old,new).replace('cycles.physical_assignment_date','cycles.get("physical_assignment_date",pd.Series(dtype=object))').replace('cycles.early_rolls','cycles.get("early_rolls",pd.Series(dtype=float))')
    ns=dict(vars(short)); exec(compile(src,str(Path(__file__)),'exec'),ns); return ns['run_model']

def mask_schedule(schedule,scope,ic_dates):
    s=schedule.copy(); mask=pd.Series(True,index=pd.DatetimeIndex(ic_dates))
    allowed=s.execution_date.map(mask).fillna(False) & s.layer.eq(scope)
    s.loc[s.layer.eq(scope)&~allowed,'target_delta']=0.0
    return s

def add_core(router,put,label):
    r=router.reset_index(drop=True).copy(); p=put.reset_index(drop=True)
    if not r.date.equals(p.date): raise RuntimeError('router/core dates differ')
    noncash=r.return_net-r.cash_weight*CASH
    cash=np.maximum(r.cash_weight-p.put_mark_fraction,0)
    r['return_net']=(1+noncash+p.put_pnl_ret)*(1-p.put_cost_rate)-1+cash*CASH
    r['candidate']=label; r['nav']=(1+r.return_net).cumprod(); return r

def baseline(active,scope):
    d=pd.DataFrame({'date':active.date,'candidate':f'{scope}_pure_rolling_ic','return_net':active.ic_net_ret.astype(float)+.7*CASH,'state':'ic','action':'','route':False,'iv':np.nan,'cash_weight':.7})
    d['nav']=(1+d.return_net).cumprod(); return d

def summarize(daily):
    rows=[]; wide=[]; unavailable={}
    for cand,g in daily.groupby('candidate',sort=False):
        w={'candidate':cand}; g=g.sort_values('date')
        for seg,y in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            start=g.date.min() if y is None else g.date.max()-pd.DateOffset(years=y); ok=y is None or g.date.min()<=start
            sub=g[g.date>=start] if ok else g.iloc[:0]; vals=proxy.metrics(sub.return_net) if ok else {k:'N/A' for k in ('total_return','ann_return','ann_vol','sharpe_repo','max_dd')}
            if not ok: unavailable.setdefault(cand,{})[seg]='history shorter than requested window'
            rows.append({'candidate':cand,'segment':seg,'start':str(start.date()),'end':str(g.date.max().date()),'rows':len(sub),**vals})
            for k,v in vals.items(): w[f'{k}_{seg}']=v
        wide.append(w)
    return pd.DataFrame(rows),pd.DataFrame(wide),unavailable

def main():
    meta_path=RUN/'scan_meta.json'; meta=json.loads(meta_path.read_text(encoding='utf-8'))
    if meta.get('phase')!='init': raise RuntimeError('run already started')
    schedule=current_schedule(); model_eng,real_eng,engsrc=patched_engines(); model_short=safe_model_runner(); frame,_,_=ic.load_base_components(); frames,_,market,_=ic.ic_put.v1.put_engine.v19.v18.load_close_inputs(); roll=ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames['ic'])
    layers={}
    ra,etf,chains,opts,exp,rfut=short.real_inputs(); ma,mm,mfut=short.model_source.model_inputs()
    for scope,active,futures,marketx in [('real',ra,rfut,None),('model',ma,mfut,mm)]:
        base=baseline(active,scope); always=mask_schedule(schedule,scope,base.date)
        put,tr=(real_eng(frames['ic'],always,frames,market,f'{scope}_core',roll) if scope=='real' else model_eng(frames['ic'],always,market,f'{scope}_core',roll))
        put=put[put.date.isin(base.date)].reset_index(drop=True); core=add_core(base,put,f'{scope}_rolling_ic_current_core_put')
        parts=[base,core]; audits={'pure_parity':float(abs(base.return_net-(active.ic_net_ret+.7*CASH)).max()),'core_trades':len(tr)}
        for t in THRESHOLDS:
            sig=real_signals(active,chains,frames['histories'],t) if scope=='real' else model_signals(active,marketx,t)
            isolated,events,cycles,audit=(short.run_real(entry_series(sig),.60) if scope=='real' else model_short(entry_series(sig),.60))
            routed=stitched_router(scope,active,futures,isolated,sig); icdays=routed.loc[routed.state.eq('ic'),'date']; ms=mask_schedule(schedule,scope,icdays); exits=set(routed.loc[routed.action.eq('ic_to_short_put_open'),'date'])
            p,tr=(real_eng(frames['ic'],ms,frames,market,f'{scope}_iv{int(t*1000):03d}',roll,exits) if scope=='real' else model_eng(frames['ic'],ms,market,f'{scope}_iv{int(t*1000):03d}',roll,exits))
            p=p[p.date.isin(routed.date)].reset_index(drop=True); label=f'{scope}_iv{int(t*1000):03d}_coreput_to_short95_decay60'; combined=add_core(routed,p,label); parts.append(combined)
            sim=set(tr.loc[tr.action.eq('route_open_exit'),'actual_execution_date']);
            if sim-exits: raise RuntimeError('core Put exit outside route')
            audits[label]={**audit,'route_switches':len(exits),'simultaneous_core_put_exits':len(sim),'routes_without_active_core_put':len(exits-sim)}
            sig.assign(candidate=label).to_csv(RUN/f'{label}_signal.csv',index=False)
        layers[scope]=(pd.concat(parts,ignore_index=True),audits)
    daily=pd.concat([layers['real'][0],layers['model'][0]],ignore_index=True); summary,wide,unavailable=summarize(daily); out=RUN/'daily_outputs';out.mkdir(exist_ok=False);daily.to_csv(out/'daily.csv.gz',index=False,compression='gzip');summary.to_csv(RUN/'scan_summary.csv',index=False,encoding='utf-8-sig');wide.to_csv(RUN/'window_metrics.csv',index=False,encoding='utf-8-sig');(RUN/'executed_core_engines.py').write_text(engsrc,encoding='utf-8')
    full=summary[summary.segment.eq('full')]
    meta.update(scan_type='IC current core Put to high-IV short95 router',candidate_grid=[{'iv_threshold':x,'decay':.60} for x in THRESHOLDS],baseline={'pure':'*_pure_rolling_ic','protected':'*_rolling_ic_current_core_put'},data_snapshot={'real':'2022-09-19..2026-08-14 actual 510500/IC','model':'2015-04-16..2026-08-14 theoretical 510500 Put proxy plus historical IC','spec_sha256':sha(SPEC)},cost_model={'one_way_notional':ONE_WAY,'reserve':.30,'cash_annual':.03,'execution':'T close signal, T+1 open route; core Put exit simultaneous'},audit={'real':layers['real'][1],'model':layers['model'][1]},unavailable_segments=unavailable,outputs={**meta['outputs'],'daily':str(out/'daily.csv.gz'),'executed_core_engines':str(RUN/'executed_core_engines.py')},warnings=['Current IC core Put is 95% target, not IM 102%.','Real option history is short; model option history is theoretical proxy.','No bid-ask, dynamic margin, forced liquidation, tax, capacity or integer sizing.'],decision='research_only_pending_interpretation',stability_label='pending_review',git_status_after=git_status())
    meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');(RUN/'record.md').write_text('# IC核心Put与高IV卖95% Put混合路由\n\n真实挂牌与理论延展分开；不改生产。\n\n## Full Results\n\n'+full.to_markdown(index=False)+'\n\n## Audit\n\n```json\n'+json.dumps(meta['audit'],ensure_ascii=False,indent=2)+'\n```\n\n## Decision\n\nresearch_only_pending_interpretation\n',encoding='utf-8');(RUN/'command_log.txt').open('a',encoding='utf-8').write(f'cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n');print(full.to_string(index=False))

if __name__=='__main__': main()
