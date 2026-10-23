"""Reintegrate causal 2x/3x core-Put profit restrikes into a matched IM v1.3 portfolio.

Research only.  The frozen September 8 full-component snapshot is first
reproduced exactly, then all three candidates receive the same current-rule
counterfactual surrounding path.  Only the core-Put profit multiple differs.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_core_put_profit_restrike_v1 as profit
import research_im_v13_short_momentum_debounce_v1 as debounce
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as valuation
from im_put_maturity_valuation_tiers_v3 import metrics


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_im_v1_3_current_counterfactual_full_portfolio_core_put_profit_realization_multiple_baseline_2x_3x"
SPEC = ROOT / "docs" / "im_v13_core_put_profit_restrike_full_reintegration_v1_spec.md"
ARTIFACT = ROOT / "quant_param_scan_runs" / "20260908_im_mom120_put102_combined_v1"
OHLCV = (
    ROOT / "quant_param_scan_runs" / "20260912_ic_im_v13_r7_r2_current_f_base_width_scan"
    / "im_ohlcv_frozen_plus_fresh.csv.gz"
)
MULTIPLES = (None, 2.0, 3.0)
ONE_WAY_COST = 0.0001
PUT_FIELDS = tuple(common.PUT_FIELDS)
END = pd.Timestamp("2026-08-14")

sys.path.insert(0, str(ARTIFACT))
import run_combined as full  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path, dates: tuple[str, ...] = ("date",)) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=list(dates), low_memory=False)


def current_momentum_weights() -> pd.Series:
    authority = debounce.load_authority()
    raw = read(OHLCV).sort_values("date").set_index("date")
    selected = debounce.Variant("current_abs20_reentry_2d_p1", 1, 2, 0.0, 0.01)
    schedule = debounce.build_variant(authority, raw, selected)
    return schedule.set_index("date").execution_weight.astype(float)


def rebuild_base(scope: str, weights: pd.Series) -> tuple[pd.DataFrame, dict[str, float]]:
    source = read(ARTIFACT / f"{scope}_fixed_base.csv.gz")
    source = source[source.date.le(END)].reset_index(drop=True)
    old_units = source.base_units.astype(float)
    unit_gross = source.base_futures_gross.astype(float) / old_units

    # First prove the local reconstruction of the frozen base before changing
    # its momentum schedule.
    old_cost = ONE_WAY_COST * (
        old_units.diff().fillna(old_units).abs() + 2.0 * old_units * source.roll_event.astype(float)
    )
    gross_parity = float(np.max(np.abs(unit_gross * old_units - source.base_futures_gross)))
    cost_parity = float(np.max(np.abs(old_cost - source.base_futures_cost)))
    if gross_parity > 1e-12 or cost_parity > 1e-12:
        raise RuntimeError(f"{scope} frozen base reconstruction failed: {gross_parity}, {cost_parity}")

    momentum = source.date.map(weights)
    if momentum.isna().any():
        raise RuntimeError(f"{scope} current momentum schedule has missing dates")
    out = source.copy()
    out["momentum_weight"] = momentum.to_numpy(dtype=float)
    out["base_units"] = 0.5 + 0.5 * out.momentum_weight
    out["base_futures_gross"] = unit_gross * out.base_units
    out["base_futures_cost"] = ONE_WAY_COST * (
        out.base_units.diff().fillna(out.base_units).abs()
        + 2.0 * out.base_units * out.roll_event.astype(float)
    )
    if "long_carry" in out:
        out["long_carry"] = source.long_carry / old_units * out.base_units
    return out, {
        "frozen_base_gross_parity": gross_parity,
        "frozen_base_cost_parity": cost_parity,
        "changed_momentum_days": int((out.momentum_weight - source.momentum_weight).abs().gt(1e-12).sum()),
    }


def half_grid(scope: str) -> pd.DataFrame:
    grid = read(ARTIFACT / f"{scope}_fixed_grid.csv.gz")
    grid = grid[grid.date.le(END)].reset_index(drop=True)
    for column in (
        "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
        "overlay_gross_ret", "overlay_cost_rate", "grid_carry", "iv_grid_turnover",
        "overlay_open_buy_units", "overlay_open_sell_units", "overlay_iv_close_units",
    ):
        if column in grid:
            grid[column] = 0.5 * grid[column].astype(float)
    return grid


def legacy_full_parity(scope: str) -> float:
    base = read(ARTIFACT / f"{scope}_fixed_base.csv.gz")
    grid = read(ARTIFACT / f"{scope}_fixed_grid.csv.gz")
    call = read(ARTIFACT / f"{scope}_fixed_call.csv.gz")
    core = read(ARTIFACT / f"{scope}_combined_core_put.csv.gz")
    momentum = read(ARTIFACT / f"{scope}_combined_mom_put.csv.gz")
    total = core.copy()
    for column in PUT_FIELDS:
        total[column] = core[column].astype(float) + momentum[column].astype(float)
    replay = full.comp.compose(base, total, grid, call)
    reference = read(ARTIFACT / f"{scope}_combined_daily.csv.gz")
    error = float(np.max(np.abs(replay.ret - reference.ret)))
    if error > 1e-12:
        raise RuntimeError(f"{scope} official full-component baseline parity failed: {error}")
    return error


def momentum_put(scope: str, base: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    template = read(ARTIFACT / f"{scope}_combined_mom_schedule.csv.gz", ("eval_date", "execution_date"))
    template = template[template.execution_date.le(END)].reset_index(drop=True)
    state = read(full.BASE / "valuation_state_through_last_required_eval.csv.gz").set_index("date")
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
        market = read(full.BASE / "model_market.csv.gz")
        market = market[market.date.le(END)].reset_index(drop=True)
        put, trades, _ = full.engine.run_model_monthly_close(
            market, schedule, "3m", 1.02, f"{scope}_current_momentum_put102",
            reset_dates=full.engine.monthly_dates(base.date),
        )
        scale = 0.25 / 4.0
    else:
        upstream = read(full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.le(END)].reset_index(drop=True)
        active = read(full.BASE / "real_active.csv.gz")
        active = active[active.date.le(END)].reset_index(drop=True)
        options = full.engine.with_execution_prices(read(
            full.BASE / "real_options.csv.gz", ("date", "contract_month", "rule_expiry", "actual_expiry")
        ))
        options = options[options.date.le(END)].reset_index(drop=True)
        put, trades, _ = full.engine.run_real_monthly_close(
            upstream, options, active, schedule, "3m", 1.02,
            f"{scope}_current_momentum_put102", reset_dates=full.engine.monthly_dates(base.date),
        )
        scale = 0.25 / 4.0
    fields = list(PUT_FIELDS)
    put[fields] = put[fields] * scale
    return put, trades


def core_puts(scope: str, base: pd.DataFrame, real_engine, model_engine):
    schedule = valuation.corrected_core_schedule(base.date, scope, pd.Series(True, index=pd.DatetimeIndex(base.date)))
    if scope == "model":
        market = read(full.BASE / "model_market.csv.gz")
        market = market[market.date.le(END)].reset_index(drop=True)
        inputs = (market, schedule, "3m", 1.02)
    else:
        upstream = read(full.BASE / "real_upstream.csv.gz")
        upstream = upstream[upstream.date.le(END)].reset_index(drop=True)
        active = read(full.BASE / "real_active.csv.gz")
        active = active[active.date.le(END)].reset_index(drop=True)
        options = full.engine.with_execution_prices(read(
            full.BASE / "real_options.csv.gz", ("date", "contract_month", "rule_expiry", "actual_expiry")
        ))
        options = options[options.date.le(END)].reset_index(drop=True)
        inputs = (upstream, options, active, schedule, "3m", 1.02)

    result = {}
    for multiple in MULTIPLES:
        tag = "baseline" if multiple is None else f"profit{int(multiple)}x"
        label = f"{scope}_{tag}_core_put102"
        if scope == "model":
            put, trades, _ = model_engine(
                *inputs, label, reset_dates=full.engine.monthly_dates(base.date), profit_multiple=multiple,
            )
        else:
            put, trades, _ = real_engine(
                *inputs, label, reset_dates=full.engine.monthly_dates(base.date), market=None,
                profit_multiple=multiple,
            )
        # common.scale_put is the full 1x IMC scale.  The v1.3 core sleeve is
        # fixed at 0.5x, so halve the economically normalized leg.
        put = common.scale_put(put, scope)
        fields = list(PUT_FIELDS)
        put[fields] = 0.5 * put[fields]
        result[tag] = (put, trades)
    return result


def metric_rows(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, str]]]:
    rows, wide_rows, unavailable = [], [], {}
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        wide = {"candidate": candidate}
        for segment, years in (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1)):
            start = group.date.min() if years is None else group.date.max() - pd.DateOffset(years=years)
            valid = years is None or group.date.min() <= start
            if valid:
                sample = group[group.date >= start]
                values = metrics(sample.ret)
            else:
                sample = group.iloc[:0]
                values = {key: "N/A" for key in ("ann_return", "ann_vol", "sharpe_repo", "max_dd")}
                unavailable.setdefault(candidate, {})[segment] = "history shorter than requested window"
            rows.append({
                "candidate": candidate, "scope": candidate.split("_", 1)[0], "segment": segment,
                "start": str(start.date()), "end": str(group.date.max().date()), "rows": len(sample), **values,
            })
            for key, value in values.items():
                wide[f"{key}_{segment}"] = value
        wide_rows.append(wide)
    return pd.DataFrame(rows), pd.DataFrame(wide_rows), unavailable


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a non-init run")

    weights = current_momentum_weights()
    real_engine, model_engine, executed = profit.patched_engines()
    audits, daily_parts, trade_parts, event_rows = {}, [], [], []

    for scope in ("model", "real"):
        official_error = legacy_full_parity(scope)
        base, base_audit = rebuild_base(scope, weights)
        grid = half_grid(scope)
        call = read(ARTIFACT / f"{scope}_fixed_call.csv.gz")
        call = call[call.date.le(END)].reset_index(drop=True)
        mom_put, mom_trades = momentum_put(scope, base)
        cores = core_puts(scope, base, real_engine, model_engine)
        audits[scope] = {
            "official_full_component_parity": official_error,
            **base_audit,
            "grid_max_units": float(grid.overlay_held_eod.max()),
            "momentum_put_trade_events": int(len(mom_trades)),
        }
        trade_parts.append(mom_trades.assign(scope=scope, sleeve="momentum", candidate=f"{scope}_all"))

        for tag, (core, trades) in cores.items():
            total = core.copy()
            for column in PUT_FIELDS:
                total[column] = core[column].astype(float) + mom_put[column].astype(float)
            portfolio = full.comp.compose(base, total, grid, call)
            portfolio["candidate"] = f"{scope}_{tag}"
            portfolio["scope"] = scope
            portfolio["variant"] = tag
            if portfolio.cash_weight.min() < -1e-12:
                raise RuntimeError(f"{scope} {tag} has negative cash")
            if not np.isfinite(portfolio.ret).all() or (1 + portfolio.ret).le(0).any():
                raise RuntimeError(f"{scope} {tag} has invalid returns")
            daily_parts.append(portfolio)
            trade_parts.append(trades.assign(scope=scope, sleeve="core", candidate=f"{scope}_{tag}"))

            if len(trades):
                if not trades.signal_eval_date.lt(trades.actual_execution_date).all():
                    raise RuntimeError(f"{scope} {tag} has non-causal core Put trades")
                profit_days = set(trades.loc[trades.action.eq("close_profit_restrike"), "actual_execution_date"])
                monthly_days = set(trades.loc[trades.action.eq("close_roll_monthly"), "actual_execution_date"])
                if profit_days & monthly_days:
                    raise RuntimeError(f"{scope} {tag} duplicates profit and monthly maintenance")
            event_rows.append({
                "scope": scope, "variant": tag, "core_trade_events": int(len(trades)),
                "profit_restrikes": int(trades.action.eq("close_profit_restrike").sum()),
                "monthly_rolls": int(trades.action.eq("close_roll_monthly").sum()),
                "exit_events": int(trades.action.eq("close_exit").sum()),
            })

    daily = pd.concat(daily_parts, ignore_index=True)
    trades = pd.concat(trade_parts, ignore_index=True)
    summary, wide, unavailable = metric_rows(daily)
    events = pd.DataFrame(event_rows)
    output = RUN / "daily_outputs"
    output.mkdir(exist_ok=False)
    daily.to_csv(output / "daily.csv.gz", index=False, compression="gzip")
    trades.to_csv(output / "trades.csv", index=False)
    events.to_csv(RUN / "event_counts.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_profit_engines.py").write_text(executed, encoding="utf-8")

    full_rows = summary[summary.segment.eq("full")].copy()
    meta.update({
        "scan_type": "matched_full_IM_v1_3_current_rule_counterfactual_core_put_profit_restrike",
        "baseline": {"candidate": ["model_baseline", "real_baseline"], "definition": "same current counterfactual portfolio without early core-Put profit realization"},
        "candidate_grid": [{"profit_multiple": item} for item in ("none", 2, 3)],
        "data_snapshot": {
            "model_start": str(daily.loc[daily.scope.eq("model"), "date"].min().date()),
            "model_end": str(daily.loc[daily.scope.eq("model"), "date"].max().date()),
            "real_start": str(daily.loc[daily.scope.eq("real"), "date"].min().date()),
            "real_end": str(daily.loc[daily.scope.eq("real"), "date"].max().date()),
            "artifact": str(ARTIFACT.relative_to(ROOT)), "ohlcv": str(OHLCV.relative_to(ROOT)),
        },
        "cost_model": {
            "futures_one_way": ONE_WAY_COST, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03,
            "grid": "1.6/2.0, 0.5x; existing gross/cost path scaled before portfolio compounding",
            "core_put": "102%, about 3m, existing side cost, 0.5x core sleeve",
            "momentum_put": "102%, original negative-MOM120 quantity rule, current momentum sleeve weight",
            "call": "fixed validated D10 raw IV26 path; no short-Put route in this scan",
        },
        "audits": audits, "event_counts": event_rows, "unavailable_segments": unavailable,
        "source_hashes": {
            "script": sha256(Path(__file__)), "spec": sha256(SPEC), "ohlcv": sha256(OHLCV),
            "profit_engine_source": sha256(ROOT / "research_im_core_put_profit_restrike_v1.py"),
            "early_valuation_source": sha256(ROOT / "research_imc_current_core_put_short95_earlyvaluation_v4.py"),
        },
        "outputs": {
            **meta["outputs"], "daily": str(output / "daily.csv.gz"), "trades": str(output / "trades.csv"),
            "event_counts": str(RUN / "event_counts.csv"), "executed_profit_engines": str(RUN / "executed_profit_engines.py"),
        },
        "warnings": [
            "Current rules are replayed counterfactually; live effective dates and ledgers are not rewritten.",
            "The model layer is theoretical/proxy and is not executable listed history.",
            "The validated historical quarterly-chain and fixed Call components are held constant; no bid-ask impact, dynamic margin, forced liquidation, tax, capacity, or integer portfolio sizing is replayed.",
        ],
        "decision": "research_only_pending_interpretation", "stability_label": "full_reintegration_pending_review",
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.3 核心 Put 2x/3x 盈利兑现完整组合回放\n\n"
        "## Data\n\n理论延展与真实 IM/MO 分开；当前规则按历史反事实重放，2015 早期估值使用认证恢复。\n\n"
        "## Results\n\n" + full_rows.to_markdown(index=False) +
        "\n\n## Events\n\n" + events.to_markdown(index=False) +
        "\n\n## Verification\n\n" + json.dumps(audits, ensure_ascii=False, indent=2) +
        "\n\n## Decision\n\nresearch_only_pending_interpretation\n\n"
        "## Stability\n\nfull_reintegration_pending_review\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full_rows.to_string(index=False))
    print(events.to_string(index=False))
    print(json.dumps(audits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
