"""Rebuild IC Put research, layer 12: 95%-strike versus ATM short Put at equal entry Delta."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import types
from pathlib import Path

import numpy as np
import pandas as pd

import research_ic_put_rebuild_layer3_maturity_v1 as layer3
import research_ic_put_rebuild_layer9_short_quantity_delta_v1 as layer9
import research_ic_put_rebuild_layer10_cost_cycle_robustness_v1 as layer10
import research_ic_short95_maturity_scan_v1 as maturity
import research_ic_v13_short95_quantity_delta_scan_v1 as quantity
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l12_3x_qd05_strike_95pct_versus_atm"
LAYER11 = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l11_3x_joint_neighborhood_iv275_300_325_x_q1_qd05"
SPEC = ROOT / "docs" / "ic_put_rebuild_layer12_short_strike_atm_vs95_v1_spec.md"
STRIKES = {"strike95": 0.95, "atm": 1.00}
TARGET_DELTA = 0.50


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def choose_real_target(chain: pd.DataFrame, prior_spot: float, month: pd.Timestamp,
                       strike_ratio: float):
    selected = chain[chain.contract_month.eq(month)].copy()
    if selected.empty:
        return None
    selected["distance"] = (
        selected.strike.astype(float) - prior_spot * strike_ratio
    ).abs()
    return selected.sort_values(["distance", "strike", "contract_id"]).iloc[0]


def strike_runners(strike_ratio: float):
    real_runner, model_runner, source = maturity.patched_short_runners()
    real_runner.__globals__["choose_real"] = (
        lambda chain, prior_spot, month: choose_real_target(
            chain, prior_spot, month, strike_ratio
        )
    )
    if math.isclose(strike_ratio, 0.95):
        return real_runner, model_runner, source
    marker = "\ndef run_model_maturity("
    if marker not in source:
        raise RuntimeError("Unable to split patched short-Put runners")
    _, model_tail = source.split(marker, 1)
    model_source = "def run_model_maturity(" + model_tail
    old = "float(market.iloc[i-1].spot_close)*.95"
    if old not in model_source:
        raise RuntimeError("Model strike hook changed")
    model_source = model_source.replace(
        old, f"float(market.iloc[i-1].spot_close)*{strike_ratio:.8f}"
    )
    namespace = dict(vars(maturity.base.short))
    namespace.update(
        real_entry_month=maturity.real_entry_month,
        model_entry_month=maturity.model_entry_month,
    )
    exec(compile(model_source, str(Path(__file__)), "exec"), namespace)
    return real_runner, namespace["run_model_maturity"], source + "\n\n# ATM model override\n" + model_source


def configure_runner_inputs(real_runner, model_runner, path: pd.DataFrame,
                            futures: pd.DataFrame, model_market: pd.DataFrame):
    prior = base.prior
    _, etf, chains, options, expiries, _ = prior.router_base.short.real_inputs()
    real_path = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    model_path = path.reset_index(drop=True)
    real_runner.__globals__["real_inputs"] = (
        lambda: (real_path, etf, chains, options, expiries, futures)
    )
    model_runner.__globals__["model_source"] = types.SimpleNamespace(
        model_inputs=lambda: (model_path, model_market, futures),
        CASH=prior.router_base.short.model_source.CASH,
    )
    return real_runner, model_runner


def cycle_sizing(scope: str, active: pd.DataFrame, market: pd.DataFrame,
                 cycles: pd.DataFrame, strike_ratio: float,
                 strike_label: str) -> pd.DataFrame:
    prior = base.prior
    rows = []
    active_dates = list(pd.to_datetime(active.date))
    date_pos = {day: i for i, day in enumerate(active_dates)}
    if scope == "real":
        _, etf, chains, _, _, _ = prior.router_base.short.real_inputs()
        etf = etf.sort_index()
    else:
        market_idx = market.set_index("date").sort_index()
        proxy = prior.router_base.short.model_source.proxy
    for cycle_id, cycle in cycles.reset_index(drop=True).iterrows():
        entry = pd.Timestamp(cycle.entry_date)
        if entry not in date_pos or date_pos[entry] == 0:
            raise RuntimeError(f"Invalid {scope} cycle entry {entry}")
        previous_day = active_dates[date_pos[entry] - 1]
        if scope == "real":
            chain = chains.get(previous_day, pd.DataFrame())
            hit = chain[chain.contract_id.astype(str).eq(str(cycle.put_contract))]
            if len(hit) != 1:
                raise RuntimeError(f"Missing decision-known Delta for {cycle.put_contract}")
            abs_delta = abs(float(hit.iloc[0].delta))
            previous_active = active.iloc[date_pos[entry] - 1]
            etf_close = float(etf.loc[previous_day, "close"])
            contracts_per_1_ic_q1 = (
                float(previous_active.settle) * prior.router_base.short.real_source.IC_MULTIPLIER
                / (etf_close * prior.router_base.short.real_source.ETF_MULTIPLIER)
            )
            actual_moneyness = float(hit.iloc[0].strike) / etf_close
            delta_source = "previous_close_listed_chain"
        else:
            previous_market = market_idx.loc[previous_day]
            month = pd.Timestamp(cycle.entry_contract_month)
            expiry = proxy.fourth_wednesday(month, pd.DatetimeIndex(active.date))
            spot = float(previous_market.spot_close)
            strike = strike_ratio * spot
            years = (expiry - previous_day).days / 365.0
            abs_delta = quantity.bs_put_abs_delta(
                spot, strike, float(previous_market.rate_close),
                float(previous_market.dividend_close),
                float(previous_market.sigma_close), years,
            )
            contracts_per_1_ic_q1 = np.nan
            actual_moneyness = strike_ratio
            delta_source = "previous_close_model_qvix"
        if not (np.isfinite(abs_delta) and abs_delta > 1e-8):
            raise RuntimeError(f"Invalid entry Delta {scope} {entry}: {abs_delta}")
        scale = TARGET_DELTA / abs_delta
        end_value = (
            cycle.exit_date if pd.notna(cycle.get("exit_date", np.nan))
            else cycle.get("mark_date", active.date.iloc[-1])
        )
        rows.append({
            "scope": scope, "strike_variant": strike_label,
            "strike_target": strike_ratio, "cycle_id": int(cycle_id),
            "entry_date": entry, "end_date": pd.Timestamp(end_value),
            "put_contract": getattr(cycle, "put_contract", ""),
            "quantity_mode": "q_delta05",
            "target_delta_per_1_ic": TARGET_DELTA,
            "decision_known_entry_abs_delta": abs_delta,
            "actual_entry_moneyness": actual_moneyness,
            "quantity_scale": scale,
            "effective_entry_delta_per_1_ic": abs_delta * scale,
            "contracts_per_1_ic": contracts_per_1_ic_q1 * scale,
            "contracts_for_fixed_core_0_5x": 0.5 * contracts_per_1_ic_q1 * scale,
            "delta_source": delta_source,
        })
    return pd.DataFrame(rows)


def run_variant(scope: str, strike_label: str, strike_ratio: float,
                prepared: dict[str, object], futures: pd.DataFrame,
                weights: pd.Series, option_market: pd.DataFrame,
                model_market: pd.DataFrame, real_runner, model_runner,
                model_profit, real_profit):
    prior = base.prior
    active = prepared["active"]
    signal = layer9.build_signal(prepared)
    runner = real_runner if scope == "real" else model_runner
    isolated, short_events, cycles, short_audit = runner(
        prior.router_base.entry_series(signal), None, "m1", prior.OPTION_ONE_WAY
    )
    routed = prior.router_base.stitched_router(scope, active, futures, isolated, signal)
    route_dates = set(routed.loc[routed.action.eq("ic_to_short_put_open"), "date"])
    core_masked = prior.router_base.mask_schedule(
        prepared["core_schedule"], scope, routed.loc[routed.state.eq("ic"), "date"]
    )
    label = f"{scope}_{strike_label}_qdelta05_profit3x"
    if scope == "real":
        core_put, core_trades = real_profit(
            prepared["qic"], core_masked, prepared["qframes"], option_market,
            label, prepared["put_roll_dates"], open_exit_dates=route_dates,
            profit_multiple=3.0,
        )
    else:
        core_put, core_trades = model_profit(
            prepared["qic"], core_masked, option_market, label,
            prepared["put_roll_dates"], open_exit_dates=route_dates,
            profit_multiple=3.0,
        )
    core_put = core_put[core_put.date.isin(active.date)].reset_index(drop=True)
    core_put = prior.maturity.costed_core(core_put, base.PUT_COST_MULTIPLIER)
    total_put = prior.combine_puts(core_put, prepared["mom_put"])
    sizing = cycle_sizing(scope, active, model_market, cycles, strike_ratio, strike_label)
    daily = quantity.compose_sized(
        active, routed, weights, prepared["grid"], total_put,
        label, sizing, "q_delta05",
    )
    daily["scope"] = scope
    daily["variant"] = f"{strike_label}_qdelta05_profit3x"
    daily["strike_variant"] = strike_label
    audit = {
        "candidate": label, "scope": scope, "strike_variant": strike_label,
        "strike_target": strike_ratio, "cycles": len(cycles),
        "assignments": int(short_audit["assignments"]),
        "short_put_days": int(routed.state.eq("short_put").sum()),
        "min_cash_weight": float(daily.cash_weight.min()),
        "negative_cash_days": int(daily.cash_weight.lt(-1e-12).sum()),
        "ledger_error": float(short_audit["ledger_max_abs_error"]),
        "entry_delta_min": float(sizing.decision_known_entry_abs_delta.min()),
        "entry_delta_median": float(sizing.decision_known_entry_abs_delta.median()),
        "entry_delta_max": float(sizing.decision_known_entry_abs_delta.max()),
        "entry_moneyness_min": float(sizing.actual_entry_moneyness.min()),
        "entry_moneyness_median": float(sizing.actual_entry_moneyness.median()),
        "entry_moneyness_max": float(sizing.actual_entry_moneyness.max()),
        "quantity_scale_median": float(sizing.quantity_scale.median()),
        "quantity_scale_max": float(sizing.quantity_scale.max()),
    }
    return daily, cycles.assign(scope=scope, strike_variant=strike_label), sizing, short_events, core_trades, audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite a started layer-12 run")
    prior = base.prior
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    _, _, short95_source, model_market = prior.configure_short_runners(path, futures)
    model_profit, real_profit, profit_source = prior.profit.patched_profit_engines()
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, _, _, expiries, _ = prior.router_base.short.real_inputs()
    active_real = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    corrected_real = layer3.corrected_real_signal_for_maturity(
        active_real, frames, option_market, expiries, "m1"
    )
    prepared = {
        scope: base.prepare_scope(
            scope, path, futures, weights, selected, grid, frames,
            option_market, model_market, corrected_real,
        )
        for scope in ("real", "model")
    }
    prepared["model"]["base_signal"] = prior.maturity.maturity_model_signals(
        prepared["model"]["active"], model_market, "m1"
    )
    old_threshold = layer9.IV_THRESHOLD
    layer9.IV_THRESHOLD = 0.30
    results = {}
    sources = []
    try:
        for strike_label, strike_ratio in STRIKES.items():
            real_runner, model_runner, source = strike_runners(strike_ratio)
            real_runner, model_runner = configure_runner_inputs(
                real_runner, model_runner, path, futures, model_market
            )
            sources.append(source)
            for scope in ("real", "model"):
                results[(scope, strike_label)] = run_variant(
                    scope, strike_label, strike_ratio, prepared[scope], futures,
                    weights, option_market, model_market, real_runner,
                    model_runner, model_profit, real_profit,
                )
    finally:
        layer9.IV_THRESHOLD = old_threshold

    daily_parts = [results[key][0] for key in results]
    for scope in ("real", "model"):
        daily_parts.append(layer10.no_seller_profit3x(
            scope, prepared[scope], weights, option_market,
            model_profit, real_profit, 5,
        ))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    cycles = pd.concat([results[key][1] for key in results], ignore_index=True, sort=False)
    sizing = pd.concat([results[key][2] for key in results], ignore_index=True, sort=False)
    events = pd.concat([results[key][3].assign(scope=key[0], strike_variant=key[1]) for key in results], ignore_index=True, sort=False)
    audits = pd.DataFrame([results[key][5] for key in results])
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()

    comparison_rows = []
    gate_rows = []
    for scope in ("real", "model"):
        no = full[full.candidate.eq(f"{scope}_noseller_profit3x_cost5bp")].iloc[0]
        old = full[full.candidate.eq(f"{scope}_strike95_qdelta05_profit3x")].iloc[0]
        atm = full[full.candidate.eq(f"{scope}_atm_qdelta05_profit3x")].iloc[0]
        for label, row in (("strike95", old), ("atm", atm)):
            audit = audits[audits.candidate.eq(row.candidate)].iloc[0]
            comparison_rows.append({
                "scope": scope, "strike_variant": label,
                "ann_return": row.ann_return,
                "ann_return_delta_vs_noseller": row.ann_return - no.ann_return,
                "sharpe": row.sharpe_repo,
                "sharpe_delta_vs_noseller": row.sharpe_repo - no.sharpe_repo,
                "max_dd": row.max_dd,
                "mdd_abs_worsening_vs_noseller": abs(row.max_dd) - abs(no.max_dd),
                "cycles": int(audit.cycles), "assignments": int(audit.assignments),
                "entry_delta_median": audit.entry_delta_median,
                "entry_moneyness_median": audit.entry_moneyness_median,
                "quantity_scale_median": audit.quantity_scale_median,
                "min_cash_weight": audit.min_cash_weight,
            })
        audit_atm = audits[audits.candidate.eq(atm.candidate)].iloc[0]
        cagr_diff_pp = 100.0 * (atm.ann_return - old.ann_return)
        sharpe_diff = atm.sharpe_repo - old.sharpe_repo
        mdd_worse_pp = 100.0 * max(0.0, abs(atm.max_dd) - abs(old.max_dd))
        gate_rows.append({
            "scope": scope, "cagr_diff_pp_atm_vs_95": cagr_diff_pp,
            "sharpe_diff_atm_vs_95": sharpe_diff,
            "mdd_worse_pp_atm_vs_95": mdd_worse_pp,
            "atm_cycles": int(audit_atm.cycles),
            "atm_assignments": int(audit_atm.assignments),
            "atm_min_cash_weight": audit_atm.min_cash_weight,
            "cagr_gate": bool(cagr_diff_pp >= 0.0),
            "sharpe_gate": bool(sharpe_diff >= -0.05),
            "drawdown_gate": bool(mdd_worse_pp <= 1.0),
            "capital_gate": bool(audit_atm.negative_cash_days == 0),
            "event_gate": bool(audit_atm.cycles >= 1),
            "ledger_gate": bool(audit_atm.ledger_error <= 1e-11),
        })
    comparison = pd.DataFrame(comparison_rows)
    gates = pd.DataFrame(gate_rows)
    atm_pass = bool(gates[[
        "cagr_gate", "sharpe_gate", "drawdown_gate", "capital_gate",
        "event_gate", "ledger_gate",
    ]].all().all())
    decision = (
        "select_atm_qdelta05_over_strike95_awaiting_user_confirmation"
        if atm_pass else
        "keep_strike95_qdelta05_reject_atm_replacement_awaiting_user_confirmation"
    )
    stability = (
        "atm_noninferior_across_real_and_model"
        if atm_pass else "atm_failed_cross_layer_return_or_risk_gate"
    )

    reference = pd.read_csv(LAYER11 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    parity = {}
    for scope in ("real", "model"):
        got = daily[daily.candidate.eq(f"{scope}_strike95_qdelta05_profit3x")].sort_values("date")
        old = reference[reference.candidate.eq(f"{scope}_iv300_qd05_profit3x")].sort_values("date")
        if not got.date.reset_index(drop=True).equals(old.date.reset_index(drop=True)):
            raise RuntimeError(f"95% parity dates failed {scope}")
        error = float(np.max(np.abs(got.return_net.to_numpy() - old.return_net.to_numpy())))
        if error > 1e-12:
            raise RuntimeError(f"95% parity returns failed {scope}: {error}")
        parity[scope] = error

    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    cycles.to_csv(out / "cycles.csv", index=False, encoding="utf-8-sig")
    sizing.to_csv(out / "quantity_sizing.csv", index=False, encoding="utf-8-sig")
    events.to_csv(out / "events.csv", index=False, encoding="utf-8-sig")
    audits.to_csv(out / "exposure_audit.csv", index=False, encoding="utf-8-sig")
    comparison.to_csv(RUN / "strike_comparison.csv", index=False, encoding="utf-8-sig")
    gates.to_csv(RUN / "replacement_gates.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(
        short95_source + "\n\n" + "\n\n".join(sources) + "\n\n" + profit_source,
        encoding="utf-8",
    )
    meta.update({
        "scan_type": "candidate_bundle",
        "parameter_group": "short_put_strike95_vs_atm_at_equal_entry_delta05",
        "baseline": {"strike_variant": "strike95", "layer11_center_parity_max_abs": parity, "no_short_put": "same profit3x full portfolio"},
        "candidate_grid": [{"strike_variant": key, "strike_target": value, "target_delta_per_1_ic": TARGET_DELTA} for key, value in STRIKES.items()],
        "signal_isolation": "Both execution strikes use the corrected strike95 contract IV>30% admission signal; only execution contract strike changes.",
        "data_snapshot": {"real": ["2022-09-19", "2026-08-14"], "model": ["2015-04-16", "2026-08-14"]},
        "cost_model": {"all_510500_put_one_way_bp": 5, "ic_one_way_bp": 1, "futures_buffer_per_1x": 0.30, "cash_annual": 0.03},
        "fixed_policy": {"iv_signal_threshold": 0.30, "maturity": "M+1", "premium_decay": None, "core_profit_multiple": 3, "seller_mom120": False, "call": "excluded"},
        "replacement_gates": gate_rows,
        "unavailable_segments": unavailable,
        "decision": decision,
        "stability_label": stability,
        "source_hashes": {"script": sha256(Path(__file__)), "spec": sha256(SPEC), "maturity_engine": sha256(ROOT / "research_ic_short95_maturity_scan_v1.py"), "layer11_daily": sha256(LAYER11 / "daily_outputs" / "daily.csv.gz")},
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "cycles": str(out / "cycles.csv"), "sizing": str(out / "quantity_sizing.csv"), "events": str(out / "events.csv"), "exposure": str(out / "exposure_audit.csv"), "comparison": str(RUN / "strike_comparison.csv"), "gates": str(RUN / "replacement_gates.csv")},
        "warnings": ["Research only; no production, email, ledger, registry or order change.", "ATM and strike95 share the strike95-IV admission signal to isolate execution strike.", "Real listed history and route cycles are sparse.", "Model options are theoretical proxy.", "Continuous sizing ignores integer contracts, dynamic margin, forced liquidation, tax and explicit bid-ask impact.", "Legacy engine action labels still contain 95_put for ATM; candidate, strike audit and selected contract are authoritative.", "Worktree was dirty before this isolated run."],
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "\n".join([
        "# IC Put 污染后重建：第十二层卖 Put 行权价 ATM vs 95%", "",
        "## Run Metadata", "", "研究专用；两组总初始Delta均为0.5；固定3倍兑现、IV30%信号与持有到期；未修改生产。", "",
        "## Isolation", "", "两组共用修正后95%信号合约IV>30%的入场日期，只改变执行合约行权价；避免把IV微笑与选约效果混入主比较。", "",
        "## Full-Sample Comparison", "", comparison.to_markdown(index=False, floatfmt=".6f"), "",
        "## Replacement Gates", "", gates.to_markdown(index=False, floatfmt=".6f"), "",
        "## Entry Sizing", "", sizing.to_markdown(index=False, floatfmt=".6f"), "",
        "## Verification", "", f"95%路径与第十一层中心逐日误差：{parity}。", "",
        "## Stability Classification", "", f"`{stability}`。", "",
        "## Decision", "", f"`{decision}`。未经用户确认不进入下一层。", "",
    ])
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(comparison.to_string(index=False))
    print(gates.to_string(index=False))


if __name__ == "__main__":
    main()
