"""Rebuild IC short-Put research, layer 5: causal relative-IV entry gates."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer5_iv30_no_mom_m1_short95_hold_naked_router_"
    "causal_relative_iv_replace_and_overlay_w126_w252_w504_q70_q80_q90"
)
LAYER4 = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer4_iv30_no_mom_m1_short95_put_router_"
    "same_expiry_catastrophe_put_none_90_85_80"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer5_relative_iv_v1_spec.md"
WINDOWS = (126, 252, 504)
QUANTILES = (0.70, 0.80, 0.90)
MODES = ("replace", "overlay")
MIN_HISTORY = 60
IV_THRESHOLD = 0.30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def causal_relative(signal: pd.DataFrame, window: int, quantile: float) -> pd.DataFrame:
    out = signal.sort_values("eval_date").reset_index(drop=True).copy()
    raw = out.iv.to_numpy(dtype=float)
    ranks = np.full(len(out), np.nan)
    counts = np.zeros(len(out), dtype=int)
    for idx, current in enumerate(raw):
        start = max(0, idx - window)
        history = raw[start:idx]
        valid = np.isfinite(history)
        counts[idx] = int(valid.sum())
        if np.isfinite(current) and counts[idx] >= MIN_HISTORY:
            ranks[idx] = float(np.mean(history[valid] <= current))
    out["raw_iv"] = raw
    out["relative_iv_percentile"] = ranks
    out["relative_iv_history_count"] = counts
    out["relative_iv_window"] = window
    out["relative_iv_quantile"] = quantile
    out["relative_pass"] = np.isfinite(ranks) & (ranks > quantile)
    return out


def variant_prepared(
    prepared: dict[str, object], mode: str, window: int | None, quantile: float | None
) -> tuple[dict[str, object], pd.DataFrame]:
    source = prepared["base_signal"].copy()
    original_admission = source.admission.astype(bool).copy()
    original_execution_open_valid = source.execution_open_valid.astype(bool).copy()
    if mode == "abs30":
        diagnostic = source.copy()
        diagnostic["raw_iv"] = diagnostic.iv.astype(float)
        diagnostic["relative_iv_percentile"] = np.nan
        diagnostic["relative_iv_history_count"] = 0
        diagnostic["relative_iv_window"] = 0
        diagnostic["relative_iv_quantile"] = np.nan
        diagnostic["relative_pass"] = False
        modified = source
    else:
        assert window is not None and quantile is not None
        diagnostic = causal_relative(source, window, quantile)
        modified = diagnostic.copy()
        modified["execution_open_valid"] = (
            original_execution_open_valid & diagnostic.relative_pass.astype(bool)
        )
        if mode == "replace":
            modified["iv"] = np.where(np.isfinite(modified.raw_iv), 1.0, np.nan)
        elif mode != "overlay":
            raise ValueError(mode)
    copied = dict(prepared)
    copied["base_signal"] = modified
    diagnostic["original_admission"] = original_admission
    diagnostic["original_execution_open_valid"] = original_execution_open_valid
    return copied, diagnostic


def tag(mode: str, window: int | None = None, quantile: float | None = None) -> str:
    if mode == "abs30":
        return "abs30"
    return f"{mode}_w{window}_q{int(round(float(quantile) * 100))}"


def relabel(result, diagnostic: pd.DataFrame, mode: str, window: int | None, quantile: float | None):
    daily, trades, signals, cycles, audit = result
    scope = str(audit["scope"])
    name = tag(mode, window, quantile)
    candidate = f"{scope}_m1_iv300_nomom_{name}"
    for frame in (daily, trades, cycles):
        if len(frame):
            frame["candidate"] = candidate
            frame["variant"] = f"m1_iv300_nomom_{name}"
            frame["relative_iv_mode"] = mode
            frame["relative_iv_window"] = 0 if window is None else window
            frame["relative_iv_quantile"] = np.nan if quantile is None else quantile
    signals = signals.sort_values("eval_date").reset_index(drop=True)
    diagnostic = diagnostic.sort_values("eval_date").reset_index(drop=True)
    if not signals.eval_date.equals(diagnostic.eval_date):
        raise RuntimeError(f"{candidate} signal dates differ")
    signals["iv"] = diagnostic.raw_iv.to_numpy(dtype=float)
    signals["raw_iv"] = diagnostic.raw_iv.to_numpy(dtype=float)
    signals["relative_iv_percentile"] = diagnostic.relative_iv_percentile.to_numpy(dtype=float)
    signals["relative_iv_history_count"] = diagnostic.relative_iv_history_count.to_numpy(dtype=int)
    signals["relative_iv_window"] = 0 if window is None else window
    signals["relative_iv_quantile"] = np.nan if quantile is None else quantile
    signals["relative_iv_mode"] = mode
    signals["original_admission"] = diagnostic.original_admission.to_numpy(dtype=bool)
    signals["execution_open_valid"] = diagnostic.original_execution_open_valid.to_numpy(dtype=bool)
    signals["candidate"] = candidate
    signals["variant"] = f"m1_iv300_nomom_{name}"
    audit.update({
        "candidate": candidate,
        "variant": f"m1_iv300_nomom_{name}",
        "relative_iv_mode": mode,
        "relative_iv_window": 0 if window is None else window,
        "relative_iv_quantile": np.nan if quantile is None else quantile,
        "relative_valid_days": int(np.isfinite(diagnostic.relative_iv_percentile).sum()),
        "relative_pass_days": int(diagnostic.relative_pass.astype(bool).sum()),
    })
    return daily, trades, signals, cycles, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-5 run")

    prior = base.prior
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, model_market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, _, _, expiries, _ = prior.router_base.short.real_inputs()
    active_real = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    corrected_real = layer3.corrected_real_signal_for_maturity(
        active_real, frames, option_market, expiries, "m1"
    )
    prepared = {
        scope: base.prepare_scope(
            scope, path, futures, weights, selected, grid, frames, option_market,
            model_market, corrected_real,
        )
        for scope in ("real", "model")
    }
    prepared["model"]["base_signal"] = prior.maturity.maturity_model_signals(
        prepared["model"]["active"], model_market, "m1"
    )

    definitions: list[tuple[str, int | None, float | None]] = [("abs30", None, None)]
    definitions.extend(
        (mode, window, quantile)
        for mode in MODES for window in WINDOWS for quantile in QUANTILES
    )
    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    old_decay = base.DECAY
    try:
        base.DECAY = None
        for mode, window, quantile in definitions:
            for scope in ("real", "model"):
                candidate_prepared, diagnostic = variant_prepared(
                    prepared[scope], mode, window, quantile
                )
                result = base.run_candidate(
                    candidate_prepared, IV_THRESHOLD, False, futures, weights,
                    option_market, real_short, model_short, model_profit, real_profit,
                )
                d, t, s, c, a = relabel(result, diagnostic, mode, window, quantile)
                daily_parts.append(d)
                trade_parts.append(t)
                signal_parts.append(s)
                if len(c):
                    cycle_parts.append(c)
                audits.append(a)
    finally:
        base.DECAY = old_decay

    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    signals = pd.concat(signal_parts, ignore_index=True, sort=False)
    cycles = pd.concat(cycle_parts, ignore_index=True, sort=False)
    exposure = pd.DataFrame(audits)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    params = exposure.set_index("candidate")[[
        "relative_iv_mode", "relative_iv_window", "relative_iv_quantile",
        "iv_threshold", "mom120_gate",
    ]]
    for table in (summary, wide):
        for column in params.columns:
            table[column] = table.candidate.map(params[column])

    cycles["cycle_pnl"] = cycles.realized_pnl.fillna(0.0) + cycles.open_cycle_pnl.fillna(0.0)
    concentration_rows: list[dict[str, object]] = []
    for candidate, frame in cycles.groupby("candidate", sort=False):
        absolute = frame.cycle_pnl.abs()
        total = float(absolute.sum())
        concentration_rows.append({
            "candidate": candidate,
            "scope": frame.scope.iloc[0],
            "relative_iv_mode": frame.relative_iv_mode.iloc[0],
            "relative_iv_window": frame.relative_iv_window.iloc[0],
            "relative_iv_quantile": frame.relative_iv_quantile.iloc[0],
            "cycles": len(frame),
            "closed_cycles": int(frame.closed.fillna(False).sum()),
            "positive_cycles": int(frame.cycle_pnl.gt(0).sum()),
            "negative_cycles": int(frame.cycle_pnl.lt(0).sum()),
            "worst_cycle_pnl": float(frame.cycle_pnl.min()),
            "cycle_pnl_total": float(frame.cycle_pnl.sum()),
            "largest_abs_cycle_share": float(absolute.max() / total) if total else np.nan,
        })
    concentration = pd.DataFrame(concentration_rows)

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    gate_details: dict[str, object] = {}
    passed: set[tuple[str, int, float]] = set()
    for mode, window, quantile in definitions:
        name = tag(mode, window, quantile)
        for scope in ("real", "model"):
            baseline = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_abs30")].iloc[0]
            row = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_{name}")].iloc[0]
            exp = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            conc = concentration[concentration.candidate.eq(row.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "relative_iv_mode": mode,
                "relative_iv_window": 0 if window is None else window,
                "relative_iv_quantile": np.nan if quantile is None else quantile,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_abs30": row.ann_return - baseline.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_abs30": row.sharpe_repo - baseline.sharpe_repo,
                "max_dd": row.max_dd,
                "max_dd_delta_vs_abs30": row.max_dd - baseline.max_dd,
                "cycles": int(exp.cycles),
                "closed_cycles": int(conc.closed_cycles),
                "short_put_days": int(exp.short_put_days),
                "short_put_share": exp.short_put_share,
                "relative_valid_days": int(exp.relative_valid_days),
                "relative_pass_days": int(exp.relative_pass_days),
            })
    paired = pd.DataFrame(paired_rows)

    for mode in MODES:
        for window in WINDOWS:
            for quantile in QUANTILES:
                layers = []
                for scope in ("real", "model"):
                    baseline = paired[(paired.scope.eq(scope)) & (paired.relative_iv_mode.eq("abs30"))].iloc[0]
                    candidate = paired[
                        paired.scope.eq(scope)
                        & paired.relative_iv_mode.eq(mode)
                        & paired.relative_iv_window.eq(window)
                        & paired.relative_iv_quantile.eq(quantile)
                    ].iloc[0]
                    cagr_diff_pp = 100.0 * (candidate.ann_return - baseline.ann_return)
                    mdd_worse_pp = 100.0 * max(0.0, abs(candidate.max_dd) - abs(baseline.max_dd))
                    minimum_cycles = 3 if scope == "real" else 8
                    layers.append({
                        "scope": scope,
                        "cagr_diff_pp": float(cagr_diff_pp),
                        "sharpe_diff": float(candidate.sharpe - baseline.sharpe),
                        "mdd_worse_pp": float(mdd_worse_pp),
                        "closed_cycles": int(candidate.closed_cycles),
                        "sharpe_gate": bool(candidate.sharpe >= baseline.sharpe),
                        "drawdown_gate": bool(mdd_worse_pp <= 0.50),
                        "cagr_gate": bool(cagr_diff_pp >= -0.50),
                        "cycle_gate": bool(candidate.closed_cycles >= minimum_cycles),
                    })
                individual = all(
                    all(layer[key] for key in ("sharpe_gate", "drawdown_gate", "cagr_gate", "cycle_gate"))
                    for layer in layers
                )
                name = tag(mode, window, quantile)
                gate_details[name] = {"layers": layers, "individual_gate_pass": individual}
                if individual:
                    passed.add((mode, window, quantile))
    adjacent_pairs: list[dict[str, object]] = []
    for mode in MODES:
        for window in WINDOWS:
            for first, second in zip(QUANTILES, QUANTILES[1:]):
                if (mode, window, first) in passed and (mode, window, second) in passed:
                    adjacent_pairs.append({"mode": mode, "axis": "quantile", "window": window, "values": [first, second]})
        for quantile in QUANTILES:
            for first, second in zip(WINDOWS, WINDOWS[1:]):
                if (mode, first, quantile) in passed and (mode, second, quantile) in passed:
                    adjacent_pairs.append({"mode": mode, "axis": "window", "quantile": quantile, "values": [first, second]})
    gate_details["passed_candidates"] = [tag(*item) for item in sorted(passed)]
    gate_details["adjacent_pairs"] = adjacent_pairs

    layer4_daily = pd.read_csv(LAYER4 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(f"{scope}_m1_iv300_nomom_abs30")].sort_values("date")
        old = layer4_daily[layer4_daily.candidate.eq(f"{scope}_m1_iv300_nomom_tailnone")].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"{scope} abs30 layer-4 date parity failed")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"{scope} abs30 layer-4 return parity failed: {error}")
        parity[scope] = error

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(RUN / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "relative_iv_paired_vs_abs30.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer5_complete_awaiting_user_confirmation"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "causal_relative_iv_replace_and_overlay_at_corrected_abs_iv300_no_mom120_m1_hold_naked",
        "baseline": {"type": "absolute", "threshold": IV_THRESHOLD, "layer4_parity_max_abs": parity},
        "candidate_grid": [{"type": "absolute", "threshold": IV_THRESHOLD}] + [
            {"type": mode, "window": window, "quantile": quantile, "min_history": MIN_HISTORY}
            for mode in MODES for window in WINDOWS for quantile in QUANTILES
        ],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit 95% M+1 IV"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close causal signal using prior observations only; T+1 open route; hold to expiry",
        "excluded_layers": ["premium_decay_early_roll", "valuation_debounce", "seller_mom120", "maturity_change", "catastrophe_put"],
        "decision_checks": gate_details,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "layer4_daily": sha256(LAYER4 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "relative_iv_paired_vs_abs30.csv"), "concentration": str(RUN / "cycle_concentration.csv")},
        "warnings": ["Research only; no production or ledger change.", "Relative-IV warmup requires 60 prior valid observations.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "No bid-ask, impact, capacity, tax, dynamic margin or integer sizing.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第五层因果相对IV", "",
        "## Run Metadata", "", "研究专用；第五层完成后等待用户确认；生产、日报、账本和交易接口未修改。", "",
        "## Research Question", "", "固定修正后绝对IV>30%、不加MOM120、M+1、95% Put、q1、持有到期、无灾难保护；比较绝对基线、相对IV替代和绝对+相对叠加。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；绝对基线与第四层无保护逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "510500 Put单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘因果信号、T+1开盘；持有到期。", "",
        "## Runtime Override Plan", "", "只改变首次入场的IV尺度；相对分位不使用当天或未来数据；不改生产源码。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Output Files", "", "完整窗口、逐日路径、周期集中度、因果分位诊断及相对绝对IV30基线的配对差见本目录CSV。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus Abs30", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Decision Gates", "", "```json", json.dumps(gate_details, ensure_ascii=False, indent=2), "```", "",
        "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。", "",
        "## Stability Classification", "", f"`{stability}`，待结果解释后在最终化时更新。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第六层。", "",
        "## User-Facing Summary", "", "本层只归因因果相对IV入场尺度，不推导后续机制。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(json.dumps(gate_details, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
