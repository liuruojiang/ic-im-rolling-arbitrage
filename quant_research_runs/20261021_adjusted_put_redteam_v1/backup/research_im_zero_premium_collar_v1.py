from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "docs/im_zero_premium_collar_v1_spec.md"
OUTPUT = ROOT / "outputs/im_zero_premium_collar_v1"
SOURCES = {
    "im": ROOT / "data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv",
    "put": ROOT / "data/im_monthly_roll_3m_lowest_put_v1/cffex_mo_puts.csv",
    "call": ROOT / "data/im_mo_call_data_build_v1/cffex_mo_calls.csv",
    "spot": ROOT / "data/im_monthly_discount_roll_v1/csindex_000852.csv",
    "schedule": ROOT / "outputs/im_monthly_discount_roll_v1/roll_schedule.csv",
    "continuous_im": ROOT / "outputs/im_monthly_discount_roll_v1/daily_nav.csv",
}
ARMS = ("matched_im", "matched_put", "matched_call", "matched_collar")
FUTURE_SIDE_COST = 0.0001
OPTION_SIDE_COST = 0.0005
MAX_PREMIUM_GAP = 0.001
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    data = {name: pd.read_csv(path, parse_dates=["date"]) for name, path in SOURCES.items() if name not in ("schedule", "continuous_im")}
    schedule = pd.read_csv(SOURCES["schedule"], parse_dates=["entry_date", "exit_date", "expected_expiry"])
    for name in ("im", "put", "call"):
        frame = data[name]
        if frame.duplicated(["date", "contract"]).any():
            raise RuntimeError(f"Duplicate {name} date-contract source rows")
        if not frame["date"].is_monotonic_increasing:
            frame.sort_values(["date", "contract"], inplace=True)
        data[name] = frame.set_index(["date", "contract"], drop=False)
    spot = data["spot"]
    if spot["date"].duplicated().any() or (spot["close"] <= 0).any():
        raise RuntimeError("Invalid spot data")
    data["spot"] = spot.set_index("date", drop=False)
    return data, schedule


def quote(data: pd.DataFrame, day: pd.Timestamp, contract: str) -> pd.Series | None:
    try:
        row = data.loc[(day, contract)]
    except KeyError:
        return None
    if isinstance(row, pd.DataFrame):
        raise RuntimeError(f"Duplicate quote {contract} {day.date()}")
    return row


def liquid(row: pd.Series | None) -> bool:
    return bool(
        row is not None
        and np.isfinite(row["close"])
        and row["close"] > 0
        and row["volume"] > 0
        and row["open_interest"] > 0
    )


