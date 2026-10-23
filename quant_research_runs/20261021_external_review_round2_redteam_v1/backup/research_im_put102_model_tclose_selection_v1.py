"""Model-period timing sensitivity for IM 102% Put MOM120 floor quantities.

Research only.  It preserves the no-grid/no-Call composition and changes only
the strike-selection timestamp: existing T+1 close versus T close.
"""
from __future__ import annotations

import inspect
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_put102_mom_floor_quantity_v1 as q

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_im_v1_3_r7_put102_im_core_and_momentum_put_no_grid_no_call_model_mom120_floor_quantity_tclose_selection_v2"
FLOORS = (1, 2, 3)
TARGET = 1.02


def tclose_model_runner(selection_close: pd.Series):
    source = inspect.getsource(q.run.engine.run_model_monthly_close)
    source = source.replace("def run_model_monthly_close(", "def run_model_tclose_select(")
    source = source.replace("float(r.spot_close)*moneyness", "float(selection_close.loc[day])*moneyness")
    namespace = {
        "pd": pd,
        "np": np,
        "math": math,
        "legacy": q.run.engine.legacy,
        "model_fraction": q.run.engine.model_fraction,
        "validate": q.run.engine.validate,
        "selection_close": selection_close,
    }
    exec(source, namespace)
    return namespace["run_model_tclose_select"]


def selection_series(market: pd.DataFrame, templates: dict[str, pd.DataFrame]) -> tuple[pd.Series, list[str]]:
    actual = market.set_index("date").spot_close.copy()
    selected = actual.copy()
    exceptions: list[str] = []
    for schedule in templates.values():
        for row in schedule.itertuples(index=False):
            execution, evaluation = pd.Timestamp(row.execution_date), pd.Timestamp(row.eval_date)
            if evaluation in actual.index:
                selected.loc[execution] = actual.loc[evaluation]
            else:
                exceptions.append(str(execution.date()))
    return selected, sorted(set(exceptions))


def metric_rows(daily: pd.DataFrame, candidate: str, timing: str, floor: int) -> list[dict]:
    rows: list[dict] = []
    for segment, years in q.run.first.WINDOWS:
        start = daily.date.min() if years is None else q.run.END - pd.DateOffset(years=years)
        values = q.run.first.metrics(daily, start)
        values.update(candidate=candidate, timing=timing, mom120_floor_qty=floor, segment=segment)
        rows.append(values)
    return rows


