from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

import im_mo_call_daily_entry_profit_roll_v22 as v22
import im_mo_call_overwrite_delta_tenor_v19 as v19
import im_mo_call_valuation_threat_roll_v25 as v25


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_daily_d10_threat_roll_v27"
BASELINE = v22.DAILY
CANDIDATE = "front_d10_iv26_daily_threat5_up5_next1_max5"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "38ab5360438c83b2d2a637d4c33dcdc2df02c001a5117bf9d15b64a469886c73"
V22_OUTPUT = ROOT / "outputs" / "im_mo_call_daily_entry_profit_roll_v22"

FROZEN_HASHES = {
    ROOT / "im_mo_call_daily_entry_profit_roll_v22.py": "18c90e87b4ec0714d9560865f751d4ebb2748d9dec00416d76f09a9651097932",
    ROOT / "im_mo_call_valuation_threat_roll_v25.py": "35ac7b2c5cf44721516c46252b8a800b69e1ae6603f61c538e7ad4e5b56bdb96",
    ROOT / "im_mo_call_valuation_threat_roll_v25r2.py": "391f631fa01e0faa2739a337a0655331de59a2c1cf9ae8ec18888322d032107b",
    ROOT / "im_mo_call_overwrite_delta_tenor_v19.py": "22f5b2fadfd421fa6be0f1680df3c4c6ac04eecb97cca39b6439e11ab8be7920",
    V22_OUTPUT / "call_trades.csv": "4b5485b86c3352ded7827d5c67f5b59739e15f18baf2da4fcdd6d1a44b605923",
    V22_OUTPUT / "signals.csv": "21952c90df5d0af932b0e767a133d6bcd3c0f2ff640e7d372b9a100607e1e6b3",
    V22_OUTPUT / "daily_candidates.csv.gz": "ab93649cde1c86de9f1ff6c30acc8297bf0d9b8e92c346f2a60459ca620a8c6c",
    V22_OUTPUT / "output_manifest.json": "0caece179821cd570a88497c7e9f0002458717796a3cec9ee9cf76a75ff82bdd",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError(f"Formal or staging output already exists: {OUTPUT}")
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v27 specification hash mismatch")
    if SPEC_HASH.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v27 specification sidecar mismatch")
    for path, expected in FROZEN_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen input changed: {path}")
    return {str(path.relative_to(ROOT)): expected for path, expected in FROZEN_HASHES.items()}


def flat_states(dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "history_kind": "daily_d10_no_valuation",
            "date": dates,
            "official_rolling_pe": np.nan,
            "pe_percentile_10y": np.nan,
            "history_start": pd.NaT,
            "history_end": pd.NaT,
            "history_rows": 0,
            "valuation_state": "normal",
            "state_changed": False,
            "state_from": "normal",
            "state_to": "normal",
        }
    )


def cycle_lookup(events: pd.DataFrame, selections: list[v19.Selection]) -> Callable[[pd.Timestamp], v19.Selection | None]:
    selection_by_day = {pd.Timestamp(item.eval_date): item for item in selections}
    event_days = pd.DatetimeIndex(sorted(pd.Timestamp(day) for day in events["eval_date"]))

    def lookup(day: pd.Timestamp) -> v19.Selection | None:
        eligible = event_days[event_days <= day]
        if len(eligible) == 0:
            return None
        return selection_by_day[pd.Timestamp(eligible[-1])]

    return lookup


def d10_model_selector(cycle: Callable[[pd.Timestamp], v19.Selection | None]):
    def selector(
        market: pd.DataFrame,
        dates: pd.DatetimeIndex,
        day: pd.Timestamp,
        execution: pd.Timestamp,
        label: str,
        reason: str,
        state: pd.Series,
    ) -> tuple[v19.Selection | None, dict[str, Any]]:
        del dates
        anchor = cycle(day)
        row = market.set_index("date").loc[day]
        spot = float(row["spot_close"])
        selection = None
        if anchor is not None and anchor.expiry > day:
            selection = v22.model_selection_for_expiry(
                row, day, execution, anchor.month, anchor.expiry, label, reason
            )
        dte = int((selection.expiry - day).days) if selection is not None else np.nan
        return selection, v25.v23.selection_meta(
            selection, state, 10, np.nan, np.nan, np.nan, dte, spot
        )

    return selector


