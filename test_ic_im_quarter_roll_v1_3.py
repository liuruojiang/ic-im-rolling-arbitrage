from copy import deepcopy
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given, settings, strategies as st

import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_3_bot as bot
import poe_ic_im_v1_3_state as state
from migrate_ic_im_v1_3_r6_to_r7_state import migrate, validate_r6

ROOT = Path(__file__).resolve().parent


def expiry(c):
    return bot._third_friday(*bot._contract_month(c))


@pytest.mark.parametrize("product,day,preview", [("IC",15,14),("IM",17,16)])
def test_preview_intraday_close_and_next_day(product,day,preview):
    contracts = [product+m for m in ["2609","2610","2612","2703"]]
    args = (product,product+"2609",contracts)
    before=q.roll_state(*args,date(2026,9,preview),False,expiry,bot._is_exchange_trading_day)
    notice=q.roll_state(*args,date(2026,9,preview),True,expiry,bot._is_exchange_trading_day)
    intraday=q.roll_state(*args,date(2026,9,day),False,expiry,bot._is_exchange_trading_day)
    close=q.roll_state(*args,date(2026,9,day),True,expiry,bot._is_exchange_trading_day)
    assert before["core_action"] == "HOLD"
    assert notice["core_target"] == product+"2612"
    assert notice["core_eod_contract"] == intraday["core_eod_contract"] == product+"2609"
    assert close["core_eod_contract"] == product+"2612" and close["roll_confirmed"]
    held=q.roll_state(product,product+"2612",contracts,date(2026,9,day+1),True,expiry,bot._is_exchange_trading_day)
    assert held["core_action"] == "HOLD"


def test_october_holds_december_and_crossyear_next_is_march():
    r=q.roll_state("IM","IM2612",["IM2610","IM2611","IM2612","IM2703"],
                   date(2026,10,16),True,expiry,bot._is_exchange_trading_day)
    assert r["core_target"] == "IM2612" and r["next_core"] == "IM2703"


def test_missing_roll_and_missing_next_quarter_fail_closed():
    with pytest.raises(RuntimeError,match="missed"):
        q.roll_state("IC","IC2609",["IC2609","IC2612"],date(2026,9,16),True,expiry,bot._is_exchange_trading_day)
    with pytest.raises(RuntimeError,match="no later"):
        q.roll_state("IC","IC2609",["IC2609","IC2610"],date(2026,9,4),True,expiry,bot._is_exchange_trading_day)


def quotes(near=6000.,far=5900.):
    f=pd.DataFrame({"instrument":["IM2609","IM2612"],"lastprice":[near,far]})
    f.attrs.update(source="test snapshot",source_date=date(2026,9,4))
    return f


@settings(max_examples=100,derandomize=True)
@given(near=st.floats(100,10000,allow_nan=False,allow_infinity=False),
       far=st.floats(100,10000,allow_nan=False,allow_infinity=False),
       scale=st.floats(.1,10,allow_nan=False,allow_infinity=False))
def test_spread_scale_invariance(near,far,scale):
    a=q.quarter_spread("IM",quotes(near,far),date(2026,9,4),expiry)
    b=q.quarter_spread("IM",quotes(near*scale,far*scale),date(2026,9,4),expiry)
    assert a["status"]==b["status"]=="ok"
    assert a["ratio"]==pytest.approx(b["ratio"])
    assert a["points"]*scale==pytest.approx(b["points"])


@pytest.mark.parametrize("kind",["stale","nan","missing","gap","duplicate"])
def test_spread_invalid_inputs_are_na(kind):
    f=quotes()
    if kind=="stale": f.attrs["source_date"]=date(2026,9,3)
    if kind=="nan": f.loc[1,"lastprice"]=float("nan")
    if kind=="missing": f=f.iloc[:1]
    if kind=="gap": f.loc[1,"instrument"]="IM2703"
    if kind=="duplicate": f=pd.concat([f,f.iloc[:1]])
    r=q.quarter_spread("IM",f,date(2026,9,4),expiry)
    assert r["status"]=="unavailable" and "N/A" in q.format_spread(r)


def test_real_r6_migration_preserves_every_anchor(tmp_path):
    source=ROOT/"runtime/ic_im_v1_3_r6"
    if not source.exists(): pytest.skip("local runtime audit only")
    old=validate_r6(source)
    result=migrate(source,tmp_path/"r7")
    latest=state.StateStore(tmp_path/"r7").load_latest()
    assert latest["products"]==old["products"]
    assert latest["strategy_revision"]=="r7"
    assert result["old_ledger"]["digest"]==old["digest"]
    with pytest.raises(FileExistsError): migrate(source,tmp_path/"r7")


@pytest.mark.parametrize("product,candidate,folder",[
    ("IC","quarter_T3_fixed","20260904_ic_v13_full_roll_tenor_timing_v2"),
    ("IM","quarter_T1_close","20260904_im_v13_quarter_t3_close_impact_v4")])
def test_real_scan_calendar_parity(product,candidate,folder):
    p=ROOT/"quant_param_scan_runs"/folder/"roll_events.csv"
    if not p.exists(): pytest.skip("local historical scan audit only")
    events=pd.read_csv(p,parse_dates=["date"])
    events=events.loc[events.candidate.eq(candidate)]
    source=ROOT/("data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv" if product=="IC" else "data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv")
    raw=pd.read_csv(source,parse_dates=["date"])
    calendar=set(raw.date.dt.date)
    ends=raw.groupby("contract").date.max().dt.date.to_dict()
    for row in events.itertuples():
        held=getattr(row,"old_contract",getattr(row,"roll_from",None))
        # IC scan uses current contract under a distinct column; audited explicitly below.
        assert held is not None
        listing=raw.loc[raw.date.eq(row.date),"contract"].tolist()
        plan=q.roll_state(product,held,listing,row.date.date(),True,lambda c: ends[c],lambda d:d in calendar)
        assert plan["roll_confirmed"]
        assert plan["core_eod_contract"]==getattr(row,"new_contract",getattr(row,"roll_to",None))


def test_old_continuation_cannot_claim_r7_returns():
    with pytest.raises(RuntimeError,match="禁止用r6"):
        bot.latest_continuation_frame("IM",date(2026,9,4))


def test_legacy_migration_cannot_silently_generate_r7(tmp_path):
    from migrate_ic_im_v1_3_r5_to_r6_state import migrate as legacy_migrate
    with pytest.raises(RuntimeError,match="归档r6"):
        legacy_migrate(tmp_path/"r5",tmp_path/"not_r6")


def test_quarter_cannot_skip_missing_december():
    with pytest.raises(RuntimeError,match="cannot skip"):
        q.next_quarter("IM",["IM2609","IM2703"],"IM2609",expiry)
