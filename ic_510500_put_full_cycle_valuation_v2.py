from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_proxy_validation_v1 as proxy
from im_monthly_roll_valuation_gated_put_v1 import walk_forward_forecast


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_full_cycle_valuation_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "01e90e66639c5dc35588c17f5de01da73dbd1c61a1eee1811567c678f915b04e"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_full_cycle_valuation_v2"

FULL_STATES_PATH = ROOT / "outputs" / "ic_im_valuation_risk_premium_forecast_v2" / "monthly_valuation_state.csv"
LEGACY_STATES_PATH = ROOT / "outputs" / "ic_im_valuation_risk_premium_forecast_v3" / "monthly_valuation_state.csv"
VALUATION_PATH = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "legulegu_000905_valuation.csv"
PRICE_PATH = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000905.csv"
TRI_PATH = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_H00905.csv"
GOV10Y_PATH = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "chinabond_government_10y.csv"
PROXY_DATA_MANIFEST = ROOT / "data" / "ic_510500_put_proxy_validation_v1" / "data_manifest.json"

MODEL_START = proxy.MODEL_START
REAL_START = proxy.REAL_START
END = proxy.END
TRADING_DAYS = proxy.TRADING_DAYS
FEATURES = ["pe_aggregate_ttm", "pb_aggregate", "erp", "trailing_dividend_contribution"]
VARIANTS = [
    "always_50",
    "always_100",
    "legacy_2008_dynamic",
    "full_2007_dynamic",
    "frozen_2007_2014_ecdf",
    "absolute_four_factor_gate",
]
ALL_VARIANTS = ["no_put", *VARIANTS]
START_SENSITIVITY_YEARS = [2007, 2008, 2009, 2010, 2011]
WINDOWS = proxy.WINDOWS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v2 specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Frozen v2 specification sidecar mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")

    manifest = json.loads(PROXY_DATA_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("spec_sha256") != proxy.SPEC_HASH:
        raise RuntimeError("Proxy data manifest is not tied to frozen proxy v1 spec")
    for filename, item in manifest["files"].items():
        path = proxy.DATA / filename
        if not path.exists() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Frozen proxy data hash mismatch: {path}")
    for path in [
        proxy.IC_DAILY,
        FULL_STATES_PATH,
        LEGACY_STATES_PATH,
        VALUATION_PATH,
        PRICE_PATH,
        TRI_PATH,
        GOV10Y_PATH,
    ]:
        if not path.exists():
            raise FileNotFoundError(path)
    return manifest


def load_inputs() -> dict[str, pd.DataFrame]:
    frames = proxy.load_inputs()
    full = pd.read_csv(FULL_STATES_PATH, parse_dates=["date"])
    legacy = pd.read_csv(LEGACY_STATES_PATH, parse_dates=["date"])
    full = full[full["product"].eq("IC")].sort_values("date").reset_index(drop=True)
    legacy = legacy[legacy["product"].eq("IC")].sort_values("date").reset_index(drop=True)
    if pd.Timestamp(full["date"].min()) != pd.Timestamp("2007-01-31"):
        raise RuntimeError("Full-cycle four-factor state does not start at 2007-01-31")
    if pd.Timestamp(legacy["date"].min()) != pd.Timestamp("2008-01-31"):
        raise RuntimeError("Legacy four-factor state does not start at 2008-01-31")
    if pd.Timestamp(full["date"].max()) != END or pd.Timestamp(legacy["date"].max()) != END:
        raise RuntimeError("Valuation state end-date mismatch")
    frames["states_full"] = full
    frames["states_legacy"] = legacy
    return frames


def build_daily_valuation_full(
    full_states: pd.DataFrame, legacy_states: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, object]]:
    valuation = pd.read_csv(VALUATION_PATH, parse_dates=["date"])
    price = pd.read_csv(PRICE_PATH, parse_dates=["date"]).rename(columns={"close": "price_close"})
    tri = pd.read_csv(TRI_PATH, parse_dates=["date"])[["date", "close"]].rename(
        columns={"close": "tri_close"}
    )
    gov = pd.read_csv(GOV10Y_PATH, parse_dates=["date"]).rename(columns={"date": "gov10y_date"})
    daily = valuation.merge(
        price[["date", "price_close", "official_rolling_pe"]], on="date", validate="one_to_one"
    ).merge(tri, on="date", validate="one_to_one")
    daily = daily[daily["date"] >= pd.Timestamp("2007-01-15")].sort_values("date").reset_index(drop=True)
    daily = pd.merge_asof(
        daily,
        gov.sort_values("gov10y_date"),
        left_on="date",
        right_on="gov10y_date",
        direction="backward",
        allow_exact_matches=True,
    )
    daily["gov10y_staleness_days"] = (daily["date"] - daily["gov10y_date"]).dt.days

    targets = daily[["date"]].copy()
    targets["prior_target_date"] = targets["date"] - pd.DateOffset(years=1)
    official = price[["date", "price_close"]].merge(tri, on="date", validate="one_to_one").rename(
        columns={
            "date": "prior_observation_date",
            "price_close": "prior_price_close",
            "tri_close": "prior_tri_close",
        }
    )
    prior = pd.merge_asof(
        targets.sort_values("prior_target_date"),
        official.sort_values("prior_observation_date"),
        left_on="prior_target_date",
        right_on="prior_observation_date",
        direction="backward",
        allow_exact_matches=True,
    )
    daily = daily.merge(
        prior[
            [
                "date",
                "prior_target_date",
                "prior_observation_date",
                "prior_price_close",
                "prior_tri_close",
            ]
        ],
        on="date",
        validate="one_to_one",
    )
    daily["trailing_dividend_contribution"] = (
        (daily["tri_close"] / daily["prior_tri_close"])
        / (daily["price_close"] / daily["prior_price_close"])
        - 1.0
    )
    daily["erp"] = 1.0 / daily["pe_aggregate_ttm"] - daily["gov10y_yield"]
    daily = daily.dropna(subset=[*FEATURES, "tri_close"]).reset_index(drop=True)
    if pd.Timestamp(daily["date"].min()) != pd.Timestamp("2007-01-15"):
        raise RuntimeError("Daily full-cycle valuation did not reconstruct from publication date")
    if pd.Timestamp(daily["date"].max()) != END:
        raise RuntimeError("Daily valuation end-date mismatch")
    if (daily["gov10y_date"] > daily["date"]).any():
        raise RuntimeError("Future government yield used")

    crosschecks: dict[str, object] = {
        "daily_start": daily["date"].min().date().isoformat(),
        "daily_end": daily["date"].max().date().isoformat(),
        "max_gov10y_staleness_days": int(daily["gov10y_staleness_days"].max()),
        "state_differences": {},
    }
    for name, states in [("full", full_states), ("legacy", legacy_states)]:
        matched = daily[daily["date"].isin(states["date"])].merge(
            states[["date", *FEATURES, "tri_close"]],
            on="date",
            suffixes=("_daily", "_frozen"),
            validate="one_to_one",
        )
        differences = {
            feature: float((matched[f"{feature}_daily"] - matched[f"{feature}_frozen"]).abs().max())
            for feature in [*FEATURES, "tri_close"]
        }
        if max(differences.values()) > 1e-12:
            raise RuntimeError(f"Daily/monthly valuation parity failed for {name}: {differences}")
        crosschecks["state_differences"][name] = differences
    return daily, crosschecks


