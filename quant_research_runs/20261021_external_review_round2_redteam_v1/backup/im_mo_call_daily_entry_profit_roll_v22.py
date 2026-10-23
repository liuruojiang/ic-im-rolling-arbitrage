from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_overwrite_delta_tenor_v19 as v19


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_daily_entry_profit_roll_v22"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "051e6e5898add6a588af4718f8ea34fdaa91505dfc014e7f5291e9514c6241bb"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_mo_call_daily_entry_profit_roll_v22"
V20_OUTPUT = ROOT / "outputs" / "im_mo_call_iv_entry_gate_v20"
V20_DAILY = V20_OUTPUT / "daily_candidates.csv.gz"
V20_TRADES = V20_OUTPUT / "call_trades.csv"

BASELINE = "base_core_put"
MONTHLY = "front_d10_iv26"
DAILY = "front_d10_iv26_daily"
DAILY_TP80 = "front_d10_iv26_daily_tp80"
CANDIDATES = (DAILY, DAILY_TP80)
IV_THRESHOLD = 0.26
TARGET_DELTA = 0.10
TP_REMAINING_RATIO = 0.20

FROZEN_HASHES = {
    ROOT / "im_mo_call_overwrite_delta_tenor_v19.py": "22f5b2fadfd421fa6be0f1680df3c4c6ac04eecb97cca39b6439e11ab8be7920",
    ROOT / "im_mo_call_iv_entry_gate_v20.py": "8e624b0cfe6aaf017b6435b4edbd524238f914c6adc45bf5af0f6add7dcfda6f",
    ROOT / "docs" / "im_mo_call_iv_entry_gate_v20_spec.md": "a2587db43e175cc046990327516b3660fa01032fe9e527f59b79d07675d581c5",
    V20_DAILY: "c0467e8a7b745456e3cb056d1e7fb62bd942fd3fa11847a0456172188a79505e",
    V20_TRADES: "b7a6f099e1fe6197dab020d5876289f73a7a4887de38f7967f25a7eb3a023c3c",
    V20_OUTPUT / "data_manifest.json": "c2a7af1a218992fee443c26e4847fdb5e0d010b90d7489a32e7cbd0749b4416d",
    V20_OUTPUT / "output_manifest.json": "c07aad2cb21af506046b33f35d8eed56ef46b542a2d9048fa6856169cc8c09a3",
    ROOT / "data" / "im_mo_call_data_build_v1" / "cffex_mo_calls.csv": "3c5bd3f5b4ca057a87fa8e0c0d1600980d773125b207b7d2c858500d2927f4c0",
}


@dataclass
class Pending:
    eval_date: pd.Timestamp
    scheduled_execution_date: pd.Timestamp
    action: str
    reason: str
    selection: v19.Selection | None
    gate_iv: float
    gate_pass: bool
    remaining_ratio: float
    old_expiry: pd.Timestamp | None


@dataclass
class ModelActive:
    selection: v19.Selection
    units: float
    prior_mark: float
    entry_close: float
    cycle_id: int


@dataclass
class RealActive:
    selection: v19.Selection
    qty: int
    prior_settle: float
    entry_close: float
    cycle_id: int


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


def verify_inputs() -> None:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v22 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v22 specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging v22 output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v22 parameter folder is missing")
    for path, expected in FROZEN_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v22 input changed: {path}")


def reference_daily() -> pd.DataFrame:
    source = pd.read_csv(V20_DAILY, parse_dates=["date", "call_expiry"])
    keep = source[source["candidate"].isin([BASELINE, MONTHLY])].copy()
    for layer, expected_rows in [("model", 2 * 2756), ("real", 2 * 986)]:
        if len(keep[keep["layer"].eq(layer)]) != expected_rows:
            raise RuntimeError(f"Unexpected v20 reference rows: {layer}")
    return keep.sort_values(["layer", "candidate", "date"]).reset_index(drop=True)


def next_day_map(dates: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.Timestamp]:
    return {pd.Timestamp(dates[i]): pd.Timestamp(dates[i + 1]) for i in range(len(dates) - 1)}


def model_selection_for_expiry(
    row: pd.Series,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    month: pd.Timestamp,
    expiry: pd.Timestamp,
    label: str,
    reason: str,
) -> v19.Selection:
    years = (expiry - day).days / 365.0
    if years <= 0:
        raise RuntimeError(f"Non-positive model option life on {day.date()}")
    spot = float(row["spot_close"])
    sigma = float(row["sigma_close"])
    strike = v19.strike_for_delta(
        spot,
        float(row["rate_close"]),
        float(row["dividend_close"]),
        sigma,
        years,
        TARGET_DELTA,
    )
    close = v19.bs_call(
        spot,
        strike,
        float(row["rate_close"]),
        float(row["dividend_close"]),
        sigma,
        years,
    )
    return v19.Selection(
        "model",
        label,
        "front",
        TARGET_DELTA,
        reason,
        day,
        execution,
        f"MODEL_{month:%y%m}_{strike:.6f}",
        month,
        expiry,
        strike,
        TARGET_DELTA,
        sigma,
        close,
        np.nan,
        np.nan,
    )


