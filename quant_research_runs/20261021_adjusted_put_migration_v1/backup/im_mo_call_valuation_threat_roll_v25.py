from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_valuation_profit_roll_v24 as v24


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_valuation_threat_roll_v25"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "2ae68ed9d347a7d56e93e322c9662c60a18eb152ca0e5aaef401d363ebbbeb35"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260819_new_strategy_research_im_mo_call_valuation_threat_roll_v25_im_mo_call_overwrite_threat_otm5_up5_next_expiry_max5"
)
V23_OUTPUT = ROOT / "outputs" / "im_mo_call_valuation_hysteresis_v23"
V23_DAILY = V23_OUTPUT / "daily_candidates.csv.gz"
V23_STATES = V23_OUTPUT / "pe_valuation_states.csv.gz"

v23 = v24.v23
v22 = v24.v22
v19 = v24.v19
BASELINE = v23.BASELINE
MONTHLY = v23.MONTHLY
DAILY_D10 = v23.DAILY_D10
CONTROL_0 = v23.CONTROL
FORMAL_0 = v23.FORMAL
CONTROL_2 = "article_ladder_iv26_daily_threat5_up5_next1_max5"
FORMAL_2 = "article_pe20_60_hysteresis_iv26_daily_threat5_up5_next1_max5"
CANDIDATES = (CONTROL_2, FORMAL_2)
IV_THRESHOLD = 0.26
THREAT_OTM = 0.05
STRIKE_STEP = 0.05
MAX_THREAT_ROLLS = 5
END = v23.END

FROZEN_HASHES = {
    **v24.FROZEN_HASHES,
    ROOT / "im_mo_call_valuation_profit_roll_v24.py": "91250dea6b297b39dc28e9993660006349ce52e456748f8f3713c2add0b6053f",
}


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


def verify_inputs() -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v25 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v25 specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging v25 output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v25 parameter folder is missing")
    for path, expected in FROZEN_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v25 input changed: {path}")
    return {str(path.relative_to(ROOT)): expected for path, expected in FROZEN_HASHES.items()}


def threat_model_selection(
    market: pd.DataFrame,
    dates: pd.DatetimeIndex,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    active: v22.ModelActive,
    label: str,
) -> tuple[v19.Selection | None, dict[str, Any]]:
    row = market.set_index("date").loc[day]
    spot = float(row["spot_close"])
    listed = v19.v6.model_listed_months(day, dates)
    available = pd.DataFrame(
        {
            "month": listed,
            "expiry": [v19.rule_expiry(month, dates) for month in listed],
        }
    )
    available = available[available["expiry"].gt(active.selection.expiry)]
    target = float(active.selection.strike) * (1.0 + STRIKE_STEP)
    if available.empty:
        return None, {
            "target_strike": target,
            "new_eval_moneyness": np.nan,
            "new_dte": np.nan,
        }
    chosen = available.sort_values(["expiry", "month"]).iloc[0]
    month = pd.Timestamp(chosen["month"])
    expiry = pd.Timestamp(chosen["expiry"])
    years = (expiry - day).days / 365.0
    sigma = float(row["sigma_close"])
    delta = v19.bs_call_delta(
        spot,
        target,
        float(row["rate_close"]),
        float(row["dividend_close"]),
        sigma,
        years,
    )
    close = v19.bs_call(
        spot,
        target,
        float(row["rate_close"]),
        float(row["dividend_close"]),
        sigma,
        years,
    )
    selection = v19.Selection(
        "model",
        label,
        "threat_up5",
        delta,
        "threat_roll",
        day,
        execution,
        f"MODEL_THREAT_{month:%y%m}_{target:.6f}",
        month,
        expiry,
        target,
        delta,
        sigma,
        close,
        np.nan,
        np.nan,
    )
    return selection, {
        "target_strike": target,
        "new_eval_moneyness": target / spot - 1.0,
        "new_dte": int((expiry - day).days),
    }


def threat_real_selection(
    calls: pd.DataFrame,
    market_row: pd.Series,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    active: v22.RealActive,
    label: str,
) -> tuple[v19.Selection | None, dict[str, Any]]:
    spot = float(market_row["spot_close"])
    target = float(active.selection.strike) * (1.0 + STRIKE_STEP)
    chain = calls[
        calls["date"].eq(day)
        & calls["actual_expiry"].gt(active.selection.expiry)
    ].copy()
    if chain.empty:
        return None, {
            "target_strike": target,
            "new_eval_moneyness": np.nan,
            "new_dte": np.nan,
        }
    next_expiry = pd.Timestamp(chain["actual_expiry"].min())
    eligible = chain[
        chain["actual_expiry"].eq(next_expiry)
        & chain["strike"].ge(target - 1e-12)
        & chain["close"].gt(0)
        & chain["volume"].gt(0)
        & chain["open_interest"].gt(0)
    ].sort_values(
        ["strike", "open_interest", "volume", "contract"],
        ascending=[True, False, False, True],
    )
    for quote in eligible.itertuples(index=False):
        dte = int((pd.Timestamp(quote.actual_expiry) - day).days)
        iv = v19.implied_volatility(
            float(quote.close),
            spot,
            float(quote.strike),
            float(market_row["rate_close"]),
            float(market_row["dividend_close"]),
            dte / 365.0,
        )
        if iv is None or not np.isfinite(iv) or iv <= 0:
            continue
        delta = v19.bs_call_delta(
            spot,
            float(quote.strike),
            float(market_row["rate_close"]),
            float(market_row["dividend_close"]),
            iv,
            dte / 365.0,
        )
        selection = v19.Selection(
            "real",
            label,
            "threat_up5",
            delta,
            "threat_roll",
            day,
            execution,
            str(quote.contract),
            pd.Timestamp(quote.contract_month),
            pd.Timestamp(quote.actual_expiry),
            float(quote.strike),
            delta,
            iv,
            float(quote.close),
            float(quote.volume),
            float(quote.open_interest),
        )
        return selection, {
            "target_strike": target,
            "new_eval_moneyness": float(quote.strike) / spot - 1.0,
            "new_dte": dte,
        }
    return None, {
        "target_strike": target,
        "new_eval_moneyness": np.nan,
        "new_dte": int((next_expiry - day).days),
    }


