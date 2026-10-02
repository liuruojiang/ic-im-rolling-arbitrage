"""Cost and leave-one-cycle-out robustness for IC joint3x q1-notional candidate."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_joint3x_q1_notional_all_510500_put_cost_and_short_put_cycle_concentration_robustness_5bp_10bp_20bp_leave_one_cycle_out"
SPEC = ROOT / "docs" / "ic_v13_joint3x_q1_cost_cycle_robustness_v1_spec.md"
REFERENCE = prior.RUN
COST_BPS = (5, 10, 20)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def run_cost(cost_bp: int, path: pd.DataFrame, futures: pd.DataFrame,
             market: pd.DataFrame, weights: pd.Series, selected: pd.DataFrame,
             grid: pd.DataFrame, real_short, model_short, model_profit, real_profit):
    prior.OPTION_ONE_WAY = cost_bp / 10000.0
    prior.PUT_COST_MULTIPLIER = float(cost_bp)
    result = {
        scope: prior.run_layer(
            scope, path, futures, market, weights, selected, grid,
            real_short, model_short, model_profit, real_profit,
        )
        for scope in ("real", "model")
    }
    daily_parts = []
    for scope in ("real", "model"):
        daily = result[scope][0]
        for variant, out_variant in (("profit3x_only", "noseller"), ("joint3x", "joint3x_q1")):
            part = daily[daily.variant.eq(variant)].copy()
            part["candidate"] = f"{scope}_{out_variant}_cost{cost_bp}bp"
            part["variant"] = out_variant
            part["cost_bp"] = cost_bp
            part["stress_type"] = "cost"
            daily_parts.append(part)
    return pd.concat(daily_parts, ignore_index=True), result


def leave_one_cycle_out(scope: str, joint: pd.DataFrame, no_seller: pd.DataFrame,
                        cycles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    joint = joint.sort_values("date").reset_index(drop=True)
    no_seller = no_seller.sort_values("date").reset_index(drop=True)
    if not joint.date.equals(no_seller.date):
        raise RuntimeError(f"LOO date mismatch {scope}")
    starts = sorted(pd.to_datetime(cycles.entry_date.unique()))
    if not starts:
        raise RuntimeError(f"No cycles for {scope}")
    all_dates = pd.DatetimeIndex(joint.date)
    daily_parts, rows = [], []
    log_increment_total = float(np.log1p(joint.return_net).sum() - np.log1p(no_seller.return_net).sum())
    raw = []
    for i, start in enumerate(starts):
        next_start = starts[i + 1] if i + 1 < len(starts) else None
        mask = (all_dates >= start) if next_start is None else ((all_dates >= start) & (all_dates < next_start))
        if not mask.any():
            raise RuntimeError(f"Empty LOO block {scope} {i}")
        block_increment = float(
            np.log1p(joint.loc[mask, "return_net"]).sum()
            - np.log1p(no_seller.loc[mask, "return_net"]).sum()
        )
        raw.append((i, start, all_dates[mask][-1], mask, block_increment))
    absolute_total = sum(abs(item[4]) for item in raw)
    for i, start, end, mask, block_increment in raw:
        candidate = joint.copy()
        candidate.loc[mask, "return_net"] = no_seller.loc[mask, "return_net"].to_numpy()
        candidate["candidate"] = f"{scope}_joint3x_q1_cost5bp_loo_cycle{i:02d}"
        candidate["variant"] = "joint3x_q1_loo"
        candidate["cost_bp"] = 5
        candidate["stress_type"] = "leave_one_cycle_out"
        candidate["nav"] = (1.0 + candidate.return_net).cumprod()
        daily_parts.append(candidate)
        rows.append({
            "scope": scope, "cycle_id": i, "block_start": start, "block_end": end,
            "block_rows": int(mask.sum()), "incremental_log_return": block_increment,
            "absolute_contribution_share": abs(block_increment) / absolute_total if absolute_total else 0.0,
            "total_incremental_log_return": log_increment_total,
            "loo_candidate": candidate.candidate.iloc[0],
        })
    return pd.concat(daily_parts, ignore_index=True), pd.DataFrame(rows)


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    checkpoint = pd.read_csv(prior.QUARTER, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    formal_parity = prior.official_parity(checkpoint)
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()

    original_option_cost = prior.OPTION_ONE_WAY
    original_put_multiplier = prior.PUT_COST_MULTIPLIER
    cost_daily_parts, cost_results = [], {}
    try:
        for cost_bp in COST_BPS:
            daily, result = run_cost(
                cost_bp, path, futures, market, weights, selected, grid,
                real_short, model_short, model_profit, real_profit,
            )
            cost_daily_parts.append(daily)
            cost_results[cost_bp] = result
    finally:
        prior.OPTION_ONE_WAY = original_option_cost
        prior.PUT_COST_MULTIPLIER = original_put_multiplier

    cost_daily = pd.concat(cost_daily_parts, ignore_index=True)
    reference = pd.read_csv(REFERENCE / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity = {}
    for scope in ("real", "model"):
        for current, old in (("joint3x_q1", "joint3x"), ("noseller", "profit3x_only")):
            candidate = cost_daily[
                cost_daily.candidate.eq(f"{scope}_{current}_cost5bp")
            ].sort_values("date")
            ref = reference[reference.candidate.eq(f"{scope}_{old}")].sort_values("date")
            error = float(np.max(np.abs(candidate.return_net.to_numpy() - ref.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"5BP parity failed {scope} {current}: {error}")
            parity[f"{scope}_{current}"] = error

    loo_daily_parts, concentration_parts = [], []
    for scope in ("real", "model"):
        joint = cost_daily[cost_daily.candidate.eq(f"{scope}_joint3x_q1_cost5bp")]
        no_seller = cost_daily[cost_daily.candidate.eq(f"{scope}_noseller_cost5bp")]
        cycles = cost_results[5][scope][4]
        loo_daily, concentration = leave_one_cycle_out(scope, joint, no_seller, cycles)
        loo_daily_parts.append(loo_daily)
        concentration_parts.append(concentration)
    loo_daily = pd.concat(loo_daily_parts, ignore_index=True)
    concentration = pd.concat(concentration_parts, ignore_index=True)
    daily = pd.concat([cost_daily, loo_daily], ignore_index=True)

    summary, wide, unavailable = prior.router_base.summarize(daily)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]
    full = summary[summary.segment.eq("full")].copy()
    comparison_rows = []
    for scope in ("real", "model"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        for cost_bp in COST_BPS:
            no_seller = block.loc[f"{scope}_noseller_cost{cost_bp}bp"]
            joint = block.loc[f"{scope}_joint3x_q1_cost{cost_bp}bp"]
            comparison_rows.append({
                "scope": scope, "cost_bp_one_way_all_puts": cost_bp,
                "noseller_ann_return": float(no_seller.ann_return),
                "joint_ann_return": float(joint.ann_return),
                "joint_minus_noseller_ann_pp": 100.0 * float(joint.ann_return - no_seller.ann_return),
                "noseller_sharpe": float(no_seller.sharpe_repo),
                "joint_sharpe": float(joint.sharpe_repo),
                "joint_minus_noseller_sharpe": float(joint.sharpe_repo - no_seller.sharpe_repo),
                "noseller_max_dd": float(no_seller.max_dd),
                "joint_max_dd": float(joint.max_dd),
                "joint_mdd_abs_worsening_pp": 100.0 * float(abs(joint.max_dd) - abs(no_seller.max_dd)),
                "route_cycles": int(len(cost_results[cost_bp][scope][4])),
            })
    comparison = pd.DataFrame(comparison_rows)

    loo_rows = []
    for row in concentration.itertuples(index=False):
        metric = full[full.candidate.eq(row.loo_candidate)].iloc[0]
        loo_rows.append({
            **row._asdict(), "loo_ann_return": float(metric.ann_return),
            "loo_sharpe": float(metric.sharpe_repo), "loo_max_dd": float(metric.max_dd),
        })
    loo = pd.DataFrame(loo_rows)
    gates = []
    for scope in ("real", "model"):
        c10 = comparison[(comparison.scope.eq(scope)) & (comparison.cost_bp_one_way_all_puts.eq(10))].iloc[0]
        no5 = comparison[(comparison.scope.eq(scope)) & (comparison.cost_bp_one_way_all_puts.eq(5))].iloc[0]
        l = loo[loo.scope.eq(scope)]
        cost10_gate = bool(
            c10.joint_minus_noseller_ann_pp >= -1e-10
            and c10.joint_minus_noseller_sharpe >= -0.05 - 1e-12
            and c10.joint_mdd_abs_worsening_pp <= 1.0 + 1e-10
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
        "retain_joint3x_q1_after_cost_cycle_robustness_no_production_change"
        if passed else "joint3x_q1_fails_cost_or_cycle_robustness_no_production_change"
    )
    stability = (
        "cost10_and_leave_one_cycle_out_cross_layer_passed_real_events_sparse"
        if passed else "candidate_sensitive_to_cost_or_single_cycle"
    )
    annual = (
        cost_daily.assign(year=cost_daily.date.dt.year)
        .groupby(["candidate", "scope", "variant", "cost_bp", "year"], as_index=False)
        .agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    )

    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    cost_daily.to_csv(out / "cost_daily.csv.gz", index=False, compression="gzip")
    loo_daily.to_csv(out / "loo_daily.csv.gz", index=False, compression="gzip")
    concentration.to_csv(out / "cycle_contribution.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "cost_comparison.csv", index=False, encoding="utf-8-sig")
    loo.to_csv(RUN / "leave_one_cycle_out.csv", index=False, encoding="utf-8-sig")
    gate_frame.to_csv(RUN / "robustness_gates.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    meta.update({
        "phase": "complete", "scan_type": "IC_joint3x_q1_all_put_cost_and_cycle_concentration_robustness",
        "baseline": {"no_short_put": "profit3x_only at the same cost", "reference_run": str(REFERENCE),
                     "five_bp_daily_parity": parity, "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [{"all_510500_put_one_way_bp": x} for x in COST_BPS],
        "data_snapshot": {"real_start": "2022-09-19", "real_end": "2026-08-14",
                          "model_start": "2015-04-16", "model_end": "2026-08-14"},
        "cost_model": {"all_510500_put_one_way_bp": list(COST_BPS), "IC_one_way_bp": 1,
                       "cash_annual": 0.03, "futures_buffer_per_1x": 0.30,
                       "rerun_state_machine_each_cost": True},
        "leave_one_cycle_out_definition": "entry date through trading day before next entry; replace full block with same-cost no-short-Put return",
        "cost_comparison": comparison.to_dict("records"), "robustness_gates": gates,
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"),
                    "cost_daily": str(out / "cost_daily.csv.gz"), "loo_daily": str(out / "loo_daily.csv.gz"),
                    "cycle_contribution": str(out / "cycle_contribution.csv"),
                    "cost_comparison": str(RUN / "cost_comparison.csv"),
                    "leave_one_cycle_out": str(RUN / "leave_one_cycle_out.csv"),
                    "robustness_gates": str(RUN / "robustness_gates.csv"),
                    "annual": str(RUN / "annual_attribution.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC),
                          "reference_daily": sha(REFERENCE / "daily_outputs" / "daily.csv.gz")},
        "warnings": ["Research-only counterfactual; production unchanged.",
                     "Model 510500 Put is theoretical before listed history.",
                     "Real short-Put cycles are sparse.",
                     "Cost stress changes recovery exit dates and is therefore fully rerun, not a static deduction.",
                     "No dynamic margin, forced liquidation, tax, integer sizing, or explicit one-day fill delay."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IC v1.3等名义卖Put＋核心Put三倍兑现稳健性压力\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Cost Comparison\n\n" + comparison.to_markdown(index=False) +
        "\n\n## Robustness Gates\n\n" + gate_frame.to_markdown(index=False) +
        "\n\n## Leave-One-Cycle-Out\n\n" + loo.to_markdown(index=False) +
        "\n\n## Full Window Results\n\n详见scan_summary.csv与window_metrics.csv。\n\n"
        "## Decision\n\n" + decision + "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(gate_frame.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
