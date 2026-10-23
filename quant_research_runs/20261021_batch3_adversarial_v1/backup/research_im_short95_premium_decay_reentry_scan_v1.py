"""Nearest-month short Put: one 50--80% take-profit roll, with re-admission."""
from pathlib import Path
import research_im_short95_premium_decay_roll_scan_v1 as scan

scan.RUN = Path(__file__).resolve().parent / 'quant_param_scan_runs' / '20260916_ic_im_im_short95_recovery_v1_nearest_month_short_put_one_step_early_roll_premium_decay_50_80_with_re_admission'

if __name__ == '__main__':
    scan.main()
