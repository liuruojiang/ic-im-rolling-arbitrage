"""Research-only IM covered-Call entry regime scan on the native v1.4 account."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260930_ic_im_v1_4_fix9_no_call_baseline_im_core_covered_call_research_"
    "entry_regime_mom120_and_fear_greed_v2_account_gate"
)
NATIVE = ROOT / "outputs" / "re_certification" / "icim_v14_fix4_recert_20260924" / "l5_native_signal_reconstruction_20260925"
L8 = ROOT / "quant_param_scan_runs" / "20260925_ic_im_fix5_v1_4_fix5_joint_account_enhancement_layer_conditional_ablation"
NO_CALL = ROOT / "quant_param_scan_runs" / "20260925_no_call_2x_2p5x_3x"
FEAR_PATH = ROOT / "quant_research_runs" / "20260928_csi1000_fear_greed_reproduction" / "aligned_daily_inputs.csv"
MOM_PATH = NATIVE / "native_fix4_im_risk_signals_v1.csv.gz"
TARGET_PATH = NATIVE / "native_fix4_im_noseller_targets_v3.csv.gz"
SPEC_PATH = RUN / "preregistered_spec.md"
SPEC_HASH_PATH = RUN / "preregistered_spec.md.sha256"
OUTPUT_FILES = (
    "scan_summary.csv",
    "window_metrics.csv",
    "daily_outputs.csv.gz",
    "call_target_schedules.csv.gz",
    "call_overlay_audit.csv",
    "run_audit.json",
)
CANDIDATES = (
    "no_call_fix6",
    "old_iv26",
    "iv26_safety15_2",
    "momneg_fear_gt25",
    "momneg_fear_gt35",
    "momneg_fear_gt45",
    "greed75_level",
    "greed75_downcross",
)
SAFETY_CANDIDATES = CANDIDATES[2:]
GATED_CANDIDATES = CANDIDATES[3:]
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")

sys.path.insert(0, str(NATIVE))
import native_fix4_account_v1 as account  # noqa: E402
from native_fix4_account_v2 import fresh_reentry  # noqa: E402
from native_fix4_historical_sources_v1 import ROOT as SOURCE_ROOT  # noqa: E402
from native_fix4_historical_sources_v2 import DatedSources  # noqa: E402
from native_fix4_risk_signals_v1 import OHLCV  # noqa: E402

import im_mo_call_daily_d10_threat_roll_v27 as v27  # noqa: E402

v25 = v27.v25
v22 = v27.v22
v19 = v27.v19


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def load_signal_frame() -> pd.DataFrame:
    mom = pd.read_csv(MOM_PATH, parse_dates=["signal_date"])[["signal_date", "momentum_120"]]
    fear = pd.read_csv(FEAR_PATH, parse_dates=["date"])[["date", "fear_greed_index"]]
    frame = mom.merge(fear, left_on="signal_date", right_on="date", how="left", validate="one_to_one")
    frame = frame.drop(columns="date").sort_values("signal_date").reset_index(drop=True)
    frame["fear_prev"] = frame["fear_greed_index"].shift(1)
    if not np.isfinite(frame["momentum_120"].to_numpy(float)).all():
        raise RuntimeError("MOM120 contains missing or non-finite values")
    frame["momneg_fear_gt25"] = frame["momentum_120"].lt(0.0) & frame["fear_greed_index"].gt(25.0)
    frame["momneg_fear_gt35"] = frame["momentum_120"].lt(0.0) & frame["fear_greed_index"].gt(35.0)
    frame["momneg_fear_gt45"] = frame["momentum_120"].lt(0.0) & frame["fear_greed_index"].gt(45.0)
    frame["greed75_level"] = frame["fear_greed_index"].ge(75.0)
    frame["greed75_downcross"] = frame["fear_prev"].ge(75.0) & frame["fear_greed_index"].lt(75.0)
    return frame


def flat_states(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "history_kind": "daily_d10_entry_regime_research",
            "date": dates,
            "official_rolling_pe": np.nan,
            "pe_percentile_10y": np.nan,
            "history_start": pd.NaT,
            "history_end": pd.NaT,
            "history_rows": 0,
            "valuation_state": "normal",
            "state_changed": False,
            "state_from": "normal",
            "state_to": "normal",
        }
    )


def gated_normal_signal_factory(
    label: str,
    gate: dict[pd.Timestamp, bool],
    require_iv26: bool = False,
) -> Callable[..., tuple[dict[str, Any], v22.Pending | None]]:
    def gated_normal_signal_and_pending(
        layer: str,
        candidate: str,
        day: pd.Timestamp,
        tomorrow: pd.Timestamp,
        reason: str,
        active: Any,
        selection: v19.Selection | None,
        meta: dict[str, Any],
    ) -> tuple[dict[str, Any], v22.Pending | None]:
        day = pd.Timestamp(day)
        enough_dte = bool(selection is not None and (pd.Timestamp(selection.expiry) - day).days >= 15)
        iv_allowed = bool(selection is not None and selection.implied_vol >= v25.IV_THRESHOLD - 1e-12)
        entry_allowed = bool(gate.get(day, False) and enough_dte and (iv_allowed or not require_iv26))
        keep_or_roll = active is not None and selection is not None
        open_new = active is None and selection is not None and entry_allowed
        selected = selection if (keep_or_roll or open_new) else None
        if active is not None and selected is not None:
            action = "roll"
        elif active is not None:
            action = "close"
        elif selected is not None:
            action = "open"
        else:
            action = "skip"
        signal = v25.make_signal(layer, candidate, day, tomorrow, reason, active, selection, action, meta)
        signal.update(
            {
                "gate_pass": bool(selected is not None),
                "entry_regime_gate": entry_allowed,
                "entry_gate_candidate": label,
                "entry_iv_gate_pass": iv_allowed,
                "entry_min_dte_pass": enough_dte,
            }
        )
        if active is None and selected is None:
            return signal, None
        pending = v22.Pending(
            day,
            pd.Timestamp(tomorrow),
            action,
            reason,
            selected,
            float(selection.implied_vol) if selection is not None else np.nan,
            bool(selected is not None),
            np.nan,
            active.selection.expiry if active is not None else None,
        )
        return signal, pending

    return gated_normal_signal_and_pending


def run_real_with_gate_safety(
    upstream: pd.DataFrame,
    calls: pd.DataFrame,
    market: pd.DataFrame,
    events: pd.DataFrame,
    states: pd.DataFrame,
    label: str,
    normal_handler: Callable[..., tuple[dict[str, Any], v22.Pending | None]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """v25 real lifecycle with uniform entry-DTE and expiry-close safety."""
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
        "expiry_safety_closes": 0,
    }
    for index, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(prior_im.iloc[index])
        market_row = market_lookup.loc[day]
        pnl = cost = 0.0
        traded = False
        old_quote = v19.quote_row(call_lookup, active.selection.contract, day) if active is not None else None
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
            stats["delayed_trading_days"] += delay
            trades.append(
                v25.trade_row(
                    "real", label, pending, pending_context, day, old, active,
                    old_close, new_close, new_settle, delay,
                )
            )
            if pending.reason == "threat_roll":
                threat_count = int(pending_context["threat_count_before"]) + 1
                stats["threat_rolls"] += 1
                stats["max_consecutive_threat_rolls"] = max(stats["max_consecutive_threat_rolls"], threat_count)
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
                raise RuntimeError(f"Missing real settlement after safety rule: {label} {day.date()}")
            pnl += active.qty * v19.MO_MULTIPLIER / v19.IM_MULTIPLIER * (active.prior_settle - float(old_quote["settle"])) / denominator
            active.prior_settle = float(old_quote["settle"])
        event = event_lookup.loc[day] if day in event_lookup.index else None
        if blocked:
            stats["blocked_days"] += 1
        if pending is None and not traded and day in next_days:
            tomorrow = next_days[day]
            state = v25.v23.state_row(states, day, True)
            if active is not None:
                remaining_sessions = int(((dates > day) & (dates <= pd.Timestamp(active.selection.expiry))).sum())
                if remaining_sessions <= 2:
                    reason = "expiry_safety_close"
                    pending = v22.Pending(day, tomorrow, "close", reason, None, np.nan, False, np.nan, active.selection.expiry)
                    pending_context = {"remaining_sessions": remaining_sessions}
                    meta = {"target_strike": np.nan, "new_eval_moneyness": np.nan, "new_dte": np.nan}
                    signals.append(v25.make_signal("real", label, day, tomorrow, reason, active, None, "close", meta))
                    stats["expiry_safety_closes"] += 1
                else:
                    threat_otm = float(active.selection.strike) / float(market_row["spot_close"]) - 1.0
                    if threat_otm <= v25.THREAT_OTM + 1e-12:
                        stats["threat_signals"] += 1
                        if threat_count >= v25.MAX_THREAT_ROLLS:
                            reason = "threat_stop_max5"
                            proposed = None
                            meta = {"target_strike": np.nan, "new_eval_moneyness": np.nan, "new_dte": np.nan}
                            stats["threat_max5_stops"] += 1
                        else:
                            proposed, meta = v25.threat_real_selection(calls, market_row, day, tomorrow, active, label)
                            reason = "threat_roll" if proposed is not None else "threat_stop_no_contract"
                            if proposed is None:
                                stats["threat_no_contract_stops"] += 1
                        pending = v25.threat_pending(active, proposed, reason, day, tomorrow)
                        pending_context = {"threat_otm": threat_otm, "threat_count_before": threat_count, **meta}
                        signals.append(
                            v25.make_signal(
                                "real", label, day, tomorrow, reason, active, proposed,
                                "roll" if proposed is not None else "close", meta, threat_otm, threat_count,
                            )
                        )
                    elif event is not None:
                        must_roll = active.selection.expiry <= pd.Timestamp(event.current_expiry)
                        if must_roll:
                            proposed, meta = v25.v23.real_selection(calls, market_row, day, tomorrow, label, "monthly", state)
                            signal, pending = normal_handler("real", label, day, tomorrow, "monthly", active, proposed, meta)
                            signals.append(signal)
                            pending_context = {}
                        else:
                            meta = v25.v23.selection_meta(None, state, 0, np.nan, np.nan, np.nan, np.nan, float(market_row["spot_close"]))
                            signals.append(v25.make_signal("real", label, day, tomorrow, "monthly_keep_far", active, None, "keep_far", meta))
            elif event is not None:
                if blocked:
                    blocked = False
                    stats["reenable_events"] += 1
                proposed, meta = v25.v23.real_selection(calls, market_row, day, tomorrow, label, "monthly", state)
                signal, pending = normal_handler("real", label, day, tomorrow, "monthly", None, proposed, meta)
                signals.append(signal)
                pending_context = {}
            elif not blocked:
                proposed, meta = v25.v23.real_selection(calls, market_row, day, tomorrow, label, "daily_entry", state)
                signal, pending = normal_handler("real", label, day, tomorrow, "daily_entry", None, proposed, meta)
                signals.append(signal)
                pending_context = {}
        daily = v25.v23.real_daily_row(day, label, base, market_row, call_lookup, active, pnl, cost)
        daily.update({"threat_roll_count": threat_count, "threat_entry_blocked": blocked})
        rows.append(daily)
    if pending is not None:
        raise RuntimeError(f"Unexecuted final real action: {label}")
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(signals), stats


def call_overlay_for_gate(
    label: str,
    gate: dict[pd.Timestamp, bool],
    upstream: pd.DataFrame,
    calls: pd.DataFrame,
    real_market: pd.DataFrame,
    real_events: pd.DataFrame,
    real_monthly: list[v19.Selection],
    require_iv26: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    selector = v27.d10_real_selector(v27.cycle_lookup(real_events, real_monthly))
    normal_handler = gated_normal_signal_factory(label, gate, require_iv26=require_iv26)
    with patch.object(v25.v23, "real_selection", selector):
        return run_real_with_gate_safety(
            upstream,
            calls,
            real_market,
            real_events,
            flat_states(pd.DatetimeIndex(upstream["date"])),
            label,
            normal_handler,
        )


def targets_from_overlay(
    original: pd.DataFrame,
    overlay: pd.DataFrame,
    lifecycle: pd.DataFrame,
) -> pd.DataFrame:
    targets = original.copy()
    lookup = overlay.set_index(pd.to_datetime(overlay["date"]))
    prior_contract = ""
    actions: list[str] = []
    contracts: list[Any] = []
    quantities: list[float] = []
    rescues: list[int] = []
    for row in targets.itertuples(index=False):
        if pd.isna(row.execution_date):
            contracts.append(np.nan)
            quantities.append(0.0)
            actions.append("NO_NEXT_EXECUTION")
            rescues.append(0)
            continue
        execution = pd.Timestamp(row.execution_date)
        state = lookup.loc[execution]
        contract = str(state["call_contract"]) if pd.notna(state["call_contract"]) else ""
        if contract.lower() == "nan":
            contract = ""
        if not prior_contract and contract:
            action = "OPEN_CALL"
        elif prior_contract and not contract:
            action = "CLOSE_CALL"
        elif prior_contract and contract != prior_contract:
            action = "ROLL_CALL"
        else:
            action = "HOLD"
        contracts.append(contract if contract else np.nan)
        quantities.append(-1.0 if contract else 0.0)
        actions.append(action)
        rescues.append(int(state.get("threat_roll_count", 0)))
        prior_contract = contract
    targets["call_contract_target"] = contracts
    targets["call_qty_normalized_target"] = quantities
    targets["call_action"] = actions
    targets["call_rescue_count"] = rescues

    # The account can temporarily leave the futures route.  A static target that
    # remains non-zero would otherwise be opened later when the route returns,
    # even though the entry signal is no longer true.  Once an intended episode
    # misses/loses its opening window, suppress it until the overlay first goes
    # flat and a genuinely new OPEN_CALL episode begins.
    life = lifecycle.loc[
        lifecycle["product"].eq("IM") & lifecycle["arm"].eq("repeat_roll"),
        ["execution_date", "route_after_execution"],
    ].copy()
    route_by_execution = life.set_index(pd.to_datetime(life["execution_date"]))["route_after_execution"]
    account_episode_active = False
    previous_desired = ""
    gated_contracts: list[Any] = []
    gated_quantities: list[float] = []
    gated_actions: list[str] = []
    prior_gated_contract = ""
    for row in targets.itertuples(index=False):
        desired = str(row.call_contract_target) if pd.notna(row.call_contract_target) else ""
        if desired.lower() == "nan":
            desired = ""
        execution = pd.Timestamp(row.execution_date) if pd.notna(row.execution_date) else None
        route = route_by_execution.get(execution, None) if execution is not None else None
        fresh_episode = bool(desired and not previous_desired)
        if not desired:
            account_episode_active = False
            gated = ""
        elif route != "future":
            account_episode_active = False
            gated = ""
        elif account_episode_active:
            gated = desired
        elif fresh_episode:
            account_episode_active = True
            gated = desired
        else:
            gated = ""
        if not prior_gated_contract and gated:
            action = "OPEN_CALL"
        elif prior_gated_contract and not gated:
            action = "CLOSE_CALL"
        elif prior_gated_contract and gated != prior_gated_contract:
            action = "ROLL_CALL"
        else:
            action = "HOLD"
        gated_contracts.append(gated if gated else np.nan)
        gated_quantities.append(-1.0 if gated else 0.0)
        gated_actions.append(action)
        prior_gated_contract = gated
        previous_desired = desired
    targets["call_contract_target"] = gated_contracts
    targets["call_qty_normalized_target"] = gated_quantities
    targets["call_action"] = gated_actions
    return targets


def run_account(
    label: str,
    targets: pd.DataFrame,
    disable_call: bool,
    source: DatedSources,
    lifecycle: pd.DataFrame,
    seller_candidates: pd.DataFrame,
    gov: pd.Series,
    spot: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    original_read = pd.read_csv

    def read_targets(path: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
        if isinstance(path, (str, Path)) and Path(path).resolve() == TARGET_PATH.resolve():
            return targets.copy()
        return original_read(path, *args, **kwargs)

    old_disabled = account.DISABLED_MODULES
    account.DISABLED_MODULES = frozenset({"call"}) if disable_call else frozenset()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(pd, "read_csv", side_effect=read_targets))
            daily, journal, audit = account.replay(
                "IM", "repeat_roll", source, lifecycle, seller_candidates, gov, spot
            )
    finally:
        account.DISABLED_MODULES = old_disabled
    daily = daily.copy()
    journal = journal.copy()
    daily.insert(0, "candidate", label)
    journal.insert(0, "candidate", label)
    return daily, journal, audit


def metric_rows(
    daily: pd.DataFrame,
    label: str,
    call_holding_days: int,
    call_trade_sides: int,
    call_open_events: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    end = frame["date"].iloc[-1]
    rows: list[dict[str, Any]] = []
    wide: dict[str, Any] = {"candidate": label}
    unavailable: dict[str, str] = {}
    for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
        if years is not None and frame["date"].iloc[0] > end - pd.DateOffset(years=years):
            reason = f"Native listed-option account starts {frame['date'].iloc[0].date()}; fewer than {years} full years."
            unavailable[segment] = reason
            rows.append(
                {
                    "candidate": label,
                    "segment": segment,
                    "start": "N/A",
                    "end": "N/A",
                    "rows": 0,
                    "ann_return": "N/A",
                    "ann_vol": "N/A",
                    "sharpe_repo": "N/A",
                    "max_dd": "N/A",
                    "call_holding_days": call_holding_days,
                    "call_trade_sides": call_trade_sides,
                    "call_open_events": call_open_events,
                    "cost_total": "N/A",
                }
            )
            wide[f"ann_return_{segment}"] = "N/A"
            wide[f"max_dd_{segment}"] = "N/A"
            continue
        sample = frame if years is None else frame.loc[frame["date"] >= end - pd.DateOffset(years=years)].copy()
        nav = sample["nav"].to_numpy(float)
        relative = nav / (1.0 if years is None else nav[0])
        returns = relative[1:] / relative[:-1] - 1.0
        ann_vol = float(np.std(returns, ddof=1) * math.sqrt(252))
        ann_return = float(relative[-1] ** (252 / (len(relative) - 1)) - 1.0)
        max_dd = float(np.min(relative / np.maximum.accumulate(np.r_[1.0, relative])[:-1] - 1.0))
        sharpe = float(np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(252))
        rows.append(
            {
                "candidate": label,
                "segment": segment,
                "start": sample["date"].iloc[0].date().isoformat(),
                "end": sample["date"].iloc[-1].date().isoformat(),
                "rows": len(sample),
                "ann_return": ann_return,
                "ann_vol": ann_vol,
                "sharpe_repo": sharpe,
                "max_dd": max_dd,
                "call_holding_days": call_holding_days,
                "call_trade_sides": call_trade_sides,
                "call_open_events": call_open_events,
                "cost_total": float(sample["fees_cumulative"].iloc[-1]),
            }
        )
        wide[f"ann_return_{segment}"] = ann_return
        wide[f"max_dd_{segment}"] = max_dd
        if segment == "full":
            wide["sharpe_repo_full"] = sharpe
            wide["call_holding_days_full"] = call_holding_days
            wide["call_trade_sides_full"] = call_trade_sides
            wide["call_open_events_full"] = call_open_events
    return rows, wide, unavailable


def call_event_counts(journal: pd.DataFrame, daily: pd.DataFrame) -> tuple[int, int, int]:
    call_trades = journal[
        journal["event"].eq("trade") & journal["symbol"].fillna("").astype(str).str.startswith("option|call|")
    ].copy()
    sides = len(call_trades)
    holding_days = int(pd.to_numeric(daily["call_buffer"], errors="coerce").fillna(0.0).gt(1e-12).sum())
    positions: dict[str, float] = {}
    opens = 0
    for _, group in call_trades.sort_values(["date", "order_id"]).groupby("date", sort=True):
        before = sum(positions.values())
        for row in group.itertuples(index=False):
            name = str(row.symbol)
            positions[name] = positions.get(name, 0.0) + float(row.quantity_change)
        after = sum(positions.values())
        if before >= -1e-9 and after < -1e-9:
            opens += 1
    return holding_days, sides, opens


def account_entry_gate_violations(journal: pd.DataFrame, gate_map: dict[pd.Timestamp, bool]) -> int:
    call_trades = journal[
        journal["event"].eq("trade") & journal["symbol"].fillna("").astype(str).str.startswith("option|call|")
    ].copy()
    positions: dict[str, float] = {}
    violations = 0
    for _, group in call_trades.sort_values(["date", "order_id"]).groupby("date", sort=True):
        before = sum(positions.values())
        for row in group.itertuples(index=False):
            name = str(row.symbol)
            positions[name] = positions.get(name, 0.0) + float(row.quantity_change)
        after = sum(positions.values())
        if before >= -1e-9 and after < -1e-9:
            negative = group[pd.to_numeric(group["quantity_change"], errors="coerce").lt(0.0)]
            signal_day = pd.Timestamp(negative.iloc[-1]["signal_date"])
            if not bool(gate_map.get(signal_day, False)):
                violations += 1
    return violations


def main() -> None:
    started = time.perf_counter()
    if any((RUN / name).exists() for name in OUTPUT_FILES):
        raise FileExistsError("First-run outputs already exist; refusing overwrite")
    if not SPEC_HASH_PATH.exists() or SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower() != sha256(SPEC_PATH):
        raise RuntimeError("Preregistered specification hash mismatch")

    account.VERSION = "v7"
    account.REENTRY_SELECTOR = fresh_reentry
    account.MARGIN_MODEL = "v3"
    account.EXECUTION_MODEL = "mixed"
    account.LIFECYCLE_VERSION = "v4"
    account.QUARTER_ROLL_AT_SIGNAL_CLOSE = True
    account.PROFIT_RESTRIKE_MULTIPLE = 3.0

    signals = load_signal_frame()
    gate_maps: dict[str, dict[pd.Timestamp, bool]] = {
        label: dict(zip(signals["signal_date"], signals[label].astype(bool), strict=True))
        for label in GATED_CANDIDATES
    }
    gate_maps["iv26_safety15_2"] = {pd.Timestamp(day): True for day in signals["signal_date"]}
    original_targets = pd.read_csv(TARGET_PATH)
    upstream = v19.load_upstream()
    market, market_checks = v19.v6.model_market()
    real_market = market[market["date"].ge(v19.REAL_START)].copy()
    calls = v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    real_dates = pd.DatetimeIndex(upstream["date"])
    real_rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    real_events = v19.monthly_events(v19.REAL_START, real_dates, real_rolls)
    real_monthly = v19.build_real_selections(calls, real_market, real_events, "front", v22.TARGET_DELTA, v22.MONTHLY)
    lifecycle = pd.read_csv(NATIVE / "native_fix4_seller_lifecycle_v4.csv.gz")

    overlays: dict[str, pd.DataFrame] = {}
    overlay_audits: list[dict[str, Any]] = []
    target_frames: list[pd.DataFrame] = []
    for label in SAFETY_CANDIDATES:
        overlay, trades, call_signals, stats = call_overlay_for_gate(
            label,
            gate_maps[label],
            upstream,
            calls,
            real_market,
            real_events,
            real_monthly,
            require_iv26=label == "iv26_safety15_2",
        )
        overlays[label] = overlay
        targets = targets_from_overlay(original_targets, overlay, lifecycle)
        targets.insert(0, "candidate", label)
        target_frames.append(targets)
        opens = call_signals[
            call_signals["action"].eq("open") & call_signals["reason"].isin(["monthly", "daily_entry"])
        ].copy()
        gate_lookup = pd.Series(gate_maps[label])
        violations = sum(not bool(gate_lookup.get(pd.Timestamp(day), False)) for day in pd.to_datetime(opens["eval_date"]))
        overlay_audits.append(
            {
                "candidate": label,
                "eligible_signal_days": int(signals[label].sum()) if label in signals.columns else len(signals),
                "overlay_open_signals": len(opens),
                "overlay_trade_events": len(trades),
                "overlay_call_days": int(overlay["call_contract"].fillna("").ne("").sum()),
                "entry_gate_violations": int(violations),
                "entry_iv_gate_violations": int((~opens["entry_iv_gate_pass"].astype(bool)).sum()) if label == "iv26_safety15_2" else 0,
                "threat_signals": int(stats["threat_signals"]),
                "threat_rolls": int(stats["threat_rolls"]),
                "threat_stops": int(stats["threat_no_contract_stops"] + stats["threat_max5_stops"]),
                "scheduled_execution_failures": int(stats["scheduled_execution_failures"]),
                "delayed_trading_days": int(stats["delayed_trading_days"]),
                "expiry_safety_closes": int(stats["expiry_safety_closes"]),
            }
        )

    source = DatedSources()
    seller_candidates = pd.read_csv(NATIVE / "native_fix4_seller_candidates_v1.csv.gz")
    gov = pd.read_csv(
        SOURCE_ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v4" / "chinabond_government_10y.csv",
        parse_dates=["date"],
    ).set_index("date")["gov10y_yield"]
    spot = pd.read_csv(OHLCV["IM"], parse_dates=["date"]).set_index("date")["close"]

    target_by_candidate = {
        label: frame.drop(columns="candidate") for label, frame in ((f.iloc[0]["candidate"], f) for f in target_frames)
    }
    dailies: list[pd.DataFrame] = []
    journals: list[pd.DataFrame] = []
    account_audits: list[dict[str, Any]] = []
    for label in CANDIDATES:
        targets = original_targets if label in {"no_call_fix6", "old_iv26"} else target_by_candidate[label]
        daily, journal, audit = run_account(
            label,
            targets,
            label == "no_call_fix6",
            source,
            lifecycle,
            seller_candidates,
            gov,
            spot,
        )
        dailies.append(daily)
        journals.append(journal)
        holding, sides, opens = call_event_counts(journal, daily)
        account_gate_violations = (
            account_entry_gate_violations(journal, gate_maps[label]) if label in gate_maps else 0
        )
        account_audits.append(
            {
                "candidate": label,
                **audit,
                "call_holding_days": holding,
                "call_trade_sides": sides,
                "call_open_sides": opens,
                "account_entry_gate_violations": account_gate_violations,
            }
        )

    all_daily = pd.concat(dailies, ignore_index=True)
    all_events = pd.concat(journals, ignore_index=True)

    frozen_no_call = pd.read_csv(NO_CALL / "daily_outputs.csv.gz")
    frozen_no_call = frozen_no_call[frozen_no_call["candidate"].eq("IM_repeat_roll_3p0x")].reset_index(drop=True)
    frozen_old = pd.read_csv(L8 / "daily_outputs.csv.gz")
    frozen_old = frozen_old[frozen_old["candidate"].eq("IM_baseline_repeat_roll")].reset_index(drop=True)
    generated_no_call = all_daily[all_daily["candidate"].eq("no_call_fix6")].reset_index(drop=True)
    generated_old = all_daily[all_daily["candidate"].eq("old_iv26")].reset_index(drop=True)
    if not generated_no_call["date"].equals(frozen_no_call["date"]):
        raise RuntimeError("No-Call parity date mismatch")
    if not generated_old["date"].equals(frozen_old["date"]):
        raise RuntimeError("Old IV26 parity date mismatch")
    parity = {
        "no_call_nav_max_abs": float(np.max(np.abs(generated_no_call["nav"].to_numpy(float) - frozen_no_call["nav"].to_numpy(float)))),
        "old_iv26_nav_max_abs": float(np.max(np.abs(generated_old["nav"].to_numpy(float) - frozen_old["nav"].to_numpy(float)))),
    }
    if max(parity.values()) > 1e-10:
        raise RuntimeError(f"Frozen baseline parity failed: {parity}")

    metric_long: list[dict[str, Any]] = []
    metric_wide: list[dict[str, Any]] = []
    unavailable: dict[str, dict[str, str]] = {}
    account_audit_lookup = {row["candidate"]: row for row in account_audits}
    for label in CANDIDATES:
        daily = all_daily[all_daily["candidate"].eq(label)].reset_index(drop=True)
        audit = account_audit_lookup[label]
        rows, wide, missing = metric_rows(
            daily,
            label,
            int(audit["call_holding_days"]),
            int(audit["call_trade_sides"]),
            int(audit["call_open_sides"]),
        )
        metric_long.extend(rows)
        metric_wide.append(wide)
        if missing:
            unavailable[label] = missing

    long = pd.DataFrame(metric_long)
    wide = pd.DataFrame(metric_wide)
    full = long[long["segment"].eq("full")].set_index("candidate")
    base = full.loc["no_call_fix6"]
    primary = full.loc["momneg_fear_gt25"]
    cagr_delta = float(primary["ann_return"] - base["ann_return"])
    dd_delta = float(primary["max_dd"] - base["max_dd"])
    main_gate = bool(
        cagr_delta >= -0.0025 - 1e-12
        and dd_delta >= -0.0025 - 1e-12
        and (cagr_delta >= 0.0025 - 1e-12 or dd_delta >= 0.0025 - 1e-12)
    )
    recent = long[long["segment"].isin(["last_3y", "last_1y"])].pivot(index="candidate", columns="segment", values="ann_return")
    recent_both_worse = bool(
        float(recent.loc["momneg_fear_gt25", "last_3y"]) < float(recent.loc["no_call_fix6", "last_3y"])
        and float(recent.loc["momneg_fear_gt25", "last_1y"]) < float(recent.loc["no_call_fix6", "last_1y"])
    )
    audit_pass = bool(
        max(parity.values()) <= 1e-10
        and all(row["entry_gate_violations"] == 0 for row in overlay_audits)
        and all(row["entry_iv_gate_violations"] == 0 for row in overlay_audits)
        and all(row["scheduled_execution_failures"] == 0 for row in overlay_audits)
        and all(row["account_entry_gate_violations"] == 0 for row in account_audits)
    )
    decision = "watchlist" if main_gate and not recent_both_worse and audit_pass else "keep_default"
    stability = "data_sensitive" if decision == "watchlist" else "reject"

    long.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    all_daily.to_csv(RUN / "daily_outputs.csv.gz", index=False, compression="gzip")
    all_events.to_csv(RUN / "events.csv.gz", index=False, compression="gzip")
    pd.concat(target_frames, ignore_index=True).to_csv(RUN / "call_target_schedules.csv.gz", index=False, compression="gzip")
    pd.DataFrame(overlay_audits).to_csv(RUN / "call_overlay_audit.csv", index=False)
    signals.to_csv(RUN / "signal_inputs.csv.gz", index=False, compression="gzip")

    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in (
            SPEC_PATH,
            MOM_PATH,
            FEAR_PATH,
            TARGET_PATH,
            ROOT / "im_mo_call_daily_d10_threat_roll_v27.py",
            ROOT / "im_mo_call_valuation_threat_roll_v25.py",
            NATIVE / "native_fix4_account_v1.py",
            NO_CALL / "daily_outputs.csv.gz",
            L8 / "daily_outputs.csv.gz",
        )
    }
    run_audit = {
        "classification": "IM_CALL_ENTRY_REGIME_RESEARCH_ONLY",
        "decision": decision,
        "stability_label": stability,
        "primary_candidate": "momneg_fear_gt25",
        "primary_vs_no_call": {"cagr_delta": cagr_delta, "max_dd_delta": dd_delta},
        "primary_gate_pass": main_gate,
        "recent_both_worse": recent_both_worse,
        "baseline_parity": parity,
        "market_checks": market_checks,
        "overlay_audits": overlay_audits,
        "account_audits": account_audits,
        "unavailable_segments": unavailable,
        "fear_point_in_time_verified": False,
        "production_source_changed": False,
        "all_pass": audit_pass,
    }
    (RUN / "run_audit.json").write_text(json.dumps(run_audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    display = long[long["segment"].isin(["full", "last_3y", "last_1y"])].copy()
    table_lines = [
        "|候选|窗口|CAGR|Sharpe|MaxDD|Call持有日|开仓侧|",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in display.itertuples(index=False):
        table_lines.append(
            f"|{row.candidate}|{row.segment}|{float(row.ann_return):.2%}|{float(row.sharpe_repo):.3f}|{float(row.max_dd):.2%}|{int(row.call_holding_days)}|{int(row.call_open_events)}|"
        )
    record = f"""# IM 卖 Call：MOM120 与恐贪入场状态扫描

