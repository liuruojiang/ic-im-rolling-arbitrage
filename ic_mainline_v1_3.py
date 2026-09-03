#!/usr/bin/env python
"""IC v1.3 research candidate aligned to the current A-share CSI500 sleeve."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_mainline_v1_2 as previous
import im_mainline_v1_3 as shared


ROOT = Path(__file__).resolve().parent
VERSION = "ic_mainline_v1_3"
STATUS = "research_candidate_not_live_authority"
RESEARCH_START = pd.Timestamp("2015-04-16")
FORMAL_START = pd.Timestamp("2007-01-15")
CORE_CAPITAL_SHARE = 0.50
MOMENTUM_CAPITAL_SHARE = 0.50
PER_IC_MARGIN_BUFFER = 0.30
A_SHARE_CASH_YIELD = 0.02
A_SHARE_COST_RATE = 0.001

A_SHARE_V13_BOT = previous.A_SHARE_V13_BOT
CSI500_OHLCV_PATH = (
    ROOT / "data" / "ic_510500_put_proxy_validation_v1" / "sina_000905_index.csv"
)
SPEC_PATH = ROOT / "docs" / "ic_mainline_v1_3_spec.md"


@dataclass(frozen=True)
class MomentumPolicy:
    source_version: str = "cn_six_index_simplified_momentum_combo_v1_3"
    source_sleeve: str = "zz500"
    bias_ma: int = 110
    momentum_days: int = 24
    linear_weight_end: float = 2.0
    score_threshold: float = 0.0
    absolute_momentum_days: int = 20
    absolute_momentum_threshold: float = 0.0
    absolute_filter_share: float = 0.5
    nav_decay_threshold: float = 0.06
    nav_decay_scale: float = 0.5
    annual_cash_yield: float = A_SHARE_CASH_YIELD
    annualization_days: int = 244
    cost_rate: float = A_SHARE_COST_RATE
    volume_filter: str = "off"
    hot_score_filter: str = "off"
    execution_weights: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0)
    execution: str = "T_close_signal_and_base_nav_drawdown_to_next_common_session_position"


MOMENTUM_POLICY = MomentumPolicy()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_a_share_module():
    spec = importlib.util.spec_from_file_location(
        "a_share_v13_ic_authority", A_SHARE_V13_BOT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load A-share authority: {A_SHARE_V13_BOT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def calc_bias_momentum(close: pd.Series) -> pd.Series:
    return shared.calc_bias_momentum(
        close,
        bias_ma=MOMENTUM_POLICY.bias_ma,
        momentum_days=MOMENTUM_POLICY.momentum_days,
        linear_weight_end=MOMENTUM_POLICY.linear_weight_end,
    ).rename("score")


def build_momentum_schedule(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """Reproduce the A-share CSI500 base-NAV defense with exact T+1 timing."""

    source = shared.validate_ohlcv(ohlcv, "CSI500 OHLCV")
    formal = source.loc[source.index >= FORMAL_START].copy()
    if formal.empty or formal.index[0] != FORMAL_START:
        raise ValueError(
            "CSI500 OHLCV must cover the complete formal period from 2007-01-15; "
            "truncated history would reset the NAV-defense high-water mark"
        )
    indicator_close = source["close"].astype(float)
    close = formal["close"].astype(float)
    score = calc_bias_momentum(indicator_close).reindex(close.index)
    abs20 = (
        indicator_close / indicator_close.shift(MOMENTUM_POLICY.absolute_momentum_days)
        - 1.0
    ).reindex(close.index).rename("abs20")
    if not np.isfinite(float(score.iloc[0])) or not np.isfinite(float(abs20.iloc[0])):
        raise ValueError("CSI500 OHLCV lacks the indicator warm-up required at formal start")
    base_signal = (
        score.gt(MOMENTUM_POLICY.score_threshold).astype(float)
        * (
            1.0
            - MOMENTUM_POLICY.absolute_filter_share
            + MOMENTUM_POLICY.absolute_filter_share
            * abs20.gt(MOMENTUM_POLICY.absolute_momentum_threshold).astype(float)
        )
    ).rename("base_momentum_signal_target")
    base_execution = base_signal.shift(1, fill_value=0.0).rename(
        "base_momentum_execution_weight"
    )
    raw_ret = close.pct_change().fillna(0.0)
    turnover = base_execution.diff().abs().fillna(base_execution.abs())
    daily_cash = (1.0 + MOMENTUM_POLICY.annual_cash_yield) ** (
        1.0 / MOMENTUM_POLICY.annualization_days
    ) - 1.0
    cash_ret = (1.0 - base_execution).clip(lower=0.0, upper=1.0) * daily_cash
    cash_ret.iloc[0] = 0.0
    base_ret = (
        base_execution * raw_ret
        + cash_ret
        - MOMENTUM_POLICY.cost_rate * turnover
    ).rename("base_strategy_ret_for_nav_gate")
    base_nav = (1.0 + base_ret).cumprod().rename("base_nav_for_dd")
    base_dd = (base_nav / base_nav.cummax() - 1.0).rename("base_dd_for_gate")
    nav_decay_signal = base_dd.le(-MOMENTUM_POLICY.nav_decay_threshold).rename(
        "nav_decay_signal"
    )
    signal_scale = pd.Series(
        np.where(nav_decay_signal, MOMENTUM_POLICY.nav_decay_scale, 1.0),
        index=close.index,
        name="nav_decay_signal_scale",
    )
    signal_target = (base_signal * signal_scale).rename("momentum_signal_target")
    execution_weight = signal_target.shift(1, fill_value=0.0).rename(
        "momentum_execution_weight"
    )
    return pd.DataFrame(
        {
            "date": close.index,
            "close": close,
            "score": score,
            "abs20": abs20,
            "base_momentum_signal_target": base_signal,
            "base_momentum_execution_weight": base_execution,
            "base_strategy_ret_for_nav_gate": base_ret,
            "base_nav_for_dd": base_nav,
            "base_dd_for_gate": base_dd,
            "nav_decay_signal": nav_decay_signal,
            "nav_decay_signal_scale": signal_scale,
            "momentum_signal_target": signal_target,
            "momentum_execution_weight": execution_weight,
        }
    ).reset_index(drop=True)


def compose_from_v12(
    parent_schedule: pd.DataFrame, momentum_schedule: pd.DataFrame
) -> pd.DataFrame:
    parent = parent_schedule.copy()
    parent["date"] = shared.normalize_daily_dates(parent["date"], "IC v1.2 dates")
    parent = parent.sort_values("date").reset_index(drop=True)
    momentum = momentum_schedule.copy()
    momentum["date"] = shared.normalize_daily_dates(
        momentum["date"], "IC v1.3 momentum dates"
    )
    momentum = momentum.sort_values("date").reset_index(drop=True)
    momentum = momentum[momentum["date"].isin(parent["date"])].copy()
    if len(momentum) != len(parent):
        raise ValueError("IC v1.3 momentum does not cover the parent schedule")

    archival_renames = {
        "momentum_weight": "parent_v1_2_put_momentum_weight",
        "momentum_valuation_target_delta": (
            "parent_v1_2_momentum_valuation_target_delta"
        ),
        "executed_put_contract": "parent_v1_2_executed_put_contract",
        "executed_put_qty": "parent_v1_2_executed_put_qty",
        "executed_put_target_delta": "parent_v1_2_executed_put_target_delta",
        "executed_put_layer": "parent_v1_2_executed_put_layer",
    }
    parent = parent.rename(columns=archival_renames)
    parent = parent.drop(
        columns=[
            "score",
            "abs20",
            "momentum_signal_target",
            "momentum_execution_weight",
        ],
        errors="ignore",
    )
    keep = [column for column in momentum.columns if column != "close"]
    frame = parent.merge(momentum[keep], on="date", validate="one_to_one")

    allowed = np.asarray(MOMENTUM_POLICY.execution_weights)
    values = frame["momentum_execution_weight"].to_numpy(dtype=float)
    if not np.isclose(values[:, None], allowed[None, :], atol=1e-12).any(axis=1).all():
        raise ValueError("IC v1.3 momentum weight is outside 0/0.25/0.5/1")
    if len(frame) > 1 and not np.allclose(
        frame["momentum_execution_weight"].iloc[1:],
        frame["momentum_signal_target"].shift(1).iloc[1:],
        atol=1e-12,
        rtol=0.0,
    ):
        raise ValueError("IC v1.3 momentum execution is not prior-session target")

    frame["core_ic_units"] = CORE_CAPITAL_SHARE
    frame["momentum_ic_units"] = (
        MOMENTUM_CAPITAL_SHARE * frame["momentum_execution_weight"]
    )
    frame["total_ic_units"] = (
        frame["core_ic_units"] + frame["momentum_ic_units"] + frame["grid_ic_units"]
    )
    frame["momentum_valuation_target_delta"] = (
        MOMENTUM_CAPITAL_SHARE
        * frame["momentum_execution_weight"]
        * frame["valuation_only_target_delta"]
    )
    frame["momentum_put_target_delta"] = frame[
        "momentum_valuation_target_delta"
    ]
    frame["total_put_target_delta"] = (
        frame["core_put_target_delta"] + frame["momentum_put_target_delta"]
    )
    frame["target_delta"] = frame["total_put_target_delta"]
    frame["call_target_contracts"] = 0.0
    frame["has_call"] = False
    frame["margin_buffer_fraction"] = PER_IC_MARGIN_BUFFER * frame["total_ic_units"]
    return frame


def load_authoritative_local_state() -> tuple[pd.DataFrame, dict[str, Any]]:
    for path in (A_SHARE_V13_BOT, CSI500_OHLCV_PATH, SPEC_PATH):
        if not path.is_file():
            raise FileNotFoundError(path)
    parent, parent_audit = previous.load_authoritative_local_state()
    ohlcv = pd.read_csv(CSI500_OHLCV_PATH, parse_dates=["date"])
    momentum = build_momentum_schedule(ohlcv)
    schedule = compose_from_v12(parent, momentum)

    source = _load_a_share_module()
    cfg = next(item for item in source.SLEEVES if item.key == "zz500")
    source_curve = source.build_sleeve_curve(
        shared.validate_ohlcv(ohlcv, "CSI500 source parity OHLCV"), cfg
    )
    momentum_indexed = momentum.set_index("date")
    source_curve = source_curve.reindex(momentum_indexed.index)
    actual_weights = momentum_indexed["momentum_execution_weight"].to_numpy(dtype=float)
    expected_weights = source_curve["final_weight"].to_numpy(dtype=float)
    actual_nav = momentum_indexed["base_nav_for_dd"].to_numpy(dtype=float)
    expected_nav = source_curve["base_nav_for_dd"].to_numpy(dtype=float)
    actual_dd = momentum_indexed["base_dd_for_gate"].to_numpy(dtype=float)
    expected_dd = source_curve["base_dd_for_gate"].to_numpy(dtype=float)
    parity_arrays = {
        "execution weights": (actual_weights, expected_weights),
        "base NAV": (actual_nav, expected_nav),
        "base drawdown": (actual_dd, expected_dd),
    }
    if any(
        not np.isfinite(actual).all() or not np.isfinite(expected).all()
        for actual, expected in parity_arrays.values()
    ):
        raise RuntimeError("A-share IC authority parity contains non-finite values")
    parity_error = float(np.max(np.abs(actual_weights - expected_weights)))
    nav_error = float(np.max(np.abs(actual_nav - expected_nav)))
    dd_error = float(np.max(np.abs(actual_dd - expected_dd)))
    old_weight = parent["momentum_execution_weight"].to_numpy(dtype=float)
    new_weight = schedule["momentum_execution_weight"].to_numpy(dtype=float)
    inherited_columns = [
        "core_ic_units",
        "grid_ic_units",
        "core_put_target_delta",
        "grid_put_target_delta",
        "call_target_contracts",
    ]
    inherited_errors = {
        column: float(
            np.max(
                np.abs(
                    schedule[column].to_numpy(dtype=float)
                    - parent[column].to_numpy(dtype=float)
                )
            )
        )
        for column in inherited_columns
    }
    latest = schedule.iloc[-1]
    audit = {
        "version": VERSION,
        "status": STATUS,
        "start": schedule["date"].min().date().isoformat(),
        "end": schedule["date"].max().date().isoformat(),
        "rows": int(len(schedule)),
        "a_share_execution_weight_max_abs_error": parity_error,
        "a_share_base_nav_max_abs_error": nav_error,
        "a_share_base_dd_max_abs_error": dd_error,
        "v1_2_execution_weight_changed_rows": int(
            (np.abs(new_weight - old_weight) > 1e-12).sum()
        ),
        "v1_2_execution_weight_max_abs_change": float(
            np.max(np.abs(new_weight - old_weight))
        ),
        "nav_defense_active_rows": int(
            (
                schedule["nav_decay_signal"].astype(bool)
                & schedule["base_momentum_signal_target"].gt(0.0)
            ).sum()
        ),
        "execution_weight_counts": {
            str(float(key)): int(value)
            for key, value in schedule["momentum_execution_weight"]
            .value_counts()
            .sort_index()
            .items()
        },
        "inherited_component_max_abs_errors": inherited_errors,
        "call_nonzero_rows": int(schedule["call_target_contracts"].ne(0.0).sum()),
        "grid_put_nonzero_rows": int(schedule["grid_put_target_delta"].ne(0.0).sum()),
        "latest_state": {
            "date": latest["date"].date().isoformat(),
            "base_dd_for_gate": float(latest["base_dd_for_gate"]),
            "nav_decay_signal": bool(latest["nav_decay_signal"]),
            "momentum_signal_target": float(latest["momentum_signal_target"]),
            "momentum_execution_weight": float(latest["momentum_execution_weight"]),
            "momentum_ic_units": float(latest["momentum_ic_units"]),
            "total_ic_units": float(latest["total_ic_units"]),
            "total_put_target_delta": float(latest["total_put_target_delta"]),
        },
        "parent_v1_2_audit": parent_audit,
    }
    if max(parity_error, nav_error, dd_error) > 1e-12 or max(inherited_errors.values()) > 1e-12:
        raise RuntimeError(f"IC v1.3 parity failed: {audit}")
    return schedule, audit


def rule_manifest() -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": STATUS,
        "parent_version": "ic_mainline_v1_2",
        "capital_sleeves": {
            "core_share": CORE_CAPITAL_SHARE,
            "momentum_share": MOMENTUM_CAPITAL_SHARE,
        },
        "momentum": asdict(MOMENTUM_POLICY),
        "put": asdict(previous.PUT_POLICY),
        "grid": previous.rule_manifest()["grid"],
        "call": {"included": False},
        "provenance": {
            "parent": "ic_mainline_v1_2.py",
            "a_share_v1_3": str(A_SHARE_V13_BOT),
            "ohlcv": str(CSI500_OHLCV_PATH),
            "spec": str(SPEC_PATH),
            "source_sha256": {
                "parent": _sha256(ROOT / "ic_mainline_v1_2.py"),
                "a_share_v1_3": _sha256(A_SHARE_V13_BOT),
                "ohlcv": _sha256(CSI500_OHLCV_PATH),
                "spec": _sha256(SPEC_PATH),
            },
        },
        "orders": "not_generated",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / VERSION / "target_schedule.csv.gz",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=ROOT / "outputs" / VERSION / "audit.json",
    )
    args = parser.parse_args()
    shared.ensure_new_output_paths(args.output, args.audit)
    schedule, audit = load_authoritative_local_state()
    shared._atomic_write_csv(schedule, args.output)
    payload = {
        "rules": rule_manifest(),
        "local_audit": audit,
        "schedule_output": str(args.output.resolve()),
        "audit_output": str(args.audit.resolve()),
    }
    shared._atomic_write_text(
        args.audit, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
