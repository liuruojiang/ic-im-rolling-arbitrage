"""Final IC v1.3-to-v1.4 joint promotion gate."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior
import research_ic_v13_decay50_vs60_cost_cycle_final_v1 as robustness
import research_ic_v13_momentum_short95_full_joint_v1 as momentum_test


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_to_v1_4_final_joint_upgrade_gate_redteam_v3"
SPEC = ROOT / "docs" / "ic_v14_redteam_correction_rerun_v1_spec.md"
DECAY = 0.50
IV = 0.375
REFERENCE_BASE = prior.RUN
REFERENCE_FINAL = robustness.RUN
REFERENCE_MOMENTUM = momentum_test.RUN
VARIANTS = {
    "current_v13": (False, None),
    "profit3x_only": (False, 3.0),
    "fixed_short95_only": (True, None),
    "final_joint": (True, 3.0),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def metric_row(block: pd.DataFrame, candidate: str, segment: str) -> pd.Series:
    hit = block[(block.candidate.eq(candidate)) & (block.segment.eq(segment))]
    if len(hit) != 1:
        raise RuntimeError(f"Missing metric row: {candidate} {segment}")
    return hit.iloc[0]


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

    old_variants = prior.VARIANTS
    old_maturity_iv = prior.maturity.IV_THRESHOLD
    old_seller_iv = prior.seller_state.IV_THRESHOLD
    try:
        prior.VARIANTS = VARIANTS
        prior.maturity.IV_THRESHOLD = IV
        prior.seller_state.IV_THRESHOLD = IV

        def real_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY):
            return real_short(admission, DECAY, maturity, option_one_way)

        def model_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY):
            return model_short(admission, DECAY, maturity, option_one_way)

        results = {
            scope: prior.run_layer(
                scope, path, futures, market, weights, selected, grid,
                real_decay, model_decay, model_profit, real_profit,
            )
            for scope in ("real", "model")
        }
    finally:
        prior.VARIANTS = old_variants
        prior.maturity.IV_THRESHOLD = old_maturity_iv
        prior.seller_state.IV_THRESHOLD = old_seller_iv

    daily_parts = []
    for scope in ("real", "model"):
        part = results[scope][0].copy()
        part["candidate"] = part.apply(lambda row: f"{scope}_{row['variant']}", axis=1)
        daily_parts.append(part)
    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    audits = {x: results[x][5] for x in ("real", "model")}

    base_reference = pd.read_csv(REFERENCE_BASE / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    final_reference = pd.read_csv(REFERENCE_FINAL / "daily_outputs" / "cost_daily.csv.gz", parse_dates=["date"])
    parity = {}
    for scope in ("real", "model"):
        mappings = {
            "current_v13": (base_reference, f"{scope}_baseline"),
            "profit3x_only": (base_reference, f"{scope}_profit3x_only"),
        }
        parity[scope] = {}
        for candidate, (reference, old_name) in mappings.items():
            current = daily[daily.candidate.eq(f"{scope}_{candidate}")].sort_values("date")
            old = reference[reference.candidate.eq(old_name)].sort_values("date")
            error = float(np.max(np.abs(current.return_net.to_numpy() - old.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"Final IC parity failed {scope} {candidate}: {error}")
            parity[scope][candidate] = error

    summary, wide, unavailable = prior.router_base.summarize(daily)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]
    gates = []
    all_pass = True
    for scope in ("real", "model"):
        for segment in ("full", "last_3y", "last_1y"):
            current = metric_row(summary, f"{scope}_current_v13", segment)
            profit = metric_row(summary, f"{scope}_profit3x_only", segment)
            short = metric_row(summary, f"{scope}_fixed_short95_only", segment)
            joint = metric_row(summary, f"{scope}_final_joint", segment)
            if segment == "full":
                baseline_gate = bool(
                    joint.ann_return >= current.ann_return - 1e-12
                    and joint.sharpe_repo >= current.sharpe_repo - 1e-12
                    and abs(joint.max_dd) - abs(current.max_dd) <= 0.005 + 1e-12
                )
                best_single_ann = max(float(profit.ann_return), float(short.ann_return))
                best_single_sharpe = max(float(profit.sharpe_repo), float(short.sharpe_repo))
                worst_single_dd = max(abs(float(profit.max_dd)), abs(float(short.max_dd)))
                single_gate = bool(
                    joint.ann_return >= best_single_ann - 1e-12
                    and joint.sharpe_repo >= best_single_sharpe - 0.05 - 1e-12
                    and abs(joint.max_dd) - worst_single_dd <= 0.01 + 1e-12
                )
            elif segment == "last_3y":
                baseline_gate = bool(
                    joint.ann_return >= current.ann_return - 1e-12
                    and joint.sharpe_repo >= current.sharpe_repo - 1e-12
                    and abs(joint.max_dd) - abs(current.max_dd) <= 0.005 + 1e-12
                )
                single_gate = True
            else:
                baseline_gate = bool(
                    joint.sharpe_repo >= current.sharpe_repo - 0.05 - 1e-12
                    and abs(joint.max_dd) - abs(current.max_dd) <= 0.005 + 1e-12
                )
                single_gate = True
            gate = baseline_gate and single_gate
            all_pass = all_pass and gate
            gates.append({
                "scope": scope, "segment": segment,
                "current_ann_return": float(current.ann_return),
                "joint_ann_return": float(joint.ann_return),
                "joint_minus_current_ann_pp": 100.0 * float(joint.ann_return - current.ann_return),
                "current_sharpe": float(current.sharpe_repo),
                "joint_sharpe": float(joint.sharpe_repo),
                "joint_minus_current_sharpe": float(joint.sharpe_repo - current.sharpe_repo),
                "current_max_dd": float(current.max_dd), "joint_max_dd": float(joint.max_dd),
                "joint_mdd_abs_worsening_pp": 100.0 * float(abs(joint.max_dd) - abs(current.max_dd)),
                "baseline_gate": baseline_gate, "best_single_gate": single_gate, "gate": gate,
            })
    gates = pd.DataFrame(gates)

    integrity_rows = []
    for scope in ("real", "model"):
        joint_audit = audits[scope]["variants"]["final_joint"]
        integrity_rows.append({
            "scope": scope,
            "max_reference_parity_error": max(parity[scope].values()),
            "min_cash_weight": joint_audit["min_cash_weight"],
            "duplicate_route_profit_days": joint_audit["duplicate_route_profit_days"],
            "momentum_short_put_events": 0,
            "route_cycles": int(len(results[scope][4])),
            "core_profit_restrikes": joint_audit["profit_restrikes"],
        })
    integrity = pd.DataFrame(integrity_rows)
    integrity_pass = bool(
        integrity.max_reference_parity_error.le(1e-12).all()
        and integrity.min_cash_weight.ge(-1e-12).all()
        and integrity.duplicate_route_profit_days.eq(0).all()
        and integrity.momentum_short_put_events.eq(0).all()
    )
    all_pass = all_pass and integrity_pass

    # The former decay/cycle robustness artifact inherited the sample-end
    # pseudo-expiry defect.  It is deliberately invalidated rather than
    # carried forward into the corrected decision.
    cost_selection_pass = False
    common_concentration_pass = False
    old_concentration = pd.DataFrame(
        [{
            "status": "invalidated_by_sample_end_expiry_correction",
            "selection_pass": False,
            "common_concentration_gate": False,
        }]
    )
    decision = (
        "ic_v14_final_joint_performance_gate_passed_upgrade_candidate_pending_user_approval"
        if all_pass and cost_selection_pass
        else "ic_v14_final_joint_gate_failed_keep_v13"
    )
    stability = (
        "performance_and_cost_gates_passed_theoretical_cycle_concentration_limit_remains"
        if all_pass and cost_selection_pass and not common_concentration_pass
        else "all_registered_gates_passed" if all_pass and cost_selection_pass
        else "final_joint_gate_failed"
    )

    annual = daily.assign(year=daily.date.dt.year).groupby(
        ["candidate", "scope", "variant", "year"], as_index=False
    ).agg(annual_return=("return_net", lambda x: float((1.0 + x).prod() - 1.0)))
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(out / "trades.csv", index=False)
    signals.to_csv(out / "signals.csv", index=False)
    events.to_csv(out / "router_events.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    gates.to_csv(RUN / "promotion_gates.csv", index=False, encoding="utf-8-sig")
    integrity.to_csv(RUN / "integrity_checks.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_attribution.csv", index=False, encoding="utf-8-sig")
    old_concentration.to_csv(RUN / "referenced_cycle_robustness.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    meta.update({
        "phase": "complete", "scan_type": "IC_v1_3_to_v1_4_final_joint_upgrade_gate",
        "baseline": {"candidate": "current_v13", "reference_run": str(REFERENCE_BASE),
                     "daily_parity": parity, "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [{"variant": x} for x in VARIANTS],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 listed 510500 options",
                          "model": "2015-04-16..2026-08-14 theoretical extension"},
        "cost_model": {"all_510500_put_one_way_bp": 5, "IC_one_way_bp": 1,
                       "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "final_policy": {"maturity": "M+1", "iv_threshold": IV,
                         "premium_decay": DECAY, "quantity": "q1 equal notional",
                         "core_put_profit_multiple": 3.0, "momentum_short_put": "excluded",
                         "call": "excluded"},
        "promotion_gate_passed": all_pass, "cost_selection_gate_passed": cost_selection_pass,
        "theoretical_cycle_concentration_gate_passed": common_concentration_pass,
        "audit": audits, "promotion_gates": gates.to_dict("records"),
        "integrity_checks": integrity.to_dict("records"),
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"),
                    "trades": str(out / "trades.csv"), "signals": str(out / "signals.csv"),
                    "events": str(out / "router_events.csv"), "cycles": str(out / "cycles.csv"),
                    "gates": str(RUN / "promotion_gates.csv"),
                    "integrity": str(RUN / "integrity_checks.csv"),
                    "annual": str(RUN / "annual_attribution.csv"),
                    "cycle_robustness": str(RUN / "referenced_cycle_robustness.csv"),
                    "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC),
                          "baseline_daily": sha(REFERENCE_BASE / "daily_outputs" / "daily.csv.gz"),
                          "invalidated_final_reference_daily": sha(REFERENCE_FINAL / "daily_outputs" / "cost_daily.csv.gz"),
                          "momentum_rejection_record": sha(REFERENCE_MOMENTUM / "record.md"),
                          "quarter": sha(prior.QUARTER), "grid": sha(prior.GRID)},
        "warnings": ["Research-only; no production, digest, ledger, or trading changes.",
                     "Model 510500 Put is theoretical.",
                     "The prior decay/cycle robustness result is invalid after correcting sample-end pseudo-expiry and is not carried forward.",
                     "No dynamic margin, forced liquidation, tax, capacity, integer sizing, or explicit fill delay."],
        "decision": decision, "stability_label": stability,
        "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    record = (
        "# IC v1.3 → v1.4最终联合升级闸门\n\n"
        "## Data Snapshot\n\n"
        "真实挂牌510500期权层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Promotion Gates\n\n" + gates.to_markdown(index=False) +
        "\n\n## Integrity\n\n" + integrity.to_markdown(index=False) +
        "\n\n## Prior Robustness Carry-forward\n\n"
        f"- Cost and relative decay selection gate: {cost_selection_pass}.\n"
        f"- Theoretical cycle concentration gate: {common_concentration_pass}.\n"
        "- The concentration limit remains a disclosed residual risk; it is not repaired by this replay.\n"
        "\n## Decision\n\n" + decision +
        "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(gates.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
