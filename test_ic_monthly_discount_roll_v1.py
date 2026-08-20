import pandas as pd
import pytest

from ic_monthly_discount_roll_v1 import (
    Cycle,
    build_futures_daily,
    metric_from_returns,
    third_friday,
)


def test_third_friday() -> None:
    assert third_friday(pd.Timestamp("2026-08-01")) == pd.Timestamp("2026-08-21")
    assert third_friday(pd.Timestamp("2015-05-01")) == pd.Timestamp("2015-05-15")


def test_roll_gap_is_not_counted_as_return() -> None:
    futures = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-01-18"), "contract": "IC2401", "settle": 100.0, "close": 100.0, "volume": 1, "open_interest": 1},
            {"date": pd.Timestamp("2024-01-19"), "contract": "IC2401", "settle": 102.0, "close": 102.0, "volume": 1, "open_interest": 0},
            {"date": pd.Timestamp("2024-01-19"), "contract": "IC2402", "settle": 95.0, "close": 95.0, "volume": 1, "open_interest": 1},
            {"date": pd.Timestamp("2024-01-22"), "contract": "IC2402", "settle": 96.0, "close": 96.0, "volume": 1, "open_interest": 1},
        ]
    )
    cycles = [
        Cycle("IC2401", pd.Timestamp("2024-01-18"), pd.Timestamp("2024-01-19"), pd.Timestamp("2024-01-19"), True),
        Cycle("IC2402", pd.Timestamp("2024-01-19"), pd.Timestamp("2024-01-22"), pd.Timestamp("2024-02-16"), False),
    ]
    daily, schedule = build_futures_daily(futures, cycles)
    assert daily["date"].tolist() == [
        pd.Timestamp("2024-01-18"),
        pd.Timestamp("2024-01-19"),
        pd.Timestamp("2024-01-22"),
    ]
    assert daily["ic_gross_ret"].tolist() == pytest.approx([0.0, 0.02, 96.0 / 95.0 - 1.0])
    assert daily.loc[daily["date"].eq(pd.Timestamp("2024-01-19")), "cost_rate"].item() == pytest.approx(0.0002)
    assert len(schedule) == 2


def test_metric_from_returns_includes_compounding() -> None:
    result = metric_from_returns(pd.Series([0.10, -0.10]))
    assert result["total_return"] == pytest.approx(-0.01)
    assert result["max_drawdown"] == pytest.approx(-0.10)
