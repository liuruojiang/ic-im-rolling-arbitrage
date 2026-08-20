import pandas as pd

import ic_valuation_overlay_entry_exit_scan_v2 as scan


def test_preregistered_grid_has_current_and_96_pairs() -> None:
    pairs = scan.grid()
    assert len(pairs) == 96
    assert len(set(pairs)) == 96
    assert scan.CURRENT_PAIR in pairs
    assert all(high - low >= scan.MIN_GAP - 1e-12 for low, high in pairs)


def test_index_proxy_uses_next_close_and_hysteresis() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=6, freq="D"),
            "price_close": [100.0, 90.0, 99.0, 110.0, 121.0, 120.0],
            "unbounded_median_knot": [0.9, 0.8, 1.2, 2.1, 2.2, 1.9],
        }
    )
    daily, trades, audit = scan.simulate_index_proxy(frame, 1.0, 2.0)
    assert trades["action"].tolist() == ["buy", "sell"]
    assert trades["signal_date"].tolist() == [frame.loc[0, "date"], frame.loc[3, "date"]]
    assert trades["execution_date"].tolist() == [frame.loc[1, "date"], frame.loc[4, "date"]]
    assert daily["overlay_held_eod"].tolist() == [0, 1, 1, 1, 0, 0]
    assert abs(daily.loc[1, "index_ret"] + 0.10) < 1e-12
    assert daily.loc[1, "cash_ret"] < 0.001  # Entry at close: no same-day index return.
    assert audit["completed_cycles"] == 1


def test_drawdown_dates_returns_last_peak_before_trough() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=5, freq="D"),
            "cash_ret": [0.0, 0.1, 0.0, -0.2, 0.05],
        }
    )
    result = scan.max_drawdown_dates(frame, "cash_ret")
    assert result["peak_date"] == frame.loc[2, "date"]
    assert result["trough_date"] == frame.loc[3, "date"]
    assert abs(result["max_dd"] + 0.2) < 1e-12
