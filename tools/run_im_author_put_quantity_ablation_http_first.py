from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import im_author_put_quantity_ablation_v1 as research


# The official CFFEX HTTP archive endpoint is reachable from GitHub-hosted
# runners; HTTPS is not. This changes transport only, not the strategy.
research.CFFEX_URL = "http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip"


# The frozen research source's build_market() prepares the filtered Put frame
# locally, while main() later reads it as a module global. Preserve the frozen
# source and repair that handoff here without changing any data or strategy
# semantics.
_original_build_market = research.build_market


def _build_market_with_put_handoff(im_all, puts_all):
    index, im, im_expiry, put_expiry = _original_build_market(im_all, puts_all)
    puts = puts_all[
        (puts_all["date"] >= research.START) & (puts_all["date"] <= research.END)
    ].copy()
    puts["expiry_date"] = puts["contract"].map(put_expiry)
    puts["dte"] = (puts["expiry_date"] - puts["date"]).dt.days
    research.puts = puts
    return index, im, im_expiry, put_expiry


research.build_market = _build_market_with_put_handoff


if __name__ == "__main__":
    raise SystemExit(research.main())
