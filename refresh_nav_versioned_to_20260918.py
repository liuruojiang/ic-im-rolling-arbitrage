"""Rebuild the versioned IC/IM NAV through the latest completed session.

This is an isolated research runner.  It keeps r7 rules for dates before the
v1.4 effective date and uses only a dedicated output directory.
"""

from datetime import date
from pathlib import Path

import pandas as pd

import refresh_nav_r7_complete_20260911 as replay
import poe_ic_im_mainline_v1_3_bot as market_source


replay.END = date(2026, 9, 17)
replay.OUTPUT = Path(__file__).resolve().parent / "outputs" / "nav_versioned_refresh_20260920_final"


def load_fresh_benchmark(_path: Path, end: date) -> pd.Series:
    """Download and validate the same public index OHLCV used by the r7 entrypoint."""
    product = "IC" if _path == replay.IC_BENCHMARK else "IM"
    frame = market_source.fetch_ohlcv_history(product)
    values = pd.to_numeric(frame["close"], errors="raise")
    result = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(frame.index))
    result = result[~result.index.duplicated(keep="last")].sort_index()
    if result.index[-1].date() < end:
        raise RuntimeError(f"fresh {product} benchmark ends at {result.index[-1].date()}, before {end}")
    return result


replay._load_benchmark = load_fresh_benchmark


if __name__ == "__main__":
    replay.main()
