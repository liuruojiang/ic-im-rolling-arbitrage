"""Correct IC 95%-Put IV selection and scan threshold x seller MOM120."""
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
    "20260917_ic_im_ic_v1_4_r1_current_joint_corrected_iv_ic_fixed_core_"
    "short95_put_router_corrected_m_1_95_iv_threshold_x_seller_mom120_gate"
)
SPEC = ROOT / "docs" / "ic_v14_corrected_iv_mom120_scan_v1_spec.md"
REFERENCE_RUN = ROOT / "quant_param_scan_runs" / "20260917_ic_im_ic_v1_3_to_v1_4_final_joint_upgrade_gate_redteam_v3"
IM_REFERENCE = ROOT / "quant_param_scan_runs" / "20260917_ic_im_im_v1_3_momentum_short95_full_joint_q3_redteam_v3"
THRESHOLDS = (0.15, 0.175, 0.20, 0.225, 0.25, 0.275, 0.30, 0.325, 0.35, 0.375, 0.40)
DECAY = 0.50
PUT_COST_MULTIPLIER = 5.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label(scope: str, threshold: float, mom120: bool) -> str:
    return f"{scope}_iv{int(round(threshold * 1000)):03d}_{'mom120' if mom120 else 'nomom'}"


def corrected_real_signal(
    active: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    ic_market: pd.DataFrame,
    expiries: pd.Series,
) -> pd.DataFrame:
    admission = prior.maturity.base.permission()
    snapshots = frames["snapshots"]
    chains = {pd.Timestamp(day): group for day, group in snapshots.groupby("date", sort=False)}
    histories = frames["histories"].set_index(["security_id", "date"])
    etf = frames["etf500"].set_index("date")
    market = ic_market.set_index("date")
    trade_dates = pd.DatetimeIndex(active.date)
    rows: list[dict[str, object]] = []
    engine = prior.sleeve.ic_put.v1.put_engine
    for i in range(1, len(active)):
        evaluation = pd.Timestamp(active.loc[i - 1, "date"])
        execution = pd.Timestamp(active.loc[i, "date"])
        etf_close = float(etf.loc[evaluation, "close"])
        chain = chains.get(evaluation, snapshots.iloc[:0])
        month = prior.maturity.real_entry_month(execution, chain, expiries, trade_dates, "m1")
        selected = prior.router_base.short.choose_real(chain, etf_close, month)
        row: dict[str, object] = {
            "eval_date": evaluation,
            "execution_date": execution,
            "maturity": "m1",
            "selected_contract_month": month,
            "execution_contract": "",
            "iv_contract": "",
            "iv": np.nan,
            "vendor_iv": np.nan,
            "admission": bool(admission.get(execution, False)),
            "execution_open_valid": False,
            "front_fallback_to_m1": False,
            "signal_moneyness": np.nan,
        }
        if selected is not None:
            security_id = str(selected.security_id)
            eval_quote = histories.loc[(security_id, evaluation)] if (security_id, evaluation) in histories.index else None
            execution_quote = histories.loc[(security_id, execution)] if (security_id, execution) in histories.index else None
            expiry = pd.Timestamp(expiries.loc[security_id])
            years = max((expiry - evaluation).days, 0) / 365.0
            iv = None
            if eval_quote is not None:
                iv = engine.implied_volatility(
                    float(eval_quote.close),
                    etf_close,
                    float(selected.strike),
                    float(market.loc[evaluation, "rate_close"]),
                    float(market.loc[evaluation, "dividend_close"]),
                    years,
                )
            valid = bool(
                execution_quote is not None
                and float(execution_quote.open) > 0
                and float(execution_quote.volume) > 0
            )
            row.update(
                {
                    "execution_contract": security_id,
                    "iv_contract": security_id,
                    "iv": float(iv) if iv is not None else np.nan,
                    "vendor_iv": float(selected.implied_volatility),
                    "execution_open_valid": valid,
                    "signal_moneyness": float(selected.strike) / etf_close,
                }
            )
        rows.append(row)
    out = pd.DataFrame(rows)
    if out.iv.isna().any() or not out.signal_moneyness.between(0.90, 1.00).all():
        raise RuntimeError("Corrected real IC IV signal is incomplete or not a 95% Put")
    return out


