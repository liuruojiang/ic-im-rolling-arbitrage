from datetime import date
from pathlib import Path
import pandas as pd
import pytest
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
ROOT = Path(__file__).resolve().parent
def expiry(c):
    return bot._third_friday(*bot._contract_month(c))


def test_stored_close_attachment_shows_all_three_pairs_and_keeps_old_signal():
    import copy
    import run_ic_im_v1_4_github_digest as digest
    latest = {"verified_day": "2026-09-16", "sequence": 0, "digest": "diagnostic",
              "signals": {"IC": {}, "IM": {}}}
    before = copy.deepcopy(latest)
    report = digest.render_stored_close_report(latest)
    assert "当月－次月" not in report and "当月－当季" not in report
    assert latest == before
    for signal in latest["signals"].values():
        signal["market_date"] = "2026-10-19"
    latest["verified_day"] = "2026-10-19"
    report = digest.render_stored_close_report(latest)
    assert report.count("当月－次月") == report.count("当月－当季") == 2


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_month_spreads_use_front_month_not_held_quarter(product):
    f = pd.DataFrame({"instrument": [product + m for m in ["2610", "2611", "2612", "2703"]],
                      "lastprice": [6100., 6075., 6000., 5900.]})
    f.attrs.update(source="frozen test", source_date="2026-10-15")
    values = q.monthly_spreads(product, f, date(2026, 10, 15), expiry)
    assert values["month_next_spread"]["points"] == 25
    assert values["month_quarter_spread"]["points"] == 100
    assert values["month_next_spread"]["ratio"] == pytest.approx(25/6100)
    assert values["month_quarter_spread"]["far_contract"] == product + "2612"
    lines = q.format_spreads({**values, "quarter_spread": q.quarter_spread(product, f, date(2026, 10, 15), expiry)})
    assert len(lines) == 3 and "近季－远季" in lines[0]
    assert "当月－次月" in lines[1] and "当月－当季" in lines[2]


def test_expiry_session_included_then_front_month_advances():
    f = pd.DataFrame({"instrument": ["IM2610", "IM2611", "IM2612", "IM2703"],
                      "lastprice": [6100., 6075., 6000., 5900.]})
    f.attrs.update(source_date="2026-10-16")
    assert q.monthly_spreads("IM", f, date(2026, 10, 16), expiry)["month_next_spread"]["near_contract"] == "IM2610"
    f.attrs["source_date"] = "2026-10-19"
    values = q.monthly_spreads("IM", f, date(2026, 10, 19), expiry)
    assert values["month_next_spread"]["near_contract"] == "IM2611"
    assert values["month_next_spread"]["points"] == 75


def test_quarter_month_overlap_and_december_crossyear():
    f = pd.DataFrame({"instrument": ["IC2612", "IC2701", "IC2703", "IC2706"],
                      "lastprice": [6100., 6125., 6000., 5900.]})
    f.attrs.update(source_date="2026-12-10")
    values = q.monthly_spreads("IC", f, date(2026, 12, 10), expiry)
    assert values["month_next_spread"]["far_contract"] == "IC2701"
    assert values["month_next_spread"]["points"] == -25
    same = values["month_quarter_spread"]
    assert same["points"] == 0 and same["same_contract"] and same["annualized"] is None
    assert "同一合约，年化不适用" in q.format_month_spread(same, "当月－当季")


@pytest.mark.parametrize("kind", ["stale", "missing", "duplicate", "nan", "zero", "inf", "skip_next", "skip_quarter"])
def test_month_spreads_reject_bad_legs_without_affecting_other_pairs(kind):
    f = pd.DataFrame({"instrument": ["IM2610", "IM2611", "IM2612", "IM2703"],
                      "lastprice": [6100., 6075., 6000., 5900.]})
    f.attrs.update(source_date="2026-10-15", listed_instruments=f.instrument.tolist())
    if kind == "stale": f.attrs["source_date"] = "2026-10-14"
    if kind == "missing": f = f.drop(index=1)
    if kind == "duplicate": f = pd.concat([f, f.iloc[[1]]])
    if kind in ("nan", "zero", "inf"): f.loc[1, "lastprice"] = {"nan": float("nan"), "zero": 0., "inf": float("inf")}[kind]
    if kind == "skip_next": f.attrs["listed_instruments"].remove("IM2611")
    if kind == "skip_quarter": f.attrs["listed_instruments"].remove("IM2612")
    values = q.monthly_spreads("IM", f, date(2026, 10, 15), expiry)
    bad_key = "month_quarter_spread" if kind == "skip_quarter" else "month_next_spread"
    assert values[bad_key]["status"] == "unavailable"
    assert "N/A" in q.format_month_spread(values[bad_key], q.MONTH_SPREAD_LABELS[bad_key])
    if kind not in ("stale", "skip_quarter"):
        assert values["month_quarter_spread"]["status"] == "ok"


@pytest.mark.parametrize("product,source", [
    ("IC", "data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv"),
    ("IM", "data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv"),
])
def test_month_spreads_independently_match_real_official_closes(product, source):
    raw = pd.read_csv(ROOT / source)
    raw = raw.loc[raw.date.eq(raw.date.max())]
    day = date.fromisoformat(str(raw.date.iloc[0]))
    f = raw.rename(columns={"contract": "instrument", "close": "lastprice"})
    f.attrs.update(source="saved official CFFEX close", source_date=day.isoformat())
    before = f.copy(deep=True)
    values = q.monthly_spreads(product, f, day, expiry)
    for key in q.MONTH_SPREAD_LABELS:
        v = values[key]
        assert v["status"] == "ok", v
        prices = raw.set_index("contract")["close"]
        assert v["points"] == pytest.approx(prices[v["near_contract"]] - prices[v["far_contract"]])
    pd.testing.assert_frame_equal(f, before)
