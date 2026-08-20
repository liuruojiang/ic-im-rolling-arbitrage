from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_absolute_valuation_stress_v5 as v5
import ic_510500_put_proxy_validation_v1 as proxy
import ic_510500_put_rolling_continuous_valuation_v3 as core
import ic_510500_put_v4_monthly_tenor_rerun_v6 as v6


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_persistent_stress_hold3m_v7"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "775a276b1715fb987c4b14479ea83040b726838f432fb63420add345affcc2bd"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_persistent_stress_hold3m_v7"

V5_PATH = Path(v5.__file__).resolve()
V5_SHA256 = "842affa33e9f7bc08d7ba85c5031fb8c26a160a2c7bbbb11f29c288d6d0e7d33"
V6_PATH = Path(v6.__file__).resolve()
V6_SHA256 = "16c488da8b0c1255758036a57549476521cce2e8004dbc5660ad298161a8fee2"
V3_PATH = Path(core.__file__).resolve()
V3_SHA256 = "7b05088caa2ae40358ade307be6fac9728e34c276f04fba97fb110964fc4ebd9"
PROXY_PATH = Path(proxy.__file__).resolve()
PROXY_SHA256 = "5836849ca4c0e42ab4a04e2c82d81f049b5f2fb2799333c67177209b8fc2a7a3"

EXECUTION_MODES = ["front_original", "3m_monthly", "3m_hold_expiry"]
SIGNAL_MODES = ["v5_original", "stress_latch", "always_50", "always_100"]
ECON_SIGNALS = ["v5_original", "stress_latch"]
GRID_VARIANTS = [
    f"{execution}_{signal}"
    for execution in EXECUTION_MODES
    for signal in SIGNAL_MODES
]
ALL_GRID_VARIANTS = ["no_put", *GRID_VARIANTS]
EXTRA_WINDOWS = list(v5.EXTRA_WINDOWS)
REQUIRED_SEGMENTS = list(core.REQUIRED_WINDOWS)

PAYOUT_WINDOWS = {
    "known_drawdown": (pd.Timestamp("2021-09-13"), pd.Timestamp("2024-02-05")),
    "early_drawdown": (pd.Timestamp("2021-09-13"), pd.Timestamp("2021-12-31")),
    "payout_2022": (pd.Timestamp("2022-03-15"), pd.Timestamp("2022-04-27")),
    "payout_2024": (pd.Timestamp("2024-01-22"), pd.Timestamp("2024-02-05")),
    "event_2015_partial": (core.MODEL_START, pd.Timestamp("2015-12-31")),
    "recent_2025_2026": (v5.RECENT_START, core.END),
}


def sha256(path: Path) -> str:
    return core.sha256(path)


def split_grid_variant(grid_variant: str) -> tuple[str, str]:
    for execution in EXECUTION_MODES:
        prefix = f"{execution}_"
        if grid_variant.startswith(prefix):
            return execution, grid_variant[len(prefix) :]
    raise ValueError(f"Unknown v7 grid variant: {grid_variant}")


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {
            "execution_mode": "none",
            "signal_variant": "no_put",
            "signal_mode": "baseline",
        }
    execution, signal = split_grid_variant(grid_variant)
    signal_mode = {
        "v5_original": "absolute_stress_original",
        "stress_latch": "absolute_stress_latched",
        "always_50": "engine_control",
        "always_100": "engine_control",
    }[signal]
    return {
        "execution_mode": execution,
        "signal_variant": signal,
        "signal_mode": signal_mode,
    }


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, grid_variant = candidate.split("_", 1)
    return {"layer": layer, **variant_parameters(grid_variant)}