def select_cycle(
    data: dict[str, pd.DataFrame],
    trade_dates: pd.DatetimeIndex,
    signal: pd.Timestamp,
    entry: pd.Timestamp,
    exit_day: pd.Timestamp,
    future_contract: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "future_contract": future_contract,
        "signal_date": signal,
        "entry_date": entry,
        "expiry_date": exit_day,
        "status": "",
    }

    def reject(reason: str) -> dict[str, object]:
        result["status"] = reason
        return result

    if signal not in data["spot"].index or entry not in data["spot"].index:
        return reject("missing_spot")
    spot_t = float(data["spot"].loc[signal, "close"])
    spot_entry = float(data["spot"].loc[entry, "close"])
    result["spot_signal"] = spot_t
    result["spot_entry"] = spot_entry
    future_signal = quote(data["im"], signal, future_contract)
    future_entry = quote(data["im"], entry, future_contract)
    if not liquid(future_signal) or not liquid(future_entry):
        return reject("missing_liquid_future")
    result["future_signal_close"] = float(future_signal["close"])
    result["future_entry_close"] = float(future_entry["close"])
    result["entry_discount_points"] = spot_entry - float(future_entry["close"])

    month_code = future_contract[2:]
    put_chain = data["put"].loc[signal].reset_index(drop=True)
    call_chain = data["call"].loc[signal].reset_index(drop=True)
    put_chain = put_chain[
        put_chain["contract"].str.startswith(f"MO{month_code}-P-")
        & (put_chain["strike"] < spot_t)
        & (put_chain["close"] > 0)
        & (put_chain["volume"] > 0)
        & (put_chain["open_interest"] > 0)
    ].copy()
    if put_chain.empty:
        return reject("no_liquid_otm_put_signal")
    put_chain["target_error"] = (put_chain["strike"] - 0.95 * spot_t).abs()
    put_choice = put_chain.sort_values(["target_error", "strike", "contract"], ascending=[True, False, True]).iloc[0]
    call_chain = call_chain[
        call_chain["contract"].str.startswith(f"MO{month_code}-C-")
        & (call_chain["strike"] > spot_t)
        & (call_chain["close"] > 0)
        & (call_chain["volume"] > 0)
        & (call_chain["open_interest"] > 0)
    ].copy()
    if call_chain.empty:
        return reject("no_liquid_otm_call_signal")
    call_chain["premium_error"] = (call_chain["close"] - float(put_choice["close"])).abs()
    call_choice = call_chain.sort_values(["premium_error", "strike", "contract"], ascending=[True, False, True]).iloc[0]
    pcode, ccode = str(put_choice["contract"]), str(call_choice["contract"])
    pentry, centry = quote(data["put"], entry, pcode), quote(data["call"], entry, ccode)
    result.update(
        put_contract=pcode,
        call_contract=ccode,
        put_strike=float(put_choice["strike"]),
        call_strike=float(call_choice["strike"]),
        put_signal_close=float(put_choice["close"]),
        call_signal_close=float(call_choice["close"]),
        signal_premium_gap_points=float(call_choice["close"] - put_choice["close"]),
    )
    if not liquid(pentry) or not liquid(centry):
        return reject("missing_liquid_options_entry")
    pclose, cclose = float(pentry["close"]), float(centry["close"])
    gap_fraction = (cclose - pclose) / float(future_entry["close"])
    result.update(
        put_entry_close=pclose,
        call_entry_close=cclose,
        put_entry_volume=int(pentry["volume"]),
        call_entry_volume=int(centry["volume"]),
        put_entry_open_interest=int(pentry["open_interest"]),
        call_entry_open_interest=int(centry["open_interest"]),
        entry_premium_gap_points=cclose - pclose,
        entry_premium_gap_fraction=gap_fraction,
    )
    if not (float(put_choice["strike"]) < spot_entry < float(call_choice["strike"])):
        return reject("not_both_otm_at_entry")
    if abs(gap_fraction) > MAX_PREMIUM_GAP:
        return reject("premium_gap_over_10bp")
    active_days = trade_dates[(trade_dates > entry) & (trade_dates <= exit_day)]
    if len(active_days) == 0:
        return reject("no_holding_days")
    for day in active_days:
        rows = (quote(data["im"], day, future_contract), quote(data["put"], day, pcode), quote(data["call"], day, ccode))
        if any(row is None or not np.isfinite(row["settle"]) or row["settle"] < 0 for row in rows):
            return reject("missing_future_daily_mark")
        if rows[0]["settle"] <= 0:
            return reject("invalid_future_daily_mark")
    result["status"] = "selected"
    result["holding_days"] = len(active_days)
    return result


def call_margin(mark: float, spot: float, strike: float) -> float:
    # Same research approximation as im_mo_call_overwrite_delta_tenor_v19.call_margin_fraction.
    return 200.0 * (mark + max(0.12 * spot - max(strike - spot, 0.0), 0.07 * spot))


