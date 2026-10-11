"""Continuous 1x quarterly IC/IM futures-only accounts since exchange listing."""
from __future__ import annotations

import json
import sys
from datetime import date

import numpy as np
import pandas as pd

from run_core_only import HERE, ROOT, NATIVE, REFERENCE, STUDY, digest, load

RAW = {
    "IC": ROOT / "data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv",
    "IM": ROOT / "data/im_monthly_discount_roll_v1/cffex_im_contracts.csv",
}
INPUTS = HERE / "pure_roll_since_listing_inputs"
ENGINE = STUDY / "native_account.py"


class FuturesOnlySources:
    def __init__(self) -> None:
        self.futures = {p: pd.read_csv(path, parse_dates=["date"]) for p, path in RAW.items()}
        self.by_day = {
            p: {day.date(): frame.drop(columns=["date"]).copy()
                for day, frame in raw.groupby("date", sort=True)}
            for p, raw in self.futures.items()
        }
        index = pd.read_csv(ROOT / "data/ic_monthly_discount_roll_v1/csindex_000905.csv",
                            parse_dates=["date"]).set_index("date", verify_integrity=True)
        self.ic_spot = index.close

    def futures_on(self, product: str, day: date) -> pd.DataFrame:
        frame = self.by_day[product].get(day)
        if frame is None or frame[["open", "close", "settle"]].isna().any().any():
            raise RuntimeError(f"missing dated futures quote: {product} {day}")
        return frame

    def ic_on(self, day: date) -> tuple[float, pd.DataFrame]:
        value = float(self.ic_spot.loc[pd.Timestamp(day)])
        if not np.isfinite(value) or value <= 0:
            raise RuntimeError(f"missing IC spot: {day}")
        return value, pd.DataFrame()


def quarters(product: str, source: FuturesOnlySources, formal) -> pd.DataFrame:
    days = sorted(source.by_day[product])
    day_set = set(days)
    final_day = days[-1]
    expiry = lambda contract: formal._third_friday(*formal._contract_month(contract))
    covered = lambda day: day in day_set or (day > final_day and day.weekday() < 5)
    held: str | None = None
    rows = []
    for day in days:
        frame = source.futures_on(product, day)
        listed = set(frame.contract)
        if held is None:
            available = sorted((c for c in listed if int(c[-2:]) in (3, 6, 9, 12) and expiry(c) >= day),
                               key=expiry)
            if not available:
                raise RuntimeError(f"no initial listed quarterly contract: {product} {day}")
            held = available[0]
        plan = formal.quarter_roll.roll_state(product, held, listed, day, True, expiry, covered)
        held = str(plan["core_eod_contract"])
        selected = frame.loc[frame.contract.eq(held), "close"]
        if len(selected) != 1:
            raise RuntimeError(f"target quarter unlisted: {product} {day} {held}")
        rows.append({"signal_date": day.isoformat(), "execution_date": days[len(rows)+1].isoformat()
                     if len(rows)+1 < len(days) else None,
                     "quarter_core_eod": held, "quarter_roll_confirmed": bool(plan["roll_confirmed"]),
                     "future_close": float(selected.iloc[0])})
    return pd.DataFrame(rows)


def validate_target_path(product: str, target: pd.DataFrame) -> dict:
    if product == "IC":
        paths = [HERE / "latest_model_inputs/native_fix4_ic_noseller_targets_v2.csv.gz",
                 ROOT / "quant_research_runs/20261002_fix10_vs_fix11_full_history/account_inputs/FIX11_full/native_fix4_ic_noseller_targets_v2.csv.gz"]
    else:
        paths = [STUDY / "input_variants/im/formal/native_fix4_im_noseller_targets_v3.csv.gz"]
    checks = {}
    for path in paths:
        saved = pd.read_csv(path)
        common = target.merge(saved[["signal_date", "quarter_core_eod", "quarter_roll_confirmed", "future_close"]],
                              on="signal_date", suffixes=("_new", "_saved"), validate="one_to_one")
        mismatch = (common.quarter_core_eod_new.ne(common.quarter_core_eod_saved) |
                    common.quarter_roll_confirmed_new.ne(common.quarter_roll_confirmed_saved))
        price_error = float(np.max(np.abs(common.future_close_new - common.future_close_saved)))
        if mismatch.any() or price_error > 1e-9 or len(common) != len(saved):
            raise RuntimeError(f"quarter target parity failed: {product} {path}: {int(mismatch.sum())}, {price_error}")
        checks[str(path.relative_to(ROOT))] = {"overlap_rows": len(common), "max_future_close_error": price_error}
    return checks


