"""Rebuild IC short-Put research, layer 7: premium-decay early roll."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_short95_unified_valuation_debounce_v1 as debounce
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer7_iv30_no_mom_m1_short95_naked_absolute_iv_instant_valuation_router_"
    "premium_decay_early_roll_hold_50_60_70_80"
)
LAYER6 = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer6_iv30_no_mom_m1_short95_hold_naked_absolute_iv_router_"
    "seller_only_and_unified_valuation_debounce_instant_confirm2_confirm3"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer7_premium_decay_v1_spec.md"
DECAYS: tuple[float | None, ...] = (None, 0.50, 0.60, 0.70, 0.80)
IV_THRESHOLD = 0.30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decay_tag(decay: float | None) -> str:
    return "hold" if decay is None else f"decay{int(round(decay * 100))}"


def relabel(result, decay: float | None):
    daily, trades, signals, cycles, audit = result
    scope = str(audit["scope"])
    tag = decay_tag(decay)
    candidate = f"{scope}_m1_iv300_nomom_val_instant_{tag}"
    for frame in (daily, trades, signals, cycles):
        if len(frame):
            frame["candidate"] = candidate
            frame["variant"] = f"m1_iv300_nomom_val_instant_{tag}"
            frame["premium_decay"] = np.nan if decay is None else decay
    audit.update({
        "candidate": candidate,
        "variant": f"m1_iv300_nomom_val_instant_{tag}",
        "premium_decay": np.nan if decay is None else decay,
    })
    return daily, trades, signals, cycles, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-7 run")

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

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    old_decay = base.DECAY
    try:
        for decay in DECAYS:
            base.DECAY = decay

            def real_runner(admission, _ignored, maturity, cost, selected_decay=decay):
                return real_short(admission, selected_decay, maturity, cost)

            def model_runner(admission, _ignored, maturity, cost, selected_decay=decay):
                return model_short(admission, selected_decay, maturity, cost)

            for scope in ("real", "model"):
                result = base.run_candidate(
                    prepared[scope], IV_THRESHOLD, False, futures, weights,
                    option_market, real_runner, model_runner, model_profit, real_profit,
                )
                d, t, s, c, a = relabel(result, decay)
                daily_parts.append(d); trade_parts.append(t); signal_parts.append(s)
                if len(c): cycle_parts.append(c)
                audits.append(a)
    finally:
        base.DECAY = old_decay

    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    signals = pd.concat(signal_parts, ignore_index=True, sort=False)
    cycles = pd.concat(cycle_parts, ignore_index=True, sort=False)
    exposure = pd.DataFrame(audits)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    params = exposure.set_index("candidate")[["premium_decay"]]
    for table in (summary, wide):
        table["premium_decay"] = table.candidate.map(params.premium_decay)

    cycles["cycle_pnl"] = cycles.realized_pnl.fillna(0.0) + cycles.open_cycle_pnl.fillna(0.0)
    concentration_rows: list[dict[str, object]] = []
    for candidate, frame in cycles.groupby("candidate", sort=False):
        absolute = frame.cycle_pnl.abs()
        total = float(absolute.sum())
        concentration_rows.append({
            "candidate": candidate,
            "scope": frame.scope.iloc[0],
            "premium_decay": frame.premium_decay.iloc[0],
            "cycles": len(frame),
            "closed_cycles": int(frame.closed.fillna(False).sum()),
            "positive_cycles": int(frame.cycle_pnl.gt(0).sum()),
            "negative_cycles": int(frame.cycle_pnl.lt(0).sum()),
            "early_rolls": int(frame.early_rolls.fillna(0).sum()),
            "worst_cycle_pnl": float(frame.cycle_pnl.min()),
            "cycle_pnl_total": float(frame.cycle_pnl.sum()),
            "largest_abs_cycle_share": float(absolute.max() / total) if total else np.nan,
        })
    concentration = pd.DataFrame(concentration_rows)

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    gate_details: dict[str, object] = {}
    passed: list[int] = []
    for decay in DECAYS:
        tag = decay_tag(decay)
        for scope in ("real", "model"):
            baseline = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant_hold")].iloc[0]
            row = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant_{tag}")].iloc[0]
            exp = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            conc = concentration[concentration.candidate.eq(row.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "premium_decay": "hold" if decay is None else decay,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_hold": row.ann_return - baseline.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_hold": row.sharpe_repo - baseline.sharpe_repo,
                "max_dd": row.max_dd,
                "max_dd_delta_vs_hold": row.max_dd - baseline.max_dd,
                "cycles": int(exp.cycles),
                "closed_cycles": int(conc.closed_cycles),
                "early_rolls": int(conc.early_rolls),
                "short_put_days": int(exp.short_put_days),
                "short_put_share": exp.short_put_share,
                "worst_cycle_pnl": conc.worst_cycle_pnl,
                "largest_abs_cycle_share": conc.largest_abs_cycle_share,
            })
    paired = pd.DataFrame(paired_rows)

    for decay in (0.50, 0.60, 0.70, 0.80):
        layers = []
        for scope in ("real", "model"):
            baseline = paired[(paired.scope.eq(scope)) & paired.premium_decay.eq("hold")].iloc[0]
            candidate = paired[(paired.scope.eq(scope)) & paired.premium_decay.eq(decay)].iloc[0]
            cagr_diff_pp = 100.0 * (candidate.ann_return - baseline.ann_return)
            sharpe_diff = candidate.sharpe - baseline.sharpe
            mdd_worse_pp = 100.0 * max(0.0, abs(candidate.max_dd) - abs(baseline.max_dd))
            layers.append({
                "scope": scope, "cagr_diff_pp": float(cagr_diff_pp),
                "sharpe_diff": float(sharpe_diff), "mdd_worse_pp": float(mdd_worse_pp),
                "early_rolls": int(candidate.early_rolls),
                "cagr_gate": bool(cagr_diff_pp >= -0.50),
                "sharpe_gate": bool(sharpe_diff >= -0.02),
                "drawdown_gate": bool(mdd_worse_pp <= 0.50),
                "actual_roll_gate": bool(candidate.early_rolls >= 1),
            })
        individual = all(
            all(layer[key] for key in ("cagr_gate", "sharpe_gate", "drawdown_gate", "actual_roll_gate"))
            for layer in layers
        )
        key = str(int(decay * 100))
        gate_details[key] = {"layers": layers, "individual_gate_pass": individual}
        if individual: passed.append(int(decay * 100))
    adjacent_pairs = [
        [first, second] for first, second in zip((50, 60, 70), (60, 70, 80))
        if first in passed and second in passed
    ]
    gate_details["passed_candidates"] = passed
    gate_details["adjacent_pairs"] = adjacent_pairs

    layer6_daily = pd.read_csv(LAYER6 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant_hold")].sort_values("date")
        old = layer6_daily[layer6_daily.candidate.eq(f"{scope}_m1_iv300_nomom_val_instant")].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"{scope} hold layer-6 date parity failed")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"{scope} hold layer-6 return parity failed: {error}")
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
    paired.to_csv(RUN / "premium_decay_paired_vs_hold.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer7_complete_awaiting_user_confirmation"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "premium_decay_early_roll_at_corrected_abs_iv300_no_mom120_m1_instant_naked",
        "baseline": {"premium_decay": None, "layer6_parity_max_abs": parity},
        "candidate_grid": [{"premium_decay": decay, "max_early_rolls": 0 if decay is None else 1} for decay in DECAYS],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit 95% M+1 IV"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close decay signal; T+1 open buyback and sell next listed month; at most one early roll; new leg re-admitted",
        "excluded_layers": ["relative_iv", "valuation_debounce", "seller_mom120", "maturity_change", "catastrophe_put"],
        "decision_checks": gate_details,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "layer6_daily": sha256(LAYER6 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "premium_decay_paired_vs_hold.csv"), "concentration": str(RUN / "cycle_concentration.csv")},
        "warnings": ["Research only; no production or ledger change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "5bp is a notional one-way friction assumption.", "No bid-ask, impact, capacity, tax, dynamic margin or integer sizing.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第七层权利金衰减提前展期", "",
        "## Run Metadata", "", "研究专用；第七层完成后等待用户确认；生产、日报、账本和交易接口未修改。", "",
        "## Research Question", "", "固定修正后IV>30%、不加MOM120、M+1、95% Put、q1、即时估值、无灾难保护和相对IV；比较持有到期与50%/60%/70%/80%权利金衰减后最多提前展期一次。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；持有到期与第六层即时许可逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "510500 Put单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘衰减信号、T+1开盘换腿；最多一次。", "",
        "## Runtime Override Plan", "", "只改变卖Put提前展期阈值；新腿重新准入；不改生产源码。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Output Files", "", "完整窗口、逐日路径、提前展期次数、周期集中度及相对持有到期的配对差见本目录CSV。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus Hold", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Decision Gates", "", "```json", json.dumps(gate_details, ensure_ascii=False, indent=2), "```", "",
        "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。", "",
        "## Stability Classification", "", f"`{stability}`，待结果解释后在最终化时更新。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第八层。", "",
        "## User-Facing Summary", "", "本层只归因卖Put权利金衰减提前展期，不推导后续机制。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(json.dumps(gate_details, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
