import hashlib
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import im_mainline_v1_3 as v13


@pytest.mark.parametrize("mutation", ["reverse", "duplicate"])
def test_ohlcv_dates_must_be_unique_and_strictly_increasing(mutation) -> None:
    frame = pd.read_csv(v13.CSI1000_OHLCV_PATH, parse_dates=["date"])
    if mutation == "reverse":
        frame = frame.iloc[::-1]
        message = "strictly increasing"
    else:
        frame = pd.concat([frame, frame.iloc[[-1]]], ignore_index=True)
        message = "duplicate"
    with pytest.raises(ValueError, match=message):
        v13.validate_ohlcv(frame)


def _load_a_share_v13_module():
    path = v13.A_SHARE_V13_BOT
    spec = importlib.util.spec_from_file_location("a_share_v13_parity_source", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_inherits_v12_and_records_core_only_options() -> None:
    manifest = v13.rule_manifest()
    assert manifest["parent_version"] == "im_mainline_v1_2"
    assert manifest["component_parent_version"] == "im_mainline_v1_1"
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
    assert manifest["performance_claim"] == "versioned_v1_3_fixed_reference_built_separately"


def test_momentum_formula_and_config_match_a_share_v13_zz1000() -> None:
    source = _load_a_share_v13_module()
    cfg = next(item for item in source.SLEEVES if item.key == "zz1000")
    assert cfg.bias_ma == v13.MOMENTUM_POLICY.bias_ma
    assert cfg.mom_day == v13.MOMENTUM_POLICY.momentum_days
    assert cfg.weight_end == v13.MOMENTUM_POLICY.linear_weight_end
    assert cfg.score_threshold == v13.MOMENTUM_POLICY.score_threshold
    assert cfg.abs_mom_day == v13.MOMENTUM_POLICY.absolute_momentum_days
    assert cfg.abs_mom_threshold == v13.MOMENTUM_POLICY.absolute_momentum_threshold
    assert cfg.abs_filter_share == v13.MOMENTUM_POLICY.absolute_filter_share
    assert cfg.volume_ma == v13.MOMENTUM_POLICY.volume_ma == 160
    assert cfg.volume_ratio_threshold == v13.MOMENTUM_POLICY.volume_ratio_threshold == 0.85
    assert cfg.hot_score_threshold == v13.MOMENTUM_POLICY.hot_score_threshold == 150.0
    assert cfg.hot_scale == v13.MOMENTUM_POLICY.hot_scale == 0.0

    index = pd.bdate_range("2023-01-02", periods=180)
    close = pd.Series(
        1000.0 * np.exp(np.linspace(0.0, 0.18, len(index)) + 0.02 * np.sin(np.arange(len(index)) / 7.0)),
        index=index,
    )
    expected = source.calc_bias_momentum(
        close, cfg.bias_ma, cfg.mom_day, cfg.weight_end
    )
    actual = v13.calc_bias_momentum(close)
    np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0.0, equal_nan=True)

    ohlcv = pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.linspace(1_000_000, 1_500_000, len(close)),
        },
        index=index,
    )
    source_curve = source.build_sleeve_curve(ohlcv, cfg)
    ours = v13.build_momentum_schedule(ohlcv).set_index("date")
    np.testing.assert_allclose(
        ours["momentum_execution_weight"],
        source_curve["final_weight"],
        atol=1e-12,
        rtol=0.0,
    )


def test_v13_schedule_splits_capital_and_keeps_options_off_momentum_and_grid() -> None:
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
    schedule = v13.build_target_schedule(state, momentum)

    assert schedule["core_im_units"].tolist() == [0.5] * 4
    assert schedule["momentum_im_units"].tolist() == [0.0, 0.0, 0.25, 0.5]
    assert schedule["grid_im_units"].tolist() == [0.0, 1.0, 1.0, 0.0]
    assert schedule["total_im_units"].tolist() == [0.5, 1.5, 1.75, 1.0]
    assert schedule["core_put_signal_qty_normalized"].tolist() == [1.5, 1.5, 2.0, 0.0]
    assert schedule["core_put_execution_qty_normalized"].tolist() == [0.0, 1.5, 1.5, 2.0]
    assert schedule["momentum_put_qty_normalized"].eq(0.0).all()
    assert schedule["grid_put_qty"].eq(0).all()
    assert schedule["core_call_coverage_capacity_contracts_normalized"].eq(1.0).all()
    assert schedule["core_call_actual_target_contracts_normalized"].isna().all()
    assert schedule["core_call_target_contracts_normalized"].isna().all()
    assert ~schedule["actual_call_state_available"].any()
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
        v13.compose_from_parent_schedule(parent_schedule, momentum)


@pytest.mark.parametrize("bad_price", [np.nan, np.inf, 0.0, -1.0])
def test_momentum_ohlcv_fails_closed_on_invalid_prices(bad_price: float) -> None:
    close = pd.Series(
        np.linspace(1000.0, 1100.0, 180),
        index=pd.bdate_range("2023-01-02", periods=180),
    )
    close.iloc[-2] = bad_price
    frame = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 2_000_000.0},
        index=close.index,
    )
    with pytest.raises(ValueError, match="NaN or infinite|nonpositive"):
        v13.build_momentum_schedule(frame)


