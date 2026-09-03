import hashlib

import pandas as pd
import pytest

import build_ic_im_mainline_v1_3_fixed_performance as fixed
import ic_im_mainline_v1_3 as combined
import ic_im_mainline_v1_2 as previous


def test_combined_manifest_contains_both_research_legs() -> None:
    manifest = combined.rule_manifest()
    assert manifest["version"] == "ic_im_mainline_v1_3_r5"
    assert manifest["status"] == "research_candidate_not_live_authority"
    assert manifest["products"]["IC"]["version"] == "ic_mainline_v1_3"
    assert manifest["products"]["IM"]["version"] == "im_mainline_v1_3"
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


def test_v13_changes_both_momentum_sleeves_and_keeps_parent_components_exact() -> None:
    old_schedules, _ = previous.load_authoritative_local_state()
    new_schedules, _ = combined.load_authoritative_local_state()
    assert (
        new_schedules["IC"]["momentum_execution_weight"]
        - old_schedules["IC"]["momentum_execution_weight"]
    ).abs().gt(1e-12).sum() == 1114
    for column in (
        "date",
        "core_ic_units",
        "grid_ic_units",
        "core_put_target_delta",
        "grid_put_target_delta",
        "call_target_contracts",
    ):
        pd.testing.assert_series_equal(
            new_schedules["IC"][column],
            old_schedules["IC"][column],
            check_names=False,
        )

    inherited_im_columns = [
        "date",
        "tri_close_all",
        "momentum_120",
        "valuation_score",
        "absolute_tier",
        "effective_month",
        "relative_calibrated",
        "relative_sample_months",
        "relative_window_start",
        "relative_window_end",
        "threshold_1",
        "threshold_2",
        "threshold_3",
        "threshold_4",
        "relative_tier",
        "valuation_tier",
        "mom120_active",
        "mom120_floor_qty",
        "target_qty",
        "put_signal_target_qty",
        "put_execution_target_qty",
        "grid_held_before_open",
        "grid_executed_at_open",
        "grid_signal_at_close",
        "grid_held_eod",
        "core_im_units",
        "grid_im_units",
        "put_covered_im_units",
        "call_covered_im_units",
        "grid_put_qty",
        "grid_call_qty",
        "parent_core_im_units",
        "parent_grid_im_units",
        "parent_total_im_units",
        "parent_put_signal_target_qty",
        "parent_put_execution_target_qty",
        "core_put_signal_qty_normalized",
        "core_put_execution_qty_normalized",
        "momentum_put_qty_normalized",
        "core_call_covered_im_units",
        "core_call_coverage_capacity_contracts_normalized",
        "core_call_actual_target_contracts_normalized",
        "core_call_target_contracts_normalized",
        "actual_call_state_available",
        "momentum_call_target_contracts_normalized",
        "call_active",
        "call_contract",
        "call_expiry",
        "threat_roll_count",
        "threat_entry_blocked",
    ]
    pd.testing.assert_frame_equal(
        new_schedules["IM"][inherited_im_columns],
        old_schedules["IM"][inherited_im_columns],
    )
    assert (
        new_schedules["IM"]["margin_buffer_fraction"]
        - 0.30 * new_schedules["IM"]["total_im_units"]
    ).abs().max() <= 1e-12


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
    assert "build_ic_im_mainline_v1_3_fixed_performance.py" in code_sources
    assert "ic_mainline_v1_3.py" in code_sources
    assert "im_mainline_v1_3.py" in code_sources
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
    assert constants["builder_version"] == "ic_im_mainline_v1_3_fixed_performance_v5"
    assert constants["supersedes_builder_output"] == (
        "ic_im_mainline_v1_3_fixed_performance_v4"
    )
    assert constants["prior_output_policy"] == "immutable_read_only"


def test_fixed_builder_v2_record_discloses_prelisting_lookahead() -> None:
    disclosure = "\n".join(fixed.prelisting_basis_disclosure_lines())
    assert "daily=0.00038985993765572324" in disclosure
    assert "annual=10.321159572014937%" in disclosure
    assert "上市后991个交易日均值回填" in disclosure
    assert "含前视" in disclosure
    assert "仅为参考情景" in disclosure
    assert "不是样本外结果" in disclosure
    assert "不是实盘依据" in disclosure


def test_fixed_builder_v13_route_keeps_prior_v13_output_read_only() -> None:
    assert fixed.VERSION == "ic_im_mainline_v1_3_fixed_performance_v5"
    assert fixed.OUTPUT.name == "ic_im_mainline_v1_3_fixed_performance_v5"
    assert fixed.STAGING.name == ".ic_im_mainline_v1_3_fixed_performance_v5.staging"
    assert fixed.PRIOR_OUTPUT.name == "ic_im_mainline_v1_3_fixed_performance_v4"
    assert fixed.PRIOR_OUTPUT != fixed.OUTPUT
    assert fixed.PRIOR_OUTPUT.is_dir()


def test_fixed_v5_ic_put_outputs_match_the_executed_layers() -> None:
    schedule = pd.read_csv(fixed.OUTPUT / "ic_put_target_schedule.csv.gz")
    trades = pd.read_csv(fixed.OUTPUT / "ic_put_trades.csv.gz")
    assert len(schedule) == schedule["execution_date"].nunique() == 2756
    assert schedule.groupby("layer").size().to_dict() == {"model": 1810, "real": 946}
    assert trades.groupby("layer").size().to_dict() == {"model": 185, "real": 95}
    actual_dates = pd.to_datetime(trades["actual_execution_date"])
    assert actual_dates[trades["layer"].eq("model")].max() < fixed.ic_put.v1.REAL_START
    assert actual_dates[trades["layer"].eq("real")].min() >= fixed.ic_put.v1.REAL_START


def test_fixed_v5_returns_are_byte_equivalent_to_v4() -> None:
    for product in ("ic", "im"):
        old = pd.read_csv(fixed.PRIOR_OUTPUT / f"{product}_daily.csv.gz")
        new = pd.read_csv(fixed.OUTPUT / f"{product}_daily.csv.gz")
        pd.testing.assert_frame_equal(new[["date", "ret"]], old[["date", "ret"]])


def test_combined_spec_hash_matches_sidecar() -> None:
    sidecar = combined.SPEC_PATH.with_suffix(combined.SPEC_PATH.suffix + ".sha256")
    expected = sidecar.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(combined.SPEC_PATH.read_bytes()).hexdigest()
    assert actual == expected
