from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

import ic_510500_put_rolling_continuous_valuation_v3 as core


ROOT = Path(__file__).resolve().parent
VERSION = "ic_510500_put_rolling_continuous_valuation_v4"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "509c9a00ba1a88ede5f15a84a0ad37124ff3ab0e66072ea2fbd2b43124541d24"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = ROOT / "quant_param_scan_runs" / "20260816_ic_510500_put_rolling_continuous_valuation_v4"
V3_PATH = Path(core.__file__).resolve()
V3_SHA256 = "7b05088caa2ae40358ade307be6fac9728e34c276f04fba97fb110964fc4ebd9"

HISTORY_MONTHS = [84, 90, 96]
MAPPINGS = [(0.40, 0.80), (0.50, 0.90), (0.60, 0.90)]
ECON_VARIANTS = [
    f"econ_m{months:02d}_l{int(lower*100):02d}_h{int(full*100):02d}"
    for months in HISTORY_MONTHS
    for lower, full in MAPPINGS
]
STRUCTURAL_VARIANT = "equal4_m90_l50_h90"
VARIANTS = ["always_50", "always_100", *ECON_VARIANTS, STRUCTURAL_VARIANT]
ALL_VARIANTS = ["no_put", *VARIANTS]


def sha256(path: Path) -> str:
    return core.sha256(path)


def variant_parameters(variant: str) -> dict[str, object]:
    if variant in {"no_put", "always_50", "always_100"}:
        return {
            "score_type": "baseline",
            "window_months": np.nan,
            "window_years": np.nan,
            "lower_risk": np.nan,
            "full_risk": np.nan,
        }
    if variant == STRUCTURAL_VARIANT:
        return {
            "score_type": "equal4",
            "window_months": 90,
            "window_years": 7.5,
            "lower_risk": 0.50,
            "full_risk": 0.90,
        }
    match = re.fullmatch(r"econ_m(\d{2})_l(\d{2})_h(\d{2})", variant)
    if match is None:
        raise ValueError(f"Unknown v4 variant: {variant}")
    months = int(match.group(1))
    return {
        "score_type": "economic",
        "window_months": months,
        "window_years": months / 12.0,
        "lower_risk": int(match.group(2)) / 100.0,
        "full_risk": int(match.group(3)) / 100.0,
    }


