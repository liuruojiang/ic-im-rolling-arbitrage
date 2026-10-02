"""Forward-only IC CSI500 momentum migration invariants."""

from datetime import date

import numpy as np
import pandas as pd

import poe_ic_im_mainline_v1_4_bot as strategy


def test_ic_new_abs40_starts_on_signal_day_and_carries_old_nav(monkeypatch):
    rng = np.random.default_rng(1729)
    dates = pd.bdate_range("2005-01-03", periods=760)
    close = pd.Series(1000.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.018, len(dates)))), index=dates)
    ohlcv = pd.DataFrame({"open": close, "high": close, "low": close,
                          "close": close, "volume": 1e8}, index=dates)

    monkeypatch.setattr(strategy, "IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE", date(2099, 1, 1))
    legacy = strategy.v13_momentum_schedule("IC", close, ohlcv=ohlcv)
    new_score = strategy.calc_v13_momentum_score("IC", close).reindex(legacy.index)
    abs40 = (close / close.shift(40) - 1.0).reindex(legacy.index)
    new_base_target = (new_score > 0).astype(float) * (0.5 + 0.5 * abs40.gt(0).astype(float))
    different = new_base_target.ne(legacy["base_signal_target"])
    eligible = different & (legacy.index >= pd.Timestamp("2007-03-01"))
    assert eligible.any(), "synthetic path must exercise a changed IC signal"
    effective = eligible[eligible].index[0]

    monkeypatch.setattr(strategy, "IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE", effective.date())
    migrated = strategy.v13_momentum_schedule("IC", close, ohlcv=ohlcv)
    old_dates = legacy.index < effective
    for column in ("momentum_score", "abs20", "base_signal_target", "base_nav_for_dd",
                   "base_dd_for_gate", "nav_decay_signal", "signal_target", "execution_weight"):
        pd.testing.assert_series_equal(migrated.loc[old_dates, column], legacy.loc[old_dates, column])

    assert migrated.loc[effective, "momentum_score"] == new_score.loc[effective]
    assert migrated.loc[effective, "abs_momentum"] == abs40.loc[effective]
    assert migrated.loc[effective, "abs_momentum_days"] == 40
    assert not migrated.loc[effective, "abs_debounce_active"]
    assert migrated.loc[effective, "base_signal_target"] == new_base_target.loc[effective]
    assert migrated.loc[effective, "base_nav_for_dd"] == legacy.loc[effective, "base_nav_for_dd"]
    assert migrated.loc[effective, "base_dd_for_gate"] == legacy.loc[effective, "base_dd_for_gate"]
    assert migrated.loc[effective, "execution_weight"] == legacy.loc[effective, "execution_weight"]
    next_day = migrated.index[migrated.index.get_loc(effective) + 1]
    assert migrated.loc[next_day, "execution_weight"] == migrated.loc[effective, "signal_target"]
    cash_daily = 1.02 ** (1.0 / 244.0) - 1.0
    previous_day = legacy.index[legacy.index.get_loc(effective) - 1]
    executed_old_on_effective = legacy.loc[previous_day, "base_signal_target"]
    executed_new_on_next = migrated.loc[effective, "base_signal_target"]
    next_base_return = (
        executed_new_on_next * (close.loc[next_day] / close.loc[effective] - 1.0)
        + (1.0 - executed_new_on_next) * cash_daily
        - 0.001 * abs(executed_new_on_next - executed_old_on_effective)
    )
    expected_next_nav = migrated.loc[effective, "base_nav_for_dd"] * (1.0 + next_base_return)
    assert np.isclose(migrated.loc[next_day, "base_nav_for_dd"], expected_next_nav)
    carried_peak = max(legacy.loc[:effective, "base_nav_for_dd"].max(), expected_next_nav)
    expected_next_dd = expected_next_nav / carried_peak - 1.0
    assert np.isclose(migrated.loc[next_day, "base_dd_for_gate"], expected_next_dd)
    assert migrated.loc[next_day, "nav_decay_signal"] == (expected_next_dd <= -0.06)
    pd.testing.assert_series_equal(
        migrated.loc[migrated.index >= effective, "abs_momentum"],
        abs40.loc[abs40.index >= effective].rename("abs_momentum"),
    )

    monkeypatch.setattr(strategy, "IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE", date(2099, 1, 1))
    im_before = strategy.v13_momentum_schedule("IM", close, ohlcv=ohlcv)
    monkeypatch.setattr(strategy, "IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE", effective.date())
    im_after = strategy.v13_momentum_schedule("IM", close, ohlcv=ohlcv)
    pd.testing.assert_frame_equal(im_before, im_after)
