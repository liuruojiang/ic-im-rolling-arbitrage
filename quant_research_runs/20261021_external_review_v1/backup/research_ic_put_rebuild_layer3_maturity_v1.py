"""Rebuild IC short-Put research, layer 3: listed maturity selection."""
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
    "20260918_ic_im_ic_put_rebuild_layer3_iv30_no_mom_short95_put_router_"
    "listed_maturity_front10_m1_m2_m3"
)
LAYER1 = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_ic_put_rebuild_layer1_fixed_core_short95_put_router_"
    "corrected_absolute_iv_threshold"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer3_maturity_v1_spec.md"
MATURITIES = ("front10", "m1", "m2", "m3")
IV_THRESHOLD = 0.30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corrected_real_signal_for_maturity(
    active: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    option_market: pd.DataFrame,
    expiries: list[pd.Timestamp],
    maturity: str,
) -> pd.DataFrame:
    """Build the real signal with ETF-unit strike selection for one maturity."""
    prior = base.prior
    admission = prior.maturity.base.permission()
    snapshots = frames["snapshots"]
    chains = {pd.Timestamp(day): group for day, group in snapshots.groupby("date", sort=False)}
    histories = frames["histories"].set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    market = option_market.set_index("date")
    trade_dates = pd.DatetimeIndex(active.date)
    rows: list[dict[str, object]] = []
    engine = prior.sleeve.ic_put.v1.put_engine
    for idx in range(1, len(active)):
        evaluation = pd.Timestamp(active.loc[idx - 1, "date"])
        execution = pd.Timestamp(active.loc[idx, "date"])
        etf_close = float(etf.loc[evaluation, "close"])
        chain = chains.get(evaluation, snapshots.iloc[:0])
        month = prior.maturity.real_entry_month(execution, chain, expiries, trade_dates, maturity)
        selected = prior.router_base.short.choose_real(chain, etf_close, month)
        row: dict[str, object] = {
            "evaluation_date": evaluation,
            "eval_date": evaluation,
            "execution_date": execution,
            "maturity": maturity,
            "selected_contract_month": month,
            "execution_contract": "",
            "iv_contract": "",
            "iv": np.nan,
            "vendor_iv": np.nan,
            "admission": bool(admission.get(execution, False)),
            "execution_open_valid": False,
            "front_fallback_to_m1": bool(
                maturity == "front10" and month != execution.to_period("M").to_timestamp()
            ),
            "signal_moneyness": np.nan,
        }
        if selected is not None:
            security_id = str(selected.security_id)
            eval_quote = histories.loc[(security_id, evaluation)] if (security_id, evaluation) in histories.index else None
            execution_quote = histories.loc[(security_id, execution)] if (security_id, execution) in histories.index else None
            expiry = pd.Timestamp(expiries.loc[security_id])
            years = max((expiry - evaluation).days, 0) / 365.0
            iv = None
            if eval_quote is not None:
                iv = engine.implied_volatility(
                    float(eval_quote.close), etf_close, float(selected.strike),
                    float(market.loc[evaluation, "rate_close"]),
                    float(market.loc[evaluation, "dividend_close"]), years,
                )
            valid = bool(
                execution_quote is not None
                and float(execution_quote.open) > 0
                and float(execution_quote.volume) > 0
            )
            row.update({
                "execution_contract": security_id,
                "iv_contract": security_id,
                "iv": float(iv) if iv is not None else np.nan,
                "vendor_iv": float(selected.implied_volatility),
                "execution_open_valid": valid,
                "signal_moneyness": float(selected.strike) / etf_close,
            })
        rows.append(row)
    out = pd.DataFrame(rows)
    observed = out.signal_moneyness.dropna()
    if observed.empty or not observed.between(0.90, 1.00).all():
        raise RuntimeError(f"Corrected real IC {maturity} observed signal is not a 95% Put")
    return out


