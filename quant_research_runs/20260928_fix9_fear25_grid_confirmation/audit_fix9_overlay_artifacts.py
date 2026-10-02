"""Independent recomputation and signal/fill audit for the saved fix9 overlay replay."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

RUN_DIR = Path(__file__).resolve().parent
ROOT = RUN_DIR.parents[1]
OUT = RUN_DIR / "outputs"
DAILY_FILE = OUT / "fix9_full_account_daily_nav.csv.gz"
SUMMARY_FILE = RUN_DIR / "portfolio_summary.csv"
SIGNALS_FILE = OUT / "grid_transition_signals.csv"
FILLS_FILE = OUT / "grid_account_fills.csv"
FEAR_FILE = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
STUDY_DIR = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
TOL = 1e-9


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    daily = pd.read_csv(DAILY_FILE, dtype={"date": str})
    summary = pd.read_csv(SUMMARY_FILE)
    signals = pd.read_csv(SIGNALS_FILE, dtype={"signal_date": str, "expected_execution_date": str})
    fills = pd.read_csv(FILLS_FILE, dtype={"date": str, "signal_date": str})
    fear_df = pd.read_csv(FEAR_FILE, dtype={"date": str})
    fear_df["fear_greed_index"] = pd.to_numeric(fear_df.fear_greed_index, errors="coerce")
    fear = fear_df.set_index("date").fear_greed_index
    verification = json.loads((RUN_DIR / "verification.json").read_text(encoding="utf-8"))

    metric_checks = []
    for row in summary.loc[summary.segment.eq("full")].itertuples(index=False):
        q = daily.loc[daily.candidate.eq(row.candidate)].sort_values("date")
        ret = pd.to_numeric(q.return_net, errors="raise").to_numpy(float)
        if len(q) != int(row.rows) or len(ret) == 0 or not np.isfinite(ret).all() or (ret <= -1).any():
            raise RuntimeError(f"invalid daily returns or row count: {row.candidate}")
        nav = np.cumprod(1.0 + ret)
        account_nav = pd.to_numeric(q.nav, errors="raise").to_numpy(float)
        computed = {
            "ann_return": float(nav[-1] ** (252.0 / len(ret)) - 1.0),
            "total_return": float(nav[-1] - 1.0),
            "max_dd": float(np.min(nav / np.maximum.accumulate(nav) - 1.0)),
            "account_nav_max_abs_error": float(np.max(np.abs(nav - account_nav))),
        }
        differences = {k: abs(computed[k] - float(getattr(row, k))) for k in ("ann_return", "total_return", "max_dd")}
        if max(differences.values()) > TOL or computed["account_nav_max_abs_error"] > TOL:
            raise RuntimeError(f"independent metric recomputation differs: {row.candidate}: {differences}")
        metric_checks.append({"candidate": row.candidate, "rows": len(q),
                              "computed": computed, "summary_abs_differences": differences,
                              "status": "PASS"})

    signal_checks = []
    entry_comparisons = []
    for product in ("IC", "IM"):
        arm_val = f"{product}_VALUATION_GRID_LISTED_FIX9"
        arm_fear = f"{product}_FEAR25_ENTRY_CONFIRM_LISTED_FIX9"
        arm_none = f"{product}_NO_GRID_LISTED_FIX9"
        risk_path = STUDY_DIR / f"input_variants/{product.lower()}/formal/native_fix4_{product.lower()}_risk_signals_v1.csv.gz"
        risk = pd.read_csv(risk_path, dtype={"signal_date": str})
        base_target = pd.to_numeric(risk.grid_target_units, errors="raise").to_numpy(float)
        dates = risk.signal_date.astype(str).tolist()
        expected = []
        state = 0.0
        for day, base in zip(dates, base_target):
            if base <= 0:
                state = 0.0
            elif state == 0 and pd.notna(fear.get(day, np.nan)) and float(fear.get(day)) <= 25.0:
                state = 0.5
            expected.append(state)

        for candidate, target in ((arm_none, np.zeros(len(risk))), (arm_val, base_target), (arm_fear, np.asarray(expected))):
            q = daily.loc[daily.candidate.eq(candidate)].sort_values("date")
            actual = pd.to_numeric(q.grid_target_signal_units, errors="raise").to_numpy(float)
            if len(actual) != len(target) or not np.array_equal(actual, target):
                raise RuntimeError(f"grid state differs from independently reconstructed rule: {candidate}")

        s_val = signals.loc[signals.candidate.eq(arm_val)].copy()
        s_fear = signals.loc[signals.candidate.eq(arm_fear)].copy()
        val_sells = s_val.loc[s_val.action.eq("SELL_NEXT_OPEN"), "signal_date"].tolist()
        fear_sells = s_fear.loc[s_fear.action.eq("SELL_NEXT_OPEN"), "signal_date"].tolist()
        if val_sells != fear_sells:
            raise RuntimeError(f"Fear gate altered valuation exits: {product}")
        buy_rows = s_fear.loc[s_fear.action.eq("BUY_NEXT_OPEN")]
        if not (pd.to_numeric(buy_rows.fear_greed_index, errors="raise") <= 25.0).all():
            raise RuntimeError(f"Fear-confirm candidate has a buy above 25: {product}")
        if not set(buy_rows.signal_date).issubset(set(risk.loc[base_target > 0, "signal_date"].astype(str))):
            raise RuntimeError(f"Fear-confirm candidate entered outside valuation zone: {product}")
        signal_checks.append({"product": product,
                              "valuation_exit_dates": val_sells,
                              "fear_exit_dates": fear_sells,
                              "fear_buy_dates": buy_rows.signal_date.tolist(),
                              "fear_scores_at_buy": pd.to_numeric(buy_rows.fear_greed_index, errors="raise").tolist(),
                              "candidate_state_matches_rule": True,
                              "exits_unchanged": True,
                              "status": "PASS"})

        product_daily = daily.loc[daily.instrument.eq(product) & daily.candidate.eq(arm_fear)].sort_values("date")
        date_index = {date: i for i, date in enumerate(product_daily.date.astype(str))}
        for arm, candidate in (("valuation", arm_val), ("fear25", arm_fear)):
            s = signals.loc[signals.candidate.eq(candidate) & signals.action.eq("BUY_NEXT_OPEN")]
            arm_fills = fills.loc[
                fills.candidate.eq(candidate)
                & fills.phase.astype(str).str.lower().eq("open")
                & pd.to_numeric(fills.quantity_change, errors="coerce").gt(0)
            ].copy()
            if "quarter_roll" in arm_fills:
                roll = arm_fills.quarter_roll.fillna(False).astype(str).str.lower().isin(["true", "1"])
                arm_fills = arm_fills.loc[~roll]
            for sig in s.itertuples(index=False):
                matches = arm_fills.loc[arm_fills.signal_date.astype(str).eq(str(sig.signal_date))]
                if len(matches) != 1:
                    raise RuntimeError(f"expected one entry fill for {candidate}/{sig.signal_date}, got {len(matches)}")
                fill = matches.iloc[0]
                if str(fill.date) != str(sig.expected_execution_date):
                    raise RuntimeError(f"entry fill date mismatch: {candidate}/{sig.signal_date}")
                entry_comparisons.append({"product": product, "arm": arm, "signal_date": str(sig.signal_date),
                                          "execution_date": str(fill.date), "fear_score": float(sig.fear_greed_index),
                                          "open_price": float(fill.price), "grid_contract": str(fill.symbol)})
        signal_checks[-1]["entry_fills_t1_open_checked"] = True

    execution = verification["grid_execution_audits"]
    if any(x["missing_t1_open_fills"] or x["wrong_phase_or_date"] for x in execution):
        raise RuntimeError("saved execution audit has missing or mistimed T+1 fills")
    baseline = verification["valuation_grid_fix9_baseline_parity"]
    no_grid = verification["no_grid_saved_fix9_replay_parity"]
    if any(x["status"] != "PASS" for x in baseline + no_grid):
        raise RuntimeError("saved fix9 baseline parity did not pass")

    audit = {
        "classification": "independent_output_recomputation",
        "metric_checks": metric_checks,
        "signal_rule_checks": signal_checks,
        "valuation_grid_fix9_baseline_parity_status": [x["status"] for x in baseline],
        "no_grid_saved_fix9_parity_status": [x["status"] for x in no_grid],
        "all_grid_transition_execution_checks_pass": True,
        "entry_fills": entry_comparisons,
        "audit_script_sha256": sha256(Path(__file__).resolve()),
    }
    (OUT / "independent_recheck.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hashes_path = RUN_DIR / "source_hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    hashes[str(Path(__file__).resolve().relative_to(ROOT))] = sha256(Path(__file__).resolve())
    hashes_path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "metric_paths": len(metric_checks),
                      "signal_rule_paths": len(signal_checks),
                      "entry_fills": len(entry_comparisons),
                      "output": str((OUT / "independent_recheck.json").relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
