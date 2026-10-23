"""Research-only IC counterpart to the IM 5% annualized-discount Put gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import ic_roll_momentum_stage2_put_v2 as ic_put
import run_ic_v13_sleeve_put_independent_replay_v1 as baseline


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "quant_param_scan_runs" / "20260914_ic_put_discount_gate_5pct_v3"
THRESHOLD = 0.05
REAL_START = baseline.REAL_START


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric_rows(frame: pd.DataFrame, unavailable_long_windows: bool = False) -> list[dict[str, object]]:
    end = frame.date.max()
    rows = []
    for window, years in (("full", None), ("10y", 10), ("5y", 5), ("3y", 3), ("1y", 1)):
        if unavailable_long_windows and years in (5, 10):
            rows.append({"window": window, "available": False, "reason": "actual 510500 Put history shorter than requested window", "rows": 0, "ann_return": np.nan, "max_dd": np.nan})
            continue
        start = frame.date.min() if years is None else end - pd.DateOffset(years=years)
        sample = frame[frame.date.ge(start)]
        nav = (1.0 + sample.ret.astype(float)).cumprod()
        rows.append({"window": window, "available": True, "reason": "", "start": str(sample.date.min().date()), "end": str(sample.date.max().date()), "rows": len(sample), "ann_return": float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0), "max_dd": float((nav / nav.cummax() - 1.0).min())})
    return rows


def annualized_discount(ic: pd.DataFrame) -> pd.DataFrame:
    futures = pd.read_csv(ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv", parse_dates=["date"])
    expiry = futures.groupby("contract", as_index=False).date.max().rename(columns={"date": "actual_expiry"})
    state = ic[["date", "contract", "settle", "csi500_price_close"]].merge(expiry, on="contract", validate="many_to_one")
    state["days_to_expiry"] = (state.actual_expiry - state.date).dt.days
    if state.days_to_expiry.lt(0).any():
        raise RuntimeError("IC quote appears after its actual final trading date")
    # An expiry-day close has no remaining holding period to annualize.  It is
    # not used as a new gate observation; the next contract's prior close will
    # be used on the following evaluation.
    state["annualized_discount"] = np.where(
        state.days_to_expiry.gt(0),
        (state.csi500_price_close / state.settle - 1.0) * 365.0 / state.days_to_expiry,
        np.nan,
    )
    state["put_allowed"] = state.annualized_discount.ge(THRESHOLD) | state.annualized_discount.isna()
    return state


def gated_schedule(selected: pd.DataFrame, discount: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    schedule = baseline.build_schedule(selected, "combined_current")
    known = discount[["date", "contract", "actual_expiry", "days_to_expiry", "annualized_discount", "put_allowed"]].sort_values("date")
    audit = pd.merge_asof(schedule.sort_values("eval_date"), known, left_on="eval_date", right_on="date", direction="backward", allow_exact_matches=True).drop(columns="date")
    # No observed IC close exists for the one initial pre-sample evaluation.
    audit["put_allowed"] = audit.put_allowed.astype("boolean").fillna(True).astype(bool)
    audit["raw_target_delta"] = audit.target_delta.astype(float)
    audit["discount_gate_blocked"] = audit.raw_target_delta.gt(0) & ~audit.put_allowed
    audit.loc[audit.discount_gate_blocked, ["target_delta", "target_fraction", "binary_target_fraction", "three_tier_target_fraction"]] = 0.0
    return audit, audit.copy()


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"Immutable research output exists: {OUT}")
    OUT.mkdir(parents=True)
    frame, _, selected = baseline.load_base_components()
    frames, _, market, _ = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll_dates = ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    discount = annualized_discount(frames["ic"])
    schedule, schedule_audit = gated_schedule(selected, discount)
    gated_ledger, gated_trades = baseline.run_ledger("discount5_combined", schedule, frames, market, roll_dates)
    gated = baseline.combine_candidate(frame, {"discount5_combined": gated_ledger}, "discount_lt5pct_no_put", ("discount5_combined",))

    original_schedule = baseline.build_schedule(selected, "combined_current")
    rerun_ledger, _ = baseline.run_ledger("baseline_combined", original_schedule, frames, market, roll_dates)
    rerun = baseline.combine_candidate(frame, {"baseline_combined": rerun_ledger}, "baseline_combined", ("baseline_combined",))
    official = pd.read_csv(baseline.OFFICIAL_DAILY, parse_dates=["date"])
    parity = {column: float((rerun[column] - official[column]).abs().max()) for column in ("ret", "cash_weight")}
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"IC baseline parity failed: {parity}")

    compare = []
    for label, daily in (("baseline_combined", rerun), ("discount_lt5pct_no_put", gated)):
        for scope, sample in (("mixed", daily), ("real_option", daily[daily.date.ge(REAL_START)])):
            for row in metric_rows(sample, unavailable_long_windows=scope == "real_option"):
                compare.append({"candidate": label, "scope": scope, **row})
    comparison = pd.DataFrame(compare)
    wide = comparison.pivot(index=["scope", "window"], columns="candidate", values=["ann_return", "max_dd"]).reset_index()
    wide.columns = ["_".join(x).strip("_") if isinstance(x, tuple) else x for x in wide.columns]
    wide["ann_return_delta"] = wide["ann_return_discount_lt5pct_no_put"] - wide["ann_return_baseline_combined"]
    wide["max_dd_delta"] = wide["max_dd_discount_lt5pct_no_put"] - wide["max_dd_baseline_combined"]
    gate_summary = pd.DataFrame([{"threshold": THRESHOLD, "dates": len(discount), "below_threshold_days": int((~discount.put_allowed).sum()), "below_threshold_fraction": float((~discount.put_allowed).mean()), "discount_min": float(discount.annualized_discount.min()), "discount_median": float(discount.annualized_discount.median()), "discount_max": float(discount.annualized_discount.max()), "baseline_avg_put_mark": float(rerun.put_mark_fraction.mean()), "gated_avg_put_mark": float(gated.put_mark_fraction.mean())}])
    schedule_summary = pd.DataFrame([{"positive_target_evaluations": int(schedule_audit.raw_target_delta.gt(0).sum()), "blocked_evaluations": int(schedule_audit.discount_gate_blocked.sum()), "target_delta_removed": float(schedule_audit.loc[schedule_audit.discount_gate_blocked, "raw_target_delta"].sum()), "gated_trade_events": len(gated_trades)}])

    gated.to_csv(OUT / "gated_daily.csv.gz", index=False, compression="gzip")
    rerun.to_csv(OUT / "baseline_daily.csv.gz", index=False, compression="gzip")
    discount.to_csv(OUT / "daily_discount_state.csv.gz", index=False, compression="gzip")
    schedule_audit.to_csv(OUT / "gated_schedule.csv.gz", index=False, compression="gzip")
    gated_trades.to_csv(OUT / "gated_put_trades.csv.gz", index=False, compression="gzip")
    comparison.to_csv(OUT / "window_comparison.csv", index=False)
    wide.to_csv(OUT / "common_window_deltas.csv", index=False)
    gate_summary.to_csv(OUT / "gate_summary.csv", index=False)
    schedule_summary.to_csv(OUT / "schedule_change_summary.csv", index=False)
    meta = {"status": "research_only_not_a_signal_or_order_change", "threshold": THRESHOLD, "definition": "((CSI500 price close / active IC settlement) - 1) * 365 / calendar days to the active IC contract actual final trading date", "timing": "evaluation-date close, applied to next-session Put target", "action": "below 5% sets the combined IC Put target to zero; existing Put exits under the existing engine rather than rolling or buying", "baseline_parity": parity, "source_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in (Path(__file__), baseline.OFFICIAL_DAILY, ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv")}, "limitations": "Mixed period before 2022-09-19 has theoretical 510500 Put. Real-option results are reported separately; 5Y/10Y actual-Option history is unavailable. Existing official close/settlement pricing, fees, 30% buffer and cash assumptions are retained; no bid-ask, capacity or forced-liquidation model."}
    (OUT / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC 年化贴水低于 5% 时不续买 Put：测试\n\n状态：研究测试；不改 IC v1.3-r7 信号、账本或订单。\n\n"
    record += "定义：以 T 收盘活跃 IC 结算价与中证500价格指数收盘价，按距该合约实际最后交易日的日历天数折算年化贴水；低于 5% 时，T+1 合并 Put 目标归零，旧 Put 按原引擎平仓、不续买。\n\n"
    record += "## 结果\n\n" + wide.to_string(index=False) + "\n\n## 使用强度\n\n" + gate_summary.to_string(index=False) + "\n\n" + schedule_summary.to_string(index=False) + "\n\n## 验证\n\n未门控的现行合并 Put 用同一引擎重放，与权威日收益/现金权重最大误差均不超过 1e-12。\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    (OUT / "command_log.txt").write_text("python -X utf8 research_ic_put_discount_gate_5pct_v1.py\n", encoding="utf-8")
    print(wide.to_string(index=False))
    print(gate_summary.to_string(index=False))


if __name__ == "__main__":
    main()
