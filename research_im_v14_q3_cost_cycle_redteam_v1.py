"""Adversarial cost and leave-one-short-Put-cycle tests for the IM q3 candidate."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v13_short95_quantity_delta_scan_v1 as quantity
import research_im_v13_full_short95_profit3x_joint_v1 as prior
import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_to_v1_4_redteam_fixed_core_q3_short95_core_put_3x_all_put_cost_5_10_20bp_and_cycle_leave_one_out"
SOURCE = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_short95_quantity_redteam_v2"
SPEC = ROOT / "docs" / "im_v14_q3_cost_cycle_redteam_v1_spec.md"
COST_BPS = (5, 10, 20)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()


def reprice_cost(daily: pd.DataFrame, cost_bp: int, candidate: str) -> pd.DataFrame:
    d = daily.copy()
    d["put_cost_rate"] = d.put_cost_rate.astype(float) * (cost_bp / 5.0)
    d["ret"] = (
        (1 + d.futures_gross_ret + d.put_pnl_ret + d.call_pnl_ret)
        * (1 - d.futures_cost_rate) * (1 - d.put_cost_rate) * (1 - d.call_cost_rate)
        - 1 + d.cash_weight * prior.CASH_DAILY
    )
    if not np.isfinite(d.ret).all() or d.ret.le(-1).any():
        raise RuntimeError(f"Invalid cost repricing: {candidate}")
    d["nav"] = (1 + d.ret).cumprod()
    d["drawdown"] = d.nav / d.nav.cummax() - 1
    d["candidate"] = candidate
    d["cost_bp"] = cost_bp
    return d


def q3_with_blocked_window(
    scope: str,
    weights: pd.DataFrame,
    router_fn,
    real_engine,
    model_engine,
    start: pd.Timestamp,
    end: pd.Timestamp,
    call_dir: Path,
):
    base, _ = portfolio.rebuild_base(scope, weights)
    grid = portfolio.half_grid(scope)
    momentum_put, _ = portfolio.momentum_put(scope, base)
    market, router_base, options, options_for_put, futures, _ = prior.quarterly_router_inputs(scope, base)
    signal = maturity.prepare_signal(router_base, options, "m1")
    blocked = signal.execution_date.between(start, end)
    signal.loc[blocked, "short_put_permission"] = False
    signal.loc[blocked, "permission_reason"] = "leave_one_cycle_out"
    routed, _, cycles = router_fn(
        router_base, options, futures, signal, 0.35, prior.common.FALLBACK, 0.60, 1.5
    )
    routed["date"] = pd.to_datetime(routed.date)
    route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
    imc_mask = routed.set_index("date").state.eq("imc")
    prefix = "r" if scope == "real" else "m"
    call_route, _, _ = maturity.call_inputs(
        scope, base.date, routed.state.eq("imc").astype(float),
        f"{prefix}loo{start:%Y%m%d}", call_dir,
    )
    schedule = valuation.corrected_core_schedule(base.date, scope, imc_mask)
    core_1bp, _ = prior.run_core(
        scope, base, market, options_for_put, schedule, real_engine, model_engine,
        f"{scope}_loo_{start:%Y%m%d}", 3.0, route_dates, 1.0,
    )
    core = core_1bp.copy()
    core["put_cost_rate"] *= 5.0
    total = prior.combine_puts(core, momentum_put, 5.0)
    daily = quantity.compose_sized(base, routed, total, grid, call_route, 1.5)
    daily["candidate"] = f"{scope}_q3_joint_loo_{start:%Y%m%d}"
    daily["scope"] = scope
    daily["variant"] = "joint_q3_leave_one_cycle_out"
    daily["quantity_label"] = "q3_delta05"
    daily["cost_bp"] = 5
    return daily, cycles


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    out = RUN / "daily_outputs"
    # A failed preflight may already have created this run-local directory.
    out.mkdir(exist_ok=True)

    source_daily = pd.read_csv(SOURCE / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    source_cycles = pd.read_csv(
        SOURCE / "daily_outputs" / "cycles.csv",
        parse_dates=["entry_date", "exit_date", "expiry_date"],
    )
    cost_parts = []
    cost_rows = []
    for scope in ("real", "model"):
        profit = source_daily[source_daily.candidate.eq(f"{scope}_profit3x_only")].sort_values("date")
        q3 = source_daily[source_daily.candidate.eq(f"{scope}_joint_q3_delta05")].sort_values("date")
        if profit.empty or q3.empty or not profit.date.reset_index(drop=True).equals(q3.date.reset_index(drop=True)):
            raise RuntimeError(f"Missing/misaligned source candidates: {scope}")
        for bp in COST_BPS:
            p = reprice_cost(profit, bp, f"{scope}_profit3x_cost{bp}bp")
            q = reprice_cost(q3, bp, f"{scope}_q3_joint_cost{bp}bp")
            cost_parts.extend([p, q])

    cost_daily = pd.concat(cost_parts, ignore_index=True)
    summary, wide, unavailable = portfolio.metric_rows(cost_daily)
    full = summary[summary.segment.eq("full")].set_index("candidate")
    for scope in ("real", "model"):
        for bp in COST_BPS:
            p = full.loc[f"{scope}_profit3x_cost{bp}bp"]
            q = full.loc[f"{scope}_q3_joint_cost{bp}bp"]
            cost_rows.append({
                "scope": scope, "cost_bp": bp,
                "profit3x_ann_return": float(p.ann_return),
                "q3_joint_ann_return": float(q.ann_return),
                "ann_return_diff": float(q.ann_return - p.ann_return),
                "profit3x_sharpe": float(p.sharpe_repo), "q3_joint_sharpe": float(q.sharpe_repo),
                "profit3x_max_dd": float(p.max_dd), "q3_joint_max_dd": float(q.max_dd),
                "max_dd_diff": float(q.max_dd - p.max_dd),
                "passes": bool(q.ann_return >= p.ann_return and q.max_dd - p.max_dd >= -0.01),
            })
    cost_comparison = pd.DataFrame(cost_rows)

    weights = portfolio.current_momentum_weights()
    prior.component.OPTION_ONE_WAY_COST = 0.0005
    router_fn, router_source = quantity.sized_router_factory()
    real_engine, model_engine, engine_source = prior.component.profit_route_engines()
    loo_parts = []
    loo_rows = []
    rerun_cycles = []
    for scope in ("real", "model"):
        original = source_cycles[
            source_cycles.scope.eq(scope) & source_cycles.quantity_label.eq("q3_delta05")
        ].sort_values("entry_date")
        full_ann = float(full.loc[f"{scope}_q3_joint_cost5bp"].ann_return)
        for _, cycle in original.iterrows():
            start = pd.Timestamp(cycle.entry_date)
            end = pd.Timestamp(cycle.exit_date)
            daily, new_cycles = q3_with_blocked_window(
                scope, weights, router_fn, real_engine, model_engine, start, end,
                out / "call_artifacts" / scope / f"{start:%Y%m%d}",
            )
            loo_parts.append(daily)
            if len(new_cycles):
                rerun_cycles.append(new_cycles.assign(scope=scope, excluded_entry=start, excluded_exit=end))
            metrics, _, _ = portfolio.metric_rows(daily)
            row = metrics[metrics.segment.eq("full")].iloc[0]
            loss = full_ann - float(row.ann_return)
            loo_rows.append({
                "scope": scope, "excluded_entry": start, "excluded_exit": end,
                "full_q3_ann_return": full_ann, "loo_ann_return": float(row.ann_return),
                "ann_return_loss": loss, "positive_contribution": max(loss, 0.0),
                "loo_sharpe": float(row.sharpe_repo), "loo_max_dd": float(row.max_dd),
                "rerun_cycle_count": int(len(new_cycles)),
            })
    loo = pd.DataFrame(loo_rows)
    concentration_rows = []
    for scope, group in loo.groupby("scope"):
        positive_sum = float(group.positive_contribution.sum())
        maximum = float(group.positive_contribution.max())
        concentration = maximum / positive_sum if positive_sum > 0 else 0.0
        concentration_rows.append({
            "scope": scope, "cycle_count": int(len(group)),
            "positive_contribution_sum": positive_sum,
            "largest_positive_contribution": maximum,
            "largest_cycle_share": concentration,
            "passes_50pct_gate": bool(concentration <= 0.50),
        })
    concentration = pd.DataFrame(concentration_rows)
    cost_pass = bool(cost_comparison.passes.all())
    concentration_pass = bool(concentration.passes_50pct_gate.all())
    promote = cost_pass and concentration_pass
    decision = (
        "promote_im_q3_joint_to_v14_research_mainline_no_production_change"
        if promote else "reject_im_q3_joint_v14_upgrade_keep_v13"
    )
    stability = (
        "cost_and_cycle_concentration_gates_passed"
        if promote else "cost_or_cycle_concentration_gate_failed"
    )

    cost_daily.to_csv(out / "cost_daily.csv.gz", index=False, compression="gzip")
    pd.concat(loo_parts, ignore_index=True).to_csv(out / "loo_daily.csv.gz", index=False, compression="gzip")
    if rerun_cycles:
        pd.concat(rerun_cycles, ignore_index=True).to_csv(out / "loo_rerun_cycles.csv", index=False)
    else:
        pd.DataFrame().to_csv(out / "loo_rerun_cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    cost_comparison.to_csv(RUN / "cost_comparison.csv", index=False, encoding="utf-8-sig")
    loo.to_csv(RUN / "cycle_leave_one_out.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + engine_source, encoding="utf-8")

    meta.update({
        "scan_type": "robustness_bundle",
        "baseline": {"candidate": ["real_profit3x_cost5bp", "model_profit3x_cost5bp"], "source": str(SOURCE)},
        "candidate_grid": [{"all_put_one_way_cost_bp": bp} for bp in COST_BPS],
        "data_snapshot": {"real_start": "2022-07-22", "real_end": "2026-08-14", "model_start": "2015-04-16", "model_end": "2026-08-14"},
        "cost_model": {"all_mo_put_one_way_bp": list(COST_BPS), "futures_one_way_bp": 1, "cash_annual": 0.03, "futures_buffer_per_1x": 0.30},
        "audit": {"cost_gate_pass": cost_pass, "concentration_gate_pass": concentration_pass, "source_phase": json.loads((SOURCE / "scan_meta.json").read_text(encoding="utf-8")).get("phase")},
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "cost_daily": str(out / "cost_daily.csv.gz"), "loo_daily": str(out / "loo_daily.csv.gz"), "cost_comparison": str(RUN / "cost_comparison.csv"), "leave_one_out": str(RUN / "cycle_leave_one_out.csv"), "concentration": str(RUN / "cycle_concentration.csv"), "executed": str(RUN / "executed_state_machines.py")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "source_daily": sha(SOURCE / "daily_outputs" / "daily.csv.gz"), "source_cycles": sha(SOURCE / "daily_outputs" / "cycles.csv")},
        "warnings": ["Research-only; production and ledger unchanged.", "Real listed data end at 2026-08-14.", "Model layer is theoretical extension.", "q3 uses continuous quantities; integer account sizing is unresolved."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM q3 成本与周期集中度对抗测试\n\n"
        "## Run Metadata\n\n研究专用；生产、账本、邮件、部署和下单均未修改。\n\n"
        "## Research Question\n\n检验 q3 联合方案能否承受 5/10/20BP 全 Put 成本，以及是否依赖少数卖 Put 周期。\n\n"
        "## Implementation Anchor\n\n基于修正后的 IM q3 完整逐日组合；逐周期剔除会重跑路由、Call、核心 Put 与组合。\n\n"
        "## Data Snapshot\n\n真实挂牌层 2022-07-22 至 2026-08-14；理论延展层 2015-04-16 至 2026-08-14。\n\n"
        "## Cost and Execution Assumptions\n\n所有 MO Put 单边 5/10/20BP；期货单边 1BP；T+1 开盘同步切换。\n\n"
        "## Runtime Override Plan\n\n仅研究进程屏蔽指定卖 Put 周期，不修改正式实现。\n\n"
        "## Commands\n\n见 command_log.txt。\n\n"
        "## Output Files\n\n见 scan_meta.json。\n\n"
        "## Full-Sample Results\n\n" + cost_comparison.to_markdown(index=False) +
        "\n\n## Window Results\n\n详见 scan_summary.csv 与 window_metrics.csv。\n\n"
        "## Cycle Leave-One-Out\n\n" + loo.to_markdown(index=False) +
        "\n\n## Concentration\n\n" + concentration.to_markdown(index=False) +
        "\n\n## Stability Classification\n\n" + stability +
        "\n\n## Decision\n\n" + decision +
        "\n\n## Limitations\n\n真实卖 Put 周期很少；理论层为代理延展；连续张数未解决整数账户约束。\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")


if __name__ == "__main__":
    main()