def relabel(
    frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]],
    maturity: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    daily, trades, signals, cycles, audit = frames
    scope = str(audit["scope"])
    candidate = f"{scope}_{maturity}_iv300_nomom"
    for frame in (daily, trades, signals, cycles):
        if len(frame):
            frame["candidate"] = candidate
            frame["variant"] = f"{maturity}_iv300_nomom"
            frame["maturity"] = maturity
    audit["candidate"] = candidate
    audit["variant"] = f"{maturity}_iv300_nomom"
    audit["maturity"] = maturity
    return daily, trades, signals, cycles, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-3 run")

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

    corrected_signals = {
        maturity: corrected_real_signal_for_maturity(active_real, frames, option_market, expiries, maturity)
        for maturity in MATURITIES
    }
    prepared: dict[tuple[str, str], dict[str, object]] = {}
    for maturity in MATURITIES:
        real_prepared = base.prepare_scope(
            "real", path, futures, weights, selected, grid, frames, option_market,
            model_market, corrected_signals[maturity],
        )
        real_prepared["base_signal"] = corrected_signals[maturity]
        prepared[("real", maturity)] = real_prepared

        model_prepared = base.prepare_scope(
            "model", path, futures, weights, selected, grid, frames, option_market,
            model_market, corrected_signals[maturity],
        )
        model_prepared["base_signal"] = prior.maturity.maturity_model_signals(
            model_prepared["active"], model_market, maturity
        )
        prepared[("model", maturity)] = model_prepared

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    old_decay = base.DECAY
    try:
        base.DECAY = None
        for maturity in MATURITIES:
            real_runner = lambda admission, decay, _ignored, cost, m=maturity: real_short(admission, decay, m, cost)
            model_runner = lambda admission, decay, _ignored, cost, m=maturity: model_short(admission, decay, m, cost)
            for scope in ("real", "model"):
                result = base.run_candidate(
                    prepared[(scope, maturity)], IV_THRESHOLD, False, futures, weights,
                    option_market, real_runner, model_runner, model_profit, real_profit,
                )
                d, t, s, c, a = relabel(result, maturity)
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

    params = exposure.set_index("candidate")[["maturity", "iv_threshold", "mom120_gate"]]
    for table in (summary, wide):
        table["maturity"] = table.candidate.map(params.maturity)
        table["iv_threshold"] = table.candidate.map(params.iv_threshold)
        table["mom120_gate"] = table.candidate.map(params.mom120_gate)

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    for scope in ("real", "model"):
        baseline = full[full.candidate.eq(f"{scope}_m1_iv300_nomom")].iloc[0]
        baseline_exp = exposure[exposure.candidate.eq(baseline.candidate)].iloc[0]
        for maturity in MATURITIES:
            row = full[full.candidate.eq(f"{scope}_{maturity}_iv300_nomom")].iloc[0]
            row_exp = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "maturity": maturity,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_m1": row.ann_return - baseline.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_m1": row.sharpe_repo - baseline.sharpe_repo,
                "max_dd": row.max_dd,
                "max_dd_delta_vs_m1": row.max_dd - baseline.max_dd,
                "cycles": int(row_exp.cycles),
                "cycles_delta_vs_m1": int(row_exp.cycles - baseline_exp.cycles),
                "short_put_days": int(row_exp.short_put_days),
                "short_put_share": row_exp.short_put_share,
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
            "maturity": frame.maturity.iloc[0],
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
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(f"{scope}_m1_iv300_nomom")].sort_values("date")
        old = layer1[layer1.candidate.eq(f"{scope}_iv300_nomom")].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"{scope} M+1 layer-1 date parity failed")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"{scope} M+1 layer-1 return parity failed: {error}")
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
    paired.to_csv(RUN / "maturity_paired_vs_m1.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    pd.concat(corrected_signals.values(), ignore_index=True).to_csv(
        RUN / "corrected_real_signals_by_maturity.csv.gz", index=False, compression="gzip"
    )
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "layer3_complete_awaiting_user_confirmation"
    stability = "pending_result_interpretation"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "listed_maturity_at_corrected_iv300_no_mom120",
        "baseline": {"maturity": "m1", "layer1_parity_max_abs": parity},
        "candidate_grid": [{"maturity": m, "iv_threshold": IV_THRESHOLD, "mom120_gate": False, "early_roll": False} for m in MATURITIES],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit 95% selection"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close signal; T+1 open route; short Put held to expiry; no early roll",
        "excluded_layers": ["relative_iv", "premium_decay_early_roll", "catastrophe_put", "valuation_debounce", "seller_mom120"],
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "corrected_engine": sha256(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py"), "layer1_daily": sha256(LAYER1 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "maturity_paired_vs_m1.csv"), "concentration": str(RUN / "cycle_concentration.csv")},
        "warnings": ["Research only; no production or ledger change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第三层挂牌期限", "",
        "## Run Metadata", "", "研究专用；第三层完成后等待用户确认；生产、日报、账本和交易接口未修改。", "",
        "## Research Question", "", "固定修正后IV>30%、不加MOM120、95% Put、q1、持有到期；比较front10/M+1/M+2/M+3，M+1为基线。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；M+1与第一层逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "510500 Put单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘信号、T+1开盘；持有到期。", "",
        "## Runtime Override Plan", "", "只在本次运行切换挂牌期限；M+1是不加期限改变的基线；不改生产源码。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Output Files", "", "完整窗口、逐日路径、周期集中度及相对M+1配对差见本目录CSV。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus M+1", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Window Results", "", "完整窗口见 `scan_summary.csv` 与 `window_metrics.csv`。", "",
        "## Stability Classification", "", f"`{stability}`，待结果解释后在最终化时更新。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第四层。", "",
        "## User-Facing Summary", "", "本层只归因挂牌期限，不推导后续机制。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(concentration.to_string(index=False))


if __name__ == "__main__":
    main()
