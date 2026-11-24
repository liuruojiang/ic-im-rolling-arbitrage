from copy import deepcopy
from datetime import date

import pytest

import ic_im_quarter_roll_v1_3 as q
from test_icim_tenor_calendar_20261120 import quotes, expiry


def close_signal(product="IC", day=date(2026, 11, 25), spot=6100.0):
    frame, contracts, prices = quotes(product, day)
    signal = dict(product=product, market_date=day, close_confirmed=True,
                  index_price=spot, future_quote_date=day.isoformat(),
                  future_quote_source=frame.attrs["source"],
                  **q.listed_tenor_spreads(product, frame, day, expiry))
    return signal, contracts, prices


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("spot", [6100., 5900.])
def test_four_roles_arithmetic_and_existing_spreads_preserved(product, spot):
    signal, contracts, prices = close_signal(product, spot=spot)
    original = deepcopy(signal)
    lines = q.format_spreads(signal, product=product)
    assert lines[:3] == q.format_tenor_spreads(signal, product=product)
    assert len(lines) == 7
    for label, contract, line in zip(q.INDEX_BASIS_LABELS, contracts, lines[3:]):
        days = (expiry(contract) - signal["market_date"]).days
        points = spot - prices[contract]
        ratio = points / spot
        assert label in line and contract in line and q.INDEX_NAMES[product] in line
        assert f"{points:+.2f}点" in line and f"{ratio:+.2%}" in line
        assert f"距到期 {days}天" in line and f"{ratio * 365 / days:+.2%}/年" in line
    assert signal == original


def test_expiry_day_displays_raw_discount_without_annualizing_zero_days():
    signal, contracts, _ = close_signal(day=date(2026, 12, 18))
    lines = q.format_index_basis(signal)
    assert contracts[0] == "IC2612" and "年化 N/A（交割当日）" in lines[0]
    assert "距到期 0天" in lines[0] and "+1.64%" in lines[0]
    assert all("N/A" not in line for line in lines[1:])


def test_old_archive_and_intraday_display_unchanged():
    old, _, _ = close_signal(day=date(2026, 11, 23))
    assert q.format_spreads(old) == q.format_tenor_spreads(old)
    live, _, _ = close_signal()
    live["close_confirmed"] = False
    assert q.format_spreads(live) == q.format_tenor_spreads(live)


@pytest.mark.parametrize("spot", [None, True, "6000", float("nan"), float("inf"), -1, 0])
def test_invalid_index_is_never_displayed(spot):
    signal, _, _ = close_signal(spot=spot)
    assert all("N/A" in line for line in q.format_index_basis(signal))


@pytest.mark.parametrize("field,value", [("future_quote_date", "2026-11-24"),
                                         ("future_quote_source", "another source"),
                                         ("product", "IM"), ("close_confirmed", False)])
def test_inconsistent_signal_rejected(field, value):
    signal, _, _ = close_signal()
    signal[field] = value
    assert all("N/A" in v for v in q.format_index_basis(signal, product="IC"))


def test_missing_far_leg_does_not_hide_remaining_verified_legs():
    signal, _, _ = close_signal()
    del signal["near_far_month_spread"]
    lines = q.format_index_basis(signal)
    assert ["N/A" in line for line in lines] == [False, True, False, False]


def test_conflicting_front_anchor_never_mixes_snapshots():
    signal, _, _ = close_signal()
    value = signal["near_far_month_spread"]
    value["near_price"] += 10
    value["points"] = value["near_price"] - value["far_price"]
    value["ratio"] = value["points"] / value["near_price"]
    value["annualized"] = value["ratio"] * 365 / value["expiry_gap_days"]
    assert all("N/A" in line for line in q.format_index_basis(signal))
