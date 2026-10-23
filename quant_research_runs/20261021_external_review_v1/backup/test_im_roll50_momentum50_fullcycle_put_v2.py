from __future__ import annotations

import numpy as np
import pandas as pd

import im_roll50_momentum50_fullcycle_put_v2 as subject


def load_daily() -> pd.DataFrame:
    return pd.read_csv(subject.OUTPUT / "daily_nav.csv.gz", parse_dates=["date"], low_memory=False)


def test_frozen_inputs_and_common_calendar() -> None:
    expected_spec = subject.SPEC_HASH.read_text(encoding="utf-8").split()[0]
    assert subject.sha256(subject.SPEC) == expected_spec
    for path, expected in subject.PINNED_HASHES.items():
        assert subject.sha256(path) == expected
    daily = load_daily()
    v1 = pd.read_csv(subject.V1_DAILY, usecols=["date"], parse_dates=["date"])
    assert daily["date"].equals(v1["date"])


def test_original_v2_put_is_unscaled_dynamic_zero_to_four() -> None:
    daily = load_daily()
    assert np.allclose(daily["put_original_v2_dynamic_put_scale"], 1.0, atol=0.0)
    assert np.allclose(
        daily["put_original_v2_dynamic_put_qty"], 2.0 * daily["put_fraction"], atol=1e-14
    )
    assert set(daily["put_original_v2_dynamic_put_qty"].unique()).issubset(
        {0.0, 1.0, 2.0, 3.0, 4.0}
    )
    assert daily["put_original_v2_dynamic_put_qty"].nunique() == 5


def test_original_v2_return_and_cash_identity() -> None:
    daily = load_daily()
    expected_pre_cash = (
        (1.0 + daily["baseline_pre_cash_ret"] + daily["put_pnl_ret"])
        * (1.0 - daily["put_cost_rate"])
        - 1.0
    )
    expected_cash = daily["blend_cash_weight"] - daily["put_mark_fraction"]
    expected_ret = expected_pre_cash + expected_cash * subject.CASH_DAILY
    assert np.allclose(
        daily["put_original_v2_dynamic_pre_cash_ret"], expected_pre_cash, atol=1e-14
    )
    assert np.allclose(
        daily["put_original_v2_dynamic_cash_weight_raw"], expected_cash, atol=1e-14
    )
    assert np.allclose(daily["put_original_v2_dynamic_ret"], expected_ret, atol=1e-14)
    assert expected_cash.min() >= -1e-12


def test_half_scaled_control_is_preserved() -> None:
    daily = load_daily()
    assert np.allclose(
        daily["put_half_scaled_ret"], daily["put_fixed_0p5_core_ret"], atol=0.0
    )
    assert np.allclose(daily["put_half_scaled_put_scale"], 0.5, atol=0.0)
