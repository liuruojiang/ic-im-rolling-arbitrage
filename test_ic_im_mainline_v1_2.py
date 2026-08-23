import hashlib

import ic_im_mainline_v1_2 as combined


def test_combined_manifest_contains_both_research_legs() -> None:
    manifest = combined.rule_manifest()
    assert manifest["version"] == "ic_im_mainline_v1_2"
    assert manifest["status"] == "research_candidate_not_live_authority"
    assert manifest["products"]["IC"]["version"] == "ic_mainline_v1_2"
    assert manifest["products"]["IM"]["version"] == "im_mainline_v1_2"
    assert manifest["cross_product_capital_allocation"] == "not_defined"
    assert manifest["cross_product_performance"] == "not_claimed"
    assert manifest["orders"] == "not_generated"


def test_combined_local_audit_aligns_ic_and_im() -> None:
    schedules, audit = combined.load_authoritative_local_state()
    assert audit["start"] == "2015-04-16"
    assert audit["end"] == "2026-08-14"
    assert audit["rows_per_product"] == 2756
    assert audit["date_index_parity"] is True
    assert audit["orders_generated"] is False
    assert len(schedules["IC"]) == len(schedules["IM"]) == 2756
    assert audit["IC"]["status"] == "research_candidate_not_live_authority"
    assert audit["IM"]["status"] == "research_candidate_not_live_authority"
    assert audit["IC"]["call_nonzero_rows"] == 0
    assert audit["IM"]["momentum_call_nonzero_rows"] == 0


def test_combined_spec_hash_matches_sidecar() -> None:
    sidecar = combined.SPEC_PATH.with_suffix(combined.SPEC_PATH.suffix + ".sha256")
    expected = sidecar.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(combined.SPEC_PATH.read_bytes()).hexdigest()
    assert actual == expected

