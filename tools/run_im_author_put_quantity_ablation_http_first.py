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
# source and repair that handoff here without changing data or strategy rules.
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


# A contract whose prescribed roll date lies after the formal sample end must
# remain the active contract at the last observation; it is not an execution
# error. The frozen source checked the next listed contract unconditionally.
def _build_roll_schedule_bounded(dates, im, expiries):
    available = set(im["contract"])
    valid_close = set(
        zip(
            research.pd.to_datetime(im.loc[im["close"].gt(0), "date"]),
            im.loc[im["close"].gt(0), "contract"].astype(str),
        )
    )
    schedule = {}
    contract = "IM2208"
    last_day = research.pd.Timestamp(max(dates))
    while contract in available:
        nxt = research.next_im_contract(contract)
        if nxt not in available:
            break
        expiry = expiries[contract]
        target = expiry - research.pd.Timedelta(days=3)
        if target > last_day:
            break
        candidates = [
            day
            for day in dates[(dates >= target) & (dates <= expiry)]
            if (research.pd.Timestamp(day), contract) in valid_close
            and (research.pd.Timestamp(day), nxt) in valid_close
        ]
        if not candidates:
            raise RuntimeError(f"No executable roll date for {contract}->{nxt}")
        roll_day = research.pd.Timestamp(min(candidates))
        if research.START <= roll_day <= research.END:
            schedule[roll_day] = (contract, nxt)
        contract = nxt
    return schedule


research.build_roll_schedule = _build_roll_schedule_bounded


if __name__ == "__main__":
    raise SystemExit(research.main())
