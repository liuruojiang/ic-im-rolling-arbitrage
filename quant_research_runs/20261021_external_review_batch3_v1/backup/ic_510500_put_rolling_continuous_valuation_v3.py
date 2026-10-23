from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_full_cycle_valuation_v2 as v2
import ic_510500_put_proxy_validation_v1 as proxy


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_rolling_continuous_valuation_v3"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "d81abf6548512ac2228b49572ca80f878633a5c57624fd04d75cb2adc5c5571d"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_rolling_continuous_valuation_v3"

MODEL_START = proxy.MODEL_START
REAL_START = proxy.REAL_START
END = proxy.END
DEVELOPMENT_END = pd.Timestamp("2020-12-31")
HOLDOUT_START = pd.Timestamp("2021-01-04")
TRADING_DAYS = proxy.TRADING_DAYS
MIN_HISTORY_MONTHS = 96

ECON_WINDOWS = [8, 10, 12]
MAPPINGS = [(0.40, 0.80), (0.50, 0.90), (0.60, 0.90)]
ECON_VARIANTS = [
    f"econ_w{window:02d}_l{int(low*100):02d}_h{int(high*100):02d}"
    for window in ECON_WINDOWS
    for low, high in MAPPINGS
]
STRUCTURAL_VARIANT = "equal4_w10_l50_h90"
VARIANTS = ["always_50", "always_100", *ECON_VARIANTS, STRUCTURAL_VARIANT]
ALL_VARIANTS = ["no_put", *VARIANTS]
REQUIRED_WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}
EXTRA_WINDOWS = ["development", "holdout"]


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


def variant_parameters(variant: str) -> dict[str, object]:
    if variant in {"no_put", "always_50", "always_100"}:
        return {
            "score_type": "baseline",
            "window_years": np.nan,
            "lower_risk": np.nan,
            "full_risk": np.nan,
        }
    if variant == STRUCTURAL_VARIANT:
        return {
            "score_type": "equal4",
            "window_years": 10,
            "lower_risk": 0.50,
            "full_risk": 0.90,
        }
    match = re.fullmatch(r"econ_w(\d{2})_l(\d{2})_h(\d{2})", variant)
    if match is None:
        raise ValueError(f"Unknown variant: {variant}")
    return {
        "score_type": "economic",
        "window_years": int(match.group(1)),
        "lower_risk": int(match.group(2)) / 100.0,
        "full_risk": int(match.group(3)) / 100.0,
    }


def candidate_parts(candidate: str) -> dict[str, object]:
    layer, variant = candidate.split("_", 1)
    return {"layer": layer, "signal_variant": variant, **variant_parameters(variant)}


def verify_inputs() -> dict[str, object]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v3 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v3 specification sidecar mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Preregistered scan folder missing: {SCAN}")

    v2_manifest_path = v2.OUTPUT / "data_manifest.json"
    v2_manifest = json.loads(v2_manifest_path.read_text(encoding="utf-8"))
    if sha256(Path(v2.__file__)) != v2_manifest["script_sha256"]:
        raise RuntimeError("Imported v2 implementation changed after its formal run")
    if sha256(v2.SPEC) != v2_manifest["spec_sha256"]:
        raise RuntimeError("Imported v2 specification changed")

    proxy_manifest = json.loads(v2.PROXY_DATA_MANIFEST.read_text(encoding="utf-8"))
    if proxy_manifest.get("spec_sha256") != proxy.SPEC_HASH:
        raise RuntimeError("Frozen proxy data is not tied to proxy v1 spec")
    for filename, item in proxy_manifest["files"].items():
        path = proxy.DATA / filename
        if not path.exists() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Frozen proxy data hash mismatch: {path}")
    return {"v2_manifest": v2_manifest, "proxy_manifest": proxy_manifest}


def round_target(raw_target: float) -> float:
    clipped = min(max(float(raw_target), 0.0), 1.0)
    return math.floor(clipped * 10.0 + 0.5) / 10.0


