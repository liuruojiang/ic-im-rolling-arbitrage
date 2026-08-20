from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_valuation_threat_roll_v25 as threat
import im_mo_call_valuation_hysteresis_v23 as v23
import im_mo_csi1000_put_protection_battery_v6 as v6


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_call_threat_roll_extended_proxy_v26r1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "82236d6e0125d7c53e8467650b198384f3c6afc016e0577a11559c214977971d"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260819_new_strategy_research_im_mo_call_threat_roll_extended_proxy_v26r1_im_mo_call_overwrite_pre2015_ivrv_p25_p50_p75_fixed_threat5_split_pe_history"
)
FAILED_V26 = ROOT / "outputs" / "im_mo_call_threat_roll_extended_proxy_v26_failed_preflight" / "record.md"
V25R2_OUTPUT = ROOT / "outputs" / "im_mo_call_valuation_threat_roll_v25r2"

v19 = v23.v19
END = pd.Timestamp("2026-08-14")
QIVX_START = pd.Timestamp("2015-04-16")
PE_START = pd.Timestamp("2012-06-29")
IV_SCENARIOS = {
    "p25": 0.9984611590392641,
    "p50": 1.1352106814268557,
    "p75": 1.2692067075266782,
}
AXES = ("normal", "pe20_60")

PRICE = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv"
TRI = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_H00852.csv"
GOV10Y = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "chinabond_government_10y.csv"
PE_SOURCE = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v4" / "csindex_000852.csv"

FROZEN_HASHES = {
    ROOT / "im_mo_call_valuation_threat_roll_v25r2.py": "391f631fa01e0faa2739a337a0655331de59a2c1cf9ae8ec18888322d032107b",
    ROOT / "docs" / "im_mo_call_valuation_threat_roll_v25r2_spec.md": "52a162222339893b004d5eac402730db53701a87a13353bfe3b198ae9ea50fc7",
    V25R2_OUTPUT / "metrics_by_window.csv": "fb972c7120c18f4425424bee6e1ebdbc2ddeb78899d2a587f86bb8d2573c7bf2",
    ROOT / "im_mo_call_valuation_hysteresis_v23.py": "7266ad401ff0ec2e6bc4d9f4fc417cb1f8c3cd5d1e5673475d452af532d060cf",
    ROOT / "im_mo_csi1000_put_protection_battery_v6.py": "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d",
    PRICE: "e42b94ad52a39687a5a0d92fe7f3c28481f34420bac6ac0d0c62ffcdf0e68bf9",
    TRI: "6483caa2cba5c2bf7e300c949380ddc8ffeaf7877152679e3754a99d841ae40a",
    GOV10Y: "f70dc82a18da9e69176393066467f68666fe451c3f659a0a36b42a351c833d39",
    PE_SOURCE: "2022d89da20cb4e81e63c82999ed1deb2488353199d3f40fa0f1f7d44401dd89",
    ROOT / "data" / "ic_510500_put_proxy_validation_v1" / "qvix_50etf.csv": "d2a3c1ce87434956accbb3b5b2c3ea15cb1f59f8316c88a4113aea698e4b5a10",
    ROOT / "data" / "ic_510500_put_proxy_validation_v1" / "sina_510050_etf.csv": "e2782791949918ee67cf3784bb51906ab86a98108f07f365203547d265d2acfd",
    ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "sina_sh000852_index.csv": "9d3995a7189137fee79e5aaa2a58aced57101a1329f1236aca8a0adc86babe74",
    FAILED_V26: "",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v26r1 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v26r1 specification sidecar mismatch")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal or staging v26r1 output already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Preregistered v26r1 scan folder is missing")
    hashes: dict[str, str] = {}
    for path, expected in FROZEN_HASHES.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing frozen v26r1 input: {path}")
        actual = sha256(path)
        if expected and actual != expected:
            raise RuntimeError(f"Frozen v26r1 input changed: {path}")
        hashes[str(path.relative_to(ROOT))] = actual
    return hashes


