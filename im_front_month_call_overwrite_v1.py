from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_overwrite_delta_tenor_v19 as v19


ROOT = Path(__file__).resolve().parent
VERSION = "im_front_month_call_overwrite_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "951f599bb92e8b89c8482a1274ea52b88525a82d156ec1d6a9662c5f754295e0"
UPSTREAM_DIR = ROOT / "outputs" / "im_monthly_discount_roll_v1"
UPSTREAM_DAILY = UPSTREAM_DIR / "daily_nav.csv"
UPSTREAM_SCHEDULE = UPSTREAM_DIR / "roll_schedule.csv"
UPSTREAM_MANIFEST = UPSTREAM_DIR / "data_manifest.json"
CALL_DATA = ROOT / "data" / "im_mo_call_data_build_v1" / "cffex_mo_calls.csv"
CALL_MANIFEST = ROOT / "data" / "im_mo_call_data_build_v1" / "data_manifest.json"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = ROOT / "quant_param_scan_runs" / (
    "20260831_ic_im_rolling_arbitrage_im_front_month_covered_call_v1_"
    "im_front_month_call_overwrite_target_moneyness_0_1p5_3_5"
)

BASELINE = "roll_im_only"
MONEYNESS = {
    "call_atm": 0.0,
    "call_otm_1p5": 0.015,
    "call_otm_3": 0.03,
    "call_otm_5": 0.05,
}
CANDIDATES = (BASELINE, *MONEYNESS)
START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-08-14")
IM_MULTIPLIER = 200.0
MO_MULTIPLIER = 100.0
MO_QTY = 2
IM_ONE_WAY_COST = 0.0001
CALL_BASKET_ONE_WAY_COST = 0.0001
CASH_BASE = 0.70
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen specification hash mismatch")
    if SPEC_HASH.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized parameter-scan folder is missing")
    required = [
        UPSTREAM_DAILY,
        UPSTREAM_SCHEDULE,
        UPSTREAM_MANIFEST,
        CALL_DATA,
        CALL_MANIFEST,
    ]
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    return {str(path.relative_to(ROOT)): sha256(path) for path in required}


def prepare_calls() -> pd.DataFrame:
    calls = pd.read_csv(CALL_DATA, parse_dates=["date"])
    parsed = calls["contract"].str.extract(
        r"^MO(?P<yymm>\d{4})-C-(?P<strike>\d+)$"
    )
    if parsed.isna().any().any():
        raise RuntimeError("Invalid MO Call identifier")
    calls["contract_month"] = pd.to_datetime(
        "20" + parsed["yymm"] + "01", format="%Y%m%d"
    )
    if not np.allclose(calls["strike"], parsed["strike"].astype(float)):
        raise RuntimeError("Call strike and identifier disagree")
    if calls.duplicated(["date", "contract"]).any():
        raise RuntimeError("Duplicate Call quotes")
    return calls.sort_values(
        ["date", "contract_month", "strike", "contract"]
    ).reset_index(drop=True)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = pd.read_csv(UPSTREAM_DAILY, parse_dates=["date"])
    schedule = pd.read_csv(
        UPSTREAM_SCHEDULE,
        parse_dates=["entry_date", "exit_date", "expected_expiry"],
    )
    calls = prepare_calls()
    if len(daily) != 986 or daily["date"].min() != START or daily["date"].max() != END:
        raise RuntimeError("Unexpected frozen IM sample")
    if daily["date"].duplicated().any():
        raise RuntimeError("Duplicate IM dates")
    if not np.allclose(daily["im_net_ret"], (1 + daily["im_gross_ret"]) * (1 - daily["cost_rate"]) - 1):
        raise RuntimeError("IM upstream return identity failed")
    return daily, schedule, calls


@dataclass(frozen=True)
class Selection:
    candidate: str
    target_moneyness: float
    eval_date: pd.Timestamp
    scheduled_execution_date: pd.Timestamp
    im_contract: str
    contract_month: pd.Timestamp
    expiry: pd.Timestamp
    contract: str
    strike: float
    spot: float
    actual_moneyness: float
    eval_close: float
    eval_volume: float
    eval_open_interest: float


@dataclass
class Position:
    selection: Selection
    prior_settle: float


def im_contract_month(contract: str) -> pd.Timestamp:
    if not contract.startswith("IM") or len(contract) != 6:
        raise RuntimeError(f"Invalid IM contract: {contract}")
    return pd.to_datetime("20" + contract[2:] + "01", format="%Y%m%d")