def map_score(score: float, lower: float, full: float) -> tuple[float, float]:
    if not 0 <= lower < full <= 1:
        raise ValueError("Invalid continuous mapping")
    raw = min(max((float(score) - lower) / (full - lower), 0.0), 1.0)
    return raw, round_target(raw)


def risk_score_on_day(
    day: pd.Timestamp,
    window_years: int,
    daily_row: pd.Series,
    states: pd.DataFrame,
) -> dict[str, object]:
    lower_date = day - pd.DateOffset(years=window_years)
    history = states[states["date"].between(lower_date, day, inclusive="both")]
    if len(history) < MIN_HISTORY_MONTHS:
        raise RuntimeError(
            f"Insufficient causal valuation history on {day.date()} for {window_years}y: {len(history)}"
        )
    risks = {
        "pe_risk": v2.risk_ecdf(
            daily_row["pe_aggregate_ttm"], history["pe_aggregate_ttm"], True
        ),
        "pb_risk": v2.risk_ecdf(daily_row["pb_aggregate"], history["pb_aggregate"], True),
        "erp_risk": v2.risk_ecdf(daily_row["erp"], history["erp"], False),
        "dividend_risk": v2.risk_ecdf(
            daily_row["trailing_dividend_contribution"],
            history["trailing_dividend_contribution"],
            False,
        ),
    }
    economic = 0.25 * risks["pb_risk"] + 0.50 * risks["erp_risk"] + 0.25 * risks["dividend_risk"]
    equal4 = float(np.mean(list(risks.values())))
    return {
        **risks,
        "economic_risk": float(economic),
        "equal4_risk": equal4,
        "history_months": int(len(history)),
        "history_start": pd.Timestamp(history["date"].min()),
        "history_end": pd.Timestamp(history["date"].max()),
    }


def build_signal_panel(
    ic: pd.DataFrame,
    daily_valuation: pd.DataFrame,
    states: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trade_dates = pd.DatetimeIndex(ic["date"])
    layer_evals = {
        "model": proxy.evaluation_dates("daily", MODEL_START, END, trade_dates, daily_valuation),
        "real": proxy.evaluation_dates("daily", REAL_START, END, trade_dates, daily_valuation),
    }
    unique_days = sorted(set(layer_evals["model"]) | set(layer_evals["real"]))
    daily_lookup = daily_valuation.set_index("date")

    score_cache: dict[tuple[pd.Timestamp, int], dict[str, object]] = {}
    signal_rows: list[dict[str, object]] = []
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {variant: {} for variant in VARIANTS}
    for day in unique_days:
        row = daily_lookup.loc[day]
        for window in ECON_WINDOWS:
            score_cache[(day, window)] = risk_score_on_day(day, window, row, states)
        for variant in ECON_VARIANTS:
            params = variant_parameters(variant)
            window = int(params["window_years"])
            values = score_cache[(day, window)]
            score = float(values["economic_risk"])
            raw, target = map_score(score, float(params["lower_risk"]), float(params["full_risk"]))
            target_lookup[variant][day] = target
            signal_rows.append(
                {
                    "signal_variant": variant,
                    "eval_date": day,
                    **params,
                    **{feature: float(row[feature]) for feature in v2.FEATURES},
                    **values,
                    "risk_score": score,
                    "raw_target_fraction": raw,
                    "target_fraction": target,
                }
            )

        values = score_cache[(day, 10)]
        score = float(values["equal4_risk"])
        raw, target = map_score(score, 0.50, 0.90)
        target_lookup[STRUCTURAL_VARIANT][day] = target
        signal_rows.append(
            {
                "signal_variant": STRUCTURAL_VARIANT,
                "eval_date": day,
                **variant_parameters(STRUCTURAL_VARIANT),
                **{feature: float(row[feature]) for feature in v2.FEATURES},
                **values,
                "risk_score": score,
                "raw_target_fraction": raw,
                "target_fraction": target,
            }
        )
        target_lookup["always_50"][day] = 0.5
        target_lookup["always_100"][day] = 1.0

    for variant, target in [("always_50", 0.5), ("always_100", 1.0)]:
        for day in unique_days:
            signal_rows.append(
                {
                    "signal_variant": variant,
                    "eval_date": day,
                    **variant_parameters(variant),
                    "target_fraction": target,
                }
            )

    schedule_rows: list[dict[str, object]] = []
    for layer, evaluations in layer_evals.items():
        start = MODEL_START if layer == "model" else REAL_START
        for variant in VARIANTS:
            for sequence, day in enumerate(evaluations):
                execution, initial = proxy.next_execution(day, start, trade_dates)
                target = target_lookup[variant][day]
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
                    }
                )
    schedule = pd.DataFrame(schedule_rows).sort_values(
        ["layer", "signal_variant", "execution_date"]
    ).reset_index(drop=True)
    if schedule.duplicated(["layer", "signal_variant", "execution_date"]).any():
        raise RuntimeError("Duplicate execution schedule")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("Signal execution leakage")

    signals = pd.DataFrame(signal_rows).sort_values(["signal_variant", "eval_date"]).reset_index(drop=True)
    window_stability = build_window_stability(signals)
    mapping_stability = build_mapping_stability(signals)
    current = build_current_signals(daily_lookup.loc[END], states)
    return schedule, signals, window_stability, mapping_stability, current


