"""Recover metadata after the completed full-component scan writes its data."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

import run_ic_im_v13_r7_r2_full_components_width_scan as scan


def main() -> None:
    run = scan.RUN
    summary = pd.read_csv(run / "candidate_summary_detail.csv")
    metrics_path = run / "window_metrics_detail.csv"
    metrics = pd.read_csv(metrics_path if metrics_path.is_file() else run / "window_metrics.csv")
    validation = json.loads((run / "validation.json").read_text(encoding="utf-8"))
    if len(summary) != 232 or summary["candidate"].nunique() != 232:
        raise RuntimeError("completed candidate table is incomplete")
    for scope in ("full", "real"):
        summary[f"{scope}_return_improved"] = summary[f"{scope}_ann_return_delta_pp"].astype(float).gt(100.0 * scan.IMPROVEMENT_EPS)
        summary[f"{scope}_max_dd_improved"] = summary[f"{scope}_max_dd_delta_pp"].astype(float).gt(100.0 * scan.IMPROVEMENT_EPS)
        summary[f"{scope}_pass_either"] = summary[[f"{scope}_return_improved", f"{scope}_max_dd_improved"]].any(axis=1)
    summary["pass_both_segments"] = summary["full_pass_either"] & summary["real_pass_either"]
    summary.to_csv(run / "candidate_summary_detail.csv", index=False, encoding="utf-8-sig")
    fbase = scan.load_module("full_component_recovery_fbase", scan.F_BASE_SCRIPT)
    width = pd.concat(
        [fbase.summarize_width(summary.rename(columns={"real_pass_either": "real_only_pass_either"}), gate) for gate in ("full", "real_only", "both")],
        ignore_index=True,
    )
    width.to_csv(run / "width_summary.csv", index=False, encoding="utf-8-sig")
    if not (width["product"].eq("IC") & width["gate"].eq("full")).any():
        raise RuntimeError("IC full width result is missing")
    long_metrics, wide_metrics = scan.checker_compatible_tables(metrics)
    metrics.to_csv(run / "window_metrics_detail.csv", index=False, encoding="utf-8-sig")
    long_metrics.to_csv(run / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide_metrics.to_csv(run / "window_metrics.csv", index=False, encoding="utf-8-sig")
    validation["improvement_epsilon"] = scan.IMPROVEMENT_EPS
    (run / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    scan.write_record(summary, validation, width)
    meta_path = run / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    full_width = width.loc[width["product"].eq("IC") & width["gate"].eq("full")].iloc[0]
    stability = "broad" if bool(full_width["spans_3x3"]) else "narrow"
    meta.update({
        "phase": "executed",
        "scan_type": "two_dimensional_r2_window_threshold_width_scan_full_components",
        "baseline": {"candidate": "r2_off", "definition": "IC fixed F + momentum F + grid + core/momentum Put; grid Put=0; Call=0"},
        "candidate_grid": {"r2_windows": list(scan.R2_WINDOWS), "r2_thresholds": list(scan.R2_THRESHOLDS), "candidate_count": 231},
        "data_snapshot": {"start": scan.START.date().isoformat(), "historical_component_end": scan.HISTORICAL_END.date().isoformat(), "end": scan.END.date().isoformat(), "real_option_start": scan.REAL_START.date().isoformat(), "tail_refresh": str(scan.REFRESH.relative_to(scan.ROOT))},
        "cost_model": {"futures_one_way": scan.ONE_WAY, "put_one_way": scan.ONE_WAY, "margin_buffer_per_IC_unit": scan.MARGIN, "cash_annual": 0.03},
        "source_hashes": {scan.display_path(path): scan.sha256(path) for path in (scan.F_BASE_SCRIPT, scan.IC_MAINLINE, scan.ASHARE_SOURCE, scan.REFRESH_SCRIPT, scan.SCHEDULE_FILE)},
        "validation": validation,
        "decision": "research_only_pending_user_review",
        "stability_label": stability,
        "git_status_after": scan.git_value("status", "--short"),
    })
    meta["outputs"].update({
        "candidate_summary_detail": str((run / "candidate_summary_detail.csv").relative_to(scan.ROOT)),
        "daily_candidate_returns": str((run / "daily_candidate_returns.csv.gz").relative_to(scan.ROOT)),
        "tail_candidate_returns": str((run / "tail_candidate_returns.csv.gz").relative_to(scan.ROOT)),
        "width_summary": str((run / "width_summary.csv").relative_to(scan.ROOT)),
        "validation": str((run / "validation.json").relative_to(scan.ROOT)),
        "heatmap": str((run / "ic_full_components_r2_full_real_heatmap.png").relative_to(scan.ROOT)),
        "window_metrics_detail": str((run / "window_metrics_detail.csv").relative_to(scan.ROOT)),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (run / "command_log.txt").write_text(
        (run / "command_log.txt").read_text(encoding="utf-8")
        + f"python -X utf8 {Path(__file__).name}\n"
        + "recovered_metadata_without_replaying_candidates=true\n"
        + f"recovery_epoch={time.time():.6f}\n",
        encoding="utf-8",
    )
    print(json.dumps({"rows": len(summary), "stability": stability, "full_pass": int(summary.loc[summary["candidate"].ne("r2_off"), "full_pass_either"].sum())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
