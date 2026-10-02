from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mainline_v1_1 as policy
import im_mo_csi1000_put_protection_battery_v6 as market_v6
import research_im_short_put_recovery_atm_real_v1 as short95
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map, prepare_options, metrics


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_rolling_arbitrage_imc_to_short95_router_v1_high_iv_capital_router_iv_entry_threshold_and_nonadmission_route"
SPEC = ROOT / "docs" / "imc_high_iv_short95_router_v1_spec.md"
BASE = short95.BASE
OPTIONS = short95.OP
FUTURES = short95.FU
THRESHOLDS = (0.35, 0.375, 0.40)
CASH_DAILY = 1.03 ** (1 / 252) - 1
ONE_WAY_COST = 0.0001
WINDOWS = (("full", None), ("last_10y", 10), ("last_5y", 5), ("last_3y", 3), ("last_1y", 1))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False).stdout.strip()


def implied_vol(price: float, spot: float, strike: float, rate: float, dividend: float, years: float) -> float | None:
    if min(price, spot, strike, years) <= 0:
        return None
    low, high = 0.01, 5.0
    lo = market_v6.proxy.bs_put(spot, strike, rate, dividend, low, years)
    hi = market_v6.proxy.bs_put(spot, strike, rate, dividend, high, years)
    if price < lo - 1e-8 or price > hi + 1e-8:
        return None
    for _ in range(100):
        mid = (low + high) / 2
        if market_v6.proxy.bs_put(spot, strike, rate, dividend, mid, years) < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def prepare_signal(base: pd.DataFrame, options: pd.DataFrame) -> pd.DataFrame:
    """Build only T-close information, mapped to its next listed execution day."""
    state, _ = policy.load_authoritative_local_state()
    state = state.set_index("date")
    market, checks = market_v6.model_market()
    market = market.set_index("date")
    by_day = {day: part for day, part in options.groupby("date", sort=False)}
    rows: list[dict[str, Any]] = []
    for i in range(1, len(base)):
        eval_row = base.iloc[i - 1]
        execution = pd.Timestamp(base.iloc[i].date)
        evaluation = pd.Timestamp(eval_row.date)
        permission = bool(
            evaluation in state.index
            and pd.notna(state.loc[evaluation, "valuation_tier"])
            and int(state.loc[evaluation, "valuation_tier"]) <= 1
            and pd.notna(state.loc[evaluation, "momentum_120"])
            and float(state.loc[evaluation, "momentum_120"]) >= 0
        )
        row: dict[str, Any] = {
            "eval_date": evaluation,
            "execution_date": execution,
            "short_put_permission": permission,
            "permission_reason": "allowed" if permission else "valuation_or_mom120_failed",
            "iv_contract": "",
            "iv": np.nan,
            "iv_valid": False,
            "execution_open_valid": False,
            "execution_contract": "",
            "signal_moneyness": np.nan,
        }
        chain = by_day.get(evaluation, options.iloc[:0])
        month = execution.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
        chain = chain[(chain.contract_month == month) & (chain.strike < float(eval_row.csi1000_price_close))].copy()
        if not chain.empty and evaluation in market.index:
            chain["distance"] = (chain.strike - float(eval_row.csi1000_price_close) * 0.95).abs()
            quote = chain.sort_values(["distance", "strike"]).iloc[0]
            years = (pd.Timestamp(quote.actual_expiry) - evaluation).days / 365
            iv = implied_vol(float(quote.close), float(market.loc[evaluation, "spot_close"]), float(quote.strike), float(market.loc[evaluation, "rate_close"]), float(market.loc[evaluation, "dividend_close"]), years)
            row.update({"iv_contract": str(quote.contract), "iv": iv if iv is not None else np.nan, "iv_valid": iv is not None, "signal_moneyness": float(quote.strike / eval_row.csi1000_price_close)})
            execution_quote = options[(options.date == execution) & (options.contract == quote.contract)]
            if len(execution_quote) == 1:
                execution_quote = execution_quote.iloc[0]
                valid = bool(execution_quote.open > 0 and execution_quote.volume > 0 and execution_quote.open_interest > 0)
                row.update({"execution_open_valid": valid, "execution_contract": str(quote.contract)})
        rows.append(row)
    result = pd.DataFrame(rows)
    if not (result.execution_date > result.eval_date).all():
        raise RuntimeError("non-causal signal mapping")
    result.attrs["market_checks"] = checks
    return result