def prepare_scope(
    scope: str,
    path: pd.DataFrame,
    futures: pd.DataFrame,
    weights: pd.Series,
    selected: pd.DataFrame,
    grid_all: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    option_market: pd.DataFrame,
    model_market: pd.DataFrame,
    corrected_real: pd.DataFrame,
) -> dict[str, object]:
    qic = frames["ic"].copy()
    for column in ("contract", "settle", "close", "volume", "open_interest", "ic_gross_ret", "cost_rate", "roll_from", "roll_to"):
        qic[column] = path[column].to_numpy()
    qframes = {**frames, "ic": qic}
    put_roll_dates = prior.sleeve.ic_put.v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    active = path[path.date.ge(prior.REAL_START)].reset_index(drop=True) if scope == "real" else path.reset_index(drop=True)
    grid = grid_all[grid_all.date.isin(active.date)].reset_index(drop=True)
    core_schedule = prior.sleeve.build_schedule(selected, "core")
    momentum_schedule = prior.sleeve.build_schedule(selected, "momentum")
    momentum_scope = prior.router_base.mask_schedule(momentum_schedule, scope, active.date)
    engine = prior.sleeve.ic_put.v1.put_engine
    if scope == "real":
        mom_put, mom_trades = engine.run_real_delta(qic, momentum_scope, qframes, option_market, f"{scope}_momentum", put_roll_dates)
        base_signal = corrected_real.copy()
    else:
        mom_put, mom_trades = engine.run_model_delta(qic, momentum_scope, option_market, f"{scope}_momentum", put_roll_dates)
        base_signal = prior.maturity.maturity_model_signals(active, model_market, "m1")
    mom_put = mom_put[mom_put.date.isin(active.date)].reset_index(drop=True)
    mom_put = prior.maturity.costed_core(mom_put, PUT_COST_MULTIPLIER)
    return {
        "scope": scope,
        "active": active,
        "grid": grid,
        "qic": qic,
        "qframes": qframes,
        "put_roll_dates": put_roll_dates,
        "core_schedule": core_schedule,
        "mom_put": mom_put,
        "mom_trades": mom_trades,
        "base_signal": base_signal,
        "current_combined": prior.router_base.current_schedule(),
    }


