from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import im_author_put_quantity_ablation_v1 as research


# CFFEX's official HTTPS route is unreachable from GitHub-hosted runners,
# while the official HTTP archive endpoint returns the same ZIP payload.
# This changes only network transport order; all strategy logic and the
# preregistered specification remain unchanged.
research.CFFEX_TEMPLATES = (
    "http://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip",
    "https://www.cffex.com.cn/sj/historysj/{ym}/zip/{ym}.zip",
)


if __name__ == "__main__":
    raise SystemExit(research.main())
