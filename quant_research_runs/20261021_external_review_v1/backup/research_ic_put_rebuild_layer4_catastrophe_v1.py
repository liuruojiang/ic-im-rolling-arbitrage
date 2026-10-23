"""Rebuild IC short-Put research, layer 4: same-expiry catastrophe Put."""
from __future__ import annotations

import hashlib
import json
import subprocess
import types
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_short95_catastrophe_put_scan_v1 as catastrophe
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer4_iv30_no_mom_m1_short95_put_router_"
    "same_expiry_catastrophe_put_none_90_85_80"
)
LAYER3 = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer3_iv30_no_mom_short95_put_router_"
    "listed_maturity_front10_m1_m2_m3"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer4_catastrophe_v1_spec.md"
RATIOS: tuple[float | None, ...] = (None, 0.90, 0.85, 0.80)
IV_THRESHOLD = 0.30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hold_to_expiry_protected_runners():
    """Reuse the audited catastrophe state machines while allowing threshold=None."""
    _, _, source = catastrophe.protected_short_runners()
    real_old = '    label = f"real_{maturity_name}_decay_{int(threshold * 100)}_tail{\'none\' if catastrophe_ratio is None else int(catastrophe_ratio * 100)}"'
    real_new = '    roll_tag = "hold" if threshold is None else f"decay{int(threshold * 100)}"\n    label = f"real_{maturity_name}_{roll_tag}_tail{\'none\' if catastrophe_ratio is None else int(catastrophe_ratio * 100)}"'
    model_old = '    label = f"model_{maturity_name}_decay_{int(threshold * 100)}_tail{\'none\' if catastrophe_ratio is None else int(catastrophe_ratio * 100)}"'
    model_new = '    roll_tag = "hold" if threshold is None else f"decay{int(threshold * 100)}"\n    label = f"model_{maturity_name}_{roll_tag}_tail{\'none\' if catastrophe_ratio is None else int(catastrophe_ratio * 100)}"'
    if real_old not in source or model_old not in source:
        raise RuntimeError("Catastrophe runner label anchor changed")
    source = source.replace(real_old, real_new, 1).replace(model_old, model_new, 1)
    real_pos_old = '"entry_premium": float(q.open), "rolled": False, "roll_wait_reason": ""}'
    real_pos_new = '"entry_premium": float(q.open), "rolled": False, "roll_wait_reason": "", "tail_security_id": (str(tail_selected.security_id) if catastrophe_ratio is not None else ""), "tail_strike": (float(tail_selected.strike) if catastrophe_ratio is not None else np.nan), "tail_mark": (float(tq.open) if catastrophe_ratio is not None else np.nan)}'
    model_pos_old = '"entry_premium":op,"rolled":False,"roll_wait_reason":""}'
    model_pos_new = '"entry_premium":op,"rolled":False,"roll_wait_reason":"","tail_strike":tail_strike,"tail_mark":tail_open}'
    if real_pos_old not in source or model_pos_old not in source:
        raise RuntimeError("Catastrophe runner position anchor changed")
    source = source.replace(real_pos_old, real_pos_new, 1).replace(model_pos_old, model_pos_new, 1)
    namespace = dict(vars(catastrophe.base.short))
    namespace.update(maturity=catastrophe.maturity, choose_tail_real=catastrophe.choose_tail_real)
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    return namespace["run_real_tail"], namespace["run_model_tail"], source


def suffix(ratio: float | None) -> str:
    return "none" if ratio is None else str(int(round(ratio * 100)))