## Run Metadata

- Run id: `{RUN.name}`
- Run date: {datetime.now().astimezone().isoformat(timespec='seconds')}
- Timezone: Asia/Shanghai
- Project: IC和IM滚动套利
- Strategy: v1.4-fix9 no-Call baseline; historical native account replay
- Subsystem: IM fixed-core covered Call
- Parameter group: MOM120 and Fear/Greed entry regime
- Scan type: candidate_bundle
- Git commit: `{git_value('rev-parse', 'HEAD')}`
- Working tree was already dirty; this run added only research artifacts and did not edit production source.

## Research Question

- Baseline: `no_call_fix6`.
- Candidate grid: {', '.join(CANDIDATES)}.
- Decision target: keep_default or watchlist; Fear snapshot prevents promotion.
- Source-change rule: research_only_no_source_change.
- Required windows: full/3Y/1Y; 5Y/10Y are N/A because native listed-option history is shorter.
- Promotion threshold: frozen in `preregistered_spec.md`.
- Rerun triggers: new point-in-time Fear archive, changed native account, changed Call lifecycle, or new complete MO history.

## Implementation Anchor

- Official historical account path: `native_fix4_account_v1.replay` with v7/mixed/v4/repeat_roll/3x settings.
- Call path: `im_mo_call_daily_d10_threat_roll_v27` selectors plus `im_mo_call_valuation_threat_roll_v25` lifecycle.
- Runtime override: target table intercepted in memory; production files unchanged.
- Baseline parity: no-Call `{parity['no_call_nav_max_abs']:.3e}`, old IV26 `{parity['old_iv26_nav_max_abs']:.3e}` max absolute NAV error.

