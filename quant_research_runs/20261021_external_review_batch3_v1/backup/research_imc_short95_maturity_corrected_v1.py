"""Corrected IM high-IV short95 maturity scan with Call suspended in routed states."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_csi1000_put_protection_battery_v6 as market_v6
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_decay50_60_router_parityfix_v2 as parity
import research_imc_current_core_put_short95_earlyvaluation_v4 as v4

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_corrected_im_core_put_high_iv_short95_router_im_short_put_maturity_with_call_suspension_front10_m_1_m_2_m_3"
SPEC = ROOT / "docs" / "im_short95_maturity_corrected_mixed_v1_spec.md"
MATURITIES = ("front10", "m1", "m2", "m3")
IV_THRESHOLD = 0.35
DECAY = 0.60
MIN_FRONT_SESSIONS = 10

JOINT = ROOT / "quant_param_scan_runs" / "20260908_im_full_combination_joint_iv_derisk_v1"
sys.path.insert(0, str(JOINT))
import run_joint as joint_run  # noqa: E402
import joint_components as joint_comp  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def target_month(execution: pd.Timestamp, maturity: str) -> pd.Timestamp:
    offset = {"m1": 1, "m2": 2, "m3": 3}[maturity]
    return execution.to_period("M").to_timestamp() + pd.offsets.MonthBegin(offset)


def prepare_signal(base: pd.DataFrame, options: pd.DataFrame, maturity: str) -> pd.DataFrame:
    state = v4.effective_state().set_index("date").sort_index()
    market, _ = market_v6.model_market()
    market = market.set_index("date")
    by_day = {day: part for day, part in options.groupby("date", sort=False)}
    sessions = pd.DatetimeIndex(base.date)
    rows: list[dict[str, Any]] = []
    for i in range(1, len(base)):
        eval_row = base.iloc[i - 1]
        evaluation = pd.Timestamp(eval_row.date)
        execution = pd.Timestamp(base.iloc[i].date)
        st = state.loc[evaluation] if evaluation in state.index else None
        permission = bool(
            st is not None
            and pd.notna(st.effective_valuation_tier)
            and int(st.effective_valuation_tier) <= 1
            and pd.notna(st.momentum_120)
            and float(st.momentum_120) >= 0
        )
        row: dict[str, Any] = {
            "eval_date": evaluation, "execution_date": execution,
            "short_put_permission": permission,
            "permission_reason": "allowed" if permission else "valuation_or_mom120_failed",
            "valuation_source": "missing_failclosed" if st is None else str(st.valuation_source),
            "effective_valuation_tier": np.nan if st is None else st.effective_valuation_tier,
            "iv_contract": "", "iv": np.nan, "iv_valid": False,
            "execution_open_valid": False, "execution_contract": "",
            "signal_moneyness": np.nan, "selected_contract_month": pd.NaT,
            "front_fallback_to_m1": False, "remaining_sessions": np.nan,
        }
        chain = by_day.get(evaluation, options.iloc[:0]).copy()
        if maturity == "front10":
            month0 = execution.to_period("M").to_timestamp()
            current = chain[chain.contract_month.eq(month0)].copy()
            eligible = False
            if not current.empty:
                expiry = pd.Timestamp(current.actual_expiry.iloc[0])
                remaining = int(((sessions >= execution) & (sessions <= expiry)).sum())
                eligible = remaining >= MIN_FRONT_SESSIONS
            else:
                remaining = 0
            month = month0 if eligible else execution.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
            row["front_fallback_to_m1"] = not eligible
            row["remaining_sessions"] = remaining
        else:
            month = target_month(execution, maturity)
        chain = chain[(chain.contract_month == month) & (chain.strike < float(eval_row.csi1000_price_close))].copy()
        if not chain.empty and evaluation in market.index:
            chain["distance"] = (chain.strike - float(eval_row.csi1000_price_close) * 0.95).abs()
            quote = chain.sort_values(["distance", "strike", "contract"]).iloc[0]
            years = (pd.Timestamp(quote.actual_expiry) - evaluation).days / 365
            iv = common.router.implied_vol(
                float(quote.close), float(market.loc[evaluation, "spot_close"]), float(quote.strike),
                float(market.loc[evaluation, "rate_close"]), float(market.loc[evaluation, "dividend_close"]), years,
            )
            row.update({
                "iv_contract": str(quote.contract), "iv": np.nan if iv is None else iv,
                "iv_valid": iv is not None, "signal_moneyness": float(quote.strike / eval_row.csi1000_price_close),
                "selected_contract_month": pd.Timestamp(quote.contract_month),
            })
            execution_quote = options[(options.date == execution) & (options.contract == quote.contract)]
            if len(execution_quote) == 1:
                q = execution_quote.iloc[0]
                row.update({
                    "execution_open_valid": bool(q.open > 0 and q.volume > 0 and q.open_interest > 0),
                    "execution_contract": str(quote.contract),
                })
        rows.append(row)
    result = pd.DataFrame(rows)
    if not (result.execution_date > result.eval_date).all():
        raise RuntimeError("Non-causal signal mapping")
    return result


def call_inputs(scope: str, dates: pd.Series, scale: pd.Series, label: str, call_dir: Path):
    model_market = pd.read_csv(joint_run.BASE / "model_market.csv.gz", parse_dates=["date"])
    model_market = model_market[model_market.date.isin(pd.DatetimeIndex(dates))].reset_index(drop=True)
    calls = pd.read_csv(joint_run.FULL / "prepared_calls.csv.gz", parse_dates=["date", "contract_month", "actual_expiry"])
    if scope == "real":
        upstream = pd.read_csv(joint_run.BASE / "real_upstream.csv.gz", parse_dates=["date"])
        upstream = upstream[upstream.date.isin(pd.DatetimeIndex(dates))].reset_index(drop=True)
        if not upstream.date.equals(pd.Series(pd.DatetimeIndex(dates))):
            raise RuntimeError("Real Call upstream date mismatch")
    else:
        upstream = pd.DataFrame({"date": pd.DatetimeIndex(dates)})
    if not model_market.date.equals(pd.Series(pd.DatetimeIndex(dates))):
        raise RuntimeError(f"{scope} Call market date mismatch")
    call_dir.mkdir(parents=True, exist_ok=True)
    joint_comp.RUN = call_dir
    data = {"market": model_market, "calls": calls, "iv_scale": scale.reset_index(drop=True)}
    chain = {"name": label, "up": upstream}
    daily, trades, signals = joint_comp.call_component(
        data, scope, chain, policy="p", call_policy="raw26"
    )
    daily = daily.copy()
    trades = trades.copy()
    target = pd.to_numeric(scale.reset_index(drop=True), errors="raise").astype(float)
    transitions = target.eq(0.0) & target.shift(1, fill_value=1.0).gt(0.0)
    if not pd.Series(pd.DatetimeIndex(daily.date)).equals(
        pd.Series(pd.DatetimeIndex(dates))
    ):
        raise RuntimeError("Call synchronous-exit dates do not match the portfolio")

    # The route is executed at T+1 open.  Reprice an existing short Call to
    # that same open; leaving the legacy close execution in place would create
    # an uncovered intraday Call after the fixed IM future has already exited.
    market_by_day = model_market.set_index("date")
    calls_by_key = calls.set_index(["contract", "date"])
    if scope == "real":
        denominator = upstream["settle"].shift(1)
        denominator.iloc[0] = upstream.iloc[0]["settle"]
    else:
        denominator = model_market["base_prior_close"]
    corrections = []
    for i in np.flatnonzero(transitions.to_numpy()):
        if i == 0:
            continue
        previous = daily.iloc[i - 1]
        coverage = float(previous.get("call_coverage", 0.0))
        contract = previous.get("call_contract")
        if coverage <= 0.0 or pd.isna(contract) or str(contract) == "":
            continue
        day = pd.Timestamp(daily.iloc[i].date)
        if scope == "real":
            key = (str(contract), day)
            if key not in calls_by_key.index:
                raise RuntimeError(f"Missing Call open quote for synchronous exit: {key}")
            quote = calls_by_key.loc[key]
            if isinstance(quote, pd.DataFrame):
                raise RuntimeError(f"Duplicate Call open quote for synchronous exit: {key}")
            if not (
                float(quote.open) > 0
                and float(quote.close) > 0
                and float(quote.volume) > 0
                and float(quote.open_interest) > 0
            ):
                raise RuntimeError(f"Untradable Call open quote for synchronous exit: {key}")
            open_mark, close_mark = float(quote.open), float(quote.close)
        else:
            row = market_by_day.loc[day]
            strike = float(previous.call_strike)
            expiry = pd.Timestamp(previous.call_expiry)
            years = max((expiry - day).days / 365.0, 0.0)
            v19 = joint_comp.call.v19
            open_mark = v19.bs_call(
                float(row.spot_open), strike, float(row.rate_open),
                float(row.dividend_open), float(row.sigma_open), years,
            )
            close_mark = v19.bs_call(
                float(row.spot_close), strike, float(row.rate_close),
                float(row.dividend_close), float(row.sigma_close), years,
            )
        denom = float(denominator.iloc[i])
        correction = coverage * (close_mark - open_mark) / denom
        daily.loc[daily.index[i], "call_pnl_ret"] = (
            float(daily.iloc[i].call_pnl_ret) + correction
        )
        corrections.append(
            {
                "date": day,
                "contract": str(contract),
                "coverage": coverage,
                "legacy_close": close_mark,
                "synchronous_open": open_mark,
                "pnl_correction": correction,
            }
        )
        hit = (
            pd.to_datetime(trades.get("actual_execution_date"), errors="coerce").eq(day)
            & trades.get("old_contract", pd.Series(index=trades.index, dtype=object)).astype(str).eq(str(contract))
        )
        if hit.any():
            trades.loc[hit, "old_close"] = open_mark
            trades.loc[hit, "execution_timing"] = "open"
            trades.loc[hit, "synchronous_short_put_route_exit"] = True
        else:
            trades = pd.concat(
                [
                    trades,
                    pd.DataFrame(
                        [
                            {
                                "layer": scope,
                                "candidate": "raw26",
                                "eval_date": day,
                                "scheduled_execution_date": day,
                                "actual_execution_date": day,
                                "action": "close",
                                "reason": "short_put_route_open",
                                "old_contract": str(contract),
                                "old_close": open_mark,
                                "execution_timing": "open",
                                "synchronous_short_put_route_exit": True,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
                sort=False,
            )
    daily["synchronous_route_open_exit"] = False
    if corrections:
        corrected_days = {item["date"] for item in corrections}
        daily.loc[pd.to_datetime(daily.date).isin(corrected_days), "synchronous_route_open_exit"] = True
    return daily, trades, signals


def add_call(combined: pd.DataFrame, call_daily: pd.DataFrame) -> pd.DataFrame:
    if not combined.date.reset_index(drop=True).equals(call_daily.date.reset_index(drop=True)):
        raise RuntimeError("Call/component date mismatch")
    out = combined.copy()
    for col in joint_comp.CALL_FIELDS:
        out[col] = call_daily[col].to_numpy(dtype=float)
    out["return_net"] = (
        out.return_net + out.call_pnl_ret - out.call_cost_rate
        - out.call_margin_fraction * common.CASH_DAILY
    )
    out["nav"] = (1 + out.return_net).cumprod()
    if not np.isfinite(out.return_net).all() or out.return_net.le(-1).any():
        raise RuntimeError("Invalid Call-combined return")
    return out


def load_layer(scope: str):
    router = common.router
    if scope == "real":
        base = pd.read_csv(router.BASE, parse_dates=["date"])
        raw = pd.read_csv(router.OPTIONS, parse_dates=["date"])
        raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
        options = common.prepare_options(raw, common.actual_expiry_map(raw, base))
        options_for_put = common.engine.with_execution_prices(options.copy())
        futures = pd.read_csv(router.FUTURES, parse_dates=["date"])
        market = None
    else:
        market, base, options, futures, _, _ = common.model_source.build_inputs()
        options = options.copy(); options["close"] = options["settle"]
        options_for_put = None
    return market, base, options, options_for_put, futures


def run_layer(scope: str, router_fn, real_put_engine, model_put_engine, call_dir: Path):
    market, base, options, options_for_put, futures = load_layer(scope)
    bare = pd.DataFrame({"date": base.date, "candidate": f"{scope}_bare_monthly_imc", "return_net": base.baseline_plus_cash_ret.astype(float)})
    bare["nav"] = (1 + bare.return_net).cumprod(); bare["state"] = "imc"; bare["action"] = ""

    always = pd.Series(True, index=pd.DatetimeIndex(base.date))
    schedule = v4.corrected_core_schedule(base.date, scope, always)
    if scope == "real":
        put, trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=common.engine.monthly_dates(base.date), market=None)
    else:
        put, trades, _ = model_put_engine(market, schedule, "3m", 1.02, f"{scope}_core_put", reset_dates=common.engine.monthly_dates(base.date))
    put = common.scale_put(put, scope)
    protected = common.apply_core_put(bare, put)
    full_scale = pd.Series(1.0, index=base.index)
    short_scope = "r" if scope == "real" else "m"
    call_base, call_base_trades, _ = call_inputs(scope, base.date, full_scale, f"{short_scope}b", call_dir)
    protected = add_call(protected, call_base); protected["candidate"] = f"{scope}_imc_coreput_call_baseline"

    candidates = [protected]
    signals = []; cycle_rows = []; audits: dict[str, Any] = {}
    for maturity in MATURITIES:
        signal = prepare_signal(base, options, maturity)
        routed, events, cycles = router_fn(base, options, futures, signal, IV_THRESHOLD, common.FALLBACK, DECAY)
        routed["date"] = pd.to_datetime(routed.date)
        imc_mask = routed.set_index("date").state.eq("imc")
        route_dates = set(routed.loc[routed.route.eq("high_iv_permitted_short_put"), "date"])
        schedule = v4.corrected_core_schedule(base.date, scope, imc_mask)
        label = f"{scope}_{maturity}_iv35_coreput_to_short95_decay60_callpaused"
        if scope == "real":
            put, put_trades, _ = real_put_engine(base, options_for_put, base, schedule, "3m", 1.02, label, reset_dates=common.engine.monthly_dates(base.date), market=None, open_exit_dates=route_dates)
        else:
            put, put_trades, _ = model_put_engine(market, schedule, "3m", 1.02, label, reset_dates=common.engine.monthly_dates(base.date), open_exit_dates=route_dates)
        put = common.scale_put(put, scope)
        combined = common.apply_core_put(routed, put)
        call_scale = routed.state.eq("imc").astype(float)
        short_maturity = {"front10": "f", "m1": "1", "m2": "2", "m3": "3"}[maturity]
        call_daily, call_trades, _ = call_inputs(scope, base.date, call_scale, f"{short_scope}{short_maturity}", call_dir)
        combined = add_call(combined, call_daily); combined["candidate"] = label
        candidates.append(combined)
        signals.append(signal.assign(layer=scope, maturity=maturity))
        if len(cycles): cycle_rows.append(cycles.assign(layer=scope, maturity=maturity, candidate=label))
        simultaneous_put = set(put_trades.loc[put_trades.action.eq("route_open_exit"), "actual_execution_date"])
        if not simultaneous_put.issubset(route_dates):
            raise RuntimeError(f"{label}: core Put exit mismatch")
        call_zero = call_daily.loc[routed.route.eq("high_iv_permitted_short_put"), "call_coverage"].eq(0).all()
        if not call_zero:
            raise RuntimeError(f"{label}: Call not closed on short-Put route")
        audits[label] = {
            "route_switches": len(route_dates), "short_put_cycles": len(cycles),
            "assignments": int(cycles.assignment_date.fillna("").astype(str).ne("").sum()) if len(cycles) and "assignment_date" in cycles else 0,
            "short_put_or_recovery_days": int(routed.state.isin(["put", "recovery_im", "cash"]).sum()),
            "call_paused_days": int(call_scale.eq(0).sum()), "call_trade_events": len(call_trades),
            "core_put_simultaneous_exits": len(simultaneous_put),
            "front_fallback_days": int(signal.front_fallback_to_m1.sum()),
            "early_rolls": int(routed.action.eq("put_early_roll60_buyback_and_sell_next_open").sum()),
        }
    audits[f"{scope}_baseline"] = {"core_put_trade_events": len(trades), "call_trade_events": len(call_base_trades)}
    return pd.concat(candidates, ignore_index=True), pd.concat(signals, ignore_index=True), (pd.concat(cycle_rows, ignore_index=True) if cycle_rows else pd.DataFrame()), audits


def main() -> None:
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError("Refusing to overwrite non-init run")
    router_fn, router_source = parity.parity_fixed_runner()
    parity_checks = {scope: parity.no_route_parity(scope, router_fn) for scope in ("real", "model")}
    real_put_engine, model_put_engine, put_source = common.patched_put_engines()
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    call_dir = out / "call_artifacts"
    real_daily, real_signal, real_cycles, real_audit = run_layer("real", router_fn, real_put_engine, model_put_engine, call_dir)
    model_daily, model_signal, model_cycles, model_audit = run_layer("model", router_fn, real_put_engine, model_put_engine, call_dir)
    daily = pd.concat([real_daily, model_daily], ignore_index=True)
    summary, wide, unavailable = common.window_tables(daily)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([real_signal, model_signal], ignore_index=True).to_csv(out / "signal_audit.csv", index=False)
    pd.concat([real_cycles, model_cycles], ignore_index=True).to_csv(out / "cycles.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_state_machines.py").write_text(router_source + "\n\n" + put_source, encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    audit = {"no_route_parity": parity_checks, "real": real_audit, "model": model_audit}
    meta.update(
        scan_type="corrected_im_high_iv_short95_maturity_with_call_suspension",
        baseline={"candidate": "*_imc_coreput_call_baseline", "source": str(common.router.BASE)},
        candidate_grid=[{"maturity": x, "iv_threshold": IV_THRESHOLD, "decay": DECAY} for x in MATURITIES],
        data_snapshot={"real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()), "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date()), "options_sha256": sha(common.router.OPTIONS), "futures_sha256": sha(common.router.FUTURES)},
        cost_model={"one_way_notional": common.router.ONE_WAY_COST, "reserve": 0.30, "cash_annual": 0.03, "call_rule": "original D10/IV26; synchronous close on short-Put route; suspended through Put/cash/recovery; normal re-admission after IMC restoration", "core_put": "current 102% valuation/MOM120 debounce; synchronous route exit", "short_put": "95%; IV>35%; decay60 one-step roll"},
        audit=audit, unavailable_segments=unavailable,
        outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "signals": str(out / "signal_audit.csv"), "cycles": str(out / "cycles.csv"), "executed_state_machines": str(RUN / "executed_state_machines.py")},
        source_hashes={"script": sha(Path(__file__)), "spec": sha(SPEC), "parity_engine": sha(Path(parity.__file__)), "early_valuation": sha(v4.EARLY), "call_component": sha(Path(joint_comp.__file__))},
        warnings=["Historical 102% Put and MOM120 debounce are counterfactual replay.", "Model layer uses theoretical/proxy options and calibrated carry; it is not executable history.", "Real history is short; cycle counts accompany all conclusions.", "No bid-ask, impact, capacity, dynamic margin, forced liquidation, tax, or integer sizing."],
        decision="research_only_pending_interpretation", stability_label="maturity_scan_pending_review", git_status_after=git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = (
        "# IM高IV卖95% Put期限统一复测\n\n"
        "基于统一修正后的IMC/核心Put/卖Put状态机，并加入原Call在卖Put及回本期间同步暂停。\n\n"
        "## Full Results\n\n" + full.to_markdown(index=False) + "\n\n"
        "## Audit\n\n```json\n" + json.dumps(audit, ensure_ascii=False, indent=2) + "\n```\n\n"
        "## Stability\n\n待解释。\n\n## Decision\n\nresearch_only_pending_interpretation\n"
    )
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
