#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "numpy",
#   "pandas",
#   "tabulate",
# ]
# ///
"""Preregistered IC + 510500 ETF Put tiered-notional and tiered-Delta study."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_510500_put_unbounded_valuation_or_mom120_v19 as v19

ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_tiered_notional_delta_v20"
OUTPUT = ROOT / "outputs" / VERSION
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "87cf67abf960d5935bf5211b958f74a470fb4c18c6ebd58a4b49eac73bc1c874"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_510500etf_put_ic_510500_put_tiered_notional_delta_v20_"
    "ic_3m_monthly_exit_m95_close_valuation_tier_notional_delta_sizing"
)

V19_OUTPUT = ROOT / "outputs" / "ic_510500_put_unbounded_valuation_or_mom120_v19"
V19_DAILY = V19_OUTPUT / "daily_candidates.csv.gz"
V19_OUTPUT_MANIFEST = V19_OUTPUT / "output_manifest.json"
V19_DATA_MANIFEST = V19_OUTPUT / "data_manifest.json"

MODEL_START = v19.MODEL_START
REAL_START = v19.REAL_START
END = v19.END
WINDOWS = v19.WINDOWS
MONEYNESS = 0.95
TENOR = "3m_monthly"
PUT_SIDE_COST = v19.v18.v13.proxy.PUT_FULL_SIDE_COST
IC_MULTIPLIER = 200.0
OPTION_MULTIPLIER = 10000.0

VARIANTS = (
    "no_put",
    "binary_notional1x",
    "binary_delta25",
    "tier_notional123",
    "tier_delta255075",
)
DELTA_VARIANTS = {"binary_delta25", "tier_delta255075"}
FIXED_VARIANTS = {"binary_notional1x", "tier_notional123"}

INPUT_HASHES = {
    ROOT / "ic_510500_put_unbounded_valuation_or_mom120_v19.py": (
        "43b19a7f0a229d06627fede7615c61c2eb7032f359eafdf250e2b8168d16c7a1"
    ),
    V19_OUTPUT_MANIFEST: "a2e7bd8b9727a16bdb40486a2c48a223cd9891d6ad5eace0905f2cc6522d8aea",
    V19_DATA_MANIFEST: "29b4fe88f6d2ae56f0ac8030637dd3ed67ee93a4bd440a18e6b82fce1013c850",
    V19_DAILY: "6d92ac2771511f04eb2b42bf8f65fe2f0dc4feaec7898ae8a6559e5dc4e1fe18",
    ROOT
    / "outputs"
    / "ic_fixed_valuation_unbounded_score_v6"
    / "daily_unbounded_fixed_scores.csv.gz": (
        "34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77"
    ),
    ROOT / "ic_510500_put_unbounded_valuation_gate_v18.py": (
        "f7a9b7ccc8812bf448d36170e75bf7a661aec15c2af1ded99cd9162d6845d121"
    ),
    ROOT / "ic_510500_put_close_execution_full_retest_v17.py": (
        "24c1702082e08f6cdf1538a879586ac684480dba8890d9ea3649c34a36150629"
    ),
    ROOT / "ic_510500_put_absolute_momentum_protection_tool_v11.py": (
        "2149d52637304bf09a2d1be674ff3c761d8d56033a9391a2ed46f3387ed3d4f7"
    ),
    ROOT / "ic_510500_put_v4_monthly_tenor_rerun_v6.py": (
        "16c488da8b0c1255758036a57549476521cce2e8004dbc5660ad298161a8fee2"
    ),
    ROOT / "ic_510500_put_proxy_validation_v1.py": (
        "5836849ca4c0e42ab4a04e2c82d81f049b5f2fb2799333c67177209b8fc2a7a3"
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def variant_parameters(variant: str) -> dict[str, Any]:
    mapping = {
        "no_put": ("baseline", "none", "none"),
        "binary_notional1x": ("binary", "notional", "1x"),
        "binary_delta25": ("binary", "delta", "25%"),
        "tier_notional123": ("tiered", "notional", "1x/2x/3x"),
        "tier_delta255075": ("tiered", "delta", "25%/50%/75%"),
    }
    signal_shape, sizing_method, sizing_ladder = mapping[variant]
    return {
        "signal_shape": signal_shape,
        "sizing_method": sizing_method,
        "sizing_ladder": sizing_ladder,
    }


def candidate_parts(candidate: str) -> dict[str, Any]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "variant": variant, **variant_parameters(variant)}


def verify_inputs() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v20 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v20 specification sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v20 input changed: {path.relative_to(ROOT)}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    upstream_manifest = json.loads(V19_OUTPUT_MANIFEST.read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in upstream_manifest.items():
        path = V19_OUTPUT / name
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append({"file": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"v19 output manifest mismatch: {mismatches}")
    return {
        "v19_output_manifest_files": len(upstream_manifest),
        "v19_output_manifest_match": True,
    }


def valuation_tier(median_score: float) -> int:
    if median_score + 1e-12 >= 2.15:
        return 3
    if median_score + 1e-12 >= 2.10:
        return 2
    if median_score + 1e-12 >= 2.00:
        return 1
    return 0


def risk_tier(median_score: float, momentum_120: float) -> int:
    return max(valuation_tier(median_score), int(momentum_120 <= 1e-12))


def target_for_variant(variant: str, tier: int) -> tuple[float, float]:
    if variant == "binary_notional1x":
        return (float(tier > 0), math.nan)
    if variant == "tier_notional123":
        return (float(tier), math.nan)
    if variant == "binary_delta25":
        return (math.nan, 0.25 * float(tier > 0))
    if variant == "tier_delta255075":
        return (math.nan, 0.25 * float(tier))
    raise ValueError(variant)


def build_schedules(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    signal_inputs: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v19.v18.v13.proxy
    frame = signal_inputs.set_index("date")
    trade_dates = pd.DatetimeIndex(ic["date"])
    evaluations = {
        "model": proxy.evaluation_dates(
            "daily", MODEL_START, END, trade_dates, daily_valuation
        ),
        "real": proxy.evaluation_dates(
            "daily", REAL_START, END, trade_dates, daily_valuation
        ),
    }
    signal_rows: list[dict[str, Any]] = []
    schedule_rows: list[dict[str, Any]] = []
    for layer, dates in evaluations.items():
        start = MODEL_START if layer == "model" else REAL_START
        for variant in VARIANTS:
            if variant == "no_put":
                continue
            for sequence, day in enumerate(dates):
                row = frame.loc[day]
                tier = risk_tier(
                    float(row["unbounded_median_knot"]), float(row["momentum_120"])
                )
                notional_target, delta_target = target_for_variant(variant, tier)
                execution, initial = proxy.next_execution(day, start, trade_dates)
                common = {
                    "layer": layer,
                    "signal_variant": variant,
                    "sequence": sequence,
                    "eval_date": day,
                    "risk_tier": tier,
                    "valuation_tier": valuation_tier(
                        float(row["unbounded_median_knot"])
                    ),
                    "momentum_floor_on": bool(float(row["momentum_120"]) <= 1e-12),
                    "target_notional_fraction": notional_target,
                    "target_delta": delta_target,
                    "unbounded_median_knot": float(row["unbounded_median_knot"]),
                    "momentum_120": float(row["momentum_120"]),
                    "old_fixed_risk": float(row["old_fixed_risk"]),
                    "pe_aggregate_ttm": float(row["pe_aggregate_ttm"]),
                    "pb_aggregate": float(row["pb_aggregate"]),
                    "erp": float(row["erp"]),
                }
                signal_rows.append(common)
                schedule_rows.append(
                    {
                        **common,
                        "frequency": "daily",
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": (
                            notional_target if np.isfinite(notional_target) else delta_target
                        ),
                        "three_tier_target_fraction": (
                            notional_target if np.isfinite(notional_target) else delta_target
                        ),
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    )
    signals = pd.DataFrame(signal_rows).sort_values(
        ["layer", "signal_variant", "eval_date"]
    )
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v20 execution event")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v20 signal/execution leakage")
    calculated = schedule.apply(
        lambda row: risk_tier(
            float(row["unbounded_median_knot"]), float(row["momentum_120"])
        ),
        axis=1,
    )
    if not calculated.equals(schedule["risk_tier"]):
        raise RuntimeError("v20 risk-tier identity failed")
    return schedule.reset_index(drop=True), signals.reset_index(drop=True)


def bs_put_delta(
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    sigma: float,
    years: float,
) -> float:
    proxy = v19.v18.v13.proxy
    if years <= 0:
        if spot < strike:
            return -1.0
        if math.isclose(spot, strike, rel_tol=0.0, abs_tol=1e-12):
            return -0.5
        return 0.0
    if min(spot, strike, sigma) <= 0:
        raise RuntimeError("Invalid Black-Scholes Delta input")
    root = math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend + 0.5 * sigma * sigma) * years
    ) / (sigma * root)
    return -math.exp(-dividend * years) * proxy.norm_cdf(-d1)


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    years: float,
) -> float | None:
    proxy = v19.v18.v13.proxy
    if years <= 0 or min(price, spot, strike) <= 0:
        return None
    low = 1e-4
    high = 5.0
    low_value = proxy.bs_put(spot, strike, rate, dividend, low, years)
    high_value = proxy.bs_put(spot, strike, rate, dividend, high, years)
    tolerance = 1e-10 * max(1.0, spot, strike)
    if price < low_value - tolerance or price > high_value + tolerance:
        return None
    target = min(max(price, low_value), high_value)
    for _ in range(100):
        middle = 0.5 * (low + high)
        value = proxy.bs_put(spot, strike, rate, dividend, middle, years)
        if value > target:
            high = middle
        else:
            low = middle
    return 0.5 * (low + high)


def model_price_and_delta(position: Any, row: object) -> tuple[float, float]:
    proxy = v19.v18.v13.proxy
    day = pd.Timestamp(row.date)
    years = max((position.expiry - day).days, 0) / 365.0
    spot = float(row.spot_close)
    rate = float(row.rate_close)
    dividend = float(row.dividend_close)
    sigma = float(row.sigma_close)
    price = proxy.bs_put(spot, position.strike, rate, dividend, sigma, years)
    delta = bs_put_delta(spot, position.strike, rate, dividend, sigma, years)
    return price, delta


def real_delta_from_mark(
    price: float,
    spot: float,
    strike: float,
    expiry: pd.Timestamp,
    day: pd.Timestamp,
    rate: float,
    dividend: float,
    fallback_sigma: float,
) -> tuple[float, float, str]:
    years = max((expiry - day).days, 0) / 365.0
    sigma = implied_volatility(price, spot, strike, rate, dividend, years)
    source = "actual_close_iv"
    if sigma is None:
        sigma = fallback_sigma
        source = "qvix_proxy_fallback"
    delta = bs_put_delta(spot, strike, rate, dividend, sigma, years)
    return delta, sigma, source


@dataclass
class DeltaModelPosition:
    contract_month: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    units: float
    notional_fraction: float
    target_delta: float
    prior_mark: float


@dataclass
class DeltaRealPosition:
    security_id: str
    contract_id: str
    contract_month: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    qty: int
    full_qty: int
    notional_fraction: float
    target_delta: float
    prior_mark: float
    entry_moneyness: float
    last_iv: float


def _schedule_events(schedule: pd.DataFrame, layer: str) -> dict[pd.Timestamp, object]:
    subset = schedule[schedule["layer"].eq(layer)]
    return {
        pd.Timestamp(row.execution_date): row for row in subset.itertuples(index=False)
    }


def _trading_delay(
    trade_dates: pd.DatetimeIndex, request: pd.Timestamp, actual: pd.Timestamp
) -> int:
    return int(((trade_dates > request) & (trade_dates <= actual)).sum())


def run_model_delta(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    market: pd.DataFrame,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v19.v18.v13.proxy
    v6 = v19.v18.v13.v6
    daily = ic[ic["date"] >= MODEL_START].copy().reset_index(drop=True)
    daily["prior_settle"] = daily["settle"].shift(1)
    daily.loc[0, "prior_settle"] = daily.loc[0, "settle"]
    merged = daily.merge(market.drop(columns=["settle"]), on="date", validate="one_to_one")
    events = _schedule_events(schedule, "model")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: DeltaModelPosition | None = None
    latest_target = 0.0
    latest_tier = 0
    latest_eval: pd.Timestamp | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    def open_position(row: object, target: float) -> tuple[DeltaModelPosition, float, float]:
        day = pd.Timestamp(row.date)
        month = v6.desired_model_month(day, TENOR, trade_dates)
        expiry = proxy.fourth_wednesday(month, trade_dates)
        strike = float(row.spot_close) * MONEYNESS
        shell = DeltaModelPosition(month, expiry, strike, 0.0, 0.0, target, 0.0)
        price, delta = model_price_and_delta(shell, row)
        absolute_delta = abs(delta)
        if absolute_delta <= 1e-8:
            raise RuntimeError("Model entry Delta too small for sizing")
        notional_fraction = target / absolute_delta
        units = (
            float(row.settle)
            * IC_MULTIPLIER
            / float(row.spot_close)
            * notional_fraction
        )
        position = DeltaModelPosition(
            month, expiry, strike, units, notional_fraction, target, price
        )
        return position, absolute_delta, abs(notional_fraction * absolute_delta - target)

    for row in merged.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        changed = False
        if event is not None:
            new_target = float(event.target_delta)
            changed = not math.isclose(new_target, latest_target, abs_tol=1e-12)
            latest_target = new_target
            latest_tier = int(event.risk_tier)
            latest_eval = pd.Timestamp(event.eval_date)
        denominator = float(row.prior_settle) * IC_MULTIPLIER
        pnl = 0.0
        cost = 0.0
        action = ""
        old = active
        old_notional = active.notional_fraction if active is not None else 0.0
        entry_delta = np.nan
        target_error = np.nan
        monthly_request = bool(day in roll_dates and active is not None and latest_target > 0)

        if active is None and latest_target > 0:
            active, entry_delta, target_error = open_position(row, latest_target)
            cost += active.notional_fraction * PUT_SIDE_COST
            action = "close_buy"
        elif active is not None and latest_target == 0:
            price, _ = model_price_and_delta(active, row)
            pnl += active.units * (price - active.prior_mark) / denominator
            cost += active.notional_fraction * PUT_SIDE_COST
            active = None
            action = "close_exit"
        elif active is not None and monthly_request:
            old_price, _ = model_price_and_delta(active, row)
            pnl += active.units * (old_price - active.prior_mark) / denominator
            new_position, entry_delta, target_error = open_position(row, latest_target)
            cost += (active.notional_fraction + new_position.notional_fraction) * PUT_SIDE_COST
            active = new_position
            action = "close_roll_monthly"
        elif active is not None and changed:
            price, delta = model_price_and_delta(active, row)
            pnl += active.units * (price - active.prior_mark) / denominator
            entry_delta = abs(delta)
            if entry_delta <= 1e-8:
                raise RuntimeError("Model resize Delta too small for sizing")
            new_notional = latest_target / entry_delta
            new_units = (
                float(row.settle)
                * IC_MULTIPLIER
                / float(row.spot_close)
                * new_notional
            )
            cost += abs(new_notional - active.notional_fraction) * PUT_SIDE_COST
            active.units = new_units
            active.notional_fraction = new_notional
            active.target_delta = latest_target
            active.prior_mark = price
            target_error = abs(new_notional * entry_delta - latest_target)
            action = "close_resize"
        elif active is not None:
            price, _ = model_price_and_delta(active, row)
            pnl += active.units * (price - active.prior_mark) / denominator
            active.prior_mark = price

        if action:
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": day,
                    "actual_execution_date": day,
                    "action": action,
                    "risk_tier": latest_tier,
                    "target_delta": latest_target,
                    "target_delta_error": target_error,
                    "entry_abs_delta": entry_delta,
                    "delta_source": "model_qvix",
                    "old_notional_fraction": old_notional,
                    "new_notional_fraction": active.notional_fraction if active else 0.0,
                    "old_contract": "",
                    "new_contract": "",
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": MONEYNESS if active else np.nan,
                    "delay_days": 0,
                    "delay_trading_days": 0,
                    "forced_month_roll": action == "close_roll_monthly",
                    "roll_request_date": day if action == "close_roll_monthly" else pd.NaT,
                    "same_month_reset": bool(
                        old is not None
                        and active is not None
                        and old.contract_month == active.contract_month
                    ),
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
        mark_fraction = 0.0
        contract = ""
        units = 0.0
        notional_fraction = 0.0
        effective_delta = 0.0
        absolute_delta = np.nan
        if active is not None:
            _, current_delta = model_price_and_delta(active, row)
            absolute_delta = abs(current_delta)
            notional_fraction = (
                active.units
                * float(row.spot_close)
                / (float(row.settle) * IC_MULTIPLIER)
            )
            effective_delta = notional_fraction * absolute_delta
            mark_fraction = (
                active.units * active.prior_mark / (float(row.settle) * IC_MULTIPLIER)
            )
            contract = f"MODEL_{active.contract_month.strftime('%y%m')}_{active.strike:.6f}"
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
                "target_fraction": latest_target,
                "signal_target_fraction": latest_target,
                "target_delta": latest_target,
                "risk_tier": latest_tier,
                "actual_notional_fraction": notional_fraction,
                "abs_put_delta": absolute_delta,
                "effective_delta_hedge_ratio": effective_delta,
                "delta_source": "model_qvix" if active is not None else "",
                "entry_moneyness_mark": MONEYNESS if active is not None else np.nan,
                "carried_mark": False,
                "mark_stale_days": 0,
                "deferred_adjustment": False,
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def _full_real_qty(row: object, etf_close: float) -> int:
    return max(
        1,
        round(
            float(row.settle)
            * IC_MULTIPLIER
            / (etf_close * OPTION_MULTIPLIER)
        ),
    )


def _delta_real_qty(full_qty: int, target_delta: float, absolute_delta: float) -> int:
    if absolute_delta <= 1e-8:
        raise RuntimeError("Real trade Delta too small for sizing")
    return max(1, round(full_qty * target_delta / absolute_delta))


def run_real_delta(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v19.v18.v13.proxy
    v6 = v19.v18.v13.v6
    v11 = v19.v18.v11
    daily = ic[ic["date"] >= REAL_START].copy().reset_index(drop=True)
    offset = len(ic) - len(daily)
    daily["prior_settle"] = ic["settle"].shift(1).loc[daily.index + offset].to_numpy()
    daily.loc[0, "prior_settle"] = float(
        ic.loc[ic["date"] < REAL_START, "settle"].iloc[-1]
    )
    etf = frames["etf500"].set_index("date")
    market_lookup = market.set_index("date")
    histories = frames["histories"]
    history_lookup = histories.set_index(["security_id", "date"])
    history_groups = {
        key: group.sort_values("date") for key, group in histories.groupby("security_id")
    }
    snapshots = frames["snapshots"]
    events = _schedule_events(schedule, "real")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: DeltaRealPosition | None = None
    latest_target = 0.0
    latest_tier = 0
    latest_eval: pd.Timestamp | None = None
    pending_action_since: pd.Timestamp | None = None
    pending_roll_since: pd.Timestamp | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    def select_contract(day: pd.Timestamp, row: object) -> tuple[pd.Series, pd.Series] | None:
        month = v6.desired_real_month(snapshots, day, TENOR, trade_dates)
        if month is None:
            return None
        return v11.select_real_contract_target(
            snapshots,
            history_lookup,
            day,
            month,
            float(etf.loc[day, "close"]),
            MONEYNESS,
        )

    def sized_position(
        day: pd.Timestamp,
        row: object,
        master: pd.Series,
        quote: pd.Series,
        target: float,
    ) -> tuple[DeltaRealPosition, float, float, str]:
        market_row = market_lookup.loc[day]
        etf_close = float(etf.loc[day, "close"])
        month = pd.Timestamp(master["contract_month"])
        expiry = proxy.fourth_wednesday(month, trade_dates)
        delta, sigma, source = real_delta_from_mark(
            float(quote["close"]),
            etf_close,
            float(master["strike"]),
            expiry,
            day,
            float(market_row["rate_close"]),
            float(market_row["dividend_close"]),
            float(market_row["sigma_close"]),
        )
        absolute_delta = abs(delta)
        full_qty = _full_real_qty(row, etf_close)
        qty = _delta_real_qty(full_qty, target, absolute_delta)
        notional = qty / full_qty
        error = abs(notional * absolute_delta - target)
        return (
            DeltaRealPosition(
                str(master["security_id"]),
                str(master["contract_id"]),
                month,
                expiry,
                float(master["strike"]),
                qty,
                full_qty,
                notional,
                target,
                float(quote["close"]),
                float(master["strike"]) / etf_close,
                sigma,
            ),
            absolute_delta,
            error,
            source,
        )

    for row in daily.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        if event is not None:
            new_target = float(event.target_delta)
            if not math.isclose(new_target, latest_target, abs_tol=1e-12):
                pending_action_since = pending_action_since or day
            latest_target = new_target
            latest_tier = int(event.risk_tier)
            latest_eval = pd.Timestamp(event.eval_date)
        if day in roll_dates and active is not None and latest_target > 0:
            pending_roll_since = pending_roll_since or day
        if active is None and latest_target > 0:
            pending_action_since = pending_action_since or day

        denominator = float(row.prior_settle) * IC_MULTIPLIER
        etf_close = float(etf.loc[day, "close"])
        pnl = 0.0
        cost = 0.0
        stale_days = 0
        carried = False
        action = ""
        old = active
        old_notional = active.notional_fraction if active is not None else 0.0
        request_date = pending_roll_since or pending_action_since or day
        entry_delta = np.nan
        target_error = np.nan
        delta_source = ""

        if active is not None and latest_target == 0:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            if quote is not None and float(quote["close"]) > 0 and float(quote["volume"]) > 0:
                pnl += (
                    active.qty
                    * OPTION_MULTIPLIER
                    * (float(quote["close"]) - active.prior_mark)
                    / denominator
                )
                cost += active.notional_fraction * PUT_SIDE_COST
                active = None
                action = "close_exit"
                pending_action_since = None
                pending_roll_since = None
        elif active is not None and pending_roll_since is not None:
            selected = select_contract(day, row)
            old_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if (
                selected is not None
                and old_quote is not None
                and float(old_quote["close"]) > 0
                and float(old_quote["volume"]) > 0
            ):
                master, new_quote = selected
                new_position, entry_delta, target_error, delta_source = sized_position(
                    day, row, master, new_quote, latest_target
                )
                pnl += (
                    active.qty
                    * OPTION_MULTIPLIER
                    * (float(old_quote["close"]) - active.prior_mark)
                    / denominator
                )
                cost += (
                    active.notional_fraction + new_position.notional_fraction
                ) * PUT_SIDE_COST
                active = new_position
                action = "close_roll_monthly"
                pending_roll_since = None
                pending_action_since = None
        elif active is None and latest_target > 0:
            selected = select_contract(day, row)
            if selected is not None:
                master, quote = selected
                active, entry_delta, target_error, delta_source = sized_position(
                    day, row, master, quote, latest_target
                )
                cost += active.notional_fraction * PUT_SIDE_COST
                action = "close_buy"
                pending_action_since = None
        elif active is not None and pending_action_since is not None:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            if quote is not None and float(quote["close"]) > 0 and float(quote["volume"]) > 0:
                market_row = market_lookup.loc[day]
                delta, sigma, delta_source = real_delta_from_mark(
                    float(quote["close"]),
                    etf_close,
                    active.strike,
                    active.expiry,
                    day,
                    float(market_row["rate_close"]),
                    float(market_row["dividend_close"]),
                    float(market_row["sigma_close"]),
                )
                entry_delta = abs(delta)
                full_qty = _full_real_qty(row, etf_close)
                new_qty = _delta_real_qty(full_qty, latest_target, entry_delta)
                new_notional = new_qty / full_qty
                target_error = abs(new_notional * entry_delta - latest_target)
                pnl += (
                    active.qty
                    * OPTION_MULTIPLIER
                    * (float(quote["close"]) - active.prior_mark)
                    / denominator
                )
                cost += abs(new_notional - active.notional_fraction) * PUT_SIDE_COST
                active.qty = new_qty
                active.full_qty = full_qty
                active.notional_fraction = new_notional
                active.target_delta = latest_target
                active.prior_mark = float(quote["close"])
                active.last_iv = sigma
                action = "close_resize"
                pending_action_since = None

        if not action and active is not None:
            mark, stale_days, carried = proxy.real_mark(
                history_groups, active, day, etf_close
            )
            pnl += (
                active.qty * OPTION_MULTIPLIER * (mark - active.prior_mark) / denominator
            )
            active.prior_mark = mark

        if action:
            actual_request = request_date
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": actual_request,
                    "actual_execution_date": day,
                    "action": action,
                    "risk_tier": latest_tier,
                    "target_delta": latest_target,
                    "target_delta_error": target_error,
                    "entry_abs_delta": entry_delta,
                    "delta_source": delta_source,
                    "old_notional_fraction": old_notional,
                    "new_notional_fraction": active.notional_fraction if active else 0.0,
                    "old_contract": old.contract_id if old else "",
                    "new_contract": active.contract_id if active else "",
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": active.entry_moneyness if active else np.nan,
                    "delay_days": int((day - actual_request).days),
                    "delay_trading_days": _trading_delay(
                        trade_dates, actual_request, day
                    ),
                    "forced_month_roll": action == "close_roll_monthly",
                    "roll_request_date": actual_request
                    if action == "close_roll_monthly"
                    else pd.NaT,
                    "same_month_reset": bool(
                        old is not None
                        and active is not None
                        and old.contract_month == active.contract_month
                    ),
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
            pending_action_since = day
        mark_fraction = 0.0
        contract = ""
        qty = 0
        entry_moneyness = np.nan
        notional_fraction = 0.0
        effective_delta = 0.0
        absolute_delta = np.nan
        mark_delta_source = ""
        if active is not None:
            market_row = market_lookup.loc[day]
            current_delta, sigma, mark_delta_source = real_delta_from_mark(
                active.prior_mark,
                etf_close,
                active.strike,
                active.expiry,
                day,
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                float(market_row["sigma_close"]),
            )
            active.last_iv = sigma
            absolute_delta = abs(current_delta)
            notional_fraction = (
                active.qty
                * OPTION_MULTIPLIER
                * etf_close
                / (float(row.settle) * IC_MULTIPLIER)
            )
            effective_delta = notional_fraction * absolute_delta
            mark_fraction = (
                active.qty
                * OPTION_MULTIPLIER
                * active.prior_mark
                / (float(row.settle) * IC_MULTIPLIER)
            )
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
                "target_fraction": latest_target,
                "signal_target_fraction": latest_target,
                "target_delta": latest_target,
                "risk_tier": latest_tier,
                "actual_notional_fraction": notional_fraction,
                "abs_put_delta": absolute_delta,
                "effective_delta_hedge_ratio": effective_delta,
                "delta_source": mark_delta_source,
                "entry_moneyness_mark": entry_moneyness,
                "carried_mark": carried,
                "mark_stale_days": stale_days,
                "deferred_adjustment": bool(
                    pending_action_since is not None or pending_roll_since is not None
                ),
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def _attach_risk_tier(
    overlay: pd.DataFrame, candidate_schedule: pd.DataFrame
) -> pd.DataFrame:
    states = candidate_schedule[["execution_date", "risk_tier", "target_delta"]].copy()
    states = states.rename(columns={"execution_date": "date"})
    merged = overlay.merge(states, on="date", how="left", suffixes=("", "_schedule"))
    merged["risk_tier"] = merged["risk_tier"].ffill().fillna(0).astype(int)
    if "target_delta_schedule" in merged:
        merged["target_delta"] = merged["target_delta_schedule"].ffill().fillna(0.0)
        merged = merged.drop(columns=["target_delta_schedule"])
    return merged


def decorate_fixed_overlay(
    overlay: pd.DataFrame,
    layer: str,
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
) -> pd.DataFrame:
    proxy = v19.v18.v13.proxy
    result = overlay.copy()
    result["target_delta"] = np.nan
    result["actual_notional_fraction"] = 0.0
    result["abs_put_delta"] = np.nan
    result["effective_delta_hedge_ratio"] = 0.0
    result["delta_source"] = ""
    ic_lookup = frames["ic"].set_index("date")
    market_lookup = market.set_index("date")
    trade_dates = pd.DatetimeIndex(frames["ic"]["date"])
    if layer == "real":
        etf = frames["etf500"].set_index("date")
        contract_master = (
            frames["snapshots"]
            .sort_values("date")
            .drop_duplicates("contract_id", keep="last")
            .set_index("contract_id")
        )
    for index, row in result[result["put_qty"].astype(float).gt(0)].iterrows():
        day = pd.Timestamp(row["date"])
        ic_settle = float(ic_lookup.loc[day, "settle"])
        market_row = market_lookup.loc[day]
        if layer == "model":
            parts = str(row["put_contract"]).split("_", 2)
            month = pd.Timestamp(2000 + int(parts[1][:2]), int(parts[1][2:]), 1)
            strike = float(parts[2])
            expiry = proxy.fourth_wednesday(month, trade_dates)
            years = max((expiry - day).days, 0) / 365.0
            spot = float(market_row["spot_close"])
            delta = bs_put_delta(
                spot,
                strike,
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                float(market_row["sigma_close"]),
                years,
            )
            notional = float(row["put_qty"]) * spot / (ic_settle * IC_MULTIPLIER)
            source = "model_qvix"
        else:
            contract = str(row["put_contract"])
            if contract not in contract_master.index:
                raise RuntimeError(f"Missing real contract master: {contract}")
            master = contract_master.loc[contract]
            if isinstance(master, pd.DataFrame):
                master = master.iloc[-1]
            month = pd.Timestamp(master["contract_month"])
            expiry = proxy.fourth_wednesday(month, trade_dates)
            strike = float(master["strike"])
            spot = float(etf.loc[day, "close"])
            price = (
                float(row["put_mark_fraction"])
                * ic_settle
                * IC_MULTIPLIER
                / (float(row["put_qty"]) * OPTION_MULTIPLIER)
            )
            delta, _, source = real_delta_from_mark(
                price,
                spot,
                strike,
                expiry,
                day,
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                float(market_row["sigma_close"]),
            )
            notional = (
                float(row["put_qty"])
                * OPTION_MULTIPLIER
                * spot
                / (ic_settle * IC_MULTIPLIER)
            )
        result.loc[index, "actual_notional_fraction"] = notional
        result.loc[index, "abs_put_delta"] = abs(delta)
        result.loc[index, "effective_delta_hedge_ratio"] = notional * abs(delta)
        result.loc[index, "delta_source"] = source
    return result


def normalize_fixed_trades(trades: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    result = trades.copy()
    event_lookup = schedule.set_index("execution_date")[["risk_tier"]]
    result["risk_tier"] = result["actual_execution_date"].map(
        event_lookup["risk_tier"].to_dict()
    )
    result["risk_tier"] = result["risk_tier"].ffill().fillna(0).astype(int)
    result["target_delta"] = np.nan
    result["target_delta_error"] = np.nan
    result["entry_abs_delta"] = np.nan
    result["delta_source"] = ""
    result["old_notional_fraction"] = np.nan
    result["new_notional_fraction"] = result["target_fraction"].astype(float)
    result["action"] = result["action"].str.replace("open_", "close_", regex=False)
    return result


def run_candidates(
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proxy = v19.v18.v13.proxy
    roll_dates = v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    daily_parts = [
        proxy.no_put_rows(frames["ic"], MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    for layer in ("model", "real"):
        for variant in VARIANTS:
            if variant == "no_put":
                continue
            candidate_schedule = schedule[
                schedule["layer"].eq(layer)
                & schedule["signal_variant"].eq(variant)
            ].copy()
            label = f"{layer}_{variant}"
            if variant in FIXED_VARIANTS:
                if layer == "model":
                    overlay, trades, _ = v19.v18.v11.run_model_tool(
                        frames,
                        market,
                        candidate_schedule,
                        v19.EXECUTION_STRUCTURE,
                        MONEYNESS,
                        label,
                        roll_dates,
                    )
                else:
                    overlay, trades, _ = v19.v18.v11.run_real_tool(
                        frames,
                        candidate_schedule,
                        v19.EXECUTION_STRUCTURE,
                        MONEYNESS,
                        label,
                        roll_dates,
                    )
                overlay = _attach_risk_tier(overlay, candidate_schedule)
                overlay = decorate_fixed_overlay(overlay, layer, frames, market)
                trades = normalize_fixed_trades(trades, candidate_schedule)
            else:
                if layer == "model":
                    overlay, trades = run_model_delta(
                        frames["ic"], candidate_schedule, market, label, roll_dates
                    )
                else:
                    overlay, trades = run_real_delta(
                        frames["ic"],
                        candidate_schedule,
                        frames,
                        market,
                        label,
                        roll_dates,
                    )
            daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
            if not trades.empty:
                trade_parts.append(trades)

    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    )
    for column, default in [
        ("signal_target_fraction", 0.0),
        ("target_delta", np.nan),
        ("risk_tier", 0),
        ("actual_notional_fraction", 0.0),
        ("abs_put_delta", np.nan),
        ("effective_delta_hedge_ratio", 0.0),
        ("delta_source", ""),
    ]:
        if column not in daily:
            daily[column] = default
        daily[column] = daily[column].fillna(default) if not pd.isna(default) else daily[column]
    daily["cash_nav"] = daily.groupby("candidate", sort=False)["cash_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    daily["cash_drawdown"] = daily.groupby("candidate", sort=False)[
        "cash_nav"
    ].transform(lambda values: values / values.cummax() - 1.0)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    return daily.reset_index(drop=True), trades.reset_index(drop=True)


def baseline_parity(daily: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    frozen = pd.read_csv(V19_DAILY, parse_dates=["date"])
    mappings = {
        "model_no_put": "model_no_put",
        "real_no_put": "real_no_put",
        "model_binary_notional1x": "model_median200_or_mom120",
        "real_binary_notional1x": "real_median200_or_mom120",
    }
    columns = [
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "target_fraction",
        "ret",
        "cash_ret",
    ]
    rows: list[dict[str, Any]] = []
    for current, prior in mappings.items():
        left = daily[daily["candidate"].eq(current)][["date", *columns]]
        right = frozen[frozen["candidate"].eq(prior)][["date", *columns]]
        joined = left.merge(right, on="date", suffixes=("_v20", "_v19"), validate="one_to_one")
        row: dict[str, Any] = {
            "current_candidate": current,
            "prior_candidate": prior,
            "rows": len(joined),
        }
        for column in columns:
            row[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v20"] - joined[f"{column}_v19"]).abs().max()
            )
        rows.append(row)
    v19_schedule = pd.read_csv(
        V19_OUTPUT / "evaluation_schedule.csv.gz", parse_dates=["execution_date"]
    )
    for layer in ("model", "real"):
        current = schedule[
            schedule["layer"].eq(layer)
            & schedule["signal_variant"].eq("binary_notional1x")
        ][["execution_date", "three_tier_target_fraction"]]
        prior = v19_schedule[
            v19_schedule["layer"].eq(layer)
            & v19_schedule["signal_variant"].eq("median200_or_mom120")
        ][["execution_date", "three_tier_target_fraction"]]
        joined = current.merge(
            prior,
            on="execution_date",
            suffixes=("_v20", "_v19"),
            validate="one_to_one",
        )
        rows.append(
            {
                "current_candidate": f"{layer}_binary_notional1x_schedule",
                "prior_candidate": f"{layer}_median200_or_mom120_schedule",
                "rows": len(joined),
                "max_abs_target_fraction_diff": float(
                    (
                        joined["three_tier_target_fraction_v20"]
                        - joined["three_tier_target_fraction_v19"]
                    )
                    .abs()
                    .max()
                ),
            }
        )
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].fillna(0.0).to_numpy().max() > 1e-14:
        raise RuntimeError("v20/v19 baseline parity failed")
    return table


def contract_selection_audit(
    trades: pd.DataFrame, frames: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    snapshots = frames["snapshots"]
    history_lookup = frames["histories"].set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    opening = {
        "open_buy",
        "open_roll_monthly",
        "close_buy",
        "close_roll_monthly",
    }
    selected = trades[
        trades["candidate"].str.startswith("real_")
        & trades["action"].isin(opening)
        & trades["new_contract"].fillna("").ne("")
    ]
    rows: list[dict[str, Any]] = []
    for trade in selected.itertuples(index=False):
        day = pd.Timestamp(trade.actual_execution_date)
        month = pd.Timestamp(trade.new_month)
        choice = v19.v18.v11.select_real_contract_target(
            snapshots,
            history_lookup,
            day,
            month,
            float(etf.loc[day, "close"]),
            MONEYNESS,
        )
        expected_contract = str(choice[0]["contract_id"]) if choice is not None else ""
        expected_security = str(choice[0]["security_id"]) if choice is not None else ""
        actual = str(trade.new_contract)
        rows.append(
            {
                "candidate": trade.candidate,
                "actual_execution_date": day,
                "action": trade.action,
                "target_moneyness": MONEYNESS,
                "actual_moneyness": float(trade.new_entry_moneyness),
                "absolute_target_error": abs(
                    float(trade.new_entry_moneyness) - MONEYNESS
                ),
                "expected_contract_id": expected_contract,
                "expected_security_id": expected_security,
                "actual_contract": actual,
                "nearest_contract_match": actual
                in {expected_contract, expected_security},
            }
        )
    table = pd.DataFrame(rows)
    if table.empty or not table["nearest_contract_match"].all():
        raise RuntimeError("v20 real contract selection audit failed")
    return table


def metrics(returns: pd.Series) -> dict[str, float]:
    return v19.metrics(returns)


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        group = group.sort_values("date")
        parts = candidate_parts(candidate)
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else END - offset
            available = bool(offset is None or group["date"].min() <= requested)
            subset = group if offset is None else group[group["date"] >= requested]
            row: dict[str, Any] = {
                "candidate": candidate,
                **parts,
                "window": window,
                "available": available,
                "requested_start": requested,
                "actual_start": subset["date"].min() if available else pd.NaT,
                "end": subset["date"].max() if available else pd.NaT,
                "rows": len(subset) if available else 0,
            }
            row.update(
                metrics(subset["cash_ret"])
                if available
                else {
                    "total_return": np.nan,
                    "ann_return": np.nan,
                    "ann_vol": np.nan,
                    "sharpe_repo": np.nan,
                    "max_dd": np.nan,
                }
            )
            rows.append(row)
    table = pd.DataFrame(rows)
    for baseline_variant, prefix in [
        ("no_put", "no_put"),
        ("binary_notional1x", "binary_notional"),
        ("binary_delta25", "binary_delta"),
    ]:
        baseline = table[table["variant"].eq(baseline_variant)][
            ["layer", "window", "ann_return", "max_dd"]
        ].rename(
            columns={
                "ann_return": f"{prefix}_ann_return",
                "max_dd": f"{prefix}_max_dd",
            }
        )
        table = table.merge(baseline, on=["layer", "window"], validate="many_to_one")
        table[f"ann_return_delta_vs_{prefix}"] = (
            table["ann_return"] - table[f"{prefix}_ann_return"]
        )
        table[f"max_dd_improvement_vs_{prefix}"] = (
            table["max_dd"] - table[f"{prefix}_max_dd"]
        )
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in table.groupby("candidate", sort=True):
        row = {"candidate": candidate, **candidate_parts(candidate)}
        for metric_row in group.itertuples(index=False):
            window = metric_row.window
            for field in ["ann_return", "ann_vol", "sharpe_repo", "max_dd"]:
                row[f"{field}_{window}"] = getattr(metric_row, field)
        wide_rows.append(row)
    return table, pd.DataFrame(wide_rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (candidate, year), group in daily.groupby(
        ["candidate", daily["date"].dt.year], sort=True
    ):
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "year": int(year),
                "rows": len(group),
                **metrics(group.sort_values("date")["cash_ret"]),
            }
        )
    return pd.DataFrame(rows)


def exposure_diagnostics(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=True):
        trade = trades[trades["candidate"].eq(candidate)]
        active = group[group["put_qty"].astype(float).gt(0)]
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "protected_days": len(active),
                "protected_day_ratio": float(len(active) / len(group)),
                "tier1_days": int(group["risk_tier"].eq(1).sum()),
                "tier2_days": int(group["risk_tier"].eq(2).sum()),
                "tier3_days": int(group["risk_tier"].eq(3).sum()),
                "trade_events": len(trade),
                "resize_events": int(trade["action"].eq("close_resize").sum()),
                "monthly_roll_events": int(
                    trade["action"].eq("close_roll_monthly").sum()
                ),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "average_put_mark_fraction": float(group["put_mark_fraction"].mean()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "average_actual_notional_when_active": float(
                    active["actual_notional_fraction"].mean()
                )
                if len(active)
                else 0.0,
                "max_actual_notional_fraction": float(
                    group["actual_notional_fraction"].max()
                ),
                "average_effective_delta_when_active": float(
                    active["effective_delta_hedge_ratio"].mean()
                )
                if len(active)
                else 0.0,
                "max_effective_delta_hedge_ratio": float(
                    group["effective_delta_hedge_ratio"].max()
                ),
                "days_effective_delta_over_100pct": int(
                    group["effective_delta_hedge_ratio"].gt(1.0).sum()
                ),
                "deferred_days": int(group["deferred_adjustment"].sum()),
                "carried_mark_days": int(group["carried_mark"].sum()),
                "max_mark_stale_days": int(group["mark_stale_days"].max()),
            }
        )
    return pd.DataFrame(rows)


def tier_activity(schedule: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, variant), group in schedule.groupby(
        ["layer", "signal_variant"], sort=True
    ):
        group = group.sort_values("execution_date")
        transitions = group["risk_tier"].ne(group["risk_tier"].shift())
        for tier in range(4):
            subset = group[group["risk_tier"].eq(tier)]
            rows.append(
                {
                    "layer": layer,
                    "variant": variant,
                    "risk_tier": tier,
                    "evaluation_days": len(subset),
                    "evaluation_ratio": float(len(subset) / len(group)),
                    "transition_entries": int(
                        (transitions & group["risk_tier"].eq(tier)).sum()
                    ),
                    "first_eval_date": subset["eval_date"].min()
                    if len(subset)
                    else pd.NaT,
                    "last_eval_date": subset["eval_date"].max()
                    if len(subset)
                    else pd.NaT,
                }
            )
    return pd.DataFrame(rows)


def delta_trade_diagnostics(trades: pd.DataFrame) -> pd.DataFrame:
    selected = trades[trades["candidate"].str.contains("_delta")].copy()
    selected = selected[selected["target_delta"].fillna(0).gt(0)]
    return selected[
        [
            "candidate",
            "actual_execution_date",
            "action",
            "risk_tier",
            "target_delta",
            "entry_abs_delta",
            "target_delta_error",
            "old_notional_fraction",
            "new_notional_fraction",
            "delta_source",
            "new_contract",
            "new_strike",
            "new_entry_moneyness",
        ]
    ].reset_index(drop=True)


def candidate_decisions(
    metric_table: pd.DataFrame,
    exposure: pd.DataFrame,
    delta_trades: pd.DataFrame,
    daily: pd.DataFrame,
) -> pd.DataFrame:
    pairs = [
        ("tier_notional123", "binary_notional1x", "notional"),
        ("tier_delta255075", "binary_delta25", "delta"),
    ]
    return_tolerance = {
        "full": 0.02,
        "last_10y": 0.02,
        "last_5y": 0.02,
        "last_3y": 0.04,
        "last_1y": 0.04,
    }
    rows: list[dict[str, Any]] = []
    for variant, control, method in pairs:
        result: dict[str, Any] = {
            "variant": variant,
            "control": control,
            "sizing_method": method,
        }
        for layer in ("model", "real"):
            part = metric_table[
                metric_table["layer"].eq(layer)
                & metric_table["variant"].eq(variant)
            ].set_index("window")
            windows = list(WINDOWS) if layer == "model" else ["full", "last_3y", "last_1y"]
            control_prefix = "binary_notional" if method == "notional" else "binary_delta"
            cagr_deltas = {
                window: float(part.loc[window, f"ann_return_delta_vs_{control_prefix}"])
                for window in windows
            }
            dd_deltas = {
                window: float(part.loc[window, f"max_dd_improvement_vs_{control_prefix}"])
                for window in windows
            }
            full_dd = dd_deltas["full"] >= 0.02 - 1e-12
            dd_count = sum(value > 1e-12 for value in dd_deltas.values())
            dd_breadth = dd_count >= (3 if layer == "model" else 2)
            dd_floor = all(value >= -0.01 - 1e-12 for value in dd_deltas.values())
            return_pass = all(
                cagr_deltas[window] >= -return_tolerance[window] - 1e-12
                for window in windows
            )
            no_put_return = all(
                float(part.loc[window, "ann_return_delta_vs_no_put"])
                >= -return_tolerance[window] - 1e-12
                for window in windows
            )
            no_put_dd = float(part.loc["full", "max_dd_improvement_vs_no_put"]) > 1e-12
            exp = exposure[
                exposure["layer"].eq(layer) & exposure["variant"].eq(variant)
            ].iloc[0]
            activity = bool(
                int(exp["tier2_days"]) >= (20 if layer == "model" else 1)
                and int(exp["tier3_days"]) >= (20 if layer == "model" else 1)
            )
            capital = bool(float(exp["max_put_mark_fraction"]) <= 0.70 + 1e-12)
            returns_valid = bool(
                daily[daily["candidate"].eq(f"{layer}_{variant}")]["cash_ret"].min()
                > -1.0
            )
            if method == "delta":
                trade = delta_trades[
                    delta_trades["candidate"].eq(f"{layer}_{variant}")
                ]
                delta_error = bool(
                    len(trade)
                    and float(trade["target_delta_error"].max())
                    <= (1e-12 if layer == "model" else 0.02) + 1e-12
                )
                fallback_ratio = float(
                    trade["delta_source"].eq("qvix_proxy_fallback").mean()
                )
                fallback_pass = bool(layer == "model" or fallback_ratio <= 0.05 + 1e-12)
            else:
                delta_error = True
                fallback_ratio = 0.0
                fallback_pass = True
            method_pass = bool(
                full_dd
                and dd_breadth
                and dd_floor
                and return_pass
                and no_put_return
                and no_put_dd
                and activity
                and capital
                and returns_valid
                and delta_error
                and fallback_pass
            )
            result.update(
                {
                    f"{layer}_full_dd_2pp_pass": full_dd,
                    f"{layer}_dd_windows_improved": dd_count,
                    f"{layer}_dd_breadth_pass": dd_breadth,
                    f"{layer}_dd_floor_pass": dd_floor,
                    f"{layer}_return_tolerance_pass": return_pass,
                    f"{layer}_no_put_return_pass": no_put_return,
                    f"{layer}_no_put_dd_pass": no_put_dd,
                    f"{layer}_activity_pass": activity,
                    f"{layer}_capital_pass": capital,
                    f"{layer}_returns_valid": returns_valid,
                    f"{layer}_delta_error_pass": delta_error,
                    f"{layer}_iv_fallback_ratio": fallback_ratio,
                    f"{layer}_iv_fallback_pass": fallback_pass,
                    f"{layer}_method_pass": method_pass,
                    f"{layer}_full_cagr_delta_vs_control": cagr_deltas["full"],
                    f"{layer}_full_dd_improvement_vs_control": dd_deltas["full"],
                }
            )
        result["both_layers_pass"] = bool(
            result["model_method_pass"] and result["real_method_pass"]
        )
        rows.append(result)
    return pd.DataFrame(rows)


def decision_summary(decisions: pd.DataFrame, exposure: pd.DataFrame) -> dict[str, Any]:
    passing = decisions.loc[decisions["both_layers_pass"], "sizing_method"].tolist()
    real_upper = exposure[
        exposure["candidate"].eq("real_tier_notional123")
    ].iloc[0]
    upper_days = int(real_upper["tier2_days"] + real_upper["tier3_days"])
    if len(passing) == 2:
        decision = "cross_method_supported_watchlist"
        stability = "cross_method_supported"
    elif len(passing) == 1:
        decision = "method_specific_watchlist"
        stability = "method_specific"
    else:
        decision = "reject_tiering_keep_binary_notional1x"
        stability = "rejected"
    return {
        "decision": decision,
        "stability_label": stability,
        "passing_methods": passing,
        "real_tier2_plus_tier3_days": upper_days,
        "real_upper_tier_sample_sufficient_for_live": upper_days >= 20,
        "selected_variant": None,
        "carried_research_baseline": "binary_notional1x",
        "promotion_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "sample_reuse": "not_independent_oos",
    }


def check_integrity(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    delta_trades: pd.DataFrame,
) -> dict[str, Any]:
    expected = {
        f"{layer}_{variant}" for layer in ("model", "real") for variant in VARIANTS
    }
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("v20 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v20 candidate/date")
    if daily[["ret", "cash_ret"]].isna().any().any():
        raise RuntimeError("Missing v20 return")
    if (daily[["ret", "cash_ret"]] <= -1.0).any().any():
        raise RuntimeError("Invalid v20 return <= -100%")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes request")
    max_delay = int(trades["delay_trading_days"].fillna(0).max())
    if max_delay > 5:
        raise RuntimeError("Real execution delay exceeded five trading days")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Signal execution leakage")
    parity_columns = [column for column in parity if column.startswith("max_abs_")]
    parity_max = float(parity[parity_columns].fillna(0.0).to_numpy().max())
    if parity_max > 1e-14:
        raise RuntimeError("Baseline parity exceeded tolerance")
    if contract_audit.empty or not contract_audit["nearest_contract_match"].all():
        raise RuntimeError("Contract selection audit failed")
    if close_audit.empty or not close_audit["passed"].all():
        raise RuntimeError("Close execution audit failed")
    model_delta = delta_trades[delta_trades["candidate"].str.startswith("model_")]
    real_delta = delta_trades[delta_trades["candidate"].str.startswith("real_")]
    model_error = float(model_delta["target_delta_error"].max())
    real_error = float(real_delta["target_delta_error"].max())
    if model_error > 1e-12:
        raise RuntimeError("Model Delta sizing identity failed")
    if real_error > 0.02 + 1e-12:
        raise RuntimeError("Real Delta integer-sizing tolerance failed")
    active = daily[daily["put_qty"].astype(float).gt(0)]
    if active[["actual_notional_fraction", "abs_put_delta", "effective_delta_hedge_ratio"]].isna().any().any():
        raise RuntimeError("Missing active-position Delta diagnostic")
    return {
        "candidate_count": len(expected),
        "daily_rows": len(daily),
        "trade_rows": len(trades),
        "schedule_rows": len(schedule),
        "parity_max_abs": parity_max,
        "max_execution_delay_trading_days": max_delay,
        "model_max_target_delta_error": model_error,
        "real_max_target_delta_error": real_error,
        "real_delta_iv_fallback_ratio": float(
            real_delta["delta_source"].eq("qvix_proxy_fallback").mean()
        ),
        "max_put_mark_fraction": float(daily["put_mark_fraction"].max()),
        "max_actual_notional_fraction": float(
            daily["actual_notional_fraction"].max()
        ),
        "max_effective_delta_hedge_ratio": float(
            daily["effective_delta_hedge_ratio"].max()
        ),
        "all_checks_passed": True,
    }


def _fmt(value: Any) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def build_record(
    metric_table: pd.DataFrame,
    exposure: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
) -> str:
    lines = [
        f"# {VERSION} 正式记录",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "- 状态：研究回测，未批准实盘。",
        "- 信号：v6二取三中位数2.00/2.10/2.15三级；MOM120<=0只提供最低1档。",
        "- 工具：3个月、95% Put，随IC月换；T收盘评估、T+1共同交易日收盘执行。",
        "- 比较：固定名义1/2/3倍与交易时Delta 25%/50%/75%，各自带二元控制组。",
        "",
        "## 窗口结果",
        "",
        "| 层 | 候选 | 窗口 | CAGR | MaxDD |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for row in metric_table[metric_table["available"]].itertuples(index=False):
        lines.append(
            f"| {row.layer} | `{row.variant}` | {row.window} | {_fmt(row.ann_return)} | {_fmt(row.max_dd)} |"
        )
    lines.extend(
        [
            "",
            "## 分档判定",
            "",
            decisions.to_markdown(index=False),
            "",
            "## 资本与Delta诊断",
            "",
            exposure.to_markdown(index=False),
            "",
            "## 结论",
            "",
            f"- 判定：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
            f"- 真实2档+3档仅{summary['real_tier2_plus_tier3_days']}日；是否足够实盘：{summary['real_upper_tier_sample_sufficient_for_live']}。",
            f"- 全局最大Put市值：{_fmt(integrity['max_put_mark_fraction'])}；最大所需名义倍数：{integrity['max_actual_notional_fraction']:.2f}。",
            "- 结果重复使用既有样本，不是独立样本外验证，不是交易指令。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    schedule: pd.DataFrame,
    signals: pd.DataFrame,
    metric_table: pd.DataFrame,
    wide: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    tiers: pd.DataFrame,
    delta_trades: pd.DataFrame,
    parity: pd.DataFrame,
    contract_audit: pd.DataFrame,
    close_audit: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, Any],
    integrity: dict[str, Any],
    upstream: dict[str, Any],
    checks: dict[str, Any],
    signal_checks: dict[str, Any],
    git_before: str,
) -> None:
    OUTPUT.mkdir(parents=False, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "signal_history.csv.gz", index=False, compression="gzip")
    metric_table.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    wide.to_csv(OUTPUT / "window_metrics_wide.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_delta.csv", index=False)
    tiers.to_csv(OUTPUT / "tier_activity.csv", index=False)
    delta_trades.to_csv(OUTPUT / "delta_trade_diagnostics.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    contract_audit.to_csv(OUTPUT / "real_contract_selection_audit.csv", index=False)
    close_audit.to_csv(OUTPUT / "close_price_integrity_audit.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    record = build_record(metric_table, exposure, decisions, summary, integrity)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    command = "uv run ic_510500_put_tiered_notional_delta_v20.py"
    (OUTPUT / "command_log.txt").write_text(command + "\n", encoding="utf-8")

    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script_sha256": sha256(Path(__file__)),
        "spec_sha256": SPEC_SHA256,
        "input_hashes": {
            str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()
        },
        "upstream_verification": upstream,
        "sample": {
            "valuation_start": "2007-01-15",
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "execution": {
            "signal": "T close",
            "trade": "next common trading day close",
            "put": "3m monthly exit, target 95% strike/spot",
            "delta_rebalance": "entry, monthly roll, or tier change only",
        },
        "capital_and_cost": {
            "ic_notional": 1.0,
            "margin_and_buffer": 0.3,
            "cash_weight_before_put_premium": 0.7,
            "cash_yield": 0.03,
            "ic_and_put_side_cost": PUT_SIDE_COST,
        },
        "risk_tiers": {"thresholds": [2.00, 2.10, 2.15], "mom120_floor": 1},
        "candidates": list(VARIANTS),
        "checks": {
            "upstream_market": checks,
            "signal_inputs": signal_checks,
            "integrity": integrity,
        },
        "decision": summary,
        "warnings": [
            "No independent OOS",
            "Model Put is theoretical",
            "Daily close is not a closing-auction fill or capacity guarantee",
            "Real upper-tier sample is very short",
            "Uncapped Delta target may require more than three notional sets",
            "Research state is not an order",
        ],
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    output_hashes = {
        path.name: sha256(path)
        for path in sorted(OUTPUT.iterdir())
        if path.name != "output_manifest.json"
    }
    (OUTPUT / "output_manifest.json").write_text(
        json.dumps(output_hashes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    model_long = metric_table[
        metric_table["layer"].eq("model") & metric_table["available"]
    ].copy()
    model_long = model_long.rename(columns={"window": "segment"})
    model_long.to_csv(SCAN / "scan_summary.csv", index=False)
    model_wide = wide[wide["layer"].eq("model")].copy()
    model_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command + "\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "candidate_bundle",
            "baseline": {
                "candidate": "model_binary_notional1x",
                "same_run": True,
            },
            "candidate_grid": list(VARIANTS),
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["capital_and_cost"],
            "execution": manifest["execution"],
            "source_hashes": manifest["input_hashes"],
            "parity_check": integrity["parity_max_abs"],
            "formal_output": str(OUTPUT.relative_to(ROOT)),
            "outputs": {
                "record": str((SCAN / "record.md").resolve()),
                "scan_summary": str((SCAN / "scan_summary.csv").resolve()),
                "window_metrics": str((SCAN / "window_metrics.csv").resolve()),
                "scan_meta": str(meta_path.resolve()),
                "command_log": str((SCAN / "command_log.txt").resolve()),
            },
            "git_status_before": git_before,
            "git_status_after": git_status(),
            "warnings": manifest["warnings"],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    git_before = git_status()
    upstream = verify_inputs()
    frames, daily_valuation, market, checks = v19.v18.load_close_inputs()
    signal_inputs, signal_checks = v19.v18.build_signal_inputs(daily_valuation)
    schedule, signals = build_schedules(frames["ic"], daily_valuation, signal_inputs)
    daily, trades = run_candidates(frames, market, schedule)
    parity = baseline_parity(daily, schedule)
    contract_audit = contract_selection_audit(trades, frames)
    close_audit = v19.v18.close_price_audit(trades, frames)
    metric_table, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_diagnostics(daily, trades)
    tiers = tier_activity(schedule)
    delta_trades = delta_trade_diagnostics(trades)
    decisions = candidate_decisions(metric_table, exposure, delta_trades, daily)
    summary = decision_summary(decisions, exposure)
    integrity = check_integrity(
        daily,
        trades,
        schedule,
        parity,
        contract_audit,
        close_audit,
        delta_trades,
    )
    write_outputs(
        daily,
        trades,
        schedule,
        signals,
        metric_table,
        wide,
        annual,
        exposure,
        tiers,
        delta_trades,
        parity,
        contract_audit,
        close_audit,
        decisions,
        summary,
        integrity,
        upstream,
        checks,
        signal_checks,
        git_before,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
