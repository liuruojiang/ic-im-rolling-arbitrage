"""Read-only reconciliation of completed research runs against their events.

This validates the actual saved replay, not a separate return calculator.  It
does not certify fill availability or the historical model's realism.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
RUN = HERE.parent
TOLERANCE = 1e-6


def near(actual, expected, label):
    error = float(np.max(np.abs(np.asarray(actual, float) - np.asarray(expected, float))))
    assert np.isfinite(error) and error < TOLERANCE, (label, error)
    return error


def check(path):
    daily = pd.read_csv(path / "daily.csv.gz")
    events = pd.read_csv(path / "events.csv.gz", low_memory=False)
    audit = json.loads((path / "audit.json").read_text(encoding="utf-8"))
    event_daily = events.groupby("date").equity_change.sum().reindex(daily.date, fill_value=0)
    reconstructed = audit["initial_capital"] + np.cumsum(event_daily.to_numpy(float))
    equity_error = near(reconstructed, daily.equity_close, "account equity")
    cash_daily = events.groupby("date").cash_change.sum().reindex(daily.date, fill_value=0)
    cash_error = near(audit["initial_capital"] + np.cumsum(cash_daily.to_numpy(float)),
                      daily.cash_close, "account cash")
    fee_error = near(events.fee.fillna(0).sum(), daily.fees_cumulative.iloc[-1], "fees")
    result = {"run": path.relative_to(RUN).as_posix(), "daily_rows": len(daily),
              "event_rows": len(events), "equity_max_error": equity_error,
              "cash_max_error": cash_error, "fee_error": fee_error,
              "files_sha256": {name: hashlib.sha256((path / name).read_bytes()).hexdigest()
                               for name in ("daily.csv.gz", "events.csv.gz", "audit.json")}}
    if audit["research_arm"] == "A0":
        assert audit["saved_baseline_parity"]["status"] == "PASS"
        assert audit["saved_event_parity"]["status"] == "PASS"
        return dict(result, status="PASS", baseline_parity="PASS")

    for key, filename in (("engine_sha256", "recovery_account.py"),
                          ("controller_sha256", "recovery_cycle_controller.py"),
                          ("preregistered_spec_sha256", "preregistered_spec.txt")):
        assert audit[key] == hashlib.sha256((RUN / filename).read_bytes()).hexdigest(), \
            f"Saved candidate output no longer matches {filename}"

    attribution = pd.DataFrame(audit["recovery_cycle_attribution"])
    assert attribution.entry_index.is_unique, "An account event was assigned to multiple cycles"
    for record in attribution.itertuples(index=False):
        actual = events.iloc[int(record.entry_index)]
        assert actual.date == record.date and actual.event == record.event and actual.symbol == record.symbol
        near(actual.equity_change, record.equity_change, "attributed event PnL")
        near(0.0 if pd.isna(actual.fee) else actual.fee, record.fee, "attributed event fee")

    cycle_results = []
    for cycle in audit["recovery_cycle_records"]:
        a = attribution.loc[attribution.cycle_id.eq(cycle["cycle_id"])]
        near(a.equity_change.sum(), cycle["final_net_pnl"], "cycle net PnL")
        near(sum(cycle["pnl_by_sleeve"].values()), cycle["final_net_pnl"], "cycle sleeve sum")
        for sleeve, expected in cycle["pnl_by_sleeve"].items():
            part = a.loc[a.category.eq(sleeve)]
            near(part.equity_change.sum(), expected, "sleeve PnL")
            near(part.fee.sum(), cycle["fees_by_sleeve"][sleeve], "sleeve fee")
        insurance = a.loc[a.category.eq("recovery_put")]
        insurance_events = events.iloc[insurance.entry_index.to_numpy(int)]
        net_cash = float(insurance_events.cash_change.sum())
        net_quantity = float(insurance_events.quantity_change.sum())
        if cycle["closed"]:
            near(net_quantity, 0.0, "closed cycle insurance quantity")
            near(net_cash, cycle["pnl_by_sleeve"]["recovery_put"],
                 "flat insurance cash includes all premiums, proceeds and fees")
            if cycle["exit_action"] == "EXIT_RECOVERY_AT_BREAKEVEN":
                assert cycle["decision_net_pnl"] >= 0.0, "Exit did not cover estimated close fee"
            assert cycle["negative_pnl_after_next_open"] == (cycle["final_net_pnl"] < 0)
        else:
            assert cycle["right_censored"] is True
        cycle_results.append({"cycle_id": cycle["cycle_id"], "closed": cycle["closed"],
                              "attributed_events": len(a), "net_pnl": cycle["final_net_pnl"],
                              "insurance_net_cash": net_cash, "insurance_quantity_remaining": net_quantity})

    # The active daily cycle balance is the cumulative attributed event PnL.
    for cycle_id, a in attribution.groupby("cycle_id", sort=False):
        cumulative = a.groupby("date").equity_change.sum().sort_index().cumsum()
        rows = daily.loc[daily.date.isin(cumulative.index) & daily.recovery_cycle_active.eq(True)]
        if len(rows):
            near(rows.recovery_cycle_net_pnl, cumulative.reindex(rows.date), "daily active cycle")

    insurance = events.loc[events.symbol.fillna("").str.startswith("option|recovery_put|")]
    trades = insurance.loc[insurance.event.eq("trade")]
    assert trades.phase.eq("open").all()
    assert trades.signal_date.lt(trades.date).all()
    assert trades.price.notna().all() and trades.price.gt(0).all()
    assert trades.price_basis.notna().all()
    buys = trades.loc[trades.quantity_change.gt(0)]
    for trade in buys.itertuples(index=False):
        matching = [p for p in audit["recovery_protection_plans"]
                    if p["signal_date"] == trade.signal_date and p["execution_date"] == trade.date
                    and trade.symbol == "option|recovery_put|" + p["contract"]]
        assert len(matching) == 1, "Insurance buy lacked an exact T-known plan"
        assert matching[0]["three_x_enabled"] is False
    near(insurance.quantity_change.groupby(insurance.date).sum().reindex(daily.date, fill_value=0).cumsum(),
         daily.recovery_put_held_quantity, "held insurance quantity")
    return dict(result, status="PASS", cycles=cycle_results,
                insurance_fills=len(trades), insurance_fill_basis=trades.price_basis.value_counts().to_dict(),
                attributed_events=len(attribution))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--panels", nargs="+", choices=["listed", "model"], default=["listed", "model"])
    args = parser.parse_args()
    results = []
    for panel in args.panels:
        for product in ("IC", "IM"):
            for arm in ("A0", "A", "B", "C"):
                path = RUN / "runs" / panel / product / arm
                if not all((path / name).is_file() for name in ("daily.csv.gz", "events.csv.gz", "audit.json")):
                    continue
                results.append(check(path))
    assert results
    output = {"status": "PASS", "scope": "Completed saved outputs only; event/cash/fee/cycle/insurance reconciliation",
              "panels_requested": args.panels, "runs_checked": len(results), "results": results}
    destination = HERE / ("replayed_" + "_".join(args.panels) + "_account_audit.json")
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "runs_checked": len(results), "output": str(destination)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
