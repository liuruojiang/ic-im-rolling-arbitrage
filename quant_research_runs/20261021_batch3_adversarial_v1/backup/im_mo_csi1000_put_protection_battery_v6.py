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

import ic_510500_put_proxy_validation_v1 as proxy
import im_mo_front95_fixed_dynamic_momentum_validation_v5 as v5
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_csi1000_put_protection_battery_v6"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "a938ac055dfbb82b18512777b288647ec6188a486d91f5682fd5ca67b57c82e4"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260817_im_mo_csi1000_put_protection_battery_v6"
DATA = ROOT / "data" / VERSION

V4_SHA256 = "c654aa7c30c4a89954f8c7db7d352664ab3ac0c5455c2b26248c5aca75476461"
V5_SHA256 = "b4e77f6f1691f18dba7a517f4f024bb2eec9e8feadb46782e74a0ac63b18ab4b"
PROXY_SHA256 = "5836849ca4c0e42ab4a04e2c82d81f049b5f2fb2799333c67177209b8fc2a7a3"

MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-08-14")
TRADING_DAYS = 252
CASH_WEIGHT = 0.70
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0
PUT_SIDE_COST = 0.0001
OHLC = DATA / "sina_sh000852_index.csv"
DATA_MANIFEST = DATA / "data_manifest.json"

PRICE = v4.PRICE
TRI = v4.TRI
GOV10Y = v4.GOV10Y
IM_BASE = v4.UPSTREAM
MO_QUOTES = v4.OPTIONS
IM_QUOTES = v5.IM_QUOTES
Q50 = proxy.DATA / "qvix_50etf.csv"
ETF50 = proxy.DATA / "sina_510050_etf.csv"

WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}
STRUCTURES = ["front_exit", "2m_monthly_exit", "3m_monthly_exit", "3cycle_hold_expiry"]
MONEYNESS = [0.85, 0.90, 0.95]
MOMENTUM_HORIZONS = list(range(20, 121, 10))
_THIRD_FRIDAY_CACHE: dict[tuple[pd.Timestamp, pd.Timestamp, int, int], pd.Timestamp] = {}
_LISTED_MONTH_CACHE: dict[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp], tuple[pd.Timestamp, ...]] = {}
_MODEL_MONTH_CACHE: dict[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp], pd.Timestamp] = {}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v6 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v6 sidecar mismatch")
    dependencies = [(Path(v4.__file__), V4_SHA256), (Path(v5.__file__), V5_SHA256), (Path(proxy.__file__), PROXY_SHA256)]
    for path, expected in dependencies:
        if sha256(path.resolve()) != expected:
            raise RuntimeError(f"Frozen dependency mismatch: {path.name}")
    if OUTPUT.exists() or SCAN.exists():
        raise FileExistsError("Formal v6 output or scan directory already exists")
    manifest = json.loads(DATA_MANIFEST.read_text(encoding="utf-8"))
    if manifest["spec_sha256"] != SPEC_SHA256 or manifest["output"]["sha256"] != sha256(OHLC):
        raise RuntimeError("Frozen v6 data manifest mismatch")
    return manifest