def real_selection_for_expiry(
    calls: pd.DataFrame,
    market_row: pd.Series,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    month: pd.Timestamp,
    expiry: pd.Timestamp,
    label: str,
    reason: str,
) -> v19.Selection | None:
    spot = float(market_row["spot_close"])
    chain = calls[
        calls["date"].eq(day)
        & calls["contract_month"].eq(month)
        & calls["actual_expiry"].eq(expiry)
        & calls["strike"].gt(spot)
        & calls["close"].gt(0)
        & calls["volume"].gt(0)
        & calls["open_interest"].gt(0)
    ]
    choices: list[dict[str, Any]] = []
    for quote in chain.itertuples(index=False):
        years = (pd.Timestamp(quote.actual_expiry) - day).days / 365.0
        if years <= 0:
            continue
        iv = v19.implied_volatility(
            float(quote.close),
            spot,
            float(quote.strike),
            float(market_row["rate_close"]),
            float(market_row["dividend_close"]),
            years,
        )
        if iv is None:
            continue
        delta = v19.bs_call_delta(
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
                "error": abs(delta - TARGET_DELTA),
            }
        )
    if not choices:
        return None
    chosen = sorted(
        choices,
        key=lambda item: (
            item["error"],
            item["delta"],
            item["quote"].strike,
            item["quote"].contract,
        ),
    )[0]
    quote = chosen["quote"]
    return v19.Selection(
        "real",
        label,
        "front",
        TARGET_DELTA,
        reason,
        day,
        execution,
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


def model_far_selection(
    market: pd.DataFrame,
    dates: pd.DatetimeIndex,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    active_expiry: pd.Timestamp,
    label: str,
) -> v19.Selection | None:
    listed = v19.v6.model_listed_months(day, dates)
    choices = sorted(
        (v19.rule_expiry(month, dates), pd.Timestamp(month))
        for month in listed
        if v19.rule_expiry(month, dates) > active_expiry
    )
    if not choices:
        return None
    expiry, month = choices[0]
    row = market.set_index("date").loc[day]
    return model_selection_for_expiry(
        row, day, execution, month, expiry, label, "tp80"
    )


def real_far_selection(
    calls: pd.DataFrame,
    market_row: pd.Series,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    active_expiry: pd.Timestamp,
    label: str,
) -> v19.Selection | None:
    available = calls[
        calls["date"].eq(day) & calls["actual_expiry"].gt(active_expiry)
    ][["contract_month", "actual_expiry"]].drop_duplicates()
    for item in available.sort_values(["actual_expiry", "contract_month"]).itertuples(index=False):
        selection = real_selection_for_expiry(
            calls,
            market_row,
            day,
            execution,
            pd.Timestamp(item.contract_month),
            pd.Timestamp(item.actual_expiry),
            label,
            "tp80",
        )
        if selection is not None:
            return selection
    return None


def pending_from_selection(
    active: bool,
    selection: v19.Selection | None,
    reason: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    remaining_ratio: float = np.nan,
    old_expiry: pd.Timestamp | None = None,
) -> Pending | None:
    gate_iv = float(selection.implied_vol) if selection is not None else np.nan
    gate_pass = bool(selection is not None and gate_iv >= IV_THRESHOLD - 1e-12)
    if active and gate_pass:
        action = "roll"
    elif active:
        action = "close"
    elif gate_pass:
        action = "open"
    else:
        return None
    return Pending(
        day,
        execution,
        action,
        reason,
        selection if gate_pass else None,
        gate_iv,
        gate_pass,
        remaining_ratio,
        old_expiry,
    )


def signal_row(
    layer: str,
    label: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    reason: str,
    active: bool,
    selection: v19.Selection | None,
    action: str,
    remaining_ratio: float = np.nan,
    old_expiry: pd.Timestamp | None = None,
) -> dict[str, Any]:
    iv = float(selection.implied_vol) if selection is not None else np.nan
    gate = bool(selection is not None and iv >= IV_THRESHOLD - 1e-12)
    return {
        "layer": layer,
        "candidate": label,
        "eval_date": day,
        "scheduled_execution_date": execution,
        "reason": reason,
        "had_old_position": active,
        "action": action,
        "contract": selection.contract if selection is not None else "",
        "gate_iv": iv,
        "iv_threshold": IV_THRESHOLD,
        "gate_pass": gate,
        "selection_delta": selection.selected_delta if selection is not None else np.nan,
        "selection_expiry": selection.expiry if selection is not None else pd.NaT,
        "selection_strike": selection.strike if selection is not None else np.nan,
        "eval_close": selection.eval_close if selection is not None else np.nan,
        "eval_volume": selection.eval_volume if selection is not None else np.nan,
        "eval_open_interest": selection.eval_open_interest if selection is not None else np.nan,
        "remaining_price_ratio": remaining_ratio,
        "old_expiry": old_expiry if old_expiry is not None else pd.NaT,
    }


def run_model(
    market: pd.DataFrame,
    events: pd.DataFrame,
    monthly_selections: list[v19.Selection],
    label: str,
    tp_enabled: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    dates = pd.DatetimeIndex(market["date"])
    next_days = next_day_map(dates)
    event_lookup = events.set_index("eval_date")
    monthly_lookup = {item.eval_date: item for item in monthly_selections}
    market_lookup = market.set_index("date")
    active: ModelActive | None = None
    pending: Pending | None = None
    cycle_month: pd.Timestamp | None = None
    cycle_expiry: pd.Timestamp | None = None
    cycle_id = 0
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    tp_opportunities = tp_iv_failures = 0
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        denominator = float(row.base_prior_close)
        pnl = 0.0
        cost = 0.0
        traded = False
        old_mark = np.nan
        if active is not None:
            old_mark, _ = v19.model_mark(
                v19.ModelPosition(active.selection, active.units, active.prior_mark, active.cycle_id),
                row,
            )
            pnl = -active.units * (old_mark - active.prior_mark) / denominator
            active.prior_mark = old_mark
        if pending is not None and day >= pending.scheduled_execution_date:
            old = active
            if old is not None:
                cost += v19.CALL_BASKET_SIDE_COST
            active = None
            entry_mark = np.nan
            if pending.selection is not None:
                units = denominator / float(row.spot_close)
                shell = v19.ModelPosition(pending.selection, units, 0.0, cycle_id + 1)
                entry_mark, _ = v19.model_mark(shell, row)
                cycle_id += 1
                active = ModelActive(
                    pending.selection, units, entry_mark, entry_mark, cycle_id
                )
                cost += v19.CALL_BASKET_SIDE_COST
            trades.append(
                {
                    "layer": "model",
                    "candidate": label,
                    "eval_date": pending.eval_date,
                    "scheduled_execution_date": pending.scheduled_execution_date,
                    "actual_execution_date": day,
                    "action": pending.action,
                    "reason": pending.reason,
                    "old_contract": old.selection.contract if old is not None else "",
                    "new_contract": pending.selection.contract if pending.selection is not None else "",
                    "old_expiry": old.selection.expiry if old is not None else pd.NaT,
                    "new_expiry": pending.selection.expiry if pending.selection is not None else pd.NaT,
                    "gate_iv": pending.gate_iv,
                    "gate_pass": pending.gate_pass,
                    "remaining_price_ratio": pending.remaining_ratio,
                    "old_entry_close": old.entry_close if old is not None else np.nan,
                    "old_close": old_mark,
                    "new_close": entry_mark,
                    "new_settle": entry_mark,
                    "delay_trading_days": 0,
                    "cycle_id": cycle_id if active is not None else 0,
                }
            )
            pending = None
            traded = True
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if event is not None:
            monthly = monthly_lookup[day]
            cycle_month = monthly.month
            cycle_expiry = monthly.expiry
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            if event is not None:
                must_roll = active is None or active.selection.expiry <= pd.Timestamp(event.current_expiry)
                if must_roll:
                    monthly = monthly_lookup[day]
                    proposed = monthly
                    temp = pending_from_selection(
                        active is not None,
                        proposed,
                        "monthly",
                        day,
                        tomorrow,
                        old_expiry=active.selection.expiry if active is not None else None,
                    )
                    gate = proposed.implied_vol >= IV_THRESHOLD - 1e-12
                    action = (
                        "roll" if active is not None and gate else
                        "close" if active is not None else
                        "open" if gate else "skip"
                    )
                    signals.append(signal_row("model", label, day, tomorrow, "monthly", active is not None, proposed, action, old_expiry=active.selection.expiry if active is not None else None))
                    pending = temp
                else:
                    signals.append(signal_row("model", label, day, tomorrow, "monthly_keep_far", True, None, "keep_far", old_expiry=active.selection.expiry))
            elif active is None and cycle_month is not None and cycle_expiry is not None:
                proposed = model_selection_for_expiry(
                    market_lookup.loc[day], day, tomorrow, cycle_month, cycle_expiry, label, "daily_entry"
                )
                gate = proposed.implied_vol >= IV_THRESHOLD - 1e-12
                action = "open" if gate else "skip"
                signals.append(signal_row("model", label, day, tomorrow, "daily_entry", False, proposed, action))
                pending = pending_from_selection(False, proposed, "daily_entry", day, tomorrow)
            elif tp_enabled and active is not None:
                current_mark, _ = v19.model_mark(
                    v19.ModelPosition(active.selection, active.units, active.prior_mark, active.cycle_id),
                    row,
                )
                ratio = current_mark / active.entry_close if active.entry_close > 0 else np.nan
                if ratio <= TP_REMAINING_RATIO + 1e-12:
                    tp_opportunities += 1
                    proposed = model_far_selection(
                        market, dates, day, tomorrow, active.selection.expiry, label
                    )
                    gate = bool(proposed is not None and proposed.implied_vol >= IV_THRESHOLD - 1e-12)
                    if not gate:
                        tp_iv_failures += 1
                    action = "roll" if gate else "hold"
                    signals.append(signal_row("model", label, day, tomorrow, "tp80", True, proposed, action, ratio, active.selection.expiry))
                    if gate:
                        pending = pending_from_selection(True, proposed, "tp80", day, tomorrow, ratio, active.selection.expiry)
        mark_fraction = margin_fraction = coverage = 0.0
        call_delta = np.nan
        contract = ""
        strike = np.nan
        expiry = pd.NaT
        itm = False
        active_cycle = 0
        entry_close = np.nan
        if active is not None:
            mark, call_delta = v19.model_mark(
                v19.ModelPosition(active.selection, active.units, active.prior_mark, active.cycle_id),
                row,
            )
            active.prior_mark = mark
            coverage = active.units * float(row.spot_close) / float(row.tri_close)
            mark_fraction = active.units * mark / float(row.tri_close)
            margin_fraction = v19.call_margin_fraction(mark, float(row.spot_close), active.selection.strike, active.units, float(row.tri_close))
            contract = active.selection.contract
            strike = active.selection.strike
            expiry = active.selection.expiry
            itm = float(row.spot_close) > strike
            active_cycle = active.cycle_id
            entry_close = active.entry_close
            if expiry <= day:
                raise RuntimeError(f"Model daily Call reached expiry: {label} {day.date()}")
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
                "call_entry_close": entry_close,
            }
        )
    if pending is not None:
        raise RuntimeError(f"Unexecuted final model action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), {
        "scheduled_execution_failures": 0,
        "delayed_trading_days": 0,
        "tp_opportunities": tp_opportunities,
        "tp_iv_or_far_failures": tp_iv_failures,
    }


