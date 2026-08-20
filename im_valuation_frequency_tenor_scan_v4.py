from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from im_monthly_roll_valuation_gated_put_v1 import walk_forward_forecast
from im_monthly_roll_valuation_gated_put_v2 import add_option_expiry
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, metrics, prepare_options


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "docs" / "im_valuation_frequency_tenor_scan_v4_spec.md"
SPEC_HASH = "8FFD4437108F0711B9766CF55C99821DDCDB5BDA87BA9D0A6BF1B1CEB30386D7"
OUTPUT = ROOT / "outputs" / "im_valuation_frequency_tenor_scan_v4"
SCAN = ROOT / "quant_param_scan_runs" / "20260816_im_val_freq_tenor_v4"

UPSTREAM = ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1" / "daily_nav.csv"
V2_DAILY = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v2" / "daily_nav.csv"
V2_DECISIONS = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v2" / "decision_schedule.csv"
MONTHLY_STATES = ROOT / "outputs" / "ic_im_valuation_risk_premium_forecast_v3" / "monthly_valuation_state.csv"
DATA = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3"
VALUATION = DATA / "legulegu_000852_valuation.csv"
PRICE = DATA / "csindex_000852.csv"
TRI = DATA / "csindex_H00852.csv"
GOV10Y = DATA / "chinabond_government_10y.csv"
OPTIONS = ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_mo_puts.csv"

START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-08-14")
TRADING_DAYS = 252
CASH_WEIGHT = 0.70
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0
MO_CONTRACT_SIDE_COST = 0.00005
FREQUENCIES = ["monthly", "weekly", "daily"]
TENORS = ["front", "2m", "3m"]
TIERS = ["binary", "three_tier"]
GRID = [f"{frequency}_{tenor}_{tier}" for frequency in FREQUENCIES for tenor in TENORS for tier in TIERS]
CANDIDATES = ["no_put", *GRID]
BASELINE = "monthly_3m_binary"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    result = subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip()


def verify() -> None:
    if sha256(SPEC).upper() != SPEC_HASH:
        raise RuntimeError("Frozen v4 spec hash mismatch")
    if OUTPUT.exists():
        raise RuntimeError(f"Formal output exists and cannot be overwritten: {OUTPUT}")


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    upstream = pd.read_csv(UPSTREAM, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    v2 = pd.read_csv(V2_DAILY, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    decisions = pd.read_csv(V2_DECISIONS, parse_dates=["state_date", "execution_date"])
    states = pd.read_csv(MONTHLY_STATES, parse_dates=["date"])
    states = states[states["product"].eq("IM")].sort_values("date").reset_index(drop=True)
    tri = pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].sort_values("date")
    options = add_option_expiry(pd.read_csv(OPTIONS, parse_dates=["date"]))
    if len(upstream) != 986 or upstream.date.min() != START or upstream.date.max() != END:
        raise RuntimeError("Unexpected IM/MO formal sample")
    if not upstream.date.equals(v2.date):
        raise RuntimeError("Upstream and v2 calendars differ")
    return upstream, v2, decisions, states, tri, options


def build_daily_valuation() -> tuple[pd.DataFrame, dict[str, float]]:
    valuation = pd.read_csv(VALUATION, parse_dates=["date"])
    price = pd.read_csv(PRICE, parse_dates=["date"]).rename(columns={"close": "price_close"})
    tri = pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "tri_close"})
    gov = pd.read_csv(GOV10Y, parse_dates=["date"]).rename(columns={"date": "gov10y_date"})
    daily = valuation.merge(
        price[["date", "price_close", "official_rolling_pe"]], on="date", validate="one_to_one"
    ).merge(tri, on="date", validate="one_to_one")
    daily = daily[daily.date >= pd.Timestamp("2015-10-17")].sort_values("date").reset_index(drop=True)
    daily = pd.merge_asof(
        daily,
        gov.sort_values("gov10y_date"),
        left_on="date",
        right_on="gov10y_date",
        direction="backward",
        allow_exact_matches=True,
    )
    daily["gov10y_staleness_days"] = (daily.date - daily.gov10y_date).dt.days
    targets = daily[["date"]].copy()
    targets["prior_target_date"] = targets.date - pd.DateOffset(years=1)
    official = price[["date", "price_close"]].merge(tri, on="date", validate="one_to_one").rename(
        columns={"date": "prior_observation_date", "price_close": "prior_price_close", "tri_close": "prior_tri_close"}
    )
    prior = pd.merge_asof(
        targets.sort_values("prior_target_date"),
        official.sort_values("prior_observation_date"),
        left_on="prior_target_date",
        right_on="prior_observation_date",
        direction="backward",
        allow_exact_matches=True,
    )
    daily = daily.merge(
        prior[["date", "prior_target_date", "prior_observation_date", "prior_price_close", "prior_tri_close"]],
        on="date",
        validate="one_to_one",
    )
    daily["trailing_dividend_contribution"] = (
        (daily.tri_close / daily.prior_tri_close) / (daily.price_close / daily.prior_price_close) - 1.0
    )
    daily["earnings_yield"] = 1.0 / daily.pe_aggregate_ttm
    daily["erp"] = daily.earnings_yield - daily.gov10y_yield
    required = ["pe_aggregate_ttm", "pb_aggregate", "erp", "trailing_dividend_contribution", "tri_close"]
    if daily[required].isna().any().any() or (daily.gov10y_date > daily.date).any():
        raise RuntimeError("Invalid daily valuation reconstruction")
    frozen = pd.read_csv(MONTHLY_STATES, parse_dates=["date"])
    frozen = frozen[frozen["product"].eq("IM")]
    matched = daily[daily.date.isin(frozen.date)].merge(
        frozen[["date", *required]], on="date", suffixes=("_daily", "_frozen"), validate="one_to_one"
    )
    diffs = {
        feature: float((matched[f"{feature}_daily"] - matched[f"{feature}_frozen"]).abs().max())
        for feature in required
    }
    return daily, diffs