def third_friday(month: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
    month = pd.Timestamp(month.year, month.month, 1)
    key = (pd.Timestamp(trade_dates[0]), pd.Timestamp(trade_dates[-1]), month.year, month.month)
    if key in _THIRD_FRIDAY_CACHE:
        return _THIRD_FRIDAY_CACHE[key]
    target = month + pd.Timedelta(days=(4 - month.weekday()) % 7 + 14)
    location = int(trade_dates.searchsorted(target, side="left"))
    result = pd.Timestamp(trade_dates[location]) if location < len(trade_dates) else pd.Timestamp(target)
    _THIRD_FRIDAY_CACHE[key] = result
    return result


def model_listed_months(day: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    key = (pd.Timestamp(trade_dates[0]), pd.Timestamp(trade_dates[-1]), pd.Timestamp(day))
    if key in _LISTED_MONTH_CACHE:
        return list(_LISTED_MONTH_CACHE[key])
    start = pd.Timestamp(day.year, day.month, 1)
    future = []
    for number in range(18):
        month = start + pd.DateOffset(months=number)
        if third_friday(month, trade_dates) > day:
            future.append(month)
    result = tuple(sorted(set([*future[:2], *[value for value in future[2:] if value.month in {3, 6, 9, 12}][:2]])))
    _LISTED_MONTH_CACHE[key] = result
    return list(result)


def model_month(day: pd.Timestamp, target: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
    key = (
        pd.Timestamp(trade_dates[0]), pd.Timestamp(trade_dates[-1]),
        pd.Timestamp(day), pd.Timestamp(target),
    )
    if key in _MODEL_MONTH_CACHE:
        return _MODEL_MONTH_CACHE[key]
    months = model_listed_months(day, trade_dates)
    result = sorted(
        months,
        key=lambda value: (
            abs((third_friday(value, trade_dates) - target).days),
            -third_friday(value, trade_dates).value,
        ),
    )[0]
    _MODEL_MONTH_CACHE[key] = result
    return result


def model_market() -> tuple[pd.DataFrame, dict[str, float]]:
    official = pd.read_csv(PRICE, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "official_close"})
    tri = pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "tri_close"})
    ohlc = pd.read_csv(OHLC, parse_dates=["date"])[["date", "open", "close"]].rename(
        columns={"open": "spot_open", "close": "sina_close"}
    )
    etf = pd.read_csv(ETF50, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "etf50_close"})
    q50 = pd.read_csv(Q50, parse_dates=["date"])[["date", "open", "close"]].rename(
        columns={"open": "qvix_open", "close": "qvix_close"}
    )
    official_history = official.merge(tri, on="date", validate="one_to_one")
    frame = official_history.merge(ohlc, on="date", validate="one_to_one")
    frame["spot_close"] = frame["official_close"]
    frame["close_relative_error"] = (frame["sina_close"] / frame["official_close"] - 1.0).abs()
    frame = frame.merge(etf, on="date", how="left", validate="one_to_one")
    frame["rv1000"] = np.log(frame["spot_close"]).diff().rolling(60, min_periods=60).std(ddof=1) * math.sqrt(252)
    frame["rv50"] = np.log(frame["etf50_close"]).diff().rolling(60, min_periods=60).std(ddof=1) * math.sqrt(252)
    frame["rv_ratio_close"] = frame["rv1000"] / frame["rv50"]
    frame["rv_ratio_open"] = frame["rv_ratio_close"].shift(1)
    frame = frame.merge(q50, on="date", how="left", validate="one_to_one")
    frame["qvix_close_used"] = frame["qvix_close"].ffill()
    frame["qvix_open_used"] = frame["qvix_open"].where(frame["qvix_open"].gt(0), frame["qvix_close_used"].shift(1))
    frame["sigma_close"] = frame["qvix_close_used"] / 100.0 * frame["rv_ratio_close"]
    frame["sigma_open"] = frame["qvix_open_used"] / 100.0 * frame["rv_ratio_open"]

    gov = pd.read_csv(GOV10Y, parse_dates=["date"]).rename(columns={"date": "gov_date"})
    frame = pd.merge_asof(
        frame.sort_values("date"), gov.sort_values("gov_date"), left_on="date", right_on="gov_date",
        direction="backward", allow_exact_matches=True,
    )
    targets = frame[["date"]].copy()
    targets["prior_target"] = targets["date"] - pd.DateOffset(years=1)
    prior = official_history[["date", "official_close", "tri_close"]].rename(
        columns={"date": "prior_date", "official_close": "prior_spot", "tri_close": "prior_tri"}
    )
    targets = pd.merge_asof(
        targets.sort_values("prior_target"), prior.sort_values("prior_date"),
        left_on="prior_target", right_on="prior_date", direction="backward", allow_exact_matches=True,
    )
    frame = frame.merge(targets[["date", "prior_spot", "prior_tri"]], on="date", validate="one_to_one")
    frame["dividend_close"] = ((frame["tri_close"] / frame["prior_tri"]) / (frame["spot_close"] / frame["prior_spot"]) - 1.0).clip(lower=0)
    frame["dividend_open"] = frame["dividend_close"].shift(1)
    frame["rate_close"] = frame["gov10y_yield"].ffill()
    frame["rate_open"] = frame["rate_close"].shift(1)
    frame["base_prior_close"] = frame["tri_close"].shift(1)
    sample = frame[(frame["date"] >= MODEL_START) & (frame["date"] <= END)].copy().reset_index(drop=True)
    sample.loc[0, "base_prior_close"] = sample.loc[0, "tri_close"]
    required = [
        "spot_open", "spot_close", "tri_close", "base_prior_close", "sigma_open", "sigma_close",
        "rate_open", "rate_close", "dividend_open", "dividend_close",
    ]
    if sample[required].isna().any().any() or (sample[["spot_open", "spot_close", "sigma_open", "sigma_close"]] <= 0).any().any():
        missing = {column: int(sample[column].isna().sum()) for column in required if sample[column].isna().any()}
        nonpositive = {
            column: int(sample[column].le(0).sum())
            for column in ["spot_open", "spot_close", "sigma_open", "sigma_close"]
            if sample[column].le(0).any()
        }
        raise RuntimeError(f"Incomplete theoretical CSI 1000 option market: missing={missing}, nonpositive={nonpositive}")
    checks = {
        "rows": len(sample),
        "start": str(sample["date"].min().date()),
        "end": str(sample["date"].max().date()),
        "close_median_relative_error": float(sample["close_relative_error"].median()),
        "close_max_relative_error": float(sample["close_relative_error"].max()),
        "sigma_min": float(sample["sigma_close"].min()),
        "sigma_max": float(sample["sigma_close"].max()),
    }
    if checks["close_median_relative_error"] > 0.0005 or checks["close_max_relative_error"] > 0.005:
        raise RuntimeError(f"CSI 1000 OHLC close validation failed: {checks}")
    return sample, checks


def model_baseline(market: pd.DataFrame) -> pd.DataFrame:
    result = market[["date", "tri_close"]].copy()
    result["gross_ret"] = result["tri_close"].pct_change().fillna(0.0)
    dates = pd.DatetimeIndex(result["date"])
    roll_dates = {third_friday(pd.Timestamp(year, month, 1), dates) for year in range(MODEL_START.year, END.year + 1) for month in range(1, 13)}
    result["cost_rate"] = np.where(result["date"].isin(roll_dates), 0.0002, 0.0)
    result.loc[0, "cost_rate"] = 0.0001
    result["net_ret"] = (1.0 + result["gross_ret"]) * (1.0 - result["cost_rate"]) - 1.0
    return result


def signal_state(daily_valuation: pd.DataFrame) -> pd.DataFrame:
    tri = pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "tri_close_all"})
    official_dates = pd.read_csv(OHLC, parse_dates=["date"])[["date"]]
    frame = official_dates.merge(tri, on="date", how="left", validate="one_to_one").merge(
        daily_valuation[["date", "pb_aggregate", "erp", "trailing_dividend_contribution"]],
        on="date", how="left", validate="one_to_one",
    )
    eligible = frame["pb_aggregate"].notna()
    frame["fixed_risk"] = np.nan
    frame.loc[eligible, "fixed_risk"] = v5.fixed_risk(frame.loc[eligible])
    frame["dynamic_risk"] = np.nan
    frame.loc[eligible, "dynamic_risk"] = v5.dynamic_risk(frame.loc[eligible])
    frame["fixed175"] = frame["fixed_risk"].ge(1.75 - 1e-12).astype(int) * 2
    for threshold in [75, 80, 85]:
        frame[f"dynamic{threshold:03d}"] = frame["dynamic_risk"].ge(threshold / 100 - 1e-12).astype(int) * 2
    for horizon in MOMENTUM_HORIZONS:
        frame[f"momentum_{horizon:03d}"] = frame["tri_close_all"] / frame["tri_close_all"].shift(horizon) - 1.0
        frame[f"mom{horizon:03d}"] = frame[f"momentum_{horizon:03d}"].le(1e-12).astype(int) * 2
    frame["fixed175_or_mom120"] = frame[["fixed175", "mom120"]].max(axis=1)
    for threshold in [75, 80, 85]:
        frame[f"dynamic{threshold:03d}_or_mom120"] = frame[[f"dynamic{threshold:03d}", "mom120"]].max(axis=1)
    return frame


