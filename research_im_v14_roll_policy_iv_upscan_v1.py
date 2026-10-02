"""IM v1.4 full-joint IV up-scan for one-roll versus repeated-roll policies."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_im_v14_mom_negative_put_qty_2_vs_3_v1 as latest
import research_im_v14_put_monthly_roll_timing_v1 as t
import research_im_v14_short_put_roll_count_v1 as prior


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260920_ic_im_im_v1_4_r1_full_joint_fixed_core_short_put_roll_policy_iv_threshold_35_to_45_x_one_versus_repeated_early_roll"
SPEC = ROOT / "docs" / "im_v14_roll_policy_iv_upscan_v1_spec.md"
CURRENT = prior.CURRENT
TAIL = prior.TAIL
THRESHOLDS = (0.35, 0.375, 0.40, 0.425, 0.45)
POLICIES = ("one", "repeat")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def tag(value: float) -> str:
    return str(int(round(value * 1000)))


def append_tail(daily: pd.DataFrame, candidate: str) -> pd.DataFrame:
    if daily.fixed_router_state.iloc[-1] != "imc":
        raise RuntimeError(f"{candidate} is not IMC at the formal-chain boundary")
    tail = pd.read_csv(TAIL, parse_dates=["date"])[["date", "ret"]]
    tail = tail[tail.date > daily.date.max()].copy()
    if tail.empty or str(tail.date.max().date()) != "2026-09-18":
        raise RuntimeError("latest official tail missing")
    tail["candidate"] = candidate
    tail["scope"] = "real"
    tail["sessions_before"] = 0
    tail["fixed_router_state"] = "imc"
    return pd.concat([daily, tail], ignore_index=True, sort=False)


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("refusing to overwrite a started run")
    one_router, repeat_router, one_source, repeat_source = prior.routers()
    real_engine, model_engine, _ = t.joint.component.profit_route_engines()
    weights = t.portfolio.current_momentum_weights()
    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    call_dir = out / "calls"
    call_dir.mkdir()
    all_daily, all_events, all_cycles, audits = [], [], [], {}

    for scope in ("model", "real"):
        base, _ = t.portfolio.rebuild_base(scope, weights)
        grid = t.portfolio.half_grid(scope)
        market, router_base, options, _, futures, _ = t.joint.quarterly_router_inputs(scope, base)
        signal = t.maturity.prepare_signal(router_base, options, "m1")
        resets, _ = t.shifted_reset_dates(base.date, 0)
        momentum, _ = latest.mom(scope, base, resets, 3)
        for policy in POLICIES:
            router_fn = one_router if policy == "one" else repeat_router
            for threshold in THRESHOLDS:
                ivtag = tag(threshold)
                candidate = f"{scope}_{policy}_iv{ivtag}"
                routed, events, cycles = router_fn(router_base, options, futures, signal, threshold, t.common.FALLBACK, 0.60)
                routed["date"] = pd.to_datetime(routed.date)
                route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
                mask = routed.set_index("date").state.eq("imc")
                call, _, _ = t.maturity.call_inputs(scope, base.date, routed.state.eq("imc").astype(float), f"{policy[0]}{ivtag}", call_dir)
                core, _ = latest.core(scope, base, market, resets, route_dates, mask, real_engine, model_engine, 3)
                total = t.joint.combine_puts(core, momentum, t.PUT_COST_MULTIPLIER)
                daily = t.joint.compose_routed(base, routed, total, grid, call)
                daily["candidate"] = candidate
                daily["scope"] = scope
                daily["sessions_before"] = 0
                daily["fixed_router_state"] = routed.state.to_numpy()
                audit = prior.cycle_audit(cycles, events, routed)
                audit.update(iv_threshold=threshold, policy=policy, chain_end_state=str(routed.state.iloc[-1]))
                if scope == "real":
                    daily = append_tail(daily, candidate)
                    audit["reported_end"] = str(daily.date.max().date())
                audits[candidate] = audit
                all_daily.append(daily)
                all_events.append(events.assign(candidate=candidate, scope=scope, iv_threshold=threshold, policy=policy))
                all_cycles.append(cycles.assign(candidate=candidate, scope=scope, iv_threshold=threshold, policy=policy))

    daily = pd.concat(all_daily, ignore_index=True)
    events = pd.concat(all_events, ignore_index=True)
    cycles = pd.concat(all_cycles, ignore_index=True)
    summary, wide, unavailable = t.build_metrics(daily)
    full = summary[summary.segment.eq("full")].copy()

    reference = pd.read_csv(CURRENT, compression="gzip", parse_dates=["date"])
    reference = reference[(reference.scope.eq("real")) & (reference.candidate.eq("real_floor3"))].sort_values("date")
    baseline = daily[(daily.candidate.eq("real_one_iv350")) & daily.date.le(pd.Timestamp("2026-08-14"))].sort_values("date")
    parity = float(np.max(np.abs(baseline.ret.to_numpy() - reference.ret.to_numpy())))
    if parity > 1e-12:
        raise RuntimeError(f"current baseline parity failed: {parity}")

    paired = []
    for scope in ("model", "real"):
        block = full[full.scope.eq(scope)].set_index("candidate")
        ref = block.loc[f"{scope}_one_iv350"]
        for policy in POLICIES:
            for threshold in THRESHOLDS:
                candidate = f"{scope}_{policy}_iv{tag(threshold)}"
                row = block.loc[candidate]
                paired.append({
                    "scope": scope, "policy": policy, "iv_threshold": threshold,
                    "ann_return": row.ann_return, "sharpe": row.sharpe_repo, "max_dd": row.max_dd,
                    "ann_return_delta_vs_current_pp": 100 * (row.ann_return - ref.ann_return),
                    "sharpe_delta_vs_current": row.sharpe_repo - ref.sharpe_repo,
                    "max_dd_abs_delta_vs_current_pp": 100 * (abs(row.max_dd) - abs(ref.max_dd)),
                    **audits[candidate],
                })
    paired = pd.DataFrame(paired)

    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "events.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_router_one.py").write_text(one_source, encoding="utf-8")
    (RUN / "executed_router_repeat.py").write_text(repeat_source, encoding="utf-8")

    real = paired[paired.scope.eq("real")].sort_values("ann_return", ascending=False)
    best = real.iloc[0]
    decision = "keep_current_one_iv35_no_production_change" if best.policy == "one" and abs(best.iv_threshold - 0.35) < 1e-12 else "retain_best_as_research_candidate_no_production_change"
    stability = "two_dimensional_matched_scan_real_events_sparse_model_direction_checked"
    meta.update({
        "phase": "complete", "scan_type": "IM_v1_4_roll_policy_x_iv_upscan",
        "baseline": {"candidate": "real_one_iv350", "parity_max_abs": parity},
        "candidate_grid": [{"policy": p, "iv_threshold": v} for p in POLICIES for v in THRESHOLDS],
        "data_snapshot": {"real_option_chain_start": "2022-07-22", "real_option_chain_end": "2026-08-14", "real_reported_end_common_tail": "2026-09-18", "model_start": "2015-04-16", "model_end": "2026-08-14"},
        "cost_model": {"mo_put_one_way": 0.0005, "futures_one_way": 0.0001, "cash_annual": 0.03, "futures_buffer_per_1x": 0.30},
        "audit": audits, "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "events": str(out / "events.csv"), "cycles": str(out / "cycles.csv"), "paired": str(RUN / "paired_comparison.csv")},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "current_v14_reference": sha(CURRENT), "latest_tail": sha(TAIL)},
        "warnings": ["Research only; formal v1.4 unchanged.", "Real listed-option behavior ends 2026-08-14; a common official-mark tail is appended only after all candidates return to IMC.", "Model layer uses theoretical options.", "No dynamic margin, forced liquidation, tax, capacity, bid-ask depth, or integer sizing."],
        "decision": decision, "stability_label": stability, "git_status_after": git_status(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.4 展期规则 × IV上扫\n\n"
        "## Data\n\n真实卖Put链2022-07-22至2026-08-14；共同官方行情尾段至2026-09-18。理论层2015-04-16至2026-08-14。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) +
        "\n\n## Paired Comparison\n\n" + paired.to_markdown(index=False) +
        "\n\n## Decision\n\n" + decision +
        "\n\n## Stability\n\n" + stability + "；真实事件稀疏，研究结果不修改正式规则。\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(real.to_string(index=False))
    print(json.dumps({"parity": parity, "decision": decision}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
