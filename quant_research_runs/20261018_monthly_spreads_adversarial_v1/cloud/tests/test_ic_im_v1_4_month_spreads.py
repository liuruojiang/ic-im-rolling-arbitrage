import copy
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest


def test_month_spreads_in_text_and_html_without_changing_signal():
    signal = {"month_next_spread": dict(status="ok", near_contract="IC2610", far_contract="IC2611",
              near_price=6100, far_price=6075, points=25, ratio=25/6100, annualized=25/6100*365/35,
              near_expiry="2026-10-16", far_expiry="2026-11-20", expiry_gap_days=35,
              same_contract=False, source_date="2026-10-15", source="CFFEX"),
              "month_quarter_spread": dict(status="ok", near_contract="IC2610", far_contract="IC2612",
              near_price=6100, far_price=6000, points=100, ratio=100/6100, annualized=100/6100*365/63,
              near_expiry="2026-10-16", far_expiry="2026-12-18", expiry_gap_days=63,
              same_contract=False, source_date="2026-10-15", source="CFFEX")}
    before = copy.deepcopy(signal)
    lines = digest.month_spread_reasons(signal)
    assert "当月－次月" in lines[0] and "+25.00点" in lines[0]
    assert "当月－当季" in lines[1] and "+100.00点" in lines[1]
    for line in lines:
        assert line in digest.product_reasons("IC", signal)
        assert line in digest.reasons_html("IC", signal)
    assert signal == before


def test_historical_missing_is_na_and_same_contract_has_no_annualized_rate():
    assert all("N/A" in line and "+0.00点" not in line for line in digest.month_spread_reasons({}))
    signal = {"month_quarter_spread": dict(status="ok", near_contract="IM2612", far_contract="IM2612",
              near_price=6000, far_price=6000, points=0, ratio=0, annualized=None,
              near_expiry="2026-12-18", far_expiry="2026-12-18", expiry_gap_days=0,
              same_contract=True, source_date="2026-12-10", source="CFFEX")}
    text = digest.month_spread_reasons(signal)[1]
    assert "+0.00点" in text and "同一合约，年化不适用" in text
    assert text in digest.reasons_html("IM", signal)


def test_archived_reasons_unchanged_and_new_missing_quotes_are_na():
    old = {"market_date": "2026-10-09"}
    assert not any("月度价差" in line for line in digest.product_reasons("IC", old))
    new = {"market_date": "2026-10-19"}
    assert sum("月度价差" in line and "N/A" in line for line in digest.product_reasons("IC", new)) == 2


@pytest.mark.parametrize("field,invalid", [
    ("near_price", True), ("far_price", None), ("points", float("inf")),
    ("ratio", float("nan")), ("annualized", None), ("annualized", 1e300),
    ("source_date", "2026-10-14"), ("near_contract", "IM2610"),
    ("far_contract", "IC2612"), ("same_contract", True),
    ("expiry_gap_days", 30), ("points", 999), ("near_expiry", "2026-09-18"), ("source", None),
])
def test_malformed_display_group_is_isolated_in_both_mail_formats(field, invalid):
    good = dict(status="ok", near_contract="IC2610", far_contract="IC2611",
                near_price=6100., far_price=6075., points=25., ratio=25/6100,
                annualized=25/6100*365/35, same_contract=False,
                near_expiry="2026-10-16", far_expiry="2026-11-20", expiry_gap_days=35,
                source_date="2026-10-15", source="synthetic boundary test")
    bad = {**good, field: invalid}
    signal = {"market_date": "2026-10-15", "month_next_spread": bad,
              "month_quarter_spread": {**good, "far_contract": "IC2612", "far_price": 6000.,
              "points": 100., "ratio": 100/6100, "annualized": 100/6100*365/63,
              "far_expiry": "2026-12-18", "expiry_gap_days": 63}}
    before = copy.deepcopy(signal)
    lines = digest.month_spread_reasons(signal, "IC")
    assert "N/A" in lines[0] and "+100.00点" in lines[1]
    assert "N/A" in digest.reasons_html("IC", signal)
    assert signal == before


@pytest.mark.parametrize("missing", ["source", "source_date", "near_contract", "far_contract", "near_price",
    "far_price", "points", "ratio", "annualized", "same_contract", "near_expiry", "far_expiry", "expiry_gap_days"])
def test_every_required_month_spread_key_missing_renders_na(missing):
    value = dict(status="ok", near_contract="IC2610", far_contract="IC2611",
                 near_price=6100., far_price=6075., points=25., ratio=25/6100,
                 annualized=25/6100*365/35, same_contract=False,
                 near_expiry="2026-10-16", far_expiry="2026-11-20", expiry_gap_days=35,
                 source_date="2026-10-15", source="synthetic boundary test")
    del value[missing]
    signal = {"market_date": "2026-10-15", "month_next_spread": value}
    assert "N/A" in digest.month_spread_reasons(signal, "IC")[0]
    assert "N/A" in digest.reasons_html("IC", signal)