def daily_signal_schedule(
    name: str, target_column: str, trade_dates: pd.DatetimeIndex, state: pd.DataFrame
) -> pd.DataFrame:
    lookup = state.set_index("date")
    initial_candidates = lookup.index[lookup.index < trade_dates[0]]
    evaluations = [pd.Timestamp(initial_candidates.max()), *[pd.Timestamp(value) for value in trade_dates[:-1]]]
    rows = []
    for sequence, day in enumerate(evaluations):
        execution = pd.Timestamp(trade_dates[0] if day < trade_dates[0] else trade_dates[trade_dates > day][0])
        qty = int(lookup.loc[day, target_column])
        rows.append({
            "frequency": name, "sequence": sequence, "eval_date": day, "execution_date": execution,
            "initial_listing_exception": bool(day < trade_dates[0]), "binary_target_qty": qty,
            "three_tier_target_qty": qty,
        })
    return pd.DataFrame(rows)


def valuation_schedules(
    trade_dates: pd.DatetimeIndex, daily_valuation: pd.DataFrame, states: pd.DataFrame, tri: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    eligible = daily_valuation[daily_valuation["date"].isin(trade_dates) & (daily_valuation["date"] < trade_dates[-1])][["date"]].copy()
    by_frequency: dict[str, list[pd.Timestamp]] = {"daily": [pd.Timestamp(value) for value in eligible["date"]]}
    eligible["week"] = eligible["date"].dt.to_period("W-FRI")
    eligible["month"] = eligible["date"].dt.to_period("M")
    by_frequency["weekly"] = [pd.Timestamp(value) for value in eligible.groupby("week").tail(1)["date"]]
    by_frequency["monthly"] = [pd.Timestamp(value) for value in eligible.groupby("month").tail(1)["date"]]
    cache: dict[pd.Timestamp, dict[str, object]] = {}
    for number, day in enumerate(sorted(set().union(*[set(values) for values in by_frequency.values()]))):
        forecast, _ = v4.forecast_at(day, daily_valuation, states, tri, f"v6_{number:04d}_{day.date()}")
        cache[day] = forecast
    schedules: dict[str, pd.DataFrame] = {}
    for frequency, evaluations in by_frequency.items():
        rows = []
        for sequence, day in enumerate(evaluations):
            later = trade_dates[trade_dates > day]
            if not len(later):
                continue
            item = cache[day]
            enough = bool(item["enough_analogues"])
            median = float(item["forecast_3y_median"]) if enough else np.nan
            binary = 2 if enough and median < 0 else 0
            three = 0 if not enough else (2 if median < 0 else (1 if median < 0.03 else 0))
            rows.append({
                "frequency": frequency, "sequence": sequence, "eval_date": day,
                "execution_date": pd.Timestamp(later[0]), "initial_listing_exception": False,
                "binary_target_qty": binary, "three_tier_target_qty": three,
                "forecast_3y_median": median, "enough_analogues": enough,
            })
        schedules[frequency] = pd.DataFrame(rows)
    cache_frame = pd.DataFrame([{"eval_date": key, **value} for key, value in cache.items()])
    return schedules, cache_frame


def candidate_definitions() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for frequency in v4.FREQUENCIES:
        for tenor in v4.TENORS:
            for tier in v4.TIERS:
                rows.append({
                    "candidate": f"val_{frequency}_{tenor}_{tier}_m95", "group": "valuation_grid",
                    "signal": f"valuation_{frequency}_{tier}", "frequency": frequency, "tier": tier,
                    "structure": f"{tenor}_exit", "tenor": tenor, "moneyness": 0.95,
                })
    signals = [
        "fixed175", "dynamic075", "dynamic080", "dynamic085", "mom120",
        "dynamic075_or_mom120", "dynamic080_or_mom120", "dynamic085_or_mom120",
    ]
    for signal in signals:
        rows.append({
            "candidate": f"sig_{signal}_front_m95", "group": "signal_front95", "signal": signal,
            "frequency": signal, "tier": "binary", "structure": "front_exit", "tenor": "front",
            "moneyness": 0.95,
        })
    for horizon in MOMENTUM_HORIZONS:
        signal = f"mom{horizon:03d}"
        candidate = f"mom{horizon:03d}_front_m95"
        if horizon == 120:
            candidate = "sig_mom120_front_m95"
        else:
            rows.append({
                "candidate": candidate, "group": "momentum_scan", "signal": signal,
                "frequency": signal, "tier": "binary", "structure": "front_exit", "tenor": "front",
                "moneyness": 0.95,
            })
    for structure in STRUCTURES:
        for moneyness in MONEYNESS:
            if structure == "front_exit" and math.isclose(moneyness, 0.95):
                candidate = "fixed175_or_mom120_front_exit_m95"
            else:
                candidate = f"fixed175_or_mom120_{structure}_m{int(moneyness * 100)}"
            rows.append({
                "candidate": candidate, "group": "tool_grid", "signal": "fixed175_or_mom120",
                "frequency": "fixed175_or_mom120", "tier": "binary", "structure": structure,
                "tenor": {"front_exit": "front", "2m_monthly_exit": "2m", "3m_monthly_exit": "3m"}.get(structure, "3cycle"),
                "moneyness": moneyness,
            })
    table = pd.DataFrame(rows).drop_duplicates("candidate").reset_index(drop=True)
    return table


@dataclass
class ModelPosition:
    month: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    units: float
    fraction: float
    prior_mark: float
    entry_date: pd.Timestamp


def option_price(position: ModelPosition, row: object, when: str) -> float:
    years = max((position.expiry - pd.Timestamp(row.date)).days / 365.0, 0.0)
    return proxy.bs_put(
        float(getattr(row, f"spot_{when}")), position.strike, float(getattr(row, f"rate_{when}")),
        float(getattr(row, f"dividend_{when}")), float(getattr(row, f"sigma_{when}")), years,
    )


def normal_target_month(
    tenor: str, day: pd.Timestamp, eval_date: pd.Timestamp, trade_dates: pd.DatetimeIndex
) -> pd.Timestamp:
    target = day if tenor == "front" else eval_date + pd.DateOffset(months=2 if tenor == "2m" else 3)
    return model_month(day, target, trade_dates)


def run_model_normal(
    market: pd.DataFrame, schedule: pd.DataFrame, tenor: str, moneyness: float, label: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    dates = pd.DatetimeIndex(market["date"])
    active: ModelPosition | None = None
    latest_qty = 0
    latest_eval: pd.Timestamp | None = None
    rows, trades, lives = [], [], []
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        action = ""
        pnl = cost = 0.0
        if event is not None:
            latest_qty = int(event.binary_target_qty)
            latest_eval = pd.Timestamp(event.eval_date)
        desired = None
        if latest_qty > 0:
            if latest_eval is None:
                raise RuntimeError("Positive theoretical target without evaluation")
            desired = normal_target_month(tenor, day, latest_eval, dates)
        needs = event is not None or (active is not None and active.expiry <= day) or (active is None and latest_qty > 0)
        old = active
        if needs:
            if active is not None and latest_qty == 0:
                mark = option_price(active, row, "open")
                pnl += active.units * (mark - active.prior_mark) / float(row.base_prior_close)
                cost += active.fraction * PUT_SIDE_COST
                lives.append({"candidate": label, "entry_date": active.entry_date, "expiry": active.expiry, "exit_date": day, "exit_reason": "signal"})
                active = None
                action = "open_exit"
            elif latest_qty > 0 and active is None:
                expiry = third_friday(desired, dates)
                fraction = latest_qty / 2.0
                units = float(row.base_prior_close) / float(row.spot_open) * fraction
                active = ModelPosition(desired, expiry, float(row.spot_open) * moneyness, units, fraction, 0.0, day)
                open_mark = option_price(active, row, "open")
                close_mark = option_price(active, row, "close")
                pnl += units * (close_mark - open_mark) / float(row.base_prior_close)
                active.prior_mark = close_mark
                cost += fraction * PUT_SIDE_COST
                action = "open_buy"
            elif latest_qty > 0 and active is not None and desired != active.month:
                old_open = option_price(active, row, "open")
                pnl += active.units * (old_open - active.prior_mark) / float(row.base_prior_close)
                cost += active.fraction * PUT_SIDE_COST
                lives.append({"candidate": label, "entry_date": active.entry_date, "expiry": active.expiry, "exit_date": day, "exit_reason": "roll"})
                fraction = latest_qty / 2.0
                expiry = third_friday(desired, dates)
                active = ModelPosition(
                    desired, expiry, float(row.spot_open) * moneyness,
                    float(row.base_prior_close) / float(row.spot_open) * fraction, fraction, 0.0, day,
                )
                open_mark = option_price(active, row, "open")
                close_mark = option_price(active, row, "close")
                pnl += active.units * (close_mark - open_mark) / float(row.base_prior_close)
                active.prior_mark = close_mark
                cost += fraction * PUT_SIDE_COST
                action = "open_roll"
            elif latest_qty > 0 and active is not None and not math.isclose(active.fraction, latest_qty / 2.0):
                open_mark = option_price(active, row, "open")
                pnl += active.units * (open_mark - active.prior_mark) / float(row.base_prior_close)
                old_fraction = active.fraction
                fraction = latest_qty / 2.0
                active.units = float(row.base_prior_close) / float(row.spot_open) * fraction
                active.fraction = fraction
                close_mark = option_price(active, row, "close")
                pnl += active.units * (close_mark - open_mark) / float(row.base_prior_close)
                active.prior_mark = close_mark
                cost += abs(fraction - old_fraction) * PUT_SIDE_COST
                action = "open_resize"
        if not action and active is not None:
            close_mark = option_price(active, row, "close")
            pnl += active.units * (close_mark - active.prior_mark) / float(row.base_prior_close)
            active.prior_mark = close_mark
        if action:
            trades.append({
                "layer": "model", "candidate": label, "signal_eval_date": latest_eval,
                "actual_execution_date": day, "action": action, "target_fraction": latest_qty / 2.0,
                "new_month": active.month if active else pd.NaT, "new_strike": active.strike if active else np.nan,
                "entry_moneyness": moneyness if active else np.nan,
            })
        mark_fraction = 0.0 if active is None else active.units * active.prior_mark / float(row.tri_close)
        rows.append({
            "date": day, "put_pnl_ret": pnl, "put_cost_rate": cost,
            "put_mark_fraction": mark_fraction, "put_fraction": 0.0 if active is None else active.fraction,
            "put_contract": "" if active is None else f"MODEL_{active.month:%y%m}_{active.strike:.4f}",
        })
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def three_cycle_month(
    day: pd.Timestamp, months: list[tuple[pd.Timestamp, pd.Timestamp]], roll_dates: pd.DatetimeIndex
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    future_rolls = roll_dates[roll_dates >= day]
    if len(future_rolls) < 4:
        return None
    r3, r4 = pd.Timestamp(future_rolls[2]), pd.Timestamp(future_rolls[3])
    eligible = [(month, expiry) for month, expiry in months if expiry >= r3 and expiry < r4]
    return sorted(eligible, key=lambda item: (item[1], item[0]))[0] if eligible else None


def run_model_hold(
    market: pd.DataFrame, schedule: pd.DataFrame, moneyness: float, label: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    dates = pd.DatetimeIndex(market["date"])
    roll_dates = pd.DatetimeIndex(sorted({third_friday(pd.Timestamp(y, m, 1), dates) for y in range(2015, 2027) for m in range(1, 13)}))
    active: ModelPosition | None = None
    latest_qty = 0
    latest_eval: pd.Timestamp | None = None
    rows, trades, lives = [], [], []
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        if day in events:
            latest_qty = int(events[day].binary_target_qty)
            latest_eval = pd.Timestamp(events[day].eval_date)
        pnl = cost = 0.0
        action = ""
        if active is None and latest_qty > 0:
            months = [(month, third_friday(month, dates)) for month in model_listed_months(day, dates)]
            selected = three_cycle_month(day, months, roll_dates)
            if selected is not None:
                month, expiry = selected
                fraction = latest_qty / 2.0
                active = ModelPosition(
                    month, expiry, float(row.spot_open) * moneyness,
                    float(row.base_prior_close) / float(row.spot_open) * fraction, fraction, 0.0, day,
                )
                open_mark = option_price(active, row, "open")
                close_mark = option_price(active, row, "close")
                pnl += active.units * (close_mark - open_mark) / float(row.base_prior_close)
                active.prior_mark = close_mark
                cost += fraction * PUT_SIDE_COST
                action = "open_buy_hold"
        elif active is not None:
            close_mark = option_price(active, row, "close")
            pnl += active.units * (close_mark - active.prior_mark) / float(row.base_prior_close)
            active.prior_mark = close_mark
        if action:
            trades.append({
                "layer": "model", "candidate": label, "signal_eval_date": latest_eval,
                "actual_execution_date": day, "action": action, "target_fraction": latest_qty / 2.0,
                "new_month": active.month, "new_strike": active.strike, "entry_moneyness": moneyness,
            })
        expired = active is not None and active.expiry == day
        mark_fraction = 0.0 if active is None else active.units * active.prior_mark / float(row.tri_close)
        fraction = 0.0 if active is None else active.fraction
        contract = "" if active is None else f"MODEL_{active.month:%y%m}_{active.strike:.4f}"
        rows.append({
            "date": day, "put_pnl_ret": pnl, "put_cost_rate": cost,
            "put_mark_fraction": mark_fraction, "put_fraction": fraction, "put_contract": contract,
        })
        if expired:
            coverage = int(((roll_dates >= active.entry_date) & (roll_dates <= active.expiry)).sum())
            lives.append({
                "candidate": label, "entry_date": active.entry_date, "expiry": active.expiry,
                "exit_date": day, "exit_reason": "expiry", "covered_rolls": coverage,
            })
            active = None
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def target_selector(
    active: pd.DataFrame,
    moneyness: float,
    option_groups: dict[tuple[pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None,
):
    im_open = active.set_index("date")["open"]

    def select(options: pd.DataFrame, day: pd.Timestamp, month: pd.Timestamp) -> pd.Series | None:
        if option_groups is None:
            chain = options[(options["date"] == day) & (options["contract_month"] == month)].copy()
        else:
            chain = option_groups.get((pd.Timestamp(day), pd.Timestamp(month)), pd.DataFrame()).copy()
        if chain.empty:
            return None
        liquid = chain[
            chain["open"].notna() & chain["open"].gt(0) & chain["volume"].gt(0) & chain["open_interest"].gt(0)
        ].copy()
        if liquid.empty:
            return None
        liquid["entry_moneyness"] = liquid["strike"] / float(im_open.loc[day])
        liquid["target_error"] = (liquid["entry_moneyness"] - moneyness).abs().round(12)
        selected = liquid.sort_values(["target_error", "strike", "contract"]).iloc[0].copy()
        selected["literal_min_strike"] = float(chain["strike"].min())
        selected["liquidity_fallback"] = False
        return selected

    return select


def run_real_normal(
    upstream: pd.DataFrame, options: pd.DataFrame, active_im: pd.DataFrame, schedule: pd.DataFrame,
    frequency: str, tier: str, tenor: str, moneyness: float, label: str,
    option_groups: dict[tuple[pd.Timestamp, pd.Timestamp], pd.DataFrame] | None = None,
    month_groups: dict[pd.Timestamp, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selector = target_selector(active_im, moneyness, option_groups)
    original_selector = v4.lowest_liquid
    original_month = v4.selected_month

    def select_month(_: pd.DataFrame, day: pd.Timestamp, target_date: pd.Timestamp) -> pd.Timestamp:
        if month_groups is None:
            return original_month(options, day, target_date)
        months = month_groups[pd.Timestamp(day)].copy()
        months["distance"] = (months["actual_expiry"] - target_date).abs().dt.days
        return pd.Timestamp(
            months.sort_values(["distance", "actual_expiry"], ascending=[True, False]).iloc[0]["contract_month"]
        )

    v4.lowest_liquid = selector
    v4.selected_month = select_month
    try:
        overlay, trades = v4.run_candidate(upstream, schedule, options, frequency, tenor, tier, label)
    finally:
        v4.lowest_liquid = original_selector
        v4.selected_month = original_month
    result = pd.DataFrame({
        "date": overlay["date"], "put_pnl_ret": overlay[f"{label}_put_pnl_ret"],
        "put_cost_rate": overlay[f"{label}_put_cost_rate"],
        "put_mark_fraction": overlay[f"{label}_put_mark_notional"],
        "put_fraction": overlay[f"{label}_put_qty_eod"] / 2.0,
        "put_contract": overlay[f"{label}_put_contract"],
    })
    if len(trades):
        trades["layer"] = "real"
        entries = trades["new_contract"].fillna("").ne("")
        open_lookup = active_im.set_index("date")["open"]
        trades.loc[entries, "entry_moneyness"] = (
            trades.loc[entries, "new_strike"]
            / trades.loc[entries, "actual_execution_date"].map(open_lookup)
        )
    return result, trades


def run_real_hold(
    upstream: pd.DataFrame, raw_options: pd.DataFrame, active_im: pd.DataFrame,
    schedule: pd.DataFrame, moneyness: float, label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    lookup = options.set_index(["contract", "date"])
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    roll_dates = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].fillna("").ne(""), "date"])
    im_open = active_im.set_index("date")["open"]
    active = None
    latest_qty = 0
    latest_eval = None
    rows, trades, lives = [], [], []
    pending_since = None
    for idx, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        if day in events:
            latest_qty = int(events[day].binary_target_qty)
            latest_eval = pd.Timestamp(events[day].eval_date)
        pnl = cost = 0.0
        action = ""
        denominator = float(base["settle"] if idx == 0 else upstream.loc[idx - 1, "settle"])
        if active is None and latest_qty > 0:
            if pending_since is None:
                pending_since = day
            future_rolls = roll_dates[roll_dates >= day]
            selected = None
            if len(future_rolls) >= 4:
                r3, r4 = pd.Timestamp(future_rolls[2]), pd.Timestamp(future_rolls[3])
                months = options.loc[
                    options["date"].eq(day) & options["actual_expiry"].ge(r3) & options["actual_expiry"].lt(r4),
                    ["contract_month", "actual_expiry"],
                ].drop_duplicates().sort_values(["actual_expiry", "contract_month"])
                for month in months["contract_month"]:
                    selected = target_selector(active_im, moneyness)(options, day, pd.Timestamp(month))
                    if selected is not None:
                        break
            if selected is not None:
                qty = latest_qty
                entry = float(selected["settle"] if idx == 0 else selected["open"])
                if idx > 0:
                    pnl += qty * 0.5 * (float(selected["settle"]) - entry) / denominator
                active = {
                    "contract": str(selected["contract"]), "expiry": pd.Timestamp(selected["actual_expiry"]),
                    "month": pd.Timestamp(selected["contract_month"]), "qty": qty,
                    "prior": float(selected["settle"]), "entry": day,
                }
                cost += qty * v4.MO_CONTRACT_SIDE_COST
                action = "initial_settle_buy_hold" if idx == 0 else "open_buy_hold"
                trades.append({
                    "layer": "real", "candidate": label, "signal_eval_date": latest_eval,
                    "scheduled_execution_date": pending_since, "actual_execution_date": day, "action": action,
                    "target_fraction": qty / 2.0, "new_contract": active["contract"],
                    "new_month": active["month"], "new_strike": float(selected["strike"]),
                    "entry_moneyness": float(selected["strike"]) / float(im_open.loc[day]),
                })
                pending_since = None
        elif active is not None:
            mark = lookup.loc[(active["contract"], day)]
            if isinstance(mark, pd.DataFrame):
                raise RuntimeError("Duplicate real hold mark")
            pnl += active["qty"] * 0.5 * (float(mark["settle"]) - active["prior"]) / denominator
            active["prior"] = float(mark["settle"])
        expired = active is not None and active["expiry"] == day
        mark_fraction = 0.0 if active is None else active["qty"] * 0.5 * active["prior"] / float(base["settle"])
        rows.append({
            "date": day, "put_pnl_ret": pnl, "put_cost_rate": cost,
            "put_mark_fraction": mark_fraction, "put_fraction": 0.0 if active is None else active["qty"] / 2.0,
            "put_contract": "" if active is None else active["contract"],
        })
        if expired:
            coverage = int(((roll_dates >= active["entry"]) & (roll_dates <= active["expiry"])).sum())
            lives.append({
                "candidate": label, "entry_date": active["entry"], "expiry": active["expiry"],
                "exit_date": day, "exit_reason": "expiry", "covered_rolls": coverage,
            })
            active = None
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def assemble_layer(
    layer: str, base: pd.DataFrame, overlays: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    rows = []
    no = base.copy()
    no["candidate"] = "no_put"
    no["put_pnl_ret"] = 0.0
    no["put_cost_rate"] = 0.0
    no["put_mark_fraction"] = 0.0
    no["put_fraction"] = 0.0
    no["put_contract"] = ""
    no["ret"] = no["net_ret"]
    no["cash_ret"] = no["ret"] + CASH_WEIGHT * CASH_DAILY
    rows.append(no)
    for candidate, overlay in overlays.items():
        frame = base.merge(overlay, on="date", validate="one_to_one")
        frame["candidate"] = candidate
        frame["ret"] = (
            (1.0 + frame["gross_ret"] + frame["put_pnl_ret"])
            * (1.0 - frame["cost_rate"]) * (1.0 - frame["put_cost_rate"]) - 1.0
        )
        frame["cash_ret"] = frame["ret"] + (CASH_WEIGHT - frame["put_mark_fraction"]).clip(lower=0) * CASH_DAILY
        rows.append(frame)
    daily = pd.concat(rows, ignore_index=True)
    daily["layer"] = layer
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError(f"Invalid {layer} candidate returns")
    return daily


def metrics(returns: pd.Series) -> dict[str, float]:
    return v4.metrics(returns)


def metrics_tables(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, annual = [], []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        group = group.sort_values("date")
        start, end = pd.Timestamp(group["date"].min()), pd.Timestamp(group["date"].max())
        for window, offset in WINDOWS.items():
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"] >= requested] if available else group.iloc[0:0]
            item = {
                "layer": layer, "candidate": candidate, "window": window, "available": available,
                "requested_start": requested, "actual_start": sample["date"].min() if available else pd.NaT,
                "end": end, "rows": len(sample),
            }
            if available:
                item.update(metrics(sample["cash_ret"]))
            else:
                item.update({key: np.nan for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd"]})
            rows.append(item)
        for year, sample in group.groupby(group["date"].dt.year):
            annual.append({"layer": layer, "candidate": candidate, "year": int(year), **metrics(sample["cash_ret"])})
    return pd.DataFrame(rows), pd.DataFrame(annual)


def exposure_table(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        trade = trades[(trades["layer"] == layer) & (trades["candidate"] == candidate)] if len(trades) else trades
        entries = trade[trade["entry_moneyness"].notna()] if len(trade) and "entry_moneyness" in trade else trade.iloc[0:0]
        rows.append({
            "layer": layer, "candidate": candidate, "protected_days": int(group["put_fraction"].gt(0).sum()),
            "protected_day_ratio": float(group["put_fraction"].gt(0).mean()),
            "average_fraction": float(group["put_fraction"].mean()),
            "put_cost_sum": float(group["put_cost_rate"].sum()), "trade_events": len(trade),
            "average_entry_moneyness": float(entries["entry_moneyness"].mean()) if len(entries) else np.nan,
        })
    return pd.DataFrame(rows)


def cross_validation(daily: pd.DataFrame, definitions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    rows = []
    candidates = definitions["candidate"].tolist()
    for candidate in candidates:
        model = daily[(daily["layer"] == "model") & (daily["candidate"] == candidate)][["date", "put_pnl_ret", "cash_ret"]]
        real = daily[(daily["layer"] == "real") & (daily["candidate"] == candidate)][["date", "put_pnl_ret", "cash_ret"]]
        joined = model.merge(real, on="date", suffixes=("_model", "_real"), validate="one_to_one")
        model_no = daily[(daily["layer"] == "model") & daily["candidate"].eq("no_put") & daily["date"].isin(joined["date"])]
        real_no = daily[(daily["layer"] == "real") & daily["candidate"].eq("no_put") & daily["date"].isin(joined["date"])]
        model_dd = metrics(joined["cash_ret_model"])["max_dd"] - metrics(model_no["cash_ret"])["max_dd"]
        real_dd = metrics(joined["cash_ret_real"])["max_dd"] - metrics(real_no["cash_ret"])["max_dd"]
        rows.append({
            "candidate": candidate, "rows": len(joined),
            "put_pnl_correlation": float(joined["put_pnl_ret_model"].corr(joined["put_pnl_ret_real"])),
            "put_pnl_median_abs_error": float((joined["put_pnl_ret_model"] - joined["put_pnl_ret_real"]).abs().median()),
            "model_dd_improvement_overlap": model_dd, "real_dd_improvement_overlap": real_dd,
            "dd_direction_agrees": bool(np.sign(model_dd) == np.sign(real_dd)),
        })
    table = pd.DataFrame(rows)
    stats = {
        "median_put_pnl_correlation": float(table["put_pnl_correlation"].median()),
        "median_put_pnl_abs_error": float(table["put_pnl_median_abs_error"].median()),
        "dd_direction_agreement_ratio": float(table["dd_direction_agrees"].mean()),
    }
    stats["model_sensitive"] = bool(
        stats["median_put_pnl_correlation"] < 0.50 or stats["dd_direction_agreement_ratio"] < 0.60
    )
    return table, stats


def decisions(formal: pd.DataFrame, exposure: pd.DataFrame, definitions: pd.DataFrame, cross: dict[str, object]) -> tuple[pd.DataFrame, dict[str, object]]:
    lookup = formal.set_index(["layer", "candidate", "window"])
    exposure_lookup = exposure.set_index(["layer", "candidate"])
    rows = []
    for item in definitions.itertuples(index=False):
        candidate = item.candidate
        model_ok = True
        for window in ["full", "last_10y", "last_5y"]:
            row, no = lookup.loc[("model", candidate, window)], lookup.loc[("model", "no_put", window)]
            model_ok &= bool(row["max_dd"] > no["max_dd"] and row["ann_return"] - no["ann_return"] >= -0.02)
        real_full, real_no_full = lookup.loc[("real", candidate, "full")], lookup.loc[("real", "no_put", "full")]
        real_three, real_no_three = lookup.loc[("real", candidate, "last_3y")], lookup.loc[("real", "no_put", "last_3y")]
        real_one, real_no_one = lookup.loc[("real", candidate, "last_1y")], lookup.loc[("real", "no_put", "last_1y")]
        real_ok = bool(
            real_full["max_dd"] - real_no_full["max_dd"] >= 0.005
            and real_three["max_dd"] - real_no_three["max_dd"] >= 0.005
            and real_full["ann_return"] - real_no_full["ann_return"] >= -0.02
            and real_three["ann_return"] - real_no_three["ann_return"] >= -0.02
            and real_one["ann_return"] - real_no_one["ann_return"] >= -0.03
            and real_one["max_dd"] - real_no_one["max_dd"] >= -0.01
        )
        activity = bool(
            exposure_lookup.loc[("model", candidate), "protected_days"] >= 20
            and exposure_lookup.loc[("real", candidate), "protected_days"] >= 20
        )
        rows.append({
            "candidate": candidate, "group": item.group, "model_pass": model_ok,
            "real_pass": real_ok, "activity_pass": activity, "two_layer_base_pass": model_ok and real_ok and activity,
            "model_full_cagr_delta": lookup.loc[("model", candidate, "full"), "ann_return"] - lookup.loc[("model", "no_put", "full"), "ann_return"],
            "model_full_dd_improvement": lookup.loc[("model", candidate, "full"), "max_dd"] - lookup.loc[("model", "no_put", "full"), "max_dd"],
            "real_full_cagr_delta": real_full["ann_return"] - real_no_full["ann_return"],
            "real_full_dd_improvement": real_full["max_dd"] - real_no_full["max_dd"],
        })
    table = pd.DataFrame(rows)
    passed = table[table["two_layer_base_pass"]]
    conclusion = "research_watchlist" if len(passed) and not cross["model_sensitive"] else ("mixed_not_confirmed" if len(passed) else "not_confirmed")
    summary = {
        "conclusion": conclusion, "model_sensitive": cross["model_sensitive"],
        "two_layer_base_pass_count": len(passed), "two_layer_base_pass_candidates": passed["candidate"].tolist(),
        "research_status": "research_only_not_live_approved",
    }
    return table, summary


def record(formal: pd.DataFrame, decision: pd.DataFrame, summary: dict[str, object], cross: dict[str, object]) -> str:
    required = formal[formal["window"].isin(WINDOWS)].copy()
    no_put = required[required["candidate"].eq("no_put")][["layer", "window", "available", "ann_return", "max_dd"]]
    ranked = decision.sort_values(["two_layer_base_pass", "real_full_dd_improvement", "real_full_cagr_delta"], ascending=False).head(15)
    return "\n".join([
        "# 中证1000 Put保护全类别复测 v6", "",
        "> 研究回测；未获准实盘。模型层2022年前不是真实IM/MO。", "",
        "## 结论", "", f"- `{summary['conclusion']}`；model_sensitive=`{summary['model_sensitive']}`。", "",
        "## 无Put基线", "", no_put.to_markdown(index=False, floatfmt=".4f"), "",
        "## 两层综合排序（前15）", "", ranked.to_markdown(index=False, floatfmt=".4f"), "",
        "## 模型—真实重叠验证", "", f"- 日Put PnL相关中位数：{cross['median_put_pnl_correlation']:.4f}。",
        f"- MaxDD改善方向一致率：{cross['dd_direction_agreement_ratio']:.2%}。", "",
        "## 限制", "",
        "- 长层是中证1000 TRI+理论Put，不包含2022年前不存在的IM贴水。",
        "- 理论Put使用50ETF QVIX与实现波动率比，是模型敏感性，不是历史MO报价。",
        "- 真实MO只有约4年，多轮历史复用且没有独立OOS。", "",
    ])


def main() -> None:
    data_manifest = verify_inputs()
    market, market_checks = model_market()
    model_base = model_baseline(market)
    upstream, _, decisions_v2, states, tri, raw_options = v4.load_inputs()
    daily_valuation, feature_diffs = v4.build_daily_valuation()
    if max(feature_diffs.values()) > 1e-14:
        raise RuntimeError("Frozen daily valuation parity failed")
    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    state = signal_state(daily_valuation)
    model_valuation_schedules, model_forecasts = valuation_schedules(model_dates, daily_valuation, states, tri)
    real_valuation_schedule, real_forecasts, real_analogues, v4_signal_checks = v4.build_signal_schedules(
        upstream, decisions_v2, daily_valuation, states, tri
    )
    model_signal_schedules = {
        name: daily_signal_schedule(name, name, model_dates, state)
        for name in [
            "fixed175", "dynamic075", "dynamic080", "dynamic085", "mom120",
            "fixed175_or_mom120", "dynamic075_or_mom120", "dynamic080_or_mom120", "dynamic085_or_mom120",
            *[f"mom{h:03d}" for h in MOMENTUM_HORIZONS],
        ]
    }
    real_signal_schedules = {
        name: daily_signal_schedule(name, name, real_dates, state)
        for name in model_signal_schedules
    }
    definitions = candidate_definitions()
    active_im = v5.active_im_opens(upstream)
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    prepared_options = v4.prepare_options(raw_options, expiry_map)
    option_groups = {
        (pd.Timestamp(day), pd.Timestamp(month)): group.copy()
        for (day, month), group in prepared_options.groupby(["date", "contract_month"], sort=False)
    }
    month_groups = {
        pd.Timestamp(day): group[["contract_month", "actual_expiry"]].drop_duplicates().loc[
            lambda value: value["actual_expiry"] > pd.Timestamp(day)
        ].copy()
        for day, group in prepared_options.groupby("date", sort=False)
    }
    model_overlays, real_overlays = {}, {}
    trade_parts, life_parts = [], []
    for item in definitions.itertuples(index=False):
        if item.group == "valuation_grid":
            model_schedule = model_valuation_schedules[item.frequency].copy()
            real_schedule = real_valuation_schedule.copy()
        else:
            model_schedule = model_signal_schedules[item.signal]
            real_schedule = real_signal_schedules[item.signal]
        if item.tier == "three_tier":
            model_schedule = model_schedule.copy()
            model_schedule["binary_target_qty"] = model_schedule["three_tier_target_qty"]
        if item.structure == "3cycle_hold_expiry":
            mo, mt, ml = run_model_hold(market, model_schedule, item.moneyness, item.candidate)
            ro, rt, rl = run_real_hold(upstream, raw_options, active_im, real_schedule, item.moneyness, item.candidate)
        else:
            mo, mt, ml = run_model_normal(market, model_schedule, item.tenor, item.moneyness, item.candidate)
            ro, rt = run_real_normal(
                upstream, prepared_options, active_im, real_schedule,
                item.frequency, item.tier, item.tenor, item.moneyness, item.candidate,
                option_groups, month_groups,
            )
            rl = pd.DataFrame()
        model_overlays[item.candidate] = mo
        real_overlays[item.candidate] = ro
        if len(mt):
            trade_parts.append(mt)
        if len(rt):
            trade_parts.append(rt)
        if len(ml):
            ml["layer"] = "model"
            life_parts.append(ml)
        if len(rl):
            rl["layer"] = "real"
            life_parts.append(rl)
    real_base = upstream[["date", "im_gross_ret", "cost_rate", "im_net_ret"]].rename(
        columns={"im_gross_ret": "gross_ret", "im_net_ret": "net_ret"}
    )
    model_daily = assemble_layer("model", model_base, model_overlays)
    real_daily = assemble_layer("real", real_base, real_overlays)
    daily = pd.concat([model_daily, real_daily], ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False) if life_parts else pd.DataFrame()
    formal, annual = metrics_tables(daily)
    exposure = exposure_table(daily, trades)
    cross_table, cross_stats = cross_validation(daily, definitions)
    decision, decision_summary = decisions(formal, exposure, definitions, cross_stats)

    expected_candidates = set(definitions["candidate"]) | {"no_put"}
    for layer in ["model", "real"]:
        if set(daily.loc[daily["layer"].eq(layer), "candidate"]) != expected_candidates:
            raise RuntimeError(f"Incomplete {layer} candidate set")
    holds = lifecycles[lifecycles["candidate"].str.contains("3cycle_hold_expiry", na=False)]
    complete_model_holds = holds[(holds["layer"] == "model") & holds["covered_rolls"].notna()]
    complete_real_holds = holds[(holds["layer"] == "real") & holds["covered_rolls"].notna()]
    model_hold_ratio = float(complete_model_holds["covered_rolls"].eq(3).mean()) if len(complete_model_holds) else np.nan
    real_hold_ratio = float(complete_real_holds["covered_rolls"].eq(3).mean()) if len(complete_real_holds) else np.nan
    if not math.isclose(model_hold_ratio, 1.0, abs_tol=1e-12) or real_hold_ratio < 0.90:
        raise RuntimeError(f"Strict three-cycle audit failed: {model_hold_ratio}, {real_hold_ratio}")
    real_no = real_daily[real_daily["candidate"].eq("no_put")]
    parity = float(np.abs(real_no["ret"].to_numpy() - upstream["im_net_ret"].to_numpy()).max(initial=0))
    if parity > 1e-14:
        raise RuntimeError("Real no-Put parity failed")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    SCAN.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost.csv", index=False)
    definitions.to_csv(OUTPUT / "candidate_definitions.csv", index=False)
    trades.to_csv(OUTPUT / "trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(OUTPUT / "lifecycle_audit.csv", index=False)
    cross_table.to_csv(OUTPUT / "model_real_cross_validation.csv", index=False)
    decision.to_csv(OUTPUT / "decision_table.csv", index=False)
    model_forecasts.to_csv(OUTPUT / "model_valuation_forecasts.csv.gz", index=False, compression="gzip")
    real_forecasts.to_csv(OUTPUT / "real_valuation_forecasts.csv.gz", index=False, compression="gzip")
    real_analogues.to_csv(OUTPUT / "real_valuation_analogues.csv.gz", index=False, compression="gzip")
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps({**decision_summary, "cross_validation": cross_stats}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "record.md").write_text(record(formal, decision, decision_summary, cross_stats), encoding="utf-8")
    input_paths = [PRICE, TRI, GOV10Y, IM_BASE, MO_QUOTES, IM_QUOTES, Q50, ETF50, OHLC, v4.VALUATION, v4.MONTHLY_STATES]
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION, "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256, "script_sha256": sha256(Path(__file__)),
        "dependencies": {"v4": V4_SHA256, "v5": V5_SHA256, "proxy": PROXY_SHA256},
        "inputs": {str(path.relative_to(ROOT)): {"sha256": sha256(path), "bytes": path.stat().st_size} for path in input_paths},
        "data_freeze_manifest": data_manifest,
        "samples": {"model": [str(MODEL_START.date()), str(END.date())], "real": [str(REAL_START.date()), str(END.date())]},
        "candidate_count_per_layer": len(expected_candidates),
        "market_checks": market_checks, "valuation_checks": feature_diffs,
        "v4_signal_checks": v4_signal_checks, "cross_validation": cross_stats,
        "three_cycle_exact_ratio": {"model": model_hold_ratio, "real": real_hold_ratio},
        "decision": decision_summary, "git_status": git_status(),
        "warnings": [
            "Model layer before 2022 is CSI 1000 TRI plus theoretical Put, not historical IM/MO",
            "Model layer contains no pre-2022 IM discount carry",
            "No independent out-of-sample evidence; research only",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(
        "python.exe -m pytest test_im_mo_csi1000_put_protection_battery_v6.py -q\n"
        "python.exe im_mo_csi1000_put_protection_battery_v6.py\n",
        encoding="utf-8",
    )
    scan_summary = decision.sort_values(["two_layer_base_pass", "real_full_dd_improvement"], ascending=False)
    scan_summary.to_csv(SCAN / "summary.csv", index=False)
    (SCAN / "config.json").write_text(
        json.dumps({"version": VERSION, "spec_sha256": SPEC_SHA256, "rows": len(definitions)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**decision_summary, "cross_validation": cross_stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