def active_comparison(left: pd.Series, right: pd.Series) -> dict[str, float]:
    union = left.gt(0) | right.gt(0)
    intersection = left.gt(0) & right.gt(0)
    union_count = int(union.sum())
    return {
        "all_day_exact_agreement": float(left.eq(right).mean()),
        "active_union_days": union_count,
        "protected_day_jaccard": float(intersection.sum() / union_count) if union_count else 1.0,
        "active_exact_agreement": float(left[union].eq(right[union]).mean()) if union_count else 1.0,
        "active_target_mae": float((left[union] - right[union]).abs().mean()) if union_count else 0.0,
    }


def build_window_stability(signals: pd.DataFrame) -> pd.DataFrame:
    economic = signals[signals["signal_variant"].isin(ECON_VARIANTS)].copy()
    pivot = economic.pivot(index="eval_date", columns="signal_variant", values="target_fraction")
    rows: list[dict[str, object]] = []
    for lower, full in MAPPINGS:
        variants = {
            window: f"econ_w{window:02d}_l{int(lower*100):02d}_h{int(full*100):02d}"
            for window in ECON_WINDOWS
        }
        for left_window, right_window in [(8, 10), (10, 12), (8, 12)]:
            left, right = variants[left_window], variants[right_window]
            rows.append(
                {
                    "lower_risk": lower,
                    "full_risk": full,
                    "left_window_years": left_window,
                    "right_window_years": right_window,
                    "left_variant": left,
                    "right_variant": right,
                    **active_comparison(pivot[left], pivot[right]),
                }
            )
    return pd.DataFrame(rows)


def build_mapping_stability(signals: pd.DataFrame) -> pd.DataFrame:
    economic = signals[signals["signal_variant"].isin(ECON_VARIANTS)].copy()
    pivot = economic.pivot(index="eval_date", columns="signal_variant", values="target_fraction")
    rows: list[dict[str, object]] = []
    for window in ECON_WINDOWS:
        labels = [
            f"econ_w{window:02d}_l{int(lower*100):02d}_h{int(full*100):02d}"
            for lower, full in MAPPINGS
        ]
        for left, right in zip(labels[:-1], labels[1:]):
            rows.append(
                {
                    "window_years": window,
                    "left_variant": left,
                    "right_variant": right,
                    **active_comparison(pivot[left], pivot[right]),
                }
            )
    return pd.DataFrame(rows)


