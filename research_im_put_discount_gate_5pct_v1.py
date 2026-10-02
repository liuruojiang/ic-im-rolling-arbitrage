"""Research-only: block IM long-Put targets when annualized spot/future discount is <5%."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "quant_param_scan_runs" / "20260908_im_mom120_put102_combined_v1"
OUT = ROOT / "quant_param_scan_runs" / "20260914_im_put_discount_gate_5pct_v1"
THRESHOLD = 0.05

sys.path.insert(0, str(SOURCE))
import run_combined as source  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(frame: pd.DataFrame, end: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for window, years in (("full", None), ("10y", 10), ("5y", 5), ("3y", 3), ("1y", 1)):
        start = frame.date.min() if years is None else end - pd.DateOffset(years=years)
        if start < frame.date.min():
            rows.append({"window": window, "available": False, "reason": "real IM/MO history insufficient", "rows": 0})
            continue
        sample = frame[frame.date.ge(start)].copy()
        ret = sample.ret.astype(float)
        nav = (1.0 + ret).cumprod()
        rows.append({
            "window": window, "available": True, "reason": "", "start": str(sample.date.min().date()),
            "end": str(sample.date.max().date()), "rows": len(sample),
            "ann_return": float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0),
            "ann_vol": float(ret.std(ddof=1) * np.sqrt(252.0)),
            "max_dd": float((nav / nav.cummax() - 1.0).min()),
            "total_return": float(nav.iloc[-1] - 1.0),
        })
    return rows


def discount_states() -> pd.DataFrame:
    upstream = pd.read_csv(source.BASE / "real_upstream.csv.gz", parse_dates=["date"])
    options = pd.read_csv(source.BASE / "real_options.csv.gz", parse_dates=["contract_month", "actual_expiry"])
    expiry = options[["contract_month", "actual_expiry"]].drop_duplicates()
    if not expiry.groupby("contract_month").actual_expiry.nunique().eq(1).all():
        raise RuntimeError("MO actual-expiry map is ambiguous")
    expiry = expiry.set_index("contract_month")["actual_expiry"]
    upstream["contract_month"] = pd.to_datetime("20" + upstream.contract.str[2:6], format="%Y%m")
    upstream["actual_expiry"] = upstream.contract_month.map(expiry)
    upstream["days_to_expiry"] = (upstream.actual_expiry - upstream.date).dt.days
    if upstream.actual_expiry.isna().any() or upstream.days_to_expiry.le(0).any():
        raise RuntimeError("Missing or non-positive active-IM time to expiry")
    upstream["annualized_discount"] = (
        (upstream.csi1000_price_close / upstream.settle - 1.0) * 365.0 / upstream.days_to_expiry
    )
    upstream["put_allowed"] = upstream.annualized_discount.ge(THRESHOLD)
    return upstream[["date", "contract", "settle", "csi1000_price_close", "actual_expiry", "days_to_expiry", "annualized_discount", "put_allowed"]]


def gated_schedule(name: str, discount: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    schedule = pd.read_csv(SOURCE / f"real_combined_{name}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"])
    state = schedule.merge(discount, left_on="eval_date", right_on="date", how="left", validate="one_to_one")
    # The initial 2022-07-21 evaluation precedes the verified real IM sample.
    # Preserve its original target rather than inventing an unavailable prior-close discount.
    state["put_allowed"] = state.put_allowed.fillna(True).astype(bool)
    state["raw_target_qty"] = state.binary_target_qty.astype(int)
    state["discount_gate_blocked"] = state.raw_target_qty.gt(0) & ~state.put_allowed
    state.loc[state.discount_gate_blocked, "binary_target_qty"] = 0
    state["three_tier_target_qty"] = state.binary_target_qty
    state["put_buy_allowed"] = state.put_allowed
    return state.drop(columns="date"), state


def run_leg(name: str, schedule: pd.DataFrame, upstream: pd.DataFrame, options: pd.DataFrame, active: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    put, trades, _ = source.engine.run_real_monthly_close(
        upstream, options, active, schedule, "3m", 1.02, f"discount5_{name}", reset_dates=source.engine.monthly_dates(upstream.date)
    )
    put[source.first.FIELDS] *= 0.25 / 4.0
    trades["sleeve"] = name
    return put, trades


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"Immutable research output already exists: {OUT}")
    OUT.mkdir(parents=True)
    if not json.loads((SOURCE / "verification.json").read_text(encoding="utf-8"))["passed"]:
        raise RuntimeError("Selected IM combined baseline is not verified")

    discount = discount_states()
    upstream = pd.read_csv(source.BASE / "real_upstream.csv.gz", parse_dates=["date"])
    active = pd.read_csv(source.BASE / "real_active.csv.gz", parse_dates=["date"])
    raw = pd.read_csv(source.BASE / "real_options.csv.gz", parse_dates=["date", "contract_month", "rule_expiry", "actual_expiry"])
    options = source.engine.with_execution_prices(raw)
    base = pd.read_csv(SOURCE / "real_fixed_base.csv.gz", parse_dates=["date"])
    grid = pd.read_csv(SOURCE / "real_fixed_grid.csv.gz", parse_dates=["date"])
    call = pd.read_csv(SOURCE / "real_fixed_call.csv.gz", parse_dates=["date"])
    baseline = pd.read_csv(SOURCE / "real_combined_daily.csv.gz", parse_dates=["date"])

    legs, trades, schedules = [], [], []
    for sleeve in ("core", "mom"):
        runner_schedule, audit_schedule = gated_schedule(sleeve, discount)
        leg, leg_trades = run_leg(sleeve, runner_schedule, upstream, options, active)
        legs.append(leg)
        trades.append(leg_trades)
        schedules.append(audit_schedule.assign(sleeve=sleeve))
    put = sum(leg[source.first.FIELDS] for leg in legs)
    put["date"] = base.date
    gated = source.comp.compose(base, put, grid, call)
    if not gated.date.equals(baseline.date):
        raise RuntimeError("Gated and baseline calendars differ")

    # Baseline parity is proven by rerunning the unchanged schedules through the same engine.
    baseline_legs = []
    for sleeve in ("core", "mom"):
        original = pd.read_csv(SOURCE / f"real_combined_{sleeve}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"])
        leg, _ = run_leg(sleeve, original, upstream, options, active)
        baseline_legs.append(leg)
    rerun_put = sum(leg[source.first.FIELDS] for leg in baseline_legs)
    rerun_put["date"] = base.date
    rerun = source.comp.compose(base, rerun_put, grid, call)
    parity = {column: float((rerun[column] - baseline[column]).abs().max()) for column in ("ret", "nav", "cash_weight", *source.first.FIELDS)}
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"Baseline replay parity failed: {parity}")

    end = pd.Timestamp(gated.date.max())
    comparison: list[dict[str, object]] = []
    for label, frame in (("baseline_combined", baseline), ("discount_lt5pct_no_put", gated)):
        for row in metrics(frame, end):
            comparison.append({"candidate": label, **row})
    comparison_frame = pd.DataFrame(comparison)
    gate_summary = pd.DataFrame([
        {
            "threshold": THRESHOLD, "real_dates": len(discount), "discount_lt_threshold_days": int((~discount.put_allowed).sum()),
            "discount_lt_threshold_fraction": float((~discount.put_allowed).mean()),
            "annualized_discount_min": float(discount.annualized_discount.min()),
            "annualized_discount_median": float(discount.annualized_discount.median()),
            "annualized_discount_max": float(discount.annualized_discount.max()),
        }
    ])
    schedule_audit = pd.concat(schedules, ignore_index=True)
    change_summary = schedule_audit.groupby("sleeve", as_index=False).agg(
        positive_target_evaluations=("raw_target_qty", lambda s: int(s.gt(0).sum())),
        gate_blocked_evaluations=("discount_gate_blocked", "sum"),
        total_target_removed=("raw_target_qty", lambda s: int(s[schedule_audit.loc[s.index, "discount_gate_blocked"]].sum())),
    )
    all_trades = pd.concat(trades, ignore_index=True)
    all_trades.to_csv(OUT / "gated_put_trades.csv.gz", index=False, compression="gzip")
    gated.to_csv(OUT / "gated_daily.csv.gz", index=False, compression="gzip")
    baseline.to_csv(OUT / "baseline_daily.csv.gz", index=False, compression="gzip")
    discount.to_csv(OUT / "daily_discount_state.csv.gz", index=False, compression="gzip")
    schedule_audit.to_csv(OUT / "gated_schedules.csv.gz", index=False, compression="gzip")
    comparison_frame.to_csv(OUT / "window_comparison.csv", index=False)
    gate_summary.to_csv(OUT / "gate_summary.csv", index=False)
    change_summary.to_csv(OUT / "schedule_change_summary.csv", index=False)

    common = comparison_frame.pivot(index="window", columns="candidate", values=["ann_return", "max_dd"])
    common.columns = ["_".join(column) for column in common.columns]
    common = common.reset_index()
    common["ann_return_delta"] = common["ann_return_discount_lt5pct_no_put"] - common["ann_return_baseline_combined"]
    common["max_dd_delta"] = common["max_dd_discount_lt5pct_no_put"] - common["max_dd_baseline_combined"]
    common.to_csv(OUT / "common_window_deltas.csv", index=False)
    source_paths = [Path(__file__), SOURCE / "real_combined_daily.csv.gz", SOURCE / "real_combined_core_schedule.csv.gz", SOURCE / "real_combined_mom_schedule.csv.gz", source.BASE / "real_upstream.csv.gz", source.BASE / "real_active.csv.gz", source.BASE / "real_options.csv.gz"]
    meta = {
        "status": "research_only_not_live_or_production_change", "threshold": THRESHOLD,
        "definition": "((CSI1000 price close / active IM settle) - 1) * 365 / calendar days to the active IM contract actual expiry",
        "signal_timing": "evaluation-date close, applied to next-session Put target", "gate_action": "sets each long-Put sleeve target to zero; an existing Put is therefore exited by the existing engine rather than rolled or newly bought",
        "initial_exception": "2022-07-21 has no verified prior real IM row, so the pre-listing initial target is unchanged",
        "baseline_replay_max_abs_error": parity, "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in source_paths},
        "limitations": "Real IM/MO sample only; no model result because model futures use an imposed carry proxy, not observed discount. Uses close/settlement marks and existing fee, cash, grid, Call, and 30% margin assumptions. No bid-ask, impact, capacity, forced liquidation, or account-integer mapping.",
    }
    (OUT / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IM 年化贴水低于 5% 时不续买 Put：真实样本测试\n\n"
    record += "状态：研究测试；不改 v1.3-r7 信号、账本或下单。\n\n"
    record += "## 规则\n\n"
    record += "以前收盘可见的活跃 IM 结算价和中证1000价格指数收盘价计算年化贴水；低于 5% 时，下一交易日核心与动量 Put 目标均置零。现有 Put 因而按原引擎平仓、不滚动或新买。\n\n"
    record += "## 结果\n\n" + common.to_string(index=False) + "\n\n## 使用强度\n\n" + gate_summary.to_string(index=False) + "\n\n" + change_summary.to_string(index=False) + "\n\n## 验证\n\n"
    record += "未改门控的两条腿用同一引擎重放，逐日收益、净值、现金权重和所有 Put 字段最大误差均不超过 1e-12。\n"
    (OUT / "record.md").write_text(record, encoding="utf-8")
    (OUT / "command_log.txt").write_text("python -X utf8 research_im_put_discount_gate_5pct_v1.py\n", encoding="utf-8")
    print(common.to_string(index=False))
    print(gate_summary.to_string(index=False))
    print(change_summary.to_string(index=False))


if __name__ == "__main__":
    main()
