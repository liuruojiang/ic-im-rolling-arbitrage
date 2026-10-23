from __future__ import annotations

import numpy as np
import pandas as pd

import im_roll50_momentum50_fullcycle_put_v3 as subject


def load_daily() -> pd.DataFrame:
    return pd.read_csv(subject.OUTPUT / "daily_nav.csv.gz", parse_dates=["date"], low_memory=False)


def test_pinned_inputs_and_spec() -> None:
    assert subject.sha256(subject.SPEC) == subject.SPEC_HASH.read_text(encoding="utf-8").split()[0]
    for path, expected in subject.PINNED.items():
        assert subject.sha256(path) == expected


def test_sleeve_put_quantities_sum_and_cap() -> None:
    daily = load_daily()
    raw_target = 2.0 * daily["put_fraction"]
    assert np.allclose(daily["v2_target_put_qty"], raw_target, atol=1e-14)
    assert np.allclose(daily["bare_sleeve_put_qty"], 0.5 * raw_target, atol=1e-14)
    assert np.allclose(
        daily["momentum_sleeve_put_qty"],
        0.5 * daily["momentum_weight"] * raw_target,
        atol=1e-14,
    )
    assert np.allclose(
        daily["combined_put_qty"],
        daily["bare_sleeve_put_qty"] + daily["momentum_sleeve_put_qty"],
        atol=1e-14,
    )
    assert np.allclose(
        daily["combined_put_qty"], daily["total_im_units"] * raw_target, atol=1e-14
    )
    assert daily["bare_sleeve_put_qty"].max() <= 2.0
    assert daily["momentum_sleeve_put_qty"].max() <= 2.0
    assert daily["combined_put_qty"].max() <= 4.0


def test_all_sleeve_put_components_sum() -> None:
    daily = load_daily()
    for field in (
        "put_scale", "put_qty", "put_notional_fraction", "put_pnl_ret",
        "put_cost_rate", "put_mark_fraction",
    ):
        expected = daily[f"bare_sleeve_{field}"] + daily[f"momentum_sleeve_{field}"]
        assert np.allclose(daily[f"combined_{field}"], expected, atol=1e-14)


def test_return_recomposition_and_prior_path_parity() -> None:
    daily = load_daily()
    expected_pre_cash = (
        (1.0 + daily["baseline_pre_cash_ret"] + daily["combined_put_pnl_ret"])
        * (1.0 - daily["combined_put_cost_rate"])
        - 1.0
    )
    expected_cash = daily["blend_cash_weight"] - daily["combined_put_mark_fraction"]
    expected_ret = expected_pre_cash + expected_cash * subject.CASH_DAILY
    assert np.allclose(
        daily["sleeve_matched_dynamic_put_pre_cash_ret"], expected_pre_cash, atol=1e-14
    )
    assert np.allclose(
        daily["sleeve_matched_dynamic_put_cash_weight_raw"], expected_cash, atol=1e-14
    )
    assert np.allclose(daily["sleeve_matched_dynamic_put_ret"], expected_ret, atol=1e-14)
    assert np.allclose(
        daily["sleeve_matched_dynamic_put_ret"],
        daily["put_scaled_total_exposure_ret"],
        atol=1e-14,
    )
    assert expected_cash.min() >= -1e-12
