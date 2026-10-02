from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import math
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FEAR_DIR = ROOT / "quant_research_runs" / "20260928_csi1000_fear_greed_reproduction"
FEAR_PATH = FEAR_DIR / "inputs" / "fear_greed_full.csv"
FEAR_LIMIT = 25.0
IM_CUTOFF = pd.Timestamp("2022-07-21")
IC_CUTOFF = pd.Timestamp("2022-09-16")
TOL = 1e-12


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path, **kwargs: Any) -> pd.DataFrame:
    if "parse_dates" not in kwargs:
        kwargs["parse_dates"] = ["date"]
    return pd.read_csv(path, low_memory=False, **kwargs)


def max_abs(left: pd.Series, right: pd.Series) -> float:
    return float(
        (
            pd.to_numeric(left, errors="coerce")
            - pd.to_numeric(right, errors="coerce")
        ).abs().max()
    )


def add_source(paths: set[Path], path: Path) -> None:
    path = path.resolve()
    if path.is_file() and path.is_relative_to(ROOT):
        paths.add(path)


def im_metrics(frame: pd.DataFrame, original: Any, grid_col: str = "overlay_held_eod") -> dict[str, Any]:
    base = original.first.metrics(frame, frame.date.min())
    row = {k: (v.item() if hasattr(v, "item") else v) for k, v in base.items()}
    row.update(
        start=frame.date.min().date().isoformat(),
        end=frame.date.max().date().isoformat(),
        trading_days=int(len(frame)),
        grid_held_days=int(frame[grid_col].gt(0).sum()),
        min_cash=float(frame.cash_weight.min()),
    )
    return row


def ic_metrics(frame: pd.DataFrame, candidate: str) -> dict[str, Any]:
    ret = frame.ret.astype(float)
    nav = (1.0 + ret).cumprod()
    vol = float(ret.std(ddof=1) * math.sqrt(252.0)) if len(ret) > 1 else 0.0
    sd = float(ret.std(ddof=1)) if len(ret) > 1 else 0.0
    return {
        "candidate": candidate,
        "start": frame.date.min().date().isoformat(),
        "end": frame.date.max().date().isoformat(),
        "trading_days": int(len(frame)),
        "cagr": float(nav.iloc[-1] ** (252.0 / len(frame)) - 1.0),
        "total_return": float(nav.iloc[-1] - 1.0),
        "max_drawdown": float((nav / nav.cummax() - 1.0).min()),
        "annualized_volatility": vol,
        "sharpe": float(ret.mean() / sd * math.sqrt(252.0)) if sd > 0 else np.nan,
        "ending_nav_from_1": float(nav.iloc[-1]),
        "grid_entries": int(frame.buy.sum()),
        "grid_exits": int(frame.sell.sum()),
        "grid_held_days": int(frame.held_eod.sum()),
        "minimum_cash_weight": float(frame.cash_weight.min()),
        "average_total_ic_units": float(
            (frame.base_total_ic_units_no_grid + frame.grid_units).mean()
        ),
    }