def make_signal(
    layer: str,
    label: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    reason: str,
    active: Any,
    selection: v19.Selection | None,
    action: str,
    meta: dict[str, Any],
    threat_otm: float = np.nan,
    threat_count_before: int = 0,
) -> dict[str, Any]:
    state_meta = meta.get("state_meta")
    if state_meta is None:
        state_meta = {
            "history_kind": meta.get("history_kind", ""),
            "official_rolling_pe": meta.get("official_rolling_pe", np.nan),
            "pe_percentile_10y": meta.get("pe_percentile_10y", np.nan),
            "pe_history_rows": meta.get("pe_history_rows", 0),
            "valuation_state": meta.get("valuation_state", ""),
            "state_changed": meta.get("state_changed", False),
            "tier": meta.get("tier", 0),
            "dte_low": meta.get("dte_low", np.nan),
            "dte_high": meta.get("dte_high", np.nan),
            "min_otm": meta.get("min_otm", np.nan),
            "dte": meta.get("dte", np.nan),
            "spot": meta.get("spot", np.nan),
            "moneyness": meta.get("moneyness", np.nan),
        }
    row = v23.make_signal(
        layer,
        label,
        day,
        execution,
        reason,
        active is not None,
        selection,
        action,
        state_meta,
        active.selection.expiry if active is not None else None,
    )
    iv = float(selection.implied_vol) if selection is not None else np.nan
    is_threat = reason.startswith("threat_")
    row.update(
        {
            "gate_pass": bool(selection is not None and (is_threat or iv >= IV_THRESHOLD - 1e-12)),
            "entry_iv_gate_pass": bool(selection is not None and iv >= IV_THRESHOLD - 1e-12),
            "threat_iv_override": bool(is_threat and selection is not None and iv < IV_THRESHOLD - 1e-12),
            "threat_otm": threat_otm,
            "threat_count_before": threat_count_before,
            "target_strike": meta.get("target_strike", np.nan),
            "new_eval_moneyness": meta.get("new_eval_moneyness", np.nan),
            "new_dte": meta.get("new_dte", np.nan),
            "old_contract": active.selection.contract if active is not None else "",
            "old_strike": active.selection.strike if active is not None else np.nan,
        }
    )
    return row


def normal_pending(
    active: Any,
    selection: v19.Selection | None,
    reason: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
) -> v22.Pending | None:
    return v22.pending_from_selection(
        active is not None,
        selection,
        reason,
        day,
        execution,
        old_expiry=active.selection.expiry if active is not None else None,
    )


def threat_pending(
    active: Any,
    selection: v19.Selection | None,
    reason: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
) -> v22.Pending:
    return v22.Pending(
        day,
        execution,
        "roll" if selection is not None else "close",
        reason,
        selection,
        float(selection.implied_vol) if selection is not None else np.nan,
        bool(selection is not None),
        np.nan,
        active.selection.expiry,
    )


def trade_row(
    layer: str,
    label: str,
    pending: v22.Pending,
    context: dict[str, Any],
    day: pd.Timestamp,
    old: Any,
    active: Any,
    old_close: float,
    new_close: float,
    new_settle: float,
    delay: int,
) -> dict[str, Any]:
    row = v23.trade_row(
        layer,
        label,
        pending,
        day,
        old,
        active,
        old_close,
        new_close,
        new_settle,
        delay,
    )
    row.update(context)
    return row


def normal_signal_and_pending(
    layer: str,
    label: str,
    day: pd.Timestamp,
    tomorrow: pd.Timestamp,
    reason: str,
    active: Any,
    selection: v19.Selection | None,
    meta: dict[str, Any],
) -> tuple[dict[str, Any], v22.Pending | None]:
    gate = bool(selection is not None and selection.implied_vol >= IV_THRESHOLD - 1e-12)
    action = (
        "roll"
        if active is not None and gate
        else "close"
        if active is not None
        else "open"
        if gate
        else "skip"
    )
    return (
        make_signal(layer, label, day, tomorrow, reason, active, selection, action, meta),
        normal_pending(active, selection, reason, day, tomorrow),
    )


