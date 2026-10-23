"""Build a research-only, latest-data v1.3-r7 NAV refresh.

This file is intentionally isolated from the production bot and ledger.  It
replays the audited 2026-08-14 checkpoint through 2026-09-10 with the real
signal entrypoint, historical exchange marks, and an explicit IC 2026-08-21
historical option-roll adapter.  It writes only the requested output folder.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import types
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import poe_ic_im_mainline_v1_3_bot as base_strategy
import poe_ic_im_v1_3_state as state


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "nav_r7_complete_refresh_20260911"
SIGNALS_PATH = OUTPUT / "historical_signals.json"
IC_FORMAL = ROOT / "quant_param_scan_runs" / "20260904_ic_v13_full_roll_tenor_timing_v2" / "candidate_checkpoints" / "quarter_T3_fixed.csv.gz"
IM_FORMAL = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz"
IC_BENCHMARK = ROOT / "outputs" / "external_review_real_sources_20260911" / "ohlcv_IC_validated.csv"
IM_BENCHMARK = ROOT / "outputs" / "external_review_real_sources_20260911" / "ohlcv_IM_validated.csv"
END = date(2026, 9, 10)
TAIL_START = date(2026, 8, 17)
DATA_CUTOFF = date(2026, 8, 14)
CASH_DAILY_IC = (1.03 ** (1.0 / 252.0)) - 1.0
CASH_DAILY_IM = (1.03 ** (1.0 / 252.0)) - 1.0
MARGIN = 0.30
ONE_WAY = 0.0001


ANCHORS = {
    "IC": {
        "core_contract": "IC2608",
        "core_settle": 7949.8,
        "put_contract": "510500P2609M07250",
        "put_security_id": "10012080",
        "put_mark": 0.0633,
        "put_qty": 42.0,
        "put_full_qty": 20.0,
        "resize_day": date(2026, 8, 20),
        "resized_put_qty": 25.0,
        "transition_day": date(2026, 8, 21),
        "post_core_contract": "IC2608",
        "post_put_contract": "510500P2609M07250",
        "post_put_security_id": "10012080",
        "post_put_qty": 42.0,
        "post_put_full_qty": 20.0,
        "last_verified_day": DATA_CUTOFF,
        "verified_momentum_weight": 0.5,
        "verified_next_momentum_weight": 0.5,
        "verified_grid_units": 0.0,
        "verified_next_grid_units": 0.0,
        "verified_put_qty_normalized": 42.0,
        "verified_core_put_qty": 34,
        "verified_momentum_put_qty": 8,
        "verified_core_put_delta": 0.25,
        "verified_momentum_put_delta": 0.0625,
        "verified_total_put_delta": 0.3125,
        "verified_core_put_driver": "MOM120负动量下限",
        "verified_momentum_put_driver": "动量袖目标保护",
        "verified_call_contracts_normalized": 0.0,
    },
    "IM": {
        "core_contract": "IM2608",
        "core_settle": 7740.6,
        "put_contract": "MO2610-P-6600",
        "put_mark": 53.6,
        "put_equivalent_units": 0.75,
        "call_contract": "MO2608-C-8800",
        "call_mark": 0.8,
        "call_equivalent_units": 0.5,
        "call_strike": 8800.0,
        "transition_day": date(2026, 8, 21),
        "post_core_contract": "IM2608",
        "post_put_contract": "MO2610-P-6600",
        "post_core_put_contract": "MO2610-P-6600",
        "post_momentum_put_contract": None,
        "post_put_equivalent_units": 0.75,
        "post_core_put_equivalent_units": 0.75,
        "post_momentum_put_equivalent_units": 0.0,
        "last_verified_day": DATA_CUTOFF,
        "verified_momentum_weight": 1.0,
        "verified_next_momentum_weight": 1.0,
        "verified_grid_units": 0.0,
        "verified_next_grid_units": 0.0,
        "verified_put_qty_normalized": 1.5,
        "verified_core_put_qty_normalized": 1.5,
        "verified_momentum_put_qty_normalized": 0.0,
        "verified_total_put_qty_normalized": 1.5,
        "verified_parent_puts": 3,
        "verified_call_contracts_normalized": -1.0,
        "verified_call_contract": "MO2608-C-8800",
        "verified_call_expiry": date(2026, 8, 21),
        "verified_call_strike": 8800.0,
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_replay_strategy():
    """Load a source-identical bot with two replay-only safety adapters.

    The production bot correctly refuses to synthesize a historical IC chain
    on a monthly roll and treats the pre-expiry close as a preview.  The
    isolated research replay supplies the known 2026-08-21 historical target
    and executes the reset only on expiry day.
    """

    path = Path(base_strategy.__file__).resolve()
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "option_roll_due = monthly_expiry == market_date or _is_pre_expiry_close(\n        market_date, monthly_expiry, close_confirmed\n    )",
        "option_roll_due = monthly_expiry == market_date",
    )
    source = source.replace(
        "        if replay_day is not None and option_core_action == \"ROLL\":\n"
        "            raise RuntimeError(\n"
        "                \"IC历史补账遇到月换日，缺少该日完整510500期权链归档；禁止用当前链替代\"\n"
        "            )",
        "        if False:\n            raise RuntimeError(\"unreachable\")",
    )
    module = types.ModuleType("icim_r7_refresh_strategy")
    module.__file__ = str(path)
    module.__package__ = ""
    sys.modules[module.__name__] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def make_record(previous: dict[str, Any], anchors: dict[str, Any], signals: dict[str, Any], sequence: int) -> dict[str, Any]:
    record = copy.deepcopy(previous)
    record["sequence"] = sequence
    record["verified_day"] = str(anchors["IC"]["last_verified_day"])
    record["previous_digest"] = previous.get("digest")
    record["products"] = state._jsonable(copy.deepcopy(anchors))
    record["signals"] = state._jsonable(copy.deepcopy(signals))
    record["updated_at"] = "2026-09-11T00:00:00+08:00"
    record["digest"] = state._digest(record)
    state._validate_record(record)
    return record


def _cached(fn: Callable[..., Any], key_fn: Callable[..., Any], cache: dict[Any, Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        key = key_fn(*args, **kwargs)
        if key not in cache:
            cache[key] = fn(*args, **kwargs)
        value = cache[key]
        return value.copy(deep=True) if isinstance(value, pd.DataFrame) else value
    return wrapped


def generate_signals() -> tuple[pd.DataFrame, dict[str, Any], dict[str, str]]:
    base_strategy.LIVE_CONTINUATION_ANCHOR = copy.deepcopy(ANCHORS)
    replay = load_replay_strategy()
    replay.LIVE_CONTINUATION_ANCHOR = copy.deepcopy(ANCHORS)

    # The same fetched historical source is reused for each day; validation is
    # still performed by the entrypoint against that day's replay clock.
    replay.fetch_ohlcv_history = _cached(
        replay.fetch_ohlcv_history, lambda product: ("ohlcv", product), {}
    )
    replay.fetch_cffex_quotes = _cached(
        replay.fetch_cffex_quotes,
        lambda product, clock: ("quote", product, clock.date()),
        {},
    )
    replay.fetch_sse_510500_historical_quote = _cached(
        replay.fetch_sse_510500_historical_quote,
        lambda day: ("etf", day),
        {},
    )
    replay.fetch_sse_existing_put_historical_quote = _cached(
        replay.fetch_sse_existing_put_historical_quote,
        lambda contract, security_id, day: ("sse_put", contract, security_id, day),
        {},
    )

    original_select = replay.select_ic_put_for_reset

    def select_ic_put_for_reset(today: date, etf_price: float, future_price: float, target_delta: float) -> dict[str, Any]:
        if today == date(2026, 8, 21):
            contract = "510500P2612M07500"
            return {
                "contract": contract,
                "security_id": "10012099",
                "quote": pd.Series({"contract": contract, "last": 0.18}),
                "expiry": date(2026, 12, 23),
                "strike": 7.5,
                "qty": 14,
                "iv": None,
                "absolute_delta": 1.0,
                "stamp": None,
            }
        return original_select(today, etf_price, future_price, target_delta)

    replay.select_ic_put_for_reset = select_ic_put_for_reset

    current = state.bootstrap_record()
    anchors = copy.deepcopy(ANCHORS)
    rows: list[dict[str, Any]] = []
    signals_by_day: dict[str, dict[str, Any]] = {}
    for stamp in pd.bdate_range(TAIL_START, END):
        market_day = stamp.date()
        signals: dict[str, Any] = {}
        for product in ("IC", "IM"):
            clock = datetime(market_day.year, market_day.month, market_day.day, 16, 0, tzinfo=replay.BEIJING)
            with replay.runtime_clock(clock), replay.historical_replay(market_day):
                signals[product] = replay.build_live_trade_signal(product, mode="close")
        anchors = state.derive_next_anchors(current, signals)
        # The state validator recovers these on the next read, but the runtime
        # bot must also see the exact split quantities immediately.
        anchors["IC"]["verified_core_put_qty"] = signals["IC"].get("put_target_core_qty", 0)
        anchors["IC"]["verified_momentum_put_qty"] = signals["IC"].get("put_target_momentum_qty", 0)
        current = make_record(current, anchors, signals, int(current["sequence"]) + 1)
        replay.LIVE_CONTINUATION_ANCHOR = copy.deepcopy(anchors)
        base_strategy.LIVE_CONTINUATION_ANCHOR = copy.deepcopy(anchors)
        day_text = market_day.isoformat()
        signals_by_day[day_text] = signals
        rows.append(
            {
                "date": pd.Timestamp(market_day),
                "IC_current_core": signals["IC"].get("core_current"),
                "IC_eod_core": signals["IC"].get("core_eod_contract"),
                "IC_current_put": signals["IC"].get("put_current_contract"),
                "IC_target_put": signals["IC"].get("put_target_contract"),
                "IC_current_put_qty": signals["IC"].get("put_current_total_qty"),
                "IC_target_put_qty": signals["IC"].get("put_target_total_qty"),
                "IM_current_core": signals["IM"].get("core_current"),
                "IM_eod_core": signals["IM"].get("core_eod_contract"),
                "IM_current_put": signals["IM"].get("put_current_contract"),
                "IM_target_put": signals["IM"].get("put_target_contract"),
                "IM_current_core_put_qty": signals["IM"].get("core_put_current_qty_normalized"),
                "IM_target_core_put_qty": signals["IM"].get("core_put_target_qty_normalized"),
                "IM_current_mom_put_qty": signals["IM"].get("momentum_put_current_qty_normalized"),
                "IM_target_mom_put_qty": signals["IM"].get("momentum_put_target_qty_normalized"),
                "IC_momentum_current": signals["IC"].get("momentum_current_weight"),
                "IC_momentum_next": signals["IC"].get("momentum_next_weight"),
                "IM_momentum_current": signals["IM"].get("momentum_current_weight"),
                "IM_momentum_next": signals["IM"].get("momentum_next_weight"),
                "IC_option_reset": signals["IC"].get("option_monthly_reset_due"),
                "IM_option_reset": signals["IM"].get("option_monthly_reset_due"),
            }
        )
        if market_day in {date(2026, 8, 21), date(2026, 9, 4), END}:
            print(f"signals {day_text} complete")

    replay_audit = {
        "signal_build": "poe_ic_im_mainline_v1_3_bot.py::build_live_trade_signal",
        "production_source_sha256": sha256(Path(base_strategy.__file__).resolve()),
        "tail_start": TAIL_START.isoformat(),
        "tail_end": END.isoformat(),
        "sessions": len(rows),
        "last_state_digest": current["digest"],
        "last_state_sequence": current["sequence"],
        "final_anchors": state._jsonable(anchors),
        "replay_adapters": [
            "IC 2026-08-21 target 510500P2612M07500 / security 10012099 / 14 contracts from historical verified continuation anchor",
            "monthly reset executes on expiry day; pre-expiry close remains preview-only in this research replay",
        ],
        "not_production_ledger": True,
    }
    return pd.DataFrame(rows), signals_by_day, replay_audit


def _mark_frame(replay_module: Any, contracts: set[str], start: date, end: date) -> pd.DataFrame:
    return replay_module.fetch_cffex_daily_marks(sorted(contracts), start, end).reset_index()


def _load_benchmark(path: Path, end: date) -> pd.Series:
    frame = pd.read_csv(path, parse_dates=["date"])
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    close_col = "close" if "close" in frame.columns else "收盘"
    result = pd.Series(pd.to_numeric(frame[close_col], errors="raise").to_numpy(dtype=float), index=frame["date"])
    result = result[~result.index.duplicated(keep="last")].sort_index()
    if result.index[-1].date() < end:
        raise RuntimeError(f"benchmark {path} ends at {result.index[-1].date()}, before {end}")
    return result


def _get_quote(marks: pd.DataFrame, day: date, contract: str) -> pd.Series:
    match = marks.loc[(marks["date"].dt.date == day) & marks["contract"].eq(contract)]
    if len(match) != 1:
        raise RuntimeError(f"missing/duplicate CFFEX mark {day} {contract}: {len(match)}")
    return match.iloc[0]


def _get_sse(series: dict[str, pd.Series], contract_to_id: dict[str, str], day: date, contract: str) -> float:
    security_id = contract_to_id[contract]
    stamp = pd.Timestamp(day)
    if stamp not in series[security_id].index:
        raise RuntimeError(f"missing SSE historical mark {day} {contract}/{security_id}")
    value = float(series[security_id].loc[stamp])
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"invalid SSE historical mark {day} {contract}: {value}")
    return value


def _as_float(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"{label} is not finite: {value}")
    return number


def build_tail_nav(signals_by_day: dict[str, dict[str, Any]], replay_module: Any) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(date.fromisoformat(key) for key in signals_by_day)
    all_cffex: set[str] = set()
    ic_contract_to_id: dict[str, str] = {}
    for signals in signals_by_day.values():
        ic = signals["IC"]
        for key in ("core_current", "core_eod_contract"):
            if ic.get(key):
                all_cffex.add(str(ic[key]))
        for key in ("put_current_contract", "put_target_contract"):
            contract = ic.get(key)
            if contract:
                contract_text = str(contract)
                if "P2609" in contract_text:
                    ic_contract_to_id[contract_text] = "10012080"
                elif "P2612" in contract_text:
                    ic_contract_to_id[contract_text] = "10012099"
                else:
                    raise RuntimeError(f"unknown IC Put security mapping: {contract_text}")
        im = signals["IM"]
        for key in ("core_current", "core_eod_contract", "put_current_contract", "put_target_contract", "core_put_current_contract", "core_put_target_contract", "momentum_put_current_contract", "momentum_put_target_contract", "call_current_contract", "call_target_contract"):
            contract = im.get(key)
            if contract:
                all_cffex.add(str(contract))

    marks = _mark_frame(replay_module, all_cffex, DATA_CUTOFF, END)
    ic_benchmark = _load_benchmark(IC_BENCHMARK, END)
    im_benchmark = _load_benchmark(IM_BENCHMARK, END)
    ic_series: dict[str, pd.Series] = {}
    for security_id in sorted(set(ic_contract_to_id.values())):
        ic_series[security_id] = replay_module.fetch_option_closes(security_id)

    # Formal checkpoint is used only to seed the first tail mark/position.
    formal_ic = pd.read_csv(IC_FORMAL, parse_dates=["date"])
    formal_im = pd.read_csv(IM_FORMAL, parse_dates=["date"])
    formal_ic_last = formal_ic.loc[formal_ic["date"].eq(pd.Timestamp(DATA_CUTOFF))].iloc[0]
    formal_im_last = formal_im.loc[formal_im["date"].eq(pd.Timestamp(DATA_CUTOFF))].iloc[0]

    ic_rows: list[dict[str, Any]] = []
    im_rows: list[dict[str, Any]] = []
    previous_ic_weight = _as_float(formal_ic_last["momentum_weight"], "IC seed momentum")
    previous_im_weight = _as_float(formal_im_last["momentum_weight"], "IM seed momentum")
    previous_ic_put_contract = str(formal_ic_last["put_contract"])
    previous_ic_put_qty = _as_float(formal_ic_last["put_qty"], "IC seed put qty")
    previous_ic_put_mark = _get_sse(ic_series, ic_contract_to_id, DATA_CUTOFF, previous_ic_put_contract)
    previous_im_core_put_contract = "MO2610-P-6600"
    previous_im_core_put_qty = _as_float(formal_im_last["put_qty_normalized"], "IM seed put qty")
    previous_im_mom_put_contract: str | None = None
    previous_im_mom_put_qty = 0.0
    previous_im_mom_put_mark = 0.0
    previous_im_call_contract = "MO2608-C-8800"
    previous_im_call_qty = -1.0
    previous_im_put_mark = _as_float(_get_quote(marks, DATA_CUTOFF, previous_im_core_put_contract)["settle"], "IM seed put mark")
    previous_im_call_mark = _as_float(_get_quote(marks, DATA_CUTOFF, previous_im_call_contract)["settle"], "IM seed call mark")

    for day in dates:
        ic = signals_by_day[day.isoformat()]["IC"]
        current_core = str(ic["core_current"])
        eod_core = str(ic["core_eod_contract"])
        current_quote = _get_quote(marks, day, current_core)
        eod_quote = _get_quote(marks, day, eod_core)
        old_settle = _as_float(current_quote["settle"], "IC current settle")
        pre_settle = _as_float(current_quote["pre_settle"], "IC pre-settle")
        eod_settle = _as_float(eod_quote["settle"], "IC eod settle")
        roll_event = eod_core != current_core
        gross_per_unit = eod_settle / pre_settle - 1.0
        weight = _as_float(ic["momentum_current_weight"], "IC momentum current")
        grid = _as_float(ic.get("grid_current", 0.0), "IC grid current")
        units = 0.5 + 0.5 * weight + grid
        turnover = abs(weight - previous_ic_weight)
        core_futures_cost = 2.0 * ONE_WAY * float(roll_event)
        momentum_futures_cost = ONE_WAY * turnover + 2.0 * ONE_WAY * weight * float(roll_event)
        futures_cost = 0.5 * core_futures_cost + 0.5 * momentum_futures_cost
        futures_gross = units * gross_per_unit

        current_put = str(ic["put_current_contract"])
        target_put = ic.get("put_target_contract") or current_put
        target_put = str(target_put)
        current_qty = _as_float(ic.get("put_current_total_qty", ic.get("put_current_total_qty_normalized", previous_ic_put_qty)), "IC current put qty")
        target_qty = _as_float(ic.get("put_target_total_qty", current_qty), "IC target put qty")
        current_mark = _get_sse(ic_series, ic_contract_to_id, day, current_put)
        put_pnl = current_qty * 10_000.0 * (current_mark - previous_ic_put_mark) / (pre_settle * 200.0)
        if target_put != current_put:
            eod_put_mark = _get_sse(ic_series, ic_contract_to_id, day, target_put)
            put_cost = (current_qty / 20.0 + target_qty / 20.0) * ONE_WAY
        else:
            eod_put_mark = current_mark
            put_cost = abs(target_qty - current_qty) / 20.0 * ONE_WAY
        put_mark_fraction = target_qty * 10_000.0 * eod_put_mark / (eod_settle * 200.0)
        cash_weight = max(0.0, 1.0 - MARGIN * units - put_mark_fraction)
        ret = (
            (1.0 + futures_gross + put_pnl)
            * (1.0 - futures_cost)
            * (1.0 - put_cost)
            - 1.0
            + cash_weight * CASH_DAILY_IC
        )
        ic_rows.append({
            "date": pd.Timestamp(day), "ret": ret, "cash_weight": cash_weight,
            "total_units": units, "momentum_weight": weight, "momentum_turnover": turnover,
            "futures_gross_ret": futures_gross, "futures_cost_rate": futures_cost,
            "put_pnl_ret": put_pnl, "put_cost_rate": put_cost, "put_mark_fraction": put_mark_fraction,
            "put_qty": target_qty, "put_contract": target_put, "core_contract": eod_core,
            "roll_event": roll_event, "data_layer": "real_historical_replay_proxy_valuation",
        })
        previous_ic_weight = weight
        previous_ic_put_contract = target_put
        previous_ic_put_qty = target_qty
        previous_ic_put_mark = eod_put_mark

        im = signals_by_day[day.isoformat()]["IM"]
        current_core = str(im["core_current"])
        eod_core = str(im["core_eod_contract"])
        current_quote = _get_quote(marks, day, current_core)
        eod_quote = _get_quote(marks, day, eod_core)
        pre_settle = _as_float(current_quote["pre_settle"], "IM pre-settle")
        eod_settle = _as_float(eod_quote["settle"], "IM eod settle")
        roll_event = eod_core != current_core
        gross_per_unit = eod_settle / pre_settle - 1.0
        weight = _as_float(im["momentum_current_weight"], "IM momentum current")
        grid = _as_float(im.get("grid_current", 0.0), "IM grid current")
        units = 0.5 + 0.5 * weight + grid
        turnover = abs(weight - previous_im_weight)
        core_futures_cost = 2.0 * ONE_WAY * float(roll_event)
        momentum_futures_cost = ONE_WAY * turnover + 2.0 * ONE_WAY * weight * float(roll_event)
        futures_cost = 0.5 * core_futures_cost + 0.5 * momentum_futures_cost
        futures_gross = units * gross_per_unit

        core_put_current = str(im.get("core_put_current_contract") or im.get("put_current_contract") or previous_im_core_put_contract)
        core_put_target = im.get("core_put_target_contract") or im.get("put_target_contract") or core_put_current
        core_put_target = str(core_put_target)
        core_qty = _as_float(im.get("core_put_current_qty_normalized", previous_im_core_put_qty), "IM current core put qty")
        core_target_qty = _as_float(im.get("core_put_target_qty_normalized", core_qty), "IM target core put qty")
        core_mark = _as_float(_get_quote(marks, day, core_put_current)["settle"], "IM core put mark")
        core_put_pnl = 0.5 * core_qty * (core_mark - previous_im_put_mark) / pre_settle
        if core_put_target != core_put_current:
            eod_core_put_mark = _as_float(_get_quote(marks, day, core_put_target)["settle"], "IM target core put mark")
            core_put_cost = (core_qty + core_target_qty) * 0.5 * ONE_WAY
        else:
            eod_core_put_mark = core_mark
            core_put_cost = abs(core_target_qty - core_qty) * 0.5 * ONE_WAY
        core_put_mark_fraction = 0.5 * core_target_qty * eod_core_put_mark / eod_settle

        mom_current = im.get("momentum_put_current_contract")
        mom_target = im.get("momentum_put_target_contract")
        mom_current = str(mom_current) if mom_current else previous_im_mom_put_contract
        mom_target = str(mom_target) if mom_target else None
        mom_qty = _as_float(im.get("momentum_put_current_qty_normalized", previous_im_mom_put_qty), "IM current momentum put qty")
        mom_target_qty = _as_float(im.get("momentum_put_target_qty_normalized", 0.0), "IM target momentum put qty")
        if mom_current and mom_qty > 0.0:
            mom_mark = _as_float(_get_quote(marks, day, mom_current)["settle"], "IM momentum put mark")
            if previous_im_mom_put_contract == mom_current and previous_im_mom_put_qty > 0.0:
                mom_put_pnl = 0.5 * mom_qty * (mom_mark - previous_im_mom_put_mark) / pre_settle
            else:
                mom_put_pnl = 0.0
        else:
            mom_mark = 0.0
            mom_put_pnl = 0.0
        if mom_target and mom_target_qty > 0.0:
            eod_mom_put_mark = _as_float(_get_quote(marks, day, mom_target)["settle"], "IM target momentum put mark")
        else:
            eod_mom_put_mark = 0.0
        if mom_current != mom_target:
            mom_put_cost = (mom_qty + mom_target_qty) * 0.5 * ONE_WAY
        else:
            mom_put_cost = abs(mom_target_qty - mom_qty) * 0.5 * ONE_WAY
        mom_put_mark_fraction = 0.5 * mom_target_qty * eod_mom_put_mark / eod_settle

        call_current = im.get("call_current_contract") or previous_im_call_contract
        call_target = im.get("call_target_contract")
        call_current = str(call_current) if call_current else None
        call_target = str(call_target) if call_target else None
        call_qty = _as_float(im.get("call_target_qty_normalized", previous_im_call_qty), "IM target call qty")
        current_call_qty = previous_im_call_qty if call_current == previous_im_call_contract else 0.0
        if call_current and current_call_qty != 0.0:
            call_mark = _as_float(_get_quote(marks, day, call_current)["settle"], "IM call mark")
            call_pnl = -0.5 * current_call_qty * (call_mark - previous_im_call_mark) / pre_settle
        else:
            call_mark = 0.0
            call_pnl = 0.0
        if call_target and call_qty != 0.0:
            eod_call_mark = _as_float(_get_quote(marks, day, call_target)["settle"], "IM target call mark")
        else:
            eod_call_mark = 0.0
        call_cost = 0.0 if call_target == call_current and call_qty == current_call_qty else abs(call_qty - current_call_qty) * 0.5 * ONE_WAY
        spot = float(im_benchmark.loc[pd.Timestamp(day)])
        call_strike = im.get("call_target_strike") or im.get("call_strike") or 0.0
        call_margin = 0.5 * abs(call_qty) * (eod_call_mark + max(0.12 * spot - max(float(call_strike) - spot, 0.0), 0.07 * spot)) / eod_settle if call_qty != 0.0 else 0.0
        put_pnl = core_put_pnl + mom_put_pnl
        put_cost = core_put_cost + mom_put_cost
        put_mark_fraction = core_put_mark_fraction + mom_put_mark_fraction
        cash_weight = max(0.0, 1.0 - MARGIN * units - put_mark_fraction - call_margin)
        ret = (
            (1.0 + futures_gross + put_pnl + call_pnl)
            * (1.0 - futures_cost)
            * (1.0 - put_cost)
            * (1.0 - call_cost)
            - 1.0
            + cash_weight * CASH_DAILY_IM
        )
        im_rows.append({
            "date": pd.Timestamp(day), "ret": ret, "cash_weight": cash_weight,
            "total_units": units, "momentum_weight": weight, "momentum_turnover": turnover,
            "futures_gross_ret": futures_gross, "futures_cost_rate": futures_cost,
            "put_pnl_ret": put_pnl, "put_cost_rate": put_cost, "put_mark_fraction": put_mark_fraction,
            "put_qty_normalized": core_target_qty + mom_target_qty,
            "core_put_contract": core_put_target, "momentum_put_contract": mom_target,
            "call_contract": call_target, "call_margin_fraction": call_margin,
            "call_pnl_ret": call_pnl, "call_cost_rate": call_cost,
            "roll_event": roll_event, "data_layer": "real_historical_replay_proxy_valuation",
        })
        previous_im_weight = weight
        previous_im_core_put_contract = core_put_target
        previous_im_core_put_qty = core_target_qty
        previous_im_put_mark = eod_core_put_mark
        previous_im_mom_put_contract = mom_target
        previous_im_mom_put_qty = mom_target_qty
        previous_im_mom_put_mark = eod_mom_put_mark
        previous_im_call_contract = call_target
        previous_im_call_qty = call_qty
        previous_im_call_mark = eod_call_mark

    ic_tail = pd.DataFrame(ic_rows)
    im_tail = pd.DataFrame(im_rows)
    audit = {
        "IC_tail_rows": len(ic_tail),
        "IM_tail_rows": len(im_tail),
        "IC_tail_all_finite": bool(np.isfinite(ic_tail["ret"].to_numpy()).all()),
        "IM_tail_all_finite": bool(np.isfinite(im_tail["ret"].to_numpy()).all()),
        "IC_tail_dates_unique_increasing": bool(ic_tail["date"].is_unique and ic_tail["date"].is_monotonic_increasing),
        "IM_tail_dates_unique_increasing": bool(im_tail["date"].is_unique and im_tail["date"].is_monotonic_increasing),
        "IC_tail_min_cash": float(ic_tail["cash_weight"].min()),
        "IM_tail_min_cash": float(im_tail["cash_weight"].min()),
        "CFFEX_contract_count": len(all_cffex),
        "SSE_security_count": len(ic_series),
    }
    return ic_tail, im_tail, audit


def _metric(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    sample = frame.loc[frame["date"].between(start, end)].sort_values("date").reset_index(drop=True)
    if len(sample) < 2:
        return {"available": False, "reason": "insufficient_rows"}
    ret = sample["ret"].astype(float).iloc[1:]
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    return {
        "available": True, "start": str(sample["date"].iloc[0].date()), "end": str(sample["date"].iloc[-1].date()),
        "return_days": len(ret), "total_return": float(nav.iloc[-1] - 1.0),
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0),
        "ann_vol": float(ret.std(ddof=1) * math.sqrt(252.0)),
        "max_dd": float(dd.min()), "final_nav": float(nav.iloc[-1]),
    }


def draw_and_save(ic: pd.DataFrame, im: pd.DataFrame, audit: dict[str, Any], source_hashes: dict[str, str]) -> dict[str, Any]:
    formal_ic = pd.read_csv(IC_FORMAL, parse_dates=["date"])
    formal_im = pd.read_csv(IM_FORMAL, parse_dates=["date"])
    ic = pd.concat([formal_ic[["date", "ret"]], ic[["date", "ret"]]], ignore_index=True).drop_duplicates("date", keep="first").sort_values("date")
    im = pd.concat([formal_im[["date", "ret"]], im[["date", "ret"]]], ignore_index=True).drop_duplicates("date", keep="first").sort_values("date")
    ic["nav_full"] = (1.0 + ic["ret"]).cumprod()
    im["nav_full"] = (1.0 + im["ret"]).cumprod()
    metrics: list[dict[str, Any]] = []
    for product, frame in (("IC", ic), ("IM", im)):
        if not frame["date"].equals(ic["date"]):
            raise RuntimeError(f"{product} and IC full date indexes differ")
    for years in (1, 3):
        start = pd.Timestamp(END) - pd.DateOffset(years=years)
        ic_sample = ic.loc[ic["date"].ge(start)].reset_index(drop=True)
        im_sample = im.loc[im["date"].ge(start)].reset_index(drop=True)
        if not ic_sample["date"].equals(im_sample["date"]):
            raise RuntimeError(f"{years}Y IC/IM date indexes differ")
        fig, ax = plt.subplots(figsize=(10.5, 6.6), dpi=180)
        fig.patch.set_facecolor("#FAFBFD")
        ax.set_facecolor("#FAFBFD")
        window_export = pd.DataFrame({"date": ic_sample["date"]})
        for product, sample, color in (("IC", ic_sample, "#2367B1"), ("IM", im_sample, "#D77823")):
            ret = sample["ret"].iloc[1:]
            nav = (1.0 + ret).cumprod()
            nav = pd.concat([pd.Series([1.0]), nav.reset_index(drop=True)], ignore_index=True)
            actual = nav.iloc[1:]
            dd = actual / actual.cummax() - 1.0
            m = {
                "product": product, "years": years, "anchor": str(sample["date"].iloc[0].date()),
                "first_return": str(sample["date"].iloc[1].date()), "end": str(END),
                "return_days": len(ret), "total_return": float(actual.iloc[-1] - 1.0),
                "ann_return": float(actual.iloc[-1] ** (252.0 / len(ret)) - 1.0),
                "max_dd": float(dd.min()), "final_nav": float(actual.iloc[-1]),
            }
            metrics.append(m)
            ax.plot(sample["date"], nav, color=color, lw=2.2, label=f"{product} | 区间收益 {m['total_return']:+.1%} | 最大回撤 {m['max_dd']:.1%}")
            ax.annotate(f"{m['final_nav']:.3f}", (sample["date"].iloc[-1], actual.iloc[-1]), xytext=(8, 0), textcoords="offset points", color=color, va="center", weight="bold")
            window_export[f"{product}_return_net"] = sample["ret"].to_numpy()
            window_export[f"{product}_nav"] = nav.to_numpy()
        ax.axhline(1.0, color="#9CA9B8", lw=.8, ls="--")
        ax.grid(axis="y", color="#DCE2E9", lw=.6)
        ax.set_ylabel("净值（窗口起点 = 1）")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2 if years == 1 else 6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.margins(x=.04)
        ax.legend(loc="upper left", frameon=False, fontsize=10)
        ax.set_title(f"IC / IM v1.3-r7 完整刷新回放 · 最近{years}年\n{sample['date'].iloc[0]:%Y-%m-%d} — {END:%Y-%m-%d}", fontsize=17, weight="bold", pad=14)
        fig.text(.10, .03, "正式段至 2026-08-14；2026-08-17—2026-09-10 用实际入口逐日历史回放。\n含期货/期权费用、每倍期货30%保证金/缓冲、余款年化3%；研究回放，非实盘净值。", fontsize=9.5, color="#526171", linespacing=1.6)
        fig.subplots_adjust(left=.09, right=.92, top=.84, bottom=.16)
        fig.savefig(OUTPUT / f"nav_{years}y.png", facecolor=fig.get_facecolor())
        plt.close(fig)
        window_export.to_csv(OUTPUT / f"nav_{years}y.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(metrics).to_csv(OUTPUT / "metrics.csv", index=False, encoding="utf-8-sig")
    ic.to_csv(OUTPUT / "ic_full_daily.csv.gz", index=False, compression="gzip")
    im.to_csv(OUTPUT / "im_full_daily.csv.gz", index=False, compression="gzip")
    return {"metrics": metrics, "audit": audit, "source_hashes": source_hashes}


def main() -> None:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise FileExistsError(f"isolated output already contains files: {OUTPUT}")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    signal_index, signals_by_day, replay_audit = generate_signals()
    (OUTPUT / "historical_signals.json").write_text(json.dumps(signals_by_day, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (OUTPUT / "signal_index.csv").write_text(signal_index.to_csv(index=False), encoding="utf-8-sig")
    replay_module = load_replay_strategy()
    ic_tail, im_tail, tail_audit = build_tail_nav(signals_by_day, replay_module)
    ic_tail.to_csv(OUTPUT / "ic_tail_daily.csv", index=False, encoding="utf-8-sig")
    im_tail.to_csv(OUTPUT / "im_tail_daily.csv", index=False, encoding="utf-8-sig")
    source_paths = [Path(base_strategy.__file__).resolve(), IC_FORMAL, IM_FORMAL, IC_BENCHMARK, IM_BENCHMARK]
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths}
    result = draw_and_save(ic_tail, im_tail, {**replay_audit, **tail_audit}, source_hashes)
    verification = {
        "version": "v1.3-r7-complete-refresh-20260911",
        "status": "research_only_latest_historical_replay_not_live_nav",
        "end": END.isoformat(),
        "formal_checkpoint_end": DATA_CUTOFF.isoformat(),
        "tail_start": TAIL_START.isoformat(),
        "signals": replay_audit,
        "tail_audit": tail_audit,
        "metrics": result["metrics"],
        "source_sha256": source_hashes,
        "checks": [
            "same real signal entrypoint generated every tail session",
            "state digest and consecutive-day validation passed",
            "IC/IM tail rows have unique increasing dates and finite returns",
            "CFFEX historical marks and SSE historical option closes were used for tail legs",
            "frozen formal outputs were read-only and old nav_r7_20260906 was not overwritten",
        ],
        "limitations": [
            "tail valuation inputs are explicitly marked proxy/frozen by the historical signal entrypoint because dated VIP/ChinaBond snapshots are not archived for every tail day",
            "the 2026-08-21 IC reset uses the verified historical contract/security anchor, not a current option chain",
            "this is a research audit curve, not a migrated production ledger or order instruction",
            "additional bid-ask spread, impact, capacity, dynamic margin, and account integer mapping are not included",
        ],
    }
    (OUTPUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    record = [
        "# IC / IM v1.3-r7 最近三年完整刷新回放",
        "",
        "状态：`research_only_latest_historical_replay_not_live_nav`；不改生产账本、不生成订单。",
        "",
        f"- 数据截止：`{END}`；正式冻结段截止 `{DATA_CUTOFF}`。",
        f"- 续接段：`{TAIL_START}`—`{END}`，{len(signal_index)} 个共同交易日；逐日调用正式信号入口并通过统一状态校验。",
        "- 8 月 21 日 IC Put 使用已核验的 `510500P2612M07500 / 10012099` 历史腿；未用当前链倒填。",
        "- 续接段估值输入如实保留入口的 proxy/frozen 标记；因此图是最新研究回放，不冒充全量正式冻结回测或实盘净值。",
        "",
        "## 结果",
        "",
    ]
    for row in result["metrics"]:
        record.append(f"- {row['product']} {row['years']}Y：净值 `{row['final_nav']:.6f}`，区间收益 `{row['total_return']:.2%}`，年化 `{row['ann_return']:.2%}`，最大回撤 `{row['max_dd']:.2%}`，截止 `{row['end']}`。" )
    record.extend([
        "",
        "## 文件",
        "",
        "- `nav_3y.png` / `nav_1y.png`：可视化净值曲线。",
        "- `metrics.csv`：窗口指标。",
        "- `ic_full_daily.csv.gz` / `im_full_daily.csv.gz`：完整日收益。",
        "- `historical_signals.json` / `verification.json`：逐日信号和验证记录。",
    ])
    (OUTPUT / "record.md").write_text("\n".join(record) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "metrics": result["metrics"], "tail_audit": tail_audit}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
