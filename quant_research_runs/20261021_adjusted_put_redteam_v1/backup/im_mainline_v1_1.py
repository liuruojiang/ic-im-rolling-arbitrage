#!/usr/bin/env python
"""IM research-candidate rule set v1.1.

This file records the two approved-for-research rule changes without modifying
the frozen IC/IM V2 mainline:

* independent IM valuation grid: enter <= 1.60, exit >= 2.00;
* four-tier IM Put: MOM120 < 0 sets a three-contract floor, while the fourth
  valuation tier remains the only route to four contracts.

The module generates signal/target schedules only.  It does not place orders.
Put contract selection/pricing and the unchanged Call implementation remain in
the audited upstream research paths named in ``PROVENANCE``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_mainline_v1_1"
STATUS = "research_candidate_not_live_authority"
RESEARCH_START = pd.Timestamp("2015-04-16")

# Existing IM core and valuation definitions are unchanged.
CORE_IM_UNITS = 1.0
PUT_ABSOLUTE_THRESHOLDS = (2.45, 2.50, 2.60)
PUT_RELATIVE_QUANTILES = (0.750, 0.850, 0.900, 0.925)
PUT_MAX_QTY = 4
MOM120_NEGATIVE_FLOOR_QTY = 3
MOM120_BOUNDARY = "strictly_less_than_zero"

# v1.1 grid change.  The added grid unit is deliberately unhedged by Put/Call.
GRID_ENTRY = 1.60
GRID_EXIT = 2.00
GRID_ADDITIONAL_UNITS = 1.0
GRID_ONE_WAY_COST = 0.0001
PER_IM_MARGIN_BUFFER = 0.30

PUT_TENOR_MONTHS = 3
PUT_MONEYNESS = 0.95
PUT_SIGNAL_TO_EXECUTION = "T_close_to_next_session_close"
GRID_SIGNAL_TO_EXECUTION = "T_close_to_next_session_open"

PROVENANCE = {
    "frozen_authority": "outputs/ic_im_system_mainlines_v2",
    "grid_research": "outputs/im_fixed_valuation_overlay_model2015_avg_basis_scan_v21",
    "put_research": "quant_param_scan_runs/20260823_im_grid160_put_carry_scan_v23",
    "put_execution": "im_mo_close_execution_v8.py",
    "call_execution": "im_mo_call_daily_d10_threat_roll_v27.py",
}


@dataclass(frozen=True)
class CallPolicy:
    """Unchanged IM Call policy carried into v1.1."""

    target_days_to_expiry: int = 10
    minimum_implied_volatility: float = 0.26
    threat_rescue_ratio: float = 0.05
    maximum_rescues: int = 5
    rescue_expiry_rule: str = "rescue_next_listed"
    coverage: str = "core_im_only"
    grid_unit_covered: bool = False


CALL_POLICY = CallPolicy()


def put_target_qty(
    absolute_tier: int,
    relative_tier: int,
    momentum_120: float | None,
) -> int:
    """Return the v1.1 IM Put target in normalized contracts per 1x core IM.

    ``absolute_tier`` is 0..3 and ``relative_tier`` is 0..4.  The valuation
    target is their maximum.  Strictly negative MOM120 creates a floor of 3,
    not 4; therefore only valuation tier 4 can produce a four-contract target.
    """

    if isinstance(absolute_tier, bool) or not isinstance(absolute_tier, (int, np.integer)):
        raise TypeError("absolute_tier must be an integer")
    if isinstance(relative_tier, bool) or not isinstance(relative_tier, (int, np.integer)):
        raise TypeError("relative_tier must be an integer")
    absolute_tier = int(absolute_tier)
    relative_tier = int(relative_tier)
    if not 0 <= absolute_tier <= 3:
        raise ValueError("absolute_tier must be in 0..3")
    if not 0 <= relative_tier <= 4:
        raise ValueError("relative_tier must be in 0..4")

    valuation_target = max(absolute_tier, relative_tier)
    negative = momentum_120 is not None and not pd.isna(momentum_120) and float(momentum_120) < 0.0
    momentum_floor = MOM120_NEGATIVE_FLOOR_QTY if negative else 0
    target = max(valuation_target, momentum_floor)
    if target == 4 and valuation_target != 4:
        raise AssertionError("Only fourth valuation tier may create four-Put target")
    return target


def grid_close_signal(score: float, held_after_execution: bool) -> str:
    """Return the order signal produced at today's close for next open."""

    if pd.isna(score):
        return "none"
    value = float(score)
    if not held_after_execution and value <= GRID_ENTRY:
        return "buy_next_open"
    if held_after_execution and value >= GRID_EXIT:
        return "sell_next_open"
    return "none"


