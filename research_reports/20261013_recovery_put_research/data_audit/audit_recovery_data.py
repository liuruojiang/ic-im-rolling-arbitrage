"""Read-only frozen-source audit of the actual saved recovery windows.

No networking, production writes, parameter scan or account replay. A positive
listed contract reference is a data-availability probe, never a position target.
"""
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch
import zipfile

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NATIVE = ROOT / "outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925"
STUDY = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
FIX11 = ROOT / "quant_research_runs/20261002_fix10_vs_fix11_full_history"
PAIRED = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2/outputs"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["ICIM_STATE_DIR"] = str(HERE / "isolated_state")
os.environ["ICIM_REQUIRE_MIGRATION"] = "0"
sys.path[:0] = [str(STUDY), str(NATIVE), str(ROOT)]

from native_fix4_historical_sources_v2 import DatedSources
from native_fix4_historical_sources_v1 import IC_DATA, IC_FUTURES, IM_FUTURES, IM_ARCHIVES, formal
from native_fix4_risk_signals_v1 import OHLCV
import native_account as account


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def finite_positive(value):
    return pd.notna(value) and math.isfinite(float(value)) and float(value) > 0


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    source = DatedSources()
    daily_paths = {"IC": FIX11 / "account_daily_paths.csv.gz",
                   "IM": PAIRED / "fix9_full_account_daily_nav.csv.gz"}
    event_paths = {"IC": FIX11 / "account_events.csv.gz", "IM": PAIRED / "full_account_events.csv.gz"}
    target_paths = {
        "IC": FIX11 / "account_inputs/FIX11_full/native_fix4_ic_noseller_targets_v2.csv.gz",
        "IM": STUDY / "input_variants/im/formal/native_fix4_im_noseller_targets_v3.csv.gz",
    }
    risk_paths = {"IC": FIX11 / "account_inputs/FIX11_full/native_fix4_ic_risk_signals_v1.csv.gz",
                  "IM": STUDY / "input_variants/im/formal/native_fix4_im_risk_signals_v1.csv.gz"}
    raw_paths = [IC_DATA / "sse_510500_standard_put_snapshots.csv.gz",
                 IC_DATA / "sina_510500_put_histories.csv.gz", IC_DATA / "sina_510500_etf.csv",
                 IC_FUTURES, IM_FUTURES, IM_ARCHIVES / "202501.zip", *OHLCV.values(),
                 ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv"]
    provenance_paths = [*raw_paths, *daily_paths.values(), *event_paths.values(),
                        *target_paths.values(), *risk_paths.values(),
                        NATIVE / "native_fix4_historical_sources_v1.py",
                        NATIVE / "native_fix4_historical_sources_v2.py", STUDY / "native_account.py",
                        STUDY / "input_variants/im/formal/native_fix4_seller_lifecycle_v4.csv.gz",
                        FIX11 / "run_account.py", ROOT / "poe_ic_im_mainline_v1_4_bot.py"]
    manifest = [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha(path)}
                for path in dict.fromkeys(provenance_paths)]
    save("input_manifest.json", manifest)
    coverage = {}
    for key, frame in {"IC_master": source.ic_master, "IC_option_OHLCV": source.ic_hist,
                       "IC_ETF": source.ic_etf.reset_index(), **{k + "_futures": v for k, v in source.futures.items()}}.items():
        coverage[key] = dict(rows=len(frame), distinct_days=frame.date.nunique(),
                             start=frame.date.min(), end=frame.date.max(), columns=list(frame.columns))
    archives = sorted(IM_ARCHIVES.glob("20????.zip"))
    archive_days = set()
    for path in archives:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                leaf = Path(name).name
                if leaf[:8].isdigit() and leaf.endswith("_1.csv"):
                    archive_days.add(date(int(leaf[:4]), int(leaf[4:6]), int(leaf[6:8])))
    coverage["IM_archive_inventory"] = {
        "archives": len(archives), "first_file": archives[0].name, "last_file": archives[-1].name,
        "dated_csv_members": len(archive_days), "first_member_day": min(archive_days), "last_member_day": max(archive_days),
        "boundary": "Archive member inventory only; recovery-window MO chains are fully parsed below. This is not all-history per-row price certification."}
    daily, targets, risks, windows = {}, {}, {}, {}
    audit_rows, chain_rows, event_rows = [], [], []
    missing = []
    dates = {"IC": [date(2025, 1, d) for d in (21, 22, 23, 24, 27)],
             "IM": [date(2025, 1, d) for d in (16, 17, 20, 21, 22)]}
    for product in ("IC", "IM"):
        df = pd.read_csv(daily_paths[product])
        df = df.loc[df.arm.eq("FIX11_full")] if product == "IC" else df.loc[df.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")]
        df = df.sort_values("date").reset_index(drop=True)
        daily[product] = df
        targets[product] = pd.read_csv(target_paths[product]).set_index("signal_date", verify_integrity=True)
        risks[product] = pd.read_csv(risk_paths[product]).set_index("signal_date", verify_integrity=True)
        selected = df.seller_route.isin(["assigned_etf", "cash_wait", "recovery_future"]) | df.etf_value.gt(0)
        windows[product] = {"account_start": df.date.min(), "account_end": df.date.max(), "account_days": len(df),
                            "route_counts": df.seller_route.value_counts().to_dict(),
                            "actual_recovery_or_assigned_asset_days": df.loc[selected, ["date", "seller_route", "etf_value"]].to_dict("records")}
        events = pd.read_csv(event_paths[product])
        events = events.loc[events.arm.eq("FIX11_full")] if product == "IC" else events.loc[events.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")]
        events = events.loc[events.date.between(str(min(dates[product])), str(max(dates[product])))].copy()
        events["audit_product"] = product
        event_rows.append(events)
        sessions = sorted({d.date() for d in source.futures[product].date})
        market = account.Market(source, product)
        for day in dates[product]:
            stamp = str(day)
            t = targets[product].loc[stamp]
            r = risks[product].loc[stamp]
            chain = market.option_chain(day)
            nxt = sessions[sessions.index(day) + 1]
            chain_next = market.option_chain(nxt)
            next_futures = source.futures_on(product, nxt)
            name, close_field, open_field = (("contract", "last", "open") if product == "IC" else ("instrument", "lastprice", "openprice"))
            chain_rows.append({"product": product, "day": day, "chain_rows": len(chain),
                               "positive_close": int(chain[close_field].map(finite_positive).sum()),
                               "positive_open": int(chain[open_field].map(finite_positive).sum()),
                               "missing_or_nonpositive_open": int((~chain[open_field].map(finite_positive)).sum()),
                               "settlement_estimate_rows": int(chain.get("pricing_basis", pd.Series("", index=chain.index)).eq("official_settlement_estimate_zero_volume").sum())})
            # Reference identity only. Actual ordinary target is recorded below
            # and is zero on these recovery days; this probe never creates a trade.
            if product == "IC":
                target_day = (pd.Timestamp(day) + pd.DateOffset(months=3)).date()
                expiries = [(m, formal._fourth_wednesday(m.year, m.month)) for m in chain.contract_month.unique()]
                chosen_month, expiry = min([(m, e) for m, e in expiries if e > day], key=lambda x: (abs((x[1] - target_day).days), x[1]))
                candidates = chain.loc[chain.contract_month.eq(chosen_month) & chain[close_field].map(finite_positive)].copy()
                candidates["strike_error"] = (candidates.strike - .95 * market.spot(day)).abs()
                row = candidates.sort_values(["strike_error", "contract"]).iloc[0]
                requested_moneyness = .95
            else:
                with patch.object(formal.im_put_policy, "EFFECTIVE_DATE", date(1900, 1, 1)):
                    row = formal.select_im_put_for_reset(chain, day, float(t.future_close))
                expiry = account.option_expiry(product, str(row[name]))
                requested_moneyness = 1.02
            contract = str(row[name])
            next_rows = chain_next.loc[chain_next[name].eq(contract)]
            found = len(next_rows) == 1
            next_open = float(next_rows[open_field].iloc[0]) if found else None
            next_close = float(next_rows[close_field].iloc[0]) if found else None
            proxy_value, price_basis = market.buyer_open_quote(nxt, contract, day)
            if not found or not finite_positive(next_open):
                missing.append({"product": product, "signal_day": day, "execution_day": nxt,
                                "contract": contract, "missing": "exact T+1 positive raw open", "fallback": price_basis})
            future_row = next_futures.loc[next_futures.contract.eq(str(t.quarter_core_eod))]
            risk_subset = {key: r[key] for key in ("score", "momentum_120", "mom120_floor_active", "core_put_target_delta", "core_put_target_qty_normalized") if key in r}
            audit_rows.append({"product": product, "signal_day": day, "next_session": nxt,
                               "saved_ordinary_core_contract": t.core_put_contract_target,
                               "saved_ordinary_core_qty": float(t.core_put_qty_target),
                               "reference_only_contract": contract, "security_id": row.get("security_id"),
                               "expiry": expiry, "calendar_days_to_expiry": (expiry - day).days,
                               "requested_moneyness": requested_moneyness, "strike": float(row.strike),
                               "signal_close": float(row[close_field]), "signal_volume": row.get("volume"),
                               "signal_pricing_basis": row.get("pricing_basis", "vendor_daily_close"),
                               "t1_exact_identity_found": found, "t1_raw_open": next_open, "t1_close": next_close,
                               "buyer_open_model_quote": proxy_value, "buyer_open_basis": price_basis,
                               "quarter_future": str(t.quarter_core_eod),
                               "t1_future_open": float(future_row.open.iloc[0]) if len(future_row) == 1 else None,
                               "t1_etf_open": market.spot_open(nxt), **risk_subset})
        windows[product]["ordinary_put_zero_over_probed_window"] = all(row["saved_ordinary_core_qty"] == 0 for row in audit_rows if row["product"] == product)
        if product == "IM":
            absent = [day for day in pd.to_datetime(df.date).dt.date if day not in archive_days]
            coverage["IM_archive_inventory"]["missing_saved_account_days"] = [str(day) for day in absent]
    pd.DataFrame(audit_rows).to_csv(HERE / "recovery_contract_price_availability.csv", index=False)
    pd.DataFrame(chain_rows).to_csv(HERE / "recovery_raw_chain_coverage.csv", index=False)
    pd.concat(event_rows, ignore_index=True).to_csv(HERE / "saved_recovery_account_events.csv", index=False)
    save("coverage_and_windows.json", {"coverage": coverage, "actual_windows": windows, "missing": missing,
                                         "classification": "real frozen inputs; reference-only selector identities are not signal targets or fills"})
    save("data_audit_findings.json", {
        "status": "READY_FOR_FROZEN_DATA_RESEARCH_WITH_STATED_LIMITS",
        "most_material_fact": "Both saved IC/IM actual recovery windows have ordinary core Put target quantity zero. Removing only the recovery route prohibition cannot test positive protection in this sample.",
        "execution_boundary": [
            "IC is assigned at expiry close on 2025-01-22, although saved daily route still says short_put. It holds ETF through 01-23 close and converts at 01-24 open. A T=01-22 post-settlement decision can first protect at 01-23 open; a decision delayed until assigned_etf appears at 01-23 close first fills at conversion open 01-24 and misses the ETF holding day.",
            "IM cash settlement occurs 2025-01-17; cash_wait route is 01-20 and has no fixed-core long asset. Recovery future begins 01-21 open; a 01-20 close decision can protect from that same entry open. Waiting until 01-21 close first buys on 01-22 when the special recovery state has already ended.",
            "The short special state does not mean the long future is liquidated on exit. The position can continue in ordinary future; candidate hedge closure/continuation and full-cycle PnL effects need explicit research rules.",
            "native_account.Market.buyer_open_quote first uses exact T+1 raw open, then T-close sticky-IV/T+1 underlying-open proxy. Market.quote can use prior close for missing open marks when prior_day is provided. Keep raw-open and proxy counts separate.",
            "Ordinary buyer adjustments and 3x resets use T+1 open; scheduled monthly maintenance is same-day settlement close; quarterly roll uses signal close. Do not apply a universal open clock to all event classes."
        ],
        "selection": {"IC": "nearest listed expiry to T+3 calendar months, earlier expiry tie; 95% of ETF close strike nearest with contract-name tie; security_id and dated standard contract master join",
                      "IM": "nearest listed expiry to T+3 calendar months, later expiry tie; 102% of held IM quarterly reference under as-if-current policy; exact listed target may not jump strike/month when quote unavailable"},
        "costs": {"future_one_way_rate_of_notional": account.FUTURE_FEE,
                  "option_one_way_rate_of_underlying_notional": account.OPTION_FEE,
                  "IC_option_multiplier": 10000, "MO_multiplier": 100, "future_multiplier": 200,
                  "ETF_one_way_rate_of_trade_value": account.FUTURE_FEE,
                  "cash_annual_rate": 0.03,
                  "not_included_proof": "Observed spreads, market impact, actual broker fees, margin financing and guaranteed fill liquidity are not established by daily source data."},
        "loader": "Insert STUDY then NATIVE on sys.path, import DatedSources from native_fix4_historical_sources_v2; use source.ic_on(date), source.im_mo_on(date), source.futures_on(product,date), and native_account.Market(source,product). Do not call any producer main or reuse target_for_arm cached producer SHA assertion against changed production source.",
        "full_replay_loader": "run_account.listed.load_account_inputs() additionally rebuilds IC fix9 seller lifecycle in memory. Do not replace it with the raw fix8 IC formal lifecycle. run_account.replay continues through the listed adapter and native account engine.",
        "limitations": ["IC dated membership is a later historical reconstruction, not exchange publication-time PIT certification.",
                        "Fear history is a later downloaded snapshot and retains prior PIT limitations.",
                        "Current MO parser substitutes official settlement estimate for zero-volume Put close; preserve raw_lastprice/pricing_basis in comparisons with the older frozen account.",
                        "Saved lifecycle recovery_pnl_reference uses spot-reference PnL and does not itself demonstrate cost-complete cycle breakeven. Adding protection without re-evaluating exit produces a frozen-route conditional study, not a newly validated full-cycle exit policy.",
                        "Actual recovery observations are only one short window per product; no long bear-market recovery evidence.",
                        "No future data was fetched or appended; account endpoints remain IC 2026-08-13, IM 2026-08-14."]})
    assert all(sha(Path(item["path"])) == item["sha256"] for item in manifest), "An input changed during audit"
    print(json.dumps({"output": str(HERE), "reference_rows": len(audit_rows), "exact_open_missing": len(missing),
                      "all_inputs_unchanged": True, "ordinary_zero": {k:v["ordinary_put_zero_over_probed_window"] for k,v in windows.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    with patch.object(requests.sessions.Session, "request", side_effect=RuntimeError("network forbidden in frozen-source audit")):
        main()
