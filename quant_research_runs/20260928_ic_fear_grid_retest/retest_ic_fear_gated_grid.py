from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FEAR_PATH = ROOT / "quant_research_runs" / "20260928_csi1000_fear_greed_reproduction" / "inputs" / "fear_greed_full.csv"
TARGET_PATH = ROOT / "outputs" / "ic_mainline_v1_3" / "target_schedule.csv.gz"
FORMAL_PATH = ROOT / "quant_param_scan_runs" / "20260904_ic_v13_full_roll_tenor_timing_v2" / "candidate_checkpoints" / "quarter_T3_fixed.csv.gz"
FUTURES_PATH = ROOT / "data" / "ic_monthly_discount_roll_v1" / "cffex_ic_contracts.csv"
OFFICIAL_DAILY_PATH = ROOT / "outputs" / "nav_r7_complete_refresh_20260912" / "ic_full_daily.csv.gz"
OWN_GRID_DAILY_PATH = ROOT / "quant_param_scan_runs" / "20260913_ic_grid_own_calibration" / "daily_candidates.csv.gz"
OWN_GRID_EVENTS_PATH = ROOT / "quant_param_scan_runs" / "20260913_ic_grid_own_calibration" / "trade_events.csv"

FEAR_START = pd.Timestamp("2022-07-22")
REAL_START = pd.Timestamp("2022-09-19")
ENTRY = 0.5
EXIT = 1.0
GRID_SIZE = 0.5
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_DAILY = (1.03 ** (1.0 / 252.0)) - 1.0
TOL = 1e-12


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path, **kwargs: Any) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False, **kwargs)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def simulate_grid(
    market: pd.DataFrame, *, fear_gate: bool, entry: float = ENTRY, exit: float = EXIT
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay the project's T-close / next-open grid engine with an optional fear entry gate."""
    state = False
    pending: dict[str, Any] | None = None
    cycle = 0
    active_cycle = 0
    dates = list(pd.DatetimeIndex(market["date"]))
    daily: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for index, row in enumerate(market.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        held_before = state
        buy = False
        sell = False
        signal_date = pd.NaT
        signal_score = np.nan
        signal_fear = np.nan
        if pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            signal_date = pd.Timestamp(pending["signal_date"])
            signal_score = float(pending["signal_score"])
            signal_fear = float(pending["fear_score"]) if pd.notna(pending["fear_score"]) else np.nan
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError(f"Duplicate IC grid buy on {day.date()}")
                state = True
                buy = True
                cycle += 1
                active_cycle = cycle
            else:
                if not state:
                    raise RuntimeError(f"IC grid sell while flat on {day.date()}")
                state = False
                sell = True
            pending = None

        held_eod = state
        if held_before and held_eod:
            gross = float(row.futures_gross_ret) if bool(row.roll_event) else float(row.settle) / float(row.pre_settle) - 1.0
        elif not held_before and held_eod:
            gross = float(row.settle) / float(row.open) - 1.0
        elif held_before and not held_eod:
            gross = float(row.open) / float(row.pre_settle) - 1.0
        else:
            gross = 0.0
        trade_cost = ONE_WAY_COST * (int(buy) + int(sell))
        roll_cost = 2.0 * ONE_WAY_COST if held_eod and bool(row.roll_event) else 0.0
        cost = trade_cost + roll_cost
        active_cycle_today = active_cycle if (held_before or held_eod or buy or sell) else 0

        if buy or sell:
            events.append(
                {
                    "path": "fear25_confirmed" if fear_gate else "valuation_only",
                    "action": "buy" if buy else "sell",
                    "signal_date": signal_date.date().isoformat(),
                    "signal_ic_score": signal_score,
                    "signal_fear_score": signal_fear,
                    "execution_date": day.date().isoformat(),
                    "contract": row.contract,
                    "execution_open": float(row.open),
                    "cycle_id": active_cycle_today,
                }
            )

        score = float(row.valuation_score)
        fear = float(row.fear_greed_index) if pd.notna(row.fear_greed_index) else np.nan
        low_score = score <= entry + TOL
        fear_allowed = pd.notna(fear) and fear <= 25.0
        blocked_by_fear = bool(not state and low_score and fear_gate and not fear_allowed)
        if sell:
            active_cycle = 0
        if pending is None:
            action = "buy" if (not state and low_score and (not fear_gate or fear_allowed)) else None
            if state and score >= exit - TOL:
                action = "sell"
            if action is not None and index + 1 < len(dates):
                pending = {
                    "action": action,
                    "signal_date": day,
                    "signal_score": score,
                    "fear_score": fear,
                    "execution_date": dates[index + 1],
                }

        daily.append(
            {
                "date": day,
                "path": "fear25_confirmed" if fear_gate else "valuation_only",
                "ic_valuation_score": score,
                "fear_greed_index": fear,
                "fear_gate_blocked_entry_day": int(blocked_by_fear),
                "held_before": int(held_before),
                "held_eod": int(held_eod),
                "buy": int(buy),
                "sell": int(sell),
                "gross_return_1x": gross,
                "cost_rate_1x": cost,
                "grid_net_increment_1x": (1.0 + gross) * (1.0 - cost) - 1.0,
                "cycle_id": active_cycle_today,
                "contract": row.contract,
                "open": float(row.open),
                "settle": float(row.settle),
                "pre_settle": float(row.pre_settle),
                "roll_event": int(bool(row.roll_event)),
            }
        )

    return pd.DataFrame(daily), pd.DataFrame(events)


def compose_portfolio(base: pd.DataFrame, grid: pd.DataFrame, *, candidate: str, size: float) -> pd.DataFrame:
    result = base.merge(
        grid[["date", "held_eod", "buy", "sell", "gross_return_1x", "cost_rate_1x", "grid_net_increment_1x", "cycle_id"]],
        on="date",
        how="left",
        validate="one_to_one",
    )
    if result["held_eod"].isna().any():
        raise RuntimeError(f"Grid calendar incomplete for {candidate}")
    result["held_eod"] = result["held_eod"].astype(float)
    result["buy"] = result["buy"].astype(int)
    result["sell"] = result["sell"].astype(int)
    # The formal IC grid-size ablation scales the saved net grid contribution linearly.
    result["grid_net_increment"] = size * result["grid_net_increment_1x"]
    result["grid_units"] = size * result["held_eod"]
    result["cash_weight"] = result["base_cash_weight_no_grid"] - MARGIN_RATE * result["grid_units"]
    if result["cash_weight"].lt(-TOL).any():
        raise RuntimeError(f"Negative cash weight for {candidate}")
    result["ret"] = result["base_ret_without_grid"] + result["grid_net_increment"] + result["cash_weight"] * CASH_DAILY
    if result["ret"].isna().any() or result["ret"].le(-1.0).any():
        raise RuntimeError(f"Invalid portfolio returns for {candidate}")
    result["nav"] = (1.0 + result["ret"]).cumprod()
    result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
    result["candidate"] = candidate
    result["size"] = size
    return result


def metrics(frame: pd.DataFrame, candidate: str) -> dict[str, Any]:
    ret = frame["ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    vol = float(ret.std(ddof=1) * math.sqrt(252.0)) if len(ret) > 1 else 0.0
    stdev = float(ret.std(ddof=1)) if len(ret) > 1 else 0.0
    return {
        "candidate": candidate,
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "trading_days": int(len(frame)),
        "cagr": float(nav.iloc[-1] ** (252.0 / len(frame)) - 1.0),
        "total_return": float(nav.iloc[-1] - 1.0),
        "max_drawdown": float((nav / nav.cummax() - 1.0).min()),
        "annualized_volatility": vol,
        "sharpe": float(ret.mean() / stdev * math.sqrt(252.0)) if stdev > 0 else np.nan,
        "ending_nav_from_1": float(nav.iloc[-1]),
        "grid_entries": int(frame["buy"].sum()),
        "grid_exits": int(frame["sell"].sum()),
        "grid_held_days": int(frame["held_eod"].sum()),
        "average_total_ic_units": float((frame["base_total_ic_units_no_grid"] + frame["grid_units"]).mean()),
        "maximum_total_ic_units": float((frame["base_total_ic_units_no_grid"] + frame["grid_units"]).max()),
        "minimum_cash_weight": float(frame["cash_weight"].min()),
        "grid_cost_rate_sum": float((frame["cost_rate_1x"] * frame["size"]).sum()),
    }


def max_abs(left: pd.Series, right: pd.Series) -> float:
    return float((pd.to_numeric(left, errors="coerce") - pd.to_numeric(right, errors="coerce")).abs().max())


def main() -> None:
    paths = [FEAR_PATH, TARGET_PATH, FORMAL_PATH, FUTURES_PATH, OFFICIAL_DAILY_PATH, OWN_GRID_DAILY_PATH, OWN_GRID_EVENTS_PATH]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required IC research inputs: {missing}")

    fear = read(FEAR_PATH)
    fear = fear[["date", "fear_greed_index"]].drop_duplicates("date", keep="last")
    target = read(TARGET_PATH, usecols=["date", "grid_held_eod", "grid_buy", "grid_sell", "grid_valuation_score"])
    base_formal = read(FORMAL_PATH)
    target_state = target[["date", "grid_held_eod", "grid_buy", "grid_sell", "grid_valuation_score"]]
    base_formal = base_formal.merge(target_state, on="date", how="left", validate="one_to_one")
    if base_formal[["grid_held_eod", "grid_valuation_score"]].isna().any().any():
        raise RuntimeError("IC formal curve is missing frozen grid state or valuation score")
    futures = read(FUTURES_PATH, parse_dates=["date"])
    quote_cols = futures[["date", "contract", "open", "settle", "pre_settle"]]
    market = base_formal[["date", "contract", "roll_event", "futures_gross_ret", "grid_valuation_score"]].merge(
        quote_cols, on=["date", "contract"], how="left", validate="one_to_one"
    ).rename(columns={"grid_valuation_score": "valuation_score"})
    market = market.loc[market["date"].ge(FEAR_START)].sort_values("date").reset_index(drop=True)
    market = market.merge(fear, on="date", how="left", validate="one_to_one")
    quote_missing_rows = int(market[["open", "settle", "pre_settle"]].isna().any(axis=1).sum())
    if market["valuation_score"].isna().any() or quote_missing_rows:
        raise RuntimeError("IC score or official quarterly-contract quote missing in study interval")
    target_check = market.merge(target[["date", "grid_valuation_score"]], on="date", how="inner", validate="one_to_one")
    score_error = max_abs(target_check["valuation_score"], target_check["grid_valuation_score"])
    if score_error > TOL:
        raise RuntimeError(f"IC valuation score mismatch against target schedule: {score_error}")

    if market.iloc[0]["date"] != FEAR_START:
        raise RuntimeError(f"Fear study does not start on expected date {FEAR_START.date()}")
    if float(target.loc[target["date"].eq(FEAR_START), "grid_held_eod"].iloc[0]) != 0.0:
        raise RuntimeError("IC grid was not flat at the start of the registered Fear window")

    official = read(OFFICIAL_DAILY_PATH, usecols=["date", "ret"])
    base_parity = base_formal.merge(official, on="date", how="inner", suffixes=("_formal", "_official"), validate="one_to_one")
    formal_return_error = max_abs(base_parity["ret_formal"], base_parity["ret_official"])
    if formal_return_error > TOL:
        raise RuntimeError(f"IC formal return parity failed: {formal_return_error}")

    base_formal["base_ret_without_grid"] = (
        base_formal["ret"] - base_formal["grid_net_increment"] - base_formal["cash_weight"] * CASH_DAILY
    )
    base_formal["base_cash_weight_no_grid"] = base_formal["cash_weight"] + MARGIN_RATE * base_formal["grid_held_eod"]
    base_formal["base_total_ic_units_no_grid"] = base_formal["total_units"] - base_formal["grid_held_eod"]
    real_end = min(market["date"].max(), base_formal["date"].max())
    real_base = base_formal.loc[base_formal["date"].between(REAL_START, real_end)].copy()
    if real_base.empty or real_base["data_layer"].astype(str).str.contains("model", case=False).any():
        raise RuntimeError("Real IC Put window is empty or includes modeled Put rows")

    old_grid, _ = simulate_grid(market, fear_gate=False, entry=0.375, exit=1.0)
    old_grid = old_grid.merge(
        target[["date", "grid_held_eod", "grid_buy", "grid_sell"]],
        on="date", how="inner", validate="one_to_one",
    )
    old_grid_parity = {
        "held_state": max_abs(old_grid["held_eod"], old_grid["grid_held_eod"]),
        "buy_events": max_abs(old_grid["buy"], old_grid["grid_buy"]),
        "sell_events": max_abs(old_grid["sell"], old_grid["grid_sell"]),
    }
    if max(old_grid_parity.values()) > TOL:
        raise RuntimeError(f"Old IC grid replay did not match saved target-schedule path: {old_grid_parity}")

    current_grid, current_events = simulate_grid(market, fear_gate=False, entry=ENTRY, exit=EXIT)
    fear_grid, fear_events = simulate_grid(market, fear_gate=True, entry=ENTRY, exit=EXIT)

    own_grid = read(OWN_GRID_DAILY_PATH)
    own_grid = own_grid.loc[own_grid["candidate"].eq("L0.500_H1.000"), ["date", "grid", "grid_net_ret", "ret"]].copy()
    own_grid["date"] = pd.to_datetime(own_grid["date"])
    current_to_own = current_grid.merge(own_grid, on="date", how="inner", validate="one_to_one")
    current_half_threshold_parity = {
        "held_state": max_abs(current_to_own["held_eod"], current_to_own["grid"]),
        "grid_net_increment_1x": max_abs(current_to_own["grid_net_increment_1x"], current_to_own["grid_net_ret"]),
    }
    if max(current_half_threshold_parity.values()) > TOL:
        raise RuntimeError(f"IC 0.5/1.0 grid replay did not match saved calibration path: {current_half_threshold_parity}")

    baseline_saved_events = read(OWN_GRID_EVENTS_PATH)
    baseline_saved_events = baseline_saved_events.loc[
        baseline_saved_events["candidate"].eq("L0.500_H1.000") & pd.to_datetime(baseline_saved_events["signal_date"]).ge(FEAR_START)
    ]
    simulated_events = current_events.loc[pd.to_datetime(current_events["signal_date"]).ge(FEAR_START)]
    event_pairs = baseline_saved_events[["action", "signal_date", "execution_date"]].astype(str).sort_values(list(["action", "signal_date", "execution_date"])).reset_index(drop=True)
    simulated_pairs = simulated_events[["action", "signal_date", "execution_date"]].astype(str).sort_values(list(["action", "signal_date", "execution_date"])).reset_index(drop=True)
    if not event_pairs.equals(simulated_pairs):
        raise RuntimeError("IC 0.5/1.0 entry/exit dates differ from saved calibration events")

    current_1x_replay = base_formal.loc[base_formal["date"].ge(FEAR_START), [
        "date", "base_ret_without_grid", "base_cash_weight_no_grid"
    ]].merge(current_grid[["date", "held_eod", "grid_net_increment_1x"]], on="date", validate="one_to_one")
    current_1x_replay = current_1x_replay.merge(own_grid[["date", "ret"]], on="date", suffixes=("", "_saved"), validate="one_to_one")
    current_1x_replay["ret_replayed"] = (
        current_1x_replay["base_ret_without_grid"]
        + current_1x_replay["grid_net_increment_1x"]
        + (current_1x_replay["base_cash_weight_no_grid"] - MARGIN_RATE * current_1x_replay["held_eod"]) * CASH_DAILY
    )
    current_1x_return_error = max_abs(current_1x_replay["ret_replayed"], current_1x_replay["ret"])
    if current_1x_return_error > TOL:
        raise RuntimeError(f"IC current-grid return recomposition did not match saved calibration path: {current_1x_return_error}")

    no_grid_daily = compose_portfolio(real_base, current_grid.assign(
        held_eod=0, buy=0, sell=0, gross_return_1x=0.0, cost_rate_1x=0.0, grid_net_increment_1x=0.0, cycle_id=0
    ), candidate="no_grid", size=0.0)
    current_daily = compose_portfolio(real_base, current_grid, candidate="IC_valuation_0.5_1.0_grid_half", size=GRID_SIZE)
    fear_daily = compose_portfolio(real_base, fear_grid, candidate="fear25_confirmed_IC_grid_half", size=GRID_SIZE)
    comparison = pd.concat([no_grid_daily, current_daily, fear_daily], ignore_index=True)
    summary = pd.DataFrame([
        metrics(no_grid_daily, "no_grid"),
        metrics(current_daily, "IC_valuation_0.5_1.0_grid_half"),
        metrics(fear_daily, "fear25_confirmed_IC_grid_half"),
    ])

    overlap = market[["date", "fear_greed_index", "valuation_score"]].copy()
    overlap["fear_le_25"] = overlap["fear_greed_index"].le(25).astype(int)
    overlap["ic_entry_score_le_0_5"] = overlap["valuation_score"].le(ENTRY + TOL).astype(int)
    overlap["same_day_overlap"] = (overlap["fear_le_25"].eq(1) & overlap["ic_entry_score_le_0_5"].eq(1)).astype(int)
    overlap = overlap.merge(
        current_grid[["date", "held_eod", "buy", "sell"]].rename(columns={"held_eod": "current_grid_held_eod", "buy": "current_grid_buy", "sell": "current_grid_sell"}),
        on="date", how="left", validate="one_to_one",
    )
    overlap = overlap.merge(
        fear_grid[["date", "held_eod", "buy", "sell", "fear_gate_blocked_entry_day"]].rename(columns={"held_eod": "fear_grid_held_eod", "buy": "fear_grid_buy", "sell": "fear_grid_sell"}),
        on="date", how="left", validate="one_to_one",
    )
    fear_days = overlap.loc[overlap["fear_le_25"].eq(1)]
    fear_count = int(len(fear_days))
    overlap_summary = {
        "fear_window_start": FEAR_START.date().isoformat(),
        "fear_window_end": market["date"].max().date().isoformat(),
        "market_sessions": int(len(market)),
        "fear_score_missing_sessions": int(market["fear_greed_index"].isna().sum()),
        "extreme_fear_days_le_25": fear_count,
        "fear_days_with_IC_score_le_0_5": int(fear_days["ic_entry_score_le_0_5"].sum()),
        "share_of_extreme_fear_days_in_IC_entry_zone": float(fear_days["ic_entry_score_le_0_5"].mean()) if fear_count else np.nan,
        "all_IC_score_le_0_5_days": int(overlap["ic_entry_score_le_0_5"].sum()),
        "IC_entry_zone_days_also_extreme_fear": int(overlap["same_day_overlap"].sum()),
        "share_of_IC_entry_zone_days_also_extreme_fear": float(overlap.loc[overlap["ic_entry_score_le_0_5"].eq(1), "fear_le_25"].mean()) if overlap["ic_entry_score_le_0_5"].sum() else np.nan,
        "valuation_only_grid_entries_in_fear_window": int(current_grid.loc[current_grid["date"].ge(FEAR_START), "buy"].sum()),
        "valuation_only_entries_on_extreme_fear_signal_dates": int(current_events.loc[
            pd.to_datetime(current_events["signal_date"]).ge(FEAR_START) & current_events["action"].eq("buy"), "signal_fear_score"
        ].le(25).sum()),
        "fear_confirmed_grid_entries_in_fear_window": int(fear_grid.loc[fear_grid["date"].ge(FEAR_START), "buy"].sum()),
        "fear_gate_blocked_entry_days": int(fear_grid.loc[fear_grid["date"].ge(FEAR_START), "fear_gate_blocked_entry_day"].sum()),
        "real_portfolio_start": REAL_START.date().isoformat(),
        "real_portfolio_end": real_end.date().isoformat(),
        "real_portfolio_sessions": int(len(real_base)),
    }

    all_events = pd.concat([current_events, fear_events], ignore_index=True)
    all_events["fear_le_25"] = pd.to_numeric(all_events["signal_fear_score"], errors="coerce").le(25).astype(int)
    overlap.to_csv(OUT / "ic_fear_score_grid_overlap_by_day.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([overlap_summary]).to_csv(OUT / "ic_fear_score_grid_overlap_summary.csv", index=False, encoding="utf-8-sig")
    all_events.to_csv(OUT / "ic_grid_trade_events.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(OUT / "ic_grid_portfolio_daily.csv.gz", index=False, compression="gzip", encoding="utf-8")
    summary.to_csv(OUT / "ic_grid_portfolio_summary.csv", index=False, encoding="utf-8-sig")

    cycle_rows: list[dict[str, Any]] = []
    for path_name, daily in (("valuation_only", current_grid), ("fear25_confirmed", fear_grid)):
        held = daily.loc[daily["cycle_id"].gt(0)].copy()
        for cycle_id, group in held.groupby("cycle_id", sort=True):
            series = (1.0 + GRID_SIZE * group["grid_net_increment_1x"]).cumprod()
            cycle_rows.append({
                "path": path_name,
                "cycle_id": int(cycle_id),
                "start_date": group["date"].min().date().isoformat(),
                "end_date": group["date"].max().date().isoformat(),
                "held_sessions": int(len(group)),
                "grid_contribution_return_at_half_size": float(series.iloc[-1] - 1.0),
                "grid_contribution_increment_sum_at_half_size": float((GRID_SIZE * group["grid_net_increment_1x"]).sum()),
                "grid_contribution_max_drawdown_at_half_size": float((series / series.cummax() - 1.0).min()),
            })
    pd.DataFrame(cycle_rows).to_csv(OUT / "ic_grid_cycles.csv", index=False, encoding="utf-8-sig")

    input_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in paths}
    input_hashes[str(Path(__file__).resolve().relative_to(ROOT))] = sha256(Path(__file__).resolve())
    input_hashes[str((OUT / "preregistered_spec.md").relative_to(ROOT))] = sha256(OUT / "preregistered_spec.md")
    (OUT / "source_hashes.json").write_text(json.dumps(input_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verification = {
        "status": "PASS",
        "ic_score_matches_target_schedule_max_abs": score_error,
        "official_contract_quote_missing_rows": quote_missing_rows,
        "ic_formal_return_matches_official_daily_max_abs": formal_return_error,
        "saved_real_grid_replay_parity": old_grid_parity,
        "saved_ic_0_5_1_0_calibration_parity": current_half_threshold_parity,
        "saved_ic_0_5_1_0_full_return_recomposition_max_abs": current_1x_return_error,
        "saved_ic_0_5_1_0_event_dates_match": True,
        "window_calendar_complete": bool(len(real_base) == real_base["date"].nunique() == len(current_daily) == len(fear_daily)),
        "nonnegative_candidate_cash": bool(min(summary["minimum_cash_weight"]) >= -TOL),
        "portfolio_paths_have_same_calendar": bool(all(len(x) == len(real_base) for x in (no_grid_daily, current_daily, fear_daily))),
        "fear_join_kept_market_calendar": True,
        "thresholds": {"entry": ENTRY, "exit": EXIT, "fear_le": 25, "grid_size": GRID_SIZE},
    }
    if not all(verification[key] for key in ("saved_ic_0_5_1_0_event_dates_match", "window_calendar_complete", "nonnegative_candidate_cash", "portfolio_paths_have_same_calendar", "fear_join_kept_market_calendar")):
        raise RuntimeError(f"Research verification failed: {verification}")
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    existing_entries = all_events.loc[all_events["action"].eq("buy")]
    summary_names = {
        "no_grid": "无网格",
        "IC_valuation_0.5_1.0_grid_half": "IC估值0.5/1.0，半仓网格",
        "fear25_confirmed_IC_grid_half": "IC估值+恐贪≤25，半仓网格",
    }
    table_lines = [
        "| 路径 | CAGR | 累计收益 | 最大回撤 | 年化波动 | Sharpe | 网格开/平仓 | 持仓日 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.to_dict(orient="records"):
        table_lines.append(
            f"| {summary_names[row['candidate']]} | {row['cagr']:.2%} | {row['total_return']:.2%} "
            f"| {row['max_drawdown']:.2%} | {row['annualized_volatility']:.2%} | {row['sharpe']:.2f} "
            f"| {row['grid_entries']}/{row['grid_exits']} | {row['grid_held_days']} |"
        )
    summary_table = "\n".join(table_lines)
    entry_table = existing_entries[["path", "signal_date", "signal_ic_score", "signal_fear_score", "execution_date", "contract", "execution_open"]].to_markdown(index=False, floatfmt=".4f")
    base_buys = existing_entries.loc[existing_entries["path"].eq("valuation_only"), [
        "cycle_id", "signal_date", "execution_date", "execution_open", "signal_fear_score"
    ]].rename(columns={
        "signal_date": "base_signal_date", "execution_date": "base_execution_date",
        "execution_open": "base_execution_open", "signal_fear_score": "base_signal_fear_score",
    })
    fear_buys = existing_entries.loc[existing_entries["path"].eq("fear25_confirmed"), [
        "cycle_id", "signal_date", "execution_date", "execution_open", "signal_fear_score"
    ]].rename(columns={
        "signal_date": "fear_signal_date", "execution_date": "fear_execution_date",
        "execution_open": "fear_execution_open", "signal_fear_score": "fear_signal_fear_score",
    })
    entry_timing = base_buys.merge(fear_buys, on="cycle_id", how="outer", validate="one_to_one")
    market_row_by_date = {day.date().isoformat(): i for i, day in enumerate(market["date"])}
    entry_timing["delay_sessions"] = entry_timing.apply(
        lambda row: market_row_by_date[row["fear_execution_date"]] - market_row_by_date[row["base_execution_date"]], axis=1
    )
    entry_timing["entry_open_change"] = entry_timing["fear_execution_open"] / entry_timing["base_execution_open"] - 1.0
    entry_timing.to_csv(OUT / "ic_grid_entry_timing_comparison.csv", index=False, encoding="utf-8-sig")
    entry_timing_table = entry_timing[[
        "cycle_id", "base_signal_date", "base_execution_date", "fear_signal_date", "fear_execution_date",
        "delay_sessions", "base_execution_open", "fear_execution_open", "entry_open_change",
    ]].rename(columns={
        "cycle_id": "轮次", "base_signal_date": "估值信号日", "base_execution_date": "估值执行日",
        "fear_signal_date": "恐贪信号日", "fear_execution_date": "恐贪执行日", "delay_sessions": "延后交易日",
        "base_execution_open": "估值入场开盘", "fear_execution_open": "恐贪入场开盘", "entry_open_change": "开盘价差",
    }).copy()
    entry_timing_table["开盘价差"] = entry_timing_table["开盘价差"].map(lambda value: f"{value:+.2%}")
    entry_timing_table["估值入场开盘"] = entry_timing_table["估值入场开盘"].map(lambda value: f"{value:.1f}")
    entry_timing_table["恐贪入场开盘"] = entry_timing_table["恐贪入场开盘"].map(lambda value: f"{value:.1f}")
    entry_timing_table = entry_timing_table.to_markdown(index=False)
    current_summary = summary.loc[summary["candidate"].eq("IC_valuation_0.5_1.0_grid_half")].iloc[0]
    fear_summary = summary.loc[summary["candidate"].eq("fear25_confirmed_IC_grid_half")].iloc[0]
    cagr_delta_pp = 100.0 * (fear_summary["cagr"] - current_summary["cagr"])
    total_delta_pp = 100.0 * (fear_summary["total_return"] - current_summary["total_return"])
    drawdown_delta_pp = 100.0 * (fear_summary["max_drawdown"] - current_summary["max_drawdown"])
    if abs(drawdown_delta_pp) < 1e-9:
        drawdown_delta_pp = 0.0
    overlap_dates = ", ".join(fear_days.loc[fear_days["ic_entry_score_le_0_5"].eq(1), "date"].astype(str).tolist())
    record = f"""# IC 恐贪≤25 确认网格：历史候选复核

状态：研究候选；没有修改正式信号、生产代码或实际持仓。

## 口径

IC网格按自身估值分≤0.5入场、≥1.0退出，信号日收盘后判断、下一交易日开盘执行；新增仓位按0.5倍。候选只在新开仓时增加恐贪分≤25条件，已有仓位的退出不变。无网格、估值网格、恐贪确认网格在相同IC基础组合、交易日和费用下比较。

恐贪与估值重合窗口为 {FEAR_START.date()} 至 {market['date'].max().date()}；组合绩效只用真实510500 Put期 {REAL_START.date()} 至 {real_end.date()}，共 {len(real_base)} 个IC组合交易日。该段期货为官方IC数据，510500 Put为真实期权路径。

IC估值评分与季度展期合约报价的可核验截止日为2026-08-14；恐贪完整复现窗口到2026-09-18，但此后没有匹配的IC估值/合约候选表，因此没有延长IC测试。

## 恐贪与IC入场区重合

- 恐贪≤25共 {overlap_summary['extreme_fear_days_le_25']} 日；其中IC网格分≤0.5有 {overlap_summary['fear_days_with_IC_score_le_0_5']} 日（{overlap_summary['share_of_extreme_fear_days_in_IC_entry_zone']:.1%}）。
- 全部IC估值分≤0.5共 {overlap_summary['all_IC_score_le_0_5_days']} 日，其中恐贪也≤25有 {overlap_summary['IC_entry_zone_days_also_extreme_fear']} 日（{overlap_summary['share_of_IC_entry_zone_days_also_extreme_fear']:.1%}）。
- 两个条件同日满足的日期：{overlap_dates}。其中2024-01-30至2024-02-05的五个重合日已处于网格持仓中，不是新的开仓机会。
- 估值单独触发 {overlap_summary['valuation_only_grid_entries_in_fear_window']} 次新开；其中信号日恐贪≤25的有 {overlap_summary['valuation_only_entries_on_extreme_fear_signal_dates']} 次。加恐贪确认后新开 {overlap_summary['fear_confirmed_grid_entries_in_fear_window']} 次，估值入场区被恐贪挡住的交易日数为 {overlap_summary['fear_gate_blocked_entry_days']}。

## 真实期组合对照

{summary_table}

入场/退出执行记录见 `ic_grid_trade_events.csv`。所有候选均沿用相同的IC底层、网格交易规则和费用。结果只用于检验额外恐贪入场条件，不能与其他策略或不同时段直接比较。

相对估值单独半仓网格，恐贪确认候选的CAGR变化为 {cagr_delta_pp:+.2f} 个百分点，累计收益变化 {total_delta_pp:+.2f} 个百分点，最大回撤变化 {drawdown_delta_pp:+.2f} 个百分点；开平仓轮数仍为 {int(fear_summary['grid_entries'])}/{int(fear_summary['grid_exits'])}，持仓少 {int(current_summary['grid_held_days'] - fear_summary['grid_held_days'])} 个交易日。改善来自两轮延后进场，样本只有三轮。

### 入场时点变化

{entry_timing_table}

开盘价差 = 恐贪确认入场开盘 / 估值单独入场开盘 − 1。价差只描述这几次历史入场，不是未来价格预测。

## 主要事件

{entry_table}

## 复核

- 当前网格评分与 IC 正式目标表最大差值：{score_error:.3e}。
- 期货行情按当前季度展期合约映射到中金所原始报价；缺失报价行：{quote_missing_rows}。
- 当前IC正式日收益与保存的IC日收益最大差值：{formal_return_error:.3e}。
- 旧0.375/1.0网格的重放状态和买卖日与已保存IC目标表完全匹配；0.5/1.0网格的状态、交易日和逐日净贡献与既有校准输出匹配，1倍组合收益重组误差小于1e-12，详情见 `verification.json`。

## 限制

恐贪历史值来自当前下载快照，没有逐日原始发布版本。此回放将0.5/1.0、0.5倍 IC 网格参数应用于历史；当前规则是在2026-09-14信号日起生效，历史组合曲线并非该新规则的实时实盘记录。测试没有晋级正式信号，也不证明可实时获取、独立样本外表现或实际成交。

## 决定

`research_only_no_promotion`

## 工件

- `ic_fear_score_grid_overlap_summary.csv`：恐贪分与IC入场区重合统计。
- `ic_fear_score_grid_overlap_by_day.csv`：逐日对齐表。
- `ic_grid_portfolio_summary.csv` / `ic_grid_portfolio_daily.csv.gz`：无网格、IC估值网格和恐贪确认网格对照。
- `ic_grid_trade_events.csv` / `ic_grid_cycles.csv` / `ic_grid_entry_timing_comparison.csv`：执行事件、持仓周期和入场时点变化。
- `source_hashes.json` / `verification.json`：输入身份与复现核验。
"""
    (OUT / "record.md").write_text(record, encoding="utf-8")
    print(json.dumps({"overlap": overlap_summary, "portfolio_summary": summary.to_dict(orient="records"), "verification": verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
