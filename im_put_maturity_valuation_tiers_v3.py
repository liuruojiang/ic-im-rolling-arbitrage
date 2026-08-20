from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from im_monthly_roll_valuation_gated_put_v2 import add_option_expiry, third_friday


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "docs" / "im_put_maturity_valuation_tiers_v3_spec.md"
SPEC_HASH = "3D26B34D599F336B1E80FD4A9E8B23940FA79DBD1EBE137152723ADAAE4D1D6D"
OUTPUT = ROOT / "outputs" / "im_put_maturity_valuation_tiers_v3"
SCAN = ROOT / "quant_param_scan_runs" / "20260816_hold_and_tiers"

UPSTREAM_DAILY = ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1" / "daily_nav.csv"
V2_DAILY = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v2" / "daily_nav.csv"
V2_DECISIONS = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v2" / "decision_schedule.csv"
V2_ANALOGUES = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v2" / "signal_analogues.csv"
V2_CURRENT = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v2" / "current_signal.csv"
OPTIONS = ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1" / "cffex_mo_puts.csv"

TRADING_DAYS = 252
CASH_WEIGHT = 0.70
CASH_ANNUAL_RETURN = 0.03
CASH_DAILY_RETURN = (1.0 + CASH_ANNUAL_RETURN) ** (1.0 / TRADING_DAYS) - 1.0
IM_ONE_WAY_COST = 0.0001
PUT_PER_CONTRACT_SIDE_COST = 0.00005