def run_real(
    upstream: pd.DataFrame,
    calls: pd.DataFrame,
    market: pd.DataFrame,
    events: pd.DataFrame,
    monthly_selections: list[v19.Selection],
    label: str,
    tp_enabled: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    dates = pd.DatetimeIndex(upstream["date"])
    next_days = next_day_map(dates)
    event_lookup = events.set_index("eval_date")
    monthly_lookup = {item.eval_date: item for item in monthly_selections}
    market_lookup = market.set_index("date")
    call_lookup = calls.set_index(["contract", "date"])
    prior_im = upstream["settle"].shift(1)
    prior_im.iloc[0] = upstream.iloc[0]["settle"]
    active: RealActive | None = None
    pending: Pending | None = None
    cycle_month: pd.Timestamp | None = None
    cycle_expiry: pd.Timestamp | None = None
    cycle_id = 0
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    scheduled_failures = delayed_days = 0
    tp_opportunities = tp_iv_failures = 0
    for index, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(prior_im.iloc[index])
        market_row = market_lookup.loc[day]
        pnl = 0.0
        cost = 0.0
        traded = False
        old_quote = v19.quote_row(call_lookup, active.selection.contract, day) if active is not None else None
        new_quote = v19.quote_row(call_lookup, pending.selection.contract, day) if pending is not None and pending.selection is not None else None
        old_tradable = active is None or (
            old_quote is not None
            and float(old_quote["close"]) > 0
            and float(old_quote["volume"]) > 0
            and float(old_quote["open_interest"]) > 0
        )
        new_tradable = pending is None or pending.selection is None or (
            new_quote is not None
            and float(new_quote["close"]) > 0
            and float(new_quote["volume"]) > 0
            and float(new_quote["open_interest"]) > 0
        )
        if pending is not None and day >= pending.scheduled_execution_date and old_tradable and new_tradable:
            old = active
            old_close = np.nan
            if old is not None:
                old_close = float(old_quote["close"])
                pnl += old.qty * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER * (old.prior_settle - old_close) / denominator
                cost += v19.CALL_BASKET_SIDE_COST
            active = None
            new_close = new_settle = np.nan
            if pending.selection is not None:
                new_close = float(new_quote["close"])
                new_settle = float(new_quote["settle"])
                pnl += v19.MO_QTY * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER * (new_close - new_settle) / denominator
                cost += v19.CALL_BASKET_SIDE_COST
                cycle_id += 1
                active = RealActive(
                    pending.selection,
                    v19.MO_QTY,
                    new_settle,
                    new_close,
                    cycle_id,
                )
            delay = int(((dates > pending.scheduled_execution_date) & (dates <= day)).sum())
            delayed_days += delay
            trades.append(
                {
                    "layer": "real",
                    "candidate": label,
                    "eval_date": pending.eval_date,
                    "scheduled_execution_date": pending.scheduled_execution_date,
                    "actual_execution_date": day,
                    "action": pending.action,
                    "reason": pending.reason,
                    "old_contract": old.selection.contract if old is not None else "",
                    "new_contract": pending.selection.contract if pending.selection is not None else "",
                    "old_expiry": old.selection.expiry if old is not None else pd.NaT,
                    "new_expiry": pending.selection.expiry if pending.selection is not None else pd.NaT,
                    "gate_iv": pending.gate_iv,
                    "gate_pass": pending.gate_pass,
                    "remaining_price_ratio": pending.remaining_ratio,
                    "old_entry_close": old.entry_close if old is not None else np.nan,
                    "old_close": old_close,
                    "new_close": new_close,
                    "new_settle": new_settle,
                    "delay_trading_days": delay,
                    "cycle_id": cycle_id if active is not None else 0,
                }
            )
            pending = None
            traded = True
        elif pending is not None and day == pending.scheduled_execution_date:
            scheduled_failures += 1
        if not traded and active is not None:
            if old_quote is None or float(old_quote["settle"]) <= 0:
                raise RuntimeError(f"Missing real daily settlement: {label} {day.date()}")
            pnl += active.qty * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER * (active.prior_settle - float(old_quote["settle"])) / denominator
            active.prior_settle = float(old_quote["settle"])
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if event is not None:
            monthly = monthly_lookup[day]
            cycle_month = monthly.month
            cycle_expiry = monthly.expiry
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            if event is not None:
                must_roll = active is None or active.selection.expiry <= pd.Timestamp(event.current_expiry)
                if must_roll:
                    proposed = monthly_lookup[day]
                    gate = proposed.implied_vol >= IV_THRESHOLD - 1e-12
                    action = (
                        "roll" if active is not None and gate else
                        "close" if active is not None else
                        "open" if gate else "skip"
                    )
                    signals.append(signal_row("real", label, day, tomorrow, "monthly", active is not None, proposed, action, old_expiry=active.selection.expiry if active is not None else None))
                    pending = pending_from_selection(active is not None, proposed, "monthly", day, tomorrow, old_expiry=active.selection.expiry if active is not None else None)
                else:
                    signals.append(signal_row("real", label, day, tomorrow, "monthly_keep_far", True, None, "keep_far", old_expiry=active.selection.expiry))
            elif active is None and cycle_month is not None and cycle_expiry is not None:
                proposed = real_selection_for_expiry(calls, market_row, day, tomorrow, cycle_month, cycle_expiry, label, "daily_entry")
                gate = bool(proposed is not None and proposed.implied_vol >= IV_THRESHOLD - 1e-12)
                action = "open" if gate else "skip"
                signals.append(signal_row("real", label, day, tomorrow, "daily_entry", False, proposed, action))
                pending = pending_from_selection(False, proposed, "daily_entry", day, tomorrow)
            elif tp_enabled and active is not None:
                quote = v19.quote_row(call_lookup, active.selection.contract, day)
                if quote is None or float(quote["close"]) <= 0:
                    raise RuntimeError(f"Missing real signal close: {label} {day.date()}")
                ratio = float(quote["close"]) / active.entry_close if active.entry_close > 0 else np.nan
                if ratio <= TP_REMAINING_RATIO + 1e-12:
                    tp_opportunities += 1
                    proposed = real_far_selection(calls, market_row, day, tomorrow, active.selection.expiry, label)
                    gate = bool(proposed is not None and proposed.implied_vol >= IV_THRESHOLD - 1e-12)
                    if not gate:
                        tp_iv_failures += 1
                    action = "roll" if gate else "hold"
                    signals.append(signal_row("real", label, day, tomorrow, "tp80", True, proposed, action, ratio, active.selection.expiry))
                    if gate:
                        pending = pending_from_selection(True, proposed, "tp80", day, tomorrow, ratio, active.selection.expiry)
        mark_fraction = margin_fraction = coverage = 0.0
        call_delta = np.nan
        contract = ""
        strike = np.nan
        expiry = pd.NaT
        itm = False
        active_cycle = 0
        entry_close = np.nan
        if active is not None:
            quote = v19.quote_row(call_lookup, active.selection.contract, day)
            if quote is None or float(quote["settle"]) <= 0:
                raise RuntimeError(f"Missing real EOD quote: {label} {day.date()}")
            active.prior_settle = float(quote["settle"])
            equivalent_units = active.qty * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER
            coverage = equivalent_units * float(market_row["spot_close"]) / float(base["settle"])
            mark_fraction = equivalent_units * float(quote["settle"]) / float(base["settle"])
            margin_fraction = v19.call_margin_fraction(float(quote["settle"]), float(market_row["spot_close"]), active.selection.strike, equivalent_units, float(base["settle"]))
            call_delta = v19.real_daily_delta(quote, active.selection, market_row, day)
            contract = active.selection.contract
            strike = active.selection.strike
            expiry = active.selection.expiry
            itm = float(market_row["spot_close"]) > strike
            active_cycle = active.cycle_id
            entry_close = active.entry_close
            if expiry <= day:
                raise RuntimeError(f"Real daily Call reached expiry: {label} {day.date()}")
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
                "call_entry_close": entry_close,
            }
        )
    if pending is not None:
        raise RuntimeError(f"Unexecuted final real action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), {
        "scheduled_execution_failures": scheduled_failures,
        "delayed_trading_days": delayed_days,
        "tp_opportunities": tp_opportunities,
        "tp_iv_or_far_failures": tp_iv_failures,
    }


