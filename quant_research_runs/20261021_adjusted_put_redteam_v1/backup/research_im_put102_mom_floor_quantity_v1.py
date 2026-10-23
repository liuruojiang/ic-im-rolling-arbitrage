"""Research-only 102% IM Put MOM120 floor quantity scan using the released full-combination engine."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_im_v1_3_r7_mom120_put102_combined_im_core_and_momentum_put_mom120_floor_qty_at_102pct_v2"
SPEC = ROOT / "docs" / "im_put102_mom_floor_quantity_v1_spec.md"
SOURCE = ROOT / "quant_param_scan_runs" / "20260908_im_mom120_put102_combined_v1"
sys.path.insert(0, str(SOURCE))
import run_combined as run  # noqa: E402

FLOORS = (1, 2, 3)
TARGET = 1.02


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule(template: pd.DataFrame, state: pd.DataFrame, weight: pd.Series, scope: str, sleeve: str, floor: int) -> pd.DataFrame:
    out = template.copy()
    valuation = out.eval_date.map(state.valuation_tier)
    mom = out.eval_date.map(state.momentum_120)
    if valuation.isna().any() or mom.isna().any():
        raise RuntimeError("missing frozen valuation or MOM120 state")
    active = mom.lt(0).to_numpy()
    parent = np.maximum(valuation.to_numpy(float) if sleeve == "core" else 0.0, np.where(active, floor, 0.0))
    quantity = parent * (1 if scope == "model" and sleeve == "core" else 2) * (weight.to_numpy(float) if sleeve == "mom" else 1.0)
    if not np.equal(quantity, np.floor(quantity)).all():
        raise RuntimeError("normalized quantity is unexpectedly fractional before scaling")
    out["binary_target_qty"] = (quantity.astype(int) * 4)
    out["three_tier_target_qty"] = out["binary_target_qty"]
    out["original_qty"] = quantity.astype(int)
    out["valuation_tier"] = valuation if sleeve == "core" else 0
    out["mom120_floor_qty"] = np.where(active, floor, 0)
    out["mom120_active"] = active
    out["put_buy_allowed"] = True
    return out


def metric_rows(daily: pd.DataFrame, candidate: str, scope: str) -> list[dict]:
    result = []
    for segment, years in run.first.WINDOWS:
        start = daily.date.min() if years is None else run.END - pd.DateOffset(years=years)
        row = {"candidate": candidate, "scope": scope, "mom120_floor_qty": int(candidate.rsplit("floor", 1)[1]), "segment": segment}
        if start < daily.date.min():
            row.update(start="N/A", end=str(run.END.date()), rows=0, ann_return="N/A", ann_vol="N/A", sharpe_repo="N/A", max_dd="N/A")
        else:
            row.update(run.first.metrics(daily, start))
        result.append(row)
    return result


def main() -> None:
    if any((RUN / name).exists() for name in ("scan_summary.csv", "window_metrics.csv", "daily_outputs")):
        raise RuntimeError("scan outputs already exist")
    state = pd.read_csv(run.BASE / "valuation_state_through_last_required_eval.csv.gz", parse_dates=["date"]).set_index("date")
    market = pd.read_csv(run.BASE / "model_market.csv.gz", parse_dates=["date"])
    upstream = pd.read_csv(run.BASE / "real_upstream.csv.gz", parse_dates=["date"])
    active = pd.read_csv(run.BASE / "real_active.csv.gz", parse_dates=["date"])
    raw = pd.read_csv(run.BASE / "real_options.csv.gz", parse_dates=["date", "contract_month", "rule_expiry", "actual_expiry"])
    options = run.engine.with_execution_prices(raw)
    rows, daily_all, costs, parity_rows = [], [], [], []
    for scope in ("model", "real"):
        base = pd.read_csv(run.JOINT / f"{scope}_baseline_base.csv.gz", parse_dates=["date"])
        grid = pd.read_csv(run.JOINT / f"{scope}_baseline_grid.csv.gz", parse_dates=["date"])
        call = pd.read_csv(run.JOINT / f"{scope}_baseline_call.csv.gz", parse_dates=["date"])
        templates = {s: pd.read_csv(run.FULL / f"{scope}_dual_{s}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"]) for s in ("core", "mom")}
        for floor in FLOORS:
            legs, trades = [], []
            for sleeve in ("core", "mom"):
                s = schedule(templates[sleeve], state, base.momentum_weight, scope, sleeve, floor)
                label = f"{scope}_floor{floor}_{sleeve}"
                if scope == "model":
                    put, leg_trades, _ = run.engine.run_model_monthly_close(market, s, "3m", TARGET, label, reset_dates=run.engine.monthly_dates(base.date))
                    norm = (.5 if sleeve == "core" else .25) / 4
                else:
                    put, leg_trades, _ = run.engine.run_real_monthly_close(upstream, options, active, s, "3m", TARGET, label, reset_dates=run.engine.monthly_dates(base.date))
                    norm = .25 / 4
                if put.attrs.get("pending_end"):
                    raise RuntimeError(f"pending end state: {label}")
                put[run.first.FIELDS] *= norm
                legs.append(put)
                trades.append(leg_trades.assign(sleeve=sleeve, normalization_scale=norm))
            combined_put = sum(x[run.first.FIELDS] for x in legs)
            combined_put["date"] = base.date
            daily = run.comp.compose(base, combined_put, grid, call)
            candidate = f"floor{floor}"
            daily["candidate"] = candidate
            daily["scope"] = scope
            rows.extend(metric_rows(daily, f"{scope}_{candidate}", scope))
            daily_all.append(daily)
            tr = pd.concat(trades, ignore_index=True)
            costs.append({"candidate": candidate, "scope": scope, "mom120_floor_qty": floor, "put_cost_total": float(daily.put_cost_rate.sum()), "mean_put_capital": float(daily.put_mark_fraction.mean()), "max_put_capital": float(daily.put_mark_fraction.max()), "min_cash": float(daily.cash_weight.min()), "trade_events": int(len(tr))})
            if floor == 3:
                old = pd.read_csv(SOURCE / f"{scope}_combined_daily.csv.gz", parse_dates=["date"])
                err = float(np.abs(daily.ret.to_numpy() - old.ret.to_numpy()).max())
                if err > 1e-12:
                    raise RuntimeError(f"published floor3 parity failed: {scope} {err}")
                parity_rows.append({"scope": scope, "ret_max_abs": err})
    summary = pd.DataFrame(rows)
    wide = summary.pivot(index=["scope", "candidate", "mom120_floor_qty"], columns="segment", values=["ann_return", "ann_vol", "sharpe_repo", "max_dd"]).reset_index()
    wide.columns = ["_".join(x).rstrip("_") if isinstance(x, tuple) else x for x in wide.columns]
    for scope, part in wide.groupby("scope", sort=False):
        ref = part[part.candidate.eq(f"{scope}_floor3")].iloc[0]
        for metric in ("ann_return", "sharpe_repo", "max_dd"):
            for segment in ("full", "last_10y", "last_5y", "last_3y", "last_1y"):
                wide.loc[wide.scope.eq(scope), f"{metric}_{segment}_vs_floor3"] = wide.loc[wide.scope.eq(scope), f"{metric}_{segment}"] - ref[f"{metric}_{segment}"] if ref[f"{metric}_{segment}"] != "N/A" else "N/A"
    (RUN / "daily_outputs").mkdir()
    pd.concat(daily_all, ignore_index=True).to_csv(RUN / "daily_outputs" / "daily_candidates.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    pd.DataFrame(costs).to_csv(RUN / "protection_costs.csv", index=False)
    pd.DataFrame(parity_rows).to_csv(RUN / "parity_checks.csv", index=False)
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    unavailable = {f"real_floor{x}": {segment: "Actual IM/MO starts 2022-07-22; fewer than 5 or 10 years." for segment in ("last_10y", "last_5y")} for x in FLOORS}
    meta.update({"scan_type": "one_parameter_sweep", "baseline": {"candidate": "real_floor3", "published_artifact": str(SOURCE.relative_to(ROOT))}, "candidate_grid": [{"mom120_floor_qty": x, "target_moneyness": 1.02} for x in FLOORS], "data_snapshot": {"model": ["2015-04-16", str(run.END.date())], "real": ["2022-07-22", str(run.END.date())], "source": "released 20260908 frozen full-combination inputs"}, "cost_model": {"execution": "T signal / T+1 close", "futures_buffer": 0.30, "cash_annual": 0.03, "fees": "inherited futures/Put/Call fees", "real_zero_volume": "official settlement estimate"}, "unavailable_segments": unavailable, "parity_check": parity_rows, "source_hashes": {str(SPEC.relative_to(ROOT)): sha(SPEC), str((SOURCE / "run_combined.py").relative_to(ROOT)): sha(SOURCE / "run_combined.py"), str((SOURCE / "real_combined_daily.csv.gz").relative_to(ROOT)): sha(SOURCE / "real_combined_daily.csv.gz")}, "warnings": ["actual MO begins 2022-07-22", "no new slippage or order-book capacity model", "research only; no published signal change"]})
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    display = wide[wide.scope.eq("real")][["candidate", "ann_return_full", "sharpe_repo_full", "max_dd_full", "ann_return_last_3y", "max_dd_last_3y", "ann_return_last_1y", "max_dd_last_1y"]].to_string(index=False)
    (RUN / "record.md").write_text(f"""# IM 102% Put下MOM120保护数量扫描 v1\n\n## Run Metadata\n\n- 研究扫描；正式信号未修改。\n\n## Research Question\n\n- 固定102%目标行权价，扫描负动量下限1/2/3张。\n\n## Implementation Anchor\n\n- 复用已发布组合的`run_combined.py`完整函数链；floor3逐日复现已发布工件。\n\n## Data Snapshot\n\n- 真实MO：2022-07-22至{run.END.date()}；模型层另作机制检查。\n\n## Cost and Execution Assumptions\n\n- T信号/T+1收盘，30%期货缓冲、3%现金及继承费用。\n\n## Commands\n\n- `python research_im_put102_mom_floor_quantity_v1.py`\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`、`protection_costs.csv`、`daily_outputs/`。\n\n## Full-Sample Results\n\n```text\n{display}\n```\n\n## Window Results\n\n- 五窗口完整结果见`window_metrics.csv`。\n\n## Stability Classification\n\n- 待根据实际结果填写。\n\n## Decision\n\n- Decision: `pending_research_judgment`.\n\n## User-Facing Summary\n\n- 使用实际MO数据直接回答102%下1/2/3张的问题。\n""", encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as f:
        f.write("python research_im_put102_mom_floor_quantity_v1.py\n")
    print(wide.to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
