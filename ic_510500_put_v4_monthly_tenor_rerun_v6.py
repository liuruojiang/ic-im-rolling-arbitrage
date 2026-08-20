from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_proxy_validation_v1 as proxy
import ic_510500_put_rolling_continuous_valuation_v3 as core
import ic_510500_put_rolling_continuous_valuation_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_v4_monthly_tenor_rerun_v6"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "d6acd881fc21c8571e67ce8db3037c742a38b53e07a2a8d3284a8c0d616baa93"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_v4_monthly_tenor_rerun_v6"

V4_PATH = Path(v4.__file__).resolve()
V4_SHA256 = "ad4df8f5a1adcb8d193514083135f4b2d0551686172e41878c2e95b5337339c4"
V3_PATH = Path(core.__file__).resolve()
V3_SHA256 = "7b05088caa2ae40358ade307be6fac9728e34c276f04fba97fb110964fc4ebd9"
PROXY_PATH = Path(proxy.__file__).resolve()
PROXY_SHA256 = "5836849ca4c0e42ab4a04e2c82d81f049b5f2fb2799333c67177209b8fc2a7a3"

TENORS = ["front_original", "2m_monthly", "3m_monthly"]
MONTHLY_TENORS = ["2m_monthly", "3m_monthly"]
TENOR_MONTHS = {"2m_monthly": 2, "3m_monthly": 3}
GRID_VARIANTS = [f"{tenor}_{signal}" for tenor in TENORS for signal in v4.VARIANTS]
ALL_GRID_VARIANTS = ["no_put", *GRID_VARIANTS]
REQUIRED_SEGMENTS = list(core.REQUIRED_WINDOWS)


def sha256(path: Path) -> str:
    return core.sha256(path)


def split_grid_variant(grid_variant: str) -> tuple[str, str]:
    for tenor in TENORS:
        prefix = f"{tenor}_"
        if grid_variant.startswith(prefix):
            return tenor, grid_variant[len(prefix) :]
    raise ValueError(f"Unknown v6 grid variant: {grid_variant}")


def variant_parameters(grid_variant: str) -> dict[str, object]:
    if grid_variant == "no_put":
        return {
            "tenor": "none",
            "score_type": "baseline",
            "window_months": np.nan,
            "window_years": np.nan,
            "lower_risk": np.nan,
            "full_risk": np.nan,
        }
    tenor, signal = split_grid_variant(grid_variant)
    return {"tenor": tenor, **v4.variant_parameters(signal)}


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, grid_variant = candidate.split("_", 1)
    if grid_variant == "no_put":
        return {
            "layer": layer,
            "signal_variant": "no_put",
            **variant_parameters("no_put"),
        }
    tenor, signal = split_grid_variant(grid_variant)
    return {
        "layer": layer,
        "signal_variant": signal,
        "tenor": tenor,
        **v4.variant_parameters(signal),
    }


def configure_metrics() -> None:
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.VARIANTS = GRID_VARIANTS
    core.ALL_VARIANTS = ALL_GRID_VARIANTS
    core.variant_parameters = variant_parameters
    core.candidate_parts = candidate_parts
    core.v2.candidate_parts = candidate_parts


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v6 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v6 specification sidecar mismatch")
    for path, expected in [
        (V4_PATH, V4_SHA256),
        (V3_PATH, V3_SHA256),
        (PROXY_PATH, PROXY_SHA256),
    ]:
        if sha256(path) != expected:
            raise RuntimeError(f"Frozen dependency changed: {path.name}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")
    manifest = json.loads((v4.OUTPUT / "data_manifest.json").read_text(encoding="utf-8"))
    if manifest["script_sha256"] != V4_SHA256 or manifest["spec_sha256"] != v4.SPEC_SHA256:
        raise RuntimeError("v4 formal manifest dependency mismatch")
    for relative, expected in manifest["source_hashes"].items():
        path = ROOT / relative
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"v4 frozen input changed: {relative}")
    return manifest


def forced_roll_dates(ic: pd.DataFrame) -> set[pd.Timestamp]:
    if "roll_from" not in ic.columns:
        raise RuntimeError("Frozen IC baseline is missing roll_from")
    dates = set(pd.to_datetime(ic.loc[ic["roll_from"].notna(), "date"]))
    if len(dates) != 135:
        raise RuntimeError(f"Unexpected IC roll-date count: {len(dates)}")
    return dates


def desired_model_month(
    day: pd.Timestamp, tenor: str, trade_dates: pd.DatetimeIndex
) -> pd.Timestamp:
    target = day + pd.DateOffset(months=TENOR_MONTHS[tenor])
    return proxy.select_model_month(day, target, trade_dates)


def desired_real_month(
    snapshots: pd.DataFrame,
    day: pd.Timestamp,
    tenor: str,
    trade_dates: pd.DatetimeIndex,
) -> pd.Timestamp | None:
    target = day + pd.DateOffset(months=TENOR_MONTHS[tenor])
    return proxy.select_real_month(snapshots, day, target, trade_dates)


