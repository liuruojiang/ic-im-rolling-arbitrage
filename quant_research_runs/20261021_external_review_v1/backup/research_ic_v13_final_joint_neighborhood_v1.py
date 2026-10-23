"""Final joint-neighborhood robustness scan for the IC v1.3 short-Put candidate."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_ic_fixed_core_short95_plus_core_put_profit_restrike_final_joint_neighborhood_iv_x_decay_x_profit_multiple"
SPEC = ROOT / "docs" / "ic_v13_final_joint_neighborhood_v1_spec.md"
IVS = (0.35, 0.375, 0.40)
DECAYS = (0.50, 0.60, 0.70)
MULTIPLES = (2.0, 3.0)
CENTER = (0.375, 0.60, 3.0)
AXIS_NEIGHBORS = {
    (0.35, 0.60, 3.0), (0.40, 0.60, 3.0),
    (0.375, 0.50, 3.0), (0.375, 0.70, 3.0),
    (0.375, 0.60, 2.0),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def tag(iv: float, decay: float, multiple: float) -> str:
    return f"iv{int(round(iv * 1000)):03d}_decay{int(round(decay * 100)):02d}_profit{int(multiple)}x"


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
    original_maturity_iv = prior.maturity.IV_THRESHOLD
    original_seller_iv = prior.seller_state.IV_THRESHOLD
    daily_parts = []
    audit = {}
    try:
        for iv in IVS:
            prior.maturity.IV_THRESHOLD = iv
            prior.seller_state.IV_THRESHOLD = iv
            for decay in DECAYS:
                def real_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY, *, _d=decay):
                    return real_short(admission, _d, maturity, option_one_way)

                def model_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY, *, _d=decay):
                    return model_short(admission, _d, maturity, option_one_way)

                for multiple in MULTIPLES:
                    label = tag(iv, decay, multiple)
                    prior.VARIANTS = {"noseller": (False, multiple), "joint": (True, multiple)}
                    for scope in ("real", "model"):
                        result = prior.run_layer(
                            scope, path, futures, market, weights, selected, grid,
                            real_decay, model_decay, model_profit, real_profit,
                        )
                        daily = result[0]
                        for variant in ("noseller", "joint"):
                            part = daily[daily.variant.eq(variant)].copy()
                            part["candidate"] = f"{scope}_{label}_{variant}"
                            part["scope"] = scope
                            part["variant"] = variant
                            part["iv_threshold"] = iv
                            part["decay_threshold"] = decay
                            part["profit_multiple"] = multiple
                            daily_parts.append(part)
                        audit[f"{scope}_{label}"] = {
                            "short_router": result[5]["short_router"],
                            "route_switches": result[5]["route_switches"],
                            "joint": result[5]["variants"]["joint"],
                        }
    finally:
        prior.VARIANTS = original_variants
        prior.maturity.IV_THRESHOLD = original_maturity_iv
        prior.seller_state.IV_THRESHOLD = original_seller_iv

    daily = pd.concat(daily_parts, ignore_index=True)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].set_index("candidate")
    rows = []
    for iv in IVS:
        for decay in DECAYS:
            for multiple in MULTIPLES:
                label = tag(iv, decay, multiple)
                item = {"iv_threshold": iv, "decay_threshold": decay, "profit_multiple": multiple}
                all_scope_pass = True
                for scope in ("real", "model"):
                    no = full.loc[f"{scope}_{label}_noseller"]
                    joint = full.loc[f"{scope}_{label}_joint"]
                    ann = 100.0 * float(joint.ann_return - no.ann_return)
                    sharpe = float(joint.sharpe_repo - no.sharpe_repo)
                    dd = 100.0 * float(abs(joint.max_dd) - abs(no.max_dd))
                    passed = ann >= -1e-10 and sharpe >= -0.05 - 1e-12 and dd <= 1.0 + 1e-10
                    item.update({
                        f"{scope}_noseller_ann_return": float(no.ann_return),
                        f"{scope}_joint_ann_return": float(joint.ann_return),
                        f"{scope}_joint_minus_noseller_ann_pp": ann,
                        f"{scope}_joint_minus_noseller_sharpe": sharpe,
                        f"{scope}_mdd_abs_worsening_pp": dd,
                        f"{scope}_pass": passed,
                    })
                    all_scope_pass = all_scope_pass and passed
                item["cross_scope_pass"] = all_scope_pass
                item["is_center"] = (iv, decay, multiple) == CENTER
                item["is_axis_neighbor"] = (iv, decay, multiple) in AXIS_NEIGHBORS
                rows.append(item)
    comparison = pd.DataFrame(rows)
    center_pass = bool(comparison.loc[comparison.is_center, "cross_scope_pass"].iloc[0])
    neighbor_passes = int(comparison.loc[comparison.is_axis_neighbor, "cross_scope_pass"].sum())
    plateau_pass = center_pass and neighbor_passes >= 4
    decision = (
        "retain_ic_joint_center_as_research_candidate_neighborhood_plateau_passed_no_production_change"
        if plateau_pass else
        "do_not_promote_ic_joint_center_neighborhood_plateau_failed_no_production_change"
    )
    stability = (
        f"center_passed_axis_neighbors_{neighbor_passes}_of_5_passed"
        if plateau_pass else f"center_{'passed' if center_pass else 'failed'}_axis_neighbors_{neighbor_passes}_of_5_passed"
    )

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    comparison.to_csv(RUN / "joint_neighborhood_comparison.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    meta.update({
        "phase": "complete",
        "scan_type": "IC_v1_3_final_joint_neighborhood",
        "baseline": {"definition": "same profit-restrike multiple without short Put", "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [
            {"iv_threshold": iv, "decay_threshold": decay, "profit_multiple": multiple}
            for iv in IVS for decay in DECAYS for multiple in MULTIPLES
        ],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 listed 510500 options", "model": "2015-04-16..2026-08-14 theoretical extension"},
        "cost_model": {"all_510500_put_one_way_bp": 5, "IC_one_way_bp": 1, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "fixed_policy": {"maturity": "M+1", "quantity": "q1 equal notional", "valuation_debounce": "instant", "catastrophe_put": "none", "call": "excluded"},
        "plateau_gate": {"center": CENTER, "center_pass": center_pass, "axis_neighbors_required": 4, "axis_neighbors_passed": neighbor_passes, "passed": plateau_pass},
        "audit": audit,
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "comparison": str(RUN / "joint_neighborhood_comparison.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "quarter": sha(prior.QUARTER), "grid": sha(prior.GRID)},
        "warnings": [
            "Research-only historical counterfactual; production ledgers unchanged.",
            "Model 510500 Put is theoretical and not executable listed history.",
            "Real option history and short-Put cycles are sparse.",
            "No dynamic margin, forced liquidation, tax, capacity, integer sizing, or explicit fill delay.",
        ],
        "decision": decision,
        "stability_label": stability,
        "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    center = comparison[comparison.is_center]
    neighbors = comparison[comparison.is_axis_neighbor]
    record = (
        "# IC v1.3 最终联合邻域稳健性\n\n"
        "## Data Snapshot\n\n真实挂牌层 2022-09-19 至 2026-08-14；理论延展层 2015-04-16 至 2026-08-14，二者分开判定。\n\n"
        "## Center\n\n" + center.to_markdown(index=False) +
        "\n\n## Axis Neighbors\n\n" + neighbors.to_markdown(index=False) +
        "\n\n## Full Factorial Grid\n\n" + comparison.to_markdown(index=False) +
        "\n\n## Decision\n\n" + decision +
        "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
