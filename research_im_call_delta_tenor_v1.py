"""Research-only real MO Call Delta/tenor grid on the frozen native IM account."""
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

import research_im_call_mom_fear_entry_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260930_im_call_delta_tenor_entry_regime_v1"
SPEC = RUN / "preregistered_spec.md"
SPEC_HASH = RUN / "preregistered_spec.md.sha256"
PRIOR_RUN = ROOT / "quant_param_scan_runs" / (
    "20260930_ic_im_v1_4_fix9_no_call_baseline_im_core_covered_call_research_"
    "entry_regime_mom120_and_fear_greed_v2_account_gate"
)
REGIMES = {
    "mom25": "momneg_fear_gt25",
    "greed_cross": "greed75_downcross",
}
DELTAS = (0.10, 0.15, 0.20)
TENORS = ("front", "second_listed")
BASELINES = ("no_call_fix6", "old_iv26")
GRID = tuple(
    (f"{regime}_{tenor}_d{int(round(delta * 100)):02d}", regime, tenor, delta)
    for regime in REGIMES
    for tenor in TENORS
    for delta in DELTAS
)
CANDIDATES = BASELINES + tuple(row[0] for row in GRID)
OUTPUTS = ("scan_summary.csv", "window_metrics.csv", "daily_outputs.csv.gz", "run_audit.json")


def git_value(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, check=False, capture_output=True, text=True)
    return done.stdout.strip()


def anchors_for(
    calls: pd.DataFrame,
    market: pd.DataFrame,
    events: pd.DataFrame,
    label: str,
    tenor: str,
    delta: float,
) -> tuple[list[prior.v19.Selection], list[dict[str, object]]]:
    if tenor == "front":
        anchors = prior.v19.build_real_selections(calls, market, events, "front", delta, label)
        evidence = [
            {"candidate": label, "signal_date": str(s.eval_date.date()),
             "front_expiry": str(s.expiry.date()), "chosen_expiry": str(s.expiry.date()),
             "chosen_dte": int((s.expiry - s.eval_date).days)}
            for s in anchors
        ]
        return anchors, evidence
    if tenor != "second_listed":
        raise ValueError(tenor)
    market_lookup = market.set_index("date")
    anchors: list[prior.v19.Selection] = []
    evidence: list[dict[str, object]] = []
    for event in events.itertuples(index=False):
        day = pd.Timestamp(event.eval_date)
        chain = calls[calls["date"].eq(day)]
        _, front_expiry = prior.v19.target_month(chain, event, "front")
        later = chain[chain["actual_expiry"].gt(front_expiry)][
            ["contract_month", "actual_expiry"]
        ].drop_duplicates().sort_values(["actual_expiry", "contract_month"])
        if later.empty:
            raise RuntimeError(f"Second listed expiry absent at {day.date()}")
        chosen = later.iloc[0]
        selected = prior.v22.real_selection_for_expiry(
            calls, market_lookup.loc[day], day, pd.Timestamp(event.execution_date),
            pd.Timestamp(chosen.contract_month), pd.Timestamp(chosen.actual_expiry), label, "monthly",
        )
        if selected is None:
            raise RuntimeError(f"Second listed D{delta:.2f} selection absent at {day.date()}")
        anchors.append(selected)
        evidence.append(
            {"candidate": label, "signal_date": str(day.date()),
             "front_expiry": str(front_expiry.date()), "chosen_expiry": str(selected.expiry.date()),
             "chosen_dte": int((selected.expiry - day).days)}
        )
    return anchors, evidence


