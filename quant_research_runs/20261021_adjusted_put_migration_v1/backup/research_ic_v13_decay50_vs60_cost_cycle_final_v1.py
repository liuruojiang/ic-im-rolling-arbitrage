"""Final cost and cycle robustness comparison of IC decay50 versus decay60."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_decay50_vs_decay60_final_all_put_cost_and_leave_one_cycle_out"
SPEC = ROOT / "docs" / "ic_v13_decay50_vs60_cost_cycle_final_v1_spec.md"
DECAYS = (0.50, 0.60)
COST_BPS = (5, 10, 20)
IV = 0.375
MULTIPLE = 3.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def leave_one_cycle_out(scope: str, decay: int, joint: pd.DataFrame, no_seller: pd.DataFrame,
                        cycles: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    joint = joint.sort_values("date").reset_index(drop=True)
    no_seller = no_seller.sort_values("date").reset_index(drop=True)
    if not joint.date.equals(no_seller.date):
        raise RuntimeError(f"LOO date mismatch {scope} decay{decay}")
    starts = sorted(pd.to_datetime(cycles.entry_date.unique()))
    if not starts:
        raise RuntimeError(f"No cycles {scope} decay{decay}")
    dates = pd.DatetimeIndex(joint.date)
    raw = []
    for i, start in enumerate(starts):
        end_start = starts[i + 1] if i + 1 < len(starts) else None
        mask = (dates >= start) if end_start is None else ((dates >= start) & (dates < end_start))
        increment = float(np.log1p(joint.loc[mask, "return_net"]).sum() - np.log1p(no_seller.loc[mask, "return_net"]).sum())
        raw.append((i, start, dates[mask][-1], mask, increment))
    absolute_total = sum(abs(x[4]) for x in raw)
    daily_parts, rows = [], []
    for i, start, end, mask, increment in raw:
        candidate = joint.copy()
        candidate.loc[mask, "return_net"] = no_seller.loc[mask, "return_net"].to_numpy()
        name = f"{scope}_decay{decay}_cost5bp_loo_cycle{i:02d}"
        candidate["candidate"] = name
        candidate["stress_type"] = "leave_one_cycle_out"
        candidate["nav"] = (1.0 + candidate.return_net).cumprod()
        daily_parts.append(candidate)
        rows.append({
            "scope": scope, "decay_threshold": decay / 100.0, "cycle_id": i,
            "block_start": start, "block_end": end, "block_rows": int(mask.sum()),
            "incremental_log_return": increment,
            "absolute_contribution_share": abs(increment) / absolute_total if absolute_total else 0.0,
            "loo_candidate": name,
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

    original_variants = prior.VARIANTS
    original_option_cost = prior.OPTION_ONE_WAY
    original_put_multiplier = prior.PUT_COST_MULTIPLIER
    original_maturity_iv = prior.maturity.IV_THRESHOLD
    original_seller_iv = prior.seller_state.IV_THRESHOLD
    cost_parts, results, audit = [], {}, {}
    try:
        prior.VARIANTS = {"noseller": (False, MULTIPLE), "joint": (True, MULTIPLE)}
        prior.maturity.IV_THRESHOLD = IV
        prior.seller_state.IV_THRESHOLD = IV
        for cost_bp in COST_BPS:
            prior.OPTION_ONE_WAY = cost_bp / 10000.0
            prior.PUT_COST_MULTIPLIER = float(cost_bp)
            for decay in DECAYS:
                def real_decay(admission, _ignored, maturity="m1", option_one_way=None, *, _d=decay):
                    return real_short(admission, _d, maturity, prior.OPTION_ONE_WAY if option_one_way is None else option_one_way)

                def model_decay(admission, _ignored, maturity="m1", option_one_way=None, *, _d=decay):
                    return model_short(admission, _d, maturity, prior.OPTION_ONE_WAY if option_one_way is None else option_one_way)

                for scope in ("real", "model"):
                    result = prior.run_layer(
                        scope, path, futures, market, weights, selected, grid,
                        real_decay, model_decay, model_profit, real_profit,
                    )
                    results[(cost_bp, int(decay * 100), scope)] = result
                    for variant in ("noseller", "joint"):
                        part = result[0][result[0].variant.eq(variant)].copy()
                        part["candidate"] = f"{scope}_decay{int(decay * 100)}_{variant}_cost{cost_bp}bp"
                        part["scope"] = scope
                        part["decay_threshold"] = decay
                        part["cost_bp"] = cost_bp
                        part["stress_type"] = "cost"
                        cost_parts.append(part)
                    audit[f"{scope}_decay{int(decay * 100)}_cost{cost_bp}"] = result[5]
    finally:
        prior.VARIANTS = original_variants
        prior.OPTION_ONE_WAY = original_option_cost
        prior.PUT_COST_MULTIPLIER = original_put_multiplier
        prior.maturity.IV_THRESHOLD = original_maturity_iv
        prior.seller_state.IV_THRESHOLD = original_seller_iv

    cost_daily = pd.concat(cost_parts, ignore_index=True)
    loo_parts, concentration_parts = [], []
    for decay in (50, 60):
        for scope in ("real", "model"):
            joint = cost_daily[cost_daily.candidate.eq(f"{scope}_decay{decay}_joint_cost5bp")]
            no = cost_daily[cost_daily.candidate.eq(f"{scope}_decay{decay}_noseller_cost5bp")]
            cycles = results[(5, decay, scope)][4]
            loo_daily, concentration = leave_one_cycle_out(scope, decay, joint, no, cycles)
            loo_parts.append(loo_daily)
            concentration_parts.append(concentration)
    loo_daily = pd.concat(loo_parts, ignore_index=True)
    concentration = pd.concat(concentration_parts, ignore_index=True)
    daily = pd.concat([cost_daily, loo_daily], ignore_index=True)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].set_index("candidate")

    comparisons = []
    for decay in (50, 60):
        for scope in ("real", "model"):
            for cost_bp in COST_BPS:
                no = full.loc[f"{scope}_decay{decay}_noseller_cost{cost_bp}bp"]
                joint = full.loc[f"{scope}_decay{decay}_joint_cost{cost_bp}bp"]
                comparisons.append({
                    "scope": scope, "decay_threshold": decay / 100.0, "cost_bp": cost_bp,
                    "noseller_ann_return": float(no.ann_return), "joint_ann_return": float(joint.ann_return),
                    "joint_minus_noseller_ann_pp": 100.0 * float(joint.ann_return - no.ann_return),
                    "noseller_sharpe": float(no.sharpe_repo), "joint_sharpe": float(joint.sharpe_repo),
                    "joint_minus_noseller_sharpe": float(joint.sharpe_repo - no.sharpe_repo),
                    "noseller_max_dd": float(no.max_dd), "joint_max_dd": float(joint.max_dd),
                    "joint_mdd_abs_worsening_pp": 100.0 * float(abs(joint.max_dd) - abs(no.max_dd)),
                    "route_cycles": int(len(results[(cost_bp, decay, scope)][4])),
                    "early_rolls": int(results[(cost_bp, decay, scope)][4].early_rolls.fillna(0).sum()),
                })
    comparison = pd.DataFrame(comparisons)

    loo_rows = []
    for row in concentration.itertuples(index=False):
        metric = full.loc[row.loo_candidate]
        loo_rows.append({**row._asdict(), "loo_ann_return": float(metric.ann_return),
                         "loo_sharpe": float(metric.sharpe_repo), "loo_max_dd": float(metric.max_dd)})
    loo = pd.DataFrame(loo_rows)
    aggregate = (
        loo.groupby(["scope", "decay_threshold"], as_index=False)
        .agg(worst_loo_ann_return=("loo_ann_return", "min"),
             worst_loo_sharpe=("loo_sharpe", "min"),
             worst_loo_max_dd=("loo_max_dd", "min"),
             max_absolute_cycle_contribution_share=("absolute_contribution_share", "max"),
             cycles=("cycle_id", "size"))
    )

    pair_rows = []
    for scope in ("real", "model"):
        for cost_bp in COST_BPS:
            c50 = comparison[(comparison.scope.eq(scope)) & comparison.decay_threshold.eq(0.50) & comparison.cost_bp.eq(cost_bp)].iloc[0]
            c60 = comparison[(comparison.scope.eq(scope)) & comparison.decay_threshold.eq(0.60) & comparison.cost_bp.eq(cost_bp)].iloc[0]
            pair_rows.append({
                "scope": scope, "cost_bp": cost_bp,
                "decay50_minus_60_ann_pp": 100.0 * float(c50.joint_ann_return - c60.joint_ann_return),
                "decay50_minus_60_sharpe": float(c50.joint_sharpe - c60.joint_sharpe),
                "decay50_mdd_abs_minus_60_pp": 100.0 * float(abs(c50.joint_max_dd) - abs(c60.joint_max_dd)),
                "decay50_minus_60_early_rolls": int(c50.early_rolls - c60.early_rolls),
            })
    pairwise = pd.DataFrame(pair_rows)

    real_pair = pairwise[pairwise.scope.eq("real")]
    real_cost_gate = bool((real_pair.decay50_minus_60_ann_pp.ge(-1e-10) &
                           real_pair.decay50_minus_60_sharpe.ge(-1e-12) &
                           real_pair.decay50_mdd_abs_minus_60_pp.le(0.5 + 1e-10)).all())
    real_agg = aggregate[aggregate.scope.eq("real")].set_index("decay_threshold")
    real_loo_gate = bool(real_agg.loc[0.50, "worst_loo_ann_return"] >= real_agg.loc[0.60, "worst_loo_ann_return"] - 1e-12)
    model10 = pairwise[(pairwise.scope.eq("model")) & pairwise.cost_bp.eq(10)].iloc[0]
    model_tolerance_gate = bool(model10.decay50_minus_60_ann_pp >= -0.1 - 1e-12 and
                                model10.decay50_minus_60_sharpe >= -0.01 - 1e-12 and
                                model10.decay50_mdd_abs_minus_60_pp <= 0.5 + 1e-12)
    cost10_baseline_gate = True
    for scope in ("real", "model"):
        c50 = comparison[(comparison.scope.eq(scope)) & comparison.decay_threshold.eq(0.50) & comparison.cost_bp.eq(10)].iloc[0]
        cost10_baseline_gate = cost10_baseline_gate and bool(
            c50.joint_minus_noseller_ann_pp >= -1e-10 and
            c50.joint_minus_noseller_sharpe >= -0.05 - 1e-12 and
            c50.joint_mdd_abs_worsening_pp <= 1.0 + 1e-10
        )
    selection_pass = real_cost_gate and real_loo_gate and model_tolerance_gate and cost10_baseline_gate
    common_concentration_pass = bool(aggregate.max_absolute_cycle_contribution_share.le(0.50 + 1e-12).all())
    decision = (
        "prefer_decay50_over_decay60_as_IC_research_candidate_common_concentration_limit_no_production_change"
        if selection_pass else
        "retain_decay60_anchor_decay50_comparative_gate_failed_no_production_change"
    )
    stability = (
        "decay50_comparative_gate_passed_but_common_cross_layer_concentration_failed"
        if selection_pass and not common_concentration_pass else
        "decay50_comparative_and_common_concentration_passed" if selection_pass else
        "decay50_comparative_gate_failed"
    )
    gates = pd.DataFrame([{
        "real_all_cost_dominance_gate": real_cost_gate,
        "real_worst_loo_gate": real_loo_gate,
        "model_10bp_tolerance_gate": model_tolerance_gate,
        "decay50_10bp_vs_noseller_cross_scope_gate": cost10_baseline_gate,
        "selection_pass": selection_pass,
        "common_concentration_gate": common_concentration_pass,
    }])

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    cost_daily.to_csv(out / "cost_daily.csv.gz", index=False, compression="gzip")
    loo_daily.to_csv(out / "loo_daily.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "cost_comparison.csv", index=False, encoding="utf-8-sig")
    pairwise.to_csv(RUN / "decay50_vs60_pairwise.csv", index=False, encoding="utf-8-sig")
    loo.to_csv(RUN / "leave_one_cycle_out.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_contribution.csv", index=False, encoding="utf-8-sig")
    aggregate.to_csv(RUN / "cycle_robustness_summary.csv", index=False, encoding="utf-8-sig")
    gates.to_csv(RUN / "selection_gates.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    meta.update({
        "phase": "complete", "scan_type": "IC_decay50_vs60_cost_cycle_final",
        "baseline": {"definition": "same-cost profit3x-only complete IC portfolio", "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [{"premium_decay": d, "all_510500_put_one_way_bp": c} for d in DECAYS for c in COST_BPS],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 listed 510500 options", "model": "2015-04-16..2026-08-14 theoretical extension"},
        "cost_model": {"all_510500_put_one_way_bp": list(COST_BPS), "IC_one_way_bp": 1, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03, "rerun_state_machine_each_cost": True},
        "fixed_policy": {"maturity": "M+1", "iv_threshold": IV, "quantity": "q1 equal notional", "profit_multiple": MULTIPLE, "call": "excluded"},
        "selection_gates": gates.iloc[0].to_dict(), "common_concentration_limit": not common_concentration_pass,
        "audit": audit, "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "cost_daily": str(out / "cost_daily.csv.gz"), "loo_daily": str(out / "loo_daily.csv.gz"), "cost_comparison": str(RUN / "cost_comparison.csv"), "pairwise": str(RUN / "decay50_vs60_pairwise.csv"), "loo": str(RUN / "leave_one_cycle_out.csv"), "concentration": str(RUN / "cycle_contribution.csv"), "cycle_summary": str(RUN / "cycle_robustness_summary.csv"), "gates": str(RUN / "selection_gates.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "quarter": sha(prior.QUARTER), "grid": sha(prior.GRID)},
        "warnings": ["Research-only; production unchanged.", "Model 510500 Put is theoretical and has only three route cycles.", "No dynamic margin, forced liquidation, tax, capacity, integer sizing, or explicit fill delay."],
        "decision": decision, "stability_label": stability,
        "git_status_after": git_status(), "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IC v1.3衰减50%与60%成本及周期最终对决\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Cost Pairwise\n\n" + pairwise.to_markdown(index=False) +
        "\n\n## Cycle Robustness\n\n" + aggregate.to_markdown(index=False) +
        "\n\n## Selection Gates\n\n" + gates.to_markdown(index=False) +
        "\n\n## Decision\n\n" + decision + "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(pairwise.to_string(index=False))
    print(aggregate.to_string(index=False))
    print(gates.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