def build_current_signals(current_row: pd.Series, states: pd.DataFrame) -> pd.DataFrame:
    score_cache = {
        window: risk_score_on_day(END, window, current_row, states) for window in ECON_WINDOWS
    }
    rows: list[dict[str, object]] = []
    for variant in [*ECON_VARIANTS, STRUCTURAL_VARIANT]:
        params = variant_parameters(variant)
        window = int(params["window_years"])
        values = score_cache[window]
        score = float(values["equal4_risk"] if variant == STRUCTURAL_VARIANT else values["economic_risk"])
        raw, target = map_score(score, float(params["lower_risk"]), float(params["full_risk"]))
        rows.append(
            {
                "as_of": END,
                "signal_variant": variant,
                **params,
                **values,
                "risk_score": score,
                "raw_target_fraction": raw,
                "research_target_fraction": target,
                "execution_status": "research_state_only_next_open_unobserved",
            }
        )
    return pd.DataFrame(rows)


def metrics(returns: pd.Series) -> dict[str, float]:
    return proxy.metrics(returns)


def segment_slice(
    group: pd.DataFrame, segment: str
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp, bool]:
    first = pd.Timestamp(group["date"].min())
    last = pd.Timestamp(group["date"].max())
    if segment in REQUIRED_WINDOWS:
        offset = REQUIRED_WINDOWS[segment]
        requested_start = first if offset is None else END - offset
        requested_end = END
        available = offset is None or first <= requested_start
    elif segment == "development":
        requested_start, requested_end = MODEL_START, DEVELOPMENT_END
        available = first <= requested_start and last >= requested_end
    elif segment == "holdout":
        requested_start, requested_end = HOLDOUT_START, END
        available = first <= requested_start and last >= requested_end
    else:
        raise ValueError(segment)
    subset = group[group["date"].between(requested_start, requested_end, inclusive="both")]
    return subset, requested_start, requested_end, available