def next_trade_date(dates: pd.DatetimeIndex, day: pd.Timestamp) -> pd.Timestamp:
    location = int(dates.get_indexer([day])[0])
    if location < 0 or location + 1 >= len(dates):
        raise RuntimeError(f"No T+1 date after {day.date()}")
    return pd.Timestamp(dates[location + 1])


def choose_call(
    chain: pd.DataFrame,
    spot: float,
    target_moneyness: float,
) -> pd.Series:
    eligible = chain[
        chain["close"].gt(0)
        & chain["volume"].gt(0)
        & chain["open_interest"].gt(0)
    ].copy()
    if target_moneyness > 0:
        eligible = eligible[eligible["strike"].gt(spot)].copy()
    if eligible.empty:
        raise RuntimeError("No tradable current-month MO Call")
    eligible["actual_moneyness"] = eligible["strike"] / spot - 1.0
    eligible["target_error"] = (
        eligible["actual_moneyness"] - target_moneyness
    ).abs()
    eligible["abs_moneyness"] = eligible["actual_moneyness"].abs()
    return eligible.sort_values(
        [
            "target_error",
            "abs_moneyness",
            "open_interest",
            "volume",
            "strike",
            "contract",
        ],
        ascending=[True, True, False, False, True, True],
    ).iloc[0]


def build_selections(
    daily: pd.DataFrame,
    schedule: pd.DataFrame,
    calls: pd.DataFrame,
    candidate: str,
    target_moneyness: float,
) -> list[Selection]:
    dates = pd.DatetimeIndex(daily["date"])
    spot_lookup = daily.set_index("date")["csi1000_price_close"]
    rows: list[Selection] = []
    for cycle in schedule.itertuples(index=False):
        eval_date = pd.Timestamp(cycle.entry_date)
        if eval_date >= END:
            continue
        month = im_contract_month(str(cycle.contract))
        chain = calls[
            calls["date"].eq(eval_date) & calls["contract_month"].eq(month)
        ]
        spot = float(spot_lookup.loc[eval_date])
        quote = choose_call(chain, spot, target_moneyness)
        rows.append(
            Selection(
                candidate=candidate,
                target_moneyness=target_moneyness,
                eval_date=eval_date,
                scheduled_execution_date=next_trade_date(dates, eval_date),
                im_contract=str(cycle.contract),
                contract_month=month,
                expiry=pd.Timestamp(cycle.exit_date if bool(cycle.complete) else cycle.expected_expiry),
                contract=str(quote["contract"]),
                strike=float(quote["strike"]),
                spot=spot,
                actual_moneyness=float(quote["strike"]) / spot - 1.0,
                eval_close=float(quote["close"]),
                eval_volume=float(quote["volume"]),
                eval_open_interest=float(quote["open_interest"]),
            )
        )
    return rows


def quote_row(
    lookup: pd.DataFrame, contract: str, day: pd.Timestamp
) -> pd.Series | None:
    try:
        row = lookup.loc[(contract, day)]
    except KeyError:
        return None
    if isinstance(row, pd.DataFrame):
        raise RuntimeError("Duplicate quote lookup")
    return row