def build_market(multiplier: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    price = pd.read_csv(PRICE, parse_dates=["date"])[["date", "close"]].rename(
        columns={"close": "spot_close"}
    )
    tri = pd.read_csv(TRI, parse_dates=["date"])[["date", "close"]].rename(
        columns={"close": "tri_close"}
    )
    frame = price.merge(tri, on="date", validate="one_to_one").sort_values("date")
    frame["rv60"] = (
        np.log(frame["spot_close"]).diff().rolling(60, min_periods=60).std(ddof=1)
        * math.sqrt(252.0)
    )
    targets = frame[["date"]].copy()
    targets["prior_target"] = targets["date"] - pd.DateOffset(years=1)
    prior = frame[["date", "spot_close", "tri_close"]].rename(
        columns={"date": "prior_date", "spot_close": "prior_spot", "tri_close": "prior_tri"}
    )
    targets = pd.merge_asof(
        targets.sort_values("prior_target"),
        prior.sort_values("prior_date"),
        left_on="prior_target",
        right_on="prior_date",
        direction="backward",
        allow_exact_matches=True,
    )
    frame = frame.merge(
        targets[["date", "prior_spot", "prior_tri"]], on="date", validate="one_to_one"
    )
    frame["dividend_close"] = (
        (frame["tri_close"] / frame["prior_tri"])
        / (frame["spot_close"] / frame["prior_spot"])
        - 1.0
    ).clip(lower=0.0)
    gov = pd.read_csv(GOV10Y, parse_dates=["date"]).rename(columns={"date": "gov_date"})
    frame = pd.merge_asof(
        frame.sort_values("date"),
        gov.sort_values("gov_date"),
        left_on="date",
        right_on="gov_date",
        direction="backward",
        allow_exact_matches=True,
    )
    frame["rate_close"] = frame["gov10y_yield"]
    post_market, _ = v6.model_market()
    post = post_market[["date", "sigma_close"]].rename(columns={"sigma_close": "post_sigma"})
    frame = frame.merge(post, on="date", how="left", validate="one_to_one")
    frame["sigma_close"] = np.where(
        frame["date"].ge(QIVX_START), frame["post_sigma"], frame["rv60"] * multiplier
    )
    frame["base_prior_close"] = frame["tri_close"].shift(1)
    required = [
        "spot_close",
        "tri_close",
        "rv60",
        "dividend_close",
        "rate_close",
        "sigma_close",
        "base_prior_close",
    ]
    frame = frame[frame["date"].le(END) & frame[required].notna().all(axis=1)].copy()
    frame = frame[frame["date"].ge(pd.Timestamp("2007-01-04"))].reset_index(drop=True)
    if frame.empty or (frame[["spot_close", "tri_close", "sigma_close"]] <= 0).any().any():
        raise RuntimeError("Invalid extended theoretical market")
    post_rows = frame[frame["date"].ge(QIVX_START)]
    parity = float((post_rows["sigma_close"] - post_rows["post_sigma"]).abs().max())
    checks = {
        "start": str(frame["date"].min().date()),
        "end": str(frame["date"].max().date()),
        "rows": len(frame),
        "pre_qivx_rows": int(frame["date"].lt(QIVX_START).sum()),
        "post_qivx_rows": int(frame["date"].ge(QIVX_START).sum()),
        "sigma_min": float(frame["sigma_close"].min()),
        "sigma_max": float(frame["sigma_close"].max()),
        "post_qivx_sigma_parity_max_abs": parity,
    }
    if parity > 1e-14:
        raise RuntimeError(f"Post-QIVX sigma parity failed: {parity}")
    return frame, checks


def build_pe_states(market_dates: pd.DatetimeIndex) -> pd.DataFrame:
    source = pd.read_csv(PE_SOURCE, parse_dates=["date"]).sort_values("date")
    source = source[
        source["date"].between(PE_START, END) & source["official_rolling_pe"].notna()
    ][["date", "official_rolling_pe"]].reset_index(drop=True)
    if source.empty or pd.Timestamp(source.iloc[0]["date"]) != PE_START:
        raise RuntimeError("Unexpected official rolling PE start")
    rows: list[dict[str, Any]] = []
    state = "normal"
    for item in source.itertuples(index=False):
        day = pd.Timestamp(item.date)
        left = max(PE_START, day - pd.DateOffset(years=10))
        history = source[source["date"].between(left, day)]
        percentile = float(
            (history["official_rolling_pe"] <= float(item.official_rolling_pe)).mean()
        )
        prior_state = state
        if percentile <= v23.LOW_ENTER + 1e-15:
            state = "low_recovery"
        elif percentile >= v23.LOW_EXIT - 1e-15:
            state = "normal"
        rows.append(
            {
                "history_kind": "prepublication_backcast_from_2012_06_29",
                "date": day,
                "official_rolling_pe": float(item.official_rolling_pe),
                "pe_percentile_10y": percentile,
                "history_start": pd.Timestamp(history["date"].min()),
                "history_end": pd.Timestamp(history["date"].max()),
                "history_rows": len(history),
                "valuation_state": state,
                "state_changed": state != prior_state,
                "state_from": prior_state,
                "state_to": state,
            }
        )
    result = pd.DataFrame(rows)
    result = result[result["date"].isin(market_dates)].reset_index(drop=True)
    if result.empty or result["date"].min() != PE_START:
        raise RuntimeError("PE state and market calendars do not align at start")
    return result


def normal_states(market: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "history_kind": "no_valuation_force_normal",
            "date": market["date"],
            "official_rolling_pe": np.nan,
            "pe_percentile_10y": np.nan,
            "history_start": market["date"],
            "history_end": market["date"],
            "history_rows": 0,
            "valuation_state": "normal",
            "state_changed": False,
            "state_from": "normal",
            "state_to": "normal",
        }
    )


