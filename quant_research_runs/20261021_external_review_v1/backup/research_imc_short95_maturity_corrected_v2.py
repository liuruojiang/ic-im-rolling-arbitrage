"""V2 wrapper: provide M0--M+3 theoretical option coverage for the maturity scan."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import im_mo_csi1000_put_protection_battery_v6 as mkt
import research_imc_short95_maturity_corrected_v1 as scan

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_corrected_im_core_put_high_iv_short95_router_im_short_put_maturity_with_call_suspension_v2_front10_m_1_m_2_m_3_model_all_tenors"
ORIGINAL_LOAD_LAYER = scan.load_layer


def extended_model_inputs():
    market, base, _, futures, _, _ = scan.common.model_source.build_inputs()
    dates = pd.DatetimeIndex(market.date)
    months = pd.date_range(
        market.date.min().to_period("M").to_timestamp(),
        market.date.max() + pd.DateOffset(months=4), freq="MS",
    )
    expiries = {month: mkt.third_friday(month, dates) for month in months}
    keys: set[tuple[pd.Timestamp, int]] = set()
    for i, row in enumerate(market.itertuples(index=False)):
        if i == 0:
            continue
        spot = float(market.iloc[i - 1].spot_close)
        step = 25 if spot <= 2500 else 50 if spot <= 5000 else 100 if spot <= 10000 else 200
        for ahead in (0, 1, 2, 3):
            month = row.date.to_period("M").to_timestamp() + pd.offsets.MonthBegin(ahead)
            keys.add((month, math.floor(spot * 0.95 / step + 0.5) * step))
    rows = []
    for month, strike in sorted(keys):
        if month not in expiries:
            continue
        expiry = expiries[month]
        sample = market[(market.date >= month - pd.DateOffset(months=3)) & (market.date <= expiry)]
        for row in sample.itertuples(index=False):
            years = max((expiry - row.date).days / 365, 0)
            open_price = mkt.proxy.bs_put(row.spot_open, strike, row.rate_open, row.dividend_open, row.sigma_open, years)
            close_price = mkt.proxy.bs_put(row.spot_close, strike, row.rate_close, row.dividend_close, row.sigma_close, years)
            rows.append({
                "date": row.date, "contract": "MO" + month.strftime("%y%m") + "-P-" + str(strike),
                "contract_month": month, "actual_expiry": expiry, "strike": strike,
                "open": open_price, "settle": close_price, "close": close_price,
                "volume": 1, "open_interest": 1,
            })
    options = pd.DataFrame(rows).drop_duplicates(["contract", "date"]).sort_values(["date", "contract"])
    return market, base, options, futures


def load_layer(scope: str):
    if scope == "real":
        return ORIGINAL_LOAD_LAYER(scope)
    market, base, options, futures = extended_model_inputs()
    return market, base, options, None, futures


def main() -> None:
    scan.RUN = RUN
    scan.load_layer = load_layer
    scan.main()
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scan_type"] = "corrected_im_high_iv_short95_maturity_with_call_suspension_model_all_tenors_v2"
    meta["source_hashes"]["v2_wrapper"] = scan.sha(Path(__file__))
    meta["model_option_generation"] = "Black-Scholes proxy M0 through M+3; same market/rate/dividend/sigma series; no executable-history claim"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