## Data Snapshot

- Account window: 2022-07-22—2026-08-14, 986 rows.
- Signal end: 2026-08-13.
- MOM120: native risk-signal file.
- Fear: 2026-09-28 downloaded historical snapshot; historical point-in-time availability unverified.
- Trading calendar/timezone: dated IM/MO sessions, Asia/Shanghai.
- Adjustment mode: official futures/option dated quotes; no equity-adjustment transformation.

## Cost and Execution Assumptions

- Futures 1bp one-way; bought Put 5bp; IM Call 1bp.
- T-close signal, T+1 dated close Call paper fill using positive historical quote.
- 30% futures performance buffer per 1x; remaining cash earns 3% net annualized.
- Historical paper market-maker quote assumption is not a broker fill receipt.

## Full-Sample Results

{chr(10).join(table_lines)}

Full detail: `scan_summary.csv`; wide windows: `window_metrics.csv`.

## Stability Classification

- Label: `{stability}`.
- Fear-containing paths remain data-sensitive because the downloaded history is not a point-in-time archive.
- The 25/35/45 neighborhood is reported together; no exact threshold is promoted from this repeatedly researched short sample.

## Decision

- Decision: `{decision}`.
- Primary candidate Full CAGR delta versus no Call: {cagr_delta:+.2%}; MaxDD delta: {dd_delta:+.2%}.
- Primary mechanical gate pass: {main_gate}; 3Y and 1Y both worse: {recent_both_worse}; audit pass: {audit_pass}.
- Production action: none. Current IM no-Call rule remains unchanged.

