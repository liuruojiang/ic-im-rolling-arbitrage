"""V3 causal rerun: T-close profit signal, T+1-close full recycle and re-strike."""
from __future__ import annotations

import json
from pathlib import Path

import research_im_core_put_profit_restrike_v1 as scan

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_corrected_im_current_core_put_im_core_put_profit_recycle_restrike_v3_causal_t_close_signal_t_1_close_baseline_2x_3x"
SPEC = ROOT / "docs" / "im_core_put_profit_restrike_causal_v3_spec.md"


def main() -> None:
    scan.RUN = RUN
    scan.SPEC = SPEC
    scan.main()
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scan_type"] = "im_core_put_profit_restrike_causal_v3"
    meta["source_hashes"]["v3_wrapper"] = scan.sha(Path(__file__))
    meta["execution_rule"] = "T close confirms multiple; T+1 close sells old and buys current 3m 102% Put; scheduled monthly maintenance has priority"
    meta["invalid_predecessors"] = ["v1 real position switch bug", "v2 same-close signal/execution lookahead"]
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
