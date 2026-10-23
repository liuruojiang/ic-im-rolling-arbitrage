from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_valuation_hysteresis_v23 as v23


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_valuation_profit_roll_v24"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "19b7386d1a3d9b1c7d3a3cc8d240bfbac7fe23f2d4f34b5a6484302bdac78e28"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260819_new_strategy_research_im_mo_call_valuation_profit_roll_v24_im_mo_call_overwrite_tp80_by_valuation_state"
)
V23_OUTPUT = ROOT / "outputs" / "im_mo_call_valuation_hysteresis_v23"
V23_DAILY = V23_OUTPUT / "daily_candidates.csv.gz"
V23_STATES = V23_OUTPUT / "pe_valuation_states.csv.gz"

v22 = v23.v22
v19 = v23.v19
BASELINE = v23.BASELINE
MONTHLY = v23.MONTHLY
DAILY_D10 = v23.DAILY_D10
CONTROL_0 = v23.CONTROL
FORMAL_0 = v23.FORMAL
CONTROL_1 = "article_ladder_iv26_daily_tp80"
FORMAL_1 = "article_pe20_60_hysteresis_iv26_daily_tp80"
CANDIDATES = (CONTROL_1, FORMAL_1)
IV_THRESHOLD = 0.26
TP_REMAINING_RATIO = 0.20
END = v23.END

