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


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_proxy_validation_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = "7c72a554a32523f1357147f0689d89bc7745070d66925bf3d1c0f6451acd910a"
DATA = ROOT / "data" / VERSION
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_proxy_validation_v1"

IC_DAILY = ROOT / "outputs" / "ic_monthly_discount_roll_v1" / "daily_nav.csv"
MONTHLY_STATES = ROOT / "outputs" / "ic_im_valuation_risk_premium_forecast_v3" / "monthly_valuation_state.csv"
VALUATION_DATA = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3"
VALUATION = VALUATION_DATA / "legulegu_000905_valuation.csv"
PRICE = VALUATION_DATA / "csindex_000905.csv"
TRI = VALUATION_DATA / "csindex_H00905.csv"
GOV10Y = VALUATION_DATA / "chinabond_government_10y.csv"

MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-09-19")
END = pd.Timestamp("2026-08-14")
TRADING_DAYS = 252
CASH_WEIGHT = 0.70
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0
PUT_FULL_SIDE_COST = 0.0001
FREQUENCIES = ["monthly", "weekly", "daily"]
TENORS = ["front", "2m", "3m"]
TIERS = ["binary", "three_tier"]
MONEYNESS = [0.80, 0.85, 0.90]
REAL_CANDIDATES = [f"real_{f}_{t}_{tier}" for f in FREQUENCIES for t in TENORS for tier in TIERS]
MODEL_CANDIDATES = [
    f"model_m{int(m * 100)}_{f}_{t}_{tier}"
    for m in MONEYNESS
    for f in FREQUENCIES
    for t in TENORS
    for tier in TIERS
]
WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip()


def verify() -> dict[str, object]:
    if sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v1 specification hash mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    manifest_path = DATA / "data_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Frozen data manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("spec_sha256") != SPEC_HASH:
        raise RuntimeError("Frozen data was collected under a different specification")
    for filename, item in manifest["files"].items():
        path = DATA / filename
        if not path.exists() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Frozen data hash mismatch: {path}")
    return manifest


def load_inputs() -> dict[str, pd.DataFrame]:
    frames = {
        "ic": pd.read_csv(IC_DAILY, parse_dates=["date"]).sort_values("date").reset_index(drop=True),
        "states": pd.read_csv(MONTHLY_STATES, parse_dates=["date"]),
        "tri": pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].sort_values("date"),
        "snapshots": pd.read_csv(
            DATA / "sse_510500_standard_put_snapshots.csv.gz",
            parse_dates=["date", "contract_month"],
            dtype={"security_id": str},
        ),
        "histories": pd.read_csv(
            DATA / "sina_510500_put_histories.csv.gz",
            parse_dates=["date"],
            dtype={"security_id": str},
        ),
        "q50": pd.read_csv(DATA / "qvix_50etf.csv", parse_dates=["date"]),
        "q500": pd.read_csv(DATA / "qvix_500etf.csv", parse_dates=["date"]),
        "etf50": pd.read_csv(DATA / "sina_510050_etf.csv", parse_dates=["date"]),
        "etf500": pd.read_csv(DATA / "sina_510500_etf.csv", parse_dates=["date"]),
        "index_sina": pd.read_csv(DATA / "sina_000905_index.csv", parse_dates=["date"]),
    }
    frames["states"] = frames["states"][frames["states"]["product"].eq("IC")].sort_values("date")
    ic = frames["ic"]
    if ic["date"].min() != MODEL_START or ic["date"].max() != END or len(ic) != 2756:
        raise RuntimeError("Unexpected frozen IC daily baseline")
    if frames["snapshots"].duplicated(["date", "security_id"]).any():
        raise RuntimeError("Duplicate frozen option snapshots")
    if frames["histories"].duplicated(["security_id", "date"]).any():
        raise RuntimeError("Duplicate frozen option histories")
    return frames


def build_daily_valuation() -> tuple[pd.DataFrame, dict[str, float]]:
    valuation = pd.read_csv(VALUATION, parse_dates=["date"])
    price = pd.read_csv(PRICE, parse_dates=["date"]).rename(columns={"close": "price_close"})
    tri = pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "tri_close"})
    gov = pd.read_csv(GOV10Y, parse_dates=["date"]).rename(columns={"date": "gov10y_date"})
    daily = valuation.merge(
        price[["date", "price_close", "official_rolling_pe"]], on="date", validate="one_to_one"
    ).merge(tri, on="date", validate="one_to_one")
    daily = daily[daily["date"] >= pd.Timestamp("2008-01-01")].sort_values("date").reset_index(drop=True)
    daily = pd.merge_asof(
        daily,
        gov.sort_values("gov10y_date"),
        left_on="date",
        right_on="gov10y_date",
        direction="backward",
        allow_exact_matches=True,
    )
    daily["gov10y_staleness_days"] = (daily["date"] - daily["gov10y_date"]).dt.days
    targets = daily[["date"]].copy()
    targets["prior_target_date"] = targets["date"] - pd.DateOffset(years=1)
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
        (daily["tri_close"] / daily["prior_tri_close"])
        / (daily["price_close"] / daily["prior_price_close"])
        - 1.0
    )
    daily["earnings_yield"] = 1.0 / daily["pe_aggregate_ttm"]
    daily["erp"] = daily["earnings_yield"] - daily["gov10y_yield"]
    required = ["pe_aggregate_ttm", "pb_aggregate", "erp", "trailing_dividend_contribution", "tri_close"]
    if daily[daily["date"] >= pd.Timestamp("2008-01-01")][required].isna().any().any():
        raise RuntimeError("Invalid IC daily valuation reconstruction")
    frozen = pd.read_csv(MONTHLY_STATES, parse_dates=["date"])
    frozen = frozen[frozen["product"].eq("IC")]
    matched = daily[daily["date"].isin(frozen["date"])].merge(
        frozen[["date", *required]], on="date", suffixes=("_daily", "_frozen"), validate="one_to_one"
    )
    diffs = {
        feature: float((matched[f"{feature}_daily"] - matched[f"{feature}_frozen"]).abs().max())
        for feature in required
    }
    return daily, diffs


def completed_period_initial(daily: pd.DataFrame, start: pd.Timestamp, frequency: str) -> pd.Timestamp:
    pre = daily[daily["date"] < start][["date"]].copy()
    if frequency == "daily":
        return pd.Timestamp(pre["date"].max())
    period_code = "M" if frequency == "monthly" else "W-FRI"
    pre["period"] = pre["date"].dt.to_period(period_code)
    completed = pre[pre["period"].map(lambda value: value.end_time.normalize() < start)]
    return pd.Timestamp(completed.groupby("period").tail(1)["date"].iloc[-1])


