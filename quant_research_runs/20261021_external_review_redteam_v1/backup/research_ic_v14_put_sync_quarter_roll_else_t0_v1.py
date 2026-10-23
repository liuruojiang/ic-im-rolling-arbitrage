"""Test syncing IC long-Put maintenance with actual quarterly futures rolls."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_v13_full_short95_profit_restrike_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_ic_v1_4_r1_current_joint_ic_core_and_momentum_long_put_"
    "monthly_maintenance_sync_put_with_ic_quarterly_roll_else_t0"
)
SPEC = ROOT / "docs" / "ic_v14_put_sync_quarter_roll_else_t0_v1_spec.md"
REFERENCE = (
    ROOT
    / "quant_param_scan_runs"
    / "20260917_ic_im_ic_v1_3_to_v1_4_final_joint_upgrade_gate_redteam_v3"
    / "daily_outputs"
    / "daily.csv.gz"
)
DECAY = 0.50
IV = 0.375
VARIANTS = {"final_joint": (True, 3.0)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hybrid_reset_dates(
    base_dates: pd.Series, roll_dates: set[pd.Timestamp], original_t0: set[pd.Timestamp]
) -> tuple[set[pd.Timestamp], pd.DataFrame]:
    t0_dates = sorted(pd.Timestamp(x) for x in original_t0)
    mapped: dict[pd.Timestamp, pd.Timestamp] = {}
    rows: list[dict[str, object]] = []
    for roll in sorted(pd.Timestamp(x) for x in roll_dates):
        following = [t0 for t0 in t0_dates if t0 >= roll]
        if not following:
            raise RuntimeError(f"No following Put T0 for IC roll {roll.date()}")
        t0 = following[0]
        gap = int((t0 - roll).days)
        if gap < 0 or gap > 10:
            raise RuntimeError(f"IC roll does not map to nearby Put T0: {roll.date()} -> {t0.date()}")
        if t0 in mapped:
            raise RuntimeError(f"Duplicate quarterly mapping to Put T0 {t0.date()}")
        mapped[t0] = roll
    resets: set[pd.Timestamp] = set()
    for t0 in t0_dates:
        execution = mapped.get(t0, t0)
        resets.add(execution)
        rows.append(
            {
                "monthly_t0": t0,
                "ic_roll_event": t0 in mapped,
                "execution_date": execution,
                "calendar_gap_days": int((t0 - execution).days),
            }
        )
    if len(resets) != len(t0_dates):
        raise RuntimeError("Duplicate hybrid Put reset date")
    if set(mapped.values()) != set(roll_dates):
        raise RuntimeError("Not every IC roll event was mapped")
    return resets, pd.DataFrame(rows)


def metric_row(summary: pd.DataFrame, candidate: str, segment: str) -> pd.Series:
    hit = summary[summary.candidate.eq(candidate) & summary.segment.eq(segment)]
    if len(hit) != 1:
        raise RuntimeError(f"Missing metric row {candidate} {segment}")
    return hit.iloc[0]


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()
    frames, _, _, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    forced_owner = prior.sleeve.ic_put.v1.put_engine.v19.v18.v13.v6
    original_forced = forced_owner.forced_roll_dates
    original_t0 = set(pd.Timestamp(x) for x in original_forced(frames["ic"]))
    all_roll_dates = set(pd.to_datetime(path.loc[path.roll_event.astype(bool), "date"]))
    hybrid_dates, base_mapping = hybrid_reset_dates(path.date, all_roll_dates, original_t0)
    if len(original_t0) != len(hybrid_dates):
        raise RuntimeError("Hybrid reset count changed")

    old_variants = prior.VARIANTS
    old_maturity_iv = prior.maturity.IV_THRESHOLD
    old_seller_iv = prior.seller_state.IV_THRESHOLD
    results: dict[tuple[str, str], tuple] = {}
    try:
        prior.VARIANTS = VARIANTS
        prior.maturity.IV_THRESHOLD = IV
        prior.seller_state.IV_THRESHOLD = IV

        def real_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY):
            return real_short(admission, DECAY, maturity, option_one_way)

        def model_decay(admission, _ignored, maturity="m1", option_one_way=prior.OPTION_ONE_WAY):
            return model_short(admission, DECAY, maturity, option_one_way)

        candidates = {"current_T0": original_t0, "sync_quarter_else_T0": hybrid_dates}
        for candidate, reset_dates in candidates.items():
            forced_owner.forced_roll_dates = lambda _ic, dates=reset_dates: dates
            for scope in ("real", "model"):
                results[(scope, candidate)] = prior.run_layer(
                    scope,
                    path,
                    futures,
                    market,
                    weights,
                    selected,
                    grid,
                    real_decay,
                    model_decay,
                    model_profit,
                    real_profit,
                )
    finally:
        forced_owner.forced_roll_dates = original_forced
        prior.VARIANTS = old_variants
        prior.maturity.IV_THRESHOLD = old_maturity_iv
        prior.seller_state.IV_THRESHOLD = old_seller_iv

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    calendar_parts: list[pd.DataFrame] = []
    event_rows: list[dict[str, object]] = []
    audits: dict[str, object] = {}
    reference = pd.read_csv(REFERENCE, parse_dates=["date"])
    for scope in ("real", "model"):
        audits[scope] = {}
        active_start = prior.REAL_START if scope == "real" else path.date.min()
        for candidate in ("current_T0", "sync_quarter_else_T0"):
            result = results[(scope, candidate)]
            daily = result[0].copy()
            if daily.variant.nunique() != 1 or daily.variant.iloc[0] != "final_joint":
                raise RuntimeError("Unexpected IC variant output")
            label = f"{scope}_{candidate}"
            daily["candidate"] = label
            daily["variant"] = candidate
            trades = result[1].copy()
            trades["candidate"] = label
            trades["path_variant"] = candidate
            daily_parts.append(daily)
            trade_parts.append(trades)
            mapping = base_mapping.copy()
            if candidate == "current_T0":
                mapping["ic_roll_event"] = False
                mapping["execution_date"] = mapping.monthly_t0
                mapping["calendar_gap_days"] = 0
            mapping = mapping[mapping.execution_date.ge(active_start)].copy()
            mapping["scope"] = scope
            mapping["candidate"] = label
            calendar_parts.append(mapping)
            variant_audit = result[5]["variants"]["final_joint"]
            event_rows.append({"scope": scope, "candidate": label, **variant_audit})
            audits[scope][candidate] = variant_audit

            if candidate == "current_T0":
                old = reference[reference.candidate.eq(f"{scope}_final_joint")].sort_values("date")
                now = daily.sort_values("date")
                if len(now) != len(old):
                    raise RuntimeError(f"{scope} baseline length mismatch")
                error = float(np.max(np.abs(now.return_net.to_numpy() - old.return_net.to_numpy())))
                if error > 1e-12:
                    raise RuntimeError(f"{scope} T0 baseline parity failed: {error}")
                audits[scope]["t0_reference_parity_error"] = error

    daily_all = pd.concat(daily_parts, ignore_index=True)
    trades_all = pd.concat(trade_parts, ignore_index=True, sort=False)
    calendars = pd.concat(calendar_parts, ignore_index=True, sort=False)
    events = pd.DataFrame(event_rows)
    summary, wide, unavailable = prior.router_base.summarize(daily_all)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]

    alignment_rows: list[dict[str, object]] = []
    for scope in ("real", "model"):
        label = f"{scope}_sync_quarter_else_T0"
        mapping = calendars[calendars.candidate.eq(label)].copy()
        expected_sync = set(pd.to_datetime(mapping.loc[mapping.ic_roll_event.astype(bool), "execution_date"]))
        expected_t0 = set(pd.to_datetime(mapping.loc[~mapping.ic_roll_event.astype(bool), "execution_date"]))
        monthly = trades_all[
            trades_all.candidate.eq(label) & trades_all.action.eq("close_roll_monthly")
        ].copy()
        monthly["actual_execution_date"] = pd.to_datetime(monthly.actual_execution_date)
        monthly["roll_request_date"] = pd.to_datetime(monthly.roll_request_date)
        allowed = expected_sync | expected_t0
        outside = monthly.loc[~monthly.roll_request_date.isin(allowed)]
        if len(outside):
            raise RuntimeError(f"{scope} monthly Put request outside hybrid calendar")
        delayed = monthly[monthly.actual_execution_date.ne(monthly.roll_request_date)]
        on_sync = monthly.roll_request_date.isin(expected_sync)
        on_t0 = monthly.roll_request_date.isin(expected_t0)
        active_rolls = set(pd.to_datetime(path.loc[
            path.roll_event.astype(bool) & path.date.ge(prior.REAL_START if scope == "real" else path.date.min()),
            "date",
        ]))
        if expected_sync != active_rolls:
            raise RuntimeError(f"{scope} hybrid Put calendar not aligned to IC rolls")
        alignment_rows.append(
            {
                "scope": scope,
                "ic_roll_events": len(active_rolls),
                "hybrid_calendar_sync_events": len(expected_sync),
                "monthly_put_roll_trade_requests_on_sync_dates": int(on_sync.sum()),
                "monthly_put_roll_trade_requests_on_t0_dates": int(on_t0.sum()),
                "delayed_monthly_put_roll_trades": len(delayed),
                "max_delay_trading_days": int(pd.to_numeric(delayed.delay_trading_days, errors="coerce").max()) if len(delayed) else 0,
            }
        )
    alignment = pd.DataFrame(alignment_rows)

    paired_rows: list[dict[str, object]] = []
    annual_rows: list[dict[str, object]] = []
    for scope in ("real", "model"):
        current = daily_all[daily_all.candidate.eq(f"{scope}_current_T0")].set_index("date")
        hybrid = daily_all[daily_all.candidate.eq(f"{scope}_sync_quarter_else_T0")].set_index("date")
        diff = hybrid.return_net - current.return_net
        paired_rows.append(
            {
                "scope": scope,
                "nonzero_return_days": int(diff.abs().gt(1e-15).sum()),
                "arithmetic_return_difference": float(diff.sum()),
                "largest_daily_advantage": float(diff.max()),
                "largest_daily_disadvantage": float(diff.min()),
                "largest_abs_difference_date": str(diff.abs().idxmax().date()),
            }
        )
        for year in sorted(current.index.year.unique()):
            c = current[current.index.year == year].return_net
            h = hybrid[hybrid.index.year == year].return_net
            annual_rows.append(
                {
                    "scope": scope,
                    "year": int(year),
                    "current_T0_return": float((1.0 + c).prod() - 1.0),
                    "sync_quarter_else_T0_return": float((1.0 + h).prod() - 1.0),
                    "difference_pp": 100.0 * float((1.0 + h).prod() - (1.0 + c).prod()),
                }
            )
    paired = pd.DataFrame(paired_rows)
    annual = pd.DataFrame(annual_rows)

    real_current_full = metric_row(summary, "real_current_T0", "full")
    real_hybrid_full = metric_row(summary, "real_sync_quarter_else_T0", "full")
    real_current_3y = metric_row(summary, "real_current_T0", "last_3y")
    real_hybrid_3y = metric_row(summary, "real_sync_quarter_else_T0", "last_3y")
    model_current_full = metric_row(summary, "model_current_T0", "full")
    model_hybrid_full = metric_row(summary, "model_sync_quarter_else_T0", "full")
    model_risk_ok = bool(
        float(model_hybrid_full.ann_return) >= float(model_current_full.ann_return) - 0.0025
        and abs(float(model_hybrid_full.max_dd)) <= abs(float(model_current_full.max_dd)) + 0.01
    )
    gate = bool(
        float(real_hybrid_full.ann_return) >= float(real_current_full.ann_return) - 0.0025
        and float(real_hybrid_3y.ann_return) >= float(real_current_3y.ann_return) - 0.0025
        and abs(float(real_hybrid_full.max_dd)) <= abs(float(real_current_full.max_dd)) + 0.01
        and model_risk_ok
        and alignment.delayed_monthly_put_roll_trades.eq(0).all()
    )
    decision = "watchlist" if gate else "keep_default"
    stability = "operational_sync_candidate" if gate else "reject"

    output = RUN / "daily_outputs"
    output.mkdir(exist_ok=False)
    daily_all.to_csv(output / "daily.csv.gz", index=False, compression="gzip")
    trades_all.to_csv(output / "trades.csv.gz", index=False, compression="gzip")
    calendars.to_csv(RUN / "reset_calendar.csv", index=False)
    events.to_csv(RUN / "event_counts.csv", index=False)
    alignment.to_csv(RUN / "alignment_audit.csv", index=False)
    paired.to_csv(RUN / "paired_differences.csv", index=False)
    annual.to_csv(RUN / "annual_attribution.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    meta.update(
        {
            "scan_type": "candidate_bundle",
            "parameter_group": "sync long Put maintenance with actual IC quarterly roll else T0",
            "baseline": {"candidate": ["model_current_T0", "real_current_T0"]},
            "candidate_grid": [
                {"candidate": "current_T0", "rule": "all monthly Put maintenance at T0 close"},
                {"candidate": "sync_quarter_else_T0", "rule": "actual IC T-3 roll_event close in quarter months; otherwise T0"},
            ],
            "data_snapshot": {
                "model": [str(path.date.min().date()), str(path.date.max().date())],
                "real": [str(prior.REAL_START.date()), str(path.date.max().date())],
                "timezone": "Asia/Shanghai",
                "real_source": "frozen listed IC and 510500 ETF option daily bars used by current joint replay",
            },
            "cost_model": {
                "IC_one_way_bp": 1,
                "510500_put_one_way_bp": 5,
                "futures_buffer_per_1x": 0.30,
                "cash_annual": 0.03,
                "execution": "official/model close",
                "slippage": "excluded beyond fixed costs",
            },
            "audits": audits,
            "alignment_audit": alignment.to_dict("records"),
            "unavailable_segments": unavailable,
            "decision": decision,
            "stability_label": stability,
            "source_hashes": {
                "script": sha256(Path(__file__)),
                "spec": sha256(SPEC),
                "reference_daily": sha256(REFERENCE),
            },
            "outputs": {
                **meta["outputs"],
                "daily": str(output / "daily.csv.gz"),
                "trades": str(output / "trades.csv.gz"),
                "reset_calendar": str(RUN / "reset_calendar.csv"),
                "event_counts": str(RUN / "event_counts.csv"),
                "alignment_audit": str(RUN / "alignment_audit.csv"),
                "paired_differences": str(RUN / "paired_differences.csv"),
                "annual": str(RUN / "annual_attribution.csv"),
            },
            "warnings": [
                "Historical counterfactual research; production rules, digest, ledger, and trading interfaces unchanged.",
                "Real listed sample starts 2022-09-19; 10Y and 5Y are unavailable.",
                "Model Put layer is theoretical, not executable listed history.",
                "Daily close excludes bid-ask, close impact, capacity, dynamic margin, tax, and forced liquidation.",
            ],
            "git_status_after": subprocess.run(
                ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip(),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    display = wide[
        [
            "candidate",
            "ann_return_full",
            "sharpe_repo_full",
            "max_dd_full",
            "ann_return_last_3y",
            "max_dd_last_3y",
            "ann_return_last_1y",
            "max_dd_last_1y",
        ]
    ]
    record = "\n".join(
        [
            "# IC v1.4-r1：季度期货换仓同步 Put、其余月份 T0",
            "",
            "> 研究回放；未修改正式规则、日报、账本或交易接口。",
            "",
            "## Run Metadata",
            "",
            f"- Decision: `{decision}`",
            f"- Stability: `{stability}`",
            "",
            "## Research Question",
            "",
            "IC季度T-3换仓日同步维护核心与动量Put；IC不换仓的月份，Put维持T0。",
            "",
            "## Implementation Anchor",
            "",
            "完整当前联合路径；current_T0逐日收益必须与保存的final_joint基准一致。",
            "",
            "## Data Snapshot",
            "",
            f"- Model: {path.date.min().date()} to {path.date.max().date()}.",
            f"- Real listed IC/510500 Put: {prior.REAL_START.date()} to {path.date.max().date()}.",
            "",
            "## Cost and Execution Assumptions",
            "",
            "IC单边1bp、Put单边5bp、close成交；不含bid/ask与冲击。",
            "",
            "## Runtime Override Plan",
            "",
            "只替换月度Put reset_dates；其他完整路径不变；不修改生产源。",
            "",
            "## Commands",
            "",
            "见 `command_log.txt`。",
            "",
            "## Output Files",
            "",
            "`scan_summary.csv`, `window_metrics.csv`, `alignment_audit.csv`, `daily_outputs/`。",
            "",
            "## Full-Sample Results",
            "",
            display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Alignment Audit",
            "",
            alignment.to_markdown(index=False),
            "",
            "## Stability Classification",
            "",
            f"`{stability}`。",
            "",
            "## Decision",
            "",
            f"`{decision}`；研究结果不自动修改正式规则。",
            "",
        ]
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(display.to_string(index=False))
    print(alignment.to_string(index=False))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
