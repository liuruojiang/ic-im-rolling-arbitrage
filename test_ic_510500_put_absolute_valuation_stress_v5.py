from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

import ic_510500_put_absolute_valuation_stress_v5 as v5


def test_frozen_spec_and_dependencies_match() -> None:
    assert v5.sha256(v5.SPEC) == v5.SPEC_SHA256
    assert v5.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == v5.SPEC_SHA256
    assert v5.sha256(v5.V3_PATH) == v5.V3_SHA256
    assert v5.sha256(v5.V2_PATH) == v5.V2_SHA256


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.9999, 0), (2.0, 1), (2.4999, 1), (2.5, 2)],
)
def test_pb_boundaries(value: float, expected: int) -> None:
    assert v5.pb_level(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0301, 0), (0.03, 1), (0.0151, 1), (0.015, 2)],
)
def test_erp_boundaries(value: float, expected: int) -> None:
    assert v5.erp_level(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0200, 0), (0.0199, 1), (0.0100, 1), (0.0099, 2)],
)
def test_dividend_boundaries(value: float, expected: int) -> None:
    assert v5.dividend_level(value) == expected


def test_target_matrix_is_frozen() -> None:
    for variant in v5.CONDITIONAL_VARIANTS:
        assert v5.target_for_variant(variant, "low", False) == 0.0
        assert v5.target_for_variant(variant, "low", True) == 0.0
    assert v5.target_for_variant("abs_base50", "high", True) == 0.5
    assert v5.target_for_variant("abs_stress_high", "medium", True) == 0.5
    assert v5.target_for_variant("abs_stress_high", "high", True) == 1.0
    assert v5.target_for_variant("abs_stress_any", "medium", True) == 1.0
    assert v5.target_for_variant("abs_3tier", "high", False) == 1.0


def test_stress_features_are_prefix_causal() -> None:
    frames = v5.core.v2.load_inputs()
    daily, _ = v5.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    full = v5.prepare_signal_frame(daily)
    cutoff = pd.Timestamp("2022-06-30")
    prefix = v5.prepare_signal_frame(daily[daily["date"] <= cutoff])
    columns = ["tri_sma120", "tri_rv20", "trend_stress", "vol_stress", "stress"]
    left = full.loc[full["date"].eq(cutoff), columns].iloc[0]
    right = prefix.loc[prefix["date"].eq(cutoff), columns].iloc[0]
    assert math.isclose(float(left["tri_sma120"]), float(right["tri_sma120"]), abs_tol=1e-12)
    assert math.isclose(float(left["tri_rv20"]), float(right["tri_rv20"]), abs_tol=1e-12)
    assert bool(left["trend_stress"]) == bool(right["trend_stress"])
    assert bool(left["vol_stress"]) == bool(right["vol_stress"])
    assert bool(left["stress"]) == bool(right["stress"])


def test_actual_signal_panel_current_state_and_execution() -> None:
    frames = v5.core.v2.load_inputs()
    daily, _ = v5.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, structural, state_summary, current = v5.build_signal_panel(
        frames["ic"], daily, frames["states_full"]
    )
    expected = {
        "abs_base50": 0.5,
        "abs_stress_high": 0.5,
        "abs_stress_any": 1.0,
        "abs_3tier": 0.5,
    }
    observed = current.set_index("signal_variant")["research_target_fraction"].to_dict()
    assert observed == expected
    assert set(current["valuation_state"]) == {"medium"}
    assert current["trend_stress"].all() and current["vol_stress"].all()
    regular = schedule[~schedule["initial_exception"]]
    assert (regular["execution_date"] > regular["eval_date"]).all()
    assert not schedule.duplicated(["layer", "signal_variant", "execution_date"]).any()
    assert len(structural) == len(v5.STRUCTURAL_EDGES)
    assert set(state_summary["signal_variant"]) == set(v5.VARIANTS)
    assert set(signals["signal_variant"]) == set(v5.VARIANTS)
    assert all(
        values["early_drawdown_average_target"] >= 0.5
        for values in v5.SIGNAL_DIAGNOSTICS.values()
    )


def test_segment_boundaries() -> None:
    dates = pd.date_range(v5.core.MODEL_START, v5.core.END, freq="B")
    group = pd.DataFrame({"date": dates})
    for segment, start, end in [
        ("development", v5.core.MODEL_START, v5.DEVELOPMENT_END),
        ("revision_validation", v5.REVISION_START, v5.REVISION_END),
        ("recent_expansion", v5.RECENT_START, v5.core.END),
    ]:
        subset, requested_start, requested_end, available = v5.segment_slice(group, segment)
        assert available
        assert requested_start == start
        assert requested_end == end
        assert subset["date"].min() >= start
        assert subset["date"].max() <= end


def test_formal_output_is_absent_or_tied_to_v5() -> None:
    if not Path(v5.OUTPUT).exists():
        return
    manifest = pd.read_json(v5.OUTPUT / "data_manifest.json", typ="series")
    assert manifest["version"] == v5.VERSION
    assert manifest["spec_sha256"] == v5.SPEC_SHA256
    assert manifest["script_sha256"] == v5.sha256(Path(v5.__file__))