def _model_open_position(
    row: object,
    tenor: str,
    fraction: float,
    trade_dates: pd.DatetimeIndex,
) -> tuple[proxy.ModelPosition, float, float]:
    day = pd.Timestamp(row.date)
    month = desired_model_month(day, tenor, trade_dates)
    expiry = proxy.fourth_wednesday(month, trade_dates)
    strike = float(row.spot_open) * 0.85
    units = float(row.settle) * 200.0 / float(row.spot_open) * fraction
    position = proxy.ModelPosition(month, expiry, strike, units, fraction, 0.0)
    open_price = proxy.option_price(position, row, "open")
    close_price = proxy.option_price(position, row, "close")
    position.prior_mark = close_price
    return position, open_price, close_price


def run_model_monthly_tenor(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    market: pd.DataFrame,
    tenor: str,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = ic[ic["date"] >= core.MODEL_START].copy().reset_index(drop=True)
    daily["prior_settle"] = daily["settle"].shift(1)
    daily.loc[0, "prior_settle"] = daily.loc[0, "settle"]
    merged = daily.merge(market.drop(columns=["settle"]), on="date", validate="one_to_one")
    events = proxy.schedule_events(schedule, "model", "daily")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: proxy.ModelPosition | None = None
    latest_fraction = 0.0
    latest_eval: pd.Timestamp | None = None
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []

    for row in merged.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        fraction_changed = False
        if event is not None:
            new_fraction = float(event.three_tier_target_fraction)
            fraction_changed = not math.isclose(new_fraction, latest_fraction, abs_tol=1e-12)
            latest_fraction = new_fraction
            latest_eval = pd.Timestamp(event.eval_date)
        denominator = float(row.prior_settle) * 200.0
        pnl = 0.0
        cost = 0.0
        action = ""
        old = active
        monthly_request = bool(day in roll_dates and active is not None and latest_fraction > 0)
        same_month_reset = False

        if active is None and latest_fraction > 0:
            active, open_price, close_price = _model_open_position(
                row, tenor, latest_fraction, trade_dates
            )
            pnl += active.units * (close_price - open_price) / denominator
            cost += latest_fraction * proxy.PUT_FULL_SIDE_COST
            action = "open_buy"
        elif active is not None and latest_fraction == 0:
            open_price = proxy.option_price(active, row, "open")
            pnl += active.units * (open_price - active.prior_mark) / denominator
            cost += active.fraction * proxy.PUT_FULL_SIDE_COST
            active = None
            action = "open_exit"
        elif active is not None and monthly_request:
            old_open = proxy.option_price(active, row, "open")
            pnl += active.units * (old_open - active.prior_mark) / denominator
            new_position, new_open, new_close = _model_open_position(
                row, tenor, latest_fraction, trade_dates
            )
            same_month_reset = new_position.contract_month == active.contract_month
            pnl += new_position.units * (new_close - new_open) / denominator
            cost += (active.fraction + latest_fraction) * proxy.PUT_FULL_SIDE_COST
            active = new_position
            action = "open_roll_monthly"
        elif active is not None and fraction_changed:
            open_price = proxy.option_price(active, row, "open")
            pnl += active.units * (open_price - active.prior_mark) / denominator
            new_units = float(row.settle) * 200.0 / float(row.spot_open) * latest_fraction
            close_price = proxy.option_price(active, row, "close")
            pnl += new_units * (close_price - open_price) / denominator
            cost += abs(latest_fraction - active.fraction) * proxy.PUT_FULL_SIDE_COST
            active.units = new_units
            active.fraction = latest_fraction
            active.prior_mark = close_price
            action = "open_resize"
        elif active is not None:
            close_price = proxy.option_price(active, row, "close")
            pnl += active.units * (close_price - active.prior_mark) / denominator
            active.prior_mark = close_price

        if action:
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": day,
                    "actual_execution_date": day,
                    "action": action,
                    "target_fraction": latest_fraction,
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": 0.85 if active is not None else np.nan,
                    "delay_days": 0,
                    "delay_trading_days": 0,
                    "forced_month_roll": action == "open_roll_monthly",
                    "roll_request_date": day if action == "open_roll_monthly" else pd.NaT,
                    "same_month_reset": same_month_reset,
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
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
                "target_fraction": latest_fraction,
                "entry_moneyness_mark": 0.85 if active is not None else np.nan,
                "carried_mark": False,
                "mark_stale_days": 0,
                "deferred_adjustment": False,
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def _real_target_qty(row: object, etf_open: float, fraction: float) -> tuple[int, int]:
    full_qty = max(1, int(round(float(row.settle) * 200.0 / (etf_open * 10000.0))))
    return max(1, int(round(full_qty * fraction))), full_qty


def _trading_delay(
    trade_dates: pd.DatetimeIndex, request: pd.Timestamp, actual: pd.Timestamp
) -> int:
    return int(((trade_dates > request) & (trade_dates <= actual)).sum())


def run_real_monthly_tenor(
    ic: pd.DataFrame,
    schedule: pd.DataFrame,
    snapshots: pd.DataFrame,
    histories: pd.DataFrame,
    etf500: pd.DataFrame,
    tenor: str,
    label: str,
    roll_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = ic[ic["date"] >= core.REAL_START].copy().reset_index(drop=True)
    offset = len(ic) - len(daily)
    daily["prior_settle"] = ic["settle"].shift(1).loc[daily.index + offset].to_numpy()
    daily.loc[0, "prior_settle"] = float(ic.loc[ic["date"] < core.REAL_START, "settle"].iloc[-1])
    etf = etf500.set_index("date")
    history_lookup = histories.set_index(["security_id", "date"])
    history_groups = {
        key: group.sort_values("date") for key, group in histories.groupby("security_id")
    }
    events = proxy.schedule_events(schedule, "real", "daily")
    trade_dates = pd.DatetimeIndex(ic["date"])
    active: proxy.RealPosition | None = None
    latest_fraction = 0.0
    latest_eval: pd.Timestamp | None = None
    pending_roll_since: pd.Timestamp | None = None
    pending_action_since: pd.Timestamp | None = None
    rows: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []

    for row in daily.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        if event is not None:
            new_fraction = float(event.three_tier_target_fraction)
            if not math.isclose(new_fraction, latest_fraction, abs_tol=1e-12):
                pending_action_since = pending_action_since or day
            latest_fraction = new_fraction
            latest_eval = pd.Timestamp(event.eval_date)
        if day in roll_dates and active is not None and latest_fraction > 0:
            pending_roll_since = pending_roll_since or day
        if active is None and latest_fraction > 0:
            pending_action_since = pending_action_since or day
        if active is not None and (
            latest_fraction == 0
            or not math.isclose(latest_fraction, active.fraction, abs_tol=1e-12)
        ):
            pending_action_since = pending_action_since or day

        denominator = float(row.prior_settle) * 200.0
        etf_row = etf.loc[day]
        etf_open, etf_close = float(etf_row["open"]), float(etf_row["close"])
        pnl = 0.0
        cost = 0.0
        stale_days = 0
        carried = False
        action = ""
        old = active
        request_date = pending_roll_since or pending_action_since
        same_month_reset = False

        if active is not None and latest_fraction == 0:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            if quote is not None and float(quote["open"]) > 0 and float(quote["volume"]) > 0:
                pnl += active.qty * 10000.0 * (float(quote["open"]) - active.prior_mark) / denominator
                cost += active.fraction * proxy.PUT_FULL_SIDE_COST
                active = None
                action = "open_exit"
                pending_action_since = None
                pending_roll_since = None
        elif active is not None and pending_roll_since is not None:
            month = desired_real_month(snapshots, day, tenor, trade_dates)
            selected = (
                proxy.select_real_contract(snapshots, history_lookup, day, month)
                if month is not None
                else None
            )
            old_quote = proxy.history_exact(history_lookup, active.security_id, day)
            if (
                selected is not None
                and old_quote is not None
                and float(old_quote["open"]) > 0
                and float(old_quote["volume"]) > 0
            ):
                master, new_quote = selected
                qty, full_qty = _real_target_qty(row, etf_open, latest_fraction)
                pnl += active.qty * 10000.0 * (float(old_quote["open"]) - active.prior_mark) / denominator
                pnl += qty * 10000.0 * (float(new_quote["close"]) - float(new_quote["open"])) / denominator
                cost += (active.fraction + latest_fraction) * proxy.PUT_FULL_SIDE_COST
                same_month_reset = pd.Timestamp(master["contract_month"]) == active.contract_month
                active = proxy.RealPosition(
                    str(master["security_id"]),
                    str(master["contract_id"]),
                    pd.Timestamp(master["contract_month"]),
                    proxy.fourth_wednesday(pd.Timestamp(master["contract_month"]), trade_dates),
                    float(master["strike"]),
                    qty,
                    full_qty,
                    latest_fraction,
                    float(new_quote["close"]),
                    float(master["strike"]) / etf_open,
                )
                action = "open_roll_monthly"
                pending_roll_since = None
                pending_action_since = None
        elif active is None and latest_fraction > 0:
            month = desired_real_month(snapshots, day, tenor, trade_dates)
            selected = (
                proxy.select_real_contract(snapshots, history_lookup, day, month)
                if month is not None
                else None
            )
            if selected is not None:
                master, quote = selected
                qty, full_qty = _real_target_qty(row, etf_open, latest_fraction)
                pnl += qty * 10000.0 * (float(quote["close"]) - float(quote["open"])) / denominator
                cost += latest_fraction * proxy.PUT_FULL_SIDE_COST
                active = proxy.RealPosition(
                    str(master["security_id"]),
                    str(master["contract_id"]),
                    pd.Timestamp(master["contract_month"]),
                    proxy.fourth_wednesday(pd.Timestamp(master["contract_month"]), trade_dates),
                    float(master["strike"]),
                    qty,
                    full_qty,
                    latest_fraction,
                    float(quote["close"]),
                    float(master["strike"]) / etf_open,
                )
                action = "open_buy"
                pending_action_since = None
        elif active is not None and pending_action_since is not None:
            quote = proxy.history_exact(history_lookup, active.security_id, day)
            if quote is not None and float(quote["open"]) > 0 and float(quote["volume"]) > 0:
                open_price, close_price = float(quote["open"]), float(quote["close"])
                pnl += active.qty * 10000.0 * (open_price - active.prior_mark) / denominator
                new_qty, full_qty = _real_target_qty(row, etf_open, latest_fraction)
                pnl += new_qty * 10000.0 * (close_price - open_price) / denominator
                cost += abs(latest_fraction - active.fraction) * proxy.PUT_FULL_SIDE_COST
                active.qty = new_qty
                active.full_qty = full_qty
                active.fraction = latest_fraction
                active.prior_mark = close_price
                action = "open_resize"
                pending_action_since = None

        if not action and active is not None:
            mark, stale_days, carried = proxy.real_mark(history_groups, active, day, etf_close)
            pnl += active.qty * 10000.0 * (mark - active.prior_mark) / denominator
            active.prior_mark = mark

        if action:
            actual_request = request_date or day
            trades.append(
                {
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": actual_request,
                    "actual_execution_date": day,
                    "action": action,
                    "target_fraction": latest_fraction,
                    "old_contract": old.contract_id if old else "",
                    "new_contract": active.contract_id if active else "",
                    "old_month": old.contract_month if old else pd.NaT,
                    "new_month": active.contract_month if active else pd.NaT,
                    "new_strike": active.strike if active else np.nan,
                    "new_entry_moneyness": active.entry_moneyness if active else np.nan,
                    "desired_month": active.contract_month if active else pd.NaT,
                    "delay_days": int((day - actual_request).days),
                    "delay_trading_days": _trading_delay(trade_dates, actual_request, day),
                    "forced_month_roll": action == "open_roll_monthly",
                    "roll_request_date": actual_request if action == "open_roll_monthly" else pd.NaT,
                    "same_month_reset": same_month_reset,
                }
            )

        expired = False
        if active is not None and active.expiry == day:
            expired = True
            active = None
            pending_action_since = day
        mark_fraction = 0.0
        contract = ""
        qty = 0
        entry_moneyness = np.nan
        if active is not None:
            mark_fraction = active.qty * 10000.0 * active.prior_mark / (float(row.settle) * 200.0)
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
                "target_fraction": latest_fraction,
                "entry_moneyness_mark": entry_moneyness,
                "carried_mark": carried,
                "mark_stale_days": stale_days,
                "deferred_adjustment": bool(pending_action_since is not None or pending_roll_since is not None),
                "expired": expired,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades)


def build_roll_audit(
    trades: pd.DataFrame, roll_dates: set[pd.Timestamp]
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer, start in [("model", core.MODEL_START), ("real", core.REAL_START)]:
        applicable = sorted(day for day in roll_dates if start <= day <= core.END)
        for tenor in MONTHLY_TENORS:
            for signal in ["always_50", "always_100"]:
                candidate = f"{layer}_{tenor}_{signal}"
                subset = trades[
                    trades["candidate"].eq(candidate)
                    & trades["action"].eq("open_roll_monthly")
                ].copy()
                completed = set(pd.to_datetime(subset["roll_request_date"]))
                missing = [day for day in applicable if day not in completed]
                rows.append(
                    {
                        "layer": layer,
                        "tenor": tenor,
                        "signal_variant": signal,
                        "candidate": candidate,
                        "expected_rolls": len(applicable),
                        "completed_rolls": len(completed),
                        "completion_ratio": len(completed) / len(applicable),
                        "missing_rolls": len(missing),
                        "deferred_rolls": int(subset["delay_trading_days"].gt(0).sum()),
                        "max_delay_trading_days": int(subset["delay_trading_days"].max()) if len(subset) else 0,
                        "same_month_resets": int(subset["same_month_reset"].fillna(False).sum()) if len(subset) else 0,
                        "passed": bool(
                            (layer == "model" and not missing)
                            or (
                                layer == "real"
                                and len(completed) / len(applicable) >= 0.90
                                and (int(subset["delay_trading_days"].max()) if len(subset) else 999) <= 5
                            )
                        ),
                    }
                )
    return pd.DataFrame(rows)


def front_parity(daily: pd.DataFrame) -> pd.DataFrame:
    prior = pd.read_csv(v4.OUTPUT / "daily_candidates.csv.gz", parse_dates=["date"])
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        for signal in v4.ALL_VARIANTS:
            current_label = f"{layer}_front_original_{signal}" if signal != "no_put" else f"{layer}_no_put"
            prior_label = f"{layer}_{signal}"
            left = daily[daily["candidate"].eq(current_label)]
            right = prior[prior["candidate"].eq(prior_label)]
            joined = left.merge(right, on="date", suffixes=("_v6", "_v4"), validate="one_to_one")
            row = {"layer": layer, "signal_variant": signal, "rows": len(joined)}
            for column in ["put_pnl_ret", "put_cost_rate", "target_fraction", "ret", "cash_ret"]:
                row[f"max_abs_{column}_diff"] = float(
                    (joined[f"{column}_v6"] - joined[f"{column}_v4"]).abs().max()
                )
            rows.append(row)
    table = pd.DataFrame(rows)
    numeric = [column for column in table if column.startswith("max_abs_")]
    if table[numeric].to_numpy().max() > 1e-14:
        raise RuntimeError("v4 front parity failed")
    return table


def decision_outputs(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    signal_stability: pd.DataFrame,
    roll_audit: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model = formal[formal["layer"].eq("model")]
    base = model[model["signal_variant"].eq("no_put")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    rows: list[dict[str, object]] = []
    for tenor in TENORS:
        for signal in v4.ECON_VARIANTS:
            metrics = model[
                model["tenor"].eq(tenor) & model["signal_variant"].eq(signal)
            ].set_index("segment")
            dev_cagr = float(metrics.loc["development", "cash_ann_return"] - base.loc["development", "cash_ann_return"])
            dev_dd = float(metrics.loc["development", "cash_max_dd"] - base.loc["development", "cash_max_dd"])
            hold_cagr = float(metrics.loc["holdout", "cash_ann_return"] - base.loc["holdout", "cash_ann_return"])
            hold_dd = float(metrics.loc["holdout", "cash_max_dd"] - base.loc["holdout", "cash_max_dd"])
            window_cagr = {
                segment: float(metrics.loc[segment, "cash_ann_return"] - base.loc[segment, "cash_ann_return"])
                for segment in REQUIRED_SEGMENTS
            }
            window_dd = {
                segment: float(metrics.loc[segment, "cash_max_dd"] - base.loc[segment, "cash_max_dd"])
                for segment in REQUIRED_SEGMENTS
            }
            return_pass = all(
                window_cagr[segment] >= (-0.01 if segment in {"full", "last_10y", "last_5y"} else -0.03)
                for segment in REQUIRED_SEGMENTS
            )
            improved = sum(value > 1e-12 for value in window_dd.values())
            model_days = int(exposure_lookup.loc[f"model_{tenor}_{signal}", "protected_days"])
            real_days = int(exposure_lookup.loc[f"real_{tenor}_{signal}", "protected_days"])
            single = bool(
                dev_dd >= 0.03
                and dev_cagr >= -0.01
                and hold_dd >= 0.03
                and hold_cagr >= -0.01
                and improved >= 3
                and return_pass
                and model_days >= 20
                and real_days >= 20
            )
            rows.append(
                {
                    "tenor": tenor,
                    "signal_variant": signal,
                    **v4.variant_parameters(signal),
                    "development_cagr_delta": dev_cagr,
                    "development_dd_improvement": dev_dd,
                    "holdout_cagr_delta": hold_cagr,
                    "holdout_dd_improvement": hold_dd,
                    "improved_required_windows": improved,
                    "return_tolerance_pass": return_pass,
                    "model_protected_days": model_days,
                    "real_protected_days": real_days,
                    "single_candidate_pass": single,
                }
            )
    decisions = pd.DataFrame(rows)
    lookup = decisions.set_index(["tenor", "signal_variant"])["single_candidate_pass"].to_dict()
    audit_pass = {
        tenor: True
        if tenor == "front_original"
        else bool(roll_audit[roll_audit["tenor"].eq(tenor)]["passed"].all())
        for tenor in TENORS
    }
    support: list[dict[str, object]] = []
    for row in decisions.itertuples(index=False):
        tenor = row.tenor
        signal = row.signal_variant
        tenor_index = TENORS.index(tenor)
        tenor_neighbors = []
        if tenor_index > 0:
            tenor_neighbors.append(TENORS[tenor_index - 1])
        if tenor_index < len(TENORS) - 1:
            tenor_neighbors.append(TENORS[tenor_index + 1])
        tenor_support = any(lookup.get((neighbor, signal), False) for neighbor in tenor_neighbors)

        params = v4.variant_parameters(signal)
        months = int(params["window_months"])
        lower, upper = float(params["lower_risk"]), float(params["full_risk"])
        window_support = any(
            lookup.get(
                (
                    tenor,
                    f"econ_m{neighbor:02d}_l{int(lower*100):02d}_h{int(upper*100):02d}",
                ),
                False,
            )
            for neighbor in v4.HISTORY_MONTHS
            if abs(neighbor - months) == 6
        )
        mapping_index = v4.MAPPINGS.index((lower, upper))
        mapping_neighbors = []
        if mapping_index > 0:
            mapping_neighbors.append(v4.MAPPINGS[mapping_index - 1])
        if mapping_index < len(v4.MAPPINGS) - 1:
            mapping_neighbors.append(v4.MAPPINGS[mapping_index + 1])
        mapping_support = any(
            lookup.get(
                (
                    tenor,
                    f"econ_m{months:02d}_l{int(item[0]*100):02d}_h{int(item[1]*100):02d}",
                ),
                False,
            )
            for item in mapping_neighbors
        )
        active_support = False
        if window_support:
            pairs = signal_stability[
                signal_stability["left_variant"].eq(signal)
                | signal_stability["right_variant"].eq(signal)
            ]
            active_support = bool(
                (
                    (pairs["protected_day_jaccard"] >= 0.60)
                    & (pairs["active_target_mae"] <= 0.20)
                ).any()
            )
        support.append(
            {
                "tenor": tenor,
                "signal_variant": signal,
                "tenor_neighbor_pass": tenor_support,
                "window_neighbor_pass": window_support,
                "mapping_neighbor_pass": mapping_support,
                "active_signal_stability_pass": active_support,
                "monthly_roll_audit_pass": audit_pass[tenor],
                "all_preregistered_pass": bool(
                    row.single_candidate_pass
                    and tenor_support
                    and window_support
                    and mapping_support
                    and active_support
                    and audit_pass[tenor]
                ),
            }
        )
    decisions = decisions.merge(pd.DataFrame(support), on=["tenor", "signal_variant"], validate="one_to_one")
    passed = decisions[decisions["all_preregistered_pass"]].copy()
    if not bool(roll_audit["passed"].all()):
        summary = {
            "decision": "rerun_required",
            "stability_label": "data_sensitive",
            "selected_variant": None,
            "passing_candidates": [],
            "sample_reuse": "not_independent_oos",
            "failed_roll_audits": roll_audit.loc[
                ~roll_audit["passed"], "candidate"
            ].tolist(),
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
        all_passed = passed.copy()
        preferred_signal = "econ_m90_l50_h90"
        passed["tenor_neighbor_count"] = passed.apply(
            lambda row: sum(
                bool(lookup.get((neighbor, row["signal_variant"]), False))
                for neighbor in TENORS
                if neighbor != row["tenor"]
                and abs(TENORS.index(neighbor) - TENORS.index(row["tenor"])) == 1
            ),
            axis=1,
        )
        best_neighbors = int(passed["tenor_neighbor_count"].max())
        passed = passed[passed["tenor_neighbor_count"].eq(best_neighbors)].copy()
        best_dd = float(passed["holdout_dd_improvement"].max())
        passed = passed[
            passed["holdout_dd_improvement"] >= best_dd - 0.001
        ].copy()
        passed["tenor_rank"] = passed["tenor"].map(
            {"2m_monthly": 0, "front_original": 1, "3m_monthly": 2}
        )
        passed["central_signal_rank"] = passed["signal_variant"].ne(preferred_signal).astype(int)
        passed["average_protection"] = passed.apply(
            lambda row: float(
                exposure_lookup.loc[
                    f"model_{row['tenor']}_{row['signal_variant']}",
                    "average_target_fraction",
                ]
            ),
            axis=1,
        )
        selected = passed.sort_values(
            ["tenor_rank", "central_signal_rank", "average_protection"],
            ascending=[True, True, True],
        ).iloc[0]
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if len(all_passed) >= 4 else "narrow_stable",
            "selected_variant": f"{selected['tenor']}_{selected['signal_variant']}",
            "passing_candidates": [
                f"{row.tenor}_{row.signal_variant}"
                for row in all_passed.itertuples(index=False)
            ],
            "sample_reuse": "not_independent_oos",
        }
    return decisions, summary


def tenor_comparison(formal: pd.DataFrame, exposure: pd.DataFrame) -> pd.DataFrame:
    metrics = formal.merge(
        exposure[
            [
                "candidate",
                "put_cost_sum",
                "trade_events",
                "average_put_mark_fraction",
                "average_entry_moneyness",
            ]
        ],
        on="candidate",
        how="left",
        validate="many_to_one",
    )
    return metrics[
        metrics["signal_variant"].isin(v4.VARIANTS)
    ][
        [
            "candidate",
            "signal_variant",
            "tenor",
            "segment",
            "cash_ann_return",
            "cash_max_dd",
            "put_cost_sum",
            "trade_events",
            "average_put_mark_fraction",
            "average_entry_moneyness",
        ]
    ].reset_index(drop=True)


def period_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "2015_event": (pd.Timestamp("2015-01-01"), pd.Timestamp("2015-12-31")),
        "2025_2026_drag": (pd.Timestamp("2025-01-01"), core.END),
    }
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        baseline = daily[daily["candidate"].eq(f"{layer}_no_put")][
            ["date", "cash_ret"]
        ].rename(columns={"cash_ret": "baseline_cash_ret"})
        for tenor in TENORS:
            for signal in v4.VARIANTS:
                candidate = f"{layer}_{tenor}_{signal}"
                path = daily[daily["candidate"].eq(candidate)][
                    ["date", "cash_ret", "put_pnl_ret", "put_cost_rate"]
                ]
                joined = path.merge(baseline, on="date", validate="one_to_one")
                for period, (start, end) in periods.items():
                    sample = joined[joined["date"].between(start, end)].copy()
                    available = not sample.empty and pd.Timestamp(sample["date"].min()) <= start
                    relative_log = (
                        np.log1p(sample["cash_ret"])
                        - np.log1p(sample["baseline_cash_ret"])
                    )
                    rows.append(
                        {
                            "candidate": candidate,
                            **candidate_parts(candidate),
                            "period": period,
                            "available": available,
                            "actual_start": sample["date"].min() if len(sample) else pd.NaT,
                            "actual_end": sample["date"].max() if len(sample) else pd.NaT,
                            "rows": len(sample),
                            "candidate_total_return": (
                                float((1.0 + sample["cash_ret"]).prod() - 1.0)
                                if len(sample)
                                else np.nan
                            ),
                            "baseline_total_return": (
                                float((1.0 + sample["baseline_cash_ret"]).prod() - 1.0)
                                if len(sample)
                                else np.nan
                            ),
                            "relative_log_contribution": (
                                float(relative_log.sum()) if len(sample) else np.nan
                            ),
                            "relative_terminal_return": (
                                float(np.expm1(relative_log.sum())) if len(sample) else np.nan
                            ),
                            "put_pnl_ret_sum": (
                                float(sample["put_pnl_ret"].sum()) if len(sample) else np.nan
                            ),
                            "put_cost_rate_sum": (
                                float(sample["put_cost_rate"].sum()) if len(sample) else np.nan
                            ),
                        }
                    )
    return pd.DataFrame(rows)


def build_record(
    formal: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, object],
    roll_audit: pd.DataFrame,
) -> str:
    selected_signals = ["no_put", "always_100", "econ_m90_l50_h90"]
    table = formal[
        formal["layer"].eq("model")
        & formal["signal_variant"].isin(selected_signals)
        & formal["segment"].isin(REQUIRED_SEGMENTS)
        & formal["available"].eq(True)
    ][["signal_variant", "tenor", "segment", "cash_ann_return", "cash_max_dd"]]
    lines = [
        "# IC + 510500 Put v4信号强制月滚期限复核 v6",
        "",
        "> 研究回测；未获准实盘；全部历史均已被前版观察。",
        "",
        "## 决定",
        "",
        f"- 决定：`{summary['decision']}`。",
        f"- 稳定性：`{summary['stability_label']}`。",
        f"- 观察线：`{summary['selected_variant']}`。",
        f"- 月滚审计总通过：`{bool(roll_audit['passed'].all())}`。",
        "",
        "## 中央v4信号期限结果",
        "",
        table.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 预注册判断",
        "",
        decisions[
            [
                "tenor",
                "signal_variant",
                "development_dd_improvement",
                "holdout_dd_improvement",
                "improved_required_windows",
                "all_preregistered_pass",
            ]
        ].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 月滚审计",
        "",
        roll_audit.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 限制",
        "",
        "- front_original逐日复现v4；2m/3m在IC到期换月日开盘强制重置。",
        "- IC基线同日按结算换月，Put按开盘换月，存在时点差异。",
        "- 2015—2022模型Put不是历史成交；真实层为第三方日线。",
        "- 当前状态和所有结果均不是订单。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    v4_manifest = verify_inputs()
    configure_metrics()
    frames = core.v2.load_inputs()
    daily_valuation, valuation_checks = core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, window_stability, mapping_stability, current = v4.build_signal_panel(
        frames["ic"], daily_valuation, frames["states_full"]
    )
    market, market_checks = proxy.prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = proxy.qvix_validation(market, frames["q500"])
    roll_dates = forced_roll_dates(frames["ic"])

    daily_parts: list[pd.DataFrame] = [
        proxy.no_put_rows(frames["ic"], core.MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], core.REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    for tenor in TENORS:
        for signal in v4.VARIANTS:
            model_schedule = schedule[
                schedule["layer"].eq("model") & schedule["signal_variant"].eq(signal)
            ]
            real_schedule = schedule[
                schedule["layer"].eq("real") & schedule["signal_variant"].eq(signal)
            ]
            model_label = f"model_{tenor}_{signal}"
            real_label = f"real_{tenor}_{signal}"
            if tenor == "front_original":
                overlay, trades = proxy.run_model_candidate(
                    frames["ic"], model_schedule, market, "daily", "front", "three_tier", 0.85, model_label
                )
                daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
                if not trades.empty:
                    trade_parts.append(trades)
                overlay, trades = proxy.run_real_candidate(
                    frames["ic"], real_schedule, frames["snapshots"], frames["histories"],
                    frames["etf500"], "daily", "front", "three_tier", real_label
                )
            else:
                overlay, trades = run_model_monthly_tenor(
                    frames["ic"], model_schedule, market, tenor, model_label, roll_dates
                )
                daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
                if not trades.empty:
                    trade_parts.append(trades)
                overlay, trades = run_real_monthly_tenor(
                    frames["ic"], real_schedule, frames["snapshots"], frames["histories"],
                    frames["etf500"], tenor, real_label, roll_dates
                )
            daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
            if not trades.empty:
                trade_parts.append(trades)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"]).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False) if trade_parts else pd.DataFrame()
    parity_table = front_parity(daily)
    formal, scan_summary, wide = core.metric_outputs(daily)
    annual = core.annual_metrics(daily)
    exposure = core.v2.exposure_summary(daily, trades)
    cross_table, cross_stats = core.real_model_validation(daily)
    concentration = core.event_concentration(daily)
    roll_audit = build_roll_audit(trades, roll_dates)
    decisions, decision_summary = decision_outputs(formal, exposure, window_stability, roll_audit)
    tenor_table = tenor_comparison(formal, exposure)
    attribution = period_attribution(daily)

    expected = {f"{layer}_{variant}" for layer in ["model", "real"] for variant in ALL_GRID_VARIANTS}
    if set(daily["candidate"]) != expected:
        raise RuntimeError("v6 candidate set mismatch")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate v6 candidate date")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid v6 daily return")
    if not qvix_stats["passed"]:
        raise RuntimeError("QVIX proxy validation failed")
    if (trades["actual_execution_date"] < trades["scheduled_execution_date"]).any():
        raise RuntimeError("Trade execution precedes its scheduled execution date")
    permanent = exposure[
        exposure["signal_variant"].isin(["always_50", "always_100"])
    ]
    if (permanent["trade_events"] <= 0).any() or (
        permanent["average_put_mark_fraction"] <= 0
    ).any():
        raise RuntimeError("Permanent Put engine benchmark is empty")
    for layer in ["model", "real"]:
        for tenor in TENORS:
            for signal, target in [("always_50", 0.5), ("always_100", 1.0)]:
                observed = exposure.loc[
                    exposure["candidate"].eq(f"{layer}_{tenor}_{signal}"),
                    "average_target_fraction",
                ].item()
                if not math.isclose(observed, target, abs_tol=1e-12):
                    raise RuntimeError(
                        f"Permanent target mismatch: {layer}_{tenor}_{signal}"
                    )

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(OUTPUT / "v4_evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "v4_valuation_signals.csv.gz", index=False, compression="gzip")
    current.to_csv(OUTPUT / "current_research_signals.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_segment.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    parity_table.to_csv(OUTPUT / "v4_front_parity.csv", index=False)
    roll_audit.to_csv(OUTPUT / "monthly_roll_audit.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    tenor_table.to_csv(OUTPUT / "tenor_comparison.csv", index=False)
    attribution.to_csv(OUTPUT / "period_attribution.csv", index=False)
    window_stability.to_csv(OUTPUT / "v4_window_signal_stability.csv", index=False)
    mapping_stability.to_csv(OUTPUT / "v4_mapping_signal_stability.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "record.md").write_text(
        build_record(formal, decisions, decision_summary, roll_audit), encoding="utf-8"
    )

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "candidate_count": len(expected),
        "sample": v4_manifest["sample"],
        "valuation_checks": valuation_checks,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "front_parity_max_abs": float(
            parity_table[[column for column in parity_table if column.startswith("max_abs_")]].to_numpy().max()
        ),
        "roll_audit_pass": bool(roll_audit["passed"].all()),
        "decision_summary": decision_summary,
        "dependencies": {
            "v4_signal": {"path": str(V4_PATH.relative_to(ROOT)), "sha256": V4_SHA256},
            "v3_metrics": {"path": str(V3_PATH.relative_to(ROOT)), "sha256": V3_SHA256},
            "proxy_engine": {"path": str(PROXY_PATH.relative_to(ROOT)), "sha256": PROXY_SHA256},
        },
        "source_hashes": v4_manifest["source_hashes"],
        "git_status": core.git_status(),
        "warnings": [
            "All history was observed before v6 and is not independent OOS.",
            "IC rolls at settlement while forced Put rolls are modeled at same-date open.",
            "Model Put is theoretical; actual option daily bars are not executable quote proof.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    commands = (
        "python.exe -m pytest test_ic_510500_put_v4_monthly_tenor_rerun_v6.py -q\n"
        "python.exe ic_510500_put_v4_monthly_tenor_rerun_v6.py\n"
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
            "front_parity_max_abs": manifest["front_parity_max_abs"],
            "roll_audit_pass": manifest["roll_audit_pass"],
            "qvix_proxy": qvix_stats,
            "decision_summary": decision_summary,
            "formal_output": str(OUTPUT.relative_to(ROOT)),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "front_parity": manifest["front_parity_max_abs"],
                "roll_audit_pass": manifest["roll_audit_pass"],
                "qvix_passed": qvix_stats["passed"],
                **decision_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
