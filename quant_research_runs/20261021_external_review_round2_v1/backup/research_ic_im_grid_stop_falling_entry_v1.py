"""IC/IM half-grid entry delay after valuation trigger until new lows stop."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import analyze_no_grid_annual_20260912 as source
import ic_valuation_overlay_put_sync_v1 as ice
import im_fixed_valuation_overlay_entry_exit_scan_v15 as ime
from im_put_maturity_valuation_tiers_v3 import metrics

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_current_half_grid_mechanism_replay_ic_im_valuation_grid_stop_falling_entry_immediate_no_new_20d_low_3_5_10_sessions"
SPEC = ROOT / "docs" / "ic_im_grid_stop_falling_entry_v1_spec.md"
WAITS = (0, 3, 5, 10)
ONE_WAY = 0.0001
AUDIT: dict[str, object] = {}
HASHES: dict[str, str] = {}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path, **kwargs) -> pd.DataFrame:
    HASHES[str(path.relative_to(ROOT))] = sha(path)
    return pd.read_csv(path, parse_dates=["date"], **kwargs)


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def stop_permission(dates: pd.Series, scores: pd.Series, closes: pd.Series, low: float, wait: int) -> pd.Series:
    dates = pd.Series(pd.to_datetime(dates)).reset_index(drop=True)
    score = pd.Series(scores, dtype=float).reset_index(drop=True)
    close = pd.Series(closes, dtype=float).reset_index(drop=True)
    prior20 = close.shift(1).rolling(20, min_periods=20).min()
    strict_new_low = close.lt(prior20)
    allowed = np.zeros(len(dates), dtype=bool)
    if wait == 0:
        allowed = score.le(low + 1e-12).to_numpy(dtype=bool)
    else:
        armed = confirmed = False
        streak = 0
        for i in range(len(dates)):
            if not np.isfinite(score.iloc[i]) or score.iloc[i] > low + 1e-12:
                armed = confirmed = False; streak = 0
                continue
            if not armed:
                armed = True; confirmed = False; streak = 0
                continue
            if not np.isfinite(prior20.iloc[i]) or bool(strict_new_low.iloc[i]):
                streak = 0
            else:
                streak += 1
            if streak >= wait:
                confirmed = True
            allowed[i] = confirmed
    return pd.Series(allowed, index=pd.DatetimeIndex(dates), name=f"wait{wait}_allowed")


def gate_scores(frame: pd.DataFrame, permission: pd.Series, low: float, high: float) -> pd.DataFrame:
    result = frame.copy()
    ok = result.date.map(permission).fillna(False).astype(bool)
    blocked_low = result.unbounded_median_knot.le(low + 1e-12) & ~ok
    result.loc[blocked_low, "unbounded_median_knot"] = (low + high) / 2
    return result


def ic_paths() -> tuple[list[pd.DataFrame], list[pd.DataFrame], list[pd.DataFrame]]:
    base = read(source.IC_FORMAL)
    scores = read(ice.SCORE_FILE)
    raw = read(ice.IC_RAW, usecols=["date", "contract", "open", "settle", "pre_settle", "volume"]).rename(columns={"volume": "raw_volume"})
    chain = base[["date", "contract", "roll_event"]].merge(raw, on=["date", "contract"], validate="one_to_one").merge(scores[["date", "unbounded_median_knot"]], on="date", validate="one_to_one")
    tri = read(ROOT / "data/ic_im_valuation_risk_premium_forecast_v3/csindex_H00905.csv").set_index("date").close
    closes = chain.date.map(tri)
    original = read(source.IC_TARGET, usecols=["date", "grid_held_eod"])
    base = base.merge(original, on="date", validate="one_to_one")
    results: list[pd.DataFrame] = []; events: list[pd.DataFrame] = []; gates: list[pd.DataFrame] = []
    for wait in WAITS:
        permission = stop_permission(chain.date, chain.unbounded_median_knot, closes, 0.5, wait)
        gated = gate_scores(chain, permission, 0.5, 1.0)
        grid, trades, _ = ice.simulate_overlay(gated, 0.5, 1.0)
        held_roll = chain.roll_event & grid.overlay_held_before.eq(1) & grid.overlay_held_eod.eq(1)
        grid.loc[held_roll, "overlay_gross_ret"] = base.loc[held_roll, "futures_gross_ret"].to_numpy()
        size = 0.5
        grid_net = size * ((1 + grid.overlay_gross_ret) * (1 - grid.overlay_cost_rate) - 1)
        cash = base.cash_weight + 0.3 * (base.grid_held_eod - size * grid.overlay_held_eod)
        ret = base.ret - base.grid_net_increment + grid_net + (cash - base.cash_weight) * ice.CASH_DAILY
        tag = "immediate" if wait == 0 else f"wait{wait}"
        candidate = f"IC_{tag}"
        daily = pd.DataFrame({"date": base.date, "candidate": candidate, "return_net": ret, "grid_units": size * grid.overlay_held_eod, "cash_weight": cash, "grid_cost": size * grid.overlay_cost_rate})
        if wait == 0:
            reference = read(ROOT / "quant_param_scan_runs/20260913_ic_balanced_half_grid/daily_candidates.csv.gz")
            reference = reference[reference.candidate.eq("L0.500_H1.000")].reset_index(drop=True)
            err = float(np.max(np.abs(daily.return_net - reference.ret)))
            if err > 1e-12: raise RuntimeError(f"IC immediate parity failed: {err}")
            AUDIT["IC_current_half_parity"] = err
        if len(trades):
            trades = trades.assign(candidate=candidate)
            buy = trades[trades.action.eq("buy")]
            if wait and not buy.signal_date.map(permission).fillna(False).all(): raise RuntimeError(f"{candidate} entry gate failed")
            events.append(trades)
        gates.append(pd.DataFrame({"date": chain.date, "product": "IC", "candidate": candidate, "valuation": chain.unbounded_median_knot, "close": closes, "entry_allowed": chain.date.map(permission).fillna(False)}))
        results.append(daily)
    return results, events, gates


def im_paths() -> tuple[list[pd.DataFrame], list[pd.DataFrame], list[pd.DataFrame]]:
    source.REFRESH = ROOT / "outputs/nav_r7_complete_refresh_20260912"
    source.IM_FULL = source.REFRESH / "im_full_daily.csv.gz"
    source.IM_TAIL = source.REFRESH / "im_tail_daily.csv"
    source.HISTORICAL_SIGNALS = source.REFRESH / "historical_signals.json"
    base, source_audit = source.prepare_im(); base = base[base.date.le("2026-08-14")].reset_index(drop=True)
    AUDIT["IM_source"] = source_audit
    old, _, percentile = ime.load_sources()
    score_path = ROOT / "quant_param_scan_runs/20260913_im_reconstruct_exit_cliff/reconstructed_valuation_panel.csv.gz"
    scores = read(score_path)[["date", "unbounded_median_knot"]]
    model, _ = ime.build_model_market(old, scores, percentile)
    real, _ = ime.build_real_market(old, scores, percentile)
    tri = read(ROOT / "data/ic_im_valuation_risk_premium_forecast_v3/csindex_H00852.csv").set_index("date").close
    closes = scores.date.map(tri)
    no_grid_gross = base.futures_gross_ret - base.grid_gross_component
    no_grid_cost = base.futures_cost_rate - base.overlay_cost_rate
    no_grid_cash = base.cash_weight + 0.3 * base.grid_units
    basis = 0.00038985993765572324
    results: list[pd.DataFrame] = []; events: list[pd.DataFrame] = []; gates: list[pd.DataFrame] = []
    for wait in WAITS:
        permission = stop_permission(scores.date, scores.unbounded_median_knot, closes, 1.6, wait)
        gm, tm, _ = ime.simulate_overlay(gate_scores(model, permission, 1.6, 2.0), gate_scores(scores, permission, 1.6, 2.0), "unbounded_median_knot", 1.6, 2.0, f"wait{wait}", "fixed", "model")
        gr, tr, _ = ime.simulate_overlay(gate_scores(real, permission, 1.6, 2.0), gate_scores(scores, permission, 1.6, 2.0), "unbounded_median_knot", 1.6, 2.0, f"wait{wait}", "fixed", "real")
        gm["basis"] = (1 + gm.overlay_gross_ret) * basis * gm.overlay_held_before
        gr["basis"] = 0.0
        grid = pd.concat([gm[gm.date.lt("2022-07-22")], gr]).sort_values("date").reset_index(drop=True)
        if not base.date.equals(grid.date): raise RuntimeError("IM date mismatch")
        size = 0.5
        cash = no_grid_cash - 0.3 * size * grid.overlay_held_eod
        cost = no_grid_cost + size * grid.overlay_cost_rate
        ret = (1 + no_grid_gross + size * (grid.overlay_gross_ret + grid.basis) + base.put_pnl_ret + base.call_pnl_ret) * (1 - cost) * (1 - base.put_cost_rate) * (1 - base.call_cost_rate) - 1 + cash * source.CASH_DAILY
        tag = "immediate" if wait == 0 else f"wait{wait}"
        candidate = f"IM_{tag}"
        daily = pd.DataFrame({"date": base.date, "candidate": candidate, "return_net": ret, "grid_units": size * grid.overlay_held_eod, "cash_weight": cash, "grid_cost": size * grid.overlay_cost_rate})
        if wait == 0:
            reference = read(ROOT / "quant_param_scan_runs/20260914_im_grid160_half_mom120_v2/daily.csv.gz")
            reference = reference[reference.candidate.eq("IM_original160_half")].reset_index(drop=True)
            err = float(np.max(np.abs(daily.return_net - reference.return_net)))
            if err > 1e-12: raise RuntimeError(f"IM immediate parity failed: {err}")
            AUDIT["IM_current_half_parity"] = err
        trades = pd.concat([tm[tm.execution_date.lt("2022-07-22")], tr[tr.execution_date.ge("2022-07-22")]], ignore_index=True)
        if len(trades):
            trades = trades.assign(candidate=candidate)
            buy = trades[trades.action.eq("buy")]
            if wait and not buy.signal_date.map(permission).fillna(False).all(): raise RuntimeError(f"{candidate} entry gate failed")
            events.append(trades)
        gates.append(pd.DataFrame({"date": scores.date, "product": "IM", "candidate": candidate, "valuation": scores.unbounded_median_knot, "close": closes, "entry_allowed": scores.date.map(permission).fillna(False)}))
        results.append(daily)
    return results, events, gates


def tables(daily: pd.DataFrame):
    rows = []; wide_rows = []; unavailable: dict[str, dict[str, str]] = {}
    real_starts = {"IC": pd.Timestamp("2022-09-19"), "IM": pd.Timestamp("2022-07-22")}
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date"); end = group.date.max(); wide = {"candidate": candidate}
        product = candidate[:2]; real_start = real_starts[product]
        segments = (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1), ("real_period", 0), ("model_period", -1))
        for segment, years in segments:
            if years is None: sample = group; start = group.date.min()
            elif years == 0: sample = group[group.date.ge(real_start)]; start = real_start
            elif years == -1: sample = group[group.date.lt(real_start)]; start = group.date.min()
            else: start = end - pd.DateOffset(years=years); sample = group[group.date.ge(start)]
            values = metrics(sample.return_net)
            held = sample.grid_units.gt(0)
            rows.append({"candidate": candidate, "segment": segment, "start": str(start.date()), "end": str(sample.date.max().date()), "rows": len(sample), "grid_days": int(held.sum()), "grid_fraction": float(held.mean()), "grid_fee_sum": float(sample.grid_cost.sum()), **values})
            if segment in {"full", "last_10y", "last_5y", "last_3y", "last_1y"}:
                for key, value in values.items(): wide[f"{key}_{segment}"] = value
        wide_rows.append(wide)
    return pd.DataFrame(rows), pd.DataFrame(wide_rows), unavailable


def main() -> None:
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    ic_daily, ic_events, ic_gates = ic_paths(); im_daily, im_events, im_gates = im_paths()
    daily = pd.concat(ic_daily + im_daily, ignore_index=True)
    events = pd.concat(ic_events + im_events, ignore_index=True)
    gates = pd.concat(ic_gates + im_gates, ignore_index=True)
    summary, wide, unavailable = tables(daily)
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    events.to_csv(out / "events.csv", index=False)
    gates.to_csv(out / "entry_gate_audit.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    full = summary[summary.segment.isin(["full", "real_period", "model_period"])]
    meta.update(
        scan_type="ic_im_grid_stop_falling_entry_wait_scan",
        baseline={"candidates": ["IC_immediate", "IM_immediate"]},
        candidate_grid=[{"wait_no_new_20d_low_sessions": x} for x in WAITS],
        data_snapshot=HASHES, cost_model={"one_way_notional": ONE_WAY, "grid_size": 0.5, "futures_buffer": 0.30, "cash_annual": 0.03, "non_grid_components": "fixed from validated source paths"},
        audit=AUDIT, unavailable_segments=unavailable,
        outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "events": str(out / "events.csv"), "entry_gate_audit": str(out / "entry_gate_audit.csv.gz")},
        source_hashes={"script": sha(Path(__file__)), "spec": sha(SPEC)},
        warnings=["Fixed historical non-grid components are a mechanism replay, not the latest all-rules production performance.", "IM pre-listing futures and carry are modeled with ex-post mean basis.", "No bid-ask, dynamic margin, forced liquidation, tax, capacity, or integer sizing."],
        decision="research_only_pending_interpretation", stability_label="stop_falling_wait_scan_pending_review", git_status_after=git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    record = (
        "# IC/IM低估网格停止创新低入场扫描\n\n## Data\n\n"
        "固定历史2015-04-16至2026-08-14；真实层IC自2022-09-19、IM自2022-07-22，之前部分单列模型/代理期。\n\n"
        "## Implementation\n\n估值触发后等待连续3/5/10日不再严格跌破此前20日最低收盘；仅门控首次买入，持有与退出规则不变。\n\n"
        "## Cost\n\n原期货、Put、Call与网格成本保持；网格0.5倍、单边1bp、30%缓冲、现金3%。\n\n"
        "## Results\n\n" + full.to_markdown(index=False) + "\n\n"
        "## Stability\n\n待解释。\n\n## Decision\n\nresearch_only_pending_interpretation\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle: handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(AUDIT, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