def account_open_dte(journal: pd.DataFrame) -> tuple[list[dict[str, object]], dict[str, object]]:
    trades = journal[
        journal["event"].eq("trade") & journal["symbol"].fillna("").astype(str).str.startswith("option|call|")
    ].sort_values(["date", "order_id"])
    positions: dict[str, float] = {}
    rows: list[dict[str, object]] = []
    for day, group in trades.groupby("date", sort=True):
        before = sum(positions.values())
        for row in group.itertuples(index=False):
            positions[str(row.symbol)] = positions.get(str(row.symbol), 0.0) + float(row.quantity_change)
        after = sum(positions.values())
        if before >= -1e-9 and after < -1e-9:
            negative = group[pd.to_numeric(group["quantity_change"], errors="coerce").lt(0)]
            row = negative.iloc[-1]
            contract = str(row["symbol"]).split("|")[-1]
            signal_day = pd.Timestamp(row["signal_date"])
            execution_day = pd.Timestamp(day)
            expiry = pd.Timestamp(prior.account.option_expiry("IM", contract))
            rows.append(
                {"signal_date": str(signal_day.date()), "execution_date": str(execution_day.date()),
                 "contract": contract, "expiry": str(expiry.date()),
                 "signal_dte": int((expiry - signal_day).days),
                 "execution_dte": int((expiry - execution_day).days),
                 "premium_points": float(row["price"]),
                 "price_basis": str(row["price_basis"])}
            )
    dtes = [int(row["signal_dte"]) for row in rows]
    stats: dict[str, object] = {
        "account_open_episodes": len(rows),
        "signal_dte_min": min(dtes) if dtes else None,
        "signal_dte_median": float(np.median(dtes)) if dtes else None,
        "signal_dte_max": max(dtes) if dtes else None,
        "signal_dte_below30": sum(d < 30 for d in dtes),
        "signal_dte_above60": sum(d > 60 for d in dtes),
    }
    return rows, stats


def account_zero_volume_call_fills(journal: pd.DataFrame, calls: pd.DataFrame) -> int:
    trades = journal[
        journal["event"].eq("trade") & journal["symbol"].fillna("").astype(str).str.startswith("option|call|")
    ][["date", "symbol"]].copy()
    if trades.empty:
        return 0
    trades["contract"] = trades["symbol"].astype(str).str.split("|").str[-1]
    trades["date"] = pd.to_datetime(trades["date"])
    matched = trades.merge(
        calls[["date", "contract", "volume", "open_interest", "close"]],
        on=["date", "contract"], how="left", validate="many_to_one",
    )
    if matched[["volume", "open_interest", "close"]].isna().any().any():
        raise RuntimeError("Account Call trade lacks a dated source quote")
    return int((matched["volume"].le(0) | matched["open_interest"].le(0)).sum())


