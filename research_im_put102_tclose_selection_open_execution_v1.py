"""Research-only IM Put timing rerun: T-close strike selection, T+1 close/open execution."""
from __future__ import annotations

import inspect
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

import research_im_put102_mom_floor_quantity_v1 as q

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_im_put102_tclose_selection_t1open_no_grid_call_v5"
FLOORS = (1, 2, 3)
TARGET = 1.02


def build_tclose_active(active: pd.DataFrame, templates: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[str]]:
    original = active.copy().set_index("date")
    out = original.copy()
    refs: dict[pd.Timestamp, pd.Timestamp] = {}
    for template in templates.values():
        for row in template.itertuples(index=False):
            refs[pd.Timestamp(row.execution_date)] = pd.Timestamp(row.eval_date)
    exceptions: list[str] = []
    for execution, evaluation in refs.items():
        if evaluation in original.index:
            out.loc[execution, "close"] = original.loc[evaluation, "close"]
        else:
            exceptions.append(str(execution.date()))
    return out.reset_index(), exceptions


def priced_engine(price_column: str, method_name: str):
    """Adapt the frozen real engine only in-memory; no production engine is changed."""
    source = inspect.getsource(q.run.engine.run_real_monthly_close)
    source = source.replace("def run_real_monthly_close(", f"def {method_name}(")
    source = source.replace("legacy.select_close_contract", "legacy.select_open_contract")
    source = source.replace("legacy.executable_close", "legacy.executable_open")
    source = source.replace("float(selected['close'])", f"float(selected['{price_column}'])")
    source = source.replace("float(oldq['close'])", f"float(oldq['{price_column}'])")

    legacy = SimpleNamespace(**vars(q.run.engine.legacy))

    def executable_open(row: pd.Series) -> bool:
        return bool(pd.notna(row[price_column]) and np.isfinite(float(row[price_column])) and float(row[price_column]) > 0)

    def select_open_contract(options: pd.DataFrame, im_close: pd.Series, day: pd.Timestamp, month: pd.Timestamp, moneyness: float) -> pd.Series:
        chain = options.loc[options.date.eq(day) & options.contract_month.eq(month)].copy()
        if chain.empty:
            raise RuntimeError(f"Missing listed chain {day} {month}")
        chain["entry_moneyness"] = chain.strike / float(im_close.loc[day])
        chain["target_error"] = (chain.entry_moneyness - moneyness).abs().round(12)
        selected = chain.sort_values(["target_error", "strike", "contract"]).iloc[0].copy()
        if not executable_open(selected):
            raise RuntimeError(f"No executable open/close fallback {day} {selected.contract}")
        return selected

    legacy.executable_open = executable_open
    legacy.select_open_contract = select_open_contract
    namespace = {"pd": pd, "np": np, "math": math, "legacy": legacy, "real_quantity": q.run.engine.real_quantity, "validate": q.run.engine.validate}
    exec(source, namespace)
    return namespace[method_name]


def timing_options(raw: pd.DataFrame) -> pd.DataFrame:
    out = q.run.engine.with_execution_prices(raw)
    use_open = out.open.notna() & np.isfinite(out.open) & out.open.gt(0)
    out["execution_open_price"] = np.where(use_open, out.open, out.close)
    out["execution_price_source"] = np.where(use_open, "official_open", out.execution_price_source + "_open_missing_close_fallback")
    out = out.sort_values(["contract", "date"]).copy()
    prior_close = out.groupby("contract", sort=False).close.shift()
    valid_prior = prior_close.notna() & np.isfinite(prior_close) & prior_close.gt(0)
    out["tclose_proxy_price"] = np.where(valid_prior, prior_close, out.execution_open_price)
    out["tclose_proxy_price_source"] = np.where(valid_prior, "prior_session_official_close", out.execution_price_source + "_prior_close_missing_open_or_close_fallback")
    return out


def run_variant(mode: str, runners: dict[str, object], upstream: pd.DataFrame, options: pd.DataFrame, active: pd.DataFrame, schedule: pd.DataFrame, label: str, reset_dates: set[pd.Timestamp]):
    if mode == "tclose_select_t1_close":
        return q.run.engine.run_real_monthly_close(upstream, options, active, schedule, "3m", TARGET, label, reset_dates=reset_dates)
    return runners[mode](upstream, options, active, schedule, "3m", TARGET, label, reset_dates=reset_dates)