def run_candidate(
    prepared: dict[str, object],
    threshold: float,
    mom120_gate: bool,
    futures: pd.DataFrame,
    weights: pd.Series,
    option_market: pd.DataFrame,
    real_short,
    model_short,
    model_profit,
    real_profit,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    scope = str(prepared["scope"])
    active = prepared["active"]
    base_signal = prepared["base_signal"].copy()
    base_signal["route"] = (
        base_signal.admission.astype(bool)
        & base_signal.execution_open_valid.astype(bool)
        & np.isfinite(base_signal.iv.astype(float))
        & base_signal.iv.astype(float).gt(threshold)
    )
    old_maturity = prior.maturity.IV_THRESHOLD
    old_seller = prior.seller_state.IV_THRESHOLD
    try:
        prior.maturity.IV_THRESHOLD = threshold
        prior.seller_state.IV_THRESHOLD = threshold
        signal = prior.seller_state.signal_variant(
            base_signal,
            prepared["current_combined"],
            scope,
            "instant",
            prior.seller_state.momentum_permission(),
        )
    finally:
        prior.maturity.IV_THRESHOLD = old_maturity
        prior.seller_state.IV_THRESHOLD = old_seller
    state = prepared["current_combined"]
    state = state[state.layer.eq(scope)].set_index("execution_date")
    signal["momentum_120"] = signal.execution_date.map(state.momentum_120).astype(float)
    if signal.momentum_120.isna().any():
        raise RuntimeError(f"Missing MOM120 for {scope}")
    if mom120_gate:
        signal["route"] = signal.route.astype(bool) & signal.momentum_120.ge(0)
    runner = real_short if scope == "real" else model_short
    isolated, short_events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), DECAY, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    core_masked = prior.router_base.mask_schedule(
        prepared["core_schedule"], scope, routed.loc[routed.state.eq("ic"), "date"]
    )
    candidate = label(scope, threshold, mom120_gate)
    if scope == "real":
        core_put, core_trades = real_profit(
            prepared["qic"],
            core_masked,
            prepared["qframes"],
            option_market,
            candidate,
            prepared["put_roll_dates"],
            open_exit_dates=route_dates,
            profit_multiple=3.0,
        )
    else:
        core_put, core_trades = model_profit(
            prepared["qic"],
            core_masked,
            option_market,
            candidate,
            prepared["put_roll_dates"],
            open_exit_dates=route_dates,
            profit_multiple=3.0,
        )
    core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
    core_put = prior.maturity.costed_core(core_put, PUT_COST_MULTIPLIER)
    total_put = prior.combine_puts(core_put, prepared["mom_put"])
    daily = prior.compose(active, routed, weights, prepared["grid"], total_put, candidate)
    daily["scope"] = scope
    daily["variant"] = candidate.removeprefix(scope + "_")
    trades = pd.concat(
        [
            core_trades.assign(scope=scope, sleeve="core", candidate=candidate),
            prepared["mom_trades"].assign(scope=scope, sleeve="momentum", candidate=candidate),
        ],
        ignore_index=True,
        sort=False,
    )
    signal = signal.assign(scope=scope, candidate=candidate, iv_threshold=threshold, mom120_gate=mom120_gate)
    cycles = cycles.assign(scope=scope, candidate=candidate) if len(cycles) else pd.DataFrame()
    audit = {
        "candidate": candidate,
        "scope": scope,
        "iv_threshold": threshold,
        "mom120_gate": mom120_gate,
        "eligible_days": int(signal.route.astype(bool).sum()),
        "cycles": int(len(cycles)),
        "short_put_days": int(routed.state.eq("short_put").sum()),
        "short_put_share": float(routed.state.eq("short_put").mean()),
        "assignment_or_recovery_days": int(routed.state.isin(["pending_etf_to_ic", "ic_future"]).sum()),
        "early_rolls": int(cycles.early_rolls.fillna(0).sum()) if len(cycles) and "early_rolls" in cycles else 0,
        "min_cash_weight": float(daily.cash_weight.min()),
        "core_profit_restrikes": int(core_trades.action.eq("close_profit_restrike").sum()),
        "core_route_exits": int(core_trades.action.eq("route_open_exit").sum()),
        "short_ledger_error": float(short_audit["ledger_max_abs_error"]),
    }
    return daily, trades, signal, cycles, audit


