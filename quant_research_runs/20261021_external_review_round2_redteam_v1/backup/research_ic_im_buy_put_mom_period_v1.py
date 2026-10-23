from pathlib import Path
import sys,json,hashlib
import pandas as pd
import numpy as np
from im_put_maturity_valuation_tiers_v3 import metrics
import run_ic_v13_sleeve_put_independent_replay_v1 as ic
import ic_510500_put_full_cycle_valuation_v2 as icval
import im_mo_csi1000_put_protection_battery_v6 as immarket
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'quant_param_scan_runs/20260908_im_mom120_put102_combined_v1'
sys.path.insert(0,str(SOURCE));import run_combined as im
OUT=ROOT/'quant_param_scan_runs/20260914_ic_im_buy_put_mom_period_v1'
END=pd.Timestamp('2026-08-14');HASHES={};AUDIT={};DS=[];TS=[]

def read(p,dates=('date',)):
    HASHES[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return pd.read_csv(p,parse_dates=list(dates),low_memory=False)

def im_run():
    market=read(im.BASE/'model_market.csv.gz');up=read(im.BASE/'real_upstream.csv.gz');active=read(im.BASE/'real_active.csv.gz')
    options=im.engine.with_execution_prices(read(im.BASE/'real_options.csv.gz',('date','contract_month','rule_expiry','actual_expiry')))
    state=read(im.BASE/'valuation_state_through_last_required_eval.csv.gz').set_index('date')
    tri=state.tri_close
    for scope in ['model','real']:
        b=read(SOURCE/(scope+'_fixed_base.csv.gz'))
        for days in [120,60,240]:
            mom=tri.pct_change(days,fill_method=None).reindex(state.index)
            if days==120:
                z=state.momentum_120.notna();err=float(abs(mom[z]-state.momentum_120[z]).max());assert err<1e-12
                AUDIT['IM_momentum_formula']=err
            legs=[]
            for sleeve in ['core','mom']:
                s=read(SOURCE/f'{scope}_combined_{sleeve}_schedule.csv.gz',('eval_date','execution_date'))
                mo=s.eval_date.map(mom)
                parent=np.maximum(s.eval_date.map(state.valuation_tier) if sleeve=='core' else 0,np.where(mo.lt(0),3,0))
                factor=1 if scope=='model' and sleeve=='core' else 2
                qty=parent*factor*(b.momentum_weight.to_numpy() if sleeve=='mom' else 1)*4
                assert np.equal(qty,np.floor(qty)).all()
                if days==120:assert np.array_equal(qty,s.binary_target_qty)
                s.binary_target_qty=qty.astype(int);s.three_tier_target_qty=s.binary_target_qty
                s['momentum_120']=mo;s['mom120_floor_qty']=np.where(mo.lt(0),3,0)
                label=f'IM_{scope}_mom{days}_{sleeve}'
                if scope=='model':
                    p,t,_=im.engine.run_model_monthly_close(market,s,'3m',1.02,label,reset_dates=im.engine.monthly_dates(b.date));norm=(.5 if sleeve=='core' else .25)/4
                else:
                    p,t,_=im.engine.run_real_monthly_close(up,options,active,s,'3m',1.02,label,reset_dates=im.engine.monthly_dates(b.date));norm=.25/4
                p[im.first.FIELDS]*=norm
                if days==120:
                    ref=read(SOURCE/f'{scope}_combined_{sleeve}_put.csv.gz')
                    err=max(float(abs(p[f]-ref[f]).max()) for f in im.first.FIELDS);assert err<1e-12
                    assert p.put_contract.fillna('').equals(ref.put_contract.fillna(''));AUDIT[label+'_parity']=err
                p.to_csv(OUT/(label+'_put.csv.gz'),index=False);s.to_csv(OUT/(label+'_schedule.csv.gz'),index=False)
                t['candidate']=label;TS.append(t);legs.append(p)
            put=legs[0].copy()
            for f in im.first.FIELDS:put[f]=legs[0][f]+legs[1][f]
            g=read(SOURCE/(scope+'_fixed_grid.csv.gz'));c=read(SOURCE/(scope+'_fixed_call.csv.gz'))
            if days==120:
                full=im.comp.compose(b,put,g,c);ref=read(SOURCE/(scope+'_combined_daily.csv.gz'))
                err=float(abs(full.ret-ref.ret).max());assert err<1e-12;AUDIT[f'IM_{scope}_full_native_parity']=err
            d=im.first.compose(b,put);d['return_net']=d.ret;d['candidate']=f'IM_{scope}_mom{days}';DS.append(d[d.date<=END].copy())
            print(d.candidate.iloc[0],flush=True)

def ic_run():
    frame,base,selected=ic.load_base_components()
    frames,valuation,market,checks=ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll=ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames['ic'])
    tri=valuation.set_index('date').tri_close
    # Hold futures and remove only the grid overlay for this controlled protection comparison.
    plain=frame.copy();plain.grid_net_increment=0
    plain.pre_put_cash_weight+=.3*plain.grid_held_eod
    plain.total_ic_units-=plain.grid_held_eod;plain.grid_held_eod=0
    saved=read(ic.OUTPUT/'daily_candidates.csv.gz')
    for days in [120,60,240]:
        s=selected.copy();mo=s.eval_date.map(tri.pct_change(days,fill_method=None))
        if days==120:
            z=s.momentum_120.notna();err=float(abs(mo[z]-s.momentum_120[z]).max());assert err<1e-12;AUDIT['IC_momentum_formula']=err
        full=np.maximum(s.valuation_tier_new*.25,np.where(mo.lt(0),.5,0))
        if days==120:assert np.allclose(full,s.v2_target_delta,atol=1e-12)
        s.v2_target_delta=full;s['momentum_120']=mo
        schedule=ic.build_schedule(s,'combined_current')
        schedule.to_csv(OUT/f'IC_mom{days}_schedule.csv.gz',index=False)
        for scope in ['model','real']:
            label=f'IC_{scope}_mom{days}';engine=ic.ic_put.v1.put_engine
            if scope=='model':p,t=engine.run_model_delta(frames['ic'],schedule,market,label,roll)
            else:p,t=engine.run_real_delta(frames['ic'],schedule,frames,market,label,roll)
            p.to_csv(OUT/(label+'_put.csv.gz'),index=False);t['candidate']=label;TS.append(t)
            # The IC ledger may return only its own layer's dates: align explicitly.
            f=plain[plain.date.isin(p.date)].copy().reset_index(drop=True);p=p.reset_index(drop=True)
            assert f.date.equals(p.date)
            d=ic.combine_candidate(f,{'combined':p},label,('combined',));d['return_net']=d.ret
            if scope=='real':d=d[d.date>=pd.Timestamp('2022-09-19')].copy()
            if days==120:
                ref=saved[saved.candidate=='authoritative_current_combined'].copy()
                # Saved native includes the grid; compare the full composition on matching native layer dates.
                nativeframe=frame[frame.date.isin(p.date)].reset_index(drop=True)
                native=ic.combine_candidate(nativeframe,{'combined':p},label,('combined',))
                valid=native.date.lt('2022-09-19') if scope=='model' else native.date.ge('2022-09-19')
                q=native[valid].merge(ref[['date','ret']],on='date',suffixes=('_new','_old'),validate='one_to_one')
                assert len(q)==valid.sum();err=float(abs(q.ret_new-q.ret_old).max());assert err<1e-12;AUDIT[label+'_native_parity']=err
            DS.append(d[d.date<=END]);print(label,flush=True)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'preregister.json').write_text(json.dumps(dict(days=[60,120,240],instruments=['IC','IM'],scope='0.5 core + 0.5 native momentum futures, original Put protection; grid/call excluded',IC='core max valuation delta and negative momentum minimum50%; momentum valuation only',IM='core max valuation and negative momentum minimum3; momentum negative momentum minimum3 times native momentum exposure',strike={'IC':.95,'IM':1.02},cutoff=str(END.date()),unknown_momentum='no additional momentum floor; existing valuation retained'),indent=2),encoding='utf-8')
    im_run();ic_run()
    daily=pd.concat(DS,ignore_index=True);daily.to_csv(OUT/'daily.csv.gz',index=False);pd.concat(TS,ignore_index=True).to_csv(OUT/'trades.csv.gz',index=False)
    rows=[];wide=[];unavailable={}
    for label,d in daily.groupby('candidate'):
        w={'candidate':label}
        for seg,y in [('full',None),('last_10y',10),('last_5y',5),('last_3y',3),('last_1y',1)]:
            start=END-pd.DateOffset(years=y) if y else d.date.min();ok=start>=d.date.min();sub=d[d.date>=start]
            m=metrics(sub.return_net) if ok else {k:'N/A' for k in ['ann_return','ann_vol','sharpe_repo','max_dd']}
            if not ok:unavailable.setdefault(label,{})[seg]='Insufficient actual option history'
            rows.append(dict(candidate=label,segment=seg,start=str(start.date()),end=str(END.date()),rows=len(sub) if ok else 0,**m))
            for k,v in m.items():w[k+'_'+seg]=v
        wide.append(w)
    result=pd.DataFrame(rows);result.to_csv(OUT/'scan_summary.csv',index=False);pd.DataFrame(wide).to_csv(OUT/'window_metrics.csv',index=False)
    meta=json.loads((OUT/'scan_meta.json').read_text(encoding='utf-8'));meta.update(data_snapshot=HASHES,audit=AUDIT,baseline={'candidate':'IM_real_mom120'},unavailable_segments=unavailable,cost_model={'native':'unchanged original fees and 30% futures reserve, cash3%'},limitations='Frozen historical components, not current grid-half allrules. Uniform model and actual layer separate. Original option proxy, liquidity/settlement estimates, fractional sizing retained; no forced liquidation, bidask or integer model. Only buy-Put momentum horizon changed. No production changes.')
    (OUT/'scan_meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    text='''# IC/IM买Put保护：60/120/240日绝对动量
## Data Snapshot
截止2026-08-14。各自模型与真实独立报告，模拟不是实际上市前可交易。真实历史不足5/10年N/A。
## Implementation Anchor
原版0.5核心＋0.5动量期货＋原买Put；不叠加网格/Call。IC95%/原Delta目标、核心负动量最低50%、动量Put仅估值；IM102%、核心估值与负动量最低3取最大、动量Put负动量最低3乘现行动量权重。仅替换保护MOM周期，不改期货动量开仓信号，不涉及卖Put入场。
## Cost and Execution
复用原引擎原执行、三个月最近挂牌期限、独立月重置、费率、30%期货缓冲及现金3%。每笔实际重放，未按理论Put payoff替代路径。原历史挂牌/定价及零成交结算估计限制保留；无盘口、整数手数、追保或强平。
## Verification
120日目标数量、期权各字段与合约及原完整组合逐日收益复现通过，详见scan_meta.audit；60/240预设邻点，不选择全局最优。历史组件快照无生产配置修改，不冒称当前网格减半完整策略绩效。
## Stability
固定三个周期，无独立OOS。需检查收益牺牲、回撤与各窗口稳定性。
## Decision
research_only_no_promotion
## Commands
python -X utf8 research_ic_im_buy_put_mom_period_v1.py
## Results
'''+result.to_string(index=False)
    (OUT/'record.md').write_text(text,encoding='utf-8')
    with (OUT/'command_log.txt').open('a',encoding='utf-8') as f:f.write('\npython -X utf8 research_ic_im_buy_put_mom_period_v1.py\n')
    print(result[result.segment=='full'].to_string(index=False))
if __name__=='__main__':main()
