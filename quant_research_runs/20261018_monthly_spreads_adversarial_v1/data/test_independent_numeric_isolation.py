"""Fixed-input independent regressions: new report metrics must fail locally."""
from pathlib import Path
import sys
from datetime import date
import pandas as pd
import numpy as np
import pytest
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as digest

def frame():
    f=pd.DataFrame({'instrument':['IM2610','IM2611','IM2612','IM2703'],
                    'lastprice':pd.Series([6100.,6075.,6000.,5900.],dtype=object)})
    f.attrs.update(source='independent frozen inputs',source_date='2026-10-15',listed_instruments=f.instrument.tolist())
    return f

def values(f):
    return q.monthly_spreads('IM',f,date(2026,10,15),lambda c:bot._third_friday(*bot._contract_month(c)))

@pytest.mark.parametrize('bad',[True,np.bool_(True),10**400],ids=['boolean-price','numpy-boolean-price','overflow-int-price'])
def test_bad_month_next_input_must_not_block_month_quarter(bad):
    f=frame()
    f.at[1,'lastprice']=bad
    v=values(f)
    assert v['month_next_spread']['status']=='unavailable'
    assert v['month_quarter_spread']['status']=='ok'
    assert v['month_quarter_spread']['points']==100

def test_finite_positive_prices_with_annual_overflow_fail_one_group_only():
    f=frame()
    f.at[0,'lastprice']=1.0
    f.at[1,'lastprice']=1e308
    v=values(f)
    assert v['month_next_spread']['status']=='unavailable'
    assert v['month_quarter_spread']['status']=='ok'

def test_finite_positive_prices_with_ratio_overflow_fail_both_groups():
    f=frame()
    f.at[0,'lastprice']=5e-324
    f.at[1,'lastprice']=1e308
    assert all(r['status']=='unavailable' for r in values(f).values())

def test_overflow_diagnostics_survive_json_normalization_and_stored_report():
    f=frame()
    f.at[0,'lastprice']=1.0
    f.at[1,'lastprice']=1e308
    saved=state._jsonable(values(f))
    report=digest.render_stored_close_report(dict(verified_day='2026-10-15',sequence=0,digest='independent-test',
        signals={'IC':{},'IM':{'market_date':'2026-10-15',**saved}}))
    assert 'N/A' in report
    assert '当月－当季' in report
