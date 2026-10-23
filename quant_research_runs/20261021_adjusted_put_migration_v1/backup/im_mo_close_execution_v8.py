from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import im_mo_csi1000_put_protection_battery_v6 as v6
import im_mo_front95_fixed_dynamic_momentum_validation_v5 as v5
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_close_execution_v8"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "7470d2bf37bff3138b90d0bc3def33808e7c5284bce4f8db9c2c83034f83c054"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260817_1000_put_im_mo_close_execution_v8_fixed175_or_mom120_put_tool_grid_execution_close_tenor_moneyness"
)
V6_DAILY = ROOT / "outputs" / "im_mo_csi1000_put_protection_battery_v6" / "daily_candidates.csv.gz"
V7_RECORD = ROOT / "outputs" / "im_mo_csi1000_put_protection_battery_audit_v7" / "record.md"

V6_SHA256 = "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d"
V4_SHA256 = "c654aa7c30c4a89954f8c7db7d352664ab3ac0c5455c2b26248c5aca75476461"
V5_SHA256 = "b4e77f6f1691f18dba7a517f4f024bb2eec9e8feadb46782e74a0ac63b18ab4b"
V6_DAILY_SHA256 = "5d97d68213ded9595880b10f8b52b6e179bdbf3e156a02424435f9664d62a8cc"
V7_RECORD_SHA256 = "cf8e16a602f5142485882489bd55eaf13811606b6202247aa78a1063a874754e"
MO_SHA256 = "cf7be9a5218c361961641c6e6a05745d581f87875ac67702fa95d3f4dbe71596"
IM_SHA256 = "6f19f04824026e3cf7e4fc7ebfeb20f60637e53bfc3caebc616fae47794f3cc0"

STRUCTURES = ["front_exit", "2m_monthly_exit", "3m_monthly_exit", "3cycle_hold_expiry"]
MONEYNESS = [0.85, 0.90, 0.95]
WINDOWS = v6.WINDOWS
PUT_SIDE_COST = v6.PUT_SIDE_COST
REAL_START = v6.REAL_START
MODEL_START = v6.MODEL_START
END = v6.END


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def verify_inputs() -> None:
    expected = [
        (SPEC, SPEC_SHA256),
        (Path(v6.__file__), V6_SHA256),
        (Path(v4.__file__), V4_SHA256),
        (Path(v5.__file__), V5_SHA256),
        (V6_DAILY, V6_DAILY_SHA256),
        (V7_RECORD, V7_RECORD_SHA256),
        (v4.OPTIONS, MO_SHA256),
        (v5.IM_QUOTES, IM_SHA256),
    ]
    for path, wanted in expected:
        if sha256(Path(path).resolve()) != wanted:
            raise RuntimeError(f"Frozen input mismatch: {Path(path).name}")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Specification sidecar mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized parameter-scan folder is missing")


def candidate_definitions() -> pd.DataFrame:
    rows = []
    for structure in STRUCTURES:
        tenor = {
            "front_exit": "front",
            "2m_monthly_exit": "2m",
            "3m_monthly_exit": "3m",
            "3cycle_hold_expiry": "3cycle",
        }[structure]
        for moneyness in MONEYNESS:
            rows.append(
                {
                    "candidate": f"fixed175_or_mom120_{structure}_m{int(moneyness * 100)}",
                    "group": "close_tool_grid",
                    "signal": "fixed175_or_mom120",
                    "frequency": "fixed175_or_mom120",
                    "tier": "binary",
                    "structure": structure,
                    "tenor": tenor,
                    "moneyness": moneyness,
                    "execution": "t_plus_1_close",
                }
            )
    return pd.DataFrame(rows)


def active_im_closes(upstream: pd.DataFrame) -> pd.DataFrame:
    quotes = pd.read_csv(v5.IM_QUOTES, parse_dates=["date"])[
        ["contract", "date", "close", "volume"]
    ]
    active = upstream[["date", "contract"]].merge(
        quotes, on=["date", "contract"], how="left", validate="one_to_one"
    )
    if active[["close", "volume"]].isna().any().any() or active["close"].le(0).any():
        raise RuntimeError("Missing active IM closing quote")
    return active


def select_close_contract(
    options: pd.DataFrame,
    im_close: pd.Series,
    day: pd.Timestamp,
    month: pd.Timestamp,
    moneyness: float,
) -> pd.Series | None:
    chain = options[(options["date"] == day) & (options["contract_month"] == month)].copy()
    if chain.empty:
        return None
    liquid = chain[
        chain["close"].notna()
        & chain["close"].gt(0)
        & chain["volume"].gt(0)
        & chain["open_interest"].gt(0)
    ].copy()
    if liquid.empty:
        return None
    liquid["entry_moneyness"] = liquid["strike"] / float(im_close.loc[day])
    liquid["target_error"] = (liquid["entry_moneyness"] - moneyness).abs().round(12)
    selected = liquid.sort_values(["target_error", "strike", "contract"]).iloc[0].copy()
    selected["literal_min_strike"] = float(chain["strike"].min())
    return selected


def executable_close(row: pd.Series) -> bool:
    return bool(pd.notna(row["close"]) and float(row["close"]) > 0 and float(row["volume"]) > 0)


@dataclass
class RealPosition:
    contract: str
    contract_month: pd.Timestamp
    actual_expiry: pd.Timestamp
    qty: int
    prior_settle: float
    entry_date: pd.Timestamp


