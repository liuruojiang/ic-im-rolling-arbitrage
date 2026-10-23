"""Read-only diagnostic: apply the certified 2015 tier-3 reconstruction to v3.

The strategy code and frozen outputs are not edited.  This harness patches the
authoritative-state loader in memory, reruns only the theoretical/model layer,
and writes a separate diagnostic folder under the v3 run.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import research_imc_current_core_put_decay50_60_router_fresh_v1 as pair
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as parityfix


ROOT = Path(__file__).resolve().parent
V3 = ROOT / "quant_param_scan_runs" / "20260916_imc_coreput_highiv_short95_decay50_60_adversarial_v3"
STEP2 = ROOT / "quant_param_scan_runs" / "20260907_im_put_early_valuation_step2"
OUT = V3 / "early_valuation_audit"


def metrics(group: pd.DataFrame) -> dict[str, object]:
    ret = group.return_net.astype(float).reset_index(drop=True)
    nav = (1 + ret).cumprod()
    dd = nav / nav.cummax() - 1
    trough = int(dd.idxmin())
    peak = int(nav.iloc[: trough + 1].idxmax())
    return {
        "candidate": group.candidate.iloc[0],
        "rows": len(group),
        "ann_return": float(nav.iloc[-1] ** (252 / len(group)) - 1),
        "max_dd": float(dd.iloc[trough]),
        "dd_peak": group.date.iloc[peak].date().isoformat(),
        "dd_trough": group.date.iloc[trough].date().isoformat(),
    }


def main() -> None:
    if OUT.exists():
        raise RuntimeError(f"Refusing to overwrite {OUT}")
    OUT.mkdir()

    original_loader = common.policy.load_authoritative_local_state
    original_state, original_meta = original_loader()
    original_state = original_state.copy()
    original_state["date"] = pd.to_datetime(original_state.date)
    rebuilt = pd.read_csv(STEP2 / "early_valuation_rebuilt.csv", parse_dates=["date"])
    certified = rebuilt.set_index("date").certified_tier.astype(int)
    if not certified.eq(3).all():
        raise RuntimeError("The frozen early reconstruction is not uniformly tier 3")

    patched_state = original_state.copy()
    mapped = patched_state.date.map(certified)
    patch_mask = mapped.notna() & patched_state.date.le(pd.Timestamp("2015-10-16"))
    before = patched_state.loc[patch_mask, ["date", "valuation_tier", "momentum_120"]].copy()
    patched_state.loc[patch_mask, "valuation_tier"] = mapped.loc[patch_mask].astype(int)
    after = patched_state.loc[patch_mask, ["date", "valuation_tier", "momentum_120"]].copy()
    comparison = before.merge(after, on="date", suffixes=("_v3", "_certified"))
    comparison["tier_changed"] = comparison.valuation_tier_v3 != comparison.valuation_tier_certified
    comparison.to_csv(OUT / "valuation_state_comparison.csv", index=False)

    def patched_loader():
        return patched_state.copy(), original_meta

    common.policy.load_authoritative_local_state = patched_loader
    common.router.policy.load_authoritative_local_state = patched_loader

    router_fn, _ = parityfix.parity_fixed_runner()
    real_put_engine, model_put_engine, _ = common.patched_put_engines()
    corrected_daily, corrected_trades, corrected_signal, corrected_audit = pair.run_layer(
        "model", router_fn, real_put_engine, model_put_engine
    )
    corrected_daily.to_csv(OUT / "corrected_model_daily.csv.gz", index=False, compression="gzip")
    corrected_trades.to_csv(OUT / "corrected_model_core_put_trades.csv", index=False)
    corrected_signal.to_csv(OUT / "corrected_model_signal_audit.csv", index=False)

    old_daily = pd.read_csv(V3 / "daily_outputs" / "daily.csv.gz", parse_dates=["date"])
    old_signal = pd.read_csv(V3 / "daily_outputs" / "model_signal_audit.csv", parse_dates=["eval_date", "execution_date"])
    old_metrics = pd.DataFrame(
        [metrics(g.sort_values("date")) for _, g in old_daily[old_daily.candidate.str.startswith("model_")].groupby("candidate")]
    ).assign(version="v3_missing_early_valuation")
    new_metrics = pd.DataFrame(
        [metrics(g.sort_values("date")) for _, g in corrected_daily.groupby("candidate")]
    ).assign(version="certified_tier3_early_valuation")
    metric_comparison = pd.concat([old_metrics, new_metrics], ignore_index=True)
    metric_comparison.to_csv(OUT / "metric_comparison.csv", index=False)

    cutoff = pd.Timestamp("2015-10-16")
    signal_compare = old_signal[old_signal.eval_date.le(cutoff)][["eval_date", "execution_date", "short_put_permission"]].merge(
        corrected_signal[corrected_signal.eval_date.le(cutoff)][["eval_date", "execution_date", "short_put_permission"]],
        on=["eval_date", "execution_date"], suffixes=("_v3", "_certified")
    )
    signal_compare["permission_changed"] = signal_compare.short_put_permission_v3 != signal_compare.short_put_permission_certified
    signal_compare.to_csv(OUT / "short_put_permission_comparison.csv", index=False)

    focus = metric_comparison[metric_comparison.candidate.isin([
        "model_bare_monthly_imc",
        "model_monthly_imc_current_core_put102",
        "model_iv350_core_put_to_short95_decay60",
    ])]
    summary = pd.DataFrame([{
        "patched_state_days": int(patch_mask.sum()),
        "tier_changed_days": int(comparison.tier_changed.sum()),
        "early_permission_days_v3": int(signal_compare.short_put_permission_v3.sum()),
        "early_permission_days_certified": int(signal_compare.short_put_permission_certified.sum()),
        "permission_changed_days": int(signal_compare.permission_changed.sum()),
        "old_iv35_route_switches": int(old_daily[(old_daily.candidate.eq("model_iv350_core_put_to_short95_decay60")) & (old_daily.date.le(cutoff))].route.eq("high_iv_permitted_short_put").sum()),
        "corrected_iv35_route_switches": int(corrected_daily[(corrected_daily.candidate.eq("model_iv350_core_put_to_short95_decay60")) & (corrected_daily.date.le(cutoff))].route.eq("high_iv_permitted_short_put").sum()),
    }])
    summary.to_csv(OUT / "audit_summary.csv", index=False)
    (OUT / "report.md").write_text(
        "# adversarial_v3早期估值独立复核\n\n"
        "本诊断只在内存中把冻结step2的2015-10-19以前certified_tier接入同一v3引擎，未修改生产或既有正式产物。\n\n"
        "## Signal differences\n\n" + summary.to_markdown(index=False) + "\n\n"
        "## Metric comparison\n\n" + focus.to_markdown(index=False) + "\n\n"
        "## Interpretation\n\n"
        "原v3早期把缺失估值当作低档，允许高IV卖Put并缺少核心Put；这与冻结的早期certified_tier=3直接冲突。"
        "因此原理论层MaxDD不能作为当前规则的有效风险度量。\n\n"
        "本次影响诊断仍不能替代正式重跑：v3调度器缺少step2的2015-04-15评估、2015-04-16首次挂牌例外，"
        "所以诊断路径在2015-04-17才建立核心Put；同时v3使用全1倍核心Put，step2核心腿为0.5倍口径。\n",
        encoding="utf-8",
    )
    print(summary.to_string(index=False))
    print(focus.to_string(index=False))


if __name__ == "__main__":
    main()
