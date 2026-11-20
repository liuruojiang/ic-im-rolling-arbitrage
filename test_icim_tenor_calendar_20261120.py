"""Independent adversarial oracle for the report-only front-month spreads."""
import calendar
from datetime import date, timedelta
import copy

import pandas as pd
import pytest

import ic_im_quarter_roll_v1_3 as q


KEYS = ("near_far_month_spread", "near_near_quarter_spread", "near_far_quarter_spread")


def month_after(year, month):
    return (year + 1, 1) if month == 12 else (year, month + 1)


def contract(product, year, month):
    return f"{product}{year - 2000:02d}{month:02d}"


def expiry(c):
    year, month = 2000 + int(c[2:4]), int(c[-2:])
    fridays = [date(year, month, n) for n in range(1, calendar.monthrange(year, month)[1]+1)
               if date(year, month, n).weekday() == calendar.FRIDAY]
    return fridays[2]


def oracle(product, day, expiration=expiry):
    """Do not import production month ordinals or quarter-selection helpers."""
    year, month = day.year, day.month
    if day > expiration(contract(product, year, month)):
        year, month = month_after(year, month)
    months = [contract(product, year, month)]
    year, month = month_after(year, month)
    months.append(contract(product, year, month))
    while len(months) < 4:
        year, month = month_after(year, month)
        if month in (3, 6, 9, 12):
            months.append(contract(product, year, month))
    return months


def quotes(product, day, expiration=expiry):
    names = oracle(product, day, expiration)
    prices = [6000., 6001., 5800., 5700.]
    f = pd.DataFrame({"instrument": names[::-1], "lastprice": prices[::-1]})
    f.attrs.update(source="independent test fixture", source_date=day.isoformat(), listed_instruments=names[::-1])
    return f, names, dict(zip(names, prices))


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("month", range(1, 13))
@pytest.mark.parametrize("delta", [-1, 0, 3])
def test_calendar_and_arithmetic_matrix(product, month, delta):
    day = expiry(contract(product, 2027, month)) + timedelta(days=delta)
    f, names, prices = quotes(product, day)
    before = f.copy(deep=True)
    attrs = copy.deepcopy(f.attrs)
    results = q.listed_tenor_spreads(product, f, day, expiry)
    assert tuple(results) == KEYS
    for key, far in zip(KEYS, names[1:]):
        r = results[key]
        assert r["status"] == "ok", r
        assert (r["near_contract"], r["far_contract"]) == (names[0], far)
        assert len(set(names)) == 4
        assert r["near_expiry"] == expiry(names[0]).isoformat()
        assert r["far_expiry"] == expiry(far).isoformat()
        gap = (expiry(far) - expiry(names[0])).days
        points = prices[names[0]] - prices[far]
        assert r["expiry_gap_days"] == gap > 0
        assert r["points"] == points
        assert r["ratio"] == pytest.approx(points / prices[names[0]])
        assert r["annualized"] == pytest.approx(points / prices[names[0]] * 365 / gap)
    # This oracle intentionally supplies synthetic third-Friday dates beyond
    # the authorized exchange-calendar horizon. Do not treat it as live
    # calendar evidence; rendering is verified separately below.
    pd.testing.assert_frame_equal(f, before)
    assert f.attrs == attrs


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("day,months", [(date(2026, 11, 19), ("2611", "2612", "2703", "2706")),
                                       (date(2026, 11, 20), ("2611", "2612", "2703", "2706")),
                                       (date(2026, 11, 23), ("2612", "2701", "2703", "2706")),
                                       (date(2027, 1, 18), ("2702", "2703", "2706", "2709")),
                                       (date(2027, 4, 19), ("2705", "2706", "2709", "2712"))])
def test_exact_user_examples_and_new_listing_dates(product, day, months):
    f, names, _ = quotes(product, day)
    assert names == [product + m for m in months]
    signal = q.monthly_spreads(product, f, day, expiry)
    if day >= date(2026, 11, 20):
        assert set(signal) == set(KEYS)
        lines = q.format_spreads({**signal, "market_date": day}, product=product)
        assert len(lines) == 3
        for key, expected, line in zip(KEYS, names[1:], lines):
            assert signal[key]["near_contract"] == names[0]
            assert signal[key]["far_contract"] == expected
            assert names[0] in line and expected in line and "N/A" not in line
            assert "期限贴水" in line and "近季－远季" not in line
    else:
        assert not set(KEYS) & signal.keys()
        assert set(signal) == {"month_next_spread", "month_quarter_spread"}