## User-Facing Summary

This is a same-window native-account historical counterfactual. It tests the user's proposed `MOM120<0` rule while blocking new Call sales in fear states, plus greed controls. Results are not live approval or trade instructions.
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"candidate": "no_call_fix6", "definition": "native repeat-roll 3x account with Call disabled"},
            "candidate_grid": list(CANDIDATES),
            "data_snapshot": {
                "start": "2022-07-22",
                "end": "2026-08-14",
                "rows": 986,
                "mom120_source": str(MOM_PATH),
                "fear_source": str(FEAR_PATH),
                "fear_point_in_time_verified": False,
            },
            "cost_model": {
                "future_one_way": 0.0001,
                "buyer_put_one_way": 0.0005,
                "im_call_one_way": 0.0001,
                "cash_annual_net": 0.03,
                "futures_performance_buffer_per_1x": 0.30,
            },
            "parity_check": parity,
            "unavailable_segments": unavailable,
            "source_hashes": source_hashes,
            "outputs": {
                "record": str(RUN / "record.md"),
                "scan_summary": str(RUN / "scan_summary.csv"),
                "window_metrics": str(RUN / "window_metrics.csv"),
                "scan_meta": str(RUN / "scan_meta.json"),
                "command_log": str(RUN / "command_log.txt"),
                "daily_outputs": str(RUN / "daily_outputs.csv.gz"),
                "run_audit": str(RUN / "run_audit.json"),
            },
            "decision": decision,
            "stability_label": stability,
            "elapsed_sec": time.perf_counter() - started,
            "warnings": [
                "Fear history is a later-downloaded snapshot, not a verified point-in-time archive.",
                "Native real listed-option history is shorter than five full years.",
                "Worktree was dirty before this research run.",
            ],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\n")
        handle.write("python -X utf8 research_im_call_mom_fear_entry_v1.py\n")
        handle.write(f"elapsed_sec={time.perf_counter() - started:.3f}\n")

    print(json.dumps({"decision": decision, "stability": stability, "parity": parity, "primary_cagr_delta": cagr_delta, "primary_max_dd_delta": dd_delta}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
