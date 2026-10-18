"""Read-only audit of saved latest-rule account evidence; no replay or mutation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
IC = ROOT / "quant_research_runs/20261002_fix10_vs_fix11_full_history"
IM = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2/outputs"
MODEL = ROOT / "quant_research_runs/20261003_mom120_core_only_annual"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(label, daily, events):
    daily = daily.reset_index(drop=True)
    changes = pd.to_numeric(events.equity_change, errors="raise").fillna(0).groupby(events.date).sum()
    first_equity = float(daily.equity_close.iloc[0])
    initial = first_equity - float(changes.get(str(daily.date.iloc[0]), 0))
    expected_equity = initial + changes.reindex(daily.date, fill_value=0).cumsum().to_numpy()
    error = float(np.max(np.abs(expected_equity - daily.equity_close.to_numpy(float))))
    fees = float(pd.to_numeric(events.fee, errors="coerce").fillna(0).sum())
    fee_error = abs(fees - float(daily.fees_cumulative.iloc[-1]))
    assert error < 1e-5, (label, error)
    assert fee_error < 1e-5, (label, fee_error)
    mask = daily.seller_route.eq("recovery_future")
    groups = mask.ne(mask.shift(fill_value=False)).cumsum()
    episodes = []
    for _, segment in daily.loc[mask].groupby(groups[mask]):
        end = segment.index[-1]
        episodes.append({"first_held_day": str(segment.date.iloc[0]),
                         "last_held_day": str(segment.date.iloc[-1]),
                         "held_sessions": len(segment),
                         "calendar_days_inclusive": (pd.Timestamp(segment.date.iloc[-1]) - pd.Timestamp(segment.date.iloc[0])).days + 1,
                         "right_censored_or_terminal_route_unknown": bool(end == len(daily) - 1 or pd.isna(daily.seller_route.iloc[min(end + 1, len(daily) - 1)]))})
    recovery_dates = set(daily.loc[mask, "date"])
    symbols = events.symbol.fillna("").astype(str)
    protection = symbols.str.startswith("option|core_put|") & events.date.isin(recovery_dates)
    return {"label": label, "rows": len(daily), "start": str(daily.date.iloc[0]),
            "end": str(daily.date.iloc[-1]), "event_rows": len(events),
            "initial_capital_reconstructed": initial, "final_equity": float(daily.equity_close.iloc[-1]),
            "equity_event_reconciliation_max_abs_error": error,
            "fees_paid": fees, "fee_event_reconciliation_abs_error": fee_error,
            "route_counts": daily.seller_route.fillna("terminal_unknown").value_counts().to_dict(),
            "recovery_core_put_event_rows": int(protection.sum()), "recovery_episodes": episodes}


def main():
    inputs = [IC / "account_daily_paths.csv.gz", IC / "account_events.csv.gz",
              IM / "fix9_full_account_daily_nav.csv.gz", IM / "full_account_events.csv.gz",
              MODEL / "model_latest_full_daily.csv.gz", MODEL / "model_latest_full_events.csv.gz"]
    d_ic, e_ic, d_im, e_im, d_model, e_model = [pd.read_csv(p, low_memory=False) for p in inputs]
    label = "IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9"
    results = [summarize("listed_IC_FIX11", d_ic.loc[d_ic.arm.eq("FIX11_full")], e_ic.loc[e_ic.arm.eq("FIX11_full")]),
               summarize("listed_IM_unchanged_FIX11", d_im.loc[d_im.candidate.eq(label)], e_im.loc[e_im.candidate.eq(label)])]
    for product in ("IC", "IM"):
        results.append(summarize(f"prelisting_model_{product}_latest", d_model.loc[d_model["product"].eq(product)],
                                 e_model.loc[e_model["product"].eq(product)]))
    result = {"classification": "read_only_saved_theoretical_account_evidence_not_new_performance",
              "scope": "Source audit plus event-equity and fee reconciliation of saved same-run files; no fresh baseline replay claim",
              "inputs_sha256": {str(p.relative_to(ROOT)): sha(p) for p in inputs}, "results": results}
    (OUT / "saved_account_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