def risk_score_on_day(
    day: pd.Timestamp,
    history_months: int,
    daily_row: pd.Series,
    states: pd.DataFrame,
) -> dict[str, object]:
    available = states[states["date"] <= day].sort_values("date")
    if len(available) < history_months:
        raise RuntimeError(
            f"Insufficient completed month-ends on {day.date()} for {history_months}: {len(available)}"
        )
    history = available.tail(history_months)
    if len(history) != history_months or pd.Timestamp(history["date"].max()) > day:
        raise RuntimeError("Fixed-month causal history integrity failure")
    risks = {
        "pe_risk": core.v2.risk_ecdf(
            daily_row["pe_aggregate_ttm"], history["pe_aggregate_ttm"], True
        ),
        "pb_risk": core.v2.risk_ecdf(
            daily_row["pb_aggregate"], history["pb_aggregate"], True
        ),
        "erp_risk": core.v2.risk_ecdf(daily_row["erp"], history["erp"], False),
        "dividend_risk": core.v2.risk_ecdf(
            daily_row["trailing_dividend_contribution"],
            history["trailing_dividend_contribution"],
            False,
        ),
    }
    economic = 0.25 * risks["pb_risk"] + 0.50 * risks["erp_risk"] + 0.25 * risks["dividend_risk"]
    return {
        **risks,
        "economic_risk": float(economic),
        "equal4_risk": float(np.mean(list(risks.values()))),
        "history_months": int(len(history)),
        "history_target_months": int(history_months),
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
        "model": core.proxy.evaluation_dates(
            "daily", core.MODEL_START, core.END, trade_dates, daily_valuation
        ),
        "real": core.proxy.evaluation_dates(
            "daily", core.REAL_START, core.END, trade_dates, daily_valuation
        ),
    }
    unique_days = sorted(set(layer_evals["model"]) | set(layer_evals["real"]))
    daily_lookup = daily_valuation.set_index("date")
    score_cache: dict[tuple[pd.Timestamp, int], dict[str, object]] = {}
    signal_rows: list[dict[str, object]] = []
    target_lookup: dict[str, dict[pd.Timestamp, float]] = {variant: {} for variant in VARIANTS}

    for day in unique_days:
        row = daily_lookup.loc[day]
        for months in HISTORY_MONTHS:
            score_cache[(day, months)] = risk_score_on_day(day, months, row, states)
        for variant in ECON_VARIANTS:
            params = variant_parameters(variant)
            months = int(params["window_months"])
            values = score_cache[(day, months)]
            score = float(values["economic_risk"])
            raw, target = core.map_score(
                score, float(params["lower_risk"]), float(params["full_risk"])
            )
            target_lookup[variant][day] = target
            signal_rows.append(
                {
                    "signal_variant": variant,
                    "eval_date": day,
                    **params,
                    **{feature: float(row[feature]) for feature in core.v2.FEATURES},
                    **values,
                    "risk_score": score,
                    "raw_target_fraction": raw,
                    "target_fraction": target,
                }
            )

        params = variant_parameters(STRUCTURAL_VARIANT)
        values = score_cache[(day, 90)]
        score = float(values["equal4_risk"])
        raw, target = core.map_score(score, 0.50, 0.90)
        target_lookup[STRUCTURAL_VARIANT][day] = target
        signal_rows.append(
            {
                "signal_variant": STRUCTURAL_VARIANT,
                "eval_date": day,
                **params,
                **{feature: float(row[feature]) for feature in core.v2.FEATURES},
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
        start = core.MODEL_START if layer == "model" else core.REAL_START
        for variant in VARIANTS:
            for sequence, day in enumerate(evaluations):
                execution, initial = core.proxy.next_execution(day, start, trade_dates)
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
        raise RuntimeError("Duplicate v4 execution schedule")
    regular = schedule[~schedule["initial_exception"]]
    if (regular["execution_date"] <= regular["eval_date"]).any():
        raise RuntimeError("v4 signal execution leakage")

    signals = pd.DataFrame(signal_rows).sort_values(["signal_variant", "eval_date"]).reset_index(drop=True)
    window_stability = build_window_stability(signals)
    mapping_stability = build_mapping_stability(signals)
    current = build_current_signals(daily_lookup.loc[core.END], states)
    return schedule, signals, window_stability, mapping_stability, current


def build_window_stability(signals: pd.DataFrame) -> pd.DataFrame:
    economic = signals[signals["signal_variant"].isin(ECON_VARIANTS)]
    pivot = economic.pivot(index="eval_date", columns="signal_variant", values="target_fraction")
    rows: list[dict[str, object]] = []
    for lower, full in MAPPINGS:
        variants = {
            months: f"econ_m{months:02d}_l{int(lower*100):02d}_h{int(full*100):02d}"
            for months in HISTORY_MONTHS
        }
        for left_months, right_months in [(84, 90), (90, 96), (84, 96)]:
            left, right = variants[left_months], variants[right_months]
            rows.append(
                {
                    "lower_risk": lower,
                    "full_risk": full,
                    "left_window_months": left_months,
                    "right_window_months": right_months,
                    "left_variant": left,
                    "right_variant": right,
                    **core.active_comparison(pivot[left], pivot[right]),
                }
            )
    return pd.DataFrame(rows)


def build_mapping_stability(signals: pd.DataFrame) -> pd.DataFrame:
    economic = signals[signals["signal_variant"].isin(ECON_VARIANTS)]
    pivot = economic.pivot(index="eval_date", columns="signal_variant", values="target_fraction")
    rows: list[dict[str, object]] = []
    for months in HISTORY_MONTHS:
        labels = [
            f"econ_m{months:02d}_l{int(lower*100):02d}_h{int(full*100):02d}"
            for lower, full in MAPPINGS
        ]
        for left, right in zip(labels[:-1], labels[1:]):
            rows.append(
                {
                    "window_months": months,
                    "left_variant": left,
                    "right_variant": right,
                    **core.active_comparison(pivot[left], pivot[right]),
                }
            )
    return pd.DataFrame(rows)


def build_current_signals(current_row: pd.Series, states: pd.DataFrame) -> pd.DataFrame:
    cache = {
        months: risk_score_on_day(core.END, months, current_row, states)
        for months in HISTORY_MONTHS
    }
    rows: list[dict[str, object]] = []
    for variant in [*ECON_VARIANTS, STRUCTURAL_VARIANT]:
        params = variant_parameters(variant)
        values = cache[int(params["window_months"])]
        score = float(
            values["equal4_risk"] if variant == STRUCTURAL_VARIANT else values["economic_risk"]
        )
        raw, target = core.map_score(
            score, float(params["lower_risk"]), float(params["full_risk"])
        )
        rows.append(
            {
                "as_of": core.END,
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


def candidate_decisions(
    formal: pd.DataFrame,
    exposure: pd.DataFrame,
    window_stability: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    model = formal[formal["layer"].eq("model")]
    base = model[model["signal_variant"].eq("no_put")].set_index("segment")
    exposure_lookup = exposure.set_index("candidate")
    rows: list[dict[str, object]] = []
    for variant in ECON_VARIANTS:
        candidate = f"model_{variant}"
        metrics = model[model["signal_variant"].eq(variant)].set_index("segment")
        dev_cagr = float(metrics.loc["development", "cash_ann_return"] - base.loc["development", "cash_ann_return"])
        dev_dd = float(metrics.loc["development", "cash_max_dd"] - base.loc["development", "cash_max_dd"])
        hold_cagr = float(metrics.loc["holdout", "cash_ann_return"] - base.loc["holdout", "cash_ann_return"])
        hold_dd = float(metrics.loc["holdout", "cash_max_dd"] - base.loc["holdout", "cash_max_dd"])
        window_cagr = {
            segment: float(metrics.loc[segment, "cash_ann_return"] - base.loc[segment, "cash_ann_return"])
            for segment in core.REQUIRED_WINDOWS
        }
        window_dd = {
            segment: float(metrics.loc[segment, "cash_max_dd"] - base.loc[segment, "cash_max_dd"])
            for segment in core.REQUIRED_WINDOWS
        }
        return_pass = all(
            window_cagr[segment]
            >= (-0.01 if segment in {"full", "last_10y", "last_5y"} else -0.03)
            for segment in core.REQUIRED_WINDOWS
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
            and return_pass
            and model_days >= 20
            and real_days >= 20
        )
        rows.append(
            {
                "signal_variant": variant,
                **variant_parameters(variant),
                "development_cagr_delta": dev_cagr,
                "development_dd_improvement": dev_dd,
                "holdout_cagr_delta": hold_cagr,
                "holdout_dd_improvement": hold_dd,
                "improved_required_windows": improved_windows,
                "return_tolerance_pass": return_pass,
                "model_protected_days": model_days,
                "real_protected_days": real_days,
                "average_target_fraction": float(
                    exposure_lookup.loc[candidate, "average_target_fraction"]
                ),
                "single_candidate_pass": single_pass,
            }
        )
    decisions = pd.DataFrame(rows)
    pass_lookup = decisions.set_index("signal_variant")["single_candidate_pass"].to_dict()
    ridge_rows: list[dict[str, object]] = []
    for row in decisions.itertuples(index=False):
        variant = row.signal_variant
        params = variant_parameters(variant)
        months = int(params["window_months"])
        lower, full = float(params["lower_risk"]), float(params["full_risk"])
        month_neighbors = [value for value in HISTORY_MONTHS if abs(value - months) == 6]
        month_support = False
        stability_support = False
        for neighbor in month_neighbors:
            neighbor_variant = f"econ_m{neighbor:02d}_l{int(lower*100):02d}_h{int(full*100):02d}"
            if not pass_lookup.get(neighbor_variant, False):
                continue
            pair = window_stability[
                window_stability["left_variant"].isin([variant, neighbor_variant])
                & window_stability["right_variant"].isin([variant, neighbor_variant])
            ]
            if len(pair) != 1:
                raise RuntimeError(f"Missing v4 stability pair: {variant}, {neighbor_variant}")
            month_support = True
            stability_support = bool(
                float(pair.iloc[0]["protected_day_jaccard"]) >= 0.60
                and float(pair.iloc[0]["active_target_mae"]) <= 0.20
            )
            if stability_support:
                break

        mapping_index = MAPPINGS.index((lower, full))
        mapping_neighbors = []
        if mapping_index > 0:
            mapping_neighbors.append(MAPPINGS[mapping_index - 1])
        if mapping_index < len(MAPPINGS) - 1:
            mapping_neighbors.append(MAPPINGS[mapping_index + 1])
        mapping_support = any(
            pass_lookup.get(
                f"econ_m{months:02d}_l{int(item[0]*100):02d}_h{int(item[1]*100):02d}", False
            )
            for item in mapping_neighbors
        )
        ridge_rows.append(
            {
                "signal_variant": variant,
                "window_neighbor_pass": month_support,
                "active_stability_pass": stability_support,
                "mapping_neighbor_pass": mapping_support,
                "all_preregistered_pass": bool(
                    row.single_candidate_pass
                    and month_support
                    and stability_support
                    and mapping_support
                ),
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
            ["average_target_fraction", "window_months", "lower_risk", "full_risk"]
        )
        minimum = float(passed.iloc[0]["average_target_fraction"])
        near = passed[passed["average_target_fraction"] <= minimum + 0.01]
        preferred = near[
            (near["window_months"] == 90)
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


def configure_core() -> None:
    core.VERSION = VERSION
    core.SPEC = SPEC
    core.SPEC_HASH_FILE = SPEC_HASH_FILE
    core.SPEC_SHA256 = SPEC_SHA256
    core.OUTPUT = OUTPUT
    core.SCAN = SCAN
    core.ECON_WINDOWS = HISTORY_MONTHS
    core.MAPPINGS = MAPPINGS
    core.ECON_VARIANTS = ECON_VARIANTS
    core.STRUCTURAL_VARIANT = STRUCTURAL_VARIANT
    core.VARIANTS = VARIANTS
    core.ALL_VARIANTS = ALL_VARIANTS
    core.variant_parameters = variant_parameters
    core.risk_score_on_day = risk_score_on_day
    core.build_signal_panel = build_signal_panel
    core.build_window_stability = build_window_stability
    core.build_mapping_stability = build_mapping_stability
    core.build_current_signals = build_current_signals
    core.candidate_decisions = candidate_decisions
    core.__file__ = str(Path(__file__).resolve())


def augment_dependency_manifests() -> None:
    manifest_path = OUTPUT / "data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["framework_dependency"] = {
        "path": str(V3_PATH.relative_to(ROOT)),
        "sha256": V3_SHA256,
        "role": "common tested harness; v4 overrides fixed-month signal layer",
    }
    manifest["source_hashes"][str(V3_PATH.relative_to(ROOT))] = V3_SHA256
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["dependencies"] = {"v3_script": str(V3_PATH.relative_to(ROOT)), "v3_script_sha256": V3_SHA256}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> None:
    if sha256(V3_PATH) != V3_SHA256:
        raise RuntimeError("Frozen v3 framework dependency changed")
    configure_core()
    core.main()
    augment_dependency_manifests()


if __name__ == "__main__":
    main()