def route_label(threshold: float, fallback: str) -> str:
    return f"iv{int(round(threshold * 1000)):03d}__{fallback}"


def run_router(base: pd.DataFrame, options: pd.DataFrame, futures: pd.DataFrame, signal: pd.DataFrame, threshold: float, fallback: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    options_by_day = {day: part for day, part in options.groupby("date", sort=False)}
    option_lookup = options.set_index(["contract", "date"])
    future_lookup = futures.set_index(["contract", "date"])
    decisions = signal.set_index("execution_date")
    candidate = route_label(threshold, fallback)
    equity = 1.0
    # Start in a rolling-IMC future at the first official settlement, matching the baseline's initial cost convention.
    first = base.iloc[0]
    first_quote = future_lookup.loc[(first.contract, first.date)]
    position: dict[str, Any] = {"contract": first.contract, "units": 1 / (float(first_quote.settle) * 200), "mark": float(first_quote.settle)}
    state = "imc"
    pending = ""
    cycle: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    for i, b in enumerate(base.itertuples(index=False)):
        day = pd.Timestamp(b.date)
        previous_equity = equity
        pnl, cost, action, decision_reason = 0.0, 0.0, "", ""
        route = "initial_imc" if i == 0 else ""
        decision = decisions.loc[day] if day in decisions.index else None
        high_iv = bool(decision is not None and bool(decision.iv_valid) and float(decision.iv) > threshold)
        permitted = bool(decision is not None and bool(decision.short_put_permission))
        open_valid = bool(decision is not None and bool(decision.execution_open_valid))
        should_short = high_iv and permitted and open_valid
        nonadmission = high_iv and not permitted
        # T+1 open transition out of IMC, before its remaining intraday/close return.
        if i > 0 and state == "imc" and (should_short or (nonadmission and fallback == "cash_when_ineligible")):
            q = future_lookup.loc[(position["contract"], day)]
            pnl += position["units"] * 200 * (float(q.open) - position["mark"])
            cost += position["units"] * 200 * float(q.open) * ONE_WAY_COST
            position = {}
            state = "cash"
            if should_short:
                oq = option_lookup.loc[(str(decision.execution_contract), day)]
                units = previous_equity / (float(b.csi1000_price_close) * 200)
                position = {"contract": str(decision.execution_contract), "units": units, "mark": float(oq.settle), "expiry": pd.Timestamp(oq.actual_expiry)}
                pnl += units * 200 * (float(oq.open) - float(oq.settle))
                cost += units * 200 * float(b.csi1000_price_close) * ONE_WAY_COST
                cycle = {"entry_date": str(day.date()), "put_contract": str(decision.execution_contract), "units": units, "realized_pnl": pnl - cost, "closed": False}
                state, action, route = "put", "imc_to_short_put_open", "high_iv_permitted_short_put"
            else:
                action, route = "imc_to_cash_open", "high_iv_ineligible_cash"
            decision_reason = str(decision.permission_reason)
        # A cash state may re-enter IMC under low IV, or start an eligible high-IV Put cycle.
        if i > 0 and state == "cash" and action == "" and decision is not None:
            if should_short:
                oq = option_lookup.loc[(str(decision.execution_contract), day)]
                units = previous_equity / (float(b.csi1000_price_close) * 200)
                position = {"contract": str(decision.execution_contract), "units": units, "mark": float(oq.settle), "expiry": pd.Timestamp(oq.actual_expiry)}
                pnl += units * 200 * (float(oq.open) - float(oq.settle))
                cost += units * 200 * float(b.csi1000_price_close) * ONE_WAY_COST
                cycle = {"entry_date": str(day.date()), "put_contract": str(decision.execution_contract), "units": units, "realized_pnl": pnl - cost, "closed": False}
                state, action, route = "put", "cash_to_short_put_open", "high_iv_permitted_short_put"
            elif not high_iv:
                q = future_lookup.loc[(b.contract, day)]
                units = previous_equity / (float(q.open) * 200)
                position = {"contract": b.contract, "units": units, "mark": float(q.settle)}
                pnl += units * 200 * (float(q.settle) - float(q.open))
                cost += units * 200 * float(q.open) * ONE_WAY_COST
                state, action, route = "imc", "cash_to_imc_open", "low_iv_restore_imc"
        # Mark the state through the rest of the session, preserving the existing short-Put recovery engine semantics.
        if state == "imc":
            q = future_lookup.loc[(position["contract"], day)]
            if i == 0:
                cost += position["units"] * 200 * float(q.settle) * ONE_WAY_COST
            elif str(b.roll_to) not in ("nan", "") and str(b.roll_to) != position["contract"]:
                pnl += position["units"] * 200 * (float(q.close) - position["mark"])
                nq = future_lookup.loc[(b.roll_to, day)]
                cost += position["units"] * 200 * (float(q.close) + float(nq.close)) * ONE_WAY_COST
                pnl += position["units"] * 200 * (float(nq.settle) - float(nq.close))
                position.update(contract=b.roll_to, mark=float(nq.settle))
                action = action or "imc_monthly_roll_close"
            elif not (i > 0 and action.startswith("imc_to_")):
                pnl += position["units"] * 200 * (float(q.settle) - position["mark"])
                position["mark"] = float(q.settle)
        elif state == "put":
            q = option_lookup.loc[(position["contract"], day)]
            pnl += position["units"] * 200 * (position["mark"] - float(q.settle))
            position["mark"] = float(q.settle)
        elif state == "recovery_im":
            q = future_lookup.loc[(position["contract"], day)]
            if pending == "exit":
                pnl += position["units"] * 200 * (float(q.open) - position["mark"])
                cost += position["units"] * 200 * float(q.open) * ONE_WAY_COST
                cycle["exit_date"], cycle["closed"] = str(day.date()), True
                cycle["realized_pnl"] += pnl - cost
                cycle["exit_cycle_pnl"] = cycle["realized_pnl"]
                cycles.append(cycle.copy())
                cycle, position, state, pending, action = None, {}, "cash", "", "recovery_exit_next_open"
            elif str(b.roll_to) not in ("nan", "") and str(b.roll_to) != position["contract"]:
                pnl += position["units"] * 200 * (float(q.close) - position["mark"])
                nq = future_lookup.loc[(b.roll_to, day)]
                cost += position["units"] * 200 * (float(q.close) + float(nq.close)) * ONE_WAY_COST
                pnl += position["units"] * 200 * (float(nq.settle) - float(nq.close))
                position.update(contract=b.roll_to, mark=float(nq.settle))
                action = action or "recovery_monthly_roll_close"
            else:
                pnl += position["units"] * 200 * (float(q.settle) - position["mark"])
                position["mark"] = float(q.settle)
        if state == "put" and day == position["expiry"]:
            cycle["expiry_date"] = str(day.date())
            if position["mark"] > 0:
                pending, state, position, action = "assign", "cash", {}, "itm_cash_settlement"
            else:
                cycle["exit_date"], cycle["closed"] = str(day.date()), True
                cycle["realized_pnl"] += pnl - cost
                cycle["exit_cycle_pnl"] = cycle["realized_pnl"]
                cycles.append(cycle.copy())
                cycle, position, state, action = None, {}, "cash", "worthless_expiry"
        if pending == "assign":
            q = future_lookup.loc[(b.contract, day)]
            units = cycle["units"]
            position = {"contract": b.contract, "units": units, "mark": float(q.settle)}
            pnl += units * 200 * (float(q.settle) - float(q.open))
            cost += units * 200 * float(q.open) * ONE_WAY_COST
            cycle["assignment_date"] = str(day.date())
            state, pending, action = "recovery_im", "", "assignment_buy_next_open"
        if state == "recovery_im":
            exit_cost = position["units"] * 200 * position["mark"] * ONE_WAY_COST
            if cycle["realized_pnl"] >= exit_cost:
                pending = "exit"
        if cycle is not None:
            cycle["realized_pnl"] += pnl - cost
        cash = previous_equity * (0.7 if state in {"imc", "put", "recovery_im"} or pending == "assign" else 1.0) * CASH_DAILY
        equity += pnl - cost + cash
        if not math.isfinite(equity) or equity <= 0:
            raise RuntimeError(f"invalid equity on {day.date()} for {candidate}")
        item = {"date": day, "candidate": candidate, "return_net": equity / previous_equity - 1, "nav": equity, "state": state, "action": action, "route": route, "high_iv": high_iv, "short_put_permission": permitted, "iv": float(decision.iv) if decision is not None and pd.notna(decision.iv) else np.nan, "decision_reason": decision_reason}
        rows.append(item)
        if action:
            events.append(item.copy())
    if cycle is not None:
        cycle["open_cycle_pnl"] = cycle["realized_pnl"]
        cycles.append(cycle.copy())
    return pd.DataFrame(rows), pd.DataFrame(events), pd.DataFrame(cycles)


def metric_table(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, wide = [], []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        line = {"candidate": candidate}
        for segment, years in WINDOWS:
            requested = group.date.min() if years is None else group.date.max() - pd.DateOffset(years=years)
            complete = years is None or group.date.min() <= requested
            if not complete:
                values = {"ann_return": "N/A", "ann_vol": "N/A", "sharpe_repo": "N/A", "max_dd": "N/A"}
                sample = group.iloc[:0]
            else:
                sample = group[group.date >= requested]
                values = metrics(sample.return_net)
            row = {"candidate": candidate, "segment": segment, "start": str(requested.date()), "end": str(group.date.max().date()), "rows": len(sample), **values, "high_iv_days": int(sample.high_iv.sum()), "cash_days": int(sample.state.eq("cash").sum()), "short_put_days": int(sample.state.eq("put").sum()), "recovery_im_days": int(sample.state.eq("recovery_im").sum())}
            rows.append(row)
            for key in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                line[f"{key}_{segment}"] = values[key]
        wide.append(line)
    return pd.DataFrame(rows), pd.DataFrame(wide)


def main() -> None:
    meta = json.loads((RUN / "scan_meta.json").read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("refusing to overwrite initialized/completed scan")
    base = pd.read_csv(BASE, parse_dates=["date"])
    raw = pd.read_csv(OPTIONS, parse_dates=["date"])
    raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
    options = prepare_options(raw, actual_expiry_map(raw, base))
    futures = pd.read_csv(FUTURES, parse_dates=["date"])
    signal = prepare_signal(base, options)
    signal.to_csv(RUN / "signal_audit.csv", index=False, encoding="utf-8-sig")
    daily_parts, event_parts, cycle_parts = [], [], []
    for threshold in THRESHOLDS:
        for fallback in ("cash_when_ineligible", "continue_imc_when_ineligible"):
            daily, events, cycles = run_router(base, options, futures, signal, threshold, fallback)
            daily_parts.append(daily)
            if len(events): event_parts.append(events)
            if len(cycles): cycle_parts.append(cycles.assign(candidate=route_label(threshold, fallback)))
    baseline = pd.DataFrame({"date": base.date, "candidate": "pure_rolling_imc", "return_net": base.baseline_plus_cash_ret, "state": "imc", "action": "", "route": "baseline", "high_iv": False, "short_put_permission": False, "iv": np.nan, "decision_reason": ""})
    baseline["nav"] = (1 + baseline.return_net).cumprod()
    daily = pd.concat([baseline, *daily_parts], ignore_index=True)
    summary, wide = metric_table(daily)
    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(exist_ok=False)
    daily.to_csv(daily_dir / "daily.csv.gz", index=False, compression="gzip")
    pd.concat(event_parts, ignore_index=True).to_csv(daily_dir / "events.csv", index=False)
    pd.concat(cycle_parts, ignore_index=True).to_csv(daily_dir / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    full = summary[summary.segment.eq("full")]
    full.to_csv(RUN / "full_comparison.csv", index=False, encoding="utf-8-sig")
    unavailable = {name: {"last_10y": "Real IM/MO history begins 2022-07-22.", "last_5y": "Real IM/MO history begins 2022-07-22."} for name in daily.candidate.unique()}
    decision = "research_only_pending_interpretation"
    stability = "unclassified_first_real_router_scan"
    meta.update({"scan_type": "matched_high_iv_router_grid", "baseline": {"candidate": "pure_rolling_imc", "source": str(BASE)}, "candidate_grid": [{"iv_threshold": t, "nonadmission_route": f} for t in THRESHOLDS for f in ("cash_when_ineligible", "continue_imc_when_ineligible")], "data_snapshot": {"start": str(base.date.min().date()), "end": str(base.date.max().date()), "rows": len(base), "options": digest(OPTIONS), "futures": digest(FUTURES), "base": digest(BASE)}, "cost_model": {"one_way_notional": ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03}, "unavailable_segments": unavailable, "outputs": {**meta["outputs"], "signal_audit": str(RUN / "signal_audit.csv"), "daily": str(daily_dir / "daily.csv.gz"), "events": str(daily_dir / "events.csv"), "cycles": str(daily_dir / "cycles.csv"), "full_comparison": str(RUN / "full_comparison.csv")}, "decision": decision, "stability_label": stability, "source_hashes": {"spec": digest(SPEC), "script": digest(Path(__file__))}, "warnings": ["Real-only first layer; no proxy/model result is mixed in.", "No bid-ask, dynamic margin, forced liquidation, capacity, tax or integer contract constraints.", "Existing dirty worktree was preserved; no frozen mainline or production file was edited."], "git_status_after": git("status", "--short")})
    (RUN / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = "# IMC 高 IV 路由至卖95%Put：未准入分支扫描 v1\n\n## Run Metadata\n\n- 研究层：真实 CFFEX IM/MO；不改生产。\n\n## Research Question\n\n- 高 IV 且卖Put不准入时，比较持现金与继续 IMC。\n\n## Implementation Anchor\n\n- 纯滚动 IMC 基线与卖Put回本状态机均来自既有本地真实路径；信号合约、IV、许可和执行有效性详见 `signal_audit.csv`。\n\n## Data Snapshot\n\n- " + f"{base.date.min().date()} 至 {base.date.max().date()}，{len(base)} 日；真实5Y/10Y为 N/A。\n\n## Cost and Execution Assumptions\n\n- T收盘信号、T+1开盘切换；单边1bp，30%缓冲和3%现金。限制见规格。\n\n## Runtime Override Plan\n\n- 独立研究脚本和输出；未改冻结主线。\n\n## Commands\n\n```powershell\npython -X utf8 research_imc_high_iv_short95_router_v1.py\n```\n\n## Output Files\n\n- `scan_summary.csv`、`window_metrics.csv`、`signal_audit.csv`、`daily_outputs/`。\n\n## Full-Sample Results\n\n" + full.to_markdown(index=False) + "\n\n## Window Results\n\n- 见 `scan_summary.csv`。\n\n## Stability Classification\n\n- `unclassified_first_real_router_scan`；下一步按预注册规格判断相邻阈值平台，不据单点选参数。\n\n## Decision\n\n- `research_only_pending_interpretation`；不晋级。\n\n## User-Facing Summary\n\n- 首轮真实路由扫描已完成，结果仅作研究证据。\n"
    (RUN / "record.md").write_text(text, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False))


if __name__ == "__main__":
    main()
