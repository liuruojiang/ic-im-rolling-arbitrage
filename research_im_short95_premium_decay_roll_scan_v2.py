"""V2 locks every ordinary entry to the original M+1 contract definition."""
from pathlib import Path

import research_im_short95_premium_decay_roll_scan_v1 as scan


scan.RUN = Path(__file__).resolve().parent / 'quant_param_scan_runs' / '20260916_ic_im_im_short95_recovery_v1_short_put_premium_decay_early_roll_premium_decay_threshold_v2_baseline_parity'


if __name__ == '__main__':
    scan.main()
