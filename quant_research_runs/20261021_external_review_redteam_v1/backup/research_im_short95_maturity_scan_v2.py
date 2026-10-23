"""Rerun v1 maturity scan with the corrected fail-closed early valuation gate."""
from pathlib import Path

import research_im_short95_maturity_scan_v1 as scan


scan.RUN = Path(__file__).resolve().parent / "quant_param_scan_runs" / "20260916_ic_im_rolling_arbitrage_im_short95_maturity_scan_v2_short_put_recovery_put_contract_month_ahead"


if __name__ == "__main__":
    scan.main()
