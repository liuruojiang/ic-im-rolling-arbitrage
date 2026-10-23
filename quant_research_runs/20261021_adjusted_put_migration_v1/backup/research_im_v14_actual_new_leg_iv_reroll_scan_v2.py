"""Causal IM v1.4 actual-new-leg IV reroll scan; research only."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tmp"))

import im_v14_actual_new_leg_iv_reroll_audit_20260920 as impl

t = impl.t
latest = impl.latest
prior = impl.prior
RUN = ROOT / "quant_param_scan_runs" / "20260920_ic_im_im_v1_4_r1_actual_new_leg_iv_unified_threshold_v7_iv30_32_5_35_x_one_repeat"
SPEC = ROOT / "docs" / "im_v14_actual_new_leg_iv_unified_threshold_scan_v7_spec.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def common_inputs(scope: str):
    weights = t.portfolio.current_momentum_weights()
    real_engine, model_engine, _ = t.joint.component.profit_route_engines()
    base, _ = t.portfolio.rebuild_base(scope, weights)
    grid = t.portfolio.half_grid(scope)
    market, router_base, options, _, futures, _ = t.joint.quarterly_router_inputs(scope, base)
    options = impl.add_close_iv(options)
    signal = t.maturity.prepare_signal(router_base, options, "m1")
    signal["execution_contract"] = signal["iv_contract"].astype(str)
    lookup = options.set_index(["contract", "date"])
    valid = []
    for row in signal.itertuples(index=False):
        sk = (str(row.iv_contract), pd.Timestamp(row.eval_date))
        ek = (str(row.iv_contract), pd.Timestamp(row.execution_date))
        sq = lookup.loc[sk] if sk in lookup.index else None
        eq = lookup.loc[ek] if ek in lookup.index else None
        valid.append(bool(
            sq is not None and pd.notna(sq.volume) and float(sq.volume) > 0
            and pd.notna(sq.open_interest) and float(sq.open_interest) > 0
            and eq is not None and pd.notna(eq.open) and float(eq.open) > 0
        ))
    signal["execution_open_valid"] = valid
    resets, _ = t.shifted_reset_dates(base.date, 0)
    momentum, _ = latest.mom(scope, base, resets, 3)
    return base, grid, market, router_base, options, futures, signal, resets, momentum, real_engine, model_engine


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("refusing overwrite of non-init scan")
    snapshot = RUN / "source_snapshot"
    snapshot.mkdir(exist_ok=False)
    source_files = [
        Path(__file__), SPEC,
        ROOT / "tmp" / "im_v14_actual_new_leg_iv_reroll_audit_20260920.py",
        Path(t.__file__), Path(latest.__file__), Path(prior.__file__),
        prior.CURRENT, prior.TAIL,
    ]
    source_manifest = []
    for source_path in source_files:
        target = snapshot / source_path.name
        target.write_bytes(source_path.read_bytes())
        source_manifest.append({"source": str(source_path), "snapshot": str(target), "bytes": source_path.stat().st_size, "sha256": sha(source_path)})
    (RUN / "source_manifest.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = RUN / "daily_outputs"
    out.mkdir(exist_ok=False)
    calls = out / "calls"; calls.mkdir()
    all_daily, all_events, all_cycles, all_reroll, summaries = [], [], [], [], []
    sources, expiry_map = {}, {}
    for scope in ("model", "real"):
        common = common_inputs(scope)
        expiry_map.update(common[4].drop_duplicates("contract").set_index("contract").actual_expiry.astype(str).to_dict())
        for policy in ("one", "repeat"):
            for threshold in impl.REROLL_THRESHOLDS:
                summary, reroll, source, daily, events, cycles = impl.run(
                    scope, policy, threshold, common, calls, append_real_tail=False
                )
                candidate = daily.candidate.iloc[0]
                summary["candidate"] = candidate
                if scope == "real":
                    tail = pd.read_csv(prior.TAIL, parse_dates=["date"])[["date", "ret"]]
                    extra = tail[tail.date > daily.date.max()].copy()
                    extra["candidate"] = candidate; extra["scope"] = scope; extra["sessions_before"] = 0
                    extra["fixed_router_state"] = "imc"
                    daily = pd.concat([daily, extra], ignore_index=True, sort=False)
                summaries.append(summary)
                all_daily.append(daily); all_events.append(events); all_cycles.append(cycles)
                all_reroll.extend(reroll); sources[policy] = source
    daily = pd.concat(all_daily, ignore_index=True)
    events = pd.concat(all_events, ignore_index=True)
    cycles = pd.concat(all_cycles, ignore_index=True)
    reroll = pd.DataFrame(all_reroll)
    reroll["old_actual_expiry"] = reroll.old_contract.map(expiry_map)
    scan_summary, window_metrics, unavailable = t.build_metrics(daily)
    summary = pd.DataFrame(summaries)
    full_lookup = scan_summary[scan_summary.segment.eq("full")].set_index("candidate")
    for index, row in summary.iterrows():
        metrics_row = full_lookup.loc[row.candidate]
        summary.loc[index, "ann_return"] = float(metrics_row.ann_return)
        summary.loc[index, "sharpe"] = float(metrics_row.sharpe_repo)
        summary.loc[index, "max_dd"] = float(metrics_row.max_dd)
    annual = []
    for (candidate, year), group in daily.groupby(["candidate", daily.date.dt.year], sort=True):
        annual.append({"candidate": candidate, "year": int(year), "annual_return": float((1 + group.ret).prod() - 1)})
    annual = pd.DataFrame(annual)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "events.csv", index=False)
    cycles.to_csv(out / "cycles.csv", index=False)
    reroll.to_csv(out / "reroll_event_audit.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "paired_comparison.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(RUN / "annual_returns.csv", index=False, encoding="utf-8-sig")
    scan_summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    window_metrics.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    for policy, source in sources.items():
        (RUN / f"executed_router_{policy}.py").write_text(source, encoding="utf-8")
    real = summary[summary.scope.eq("real")].sort_values(["ann_return", "sharpe"], ascending=False)
    best = real.iloc[0]
    decision = f"research_only_best_real_{best.policy}_rerolliv{int(best.reroll_iv_threshold*1000)}_no_production_change"
    meta.update({
        "phase": "complete", "scan_type": "actual_new_leg_own_IV_causal_reroll",
        "baseline": {"candidate_threshold": 0.35, "applies_to": "initial_and_reroll_actual_leg_own_iv"},
        "candidate_grid": [{"policy": p, "initial_iv_threshold": v, "reroll_iv_threshold": v} for p in ("one", "repeat") for v in impl.REROLL_THRESHOLDS],
        "data_snapshot": {"real_option_chain_end": "2026-08-14", "real_reported_end_common_tail": "2026-09-18", "model_end": "2026-08-14"},
        "cost_model": {"mo_put_one_way": 0.0005, "futures_one_way": 0.0001, "cash_annual": 0.03, "futures_buffer_per_1x": 0.30},
        "execution": "T locks initial/new contract, own IV, T volume/OI; T+1 positive finite opens for both reroll legs; atomic reroll; actual contract_month and expiry strictly later",
        "unavailable_segments": unavailable,
        "outputs": {**meta["outputs"], "daily": str(out / "daily.csv.gz"), "events": str(out / "events.csv"), "cycles": str(out / "cycles.csv"), "reroll_audit": str(out / "reroll_event_audit.csv"), "paired": str(RUN / "paired_comparison.csv"), "annual_returns": str(RUN / "annual_returns.csv"), "source_manifest": str(RUN / "source_manifest.json"), "source_snapshot": str(snapshot)},
        "source_hashes": {"script": sha(Path(__file__)), "spec": sha(SPEC), "helper": sha(ROOT / "tmp" / "im_v14_actual_new_leg_iv_reroll_audit_20260920.py"), "tail": sha(prior.TAIL)},
        "decision": decision, "stability_label": "causal_actual_new_leg_iv_real_and_model_event_audited_sparse_real_cycles",
        "git_status_after": subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM v1.4 卖 Put 统一 IV 门槛扫描 v7\n\n"
        "首次入场与再展期实际新腿自身IV统一使用30%/32.5%/35%；T日锁定合约与量/OI，T+1双腿有限正开盘并原子执行。\n\n"
        "## Data\n\n真实挂牌期权链截至2026-08-14；共同正式尾段至2026-09-18。理论层截至2026-08-14。\n\n"
        "## Full portfolio comparison\n\n" + summary.to_markdown(index=False) +
        "\n\n## Decision\n\n" + decision +
        "\n\n## Stability\n\n真实周期稀疏，且理论层与真实层最优阈值并不一致；不得自动晋级。\n\n"
        "研究专用；不修改v1.4正式规则。真实期权事件截至2026-08-14，所有候选回到IMC后才拼共同官方mark尾部至2026-09-18。\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as f:
        f.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(summary.to_string(index=False)); print(decision)


if __name__ == "__main__":
    main()