def risk_ecdf(value: float, calibration: pd.Series, high_value_is_risk: bool) -> float:
    values = np.sort(calibration.dropna().astype(float).to_numpy())
    if not len(values):
        raise RuntimeError("Empty frozen ECDF calibration")
    percentile = float(np.searchsorted(values, float(value), side="right") / len(values))
    return percentile if high_value_is_risk else 1.0 - percentile


def frozen_ecdf_signal(row: pd.Series, calibration: pd.DataFrame) -> dict[str, float]:
    risks = {
        "pe_risk": risk_ecdf(row["pe_aggregate_ttm"], calibration["pe_aggregate_ttm"], True),
        "pb_risk": risk_ecdf(row["pb_aggregate"], calibration["pb_aggregate"], True),
        "erp_risk": risk_ecdf(row["erp"], calibration["erp"], False),
        "dividend_risk": risk_ecdf(
            row["trailing_dividend_contribution"], calibration["trailing_dividend_contribution"], False
        ),
    }
    score = float(np.mean(list(risks.values())))
    target = 1.0 if score >= 0.80 else (0.5 if score >= 0.65 else 0.0)
    return {**risks, "risk_score": score, "target_fraction": target}


def absolute_four_factor_signal(row: pd.Series) -> dict[str, float]:
    pe = float(row["pe_aggregate_ttm"])
    pb = float(row["pb_aggregate"])
    erp = float(row["erp"])
    dividend = float(row["trailing_dividend_contribution"])
    risks = {
        "pe_risk": 1.0 if pe >= 35.0 else (0.5 if pe >= 25.0 else 0.0),
        "pb_risk": 1.0 if pb >= 3.0 else (0.5 if pb >= 2.0 else 0.0),
        "erp_risk": 1.0 if erp < 0.0 else (0.5 if erp <= 0.01 else 0.0),
        "dividend_risk": 1.0 if dividend < 0.005 else (0.5 if dividend <= 0.01 else 0.0),
    }
    score = float(np.mean(list(risks.values())))
    target = 1.0 if score >= 0.75 else (0.5 if score >= 0.50 else 0.0)
    return {**risks, "risk_score": score, "target_fraction": target}


