#!/usr/bin/env python
"""IC rolling-arbitrage research candidate v1.2 with a 50% momentum sleeve.

The core sleeve keeps the full frozen IC V2 Put.  The momentum sleeve uses
valuation-only Put protection, excluding its redundant MOM120 floor.  The
frozen IC valuation grid remains independent and unprotected.  No Call exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_roll_momentum_stage3_grid_v1 as grid_source
import im_mainline_v1_2 as shared


ROOT = Path(__file__).resolve().parent
VERSION = "ic_mainline_v1_2"
STATUS = "research_candidate_not_live_authority"
PARENT_VERSION = "ic_im_system_mainlines_v2__ic"
RESEARCH_START = pd.Timestamp("2015-04-16")

CORE_CAPITAL_SHARE = 0.50
MOMENTUM_CAPITAL_SHARE = 0.50
PER_IC_MARGIN_BUFFER = 0.30
GRID_ADDITIONAL_UNITS = 1.0
GRID_ENTRY = 0.375
GRID_EXIT = 1.000

STAGE1_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage1_v1" / "daily_nav.csv.gz"
STAGE2_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage2_put_v2" / "daily_nav.csv.gz"
STAGE2_SCHEDULE = (
    ROOT / "outputs" / "ic_roll_momentum_stage2_put_v2" / "put_target_schedule.csv.gz"
)
SPEC_PATH = ROOT / "docs" / "ic_mainline_v1_2_spec.md"
A_SHARE_V13_BOT = shared.A_SHARE_V13_BOT


@dataclass(frozen=True)
class MomentumPolicy:
    source_version: str = "cn_four_index_raw_momentum_combo_v1_3"
    source_sleeve: str = "zz500"
    bias_ma: int = 110
    momentum_days: int = 24
    linear_weight_end: float = 2.0
    score_threshold: float = 0.0
    absolute_momentum_days: int = 20
    absolute_momentum_threshold: float = 0.0
    absolute_filter_share: float = 0.5
    signal_targets: tuple[float, ...] = (0.0, 0.5, 1.0)
    execution: str = "T_close_to_next_common_session_position"


@dataclass(frozen=True)
class PutPolicy:
    valuation_thresholds: tuple[float, ...] = (1.90, 1.95, 2.00, 2.05)
    valuation_target_deltas: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00)
    core_mom120_negative_floor_delta: float = 0.50
    momentum_sleeve_uses_mom120_floor: bool = False
    tenor_months: int = 3
    moneyness: float = 0.95
    execution: str = "T_close_to_next_common_session_close"


MOMENTUM_POLICY = MomentumPolicy()
PUT_POLICY = PutPolicy()


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def calc_bias_momentum(close: pd.Series) -> pd.Series:
    """Calculate the exact A-share v1.3 ZZ500 weighted bias-momentum Score."""

    return shared.calc_bias_momentum(
        close,
        bias_ma=MOMENTUM_POLICY.bias_ma,
        momentum_days=MOMENTUM_POLICY.momentum_days,
        linear_weight_end=MOMENTUM_POLICY.linear_weight_end,
    )


def momentum_signal_target(score: pd.Series, abs20: pd.Series) -> pd.Series:
    score_on = pd.to_numeric(score, errors="coerce").gt(MOMENTUM_POLICY.score_threshold)
    abs_on = pd.to_numeric(abs20, errors="coerce").gt(
        MOMENTUM_POLICY.absolute_momentum_threshold
    )
    target = score_on.astype(float) * (
        (1.0 - MOMENTUM_POLICY.absolute_filter_share)
        + MOMENTUM_POLICY.absolute_filter_share * abs_on.astype(float)
    )
    return target.rename("momentum_signal_target")


def build_momentum_schedule(close: pd.Series) -> pd.DataFrame:
    close = pd.to_numeric(close, errors="coerce").astype(float)
    if close.empty:
        raise ValueError("close is empty")
    if close.index.has_duplicates:
        raise ValueError("close index contains duplicate dates")
    if not close.index.is_monotonic_increasing:
        close = close.sort_index()
    score = calc_bias_momentum(close)
    abs20 = (close / close.shift(MOMENTUM_POLICY.absolute_momentum_days) - 1.0).rename(
        "abs20"
    )
    signal = momentum_signal_target(score, abs20)
    execution = signal.shift(1, fill_value=0.0).rename("momentum_execution_weight")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(close.index),
            "close": close.to_numpy(dtype=float),
            "score": score.to_numpy(dtype=float),
            "abs20": abs20.to_numpy(dtype=float),
            "momentum_signal_target": signal.to_numpy(dtype=float),
            "momentum_execution_weight": execution.to_numpy(dtype=float),
        }
    )


def compose_target_schedule(
    momentum: pd.DataFrame,
    put_schedule: pd.DataFrame,
    grid: pd.DataFrame,
    executed_put: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compose the IC v1.2 target schedule from audited upstream components."""

    required_momentum = {
        "date",
        "score",
        "abs20",
        "momentum_signal_target",
        "momentum_execution_weight",
    }
    missing = sorted(required_momentum.difference(momentum.columns))
    if missing:
        raise ValueError(f"Missing momentum columns: {missing}")
    mom = momentum.copy()
    mom["date"] = pd.to_datetime(mom["date"])
    mom = mom.sort_values("date").reset_index(drop=True)
    if mom.empty or mom["date"].duplicated().any():
        raise ValueError("Momentum dates are empty or duplicated")

    allowed = np.asarray(MOMENTUM_POLICY.signal_targets, dtype=float)
    for column in ("momentum_signal_target", "momentum_execution_weight"):
        values = pd.to_numeric(mom[column], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{column} contains non-finite values")
        valid = np.isclose(values[:, None], allowed[None, :], atol=1e-12).any(axis=1)
        if not valid.all():
            raise ValueError(f"{column} contains values outside 0/0.5/1")
    expected_execution = mom["momentum_signal_target"].shift(1).to_numpy(dtype=float)
    actual_execution = mom["momentum_execution_weight"].to_numpy(dtype=float)
    if len(mom) > 1 and not np.allclose(
        actual_execution[1:], expected_execution[1:], atol=1e-12, rtol=0.0
    ):
        raise ValueError("Momentum execution weight is not the prior-session close target")

    required_put = {
        "execution_date",
        "eval_date",
        "valuation_tier_new",
        "v2_target_delta",
        "valuation_only_target_delta",
        "bare_full_target_delta",
        "momentum_valuation_target_delta",
        "target_delta",
        "momentum_weight",
        "momentum_120",
        "mom_floor_binding",
    }
    missing = sorted(required_put.difference(put_schedule.columns))
    if missing:
        raise ValueError(f"Missing Put schedule columns: {missing}")
    put = put_schedule.copy()
    put["date"] = pd.to_datetime(put["execution_date"])
    put["put_eval_date"] = pd.to_datetime(put["eval_date"])
    put = put.sort_values("date").reset_index(drop=True)
    if put.empty or put["date"].duplicated().any():
        raise ValueError("Put schedule dates are empty or duplicated")

    required_grid = {
        "date",
        "overlay_held_eod",
        "overlay_buy",
        "overlay_sell",
        "signal_date_executed",
        "valuation_score",
    }
    missing = sorted(required_grid.difference(grid.columns))
    if missing:
        raise ValueError(f"Missing grid columns: {missing}")
    grid_frame = grid.copy()
    grid_frame["date"] = pd.to_datetime(grid_frame["date"])
    grid_frame = grid_frame.sort_values("date").reset_index(drop=True)
    if grid_frame.empty or grid_frame["date"].duplicated().any():
        raise ValueError("Grid dates are empty or duplicated")

    frame = mom.merge(
        put[
            [
                "date",
                "put_eval_date",
                "valuation_tier_new",
                "v2_target_delta",
                "valuation_only_target_delta",
                "bare_full_target_delta",
                "momentum_valuation_target_delta",
                "target_delta",
                "momentum_weight",
                "momentum_120",
                "mom_floor_binding",
            ]
        ],
        on="date",
        how="inner",
        validate="one_to_one",
        suffixes=("", "_put_source"),
    )
    frame = frame.merge(
        grid_frame[
            [
                "date",
                "overlay_held_eod",
                "overlay_buy",
                "overlay_sell",
                "signal_date_executed",
                "valuation_score",
            ]
        ].rename(
            columns={
                "overlay_held_eod": "grid_held_eod",
                "overlay_buy": "grid_buy",
                "overlay_sell": "grid_sell",
                "signal_date_executed": "grid_signal_date_executed",
                "valuation_score": "grid_valuation_score",
            }
        ),
        on="date",
        how="inner",
        validate="one_to_one",
    )
    if len(frame) != len(mom) or len(frame) != len(put) or len(frame) != len(grid_frame):
        raise ValueError("Momentum, Put and grid dates do not align one-to-one")

    frame["core_ic_units"] = CORE_CAPITAL_SHARE
    frame["momentum_ic_units"] = (
        MOMENTUM_CAPITAL_SHARE * frame["momentum_execution_weight"].astype(float)
    )
    frame["grid_ic_units"] = GRID_ADDITIONAL_UNITS * frame["grid_held_eod"].astype(float)
    frame["total_ic_units"] = (
        frame["core_ic_units"] + frame["momentum_ic_units"] + frame["grid_ic_units"]
    )

    frame["core_put_target_delta"] = CORE_CAPITAL_SHARE * frame[
        "v2_target_delta"
    ].astype(float)
    frame["momentum_put_target_delta"] = (
        MOMENTUM_CAPITAL_SHARE
        * frame["momentum_execution_weight"].astype(float)
        * frame["valuation_only_target_delta"].astype(float)
    )
    frame["grid_put_target_delta"] = 0.0
    frame["total_put_target_delta"] = (
        frame["core_put_target_delta"] + frame["momentum_put_target_delta"]
    )
    frame["call_target_contracts"] = 0.0
    frame["has_call"] = False
    frame["margin_buffer_fraction"] = PER_IC_MARGIN_BUFFER * frame["total_ic_units"]

    if executed_put is not None:
        executed = executed_put.copy()
        executed["date"] = pd.to_datetime(executed["date"])
        executed = executed.sort_values("date")
        columns = {
            "put_momentum_valuation_only_put_contract": "executed_put_contract",
            "put_momentum_valuation_only_put_qty": "executed_put_qty",
            "put_momentum_valuation_only_target_delta": "executed_put_target_delta",
            "put_momentum_valuation_only_layer": "executed_put_layer",
        }
        missing = sorted(set(columns).difference(executed.columns))
        if missing:
            raise ValueError(f"Missing executed Put columns: {missing}")
        frame = frame.merge(
            executed[["date", *columns]].rename(columns=columns),
            on="date",
            how="left",
            validate="one_to_one",
        )
        if frame["executed_put_target_delta"].isna().any():
            raise ValueError("Executed Put path does not cover all dates")
    return frame


def load_authoritative_local_state() -> tuple[pd.DataFrame, dict[str, Any]]:
    for path in (STAGE1_DAILY, STAGE2_DAILY, STAGE2_SCHEDULE):
        if not path.is_file():
            raise FileNotFoundError(path)

    stage1 = pd.read_csv(STAGE1_DAILY, compression="gzip")
    stage1["date"] = pd.to_datetime(stage1["date"])
    momentum = stage1[
        ["date", "score", "abs20", "desired_weight", "momentum_weight"]
    ].rename(
        columns={
            "desired_weight": "momentum_signal_target",
            "momentum_weight": "momentum_execution_weight",
        }
    )

    raw_schedule = pd.read_csv(STAGE2_SCHEDULE, compression="gzip", low_memory=False)
    put = raw_schedule[
        raw_schedule["layer"].eq("model")
        & raw_schedule["signal_variant"].eq("put_momentum_valuation_only")
    ].copy()
    executed_put = pd.read_csv(STAGE2_DAILY, compression="gzip", low_memory=False)
    grid_base = grid_source.load_base()
    grid, _events, grid_audit = grid_source.load_grid(grid_base)
    schedule = compose_target_schedule(momentum, put, grid, executed_put)

    expected_signal = momentum_signal_target(schedule["score"], schedule["abs20"])
    signal_error = float(
        np.max(
            np.abs(
                expected_signal.to_numpy(dtype=float)
                - schedule["momentum_signal_target"].to_numpy(dtype=float)
            )
        )
    )
    lag_error = float(
        np.max(
            np.abs(
                schedule["momentum_execution_weight"].iloc[1:].to_numpy(dtype=float)
                - schedule["momentum_signal_target"].shift(1).iloc[1:].to_numpy(dtype=float)
            )
        )
    )
    source_weight_error = float(
        np.max(
            np.abs(
                schedule["momentum_execution_weight"]
                - schedule["momentum_weight"]
            )
        )
    )
    core_units_error = float(np.max(np.abs(schedule["core_ic_units"] - 0.5)))
    momentum_units_error = float(
        np.max(
            np.abs(
                schedule["momentum_ic_units"]
                - 0.5 * schedule["momentum_execution_weight"]
            )
        )
    )
    total_units_error = float(
        np.max(
            np.abs(
                schedule["total_ic_units"]
                - schedule["core_ic_units"]
                - schedule["momentum_ic_units"]
                - schedule["grid_ic_units"]
            )
        )
    )
    core_put_error = float(
        np.max(
            np.abs(schedule["core_put_target_delta"] - schedule["bare_full_target_delta"])
        )
    )
    momentum_put_error = float(
        np.max(
            np.abs(
                schedule["momentum_put_target_delta"]
                - schedule["momentum_valuation_target_delta"]
            )
        )
    )
    total_put_error = float(
        np.max(np.abs(schedule["total_put_target_delta"] - schedule["target_delta"]))
    )
    executed_put_error = float(
        np.max(
            np.abs(
                schedule["total_put_target_delta"]
                - schedule["executed_put_target_delta"]
            )
        )
    )
    flat = schedule["momentum_execution_weight"].eq(0.0)
    flat_momentum_put = float(schedule.loc[flat, "momentum_put_target_delta"].abs().max())
    latest = schedule.iloc[-1]
    audit = {
        "version": VERSION,
        "status": STATUS,
        "start": schedule["date"].min().date().isoformat(),
        "end": schedule["date"].max().date().isoformat(),
        "rows": int(len(schedule)),
        "source_signal_rule_max_abs_error": signal_error,
        "momentum_t_plus_1_max_abs_error": lag_error,
        "put_source_momentum_weight_max_abs_error": source_weight_error,
        "core_units_formula_max_abs_error": core_units_error,
        "momentum_units_formula_max_abs_error": momentum_units_error,
        "total_units_formula_max_abs_error": total_units_error,
        "core_put_formula_max_abs_error": core_put_error,
        "momentum_valuation_put_formula_max_abs_error": momentum_put_error,
        "total_put_formula_max_abs_error": total_put_error,
        "executed_put_target_parity_max_abs_error": executed_put_error,
        "flat_momentum_put_max_abs": flat_momentum_put,
        "call_nonzero_rows": int(schedule["call_target_contracts"].ne(0.0).sum()),
        "grid_put_nonzero_rows": int(schedule["grid_put_target_delta"].ne(0.0).sum()),
        "grid_independent_of_momentum": True,
        "grid_t_plus_1_execution": bool(grid_audit["grid_signal_date_overlap_equal"]),
        "grid_entries": int(grid_audit["grid_entries"]),
        "grid_exits": int(grid_audit["grid_exits"]),
        "grid_holding_days": int(grid_audit["grid_holding_days"]),
        "momentum_execution_weight_counts": {
            str(float(key)): int(value)
            for key, value in schedule["momentum_execution_weight"].value_counts().sort_index().items()
        },
        "latest_state": {
            "date": latest["date"].date().isoformat(),
            "momentum_signal_target": float(latest["momentum_signal_target"]),
            "momentum_execution_weight": float(latest["momentum_execution_weight"]),
            "core_ic_units": float(latest["core_ic_units"]),
            "momentum_ic_units": float(latest["momentum_ic_units"]),
            "grid_ic_units": float(latest["grid_ic_units"]),
            "total_ic_units": float(latest["total_ic_units"]),
            "core_put_target_delta": float(latest["core_put_target_delta"]),
            "momentum_put_target_delta": float(latest["momentum_put_target_delta"]),
            "total_put_target_delta": float(latest["total_put_target_delta"]),
            "executed_put_contract": str(latest["executed_put_contract"]),
            "executed_put_qty": float(latest["executed_put_qty"]),
        },
        "grid_upstream_audit": grid_audit,
    }
    return schedule, audit


def rule_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": STATUS,
        "research_start": RESEARCH_START.date().isoformat(),
        "parent_version": PARENT_VERSION,
        "capital_sleeves": {
            "core_share": CORE_CAPITAL_SHARE,
            "momentum_share": MOMENTUM_CAPITAL_SHARE,
            "core_always_held": True,
        },
        "momentum": asdict(MOMENTUM_POLICY),
        "put": asdict(PUT_POLICY),
        "grid": {
            "entry_lte": GRID_ENTRY,
            "exit_gte": GRID_EXIT,
            "additional_units": GRID_ADDITIONAL_UNITS,
            "independent_of_momentum": True,
            "put_covered": False,
            "call_covered": False,
            "execution": "T_close_to_next_session_open",
        },
        "call": {"included": False},
        "provenance": {
            "momentum": str(STAGE1_DAILY),
            "put_schedule": str(STAGE2_SCHEDULE),
            "put_execution": str(STAGE2_DAILY),
            "grid": "ic_roll_momentum_stage3_grid_v1.load_grid",
            "a_share_v1_3": str(A_SHARE_V13_BOT),
            "research_record": "2026-08-23_滚IC滚IM叠加动量完整研究记录.md",
            "spec": str(SPEC_PATH),
            "source_sha256": {
                "stage1_daily": _sha256(STAGE1_DAILY),
                "stage2_schedule": _sha256(STAGE2_SCHEDULE),
                "stage2_daily": _sha256(STAGE2_DAILY),
                "a_share_v1_3": _sha256(A_SHARE_V13_BOT),
                "spec": _sha256(SPEC_PATH),
            },
        },
        "performance_claim": "none_new_combined_put_grid_schedule_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-local", action="store_true")
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    if (args.output_csv or args.output_json) and not args.audit_local:
        parser.error("--output-csv/--output-json require --audit-local")

    payload: dict[str, Any] = {"rules": rule_manifest()}
    if args.audit_local:
        schedule, audit = load_authoritative_local_state()
        payload["local_audit"] = audit
        if args.output_csv:
            args.output_csv.parent.mkdir(parents=True, exist_ok=True)
            schedule.to_csv(args.output_csv, index=False)
            payload["schedule_output"] = str(args.output_csv.resolve())
        if args.output_json:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            payload["audit_output"] = str(args.output_json.resolve())
            args.output_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

