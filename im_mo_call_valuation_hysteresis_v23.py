from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_daily_entry_profit_roll_v22 as v22


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_valuation_hysteresis_v23"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "5e130db2089e0d9df5f411b581d5bd098f05cbffa231aaa1ede77bc63eea8d74"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260819_new_strategy_research_im_mo_call_valuation_hysteresis_v23_im_mo_call_overwrite_article_pe20_60_hysteresis"
)
V22_OUTPUT = ROOT / "outputs" / "im_mo_call_daily_entry_profit_roll_v22"
V22_DAILY = V22_OUTPUT / "daily_candidates.csv.gz"
PE_SOURCE = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v4" / "csindex_000852.csv"

v19 = v22.v19
BASELINE = v22.BASELINE
MONTHLY = v22.MONTHLY
DAILY_D10 = v22.DAILY
CONTROL = "article_ladder_iv26_daily"
FORMAL = "article_pe20_60_hysteresis_iv26_daily"
BACKFILL = "article_pe20_60_hysteresis_iv26_daily_backfill_diag"
CANDIDATES = (CONTROL, FORMAL, BACKFILL)
IV_THRESHOLD = 0.26
LOW_ENTER = 0.20
LOW_EXIT = 0.60
FORMAL_HISTORY_START = pd.Timestamp("2014-10-17")
BACKFILL_HISTORY_START = pd.Timestamp("2012-06-29")
END = pd.Timestamp("2026-08-14")
NORMAL_TIERS = (
    (1, 30, 60, 0.15, 45.0),
    (2, 61, 90, 0.20, 75.0),
    (3, 91, 120, 0.25, 105.0),
)
LOW_TIERS = ((1, 30, 60, 0.25, 45.0),)