def main() -> None:
    sys.path[:0] = [str(REFERENCE), str(NATIVE), str(STUDY), str(ROOT)]
    import poe_ic_im_mainline_v1_4_bot as formal
    import run_study as study

    runner = load("since_listing_pure_config", REFERENCE / "run_candidate.py")
    engine = load("since_listing_pure_engine", ENGINE)
    source = FuturesOnlySources()
    INPUTS.mkdir(exist_ok=True)
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                      parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(study.base.OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    results, event_parts, audits = [], [], {}
    for product in ("IC", "IM"):
        target = quarters(product, source, formal)
        path_checks = validate_target_path(product, target)
        risk = target[["signal_date", "execution_date"]].copy()
        risk["momentum_future_units"] = 0.0
        risk["grid_target_units"] = 0.0
        target["monthly_reset_due"] = False
        target["monthly_reset_reference_day"] = None
        target["core_put_contract_target"] = None
        target["core_put_qty_target"] = 0.0
        target["momentum_put_contract_target"] = None
        target["momentum_put_qty_target"] = 0.0
        target["call_contract_target"] = None
        target["call_qty_normalized_target"] = 0.0
        risk.to_csv(INPUTS / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz", index=False, compression="gzip")
        target.to_csv(INPUTS / f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz",
                      index=False, compression="gzip")
        life = risk.iloc[:-1][["signal_date", "execution_date"]].copy()
        life["product"] = product
        life["arm"] = "repeat_roll"
        life["route_before"] = "future"
        life["route_after_execution"] = "future"
        life["action"] = "HOLD"
        candidates = life[["product"]].copy()
        runner.configure_engine(engine, INPUTS)
        engine.HERE = INPUTS
        engine.DISABLED_MODULES = frozenset({"buyer_put", "momentum", "grid", "call"})
        engine.CORE_UNITS = 1.0
        engine.BUYER_OPEN_SLEEVES = frozenset()
        daily, events, _ = engine.replay(product, "repeat_roll", source, life, candidates, gov, spot)
        if daily.date.tolist() != target.signal_date.tolist():
            raise RuntimeError(f"daily calendar mismatch: {product}")
        trades = events.loc[events.event.eq("trade")].copy()
        if trades.empty or not trades.symbol.fillna("").str.startswith("future|core|").all():
            raise RuntimeError(f"non-pure futures trade: {product}")
        nav_error = float(np.max(np.abs(np.cumprod(1 + daily.return_net.to_numpy(float)) - daily.nav.to_numpy(float))))
        if nav_error > 2e-9 or float((daily.cash_close - daily.margin_reserved).min()) < -1e-7:
            raise RuntimeError(f"account identity failed: {product}")
        if product == "IM":
            saved = pd.read_csv(HERE / "real_pure_roll_daily.csv.gz")
            saved = saved.loc[saved["product"].eq("IM")].reset_index(drop=True)
            parity = float(np.max(np.abs(daily.return_net.to_numpy(float) - saved.return_net.to_numpy(float))))
            if daily.date.tolist() != saved.date.tolist() or parity > 2e-10:
                raise RuntimeError(f"IM listing-period baseline parity failed: {parity}")
        else:
            saved = pd.read_csv(HERE / "model_pure_roll_daily.csv.gz")
            saved = saved.loc[saved["product"].eq("IC")].reset_index(drop=True)
            early = daily.iloc[:len(saved)]
            parity = float(np.max(np.abs(early.return_net.to_numpy(float) - saved.return_net.to_numpy(float))))
            if early.date.tolist() != saved.date.tolist() or parity > 2e-10:
                raise RuntimeError(f"IC pre-gap baseline parity failed: {parity}")
        annualized = float(np.expm1(np.log1p(daily.return_net.to_numpy(float)).sum() * 252 / len(daily)))
        daily.insert(0, "data_layer", "since_listing_actual_futures")
        results.append(daily)
        events.insert(0, "product", product)
        event_parts.append(events)
        audits[product] = {"start": str(daily.date.iloc[0]), "end": str(daily.date.iloc[-1]),
                           "rows": len(daily), "annualized_252": annualized,
                           "total_return": float(daily.nav.iloc[-1] - 1),
                           "baseline_max_daily_return_error": parity,
                           "target_path_parity": path_checks,
                           "future_trades": len(trades), "non_future_trades": 0,
                           "nav_identity_max_error": nav_error,
                           "missing_calendar_days": 0}
        print(json.dumps({"product": product, **audits[product]}, ensure_ascii=False), flush=True)
    pd.concat(results, ignore_index=True).to_csv(HERE / "since_listing_pure_roll_daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(HERE / "since_listing_pure_roll_events.csv.gz", index=False, compression="gzip")
    summary_rows = []
    periods = {
        "model_period": pd.read_csv(HERE / "model_pure_roll_daily.csv.gz"),
        "listed_real_period": pd.read_csv(HERE / "real_pure_roll_daily.csv.gz"),
        "since_futures_listing": pd.concat(results, ignore_index=True),
    }
    for period, frame in periods.items():
        for product in ("IC", "IM"):
            segment = frame.loc[frame["product"].eq(product)].sort_values("date").reset_index(drop=True)
            daily_returns = segment.return_net.to_numpy(float)
            compound = float(np.expm1(np.log1p(daily_returns).sum()))
            if abs(compound - (float(segment.nav.iloc[-1]) - 1)) > 2e-9:
                raise RuntimeError(f"pure-roll period NAV mismatch: {period}/{product}")
            summary_rows.append({"product": product, "period": period,
                                 "start": str(segment.date.iloc[0]), "end": str(segment.date.iloc[-1]),
                                 "trading_days": len(segment),
                                 "cumulative_return": compound,
                                 "annualized_return_252": float(np.expm1(np.log1p(daily_returns).sum() * 252 / len(segment))),
                                 "data_basis": ("synthetic_IM_futures" if period == "model_period" and product == "IM"
                                                else "actual_listed_futures")})
    pd.DataFrame(summary_rows).to_csv(HERE / "pure_roll_annualized_periods.csv", index=False, encoding="utf-8-sig")
    (HERE / "since_listing_pure_roll_audit.json").write_text(json.dumps({
        "classification": "research_only_continuous_actual_futures_since_listing",
        "account_engine_sha256": digest(ENGINE),
        "raw_futures_sha256": {p: digest(path) for p, path in RAW.items()},
        "source_index_sha256": digest(ROOT / "data/ic_monthly_discount_roll_v1/csindex_000905.csv"),
        "audits": audits,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
