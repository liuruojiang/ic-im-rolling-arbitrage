"""Read-only comparison of the certified paired-exit candidates and fix9 grid baseline.

This audit does not run a new strategy variant or alter frozen research outputs.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE_RUN = ROOT / "quant_research_runs/20260928_fix9_valuation_or_fear25_entry"
PAIRED_RUN = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2"
FEAR_FILE = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
SELECTED = {"IC": 50, "IM": 55}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    returns = pd.to_numeric(frame["return_net"], errors="raise").astype(float)
    if not len(returns) or not np.isfinite(returns).all() or (returns <= -1).any():
        raise ValueError("Invalid full-account return stream")
    nav = (1 + returns).cumprod()
    volatility = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    return {
        "rows": len(returns),
        "ann_return": float(nav.iloc[-1] ** (252 / len(returns)) - 1),
        "max_dd": float((nav / nav.cummax() - 1).min()),
        "sharpe_repo": float(returns.mean() / volatility * math.sqrt(252)) if volatility else 0.0,
        "ending_nav_from_1": float(nav.iloc[-1]),
    }


def window(frame: pd.DataFrame, segment: str) -> pd.DataFrame:
    if segment == "full":
        return frame
    end = pd.Timestamp(frame["date"].iloc[-1])
    years = {"last_3y": 3, "last_1y": 1}[segment]
    first = end - pd.DateOffset(years=years) + pd.Timedelta(days=1)
    return frame.loc[pd.to_datetime(frame["date"]) >= first]


def checked_metric(frame: pd.DataFrame, summary: pd.DataFrame, label: str, segment: str) -> dict:
    selected = summary.loc[summary.candidate.eq(label) & summary.segment.eq(segment)]
    if len(selected) != 1:
        raise ValueError(f"Missing or duplicate saved summary: {label}/{segment}")
    result = metrics(window(frame, segment))
    saved = selected.iloc[0]
    for key in ("ann_return", "max_dd", "sharpe_repo", "ending_nav_from_1"):
        if not math.isclose(float(result[key]), float(saved[key]), abs_tol=1e-10, rel_tol=1e-10):
            raise ValueError(f"Saved metric differs: {label}/{segment}/{key}")
    if int(result["rows"]) != int(saved["rows"]):
        raise ValueError(f"Saved row count differs: {label}/{segment}")
    result["start"] = str(window(frame, segment).date.iloc[0])
    result["end"] = str(window(frame, segment).date.iloc[-1])
    result["grid_entries"] = int(saved.grid_entries)
    result["grid_exits"] = int(saved.grid_exits)
    result["grid_held_days"] = int(saved.grid_held_days)
    result["grid_trade_fees"] = float(saved.grid_trade_fees)
    return result


def main() -> None:
    files = {
        "valuation_daily": BASE_RUN / "outputs/fix9_full_account_daily_nav.csv.gz",
        "valuation_summary": BASE_RUN / "portfolio_summary.csv",
        "candidate_daily": PAIRED_RUN / "outputs/fix9_full_account_daily_nav.csv.gz",
        "candidate_summary": PAIRED_RUN / "scan_summary.csv",
        "candidate_signals": PAIRED_RUN / "outputs/grid_transition_signals.csv",
        "fear_snapshot": FEAR_FILE,
    }
    for name, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(f"{name}: {path}")
    base_daily = pd.read_csv(files["valuation_daily"], dtype={"date": str})
    base_summary = pd.read_csv(files["valuation_summary"])
    paired_daily = pd.read_csv(files["candidate_daily"], dtype={"date": str})
    paired_summary = pd.read_csv(files["candidate_summary"])
    signals = pd.read_csv(files["candidate_signals"], dtype={"signal_date": str})
    fear = pd.read_csv(FEAR_FILE, dtype={"date": str})
    if fear.date.duplicated().any():
        raise ValueError("Duplicate Fear dates")

    comparisons: list[dict] = []
    years: list[dict] = []
    boundary: list[dict] = []
    missing_fear_dates: set[str] = set()
    for product, exit_level in SELECTED.items():
        baseline_label = f"{product}_VALUATION_GRID_LISTED_FIX9"
        candidate_label = f"{product}_OR_ENTRY_PAIRED_FEAR_EXIT_{exit_level}_LISTED_FIX9"
        baseline = base_daily.loc[base_daily.candidate.eq(baseline_label)].sort_values("date").reset_index(drop=True)
        candidate = paired_daily.loc[paired_daily.candidate.eq(candidate_label)].sort_values("date").reset_index(drop=True)
        if not len(baseline) or len(baseline) != len(candidate) or not baseline.date.equals(candidate.date):
            raise ValueError(f"Baseline/candidate date alignment failed: {product}")
        if float(candidate.grid_target_signal_units.max()) > 0.5 or float(candidate.grid_held_units.max()) > 0.5:
            raise ValueError(f"Candidate exceeds single-grid 0.5x cap: {product}")
        missing_fear_dates.update(set(candidate.date) - set(fear.date))
        product_signals = signals.loc[signals.candidate.eq(candidate_label)].sort_values("signal_date")

        for segment in ("full", "last_3y", "last_1y"):
            b = checked_metric(baseline, base_summary, baseline_label, segment)
            c = checked_metric(candidate, paired_summary, candidate_label, segment)
            comparisons.append({
                "instrument": product, "segment": segment, "start": b["start"], "end": b["end"],
                "rows": b["rows"], "valuation_ann_return": b["ann_return"],
                "paired_ann_return": c["ann_return"], "ann_return_delta_pp": 100 * (c["ann_return"] - b["ann_return"]),
                "valuation_max_dd": b["max_dd"], "paired_max_dd": c["max_dd"],
                "max_dd_improvement_pp": 100 * (c["max_dd"] - b["max_dd"]),
                "valuation_sharpe": b["sharpe_repo"], "paired_sharpe": c["sharpe_repo"],
                "valuation_grid_entries": b["grid_entries"], "paired_grid_entries": c["grid_entries"],
                "valuation_grid_exits": b["grid_exits"], "paired_grid_exits": c["grid_exits"],
                "valuation_grid_held_days": b["grid_held_days"], "paired_grid_held_days": c["grid_held_days"],
                "valuation_grid_fees": b["grid_trade_fees"], "paired_grid_fees": c["grid_trade_fees"],
                "candidate_terminal_grid_open": bool(candidate.grid_held_units.iloc[-1] > 0),
            })

        for year in sorted(set(baseline.date.str[:4])):
            b = baseline.loc[baseline.date.str.startswith(year), "return_net"].astype(float)
            c = candidate.loc[candidate.date.str.startswith(year), "return_net"].astype(float)
            if len(b) != len(c):
                raise ValueError(f"Calendar-year day count mismatch: {product}/{year}")
            baseline_return = float((1 + b).prod() - 1)
            candidate_return = float((1 + c).prod() - 1)
            years.append({"instrument": product, "year": year, "rows": len(b),
                          "valuation_return": baseline_return, "paired_return": candidate_return,
                          "return_delta_pp": 100 * (candidate_return - baseline_return)})

        sells = product_signals.loc[product_signals.action.eq("SELL_NEXT_OPEN")]
        dates = candidate.date.tolist()
        index = {date: i for i, date in enumerate(dates)}
        for event in sells.itertuples(index=False):
            i = index[str(event.signal_date)]
            valuation_entry = float(candidate.valuation_score.iloc[i]) <= (0.5 if product == "IC" else 1.6)
            fear_entry = pd.notna(candidate.fear_greed_index.iloc[i]) and float(candidate.fear_greed_index.iloc[i]) <= 25
            next_date = dates[i + 1] if i + 1 < len(dates) else ""
            next_signal_buy = bool(next_date and ((product_signals.signal_date.eq(next_date)) &
                                                   product_signals.action.eq("BUY_NEXT_OPEN")).any())
            boundary.append({"instrument": product, "exit_signal_date": event.signal_date,
                             "exit_execution_date": event.expected_execution_date,
                             "exit_reason": event.fear_entry_cause,
                             "fear_on_exit_signal": event.fear_greed_index,
                             "valuation_on_exit_signal": event.valuation_score,
                             "other_entry_condition_on_exit_signal": bool(valuation_entry or fear_entry),
                             "next_signal_date": next_date, "buy_on_next_signal_date": next_signal_buy})

        if int(product_signals.action.eq("BUY_NEXT_OPEN").sum()) != int(comparisons[-3]["paired_grid_entries"]):
            raise ValueError(f"Candidate signal count mismatch: {product}")
        if int(product_signals.action.eq("SELL_NEXT_OPEN").sum()) != int(comparisons[-3]["paired_grid_exits"]):
            raise ValueError(f"Candidate exit count mismatch: {product}")

    comparison_frame = pd.DataFrame(comparisons)
    year_frame = pd.DataFrame(years)
    boundary_frame = pd.DataFrame(boundary)
    comparison_frame.to_csv(HERE / "comparison.csv", index=False)
    year_frame.to_csv(HERE / "year_returns.csv", index=False)
    boundary_frame.to_csv(HERE / "exit_entry_overlap.csv", index=False)
    audit = {
        "status": "PASS_FOR_SAVED_DATA_ARITHMETIC_ONLY",
        "scope": "Independent valuation-only fix9 comparator; selected IC Fear50 and IM Fear55 paired-exit arms",
        "selected": SELECTED,
        "comparison_rows": len(comparison_frame), "year_rows": len(year_frame),
        "exit_signal_rows": len(boundary_frame),
        "selected_exit_signal_other_entry_count": int(boundary_frame.other_entry_condition_on_exit_signal.sum()),
        "selected_next_signal_reentry_count": int(boundary_frame.buy_on_next_signal_date.sum()),
        "fear_missing_on_model_calendar": sorted(missing_fear_dates),
        "source_hashes_sha256": {name: sha256(path) for name, path in files.items()},
        "limits": ["Retrospective Fear snapshot is not point-in-time certified",
                   "Saved full-account paths do not model live fills or new timing/missing-value scenarios"],
    }
    (HERE / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("status", "comparison_rows", "year_rows", "exit_signal_rows",
                                              "selected_exit_signal_other_entry_count", "selected_next_signal_reentry_count")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
