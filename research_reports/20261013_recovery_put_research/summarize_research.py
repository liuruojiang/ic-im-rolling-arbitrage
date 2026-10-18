"""Summarize same-run outputs and plot the recovery trade-off, without reruns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
LABELS = {"A0": "旧参考路线", "A": "统一净损益，不加保险", "B": "普通条件保护尺度", "C": "强制最低保护"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    metrics, episodes, executions, legacy, data = [], [], [], [], {}
    for panel in ("listed", "model"):
        for product in ("IC", "IM"):
            for arm in ("A0", "A", "B", "C"):
                folder = OUT / "runs" / panel / product / arm
                daily = pd.read_csv(folder / "daily.csv.gz")
                events = pd.read_csv(folder / "events.csv.gz", low_memory=False)
                audit = read(folder / "audit.json")
                m = read(folder / "metrics.json")
                data[panel, product, arm] = daily, events, audit
                m.update(label=LABELS[arm],
                         native_prior_close_open_fallback_calls=audit["native_prior_close_open_fallback_call_count"],
                         buyer_open_proxy_calls=len(audit.get("buyer_open_proxies", [])))
                records = audit.get("recovery_cycle_records", [])
                m["seller_cycles"] = len(records) if arm != "A0" else None
                m["recovery_cycles"] = sum(bool(c.get("recovery_first_execution_date")) for c in records) if arm != "A0" else None
                m["recovery_future_days"] = int(daily.seller_route.eq("recovery_future").sum())
                m["right_censored_cycles"] = sum(bool(c.get("right_censored")) for c in records) if arm != "A0" else None
                metrics.append(m)
                if arm == "A0":
                    active = daily.loc[daily.seller_route.eq("recovery_future"), "date"]
                    legacy.append(dict(panel=panel, product=product, labelled_recovery_days=len(active),
                                       first_labelled_day=active.iloc[0] if len(active) else None,
                                       last_labelled_day=active.iloc[-1] if len(active) else None,
                                       final_route_missing=bool(pd.isna(daily.seller_route.iloc[-1])),
                                       exit_not_inferred_from_missing_label=True))
                insurance = events.loc[events.event.eq("trade") & events.symbol.fillna("").str.startswith("option|recovery_put|")]
                for _, e in insurance.iterrows():
                    executions.append(dict(panel=panel, product=product, arm=arm, date=e["date"],
                                           signal_date=e.get("signal_date"), contract=str(e.symbol).split("|")[-1],
                                           quantity_change=e.quantity_change, price=e.price, fee=e.fee,
                                           price_basis=e.price_basis, phase=e.phase))
                for c in records:
                    first = c.get("recovery_first_execution_date")
                    if not first:
                        continue
                    start = c["start_execution_date"]
                    end = c.get("exit_execution_date", daily.date.iloc[-1])
                    previous = daily.loc[daily.date.lt(start)]
                    initial_equity = float(previous.equity_close.iloc[-1]) if len(previous) else float(audit["initial_capital"])
                    period = daily.loc[daily.date.ge(start) & daily.date.le(end)]
                    held = daily.loc[daily.date.ge(first) & daily.date.lt(end) & daily.seller_route.eq("recovery_future")]
                    cycle_pnl = period.loc[period.recovery_cycle_active.astype(bool), "recovery_cycle_net_pnl"].to_numpy(float)
                    pnl = np.r_[0.0, cycle_pnl, float(c["final_net_pnl"])]
                    recovery_period = period.loc[period.date.ge(first) & period.recovery_cycle_active.astype(bool)]
                    rec_pnl = np.r_[recovery_period.recovery_cycle_net_pnl.to_numpy(float), float(c["final_net_pnl"])]
                    net_insurance = float(c["pnl_by_sleeve"]["recovery_put"])
                    trades = insurance.loc[insurance.date.ge(start) & insurance.date.le(end)]
                    multiplier = 10_000 if product == "IC" else 100
                    gross_paid = float((trades.quantity_change.clip(lower=0) * trades.price * multiplier).sum())
                    gross_received = float((-trades.quantity_change.clip(upper=0) * trades.price * multiplier).sum())
                    episodes.append(dict(panel=panel, product=product, arm=arm, cycle_id=c["cycle_id"],
                        start_execution_date=start, assigned_first_execution_date=c.get("assigned_first_execution_date"),
                        recovery_first_execution_date=first, exit_execution_date=c.get("exit_execution_date"),
                        closed=bool(c["closed"]), right_censored=bool(c.get("right_censored")),
                        recovery_future_trading_days=len(held), cycle_start_equity=initial_equity,
                        final_cycle_net_pnl=float(c["final_net_pnl"]),
                        minimum_cycle_net_pnl=float(pnl.min()), minimum_recovery_cycle_net_pnl=float(rec_pnl.min()),
                        minimum_recovery_cycle_pnl_pct=float(rec_pnl.min() / initial_equity),
                        insurance_net_pnl=net_insurance, insurance_net_pnl_pct_cycle_equity=net_insurance / initial_equity,
                        insurance_fees=float(c["fees_by_sleeve"]["recovery_put"]),
                        insurance_gross_premiums_paid=gross_paid, insurance_gross_premiums_received=gross_received,
                        negative_after_next_open=bool(c.get("negative_pnl_after_next_open"))))
    metrics = pd.DataFrame(metrics)
    episodes = pd.DataFrame(episodes)
    metrics.to_csv(OUT / "summary_metrics.csv", index=False)
    episodes.to_csv(OUT / "recovery_episodes.csv", index=False)
    pd.DataFrame(executions).to_csv(OUT / "insurance_execution_audit.csv", index=False)
    pd.DataFrame(legacy).to_csv(OUT / "legacy_route_coverage.csv", index=False)
    # Same calendar date and the common recovery interval help distinguish
    # endogenous whole-path changes from an isolated insurance return.
    im_episodes = episodes.loc[(episodes.panel == "model") & (episodes["product"] == "IM")]
    common_start = im_episodes.recovery_first_execution_date.max()
    common_end = im_episodes.exit_execution_date.min()
    stress = []
    for arm in ("A", "B", "C"):
        daily = data["model", "IM", arm][0]
        common = daily.loc[daily.date.ge(common_start) & daily.date.lt(common_end)]
        same_day = float(daily.loc[daily.date.eq("2020-02-03"), "return_net"].iloc[0])
        worst = daily.loc[daily.return_net.idxmin()]
        stress.append(dict(arm=arm, same_date="2020-02-03", same_date_whole_account_return=same_day,
                           own_worst_day=str(worst.date), own_worst_return=float(worst.return_net),
                           common_recovery_start=common_start, common_recovery_end_exclusive=common_end,
                           common_recovery_worst_return=float(common.return_net.min())))
    pd.DataFrame(stress).to_csv(OUT / "im_model_stress_comparison.csv", index=False)
    colors = {"A": "#526274", "B": "#D58A19", "C": "#157F83"}
    fig = plt.figure(figsize=(9, 9), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=[1.45, 1])
    ax = fig.add_subplot(grid[0, :])
    for arm in ("A", "B", "C"):
        e = im_episodes.loc[im_episodes.arm.eq(arm)].iloc[0]
        d = data["model", "IM", arm][0]
        z = d.loc[d.date.ge(e.recovery_first_execution_date) & d.date.lt(e.exit_execution_date)]
        dates = list(pd.to_datetime(z.date)) + [pd.Timestamp(e.exit_execution_date)]
        pnl = np.r_[z.recovery_cycle_net_pnl.to_numpy(float), e.final_cycle_net_pnl] / e.cycle_start_equity * 100
        ax.plot(dates, pnl, label={"A": "A: no recovery Put", "B": "B: conditional scale", "C": "C: minimum floor"}[arm], color=colors[arm], linewidth=1.8)
    ax.axhline(0, color="#444444", linewidth=.8)
    ax.set(title="IM recovery: net seller-cycle P&L", ylabel="% of cycle-entry account equity")
    ax.legend(fontsize=9, frameon=False)
    ax.grid(alpha=.2)
    ax.tick_params(axis="x", rotation=20)
    ax2 = fig.add_subplot(grid[1, 0])
    days = [int(im_episodes.loc[im_episodes.arm.eq(arm), "recovery_future_trading_days"].iloc[0]) for arm in ("A", "B", "C")]
    bars = ax2.bar(["A", "B", "C"], days, color=list(colors.values()))
    ax2.bar_label(bars, padding=3)
    ax2.set(title="Recovery holding days", ylabel="Trading days", ylim=(0, max(days) * 1.2))
    ax2.grid(axis="y", alpha=.2)
    ax3 = fig.add_subplot(grid[1, 1])
    values = [row["same_date_whole_account_return"] * 100 for row in stress]
    bars = ax3.bar(["A", "B", "C"], values, color=list(colors.values()))
    ax3.bar_label(bars, labels=[f"{value:.2f}%" for value in values], padding=3)
    ax3.set(title="2020-02-03 account return", ylabel="Daily return (%)", ylim=(-10, 1))
    ax3.grid(axis="y", alpha=.2)
    fig.suptitle("Recovery Put research — PRE-LISTING MODEL ONLY\nSame-rule account comparisons; endogenous routes differ", fontsize=13)
    fig.savefig(OUT / "im_model_recovery_tradeoff.png", dpi=150)
    plt.close(fig)
    summary = {"classification": "research_only; no production promotion",
               "arms": LABELS, "metrics": metrics.replace({np.nan: None}).to_dict(orient="records"),
               "recovery_episodes": episodes.replace({np.nan: None}).to_dict(orient="records"),
               "model_im_stress": stress, "legacy_route_coverage": legacy,
               "scope": {"listed": "Frozen listed-date quotes, theoretical market-maker fills; ends Aug 2026",
                         "model": "2015-04-16 to 2022-07-21 reconstructed model, not actual historical option trades"},
               "attribution_boundary": "A/B/C endogenous full account paths; changes are not isolated insurance-only returns. A0-to-A is an accounting/route change, not insurance benefit."}
    (OUT / "results_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(metrics[["panel", "product", "arm", "cagr_252", "max_drawdown", "worst_day", "recovery_future_days"]].to_string(index=False))
    print(episodes[["panel", "product", "arm", "recovery_future_trading_days", "insurance_net_pnl_pct_cycle_equity"]].to_string(index=False))


if __name__ == "__main__":
    main()
