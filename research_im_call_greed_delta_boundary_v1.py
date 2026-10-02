"""Research-only near-month MO Call Delta boundary scan after Fear crosses below 75."""
from __future__ import annotations

import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import research_im_call_delta_tenor_v1 as prior_grid


prior = prior_grid.prior
ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260930_im_call_greed_front_delta_boundary_v1"
PREVIOUS = ROOT / "quant_param_scan_runs" / "20260930_im_call_delta_tenor_entry_regime_v1"
SPEC = RUN / "preregistered_spec.md"
SPEC_HASH = RUN / "preregistered_spec.md.sha256"
DELTAS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
CONTROL_IMPORTED = (0.10, 0.15)
FRESH_DELTAS = (0.20, 0.25, 0.30, 0.35, 0.40)
LABELS = tuple(f"greed_cross_front_d{int(delta * 100):02d}" for delta in DELTAS)
CANDIDATES = ("no_call_fix6",) + LABELS
OUTPUTS = ("scan_summary.csv", "window_metrics.csv", "daily_outputs.csv.gz", "run_audit.json")


def git_value(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)
    return done.stdout.strip()


def entry_audit(
    rows: list[dict[str, object]],
    targets: pd.DataFrame,
    calls: pd.DataFrame,
    market: pd.DataFrame,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    index_by_signal = targets.set_index(pd.to_datetime(targets["signal_date"]))["index_close"]
    chain = calls.set_index(["date", "contract"])
    states = market.set_index("date")
    enriched: list[dict[str, object]] = []
    for row in rows:
        day = pd.Timestamp(row["signal_date"])
        contract = str(row["contract"])
        quote = chain.loc[(day, contract)]
        state = states.loc[day]
        spot = float(state["spot_close"])
        strike = float(quote["strike"])
        years = (pd.Timestamp(row["expiry"]) - day).days / 365.0
        iv = prior.v19.implied_volatility(
            float(quote["close"]), spot, strike,
            float(state["rate_close"]), float(state["dividend_close"]), years,
        )
        if iv is None:
            raise RuntimeError(f"Opening contract has no valid IV: {contract} {day.date()}")
        delta = prior.v19.bs_call_delta(
            spot, strike, float(state["rate_close"]), float(state["dividend_close"]), iv, years,
        )
        index_close = float(index_by_signal.loc[day])
        entry_otm = strike / index_close - 1.0
        enriched.append({**row, "index_close": index_close, "strike": strike,
                         "entry_otm": entry_otm, "actual_delta_t_close": float(delta),
                         "entry_inside_5pct_threat": bool(entry_otm <= 0.05 + 1e-12)})
    stats = {
        "entry_inside_5pct_threat": sum(bool(row["entry_inside_5pct_threat"]) for row in enriched),
        "actual_delta_median": float(np.median([row["actual_delta_t_close"] for row in enriched])) if enriched else None,
        "entry_otm_median": float(np.median([row["entry_otm"] for row in enriched])) if enriched else None,
        "premium_points_median": float(np.median([row["premium_points"] for row in enriched])) if enriched else None,
    }
    return enriched, stats


def main() -> None:
    started = time.perf_counter()
    if any((RUN / name).exists() for name in OUTPUTS):
        raise FileExistsError("First-run outputs already exist; refusing overwrite")
    if SPEC_HASH.read_text(encoding="utf-8").split()[0].lower() != prior.sha256(SPEC):
        raise RuntimeError("Pre-registered specification hash mismatch")
    previous_meta = json.loads((PREVIOUS / "scan_meta.json").read_text(encoding="utf-8"))
    previous_audit = json.loads((PREVIOUS / "run_audit.json").read_text(encoding="utf-8"))
    if previous_meta.get("phase") != "complete" or not previous_audit.get("all_pass"):
        raise RuntimeError("Previous validated scan is not complete and passed")

    prior.account.VERSION = "v7"
    prior.account.REENTRY_SELECTOR = prior.fresh_reentry
    prior.account.MARGIN_MODEL = "v3"
    prior.account.EXECUTION_MODEL = "mixed"
    prior.account.LIFECYCLE_VERSION = "v4"
    prior.account.QUARTER_ROLL_AT_SIGNAL_CLOSE = True
    prior.account.PROFIT_RESTRIKE_MULTIPLE = 3.0

    signals = prior.load_signal_frame()
    gate = dict(zip(signals["signal_date"], signals["greed75_downcross"].astype(bool), strict=True))
    original_targets = pd.read_csv(prior.TARGET_PATH)
    upstream = prior.v19.load_upstream()
    market, market_checks = prior.v19.v6.model_market()
    real_market = market[market["date"].ge(prior.v19.REAL_START)].copy()
    calls = prior.v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    dates = pd.DatetimeIndex(upstream["date"])
    rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    monthly_events = prior.v19.monthly_events(prior.v19.REAL_START, dates, rolls)
    lifecycle = pd.read_csv(prior.NATIVE / "native_fix4_seller_lifecycle_v4.csv.gz")

    target_by_candidate: dict[str, pd.DataFrame] = {}
    overlay_audits: list[dict[str, object]] = []
    anchor_evidence: list[dict[str, object]] = []
    for delta in FRESH_DELTAS:
        label = f"greed_cross_front_d{int(delta * 100):02d}"
        native_quote_row = prior.v19.quote_row
        model_quote_refs: set[tuple[str, str]] = set()

        def positive_model_maker_quote(lookup, contract, day):
            quote = native_quote_row(lookup, contract, day)
            if quote is None or not np.isfinite(float(quote["close"])) or float(quote["close"]) <= 0:
                return quote
            if float(quote["volume"]) <= 0 or float(quote["open_interest"]) <= 0:
                model_quote_refs.add((str(contract), str(pd.Timestamp(day).date())))
                quote = quote.copy()
                quote["volume"] = max(1.0, float(quote["volume"]))
                quote["open_interest"] = max(1.0, float(quote["open_interest"]))
            return quote

        with patch.object(prior.v22, "TARGET_DELTA", delta):
            anchors, evidence = prior_grid.anchors_for(
                calls, real_market, monthly_events, label, "front", delta,
            )
            with patch.object(prior.v19, "quote_row", positive_model_maker_quote):
                overlay, overlay_trades, overlay_signals, stats = prior.call_overlay_for_gate(
                    label, gate, upstream, calls, real_market, monthly_events, anchors,
                    require_iv26=False,
                )
        target_by_candidate[label] = prior.targets_from_overlay(original_targets, overlay, lifecycle)
        anchor_evidence.extend(evidence)
        opens = overlay_signals[
            overlay_signals["action"].eq("open")
            & overlay_signals["reason"].isin(["monthly", "daily_entry"])
        ]
        violations = sum(not bool(gate.get(pd.Timestamp(day), False)) for day in pd.to_datetime(opens["eval_date"]))
        overlay_audits.append(
            {"candidate": label, "target_delta": delta,
             "eligible_signal_days": int(signals["greed75_downcross"].sum()),
             "overlay_open_signals": len(opens),
             "overlay_trade_events": len(overlay_trades),
             "entry_gate_violations": violations,
             "scheduled_execution_failures": int(stats["scheduled_execution_failures"]),
             "delayed_trading_days": int(stats["delayed_trading_days"]),
             "threat_signals": int(stats["threat_signals"]),
             "threat_rolls": int(stats["threat_rolls"]),
             "threat_stops": int(stats["threat_no_contract_stops"] + stats["threat_max5_stops"]),
             "expiry_safety_closes": int(stats["expiry_safety_closes"]),
             "positive_quote_zero_volume_or_oi_refs": len(model_quote_refs)}
        )
        print(f"overlay {label} ready", flush=True)

    source = prior.DatedSources()
    seller_candidates = pd.read_csv(prior.NATIVE / "native_fix4_seller_candidates_v1.csv.gz")
    gov = pd.read_csv(
        prior.SOURCE_ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v4" / "chinabond_government_10y.csv",
        parse_dates=["date"],
    ).set_index("date")["gov10y_yield"]
    spot = pd.read_csv(prior.OHLCV["IM"], parse_dates=["date"]).set_index("date")["close"]
    fresh_labels = ("no_call_fix6",) + tuple(
        f"greed_cross_front_d{int(delta * 100):02d}" for delta in FRESH_DELTAS
    )
    dailies: list[pd.DataFrame] = []
    journals: list[pd.DataFrame] = []
    account_audits: list[dict[str, object]] = []
    candidate_origin: dict[str, str] = {}
    for label in fresh_labels:
        targets = original_targets if label == "no_call_fix6" else target_by_candidate[label]
        daily, journal, native_audit = prior.run_account(
            label, targets, label == "no_call_fix6", source, lifecycle,
            seller_candidates, gov, spot,
        )
        dailies.append(daily)
        journals.append(journal)
        candidate_origin[label] = "fresh_native_account_replay"
        opens, dte_stats = prior_grid.account_open_dte(journal)
        holding, sides, _ = prior.call_event_counts(journal, daily)
        account_audits.append(
            {"candidate": label, **native_audit, **dte_stats,
             "call_holding_days": holding, "call_trade_sides": sides,
             "account_entry_gate_violations": 0 if label == "no_call_fix6" else prior.account_entry_gate_violations(journal, gate),
             "call_trades_zero_volume_or_oi": prior_grid.account_zero_volume_call_fills(journal, calls)}
        )
        print(f"account {label} complete", flush=True)

    previous_daily = pd.read_csv(PREVIOUS / "daily_outputs.csv.gz")
    previous_events = pd.read_csv(PREVIOUS / "events.csv.gz")
    for delta in CONTROL_IMPORTED:
        label = f"greed_cross_front_d{int(delta * 100):02d}"
        daily = previous_daily[previous_daily["candidate"].eq(label)].copy().reset_index(drop=True)
        journal = previous_events[previous_events["candidate"].eq(label)].copy().reset_index(drop=True)
        if len(daily) != 986 or journal.empty:
            raise RuntimeError(f"Previous control data incomplete: {label}")
        dailies.append(daily)
        journals.append(journal)
        candidate_origin[label] = "normalized_from_strict_validated_previous_run"
        opens, dte_stats = prior_grid.account_open_dte(journal)
        holding, sides, _ = prior.call_event_counts(journal, daily)
        previous_native = next(row for row in previous_audit["account_audits"] if row["candidate"] == label)
        account_audits.append(
            {"candidate": label, **previous_native, **dte_stats,
             "call_holding_days": holding, "call_trade_sides": sides,
             "account_entry_gate_violations": prior.account_entry_gate_violations(journal, gate),
             "call_trades_zero_volume_or_oi": prior_grid.account_zero_volume_call_fills(journal, calls)}
        )
        previous_overlay = next(row for row in previous_audit["overlay_audits"] if row["candidate"] == label)
        overlay_audits.append({**previous_overlay, "origin": candidate_origin[label]})

    all_daily = pd.concat(dailies, ignore_index=True)
    all_events = pd.concat(journals, ignore_index=True)
    frozen_no_call = pd.read_csv(prior.NO_CALL / "daily_outputs.csv.gz")
    frozen_no_call = frozen_no_call[frozen_no_call["candidate"].eq("IM_repeat_roll_3p0x")].reset_index(drop=True)
    frozen_d20 = previous_daily[previous_daily["candidate"].eq("greed_cross_front_d20")].reset_index(drop=True)
    parity: dict[str, float] = {}
    for label, reference in {"no_call_fix6": frozen_no_call,
                             "greed_cross_front_d20": frozen_d20}.items():
        generated = all_daily[all_daily["candidate"].eq(label)].reset_index(drop=True)
        if not generated["date"].equals(reference["date"]):
            raise RuntimeError(f"Date parity mismatch: {label}")
        parity[label] = float(np.max(np.abs(generated["nav"].to_numpy(float) - reference["nav"].to_numpy(float))))
    if max(parity.values()) > 1e-10:
        raise RuntimeError(f"Frozen parity failed: {parity}")

    audits = {str(row["candidate"]): row for row in account_audits}
    openings: list[dict[str, object]] = []
    entry_stats: dict[str, dict[str, object]] = {}
    for label in CANDIDATES:
        journal = all_events[all_events["candidate"].eq(label)].reset_index(drop=True)
        rows, _ = prior_grid.account_open_dte(journal)
        enriched, stats = entry_audit(rows, original_targets, calls, real_market)
        openings.extend({"candidate": label, **row} for row in enriched)
        entry_stats[label] = stats

    metric_long: list[dict[str, object]] = []
    metric_wide: list[dict[str, object]] = []
    unavailable: dict[str, dict[str, str]] = {}
    for label in CANDIDATES:
        daily = all_daily[all_daily["candidate"].eq(label)].reset_index(drop=True)
        audit = audits[label]
        rows, wide, missing = prior.metric_rows(
            daily, label, int(audit["call_holding_days"]),
            int(audit["call_trade_sides"]), int(audit["account_open_episodes"]),
        )
        delta = float(label.rsplit("d", 1)[-1]) / 100 if label != "no_call_fix6" else np.nan
        for row in rows:
            row.update({"target_delta": delta, "origin": candidate_origin[label],
                        "account_open_episodes": int(audit["account_open_episodes"])})
        wide.update({"target_delta": delta, "origin": candidate_origin[label],
                     "account_open_episodes": int(audit["account_open_episodes"])})
        metric_long.extend(rows)
        metric_wide.append(wide)
        if missing:
            unavailable[label] = missing
    long = pd.DataFrame(metric_long)
    wide = pd.DataFrame(metric_wide)
    full = long[long["segment"].eq("full")].set_index("candidate")
    base = full.loc["no_call_fix6"]
    reference = full.loc["greed_cross_front_d20"]
    comparison: list[dict[str, object]] = []
    for delta in DELTAS:
        label = f"greed_cross_front_d{int(delta * 100):02d}"
        row = full.loc[label]
        comparison.append(
            {"candidate": label, "target_delta": delta,
             "cagr": float(row.ann_return), "sharpe": float(row.sharpe_repo),
             "max_dd": float(row.max_dd),
             "cagr_vs_no_call": float(row.ann_return - base.ann_return),
             "max_dd_vs_no_call": float(row.max_dd - base.max_dd),
             "cagr_vs_d20": float(row.ann_return - reference.ann_return),
             "max_dd_vs_d20": float(row.max_dd - reference.max_dd),
             "account_open_episodes": int(audits[label]["account_open_episodes"]),
             "call_holding_days": int(audits[label]["call_holding_days"]),
             "call_trade_sides": int(audits[label]["call_trade_sides"]),
             "call_trades_zero_volume_or_oi": int(audits[label]["call_trades_zero_volume_or_oi"]),
             "entry_inside_5pct_threat": int(entry_stats[label]["entry_inside_5pct_threat"]),
             "actual_delta_median": entry_stats[label]["actual_delta_median"],
             "entry_otm_median": entry_stats[label]["entry_otm_median"],
             "premium_points_median": entry_stats[label]["premium_points_median"],
             "threat_rolls": next(int(x["threat_rolls"]) for x in overlay_audits if x["candidate"] == label),
             "origin": candidate_origin[label]}
        )
    compare = pd.DataFrame(comparison)
    recent = long[long["segment"].isin(["last_3y", "last_1y"])].pivot(
        index="candidate", columns="segment", values="ann_return"
    )
    audit_pass = bool(
        max(parity.values()) <= 1e-10
        and all(int(x["entry_gate_violations"]) == 0 for x in overlay_audits)
        and all(int(x["scheduled_execution_failures"]) == 0 for x in overlay_audits)
        and all(int(x["account_entry_gate_violations"]) == 0 for x in account_audits)
        and all(math.isfinite(float(row["ann_return"])) for _, row in full.iterrows())
    )
    watch = [
        label for label in LABELS
        if float(full.loc[label, "ann_return"] - base.ann_return) >= 0.0025 - 1e-12
        and float(full.loc[label, "max_dd"] - base.max_dd) >= -0.0025 - 1e-12
        and not (
            float(recent.loc[label, "last_3y"]) < float(recent.loc["no_call_fix6", "last_3y"])
            and float(recent.loc[label, "last_1y"]) < float(recent.loc["no_call_fix6", "last_1y"])
        )
    ]
    decision = "rerun_required" if not audit_pass else "watchlist" if watch else "keep_default"
    best_label = compare.loc[compare["cagr"].idxmax(), "candidate"]
    boundary = [
        f"{int(DELTAS[i - 1] * 100)}->{int(DELTAS[i] * 100)}"
        for i in range(1, len(DELTAS))
        if float(compare.iloc[i].cagr) <= float(compare.iloc[i - 1].cagr)
    ]
    stability = "data_sensitive" if audit_pass and watch else "reject" if audit_pass else "data_sensitive"

    long.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    all_daily.to_csv(RUN / "daily_outputs.csv.gz", index=False, compression="gzip")
    all_events.to_csv(RUN / "events.csv.gz", index=False, compression="gzip")
    pd.concat([frame.assign(candidate=label) for label, frame in target_by_candidate.items()],
              ignore_index=True).to_csv(RUN / "call_target_schedules_fresh.csv.gz", index=False, compression="gzip")
    pd.DataFrame(openings).to_csv(RUN / "account_call_open_events.csv", index=False)
    pd.DataFrame(anchor_evidence).to_csv(RUN / "tenor_anchor_evidence_fresh.csv", index=False)
    pd.DataFrame(overlay_audits).to_csv(RUN / "call_overlay_audit.csv", index=False)
    compare.to_csv(RUN / "delta_boundary_comparison.csv", index=False)
    signals.to_csv(RUN / "signal_inputs.csv.gz", index=False, compression="gzip")

    source_files = [
        SPEC, Path(__file__), ROOT / "research_im_call_delta_tenor_v1.py",
        ROOT / "research_im_call_mom_fear_entry_v1.py", prior.MOM_PATH,
        prior.FEAR_PATH, prior.TARGET_PATH,
        ROOT / "im_mo_call_daily_d10_threat_roll_v27.py",
        ROOT / "im_mo_call_daily_entry_profit_roll_v22.py",
        ROOT / "im_mo_call_overwrite_delta_tenor_v19.py",
        ROOT / "im_mo_call_valuation_threat_roll_v25.py",
        prior.NATIVE / "native_fix4_account_v1.py",
        PREVIOUS / "daily_outputs.csv.gz", PREVIOUS / "events.csv.gz",
        PREVIOUS / "run_audit.json",
    ]
    hashes = {str(path.relative_to(ROOT)): prior.sha256(path) for path in source_files}
    run_audit = {
        "classification": "IM_GREED_CROSS_FRONT_CALL_DELTA_BOUNDARY_RESEARCH_ONLY",
        "decision": decision, "stability_label": stability, "all_pass": audit_pass,
        "baseline_parity": parity, "market_checks": market_checks,
        "candidate_origin": candidate_origin, "overlay_audits": overlay_audits,
        "account_audits": account_audits, "entry_stats": entry_stats,
        "cagr_best_label": best_label, "non_improving_delta_steps": boundary,
        "watchlist_candidates": watch, "unavailable_segments": unavailable,
        "fear_point_in_time_verified": False, "production_source_changed": False,
    }
    (RUN / "run_audit.json").write_text(json.dumps(run_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    display = long[long["segment"].isin(["full", "last_3y", "last_1y"])]
    lines = ["|候选|窗口|CAGR|Sharpe|MaxDD|真实开仓周期|",
             "|---|---|---:|---:|---:|---:|"]
    for row in display.itertuples(index=False):
        lines.append(
            f"|{row.candidate}|{row.segment}|{float(row.ann_return):.2%}|"
            f"{float(row.sharpe_repo):.3f}|{float(row.max_dd):.2%}|{int(row.account_open_episodes)}|"
        )
    record = f"""# IM 贪婪跌破75后近月 Call Delta 上探

## Run Metadata

- Run id: `{RUN.name}`; run date: {datetime.now().astimezone().isoformat(timespec='seconds')}; timezone: Asia/Shanghai.
- Project: IC和IM滚动套利; strategy: v1.4-fix9 no-Call native IM account.
- Subsystem: fixed-core covered Call; scan type: single_parameter.
- Git commit: `{git_value('rev-parse', 'HEAD')}`; pre-existing worktree was dirty.

## Research Question

- Baseline: no Call; control: greed-cross front 0.20 Delta; grid: target Delta 0.10 through 0.40 in 0.05 steps.
- Decision target: keep_default/watchlist/rerun_required; source-change rule: research_only_no_source_change.
- Required windows: Full/3Y/1Y; 5Y/10Y explicit N/A. Thresholds and rerun triggers: `preregistered_spec.md`.

## Implementation Anchor

- Native `native_fix4_account_v1.replay` with v7/mixed/v4/repeat_roll/3x, prior verified Call selectors/lifecycle, runtime target interception.
- Imported 0.10/0.15 control rows come from the strict-validated prior run; no Call and 0.20 are fresh. Parity max absolute NAV errors: {json.dumps(parity)}.

## Data Snapshot

- Real listed IM/MO account days: 2022-07-22—2026-08-14, 986 rows; signal end 2026-08-13.
- MOM120: native risk signal. Fear: downloaded 2026-09-28 historical snapshot, point-in-time availability unverified.
- Raw dated futures/options, no equity adjustment; Asia/Shanghai IM/MO trading sessions.

## Cost and Execution Assumptions

- Futures 1bp, bought Put 5bp, Call 1bp one-way; 30% performance margin buffer per 1x and 3% net annual interest on residual cash.
- T close signal, T+1 dated close historical market-maker option paper fill, without order-book spread/capacity proof.
- Existing positive quote with zero daily volume/OI may fill under this paper model; counted separately.

## Runtime Override Plan

- Only target Delta varies for fresh candidates. Constants restored after each path. Front tenor and 5% rescue unchanged.
- 0.10/0.15 are explicit normalized controls from the previous strict-valid run; all other paths are fresh native-account replays.

## Commands

```powershell
python -X utf8 research_im_call_greed_delta_boundary_v1.py
```

## Output Files

- Standard metrics: `scan_summary.csv`, `window_metrics.csv`, `scan_meta.json`, `command_log.txt`.
- Evidence: `delta_boundary_comparison.csv`, `account_call_open_events.csv`, `call_overlay_audit.csv`, `daily_outputs.csv.gz`, `events.csv.gz`, `run_audit.json`.

## Full-Sample Results

{chr(10).join(lines)}

## Window Results

- Full/3Y/1Y are in `scan_summary.csv`; 5Y/10Y N/A reasons in `scan_meta.json`.

## Stability Classification

- Label: `{stability}`. Few entry episodes, repeated in-sample parameter search, and unverified historical Fear availability dominate numerical improvements.
- Non-improving adjacent Full-CAGR steps: {', '.join(boundary) if boundary else 'none through 0.40'}.

## Decision

- Decision: `{decision}`; best Full CAGR label: `{best_label}`; mechanical watchlist set: {', '.join(watch) if watch else 'none'}.
- Audit pass: {audit_pass}. Production action: none.

## User-Facing Summary

This is a historical native-account counterfactual for the user-requested Delta boundary. It is not a verified historical real-time Fear strategy or live trading approval.
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {"scan_type": "single_parameter",
         "baseline": {"candidate": "no_call_fix6", "definition": "native repeat-roll 3x account with Call disabled"},
         "candidate_grid": list(CANDIDATES), "candidate_origin": candidate_origin,
         "data_snapshot": {"start": "2022-07-22", "end": "2026-08-14", "rows": 986,
                           "fear_point_in_time_verified": False,
                           "mom120_source": str(prior.MOM_PATH), "fear_source": str(prior.FEAR_PATH)},
         "cost_model": {"future_one_way": 0.0001, "buyer_put_one_way": 0.0005,
                        "im_call_one_way": 0.0001, "cash_annual_net": 0.03,
                        "futures_performance_buffer_per_1x": 0.30},
         "parity_check": parity, "source_hashes": hashes,
         "unavailable_segments": unavailable,
         "outputs": {"record": str(RUN / "record.md"), "scan_summary": str(RUN / "scan_summary.csv"),
                     "window_metrics": str(RUN / "window_metrics.csv"), "scan_meta": str(meta_path),
                     "command_log": str(RUN / "command_log.txt"),
                     "daily_outputs": str(RUN / "daily_outputs.csv.gz"),
                     "run_audit": str(RUN / "run_audit.json")},
         "decision": decision, "stability_label": stability,
         "elapsed_sec": time.perf_counter() - started,
         "warnings": ["Fear historical series is a later-downloaded snapshot without point-in-time archive.",
                      "0.10/0.15 controls were normalized from the prior strict-valid run.",
                      "Historical market-maker price is not an order-book fill.",
                      "The worktree was already dirty."]}
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python -X utf8 research_im_call_greed_delta_boundary_v1.py\n")
        handle.write(f"cwd={ROOT}\n")
        handle.write(f"elapsed_sec={time.perf_counter() - started:.3f}\n")
    print(json.dumps({"decision": decision, "stability": stability, "parity": parity,
                      "best_cagr": best_label, "non_improving_steps": boundary,
                      "watchlist": watch, "all_pass": audit_pass}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
