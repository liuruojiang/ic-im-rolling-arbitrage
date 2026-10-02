"""Test syncing monthly long-Put maintenance with actual IM quarterly rolls."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v14_put_monthly_roll_timing_v1 as timing


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_im_v1_4_r1_current_joint_im_core_and_momentum_long_put_"
    "monthly_maintenance_sync_put_with_im_quarter_roll_else_t0"
)
SPEC = ROOT / "docs" / "im_v14_put_sync_quarter_roll_else_t0_v1_spec.md"
PRIOR = timing.PRIOR
PUT_FIELDS = timing.PUT_FIELDS
PUT_COST_MULTIPLIER = timing.PUT_COST_MULTIPLIER


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hybrid_reset_dates(base: pd.DataFrame) -> tuple[set[pd.Timestamp], pd.DataFrame]:
    calendar = pd.DatetimeIndex(pd.to_datetime(base.date)).sort_values().unique()
    positions = {pd.Timestamp(day): idx for idx, day in enumerate(calendar)}
    t0_dates = sorted(timing.portfolio.full.engine.monthly_dates(calendar))
    roll_dates = set(pd.to_datetime(base.loc[base.roll_event.astype(bool), "date"]))
    resets: set[pd.Timestamp] = set()
    rows: list[dict[str, object]] = []
    matched_roll_dates: set[pd.Timestamp] = set()
    for t0 in t0_dates:
        idx = positions[pd.Timestamp(t0)]
        previous = pd.Timestamp(calendar[idx - 1]) if idx else pd.NaT
        sync = pd.notna(previous) and previous in roll_dates
        execution = previous if sync else pd.Timestamp(t0)
        resets.add(execution)
        if sync:
            matched_roll_dates.add(previous)
        rows.append(
            {
                "monthly_t0": pd.Timestamp(t0),
                "previous_session": previous,
                "im_roll_event": bool(sync),
                "execution_date": execution,
            }
        )
    if matched_roll_dates != roll_dates:
        missing = sorted(roll_dates - matched_roll_dates)
        raise RuntimeError(f"IM roll events not mapped to following monthly T0: {missing}")
    if len(resets) != len(rows):
        raise RuntimeError("Duplicate hybrid monthly reset date")
    return resets, pd.DataFrame(rows)


def run_candidate(
    scope: str,
    base: pd.DataFrame,
    grid: pd.DataFrame,
    market: pd.DataFrame | None,
    routed: pd.DataFrame,
    call: pd.DataFrame,
    reset_dates: set[pd.Timestamp],
    candidate: str,
    real_engine,
    model_engine,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    route_dates = set(pd.to_datetime(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"]))
    imc_mask = routed.set_index("date").state.eq("imc")
    momentum, momentum_trades = timing.rerun_momentum_put(scope, base, reset_dates)
    core, core_trades = timing.rerun_core_put(
        scope,
        base,
        market,
        reset_dates,
        route_dates,
        imc_mask,
        real_engine,
        model_engine,
    )
    total = timing.joint.combine_puts(core, momentum, PUT_COST_MULTIPLIER)
    daily = timing.joint.compose_routed(base, routed, total, grid, call)
    daily["candidate"] = candidate
    daily["scope"] = scope
    daily["sessions_before"] = 0 if candidate.endswith("current_T0") else -1
    core_trades = core_trades.assign(candidate=candidate, scope=scope, sleeve="core")
    momentum_trades = momentum_trades.assign(candidate=candidate, scope=scope, sleeve="momentum")
    trades = pd.concat([core_trades, momentum_trades], ignore_index=True, sort=False)
    return daily, trades, {
        "core_trade_events": len(core_trades),
        "momentum_trade_events": len(momentum_trades),
        "monthly_roll_trades": int(trades.action.eq("close_roll_monthly").sum()),
        "profit_restrikes": int(core_trades.action.eq("close_profit_restrike").sum()),
        "route_exits": int(core_trades.action.eq("route_open_exit").sum()),
        "min_cash_weight": float(daily.cash_weight.min()),
    }


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    real_engine, model_engine, executed = timing.joint.component.profit_route_engines()
    router_fn, router_source = timing.joint.audited_router()
    weights = timing.portfolio.current_momentum_weights()
    prior = timing.read(PRIOR / "daily_outputs" / "daily.csv.gz")
    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    calendar_parts: list[pd.DataFrame] = []
    event_rows: list[dict[str, object]] = []
    audits: dict[str, object] = {}

    for scope in ("model", "real"):
        base, base_audit = timing.portfolio.rebuild_base(scope, weights)
        grid = timing.portfolio.half_grid(scope)
        market, router_base, options, _, futures, quarter_error = timing.joint.quarterly_router_inputs(scope, base)
        signal = timing.maturity.prepare_signal(router_base, options, "m1")
        routed, _, cycles = router_fn(router_base, options, futures, signal, 0.35, timing.common.FALLBACK, 0.60)
        routed["date"] = pd.to_datetime(routed.date)
        prefix = "r" if scope == "real" else "m"
        call, call_trades, _ = timing.maturity.call_inputs(
            scope,
            base.date,
            routed.state.eq("imc").astype(float),
            prefix + "syncput",
            RUN / "call_artifacts",
        )
        t0_dates, t0_map = timing.shifted_reset_dates(base.date, 0)
        hybrid_dates, hybrid_map = hybrid_reset_dates(base)
        candidates = [
            (f"{scope}_current_T0", t0_dates, t0_map.assign(im_roll_event=False)),
            (f"{scope}_sync_quarter_else_T0", hybrid_dates, hybrid_map),
        ]
        scope_audit: dict[str, object] = {
            **base_audit,
            "quarter_unit_gross_error": quarter_error,
            "im_roll_events": int(base.roll_event.astype(bool).sum()),
            "short_put_cycles": len(cycles),
            "call_trade_events": len(call_trades),
        }
        for candidate, resets, reset_map in candidates:
            reset_map = reset_map.copy()
            reset_map["scope"] = scope
            reset_map["candidate"] = candidate
            calendar_parts.append(reset_map)
            daily, trades, event = run_candidate(
                scope,
                base,
                grid,
                market,
                routed,
                call,
                resets,
                candidate,
                real_engine,
                model_engine,
            )
            daily_parts.append(daily)
            trade_parts.append(trades)
            event_rows.append({"candidate": candidate, "scope": scope, **event})
            if candidate.endswith("current_T0"):
                reference = prior[prior.candidate.eq(f"{scope}_joint")].sort_values("date")
                error = float(np.max(np.abs(daily.ret.to_numpy() - reference.ret.to_numpy())))
                if error > 1e-12:
                    raise RuntimeError(f"{scope} T0 baseline parity failed: {error}")
                scope_audit["t0_current_joint_return_parity_error"] = error
        audits[scope] = scope_audit

    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    calendars = pd.concat(calendar_parts, ignore_index=True, sort=False)
    events = pd.DataFrame(event_rows)
    summary, wide, unavailable = timing.build_metrics(daily)
    wide = wide.drop(columns=["sessions_before"])
    summary = summary.drop(columns=["sessions_before"])

    alignment_rows: list[dict[str, object]] = []
    for scope in ("model", "real"):
        candidate = f"{scope}_sync_quarter_else_T0"
        mapping = calendars[calendars.candidate.eq(candidate)].copy()
        monthly = trades[
            trades.candidate.eq(candidate) & trades.action.eq("close_roll_monthly")
        ].copy()
        matched = monthly.merge(
            mapping[["execution_date", "im_roll_event", "monthly_t0"]],
            left_on="actual_execution_date",
            right_on="execution_date",
            how="left",
            validate="many_to_one",
        )
        if matched.im_roll_event.isna().any():
            raise RuntimeError(f"{scope} monthly Put trade outside hybrid calendar")
        roll_days = set(pd.to_datetime(mapping.loc[mapping.im_roll_event.astype(bool), "execution_date"]))
        expected_roll_days = set(pd.to_datetime(daily.loc[(daily.scope.eq(scope)) & daily.candidate.eq(candidate) & daily.fixed_router_futures_units.notna(), "date"]))
        base_roll_days = set(pd.to_datetime(
            timing.portfolio.rebuild_base(scope, weights)[0].loc[
                lambda frame: frame.roll_event.astype(bool), "date"
            ]
        ))
        if roll_days != base_roll_days:
            raise RuntimeError(f"{scope} hybrid Put calendar not aligned to IM roll events")
        alignment_rows.append(
            {
                "scope": scope,
                "im_roll_events": len(base_roll_days),
                "hybrid_calendar_sync_events": len(roll_days),
                "monthly_put_roll_trades_on_sync_dates": int(matched.im_roll_event.astype(bool).sum()),
                "monthly_put_roll_trades_on_t0_dates": int((~matched.im_roll_event.astype(bool)).sum()),
            }
        )
    alignment = pd.DataFrame(alignment_rows)

    paired_rows: list[dict[str, object]] = []
    for scope in ("model", "real"):
        current = daily[daily.candidate.eq(f"{scope}_current_T0")].set_index("date")
        hybrid = daily[daily.candidate.eq(f"{scope}_sync_quarter_else_T0")].set_index("date")
        diff = hybrid.ret - current.ret
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
    paired = pd.DataFrame(paired_rows)

    output = RUN / "daily_outputs"
    output.mkdir(exist_ok=False)
    daily.to_csv(output / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(output / "trades.csv.gz", index=False, compression="gzip")
    calendars.to_csv(RUN / "reset_calendar.csv", index=False)
    events.to_csv(RUN / "event_counts.csv", index=False)
    alignment.to_csv(RUN / "alignment_audit.csv", index=False)
    paired.to_csv(RUN / "paired_differences.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_profit_engines.py").write_text(executed, encoding="utf-8")
    (RUN / "executed_router.py").write_text(router_source, encoding="utf-8")

    real = wide[wide.scope.eq("real")].set_index("candidate")
    current = real.loc["real_current_T0"]
    hybrid = real.loc["real_sync_quarter_else_T0"]
    gate = (
        float(hybrid.ann_return_full) >= float(current.ann_return_full) - 0.0025
        and float(hybrid.ann_return_last_3y) >= float(current.ann_return_last_3y) - 0.0025
        and float(hybrid.max_dd_full) >= float(current.max_dd_full) - 0.01
    )
    decision = "watchlist" if gate else "keep_default"
    stability = "operational_sync_candidate" if gate else "reject"

    meta.update(
        {
            "scan_type": "candidate_bundle",
            "parameter_group": "sync long Put monthly maintenance with actual IM quarterly roll else T0",
            "baseline": {"candidate": ["model_current_T0", "real_current_T0"]},
            "candidate_grid": [
                {"candidate": "current_T0", "rule": "all monthly Put maintenance at T0 close"},
                {"candidate": "sync_quarter_else_T0", "rule": "actual IM roll_event close when immediately before T0; otherwise T0"},
            ],
            "data_snapshot": {
                "model": ["2015-04-16", "2026-08-14"],
                "real": ["2022-07-22", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "real_source": "frozen CFFEX official IM/MO daily bars used by current joint replay",
            },
            "cost_model": {
                "futures_one_way": timing.joint.FUTURES_ONE_WAY,
                "futures_buffer_per_1x": 0.30,
                "cash_annual": 0.03,
                "put_side_cost_multiplier": PUT_COST_MULTIPLIER,
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
                "timing_harness": sha256(ROOT / "research_im_v14_put_monthly_roll_timing_v1.py"),
                "prior_joint_daily": sha256(PRIOR / "daily_outputs" / "daily.csv.gz"),
            },
            "outputs": {
                **meta["outputs"],
                "daily": str(output / "daily.csv.gz"),
                "trades": str(output / "trades.csv.gz"),
                "reset_calendar": str(RUN / "reset_calendar.csv"),
                "event_counts": str(RUN / "event_counts.csv"),
                "alignment_audit": str(RUN / "alignment_audit.csv"),
                "paired_differences": str(RUN / "paired_differences.csv"),
            },
            "warnings": [
                "Historical counterfactual research; production rules and ledgers unchanged.",
                "Real listed sample ends 2026-08-14; 10Y/5Y unavailable.",
                "Model layer is theoretical/proxy, not executable listed history.",
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
            "# IM v1.4-r1：季度期货换仓同步 Put、其余月份 T0",
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
            "IM季度换仓日同步维护Put；IM不换仓的月份，Put维持T0。",
            "",
            "## Implementation Anchor",
            "",
            "完整当前联合路径；T0逐日收益必须与保存基准一致。",
            "",
            "## Data Snapshot",
            "",
            "- Model: 2015-04-16 to 2026-08-14.",
            "- Real listed IM/MO: 2022-07-22 to 2026-08-14.",
            "",
            "## Cost and Execution Assumptions",
            "",
            "Put 5x既有单边费用；close成交；不含bid/ask与冲击。",
            "",
            "## Runtime Override Plan",
            "",
            "只替换月度reset_dates；其他路径不变；不修改生产源。",
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
            "## Window Results",
            "",
            "完整窗口见CSV。",
            "",
            "## Stability Classification",
            "",
            f"`{stability}`。",
            "",
            "## Decision",
            "",
            f"`{decision}`；研究结果不自动修改正式规则。",
            "",
            "## User-Facing Summary",
            "",
            "待严格校验后补充解释。",
            "",
        ]
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")


if __name__ == "__main__":
    main()