def test_momentum_ohlcv_accepts_shanghai_daily_timezone_and_rejects_utc() -> None:
    def frame(index: pd.DatetimeIndex) -> pd.DataFrame:
        close = pd.Series(np.linspace(1000.0, 1100.0, len(index)), index=index)
        return pd.DataFrame(
            {"open": close, "high": close, "low": close, "close": close, "volume": 2_000_000.0},
            index=index,
        )

    result = v13.build_momentum_schedule(
        frame(pd.date_range("2024-01-02", periods=180, tz="Asia/Shanghai"))
    )
    assert result["date"].dt.tz is None
    with pytest.raises(ValueError, match="timezone must be Asia/Shanghai"):
        v13.build_momentum_schedule(frame(pd.date_range("2024-01-02", periods=180, tz="UTC")))


def test_volume_and_hot_score_use_signal_day_for_next_execution(monkeypatch) -> None:
    index = pd.bdate_range("2024-01-02", periods=165)
    close = pd.Series(np.linspace(1000.0, 1200.0, len(index)), index=index)
    volume = pd.Series(2_000_000.0, index=index)
    volume.iloc[159] = 100_000.0
    score = pd.Series(10.0, index=index)
    score.iloc[160] = 160.0
    monkeypatch.setattr(v13, "calc_bias_momentum", lambda _close: score)
    frame = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": volume}
    )
    schedule = v13.build_momentum_schedule(frame).set_index("date")
    assert bool(schedule.iloc[158]["volume_pass"]) is True
    assert bool(schedule.iloc[159]["volume_pass"]) is False
    assert schedule.iloc[160]["momentum_execution_weight"] == 0.0
    assert bool(schedule.iloc[160]["score_hot_signal"]) is True
    assert schedule.iloc[161]["momentum_execution_weight"] == 0.0


def test_local_real_artifact_audit_passes() -> None:
    schedule, audit = v13.load_authoritative_local_state()
    assert audit["start"] == "2015-04-16"
    assert audit["end"] == "2026-08-14"
    assert audit["rows"] == 2756
    assert len(schedule) == 2756
    for key in (
        "source_signal_rule_max_abs_error",
        "frozen_candidate_weight_max_abs_error",
        "core_units_formula_max_abs_error",
        "momentum_units_formula_max_abs_error",
        "total_units_formula_max_abs_error",
        "grid_parent_parity_max_abs_error",
        "put_core_only_formula_max_abs_error",
        "call_actual_target_formula_max_abs_error",
    ):
        assert audit[key] <= 1e-12
    assert audit["a_share_strategy_spec_hash"] == "9c81ef4d468b0b4f6d28d36f7a0174e88097b2f11c368124ab44edaccc3fee04"
    assert audit["a_share_implementation_hash"] == "45cb8ab59e0ba6e2f21759e35fc57068a733f49b5c2c82d2566ec711447f91c4"
    assert audit["volume_block_signal_rows"] > 0
    assert audit["score_hot_signal_rows"] > 0
    assert audit["momentum_put_nonzero_rows"] == 0
    assert audit["momentum_call_nonzero_rows"] == 0
    assert audit["normalized_four_put_without_parent_tier4_rows"] == 0
    assert audit["call_actual_active_rows"] == 1652
    assert audit["call_actual_flat_rows"] == 1104
    assert audit["call_threat_roll_events"] == 36
    assert audit["call_threat_roll_count_increment_failures"] == 0
    assert audit["call_threat_roll_expiry_order_failures"] == 0
    assert audit["call_max_threat_roll_count"] == 5
    assert audit["call_threat_entry_blocked_rows"] == 58
    assert audit["call_threat_entry_blocked_inconsistency_rows"] == 0
    assert audit["call_coverage_capacity_contracts_normalized"] == 1.0
    assert audit["call_rescue_expiry_rule"] == "rescue_next_listed"
    assert schedule["core_call_coverage_capacity_contracts_normalized"].eq(1.0).all()
    assert schedule["core_call_target_contracts_normalized"].notna().all()
    assert int(schedule["core_call_target_contracts_normalized"].eq(1.0).sum()) == 1652
    assert int(schedule["core_call_target_contracts_normalized"].eq(0.0).sum()) == 1104

    roll_count = schedule["threat_roll_count"].astype(int)
    threat_roll = roll_count.gt(roll_count.shift())
    assert roll_count.between(0, 5).all()
    assert roll_count.loc[threat_roll].sub(roll_count.shift().loc[threat_roll]).eq(1).all()
    assert schedule.loc[threat_roll, "call_expiry"].gt(
        schedule["call_expiry"].shift().loc[threat_roll]
    ).all()
    blocked = schedule["threat_entry_blocked"].astype(bool)
    assert (~schedule.loc[blocked, "call_active"]).all()
    assert schedule.loc[blocked, "call_contract"].eq("").all()
    assert schedule.loc[blocked, "call_expiry"].isna().all()
    assert schedule.loc[blocked, "threat_roll_count"].eq(0).all()


def test_frozen_spec_hash_matches_sidecar() -> None:
    expected = (v13.SPEC_PATH.with_suffix(v13.SPEC_PATH.suffix + ".sha256"))
    expected_hash = expected.read_text(encoding="utf-8").split()[0]
    actual_hash = hashlib.sha256(v13.SPEC_PATH.read_bytes()).hexdigest()
    assert actual_hash == expected_hash
