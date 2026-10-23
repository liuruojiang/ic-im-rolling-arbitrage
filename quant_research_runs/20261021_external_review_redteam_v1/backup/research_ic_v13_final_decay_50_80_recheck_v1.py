"""Recheck IC premium-decay 50%-80% in the current complete joint portfolio."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_final_joint_decay_recheck_premium_decay_50_60_70_80"
SPEC = ROOT / "docs" / "ic_v13_final_decay_50_80_recheck_v1_spec.md"
DECAYS = (0.50, 0.60, 0.70, 0.80)
IV = 0.375
MULTIPLE = 3.0


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


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
    frames, _, _, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, real_chains, _, _, _ = prior.router_base.short.real_inputs()

    original_variants = prior.VARIANTS
    original_maturity_iv = prior.maturity.IV_THRESHOLD
    original_seller_iv = prior.seller_state.IV_THRESHOLD
    daily_parts, isolated_parts, cycle_parts, audit = [], [], [], {}
    try:
        prior.maturity.IV_THRESHOLD = IV
        prior.seller_state.IV_THRESHOLD = IV
        prior.VARIANTS = {"noseller": (False, MULTIPLE), "joint": (True, MULTIPLE)}
        for decay in DECAYS:
            def real_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY, *, _d=decay):
                return real_short(admission, _d, maturity, option_one_way)

            def model_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY, *, _d=decay):
                return model_short(admission, _d, maturity, option_one_way)

            for scope in ("real", "model"):
                result = prior.run_layer(
                    scope, path, futures, market, weights, selected, grid,
                    real_decay, model_decay, model_profit, real_profit,
                )
                for variant in ("noseller", "joint"):
                    part = result[0][result[0].variant.eq(variant)].copy()
                    part["candidate"] = f"{scope}_decay{int(decay * 100)}_{variant}"
                    part["scope"] = scope
                    part["variant"] = variant
                    part["decay_threshold"] = decay
                    daily_parts.append(part)

                active = path[path.date.ge(prior.REAL_START)].reset_index(drop=True) if scope == "real" else path.reset_index(drop=True)
                base_signal = (
                    prior.maturity.maturity_real_signals(active, real_chains, frames["histories"], "m1")
                    if scope == "real" else prior.maturity.maturity_model_signals(active, market, "m1")
                )
                signal = prior.seller_state.signal_variant(
                    base_signal, prior.router_base.current_schedule(), scope,
                    "instant", prior.seller_state.momentum_permission(),
                )
                runner = real_decay if scope == "real" else model_decay
                isolated, _, cycles, short_audit = runner(
                    prior.router_base.entry_series(signal), decay, "m1", prior.OPTION_ONE_WAY,
                )
                isolated = isolated.copy()
                isolated["candidate"] = f"{scope}_decay{int(decay * 100)}_isolated"
                isolated["scope"] = scope
                isolated["decay_threshold"] = decay
                isolated_parts.append(isolated)
                if len(cycles):
                    cycle_parts.append(cycles.assign(scope=scope, decay_threshold=decay))
                audit[f"{scope}_decay{int(decay * 100)}"] = {
                    "full_joint": result[5], "isolated": short_audit,
                }
    finally:
        prior.VARIANTS = original_variants
        prior.maturity.IV_THRESHOLD = original_maturity_iv
        prior.seller_state.IV_THRESHOLD = original_seller_iv

    daily = pd.concat(daily_parts, ignore_index=True)
    isolated = pd.concat(isolated_parts, ignore_index=True)
    cycles = pd.concat(cycle_parts, ignore_index=True)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    isolated_summary, _, isolated_unavailable = prior.router_base.summarize(isolated)
    full = summary[summary.segment.eq("full")].set_index("candidate")
    isolated_full = isolated_summary[isolated_summary.segment.eq("full")].set_index("candidate")

    rows = []
    for decay in DECAYS:
        item = {"decay_threshold": decay}
        both = True
        for scope in ("real", "model"):
            no = full.loc[f"{scope}_decay{int(decay * 100)}_noseller"]
            joint = full.loc[f"{scope}_decay{int(decay * 100)}_joint"]
            iso = isolated_full.loc[f"{scope}_decay{int(decay * 100)}_isolated"]
            ann_diff = 100.0 * float(joint.ann_return - no.ann_return)
            sharpe_diff = float(joint.sharpe_repo - no.sharpe_repo)
            dd_worse = 100.0 * float(abs(joint.max_dd) - abs(no.max_dd))
            passed = ann_diff >= -1e-10 and sharpe_diff >= -0.05 - 1e-12 and dd_worse <= 1.0 + 1e-10
            scope_cycles = cycles[(cycles.scope.eq(scope)) & (cycles.decay_threshold.eq(decay))]
            item.update({
                f"{scope}_joint_ann_return": float(joint.ann_return),
                f"{scope}_joint_sharpe": float(joint.sharpe_repo),
                f"{scope}_joint_max_dd": float(joint.max_dd),
                f"{scope}_joint_minus_noseller_ann_pp": ann_diff,
                f"{scope}_joint_minus_noseller_sharpe": sharpe_diff,
                f"{scope}_mdd_abs_worsening_pp": dd_worse,
                f"{scope}_isolated_ann_return": float(iso.ann_return),
                f"{scope}_isolated_sharpe": float(iso.sharpe_repo),
                f"{scope}_isolated_max_dd": float(iso.max_dd),
                f"{scope}_cycles": int(len(scope_cycles)),
                f"{scope}_early_rolls": int(scope_cycles.early_rolls.fillna(0).sum()),
                f"{scope}_pass": passed,
            })
            both = both and passed
        item["cross_scope_pass"] = both
        rows.append(item)
    comparison = pd.DataFrame(rows)

    passing = comparison[comparison.cross_scope_pass]
    best = {
        "real_joint_cagr": int(round(100 * passing.loc[passing.real_joint_ann_return.idxmax(), "decay_threshold"])),
        "model_joint_cagr": int(round(100 * passing.loc[passing.model_joint_ann_return.idxmax(), "decay_threshold"])),
        "real_joint_sharpe": int(round(100 * passing.loc[passing.real_joint_sharpe.idxmax(), "decay_threshold"])),
        "model_joint_sharpe": int(round(100 * passing.loc[passing.model_joint_sharpe.idxmax(), "decay_threshold"])),
    }
    decay70_dominates = all(value == 70 for value in best.values())
    decision = (
        "promote_decay70_over_decay60_as_research_candidate_no_production_change"
        if decay70_dominates else
        "decay70_not_uniformly_optimal_keep_decay60_anchor_pending_tradeoff_decision_no_production_change"
    )
    stability = "decay70_uniform_winner" if decay70_dominates else "decay_ranking_differs_by_real_model_and_metric"

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    isolated.to_csv(out / "isolated_daily.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "decay_comparison.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    isolated_summary.to_csv(RUN / "isolated_scan_summary.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    meta.update({
        "phase": "complete",
        "scan_type": "IC_v1_3_final_joint_decay_50_80_recheck",
        "baseline": {"candidate": "same 3x core-Put profit restrike without short Put", "frozen_quarter_T3_composition_parity": formal_parity},
        "candidate_grid": [{"premium_decay": x} for x in DECAYS],
        "data_snapshot": {"real": "2022-09-19..2026-08-14 listed 510500 options", "model": "2015-04-16..2026-08-14 theoretical extension"},
        "cost_model": {"all_510500_put_one_way_bp": 5, "IC_one_way_bp": 1, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "fixed_policy": {"maturity": "M+1", "iv_threshold": IV, "quantity": "q1 equal notional", "profit_multiple": MULTIPLE, "call": "excluded"},
        "metric_leaders": best,
        "audit": audit,
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "isolated_daily": str(out / "isolated_daily.csv.gz"), "cycles": str(out / "cycles.csv"), "comparison": str(RUN / "decay_comparison.csv"), "isolated_summary": str(RUN / "isolated_scan_summary.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "quarter": sha(prior.QUARTER), "grid": sha(prior.GRID)},
        "warnings": ["Research-only; production unchanged.", "Model 510500 Put is theoretical.", "Real option history and route cycles are sparse.", "No dynamic margin, forced liquidation, tax, capacity, integer sizing, or explicit fill delay."],
        "decision": decision,
        "stability_label": stability,
        "git_status_after": git_status(),
        "finalized_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IC v1.3最终组合权利金衰减50%–80%复核\n\n"
        "## Data Snapshot\n\n真实挂牌层2022-09-19至2026-08-14；理论延展层2015-04-16至2026-08-14。\n\n"
        "## Comparison\n\n" + comparison.to_markdown(index=False) +
        "\n\n## Metric Leaders\n\n```json\n" + json.dumps(best, ensure_ascii=False, indent=2) +
        "\n```\n\n## Decision\n\n" + decision + "\n\n## Stability\n\n" + stability + "\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(json.dumps({"leaders": best, "decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