def evaluation_dates(
    frequency: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    ic_dates: pd.DatetimeIndex,
    daily_valuation: pd.DataFrame,
) -> list[pd.Timestamp]:
    initial = completed_period_initial(daily_valuation, start, frequency)
    eligible = pd.DataFrame({"date": ic_dates[(ic_dates >= start) & (ic_dates < end)]})
    if frequency == "daily":
        regular = eligible["date"].tolist()
    else:
        code = "M" if frequency == "monthly" else "W-FRI"
        eligible["period"] = eligible["date"].dt.to_period(code)
        eligible = eligible[eligible["period"].map(lambda value: value.end_time.normalize() < end)]
        regular = eligible.groupby("period").tail(1)["date"].tolist()
    return [initial, *[pd.Timestamp(value) for value in regular]]


def next_execution(eval_date: pd.Timestamp, start: pd.Timestamp, dates: pd.DatetimeIndex) -> tuple[pd.Timestamp, bool]:
    if eval_date < start:
        return start, True
    later = dates[dates > eval_date]
    if not len(later):
        raise RuntimeError(f"No execution after {eval_date.date()}")
    return pd.Timestamp(later[0]), False


def forecast_at(
    day: pd.Timestamp,
    daily: pd.DataFrame,
    states: pd.DataFrame,
    tri: pd.DataFrame,
    decision_id: str,
) -> tuple[dict[str, object], pd.DataFrame]:
    history = states[states["date"] <= day].copy()
    if history.empty or pd.Timestamp(history["date"].max()) != day:
        current = daily[daily["date"].eq(day)]
        if len(current) != 1:
            raise RuntimeError(f"Missing IC daily valuation on {day.date()}")
        source = current.iloc[0]
        row = {column: np.nan for column in states.columns}
        for feature in ["pe_aggregate_ttm", "pb_aggregate", "erp", "trailing_dividend_contribution", "tri_close"]:
            row[feature] = float(source[feature])
        row.update({"date": day, "product": "IC", "index_name": "中证500"})
        history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    return walk_forward_forecast(history, tri, day, decision_id)


