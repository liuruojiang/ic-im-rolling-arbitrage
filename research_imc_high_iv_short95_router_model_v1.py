from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

import research_im_short_put_recovery_atm_full_model_v1 as model_source
import research_imc_high_iv_short95_router_v1 as router
from im_put_maturity_valuation_tiers_v3 import metrics


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_rolling_arbitrage_imc_to_short95_router_v1_model_extension_high_iv_capital_router_iv_entry_threshold_and_nonadmission_route"
SPEC = ROOT / "docs" / "imc_high_iv_short95_router_model_v1_spec.md"
STRESS = {"stress_2015q3": ("2015-06-01", "2015-09-30"), "stress_2018": ("2018-01-01", "2018-12-31"), "stress_2020h1": ("2020-01-01", "2020-06-30")}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False).stdout.strip()


def segment_rows(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    segments = [("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)]
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        for name, years in segments:
            sample = group if years is None else group[group.date >= group.date.max() - pd.DateOffset(years=years)]
            rows.append({"candidate": candidate, "segment": name, "start": str(sample.date.min().date()), "end": str(sample.date.max().date()), "rows": len(sample), **metrics(sample.return_net), "high_iv_days": int(sample.high_iv.sum()), "cash_days": int(sample.state.eq("cash").sum()), "short_put_days": int(sample.state.eq("put").sum()), "recovery_im_days": int(sample.state.eq("recovery_im").sum())})
        for name, (start, end) in STRESS.items():
            sample = group[(group.date >= start) & (group.date <= end)]
            rows.append({"candidate": candidate, "segment": name, "start": start, "end": end, "rows": len(sample), **metrics(sample.return_net), "high_iv_days": int(sample.high_iv.sum()), "cash_days": int(sample.state.eq("cash").sum()), "short_put_days": int(sample.state.eq("put").sum()), "recovery_im_days": int(sample.state.eq("recovery_im").sum())})
    return pd.DataFrame(rows)


def main() -> None:
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("refusing to overwrite initialized/completed scan")
    market, base, options, futures, checks, basis = model_source.build_inputs()
    # The synthetic source uses settlement as its close mark.  This preserves the
    # source's stated synthetic availability assumption while keeping IV T-close.
    options = options.copy()
    options["close"] = options["settle"]
    signal = router.prepare_signal(base, options)
    signal.to_csv(RUN / "signal_audit.csv", index=False, encoding="utf-8-sig")
    parts, event_parts, cycle_parts = [], [], []
    for threshold in router.THRESHOLDS:
        for fallback in ("cash_when_ineligible", "continue_imc_when_ineligible"):
            daily, events, cycles = router.run_router(base, options, futures, signal, threshold, fallback)
            parts.append(daily)
            if len(events): event_parts.append(events)
            if len(cycles): cycle_parts.append(cycles.assign(candidate=router.route_label(threshold, fallback)))
    baseline = pd.DataFrame({"date": base.date, "candidate": "model_pure_rolling_imc", "return_net": base.baseline_plus_cash_ret, "state": "imc", "action": "", "route": "baseline", "high_iv": False, "short_put_permission": False, "iv": float("nan"), "decision_reason": ""})
    baseline["nav"] = (1 + baseline.return_net).cumprod()
    daily = pd.concat([baseline, *parts], ignore_index=True)
    summary = segment_rows(daily)
    wide_rows = []
    for candidate, group in summary[summary.segment.isin(["full", "last_10y", "last_5y", "last_3y", "last_1y"])].groupby("candidate", sort=False):
        row = {"candidate": candidate}
        for item in group.itertuples(index=False):
            for field in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{field}_{item.segment}"] = getattr(item, field)
        wide_rows.append(row)
    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(exist_ok=False)
    daily.to_csv(daily_dir / "daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(daily_dir / "events.csv", index=False)
    pd.concat(cycle_parts, ignore_index=True).to_csv(daily_dir / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(wide_rows).to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    summary[summary.segment.isin(STRESS)].to_csv(RUN / "stress_period_metrics.csv", index=False, encoding="utf-8-sig")
    meta.update({"scan_type": "theoretical_proxy_extension", "baseline": {"candidate": "model_pure_rolling_imc"}, "candidate_grid": [{"iv_threshold": t, "nonadmission_route": f} for t in router.THRESHOLDS for f in ("cash_when_ineligible", "continue_imc_when_ineligible")], "data_snapshot": {"start": str(base.date.min().date()), "end": str(base.date.max().date()), "rows": len(base), "layer": "uniform_theoretical_model_not_real_im_mo", "model_market_checks": checks, "basis_calibration": basis}, "cost_model": {"one_way_notional": router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03}, "outputs": {**meta["outputs"], "signal_audit": str(RUN / "signal_audit.csv"), "daily": str(daily_dir / "daily.csv.gz"), "events": str(daily_dir / "events.csv"), "cycles": str(daily_dir / "cycles.csv"), "stress_period_metrics": str(RUN / "stress_period_metrics.csv")}, "decision": "research_only_model_direction_check", "stability_label": "unclassified_theoretical_proxy", "source_hashes": {"spec": sha(SPEC), "script": sha(Path(__file__)), "router": sha(ROOT / "research_imc_high_iv_short95_router_v1.py"), "model_source": sha(ROOT / "research_im_short_put_recovery_atm_full_model_v1.py")}, "warnings": ["Theoretical/proxy only; no real IM basis, MO surface, observed liquidity or executable historical claim.", "Post-listing average basis calibration is applied pre-listing and is look-ahead sensitive.", "No production/mainline files edited."], "git_status_after": git("status", "--short")})
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    text = "# IMC 高IV路由至卖95%Put：理论模型扩展 v1\n\n## Run Metadata\n\n- 证据层：理论/代理，绝非真实IM/MO执行历史。\n\n## Research Question\n\n- 检验真实层两种未准入分支在2015—2026代理压力期的方向。\n\n## Implementation Anchor\n\n- 复用统一模型市场、期货贴水和期权定价；路由状态机与真实层一致。\n\n## Data Snapshot\n\n- " + f"{base.date.min().date()} 至 {base.date.max().date()}，{len(base)}日。\n\n## Cost and Execution Assumptions\n\n- T收盘/T+1开盘、1bp、30%缓冲、现金3%；模型限制见规格。\n\n## Runtime Override Plan\n\n- 独立模型工件，不触碰真实研究或生产。\n\n## Commands\n\n```powershell\npython -X utf8 research_imc_high_iv_short95_router_model_v1.py\n```\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`、`stress_period_metrics.csv`、`daily_outputs/`。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) + "\n\n## Window Results\n\n- Full/10Y/5Y/3Y/1Y及压力段见CSV。\n\n## Stability Classification\n\n- `unclassified_theoretical_proxy`；只与真实层作方向对照。\n\n## Decision\n\n- `research_only_model_direction_check`；不得以模型结果晋级。\n\n## User-Facing Summary\n\n- 理论延伸已完成，必须和真实层分开解释。\n"
    (RUN / "record.md").write_text(text, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))


if __name__ == "__main__":
    main()
