import hashlib

import pandas as pd
import pytest

import build_ic_im_mainline_v1_2_fixed_performance as fixed
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
    assert audit["IM"]["call_actual_active_rows"] == 1652
    assert audit["IM"]["call_actual_flat_rows"] == 1104


def test_cli_atomic_writers_refuse_existing_outputs(tmp_path) -> None:
    existing = tmp_path / "existing.json"
    existing.write_text("frozen\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        combined.im._atomic_write_text(existing, "replacement\n")
    assert existing.read_text(encoding="utf-8") == "frozen\n"

    existing_csv = tmp_path / "existing.csv"
    existing_csv.write_text("frozen\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        combined.im._atomic_write_csv(pd.DataFrame({"x": [1]}), existing_csv)
    assert existing_csv.read_text(encoding="utf-8") == "frozen\n"


def test_fixed_builder_put_execution_lag_is_verified_and_no_other_mismatch() -> None:
    _curve, audit = fixed.build_im()
    timing = audit["put_target_vs_executable_actual_timing"]
    assert timing["execution_lag_rows"] == 1
    assert timing["execution_lag_dates"] == ["2023-03-06"]
    assert timing["execution_lag_details"][0]["actual_execution_date"] == "2023-03-07"
    assert timing["execution_lag_details"][0]["verified_old_contracts"] == [
        "MO2305-P-6600"
    ]
    assert timing["unexplained_mismatch_rows"] == 0
    assert audit["call_actual_active_rows"] == 1652
    assert audit["call_actual_flat_rows"] == 1104


def test_fixed_builder_reproducibility_manifest_covers_code_and_constants() -> None:
    code_sources, constants = fixed.reproducibility_sources()
    assert "build_ic_im_mainline_v1_2_fixed_performance.py" in code_sources
    assert "ic_mainline_v1_2.py" in code_sources
    assert "im_mainline_v1_2.py" in code_sources
    assert constants["im_prelisting_scenario"] == "model_avg_basis"
    assert constants["im_prelisting_basis_daily"] == 0.00038985993765572324
    assert constants["im_prelisting_basis_annual_pct"] == 10.321159572014937
    assert constants["im_prelisting_basis_postlisting_observations"] == 991
    assert constants["im_prelisting_basis_lookahead"] is True
    assert constants["im_prelisting_basis_usage"] == (
        "reference_only_not_out_of_sample_not_live_authority"
    )
    assert constants["im_variant"] == "current_4tier_mom3"
    assert constants["im_margin_buffer_rate"] == 0.30
    assert constants["builder_version"] == "ic_im_mainline_v1_2_fixed_performance_v2"
    assert constants["supersedes_builder_output"] == (
        "ic_im_mainline_v1_2_fixed_performance_v1"
    )
    assert constants["v1_output_policy"] == "immutable_read_only"


def test_fixed_builder_v2_record_discloses_prelisting_lookahead() -> None:
    disclosure = "\n".join(fixed.prelisting_basis_disclosure_lines())
    assert "daily=0.00038985993765572324" in disclosure
    assert "annual=10.321159572014937%" in disclosure
    assert "上市后991个交易日均值回填" in disclosure
    assert "含前视" in disclosure
    assert "仅为参考情景" in disclosure
    assert "不是样本外结果" in disclosure
    assert "不是实盘依据" in disclosure


def test_fixed_builder_v2_route_keeps_v1_output_read_only() -> None:
    assert fixed.VERSION == "ic_im_mainline_v1_2_fixed_performance_v2"
    assert fixed.OUTPUT.name == "ic_im_mainline_v1_2_fixed_performance_v2"
    assert fixed.STAGING.name == ".ic_im_mainline_v1_2_fixed_performance_v2.staging"
    assert fixed.V1_OUTPUT.name == "ic_im_mainline_v1_2_fixed_performance_v1"
    assert fixed.V1_OUTPUT != fixed.OUTPUT
    assert fixed.V1_OUTPUT.is_dir()


def test_combined_spec_hash_matches_sidecar() -> None:
    sidecar = combined.SPEC_PATH.with_suffix(combined.SPEC_PATH.suffix + ".sha256")
    expected = sidecar.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(combined.SPEC_PATH.read_bytes()).hexdigest()
    assert actual == expected
