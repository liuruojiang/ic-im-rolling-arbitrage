from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_front95_fixed_dynamic_momentum_validation_v5"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "507857d8f04aa0bdd05581f2c768268bde20dad330ddfd4bf27e3ed3850caa18"
OUTPUT = ROOT / "outputs" / VERSION

V4_PATH = Path(v4.__file__).resolve()
V4_SHA256 = "c654aa7c30c4a89954f8c7db7d352664ab3ac0c5455c2b26248c5aca75476461"
V4_MANIFEST = v4.OUTPUT / "data_manifest.json"
IM_QUOTES = ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_im_contracts.csv"

START = v4.START
END = v4.END
CASH_WEIGHT = v4.CASH_WEIGHT
CASH_DAILY = v4.CASH_DAILY
MO_SIDE_COST = v4.MO_CONTRACT_SIDE_COST

SIGNALS = [
    "mom120_only",
    "fixed175_only",
    "dynamic075_only",
    "fixed175_or_mom120",
    "dynamic075_or_mom120",
]
CANDIDATES = ["no_put", *SIGNALS]
REQUIRED_WINDOWS = ["full", "last_10y", "last_5y", "last_3y", "last_1y"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v5 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v5 specification sidecar mismatch")
    if sha256(V4_PATH) != V4_SHA256:
        raise RuntimeError("Frozen v4 dependency changed")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    manifest = json.loads(V4_MANIFEST.read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V4_SHA256:
        raise RuntimeError("Frozen v4 formal manifest mismatch")
    return manifest


def fixed_risk(frame: pd.DataFrame) -> pd.Series:
    pb = np.select(
        [frame["pb_aggregate"].ge(2.50), frame["pb_aggregate"].ge(2.00)], [2.0, 1.0], default=0.0
    )
    erp = np.select([frame["erp"].le(0.015), frame["erp"].le(0.030)], [2.0, 1.0], default=0.0)
    dividend = np.select(
        [frame["trailing_dividend_contribution"].lt(0.010),
         frame["trailing_dividend_contribution"].lt(0.020)],
        [2.0, 1.0], default=0.0,
    )
    return pd.Series(0.25 * pb + 0.50 * erp + 0.25 * dividend, index=frame.index)


def dynamic_risk(frame: pd.DataFrame) -> pd.Series:
    values: list[float] = []
    indexed = frame.set_index("date")
    for row in frame.itertuples(index=False):
        day = pd.Timestamp(row.date)
        history = indexed.loc[(indexed.index >= day - pd.DateOffset(years=8)) & (indexed.index <= day)]
        pb = float((history["pb_aggregate"] <= float(row.pb_aggregate) + 1e-12).mean())
        erp = float((history["erp"] >= float(row.erp) - 1e-12).mean())
        dividend = float(
            (history["trailing_dividend_contribution"]
             >= float(row.trailing_dividend_contribution) - 1e-12).mean()
        )
        values.append(0.25 * pb + 0.50 * erp + 0.25 * dividend)
    return pd.Series(values, index=frame.index)


def build_signal_state(daily_valuation: pd.DataFrame) -> pd.DataFrame:
    tri = pd.read_csv(v4.TRI, parse_dates=["date"])[["date", "close"]].rename(
        columns={"close": "tri_close_signal"}
    )
    frame = daily_valuation.merge(tri, on="date", how="left", validate="one_to_one")
    frame["momentum_120"] = frame["tri_close_signal"] / frame["tri_close_signal"].shift(120) - 1.0
    frame["fixed_risk"] = fixed_risk(frame)
    frame["dynamic_risk"] = dynamic_risk(frame)
    frame["mom120_target"] = frame["momentum_120"].le(1e-12).astype(int) * 2
    frame["fixed175_target"] = frame["fixed_risk"].ge(1.75 - 1e-12).astype(int) * 2
    frame["dynamic075_target"] = frame["dynamic_risk"].ge(0.75 - 1e-12).astype(int) * 2
    frame["fixed175_or_mom120_target"] = frame[["fixed175_target", "mom120_target"]].max(axis=1)
    frame["dynamic075_or_mom120_target"] = frame[["dynamic075_target", "mom120_target"]].max(axis=1)
    required = [
        "momentum_120", "fixed_risk", "dynamic_risk", "mom120_target", "fixed175_target",
        "dynamic075_target", "fixed175_or_mom120_target", "dynamic075_or_mom120_target",
    ]
    sample = frame[frame["date"] >= pd.Timestamp("2022-07-21")]
    if sample[required].isna().any().any():
        raise RuntimeError("Incomplete v5 signal state")
    return frame


def target_column(signal: str) -> str:
    return {
        "mom120_only": "mom120_target",
        "fixed175_only": "fixed175_target",
        "dynamic075_only": "dynamic075_target",
        "fixed175_or_mom120": "fixed175_or_mom120_target",
        "dynamic075_or_mom120": "dynamic075_or_mom120_target",
    }[signal]


def build_schedules(
    upstream: pd.DataFrame, signal_state: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    trade_dates = pd.DatetimeIndex(upstream["date"])
    initial = pd.Timestamp(signal_state[signal_state["date"] < START]["date"].max())
    eval_dates = [initial, *[pd.Timestamp(value) for value in trade_dates if value < END]]
    state = signal_state.set_index("date")
    schedules: dict[str, pd.DataFrame] = {}
    history_rows: list[dict[str, object]] = []
    for day in sorted(set([*eval_dates, END])):
        row = state.loc[day]
        for signal in SIGNALS:
            history_rows.append({
                "signal_variant": signal,
                "eval_date": day,
                "momentum_120": float(row["momentum_120"]),
                "fixed_risk": float(row["fixed_risk"]),
                "dynamic_risk": float(row["dynamic_risk"]),
                "target_qty": int(row[target_column(signal)]),
                "target_fraction": float(row[target_column(signal)] / 2.0),
                "current_state_only": bool(day == END),
            })
    history = pd.DataFrame(history_rows)
    for signal in SIGNALS:
        rows: list[dict[str, object]] = []
        for sequence, day in enumerate(eval_dates):
            execution, initial_exception = v4.next_execution(day, trade_dates)
            target = int(state.loc[day, target_column(signal)])
            rows.append({
                "frequency": signal,
                "sequence": sequence,
                "eval_date": day,
                "execution_date": execution,
                "initial_listing_exception": initial_exception,
                "binary_target_qty": target,
                "three_tier_target_qty": target,
            })
        schedule = pd.DataFrame(rows)
        regular = schedule[~schedule["initial_listing_exception"]]
        if (regular["execution_date"] <= regular["eval_date"]).any():
            raise RuntimeError(f"Signal execution leakage: {signal}")
        schedules[signal] = schedule
    combined = pd.concat(schedules.values(), ignore_index=True)
    current = history[history["current_state_only"]].copy()
    return schedules, history, current


def active_im_opens(upstream: pd.DataFrame) -> pd.DataFrame:
    quotes = pd.read_csv(IM_QUOTES, parse_dates=["date"])[["contract", "date", "open", "volume"]]
    active = upstream[["date", "contract"]].merge(
        quotes, on=["date", "contract"], how="left", validate="one_to_one"
    )
    if active[["open", "volume"]].isna().any().any() or (active["open"] <= 0).any():
        raise RuntimeError("Missing active IM opening quote")
    return active


def target95_selector(active: pd.DataFrame):
    im_open = active.set_index("date")["open"]

    def select(options: pd.DataFrame, day: pd.Timestamp, month: pd.Timestamp) -> pd.Series | None:
        chain = options[(options["date"] == day) & (options["contract_month"] == month)].copy()
        if chain.empty:
            return None
        liquid = chain[
            chain["open"].notna() & chain["open"].gt(0)
            & chain["volume"].gt(0) & chain["open_interest"].gt(0)
        ].copy()
        if liquid.empty:
            return None
        liquid["entry_moneyness"] = liquid["strike"] / float(im_open.loc[day])
        liquid["target_error"] = (liquid["entry_moneyness"] - 0.95).abs().round(12)
        row = liquid.sort_values(["target_error", "strike", "contract"]).iloc[0].copy()
        row["literal_min_strike"] = float(chain["strike"].min())
        row["liquidity_fallback"] = False
        return row

    return select


def run_overlays(
    upstream: pd.DataFrame,
    raw_options: pd.DataFrame,
    schedules: dict[str, pd.DataFrame],
    active: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    selector = target95_selector(active)
    original = v4.lowest_liquid
    overlays: dict[str, pd.DataFrame] = {}
    trades: list[pd.DataFrame] = []
    v4.lowest_liquid = selector
    try:
        for signal in SIGNALS:
            overlay, trade = v4.run_candidate(
                upstream, schedules[signal], options, signal, "front", "binary", signal
            )
            overlays[signal] = overlay
            if len(trade):
                trades.append(trade)
    finally:
        v4.lowest_liquid = original
    trade_table = pd.concat(trades, ignore_index=True)
    if len(trade_table):
        trade_table = trade_table.merge(
            active[["date", "open"]].rename(columns={"date": "actual_execution_date", "open": "active_im_open"}),
            on="actual_execution_date", how="left", validate="many_to_one",
        )
        trade_table["entry_moneyness"] = trade_table["new_strike"] / trade_table["active_im_open"]
        trade_table["target_moneyness_error"] = (trade_table["entry_moneyness"] - 0.95).abs()
        trade_dates = pd.DatetimeIndex(upstream["date"])
        scheduled = pd.to_datetime(trade_table["scheduled_execution_date"])
        actual = pd.to_datetime(trade_table["actual_execution_date"])
        first_eligible = trade_dates.searchsorted(scheduled, side="left")
        actual_location = trade_dates.searchsorted(actual, side="left")
        trade_table["delay_trading_days"] = actual_location - first_eligible
        if (trade_table["delay_trading_days"] < 0).any():
            raise RuntimeError("Negative execution delay")
    return overlays, trade_table


def assemble(upstream: pd.DataFrame, overlays: dict[str, pd.DataFrame]) -> pd.DataFrame:
    daily = upstream.copy()
    daily["no_put_ret"] = daily["baseline_net_ret"]
    daily["no_put_cash_ret"] = daily["baseline_plus_cash_ret"]
    for signal, overlay in overlays.items():
        daily = daily.merge(overlay.drop(columns=["settle"]), on="date", validate="one_to_one")
        daily[f"{signal}_gross_ret"] = daily["im_gross_ret"] + daily[f"{signal}_put_pnl_ret"]
        daily[f"{signal}_ret"] = (
            (1.0 + daily[f"{signal}_gross_ret"])
            * (1.0 - daily["cost_rate"])
            * (1.0 - daily[f"{signal}_put_cost_rate"])
            - 1.0
        )
        daily[f"{signal}_cash_weight"] = (
            CASH_WEIGHT - daily[f"{signal}_put_mark_notional"]
        ).clip(lower=0.0)
        daily[f"{signal}_cash_ret"] = (
            daily[f"{signal}_ret"] + daily[f"{signal}_cash_weight"] * CASH_DAILY
        )
    if (daily["no_put_ret"] - daily["baseline_net_ret"]).abs().max() > 1e-14:
        raise RuntimeError("v5 no-Put net parity failed")
    if (daily["no_put_cash_ret"] - daily["baseline_plus_cash_ret"]).abs().max() > 1e-14:
        raise RuntimeError("v5 no-Put cash parity failed")
    core = ["no_put_ret", "no_put_cash_ret", *[f"{value}_ret" for value in SIGNALS],
            *[f"{value}_cash_ret" for value in SIGNALS]]
    if daily[core].isna().any().any() or (daily[core] <= -1).any().any():
        raise RuntimeError("Invalid v5 daily returns")
    return daily


def ret_col(candidate: str, cash: bool = True) -> str:
    return f"{candidate}_{'cash_ret' if cash else 'ret'}"


def metric_outputs(daily: pd.DataFrame) -> pd.DataFrame:
    start, end = pd.Timestamp(daily["date"].min()), pd.Timestamp(daily["date"].max())
    windows = {
        "full": start,
        "last_10y": end - pd.DateOffset(years=10),
        "last_5y": end - pd.DateOffset(years=5),
        "last_3y": end - pd.DateOffset(years=3),
        "last_1y": end - pd.DateOffset(years=1),
    }
    rows: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        for window, cutoff in windows.items():
            available = window == "full" or start <= cutoff
            sample = daily[daily["date"] >= cutoff] if available else daily.iloc[0:0]
            row: dict[str, object] = {
                "candidate": candidate, "window": window, "available": available,
                "requested_start": cutoff, "actual_start": sample["date"].min() if available else pd.NaT,
                "end": end, "rows": len(sample),
            }
            if available:
                row.update(v4.metrics(sample[ret_col(candidate, False)]))
                row.update({f"cash_{key}": value for key, value in v4.metrics(sample[ret_col(candidate, True)]).items()})
            else:
                for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd",
                            "cash_total_return", "cash_ann_return", "cash_ann_vol",
                            "cash_sharpe_repo", "cash_max_dd"]:
                    row[key] = np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        for year, sample in daily.groupby(daily["date"].dt.year):
            values = v4.metrics(sample[ret_col(candidate, True)])
            rows.append({"candidate": candidate, "year": int(year), **values})
    return pd.DataFrame(rows)


def exposure_summary(daily: pd.DataFrame, trades: pd.DataFrame, schedules: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = [{
        "candidate": "no_put", "protected_days": 0, "protected_day_ratio": 0.0,
        "signal_active_ratio": 0.0, "signal_switches": 0, "trade_events": 0,
        "trade_sides": 0, "put_cost_sum": 0.0, "average_entry_moneyness": np.nan,
        "max_entry_moneyness_error": np.nan,
    }]
    for signal in SIGNALS:
        qty = daily[f"{signal}_put_qty_eod"]
        schedule = schedules[signal]
        targets = schedule["binary_target_qty"].astype(int)
        entries = trades[(trades["candidate"] == signal) & trades["new_contract"].fillna("").ne("")]
        rows.append({
            "candidate": signal,
            "protected_days": int(qty.gt(0).sum()),
            "protected_day_ratio": float(qty.gt(0).mean()),
            "signal_active_ratio": float(targets.gt(0).mean()),
            "signal_switches": int(targets.ne(targets.shift()).sum() - 1),
            "trade_events": int((trades["candidate"] == signal).sum()),
            "trade_sides": int(daily[f"{signal}_buy_qty"].sum() + daily[f"{signal}_sell_qty"].sum()),
            "put_cost_sum": float(daily[f"{signal}_put_cost_rate"].sum()),
            "average_entry_moneyness": float(entries["entry_moneyness"].mean()),
            "max_entry_moneyness_error": float(entries["target_moneyness_error"].max()),
        })
    return pd.DataFrame(rows)


def contract_audit(trades: pd.DataFrame, raw_options: pd.DataFrame, upstream: pd.DataFrame, active: pd.DataFrame) -> pd.DataFrame:
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    selector = target95_selector(active)
    rows: list[dict[str, object]] = []
    entries = trades[trades["new_contract"].fillna("").ne("")]
    for trade in entries.itertuples(index=False):
        day = pd.Timestamp(trade.actual_execution_date)
        month = pd.Timestamp(trade.desired_contract_month)
        selected = selector(options, day, month)
        expected = "" if selected is None else str(selected["contract"])
        rows.append({
            "candidate": trade.candidate, "actual_execution_date": day,
            "desired_contract_month": month, "actual_contract": trade.new_contract,
            "expected_contract": expected, "contract_match": str(trade.new_contract) == expected,
            "entry_moneyness": trade.entry_moneyness,
            "target_moneyness_error": trade.target_moneyness_error,
        })
    table = pd.DataFrame(rows)
    if table.empty or not table["contract_match"].all():
        raise RuntimeError("v5 contract selection audit failed")
    return table


def decision_summary(formal: pd.DataFrame) -> dict[str, object]:
    full = formal[(formal["window"] == "full") & formal["available"]].set_index("candidate")
    three = formal[(formal["window"] == "last_3y") & formal["available"]].set_index("candidate")
    one = formal[(formal["window"] == "last_1y") & formal["available"]].set_index("candidate")

    def delta(candidate: str, sample: pd.DataFrame) -> tuple[float, float]:
        return (
            float(sample.loc[candidate, "cash_ann_return"] - sample.loc["no_put", "cash_ann_return"]),
            float(sample.loc[candidate, "cash_max_dd"] - sample.loc["no_put", "cash_max_dd"]),
        )

    candidate_checks: dict[str, object] = {}
    for candidate in SIGNALS:
        full_cagr, full_dd = delta(candidate, full)
        three_cagr, three_dd = delta(candidate, three)
        one_cagr, one_dd = delta(candidate, one)
        supported = bool(
            full_dd >= 0.005 and full_cagr >= -0.01
            and three_cagr >= -0.03 and three_dd >= -0.01
            and one_cagr >= -0.03 and one_dd >= -0.01
        )
        candidate_checks[candidate] = {
            "full_cagr_delta": full_cagr, "full_dd_improvement": full_dd,
            "last3_cagr_delta": three_cagr, "last3_dd_improvement": three_dd,
            "last1_cagr_delta": one_cagr, "last1_dd_improvement": one_dd,
            "tool_supported": supported,
        }
    dynamic_fixed = {
        "full_cagr_delta": float(
            full.loc["dynamic075_or_mom120", "cash_ann_return"]
            - full.loc["fixed175_or_mom120", "cash_ann_return"]
        ),
        "full_dd_delta": float(
            full.loc["dynamic075_or_mom120", "cash_max_dd"]
            - full.loc["fixed175_or_mom120", "cash_max_dd"]
        ),
        "last3_cagr_delta": float(
            three.loc["dynamic075_or_mom120", "cash_ann_return"]
            - three.loc["fixed175_or_mom120", "cash_ann_return"]
        ),
        "last3_dd_delta": float(
            three.loc["dynamic075_or_mom120", "cash_max_dd"]
            - three.loc["fixed175_or_mom120", "cash_max_dd"]
        ),
    }
    dynamic_fixed["direction_reproduced"] = bool(
        dynamic_fixed["full_cagr_delta"] >= 0
        and dynamic_fixed["full_dd_delta"] >= -0.01
        and dynamic_fixed["last3_cagr_delta"] >= 0
        and dynamic_fixed["last3_dd_delta"] >= -0.01
    )
    fixed_supported = bool(candidate_checks["fixed175_or_mom120"]["tool_supported"])
    dynamic_supported = bool(candidate_checks["dynamic075_or_mom120"]["tool_supported"])
    if fixed_supported and dynamic_supported and dynamic_fixed["direction_reproduced"]:
        conclusion = "confirmed_on_im_mo"
    elif fixed_supported or dynamic_supported or dynamic_fixed["direction_reproduced"]:
        conclusion = "partly_confirmed"
    else:
        conclusion = "not_confirmed"
    return {
        "conclusion": conclusion,
        "research_status": "research_only_not_live_approved",
        "candidate_checks": candidate_checks,
        "dynamic_vs_fixed": dynamic_fixed,
        "sample_reuse": "not_independent_oos",
    }


def build_record(
    formal: pd.DataFrame, exposure: pd.DataFrame, current: pd.DataFrame, decision: dict[str, object]
) -> str:
    show = formal[formal["window"].isin(REQUIRED_WINDOWS)].copy()
    show["cagr"] = show["cash_ann_return"].where(show["available"])
    show["max_dd_display"] = show["cash_max_dd"].where(show["available"])
    table = show[["candidate", "window", "available", "cagr", "max_dd_display"]]
    return "\n".join([
        "# IM + MO 前月95%固定/动态估值与绝对动量验证 v5", "",
        "> 真实中金所IM/MO研究回测；未获准实盘。", "",
        "## 产品边界", "",
        "- 中金所没有中证500股指期权；本版验证的是同指数的IM+MO。",
        "- 正式样本2022-07-22至2026-08-14；10年/5年不可用。", "",
        "## Decision", "", f"- `{decision['conclusion']}`。", "",
        "## 强制窗口（含70%现金年化3%）", "",
        table.to_markdown(index=False, floatfmt=".4f"), "",
        "## 暴露、成本与流动性", "", exposure.to_markdown(index=False, floatfmt=".6f"), "",
        "## 样本末研究状态", "", current.to_markdown(index=False, floatfmt=".6f"), "",
        "## 限制", "",
        "- 固定1.75是从中证500迁移的绝对阈值，不是中证1000校准结果。",
        "- 官方日开盘/结算不代表盘口成交保证；未计冲击、经纪商附加保证金与结算附加费。",
        "- 历史只有约4年且多轮复用，不是独立OOS；研究状态不是订单。", "",
    ])


def main() -> None:
    v4_manifest = verify_inputs()
    upstream, _, _, _, _, raw_options = v4.load_inputs()
    daily_valuation, feature_diffs = v4.build_daily_valuation()
    if max(feature_diffs.values()) > 1e-14:
        raise RuntimeError(f"Daily valuation parity failed: {feature_diffs}")
    signal_state = build_signal_state(daily_valuation)
    schedules, signal_history, current = build_schedules(upstream, signal_state)
    active = active_im_opens(upstream)
    overlays, trades = run_overlays(upstream, raw_options, schedules, active)
    daily = assemble(upstream, overlays)
    formal = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_summary(daily, trades, schedules)
    contract = contract_audit(trades, raw_options, upstream, active)
    decision = decision_summary(formal)

    if daily["date"].duplicated().any() or len(daily) != 986:
        raise RuntimeError("Invalid v5 date grid")
    if set(exposure["candidate"]) != set(CANDIDATES):
        raise RuntimeError("Invalid v5 candidate set")
    if not contract["contract_match"].all():
        raise RuntimeError("Invalid v5 contract selection")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade before scheduled execution")
    if len(trades) and int(trades["delay_trading_days"].max()) > 5:
        raise RuntimeError("A single v5 adjustment was delayed by more than five trading days")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    signal_history.to_csv(OUTPUT / "signal_history.csv.gz", index=False, compression="gzip")
    pd.concat(schedules.values(), ignore_index=True).to_csv(
        OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip"
    )
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    contract.to_csv(OUTPUT / "contract_selection_audit.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record = build_record(formal, exposure, current, decision)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    inputs = [
        v4.UPSTREAM, v4.OPTIONS, IM_QUOTES, v4.VALUATION, v4.PRICE, v4.TRI, v4.GOV10Y,
        v4.MONTHLY_STATES,
    ]
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "dependency": {"v4_path": str(V4_PATH.relative_to(ROOT)), "v4_sha256": V4_SHA256},
        "inputs": {
            str(path.relative_to(ROOT)): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in inputs
        },
        "sample": {"start": str(START.date()), "end": str(END.date()), "rows": len(daily),
                   "timezone": "Asia/Shanghai"},
        "products": {
            "future": "IM CSI 1000 index future, multiplier CNY 200/point",
            "option": "MO CSI 1000 index option, multiplier CNY 100/point",
            "official_product_url": "https://www.cffex.com.cn/cn/zz1000gzqq.html",
        },
        "cost_model": {"im": "frozen upstream, 1bp per side", "mo_per_contract_side": MO_SIDE_COST,
                       "cash_weight": CASH_WEIGHT, "cash_annual_return": 0.03},
        "checks": {
            "daily_feature_month_end_max_abs": feature_diffs,
            "no_put_net_parity_max_abs": float((daily["no_put_ret"] - daily["baseline_net_ret"]).abs().max()),
            "no_put_cash_parity_max_abs": float((daily["no_put_cash_ret"] - daily["baseline_plus_cash_ret"]).abs().max()),
            "contract_selection_match": bool(contract["contract_match"].all()),
            "max_execution_delay_trading_days": (
                int(trades["delay_trading_days"].max()) if len(trades) else 0
            ),
            "candidate_count": len(CANDIDATES), "date_rows": len(daily),
        },
        "decision": decision,
        "v4_manifest_sha256": sha256(V4_MANIFEST),
        "v4_formal_input_hashes": v4_manifest.get("inputs", {}),
        "git_status": git_status(),
        "warnings": [
            "No CSI 500 index option exists; IM+MO is a cross-underlying validation",
            "Only about four years; no 10Y/5Y real window",
            "Fixed 1.75 is transferred, not calibrated for CSI 1000",
            "Official daily prices are not order-book fill guarantees",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(
        "python.exe -m pytest test_im_mo_front95_fixed_dynamic_momentum_validation_v5.py -q\n"
        "python.exe im_mo_front95_fixed_dynamic_momentum_validation_v5.py\n",
        encoding="utf-8",
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
