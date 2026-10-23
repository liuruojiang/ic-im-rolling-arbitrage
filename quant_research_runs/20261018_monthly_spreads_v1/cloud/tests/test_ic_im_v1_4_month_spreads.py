import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_ic_im_v1_4_digest as digest


def test_month_spreads_in_text_and_html_without_changing_signal():
    signal = {"month_next_spread": dict(status="ok", near_contract="IC2610", far_contract="IC2611",
              near_price=6100, far_price=6075, points=25, ratio=25/6100, annualized=.05,
              same_contract=False, source_date="2026-10-15", source="CFFEX"),
              "month_quarter_spread": dict(status="ok", near_contract="IC2610", far_contract="IC2612",
              near_price=6100, far_price=6000, points=100, ratio=100/6100, annualized=.1,
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
              same_contract=True, source_date="2026-12-10", source="CFFEX")}
    text = digest.month_spread_reasons(signal)[1]
    assert "+0.00点" in text and "同一合约，年化不适用" in text
    assert text in digest.reasons_html("IM", signal)


def test_archived_reasons_unchanged_and_new_missing_quotes_are_na():
    old = {"market_date": "2026-10-09"}
    assert not any("月度价差" in line for line in digest.product_reasons("IC", old))
    new = {"market_date": "2026-10-19"}
    assert sum("月度价差" in line and "N/A" in line for line in digest.product_reasons("IC", new)) == 2
