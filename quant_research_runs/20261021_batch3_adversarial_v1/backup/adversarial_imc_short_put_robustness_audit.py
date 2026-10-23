"""Adversarial diagnostics for the matched IMC/core-Put/high-IV-short-Put run.

Read-only with respect to strategy and production code.  The script consumes the
single-run daily output and writes only diagnostic tables beside that run.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_coreput_highiv_short95_earlyvaluation_adversarial_v4"
SOURCE = RUN / "daily_outputs" / "daily.csv.gz"
OUT = RUN / "adversarial_audit"


def annualized(ret: pd.Series) -> float:
    ret = ret.dropna().astype(float)
    return float(np.prod(1.0 + ret) ** (252.0 / len(ret)) - 1.0) if len(ret) else np.nan


def log_excess(candidate: pd.Series, baseline: pd.Series) -> pd.Series:
    return np.log1p(candidate.astype(float)) - np.log1p(baseline.astype(float))


def aligned(daily: pd.DataFrame, candidate: str, baseline: str) -> pd.DataFrame:
    c = daily.loc[daily.candidate.eq(candidate)].set_index("date")
    b = daily.loc[daily.candidate.eq(baseline)].set_index("date")
    x = c.join(b[["return_net"]].rename(columns={"return_net": "baseline_return"}), how="inner")
    x["log_excess"] = log_excess(x.return_net, x.baseline_return)
    return x


def find_cycles(x: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    opens = x.index[x.action.isin(["imc_to_short_put_open", "cash_to_short_put_open"])].tolist()
    closes = x.index[x.action.eq("cash_to_imc_open")].tolist()
    cycles: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for start in opens:
        later = [day for day in closes if day >= start]
        cycles.append((start, later[0] if later else x.index.max()))
    return cycles


def main() -> None:
    OUT.mkdir(exist_ok=True)
    daily = pd.read_csv(SOURCE, parse_dates=["date"])

    candidates = [c for c in daily.candidate.unique() if "_iv" in c]
    grid_rows = []
    yearly_rows = []
    cycle_rows = []
    loo_rows = []
    concentration_rows = []
    top_day_rows = []
    parity_rows = []

    for candidate in candidates:
        layer = candidate.split("_", 1)[0]
        bare = f"{layer}_bare_monthly_imc"
        core = f"{layer}_monthly_imc_current_core_put102"
        cand = daily.loc[daily.candidate.eq(candidate)].sort_values("date")
        # Strip the core-Put overlay back out of the candidate.  Before the
        # first route switch this implied router return must match bare IMC if
        # the comparison truly changes only the high-IV route.
        bare_series = daily.loc[daily.candidate.eq(bare)].set_index("date").return_net
        cand_indexed = cand.set_index("date")
        implied_router = (
            cand_indexed.return_net
            - cand_indexed.core_put_pnl_ret
            + cand_indexed.core_put_cost_rate
            + cand_indexed.core_put_mark_fraction * (1.03 ** (1 / 252) - 1)
        )
        switch_days = cand_indexed.index[cand_indexed.route.eq("high_iv_permitted_short_put")]
        first_switch = switch_days.min() if len(switch_days) else cand_indexed.index.max() + pd.Timedelta(days=1)
        pre = cand_indexed.index < first_switch
        pre_log = log_excess(implied_router.loc[pre], bare_series.loc[pre])
        imc_days = cand_indexed.state.eq("imc")
        imc_log = log_excess(implied_router.loc[imc_days], bare_series.loc[imc_days])
        parity_rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "first_switch": first_switch.date().isoformat(),
                "pre_switch_days": int(pre.sum()),
                "pre_switch_different_days": int((pre_log.abs() > 1e-12).sum()),
                "pre_switch_router_to_bare_wealth_ratio": float(np.exp(pre_log.sum())),
                "imc_state_days": int(imc_days.sum()),
                "imc_state_different_days": int((imc_log.abs() > 1e-12).sum()),
                "imc_state_router_to_bare_wealth_ratio": float(np.exp(imc_log.sum())),
            }
        )
        for baseline_kind, baseline in (("bare_imc", bare), ("core_put", core)):
            x = aligned(daily, candidate, baseline)
            total_log = float(x.log_excess.sum())
            positive_log = float(x.log_excess.clip(lower=0).sum())
            grid_rows.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "baseline_kind": baseline_kind,
                    "candidate_ann": annualized(x.return_net),
                    "baseline_ann": annualized(x.baseline_return),
                    "ann_delta_pp": 100 * (annualized(x.return_net) - annualized(x.baseline_return)),
                    "wealth_ratio": float(np.exp(total_log)),
                    "different_days": int((x.log_excess.abs() > 1e-12).sum()),
                }
            )
            by_year = x.assign(year=x.index.year).groupby("year")
            for year, part in by_year:
                yearly_rows.append(
                    {
                        "layer": layer,
                        "candidate": candidate,
                        "baseline_kind": baseline_kind,
                        "year": int(year),
                        "days": len(part),
                        "candidate_return": float(np.prod(1 + part.return_net) - 1),
                        "baseline_return": float(np.prod(1 + part.baseline_return) - 1),
                        "log_excess": float(part.log_excess.sum()),
                        "wealth_ratio": float(np.exp(part.log_excess.sum())),
                    }
                )
            abs_ranked = x.log_excess.abs().sort_values(ascending=False)
            for rank, day in enumerate(abs_ranked.index[:20], 1):
                top_day_rows.append(
                    {
                        "layer": layer,
                        "candidate": candidate,
                        "baseline_kind": baseline_kind,
                        "rank": rank,
                        "date": day.date().isoformat(),
                        "candidate_return": float(x.loc[day, "return_net"]),
                        "baseline_return": float(x.loc[day, "baseline_return"]),
                        "log_excess": float(x.loc[day, "log_excess"]),
                        "state": x.loc[day, "state"],
                        "action": x.loc[day, "action"],
                    }
                )
            for k in (1, 3, 5, 10):
                days = abs_ranked.index[:k]
                signed = float(x.loc[days, "log_excess"].sum())
                concentration_rows.append(
                    {
                        "layer": layer,
                        "candidate": candidate,
                        "baseline_kind": baseline_kind,
                        "top_abs_days": k,
                        "signed_log_excess": signed,
                        "share_of_net_excess": signed / total_log if abs(total_log) > 1e-15 else np.nan,
                        "share_of_positive_excess": float(x.loc[days, "log_excess"].clip(lower=0).sum()) / positive_log if positive_log > 0 else np.nan,
                    }
                )

        # High-IV episode diagnostics are defined relative to the core-Put path.
        x = aligned(daily, candidate, core)
        cycles = find_cycles(cand.set_index("date"))
        for cycle_id, (start, end) in enumerate(cycles, 1):
            mask = (x.index >= start) & (x.index <= end)
            part = x.loc[mask]
            cycle_rows.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "cycle": cycle_id,
                    "start": start.date().isoformat(),
                    "end": end.date().isoformat(),
                    "days": int(mask.sum()),
                    "candidate_return": float(np.prod(1 + part.return_net) - 1),
                    "core_put_return": float(np.prod(1 + part.baseline_return) - 1),
                    "wealth_ratio_vs_core": float(np.exp(part.log_excess.sum())),
                    "log_excess_vs_core": float(part.log_excess.sum()),
                }
            )
            neutral = x.return_net.copy()
            neutral.loc[mask] = x.loc[mask, "baseline_return"]
            loo_rows.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "omitted_cycle": cycle_id,
                    "omitted_start": start.date().isoformat(),
                    "omitted_end": end.date().isoformat(),
                    "ann_after_neutralizing_cycle": annualized(neutral),
                    "core_put_ann": annualized(x.baseline_return),
                    "ann_delta_vs_core_pp": 100 * (annualized(neutral) - annualized(x.baseline_return)),
                }
            )

    grid = pd.DataFrame(grid_rows)
    yearly = pd.DataFrame(yearly_rows)
    cycles = pd.DataFrame(cycle_rows)
    loo = pd.DataFrame(loo_rows)
    concentration = pd.DataFrame(concentration_rows)
    grid.to_csv(OUT / "grid_comparison.csv", index=False)
    yearly.to_csv(OUT / "yearly_excess.csv", index=False)
    cycles.to_csv(OUT / "cycle_contributions.csv", index=False)
    loo.to_csv(OUT / "leave_one_cycle_out.csv", index=False)
    concentration.to_csv(OUT / "daily_concentration.csv", index=False)
    pd.DataFrame(top_day_rows).to_csv(OUT / "top_daily_excess.csv", index=False)
    pd.DataFrame(parity_rows).to_csv(OUT / "router_baseline_parity.csv", index=False)

    # Exact identity check for 50% vs 60% in each layer/IV threshold.
    pair_rows = []
    for layer in ("real", "model"):
        for iv in (350, 375, 400):
            a = daily[daily.candidate.eq(f"{layer}_iv{iv}_core_put_to_short95_decay50")].set_index("date").return_net
            b = daily[daily.candidate.eq(f"{layer}_iv{iv}_core_put_to_short95_decay60")].set_index("date").return_net
            diff = a - b
            pair_rows.append({"layer": layer, "iv": iv / 10, "different_days": int((diff.abs() > 1e-12).sum()), "max_abs_daily_diff": float(diff.abs().max()), "terminal_wealth_ratio_50_to_60": float(np.prod(1 + a) / np.prod(1 + b))})
    pd.DataFrame(pair_rows).to_csv(OUT / "decay50_vs_60_identity.csv", index=False)

    focus = grid[(grid.layer == "real") & (grid.candidate.str.contains("iv350"))]
    focus_cycles = cycles[(cycles.layer == "real") & (cycles.candidate.str.contains("iv350"))]
    report = "# 对抗性稳健性诊断：IMC＋核心Put＋高IV卖Put\n\n"
    report += "诊断只读取统一重算的逐日结果，不修改策略、生产或订单逻辑。\n\n"
    report += "## IV35真实挂牌层总览\n\n" + focus.to_markdown(index=False) + "\n\n"
    report += "## IV35真实挂牌层逐轮贡献\n\n" + focus_cycles.to_markdown(index=False) + "\n\n"
    report += "## 判定边界\n\n"
    parity_focus = pd.DataFrame(parity_rows)
    parity_pass = bool(
        parity_focus.loc[(parity_focus.layer == "real") & parity_focus.candidate.str.contains("iv350"), "pre_switch_different_days"].eq(0).all()
    )
    report += ("- 同基线检查通过：第一次高IV切换前，剥离核心Put后的router逐日严格复现纯滚IMC。\n" if parity_pass else "- 同基线检查失败：第一次高IV切换前router已偏离纯滚IMC。\n")
    report += "- 真实层只有3个IV35路由周期；逐轮和leave-one-cycle-out可衡量集中度，但不足以建立统计显著性。\n"
    report += "- 理论层使用事后平均2022-2026贴水校准、代理波动率和理论期权价格，不能作为独立样本或可成交历史。\n"
    report += "- 结果未覆盖盘口价差、冲击、动态保证金、强平、容量、税费和整数张数，因此不能据此晋级生产。\n"
    (OUT / "report.md").write_text(report, encoding="utf-8")
    print(grid.to_string(index=False))
    print("\nREAL IV35 CYCLES\n", focus_cycles.to_string(index=False))


if __name__ == "__main__":
    main()