def model_events(start: pd.Timestamp, dates: pd.DatetimeIndex) -> pd.DataFrame:
    rolls: list[pd.Timestamp] = []
    for year in range(start.year, END.year + 1):
        for month in range(1, 13):
            day = v6.third_friday(pd.Timestamp(year, month, 1), dates)
            if day in dates and start <= day <= END:
                rolls.append(day)
    return v19.monthly_events(start, dates, pd.DatetimeIndex(sorted(set(rolls))))


def proxy_base(market: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    frame = market[["date", "tri_close"]].copy()
    frame["gross_ret"] = frame["tri_close"].pct_change().fillna(0.0)
    roll_dates = set(pd.to_datetime(events.loc[events["reason"].eq("monthly"), "current_expiry"]))
    frame["cost_rate"] = np.where(frame["date"].isin(roll_dates), 0.0002, 0.0)
    frame.loc[frame.index[0], "cost_rate"] = 0.0001
    frame["put_pnl_ret"] = 0.0
    frame["put_cost_rate"] = 0.0
    frame["put_mark_fraction"] = 0.0
    return frame


def candidate_label(scenario: str, axis: str, rescue: bool) -> str:
    return f"{scenario}_{axis}_{'threat5' if rescue else 'no_rescue'}"


def exposure_summary(
    daily: pd.DataFrame, trades: pd.DataFrame, stats: dict[str, dict[str, int]]
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate"):
        t = trades[trades["candidate"].eq(candidate)]
        delta = group["call_delta"].dropna()
        candidate_stats = stats.get(candidate, {})
        rows.append(
            {
                "candidate": candidate,
                "call_days": int(group["call_contract"].fillna("").ne("").sum()),
                "call_pnl_sum": float(group["call_pnl_ret"].sum()),
                "call_cost_sum": float(group["call_cost_rate"].sum()),
                "max_call_delta": float(delta.max()) if len(delta) else np.nan,
                "p95_call_delta": float(delta.quantile(0.95)) if len(delta) else np.nan,
                "max_margin_fraction": float(group["call_margin_fraction"].max()),
                "capital_breach_days": int(
                    (group["call_margin_fraction"] > v19.CASH_BASE + 1e-12).sum()
                ),
                "threat_rolls": int(t["reason"].eq("threat_roll").sum()) if len(t) else 0,
                "threat_stops": int(t["reason"].str.startswith("threat_stop").sum()) if len(t) else 0,
                "max_consecutive_threat_rolls": int(
                    candidate_stats.get("max_consecutive_threat_rolls", 0)
                ),
            }
        )
    return pd.DataFrame(rows)


def metric_value(
    formal: pd.DataFrame, candidate: str, window: str, column: str
) -> float:
    row = formal[formal["candidate"].eq(candidate) & formal["window"].eq(window)]
    if len(row) != 1 or not bool(row.iloc[0]["available"]):
        raise RuntimeError(f"Missing metric {candidate} {window}")
    return float(row.iloc[0][column])


def exposure_value(exposure: pd.DataFrame, candidate: str, column: str) -> float:
    row = exposure[exposure["candidate"].eq(candidate)]
    return float(row.iloc[0][column])


def pair_comparison(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    stats: dict[str, dict[str, int]],
    audit_ok: bool,
) -> tuple[pd.DataFrame, dict[str, bool]]:
    rows: list[dict[str, Any]] = []
    axis_pass: dict[str, bool] = {}
    for axis in AXES:
        scenario_passes: list[bool] = []
        for scenario, multiplier in IV_SCENARIOS.items():
            baseline = candidate_label(scenario, axis, False)
            candidate = candidate_label(scenario, axis, True)
            values: dict[str, Any] = {
                "scenario": scenario,
                "ivrv_multiplier": multiplier,
                "axis": axis,
                "baseline": baseline,
                "candidate": candidate,
            }
            return_pass = True
            for window in ["full", "last_10y", "last_5y", "last_3y", "last_1y"]:
                ann_delta = metric_value(formal, candidate, window, "ann_return") - metric_value(
                    formal, baseline, window, "ann_return"
                )
                dd_improvement = metric_value(formal, candidate, window, "max_dd") - metric_value(
                    formal, baseline, window, "max_dd"
                )
                values[f"{window}_ann_delta"] = ann_delta
                values[f"{window}_maxdd_improvement"] = dd_improvement
                tolerance = 0.01 if window in {"full", "last_10y"} else 0.03
                return_pass &= ann_delta >= -tolerance - 1e-12
            delta_improvement = exposure_value(exposure, baseline, "max_call_delta") - exposure_value(
                exposure, candidate, "max_call_delta"
            )
            rolls = int(stats[candidate]["threat_rolls"])
            exposure_gate = delta_improvement >= 0.10 - 1e-12
            event_gate = rolls >= 5
            execution_gate = (
                stats[candidate]["final_pending"] == 0
                and exposure_value(exposure, candidate, "capital_breach_days") == 0
            )
            hard_pass = bool(
                return_pass and exposure_gate and event_gate and execution_gate and audit_ok
            )
            values.update(
                {
                    "max_call_delta_improvement": delta_improvement,
                    "threat_rolls": rolls,
                    "return_gate": return_pass,
                    "exposure_gate": exposure_gate,
                    "event_gate": event_gate,
                    "execution_gate": execution_gate,
                    "audit_gate": audit_ok,
                    "hard_pass": hard_pass,
                }
            )
            scenario_passes.append(hard_pass)
            rows.append(values)
        axis_pass[axis] = all(scenario_passes)
    return pd.DataFrame(rows), axis_pass


def segment_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    segments = {
        "pre_qivx": (pd.Timestamp("2007-01-04"), QIVX_START - pd.Timedelta(days=1)),
        "post_qivx": (QIVX_START, END),
    }
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate"):
        for segment, (start, end) in segments.items():
            sample = group[group["date"].between(start, end)]
            if sample.empty:
                continue
            rows.append(
                {
                    "candidate": candidate,
                    "segment": segment,
                    "start": sample["date"].min(),
                    "end": sample["date"].max(),
                    "rows": len(sample),
                    **v19.metrics(sample["cash_ret"]),
                }
            )
    return pd.DataFrame(rows)


def audit_results(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    states: pd.DataFrame,
    markets: dict[str, pd.DataFrame],
    checks: dict[str, dict[str, Any]],
    stats: dict[str, dict[str, int]],
) -> dict[str, Any]:
    expected_ret = (
        1.0 + daily["gross_ret"] + daily["put_pnl_ret"] + daily["call_pnl_ret"]
    ) * (1.0 - daily["cost_rate"]) * (1.0 - daily["put_cost_rate"]) * (
        1.0 - daily["call_cost_rate"]
    ) - 1.0
    expected_cash = daily["ret"] + (
        v19.CASH_BASE - daily["put_mark_fraction"] - daily["call_margin_fraction"]
    ).clip(lower=0.0) * v19.CASH_DAILY
    threat_signals = signals[signals["reason"].str.startswith("threat_")]
    threat_roll_signals = threat_signals[threat_signals["reason"].eq("threat_roll")]
    threat_trades = trades[trades["reason"].eq("threat_roll")]
    normal_selected = signals[
        signals["reason"].isin(["monthly", "daily_entry"])
        & signals["contract"].fillna("").ne("")
    ]
    normal_gate_errors = int(
        (
            normal_selected["gate_pass"].astype(bool)
            != normal_selected["gate_iv"].ge(v23.IV_THRESHOLD - 1e-12)
        ).sum()
    )
    threat_errors = int(
        (threat_signals["threat_otm"] > threat.THREAT_OTM + 1e-12).sum()
        + (
            pd.to_datetime(threat_trades["new_expiry"])
            <= pd.to_datetime(threat_trades["old_expiry"])
        ).sum()
        + (
            pd.to_numeric(threat_roll_signals["selection_strike"], errors="coerce")
            + 1e-12
            < pd.to_numeric(threat_roll_signals["old_strike"], errors="coerce")
            * (1.0 + threat.STRIKE_STEP)
        ).sum()
    )
    causality = int(
        (signals["eval_date"] >= signals["scheduled_execution_date"]).sum()
        + (trades["eval_date"] >= trades["actual_execution_date"]).sum()
    )
    pe_future_errors = int((states["history_end"] > states["date"]).sum())
    pair_date_errors = 0
    for scenario in IV_SCENARIOS:
        for axis in AXES:
            a = daily[daily["candidate"].eq(candidate_label(scenario, axis, False))]["date"]
            b = daily[daily["candidate"].eq(candidate_label(scenario, axis, True))]["date"]
            pair_date_errors += int(not a.reset_index(drop=True).equals(b.reset_index(drop=True)))
    post_sigma_error = max(item["post_qivx_sigma_parity_max_abs"] for item in checks.values())
    final_pending = sum(item["final_pending"] for item in stats.values())
    capital_breach_days = int(
        (daily["call_margin_fraction"] > v19.CASH_BASE + 1e-12).sum()
    )
    result = {
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - expected_cash).abs().max()),
        "normal_iv_gate_errors": normal_gate_errors,
        "threat_rule_errors": threat_errors,
        "causality_errors": causality,
        "pe_future_errors": pe_future_errors,
        "pair_date_errors": pair_date_errors,
        "post_qivx_sigma_parity_max_abs": post_sigma_error,
        "final_pending": final_pending,
        "capital_breach_days": capital_breach_days,
        "market_checks": checks,
    }
    result["all_pass"] = bool(
        result["return_identity_max_abs"] <= 1e-12
        and result["cash_identity_max_abs"] <= 1e-12
        and normal_gate_errors == 0
        and threat_errors == 0
        and causality == 0
        and pe_future_errors == 0
        and pair_date_errors == 0
        and post_sigma_error <= 1e-14
        and final_pending == 0
        and capital_breach_days == 0
    )
    return result