def evaluation_dates(frequency: str, upstream: pd.DataFrame, daily_valuation: pd.DataFrame, decisions: pd.DataFrame) -> list[pd.Timestamp]:
    trade_dates = pd.DatetimeIndex(upstream.date)
    if frequency == "monthly":
        return [pd.Timestamp(value) for value in decisions.state_date]
    if frequency == "daily":
        initial = pd.Timestamp(daily_valuation[daily_valuation.date < START].date.max())
        return [initial, *[pd.Timestamp(value) for value in trade_dates if value < END]]
    pre = daily_valuation[daily_valuation.date < START][["date"]].copy()
    pre["week"] = pre.date.dt.to_period("W-FRI")
    completed = pre[pre.week.map(lambda period: period.end_time.normalize() < START)]
    initial = pd.Timestamp(completed.groupby("week").tail(1).date.iloc[-1])
    post = upstream[upstream.date < END][["date"]].copy()
    post["week"] = post.date.dt.to_period("W-FRI")
    weekly = [pd.Timestamp(value) for value in post.groupby("week").tail(1).date]
    weekly = [value for value in weekly if len(trade_dates[trade_dates > value])]
    return sorted(set([initial, *weekly]))


def next_execution(eval_date: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> tuple[pd.Timestamp, bool]:
    if eval_date < START:
        return START, True
    later = trade_dates[trade_dates > eval_date]
    if not len(later):
        raise RuntimeError(f"No execution date after {eval_date.date()}")
    return pd.Timestamp(later[0]), False


def forecast_at(
    day: pd.Timestamp,
    daily_valuation: pd.DataFrame,
    monthly_states: pd.DataFrame,
    tri: pd.DataFrame,
    decision_id: str,
) -> tuple[dict[str, object], pd.DataFrame]:
    history = monthly_states[monthly_states.date <= day].copy()
    if history.empty or pd.Timestamp(history.date.max()) != day:
        current = daily_valuation[daily_valuation.date.eq(day)]
        if len(current) != 1:
            raise RuntimeError(f"Missing daily valuation state: {day.date()}")
        source = current.iloc[0]
        row = {column: np.nan for column in monthly_states.columns}
        for feature in ["pe_aggregate_ttm", "pb_aggregate", "erp", "trailing_dividend_contribution", "tri_close"]:
            row[feature] = float(source[feature])
        row.update({"date": day, "product": "IM", "index_name": "中证1000"})
        history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    return walk_forward_forecast(history, tri, day, decision_id)


def build_signal_schedules(
    upstream: pd.DataFrame,
    decisions: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    monthly_states: pd.DataFrame,
    tri: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, float]]:
    dates_by_frequency = {
        frequency: evaluation_dates(frequency, upstream, daily_valuation, decisions) for frequency in FREQUENCIES
    }
    all_dates = sorted(set().union(*[set(values) for values in dates_by_frequency.values()]))
    signal_by_date: dict[pd.Timestamp, dict[str, object]] = {}
    analogue_parts: list[pd.DataFrame] = []
    for number, day in enumerate(all_dates):
        signal, analogues = forecast_at(day, daily_valuation, monthly_states, tri, f"v4_{number:04d}_{day.date()}")
        signal_by_date[day] = signal
        analogue_parts.append(analogues)
    rows: list[dict[str, object]] = []
    trade_dates = pd.DatetimeIndex(upstream.date)
    for frequency, dates in dates_by_frequency.items():
        for sequence, day in enumerate(dates):
            execution, initial = next_execution(day, trade_dates)
            signal = signal_by_date[day]
            forecast = signal["forecast_3y_median"]
            enough = bool(signal["enough_analogues"])
            binary = 2 if enough and float(forecast) < 0 else 0
            three = 0 if not enough else (2 if float(forecast) < 0 else (1 if float(forecast) < 0.03 else 0))
            rows.append(
                {
                    "frequency": frequency,
                    "sequence": sequence,
                    "eval_date": day,
                    "execution_date": execution,
                    "initial_listing_exception": initial,
                    "binary_target_qty": binary,
                    "three_tier_target_qty": three,
                    **{key: value for key, value in signal.items() if key not in {"decision_id", "state_date"}},
                }
            )
    schedule = pd.DataFrame(rows).sort_values(["frequency", "execution_date"]).reset_index(drop=True)
    analogues = pd.concat(analogue_parts, ignore_index=True)
    frozen = decisions[["state_date", "forecast_3y_median", "signal_on"]].copy()
    recomputed = schedule[schedule.frequency.eq("monthly")][["eval_date", "forecast_3y_median", "binary_target_qty"]]
    parity = frozen.merge(recomputed, left_on="state_date", right_on="eval_date", suffixes=("_frozen", "_new"))
    median_diff = float((parity.forecast_3y_median_frozen - parity.forecast_3y_median_new).abs().max())
    signal_equal = bool((parity.signal_on.astype(int) * 2 == parity.binary_target_qty).all())
    if median_diff > 1e-14 or not signal_equal:
        raise RuntimeError(f"Monthly signal parity failed: {median_diff}, {signal_equal}")
    if (analogues.forward_end_date > analogues.as_of).any():
        raise RuntimeError("Analogue outcome leakage")
    cache = pd.DataFrame(signal_by_date.values()).rename(columns={"state_date": "eval_date"})
    cache["binary_target_qty"] = np.where(cache.enough_analogues & (cache.forecast_3y_median < 0), 2, 0)
    cache["three_tier_target_qty"] = np.where(
        ~cache.enough_analogues,
        0,
        np.where(cache.forecast_3y_median < 0, 2, np.where(cache.forecast_3y_median < 0.03, 1, 0)),
    )
    return schedule, cache, analogues, {"monthly_forecast_median_max_abs": median_diff, "monthly_signal_equal": signal_equal}


