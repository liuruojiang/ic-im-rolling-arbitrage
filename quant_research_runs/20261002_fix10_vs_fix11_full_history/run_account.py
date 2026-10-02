"""Listed IC account replay for FIX10/FIX11, each rule set applied from inception."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STUDY = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
PAIRED = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2"
sys.path.insert(0, str(STUDY))
import run_study as study  # noqa: E402

spec = importlib.util.spec_from_file_location("paired_fear50_scan", PAIRED / "run_scan.py")
assert spec is not None and spec.loader is not None
paired = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paired)

formal = study.FORMAL
producer = study.risk_producer
targets = study.ic_targets
listed = paired.listed
BASELINE = PAIRED / "outputs/fix9_full_account_daily_nav.csv.gz"
BASELINE_LABEL = "IC_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_frame_parity(left: pd.DataFrame, right: pd.DataFrame, tolerance: float) -> None:
    assert left.columns.tolist() == right.columns.tolist() and len(left) == len(right)
    for col in left:
        a, b = left[col].reset_index(drop=True), right[col].reset_index(drop=True)
        assert a.isna().equals(b.isna()), f"missingness differs: {col}"
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            assert np.allclose(a, b, atol=tolerance, rtol=0, equal_nan=True), col
        else:
            assert a.fillna("<NA>").astype(str).equals(b.fillna("<NA>").astype(str)), col


def risk_for_full_arm(effective_date: date, source) -> pd.DataFrame:
    old = formal.IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE
    try:
        formal.IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE = effective_date
        result, _ = producer.produce("IC", source)
    finally:
        formal.IC_MOMENTUM_EFFECTIVE_SIGNAL_DATE = old
    return result


def target_for_arm(name: str, risk: pd.DataFrame, source) -> Path:
    folder = HERE / "account_inputs" / name
    folder.mkdir(parents=True, exist_ok=True)
    risk_path = folder / "native_fix4_ic_risk_signals_v1.csv.gz"
    target_path = folder / "native_fix4_ic_noseller_targets_v2.csv.gz"
    if target_path.exists():
        saved_risk = pd.read_csv(risk_path)
        assert_frame_parity(saved_risk, risk, 1e-12)
        saved_audit = json.loads((folder / "native_fix4_ic_noseller_targets_v2.json").read_text(encoding="utf-8"))
        assert saved_audit["input_risk_sha256"] == sha(risk_path)
        assert saved_audit["formal_producer_sha256"] == sha(ROOT / "poe_ic_im_mainline_v1_4_bot.py")
        return target_path
    risk.to_csv(risk_path, index=False, compression="gzip")
    old_here, old_source = targets.HERE, targets.DatedSources
    try:
        targets.HERE = folder
        targets.DatedSources = lambda: source
        targets.main(version="v2")
    finally:
        targets.HERE, targets.DatedSources = old_here, old_source
    return target_path


def account_metrics(daily: pd.DataFrame, start: pd.Timestamp | None) -> dict:
    z = daily.copy()
    z["date"] = pd.to_datetime(z.date)
    if start is not None:
        z = z.loc[z.date >= start]
    ret = pd.to_numeric(z.return_net, errors="raise").to_numpy(float)
    nav = np.r_[1.0, np.cumprod(1.0 + ret)]
    dd = nav / np.maximum.accumulate(nav) - 1.0
    sd = np.std(ret, ddof=1)
    return {
        "start": str(z.date.iloc[0].date()), "end": str(z.date.iloc[-1].date()),
        "rows": len(z), "cumulative_return": float(nav[-1] - 1.0),
        "annual_return_252": float(nav[-1] ** (252 / len(z)) - 1.0),
        "sharpe_252": float(np.mean(ret) / sd * np.sqrt(252)) if sd > 0 else 0.0,
        "max_drawdown": float(dd.min()),
        "final_nav_from_one": float(nav[-1]),
    }


def main() -> None:
    source, candidates, gov, spot, lifecycles, formal_folders, *_ = listed.load_account_inputs()
    risk10 = risk_for_full_arm(date(2100, 1, 1), source)
    risk11 = risk_for_full_arm(date(1900, 1, 1), source)
    original = pd.read_csv(formal_folders["IC"] / "native_fix4_ic_risk_signals_v1.csv.gz")
    assert_frame_parity(risk10, original, 1e-12)
    unchanged = [c for c in risk10 if c not in {
        "momentum_target_weight", "momentum_future_units", "momentum_put_target_delta"}]
    assert_frame_parity(risk10[unchanged], risk11[unchanged], 1e-12)
    assert len(risk10) == len(risk11) == 945
    fear_frame = pd.read_csv(listed.FEAR_FILE, dtype={"date": str})
    fear = pd.to_numeric(fear_frame.set_index("date").fear_greed_index, errors="coerce")
    arms: dict[str, pd.DataFrame] = {}
    signal_rows = []
    event_rows = []
    audit = {"risk10_saved_parity": "PASS", "risk_unchanged_columns_parity": "PASS",
             "changed_momentum_target_days": int((risk10.momentum_target_weight - risk11.momentum_target_weight).abs().gt(1e-12).sum())}
    listed.SCRATCH_DIR = HERE / "scratch"
    listed.SCRATCH_DIR.mkdir(exist_ok=True)
    for name, risk in (("FIX10_full", risk10), ("FIX11_full", risk11)):
        risk_grid, signals, _ = paired.build_targets(risk, fear, "IC", 50.0)
        target_path = target_for_arm(name, risk_grid, source)
        if name == "FIX10_full":
            original_targets = pd.read_csv(formal_folders["IC"] / "native_fix4_ic_noseller_targets_v2.csv.gz")
            generated_targets = pd.read_csv(target_path)
            assert_frame_parity(generated_targets, original_targets, 1e-10)
            audit["fix10_put_targets_saved_parity"] = "PASS"
        daily, events, _ = listed.replay(
            "IC", risk_grid, target_path, lifecycles["IC"], source, candidates, gov, spot
        )
        if name == "FIX10_full":
            saved = pd.read_csv(BASELINE)
            saved = saved.loc[saved.candidate.eq(BASELINE_LABEL)].reset_index(drop=True)
            assert len(saved) == len(daily) == 945
            assert saved.date.astype(str).tolist() == daily.date.astype(str).tolist()
            for col in daily.columns:
                if col == "arm":
                    continue
                a, b = daily[col], saved[col]
                assert a.isna().equals(b.isna()), f"saved account missingness differs: {col}"
                if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
                    assert np.allclose(a, b, rtol=0, atol=1e-6, equal_nan=True), col
                else:
                    assert a.fillna("<NA>").astype(str).equals(b.fillna("<NA>").astype(str)), col
            audit["fix10_saved_full_account_parity"] = "PASS"
        assert len(daily) == 945 and np.isfinite(daily.return_net).all()
        daily["arm"] = name
        arms[name] = daily
        events["arm"] = name
        event_rows.append(events)
        signals["arm"] = name
        signal_rows.append(signals)
        print(json.dumps({"arm": name, "rows": len(daily), "events": len(events)}, ensure_ascii=False), flush=True)
    assert arms["FIX10_full"].date.astype(str).tolist() == arms["FIX11_full"].date.astype(str).tolist()
    end = pd.Timestamp(arms["FIX10_full"].date.iloc[-1])
    windows = {"Full": None, "3Y": end - pd.DateOffset(years=3), "1Y": end - pd.DateOffset(years=1)}
    summary = [{"arm": name, "window": window, **account_metrics(daily, start)}
               for name, daily in arms.items() for window, start in windows.items()]
    pd.DataFrame(summary).to_csv(HERE / "account_window_metrics.csv", index=False)
    pd.concat(arms.values(), ignore_index=True).to_csv(HERE / "account_daily_paths.csv.gz", index=False, compression="gzip")
    pd.concat(event_rows, ignore_index=True).to_csv(HERE / "account_events.csv.gz", index=False, compression="gzip")
    pd.concat(signal_rows, ignore_index=True).to_csv(HERE / "account_grid_signals.csv", index=False)
    audit.update({"scope": "IC listed full-account theoretical replay; Fear snapshot not point-in-time certified",
                  "fear_sha256": sha(listed.FEAR_FILE), "baseline_sha256": sha(BASELINE),
                  "formal_source_sha256": sha(ROOT / "poe_ic_im_mainline_v1_4_bot.py"),
                  "research_script_sha256": sha(Path(__file__)),
                  "spec_sha256": sha(HERE / "preregistered_spec.md"),
                  "target_sha256": {name: sha(HERE / "account_inputs" / name /
                                      "native_fix4_ic_noseller_targets_v2.csv.gz") for name in arms}})
    (HERE / "account_manifest.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()
