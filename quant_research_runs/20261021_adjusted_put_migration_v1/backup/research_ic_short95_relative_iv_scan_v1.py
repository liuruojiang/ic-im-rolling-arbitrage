"""Causal relative-IV entry scan for the corrected IC M+1 short-95% router."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_coreput_highiv_short95_router_v1 as base
import research_ic_short95_maturity_scan_v1 as maturity


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_ic_v1_3_m1_short95_relative_iv_window_percentile"
SPEC = ROOT / "docs" / "ic_short95_relative_iv_scan_v1_spec.md"
PRIOR_RUN = maturity.RUN
WINDOWS = (126, 252, 504)
QUANTILES = (0.70, 0.80, 0.90)
MIN_HISTORY = 60
DECAY = 0.60
OPTION_ONE_WAY = 0.0005


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def relative_signal(base_signal: pd.DataFrame, window: int, quantile: float) -> pd.DataFrame:
    out = base_signal.sort_values("eval_date").reset_index(drop=True).copy()
    raw = out.iv.to_numpy(dtype=float)
    valid = np.isfinite(raw)
    ranks = np.full(len(out), np.nan)
    counts = np.zeros(len(out), dtype=int)
    for i, current in enumerate(raw):
        start = max(0, i - window)
        hist = raw[start:i]
        mask = valid[start:i] & np.isfinite(hist)
        counts[i] = int(mask.sum())
        if valid[i] and counts[i] >= MIN_HISTORY:
            ranks[i] = float(np.mean(hist[mask] <= current))
    out["raw_iv"] = raw
    out["relative_iv_percentile"] = ranks
    out["relative_iv_history_count"] = counts
    out["relative_iv_window"] = window
    out["relative_iv_quantile"] = quantile
    out["route"] = (out.admission.astype(bool) & out.execution_open_valid.astype(bool)
                    & np.isfinite(ranks) & (ranks > quantile))
    return out


def absolute_signal(signal: pd.DataFrame) -> pd.DataFrame:
    out = signal.copy()
    out["raw_iv"] = out.iv
    out["relative_iv_percentile"] = np.nan
    out["relative_iv_history_count"] = 0
    out["relative_iv_window"] = 0
    out["relative_iv_quantile"] = np.nan
    return out


def run_layer(scope, schedule, model_engine, real_engine, real_short, model_short):
    frames, _, market, _ = base.ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll = base.ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    real_active, _, real_chains, _, _, real_futures = base.short.real_inputs()
    model_active, model_market, model_futures = base.short.model_source.model_inputs()
    active, futures, marketx = (real_active, real_futures, None) if scope == "real" else (model_active, model_futures, model_market)

    pure = base.baseline(active, scope)
    always = base.mask_schedule(schedule, scope, pure.date)
    core, core_trades = (real_engine(frames["ic"], always, frames, market, f"{scope}_core", roll)
                         if scope == "real" else model_engine(frames["ic"], always, market, f"{scope}_core", roll))
    core = core[core.date.isin(pure.date)].reset_index(drop=True)
    protected = base.add_core(pure, maturity.costed_core(core, 5.0), f"{scope}_rolling_ic_current_core_put_5bp")

    base_signal = (maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
                   if scope == "real" else maturity.maturity_model_signals(active, marketx, "m1"))
    definitions = [("abs375", absolute_signal(base_signal))]
    for window in WINDOWS:
        for quantile in QUANTILES:
            definitions.append((f"relw{window}q{int(quantile * 100)}", relative_signal(base_signal, window, quantile)))

    runner = real_short if scope == "real" else model_short
    parts = [pure, protected]; signals = []; cycles_all = []; events_all = []
    audits = {"pure_parity": float(abs(pure.return_net - (active.ic_net_ret + .7 * base.CASH)).max()),
              "core_put_trade_events": len(core_trades), "core_put_cost_multiplier": 5.0}
    reference = pd.read_csv(PRIOR_RUN / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])

    for tag, signal in definitions:
        isolated, events, cycles, short_audit = runner(base.entry_series(signal), DECAY, "m1", OPTION_ONE_WAY)
        routed = base.stitched_router(scope, active, futures, isolated, signal)
        ic_dates = routed.loc[routed.state.eq("ic"), "date"]
        masked = base.mask_schedule(schedule, scope, ic_dates)
        exits = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
        p, ptrades = (real_engine(frames["ic"], masked, frames, market, f"{scope}_{tag}", roll, exits)
                      if scope == "real" else model_engine(frames["ic"], masked, market, f"{scope}_{tag}", roll, exits))
        p = p[p.date.isin(routed.date)].reset_index(drop=True)
        label = f"{scope}_m1_short95_decay60_{tag}_5bp"
        combined = base.add_core(routed, maturity.costed_core(p, 5.0), label)
        parts.append(combined); signals.append(signal.assign(scope=scope, candidate=label))
        if len(cycles): cycles_all.append(cycles.assign(scope=scope, candidate=label))
        if len(events): events_all.append(events.assign(scope=scope, candidate=label))
        simultaneous = set(ptrades.loc[ptrades.action.eq("route_open_exit"), "actual_execution_date"])
        if simultaneous - exits:
            raise RuntimeError(f"Core Put route exit mismatch {label}")
        parity = None
        if tag == "abs375":
            ref_label = f"{scope}_m1_iv375_coreput_to_short95_decay60_5bp"
            ref = reference[reference.candidate.eq(ref_label)].sort_values("date").reset_index(drop=True)
            got = combined.sort_values("date").reset_index(drop=True)
            if not ref.date.equals(got.date):
                raise RuntimeError(f"Absolute baseline dates mismatch {scope}")
            parity = float(np.max(np.abs(ref.return_net.to_numpy() - got.return_net.to_numpy())))
            if parity > 1e-12:
                raise RuntimeError(f"Absolute baseline parity failed {scope}: {parity}")
        closed_cycles = int(cycles.closed.fillna(False).sum()) if len(cycles) and "closed" in cycles else 0
        audits[label] = {**short_audit, "route_switches": len(exits), "closed_cycles": closed_cycles,
                         "relative_valid_days": int(np.isfinite(signal.relative_iv_percentile).sum()),
                         "threshold_days": int(signal.route.sum()),
                         "core_put_simultaneous_exits": len(simultaneous),
                         "routes_without_active_core_put": len(exits - simultaneous),
                         "absolute_m1_daily_parity_error": parity}
    return (pd.concat(parts, ignore_index=True), pd.concat(signals, ignore_index=True),
            pd.concat(cycles_all, ignore_index=True) if cycles_all else pd.DataFrame(),
            pd.concat(events_all, ignore_index=True) if events_all else pd.DataFrame(), audits)


def classify(full: pd.DataFrame, audits: dict) -> tuple[str, str, dict]:
    checks = {}
    passed = set()
    for window in WINDOWS:
        for quantile in QUANTILES:
            tag = f"relw{window}q{int(quantile * 100)}"
            layers = []
            for scope in ("real", "model"):
                baseline = full[full.candidate.eq(f"{scope}_m1_short95_decay60_abs375_5bp")].iloc[0]
                candidate = full[full.candidate.eq(f"{scope}_m1_short95_decay60_{tag}_5bp")].iloc[0]
                audit = audits[scope][f"{scope}_m1_short95_decay60_{tag}_5bp"]
                cagr_diff_pp = 100.0 * float(candidate.ann_return - baseline.ann_return)
                mdd_worse_pp = 100.0 * max(0.0, abs(float(candidate.max_dd)) - abs(float(baseline.max_dd)))
                minimum_cycles = 3 if scope == "real" else 8
                layers.append({"scope": scope, "cagr_diff_pp": cagr_diff_pp,
                               "sharpe_diff": float(candidate.sharpe_repo - baseline.sharpe_repo),
                               "mdd_worse_pp": mdd_worse_pp, "closed_cycles": int(audit["closed_cycles"]),
                               "sharpe_gate": float(candidate.sharpe_repo) >= float(baseline.sharpe_repo),
                               "drawdown_gate": mdd_worse_pp <= 0.50,
                               "cagr_gate": cagr_diff_pp >= -0.50,
                               "cycle_gate": int(audit["closed_cycles"]) >= minimum_cycles})
            individual = all(all(x[g] for g in ("sharpe_gate", "drawdown_gate", "cagr_gate", "cycle_gate")) for x in layers)
            checks[tag] = {"layers": layers, "individual_gate_pass": individual}
            if individual: passed.add((window, quantile))
    adjacent_pairs = []
    for window in WINDOWS:
        for q1, q2 in zip(QUANTILES, QUANTILES[1:]):
            if (window, q1) in passed and (window, q2) in passed:
                adjacent_pairs.append({"axis": "quantile", "window": window, "values": [q1, q2]})
    for quantile in QUANTILES:
        for w1, w2 in zip(WINDOWS, WINDOWS[1:]):
            if (w1, quantile) in passed and (w2, quantile) in passed:
                adjacent_pairs.append({"axis": "window", "quantile": quantile, "values": [w1, w2]})
    checks["passed_candidates"] = [f"relw{w}q{int(q * 100)}" for w, q in sorted(passed)]
    checks["adjacent_pairs"] = adjacent_pairs
    if adjacent_pairs:
        return ("retain_relative_iv_plateau_for_research_no_production_change",
                "relative_iv_cross_layer_adjacent_plateau_passed", checks)
    if passed:
        return ("do_not_replace_abs375_relative_iv_isolated_peak_watchlist",
                "relative_iv_individual_pass_without_adjacent_plateau", checks)
    return ("retain_abs375_reject_relative_iv_grid_no_production_change",
            "relative_iv_no_cross_layer_candidate_passed", checks)


def main():
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    schedule = base.current_schedule(); model_engine, real_engine, core_source = base.patched_engines()
    real_short, model_short, short_source = maturity.patched_short_runners()
    results = {scope: run_layer(scope, schedule, model_engine, real_engine, real_short, model_short)
               for scope in ("real", "model")}
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    summary, wide, unavailable = base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()
    audit = {"real": results["real"][4], "model": results["model"][4]}
    decision, stability, decision_checks = classify(full, audit)
    cycle_diag = (cycles.groupby(["scope", "candidate"], as_index=False)
                  .agg(cycles=("entry_date", "count"), closed_cycles=("closed", "sum"),
                       assignments=("physical_assignment_date", lambda x: x.notna().sum()),
                       early_rolls=("early_rolls", "sum"), worst_cycle_pnl=("realized_pnl", "min")))
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signal_audit.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False); events.to_csv(out / "events.csv", index=False)
    cycle_diag.to_csv(out / "cycle_diagnostics.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + core_source, encoding="utf-8")
    meta.update({
        "scan_type": "ic_m1_short95_causal_relative_iv_entry_scan_v1",
        "baseline": {"candidate": "*_m1_short95_decay60_abs375_5bp", "prior_run": str(PRIOR_RUN),
                     "daily_parity_max_abs_error": {scope: audit[scope][f"{scope}_m1_short95_decay60_abs375_5bp"]["absolute_m1_daily_parity_error"] for scope in ("real", "model")}},
        "candidate_grid": [{"type": "absolute", "threshold": 0.375}]
                          + [{"type": "relative_percentile", "window": w, "quantile": q, "min_history": MIN_HISTORY} for w in WINDOWS for q in QUANTILES],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 actual 510500 Put/ETF and IC",
                          "model": "2015-04-16..2026-08-14 theoretical 510500 Put plus historical IC"},
        "cost_model": {"510500_put_one_way": OPTION_ONE_WAY, "put_round_trip": 2 * OPTION_ONE_WAY,
                       "ETF_and_IC_one_way": base.ONE_WAY, "risk_buffer": 0.30, "cash_annual": 0.03},
        "audit": audit, "decision_checks": decision_checks, "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signal_audit.csv.gz"),
                    "cycles": str(out / "cycles.csv"), "events": str(out / "events.csv"),
                    "cycle_diagnostics": str(out / "cycle_diagnostics.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "maturity_run_meta": sha(PRIOR_RUN / "scan_meta.json")},
        "warnings": ["Relative-IV warmup has no entries until 60 prior valid observations.",
                     "Real listed option history is short.", "Model 510500 Put is theoretical and not executable history.",
                     "No bid-ask, dynamic margin, forced liquidation, tax, capacity or integer sizing."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = ("# IC M+1卖95% Put相对IV扫描\n\n## Run Metadata\n\n研究专用；IC无Call；生产未修改。\n\n"
              "## Research Question\n\n因果滚动IV分位能否替代绝对IV>37.5%。\n\n"
              "## Implementation Anchor\n\n固定M+1、95% Put、60%衰减、现行IC准入、核心Put同步退出、实物交割ETF转IC及回本退出。\n\n"
              "## Data Snapshot\n\n真实2022-09-19至2026-08-14；理论2015-04-16至2026-08-14。\n\n"
              "## Cost and Execution Assumptions\n\n510500 Put单边5BP；ETF与IC单边1BP；T收盘信号、T+1开盘执行；30%缓冲、3%现金。\n\n"
              "## Commands\n\n见command_log.txt。\n\n## Output Files\n\n见scan_meta.json。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) +
              "\n\n## Cycle Diagnostics\n\n" + cycle_diag.to_markdown(index=False) +
              "\n\n## Decision Gates\n\n```json\n" + json.dumps(decision_checks, ensure_ascii=False, indent=2) +
              "\n```\n\n## Stability Classification\n\n" + stability + "\n\n## Decision\n\n" + decision +
              "\n\n## User-Facing Summary\n\n本层完成后暂停，由用户确认是否进入估值防抖层。\n")
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(cycle_diag.to_string(index=False)); print(json.dumps(decision_checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
