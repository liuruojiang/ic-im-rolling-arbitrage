"""Independent return recomputation and entry/exit rule audit for the fix9 OR replay."""
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
STUDY_DIR = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
FEAR_FILE = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
THRESHOLDS = {"IC": {"entry": 0.5, "exit": 1.0}, "IM": {"entry": 1.6, "exit": 2.0}}
TOL = 1e-9


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    daily = pd.read_csv(OUT / "fix9_full_account_daily_nav.csv.gz", dtype={"date": str})
    summary = pd.read_csv(RUN_DIR / "portfolio_summary.csv")
    signals = pd.read_csv(OUT / "grid_transition_signals.csv", dtype={"signal_date": str, "expected_execution_date": str})
    fear_df = pd.read_csv(FEAR_FILE, dtype={"date": str})
    fear_df["fear_greed_index"] = pd.to_numeric(fear_df.fear_greed_index, errors="coerce")
    fear = fear_df.set_index("date").fear_greed_index
    checks = []
    for row in summary.loc[summary.segment.eq("full")].itertuples(index=False):
        q = daily.loc[daily.candidate.eq(row.candidate)].sort_values("date")
        ret = pd.to_numeric(q.return_net, errors="raise").to_numpy(float)
        nav = np.cumprod(1 + ret)
        calc = {"ann_return": float(nav[-1] ** (252 / len(nav)) - 1),
                "total_return": float(nav[-1] - 1),
                "max_dd": float(np.min(nav / np.maximum.accumulate(nav) - 1)),
                "nav_max_abs_error": float(np.max(np.abs(nav - q.nav.to_numpy(float))))}
        diff = {k: abs(calc[k] - float(getattr(row, k))) for k in ("ann_return", "total_return", "max_dd")}
        if max(diff.values()) > TOL or calc["nav_max_abs_error"] > TOL:
            raise RuntimeError(f"summary metric mismatch: {row.candidate}/{diff}")
        checks.append({"candidate": row.candidate, "rows": len(q), "computed": calc,
                       "summary_abs_differences": diff, "status": "PASS"})

    fear_rule_checks = []
    for product in ("IC", "IM"):
        risk_path = STUDY_DIR / f"input_variants/{product.lower()}/formal/native_fix4_{product.lower()}_risk_signals_v1.csv.gz"
        risk = pd.read_csv(risk_path, dtype={"signal_date": str})
        scores = pd.to_numeric(risk.score, errors="coerce").to_numpy(float)
        dates = risk.signal_date.astype(str).tolist()
        original = pd.to_numeric(risk.grid_target_units, errors="raise").to_numpy(float)
        states_by_arm = {}
        for arm in ("FEAR_ONLY_ENTRY_VALUATION_EXIT", "VALUATION_OR_FEAR25"):
            state = 0.0
            targets = []
            for day, score in zip(dates, scores):
                fear_score = fear.get(day, np.nan)
                if state > 0:
                    if math.isfinite(score) and score >= THRESHOLDS[product]["exit"]:
                        state = 0.0
                elif arm == "FEAR_ONLY_ENTRY_VALUATION_EXIT":
                    if pd.notna(fear_score) and float(fear_score) <= 25:
                        state = 0.5
                elif ((math.isfinite(score) and score <= THRESHOLDS[product]["entry"])
                      or (pd.notna(fear_score) and float(fear_score) <= 25)):
                    state = 0.5
                targets.append(state)
            candidate = f"{product}_{arm}_LISTED_FIX9"
            q = daily.loc[daily.candidate.eq(candidate)].sort_values("date")
            actual = pd.to_numeric(q.grid_target_signal_units, errors="raise").to_numpy(float)
            if not np.array_equal(actual, np.asarray(targets, dtype=float)):
                raise RuntimeError(f"state reconstruction mismatch: {candidate}")
            states_by_arm[arm] = targets
            arm_signals = signals.loc[signals.candidate.eq(candidate)]
            buys = arm_signals.loc[arm_signals.action.eq("BUY_NEXT_OPEN")]
            sells = arm_signals.loc[arm_signals.action.eq("SELL_NEXT_OPEN")]
            for b in buys.itertuples(index=False):
                val = pd.to_numeric(pd.Series([b.valuation_score]), errors="coerce").iloc[0]
                f = fear.get(str(b.signal_date), np.nan)
                if arm == "FEAR_ONLY_ENTRY_VALUATION_EXIT":
                    valid = pd.notna(f) and float(f) <= 25
                else:
                    valid = (pd.notna(val) and float(val) <= THRESHOLDS[product]["entry"]) or (pd.notna(f) and float(f) <= 25)
                if not valid:
                    raise RuntimeError(f"invalid entry trigger: {candidate}/{b.signal_date}")
            if not (pd.to_numeric(sells.valuation_score, errors="raise") >= THRESHOLDS[product]["exit"]).all():
                raise RuntimeError(f"exit did not follow valuation line: {candidate}")
            fear_rule_checks.append({"candidate": candidate, "buys": len(buys), "sells": len(sells),
                                     "state_reconstruction": "PASS", "entry_conditions": "PASS",
                                     "valuation_exits": "PASS"})
        # The OR path must contain every valuation-only entry rule; its actual state stream is audited above.
        val_target = daily.loc[daily.candidate.eq(f"{product}_VALUATION_GRID_LISTED_FIX9")].sort_values("date").grid_target_signal_units.to_numpy(float)
        if not np.array_equal(val_target, original):
            raise RuntimeError(f"valuation baseline target stream differs: {product}")

    verification = json.loads((RUN_DIR / "verification.json").read_text(encoding="utf-8"))
    all_exec = verification["grid_execution_audits"]
    if any(x["missing_t1_open_fills"] or x["wrong_phase_or_date"] for x in all_exec):
        raise RuntimeError("grid fill date/phase audit failed")
    parity = verification["valuation_grid_fix9_baseline_parity"] + verification["no_grid_saved_fix9_replay_parity"]
    if any(x["status"] != "PASS" for x in parity):
        raise RuntimeError("fix9 baseline parity failed")
    audit = {"classification": "independent_output_recomputation",
             "metric_path_count": len(checks), "metric_checks": checks,
             "entry_exit_rule_checks": fear_rule_checks,
             "baseline_parity": [x["status"] for x in parity],
             "all_grid_execution_audits_pass": True,
             "audit_script_sha256": sha256(Path(__file__).resolve())}
    (OUT / "independent_recheck.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hashes_path = RUN_DIR / "source_hashes.json"
    hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
    hashes[str(Path(__file__).resolve().relative_to(ROOT))] = sha256(Path(__file__).resolve())
    hashes_path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "metric_paths": len(checks),
                      "state_rule_paths": len(fear_rule_checks),
                      "baseline_parity": len(parity), "execution_paths": len(all_exec)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
