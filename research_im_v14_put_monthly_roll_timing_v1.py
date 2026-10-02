"""Scan actual IM long-Put monthly reset close across T0/T-1/T-2/T-3.

Research only.  This reuses the current full IM joint replay and changes only
the monthly reset calendar for both core and momentum long Put sleeves.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_im_v13_full_short95_profit3x_joint_v1 as joint
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
import research_imc_short95_maturity_corrected_v1 as maturity
from im_put_maturity_valuation_tiers_v3 import metrics


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / (
    "20260917_ic_im_im_v1_4_r1_current_joint_im_core_and_momentum_long_put_"
    "monthly_maintenance_option_monthly_roll_execution_t0_t1_t2_t3"
)
SPEC = ROOT / "docs" / "im_v14_put_monthly_roll_timing_v1_spec.md"
PRIOR = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_full_short95_profit3x_joint_redteam_v2"
CANDIDATES = (0, 1, 2, 3)
PUT_COST_MULTIPLIER = joint.PUT_COST_MULTIPLIER
PUT_FIELDS = list(joint.PUT_FIELDS)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path, dates: tuple[str, ...] = ("date",)) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=list(dates), low_memory=False)


def shifted_reset_dates(dates: pd.Series, sessions_before: int) -> tuple[set[pd.Timestamp], pd.DataFrame]:
    calendar = pd.DatetimeIndex(pd.to_datetime(dates)).sort_values().unique()
    t0_dates = sorted(portfolio.full.engine.monthly_dates(calendar))
    positions = {pd.Timestamp(day): idx for idx, day in enumerate(calendar)}
    rows: list[dict[str, object]] = []
    shifted: set[pd.Timestamp] = set()
    for t0 in t0_dates:
        idx = positions[pd.Timestamp(t0)]
        if idx < sessions_before:
            continue
        execution = pd.Timestamp(calendar[idx - sessions_before])
        shifted.add(execution)
        rows.append(
            {
                "monthly_t0": pd.Timestamp(t0),
                "sessions_before": sessions_before,
                "execution_date": execution,
            }
        )
    if len(shifted) != len(rows) or not shifted.issubset(set(calendar)):
        raise RuntimeError("Invalid shifted monthly reset calendar")
    return shifted, pd.DataFrame(rows)


def rerun_momentum_put(
    scope: str,
    base: pd.DataFrame,
    reset_dates: set[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    template = read(
        portfolio.ARTIFACT / f"{scope}_combined_mom_schedule.csv.gz",
        ("eval_date", "execution_date"),
    )
    template = template[template.execution_date.le(portfolio.END)].reset_index(drop=True)
    state = read(portfolio.full.BASE / "valuation_state_through_last_required_eval.csv.gz").set_index("date")
    mom120 = template.eval_date.map(state.momentum_120)
    if mom120.isna().any():
        raise RuntimeError(f"{scope} MOM120 has missing dates")
    parent = np.where(mom120.lt(0), 3, 0)
    target = parent * 2.0 * base.momentum_weight.to_numpy(dtype=float) * 4.0
    if not np.allclose(target, np.rint(target)):
        raise RuntimeError(f"{scope} momentum Put target is not integral")
    schedule = template.copy()
    schedule["binary_target_qty"] = np.rint(target).astype(int)
    schedule["three_tier_target_qty"] = schedule.binary_target_qty
    schedule["momentum_120"] = mom120.to_numpy(dtype=float)
    schedule["mom120_active"] = mom120.lt(0).to_numpy(dtype=bool)
    schedule["put_buy_allowed"] = True

    if scope == "model":
        market = read(portfolio.full.BASE / "model_market.csv.gz")
        market = market[market.date.le(portfolio.END)].reset_index(drop=True)
        put, trades, _ = portfolio.full.engine.run_model_monthly_close(
            market,
            schedule,
            "3m",
            1.02,
            f"{scope}_momentum_put102",
            reset_dates=reset_dates,
        )
        scale = 0.25 / 4.0
    else:
        upstream = read(portfolio.full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.le(portfolio.END)].reset_index(drop=True)
        active = read(portfolio.full.BASE / "real_active.csv.gz")
        active = active[active.date.le(portfolio.END)].reset_index(drop=True)
        options = portfolio.full.engine.with_execution_prices(
            read(
                portfolio.full.BASE / "real_options.csv.gz",
                ("date", "contract_month", "rule_expiry", "actual_expiry"),
            )
        )
        options = options[options.date.le(portfolio.END)].reset_index(drop=True)
        put, trades, _ = portfolio.full.engine.run_real_monthly_close(
            upstream,
            options,
            active,
            schedule,
            "3m",
            1.02,
            f"{scope}_momentum_put102",
            reset_dates=reset_dates,
        )
        scale = 0.25 / 4.0
    put[PUT_FIELDS] = put[PUT_FIELDS] * scale
    return put, trades


def rerun_core_put(
    scope: str,
    base: pd.DataFrame,
    market: pd.DataFrame | None,
    reset_dates: set[pd.Timestamp],
    route_dates: set[pd.Timestamp],
    imc_mask: pd.Series,
    real_engine,
    model_engine,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    schedule = valuation.corrected_core_schedule(base.date, scope, imc_mask)
    label = f"{scope}_joint_core_put102"
    kwargs = {
        "reset_dates": reset_dates,
        "profit_multiple": 3.0,
        "profit_delay_sessions": 1,
        "open_exit_dates": route_dates,
    }
    if scope == "real":
        upstream = read(portfolio.full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.isin(base.date)].reset_index(drop=True)
        active = read(portfolio.full.BASE / "real_active.csv.gz")
        active = active[active.date.isin(base.date)].reset_index(drop=True)
        options = portfolio.full.engine.with_execution_prices(
            read(
                portfolio.full.BASE / "real_options.csv.gz",
                ("date", "contract_month", "rule_expiry", "actual_expiry"),
            )
        )
        options = options[options.date.isin(base.date)].reset_index(drop=True)
        put, trades, _ = real_engine(
            upstream,
            options,
            active,
            schedule,
            "3m",
            1.02,
            label,
            market=None,
            **kwargs,
        )
    else:
        if market is None:
            raise RuntimeError("Missing model market")
        put, trades, _ = model_engine(market, schedule, "3m", 1.02, label, **kwargs)
    put = common.scale_put(put, scope)
    put[PUT_FIELDS] = 0.5 * put[PUT_FIELDS]
    put["put_cost_rate"] = put.put_cost_rate.astype(float) * PUT_COST_MULTIPLIER
    return put, trades


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, str]]]:
    rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    unavailable: dict[str, dict[str, str]] = {}
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        wide: dict[str, object] = {
            "candidate": candidate,
            "scope": group.scope.iloc[0],
            "sessions_before": int(group.sessions_before.iloc[0]),
        }
        for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
            requested = group.date.min() if years is None else group.date.max() - pd.DateOffset(years=years)
            available = years is None or group.date.min() <= requested
            if available:
                sample = group[group.date.ge(requested)]
                values = metrics(sample.ret)
            else:
                sample = group.iloc[:0]
                values = {key: "N/A" for key in ("ann_return", "ann_vol", "sharpe_repo", "max_dd")}
                unavailable.setdefault(candidate, {})[segment] = (
                    f"Formal {group.scope.iloc[0]} history starts {group.date.min().date()}, fewer than {years} years."
                )
            rows.append(
                {
                    "candidate": candidate,
                    "segment": segment,
                    "start": str(requested.date()),
                    "end": str(group.date.max().date()),
                    "rows": len(sample),
                    "scope": group.scope.iloc[0],
                    "sessions_before": int(group.sessions_before.iloc[0]),
                    **values,
                }
            )
            for key, value in values.items():
                wide[f"{key}_{segment}"] = value
        wide_rows.append(wide)
    return pd.DataFrame(rows), pd.DataFrame(wide_rows), unavailable


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a non-init run")

    real_engine, model_engine, executed = joint.component.profit_route_engines()
    router_fn, router_source = joint.audited_router()
    weights = portfolio.current_momentum_weights()
    prior = read(PRIOR / "daily_outputs" / "daily.csv.gz")
    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    calendar_parts: list[pd.DataFrame] = []
    event_rows: list[dict[str, object]] = []
    audits: dict[str, object] = {}

    for scope in ("model", "real"):
        base, base_audit = portfolio.rebuild_base(scope, weights)
        grid = portfolio.half_grid(scope)
        market, router_base, options, _, futures, quarter_error = joint.quarterly_router_inputs(scope, base)
        signal = maturity.prepare_signal(router_base, options, "m1")
        routed, _, cycles = router_fn(router_base, options, futures, signal, 0.35, common.FALLBACK, 0.60)
        routed["date"] = pd.to_datetime(routed.date)
        route_dates = set(pd.to_datetime(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"]))
        imc_mask = routed.set_index("date").state.eq("imc")
        prefix = "r" if scope == "real" else "m"
        call, call_trades, _ = maturity.call_inputs(
            scope,
            base.date,
            routed.state.eq("imc").astype(float),
            prefix + "timing",
            RUN / "call_artifacts",
        )
        scope_audit: dict[str, object] = {
            **base_audit,
            "quarter_unit_gross_error": quarter_error,
            "short_put_cycles": len(cycles),
            "call_trade_events": len(call_trades),
        }

        for sessions_before in CANDIDATES:
            reset_dates, reset_map = shifted_reset_dates(base.date, sessions_before)
            tag = f"T{sessions_before}" if sessions_before == 0 else f"T-{sessions_before}"
            candidate = f"{scope}_{tag}"
            reset_map["scope"] = scope
            reset_map["candidate"] = candidate
            calendar_parts.append(reset_map)

            momentum, momentum_trades = rerun_momentum_put(scope, base, reset_dates)
            core, core_trades = rerun_core_put(
                scope,
                base,
                market,
                reset_dates,
                route_dates,
                imc_mask,
                real_engine,
                model_engine,
            )
            total = joint.combine_puts(core, momentum, PUT_COST_MULTIPLIER)
            daily = joint.compose_routed(base, routed, total, grid, call)
            daily["candidate"] = candidate
            daily["scope"] = scope
            daily["sessions_before"] = sessions_before
            daily_parts.append(daily)

            core_trades = core_trades.assign(candidate=candidate, scope=scope, sleeve="core")
            momentum_trades = momentum_trades.assign(candidate=candidate, scope=scope, sleeve="momentum")
            trade_parts.extend([core_trades, momentum_trades])
            combined_trades = pd.concat([core_trades, momentum_trades], ignore_index=True)
            monthly = combined_trades[combined_trades.action.eq("close_roll_monthly")]
            duplicates = combined_trades.duplicated(["sleeve", "actual_execution_date", "action"]).sum()
            if duplicates:
                raise RuntimeError(f"Duplicate Put actions for {candidate}: {duplicates}")
            event_rows.append(
                {
                    "candidate": candidate,
                    "scope": scope,
                    "sessions_before": sessions_before,
                    "calendar_events": len(reset_dates),
                    "monthly_roll_trades": len(monthly),
                    "core_trade_events": len(core_trades),
                    "momentum_trade_events": len(momentum_trades),
                    "profit_restrikes": int(core_trades.action.eq("close_profit_restrike").sum()),
                    "route_exits": int(core_trades.action.eq("route_open_exit").sum()),
                    "min_cash_weight": float(daily.cash_weight.min()),
                }
            )

            if sessions_before == 0:
                reference = prior[prior.candidate.eq(f"{scope}_joint")].sort_values("date")
                if not daily.date.reset_index(drop=True).equals(reference.date.reset_index(drop=True)):
                    raise RuntimeError(f"{scope} T0/reference date mismatch")
                parity = float(np.max(np.abs(daily.ret.to_numpy() - reference.ret.to_numpy())))
                if parity > 1e-12:
                    raise RuntimeError(f"{scope} current T0 baseline parity failed: {parity}")
                scope_audit["t0_current_joint_return_parity_error"] = parity
        audits[scope] = scope_audit

    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    reset_calendar = pd.concat(calendar_parts, ignore_index=True)
    events = pd.DataFrame(event_rows)
    summary, wide, unavailable = build_metrics(daily)

    paired_rows: list[dict[str, object]] = []
    for scope in ("model", "real"):
        baseline = daily[daily.candidate.eq(f"{scope}_T0")].set_index("date")
        for sessions_before in CANDIDATES[1:]:
            candidate = f"{scope}_T-{sessions_before}"
            other = daily[daily.candidate.eq(candidate)].set_index("date")
            diff = other.ret - baseline.ret
            paired_rows.append(
                {
                    "candidate": candidate,
                    "scope": scope,
                    "sessions_before": sessions_before,
                    "nonzero_return_days": int(diff.abs().gt(1e-15).sum()),
                    "cumulative_arithmetic_return_difference": float(diff.sum()),
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
    reset_calendar.to_csv(RUN / "reset_calendar.csv", index=False)
    events.to_csv(RUN / "event_counts.csv", index=False)
    paired.to_csv(RUN / "paired_differences.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_profit_engines.py").write_text(executed, encoding="utf-8")
    (RUN / "executed_router.py").write_text(router_source, encoding="utf-8")

    real_wide = wide[wide.scope.eq("real")].set_index("sessions_before")
    baseline = real_wide.loc[0]
    eligible: list[int] = []
    for sessions_before in CANDIDATES[1:]:
        row = real_wide.loc[sessions_before]
        if (
            float(row.ann_return_full) >= float(baseline.ann_return_full) - 0.0025
            and float(row.ann_return_last_3y) >= float(baseline.ann_return_last_3y) - 0.0025
            and float(row.max_dd_full) >= float(baseline.max_dd_full) - 0.01
        ):
            eligible.append(sessions_before)
    decision = "watchlist" if eligible else "keep_default"
    stability = "narrow_stable" if len(eligible) == 1 else ("wide_stable" if len(eligible) > 1 else "reject")

    meta.update(
        {
            "scan_type": "single_parameter",
            "parameter_group": "IM long Put monthly actual execution sessions before T0",
            "baseline": {"candidate": ["model_T0", "real_T0"], "definition": "current monthly T0 close execution"},
            "candidate_grid": [
                {"candidate": "T0" if item == 0 else f"T-{item}", "sessions_before": item}
                for item in CANDIDATES
            ],
            "data_snapshot": {
                "model": [
                    str(daily.loc[daily.scope.eq("model"), "date"].min().date()),
                    str(daily.loc[daily.scope.eq("model"), "date"].max().date()),
                ],
                "real": [
                    str(daily.loc[daily.scope.eq("real"), "date"].min().date()),
                    str(daily.loc[daily.scope.eq("real"), "date"].max().date()),
                ],
                "timezone": "Asia/Shanghai",
                "real_source": "frozen CFFEX official IM/MO daily bars used by current joint replay",
                "model_source": "existing CSI1000 plus theoretical option model layer",
            },
            "cost_model": {
                "futures_one_way": joint.FUTURES_ONE_WAY,
                "futures_buffer_per_1x": 0.30,
                "cash_annual": 0.03,
                "put_side_cost_multiplier": PUT_COST_MULTIPLIER,
                "execution": "official/model close on actual T0/T-1/T-2/T-3 session",
                "slippage": "excluded beyond existing fixed costs",
            },
            "audits": audits,
            "unavailable_segments": unavailable,
            "eligible_watchlist_sessions_before": eligible,
            "decision": decision,
            "stability_label": stability,
            "source_hashes": {
                "script": sha256(Path(__file__)),
                "spec": sha256(SPEC),
                "prior_joint_daily": sha256(PRIOR / "daily_outputs" / "daily.csv.gz"),
            },
            "outputs": {
                **meta["outputs"],
                "daily": str(output / "daily.csv.gz"),
                "trades": str(output / "trades.csv.gz"),
                "reset_calendar": str(RUN / "reset_calendar.csv"),
                "event_counts": str(RUN / "event_counts.csv"),
                "paired_differences": str(RUN / "paired_differences.csv"),
            },
            "warnings": [
                "Current v1.4-r1 rules are counterfactually replayed over historical data; live ledgers are not rewritten.",
                "The real listed sample is short and ends 2026-08-14; 10Y/5Y are unavailable.",
                "The model layer is theoretical/proxy, not executable listed IM/MO history.",
                "Daily close is not bid/ask, close VWAP, market impact, or capacity evidence.",
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
    ].copy()
    record = "\n".join(
        [
            "# IM v1.4-r1 买 Put 月度实际展期日扫描",
            "",
            "> 研究回放；未修改正式规则、日报、账本或交易接口。",
            "",
            "## Run Metadata",
            "",
            f"- Spec: `{SPEC.relative_to(ROOT)}`",
            f"- Decision: `{decision}`",
            f"- Stability: `{stability}`",
            f"- Eligible watchlist offsets: `{eligible}`",
            "",
            "## Research Question",
            "",
            "只移动核心与动量买 Put 的月度实际换约收盘日，对比 T0/T-1/T-2/T-3。",
            "",
            "## Implementation Anchor",
            "",
            "- Current joint replay: `research_im_v13_full_short95_profit3x_joint_v1.py`",
            "- Current 102% core/momentum Put engine and 3x core profit reset are reused.",
            "- T0 return path must match the saved current joint artifact to 1e-12.",
            "",
            "## Data Snapshot",
            "",
            f"- Model: {daily.loc[daily.scope.eq('model'), 'date'].min().date()} to {daily.loc[daily.scope.eq('model'), 'date'].max().date()}.",
            f"- Real listed IM/MO: {daily.loc[daily.scope.eq('real'), 'date'].min().date()} to {daily.loc[daily.scope.eq('real'), 'date'].max().date()}.",
            "",
            "## Cost and Execution Assumptions",
            "",
            "- Put 5x existing side-cost stress; official/model close; no bid/ask or market impact.",
            "- Futures, grid, Call, short95 router, cash and margin-buffer assumptions are fixed.",
            "",
            "## Full-Sample Results",
            "",
            display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Window Results",
            "",
            "Full rows are in `scan_summary.csv`; wide comparison is in `window_metrics.csv`.",
            "",
            "## Stability Classification",
            "",
            f"`{stability}`. The automatic gate is only a screening aid; event concentration and model direction still require interpretation.",
            "",
            "## Decision",
            "",
            f"`{decision}`; research only, no source change.",
            "",
            "## User-Facing Summary",
            "",
            "See the final review after strict artifact validation.",
            "",
            "## Commands",
            "",
            "See `command_log.txt`.",
            "",
            "## Output Files",
            "",
            "`scan_summary.csv`, `window_metrics.csv`, `daily_outputs/`, `reset_calendar.csv`, `event_counts.csv`, `paired_differences.csv`.",
            "",
        ]
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -X utf8 research_im_v14_put_monthly_roll_timing_v1.py\n")


if __name__ == "__main__":
    main()