def active_arm_rows(
    data: dict[str, pd.DataFrame],
    cycle: dict[str, object],
    trade_dates: pd.DatetimeIndex,
    arm: str,
) -> list[dict[str, object]]:
    entry = pd.Timestamp(cycle["entry_date"])
    expiry = pd.Timestamp(cycle["expiry_date"])
    dates = trade_dates[(trade_dates >= entry) & (trade_dates <= expiry)]
    fcode, pcode, ccode = str(cycle["future_contract"]), str(cycle["put_contract"]), str(cycle["call_contract"])
    has_put = arm in ("matched_put", "matched_collar")
    has_call = arm in ("matched_call", "matched_collar")
    fprev = float(cycle["future_entry_close"])
    pprev = float(cycle["put_entry_close"])
    cprev = float(cycle["call_entry_close"])
    notional = 200.0 * fprev
    paid_put = 200.0 * pprev if has_put else 0.0
    received_call = 200.0 * cprev if has_call else 0.0
    cum_pnl = 0.0
    rows: list[dict[str, object]] = []
    for day in dates:
        is_entry = day == entry
        is_expiry = day == expiry
        spot = float(data["spot"].loc[day, "close"])
        margin = call_margin(cprev, spot, float(cycle["call_strike"])) if has_call else 0.0
        free_cash = 0.70 * notional - paid_put + received_call - margin
        if is_entry:
            fmark, pmark, cmark = fprev, pprev, cprev
            future_pnl = put_pnl = call_pnl = 0.0
        else:
            fmark = float(quote(data["im"], day, fcode)["settle"])
            pmark = float(quote(data["put"], day, pcode)["settle"])
            cmark = float(quote(data["call"], day, ccode)["settle"])
            future_pnl = 200.0 * (fmark - fprev)
            put_pnl = 200.0 * (pmark - pprev) if has_put else 0.0
            call_pnl = -200.0 * (cmark - cprev) if has_call else 0.0
        future_cost = notional * FUTURE_SIDE_COST * (int(is_entry) + int(is_expiry))
        put_cost = notional * OPTION_SIDE_COST * (int(is_entry) + int(is_expiry)) if has_put else 0.0
        call_cost = notional * OPTION_SIDE_COST * (int(is_entry) + int(is_expiry)) if has_call else 0.0
        cash_pnl = max(free_cash, 0.0) * CASH_DAILY
        total_pnl = future_pnl + put_pnl + call_pnl + cash_pnl - future_cost - put_cost - call_cost
        prior_equity = notional + cum_pnl
        if prior_equity <= 0:
            raise RuntimeError(f"Nonpositive equity {arm} {day.date()}")
        day_return = total_pnl / prior_equity
        cum_pnl += total_pnl
        rows.append(
            dict(date=day, arm=arm, active=True, future_contract=fcode, put_contract=pcode if has_put else "", call_contract=ccode if has_call else "",
                 future_pnl=future_pnl, put_pnl=put_pnl, call_pnl=call_pnl, cash_pnl=cash_pnl,
                 future_cost=future_cost, put_cost=put_cost, call_cost=call_cost, total_pnl=total_pnl,
                 free_cash=free_cash, call_margin=margin, notional=notional,
                 return_net=day_return, cycle_cum_return=cum_pnl / notional)
        )
        fprev, pprev, cprev = fmark, pmark, cmark
    return rows


