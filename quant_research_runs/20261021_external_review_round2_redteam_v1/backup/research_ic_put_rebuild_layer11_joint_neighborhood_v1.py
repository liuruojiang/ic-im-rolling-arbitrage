"""Rebuild IC Put research, layer 11: joint IV and quantity neighborhood."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_put_rebuild_layer9_short_quantity_delta_v1 as layer9
import research_ic_put_rebuild_layer10_cost_cycle_robustness_v1 as layer10
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l11_3x_joint_neighborhood_iv275_300_325_x_q1_qd05"
LAYER10 = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l10_3x_qd05_robustness_put_cost_5_10_20bp_and_cycle_loo"
SPEC = ROOT / "docs" / "ic_put_rebuild_layer11_joint_neighborhood_v1_spec.md"
IVS = (0.275, 0.300, 0.325)
MODES = ("q1_notional", "q_delta05")
CENTER = (0.300, "q_delta05")
AXIS_NEIGHBORS = {
    (0.275, "q_delta05"),
    (0.325, "q_delta05"),
    (0.300, "q1_notional"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iv_tag(iv: float) -> str:
    return f"iv{int(round(iv * 1000)):03d}"


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-11 run")

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
            scope, path, futures, weights, selected, grid, frames,
            option_market, model_market, corrected_real,
        )
        for scope in ("real", "model")
    }
    prepared["model"]["base_signal"] = prior.maturity.maturity_model_signals(
        prepared["model"]["active"], model_market, "m1"
    )

    daily_parts: list[pd.DataFrame] = []
    exposure_parts: list[pd.DataFrame] = []
    old_threshold = layer9.IV_THRESHOLD
    try:
        for iv in IVS:
            layer9.IV_THRESHOLD = iv
            for scope in ("real", "model"):
                result = layer9.run_scope(
                    scope, prepared[scope], futures, weights, option_market,
                    model_market, real_short, model_short, model_profit, real_profit,
                )
                source = result[0]
                for mode in MODES:
                    part = source[source.candidate.eq(
                        f"{scope}_m1_iv300_nomom_hold_coreprofit_profit3x_{mode}"
                    )].copy()
                    tag = "q1" if mode == "q1_notional" else "qd05"
                    part["candidate"] = f"{scope}_{iv_tag(iv)}_{tag}_profit3x"
                    part["variant"] = f"{iv_tag(iv)}_{tag}_profit3x"
                    part["iv_threshold"] = iv
                    part["quantity_mode"] = mode
                    daily_parts.append(part)
                exp = result[6]
                exp = exp[
                    exp.profit_line.eq("profit3x")
                    & exp.quantity_mode.isin(MODES)
                ].copy()
                exp["iv_threshold"] = iv
                exposure_parts.append(exp)
    finally:
        layer9.IV_THRESHOLD = old_threshold

    for scope in ("real", "model"):
        daily_parts.append(layer10.no_seller_profit3x(
            scope, prepared[scope], weights, option_market,
            model_profit, real_profit, 5,
        ))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    exposure = pd.concat(exposure_parts, ignore_index=True, sort=False)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()

    comparison_rows = []
    for iv in IVS:
        for mode in MODES:
            item: dict[str, object] = {
                "iv_threshold": iv,
                "quantity_mode": mode,
                "is_center": (iv, mode) == CENTER,
                "is_axis_neighbor": (iv, mode) in AXIS_NEIGHBORS,
            }
            cross_scope_pass = True
            for scope in ("real", "model"):
                tag = "q1" if mode == "q1_notional" else "qd05"
                candidate = full[full.candidate.eq(
                    f"{scope}_{iv_tag(iv)}_{tag}_profit3x"
                )].iloc[0]
                no = full[full.candidate.eq(
                    f"{scope}_noseller_profit3x_cost5bp"
                )].iloc[0]
                exp = exposure[
                    exposure.scope.eq(scope)
                    & exposure.iv_threshold.eq(iv)
                    & exposure.quantity_mode.eq(mode)
                ].iloc[0]
                ann_pp = 100.0 * (candidate.ann_return - no.ann_return)
                sharpe_diff = candidate.sharpe_repo - no.sharpe_repo
                mdd_worse_pp = 100.0 * (abs(candidate.max_dd) - abs(no.max_dd))
                passed = bool(
                    ann_pp >= -1e-10
                    and sharpe_diff >= -0.05 - 1e-12
                    and mdd_worse_pp <= 1.0 + 1e-10
                    and exp.negative_cash_days == 0
                )
                item.update({
                    f"{scope}_noseller_ann_return": no.ann_return,
                    f"{scope}_candidate_ann_return": candidate.ann_return,
                    f"{scope}_candidate_minus_noseller_ann_pp": ann_pp,
                    f"{scope}_candidate_minus_noseller_sharpe": sharpe_diff,
                    f"{scope}_mdd_abs_worsening_pp": mdd_worse_pp,
                    f"{scope}_cycles": int(exp.cycles),
                    f"{scope}_short_put_days": int(exp.short_put_days),
                    f"{scope}_min_cash_weight": exp.min_cash_weight,
                    f"{scope}_pass": passed,
                })
                cross_scope_pass = cross_scope_pass and passed
            item["cross_scope_pass"] = cross_scope_pass
            comparison_rows.append(item)
    comparison = pd.DataFrame(comparison_rows)
    center_pass = bool(comparison.loc[comparison.is_center, "cross_scope_pass"].iloc[0])
    neighbor_passes = int(comparison.loc[comparison.is_axis_neighbor, "cross_scope_pass"].sum())
    plateau_pass = center_pass and neighbor_passes == len(AXIS_NEIGHBORS)
    decision = (
        "retain_profit3x_qdelta05_iv30_neighborhood_plateau_passed_awaiting_user_confirmation"
        if plateau_pass else
        "do_not_promote_profit3x_qdelta05_iv30_neighborhood_failed_awaiting_user_confirmation"
    )
    stability = (
        "center_and_all_three_axis_neighbors_passed"
        if plateau_pass else
        f"center_{'passed' if center_pass else 'failed'}_axis_neighbors_{neighbor_passes}_of_3_passed"
    )

    reference = pd.read_csv(LAYER10 / "daily_outputs" / "cost_daily.csv.gz", parse_dates=["date"])
    parity: dict[str, float] = {}
    for scope in ("real", "model"):
        for tag in ("q1", "qd05"):
            got = daily[daily.candidate.eq(f"{scope}_iv300_{tag}_profit3x")].sort_values("date")
            old = reference[reference.candidate.eq(
                f"{scope}_{tag}_profit3x_cost5bp"
            )].sort_values("date")
            if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
                raise RuntimeError(f"Layer10 parity dates failed {scope} {tag}")
            error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
            if error > 1e-12:
                raise RuntimeError(f"Layer10 parity returns failed {scope} {tag}: {error}")
            parity[f"{scope}_{tag}"] = error

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    exposure.to_csv(out / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "joint_neighborhood_comparison.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    corrected_real.to_csv(RUN / "corrected_real_signal_m1.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(
        short_source + "\n\n" + profit_source, encoding="utf-8"
    )
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "profit3x_joint_neighborhood_corrected_iv275_300_325_x_q1_qdelta05",
        "baseline": {"definition": "same profit3x full portfolio without short Put", "layer10_iv30_5bp_parity_max_abs": parity},
        "candidate_grid": [{"iv_threshold": iv, "quantity_mode": mode} for iv in IVS for mode in MODES],
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"]},
        "cost_model": {"all_510500_put_one_way_bp": 5, "ic_one_way_bp": 1, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "fixed_policy": {"maturity": "M+1", "premium_decay": None, "core_profit_multiple": 3, "valuation_debounce": "instant", "seller_mom120": False, "catastrophe_put": "none", "call": "excluded"},
        "plateau_gate": {"center": CENTER, "center_pass": center_pass, "axis_neighbors": sorted([list(x) for x in AXIS_NEIGHBORS]), "axis_neighbors_required": 3, "axis_neighbors_passed": neighbor_passes, "passed": plateau_pass},
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "layer9_engine": sha256(ROOT / "research_ic_put_rebuild_layer9_short_quantity_delta_v1.py"), "layer10_cost_daily": sha256(LAYER10 / "daily_outputs" / "cost_daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "exposure": str(out / "exposure_audit.csv"), "comparison": str(RUN / "joint_neighborhood_comparison.csv")},
        "warnings": ["Research only; no production, email, ledger, registry or order change.", "Real listed history and route cycles are sparse.", "Model options are theoretical proxy.", "No dynamic margin, forced liquidation, tax, integer sizing or explicit bid-ask impact.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    center = comparison[comparison.is_center]
    neighbors = comparison[comparison.is_axis_neighbor]
    record = "\n".join([
        "# IC Put 污染后重建：第十一层最终联合参数邻域", "",
        "## Run Metadata", "", "研究专用；固定核心Put三倍兑现、持有到期和5bp；完成后等待确认；未修改生产。", "",
        "## Center", "", center.to_markdown(index=False, floatfmt=".6f"), "",
        "## Axis Neighbors", "", neighbors.to_markdown(index=False, floatfmt=".6f"), "",
        "## Full Factorial Grid", "", comparison.to_markdown(index=False, floatfmt=".6f"), "",
        "## Verification", "", f"IV30 q1/qd05与第十层5bp逐日误差：{parity}。", "",
        "## Stability Classification", "", f"`{stability}`。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入第十二层。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(json.dumps(meta["plateau_gate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