FROZEN_HASHES = {
    ROOT / "im_mo_call_daily_entry_profit_roll_v22.py": "18c90e87b4ec0714d9560865f751d4ebb2748d9dec00416d76f09a9651097932",
    V22_DAILY: "ab93649cde1c86de9f1ff6c30acc8297bf0d9b8e92c346f2a60459ca620a8c6c",
    V22_OUTPUT / "call_trades.csv": "4b5485b86c3352ded7827d5c67f5b59739e15f18baf2da4fcdd6d1a44b605923",
    V22_OUTPUT / "signals.csv": "21952c90df5d0af932b0e767a133d6bcd3c0f2ff640e7d372b9a100607e1e6b3",
    V22_OUTPUT / "data_manifest.json": "d898da8b079594baa9332270346980c1be6a51a6fc7040294983968cebd4d604",
    V22_OUTPUT / "output_manifest.json": "0caece179821cd570a88497c7e9f0002458717796a3cec9ee9cf76a75ff82bdd",
    PE_SOURCE: "2022d89da20cb4e81e63c82999ed1deb2488353199d3f40fa0f1f7d44401dd89",
    ROOT / "data" / "im_mo_call_data_build_v1" / "cffex_mo_calls.csv": "3c5bd3f5b4ca057a87fa8e0c0d1600980d773125b207b7d2c858500d2927f4c0",
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
        raise RuntimeError("Frozen v23 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v23 specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging v23 output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v23 parameter folder is missing")
    for path, expected in FROZEN_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v23 input changed: {path}")
    return {str(path.relative_to(ROOT)): expected for path, expected in FROZEN_HASHES.items()}


def reference_daily() -> pd.DataFrame:
    source = pd.read_csv(V22_DAILY, parse_dates=["date", "call_expiry"])
    keep = source[source["candidate"].isin([BASELINE, MONTHLY, DAILY_D10])].copy()
    expected = {"model": 3 * 2756, "real": 3 * 986}
    for layer, rows in expected.items():
        if len(keep[keep["layer"].eq(layer)]) != rows:
            raise RuntimeError(f"Unexpected v22 reference rows for {layer}")
    return keep.sort_values(["layer", "candidate", "date"]).reset_index(drop=True)


def build_pe_states(history_start: pd.Timestamp, history_kind: str) -> pd.DataFrame:
    source = pd.read_csv(PE_SOURCE, parse_dates=["date"]).sort_values("date")
    source = source[
        source["date"].between(history_start, END)
        & source["official_rolling_pe"].notna()
    ][["date", "official_rolling_pe"]].reset_index(drop=True)
    if source.empty or source.iloc[0]["date"] != history_start:
        raise RuntimeError(f"Unexpected PE start for {history_kind}")
    rows: list[dict[str, Any]] = []
    state = "normal"
    for item in source.itertuples(index=False):
        day = pd.Timestamp(item.date)
        left = max(history_start, day - pd.DateOffset(years=10))
        history = source[source["date"].between(left, day)]
        percentile = float(
            (history["official_rolling_pe"] <= float(item.official_rolling_pe)).mean()
        )
        prior_state = state
        if percentile <= LOW_ENTER + 1e-15:
            state = "low_recovery"
        elif percentile >= LOW_EXIT - 1e-15:
            state = "normal"
        rows.append(
            {
                "history_kind": history_kind,
                "date": day,
                "official_rolling_pe": float(item.official_rolling_pe),
                "pe_percentile_10y": percentile,
                "history_start": pd.Timestamp(history["date"].min()),
                "history_end": pd.Timestamp(history["date"].max()),
                "history_rows": int(len(history)),
                "valuation_state": state,
                "state_changed": state != prior_state,
                "state_from": prior_state,
                "state_to": state,
            }
        )
    return pd.DataFrame(rows)


def state_row(states: pd.DataFrame, day: pd.Timestamp, force_normal: bool) -> pd.Series:
    row = states.set_index("date").loc[day].copy()
    if force_normal:
        row["valuation_state"] = "normal"
    return row


def tiers_for_state(state: str) -> tuple[tuple[int, int, int, float, float], ...]:
    return LOW_TIERS if state == "low_recovery" else NORMAL_TIERS


def model_selection(
    market: pd.DataFrame,
    dates: pd.DatetimeIndex,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    label: str,
    reason: str,
    state: pd.Series,
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
    available["dte"] = (available["expiry"] - day).dt.days
    for number, low, high, min_otm, midpoint in tiers_for_state(
        str(state["valuation_state"])
    ):
        eligible = available[available["dte"].between(low, high)].copy()
        if eligible.empty:
            continue
        eligible["dte_distance"] = (eligible["dte"] - midpoint).abs()
        chosen = eligible.sort_values(["dte_distance", "expiry", "month"]).iloc[0]
        month = pd.Timestamp(chosen["month"])
        expiry = pd.Timestamp(chosen["expiry"])
        strike = spot * (1.0 + min_otm)
        years = (expiry - day).days / 365.0
        sigma = float(row["sigma_close"])
        delta = v19.bs_call_delta(
            spot,
            strike,
            float(row["rate_close"]),
            float(row["dividend_close"]),
            sigma,
            years,
        )
        close = v19.bs_call(
            spot,
            strike,
            float(row["rate_close"]),
            float(row["dividend_close"]),
            sigma,
            years,
        )
        selection = v19.Selection(
            "model",
            label,
            f"tier{number}",
            delta,
            reason,
            day,
            execution,
            f"MODEL_L{number}_{month:%y%m}_{strike:.6f}",
            month,
            expiry,
            strike,
            delta,
            sigma,
            close,
            np.nan,
            np.nan,
        )
        return selection, selection_meta(
            selection, state, number, low, high, min_otm, int(chosen["dte"]), spot
        )
    return None, selection_meta(None, state, 0, np.nan, np.nan, np.nan, np.nan, spot)


def real_selection(
    calls: pd.DataFrame,
    market_row: pd.Series,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    label: str,
    reason: str,
    state: pd.Series,
) -> tuple[v19.Selection | None, dict[str, Any]]:
    spot = float(market_row["spot_close"])
    chain = calls[calls["date"].eq(day)].copy()
    chain["dte"] = (chain["actual_expiry"] - day).dt.days
    chain["moneyness"] = chain["strike"] / spot - 1.0
    for number, low, high, min_otm, midpoint in tiers_for_state(
        str(state["valuation_state"])
    ):
        eligible = chain[
            chain["dte"].between(low, high)
            & chain["moneyness"].ge(min_otm - 1e-12)
            & chain["close"].gt(0)
            & chain["volume"].gt(0)
            & chain["open_interest"].gt(0)
        ]
        choices: list[dict[str, Any]] = []
        for quote in eligible.itertuples(index=False):
            years = int(quote.dte) / 365.0
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
                    "otm_excess": float(quote.moneyness) - min_otm,
                    "dte_distance": abs(float(quote.dte) - midpoint),
                }
            )
        if not choices:
            continue
        chosen = sorted(
            choices,
            key=lambda item: (
                item["otm_excess"],
                item["dte_distance"],
                -float(item["quote"].open_interest),
                -float(item["quote"].volume),
                pd.Timestamp(item["quote"].actual_expiry),
                float(item["quote"].strike),
                str(item["quote"].contract),
            ),
        )[0]
        quote = chosen["quote"]
        selection = v19.Selection(
            "real",
            label,
            f"tier{number}",
            float(chosen["delta"]),
            reason,
            day,
            execution,
            str(quote.contract),
            pd.Timestamp(quote.contract_month),
            pd.Timestamp(quote.actual_expiry),
            float(quote.strike),
            float(chosen["delta"]),
            float(chosen["iv"]),
            float(quote.close),
            float(quote.volume),
            float(quote.open_interest),
        )
        return selection, selection_meta(
            selection, state, number, low, high, min_otm, int(quote.dte), spot
        )
    return None, selection_meta(None, state, 0, np.nan, np.nan, np.nan, np.nan, spot)