def metrics(daily: pd.DataFrame) -> pd.DataFrame:
    records = []
    for arm, group in daily.groupby("arm", sort=True):
        group = group.sort_values("date")
        ret = group["return_net"].to_numpy(float)
        nav = group["nav"].to_numpy(float)
        vol = float(np.std(ret, ddof=1) * math.sqrt(252.0))
        cagr = float(nav[-1] ** (252.0 / len(nav)) - 1.0)
        peaks = np.maximum.accumulate(np.r_[1.0, nav])[1:]
        mdd = float(np.min(nav / peaks - 1.0))
        records.append(dict(arm=arm, start=group["date"].min().date().isoformat(), end=group["date"].max().date().isoformat(), days=len(group),
                            total_return=float(nav[-1] - 1.0), cagr=cagr, annual_volatility=vol,
                            max_drawdown=mdd, sharpe_0rf=float(np.mean(ret) * math.sqrt(252.0) / np.std(ret, ddof=1)) if vol > 0 else np.nan,
                            return_drawdown_ratio=cagr / abs(mdd) if mdd < 0 else np.nan,
                            max_capital_occupancy=float((1.0 - group.loc[group["active"], "free_cash"] / group.loc[group["active"], "notional"]).max()),
                            negative_free_cash_days=int((group["free_cash"] < 0).sum())))
    return pd.DataFrame(records)


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Research output already exists: {OUTPUT}")
    data, schedule = load()
    source_hashes = {name: sha256(path) for name, path in SOURCES.items()}
    all_dates = pd.DatetimeIndex(sorted(data["spot"].index))
    if not all_dates.equals(pd.DatetimeIndex(sorted(set(data["im"].index.get_level_values("date"))))):
        raise RuntimeError("IM and spot trade date misalignment")
    completed = schedule[schedule["complete"].eq(True)].sort_values("exit_date")
    last_day = pd.Timestamp(completed["exit_date"].max())
    dates = all_dates[all_dates <= last_day]
    selections = []
    prior_expiry: pd.Timestamp | None = None
    for row in completed.itertuples(index=False):
        signal = pd.Timestamp(dates[0]) if prior_expiry is None else prior_expiry
        next_dates = dates[dates > signal]
        if len(next_dates) == 0:
            break
        entry = pd.Timestamp(next_dates[0])
        expiry = pd.Timestamp(row.exit_date)
        if entry >= expiry:
            raise RuntimeError(f"No entry before expiry for {row.contract}")
        selections.append(select_cycle(data, dates, signal, entry, expiry, str(row.contract)))
        prior_expiry = expiry
    cycles = pd.DataFrame(selections)
    active: dict[tuple[pd.Timestamp, str], dict[str, object]] = {}
    for cycle in selections:
        if cycle["status"] != "selected":
            continue
        for arm in ARMS:
            for row in active_arm_rows(data, cycle, dates, arm):
                key = (pd.Timestamp(row["date"]), arm)
                if key in active:
                    raise RuntimeError(f"Overlapping cycle {key}")
                active[key] = row

    daily_rows = []
    navs = {arm: 1.0 for arm in ARMS}
    for day in dates:
        for arm in ARMS:
            row = active.get((day, arm))
            if row is None:
                row = dict(date=day, arm=arm, active=False, future_contract="", put_contract="", call_contract="",
                           future_pnl=0.0, put_pnl=0.0, call_pnl=0.0, cash_pnl=CASH_DAILY,
                           future_cost=0.0, put_cost=0.0, call_cost=0.0, total_pnl=CASH_DAILY,
                           free_cash=1.0, call_margin=0.0, notional=1.0, return_net=CASH_DAILY, cycle_cum_return=np.nan)
            navs[arm] *= 1.0 + float(row["return_net"])
            row["nav"] = navs[arm]
            daily_rows.append(row)
    daily = pd.DataFrame(daily_rows)
    summary = metrics(daily)
    reference = pd.read_csv(SOURCES["continuous_im"], parse_dates=["date"])
    reference = reference[reference["date"].isin(dates)].sort_values("date")
    if len(reference) != len(dates):
        raise RuntimeError("Continuous IM reference missing dates")
    reference_nav = (1.0 + reference["im_net_plus_cash_ret"]).cumprod()
    reference_summary = dict(source="frozen_monthly_continuous_im_different_entry_clock", start=dates[0].date().isoformat(),
                             end=dates[-1].date().isoformat(), days=len(dates), total_return=float(reference_nav.iloc[-1] - 1.0),
                             cagr=float(reference_nav.iloc[-1] ** (252.0 / len(dates)) - 1.0),
                             max_drawdown=float((reference_nav / reference_nav.cummax() - 1.0).min()))

    OUTPUT.mkdir(parents=True)
    cycles.to_csv(OUTPUT / "cycles.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(OUTPUT / "daily.csv.gz", index=False, compression="gzip")
    summary.to_csv(OUTPUT / "metrics.csv", index=False, encoding="utf-8-sig")
    detail = {
        "status": "research_diagnostic_pending_multi_agent_audit",
        "execution": "official_close_settle_proxy_not_executable_fill",
        "input_sha256": source_hashes,
        "spec_sha256": sha256(SPEC),
        "script_sha256": sha256(Path(__file__)),
        "selected_cycles": int(cycles["status"].eq("selected").sum()),
        "total_complete_cycles": len(cycles),
        "status_counts": {str(k): int(v) for k, v in cycles["status"].value_counts().items()},
        "reference": reference_summary,
        "observed_premium_gap_max_abs_fraction": float(cycles.loc[cycles["status"].eq("selected"), "entry_premium_gap_fraction"].abs().max()),
    }
    (OUTPUT / "run.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "metrics": summary.to_dict("records"), "run": detail}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