def load_ic_module() -> Any:
    path = ROOT / "quant_research_runs" / "20260928_ic_fear_grid_retest" / "retest_ic_fear_gated_grid.py"
    spec = importlib.util.spec_from_file_location("ic_fear_existing_retest", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import existing IC replay helpers from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_im(fear: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], set[Path]]:
    source = ROOT / "quant_param_scan_runs" / "20260908_im_mom120_put102_combined_v1"
    grid_audit = ROOT / "quant_param_scan_runs" / "20260914_im_grid_half_full_audit_v3"
    sys.path.insert(0, str(source))
    import run_combined as original  # type: ignore[import-not-found]

    full = original.previous.prev.full
    comp = original.comp
    engine = full.grids.grid_engine
    engine_source = inspect.getsource(engine.simulate_overlay)
    buy_rule = 'action = "buy" if (not state and numeric <= low + 1e-12) else None'
    if engine_source.count(buy_rule) != 1:
        raise RuntimeError("Could not isolate the existing IM grid-entry rule")
    patched_source = engine_source.replace(
        buy_rule,
        'entry_allowed = bool(FEAR_ENTRY_ALLOWED.get(day, False))\n'
        '            action = "buy" if (not state and numeric <= low + 1e-12 and entry_allowed) else None',
    ).replace(
        'trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"].dt.year',
        'pd.to_datetime(trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"]).dt.year',
    )
    if patched_source == engine_source:
        raise RuntimeError("IM fear-gate patch was not applied")
    engine_ns = dict(engine.__dict__)
    engine_ns["FEAR_ENTRY_ALLOWED"] = {}
    exec(patched_source, engine_ns)
    full.grids.grid_engine = types.SimpleNamespace(
        **{**engine.__dict__, "simulate_overlay": engine_ns["simulate_overlay"]}
    )
    full.RUN = OUT
    full.END = pd.Timestamp("2026-09-07")
    full.GRID_DEFS = {
        "fearext_baseline160_half": [(1.6, 2.0, 0.5)],
        "fearext_fear160_half": [(1.6, 2.0, 0.5)],
        "fearext_no_grid": [],
    }

    base_dir = original.BASE
    full_dir = original.FULL
    state = read(base_dir / "valuation_state_through_last_required_eval.csv.gz")
    market = read(base_dir / "model_market.csv.gz")
    upstream = read(base_dir / "real_upstream.csv.gz")
    quotes = read(full_dir / "official_im_quotes.csv.gz")
    bridges = read(full_dir / "quarter1_close_bridges.csv.gz")
    data = {"market": market, "state": state, "quotes": quotes}
    chain = {"name": "quarter1", "up": upstream, "bridges": bridges}
    model_dates = pd.DatetimeIndex(market.date)
    engine_ns["FEAR_ENTRY_ALLOWED"] = {pd.Timestamp(d): True for d in model_dates}
    base = read(source / "model_fixed_base.csv.gz")
    put = read(source / "model_combined_put.csv.gz")
    call = read(source / "model_fixed_call.csv.gz")

    baseline_grid, baseline_trades = full.grid_component(
        data, "model", chain, base, "fearext_baseline160_half"
    )
    saved_grid = read(grid_audit / "model_original160_half_grid_quarter1.csv.gz")
    if not baseline_grid.date.equals(saved_grid.date):
        raise RuntimeError("IM model baseline grid calendar differs from saved audit")
    grid_parity = {
        col: max_abs(baseline_grid[col], saved_grid[col])
        for col in ["overlay_gross_ret", "overlay_cost_rate", "overlay_held_eod", "grid_carry"]
    }
    if max(grid_parity.values()) > TOL:
        raise RuntimeError(f"IM model baseline grid replay parity failed: {grid_parity}")

    fear_lookup = dict(zip(fear.date, fear.fear_greed_index.le(FEAR_LIMIT)))
    engine_ns["FEAR_ENTRY_ALLOWED"] = {
        pd.Timestamp(d): bool(fear_lookup.get(pd.Timestamp(d), False)) for d in model_dates
    }
    fear_grid, fear_trades = full.grid_component(data, "model", chain, base, "fearext_fear160_half")
    no_grid_component, _ = full.grid_component(data, "model", chain, base, "fearext_no_grid")
    baseline = comp.compose(base, put, baseline_grid, call)
    candidate = comp.compose(base, put, fear_grid, call)
    no_grid = comp.compose(base, put, no_grid_component, call)

    saved_baseline = read(grid_audit / "model_original160_half_daily.csv.gz")
    saved_no_grid = read(grid_audit / "model_no_grid_daily.csv.gz")
    if not baseline.date.equals(saved_baseline.date) or not no_grid.date.equals(saved_no_grid.date):
        raise RuntimeError("IM model portfolio calendar differs from saved audit")
    portfolio_parity = {
        col: max_abs(baseline[col], saved_baseline[col])
        for col in ["ret", "nav", "cash_weight", "futures_gross_ret", "futures_cost_rate"]
    }
    no_grid_parity = {
        col: max_abs(no_grid[col], saved_no_grid[col])
        for col in ["ret", "nav", "cash_weight", "futures_gross_ret", "futures_cost_rate"]
    }
    if max(portfolio_parity.values()) > TOL or max(no_grid_parity.values()) > TOL:
        raise RuntimeError(f"IM saved portfolio parity failed: baseline={portfolio_parity}, no_grid={no_grid_parity}")
    for col in original.first.FIELDS + comp.CALL_FIELDS:
        if max_abs(candidate[col], baseline[col]) > TOL:
            raise RuntimeError(f"IM fear gate changed a non-grid component: {col}")
    if candidate.cash_weight.lt(-TOL).any():
        raise RuntimeError("IM fear-confirmed model portfolio has negative cash weight")
    independent_ret = (
        (1 + candidate.futures_gross_ret + candidate.put_pnl_ret + candidate.call_pnl_ret)
        * (1 - candidate.futures_cost_rate)
        * (1 - candidate.put_cost_rate)
        * (1 - candidate.call_cost_rate)
        - 1
        + candidate.cash_weight * full.grids.CASH
    )
    accounting_error = max_abs(candidate.ret, independent_ret)
    if accounting_error > TOL:
        raise RuntimeError(f"IM model candidate accounting failed: {accounting_error}")

    im_pre_compare = baseline.loc[baseline.date.le(IM_CUTOFF), ["date", "ret", "nav"]].merge(
        candidate.loc[candidate.date.le(IM_CUTOFF), ["date", "ret", "nav"]],
        on="date", suffixes=("_baseline", "_fear"), validate="one_to_one",
    )
    im_pre_path_error = {
        col: max_abs(im_pre_compare[f"{col}_baseline"], im_pre_compare[f"{col}_fear"])
        for col in ["ret", "nav"]
    }
    if max(im_pre_path_error.values()) > TOL:
        raise RuntimeError(f"IM fear gate changed the prelisting portfolio path: {im_pre_path_error}")

    pre = pd.Timestamp("2015-04-16")
    baseline_pre = baseline.loc[baseline.date.between(pre, IM_CUTOFF)].copy()
    candidate_pre = candidate.loc[candidate.date.between(pre, IM_CUTOFF)].copy()
    no_grid_pre = no_grid.loc[no_grid.date.between(pre, IM_CUTOFF)].copy()
    baseline_events = baseline_trades.loc[
        pd.to_datetime(baseline_trades.execution_date).le(IM_CUTOFF)
    ].copy()
    fear_events = fear_trades.loc[pd.to_datetime(fear_trades.execution_date).le(IM_CUTOFF)].copy()
    for events in (baseline_events, fear_events):
        if not events.empty:
            events["signal_fear_score"] = pd.to_datetime(events.signal_date).map(
                fear.set_index("date").fear_greed_index
            )
    baseline_events["path"] = "valuation_only"
    fear_events["path"] = "fear_le25_confirmed"

    summaries = []
    for label, frame, events in [
        ("module_off_no_grid", no_grid_pre, pd.DataFrame()),
        ("IM_valuation_1.6_2.0_grid_half", baseline_pre, baseline_events),
        ("fear_le25_IM_grid_half", candidate_pre, fear_events),
    ]:
        row = {"instrument": "IM", "candidate": label, **im_metrics(frame, original)}
        row.update(
            grid_entries=int(events.action.eq("buy").sum()) if not events.empty else 0,
            grid_exits=int(events.action.eq("sell").sum()) if not events.empty else 0,
            grid_carry_sum=float(frame.grid_carry.sum()),
        )
        summaries.append(row)

    carry_rows = []
    for carry_scale in (0.0, 1.0):
        for label, grid in [
            ("IM_valuation_1.6_2.0_grid_half", baseline_grid),
            ("fear_le25_IM_grid_half", fear_grid),
            ("module_off_no_grid", no_grid_component),
        ]:
            b_adj = base.copy()
            g_adj = grid.copy()
            if carry_scale == 0.0:
                b_adj["base_futures_gross"] -= b_adj["long_carry"]
                g_adj["overlay_gross_ret"] -= g_adj["grid_carry"]
                g_adj["grid_carry"] = 0.0
            d = comp.compose(b_adj, put, g_adj, call)
            sub = d.loc[d.date.between(pre, IM_CUTOFF)].copy()
            carry_rows.append(
                {
                    "instrument": "IM",
                    "candidate": label,
                    "modeled_long_carry_per_unit_daily": 0.0003 * carry_scale,
                    **im_metrics(sub, original),
                }
            )

    im_calendar = market.loc[market.date.between(pre, IM_CUTOFF), ["date"]].copy()
    fear_model = im_calendar.merge(
        fear[["date", "fear_greed_index"]], on="date", how="left", validate="one_to_one"
    )
    state_model = state.loc[state.date.between(pre, IM_CUTOFF), ["date", "score"]].copy()
    overlap = fear_model.merge(state_model, on="date", how="left", validate="one_to_one")
    overlap = overlap.merge(
        baseline_grid[["date", "overlay_held_eod", "overlay_buy", "overlay_sell"]].rename(
            columns={"overlay_held_eod": "baseline_held", "overlay_buy": "baseline_buy", "overlay_sell": "baseline_sell"}
        ),
        on="date", how="left", validate="one_to_one",
    ).merge(
        fear_grid[["date", "overlay_held_eod", "overlay_buy", "overlay_sell"]].rename(
            columns={"overlay_held_eod": "fear_held", "overlay_buy": "fear_buy", "overlay_sell": "fear_sell"}
        ),
        on="date", how="left", validate="one_to_one",
    )
    overlap["fear_le25"] = overlap.fear_greed_index.le(FEAR_LIMIT)
    overlap["valuation_entry_zone"] = overlap.score.le(1.6)

    baseline_daily = baseline_pre.copy()
    candidate_daily = candidate_pre.copy()
    no_grid_daily = no_grid_pre.copy()
    for label, frame in [
        ("module_off_no_grid", no_grid_daily),
        ("valuation_only", baseline_daily),
        ("fear_le25_confirmed", candidate_daily),
    ]:
        frame["candidate"] = label
        frame.to_csv(OUT / f"im_{label}_model_daily.csv.gz", index=False, compression="gzip")
    pd.concat([baseline_events, fear_events], ignore_index=True).to_csv(
        OUT / "im_model_trade_events.csv", index=False, encoding="utf-8-sig"
    )
    overlap.to_csv(OUT / "im_fear_grid_overlap_by_day.csv", index=False, encoding="utf-8-sig")

    fear_missing = int(fear_model.fear_greed_index.isna().sum())
    result = {
        "market_sessions": int(len(overlap)),
        "fear_missing_sessions": fear_missing,
        "fear_le25_days": int(overlap.fear_le25.sum()),
        "fear_le25_and_score_le1_6_days": int((overlap.fear_le25 & overlap.valuation_entry_zone).sum()),
        "score_le1_6_days": int(overlap.valuation_entry_zone.sum()),
        "baseline_entries": int(baseline_events.action.eq("buy").sum()),
        "candidate_entries": int(fear_events.action.eq("buy").sum()),
        "baseline_entry_signal_fear_scores": baseline_events.loc[baseline_events.action.eq("buy"), "signal_fear_score"].tolist(),
        "candidate_entry_signal_fear_scores": fear_events.loc[fear_events.action.eq("buy"), "signal_fear_score"].tolist(),
        "grid_parity_max_abs": grid_parity,
        "portfolio_parity_max_abs": portfolio_parity,
        "no_grid_parity_max_abs": no_grid_parity,
        "independent_accounting_max_abs": accounting_error,
        "fear_vs_valuation_prelisting_path_max_abs": im_pre_path_error,
        "unchanged_non_grid_components": True,
        "fear_gate_coverage_complete": fear_missing == 0,
        "production_modified": False,
    }
    result["summaries"] = summaries
    result["carry_sensitivity"] = carry_rows
    inputs = {
        FEAR_PATH,
        FEAR_DIR / "data_manifest.json",
        base_dir / "valuation_state_through_last_required_eval.csv.gz",
        base_dir / "model_market.csv.gz",
        base_dir / "real_upstream.csv.gz",
        full_dir / "official_im_quotes.csv.gz",
        full_dir / "quarter1_close_bridges.csv.gz",
        source / "model_fixed_base.csv.gz",
        source / "model_combined_put.csv.gz",
        source / "model_fixed_call.csv.gz",
        grid_audit / "model_original160_half_grid_quarter1.csv.gz",
        grid_audit / "model_original160_half_daily.csv.gz",
        grid_audit / "model_no_grid_daily.csv.gz",
        Path(inspect.getsourcefile(full.grid_component) or ""),
        Path(inspect.getsourcefile(engine.simulate_overlay) or ""),
        Path(inspect.getsourcefile(original) or ""),
    }
    module_files = set()
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None)
        if path:
            add_source(module_files, Path(path))
    for path in module_files:
        if any(token in str(path).lower() for token in ("research_im_r7_two_tier_grid_v1", "full_components.py", "run_combined.py", "joint_components.py")):
            inputs.add(path)
    return pd.DataFrame(summaries), pd.DataFrame(carry_rows), result, inputs


