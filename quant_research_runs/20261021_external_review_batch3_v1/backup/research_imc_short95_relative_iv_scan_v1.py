"""Causal relative-IV entry scan for the corrected M+1 short-95% IM router."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import research_imc_short95_maturity_corrected_v1 as maturity
import research_imc_short95_maturity_corrected_v2 as maturity_v2
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as parity
import research_imc_current_core_put_short95_earlyvaluation_v4 as v4

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "docs" / "im_short95_relative_iv_scan_v1_spec.md"
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_corrected_mixed_router_m_1_short95_relative_iv_entry_relative_iv_window_and_percentile"
PRIOR_RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_corrected_im_core_put_high_iv_short95_router_im_short_put_maturity_with_call_suspension_v2_front10_m_1_m_2_m_3_model_all_tenors"
WINDOWS = (126, 252, 504)
QUANTILES = (0.70, 0.80, 0.90)
MIN_HISTORY = 60
DECAY = 0.60


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def relative_signal(base_signal: pd.DataFrame, window: int) -> pd.DataFrame:
    out = base_signal.sort_values("eval_date").reset_index(drop=True).copy()
    raw = out.iv.to_numpy(dtype=float)
    valid = out.iv_valid.to_numpy(dtype=bool) & np.isfinite(raw)
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
    out["iv"] = ranks
    out["iv_valid"] = np.isfinite(ranks)
    return out


def load_layer(scope: str):
    if scope == "real":
        return maturity.load_layer(scope)
    market, base, options, futures = maturity_v2.extended_model_inputs()
    return market, base, options, None, futures


def run_layer(scope: str, router_fn, real_put_engine, model_put_engine, call_dir: Path):
    market, base, options, options_for_put, futures = load_layer(scope)
    bare = pd.DataFrame({"date": base.date, "candidate": f"{scope}_bare_monthly_imc", "return_net": base.baseline_plus_cash_ret.astype(float)})
    bare["nav"] = (1 + bare.return_net).cumprod(); bare["state"] = "imc"; bare["action"] = ""
    always = pd.Series(True, index=pd.DatetimeIndex(base.date))
    schedule = v4.corrected_core_schedule(base.date, scope, always)
    if scope == "real":
        put, base_put_trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=maturity.common.engine.monthly_dates(base.date), market=None)
    else:
        put, base_put_trades, _ = model_put_engine(market, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=maturity.common.engine.monthly_dates(base.date))
    protected = maturity.common.apply_core_put(bare, maturity.common.scale_put(put, scope))
    call_base, call_base_trades, _ = maturity.call_inputs(scope, base.date, pd.Series(1.0, index=base.index), f"{scope[0]}rb", call_dir)
    protected = maturity.add_call(protected, call_base); protected["candidate"] = f"{scope}_imc_coreput_call_baseline"

    base_signal = maturity.prepare_signal(base, options, "m1")
    definitions: list[tuple[str, pd.DataFrame, float]] = [("abs35", base_signal.assign(raw_iv=base_signal.iv, relative_iv_percentile=np.nan, relative_iv_history_count=0, relative_iv_window=0), 0.35)]
    for window in WINDOWS:
        rel = relative_signal(base_signal, window)
        for q in QUANTILES:
            definitions.append((f"relw{window}q{int(q*100)}", rel.copy(), q))

    candidates = [protected]; signals = []; cycles_all = []; audits: dict[str, Any] = {}
    abs_daily = None
    for tag, signal, threshold in definitions:
        routed, events, cycles = router_fn(base, options, futures, signal, threshold, maturity.common.FALLBACK, DECAY)
        routed["date"] = pd.to_datetime(routed.date)
        imc_mask = routed.set_index("date").state.eq("imc")
        route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
        schedule = v4.corrected_core_schedule(base.date, scope, imc_mask)
        label = f"{scope}_m1_short95_decay60_{tag}_callpaused"
        if scope == "real":
            put, put_trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, label, reset_dates=maturity.common.engine.monthly_dates(base.date), market=None, open_exit_dates=route_dates)
        else:
            put, put_trades, _ = model_put_engine(market, schedule, "3m", 1.02, label, reset_dates=maturity.common.engine.monthly_dates(base.date), open_exit_dates=route_dates)
        combined = maturity.common.apply_core_put(routed, maturity.common.scale_put(put, scope))
        call_scale = routed.state.eq("imc").astype(float)
        call_daily, call_trades, _ = maturity.call_inputs(scope, base.date, call_scale, f"{scope[0]}r{tag}", call_dir)
        combined = maturity.add_call(combined, call_daily); combined["candidate"] = label
        candidates.append(combined)
        signals.append(signal.assign(layer=scope, candidate=label, entry_threshold=threshold))
        if len(cycles): cycles_all.append(cycles.assign(layer=scope, candidate=label))
        simultaneous = set(put_trades.loc[put_trades.action.eq("route_open_exit"), "actual_execution_date"])
        if not simultaneous.issubset(route_dates): raise RuntimeError(f"{label}: core Put exit mismatch")
        if not call_daily.loc[routed.route.eq("high_iv_permitted_short_put"), "call_coverage"].eq(0).all(): raise RuntimeError(f"{label}: Call not paused")
        audits[label] = {
            "route_switches": len(route_dates), "short_put_cycles": len(cycles),
            "assignments": int(cycles.assignment_date.fillna("").astype(str).ne("").sum()) if len(cycles) and "assignment_date" in cycles else 0,
            "early_rolls": int(routed.action.eq("put_early_roll60_buyback_and_sell_next_open").sum()),
            "relative_valid_days": int(signal.iv_valid.sum()), "threshold_days": int((signal.iv_valid & signal.iv.gt(threshold)).sum()),
            "eligible_threshold_days": int((signal.iv_valid & signal.iv.gt(threshold) & signal.short_put_permission & signal.execution_open_valid).sum()),
            "core_put_simultaneous_exits": len(simultaneous), "call_paused_days": int(call_scale.eq(0).sum()), "call_trade_events": len(call_trades),
        }
        if tag == "abs35": abs_daily = combined[["date", "return_net"]].copy()
    audits[f"{scope}_baseline"] = {"core_put_trade_events": len(base_put_trades), "call_trade_events": len(call_base_trades)}
    if abs_daily is None: raise RuntimeError("Missing absolute baseline")
    return pd.concat(candidates, ignore_index=True), pd.concat(signals, ignore_index=True), (pd.concat(cycles_all, ignore_index=True) if cycles_all else pd.DataFrame()), audits, abs_daily


def prior_parity(scope: str, current: pd.DataFrame) -> float:
    prior = pd.read_csv(PRIOR_RUN / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    label = f"{scope}_m1_iv35_coreput_to_short95_decay60_callpaused"
    prior = prior[prior.candidate.eq(label)][["date", "return_net"]].sort_values("date").reset_index(drop=True)
    got = current.sort_values("date").reset_index(drop=True)
    if not prior.date.equals(got.date): raise RuntimeError(f"{scope}: prior parity dates mismatch")
    error = float(np.max(np.abs(prior.return_net.to_numpy() - got.return_net.to_numpy())))
    if error > 1e-12: raise RuntimeError(f"{scope}: absolute baseline parity failed {error}")
    return error


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    router_fn, router_source = parity.parity_fixed_runner()
    real_put_engine, model_put_engine, put_source = maturity.common.patched_put_engines()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    real_daily, real_signal, real_cycles, real_audit, real_abs = run_layer("real", router_fn, real_put_engine, model_put_engine, call_dir)
    model_daily, model_signal, model_cycles, model_audit, model_abs = run_layer("model", router_fn, real_put_engine, model_put_engine, call_dir)
    parity_errors = {"real": prior_parity("real", real_abs), "model": prior_parity("model", model_abs)}
    daily = pd.concat([real_daily, model_daily], ignore_index=True)
    summary, wide, unavailable = maturity.common.window_tables(daily)
    cycles = pd.concat([real_cycles, model_cycles], ignore_index=True)
    cycle_diag = cycles.groupby(["layer", "candidate"], as_index=False).agg(cycles=("entry_date", "count"), worst_cycle_pnl=("exit_cycle_pnl", "min"), mean_cycle_pnl=("exit_cycle_pnl", "mean"))
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([real_signal, model_signal], ignore_index=True).to_csv(out / "signal_audit.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False)
    cycle_diag.to_csv(out / "cycle_diagnostics.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + put_source, encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    meta.update(
        scan_type="im_m1_short95_causal_relative_iv_entry_scan_v1",
        baseline={"candidate": "*_m1_short95_decay60_abs35_callpaused", "prior_run": str(PRIOR_RUN), "daily_parity_max_abs_error": parity_errors},
        candidate_grid=[{"type": "absolute", "threshold": 0.35}] + [{"type": "relative_percentile", "window": w, "quantile": q, "min_history": MIN_HISTORY} for w in WINDOWS for q in QUANTILES],
        data_snapshot={"real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()), "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date()), "options_sha256": sha(maturity.common.router.OPTIONS), "futures_sha256": sha(maturity.common.router.FUTURES)},
        cost_model={"one_way_notional": maturity.common.router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03, "call": "original D10/IV26 paused outside IMC", "core_put": "current 102% valuation/MOM120 debounce, synchronous route exit"},
        audit={"real": real_audit, "model": model_audit}, unavailable_segments=unavailable,
        outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signal_audit.csv.gz"), "cycles": str(out / "cycles.csv"), "cycle_diagnostics": str(out / "cycle_diagnostics.csv"), "executed_state_machines": str(RUN / "executed_state_machines.py")},
        source_hashes={"script": sha(Path(__file__)), "spec": sha(SPEC), "parity_engine": sha(Path(parity.__file__)), "early_valuation": sha(v4.EARLY)},
        warnings=["Relative-IV warmup has no entries until 60 prior valid observations.", "Historical 102% Put and MOM120 debounce are counterfactual replay.", "Model layer uses theoretical options and calibrated carry, not executable history.", "No bid-ask, impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing."],
        decision="research_only_pending_interpretation", stability_label="relative_iv_scan_pending_review", git_status_after=git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IM M+1卖95% Put相对IV扫描\n\n## Run Metadata\n\n- 真实IM/MO与理论延展分开；不改生产。\n\n## Research Question\n\n- 因果滚动IV分位能否替代绝对IV>35%。\n\n## Implementation Anchor\n\n- 固定M+1、95% Put、60%衰减、当前估值/MOM120防抖、核心Put同步退出和Call暂停。\n\n## Data Snapshot\n\n- 真实与理论区间见 `scan_meta.json`。\n\n## Cost and Execution Assumptions\n\n- T收盘信号、T+1开盘执行；单边1bp、30%缓冲、3%现金。\n\n## Runtime Override Plan\n\n- 独立研究脚本；未改冻结主线。\n\n## Commands\n\n```powershell\npython -X utf8 research_imc_short95_relative_iv_scan_v1.py\n```\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`及`daily_outputs/`。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) + "\n\n## Cycle Diagnostics\n\n" + cycle_diag.to_markdown(index=False) + "\n\n## Stability Classification\n\n- 待按预注册门槛解释。\n\n## Decision\n\n- research_only_pending_interpretation\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle: handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(cycle_diag.to_string(index=False)); print(json.dumps({"parity": parity_errors, "real": real_audit, "model": model_audit}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
