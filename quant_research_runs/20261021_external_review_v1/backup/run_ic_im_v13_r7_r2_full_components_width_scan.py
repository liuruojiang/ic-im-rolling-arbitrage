#!/usr/bin/env python
"""Research-only IC v1.3-r7 full-composition R2 width scan.

Unlike the F-base scan, every candidate here replays the fixed 0.5 IC sleeve,
the R2-gated 0.5 momentum sleeve, the formal grid, and the core plus momentum
510500 Put ledger.  The grid Put target remains zero because that is the
current IC rule; IC Call is excluded by the formal specification.

No production signal, ledger, specification, or order path is modified.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260912_ic_im_rolling_arbitrage_ic_v1_3_r7_full_composition_"
    "ic_full_components_r2_r2_regression_window_and_threshold"
)
F_BASE_RUN = ROOT / "quant_param_scan_runs" / "20260912_ic_im_v13_r7_r2_current_f_base_width_scan"
REFRESH = ROOT / "outputs" / "nav_r7_complete_refresh_20260912"
HISTORICAL_END = pd.Timestamp("2026-08-14")
END = pd.Timestamp("2026-09-11")
START = pd.Timestamp("2015-04-16")
REAL_START = pd.Timestamp("2022-09-19")
ONE_WAY = 0.0001
MARGIN = 0.30
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
IMPROVEMENT_EPS = 1e-10
R2_WINDOWS = tuple(range(10, 61, 5))
R2_THRESHOLDS = tuple(round(value, 3) for value in np.arange(0.0, 0.5001, 0.025))
SCHEDULE_FILE = RUN / "candidate_r2_schedules.csv.gz"

ASHARE_SOURCE = ROOT.parent / "A 股股指多头策略" / "poe_cn_four_index_raw_momentum_combo_v1_3_bot.py"
IC_MAINLINE = ROOT / "ic_mainline_v1_3.py"
F_BASE_SCRIPT = ROOT / "run_ic_im_v13_r7_r2_current_f_base_width_scan.py"
REFRESH_SCRIPT = ROOT / "refresh_nav_r7_complete_20260911.py"

_WORKER: dict[str, Any] = {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def candidate_name(window: int | None, threshold: float | None) -> str:
    if window is None or threshold is None:
        return "r2_off"
    return f"r2_w{window:02d}_t{threshold:.3f}".replace(".", "p")


def git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def metric(sample: pd.DataFrame) -> dict[str, Any]:
    returns = sample["ret"].to_numpy(dtype=float)
    nav = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(np.r_[1.0, nav])[1:]
    drawdown = nav / peak - 1.0
    rows = len(sample)
    ann_return = float(nav[-1] ** (252.0 / rows) - 1.0)
    ann_vol = float(np.std(returns, ddof=1) * math.sqrt(252.0)) if rows > 1 else math.nan
    return {
        "start": sample["date"].iloc[0].date().isoformat(),
        "end": sample["date"].iloc[-1].date().isoformat(),
        "rows": int(rows),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": ann_return / ann_vol if ann_vol > 1e-15 else math.nan,
        "max_drawdown": float(drawdown.min()),
        "calmar": ann_return / abs(float(drawdown.min())) if drawdown.min() < -1e-15 else math.nan,
        "final_nav": float(nav[-1]),
        "avg_momentum_weight": float(sample["momentum_weight"].mean()),
        "avg_total_units": float(sample["total_units"].mean()),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "futures_cost_total": float(sample["futures_cost_rate"].sum()),
    }


def make_candidate_frame(base: pd.DataFrame, weights: pd.Series, sleeve: Any) -> pd.DataFrame:
    result = base.copy()
    weight = weights.astype(float).reset_index(drop=True)
    if len(weight) != len(result):
        raise RuntimeError("candidate weight length mismatch")
    allowed = np.array([0.0, 0.25, 0.5, 1.0])
    if not np.isclose(weight.to_numpy()[:, None], allowed[None, :], atol=1e-12).any(axis=1).all():
        raise RuntimeError("candidate momentum weight escaped formal 0/0.25/0.5/1 grid")
    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    momentum_cost = (
        sleeve.ic_stage1.ONE_WAY_COST * turnover
        + 2.0 * sleeve.ic_stage1.ONE_WAY_COST * weight * result["roll_event"].astype(float)
    )
    momentum_gross = weight * result["ic_gross_ret"].astype(float)
    momentum_net = (1.0 + momentum_gross) * (1.0 - momentum_cost) - 1.0
    momentum_cash = 1.0 - sleeve.ic_grid.MARGIN_RATE * weight
    blend_cash = (
        0.5 * result["bare_roll_ic_cash_weight"].astype(float)
        + 0.5 * momentum_cash
    )
    blend_ret = (
        0.5 * result["bare_roll_ic_ret"].astype(float)
        + 0.5 * (momentum_net + momentum_cash * sleeve.ic_grid.CASH_DAILY)
    )
    result["momentum_execution_weight"] = weight.to_numpy()
    result["total_ic_units"] = (
        0.5 + 0.5 * weight + result["grid_held_eod"].astype(float)
    )
    result["base_non_cash_ret"] = blend_ret - blend_cash * sleeve.ic_grid.CASH_DAILY
    result["pre_put_cash_weight"] = (
        blend_cash - sleeve.ic_grid.MARGIN_RATE * result["grid_held_eod"].astype(float)
    )
    result["momentum_turnover"] = turnover.to_numpy()
    result["momentum_cost_rate"] = (0.5 * momentum_cost).to_numpy()
    if result["pre_put_cash_weight"].lt(-1e-12).any():
        raise RuntimeError("candidate pre-Put cash became negative")
    return result


def build_candidate_put_schedule(
    candidate_frame: pd.DataFrame, engine_base: pd.DataFrame, sleeve: Any, ic_put: Any
) -> pd.DataFrame:
    engine = engine_base.copy()
    engine["momentum_weight"] = candidate_frame["momentum_execution_weight"].to_numpy()
    selected = ic_put.v1.build_v2_schedule(engine)
    selected = selected.merge(
        candidate_frame[["date", "grid_held_eod"]].rename(columns={"date": "execution_date"}),
        on="execution_date", how="left", validate="many_to_one",
    )
    if selected["grid_held_eod"].isna().any():
        raise RuntimeError("candidate grid schedule alignment failure")
    return sleeve.build_schedule(selected, "combined_current")


def build_context() -> dict[str, Any]:
    import run_ic_v13_sleeve_put_independent_replay_v1 as sleeve
    import ic_roll_momentum_stage2_put_v2 as ic_put

    base_frame, engine_base, _ = sleeve.load_base_components()
    frames, _valuation, market, market_checks = ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll_dates = ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    return {
        "sleeve": sleeve,
        "ic_put": ic_put,
        "base_frame": base_frame,
        "engine_base": engine_base,
        "frames": frames,
        "market": market,
        "market_checks": market_checks,
        "roll_dates": roll_dates,
    }


def replay_historical(
    context: dict[str, Any], candidate: str, candidate_schedule: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    sleeve = context["sleeve"]
    weights = candidate_schedule.loc[
        candidate_schedule["date"].between(START, HISTORICAL_END), "momentum_execution_weight"
    ].reset_index(drop=True)
    candidate_frame = make_candidate_frame(context["base_frame"], weights, sleeve)
    put_schedule = build_candidate_put_schedule(
        candidate_frame, context["engine_base"], sleeve, context["ic_put"]
    )
    ledger, trades = sleeve.run_ledger(
        "combined_current", put_schedule, context["frames"], context["market"], context["roll_dates"]
    )
    result = sleeve.combine_candidate(
        candidate_frame, {"combined_current": ledger}, candidate, ("combined_current",)
    )
    result = result.rename(columns={"momentum_execution_weight": "momentum_weight", "total_ic_units": "total_units"})
    keep = [
        "date", "ret", "cash_weight", "total_units", "momentum_weight", "grid_held_eod",
        "momentum_turnover", "base_non_cash_ret", "grid_net_increment", "put_pnl_ret",
        "put_cost_rate", "put_mark_fraction", "put_target_delta",
    ]
    result = result[keep].copy()
    result["put_qty"] = ledger["put_qty"].to_numpy()
    result["put_contract"] = ledger["put_contract"].to_numpy()
    result["futures_cost_rate"] = (
        0.5 * candidate_frame["roll_event"].astype(float) * 2.0 * ONE_WAY
        + candidate_frame["momentum_cost_rate"].astype(float)
    )
    result["roll_event"] = candidate_frame["roll_event"].astype(bool).to_numpy()
    result["data_layer"] = np.where(result["date"].lt(REAL_START), "model_put", "real_put")
    last = ledger.iloc[-1]
    return result, {
        "last_put_contract": str(last["put_contract"]),
        "last_put_qty": float(last["put_qty"]),
        "last_put_target_delta": float(last["target_delta"]),
        "last_put_abs_delta": float(last["abs_put_delta"]),
        "historical_trade_rows": int(len(trades)),
    }


def worker_init(schedule_path: str) -> None:
    global _WORKER
    grouped = pd.read_csv(schedule_path, parse_dates=["date"], low_memory=False).groupby("candidate", sort=False)
    _WORKER = {
        "context": build_context(),
        "schedules": {str(name): frame.sort_values("date").reset_index(drop=True) for name, frame in grouped},
    }


def worker_replay(item: tuple[str, float | None, float | None]) -> tuple[str, float | None, float | None, pd.DataFrame, dict[str, Any]]:
    candidate, window, threshold = item
    schedule = _WORKER["schedules"][candidate]
    result, audit = replay_historical(_WORKER["context"], candidate, schedule)
    return candidate, window, threshold, result, audit


def prepare_r2_schedules() -> tuple[list[tuple[str, float | None, float | None]], dict[str, pd.DataFrame], dict[str, Any]]:
    if not (F_BASE_RUN / "ic_ohlcv_frozen_plus_fresh.csv.gz").is_file():
        raise FileNotFoundError(F_BASE_RUN / "ic_ohlcv_frozen_plus_fresh.csv.gz")
    source = load_module("full_component_r2_source", ASHARE_SOURCE)
    ic = load_module("full_component_r2_ic", IC_MAINLINE)
    fbase = load_module("full_component_r2_fbase", F_BASE_SCRIPT)
    ohlcv = pd.read_csv(F_BASE_RUN / "ic_ohlcv_frozen_plus_fresh.csv.gz", parse_dates=["date"])
    ohlcv["date"] = pd.to_datetime(ohlcv["date"]).dt.normalize()
    ohlcv = ohlcv.sort_values("date").reset_index(drop=True)
    if ohlcv["date"].iloc[-1] != END or ohlcv["date"].duplicated().any():
        raise RuntimeError("validated IC OHLCV is not complete through 2026-09-11")
    configs = {item.key: item for item in source.SLEEVES}
    cfg = configs["zz500"]
    close = pd.Series(ohlcv["close"].to_numpy(dtype=float), index=pd.DatetimeIndex(ohlcv["date"]))
    r2_by_window = {
        window: source.calc_bias_momentum_r2(close, cfg.bias_ma, window, cfg.weight_end)
        for window in R2_WINDOWS
    }
    specs: list[tuple[str, float | None, float | None]] = [("r2_off", None, None)]
    schedules: dict[str, pd.DataFrame] = {}
    baseline = ic.build_momentum_schedule(ohlcv).copy()
    schedules["r2_off"] = baseline
    for window in R2_WINDOWS:
        for threshold in R2_THRESHOLDS:
            name = candidate_name(window, threshold)
            specs.append((name, float(window), float(threshold)))
            schedules[name] = fbase.ic_r2_schedule(ic, source, ohlcv, r2_by_window[window], threshold)
    observed = pd.read_csv(F_BASE_RUN / "daily_candidate_returns.csv.gz", parse_dates=["date"], low_memory=False)
    observed = observed.loc[
        observed["product"].eq("IC") & observed["candidate"].eq("r2_off"),
        ["date", "momentum_weight"],
    ]
    check = schedules["r2_off"][["date", "momentum_execution_weight"]].merge(
        observed, on="date", validate="one_to_one"
    )
    parity = float((check["momentum_execution_weight"] - check["momentum_weight"]).abs().max())
    if parity > 1e-12:
        raise RuntimeError(f"R2-off schedule differs from validated F-base schedule: {parity}")
    export = []
    for name, window, threshold in specs:
        frame = schedules[name][[
            "date", "momentum_execution_weight", "momentum_signal_target", "base_dd_for_gate",
        ]].copy()
        frame["candidate"] = name
        frame["r2_window"] = window
        frame["r2_threshold"] = threshold
        export.append(frame)
    pd.concat(export, ignore_index=True).to_csv(SCHEDULE_FILE, index=False, compression="gzip")
    audit = {
        "r2_off_schedule_parity_max_abs": parity,
        "ohlcv_start": ohlcv["date"].iloc[0].date().isoformat(),
        "ohlcv_end": ohlcv["date"].iloc[-1].date().isoformat(),
        "ohlcv_rows": int(len(ohlcv)),
        "candidate_count": len(specs) - 1,
    }
    return specs, schedules, audit


def load_tail_context() -> dict[str, Any]:
    for filename in ("historical_signals.json", "ic_tail_daily.csv", "verification.json"):
        if not (REFRESH / filename).is_file():
            raise FileNotFoundError(REFRESH / filename)
    refresh = load_module("full_component_tail_refresh", REFRESH_SCRIPT)
    replay = refresh.load_replay_strategy()
    signals = json.loads((REFRESH / "historical_signals.json").read_text(encoding="utf-8"))
    dates = [date.fromisoformat(value) for value in sorted(signals)]
    if not dates or pd.Timestamp(dates[-1]) != END:
        raise RuntimeError("latest full-component refresh does not end on 2026-09-11")
    contracts = set()
    contract_ids: dict[str, str] = {
        str(refresh.ANCHORS["IC"]["post_put_contract"]): str(refresh.ANCHORS["IC"]["post_put_security_id"]),
    }
    for value in sorted(signals):
        signal = signals[value]["IC"]
        for field in ("core_current", "core_eod_contract"):
            if signal.get(field):
                contracts.add(str(signal[field]))
        target_contract = signal.get("put_target_contract")
        target_id = signal.get("put_target_security_id")
        if target_contract and target_id:
            contract_ids[str(target_contract)] = str(target_id)
    marks = replay.fetch_cffex_daily_marks(sorted(contracts), refresh.DATA_CUTOFF, END.date()).reset_index()
    series = {security_id: replay.fetch_option_closes(security_id) for security_id in sorted(set(contract_ids.values()))}
    for security_id, value in series.items():
        value.index = pd.to_datetime(value.index).normalize()
        if pd.Timestamp(refresh.DATA_CUTOFF) not in value.index or END not in value.index:
            raise RuntimeError(f"510500 Put {security_id} lacks 2026-08-14 or 2026-09-11 close")
    baseline = pd.read_csv(REFRESH / "ic_tail_daily.csv", parse_dates=["date"])
    return {
        "refresh": refresh,
        "replay": replay,
        "signals": signals,
        "dates": dates,
        "marks": marks,
        "series": series,
        "contract_ids": contract_ids,
        "baseline": baseline.sort_values("date").reset_index(drop=True),
    }


def get_mark(marks: pd.DataFrame, day: date, contract: str) -> pd.Series:
    row = marks.loc[(marks["date"].dt.date == day) & marks["contract"].eq(contract)]
    if len(row) != 1:
        raise RuntimeError(f"missing/duplicate CFFEX mark for {day}/{contract}")
    return row.iloc[0]


def parse_ic_put(contract: str, replay: Any) -> tuple[float, date]:
    import re
    matched = re.fullmatch(r"510500P(\d{2})(\d{2})M(\d{5})", contract)
    if not matched:
        raise RuntimeError(f"unrecognized IC Put contract: {contract}")
    expiry = replay._fourth_wednesday(2000 + int(matched.group(1)), int(matched.group(2)))
    return float(matched.group(3)) / 1000.0, expiry


def option_abs_delta(context: dict[str, Any], day: date, contract: str, signal: dict[str, Any]) -> tuple[float, int]:
    refresh = context["refresh"]
    replay = context["replay"]
    if contract not in context["contract_ids"]:
        raise RuntimeError(f"missing 510500 security mapping for {contract}")
    close = float(context["series"][context["contract_ids"][contract]].loc[pd.Timestamp(day)])
    strike, expiry = parse_ic_put(contract, replay)
    etf = float(signal["etf_price"])
    future = float(signal["future_last"])
    years = max((expiry - day).days, 1) / 365.0
    gov10y = replay._gov10y_for_day("IC", day)
    dividend = float(replay.FROZEN["IC"]["dividend"])
    iv = replay._implied_volatility("P", close, etf, strike, gov10y, dividend, years)
    if iv is None:
        raise RuntimeError(f"cannot infer IV for {day}/{contract}")
    abs_delta = abs(replay._bs_price_delta("P", etf, strike, gov10y, dividend, iv, years)[1])
    if not math.isfinite(abs_delta) or abs_delta <= 1e-8:
        raise RuntimeError(f"invalid absolute delta for {day}/{contract}")
    full_equivalent = max(1, round(future * 200.0 / (etf * 10_000.0)))
    return float(abs_delta), int(full_equivalent)


def resize_qty(context: dict[str, Any], day: date, contract: str, target_delta: float, signal: dict[str, Any]) -> int:
    if target_delta <= 1e-12:
        return 0
    abs_delta, full_equivalent = option_abs_delta(context, day, contract, signal)
    return max(1, round(full_equivalent * target_delta / abs_delta))


def tail_replay(
    candidate: str,
    schedule: pd.DataFrame,
    history_audit: dict[str, Any],
    historical_reference: pd.DataFrame,
    context: dict[str, Any],
) -> pd.DataFrame:
    values = schedule.set_index("date")
    historical = historical_reference.set_index("date")
    seed = historical.loc[HISTORICAL_END]
    current_contract = str(history_audit["last_put_contract"])
    current_qty = float(history_audit["last_put_qty"])
    if current_contract not in context["contract_ids"]:
        raise RuntimeError(f"candidate {candidate} ended historical replay on unmapped {current_contract}")
    previous_mark = float(
        context["series"][context["contract_ids"][current_contract]].loc[HISTORICAL_END]
    )
    previous_weight = float(values.loc[HISTORICAL_END, "momentum_execution_weight"])
    core_delta = float(seed["core_put_target_delta"])
    baseline_weight = float(seed["momentum_execution_weight"])
    baseline_momentum_delta = float(seed["momentum_put_target_delta"])
    valuation_delta = (
        baseline_momentum_delta / (0.5 * baseline_weight)
        if baseline_weight > 1e-12 else 0.0
    )
    momentum_delta = 0.5 * previous_weight * valuation_delta
    rows: list[dict[str, Any]] = []
    for day in context["dates"]:
        stamp = pd.Timestamp(day)
        signal = context["signals"][day.isoformat()]["IC"]
        current_core = str(signal["core_current"])
        eod_core = str(signal["core_eod_contract"])
        current_quote = get_mark(context["marks"], day, current_core)
        eod_quote = get_mark(context["marks"], day, eod_core)
        pre_settle = float(current_quote["pre_settle"])
        eod_settle = float(eod_quote["settle"])
        roll_event = eod_core != current_core
        gross_per_unit = eod_settle / pre_settle - 1.0
        weight = float(values.loc[stamp, "momentum_execution_weight"])
        next_weight = float(values.loc[stamp, "momentum_signal_target"])
        grid = float(signal.get("grid_current", 0.0))
        total_units = 0.5 + 0.5 * weight + grid
        turnover = abs(weight - previous_weight)
        core_futures_cost = 2.0 * ONE_WAY * float(roll_event)
        momentum_futures_cost = ONE_WAY * turnover + 2.0 * ONE_WAY * weight * float(roll_event)
        futures_cost = 0.5 * core_futures_cost + 0.5 * momentum_futures_cost
        futures_gross = total_units * gross_per_unit
        target_core = float(signal["core_put_target_delta"])
        target_momentum = 0.5 * next_weight * float(signal["valuation_put_delta"])
        target_total = target_core + target_momentum
        target_contract = str(signal.get("put_target_contract") or current_contract)
        if target_contract not in context["contract_ids"]:
            raise RuntimeError(f"candidate {candidate} target contract has no security mapping: {target_contract}")
        protection_changed = not (
            math.isclose(target_core, core_delta, abs_tol=1e-12)
            and math.isclose(target_momentum, momentum_delta, abs_tol=1e-12)
        )
        contract_changed = target_contract != current_contract
        target_qty = (
            resize_qty(context, day, target_contract, target_total, signal)
            if (protection_changed or contract_changed) else int(round(current_qty))
        )
        current_mark = float(
            context["series"][context["contract_ids"][current_contract]].loc[stamp]
        )
        put_pnl = current_qty * 10_000.0 * (current_mark - previous_mark) / (pre_settle * 200.0)
        if contract_changed:
            eod_mark = float(context["series"][context["contract_ids"][target_contract]].loc[stamp])
            put_cost = (current_qty / 20.0 + target_qty / 20.0) * ONE_WAY
        else:
            eod_mark = current_mark
            put_cost = abs(target_qty - current_qty) / 20.0 * ONE_WAY
        put_mark_fraction = target_qty * 10_000.0 * eod_mark / (eod_settle * 200.0)
        cash_weight = max(0.0, 1.0 - MARGIN * total_units - put_mark_fraction)
        ret = (
            (1.0 + futures_gross + put_pnl)
            * (1.0 - futures_cost)
            * (1.0 - put_cost)
            - 1.0
            + cash_weight * CASH_DAILY
        )
        rows.append({
            "date": stamp, "ret": ret, "cash_weight": cash_weight,
            "total_units": total_units, "momentum_weight": weight, "grid_held_eod": grid,
            "momentum_turnover": turnover, "put_pnl_ret": put_pnl,
            "put_cost_rate": put_cost, "put_mark_fraction": put_mark_fraction,
            "put_qty": target_qty, "put_target_delta": target_total,
            "put_contract": target_contract, "futures_cost_rate": futures_cost,
            "roll_event": roll_event, "data_layer": "real_historical_replay_proxy_valuation",
        })
        previous_weight = weight
        current_contract = target_contract
        current_qty = float(target_qty)
        previous_mark = eod_mark
        core_delta = target_core
        momentum_delta = target_momentum
    result = pd.DataFrame(rows)
    if not np.isfinite(result.select_dtypes(include=[np.number]).to_numpy(dtype=float)).all():
        raise RuntimeError(f"candidate {candidate} tail contains non-finite values")
    return result


def window_metrics(frame: pd.DataFrame, candidate: str, window: float | None, threshold: float | None) -> list[dict[str, Any]]:
    end = frame["date"].iloc[-1]
    windows = [
        ("full", frame["date"].iloc[0]),
        ("last_10y", end - pd.DateOffset(years=10)),
        ("last_5y", end - pd.DateOffset(years=5)),
        ("last_3y", end - pd.DateOffset(years=3)),
        ("last_1y", end - pd.DateOffset(years=1)),
        ("real_only", REAL_START),
    ]
    rows = []
    for label, start in windows:
        sample = frame.loc[frame["date"].ge(start)].reset_index(drop=True)
        rows.append({
            "product": "IC", "candidate": candidate, "candidate_local": candidate,
            "r2_window": window, "r2_threshold": threshold, "window": label,
            **metric(sample),
        })
    return rows


def checker_compatible_tables(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the parameter-scan skill's required long and wide contracts."""
    detail = metrics.copy()
    detail["segment"] = detail["window"].replace({
        "full": "full",
        "last_10y": "last_10y",
        "last_5y": "last_5y",
        "last_3y": "last_3y",
        "last_1y": "last_1y",
        "real_only": "real_only",
    })
    if detail["segment"].isna().any():
        raise RuntimeError("unknown metric segment while normalizing scan artifacts")
    long = detail.rename(columns={"sharpe": "sharpe_repo", "max_drawdown": "max_dd"})[
        [
            "candidate", "candidate_local", "product", "r2_window", "r2_threshold", "segment",
            "start", "end", "rows", "ann_return", "ann_vol", "sharpe_repo", "max_dd",
            "calmar", "final_nav", "avg_momentum_weight", "avg_total_units",
            "put_cost_total", "futures_cost_total",
        ]
    ].copy()
    if long.duplicated(["candidate", "segment"]).any():
        raise RuntimeError("normalized long table has duplicate candidate/segment rows")
    required_segments = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
    identity = ["candidate", "candidate_local", "product", "r2_window", "r2_threshold"]
    wide = long[identity].drop_duplicates().set_index("candidate")
    for segment in required_segments:
        part = long.loc[long["segment"].eq(segment), ["candidate", "ann_return", "max_dd"]].set_index("candidate")
        wide[f"ann_return_{segment}"] = part["ann_return"]
        wide[f"max_dd_{segment}"] = part["max_dd"]
    real = long.loc[long["segment"].eq("real_only"), ["candidate", "ann_return", "max_dd"]].set_index("candidate")
    wide["ann_return_real_only"] = real["ann_return"]
    wide["max_dd_real_only"] = real["max_dd"]
    wide = wide.reset_index()
    finite_long = ["rows", "ann_return", "ann_vol", "sharpe_repo", "max_dd"]
    finite_wide = [
        "ann_return_full", "max_dd_full", "ann_return_last_10y", "max_dd_last_10y",
        "ann_return_last_5y", "max_dd_last_5y", "ann_return_last_3y", "max_dd_last_3y",
        "ann_return_last_1y", "max_dd_last_1y",
    ]
    if not np.isfinite(long[finite_long].to_numpy(dtype=float)).all() or not np.isfinite(wide[finite_wide].to_numpy(dtype=float)).all():
        raise RuntimeError("normalized checker tables contain non-finite required metrics")
    return long, wide


