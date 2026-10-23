"""Rebuild IC short-Put research, layer 2: IV30 with/without MOM120."""
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
    "20260917_ic_im_ic_put_rebuild_layer2_iv30_fixed_core_short95_put_router_"
    "seller_mom120_gate"
)
LAYER1 = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_ic_put_rebuild_layer1_fixed_core_short95_put_router_"
    "corrected_absolute_iv_threshold"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer2_mom120_v1_spec.md"
IV_THRESHOLDS = (0.275, 0.30, 0.325)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-2 run")

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
    old_decay = base.DECAY
    try:
        base.DECAY = None
        for threshold in IV_THRESHOLDS:
            for mom120 in (False, True):
                for scope in ("real", "model"):
                    d, t, s, c, a = base.run_candidate(
                        prepared[scope], threshold, mom120, futures, weights,
                        option_market, real_short, model_short, model_profit, real_profit,
                    )
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

    params = exposure.set_index("candidate")[["iv_threshold", "mom120_gate"]]
    for table in (summary, wide):
        table["iv_threshold"] = table.candidate.map(params.iv_threshold)
        table["mom120_gate"] = table.candidate.map(params.mom120_gate)

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    for threshold in IV_THRESHOLDS:
        suffix = int(round(threshold * 1000))
        for scope in ("real", "model"):
            no = full[full.candidate.eq(f"{scope}_iv{suffix:03d}_nomom")].iloc[0]
            yes = full[full.candidate.eq(f"{scope}_iv{suffix:03d}_mom120")].iloc[0]
            no_exp = exposure[exposure.candidate.eq(no.candidate)].iloc[0]
            yes_exp = exposure[exposure.candidate.eq(yes.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "iv_threshold": threshold,
                "ann_return_without_mom120": no.ann_return,
                "ann_return_with_mom120": yes.ann_return,
                "ann_return_delta": yes.ann_return - no.ann_return,
                "sharpe_without_mom120": no.sharpe_repo,
                "sharpe_with_mom120": yes.sharpe_repo,
                "sharpe_delta": yes.sharpe_repo - no.sharpe_repo,
                "max_dd_without_mom120": no.max_dd,
                "max_dd_with_mom120": yes.max_dd,
                "max_dd_delta": yes.max_dd - no.max_dd,
                "cycles_without_mom120": int(no_exp.cycles),
                "cycles_with_mom120": int(yes_exp.cycles),
                "short_put_days_without_mom120": int(no_exp.short_put_days),
                "short_put_days_with_mom120": int(yes_exp.short_put_days),
                "short_put_share_without_mom120": no_exp.short_put_share,
                "short_put_share_with_mom120": yes_exp.short_put_share,
            })
    paired = pd.DataFrame(paired_rows)

    cycles["cycle_pnl"] = cycles.realized_pnl.fillna(0.0) + cycles.open_cycle_pnl.fillna(0.0)
    concentration_rows = []
    for candidate, frame in cycles.groupby("candidate", sort=False):
        absolute = frame.cycle_pnl.abs()
        total = float(absolute.sum())
        concentration_rows.append({
            "candidate": candidate,
            "scope": frame.scope.iloc[0],
            "cycles": len(frame),
            "positive_cycles": int(frame.cycle_pnl.gt(0).sum()),
            "negative_cycles": int(frame.cycle_pnl.lt(0).sum()),
            "cycle_pnl_total": float(frame.cycle_pnl.sum()),
            "largest_abs_cycle_share": float(absolute.max() / total) if total else np.nan,
            "top2_abs_cycle_share": float(absolute.nlargest(2).sum() / total) if total else np.nan,
        })
    concentration = pd.DataFrame(concentration_rows)

    layer1 = pd.read_csv(LAYER1 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for threshold in IV_THRESHOLDS:
        suffix = int(round(threshold * 1000))
        for scope in ("real", "model"):
            candidate = f"{scope}_iv{suffix:03d}_nomom"
            got = daily[daily.candidate.eq(candidate)].sort_values("date")
            old = layer1[layer1.candidate.eq(candidate)].sort_values("date")
            if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
                raise RuntimeError(f"{candidate} layer-1 date parity failed")
            error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"{candidate} layer-1 return parity failed: {error}")
            parity[candidate] = error

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(RUN / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "mom120_paired_effect.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_base.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer2_complete_awaiting_user_confirmation"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "seller_mom120_gate_at_corrected_iv275_iv300_iv325",
        "baseline": {"paired_without_mom120": ["iv275_nomom", "iv300_nomom", "iv325_nomom"], "layer1_parity_max_abs": parity},
        "candidate_grid": [{"iv_threshold": threshold, "mom120_gate": gate, "early_roll": False} for threshold in IV_THRESHOLDS for gate in (False, True)],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit 95% selection"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close signal; T+1 open route; short Put held to expiry; no early roll",
        "excluded_layers": ["relative_iv", "premium_decay_early_roll", "catastrophe_put", "valuation_debounce", "maturity_scan"],
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "layer1_daily": sha256(LAYER1 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "mom120_paired_effect.csv"), "concentration": str(RUN / "cycle_concentration.csv")},
        "warnings": ["Research only; no production or ledger change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第二层 MOM120 卖方许可", "",
        "## Run Metadata", "", "研究专用；第二层完成后等待用户确认；生产、日报、账本和交易接口未修改。", "",
        "## Research Question", "", "固定修正后IV>27.5%/30%/32.5%，逐档比较新增MOM120>=0许可与不新增。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；第一层IV30逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "510500 Put单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘信号、T+1开盘；持有到期。", "",
        "## Runtime Override Plan", "", "只在本次运行切换MOM120许可；提前展期保持关闭；不改生产源码。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Output Files", "", "完整窗口、逐日路径、周期集中度及配对差见本目录CSV。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。", "",
        "## Stability Classification", "", f"`{stability}`，待结果解释后在最终化时更新。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第三层。", "",
        "## User-Facing Summary", "", "本层只归因MOM120，不推导后续机制。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(concentration.to_string(index=False))


if __name__ == "__main__":
    main()
