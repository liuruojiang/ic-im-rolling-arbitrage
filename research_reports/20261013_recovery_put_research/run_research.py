"""Replay frozen latest inputs in an isolated research account; no live calls."""
from __future__ import annotations

import argparse
import calendar
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
STUDY = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
NATIVE = ROOT / "outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925"
REFERENCE = ROOT / "quant_param_scan_runs/20260928_momentum_future_breakeven_fix9_asifcurrent_full_v2"
FIX11 = ROOT / "quant_research_runs/20261002_fix10_vs_fix11_full_history"
MODEL_RUN = ROOT / "quant_research_runs/20261003_mom120_core_only_annual"
MODEL_SOURCE = ROOT / "quant_param_scan_runs/20260927_fix9_model_fullaccount_corrected_input"
MODEL_ROOT = ROOT / "quant_param_scan_runs/20260927_fix9_model_fullaccount"
TEMP = Path("D:/Codex/tmp/icim_recovery_research_20261013")
TEMP.mkdir(parents=True, exist_ok=True)
os.environ.update(ICIM_STATE_DIR=str(TEMP / "import_state"), ICIM_REQUIRE_MIGRATION="0",
                  PYTHONDONTWRITEBYTECODE="1", TEMP=str(TEMP), TMP=str(TEMP))
sys.path[:0] = [str(OUT), str(STUDY), str(NATIVE), str(REFERENCE), str(ROOT)]

import numpy as np
import pandas as pd


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def compare(actual, expected, *, label):
    assert len(actual) == len(expected), f"{label} row mismatch"
    ignored = {"arm", "product", "candidate", "data_layer"}
    columns = [col for col in actual if col in expected and col not in ignored]
    assert "date" in columns
    errors = {}
    for col in columns:
        a, b = actual[col].reset_index(drop=True), expected[col].reset_index(drop=True)
        assert a.isna().equals(b.isna()), f"{label}/{col} missingness mismatch"
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            x, y = a.to_numpy(float), b.to_numpy(float)
            finite = np.isfinite(x) & np.isfinite(y)
            error = float(np.max(np.abs(x[finite] - y[finite]))) if finite.any() else 0.0
            tolerance = 2e-10 if col in {"return_net", "nav"} else 1e-6
            assert error <= tolerance, f"{label}/{col}: {error}"
            errors[col] = error
        else:
            assert a.fillna("<NA>").astype(str).equals(b.fillna("<NA>").astype(str)), f"{label}/{col} categorical mismatch"
    return {"status": "PASS", "rows": len(actual), "columns": columns, "max_abs_errors": errors}