def tenor_target_date(tenor: str, eval_date: pd.Timestamp, execution_day: pd.Timestamp, maintenance: bool) -> pd.Timestamp:
    if tenor == "front":
        return execution_day
    months = 2 if tenor == "2m" else 3
    basis = execution_day if maintenance else eval_date
    return basis + pd.DateOffset(months=months)


def selected_month(options: pd.DataFrame, day: pd.Timestamp, target_date: pd.Timestamp) -> pd.Timestamp:
    chain = options[(options.date == day) & (options.actual_expiry > day)]
    if chain.empty:
        raise RuntimeError(f"No future option month on {day.date()}")
    months = chain[["contract_month", "actual_expiry"]].drop_duplicates().copy()
    months["distance"] = (months.actual_expiry - target_date).abs().dt.days
    return pd.Timestamp(months.sort_values(["distance", "actual_expiry"], ascending=[True, False]).iloc[0].contract_month)


def lowest_liquid(options: pd.DataFrame, day: pd.Timestamp, month: pd.Timestamp) -> pd.Series | None:
    chain = options[(options.date == day) & (options.contract_month == month)].copy()
    if chain.empty:
        return None
    literal_min = float(chain.strike.min())
    liquid = chain[
        chain.open.notna() & (chain.open > 0) & (chain.volume > 0) & (chain.open_interest > 0)
    ].sort_values(["strike", "contract"])
    if liquid.empty:
        return None
    row = liquid.iloc[0].copy()
    row["literal_min_strike"] = literal_min
    row["liquidity_fallback"] = bool(float(row.strike) != literal_min)
    return row


@dataclass
class Position:
    contract: str
    contract_month: pd.Timestamp
    actual_expiry: pd.Timestamp
    qty: int
    prior_settle: float


def executable_exit(row: pd.Series) -> bool:
    return bool(pd.notna(row.open) and float(row.open) > 0 and float(row.volume) > 0)


def option_row(lookup: pd.DataFrame, contract: str, day: pd.Timestamp) -> pd.Series:
    key = (contract, day)
    if key not in lookup.index:
        raise RuntimeError(f"Missing option row {contract} {day.date()}")
    row = lookup.loc[key]
    if isinstance(row, pd.DataFrame):
        raise RuntimeError(f"Duplicate option row {contract} {day.date()}")
    return row