def _validate_state(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date",
        "valuation_score",
        "absolute_tier",
        "relative_tier",
        "valuation_tier",
        "momentum_120",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Missing state columns: {missing}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values("date").reset_index(drop=True)
    if result.empty:
        raise ValueError("State is empty")
    if result["date"].duplicated().any():
        raise ValueError("Duplicate state dates")
    if not result["absolute_tier"].astype(int).between(0, 3).all():
        raise ValueError("absolute_tier outside 0..3")
    if not result["relative_tier"].astype(int).between(0, 4).all():
        raise ValueError("relative_tier outside 0..4")
    expected = np.maximum(
        result["absolute_tier"].astype(int).to_numpy(),
        result["relative_tier"].astype(int).to_numpy(),
    )
    if not np.array_equal(expected, result["valuation_tier"].astype(int).to_numpy()):
        raise ValueError("valuation_tier is not max(absolute_tier, relative_tier)")
    return result


def build_target_schedule(
    state: pd.DataFrame,
    *,
    initial_grid_held: bool = False,
) -> pd.DataFrame:
    """Build an auditable daily v1.1 signal schedule with T+1 execution.

    Put targets generated from T close become execution targets on the next
    available session close.  Grid orders generated from T close execute at the
    next available session open.  Grid holdings never alter Put or Call targets.
    """

    frame = _validate_state(state)
    frame["mom120_active"] = frame["momentum_120"].notna() & frame["momentum_120"].lt(0.0)
    frame["mom120_floor_qty"] = np.where(
        frame["mom120_active"], MOM120_NEGATIVE_FLOOR_QTY, 0
    ).astype(int)
    frame["put_signal_target_qty"] = np.maximum(
        frame["valuation_tier"].astype(int).to_numpy(),
        frame["mom120_floor_qty"].to_numpy(dtype=int),
    ).astype(int)
    if not frame["put_signal_target_qty"].between(0, PUT_MAX_QTY).all():
        raise RuntimeError("Put target outside 0..4")
    invalid_four = frame["put_signal_target_qty"].eq(4) & frame["valuation_tier"].ne(4)
    if invalid_four.any():
        raise RuntimeError("Four-Put target was not caused by fourth valuation tier")

    frame["put_execution_target_qty"] = (
        frame["put_signal_target_qty"].shift(1).fillna(0).astype(int)
    )

    held = bool(initial_grid_held)
    pending = "none"
    held_before_rows: list[bool] = []
    held_eod_rows: list[bool] = []
    executed_rows: list[str] = []
    signal_rows: list[str] = []
    for row in frame.itertuples(index=False):
        held_before_rows.append(held)
        executed = pending
        if pending == "buy_next_open":
            if held:
                raise RuntimeError("Duplicate grid buy")
            held = True
        elif pending == "sell_next_open":
            if not held:
                raise RuntimeError("Grid sell while flat")
            held = False
        executed_rows.append(executed)
        signal = grid_close_signal(float(row.valuation_score), held)
        signal_rows.append(signal)
        pending = signal
        held_eod_rows.append(held)

    frame["grid_held_before_open"] = held_before_rows
    frame["grid_executed_at_open"] = executed_rows
    frame["grid_signal_at_close"] = signal_rows
    frame["grid_held_eod"] = held_eod_rows
    frame["core_im_units"] = CORE_IM_UNITS
    frame["grid_im_units"] = frame["grid_held_eod"].astype(float) * GRID_ADDITIONAL_UNITS
    frame["total_im_units"] = frame["core_im_units"] + frame["grid_im_units"]
    frame["put_covered_im_units"] = CORE_IM_UNITS
    frame["call_covered_im_units"] = CORE_IM_UNITS
    frame["grid_put_qty"] = 0
    frame["grid_call_qty"] = 0
    frame.attrs["pending_grid_order_after_last_close"] = pending
    return frame


def load_authoritative_local_state() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the current audited valuation features, then apply v1.1 rules."""

    import im_roll50_momentum50_fullcycle_put_v1 as source

    state, upstream_audit = source.current_rule_state()
    state = state[state["date"].ge(RESEARCH_START)].copy()
    if state.empty or pd.Timestamp(state["date"].min()) != RESEARCH_START:
        raise RuntimeError("Authoritative local state does not cover the 2015-04-16 research start")
    schedule = build_target_schedule(state)
    audit = {
        "version": VERSION,
        "status": STATUS,
        "research_start": RESEARCH_START.date().isoformat(),
        "start": schedule["date"].min().date().isoformat(),
        "end": schedule["date"].max().date().isoformat(),
        "rows": int(len(schedule)),
        "put_signal_target_counts": {
            str(int(key)): int(value)
            for key, value in schedule["put_signal_target_qty"].value_counts().sort_index().items()
        },
        "four_put_days_without_valuation_tier4": int(
            (
                schedule["put_signal_target_qty"].eq(4)
                & schedule["valuation_tier"].ne(4)
            ).sum()
        ),
        "grid_entry_signals": int(schedule["grid_signal_at_close"].eq("buy_next_open").sum()),
        "grid_exit_signals": int(schedule["grid_signal_at_close"].eq("sell_next_open").sum()),
        "grid_holding_days": int(schedule["grid_held_eod"].sum()),
        "pending_grid_order_after_last_close": schedule.attrs[
            "pending_grid_order_after_last_close"
        ],
        "upstream_state_audit": upstream_audit,
    }
    return schedule, audit


def rule_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": STATUS,
        "research_start": RESEARCH_START.date().isoformat(),
        "im": {
            "core_units": CORE_IM_UNITS,
            "grid": {
                "entry_lte": GRID_ENTRY,
                "exit_gte": GRID_EXIT,
                "additional_units": GRID_ADDITIONAL_UNITS,
                "put_covered": False,
                "call_covered": False,
                "execution": GRID_SIGNAL_TO_EXECUTION,
            },
            "put": {
                "absolute_thresholds": PUT_ABSOLUTE_THRESHOLDS,
                "relative_quantiles": PUT_RELATIVE_QUANTILES,
                "mom120_negative_floor_qty": MOM120_NEGATIVE_FLOOR_QTY,
                "max_qty": PUT_MAX_QTY,
                "fourth_contract_source": "valuation_tier_4_only",
                "tenor_months": PUT_TENOR_MONTHS,
                "moneyness": PUT_MONEYNESS,
                "execution": PUT_SIGNAL_TO_EXECUTION,
            },
            "call": asdict(CALL_POLICY),
        },
        "provenance": PROVENANCE,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-local",
        action="store_true",
        help="apply v1.1 to the current audited local valuation state",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        help="optional target-schedule path; only valid with --audit-local",
    )
    args = parser.parse_args()
    if args.output_csv and not args.audit_local:
        parser.error("--output-csv requires --audit-local")

    payload: dict[str, Any] = {"rules": rule_manifest()}
    if args.audit_local:
        schedule, audit = load_authoritative_local_state()
        payload["local_audit"] = audit
        if args.output_csv:
            args.output_csv.parent.mkdir(parents=True, exist_ok=True)
            schedule.to_csv(args.output_csv, index=False)
            payload["schedule_output"] = str(args.output_csv.resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
