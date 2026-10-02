"""Research-only IC/IM long-Put exit scan by annualized discount threshold."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_discount_gate_5pct_v1 as ic
import research_im_put_discount_gate_5pct_v1 as im


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quant_param_scan_runs" / (
    "20260918_ic_im_v1_3_independent_long_put_historical_replay_"
    "ic_im_long_put_discount_gate_annualized_discount_exit_threshold_0_to_5pct"
)
THRESHOLDS = tuple(np.round(np.arange(0.0, 0.0501, 0.005), 4))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric_rows(product: str, candidate: str, scope: str, frame: pd.DataFrame, unavailable: set[str]) -> list[dict[str, object]]:
    end = frame.date.max()
    rows: list[dict[str, object]] = []
    for window, years in (("full", None), ("10y", 10), ("5y", 5), ("3y", 3), ("1y", 1)):
        if window in unavailable:
            rows.append(dict(product=product, candidate=candidate, scope=scope, window=window, available=False,
                             reason="actual option history insufficient", rows=0, ann_return="N/A", ann_vol="N/A",
                             sharpe="N/A", max_dd="N/A", total_return="N/A"))
            continue
        start = frame.date.min() if years is None else end - pd.DateOffset(years=years)
        sample = frame[frame.date.ge(start)]
        ret = sample.ret.astype(float)
        nav = (1.0 + ret).cumprod()
        vol = float(ret.std(ddof=1) * np.sqrt(252.0))
        ann = float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0)
        rows.append(dict(product=product, candidate=candidate, scope=scope, window=window, available=True, reason="",
                         start=str(sample.date.min().date()), end=str(sample.date.max().date()), rows=len(sample),
                         ann_return=ann, ann_vol=vol, sharpe=ann / vol if vol else np.nan,
                         max_dd=float((nav / nav.cummax() - 1.0).min()), total_return=float(nav.iloc[-1] - 1.0)))
    return rows


def ic_setup() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict, object, object, object]:
    frame, _, selected = ic.baseline.load_base_components()
    frames, _, market, _ = ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    rolls = ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    schedule = ic.baseline.build_schedule(selected, "combined_current")
    discount = ic.annualized_discount(frames["ic"])[["date", "annualized_discount"]]
    schedule = pd.merge_asof(schedule.sort_values("eval_date"), discount.sort_values("date"),
                             left_on="eval_date", right_on="date", direction="backward",
                             allow_exact_matches=True).drop(columns="date")
    ledger, _ = ic.baseline.run_ledger("baseline", ic.baseline.build_schedule(selected, "combined_current"), frames, market, rolls)
    baseline = ic.baseline.combine_candidate(frame, {"baseline": ledger}, "baseline", ("baseline",))
    official = pd.read_csv(ic.baseline.OFFICIAL_DAILY, parse_dates=["date"])
    parity = {col: float((baseline[col] - official[col]).abs().max()) for col in ("ret", "cash_weight")}
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"IC baseline parity failure: {parity}")
    return frame, schedule, baseline, parity, frames, market, rolls


def ic_candidate(threshold: float, frame: pd.DataFrame, template: pd.DataFrame, frames: object, market: object, rolls: object) -> tuple[pd.DataFrame, dict]:
    schedule = template.copy()
    regime = schedule.annualized_discount.lt(threshold).fillna(False)
    schedule["raw_target_delta"] = schedule.target_delta.astype(float)
    schedule.loc[regime, ["target_delta", "target_fraction", "binary_target_fraction", "three_tier_target_fraction"]] = 0.0
    name = f"ic_lt_{threshold:.3f}"
    ledger, trades = ic.baseline.run_ledger(name, schedule, frames, market, rolls)
    daily = ic.baseline.combine_candidate(frame, {name: ledger}, name, (name,))
    return daily, dict(product="IC", candidate=name, threshold=threshold, threshold_pct=threshold * 100,
                       regime_days=int(regime.sum()), regime_fraction=float(regime.mean()),
                       positive_target_evaluations=int(schedule.raw_target_delta.gt(0).sum()),
                       target_removed_evaluations=int((regime & schedule.raw_target_delta.gt(0)).sum()),
                       target_delta_removed=float(schedule.loc[regime, "raw_target_delta"].sum()), trade_events=len(trades))


def im_setup() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    discount = im.discount_states()
    upstream = pd.read_csv(im.source.BASE / "real_upstream.csv.gz", parse_dates=["date"])
    active = pd.read_csv(im.source.BASE / "real_active.csv.gz", parse_dates=["date"])
    raw = pd.read_csv(im.source.BASE / "real_options.csv.gz", parse_dates=["date", "contract_month", "rule_expiry", "actual_expiry"])
    options = im.source.engine.with_execution_prices(raw)
    base = pd.read_csv(im.SOURCE / "real_fixed_base.csv.gz", parse_dates=["date"])
    grid = pd.read_csv(im.SOURCE / "real_fixed_grid.csv.gz", parse_dates=["date"])
    call = pd.read_csv(im.SOURCE / "real_fixed_call.csv.gz", parse_dates=["date"])
    baseline = pd.read_csv(im.SOURCE / "real_combined_daily.csv.gz", parse_dates=["date"])
    return discount, upstream, active, options, base, grid, call, baseline


def im_candidate(threshold: float, discount: pd.DataFrame, upstream: pd.DataFrame, active: pd.DataFrame,
                 options: pd.DataFrame, base: pd.DataFrame, grid: pd.DataFrame, call: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    legs, all_trades, activity = [], [], []
    for sleeve in ("core", "mom"):
        schedule = pd.read_csv(im.SOURCE / f"real_combined_{sleeve}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"])
        schedule = schedule.merge(discount[["date", "annualized_discount"]], left_on="eval_date", right_on="date", how="left", validate="one_to_one")
        regime = schedule.annualized_discount.lt(threshold).fillna(False)
        raw_target = schedule.binary_target_qty.astype(int).copy()
        schedule.loc[regime, "binary_target_qty"] = 0
        schedule["three_tier_target_qty"] = schedule.binary_target_qty
        schedule["put_buy_allowed"] = True
        leg, trades = im.run_leg(sleeve, schedule.drop(columns="date"), upstream, options, active)
        legs.append(leg)
        all_trades.append(trades)
        activity.append(dict(sleeve=sleeve, regime_days=int(regime.sum()),
                             target_removed_evaluations=int((regime & raw_target.gt(0)).sum()),
                             target_qty_removed=int(raw_target[regime].sum())))
    put = sum(leg[im.source.first.FIELDS] for leg in legs)
    put["date"] = base.date
    name = f"im_lt_{threshold:.3f}"
    daily = im.source.comp.compose(base, put, grid, call)
    return daily, dict(product="IM", candidate=name, threshold=threshold, threshold_pct=threshold * 100,
                       regime_days=int(sum(x["regime_days"] for x in activity) / len(activity)),
                       regime_fraction=float((discount.annualized_discount.lt(threshold)).mean()),
                       target_removed_evaluations=sum(x["target_removed_evaluations"] for x in activity),
                       target_qty_removed=sum(x["target_qty_removed"] for x in activity),
                       trade_events=sum(len(x) for x in all_trades))


def main() -> None:
    if (OUT / "scan_summary.csv").exists():
        raise FileExistsError(f"scan results already exist: {OUT}")
    frame, ic_schedule, ic_base, ic_parity, frames, market, rolls = ic_setup()
    discount, upstream, active, options, im_base, grid, call, im_baseline = im_setup()
    rows, activity = [], []
    for product, candidate, scopes in (("IC", "ic_baseline", [("mixed", ic_base, set()), ("real_option", ic_base[ic_base.date.ge(ic.REAL_START)], {"10y", "5y"})]),
                                       ("IM", "im_baseline", [("real_option", im_baseline, {"10y", "5y"})])):
        for scope, daily, unavailable in scopes:
            rows.extend(metric_rows(product, candidate, scope, daily, unavailable))
    for threshold in THRESHOLDS:
        ic_daily, ic_activity = ic_candidate(threshold, frame, ic_schedule, frames, market, rolls)
        im_daily, im_activity = im_candidate(threshold, discount, upstream, active, options, im_base, grid, call)
        activity.extend((ic_activity, im_activity))
        rows.extend(metric_rows("IC", ic_activity["candidate"], "mixed", ic_daily, set()))
        rows.extend(metric_rows("IC", ic_activity["candidate"], "real_option", ic_daily[ic_daily.date.ge(ic.REAL_START)], {"10y", "5y"}))
        rows.extend(metric_rows("IM", im_activity["candidate"], "real_option", im_daily, {"10y", "5y"}))
        print(f"completed threshold={threshold:.3f}", flush=True)
    summary = pd.DataFrame(rows)
    activity_frame = pd.DataFrame(activity)
    baselines = summary[summary.candidate.isin(["ic_baseline", "im_baseline"])][["product", "scope", "window", "ann_return", "max_dd"]].rename(columns={"ann_return": "baseline_ann_return", "max_dd": "baseline_max_dd"})
    comparison = summary.merge(baselines, on=["product", "scope", "window"], how="left")
    comparison["ann_return_delta"] = pd.to_numeric(comparison.ann_return, errors="coerce") - pd.to_numeric(comparison.baseline_ann_return, errors="coerce")
    comparison["max_dd_delta"] = pd.to_numeric(comparison.max_dd, errors="coerce") - pd.to_numeric(comparison.baseline_max_dd, errors="coerce")
    summary.to_csv(OUT / "scan_summary.csv", index=False)
    comparison.to_csv(OUT / "window_metrics.csv", index=False)
    activity_frame.to_csv(OUT / "gate_activity.csv", index=False)
    meta_path = OUT / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(dict(phase="analysis_complete_pending_finalization", scan_type="predeclared_threshold_scan",
                     baseline={"IC": "unchanged independent combined Put replay", "IM": "unchanged real combined Put replay"},
                     candidate_grid=[float(x) for x in THRESHOLDS],
                     data_snapshot={"IC": "2015-04-16..2026-08-14; real option 2022-09-19..2026-08-14", "IM": "2022-07-22..2026-09-07 real IM/MO"},
                     cost_model="existing official replay fees, 30% futures buffer, residual cash annualized 3%; no bid-ask, impact, capacity, forced liquidation, or integer-account mapping",
                     timing="T close active futures settlement and index close; threshold action applies to T+1 Put target; target zero exits existing Put under existing engine",
                     outputs={**meta["outputs"], "gate_activity": str(OUT / "gate_activity.csv")},
                     baseline_parity={"IC": ic_parity},
                     source_sha256={p: sha256(ROOT / p) for p in ["research_ic_put_discount_gate_threshold_scan_v1.py", "research_ic_put_discount_gate_5pct_v1.py", "research_im_put_discount_gate_5pct_v1.py"]},
                     limitations="IC mixed history before 2022-09-19 has theoretical 510500 Put; IC real-option 5Y/10Y and IM 5Y/10Y are unavailable. This scan is a data-mined sensitivity test, not a promotion rule."))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC / IM 年化贴水低于阈值时 Put 归零扫描\n\n"
    record += "研究限定：阈值为 0%、0.5%、…、5%；T 收盘观察，T+1 把 Put 目标置零，已有 Put 依现有引擎退出。未修改正式信号、账本或订单。\n\n"
    record += "## 全样本与真实期权窗口结果\n\n" + comparison.to_string(index=False) + "\n\n"
    record += "## 门控强度\n\n" + activity_frame.to_string(index=False) + "\n\n"
    record += f"IC基线重放最大误差：{ic_parity}。\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    with (OUT / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python -X utf8 research_ic_put_discount_gate_threshold_scan_v1.py\n")


if __name__ == "__main__":
    main()
