"""Corrected T+1/T+2 robustness scan for IM v1.3 core-Put profit restrikes."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_core_put_profit_restrike_v1 as old_profit
import research_im_v13_core_put_profit_restrike_full_v1 as portfolio
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_current_counterfactual_corrected_full_portfolio_core_put_profit_restrike_robustness_2_5x_3x_3_5x_t1_t2"
SPEC = ROOT / "docs" / "im_v13_core_put_profit_restrike_robustness_v2_spec.md"
PREVIOUS = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_current_counterfactual_full_portfolio_core_put_profit_realization_multiple_baseline_2x_3x"
VARIANTS = {
    "baseline": (None, 1),
    "profit2p5x_t1": (2.5, 1),
    "profit3x_t1": (3.0, 1),
    "profit3p5x_t1": (3.5, 1),
    "profit3x_t2": (3.0, 2),
}
PUT_FIELDS = list(common.PUT_FIELDS)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def corrected_engines():
    """Repair real T+1 ordering and expose an explicit trading-session delay."""
    _, _, generated = old_profit.patched_engines()
    real_src, model_src = generated.split("\n\n", 1)

    real_src = real_src.replace(
        "market=None,profit_multiple=None):",
        "market=None,profit_multiple=None,profit_delay_sessions=1):",
        1,
    )
    real_src = real_src.replace(
        "entry_premium=np.nan;pending_profit=False",
        "entry_premium=np.nan;profit_pending=False;profit_wait=0;profit_signal_date=pd.NaT",
        1,
    )
    real_src = real_src.replace(
        "if target==0:reset_since=None\n        replace=active is not None and (reset_since is not None or active.actual_expiry<=day or profit_reset)",
        "if target==0:reset_since=None\n"
        "        if profit_pending and profit_wait>0:profit_wait-=1\n"
        "        profit_reset=bool(active is not None and profit_multiple is not None and profit_pending and profit_wait==0 and day not in reset_dates)\n"
        "        replace=active is not None and (reset_since is not None or active.actual_expiry<=day or profit_reset)",
        1,
    )
    real_src = real_src.replace(
        "        profit_reset=bool(old is not None and profit_multiple is not None and pending_profit and day not in reset_dates)\n        pending_profit=False\n",
        "",
        1,
    )
    real_src = real_src.replace(
        "actual_execution_date=day,execution_timing='close',action=action,monthly_reset=was_reset,",
        "actual_execution_date=day,execution_timing='close',action=action,monthly_reset=was_reset,"
        "profit_trigger_date=profit_signal_date if action=='close_profit_restrike' else pd.NaT,"
        "configured_profit_delay=profit_delay_sessions,",
        1,
    )
    real_src = real_src.replace(
        "            request_since=None;reset_since=None",
        "            if action in ('close_profit_restrike','close_roll_monthly','close_expiry_replace','close_exit','close_buy'):\n"
        "                profit_pending=False;profit_wait=0;profit_signal_date=pd.NaT\n"
        "            request_since=None;reset_since=None",
        1,
    )
    real_src = real_src.replace(
        "if active is None:skip_exit_pending=False;entry_premium=np.nan;pending_profit=False\n"
        "        elif profit_multiple is not None and action!='close_profit_restrike' and np.isfinite(entry_premium) and active.prior_settle>=entry_premium*profit_multiple:pending_profit=True",
        "if active is None:skip_exit_pending=False;entry_premium=np.nan;profit_pending=False;profit_wait=0;profit_signal_date=pd.NaT\n"
        "        elif profit_multiple is not None and not profit_pending and action!='close_profit_restrike' and np.isfinite(entry_premium) and active.prior_settle>=entry_premium*profit_multiple:\n"
        "            profit_pending=True;profit_wait=int(profit_delay_sessions);profit_signal_date=day",
        1,
    )

    model_src = model_src.replace(
        "anchor=None, profit_multiple=None):",
        "anchor=None, profit_multiple=None, profit_delay_sessions=1):",
        1,
    )
    model_src = model_src.replace(
        "entry_premium=np.nan;pending_profit=False",
        "entry_premium=np.nan;profit_pending=False;profit_wait=0;profit_signal_date=pd.NaT",
        1,
    )
    model_src = model_src.replace(
        "profit_reset=bool(active is not None and profit_multiple is not None and pending_profit and day not in reset_dates)\n        pending_profit=False",
        "if profit_pending and profit_wait>0:profit_wait-=1\n"
        "        profit_reset=bool(active is not None and profit_multiple is not None and profit_pending and profit_wait==0 and day not in reset_dates)",
        1,
    )
    model_src = model_src.replace(
        "scheduled_execution_date=day,actual_execution_date=day,execution_timing='close',\n                action=action,monthly_reset=reset,",
        "scheduled_execution_date=day,actual_execution_date=day,execution_timing='close',\n"
        "                action=action,monthly_reset=reset,"
        "profit_trigger_date=profit_signal_date if action=='close_profit_restrike' else pd.NaT,"
        "configured_profit_delay=profit_delay_sessions,",
        1,
    )
    model_src = model_src.replace(
        "            if old is not None and old is not active:\n                lives.append(dict(candidate=label,entry_date=old.entry_date,expiry=old.expiry,exit_date=day,exit_reason=action))",
        "            if old is not None and old is not active:\n"
        "                lives.append(dict(candidate=label,entry_date=old.entry_date,expiry=old.expiry,exit_date=day,exit_reason=action))\n"
        "            if action in ('close_profit_restrike','close_roll_monthly','close_expiry_replace','close_exit','close_buy'):\n"
        "                profit_pending=False;profit_wait=0;profit_signal_date=pd.NaT",
        1,
    )
    model_src = model_src.replace(
        "if active is None:skip_exit_pending=False;entry_premium=np.nan;pending_profit=False\n"
        "        elif profit_multiple is not None and action!='close_profit_restrike' and np.isfinite(entry_premium) and active.prior_mark>=entry_premium*profit_multiple:pending_profit=True",
        "if active is None:skip_exit_pending=False;entry_premium=np.nan;profit_pending=False;profit_wait=0;profit_signal_date=pd.NaT\n"
        "        elif profit_multiple is not None and not profit_pending and action!='close_profit_restrike' and np.isfinite(entry_premium) and active.prior_mark>=entry_premium*profit_multiple:\n"
        "            profit_pending=True;profit_wait=int(profit_delay_sessions);profit_signal_date=day",
        1,
    )

    if "pending_profit" in real_src or "pending_profit" in model_src:
        raise RuntimeError("Stale pending_profit hook remains")
    real_ns = dict(vars(portfolio.full.engine)); model_ns = dict(vars(portfolio.full.engine))
    exec(compile(real_src, str(Path(__file__)), "exec"), real_ns)
    exec(compile(model_src, str(Path(__file__)), "exec"), model_ns)
    return real_ns["run_real_profit_restrike"], model_ns["run_model_profit_restrike"], real_src + "\n\n" + model_src


def core_variant(scope: str, base: pd.DataFrame, real_engine, model_engine, multiple, delay, label):
    schedule = valuation.corrected_core_schedule(
        base.date, scope, pd.Series(True, index=pd.DatetimeIndex(base.date))
    )
    if scope == "model":
        market = portfolio.read(portfolio.full.BASE / "model_market.csv.gz")
        market = market[market.date.le(portfolio.END)].reset_index(drop=True)
        put, trades, _ = model_engine(
            market, schedule, "3m", 1.02, label,
            reset_dates=portfolio.full.engine.monthly_dates(base.date),
            profit_multiple=multiple, profit_delay_sessions=delay,
        )
    else:
        upstream = portfolio.read(portfolio.full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.le(portfolio.END)].reset_index(drop=True)
        active = portfolio.read(portfolio.full.BASE / "real_active.csv.gz")
        active = active[active.date.le(portfolio.END)].reset_index(drop=True)
        options = portfolio.full.engine.with_execution_prices(portfolio.read(
            portfolio.full.BASE / "real_options.csv.gz",
            ("date", "contract_month", "rule_expiry", "actual_expiry"),
        ))
        options = options[options.date.le(portfolio.END)].reset_index(drop=True)
        put, trades, _ = real_engine(
            upstream, options, active, schedule, "3m", 1.02, label,
            reset_dates=portfolio.full.engine.monthly_dates(base.date), market=None,
            profit_multiple=multiple, profit_delay_sessions=delay,
        )
    put = common.scale_put(put, scope)
    put[PUT_FIELDS] = 0.5 * put[PUT_FIELDS]
    return put, trades


def session_delay(dates: pd.Series, trigger: pd.Timestamp, execution: pd.Timestamp) -> int:
    index = pd.DatetimeIndex(dates)
    return int(((index > trigger) & (index <= execution)).sum())


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")

    weights = portfolio.current_momentum_weights()
    real_engine, model_engine, source = corrected_engines()
    old_daily = pd.read_csv(PREVIOUS / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    daily_parts, trade_parts, events, audits = [], [], [], {}

    for scope in ("model", "real"):
        official_parity = portfolio.legacy_full_parity(scope)
        base, base_audit = portfolio.rebuild_base(scope, weights)
        grid = portfolio.half_grid(scope)
        call = portfolio.read(portfolio.ARTIFACT / f"{scope}_fixed_call.csv.gz")
        call = call[call.date.le(portfolio.END)].reset_index(drop=True)
        mom_put, mom_trades = portfolio.momentum_put(scope, base)
        audits[scope] = {"official_full_component_parity": official_parity, **base_audit}
        trade_parts.append(mom_trades.assign(scope=scope, sleeve="momentum", candidate=f"{scope}_all"))

        for variant, (multiple, delay) in VARIANTS.items():
            label = f"{scope}_{variant}"
            core, trades = core_variant(scope, base, real_engine, model_engine, multiple, delay, label)
            total = core.copy()
            for field in PUT_FIELDS:
                total[field] = core[field].astype(float) + mom_put[field].astype(float)
            combined = portfolio.full.comp.compose(base, total, grid, call)
            combined["candidate"] = label; combined["scope"] = scope; combined["variant"] = variant
            if combined.cash_weight.min() < -1e-12 or not np.isfinite(combined.ret).all():
                raise RuntimeError(f"Invalid portfolio path {label}")
            daily_parts.append(combined)
            trade_parts.append(trades.assign(scope=scope, sleeve="core", candidate=label))

            profit_trades = trades[trades.action.eq("close_profit_restrike")].copy()
            observed_delays = []
            for trade in profit_trades.itertuples(index=False):
                if pd.isna(trade.profit_trigger_date):
                    raise RuntimeError(f"Missing profit trigger date {label}")
                observed = session_delay(base.date, pd.Timestamp(trade.profit_trigger_date), pd.Timestamp(trade.actual_execution_date))
                observed_delays.append(observed)
                if observed != delay:
                    raise RuntimeError(f"Wrong execution delay {label}: {observed} != {delay}")
            profit_days = set(profit_trades.actual_execution_date)
            monthly_days = set(trades.loc[trades.action.eq("close_roll_monthly"), "actual_execution_date"])
            if profit_days & monthly_days:
                raise RuntimeError(f"Monthly/profit duplicate {label}")
            events.append({
                "scope": scope, "variant": variant, "multiple": multiple, "configured_delay": delay,
                "profit_restrikes": len(profit_trades),
                "min_observed_delay": min(observed_delays) if observed_delays else np.nan,
                "max_observed_delay": max(observed_delays) if observed_delays else np.nan,
                "profit_execution_dates": "|".join(pd.to_datetime(profit_trades.actual_execution_date).dt.strftime("%Y-%m-%d")),
            })

            if variant == "baseline":
                reference = old_daily[old_daily.candidate.eq(f"{scope}_baseline")].sort_values("date")
                candidate = combined.sort_values("date")
                if not candidate.date.reset_index(drop=True).equals(reference.date.reset_index(drop=True)):
                    raise RuntimeError(f"Baseline date mismatch {scope}")
                error = float(np.max(np.abs(candidate.ret.to_numpy() - reference.ret.to_numpy())))
                if error > 1e-12:
                    raise RuntimeError(f"Corrected baseline parity failed {scope}: {error}")
                audits[scope]["prior_current_baseline_parity"] = error

    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True)
    event_frame = pd.DataFrame(events)
    summary, wide, unavailable = portfolio.metric_rows(daily)
    full_rows = summary[summary.segment.eq("full")].copy()

    comparison = []
    for scope in ("model", "real"):
        block = full_rows[full_rows.scope.eq(scope)].set_index("candidate")
        baseline = block.loc[f"{scope}_baseline"]
        for variant in VARIANTS:
            row = block.loc[f"{scope}_{variant}"]
            comparison.append({
                "scope": scope, "variant": variant,
                "ann_return_delta": float(row.ann_return - baseline.ann_return),
                "max_dd_delta": float(row.max_dd - baseline.max_dd),
                "ann_vol_delta": float(row.ann_vol - baseline.ann_vol),
            })
    comparison = pd.DataFrame(comparison)

    annual_rows = []
    for (candidate, year), group in daily.groupby(["candidate", daily.date.dt.year], sort=True):
        annual_rows.append({
            "candidate": candidate, "scope": group.scope.iloc[0], "variant": group.variant.iloc[0],
            "year": int(year), "annual_return": float((1 + group.ret).prod() - 1),
        })
    annual = pd.DataFrame(annual_rows)
    for scope in ("model", "real"):
        baseline_by_year = annual[annual.candidate.eq(f"{scope}_baseline")].set_index("year").annual_return
        mask = annual.scope.eq(scope)
        annual.loc[mask, "delta_vs_baseline"] = annual.loc[mask, "annual_return"] - annual.loc[mask, "year"].map(baseline_by_year)

    def positive(variant: str) -> bool:
        return bool((comparison.loc[comparison.variant.eq(variant), "ann_return_delta"] >= -1e-12).all())

    return_gate = all(positive(v) for v in ("profit2p5x_t1", "profit3x_t1", "profit3p5x_t1", "profit3x_t2"))
    drawdown_gate = bool((comparison.max_dd_delta >= -0.01 - 1e-12).all())
    robust = return_gate and drawdown_gate
    decision = "retain_profit3x_research_candidate_no_production_change" if robust else "reject_profit3x_not_robust_no_production_change"
    stability = "profit3x_neighborhood_and_delay_supported" if robust else "profit3x_fails_neighbor_or_delay_stress"

    output = RUN / "daily_outputs"; output.mkdir(exist_ok=False)
    daily.to_csv(output / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(output / "trades.csv", index=False)
    event_frame.to_csv(RUN / "event_counts.csv", index=False)
    comparison.to_csv(RUN / "paired_comparison.csv", index=False)
    annual.to_csv(RUN / "annual_attribution.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_corrected_profit_engines.py").write_text(source, encoding="utf-8")

    meta.update({
        "scan_type": "corrected_full_IM_v1_3_core_put_profit_restrike_threshold_and_delay_robustness",
        "baseline": {"candidate": ["model_baseline", "real_baseline"], "parity_source": str(PREVIOUS.relative_to(ROOT))},
        "candidate_grid": [
            {"variant": name, "profit_multiple": multiple, "execution_delay_sessions": delay}
            for name, (multiple, delay) in VARIANTS.items()
        ],
        "data_snapshot": {
            "model_start": str(daily.loc[daily.scope.eq("model"), "date"].min().date()),
            "model_end": str(daily.loc[daily.scope.eq("model"), "date"].max().date()),
            "real_start": str(daily.loc[daily.scope.eq("real"), "date"].min().date()),
            "real_end": str(daily.loc[daily.scope.eq("real"), "date"].max().date()),
        },
        "cost_model": {
            "same_as_previous_full_reintegration": True, "futures_buffer_per_1x": 0.30,
            "cash_annual": 0.03, "profit_execution": "T close signal; explicit T+N close execution",
        },
        "audit": audits, "event_counts": events,
        "robustness_gates": {"cross_layer_return_gate": return_gate, "drawdown_within_1pp_gate": drawdown_gate, "passed": robust},
        "invalid_predecessor": {
            "run": str(PREVIOUS.relative_to(ROOT)),
            "reason": "real engine evaluated replace before consuming pending profit signal, producing T+2 while reported as T+1",
        },
        "unavailable_segments": unavailable,
        "outputs": {
            **meta["outputs"], "daily": str(output / "daily.csv.gz"), "trades": str(output / "trades.csv"),
            "event_counts": str(RUN / "event_counts.csv"), "paired_comparison": str(RUN / "paired_comparison.csv"),
            "annual_attribution": str(RUN / "annual_attribution.csv"),
            "executed_engines": str(RUN / "executed_corrected_profit_engines.py"),
        },
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "previous_script": sha256(Path(portfolio.__file__))},
        "warnings": [
            "Current rules are counterfactual historical replay; live ledgers are unchanged.",
            "Model options are theoretical/proxy; real event count controls interpretation.",
            "No bid-ask impact, dynamic margin, forced liquidation, tax, capacity, or integer account sizing.",
        ],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.3 核心 Put 盈利兑现稳健性复测 v2\n\n"
        "上一版真实层实际T+2而误标T+1，本版已纠正并保留旧产物为失败审计。\n\n"
        "## Data\n\n真实期2022-07-22至2026-08-14；理论延展2015-04-16至2026-08-14，二者分开解释。\n\n"
        "## Full Results\n\n" + full_rows.to_markdown(index=False) +
        "\n\n## Paired Comparison\n\n" + comparison.to_markdown(index=False) +
        "\n\n## Events\n\n" + event_frame.to_markdown(index=False) +
        "\n\n## Annual Attribution\n\n" + annual.to_markdown(index=False) +
        f"\n\n## Decision\n\n{decision}\n\n## Stability\n\n{stability}\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full_rows.to_string(index=False))
    print(comparison.to_string(index=False))
    print(event_frame.to_string(index=False))
    print(json.dumps(meta["robustness_gates"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