def d10_real_selector(cycle: Callable[[pd.Timestamp], v19.Selection | None]):
    def selector(
        calls: pd.DataFrame,
        market_row: pd.Series,
        day: pd.Timestamp,
        execution: pd.Timestamp,
        label: str,
        reason: str,
        state: pd.Series,
    ) -> tuple[v19.Selection | None, dict[str, Any]]:
        anchor = cycle(day)
        spot = float(market_row["spot_close"])
        selection = None
        if anchor is not None and anchor.expiry > day:
            selection = v22.real_selection_for_expiry(
                calls,
                market_row,
                day,
                execution,
                anchor.month,
                anchor.expiry,
                label,
                reason,
            )
        dte = int((selection.expiry - day).days) if selection is not None else np.nan
        return selection, v25.v23.selection_meta(
            selection, state, 10, np.nan, np.nan, np.nan, dte, spot
        )

    return selector


def normalize(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    keep = frame[columns].copy().sort_values(columns[:2] + [columns[2]]).reset_index(drop=True)
    for column in keep.columns:
        if "date" in column or column.endswith("expiry"):
            keep[column] = pd.to_datetime(keep[column], errors="coerce")
    return keep


def parity_audit(
    baseline_daily: pd.DataFrame,
    baseline_trades: pd.DataFrame,
    baseline_signals: pd.DataFrame,
) -> dict[str, Any]:
    saved_daily = pd.read_csv(V22_OUTPUT / "daily_candidates.csv.gz", parse_dates=["date", "call_expiry"])
    saved_daily = saved_daily[saved_daily["candidate"].eq(BASELINE)].copy()
    saved_trades = pd.read_csv(
        V22_OUTPUT / "call_trades.csv",
        parse_dates=["eval_date", "scheduled_execution_date", "actual_execution_date", "old_expiry", "new_expiry"],
    )
    saved_trades = saved_trades[saved_trades["candidate"].eq(BASELINE)].copy()
    saved_signals = pd.read_csv(
        V22_OUTPUT / "signals.csv",
        parse_dates=["eval_date", "scheduled_execution_date", "selection_expiry", "old_expiry"],
    )
    saved_signals = saved_signals[saved_signals["candidate"].eq(BASELINE)].copy()

    def compare(left: pd.DataFrame, right: pd.DataFrame, keys: list[str]) -> dict[str, Any]:
        common = [column for column in left.columns if column in right.columns]
        left = normalize(left, common)
        right = normalize(right, common)
        row_match = len(left) == len(right)
        numeric = [column for column in common if pd.api.types.is_numeric_dtype(left[column]) and not pd.api.types.is_bool_dtype(left[column])]
        max_diff = 0.0
        if row_match and numeric:
            delta = (left[numeric].astype(float) - right[numeric].astype(float)).abs().to_numpy()
            max_diff = float(np.nanmax(delta)) if np.isfinite(delta).any() else 0.0
        nonnumeric = [column for column in common if column not in numeric]
        object_match = row_match and all(
            left[column].fillna("<NA>").astype(str).equals(right[column].fillna("<NA>").astype(str))
            for column in nonnumeric
        )
        return {
            "rows_saved": len(left),
            "rows_rerun": len(right),
            "row_match": row_match,
            "max_numeric_abs_diff": max_diff,
            "nonnumeric_match": object_match,
            "pass": bool(row_match and max_diff <= 1e-12 and object_match),
            "keys": keys,
        }

    return {
        "daily": compare(saved_daily, baseline_daily, ["layer", "candidate", "date"]),
        "trades": compare(saved_trades, baseline_trades, ["layer", "candidate", "actual_execution_date"]),
        "signals": compare(saved_signals, baseline_signals, ["layer", "candidate", "eval_date"]),
    }


def event_summary(daily: pd.DataFrame, trades: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"]):
        t = trades[trades["layer"].eq(layer) & trades["candidate"].eq(candidate)]
        s = signals[signals["layer"].eq(layer) & signals["candidate"].eq(candidate)]
        delta = group["call_delta"].dropna().abs()
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "signals": len(s),
                "normal_signals": int((~s["reason"].str.startswith("threat_")).sum()) if len(s) else 0,
                "threat_signals": int(s["reason"].str.startswith("threat_").sum()) if len(s) else 0,
                "threat_rolls": int(t["reason"].eq("threat_roll").sum()) if len(t) else 0,
                "threat_stops": int(t["reason"].str.startswith("threat_stop").sum()) if len(t) else 0,
                "open_events": int(t["action"].eq("open").sum()) if len(t) else 0,
                "roll_events": int(t["action"].eq("roll").sum()) if len(t) else 0,
                "close_events": int(t["action"].eq("close").sum()) if len(t) else 0,
                "call_days": int(group["call_contract"].fillna("").ne("").sum()),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "max_abs_call_delta": float(delta.max()) if len(delta) else np.nan,
                "p95_abs_call_delta": float(delta.quantile(0.95)) if len(delta) else np.nan,
                "average_margin_fraction": float(group["call_margin_fraction"].mean()),
                "maximum_margin_fraction": float(group["call_margin_fraction"].max()),
                "capital_breach_days": int(
                    (group["put_mark_fraction"] + group["call_margin_fraction"] > v19.CASH_BASE + 1e-12).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def candidate_audit(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    calls: pd.DataFrame,
    stats: dict[str, dict[str, int]],
) -> dict[str, Any]:
    expected_ret = (
        (1.0 + daily["gross_ret"] + daily["put_pnl_ret"] + daily["call_pnl_ret"])
        * (1.0 - daily["cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        * (1.0 - daily["call_cost_rate"])
        - 1.0
    )
    expected_cash = expected_ret + (
        v19.CASH_BASE - daily["put_mark_fraction"] - daily["call_margin_fraction"]
    ).clip(lower=0.0) * v19.CASH_DAILY
    normal = signals[~signals["reason"].str.startswith("threat_") & signals["contract"].fillna("").ne("")]
    threat = signals[signals["reason"].eq("threat_roll")]
    real_trades = trades[trades["layer"].eq("real")]
    quote_lookup = calls.set_index(["contract", "date"])
    close_errors = 0
    for item in real_trades.itertuples(index=False):
        day = pd.Timestamp(item.actual_execution_date)
        if str(item.old_contract):
            key = (str(item.old_contract), day)
            if key not in quote_lookup.index or abs(float(item.old_close) - float(quote_lookup.loc[key]["close"])) > 1e-10:
                close_errors += 1
        if str(item.new_contract):
            key = (str(item.new_contract), day)
            if key not in quote_lookup.index or abs(float(item.new_close) - float(quote_lookup.loc[key]["close"])) > 1e-10:
                close_errors += 1
    checks = {
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - expected_cash).abs().max()),
        "normal_iv_gate_errors": int((normal["gate_pass"].astype(bool) != normal["gate_iv"].ge(v25.IV_THRESHOLD - 1e-12)).sum()),
        "normal_tier_errors": int(normal["tier"].ne(10).sum()),
        "valuation_state_leak_rows": int(signals["official_rolling_pe"].notna().sum()),
        "threat_trigger_errors": int(threat["threat_otm"].gt(v25.THREAT_OTM + 1e-12).sum()),
        "threat_expiry_errors": int((pd.to_datetime(threat["selection_expiry"]) <= pd.to_datetime(threat["old_expiry"])).sum()),
        "threat_strike_errors": int((threat["selection_strike"] + 1e-12 < threat["old_strike"] * (1.0 + v25.STRIKE_STEP)).sum()),
        "causality_errors": int((pd.to_datetime(signals["scheduled_execution_date"]) <= pd.to_datetime(signals["eval_date"])).sum()),
        "official_close_errors": close_errors,
        "capital_breach_days": int((daily["put_mark_fraction"] + daily["call_margin_fraction"] > v19.CASH_BASE + 1e-12).sum()),
        "final_pending": int(sum(item["final_pending"] for item in stats.values())),
        "scheduled_execution_failures": int(sum(item["scheduled_execution_failures"] for item in stats.values())),
    }
    checks["all_pass"] = bool(
        checks["return_identity_max_abs"] <= 1e-12
        and checks["cash_identity_max_abs"] <= 1e-12
        and all(value == 0 for key, value in checks.items() if key.endswith("errors") or key in {"capital_breach_days", "final_pending"})
    )
    return checks


def metric(formal: pd.DataFrame, layer: str, candidate: str, window: str, column: str) -> float:
    row = formal[
        formal["layer"].eq(layer)
        & formal["candidate"].eq(candidate)
        & formal["window"].eq(window)
    ]
    if len(row) != 1 or not bool(row.iloc[0]["available"]):
        return np.nan
    return float(row.iloc[0][column])


def decision_table(formal: pd.DataFrame, events: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    gates: list[bool] = []
    for layer in ["model", "real"]:
        for window in ["full", "last_10y", "last_5y", "last_3y", "last_1y"]:
            base = metric(formal, layer, BASELINE, window, "ann_return")
            candidate = metric(formal, layer, CANDIDATE, window, "ann_return")
            if np.isnan(base) or np.isnan(candidate):
                limit = np.nan
                passed = True
            else:
                limit = 0.01 if window in {"full", "last_10y", "last_5y"} else 0.03
                passed = candidate >= base - limit - 1e-12
                gates.append(passed)
            rows.append(
                {
                    "gate": "cagr_retention",
                    "layer": layer,
                    "window": window,
                    "baseline": base,
                    "candidate": candidate,
                    "candidate_minus_baseline": candidate - base if np.isfinite(base) and np.isfinite(candidate) else np.nan,
                    "limit": limit,
                    "pass": passed,
                }
            )
    for layer in ["model", "real"]:
        base_row = events[events["layer"].eq(layer) & events["candidate"].eq(BASELINE)].iloc[0]
        cand_row = events[events["layer"].eq(layer) & events["candidate"].eq(CANDIDATE)].iloc[0]
        delta_improvement = float(base_row["max_abs_call_delta"] - cand_row["max_abs_call_delta"])
        delta_pass = delta_improvement >= 0.05 - 1e-12
        margin_pass = float(cand_row["maximum_margin_fraction"]) <= float(base_row["maximum_margin_fraction"]) + 1e-12
        rescue_min = 5 if layer == "model" else 2
        rescue_pass = int(cand_row["threat_rolls"]) >= rescue_min
        gates.extend([delta_pass, margin_pass, rescue_pass])
        rows.extend(
            [
                {"gate": "max_delta_improvement", "layer": layer, "window": "full", "baseline": base_row["max_abs_call_delta"], "candidate": cand_row["max_abs_call_delta"], "candidate_minus_baseline": -delta_improvement, "limit": -0.05, "pass": delta_pass},
                {"gate": "max_margin_non_worse", "layer": layer, "window": "full", "baseline": base_row["maximum_margin_fraction"], "candidate": cand_row["maximum_margin_fraction"], "candidate_minus_baseline": float(cand_row["maximum_margin_fraction"] - base_row["maximum_margin_fraction"]), "limit": 0.0, "pass": margin_pass},
                {"gate": "successful_rescues", "layer": layer, "window": "full", "baseline": 0, "candidate": int(cand_row["threat_rolls"]), "candidate_minus_baseline": int(cand_row["threat_rolls"]), "limit": rescue_min, "pass": rescue_pass},
            ]
        )
    passed = bool(all(gates))
    return pd.DataFrame(rows), {
        "selected": CANDIDATE if passed else BASELINE,
        "decision": "confirm_daily_d10_threat5_for_im_combination" if passed else "keep_daily_d10_without_threat5",
        "performance_and_exposure_gates_pass": passed,
    }


def record_text(formal: pd.DataFrame, events: pd.DataFrame, decision: dict[str, Any], audit: dict[str, Any]) -> str:
    lines = [
        f"# {VERSION} 正式记录",
        "",
        f"结论：`{decision['decision']}`。本记录是研究审计证据，不构成获准实盘。",
        "",
        "## 规则确认",
        "",
        "- 正常入场每日收盘检查D10与合约自身IV>=26%；月度节点仅刷新当前前月周期。",
        "- 已有Call每日检查5%威胁；触发后T+1收盘向上至少5%、向后最近一个到期日，最多连续5次。",
        "- 不含PE估值门控、文章期限/虚值阶梯、TP80、网格Call覆盖。",
        "",
        "## 绩效（计入3%现金收益）",
        "",
        "|层|候选|窗口|CAGR|最大回撤|",
        "|---|---|---:|---:|---:|",
    ]
    for item in formal.itertuples(index=False):
        ann = "N/A" if not item.available else f"{item.ann_return:.2%}"
        dd = "N/A" if not item.available else f"{item.max_dd:.2%}"
        lines.append(f"|{item.layer}|{item.candidate}|{item.window}|{ann}|{dd}|")
    lines.extend(["", "## 暴露与事件", "", "```text", events.to_string(index=False), "```", "", "## 审计", "", f"- 基线复现：{audit['baseline_parity_pass']}", f"- 候选路径审计：{audit['candidate_audit']['all_pass']}", f"- 总审计：{audit['all_pass']}", ""])
    return "\n".join(lines)


def main() -> None:
    frozen = verify_inputs()
    baseline = v19.load_baseline()
    upstream = v19.load_upstream()
    market, market_checks = v19.v6.model_market()
    real_market = market[market["date"].ge(v19.REAL_START)].copy()
    calls = v19.prepare_calls(pd.DatetimeIndex(market["date"]))
    model_dates = pd.DatetimeIndex(market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    model_events = v19.monthly_events(v19.MODEL_START, model_dates, v19.model_roll_dates(model_dates))
    real_rolls = pd.DatetimeIndex(upstream.loc[upstream["roll_to"].notna(), "date"])
    real_events = v19.monthly_events(v19.REAL_START, real_dates, real_rolls)
    model_monthly = v19.build_model_selections(market, model_events, "front", v22.TARGET_DELTA, v22.MONTHLY)
    real_monthly = v19.build_real_selections(calls, real_market, real_events, "front", v22.TARGET_DELTA, v22.MONTHLY)
    model_base = baseline[baseline["layer"].eq("model")].drop(columns=["layer", "candidate"])
    real_base = baseline[baseline["layer"].eq("real")].drop(columns=["layer", "candidate"])

    bm_overlay, bm_trades, bm_signals, _ = v22.run_model(market, model_events, model_monthly, BASELINE, False)
    br_overlay, br_trades, br_signals, _ = v22.run_real(upstream, calls, real_market, real_events, real_monthly, BASELINE, False)
    bm_daily = v19.assemble_candidate(model_base, bm_overlay, BASELINE); bm_daily["layer"] = "model"
    br_daily = v19.assemble_candidate(real_base, br_overlay, BASELINE); br_daily["layer"] = "real"
    baseline_daily = pd.concat([bm_daily, br_daily], ignore_index=True)
    baseline_trades = pd.concat([bm_trades, br_trades], ignore_index=True)
    baseline_signals = pd.concat([bm_signals, br_signals], ignore_index=True)
    parity = parity_audit(baseline_daily, baseline_trades, baseline_signals)

    old_model_selector = v25.v23.model_selection
    old_real_selector = v25.v23.real_selection
    v25.v23.model_selection = d10_model_selector(cycle_lookup(model_events, model_monthly))
    v25.v23.real_selection = d10_real_selector(cycle_lookup(real_events, real_monthly))
    try:
        cm_overlay, cm_trades, cm_signals, cm_stats = v25.run_model(
            market, model_events, flat_states(model_dates), CANDIDATE, True
        )
        cr_overlay, cr_trades, cr_signals, cr_stats = v25.run_real(
            upstream, calls, real_market, real_events, flat_states(real_dates), CANDIDATE, True
        )
    finally:
        v25.v23.model_selection = old_model_selector
        v25.v23.real_selection = old_real_selector
    cm_daily = v19.assemble_candidate(model_base, cm_overlay, CANDIDATE); cm_daily["layer"] = "model"
    cr_daily = v19.assemble_candidate(real_base, cr_overlay, CANDIDATE); cr_daily["layer"] = "real"
    candidate_daily = pd.concat([cm_daily, cr_daily], ignore_index=True)
    candidate_trades = pd.concat([cm_trades, cr_trades], ignore_index=True)
    candidate_signals = pd.concat([cm_signals, cr_signals], ignore_index=True)
    stats = {"model": cm_stats, "real": cr_stats}

    daily = pd.concat([baseline_daily, candidate_daily], ignore_index=True).sort_values(["layer", "candidate", "date"]).reset_index(drop=True)
    trades = pd.concat([baseline_trades, candidate_trades], ignore_index=True).sort_values(["layer", "candidate", "actual_execution_date"]).reset_index(drop=True)
    signals = pd.concat([baseline_signals, candidate_signals], ignore_index=True).sort_values(["layer", "candidate", "eval_date"]).reset_index(drop=True)
    formal, annual = v19.metrics_tables(daily)
    events = event_summary(daily, trades, signals)
    candidate_checks = candidate_audit(candidate_daily, candidate_trades, candidate_signals, calls, stats)
    gates, decision = decision_table(formal, events)
    parity_pass = bool(all(item["pass"] for item in parity.values()))
    audit = {
        "baseline_parity": parity,
        "baseline_parity_pass": parity_pass,
        "candidate_audit": candidate_checks,
        "market_checks": market_checks,
        "all_pass": bool(parity_pass and candidate_checks["all_pass"]),
    }
    decision["audit_pass"] = audit["all_pass"]
    decision["hard_pass"] = bool(decision["performance_and_exposure_gates_pass"] and audit["all_pass"])
    if not decision["hard_pass"]:
        decision["selected"] = BASELINE
        decision["decision"] = "keep_daily_d10_without_threat5"

    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "call_trades.csv", index=False)
    signals.to_csv(STAGING / "signals.csv", index=False)
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    events.to_csv(STAGING / "event_exposure_summary.csv", index=False)
    gates.to_csv(STAGING / "decision_table.csv", index=False)
    (STAGING / "audit_summary.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (STAGING / "decision_summary.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (STAGING / "execution_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "record.md").write_text(record_text(formal, events, decision, audit), encoding="utf-8")
    (STAGING / "command_log.txt").write_text(f"python {Path(__file__).name}\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "frozen_inputs": frozen,
        "sample": {"model": [str(v19.MODEL_START.date()), str(v19.END.date())], "real": [str(v19.REAL_START.date()), str(v19.END.date())]},
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
        "git_status": subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(STAGING.iterdir()) if path.is_file()},
    }
    (STAGING / "output_manifest.json").write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING.replace(OUTPUT)
    print(json.dumps({"decision": decision, "audit": audit, "events": events.to_dict("records")}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
