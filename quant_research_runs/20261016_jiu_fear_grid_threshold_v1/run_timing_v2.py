"""Pre-registered equal-source-timing comparison; no new threshold selection."""
import importlib.util, json
from pathlib import Path
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('jiu_frozen_study',HERE/'run_study.py')
study=importlib.util.module_from_spec(spec);spec.loader.exec_module(study)

def main():
    p=HERE/'timing_sensitivity_v2_spec.md'
    assert study.sha(p)==(HERE/'timing_sensitivity_v2_spec.md.sha256').read_text().strip()
    assert (study.OUT/'result_manifest.json').exists(),'Finish v1 first'
    chosen=json.loads((study.OUT/'selected_threshold.json').read_text(encoding='utf-8'))['selected']
    ctx=study.load();j,b=ctx[-3:-1]
    calendar=pd.Index(sorted(ctx[3].index.strftime('%Y-%m-%d').unique()))
    assert calendar.is_unique and calendar.is_monotonic_increasing
    jb=j.reindex(calendar).shift(1);bb=b.reindex(calendar).shift(1)
    diagnostic_spec=HERE/'timing_diagnostic_v3_spec.md'
    assert study.sha(diagnostic_spec)==(HERE/'timing_diagnostic_v3_spec.md.sha256').read_text().strip()
    rows=[];checks={}
    for product in ('IC','IM'):
        dates=ctx[5][product].signal_date.astype(str)
        ja=j.shift(1).reindex(dates).to_numpy(float);jc=jb.reindex(dates).to_numpy(float)
        assert np.array_equal(np.isnan(ja),np.isnan(jc)) and np.allclose(ja,jc,equal_nan=True,rtol=0,atol=0)
        checks[product]={'jiu_lag_calendars_equal':True,'baifenwei_missing_signal_dates':dates[bb.reindex(dates).isna().to_numpy()].tolist()}
        d,events,sig=study.runone(product,'BFW_LAG1_25_50',25,50,bb,ctx)
        for split in study.SPLITS:
            rows.append(dict(product=product,label='BFW_LAG1_25_50',entry=25,exit=50,**study.metrics(d,sig,events,split)))
        if chosen is None:
            d,events,sig=study.runone(product,'JIU_5_40_LAG1_DIAGNOSTIC',5,40,jb,ctx)
            for split in study.SPLITS:
                rows.append(dict(product=product,label='JIU_5_40_LAG1_DIAGNOSTIC',entry=5,exit=40,**study.metrics(d,sig,events,split)))
    pd.DataFrame(rows).to_csv(study.OUT/'timing_v2_metrics.csv',index=False)
    study.dump(study.OUT/'timing_v2_checks.json',{'checks':checks,'chosen_from_v1_training':chosen,'spec_sha256':study.sha(p),'diagnostic_v3_spec_sha256':study.sha(diagnostic_spec),'fixed_boundary_diagnostic_is_not_selected':chosen is None})
    print('TIMING_V2_COMPLETED',flush=True)

if __name__=='__main__':main()