@pytest.mark.parametrize("day", [date(2027, 1, 15), date(2027, 1, 18), date(2027, 1, 19)])
def test_adjusted_expiry_injected_from_exchange_calendar(day):
    def adjusted(c):
        return date(2027, 1, 18) if c.endswith("2701") else expiry(c)
    f, names, _ = quotes("IM", day, adjusted)
    result = q.listed_tenor_spreads("IM", f, day, adjusted)
    assert all(r["status"] == "ok" for r in result.values())
    assert [r["far_contract"] for r in result.values()] == names[1:]
    assert all(r["current_month_expiry"] == "2027-01-18" for r in result.values())


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("day,expected_ok", [(date(2026, 11, 20), [True, True, True]),
    (date(2026, 11, 23), [True, True, True]), (date(2027, 2, 5), [False, False, False]),
    (date(2027, 2, 14), [False, False, False]), (date(2027, 2, 15), [True, True, True]),
    (date(2027, 7, 19), [True, True, False]), (date(2027, 9, 15), [False, False, False])])
def test_rendering_distinguishes_approved_calendar_from_synthetic_generation(product, day, expected_ok):
    f, _, _ = quotes(product, day)
    r = q.listed_tenor_spreads(product, f, day, expiry)
    assert all(v["status"] == "ok" for v in r.values())
    lines = q.format_tenor_spreads({**r, "market_date": day}, product=product)
    assert ["N/A" not in line for line in lines] == expected_ok, lines


def test_same_month_but_wrong_expiry_date_does_not_render():
    day = date(2026, 11, 20)
    f, names, _ = quotes("IM", day)
    def wrong(c):
        return expiry(c) + timedelta(days=3) if c == names[-1] else expiry(c)
    result = q.listed_tenor_spreads("IM", f, day, wrong)
    assert all(r["status"] == "ok" for r in result.values())
    lines = q.format_tenor_spreads({**result, "market_date": day}, product="IM")
    assert ["N/A" not in line for line in lines] == [True, True, False]


@pytest.mark.parametrize("leg", range(4))
@pytest.mark.parametrize("fault", ["missing quote", "missing listing", "duplicate quote", "nan", "infinity", "zero", "negative", "boolean", "text"])
def test_leg_faults_never_reanchor_or_substitute(leg, fault):
    day = date(2026, 11, 20)
    f, names, _ = quotes("IM", day)
    victim = names[leg]
    i = f.index[f.instrument.eq(victim)][0]
    f["lastprice"] = f["lastprice"].astype(object)
    if fault == "missing quote":
        f = f.drop(index=i)
    elif fault == "missing listing":
        f.attrs["listed_instruments"].remove(victim)
    elif fault == "duplicate quote":
        f = pd.concat([f, f.loc[[i]]], ignore_index=True)
    else:
        f.at[i, "lastprice"] = {"nan": float("nan"), "infinity": float("inf"), "zero": 0,
                                "negative": -100, "boolean": True, "text": "6000"}[fault]
    # Include attractive but unintended substitutes, even if the intended leg is missing.
    extra = {"instrument": "IM2709", "lastprice": 5555.0}
    attrs = copy.deepcopy(f.attrs)
    f = pd.concat([f, pd.DataFrame([extra])], ignore_index=True)
    f.attrs.update(attrs)
    f.attrs["listed_instruments"].append(extra["instrument"])
    r = q.listed_tenor_spreads("IM", f, day, expiry)
    for n, key in enumerate(KEYS, 1):
        v = r[key]
        assert v["near_contract"] == names[0]
        assert v["far_contract"] == names[n]
        assert v["status"] == ("unavailable" if leg in (0, n) else "ok"), v


@pytest.mark.parametrize("source_date", ["2026-11-19", "2026-11-21", "2026-11-20T15:00:00", "", None])
def test_source_day_is_exact(source_date):
    day = date(2026, 11, 20)
    f, _, _ = quotes("IC", day)
    f.attrs["source_date"] = source_date
    result = q.listed_tenor_spreads("IC", f, day, expiry)
    assert all(r["status"] == "unavailable" for r in result.values())


def test_expiry_failure_does_not_poison_other_pairs():
    day = date(2026, 11, 20)
    f, names, _ = quotes("IC", day)
    def broken(c):
        if c == names[-1]:
            raise RuntimeError("calendar unavailable")
        return expiry(c)
    result = q.listed_tenor_spreads("IC", f, day, broken)
    assert [a["status"] for a in result.values()] == ["ok", "ok", "unavailable"]


def test_effective_date_entrypoint_isolates_expiry_failure_to_affected_spread():
    day = date(2026, 11, 20)
    f, names, _ = quotes("IC", day)
    def broken(c):
        if c == names[-1]:
            raise RuntimeError("calendar unavailable")
        return expiry(c)
    # This is the producer integration entrypoint, not just the new helper.
    result = q.monthly_spreads("IC", f, day, broken)
    assert [result[key]["status"] for key in KEYS] == ["ok", "ok", "unavailable"]


def test_wrong_product_isolated():
    day = date(2026, 11, 20)
    f, _, _ = quotes("IC", day)
    assert all(r["status"] == "unavailable" for r in q.listed_tenor_spreads("IM", f, day, expiry).values())
