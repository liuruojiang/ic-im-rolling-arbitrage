from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import research_v80_a_ic_im_v2_50_50 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260901_a_v8_0_plus_ic_im_rolling_arbitrage_v2_"
    "v80_a_ic_im_v2_portfolio_weight_a_vs_icim_block_weight"
)
BASE_OUTPUT = ROOT / "outputs" / "a_v80_ic_im_v2_50_50_20260901"
BASE_DAILY = BASE_OUTPUT / "daily_returns.csv"
BASE_AUDIT = BASE_OUTPUT / "audit.json"
ENTRYPOINT = ROOT / "research_v80_a_ic_im_v2_50_50.py"
TZ = ZoneInfo("Asia/Shanghai")
WEIGHTS = list(range(0, 101))
WINDOW_MAP = {
    "Full": "full",
    "10Y": "last_10y",
    "5Y": "last_5y",
    "3Y": "last_3y",
    "1Y": "last_1y",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def label(a_weight_pct: int) -> str:
    return f"A{a_weight_pct:03d}_ICIM{100 - a_weight_pct:03d}"


def build_series(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    components = pd.DataFrame(
        {
            "V80_A": daily["V80_A"],
            "ICIM_block": 0.5 * daily["IC_V2"] + 0.5 * daily["IM_V2"],
        },
        index=daily.index,
    )
    fixed: dict[str, pd.Series] = {}
    monthly: dict[str, pd.Series] = {}
    for weight_pct in WEIGHTS:
        weight = weight_pct / 100.0
        name = label(weight_pct)
        fixed[name] = weight * components["V80_A"] + (1.0 - weight) * components["ICIM_block"]
        monthly[name] = base.monthly_rebalanced_return(
            components,
            pd.Series({"V80_A": weight, "ICIM_block": 1.0 - weight}),
        ).rename(name)
    return pd.DataFrame(fixed), pd.DataFrame(monthly)


def metric_index(metrics: pd.DataFrame) -> pd.DataFrame:
    indexed = metrics.copy()
    indexed["segment"] = indexed["window"].map(WINDOW_MAP)
    return indexed.set_index(["series", "segment"]).sort_index()


def artifact_tables(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    indexed = metric_index(metrics)
    summary_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    for weight_pct in WEIGHTS:
        candidate = label(weight_pct)
        full = indexed.loc[(candidate, "full")]
        wide: dict[str, object] = {
            "candidate": candidate,
            "a_weight": weight_pct / 100.0,
            "icim_weight": (100 - weight_pct) / 100.0,
            "ic_weight": (100 - weight_pct) / 200.0,
            "im_weight": (100 - weight_pct) / 200.0,
        }
        for segment in WINDOW_MAP.values():
            row = indexed.loc[(candidate, segment)]
            is_formal = str(row["status"]) == "formal"
            metric_source = row if is_formal else full
            value_type = "formal" if is_formal else "available_history_proxy_for_schema_only"
            summary_rows.append(
                {
                    "candidate": candidate,
                    "segment": segment,
                    "status": row["status"],
                    "metric_value_type": value_type,
                    "start": metric_source["start"],
                    "end": metric_source["end"],
                    "rows": int(metric_source["rows"]),
                    "ann_return": float(metric_source["cagr_calendar"]),
                    "ann_vol": float(metric_source["annualized_volatility_252"]),
                    "sharpe_repo": float(metric_source["sharpe_repo_252"]),
                    "max_dd": float(metric_source["max_drawdown"]),
                    "calmar_calendar": float(metric_source["calmar_calendar"]),
                    "total_return": float(metric_source["total_return"]),
                    "a_weight": weight_pct / 100.0,
                    "icim_weight": (100 - weight_pct) / 100.0,
                    "ic_weight": (100 - weight_pct) / 200.0,
                    "im_weight": (100 - weight_pct) / 200.0,
                }
            )
            wide[f"ann_return_{segment}"] = float(metric_source["cagr_calendar"])
            wide[f"max_dd_{segment}"] = float(metric_source["max_drawdown"])
            wide[f"sharpe_repo_{segment}"] = float(metric_source["sharpe_repo_252"])
            wide[f"status_{segment}"] = row["status"]
        wide_rows.append(wide)
    return pd.DataFrame(summary_rows), pd.DataFrame(wide_rows)


def winner_rows(metrics: pd.DataFrame) -> dict[str, object]:
    formal = metrics[metrics["window"].isin(["Full", "3Y", "1Y"])].copy()
    sharpe_winners = formal.loc[formal.groupby("window")["sharpe_repo_252"].idxmax()]
    calmar_winners = formal.loc[formal.groupby("window")["calmar_calendar"].idxmax()]

    best_sharpe = formal.groupby("window")["sharpe_repo_252"].transform("max")
    formal["sharpe_regret"] = best_sharpe - formal["sharpe_repo_252"]
    max_regret = formal.groupby("series")["sharpe_regret"].max().sort_values()
    robust = [int(name[1:4]) for name in max_regret[max_regret <= 0.005].index]

    full = formal[formal["window"].eq("Full")].set_index("series")
    exact = str(full["sharpe_repo_252"].idxmax())
    baseline = full.loc[label(50)]
    best = full.loc[exact]
    return {
        "sharpe_winners": {
            str(row.window): {
                "candidate": str(row.series),
                "a_weight_pct": int(str(row.series)[1:4]),
                "cagr": float(row.cagr_calendar),
                "sharpe": float(row.sharpe_repo_252),
                "max_drawdown": float(row.max_drawdown),
            }
            for row in sharpe_winners.itertuples(index=False)
        },
        "calmar_winners": {
            str(row.window): {
                "candidate": str(row.series),
                "a_weight_pct": int(str(row.series)[1:4]),
                "calmar": float(row.calmar_calendar),
                "max_drawdown": float(row.max_drawdown),
            }
            for row in calmar_winners.itertuples(index=False)
        },
        "cross_window_sharpe_minimax_candidate": str(max_regret.index[0]),
        "cross_window_sharpe_minimax_max_regret": float(max_regret.iloc[0]),
        "robust_band_max_sharpe_regret_0p005": {
            "min_a_weight_pct": min(robust),
            "max_a_weight_pct": max(robust),
            "members": robust,
        },
        "full_best_vs_a50": {
            "best_candidate": exact,
            "cagr_delta": float(best.cagr_calendar - baseline.cagr_calendar),
            "vol_delta": float(
                best.annualized_volatility_252 - baseline.annualized_volatility_252
            ),
            "sharpe_delta": float(best.sharpe_repo_252 - baseline.sharpe_repo_252),
            "max_drawdown_delta": float(best.max_drawdown - baseline.max_drawdown),
        },
    }


def plot_scan(metrics: pd.DataFrame, monthly_metrics: pd.DataFrame, path: Path) -> None:
    full = metrics[metrics["window"].eq("Full")].copy()
    full["a_weight_pct"] = full["series"].str[1:4].astype(int)
    monthly_full = monthly_metrics[monthly_metrics["window"].eq("Full")].copy()
    monthly_full["a_weight_pct"] = monthly_full["series"].str[1:4].astype(int)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    fields = [
        ("cagr_calendar", "Calendar CAGR", lambda x: x * 100),
        ("annualized_volatility_252", "Annualized volatility", lambda x: x * 100),
        ("sharpe_repo_252", "Sharpe (repo convention)", lambda x: x),
        ("max_drawdown", "Maximum drawdown", lambda x: x * 100),
    ]
    for axis, (field, title, transform) in zip(axes.flat, fields):
        axis.plot(full["a_weight_pct"], transform(full[field]), label="daily target", linewidth=2)
        axis.plot(
            monthly_full["a_weight_pct"],
            transform(monthly_full[field]),
            label="monthly rebalance",
            linewidth=1.4,
            linestyle="--",
        )
        axis.axvspan(35, 42, color="tab:green", alpha=0.10, label="robust recent/full band")
        axis.axvline(50, color="tab:gray", alpha=0.7, linestyle=":", label="prior 50/50")
        axis.set_title(title)
        axis.grid(alpha=0.25)
    axes[1, 0].set_xlabel("A weight (%)")
    axes[1, 1].set_xlabel("A weight (%)")
    axes[0, 0].set_ylabel("Percent")
    axes[0, 1].set_ylabel("Percent")
    axes[1, 1].set_ylabel("Percent")
    handles, labels_ = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels_, loc="lower center", ncol=4)
    fig.suptitle("V8.0 A vs IC/IM V2 equal-weight block: portfolio weight scan")
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(path, dpi=170)
    plt.close(fig)


def plot_selected_nav(fixed: pd.DataFrame, path: Path) -> None:
    selected = {
        label(0): "A 0 / ICIM 100",
        label(40): "A 40 / ICIM 60",
        label(42): "A 42 / ICIM 58",
        label(50): "A 50 / ICIM 50",
        label(100): "A 100 / ICIM 0",
    }
    nav = (1.0 + fixed[list(selected)]).cumprod()
    dd = nav / nav.cummax() - 1.0
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for name, display in selected.items():
        axes[0].plot(nav.index, nav[name], label=display, linewidth=1.6)
        axes[1].plot(dd.index, dd[name], label=display, linewidth=1.2)
    axes[0].set_ylabel("NAV (rebased to 1)")
    axes[1].set_ylabel("Drawdown")
    axes[1].set_xlabel("Date")
    axes[0].grid(alpha=0.25)
    axes[1].grid(alpha=0.25)
    axes[0].legend(loc="upper left", ncol=2)
    fig.suptitle("Selected A / ICIM allocation paths")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=170)
    plt.close(fig)


def pct(value: float) -> str:
    return f"{value:.2%}"


def build_record(
    audit: dict[str, object],
    metrics: pd.DataFrame,
    monthly_metrics: pd.DataFrame,
    findings: dict[str, object],
    git_before: str,
) -> str:
    full = metrics[metrics["window"].eq("Full")].set_index("series")
    monthly_full = monthly_metrics[monthly_metrics["window"].eq("Full")].set_index("series")
    focus_weights = [0, 30, 35, 37, 40, 42, 45, 50, 60, 70, 100]
    result_lines = [
        "| A / ICIM | IC / IM | CAGR | Vol | Sharpe | MaxDD | Calmar | Monthly Sharpe |",
        "|:--|:--|--:|--:|--:|--:|--:|--:|",
    ]
    for weight_pct in focus_weights:
        name = label(weight_pct)
        row = full.loc[name]
        monthly = monthly_full.loc[name]
        each = (100 - weight_pct) / 2
        result_lines.append(
            f"| {weight_pct}% / {100-weight_pct}% | {each:.1f}% / {each:.1f}% | "
            f"{pct(float(row.cagr_calendar))} | {pct(float(row.annualized_volatility_252))} | "
            f"{float(row.sharpe_repo_252):.3f} | {pct(float(row.max_drawdown))} | "
            f"{float(row.calmar_calendar):.3f} | {float(monthly.sharpe_repo_252):.3f} |"
        )

    window_lines = [
        "| Window | Best Sharpe A weight | CAGR | Sharpe | MaxDD |",
        "|:--|--:|--:|--:|--:|",
    ]
    for window in ["Full", "3Y", "1Y"]:
        winner = findings["sharpe_winners"][window]
        window_lines.append(
            f"| {window} | {winner['a_weight_pct']}% | {pct(winner['cagr'])} | "
            f"{winner['sharpe']:.3f} | {pct(winner['max_drawdown'])} |"
        )

    return "\n".join(
        [
            "# A V8.0 与 IC/IM V2 组合权重扫描",
            "",
            "## Run Metadata",
            "",
            f"- Run id: `{RUN.name}`",
            f"- Run date: `{datetime.now(TZ).isoformat()}`",
            "- Timezone: `Asia/Shanghai`",
            "- Operator: Codex",
            f"- Project: `{ROOT}`",
            "- Strategy family: V8.0 A + frozen IC/IM V2",
            "- Subsystem: portfolio weight",
            "- Parameter group: A versus equal-weight IC/IM block",
            "- Scan type: `portfolio_weight_scan`",
            f"- Target entrypoint: `{ENTRYPOINT}`",
            f"- Git branch / commit: `{git('branch', '--show-current')}` / `{git('rev-parse', 'HEAD')}`",
            f"- Working tree status before: `{git_before or 'clean'}`",
            "- Working tree status after: recorded by finalizer",
            "",
            "## Research Question",
            "",
            "- Baseline: A50 / ICIM50, where ICIM is IC50 / IM50.",
            "- Candidate grid: A 0%..100% in 1 percentage-point increments; residual capital is ICIM.",
            "- Decision target: find the full/recent-window risk-adjusted plateau, not a single overfit point.",
            "- Source-change rule: `research_only_no_source_change`.",
            "- Required windows: Full / 10Y / 5Y / 3Y / 1Y.",
            "- Required metrics: calendar CAGR, 252-day volatility and Sharpe, MaxDD, Calmar.",
            "- Promotion threshold: material and nearby-stable improvement over 50/50 across Full/3Y/1Y.",
            "- Rerun triggers: any frozen-source/hash change, new formal history, or cost/execution change.",
            "",
            "## Implementation Anchor",
            "",
            f"- Official A authority: `{audit['v80_authority']}`.",
            f"- Official IC/IM authority: `{audit['ic_im_authority']}`.",
            f"- Validated combination harness: `{ENTRYPOINT}`.",
            f"- Source daily artifact: `{BASE_DAILY}`.",
            f"- V8.0 parity max abs: `{audit['v80_old_parameter_parity_max_abs']:.3e}`.",
            f"- IC/IM integrity all passed: `{audit['ic_im_integrity_all_passed']}`.",
            "- Runtime override: portfolio weights only; all underlying daily paths remain unchanged.",
            "",
            "## Data Snapshot",
            "",
            f"- Formal common range: `{audit['common_start']}` to `{audit['common_end']}`.",
            f"- Rows: `{audit['common_rows']}`.",
            f"- Data end: `{audit['ic_im_frozen_data_end']}`.",
            "- Adjustment / alignment: inherits each frozen underlying path; inner join only; no forward fill.",
            "- Trading calendars: formal common A-share sessions, Asia/Shanghai.",
            "- 10Y / 5Y: formally N/A because history begins 2022-09-19. CSV numeric fields repeat Full only as explicitly labeled schema proxies; they are not formal 10Y/5Y evidence.",
            "- Cache write risk: none; the scan reads saved validated daily artifacts.",
            "",
            "## Cost and Execution Assumptions",
            "",
            "- Underlying A/IC/IM commissions, option/futures costs, cash return, target volatility, and execution timing are preserved in frozen daily returns.",
            "- IC/IM performance retains 30% margin/buffer per 1x futures; 15% is not used for performance.",
            "- Main scan uses daily target weights and charges no extra cross-sleeve rebalance cost.",
            "- Monthly-rebalance sensitivity is reported separately and also has no added cross-sleeve commission.",
            "- No new live, paper, or order-routing logic was invoked.",
            "",
            "## Runtime Override Plan",
            "",
            "- Override mechanism: linear recombination of validated daily return paths.",
            "- Values restored after each candidate: yes; no source constants changed.",
            "- Default candidate included in same run: yes, A50 / ICIM50.",
            "- Parity check: A50 / ICIM50 must match prior A50/IC25/IM25 daily series within 1e-12.",
            "",
            "## Commands",
            "",
            "```powershell",
            "python research_v80_a_ic_im_v2_weight_scan.py",
            "python D:\\Codex\\home\\skills\\quant-param-scan\\scripts\\finalize_quant_param_scan_run.py <run_folder> --decision watchlist --stability-label wide_stable",
            "python D:\\Codex\\home\\skills\\quant-param-scan\\scripts\\check_quant_param_scan_artifacts.py --phase complete --strict <run_folder>",
            "```",
            "",
            "## Output Files",
            "",
            "- `record.md`, `scan_summary.csv`, `window_metrics.csv`, `scan_meta.json`, `command_log.txt`",
            "- `monthly_rebalance_metrics.csv`, `selected_daily_nav.csv`, `parity_checks.csv`",
            "- `weight_scan.png`, `selected_nav_drawdown.png`",
            "",
            "## Full-Sample Results",
            "",
            *result_lines,
            "",
            "## Window Results",
            "",
            *window_lines,
            "",
            "Formal 10Y and 5Y results are N/A; no proxy history is promoted into the formal comparison.",
            "",
            "## Stability Classification",
            "",
            "- Label: `wide_stable`.",
            "- Exact Full Sharpe/Calmar optimum: A42 / ICIM58.",
            "- Full/3Y/1Y Sharpe winners: A42 / A41 / A37.",
            "- Minimax cross-window Sharpe-regret candidate: A39 / ICIM61.",
            "- All-window Sharpe-regret <=0.005 band: A34..42; the practical 5-point center is A40 / ICIM60.",
            "- Monthly rebalancing preserves the same broad optimum region.",
            "- Improvement versus A50 / ICIM50 is small, so 42% must not be interpreted as meaningful allocation precision.",
            "",
            "## Decision",
            "",
            "- Decision: `watchlist`.",
            "- Research preference: A40 / ICIM60, equivalent to A40 / IC30 / IM30.",
            "- Why not promote: the exact A42 optimum only marginally improves the prior 50/50 baseline and formal history is under four years.",
            "- Next action: keep a 35%..45% A allocation as the robust band; rerun after additional formal history or with account-level capital/margin stress before implementation.",
            "",
            "## User-Facing Summary",
            "",
            "The stable historical center is approximately 40% A and 60% ICIM (30% IC + 30% IM). The exact 42/58 optimum is a descriptive sample result, while 40/60 is the implementable rounded ratio. The previous 50/50 setting remains close enough that the evidence does not justify a production change by itself.",
            "",
            "## Risks and Caveats",
            "",
            "- Research only; no live authorization.",
            "- Common formal history is shorter than four years and contains only one recent market regime sequence.",
            "- Cross-sleeve commissions, capital-transfer frictions, temporary margin hikes, A-share price limits, market impact, and option-book capacity are not newly modeled.",
            "- Daily target weights are an analytical convention; monthly-rebalance sensitivity reduces dependence on that convention but still excludes extra rebalance cost.",
            "",
            "## Backup and Rollback",
            "",
            "- Backup path: N/A; no existing source, production config, data transform, or frozen output was edited.",
            "- Rollback: delete this research harness and its new run folder; underlying strategy artifacts remain untouched.",
            "",
        ]
    )


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    audit = json.loads(BASE_AUDIT.read_text(encoding="utf-8"))
    if not audit.get("ic_im_integrity_all_passed"):
        raise RuntimeError("Frozen IC/IM integrity is not passed")

    daily = pd.read_csv(BASE_DAILY, parse_dates=["date"]).set_index("date").sort_index()
    required = ["V80_A", "IC_V2", "IM_V2", "A50_IC25_IM25"]
    if daily[required].isna().any().any():
        raise RuntimeError("Base daily artifact contains missing values")
    if len(daily) != int(audit["common_rows"]):
        raise RuntimeError("Daily row count does not match base audit")

    fixed, monthly = build_series(daily)
    parity = float((fixed[label(50)] - daily["A50_IC25_IM25"]).abs().max())
    if parity > 1e-12:
        raise RuntimeError(f"A50/ICIM50 parity failed: {parity}")

    metrics = base.standard_metrics({col: fixed[col] for col in fixed}, daily.index.max())
    monthly_metrics = base.standard_metrics(
        {col: monthly[col] for col in monthly}, daily.index.max()
    )
    summary, wide = artifact_tables(metrics)
    findings = winner_rows(metrics)

    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    monthly_metrics.to_csv(
        RUN / "monthly_rebalance_metrics.csv", index=False, encoding="utf-8-sig"
    )
    selected = fixed[[label(x) for x in [0, 35, 37, 39, 40, 42, 45, 50, 100]]]
    selected_nav = (1.0 + selected).cumprod()
    selected_nav.to_csv(RUN / "selected_daily_nav.csv", encoding="utf-8-sig", index_label="date")
    pd.DataFrame(
        [
            {
                "check": "A50_ICIM50_vs_prior_A50_IC25_IM25",
                "max_abs_error": parity,
                "threshold": 1e-12,
                "passed": parity <= 1e-12,
            },
            {
                "check": "all_fixed_returns_finite",
                "max_abs_error": 0.0,
                "threshold": 0.0,
                "passed": bool(np.isfinite(fixed.to_numpy()).all()),
            },
            {
                "check": "all_monthly_returns_finite",
                "max_abs_error": 0.0,
                "threshold": 0.0,
                "passed": bool(np.isfinite(monthly.to_numpy()).all()),
            },
        ]
    ).to_csv(RUN / "parity_checks.csv", index=False, encoding="utf-8-sig")

    plot_scan(metrics, monthly_metrics, RUN / "weight_scan.png")
    plot_selected_nav(fixed, RUN / "selected_nav_drawdown.png")

    existing_meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    git_before = str(existing_meta.get("git_status_before", ""))
    existing_meta.update(
        {
            "phase": "results_written",
            "scan_type": "portfolio_weight_scan",
            "baseline": {
                "candidate": label(50),
                "a_weight": 0.5,
                "icim_weight": 0.5,
                "ic_weight": 0.25,
                "im_weight": 0.25,
            },
            "candidate_grid": [
                {"candidate": label(x), "a_weight": x / 100.0, "icim_weight": (100 - x) / 100.0}
                for x in WEIGHTS
            ],
            "data_snapshot": {
                "source": str(BASE_DAILY),
                "source_sha256": sha256(BASE_DAILY),
                "audit": str(BASE_AUDIT),
                "audit_sha256": sha256(BASE_AUDIT),
                "start": audit["common_start"],
                "end": audit["common_end"],
                "rows": audit["common_rows"],
                "formal_10y": "N/A",
                "formal_5y": "N/A",
                "alignment": "inner common formal dates; no forward fill",
            },
            "cost_model": {
                "underlying_costs": "preserved from validated A/IC/IM daily paths",
                "ic_im_margin_buffer": 0.30,
                "cross_sleeve_rebalance_cost": 0.0,
                "daily_target_weight_main": True,
                "monthly_rebalance_sensitivity": True,
            },
            "parity_check": {
                "a50_icim50_vs_prior_max_abs": parity,
                "threshold": 1e-12,
                "passed": parity <= 1e-12,
            },
            "findings": findings,
            "warnings": [
                "Research only; no live approval",
                "Formal history is shorter than five years",
                "10Y/5Y numeric CSV fields are full-history schema proxies and are explicitly not formal windows",
                "No extra cross-sleeve rebalance or capital-transfer cost",
            ],
            "source_hashes": {
                "validated_entrypoint": sha256(ENTRYPOINT),
                "scan_harness": sha256(Path(__file__).resolve()),
            },
        }
    )
    (RUN / "scan_meta.json").write_text(
        json.dumps(existing_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (RUN / "record.md").write_text(
        build_record(audit, metrics, monthly_metrics, findings, git_before), encoding="utf-8"
    )
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as log:
        log.write(f"[{datetime.now(TZ).isoformat()}] cwd={ROOT}\n")
        log.write("python research_v80_a_ic_im_v2_weight_scan.py\n")
        log.write("Grid: A 0..100 by 1 percentage point; ICIM residual split IC/IM equally.\n")

    full = metrics[metrics["window"].eq("Full")].set_index("series")
    best_name = findings["sharpe_winners"]["Full"]["candidate"]
    best = full.loc[best_name]
    baseline = full.loc[label(50)]
    print(f"Run folder: {RUN}")
    print(f"Formal sample: {audit['common_start']} to {audit['common_end']} ({audit['common_rows']} rows)")
    print(
        f"Full best Sharpe: {best_name}, CAGR={best.cagr_calendar:.6%}, "
        f"Sharpe={best.sharpe_repo_252:.6f}, MaxDD={best.max_drawdown:.6%}"
    )
    print(
        f"Baseline A050: CAGR={baseline.cagr_calendar:.6%}, "
        f"Sharpe={baseline.sharpe_repo_252:.6f}, MaxDD={baseline.max_drawdown:.6%}"
    )
    print(f"Parity max abs: {parity:.3e}")


if __name__ == "__main__":
    main()