def run_overlay(
    daily: pd.DataFrame,
    calls: pd.DataFrame,
    selections: list[Selection],
    candidate: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    call_lookup = calls.set_index(["contract", "date"])
    selection_map = {x.scheduled_execution_date: x for x in selections}
    prior_im = daily["settle"].shift(1)
    prior_im.iloc[0] = daily.iloc[0]["settle"]
    active: Position | None = None
    pending: Selection | None = None
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    scheduled_failures = 0
    delayed_days = 0
    dates = pd.DatetimeIndex(daily["date"])

    for index, base in daily.iterrows():
        day = pd.Timestamp(base["date"])
        denominator = float(prior_im.iloc[index])
        pnl = 0.0
        cost = 0.0
        expired_today = False

        if active is not None:
            quote = quote_row(call_lookup, active.selection.contract, day)
            settle_value = float(quote["settle"]) if quote is not None else np.nan
            invalid_settle = (
                not np.isfinite(settle_value)
                or settle_value < 0
                or (settle_value == 0 and day < active.selection.expiry)
            )
            if quote is None or invalid_settle:
                raise RuntimeError(f"Missing active Call settlement: {candidate} {day.date()}")
            current_settle = settle_value
            pnl += (
                MO_QTY
                * MO_MULTIPLIER
                / IM_MULTIPLIER
                * (active.prior_settle - current_settle)
                / denominator
            )
            active.prior_settle = current_settle
            if day >= active.selection.expiry:
                cost += CALL_BASKET_ONE_WAY_COST
                trades.append(
                    {
                        "candidate": candidate,
                        "action": "expire",
                        "eval_date": pd.NaT,
                        "scheduled_execution_date": day,
                        "actual_execution_date": day,
                        "contract": active.selection.contract,
                        "im_contract": active.selection.im_contract,
                        "contract_month": active.selection.contract_month,
                        "strike": active.selection.strike,
                        "trade_close": np.nan,
                        "trade_settle": current_settle,
                        "delay_trading_days": 0,
                    }
                )
                active = None
                expired_today = True

        if day in selection_map:
            if pending is not None:
                raise RuntimeError(f"Unresolved pending Call: {candidate}")
            pending = selection_map[day]

        if pending is not None:
            quote = quote_row(call_lookup, pending.contract, day)
            tradable = (
                quote is not None
                and float(quote["close"]) > 0
                and float(quote["volume"]) > 0
                and float(quote["open_interest"]) > 0
            )
            if tradable:
                if active is not None:
                    raise RuntimeError("Current-month Call overlap")
                close = float(quote["close"])
                settle = float(quote["settle"])
                pnl += (
                    MO_QTY
                    * MO_MULTIPLIER
                    / IM_MULTIPLIER
                    * (close - settle)
                    / denominator
                )
                cost += CALL_BASKET_ONE_WAY_COST
                delay = int(
                    ((dates > pending.scheduled_execution_date) & (dates <= day)).sum()
                )
                delayed_days += delay
                active = Position(pending, settle)
                trades.append(
                    {
                        "candidate": candidate,
                        "action": "open",
                        "eval_date": pending.eval_date,
                        "scheduled_execution_date": pending.scheduled_execution_date,
                        "actual_execution_date": day,
                        "contract": pending.contract,
                        "im_contract": pending.im_contract,
                        "contract_month": pending.contract_month,
                        "strike": pending.strike,
                        "trade_close": close,
                        "trade_settle": settle,
                        "delay_trading_days": delay,
                    }
                )
                pending = None
            elif day == pending.scheduled_execution_date:
                scheduled_failures += 1

        contract = ""
        strike = np.nan
        expiry = pd.NaT
        mark_fraction = 0.0
        margin_fraction = 0.0
        coverage = 0.0
        itm = False
        if active is not None:
            quote = quote_row(call_lookup, active.selection.contract, day)
            if quote is None:
                raise RuntimeError("Missing active EOD quote")
            spot = float(base["csi1000_price_close"])
            settle = float(quote["settle"])
            equivalent_units = MO_QTY * MO_MULTIPLIER / IM_MULTIPLIER
            mark_fraction = equivalent_units * settle / float(base["settle"])
            coverage = equivalent_units * spot / float(base["settle"])
            margin_fraction = v19.call_margin_fraction(
                settle, spot, active.selection.strike, equivalent_units, float(base["settle"])
            )
            contract = active.selection.contract
            strike = active.selection.strike
            expiry = active.selection.expiry
            itm = spot > strike
            if expiry <= day:
                raise RuntimeError("Expired Call remained active")

        rows.append(
            {
                "date": day,
                "candidate": candidate,
                "call_pnl_ret": pnl,
                "call_cost_rate": cost,
                "call_mark_fraction": mark_fraction,
                "call_margin_fraction": margin_fraction,
                "call_coverage": coverage,
                "call_contract": contract,
                "call_strike": strike,
                "call_expiry": expiry,
                "call_itm": itm,
                "expired_today": expired_today,
            }
        )

    return pd.DataFrame(rows), pd.DataFrame(trades), {
        "scheduled_execution_failures": scheduled_failures,
        "delayed_trading_days": delayed_days,
        "unexecuted_final_selection": int(pending is not None),
    }


def baseline_frame(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    frame["candidate"] = BASELINE
    frame["call_pnl_ret"] = 0.0
    frame["call_cost_rate"] = 0.0
    frame["call_mark_fraction"] = 0.0
    frame["call_margin_fraction"] = 0.0
    frame["call_coverage"] = 0.0
    frame["call_contract"] = ""
    frame["call_strike"] = np.nan
    frame["call_expiry"] = pd.NaT
    frame["call_itm"] = False
    frame["expired_today"] = False
    frame["ret"] = (1 + frame["im_gross_ret"]) * (1 - frame["cost_rate"]) - 1
    frame["cash_weight"] = CASH_BASE
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    return finish_path(frame)


def assemble_candidate(
    daily: pd.DataFrame, overlay: pd.DataFrame, candidate: str
) -> pd.DataFrame:
    frame = daily.merge(overlay, on="date", validate="one_to_one")
    frame["candidate"] = candidate
    frame["ret"] = (
        (1 + frame["im_gross_ret"] + frame["call_pnl_ret"])
        * (1 - frame["cost_rate"])
        * (1 - frame["call_cost_rate"])
        - 1
    )
    frame["cash_weight"] = (CASH_BASE - frame["call_margin_fraction"]).clip(lower=0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    return finish_path(frame)


def finish_path(frame: pd.DataFrame) -> pd.DataFrame:
    if frame[["ret", "cash_ret"]].isna().any().any() or (frame[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid strategy return")
    frame["nav"] = (1 + frame["ret"]).cumprod()
    frame["cash_nav"] = (1 + frame["cash_ret"]).cumprod()
    frame["cash_drawdown"] = frame["cash_nav"] / frame["cash_nav"].cummax() - 1
    return frame


def metric(returns: pd.Series) -> dict[str, float]:
    values = returns.astype(float)
    wealth = (1 + values).cumprod()
    years = len(values) / 252.0
    vol = float(values.std(ddof=1) * math.sqrt(252)) if len(values) > 1 else np.nan
    ann = float(wealth.iloc[-1] ** (1 / years) - 1) if years > 0 else np.nan
    max_dd = float((wealth / wealth.cummax() - 1).min())
    return {
        "total_return": float(wealth.iloc[-1] - 1),
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe_repo": float(values.mean() / values.std(ddof=1) * math.sqrt(252))
        if len(values) > 1 and values.std(ddof=1) > 0
        else np.nan,
        "max_dd": max_dd,
        "calmar": ann / abs(max_dd) if max_dd < 0 else np.nan,
    }


def metric_tables(paths: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    annual: list[dict[str, Any]] = []
    for candidate, group in paths.groupby("candidate", sort=False):
        group = group.sort_values("date")
        start, end = group["date"].min(), group["date"].max()
        for segment, offset in WINDOWS.items():
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"].ge(requested)] if available else group.iloc[0:0]
            item: dict[str, Any] = {
                "candidate": candidate,
                "segment": segment,
                "target_moneyness": MONEYNESS.get(candidate, np.nan),
                "available": available,
                "start": sample["date"].min() if available else pd.NaT,
                "end": end,
                "rows": len(sample),
            }
            item.update(metric(sample["cash_ret"]) if available else {
                key: np.nan for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "calmar"]
            })
            rows.append(item)
        for year, sample in group.groupby(group["date"].dt.year):
            annual.append({"candidate": candidate, "year": int(year), **metric(sample["cash_ret"])})
    long = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in long.groupby("candidate", sort=False):
        item = {
            "candidate": candidate,
            "target_moneyness": MONEYNESS.get(candidate, np.nan),
        }
        for row in group.itertuples(index=False):
            item[f"ann_return_{row.segment}"] = row.ann_return
            item[f"max_dd_{row.segment}"] = row.max_dd
            item[f"sharpe_repo_{row.segment}"] = row.sharpe_repo
        wide_rows.append(item)
    return long, pd.DataFrame(wide_rows), pd.DataFrame(annual)


def selection_frame(items: list[Selection]) -> pd.DataFrame:
    return pd.DataFrame([x.__dict__ for x in items]).assign(
        moneyness_error=lambda x: (x.actual_moneyness - x.target_moneyness).abs()
    )


def exposure_table(
    paths: pd.DataFrame, selections: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    for candidate, group in paths.groupby("candidate", sort=False):
        selected = selections[selections["candidate"].eq(candidate)] if len(selections) else selections
        trade = trades[trades["candidate"].eq(candidate)] if len(trades) else trades
        held = group["call_contract"].fillna("").ne("")
        rows.append(
            {
                "candidate": candidate,
                "target_moneyness": MONEYNESS.get(candidate, np.nan),
                "selection_rows": len(selected),
                "open_events": int(trade["action"].eq("open").sum()) if len(trade) else 0,
                "expiry_events": int(trade["action"].eq("expire").sum()) if len(trade) else 0,
                "call_days": int(held.sum()),
                "call_day_ratio": float(held.mean()),
                "median_moneyness_error": float(selected["moneyness_error"].median()) if len(selected) else np.nan,
                "max_moneyness_error": float(selected["moneyness_error"].max()) if len(selected) else np.nan,
                "minimum_eval_volume": float(selected["eval_volume"].min()) if len(selected) else np.nan,
                "minimum_eval_open_interest": float(selected["eval_open_interest"].min()) if len(selected) else np.nan,
                "itm_days": int(group["call_itm"].sum()),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "average_margin_fraction": float(group["call_margin_fraction"].mean()),
                "maximum_margin_fraction": float(group["call_margin_fraction"].max()),
                "average_coverage_when_held": float(group.loc[held, "call_coverage"].mean()) if held.any() else np.nan,
                "capital_breach_days": int((group["call_margin_fraction"] > CASH_BASE + 1e-12).sum()),
            }
        )
    return pd.DataFrame(rows)


def audit(
    upstream: pd.DataFrame,
    paths: pd.DataFrame,
    selections: pd.DataFrame,
    trades: pd.DataFrame,
    calls: pd.DataFrame,
    runtime: dict[str, dict[str, int]],
) -> dict[str, Any]:
    base = paths[paths["candidate"].eq(BASELINE)].sort_values("date")
    baseline_parity = float(np.max(np.abs(base["ret"].to_numpy() - upstream["im_net_ret"].to_numpy())))
    candidates = paths[paths["candidate"].ne(BASELINE)].copy()
    expected = (
        (1 + candidates["im_gross_ret"] + candidates["call_pnl_ret"])
        * (1 - candidates["cost_rate"])
        * (1 - candidates["call_cost_rate"])
        - 1
    )
    expected_cash = candidates["ret"] + (CASH_BASE - candidates["call_margin_fraction"]).clip(lower=0) * CASH_DAILY
    month_errors = int((selections["contract_month"] != selections["im_contract"].map(im_contract_month)).sum())
    causality = int((selections["eval_date"] >= selections["scheduled_execution_date"]).sum())
    opens = trades[trades["action"].eq("open")]
    causality += int((opens["eval_date"] >= opens["actual_execution_date"]).sum())
    causality += int((opens["actual_execution_date"] < opens["scheduled_execution_date"]).sum())
    lookup = calls.set_index(["contract", "date"])
    close_error = 0.0
    for row in opens.itertuples(index=False):
        quote = lookup.loc[(row.contract, row.actual_execution_date)]
        close_error = max(close_error, abs(float(quote["close"]) - float(row.trade_close)))
    expiry_hold_errors = int(
        paths[
            paths["call_contract"].fillna("").ne("")
            & (pd.to_datetime(paths["call_expiry"]) <= paths["date"])
        ].shape[0]
    )
    result = {
        "baseline_im_net_parity_max_abs": baseline_parity,
        "return_identity_max_abs": float((candidates["ret"] - expected).abs().max()),
        "cash_identity_max_abs": float((candidates["cash_ret"] - expected_cash).abs().max()),
        "month_match_errors": month_errors,
        "causality_errors": causality,
        "official_close_max_abs_error": close_error,
        "expiry_hold_errors": expiry_hold_errors,
        "runtime": runtime,
    }
    result["all_pass"] = bool(
        baseline_parity <= 1e-15
        and result["return_identity_max_abs"] <= 1e-15
        and result["cash_identity_max_abs"] <= 1e-15
        and month_errors == 0
        and causality == 0
        and close_error <= 1e-12
        and expiry_hold_errors == 0
        and all(x["unexecuted_final_selection"] == 0 for x in runtime.values())
    )
    return result


def metric_lookup(long: pd.DataFrame, candidate: str, segment: str, field: str) -> float:
    row = long[long["candidate"].eq(candidate) & long["segment"].eq(segment)]
    return float(row.iloc[0][field])


def decide(
    long: pd.DataFrame, exposure: pd.DataFrame, audit_result: dict[str, Any]
) -> tuple[pd.DataFrame, str, str, str]:
    base = {
        segment: {
            field: metric_lookup(long, BASELINE, segment, field)
            for field in ["ann_return", "max_dd"]
        }
        for segment in ["full", "last_3y", "last_1y"]
    }
    rows = []
    for candidate in MONEYNESS:
        improvements = {
            segment: metric_lookup(long, candidate, segment, "ann_return") - base[segment]["ann_return"]
            for segment in base
        }
        maxdd_delta = metric_lookup(long, candidate, "full", "max_dd") - base["full"]["max_dd"]
        breaches = int(exposure.loc[exposure["candidate"].eq(candidate), "capital_breach_days"].iloc[0])
        hard_pass = bool(
            improvements["full"] >= 0
            and improvements["last_3y"] >= 0
            and improvements["last_1y"] >= -0.01
            and maxdd_delta >= -0.02
            and breaches == 0
            and audit_result["all_pass"]
        )
        rows.append(
            {
                "candidate": candidate,
                "target_moneyness": MONEYNESS[candidate],
                "cagr_improvement_full": improvements["full"],
                "cagr_improvement_last_3y": improvements["last_3y"],
                "cagr_improvement_last_1y": improvements["last_1y"],
                "minimum_cagr_improvement": min(improvements.values()),
                "full_max_dd_delta": maxdd_delta,
                "capital_breach_days": breaches,
                "hard_pass": hard_pass,
            }
        )
    table = pd.DataFrame(rows)
    passed = table[table["hard_pass"]]
    if passed.empty:
        return table, "keep_default", "reject", BASELINE
    selected = passed.sort_values(
        ["minimum_cagr_improvement", "cagr_improvement_full", "full_max_dd_delta", "target_moneyness"],
        ascending=[False, False, False, True],
    ).iloc[0]["candidate"]
    pass_positions = [i for i, c in enumerate(MONEYNESS) if c in set(passed["candidate"])]
    adjacent = any(b - a == 1 for a, b in zip(pass_positions, pass_positions[1:]))
    stability = "narrow_stable" if adjacent else "peak_only"
    return table, "watchlist", stability, str(selected)


def pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def record_text(
    long: pd.DataFrame,
    exposure: pd.DataFrame,
    decision_table: pd.DataFrame,
    decision: str,
    stability: str,
    selected: str,
    audit_result: dict[str, Any],
    source_hashes: dict[str, str],
) -> str:
    lines = [
        "# IM 当月备兑 Call 虚值度对照 v1",
        "",
        f"Decision: `{decision}`；机械最佳：`{selected}`；未批准实盘。",
        f"Stability: `{stability}`。",
        f"Data: 中金所官方 IM/MO，{START.date()}—{END.date()}，986 个交易日。",
        "",
        "## 口径",
        "",
        "- 基准为固定1倍、持有当月IM至到期并逐月滚动；候选每1张IM卖2张同月份MO Call。",
        "- Call在周期进入日T收盘按中证1000价格指数选择，T+1官方收盘执行，持有至当月到期结算。",
        "- 主结果含IM成本、Call成本，以及30%保证金/缓冲后可计息现金；Call保证金从70%现金中扣除。",
        "- 不含Put、网格、IV门槛、止盈、救援和远月；本研究不改变冻结V2主线。",
        "",
        "## 同窗收益（CAGR / MaxDD）",
        "",
        "|候选|full|10Y|5Y|3Y|1Y|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for candidate in CANDIDATES:
        cells = []
        for segment in WINDOWS:
            row = long[(long["candidate"].eq(candidate)) & (long["segment"].eq(segment))].iloc[0]
            cells.append(f"{pct(row.ann_return)} / {pct(row.max_dd)}")
        lines.append("|" + candidate + "|" + "|".join(cells) + "|")
    lines += [
        "",
        "## 机制质量",
        "",
        exposure.to_markdown(index=False),
        "",
        "## 预注册门槛",
        "",
        decision_table.to_markdown(index=False),
        "",
        "## 审计",
        "",
        "```json",
        json.dumps(audit_result, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## 数据与复现",
        "",
        f"- 规格SHA-256：`{SPEC_SHA256}`。",
        f"- 源文件哈希：`{json.dumps(source_hashes, ensure_ascii=False)}`。",
        "- 命令：`python im_front_month_call_overwrite_v1.py`。",
        "- 真实历史不足5年，因此5Y/10Y为N/A；不使用理论期权或代理历史延长正式收益。",
        "- 结算价不保证可成交，未计盘口冲击、专项行权费和经纪商压力保证金。",
        "",
        "本结果是研究审计证据，不构成交易建议或实盘授权。",
        "",
    ]
    return "\n".join(lines)


def update_scan(
    long: pd.DataFrame,
    wide: pd.DataFrame,
    record: str,
    decision: str,
    stability: str,
    selected: str,
    source_hashes: dict[str, str],
) -> None:
    long.to_csv(SCAN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(SCAN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "single_parameter",
            "baseline": {"candidate": BASELINE, "target_moneyness": None},
            "candidate_grid": [
                {"candidate": c, "target_moneyness": m} for c, m in MONEYNESS.items()
            ],
            "data_snapshot": {
                "source": "CFFEX official frozen local IM/MO data",
                "start": str(START.date()),
                "end": str(END.date()),
                "rows": 986,
                "timezone": "Asia/Shanghai",
                "adjustment_mode": "official settlement/index close; no adjustment",
                "source_hashes": source_hashes,
            },
            "cost_model": {
                "im_one_way": IM_ONE_WAY_COST,
                "call_basket_one_way": CALL_BASKET_ONE_WAY_COST,
                "cash_base": CASH_BASE,
                "cash_annual": 0.03,
                "execution": "T close select, T+1 official close fill, hold Call to expiry",
            },
            "selected_candidate": selected,
            "decision": decision,
            "stability_label": stability,
            "warnings": [
                "5Y and 10Y are unavailable because real IM/MO history is shorter than five years",
                "frozen local official data ends 2026-08-14",
            ],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython im_front_month_call_overwrite_v1.py\n")


def main() -> None:
    source_hashes = verify_inputs()
    upstream, schedule, calls = load_inputs()
    all_paths = [baseline_frame(upstream)]
    all_selections: list[Selection] = []
    trade_frames = []
    runtime: dict[str, dict[str, int]] = {}
    for candidate, target in MONEYNESS.items():
        selections = build_selections(upstream, schedule, calls, candidate, target)
        overlay, trades, stats = run_overlay(upstream, calls, selections, candidate)
        all_paths.append(assemble_candidate(upstream, overlay, candidate))
        all_selections.extend(selections)
        trade_frames.append(trades)
        runtime[candidate] = stats
    paths = pd.concat(all_paths, ignore_index=True)
    selections = selection_frame(all_selections)
    trades = pd.concat(trade_frames, ignore_index=True)
    long, wide, annual = metric_tables(paths)
    exposure = exposure_table(paths, selections, trades)
    audit_result = audit(upstream, paths, selections, trades, calls, runtime)
    decision_table, decision, stability, selected = decide(long, exposure, audit_result)
    record = record_text(
        long, exposure, decision_table, decision, stability, selected, audit_result, source_hashes
    )

    STAGING.mkdir(parents=True)
    paths.to_csv(STAGING / "daily_paths.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    selections.to_csv(STAGING / "selections.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(STAGING / "trades.csv", index=False, encoding="utf-8-sig")
    long.to_csv(STAGING / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(STAGING / "window_metrics.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(STAGING / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(STAGING / "exposure_quality.csv", index=False, encoding="utf-8-sig")
    decision_table.to_csv(STAGING / "decision_table.csv", index=False, encoding="utf-8-sig")
    (STAGING / "audit_summary.json").write_text(
        json.dumps(audit_result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STAGING / "data_manifest.json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "generated_at": datetime.now().astimezone().isoformat(),
                "status": "research_only_not_approved_for_live_trading",
                "sample": {"start": str(START.date()), "end": str(END.date()), "rows": 986},
                "source_hashes": source_hashes,
                "spec_sha256": SPEC_SHA256,
                "script_sha256": sha256(Path(__file__)),
                "git_commit": git_value("rev-parse", "HEAD"),
                "git_status": git_value("status", "--short"),
                "decision": decision,
                "stability_label": stability,
                "selected_candidate": selected,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python im_front_month_call_overwrite_v1.py\n", encoding="utf-8"
    )
    if not audit_result["all_pass"]:
        raise RuntimeError("Formal audit failed")
    STAGING.rename(OUTPUT)
    update_scan(long, wide, record, decision, stability, selected, source_hashes)
    print(record)


if __name__ == "__main__":
    main()