def write_heatmap(summary: pd.DataFrame) -> None:
    subset = summary.loc[summary["candidate"].ne("r2_off")].copy()
    maps = []
    for column in (
        "full_ann_return_delta_pp", "full_max_dd_delta_pp",
        "real_ann_return_delta_pp", "real_max_dd_delta_pp",
    ):
        pivot = subset.pivot(index="r2_threshold", columns="r2_window", values=column)
        pivot = pivot.reindex(index=R2_THRESHOLDS, columns=R2_WINDOWS)
        maps.append((column, pivot.to_numpy(dtype=float)))
    bound = max(abs(np.nanmin(values)) for _name, values in maps)
    bound = max(bound, max(abs(np.nanmax(values)) for _name, values in maps), 0.1)
    titles = (
        "Full annual return Δ pp", "Full max drawdown Δ pp",
        "Real-only annual return Δ pp", "Real-only max drawdown Δ pp",
    )
    fig, axes = plt.subplots(2, 2, figsize=(16, 14), sharex=True, sharey=True)
    image = None
    for axis, title, (_name, values) in zip(axes.ravel(), titles, maps):
        image = axis.imshow(values, aspect="auto", origin="lower", cmap="RdYlGn", vmin=-bound, vmax=bound)
        axis.set_title(title, weight="bold")
        axis.set_xlabel("R2 regression window (trading days)")
        axis.set_ylabel("R2 threshold")
        axis.set_xticks(range(len(R2_WINDOWS)), [str(value) for value in R2_WINDOWS])
        axis.set_yticks(range(0, len(R2_THRESHOLDS), 2), [f"{R2_THRESHOLDS[i]:.3f}" for i in range(0, len(R2_THRESHOLDS), 2)])
    fig.suptitle("IC v1.3-r7 full composition — all R2 candidates vs R2-off", fontsize=18, weight="bold")
    fig.text(0.5, 0.025, "Full: 2015-04-16—2026-09-11 (model+real Put); real-only: 2022-09-19—2026-09-11.\nR2 changes the 0.5 momentum F sleeve and its linked momentum Put; grid is included but remains unprotected; IC Call is excluded.", ha="center", color="#526171")
    fig.subplots_adjust(left=0.07, right=0.84, bottom=0.12, top=0.88, hspace=0.22, wspace=0.12)
    color_axis = fig.add_axes([0.88, 0.24, 0.018, 0.52])
    fig.colorbar(image, cax=color_axis, label="change versus R2-off (percentage points; green is better)")
    fig.savefig(RUN / "ic_full_components_r2_full_real_heatmap.png", dpi=180)
    plt.close(fig)


