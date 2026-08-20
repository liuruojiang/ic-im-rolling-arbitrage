from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_csi1000_put_protection_battery_v6 as v6


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_overwrite_delta_tenor_v19"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "1ecab3fd67b4d8f25ca8e0d0b97b3cab888afc6544f63bc1a7eab86e1d551d9f"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_mo_call_overwrite_delta_tenor_v19"

V14_OUTPUT = ROOT / "outputs" / "im_mo_reconstructed_floor_selection_v14"
V14_DAILY = V14_OUTPUT / "daily_candidates.csv.gz"
V14_MANIFEST = V14_OUTPUT / "data_manifest.json"
IM_UPSTREAM = ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1" / "daily_nav.csv"
CALL_DATA = ROOT / "data" / "im_mo_call_data_build_v1" / "cffex_mo_calls.csv"
CALL_MANIFEST = ROOT / "data" / "im_mo_call_data_build_v1" / "data_manifest.json"
CALL_BUILD_SCRIPT = ROOT / "im_mo_call_data_build_v1.py"
CALL_BUILD_SPEC = ROOT / "docs" / "im_mo_call_data_build_v1_spec.md"

MODEL_START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-08-14")
BASELINE_SOURCE = "reconstructed_valmom_floor3"
BASELINE = "base_core_put"
TENORS = ("front", "2m")
DELTAS = (0.10, 0.20)
IM_MULTIPLIER = 200.0
MO_MULTIPLIER = 100.0
MO_QTY = 2
CALL_BASKET_SIDE_COST = 0.0001
CASH_BASE = 0.70
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}

FROZEN_HASHES = {
    V14_DAILY: "c013e2ffdbe5435ae87601af319a3e263850e7d55f31e25fa3eee8a7ebb56614",
    V14_MANIFEST: "d6caa2000d4706a3da1b3ad0c6f6207b56df428c0808b051012fb7d36c1c9212",
    IM_UPSTREAM: "0a3719ade254a32eaf1886dc7d00e9d84aa93498e9a2fecf2868cbefefb60b99",
    CALL_DATA: "3c5bd3f5b4ca057a87fa8e0c0d1600980d773125b207b7d2c858500d2927f4c0",
    CALL_MANIFEST: "3d7bbae7e65f4f76f758e845886c562aef87f112c6fd41543d0970697e5c5ad3",
    CALL_BUILD_SCRIPT: "c7c1689ddabf0a34195e04a10b85c05647af74d0cd2facf9285ce8ce587cff81",
    CALL_BUILD_SPEC: "e8c6edd767802682bc7abcb76f7e5e81728c283f4c5fa442305b305cd49d5cd3",
    ROOT / "im_mo_csi1000_put_protection_battery_v6.py": "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d",
}


def candidate_name(tenor: str, delta: float) -> str:
    return f"{tenor}_d{int(round(delta * 100)):02d}"


CANDIDATES = tuple(candidate_name(tenor, delta) for tenor in TENORS for delta in DELTAS)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, text=True, capture_output=True
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v19 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v19 specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging v19 output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered parameter-scan folder is missing")
    for path, expected in FROZEN_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v19 input changed: {path}")
    call_manifest = json.loads(CALL_MANIFEST.read_text(encoding="utf-8"))
    listed = call_manifest.get("files", {})
    for filename, item in listed.items():
        path = CALL_MANIFEST.parent / filename
        if not path.exists() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Call data manifest mismatch: {filename}")
    return call_manifest


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def bs_call(
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    sigma: float,
    years: float,
) -> float:
    if years <= 0:
        return max(spot - strike, 0.0)
    if min(spot, strike, sigma) <= 0:
        raise RuntimeError("Invalid Black-Scholes Call input")
    root = math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend + 0.5 * sigma * sigma) * years
    ) / (sigma * root)
    d2 = d1 - sigma * root
    return spot * math.exp(-dividend * years) * norm_cdf(d1) - strike * math.exp(
        -rate * years
    ) * norm_cdf(d2)


def bs_call_delta(
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    sigma: float,
    years: float,
) -> float:
    if years <= 0:
        return 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
    root = math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend + 0.5 * sigma * sigma) * years
    ) / (sigma * root)
    return math.exp(-dividend * years) * norm_cdf(d1)


def strike_for_delta(
    spot: float,
    rate: float,
    dividend: float,
    sigma: float,
    years: float,
    target_delta: float,
) -> float:
    probability = target_delta * math.exp(dividend * years)
    probability = min(max(probability, 1e-8), 1.0 - 1e-8)
    d1 = statistics.NormalDist().inv_cdf(probability)
    return spot * math.exp(
        (rate - dividend + 0.5 * sigma * sigma) * years
        - d1 * sigma * math.sqrt(years)
    )


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    years: float,
) -> float | None:
    if years <= 0 or price <= 0:
        return None
    low, high = 0.01, 5.0
    low_price = bs_call(spot, strike, rate, dividend, low, years)
    high_price = bs_call(spot, strike, rate, dividend, high, years)
    if price < low_price - 1e-8 or price > high_price + 1e-8:
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        value = bs_call(spot, strike, rate, dividend, mid, years)
        if value < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def call_margin_fraction(
    mark: float,
    spot: float,
    strike: float,
    equivalent_units: float,
    denominator: float,
) -> float:
    out_of_money = max(strike - spot, 0.0)
    per_unit = mark + max(0.12 * spot - out_of_money, 0.07 * spot)
    return equivalent_units * per_unit / denominator