def relabel(result, ratio: float | None, short_audit: dict[str, object]):
    daily, trades, signals, cycles, audit = result
    scope = str(audit["scope"])
    tail = suffix(ratio)
    candidate = f"{scope}_m1_iv300_nomom_tail{tail}"
    for frame in (daily, trades, signals, cycles):
        if len(frame):
            frame["candidate"] = candidate
            frame["variant"] = f"m1_iv300_nomom_tail{tail}"
            frame["catastrophe_ratio"] = np.nan if ratio is None else ratio
    audit.update({
        "candidate": candidate,
        "variant": f"m1_iv300_nomom_tail{tail}",
        "catastrophe_ratio": np.nan if ratio is None else ratio,
        "tail_entry_skips": int(short_audit.get("tail_entry_skips", 0)),
        "tail_stale_mark_days": int(short_audit.get("tail_stale_mark_days", 0)),
        "tail_positive_expiries": int(short_audit.get("tail_positive_expiries", 0)),
    })
    return daily, trades, signals, cycles, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-4 run")

    prior = base.prior
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    _, _, _, model_market = prior.configure_short_runners(path, futures)
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

    real_tail, model_tail, short_source = hold_to_expiry_protected_runners()
    _, etf, chains, options, _, _ = prior.router_base.short.real_inputs()
    real_path = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    model_path = path.reset_index(drop=True)
    real_tail.__globals__["real_inputs"] = lambda: (
        real_path, etf, chains, options, expiries, futures
    )
    model_tail.__globals__["model_source"] = types.SimpleNamespace(
        model_inputs=lambda: (model_path, model_market, futures),
        CASH=prior.router_base.short.model_source.CASH,
    )
    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    old_decay = base.DECAY
    try:
        base.DECAY = None
        for ratio in RATIOS:
            for scope in ("real", "model"):
                captured: dict[str, object] = {}

                def real_runner(admission, _decay, maturity, cost, r=ratio):
                    output = real_tail(admission, None, maturity, cost, r)
                    captured.update(output[3])
                    return output

                def model_runner(admission, _decay, maturity, cost, r=ratio):
                    output = model_tail(admission, None, maturity, cost, r)
                    captured.update(output[3])
                    return output

                result = base.run_candidate(
                    prepared[scope], IV_THRESHOLD, False, futures, weights,
                    option_market, real_runner, model_runner, model_profit, real_profit,
                )
                d, t, s, c, a = relabel(result, ratio, captured)
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
    params = exposure.set_index("candidate")[["catastrophe_ratio", "iv_threshold", "mom120_gate"]]
    for table in (summary, wide):
        table["catastrophe_ratio"] = table.candidate.map(params.catastrophe_ratio)
        table["iv_threshold"] = table.candidate.map(params.iv_threshold)
        table["mom120_gate"] = table.candidate.map(params.mom120_gate)

    cycles["cycle_pnl"] = cycles.realized_pnl.fillna(0.0) + cycles.open_cycle_pnl.fillna(0.0)
    concentration_rows: list[dict[str, object]] = []
    for candidate, frame in cycles.groupby("candidate", sort=False):
        absolute = frame.cycle_pnl.abs()
        total = float(absolute.sum())
        concentration_rows.append({
            "candidate": candidate,
            "scope": frame.scope.iloc[0],
            "catastrophe_ratio": frame.catastrophe_ratio.iloc[0],
            "cycles": len(frame),
            "positive_cycles": int(frame.cycle_pnl.gt(0).sum()),
            "negative_cycles": int(frame.cycle_pnl.lt(0).sum()),
            "worst_cycle_pnl": float(frame.cycle_pnl.min()),
            "cycle_pnl_total": float(frame.cycle_pnl.sum()),
            "largest_abs_cycle_share": float(absolute.max() / total) if total else np.nan,
            "tail_positive_expiries": int(frame.tail_expiry_payoff.fillna(0).gt(0).sum()),
            "tail_expiry_payoff_total": float(frame.tail_expiry_payoff.fillna(0).sum()),
        })
    concentration = pd.DataFrame(concentration_rows)

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    gate_details: dict[str, object] = {}
    retained: list[int] = []
    for ratio in RATIOS:
        tail = suffix(ratio)
        for scope in ("real", "model"):
            baseline = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_tailnone")].iloc[0]
            row = full[full.candidate.eq(f"{scope}_m1_iv300_nomom_tail{tail}")].iloc[0]
            exp = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            conc = concentration[concentration.candidate.eq(row.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "catastrophe_ratio": "none" if ratio is None else ratio,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_none": row.ann_return - baseline.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_none": row.sharpe_repo - baseline.sharpe_repo,
                "max_dd": row.max_dd,
                "max_dd_delta_vs_none": row.max_dd - baseline.max_dd,
                "cycles": int(exp.cycles),
                "short_put_days": int(exp.short_put_days),
                "tail_entry_skips": int(exp.tail_entry_skips),
                "tail_stale_mark_days": int(exp.tail_stale_mark_days),
                "tail_positive_expiries": int(exp.tail_positive_expiries),
                "worst_cycle_pnl": conc.worst_cycle_pnl,
            })
    paired = pd.DataFrame(paired_rows)

    for ratio in (0.90, 0.85, 0.80):
        layers = []
        worst_improved = False
        tail_identified = False
        for scope in ("real", "model"):
            naked = paired[(paired.scope.eq(scope)) & (paired.catastrophe_ratio.eq("none"))].iloc[0]
            candidate = paired[(paired.scope.eq(scope)) & (paired.catastrophe_ratio.eq(ratio))].iloc[0]
            cagr_loss_pp = 100.0 * (naked.ann_return - candidate.ann_return)
            mdd_worse_pp = 100.0 * max(0.0, abs(candidate.max_dd) - abs(naked.max_dd))
            worst_improved |= bool(candidate.worst_cycle_pnl > naked.worst_cycle_pnl)
            tail_identified |= bool(candidate.tail_positive_expiries > 0)
            layers.append({
                "scope": scope,
                "cagr_loss_pp": float(cagr_loss_pp),
                "mdd_worse_pp": float(mdd_worse_pp),
                "return_gate": bool(cagr_loss_pp <= 1.50),
                "drawdown_gate": bool(mdd_worse_pp <= 0.50),
            })
        passed = all(x["return_gate"] and x["drawdown_gate"] for x in layers) and worst_improved and tail_identified
        gate_details[str(int(ratio * 100))] = {
            "layers": layers,
            "worst_cycle_improved_any_layer": worst_improved,
            "tail_effect_identified": tail_identified,
            "individual_gate_pass": passed,
        }
        if passed:
            retained.append(int(ratio * 100))
    adjacent = any(a in retained and b in retained for a, b in ((90, 85), (85, 80)))
    gate_details["adjacent_gate"] = adjacent

    layer3_daily = pd.read_csv(LAYER3 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(f"{scope}_m1_iv300_nomom_tailnone")].sort_values("date")
        old = layer3_daily[layer3_daily.candidate.eq(f"{scope}_m1_iv300_nomom")].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"{scope} naked layer-3 date parity failed")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"{scope} naked layer-3 return parity failed: {error}")
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
    paired.to_csv(RUN / "catastrophe_paired_vs_none.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer4_complete_awaiting_user_confirmation"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "same_expiry_catastrophe_put_at_corrected_iv300_no_mom120_m1_hold",
        "baseline": {"catastrophe_ratio": None, "layer3_parity_max_abs": parity},
        "candidate_grid": [{"catastrophe_ratio": r, "maturity": "m1", "iv_threshold": IV_THRESHOLD, "mom120_gate": False, "early_roll": False} for r in RATIOS],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit 95% short leg and same-expiry listed tail"},
        "cost_model": {"each_510500_put_leg_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close signal; T+1 open both legs; hold to expiry; protected pair fails closed if either leg is not executable",
        "excluded_layers": ["relative_iv", "premium_decay_early_roll", "valuation_debounce", "seller_mom120", "maturity_change"],
        "decision_checks": gate_details,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "layer3_daily": sha256(LAYER3 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "catastrophe_paired_vs_none.csv"), "concentration": str(RUN / "cycle_concentration.csv")},
        "warnings": ["Research only; no production or ledger change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "No bid-ask, impact, capacity, tax, dynamic margin or integer sizing.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第四层灾难保护Put", "",
        "## Run Metadata", "", "研究专用；第四层完成后等待用户确认；生产、日报、账本和交易接口未修改。", "",
        "## Research Question", "", "固定修正后IV>30%、不加MOM120、M+1、95% Put、q1、持有到期；比较无保护与同到期等数量90%/85%/80%保护Put。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；无保护与第三层M+1逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "每条510500 Put腿单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘信号、T+1开盘同步执行；持有到期。", "",
        "## Runtime Override Plan", "", "只增加同到期灾难保护腿；无保护为不加第四层的基线；不改生产源码。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Output Files", "", "完整窗口、逐日路径、周期集中度、保护腿诊断及相对无保护配对差见本目录CSV。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus None", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Decision Gates", "", "```json", json.dumps(gate_details, ensure_ascii=False, indent=2), "```", "",
        "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。", "",
        "## Stability Classification", "", f"`{stability}`，待结果解释后在最终化时更新。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第五层。", "",
        "## User-Facing Summary", "", "本层只归因灾难保护Put，不推导后续机制。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(json.dumps(gate_details, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
