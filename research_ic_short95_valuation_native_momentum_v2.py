from pathlib import Path
import inspect,json
import pandas as pd
import research_ic_short95_native_momentum_v1 as parent
import research_ic_short95_put_mom_positive_valuation_tier_expansion_v1 as prior

OUT=Path('quant_param_scan_runs/20260914_ic_short95_valuation_native_momentum_v2')

def main():
    p=prior.permissions().set_index('date')
    # Raw score is unshifted in the historical permission table.
    val=(p.unbounded_median_knot.notna() & p.unbounded_median_knot.lt(1.95)).shift(1,fill_value=False)
    retained=p.mom_val_lt195
    (OUT/'preregister.json').write_text(json.dumps(dict(valuation='score<1.95, previous close',variants=['valuation','valuation_native_momentum','valuation_mom120','valuation_mom120_native_momentum'],strike=.95),indent=2),encoding='utf-8')
    src=inspect.getsource(parent.main)
    src=src.replace("weights.to_csv(OUT/'native_momentum_schedule.csv.gz',index=False)","weights.to_csv(OUT/'native_momentum_schedule.csv.gz',index=False)")
    src=src.replace("for name,permission in [('native_momentum',allowed),('ungated',pd.Series(True,index=allowed.index))]:","for name,permission in [('valuation',val),('valuation_native_momentum',val & allowed.reindex(val.index,fill_value=False)),('valuation_mom120',retained),('valuation_mom120_native_momentum',retained & allowed.reindex(retained.index,fill_value=False))]:")
    src=src.replace("if name=='ungated':","if name=='valuation_mom120':")
    src=src.replace("saved=pd.read_csv(real.OUT/'daily.csv.gz' if area=='real' else model.OUT/'post_assignment_ic_daily.csv.gz',parse_dates=['date'])","saved=pd.read_csv(prior.OUT/(area+'_mom_val_lt195_daily.csv.gz'),parse_dates=['date'])")
    src=src.replace("baseline={'candidate':'real_ungated'}","baseline={'candidate':'real_valuation_mom120'}")
    src=src.replace('no_valuation=True','no_valuation=False')
    src=src.replace('IC原1.3动量开仓许可卖95%Put','IC卖95%Put：估值过滤下的原1.3动量条件复验')
    src=src.replace('固定95%，不优化参数，无独立OOS。','固定95%及估值风险分数<1.95（0/1档），不优化参数，无独立OOS。四条路径分别为仅估值、估值＋原1.3动量、估值＋MOM120≥0、估值＋MOM120≥0＋原1.3动量。所有条件以前一收盘评估；已有仓位不因条件失效退出。此前无过滤对照不用于本次结论。')
    ns=dict(vars(parent));ns.update(OUT=OUT,val=val,retained=retained,prior=prior,__file__=__file__)
    (OUT/'executed_harness.py').write_text(src,encoding='utf-8')
    exec(compile(src,str(OUT/'executed_harness.py'),'exec'),ns);ns['main']()
    pd.DataFrame(dict(valuation=val,valuation_mom120=retained)).to_csv(OUT/'valuation_permissions.csv',index_label='date')
    spec=json.loads((OUT/'spec.json').read_text());spec.update(no_valuation=False,valuation='previous close unbounded_median_knot <1.95; tiers0/1',variants=['valuation','valuation_native_momentum','valuation_mom120','valuation_mom120_native_momentum']);(OUT/'spec.json').write_text(json.dumps(spec,indent=2),encoding='utf-8')
if __name__=='__main__':main()