def distribution_row(product: str, series: pd.Series, moneyness: pd.Series, source: str) -> dict[str, object]:
    x = pd.to_numeric(series, errors="coerce").dropna()
    return {
        "product": product,
        "source": source,
        "rows": len(x),
        "iv_min": float(x.min()),
        "iv_q25": float(x.quantile(0.25)),
        "iv_median": float(x.median()),
        "iv_mean": float(x.mean()),
        "iv_q75": float(x.quantile(0.75)),
        "iv_max": float(x.max()),
        "moneyness_min": float(moneyness.min()),
        "moneyness_median": float(moneyness.median()),
        "moneyness_max": float(moneyness.max()),
    }


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    real_short, model_short, short_source, model_market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, _, _, expiries, _ = prior.router_base.short.real_inputs()
    corrected_real = corrected_real_signal(
        path[path.date.ge(prior.REAL_START)].reset_index(drop=True), frames, option_market, expiries
    )
    prepared = {
        scope: prepare_scope(
            scope, path, futures, weights, selected, grid, frames, option_market,
            model_market, corrected_real,
        )
        for scope in ("real", "model")
    }

    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    cycle_parts: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []
    for threshold in THRESHOLDS:
        for mom120_gate in (False, True):
            for scope in ("real", "model"):
                daily, trades, signals, cycles, audit = run_candidate(
                    prepared[scope], threshold, mom120_gate, futures, weights,
                    option_market, real_short, model_short, model_profit, real_profit,
                )
                daily_parts.append(daily)
                trade_parts.append(trades)
                signal_parts.append(signals)
                if len(cycles):
                    cycle_parts.append(cycles)
                audit_rows.append(audit)

    reference = pd.read_csv(REFERENCE_RUN / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    for scope in ("real", "model"):
        legacy = reference[reference.candidate.eq(f"{scope}_final_joint")].copy()
        legacy["candidate"] = f"{scope}_legacy_flawed_iv375_nomom"
        legacy["variant"] = "legacy_flawed_iv375_nomom"
        daily_parts.append(legacy)
        audit_rows.append(
            {
                "candidate": legacy.candidate.iloc[0],
                "scope": scope,
                "iv_threshold": 0.375,
                "mom120_gate": False,
                "eligible_days": np.nan,
                "cycles": np.nan,
                "short_put_days": int(legacy.fixed_router_state.eq("short_put").sum()),
                "short_put_share": float(legacy.fixed_router_state.eq("short_put").mean()),
                "assignment_or_recovery_days": int(legacy.fixed_router_state.isin(["pending_etf_to_ic", "ic_future"]).sum()),
                "early_rolls": np.nan,
                "min_cash_weight": float(legacy.cash_weight.min()),
                "core_profit_restrikes": np.nan,
                "core_route_exits": np.nan,
                "short_ledger_error": np.nan,
            }
        )

    daily_all = pd.concat(daily_parts, ignore_index=True, sort=False)
    trades_all = pd.concat(trade_parts, ignore_index=True, sort=False)
    signals_all = pd.concat(signal_parts, ignore_index=True, sort=False)
    cycles_all = pd.concat(cycle_parts, ignore_index=True, sort=False)
    exposure = pd.DataFrame(audit_rows)
    summary, wide, unavailable = prior.router_base.summarize(daily_all)
    summary["scope"] = summary.candidate.str.split("_", n=1).str[0]

    model_current = daily_all[daily_all.candidate.eq("model_iv375_nomom")].sort_values("date")
    model_legacy = daily_all[daily_all.candidate.eq("model_legacy_flawed_iv375_nomom")].sort_values("date")
    model_parity = float(np.max(np.abs(model_current.return_net.to_numpy() - model_legacy.return_net.to_numpy())))
    if model_parity > 1e-12:
        raise RuntimeError(f"Corrected harness model parity failed: {model_parity}")

    legacy_signals = pd.read_csv(REFERENCE_RUN / "daily_outputs" / "signals.csv", parse_dates=["eval_date"])
    legacy_signals = legacy_signals[legacy_signals.scope.eq("real")].copy()
    master = frames["snapshots"][["date", "security_id", "strike"]]
    etf = frames["etf500"][["date", "close"]].rename(columns={"date": "eval_date", "close": "etf_close"})
    legacy_signals = legacy_signals.merge(
        master, left_on=["eval_date", "execution_contract"], right_on=["date", "security_id"],
        how="left", validate="one_to_one",
    ).merge(etf, on="eval_date", how="left", validate="many_to_one")
    legacy_moneyness = legacy_signals.strike / legacy_signals.etf_close
    im_signals = pd.read_csv(IM_REFERENCE / "daily_outputs" / "signals.csv")
    im_signals = im_signals[(im_signals.scope.eq("real")) & (im_signals.signal_type.eq("fixed"))]
    iv_distribution = pd.DataFrame(
        [
            distribution_row("IC", legacy_signals.iv, legacy_moneyness, "legacy index-point/ETF-strike mismatch"),
            distribution_row("IC", corrected_real.iv, corrected_real.signal_moneyness, "corrected BS IV from 95% ETF Put close"),
            distribution_row("IC", corrected_real.vendor_iv, corrected_real.signal_moneyness, "corrected 95% ETF Put vendor IV"),
            distribution_row("IM", im_signals.iv, im_signals.signal_moneyness, "BS IV from 95% MO Put close"),
        ]
    )
    threshold_coverage = pd.DataFrame(
        [
            {
                "threshold": t,
                "corrected_real_ic_days_above": int(corrected_real.iv.gt(t).sum()),
                "corrected_real_ic_share_above": float(corrected_real.iv.gt(t).mean()),
            }
            for t in THRESHOLDS
        ]
    )

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily_all.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    trades_all.to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    signals_all.to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    cycles_all.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    exposure.to_csv(RUN / "exposure_audit.csv", index=False)
    iv_distribution.to_csv(RUN / "iv_provenance_audit.csv", index=False)
    threshold_coverage.to_csv(RUN / "threshold_coverage.csv", index=False)
    corrected_real.to_csv(RUN / "corrected_real_signal_base.csv.gz", index=False, compression="gzip")
    (RUN / "executed_state_machines.py").write_text(short_source + "\n\n" + profit_source, encoding="utf-8")

    decision = "corrected_iv_scan_complete_legacy_ic_iv_threshold_invalid_no_production_change"
    stability = "two_dimensional_scan_pending_interpretation"
    meta.update(
        {
            "scan_type": "IC_corrected_95_put_IV_threshold_x_MOM120_gate",
            "baseline": {
                "candidate": ["real_legacy_flawed_iv375_nomom", "model_legacy_flawed_iv375_nomom"],
                "model_corrected_harness_parity_error": model_parity,
                "legacy_real_status": "invalid_for_parameter_selection_due_index_point_ETF_strike_unit_mismatch",
            },
            "candidate_grid": [
                {"iv_threshold": t, "seller_mom120_nonnegative": gate}
                for t in THRESHOLDS for gate in (False, True)
            ],
            "data_snapshot": {
                "real": [str(prior.REAL_START.date()), str(path.date.max().date())],
                "model": [str(path.date.min().date()), str(path.date.max().date())],
                "timezone": "Asia/Shanghai",
                "real_option": "510500 ETF listed option daily close/open/volume plus chain master",
            },
            "iv_definition": {
                "selection": "M+1 closest strike to 95% of prior-close 510500 ETF",
                "calculation": "Black-Scholes inversion from option close, ETF close, rate, dividend, actual expiry",
                "legacy_defect": "CSI500 index points were passed to an ETF-strike selector",
            },
            "cost_model": {
                "IC_one_way_bp": 1,
                "510500_put_one_way_bp": 5,
                "futures_buffer_per_1x": 0.30,
                "cash_annual": 0.03,
                "execution": "T close signal, T+1 option/futures open route; current close accounting thereafter",
                "excluded": ["bid_ask", "market_impact", "capacity", "tax", "dynamic_margin", "forced_liquidation"],
            },
            "unavailable_segments": unavailable,
            "decision": decision,
            "stability_label": stability,
            "source_hashes": {
                "script": sha256(Path(__file__)),
                "spec": sha256(SPEC),
                "reference_daily": sha256(REFERENCE_RUN / "daily_outputs" / "daily.csv.gz"),
                "reference_signals": sha256(REFERENCE_RUN / "daily_outputs" / "signals.csv"),
                "im_reference_signals": sha256(IM_REFERENCE / "daily_outputs" / "signals.csv"),
            },
            "outputs": {
                **meta["outputs"],
                "daily": str(out / "daily.csv.gz"),
                "trades": str(out / "trades.csv.gz"),
                "signals": str(out / "signals.csv.gz"),
                "cycles": str(out / "cycles.csv"),
                "exposure": str(RUN / "exposure_audit.csv"),
                "iv_provenance": str(RUN / "iv_provenance_audit.csv"),
                "threshold_coverage": str(RUN / "threshold_coverage.csv"),
                "corrected_real_signal_base": str(RUN / "corrected_real_signal_base.csv.gz"),
            },
            "warnings": [
                "Historical counterfactual research; production rules, digest, ledger, and trading interfaces unchanged.",
                "Legacy real IC threshold results are invalid for selection because their IV contract was not the 95% ETF Put.",
                "Model options remain theoretical and are not executable listed history.",
                "Real sample is shorter than five years; sparse high-threshold cycles cannot establish robustness.",
            ],
            "git_status_after": subprocess.run(
                ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip(),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    full = summary[summary.segment.eq("full")].copy()
    record = "\n".join(
        [
            "# IC v1.4-r1：修正95%选约后的IV阈值 × MOM120扫描",
            "",
            "> 研究回放；旧IC阈值路径存在指数点位/ETF行权价单位错配；未修改生产。",
            "",
            "## IV Provenance Audit",
            "",
            iv_distribution.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Full Results",
            "",
            full.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Exposure Audit",
            "",
            exposure.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Decision",
            "",
            f"`{decision}`。旧真实37.5%结果作废；新参数须按相邻阈值、双层方向和周期集中度解释，不自动晋级。",
            "",
        ]
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(iv_distribution.to_string(index=False))
    print(full[["candidate", "ann_return", "sharpe_repo", "max_dd"]].to_string(index=False))


if __name__ == "__main__":
    main()