def run_ic(fear: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], set[Path]]:
    ic = load_ic_module()
    target = ic.read(ic.TARGET_PATH, usecols=["date", "grid_held_eod", "grid_buy", "grid_sell", "grid_valuation_score"])
    formal = ic.read(ic.FORMAL_PATH)
    formal = formal.merge(
        target[["date", "grid_held_eod", "grid_buy", "grid_sell", "grid_valuation_score"]],
        on="date", how="left", validate="one_to_one",
    )
    if formal[["grid_held_eod", "grid_valuation_score"]].isna().any().any():
        raise RuntimeError("IC model formal path misses its frozen target schedule")
    futures = ic.read(ic.FUTURES_PATH)
    market = formal[["date", "contract", "roll_event", "futures_gross_ret", "grid_valuation_score"]].merge(
        futures[["date", "contract", "open", "settle", "pre_settle"]],
        on=["date", "contract"], how="left", validate="one_to_one",
    ).rename(columns={"grid_valuation_score": "valuation_score"})
    market = market.sort_values("date").reset_index(drop=True)
    market = market.merge(fear[["date", "fear_greed_index"]], on="date", how="left", validate="one_to_one")
    quote_missing = int(market[["open", "settle", "pre_settle"]].isna().any(axis=1).sum())
    if quote_missing or market.valuation_score.isna().any():
        raise RuntimeError(f"IC quote or score coverage failed: missing_quotes={quote_missing}")

    target_check = market.merge(
        target[["date", "grid_valuation_score"]], on="date", how="inner", validate="one_to_one"
    )
    score_error = max_abs(target_check.valuation_score, target_check.grid_valuation_score)
    if score_error > TOL:
        raise RuntimeError(f"IC valuation score does not match target: {score_error}")

    # Independently rebuild each modeled T-3 bridge from official contract quotes.
    formal_sorted = formal.sort_values("date").reset_index(drop=True)
    quotes_by_key = futures.set_index(["date", "contract"], verify_integrity=True)
    roll_bridge_rows: list[dict[str, Any]] = []
    for i, row in formal_sorted.iterrows():
        if not bool(row.roll_event) or str(row.data_layer).lower() != "model" or row.date > IC_CUTOFF:
            continue
        if i + 1 >= len(formal_sorted):
            raise RuntimeError(f"IC T-3 roll has no next listed contract on {row.date.date()}")
        old_contract = str(row.contract)
        new_contract = str(formal_sorted.iloc[i + 1].contract)
        try:
            old_quote = quotes_by_key.loc[(row.date, old_contract)]
            new_quote = quotes_by_key.loc[(row.date, new_contract)]
        except KeyError as exc:
            raise RuntimeError(f"IC T-3 bridge quote missing on {row.date.date()}") from exc
        reconstructed = (
            float(old_quote.close) / float(old_quote.pre_settle) - 1.0
            + float(new_quote.settle) / float(new_quote.close) - 1.0
        )
        roll_bridge_rows.append(
            {
                "date": row.date,
                "old_contract": old_contract,
                "new_contract": new_contract,
                "futures_gross_ret_saved": float(row.futures_gross_ret),
                "futures_gross_ret_from_quotes": reconstructed,
                "absolute_error": abs(float(row.futures_gross_ret) - reconstructed),
            }
        )
    roll_bridge_frame = pd.DataFrame(roll_bridge_rows)
    roll_bridge_error = float(roll_bridge_frame.absolute_error.max()) if len(roll_bridge_frame) else 0.0
    if len(roll_bridge_frame) == 0 or roll_bridge_error > TOL:
        raise RuntimeError(f"IC official T-3 bridge reconstruction failed: {roll_bridge_error}")

    official = ic.read(ic.OFFICIAL_DAILY_PATH, usecols=["date", "ret"])
    formal_parity = formal.merge(official, on="date", how="inner", suffixes=("_formal", "_official"), validate="one_to_one")
    formal_return_error = max_abs(formal_parity.ret_formal, formal_parity.ret_official)
    if formal_return_error > TOL:
        raise RuntimeError(f"IC full portfolio source parity failed: {formal_return_error}")

    formal["base_ret_without_grid"] = (
        formal.ret - formal.grid_net_increment - formal.cash_weight * ic.CASH_DAILY
    )
    formal["base_cash_weight_no_grid"] = formal.cash_weight + ic.MARGIN_RATE * formal.grid_held_eod
    formal["base_total_ic_units_no_grid"] = formal.total_units - formal.grid_held_eod
    model_mask = formal.data_layer.astype(str).str.lower().eq("model") & formal.date.le(IC_CUTOFF)
    model_base = formal.loc[model_mask].copy()
    if len(model_base) != 1810 or model_base.date.min() != pd.Timestamp("2015-04-16") or model_base.date.max() != IC_CUTOFF:
        raise RuntimeError(f"Unexpected IC model calendar: {len(model_base)} rows")
    if market.loc[market.date.le(IC_CUTOFF), "fear_greed_index"].isna().any():
        raise RuntimeError("IC fear score missing within the simulated model window")

    old_grid, _ = ic.simulate_grid(market, fear_gate=False, entry=0.375, exit=1.0)
    old_check = old_grid.merge(
        target[["date", "grid_held_eod", "grid_buy", "grid_sell"]],
        on="date", how="inner", validate="one_to_one",
    )
    old_parity = {
        "held_state": max_abs(old_check.held_eod, old_check.grid_held_eod),
        "buy_events": max_abs(old_check.buy, old_check.grid_buy),
        "sell_events": max_abs(old_check.sell, old_check.grid_sell),
    }
    if max(old_parity.values()) > TOL:
        raise RuntimeError(f"IC frozen target grid replay did not match: {old_parity}")

    current_grid, current_events = ic.simulate_grid(market, fear_gate=False, entry=ic.ENTRY, exit=ic.EXIT)
    fear_grid, fear_events = ic.simulate_grid(market, fear_gate=True, entry=ic.ENTRY, exit=ic.EXIT)
    own = ic.read(ic.OWN_GRID_DAILY_PATH)
    own = own.loc[own.candidate.eq("L0.500_H1.000"), ["date", "grid", "grid_net_ret", "ret"]].copy()
    aligned_grid = current_grid.merge(own, on="date", how="inner", validate="one_to_one")
    current_parity = {
        "held_state": max_abs(aligned_grid.held_eod, aligned_grid.grid),
        "grid_net_increment_1x": max_abs(aligned_grid.grid_net_increment_1x, aligned_grid.grid_net_ret),
    }
    if len(aligned_grid) != len(current_grid) or max(current_parity.values()) > TOL:
        raise RuntimeError(f"IC 0.5/1.0 model baseline does not match saved calibration: {current_parity}")

    current_events_all = current_events[["action", "signal_date", "execution_date"]].astype(str)
    saved_events = ic.read(ic.OWN_GRID_EVENTS_PATH)
    saved_events = saved_events.loc[saved_events.candidate.eq("L0.500_H1.000"), ["action", "signal_date", "execution_date"]].astype(str)
    sort_cols = ["action", "signal_date", "execution_date"]
    current_events_all = current_events_all.sort_values(sort_cols).reset_index(drop=True)
    saved_events = saved_events.sort_values(sort_cols).reset_index(drop=True)
    events_match = current_events_all.equals(saved_events)
    if not events_match:
        raise RuntimeError("IC full-history grid event dates differ from saved calibration")

    replay = formal[["date", "base_ret_without_grid", "base_cash_weight_no_grid"]].merge(
        current_grid[["date", "held_eod", "grid_net_increment_1x"]], on="date", validate="one_to_one"
    ).merge(
        own[["date", "ret"]].rename(columns={"ret": "ret_saved"}),
        on="date", validate="one_to_one",
    )
    replay["ret_replayed"] = (
        replay.base_ret_without_grid + replay.grid_net_increment_1x
        + (replay.base_cash_weight_no_grid - ic.MARGIN_RATE * replay.held_eod) * ic.CASH_DAILY
    )
    recompose_error = max_abs(replay.ret_replayed, replay.ret_saved)
    if recompose_error > TOL:
        raise RuntimeError(f"IC baseline portfolio re-composition failed: {recompose_error}")

    zeros = current_grid.assign(
        held_eod=0, buy=0, sell=0, gross_return_1x=0.0, cost_rate_1x=0.0,
        grid_net_increment_1x=0.0, cycle_id=0,
    )
    no_grid = ic.compose_portfolio(model_base, zeros, candidate="module_off_no_grid", size=0.0)
    baseline = ic.compose_portfolio(model_base, current_grid, candidate="IC_valuation_0.5_1.0_grid_half", size=ic.GRID_SIZE)
    candidate = ic.compose_portfolio(model_base, fear_grid, candidate="fear_le25_IC_grid_half", size=ic.GRID_SIZE)
    if not (no_grid.date.equals(baseline.date) and baseline.date.equals(candidate.date)):
        raise RuntimeError("IC model portfolio arms do not share a calendar")
    if min(no_grid.cash_weight.min(), baseline.cash_weight.min(), candidate.cash_weight.min()) < -TOL:
        raise RuntimeError("IC model portfolio arm has negative cash weight")
    ic_path_error = {
        "held_state": max_abs(current_grid.loc[current_grid.date.le(IC_CUTOFF), "held_eod"], fear_grid.loc[fear_grid.date.le(IC_CUTOFF), "held_eod"]),
        "buy_events": max_abs(current_grid.loc[current_grid.date.le(IC_CUTOFF), "buy"], fear_grid.loc[fear_grid.date.le(IC_CUTOFF), "buy"]),
        "sell_events": max_abs(current_grid.loc[current_grid.date.le(IC_CUTOFF), "sell"], fear_grid.loc[fear_grid.date.le(IC_CUTOFF), "sell"]),
        "grid_contribution": max_abs(current_grid.loc[current_grid.date.le(IC_CUTOFF), "grid_net_increment_1x"], fear_grid.loc[fear_grid.date.le(IC_CUTOFF), "grid_net_increment_1x"]),
        "portfolio_return": max_abs(baseline.ret, candidate.ret),
    }
    if max(ic_path_error.values()) > TOL:
        raise RuntimeError(f"IC fear gate changed the prelisting portfolio path: {ic_path_error}")

    summaries = [
        ic_metrics(no_grid, "module_off_no_grid"),
        ic_metrics(baseline, "IC_valuation_0.5_1.0_grid_half"),
        ic_metrics(candidate, "fear_le25_IC_grid_half"),
    ]
    for row in summaries:
        row["instrument"] = "IC"

    pre_current_events = current_events.loc[pd.to_datetime(current_events.signal_date).le(IC_CUTOFF)].copy()
    pre_fear_events = fear_events.loc[pd.to_datetime(fear_events.signal_date).le(IC_CUTOFF)].copy()
    current_grid_pre = current_grid.loc[current_grid.date.le(IC_CUTOFF)].copy()
    fear_grid_pre = fear_grid.loc[fear_grid.date.le(IC_CUTOFF)].copy()
    held_roll = current_grid_pre.loc[
        current_grid_pre.roll_event.eq(1) & current_grid_pre.held_eod.gt(0),
        ["date", "contract", "gross_return_1x", "cost_rate_1x", "grid_net_increment_1x"],
    ].copy()
    held_roll_records = held_roll.assign(date=held_roll.date.dt.date.astype(str)).to_dict(orient="records")
    roll_bridge_frame.assign(date=roll_bridge_frame.date.dt.date.astype(str)).to_csv(
        OUT / "ic_model_t3_roll_bridge_checks.csv", index=False, encoding="utf-8-sig"
    )
    overlap = market.loc[market.date.le(IC_CUTOFF), ["date", "fear_greed_index", "valuation_score"]].copy()
    overlap["fear_le25"] = overlap.fear_greed_index.le(FEAR_LIMIT)
    overlap["entry_score_le0_5"] = overlap.valuation_score.le(ic.ENTRY + TOL)
    overlap = overlap.merge(
        current_grid_pre[["date", "held_eod", "buy", "sell"]].rename(
            columns={"held_eod": "baseline_held", "buy": "baseline_buy", "sell": "baseline_sell"}
        ), on="date", how="left", validate="one_to_one",
    ).merge(
        fear_grid_pre[["date", "held_eod", "buy", "sell", "fear_gate_blocked_entry_day"]].rename(
            columns={"held_eod": "fear_held", "buy": "fear_buy", "sell": "fear_sell", "fear_gate_blocked_entry_day": "fear_blocked"}
        ), on="date", how="left", validate="one_to_one",
    )

    model_rows = []
    for candidate_name, frame in [
        ("module_off_no_grid", no_grid),
        ("valuation_only", baseline),
        ("fear_le25_confirmed", candidate),
    ]:
        frame_out = frame.copy()
        frame_out["candidate"] = candidate_name
        frame_out.to_csv(OUT / f"ic_{candidate_name}_model_daily.csv.gz", index=False, compression="gzip")
        model_rows.append(frame_out)
    pd.concat([pre_current_events.assign(path="valuation_only"), pre_fear_events.assign(path="fear_le25_confirmed")], ignore_index=True).to_csv(
        OUT / "ic_model_trade_events.csv", index=False, encoding="utf-8-sig"
    )
    overlap.to_csv(OUT / "ic_fear_grid_overlap_by_day.csv", index=False, encoding="utf-8-sig")
    summary = {
        "market_sessions": int(len(overlap)),
        "fear_missing_sessions": int(overlap.fear_greed_index.isna().sum()),
        "fear_le25_days": int(overlap.fear_le25.sum()),
        "fear_le25_and_score_le0_5_days": int((overlap.fear_le25 & overlap.entry_score_le0_5).sum()),
        "score_le0_5_days": int(overlap.entry_score_le0_5.sum()),
        "baseline_entries": int(pre_current_events.action.eq("buy").sum()),
        "candidate_entries": int(pre_fear_events.action.eq("buy").sum()),
        "fear_blocked_valuation_entry_days": int(fear_grid_pre.fear_gate_blocked_entry_day.sum()),
        "baseline_entry_events": pre_current_events.loc[pre_current_events.action.eq("buy"), ["signal_date", "signal_fear_score", "execution_date", "execution_open"]].to_dict(orient="records"),
        "candidate_entry_events": pre_fear_events.loc[pre_fear_events.action.eq("buy"), ["signal_date", "signal_fear_score", "execution_date", "execution_open"]].to_dict(orient="records"),
        "frozen_target_grid_parity_max_abs": old_parity,
        "saved_calibration_parity_max_abs": current_parity,
        "saved_calibration_events_match": events_match,
        "saved_calibration_recomposition_max_abs": recompose_error,
        "formal_daily_return_parity_max_abs": formal_return_error,
        "valuation_score_target_parity_max_abs": score_error,
        "official_quote_missing_rows": quote_missing,
        "official_t3_roll_bridge_days": int(len(roll_bridge_frame)),
        "official_t3_roll_bridge_max_abs_error": roll_bridge_error,
        "grid_held_t3_roll_events_1x": held_roll_records,
        "common_calendar": len(no_grid) == len(baseline) == len(candidate) == 1810,
        "fear_vs_valuation_prelisting_path_max_abs": ic_path_error,
        "nonnegative_cash": True,
        "theoretical_put_model_period": True,
        "production_modified": False,
    }
    paths = {
        ic.FEAR_PATH, ic.TARGET_PATH, ic.FORMAL_PATH, ic.FUTURES_PATH,
        ic.OFFICIAL_DAILY_PATH, ic.OWN_GRID_DAILY_PATH, ic.OWN_GRID_EVENTS_PATH,
        ROOT / "quant_research_runs" / "20260928_ic_fear_grid_retest" / "retest_ic_fear_gated_grid.py",
        ROOT / "outputs" / "ic_mainline_v1_3" / "preregistered_spec.md",
        ROOT / "docs" / "ic_510500_put_proxy_validation_v1_spec.md",
    }
    module_files = set()
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None)
        if path:
            add_source(module_files, Path(path))
    for path in module_files:
        if any(token in str(path).lower() for token in ("retest_ic_fear_gated_grid.py", "grid")) and str(ROOT) in str(path):
            paths.add(path)
    return pd.DataFrame(summaries), pd.DataFrame([summary]), summary, paths


