from pathlib import Path
import json,hashlib
import pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'outputs/im_valuation_entry_semantics_audit_20260914_v1'
BODY=ROOT/'outputs/im_fixed_valuation_tier_relationship_v3/daily_tier_states.csv.gz'
STATE=ROOT/'quant_param_scan_runs/20260908_im_put_revalidation_layer1_v1/valuation_state_through_last_required_eval.csv.gz'
ENTRY=ROOT/'quant_param_scan_runs/20260914_icim_im_short95_entry_valuation_0123_v2_entry_valuation_tier/entry_valuation_state.csv'
PERM=ENTRY.parent/'entry_permissions.csv'
RESULT=ROOT/'outputs/grid_half_release_20260913/remote_success/ic-im-v1-3-r7-post-close-digest/strategy-artifacts/result.json'
SNAPSHOT=ROOT/'runtime/legulegu/latest.json'
LEDGER=ROOT/'runtime/ic_im_v1_3_r7/latest.json'
FROZEN=np.array([1.9763424455071,2.09523542263064,2.28601924893132,2.32518798514896])
QUANTILES=[.75,.85,.90,.925]
def main():
    OUT.mkdir(exist_ok=True)
    body=pd.read_csv(BODY,parse_dates=['date']);b=pd.read_csv(STATE,parse_dates=['date']);e=pd.read_csv(ENTRY,parse_dates=['date']);permissions=pd.read_csv(PERM,parse_dates=['date'])
    signal=json.loads(RESULT.read_text(encoding='utf-8'))['signals']['IM'];snap=json.loads(SNAPSHOT.read_text(encoding='utf-8'));day=pd.Timestamp(signal['market_date'])
    assert signal['valuation_provenance']['mode']=='vip_actual' and signal['valuation_provenance']['gov10y']['mode']=='official_actual'
    assert snap['valuation_date']==str(day.date())
    pb=snap['indices']['000852']['pb_aggregate'];pe=snap['indices']['000852']['pe_aggregate_ttm'];rate=signal['valuation_provenance']['gov10y']['yield_decimal'];dividend=.009203691969088101
    pbs=(pb-1.5)/.5;erps=(.045-(1/pe-rate))/.015;divs=(.03-dividend)/.01;score=float(np.median([pbs,erps,divs]));assert abs(score-signal['score'])<1e-12
    formula=np.median(np.column_stack([(body.pb_aggregate-1.5)/.5,(.045-body.erp)/.015,(.03-body.trailing_dividend_contribution)/.01]),axis=1)
    formula_error=float(abs(formula-body.unbounded_median_knot).max());assert formula_error<1e-12
    overlap=body[['date','unbounded_median_knot','pe_aggregate_ttm','pb_aggregate']].merge(b,on='date',suffixes=('_body','_state'))
    hist_error=float(abs(overlap.unbounded_median_knot-overlap.score).max());assert hist_error<1e-12
    tier= e[['date','entry_valuation_tier']].merge(b[['date','valuation_tier']],on='date');assert (tier.entry_valuation_tier==tier.valuation_tier).all()
    # Only complete PRIOR calendar months: include certified2026-08-31,
    # never treat the frozen2026-08-17 partial-month endpoint as August month-end.
    months=b[b.date>=body.date.min()].groupby(b[b.date>=body.date.min()].date.dt.to_period('M')).tail(1)
    sample=months[months.date<day.to_period('M').to_timestamp()].tail(57).copy();assert len(sample)==57 and sample.date.max()==pd.Timestamp('2026-08-31')
    levels=np.quantile(sample.score,QUANTILES,method='linear');percentile=float((sample.score<=score).mean());assert sample.date.lt(day.to_period('M').to_timestamp()).all()
    sample[['date','score','pe_aggregate_ttm','pb_aggregate','erp','trailing_dividend_contribution']].to_csv(OUT/'current_57_month_sample.csv',index=False)
    coverage=[]
    selections={'full':e,'real':e[e.date>='2022-07-22'],'calibrated':e[e.relative_calibrated.fillna(False).astype(bool)]}
    for year,g in e.groupby(e.date.dt.year):selections['year_'+str(year)]=g
    for name,g in selections.items():
        for t in range(5):coverage.append(dict(segment=name,start=str(g.date.min().date()),end=str(g.date.max().date()),rows=len(g),tier=t,days=int(g.entry_valuation_tier.eq(t).sum()),fraction=float(g.entry_valuation_tier.eq(t).mean())))
    coverage=pd.DataFrame(coverage);coverage.to_csv(OUT/'tier_coverage.csv',index=False)
    # Continuous historical percentile follows the original causal57-month window.
    original_months=body.groupby(body.date.dt.to_period('M')).tail(1)
    diagnostic=[]
    for month,g in e.groupby(e.date.dt.to_period('M')):
        history=original_months[original_months.date<month.to_timestamp()].tail(57)
        if len(history)!=57:continue
        values=history.unbounded_median_knot.to_numpy()
        for q in g.itertuples():
            if not np.isfinite(q.valuation_score):continue
            diagnostic.append(dict(date=q.date,score=q.valuation_score,tier=q.entry_valuation_tier,percentile=float((values<=q.valuation_score).mean()),sample_max_date=str(history.date.max().date())))
    diag=pd.DataFrame(diagnostic);diag.to_csv(OUT/'historical_continuous_percentiles.csv',index=False)
    ranges=[]
    for name,g in [('real',diag[diag.date>='2022-07-22']),('calibrated',diag)]:
        for t in range(5):
            z=g[g.tier==t]
            ranges.append(dict(segment=name,tier=t,rows=len(z),percentile_min=z.percentile.min(),percentile_median=z.percentile.median(),percentile_max=z.percentile.max()))
    pd.DataFrame(ranges).to_csv(OUT/'tier_percentile_ranges.csv',index=False)
    thresholds=pd.DataFrame(dict(quantile=QUANTILES,frozen_august=FROZEN,september_causal=levels,change=levels-FROZEN));thresholds.to_csv(OUT/'current_threshold_comparison.csv',index=False)
    permission_summary=[]
    for name,g in [('real',permissions[permissions.date>='2022-07-22']),('full',permissions)]:
        permission_summary.append(dict(segment=name,rows=len(g),valuation_le1_days=int(g.val_le1.sum()),valuation_le1_fraction=float(g.val_le1.mean()),with_mom_days=int(g.val_le1_mom.sum()),with_mom_fraction=float(g.val_le1_mom.mean())))
    pd.DataFrame(permission_summary).to_csv(OUT/'entry_permission_coverage.csv',index=False)
    observations=b[b.date.isin(pd.to_datetime(['2024-08-30','2024-09-13','2026-08-14','2026-08-31']))][['date','price_close','pe_aggregate_ttm','pb_aggregate','erp','score']].copy()
    observations.to_csv(OUT/'dated_valuation_observations.csv',index=False)
    checks={'historical_formula_max_error':formula_error,'body_revalidation_overlap_score_max_error':hist_error,'body_revalidation_PE_max_error':float(abs(overlap.pe_aggregate_ttm_body-overlap.pe_aggregate_ttm_state).max()),'body_revalidation_PB_max_error':float(abs(overlap.pb_aggregate_body-overlap.pb_aggregate_state).max()),'entry_tier_revalidation_mismatches':int((tier.entry_valuation_tier!=tier.valuation_tier).sum()),'current_score':score,'current_pb_pressure':pbs,'current_erp_pressure':erps,'current_dividend_pressure':divs,'current_57month_percentile':percentile,'current_frozen_tier':int((score>=FROZEN).sum()),'current_september_relative_tier':int((score>=levels).sum()),'current_absolute_tier':int((score>=np.array([2.45,2.50,2.60])).sum())}
    meta={'scope':'valuation semantics and source parity audit only; no strategy threshold scan or production modification','market_cutoff':str(day.date()),'historical_entry_cutoff':str(e.date.max().date()),'complete_month_cutoff':str(sample.date.max().date()),'checks':checks,'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [BODY,STATE,ENTRY,PERM,RESULT,SNAPSHOT,LEDGER,Path(__file__)]},'limitations':'Current percentile based on reconstructed historical aggregatePE/PB, not all observations saved contemporaneous vintage. Current dividend still frozenAugust14, relative thresholds in formal signal frozenAugust14; newSeptember thresholds diagnostic only, not live promotion. Original historical continuous relative distribution startsOct2015; early certified valuation is excluded from that sample. No2026-09-14 current estimate or trading suggestion.'}
    (OUT/'audit.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    real=coverage[coverage.segment=='real'];le1=float(real[real.tier.le(1)].fraction.sum())
    report=f'''# IM估值与卖Put入场语义审计\n\n数据日：{day.date()}；真实PE/PB与当日中债，非9月14日当前估值。\n\n## 结论\n历史与当前评分公式一致，历史PE/PB与后续重验证重叠值一致，估值入场档位无差异。当前分数{score:.6f}，在2021-12-31..2026-08-31的57个月末样本中为{percentile:.2%}弱ECDF位置。线上冻结阈值与9月诊断更新阈值都给0档，因此本次0档并非冻结阈值造成。\n\n0/1档是保护尚未到高档的广泛区间，不等于低估值：真实986日0档{float(real[real.tier==0].fraction.iloc[0]):.2%}，0/1合计{le1:.2%}。理论85%为月末分位边界，不是每日应恰好85%的保证。历史日度覆盖存在行情阶段与动态阈值影响。\n\n## 分数拆解\nPB压力{pbs:.6f}、ERP压力{erps:.6f}、股息压力{divs:.6f}，三项中位数{score:.6f}；当前ERP为中位项。实际PE/PB变化不等于价格比例变化，不把其全部归因于盈利增长，需财报/指数编制口径进一步证据。\n\n## 月度阈值核对\n{thresholds.to_string(index=False)}\n\n## 各档覆盖\n{coverage[coverage.segment.isin(['full','real','calibrated'])].to_string(index=False)}\n\n## 原入场许可覆盖（前日信号）\n{pd.DataFrame(permission_summary).to_string(index=False)}\n\n## 日度连续分位范围\n{pd.DataFrame(ranges).to_string(index=False)}\n\n## 日期对照\n{observations.to_string(index=False)}\n\n8月14日与8月31日指数价格接近，但PE32.53降到30.46、分数2.0796降到1.9369，说明用指数涨跌代替真实估值可能给出不同档位。9月11日本地旧记录为proxy1档，真实日报0档，二者不得混用。\n\n## 审计与下一步\n{json.dumps(checks,ensure_ascii=False,indent=2)}\n\n研究判断：保留原核心Put分档；为卖Put单独定义连续分位的入场候选，再作同价格同成本回测，不把保护档标签当低估证明。下一层可预注册50%/60%/70%分位上限以及现有0/1基准，保留MOM120>=0，按相邻参数及全窗口验证；本层不执行参数选优，不晋级生产。\n\n## 限制\n{meta['limitations']}\n'''
    (OUT/'report.md').write_text(report,encoding='utf-8');print(json.dumps(checks,ensure_ascii=False,indent=2));print(coverage[coverage.segment=='real'].to_string(index=False));print(pd.DataFrame(permission_summary).to_string(index=False));print(pd.DataFrame(ranges).to_string(index=False))
if __name__=='__main__':main()