def run_candidate(
    upstream: pd.DataFrame,
    schedule: pd.DataFrame,
    options: pd.DataFrame,
    frequency: str,
    tenor: str,
    tier: str,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    target_column = "binary_target_qty" if tier == "binary" else "three_tier_target_qty"
    events = {
        pd.Timestamp(row.execution_date): row
        for row in schedule[schedule.frequency.eq(frequency)].itertuples(index=False)
    }
    lookup = options.set_index(["contract", "date"])
    result = upstream[["date", "settle"]].copy()
    for suffix, default in [
        ("put_pnl_ret", 0.0), ("put_cost_rate", 0.0), ("put_qty_held", 0), ("put_qty_eod", 0),
        ("put_mark_notional", 0.0), ("put_contract", ""), ("buy_qty", 0), ("sell_qty", 0),
        ("expired_qty", 0), ("deferred_adjustment", False),
    ]:
        result[f"{label}_{suffix}"] = default
    active: Position | None = None
    latest_target = 0
    latest_eval: pd.Timestamp | None = None
    pending = False
    pending_since: pd.Timestamp | None = None
    maintenance = False
    trades: list[dict[str, object]] = []

    for idx, day_row in result.iterrows():
        day = pd.Timestamp(day_row.date)
        denominator = float(result.loc[idx - 1, "settle"]) if idx > 0 else float(day_row.settle)
        event = events.get(day)
        if event is not None:
            latest_target = int(getattr(event, target_column))
            latest_eval = pd.Timestamp(event.eval_date)
            pending = True
            pending_since = day
            maintenance = False
        if active is None and latest_target > 0 and not pending:
            pending = True
            pending_since = day
            maintenance = True

        equivalent_points = 0.0
        buy_qty = sell_qty = 0
        traded = False
        action = ""
        old_contract = active.contract if active else ""
        old_qty = active.qty if active else 0
        target_date: pd.Timestamp | None = None
        desired_month: pd.Timestamp | None = None
        new_row: pd.Series | None = None

        if pending:
            if latest_target > 0:
                if latest_eval is None:
                    raise RuntimeError("Positive target without valuation state")
                target_date = tenor_target_date(tenor, latest_eval, day, maintenance)
                desired_month = selected_month(options, day, target_date)
                if active is not None and active.contract_month == desired_month:
                    new_row = option_row(lookup, active.contract, day)
                else:
                    new_row = lowest_liquid(options, day, desired_month)

            if active is None:
                if latest_target == 0:
                    pending = False
                elif new_row is not None:
                    initial = idx == 0
                    entry = float(new_row.settle) if initial else float(new_row.open)
                    if not initial:
                        equivalent_points += latest_target * 0.5 * (float(new_row.settle) - entry)
                    buy_qty = latest_target
                    active = Position(
                        str(new_row.contract), pd.Timestamp(new_row.contract_month), pd.Timestamp(new_row.actual_expiry),
                        latest_target, float(new_row.settle)
                    )
                    traded = True
                    action = "initial_settle_buy" if initial else "open_buy"
                    pending = False
            elif latest_target == 0:
                old = option_row(lookup, active.contract, day)
                if executable_exit(old):
                    equivalent_points += active.qty * 0.5 * (float(old.open) - active.prior_settle)
                    sell_qty = active.qty
                    active = None
                    traded = True
                    action = "open_exit"
                    pending = False
                else:
                    result.loc[idx, f"{label}_deferred_adjustment"] = True
            elif desired_month == active.contract_month:
                delta = latest_target - active.qty
                if delta == 0:
                    pending = False
                else:
                    old = option_row(lookup, active.contract, day)
                    can_trade = executable_exit(old) and float(old.open_interest) > 0
                    if can_trade:
                        equivalent_points += active.qty * 0.5 * (float(old.open) - active.prior_settle)
                        equivalent_points += latest_target * 0.5 * (float(old.settle) - float(old.open))
                        buy_qty = max(delta, 0)
                        sell_qty = max(-delta, 0)
                        active.qty = latest_target
                        active.prior_settle = float(old.settle)
                        traded = True
                        action = "open_increase" if delta > 0 else "open_reduce"
                        pending = False
                    else:
                        result.loc[idx, f"{label}_deferred_adjustment"] = True
            else:
                old = option_row(lookup, active.contract, day)
                if executable_exit(old) and new_row is not None:
                    equivalent_points += active.qty * 0.5 * (float(old.open) - active.prior_settle)
                    equivalent_points += latest_target * 0.5 * (float(new_row.settle) - float(new_row.open))
                    sell_qty = active.qty
                    buy_qty = latest_target
                    active = Position(
                        str(new_row.contract), pd.Timestamp(new_row.contract_month), pd.Timestamp(new_row.actual_expiry),
                        latest_target, float(new_row.settle)
                    )
                    traded = True
                    action = "open_roll"
                    pending = False
                else:
                    result.loc[idx, f"{label}_deferred_adjustment"] = True

        if not traded and active is not None:
            mark = option_row(lookup, active.contract, day)
            equivalent_points += active.qty * 0.5 * (float(mark.settle) - active.prior_settle)
            active.prior_settle = float(mark.settle)

        result.loc[idx, f"{label}_put_pnl_ret"] = equivalent_points / denominator
        result.loc[idx, f"{label}_put_cost_rate"] = (buy_qty + sell_qty) * MO_CONTRACT_SIDE_COST
        result.loc[idx, f"{label}_buy_qty"] = buy_qty
        result.loc[idx, f"{label}_sell_qty"] = sell_qty
        result.loc[idx, f"{label}_put_qty_held"] = old_qty if (traded and active is None) else (active.qty if active else 0)

        if traded:
            trades.append(
                {
                    "candidate": label,
                    "frequency": frequency,
                    "tenor": tenor,
                    "tier": tier,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": pending_since,
                    "actual_execution_date": day,
                    "delay_calendar_days": int((day - pending_since).days) if pending_since is not None else 0,
                    "action": action,
                    "target_qty": latest_target,
                    "old_contract": old_contract,
                    "old_qty": old_qty,
                    "new_contract": active.contract if active else "",
                    "new_qty": active.qty if active else 0,
                    "target_date": target_date,
                    "desired_contract_month": desired_month,
                    "buy_qty": buy_qty,
                    "sell_qty": sell_qty,
                    "new_strike": float(new_row.strike) if new_row is not None and active is not None else np.nan,
                    "new_open": float(new_row.open) if new_row is not None and active is not None else np.nan,
                    "new_volume": float(new_row.volume) if new_row is not None and active is not None else np.nan,
                    "new_open_interest": float(new_row.open_interest) if new_row is not None and active is not None else np.nan,
                    "literal_min_strike": float(new_row.literal_min_strike) if new_row is not None and "literal_min_strike" in new_row else np.nan,
                    "liquidity_fallback": bool(new_row.liquidity_fallback) if new_row is not None and "liquidity_fallback" in new_row else False,
                }
            )

        if active is not None and active.actual_expiry == day:
            result.loc[idx, f"{label}_expired_qty"] = active.qty
            active = None
            if latest_target > 0:
                pending = True
                pending_since = None
                maintenance = True

        if active is not None:
            mark = option_row(lookup, active.contract, day)
            active.prior_settle = float(mark.settle)
            result.loc[idx, f"{label}_put_qty_eod"] = active.qty
            result.loc[idx, f"{label}_put_mark_notional"] = active.qty * 0.5 * float(mark.settle) / float(day_row.settle)
            result.loc[idx, f"{label}_put_contract"] = active.contract
        if pending and pending_since is None:
            pending_since = day + pd.Timedelta(days=1)

    return result, pd.DataFrame(trades)


def assemble(upstream: pd.DataFrame, v2: pd.DataFrame, overlays: dict[str, pd.DataFrame]) -> pd.DataFrame:
    daily = upstream.copy()
    daily["no_put_ret"] = daily.baseline_net_ret
    daily["no_put_cash_ret"] = daily.baseline_plus_cash_ret
    for label, overlay in overlays.items():
        daily = daily.merge(overlay.drop(columns=["settle"]), on="date", validate="one_to_one")
        daily[f"{label}_gross_ret"] = daily.im_gross_ret + daily[f"{label}_put_pnl_ret"]
        daily[f"{label}_ret"] = (
            (1 + daily[f"{label}_gross_ret"]) * (1 - daily.cost_rate) * (1 - daily[f"{label}_put_cost_rate"]) - 1
        )
        daily[f"{label}_cash_weight"] = (CASH_WEIGHT - daily[f"{label}_put_mark_notional"]).clip(lower=0)
        daily[f"{label}_cash_ret"] = daily[f"{label}_ret"] + daily[f"{label}_cash_weight"] * CASH_DAILY
        daily[f"nav_{label}"] = (1 + daily[f"{label}_ret"]).cumprod()
    daily["nav_no_put"] = (1 + daily.no_put_ret).cumprod()
    net_diff = float((daily[f"{BASELINE}_ret"] - v2.immediate_gated_net_ret).abs().max())
    cash_diff = float((daily[f"{BASELINE}_cash_ret"] - v2.immediate_gated_plus_cash_ret).abs().max())
    if net_diff > 1e-14 or cash_diff > 1e-14:
        raise RuntimeError(f"Frozen v2 parity failed: net={net_diff}, cash={cash_diff}")
    core = ["no_put_ret", *[f"{label}_ret" for label in GRID]]
    if daily[core].isna().any().any() or (daily[core] <= -1).any().any():
        raise RuntimeError("Invalid v4 daily returns")
    return daily


def ret_col(candidate: str, cash: bool = False) -> str:
    if candidate == "no_put":
        return "no_put_cash_ret" if cash else "no_put_ret"
    return f"{candidate}_cash_ret" if cash else f"{candidate}_ret"


def parameter_parts(candidate: str) -> tuple[str, str, str]:
    if candidate == "no_put":
        return "none", "none", "none"
    frequency, tenor, *tier_parts = candidate.split("_")
    return frequency, tenor, "_".join(tier_parts)


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    start, end = pd.Timestamp(daily.date.min()), pd.Timestamp(daily.date.max())
    windows = {
        "full": start,
        "last_10y": end - pd.DateOffset(years=10),
        "last_5y": end - pd.DateOffset(years=5),
        "last_3y": end - pd.DateOffset(years=3),
        "last_1y": end - pd.DateOffset(years=1),
    }
    formal_rows, scan_rows, wide_rows = [], [], []
    for candidate in CANDIDATES:
        frequency, tenor, tier = parameter_parts(candidate)
        wide: dict[str, object] = {"candidate": candidate, "frequency": frequency, "tenor": tenor, "tier": tier}
        for window, cutoff in windows.items():
            available = window == "full" or start <= cutoff
            subset = daily[daily.date >= cutoff] if available else daily.iloc[0:0]
            base = {
                "candidate": candidate, "frequency": frequency, "tenor": tenor, "tier": tier,
                "window": window, "available": available,
                "requested_start": cutoff.date().isoformat(),
                "actual_start": subset.date.min().date().isoformat() if available else "",
                "end": end.date().isoformat(), "rows": len(subset),
            }
            if available:
                base.update(metrics(subset[ret_col(candidate)]))
                base.update({f"cash_{key}": value for key, value in metrics(subset[ret_col(candidate, True)]).items()})
            else:
                for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "cash_total_return", "cash_ann_return", "cash_ann_vol", "cash_sharpe_repo", "cash_max_dd"]:
                    base[key] = np.nan
            formal_rows.append(base)
            clipped = subset if available else daily
            values = metrics(clipped[ret_col(candidate)])
            scan_rows.append(
                {
                    "candidate": candidate, "segment": window,
                    "start": clipped.date.min().date().isoformat(), "end": clipped.date.max().date().isoformat(),
                    "rows": len(clipped), "ann_return": values["ann_return"], "ann_vol": values["ann_vol"],
                    "sharpe_repo": values["sharpe_repo"], "max_dd": values["max_dd"],
                    "frequency": frequency, "tenor": tenor, "tier": tier,
                    "requested_window_available": available, "clipped_to_available_history": not available,
                }
            )
            wide[f"ann_return_{window}"] = values["ann_return"]
            wide[f"max_dd_{window}"] = values["max_dd"]
            wide[f"available_{window}"] = available
        wide_rows.append(wide)
    return pd.DataFrame(formal_rows), pd.DataFrame(scan_rows), pd.DataFrame(wide_rows)