FROZEN_HASHES = {
    ROOT / "im_mo_call_valuation_hysteresis_v23.py": "7266ad401ff0ec2e6bc4d9f4fc417cb1f8c3cd5d1e5673475d452af532d060cf",
    ROOT / "docs" / "im_mo_call_valuation_hysteresis_v23_spec.md": "5e130db2089e0d9df5f411b581d5bd098f05cbffa231aaa1ede77bc63eea8d74",
    V23_DAILY: "d43ca9503b45fbf5b0d473d5f5a627d6ade07f0230e8165554c5f17304984225",
    V23_OUTPUT / "call_trades.csv": "3c87481dfe2b16b72440908a815807b543d4a578e284b0f92de0484d95a09d24",
    V23_OUTPUT / "signals.csv": "3076c482a52ccf793a81cea5a842f37c647beff04b19781f6ed90c10465ea833",
    V23_STATES: "001bed38a4ab04e18d7d8e5173bfd6c7395d0552074ed6f3813b8d9a8c33bb28",
    V23_OUTPUT / "data_manifest.json": "cbbdf18af8c0de5a01a482d54b1fc13d050e32d39f03bf56c737172787ca5fe9",
    V23_OUTPUT / "output_manifest.json": "c1fa7a3be0c88f294a3e3d40beffedba19aa0744bd186ef23c813fb49cb3741c",
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
        raise RuntimeError("Frozen v24 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v24 specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging v24 output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v24 parameter folder is missing")
    for path, expected in FROZEN_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v24 input changed: {path}")
    return {str(path.relative_to(ROOT)): expected for path, expected in FROZEN_HASHES.items()}


def reference_daily() -> pd.DataFrame:
    source = pd.read_csv(V23_DAILY, parse_dates=["date", "call_expiry"])
    keep_names = [BASELINE, MONTHLY, DAILY_D10, CONTROL_0, FORMAL_0]
    keep = source[source["candidate"].isin(keep_names)].copy()
    for layer, rows in {"model": 5 * 2756, "real": 5 * 986}.items():
        if len(keep[keep["layer"].eq(layer)]) != rows:
            raise RuntimeError(f"Unexpected v23 reference rows for {layer}")
    return keep.sort_values(["layer", "candidate", "date"]).reset_index(drop=True)


def formal_states() -> pd.DataFrame:
    states = pd.read_csv(
        V23_STATES,
        parse_dates=["date", "history_start", "history_end"],
    )
    states = states[states["history_kind"].eq("postpublication_formal")].copy()
    if states.empty or states["date"].min() != v23.FORMAL_HISTORY_START:
        raise RuntimeError("Missing frozen formal PE states")
    return states.sort_values("date").reset_index(drop=True)


def model_far_selection(
    market: pd.DataFrame,
    dates: pd.DatetimeIndex,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    active_expiry: pd.Timestamp,
    label: str,
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
    available = available[available["expiry"].gt(active_expiry)].copy()
    for number, low, high, min_otm, midpoint in v23.tiers_for_state(
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
            "tp80",
            day,
            execution,
            f"MODEL_TP_L{number}_{month:%y%m}_{strike:.6f}",
            month,
            expiry,
            strike,
            delta,
            sigma,
            close,
            np.nan,
            np.nan,
        )
        return selection, v23.selection_meta(
            selection,
            state,
            number,
            low,
            high,
            min_otm,
            int(chosen["dte"]),
            spot,
        )
    return None, v23.selection_meta(
        None, state, 0, np.nan, np.nan, np.nan, np.nan, spot
    )


def real_far_selection(
    calls: pd.DataFrame,
    market_row: pd.Series,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    active_expiry: pd.Timestamp,
    label: str,
    state: pd.Series,
) -> tuple[v19.Selection | None, dict[str, Any]]:
    spot = float(market_row["spot_close"])
    chain = calls[
        calls["date"].eq(day) & calls["actual_expiry"].gt(active_expiry)
    ].copy()
    chain["dte"] = (chain["actual_expiry"] - day).dt.days
    chain["moneyness"] = chain["strike"] / spot - 1.0
    for number, low, high, min_otm, midpoint in v23.tiers_for_state(
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
            "tp80",
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
        return selection, v23.selection_meta(
            selection,
            state,
            number,
            low,
            high,
            min_otm,
            int(quote.dte),
            spot,
        )
    return None, v23.selection_meta(
        None, state, 0, np.nan, np.nan, np.nan, np.nan, spot
    )


def signal_row(
    layer: str,
    label: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    reason: str,
    active: Any,
    selection: v19.Selection | None,
    action: str,
    meta: dict[str, Any],
    remaining_ratio: float = np.nan,
) -> dict[str, Any]:
    row = v23.make_signal(
        layer,
        label,
        day,
        execution,
        reason,
        active is not None,
        selection,
        action,
        meta,
        active.selection.expiry if active is not None else None,
    )
    row.update(
        {
            "remaining_price_ratio": remaining_ratio,
            "old_contract": active.selection.contract if active is not None else "",
            "old_entry_close": active.entry_close if active is not None else np.nan,
        }
    )
    return row


def pending_for(
    active: bool,
    selection: v19.Selection | None,
    reason: str,
    day: pd.Timestamp,
    execution: pd.Timestamp,
    remaining_ratio: float = np.nan,
    old_expiry: pd.Timestamp | None = None,
) -> v22.Pending | None:
    return v22.pending_from_selection(
        active,
        selection,
        reason,
        day,
        execution,
        remaining_ratio,
        old_expiry,
    )


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
    row["remaining_price_ratio"] = pending.remaining_ratio
    return row


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
    tp_opportunities = tp_no_far = tp_iv_failures = 0
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        denominator = float(row.base_prior_close)
        pnl = cost = 0.0
        traded = False
        old_mark = np.nan
        if active is not None:
            old_mark, _ = v19.model_mark(
                v19.ModelPosition(
                    active.selection,
                    active.units,
                    active.prior_mark,
                    active.cycle_id,
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
                shell = v19.ModelPosition(
                    pending.selection, units, 0.0, cycle_id + 1
                )
                entry_mark, _ = v19.model_mark(shell, row)
                cycle_id += 1
                active = v22.ModelActive(
                    pending.selection, units, entry_mark, entry_mark, cycle_id
                )
                cost += v19.CALL_BASKET_SIDE_COST
            trades.append(
                trade_row(
                    "model",
                    label,
                    pending,
                    day,
                    old,
                    active,
                    old_mark,
                    entry_mark,
                    entry_mark,
                    0,
                )
            )
            pending = None
            traded = True
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = v23.state_row(states, day, force_normal)
            if event is not None:
                must_roll = active is None or active.selection.expiry <= pd.Timestamp(
                    event.current_expiry
                )
                if must_roll:
                    proposed, meta = v23.model_selection(
                        market, dates, day, tomorrow, label, "monthly", state
                    )
                    gate = bool(
                        proposed is not None
                        and proposed.implied_vol >= IV_THRESHOLD - 1e-12
                    )
                    action = (
                        "roll"
                        if active is not None and gate
                        else "close"
                        if active is not None
                        else "open"
                        if gate
                        else "skip"
                    )
                    signals.append(
                        signal_row(
                            "model",
                            label,
                            day,
                            tomorrow,
                            "monthly",
                            active,
                            proposed,
                            action,
                            meta,
                        )
                    )
                    pending = pending_for(
                        active is not None,
                        proposed,
                        "monthly",
                        day,
                        tomorrow,
                        old_expiry=active.selection.expiry
                        if active is not None
                        else None,
                    )
                else:
                    meta = v23.selection_meta(
                        None,
                        state,
                        0,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        float(row.spot_close),
                    )
                    signals.append(
                        signal_row(
                            "model",
                            label,
                            day,
                            tomorrow,
                            "monthly_keep_far",
                            active,
                            None,
                            "keep_far",
                            meta,
                        )
                    )
            elif active is None:
                proposed, meta = v23.model_selection(
                    market, dates, day, tomorrow, label, "daily_entry", state
                )
                gate = bool(
                    proposed is not None
                    and proposed.implied_vol >= IV_THRESHOLD - 1e-12
                )
                signals.append(
                    signal_row(
                        "model",
                        label,
                        day,
                        tomorrow,
                        "daily_entry",
                        None,
                        proposed,
                        "open" if gate else "skip",
                        meta,
                    )
                )
                pending = pending_for(
                    False, proposed, "daily_entry", day, tomorrow
                )
            else:
                ratio = (
                    old_mark / active.entry_close
                    if active.entry_close > 0
                    else np.nan
                )
                if ratio <= TP_REMAINING_RATIO + 1e-12:
                    tp_opportunities += 1
                    proposed, meta = model_far_selection(
                        market,
                        dates,
                        day,
                        tomorrow,
                        active.selection.expiry,
                        label,
                        state,
                    )
                    if proposed is None:
                        tp_no_far += 1
                    gate = bool(
                        proposed is not None
                        and proposed.implied_vol >= IV_THRESHOLD - 1e-12
                    )
                    if proposed is not None and not gate:
                        tp_iv_failures += 1
                    signals.append(
                        signal_row(
                            "model",
                            label,
                            day,
                            tomorrow,
                            "tp80",
                            active,
                            proposed,
                            "roll" if gate else "hold",
                            meta,
                            ratio,
                        )
                    )
                    if gate:
                        pending = pending_for(
                            True,
                            proposed,
                            "tp80",
                            day,
                            tomorrow,
                            ratio,
                            active.selection.expiry,
                        )
        rows.append(v23.model_daily_row(day, label, row, active, pnl, cost))
    if pending is not None:
        raise RuntimeError(f"Unexecuted final model action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), {
        "final_pending": 0,
        "scheduled_execution_failures": 0,
        "delayed_trading_days": 0,
        "tp_opportunities": tp_opportunities,
        "tp_no_far": tp_no_far,
        "tp_iv_failures": tp_iv_failures,
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
    tp_opportunities = tp_no_far = tp_iv_failures = tp_no_close = 0
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
                    pending.selection,
                    v19.MO_QTY,
                    new_settle,
                    new_close,
                    cycle_id,
                )
            delay = int(
                ((dates > pending.scheduled_execution_date) & (dates <= day)).sum()
            )
            delayed_days += delay
            trades.append(
                trade_row(
                    "real",
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
            )
            pending = None
            traded = True
        elif pending is not None and day == pending.scheduled_execution_date:
            scheduled_failures += 1
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
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = v23.state_row(states, day, force_normal)
            if event is not None:
                must_roll = active is None or active.selection.expiry <= pd.Timestamp(
                    event.current_expiry
                )
                if must_roll:
                    proposed, meta = v23.real_selection(
                        calls,
                        market_row,
                        day,
                        tomorrow,
                        label,
                        "monthly",
                        state,
                    )
                    gate = bool(
                        proposed is not None
                        and proposed.implied_vol >= IV_THRESHOLD - 1e-12
                    )
                    action = (
                        "roll"
                        if active is not None and gate
                        else "close"
                        if active is not None
                        else "open"
                        if gate
                        else "skip"
                    )
                    signals.append(
                        signal_row(
                            "real",
                            label,
                            day,
                            tomorrow,
                            "monthly",
                            active,
                            proposed,
                            action,
                            meta,
                        )
                    )
                    pending = pending_for(
                        active is not None,
                        proposed,
                        "monthly",
                        day,
                        tomorrow,
                        old_expiry=active.selection.expiry
                        if active is not None
                        else None,
                    )
                else:
                    meta = v23.selection_meta(
                        None,
                        state,
                        0,
                        np.nan,
                        np.nan,
                        np.nan,
                        np.nan,
                        float(market_row["spot_close"]),
                    )
                    signals.append(
                        signal_row(
                            "real",
                            label,
                            day,
                            tomorrow,
                            "monthly_keep_far",
                            active,
                            None,
                            "keep_far",
                            meta,
                        )
                    )
            elif active is None:
                proposed, meta = v23.real_selection(
                    calls,
                    market_row,
                    day,
                    tomorrow,
                    label,
                    "daily_entry",
                    state,
                )
                gate = bool(
                    proposed is not None
                    and proposed.implied_vol >= IV_THRESHOLD - 1e-12
                )
                signals.append(
                    signal_row(
                        "real",
                        label,
                        day,
                        tomorrow,
                        "daily_entry",
                        None,
                        proposed,
                        "open" if gate else "skip",
                        meta,
                    )
                )
                pending = pending_for(
                    False, proposed, "daily_entry", day, tomorrow
                )
            elif old_quote is None or float(old_quote["close"]) <= 0:
                tp_no_close += 1
            else:
                ratio = float(old_quote["close"]) / active.entry_close
                if ratio <= TP_REMAINING_RATIO + 1e-12:
                    tp_opportunities += 1
                    proposed, meta = real_far_selection(
                        calls,
                        market_row,
                        day,
                        tomorrow,
                        active.selection.expiry,
                        label,
                        state,
                    )
                    if proposed is None:
                        tp_no_far += 1
                    gate = bool(
                        proposed is not None
                        and proposed.implied_vol >= IV_THRESHOLD - 1e-12
                    )
                    if proposed is not None and not gate:
                        tp_iv_failures += 1
                    signals.append(
                        signal_row(
                            "real",
                            label,
                            day,
                            tomorrow,
                            "tp80",
                            active,
                            proposed,
                            "roll" if gate else "hold",
                            meta,
                            ratio,
                        )
                    )
                    if gate:
                        pending = pending_for(
                            True,
                            proposed,
                            "tp80",
                            day,
                            tomorrow,
                            ratio,
                            active.selection.expiry,
                        )
        rows.append(
            v23.real_daily_row(
                day,
                label,
                base,
                market_row,
                call_lookup,
                active,
                pnl,
                cost,
            )
        )
    if pending is not None:
        raise RuntimeError(f"Unexecuted final real action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), {
        "final_pending": 0,
        "scheduled_execution_failures": scheduled_failures,
        "delayed_trading_days": delayed_days,
        "tp_opportunities": tp_opportunities,
        "tp_no_far": tp_no_far,
        "tp_iv_failures": tp_iv_failures,
        "tp_no_close_days": tp_no_close,
    }


def event_summary(
    daily: pd.DataFrame, trades: pd.DataFrame, signals: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        t = (
            trades[
                trades["layer"].eq(layer) & trades["candidate"].eq(candidate)
            ]
            if len(trades)
            else trades
        )
        s = (
            signals[
                signals["layer"].eq(layer) & signals["candidate"].eq(candidate)
            ]
            if len(signals)
            else signals
        )
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "signals": len(s),
                "daily_checks": int(s["reason"].eq("daily_entry").sum())
                if len(s)
                else 0,
                "tp80_signals": int(s["reason"].eq("tp80").sum())
                if len(s)
                else 0,
                "tp80_hold_signals": int(
                    (s["reason"].eq("tp80") & s["action"].eq("hold")).sum()
                )
                if len(s)
                else 0,
                "tp80_rolls": int(t["reason"].eq("tp80").sum())
                if len(t)
                else 0,
                "open_events": int(t["action"].eq("open").sum())
                if len(t)
                else 0,
                "roll_events": int(t["action"].eq("roll").sum())
                if len(t)
                else 0,
                "close_events": int(t["action"].eq("close").sum())
                if len(t)
                else 0,
                "call_days": int(group["call_contract"].fillna("").ne("").sum()),
                "call_day_ratio": float(
                    group["call_contract"].fillna("").ne("").mean()
                ),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "average_margin_fraction": float(
                    group["call_margin_fraction"].mean()
                ),
                "maximum_margin_fraction": float(
                    group["call_margin_fraction"].max()
                ),
                "capital_breach_days": int(
                    (
                        group["put_mark_fraction"]
                        + group["call_margin_fraction"]
                        > v19.CASH_BASE + 1e-12
                    ).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def metric_value(
    formal: pd.DataFrame,
    layer: str,
    candidate: str,
    window: str,
    column: str,
) -> float:
    return v19.metric_value(formal, layer, candidate, window, column)


def stress_value(
    stress: pd.DataFrame, candidate: str, period: str, column: str
) -> float:
    row = stress[
        stress["layer"].eq("real")
        & stress["candidate"].eq(candidate)
        & stress["period"].eq(period)
    ]
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
        left = reference[reference["candidate"].eq(candidate)].sort_values(
            ["layer", "date"]
        )
        right = daily[daily["candidate"].eq(candidate)].sort_values(
            ["layer", "date"]
        )
        parity[candidate] = {
            column: float(
                np.max(np.abs(left[column].to_numpy() - right[column].to_numpy()))
            )
            for column in ["ret", "cash_ret", "nav", "cash_nav"]
        }
    candidates = daily[daily["candidate"].isin(CANDIDATES)]
    expected_ret = (
        1.0
        + candidates["gross_ret"]
        + candidates["put_pnl_ret"]
        + candidates["call_pnl_ret"]
    ) * (1.0 - candidates["cost_rate"]) * (
        1.0 - candidates["put_cost_rate"]
    ) * (
        1.0 - candidates["call_cost_rate"]
    ) - 1.0
    expected_cash = candidates["ret"] + (
        v19.CASH_BASE
        - candidates["put_mark_fraction"]
        - candidates["call_margin_fraction"]
    ).clip(lower=0.0) * v19.CASH_DAILY
    selected = signals[signals["contract"].fillna("").ne("")]
    gate_errors = int(
        (
            selected["gate_pass"].astype(bool)
            != selected["gate_iv"].ge(IV_THRESHOLD - 1e-12)
        ).sum()
    )
    tier_errors = int(
        (
            ~selected["dte"].between(selected["dte_low"], selected["dte_high"])
            | selected["moneyness"].lt(selected["min_otm"] - 1e-12)
        ).sum()
    )
    causality = int(
        (signals["eval_date"] >= signals["scheduled_execution_date"]).sum()
        + (trades["eval_date"] >= trades["actual_execution_date"]).sum()
        + (
            trades["actual_execution_date"]
            < trades["scheduled_execution_date"]
        ).sum()
    )
    tp_signals = signals[signals["reason"].eq("tp80")]
    tp_trades = trades[trades["reason"].eq("tp80")]
    tp_errors = int(
        (tp_signals["remaining_price_ratio"] > TP_REMAINING_RATIO + 1e-12).sum()
        + (
            pd.to_datetime(tp_trades["new_expiry"])
            <= pd.to_datetime(tp_trades["old_expiry"])
        ).sum()
        + (~tp_trades["gate_pass"].astype(bool)).sum()
    )
    real_tp = tp_signals[tp_signals["layer"].eq("real")].copy()
    call_lookup = calls.set_index(["contract", "date"])
    ratio_recalc_errors: list[float] = []
    for signal in real_tp.itertuples(index=False):
        quote = v19.quote_row(call_lookup, signal.old_contract, signal.eval_date)
        if quote is None:
            ratio_recalc_errors.append(np.inf)
        else:
            ratio_recalc_errors.append(
                abs(
                    float(quote["close"]) / float(signal.old_entry_close)
                    - float(signal.remaining_price_ratio)
                )
            )
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
    formal_signal_states = signals[
        signals["candidate"].eq(FORMAL_1)
    ][["eval_date", "valuation_state"]].merge(
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
    result = {
        "reference_parity_max_abs": parity,
        "return_identity_max_abs": float(
            (candidates["ret"] - expected_ret).abs().max()
        ),
        "cash_identity_max_abs": float(
            (candidates["cash_ret"] - expected_cash).abs().max()
        ),
        "gate_formula_errors": gate_errors,
        "tier_rule_errors": tier_errors,
        "causality_failures": causality,
        "tp80_rule_errors": tp_errors,
        "tp80_ratio_max_abs_error": max(ratio_recalc_errors)
        if ratio_recalc_errors
        else 0.0,
        "official_close_max_abs_error": max(close_errors)
        if close_errors
        else 0.0,
        "formal_state_errors": state_errors,
    }
    result["all_pass"] = bool(
        max(max(values.values()) for values in parity.values()) <= 1e-15
        and result["return_identity_max_abs"] <= 3e-15
        and result["cash_identity_max_abs"] <= 1e-15
        and gate_errors == 0
        and tier_errors == 0
        and causality == 0
        and tp_errors == 0
        and result["tp80_ratio_max_abs_error"] <= 1e-12
        and result["official_close_max_abs_error"] <= 1e-12
        and state_errors == 0
    )
    return result


def line_pass(
    formal: pd.DataFrame,
    stress: pd.DataFrame,
    events: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    candidate: str,
    reference: str,
    audit_ok: bool,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for window in ["full", "last_3y", "last_1y"]:
        values[f"{window}_ann_delta"] = metric_value(
            formal, "real", candidate, window, "ann_return"
        ) - metric_value(formal, "real", reference, window, "ann_return")
        values[f"{window}_maxdd_improvement"] = metric_value(
            formal, "real", candidate, window, "max_dd"
        ) - metric_value(formal, "real", reference, window, "max_dd")
    values["rebound_2024_delta"] = stress_value(
        stress, candidate, "rebound_2024_0918_1008", "total_return"
    ) - stress_value(stress, reference, "rebound_2024_0918_1008", "total_return")
    event = events[
        events["layer"].eq("real") & events["candidate"].eq(candidate)
    ].iloc[0]
    values["real_tp80_rolls"] = int(event["tp80_rolls"])
    values["capital_breach_days"] = int(event["capital_breach_days"])
    values["final_pending"] = int(stats[candidate]["final_pending"])
    return_gate = (
        values["full_ann_delta"] >= -1e-12
        and values["last_3y_ann_delta"] >= -1e-12
        and values["last_1y_ann_delta"] >= -0.005 - 1e-12
    )
    risk_gate = values["full_maxdd_improvement"] >= -0.02 - 1e-12
    stress_gate = values["rebound_2024_delta"] >= -0.01 - 1e-12
    event_gate = values["real_tp80_rolls"] >= 2
    execution_gate = (
        values["capital_breach_days"] == 0 and values["final_pending"] == 0
    )
    values.update(
        {
            "candidate": candidate,
            "reference": reference,
            "return_gate": return_gate,
            "risk_gate": risk_gate,
            "stress_gate": stress_gate,
            "event_gate": event_gate,
            "execution_gate": execution_gate,
            "audit_gate": audit_ok,
            "hard_pass": bool(
                audit_ok
                and return_gate
                and risk_gate
                and stress_gate
                and event_gate
                and execution_gate
            ),
        }
    )
    return values


def decision_result(
    formal: pd.DataFrame,
    stress: pd.DataFrame,
    events: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    audit_ok: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    control = line_pass(
        formal, stress, events, stats, CONTROL_1, CONTROL_0, audit_ok
    )
    valuation = line_pass(
        formal, stress, events, stats, FORMAL_1, FORMAL_0, audit_ok
    )
    table = pd.DataFrame([control, valuation])
    interaction_full = valuation["full_ann_delta"] - control["full_ann_delta"]
    interaction_3y = valuation["last_3y_ann_delta"] - control["last_3y_ann_delta"]
    if bool(valuation["hard_pass"]):
        conclusion = "valuation_tp80_supported_real_short_sample"
        selected = FORMAL_1
        stability = "official_real_short_sample_supported"
    elif bool(control["hard_pass"]):
        conclusion = "tp80_without_valuation_only"
        selected = CONTROL_1
        stability = "control_only_supported"
    else:
        combined_level = (
            metric_value(formal, "real", FORMAL_1, "full", "ann_return")
            >= metric_value(formal, "real", BASELINE, "full", "ann_return")
            - 1e-12
        )
        conclusion = (
            "combined_level_not_incremental"
            if combined_level
            else "tp80_not_supported"
        )
        selected = FORMAL_0
        stability = "incremental_gate_failed"
    decision = {
        "conclusion": conclusion,
        "selected_candidate": selected,
        "control_tp80_pass": bool(control["hard_pass"]),
        "valuation_tp80_pass": bool(valuation["hard_pass"]),
        "full_cagr_interaction": interaction_full,
        "last_3y_cagr_interaction": interaction_3y,
        "stability_label": stability,
        "live_approved": False,
        "research_status": "official_real_short_sample_mechanism_only_not_live_approved",
    }
    return table, decision


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
    focus = formal[
        formal["window"].isin(
            ["full", "last_10y", "last_5y", "last_3y", "last_1y"]
        )
    ]
    lines = [
        "# IM + MO Call PE估值滞回 × 盈利80%提前展期 v24",
        "",
        f"Decision: `{decision['conclusion']}`；未批准实盘。",
        f"Stability: `{decision['stability_label']}`。",
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
            lines.append(
                f"|{row.layer}|{row.candidate}|{row.window}|是|{row.ann_return:.2%}|{row.max_dd:.2%}|{row.sharpe_repo:.3f}|"
            )
        else:
            lines.append(
                f"|{row.layer}|{row.candidate}|{row.window}|否：历史不足|N/A|N/A|N/A|"
            )
    lines.extend(
        [
            "",
            "## 真实逐年",
            "",
            annual[annual["layer"].eq("real")].to_markdown(index=False),
            "",
            "## 压力窗口",
            "",
            stress[stress["layer"].eq("real")].to_markdown(index=False),
            "",
            "## 事件与暴露",
            "",
            events.to_markdown(index=False),
            "",
            "## 判定",
            "",
            decision_table.to_markdown(index=False),
            "",
            "## 执行统计",
            "",
            "```json",
            json.dumps(stats, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 审计",
            "",
            "```json",
            json.dumps(audit, ensure_ascii=False, indent=2),
            "```",
            "",
            "本研究只验证盈利80%提前展期及其与正式PE状态的交互，不是完整外部策略复现或交易建议。",
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
        handle.write("python im_mo_call_valuation_profit_roll_v24.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "baseline": {
                "candidates": [CONTROL_0, FORMAL_0],
                "definition": "matched v23 no-TP control and formal valuation lines",
            },
            "candidate_grid": [
                BASELINE,
                MONTHLY,
                DAILY_D10,
                CONTROL_0,
                FORMAL_0,
                CONTROL_1,
                FORMAL_1,
            ],
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
    signals[signals["reason"].eq("tp80")].to_csv(
        STAGING / "tp80_signal_audit.csv", index=False
    )
    states.to_csv(STAGING / "formal_pe_states.csv.gz", index=False, compression="gzip")
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    stress.to_csv(STAGING / "stress_period_metrics.csv", index=False)
    events.to_csv(STAGING / "event_exposure_summary.csv", index=False)
    decision_table.to_csv(STAGING / "decision_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "execution_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "audit_summary.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python im_mo_call_valuation_profit_roll_v24.py\n", encoding="utf-8"
    )
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
        "execution": "T close signal, T+1 official close, delayed frozen contract if unavailable",
        "frictions": {
            "call_basket_one_way": v19.CALL_BASKET_SIDE_COST,
            "cash_annual": 0.03,
            "bid_ask_impact": "excluded",
        },
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
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
    source_hashes = verify_inputs()
    reference = reference_daily()
    states = formal_states()
    baseline = v19.load_baseline()
    upstream = v19.load_upstream()
    market, market_checks = v19.v6.model_market()
    market = market[market["date"].le(END)].copy()
    upstream = upstream[upstream["date"].le(END)].copy()
    real_market = market[market["date"].ge(v19.REAL_START)].copy()
    calls = v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    model_events = v19.monthly_events(
        v19.MODEL_START, model_dates, v19.model_roll_dates(model_dates)
    )
    real_rolls = pd.DatetimeIndex(
        upstream.loc[upstream["roll_to"].notna(), "date"]
    )
    real_events = v19.monthly_events(v19.REAL_START, real_dates, real_rolls)
    model_base = baseline[
        baseline["layer"].eq("model") & baseline["date"].le(END)
    ].drop(columns=["layer", "candidate"])
    real_base = baseline[
        baseline["layer"].eq("real") & baseline["date"].le(END)
    ].drop(columns=["layer", "candidate"])
    daily_parts = [reference]
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    stats: dict[str, dict[str, int]] = {}
    for label, force_normal in [(CONTROL_1, True), (FORMAL_1, False)]:
        model_overlay, model_trades, model_signals, model_stats = run_model(
            market, model_events, states, label, force_normal
        )
        real_overlay, real_trades, real_signals, real_stats = run_real(
            upstream,
            calls,
            real_market,
            real_events,
            states,
            label,
            force_normal,
        )
        model_candidate = v19.assemble_candidate(model_base, model_overlay, label)
        model_candidate["layer"] = "model"
        real_candidate = v19.assemble_candidate(real_base, real_overlay, label)
        real_candidate["layer"] = "real"
        daily_parts.extend([model_candidate, real_candidate])
        trade_parts.extend([model_trades, real_trades])
        signal_parts.extend([model_signals, real_signals])
        stats[label] = {
            "final_pending": model_stats["final_pending"]
            + real_stats["final_pending"],
            "scheduled_execution_failures": model_stats[
                "scheduled_execution_failures"
            ]
            + real_stats["scheduled_execution_failures"],
            "delayed_trading_days": model_stats["delayed_trading_days"]
            + real_stats["delayed_trading_days"],
            "model_tp_opportunities": model_stats["tp_opportunities"],
            "real_tp_opportunities": real_stats["tp_opportunities"],
            "model_tp_no_far": model_stats["tp_no_far"],
            "real_tp_no_far": real_stats["tp_no_far"],
            "model_tp_iv_failures": model_stats["tp_iv_failures"],
            "real_tp_iv_failures": real_stats["tp_iv_failures"],
            "real_tp_no_close_days": real_stats["tp_no_close_days"],
        }
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(
        ["layer", "candidate", "date"]
    ).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(
        ["layer", "candidate", "actual_execution_date"]
    ).reset_index(drop=True)
    signals = pd.concat(signal_parts, ignore_index=True).sort_values(
        ["layer", "candidate", "eval_date"]
    ).reset_index(drop=True)
    formal, annual = v19.metrics_tables(daily)
    stress = v23.stress_table(daily)
    events = event_summary(daily, trades, signals)
    audit = audit_results(reference, daily, trades, signals, calls, states)
    audit["market_checks"] = market_checks
    decision_table, decision = decision_result(
        formal, stress, events, stats, bool(audit["all_pass"])
    )
    scan_long, scan_wide = v23.scan_tables(formal)
    record = record_text(
        formal, annual, stress, events, decision_table, decision, audit, stats
    )
    update_scan(scan_long, scan_wide, record, decision)
    write_outputs(
        daily,
        trades,
        signals,
        states,
        formal,
        annual,
        stress,
        events,
        decision_table,
        decision,
        audit,
        record,
        stats,
        source_hashes,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
