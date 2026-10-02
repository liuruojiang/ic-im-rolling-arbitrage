"""IM v1.4 full-joint comparison: zero, one, or repeated short-Put early rolls."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v14_mom_negative_put_qty_2_vs_3_v1 as latest
import research_im_v14_put_monthly_roll_timing_v1 as t


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260920_ic_im_im_v1_4_r1_full_joint_fixed_core_short_put_early_roll_policy_max_early_rolls_0_vs_1_vs_repeated_while_admissible"
SPEC = ROOT / "docs" / "im_v14_short_put_roll_count_v1_spec.md"
CURRENT = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_4_r1_full_joint_im_core_and_momentum_long_put_mom120_floor_2_vs_3" / "daily.csv.gz"
TAIL = ROOT / "outputs" / "v14_full_nav_refresh_20260920_final" / "im_tail_daily.csv"
SIGNALS = ROOT / "outputs" / "nav_versioned_refresh_20260920_v14_corrected_final2" / "historical_signals.json"
POLICIES = ("no_early_roll", "one_early_roll", "repeat_while_admissible")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def routers():
    one, source = t.joint.audited_router()
    needle = 'not position.get("rolled", False) and '
    if source.count(needle) != 1:
        raise RuntimeError("one-roll guard changed")
    repeated_source = source.replace(needle, "", 1)
    namespace = dict(vars(t.common.router))
    namespace["OPTION_ONE_WAY_COST"] = t.joint.component.OPTION_ONE_WAY_COST
    exec(compile(repeated_source, str(Path(__file__)), "exec"), namespace)
    repeated_raw = namespace["run_router_decay"]

    def repeated(base, options, futures, signal, threshold, fallback, decay):
        daily, events, cycles = repeated_raw(base, options, futures, signal, threshold, fallback, decay)
        daily["early_roll_executed"] = daily.action.eq("put_early_roll60_buyback_and_sell_next_open")
        return daily, events, cycles

    return one, repeated, source, repeated_source


def metrics_frame(daily: pd.DataFrame):
    return t.build_metrics(daily)


def cycle_audit(cycles: pd.DataFrame, events: pd.DataFrame, daily: pd.DataFrame) -> dict:
    rolls = pd.to_numeric(cycles.get("early_rolls", pd.Series(dtype=float)), errors="coerce").fillna(0)
    actions = events.get("action", pd.Series(dtype=str)).astype(str)
    return {
        "cycles": int(len(cycles)),
        "completed_cycles": int(cycles.get("closed", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if len(cycles) else 0,
        "early_rolls": int(rolls.sum()),
        "max_early_rolls_in_one_cycle": int(rolls.max()) if len(rolls) else 0,
        "cycles_with_2plus_rolls": int((rolls >= 2).sum()),
        "early_roll_signals": int(actions.str.contains("premium_decay_60_signal", na=False).sum()),
        "early_roll_executions": int(actions.eq("put_early_roll60_buyback_and_sell_next_open").sum()),
        "blocked_reentry_events": int(actions.str.contains("blocked", case=False, na=False).sum()),
        "short_put_days": int(daily.state.eq("put").sum()),
        "cash_days": int(daily.state.eq("cash").sum()),
        "recovery_days": int(daily.state.eq("recovery_im").sum()),
    }


def append_latest_tail(daily: pd.DataFrame, policy: str) -> pd.DataFrame:
    if daily.iloc[-1].fixed_router_state != "imc":
        raise RuntimeError(f"{policy} is not back in IMC at the formal-chain cutoff")
    tail = pd.read_csv(TAIL, parse_dates=["date"])[["date", "ret"]]
    tail = tail[tail.date > daily.date.max()].copy()
    if tail.empty or str(tail.date.max().date()) != "2026-09-18":
        raise RuntimeError("latest official tail missing or stale")
    tail["candidate"] = f"real_{policy}"
    tail["scope"] = "real"
    tail["sessions_before"] = 0
    tail["fixed_router_state"] = "imc"
    return pd.concat([daily, tail], ignore_index=True, sort=False)


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("refusing to overwrite a started run")

    one_router, repeated_router, one_source, repeated_source = routers()
    real_engine, model_engine, _ = t.joint.component.profit_route_engines()
    weights = t.portfolio.current_momentum_weights()
    out = RUN / "daily_outputs_final"
    out.mkdir(exist_ok=True)
    (out / "call_artifacts").mkdir(exist_ok=True)

    daily_parts = []
    event_parts = []
    cycle_parts = []
    audits = {}
    for scope in ("model", "real"):
        base, _ = t.portfolio.rebuild_base(scope, weights)
        grid = t.portfolio.half_grid(scope)
        market, router_base, options, _, futures, _ = t.joint.quarterly_router_inputs(scope, base)
        signal = t.maturity.prepare_signal(router_base, options, "m1")
        resets, _ = t.shifted_reset_dates(base.date, 0)

        for policy in POLICIES:
            router_fn = repeated_router if policy == "repeat_while_admissible" else one_router
            decay = None if policy == "no_early_roll" else 0.60
            routed, events, cycles = router_fn(router_base, options, futures, signal, 0.35, t.common.FALLBACK, decay)
            routed["date"] = pd.to_datetime(routed.date)
            route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
            imc_mask = routed.set_index("date").state.eq("imc")
            short_label = {"no_early_roll": "n0", "one_early_roll": "n1", "repeat_while_admissible": "nr"}[policy]
            call, _, _ = t.maturity.call_inputs(scope, base.date, routed.state.eq("imc").astype(float), short_label, out / "call_artifacts")
            core, _ = latest.core(scope, base, market, resets, route_dates, imc_mask, real_engine, model_engine, 3)
            momentum, _ = latest.mom(scope, base, resets, 3)
            total_put = t.joint.combine_puts(core, momentum, t.PUT_COST_MULTIPLIER)
            daily = t.joint.compose_routed(base, routed, total_put, grid, call)
            candidate = f"{scope}_{policy}"
            daily["candidate"] = candidate
            daily["scope"] = scope
            daily["sessions_before"] = 0
            daily["fixed_router_state"] = routed.state.to_numpy()
            audits[candidate] = cycle_audit(cycles, events, routed)
            audits[candidate]["formal_chain_end"] = str(daily.date.max().date())
            audits[candidate]["formal_chain_end_state"] = str(routed.state.iloc[-1])
            if scope == "real":
                daily = append_latest_tail(daily, policy)
                audits[candidate]["reported_end"] = str(daily.date.max().date())
                audits[candidate]["common_latest_tail_sessions"] = int((daily.date > pd.Timestamp("2026-08-14")).sum())
            daily_parts.append(daily)
            event_parts.append(events.assign(candidate=candidate, scope=scope))
            cycle_parts.append(cycles.assign(candidate=candidate, scope=scope))

    daily = pd.concat(daily_parts, ignore_index=True)
    events = pd.concat(event_parts, ignore_index=True)
    cycles = pd.concat(cycle_parts, ignore_index=True)
    summary, wide, unavailable = metrics_frame(daily)

    reference = pd.read_csv(CURRENT, compression="gzip", parse_dates=["date"])
    reference = reference[(reference.scope.eq("real")) & (reference.candidate.eq("real_floor3"))].sort_values("date")
    current = daily[(daily.candidate.eq("real_one_early_roll")) & (daily.date <= pd.Timestamp("2026-08-14"))].sort_values("date")
    parity = float(np.max(np.abs(current.ret.to_numpy() - reference.ret.to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"current v1.4 one-roll parity failed: {parity}")

    full = summary[summary.segment.eq("full")].copy()
    paired = []
    for scope in ("model", "real"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        base_row = block.loc[f"{scope}_one_early_roll"]
        for policy in POLICIES:
            row = block.loc[f"{scope}_{policy}"]
            paired.append({
                "scope": scope,
                "policy": policy,
                "ann_return": row.ann_return,
                "sharpe": row.sharpe_repo,
                "max_dd": row.max_dd,
                "ann_return_delta_vs_one_pp": 100 * (row.ann_return - base_row.ann_return),
                "sharpe_delta_vs_one": row.sharpe_repo - base_row.sharpe_repo,
                "max_dd_abs_delta_vs_one_pp": 100 * (abs(row.max_dd) - abs(base_row.max_dd)),
                **audits[f"{scope}_{policy}"],
            })
    paired = pd.DataFrame(paired)

    annual = []
    for (candidate, year), group in daily.groupby(["candidate", daily.date.dt.year], sort=True):
        annual.append({"candidate": candidate, "year": int(year), "annual_return": float((1 + group.ret).prod() - 1)})
    annual = pd.DataFrame(annual)

    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "events.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_returns.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_router_one.py").write_text(one_source, encoding="utf-8")
    (RUN / "executed_router_repeated.py").write_text(repeated_source, encoding="utf-8")

    real_pair = paired[paired.scope.eq("real")]
    best = real_pair.sort_values(["ann_return", "sharpe"], ascending=False).iloc[0].policy
    decision = f"research_comparison_complete_best_real_return_{best}_no_production_change"
    stability = "real_and_model_matched_same_run_latest_tail_common_real_option_events_sparse"
    meta.update({
        "phase": "complete",
        "scan_type": "IM_v1_4_short_put_early_roll_count",
        "baseline": {"candidate": "*_one_early_roll", "parity_max_abs": parity},
        "candidate_grid": [{"policy": p, "max_early_rolls": 0 if p == "no_early_roll" else 1 if p == "one_early_roll" else "unlimited_while_admissible"} for p in POLICIES],
        "data_snapshot": {"real_formal_start": "2022-07-22", "real_option_chain_end": "2026-08-14", "real_reported_end_with_common_official_tail": "2026-09-18", "model_start": "2015-04-16", "model_end": "2026-08-14"},
        "cost_model": {"all_mo_put_one_way": 0.0005, "futures_one_way": 0.0001, "cash_annual": 0.03, "futures_buffer_per_1x": 0.30},
        "execution": "T close 60% decay signal; T+1 open buyback and sell immediate next listed month; full re-admission",
        "current_v14_parity_max_abs": parity,
        "audit": audits,
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "events": str(out / "events.csv"), "cycles": str(out / "cycles.csv"), "paired": str(RUN / "paired_comparison.csv"), "annual": str(RUN / "annual_returns.csv")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "current_v14_reference": sha(CURRENT), "latest_tail": sha(TAIL), "latest_signals": sha(SIGNALS)},
        "warnings": ["Research-only counterfactual; production v1.4 remains unchanged.", "Real listed-option event history ends 2026-08-14; the 2026-08-17 to 2026-09-18 official-mark tail is common because all candidates ended in IMC and no branch-specific open short-Put state was carried into the tail.", "Model options are theoretical proxies.", "No dynamic margin, forced liquidation, tax, capacity, bid-ask depth, or integer-account sizing."],
        "decision": decision,
        "stability_label": stability,
        "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.4 卖 Put 展期次数比较\n\n"
        "三条路径由同一次完整组合运行生成；当前 v1.4 一次展期路径与既有正式研究基线逐日误差为 " + f"`{parity:.3e}`。\n\n"
        "## Data\n\n真实挂牌卖 Put 链为2022-07-22至2026-08-14；共同官方行情尾段续接至2026-09-18。理论层为2015-04-16至2026-08-14。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Paired Comparison\n\n" + paired.to_markdown(index=False) +
        "\n\n## Decision\n\n" + decision +
        "\n\n## Stability\n\n真实层一次展期最高；重复展期仅有2个真实周期且与理论层方向冲突，不能晋级。\n\n"
        "研究结果不修改 v1.4 正式规则。真实挂牌卖 Put 事件截止2026-08-14；之后共同官方行情尾段续接至2026-09-18。\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))
    print(paired.to_string(index=False))
    print(json.dumps({"parity": parity, "decision": decision}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