def main() -> None:
    if RUN.exists():
        raise RuntimeError(f"Refusing to overwrite {RUN}")
    RUN.mkdir(parents=True)
    base_root = q.run.BASE
    state = pd.read_csv(base_root / "valuation_state_through_last_required_eval.csv.gz", parse_dates=["date"]).set_index("date")
    upstream = pd.read_csv(base_root / "real_upstream.csv.gz", parse_dates=["date"])
    active = pd.read_csv(base_root / "real_active.csv.gz", parse_dates=["date"])
    raw = pd.read_csv(base_root / "real_options.csv.gz", parse_dates=["date", "contract_month", "rule_expiry", "actual_expiry"])
    options = timing_options(raw)
    base = pd.read_csv(q.run.JOINT / "real_baseline_base.csv.gz", parse_dates=["date"])
    grid = pd.read_csv(q.run.JOINT / "real_baseline_grid.csv.gz", parse_dates=["date"])
    for col in grid.columns:
        if col != "date":
            grid[col] = 0.0
    templates = {s: pd.read_csv(q.run.FULL / f"real_dual_{s}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"]) for s in ("core", "mom")}
    selected_active, initial_exceptions = build_tclose_active(active, templates)
    reset_dates = q.run.engine.monthly_dates(base.date)
    runners = {
        "tclose_select_t1_open": priced_engine("execution_open_price", "run_real_tclose_open"),
        "tclose_select_tclose_price_proxy": priced_engine("tclose_proxy_price", "run_real_tclose_proxy"),
    }
    all_daily: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    metrics: list[dict] = []
    audit_rows: list[dict] = []
    for mode in ("tclose_select_t1_close", "tclose_select_t1_open", "tclose_select_tclose_price_proxy"):
        for floor in FLOORS:
            legs: list[pd.DataFrame] = []
            trades: list[pd.DataFrame] = []
            for sleeve in ("core", "mom"):
                schedule = q.schedule(templates[sleeve], state, base.momentum_weight, "real", sleeve, floor)
                put, trade, _ = run_variant(mode, runners, upstream, options, selected_active, schedule, f"{mode}_floor{floor}_{sleeve}", reset_dates)
                if put.attrs.get("pending_end"):
                    raise RuntimeError(f"pending end state: {mode} floor{floor} {sleeve}")
                put[q.run.first.FIELDS] *= 0.25 / 4
                legs.append(put)
                trades.append(trade.assign(mode=mode, mom120_floor_qty=floor, sleeve=sleeve))
            combined = sum(x[q.run.first.FIELDS] for x in legs)
            combined["date"] = base.date
            daily = q.run.comp.compose(base, combined, grid, None)
            daily["mode"] = mode
            daily["mom120_floor_qty"] = floor
            all_daily.append(daily)
            joined = pd.concat(trades, ignore_index=True)
            all_trades.append(joined)
            for segment, years in q.run.first.WINDOWS:
                start = daily.date.min() if years is None else q.run.END - pd.DateOffset(years=years)
                if start >= daily.date.min():
                    values = q.run.first.metrics(daily, start)
                    values.update(mode=mode, mom120_floor_qty=floor, segment=segment)
                    metrics.append(values)
            entries = joined.loc[joined.action.isin(["close_buy", "close_roll_monthly", "close_expiry_replace"])].copy()
            for row in entries.itertuples(index=False):
                if not row.new_contract:
                    continue
                quote = options.loc[(options.date.eq(row.actual_execution_date)) & (options.contract.eq(row.new_contract))].iloc[0]
                price_source = quote.execution_price_source if mode == "tclose_select_t1_open" else (quote.tclose_proxy_price_source if mode == "tclose_select_tclose_price_proxy" else quote.execution_price_source)
                audit_rows.append(dict(mode=mode, floor=floor, sleeve=row.sleeve, date=row.actual_execution_date, signal_date=row.signal_eval_date, contract=row.new_contract, strike=row.new_strike, reference_im=float(selected_active.set_index("date").loc[row.actual_execution_date, "close"]), execution_price=row.new_trade_price, execution_price_source=price_source, action=row.action))
    daily_out = pd.concat(all_daily, ignore_index=True)
    trades_out = pd.concat(all_trades, ignore_index=True)
    audits = pd.DataFrame(audit_rows)
    checks = {
        "all_grid_zero": bool(daily_out[[c for c in daily_out if c.startswith("overlay_") or c == "grid_carry"]].abs().to_numpy().max() == 0),
        "all_call_zero": bool(daily_out[["call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction", "call_coverage"]].abs().to_numpy().max() == 0),
        "signal_precedes_execution": bool(trades_out.signal_eval_date.lt(trades_out.actual_execution_date).all()),
        "tclose_reference_for_comparable_entries": bool(np.allclose(audits.loc[audits.signal_date.ge(active.date.min()), "reference_im"], audits.loc[audits.signal_date.ge(active.date.min()), "signal_date"].map(active.set_index("date").close))),
        "initial_tclose_reference_exceptions": initial_exceptions,
        "open_fallback_entries": int(audits.loc[audits["mode"].eq("tclose_select_t1_open"), "execution_price_source"].str.contains("fallback").sum()),
        "tclose_proxy_fallback_entries": int(audits.loc[audits["mode"].eq("tclose_select_tclose_price_proxy"), "execution_price_source"].str.contains("fallback").sum()),
    }
    daily_out.to_csv(RUN / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades_out.to_csv(RUN / "trades.csv", index=False)
    audits.to_csv(RUN / "execution_audit.csv", index=False)
    pd.DataFrame(metrics).to_csv(RUN / "metrics.csv", index=False)
    (RUN / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RUN / "record.md").write_text("# IM 102% Put：T收盘选约、T+1开盘执行\n\n研究运行中生成的指标见 `metrics.csv`；逐笔选约与成交来源见 `execution_audit.csv`。仅研究，不修改正式信号。\n", encoding="utf-8")
    print(pd.DataFrame(metrics).query("segment == 'full'").to_string(index=False))
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