def metric_outputs(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    formal_rows: list[dict[str, object]] = []
    scan_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    segments = [*REQUIRED_WINDOWS, *EXTRA_WINDOWS]
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        parts = candidate_parts(candidate)
        wide: dict[str, object] = {"candidate": candidate, **parts}
        for segment in segments:
            subset, requested_start, requested_end, available = segment_slice(group, segment)
            formal: dict[str, object] = {
                "candidate": candidate,
                **parts,
                "segment": segment,
                "available": available,
                "requested_start": requested_start,
                "requested_end": requested_end,
                "actual_start": subset["date"].min() if len(subset) else pd.NaT,
                "actual_end": subset["date"].max() if len(subset) else pd.NaT,
                "rows": int(len(subset)),
            }
            if available and len(subset):
                base = metrics(subset["ret"])
                cash = metrics(subset["cash_ret"])
                formal.update(base)
                formal.update({f"cash_{key}": value for key, value in cash.items()})
            formal_rows.append(formal)

            scan_subset = subset if len(subset) else group
            base = metrics(scan_subset["ret"])
            cash = metrics(scan_subset["cash_ret"])
            scan_rows.append(
                {
                    "candidate": candidate,
                    **parts,
                    "segment": segment,
                    "start": scan_subset["date"].min(),
                    "end": scan_subset["date"].max(),
                    "rows": int(len(scan_subset)),
                    "window_available": available,
                    **base,
                    **{f"cash_{key}": value for key, value in cash.items()},
                }
            )
            if segment in REQUIRED_WINDOWS:
                for key, value in base.items():
                    wide[f"{key}_{segment}"] = value
                for key, value in cash.items():
                    wide[f"cash_{key}_{segment}"] = value
        wide_rows.append(wide)
    return pd.DataFrame(formal_rows), pd.DataFrame(scan_rows), pd.DataFrame(wide_rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (candidate, year), group in daily.groupby(["candidate", daily["date"].dt.year], sort=False):
        group = group.sort_values("date")
        base, cash = metrics(group["ret"]), metrics(group["cash_ret"])
        rows.append(
            {
                "candidate": candidate,
                **candidate_parts(candidate),
                "year": int(year),
                "rows": int(len(group)),
                **base,
                **{f"cash_{key}": value for key, value in cash.items()},
            }
        )
    return pd.DataFrame(rows)


def real_model_validation(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[dict[str, object]] = []
    real_base = daily[daily["candidate"].eq("real_no_put")].sort_values("date")
    model_base = daily[
        daily["candidate"].eq("model_no_put") & (daily["date"] >= REAL_START)
    ].sort_values("date")
    real_base_metrics, model_base_metrics = metrics(real_base["cash_ret"]), metrics(model_base["cash_ret"])
    for variant in ALL_VARIANTS:
        real = daily[daily["candidate"].eq(f"real_{variant}")].sort_values("date")
        model = daily[
            daily["candidate"].eq(f"model_{variant}") & (daily["date"] >= REAL_START)
        ].sort_values("date")
        if not real["date"].reset_index(drop=True).equals(model["date"].reset_index(drop=True)):
            raise RuntimeError(f"Real/model calendar mismatch: {variant}")
        real_metrics, model_metrics = metrics(real["cash_ret"]), metrics(model["cash_ret"])
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
    stats = {
        "spearman_cagr_delta": float(
            active["real_cagr_delta_vs_no_put"].rank().corr(
                active["model_cagr_delta_vs_no_put"].rank()
            )
        ),
        "same_cagr_direction_ratio": float(
            (
                active["real_cagr_delta_vs_no_put"] * active["model_cagr_delta_vs_no_put"] >= 0
            ).mean()
        ),
        "same_dd_direction_ratio": float(
            (
                active["real_dd_improvement_vs_no_put"]
                * active["model_dd_improvement_vs_no_put"]
                >= 0
            ).mean()
        ),
    }
    return table, stats


def event_concentration(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ["model", "real"]:
        baseline = daily[daily["candidate"].eq(f"{layer}_no_put")][["date", "cash_ret"]].rename(
            columns={"cash_ret": "baseline_ret"}
        )
        for variant in VARIANTS:
            label = f"{layer}_{variant}"
            candidate = daily[daily["candidate"].eq(label)][["date", "cash_ret"]].rename(
                columns={"cash_ret": "candidate_ret"}
            )
            merged = candidate.merge(baseline, on="date", validate="one_to_one")
            merged["relative_log"] = np.log1p(merged["candidate_ret"]) - np.log1p(
                merged["baseline_ret"]
            )
            total = float(merged["relative_log"].sum())
            top = merged.nlargest(5, "relative_log")
            rows.append(
                {
                    "candidate": label,
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


def candidate_decisions(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    window_stability: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model = formal[formal["layer"].eq("model")].copy()
    base = model[model["signal_variant"].eq("no_put")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    single_rows: list[dict[str, object]] = []
    for variant in ECON_VARIANTS:
        candidate = f"model_{variant}"
        rows = model[model["signal_variant"].eq(variant)].set_index("segment")
        dev_cagr = float(rows.loc["development", "cash_ann_return"] - base.loc["development", "cash_ann_return"])
        dev_dd = float(rows.loc["development", "cash_max_dd"] - base.loc["development", "cash_max_dd"])
        hold_cagr = float(rows.loc["holdout", "cash_ann_return"] - base.loc["holdout", "cash_ann_return"])
        hold_dd = float(rows.loc["holdout", "cash_max_dd"] - base.loc["holdout", "cash_max_dd"])
        window_cagr = {
            segment: float(rows.loc[segment, "cash_ann_return"] - base.loc[segment, "cash_ann_return"])
            for segment in REQUIRED_WINDOWS
        }
        window_dd = {
            segment: float(rows.loc[segment, "cash_max_dd"] - base.loc[segment, "cash_max_dd"])
            for segment in REQUIRED_WINDOWS
        }
        return_tolerance = all(
            window_cagr[segment] >= (-0.01 if segment in {"full", "last_10y", "last_5y"} else -0.03)
            for segment in REQUIRED_WINDOWS
        )
        improved_windows = sum(value > 1e-12 for value in window_dd.values())
        model_days = int(exposure_lookup.loc[candidate, "protected_days"])
        real_days = int(exposure_lookup.loc[f"real_{variant}", "protected_days"])
        single_pass = bool(
            dev_dd >= 0.03
            and dev_cagr >= -0.01
            and hold_dd >= 0.03
            and hold_cagr >= -0.01
            and improved_windows >= 3
            and return_tolerance
            and model_days >= 20
            and real_days >= 20
        )
        single_rows.append(
            {
                "signal_variant": variant,
                **variant_parameters(variant),
                "development_cagr_delta": dev_cagr,
                "development_dd_improvement": dev_dd,
                "holdout_cagr_delta": hold_cagr,
                "holdout_dd_improvement": hold_dd,
                "improved_required_windows": improved_windows,
                "return_tolerance_pass": return_tolerance,
                "model_protected_days": model_days,
                "real_protected_days": real_days,
                "average_target_fraction": float(
                    exposure_lookup.loc[candidate, "average_target_fraction"]
                ),
                "single_candidate_pass": single_pass,
            }
        )
    decisions = pd.DataFrame(single_rows)
    pass_lookup = decisions.set_index("signal_variant")["single_candidate_pass"].to_dict()

    ridge_rows: list[dict[str, object]] = []
    for row in decisions.itertuples(index=False):
        variant = row.signal_variant
        params = variant_parameters(variant)
        window = int(params["window_years"])
        lower, full_risk = float(params["lower_risk"]), float(params["full_risk"])
        window_neighbors = [value for value in ECON_WINDOWS if abs(value - window) == 2]
        window_support = False
        stability_support = False
        for neighbor in window_neighbors:
            neighbor_variant = f"econ_w{neighbor:02d}_l{int(lower*100):02d}_h{int(full_risk*100):02d}"
            if not pass_lookup.get(neighbor_variant, False):
                continue
            match = window_stability[
                (
                    window_stability["left_variant"].isin([variant, neighbor_variant])
                    & window_stability["right_variant"].isin([variant, neighbor_variant])
                )
            ]
            if len(match) != 1:
                raise RuntimeError(f"Missing window stability pair: {variant}, {neighbor_variant}")
            window_support = True
            stability_support = bool(
                float(match.iloc[0]["protected_day_jaccard"]) >= 0.60
                and float(match.iloc[0]["active_target_mae"]) <= 0.20
            )
            if stability_support:
                break

        mapping_index = MAPPINGS.index((lower, full_risk))
        mapping_neighbors = []
        if mapping_index > 0:
            mapping_neighbors.append(MAPPINGS[mapping_index - 1])
        if mapping_index < len(MAPPINGS) - 1:
            mapping_neighbors.append(MAPPINGS[mapping_index + 1])
        mapping_support = any(
            pass_lookup.get(
                f"econ_w{window:02d}_l{int(item[0]*100):02d}_h{int(item[1]*100):02d}", False
            )
            for item in mapping_neighbors
        )
        all_pass = bool(
            row.single_candidate_pass and window_support and stability_support and mapping_support
        )
        ridge_rows.append(
            {
                "signal_variant": variant,
                "window_neighbor_pass": window_support,
                "active_stability_pass": stability_support,
                "mapping_neighbor_pass": mapping_support,
                "all_preregistered_pass": all_pass,
            }
        )
    decisions = decisions.merge(pd.DataFrame(ridge_rows), on="signal_variant", validate="one_to_one")
    passed = decisions[decisions["all_preregistered_pass"]].copy()
    if passed.empty:
        summary = {
            "decision": "keep_default",
            "stability_label": "reject",
            "selected_variant": None,
            "passing_candidates": [],
        }
    else:
        passed = passed.sort_values(
            ["average_target_fraction", "window_years", "lower_risk", "full_risk"]
        )
        minimum = float(passed.iloc[0]["average_target_fraction"])
        near = passed[passed["average_target_fraction"] <= minimum + 0.01].copy()
        preferred = near[
            (near["window_years"] == 10)
            & (near["lower_risk"] == 0.50)
            & (near["full_risk"] == 0.90)
        ]
        selected = preferred.iloc[0] if len(preferred) else near.iloc[0]
        summary = {
            "decision": "watchlist",
            "stability_label": "wide_stable" if len(passed) >= 4 else "narrow_stable",
            "selected_variant": str(selected["signal_variant"]),
            "passing_candidates": passed["signal_variant"].tolist(),
        }
    return decisions, summary


def build_record(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    decisions: pd.DataFrame,
    decision_summary: dict[str, object],
    current: pd.DataFrame,
    cross_stats: dict[str, object],
    qvix_stats: dict[str, object],
) -> str:
    full = formal[
        formal["layer"].eq("model")
        & formal["segment"].isin(["full", "last_10y", "last_5y", "last_3y", "last_1y"])
        & formal["available"].eq(True)
    ][["candidate", "segment", "cash_ann_return", "cash_max_dd"]]
    decision_cols = [
        "signal_variant",
        "development_cagr_delta",
        "development_dd_improvement",
        "holdout_cagr_delta",
        "holdout_dd_improvement",
        "all_preregistered_pass",
    ]
    current_cols = [
        "signal_variant",
        "risk_score",
        "research_target_fraction",
        "history_months",
    ]
    lines = [
        "# IC + 510500 Put 滚动连续估值保护 v3",
        "",
        "> 研究回测；未获准实盘；当前目标仅为审计状态，不是订单。",
        "",
        "## 决定",
        "",
        f"- 决定：`{decision_summary['decision']}`。",
        f"- 稳定性：`{decision_summary['stability_label']}`。",
        f"- 观察线：`{decision_summary['selected_variant']}`。",
        "",
        "## 模型层强制窗口",
        "",
        full.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 预注册判断",
        "",
        decisions[decision_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 2026-08-14研究状态",
        "",
        current[current_cols].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 验证与限制",
        "",
        f"- QVIX代理校验：{'通过' if qvix_stats['passed'] else '未通过'}。",
        f"- 实际/模型CAGR差方向一致率：{cross_stats['same_cagr_direction_ratio']:.2%}。",
        f"- 实际/模型回撤改善方向一致率：{cross_stats['same_dd_direction_ratio']:.2%}。",
        "- 2015—2022仍为模型Put；实际层是第三方日线，不代表可成交盘口。",
        "- 所有阈值、窗口、权重和选择规则均在收益计算前冻结。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    upstream = verify_inputs()
    frames = v2.load_inputs()
    daily_valuation, valuation_checks = v2.build_daily_valuation_full(
        frames["states_full"], frames["states_legacy"]
    )
    schedule, signals, window_stability, mapping_stability, current = build_signal_panel(
        frames["ic"], daily_valuation, frames["states_full"]
    )
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
            frames["ic"],
            model_schedule,
            market,
            "daily",
            "front",
            "three_tier",
            0.85,
            f"model_{variant}",
        )
        daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
        if not trades.empty:
            trade_parts.append(trades)

        real_schedule = schedule[
            schedule["layer"].eq("real") & schedule["signal_variant"].eq(variant)
        ]
        overlay, trades = proxy.run_real_candidate(
            frames["ic"],
            real_schedule,
            frames["snapshots"],
            frames["histories"],
            frames["etf500"],
            "daily",
            "front",
            "three_tier",
            f"real_{variant}",
        )
        daily_parts.append(proxy.assemble_candidate(overlay, frames["ic"]))
        if not trades.empty:
            trade_parts.append(trades)

    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["candidate", "date"]).reset_index(drop=True)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False) if trade_parts else pd.DataFrame()
    formal, scan_summary, wide = metric_outputs(daily)
    annual = annual_metrics(daily)
    exposure = v2.exposure_summary(daily, trades)
    cross_table, cross_stats = real_model_validation(daily)
    concentration = event_concentration(daily)
    decisions, decision_summary = candidate_decisions(formal, exposure, window_stability)

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
        raise RuntimeError("Invalid daily return")
    if daily.duplicated(["candidate", "date"]).any():
        raise RuntimeError("Duplicate candidate date")

    permanent = exposure[exposure["signal_variant"].isin(["always_50", "always_100"])]
    if (permanent["trade_events"] <= 0).any() or (permanent["average_put_mark_fraction"] <= 0).any():
        raise RuntimeError("Permanent Put engine benchmark is empty")
    for layer in ["model", "real"]:
        for variant, target in [("always_50", 0.5), ("always_100", 1.0)]:
            value = exposure.loc[
                exposure["candidate"].eq(f"{layer}_{variant}"), "average_target_fraction"
            ].item()
            if not math.isclose(value, target, abs_tol=1e-12):
                raise RuntimeError(f"Permanent target mismatch: {layer}_{variant}")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv", index=False)
    schedule.to_csv(OUTPUT / "evaluation_schedule.csv.gz", index=False, compression="gzip")
    signals.to_csv(OUTPUT / "valuation_signals.csv.gz", index=False, compression="gzip")
    current.to_csv(OUTPUT / "current_research_signals.csv", index=False)
    window_stability.to_csv(OUTPUT / "window_signal_stability.csv", index=False)
    mapping_stability.to_csv(OUTPUT / "mapping_signal_stability.csv", index=False)
    formal.to_csv(OUTPUT / "metrics_by_segment.csv", index=False)
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False)
    exposure.to_csv(OUTPUT / "exposure_cost_liquidity.csv", index=False)
    decisions.to_csv(OUTPUT / "candidate_decisions.csv", index=False)
    cross_table.to_csv(OUTPUT / "real_model_cross_validation.csv", index=False)
    concentration.to_csv(OUTPUT / "event_concentration.csv", index=False)
    qvix_table.to_csv(OUTPUT / "qvix_proxy_validation.csv", index=False)
    (OUTPUT / "decision_summary.json").write_text(
        json.dumps(decision_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record = build_record(formal, exposure, decisions, decision_summary, current, cross_stats, qvix_stats)
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")

    source_paths = [
        proxy.IC_DAILY,
        v2.FULL_STATES_PATH,
        v2.LEGACY_STATES_PATH,
        v2.VALUATION_PATH,
        v2.PRICE_PATH,
        v2.TRI_PATH,
        v2.GOV10Y_PATH,
        v2.PROXY_DATA_MANIFEST,
        Path(v2.__file__),
    ]
    manifest = {
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
            "development": [str(MODEL_START.date()), str(DEVELOPMENT_END.date())],
            "holdout": [str(HOLDOUT_START.date()), str(END.date())],
        },
        "valuation_checks": valuation_checks,
        "market_checks": market_checks,
        "qvix_proxy": qvix_stats,
        "real_model_cross_validation": cross_stats,
        "baseline_parity": parity,
        "decision_summary": decision_summary,
        "source_hashes": {str(path.relative_to(ROOT)): sha256(path) for path in source_paths},
        "upstream_v2_manifest_sha256": sha256(v2.OUTPUT / "data_manifest.json"),
        "upstream_proxy_file_count": len(upstream["proxy_manifest"]["files"]),
        "git_status": git_status(),
        "warnings": [
            "2007 state uses official pre-publication backcast price/TRI history.",
            "Model Put is theoretical and not historical executable pricing.",
            "Actual continuous target is implemented through integer option quantities and third-party daily bars.",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(
        "python.exe -m pytest test_ic_510500_put_rolling_continuous_valuation_v3.py -q\n"
        "python.exe ic_510500_put_rolling_continuous_valuation_v3.py\n",
        encoding="utf-8",
    )

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(SCAN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython.exe -m pytest test_ic_510500_put_rolling_continuous_valuation_v3.py -q\n")
        handle.write("python.exe ic_510500_put_rolling_continuous_valuation_v3.py\n")
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "source_hashes": manifest["source_hashes"],
            "baseline_parity": parity,
            "qvix_proxy": qvix_stats,
            "real_model_cross_validation": cross_stats,
            "decision_summary": decision_summary,
            "formal_output": str(OUTPUT.relative_to(ROOT)),
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "baseline_parity": parity,
                "qvix_passed": qvix_stats["passed"],
                **decision_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
