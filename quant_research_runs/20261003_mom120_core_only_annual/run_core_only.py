"""Research-only core-account replay; original strategy and frozen outputs stay intact."""
from __future__ import annotations

import argparse
import calendar
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODEL = ROOT / "quant_param_scan_runs/20260928_fix9_no_momentum_futures_no_grid_model_extension_v1"
REAL = ROOT / "quant_param_scan_runs/20260928_fix9_no_momentum_futures_no_grid_worst_rolling_v1"
STUDY = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
REFERENCE = ROOT / "quant_param_scan_runs/20260928_momentum_future_breakeven_fix9_asifcurrent_full_v2"
MODEL_SOURCE = ROOT / "quant_param_scan_runs/20260927_fix9_model_fullaccount_corrected_input"
NATIVE = ROOT / "outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925"
FIX11 = ROOT / "quant_research_runs/20261002_fix10_vs_fix11_full_history"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_daily(actual: pd.DataFrame, expected: pd.DataFrame, label: str) -> float:
    if actual.date.astype(str).tolist() != expected.date.astype(str).tolist():
        raise RuntimeError(f"{label} calendar mismatch")
    err = float(np.max(np.abs(actual.return_net.to_numpy(float) - expected.return_net.to_numpy(float))))
    if err > 2e-10:
        raise RuntimeError(f"{label} baseline return mismatch: {err}")
    return err


def validate(daily: pd.DataFrame, events: pd.DataFrame, product: str, layer: str) -> dict:
    trades = events.loc[events.event.eq("trade")].copy()
    symbols = trades.symbol.fillna("").astype(str)
    counts = {s: int(symbols.str.startswith(s).sum()) for s in (
        "future|core|", "future|momentum|", "future|grid|",
        "option|core_put|", "option|momentum_put|", "option|seller|", "option|call|"
    )}
    if counts["future|momentum|"] or counts["future|grid|"] or counts["option|momentum_put|"] or counts["option|call|"]:
        raise RuntimeError(f"{layer} {product}: excluded leg traded: {counts}")
    if not counts["future|core|"] or not counts["option|core_put|"]:
        raise RuntimeError(f"{layer} {product}: core activity missing: {counts}")
    if not daily.date.is_unique or not daily.date.is_monotonic_increasing:
        raise RuntimeError(f"{layer} {product}: invalid calendar")
    if not np.isfinite(daily.return_net).all() or (daily.nav <= 0).any():
        raise RuntimeError(f"{layer} {product}: invalid returns or NAV")
    reconstructed = np.cumprod(1 + daily.return_net.to_numpy(float))
    nav_error = float(np.max(np.abs(reconstructed - daily.nav.to_numpy(float))))
    if nav_error > 2e-9:
        raise RuntimeError(f"{layer} {product}: NAV identity mismatch: {nav_error}")
    return {"trade_counts": counts, "nav_identity_max_error": nav_error,
            "min_free_cash": float((daily.cash_close - daily.margin_reserved).min())}


def run(layer: str) -> None:
    sys.path[:0] = [str(MODEL_SOURCE), str(MODEL), str(REFERENCE), str(NATIVE), str(STUDY), str(ROOT)]
    import run_study as study  # noqa: E402
    runner = load("core_only_reference_runner", REFERENCE / "run_candidate.py")
    engine_path = (MODEL if layer == "model" else REAL) / "native_account_no_momentum_futures_grid_off.py"
    engine = load(f"core_only_{layer}_engine", engine_path)
    if layer == "model":
        from model_source_v3 import ListedCorrectedModelSources
        from native_fix4_historical_sources_v1 import formal
        source = ListedCorrectedModelSources()
        model_dates = pd.DatetimeIndex(source.ic_market.index)
        original_third_friday = formal._third_friday

        def model_third_friday(year: int, month: int) -> date:
            weeks = calendar.monthcalendar(year, month)
            fridays = [week[calendar.FRIDAY] for week in weeks if week[calendar.FRIDAY]]
            civil = pd.Timestamp(year, month, fridays[2])
            if model_dates.min() <= civil <= model_dates.max():
                index = model_dates.searchsorted(civil)
                if index >= len(model_dates):
                    raise RuntimeError("model expiry outside synthetic calendar")
                return model_dates[index].date()
            return original_third_friday(year, month)

        formal._third_friday = model_third_friday
        inputs = HERE / "latest_model_inputs"
        lifecycle = pd.read_csv(inputs / "native_fix4_seller_lifecycle_v4.csv.gz")
        candidates = pd.read_csv(inputs / "native_fix4_seller_candidates_v1.csv.gz")
    else:
        source = study.base.DatedSources()
        candidates = pd.read_csv(study.NATIVE / "native_fix4_seller_candidates_v1.csv.gz")
        ic_lifecycle, _ = runner.build_fix9_ic_lifecycle(
            source, candidates,
            pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                        parse_dates=["date"]).set_index("date").gov10y_yield,
            pd.read_csv(study.base.OHLCV["IM"], parse_dates=["date"]).set_index("date").close,
        )
        lifecycles = {"IC": ic_lifecycle,
                      "IM": pd.read_csv(STUDY / "input_variants/im/formal/native_fix4_seller_lifecycle_v4.csv.gz")}
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                      parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(study.base.OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    dailies, event_parts, audits = [], [], {}
    for product in ("IC", "IM"):
        folder = (inputs if layer == "model" else
                  FIX11 / "account_inputs/FIX11_full" if product == "IC" else
                  STUDY / "input_variants/im/formal")
        runner.configure_engine(engine, folder)
        engine.HERE = folder
        life = lifecycle if layer == "model" else lifecycles[product]
        # The copied engine already sets momentum futures to zero; turn off its
        # independently targeted momentum Put through the existing sleeve switch.
        engine.DISABLED_MODULES = frozenset({"call", "grid", "momentum"})
        daily, events, _ = engine.replay(product, "repeat_roll", source, life, candidates, gov, spot)
        check = validate(daily, events, product, layer)
        risk = pd.read_csv(folder / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz")
        signal = risk[["signal_date", "execution_date", "momentum_120"]].dropna(subset=["execution_date"]).copy()
        if signal.execution_date.duplicated().any():
            raise RuntimeError("duplicate risk execution dates")
        joined = daily.merge(signal, left_on="date", right_on="execution_date", how="left", validate="one_to_one")
        joined["mom120_negative_execution"] = joined.momentum_120.lt(0)
        joined.insert(0, "data_layer", layer)
        dailies.append(joined)
        events.insert(0, "product", product)
        events.insert(0, "data_layer", layer)
        event_parts.append(events)
        audits[product] = {"parent_target_input": str(folder.relative_to(ROOT)), **check,
                           "start": str(daily.date.iloc[0]), "end": str(daily.date.iloc[-1]),
                           "rows": len(daily)}
        print(json.dumps({"layer": layer, "product": product, **audits[product]}, ensure_ascii=False), flush=True)
    pd.concat(dailies, ignore_index=True).to_csv(HERE / f"{layer}_core_only_daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(HERE / f"{layer}_core_only_events.csv.gz", index=False, compression="gzip")
    (HERE / f"{layer}_core_only_audit.json").write_text(json.dumps({
        "audits": audits, "engine_sha256": digest(engine_path),
        "source_code_sha256": digest(ROOT / "poe_ic_im_mainline_v1_4_bot.py"),
        "classification": "research_only_core_no_momentum_no_grid_no_new_call"
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", choices=("real", "model"), required=True)
    args = parser.parse_args()
    run(args.layer)