def write_record(summary: pd.DataFrame, validation: dict[str, Any], width: pd.DataFrame) -> None:
    base = summary.loc[summary["candidate"].eq("r2_off")].iloc[0]
    full_pass = int(summary.loc[summary["candidate"].ne("r2_off"), "full_pass_either"].sum())
    real_pass = int(summary.loc[summary["candidate"].ne("r2_off"), "real_pass_either"].sum())
    both_pass = int(summary.loc[summary["candidate"].ne("r2_off"), "pass_both_segments"].sum())
    full_width = width.loc[width["product"].eq("IC") & width["gate"].eq("full")].iloc[0]
    stability = "broad" if bool(full_width["spans_3x3"]) else "narrow"
    lines = [
        "# IC v1.3-r7 全组件 R2 窗口 × 阈值扫描",
        "",
        "状态：`research_only_pending_user_review`；未修改生产信号、账本、规格或交易授权。",
        "",
        "## Run Metadata",
        "",
        f"- Run id: `{RUN.name}`；时区 Asia/Shanghai；数据截止 `{END.date()}`。",
        "- Source-change rule: `research_only_no_source_change`；本研究没有修改生产路径。",
        "",
        "## Research Question",
        "",
        "- 在当前 IC v1.3-r7 完整组合中给动量 F 加 R2 门控，是否相对 R2-off 改善年化收益或最大回撤。",
        "- 判定规则：年化收益或最大回撤（更接近0）任一严格改善即通过；不自动晋级生产。",
        "",
        "## Implementation Anchor",
        "",
        "- 正式重放链：固定0.5倍 IC + 动量0.5倍 IC + 正式网格 + 核心Put + 动量Put；R2只作用于动量 F，并联动其Put目标。",
        "- IC正式规则保持：网格不配Put，Call为0；本扫描没有添加网格Put或IC Call。",
        "",
        "## Data Snapshot",
        "",
        "- Full：2015-04-16—2026-09-11；2022-09-19之前为模型Put延伸，之后为真实510500 Put。真实期：2022-09-19—2026-09-11。",
        "- 尾段2026-08-17—2026-09-11通过同一历史信号入口、CFFEX日行情和510500历史收盘回放；估值输入保留入口的proxy/frozen标记。",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- 期货及Put单边1bp；每1倍期货30%保证金/缓冲；余款年化3%；Put按真实整数张、换月和收盘标价重放。",
        "",
        "## Runtime Override Plan",
        "",
        "- R2-off作为同次默认基线，所有候选重算动量Put、现金和费用；无运行时配置写回。",
        "",
        "## Commands",
        "",
        "- `python -X utf8 refresh_nav_r7_complete_20260912.py`",
        "- `python -X utf8 run_ic_im_v13_r7_r2_full_components_width_scan.py`",
        "",
        "## Output Files",
        "",
        "- `scan_summary.csv`：标准长表；`window_metrics.csv`：标准宽表；`candidate_summary_detail.csv`：展示用全/真实期对照。",
        "- `daily_candidate_returns.csv.gz`：逐日完整组合收益；`tail_candidate_returns.csv.gz`：最近真实续接段。",
        "- `ic_full_components_r2_full_real_heatmap.png`：四面板热图。",
        "",
        "## Full-Sample Results",
        "",
        f"- 全样本：年化 {base.full_ann_return:.2%}，最大回撤 {base.full_max_drawdown:.2%}。",
        f"- 真实期：年化 {base.real_ann_return:.2%}，最大回撤 {base.real_max_drawdown:.2%}。",
        f"- 全样本通过 {full_pass}/231；真实期通过 {real_pass}/231；两段均通过 {both_pass}/231。",
        "",
        "## Window Results",
        "",
        "- Full/10Y/5Y/3Y/1Y 和真实期的逐候选结果见 `scan_summary.csv` 与 `window_metrics.csv`。",
        "",
        "## Stability Classification",
        "",
        f"- Stability label: `{stability}`；全样本最大连续区 {int(full_width.component_cells)}格，覆盖{int(full_width.window_count)}个窗口×{int(full_width.threshold_count)}个阈值，密度{full_width.rectangle_density:.2%}。",
        "- 宽度只说明研究稳健性，不构成参数升级或实盘授权。",
        "",
        "## Decision",
        "",
        "- Decision: `research_only_pending_user_review`；在用户查看完整全样本/真实期对照前，不改变R2-off或任何正式规则。",
        "",
        "## User-Facing Summary",
        "",
        "- 完整组件下的结果已按同一R2-off基线生成；网格和Put不再被排除，但网格仍遵守不配Put的正式规则。",
        "",
        "## Validation Evidence",
        "",
        f"- R2-off历史正式路径逐日收益最大误差：{validation['historical_baseline_ret_parity_max_abs']:.3e}。",
        f"- R2-off尾段完整回放逐日收益最大误差：{validation['tail_baseline_ret_parity_max_abs']:.3e}。",
        f"- R2-off尾段完整回放现金权重最大误差：{validation['tail_baseline_cash_parity_max_abs']:.3e}。",
    ]
    (RUN / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    if not RUN.is_dir():
        raise FileNotFoundError(f"Initialize the scan run before execution: {RUN}")
    if not (REFRESH / "verification.json").is_file():
        raise FileNotFoundError("Latest full component refresh is missing; run refresh_nav_r7_complete_20260912.py first")
    git_before = git_value("status", "--short")
    specs, schedules, schedule_audit = prepare_r2_schedules()

    # Establish formal R2-off parity before dispatching the broad candidate grid.
    main_context = build_context()
    base_historical, base_history_audit = replay_historical(main_context, "r2_off", schedules["r2_off"])
    import run_ic_v13_sleeve_put_independent_replay_v1 as sleeve
    official = pd.read_csv(sleeve.OFFICIAL_DAILY, parse_dates=["date"])
    historical_error = float(np.max(np.abs(base_historical["ret"].to_numpy() - official["ret"].to_numpy())))
    if historical_error > 2e-12:
        raise RuntimeError(f"R2-off historical full-component parity failure: {historical_error}")

    historical_results: dict[str, pd.DataFrame] = {"r2_off": base_historical}
    history_audits: dict[str, dict[str, Any]] = {"r2_off": base_history_audit}
    pending = specs[1:]
    workers = min(4, max(1, os.cpu_count() or 1))
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers, mp_context=mp.get_context("spawn"),
        initializer=worker_init, initargs=(str(SCHEDULE_FILE),),
    ) as pool:
        futures = [pool.submit(worker_replay, item) for item in pending]
        for completed_count, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            candidate, _window, _threshold, frame, audit = future.result()
            historical_results[candidate] = frame
            history_audits[candidate] = audit
            if completed_count % 20 == 0 or completed_count == len(pending):
                print(f"historical full-component candidates complete: {completed_count}/{len(pending)}", flush=True)

    tail_context = load_tail_context()
    tail_results: dict[str, pd.DataFrame] = {}
    for completed_count, (candidate, _window, _threshold) in enumerate(specs, start=1):
        tail_results[candidate] = tail_replay(
            candidate, schedules[candidate], history_audits[candidate],
            main_context["base_frame"], tail_context,
        )
        if completed_count % 40 == 0 or completed_count == len(specs):
            print(f"tail full-component candidates complete: {completed_count}/{len(specs)}", flush=True)

    baseline_tail = tail_results["r2_off"].sort_values("date").reset_index(drop=True)
    expected_tail = tail_context["baseline"].sort_values("date").reset_index(drop=True)
    if not baseline_tail["date"].equals(expected_tail["date"]):
        raise RuntimeError("R2-off tail dates do not match latest full component replay")
    tail_return_error = float(np.max(np.abs(baseline_tail["ret"].to_numpy() - expected_tail["ret"].to_numpy())))
    tail_cash_error = float(np.max(np.abs(baseline_tail["cash_weight"].to_numpy() - expected_tail["cash_weight"].to_numpy())))
    if max(tail_return_error, tail_cash_error) > 2e-12:
        raise RuntimeError(f"R2-off tail parity failure: ret={tail_return_error}, cash={tail_cash_error}")

    summary_rows: list[dict[str, Any]] = []
    metrics_rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    tail_rows: list[pd.DataFrame] = []
    base_metrics: dict[str, dict[str, Any]] = {}
    for candidate, window, threshold in specs:
        historical = historical_results[candidate].sort_values("date").reset_index(drop=True)
        tail = tail_results[candidate].sort_values("date").reset_index(drop=True)
        if historical["date"].iloc[-1] != HISTORICAL_END or tail["date"].iloc[0] <= HISTORICAL_END:
            raise RuntimeError(f"candidate {candidate} historical/tail seam is invalid")
        full = pd.concat([historical, tail], ignore_index=True)
        if full["date"].duplicated().any() or full["date"].iloc[0] != START or full["date"].iloc[-1] != END:
            raise RuntimeError(f"candidate {candidate} full date coverage is invalid")
        full["candidate"] = candidate
        full["product"] = "IC"
        full["r2_window"] = window
        full["r2_threshold"] = threshold
        full["nav"] = (1.0 + full["ret"].astype(float)).cumprod()
        full["drawdown"] = full["nav"] / np.maximum.accumulate(np.r_[1.0, full["nav"].to_numpy()])[1:] - 1.0
        rows = window_metrics(full, candidate, window, threshold)
        metrics_rows.extend(rows)
        by_window = {row["window"]: row for row in rows}
        if candidate == "r2_off":
            base_metrics = {name: by_window[name] for name in ("full", "real_only")}
        else:
            full_delta = by_window["full"]["ann_return"] - base_metrics["full"]["ann_return"]
            full_dd_delta = by_window["full"]["max_drawdown"] - base_metrics["full"]["max_drawdown"]
            real_delta = by_window["real_only"]["ann_return"] - base_metrics["real_only"]["ann_return"]
            real_dd_delta = by_window["real_only"]["max_drawdown"] - base_metrics["real_only"]["max_drawdown"]
        if candidate == "r2_off":
            full_delta = full_dd_delta = real_delta = real_dd_delta = 0.0
        summary_rows.append({
            "product": "IC", "candidate": candidate, "candidate_local": candidate,
            "r2_window": window, "r2_threshold": threshold,
            "full_ann_return": by_window["full"]["ann_return"],
            "full_max_drawdown": by_window["full"]["max_drawdown"],
            "full_return_delta": full_delta, "full_max_dd_delta": full_dd_delta,
            "full_return_improved": bool(full_delta > IMPROVEMENT_EPS),
            "full_max_dd_improved": bool(full_dd_delta > IMPROVEMENT_EPS),
            "full_pass_either": bool(full_delta > IMPROVEMENT_EPS or full_dd_delta > IMPROVEMENT_EPS),
            "real_ann_return": by_window["real_only"]["ann_return"],
            "real_max_drawdown": by_window["real_only"]["max_drawdown"],
            "real_return_delta": real_delta, "real_max_dd_delta": real_dd_delta,
            "real_return_improved": bool(real_delta > IMPROVEMENT_EPS),
            "real_max_dd_improved": bool(real_dd_delta > IMPROVEMENT_EPS),
            "real_pass_either": bool(real_delta > IMPROVEMENT_EPS or real_dd_delta > IMPROVEMENT_EPS),
            "pass_both_segments": bool((full_delta > IMPROVEMENT_EPS or full_dd_delta > IMPROVEMENT_EPS) and (real_delta > IMPROVEMENT_EPS or real_dd_delta > IMPROVEMENT_EPS)),
            "full_start": by_window["full"]["start"], "full_end": by_window["full"]["end"], "full_rows": by_window["full"]["rows"],
            "real_start": by_window["real_only"]["start"], "real_end": by_window["real_only"]["end"], "real_rows": by_window["real_only"]["rows"],
            "full_ann_return_delta_pp": 100.0 * full_delta, "full_max_dd_delta_pp": 100.0 * full_dd_delta,
            "real_ann_return_delta_pp": 100.0 * real_delta, "real_max_dd_delta_pp": 100.0 * real_dd_delta,
        })
        daily_rows.append(full)
        tail_rows.append(full.loc[full["date"].gt(HISTORICAL_END)].copy())

    summary = pd.DataFrame(summary_rows)
    metrics = pd.DataFrame(metrics_rows)
    daily = pd.concat(daily_rows, ignore_index=True)
    tails = pd.concat(tail_rows, ignore_index=True)
    fbase = load_module("full_component_width_fbase", F_BASE_SCRIPT)
    width = pd.concat(
        [fbase.summarize_width(summary.rename(columns={"real_pass_either": "real_only_pass_either"}), gate) for gate in ("full", "real_only", "both")],
        ignore_index=True,
    )
    validation = {
        "historical_baseline_ret_parity_max_abs": historical_error,
        "tail_baseline_ret_parity_max_abs": tail_return_error,
        "tail_baseline_cash_parity_max_abs": tail_cash_error,
        "schedule": schedule_audit,
        "historical_candidates": len(historical_results),
        "tail_candidates": len(tail_results),
        "historical_end": HISTORICAL_END.date().isoformat(),
        "latest_end": END.date().isoformat(),
        "grid_put_nonzero_rows": 0,
        "ic_call_nonzero_rows": 0,
    }
    long_metrics, wide_metrics = checker_compatible_tables(metrics)
    long_metrics.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "candidate_summary_detail.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(RUN / "window_metrics_detail.csv", index=False, encoding="utf-8-sig")
    wide_metrics.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    width.to_csv(RUN / "width_summary.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(RUN / "daily_candidate_returns.csv.gz", index=False, compression="gzip")
    tails.to_csv(RUN / "tail_candidate_returns.csv.gz", index=False, compression="gzip")
    (RUN / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    write_heatmap(summary)
    write_record(summary, validation, width)
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    full_component_width = width.loc[width["product"].eq("IC") & width["gate"].eq("full")].iloc[0]
    stability = "broad" if bool(full_component_width["spans_3x3"]) else "narrow"
    meta.update({
        "phase": "executed",
        "scan_type": "two_dimensional_r2_window_threshold_width_scan_full_components",
        "baseline": {"candidate": "r2_off", "definition": "IC fixed F + momentum F + grid + core/momentum Put; grid Put=0; Call=0"},
        "candidate_grid": {"r2_windows": list(R2_WINDOWS), "r2_thresholds": list(R2_THRESHOLDS), "candidate_count": 231},
        "data_snapshot": {"start": START.date().isoformat(), "historical_component_end": HISTORICAL_END.date().isoformat(), "end": END.date().isoformat(), "real_option_start": REAL_START.date().isoformat(), "tail_refresh": str(REFRESH.relative_to(ROOT))},
        "cost_model": {"futures_one_way": ONE_WAY, "put_one_way": ONE_WAY, "margin_buffer_per_IC_unit": MARGIN, "cash_annual": 0.03},
        "source_hashes": {display_path(path): sha256(path) for path in (F_BASE_SCRIPT, IC_MAINLINE, ASHARE_SOURCE, REFRESH_SCRIPT, SCHEDULE_FILE)},
        "validation": validation,
        "decision": "research_only_pending_user_review",
        "stability_label": stability,
        "git_status_before": git_before,
        "git_status_after": git_value("status", "--short"),
    })
    meta["outputs"].update({
        "candidate_summary_detail": str((RUN / "candidate_summary_detail.csv").relative_to(ROOT)),
        "daily_candidate_returns": str((RUN / "daily_candidate_returns.csv.gz").relative_to(ROOT)),
        "tail_candidate_returns": str((RUN / "tail_candidate_returns.csv.gz").relative_to(ROOT)),
        "width_summary": str((RUN / "width_summary.csv").relative_to(ROOT)),
        "validation": str((RUN / "validation.json").relative_to(ROOT)),
        "heatmap": str((RUN / "ic_full_components_r2_full_real_heatmap.png").relative_to(ROOT)),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    elapsed = time.perf_counter() - started
    (RUN / "command_log.txt").write_text(
        f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\nelapsed_sec={elapsed:.3f}\nworkers={workers}\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "run": str(RUN), "elapsed_sec": round(elapsed, 3),
        "full_pass": int(summary.loc[summary["candidate"].ne("r2_off"), "full_pass_either"].sum()),
        "real_pass": int(summary.loc[summary["candidate"].ne("r2_off"), "real_pass_either"].sum()),
        "stability": stability,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
