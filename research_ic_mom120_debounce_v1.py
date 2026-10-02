"""Research-only IC core-Put MOM120 release-debounce replay."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from im_put_maturity_valuation_tiers_v3 import metrics
import run_ic_v13_sleeve_put_independent_replay_v1 as ic


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260915_ic_mom120_two_day_plus1_release"
END = pd.Timestamp("2026-08-14")


@dataclass(frozen=True)
class Variant:
    name: str
    release_days: int
    release_threshold: float


VARIANTS = (
    Variant("baseline_negative_immediate_release", 1, 0.0),
    Variant("release_positive_2d", 2, 0.0),
    Variant("release_plus1_2d", 2, 0.01),
    Variant("release_plus1_3d", 3, 0.01),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def mom_floor_state(momentum: pd.Series, *, days: int, threshold: float) -> pd.Series:
    """Activate instantly at MOM120<0; release only after N > threshold days."""
    active, streak, out = False, 0, []
    for item in pd.to_numeric(momentum, errors="coerce"):
        if not np.isfinite(item):
            out.append(active)
            continue
        if item < 0.0:
            active, streak = True, 0
        elif active and item > threshold:
            streak += 1
            if streak >= days:
                active, streak = False, 0
        elif active:
            streak = 0
        out.append(active)
    return pd.Series(out, index=momentum.index, dtype=bool)


def put_runs(state: pd.Series) -> dict[str, object]:
    lengths, run, on = [], 0, False
    for item in state:
        if item:
            run, on = run + 1, True
        elif on:
            lengths.append(run)
            run, on = 0, False
    if on:
        lengths.append(run)
    return {"floor_runs": len(lengths), "floor_days": int(state.sum()),
            "strict_lt3_runs": sum(x < 3 for x in lengths), "median_floor_run_days": float(np.median(lengths)) if lengths else math.nan}


def build_schedule(selected: pd.DataFrame, tri: pd.Series, variant: Variant) -> pd.DataFrame:
    schedule = selected.sort_values("eval_date").copy()
    schedule["momentum_120"] = schedule.eval_date.map(tri.pct_change(120, fill_method=None))
    schedule["mom120_floor_active"] = mom_floor_state(
        schedule["momentum_120"], days=variant.release_days, threshold=variant.release_threshold
    )
    schedule["mom120_floor_delta"] = np.where(schedule["mom120_floor_active"], 0.5, 0.0)
    schedule["v2_target_delta"] = np.maximum(schedule.valuation_tier_new * 0.25, schedule.mom120_floor_delta)
    return ic.build_schedule(schedule, "combined_current")


def main() -> None:
    frame, _, selected = ic.load_base_components()
    frames, valuation, market, _ = ic.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    roll = ic.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    tri = valuation.set_index("date").tri_close
    source_schedule = selected.copy()
    native = ic.build_schedule(source_schedule, "combined_current")
    # The production rule must be exactly reproduced before candidate comparison.
    baseline_schedule = build_schedule(source_schedule, tri, VARIANTS[0])
    expected = source_schedule.sort_values("eval_date").v2_target_delta.to_numpy(float)
    parity = float(np.max(np.abs(baseline_schedule.v2_target_delta.to_numpy(float) - expected)))
    if parity > 1e-12:
        raise RuntimeError(f"Native IC MOM120 target parity failed: {parity}")
    daily, trades, result_rows, state_rows = [], [], [], []
    for variant in VARIANTS:
        schedule = build_schedule(source_schedule, tri, variant)
        schedule.to_csv(RUN / f"{variant.name}_schedule.csv.gz", index=False, compression="gzip")
        state_rows.append({"candidate": variant.name, **put_runs(schedule["mom120_floor_active"])})
        for scope in ("model", "real"):
            label = f"{scope}_{variant.name}"
            engine = ic.ic_put.v1.put_engine
            if scope == "model":
                put, trade = engine.run_model_delta(frames["ic"], schedule, market, label, roll)
            else:
                put, trade = engine.run_real_delta(frames["ic"], schedule, frames, market, label, roll)
            trade.assign(candidate=label).to_csv(RUN / f"{label}_trades.csv.gz", index=False, compression="gzip")
            component = frame[frame.date.isin(put.date)].copy().reset_index(drop=True)
            put = put.reset_index(drop=True)
            if not component.date.equals(put.date):
                raise RuntimeError(f"IC component alignment failed for {label}")
            combined = ic.combine_candidate(component, {"combined": put}, label, ("combined",))
            combined["return_net"] = combined.ret
            if scope == "real":
                combined = combined[combined.date.ge(pd.Timestamp("2022-09-19"))].copy()
            combined = combined[combined.date.le(END)].copy()
            daily.append(combined.assign(candidate=label))
            trades.append(trade.assign(candidate=label))
            for seg, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
                start = combined.date.min() if years is None else END - pd.DateOffset(years=years)
                sample = combined[combined.date.ge(start)]
                if sample.empty or start < combined.date.min():
                    values = {key: "N/A" for key in ("ann_return", "ann_vol", "sharpe_repo", "max_dd")}
                    count = 0
                else:
                    values, count = metrics(sample.return_net), len(sample)
                result_rows.append({"candidate": label, "window": seg, "segment": seg, "start": str(start.date()), "end": str(END.date()), "rows": count, **values})
    summary = pd.DataFrame(result_rows)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8")
    wide_rows = []
    for candidate, group in summary.groupby("candidate", sort=False):
        wide = {"candidate": candidate}
        for _, row in group.iterrows():
            wide[f"ann_return_{row.window}"] = row.ann_return
            wide[f"max_dd_{row.window}"] = row.max_dd
        wide_rows.append(wide)
    pd.DataFrame(wide_rows).to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8")
    pd.concat(daily, ignore_index=True).to_csv(RUN / "daily.csv.gz", index=False, compression="gzip")
    pd.DataFrame(state_rows).to_csv(RUN / "floor_run_stats.csv", index=False, encoding="utf-8")
    if trades:
        pd.concat(trades, ignore_index=True).to_csv(RUN / "trades.csv.gz", index=False, compression="gzip")
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({
        "phase": "complete", "candidate_grid": [asdict(x) for x in VARIANTS],
        "repo_root": str(ROOT),
        "git_branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "baseline": {"candidate": "baseline_negative_immediate_release", "definition": "formal MOM120<0 core Put 50% floor"},
        "parity": {"formal_v2_target_delta_max_abs": parity},
        "data_snapshot": {"valuation_tri_start": str(tri.index.min().date()), "valuation_tri_end": str(tri.index.max().date())},
        "cost_model": {"method": "native IC Put engine fees/execution and frozen component replay", "excluded": "current r7 grid/other changes, live ledger, bid-ask, integer sizing and forced-liquidation stress"},
        "decision": "research_only_no_parameter_promotion", "stability_label": "pending_result_interpretation",
        "outputs": {"record": str(RUN / "record.md"), "scan_summary": str(RUN / "scan_summary.csv"), "window_metrics": str(RUN / "window_metrics.csv"), "floor_run_stats": str(RUN / "floor_run_stats.csv")},
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IC MOM120 核心 Put 防抖回放\n\n研究状态：仅研究；没有改动 r7、账本或下单。\n\n"
    record += "## Data\n\n- 复用 IC 独立 Put 引擎的冻结实际/模型组件，截止 2026-08-14。\n"
    record += f"- 原正式 MOM120<0 目标复现误差：{parity:.3e}。\n"
    record += "- 候选保持负 MOM120 当日立即提高至 50% Put 下限；仅在连续正向门槛满足后取消该下限。\n\n"
    record += "## Results\n\n" + summary.to_markdown(index=False) + "\n\n"
    record += "## Decision\n\n- Decision: research_only_pending_cost_and_full_current_r7_replay\n\n"
    record += "## Stability\n\n- Stability: coarse pre-registered release variants; real history is limited.\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as f:
        f.write("python -X utf8 research_ic_mom120_debounce_v1.py\n")
    print(json.dumps({"run": str(RUN), "parity": parity, "rows": len(summary)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
