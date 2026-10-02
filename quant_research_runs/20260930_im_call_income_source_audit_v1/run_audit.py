"""Research-only attribution from the frozen 2026-09-30 native account journal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "quant_param_scan_runs/20260930_im_call_greed_front_delta_boundary_v1"
OUT = Path(__file__).resolve().parent
CASES = ("no_call_fix6", "greed_cross_front_d20", "greed_cross_front_d40")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    events = pd.read_csv(SOURCE / "events.csv.gz")
    daily = pd.read_csv(SOURCE / "daily_outputs.csv.gz")
    opens = pd.read_csv(SOURCE / "account_call_open_events.csv")
    signals = pd.read_csv(SOURCE / "signal_inputs.csv.gz")
    source_summary = pd.read_csv(SOURCE / "scan_summary.csv")
    events = events[events.candidate.isin(CASES)].copy()
    daily = daily[daily.candidate.isin(CASES)].copy()
    # The replay explicitly starts from this capital. Each journal must bridge
    # from that capital to the account's final equity without a residual.
    initial = 27_604_800.0
    final_equity = daily.groupby("candidate", sort=False).tail(1).set_index("candidate").equity_close
    journal_equity = events.groupby("candidate").equity_change.sum()
    for case in CASES:
        assert abs(journal_equity[case] - (final_equity[case] - initial)) < 0.01

    def component(row: pd.Series) -> str:
        if row.event in ("trade", "mark"):
            parts = str(row.symbol).split("|")
            return f"{row.event}|{parts[0]}|{parts[1]}" if len(parts) > 1 else f"{row.event}|{row.kind}"
        return str(row.event)

    events["component"] = events.apply(component, axis=1)
    bridge = (events.groupby(["candidate", "component"], as_index=False).equity_change.sum()
              .pivot(index="component", columns="candidate", values="equity_change").fillna(0))
    for case in CASES[1:]:
        bridge[f"{case}_less_no_call"] = bridge[case] - bridge[CASES[0]]
    bridge.reset_index().to_csv(OUT / "account_component_bridge.csv", index=False, encoding="utf-8-sig")

    episodes = []
    phases = []
    verifications = {}
    for case in CASES[1:]:
        call = events[(events.candidate == case) & (events.event == "trade") &
                      (events.symbol.fillna("").str.startswith("option|call|"))].copy()
        call["gross_cashflow"] = -call.quantity_change * call.price * 100.0
        assert np.allclose(call.gross_cashflow - call.fee, call.cash_change, atol=0.001)
        start_dates = set(opens[opens.candidate == case].execution_date)
        assert len(start_dates) == (9 if case.endswith("d20") else 8)
        ep = None
        records = []
        for i, row in enumerate(call.itertuples(index=False)):
            if row.quantity_change < 0 and row.date in start_dates:
                assert ep is None
                ep = {"candidate": case, "open_date": row.date, "gross_initial_premium": 0.0,
                      "gross_replacement_premium": 0.0, "gross_rescue_buyback": 0.0,
                      "gross_final_buyback": 0.0, "fees": 0.0, "sides": 0, "rescue_rolls": 0}
                label = "initial_sale"
            elif row.quantity_change < 0:
                assert ep is not None
                label = "replacement_sale"
                ep["rescue_rolls"] += 1
            else:
                assert ep is not None
                label = "buyback_pending"
            records.append((row, ep, label))
            ep["sides"] += 1
            ep["fees"] += row.fee
            if label == "initial_sale":
                ep["gross_initial_premium"] += row.gross_cashflow
            elif label == "replacement_sale":
                ep["gross_replacement_premium"] += row.gross_cashflow
            # Keep episode open until a buyback has no same-day replacement.
            if row.quantity_change > 0:
                next_row = call.iloc[i + 1] if i + 1 < len(call) else None
                is_roll = next_row is not None and next_row.date == row.date and next_row.quantity_change < 0
                key = "gross_rescue_buyback" if is_roll else "gross_final_buyback"
                ep[key] += row.gross_cashflow
                records[-1] = (row, ep, "rescue_buyback" if is_roll else "final_buyback")
                if not is_roll:
                    ep["close_date"] = row.date
                    ep["net_call_pnl"] = sum(ep[key] for key in (
                        "gross_initial_premium", "gross_replacement_premium",
                        "gross_rescue_buyback", "gross_final_buyback")) - ep["fees"]
                    episodes.append(ep)
                    ep = None
        assert ep is None
        assert len(records) == len(call)
        assert sum(x["sides"] for x in episodes if x["candidate"] == case) == len(call)
        assert abs(sum(x["net_call_pnl"] for x in episodes if x["candidate"] == case) - call.cash_change.sum()) < 0.01
        for row, _, label in records:
            phases.append({"candidate": case, "date": row.date, "phase": label,
                           "symbol": row.symbol, "gross_cashflow": row.gross_cashflow,
                           "fee": row.fee, "net_cashflow": row.cash_change,
                           "quantity": row.quantity_change, "price": row.price})
        verifications[case] = {"call_sides": len(call), "episodes": len(start_dates),
                               "rescue_rolls": sum(x["rescue_rolls"] for x in episodes if x["candidate"] == case),
                               "call_cashflow_parity_error": float(sum(x["net_call_pnl"] for x in episodes if x["candidate"] == case) - call.cash_change.sum())}

    ep_df = pd.DataFrame(episodes)
    ep_df.to_csv(OUT / "episode_attribution.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(phases).to_csv(OUT / "call_trade_phase_cashflows.csv", index=False, encoding="utf-8-sig")
    summary = []
    for case in CASES:
        ep = ep_df[ep_df.candidate == case]
        direct = float(ep.net_call_pnl.sum())
        case_phases = [p for p in phases if p["candidate"] == case]
        one_point_adverse_cny = sum(abs(p["quantity"]) * 100.0 for p in case_phases)
        account_edge = float(final_equity[case] - final_equity[CASES[0]])
        scan = source_summary[(source_summary.candidate == case) & (source_summary.segment == "full")].iloc[0]
        summary.append({"candidate": case, "start": scan.start, "end": scan.end, "days": int(scan.rows),
                        "cagr": scan.ann_return, "max_dd": scan.max_dd,
                        "final_equity": final_equity[case], "account_edge_vs_no_call": account_edge,
                        "direct_call_pnl_net": direct, "indirect_account_feedback": account_edge - direct,
                        "gross_initial_premium": ep.gross_initial_premium.sum(),
                        "gross_replacement_premium": ep.gross_replacement_premium.sum(),
                        "gross_rescue_buyback": ep.gross_rescue_buyback.sum(),
                        "gross_final_buyback": ep.gross_final_buyback.sum(),
                        "call_fees": ep.fees.sum(), "episodes": len(ep),
                        "winning_episodes": int((ep.net_call_pnl > 0).sum()),
                        "losing_episodes": int((ep.net_call_pnl < 0).sum()),
                        "rescue_rolls": int(ep.rescue_rolls.sum()), "call_sides": int(ep.sides.sum()),
                        "one_point_adverse_static_cost_cny": one_point_adverse_cny,
                        "direct_call_pnl_after_static_5point_adverse": direct - 5 * one_point_adverse_cny,
                        "static_points_to_erase_direct_call_pnl":
                            direct / one_point_adverse_cny if one_point_adverse_cny else np.nan})
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    assert np.allclose(bridge[[f"{case}_less_no_call" for case in CASES[1:]]].sum().values,
                       summary_df.set_index("candidate").loc[list(CASES[1:]), "account_edge_vs_no_call"].values,
                       atol=0.01)

    # Only signal-date information is in this ex-ante diagnostic. Fill price is T+1.
    entry = opens[opens.candidate.isin(CASES[1:])].merge(
        signals, on="signal_date", how="left", validate="many_to_one")
    entry = entry.merge(ep_df[["candidate", "open_date", "net_call_pnl", "rescue_rolls"]],
                        left_on=["candidate", "execution_date"], right_on=["candidate", "open_date"],
                        how="left", validate="one_to_one")
    assert entry.net_call_pnl.notna().all()
    entry["outcome"] = np.where(entry.net_call_pnl > 0, "win", "loss")
    entry.to_csv(OUT / "entry_features_outcomes.csv", index=False, encoding="utf-8-sig")
    prior_index_study = ROOT / "quant_research_runs/20260930_im_greed75_index_event_study_v1/account_episode_attribution.csv"
    if prior_index_study.exists():
        index_ep = pd.read_csv(prior_index_study)
        prior_check = ep_df[ep_df.candidate == "greed_cross_front_d40"].merge(
            index_ep, left_on="open_date", right_on="open_execution_date", validate="one_to_one")
        assert len(prior_check) == 8
        assert np.allclose(prior_check.net_call_pnl, prior_check.call_trade_cash_pnl_net_cny, atol=0.01)
        verifications["prior_index_study_crosscheck"] = {
            "sha256": sha256(prior_index_study),
            "matched_episodes": len(prior_check),
            "all_open_to_flat_index_returns_negative": bool((prior_check.index_return_open_to_flat_close < 0).all()),
            "call_pnl_max_abs_difference": float(abs(prior_check.net_call_pnl - prior_check.call_trade_cash_pnl_net_cny).max()),
        }
    verifications["source_sha256"] = {name: sha256(SOURCE / name) for name in (
        "events.csv.gz", "daily_outputs.csv.gz", "account_call_open_events.csv",
        "signal_inputs.csv.gz", "scan_summary.csv")}
    verifications["journal_final_equity_parity_max_abs"] = float(max(
        abs(journal_equity[case] - (final_equity[case] - initial)) for case in CASES))
    (OUT / "verification.json").write_text(json.dumps(verifications, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summary_df.to_string(index=False))
    print(ep_df[["candidate", "open_date", "net_call_pnl", "rescue_rolls"]].to_string(index=False))


if __name__ == "__main__":
    main()
