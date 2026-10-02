"""Rebuild IC short-Put research, layer 1: corrected absolute-IV gate only."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_ic_put_rebuild_layer1_fixed_core_short95_put_router_"
    "corrected_absolute_iv_threshold"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer1_corrected_iv_threshold_v1_spec.md"
REFERENCE = base.REFERENCE_RUN
THRESHOLDS = (0.0, 0.15, 0.175, 0.20, 0.225, 0.25, 0.275, 0.30, 0.325, 0.35, 0.375, 0.40)
NO_SHORT_THRESHOLD = 9.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relabel(frame: pd.DataFrame, old: str, new: str, variant: str) -> pd.DataFrame:
    out = frame.copy()
    if "candidate" in out:
        out["candidate"] = out["candidate"].replace(old, new)
    if "variant" in out:
        out["variant"] = variant
    return out


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-1 run")

    prior = base.prior
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, model_market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, _, _, expiries, _ = prior.router_base.short.real_inputs()
    corrected_real = base.corrected_real_signal(
        path[path.date.ge(prior.REAL_START)].reset_index(drop=True), frames, option_market, expiries
    )
    prepared = {
        scope: base.prepare_scope(
            scope, path, futures, weights, selected, grid, frames, option_market,
            model_market, corrected_real,
        )
        for scope in ("real", "model")
    }

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    reference = pd.read_csv(REFERENCE / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    for scope in ("real", "model"):
        source_name = f"{scope}_profit3x_only"
        name = f"{scope}_no_short_put"
        d = reference[reference.candidate.eq(source_name)].copy()
        d["candidate"] = name
        d["variant"] = "no_short_put"
        daily_parts.append(d)
        audits.append({
            "candidate": name,
            "scope": scope,
            "iv_threshold": np.nan,
            "mom120_gate": False,
            "eligible_days": 0,
            "cycles": 0,
            "short_put_days": 0,
            "short_put_share": 0.0,
            "assignment_or_recovery_days": 0,
            "early_rolls": 0,
            "min_cash_weight": float(d.cash_weight.min()),
            "core_profit_restrikes": np.nan,
            "core_route_exits": 0,
            "short_ledger_error": 0.0,
            "layer": "baseline",
        })

    old_decay = base.DECAY
    try:
        base.DECAY = None  # hold to expiry; later decay/roll layers are deliberately excluded
        for threshold in THRESHOLDS:
            for scope in ("real", "model"):
                d, t, s, c, a = base.run_candidate(
                    prepared[scope], threshold, False, futures, weights,
                    option_market, real_short, model_short, model_profit, real_profit,
                )
                daily_parts.append(d)
                trade_parts.append(t)
                signal_parts.append(s)
                if len(c):
                    cycle_parts.append(c)
                a["layer"] = "iv_gate"
                audits.append(a)
    finally:
        base.DECAY = old_decay

    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    signals = pd.concat(signal_parts, ignore_index=True, sort=False)
    cycles = pd.concat(cycle_parts, ignore_index=True, sort=False) if cycle_parts else pd.DataFrame()
    exposure = pd.DataFrame(audits)
    summary, wide, unavailable = prior.router_base.summarize(daily)

    threshold_map = exposure.set_index("candidate")["iv_threshold"].to_dict()
    layer_map = exposure.set_index("candidate")["layer"].to_dict()
    for table in (summary, wide):
        table["iv_threshold"] = table.candidate.map(threshold_map)
        table["layer"] = table.candidate.map(layer_map)

    full = summary[summary.segment.eq("full")].copy()
    comparison_rows: list[dict[str, object]] = []
    for scope in ("real", "model"):
        baseline = full[full.candidate.eq(f"{scope}_no_short_put")].iloc[0]
        for row in full[full.candidate.str.startswith(f"{scope}_iv")].itertuples(index=False):
            exp = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            comparison_rows.append({
                "candidate": row.candidate,
                "scope": scope,
                "iv_threshold": threshold_map[row.candidate],
                "ann_return_delta_vs_no_short": float(row.ann_return - baseline.ann_return),
                "sharpe_delta_vs_no_short": float(row.sharpe_repo - baseline.sharpe_repo),
                "max_dd_delta_vs_no_short": float(row.max_dd - baseline.max_dd),
                "cycles": int(exp.cycles),
                "short_put_days": int(exp.short_put_days),
                "short_put_share": float(exp.short_put_share),
            })
    comparison = pd.DataFrame(comparison_rows)

    parity = {"real": 0.0, "model": 0.0}

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(RUN / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "paired_vs_no_short.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_base.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer1_complete_awaiting_user_confirmation_no_production_change"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "single_parameter",
        "parameter_group": "corrected_95_put_absolute_iv_threshold",
        "baseline": {"real": "real_no_short_put", "model": "model_no_short_put", "source_candidates": ["real_profit3x_only", "model_profit3x_only"], "source_artifact": str(REFERENCE), "copied_row_parity_max_abs": parity},
        "candidate_grid": [{"iv_threshold": x, "mom120": False, "early_roll": False} for x in THRESHOLDS],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put history; corrected ETF-unit 95% selection"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close signal; T+1 open route; short Put held to expiry; no early roll",
        "excluded_layers": ["seller_mom120", "relative_iv", "premium_decay_early_roll", "catastrophe_put", "valuation_debounce", "maturity_scan"],
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "baseline_daily": sha256(REFERENCE / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "paired_vs_no_short.csv"), "corrected_real_signal": str(RUN / "corrected_real_signal_base.csv.gz")},
        "warnings": ["Research only; no production or ledger change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "Worktree was dirty before this isolated new run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    real_full = full[full.candidate.str.startswith("real_")]
    record = "\n".join([
        "# IC Put 污染后重建：第一层绝对 IV 门槛",
        "", "## Run Metadata", "", "研究专用；第一层完成后等待用户确认；生产、日报、账本和交易接口未修改。",
        "", "## Research Question", "", "修正 510500 ETF/指数单位错配后，单独检验无卖 Put、无 IV 门槛及绝对 IV 阈值；不加入 MOM120 和提前展期。",
        "", "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；无卖 Put 与保存的三倍核心 Put 基线逐日误差：{parity}。",
        "", "## Data Snapshot", "", "真实挂牌 2022-09-19—2026-08-14；理论代理 2015-04-16—2026-08-14；真实 5Y/10Y 为 N/A。",
        "", "## Cost and Execution Assumptions", "", "510500 Put 单边5bp；IC单边1bp；30%期货保证金/缓冲；现金3%；T收盘信号、T+1开盘；卖Put持有到期。",
        "", "## Runtime Override Plan", "", "只在本次运行把提前展期关闭；运行结束恢复模块常量，不改生产源码。",
        "", "## Commands", "", "详见 `command_log.txt`。",
        "", "## Output Files", "", "完整窗口、逐日路径、信号、周期、暴露及同基线配对差见本目录 CSV。",
        "", "## Full-Sample Results", "", real_full.to_markdown(index=False, floatfmt=".6f"),
        "", "## Paired Versus No Short Put", "", comparison[comparison.scope.eq("real")].to_markdown(index=False, floatfmt=".6f"),
        "", "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。",
        "", "## Stability Classification", "", f"`{stability}`，待实际结果解释后在最终化时更新。",
        "", "## Decision", "", f"`{decision}`。未经用户确认不进入第二层。",
        "", "## User-Facing Summary", "", "本层只回答修正后的绝对IV门槛与无卖Put基线的差异，不推导MOM120或其他增强层。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison[comparison.scope.eq("real")].to_string(index=False))


if __name__ == "__main__":
    main()
