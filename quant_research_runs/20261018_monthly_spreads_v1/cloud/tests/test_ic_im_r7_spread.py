import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import build_ic_im_v1_3_digest as digest


def test_both_mail_formats_use_shared_reasons():
    signal={"quarter_spread":dict(status="ok",near_contract="IC2609",far_contract="IC2612",
        near_price=6000,far_price=5900,points=100,ratio=100/6000,annualized=0.067,
        source_date="2026-09-04",source="CFFEX")}
    text=digest.quarter_spread_reason(signal)
    assert "+100.00点" in text and "IC2609" in text and "IC2612" in text
    assert "近季－远季" in text and "非保证收益" in text
    assert text in digest.product_reasons("IC",signal)
    assert "季月价差" in digest.reasons_html("IC",signal)


def test_missing_spread_never_renders_zero():
    assert digest.quarter_spread_reason({})=="季月价差：N/A（缺少同日完整报价）"

