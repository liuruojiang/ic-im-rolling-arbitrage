"""Layer 14: causal T-volume admission, T+1 open/close execution, and momentum re-test."""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import pandas as pd

import research_ic_put_rebuild_layer13_momentum_sleeve_v1 as l13
import research_ic_put_rebuild_layer3_maturity_v1 as l3
import research_ic_v14_corrected_iv_mom120_scan_v1 as base


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_put_ic_put_t_13_r4"
SPEC = ROOT / "docs" / "ic_put_rebuild_layer14_t_volume_execution_and_momentum_retest_v1_spec.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_open_close_inputs():
    """The frozen source data, without v17's intentional close-execution override."""
    engine = base.prior.sleeve.ic_put.v1.put_engine
    v18 = engine.v19.v18
    v13 = v18.v13
    frames = v13.core.v2.load_inputs()
    daily_valuation, valuation_checks = v13.core.v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    daily_valuation = daily_valuation[daily_valuation["date"] <= v18.END].copy()
    market, market_checks = v13.proxy.prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = v13.proxy.qvix_validation(market, frames["q500"])
    if not qvix_stats["passed"]:
        raise RuntimeError("Raw open/close QVIX validation failed")
    return frames, daily_valuation, market, {
        "valuation": valuation_checks, "market": market_checks, "qvix": qvix_stats,
        "qvix_table": qvix_table, "execution_state_override": "none; raw open and close retained",
    }


def signal_t_volume(active, frames, option_market, expiries):
    """Keep the corrected T-date IV; use only the known T-date volume for admission."""
    signal = l3.corrected_real_signal_for_maturity(active, frames, option_market, expiries, "m1")
    history = frames["histories"].set_index(["security_id", "date"])
    valid = []
    for row in signal.itertuples(index=False):
        key = (str(row.execution_contract), pd.Timestamp(row.eval_date))
        quote = history.loc[key] if key in history.index else None
        valid.append(bool(quote is not None and float(quote.volume) > 0 and float(quote.close) > 0))
    signal["execution_open_valid"] = valid
    signal["admission_volume_date"] = signal.eval_date
    return signal


def causal_short_runners(path, futures, timing):
    """Remove T+1 volume filtering; only alter the execution-price field for the close variant."""
    prior = base.prior
    real_template, model_template, source = prior.maturity.patched_short_runners()
    source = source.replace(" and float(q.volume) > 0", "").replace(" and q.volume > 0", "")
    if timing == "close":
        source = source.replace("float(q.open)", "float(q.close)")
        source = source.replace("if q.open > 0", "if q.close > 0")
        source = source.replace('price(m,strike,ex,"open")', 'price(m,strike,ex,"close")')
    elif timing != "open":
        raise ValueError(timing)
    namespace = dict(real_template.__globals__)
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    real_runner, model_runner = namespace["run_real_maturity"], namespace["run_model_maturity"]
    _, etf, chains, options, expiries, _ = prior.router_base.short.real_inputs()
    _, market, _ = prior.router_base.short.model_source.model_inputs()
    real_path = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    model_path = path.reset_index(drop=True)
    real_runner.__globals__["real_inputs"] = lambda: (real_path, etf, chains, options, expiries, futures)
    model_runner.__globals__["model_source"] = base.prior.types.SimpleNamespace(
        model_inputs=lambda: (model_path, market, futures), CASH=prior.router_base.short.model_source.CASH
    )
    return real_runner, model_runner, source, market


def costed_compose():
    """Use the L13 composition but retain the IC exit turnover on route-transition days."""
    source = inspect.getsource(l13.compose_dual_sized)
    old = "turnover = turnover.where(~transitions, 0.0)"
    if old not in source:
        raise RuntimeError("Layer-13 transition-cost hook changed")
    source = source.replace(old, "# Retain turnover: the momentum IC is actually closed when the route opens.")
    namespace = dict(vars(l13))
    exec(compile(source, str(Path(__file__)), "exec"), namespace)
    return namespace["compose_dual_sized"]