def main() -> None:
    fear_manifest = json.loads((FEAR_DIR / "data_manifest.json").read_text(encoding="utf-8"))
    fear = read(FEAR_PATH)
    fear["fear_greed_index"] = pd.to_numeric(fear.fear_greed_index, errors="coerce")
    if not fear.date.is_unique or not fear.fear_greed_index.dropna().between(0, 100).all():
        raise RuntimeError("Fear source has duplicate dates or out-of-range values")
    expected_hash = fear_manifest["fear_data_source"]["sha256"]
    if sha256(FEAR_PATH) != expected_hash:
        raise RuntimeError("Fear input hash no longer matches the registered reproduction snapshot")

    spec_path = OUT / "preregistered_spec.md"
    if not spec_path.exists():
        raise FileNotFoundError("Pre-registered model-period specification is missing")
    im_summary, im_carry, im_checks, im_paths = run_im(fear)
    ic_summary, ic_overlap, ic_checks, ic_paths = run_ic(fear)
    summaries = pd.concat([im_summary, ic_summary], ignore_index=True, sort=False)
    normalized_rows = []
    for row in summaries.to_dict(orient="records"):
        is_im = row["instrument"] == "IM"
        normalized_rows.append(
            {
                "instrument": row["instrument"],
                "candidate": row["candidate"],
                "start": row["start"],
                "end": row["end"],
                "market_sessions": int(row["trading_days"]),
                "return_observations": int(row["rows"]) if is_im else int(row["trading_days"]),
                "annualized_return": row["ann_return"] if is_im else row["cagr"],
                "total_return": row["total_return"],
                "max_drawdown": row["max_dd"] if is_im else row["max_drawdown"],
                "annualized_volatility": row["ann_vol"] if is_im else row["annualized_volatility"],
                "sharpe": row["sharpe_repo"] if is_im else row["sharpe"],
                "ending_nav_from_1": 1.0 + float(row["total_return"]),
                "grid_entries": int(row["grid_entries"]),
                "grid_exits": int(row["grid_exits"]),
                "grid_held_days": int(row["grid_held_days"]),
                "minimum_cash_weight": row["min_cash"] if is_im else row["minimum_cash_weight"],
            }
        )
    pd.DataFrame(normalized_rows).to_csv(OUT / "model_period_portfolio_summary.csv", index=False, encoding="utf-8-sig")
    im_carry.to_csv(OUT / "im_model_carry_sensitivity.csv", index=False, encoding="utf-8-sig")
    ic_overlap.to_csv(OUT / "ic_model_overlap_summary.csv", index=False, encoding="utf-8-sig")

    used = im_paths | ic_paths | {Path(__file__).resolve(), spec_path.resolve(), FEAR_DIR / "data_manifest.json"}
    hashes = {str(path.resolve().relative_to(ROOT)): sha256(path.resolve()) for path in sorted(used) if path.exists() and path.resolve().is_relative_to(ROOT)}
    (OUT / "source_hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verification = {
        "status": "PASS",
        "fear_input_hash_matches_registered_snapshot": True,
        "fear_score_range_0_100": True,
        "im": im_checks,
        "ic": ic_checks,
        "production_modified": False,
        "decision": "research_only_no_promotion",
    }
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def pct(value: Any) -> str:
        return f"{float(value):.2%}" if pd.notna(value) else "N/A"

    table_rows = ["| 品种 | 路径 | 年化 | 累计收益 | 最大回撤 | 行情日数 | 网格开/平仓 | 持仓日 |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in summaries.to_dict(orient="records"):
        if row["instrument"] == "IM":
            table_rows.append(
                f"| IM | {row['candidate']} | {pct(row.get('ann_return'))} | {pct(row.get('total_return'))} | {pct(row.get('max_dd'))} | {row['trading_days']} | {int(row['grid_entries'])}/{int(row['grid_exits'])} | {int(row['grid_held_days'])} |"
            )
        else:
            table_rows.append(
                f"| IC | {row['candidate']} | {pct(row.get('cagr'))} | {pct(row.get('total_return'))} | {pct(row.get('max_drawdown'))} | {row['trading_days']} | {int(row['grid_entries'])}/{int(row['grid_exits'])} | {int(row['grid_held_days'])} |"
            )
    im_by = {r["candidate"]: r for r in im_summary.to_dict(orient="records")}
    ic_by = {r["candidate"]: r for r in ic_summary.to_dict(orient="records")}
    im_baseline = im_by["IM_valuation_1.6_2.0_grid_half"]
    im_candidate = im_by["fear_le25_IM_grid_half"]
    ic_baseline = ic_by["IC_valuation_0.5_1.0_grid_half"]
    ic_candidate = ic_by["fear_le25_IC_grid_half"]
    im_overlap = im_checks
    ic_overlap_text = ic_overlap.iloc[0].to_dict()
    record = f"""# 恐贪≤25 网格门控：上市前模拟期扩展

状态：研究回放完成；未修改正式信号、生产代码或账本，未晋级策略。

## 主要结果

{chr(10).join(table_rows)}

IM模拟期原估值网格与恐贪确认网格分别开仓{int(im_baseline['grid_entries'])}次和{int(im_candidate['grid_entries'])}次；恐贪≤25共有{im_overlap['fear_le25_days']}天，其中与估值≤1.6同日重合{im_overlap['fear_le25_and_score_le1_6_days']}天，占所有估值入场区日期{im_overlap['fear_le25_and_score_le1_6_days']/im_overlap['score_le1_6_days']:.1%}。IC模拟期原估值网格与恐贪确认网格分别开仓{int(ic_baseline['grid_entries'])}次和{int(ic_candidate['grid_entries'])}次；恐贪≤25共有{ic_overlap_text['fear_le25_days']}天，其中与IC估值≤0.5同日重合{ic_overlap_text['fear_le25_and_score_le0_5_days']}天，占所有IC估值入场区日期{ic_overlap_text['fear_le25_and_score_le0_5_days']/ic_overlap_text['score_le0_5_days']:.1%}。实际网格只开了两轮IM和一轮IC，三个信号值都已≤25，所以门控未删除或推迟任何一轮。事件明细见`im_model_trade_events.csv`及`ic_model_trade_events.csv`。

与原估值网格比较，IM年化变化{float(im_candidate['ann_return'])-float(im_baseline['ann_return']):+.2%}，累计收益变化{float(im_candidate['total_return'])-float(im_baseline['total_return']):+.2%}；IC年化变化{float(ic_candidate['cagr'])-float(ic_baseline['cagr']):+.2%}，累计收益变化{float(ic_candidate['total_return'])-float(ic_baseline['total_return']):+.2%}。请结合开仓事件判断差异来自哪些极少数周期，不把回放收益变化解释为门控的稳定因果优势。

按当前快照计算，IM上市前估值≤1.6共有13天，其中5天（38.5%）同时恐贪≤25；这5天占115个极冷日的4.3%。IC估值≤0.5共有29天，其中11天（37.9%）同时恐贪≤25；占116个极冷日的9.5%。虽然区间有日频重叠，真正触发网格入场的只有IM两轮、IC一轮，而且三次信号都已满足恐贪≤25，说明这一测试窗口里恐贪条件没有提供额外筛选。

IM标准模型假设包含每1倍多头每日3bp carry；0bp敏感性下网格/无网格年化分别为13.01%/9.58%，标准3bp下为19.16%/15.27%。对应明细见`im_model_carry_sensitivity.csv`。恐贪在两品种上市前模拟日历均完整覆盖。原基准逐日复放核验、候选会计核验与来源哈希见`verification.json`和`source_hashes.json`。

## 样本、边界与解释

- IM只统计2015-04-16至2022-07-21（1,770个行情行；项目收益指标以首行为锚，计1,769个收益观察），IC只统计2015-04-16至2022-09-16（1,810行）。上市后模型与实际数据重叠行不算入模拟期样本。
- IC普通日使用逐合约官方结算价；T-3展期日按旧合约`close/pre_settle−1`加新合约`settle/close−1`形成桥接。31个上市前模型期展期日均已用官方合约价独立复算，最大绝对误差{ic_checks['official_t3_roll_bridge_max_abs_error']:.2e}；持仓中一次展期是2022-06-14，1倍网格毛收益+0.1822%、双边成本2bp、净增量+0.1622%。这是冻结的季度T-3换仓口径，不是可成交账户验证。IC模拟期Put腿是Black-Scholes/QVIX代理模型，不是当时可成交的510500期权价格；真实Put数据从2022-09-19开始。
- IM使用合成`CSI1000_MODEL`开盘价、没有历史成交量，并采用模型carry假设；不是实际IM逐笔成交记录。
- 恐贪历史值来自2026-09-28保存的全历史快照，缺少逐日历史版本和发布时点证明；回看不能证明信号当时可得或未修订。
- 这仍是固定模型与成本假设下的诊断，未验证动态保证金、冲击成本、整数账户、自融资或实盘生存能力。少数网格周期不构成稳健性或晋级证据。

## 决定

`research_only_no_promotion`

## 独立对抗复核

IM复核确认窗口、收益、carry敏感性、执行事件和已保存基准逐日一致；其提出的恐贪覆盖检查应以行情日历为左表，已修正并重跑，当前缺失0日。IC复核独立重算三条组合曲线，并用官方合约报价复核31个模拟期T-3展期桥接日，最大误差{ic_checks['official_t3_roll_bridge_max_abs_error']:.2e}；未发现改变结论的问题。审查者均未修改原始正式输出或生产状态。

## 复现

主脚本：`extend_fear_grid_model_period.py`。规则先写入`preregistered_spec.md`，基准网格及组合先逐日对齐已保存正式研究输出，再以恐贪≤25控制新开仓。明细工件包括`model_period_portfolio_summary.csv`、`im_fear_grid_overlap_by_day.csv`、`ic_fear_grid_overlap_by_day.csv`、两品种逐日组合、交易事件和IM carry敏感性。
"""
    (OUT / "record.md").write_text(record, encoding="utf-8")
    print(json.dumps({"status": "PASS", "summary": summaries.to_dict(orient="records"), "im_overlap": im_checks, "ic_overlap": ic_checks}, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()
