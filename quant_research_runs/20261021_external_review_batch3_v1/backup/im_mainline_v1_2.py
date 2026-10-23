#!/usr/bin/env python
"""IM rolling-arbitrage research candidate v1.2 with a 50% momentum sleeve.

The version inherits IM v1.1 and adds the CSI 1000 raw-momentum rule from the
A-share long-only v1.3 bot.  It emits auditable targets only and never orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mainline_v1_1 as parent


ROOT = Path(__file__).resolve().parent
VERSION = "im_mainline_v1_2"
STATUS = "research_candidate_not_live_authority"
RESEARCH_START = pd.Timestamp("2015-04-16")

CORE_CAPITAL_SHARE = 0.50
MOMENTUM_CAPITAL_SHARE = 0.50
CALL_CONTRACTS_PER_FULL_IM = 2.0

A_SHARE_V13_BOT = (
    ROOT.parent
    / "A 股股指多头策略"
    / "poe_cn_four_index_raw_momentum_combo_v1_3_bot.py"
)
MOMENTUM_AUDIT_PATH = (
    ROOT
    / "outputs"
    / "im_roll50_momentum50_fullcycle_put_v4"
    / "daily_nav.csv.gz"
)
FIXED_COMPONENTS_PATH = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_im_grid160_put_carry_scan_v23"
    / "daily_outputs"
    / "daily_candidates.csv.gz"
)
REAL_IM_START = pd.Timestamp("2022-07-22")
SPEC_PATH = ROOT / "docs" / "im_mainline_v1_2_spec.md"


@dataclass(frozen=True)
class MomentumPolicy:
    source_version: str = "cn_four_index_raw_momentum_combo_v1_3"
    source_sleeve: str = "zz1000"
    bias_ma: int = 35
    momentum_days: int = 18
    linear_weight_end: float = 2.5
    score_threshold: float = 0.0
    absolute_momentum_days: int = 20
    absolute_momentum_threshold: float = 0.0
    absolute_filter_share: float = 0.5
    signal_targets: tuple[float, ...] = (0.0, 0.5, 1.0)
    execution: str = "T_close_to_next_common_session_position"


MOMENTUM_POLICY = MomentumPolicy()


def normalize_daily_dates(values: Any, label: str) -> pd.DatetimeIndex:
    """Return timezone-naive Shanghai trading dates after strict validation."""

    try:
        dates = pd.DatetimeIndex(pd.to_datetime(values, errors="raise"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contains invalid dates") from exc
    if dates.hasnans:
        raise ValueError(f"{label} contains missing dates")
    if dates.tz is not None:
        timezone = str(dates.tz)
        if timezone not in {"Asia/Shanghai", "PRC"}:
            raise ValueError(f"{label} timezone must be Asia/Shanghai, got {timezone}")
        dates = dates.tz_convert("Asia/Shanghai").tz_localize(None)
    if not dates.equals(dates.normalize()):
        raise ValueError(f"{label} must contain date-only midnight timestamps")
    if dates.normalize().duplicated().any():
        raise ValueError(f"{label} contains duplicate calendar dates")
    return dates


def validate_close_series(close: pd.Series, label: str = "close") -> pd.Series:
    """Fail closed on unusable prices and canonicalize the daily date index."""

    if close.empty:
        raise ValueError(f"{label} is empty")
    result = pd.to_numeric(close.copy(), errors="coerce").astype(float)
    dates = normalize_daily_dates(result.index, f"{label} index")
    values = result.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0.0).any():
        raise ValueError(f"{label} contains NaN, infinite, or nonpositive prices")
    result.index = dates
    if not result.index.is_monotonic_increasing:
        result = result.sort_index()
    return result


def normalize_optional_daily_dates(values: Any, label: str) -> pd.Series:
    """Parse optional date-only values while preserving missing inactive expiries."""

    try:
        parsed = pd.to_datetime(values, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contains invalid dates") from exc
    result = pd.Series(parsed, index=getattr(values, "index", None))
    present = result.notna()
    if present.any():
        dates = pd.DatetimeIndex(result.loc[present])
        if dates.tz is not None:
            timezone = str(dates.tz)
            if timezone not in {"Asia/Shanghai", "PRC"}:
                raise ValueError(
                    f"{label} timezone must be Asia/Shanghai, got {timezone}"
                )
            converted = dates.tz_convert("Asia/Shanghai").tz_localize(None)
            result.loc[present] = converted
            dates = converted
        if not dates.equals(dates.normalize()):
            raise ValueError(f"{label} must contain date-only midnight timestamps")
    return pd.to_datetime(result)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write a new text artifact atomically; existing files are immutable."""

    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.rename(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a new CSV (optionally gzip) through a sibling temporary file."""

    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        frame.to_csv(
            temporary,
            index=False,
            compression="gzip" if path.name.endswith(".gz") else None,
        )
        temporary.rename(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def ensure_new_output_paths(*paths: Path | None) -> None:
    """Preflight a multi-artifact write so no partial output is created."""

    selected = [Path(path).resolve() for path in paths if path is not None]
    if len(set(selected)) != len(selected):
        raise ValueError("Output paths must be distinct")
    existing = [path for path in selected if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite existing outputs: {existing}")


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def calc_bias_momentum(
    close: pd.Series,
    bias_ma: int = MOMENTUM_POLICY.bias_ma,
    momentum_days: int = MOMENTUM_POLICY.momentum_days,
    linear_weight_end: float = MOMENTUM_POLICY.linear_weight_end,
) -> pd.Series:
    """Calculate the A-share v1.3 weighted bias-momentum Score exactly."""

    prices = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(prices), np.nan)
    ma = pd.Series(prices, index=close.index).rolling(bias_ma).mean().to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        bias = np.where((ma > 1e-10) & np.isfinite(prices), prices / ma, np.nan)

    x = np.arange(momentum_days, dtype=float)
    weights = np.linspace(1.0, float(linear_weight_end), momentum_days)
    weight_sum = float(weights.sum())
    x_bar = float((weights * x).sum() / weight_sum)
    denominator = float((weights * (x - x_bar) ** 2).sum())
    for end in range(bias_ma + momentum_days - 1, len(prices)):
        y = bias[end - momentum_days + 1 : end + 1]
        if not np.isfinite(y).all() or y[0] <= 1e-10:
            continue
        y_bar = float((weights * y).sum() / weight_sum)
        slope = float((weights * (x - x_bar) * (y - y_bar)).sum() / denominator)
        result[end] = slope / float(y[0]) * 10000.0
    return pd.Series(result, index=close.index, name="score")


def momentum_signal_target(score: pd.Series, abs20: pd.Series) -> pd.Series:
    """Return the close-confirmed 0/0.5/1 target used by the v1.3 ZZ1000 sleeve."""

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
    """Build close signal and next-session execution weights from price closes."""

    close = validate_close_series(close)

    score = calc_bias_momentum(close)
    abs20 = (close / close.shift(MOMENTUM_POLICY.absolute_momentum_days) - 1.0).rename(
        "abs20"
    )
    signal_target = momentum_signal_target(score, abs20)
    execution_weight = signal_target.shift(1, fill_value=0.0).rename(
        "momentum_execution_weight"
    )
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(close.index),
            "close": close.to_numpy(dtype=float),
            "score": score.to_numpy(dtype=float),
            "abs20": abs20.to_numpy(dtype=float),
            "momentum_signal_target": signal_target.to_numpy(dtype=float),
            "momentum_execution_weight": execution_weight.to_numpy(dtype=float),
        }
    )
    return frame


def _validate_momentum_schedule(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "momentum_signal_target", "momentum_execution_weight"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Missing momentum columns: {missing}")
    result = frame.copy()
    result["date"] = normalize_daily_dates(result["date"], "Momentum schedule dates")
    result = result.sort_values("date").reset_index(drop=True)
    if result.empty:
        raise ValueError("Momentum schedule is empty")
    if result["date"].duplicated().any():
        raise ValueError("Momentum schedule contains duplicate dates")

    allowed = np.asarray(MOMENTUM_POLICY.signal_targets, dtype=float)
    for column in ("momentum_signal_target", "momentum_execution_weight"):
        values = pd.to_numeric(result[column], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{column} contains non-finite values")
        valid = np.isclose(values[:, None], allowed[None, :], atol=1e-12).any(axis=1)
        if not valid.all():
            raise ValueError(f"{column} contains values outside 0/0.5/1")

    expected = (
        result["momentum_signal_target"].shift(1, fill_value=0.0).to_numpy(dtype=float)
    )
    actual = result["momentum_execution_weight"].to_numpy(dtype=float)
    if not np.allclose(actual, expected, atol=1e-12, rtol=0.0):
        raise ValueError("Momentum execution weight is not the prior-session close target")
    return result


def compose_from_parent_schedule(
    parent_schedule: pd.DataFrame,
    momentum_schedule: pd.DataFrame,
    actual_call_state: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Apply v1.2 capital sleeves without changing any parent v1.1 decisions."""

    base = parent_schedule.copy()
    if "date" not in base.columns:
        raise ValueError("Parent schedule is missing date")
    base["date"] = normalize_daily_dates(base["date"], "Parent schedule dates")
    base = base.sort_values("date").reset_index(drop=True)
    if base.empty or base["date"].duplicated().any():
        raise ValueError("Parent schedule dates are empty or duplicated")

    momentum = _validate_momentum_schedule(momentum_schedule)
    merged = base.merge(
        momentum[["date", "momentum_signal_target", "momentum_execution_weight"]],
        on="date",
        how="left",
        validate="one_to_one",
    )
    if merged[["momentum_signal_target", "momentum_execution_weight"]].isna().any().any():
        missing_dates = merged.loc[
            merged["momentum_execution_weight"].isna(), "date"
        ].dt.strftime("%Y-%m-%d")
        raise ValueError(f"Momentum schedule does not cover parent dates: {missing_dates.head(3).tolist()}")

    merged["parent_core_im_units"] = merged["core_im_units"].astype(float)
    merged["parent_grid_im_units"] = merged["grid_im_units"].astype(float)
    merged["parent_total_im_units"] = merged["total_im_units"].astype(float)
    merged["parent_put_signal_target_qty"] = merged["put_signal_target_qty"].astype(int)
    merged["parent_put_execution_target_qty"] = merged[
        "put_execution_target_qty"
    ].astype(int)

    merged["core_im_units"] = CORE_CAPITAL_SHARE
    merged["momentum_im_units"] = (
        MOMENTUM_CAPITAL_SHARE * merged["momentum_execution_weight"].astype(float)
    )
    # v1.1's independent grid remains a whole-portfolio event sleeve.
    merged["grid_im_units"] = merged["parent_grid_im_units"]
    merged["total_im_units"] = (
        merged["core_im_units"]
        + merged["momentum_im_units"]
        + merged["grid_im_units"]
    )

    merged["core_put_signal_qty_normalized"] = (
        CORE_CAPITAL_SHARE * merged["parent_put_signal_target_qty"]
    )
    merged["core_put_execution_qty_normalized"] = (
        CORE_CAPITAL_SHARE * merged["parent_put_execution_target_qty"]
    )
    merged["momentum_put_qty_normalized"] = 0.0
    merged["grid_put_qty"] = 0
    merged["put_covered_im_units"] = CORE_CAPITAL_SHARE

    merged["core_call_covered_im_units"] = CORE_CAPITAL_SHARE
    merged["core_call_coverage_capacity_contracts_normalized"] = (
        CORE_CAPITAL_SHARE * CALL_CONTRACTS_PER_FULL_IM
    )
    merged["core_call_actual_target_contracts_normalized"] = np.nan
    merged["core_call_target_contracts_normalized"] = np.nan
    merged["actual_call_state_available"] = False
    merged["momentum_call_target_contracts_normalized"] = 0.0
    merged["grid_call_qty"] = 0
    merged["call_covered_im_units"] = CORE_CAPITAL_SHARE
    if actual_call_state is not None:
        call = actual_call_state.copy()
        required_call = {
            "date",
            "call_active",
            "call_contract",
            "call_expiry",
            "threat_roll_count",
            "threat_entry_blocked",
        }
        missing_call = sorted(required_call.difference(call.columns))
        if missing_call:
            raise ValueError(f"Missing actual Call columns: {missing_call}")
        call["date"] = normalize_daily_dates(call["date"], "Actual Call state dates")
        call = call.sort_values("date").reset_index(drop=True)
        call["call_active"] = call["call_active"].astype(bool)
        merged = merged.merge(
            call[
                [
                    "date",
                    "call_active",
                    "call_contract",
                    "call_expiry",
                    "threat_roll_count",
                    "threat_entry_blocked",
                ]
            ],
            on="date",
            how="left",
            validate="one_to_one",
        )
        if merged[
            ["call_active", "call_contract", "threat_roll_count", "threat_entry_blocked"]
        ].isna().any().any():
            raise ValueError("Actual Call state does not cover all parent dates")
        actual_target = (
            merged["call_active"].astype(float)
            * merged["core_call_coverage_capacity_contracts_normalized"]
        )
        merged["core_call_actual_target_contracts_normalized"] = actual_target
        merged["core_call_target_contracts_normalized"] = actual_target
        merged["actual_call_state_available"] = True
    merged["margin_buffer_fraction"] = parent.PER_IM_MARGIN_BUFFER * merged[
        "total_im_units"
    ]
    return merged


def build_target_schedule(
    state: pd.DataFrame,
    momentum_schedule: pd.DataFrame,
    *,
    initial_grid_held: bool = False,
) -> pd.DataFrame:
    parent_schedule = parent.build_target_schedule(
        state, initial_grid_held=initial_grid_held
    )
    return compose_from_parent_schedule(parent_schedule, momentum_schedule)


def load_authoritative_local_state() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compose the real local v1.1 state and the audited v1.3 momentum schedule."""

    parent_schedule, parent_audit = parent.load_authoritative_local_state()
    if not MOMENTUM_AUDIT_PATH.is_file():
        raise FileNotFoundError(f"Missing momentum audit artifact: {MOMENTUM_AUDIT_PATH}")
    source = pd.read_csv(MOMENTUM_AUDIT_PATH, compression="gzip")
    required = {"date", "score", "abs20", "desired_weight", "momentum_weight"}
    missing = sorted(required.difference(source.columns))
    if missing:
        raise ValueError(f"Momentum audit artifact is missing columns: {missing}")
    source["date"] = normalize_daily_dates(source["date"], "Momentum audit dates")
    source = source.loc[
        source["date"].between(parent_schedule["date"].min(), parent_schedule["date"].max())
    ].copy()

    expected_signal = momentum_signal_target(source["score"], source["abs20"])
    source_signal_error = float(
        np.max(np.abs(expected_signal.to_numpy(dtype=float) - source["desired_weight"].to_numpy(dtype=float)))
    )
    momentum = pd.DataFrame(
        {
            "date": source["date"],
            "momentum_signal_target": source["desired_weight"].astype(float),
            "momentum_execution_weight": source["momentum_weight"].astype(float),
        }
    )
    if not FIXED_COMPONENTS_PATH.is_file():
        raise FileNotFoundError(f"Missing fixed component artifact: {FIXED_COMPONENTS_PATH}")
    components = pd.read_csv(FIXED_COMPONENTS_PATH, low_memory=False)
    chosen = components[components["variant"].eq("current_4tier_mom3")].copy()
    chosen["date"] = pd.to_datetime(chosen["date"], errors="raise")
    model = chosen[
        chosen["scenario"].eq("model_avg_basis") & chosen["date"].lt(REAL_IM_START)
    ].copy()
    real = chosen[
        chosen["scenario"].eq("real_actual_basis") & chosen["date"].ge(REAL_IM_START)
    ].copy()
    call = pd.concat([model, real], ignore_index=True).sort_values("date").reset_index(drop=True)
    call["date"] = normalize_daily_dates(call["date"], "Fixed component dates")
    if call.empty or call["date"].duplicated().any():
        raise RuntimeError("Actual Call component dates are empty or duplicated")
    call_contract = call["call_contract"].fillna("").astype(str).str.strip()
    call_expiry = normalize_optional_daily_dates(
        call["call_expiry"], "Fixed component Call expiries"
    )
    threat_roll_count_numeric = pd.to_numeric(
        call["threat_roll_count"], errors="coerce"
    )
    if (
        threat_roll_count_numeric.isna().any()
        or not np.isfinite(threat_roll_count_numeric.to_numpy(dtype=float)).all()
        or not threat_roll_count_numeric.eq(threat_roll_count_numeric.round()).all()
        or not threat_roll_count_numeric.between(0, 5).all()
    ):
        raise RuntimeError("Call threat_roll_count must be an integer from 0 through 5")
    threat_roll_count = threat_roll_count_numeric.astype(int)
    blocked_raw = call["threat_entry_blocked"]
    if blocked_raw.isna().any() or not blocked_raw.isin([True, False, 0, 1]).all():
        raise RuntimeError("Call threat_entry_blocked must be a nonmissing boolean")
    threat_entry_blocked = blocked_raw.astype(bool)
    call_margin_active = pd.to_numeric(
        call["call_margin_fraction"], errors="coerce"
    ).fillna(0.0).abs().gt(1e-12)
    if not call_contract.ne("").equals(call_margin_active):
        raise RuntimeError("Actual Call contract and margin activity disagree")
    call_active = call_contract.ne("")
    if not call_expiry.notna().equals(call_active):
        raise RuntimeError("Actual Call expiry and contract activity disagree")
    blocked_inconsistency = threat_entry_blocked & (
        call_active | call_expiry.notna() | threat_roll_count.ne(0)
    )
    if blocked_inconsistency.any():
        raise RuntimeError(
            "Blocked Call entries must be flat with no expiry and zero threat roll count"
        )
    prior_roll_count = threat_roll_count.shift()
    threat_roll = threat_roll_count.gt(prior_roll_count)
    roll_increment_failures = threat_roll & threat_roll_count.sub(prior_roll_count).ne(1)
    expiry_order_failures = threat_roll & (
        call_expiry.isna()
        | call_expiry.shift().isna()
        | call_expiry.le(call_expiry.shift())
    )
    if roll_increment_failures.any():
        raise RuntimeError("Call threat roll count must increment exactly once per rescue")
    if expiry_order_failures.any():
        raise RuntimeError(
            "Call threat rescue expiry must be strictly later than the prior expiry"
        )
    actual_call = pd.DataFrame(
        {
            "date": call["date"],
            "call_active": call_active,
            "call_contract": call_contract,
            "call_expiry": call_expiry,
            "threat_roll_count": threat_roll_count,
            "threat_entry_blocked": threat_entry_blocked,
        }
    )
    schedule = compose_from_parent_schedule(parent_schedule, momentum, actual_call)

    core_formula_error = float(np.max(np.abs(schedule["core_im_units"] - CORE_CAPITAL_SHARE)))
    momentum_formula_error = float(
        np.max(
            np.abs(
                schedule["momentum_im_units"]
                - MOMENTUM_CAPITAL_SHARE * schedule["momentum_execution_weight"]
            )
        )
    )
    total_formula_error = float(
        np.max(
            np.abs(
                schedule["total_im_units"]
                - schedule["core_im_units"]
                - schedule["momentum_im_units"]
                - schedule["grid_im_units"]
            )
        )
    )
    grid_parent_error = float(
        np.max(np.abs(schedule["grid_im_units"] - schedule["parent_grid_im_units"]))
    )
    put_formula_error = float(
        np.max(
            np.abs(
                schedule["core_put_execution_qty_normalized"]
                - CORE_CAPITAL_SHARE * schedule["parent_put_execution_target_qty"]
            )
        )
    )
    actual_call_target = schedule["core_call_actual_target_contracts_normalized"].astype(float)
    expected_call_target = (
        schedule["call_active"].astype(float)
        * schedule["core_call_coverage_capacity_contracts_normalized"].astype(float)
    )
    call_actual_formula_error = float((actual_call_target - expected_call_target).abs().max())

    latest = schedule.iloc[-1]
    audit = {
        "version": VERSION,
        "status": STATUS,
        "start": schedule["date"].min().date().isoformat(),
        "end": schedule["date"].max().date().isoformat(),
        "rows": int(len(schedule)),
        "source_signal_rule_max_abs_error": source_signal_error,
        "core_units_formula_max_abs_error": core_formula_error,
        "momentum_units_formula_max_abs_error": momentum_formula_error,
        "total_units_formula_max_abs_error": total_formula_error,
        "grid_parent_parity_max_abs_error": grid_parent_error,
        "put_core_only_formula_max_abs_error": put_formula_error,
        "call_actual_target_formula_max_abs_error": call_actual_formula_error,
        "call_actual_active_rows": int(schedule["call_active"].sum()),
        "call_actual_flat_rows": int((~schedule["call_active"]).sum()),
        "call_threat_roll_events": int(threat_roll.sum()),
        "call_threat_roll_count_increment_failures": int(
            roll_increment_failures.sum()
        ),
        "call_threat_roll_expiry_order_failures": int(expiry_order_failures.sum()),
        "call_max_threat_roll_count": int(threat_roll_count.max()),
        "call_threat_entry_blocked_rows": int(threat_entry_blocked.sum()),
        "call_threat_entry_blocked_inconsistency_rows": int(
            blocked_inconsistency.sum()
        ),
        "call_coverage_capacity_contracts_normalized": float(
            schedule["core_call_coverage_capacity_contracts_normalized"].iloc[0]
        ),
        "call_rescue_expiry_rule": parent.CALL_POLICY.rescue_expiry_rule,
        "momentum_put_nonzero_rows": int(schedule["momentum_put_qty_normalized"].ne(0).sum()),
        "momentum_call_nonzero_rows": int(
            schedule["momentum_call_target_contracts_normalized"].ne(0).sum()
        ),
        "normalized_four_put_without_parent_tier4_rows": int(
            (
                schedule["core_put_signal_qty_normalized"].eq(2.0)
                & schedule["valuation_tier"].ne(4)
            ).sum()
        ),
        "momentum_execution_weight_counts": {
            str(float(key)): int(value)
            for key, value in schedule["momentum_execution_weight"].value_counts().sort_index().items()
        },
        "latest_state": {
            "date": latest["date"].date().isoformat(),
            "momentum_signal_target": float(latest["momentum_signal_target"]),
            "momentum_execution_weight": float(latest["momentum_execution_weight"]),
            "core_im_units": float(latest["core_im_units"]),
            "momentum_im_units": float(latest["momentum_im_units"]),
            "grid_im_units": float(latest["grid_im_units"]),
            "total_im_units": float(latest["total_im_units"]),
            "core_put_execution_qty_normalized": float(
                latest["core_put_execution_qty_normalized"]
            ),
            "core_call_target_contracts_normalized": float(
                latest["core_call_target_contracts_normalized"]
            ),
            "core_call_coverage_capacity_contracts_normalized": float(
                latest["core_call_coverage_capacity_contracts_normalized"]
            ),
            "call_active": bool(latest["call_active"]),
            "call_contract": str(latest["call_contract"]),
            "call_expiry": (
                latest["call_expiry"].date().isoformat()
                if pd.notna(latest["call_expiry"])
                else None
            ),
            "threat_roll_count": int(latest["threat_roll_count"]),
            "threat_entry_blocked": bool(latest["threat_entry_blocked"]),
        },
        "parent_v1_1_audit": parent_audit,
    }
    return schedule, audit


def rule_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": STATUS,
        "research_start": RESEARCH_START.date().isoformat(),
        "parent_version": parent.VERSION,
        "capital_sleeves": {
            "core_share": CORE_CAPITAL_SHARE,
            "momentum_share": MOMENTUM_CAPITAL_SHARE,
            "core_always_held": True,
        },
        "momentum": asdict(MOMENTUM_POLICY),
        "options": {
            "put_policy": "inherit_im_v1_1_core_only",
            "call_policy": "inherit_im_v1_1_core_only",
            "momentum_put": False,
            "momentum_call": False,
            "grid_put": False,
            "grid_call": False,
        },
        "grid": parent.rule_manifest()["im"]["grid"],
        "parent_rules": parent.rule_manifest()["im"],
        "provenance": {
            "parent": "im_mainline_v1_1.py",
            "momentum_implementation": str(A_SHARE_V13_BOT),
            "momentum_audit": str(MOMENTUM_AUDIT_PATH),
            "fixed_call_components": str(FIXED_COMPONENTS_PATH),
            "research_record": "2026-08-23_滚IC滚IM叠加动量完整研究记录.md",
            "spec": str(SPEC_PATH),
            "source_sha256": {
                "parent": _sha256(ROOT / "im_mainline_v1_1.py"),
                "a_share_v1_3": _sha256(A_SHARE_V13_BOT),
                "fixed_call_components": _sha256(FIXED_COMPONENTS_PATH),
                "spec": _sha256(SPEC_PATH),
            },
        },
        "performance_claim": "none_new_v1_2_schedule_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-local",
        action="store_true",
        help="compose the audited local v1.1 and v1.3 target schedules",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        help="optional v1.2 target-schedule output; requires --audit-local",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="optional manifest/audit JSON output; requires --audit-local",
    )
    args = parser.parse_args()
    if (args.output_csv or args.output_json) and not args.audit_local:
        parser.error("--output-csv/--output-json require --audit-local")
    ensure_new_output_paths(args.output_csv, args.output_json)

    payload: dict[str, Any] = {"rules": rule_manifest()}
    if args.audit_local:
        schedule, audit = load_authoritative_local_state()
        payload["local_audit"] = audit
        if args.output_csv:
            _atomic_write_csv(schedule, args.output_csv)
            payload["schedule_output"] = str(args.output_csv.resolve())
        if args.output_json:
            payload["audit_output"] = str(args.output_json.resolve())
            _atomic_write_text(
                args.output_json,
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