CANDIDATES = [
    "no_put",
    "monthly_binary_v2",
    "expiry_binary",
    "monthly_three_tier",
    "expiry_three_tier",
]
OVERLAY_CANDIDATES = CANDIDATES[1:]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> None:
    observed = sha256(SPEC).upper()
    if observed != SPEC_HASH:
        raise RuntimeError(f"Frozen spec hash mismatch: {observed} != {SPEC_HASH}")
    if OUTPUT.exists():
        raise RuntimeError(f"Formal output already exists and may not be overwritten: {OUTPUT}")


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = pd.read_csv(UPSTREAM_DAILY, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    v2 = pd.read_csv(V2_DAILY, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    decisions = pd.read_csv(
        V2_DECISIONS,
        parse_dates=["state_date", "execution_date", "target_date", "selected_contract_month", "selected_expiry"],
    )
    analogues = pd.read_csv(V2_ANALOGUES, parse_dates=["as_of", "forward_end_date"])
    options = add_option_expiry(pd.read_csv(OPTIONS, parse_dates=["date"]))
    if len(daily) != 986 or daily["date"].min() != pd.Timestamp("2022-07-22") or daily["date"].max() != pd.Timestamp("2026-08-14"):
        raise RuntimeError("Unexpected frozen IM sample")
    if not daily["date"].equals(v2["date"]):
        raise RuntimeError("v2 and upstream calendars differ")
    if not daily["date"].is_unique or not daily["date"].is_monotonic_increasing:
        raise RuntimeError("Invalid daily calendar")
    if (analogues["forward_end_date"] > analogues["as_of"]).any():
        raise RuntimeError("Walk-forward analogue leakage")
    return daily, v2, decisions, analogues, options


def tier_target(forecast: float | None, enough: bool) -> tuple[int, str]:
    if not enough or forecast is None or pd.isna(forecast):
        return 0, "insufficient_analogues_0pct"
    value = float(forecast)
    if value < 0.0:
        return 2, "adverse_below_0pct_100pct"
    if value < 0.03:
        return 1, "neutral_0_to_3pct_50pct"
    return 0, "favorable_at_least_3pct_0pct"


def enrich_decisions(decisions: pd.DataFrame) -> pd.DataFrame:
    result = decisions.copy()
    result["binary_target_qty"] = result["signal_on"].astype(bool).astype(int) * 2
    values = [tier_target(row.forecast_3y_median, bool(row.enough_analogues)) for row in result.itertuples()]
    result["three_tier_target_qty"] = [value[0] for value in values]
    result["three_tier_label"] = [value[1] for value in values]
    return result


def actual_expiry_map(options: pd.DataFrame, daily: pd.DataFrame) -> dict[pd.Timestamp, pd.Timestamp]:
    end = pd.Timestamp(daily["date"].max())
    mapping: dict[pd.Timestamp, pd.Timestamp] = {}
    for contract_month, subset in options.groupby("contract_month", sort=True):
        month = pd.Timestamp(contract_month)
        rule = pd.Timestamp(third_friday(month))
        if rule <= end:
            observed_last = pd.Timestamp(subset["date"].max())
            if observed_last < rule:
                raise RuntimeError(f"Expired option month ends before rule expiry: {month.date()}")
            mapping[month] = observed_last
        else:
            mapping[month] = rule
    return mapping


def prepare_options(options: pd.DataFrame, expiry_map: dict[pd.Timestamp, pd.Timestamp]) -> pd.DataFrame:
    result = options.copy()
    result["actual_expiry"] = result["contract_month"].map(expiry_map)
    if result["actual_expiry"].isna().any():
        raise RuntimeError("Missing actual option expiry")
    if result.duplicated(["contract", "date"]).any():
        raise RuntimeError("Duplicate option contract/date")
    return result


def select_lowest_liquid_put(
    options: pd.DataFrame,
    day: pd.Timestamp,
    target_date: pd.Timestamp,
) -> pd.Series:
    chain = options[(options["date"] == day) & (options["actual_expiry"] > day)].copy()
    if chain.empty:
        raise RuntimeError(f"No listed future MO puts on {day.date()}")
    months = chain[["contract_month", "actual_expiry"]].drop_duplicates().copy()
    months["distance_days"] = (months["actual_expiry"] - target_date).abs().dt.days
    chosen_month = months.sort_values(
        ["distance_days", "actual_expiry"], ascending=[True, False]
    ).iloc[0]
    selected_chain = chain[chain["contract_month"] == chosen_month["contract_month"]].copy()
    literal_min = float(selected_chain["strike"].min())
    liquid = selected_chain[
        (selected_chain["open"].notna())
        & (selected_chain["open"] > 0)
        & (selected_chain["volume"] > 0)
        & (selected_chain["open_interest"] > 0)
    ].sort_values(["strike", "contract"])
    if liquid.empty:
        raise RuntimeError(f"No liquid Put in chosen month on {day.date()}")
    selected = liquid.iloc[0].copy()
    selected["literal_min_strike"] = literal_min
    selected["used_liquidity_fallback"] = bool(float(selected["strike"]) != literal_min)
    selected["target_date"] = target_date
    selected["expiry_distance_days"] = abs((pd.Timestamp(selected["actual_expiry"]) - target_date).days)
    return selected


@dataclass
class Cohort:
    cohort_id: str
    contract: str
    qty: int
    entry_date: pd.Timestamp
    actual_expiry: pd.Timestamp
    prior_settle: float
    target_date: pd.Timestamp
    strike: float
    entry_open: float
    entry_settle: float
    entry_volume: float
    entry_open_interest: float
    literal_min_strike: float
    used_liquidity_fallback: bool


def option_row(lookup: pd.DataFrame, contract: str, day: pd.Timestamp) -> pd.Series:
    key = (contract, day)
    if key not in lookup.index:
        raise RuntimeError(f"Missing option mark: {contract} {day.date()}")
    row = lookup.loc[key]
    if isinstance(row, pd.DataFrame):
        raise RuntimeError(f"Duplicate option mark: {contract} {day.date()}")
    return row


def empty_overlay(daily: pd.DataFrame, label: str) -> pd.DataFrame:
    result = daily[["date", "settle"]].copy()
    result[f"{label}_put_pnl_ret"] = 0.0
    result[f"{label}_put_cost_rate"] = 0.0
    result[f"{label}_put_qty_held"] = 0
    result[f"{label}_put_qty_eod"] = 0
    result[f"{label}_put_mark_notional"] = 0.0
    result[f"{label}_put_contracts"] = ""
    result[f"{label}_buy_contracts"] = 0
    result[f"{label}_sell_contracts"] = 0
    result[f"{label}_expiry_contracts"] = 0
    return result


def monthly_overlay(
    daily: pd.DataFrame,
    decisions: pd.DataFrame,
    options: pd.DataFrame,
    target_column: str,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = empty_overlay(daily, label)
    lookup = options.set_index(["contract", "date"])
    events = {pd.Timestamp(row.execution_date): row for row in decisions.itertuples(index=False)}
    active: Cohort | None = None
    cohort_rows: list[dict[str, object]] = []
    cohort_number = 0

    for idx, row in result.iterrows():
        day = pd.Timestamp(row["date"])
        denominator = float(result.loc[idx - 1, "settle"]) if idx > 0 else float(row["settle"])
        equivalent_points = 0.0
        buy_qty = sell_qty = 0
        if day in events:
            decision = events[day]
            target_qty = int(getattr(decision, target_column))
            if active is not None:
                old = option_row(lookup, active.contract, day)
                if not (pd.notna(old["open"]) and float(old["open"]) > 0):
                    raise RuntimeError(f"Non-executable monthly exit: {active.contract} {day.date()}")
                equivalent_points += active.qty * 0.5 * (float(old["open"]) - active.prior_settle)
                sell_qty = active.qty
                for cohort_row in reversed(cohort_rows):
                    if cohort_row["cohort_id"] == active.cohort_id:
                        cohort_row.update(
                            {
                                "exit_type": "monthly_open_sale",
                                "exit_date": day,
                                "exit_price": float(old["open"]),
                            }
                        )
                        break
                active = None

            if target_qty > 0:
                new_contract = str(decision.put_contract)
                selected = option_row(lookup, new_contract, day)
                entry_price = float(selected["settle"]) if bool(decision.initial_listing_exception) else float(selected["open"])
                if not bool(decision.initial_listing_exception):
                    equivalent_points += target_qty * 0.5 * (float(selected["settle"]) - entry_price)
                buy_qty = target_qty
                cohort_number += 1
                active = Cohort(
                    cohort_id=f"{label}_{cohort_number:03d}",
                    contract=new_contract,
                    qty=target_qty,
                    entry_date=day,
                    actual_expiry=pd.Timestamp(selected["actual_expiry"]),
                    prior_settle=float(selected["settle"]),
                    target_date=pd.Timestamp(decision.target_date),
                    strike=float(selected["strike"]),
                    entry_open=entry_price,
                    entry_settle=float(selected["settle"]),
                    entry_volume=float(selected["volume"]),
                    entry_open_interest=float(selected["open_interest"]),
                    literal_min_strike=float(selected["strike"]),
                    used_liquidity_fallback=False,
                )
                cohort_rows.append(
                    {
                        **active.__dict__,
                        "candidate": label,
                        "entry_type": "initial_settle" if bool(decision.initial_listing_exception) else "monthly_open",
                        "exit_type": "open_at_sample_end",
                        "exit_date": pd.NaT,
                        "exit_price": np.nan,
                    }
                )
        elif active is not None:
            mark = option_row(lookup, active.contract, day)
            equivalent_points += active.qty * 0.5 * (float(mark["settle"]) - active.prior_settle)
            active.prior_settle = float(mark["settle"])

        result.loc[idx, f"{label}_put_pnl_ret"] = equivalent_points / denominator
        result.loc[idx, f"{label}_put_cost_rate"] = (buy_qty + sell_qty) * PUT_PER_CONTRACT_SIDE_COST
        result.loc[idx, f"{label}_buy_contracts"] = buy_qty
        result.loc[idx, f"{label}_sell_contracts"] = sell_qty
        if active is not None:
            mark = option_row(lookup, active.contract, day)
            active.prior_settle = float(mark["settle"])
            result.loc[idx, f"{label}_put_qty_held"] = active.qty
            result.loc[idx, f"{label}_put_qty_eod"] = active.qty
            result.loc[idx, f"{label}_put_mark_notional"] = active.qty * 0.5 * float(mark["settle"]) / float(row["settle"])
            result.loc[idx, f"{label}_put_contracts"] = f"{active.contract}x{active.qty}"

    return result, pd.DataFrame(cohort_rows)


def expiry_overlay(
    daily: pd.DataFrame,
    decisions: pd.DataFrame,
    options: pd.DataFrame,
    target_column: str,
    label: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = empty_overlay(daily, label)
    lookup = options.set_index(["contract", "date"])
    events = {pd.Timestamp(row.execution_date): row for row in decisions.itertuples(index=False)}
    active: list[Cohort] = []
    cohort_rows: list[dict[str, object]] = []
    target_qty = 0
    cohort_number = 0

    for idx, row in result.iterrows():
        day = pd.Timestamp(row["date"])
        denominator = float(result.loc[idx - 1, "settle"]) if idx > 0 else float(row["settle"])
        equivalent_points = 0.0
        buy_qty = 0
        if day in events:
            target_qty = int(getattr(events[day], target_column))

        for cohort in active:
            mark = option_row(lookup, cohort.contract, day)
            equivalent_points += cohort.qty * 0.5 * (float(mark["settle"]) - cohort.prior_settle)
            cohort.prior_settle = float(mark["settle"])

        current_qty = sum(cohort.qty for cohort in active)
        if current_qty < target_qty:
            buy_qty = target_qty - current_qty
            selected = select_lowest_liquid_put(options, day, day + pd.DateOffset(months=3))
            initial = idx == 0
            entry_price = float(selected["settle"]) if initial else float(selected["open"])
            if not initial:
                equivalent_points += buy_qty * 0.5 * (float(selected["settle"]) - entry_price)
            cohort_number += 1
            cohort = Cohort(
                cohort_id=f"{label}_{cohort_number:03d}",
                contract=str(selected["contract"]),
                qty=buy_qty,
                entry_date=day,
                actual_expiry=pd.Timestamp(selected["actual_expiry"]),
                prior_settle=float(selected["settle"]),
                target_date=pd.Timestamp(selected["target_date"]),
                strike=float(selected["strike"]),
                entry_open=entry_price,
                entry_settle=float(selected["settle"]),
                entry_volume=float(selected["volume"]),
                entry_open_interest=float(selected["open_interest"]),
                literal_min_strike=float(selected["literal_min_strike"]),
                used_liquidity_fallback=bool(selected["used_liquidity_fallback"]),
            )
            active.append(cohort)
            cohort_rows.append(
                {
                    **cohort.__dict__,
                    "candidate": label,
                    "entry_type": "initial_settle" if initial else "top_up_open",
                    "exit_type": "open_at_sample_end",
                    "exit_date": pd.NaT,
                    "exit_price": np.nan,
                }
            )

        held_qty = sum(cohort.qty for cohort in active)
        expiring = [cohort for cohort in active if cohort.actual_expiry == day]
        for cohort in expiring:
            mark = option_row(lookup, cohort.contract, day)
            for cohort_row in reversed(cohort_rows):
                if cohort_row["cohort_id"] == cohort.cohort_id:
                    cohort_row.update(
                        {
                            "exit_type": "official_expiry_settlement",
                            "exit_date": day,
                            "exit_price": float(mark["settle"]),
                        }
                    )
                    break
        active = [cohort for cohort in active if cohort.actual_expiry != day]

        eod_qty = sum(cohort.qty for cohort in active)
        mark_notional = 0.0
        contract_text: list[str] = []
        for cohort in active:
            mark = option_row(lookup, cohort.contract, day)
            mark_notional += cohort.qty * 0.5 * float(mark["settle"]) / float(row["settle"])
            contract_text.append(f"{cohort.contract}x{cohort.qty}")
        result.loc[idx, f"{label}_put_pnl_ret"] = equivalent_points / denominator
        result.loc[idx, f"{label}_put_cost_rate"] = buy_qty * PUT_PER_CONTRACT_SIDE_COST
        result.loc[idx, f"{label}_put_qty_held"] = held_qty
        result.loc[idx, f"{label}_put_qty_eod"] = eod_qty
        result.loc[idx, f"{label}_put_mark_notional"] = mark_notional
        result.loc[idx, f"{label}_put_contracts"] = ";".join(contract_text)
        result.loc[idx, f"{label}_buy_contracts"] = buy_qty
        result.loc[idx, f"{label}_expiry_contracts"] = sum(cohort.qty for cohort in expiring)
        if eod_qty > 2 or held_qty > 2:
            raise RuntimeError(f"Put quantity cap exceeded for {label} on {day.date()}")

    return result, pd.DataFrame(cohort_rows)


def assemble(
    upstream: pd.DataFrame,
    v2: pd.DataFrame,
    overlays: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    daily = upstream.copy()
    for label, overlay in overlays.items():
        daily = daily.merge(overlay.drop(columns=["settle"]), on="date", how="left", validate="one_to_one")
    daily["no_put_ret"] = daily["baseline_net_ret"]
    daily["no_put_cash_ret"] = daily["baseline_plus_cash_ret"]
    for label in OVERLAY_CANDIDATES:
        daily[f"{label}_gross_ret"] = daily["im_gross_ret"] + daily[f"{label}_put_pnl_ret"]
        daily[f"{label}_ret"] = (
            (1.0 + daily[f"{label}_gross_ret"])
            * (1.0 - daily["cost_rate"])
            * (1.0 - daily[f"{label}_put_cost_rate"])
            - 1.0
        )
        daily[f"{label}_cash_weight"] = (
            CASH_WEIGHT - daily[f"{label}_put_mark_notional"]
        ).clip(lower=0.0)
        daily[f"{label}_cash_ret"] = (
            daily[f"{label}_ret"] + daily[f"{label}_cash_weight"] * CASH_DAILY_RETURN
        )
    parity_net = float((daily["monthly_binary_v2_ret"] - v2["immediate_gated_net_ret"]).abs().max())
    parity_cash = float((daily["monthly_binary_v2_cash_ret"] - v2["immediate_gated_plus_cash_ret"]).abs().max())
    if parity_net > 1e-14 or parity_cash > 1e-14:
        raise RuntimeError(f"v2 parity failed: net={parity_net}, cash={parity_cash}")
    for candidate in CANDIDATES:
        column = "no_put_ret" if candidate == "no_put" else f"{candidate}_ret"
        daily[f"nav_{candidate}"] = (1.0 + daily[column]).cumprod()
        cash_column = "no_put_cash_ret" if candidate == "no_put" else f"{candidate}_cash_ret"
        daily[f"nav_{candidate}_cash"] = (1.0 + daily[cash_column]).cumprod()
    core = ["no_put_ret"] + [f"{candidate}_ret" for candidate in OVERLAY_CANDIDATES]
    if daily[core].isna().any().any() or (daily[core] <= -1).any().any():
        raise RuntimeError("Invalid return output")
    return daily


def metrics(returns: pd.Series) -> dict[str, float]:
    r = returns.astype(float).dropna().reset_index(drop=True)
    nav = pd.concat([pd.Series([1.0]), (1.0 + r).cumprod()], ignore_index=True)
    vol = float(r.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(r) > 1 else np.nan
    return {
        "total_return": float(nav.iloc[-1] - 1.0),
        "ann_return": float(nav.iloc[-1] ** (TRADING_DAYS / len(r)) - 1.0),
        "ann_vol": vol,
        "sharpe_repo": float(r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(r) > 1 and r.std(ddof=1) > 0 else np.nan,
        "max_dd": float((nav / nav.cummax() - 1.0).min()),
    }


def return_column(candidate: str, cash: bool = False) -> str:
    if candidate == "no_put":
        return "no_put_cash_ret" if cash else "no_put_ret"
    return f"{candidate}_cash_ret" if cash else f"{candidate}_ret"


def window_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    start = pd.Timestamp(daily["date"].min())
    end = pd.Timestamp(daily["date"].max())
    requested = {
        "full": start,
        "last_10y": end - pd.DateOffset(years=10),
        "last_5y": end - pd.DateOffset(years=5),
        "last_3y": end - pd.DateOffset(years=3),
        "last_1y": end - pd.DateOffset(years=1),
    }
    formal_rows: list[dict[str, object]] = []
    scan_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        wide: dict[str, object] = {"candidate": candidate}
        for window, cutoff in requested.items():
            available = window == "full" or start <= cutoff
            formal_subset = daily[daily["date"] >= cutoff] if available else daily.iloc[0:0]
            row: dict[str, object] = {
                "candidate": candidate,
                "window": window,
                "available": available,
                "unavailable_reason": "" if available else f"history starts {start.date()}, shorter than {window}",
                "requested_start": cutoff.date().isoformat(),
                "actual_start": formal_subset["date"].min().date().isoformat() if available else "",
                "end": end.date().isoformat(),
                "rows": int(len(formal_subset)),
            }
            if available:
                row.update(metrics(formal_subset[return_column(candidate)]))
                row.update({f"cash_{k}": v for k, v in metrics(formal_subset[return_column(candidate, True)]).items()})
            else:
                for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "cash_total_return", "cash_ann_return", "cash_ann_vol", "cash_sharpe_repo", "cash_max_dd"]:
                    row[key] = np.nan
            formal_rows.append(row)

            # The strict scan schema requires finite values for every named window. For unavailable
            # 10y/5y requests, use the actual clipped history and flag it; the user report remains N/A.
            scan_subset = formal_subset if available else daily
            scan_metric = metrics(scan_subset[return_column(candidate)])
            scan_rows.append(
                {
                    "candidate": candidate,
                    "segment": window,
                    "start": scan_subset["date"].min().date().isoformat(),
                    "end": scan_subset["date"].max().date().isoformat(),
                    "rows": int(len(scan_subset)),
                    "ann_return": scan_metric["ann_return"],
                    "ann_vol": scan_metric["ann_vol"],
                    "sharpe_repo": scan_metric["sharpe_repo"],
                    "max_dd": scan_metric["max_dd"],
                    "requested_window_available": available,
                    "clipped_to_available_history": not available,
                }
            )
            wide[f"ann_return_{window}"] = scan_metric["ann_return"]
            wide[f"max_dd_{window}"] = scan_metric["max_dd"]
            wide[f"available_{window}"] = available
        wide_rows.append(wide)
    return pd.DataFrame(formal_rows), pd.DataFrame(scan_rows), pd.DataFrame(wide_rows)


def annual_outputs(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, subset in daily.groupby(daily["date"].dt.year, sort=True):
        for candidate in CANDIDATES:
            metric = metrics(subset[return_column(candidate)])
            rows.append(
                {
                    "year": int(year),
                    "partial_year": int(year) in {int(daily.date.dt.year.min()), int(daily.date.dt.year.max())},
                    "candidate": candidate,
                    "start": subset.date.min().date().isoformat(),
                    "end": subset.date.max().date().isoformat(),
                    "rows": len(subset),
                    **metric,
                }
            )
    return pd.DataFrame(rows)


def exposure_outputs(daily: pd.DataFrame, cohorts: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    rows.append(
        {
            "candidate": "no_put",
            "average_put_qty_held": 0.0,
            "protected_day_ratio": 0.0,
            "days_qty_0": len(daily),
            "days_qty_1": 0,
            "days_qty_2": 0,
            "buy_contracts": 0,
            "sell_contracts": 0,
            "expiry_contracts": 0,
            "transaction_sides_contract_equivalent": 0,
            "put_cost_sum": 0.0,
            "cohort_count": 0,
            "liquidity_fallback_count": 0,
        }
    )
    for candidate in OVERLAY_CANDIDATES:
        qty = daily[f"{candidate}_put_qty_held"].astype(int)
        candidate_cohorts = cohorts[cohorts["candidate"] == candidate]
        buys = int(daily[f"{candidate}_buy_contracts"].sum())
        sells = int(daily[f"{candidate}_sell_contracts"].sum())
        expiries = int(daily[f"{candidate}_expiry_contracts"].sum())
        rows.append(
            {
                "candidate": candidate,
                "average_put_qty_held": float(qty.mean()),
                "protected_day_ratio": float((qty > 0).mean()),
                "days_qty_0": int((qty == 0).sum()),
                "days_qty_1": int((qty == 1).sum()),
                "days_qty_2": int((qty == 2).sum()),
                "buy_contracts": buys,
                "sell_contracts": sells,
                "expiry_contracts": expiries,
                "transaction_sides_contract_equivalent": buys + sells,
                "put_cost_sum": float(daily[f"{candidate}_put_cost_rate"].sum()),
                "cohort_count": len(candidate_cohorts),
                "liquidity_fallback_count": int(candidate_cohorts["used_liquidity_fallback"].astype(bool).sum()),
            }
        )
    return pd.DataFrame(rows)


def event_audit(daily: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(["2024-01-02", "2024-01-22", "2024-02-01", "2024-02-05"])
    columns = ["date", "contract", "no_put_ret"]
    for candidate in OVERLAY_CANDIDATES:
        columns.extend(
            [
                f"{candidate}_ret",
                f"{candidate}_put_pnl_ret",
                f"{candidate}_put_qty_held",
                f"{candidate}_put_contracts",
            ]
        )
    return daily[daily["date"].isin(dates)][columns].copy()


def current_status(daily: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    forecast = float(current.loc[0, "forecast_3y_median"])
    target, tier = tier_target(forecast, bool(current.loc[0, "enough_analogues"]))
    rows = []
    for candidate in OVERLAY_CANDIDATES:
        last = daily.iloc[-1]
        qty = int(last[f"{candidate}_put_qty_eod"])
        if candidate.startswith("monthly"):
            action = "prospective_buy_next_trading_day_open" if target > qty else "hold"
        else:
            action = "already_at_or_above_target_hold_to_expiry" if qty >= target else "prospective_top_up_next_trading_day_open"
        rows.append(
            {
                "as_of": current.loc[0, "state_date"],
                "candidate": candidate,
                "forecast_3y_median": forecast,
                "three_tier_label": tier,
                "current_target_qty": target if "three_tier" in candidate else 2,
                "historical_eod_qty": qty,
                "historical_eod_contracts": last[f"{candidate}_put_contracts"],
                "prospective_action": action,
                "research_only": True,
            }
        )
    return pd.DataFrame(rows)


def fmt_pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value * 100:.2f}%"


def build_record(
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    events: pd.DataFrame,
    current: pd.DataFrame,
) -> str:
    full = formal[(formal.window == "full")].set_index("candidate")
    three = formal[(formal.window == "last_3y")].set_index("candidate")
    one = formal[(formal.window == "last_1y")].set_index("candidate")
    lines = [
        "# IM Put 持有方式 × 三档估值保护 v3：结果记录",
        "",
        "运行日期：2026-08-16  ",
        "研究状态：结构性候选观察；未获准实盘  ",
        "正式样本：2022-07-22至2026-08-14（986个交易日）",
        "",
        "## 核心结果",
        "",
        "| 候选 | 全样本 CAGR / MaxDD | 3年 CAGR / MaxDD | 1年 CAGR / MaxDD |",
        "|---|---:|---:|---:|",
    ]
    for candidate in CANDIDATES:
        lines.append(
            f"| `{candidate}` | {fmt_pct(full.loc[candidate, 'ann_return'])} / {fmt_pct(full.loc[candidate, 'max_dd'])} | "
            f"{fmt_pct(three.loc[candidate, 'ann_return'])} / {fmt_pct(three.loc[candidate, 'max_dd'])} | "
            f"{fmt_pct(one.loc[candidate, 'ann_return'])} / {fmt_pct(one.loc[candidate, 'max_dd'])} |"
        )
    lines.extend(
        [
            "",
            "10年和5年窗口因IM/MO历史从2022-07-22才开始而为N/A。",
            "",
            "## 交易与保护暴露",
            "",
            "| 候选 | 平均MO张数 | 保护日占比 | 买/卖/到期张数 | Put累计成本 | 流动性上移 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in exposure.itertuples():
        lines.append(
            f"| `{row.candidate}` | {row.average_put_qty_held:.3f} | {row.protected_day_ratio * 100:.2f}% | "
            f"{row.buy_contracts}/{row.sell_contracts}/{row.expiry_contracts} | {row.put_cost_sum * 100:.3f}% | {row.liquidity_fallback_count} |"
        )
    lines.extend(["", "## 2024关键日期", ""])
    for event in events.itertuples():
        lines.append(
            f"- {pd.Timestamp(event.date).date()}：无Put {event.no_put_ret * 100:.2f}%；"
            f"月滚二档 {event.monthly_binary_v2_ret * 100:.2f}%（{event.monthly_binary_v2_put_contracts or '无Put'}）；"
            f"到期二档 {event.expiry_binary_ret * 100:.2f}%（{event.expiry_binary_put_contracts or '无Put'}）。"
        )
    lines.extend(
        [
            "",
            "## 当前估值和档位",
            "",
            f"- 现有估值不是单一PE阈值，而是PE、PB、ERP和已实现股息的四维严格走步类比；当前3年类比中位数为{float(current.iloc[0].forecast_3y_median) * 100:.2f}%。",
            "- 三档冻结规则：预测>=3%不保护；0%至3%保护50%；预测<0保护100%。当前属于100%档。",
        ]
    )
    for row in current.itertuples():
        lines.append(
            f"- `{row.candidate}`：样本日终{row.historical_eod_qty}张 `{row.historical_eod_contracts}`；当前动作 `{row.prospective_action}`。"
        )
    lines.extend(
        [
            "",
            "## 数据、执行与边界",
            "",
            "- 真实中金所IM/MO官方日行情；交易用官方开盘价，持有和到期用官方结算价。每1份IM对应2张MO为100%名义保护。",
            "- 月滚候选在每个估值T+1开盘卖旧买新；持有到期候选只补足、不提前卖出，到期后的下一共同交易日再按最新档位补足。",
            "- 持有到期的入场使用最低且开盘价、成交量、持仓量均为正的Put；2023-02-20唯一一次从6100上移至6200。",
            "- IM和Put沿用每整组每边1bp成本；70%现金年化3%，扣Put权利金占用；不使用3.33倍方向杠杆。未计盘口冲击、行权/结算附加费和追加保证金摩擦。",
            "- 样本仅约4年，不能据单一2024事件或全样本最优直接晋升。本结果不是交易建议。",
            "",
            "## 复现",
            "",
            f"- 冻结规格SHA-256：`{SPEC_HASH.lower()}`。",
            f"- 脚本SHA-256：`{sha256(Path(__file__))}`。",
            "- 命令：`python.exe im_put_maturity_valuation_tiers_v3.py`。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    daily: pd.DataFrame,
    decisions: pd.DataFrame,
    cohorts: pd.DataFrame,
    formal: pd.DataFrame,
    scan_summary: pd.DataFrame,
    window_metrics: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    events: pd.DataFrame,
    current: pd.DataFrame,
    manifest: dict[str, object],
) -> None:
    record = build_record(formal, annual, exposure, events, current)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_nav.csv", index=False)
    decisions.to_csv(OUTPUT / "valuation_tiers.csv", index=False)
    cohorts.to_csv(OUTPUT / "option_cohorts.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_and_costs.csv", index=False)
    events.to_csv(OUTPUT / "event_audit_2024.csv", index=False)
    current.to_csv(OUTPUT / "current_status.csv", index=False)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(
        "python.exe im_put_maturity_valuation_tiers_v3.py\n", encoding="utf-8"
    )

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False)
    scan_record = record.replace(
        "# IM Put 持有方式 × 三档估值保护 v3：结果记录",
        "# Quant Parameter Scan Record\n\n## Run Metadata\n\n- Run id: `20260816_hold_and_tiers`\n- Scan type: `candidate_bundle`\n- Source-change rule: `research_only_no_source_change`\n- Decision: pending post-run review\n- Stability: pending post-run review\n\n## Research Question\n\n2×2比较Put月滚/持有到期与二档/三档保护。\n\n## Data Snapshot\n\n真实中金所IM/MO日行情，2022-07-22至2026-08-14。\n\n## Cost and Execution Assumptions\n\n见冻结规格和下方正式结果；月末T收盘、T+1开盘，70%现金年化3%。\n\n## Output Files\n\n- `scan_summary.csv`\n- `window_metrics.csv`\n- `scan_meta.json`\n- `command_log.txt`\n\n## Full-Sample Results",
    )
    (SCAN / "record.md").write_text(scan_record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python.exe im_put_maturity_valuation_tiers_v3.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"candidate": "monthly_binary_v2", "parity_source": str(V2_DAILY)},
            "candidate_grid": CANDIDATES,
            "data_snapshot": manifest["sample"],
            "cost_model": manifest["cost_model"],
            "parity_check": manifest["checks"],
            "source_hashes": manifest["inputs"],
            "warnings": [
                "Formal 10y/5y metrics are N/A; strict scan tables contain clipped full-history numeric values with explicit flags.",
                "Official opens do not include order-book impact; expiry/exercise extra fees excluded.",
            ],
            "git_status_after": git_status(),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run() -> None:
    verify_spec()
    upstream, v2, decisions, analogues, options = load_inputs()
    decisions = enrich_decisions(decisions)
    expiry_map = actual_expiry_map(options, upstream)
    options = prepare_options(options, expiry_map)

    overlays: dict[str, pd.DataFrame] = {}
    cohort_frames: list[pd.DataFrame] = []
    for label, target in [
        ("monthly_binary_v2", "binary_target_qty"),
        ("monthly_three_tier", "three_tier_target_qty"),
    ]:
        overlay, cohorts = monthly_overlay(upstream, decisions, options, target, label)
        overlays[label] = overlay
        cohort_frames.append(cohorts)
    for label, target in [
        ("expiry_binary", "binary_target_qty"),
        ("expiry_three_tier", "three_tier_target_qty"),
    ]:
        overlay, cohorts = expiry_overlay(upstream, decisions, options, target, label)
        overlays[label] = overlay
        cohort_frames.append(cohorts)

    cohorts = pd.concat(cohort_frames, ignore_index=True)
    daily = assemble(upstream, v2, overlays)
    formal, scan_summary, wide = window_outputs(daily)
    annual = annual_outputs(daily)
    exposure = exposure_outputs(daily, cohorts)
    events = event_audit(daily)
    current_input = pd.read_csv(V2_CURRENT)
    current = current_status(daily, current_input)

    fallback = cohorts[cohorts["used_liquidity_fallback"].astype(bool)]
    if set(fallback["contract"].unique()) != {"MO2305-P-6200"}:
        raise RuntimeError(f"Unexpected liquidity fallback set: {fallback['contract'].unique().tolist()}")
    if not (events.loc[events.date == pd.Timestamp("2024-02-05"), "expiry_binary_put_qty_held"].iloc[0] == 2):
        raise RuntimeError("Expiry candidate failed to retain March Put through 2024-02-05")

    parity_net = float((daily["monthly_binary_v2_ret"] - v2["immediate_gated_net_ret"]).abs().max())
    parity_cash = float((daily["monthly_binary_v2_cash_ret"] - v2["immediate_gated_plus_cash_ret"]).abs().max())
    manifest: dict[str, object] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": "python.exe im_put_maturity_valuation_tiers_v3.py",
        "spec_sha256": sha256(SPEC),
        "script_sha256": sha256(Path(__file__)),
        "inputs": {
            str(path.relative_to(ROOT)): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in [UPSTREAM_DAILY, V2_DAILY, V2_DECISIONS, V2_ANALOGUES, V2_CURRENT, OPTIONS]
        },
        "sample": {
            "start": str(upstream.date.min().date()),
            "end": str(upstream.date.max().date()),
            "rows": len(upstream),
            "timezone": "Asia/Shanghai",
            "data_source": "CFFEX official IM/MO daily market data",
        },
        "candidates": CANDIDATES,
        "three_tier_rule": {"0_put": "forecast>=3% or insufficient analogues", "1_put": "0%<=forecast<3%", "2_put": "forecast<0%"},
        "cost_model": {
            "im_one_way": IM_ONE_WAY_COST,
            "mo_per_contract_per_trade": PUT_PER_CONTRACT_SIDE_COST,
            "cash_weight": CASH_WEIGHT,
            "cash_annual_return": CASH_ANNUAL_RETURN,
            "expiry_extra_fee": 0.0,
        },
        "checks": {
            "v2_net_parity_max_abs": parity_net,
            "v2_cash_parity_max_abs": parity_cash,
            "analogue_causal": bool((analogues.forward_end_date <= analogues.as_of).all()),
            "daily_dates_unique": bool(daily.date.is_unique),
            "core_nan_count": int(daily[[return_column(c) for c in CANDIDATES]].isna().sum().sum()),
            "liquidity_fallback_contracts": sorted(fallback.contract.unique().tolist()),
            "mo2606_actual_expiry": str(expiry_map[pd.Timestamp("2026-06-01")].date()),
        },
    }
    write_outputs(
        daily,
        decisions,
        cohorts,
        formal,
        scan_summary,
        wide,
        annual,
        exposure,
        events,
        current,
        manifest,
    )


if __name__ == "__main__":
    run()
