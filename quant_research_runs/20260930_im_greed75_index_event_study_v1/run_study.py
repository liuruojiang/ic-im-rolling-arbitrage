"""Descriptive CSI 1000 study after a Fear score downcrosses 75.

Research only: the Fear history is a later-downloaded snapshot, not PIT-certified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
SOURCE = ROOT / "quant_research_runs" / "20260928_csi1000_fear_greed_reproduction"
SCAN = ROOT / "quant_param_scan_runs" / "20260930_im_call_greed_front_delta_boundary_v1"
NATIVE = ROOT / "outputs" / "re_certification" / "icim_v14_fix4_recert_20260924" / "l5_native_signal_reconstruction_20260925"
CUTOFF = pd.Timestamp("2026-08-14")
HORIZONS = (1, 5, 10, 20, 40)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_frame() -> pd.DataFrame:
    manifest = json.loads((SOURCE / "data_manifest.json").read_text(encoding="utf-8"))
    fear_file = SOURCE / "inputs" / "fear_greed_full.csv"
    price_file = SOURCE / "inputs" / "csi1000_official_ohlc_response.json"
    assert sha256(fear_file) == manifest["fear_data_source"]["sha256"]
    assert sha256(price_file) == manifest["price_data_source"]["sha256"]

    fear = pd.read_csv(fear_file, parse_dates=["date"])[["date", "fear_greed_index"]]
    assert not fear.date.duplicated().any()
    payload = json.loads(price_file.read_text(encoding="utf-8"))
    assert payload.get("success") is True
    raw = pd.DataFrame(payload["data"])
    assert set(raw.indexCode.astype(str)) == {"000852"}
    prices = raw[["tradeDate", "open", "high", "low", "close"]].rename(columns={"tradeDate": "date"})
    prices["date"] = pd.to_datetime(prices["date"], format="%Y%m%d")
    for field in ("open", "high", "low", "close"):
        prices[field] = pd.to_numeric(prices[field], errors="raise")
    prices = prices.loc[prices.date.between(pd.Timestamp("2022-07-22"), CUTOFF)].sort_values("date").reset_index(drop=True)
    assert len(prices) == 986 and prices.date.iloc[-1] == CUTOFF
    assert not prices.date.duplicated().any()
    assert np.isfinite(prices[["open", "high", "low", "close"]].to_numpy()).all()
    assert (prices[["open", "high", "low", "close"]] > 0).all().all()

    frame = prices.merge(fear, on="date", how="left", validate="one_to_one")
    frame["fear_prev"] = frame.fear_greed_index.shift(1)
    frame["downcross75"] = frame.fear_prev.ge(75) & frame.fear_greed_index.lt(75)
    assert frame.fear_greed_index.isna().sum() == 1

    # Match the original Call scan's signal calendar and gate exactly.
    risk = pd.read_csv(NATIVE / "native_fix4_im_risk_signals_v1.csv.gz", parse_dates=["signal_date"])
    assert frame.date.equals(risk.signal_date)
    assert int(frame.downcross75.sum()) == 41
    return frame


def event_rows(frame: pd.DataFrame, opening_dates: set[pd.Timestamp]) -> pd.DataFrame:
    rows = []
    for h in HORIZONS:
        for i in range(len(frame) - h):
            signal = frame.iloc[i]
            if pd.isna(signal.fear_greed_index) or pd.isna(signal.fear_prev):
                continue
            path = frame.iloc[i : i + h + 1].close.to_numpy(float)
            running_peak = np.maximum.accumulate(path)
            future = path[1:]
            after_fill = np.nan
            if i + 1 + h < len(frame):
                after_fill = float(frame.close.iloc[i + 1 + h] / frame.close.iloc[i + 1] - 1)
            rows.append({
                "signal_date": signal.date.date().isoformat(),
                "signal_year": int(signal.date.year),
                "fear_prev": float(signal.fear_prev),
                "fear_now": float(signal.fear_greed_index),
                "downcross75": bool(signal.downcross75),
                "actual_d40_call_open": bool(signal.date in opening_dates),
                "horizon_sessions": h,
                "exit_date": frame.date.iloc[i + h].date().isoformat(),
                "signal_close": float(path[0]),
                "exit_close": float(path[-1]),
                "return_t_close_to_h_close": float(path[-1] / path[0] - 1),
                "return_t1_close_to_t1_plus_h_close": after_fill,
                "max_future_close_upside": float(future.max() / path[0] - 1),
                "interval_max_drawdown": float((path / running_peak - 1).min()),
            })
    return pd.DataFrame(rows)


def summary_rows(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h, group in events.groupby("horizon_sessions", sort=True):
        subsets = {
            "downcross75": group[group.downcross75],
            "all_other_days": group[~group.downcross75],
            "other_fear_below75": group[(~group.downcross75) & group.fear_now.lt(75)],
            "other_fear_55_to_below75": group[(~group.downcross75) & group.fear_now.ge(55) & group.fear_now.lt(75)],
            "downcross_with_d40_open": group[group.downcross75 & group.actual_d40_call_open],
            "downcross_without_d40_open": group[group.downcross75 & ~group.actual_d40_call_open],
        }
        for label, sub in subsets.items():
            r = sub.return_t_close_to_h_close
            fill = sub.return_t1_close_to_t1_plus_h_close.dropna()
            upside = sub.max_future_close_upside
            rows.append({
                "horizon_sessions": int(h), "group": label, "n": len(sub),
                "mean_return": float(r.mean()) if len(sub) else np.nan,
                "median_return": float(r.median()) if len(sub) else np.nan,
                "positive_fraction": float(r.gt(0).mean()) if len(sub) else np.nan,
                "mean_return_after_t1_close": float(fill.mean()) if len(fill) else np.nan,
                "n_after_t1_close": len(fill),
                "mean_max_future_close_upside": float(upside.mean()) if len(sub) else np.nan,
                "fraction_future_close_up_5pct": float(upside.ge(0.05).mean()) if len(sub) else np.nan,
                "mean_interval_max_drawdown": float(sub.interval_max_drawdown.mean()) if len(sub) else np.nan,
            })
    return pd.DataFrame(rows)


def episode_attribution(frame: pd.DataFrame, event_table: pd.DataFrame) -> pd.DataFrame:
    journal = pd.read_csv(SCAN / "events.csv.gz", parse_dates=["date", "signal_date"])
    trades = journal.loc[
        journal.candidate.eq("greed_cross_front_d40")
        & journal.event.eq("trade")
        & journal.symbol.fillna("").str.startswith("option|call|")
    ].sort_values(["date", "order_id"])
    dates = []
    qty = 0.0
    start = None
    signal_date = None
    for day, group in trades.groupby("date", sort=True):
        before = qty
        qty += float(group.quantity_change.sum())
        if before >= -1e-8 and qty < -1e-8:
            opened = group[group.quantity_change.lt(0)].iloc[-1]
            start = pd.Timestamp(day)
            signal_date = pd.Timestamp(opened.signal_date)
        if before < -1e-8 and qty >= -1e-8:
            assert start is not None and signal_date is not None
            dates.append((signal_date, start, pd.Timestamp(day)))
            start = None
            signal_date = None
    assert len(dates) == 8 and abs(qty) < 1e-7

    daily = pd.read_csv(SCAN / "daily_outputs.csv.gz", parse_dates=["date"])
    nav = daily.loc[daily.candidate.isin(["no_call_fix6", "greed_cross_front_d40"])].pivot(
        index="date", columns="candidate", values="nav"
    )
    close = frame.set_index("date").close
    records = []
    for signal_day, start_day, end_day in dates:
        position = nav.index.get_loc(start_day)
        before_day = nav.index[position - 1]
        incremental = float(
            np.log(nav.at[end_day, "greed_cross_front_d40"] / nav.at[before_day, "greed_cross_front_d40"])
            - np.log(nav.at[end_day, "no_call_fix6"] / nav.at[before_day, "no_call_fix6"])
        )
        episode_trades = trades.loc[trades.date.between(start_day, end_day)]
        episode_prices = close.loc[start_day:end_day]
        records.append({
            "signal_date": signal_day.date().isoformat(),
            "open_execution_date": start_day.date().isoformat(),
            "flat_execution_date": end_day.date().isoformat(),
            "holding_calendar_days": (end_day - start_day).days,
            "index_return_open_to_flat_close": float(close.at[end_day] / close.at[start_day] - 1),
            "index_max_close_upside_during_call": float(episode_prices.max() / close.at[start_day] - 1),
            "call_trade_cash_pnl_net_cny": float(episode_trades.cash_change.sum()),
            "call_trade_sides": len(episode_trades),
            "account_incremental_log_nav_vs_no_call": incremental,
        })
    result = pd.DataFrame(records)
    assert set(result.signal_date) == set(event_table.loc[event_table.actual_d40_call_open, "signal_date"])
    return result


def main() -> None:
    frame = source_frame()
    opens = pd.read_csv(SCAN / "account_call_open_events.csv")
    opening_dates = set(pd.to_datetime(opens.loc[opens.candidate.eq("greed_cross_front_d40"), "signal_date"]))
    assert len(opening_dates) == 8
    episodes = event_rows(frame, opening_dates)
    summary = summary_rows(episodes)
    account_episodes = episode_attribution(frame, episodes)
    forty = episodes.loc[episodes.horizon_sessions.eq(40) & episodes.downcross75].copy()
    position = pd.Series(np.arange(len(frame)), index=frame.date.dt.strftime("%Y-%m-%d"))
    clusters: list[list[object]] = []
    cluster: list[object] = []
    previous_position: int | None = None
    for row in forty.itertuples(index=False):
        current_position = int(position.at[row.signal_date])
        if previous_position is not None and current_position - previous_position > 40:
            clusters.append(cluster)
            cluster = []
        cluster.append(row)
        previous_position = current_position
    if cluster:
        clusters.append(cluster)
    cluster_sensitivity = {
        "forty_session_downcross_events": len(forty),
        "connected_forty_session_clusters": len(clusters),
        "first_event_per_cluster_mean_return": float(np.mean([
            c[0].return_t_close_to_h_close for c in clusters
        ])),
        "last_event_per_cluster_mean_return": float(np.mean([
            c[-1].return_t_close_to_h_close for c in clusters
        ])),
    }
    account_corr = {
        "episode_count": len(account_episodes),
        "pearson_terminal_index_return_vs_call_cash_pnl": float(account_episodes[
            "index_return_open_to_flat_close"
        ].corr(account_episodes["call_trade_cash_pnl_net_cny"])),
        "spearman_terminal_index_return_vs_call_cash_pnl": float(account_episodes[
            "index_return_open_to_flat_close"
        ].corr(account_episodes["call_trade_cash_pnl_net_cny"], method="spearman")),
        "pearson_peak_index_upside_vs_call_cash_pnl": float(account_episodes[
            "index_max_close_upside_during_call"
        ].corr(account_episodes["call_trade_cash_pnl_net_cny"])),
        "spearman_peak_index_upside_vs_call_cash_pnl": float(account_episodes[
            "index_max_close_upside_during_call"
        ].corr(account_episodes["call_trade_cash_pnl_net_cny"], method="spearman")),
    }
    by_year = []
    for h, group in episodes.groupby("horizon_sessions", sort=True):
        for year, same_year in group.groupby("signal_year", sort=True):
            event = same_year.loc[same_year.downcross75, "return_t_close_to_h_close"]
            control = same_year.loc[
                ~same_year.downcross75 & same_year.fear_now.lt(75),
                "return_t_close_to_h_close",
            ]
            if len(event):
                by_year.append({
                    "horizon_sessions": int(h), "signal_year": int(year),
                    "event_n": len(event), "control_n": len(control),
                    "event_mean_return": float(event.mean()),
                    "same_year_other_fear_below75_mean_return": float(control.mean()),
                    "difference": float(event.mean() - control.mean()),
                })
    episodes.loc[episodes.downcross75].to_csv(RUN / "downcross_events.csv", index=False)
    summary.to_csv(RUN / "forward_summary.csv", index=False)
    account_episodes.to_csv(RUN / "account_episode_attribution.csv", index=False)
    pd.DataFrame(by_year).to_csv(RUN / "yearly_comparison.csv", index=False)
    (RUN / "verification.json").write_text(json.dumps({
        "window": [frame.date.iloc[0].date().isoformat(), CUTOFF.date().isoformat()],
        "official_price_rows": len(frame),
        "fear_missing_dates": frame.loc[frame.fear_greed_index.isna(), "date"].dt.strftime("%Y-%m-%d").tolist(),
        "downcross_days": int(frame.downcross75.sum()),
        "actual_d40_account_open_signal_days": len(opening_dates),
        "horizons": list(HORIZONS),
        "price_source_sha256": sha256(SOURCE / "inputs" / "csi1000_official_ohlc_response.json"),
        "fear_source_sha256": sha256(SOURCE / "inputs" / "fear_greed_full.csv"),
        "account_corr": account_corr,
        "forty_session_overlap_sensitivity": cluster_sensitivity,
        "classification": "descriptive_snapshot_not_point_in_time_or_tradable",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))
    print("\nCALL EPISODES\n", account_episodes.to_string(index=False))
    print("\n", account_corr)


if __name__ == "__main__":
    main()