def build_schedules(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    states: pd.DataFrame,
    tri: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.DatetimeIndex(ic["date"])
    definitions: dict[tuple[str, str], list[pd.Timestamp]] = {}
    for layer, start in [("model", MODEL_START), ("real", REAL_START)]:
        for frequency in FREQUENCIES:
            definitions[(layer, frequency)] = evaluation_dates(frequency, start, END, dates, daily_valuation)
    unique_days = sorted(set().union(*[set(values) for values in definitions.values()]))
    by_day: dict[pd.Timestamp, dict[str, object]] = {}
    analogue_parts: list[pd.DataFrame] = []
    for number, day in enumerate(unique_days):
        signal, analogues = forecast_at(day, daily_valuation, states, tri, f"ic_v1_{number:04d}_{day.date()}")
        by_day[day] = signal
        analogue_parts.append(analogues)
    rows: list[dict[str, object]] = []
    for (layer, frequency), evals in definitions.items():
        start = MODEL_START if layer == "model" else REAL_START
        for sequence, day in enumerate(evals):
            execution, initial = next_execution(day, start, dates)
            signal = by_day[day]
            enough = bool(signal["enough_analogues"])
            forecast = float(signal["forecast_3y_median"]) if enough else np.nan
            binary = 1.0 if enough and forecast < 0 else 0.0
            tier = 0.0 if not enough else (1.0 if forecast < 0 else (0.5 if forecast < 0.03 else 0.0))
            rows.append(
                {
                    "layer": layer,
                    "frequency": frequency,
                    "sequence": sequence,
                    "eval_date": day,
                    "execution_date": execution,
                    "initial_exception": initial,
                    "binary_target_fraction": binary,
                    "three_tier_target_fraction": tier,
                    **{key: value for key, value in signal.items() if key not in {"decision_id", "state_date"}},
                }
            )
    schedule = pd.DataFrame(rows).sort_values(["layer", "frequency", "execution_date"]).reset_index(drop=True)
    if schedule.duplicated(["layer", "frequency", "execution_date"]).any():
        raise RuntimeError("Duplicate signal executions")
    analogues = pd.concat(analogue_parts, ignore_index=True)
    if (analogues["forward_end_date"] > analogues["as_of"]).any():
        raise RuntimeError("Analogue outcome leakage")
    signals = pd.DataFrame(by_day.values()).rename(columns={"state_date": "eval_date"})
    return schedule, signals, analogues


def fourth_wednesday(month: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
    month = pd.Timestamp(month.year, month.month, 1)
    days = pd.date_range(month, month + pd.offsets.MonthEnd(0), freq="D")
    target = days[days.weekday == 2][3]
    later = trade_dates[trade_dates >= target]
    return pd.Timestamp(later[0]) if len(later) else pd.Timestamp(target)


def target_date(tenor: str, eval_date: pd.Timestamp, execution_day: pd.Timestamp, maintenance: bool) -> pd.Timestamp:
    if tenor == "front":
        return execution_day
    basis = execution_day if maintenance else eval_date
    return basis + pd.DateOffset(months=2 if tenor == "2m" else 3)


def select_real_month(
    snapshots: pd.DataFrame,
    day: pd.Timestamp,
    target: pd.Timestamp,
    trade_dates: pd.DatetimeIndex,
) -> pd.Timestamp | None:
    chain = snapshots[snapshots["date"].eq(day)]
    if chain.empty:
        return None
    months = chain[["contract_month"]].drop_duplicates().copy()
    months["expiry"] = months["contract_month"].map(lambda value: fourth_wednesday(value, trade_dates))
    months = months[months["expiry"] > day]
    if months.empty:
        return None
    months["distance"] = (months["expiry"] - target).abs().dt.days
    return pd.Timestamp(months.sort_values(["distance", "expiry"], ascending=[True, False]).iloc[0]["contract_month"])


def model_listed_months(day: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    start = pd.Timestamp(day.year, day.month, 1)
    candidates: list[pd.Timestamp] = []
    for number in range(18):
        month = start + pd.DateOffset(months=number)
        if fourth_wednesday(month, trade_dates) > day:
            candidates.append(month)
    nearby = candidates[:2]
    quarterly = [value for value in candidates[2:] if value.month in {3, 6, 9, 12}][:2]
    return sorted(set([*nearby, *quarterly]))


def select_model_month(day: pd.Timestamp, target: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
    months = model_listed_months(day, trade_dates)
    ranked = sorted(
        months,
        key=lambda value: (abs((fourth_wednesday(value, trade_dates) - target).days), -fourth_wednesday(value, trade_dates).value),
    )
    return ranked[0]


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def bs_put(spot: float, strike: float, rate: float, dividend: float, sigma: float, years: float) -> float:
    if years <= 0:
        return max(strike - spot, 0.0)
    if spot <= 0 or strike <= 0 or sigma <= 0:
        raise RuntimeError("Invalid Black-Scholes input")
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * sigma * sigma) * years) / (sigma * root)
    d2 = d1 - sigma * root
    return strike * math.exp(-rate * years) * norm_cdf(-d2) - spot * math.exp(-dividend * years) * norm_cdf(-d1)


def prepare_model_market(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    q50: pd.DataFrame,
    etf50: pd.DataFrame,
    index_sina: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, float]]:
    official = pd.read_csv(PRICE, parse_dates=["date"])[["date", "close"]].rename(columns={"close": "spot_close"})
    index = index_sina[["date", "open", "close"]].rename(columns={"open": "spot_open", "close": "sina_close"})
    cross = official.merge(index, on="date", validate="one_to_one")
    close_relative_error = (cross["sina_close"] / cross["spot_close"] - 1.0).abs()
    etf = etf50[["date", "close"]].rename(columns={"close": "etf50_close"})
    cross = cross.merge(etf, on="date", how="left", validate="one_to_one")
    cross["rv500"] = np.log(cross["spot_close"]).diff().rolling(60, min_periods=60).std(ddof=1) * math.sqrt(252)
    cross["rv50"] = np.log(cross["etf50_close"]).diff().rolling(60, min_periods=60).std(ddof=1) * math.sqrt(252)
    cross["rv_ratio_close"] = cross["rv500"] / cross["rv50"]
    cross["rv_ratio_open"] = cross["rv_ratio_close"].shift(1)
    q = q50[["date", "open", "close"]].rename(columns={"open": "qvix_open", "close": "qvix_close"})
    cross = cross.merge(q, on="date", how="left", validate="one_to_one")
    cross["qvix_prior_close"] = cross["qvix_close"].ffill().shift(1)
    cross["qvix_open_used"] = cross["qvix_open"].where(cross["qvix_open"].gt(0), cross["qvix_prior_close"])
    cross["qvix_close_used"] = cross["qvix_close"].ffill()
    cross["sigma_open"] = cross["qvix_open_used"] / 100.0 * cross["rv_ratio_open"]
    cross["sigma_close"] = cross["qvix_close_used"] / 100.0 * cross["rv_ratio_close"]
    values = daily_valuation[["date", "gov10y_yield", "trailing_dividend_contribution"]].copy()
    cross = cross.merge(values, on="date", how="left", validate="one_to_one")
    cross["rate_close"] = cross["gov10y_yield"].ffill()
    cross["rate_open"] = cross["rate_close"].shift(1)
    cross["dividend_close"] = cross["trailing_dividend_contribution"].clip(lower=0)
    cross["dividend_open"] = cross["dividend_close"].shift(1)
    market = ic[["date", "settle"]].merge(cross, on="date", how="left", validate="one_to_one")
    required = [
        "spot_open", "spot_close", "sigma_open", "sigma_close", "rate_open", "rate_close", "dividend_open", "dividend_close"
    ]
    missing = market.loc[market["date"] >= MODEL_START, ["date", *required]].copy()
    if missing[required].isna().any().any():
        detail = {
            column: {
                "count": int(missing[column].isna().sum()),
                "first": str(missing.loc[missing[column].isna(), "date"].min().date()),
                "last": str(missing.loc[missing[column].isna(), "date"].max().date()),
            }
            for column in required
            if missing[column].isna().any()
        }
        raise RuntimeError(f"Incomplete model market inputs: {detail}")
    checks = {
        "official_sina_close_max_relative_error": float(close_relative_error.max()),
        "official_sina_close_median_relative_error": float(close_relative_error.median()),
        "rv_ratio_min": float(market.loc[market["date"] >= MODEL_START, "rv_ratio_close"].min()),
        "rv_ratio_max": float(market.loc[market["date"] >= MODEL_START, "rv_ratio_close"].max()),
    }
    return market, checks


def qvix_validation(market: pd.DataFrame, q500: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    actual = q500[["date", "close"]].rename(columns={"close": "qvix500_close"})
    joined = market[["date", "sigma_close"]].merge(actual, on="date", validate="one_to_one")
    joined = joined[joined["date"] >= REAL_START].dropna().copy()
    joined["actual_sigma"] = joined["qvix500_close"] / 100.0
    joined["error"] = joined["sigma_close"] - joined["actual_sigma"]
    stats: dict[str, object] = {
        "rows": len(joined),
        "start": str(joined["date"].min().date()),
        "end": str(joined["date"].max().date()),
        "pearson": float(joined["sigma_close"].corr(joined["actual_sigma"])),
        "median_abs_error": float(joined["error"].abs().median()),
        "median_signed_error": float(joined["error"].median()),
    }
    stats["passed"] = bool(
        stats["pearson"] >= 0.60
        and stats["median_abs_error"] <= 0.05
        and abs(stats["median_signed_error"]) <= 0.03
    )
    return joined, stats


@dataclass
class RealPosition:
    security_id: str
    contract_id: str
    contract_month: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    qty: int
    full_qty: int
    fraction: float
    prior_mark: float
    entry_moneyness: float


@dataclass
class ModelPosition:
    contract_month: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    units: float
    fraction: float
    prior_mark: float


def schedule_events(schedule: pd.DataFrame, layer: str, frequency: str) -> dict[pd.Timestamp, object]:
    subset = schedule[(schedule["layer"] == layer) & (schedule["frequency"] == frequency)]
    return {pd.Timestamp(row.execution_date): row for row in subset.itertuples(index=False)}


def tier_column(tier: str) -> str:
    return "binary_target_fraction" if tier == "binary" else "three_tier_target_fraction"


def history_exact(lookup: pd.DataFrame, security_id: str, day: pd.Timestamp) -> pd.Series | None:
    key = (security_id, day)
    if key not in lookup.index:
        return None
    row = lookup.loc[key]
    if isinstance(row, pd.DataFrame):
        raise RuntimeError(f"Duplicate history lookup {security_id} {day.date()}")
    return row


def select_real_contract(
    snapshots: pd.DataFrame,
    history_lookup: pd.DataFrame,
    day: pd.Timestamp,
    month: pd.Timestamp,
) -> tuple[pd.Series, pd.Series] | None:
    chain = snapshots[(snapshots["date"] == day) & (snapshots["contract_month"] == month)].sort_values(
        ["strike", "security_id"]
    )
    for master in chain.itertuples(index=False):
        quote = history_exact(history_lookup, str(master.security_id), day)
        if quote is not None and float(quote["open"]) > 0 and float(quote["volume"]) > 0:
            return pd.Series(master._asdict()), quote
    return None


def real_mark(
    history_groups: dict[str, pd.DataFrame],
    position: RealPosition,
    day: pd.Timestamp,
    etf_close: float,
) -> tuple[float, int, bool]:
    if day >= position.expiry:
        return max(position.strike - etf_close, 0.0), 0, False
    group = history_groups[position.security_id]
    observed = group[group["date"] <= day]
    if observed.empty:
        raise RuntimeError(f"No prior mark for {position.security_id} on {day.date()}")
    row = observed.iloc[-1]
    stale = int((day - pd.Timestamp(row["date"])).days)
    return float(row["close"]), stale, stale > 0


def run_real_candidate(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    snapshots: pd.DataFrame,
    histories: pd.DataFrame,
    etf500: pd.DataFrame,
    frequency: str,
    tenor: str,
    tier: str,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = ic[ic["date"] >= REAL_START].copy().reset_index(drop=True)
    daily["prior_settle"] = ic["settle"].shift(1).loc[daily.index + (len(ic) - len(daily))].to_numpy()
    daily.loc[0, "prior_settle"] = float(ic.loc[ic["date"] < REAL_START, "settle"].iloc[-1])
    etf = etf500.set_index("date")
    history_lookup = histories.set_index(["security_id", "date"])
    history_groups = {key: group.sort_values("date") for key, group in histories.groupby("security_id")}
    events = schedule_events(schedule, "real", frequency)
    trade_dates = pd.DatetimeIndex(ic["date"])
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    active: RealPosition | None = None
    latest_fraction = 0.0
    latest_eval: pd.Timestamp | None = None
    pending = False
    pending_since: pd.Timestamp | None = None
    maintenance = False

    for row in daily.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        if event is not None:
            latest_fraction = float(getattr(event, tier_column(tier)))
            latest_eval = pd.Timestamp(event.eval_date)
            pending = True
            pending_since = day
            maintenance = False
        if active is None and latest_fraction > 0 and not pending:
            pending = True
            pending_since = day
            maintenance = True

        denominator = float(row.prior_settle) * 200.0
        etf_row = etf.loc[day]
        etf_open, etf_close = float(etf_row["open"]), float(etf_row["close"])
        pnl = 0.0
        cost = 0.0
        stale_days = 0
        carried = False
        action = ""
        old = active
        desired_month: pd.Timestamp | None = None
        selected: tuple[pd.Series, pd.Series] | None = None
        new_target_qty = 0

        if pending:
            if latest_fraction > 0:
                if latest_eval is None:
                    raise RuntimeError("Positive real target without valuation date")
                desired_month = select_real_month(
                    snapshots,
                    day,
                    target_date(tenor, latest_eval, day, maintenance),
                    trade_dates,
                )
                if desired_month is not None:
                    full_qty = max(1, int(round(float(row.settle) * 200.0 / (etf_open * 10000.0))))
                    new_target_qty = max(1, int(round(full_qty * latest_fraction)))
                    if active is None or active.contract_month != desired_month:
                        selected = select_real_contract(snapshots, history_lookup, day, desired_month)

            if active is None:
                if latest_fraction == 0:
                    pending = False
                elif selected is not None:
                    master, quote = selected
                    close = float(quote["close"])
                    pnl += new_target_qty * 10000.0 * (close - float(quote["open"])) / denominator
                    full_qty = max(1, int(round(float(row.settle) * 200.0 / (etf_open * 10000.0))))
                    active = RealPosition(
                        str(master["security_id"]), str(master["contract_id"]), pd.Timestamp(master["contract_month"]),
                        fourth_wednesday(pd.Timestamp(master["contract_month"]), trade_dates), float(master["strike"]),
                        new_target_qty, full_qty, latest_fraction, close, float(master["strike"]) / etf_open,
                    )
                    cost += latest_fraction * PUT_FULL_SIDE_COST
                    action = "open_buy"
                    pending = False
            elif latest_fraction == 0:
                quote = history_exact(history_lookup, active.security_id, day)
                if quote is not None and float(quote["open"]) > 0 and float(quote["volume"]) > 0:
                    pnl += active.qty * 10000.0 * (float(quote["open"]) - active.prior_mark) / denominator
                    cost += active.fraction * PUT_FULL_SIDE_COST
                    active = None
                    action = "open_exit"
                    pending = False
            elif desired_month == active.contract_month:
                if math.isclose(latest_fraction, active.fraction, abs_tol=1e-12):
                    pending = False
                else:
                    quote = history_exact(history_lookup, active.security_id, day)
                    if quote is not None and float(quote["open"]) > 0 and float(quote["volume"]) > 0:
                        open_price, close = float(quote["open"]), float(quote["close"])
                        pnl += active.qty * 10000.0 * (open_price - active.prior_mark) / denominator
                        pnl += new_target_qty * 10000.0 * (close - open_price) / denominator
                        cost += abs(latest_fraction - active.fraction) * PUT_FULL_SIDE_COST
                        active.qty = new_target_qty
                        active.full_qty = max(1, int(round(float(row.settle) * 200.0 / (etf_open * 10000.0))))
                        active.fraction = latest_fraction
                        active.prior_mark = close
                        action = "open_resize"
                        pending = False
            elif selected is not None:
                quote_old = history_exact(history_lookup, active.security_id, day)
                if quote_old is not None and float(quote_old["open"]) > 0 and float(quote_old["volume"]) > 0:
                    master, quote_new = selected
                    pnl += active.qty * 10000.0 * (float(quote_old["open"]) - active.prior_mark) / denominator
                    pnl += new_target_qty * 10000.0 * (float(quote_new["close"]) - float(quote_new["open"])) / denominator
                    cost += (active.fraction + latest_fraction) * PUT_FULL_SIDE_COST
                    full_qty = max(1, int(round(float(row.settle) * 200.0 / (etf_open * 10000.0))))
                    active = RealPosition(
                        str(master["security_id"]), str(master["contract_id"]), pd.Timestamp(master["contract_month"]),
                        fourth_wednesday(pd.Timestamp(master["contract_month"]), trade_dates), float(master["strike"]),
                        new_target_qty, full_qty, latest_fraction, float(quote_new["close"]), float(master["strike"]) / etf_open,
                    )
                    action = "open_roll"
                    pending = False

        if not action and active is not None:
            mark, stale_days, carried = real_mark(history_groups, active, day, etf_close)
            pnl += active.qty * 10000.0 * (mark - active.prior_mark) / denominator
            active.prior_mark = mark

        if action:
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": pending_since,
                    "actual_execution_date": day,
                    "action": action,
                    "target_fraction": latest_fraction,
                    "old_contract": old.contract_id if old else "",
                    "new_contract": active.contract_id if active else "",
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": active.entry_moneyness if active else np.nan,
                    "desired_month": desired_month,
                    "delay_days": int((day - pending_since).days) if pending_since is not None else 0,
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
            pending = False

        mark_fraction = 0.0
        contract = ""
        qty = 0
        entry_moneyness = np.nan
        if active is not None:
            mark_fraction = active.qty * 10000.0 * active.prior_mark / (float(row.settle) * 200.0)
            contract = active.contract_id
            qty = active.qty
            entry_moneyness = active.entry_moneyness
        rows.append(
            {
                "date": day,
                "candidate": label,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": mark_fraction,
                "put_contract": contract,
                "put_qty": qty,
                "target_fraction": latest_fraction,
                "entry_moneyness_mark": entry_moneyness,
                "carried_mark": carried,
                "mark_stale_days": stale_days,
                "deferred_adjustment": pending,
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def option_price(position: ModelPosition, market_row: object, when: str) -> float:
    spot = float(getattr(market_row, f"spot_{when}"))
    rate = float(getattr(market_row, f"rate_{when}"))
    dividend = float(getattr(market_row, f"dividend_{when}"))
    sigma = float(getattr(market_row, f"sigma_{when}"))
    day = pd.Timestamp(market_row.date)
    years = max((position.expiry - day).days, 0) / 365.0
    return bs_put(spot, position.strike, rate, dividend, sigma, years)


def run_model_candidate(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    market: pd.DataFrame,
    frequency: str,
    tenor: str,
    tier: str,
    moneyness: float,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = ic[ic["date"] >= MODEL_START].copy().reset_index(drop=True)
    daily["prior_settle"] = daily["settle"].shift(1)
    daily.loc[0, "prior_settle"] = daily.loc[0, "settle"]
    merged = daily.merge(market.drop(columns=["settle"]), on="date", validate="one_to_one")
    events = schedule_events(schedule, "model", frequency)
    trade_dates = pd.DatetimeIndex(ic["date"])
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    active: ModelPosition | None = None
    latest_fraction = 0.0
    latest_eval: pd.Timestamp | None = None
    pending = False
    pending_since: pd.Timestamp | None = None
    maintenance = False

    for row in merged.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        if event is not None:
            latest_fraction = float(getattr(event, tier_column(tier)))
            latest_eval = pd.Timestamp(event.eval_date)
            pending = True
            pending_since = day
            maintenance = False
        if active is None and latest_fraction > 0 and not pending:
            pending = True
            pending_since = day
            maintenance = True

        denominator = float(row.prior_settle) * 200.0
        pnl = 0.0
        cost = 0.0
        action = ""
        old = active
        desired_month: pd.Timestamp | None = None

        if pending:
            if latest_fraction > 0:
                if latest_eval is None:
                    raise RuntimeError("Positive model target without valuation date")
                desired_month = select_model_month(
                    day,
                    target_date(tenor, latest_eval, day, maintenance),
                    trade_dates,
                )
            if active is None:
                if latest_fraction == 0:
                    pending = False
                else:
                    expiry = fourth_wednesday(desired_month, trade_dates)
                    strike = float(row.spot_open) * moneyness
                    units = float(row.settle) * 200.0 / float(row.spot_open) * latest_fraction
                    candidate = ModelPosition(desired_month, expiry, strike, units, latest_fraction, 0.0)
                    open_price = option_price(candidate, row, "open")
                    close_price = option_price(candidate, row, "close")
                    pnl += units * (close_price - open_price) / denominator
                    candidate.prior_mark = close_price
                    active = candidate
                    cost += latest_fraction * PUT_FULL_SIDE_COST
                    action = "open_buy"
                    pending = False
            elif latest_fraction == 0:
                open_price = option_price(active, row, "open")
                pnl += active.units * (open_price - active.prior_mark) / denominator
                cost += active.fraction * PUT_FULL_SIDE_COST
                active = None
                action = "open_exit"
                pending = False
            elif desired_month == active.contract_month:
                if math.isclose(latest_fraction, active.fraction, abs_tol=1e-12):
                    pending = False
                else:
                    open_price = option_price(active, row, "open")
                    pnl += active.units * (open_price - active.prior_mark) / denominator
                    new_units = float(row.settle) * 200.0 / float(row.spot_open) * latest_fraction
                    close_price = option_price(active, row, "close")
                    pnl += new_units * (close_price - open_price) / denominator
                    cost += abs(latest_fraction - active.fraction) * PUT_FULL_SIDE_COST
                    active.units = new_units
                    active.fraction = latest_fraction
                    active.prior_mark = close_price
                    action = "open_resize"
                    pending = False
            else:
                old_open = option_price(active, row, "open")
                pnl += active.units * (old_open - active.prior_mark) / denominator
                expiry = fourth_wednesday(desired_month, trade_dates)
                strike = float(row.spot_open) * moneyness
                units = float(row.settle) * 200.0 / float(row.spot_open) * latest_fraction
                candidate = ModelPosition(desired_month, expiry, strike, units, latest_fraction, 0.0)
                new_open = option_price(candidate, row, "open")
                new_close = option_price(candidate, row, "close")
                pnl += units * (new_close - new_open) / denominator
                candidate.prior_mark = new_close
                active = candidate
                cost += (old.fraction + latest_fraction) * PUT_FULL_SIDE_COST
                action = "open_roll"
                pending = False

        if not action and active is not None:
            close_price = option_price(active, row, "close")
            pnl += active.units * (close_price - active.prior_mark) / denominator
            active.prior_mark = close_price

        if action:
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": pending_since,
                    "actual_execution_date": day,
                    "action": action,
                    "target_fraction": latest_fraction,
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": moneyness if active else np.nan,
                    "delay_days": 0,
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
            pending = False

        mark_fraction = 0.0
        contract = ""
        units = 0.0
        if active is not None:
            mark_fraction = active.units * active.prior_mark / (float(row.settle) * 200.0)
            contract = f"MODEL_{active.contract_month.strftime('%y%m')}_{active.strike:.4f}"
            units = active.units
        rows.append(
            {
                "date": day,
                "candidate": label,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": mark_fraction,
                "put_contract": contract,
                "put_qty": units,
                "target_fraction": latest_fraction,
                "entry_moneyness_mark": moneyness if active is not None else np.nan,
                "carried_mark": False,
                "mark_stale_days": 0,
                "deferred_adjustment": False,
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def assemble_candidate(overlay: pd.DataFrame, ic: pd.DataFrame) -> pd.DataFrame:
    base = ic[["date", "ic_gross_ret", "cost_rate", "ic_net_ret"]].merge(overlay, on="date", validate="one_to_one")
    base["gross_ret"] = base["ic_gross_ret"] + base["put_pnl_ret"]
    base["ret"] = (1.0 + base["gross_ret"]) * (1.0 - base["cost_rate"]) * (1.0 - base["put_cost_rate"]) - 1.0
    base["cash_weight"] = (CASH_WEIGHT - base["put_mark_fraction"]).clip(lower=0)
    base["cash_ret"] = base["ret"] + base["cash_weight"] * CASH_DAILY
    return base


def no_put_rows(ic: pd.DataFrame, start: pd.Timestamp, label: str) -> pd.DataFrame:
    base = ic[ic["date"] >= start][["date", "ic_gross_ret", "cost_rate", "ic_net_ret"]].copy()
    base["candidate"] = label
    base["put_pnl_ret"] = 0.0
    base["put_cost_rate"] = 0.0
    base["put_mark_fraction"] = 0.0
    base["put_contract"] = ""
    base["put_qty"] = 0.0
    base["target_fraction"] = 0.0
    base["entry_moneyness_mark"] = np.nan
    base["carried_mark"] = False
    base["mark_stale_days"] = 0
    base["deferred_adjustment"] = False
    base["expired"] = False
    base["gross_ret"] = base["ic_gross_ret"]
    base["ret"] = base["ic_net_ret"]
    base["cash_weight"] = CASH_WEIGHT
    base["cash_ret"] = base["ret"] + CASH_WEIGHT * CASH_DAILY
    return base


def metrics(returns: pd.Series) -> dict[str, float]:
    values = returns.astype(float)
    nav = (1.0 + values).cumprod()
    years = len(values) / TRADING_DAYS
    total = float(nav.iloc[-1] - 1.0)
    ann = float(nav.iloc[-1] ** (1.0 / years) - 1.0)
    vol = float(values.std(ddof=1) * math.sqrt(TRADING_DAYS))
    sharpe = float(values.mean() / values.std(ddof=1) * math.sqrt(TRADING_DAYS)) if values.std(ddof=1) > 0 else np.nan
    dd = float((nav / nav.cummax() - 1.0).min())
    return {"total_return": total, "ann_return": ann, "ann_vol": vol, "sharpe_repo": sharpe, "max_dd": dd}


def parameter_parts(candidate: str) -> dict[str, object]:
    if candidate in {"real_no_put", "model_no_put"}:
        return {"layer": candidate.split("_")[0], "moneyness": np.nan, "frequency": "none", "tenor": "none", "tier": "none"}
    parts = candidate.split("_")
    if parts[0] == "real":
        return {"layer": "real", "moneyness": np.nan, "frequency": parts[1], "tenor": parts[2], "tier": "_".join(parts[3:])}
    return {
        "layer": "model",
        "moneyness": int(parts[1][1:]) / 100.0,
        "frequency": parts[2],
        "tenor": parts[3],
        "tier": "_".join(parts[4:]),
    }


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    formal_rows: list[dict[str, object]] = []
    scan_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        params = parameter_parts(candidate)
        wide: dict[str, object] = {"candidate": candidate, **params}
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else END - offset
            available = offset is None or group["date"].min() <= requested
            formal_subset = group if offset is None else group[group["date"] >= requested]
            formal = {"candidate": candidate, **params, "window": window, "available": available, "requested_start": requested, "actual_start": formal_subset["date"].min(), "end": formal_subset["date"].max(), "rows": len(formal_subset)}
            if available:
                formal.update(metrics(formal_subset["ret"]))
                formal.update({f"cash_{key}": value for key, value in metrics(formal_subset["cash_ret"]).items()})
            formal_rows.append(formal)

            clipped = formal_subset if len(formal_subset) else group
            values = metrics(clipped["ret"])
            scan_rows.append(
                {"candidate": candidate, **params, "segment": window, "start": clipped["date"].min(), "end": clipped["date"].max(), "rows": len(clipped), "window_available": available, **values}
            )
            wide[f"ann_return_{window}"] = values["ann_return"]
            wide[f"max_dd_{window}"] = values["max_dd"]
        wide_rows.append(wide)
    return pd.DataFrame(formal_rows), pd.DataFrame(scan_rows), pd.DataFrame(wide_rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (candidate, year), group in daily.groupby(["candidate", daily["date"].dt.year], sort=False):
        rows.append({"candidate": candidate, "year": year, **metrics(group.sort_values("date")["ret"])})
    return pd.DataFrame(rows)


def exposure_summary(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        subset = trades[trades["candidate"].eq(candidate)] if not trades.empty else pd.DataFrame()
        entries = subset[subset["new_entry_moneyness"].notna()] if not subset.empty and "new_entry_moneyness" in subset else pd.DataFrame()
        rows.append(
            {
                "candidate": candidate,
                **parameter_parts(candidate),
                "protected_day_ratio": float(group["target_fraction"].gt(0).mean()),
                "average_target_fraction": float(group["target_fraction"].mean()),
                "average_put_mark_fraction": float(group["put_mark_fraction"].mean()),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "trade_events": len(subset),
                "deferred_days": int(group["deferred_adjustment"].sum()),
                "carried_mark_days": int(group["carried_mark"].sum()),
                "max_mark_stale_days": int(group["mark_stale_days"].max()),
                "average_entry_moneyness": float(entries["new_entry_moneyness"].mean()) if not entries.empty else np.nan,
            }
        )
    return pd.DataFrame(rows)


def same_slice_real_model_validation(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[dict[str, object]] = []
    for real in REAL_CANDIDATES:
        suffix = real.removeprefix("real_")
        model = f"model_m85_{suffix}"
        real_group = daily[daily["candidate"].eq(real)].sort_values("date")
        model_group = daily[(daily["candidate"].eq(model)) & (daily["date"] >= REAL_START)].sort_values("date")
        if not real_group["date"].reset_index(drop=True).equals(model_group["date"].reset_index(drop=True)):
            raise RuntimeError(f"Real/model calendar mismatch: {real}")
        rm, mm = metrics(real_group["ret"]), metrics(model_group["ret"])
        rows.append(
            {
                "real_candidate": real,
                "model_candidate": model,
                "real_ann_return": rm["ann_return"],
                "real_max_dd": rm["max_dd"],
                "model_ann_return": mm["ann_return"],
                "model_max_dd": mm["max_dd"],
            }
        )
    table = pd.DataFrame(rows)
    spearman = float(table["real_ann_return"].rank().corr(table["model_ann_return"].rank()))
    directions: dict[str, object] = {}
    for tier in TIERS:
        real_weekly = table.loc[table["real_candidate"].eq(f"real_weekly_2m_{tier}"), "real_ann_return"].item()
        real_monthly = table.loc[table["real_candidate"].eq(f"real_monthly_3m_{tier}"), "real_ann_return"].item()
        model_weekly = table.loc[table["model_candidate"].eq(f"model_m85_weekly_2m_{tier}"), "model_ann_return"].item()
        model_monthly = table.loc[table["model_candidate"].eq(f"model_m85_monthly_3m_{tier}"), "model_ann_return"].item()
        directions[tier] = {
            "real_delta": real_weekly - real_monthly,
            "model_delta": model_weekly - model_monthly,
            "same_direction": bool((real_weekly - real_monthly) * (model_weekly - model_monthly) > 0),
        }
    passed = bool(spearman >= 0.50 and any(value["same_direction"] for value in directions.values()))
    return table, {"spearman_cagr": spearman, "directions": directions, "passed": passed}


def event_concentration(daily: pd.DataFrame) -> pd.DataFrame:
    pairs = [
        ("real_weekly_2m_binary", "real_monthly_3m_binary"),
        ("real_weekly_2m_three_tier", "real_monthly_3m_three_tier"),
        ("model_m85_weekly_2m_binary", "model_m85_monthly_3m_binary"),
        ("model_m85_weekly_2m_three_tier", "model_m85_monthly_3m_three_tier"),
    ]
    rows: list[dict[str, object]] = []
    for candidate, baseline in pairs:
        left = daily[daily["candidate"].eq(candidate)][["date", "ret"]].rename(columns={"ret": "candidate_ret"})
        right = daily[daily["candidate"].eq(baseline)][["date", "ret"]].rename(columns={"ret": "baseline_ret"})
        merged = left.merge(right, on="date", validate="one_to_one")
        merged["relative_log"] = np.log1p(merged["candidate_ret"]) - np.log1p(merged["baseline_ret"])
        total = float(merged["relative_log"].sum())
        positives = merged[merged["relative_log"] > 0].nlargest(5, "relative_log")
        for rank, row in enumerate(positives.itertuples(index=False), start=1):
            rows.append(
                {
                    "candidate": candidate,
                    "baseline": baseline,
                    "rank": rank,
                    "date": row.date,
                    "relative_log": row.relative_log,
                    "relative_simple": math.expm1(row.relative_log),
                    "net_log_advantage": total,
                    "share_of_net_log_advantage": row.relative_log / total if total != 0 else np.nan,
                }
            )
    return pd.DataFrame(rows)


def fmt(value: float) -> str:
    return f"{value * 100:.2f}%"


def build_record(
    formal: pd.DataFrame,
    qvix_stats: dict[str, object],
    cross_stats: dict[str, object],
    checks: dict[str, float],
) -> str:
    full = formal[(formal["window"] == "full") & formal["available"]].set_index("candidate")
    ten = formal[(formal["window"] == "last_10y") & formal["available"]].set_index("candidate")
    five = formal[(formal["window"] == "last_5y") & formal["available"]].set_index("candidate")
    lines = [
        "# IC + 510500 Put 双层验证 v1：结果记录",
        "",
        "运行日期：2026-08-16  ",
        "研究状态：真实短样本交叉验证 + 模型长样本压力测试；未获准实盘",
        "",
        "## 代理校验",
        "",
        f"- 波动率代理：相关系数{qvix_stats['pearson']:.3f}，中位绝对误差{qvix_stats['median_abs_error']*100:.2f}个波动率点，中位有符号误差{qvix_stats['median_signed_error']*100:.2f}点，{'PASS' if qvix_stats['passed'] else 'FAIL'}。",
        f"- 真实18组与模型85%同区间CAGR秩相关{cross_stats['spearman_cagr']:.3f}，结构校验{'PASS' if cross_stats['passed'] else 'FAIL'}。",
        f"- 新浪与官方中证500收盘最大相对差{checks['official_sina_close_max_relative_error']:.3e}。",
        "",
        "## 真实510500 Put层（2022-09-19起）",
        "",
        "| 候选 | 全样本 CAGR / MaxDD | 加70%现金 CAGR / MaxDD |",
        "|---|---:|---:|",
    ]
    real_show = ["real_no_put", "real_monthly_3m_binary", "real_weekly_2m_binary", "real_weekly_2m_three_tier", "real_daily_3m_binary"]
    for candidate in real_show:
        row = full.loc[candidate]
        lines.append(f"| `{candidate}` | {fmt(row.ann_return)} / {fmt(row.max_dd)} | {fmt(row.cash_ann_return)} / {fmt(row.cash_max_dd)} |")
    lines += [
        "",
        "真实层10年和5年因中证500 ETF期权于2022-09-19才上市而为N/A。",
        "",
        "## 模型85% Put长样本层（2015-04-16起）",
        "",
        "| 候选 | 全样本 CAGR / MaxDD | 10年 CAGR / MaxDD | 5年 CAGR / MaxDD |",
        "|---|---:|---:|---:|",
    ]
    model_show = ["model_no_put", "model_m85_monthly_3m_binary", "model_m85_weekly_2m_binary", "model_m85_weekly_2m_three_tier", "model_m85_daily_3m_binary"]
    for candidate in model_show:
        row, row10, row5 = full.loc[candidate], ten.loc[candidate], five.loc[candidate]
        lines.append(
            f"| `{candidate}` | {fmt(row.ann_return)} / {fmt(row.max_dd)} | {fmt(row10.ann_return)} / {fmt(row10.max_dd)} | {fmt(row5.ann_return)} / {fmt(row5.max_dd)} |"
        )
    weak = not (qvix_stats["passed"] and cross_stats["passed"])
    lines += [
        "",
        "## 判定与边界",
        "",
        f"- 模型长样本证据标签：`{'weak_proxy' if weak else 'validated_proxy_diagnostic'}`。",
        "- 真实层使用上交所标准合约主表与新浪逐合约开盘/收盘/成交量；缺少官方结算、持仓量和盘口，不能解释为可执行收益。",
        "- 模型层使用Black-Scholes与50ETF QVIX×60日实现波动率比，不是历史510500报价；80%/90%结果用于敏感性，85%为预注册主口径。",
        "- 1倍IC名义，Put满额每边1bp；70%现金年化3%并扣Put权利金；不使用3.33倍方向杠杆。",
        "- 本研究不是交易建议，所有候选均未获准实盘。",
        "",
        "## 复现",
        "",
        f"- 规格SHA-256：`{SPEC_HASH}`。",
        f"- 脚本SHA-256：`{sha256(Path(__file__))}`。",
        "- 命令：`python.exe ic_510500_put_proxy_validation_v1.py`。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    manifest = verify()
    frames = load_inputs()
    daily_valuation, valuation_diffs = build_daily_valuation()
    schedule, signals, analogues = build_schedules(frames["ic"], daily_valuation, frames["states"], frames["tri"])
    market, market_checks = prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = qvix_validation(market, frames["q500"])

    daily_parts: list[pd.DataFrame] = [
        no_put_rows(frames["ic"], MODEL_START, "model_no_put"),
        no_put_rows(frames["ic"], REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    for moneyness in MONEYNESS:
        for frequency in FREQUENCIES:
            for tenor in TENORS:
                for tier in TIERS:
                    label = f"model_m{int(moneyness*100)}_{frequency}_{tenor}_{tier}"
                    overlay, trades = run_model_candidate(
                        frames["ic"], schedule, market, frequency, tenor, tier, moneyness, label
                    )
                    daily_parts.append(assemble_candidate(overlay, frames["ic"]))
                    trade_parts.append(trades)
    for frequency in FREQUENCIES:
        for tenor in TENORS:
            for tier in TIERS:
                label = f"real_{frequency}_{tenor}_{tier}"
                overlay, trades = run_real_candidate(
                    frames["ic"], schedule, frames["snapshots"], frames["histories"], frames["etf500"],
                    frequency, tenor, tier, label,
                )
                daily_parts.append(assemble_candidate(overlay, frames["ic"]))
                trade_parts.append(trades)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"]).reset_index(drop=True)
    trades = pd.concat([part for part in trade_parts if not part.empty], ignore_index=True, sort=False)
    formal, scan_summary, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_summary(daily, trades)
    cross_table, cross_stats = same_slice_real_model_validation(daily)
    concentration = event_concentration(daily)

    expected = set(["model_no_put", "real_no_put", *MODEL_CANDIDATES, *REAL_CANDIDATES])
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("Candidate set mismatch")
    parity: dict[str, float] = {}
    for label, start in [("model_no_put", MODEL_START), ("real_no_put", REAL_START)]:
        observed = daily[daily["candidate"].eq(label)][["date", "ret"]]
        frozen = frames["ic"][frames["ic"]["date"] >= start][["date", "ic_net_ret"]]
        joined = observed.merge(frozen, on="date", validate="one_to_one")
        parity[label] = float((joined["ret"] - joined["ic_net_ret"]).abs().max())
    if max(parity.values()) > 1e-14:
        raise RuntimeError(f"IC baseline parity failed: {parity}")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid daily candidate returns")
    if (schedule.loc[~schedule["initial_exception"], "execution_date"] <= schedule.loc[~schedule["initial_exception"], "eval_date"]).any():
        raise RuntimeError("Signal execution leakage")
    model_validation_pass = bool(qvix_stats["passed"] and cross_stats["passed"])

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv", index=False)
    signals.to_csv(OUTPUT / "valuation_signals.csv", index=False)
    analogues.to_csv(OUTPUT / "signal_analogues.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    record = build_record(formal, qvix_stats, cross_stats, market_checks)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    output_manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "spec_sha256": SPEC_HASH,
        "script_sha256": sha256(Path(__file__)),
        "collector_manifest": manifest,
        "candidate_count": len(expected),
        "sample": {"model": [str(MODEL_START.date()), str(END.date())], "real": [str(REAL_START.date()), str(END.date())]},
        "valuation_month_end_max_abs": valuation_diffs,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "model_validation_pass": model_validation_pass,
        "baseline_parity": parity,
        "source_hashes": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in [IC_DAILY, MONTHLY_STATES, VALUATION, PRICE, TRI, GOV10Y]
        },
        "warnings": [
            "Real 510500 prices are third-party daily opens/closes, not official settlement or executable bid/ask.",
            "Model puts are theoretical and cannot be interpreted as historical tradable prices.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text("python.exe ic_510500_put_proxy_validation_v1.py\n", encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    scan_meta_path = SCAN / "scan_meta.json"
    meta = json.loads(scan_meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"model": "model_no_put", "real": "real_no_put", "official_source": str(IC_DAILY)},
            "candidate_grid": sorted(expected),
            "data_snapshot": output_manifest["sample"],
            "cost_model": {"ic": "frozen v1 1bp per side", "put_full_side": PUT_FULL_SIDE_COST, "cash_weight": CASH_WEIGHT, "cash_annual": 0.03},
            "parity_check": {"baseline": parity, "qvix": qvix_stats, "cross": cross_stats},
            "source_hashes": output_manifest["source_hashes"],
            "warnings": output_manifest["warnings"],
        }
    )
    scan_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (SCAN / "record.md").write_text(
        "# Quant Parameter Scan Record\n\n"
        "## Run Metadata\n\n"
        "- Run id: `20260816_ic_510500_put_proxy_validation_v1`\n"
        "- Scan type: `candidate_bundle`\n"
        "- Source-change rule: `research_only_no_source_change`\n"
        "- Decision: pending post-run audit\n"
        "- Stability Classification: pending post-run audit\n\n"
        "## Research Question\n\n"
        "Compare real 510500 Put overlays and 2015+ model-Put diagnostics across valuation frequency, tenor, tier, and preregistered moneyness.\n\n"
        "## Implementation Anchor\n\n"
        "- Entrypoint: `ic_510500_put_proxy_validation_v1.py`\n"
        "- Baselines: `model_no_put` and `real_no_put`, exact frozen IC v1 parity required.\n\n"
        "## Data Snapshot\n\n"
        "Real CFFEX IC, SSE 510500 contract master, Sina option/ETF/index prices, QVIX, and frozen valuation inputs through 2026-08-14.\n\n"
        "## Cost and Execution Assumptions\n\n"
        "T close to next open; full Put side 1bp; 70% cash at 3%; no directional leverage amplification.\n\n"
        "## Runtime Override Plan\n\nResearch-only new harness; no production or frozen upstream change.\n\n"
        "## Commands\n\n`python.exe ic_510500_put_proxy_validation_v1.py`\n\n"
        "## Output Files\n\n- `scan_summary.csv`\n- `window_metrics.csv`\n- `scan_meta.json`\n- `command_log.txt`\n\n"
        "## Full-Sample Results\n\nSee CSV files and formal record below.\n\n"
        "## Window Results\n\nSee CSV files.\n\n"
        "## Stability Classification\n\nPending post-run audit.\n\n"
        "## Decision\n\nPending post-run audit.\n\n"
        "## User-Facing Summary\n\n"
        + record,
        encoding="utf-8",
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython.exe ic_510500_put_proxy_validation_v1.py\n")
    print(
        json.dumps(
            {
                "model_validation_pass": model_validation_pass,
                "qvix": qvix_stats,
                "cross": cross_stats,
                "baseline_parity": parity,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
