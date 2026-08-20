from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_monthly_roll_valuation_gated_put_v1"
OUTPUT_DIR = ROOT / "outputs" / VERSION
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.sha256"

PUT_V1_OUTPUT = ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1"
PUT_V1_DATA = ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1"
VALUATION_V3_OUTPUT = ROOT / "outputs" / "ic_im_valuation_risk_premium_forecast_v3"
VALUATION_V3_DATA = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3"

TRADING_DAYS = 252
PUT_ONE_WAY_COST = 0.0001
CASH_WEIGHT = 0.70
CASH_ANNUAL_RETURN = 0.03
CASH_DAILY_RETURN = (1.0 + CASH_ANNUAL_RETURN) ** (1.0 / TRADING_DAYS) - 1.0
MAX_ANALOGUES = 8
MIN_ANALOGUES = 4
MIN_GAP_MONTHS = 12
HORIZON_YEARS = 3
FEATURES = [
    "pe_aggregate_ttm",
    "pb_aggregate",
    "erp",
    "trailing_dividend_contribution",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> str:
    if not SPEC_PATH.exists() or not SPEC_HASH_PATH.exists():
        raise FileNotFoundError("Frozen spec or SHA-256 file is missing")
    expected = SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC_PATH)
    if expected != actual:
        raise RuntimeError(f"Frozen spec hash mismatch: expected {expected}, actual {actual}")
    return actual


def month_distance(left: pd.Timestamp, right: pd.Timestamp) -> int:
    return abs((left.year - right.year) * 12 + left.month - right.month)


def walk_forward_forecast(
    states: pd.DataFrame,
    tri: pd.DataFrame,
    as_of: pd.Timestamp,
    decision_id: str,
) -> tuple[dict[str, object], pd.DataFrame]:
    history = states[states["date"] <= as_of].copy().sort_values("date").reset_index(drop=True)
    if history.empty or pd.Timestamp(history["date"].max()) != as_of:
        raise RuntimeError(f"Valuation state absent on as-of date {as_of.date()}")
    percentile_columns = []
    for feature in FEATURES:
        column = f"causal_{feature}_percentile"
        history[column] = history[feature].rank(method="average", pct=True)
        percentile_columns.append(column)
    current = history.iloc[-1]
    candidates = history[history["date"] + pd.DateOffset(years=HORIZON_YEARS) <= as_of].copy()
    candidates["valuation_distance"] = np.sqrt(
        candidates[percentile_columns]
        .sub(current[percentile_columns].astype(float))
        .pow(2)
        .mean(axis=1)
    )
    candidates = candidates.sort_values(["valuation_distance", "date"]).reset_index(drop=True)
    selected: list[pd.Series] = []
    analogue_rows: list[dict[str, object]] = []
    for _, candidate in candidates.iterrows():
        if any(month_distance(pd.Timestamp(candidate["date"]), pd.Timestamp(item["date"])) < MIN_GAP_MONTHS for item in selected):
            continue
        target_date = pd.Timestamp(candidate["date"]) + pd.DateOffset(years=HORIZON_YEARS)
        endpoints = tri[(tri["date"] >= target_date) & (tri["date"] <= as_of)].sort_values("date")
        if endpoints.empty:
            continue
        endpoint = endpoints.iloc[0]
        forward_annualized = float(
            (float(endpoint["close"]) / float(candidate["tri_close"])) ** (1.0 / HORIZON_YEARS) - 1.0
        )
        selected.append(candidate)
        analogue_rows.append(
            {
                "decision_id": decision_id,
                "as_of": as_of,
                "rank": len(selected),
                "anchor_date": pd.Timestamp(candidate["date"]),
                "valuation_distance": float(candidate["valuation_distance"]),
                "forward_target_date": target_date,
                "forward_end_date": pd.Timestamp(endpoint["date"]),
                "forward_tri_annualized": forward_annualized,
                **{feature: float(candidate[feature]) for feature in FEATURES},
                **{column: float(candidate[column]) for column in percentile_columns},
            }
        )
        if len(selected) == MAX_ANALOGUES:
            break

    values = pd.Series([row["forward_tri_annualized"] for row in analogue_rows], dtype=float)
    enough = len(values) >= MIN_ANALOGUES
    median = float(values.median()) if enough else np.nan
    signal_on = bool(enough and median < 0.0)
    summary: dict[str, object] = {
        "decision_id": decision_id,
        "state_date": as_of,
        "analogue_count": int(len(values)),
        "enough_analogues": enough,
        "forecast_3y_median": median,
        "signal_on": signal_on,
        "signal_reason": "forecast_3y_median_below_zero" if signal_on else (
            "forecast_nonnegative" if enough else "fewer_than_4_completed_analogues"
        ),
        **{feature: float(current[feature]) for feature in FEATURES},
        **{column: float(current[column]) for column in percentile_columns},
    }
    return summary, pd.DataFrame(analogue_rows)


