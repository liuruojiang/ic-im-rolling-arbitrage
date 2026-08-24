import hashlib
import importlib.util
import sys

import numpy as np
import pandas as pd
import pytest

import ic_mainline_v1_2 as v12


def _load_a_share_v13_module():
    spec = importlib.util.spec_from_file_location(
        "a_share_v13_ic_parity_source", v12.A_SHARE_V13_BOT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_records_ic_candidate_boundaries() -> None:
    manifest = v12.rule_manifest()
    assert manifest["parent_version"] == "ic_im_system_mainlines_v2__ic"
    assert manifest["capital_sleeves"]["core_share"] == 0.5
    assert manifest["capital_sleeves"]["momentum_share"] == 0.5
    assert manifest["put"]["core_mom120_negative_floor_delta"] == 0.5
    assert manifest["put"]["momentum_sleeve_uses_mom120_floor"] is False
    assert manifest["grid"]["entry_lte"] == 0.375
    assert manifest["grid"]["exit_gte"] == 1.0
    assert manifest["grid"]["independent_of_momentum"] is True
    assert manifest["grid"]["put_covered"] is False
    assert manifest["call"]["included"] is False
    assert manifest["performance_claim"] == "none_new_combined_put_grid_schedule_only"


def test_momentum_formula_and_config_match_a_share_v13_zz500() -> None:
    source = _load_a_share_v13_module()
    cfg = next(item for item in source.SLEEVES if item.key == "zz500")
    assert cfg.bias_ma == v12.MOMENTUM_POLICY.bias_ma
    assert cfg.mom_day == v12.MOMENTUM_POLICY.momentum_days
    assert cfg.weight_end == v12.MOMENTUM_POLICY.linear_weight_end
    assert cfg.score_threshold == v12.MOMENTUM_POLICY.score_threshold
    assert cfg.abs_mom_day == v12.MOMENTUM_POLICY.absolute_momentum_days
    assert cfg.abs_mom_threshold == v12.MOMENTUM_POLICY.absolute_momentum_threshold
    assert cfg.abs_filter_share == v12.MOMENTUM_POLICY.absolute_filter_share

    index = pd.bdate_range("2022-01-03", periods=260)
    close = pd.Series(
        5000.0
        * np.exp(
            np.linspace(0.0, 0.16, len(index))
            + 0.025 * np.sin(np.arange(len(index)) / 11.0)
        ),
        index=index,
    )
    expected_score = source.calc_bias_momentum(
        close, cfg.bias_ma, cfg.mom_day, cfg.weight_end
    )
    actual_score = v12.calc_bias_momentum(close)
    np.testing.assert_allclose(
        actual_score, expected_score, atol=1e-12, rtol=0.0, equal_nan=True
    )

    source_curve = source.build_sleeve_curve(
        pd.DataFrame(
            {
                "open": close * 0.999,
                "high": close * 1.002,
                "low": close * 0.998,
                "close": close,
                "volume": np.linspace(2_000_000, 3_000_000, len(close)),
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


def _synthetic_inputs():
    dates = pd.bdate_range("2024-01-02", periods=4)
    momentum = pd.DataFrame(
        {
            "date": dates,
            "score": [-1.0, 1.0, 1.0, -1.0],
            "abs20": [-0.1, -0.1, 0.1, 0.1],
            "momentum_signal_target": [0.0, 0.5, 1.0, 0.0],
            "momentum_execution_weight": [0.0, 0.0, 0.5, 1.0],
        }
    )
    put = pd.DataFrame(
        {
            "execution_date": dates,
            "eval_date": dates - pd.offsets.BDay(1),
            "valuation_tier_new": [0, 1, 2, 4],
            "v2_target_delta": [0.5, 0.5, 1.0, 0.25],
            "valuation_only_target_delta": [0.0, 0.25, 0.5, 1.0],
            "bare_full_target_delta": [0.25, 0.25, 0.5, 0.125],
            "momentum_valuation_target_delta": [0.0, 0.0, 0.125, 0.5],
            "target_delta": [0.25, 0.25, 0.625, 0.625],
            "momentum_weight": [0.0, 0.0, 0.5, 1.0],
            "momentum_120": [-0.1, -0.1, 0.1, 0.1],
            "mom_floor_binding": [True, True, False, False],
        }
    )
    grid = pd.DataFrame(
        {
            "date": dates,
            "overlay_held_eod": [0, 1, 1, 0],
            "overlay_buy": [0, 1, 0, 0],
            "overlay_sell": [0, 0, 0, 1],
            "signal_date_executed": [pd.NaT, dates[0], pd.NaT, dates[2]],
            "valuation_score": [0.4, 0.3, 0.8, 1.1],
        }
    )
    return momentum, put, grid


def test_compose_target_schedule_keeps_ic_sleeves_and_options_separate() -> None:
    momentum, put, grid = _synthetic_inputs()
    schedule = v12.compose_target_schedule(momentum, put, grid)
    assert schedule["core_ic_units"].tolist() == [0.5] * 4
    assert schedule["momentum_ic_units"].tolist() == [0.0, 0.0, 0.25, 0.5]
    assert schedule["grid_ic_units"].tolist() == [0.0, 1.0, 1.0, 0.0]
    assert schedule["total_ic_units"].tolist() == [0.5, 1.5, 1.75, 1.0]
    assert schedule["core_put_target_delta"].tolist() == [0.25, 0.25, 0.5, 0.125]
    assert schedule["momentum_put_target_delta"].tolist() == [0.0, 0.0, 0.125, 0.5]
    assert schedule["total_put_target_delta"].tolist() == [0.25, 0.25, 0.625, 0.625]
    assert schedule.loc[schedule["momentum_execution_weight"].eq(0), "momentum_put_target_delta"].eq(0).all()
    assert schedule["grid_put_target_delta"].eq(0).all()
    assert schedule["call_target_contracts"].eq(0).all()
    assert ~schedule["has_call"].any()


def test_rejects_same_day_momentum_execution() -> None:
    momentum, put, grid = _synthetic_inputs()
    momentum["momentum_execution_weight"] = momentum["momentum_signal_target"]
    with pytest.raises(ValueError, match="prior-session"):
        v12.compose_target_schedule(momentum, put, grid)


def test_rejects_nonzero_first_momentum_execution() -> None:
    momentum, put, grid = _synthetic_inputs()
    momentum.loc[0, "momentum_execution_weight"] = 0.5
    with pytest.raises(ValueError, match="First momentum execution"):
        v12.compose_target_schedule(momentum, put, grid)


def test_rejects_put_eval_date_not_strictly_before_execution() -> None:
    momentum, put, grid = _synthetic_inputs()
    put.loc[1, "eval_date"] = put.loc[1, "execution_date"] + pd.Timedelta(days=30)
    with pytest.raises(ValueError, match="strictly earlier"):
        v12.compose_target_schedule(momentum, put, grid)


@pytest.mark.parametrize("bad_price", [np.nan, np.inf, 0.0, -1.0])
def test_momentum_close_fails_closed_on_invalid_prices(bad_price: float) -> None:
    close = pd.Series(
        np.linspace(1000.0, 1100.0, 180),
        index=pd.bdate_range("2023-01-02", periods=180),
    )
    close.iloc[-2] = bad_price
    with pytest.raises(ValueError, match="NaN, infinite, or nonpositive"):
        v12.build_momentum_schedule(close)


def test_momentum_close_rejects_intraday_and_wrong_timezone_dates() -> None:
    close = pd.Series(
        [1000.0, 1001.0],
        index=pd.to_datetime(["2024-01-02 00:00", "2024-01-02 15:00"]),
    )
    with pytest.raises(ValueError, match="date-only|duplicate calendar"):
        v12.build_momentum_schedule(close)
    utc = pd.Series([1000.0, 1001.0], index=pd.date_range("2024-01-02", periods=2, tz="UTC"))
    with pytest.raises(ValueError, match="timezone must be Asia/Shanghai"):
        v12.build_momentum_schedule(utc)


def test_local_real_artifact_audit_passes() -> None:
    schedule, audit = v12.load_authoritative_local_state()
    assert audit["start"] == "2015-04-16"
    assert audit["end"] == "2026-08-14"
    assert audit["rows"] == 2756
    assert len(schedule) == 2756
    for key in (
        "source_signal_rule_max_abs_error",
        "momentum_t_plus_1_max_abs_error",
        "put_source_momentum_weight_max_abs_error",
        "core_units_formula_max_abs_error",
        "momentum_units_formula_max_abs_error",
        "total_units_formula_max_abs_error",
        "core_put_formula_max_abs_error",
        "momentum_valuation_put_formula_max_abs_error",
        "total_put_formula_max_abs_error",
        "executed_put_target_parity_max_abs_error",
        "flat_momentum_put_max_abs",
    ):
        assert audit[key] <= 1e-12
    assert audit["call_nonzero_rows"] == 0
    assert audit["grid_put_nonzero_rows"] == 0
    assert audit["grid_independent_of_momentum"] is True
    assert audit["grid_t_plus_1_execution"] is True
    assert audit["grid_entries"] == 3
    assert audit["grid_exits"] == 3
    latest = audit["latest_state"]
    assert latest["core_ic_units"] == 0.5
    assert latest["momentum_ic_units"] == 0.5
    assert latest["grid_ic_units"] == 0.0
    assert latest["total_put_target_delta"] == 0.375
    assert latest["executed_put_qty"] == 51.0


def test_frozen_spec_hash_matches_sidecar() -> None:
    sidecar = v12.SPEC_PATH.with_suffix(v12.SPEC_PATH.suffix + ".sha256")
    expected_hash = sidecar.read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(v12.SPEC_PATH.read_bytes()).hexdigest() == expected_hash
