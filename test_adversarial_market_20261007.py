"""Real-history calendar and causal-prefix regressions for fix11."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "quant_research_runs/20261002_ic_csi500_abs40_debounce/sina_sh000905_ohlcv.csv"
CLOCK = datetime(2026, 10, 7, 18, tzinfo=bot.BEIJING)


def real_history():
    return pd.read_csv(SOURCE, parse_dates=["date"]).set_index("date")


def test_unmodified_real_history_remains_valid():
    frame = real_history()
    pd.testing.assert_frame_equal(bot._validate_v13_ohlcv("IC", frame, CLOCK), frame.astype(float))


@pytest.mark.parametrize("extra_day", ["2026-09-27", "2026-10-01"])
def test_non_session_bar_after_frozen_cutoff_is_rejected(extra_day):
    frame = real_history()
    stamp = pd.Timestamp(extra_day)
    frame.loc[stamp] = frame.loc[frame.index < stamp].iloc[-1]
    frame = frame.sort_index()
    with pytest.raises(RuntimeError, match="非交易日"):
        bot._validate_v13_ohlcv("IC", frame, CLOCK)


def test_real_history_schedule_is_causal_for_each_prefix():
    frame = real_history()
    full = bot.v13_momentum_schedule("IC", frame["close"], ohlcv=frame)
    for cutoff in ("2026-09-15", "2026-09-16", "2026-09-29"):
        prefix = frame.loc[:cutoff]
        actual = bot.v13_momentum_schedule("IC", prefix["close"], ohlcv=prefix)
        pd.testing.assert_frame_equal(actual, full.loc[:cutoff])
