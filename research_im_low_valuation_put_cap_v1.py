"""Research-only IM low-valuation long-Put cap/ablation on the frozen real path."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import ic_im_put_max_protection_scan_v1 as base
import im_put_four_tier_mom120_floor_scan_v3 as floor3
import im_mo_adaptive_valuation_mom120_floor_v12 as im_v12


ROOT = Path(__file__).resolve().parent
VERSION = "im_low_valuation_put_cap_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_im_v2_frozen_core_put_im_put_low_valuation_put_cap"
DAILY_DIR = RUN / "daily_outputs"
CANDIDATES = (
    ("baseline_floor3", "baseline", False, False),
    ("low_value_cap1_preserve_mom", "cap1", True, False),
    ("low_value_no_put_preserve_mom", "zero", True, False),
    ("low_value_no_put_override_mom", "zero", True, True),
)
FLOOR3_DAILY = ROOT / "quant_param_scan_runs" / "20260820_ic_im_im_put_four_tier_mom120_floor_scan_v3_im_put_four_tier_mom120_floor_qty" / "daily_outputs" / "daily_candidates.csv.gz"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule_for(source: pd.DataFrame, valuation: pd.DataFrame, thresholds: pd.DataFrame, name: str, mode: str, apply_low: bool, override_mom: bool) -> pd.DataFrame:
    definition = next(x for x in floor3.CANDIDATES if x["mom_floor_qty"] == 3)
    out = floor3.build_schedule(source, valuation, thresholds, definition).copy()
    low = out["new_valuation_tier"].astype(int).isin([0, 1])
    negative = out["momentum_120"].astype(float).lt(0)
    valuation_qty = out["new_valuation_tier"].astype(int).to_numpy()
    if apply_low and mode == "cap1":
        valuation_qty = np.where(low.to_numpy(), np.minimum(valuation_qty, 1), valuation_qty)
    elif apply_low and mode == "zero":
        valuation_qty = np.where(low.to_numpy(), 0, valuation_qty)
    mom_qty = np.where(negative.to_numpy(), 3, 0)
    target = np.maximum(valuation_qty, mom_qty).astype(int)
    if apply_low and override_mom:
        target = np.where(low.to_numpy(), 0, target).astype(int)
    out["binary_target_qty"] = target
    out["three_tier_target_qty"] = target
    out["candidate"] = name
    out["low_valuation"] = low
    out["mom_negative"] = negative
    out["low_value_override_mom"] = bool(override_mom)
    return out


def metric_tables(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        for segment in base.WINDOWS:
            start = group.date.min() if segment == "full" else max(group.date.min(), group.date.max() - pd.DateOffset(years=int(segment.removeprefix("last_").removesuffix("y"))))
            rows.append(base.metric_row(candidate, "IM", segment, group[group.date.ge(start)], {"policy": "low_valuation_put_cap", "threshold": 0.0, "max_target": 3.0}))
    summary = pd.DataFrame(rows)
    wide = []
    for candidate, part in summary.groupby("candidate", sort=False):
        row = {"candidate": candidate}
        for item in part.itertuples(index=False):
            for metric in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide.append(row)
    return summary, pd.DataFrame(wide)


def main() -> None:
    if not RUN.exists() or any((RUN / name).exists() for name in ("scan_summary.csv", "window_metrics.csv", "daily_outputs")):
        raise RuntimeError("scan folder is absent or already contains results")
    upstream, active_im, options, source, valuation, thresholds, frozen = floor3.load_inputs()
    parts, schedules, trades = [], [], []
    for name, mode, apply_low, override_mom in CANDIDATES:
        schedule = schedule_for(source, valuation, thresholds, name, mode, apply_low, override_mom)
        overlay, leg_trades, _ = im_v12.v8.run_real_normal_close(upstream, options, active_im, schedule, "3m", 0.95, name)
        parts.append(base.recompose_im(frozen, overlay, name))
        schedules.append(schedule)
        trades.append(leg_trades.assign(candidate=name))
    daily = pd.concat(parts, ignore_index=True)
    schedules_df, trades_df = pd.concat(schedules, ignore_index=True), pd.concat(trades, ignore_index=True)
    summary, wide = metric_tables(daily)
    ref = wide[wide.candidate.eq("baseline_floor3")].iloc[0]
    for metric in ("ann_return", "sharpe_repo", "max_dd"):
        for window in ("full", "last_10y", "last_5y", "last_3y", "last_1y"):
            wide[f"{metric}_{window}_vs_baseline"] = wide[f"{metric}_{window}"] - float(ref[f"{metric}_{window}"])
    exposures = []
    for candidate, group in daily.groupby("candidate", sort=False):
        schedule = schedules_df[schedules_df.candidate.eq(candidate)]
        exposures.append({"candidate": candidate, "low_valuation_days": int(schedule.low_valuation.sum()), "low_valuation_negative_mom_days": int((schedule.low_valuation & schedule.mom_negative).sum()), "put_cost_total": float(group.put_cost_rate.sum()), "held_put_days": int(group.put_fraction.gt(0).sum()), "trade_events": int(len(trades_df[trades_df.candidate.eq(candidate)]))})
    parity_daily = daily[daily.candidate.eq("baseline_floor3")].sort_values("date")
    frozen_floor3 = pd.read_csv(FLOOR3_DAILY, parse_dates=["date"])
    frozen_floor3 = frozen_floor3[frozen_floor3.candidate.eq("IM_4tier_mom_floor_3")].sort_values("date")
    parity = float(np.max(np.abs(parity_daily.cash_ret.to_numpy() - frozen_floor3.cash_ret.to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"baseline parity failed: {parity}")
    DAILY_DIR.mkdir()
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules_df.to_csv(DAILY_DIR / "target_schedules.csv.gz", index=False, compression="gzip")
    trades_df.to_csv(DAILY_DIR / "put_trades.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    pd.DataFrame(exposures).to_csv(RUN / "exposure_diagnostics.csv", index=False)
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update({"scan_type": "conditional_ablation", "baseline": {"primary": "baseline_floor3"}, "candidate_grid": [{"candidate": n, "low_value_rule": m, "preserve_negative_mom_floor": not o} for n,m,_,o in CANDIDATES], "data_snapshot": {"real": [str(daily.date.min().date()), str(daily.date.max().date())], "source": "frozen real IM/MO path"}, "cost_model": {"execution": "T close signal / T+1 official close", "margin_buffer": 0.30, "cash_annual": 0.03, "put_cost": "inherited official MO cost"}, "parity_check": {"cash_ret_max_abs": parity, "tolerance": 1e-12, "reference": str(FLOOR3_DAILY.relative_to(ROOT))}, "source_hashes": {str(SPEC.relative_to(ROOT)): digest(SPEC), "im_put_four_tier_mom120_floor_scan_v3.py": digest(ROOT / "im_put_four_tier_mom120_floor_scan_v3.py"), str(FLOOR3_DAILY.relative_to(ROOT)): digest(FLOOR3_DAILY)}, "warnings": ["real MO history only", "10y/5y clip to sample start", "no independent OOS", "v1.4 short-Put route is out of scope"]})
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    display = wide[["candidate", "ann_return_full", "sharpe_repo_full", "max_dd_full", "ann_return_last_3y", "max_dd_last_3y", "ann_return_last_1y", "max_dd_last_1y"]].to_string(index=False)
    (RUN / "record.md").write_text(f"""# IM低估值核心买Put减仓/取消扫描 v1\n\n## Run Metadata\n\n- 研究用途；不修改冻结主线或v1.4研究信号。\n\n## Research Question\n\n- 见 `docs/im_low_valuation_put_cap_v1_spec.md`。\n\n## Implementation Anchor\n\n- 复用 `im_put_four_tier_mom120_floor_scan_v3.py` 的正式真实路径与执行器。\n\n## Data Snapshot\n\n- {daily.date.min().date()} 至 {daily.date.max().date()}，真实IM/MO。\n\n## Cost and Execution Assumptions\n\n- T收盘信号、T+1官方收盘；30%保证金缓冲、3%现金、继承MO成本。\n\n## Commands\n\n- `python research_im_low_valuation_put_cap_v1.py`\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`、`daily_outputs/`。\n\n## Full-Sample Results\n\n```text\n{display}\n```\n\n## Window Results\n\n- 完整五窗口见 `window_metrics.csv`。\n\n## Stability Classification\n\n- 待依据同一基线的多窗口与尾部回撤判断。\n\n## Decision\n\n- Decision: `pending_research_judgment`.\n\n## User-Facing Summary\n\n- 低估值取消Put与负动量保护已拆分比较；不将两者混同。\n""", encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python research_im_low_valuation_put_cap_v1.py\n")
    print(wide.to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
