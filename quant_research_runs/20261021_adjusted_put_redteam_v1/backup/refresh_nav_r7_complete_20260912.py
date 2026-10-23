"""Extend the verified r7 complete NAV replay through 2026-09-11.

This is an isolated research refresh.  It reuses the formal historical replay
entrypoint but supplies the same-day validated index panels produced by the
R-squared scan, leaving the frozen benchmark exports and any ledger untouched.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import refresh_nav_r7_complete_20260911 as base


ROOT = Path(__file__).resolve().parent
SOURCE_RUN = ROOT / "quant_param_scan_runs" / "20260912_ic_im_v13_r7_r2_current_f_base_width_scan"


def main() -> None:
    base.OUTPUT = ROOT / "outputs" / "nav_r7_complete_refresh_20260912"
    base.SIGNALS_PATH = base.OUTPUT / "historical_signals.json"
    base.END = date(2026, 9, 11)
    base.IC_BENCHMARK = SOURCE_RUN / "ic_ohlcv_frozen_plus_fresh.csv.gz"
    base.IM_BENCHMARK = SOURCE_RUN / "im_ohlcv_frozen_plus_fresh.csv.gz"
    base.main()


if __name__ == "__main__":
    main()