def build_signals(
    schedule: pd.DataFrame,
    states: pd.DataFrame,
    tri: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    signal_rows = []
    analogue_parts = []
    for idx, put_row in schedule.iterrows():
        entry_date = pd.Timestamp(put_row["put_entry_date"])
        prior_states = states[states["date"] < entry_date]
        if prior_states.empty:
            raise RuntimeError(f"No strictly prior valuation state for {entry_date.date()}")
        state_date = pd.Timestamp(prior_states["date"].max())
        decision_id = f"historical_{idx:02d}_{entry_date.date().isoformat()}"
        signal, analogues = walk_forward_forecast(states, tri, state_date, decision_id)
        signal.update(
            {
                "put_entry_date": entry_date,
                "target_month": pd.Timestamp(put_row["target_month"]),
                "put_contract_if_on": put_row["put_contract"],
                "strike_if_on": float(put_row["strike"]),
                "entry_settle_if_on": float(put_row["entry_settle"]),
            }
        )
        signal_rows.append(signal)
        analogue_parts.append(analogues)
    signals = pd.DataFrame(signal_rows).sort_values("put_entry_date").reset_index(drop=True)
    analogues = pd.concat(analogue_parts, ignore_index=True)
    if not (signals["state_date"] < signals["put_entry_date"]).all():
        raise RuntimeError("Signal lookahead: valuation state is not strictly prior to execution")
    if (analogues["forward_end_date"] > analogues["as_of"]).any():
        raise RuntimeError("Signal lookahead: analogue outcome was not known at decision time")
    return signals, analogues


def build_gated_daily(
    upstream_daily: pd.DataFrame,
    schedule: pd.DataFrame,
    signals: pd.DataFrame,
    options: pd.DataFrame,
) -> pd.DataFrame:
    daily = upstream_daily.copy().sort_values("date").reset_index(drop=True)
    if len(schedule) != len(signals):
        raise RuntimeError("Put schedule and signal length mismatch")
    if not schedule["put_entry_date"].reset_index(drop=True).equals(signals["put_entry_date"].reset_index(drop=True)):
        raise RuntimeError("Put schedule and signal dates mismatch")
    option_lookup = options.set_index(["contract", "date"])
    daily["gated_put_pnl_ret"] = 0.0
    daily["gated_put_cost_rate"] = 0.0
    daily["gated_put_contract"] = ""
    daily["gated_put_settle"] = np.nan
    daily["valuation_signal_on"] = False

    for idx, row in schedule.iterrows():
        signal_on = bool(signals.loc[idx, "signal_on"])
        prior_on = bool(signals.loc[idx - 1, "signal_on"]) if idx > 0 else False
        entry_date = pd.Timestamp(row["put_entry_date"])
        exit_date = pd.Timestamp(row["put_exit_date"])
        cost_sides = 0
        if idx == 0:
            cost_sides = 1 if signal_on else 0
        elif signal_on and prior_on:
            cost_sides = 2
        elif signal_on != prior_on:
            cost_sides = 1
        if cost_sides:
            cost_index = daily.index[daily["date"].eq(entry_date)]
            if len(cost_index) != 1:
                raise RuntimeError(f"Option cost date missing: {entry_date.date()}")
            daily.loc[cost_index[0], "gated_put_cost_rate"] += cost_sides * PUT_ONE_WAY_COST
        if not signal_on:
            continue

        contract = str(row["put_contract"])
        held_indices = daily.index[daily["date"].between(entry_date, exit_date)].tolist()
        held_dates = [pd.Timestamp(daily.loc[item, "date"]) for item in held_indices]
        prices = pd.Series(
            [float(option_lookup.loc[(contract, day), "settle"]) for day in held_dates],
            index=pd.DatetimeIndex(held_dates),
            dtype=float,
        )
        price_changes = prices.diff()
        for day_index, day in zip(held_indices[1:], held_dates[1:]):
            prior_future_settle = float(daily.loc[day_index - 1, "settle"])
            daily.loc[day_index, "gated_put_pnl_ret"] += float(price_changes.loc[day]) / prior_future_settle

        next_entry = pd.Timestamp(schedule.loc[idx + 1, "put_entry_date"]) if idx + 1 < len(schedule) else None
        active_mask = daily["date"].between(entry_date, exit_date) if next_entry is None else (
            (daily["date"] >= entry_date) & (daily["date"] < next_entry)
        )
        for day_index in daily.index[active_mask]:
            day = pd.Timestamp(daily.loc[day_index, "date"])
            daily.loc[day_index, "gated_put_contract"] = contract
            daily.loc[day_index, "gated_put_settle"] = float(option_lookup.loc[(contract, day), "settle"])
            daily.loc[day_index, "valuation_signal_on"] = True

    daily["gated_gross_ret"] = daily["im_gross_ret"] + daily["gated_put_pnl_ret"]
    daily["gated_net_ret"] = (
        (1.0 + daily["gated_gross_ret"])
        * (1.0 - daily["cost_rate"])
        * (1.0 - daily["gated_put_cost_rate"])
        - 1.0
    )
    daily["gated_cash_weight"] = CASH_WEIGHT
    active = daily["gated_put_contract"].ne("")
    daily.loc[active, "gated_cash_weight"] = (
        CASH_WEIGHT - daily.loc[active, "gated_put_settle"] / daily.loc[active, "settle"]
    ).clip(lower=0.0)
    daily["gated_plus_cash_ret"] = daily["gated_net_ret"] + daily["gated_cash_weight"] * CASH_DAILY_RETURN

    required_upstream = [
        "baseline_net_ret",
        "protected_net_ret",
        "baseline_plus_cash_ret",
        "protected_plus_cash_ret",
    ]
    if daily[required_upstream].isna().any().any():
        raise RuntimeError("Frozen upstream baseline/always-on columns contain missing values")
    return_columns = required_upstream + ["gated_net_ret", "gated_plus_cash_ret"]
    for column in return_columns:
        daily[f"nav_{column.removesuffix('_ret')}"] = (1.0 + daily[column]).cumprod()
    if (daily[return_columns] <= -1.0).any().any():
        raise RuntimeError("Return at or below -100%")
    return daily


def metric_from_returns(returns: pd.Series) -> dict[str, float]:
    clean = returns.astype(float).dropna()
    if clean.empty:
        return {
            "total_return": np.nan,
            "cagr": np.nan,
            "max_drawdown": np.nan,
            "annual_volatility": np.nan,
            "sharpe_0rf": np.nan,
        }
    nav = pd.concat([pd.Series([1.0]), (1.0 + clean.reset_index(drop=True)).cumprod()], ignore_index=True)
    volatility = float(clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 else np.nan
    sharpe = float(clean.mean() / clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 and clean.std(ddof=1) > 0 else np.nan
    return {
        "total_return": float(nav.iloc[-1] - 1.0),
        "cagr": float(nav.iloc[-1] ** (TRADING_DAYS / len(clean)) - 1.0),
        "max_drawdown": float((nav / nav.cummax() - 1.0).min()),
        "annual_volatility": volatility,
        "sharpe_0rf": sharpe,
    }


def comparison_metrics(subset: pd.DataFrame) -> dict[str, float]:
    baseline = metric_from_returns(subset["baseline_net_ret"])
    always = metric_from_returns(subset["protected_net_ret"])
    gated = metric_from_returns(subset["gated_net_ret"])
    baseline_cash = metric_from_returns(subset["baseline_plus_cash_ret"])
    always_cash = metric_from_returns(subset["protected_plus_cash_ret"])
    gated_cash = metric_from_returns(subset["gated_plus_cash_ret"])
    return {
        "baseline_cagr": baseline["cagr"],
        "baseline_max_drawdown": baseline["max_drawdown"],
        "always_put_cagr": always["cagr"],
        "always_put_max_drawdown": always["max_drawdown"],
        "gated_put_cagr": gated["cagr"],
        "gated_put_max_drawdown": gated["max_drawdown"],
        "gated_return_delta_vs_baseline_pp": (gated["cagr"] - baseline["cagr"]) * 100.0,
        "gated_drawdown_improvement_vs_baseline_pp": (gated["max_drawdown"] - baseline["max_drawdown"]) * 100.0,
        "gated_return_delta_vs_always_pp": (gated["cagr"] - always["cagr"]) * 100.0,
        "gated_drawdown_delta_vs_always_pp": (gated["max_drawdown"] - always["max_drawdown"]) * 100.0,
        "baseline_plus_cash_cagr": baseline_cash["cagr"],
        "baseline_plus_cash_max_drawdown": baseline_cash["max_drawdown"],
        "always_put_plus_cash_cagr": always_cash["cagr"],
        "always_put_plus_cash_max_drawdown": always_cash["max_drawdown"],
        "gated_put_plus_cash_cagr": gated_cash["cagr"],
        "gated_put_plus_cash_max_drawdown": gated_cash["max_drawdown"],
        "gated_total_return": gated["total_return"],
        "gated_annual_volatility": gated["annual_volatility"],
        "gated_sharpe_0rf": gated["sharpe_0rf"],
    }


def metrics_by_window(daily: pd.DataFrame) -> pd.DataFrame:
    end_date = pd.Timestamp(daily["date"].max())
    start_date = pd.Timestamp(daily["date"].min())
    windows = [
        ("full", start_date),
        ("10y", end_date - pd.DateOffset(years=10)),
        ("5y", end_date - pd.DateOffset(years=5)),
        ("3y", end_date - pd.DateOffset(years=3)),
        ("1y", end_date - pd.DateOffset(years=1)),
    ]
    rows = []
    for window, cutoff in windows:
        available = window == "full" or start_date <= cutoff
        subset = daily[daily["date"] >= cutoff].copy() if available else daily.iloc[0:0].copy()
        row: dict[str, object] = {
            "window": window,
            "available": available,
            "unavailable_reason": "" if available else f"IM/MO common history starts {start_date.date()}, shorter than {window}",
            "requested_start": cutoff.date().isoformat(),
            "actual_start": subset["date"].min().date().isoformat() if available else "",
            "end": end_date.date().isoformat(),
            "trading_days": int(len(subset)),
        }
        row.update(comparison_metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    start_date = pd.Timestamp(daily["date"].min())
    end_date = pd.Timestamp(daily["date"].max())
    rows = []
    for year, subset in daily.groupby(daily["date"].dt.year, sort=True):
        row: dict[str, object] = {
            "year": int(year),
            "partial_year": int(year) in {start_date.year, end_date.year},
            "period_start": subset["date"].min().date().isoformat(),
            "period_end": subset["date"].max().date().isoformat(),
            "trading_days": int(len(subset)),
        }
        row.update(comparison_metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def drawdown_episode(daily: pd.DataFrame, return_column: str) -> dict[str, object]:
    nav = (1.0 + daily[return_column]).cumprod()
    peaks = nav.cummax()
    drawdown = nav / peaks - 1.0
    trough_idx = int(drawdown.idxmin())
    peak_idx = int(nav.loc[:trough_idx].idxmax())
    recovered = daily.index[(daily.index > trough_idx) & (nav >= float(peaks.iloc[trough_idx]))]
    recovery_idx = int(recovered[0]) if len(recovered) else None
    return {
        "series": return_column,
        "peak_date": daily.loc[peak_idx, "date"].date().isoformat(),
        "trough_date": daily.loc[trough_idx, "date"].date().isoformat(),
        "recovery_date": daily.loc[recovery_idx, "date"].date().isoformat() if recovery_idx is not None else "unrecovered",
        "max_drawdown": float(drawdown.iloc[trough_idx]),
    }


def build_current_outputs(
    states: pd.DataFrame,
    tri: pd.DataFrame,
    upstream_daily: pd.DataFrame,
    schedule: pd.DataFrame,
    forecasts: pd.DataFrame,
    daily: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    as_of = pd.Timestamp(states["date"].max())
    signal, current_analogues = walk_forward_forecast(states, tri, as_of, "current_2026-08-14")
    upstream_summary = pd.read_csv(VALUATION_V3_OUTPUT / "forward_valuation_summary.csv")
    expected_median = float(
        upstream_summary.loc[
            upstream_summary["product"].eq("IM") & upstream_summary["horizon_years"].eq(3),
            "median_annualized",
        ].iloc[0]
    )
    if abs(float(signal["forecast_3y_median"]) - expected_median) > 1e-12:
        raise RuntimeError(
            f"Current forecast does not reconcile to v3: {signal['forecast_3y_median']} vs {expected_median}"
        )
    last = upstream_daily.iloc[-1]
    active_schedule = schedule[schedule["put_entry_date"] <= as_of].iloc[-1]
    current_signal = pd.DataFrame(
        [
            {
                **signal,
                "signal_for_next_put_decision": bool(signal["signal_on"]),
                "currently_marked_put": last["put_contract"],
                "current_put_strike": float(active_schedule["strike"]),
                "current_put_settle": float(last["put_settle"]),
                "current_im_contract": last["contract"],
                "current_im_settle": float(last["settle"]),
                "current_index_close": float(last["csi1000_price_close"]),
                "current_put_strike_vs_index": float(active_schedule["strike"] / last["csi1000_price_close"] - 1.0),
                "current_put_premium_to_im_notional": float(last["put_settle"] / last["settle"]),
                "next_target_month": (as_of.replace(day=1) + pd.offsets.MonthBegin(3)),
                "next_target_contract_status": "not_yet_observed_in_frozen_data",
            }
        ]
    )

    baseline_factor = float((1.0 + daily["baseline_plus_cash_ret"]).prod())
    gated_factor = float((1.0 + daily["gated_plus_cash_ret"]).prod())
    overlay_factor = gated_factor / baseline_factor
    overlay_cagr = float(overlay_factor ** (TRADING_DAYS / len(daily)) - 1.0)
    current_forecasts = forecasts[forecasts["product"].eq("IM")].copy()
    current_forecasts["historical_gated_overlay_relative_cagr"] = overlay_cagr
    current_forecasts["cost_adjusted_with_gated_overlay_annualized"] = (
        (1.0 + current_forecasts["combined_annualized"]) * (1.0 + overlay_cagr) - 1.0
    )
    current_forecasts["cost_adjusted_with_gated_overlay_cumulative"] = (
        (1.0 + current_forecasts["cost_adjusted_with_gated_overlay_annualized"])
        ** current_forecasts["horizon_years"]
        - 1.0
    )
    current_forecasts["protected_projected_max_drawdown"] = np.nan
    current_forecasts["adjustment_scope"] = (
        "historical average gated-overlay relative cost only; unknown future crash payoff excluded"
    )
    return current_signal, current_analogues, current_forecasts


def pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def pp(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:+.2f}pp"


def render_window_table(metrics: pd.DataFrame) -> str:
    lines = [
        "| 窗口 | 无保护 CAGR / MaxDD | 永久Put CAGR / MaxDD | 估值择时Put CAGR / MaxDD | 择时相对无保护：收益 / 回撤 |",
        "|---|---:|---:|---:|---:|",
    ]
    for window in ["full", "10y", "5y", "3y", "1y"]:
        row = metrics[metrics["window"].eq(window)].iloc[0]
        if not bool(row["available"]):
            lines.append(f"| {window} | N/A | N/A | N/A | N/A |")
            continue
        lines.append(
            f"| {window} | {pct(row['baseline_cagr'])} / {pct(row['baseline_max_drawdown'])} | "
            f"{pct(row['always_put_cagr'])} / {pct(row['always_put_max_drawdown'])} | "
            f"{pct(row['gated_put_cagr'])} / {pct(row['gated_put_max_drawdown'])} | "
            f"{pp(row['gated_return_delta_vs_baseline_pp'])} / {pp(row['gated_drawdown_improvement_vs_baseline_pp'])} |"
        )
    return "\n".join(lines)


def render_annual_table(annual: pd.DataFrame) -> str:
    lines = [
        "| 年份 | 无保护 CAGR / MaxDD | 永久Put CAGR / MaxDD | 估值择时Put CAGR / MaxDD | 择时相对无保护：收益 / 回撤 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in annual.itertuples(index=False):
        label = f"{row.year}（部分）" if row.partial_year else str(row.year)
        lines.append(
            f"| {label} | {pct(row.baseline_cagr)} / {pct(row.baseline_max_drawdown)} | "
            f"{pct(row.always_put_cagr)} / {pct(row.always_put_max_drawdown)} | "
            f"{pct(row.gated_put_cagr)} / {pct(row.gated_put_max_drawdown)} | "
            f"{pp(row.gated_return_delta_vs_baseline_pp)} / {pp(row.gated_drawdown_improvement_vs_baseline_pp)} |"
        )
    return "\n".join(lines)


def render_forecast_table(forecasts: pd.DataFrame) -> str:
    scenario_order = {"悲观": 0, "中等": 1, "乐观": 2}
    ordered = forecasts.assign(_order=forecasts["scenario"].map(scenario_order)).sort_values(["horizon_years", "_order"])
    lines = [
        "| 期限 | 情景 | 原无保护组合年化 / 累计 | 扣历史择时保险成本后年化 / 累计 | 保护后MaxDD |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in ordered.itertuples(index=False):
        lines.append(
            f"| {row.horizon_years}年 | {row.scenario} | {pct(row.combined_annualized)} / {pct(row.combined_cumulative)} | "
            f"{pct(row.cost_adjusted_with_gated_overlay_annualized)} / "
            f"{pct(row.cost_adjusted_with_gated_overlay_cumulative)} | N/A |"
        )
    return "\n".join(lines)


def write_record(
    output_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    annual: pd.DataFrame,
    signals: pd.DataFrame,
    current_signal: pd.DataFrame,
    current_forecasts: pd.DataFrame,
    manifest: dict[str, object],
) -> None:
    full = metrics[metrics["window"].eq("full")].iloc[0]
    current = current_signal.iloc[0]
    on_count = int(signals["signal_on"].sum())
    active_days = int(daily["valuation_signal_on"].sum())
    switches = int(signals["signal_on"].astype(int).diff().abs().fillna(0).sum())
    if full["gated_drawdown_improvement_vs_baseline_pp"] >= 8.0:
        decision = "达到8个百分点回撤改善宽容线，但仍无实盘授权。"
    elif full["gated_return_delta_vs_baseline_pp"] < -1.0:
        decision = "回撤改善不足8个百分点且年化收益损失超过1个百分点，只能归类为观察/诊断。"
    else:
        decision = "未触发预注册自动否决，但仍只允许研究观察。"
    overlay_cagr = float(current_forecasts["historical_gated_overlay_relative_cagr"].iloc[0])
    record = f"""# IM 估值预测择时 + 约3个月最低执行价 Put v1：结果记录

运行日期：{date.today().isoformat()}  
研究状态：研究审计；未获准实盘  
回测样本：{daily['date'].min().date().isoformat()} 至 {daily['date'].max().date().isoformat()}  
估值信号：严格走步四特征3年历史类比中位数 < 0

## 结论摘要

- 无保护 IM 全样本净年化 {pct(full['baseline_cagr'])}、MaxDD {pct(full['baseline_max_drawdown'])}；永久 Put 为 {pct(full['always_put_cagr'])}/{pct(full['always_put_max_drawdown'])}；估值择时 Put 为 {pct(full['gated_put_cagr'])}/{pct(full['gated_put_max_drawdown'])}。
- 择时保护相对无保护年化变化 {pp(full['gated_return_delta_vs_baseline_pp'])}、回撤改善 {pp(full['gated_drawdown_improvement_vs_baseline_pp'])}；相对永久保护多保留 {pp(full['gated_return_delta_vs_always_pp'])} 年化。{decision}
- 49次决策中开启 {on_count} 次，保护日 {active_days}/{len(daily)}（{pct(active_days/len(daily))}），状态切换 {switches} 次。信号不是按全样本PE分位回填，而是每月严格只使用当时已知的完整3年前瞻结果。
- 当前2026-08-14的3年类比中位数为 {pct(current['forecast_3y_median'])}，类比数 {int(current['analogue_count'])}，保护信号为 **{'ON' if current['signal_on'] else 'OFF'}**。这是研究审计状态，不是下单指令。

## 强制窗口

{render_window_table(metrics)}

## 逐年结果

{render_annual_table(annual)}

## 当前点位与3年/5年条件收益

- 当前中证1000收盘 {current['current_index_close']:.2f}，PE {current['pe_aggregate_ttm']:.2f}；冻结 v3 的3年指数全收益中位数为 {pct(current['forecast_3y_median'])}。
- 截至当前冻结日，历史曲线正在估值保护状态，盯市 Put 为 `{current['currently_marked_put']}`，执行价相对指数 {pct(current['current_put_strike_vs_index'])}，结算权利金占 IM 名义 {pct(current['current_put_premium_to_im_notional'])}。
- 下一计划目标月为 {pd.Timestamp(current['next_target_month']).strftime('%Y-%m')}，冻结数据中尚未挂牌/观察到价格，报告不虚构报价。
- 历史择时保护（含权利金挤占现金利息）相对无保护+现金的年化因子为 {pct(overlay_cagr)}。下表只把这项历史平均保险成本乘入 v3 情景；未知未来崩盘带来的 Put 收益没有被预测。

{render_forecast_table(current_forecasts)}

期权保护不提高当前指数方向的中位数预期。它通常压低中等和乐观情景的期望收益，价值体现在实际下跌路径中；但 v3 没有未来逐日路径，而该 Put 又在到期前滚动，所以未来 MaxDD 和悲观情景的真实缓冲不能从终点年化诚实推导。

## 执行与完整性

- 每1份IM名义匹配2份MO Put；IM与整组Put每边成本均按1bp。30%作为保证金/缓冲，70%现金年化3%，有Put时扣除权利金占用。
- 当前预测与冻结估值 v3 的3年中位数精确复核；所有历史信号使用严格早于Put交易日的估值状态，全部类比前瞻终点不晚于当时状态日。
- 无保护与永久保护曲线直接来自已核对的正式 v1；本版逐日重建择时持仓，特别处理 `ON/OFF` 切换日旧Put损益和买卖成本。
- 官方结算价不保证可成交；未计盘口冲击、经纪商保证金加收、盘中流动性和理财赎回限制。

## 复现与状态

- 冻结规格：`docs/{VERSION}_spec.md`，SHA-256 `{manifest['spec_sha256']}`。
- 脚本：`{VERSION}.py`，SHA-256 `{manifest['script_sha256']}`。
- 命令：`{manifest['command']}`。
- 本结果未获准实盘，不生成自动或人工下单信号。
"""
    (output_dir / "record.md").write_text(record, encoding="utf-8")


def run(output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Formal output directory already exists and will not be overwritten: {output_dir}")
    spec_hash = verify_spec()

    input_paths = {
        "put_daily": PUT_V1_OUTPUT / "daily_nav.csv",
        "put_schedule": PUT_V1_OUTPUT / "put_roll_schedule.csv",
        "options": PUT_V1_DATA / "cffex_mo_puts.csv",
        "valuation_states": VALUATION_V3_OUTPUT / "monthly_valuation_state.csv",
        "tri": VALUATION_V3_DATA / "csindex_H00852.csv",
        "forecasts": VALUATION_V3_OUTPUT / "combined_forecasts.csv",
        "put_manifest": PUT_V1_OUTPUT / "data_manifest.json",
        "valuation_manifest": VALUATION_V3_OUTPUT / "data_manifest.json",
    }
    missing = [str(path) for path in input_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing frozen upstream inputs: {missing}")

    upstream_daily = pd.read_csv(input_paths["put_daily"], parse_dates=["date"])
    schedule = pd.read_csv(
        input_paths["put_schedule"],
        parse_dates=["im_roll_date", "target_month", "put_entry_date", "put_exit_date"],
    )
    options = pd.read_csv(input_paths["options"], parse_dates=["date"])
    states = pd.read_csv(input_paths["valuation_states"], parse_dates=["date"])
    states = states[states["product"].eq("IM")].sort_values("date").reset_index(drop=True)
    tri = pd.read_csv(input_paths["tri"], parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    forecasts = pd.read_csv(input_paths["forecasts"])

    signals, analogues = build_signals(schedule, states, tri)
    daily = build_gated_daily(upstream_daily, schedule, signals, options)
    metrics = metrics_by_window(daily)
    annual = annual_metrics(daily)
    current_signal, current_analogues, current_forecasts = build_current_outputs(
        states, tri, upstream_daily, schedule, forecasts, daily
    )
    all_analogues = pd.concat([analogues, current_analogues], ignore_index=True)
    worst_days = daily.nsmallest(5, "baseline_net_ret")[[
        "date",
        "contract",
        "baseline_net_ret",
        "protected_net_ret",
        "gated_net_ret",
        "valuation_signal_on",
        "gated_put_contract",
        "gated_put_pnl_ret",
        "gated_put_cost_rate",
    ]].copy()
    drawdowns = pd.DataFrame([
        drawdown_episode(daily, "baseline_net_ret"),
        drawdown_episode(daily, "protected_net_ret"),
        drawdown_episode(daily, "gated_net_ret"),
        drawdown_episode(daily, "baseline_plus_cash_ret"),
        drawdown_episode(daily, "gated_plus_cash_ret"),
    ])
    extremes = daily.loc[
        daily["gated_net_ret"].abs() > 0.10,
        ["date", "contract", "gated_put_contract", "gated_net_ret", "baseline_net_ret", "gated_put_pnl_ret"],
    ].copy()

    command = f"{Path(sys.executable).name} {Path(__file__).name}"
    input_manifest = {
        name: {"path": str(path.resolve()), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for name, path in input_paths.items()
    }
    signal_sides = int(round(float(daily["gated_put_cost_rate"].sum()) / PUT_ONE_WAY_COST))
    manifest: dict[str, object] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "spec_sha256": spec_hash,
        "script_sha256": sha256_file(Path(__file__)),
        "inputs": input_manifest,
        "sample": {
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "trading_days": int(len(daily)),
            "timezone": "Asia/Shanghai",
        },
        "signal": {
            "features": FEATURES,
            "horizon_years": HORIZON_YEARS,
            "threshold": "strictly below zero median annualized total return",
            "max_analogues": MAX_ANALOGUES,
            "min_analogues": MIN_ANALOGUES,
            "minimum_anchor_gap_months": MIN_GAP_MONTHS,
            "decision_count": int(len(signals)),
            "on_decisions": int(signals["signal_on"].sum()),
            "active_trading_days": int(daily["valuation_signal_on"].sum()),
            "state_switches": int(signals["signal_on"].astype(int).diff().abs().fillna(0).sum()),
            "option_transaction_sides": signal_sides,
            "option_cost_rate_sum": float(daily["gated_put_cost_rate"].sum()),
        },
        "current": current_signal.iloc[0].to_dict(),
        "checks": {
            "strictly_prior_state_dates": bool((signals["state_date"] < signals["put_entry_date"]).all()),
            "all_analogue_outcomes_known": bool((analogues["forward_end_date"] <= analogues["as_of"]).all()),
            "baseline_series_input_is_frozen_put_v1": True,
            "always_on_series_input_is_frozen_put_v1": True,
            "extreme_gated_return_count": int(len(extremes)),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    daily.to_csv(output_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(output_dir / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    signals.to_csv(output_dir / "valuation_signals.csv", index=False, encoding="utf-8-sig")
    all_analogues.to_csv(output_dir / "signal_analogues.csv", index=False, encoding="utf-8-sig")
    worst_days.to_csv(output_dir / "worst_days.csv", index=False, encoding="utf-8-sig")
    drawdowns.to_csv(output_dir / "drawdown_episodes.csv", index=False, encoding="utf-8-sig")
    current_signal.to_csv(output_dir / "current_signal.csv", index=False, encoding="utf-8-sig")
    current_forecasts.to_csv(
        output_dir / "current_forecast_with_protection_cost.csv", index=False, encoding="utf-8-sig"
    )
    extremes.to_csv(output_dir / "extreme_returns.csv", index=False, encoding="utf-8-sig")
    (output_dir / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(output_dir, daily, metrics, annual, signals, current_signal, current_forecasts, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IM valuation-forecast gated MO put overlay v1")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.output_dir)