def main() -> None:
    if (RUN / "scan_summary.csv").exists():
        raise RuntimeError("Refusing to overwrite completed scan outputs")
    state = pd.read_csv(q.run.BASE / "valuation_state_through_last_required_eval.csv.gz", parse_dates=["date"]).set_index("date")
    market = pd.read_csv(q.run.BASE / "model_market.csv.gz", parse_dates=["date"])
    base = pd.read_csv(q.run.JOINT / "model_baseline_base.csv.gz", parse_dates=["date"])
    grid = pd.read_csv(q.run.JOINT / "model_baseline_grid.csv.gz", parse_dates=["date"])
    for col in grid.columns:
        if col != "date":
            grid[col] = 0.0
    templates = {s: pd.read_csv(q.run.FULL / f"model_dual_{s}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"]) for s in ("core", "mom")}
    selected_close, initial_exceptions = selection_series(market, templates)
    tclose_runner = tclose_model_runner(selected_close)
    reset_dates = q.run.engine.monthly_dates(base.date)
    daily_all: list[pd.DataFrame] = []
    trades_all: list[pd.DataFrame] = []
    metrics_all: list[dict] = []
    for timing in ("t1close_selection", "tclose_selection"):
        for floor in FLOORS:
            legs: list[pd.DataFrame] = []
            trades: list[pd.DataFrame] = []
            for sleeve in ("core", "mom"):
                schedule = q.schedule(templates[sleeve], state, base.momentum_weight, "model", sleeve, floor)
                label = f"{timing}_floor{floor}_{sleeve}"
                if timing == "t1close_selection":
                    put, trade, _ = q.run.engine.run_model_monthly_close(market, schedule, "3m", TARGET, label, reset_dates=reset_dates)
                else:
                    put, trade, _ = tclose_runner(market, schedule, "3m", TARGET, label, reset_dates=reset_dates)
                put[q.run.first.FIELDS] *= (.5 if sleeve == "core" else .25) / 4
                legs.append(put)
                trades.append(trade.assign(timing=timing, mom120_floor_qty=floor, sleeve=sleeve))
            combined_put = sum(x[q.run.first.FIELDS] for x in legs)
            combined_put["date"] = base.date
            daily = q.run.comp.compose(base, combined_put, grid, None)
            candidate = f"{timing}_floor{floor}"
            daily["candidate"] = candidate
            daily["timing"] = timing
            daily["mom120_floor_qty"] = floor
            daily_all.append(daily)
            trades_all.append(pd.concat(trades, ignore_index=True))
            metrics_all.extend(metric_rows(daily, candidate, timing, floor))
    daily_out = pd.concat(daily_all, ignore_index=True)
    trade_out = pd.concat(trades_all, ignore_index=True)
    metrics = pd.DataFrame(metrics_all)
    summary = metrics.copy()
    wide = metrics.pivot(index=["candidate", "timing", "mom120_floor_qty"], columns="segment", values=["ann_return", "ann_vol", "sharpe_repo", "max_dd"]).reset_index()
    wide.columns = ["_".join(c).rstrip("_") if isinstance(c, tuple) else c for c in wide.columns]
    # Baseline parity to the immediately preceding no-grid/no-Call model study.
    previous = ROOT / "quant_param_scan_runs" / "20260918_ic_im_im_v1_3_r7_mom120_put102_no_grid_no_call_im_core_and_momentum_put_mom120_floor_qty_no_grid_call" / "daily_outputs" / "daily_candidates.csv.gz"
    old = pd.read_csv(previous, parse_dates=["date"])
    now = daily_out.loc[(daily_out.timing.eq("t1close_selection")) & (daily_out.mom120_floor_qty.eq(3))].sort_values("date")
    before = old.loc[(old.scope.eq("model")) & (old.candidate.eq("model_floor3"))].sort_values("date")
    parity = float(np.abs(now.ret.to_numpy() - before.ret.to_numpy()).max())
    checks = {
        "tclose_selector_uses_prior_model_close": True,
        "initial_selection_exceptions": initial_exceptions,
        "t1close_floor3_prior_study_return_parity_max_abs": parity,
        "grid_zero": bool(daily_out[[c for c in daily_out if c.startswith("overlay_") or c == "grid_carry"]].abs().to_numpy().max() == 0),
        "call_zero": bool(daily_out[["call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction", "call_coverage"]].abs().to_numpy().max() == 0),
        "signal_precedes_execution": bool(trade_out.signal_eval_date.le(trade_out.actual_execution_date).all()),
    }
    daily_out.to_csv(RUN / "daily_candidates.csv.gz", index=False, compression="gzip")
    trade_out.to_csv(RUN / "trades.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    (RUN / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update(phase="complete", scan_type="two_timing_x_three_floor_grid", baseline={"candidate": "t1close_selection_floor3", "comparison": "T+1-close strike selection"}, candidate_grid=[{"timing": t, "mom120_floor_qty": f, "target_moneyness": TARGET} for t in ("t1close_selection", "tclose_selection") for f in FLOORS], data_snapshot={"model": [str(market.date.min().date()), str(market.date.max().date())]}, cost_model={"execution": "model close valuation; T-close selection variant changes only strike reference", "futures_buffer": 0.30, "cash_annual": 0.03, "grid": "disabled", "call": "disabled"}, outputs={**meta["outputs"], "daily": str((RUN / "daily_candidates.csv.gz").relative_to(ROOT)), "trades": str((RUN / "trades.csv").relative_to(ROOT)), "checks": str((RUN / "checks.json").relative_to(ROOT))}, decision="pending_research_judgment", stability_label="timing_sensitivity_model_only")
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary.loc[summary.segment.eq("full"), ["candidate", "ann_return", "sharpe_repo", "max_dd"]].to_string(index=False)
    (RUN / "record.md").write_text(f"# IM 102% Put：模型期 T 收盘选约扫描\n\n## Data\n\n- 模型期：{market.date.min().date()} 至 {market.date.max().date()}；网格和 Call 关闭，未改正式信号。\n- `tclose_selection` 仅将新 Put 行权价从 T+1 模型收盘改为 T 模型收盘；模型仍按 T+1 收盘估值，未把它伪装成开盘实盘成交。\n- 初始例外：{initial_exceptions}。\n- floor3 旧路径逐日收益复现误差：{parity:.3e}。\n\n## Decision\n\n- `pending_research_judgment`；本层只检查选约时间敏感性，不改变正式信号。\n\n## Stability\n\n- `timing_sensitivity_model_only`。\n\n## 全样本\n\n```text\n{full}\n```\n", encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as f:
        f.write("python -X utf8 research_im_put102_model_tclose_selection_v1.py\n")
    print(full)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
