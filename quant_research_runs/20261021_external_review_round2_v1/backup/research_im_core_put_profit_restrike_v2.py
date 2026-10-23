"""V2 rerun after requiring the real engine to switch into the re-struck contract."""
from __future__ import annotations

import json
from pathlib import Path

import research_im_core_put_profit_restrike_v1 as scan

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_corrected_im_current_core_put_im_core_put_profit_recycle_and_restrike_v2_real_position_switch_baseline_2x_3x"


def main() -> None:
    scan.RUN = RUN
    scan.main()
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scan_type"] = "im_core_put_profit_restrike_scan_v2_real_position_switch_fixed"
    meta["source_hashes"]["v2_wrapper"] = scan.sha(Path(__file__))
    meta["invalid_predecessor"] = "v1 did not assign the newly selected real contract after close_profit_restrike; all v1 performance is invalid"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
