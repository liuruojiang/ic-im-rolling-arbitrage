"""Replay latest full account with only the grid leg removed."""
from __future__ import annotations

import argparse
import calendar
import json
import shutil
import sys
from datetime import date

import numpy as np
import pandas as pd

from run_core_only import HERE, ROOT, MODEL_SOURCE, NATIVE, STUDY, REFERENCE, FIX11, digest, load, verify_daily

PAIRED = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2"
MODEL_FINAL = ROOT / "quant_param_scan_runs/20260927_fix9_model_fullaccount_final_redteam"


def run(layer: str) -> None:
    sys.path[:0] = [str(MODEL_SOURCE), str(REFERENCE), str(NATIVE), str(STUDY), str(ROOT)]
    import run_study as study  # noqa: E402
    from native_fix4_historical_sources_v1 import formal

    runner = load("no_grid_reference_runner", REFERENCE / "run_candidate.py")
    engine = load(f"no_grid_{layer}_engine", STUDY / "native_account.py")
    if layer == "model":
        from model_source_v3 import ListedCorrectedModelSources
        source = ListedCorrectedModelSources()
        model_dates = pd.DatetimeIndex(source.ic_market.index)
        original_third_friday = formal._third_friday

        def model_third_friday(year: int, month: int) -> date:
            weeks = calendar.monthcalendar(year, month)
            fridays = [week[calendar.FRIDAY] for week in weeks if week[calendar.FRIDAY]]
            civil = pd.Timestamp(year, month, fridays[2])
            if model_dates.min() <= civil <= model_dates.max():
                position = model_dates.searchsorted(civil)
                if position >= len(model_dates):
                    raise RuntimeError("model expiry outside synthetic calendar")
                return model_dates[position].date()
            return original_third_friday(year, month)

        formal._third_friday = model_third_friday
        model_folder = HERE / "latest_model_inputs"
        candidates = pd.read_csv(model_folder / "native_fix4_seller_candidates_v1.csv.gz")
        lifecycles = {p: pd.read_csv(model_folder / "native_fix4_seller_lifecycle_v4.csv.gz") for p in ("IC", "IM")}
        expected_all = pd.read_csv(HERE / "model_latest_full_daily.csv.gz")
    else:
        source = study.base.DatedSources()
        candidates = pd.read_csv(study.NATIVE / "native_fix4_seller_candidates_v1.csv.gz")
        expected_ic = pd.read_csv(FIX11 / "account_daily_paths.csv.gz")
        expected_ic = expected_ic.loc[expected_ic.arm.eq("FIX11_full") & expected_ic["product"].eq("IC")]
        expected_im = pd.read_csv(PAIRED / "outputs/fix9_full_account_daily_nav.csv.gz")
        expected_im = expected_im.loc[expected_im.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")]
        expected_all = pd.concat((expected_ic, expected_im), ignore_index=True)
        im_folder = HERE / "real_im_fear50_inputs"
        im_folder.mkdir(exist_ok=True)
        im_risk = pd.read_csv(STUDY / "input_variants/im/formal/native_fix4_im_risk_signals_v1.csv.gz")
        paired = load("no_grid_paired_rule", PAIRED / "run_scan.py")
        fear = pd.read_csv(ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv",
                           dtype={"date": str}).set_index("date").fear_greed_index
        im_risk, _, _ = paired.build_targets(im_risk, fear, "IM", 50.0)
        im_risk.to_csv(im_folder / "native_fix4_im_risk_signals_v1.csv.gz", index=False, compression="gzip")
        shutil.copy2(STUDY / "input_variants/im/formal/native_fix4_im_noseller_targets_v3.csv.gz",
                     im_folder / "native_fix4_im_noseller_targets_v3.csv.gz")
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                      parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(study.base.OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    if layer == "real":
        ic_lifecycle, _ = runner.build_fix9_ic_lifecycle(source, candidates, gov, spot)
        lifecycles = {"IC": ic_lifecycle,
                      "IM": pd.read_csv(STUDY / "input_variants/im/formal/native_fix4_seller_lifecycle_v4.csv.gz")}
    parts, event_parts, audits = [], [], {}
    for product in ("IC", "IM"):
        folder = (model_folder if layer == "model" else
                  FIX11 / "account_inputs/FIX11_full" if product == "IC" else im_folder)
        runner.configure_engine(engine, folder)
        engine.HERE = folder
        engine.DISABLED_MODULES = frozenset({"call"})
        full, _, _ = engine.replay(product, "repeat_roll", source, lifecycles[product], candidates, gov, spot)
        expected = expected_all.loc[expected_all["product"].eq(product)].reset_index(drop=True)
        full_error = verify_daily(full, expected, f"{layer} {product} full baseline")
        engine.DISABLED_MODULES = frozenset({"call", "grid"})
        daily, events, _ = engine.replay(product, "repeat_roll", source, lifecycles[product], candidates, gov, spot)
        trades = events.loc[events.event.eq("trade")].symbol.fillna("").astype(str)
        counts = {s: int(trades.str.startswith(s).sum()) for s in (
            "future|core|", "future|momentum|", "future|grid|",
            "option|core_put|", "option|momentum_put|", "option|seller|", "option|call|"
        )}
        if counts["future|grid|"] or counts["option|call|"]:
            raise RuntimeError(f"excluded leg traded: {layer} {product}: {counts}")
        if not all(counts[s] > 0 for s in ("future|core|", "future|momentum|", "option|core_put|", "option|momentum_put|")):
            raise RuntimeError(f"required core/momentum leg absent: {layer} {product}: {counts}")
        if daily.date.astype(str).tolist() != full.date.astype(str).tolist():
            raise RuntimeError("no-grid calendar mismatch")
        nav_err = float(np.max(np.abs(np.cumprod(1 + daily.return_net.to_numpy(float)) - daily.nav.to_numpy(float))))
        if nav_err > 2e-9 or (daily.cash_close - daily.margin_reserved).min() < -1e-8:
            raise RuntimeError(f"no-grid accounting failed: {layer} {product}")
        daily.insert(0, "data_layer", layer)
        parts.append(daily)
        events.insert(0, "product", product)
        events.insert(0, "data_layer", layer)
        event_parts.append(events)
        audits[product] = {"full_baseline_max_return_error": full_error,
                           "nav_identity_max_error": nav_err,
                           "min_free_cash": float((daily.cash_close - daily.margin_reserved).min()),
                           "trade_counts": counts, "rows": len(daily),
                           "start": str(daily.date.iloc[0]), "end": str(daily.date.iloc[-1]),
                           "input_folder": str(folder.relative_to(ROOT))}
        print(json.dumps({"layer": layer, "product": product, **audits[product]}, ensure_ascii=False), flush=True)
    pd.concat(parts, ignore_index=True).to_csv(HERE / f"{layer}_no_grid_daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(HERE / f"{layer}_no_grid_events.csv.gz", index=False, compression="gzip")
    (HERE / f"{layer}_no_grid_audit.json").write_text(json.dumps({
        "classification": "research_only_latest_no_grid_core_plus_momentum",
        "engine_sha256": digest(STUDY / "native_account.py"), "audits": audits,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", choices=("real", "model"), required=True)
    run(parser.parse_args().layer)
