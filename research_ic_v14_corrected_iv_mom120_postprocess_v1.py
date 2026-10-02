"""Add concentration and paired-gate diagnostics to the corrected IC scan."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_ic_v1_4_r1_current_joint_corrected_iv_ic_fixed_core_"
    "short95_put_router_corrected_m_1_95_iv_threshold_x_seller_mom120_gate"
)


def main() -> None:
    daily = pd.read_csv(RUN / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    cycles = pd.read_csv(RUN / "daily_outputs" / "cycles.csv")
    summary = pd.read_csv(RUN / "scan_summary.csv")
    exposure = pd.read_csv(RUN / "exposure_audit.csv")

    corrected = daily[~daily.candidate.str.contains("legacy")].copy()
    corrected["year"] = corrected.date.dt.year
    annual = (
        corrected.groupby(["candidate", "scope", "variant", "year"], as_index=False)
        .agg(
            days=("return_net", "size"),
            calendar_return=("return_net", lambda x: float(np.prod(1.0 + x) - 1.0)),
            put_pnl_return=("put_pnl_ret", "sum"),
            put_cost_return=("put_cost_rate", "sum"),
            short_put_days=("fixed_router_state", lambda x: int(x.eq("short_put").sum())),
        )
    )

    pnl = cycles.realized_pnl.fillna(0.0) + cycles.open_cycle_pnl.fillna(0.0)
    cycles = cycles.assign(cycle_pnl=pnl)
    concentration_rows: list[dict[str, object]] = []
    for candidate, frame in cycles.groupby("candidate", sort=False):
        abs_pnl = frame.cycle_pnl.abs()
        abs_total = float(abs_pnl.sum())
        concentration_rows.append(
            {
                "candidate": candidate,
                "scope": frame.scope.iloc[0],
                "cycles": len(frame),
                "positive_cycles": int(frame.cycle_pnl.gt(0).sum()),
                "negative_cycles": int(frame.cycle_pnl.lt(0).sum()),
                "cycle_pnl_total": float(frame.cycle_pnl.sum()),
                "best_cycle_pnl": float(frame.cycle_pnl.max()),
                "worst_cycle_pnl": float(frame.cycle_pnl.min()),
                "largest_abs_cycle_share": float(abs_pnl.max() / abs_total) if abs_total else np.nan,
                "top2_abs_cycle_share": float(abs_pnl.nlargest(2).sum() / abs_total) if abs_total else np.nan,
            }
        )
    concentration = pd.DataFrame(concentration_rows)

    full = summary[summary.segment.eq("full")][
        ["candidate", "scope", "ann_return", "sharpe_repo", "max_dd"]
    ]
    corrected_exposure = exposure[~exposure.candidate.str.contains("legacy")].copy()
    paired_rows: list[dict[str, object]] = []
    for (scope, threshold), frame in corrected_exposure.groupby(["scope", "iv_threshold"]):
        if set(frame.mom120_gate.astype(str)) != {"False", "True"}:
            continue
        no_name = frame.loc[frame.mom120_gate.astype(str).eq("False"), "candidate"].iloc[0]
        yes_name = frame.loc[frame.mom120_gate.astype(str).eq("True"), "candidate"].iloc[0]
        no = full[full.candidate.eq(no_name)].iloc[0]
        yes = full[full.candidate.eq(yes_name)].iloc[0]
        no_exp = frame[frame.candidate.eq(no_name)].iloc[0]
        yes_exp = frame[frame.candidate.eq(yes_name)].iloc[0]
        paired_rows.append(
            {
                "scope": scope,
                "iv_threshold": threshold,
                "ann_return_with_minus_without_mom120": yes.ann_return - no.ann_return,
                "sharpe_with_minus_without_mom120": yes.sharpe_repo - no.sharpe_repo,
                "max_dd_with_minus_without_mom120": yes.max_dd - no.max_dd,
                "cycles_without_mom120": no_exp.cycles,
                "cycles_with_mom120": yes_exp.cycles,
                "short_put_share_without_mom120": no_exp.short_put_share,
                "short_put_share_with_mom120": yes_exp.short_put_share,
            }
        )
    paired = pd.DataFrame(paired_rows)

    annual.to_csv(RUN / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    concentration.to_csv(RUN / "cycle_concentration.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "mom120_paired_effect.csv", index=False, encoding="utf-8-sig")

    print("REAL PAIRED EFFECT")
    print(paired[paired.scope.eq("real")].to_string(index=False))
    print("\nREAL CYCLE CONCENTRATION")
    print(concentration[concentration.scope.eq("real")].to_string(index=False))


if __name__ == "__main__":
    main()