def run_real_normal_close(
    upstream: pd.DataFrame,
    options: pd.DataFrame,
    active_im: pd.DataFrame,
    schedule: pd.DataFrame,
    tenor: str,
    moneyness: float,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    lookup = options.set_index(["contract", "date"])
    im_close = active_im.set_index("date")["close"]
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    active: RealPosition | None = None
    latest_target = 0
    latest_eval: pd.Timestamp | None = None
    pending = False
    pending_since: pd.Timestamp | None = None
    maintenance = False
    rows, trades, lives = [], [], []

    for idx, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(base["settle"] if idx == 0 else upstream.loc[idx - 1, "settle"])
        event = events.get(day)
        if event is not None:
            latest_target = int(event.binary_target_qty)
            latest_eval = pd.Timestamp(event.eval_date)
            pending = True
            pending_since = day
            maintenance = False
        if active is None and latest_target > 0 and not pending:
            pending = True
            pending_since = day
            maintenance = True

        equivalent_points = 0.0
        buy_qty = sell_qty = 0
        action = ""
        old_contract = active.contract if active else ""
        old_qty = active.qty if active else 0
        old_trade_price = np.nan
        new_trade_price = np.nan
        new_row: pd.Series | None = None
        target_date: pd.Timestamp | None = None
        desired_month: pd.Timestamp | None = None

        if pending:
            if latest_target > 0:
                if latest_eval is None:
                    raise RuntimeError("Positive target without evaluation date")
                target_date = v4.tenor_target_date(tenor, latest_eval, day, maintenance)
                desired_month = v4.selected_month(options, day, target_date)
                if active is not None and active.contract_month == desired_month:
                    new_row = v4.option_row(lookup, active.contract, day)
                else:
                    new_row = select_close_contract(options, im_close, day, desired_month, moneyness)

            if active is None:
                if latest_target == 0:
                    pending = False
                elif new_row is not None:
                    entry = float(new_row["close"])
                    equivalent_points += latest_target * 0.5 * (float(new_row["settle"]) - entry)
                    buy_qty = latest_target
                    new_trade_price = entry
                    active = RealPosition(
                        str(new_row["contract"]),
                        pd.Timestamp(new_row["contract_month"]),
                        pd.Timestamp(new_row["actual_expiry"]),
                        latest_target,
                        float(new_row["settle"]),
                        day,
                    )
                    action = "close_buy"
                    pending = False
            elif latest_target == 0:
                old = v4.option_row(lookup, active.contract, day)
                if executable_close(old):
                    old_trade_price = float(old["close"])
                    equivalent_points += active.qty * 0.5 * (old_trade_price - active.prior_settle)
                    sell_qty = active.qty
                    lives.append(
                        {
                            "candidate": label,
                            "entry_date": active.entry_date,
                            "expiry": active.actual_expiry,
                            "exit_date": day,
                            "exit_reason": "signal_close",
                        }
                    )
                    active = None
                    action = "close_exit"
                    pending = False
            elif desired_month == active.contract_month:
                delta = latest_target - active.qty
                if delta == 0:
                    pending = False
                else:
                    old = v4.option_row(lookup, active.contract, day)
                    if executable_close(old) and float(old["open_interest"]) > 0:
                        old_trade_price = float(old["close"])
                        new_trade_price = old_trade_price
                        equivalent_points += active.qty * 0.5 * (
                            old_trade_price - active.prior_settle
                        )
                        equivalent_points += latest_target * 0.5 * (
                            float(old["settle"]) - new_trade_price
                        )
                        buy_qty = max(delta, 0)
                        sell_qty = max(-delta, 0)
                        active.qty = latest_target
                        active.prior_settle = float(old["settle"])
                        action = "close_increase" if delta > 0 else "close_reduce"
                        pending = False
            else:
                old = v4.option_row(lookup, active.contract, day)
                if executable_close(old) and new_row is not None:
                    old_trade_price = float(old["close"])
                    new_trade_price = float(new_row["close"])
                    equivalent_points += active.qty * 0.5 * (
                        old_trade_price - active.prior_settle
                    )
                    equivalent_points += latest_target * 0.5 * (
                        float(new_row["settle"]) - new_trade_price
                    )
                    sell_qty = active.qty
                    buy_qty = latest_target
                    lives.append(
                        {
                            "candidate": label,
                            "entry_date": active.entry_date,
                            "expiry": active.actual_expiry,
                            "exit_date": day,
                            "exit_reason": "roll_close",
                        }
                    )
                    active = RealPosition(
                        str(new_row["contract"]),
                        pd.Timestamp(new_row["contract_month"]),
                        pd.Timestamp(new_row["actual_expiry"]),
                        latest_target,
                        float(new_row["settle"]),
                        day,
                    )
                    action = "close_roll"
                    pending = False

        traded = bool(action)
        if not traded and active is not None:
            mark = v4.option_row(lookup, active.contract, day)
            equivalent_points += active.qty * 0.5 * (
                float(mark["settle"]) - active.prior_settle
            )
            active.prior_settle = float(mark["settle"])

        cost_rate = (buy_qty + sell_qty) * v4.MO_CONTRACT_SIDE_COST
        if traded:
            trades.append(
                {
                    "layer": "real",
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "scheduled_execution_date": pending_since,
                    "actual_execution_date": day,
                    "execution_timing": "close",
                    "action": action,
                    "target_qty": latest_target,
                    "target_fraction": latest_target / 2.0,
                    "old_contract": old_contract,
                    "old_qty": old_qty,
                    "old_trade_price": old_trade_price,
                    "new_contract": active.contract if active else "",
                    "new_qty": active.qty if active else 0,
                    "new_trade_price": new_trade_price,
                    "new_strike": float(new_row["strike"])
                    if new_row is not None and active is not None
                    else np.nan,
                    "entry_moneyness": float(new_row["strike"]) / float(im_close.loc[day])
                    if new_row is not None and active is not None
                    else np.nan,
                    "target_date": target_date,
                    "desired_contract_month": desired_month,
                    "buy_qty": buy_qty,
                    "sell_qty": sell_qty,
                    "new_volume": float(new_row["volume"])
                    if new_row is not None and active is not None
                    else np.nan,
                    "new_open_interest": float(new_row["open_interest"])
                    if new_row is not None and active is not None
                    else np.nan,
                }
            )

        if active is not None and active.actual_expiry == day:
            lives.append(
                {
                    "candidate": label,
                    "entry_date": active.entry_date,
                    "expiry": active.actual_expiry,
                    "exit_date": day,
                    "exit_reason": "expiry",
                }
            )
            active = None
            if latest_target > 0:
                pending = True
                pending_since = day + pd.Timedelta(days=1)
                maintenance = True

        mark_fraction = (
            0.0
            if active is None
            else active.qty * 0.5 * active.prior_settle / float(base["settle"])
        )
        rows.append(
            {
                "date": day,
                "put_pnl_ret": equivalent_points / denominator,
                "put_cost_rate": cost_rate,
                "put_mark_fraction": mark_fraction,
                "put_fraction": 0.0 if active is None else active.qty / 2.0,
                "put_contract": "" if active is None else active.contract,
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def run_model_normal_close(
    market: pd.DataFrame,
    schedule: pd.DataFrame,
    tenor: str,
    moneyness: float,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    dates = pd.DatetimeIndex(market["date"])
    active: v6.ModelPosition | None = None
    latest_qty = 0
    latest_eval: pd.Timestamp | None = None
    rows, trades, lives = [], [], []
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        event = events.get(day)
        action = ""
        pnl = cost = 0.0
        if event is not None:
            latest_qty = int(event.binary_target_qty)
            latest_eval = pd.Timestamp(event.eval_date)
        desired = None
        if latest_qty > 0:
            if latest_eval is None:
                raise RuntimeError("Positive theoretical target without evaluation")
            desired = v6.normal_target_month(tenor, day, latest_eval, dates)
        needs = (
            event is not None
            or (active is not None and active.expiry <= day)
            or (active is None and latest_qty > 0)
        )
        old = active
        old_price = new_price = np.nan
        if needs:
            if active is not None and latest_qty == 0:
                old_price = v6.option_price(active, row, "close")
                pnl += active.units * (old_price - active.prior_mark) / float(row.base_prior_close)
                cost += active.fraction * PUT_SIDE_COST
                lives.append(
                    {
                        "candidate": label,
                        "entry_date": active.entry_date,
                        "expiry": active.expiry,
                        "exit_date": day,
                        "exit_reason": "signal_close",
                    }
                )
                active = None
                action = "close_exit"
            elif latest_qty > 0 and active is None:
                expiry = v6.third_friday(desired, dates)
                fraction = latest_qty / 2.0
                units = float(row.base_prior_close) / float(row.spot_close) * fraction
                active = v6.ModelPosition(
                    desired,
                    expiry,
                    float(row.spot_close) * moneyness,
                    units,
                    fraction,
                    0.0,
                    day,
                )
                new_price = v6.option_price(active, row, "close")
                active.prior_mark = new_price
                cost += fraction * PUT_SIDE_COST
                action = "close_buy"
            elif latest_qty > 0 and active is not None and desired != active.month:
                old_price = v6.option_price(active, row, "close")
                pnl += active.units * (old_price - active.prior_mark) / float(row.base_prior_close)
                cost += active.fraction * PUT_SIDE_COST
                lives.append(
                    {
                        "candidate": label,
                        "entry_date": active.entry_date,
                        "expiry": active.expiry,
                        "exit_date": day,
                        "exit_reason": "roll_close",
                    }
                )
                fraction = latest_qty / 2.0
                expiry = v6.third_friday(desired, dates)
                active = v6.ModelPosition(
                    desired,
                    expiry,
                    float(row.spot_close) * moneyness,
                    float(row.base_prior_close) / float(row.spot_close) * fraction,
                    fraction,
                    0.0,
                    day,
                )
                new_price = v6.option_price(active, row, "close")
                active.prior_mark = new_price
                cost += fraction * PUT_SIDE_COST
                action = "close_roll"
            elif latest_qty > 0 and active is not None and not math.isclose(
                active.fraction, latest_qty / 2.0
            ):
                old_price = v6.option_price(active, row, "close")
                pnl += active.units * (old_price - active.prior_mark) / float(row.base_prior_close)
                old_fraction = active.fraction
                fraction = latest_qty / 2.0
                active.units = float(row.base_prior_close) / float(row.spot_close) * fraction
                active.fraction = fraction
                active.prior_mark = old_price
                new_price = old_price
                cost += abs(fraction - old_fraction) * PUT_SIDE_COST
                action = "close_resize"
        if not action and active is not None:
            close_mark = v6.option_price(active, row, "close")
            pnl += active.units * (close_mark - active.prior_mark) / float(row.base_prior_close)
            active.prior_mark = close_mark
        if action:
            trades.append(
                {
                    "layer": "model",
                    "candidate": label,
                    "signal_eval_date": latest_eval,
                    "actual_execution_date": day,
                    "execution_timing": "close",
                    "action": action,
                    "target_fraction": latest_qty / 2.0,
                    "old_contract": ""
                    if old is None
                    else f"MODEL_{old.month:%y%m}_{old.strike:.4f}",
                    "old_trade_price": old_price,
                    "new_contract": ""
                    if active is None
                    else f"MODEL_{active.month:%y%m}_{active.strike:.4f}",
                    "new_trade_price": new_price,
                    "entry_moneyness": moneyness if active is not None and action != "close_exit" else np.nan,
                }
            )
        mark_fraction = (
            0.0
            if active is None
            else active.units * active.prior_mark / float(row.tri_close)
        )
        rows.append(
            {
                "date": day,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": mark_fraction,
                "put_fraction": 0.0 if active is None else active.fraction,
                "put_contract": ""
                if active is None
                else f"MODEL_{active.month:%y%m}_{active.strike:.4f}",
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def run_real_hold_close(
    upstream: pd.DataFrame,
    options: pd.DataFrame,
    active_im: pd.DataFrame,
    schedule: pd.DataFrame,
    moneyness: float,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    lookup = options.set_index(["contract", "date"])
    im_close = active_im.set_index("date")["close"]
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    roll_dates = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].fillna("").ne(""), "date"])
    active: RealPosition | None = None
    latest_qty = 0
    latest_eval: pd.Timestamp | None = None
    rows, trades, lives = [], [], []
    pending_since: pd.Timestamp | None = None
    for idx, base in upstream.iterrows():
        day = pd.Timestamp(base["date"])
        if day in events:
            latest_qty = int(events[day].binary_target_qty)
            latest_eval = pd.Timestamp(events[day].eval_date)
        denominator = float(base["settle"] if idx == 0 else upstream.loc[idx - 1, "settle"])
        pnl = cost = 0.0
        if active is None and latest_qty > 0:
            if pending_since is None:
                pending_since = day
            future_rolls = roll_dates[roll_dates >= day]
            selected = None
            if len(future_rolls) >= 4:
                r3, r4 = pd.Timestamp(future_rolls[2]), pd.Timestamp(future_rolls[3])
                months = (
                    options.loc[
                        options["date"].eq(day)
                        & options["actual_expiry"].ge(r3)
                        & options["actual_expiry"].lt(r4),
                        ["contract_month", "actual_expiry"],
                    ]
                    .drop_duplicates()
                    .sort_values(["actual_expiry", "contract_month"])
                )
                for month in months["contract_month"]:
                    selected = select_close_contract(
                        options, im_close, day, pd.Timestamp(month), moneyness
                    )
                    if selected is not None:
                        break
            if selected is not None:
                entry = float(selected["close"])
                pnl += latest_qty * 0.5 * (float(selected["settle"]) - entry) / denominator
                active = RealPosition(
                    str(selected["contract"]),
                    pd.Timestamp(selected["contract_month"]),
                    pd.Timestamp(selected["actual_expiry"]),
                    latest_qty,
                    float(selected["settle"]),
                    day,
                )
                cost += latest_qty * v4.MO_CONTRACT_SIDE_COST
                trades.append(
                    {
                        "layer": "real",
                        "candidate": label,
                        "signal_eval_date": latest_eval,
                        "scheduled_execution_date": pending_since,
                        "actual_execution_date": day,
                        "execution_timing": "close",
                        "action": "close_buy_hold",
                        "target_fraction": latest_qty / 2.0,
                        "new_contract": active.contract,
                        "new_qty": active.qty,
                        "new_trade_price": entry,
                        "new_strike": float(selected["strike"]),
                        "entry_moneyness": float(selected["strike"]) / float(im_close.loc[day]),
                        "new_volume": float(selected["volume"]),
                        "new_open_interest": float(selected["open_interest"]),
                    }
                )
                pending_since = None
        elif active is not None:
            mark = v4.option_row(lookup, active.contract, day)
            pnl += active.qty * 0.5 * (float(mark["settle"]) - active.prior_settle) / denominator
            active.prior_settle = float(mark["settle"])

        expired = active is not None and active.actual_expiry == day
        mark_fraction = (
            0.0
            if active is None
            else active.qty * 0.5 * active.prior_settle / float(base["settle"])
        )
        rows.append(
            {
                "date": day,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": mark_fraction,
                "put_fraction": 0.0 if active is None else active.qty / 2.0,
                "put_contract": "" if active is None else active.contract,
            }
        )
        if expired:
            coverage = int(
                ((roll_dates >= active.entry_date) & (roll_dates <= active.actual_expiry)).sum()
            )
            lives.append(
                {
                    "candidate": label,
                    "entry_date": active.entry_date,
                    "expiry": active.actual_expiry,
                    "exit_date": day,
                    "exit_reason": "expiry",
                    "covered_rolls": coverage,
                }
            )
            active = None
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def run_model_hold_close(
    market: pd.DataFrame,
    schedule: pd.DataFrame,
    moneyness: float,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = {pd.Timestamp(row.execution_date): row for row in schedule.itertuples(index=False)}
    dates = pd.DatetimeIndex(market["date"])
    roll_dates = pd.DatetimeIndex(
        sorted(
            {
                v6.third_friday(pd.Timestamp(year, month, 1), dates)
                for year in range(2015, 2027)
                for month in range(1, 13)
            }
        )
    )
    active: v6.ModelPosition | None = None
    latest_qty = 0
    latest_eval: pd.Timestamp | None = None
    rows, trades, lives = [], [], []
    for row in market.itertuples(index=False):
        day = pd.Timestamp(row.date)
        if day in events:
            latest_qty = int(events[day].binary_target_qty)
            latest_eval = pd.Timestamp(events[day].eval_date)
        pnl = cost = 0.0
        if active is None and latest_qty > 0:
            months = [
                (month, v6.third_friday(month, dates))
                for month in v6.model_listed_months(day, dates)
            ]
            selected = v6.three_cycle_month(day, months, roll_dates)
            if selected is not None:
                month, expiry = selected
                fraction = latest_qty / 2.0
                active = v6.ModelPosition(
                    month,
                    expiry,
                    float(row.spot_close) * moneyness,
                    float(row.base_prior_close) / float(row.spot_close) * fraction,
                    fraction,
                    0.0,
                    day,
                )
                entry = v6.option_price(active, row, "close")
                active.prior_mark = entry
                cost += fraction * PUT_SIDE_COST
                trades.append(
                    {
                        "layer": "model",
                        "candidate": label,
                        "signal_eval_date": latest_eval,
                        "actual_execution_date": day,
                        "execution_timing": "close",
                        "action": "close_buy_hold",
                        "target_fraction": fraction,
                        "new_contract": f"MODEL_{month:%y%m}_{active.strike:.4f}",
                        "new_trade_price": entry,
                        "entry_moneyness": moneyness,
                    }
                )
        elif active is not None:
            mark = v6.option_price(active, row, "close")
            pnl += active.units * (mark - active.prior_mark) / float(row.base_prior_close)
            active.prior_mark = mark
        expired = active is not None and active.expiry == day
        rows.append(
            {
                "date": day,
                "put_pnl_ret": pnl,
                "put_cost_rate": cost,
                "put_mark_fraction": 0.0
                if active is None
                else active.units * active.prior_mark / float(row.tri_close),
                "put_fraction": 0.0 if active is None else active.fraction,
                "put_contract": ""
                if active is None
                else f"MODEL_{active.month:%y%m}_{active.strike:.4f}",
            }
        )
        if expired:
            coverage = int(
                ((roll_dates >= active.entry_date) & (roll_dates <= active.expiry)).sum()
            )
            lives.append(
                {
                    "candidate": label,
                    "entry_date": active.entry_date,
                    "expiry": active.expiry,
                    "exit_date": day,
                    "exit_reason": "expiry",
                    "covered_rolls": coverage,
                }
            )
            active = None
    return pd.DataFrame(rows), pd.DataFrame(trades), pd.DataFrame(lives)


def add_neighbor_decisions(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    definitions: pd.DataFrame,
    cross_stats: dict[str, object],
) -> tuple[pd.DataFrame, dict[str, object]]:
    base, _ = v6.decisions(formal, exposure, definitions, cross_stats)
    passed = base.set_index("candidate")["two_layer_base_pass"].to_dict()
    mapping = definitions.set_index(["structure", "moneyness"])["candidate"].to_dict()
    structure_index = {value: number for number, value in enumerate(STRUCTURES)}
    money_index = {value: number for number, value in enumerate(MONEYNESS)}
    money_support, tenor_support = [], []
    for row in definitions.itertuples(index=False):
        mi = money_index[row.moneyness]
        si = structure_index[row.structure]
        m_neighbors = [
            mapping[(row.structure, MONEYNESS[j])]
            for j in [mi - 1, mi + 1]
            if 0 <= j < len(MONEYNESS)
        ]
        t_neighbors = [
            mapping[(STRUCTURES[j], row.moneyness)]
            for j in [si - 1, si + 1]
            if 0 <= j < len(STRUCTURES)
        ]
        money_support.append(bool(passed[row.candidate] and any(passed[x] for x in m_neighbors)))
        tenor_support.append(bool(passed[row.candidate] and any(passed[x] for x in t_neighbors)))
    base["moneyness_neighbor_pass"] = money_support
    base["tenor_neighbor_pass"] = tenor_support
    base["fully_supported"] = (
        base["two_layer_base_pass"]
        & base["moneyness_neighbor_pass"]
        & base["tenor_neighbor_pass"]
    )
    full = base[base["fully_supported"]]
    single = base[base["two_layer_base_pass"]]
    if len(full):
        stability = "wide_stable" if len(full) >= 4 else "narrow_stable"
        conclusion = "research_watchlist" if not cross_stats["model_sensitive"] else "mixed_not_confirmed"
    elif len(single):
        stability = "peak_only"
        conclusion = "not_confirmed"
    else:
        stability = "reject"
        conclusion = "not_confirmed"
    summary = {
        "conclusion": conclusion,
        "stability_label": stability,
        "single_line_pass_candidates": single["candidate"].tolist(),
        "fully_supported_candidates": full["candidate"].tolist(),
        "model_sensitive": bool(cross_stats["model_sensitive"]),
        "research_status": "research_only_not_live_approved",
    }
    return base, summary


def compare_open_close(
    close_daily: pd.DataFrame, definitions: pd.DataFrame
) -> pd.DataFrame:
    open_daily = pd.read_csv(V6_DAILY, parse_dates=["date"])
    wanted = set(definitions["candidate"]) | {"no_put"}
    open_daily = open_daily[open_daily["candidate"].isin(wanted)]
    rows = []
    for layer in ["model", "real"]:
        for candidate in sorted(wanted):
            for timing, source in [("open_v6", open_daily), ("close_v8", close_daily)]:
                group = source[(source["layer"] == layer) & (source["candidate"] == candidate)].sort_values("date")
                if group.empty:
                    continue
                start, end = pd.Timestamp(group["date"].min()), pd.Timestamp(group["date"].max())
                for window, offset in WINDOWS.items():
                    requested = start if offset is None else end - offset
                    available = offset is None or start <= requested
                    if not available:
                        continue
                    sample = group[group["date"] >= requested]
                    values = v6.metrics(sample["cash_ret"])
                    rows.append(
                        {
                            "layer": layer,
                            "candidate": candidate,
                            "timing": timing,
                            "window": window,
                            "ann_return": values["ann_return"],
                            "max_dd": values["max_dd"],
                        }
                    )
    table = pd.DataFrame(rows)
    wide = table.pivot(
        index=["layer", "candidate", "window"],
        columns="timing",
        values=["ann_return", "max_dd"],
    )
    wide.columns = [f"{metric}_{timing}" for metric, timing in wide.columns]
    wide = wide.reset_index()
    wide["ann_return_close_minus_open"] = (
        wide["ann_return_close_v8"] - wide["ann_return_open_v6"]
    )
    wide["max_dd_close_minus_open"] = wide["max_dd_close_v8"] - wide["max_dd_open_v6"]
    return wide


def scan_tables(
    daily: pd.DataFrame, definitions: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = definitions.set_index("candidate").to_dict("index")
    params["no_put"] = {
        "structure": "none",
        "tenor": "none",
        "moneyness": 0.0,
        "execution": "none",
    }
    rows, wide_rows = [], []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        group = group.sort_values("date")
        start, end = pd.Timestamp(group["date"].min()), pd.Timestamp(group["date"].max())
        candidate_id = f"{layer}_{candidate}"
        wide = {
            "candidate": candidate_id,
            "layer": layer,
            "base_candidate": candidate,
            "structure": params[candidate]["structure"],
            "tenor": params[candidate]["tenor"],
            "moneyness": params[candidate]["moneyness"],
            "execution": params[candidate]["execution"],
        }
        for window, offset in WINDOWS.items():
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"] >= requested] if available else group
            values = v6.metrics(sample["cash_ret"])
            rows.append(
                {
                    "candidate": candidate_id,
                    "segment": window,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "rows": len(sample),
                    "ann_return": values["ann_return"],
                    "ann_vol": values["ann_vol"],
                    "sharpe_repo": values["sharpe_repo"],
                    "max_dd": values["max_dd"],
                    "layer": layer,
                    "base_candidate": candidate,
                    "structure": params[candidate]["structure"],
                    "tenor": params[candidate]["tenor"],
                    "moneyness": params[candidate]["moneyness"],
                    "execution": params[candidate]["execution"],
                    "requested_window_available": available,
                    "clipped_to_available_history": not available,
                }
            )
            wide[f"ann_return_{window}"] = values["ann_return"]
            wide[f"max_dd_{window}"] = values["max_dd"]
            wide[f"available_{window}"] = available
        wide_rows.append(wide)
    return pd.DataFrame(rows), pd.DataFrame(wide_rows)


def price_integrity(
    trades: pd.DataFrame, raw_options: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, float]]:
    real = trades[trades["layer"].eq("real")].copy()
    lookup = raw_options.set_index(["contract", "date"])
    rows = []
    for row in real.itertuples(index=False):
        day = pd.Timestamp(row.actual_execution_date)
        for leg, contract_col, price_col in [
            ("old", "old_contract", "old_trade_price"),
            ("new", "new_contract", "new_trade_price"),
        ]:
            contract = getattr(row, contract_col, "")
            used = getattr(row, price_col, np.nan)
            if not isinstance(contract, str) or not contract or pd.isna(used):
                continue
            quote = lookup.loc[(contract, day)]
            if isinstance(quote, pd.DataFrame):
                raise RuntimeError("Duplicate quote during price audit")
            rows.append(
                {
                    "candidate": row.candidate,
                    "date": day,
                    "leg": leg,
                    "contract": contract,
                    "used_price": float(used),
                    "raw_close": float(quote["close"]),
                    "raw_open": float(quote["open"]),
                    "raw_settle": float(quote["settle"]),
                    "volume": float(quote["volume"]),
                    "open_interest": float(quote["open_interest"]),
                    "abs_close_error": abs(float(used) - float(quote["close"])),
                }
            )
    audit = pd.DataFrame(rows)
    special = audit[
        audit["date"].eq(pd.Timestamp("2024-10-08"))
        & audit["contract"].eq("MO2411-P-4300")
        & audit["candidate"].eq("fixed175_or_mom120_2m_monthly_exit_m95")
    ]
    stats = {
        "trade_legs": len(audit),
        "max_close_price_error": float(audit["abs_close_error"].max()),
        "special_rows": len(special),
        "special_used_price": float(special.iloc[0]["used_price"]) if len(special) else np.nan,
        "special_raw_open": float(special.iloc[0]["raw_open"]) if len(special) else np.nan,
        "special_raw_close": float(special.iloc[0]["raw_close"]) if len(special) else np.nan,
    }
    if stats["max_close_price_error"] > 1e-14:
        raise RuntimeError(f"Close execution price mismatch: {stats}")
    if len(special) != 1 or not math.isclose(stats["special_used_price"], 12.2, abs_tol=1e-12):
        raise RuntimeError(f"2024-10-08 special price audit failed: {stats}")
    return audit, stats


def make_record(
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: dict[str, object],
    open_close: pd.DataFrame,
    price_stats: dict[str, float],
) -> str:
    main = [
        "no_put",
        "fixed175_or_mom120_front_exit_m95",
        "fixed175_or_mom120_2m_monthly_exit_m95",
        "fixed175_or_mom120_3m_monthly_exit_m95",
        "fixed175_or_mom120_3cycle_hold_expiry_m95",
    ]
    real = formal[
        formal["layer"].eq("real")
        & formal["candidate"].isin(main)
        & formal["window"].isin(WINDOWS)
    ][["candidate", "window", "available", "ann_return", "max_dd"]]
    year2024 = annual[
        annual["layer"].eq("real")
        & annual["candidate"].isin(main)
        & annual["year"].eq(2024)
    ][["candidate", "ann_return", "max_dd"]]
    timing = open_close[
        open_close["layer"].eq("real")
        & open_close["candidate"].isin(main)
        & open_close["window"].isin(["full", "last_3y", "last_1y"])
    ]
    return "\n".join(
        [
            "# 中证1000 MO Put 收盘执行复测 v8",
            "",
            "> 研究回测；未获准实盘。T日收盘信号，T+1收盘按官方close成交。",
            "",
            "## 结论",
            "",
            f"- 判定：`{summary['conclusion']}`；稳定性：`{summary['stability_label']}`。",
            f"- 单线通过：{summary['single_line_pass_candidates']}。",
            f"- 邻点完整支持：{summary['fully_supported_candidates']}。",
            "",
            "## 真实IM/MO强制窗口",
            "",
            real.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 2024",
            "",
            year2024.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## v6开盘与v8收盘",
            "",
            timing.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 价格完整性",
            "",
            f"- 审计真实交易腿{int(price_stats['trade_legs'])}条，成交价与原始close最大误差{price_stats['max_close_price_error']:.3e}。",
            f"- 2024-10-08 `MO2411-P-4300`使用{price_stats['special_used_price']:.1f}点；原始open={price_stats['special_raw_open']:.1f}，close={price_stats['special_raw_close']:.1f}。",
            "",
            "## 证据边界",
            "",
            "- 真实层使用官方IM/MO日线，2022-07-22—2026-08-14；10年和5年窗口因上市历史不足为N/A。",
            "- 模型层2015年起是中证1000 TRI+理论Put，不是历史滚IM，也不含IM贴水。",
            "- close是日线最后成交价，不是买一/卖一或收盘VWAP；不计价差和冲击。",
            "- 同一历史已多轮使用，没有独立OOS。",
            "",
            "## 决策明细",
            "",
            decisions.to_markdown(index=False, floatfmt=".6f"),
            "",
        ]
    )


def update_scan_meta(
    scan_summary: pd.DataFrame,
    definitions: pd.DataFrame,
    summary: dict[str, object],
    source_hashes: dict[str, str],
) -> None:
    path = SCAN / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "two_parameter_grid",
            "baseline": {"candidate": "no_put", "execution": "frozen_v6"},
            "candidate_grid": definitions[
                ["candidate", "structure", "moneyness", "execution"]
            ].to_dict("records"),
            "data_snapshot": {
                "model": [str(MODEL_START.date()), str(END.date())],
                "real": [str(REAL_START.date()), str(END.date())],
                "timezone": "Asia/Shanghai",
                "im_source": str(v5.IM_QUOTES.relative_to(ROOT)),
                "mo_source": str(v4.OPTIONS.relative_to(ROOT)),
                "adjustment_mode": "official futures/options raw daily bars; CSI1000 official TRI model layer",
            },
            "cost_model": {
                "put_side_cost_full_notional": PUT_SIDE_COST,
                "cash_weight": v6.CASH_WEIGHT,
                "cash_annual_return": 0.03,
                "slippage": "excluded",
                "execution": "T signal close, T+1 official daily close",
            },
            "source_hashes": source_hashes,
            "candidate_count": int(scan_summary["candidate"].nunique()),
            "research_conclusion": summary,
            "warnings": [
                "real 10y/5y scan rows are clipped full-history placeholders for strict artifact schema; user-facing formal metrics remain N/A",
                "daily close is not bid/ask or close VWAP",
                "no independent OOS",
            ],
        }
    )
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> None:
    verify_inputs()
    definitions = candidate_definitions()
    market, market_checks = v6.model_market()
    model_base = v6.model_baseline(market)
    upstream, _, _, _, _, raw_options = v4.load_inputs()
    daily_valuation, feature_diffs = v4.build_daily_valuation()
    if max(feature_diffs.values()) > 1e-14:
        raise RuntimeError("Frozen valuation feature parity failed")
    state = v6.signal_state(daily_valuation)
    model_schedule = v6.daily_signal_schedule(
        "fixed175_or_mom120",
        "fixed175_or_mom120",
        pd.DatetimeIndex(market["date"]),
        state,
    )
    real_schedule = v6.daily_signal_schedule(
        "fixed175_or_mom120",
        "fixed175_or_mom120",
        pd.DatetimeIndex(upstream["date"]),
        state,
    )
    regular = pd.concat(
        [
            model_schedule.assign(layer="model"),
            real_schedule.assign(layer="real"),
        ],
        ignore_index=True,
    )
    regular = regular[~regular["initial_listing_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Signal/execution leakage")

    active_im = active_im_closes(upstream)
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    model_overlays, real_overlays = {}, {}
    trade_parts, life_parts = [], []
    for item in definitions.itertuples(index=False):
        if item.structure == "3cycle_hold_expiry":
            mo, mt, ml = run_model_hold_close(
                market, model_schedule, item.moneyness, item.candidate
            )
            ro, rt, rl = run_real_hold_close(
                upstream, options, active_im, real_schedule, item.moneyness, item.candidate
            )
        else:
            mo, mt, ml = run_model_normal_close(
                market, model_schedule, item.tenor, item.moneyness, item.candidate
            )
            ro, rt, rl = run_real_normal_close(
                upstream,
                options,
                active_im,
                real_schedule,
                item.tenor,
                item.moneyness,
                item.candidate,
            )
        model_overlays[item.candidate] = mo
        real_overlays[item.candidate] = ro
        for frame in [mt, rt]:
            if len(frame):
                trade_parts.append(frame)
        for layer, frame in [("model", ml), ("real", rl)]:
            if len(frame):
                frame = frame.copy()
                frame["layer"] = layer
                life_parts.append(frame)

    real_base = upstream[["date", "im_gross_ret", "cost_rate", "im_net_ret"]].rename(
        columns={"im_gross_ret": "gross_ret", "im_net_ret": "net_ret"}
    )
    model_daily = v6.assemble_layer("model", model_base, model_overlays)
    real_daily = v6.assemble_layer("real", real_base, real_overlays)
    daily = pd.concat([model_daily, real_daily], ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False)

    old = pd.read_csv(V6_DAILY, parse_dates=["date"])
    for layer, new_no in [("model", model_daily), ("real", real_daily)]:
        left = new_no[new_no["candidate"].eq("no_put")].sort_values("date")
        right = old[(old["layer"] == layer) & old["candidate"].eq("no_put")].sort_values("date")
        parity = float(np.abs(left["cash_ret"].to_numpy() - right["cash_ret"].to_numpy()).max())
        if parity > 1e-14:
            raise RuntimeError(f"{layer} no-Put parity failed: {parity}")

    expected = set(definitions["candidate"]) | {"no_put"}
    for layer in ["model", "real"]:
        layer_daily = daily[daily["layer"].eq(layer)]
        if set(layer_daily["candidate"]) != expected:
            raise RuntimeError(f"Incomplete candidate set: {layer}")
        if layer_daily.duplicated(["candidate", "date"]).any():
            raise RuntimeError(f"Duplicate daily candidate/date: {layer}")
        if layer_daily[["ret", "cash_ret"]].isna().any().any():
            raise RuntimeError(f"Missing returns: {layer}")

    holds = lifecycles[lifecycles["candidate"].str.contains("3cycle_hold_expiry", na=False)]
    hold_ratios = {}
    for layer in ["model", "real"]:
        complete = holds[(holds["layer"] == layer) & holds["covered_rolls"].notna()]
        ratio = float(complete["covered_rolls"].eq(3).mean()) if len(complete) else np.nan
        hold_ratios[layer] = ratio
        if not math.isclose(ratio, 1.0, abs_tol=1e-12):
            raise RuntimeError(f"Strict three-cycle audit failed: {layer}={ratio}")

    price_audit, price_stats = price_integrity(trades, raw_options)
    formal, annual = v6.metrics_tables(daily)
    exposure = v6.exposure_table(daily, trades)
    cross_table, cross_stats = v6.cross_validation(daily, definitions)
    decision, decision_summary = add_neighbor_decisions(
        formal, exposure, definitions, cross_stats
    )
    open_close = compare_open_close(daily, definitions)
    scan_summary, window_metrics = scan_tables(daily, definitions)
    record = make_record(
        formal, annual, decision, decision_summary, open_close, price_stats
    )

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost.csv", index=False)
    definitions.to_csv(OUTPUT / "candidate_definitions.csv", index=False)
    trades.to_csv(OUTPUT / "trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(OUTPUT / "lifecycle_audit.csv", index=False)
    cross_table.to_csv(OUTPUT / "model_real_cross_validation.csv", index=False)
    decision.to_csv(OUTPUT / "decision_table.csv", index=False)
    open_close.to_csv(OUTPUT / "open_vs_close_metrics.csv", index=False)
    price_audit.to_csv(OUTPUT / "close_price_integrity_audit.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(
            {
                **decision_summary,
                "cross_validation": cross_stats,
                "price_integrity": price_stats,
                "three_cycle_exact_ratio": hold_ratios,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")

    source_paths = [
        SPEC,
        Path(v6.__file__),
        Path(v4.__file__),
        Path(v5.__file__),
        V6_DAILY,
        V7_RECORD,
        v4.PRICE,
        v4.TRI,
        v4.GOV10Y,
        v4.UPSTREAM,
        v4.OPTIONS,
        v5.IM_QUOTES,
        v6.OHLC,
        v4.VALUATION,
        v4.MONTHLY_STATES,
    ]
    source_hashes = {str(Path(path).relative_to(ROOT)): sha256(Path(path)) for path in source_paths}
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "research_status": "research_only_not_live_approved",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "samples": {
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
        },
        "market_checks": market_checks,
        "valuation_checks": feature_diffs,
        "cross_validation": cross_stats,
        "price_integrity": price_stats,
        "three_cycle_exact_ratio": hold_ratios,
        "decision": decision_summary,
        "git_status": git_status(),
        "execution": {
            "signal": "T close",
            "trade": "T+1 official close",
            "mark": "official settle",
            "slippage": "excluded",
        },
        "warnings": [
            "Daily close is last trade, not executable bid/ask or close VWAP",
            "Model layer before 2022 is theoretical and excludes IM carry",
            "No independent OOS",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    command_text = (
        "python.exe -m pytest test_im_mo_close_execution_v8.py -q\n"
        "python.exe im_mo_close_execution_v8.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(command_text, encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(
        "\n".join(
            [
                "# Quant Parameter Scan Record",
                "",
                "## Run Metadata",
                "",
                f"- Run id: {SCAN.name}",
                "- Run date: 2026-08-17",
                "- Timezone: Asia/Shanghai",
                "- Project: 中证1000 Put保护",
                f"- Repo or workspace path: {ROOT}",
                f"- Version or strategy family: {VERSION}",
                "- Sleeve or subsystem: fixed175_or_mom120 Put工具",
                "- Parameter group: 收盘执行×期限×行权价比例",
                "- Scan type: two_parameter_grid",
                "",
                "## Research Question",
                "",
                "- Baseline: 同层no-Put；v6开盘执行为历史对照。",
                "- Candidate grid: front/2m/3m/3cycle × 85/90/95。",
                "- Decision target: watchlist or keep research-only.",
                "- Source-change rule: research_only_no_production_change.",
                "- Required windows: full/10y/5y/3y/1y；真实10y/5y不足时正式报告N/A。",
                "",
                "## Implementation Anchor",
                "",
                f"- Official entrypoint: `{Path(__file__).name}`",
                "- Function path: frozen v6 loaders/signals/baseline + v8 close execution engines.",
                "- Existing metrics reused: v6.metrics/v6.metrics_tables.",
                "",
                "## Data Snapshot",
                "",
                f"- Model: {MODEL_START.date()}—{END.date()}，CSI1000 TRI+理论Put。",
                f"- Real: {REAL_START.date()}—{END.date()}，官方IM/MO日线。",
                "- Trading calendar/timezone: IM共同交易日，Asia/Shanghai。",
                "- Adjustment mode: 期货/期权原始日线；模型层官方指数。",
                "",
                "## Cost and Execution Assumptions",
                "",
                "- Put成本：完整名义每边1bp；70%现金年化3%。",
                "- Signal T close；execution T+1 close；mark official settle。",
                "- Bid/ask、冲击及close可获得性未计。",
                "",
                "## Runtime Override Plan",
                "",
                "- 新版本独立引擎，不修改冻结v6/v7。",
                "- no-Put逐日与v6做1e-14 parity。",
                "",
                "## Commands",
                "",
                "```powershell",
                command_text.strip(),
                "```",
                "",
                "## Output Files",
                "",
                "- `record.md`",
                "- `scan_summary.csv`",
                "- `window_metrics.csv`",
                "- `scan_meta.json`",
                "- `command_log.txt`",
                f"- Formal output: `{OUTPUT.relative_to(ROOT)}`",
                "",
                "## Full-Sample Results",
                "",
                decision.to_markdown(index=False, floatfmt=".6f"),
                "",
                "## Window Results",
                "",
                "- 完整窗口见`scan_summary.csv`与`window_metrics.csv`。",
                "- real 10y/5y因历史不足，机器严格检查表使用全历史占位并标记`clipped_to_available_history=True`；正式报告为N/A。",
                "",
                "## Stability Classification",
                "",
                f"- Label: {decision_summary['stability_label']}",
                f"- Evidence: {decision_summary['fully_supported_candidates']}",
                "- Cost sensitivity: 仅含固定1bp，不含价差和冲击。",
                "- Data sensitivity: close为最后成交价，不是盘口。",
                "",
                "## Decision",
                "",
                f"- Decision: {decision_summary['conclusion']}",
                "- Recommended next action: 先解释收盘执行结果，再决定是否做盘口/VWAP新版本。",
                "",
                "## User-Facing Summary",
                "",
                "- 收盘执行结果以正式输出record.md为准；未获准实盘。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command_text)
    update_scan_meta(scan_summary, definitions, decision_summary, source_hashes)
    print(
        json.dumps(
            {
                **decision_summary,
                "price_integrity": price_stats,
                "three_cycle_exact_ratio": hold_ratios,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
