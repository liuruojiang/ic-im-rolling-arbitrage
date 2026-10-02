"""Research-only: 1x bare near-month versus strict-quarter IM rolls.

Uses CFFEX raw per-contract closing prices.  No cash yield, options, grid,
momentum sleeve, leverage scaling, or continuous-contract adjustment is used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260919_ic_im_rolling_arbitrage_im_r7_research_baseline_"
    "bare_im_futures_roll_sleeve_near_month_versus_strict_quarter_tenor"
)
SPECS = {
    "IM": {
        "path": ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_im_contracts.csv",
        "roll_days_before_expiry": 1,
    },
}
COST_PER_SIDE = 0.0001


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract_month(contract: str) -> int:
    return int(contract[-2:])


def annualized_return(returns: pd.Series) -> float:
    if len(returns) == 0:
        return np.nan
    return float(np.prod(1.0 + returns.to_numpy()) ** (252.0 / len(returns)) - 1.0)


def max_drawdown(returns: pd.Series) -> float:
    nav = (1.0 + returns).cumprod()
    return float((nav / nav.cummax() - 1.0).min())


def metrics(returns: pd.Series) -> dict[str, float]:
    ann = annualized_return(returns)
    vol = float(returns.std(ddof=1) * np.sqrt(252)) if len(returns) > 1 else np.nan
    return {
        "cagr": ann,
        "max_drawdown": max_drawdown(returns),
        "annual_volatility": vol,
        "sharpe_rf0": float(ann / vol) if vol and np.isfinite(vol) else np.nan,
        "observations": int(len(returns)),
    }


def build_path(raw: pd.DataFrame, product: str, tenor: str, k: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw["date"])
    raw["close"] = pd.to_numeric(raw["close"], errors="raise")
    raw["volume"] = pd.to_numeric(raw["volume"], errors="raise")
    raw["open_interest"] = pd.to_numeric(raw["open_interest"], errors="raise")
    raw = raw.loc[raw["close"].gt(0)].sort_values(["date", "contract"])
    expiry = raw.groupby("contract", as_index=True)["date"].max().rename("expiry")
    dates = pd.DatetimeIndex(sorted(raw["date"].unique()))
    by_date = {date: frame.set_index("contract") for date, frame in raw.groupby("date")}

    first = by_date[dates[0]]
    active = min(first.index, key=lambda c: expiry[c])
    rows: list[dict] = []
    events: list[dict] = []
    prior_close: float | None = None

    for i, date in enumerate(dates):
        today = by_date[date]
        if active not in today.index:
            raise ValueError(f"{product} {date.date()} active contract missing: {active}")
        close = float(today.loc[active, "close"])
        gross = 0.0 if prior_close is None else close / prior_close - 1.0
        cost = COST_PER_SIDE if prior_close is None else 0.0
        roll = False
        old_contract = None
        new_contract = None
        # A T-k close roll: receive the old contract's close-to-close return,
        # transact old/new at this close proxy, and begin new marking tomorrow.
        expiry_date = expiry[active]
        expiry_index = dates.get_loc(expiry_date)
        if i == expiry_index - k:
            eligible = [
                c for c in today.index
                if expiry[c] > expiry_date
                and float(today.loc[c, "close"]) > 0
                and float(today.loc[c, "volume"]) > 0
                and float(today.loc[c, "open_interest"]) > 0
            ]
            if tenor == "near":
                candidates = eligible
            elif tenor == "quarter":
                candidates = [c for c in eligible if contract_month(c) in (3, 6, 9, 12)]
            else:
                raise ValueError(tenor)
            if not candidates:
                # The terminal incomplete contract is not a failed roll.  Any
                # earlier missing eligible contract is a hard data failure.
                if expiry_date != dates[-1]:
                    raise ValueError(f"{product} {date.date()} no {tenor} destination after {active}")
            else:
                old_contract, new_contract = active, min(candidates, key=lambda c: expiry[c])
                cost += 2.0 * COST_PER_SIDE
                active = new_contract
                roll = True
                events.append({
                    "product": product,
                    "tenor": tenor,
                    "date": date.date().isoformat(),
                    "old_contract": old_contract,
                    "new_contract": new_contract,
                    "old_expiry": expiry[old_contract].date().isoformat(),
                    "new_expiry": expiry[new_contract].date().isoformat(),
                    "old_close": close,
                    "new_close": float(today.loc[new_contract, "close"]),
                    "new_volume": float(today.loc[new_contract, "volume"]),
                    "new_open_interest": float(today.loc[new_contract, "open_interest"]),
                    "roll_days_before_expiry": k,
                })
        rows.append({
            "date": date,
            "product": product,
            "candidate": f"{product}_{tenor}_T{k}",
            "marked_contract": old_contract if roll else active,
            "gross_return": gross,
            "cost_return": -cost,
            "net_return": (1.0 + gross) * (1.0 - cost) - 1.0,
            "roll_event": roll,
            "roll_cost": cost,
        })
        prior_close = close if not roll else float(today.loc[active, "close"])
    return pd.DataFrame(rows), pd.DataFrame(events)


def window_slice(frame: pd.DataFrame, years: int | None) -> pd.DataFrame:
    if years is None:
        return frame
    end = frame["date"].max()
    start = end - pd.DateOffset(years=years)
    return frame.loc[frame["date"] >= start].copy()


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    all_daily, all_events, meta_sources = [], [], {}
    for product, spec in SPECS.items():
        raw = pd.read_csv(spec["path"])
        meta_sources[product] = {
            "path": str(spec["path"].relative_to(ROOT)),
            "sha256": sha256(spec["path"]),
            "rows": int(len(raw)),
            "date_min": str(raw["date"].min()),
            "date_max": str(raw["date"].max()),
        }
        for tenor in ("near", "quarter"):
            daily, events = build_path(raw, product, tenor, spec["roll_days_before_expiry"])
            all_daily.append(daily)
            all_events.append(events)

    daily = pd.concat(all_daily, ignore_index=True).sort_values(["product", "candidate", "date"])
    events = pd.concat(all_events, ignore_index=True).sort_values(["product", "tenor", "date"])
    windows = [("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)]
    detail_rows = []
    for (product, candidate), group in daily.groupby(["product", "candidate"], sort=True):
        for name, years in windows:
            cut = window_slice(group, years)
            available = years is None or (cut["date"].max() - cut["date"].min()).days >= 365 * years - 7
            detail_rows.append({
                "product": product,
                "candidate": candidate,
                "window": name,
                "available": bool(available),
                "start": cut["date"].min().date().isoformat() if available else "N/A",
                "end": cut["date"].max().date().isoformat() if available else "N/A",
                **(
                    metrics(cut["net_return"])
                    if available
                    else {k: np.nan for k in ("cagr", "max_drawdown", "annual_volatility", "sharpe_rf0", "observations")}
                ),
                "roll_events": int(cut["roll_event"].sum()) if available else 0,
                "cumulative_cost": float(-cut["cost_return"].sum()) if available else np.nan,
            })
    detail = pd.DataFrame(detail_rows)
    full_summary = detail.loc[detail["window"].eq("full")].copy()
    full_summary["candidate_role"] = np.where(full_summary["candidate"].str.contains("quarter"), "current_r7_quarter", "near_month_candidate")
    comparison_rows = []
    for product in SPECS:
        baseline = detail.query("product == @product and candidate.str.contains('quarter')", engine="python").set_index("window")
        near = detail.query("product == @product and candidate.str.contains('near')", engine="python").set_index("window")
        for window in baseline.index:
            comparison_rows.append({
                "product": product,
                "window": window,
                "near_minus_quarter_cagr_pp": 100.0 * (near.loc[window, "cagr"] - baseline.loc[window, "cagr"]),
                "near_minus_quarter_maxdd_pp": 100.0 * (near.loc[window, "max_drawdown"] - baseline.loc[window, "max_drawdown"]),
                "near_rolls_minus_quarter": int(near.loc[window, "roll_events"] - baseline.loc[window, "roll_events"]),
            })

    long = detail.rename(columns={
        "window": "segment", "observations": "rows", "cagr": "ann_return",
        "annual_volatility": "ann_vol", "sharpe_rf0": "sharpe_repo", "max_drawdown": "max_dd",
    }).copy()
    for col in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
        long[col] = long[col].astype(object)
        long.loc[~long["available"], col] = "N/A"
    long.loc[~long["available"], "rows"] = 0
    wide_rows = []
    for candidate, group in detail.groupby("candidate", sort=True):
        row = {"candidate": candidate, "product": group["product"].iloc[0]}
        for _, point in group.iterrows():
            segment = point["window"]
            row[f"ann_return_{segment}"] = point["cagr"] if point["available"] else "N/A"
            row[f"max_dd_{segment}"] = point["max_drawdown"] if point["available"] else "N/A"
            if segment == "full":
                row["ann_vol_full"] = point["annual_volatility"]
                row["sharpe_repo_full"] = point["sharpe_rf0"]
                row["roll_events_full"] = point["roll_events"]
        wide_rows.append(row)
    wide = pd.DataFrame(wide_rows)
    daily.to_csv(RUN / "daily_candidates.csv.gz", index=False, compression="gzip")
    events.to_csv(RUN / "roll_events.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    long.to_csv(RUN / "scan_summary.csv", index=False)
    pd.DataFrame(comparison_rows).to_csv(RUN / "near_minus_quarter.csv", index=False)
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update({
        "phase": "ran",
        "scan_type": "one_product_two_candidate_bare_roll_comparison",
        "baseline": {"IM": "strict-quarter T-1"},
        "candidate_grid": ["near-month T-1 (IM)"],
        "data_snapshot": meta_sources,
        "cost_model": {"price": "CFFEX official raw close", "initial_entry": "1bp", "each_roll": "2bp", "cash": "0", "other_components": "all zero"},
        "unavailable_segments": {
            candidate: {
                "last_10y": "IM formal history starts 2022-07-22; fewer than 10 years.",
                "last_5y": "IM formal history starts 2022-07-22; fewer than 5 years.",
            }
            for candidate in sorted(detail["candidate"].unique())
        },
        "outputs": {**meta["outputs"], "daily_candidates": str((RUN / "daily_candidates.csv.gz").relative_to(ROOT)), "roll_events": str((RUN / "roll_events.csv").relative_to(ROOT)), "comparison": str((RUN / "near_minus_quarter.csv").relative_to(ROOT))},
    })
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run": str(RUN), "full_summary": full_summary[["product", "candidate", "cagr", "max_drawdown", "roll_events"]].to_dict("records")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