def metric_value(formal: pd.DataFrame, layer: str, candidate: str, window: str, column: str) -> float:
    return v19.metric_value(formal, layer, candidate, window, column)


def event_summary(daily: pd.DataFrame, trades: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        candidate_trades = trades[(trades["layer"].eq(layer)) & trades["candidate"].eq(candidate)] if len(trades) else trades
        candidate_signals = signals[(signals["layer"].eq(layer)) & signals["candidate"].eq(candidate)] if len(signals) else signals
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "signals": len(candidate_signals),
                "daily_entry_checks": int(candidate_signals["reason"].eq("daily_entry").sum()) if len(candidate_signals) else 0,
                "daily_entry_gate_pass": int((candidate_signals["reason"].eq("daily_entry") & candidate_signals["gate_pass"].astype(bool)).sum()) if len(candidate_signals) else 0,
                "open_events": int(candidate_trades["action"].eq("open").sum()) if len(candidate_trades) else 0,
                "roll_events": int(candidate_trades["action"].eq("roll").sum()) if len(candidate_trades) else 0,
                "close_events": int(candidate_trades["action"].eq("close").sum()) if len(candidate_trades) else 0,
                "tp80_signals": int(candidate_signals["reason"].eq("tp80").sum()) if len(candidate_signals) else 0,
                "tp80_rolls": int(candidate_trades["reason"].eq("tp80").sum()) if len(candidate_trades) else 0,
                "call_days": int(group["call_contract"].fillna("").ne("").sum()),
                "call_day_ratio": float(group["call_contract"].fillna("").ne("").mean()),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "average_margin_fraction": float(group["call_margin_fraction"].mean()),
                "maximum_margin_fraction": float(group["call_margin_fraction"].max()),
                "capital_breach_days": int((group["put_mark_fraction"] + group["call_margin_fraction"] > v19.CASH_BASE + 1e-12).sum()),
            }
        )
    return pd.DataFrame(rows)