def run_model(
    market: pd.DataFrame,
    events: pd.DataFrame,
    states: pd.DataFrame,
    label: str,
    force_normal: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    dates = pd.DatetimeIndex(market["date"])
    next_days = v22.next_day_map(dates)
    event_lookup = events.set_index("eval_date")
    active: v22.ModelActive | None = None
    pending: v22.Pending | None = None
    pending_context: dict[str, Any] = {}
    cycle_id = 0
    threat_count = 0
    blocked = False
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    stats = {
        "final_pending": 0,
        "scheduled_execution_failures": 0,
        "delayed_trading_days": 0,
        "threat_signals": 0,
        "threat_rolls": 0,
        "threat_no_contract_stops": 0,
        "threat_max5_stops": 0,
        "blocked_days": 0,
        "reenable_events": 0,
        "max_consecutive_threat_rolls": 0,
    }
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        denominator = float(row.base_prior_close)
        pnl = cost = 0.0
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
                active = v22.ModelActive(pending.selection, units, entry_mark, entry_mark, cycle_id)
                cost += v19.CALL_BASKET_SIDE_COST
            trades.append(
                trade_row("model", label, pending, pending_context, day, old, active, old_mark, entry_mark, entry_mark, 0)
            )
            if pending.reason == "threat_roll":
                threat_count = int(pending_context["threat_count_before"]) + 1
                stats["threat_rolls"] += 1
                stats["max_consecutive_threat_rolls"] = max(
                    stats["max_consecutive_threat_rolls"], threat_count
                )
            elif pending.reason.startswith("threat_stop"):
                blocked = True
                threat_count = 0
            else:
                threat_count = 0
            pending = None
            pending_context = {}
            traded = True
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if blocked:
            stats["blocked_days"] += 1
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = v23.state_row(states, day, force_normal)
            if active is not None:
                threat_otm = float(active.selection.strike) / float(row.spot_close) - 1.0
                if threat_otm <= THREAT_OTM + 1e-12:
                    stats["threat_signals"] += 1
                    if threat_count >= MAX_THREAT_ROLLS:
                        reason = "threat_stop_max5"
                        proposed = None
                        meta = {"target_strike": np.nan, "new_eval_moneyness": np.nan, "new_dte": np.nan}
                        stats["threat_max5_stops"] += 1
                    else:
                        proposed, meta = threat_model_selection(
                            market, dates, day, tomorrow, active, label
                        )
                        reason = "threat_roll" if proposed is not None else "threat_stop_no_contract"
                        if proposed is None:
                            stats["threat_no_contract_stops"] += 1
                    pending = threat_pending(active, proposed, reason, day, tomorrow)
                    pending_context = {
                        "threat_otm": threat_otm,
                        "threat_count_before": threat_count,
                        **meta,
                    }
                    signals.append(
                        make_signal(
                            "model", label, day, tomorrow, reason, active, proposed,
                            "roll" if proposed is not None else "close", meta,
                            threat_otm, threat_count,
                        )
                    )
                elif event is not None:
                    must_roll = active.selection.expiry <= pd.Timestamp(event.current_expiry)
                    if must_roll:
                        proposed, meta = v23.model_selection(
                            market, dates, day, tomorrow, label, "monthly", state
                        )
                        signal, pending = normal_signal_and_pending(
                            "model", label, day, tomorrow, "monthly", active, proposed, meta
                        )
                        signals.append(signal)
                        pending_context = {}
                    else:
                        meta = v23.selection_meta(
                            None, state, 0, np.nan, np.nan, np.nan, np.nan, float(row.spot_close)
                        )
                        signals.append(
                            make_signal("model", label, day, tomorrow, "monthly_keep_far", active, None, "keep_far", meta)
                        )
            elif event is not None:
                if blocked:
                    blocked = False
                    stats["reenable_events"] += 1
                proposed, meta = v23.model_selection(
                    market, dates, day, tomorrow, label, "monthly", state
                )
                signal, pending = normal_signal_and_pending(
                    "model", label, day, tomorrow, "monthly", None, proposed, meta
                )
                signals.append(signal)
                pending_context = {}
            elif not blocked:
                proposed, meta = v23.model_selection(
                    market, dates, day, tomorrow, label, "daily_entry", state
                )
                signal, pending = normal_signal_and_pending(
                    "model", label, day, tomorrow, "daily_entry", None, proposed, meta
                )
                signals.append(signal)
                pending_context = {}
        daily = v23.model_daily_row(day, label, row, active, pnl, cost)
        daily.update({"threat_roll_count": threat_count, "threat_entry_blocked": blocked})
        rows.append(daily)
    if pending is not None:
        raise RuntimeError(f"Unexecuted final model action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), stats


def run_real(
    upstream: pd.DataFrame,
    calls: pd.DataFrame,
    market: pd.DataFrame,
    events: pd.DataFrame,
    states: pd.DataFrame,
    label: str,
    force_normal: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    dates = pd.DatetimeIndex(upstream["date"])
    next_days = v22.next_day_map(dates)
    event_lookup = events.set_index("eval_date")
    market_lookup = market.set_index("date")
    call_lookup = calls.set_index(["contract", "date"])
    prior_im = upstream["settle"].shift(1)
    prior_im.iloc[0] = upstream.iloc[0]["settle"]
    active: v22.RealActive | None = None
    pending: v22.Pending | None = None
    pending_context: dict[str, Any] = {}
    cycle_id = 0
    threat_count = 0
    blocked = False
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    stats = {
        "final_pending": 0,
        "scheduled_execution_failures": 0,
        "delayed_trading_days": 0,
        "threat_signals": 0,
        "threat_rolls": 0,
        "threat_no_contract_stops": 0,
        "threat_max5_stops": 0,
        "blocked_days": 0,
        "reenable_events": 0,
        "max_consecutive_threat_rolls": 0,
    }
    for index, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(prior_im.iloc[index])
        market_row = market_lookup.loc[day]
        pnl = cost = 0.0
        traded = False
        old_quote = (
            v19.quote_row(call_lookup, active.selection.contract, day)
            if active is not None
            else None
        )
        new_quote = (
            v19.quote_row(call_lookup, pending.selection.contract, day)
            if pending is not None and pending.selection is not None
            else None
        )
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
        if (
            pending is not None
            and day >= pending.scheduled_execution_date
            and old_tradable
            and new_tradable
        ):
            old = active
            old_close = np.nan
            if old is not None:
                old_close = float(old_quote["close"])
                pnl += (
                    old.qty
                    * v19.MO_MULTIPLIER
                    / v19.IM_MULTIPLIER
                    * (old.prior_settle - old_close)
                    / denominator
                )
                cost += v19.CALL_BASKET_SIDE_COST
            active = None
            new_close = new_settle = np.nan
            if pending.selection is not None:
                new_close = float(new_quote["close"])
                new_settle = float(new_quote["settle"])
                pnl += (
                    v19.MO_QTY
                    * v19.MO_MULTIPLIER
                    / v19.IM_MULTIPLIER
                    * (new_close - new_settle)
                    / denominator
                )
                cost += v19.CALL_BASKET_SIDE_COST
                cycle_id += 1
                active = v22.RealActive(
                    pending.selection, v19.MO_QTY, new_settle, new_close, cycle_id
                )
            delay = int(((dates > pending.scheduled_execution_date) & (dates <= day)).sum())
            stats["delayed_trading_days"] += delay
            trades.append(
                trade_row(
                    "real", label, pending, pending_context, day, old, active,
                    old_close, new_close, new_settle, delay,
                )
            )
            if pending.reason == "threat_roll":
                threat_count = int(pending_context["threat_count_before"]) + 1
                stats["threat_rolls"] += 1
                stats["max_consecutive_threat_rolls"] = max(
                    stats["max_consecutive_threat_rolls"], threat_count
                )
            elif pending.reason.startswith("threat_stop"):
                blocked = True
                threat_count = 0
            else:
                threat_count = 0
            pending = None
            pending_context = {}
            traded = True
        elif pending is not None and day == pending.scheduled_execution_date:
            stats["scheduled_execution_failures"] += 1
        if not traded and active is not None:
            if old_quote is None or float(old_quote["settle"]) <= 0:
                raise RuntimeError(f"Missing real settlement: {label} {day.date()}")
            pnl += (
                active.qty
                * v19.MO_MULTIPLIER
                / v19.IM_MULTIPLIER
                * (active.prior_settle - float(old_quote["settle"]))
                / denominator
            )
            active.prior_settle = float(old_quote["settle"])
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if blocked:
            stats["blocked_days"] += 1
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = v23.state_row(states, day, force_normal)
            if active is not None:
                threat_otm = float(active.selection.strike) / float(market_row["spot_close"]) - 1.0
                if threat_otm <= THREAT_OTM + 1e-12:
                    stats["threat_signals"] += 1
                    if threat_count >= MAX_THREAT_ROLLS:
                        reason = "threat_stop_max5"
                        proposed = None
                        meta = {"target_strike": np.nan, "new_eval_moneyness": np.nan, "new_dte": np.nan}
                        stats["threat_max5_stops"] += 1
                    else:
                        proposed, meta = threat_real_selection(
                            calls, market_row, day, tomorrow, active, label
                        )
                        reason = "threat_roll" if proposed is not None else "threat_stop_no_contract"
                        if proposed is None:
                            stats["threat_no_contract_stops"] += 1
                    pending = threat_pending(active, proposed, reason, day, tomorrow)
                    pending_context = {
                        "threat_otm": threat_otm,
                        "threat_count_before": threat_count,
                        **meta,
                    }
                    signals.append(
                        make_signal(
                            "real", label, day, tomorrow, reason, active, proposed,
                            "roll" if proposed is not None else "close", meta,
                            threat_otm, threat_count,
                        )
                    )
                elif event is not None:
                    must_roll = active.selection.expiry <= pd.Timestamp(event.current_expiry)
                    if must_roll:
                        proposed, meta = v23.real_selection(
                            calls, market_row, day, tomorrow, label, "monthly", state
                        )
                        signal, pending = normal_signal_and_pending(
                            "real", label, day, tomorrow, "monthly", active, proposed, meta
                        )
                        signals.append(signal)
                        pending_context = {}
                    else:
                        meta = v23.selection_meta(
                            None, state, 0, np.nan, np.nan, np.nan, np.nan,
                            float(market_row["spot_close"]),
                        )
                        signals.append(
                            make_signal("real", label, day, tomorrow, "monthly_keep_far", active, None, "keep_far", meta)
                        )
            elif event is not None:
                if blocked:
                    blocked = False
                    stats["reenable_events"] += 1
                proposed, meta = v23.real_selection(
                    calls, market_row, day, tomorrow, label, "monthly", state
                )
                signal, pending = normal_signal_and_pending(
                    "real", label, day, tomorrow, "monthly", None, proposed, meta
                )
                signals.append(signal)
                pending_context = {}
            elif not blocked:
                proposed, meta = v23.real_selection(
                    calls, market_row, day, tomorrow, label, "daily_entry", state
                )
                signal, pending = normal_signal_and_pending(
                    "real", label, day, tomorrow, "daily_entry", None, proposed, meta
                )
                signals.append(signal)
                pending_context = {}
        daily = v23.real_daily_row(
            day, label, base, market_row, call_lookup, active, pnl, cost
        )
        daily.update({"threat_roll_count": threat_count, "threat_entry_blocked": blocked})
        rows.append(daily)
    if pending is not None:
        raise RuntimeError(f"Unexecuted final real action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), stats


def event_summary(
    daily: pd.DataFrame, trades: pd.DataFrame, signals: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        t = trades[trades["layer"].eq(layer) & trades["candidate"].eq(candidate)]
        s = signals[signals["layer"].eq(layer) & signals["candidate"].eq(candidate)]
        delta = group["call_delta"].dropna()
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "signals": len(s),
                "threat_signals": int(s["reason"].str.startswith("threat_").sum()) if len(s) else 0,
                "threat_rolls": int(t["reason"].eq("threat_roll").sum()) if len(t) else 0,
                "threat_stops": int(t["reason"].str.startswith("threat_stop").sum()) if len(t) else 0,
                "open_events": int(t["action"].eq("open").sum()) if len(t) else 0,
                "roll_events": int(t["action"].eq("roll").sum()) if len(t) else 0,
                "close_events": int(t["action"].eq("close").sum()) if len(t) else 0,
                "call_days": int(group["call_contract"].fillna("").ne("").sum()),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "max_call_delta": float(delta.max()) if len(delta) else np.nan,
                "p95_call_delta": float(delta.quantile(0.95)) if len(delta) else np.nan,
                "average_margin_fraction": float(group["call_margin_fraction"].mean()),
                "maximum_margin_fraction": float(group["call_margin_fraction"].max()),
                "capital_breach_days": int(
                    (
                        group["put_mark_fraction"] + group["call_margin_fraction"]
                        > v19.CASH_BASE + 1e-12
                    ).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def metric_value(
    formal: pd.DataFrame, layer: str, candidate: str, window: str, column: str
) -> float:
    return v19.metric_value(formal, layer, candidate, window, column)


def exposure_value(
    events: pd.DataFrame, layer: str, candidate: str, column: str
) -> float:
    row = events[events["layer"].eq(layer) & events["candidate"].eq(candidate)]
    return float(row.iloc[0][column])


def audit_results(
    reference: pd.DataFrame,
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    calls: pd.DataFrame,
    states: pd.DataFrame,
) -> dict[str, Any]:
    parity: dict[str, dict[str, float]] = {}
    for candidate in [BASELINE, MONTHLY, DAILY_D10, CONTROL_0, FORMAL_0]:
        left = reference[reference["candidate"].eq(candidate)].sort_values(["layer", "date"])
        right = daily[daily["candidate"].eq(candidate)].sort_values(["layer", "date"])
        parity[candidate] = {
            column: float(np.max(np.abs(left[column].to_numpy() - right[column].to_numpy())))
            for column in ["ret", "cash_ret", "nav", "cash_nav"]
        }
    candidates = daily[daily["candidate"].isin(CANDIDATES)]
    expected_ret = (
        1.0 + candidates["gross_ret"] + candidates["put_pnl_ret"] + candidates["call_pnl_ret"]
    ) * (1.0 - candidates["cost_rate"]) * (1.0 - candidates["put_cost_rate"]) * (
        1.0 - candidates["call_cost_rate"]
    ) - 1.0
    expected_cash = candidates["ret"] + (
        v19.CASH_BASE - candidates["put_mark_fraction"] - candidates["call_margin_fraction"]
    ).clip(lower=0.0) * v19.CASH_DAILY
    normal_selected = signals[
        signals["reason"].isin(["monthly", "daily_entry"])
        & signals["contract"].fillna("").ne("")
    ]
    threat_signals = signals[signals["reason"].str.startswith("threat_")]
    threat_roll_signals = threat_signals[threat_signals["reason"].eq("threat_roll")]
    threat_trades = trades[trades["reason"].eq("threat_roll")]
    normal_gate_errors = int(
        (
            normal_selected["gate_pass"].astype(bool)
            != normal_selected["gate_iv"].ge(IV_THRESHOLD - 1e-12)
        ).sum()
    )
    threat_rule_errors = int(
        (threat_signals["threat_otm"] > THREAT_OTM + 1e-12).sum()
        + (~threat_roll_signals["gate_pass"].astype(bool)).sum()
        + (
            pd.to_datetime(threat_trades["new_expiry"])
            <= pd.to_datetime(threat_trades["old_expiry"])
        ).sum()
        + (
            threat_trades["new_contract"].fillna("").ne("")
            & (
                pd.to_numeric(threat_trades["new_close"], errors="coerce").isna()
            )
        ).sum()
    )
    strike_errors = int(
        (
            pd.to_numeric(threat_roll_signals["selection_strike"], errors="coerce")
            + 1e-12
            < pd.to_numeric(threat_roll_signals["old_strike"], errors="coerce")
            * (1.0 + STRIKE_STEP)
        ).sum()
    )
    causality = int(
        (signals["eval_date"] >= signals["scheduled_execution_date"]).sum()
        + (trades["eval_date"] >= trades["actual_execution_date"]).sum()
        + (trades["actual_execution_date"] < trades["scheduled_execution_date"]).sum()
    )
    formal_signal_states = signals[signals["candidate"].eq(FORMAL_2)][
        ["eval_date", "valuation_state"]
    ].merge(
        states[["date", "valuation_state"]],
        left_on="eval_date",
        right_on="date",
        how="left",
        suffixes=("_signal", "_state"),
        validate="many_to_one",
    )
    state_errors = int(
        (
            formal_signal_states["valuation_state_signal"]
            != formal_signal_states["valuation_state_state"]
        ).sum()
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
        "normal_iv_gate_errors": normal_gate_errors,
        "threat_rule_errors": threat_rule_errors,
        "threat_strike_errors": strike_errors,
        "causality_errors": causality,
        "formal_state_errors": state_errors,
        "official_close_max_abs_error": max(close_errors, default=0.0),
        "capital_breach_days": int(
            (
                candidates["put_mark_fraction"] + candidates["call_margin_fraction"]
                > v19.CASH_BASE + 1e-12
            ).sum()
        ),
    }
    result["all_pass"] = bool(
        max(max(values.values()) for values in parity.values()) <= 1e-14
        and result["return_identity_max_abs"] <= 1e-12
        and result["cash_identity_max_abs"] <= 1e-12
        and normal_gate_errors == 0
        and threat_rule_errors == 0
        and strike_errors == 0
        and causality == 0
        and state_errors == 0
        and result["official_close_max_abs_error"] <= 1e-12
        and result["capital_breach_days"] == 0
    )
    return result


def line_decision(
    formal: pd.DataFrame,
    events: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    candidate: str,
    baseline: str,
    audit_ok: bool,
) -> dict[str, Any]:
    full_delta = metric_value(formal, "real", candidate, "full", "ann_return") - metric_value(
        formal, "real", baseline, "full", "ann_return"
    )
    y3_delta = metric_value(formal, "real", candidate, "last_3y", "ann_return") - metric_value(
        formal, "real", baseline, "last_3y", "ann_return"
    )
    y1_delta = metric_value(formal, "real", candidate, "last_1y", "ann_return") - metric_value(
        formal, "real", baseline, "last_1y", "ann_return"
    )
    full_dd_improvement = metric_value(formal, "real", candidate, "full", "max_dd") - metric_value(
        formal, "real", baseline, "full", "max_dd"
    )
    delta_improvement = exposure_value(events, "real", baseline, "max_call_delta") - exposure_value(
        events, "real", candidate, "max_call_delta"
    )
    margin_improvement = exposure_value(events, "real", baseline, "maximum_margin_fraction") - exposure_value(
        events, "real", candidate, "maximum_margin_fraction"
    )
    rolls = int(stats[candidate]["real_threat_rolls"])
    return_gate = full_delta >= -0.01 - 1e-12 and y3_delta >= -0.01 - 1e-12 and y1_delta >= -0.03 - 1e-12
    dd_gate = full_dd_improvement >= -0.005 - 1e-12
    exposure_gate = delta_improvement >= 0.05 - 1e-12 or margin_improvement >= 0.02 - 1e-12
    event_gate = rolls >= 2
    execution_gate = (
        stats[candidate]["final_pending"] == 0
        and exposure_value(events, "real", candidate, "capital_breach_days") == 0
    )
    return {
        "candidate": candidate,
        "baseline": baseline,
        "full_ann_delta": full_delta,
        "last_3y_ann_delta": y3_delta,
        "last_1y_ann_delta": y1_delta,
        "full_maxdd_improvement": full_dd_improvement,
        "max_call_delta_improvement": delta_improvement,
        "max_margin_improvement": margin_improvement,
        "real_threat_rolls": rolls,
        "return_gate": return_gate,
        "maxdd_gate": dd_gate,
        "exposure_gate": exposure_gate,
        "event_gate": event_gate,
        "execution_gate": execution_gate,
        "audit_gate": audit_ok,
        "hard_pass": bool(return_gate and dd_gate and exposure_gate and event_gate and execution_gate and audit_ok),
    }


def decision_result(
    formal: pd.DataFrame,
    events: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    audit_ok: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    control = line_decision(formal, events, stats, CONTROL_2, CONTROL_0, audit_ok)
    valuation = line_decision(formal, events, stats, FORMAL_2, FORMAL_0, audit_ok)
    table = pd.DataFrame([control, valuation])
    total_real_rolls = control["real_threat_rolls"] + valuation["real_threat_rolls"]
    if total_real_rolls < 2:
        conclusion = "insufficient_real_threat_events"
        selected = FORMAL_0
        stability = "event_insufficient"
    elif valuation["hard_pass"]:
        conclusion = "valuation_threat_roll_supported_real_short_sample"
        selected = FORMAL_2
        stability = "official_real_short_sample_supported"
    elif control["hard_pass"]:
        conclusion = "threat_roll_without_valuation_only"
        selected = CONTROL_2
        stability = "control_only_supported"
    else:
        conclusion = "threat_roll_not_supported"
        selected = FORMAL_0
        stability = "reject"
    return table, {
        "conclusion": conclusion,
        "selected_candidate": selected,
        "control_threat_pass": bool(control["hard_pass"]),
        "valuation_threat_pass": bool(valuation["hard_pass"]),
        "stability_label": stability,
        "live_approved": False,
        "research_status": "official_real_short_sample_mechanism_only_not_live_approved",
    }


def record_text(
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    stress: pd.DataFrame,
    events: pd.DataFrame,
    decision_table: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
    stats: dict[str, dict[str, int]],
) -> str:
    focus = formal[formal["window"].isin(["full", "last_10y", "last_5y", "last_3y", "last_1y"])]
    lines = [
        "# IM + MO Call PE估值滞回 × 受威胁向上向后移仓 v25",
        "",
        f"Decision: `{decision['conclusion']}`；未批准实盘。",
        f"Stability: `{decision['stability_label']}`。",
        "Data: 模型2015-04-16—2026-08-14；真实官方IM/MO 2022-07-22—2026-08-14。",
        "Execution: T日收盘判断；T+1官方收盘；正常入场IV26；救援移仓豁免IV；2张MO每边1bp。",
        "",
        "## 主要窗口",
        "",
        "|层|候选|窗口|可用|CAGR|MaxDD|Sharpe|",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in focus.itertuples(index=False):
        if bool(row.available):
            lines.append(f"|{row.layer}|{row.candidate}|{row.window}|是|{row.ann_return:.2%}|{row.max_dd:.2%}|{row.sharpe_repo:.3f}|")
        else:
            lines.append(f"|{row.layer}|{row.candidate}|{row.window}|否：历史不足|N/A|N/A|N/A|")
    lines.extend(
        [
            "", "## 真实逐年", "", annual[annual["layer"].eq("real")].to_markdown(index=False),
            "", "## 压力窗口", "", stress[stress["layer"].eq("real")].to_markdown(index=False),
            "", "## 事件与暴露", "", events.to_markdown(index=False),
            "", "## 预注册判定", "", decision_table.to_markdown(index=False),
            "", "## 执行统计", "", "```json", json.dumps(stats, ensure_ascii=False, indent=2), "```",
            "", "## 审计", "", "```json", json.dumps(audit, ensure_ascii=False, indent=2), "```",
            "", "本研究只验证受威胁救援机制，不包含TP80、分批止盈或风险度扩仓，不是交易建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def update_scan(
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    record: str,
    decision: dict[str, Any],
) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python im_mo_call_valuation_threat_roll_v25.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {
                "candidates": [CONTROL_0, FORMAL_0],
                "definition": "matched v23 no-TP control and formal PE20/60 lines",
            },
            "candidate_grid": [BASELINE, MONTHLY, DAILY_D10, CONTROL_0, FORMAL_0, CONTROL_2, FORMAL_2],
            "data_snapshot": {
                "model_start": str(v19.MODEL_START.date()),
                "real_start": str(v19.REAL_START.date()),
                "end": str(END.date()),
                "real_call_source": "official CFFEX daily archives",
                "valuation_state_source": str(V23_STATES.relative_to(ROOT)),
            },
            "cost_model": {
                "two_contract_call_basket_one_way": v19.CALL_BASKET_SIDE_COST,
                "cash_annual_return": 0.03,
                "execution": "T close signal, T+1 official close",
                "bid_ask_and_impact": "excluded",
            },
            "outputs": {
                "record": str(SCAN / "record.md"),
                "scan_summary": str(SCAN / "scan_summary.csv"),
                "window_metrics": str(SCAN / "window_metrics.csv"),
                "scan_meta": str(meta_path),
                "command_log": str(SCAN / "command_log.txt"),
            },
            "preliminary_decision": decision["conclusion"],
            "preliminary_stability_label": decision["stability_label"],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    states: pd.DataFrame,
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    stress: pd.DataFrame,
    events: pd.DataFrame,
    decision_table: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
    record: str,
    stats: dict[str, dict[str, int]],
    source_hashes: dict[str, str],
) -> None:
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "call_trades.csv", index=False)
    signals.to_csv(STAGING / "signals.csv", index=False)
    signals[signals["reason"].str.startswith("threat_")].to_csv(
        STAGING / "threat_signal_audit.csv", index=False
    )
    trades[trades["reason"].str.startswith("threat_")].to_csv(
        STAGING / "threat_trade_audit.csv", index=False
    )
    states.to_csv(STAGING / "formal_pe_states.csv.gz", index=False, compression="gzip")
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    stress.to_csv(STAGING / "stress_period_metrics.csv", index=False)
    events.to_csv(STAGING / "event_exposure_summary.csv", index=False)
    decision_table.to_csv(STAGING / "decision_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "execution_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "audit_summary.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text("python im_mo_call_valuation_threat_roll_v25.py\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
        "sample": {
            "model": [str(v19.MODEL_START.date()), str(END.date())],
            "real": [str(v19.REAL_START.date()), str(END.date())],
        },
        "execution": "T close signal, T+1 official close, frozen contract delayed if unavailable",
        "frictions": {
            "call_basket_one_way": v19.CALL_BASKET_SIDE_COST,
            "cash_annual": 0.03,
            "bid_ask_impact": "excluded",
        },
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sorted(STAGING.iterdir())
            if path.is_file()
        },
    }
    (STAGING / "output_manifest.json").write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING.replace(OUTPUT)


def main() -> None:
    source_hashes = verify_inputs()
    reference = v24.reference_daily()
    states = v24.formal_states()
    baseline = v19.load_baseline()
    upstream = v19.load_upstream()
    market, market_checks = v19.v6.model_market()
    market = market[market["date"].le(END)].copy()
    upstream = upstream[upstream["date"].le(END)].copy()
    real_market = market[market["date"].ge(v19.REAL_START)].copy()
    calls = v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    model_events = v19.monthly_events(v19.MODEL_START, model_dates, v19.model_roll_dates(model_dates))
    real_rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    real_events = v19.monthly_events(v19.REAL_START, real_dates, real_rolls)
    model_base = baseline[baseline["layer"].eq("model") & baseline["date"].le(END)].drop(columns=["layer", "candidate"])
    real_base = baseline[baseline["layer"].eq("real") & baseline["date"].le(END)].drop(columns=["layer", "candidate"])
    daily_parts = [reference]
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    stats: dict[str, dict[str, int]] = {}
    for label, force_normal in [(CONTROL_2, True), (FORMAL_2, False)]:
        model_overlay, model_trades, model_signals, model_stats = run_model(
            market, model_events, states, label, force_normal
        )
        real_overlay, real_trades, real_signals, real_stats = run_real(
            upstream, calls, real_market, real_events, states, label, force_normal
        )
        model_candidate = v19.assemble_candidate(model_base, model_overlay, label)
        model_candidate["layer"] = "model"
        real_candidate = v19.assemble_candidate(real_base, real_overlay, label)
        real_candidate["layer"] = "real"
        daily_parts.extend([model_candidate, real_candidate])
        trade_parts.extend([model_trades, real_trades])
        signal_parts.extend([model_signals, real_signals])
        stats[label] = {
            "final_pending": model_stats["final_pending"] + real_stats["final_pending"],
            "scheduled_execution_failures": model_stats["scheduled_execution_failures"] + real_stats["scheduled_execution_failures"],
            "delayed_trading_days": model_stats["delayed_trading_days"] + real_stats["delayed_trading_days"],
            "model_threat_signals": model_stats["threat_signals"],
            "real_threat_signals": real_stats["threat_signals"],
            "model_threat_rolls": model_stats["threat_rolls"],
            "real_threat_rolls": real_stats["threat_rolls"],
            "model_no_contract_stops": model_stats["threat_no_contract_stops"],
            "real_no_contract_stops": real_stats["threat_no_contract_stops"],
            "model_max5_stops": model_stats["threat_max5_stops"],
            "real_max5_stops": real_stats["threat_max5_stops"],
            "model_blocked_days": model_stats["blocked_days"],
            "real_blocked_days": real_stats["blocked_days"],
            "model_reenable_events": model_stats["reenable_events"],
            "real_reenable_events": real_stats["reenable_events"],
            "model_max_consecutive_threat_rolls": model_stats["max_consecutive_threat_rolls"],
            "real_max_consecutive_threat_rolls": real_stats["max_consecutive_threat_rolls"],
        }
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"]).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["layer", "candidate", "actual_execution_date"]).reset_index(drop=True)
    signals = pd.concat(signal_parts, ignore_index=True).sort_values(["layer", "candidate", "eval_date"]).reset_index(drop=True)
    formal, annual = v19.metrics_tables(daily)
    stress = v23.stress_table(daily)
    events = event_summary(daily, trades, signals)
    audit = audit_results(reference, daily, trades, signals, calls, states)
    audit["market_checks"] = market_checks
    decision_table, decision = decision_result(formal, events, stats, bool(audit["all_pass"]))
    scan_long, scan_wide = v23.scan_tables(formal)
    record = record_text(formal, annual, stress, events, decision_table, decision, audit, stats)
    update_scan(scan_long, scan_wide, record, decision)
    write_outputs(
        daily, trades, signals, states, formal, annual, stress, events,
        decision_table, decision, audit, record, stats, source_hashes,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
