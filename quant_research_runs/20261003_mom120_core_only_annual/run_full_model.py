"""Replay latest fix10 grid and fix11 IC momentum on pre-listing model inputs."""
from __future__ import annotations

import calendar
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from run_core_only import HERE, ROOT, MODEL_SOURCE, NATIVE, STUDY, REFERENCE, digest, load, verify_daily

FINAL = ROOT / "quant_param_scan_runs/20260927_fix9_model_fullaccount_final_redteam"
MODEL_ROOT = ROOT / "quant_param_scan_runs/20260927_fix9_model_fullaccount"
PAIRED = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2"
INPUTS = HERE / "latest_model_inputs"
sys.path[:0] = [str(MODEL_SOURCE), str(MODEL_ROOT), str(NATIVE), str(STUDY), str(ROOT)]

import run_model as model_runner  # noqa: E402
from model_source_v3 import ListedCorrectedModelSources  # noqa: E402
import native_account as account  # noqa: E402


def main() -> None:
    source = ListedCorrectedModelSources()
    model_dates = pd.DatetimeIndex(source.ic_market.index)
    formal = model_runner.risk_stage.formal
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
    old_fix11 = model_runner.risk_stage.formal.IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE
    producer = model_runner.patched_function(model_runner.risk_stage, "produce", {
        'first = pd.Timestamp("2022-09-19" if product == "IC" else "2022-07-22")':
            'first = pd.Timestamp("2015-04-16")',
        'last = pd.Timestamp("2026-08-13" if product == "IC" else "2026-08-14")':
            'last = pd.Timestamp("2022-07-21")',
        'if len(out) != (945 if product == "IC" else 986):': 'if len(out) != 1770:',
        'if len(previous) != 57 or previous.isna().any():':
            'if len(previous) < 6 or previous.isna().any():',
    })
    model_runner.risk_stage.formal.IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE = date(1900, 1, 1)
    try:
        risk11, _ = producer("IC", source)
    finally:
        model_runner.risk_stage.formal.IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE = old_fix11
    risk_old = pd.read_csv(FINAL / "native_fix4_ic_risk_signals_v1.csv.gz")
    if risk11.signal_date.tolist() != risk_old.signal_date.tolist():
        raise RuntimeError("IC model risk calendar mismatch")
    changed = {"momentum_target_weight", "momentum_future_units", "momentum_put_target_delta"}
    for col in risk_old.columns:
        if col in changed:
            continue
        a, b = risk11[col], risk_old[col]
        if pd.api.types.is_numeric_dtype(a):
            if not np.allclose(a, b, rtol=0, atol=1e-12, equal_nan=True):
                raise RuntimeError(f"FIX11 modified non-momentum risk: {col}")
        elif not a.fillna("<NA>").astype(str).equals(b.fillna("<NA>").astype(str)):
            raise RuntimeError(f"FIX11 modified non-momentum risk: {col}")
    sys.path.insert(0, str(PAIRED))
    paired = load("latest_model_paired_grid", PAIRED / "run_scan.py")
    fear_file = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
    fear = pd.read_csv(fear_file, dtype={"date": str}).set_index("date").fear_greed_index
    INPUTS.mkdir(exist_ok=True)
    risk_frames = {}
    grid_counts = {}
    for product in ("IC", "IM"):
        risk = risk11 if product == "IC" else pd.read_csv(FINAL / "native_fix4_im_risk_signals_v1.csv.gz")
        result, signals, _ = paired.build_targets(risk, fear, product, 50.0)
        result.to_csv(INPUTS / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz", index=False, compression="gzip")
        risk_frames[product] = result
        grid_counts[product] = {"entries": int(signals.action.eq("BUY_NEXT_OPEN").sum()),
                                "exits": int(signals.action.eq("SELL_NEXT_OPEN").sum())}
    # The seller rule depends on valuation and MOM120, both unchanged by FIX11;
    # no Call is opened under FIX6 and the grid has no option coverage.
    for name in ("native_fix4_im_noseller_targets_v3.csv.gz",
                 "native_fix4_seller_candidates_v1.csv.gz", "native_fix4_seller_lifecycle_v4.csv.gz"):
        shutil.copy2(FINAL / name, INPUTS / name)
    target_module = model_runner.ic_target_stage
    old_here, old_source = target_module.HERE, target_module.DatedSources
    try:
        target_module.HERE = INPUTS
        target_module.DatedSources = lambda: source
        build_targets = model_runner.patched_function(target_module, "main", {
            "if len(frame) != 945": "if len(frame) != 1770"
        })
        if not (INPUTS / "native_fix4_ic_noseller_targets_v2.csv.gz").exists():
            build_targets(version="v2")
    finally:
        target_module.HERE, target_module.DatedSources = old_here, old_source
    old_target = pd.read_csv(FINAL / "native_fix4_ic_noseller_targets_v2.csv.gz")
    new_target = pd.read_csv(INPUTS / "native_fix4_ic_noseller_targets_v2.csv.gz")
    if old_target.signal_date.tolist() != new_target.signal_date.tolist():
        raise RuntimeError("IC target calendar mismatch")
    core_target_changes = int(new_target.core_put_qty_target.ne(old_target.core_put_qty_target).sum())
    runner = load("latest_model_account_config", REFERENCE / "run_candidate.py")
    lifecycle = pd.read_csv(FINAL / "native_fix4_seller_lifecycle_v4.csv.gz")
    candidates = pd.read_csv(FINAL / "native_fix4_seller_candidates_v1.csv.gz")
    gov = pd.read_csv(ROOT / "data/ic_im_valuation_risk_premium_forecast_v4/chinabond_government_10y.csv",
                      parse_dates=["date"]).set_index("date").gov10y_yield
    spot = pd.read_csv(model_runner.OHLCV["IM"], parse_dates=["date"]).set_index("date").close
    baseline_errors, outputs, event_parts = {}, [], []
    for product in ("IC", "IM"):
        runner.configure_engine(account, FINAL)
        original, _, _ = account.replay(product, "repeat_roll", source, lifecycle, candidates, gov, spot)
        baseline_errors[product] = verify_daily(original, pd.read_csv(FINAL / f"{product.lower()}_daily.csv.gz"),
                                                f"model full {product}")
        runner.configure_engine(account, INPUTS)
        daily, events, _ = account.replay(product, "repeat_roll", source, lifecycle, candidates, gov, spot)
        signal = risk_frames[product][["signal_date", "execution_date", "momentum_120"]].dropna(subset=["execution_date"])
        daily = daily.merge(signal, left_on="date", right_on="execution_date", how="left", validate="one_to_one")
        daily["mom120_negative_execution"] = daily.momentum_120.lt(0)
        daily.insert(0, "data_layer", "model")
        outputs.append(daily)
        events.insert(0, "product", product)
        event_parts.append(events)
        print(json.dumps({"product": product, "rows": len(daily), "baseline_error": baseline_errors[product],
                          "latest_nav": float(daily.nav.iloc[-1])}, ensure_ascii=False), flush=True)
    pd.concat(outputs, ignore_index=True).to_csv(HERE / "model_latest_full_daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(HERE / "model_latest_full_events.csv.gz", index=False, compression="gzip")
    (HERE / "model_latest_full_audit.json").write_text(json.dumps({
        "classification": "research_only_latest_fix10_fix11_as_if_current_prelisting_model",
        "baseline_max_return_error": baseline_errors, "grid_signals": grid_counts,
        "ic_core_put_target_quantity_changed_days_from_fix9": core_target_changes,
        "producer_sha256": digest(ROOT / "poe_ic_im_mainline_v1_4_bot.py"),
        "fear_snapshot_sha256": digest(fear_file), "account_engine_sha256": digest(Path(account.__file__)),
        "input_sha256": {p.name: digest(p) for p in INPUTS.glob("*.csv.gz")}
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