def rule_expiry(month: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
    return v6.third_friday(pd.Timestamp(month.year, month.month, 1), trade_dates)


def load_baseline() -> pd.DataFrame:
    daily = pd.read_csv(V14_DAILY, parse_dates=["date"])
    daily = daily[daily["candidate"].eq(BASELINE_SOURCE)].copy()
    daily["candidate"] = BASELINE
    expected = {"model": (MODEL_START, END), "real": (REAL_START, END)}
    for layer, (start, end) in expected.items():
        part = daily[daily["layer"].eq(layer)]
        if len(part) == 0 or part["date"].min() != start or part["date"].max() != end:
            raise RuntimeError(f"Frozen baseline date mismatch: {layer}")
    return daily.sort_values(["layer", "date"]).reset_index(drop=True)


def load_upstream() -> pd.DataFrame:
    frame = pd.read_csv(IM_UPSTREAM, parse_dates=["date"])
    frame = frame[(frame["date"] >= REAL_START) & (frame["date"] <= END)].copy()
    if frame["date"].duplicated().any() or len(frame) != 986:
        raise RuntimeError("Unexpected real IM upstream")
    return frame.reset_index(drop=True)


def prepare_calls(trade_dates: pd.DatetimeIndex) -> pd.DataFrame:
    calls = pd.read_csv(CALL_DATA, parse_dates=["date"])
    parsed = calls["contract"].str.extract(r"^MO(?P<yymm>\d{4})-C-(?P<strike>\d+)$")
    if parsed.isna().any().any():
        raise RuntimeError("Invalid MO Call contract identifier")
    calls["contract_month"] = pd.to_datetime("20" + parsed["yymm"] + "01", format="%Y%m%d")
    if not np.allclose(calls["strike"].astype(float), parsed["strike"].astype(float)):
        raise RuntimeError("Call strike/identifier mismatch")
    expiry_rows: list[dict[str, Any]] = []
    for month, group in calls.groupby("contract_month"):
        last = pd.Timestamp(group["date"].max())
        expiry = last if last < END else rule_expiry(pd.Timestamp(month), trade_dates)
        expiry_rows.append({"contract_month": month, "actual_expiry": expiry})
    calls = calls.merge(pd.DataFrame(expiry_rows), on="contract_month", validate="many_to_one")
    if calls.duplicated(["date", "contract"]).any():
        raise RuntimeError("Duplicate MO Call quote")
    calls = calls.sort_values(["date", "actual_expiry", "strike", "contract"])
    return calls.reset_index(drop=True)


def monthly_events(
    start: pd.Timestamp,
    dates: pd.DatetimeIndex,
    roll_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    if start not in dates:
        raise RuntimeError("Event start is not a trade date")
    start_idx = int(dates.get_loc(start))
    if start_idx + 1 >= len(dates):
        raise RuntimeError("No initial T+1 execution date")
    rows: list[dict[str, Any]] = [
        {
            "reason": "initial",
            "eval_date": start,
            "execution_date": pd.Timestamp(dates[start_idx + 1]),
            "current_expiry": start,
        }
    ]
    for roll in sorted(set(pd.to_datetime(roll_dates))):
        roll = pd.Timestamp(roll)
        location = int(dates.get_indexer([roll])[0])
        if location < 2:
            continue
        eval_date = pd.Timestamp(dates[location - 2])
        execution_date = pd.Timestamp(dates[location - 1])
        if eval_date >= start and execution_date <= END:
            rows.append(
                {
                    "reason": "monthly",
                    "eval_date": eval_date,
                    "execution_date": execution_date,
                    "current_expiry": roll,
                }
            )
    result = pd.DataFrame(rows).sort_values(["eval_date", "execution_date"])
    if result.duplicated("execution_date").any():
        raise RuntimeError("Duplicate Call execution event")
    return result.reset_index(drop=True)


def model_roll_dates(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    rows = []
    for year in range(MODEL_START.year, END.year + 1):
        for month in range(1, 13):
            day = rule_expiry(pd.Timestamp(year, month, 1), dates)
            if day in dates and MODEL_START <= day <= END:
                rows.append(day)
    return pd.DatetimeIndex(sorted(set(rows)))


def target_month(
    available: pd.DataFrame,
    event: Any,
    tenor: str,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    threshold = max(pd.Timestamp(event.current_expiry), pd.Timestamp(event.execution_date))
    eligible = available[available["actual_expiry"].gt(threshold)][
        ["contract_month", "actual_expiry"]
    ].drop_duplicates()
    if eligible.empty:
        raise RuntimeError(f"No eligible {tenor} Call expiry on {event.eval_date}")
    if tenor == "front":
        selected = eligible.sort_values(["actual_expiry", "contract_month"]).iloc[0]
    else:
        target = pd.Timestamp(event.eval_date) + pd.DateOffset(months=2)
        eligible = eligible.copy()
        eligible["distance"] = (eligible["actual_expiry"] - target).abs().dt.days
        selected = eligible.sort_values(
            ["distance", "actual_expiry", "contract_month"]
        ).iloc[0]
    return pd.Timestamp(selected["contract_month"]), pd.Timestamp(selected["actual_expiry"])


@dataclass(frozen=True)
class Selection:
    layer: str
    candidate: str
    tenor: str
    target_delta: float
    reason: str
    eval_date: pd.Timestamp
    scheduled_execution_date: pd.Timestamp
    contract: str
    month: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    selected_delta: float
    implied_vol: float
    eval_close: float
    eval_volume: float
    eval_open_interest: float


@dataclass
class ModelPosition:
    selection: Selection
    units: float
    prior_mark: float
    cycle_id: int


@dataclass
class RealPosition:
    selection: Selection
    qty: int
    prior_settle: float
    cycle_id: int


def build_model_selections(
    market: pd.DataFrame,
    events: pd.DataFrame,
    tenor: str,
    target_delta: float,
    label: str,
) -> list[Selection]:
    dates = pd.DatetimeIndex(market["date"])
    lookup = market.set_index("date")
    rows: list[Selection] = []
    for event in events.itertuples(index=False):
        day = pd.Timestamp(event.eval_date)
        listed = v6.model_listed_months(day, dates)
        available = pd.DataFrame(
            {
                "contract_month": listed,
                "actual_expiry": [rule_expiry(month, dates) for month in listed],
            }
        )
        month, expiry = target_month(available, event, tenor)
        row = lookup.loc[day]
        years = (expiry - day).days / 365.0
        strike = strike_for_delta(
            float(row["spot_close"]),
            float(row["rate_close"]),
            float(row["dividend_close"]),
            float(row["sigma_close"]),
            years,
            target_delta,
        )
        rows.append(
            Selection(
                "model",
                label,
                tenor,
                target_delta,
                str(event.reason),
                day,
                pd.Timestamp(event.execution_date),
                f"MODEL_{month:%y%m}_{strike:.6f}",
                month,
                expiry,
                strike,
                target_delta,
                float(row["sigma_close"]),
                bs_call(
                    float(row["spot_close"]),
                    strike,
                    float(row["rate_close"]),
                    float(row["dividend_close"]),
                    float(row["sigma_close"]),
                    years,
                ),
                np.nan,
                np.nan,
            )
        )
    return rows


def build_real_selections(
    calls: pd.DataFrame,
    market: pd.DataFrame,
    events: pd.DataFrame,
    tenor: str,
    target_delta: float,
    label: str,
) -> list[Selection]:
    market_lookup = market.set_index("date")
    rows: list[Selection] = []
    for event in events.itertuples(index=False):
        day = pd.Timestamp(event.eval_date)
        chain = calls[calls["date"].eq(day)].copy()
        if chain.empty:
            raise RuntimeError(f"No real MO Call chain on {day.date()}")
        month, expiry = target_month(chain, event, tenor)
        market_row = market_lookup.loc[day]
        spot = float(market_row["spot_close"])
        eligible = chain[
            chain["contract_month"].eq(month)
            & chain["strike"].gt(spot)
            & chain["close"].gt(0)
            & chain["volume"].gt(0)
            & chain["open_interest"].gt(0)
        ].copy()
        choices: list[dict[str, Any]] = []
        for quote in eligible.itertuples(index=False):
            years = (pd.Timestamp(quote.actual_expiry) - day).days / 365.0
            iv = implied_volatility(
                float(quote.close),
                spot,
                float(quote.strike),
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                years,
            )
            if iv is None:
                continue
            delta = bs_call_delta(
                spot,
                float(quote.strike),
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                iv,
                years,
            )
            choices.append(
                {
                    "quote": quote,
                    "iv": iv,
                    "delta": delta,
                    "delta_error": abs(delta - target_delta),
                }
            )
        if not choices:
            raise RuntimeError(f"No IV-valid real MO Call on {day.date()} for {label}")
        chosen = sorted(
            choices,
            key=lambda item: (
                item["delta_error"],
                item["delta"],
                item["quote"].strike,
                item["quote"].contract,
            ),
        )[0]
        quote = chosen["quote"]
        rows.append(
            Selection(
                "real",
                label,
                tenor,
                target_delta,
                str(event.reason),
                day,
                pd.Timestamp(event.execution_date),
                str(quote.contract),
                month,
                expiry,
                float(quote.strike),
                float(chosen["delta"]),
                float(chosen["iv"]),
                float(quote.close),
                float(quote.volume),
                float(quote.open_interest),
            )
        )
    return rows


def model_mark(position: ModelPosition, row: Any) -> tuple[float, float]:
    years = max((position.selection.expiry - pd.Timestamp(row.date)).days, 0) / 365.0
    mark = bs_call(
        float(row.spot_close),
        position.selection.strike,
        float(row.rate_close),
        float(row.dividend_close),
        float(row.sigma_close),
        years,
    )
    delta = bs_call_delta(
        float(row.spot_close),
        position.selection.strike,
        float(row.rate_close),
        float(row.dividend_close),
        float(row.sigma_close),
        years,
    )
    return mark, delta


def run_model_overlay(
    market: pd.DataFrame,
    selections: list[Selection],
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pending = {item.scheduled_execution_date: item for item in selections}
    active: ModelPosition | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    cycle_id = 0
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        denominator = float(row.base_prior_close)
        pnl = 0.0
        cost = 0.0
        old_mark = np.nan
        old_delta = np.nan
        if active is not None:
            old_mark, old_delta = model_mark(active, row)
            pnl = -active.units * (old_mark - active.prior_mark) / denominator
            active.prior_mark = old_mark
        selection = pending.get(day)
        if selection is not None:
            old = active
            if old is not None:
                cost += CALL_BASKET_SIDE_COST
            units = denominator / float(row.spot_close)
            shell = ModelPosition(selection, units, 0.0, cycle_id + 1)
            entry_mark, entry_delta = model_mark(shell, row)
            cycle_id += 1
            active = ModelPosition(selection, units, entry_mark, cycle_id)
            cost += CALL_BASKET_SIDE_COST
            trades.append(
                {
                    "layer": "model",
                    "candidate": label,
                    "eval_date": selection.eval_date,
                    "scheduled_execution_date": selection.scheduled_execution_date,
                    "actual_execution_date": day,
                    "reason": selection.reason,
                    "action": "roll" if old is not None else "open",
                    "old_contract": old.selection.contract if old is not None else "",
                    "new_contract": selection.contract,
                    "target_tenor": selection.tenor,
                    "target_delta": selection.target_delta,
                    "selection_delta": selection.selected_delta,
                    "entry_delta": entry_delta,
                    "delta_error": abs(selection.selected_delta - selection.target_delta),
                    "selection_iv": selection.implied_vol,
                    "new_expiry": selection.expiry,
                    "new_strike": selection.strike,
                    "new_qty": 1.0,
                    "old_close": old_mark,
                    "new_close": entry_mark,
                    "new_settle": entry_mark,
                    "new_volume": np.nan,
                    "new_open_interest": np.nan,
                    "delay_trading_days": 0,
                    "cycle_id": cycle_id,
                }
            )
        mark_fraction = margin_fraction = coverage = 0.0
        call_delta = np.nan
        contract = ""
        strike = np.nan
        expiry = pd.NaT
        itm = False
        active_cycle = 0
        if active is not None:
            mark, call_delta = model_mark(active, row)
            active.prior_mark = mark
            coverage = active.units * float(row.spot_close) / float(row.tri_close)
            mark_fraction = active.units * mark / float(row.tri_close)
            margin_fraction = call_margin_fraction(
                mark,
                float(row.spot_close),
                active.selection.strike,
                active.units,
                float(row.tri_close),
            )
            contract = active.selection.contract
            strike = active.selection.strike
            expiry = active.selection.expiry
            itm = float(row.spot_close) > strike
            active_cycle = active.cycle_id
            if expiry <= day:
                raise RuntimeError(f"Model Call reached expiry: {label} {day.date()}")
        rows.append(
            {
                "date": day,
                "candidate": label,
                "call_pnl_ret": pnl,
                "call_cost_rate": cost,
                "call_mark_fraction": mark_fraction,
                "call_margin_fraction": margin_fraction,
                "call_coverage": coverage,
                "call_delta": call_delta,
                "call_contract": contract,
                "call_strike": strike,
                "call_expiry": expiry,
                "call_itm": itm,
                "cycle_id": active_cycle,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def quote_row(lookup: pd.DataFrame, contract: str, day: pd.Timestamp) -> pd.Series | None:
    key = (contract, day)
    if key not in lookup.index:
        return None
    row = lookup.loc[key]
    return row.iloc[-1] if isinstance(row, pd.DataFrame) else row


def real_daily_delta(
    quote: pd.Series,
    selection: Selection,
    market_row: pd.Series,
    day: pd.Timestamp,
) -> float:
    years = (selection.expiry - day).days / 365.0
    iv = implied_volatility(
        float(quote["settle"]),
        float(market_row["spot_close"]),
        selection.strike,
        float(market_row["rate_close"]),
        float(market_row["dividend_close"]),
        years,
    )
    if iv is None:
        return np.nan
    return bs_call_delta(
        float(market_row["spot_close"]),
        selection.strike,
        float(market_row["rate_close"]),
        float(market_row["dividend_close"]),
        iv,
        years,
    )


def run_real_overlay(
    upstream: pd.DataFrame,
    calls: pd.DataFrame,
    market: pd.DataFrame,
    selections: list[Selection],
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    call_lookup = calls.set_index(["contract", "date"])
    market_lookup = market.set_index("date")
    selection_map = {item.scheduled_execution_date: item for item in selections}
    dates = pd.DatetimeIndex(upstream["date"])
    active: RealPosition | None = None
    pending: Selection | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    cycle_id = 0
    scheduled_failures = 0
    delayed_days = 0
    prior_im = upstream["settle"].shift(1)
    prior_im.iloc[0] = upstream.iloc[0]["settle"]
    for index, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(prior_im.iloc[index])
        if day in selection_map:
            if pending is not None:
                raise RuntimeError(f"Unresolved Call roll before next event: {label}")
            pending = selection_map[day]
        market_row = market_lookup.loc[day]
        pnl = 0.0
        cost = 0.0
        traded = False
        old_quote = quote_row(call_lookup, active.selection.contract, day) if active else None
        new_quote = quote_row(call_lookup, pending.contract, day) if pending else None
        old_tradable = active is None or (
            old_quote is not None
            and float(old_quote["close"]) > 0
            and float(old_quote["volume"]) > 0
            and float(old_quote["open_interest"]) > 0
        )
        new_tradable = pending is not None and (
            new_quote is not None
            and float(new_quote["close"]) > 0
            and float(new_quote["volume"]) > 0
            and float(new_quote["open_interest"]) > 0
        )
        if pending is not None and old_tradable and new_tradable:
            old = active
            if old is not None:
                pnl += (
                    old.qty
                    * MO_MULTIPLIER
                    / IM_MULTIPLIER
                    * (old.prior_settle - float(old_quote["close"]))
                    / denominator
                )
                cost += CALL_BASKET_SIDE_COST
            pnl += (
                MO_QTY
                * MO_MULTIPLIER
                / IM_MULTIPLIER
                * (float(new_quote["close"]) - float(new_quote["settle"]))
                / denominator
            )
            cost += CALL_BASKET_SIDE_COST
            cycle_id += 1
            delay = int(
                (
                    (dates > pending.scheduled_execution_date)
                    & (dates <= day)
                ).sum()
            )
            delayed_days += delay
            active = RealPosition(pending, MO_QTY, float(new_quote["settle"]), cycle_id)
            trades.append(
                {
                    "layer": "real",
                    "candidate": label,
                    "eval_date": pending.eval_date,
                    "scheduled_execution_date": pending.scheduled_execution_date,
                    "actual_execution_date": day,
                    "reason": pending.reason,
                    "action": "roll" if old is not None else "open",
                    "old_contract": old.selection.contract if old is not None else "",
                    "new_contract": pending.contract,
                    "target_tenor": pending.tenor,
                    "target_delta": pending.target_delta,
                    "selection_delta": pending.selected_delta,
                    "entry_delta": np.nan,
                    "delta_error": abs(pending.selected_delta - pending.target_delta),
                    "selection_iv": pending.implied_vol,
                    "new_expiry": pending.expiry,
                    "new_strike": pending.strike,
                    "new_qty": MO_QTY,
                    "old_close": float(old_quote["close"]) if old_quote is not None else np.nan,
                    "new_close": float(new_quote["close"]),
                    "new_settle": float(new_quote["settle"]),
                    "new_volume": float(new_quote["volume"]),
                    "new_open_interest": float(new_quote["open_interest"]),
                    "delay_trading_days": delay,
                    "cycle_id": cycle_id,
                }
            )
            pending = None
            traded = True
        elif pending is not None and day == pending.scheduled_execution_date:
            scheduled_failures += 1
        if not traded and active is not None:
            if old_quote is None or float(old_quote["settle"]) <= 0:
                raise RuntimeError(f"Missing active Call settlement: {label} {day.date()}")
            pnl += (
                active.qty
                * MO_MULTIPLIER
                / IM_MULTIPLIER
                * (active.prior_settle - float(old_quote["settle"]))
                / denominator
            )
            active.prior_settle = float(old_quote["settle"])
        mark_fraction = margin_fraction = coverage = 0.0
        call_delta = np.nan
        contract = ""
        strike = np.nan
        expiry = pd.NaT
        itm = False
        active_cycle = 0
        if active is not None:
            quote = quote_row(call_lookup, active.selection.contract, day)
            if quote is None or float(quote["settle"]) <= 0:
                raise RuntimeError(f"No EOD Call quote: {label} {day.date()}")
            settle = float(quote["settle"])
            active.prior_settle = settle
            equivalent_units = active.qty * MO_MULTIPLIER / IM_MULTIPLIER
            coverage = equivalent_units * float(market_row["spot_close"]) / float(base["settle"])
            mark_fraction = equivalent_units * settle / float(base["settle"])
            margin_fraction = call_margin_fraction(
                settle,
                float(market_row["spot_close"]),
                active.selection.strike,
                equivalent_units,
                float(base["settle"]),
            )
            call_delta = real_daily_delta(quote, active.selection, market_row, day)
            contract = active.selection.contract
            strike = active.selection.strike
            expiry = active.selection.expiry
            itm = float(market_row["spot_close"]) > strike
            active_cycle = active.cycle_id
            if expiry <= day:
                raise RuntimeError(f"Real Call reached expiry: {label} {day.date()}")
        rows.append(
            {
                "date": day,
                "candidate": label,
                "call_pnl_ret": pnl,
                "call_cost_rate": cost,
                "call_mark_fraction": mark_fraction,
                "call_margin_fraction": margin_fraction,
                "call_coverage": coverage,
                "call_delta": call_delta,
                "call_contract": contract,
                "call_strike": strike,
                "call_expiry": expiry,
                "call_itm": itm,
                "cycle_id": active_cycle,
            }
        )
    if pending is not None:
        raise RuntimeError(f"Unexecuted final Call selection: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), {
        "scheduled_execution_failures": scheduled_failures,
        "delayed_trading_days": delayed_days,
    }


def assemble_candidate(
    base: pd.DataFrame,
    overlay: pd.DataFrame,
    label: str,
) -> pd.DataFrame:
    frame = base.merge(overlay, on="date", validate="one_to_one")
    frame["candidate"] = label
    combined = frame["gross_ret"] + frame["put_pnl_ret"] + frame["call_pnl_ret"]
    frame["ret"] = (
        (1.0 + combined)
        * (1.0 - frame["cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
    )
    frame["cash_weight"] = (
        CASH_BASE - frame["put_mark_fraction"] - frame["call_margin_fraction"]
    ).clip(lower=0.0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    frame["nav"] = (1.0 + frame["ret"]).cumprod()
    frame["cash_nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["cash_drawdown"] = frame["cash_nav"] / frame["cash_nav"].cummax() - 1.0
    if frame[["ret", "cash_ret"]].isna().any().any() or (
        frame[["ret", "cash_ret"]] <= -1
    ).any().any():
        raise RuntimeError(f"Invalid candidate return: {label}")
    return frame


def baseline_frame(base: pd.DataFrame) -> pd.DataFrame:
    frame = base.copy()
    frame["call_pnl_ret"] = 0.0
    frame["call_cost_rate"] = 0.0
    frame["call_mark_fraction"] = 0.0
    frame["call_margin_fraction"] = 0.0
    frame["call_coverage"] = 0.0
    frame["call_delta"] = np.nan
    frame["call_contract"] = ""
    frame["call_strike"] = np.nan
    frame["call_expiry"] = pd.NaT
    frame["call_itm"] = False
    frame["cycle_id"] = 0
    frame["cash_weight"] = (CASH_BASE - frame["put_mark_fraction"]).clip(lower=0.0)
    return frame


def metrics(returns: pd.Series) -> dict[str, float]:
    values = returns.astype(float)
    wealth = (1.0 + values).cumprod()
    years = len(values) / 252.0
    total = float(wealth.iloc[-1] - 1.0)
    ann = float(wealth.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else np.nan
    vol = float(values.std(ddof=1) * math.sqrt(252.0)) if len(values) > 1 else 0.0
    sharpe = float(values.mean() / values.std(ddof=1) * math.sqrt(252.0)) if len(values) > 1 and values.std(ddof=1) > 0 else np.nan
    drawdown = wealth / wealth.cummax() - 1.0
    max_dd = float(drawdown.min())
    calmar = ann / abs(max_dd) if max_dd < 0 else np.nan
    return {
        "total_return": total,
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe_repo": sharpe,
        "max_dd": max_dd,
        "calmar": calmar,
    }


def metrics_tables(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    formal: list[dict[str, Any]] = []
    annual: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        group = group.sort_values("date")
        start = pd.Timestamp(group["date"].min())
        end = pd.Timestamp(group["date"].max())
        for window, offset in WINDOWS.items():
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"].ge(requested)] if available else group.iloc[0:0]
            row: dict[str, Any] = {
                "layer": layer,
                "candidate": candidate,
                "window": window,
                "available": available,
                "requested_start": requested,
                "actual_start": sample["date"].min() if available else pd.NaT,
                "end": end,
                "rows": len(sample),
            }
            row.update(metrics(sample["cash_ret"]) if available else {key: np.nan for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "calmar"]})
            if available:
                wealth = (1.0 + sample["cash_ret"]).cumprod()
                drawdown = wealth / wealth.cummax() - 1.0
                trough = drawdown.idxmin()
                peak = wealth.loc[:trough].idxmax()
                row["peak_date"] = sample.loc[peak, "date"]
                row["trough_date"] = sample.loc[trough, "date"]
            else:
                row["peak_date"] = pd.NaT
                row["trough_date"] = pd.NaT
            formal.append(row)
        for year, sample in group.groupby(group["date"].dt.year):
            annual.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "year": int(year),
                    **metrics(sample["cash_ret"]),
                }
            )
    return pd.DataFrame(formal), pd.DataFrame(annual)


def selection_frame(selections: list[Selection]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "layer": item.layer,
                "candidate": item.candidate,
                "tenor": item.tenor,
                "target_delta": item.target_delta,
                "reason": item.reason,
                "eval_date": item.eval_date,
                "scheduled_execution_date": item.scheduled_execution_date,
                "contract": item.contract,
                "contract_month": item.month,
                "expiry": item.expiry,
                "strike": item.strike,
                "selected_delta": item.selected_delta,
                "delta_error": abs(item.selected_delta - item.target_delta),
                "implied_vol": item.implied_vol,
                "eval_close": item.eval_close,
                "eval_volume": item.eval_volume,
                "eval_open_interest": item.eval_open_interest,
            }
            for item in selections
        ]
    )


def metric_value(
    formal: pd.DataFrame, layer: str, candidate: str, window: str, column: str
) -> float:
    row = formal[
        formal["layer"].eq(layer)
        & formal["candidate"].eq(candidate)
        & formal["window"].eq(window)
    ]
    if len(row) != 1 or not bool(row.iloc[0]["available"]):
        raise RuntimeError(f"Missing metric {layer}/{candidate}/{window}/{column}")
    return float(row.iloc[0][column])


def decision_tables(
    formal: pd.DataFrame,
    daily: pd.DataFrame,
    selections: pd.DataFrame,
    trades: pd.DataFrame,
    execution_stats: dict[str, dict[str, int]],
    audit_ok: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        selection = selections[
            selections["layer"].eq("real") & selections["candidate"].eq(candidate)
        ]
        model_daily = daily[
            daily["layer"].eq("model") & daily["candidate"].eq(candidate)
        ]
        real_daily = daily[
            daily["layer"].eq("real") & daily["candidate"].eq(candidate)
        ]
        improvements = {
            "model_full": metric_value(formal, "model", candidate, "full", "ann_return")
            - metric_value(formal, "model", BASELINE, "full", "ann_return"),
            "model_10y": metric_value(formal, "model", candidate, "last_10y", "ann_return")
            - metric_value(formal, "model", BASELINE, "last_10y", "ann_return"),
            "real_full": metric_value(formal, "real", candidate, "full", "ann_return")
            - metric_value(formal, "real", BASELINE, "full", "ann_return"),
            "real_3y": metric_value(formal, "real", candidate, "last_3y", "ann_return")
            - metric_value(formal, "real", BASELINE, "last_3y", "ann_return"),
        }
        model_dd_delta = metric_value(formal, "model", candidate, "full", "max_dd") - metric_value(formal, "model", BASELINE, "full", "max_dd")
        real_dd_delta = metric_value(formal, "real", candidate, "full", "max_dd") - metric_value(formal, "real", BASELINE, "full", "max_dd")
        capital_breaches = int(
            ((model_daily["put_mark_fraction"] + model_daily["call_margin_fraction"] > CASH_BASE + 1e-12).sum())
            + ((real_daily["put_mark_fraction"] + real_daily["call_margin_fraction"] > CASH_BASE + 1e-12).sum())
        )
        median_error = float(selection["delta_error"].median())
        max_error = float(selection["delta_error"].max())
        stats = execution_stats[candidate]
        return_gate = all(value >= -1e-12 for value in improvements.values())
        dd_gate = model_dd_delta >= -0.0200000001 and real_dd_delta >= -0.0200000001
        quality_gate = (
            capital_breaches == 0
            and stats["scheduled_execution_failures"] == 0
            and median_error <= 0.025 + 1e-12
            and max_error <= 0.12 + 1e-12
        )
        hard_pass = bool(return_gate and dd_gate and quality_gate and audit_ok)
        rows.append(
            {
                "candidate": candidate,
                "tenor": candidate.split("_")[0],
                "target_delta": float(candidate[-2:]) / 100.0,
                **{f"cagr_improvement_{key}": value for key, value in improvements.items()},
                "minimum_main_window_cagr_improvement": min(improvements.values()),
                "model_full_maxdd_delta": model_dd_delta,
                "real_full_maxdd_delta": real_dd_delta,
                "capital_breach_days": capital_breaches,
                "scheduled_execution_failures": stats["scheduled_execution_failures"],
                "delayed_trading_days": stats["delayed_trading_days"],
                "median_delta_error": median_error,
                "max_delta_error": max_error,
                "return_gate": return_gate,
                "maxdd_gate": dd_gate,
                "quality_gate": quality_gate,
                "audit_gate": audit_ok,
                "hard_pass": hard_pass,
            }
        )
    table = pd.DataFrame(rows)
    passing = table[table["hard_pass"]].copy()
    if passing.empty:
        selected = BASELINE
        conclusion = "keep_no_call_baseline"
        stability = "no_candidate_passed"
    else:
        passing = passing.sort_values(
            [
                "minimum_main_window_cagr_improvement",
                "real_full_maxdd_delta",
                "target_delta",
                "tenor",
            ],
            ascending=[False, False, True, True],
        )
        selected = str(passing.iloc[0]["candidate"])
        conclusion = "watchlist"
        tenor = str(passing.iloc[0]["tenor"])
        same_tenor = table[table["tenor"].eq(tenor)]
        stability = "narrow_stable" if same_tenor["hard_pass"].all() else "peak_only"
    return table, {
        "conclusion": conclusion,
        "selected_candidate": selected,
        "passing_candidates": table.loc[table["hard_pass"], "candidate"].tolist(),
        "stability_label": stability,
        "live_approved": False,
        "research_status": "research_watchlist_only_not_live_approved",
    }


def audit_results(
    original_base: pd.DataFrame,
    daily: pd.DataFrame,
    selections: pd.DataFrame,
    trades: pd.DataFrame,
    calls: pd.DataFrame,
) -> dict[str, Any]:
    base_out = daily[daily["candidate"].eq(BASELINE)].sort_values(["layer", "date"])
    original = original_base.sort_values(["layer", "date"])
    parity = {
        column: float(np.max(np.abs(base_out[column].to_numpy() - original[column].to_numpy())))
        for column in ["ret", "cash_ret", "nav", "cash_nav"]
    }
    candidates = daily[daily["candidate"].ne(BASELINE)].copy()
    expected_ret = (
        (1.0 + candidates["gross_ret"] + candidates["put_pnl_ret"] + candidates["call_pnl_ret"])
        * (1.0 - candidates["cost_rate"])
        * (1.0 - candidates["put_cost_rate"])
        * (1.0 - candidates["call_cost_rate"])
        - 1.0
    )
    expected_cash = candidates["ret"] + (
        CASH_BASE - candidates["put_mark_fraction"] - candidates["call_margin_fraction"]
    ).clip(lower=0) * CASH_DAILY
    causality_failures = int(
        (selections["eval_date"] >= selections["scheduled_execution_date"]).sum()
        + (trades["eval_date"] >= trades["actual_execution_date"]).sum()
        + (trades["actual_execution_date"] < trades["scheduled_execution_date"]).sum()
    )
    call_lookup = calls.set_index(["contract", "date"])
    close_errors: list[float] = []
    for trade in trades[trades["layer"].eq("real")].itertuples(index=False):
        new = call_lookup.loc[(trade.new_contract, trade.actual_execution_date)]
        if isinstance(new, pd.DataFrame):
            new = new.iloc[-1]
        close_errors.append(abs(float(new["close"]) - float(trade.new_close)))
        if str(trade.old_contract):
            old = call_lookup.loc[(trade.old_contract, trade.actual_execution_date)]
            if isinstance(old, pd.DataFrame):
                old = old.iloc[-1]
            close_errors.append(abs(float(old["close"]) - float(trade.old_close)))
    result = {
        "baseline_parity_max_abs": parity,
        "return_identity_max_abs": float((candidates["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((candidates["cash_ret"] - expected_cash).abs().max()),
        "causality_failures": causality_failures,
        "official_close_max_abs_error": max(close_errors) if close_errors else 0.0,
        "selection_rows": len(selections),
        "trade_rows": len(trades),
    }
    result["all_pass"] = bool(
        max(parity.values()) <= 1e-15
        and result["return_identity_max_abs"] <= 1e-15
        and result["cash_identity_max_abs"] <= 1e-15
        and causality_failures == 0
        and result["official_close_max_abs_error"] <= 1e-12
    )
    return result


def exposure_quality(
    daily: pd.DataFrame,
    selections: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        selected = selections[
            selections["layer"].eq(layer) & selections["candidate"].eq(candidate)
        ]
        trade = trades[
            trades["layer"].eq(layer) & trades["candidate"].eq(candidate)
        ]
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "call_days": int(group["call_contract"].fillna("").ne("").sum()),
                "call_day_ratio": float(group["call_contract"].fillna("").ne("").mean()),
                "trade_events": len(trade),
                "roll_events": int(trade["action"].eq("roll").sum()) if len(trade) else 0,
                "selection_rows": len(selected),
                "median_delta_error": float(selected["delta_error"].median()) if len(selected) else np.nan,
                "max_delta_error": float(selected["delta_error"].max()) if len(selected) else np.nan,
                "median_iv": float(selected["implied_vol"].median()) if len(selected) else np.nan,
                "minimum_eval_volume": float(selected["eval_volume"].min()) if len(selected) and layer == "real" else np.nan,
                "minimum_eval_open_interest": float(selected["eval_open_interest"].min()) if len(selected) and layer == "real" else np.nan,
                "itm_days": int(group["call_itm"].sum()),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "average_margin_fraction": float(group["call_margin_fraction"].mean()),
                "maximum_margin_fraction": float(group["call_margin_fraction"].max()),
                "average_coverage": float(group["call_coverage"].mean()),
                "capital_breach_days": int((group["put_mark_fraction"] + group["call_margin_fraction"] > CASH_BASE + 1e-12).sum()),
            }
        )
    return pd.DataFrame(rows)


def rebound_table(daily: pd.DataFrame) -> pd.DataFrame:
    sample = daily[daily["date"].between("2024-09-18", "2024-10-08")]
    return pd.DataFrame(
        [
            {"layer": layer, "candidate": candidate, **metrics(group.sort_values("date")["cash_ret"])}
            for (layer, candidate), group in sample.groupby(["layer", "candidate"])
        ]
    )


def scan_tables(formal: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    model = formal[formal["layer"].eq("model")].copy()
    long = model.rename(columns={"window": "segment"})[
        [
            "candidate",
            "segment",
            "actual_start",
            "end",
            "rows",
            "ann_return",
            "ann_vol",
            "max_dd",
            "sharpe_repo",
        ]
    ].rename(columns={"actual_start": "start"})
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in model.groupby("candidate"):
        row: dict[str, Any] = {"candidate": candidate}
        lookup = group.set_index("window")
        for window in WINDOWS:
            row[f"ann_return_{window}"] = float(lookup.loc[window, "ann_return"])
            row[f"max_dd_{window}"] = float(lookup.loc[window, "max_dd"])
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def record_text(
    formal: pd.DataFrame,
    decision: dict[str, Any],
    decisions: pd.DataFrame,
    quality: pd.DataFrame,
    audit: dict[str, Any],
) -> str:
    focus = formal[
        formal["window"].isin(["full", "last_10y", "last_3y", "last_1y"])
    ].copy()
    lines = [
        "# IM + MO Call 固定底仓收益增强 v19",
        "",
        "Decision: `" + decision["conclusion"] + "`; 未批准实盘。",
        "Stability: `" + decision["stability_label"] + "`。",
        "Data: 模型2015-04-16—2026-08-14；真实IM/MO 2022-07-22—2026-08-14。",
        "",
        "## 结论",
        "",
        f"- 机械选择：`{decision['selected_candidate']}`。",
        f"- 通过候选：{', '.join(decision['passing_candidates']) if decision['passing_candidates'] else '无'}。",
        "- 本版完全排除网格，只覆盖冻结的1倍滚IM底仓；动态移仓与估值/趋势门控尚未测试。",
        "",
        "## 主要窗口",
        "",
        "|层|候选|窗口|CAGR|MaxDD|Sharpe|",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in focus.itertuples(index=False):
        if not bool(row.available):
            continue
        lines.append(
            f"|{row.layer}|{row.candidate}|{row.window}|{row.ann_return:.2%}|{row.max_dd:.2%}|{row.sharpe_repo:.3f}|"
        )
    lines.extend(
        [
            "",
            "## 门槛",
            "",
            decisions.to_markdown(index=False),
            "",
            "## 数据与资本",
            "",
            quality.to_markdown(index=False),
            "",
            "## 审计",
            "",
            "```json",
            json.dumps(audit, ensure_ascii=False, indent=2),
            "```",
            "",
            "本研究为审计记录，不构成交易建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def update_scan_artifacts(
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    record: str,
    decision: dict[str, Any],
) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python im_mo_call_overwrite_delta_tenor_v19.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "baseline": {
                "candidate": BASELINE,
                "definition": "frozen one-unit rolling IM plus reconstructed_valmom_floor3 Put",
            },
            "candidate_grid": [BASELINE, *CANDIDATES],
            "data_snapshot": {
                "model_start": str(MODEL_START.date()),
                "real_start": str(REAL_START.date()),
                "end": str(END.date()),
                "real_call_source": "official CFFEX daily archives",
            },
            "cost_model": {
                "mo_contract_one_way": 0.00005,
                "two_contract_basket_one_way": CALL_BASKET_SIDE_COST,
                "cash_annual_return": 0.03,
                "call_margin": "CFFEX naked-option approximation frozen in v19 spec",
            },
            "outputs": {
                "record": str(SCAN / "record.md"),
                "scan_summary": str(SCAN / "scan_summary.csv"),
                "window_metrics": str(SCAN / "window_metrics.csv"),
                "scan_meta": str(SCAN / "scan_meta.json"),
                "command_log": str(SCAN / "command_log.txt"),
            },
            "decision": decision["conclusion"],
            "stability_label": decision["stability_label"],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    selections: pd.DataFrame,
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    quality: pd.DataFrame,
    rebound: pd.DataFrame,
    decisions: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
    call_manifest: dict[str, Any],
    record: str,
) -> None:
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "call_trades.csv", index=False)
    selections.to_csv(STAGING / "selection_audit.csv", index=False)
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    quality.to_csv(STAGING / "exposure_quality.csv", index=False)
    rebound.to_csv(STAGING / "rebound_2024_0918_1008.csv", index=False)
    decisions.to_csv(STAGING / "decision_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "audit_summary.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python im_mo_call_overwrite_delta_tenor_v19.py\n", encoding="utf-8"
    )
    data_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "source_hashes": {str(path.relative_to(ROOT)): expected for path, expected in FROZEN_HASHES.items()},
        "call_data_manifest": call_manifest,
        "sample": {
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(data_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sorted(STAGING.iterdir())
            if path.is_file()
        },
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    STAGING.replace(OUTPUT)


def main() -> None:
    call_manifest = verify_inputs()
    baseline = load_baseline()
    upstream = load_upstream()
    market, market_checks = v6.model_market()
    market = market[(market["date"] >= MODEL_START) & (market["date"] <= END)].copy()
    real_market = market[market["date"] >= REAL_START].copy()
    if not np.array_equal(real_market["date"].to_numpy(), upstream["date"].to_numpy()):
        raise RuntimeError("Real market/IM calendar mismatch")
    calls = prepare_calls(pd.DatetimeIndex(market["date"]))

    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    model_events = monthly_events(MODEL_START, model_dates, model_roll_dates(model_dates))
    real_rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    real_events = monthly_events(REAL_START, real_dates, real_rolls)

    daily_parts = [baseline_frame(baseline)]
    trade_parts: list[pd.DataFrame] = []
    all_selections: list[Selection] = []
    execution_stats: dict[str, dict[str, int]] = {}
    for tenor in TENORS:
        for target_delta in DELTAS:
            label = candidate_name(tenor, target_delta)
            model_selections = build_model_selections(
                market, model_events, tenor, target_delta, label
            )
            real_selections = build_real_selections(
                calls, real_market, real_events, tenor, target_delta, label
            )
            all_selections.extend(model_selections)
            all_selections.extend(real_selections)
            model_overlay, model_trades = run_model_overlay(
                market, model_selections, label
            )
            real_overlay, real_trades, stats = run_real_overlay(
                upstream, calls, real_market, real_selections, label
            )
            execution_stats[label] = stats
            model_base = baseline[
                baseline["layer"].eq("model")
            ].drop(columns=["layer", "candidate"])
            real_base = baseline[
                baseline["layer"].eq("real")
            ].drop(columns=["layer", "candidate"])
            model_candidate = assemble_candidate(model_base, model_overlay, label)
            model_candidate["layer"] = "model"
            real_candidate = assemble_candidate(real_base, real_overlay, label)
            real_candidate["layer"] = "real"
            daily_parts.extend([model_candidate, real_candidate])
            trade_parts.extend([model_trades, real_trades])

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(
        ["layer", "candidate", "date"]
    ).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(
        ["layer", "candidate", "actual_execution_date"]
    ).reset_index(drop=True)
    selections = selection_frame(all_selections).sort_values(
        ["layer", "candidate", "eval_date"]
    ).reset_index(drop=True)
    formal, annual = metrics_tables(daily)
    audit = audit_results(baseline, daily, selections, trades, calls)
    audit["market_checks"] = market_checks
    quality = exposure_quality(daily, selections, trades)
    rebound = rebound_table(daily)
    decisions, decision = decision_tables(
        formal, daily, selections, trades, execution_stats, bool(audit["all_pass"])
    )
    scan_long, scan_wide = scan_tables(formal)
    record = record_text(formal, decision, decisions, quality, audit)
    update_scan_artifacts(scan_long, scan_wide, record, decision)
    write_outputs(
        daily,
        trades,
        selections,
        formal,
        annual,
        quality,
        rebound,
        decisions,
        decision,
        audit,
        call_manifest,
        record,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
