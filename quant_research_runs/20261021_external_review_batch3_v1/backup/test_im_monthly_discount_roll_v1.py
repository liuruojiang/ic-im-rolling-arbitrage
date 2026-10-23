import pandas as pd
import pytest

from im_monthly_discount_roll_v1 import (
    CASH_ASSET_ANNUAL_RETURN,
    CASH_ASSET_DAILY_RETURN,
    CASH_WEIGHT,
    Cycle,
    build_futures_daily,
    metric_from_returns,
    metrics_by_window,
)


def test_roll_gap_is_not_counted_as_return() -> None:
    futures = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-01-18"),
                "contract": "IM2401",
                "settle": 100.0,
                "close": 100.0,
                "volume": 1,
                "open_interest": 1,
            },
            {
                "date": pd.Timestamp("2024-01-19"),
                "contract": "IM2401",
                "settle": 102.0,
                "close": 102.0,
                "volume": 1,
                "open_interest": 0,
            },
            {
                "date": pd.Timestamp("2024-01-19"),
                "contract": "IM2402",
                "settle": 95.0,
                "close": 95.0,
                "volume": 1,
                "open_interest": 1,
            },
            {
                "date": pd.Timestamp("2024-01-22"),
                "contract": "IM2402",
                "settle": 96.0,
                "close": 96.0,
                "volume": 1,
                "open_interest": 1,
            },
        ]
    )
    cycles = [
        Cycle("IM2401", pd.Timestamp("2024-01-18"), pd.Timestamp("2024-01-19"), pd.Timestamp("2024-01-19"), True),
        Cycle("IM2402", pd.Timestamp("2024-01-19"), pd.Timestamp("2024-01-22"), pd.Timestamp("2024-02-16"), False),
    ]
    daily, schedule = build_futures_daily(futures, cycles)
    assert daily["date"].tolist() == [
        pd.Timestamp("2024-01-18"),
        pd.Timestamp("2024-01-19"),
        pd.Timestamp("2024-01-22"),
    ]
    assert daily["im_gross_ret"].tolist() == pytest.approx([0.0, 0.02, 96.0 / 95.0 - 1.0])
    assert daily.loc[daily["date"].eq(pd.Timestamp("2024-01-19")), "cost_rate"].item() == pytest.approx(0.0002)
    assert len(schedule) == 2


def test_cash_layer_is_70pct_of_3pct_asset() -> None:
    assert CASH_WEIGHT == pytest.approx(0.70)
    assert CASH_ASSET_ANNUAL_RETURN == pytest.approx(0.03)
    assert (1.0 + CASH_ASSET_DAILY_RETURN) ** 252 - 1.0 == pytest.approx(0.03)
    assert CASH_WEIGHT * CASH_ASSET_ANNUAL_RETURN == pytest.approx(0.021)


def test_unavailable_windows_are_na() -> None:
    dates = pd.bdate_range("2022-07-22", "2026-08-14")
    daily = pd.DataFrame({"date": dates})
    for column in {
        "im_gross_ret",
        "im_net_ret",
        "csi1000_price_ret",
        "csi1000_tri_ret",
        "gross_vs_price_ret",
        "net_vs_price_ret",
        "gross_vs_tri_ret",
        "im_net_plus_cash_ret",
        "net_basis_plus_cash_ret",
    }:
        daily[column] = 0.0
    metrics = metrics_by_window(daily)
    assert not metrics.loc[metrics["window"].eq("10y"), "available"].any()
    assert not metrics.loc[metrics["window"].eq("5y"), "available"].any()
    assert metrics.loc[metrics["window"].eq("3y"), "available"].all()
    assert metrics.loc[metrics["window"].eq("10y"), "cagr"].isna().all()


def test_metric_from_returns_compounds() -> None:
    result = metric_from_returns(pd.Series([0.10, -0.10]))
    assert result["total_return"] == pytest.approx(-0.01)
    assert result["max_drawdown"] == pytest.approx(-0.10)
