"""Rebuild IC Put research, layer 10: cost and leave-one-cycle-out robustness."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_put_rebuild_layer9_short_quantity_delta_v1 as layer9
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l10_3x_qd05_robustness_put_cost_5_10_20bp_and_cycle_loo"
LAYER9 = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l9_short_put_sizing_q1_qd05_qd10"
SPEC = ROOT / "docs" / "ic_put_rebuild_layer10_cost_cycle_robustness_v1_spec.md"
COST_BPS = (5, 10, 20)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def no_seller_profit3x(scope: str, prepared: dict[str, object], weights: pd.Series,
                       option_market: pd.DataFrame, model_profit, real_profit,
                       cost_bp: int) -> pd.DataFrame:
    prior = base.prior
    active = prepared["active"]
    schedule = prior.router_base.mask_schedule(
        prepared["core_schedule"], scope, active.date
    )
    label = f"{scope}_noseller_profit3x_cost{cost_bp}bp"
    if scope == "real":
        core_put, _ = real_profit(
            prepared["qic"], schedule, prepared["qframes"], option_market,
            label, prepared["put_roll_dates"], open_exit_dates=frozenset(),
            profit_multiple=3.0,
        )
    else:
        core_put, _ = model_profit(
            prepared["qic"], schedule, option_market, label,
            prepared["put_roll_dates"], open_exit_dates=frozenset(),
            profit_multiple=3.0,
        )
    core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
    core_put = prior.maturity.costed_core(core_put, float(cost_bp))
    total_put = prior.combine_puts(core_put, prepared["mom_put"])
    normal = prior.normal_router(active)
    daily = prior.compose(active, normal, weights, prepared["grid"], total_put, label)
    daily["scope"] = scope
    daily["variant"] = "noseller_profit3x"
    daily["cost_bp"] = cost_bp
    daily["stress_type"] = "cost"
    return daily


def leave_one_cycle_out(scope: str, joint: pd.DataFrame, no_seller: pd.DataFrame,
                        cycles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    joint = joint.sort_values("date").reset_index(drop=True)
    no_seller = no_seller.sort_values("date").reset_index(drop=True)
    if not joint.date.equals(no_seller.date):
        raise RuntimeError(f"LOO date mismatch {scope}")
    starts = sorted(pd.to_datetime(cycles.entry_date.unique()))
    if not starts:
        raise RuntimeError(f"No cycles for {scope}")
    dates = pd.DatetimeIndex(joint.date)
    raw = []
    total_log_increment = float(
        np.log1p(joint.return_net).sum() - np.log1p(no_seller.return_net).sum()
    )
    for i, start in enumerate(starts):
        next_start = starts[i + 1] if i + 1 < len(starts) else None
        mask = (dates >= start) if next_start is None else ((dates >= start) & (dates < next_start))
        if not mask.any():
            raise RuntimeError(f"Empty LOO block {scope} {i}")
        increment = float(
            np.log1p(joint.loc[mask, "return_net"]).sum()
            - np.log1p(no_seller.loc[mask, "return_net"]).sum()
        )
        raw.append((i, start, dates[mask][-1], mask, increment))
    absolute_total = sum(abs(item[4]) for item in raw)
    daily_parts, rows = [], []
    for i, start, end, mask, increment in raw:
        candidate = joint.copy()
        candidate.loc[mask, "return_net"] = no_seller.loc[mask, "return_net"].to_numpy()
        candidate["candidate"] = f"{scope}_qd05_profit3x_cost5bp_loo_cycle{i:02d}"
        candidate["variant"] = "qd05_profit3x_loo"
        candidate["stress_type"] = "leave_one_cycle_out"
        candidate["nav"] = (1.0 + candidate.return_net).cumprod()
        daily_parts.append(candidate)
        rows.append({
            "scope": scope, "cycle_id": i, "block_start": start,
            "block_end": end, "block_rows": int(mask.sum()),
            "incremental_log_return": increment,
            "absolute_contribution_share": abs(increment) / absolute_total if absolute_total else 0.0,
            "total_incremental_log_return": total_log_increment,
            "loo_candidate": candidate.candidate.iloc[0],
        })
    return pd.concat(daily_parts, ignore_index=True), pd.DataFrame(rows)


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-10 run")

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

    original_option_cost = prior.OPTION_ONE_WAY
    original_put_multiplier = base.PUT_COST_MULTIPLIER
    cost_parts: list[pd.DataFrame] = []
    cost_results: dict[int, dict[str, tuple]] = {}
    exposure_parts: list[pd.DataFrame] = []
    try:
        for cost_bp in COST_BPS:
            prior.OPTION_ONE_WAY = cost_bp / 10000.0
            base.PUT_COST_MULTIPLIER = float(cost_bp)
            prepared = {
                scope: base.prepare_scope(
                    scope, path, futures, weights, selected, grid, frames,
                    option_market, model_market, corrected_real,
                )
                for scope in ("real", "model")
            }
            prepared["model"]["base_signal"] = prior.maturity.maturity_model_signals(
                prepared["model"]["active"], model_market, "m1"
            )
            result = {
                scope: layer9.run_scope(
                    scope, prepared[scope], futures, weights, option_market,
                    model_market, real_short, model_short, model_profit, real_profit,
                )
                for scope in ("real", "model")
            }
            cost_results[cost_bp] = result
            for scope in ("real", "model"):
                source = result[scope][0]
                for mode, tag in (("q1_notional", "q1"), ("q_delta05", "qd05")):
                    part = source[source.candidate.eq(
                        f"{scope}_m1_iv300_nomom_hold_coreprofit_profit3x_{mode}"
                    )].copy()
                    part["candidate"] = f"{scope}_{tag}_profit3x_cost{cost_bp}bp"
                    part["variant"] = f"{tag}_profit3x"
                    part["cost_bp"] = cost_bp
                    part["stress_type"] = "cost"
                    cost_parts.append(part)
                cost_parts.append(no_seller_profit3x(
                    scope, prepared[scope], weights, option_market,
                    model_profit, real_profit, cost_bp,
                ))
                exp = result[scope][6]
                exp = exp[
                    exp.profit_line.eq("profit3x")
                    & exp.quantity_mode.isin(["q1_notional", "q_delta05"])
                ].copy()
                exp["cost_bp"] = cost_bp
                exposure_parts.append(exp)
    finally:
        prior.OPTION_ONE_WAY = original_option_cost
        base.PUT_COST_MULTIPLIER = original_put_multiplier

    cost_daily = pd.concat(cost_parts, ignore_index=True, sort=False)
    exposure = pd.concat(exposure_parts, ignore_index=True, sort=False)
    reference = pd.read_csv(LAYER9 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        for tag, old_mode in (("q1", "q1_notional"), ("qd05", "q_delta05")):
            got = cost_daily[cost_daily.candidate.eq(
                f"{scope}_{tag}_profit3x_cost5bp"
            )].sort_values("date")
            old = reference[reference.candidate.eq(
                f"{scope}_m1_iv300_nomom_hold_coreprofit_profit3x_{old_mode}"
            )].sort_values("date")
            if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
                raise RuntimeError(f"5bp parity dates failed {scope} {tag}")
            error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"5bp parity returns failed {scope} {tag}: {error}")
            parity[f"{scope}_{tag}"] = error

    loo_daily_parts, concentration_parts = [], []
    for scope in ("real", "model"):
        joint = cost_daily[cost_daily.candidate.eq(f"{scope}_qd05_profit3x_cost5bp")]
        no_seller = cost_daily[cost_daily.candidate.eq(f"{scope}_noseller_profit3x_cost5bp")]
        cycles = cost_results[5][scope][4]
        loo_daily, concentration = leave_one_cycle_out(scope, joint, no_seller, cycles)
        loo_daily_parts.append(loo_daily)
        concentration_parts.append(concentration)
    loo_daily = pd.concat(loo_daily_parts, ignore_index=True)
    concentration = pd.concat(concentration_parts, ignore_index=True)
    daily = pd.concat([cost_daily, loo_daily], ignore_index=True, sort=False)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()

    comparison_rows = []
    for scope in ("real", "model"):
        for cost_bp in COST_BPS:
            rows = {
                tag: full[full.candidate.eq(f"{scope}_{tag}_profit3x_cost{cost_bp}bp")].iloc[0]
                for tag in ("noseller", "q1", "qd05")
            }
            exp = exposure[
                exposure.scope.eq(scope) & exposure.cost_bp.eq(cost_bp)
                & exposure.quantity_mode.eq("q_delta05")
            ].iloc[0]
            comparison_rows.append({
                "scope": scope,
                "cost_bp_one_way_all_puts": cost_bp,
                "noseller_ann_return": rows["noseller"].ann_return,
                "q1_ann_return": rows["q1"].ann_return,
                "qd05_ann_return": rows["qd05"].ann_return,
                "qd05_minus_noseller_ann_pp": 100.0 * (rows["qd05"].ann_return - rows["noseller"].ann_return),
                "qd05_minus_q1_ann_pp": 100.0 * (rows["qd05"].ann_return - rows["q1"].ann_return),
                "noseller_sharpe": rows["noseller"].sharpe_repo,
                "q1_sharpe": rows["q1"].sharpe_repo,
                "qd05_sharpe": rows["qd05"].sharpe_repo,
                "qd05_minus_noseller_sharpe": rows["qd05"].sharpe_repo - rows["noseller"].sharpe_repo,
                "noseller_max_dd": rows["noseller"].max_dd,
                "q1_max_dd": rows["q1"].max_dd,
                "qd05_max_dd": rows["qd05"].max_dd,
                "qd05_mdd_abs_worsening_vs_noseller_pp": 100.0 * (abs(rows["qd05"].max_dd) - abs(rows["noseller"].max_dd)),
                "route_cycles": int(exp.cycles),
                "min_cash_weight": exp.min_cash_weight,
                "negative_cash_days": int(exp.negative_cash_days),
            })
    comparison = pd.DataFrame(comparison_rows)

    loo_rows = []
    for row in concentration.itertuples(index=False):
        metric = full[full.candidate.eq(row.loo_candidate)].iloc[0]
        loo_rows.append({
            **row._asdict(), "loo_ann_return": float(metric.ann_return),
            "loo_sharpe": float(metric.sharpe_repo),
            "loo_max_dd": float(metric.max_dd),
        })
    loo = pd.DataFrame(loo_rows)
    gates = []
    for scope in ("real", "model"):
        c10 = comparison[
            comparison.scope.eq(scope) & comparison.cost_bp_one_way_all_puts.eq(10)
        ].iloc[0]
        no5 = comparison[
            comparison.scope.eq(scope) & comparison.cost_bp_one_way_all_puts.eq(5)
        ].iloc[0]
        l = loo[loo.scope.eq(scope)]
        cost10_gate = bool(
            c10.qd05_minus_noseller_ann_pp >= -1e-10
            and c10.qd05_minus_noseller_sharpe >= -0.05 - 1e-12
            and c10.qd05_mdd_abs_worsening_vs_noseller_pp <= 1.0 + 1e-10
            and c10.negative_cash_days == 0
        )
        loo_gate = bool(l.loo_ann_return.min() >= no5.noseller_ann_return - 1e-12)
        concentration_gate = bool(l.absolute_contribution_share.max() <= 0.50 + 1e-12)
        gates.append({
            "scope": scope, "cost10_gate": cost10_gate,
            "worst_loo_ann_return": float(l.loo_ann_return.min()),
            "noseller_5bp_ann_return": float(no5.noseller_ann_return),
            "loo_gate": loo_gate,
            "max_absolute_cycle_contribution_share": float(l.absolute_contribution_share.max()),
            "concentration_gate": concentration_gate,
        })
    gate_frame = pd.DataFrame(gates)
    passed = bool(gate_frame[["cost10_gate", "loo_gate", "concentration_gate"]].all().all())
    decision = (
        "retain_profit3x_qdelta05_after_cost_cycle_robustness_awaiting_user_confirmation"
        if passed else
        "profit3x_qdelta05_fails_cost_or_cycle_robustness_awaiting_user_confirmation"
    )
    stability = (
        "cost10_and_leave_one_cycle_out_cross_layer_passed"
        if passed else "candidate_sensitive_to_cost_or_single_cycle"
    )

    annual = (
        cost_daily.assign(year=cost_daily.date.dt.year)
        .groupby(["candidate", "scope", "variant", "cost_bp", "year"], as_index=False)
        .agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    )
    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    cost_daily.to_csv(out / "cost_daily.csv.gz", index=False, compression="gzip")
    loo_daily.to_csv(out / "loo_daily.csv.gz", index=False, compression="gzip")
    exposure.to_csv(out / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(out / "cycle_contribution.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "cost_comparison.csv", index=False, encoding="utf-8-sig")
    loo.to_csv(RUN / "leave_one_cycle_out.csv", index=False, encoding="utf-8-sig")
    gate_frame.to_csv(RUN / "robustness_gates.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(
        short_source + "\n\n" + profit_source, encoding="utf-8"
    )

    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "profit3x_qdelta05_all_put_cost_5_10_20bp_and_cycle_leave_one_out",
        "baseline": {"no_short_put": "profit3x at same all-Put cost", "quantity_baseline": "q1_notional", "layer9_5bp_parity_max_abs": parity},
        "candidate_grid": [{"all_510500_put_one_way_bp": x, "seller_quantity": ["q1_notional", "q_delta05"]} for x in COST_BPS],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"]},
        "cost_model": {"all_510500_put_one_way_bp": list(COST_BPS), "ic_one_way_bp": 1, "cash_annual": 0.03, "futures_buffer_per_1x": 0.30, "rerun_state_machine_each_cost": True},
        "fixed_path": "core profit3x; seller absolute IV>30%, no MOM120, M+1 95%, qdelta05, instant valuation, naked, hold to expiry",
        "leave_one_cycle_out_definition": "entry date through trading day before next entry; replace full block with same-cost no-seller profit3x return",
        "cost_comparison": comparison.to_dict("records"),
        "robustness_gates": gates,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "layer9_engine": sha256(ROOT / "research_ic_put_rebuild_layer9_short_quantity_delta_v1.py"), "layer9_daily": sha256(LAYER9 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "cost_daily": str(out / "cost_daily.csv.gz"), "loo_daily": str(out / "loo_daily.csv.gz"), "exposure": str(out / "exposure_audit.csv"), "cycle_contribution": str(out / "cycle_contribution.csv"), "cost_comparison": str(RUN / "cost_comparison.csv"), "leave_one_cycle_out": str(RUN / "leave_one_cycle_out.csv"), "robustness_gates": str(RUN / "robustness_gates.csv"), "annual": str(RUN / "annual_attribution.csv")},
        "warnings": ["Research only; no production, email, ledger, registry or order change.", "Real listed history and seller cycles are sparse.", "Model options are theoretical proxy.", "Cost stress fully reruns state because recovery dates may change.", "No dynamic margin, forced liquidation, tax, integer sizing or explicit bid-ask impact.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = "\n".join([
        "# IC Put 污染后重建：第十层成本与逐周期集中度", "",
        "## Run Metadata", "", "研究专用；按用户确认固定核心Put三倍兑现；完成后等待确认；未修改生产。", "",
        "## Research Question", "", "检验三倍兑现＋Delta 0.5卖Put在所有510500 Put单边5/10/20bp和逐周期留一压力下的稳健性；q1与无卖Put为对照。", "",
        "## Implementation Anchor", "", f"入口：`{Path(__file__).name}`；5bp q1/qd05与第九层逐日误差：{parity}。", "",
        "## Cost Comparison", "", comparison.to_markdown(index=False, floatfmt=".6f"), "",
        "## Robustness Gates", "", gate_frame.to_markdown(index=False, floatfmt=".6f"), "",
        "## Leave-One-Cycle-Out", "", loo.to_markdown(index=False, floatfmt=".6f"), "",
        "## Stability Classification", "", f"`{stability}`。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第十一层。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(gate_frame.to_string(index=False))


if __name__ == "__main__":
    main()