def prepare(panel):
    parent = load("recovery_latest_saved_account", FIX11 / "run_account.py")
    runner = parent.listed.fix9_runner
    paths = []
    if panel == "listed":
        source, candidates, gov, spot, lifecycles, folders, cand_path, im_life, _ = parent.listed.load_account_inputs()
        fear_path = parent.listed.FEAR_FILE
        fear = pd.read_csv(fear_path, dtype={"date": str}).set_index("date").fear_greed_index
        risks = {"IC": pd.read_csv(FIX11 / "account_inputs/FIX11_full/native_fix4_ic_risk_signals_v1.csv.gz")}
        im_risk_path = folders["IM"] / "native_fix4_im_risk_signals_v1.csv.gz"
        risks["IM"], _, _ = parent.paired.build_targets(pd.read_csv(im_risk_path), fear, "IM", 50.0)
        target_paths = {"IC": FIX11 / "account_inputs/FIX11_full/native_fix4_ic_noseller_targets_v2.csv.gz",
                        "IM": folders["IM"] / "native_fix4_im_noseller_targets_v3.csv.gz"}
        saved = pd.read_csv(FIX11 / "account_daily_paths.csv.gz")
        expected = {"IC": saved.loc[saved.arm.eq("FIX11_full")].reset_index(drop=True)}
        im_saved_path = parent.BASELINE
        im_saved = pd.read_csv(im_saved_path)
        expected["IM"] = im_saved.loc[im_saved.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")].reset_index(drop=True)
        saved_events = pd.read_csv(FIX11 / "account_events.csv.gz", low_memory=False)
        expected_events = {"IC": saved_events.loc[saved_events.arm.eq("FIX11_full")].reset_index(drop=True)}
        im_events_path = parent.PAIRED / "outputs/full_account_events.csv.gz"
        im_events = pd.read_csv(im_events_path, low_memory=False)
        expected_events["IM"] = im_events.loc[im_events.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")].reset_index(drop=True)
        paths.extend([cand_path, im_life, fear_path, im_risk_path, im_saved_path, im_events_path,
                      FIX11 / "account_daily_paths.csv.gz", FIX11 / "account_events.csv.gz",
                      FIX11 / "account_inputs/FIX11_full/native_fix4_ic_risk_signals_v1.csv.gz"])
    else:
        sys.path[:0] = [str(MODEL_SOURCE), str(MODEL_ROOT)]
        from model_source_v3 import ListedCorrectedModelSources
        source = ListedCorrectedModelSources()
        model_dates = pd.DatetimeIndex(source.ic_market.index)
        formal = parent.formal
        original = formal._third_friday
        def model_expiry(year, month):
            fridays = [week[calendar.FRIDAY] for week in calendar.monthcalendar(year, month) if week[calendar.FRIDAY]]
            civil = pd.Timestamp(year, month, fridays[2])
            if model_dates.min() <= civil <= model_dates.max():
                index = model_dates.searchsorted(civil)
                if index >= len(model_dates):
                    raise RuntimeError("Model expiry beyond known sessions")
                return model_dates[index].date()
            return original(year, month)
        formal._third_friday = model_expiry
        folder = MODEL_RUN / "latest_model_inputs"
        manifest = json.loads((MODEL_RUN / "model_latest_full_audit.json").read_text(encoding="utf-8"))
        for name, expected_sha in manifest["input_sha256"].items():
            assert sha(folder / name) == expected_sha, f"Frozen model input changed: {name}"
        risks = {product: pd.read_csv(folder / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz") for product in ("IC", "IM")}
        target_paths = {product: folder / f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz" for product in ("IC", "IM")}
        lifecycle = pd.read_csv(folder / "native_fix4_seller_lifecycle_v4.csv.gz")
        lifecycles = dict(IC=lifecycle, IM=lifecycle)
        candidates = pd.read_csv(folder / "native_fix4_seller_candidates_v1.csv.gz")
        gov = pd.read_csv(parent.listed.GOV_FILE, parse_dates=["date"]).set_index("date").gov10y_yield
        spot = pd.read_csv(parent.producer.OHLCV["IM"], parse_dates=["date"]).set_index("date").close
        saved = pd.read_csv(MODEL_RUN / "model_latest_full_daily.csv.gz")
        saved_events = pd.read_csv(MODEL_RUN / "model_latest_full_events.csv.gz", low_memory=False)
        expected = {product: saved.loc[saved["product"].eq(product)].reset_index(drop=True) for product in ("IC", "IM")}
        expected_events = {product: saved_events.loc[saved_events["product"].eq(product)].reset_index(drop=True) for product in ("IC", "IM")}
        paths.extend([*folder.glob("*.csv.gz"), MODEL_RUN / "model_latest_full_audit.json",
                      MODEL_RUN / "model_latest_full_daily.csv.gz", MODEL_RUN / "model_latest_full_events.csv.gz"])
    input_folders = {}
    for product in ("IC", "IM"):
        folder = OUT / "inputs" / panel / product
        folder.mkdir(parents=True, exist_ok=True)
        risks[product].to_csv(folder / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz", index=False, compression="gzip")
        shutil.copyfile(target_paths[product], folder / target_paths[product].name)
        input_folders[product] = folder
        paths.append(target_paths[product])
    manifest = {"panel": panel, "source_class": f"{type(source).__module__}.{type(source).__name__}",
                "inputs": [{"path": str(path), "sha256": sha(path)} for path in sorted(set(paths))],
                "known_scope": "Frozen latest-rule as-if-current history; listed theoretical fills or prelisting model, not live acceptance"}
    dump(OUT / f"input_manifest_{panel}.json", manifest)
    return source, candidates, gov, spot, lifecycles, input_folders, expected, expected_events, runner


def metrics(daily):
    r = daily.return_net.to_numpy(float)
    nav = np.r_[1.0, np.cumprod(1.0 + r)]
    assert np.isfinite(nav).all() and (nav > 0).all()
    assert np.max(np.abs(nav[1:] - daily.nav.to_numpy(float))) < 2e-9
    tail_count = max(1, int(np.ceil(len(r) * .05)))
    return {"start": str(daily.date.iloc[0]), "end": str(daily.date.iloc[-1]), "rows": len(daily),
            "cagr_252": float(nav[-1] ** (252 / len(r)) - 1), "final_nav": float(nav[-1]),
            "max_drawdown": float((nav / np.maximum.accumulate(nav) - 1).min()),
            "worst_day": float(r.min()), "worst_5pct_mean": float(np.sort(r)[:tail_count].mean()),
            "fees_cumulative": float(daily.fees_cumulative.iloc[-1]),
            "min_free_cash": float((daily.cash_close - daily.margin_reserved).min())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", choices=["listed", "model"], required=True)
    parser.add_argument("--arms", nargs="+", choices=["A0", "A", "B", "C"], default=["A0", "A", "B", "C"])
    args = parser.parse_args()
    start = time.perf_counter()
    source, candidates, gov, spot, lifecycles, folders, expected, expected_events, runner = prepare(args.panel)
    engine = load("recovery_account_engine", OUT / "recovery_account.py")
    summaries = []
    for product in ("IC", "IM"):
        for arm in args.arms:
            destination = OUT / "runs" / args.panel / product / arm
            destination.mkdir(parents=True, exist_ok=True)
            runner.configure_engine(engine, folders[product])
            engine.FIXED_SELLER_CORE_UNITS = None
            engine.RECOVERY_RESEARCH_MODE = arm
            engine.RECOVERY_IC_MIN_DELTA = .125
            engine.RECOVERY_IM_MIN_QTY = 1.5
            fallback_observations = []
            original_quote = engine.Market.quote
            def observed_quote(market, day, contract, kind, *, phase="close", prior_day=None):
                # Observe the native fallback without changing its price or
                # control flow. This includes opening MTM calls, not just fills.
                if phase == "open" and prior_day is not None:
                    chain = market.future_chain(day) if kind == "future" else market.option_chain(day)
                    name = "contract" if kind == "future" or market.product == "IC" else "instrument"
                    price_column = "open" if kind == "future" or market.product == "IC" else "openprice"
                    found = chain.loc[chain[name].eq(contract), price_column]
                    if len(found) != 1 or not np.isfinite(float(found.iloc[0])) or float(found.iloc[0]) <= 0:
                        fallback_observations.append({"date": str(day), "contract": contract, "kind": kind,
                                                      "fallback_day": str(prior_day), "phase": phase})
                return original_quote(market, day, contract, kind, phase=phase, prior_day=prior_day)
            engine.Market.quote = observed_quote
            try:
                daily, events, audit = engine.replay(product, "repeat_roll", source, lifecycles[product], candidates, gov, spot)
            finally:
                engine.Market.quote = original_quote
            audit["native_prior_close_open_fallback_call_count"] = len(fallback_observations)
            audit["native_prior_close_open_fallback_calls"] = fallback_observations
            audit["reported_metrics_basis"] = "metrics.json uses all N daily returns and 252/N annualization; native audit cagr retains its legacy N-1 convention"
            if arm == "A0":
                audit["saved_baseline_parity"] = compare(daily, expected[product], label=f"{args.panel}/{product}/daily")
                audit["saved_event_parity"] = compare(events, expected_events[product], label=f"{args.panel}/{product}/events")
            else:
                prior_audit = OUT / "runs" / args.panel / product / "A0/audit.json"
                assert prior_audit.exists(), "Run and reconcile A0 before interpreting candidates"
                assert json.loads(prior_audit.read_text(encoding="utf-8"))["saved_baseline_parity"]["status"] == "PASS"
            daily.to_csv(destination / "daily.csv.gz", index=False, compression="gzip")
            events.to_csv(destination / "events.csv.gz", index=False, compression="gzip")
            audit.update(panel=args.panel, research_arm=arm, engine_sha256=sha(OUT / "recovery_account.py"),
                         controller_sha256=sha(OUT / "recovery_cycle_controller.py"),
                         preregistered_spec_sha256=sha(OUT / "preregistered_spec.txt"))
            dump(destination / "audit.json", audit)
            row = {"panel": args.panel, "product": product, "arm": arm, **metrics(daily)}
            summaries.append(row)
            dump(destination / "metrics.json", row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    dump(OUT / f"last_run_{args.panel}.json", {"arms": args.arms, "seconds": time.perf_counter() - start,
                                            "command": [sys.executable, "-B", "-X", "utf8", *sys.argv]})


if __name__ == "__main__":
    main()