def configure_metrics() -> None:
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.VARIANTS = GRID_VARIANTS
    core.ALL_VARIANTS = ALL_GRID_VARIANTS
    core.ECON_VARIANTS = [
        f"{execution}_{signal}"
        for execution in EXECUTION_MODES
        for signal in ECON_SIGNALS
    ]
    core.EXTRA_WINDOWS = EXTRA_WINDOWS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.segment_slice = v5.segment_slice
    core.v2.candidate_parts = candidate_parts


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v7 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v7 specification sidecar mismatch")
    for path, expected in [
        (V5_PATH, V5_SHA256),
        (V6_PATH, V6_SHA256),
        (V3_PATH, V3_SHA256),
        (PROXY_PATH, PROXY_SHA256),
    ]:
        if sha256(path) != expected:
            raise RuntimeError(f"Frozen dependency changed: {path.name}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads((v5.OUTPUT / "data_manifest.json").read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V5_SHA256 or manifest["spec_sha256"] != v5.SPEC_SHA256:
        raise RuntimeError("v5 formal manifest dependency mismatch")
    for relative, expected in manifest["source_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"v5 frozen input changed: {relative}")
    return manifest


def _latch_frame(daily_valuation: pd.DataFrame) -> pd.DataFrame:
    frame = v5.prepare_signal_frame(daily_valuation).copy()
    valid = frame["tri_sma120"].notna() & frame["tri_rv20"].notna()
    armed = False
    rows: list[dict[str, object]] = []
    for index, row in frame.iterrows():
        state = v5.absolute_state(row)
        valuation_state = str(state["valuation_state"])
        stress = bool(row["stress"])
        carried = False
        if not bool(valid.loc[index]):
            target = 0.0
        elif valuation_state != "low":
            armed = True
            target = 1.0 if stress else 0.5
        elif armed and stress:
            target = 1.0
            carried = True
        else:
            target = 0.0
            if not stress:
                armed = False
        rows.append(
            {
                "date": pd.Timestamp(row["date"]),
                "latch_armed": armed,
                "latch_carried_low_stress": carried,
                "latch_target_fraction": target,
            }
        )
    return frame.merge(pd.DataFrame(rows), on="date", validate="one_to_one")


def build_signal_panel(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    v5_schedule, v5_signals, _, _, v5_current = v5.build_signal_panel(
        ic, daily_valuation, pd.DataFrame()
    )
    original = v5_signals[v5_signals["signal_variant"].eq("abs_stress_any")].copy()
    original["signal_variant"] = "v5_original"
    original["latch_armed"] = np.nan
    original["latch_carried_low_stress"] = False

    latch_full = _latch_frame(daily_valuation).set_index("date")
    latch = v5_signals[v5_signals["signal_variant"].eq("abs_stress_any")].copy()
    latch["signal_variant"] = "stress_latch"
    lookup = latch_full.loc[pd.DatetimeIndex(latch["eval_date"])]
    latch["target_fraction"] = lookup["latch_target_fraction"].to_numpy()
    latch["raw_target_fraction"] = latch["target_fraction"]
    latch["latch_armed"] = lookup["latch_armed"].to_numpy()
    latch["latch_carried_low_stress"] = lookup["latch_carried_low_stress"].to_numpy()

    controls = v5_signals[
        v5_signals["signal_variant"].isin(["always_50", "always_100"])
    ].copy()
    controls["latch_armed"] = np.nan
    controls["latch_carried_low_stress"] = False
    signals = pd.concat([original, latch, controls], ignore_index=True, sort=False).sort_values(
        ["signal_variant", "eval_date"]
    ).reset_index(drop=True)

    schedule_parts: list[pd.DataFrame] = []
    for source, target in [
        ("abs_stress_any", "v5_original"),
        ("always_50", "always_50"),
        ("always_100", "always_100"),
    ]:
        part = v5_schedule[v5_schedule["signal_variant"].eq(source)].copy()
        part["signal_variant"] = target
        schedule_parts.append(part)
    latch_schedule = v5_schedule[
        v5_schedule["signal_variant"].eq("abs_stress_any")
    ].copy()
    latch_schedule["signal_variant"] = "stress_latch"
    target_map = latch.set_index("eval_date")["target_fraction"]
    latch_schedule["binary_target_fraction"] = latch_schedule["eval_date"].map(target_map)
    latch_schedule["three_tier_target_fraction"] = latch_schedule["binary_target_fraction"]
    schedule_parts.append(latch_schedule)
    schedule = pd.concat(schedule_parts, ignore_index=True).sort_values(
        ["layer", "signal_variant", "execution_date"]
    ).reset_index(drop=True)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate v7 execution schedule")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v7 signal execution leakage")

    current_rows: list[dict[str, object]] = []
    v5_row = v5_current[v5_current["signal_variant"].eq("abs_stress_any")].iloc[0].to_dict()
    v5_row["signal_variant"] = "v5_original"
    current_rows.append(v5_row)
    last_latch = latch_full.loc[core.END]
    latch_row = v5_current[v5_current["signal_variant"].eq("abs_stress_any")].iloc[0].to_dict()
    latch_row.update(
        {
            "signal_variant": "stress_latch",
            "research_target_fraction": float(last_latch["latch_target_fraction"]),
            "latch_armed": bool(last_latch["latch_armed"]),
            "latch_carried_low_stress": bool(last_latch["latch_carried_low_stress"]),
        }
    )
    current_rows.append(latch_row)
    current = pd.DataFrame(current_rows)

    original_target = original.set_index("eval_date")["target_fraction"]
    latch_target = latch.set_index("eval_date")["target_fraction"]
    common = original.set_index("eval_date")
    low_stress = common["valuation_state"].eq("low") & common["stress"].astype(bool)
    carried = latch.set_index("eval_date")["latch_carried_low_stress"].astype(bool)
    signal_audit = pd.DataFrame(
        {
            "eval_date": common.index,
            "valuation_state": common["valuation_state"],
            "stress": common["stress"],
            "v5_original_target": original_target,
            "stress_latch_target": latch_target,
            "latch_carried_low_stress": carried,
        }
    ).reset_index(drop=True)
    stats = {
        "low_stress_days": int(low_stress.sum()),
        "carried_low_stress_days": int(carried.sum()),
        "all_carried_days_full_target": bool(latch_target.loc[carried].eq(1.0).all()),
        "all_low_no_stress_days_zero": bool(
            latch.loc[
                latch["valuation_state"].eq("low") & ~latch["stress"].astype(bool),
                "target_fraction",
            ].eq(0.0).all()
        ),
    }
    return schedule, signals, current, signal_audit, stats


def _model_open_hold(
    row: object,
    fraction: float,
    trade_dates: pd.DatetimeIndex,
) -> tuple[proxy.ModelPosition, float, float]:
    day = pd.Timestamp(row.date)
    month = v6.desired_model_month(day, "3m_monthly", trade_dates)
    expiry = proxy.fourth_wednesday(month, trade_dates)
    strike = float(row.spot_open) * 0.85
    units = float(row.settle) * 200.0 / float(row.spot_open) * fraction
    position = proxy.ModelPosition(month, expiry, strike, units, fraction, 0.0)
    open_price = proxy.option_price(position, row, "open")
    close_price = proxy.option_price(position, row, "close")
    position.prior_mark = close_price
    return position, open_price, close_price


def _rolls_covered(
    entry: pd.Timestamp,
    expiry: pd.Timestamp,
    roll_dates: set[pd.Timestamp],
) -> int:
    return sum(entry <= day <= expiry for day in roll_dates)


def run_model_hold_expiry(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    market: pd.DataFrame,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = ic[ic["date"] >= core.MODEL_START].copy().reset_index(drop=True)
    daily["prior_settle"] = daily["settle"].shift(1)
    daily.loc[0, "prior_settle"] = daily.loc[0, "settle"]
    merged = daily.merge(market.drop(columns=["settle"]), on="date", validate="one_to_one")
    events = proxy.schedule_events(schedule, "model", "daily")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: proxy.ModelPosition | None = None
    meta: dict[str, object] | None = None
    latest_signal = 0.0
    latest_eval: pd.Timestamp | None = None
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    lifecycles: list[dict[str, object]] = []
    renewal_due = False

    for row in merged.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        if event is not None:
            latest_signal = float(event.three_tier_target_fraction)
            latest_eval = pd.Timestamp(event.eval_date)
        denominator = float(row.prior_settle) * 200.0
        pnl = 0.0
        cost = 0.0
        action = ""
        old_fraction = active.fraction if active is not None else 0.0
        scheduled = day

        if active is None and latest_signal > 0:
            active, open_price, close_price = _model_open_hold(
                row, latest_signal, trade_dates
            )
            pnl += active.units * (close_price - open_price) / denominator
            cost += latest_signal * proxy.PUT_FULL_SIDE_COST
            meta = {
                "entry_date": day,
                "initial_fraction": latest_signal,
                "max_fraction": latest_signal,
                "upsize_count": 0,
                "renewal": renewal_due,
            }
            action = "open_renewal" if renewal_due else "open_buy"
            renewal_due = False
        elif active is not None and latest_signal > active.fraction + 1e-12:
            open_price = proxy.option_price(active, row, "open")
            pnl += active.units * (open_price - active.prior_mark) / denominator
            new_units = max(
                active.units,
                float(row.settle) * 200.0 / float(row.spot_open) * latest_signal,
            )
            close_price = proxy.option_price(active, row, "close")
            pnl += new_units * (close_price - open_price) / denominator
            cost += (latest_signal - active.fraction) * proxy.PUT_FULL_SIDE_COST
            active.units = new_units
            active.fraction = latest_signal
            active.prior_mark = close_price
            if meta is None:
                raise RuntimeError("Missing model hold lifecycle metadata")
            meta["max_fraction"] = latest_signal
            meta["upsize_count"] = int(meta["upsize_count"]) + 1
            action = "open_upsize"
        elif active is not None:
            close_price = proxy.option_price(active, row, "close")
            pnl += active.units * (close_price - active.prior_mark) / denominator
            active.prior_mark = close_price

        if action:
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": scheduled,
                    "actual_execution_date": day,
                    "action": action,
                    "signal_target_fraction": latest_signal,
                    "old_executed_fraction": old_fraction,
                    "new_executed_fraction": active.fraction if active else 0.0,
                    "old_month": active.contract_month if action == "open_upsize" else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": 0.85 if action != "open_upsize" else np.nan,
                    "delay_days": 0,
                    "delay_trading_days": 0,
                    "renewal": action == "open_renewal",
                    "early_exit": False,
                    "downsize": False,
                }
            )

        executed_for_day = active.fraction if active is not None else 0.0
        expired = False
        if active is not None and active.expiry == day:
            expired = True
            if meta is None:
                raise RuntimeError("Missing model expiry lifecycle metadata")
            lifecycles.append(
                {
                    "candidate": label,
                    "entry_date": meta["entry_date"],
                    "contract_month": active.contract_month,
                    "expiry": active.expiry,
                    "completed": True,
                    "open_at_sample_end": False,
                    "initial_fraction": meta["initial_fraction"],
                    "max_fraction": meta["max_fraction"],
                    "upsize_count": meta["upsize_count"],
                    "renewal_entry": meta["renewal"],
                    "calendar_days": int((active.expiry - pd.Timestamp(meta["entry_date"])).days),
                    "ic_rolls_covered": _rolls_covered(
                        pd.Timestamp(meta["entry_date"]), active.expiry, roll_dates
                    ),
                    "early_exit": False,
                }
            )
            active = None
            meta = None
            renewal_due = latest_signal > 0

        mark_fraction = 0.0
        contract = ""
        units = 0.0
        if active is not None:
            mark_fraction = active.units * active.prior_mark / (float(row.settle) * 200.0)
            contract = f"MODEL_{active.contract_month.strftime('%y%m')}_{active.strike:.4f}"
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
                "signal_target_fraction": latest_signal,
                "target_fraction": executed_for_day,
                "entry_moneyness_mark": 0.85 if active is not None else np.nan,
                "carried_mark": False,
                "mark_stale_days": 0,
                "deferred_adjustment": False,
                "expired": expired,
            }
        )

    if active is not None and meta is not None:
        lifecycles.append(
            {
                "candidate": label,
                "entry_date": meta["entry_date"],
                "contract_month": active.contract_month,
                "expiry": active.expiry,
                "completed": False,
                "open_at_sample_end": True,
                "initial_fraction": meta["initial_fraction"],
                "max_fraction": meta["max_fraction"],
                "upsize_count": meta["upsize_count"],
                "renewal_entry": meta["renewal"],
                "calendar_days": int((core.END - pd.Timestamp(meta["entry_date"])).days),
                "ic_rolls_covered": _rolls_covered(
                    pd.Timestamp(meta["entry_date"]), core.END, roll_dates
                ),
                "early_exit": False,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lifecycles)


def _real_target_qty(row: object, etf_open: float, fraction: float) -> tuple[int, int]:
    full_qty = max(1, int(round(float(row.settle) * 200.0 / (etf_open * 10000.0))))
    return max(1, int(round(full_qty * fraction))), full_qty


def _next_trade_date(trade_dates: pd.DatetimeIndex, day: pd.Timestamp) -> pd.Timestamp | None:
    later = trade_dates[trade_dates > day]
    return pd.Timestamp(later[0]) if len(later) else None


def _trading_delay(
    trade_dates: pd.DatetimeIndex,
    request: pd.Timestamp,
    actual: pd.Timestamp,
) -> int:
    return int(((trade_dates > request) & (trade_dates <= actual)).sum())


def run_real_hold_expiry(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    snapshots: pd.DataFrame,
    histories: pd.DataFrame,
    etf500: pd.DataFrame,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = ic[ic["date"] >= core.REAL_START].copy().reset_index(drop=True)
    offset = len(ic) - len(daily)
    daily["prior_settle"] = ic["settle"].shift(1).loc[daily.index + offset].to_numpy()
    daily.loc[0, "prior_settle"] = float(
        ic.loc[ic["date"] < core.REAL_START, "settle"].iloc[-1]
    )
    etf = etf500.set_index("date")
    history_lookup = histories.set_index(["security_id", "date"])
    history_groups = {
        key: group.sort_values("date") for key, group in histories.groupby("security_id")
    }
    events = proxy.schedule_events(schedule, "real", "daily")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: proxy.RealPosition | None = None
    meta: dict[str, object] | None = None
    latest_signal = 0.0
    latest_eval: pd.Timestamp | None = None
    pending_since: pd.Timestamp | None = None
    renewal_due = False
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    lifecycles: list[dict[str, object]] = []

    for row in daily.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        if event is not None:
            new_signal = float(event.three_tier_target_fraction)
            if active is None and new_signal > 0:
                pending_since = pending_since or day
            elif active is not None and new_signal > active.fraction + 1e-12:
                pending_since = pending_since or day
            latest_signal = new_signal
            latest_eval = pd.Timestamp(event.eval_date)
        if active is None and latest_signal > 0:
            pending_since = pending_since or day
        if active is not None and latest_signal <= active.fraction + 1e-12:
            pending_since = None
        if active is None and latest_signal == 0:
            pending_since = None

        denominator = float(row.prior_settle) * 200.0
        etf_row = etf.loc[day]
        etf_open, etf_close = float(etf_row["open"]), float(etf_row["close"])
        pnl = 0.0
        cost = 0.0
        action = ""
        carried = False
        stale_days = 0
        old_fraction = active.fraction if active is not None else 0.0
        request = pending_since

        if active is None and latest_signal > 0 and pending_since is not None:
            month = v6.desired_real_month(
                snapshots, day, "3m_monthly", trade_dates
            )
            selected = (
                proxy.select_real_contract(snapshots, history_lookup, day, month)
                if month is not None
                else None
            )
            if selected is not None:
                master, quote = selected
                qty, full_qty = _real_target_qty(row, etf_open, latest_signal)
                pnl += qty * 10000.0 * (
                    float(quote["close"]) - float(quote["open"])
                ) / denominator
                cost += latest_signal * proxy.PUT_FULL_SIDE_COST
                active = proxy.RealPosition(
                    str(master["security_id"]),
                    str(master["contract_id"]),
                    pd.Timestamp(master["contract_month"]),
                    proxy.fourth_wednesday(
                        pd.Timestamp(master["contract_month"]), trade_dates
                    ),
                    float(master["strike"]),
                    qty,
                    full_qty,
                    latest_signal,
                    float(quote["close"]),
                    float(master["strike"]) / etf_open,
                )
                meta = {
                    "entry_date": day,
                    "initial_fraction": latest_signal,
                    "max_fraction": latest_signal,
                    "upsize_count": 0,
                    "renewal": renewal_due,
                }
                action = "open_renewal" if renewal_due else "open_buy"
                renewal_due = False
                pending_since = None
        elif active is not None and latest_signal > active.fraction + 1e-12:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            if quote is not None and float(quote["open"]) > 0 and float(quote["volume"]) > 0:
                open_price, close_price = float(quote["open"]), float(quote["close"])
                pnl += active.qty * 10000.0 * (open_price - active.prior_mark) / denominator
                target_qty, full_qty = _real_target_qty(row, etf_open, latest_signal)
                new_qty = max(active.qty, target_qty)
                pnl += new_qty * 10000.0 * (close_price - open_price) / denominator
                cost += (latest_signal - active.fraction) * proxy.PUT_FULL_SIDE_COST
                active.qty = new_qty
                active.full_qty = full_qty
                active.fraction = latest_signal
                active.prior_mark = close_price
                if meta is None:
                    raise RuntimeError("Missing real hold lifecycle metadata")
                meta["max_fraction"] = latest_signal
                meta["upsize_count"] = int(meta["upsize_count"]) + 1
                action = "open_upsize"
                pending_since = None

        if not action and active is not None:
            mark, stale_days, carried = proxy.real_mark(
                history_groups, active, day, etf_close
            )
            pnl += active.qty * 10000.0 * (mark - active.prior_mark) / denominator
            active.prior_mark = mark

        if action:
            actual_request = request or day
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": actual_request,
                    "actual_execution_date": day,
                    "action": action,
                    "signal_target_fraction": latest_signal,
                    "old_executed_fraction": old_fraction,
                    "new_executed_fraction": active.fraction if active else 0.0,
                    "old_contract": active.contract_id if action == "open_upsize" else "",
                    "new_contract": active.contract_id if active else "",
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": (
                        active.entry_moneyness if active is not None and action != "open_upsize" else np.nan
                    ),
                    "delay_days": int((day - actual_request).days),
                    "delay_trading_days": _trading_delay(
                        trade_dates, actual_request, day
                    ),
                    "renewal": action == "open_renewal",
                    "early_exit": False,
                    "downsize": False,
                }
            )

        executed_for_day = active.fraction if active is not None else 0.0
        expired = False
        if active is not None and active.expiry == day:
            expired = True
            if meta is None:
                raise RuntimeError("Missing real expiry lifecycle metadata")
            lifecycles.append(
                {
                    "candidate": label,
                    "entry_date": meta["entry_date"],
                    "contract_month": active.contract_month,
                    "contract_id": active.contract_id,
                    "expiry": active.expiry,
                    "completed": True,
                    "open_at_sample_end": False,
                    "initial_fraction": meta["initial_fraction"],
                    "max_fraction": meta["max_fraction"],
                    "upsize_count": meta["upsize_count"],
                    "renewal_entry": meta["renewal"],
                    "calendar_days": int((active.expiry - pd.Timestamp(meta["entry_date"])).days),
                    "ic_rolls_covered": _rolls_covered(
                        pd.Timestamp(meta["entry_date"]), active.expiry, roll_dates
                    ),
                    "early_exit": False,
                }
            )
            active = None
            meta = None
            renewal_due = latest_signal > 0
            pending_since = _next_trade_date(trade_dates, day) if renewal_due else None

        mark_fraction = 0.0
        contract = ""
        qty = 0
        entry_moneyness = np.nan
        if active is not None:
            mark_fraction = (
                active.qty * 10000.0 * active.prior_mark / (float(row.settle) * 200.0)
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
                "signal_target_fraction": latest_signal,
                "target_fraction": executed_for_day,
                "entry_moneyness_mark": entry_moneyness,
                "carried_mark": carried,
                "mark_stale_days": stale_days,
                "deferred_adjustment": pending_since is not None,
                "expired": expired,
            }
        )

    if active is not None and meta is not None:
        lifecycles.append(
            {
                "candidate": label,
                "entry_date": meta["entry_date"],
                "contract_month": active.contract_month,
                "contract_id": active.contract_id,
                "expiry": active.expiry,
                "completed": False,
                "open_at_sample_end": True,
                "initial_fraction": meta["initial_fraction"],
                "max_fraction": meta["max_fraction"],
                "upsize_count": meta["upsize_count"],
                "renewal_entry": meta["renewal"],
                "calendar_days": int((core.END - pd.Timestamp(meta["entry_date"])).days),
                "ic_rolls_covered": _rolls_covered(
                    pd.Timestamp(meta["entry_date"]), core.END, roll_dates
                ),
                "early_exit": False,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lifecycles)


def parity_audit(daily: pd.DataFrame) -> pd.DataFrame:
    v5_daily = pd.read_csv(v5.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    v6_daily = pd.read_csv(v6.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    pairs: list[tuple[str, str, pd.DataFrame]] = []
    for layer in ["model", "real"]:
        pairs.append((f"{layer}_no_put", f"{layer}_no_put", v5_daily))
        for signal, old in [
            ("v5_original", "abs_stress_any"),
            ("always_50", "always_50"),
            ("always_100", "always_100"),
        ]:
            pairs.append(
                (
                    f"{layer}_front_original_{signal}",
                    f"{layer}_{old}",
                    v5_daily,
                )
            )
        for signal in ["always_50", "always_100"]:
            pairs.append(
                (
                    f"{layer}_3m_monthly_{signal}",
                    f"{layer}_3m_monthly_{signal}",
                    v6_daily,
                )
            )
    rows: list[dict[str, object]] = []
    for current_label, prior_label, prior_frame in pairs:
        left = daily[daily["candidate"].eq(current_label)]
        right = prior_frame[prior_frame["candidate"].eq(prior_label)]
        joined = left.merge(right, on="date", suffixes=("_v7", "_prior"), validate="one_to_one")
        result: dict[str, object] = {
            "current_candidate": current_label,
            "prior_candidate": prior_label,
            "prior_version": "v5" if prior_frame is v5_daily else "v6",
            "rows": len(joined),
        }
        for column in [
            "put_pnl_ret",
            "put_cost_rate",
            "target_fraction",
            "ret",
            "cash_ret",
        ]:
            result[f"max_abs_{column}_diff"] = float(
                (joined[f"{column}_v7"] - joined[f"{column}_prior"]).abs().max()
            )
        rows.append(result)
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].to_numpy().max() > 1e-14:
        raise RuntimeError("v7 baseline parity failed")
    return table


def lifecycle_audit(
    lifecycles: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        for signal in ["always_50", "always_100"]:
            candidate = f"{layer}_3m_hold_expiry_{signal}"
            life = lifecycles[lifecycles["candidate"].eq(candidate)].copy()
            complete = life[life["completed"].astype(bool)].copy()
            evaluated = complete.copy()
            if layer == "model":
                evaluated = evaluated[
                    pd.to_datetime(evaluated["entry_date"]) > core.MODEL_START
                ]
            coverage_ratio = (
                float(evaluated["ic_rolls_covered"].eq(3).mean())
                if len(evaluated)
                else 0.0
            )
            trade = trades[trades["candidate"].eq(candidate)].copy()
            renewals = trade[trade.get("renewal", False).fillna(False)] if len(trade) else trade
            max_delay = (
                int(renewals["delay_trading_days"].fillna(0).max())
                if len(renewals)
                else 0
            )
            early = int(life["early_exit"].fillna(False).sum()) if len(life) else 0
            passed = bool(
                len(evaluated) > 0
                and early == 0
                and max_delay <= 5
                and (
                    math.isclose(coverage_ratio, 1.0, abs_tol=1e-12)
                    if layer == "model"
                    else coverage_ratio >= 0.90
                )
            )
            rows.append(
                {
                    "layer": layer,
                    "signal_variant": signal,
                    "candidate": candidate,
                    "lifecycles": len(life),
                    "completed_lifecycles": len(complete),
                    "evaluated_completed_lifecycles": len(evaluated),
                    "three_ic_roll_lifecycles": int(
                        evaluated["ic_rolls_covered"].eq(3).sum()
                    ),
                    "three_ic_roll_ratio": coverage_ratio,
                    "median_calendar_days": (
                        float(evaluated["calendar_days"].median())
                        if len(evaluated)
                        else np.nan
                    ),
                    "renewal_trades": len(renewals),
                    "max_renewal_delay_trading_days": max_delay,
                    "early_exits": early,
                    "passed": passed,
                }
            )
    return pd.DataFrame(rows)


def period_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        baseline = daily[daily["candidate"].eq(f"{layer}_no_put")][
            ["date", "cash_ret"]
        ].rename(columns={"cash_ret": "baseline_cash_ret"})
        for execution in EXECUTION_MODES:
            for signal in SIGNAL_MODES:
                candidate = f"{layer}_{execution}_{signal}"
                path = daily[daily["candidate"].eq(candidate)][
                    [
                        "date",
                        "cash_ret",
                        "put_pnl_ret",
                        "put_cost_rate",
                        "signal_target_fraction",
                        "target_fraction",
                    ]
                ]
                joined = path.merge(baseline, on="date", validate="one_to_one")
                for period, (start, end) in PAYOUT_WINDOWS.items():
                    sample = joined[joined["date"].between(start, end)].copy()
                    relative_log = (
                        np.log1p(sample["cash_ret"])
                        - np.log1p(sample["baseline_cash_ret"])
                    )
                    rows.append(
                        {
                            "candidate": candidate,
                            **candidate_parts(candidate),
                            "period": period,
                            "requested_start": start,
                            "requested_end": end,
                            "actual_start": sample["date"].min() if len(sample) else pd.NaT,
                            "actual_end": sample["date"].max() if len(sample) else pd.NaT,
                            "rows": len(sample),
                            "relative_terminal_return": (
                                float(np.expm1(relative_log.sum())) if len(sample) else np.nan
                            ),
                            "put_pnl_ret_sum": (
                                float(sample["put_pnl_ret"].sum()) if len(sample) else np.nan
                            ),
                            "put_cost_rate_sum": (
                                float(sample["put_cost_rate"].sum()) if len(sample) else np.nan
                            ),
                            "average_signal_target": (
                                float(sample["signal_target_fraction"].mean())
                                if len(sample)
                                else np.nan
                            ),
                            "average_executed_target": (
                                float(sample["target_fraction"].mean())
                                if len(sample)
                                else np.nan
                            ),
                            "executed_protected_ratio": (
                                float(sample["target_fraction"].gt(0).mean())
                                if len(sample)
                                else np.nan
                            ),
                        }
                    )
    return pd.DataFrame(rows)


def _candidate_period(
    attribution: pd.DataFrame,
    candidate: str,
    period: str,
) -> pd.Series:
    row = attribution[
        attribution["candidate"].eq(candidate) & attribution["period"].eq(period)
    ]
    if len(row) != 1:
        raise RuntimeError(f"Missing v7 attribution row: {candidate}, {period}")
    return row.iloc[0]


def decision_outputs(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    attribution: pd.DataFrame,
    lifecycle: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model = formal[formal["layer"].eq("model")]
    real = formal[formal["layer"].eq("real")]
    model_base = model[model["signal_variant"].eq("no_put")].set_index("segment")
    real_base = real[real["signal_variant"].eq("no_put")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    lifecycle_ok = bool(lifecycle["passed"].all())
    rows: list[dict[str, object]] = []

    for execution in EXECUTION_MODES:
        for signal in ECON_SIGNALS:
            candidate = f"model_{execution}_{signal}"
            real_candidate = f"real_{execution}_{signal}"
            model_rows = model[model["candidate"].eq(candidate)].set_index("segment")
            real_rows = real[real["candidate"].eq(real_candidate)].set_index("segment")
            window_cagr = {
                segment: float(
                    model_rows.loc[segment, "cash_ann_return"]
                    - model_base.loc[segment, "cash_ann_return"]
                )
                for segment in REQUIRED_SEGMENTS
            }
            window_dd = {
                segment: float(
                    model_rows.loc[segment, "cash_max_dd"]
                    - model_base.loc[segment, "cash_max_dd"]
                )
                for segment in REQUIRED_SEGMENTS
            }
            return_pass = all(
                window_cagr[segment]
                >= (-0.01 if segment in {"full", "last_10y", "last_5y"} else -0.03)
                for segment in REQUIRED_SEGMENTS
            )
            improved = sum(value > 1e-12 for value in window_dd.values())
            dev_cagr = float(
                model_rows.loc["development", "cash_ann_return"]
                - model_base.loc["development", "cash_ann_return"]
            )
            dev_dd = float(
                model_rows.loc["development", "cash_max_dd"]
                - model_base.loc["development", "cash_max_dd"]
            )
            revision_cagr = float(
                model_rows.loc["revision_validation", "cash_ann_return"]
                - model_base.loc["revision_validation", "cash_ann_return"]
            )
            revision_dd = float(
                model_rows.loc["revision_validation", "cash_max_dd"]
                - model_base.loc["revision_validation", "cash_max_dd"]
            )
            recent_cagr = float(
                model_rows.loc["recent_expansion", "cash_ann_return"]
                - model_base.loc["recent_expansion", "cash_ann_return"]
            )
            recent_dd = float(
                model_rows.loc["recent_expansion", "cash_max_dd"]
                - model_base.loc["recent_expansion", "cash_max_dd"]
            )
            real_cagr = float(
                real_rows.loc["full", "cash_ann_return"]
                - real_base.loc["full", "cash_ann_return"]
            )
            real_dd = float(
                real_rows.loc["full", "cash_max_dd"]
                - real_base.loc["full", "cash_max_dd"]
            )
            known = _candidate_period(attribution, candidate, "known_drawdown")
            early = _candidate_period(attribution, candidate, "early_drawdown")
            model_days = int(exposure_lookup.loc[candidate, "protected_days"])
            real_days = int(exposure_lookup.loc[real_candidate, "protected_days"])
            single = bool(
                dev_dd >= 0.03
                and dev_cagr >= -0.01
                and revision_dd >= 0.03
                and revision_cagr >= -0.01
                and recent_cagr >= -0.01
                and recent_dd >= -0.01
                and improved >= 3
                and return_pass
                and real_dd >= 0.005
                and real_cagr >= -0.01
                and model_days >= 20
                and real_days >= 20
                and float(early["average_executed_target"]) >= 0.50
            )
            rows.append(
                {
                    "execution_mode": execution,
                    "signal_variant": signal,
                    "candidate": candidate,
                    "development_cagr_delta": dev_cagr,
                    "development_dd_improvement": dev_dd,
                    "revision_cagr_delta": revision_cagr,
                    "revision_dd_improvement": revision_dd,
                    "recent_cagr_delta": recent_cagr,
                    "recent_dd_improvement": recent_dd,
                    "real_cagr_delta": real_cagr,
                    "real_dd_improvement": real_dd,
                    "improved_required_windows": improved,
                    "return_tolerance_pass": return_pass,
                    "model_protected_days": model_days,
                    "real_protected_days": real_days,
                    "known_drawdown_average_executed_target": float(
                        known["average_executed_target"]
                    ),
                    "early_drawdown_average_executed_target": float(
                        early["average_executed_target"]
                    ),
                    "average_executed_target": float(
                        exposure_lookup.loc[candidate, "average_target_fraction"]
                    ),
                    "single_candidate_pass": single,
                }
            )

    decisions = pd.DataFrame(rows)
    pass_lookup = decisions.set_index(["execution_mode", "signal_variant"])[
        "single_candidate_pass"
    ].to_dict()
    support_rows: list[dict[str, object]] = []
    for row in decisions.itertuples(index=False):
        execution_index = EXECUTION_MODES.index(row.execution_mode)
        neighbors: list[tuple[str, str]] = []
        if execution_index > 0:
            neighbors.append((EXECUTION_MODES[execution_index - 1], row.signal_variant))
        if execution_index < len(EXECUTION_MODES) - 1:
            neighbors.append((EXECUTION_MODES[execution_index + 1], row.signal_variant))
        other_signal = "stress_latch" if row.signal_variant == "v5_original" else "v5_original"
        neighbors.append((row.execution_mode, other_signal))
        supporting = [
            f"{execution}_{signal}"
            for execution, signal in neighbors
            if pass_lookup.get((execution, signal), False)
        ]
        support_rows.append(
            {
                "execution_mode": row.execution_mode,
                "signal_variant": row.signal_variant,
                "supporting_neighbors": ";".join(supporting),
                "neighbor_pass": bool(supporting),
                "all_preregistered_pass": bool(
                    row.single_candidate_pass and supporting and lifecycle_ok
                ),
            }
        )
    decisions = decisions.merge(
        pd.DataFrame(support_rows),
        on=["execution_mode", "signal_variant"],
        validate="one_to_one",
    )
    passed = decisions[decisions["all_preregistered_pass"]].copy()
    if not lifecycle_ok:
        summary = {
            "decision": "rerun_required",
            "stability_label": "data_sensitive",
            "selected_variant": None,
            "passing_candidates": [],
            "sample_reuse": "not_independent_oos",
        }
    elif passed.empty:
        summary = {
            "decision": "keep_default",
            "stability_label": "reject",
            "selected_variant": None,
            "passing_candidates": [],
            "sample_reuse": "not_independent_oos",
        }
    else:
        preferred = passed[
            passed["execution_mode"].eq("3m_hold_expiry")
            & passed["signal_variant"].eq("stress_latch")
        ]
        selected_row = (
            preferred.iloc[0]
            if len(preferred)
            else passed.sort_values(
                ["average_executed_target", "execution_mode", "signal_variant"]
            ).iloc[0]
        )
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if len(passed) >= 4 else "narrow_stable",
            "selected_variant": (
                f"{selected_row['execution_mode']}_{selected_row['signal_variant']}"
            ),
            "passing_candidates": [
                f"{item.execution_mode}_{item.signal_variant}"
                for item in passed.itertuples(index=False)
            ],
            "sample_reuse": "not_independent_oos",
        }
    return decisions, summary


def build_record(
    formal: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, object],
    lifecycle: pd.DataFrame,
    current: pd.DataFrame,
) -> str:
    selected = formal[
        formal["layer"].eq("model")
        & formal["segment"].isin(REQUIRED_SEGMENTS)
        & formal["available"].eq(True)
        & (
            formal["signal_variant"].eq("no_put")
            | formal["signal_variant"].isin(ECON_SIGNALS)
        )
    ][
        [
            "candidate",
            "execution_mode",
            "signal_variant",
            "segment",
            "cash_ann_return",
            "cash_max_dd",
        ]
    ]
    decision_cols = [
        "execution_mode",
        "signal_variant",
        "revision_cagr_delta",
        "revision_dd_improvement",
        "real_cagr_delta",
        "real_dd_improvement",
        "improved_required_windows",
        "single_candidate_pass",
        "neighbor_pass",
        "all_preregistered_pass",
    ]
    current_cols = [
        "signal_variant",
        "absolute_risk",
        "valuation_state",
        "stress",
        "research_target_fraction",
    ]
    lines = [
        "# IC + 510500 Put 压力状态保持与三周期持有 v7",
        "",
        "> 研究回测；未获准实盘；全部历史已被v4—v6观察。",
        "",
        "## 决定",
        "",
        f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。",
        "",
        "## 模型层强制窗口",
        "",
        selected.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 预注册判断",
        "",
        decisions[decision_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 持有到期生命周期审计",
        "",
        lifecycle.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 2026-08-14研究状态",
        "",
        current[current_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 限制",
        "",
        "- 状态保持和三周期持有规则在收益计算前冻结。",
        "- 3m持有批次只向上补仓，不提前平仓或减仓；到期后下一交易日才续保。",
        "- 2015—2022为模型Put；真实层是第三方日线，不代表盘口成交。",
        "- 当前状态仅用于研究审计，不是订单。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    v5_manifest = verify_inputs()
    frames = core.v2.load_inputs()
    daily_valuation, valuation_checks = core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, current, signal_audit, signal_stats = build_signal_panel(
        frames["ic"], daily_valuation
    )
    configure_metrics()
    market, market_checks = proxy.prepare_model_market(
        frames["ic"],
        daily_valuation,
        frames["q50"],
        frames["etf50"],
        frames["index_sina"],
    )
    qvix_table, qvix_stats = proxy.qvix_validation(market, frames["q500"])
    roll_dates = v6.forced_roll_dates(frames["ic"])

    daily_parts: list[pd.DataFrame] = [
        proxy.no_put_rows(frames["ic"], core.MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], core.REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    lifecycle_parts: list[pd.DataFrame] = []
    for execution in EXECUTION_MODES:
        for signal in SIGNAL_MODES:
            model_schedule = schedule[
                schedule["layer"].eq("model")
                & schedule["signal_variant"].eq(signal)
            ]
            real_schedule = schedule[
                schedule["layer"].eq("real")
                & schedule["signal_variant"].eq(signal)
            ]
            model_label = f"model_{execution}_{signal}"
            real_label = f"real_{execution}_{signal}"
            if execution == "front_original":
                model_overlay, model_trades = proxy.run_model_candidate(
                    frames["ic"],
                    model_schedule,
                    market,
                    "daily",
                    "front",
                    "three_tier",
                    0.85,
                    model_label,
                )
                real_overlay, real_trades = proxy.run_real_candidate(
                    frames["ic"],
                    real_schedule,
                    frames["snapshots"],
                    frames["histories"],
                    frames["etf500"],
                    "daily",
                    "front",
                    "three_tier",
                    real_label,
                )
            elif execution == "3m_monthly":
                model_overlay, model_trades = v6.run_model_monthly_tenor(
                    frames["ic"],
                    model_schedule,
                    market,
                    "3m_monthly",
                    model_label,
                    roll_dates,
                )
                real_overlay, real_trades = v6.run_real_monthly_tenor(
                    frames["ic"],
                    real_schedule,
                    frames["snapshots"],
                    frames["histories"],
                    frames["etf500"],
                    "3m_monthly",
                    real_label,
                    roll_dates,
                )
            else:
                model_overlay, model_trades, model_life = run_model_hold_expiry(
                    frames["ic"],
                    model_schedule,
                    market,
                    model_label,
                    roll_dates,
                )
                real_overlay, real_trades, real_life = run_real_hold_expiry(
                    frames["ic"],
                    real_schedule,
                    frames["snapshots"],
                    frames["histories"],
                    frames["etf500"],
                    real_label,
                    roll_dates,
                )
                lifecycle_parts.extend([model_life, real_life])
            for overlay, trades in [
                (model_overlay, model_trades),
                (real_overlay, real_trades),
            ]:
                if "signal_target_fraction" not in overlay:
                    overlay["signal_target_fraction"] = overlay["target_fraction"]
                daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
                if not trades.empty:
                    trade_parts.append(trades)

    daily = pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
        ["candidate", "date"]
    ).reset_index(drop=True)
    daily["signal_target_fraction"] = daily["signal_target_fraction"].fillna(
        daily["target_fraction"]
    )
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(lifecycle_parts, ignore_index=True, sort=False)
    parity = parity_audit(daily)
    formal, scan_summary, wide = core.metric_outputs(daily)
    annual = core.annual_metrics(daily)
    exposure = core.v2.exposure_summary(daily, trades)
    cross_table, cross_stats = core.real_model_validation(daily)
    concentration = core.event_concentration(daily)
    lifecycle_table = lifecycle_audit(lifecycles, trades)
    attribution = period_attribution(daily)
    decisions, decision_summary = decision_outputs(
        formal, exposure, attribution, lifecycle_table
    )

    expected = {
        f"{layer}_{variant}"
        for layer in ["model", "real"]
        for variant in ALL_GRID_VARIANTS
    }
    if set(daily["candidate"]) != expected:
        raise RuntimeError("v7 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v7 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (
        daily[["ret", "cash_ret"]] <= -1
    ).any().any():
        raise RuntimeError("Invalid v7 daily return")
    if not qvix_stats["passed"]:
        raise RuntimeError("QVIX proxy validation failed")
    if not signal_stats["all_carried_days_full_target"] or not signal_stats[
        "all_low_no_stress_days_zero"
    ]:
        raise RuntimeError("Stress latch state audit failed")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes scheduled execution")
    hold_trades = trades[trades["candidate"].str.contains("3m_hold_expiry")]
    if hold_trades["action"].isin(["open_exit", "open_resize", "open_roll"]).any():
        raise RuntimeError("Hold-to-expiry path sold, downsized, or rolled early")
    permanent = exposure[
        exposure["signal_variant"].isin(["always_50", "always_100"])
    ]
    if (permanent["trade_events"] <= 0).any() or (
        permanent["average_put_mark_fraction"] <= 0
    ).any():
        raise RuntimeError("Permanent Put engine benchmark is empty")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    lifecycles.to_csv(OUTPUT / "hold_expiry_lifecycles.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "valuation_signals.csv.gz", index=False, compression="gzip")
    current.to_csv(OUTPUT / "current_research_signals.csv", index=False)
    signal_audit.to_csv(OUTPUT / "stress_latch_signal_audit.csv.gz", index=False, compression="gzip")
    formal.to_csv(OUTPUT / "metrics_by_segment.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    parity.to_csv(OUTPUT / "baseline_parity.csv", index=False)
    lifecycle_table.to_csv(OUTPUT / "hold_expiry_lifecycle_audit.csv", index=False)
    attribution.to_csv(OUTPUT / "period_attribution.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "record.md").write_text(
        build_record(formal, decisions, decision_summary, lifecycle_table, current),
        encoding="utf-8",
    )

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "candidate_count": len(expected),
        "sample": {
            "model": [str(core.MODEL_START.date()), str(core.END.date())],
            "real": [str(core.REAL_START.date()), str(core.END.date())],
            "development": [str(core.MODEL_START.date()), str(v5.DEVELOPMENT_END.date())],
            "revision_validation": [str(v5.REVISION_START.date()), str(v5.REVISION_END.date())],
            "recent_expansion": [str(v5.RECENT_START.date()), str(core.END.date())],
        },
        "valuation_checks": valuation_checks,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "signal_latch_audit": signal_stats,
        "baseline_parity_max_abs": float(
            parity[
                [column for column in parity if column.startswith("max_abs_")]
            ].to_numpy().max()
        ),
        "lifecycle_audit_pass": bool(lifecycle_table["passed"].all()),
        "decision_summary": decision_summary,
        "dependencies": {
            "v5_signal": {"path": str(V5_PATH.relative_to(ROOT)), "sha256": V5_SHA256},
            "v6_tenor": {"path": str(V6_PATH.relative_to(ROOT)), "sha256": V6_SHA256},
            "v3_metrics": {"path": str(V3_PATH.relative_to(ROOT)), "sha256": V3_SHA256},
            "proxy_engine": {"path": str(PROXY_PATH.relative_to(ROOT)), "sha256": PROXY_SHA256},
        },
        "source_hashes": v5_manifest["source_hashes"],
        "git_status": core.git_status(),
        "warnings": [
            "All v7 history was observed during v4-v6 and is not independent OOS.",
            "Model Put is theoretical; actual daily bars are not executable quote proof.",
            "Hold-to-expiry renews next session and does not hedge the overnight gap after expiry close.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    commands = (
        "python.exe -m pytest test_ic_510500_put_persistent_stress_hold3m_v7.py -q\n"
        "python.exe ic_510500_put_persistent_stress_hold3m_v7.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(commands, encoding="utf-8")
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(SCAN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\n" + commands)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "source_hashes": manifest["source_hashes"],
            "baseline_parity_max_abs": manifest["baseline_parity_max_abs"],
            "lifecycle_audit_pass": manifest["lifecycle_audit_pass"],
            "qvix_proxy": qvix_stats,
            "decision_summary": decision_summary,
            "formal_output": str(OUTPUT.relative_to(ROOT)),
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "baseline_parity": manifest["baseline_parity_max_abs"],
                "lifecycle_audit_pass": manifest["lifecycle_audit_pass"],
                "qvix_passed": qvix_stats["passed"],
                **decision_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