def selection_meta(
    selection: v19.Selection | None,
    state: pd.Series,
    tier: int,
    dte_low: float,
    dte_high: float,
    min_otm: float,
    dte: float,
    spot: float,
) -> dict[str, Any]:
    strike = selection.strike if selection is not None else np.nan
    return {
        "history_kind": str(state["history_kind"]),
        "official_rolling_pe": float(state["official_rolling_pe"]),
        "pe_percentile_10y": float(state["pe_percentile_10y"]),
        "pe_history_rows": int(state["history_rows"]),
        "valuation_state": str(state["valuation_state"]),
        "state_changed": bool(state["state_changed"]),
        "tier": tier,
        "dte_low": dte_low,
        "dte_high": dte_high,
        "min_otm": min_otm,
        "dte": dte,
        "spot": spot,
        "moneyness": strike / spot - 1.0 if selection is not None else np.nan,
    }


def make_signal(
    layer: str,
    label: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    reason: str,
    active: bool,
    selection: v19.Selection | None,
    action: str,
    meta: dict[str, Any],
    old_expiry: pd.Timestamp | None,
) -> dict[str, Any]:
    iv = float(selection.implied_vol) if selection is not None else np.nan
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
        "gate_pass": bool(selection is not None and iv >= IV_THRESHOLD - 1e-12),
        "selection_delta": selection.selected_delta if selection is not None else np.nan,
        "selection_expiry": selection.expiry if selection is not None else pd.NaT,
        "selection_strike": selection.strike if selection is not None else np.nan,
        "eval_close": selection.eval_close if selection is not None else np.nan,
        "eval_volume": selection.eval_volume if selection is not None else np.nan,
        "eval_open_interest": selection.eval_open_interest if selection is not None else np.nan,
        "old_expiry": old_expiry if old_expiry is not None else pd.NaT,
        **meta,
    }