def run_timing(timing, prepared, selected, futures, weights, option_market, model_market, runners):
    original = l13.compose_dual_sized
    l13.compose_dual_sized = costed_compose()
    try:
        results = {
            scope: l13.run_scope(
                scope, prepared[scope], selected, futures, weights, option_market, model_market,
                runners[0], runners[1], prepared[scope]["model_profit"], prepared[scope]["real_profit"],
            )
            for scope in ("real", "model")
        }
    finally:
        l13.compose_dual_sized = original
    daily = pd.concat([results[x][0] for x in ("real", "model")], ignore_index=True)
    daily["timing"] = timing
    daily["candidate"] = daily["scope"] + "_" + timing + "_" + daily["variant"]
    trades = pd.concat([results[x][1] for x in ("real", "model")], ignore_index=True)
    signals = pd.concat([results[x][2] for x in ("real", "model")], ignore_index=True)
    events = pd.concat([results[x][3] for x in ("real", "model")], ignore_index=True)
    cycles = pd.concat([results[x][4] for x in ("real", "model")], ignore_index=True)
    audits = {x: results[x][6] for x in ("real", "model")}
    return daily, trades, signals, events, cycles, audits


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite started layer-14 run")
    prior = base.prior
    # All downstream real-input helpers reference this loader, so patch it once
    # for the isolated process.  This is the inverse of v17's close-only retest.
    prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs = raw_open_close_inputs
    path, futures = prior.quarterly_path()
    weights = prior.current_momentum_weights()
    selected = prior.current_selected(weights)
    grid = prior.current_grid(path.date)
    frames, _, option_market, _ = prior.sleeve.ic_put.v1.put_engine.v19.v18.load_close_inputs()
    _, _, _, _, expiries, _ = prior.router_base.short.real_inputs()
    active_real = path[path.date.ge(prior.REAL_START)].reset_index(drop=True)
    corrected = signal_t_volume(active_real, frames, option_market, expiries)
    prepared = {}
    for timing in ("open", "close"):
        real_runner, model_runner, source, model_market = causal_short_runners(path, futures, timing)
        scoped = {
            scope: base.prepare_scope(scope, path, futures, weights, selected, grid, frames, option_market, model_market, corrected)
            for scope in ("real", "model")
        }
        scoped["model"]["base_signal"] = prior.maturity.maturity_model_signals(scoped["model"]["active"], model_market, "m1")
        for scope in ("real", "model"):
            scoped[scope]["model_profit"], scoped[scope]["real_profit"] = prior.profit.patched_profit_engines()[:2]
        prepared[timing] = (scoped, (real_runner, model_runner), source, model_market)

    old_threshold = l13.layer9.IV_THRESHOLD
    l13.layer9.IV_THRESHOLD = 0.30
    try:
        runs = {timing: run_timing(timing, prepared[timing][0], selected, futures, weights, option_market, prepared[timing][3], prepared[timing][1]) for timing in ("open", "close")}
    finally:
        l13.layer9.IV_THRESHOLD = old_threshold
    daily = pd.concat([runs[x][0] for x in ("open", "close")], ignore_index=True)
    summary, wide, unavailable = prior.router_base.summarize(daily)
    full = summary[summary.segment.eq("full")].copy()
    paired = []
    passed = True
    for timing in ("open", "close"):
        for scope in ("real", "model"):
            fixed = full[full.candidate.eq(f"{scope}_{timing}_fixed_only_qd05")].iloc[0]
            both = full[full.candidate.eq(f"{scope}_{timing}_both_qd05")].iloc[0]
            audit = runs[timing][5][scope]["variants"]["both_qd05"]
            integrity = audit["negative_cash_days"] == 0 and audit["core_put_mark_during_route_max"] <= 1e-12 and audit["momentum_put_mark_during_route_max"] <= 1e-12
            ann, sharpe, dd = both.ann_return-fixed.ann_return, both.sharpe_repo-fixed.sharpe_repo, abs(both.max_dd)-abs(fixed.max_dd)
            gate = bool(ann >= 0 and sharpe >= -0.05 and dd <= 0.01 and integrity)
            passed = passed and gate
            paired.append({"timing": timing, "scope": scope, "fixed_ann_return": fixed.ann_return, "both_ann_return": both.ann_return, "both_minus_fixed_ann_pp": 100*ann, "both_minus_fixed_sharpe": sharpe, "both_mdd_abs_worsening_pp": 100*dd, "integrity": integrity, "gate": gate})
    paired = pd.DataFrame(paired)
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([runs[x][1] for x in ("open", "close")], ignore_index=True).to_csv(out / "trades.csv.gz", index=False, compression="gzip")
    pd.concat([runs[x][2] for x in ("open", "close")], ignore_index=True).to_csv(out / "signals.csv.gz", index=False, compression="gzip")
    pd.concat([runs[x][3] for x in ("open", "close")], ignore_index=True).to_csv(out / "router_events.csv", index=False)
    pd.concat([runs[x][4] for x in ("open", "close")], ignore_index=True).to_csv(out / "cycles.csv", index=False)
    corrected.to_csv(out / "causal_real_signal.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    decision = "reject_momentum_sleeve_short_put_under_causal_open_and_close_retest" if not passed else "retain_momentum_sleeve_short_put_under_causal_open_and_close_retest"
    meta.update({"scan_type":"candidate_bundle","candidate_grid":[{"timing":x,"variant":y} for x in ("open","close") for y in ("no_short","fixed_only","momentum_only","both")],"data_snapshot":{"real":["2022-09-19","2026-08-14"],"model":["2015-04-16","2026-08-14"]},"execution":{"signal":"T close; T-date option volume only","fills":["T+1 open","T+1 close"],"T_plus_1_volume_filter":False},"fixed_policy":{"iv_threshold":0.30,"strike":0.95,"quantity":"aggregate absolute Delta 0.5","core_profit_multiple":3},"cost_model":{"510500_put_one_way_bp":5,"ic_one_way_bp":1,"futures_buffer":0.30,"cash_annual":0.03,"momentum_route_transition_cost":"included"},"audit":{x:runs[x][5] for x in ("open","close")},"paired_gate":paired.to_dict("records"),"unavailable_segments":unavailable,"decision":decision,"stability_label":"causal_t_volume_open_close_momentum_retest","source_hashes":{"script":sha(Path(__file__)),"spec":sha(SPEC),"layer13":sha(ROOT / "research_ic_put_rebuild_layer13_momentum_sleeve_v1.py"),"base":sha(ROOT / "research_ic_v14_corrected_iv_mom120_scan_v1.py")},"outputs":{**meta["outputs"],"daily":str(out / "daily.csv.gz"),"signals":str(out / "signals.csv.gz"),"paired":str(RUN / "paired_comparison.csv")},"warnings":["Research only; no production change.","T+1 open and close are daily-price proxies, not timestamped executable quotes.","Model option history remains theoretical proxy."],"git_status_after":subprocess.run(["git","status","--short"],cwd=ROOT,capture_output=True,text=True).stdout.strip()})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=l13.old_momentum.json_default)+"\n", encoding="utf-8")
    (RUN / "record.md").write_text("# IC Put 第十四层：T日成交量与第十三层复核\n\n研究专用，未修改生产。\n\n## Full results\n\n"+full.to_markdown(index=False,floatfmt=".6f")+"\n\n## Momentum paired gates\n\n"+paired.to_markdown(index=False,floatfmt=".6f")+f"\n\n## Decision\n\n`{decision}`。\n",encoding="utf-8")
    with (RUN / "command_log.txt").open("a",encoding="utf-8") as f: f.write(f"\ncwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(paired.to_string(index=False))


if __name__ == "__main__":
    main()
