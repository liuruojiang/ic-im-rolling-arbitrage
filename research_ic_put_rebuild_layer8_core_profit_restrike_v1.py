"""Rebuild IC Put research, layer 8: core long-Put profit realization/restrike."""
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
    "20260918_ic_im_ic_put_pollution_rebuild_layer8_iv30_no_mom_m1_short95_hold_naked_absolute_iv_"
    "instant_valuation_router_core_long_put_profit_take_and_restrike_none_2x_3x"
)
LAYER7 = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_ic_put_rebuild_layer7_iv30_no_mom_m1_short95_naked_absolute_iv_instant_valuation_"
    "router_premium_decay_early_roll_hold_50_60_70_80"
)
SPEC = ROOT / "docs" / "ic_put_rebuild_layer8_core_profit_restrike_v1_spec.md"
MULTIPLES: tuple[float | None, ...] = (None, 2.0, 3.0)
IV_THRESHOLD = 0.30


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profit_tag(multiple: float | None) -> str:
    return "none" if multiple is None else f"profit{int(multiple)}x"


def override_profit_multiple(engine, multiple: float | None):
    def wrapped(*args, **kwargs):
        kwargs["profit_multiple"] = multiple
        return engine(*args, **kwargs)
    return wrapped


def relabel(result, multiple: float | None):
    daily, trades, signals, cycles, audit = result
    scope = str(audit["scope"])
    tag = profit_tag(multiple)
    candidate = f"{scope}_m1_iv300_nomom_val_instant_hold_coreprofit_{tag}"
    for frame in (daily, trades, signals, cycles):
        if len(frame):
            frame["candidate"] = candidate
            frame["variant"] = f"m1_iv300_nomom_val_instant_hold_coreprofit_{tag}"
            frame["core_profit_multiple"] = np.nan if multiple is None else multiple
    audit.update({
        "candidate": candidate,
        "variant": f"m1_iv300_nomom_val_instant_hold_coreprofit_{tag}",
        "core_profit_multiple": np.nan if multiple is None else multiple,
        "put_cost_rate_sum": float(daily.put_cost_rate.sum()),
    })
    return daily, trades, signals, cycles, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-8 run")

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

    def real_hold(admission, _ignored, maturity, cost):
        return real_short(admission, None, maturity, cost)

    def model_hold(admission, _ignored, maturity, cost):
        return model_short(admission, None, maturity, cost)

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audits: list[dict[str, object]] = []
    old_decay = base.DECAY
    try:
        base.DECAY = None
        for multiple in MULTIPLES:
            selected_model_profit = override_profit_multiple(model_profit, multiple)
            selected_real_profit = override_profit_multiple(real_profit, multiple)
            for scope in ("real", "model"):
                result = base.run_candidate(
                    prepared[scope], IV_THRESHOLD, False, futures, weights,
                    option_market, real_hold, model_hold,
                    selected_model_profit, selected_real_profit,
                )
                d, t, s, c, a = relabel(result, multiple)
                daily_parts.append(d); trade_parts.append(t); signal_parts.append(s)
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
    params = exposure.set_index("candidate")[["core_profit_multiple"]]
    for table in (summary, wide):
        table["core_profit_multiple"] = table.candidate.map(params.core_profit_multiple)

    event_rows: list[dict[str, object]] = []
    for candidate, frame in trades[trades.sleeve.eq("core")].groupby("candidate", sort=False):
        profit = frame[frame.action.eq("close_profit_restrike")]
        route = frame[frame.action.eq("route_open_exit")]
        profit_dates = set(pd.to_datetime(profit.actual_execution_date))
        route_dates = set(pd.to_datetime(route.actual_execution_date))
        event_rows.append({
            "candidate": candidate,
            "scope": frame.scope.iloc[0],
            "core_profit_multiple": frame.core_profit_multiple.iloc[0],
            "core_trade_events": len(frame),
            "profit_restrikes": len(profit),
            "profit_restrike_dates": "|".join(str(x.date()) for x in sorted(profit_dates)),
            "route_open_exits": len(route),
            "profit_route_same_execution_date": len(profit_dates & route_dates),
        })
    events = pd.DataFrame(event_rows)
    if events.profit_route_same_execution_date.ne(0).any():
        raise RuntimeError("Profit restrike and route-open exit collided on one execution date")

    full = summary[summary.segment.eq("full")]
    paired_rows: list[dict[str, object]] = []
    for multiple in MULTIPLES:
        tag = profit_tag(multiple)
        for scope in ("real", "model"):
            baseline = full[full.candidate.eq(
                f"{scope}_m1_iv300_nomom_val_instant_hold_coreprofit_none"
            )].iloc[0]
            row = full[full.candidate.eq(
                f"{scope}_m1_iv300_nomom_val_instant_hold_coreprofit_{tag}"
            )].iloc[0]
            exp = exposure[exposure.candidate.eq(row.candidate)].iloc[0]
            ev = events[events.candidate.eq(row.candidate)].iloc[0]
            paired_rows.append({
                "scope": scope,
                "core_profit_multiple": "none" if multiple is None else multiple,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_none": row.ann_return - baseline.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_none": row.sharpe_repo - baseline.sharpe_repo,
                "max_dd": row.max_dd,
                "max_dd_delta_vs_none": row.max_dd - baseline.max_dd,
                "profit_restrikes": int(ev.profit_restrikes),
                "route_open_exits": int(ev.route_open_exits),
                "put_cost_rate_sum": float(exp.put_cost_rate_sum),
                "short_put_days": int(exp.short_put_days),
                "short_put_share": exp.short_put_share,
            })
    paired = pd.DataFrame(paired_rows)

    gate_details: dict[str, object] = {}
    passed: list[int] = []
    for multiple in (2.0, 3.0):
        layers = []
        for scope in ("real", "model"):
            baseline = paired[(paired.scope.eq(scope)) & paired.core_profit_multiple.eq("none")].iloc[0]
            candidate = paired[(paired.scope.eq(scope)) & paired.core_profit_multiple.eq(multiple)].iloc[0]
            cagr_diff_pp = 100.0 * (candidate.ann_return - baseline.ann_return)
            sharpe_diff = candidate.sharpe - baseline.sharpe
            mdd_worse_pp = 100.0 * max(0.0, abs(candidate.max_dd) - abs(baseline.max_dd))
            layers.append({
                "scope": scope,
                "cagr_diff_pp": float(cagr_diff_pp),
                "sharpe_diff": float(sharpe_diff),
                "mdd_worse_pp": float(mdd_worse_pp),
                "profit_restrikes": int(candidate.profit_restrikes),
                "cagr_gate": bool(cagr_diff_pp >= 0.0),
                "sharpe_gate": bool(sharpe_diff >= 0.0),
                "drawdown_gate": bool(mdd_worse_pp <= 0.50),
                "real_event_gate": bool(scope != "real" or candidate.profit_restrikes >= 2),
            })
        individual = all(
            all(layer[key] for key in ("cagr_gate", "sharpe_gate", "drawdown_gate", "real_event_gate"))
            for layer in layers
        )
        key = str(int(multiple))
        gate_details[key] = {"layers": layers, "individual_gate_pass": individual}
        if individual:
            passed.append(int(multiple))
    direction_consistent = passed == [2, 3]
    gate_details["passed_candidates"] = passed
    gate_details["adjacent_2x_3x_direction_consistent"] = direction_consistent

    layer7_daily = pd.read_csv(LAYER7 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(
            f"{scope}_m1_iv300_nomom_val_instant_hold_coreprofit_profit3x"
        )].sort_values("date")
        old = layer7_daily[layer7_daily.candidate.eq(
            f"{scope}_m1_iv300_nomom_val_instant_hold"
        )].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"{scope} 3x layer-7 date parity failed")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"{scope} 3x layer-7 return parity failed: {error}")
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
    paired.to_csv(RUN / "core_profit_paired_vs_none.csv", index=False, encoding="utf-8-sig")
    events.to_csv(RUN / "core_profit_events.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(
        short_source + "\n\n" + profit_source, encoding="utf-8"
    )

    decision = (
        "retain_core_profit_restrike_layer_awaiting_user_confirmation"
        if direction_consistent else
        "reject_core_profit_restrike_keep_none_awaiting_user_confirmation"
    )
    stability = "adjacent_2x_3x_pass" if direction_consistent else "adjacent_threshold_gate_failed"
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "core_long_put_profit_take_and_restrike_none_2x_3x_at_corrected_seller_path",
        "baseline": {"core_profit_multiple": None, "layer7_3x_parity_max_abs": parity},
        "candidate_grid": [{"core_profit_multiple": x} for x in ("none", 2, 3)],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"], "real_source": "510500 ETF + listed 510500 Put; corrected ETF-unit seller IV"},
        "cost_model": {"510500_put_one_way": 0.0005, "ic_one_way": 0.0001, "futures_margin_buffer": 0.30, "cash_annual": 0.03},
        "execution": "T close profit signal; earliest T+1 close realize and restrike; route/monthly/target actions take priority",
        "fixed_seller_path": "absolute IV>30%; no seller MOM120; M+1 95% q1; instant valuation; naked; hold to expiry",
        "decision_checks": gate_details,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "profit_engine_source": sha256(ROOT / "research_ic_core_put_profit_restrike_v1.py"), "layer7_daily": sha256(LAYER7 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv.gz"), "signals": str(out / "signals.csv.gz"), "cycles": str(out / "cycles.csv"), "exposure": str(RUN / "exposure_audit.csv"), "paired": str(RUN / "core_profit_paired_vs_none.csv"), "events": str(RUN / "core_profit_events.csv")},
        "warnings": ["Research only; no production, email, ledger, registry or order change.", "Real listed history is shorter than five years.", "Model option history is theoretical proxy.", "5bp is a notional one-way friction assumption.", "No bid-ask, impact, capacity, tax, dynamic margin or integer sizing.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第八层核心买 Put 盈利兑现与重建", "",
        "## Run Metadata", "", "研究专用；第八层完成后等待用户确认；生产、日报、账本、登记表和交易接口未修改。", "",
        "## Research Question", "", "固定前七层最终卖 Put 路径，比较核心买 Put 不因盈利换仓、2倍兑现重建、3倍兑现重建。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；3倍路径与第七层逐日重放误差：{parity}。", "",
        "## Data Snapshot", "", "真实挂牌2022-09-19—2026-08-14；理论代理2015-04-16—2026-08-14；真实5Y/10Y为N/A。", "",
        "## Cost and Execution Assumptions", "", "510500 Put单边5bp；IC单边1bp；30%期货缓冲；现金3%；T收盘触发、最早T+1收盘兑现并重建。", "",
        "## Commands", "", "详见 `command_log.txt`。", "",
        "## Full-Sample Results", "", full.to_markdown(index=False, floatfmt=".6f"), "",
        "## Paired Effect Versus None", "", paired.to_markdown(index=False, floatfmt=".6f"), "",
        "## Event Audit", "", events.to_markdown(index=False), "",
        "## Decision Gates", "", "```json", json.dumps(gate_details, ensure_ascii=False, indent=2), "```", "",
        "## Stability Classification", "", f"`{stability}`。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第九层。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(paired.to_string(index=False))
    print(json.dumps(gate_details, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