def decision_result(axis_pass: dict[str, bool], pair_table: pd.DataFrame) -> dict[str, Any]:
    scenario_directions = []
    for row in pair_table.itertuples(index=False):
        scenario_directions.append(np.sign(float(row.full_ann_delta)))
    if axis_pass["normal"] and axis_pass["pe20_60"]:
        conclusion = "extended_proxy_directionally_supported_both_axes"
        stability = "wide_stable_across_iv_assumptions_proxy_only"
    elif axis_pass["normal"] or axis_pass["pe20_60"]:
        conclusion = "extended_proxy_axis_dependent"
        stability = "axis_dependent_proxy_only"
    elif len(set(scenario_directions)) > 1:
        conclusion = "iv_assumption_sensitive"
        stability = "data_sensitive"
    else:
        conclusion = "extended_proxy_not_supported"
        stability = "reject"
    return {
        "conclusion": conclusion,
        "normal_axis_pass": axis_pass["normal"],
        "pe20_60_axis_pass": axis_pass["pe20_60"],
        "stability_label": stability,
        "live_approved": False,
        "evidence_scope": "prepublication_index_backcast_and_synthetic_call_proxy_only",
    }


def scan_tables(formal: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    long = formal.rename(
        columns={"window": "segment", "actual_start": "start"}
    )[["candidate", "segment", "start", "end", "rows", "ann_return", "ann_vol", "sharpe_repo", "max_dd"]]
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in formal.groupby("candidate"):
        row: dict[str, Any] = {"candidate": candidate}
        for item in group.itertuples(index=False):
            row[f"ann_return_{item.window}"] = float(item.ann_return)
            row[f"max_dd_{item.window}"] = float(item.max_dd)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def record_text(
    formal: pd.DataFrame,
    pair_table: pd.DataFrame,
    exposure: pd.DataFrame,
    segments: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    focus = formal[formal["window"].isin(["full", "last_10y", "last_5y", "last_3y", "last_1y"])]
    lines = [
        "# IM + MO Call 5%威胁救援最大历史代理检验 v26r1",
        "",
        f"Decision: `{decision['conclusion']}`；未批准实盘。",
        f"Stability: `{decision['stability_label']}`。",
        "Data: 无估值轴2007年起；PE轴2012-06-29起；结束2026-08-14。2015年前为RV×固定IV/RV倍数，2015年后与冻结QIVX模型一致。",
        "Execution: T收盘信号、T+1收盘理论价；Call每边1bp；代理底仓月滚2bp；70%余额净年化3%。",
        "Scope: 中证1000TRI+合成Call，不含历史IM贴水、真实MO或Put。",
        "",
        "## Window Results",
        "",
        "|candidate|window|CAGR|MaxDD|Sharpe|",
        "|---|---|---:|---:|---:|",
    ]
    for row in focus.itertuples(index=False):
        lines.append(
            f"|{row.candidate}|{row.window}|{row.ann_return:.2%}|{row.max_dd:.2%}|{row.sharpe_repo:.3f}|"
        )
    lines.extend(
        [
            "", "## Pair Decisions", "", pair_table.to_markdown(index=False),
            "", "## Exposure", "", exposure.to_markdown(index=False),
            "", "## Pre/Post QIVX Segments", "", segments.to_markdown(index=False),
            "", "## Audit", "", "```json", json.dumps(audit, ensure_ascii=False, indent=2), "```",
            "", "## Decision", "", json.dumps(decision, ensure_ascii=False, indent=2),
            "", "本结果是发布前指数回算与合成Call诊断，不是历史可交易IM/MO，也不是交易建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    states: pd.DataFrame,
    formal: pd.DataFrame,
    annual: pd.DataFrame,
    exposure: pd.DataFrame,
    pair_table: pd.DataFrame,
    segments: pd.DataFrame,
    existing_reference: pd.DataFrame,
    decision: dict[str, Any],
    audit: dict[str, Any],
    record: str,
    stats: dict[str, dict[str, int]],
    source_hashes: dict[str, str],
) -> None:
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "call_trades.csv", index=False)
    signals.to_csv(STAGING / "signals.csv", index=False)
    states.to_csv(STAGING / "pe_states_2012_backcast.csv.gz", index=False, compression="gzip")
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    exposure.to_csv(STAGING / "event_exposure_summary.csv", index=False)
    pair_table.to_csv(STAGING / "pair_comparison.csv", index=False)
    segments.to_csv(STAGING / "segment_metrics.csv", index=False)
    existing_reference.to_csv(STAGING / "v25r2_existing_2015_model_reference.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "audit_summary.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "execution_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python im_mo_call_threat_roll_extended_proxy_v26r1.py\n", encoding="utf-8"
    )
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
        "sample": {
            "normal_axis": [str(daily[daily["candidate"].str.contains("_normal_")]["date"].min().date()), str(END.date())],
            "pe_axis": [str(daily[daily["candidate"].str.contains("_pe20_60_")]["date"].min().date()), str(END.date())],
        },
        "proxy_scope": "CSI1000 total return index plus synthetic Call; no historical IM basis or Put",
        "frictions": {
            "base_monthly_roll": 0.0002,
            "call_one_way": v19.CALL_BASKET_SIDE_COST,
            "cash_annual": 0.03,
            "bid_ask_impact": "excluded",
        },
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sorted(STAGING.iterdir())
            if path.is_file()
        },
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    STAGING.replace(OUTPUT)


def update_scan(
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    record: str,
    decision: dict[str, Any],
) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("python im_mo_call_threat_roll_extended_proxy_v26r1.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "harness_research",
            "baseline": {
                "pairs": "same IV scenario and valuation axis without threat rescue",
                "proxy": "CSI1000 TRI plus synthetic Call; no historical IM basis or Put",
            },
            "candidate_grid": list(scan_wide["candidate"]),
            "data_snapshot": {
                "normal_axis_start": str(scan_long[scan_long["candidate"].str.contains("_normal_")]["start"].min().date()),
                "pe_axis_start": str(scan_long[scan_long["candidate"].str.contains("_pe20_60_")]["start"].min().date()),
                "end": str(END.date()),
                "price_source": str(PRICE.relative_to(ROOT)),
                "tri_source": str(TRI.relative_to(ROOT)),
                "pe_source": str(PE_SOURCE.relative_to(ROOT)),
                "pre_qivx_iv_proxy": IV_SCENARIOS,
            },
            "cost_model": {
                "base_monthly_roll": 0.0002,
                "call_basket_one_way": v19.CALL_BASKET_SIDE_COST,
                "cash_annual_return": 0.03,
                "execution": "T close signal, T+1 close theoretical execution",
                "bid_ask_and_impact": "excluded",
            },
            "outputs": {
                "record": str(SCAN / "record.md"),
                "scan_summary": str(SCAN / "scan_summary.csv"),
                "window_metrics": str(SCAN / "window_metrics.csv"),
                "scan_meta": str(meta_path),
                "command_log": str(SCAN / "command_log.txt"),
            },
            "preliminary_decision": decision["conclusion"],
            "preliminary_stability_label": decision["stability_label"],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    source_hashes = verify_inputs()
    daily_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    signal_parts: list[pd.DataFrame] = []
    states_parts: list[pd.DataFrame] = []
    stats: dict[str, dict[str, int]] = {}
    markets: dict[str, pd.DataFrame] = {}
    market_checks: dict[str, dict[str, Any]] = {}
    for scenario, multiplier in IV_SCENARIOS.items():
        market, checks = build_market(multiplier)
        markets[scenario] = market
        market_checks[scenario] = checks
        full_dates = pd.DatetimeIndex(market["date"])
        pe_states = build_pe_states(full_dates)
        # The preregistered PE axis uses only the common official-PE/market
        # calendar.  Two early market days have an explicit missing PE value;
        # do not forward-fill information that was not present in the source.
        pe_market = market[market["date"].isin(pe_states["date"])].reset_index(drop=True)
        if len(pe_states) != len(pe_market) or not pe_states["date"].equals(pe_market["date"]):
            raise RuntimeError("PE states do not cover every PE-axis market day")
        for axis, axis_market, states, force_normal in [
            ("normal", market, normal_states(market), True),
            ("pe20_60", pe_market, pe_states, False),
        ]:
            dates = pd.DatetimeIndex(axis_market["date"])
            events = model_events(pd.Timestamp(dates[0]), dates)
            base = proxy_base(axis_market, events)
            no_label = candidate_label(scenario, axis, False)
            yes_label = candidate_label(scenario, axis, True)
            no_overlay, no_trades, no_signals, no_stats = v23.run_model(
                axis_market, events, states, no_label, force_normal
            )
            yes_overlay, yes_trades, yes_signals, yes_stats = threat.run_model(
                axis_market, events, states, yes_label, force_normal
            )
            no_candidate = v19.assemble_candidate(base, no_overlay, no_label)
            yes_candidate = v19.assemble_candidate(base, yes_overlay, yes_label)
            no_candidate["layer"] = "extended_proxy"
            yes_candidate["layer"] = "extended_proxy"
            daily_parts.extend([no_candidate, yes_candidate])
            trade_parts.extend([no_trades, yes_trades])
            signal_parts.extend([no_signals, yes_signals])
            states_copy = states.copy()
            states_copy["scenario"] = scenario
            states_copy["axis"] = axis
            states_parts.append(states_copy)
            stats[no_label] = {
                "final_pending": no_stats["final_pending"],
                "threat_rolls": 0,
                "threat_stops": 0,
                "max_consecutive_threat_rolls": 0,
            }
            stats[yes_label] = {
                "final_pending": yes_stats["final_pending"],
                "threat_rolls": yes_stats["threat_rolls"],
                "threat_stops": yes_stats["threat_no_contract_stops"] + yes_stats["threat_max5_stops"],
                "max_consecutive_threat_rolls": yes_stats["max_consecutive_threat_rolls"],
                "blocked_days": yes_stats["blocked_days"],
                "reenable_events": yes_stats["reenable_events"],
            }
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(
        ["candidate", "date"]
    ).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True).sort_values(
        ["candidate", "actual_execution_date"]
    ).reset_index(drop=True)
    signals = pd.concat(signal_parts, ignore_index=True).sort_values(
        ["candidate", "eval_date"]
    ).reset_index(drop=True)
    states = pd.concat(states_parts, ignore_index=True).drop_duplicates(
        ["scenario", "axis", "date"]
    )
    formal, annual = v19.metrics_tables(daily)
    exposure = exposure_summary(daily, trades, stats)
    segments = segment_metrics(daily)
    audit = audit_results(daily, trades, signals, states, markets, market_checks, stats)
    pair_table, axis_pass = pair_comparison(formal, exposure, stats, bool(audit["all_pass"]))
    decision = decision_result(axis_pass, pair_table)
    scan_long, scan_wide = scan_tables(formal)
    existing = pd.read_csv(V25R2_OUTPUT / "metrics_by_window.csv")
    existing = existing[
        existing["layer"].eq("model")
        & existing["candidate"].isin(
            [
                "article_pe20_60_hysteresis_iv26_daily",
                "article_pe20_60_hysteresis_iv26_daily_threat5_up5_next1_max5",
            ]
        )
    ].copy()
    record = record_text(formal, pair_table, exposure, segments, decision, audit)
    update_scan(scan_long, scan_wide, record, decision)
    write_outputs(
        daily,
        trades,
        signals,
        states,
        formal,
        annual,
        exposure,
        pair_table,
        segments,
        existing,
        decision,
        audit,
        record,
        stats,
        source_hashes,
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