def pending_for(
    active: bool,
    selection: v19.Selection | None,
    reason: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    old_expiry: pd.Timestamp | None,
) -> v22.Pending | None:
    return v22.pending_from_selection(
        active,
        selection,
        reason,
        day,
        execution,
        old_expiry=old_expiry,
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
    cycle_id = 0
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        denominator = float(row.base_prior_close)
        pnl = cost = 0.0
        traded = False
        old_mark = np.nan
        if active is not None:
            old_mark, _ = v19.model_mark(
                v19.ModelPosition(
                    active.selection, active.units, active.prior_mark, active.cycle_id
                ),
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
                active = v22.ModelActive(
                    pending.selection, units, entry_mark, entry_mark, cycle_id
                )
                cost += v19.CALL_BASKET_SIDE_COST
            trades.append(
                trade_row("model", label, pending, day, old, active, old_mark, entry_mark, entry_mark, 0)
            )
            pending = None
            traded = True
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = state_row(states, day, force_normal)
            reason = "monthly" if event is not None else "daily_entry"
            evaluate = active is None or (
                event is not None
                and active.selection.expiry <= pd.Timestamp(event.current_expiry)
            )
            if evaluate:
                proposed, meta = model_selection(
                    market, dates, day, tomorrow, label, reason, state
                )
                gate = bool(proposed is not None and proposed.implied_vol >= IV_THRESHOLD - 1e-12)
                action = "roll" if active is not None and gate else "close" if active is not None else "open" if gate else "skip"
                signals.append(
                    make_signal(
                        "model", label, day, tomorrow, reason, active is not None,
                        proposed, action, meta,
                        active.selection.expiry if active is not None else None,
                    )
                )
                pending = pending_for(
                    active is not None, proposed, reason, day, tomorrow,
                    active.selection.expiry if active is not None else None,
                )
            elif event is not None and active is not None:
                meta = selection_meta(None, state, 0, np.nan, np.nan, np.nan, np.nan, float(row.spot_close))
                signals.append(make_signal("model", label, day, tomorrow, "monthly_keep_far", True, None, "keep_far", meta, active.selection.expiry))
        rows.append(model_daily_row(day, label, row, active, pnl, cost))
    if pending is not None:
        raise RuntimeError(f"Unexecuted final model action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), {"final_pending": 0, "scheduled_execution_failures": 0, "delayed_trading_days": 0}


def model_daily_row(
    day: pd.Timestamp,
    label: str,
    row: Any,
    active: v22.ModelActive | None,
    pnl: float,
    cost: float,
) -> dict[str, Any]:
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
            v19.ModelPosition(active.selection, active.units, active.prior_mark, active.cycle_id), row
        )
        active.prior_mark = mark
        coverage = active.units * float(row.spot_close) / float(row.tri_close)
        mark_fraction = active.units * mark / float(row.tri_close)
        margin_fraction = v19.call_margin_fraction(
            mark, float(row.spot_close), active.selection.strike, active.units, float(row.tri_close)
        )
        contract = active.selection.contract
        strike = active.selection.strike
        expiry = active.selection.expiry
        itm = float(row.spot_close) > strike
        active_cycle = active.cycle_id
        entry_close = active.entry_close
        if expiry <= day:
            raise RuntimeError(f"Model Call reached expiry: {label} {day.date()}")
    return {
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
    cycle_id = 0
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    scheduled_failures = delayed_days = 0
    for index, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(prior_im.iloc[index])
        market_row = market_lookup.loc[day]
        pnl = cost = 0.0
        traded = False
        old_quote = v19.quote_row(call_lookup, active.selection.contract, day) if active is not None else None
        new_quote = v19.quote_row(call_lookup, pending.selection.contract, day) if pending is not None and pending.selection is not None else None
        old_tradable = active is None or (
            old_quote is not None and float(old_quote["close"]) > 0 and float(old_quote["volume"]) > 0 and float(old_quote["open_interest"]) > 0
        )
        new_tradable = pending is None or pending.selection is None or (
            new_quote is not None and float(new_quote["close"]) > 0 and float(new_quote["volume"]) > 0 and float(new_quote["open_interest"]) > 0
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
                active = v22.RealActive(pending.selection, v19.MO_QTY, new_settle, new_close, cycle_id)
            delay = int(((dates > pending.scheduled_execution_date) & (dates <= day)).sum())
            delayed_days += delay
            trades.append(trade_row("real", label, pending, day, old, active, old_close, new_close, new_settle, delay))
            pending = None
            traded = True
        elif pending is not None and day == pending.scheduled_execution_date:
            scheduled_failures += 1
        if not traded and active is not None:
            if old_quote is None or float(old_quote["settle"]) <= 0:
                raise RuntimeError(f"Missing real settlement: {label} {day.date()}")
            pnl += active.qty * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER * (active.prior_settle - float(old_quote["settle"])) / denominator
            active.prior_settle = float(old_quote["settle"])
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = state_row(states, day, force_normal)
            reason = "monthly" if event is not None else "daily_entry"
            evaluate = active is None or (
                event is not None
                and active.selection.expiry <= pd.Timestamp(event.current_expiry)
            )
            if evaluate:
                proposed, meta = real_selection(calls, market_row, day, tomorrow, label, reason, state)
                gate = bool(proposed is not None and proposed.implied_vol >= IV_THRESHOLD - 1e-12)
                action = "roll" if active is not None and gate else "close" if active is not None else "open" if gate else "skip"
                signals.append(make_signal("real", label, day, tomorrow, reason, active is not None, proposed, action, meta, active.selection.expiry if active is not None else None))
                pending = pending_for(active is not None, proposed, reason, day, tomorrow, active.selection.expiry if active is not None else None)
            elif event is not None and active is not None:
                meta = selection_meta(None, state, 0, np.nan, np.nan, np.nan, np.nan, float(market_row["spot_close"]))
                signals.append(make_signal("real", label, day, tomorrow, "monthly_keep_far", True, None, "keep_far", meta, active.selection.expiry))
        rows.append(real_daily_row(day, label, base, market_row, call_lookup, active, pnl, cost))
    if pending is not None:
        raise RuntimeError(f"Unexecuted final real action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), {"final_pending": 0, "scheduled_execution_failures": scheduled_failures, "delayed_trading_days": delayed_days}


def real_daily_row(
    day: pd.Timestamp,
    label: str,
    base: pd.Series,
    market_row: pd.Series,
    call_lookup: pd.DataFrame,
    active: v22.RealActive | None,
    pnl: float,
    cost: float,
) -> dict[str, Any]:
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
        units = active.qty * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER
        coverage = units * float(market_row["spot_close"]) / float(base["settle"])
        mark_fraction = units * float(quote["settle"]) / float(base["settle"])
        margin_fraction = v19.call_margin_fraction(float(quote["settle"]), float(market_row["spot_close"]), active.selection.strike, units, float(base["settle"]))
        call_delta = v19.real_daily_delta(quote, active.selection, market_row, day)
        contract = active.selection.contract
        strike = active.selection.strike
        expiry = active.selection.expiry
        itm = float(market_row["spot_close"]) > strike
        active_cycle = active.cycle_id
        entry_close = active.entry_close
        if expiry <= day:
            raise RuntimeError(f"Real Call reached expiry: {label} {day.date()}")
    return {
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


def trade_row(
    layer: str,
    label: str,
    pending: v22.Pending,
    day: pd.Timestamp,
    old: Any,
    active: Any,
    old_close: float,
    new_close: float,
    new_settle: float,
    delay: int,
) -> dict[str, Any]:
    return {
        "layer": layer,
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
        "old_entry_close": old.entry_close if old is not None else np.nan,
        "old_close": old_close,
        "new_close": new_close,
        "new_settle": new_settle,
        "delay_trading_days": delay,
        "cycle_id": active.cycle_id if active is not None else 0,
    }


def stress_table(daily: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "2022_partial": (pd.Timestamp("2022-07-22"), pd.Timestamp("2022-12-30")),
        "rebound_2024_0918_1008": (pd.Timestamp("2024-09-18"), pd.Timestamp("2024-10-08")),
        "2025": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        "2026_ytd": (pd.Timestamp("2026-01-01"), END),
    }
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        for period, (start, end) in periods.items():
            sample = group[group["date"].between(start, end)].sort_values("date")
            if sample.empty:
                continue
            metrics = v19.metrics(sample["cash_ret"])
            rows.append({"layer": layer, "candidate": candidate, "period": period, "start": sample["date"].min(), "end": sample["date"].max(), "rows": len(sample), **metrics})
    return pd.DataFrame(rows)


def event_summary(daily: pd.DataFrame, trades: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        t = trades[(trades["layer"].eq(layer)) & trades["candidate"].eq(candidate)] if len(trades) else trades
        s = signals[(signals["layer"].eq(layer)) & signals["candidate"].eq(candidate)] if len(signals) else signals
        rows.append({
            "layer": layer,
            "candidate": candidate,
            "signals": len(s),
            "daily_checks": int(s["reason"].eq("daily_entry").sum()) if len(s) else 0,
            "iv_pass_signals": int(s["gate_pass"].astype(bool).sum()) if len(s) else 0,
            "low_state_signals": int(s["valuation_state"].eq("low_recovery").sum()) if len(s) else 0,
            "tier1_signals": int(s["tier"].eq(1).sum()) if len(s) else 0,
            "tier2_signals": int(s["tier"].eq(2).sum()) if len(s) else 0,
            "tier3_signals": int(s["tier"].eq(3).sum()) if len(s) else 0,
            "open_events": int(t["action"].eq("open").sum()) if len(t) else 0,
            "roll_events": int(t["action"].eq("roll").sum()) if len(t) else 0,
            "close_events": int(t["action"].eq("close").sum()) if len(t) else 0,
            "call_days": int(group["call_contract"].fillna("").ne("").sum()),
            "call_day_ratio": float(group["call_contract"].fillna("").ne("").mean()),
            "call_pnl_sum": float(group["call_pnl_ret"].sum()),
            "call_cost_sum": float(group["call_cost_rate"].sum()),
            "average_margin_fraction": float(group["call_margin_fraction"].mean()),
            "maximum_margin_fraction": float(group["call_margin_fraction"].max()),
            "capital_breach_days": int((group["put_mark_fraction"] + group["call_margin_fraction"] > v19.CASH_BASE + 1e-12).sum()),
        })
    return pd.DataFrame(rows)


def signal_differences(signals: pd.DataFrame) -> pd.DataFrame:
    real = signals[signals["layer"].eq("real")].copy()
    fields = ["eval_date", "reason", "action", "contract", "valuation_state", "tier", "min_otm", "gate_pass"]
    a = real[real["candidate"].eq(CONTROL)][fields].rename(columns={c: f"control_{c}" for c in fields if c not in ["eval_date"]})
    b = real[real["candidate"].eq(FORMAL)][fields].rename(columns={c: f"formal_{c}" for c in fields if c not in ["eval_date"]})
    merged = a.merge(b, on="eval_date", how="outer", validate="one_to_one", indicator=True)
    left = merged["control_action"].fillna("missing") + "|" + merged["control_contract"].fillna("")
    right = merged["formal_action"].fillna("missing") + "|" + merged["formal_contract"].fillna("")
    merged["different_decision"] = left.ne(right)
    return merged.sort_values("eval_date").reset_index(drop=True)


def metric_value(formal: pd.DataFrame, layer: str, candidate: str, window: str, column: str) -> float:
    return v19.metric_value(formal, layer, candidate, window, column)


def audit_results(
    reference: pd.DataFrame,
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    calls: pd.DataFrame,
    formal_states: pd.DataFrame,
    backfill_states: pd.DataFrame,
) -> dict[str, Any]:
    parity: dict[str, dict[str, float]] = {}
    for candidate in [BASELINE, MONTHLY, DAILY_D10]:
        left = reference[reference["candidate"].eq(candidate)].sort_values(["layer", "date"])
        right = daily[daily["candidate"].eq(candidate)].sort_values(["layer", "date"])
        parity[candidate] = {column: float(np.max(np.abs(left[column].to_numpy() - right[column].to_numpy()))) for column in ["ret", "cash_ret", "nav", "cash_nav"]}
    candidates = daily[daily["candidate"].isin(CANDIDATES)]
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
    selected = signals[signals["contract"].fillna("").ne("")]
    gate_errors = int((selected["gate_pass"].astype(bool) != selected["gate_iv"].ge(IV_THRESHOLD - 1e-12)).sum())
    tier_errors = int((~selected["dte"].between(selected["dte_low"], selected["dte_high"]) | selected["moneyness"].lt(selected["min_otm"] - 1e-12)).sum())
    low_rule_errors = int((selected["valuation_state"].eq("low_recovery") & selected["min_otm"].ne(0.25)).sum())
    causality = int((signals["eval_date"] >= signals["scheduled_execution_date"]).sum() + (trades["eval_date"] >= trades["actual_execution_date"]).sum() + (trades["actual_execution_date"] < trades["scheduled_execution_date"]).sum())
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
    formal_prepublication = int((formal_states["history_start"] < FORMAL_HISTORY_START).sum())
    formal_future = int((formal_states["history_end"] > formal_states["date"]).sum())
    backfill_start_ok = bool(backfill_states["history_start"].min() == BACKFILL_HISTORY_START)
    result = {
        "reference_parity_max_abs": parity,
        "return_identity_max_abs": float((candidates["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((candidates["cash_ret"] - expected_cash).abs().max()),
        "gate_formula_errors": gate_errors,
        "tier_rule_errors": tier_errors,
        "low_state_rule_errors": low_rule_errors,
        "causality_failures": causality,
        "official_close_max_abs_error": max(close_errors) if close_errors else 0.0,
        "formal_prepublication_rows": formal_prepublication,
        "formal_future_pe_rows": formal_future,
        "backfill_start_ok": backfill_start_ok,
    }
    result["all_pass"] = bool(
        max(max(values.values()) for values in parity.values()) <= 1e-15
        and result["return_identity_max_abs"] <= 3e-15
        and result["cash_identity_max_abs"] <= 1e-15
        and gate_errors == 0 and tier_errors == 0 and low_rule_errors == 0
        and causality == 0 and result["official_close_max_abs_error"] <= 1e-12
        and formal_prepublication == 0 and formal_future == 0 and backfill_start_ok
    )
    return result


def decision_result(
    formal: pd.DataFrame,
    stress: pd.DataFrame,
    differences: pd.DataFrame,
    events: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    audit_ok: bool,
    daily: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    values: dict[str, Any] = {}
    for window in ["full", "last_3y", "last_1y"]:
        values[f"real_{window}_ann_delta"] = metric_value(formal, "real", FORMAL, window, "ann_return") - metric_value(formal, "real", CONTROL, window, "ann_return")
        values[f"real_{window}_maxdd_improvement"] = metric_value(formal, "real", FORMAL, window, "max_dd") - metric_value(formal, "real", CONTROL, window, "max_dd")
    def stress_return(candidate: str, period: str) -> float:
        row = stress[(stress["layer"].eq("real")) & stress["candidate"].eq(candidate) & stress["period"].eq(period)]
        return float(row.iloc[0]["total_return"])
    values["rebound_2024_improvement"] = stress_return(FORMAL, "rebound_2024_0918_1008") - stress_return(CONTROL, "rebound_2024_0918_1008")
    values["partial_2022_improvement"] = stress_return(FORMAL, "2022_partial") - stress_return(CONTROL, "2022_partial")
    values["different_real_decisions"] = int(differences["different_decision"].sum())
    formal_real = daily[(daily["layer"].eq("real")) & daily["candidate"].eq(FORMAL)].sort_values("date")
    backfill_real = daily[(daily["layer"].eq("real")) & daily["candidate"].eq(BACKFILL)].sort_values("date")
    values["formal_backfill_position_difference_days"] = int((formal_real["call_contract"].to_numpy() != backfill_real["call_contract"].to_numpy()).sum())
    values["capital_breach_days"] = int(events[(events["layer"].eq("real")) & events["candidate"].eq(FORMAL)].iloc[0]["capital_breach_days"])
    values["final_pending"] = int(stats[FORMAL]["final_pending"])
    return_gate = values["real_full_ann_delta"] >= -0.01 - 1e-12 and values["real_last_3y_ann_delta"] >= -0.01 - 1e-12 and values["real_last_1y_ann_delta"] >= -0.03 - 1e-12
    risk_gate = values["real_full_maxdd_improvement"] > 0 and values["real_last_3y_maxdd_improvement"] >= -0.02 - 1e-12 and values["real_last_1y_maxdd_improvement"] >= -0.02 - 1e-12
    stress_gate = values["rebound_2024_improvement"] >= 0.10 - 1e-12 and values["partial_2022_improvement"] >= -1e-12
    event_gate = values["different_real_decisions"] >= 2
    execution_gate = values["capital_breach_days"] == 0 and values["final_pending"] == 0
    if audit_ok and return_gate and risk_gate and stress_gate and event_gate and execution_gate:
        conclusion = "valuation_hysteresis_supported_real_short_sample"
        stability = "official_real_short_sample_supported"
    elif audit_ok and return_gate and risk_gate and event_gate and execution_gate:
        conclusion = "valuation_hysteresis_not_material"
        stability = "stress_improvement_insufficient"
    elif audit_ok and stress_gate and event_gate and execution_gate:
        conclusion = "stress_tradeoff_only"
        stability = "return_tolerance_failed"
    else:
        conclusion = "not_supported"
        stability = "not_supported"
    row = {**values, "return_gate": return_gate, "risk_gate": risk_gate, "stress_gate": stress_gate, "event_gate": event_gate, "execution_gate": execution_gate, "audit_gate": audit_ok, "hard_pass": conclusion == "valuation_hysteresis_supported_real_short_sample"}
    sensitive = values["formal_backfill_position_difference_days"] > 0
    return pd.DataFrame([row]), {
        "conclusion": conclusion,
        "selected_candidate": FORMAL if conclusion == "valuation_hysteresis_supported_real_short_sample" else CONTROL,
        "stability_label": stability,
        "prepublication_history_sensitive": sensitive,
        "live_approved": False,
        "research_status": "official_real_short_sample_mechanism_only_not_live_approved",
    }


def state_intervals(states: pd.DataFrame) -> pd.DataFrame:
    frame = states.copy().sort_values(["history_kind", "date"])
    frame["segment"] = frame.groupby("history_kind")["valuation_state"].transform(lambda s: s.ne(s.shift()).cumsum())
    return frame.groupby(["history_kind", "segment", "valuation_state"], as_index=False).agg(start=("date", "min"), end=("date", "max"), days=("date", "size"), start_percentile=("pe_percentile_10y", "first"), end_percentile=("pe_percentile_10y", "last"))


def scan_tables(formal: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return v22.scan_tables(formal)


def record_text(
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    stress: pd.DataFrame,
    events: pd.DataFrame,
    intervals: pd.DataFrame,
    decision_table: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    focus = formal[formal["window"].isin(["full", "last_10y", "last_5y", "last_3y", "last_1y"])]
    lines = [
        "# IM + MO Call 十年PE估值滞回 v23",
        "",
        f"Decision: `{decision['conclusion']}`；未批准实盘。",
        f"Stability: `{decision['stability_label']}`。",
        f"Pre-publication sensitivity: `{decision['prepublication_history_sensitive']}`。",
        "Data: 模型2015-04-16—2026-08-14；真实官方IM/MO 2022-07-22—2026-08-14。",
        "Execution: T日收盘信号，T+1官方收盘成交；2张MO每边1bp；现金余额净年化3%。",
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
    lines.extend(["", "## 真实逐年", "", annual[annual["layer"].eq("real")].to_markdown(index=False), "", "## 压力窗口", "", stress[stress["layer"].eq("real")].to_markdown(index=False), "", "## 交易与暴露", "", events.to_markdown(index=False), "", "## PE状态区间", "", intervals.to_markdown(index=False), "", "## 判定", "", decision_table.to_markdown(index=False), "", "## 审计", "", "```json", json.dumps(audit, ensure_ascii=False, indent=2), "```", "", "本研究仅验证文章PE20/60估值滞回，不包含止盈、受威胁移仓或风险度扩仓，不是交易建议。"])
    return "\n".join(lines) + "\n"


def update_scan(scan_long: pd.DataFrame, scan_wide: pd.DataFrame, record: str, decision: dict[str, Any]) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python im_mo_call_valuation_hysteresis_v23.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({
        "baseline": {"candidate": CONTROL, "definition": "same daily article ladder without valuation hysteresis"},
        "candidate_grid": [BASELINE, MONTHLY, DAILY_D10, CONTROL, FORMAL, BACKFILL],
        "data_snapshot": {"model_start": str(v19.MODEL_START.date()), "real_start": str(v19.REAL_START.date()), "end": str(END.date()), "real_call_source": "official CFFEX daily archives", "pe_source": str(PE_SOURCE.relative_to(ROOT))},
        "cost_model": {"two_contract_call_basket_one_way": v19.CALL_BASKET_SIDE_COST, "cash_annual_return": 0.03, "execution": "T close signal, T+1 official close"},
        "outputs": {"record": str(SCAN / "record.md"), "scan_summary": str(SCAN / "scan_summary.csv"), "window_metrics": str(SCAN / "window_metrics.csv"), "scan_meta": str(meta_path), "command_log": str(SCAN / "command_log.txt")},
        "preliminary_decision": decision["conclusion"],
        "preliminary_stability_label": decision["stability_label"],
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    states: pd.DataFrame,
    intervals: pd.DataFrame,
    differences: pd.DataFrame,
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
    states.to_csv(STAGING / "pe_valuation_states.csv.gz", index=False, compression="gzip")
    intervals.to_csv(STAGING / "pe_state_intervals.csv", index=False)
    differences.to_csv(STAGING / "control_formal_signal_differences.csv", index=False)
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    stress.to_csv(STAGING / "stress_period_metrics.csv", index=False)
    events.to_csv(STAGING / "event_exposure_summary.csv", index=False)
    decision_table.to_csv(STAGING / "decision_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "execution_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "audit_summary.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text("python im_mo_call_valuation_hysteresis_v23.py\n", encoding="utf-8")
    manifest = {"version": VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "spec_sha256": SPEC_SHA256, "script_sha256": sha256(Path(__file__)), "source_hashes": source_hashes, "git_commit": git_value("rev-parse", "HEAD"), "git_status": git_value("status", "--short"), "sample": {"model": [str(v19.MODEL_START.date()), str(END.date())], "real": [str(v19.REAL_START.date()), str(END.date())]}, "execution": "T close signal, T+1 official close, delayed frozen contract if unavailable", "frictions": {"call_basket_one_way": v19.CALL_BASKET_SIDE_COST, "cash_annual": 0.03, "bid_ask_impact": "excluded"}}
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_manifest = {"version": VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "files": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(STAGING.iterdir()) if path.is_file()}}
    (STAGING / "output_manifest.json").write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING.replace(OUTPUT)


def main() -> None:
    source_hashes = verify_inputs()
    reference = reference_daily()
    formal_states = build_pe_states(FORMAL_HISTORY_START, "postpublication_formal")
    backfill_states = build_pe_states(BACKFILL_HISTORY_START, "prepublication_backfill_diagnostic")
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
    model_base = baseline[(baseline["layer"].eq("model")) & baseline["date"].le(END)].drop(columns=["layer", "candidate"])
    real_base = baseline[(baseline["layer"].eq("real")) & baseline["date"].le(END)].drop(columns=["layer", "candidate"])
    daily_parts = [reference]
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    stats: dict[str, dict[str, int]] = {}
    for label, states, force_normal in [(CONTROL, formal_states, True), (FORMAL, formal_states, False), (BACKFILL, backfill_states, False)]:
        model_overlay, model_trades, model_signals, model_stats = run_model(market, model_events, states, label, force_normal)
        real_overlay, real_trades, real_signals, real_stats = run_real(upstream, calls, real_market, real_events, states, label, force_normal)
        model_candidate = v19.assemble_candidate(model_base, model_overlay, label)
        model_candidate["layer"] = "model"
        real_candidate = v19.assemble_candidate(real_base, real_overlay, label)
        real_candidate["layer"] = "real"
        daily_parts.extend([model_candidate, real_candidate])
        trade_parts.extend([model_trades, real_trades])
        signal_parts.extend([model_signals, real_signals])
        stats[label] = {"final_pending": model_stats["final_pending"] + real_stats["final_pending"], "scheduled_execution_failures": model_stats["scheduled_execution_failures"] + real_stats["scheduled_execution_failures"], "delayed_trading_days": model_stats["delayed_trading_days"] + real_stats["delayed_trading_days"]}
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"]).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(["layer", "candidate", "actual_execution_date"]).reset_index(drop=True)
    signals = pd.concat(signal_parts, ignore_index=True).sort_values(["layer", "candidate", "eval_date"]).reset_index(drop=True)
    formal, annual = v19.metrics_tables(daily)
    stress = stress_table(daily)
    events = event_summary(daily, trades, signals)
    differences = signal_differences(signals)
    states = pd.concat([formal_states, backfill_states], ignore_index=True)
    intervals = state_intervals(states)
    audit = audit_results(reference, daily, trades, signals, calls, formal_states, backfill_states)
    audit["market_checks"] = market_checks
    decision_table, decision = decision_result(formal, stress, differences, events, stats, bool(audit["all_pass"]), daily)
    scan_long, scan_wide = scan_tables(formal)
    record = record_text(formal, annual, stress, events, intervals, decision_table, decision, audit)
    update_scan(scan_long, scan_wide, record, decision)
    write_outputs(daily, trades, signals, states, intervals, differences, formal, annual, stress, events, decision_table, decision, audit, record, stats, source_hashes)
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