def annual_output(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, subset in daily.groupby(daily.date.dt.year):
        for candidate in CANDIDATES:
            rows.append({"year": year, "candidate": candidate, **metrics(subset[ret_col(candidate)])})
    return pd.DataFrame(rows)


def exposure_output(daily: pd.DataFrame, trades: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    rows = [{
        "candidate": "no_put", "frequency": "none", "tenor": "none", "tier": "none",
        "average_put_qty": 0.0, "protected_day_ratio": 0.0, "buy_qty": 0, "sell_qty": 0,
        "trade_events": 0, "contract_rolls": 0, "deferred_days": 0, "liquidity_fallback_trades": 0,
        "put_cost_sum": 0.0, "signal_evaluations": 0, "signal_switches": 0,
    }]
    for candidate in GRID:
        frequency, tenor, tier = parameter_parts(candidate)
        qty = daily[f"{candidate}_put_qty_held"]
        subset = trades[trades.candidate.eq(candidate)]
        target_col = "binary_target_qty" if tier == "binary" else "three_tier_target_qty"
        signals = schedule[schedule.frequency.eq(frequency)].sort_values("execution_date")
        rows.append(
            {
                "candidate": candidate, "frequency": frequency, "tenor": tenor, "tier": tier,
                "average_put_qty": float(qty.mean()), "protected_day_ratio": float((qty > 0).mean()),
                "buy_qty": int(daily[f"{candidate}_buy_qty"].sum()),
                "sell_qty": int(daily[f"{candidate}_sell_qty"].sum()),
                "trade_events": len(subset), "contract_rolls": int(subset.action.eq("open_roll").sum()),
                "deferred_days": int(daily[f"{candidate}_deferred_adjustment"].sum()),
                "liquidity_fallback_trades": int(subset.liquidity_fallback.astype(bool).sum()) if len(subset) else 0,
                "put_cost_sum": float(daily[f"{candidate}_put_cost_rate"].sum()),
                "signal_evaluations": len(signals),
                "signal_switches": int((signals[target_col].diff().fillna(0) != 0).sum()),
            }
        )
    return pd.DataFrame(rows)


def fmt(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value * 100:.2f}%"


def build_record(formal: pd.DataFrame, exposure: pd.DataFrame) -> str:
    full = formal[formal.window.eq("full")].set_index("candidate")
    three = formal[formal.window.eq("last_3y")].set_index("candidate")
    one = formal[formal.window.eq("last_1y")].set_index("candidate")
    lines = [
        "# IM估值频率 × MO Put期限 v4：结果记录", "",
        "运行日期：2026-08-16  ", "研究状态：参数网格研究；未获准实盘  ",
        "正式样本：2022-07-22至2026-08-14（986个交易日）", "",
        "## 同一3个月Put下的评估频率", "",
        "| 档位 | 频率 | 全样本 CAGR / MaxDD | 3年 CAGR / MaxDD | 1年 CAGR / MaxDD |", "|---|---|---:|---:|---:|",
    ]
    for tier in TIERS:
        for frequency in FREQUENCIES:
            candidate = f"{frequency}_3m_{tier}"
            lines.append(f"| {tier} | {frequency} | {fmt(full.loc[candidate,'ann_return'])} / {fmt(full.loc[candidate,'max_dd'])} | {fmt(three.loc[candidate,'ann_return'])} / {fmt(three.loc[candidate,'max_dd'])} | {fmt(one.loc[candidate,'ann_return'])} / {fmt(one.loc[candidate,'max_dd'])} |")
    lines.extend(["", "## 同一月评下的Put期限", "", "| 档位 | 期限 | 全样本 CAGR / MaxDD | 3年 CAGR / MaxDD | 1年 CAGR / MaxDD |", "|---|---|---:|---:|---:|"])
    for tier in TIERS:
        for tenor in TENORS:
            candidate = f"monthly_{tenor}_{tier}"
            lines.append(f"| {tier} | {tenor} | {fmt(full.loc[candidate,'ann_return'])} / {fmt(full.loc[candidate,'max_dd'])} | {fmt(three.loc[candidate,'ann_return'])} / {fmt(three.loc[candidate,'max_dd'])} | {fmt(one.loc[candidate,'ann_return'])} / {fmt(one.loc[candidate,'max_dd'])} |")
    lines.extend(["", "## 完整18组全样本", "", "| 候选 | CAGR / MaxDD | 加70%现金 CAGR / MaxDD | 平均Put张数 | 保护日 | Put成本 | 延迟日 |", "|---|---:|---:|---:|---:|---:|---:|"])
    exp = exposure.set_index("candidate")
    for candidate in GRID:
        lines.append(f"| `{candidate}` | {fmt(full.loc[candidate,'ann_return'])} / {fmt(full.loc[candidate,'max_dd'])} | {fmt(full.loc[candidate,'cash_ann_return'])} / {fmt(full.loc[candidate,'cash_max_dd'])} | {exp.loc[candidate,'average_put_qty']:.3f} | {exp.loc[candidate,'protected_day_ratio']*100:.2f}% | {exp.loc[candidate,'put_cost_sum']*100:.3f}% | {int(exp.loc[candidate,'deferred_days'])} |")
    lines.extend(
        [
            "", "10年和5年窗口因IM/MO历史从2022-07-22才开始，在正式用户指标中为N/A。", "",
            "## 方法和边界", "",
            "- 月/周/日评均使用真实日终PE、PB、ERP和过去一年已实现股息；T收盘确认，下一共同交易日开盘执行。历史类比锚点仍为冻结月末状态。",
            "- `front`为最近到期月；`2m`/`3m`为实际到期日最接近T+2/T+3自然月。信号不变且目标月份不变时不换Put。",
            "- 100%保护为2张MO、50%为1张。每张买卖0.5bp；70%现金年化3%且扣Put权利金；不放大方向杠杆。",
            "- 官方开盘价无有效成交时延迟整笔调整；不计盘口冲击、附加保证金和行权/结算费。样本只有约4年，本结果不是交易建议。",
            "", "## 复现", "",
            f"- 冻结规格SHA-256：`{SPEC_HASH.lower()}`。", f"- 脚本SHA-256：`{sha256(Path(__file__))}`。",
            "- 命令：`python.exe im_valuation_frequency_tenor_scan_v4.py`。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    daily: pd.DataFrame, schedule: pd.DataFrame, cache: pd.DataFrame, analogues: pd.DataFrame,
    trades: pd.DataFrame, formal: pd.DataFrame, scan_summary: pd.DataFrame, wide: pd.DataFrame,
    annual: pd.DataFrame, exposure: pd.DataFrame, manifest: dict[str, object],
) -> None:
    record = build_record(formal, exposure)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_nav.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv", index=False)
    cache.to_csv(OUTPUT / "valuation_signals.csv", index=False)
    analogues.to_csv(OUTPUT / "signal_analogues.csv", index=False)
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    (OUTPUT / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (OUTPUT / "command_log.txt").write_text("python.exe im_valuation_frequency_tenor_scan_v4.py\n", encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    scan_record = """# Quant Parameter Scan Record

## Run Metadata

- Run id: `20260816_im_val_freq_tenor_v4`
- Scan type: `two_parameter_grid` plus frozen binary/three-tier reporting
- Source-change rule: `research_only_no_source_change`
- Decision: pending post-run review
- Stability Classification: pending post-run review

## Research Question

Compare monthly/weekly/daily valuation evaluation and front/2m/3m MO Put tenors under fixed binary and three-tier rules.

## Implementation Anchor

- Entrypoint: `im_valuation_frequency_tenor_scan_v4.py`
- Baseline: `monthly_3m_binary`, exact parity required against frozen v2.

## Data Snapshot

Real CFFEX IM/MO data and daily valuation inputs, formal returns 2022-07-22 through 2026-08-14.

## Cost and Execution Assumptions

T close signal, next common open execution; 0.5bp per MO contract side; 70% cash at 3%; no directional leverage amplification.

## Runtime Override Plan

Research-only candidate grid; no production default or frozen upstream source changed.

## Commands

`python.exe im_valuation_frequency_tenor_scan_v4.py`

## Output Files

- `scan_summary.csv`
- `window_metrics.csv`
- `scan_meta.json`
- `command_log.txt`

## Full-Sample Results

See `scan_summary.csv` and the appended formal record below.

## Window Results

10y/5y are unavailable in the formal report; strict scan fields use clipped actual history with explicit availability flags.

## Decision

Pending post-run review.

## User-Facing Summary

""" + record
    (SCAN / "record.md").write_text(scan_record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python.exe im_valuation_frequency_tenor_scan_v4.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "two_parameter_grid",
            "baseline": {"candidate": BASELINE, "parity_source": str(V2_DAILY)},
            "candidate_grid": GRID,
            "data_snapshot": manifest["sample"], "cost_model": manifest["cost_model"],
            "parity_check": manifest["checks"], "source_hashes": manifest["inputs"],
            "warnings": [
                "Formal 10y/5y metrics are N/A; strict scan tables use clipped full history and explicit flags.",
                "Official opens do not guarantee order-book capacity; exercise/settlement extra fees excluded.",
            ],
            "git_status_after": git_status(),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run() -> None:
    verify()
    upstream, v2, decisions, monthly_states, tri, raw_options = load_inputs()
    daily_valuation, feature_diffs = build_daily_valuation()
    schedule, cache, analogues, signal_checks = build_signal_schedules(
        upstream, decisions, daily_valuation, monthly_states, tri
    )
    expiry_map = actual_expiry_map(raw_options, upstream)
    options = prepare_options(raw_options, expiry_map)
    overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    for frequency in FREQUENCIES:
        for tenor in TENORS:
            for tier in TIERS:
                label = f"{frequency}_{tenor}_{tier}"
                overlay, trade_log = run_candidate(upstream, schedule, options, frequency, tenor, tier, label)
                overlays[label] = overlay
                trade_parts.append(trade_log)
    trades = pd.concat(trade_parts, ignore_index=True)
    daily = assemble(upstream, v2, overlays)
    formal, scan_summary, wide = metric_outputs(daily)
    annual = annual_output(daily)
    exposure = exposure_output(daily, trades, schedule)
    net_parity = float((daily[f"{BASELINE}_ret"] - v2.immediate_gated_net_ret).abs().max())
    cash_parity = float((daily[f"{BASELINE}_cash_ret"] - v2.immediate_gated_plus_cash_ret).abs().max())
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": "python.exe im_valuation_frequency_tenor_scan_v4.py",
        "spec_sha256": sha256(SPEC), "script_sha256": sha256(Path(__file__)),
        "inputs": {
            str(path.relative_to(ROOT)): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in [UPSTREAM, V2_DAILY, V2_DECISIONS, MONTHLY_STATES, VALUATION, PRICE, TRI, GOV10Y, OPTIONS]
        },
        "sample": {
            "start": str(START.date()), "end": str(END.date()), "rows": len(upstream),
            "timezone": "Asia/Shanghai", "data_source": "CFFEX official IM/MO plus daily valuation sources frozen in valuation v3",
        },
        "grid": {"frequencies": FREQUENCIES, "tenors": TENORS, "tiers": TIERS, "candidate_count": len(GRID)},
        "cost_model": {"mo_per_contract_side": MO_CONTRACT_SIDE_COST, "cash_weight": CASH_WEIGHT, "cash_annual_return": 0.03},
        "checks": {
            **signal_checks,
            "monthly_v2_net_parity_max_abs": net_parity,
            "monthly_v2_cash_parity_max_abs": cash_parity,
            "daily_feature_month_end_max_abs": feature_diffs,
            "analogue_causal": bool((analogues.forward_end_date <= analogues.as_of).all()),
            "dates_unique": bool(daily.date.is_unique),
            "core_nan_count": int(daily[[ret_col(candidate) for candidate in CANDIDATES]].isna().sum().sum()),
        },
    }
    write_outputs(daily, schedule, cache, analogues, trades, formal, scan_summary, wide, annual, exposure, manifest)


if __name__ == "__main__":
    run()