def audit_results(
    reference: pd.DataFrame,
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    calls: pd.DataFrame,
) -> dict[str, Any]:
    parity: dict[str, dict[str, float]] = {}
    for candidate in [BASELINE, MONTHLY]:
        left = reference[reference["candidate"].eq(candidate)].sort_values(["layer", "date"])
        right = daily[daily["candidate"].eq(candidate)].sort_values(["layer", "date"])
        parity[candidate] = {
            column: float(np.max(np.abs(left[column].to_numpy() - right[column].to_numpy())))
            for column in ["ret", "cash_ret", "nav", "cash_nav"]
        }
    candidates = daily[daily["candidate"].isin(CANDIDATES)].copy()
    expected_ret = (
        (1.0 + candidates["gross_ret"] + candidates["put_pnl_ret"] + candidates["call_pnl_ret"])
        * (1.0 - candidates["cost_rate"])
        * (1.0 - candidates["put_cost_rate"])
        * (1.0 - candidates["call_cost_rate"])
        - 1.0
    )
    expected_cash = candidates["ret"] + (
        v19.CASH_BASE - candidates["put_mark_fraction"] - candidates["call_margin_fraction"]
    ).clip(lower=0.0) * v19.CASH_DAILY
    selected_signals = signals[signals["contract"].fillna("").ne("")]
    gate_errors = int((selected_signals["gate_pass"].astype(bool) != (selected_signals["gate_iv"] >= IV_THRESHOLD - 1e-12)).sum())
    causality = int(
        (signals["eval_date"] >= signals["scheduled_execution_date"]).sum()
        + (trades["eval_date"] >= trades["actual_execution_date"]).sum()
        + (trades["actual_execution_date"] < trades["scheduled_execution_date"]).sum()
    )
    tp_trades = trades[trades["reason"].eq("tp80")]
    tp_errors = int(
        (tp_trades["remaining_price_ratio"] > TP_REMAINING_RATIO + 1e-12).sum()
        + (pd.to_datetime(tp_trades["new_expiry"]) <= pd.to_datetime(tp_trades["old_expiry"])).sum()
        + (~tp_trades["gate_pass"].astype(bool)).sum()
    )
    call_lookup = calls.set_index(["contract", "date"])
    close_errors: list[float] = []
    for trade in trades[trades["layer"].eq("real")].itertuples(index=False):
        if str(trade.new_contract):
            quote = call_lookup.loc[(trade.new_contract, trade.actual_execution_date)]
            if isinstance(quote, pd.DataFrame):
                quote = quote.iloc[-1]
            close_errors.append(abs(float(quote["close"]) - float(trade.new_close)))
        if str(trade.old_contract):
            quote = call_lookup.loc[(trade.old_contract, trade.actual_execution_date)]
            if isinstance(quote, pd.DataFrame):
                quote = quote.iloc[-1]
            close_errors.append(abs(float(quote["close"]) - float(trade.old_close)))
    result = {
        "reference_parity_max_abs": parity,
        "return_identity_max_abs": float((candidates["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((candidates["cash_ret"] - expected_cash).abs().max()),
        "gate_formula_errors": gate_errors,
        "causality_failures": causality,
        "tp80_rule_errors": tp_errors,
        "official_close_max_abs_error": max(close_errors) if close_errors else 0.0,
    }
    result["all_pass"] = bool(
        max(max(values.values()) for values in parity.values()) <= 1e-15
        and result["return_identity_max_abs"] <= 3e-15
        and result["cash_identity_max_abs"] <= 1e-15
        and gate_errors == 0
        and causality == 0
        and tp_errors == 0
        and result["official_close_max_abs_error"] <= 1e-12
    )
    return result


def decision_result(
    formal: pd.DataFrame,
    events: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    audit_ok: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    monthly_opens = int(
        pd.read_csv(V20_TRADES)[lambda frame: (frame["layer"].eq("real")) & frame["candidate"].eq(MONTHLY)]["action"].eq("open").sum()
    )
    for candidate, reference in [(DAILY, MONTHLY), (DAILY_TP80, DAILY)]:
        real_events = events[(events["layer"].eq("real")) & events["candidate"].eq(candidate)]
        improvements = {
            window: metric_value(formal, "real", candidate, window, "ann_return") - metric_value(formal, "real", reference, window, "ann_return")
            for window in ["full", "last_3y", "last_1y"]
        }
        dd_delta = metric_value(formal, "real", candidate, "full", "max_dd") - metric_value(formal, "real", reference, "full", "max_dd")
        capital = int(real_events.iloc[0]["capital_breach_days"])
        failures = stats[candidate]["scheduled_execution_failures"]
        if candidate == DAILY:
            event_increment = int(real_events.iloc[0]["open_events"]) - monthly_opens
            event_gate = event_increment >= 2
        else:
            event_increment = int(real_events.iloc[0]["tp80_rolls"])
            event_gate = event_increment >= 2
        return_gate = improvements["full"] >= -1e-12 and improvements["last_3y"] >= -1e-12
        risk_gate = dd_delta >= -0.02 - 1e-12
        execution_gate = capital == 0 and failures == 0
        hard_pass = bool(return_gate and risk_gate and event_gate and execution_gate and audit_ok)
        rows.append(
            {
                "candidate": candidate,
                "reference": reference,
                "real_full_cagr_delta": improvements["full"],
                "real_3y_cagr_delta": improvements["last_3y"],
                "real_1y_cagr_delta": improvements["last_1y"],
                "real_full_maxdd_delta": dd_delta,
                "incremental_events": event_increment,
                "capital_breach_days": capital,
                "scheduled_execution_failures": failures,
                "return_gate": return_gate,
                "risk_gate": risk_gate,
                "event_gate": event_gate,
                "execution_gate": execution_gate,
                "audit_gate": audit_ok,
                "hard_pass": hard_pass,
            }
        )
    table = pd.DataFrame(rows)
    daily_pass = bool(table.loc[table["candidate"].eq(DAILY), "hard_pass"].iloc[0])
    tp_pass = bool(table.loc[table["candidate"].eq(DAILY_TP80), "hard_pass"].iloc[0])
    combined_vs_monthly = (
        metric_value(formal, "real", DAILY_TP80, "full", "ann_return") >= metric_value(formal, "real", MONTHLY, "full", "ann_return") - 1e-12
        and metric_value(formal, "real", DAILY_TP80, "last_3y", "ann_return") >= metric_value(formal, "real", MONTHLY, "last_3y", "ann_return") - 1e-12
    )
    if daily_pass and tp_pass:
        conclusion = "daily_and_tp80_incremental_supported_real_only"
        selected = DAILY_TP80
    elif daily_pass:
        conclusion = "daily_supported_tp80_not_supported"
        selected = DAILY
    elif combined_vs_monthly and audit_ok:
        conclusion = "combined_effect_only_not_separable"
        selected = MONTHLY
    else:
        conclusion = "daily_and_tp80_not_supported"
        selected = MONTHLY
    return table, {
        "conclusion": conclusion,
        "selected_candidate": selected,
        "daily_pass": daily_pass,
        "tp80_pass": tp_pass,
        "stability_label": "official_real_short_sample",
        "live_approved": False,
        "research_status": "mechanism_test_only_not_live_approved",
    }


def rebound_table(daily: pd.DataFrame) -> pd.DataFrame:
    sample = daily[daily["date"].between("2024-09-18", "2024-10-08")]
    return pd.DataFrame(
        [
            {"layer": layer, "candidate": candidate, **v19.metrics(group.sort_values("date")["cash_ret"])}
            for (layer, candidate), group in sample.groupby(["layer", "candidate"])
        ]
    )


def scan_tables(formal: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    model = formal[formal["layer"].eq("model")].copy()
    long = model.rename(columns={"window": "segment", "actual_start": "start"})[
        ["candidate", "segment", "start", "end", "rows", "ann_return", "ann_vol", "max_dd", "sharpe_repo"]
    ]
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in model.groupby("candidate"):
        lookup = group.set_index("window")
        row: dict[str, Any] = {"candidate": candidate}
        for window in v19.WINDOWS:
            row[f"ann_return_{window}"] = float(lookup.loc[window, "ann_return"])
            row[f"max_dd_{window}"] = float(lookup.loc[window, "max_dd"])
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def record_text(
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    events: pd.DataFrame,
    decision_table: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    focus = formal[formal["window"].isin(["full", "last_10y", "last_3y", "last_1y"])]
    annual_real = annual[annual["layer"].eq("real")]
    lines = [
        "# IM + MO Call 每日入场与盈利80%提前展期 v22",
        "",
        f"Decision: `{decision['conclusion']}`；未批准实盘。",
        f"Selected: `{decision['selected_candidate']}`。",
        "Data: 模型2015-04-16—2026-08-14；真实官方IM/MO 2022-07-22—2026-08-14。",
        "",
        "## 主要窗口",
        "",
        "|层|候选|窗口|CAGR|MaxDD|Sharpe|",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in focus.itertuples(index=False):
        if bool(row.available):
            lines.append(f"|{row.layer}|{row.candidate}|{row.window}|{row.ann_return:.2%}|{row.max_dd:.2%}|{row.sharpe_repo:.3f}|")
    lines.extend(["", "## 真实逐年区间累计", "", "|候选|年份|区间收益|MaxDD|", "|---|---:|---:|---:|"])
    for row in annual_real.itertuples(index=False):
        lines.append(f"|{row.candidate}|{row.year}|{row.total_return:.2%}|{row.max_dd:.2%}|")
    lines.extend(
        [
            "",
            "## 事件与暴露",
            "",
            events.to_markdown(index=False),
            "",
            "## 判定",
            "",
            decision_table.to_markdown(index=False),
            "",
            "## 审计",
            "",
            "```json",
            json.dumps(audit, ensure_ascii=False, indent=2),
            "```",
            "",
            "本研究是每日检查与盈利80%提前展期的机制验证，不是完整外部策略复现或交易建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def update_scan(scan_long: pd.DataFrame, scan_wide: pd.DataFrame, record: str, decision: dict[str, Any]) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python im_mo_call_daily_entry_profit_roll_v22.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "baseline": {"candidate": MONTHLY, "definition": "v20 monthly front D10 IV26"},
            "candidate_grid": [BASELINE, MONTHLY, DAILY, DAILY_TP80],
            "data_snapshot": {"model_start": str(v19.MODEL_START.date()), "real_start": str(v19.REAL_START.date()), "end": str(v19.END.date()), "real_call_source": "official CFFEX daily archives"},
            "cost_model": {"two_contract_call_basket_one_way": v19.CALL_BASKET_SIDE_COST, "cash_annual_return": 0.03, "call_margin": "same independent obligation approximation as v19/v20"},
            "outputs": {"record": str(SCAN / "record.md"), "scan_summary": str(SCAN / "scan_summary.csv"), "window_metrics": str(SCAN / "window_metrics.csv"), "scan_meta": str(meta_path), "command_log": str(SCAN / "command_log.txt")},
            "preliminary_decision": decision["conclusion"],
            "preliminary_stability_label": decision["stability_label"],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    events: pd.DataFrame,
    rebound: pd.DataFrame,
    decision_table: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
    record: str,
    stats: dict[str, dict[str, int]],
) -> None:
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "call_trades.csv", index=False)
    signals.to_csv(STAGING / "signals.csv", index=False)
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    events.to_csv(STAGING / "event_exposure_summary.csv", index=False)
    rebound.to_csv(STAGING / "rebound_2024_0918_1008.csv", index=False)
    decision_table.to_csv(STAGING / "decision_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "execution_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "audit_summary.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text("python im_mo_call_daily_entry_profit_roll_v22.py\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": {str(path.relative_to(ROOT)): expected for path, expected in FROZEN_HASHES.items()},
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
        "sample": {"model": [str(v19.MODEL_START.date()), str(v19.END.date())], "real": [str(v19.REAL_START.date()), str(v19.END.date())]},
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(STAGING.iterdir()) if path.is_file()},
    }
    (STAGING / "output_manifest.json").write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING.replace(OUTPUT)


def main() -> None:
    verify_inputs()
    reference = reference_daily()
    baseline = v19.load_baseline()
    upstream = v19.load_upstream()
    market, market_checks = v19.v6.model_market()
    real_market = market[market["date"].ge(v19.REAL_START)].copy()
    calls = v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    model_events = v19.monthly_events(v19.MODEL_START, model_dates, v19.model_roll_dates(model_dates))
    real_rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    real_events = v19.monthly_events(v19.REAL_START, real_dates, real_rolls)
    model_monthly = v19.build_model_selections(market, model_events, "front", TARGET_DELTA, MONTHLY)
    real_monthly = v19.build_real_selections(calls, real_market, real_events, "front", TARGET_DELTA, MONTHLY)
    model_base = baseline[baseline["layer"].eq("model")].drop(columns=["layer", "candidate"])
    real_base = baseline[baseline["layer"].eq("real")].drop(columns=["layer", "candidate"])

    daily_parts = [reference]
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    stats: dict[str, dict[str, int]] = {}
    for label, tp_enabled in [(DAILY, False), (DAILY_TP80, True)]:
        model_overlay, model_trades, model_signals, model_stats = run_model(market, model_events, model_monthly, label, tp_enabled)
        real_overlay, real_trades, real_signals, real_stats = run_real(upstream, calls, real_market, real_events, real_monthly, label, tp_enabled)
        model_candidate = v19.assemble_candidate(model_base, model_overlay, label)
        model_candidate["layer"] = "model"
        real_candidate = v19.assemble_candidate(real_base, real_overlay, label)
        real_candidate["layer"] = "real"
        daily_parts.extend([model_candidate, real_candidate])
        trade_parts.extend([model_trades, real_trades])
        signal_parts.extend([model_signals, real_signals])
        stats[label] = {
            "scheduled_execution_failures": model_stats["scheduled_execution_failures"] + real_stats["scheduled_execution_failures"],
            "delayed_trading_days": model_stats["delayed_trading_days"] + real_stats["delayed_trading_days"],
            "model_tp_opportunities": model_stats["tp_opportunities"],
            "real_tp_opportunities": real_stats["tp_opportunities"],
            "model_tp_iv_or_far_failures": model_stats["tp_iv_or_far_failures"],
            "real_tp_iv_or_far_failures": real_stats["tp_iv_or_far_failures"],
        }
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"]).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["layer", "candidate", "actual_execution_date"]).reset_index(drop=True)
    signals = pd.concat(signal_parts, ignore_index=True).sort_values(["layer", "candidate", "eval_date"]).reset_index(drop=True)
    formal, annual = v19.metrics_tables(daily)
    events = event_summary(daily, trades, signals)
    audit = audit_results(reference, daily, trades, signals, calls)
    audit["market_checks"] = market_checks
    decision_table, decision = decision_result(formal, events, stats, bool(audit["all_pass"]))
    rebound = rebound_table(daily)
    scan_long, scan_wide = scan_tables(formal)
    record = record_text(formal, annual, events, decision_table, decision, audit)
    update_scan(scan_long, scan_wide, record, decision)
    write_outputs(daily, trades, signals, formal, annual, events, rebound, decision_table, decision, audit, record, stats)
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