def dynamic_forecast(
    day: pd.Timestamp,
    daily: pd.DataFrame,
    states: pd.DataFrame,
    tri: pd.DataFrame,
    decision_id: str,
) -> tuple[dict[str, object], pd.DataFrame]:
    history = states[states["date"] <= day].copy()
    if history.empty or pd.Timestamp(history["date"].max()) != day:
        current = daily[daily["date"].eq(day)]
        if len(current) != 1:
            raise RuntimeError(f"Missing daily valuation for {day.date()}")
        source = current.iloc[0]
        new_row = {column: np.nan for column in states.columns}
        for feature in [*FEATURES, "tri_close"]:
            new_row[feature] = float(source[feature])
        new_row.update({"date": day, "product": "IC", "index_name": "中证500"})
        history = pd.concat([history, pd.DataFrame([new_row])], ignore_index=True)
    summary, analogues = walk_forward_forecast(history, tri, day, decision_id)
    enough = bool(summary["enough_analogues"])
    forecast = float(summary["forecast_3y_median"]) if enough else np.nan
    target = 0.0 if not enough else (1.0 if forecast < 0.0 else (0.5 if forecast < 0.03 else 0.0))
    summary["target_fraction"] = target
    return summary, analogues


def build_signal_panel(
    ic: pd.DataFrame,
    daily: pd.DataFrame,
    full_states: pd.DataFrame,
    tri: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.DatetimeIndex(ic["date"])
    layer_evals = {
        "model": proxy.evaluation_dates("daily", MODEL_START, END, dates, daily),
        "real": proxy.evaluation_dates("daily", REAL_START, END, dates, daily),
    }
    unique_days = sorted(set(layer_evals["model"]) | set(layer_evals["real"]))
    calibration = full_states[full_states["date"] <= pd.Timestamp("2014-12-31")].copy()
    if len(calibration) != 96 or pd.Timestamp(calibration["date"].max()) > pd.Timestamp("2014-12-31"):
        raise RuntimeError("Frozen ECDF calibration is not the preregistered 96 months")
    daily_lookup = daily.set_index("date")

    signal_rows: list[dict[str, object]] = []
    analogue_parts: list[pd.DataFrame] = []
    dynamic_targets: dict[int, dict[pd.Timestamp, float]] = {year: {} for year in START_SENSITIVITY_YEARS}
    dynamic_summaries: dict[int, dict[pd.Timestamp, dict[str, object]]] = {
        year: {} for year in START_SENSITIVITY_YEARS
    }

    for year in START_SENSITIVITY_YEARS:
        states = full_states[full_states["date"] >= pd.Timestamp(year=year, month=1, day=1)].copy()
        for sequence, day in enumerate(unique_days):
            decision_id = f"ic_v2_start{year}_{sequence:04d}_{day.date()}"
            summary, analogues = dynamic_forecast(day, daily, states, tri, decision_id)
            target = float(summary["target_fraction"])
            dynamic_targets[year][day] = target
            dynamic_summaries[year][day] = summary
            signal_rows.append(
                {
                    "signal_variant": f"dynamic_start_{year}",
                    "eval_date": day,
                    **{key: value for key, value in summary.items() if key != "state_date"},
                }
            )
            if not analogues.empty:
                copy = analogues.copy()
                copy["signal_variant"] = f"dynamic_start_{year}"
                analogue_parts.append(copy)

    fixed_targets: dict[str, dict[pd.Timestamp, float]] = {
        "frozen_2007_2014_ecdf": {},
        "absolute_four_factor_gate": {},
        "always_50": {day: 0.5 for day in unique_days},
        "always_100": {day: 1.0 for day in unique_days},
    }
    for day in unique_days:
        row = daily_lookup.loc[day]
        for variant, function in [
            ("frozen_2007_2014_ecdf", lambda value: frozen_ecdf_signal(value, calibration)),
            ("absolute_four_factor_gate", absolute_four_factor_signal),
        ]:
            values = function(row)
            fixed_targets[variant][day] = float(values["target_fraction"])
            signal_rows.append(
                {
                    "signal_variant": variant,
                    "eval_date": day,
                    **{feature: float(row[feature]) for feature in FEATURES},
                    **values,
                }
            )
    for variant in ["always_50", "always_100"]:
        for day in unique_days:
            signal_rows.append(
                {"signal_variant": variant, "eval_date": day, "target_fraction": fixed_targets[variant][day]}
            )

    candidate_targets: dict[str, dict[pd.Timestamp, float]] = {
        "legacy_2008_dynamic": dynamic_targets[2008],
        "full_2007_dynamic": dynamic_targets[2007],
        **fixed_targets,
    }
    schedule_rows: list[dict[str, object]] = []
    for layer, evals in layer_evals.items():
        start = MODEL_START if layer == "model" else REAL_START
        for variant in VARIANTS:
            for sequence, day in enumerate(evals):
                execution, initial = proxy.next_execution(day, start, dates)
                target = candidate_targets[variant][day]
                source_summary: dict[str, object] = {}
                if variant in {"legacy_2008_dynamic", "full_2007_dynamic"}:
                    year = 2008 if variant == "legacy_2008_dynamic" else 2007
                    summary = dynamic_summaries[year][day]
                    source_summary = {
                        "forecast_3y_median": summary["forecast_3y_median"],
                        "analogue_count": summary["analogue_count"],
                        "enough_analogues": summary["enough_analogues"],
                    }
                schedule_rows.append(
                    {
                        "layer": layer,
                        "frequency": "daily",
                        "signal_variant": variant,
                        "sequence": sequence,
                        "eval_date": day,
                        "execution_date": execution,
                        "initial_exception": initial,
                        "binary_target_fraction": target,
                        "three_tier_target_fraction": target,
                        **source_summary,
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    ).reset_index(drop=True)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate execution in candidate schedule")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Signal execution leakage")

    signals = pd.DataFrame(signal_rows).sort_values(["signal_variant", "eval_date"]).reset_index(drop=True)
    analogues = pd.concat(analogue_parts, ignore_index=True) if analogue_parts else pd.DataFrame()
    if not analogues.empty and (analogues["forward_end_date"] > analogues["as_of"]).any():
        raise RuntimeError("Dynamic analogue outcome leakage")
    sensitivity, pairwise = start_sensitivity_outputs(signals)
    return schedule, signals, analogues, sensitivity.merge(pairwise, how="cross") if False else pairwise


def start_sensitivity_outputs(signals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    subset = signals[signals["signal_variant"].str.startswith("dynamic_start_")].copy()
    subset["start_year"] = subset["signal_variant"].str.rsplit("_", n=1).str[-1].astype(int)
    pivot = subset.pivot(index="eval_date", columns="start_year", values="target_fraction").sort_index()
    rows: list[dict[str, object]] = []
    for year in START_SENSITIVITY_YEARS:
        values = pivot[year]
        protected = values[values > 0]
        rows.append(
            {
                "start_year": year,
                "eval_count": int(len(values)),
                "average_target_fraction": float(values.mean()),
                "protected_day_ratio": float(values.gt(0).mean()),
                "switch_count": int(values.ne(values.shift()).sum() - 1),
                "first_protected_date": protected.index.min() if len(protected) else pd.NaT,
                "agreement_with_2007": float(values.eq(pivot[2007]).mean()),
            }
        )
    pair_rows: list[dict[str, object]] = []
    for left_index, left in enumerate(START_SENSITIVITY_YEARS):
        for right in START_SENSITIVITY_YEARS[left_index + 1 :]:
            pair_rows.append(
                {
                    "left_start_year": left,
                    "right_start_year": right,
                    "agreement": float(pivot[left].eq(pivot[right]).mean()),
                    "mean_abs_target_difference": float((pivot[left] - pivot[right]).abs().mean()),
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(pair_rows)


def candidate_parts(candidate: str) -> dict[str, str]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "signal_variant": variant}


def metrics(returns: pd.Series) -> dict[str, float]:
    return proxy.metrics(returns)


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    formal_rows: list[dict[str, object]] = []
    scan_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        parts = candidate_parts(candidate)
        wide: dict[str, object] = {"candidate": candidate, **parts}
        for window, offset in WINDOWS.items():
            requested = group["date"].min() if offset is None else END - offset
            available = offset is None or group["date"].min() <= requested
            subset = group if offset is None else group[group["date"] >= requested]
            row: dict[str, object] = {
                "candidate": candidate,
                **parts,
                "window": window,
                "available": available,
                "requested_start": requested,
                "actual_start": subset["date"].min(),
                "end": subset["date"].max(),
                "rows": len(subset),
            }
            if available:
                base_metrics = metrics(subset["ret"])
                cash_metrics = metrics(subset["cash_ret"])
                row.update(base_metrics)
                row.update({f"cash_{key}": value for key, value in cash_metrics.items()})
                scan_rows.append({**row, "segment": window})
                wide[f"ann_return_{window}"] = base_metrics["ann_return"]
                wide[f"max_dd_{window}"] = base_metrics["max_dd"]
                wide[f"cash_ann_return_{window}"] = cash_metrics["ann_return"]
                wide[f"cash_max_dd_{window}"] = cash_metrics["max_dd"]
            formal_rows.append(row)
        wide_rows.append(wide)
    return pd.DataFrame(formal_rows), pd.DataFrame(scan_rows), pd.DataFrame(wide_rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (candidate, year), group in daily.groupby(["candidate", daily["date"].dt.year], sort=False):
        group = group.sort_values("date")
        base = metrics(group["ret"])
        cash = metrics(group["cash_ret"])
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "year": int(year),
                "rows": len(group),
                **base,
                **{f"cash_{key}": value for key, value in cash.items()},
            }
        )
    return pd.DataFrame(rows)


def exposure_summary(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        subset = trades[trades["candidate"].eq(candidate)] if not trades.empty else pd.DataFrame()
        entries = (
            subset[subset["new_entry_moneyness"].notna()]
            if not subset.empty and "new_entry_moneyness" in subset
            else pd.DataFrame()
        )
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "protected_days": int(group["target_fraction"].gt(0).sum()),
                "protected_day_ratio": float(group["target_fraction"].gt(0).mean()),
                "average_target_fraction": float(group["target_fraction"].mean()),
                "average_put_mark_fraction": float(group["put_mark_fraction"].mean()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "put_cost_sum": float(group["put_cost_rate"].sum()),
                "trade_events": int(len(subset)),
                "deferred_days": int(group["deferred_adjustment"].sum()),
                "carried_mark_days": int(group["carried_mark"].sum()),
                "max_mark_stale_days": int(group["mark_stale_days"].max()),
                "average_entry_moneyness": (
                    float(entries["new_entry_moneyness"].mean()) if not entries.empty else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def real_model_validation(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[dict[str, object]] = []
    real_baseline = daily[daily["candidate"].eq("real_no_put")].sort_values("date")
    model_baseline = daily[
        daily["candidate"].eq("model_no_put") & (daily["date"] >= REAL_START)
    ].sort_values("date")
    real_base_metrics = metrics(real_baseline["cash_ret"])
    model_base_metrics = metrics(model_baseline["cash_ret"])
    for variant in ALL_VARIANTS:
        real = daily[daily["candidate"].eq(f"real_{variant}")].sort_values("date")
        model = daily[
            daily["candidate"].eq(f"model_{variant}") & (daily["date"] >= REAL_START)
        ].sort_values("date")
        if not real["date"].reset_index(drop=True).equals(model["date"].reset_index(drop=True)):
            raise RuntimeError(f"Real/model calendar mismatch for {variant}")
        real_metrics = metrics(real["cash_ret"])
        model_metrics = metrics(model["cash_ret"])
        rows.append(
            {
                "signal_variant": variant,
                "real_cash_ann_return": real_metrics["ann_return"],
                "real_cash_max_dd": real_metrics["max_dd"],
                "model_cash_ann_return": model_metrics["ann_return"],
                "model_cash_max_dd": model_metrics["max_dd"],
                "real_cagr_delta_vs_no_put": real_metrics["ann_return"] - real_base_metrics["ann_return"],
                "model_cagr_delta_vs_no_put": model_metrics["ann_return"] - model_base_metrics["ann_return"],
                "real_dd_improvement_vs_no_put": real_metrics["max_dd"] - real_base_metrics["max_dd"],
                "model_dd_improvement_vs_no_put": model_metrics["max_dd"] - model_base_metrics["max_dd"],
            }
        )
    table = pd.DataFrame(rows)
    active = table[table["signal_variant"].ne("no_put")]
    spearman = float(
        active["real_cagr_delta_vs_no_put"].rank().corr(active["model_cagr_delta_vs_no_put"].rank())
    )
    same_direction = float(
        (
            active["real_cagr_delta_vs_no_put"] * active["model_cagr_delta_vs_no_put"] >= 0
        ).mean()
    )
    return table, {"spearman_cagr_delta": spearman, "same_direction_ratio": same_direction}


def event_concentration(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        baseline = daily[daily["candidate"].eq(f"{layer}_no_put")][["date", "cash_ret"]].rename(
            columns={"cash_ret": "baseline_ret"}
        )
        for variant in VARIANTS:
            candidate = f"{layer}_{variant}"
            group = daily[daily["candidate"].eq(candidate)][["date", "cash_ret"]].rename(
                columns={"cash_ret": "candidate_ret"}
            )
            merged = group.merge(baseline, on="date", validate="one_to_one")
            merged["relative_log"] = np.log1p(merged["candidate_ret"]) - np.log1p(
                merged["baseline_ret"]
            )
            total = float(merged["relative_log"].sum())
            top = merged.nlargest(5, "relative_log")
            rows.append(
                {
                    "candidate": candidate,
                    "relative_log_total": total,
                    "top5_positive_sum": float(top["relative_log"].clip(lower=0).sum()),
                    "top5_share_of_net": (
                        float(top["relative_log"].clip(lower=0).sum() / total) if total > 0 else np.nan
                    ),
                    "best_day": top.iloc[0]["date"] if len(top) else pd.NaT,
                    "best_day_relative_log": float(top.iloc[0]["relative_log"]) if len(top) else np.nan,
                }
            )
    return pd.DataFrame(rows)


def build_record(
    metrics_table: pd.DataFrame,
    exposure: pd.DataFrame,
    sensitivity: pd.DataFrame,
    pairwise: pd.DataFrame,
    cross_stats: dict[str, object],
    qvix_stats: dict[str, object],
) -> str:
    model = metrics_table[
        metrics_table["candidate"].str.startswith("model_")
        & metrics_table["window"].isin(["full", "last_10y", "last_5y"])
        & metrics_table["available"].eq(True)
    ][["candidate", "window", "cash_ann_return", "cash_max_dd"]]
    exposure_model = exposure[exposure["layer"].eq("model")][
        ["candidate", "protected_days", "average_target_fraction", "put_cost_sum", "trade_events"]
    ]
    lines = [
        "# IC + 510500 Put 全周期估值保护 v2",
        "",
        "> 研究回测；未获准实盘；不得解释为交易建议。",
        "",
        "## 资本口径核心结果",
        "",
        model.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 模型层保护暴露",
        "",
        exposure_model.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 动态起点敏感性",
        "",
        sensitivity.to_markdown(index=False, floatfmt=".4f"),
        "",
        f"- 2007—2011 起点平均两两一致率：{pairwise['agreement'].mean():.2%}。",
        f"- 实际/模型同区间 CAGR 差值 Spearman：{cross_stats['spearman_cagr_delta']:.3f}。",
        f"- 实际/模型方向一致率：{cross_stats['same_direction_ratio']:.2%}。",
        f"- QVIX 模型代理校验：{'通过' if qvix_stats['passed'] else '未通过'}。",
        "",
        "## 口径限制",
        "",
        "- 2007 年状态使用指数发布前的官方回溯价格/全收益历史，只能解释为回填研究历史。",
        "- 2015—2022 为模型 Put；实际 510500 Put 仅从 2022-09-19 起，不能直接验证 2015 年保护成交。",
        "- 实际期权为第三方日线开收盘与成交量筛选，不是交易所结算价或可执行买卖盘。",
        "- 所有阈值已在收益计算前冻结；是否保留由运行后审计决定。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    proxy_manifest = verify_inputs()
    frames = load_inputs()
    daily_valuation, valuation_checks = build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, analogues, pairwise = build_signal_panel(
        frames["ic"], daily_valuation, frames["states_full"], frames["tri"]
    )
    sensitivity, pairwise_recomputed = start_sensitivity_outputs(signals)
    if not pairwise.equals(pairwise_recomputed):
        raise RuntimeError("Start-sensitivity output is not deterministic")

    market, market_checks = proxy.prepare_model_market(
        frames["ic"], daily_valuation, frames["q50"], frames["etf50"], frames["index_sina"]
    )
    qvix_table, qvix_stats = proxy.qvix_validation(market, frames["q500"])

    daily_parts: list[pd.DataFrame] = [
        proxy.no_put_rows(frames["ic"], MODEL_START, "model_no_put"),
        proxy.no_put_rows(frames["ic"], REAL_START, "real_no_put"),
    ]
    trade_parts: list[pd.DataFrame] = []
    for variant in VARIANTS:
        model_schedule = schedule[
            schedule["layer"].eq("model") & schedule["signal_variant"].eq(variant)
        ]
        overlay, trades = proxy.run_model_candidate(
            frames["ic"], model_schedule, market, "daily", "front", "three_tier", 0.85,
            f"model_{variant}",
        )
        daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
        if not trades.empty:
            trade_parts.append(trades)

        real_schedule = schedule[
            schedule["layer"].eq("real") & schedule["signal_variant"].eq(variant)
        ]
        overlay, trades = proxy.run_real_candidate(
            frames["ic"], real_schedule, frames["snapshots"], frames["histories"], frames["etf500"],
            "daily", "front", "three_tier", f"real_{variant}",
        )
        daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
        if not trades.empty:
            trade_parts.append(trades)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"]).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False) if trade_parts else pd.DataFrame()
    formal, scan_summary, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = exposure_summary(daily, trades)
    cross_table, cross_stats = real_model_validation(daily)
    concentration = event_concentration(daily)

    expected = {f"{layer}_{variant}" for layer in ["model", "real"] for variant in ALL_VARIANTS}
    if set(daily["candidate"].unique()) != expected:
        raise RuntimeError("Candidate set mismatch")
    parity: dict[str, float] = {}
    for label, start in [("model_no_put", MODEL_START), ("real_no_put", REAL_START)]:
        observed = daily[daily["candidate"].eq(label)][["date", "ret"]]
        frozen = frames["ic"][frames["ic"]["date"] >= start][["date", "ic_net_ret"]]
        joined = observed.merge(frozen, on="date", validate="one_to_one")
        parity[label] = float((joined["ret"] - joined["ic_net_ret"]).abs().max())
    if max(parity.values()) > 1e-14:
        raise RuntimeError(f"Frozen IC baseline parity failed: {parity}")
    if daily[["ret", "cash_ret"]].isna().any().any() or (daily[["ret", "cash_ret"]] <= -1).any().any():
        raise RuntimeError("Invalid candidate return")

    permanent = exposure[exposure["signal_variant"].isin(["always_50", "always_100"])]
    if (permanent["trade_events"] <= 0).any() or (permanent["average_put_mark_fraction"] <= 0).any():
        raise RuntimeError("Permanent Put benchmark did not create a position")
    for layer in ["model", "real"]:
        for variant, expected_target in [("always_50", 0.5), ("always_100", 1.0)]:
            value = exposure.loc[
                exposure["candidate"].eq(f"{layer}_{variant}"), "average_target_fraction"
            ].item()
            if not math.isclose(value, expected_target, abs_tol=1e-12):
                raise RuntimeError(f"Permanent target mismatch for {layer}_{variant}: {value}")

    conditional_identification = {
        row.candidate: bool(row.protected_days >= 20)
        for row in exposure[
            exposure["signal_variant"].isin(
                [
                    "legacy_2008_dynamic",
                    "full_2007_dynamic",
                    "frozen_2007_2014_ecdf",
                    "absolute_four_factor_gate",
                ]
            )
        ].itertuples(index=False)
    }

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv", index=False)
    signals.to_csv(OUTPUT / "valuation_signals.csv.gz", index=False, compression="gzip")
    analogues.to_csv(OUTPUT / "signal_analogues.csv.gz", index=False, compression="gzip")
    sensitivity.to_csv(OUTPUT / "dynamic_start_sensitivity.csv", index=False)
    pairwise.to_csv(OUTPUT / "dynamic_start_pairwise.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    record = build_record(formal, exposure, sensitivity, pairwise, cross_stats, qvix_stats)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")

    source_paths = [
        proxy.IC_DAILY,
        FULL_STATES_PATH,
        LEGACY_STATES_PATH,
        VALUATION_PATH,
        PRICE_PATH,
        TRI_PATH,
        GOV10Y_PATH,
        PROXY_DATA_MANIFEST,
    ]
    output_manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": VERSION,
        "research_status": "research_only_not_live_approved",
        "history_label": "official_backcast_research_history",
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "candidate_count": len(expected),
        "candidate_grid": sorted(expected),
        "sample": {
            "model": [str(MODEL_START.date()), str(END.date())],
            "real": [str(REAL_START.date()), str(END.date())],
            "four_factor_full_start": "2007-01-31",
            "legacy_start": "2008-01-31",
        },
        "valuation_checks": valuation_checks,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "baseline_parity": parity,
        "conditional_identification_pass": conditional_identification,
        "dynamic_start_average_pairwise_agreement": float(pairwise["agreement"].mean()),
        "source_hashes": {str(path.relative_to(ROOT)): sha256(path) for path in source_paths},
        "proxy_collector_manifest_sha256": sha256(PROXY_DATA_MANIFEST),
        "proxy_file_count": len(proxy_manifest["files"]),
        "git_status": git_status(),
        "warnings": [
            "2007 state uses official pre-publication backcast price/TRI history.",
            "Model Put is theoretical and not historical executable pricing.",
            "Real 510500 option prices are third-party daily opens/closes, not executable bid/ask or official settlement.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(
        "python.exe -m pytest test_ic_510500_put_full_cycle_valuation_v2.py -q\n"
        "python.exe ic_510500_put_full_cycle_valuation_v2.py\n",
        encoding="utf-8",
    )

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython.exe -m pytest test_ic_510500_put_full_cycle_valuation_v2.py -q\n")
        handle.write("python.exe ic_510500_put_full_cycle_valuation_v2.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "source_hashes": output_manifest["source_hashes"],
            "baseline_parity": parity,
            "qvix_proxy": qvix_stats,
            "real_model_cross_validation": cross_stats,
            "conditional_identification_pass": conditional_identification,
            "dynamic_start_average_pairwise_agreement": float(pairwise["agreement"].mean()),
            "formal_output": str(OUTPUT.relative_to(ROOT)),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "baseline_parity": parity,
                "qvix_passed": qvix_stats["passed"],
                "dynamic_start_average_pairwise_agreement": float(pairwise["agreement"].mean()),
                "conditional_identification_pass": conditional_identification,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