def main() -> None:
    started = time.perf_counter()
    if any((RUN / name).exists() for name in OUTPUTS):
        raise FileExistsError("First-run outputs already exist; refusing overwrite")
    if SPEC_HASH.read_text(encoding="utf-8").split()[0].lower() != prior.sha256(SPEC):
        raise RuntimeError("Pre-registered specification hash mismatch")

    prior.account.VERSION = "v7"
    prior.account.REENTRY_SELECTOR = prior.fresh_reentry
    prior.account.MARGIN_MODEL = "v3"
    prior.account.EXECUTION_MODEL = "mixed"
    prior.account.LIFECYCLE_VERSION = "v4"
    prior.account.QUARTER_ROLL_AT_SIGNAL_CLOSE = True
    prior.account.PROFIT_RESTRIKE_MULTIPLE = 3.0

    signals = prior.load_signal_frame()
    gate_maps = {
        regime: dict(zip(signals["signal_date"], signals[col].astype(bool), strict=True))
        for regime, col in REGIMES.items()
    }
    original_targets = pd.read_csv(prior.TARGET_PATH)
    upstream = prior.v19.load_upstream()
    market, market_checks = prior.v19.v6.model_market()
    real_market = market[market["date"].ge(prior.v19.REAL_START)].copy()
    calls = prior.v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    dates = pd.DatetimeIndex(upstream["date"])
    rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    events = prior.v19.monthly_events(prior.v19.REAL_START, dates, rolls)
    lifecycle = pd.read_csv(prior.NATIVE / "native_fix4_seller_lifecycle_v4.csv.gz")

    target_by_candidate: dict[str, pd.DataFrame] = {}
    overlay_audits: list[dict[str, object]] = []
    tenor_evidence: list[dict[str, object]] = []
    for label, regime, tenor, delta in GRID:
        native_quote_row = prior.v19.quote_row
        model_maker_quote_refs: set[tuple[str, str]] = set()

        def positive_model_maker_quote(lookup, contract, day):
            quote = native_quote_row(lookup, contract, day)
            if quote is None or not np.isfinite(float(quote["close"])) or float(quote["close"]) <= 0:
                return quote
            if float(quote["volume"]) <= 0 or float(quote["open_interest"]) <= 0:
                model_maker_quote_refs.add((str(contract), str(pd.Timestamp(day).date())))
                quote = quote.copy()
                quote["volume"] = max(1.0, float(quote["volume"]))
                quote["open_interest"] = max(1.0, float(quote["open_interest"]))
            return quote

        with patch.object(prior.v22, "TARGET_DELTA", delta):
            anchors, anchor_rows = anchors_for(calls, real_market, events, label, tenor, delta)
            with patch.object(prior.v19, "quote_row", positive_model_maker_quote):
                overlay, trades, call_signals, stats = prior.call_overlay_for_gate(
                    label, gate_maps[regime], upstream, calls, real_market, events, anchors,
                    require_iv26=False,
                )
        targets = prior.targets_from_overlay(original_targets, overlay, lifecycle)
        target_by_candidate[label] = targets
        tenor_evidence.extend(anchor_rows)
        opens = call_signals[
            call_signals["action"].eq("open") & call_signals["reason"].isin(["monthly", "daily_entry"])
        ]
        gate_violations = sum(
            not bool(gate_maps[regime].get(pd.Timestamp(day), False))
            for day in pd.to_datetime(opens["eval_date"])
        )
        overlay_audits.append(
            {"candidate": label, "regime": regime, "tenor": tenor, "target_delta": delta,
             "eligible_signal_days": int(signals[REGIMES[regime]].sum()),
             "overlay_open_signals": len(opens), "overlay_trade_events": len(trades),
             "overlay_call_days": int(overlay["call_contract"].fillna("").ne("").sum()),
             "entry_gate_violations": int(gate_violations),
             "scheduled_execution_failures": int(stats["scheduled_execution_failures"]),
             "delayed_trading_days": int(stats["delayed_trading_days"]),
             "positive_quote_zero_volume_or_oi_refs": len(model_maker_quote_refs),
             "threat_rolls": int(stats["threat_rolls"]),
             "expiry_safety_closes": int(stats["expiry_safety_closes"])}
        )
        print(f"overlay {label} ready", flush=True)

    source = prior.DatedSources()
    seller_candidates = pd.read_csv(prior.NATIVE / "native_fix4_seller_candidates_v1.csv.gz")
    gov = pd.read_csv(
        prior.SOURCE_ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v4" / "chinabond_government_10y.csv",
        parse_dates=["date"],
    ).set_index("date")["gov10y_yield"]
    spot = pd.read_csv(prior.OHLCV["IM"], parse_dates=["date"]).set_index("date")["close"]
    dailies: list[pd.DataFrame] = []
    journals: list[pd.DataFrame] = []
    account_audits: list[dict[str, object]] = []
    open_events: list[dict[str, object]] = []
    for label in CANDIDATES:
        targets = original_targets if label in BASELINES else target_by_candidate[label]
        daily, journal, account_audit = prior.run_account(
            label, targets, label == "no_call_fix6", source, lifecycle,
            seller_candidates, gov, spot,
        )
        dailies.append(daily)
        journals.append(journal)
        holding, sides, _ = prior.call_event_counts(journal, daily)
        opened, dte = account_open_dte(journal)
        for row in opened:
            open_events.append({"candidate": label, **row})
        regime = next((r for name, r, _, _ in GRID if name == label), None)
        violations = (
            prior.account_entry_gate_violations(journal, gate_maps[regime])
            if regime is not None else 0
        )
        account_audits.append(
            {"candidate": label, **account_audit, "call_holding_days": holding,
             "call_trade_sides": sides, **dte,
             "call_trades_zero_volume_or_oi": account_zero_volume_call_fills(journal, calls),
             "account_entry_gate_violations": violations}
        )
        print(f"account {label} complete", flush=True)

    all_daily = pd.concat(dailies, ignore_index=True)
    all_events = pd.concat(journals, ignore_index=True)
    frozen_no_call = pd.read_csv(prior.NO_CALL / "daily_outputs.csv.gz")
    frozen_no_call = frozen_no_call[frozen_no_call["candidate"].eq("IM_repeat_roll_3p0x")].reset_index(drop=True)
    frozen_old = pd.read_csv(prior.L8 / "daily_outputs.csv.gz")
    frozen_old = frozen_old[frozen_old["candidate"].eq("IM_baseline_repeat_roll")].reset_index(drop=True)
    frozen_prior = pd.read_csv(PRIOR_RUN / "daily_outputs.csv.gz")
    references = {
        "no_call_fix6": frozen_no_call,
        "old_iv26": frozen_old,
        "mom25_front_d10": frozen_prior[frozen_prior["candidate"].eq("momneg_fear_gt25")].reset_index(drop=True),
        "greed_cross_front_d10": frozen_prior[frozen_prior["candidate"].eq("greed75_downcross")].reset_index(drop=True),
    }
    parity: dict[str, float] = {}
    for label, reference in references.items():
        generated = all_daily[all_daily["candidate"].eq(label)].reset_index(drop=True)
        if not generated["date"].equals(reference["date"]):
            raise RuntimeError(f"Date parity mismatch: {label}")
        parity[label] = float(np.max(np.abs(generated["nav"].to_numpy(float) - reference["nav"].to_numpy(float))))
    if max(parity.values()) > 1e-10:
        raise RuntimeError(f"Frozen baseline parity failed: {parity}")

    audits_by_label = {str(row["candidate"]): row for row in account_audits}
    grid_by_label = {name: (regime, tenor, delta) for name, regime, tenor, delta in GRID}
    metric_long: list[dict[str, object]] = []
    metric_wide: list[dict[str, object]] = []
    unavailable: dict[str, dict[str, str]] = {}
    for label in CANDIDATES:
        daily = all_daily[all_daily["candidate"].eq(label)].reset_index(drop=True)
        audit = audits_by_label[label]
        rows, wide, missing = prior.metric_rows(
            daily, label, int(audit["call_holding_days"]),
            int(audit["call_trade_sides"]), int(audit["account_open_episodes"]),
        )
        regime, tenor, delta = grid_by_label.get(label, ("baseline", "baseline", np.nan))
        for row in rows:
            row.update({"entry_regime": regime, "tenor": tenor, "target_delta": delta,
                        "account_open_episodes": int(audit["account_open_episodes"])})
        wide.update({"entry_regime": regime, "tenor": tenor, "target_delta": delta,
                     "account_open_episodes": int(audit["account_open_episodes"])})
        metric_long.extend(rows)
        metric_wide.append(wide)
        if missing:
            unavailable[label] = missing
    long = pd.DataFrame(metric_long)
    wide = pd.DataFrame(metric_wide)
    full = long[long["segment"].eq("full")].set_index("candidate")
    base = full.loc["no_call_fix6"]
    comparison: list[dict[str, object]] = []
    for label, regime, tenor, delta in GRID:
        reference = full.loc[f"{regime}_front_d10"]
        row = full.loc[label]
        comparison.append(
            {"candidate": label, "entry_regime": regime, "tenor": tenor, "target_delta": delta,
             "cagr_vs_no_call": float(row.ann_return - base.ann_return),
             "max_dd_vs_no_call": float(row.max_dd - base.max_dd),
             "cagr_vs_same_regime_front_d10": float(row.ann_return - reference.ann_return),
             "max_dd_vs_same_regime_front_d10": float(row.max_dd - reference.max_dd),
             "account_open_episodes": int(audits_by_label[label]["account_open_episodes"]),
             "signal_dte_min": audits_by_label[label]["signal_dte_min"],
             "signal_dte_median": audits_by_label[label]["signal_dte_median"],
             "signal_dte_max": audits_by_label[label]["signal_dte_max"],
             "signal_dte_above60": audits_by_label[label]["signal_dte_above60"]}
        )
    compare = pd.DataFrame(comparison)
    all_pass = bool(
        max(parity.values()) <= 1e-10
        and all(int(row["entry_gate_violations"]) == 0 for row in overlay_audits)
        and all(int(row["scheduled_execution_failures"]) == 0 for row in overlay_audits)
        and all(int(row["account_entry_gate_violations"]) == 0 for row in account_audits)
        and all(math.isfinite(float(row["ann_return"])) for _, row in full.iterrows())
    )
    recent = long[long["segment"].isin(["last_3y", "last_1y"])].pivot(
        index="candidate", columns="segment", values="ann_return"
    )
    watch = [
        label for label, _, _, _ in GRID
        if float(full.loc[label, "ann_return"] - base.ann_return) >= 0.0025 - 1e-12
        and float(full.loc[label, "max_dd"] - base.max_dd) >= -0.0025 - 1e-12
        and not (
            float(recent.loc[label, "last_3y"]) < float(recent.loc["no_call_fix6", "last_3y"])
            and float(recent.loc[label, "last_1y"]) < float(recent.loc["no_call_fix6", "last_1y"])
        )
    ]
    decision = "rerun_required" if not all_pass else "watchlist" if watch else "keep_default"
    stability = "data_sensitive" if watch and all_pass else "reject" if all_pass else "data_sensitive"

    long.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    all_daily.to_csv(RUN / "daily_outputs.csv.gz", index=False, compression="gzip")
    all_events.to_csv(RUN / "events.csv.gz", index=False, compression="gzip")
    pd.concat(
        [target.assign(candidate=label) for label, target in target_by_candidate.items()],
        ignore_index=True,
    ).to_csv(RUN / "call_target_schedules.csv.gz", index=False, compression="gzip")
    pd.DataFrame(overlay_audits).to_csv(RUN / "call_overlay_audit.csv", index=False)
    pd.DataFrame(tenor_evidence).to_csv(RUN / "tenor_anchor_evidence.csv", index=False)
    pd.DataFrame(open_events).to_csv(RUN / "account_call_open_events.csv", index=False)
    compare.to_csv(RUN / "candidate_comparison.csv", index=False)
    signals.to_csv(RUN / "signal_inputs.csv.gz", index=False, compression="gzip")

    source_files = [
        SPEC, Path(__file__), ROOT / "research_im_call_mom_fear_entry_v1.py",
        prior.MOM_PATH, prior.FEAR_PATH, prior.TARGET_PATH,
        ROOT / "im_mo_call_daily_d10_threat_roll_v27.py",
        ROOT / "im_mo_call_daily_entry_profit_roll_v22.py",
        ROOT / "im_mo_call_overwrite_delta_tenor_v19.py",
        ROOT / "im_mo_call_valuation_threat_roll_v25.py",
        prior.NATIVE / "native_fix4_account_v1.py",
        PRIOR_RUN / "daily_outputs.csv.gz",
    ]
    hashes = {str(path.relative_to(ROOT)): prior.sha256(path) for path in source_files}
    audit = {
        "classification": "IM_CALL_DELTA_TENOR_RESEARCH_ONLY",
        "decision": decision, "stability_label": stability, "all_pass": all_pass,
        "baseline_parity": parity, "market_checks": market_checks,
        "overlay_audits": overlay_audits, "account_audits": account_audits,
        "watchlist_candidates": watch, "unavailable_segments": unavailable,
        "fear_point_in_time_verified": False, "production_source_changed": False,
    }
    (RUN / "run_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    display = long[long["segment"].isin(["full", "last_3y", "last_1y"])]
    table = ["|候选|窗口|CAGR|Sharpe|MaxDD|Call真实开仓周期|", "|---|---|---:|---:|---:|---:|"]
    for row in display.itertuples(index=False):
        table.append(
            f"|{row.candidate}|{row.segment}|{float(row.ann_return):.2%}|"
            f"{float(row.sharpe_repo):.3f}|{float(row.max_dd):.2%}|{int(row.account_open_episodes)}|"
        )
    second_anchor = pd.DataFrame(tenor_evidence)
    second_anchor = second_anchor[second_anchor["candidate"].eq("mom25_second_listed_d10")]
    record = f"""# IM 卖 Call Delta × 期限交叉扫描

## Run Metadata

- Run id: `{RUN.name}`
- Run date: {datetime.now().astimezone().isoformat(timespec='seconds')}
- Timezone: Asia/Shanghai
- Project: IC和IM滚动套利；v1.4-fix9 no-Call native account
- Subsystem: IM fixed-core covered Call
- Scan type: two_parameter_grid under two frozen entry regimes
- Git commit: `{git_value('rev-parse', 'HEAD')}`
- The worktree was dirty before this run; only new research files were added.

## Research Question

- Baseline: `no_call_fix6`; historical bridge: `old_iv26`.
- Candidate grid: two entry regimes × front/second actual listed expiry × target Delta 0.10/0.15/0.20.
- Decision target: `keep_default`, `watchlist`, or `rerun_required`.
- Source-change rule: `research_only_no_source_change`.
- Required windows: Full/3Y/1Y; 5Y/10Y explicit N/A because real MO history is shorter.
- Promotion threshold and rerun triggers: `preregistered_spec.md`.

## Implementation Anchor

- Account: native `native_fix4_account_v1.replay`, v7/mixed/v4/repeat_roll/3x; runtime target-table interception.
- Call selection: `v19.build_real_selections`, `v22.real_selection_for_expiry`, `v27.d10_real_selector`; lifecycle/safety from prior validated research.
- Delta is the T-close Black-Scholes Delta from the actual quoted option; selected nearest to target among valid OTM contracts.
- Two unchanged native baseline and two `front_d10` parity max absolute NAV errors: {json.dumps(parity)}.

## Data Snapshot

- Real IM/MO listed window: 2022-07-22—2026-08-14, 986 account days; signal end 2026-08-13.
- MOM120: native IM risk signals. Fear: 2026-09-28 downloaded snapshot, historical point-in-time availability unverified.
- Actual second-listed anchor signal DTE: min {int(second_anchor.chosen_dte.min())}, median {float(second_anchor.chosen_dte.median()):.0f}, max {int(second_anchor.chosen_dte.max())}; {int(second_anchor.chosen_dte.gt(60).sum())}/{len(second_anchor)} above 60.
- Trading calendar/timezone: dated IM/MO sessions, Asia/Shanghai; raw official futures/options prices, no equity adjustment.

## Cost and Execution Assumptions

- Futures one-way 1bp, bought Put 5bp, Call 1bp; 30% futures performance buffer per 1x, remaining cash 3% net annualized.
- T-close signal, T+1 historical dated-close option paper fill; no order-book bid/ask or capacity proof.

## Runtime Override Plan

- Only target Delta and listed expiry selection are varied. All module constants are restored after each candidate.
- Current no-Call baseline and old IV26 bridge are rerun in the same account, with frozen-output parity checks.
- `front_d10` under both entry regimes also reproduces the prior v2 scan before new-parameter comparisons.

## Commands

```powershell
python -X utf8 research_im_call_delta_tenor_v1.py
```

## Output Files

- Full metrics: `scan_summary.csv`, `window_metrics.csv`.
- Reproducibility: `scan_meta.json`, `command_log.txt`, `run_audit.json`.
- Detailed evidence: `candidate_comparison.csv`, `tenor_anchor_evidence.csv`, `account_call_open_events.csv`, `daily_outputs.csv.gz`, `events.csv.gz`, `call_target_schedules.csv.gz`.

## Full-Sample Results

{chr(10).join(table)}

## Window Results

- All Full/3Y/1Y results are in `scan_summary.csv`; 5Y/10Y are N/A with reasons in `scan_meta.json`.

## Stability Classification

- Label: `{stability}`.
- Same historical window has already been inspected for entry-regime ideas; Fear is not point-in-time verified.
- Delta and tenor neighborhood plus account-open DTE are in `candidate_comparison.csv`.

## Decision

- Decision: `{decision}`; mechanical watchlist candidates: {', '.join(watch) if watch else 'none'}.
- Account and entry-gate audit pass: {all_pass}. Production action: none.

## User-Facing Summary

This is a same-window real-option native-account counterfactual. It measures Delta and actual listed-tenor effects under two fixed entry regimes; it is not a trade fill record or live approval.
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {"scan_type": "two_parameter_grid",
         "baseline": {"candidate": "no_call_fix6", "definition": "native repeat-roll 3x account with Call disabled"},
         "candidate_grid": list(CANDIDATES),
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
                     "command_log": str(RUN / "command_log.txt"), "daily_outputs": str(RUN / "daily_outputs.csv.gz"),
                     "run_audit": str(RUN / "run_audit.json")},
         "decision": decision, "stability_label": stability,
         "elapsed_sec": time.perf_counter() - started,
         "warnings": ["Fear historical series is a later-downloaded snapshot without point-in-time archive.",
                      "Second listed expiry is often above 60 calendar days at monthly anchors.",
                      "Real listed-option history is shorter than five full years.",
                      "Worktree was already dirty before this run."]}
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python -X utf8 research_im_call_delta_tenor_v1.py\n")
        handle.write(f"cwd={ROOT}\n")
        handle.write(f"elapsed_sec={time.perf_counter() - started:.3f}\n")
    print(json.dumps({"decision": decision, "stability": stability, "parity": parity,
                      "watchlist": watch, "all_pass": all_pass}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
