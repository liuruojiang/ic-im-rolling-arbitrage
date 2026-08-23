import hashlib
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import im_mainline_v1_2 as v12


def _load_a_share_v13_module():
    path = v12.A_SHARE_V13_BOT
    spec = importlib.util.spec_from_file_location("a_share_v13_parity_source", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_inherits_v11_and_records_core_only_options() -> None:
    manifest = v12.rule_manifest()
    assert manifest["parent_version"] == "im_mainline_v1_1"
    assert manifest["capital_sleeves"] == {
        "core_share": 0.5,
        "momentum_share": 0.5,
        "core_always_held": True,
    }
    assert manifest["grid"]["entry_lte"] == 1.60
    assert manifest["grid"]["exit_gte"] == 2.00
    assert manifest["parent_rules"]["put"]["mom120_negative_floor_qty"] == 3
    assert manifest["options"]["momentum_put"] is False
    assert manifest["options"]["momentum_call"] is False
    assert manifest["options"]["grid_put"] is False
    assert manifest["options"]["grid_call"] is False
    assert manifest["performance_claim"] == "none_new_v1_2_schedule_only"


def test_momentum_formula_and_config_match_a_share_v13_zz1000() -> None:
    source = _load_a_share_v13_module()
    cfg = next(item for item in source.SLEEVES if item.key == "zz1000")
    assert cfg.bias_ma == v12.MOMENTUM_POLICY.bias_ma
    assert cfg.mom_day == v12.MOMENTUM_POLICY.momentum_days
    assert cfg.weight_end == v12.MOMENTUM_POLICY.linear_weight_end
    assert cfg.score_threshold == v12.MOMENTUM_POLICY.score_threshold
    assert cfg.abs_mom_day == v12.MOMENTUM_POLICY.absolute_momentum_days
    assert cfg.abs_mom_threshold == v12.MOMENTUM_POLICY.absolute_momentum_threshold
    assert cfg.abs_filter_share == v12.MOMENTUM_POLICY.absolute_filter_share

    index = pd.bdate_range("2023-01-02", periods=180)
    close = pd.Series(
        1000.0 * np.exp(np.linspace(0.0, 0.18, len(index)) + 0.02 * np.sin(np.arange(len(index)) / 7.0)),
        index=index,
    )
    expected = source.calc_bias_momentum(
        close, cfg.bias_ma, cfg.mom_day, cfg.weight_end
    )
    actual = v12.calc_bias_momentum(close)
    np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0.0, equal_nan=True)

    source_curve = source.build_sleeve_curve(
        pd.DataFrame(
            {
                "open": close * 0.999,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": np.linspace(1_000_000, 1_500_000, len(close)),
            },
            index=index,
        ),
        cfg,
    )
    ours = v12.build_momentum_schedule(close).set_index("date")
    np.testing.assert_allclose(
        ours["momentum_signal_target"],
        source_curve["base_target_weight"],
        atol=1e-12,
        rtol=0.0,
    )
    np.testing.assert_allclose(
        ours["momentum_execution_weight"],
        source_curve["final_weight"],
        atol=1e-12,
        rtol=0.0,
    )


def test_v12_schedule_splits_capital_and_keeps_options_off_momentum_and_grid() -> None:
    dates = pd.bdate_range("2024-01-02", periods=4)
    state = pd.DataFrame(
        {
            "date": dates,
            "valuation_score": [1.60, 1.70, 2.00, 2.10],
            "absolute_tier": [0, 0, 0, 0],
            "relative_tier": [0, 0, 4, 0],
            "valuation_tier": [0, 0, 4, 0],
            "momentum_120": [-0.01, -0.01, 0.01, 0.01],
        }
    )
    momentum = pd.DataFrame(
        {
            "date": dates,
            "momentum_signal_target": [0.0, 0.5, 1.0, 0.0],
            "momentum_execution_weight": [0.0, 0.0, 0.5, 1.0],
        }
    )
    schedule = v12.build_target_schedule(state, momentum)

    assert schedule["core_im_units"].tolist() == [0.5] * 4
    assert schedule["momentum_im_units"].tolist() == [0.0, 0.0, 0.25, 0.5]
    assert schedule["grid_im_units"].tolist() == [0.0, 1.0, 1.0, 0.0]
    assert schedule["total_im_units"].tolist() == [0.5, 1.5, 1.75, 1.0]
    assert schedule["core_put_signal_qty_normalized"].tolist() == [1.5, 1.5, 2.0, 0.0]
    assert schedule["core_put_execution_qty_normalized"].tolist() == [0.0, 1.5, 1.5, 2.0]
    assert schedule["momentum_put_qty_normalized"].eq(0.0).all()
    assert schedule["grid_put_qty"].eq(0).all()
    assert schedule["core_call_target_contracts_normalized"].eq(1.0).all()
    assert schedule["momentum_call_target_contracts_normalized"].eq(0.0).all()
    assert schedule["grid_call_qty"].eq(0).all()
    assert schedule["put_covered_im_units"].eq(0.5).all()
    assert schedule["call_covered_im_units"].eq(0.5).all()


def test_rejects_same_day_momentum_execution() -> None:
    dates = pd.bdate_range("2024-01-02", periods=2)
    parent_schedule = pd.DataFrame(
        {
            "date": dates,
            "core_im_units": [1.0, 1.0],
            "grid_im_units": [0.0, 0.0],
            "total_im_units": [1.0, 1.0],
            "put_signal_target_qty": [0, 0],
            "put_execution_target_qty": [0, 0],
            "valuation_tier": [0, 0],
        }
    )
    momentum = pd.DataFrame(
        {
            "date": dates,
            "momentum_signal_target": [0.0, 1.0],
            "momentum_execution_weight": [0.0, 1.0],
        }
    )
    with pytest.raises(ValueError, match="prior-session"):
        v12.compose_from_parent_schedule(parent_schedule, momentum)


def test_local_real_artifact_audit_passes() -> None:
    schedule, audit = v12.load_authoritative_local_state()
    assert audit["start"] == "2015-04-16"
    assert audit["end"] == "2026-08-14"
    assert audit["rows"] == 2756
    assert len(schedule) == 2756
    for key in (
        "source_signal_rule_max_abs_error",
        "core_units_formula_max_abs_error",
        "momentum_units_formula_max_abs_error",
        "total_units_formula_max_abs_error",
        "grid_parent_parity_max_abs_error",
        "put_core_only_formula_max_abs_error",
    ):
        assert audit[key] <= 1e-12
    assert audit["momentum_put_nonzero_rows"] == 0
    assert audit["momentum_call_nonzero_rows"] == 0
    assert audit["normalized_four_put_without_parent_tier4_rows"] == 0


def test_frozen_spec_hash_matches_sidecar() -> None:
    expected = (v12.SPEC_PATH.with_suffix(v12.SPEC_PATH.suffix + ".sha256"))
    expected_hash = expected.read_text(encoding="utf-8").split()[0]
    actual_hash = hashlib.sha256(v12.SPEC_PATH.read_bytes()).hexdigest()
    assert actual_hash == expected_hash

