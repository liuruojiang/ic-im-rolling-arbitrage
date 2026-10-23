#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
# ]
# ///
"""Audit unbounded fixed-unit CSI500 valuation mean and two-of-three scores."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

from ic_fixed_valuation_raw_economic_score_v5 import (
    episode_stats,
    jaccard,
    old_fixed_risk,
    price_metrics,
    window_boundaries,
)

ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_unbounded_score_v6"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V5_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_raw_economic_score_v5"
MONTHLY_INPUT = V5_OUTPUT / "monthly_fixed_economic_scores.csv"
DAILY_INPUT = V5_OUTPUT / "daily_fixed_economic_scores.csv.gz"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_unbounded_score_v6_valuation_body_unbounded_mean_median_threshold"
)
EXPECTED_HASHES = {
    ROOT / "ic_fixed_valuation_raw_economic_score_v5.py": (
        "b4a58f5212f05e850e526352712a0a66489df34659f23f7572fe0d5f386a0414"
    ),
    ROOT / "test_ic_fixed_valuation_raw_economic_score_v5.py": (
        "b0ee26995fc0d3291eb3caaf667e26a7e909b4d1e9e7300a81368c2884ba959b"
    ),
    ROOT / "docs" / "ic_fixed_valuation_raw_economic_score_v5_spec.md": (
        "82f76097e918dd153ff47adca19cc4b0f7c42c1d7696b6389d6c7bb24b7187c5"
    ),
    ROOT / "docs" / "ic_fixed_valuation_raw_economic_score_v5_postrun_audit.md": (
        "67198d41aacf957ad035963fe191167378ceb2f5b6cf86bb22091e1e3797c588"
    ),
    MONTHLY_INPUT: "5eda25491715b45e82a84619e84508fcf7bc242f8647ff78a3e913ec6580649b",
    DAILY_INPUT: "c3be3cede20e1ff79faa89eb7d6ef59730651df79f841a68401b0f1789937247",
    V5_OUTPUT / "integrity_checks.json": (
        "df01060c2d05b0aa3efe1efa61d6dcdc7508eb3996f643fba25071be5915a729"
    ),
    V5_OUTPUT / "output_manifest.json": (
        "ce66ef1e899cc078a2f3194c309480dcb6921f59930ea74978caf7f8050fef03"
    ),
    V5_OUTPUT / "record.md": (
        "b5128198a119adb41d2d1c9d1507225acbd2fd1c9cfbdf18a23450c93209e094"
    ),
}
THRESHOLDS = np.round(np.arange(1.50, 3.00 + 0.0001, 0.05), 2)
V5_OVERLAP_THRESHOLDS = np.round(np.arange(1.50, 2.00 + 0.0001, 0.05), 2)
FAMILIES = ("unbounded_mean", "unbounded_median")
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
ECONOMIC_FIELDS = {
    "pb_aggregate": "PB",
    "erp": "ERP",
    "trailing_dividend_contribution": "过去一年股息贡献",
}
PRESSURE_NAMES = ("pb", "erp", "dividend")
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(output: Path) -> None:
    manifest = json.loads((output / "output_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        if sha256(output / name) != expected:
            raise RuntimeError(f"Upstream manifest mismatch: {output / name}")


def verify_inputs() -> dict[str, str]:
    expected_spec = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sha256(SPEC) != expected_spec:
        raise RuntimeError("Frozen v6 specification mismatch")
    if OUTPUT.exists():
        raise FileExistsError(
            f"Formal output exists and cannot be overwritten: {OUTPUT}"
        )
    if STAGING.exists():
        raise FileExistsError(f"Staging output already exists: {STAGING}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {SCAN}")
    for path, expected in EXPECTED_HASHES.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen input mismatch: {path}: {actual} != {expected}")
    verify_manifest(V5_OUTPUT)
    integrity = json.loads(
        (V5_OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    if integrity.get("status") != "passed":
        raise RuntimeError("v5 upstream is not authoritative")
    return {str(path.relative_to(ROOT)): sha256(path) for path in EXPECTED_HASHES}


def unbounded_components(frame: pd.DataFrame, convention: str) -> pd.DataFrame:
    pb = pd.to_numeric(frame["pb_aggregate"])
    erp = pd.to_numeric(frame["erp"])
    dividend = pd.to_numeric(frame["trailing_dividend_contribution"])
    if convention == "knot":
        values = {
            "pb": (pb - 1.50) / 0.50,
            "erp": (0.045 - erp) / 0.015,
            "dividend": (0.030 - dividend) / 0.010,
        }
    elif convention == "mid":
        values = {
            "pb": 0.50 + (pb - 2.00) / 0.50,
            "erp": 0.50 + (0.030 - erp) / 0.015,
            "dividend": 0.50 + (0.020 - dividend) / 0.010,
        }
    else:
        raise ValueError(f"Unknown convention: {convention}")
    result = pd.DataFrame(
        {
            f"unbounded_{name}_pressure_{convention}": value
            for name, value in values.items()
        },
        index=frame.index,
    )
    columns = [f"unbounded_{name}_pressure_{convention}" for name in PRESSURE_NAMES]
    result[f"unbounded_mean_{convention}"] = result[columns].mean(axis=1)
    result[f"unbounded_median_{convention}"] = result[columns].median(axis=1)
    return result


def add_unbounded_scores(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for convention in ("knot", "mid"):
        scores = unbounded_components(result, convention)
        for column in scores:
            result[column] = scores[column]
    knot_columns = [f"unbounded_{name}_pressure_knot" for name in PRESSURE_NAMES]
    result["unbounded_max_pressure_knot"] = result[knot_columns].max(axis=1)
    result["unbounded_min_pressure_knot"] = result[knot_columns].min(axis=1)
    result["unbounded_max_minus_median"] = (
        result["unbounded_max_pressure_knot"] - result["unbounded_median_knot"]
    )
    result["unbounded_mean_minus_median"] = (
        result["unbounded_mean_knot"] - result["unbounded_median_knot"]
    )
    result["old_fixed_risk"] = old_fixed_risk(result)
    return result


def raw_threshold_map() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "threshold": THRESHOLDS,
            "pb_at_least": 1.50 + 0.50 * THRESHOLDS,
            "erp_at_most": 0.045 - 0.015 * THRESHOLDS,
            "dividend_at_most": 0.030 - 0.010 * THRESHOLDS,
            "rule": "at_least_two_of_three",
        }
    )


def quantile_fields(part: pd.DataFrame, prefix: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in ECONOMIC_FIELDS:
        for label, quantile in (("p25", 0.25), ("median", 0.50), ("p75", 0.75)):
            result[f"{prefix}_{field}_{label}"] = (
                float(part[field].quantile(quantile)) if not part.empty else math.nan
            )
    return result


def make_economic_boundary(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for family in FAMILIES:
        score = monthly[f"{family}_knot"]
        for threshold in THRESHOLDS:
            active = score.ge(threshold)
            local = (score - threshold).abs().le(0.10 + 1e-12)
            crossing = active & ~active.shift(1, fill_value=False)
            active_frame = monthly.loc[active]
            local_frame = monthly.loc[local]
            crossing_frame = monthly.loc[crossing]
            row: dict[str, object] = {
                "family": family,
                "threshold": float(threshold),
                "local_band_low": float(threshold - 0.10),
                "local_band_high": float(threshold + 0.10),
                "local_months": int(local.sum()),
                "active_months": int(active.sum()),
                "upward_crossings": int(crossing.sum()),
                "active_share_median_ge_1": (
                    float(active_frame["unbounded_median_knot"].ge(1.0).mean())
                    if not active_frame.empty
                    else math.nan
                ),
                "active_share_median_ge_same_threshold": (
                    float(active_frame["unbounded_median_knot"].ge(threshold).mean())
                    if not active_frame.empty
                    else math.nan
                ),
                "active_median_pressure_median": (
                    float(active_frame["unbounded_median_knot"].median())
                    if not active_frame.empty
                    else math.nan
                ),
                "active_max_minus_median_median": (
                    float(active_frame["unbounded_max_minus_median"].median())
                    if not active_frame.empty
                    else math.nan
                ),
                "active_max_minus_median_p75": (
                    float(active_frame["unbounded_max_minus_median"].quantile(0.75))
                    if not active_frame.empty
                    else math.nan
                ),
                **quantile_fields(local_frame, "boundary"),
                **quantile_fields(active_frame, "active"),
            }
            for field in ECONOMIC_FIELDS:
                row[f"crossing_{field}_median"] = (
                    float(crossing_frame[field].median())
                    if not crossing_frame.empty
                    else math.nan
                )
            rows.append(row)
    return pd.DataFrame(rows)


def recent_episode_count(active: pd.Series, mask: pd.Series) -> int:
    part = active.loc[mask].reset_index(drop=True)
    return episode_stats(part)[0] if not part.empty else 0


def contiguous_bands(table: pd.DataFrame, pass_column: str) -> list[list[int]]:
    bands: list[list[int]] = []
    current: list[int] = []
    previous: float | None = None
    for index, row in table.iterrows():
        threshold = float(row["threshold"])
        if bool(row[pass_column]):
            if previous is None or np.isclose(threshold - previous, 0.05):
                current.append(index)
            else:
                if current:
                    bands.append(current)
                current = [index]
            previous = threshold
        else:
            if current:
                bands.append(current)
            current = []
            previous = None
    if current:
        bands.append(current)
    return [indices for indices in bands if len(indices) >= 3]


def band_summaries(
    table: pd.DataFrame, bands: list[list[int]]
) -> list[dict[str, float | int]]:
    return [
        {
            "low": float(table.loc[indices, "threshold"].min()),
            "high": float(table.loc[indices, "threshold"].max()),
            "points": len(indices),
        }
        for indices in bands
    ]


def select_platform(
    table: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    table = table.copy()
    family_columns = {
        "mean": "mean_core_pass",
        "median": "median_core_pass",
    }
    family_bands: dict[str, list[list[int]]] = {}
    for label, column in family_columns.items():
        bands = contiguous_bands(table, column)
        family_bands[label] = bands
        table[f"{label}_platform_band_id"] = 0
        for band_id, indices in enumerate(bands, start=1):
            table.loc[indices, f"{label}_platform_band_id"] = band_id

    joint_bands = contiguous_bands(table, "all_individual_gates_pass")
    table["joint_platform_band_id"] = 0
    for band_id, indices in enumerate(joint_bands, start=1):
        table.loc[indices, "joint_platform_band_id"] = band_id
    table["in_selected_band"] = False
    table["is_design_center"] = False
    base_decision: dict[str, Any] = {
        "mean_independent_platforms": band_summaries(table, family_bands["mean"]),
        "median_independent_platforms": band_summaries(table, family_bands["median"]),
        "threshold_is_live_approved": False,
    }
    if not joint_bands:
        return table, {
            **base_decision,
            "platform_found": False,
            "selected_band_low": None,
            "selected_band_high": None,
            "selected_band_points": 0,
            "design_center_threshold": None,
        }
    selected = max(
        joint_bands,
        key=lambda indices: (
            len(indices),
            float(table.loc[indices, "threshold"].min()),
        ),
    )
    center_index = selected[(len(selected) - 1) // 2]
    table.loc[selected, "in_selected_band"] = True
    table.loc[center_index, "is_design_center"] = True
    return table, {
        **base_decision,
        "platform_found": True,
        "selected_band_low": float(table.loc[selected, "threshold"].min()),
        "selected_band_high": float(table.loc[selected, "threshold"].max()),
        "selected_band_points": len(selected),
        "design_center_threshold": float(table.loc[center_index, "threshold"]),
    }


def make_threshold_selection(
    monthly: pd.DataFrame, economic: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, Any]]:
    recent_mask = monthly["date"] >= monthly["date"].max() - pd.DateOffset(years=10)
    economic_index = economic.set_index(["family", "threshold"])
    rows = []
    for threshold in THRESHOLDS:
        mean_active = monthly["unbounded_mean_knot"].ge(threshold)
        median_active = monthly["unbounded_median_knot"].ge(threshold)
        mean_full_episodes, mean_longest = episode_stats(mean_active)
        median_full_episodes, median_longest = episode_stats(median_active)
        mean_recent_episodes = recent_episode_count(mean_active, recent_mask)
        median_recent_episodes = recent_episode_count(median_active, recent_mask)
        mean_recent_ratio = float(mean_active.loc[recent_mask].mean())
        median_recent_ratio = float(median_active.loc[recent_mask].mean())
        mean_econ = economic_index.loc[("unbounded_mean", threshold)]
        median_econ = economic_index.loc[("unbounded_median", threshold)]
        full_jaccard = jaccard(mean_active, median_active)
        recent_jaccard = jaccard(
            mean_active.loc[recent_mask], median_active.loc[recent_mask]
        )
        recent_difference = abs(mean_recent_ratio - median_recent_ratio)
        gates = {
            "gate_mean_episode_count": bool(
                mean_full_episodes >= 5 and mean_recent_episodes >= 2
            ),
            "gate_mean_recent_tail_coverage": bool(0.05 <= mean_recent_ratio <= 0.30),
            "gate_mean_local_boundary_sample": bool(
                int(mean_econ["local_months"]) >= 8
            ),
            "gate_median_episode_count": bool(
                median_full_episodes >= 5 and median_recent_episodes >= 2
            ),
            "gate_median_recent_tail_coverage": bool(
                0.05 <= median_recent_ratio <= 0.30
            ),
            "gate_median_local_boundary_sample": bool(
                int(median_econ["local_months"]) >= 8
            ),
            "gate_mean_broad_factor_evidence": bool(
                mean_econ["active_share_median_ge_1"] >= 0.90
            ),
            "gate_joint_state_confirmation": bool(
                full_jaccard >= 0.70
                and recent_jaccard >= 0.70
                and recent_difference <= 0.10
            ),
        }
        mean_core = all(
            gates[name]
            for name in (
                "gate_mean_episode_count",
                "gate_mean_recent_tail_coverage",
                "gate_mean_local_boundary_sample",
                "gate_mean_broad_factor_evidence",
            )
        )
        median_core = all(
            gates[name]
            for name in (
                "gate_median_episode_count",
                "gate_median_recent_tail_coverage",
                "gate_median_local_boundary_sample",
            )
        )
        rows.append(
            {
                "threshold": float(threshold),
                "mean_full_monthly_activation": float(mean_active.mean()),
                "median_full_monthly_activation": float(median_active.mean()),
                "mean_recent10_monthly_activation": mean_recent_ratio,
                "median_recent10_monthly_activation": median_recent_ratio,
                "recent10_activation_abs_diff": recent_difference,
                "mean_full_monthly_episodes": mean_full_episodes,
                "median_full_monthly_episodes": median_full_episodes,
                "mean_recent10_monthly_episodes": mean_recent_episodes,
                "median_recent10_monthly_episodes": median_recent_episodes,
                "mean_full_longest_active_months": mean_longest,
                "median_full_longest_active_months": median_longest,
                "mean_local_months": int(mean_econ["local_months"]),
                "median_local_months": int(median_econ["local_months"]),
                "mean_broad_factor_share": float(mean_econ["active_share_median_ge_1"]),
                "full_monthly_mean_median_jaccard": full_jaccard,
                "recent10_monthly_mean_median_jaccard": recent_jaccard,
                "current_mean_active": bool(
                    monthly["unbounded_mean_knot"].iloc[-1] >= threshold
                ),
                "current_median_active": bool(
                    monthly["unbounded_median_knot"].iloc[-1] >= threshold
                ),
                "mean_core_pass": bool(mean_core),
                "median_core_pass": bool(median_core),
                **gates,
                "all_individual_gates_pass": bool(all(gates.values())),
            }
        )
    return select_platform(pd.DataFrame(rows))


def make_v5_parity(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    parity_rows = []
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        for convention in ("knot", "mid"):
            for name in PRESSURE_NAMES:
                expected = frame[f"unbounded_{name}_pressure_{convention}"].clip(
                    0.0, 2.0
                )
                actual = frame[f"{name}_pressure_{convention}"]
                parity_rows.append(
                    {
                        "frequency": frequency,
                        "convention": convention,
                        "component": name,
                        "rows": len(frame),
                        "max_abs_error": float(np.max(np.abs(expected - actual))),
                    }
                )
    coverage_rows = []
    anchor = daily["date"].max()
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        scopes = {
            "full": frame,
            "last_10y": frame[frame["date"] >= anchor - pd.DateOffset(years=10)],
        }
        for scope, part in scopes.items():
            for threshold in V5_OVERLAP_THRESHOLDS:
                capped = part["fixed_equal3_knot"].ge(threshold)
                unbounded = part["unbounded_mean_knot"].ge(threshold)
                coverage_rows.append(
                    {
                        "frequency": frequency,
                        "scope": scope,
                        "threshold": float(threshold),
                        "v5_capped_activation_ratio": float(capped.mean()),
                        "v6_unbounded_activation_ratio": float(unbounded.mean()),
                        "activation_ratio_difference": float(
                            unbounded.mean() - capped.mean()
                        ),
                        "state_jaccard": jaccard(capped, unbounded),
                    }
                )
    return pd.DataFrame(parity_rows), pd.DataFrame(coverage_rows)


def vintage_dates(monthly: pd.DataFrame) -> list[pd.Timestamp]:
    dates: list[pd.Timestamp] = []
    for year in range(2016, 2026):
        part = monthly[
            (monthly["date"].dt.year == year) & (monthly["date"].dt.month == 12)
        ]
        if part.empty:
            raise RuntimeError(f"Missing December vintage for {year}")
        dates.append(pd.Timestamp(part["date"].max()))
    dates.append(pd.Timestamp(monthly["date"].max()))
    return dates


def make_vintage_invariance(monthly: pd.DataFrame) -> pd.DataFrame:
    raw_columns = [
        "date",
        "price_close",
        "pb_aggregate",
        "erp",
        "trailing_dividend_contribution",
    ]
    rows = []
    for vintage in vintage_dates(monthly):
        history = monthly[monthly["date"] <= vintage].copy().reset_index(drop=True)
        recalculated = add_unbounded_scores(history[raw_columns])
        errors = {}
        for family in FAMILIES:
            for convention in ("knot", "mid"):
                column = f"{family}_{convention}"
                errors[column] = float(
                    np.max(np.abs(recalculated[column] - history[column]))
                )
        states_match = all(
            np.array_equal(
                recalculated[f"{family}_knot"].ge(threshold),
                history[f"{family}_knot"].ge(threshold),
            )
            for family in FAMILIES
            for threshold in THRESHOLDS
        )
        rows.append(
            {
                "vintage_date": vintage.date().isoformat(),
                "history_start": history["date"].min().date().isoformat(),
                "history_end": history["date"].max().date().isoformat(),
                "history_months": len(history),
                "mean_knot_max_abs_error": errors["unbounded_mean_knot"],
                "median_knot_max_abs_error": errors["unbounded_median_knot"],
                "mean_mid_max_abs_error": errors["unbounded_mean_mid"],
                "median_mid_max_abs_error": errors["unbounded_median_mid"],
                "all_family_threshold_states_match": states_match,
                "future_rows_used": False,
            }
        )
    return pd.DataFrame(rows)


def make_gap_diagnostics(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows = []
    anchor = daily["date"].max()
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        scopes = {
            "full": frame,
            "last_10y": frame[frame["date"] >= anchor - pd.DateOffset(years=10)],
        }
        for scope, part in scopes.items():
            gap = part["unbounded_mean_minus_median"]
            absolute = gap.abs()
            summary_rows.append(
                {
                    "frequency": frequency,
                    "scope": scope,
                    "rows": len(part),
                    "mean_median_correlation": float(
                        part["unbounded_mean_knot"].corr(part["unbounded_median_knot"])
                    ),
                    "gap_mean": float(gap.mean()),
                    "gap_median": float(gap.median()),
                    "gap_p25": float(gap.quantile(0.25)),
                    "gap_p75": float(gap.quantile(0.75)),
                    "absolute_gap_median": float(absolute.median()),
                    "absolute_gap_p90": float(absolute.quantile(0.90)),
                    "absolute_gap_max": float(absolute.max()),
                    "max_minus_median_p90": float(
                        part["unbounded_max_minus_median"].quantile(0.90)
                    ),
                }
            )
    extreme_columns = [
        "date",
        "price_close",
        "pb_aggregate",
        "erp",
        "trailing_dividend_contribution",
        "unbounded_pb_pressure_knot",
        "unbounded_erp_pressure_knot",
        "unbounded_dividend_pressure_knot",
        "unbounded_mean_knot",
        "unbounded_median_knot",
        "unbounded_mean_minus_median",
        "unbounded_max_minus_median",
    ]
    extremes = monthly.assign(
        absolute_mean_median_gap=monthly["unbounded_mean_minus_median"].abs()
    ).nlargest(20, "absolute_mean_median_gap")
    return pd.DataFrame(summary_rows), extremes[
        [*extreme_columns, "absolute_mean_median_gap"]
    ]


def make_price_context(daily: pd.DataFrame) -> pd.DataFrame:
    anchor = daily["date"].max()
    rows = []
    for segment, boundary in window_boundaries(anchor).items():
        part = daily if boundary is None else daily[daily["date"] >= boundary]
        rows.append({"segment": segment, **price_metrics(part)})
    return pd.DataFrame(rows)


def make_threshold_scan(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    selection: pd.DataFrame,
    price_context: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor = daily["date"].max()
    boundaries = window_boundaries(anchor)
    selection_index = selection.set_index("threshold")
    rows = []
    for family in FAMILIES:
        score_column = f"{family}_knot"
        for threshold in THRESHOLDS:
            selected = selection_index.loc[threshold]
            family_label = family.removeprefix("unbounded_")
            candidate = f"{family}_ge_{round(threshold * 100):03d}"
            for context in price_context.itertuples(index=False):
                boundary = boundaries[context.segment]
                daily_part = (
                    daily if boundary is None else daily[daily["date"] >= boundary]
                )
                monthly_part = (
                    monthly
                    if boundary is None
                    else monthly[monthly["date"] >= boundary]
                )
                month_active = monthly_part[score_column].ge(threshold)
                episodes, longest = episode_stats(month_active)
                rows.append(
                    {
                        "candidate": candidate,
                        "segment": context.segment,
                        "start": context.start,
                        "end": context.end,
                        "rows": context.rows,
                        "ann_return": context.ann_return,
                        "ann_vol": context.ann_vol,
                        "sharpe_repo": context.sharpe_repo,
                        "max_dd": context.max_dd,
                        "family": family,
                        "threshold": float(threshold),
                        "activation_day_ratio": float(
                            daily_part[score_column].ge(threshold).mean()
                        ),
                        "activation_month_ratio": float(month_active.mean()),
                        "monthly_episodes": episodes,
                        "longest_active_months": longest,
                        "family_core_pass": bool(selected[f"{family_label}_core_pass"]),
                        "joint_all_gates_pass": bool(
                            selected["all_individual_gates_pass"]
                        ),
                        "in_selected_joint_band": bool(selected["in_selected_band"]),
                        "is_joint_design_center": bool(selected["is_design_center"]),
                        "metric_semantics": (
                            "underlying_price_index_context_only_no_strategy_return"
                        ),
                    }
                )
    long = pd.DataFrame(rows)
    wide_rows = []
    for candidate, part in long.groupby("candidate", sort=False):
        first = part.iloc[0]
        row: dict[str, object] = {
            "candidate": candidate,
            "family": first["family"],
            "threshold": first["threshold"],
            "family_core_pass": first["family_core_pass"],
            "joint_all_gates_pass": first["joint_all_gates_pass"],
            "in_selected_joint_band": first["in_selected_joint_band"],
            "is_joint_design_center": first["is_joint_design_center"],
        }
        for item in part.itertuples(index=False):
            for metric in (
                "ann_return",
                "ann_vol",
                "sharpe_repo",
                "max_dd",
                "activation_day_ratio",
                "activation_month_ratio",
                "monthly_episodes",
                "longest_active_months",
            ):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def make_current_state(
    daily: pd.DataFrame, decision: dict[str, Any], threshold_map: pd.DataFrame
) -> pd.DataFrame:
    current = daily.iloc[-1]
    center = decision["design_center_threshold"]
    if center is None:
        raw = {
            "pb_at_least": math.nan,
            "erp_at_most": math.nan,
            "dividend_at_most": math.nan,
        }
    else:
        raw = threshold_map[np.isclose(threshold_map["threshold"], float(center))].iloc[
            0
        ]
    return pd.DataFrame(
        [
            {
                "date": current["date"].date().isoformat(),
                "pb_aggregate": float(current["pb_aggregate"]),
                "erp": float(current["erp"]),
                "trailing_dividend_contribution": float(
                    current["trailing_dividend_contribution"]
                ),
                "unbounded_pb_pressure_knot": float(
                    current["unbounded_pb_pressure_knot"]
                ),
                "unbounded_erp_pressure_knot": float(
                    current["unbounded_erp_pressure_knot"]
                ),
                "unbounded_dividend_pressure_knot": float(
                    current["unbounded_dividend_pressure_knot"]
                ),
                "unbounded_mean_knot": float(current["unbounded_mean_knot"]),
                "unbounded_median_knot": float(current["unbounded_median_knot"]),
                "unbounded_mean_mid": float(current["unbounded_mean_mid"]),
                "unbounded_median_mid": float(current["unbounded_median_mid"]),
                "old_fixed_risk": float(current["old_fixed_risk"]),
                "design_center_threshold": center,
                "mean_design_center_active": bool(
                    center is not None
                    and current["unbounded_mean_knot"] >= float(center)
                ),
                "median_design_center_active": bool(
                    center is not None
                    and current["unbounded_median_knot"] >= float(center)
                ),
                "center_pb_at_least": raw["pb_at_least"],
                "center_erp_at_most": raw["erp_at_most"],
                "center_dividend_at_most": raw["dividend_at_most"],
            }
        ]
    )


def setup_font() -> None:
    if FONT_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_PATH))
        plt.rcParams["font.family"] = font_manager.FontProperties(
            fname=str(FONT_PATH)
        ).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def plot_threshold_structure(
    selection: pd.DataFrame, decision: dict[str, Any], output: Path
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    for prefix, label in (("mean", "三项均值"), ("median", "二取三中位数")):
        axes[0].plot(
            selection["threshold"],
            selection[f"{prefix}_recent10_monthly_activation"] * 100,
            marker="o",
            label=label,
        )
        axes[1].plot(
            selection["threshold"],
            selection[f"{prefix}_recent10_monthly_episodes"],
            marker="o",
            label=label,
        )
    axes[0].axhspan(5, 30, color="#70AD47", alpha=0.12)
    axes[0].set_title("最近10年月度覆盖率")
    axes[0].set_ylabel("覆盖率（%）")
    axes[1].axhline(2, color="#C00000", linestyle="--", alpha=0.7)
    axes[1].set_title("最近10年独立启动段")
    axes[1].set_ylabel("段数")
    for ax in axes:
        ax.set_xlabel("固定经济压力阈值")
        ax.grid(alpha=0.2)
        ax.legend(frameon=False)
        if decision["platform_found"]:
            ax.axvspan(
                decision["selected_band_low"],
                decision["selected_band_high"],
                color="#FFC000",
                alpha=0.18,
            )
            ax.axvline(
                decision["design_center_threshold"], color="#C00000", linestyle=":"
            )
    fig.suptitle("中证500无界固定估值：均值与二取三结构扫描", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "threshold_structure.png", dpi=180)
    plt.close(fig)


def plot_joint_confirmation(selection: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    axes[0].plot(
        selection["threshold"],
        selection["full_monthly_mean_median_jaccard"],
        marker="o",
        label="全样本",
    )
    axes[0].plot(
        selection["threshold"],
        selection["recent10_monthly_mean_median_jaccard"],
        marker="o",
        label="最近10年",
    )
    axes[0].axhline(0.70, color="#C00000", linestyle="--")
    axes[0].set_ylabel("Jaccard")
    axes[0].set_title("均值与二取三状态重合")
    axes[0].legend(frameon=False)
    axes[1].plot(
        selection["threshold"],
        selection["recent10_activation_abs_diff"] * 100,
        marker="o",
    )
    axes[1].axhline(10, color="#C00000", linestyle="--")
    axes[1].set_ylabel("覆盖率绝对差（百分点）")
    axes[1].set_title("最近10年覆盖差")
    for ax in axes:
        ax.set_xlabel("固定经济压力阈值")
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output / "joint_confirmation.png", dpi=180)
    plt.close(fig)


def plot_score_history(monthly: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 5.8))
    ax.plot(
        monthly["date"], monthly["unbounded_mean_knot"], label="三项均值", linewidth=2
    )
    ax.plot(
        monthly["date"],
        monthly["unbounded_median_knot"],
        label="二取三中位数",
        alpha=0.9,
    )
    ax.axhline(2.0, color="#A5A5A5", linestyle="--", alpha=0.7, label="旧第二档压力2")
    ax.set_ylabel("无界固定经济压力")
    ax.set_title("中证500无界固定经济估值历史")
    ax.grid(alpha=0.2)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output / "unbounded_score_history.png", dpi=180)
    plt.close(fig)


def plot_raw_threshold_map(threshold_map: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    items = (
        ("pb_at_least", "PB至少", 1.0),
        ("erp_at_most", "ERP至多（%）", 100.0),
        ("dividend_at_most", "股息至多（%）", 100.0),
    )
    for ax, (column, title, scale) in zip(axes, items, strict=True):
        ax.plot(threshold_map["threshold"], threshold_map[column] * scale, linewidth=2)
        ax.set_title(title)
        ax.set_xlabel("二取三压力阈值")
        ax.grid(alpha=0.2)
    fig.suptitle("二取三规则对应的固定原始经济阈值", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "raw_threshold_map.png", dpi=180)
    plt.close(fig)


def plot_mean_median_gap(monthly: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    axes[0].scatter(
        monthly["unbounded_median_knot"],
        monthly["unbounded_mean_knot"],
        c=monthly["date"].dt.year,
        cmap="viridis",
        alpha=0.75,
    )
    limits = [
        float(
            min(
                monthly["unbounded_median_knot"].min(),
                monthly["unbounded_mean_knot"].min(),
            )
        ),
        float(
            max(
                monthly["unbounded_median_knot"].max(),
                monthly["unbounded_mean_knot"].max(),
            )
        ),
    ]
    axes[0].plot(limits, limits, color="#C00000", linestyle="--")
    axes[0].set_xlabel("二取三中位压力")
    axes[0].set_ylabel("三项平均压力")
    axes[0].set_title("均值是否被单因子拉动")
    axes[1].plot(monthly["date"], monthly["unbounded_mean_minus_median"], linewidth=1.8)
    axes[1].axhline(0, color="#A5A5A5", linestyle="--")
    axes[1].set_ylabel("均值 - 中位数")
    axes[1].set_title("均值—中位数差的历史")
    for ax in axes:
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output / "mean_median_gap.png", dpi=180)
    plt.close(fig)


def format_platforms(items: list[dict[str, float | int]]) -> str:
    if not items:
        return "无"
    return "；".join(
        f"{float(item['low']):.2f}—{float(item['high']):.2f}（{int(item['points'])}点）"
        for item in items
    )


def make_record(
    selection: pd.DataFrame,
    decision: dict[str, Any],
    current: pd.DataFrame,
    threshold_map: pd.DataFrame,
    parity: pd.DataFrame,
    gap_summary: pd.DataFrame,
) -> str:
    if decision["platform_found"]:
        center = float(decision["design_center_threshold"])
        center_row = selection[np.isclose(selection["threshold"], center)].iloc[0]
        raw = threshold_map[np.isclose(threshold_map["threshold"], center)].iloc[0]
        platform_text = (
            f"均值与二取三共同确认的平台为 **{decision['selected_band_low']:.2f}—"
            f"{decision['selected_band_high']:.2f}**，共{decision['selected_band_points']}个点；"
            f"机械设计中心为 **{center:.2f}**。"
        )
        center_text = f"""
### 共同设计中心 {center:.2f}

- 最近10年月度覆盖：均值{center_row.mean_recent10_monthly_activation:.2%}，二取三{center_row.median_recent10_monthly_activation:.2%}；
- 全样本/最近10年启动段：均值{int(center_row.mean_full_monthly_episodes)}/{int(center_row.mean_recent10_monthly_episodes)}，二取三{int(center_row.median_full_monthly_episodes)}/{int(center_row.median_recent10_monthly_episodes)}；
- 全样本/最近10年Jaccard：{center_row.full_monthly_mean_median_jaccard:.2%}/{center_row.recent10_monthly_mean_median_jaccard:.2%}；
- 二取三原始条件为PB不低于{raw.pb_at_least:.2f}、ERP不高于{raw.erp_at_most:.2%}、股息贡献不高于{raw.dividend_at_most:.2%}，三项至少满足两项。
"""
    else:
        platform_text = "没有至少3个相邻阈值同时通过均值、二取三及共同状态确认，因此本版不选择固定高估门槛。"
        center_text = ""
    table_rows = [
        f"| {row.threshold:.2f} | {row.mean_recent10_monthly_activation:.2%} | "
        f"{row.median_recent10_monthly_activation:.2%} | "
        f"{int(row.mean_recent10_monthly_episodes)}/{int(row.median_recent10_monthly_episodes)} | "
        f"{row.recent10_monthly_mean_median_jaccard:.2%} | "
        f"{'是' if row.mean_core_pass else '否'} | {'是' if row.median_core_pass else '否'} | "
        f"{'是' if row.all_individual_gates_pass else '否'} |"
        for row in selection.itertuples(index=False)
    ]
    current_row = current.iloc[0]
    gap = gap_summary[
        (gap_summary["frequency"] == "monthly") & (gap_summary["scope"] == "full")
    ].iloc[0]
    return f"""# 中证500固定经济单位无界估值 v6

## 结论

{platform_text}

均值家族独立平台：{format_platforms(decision["mean_independent_platforms"])}。

二取三家族独立平台：{format_platforms(decision["median_independent_platforms"])}。

本版没有查看策略收益、IC贴水或PUT损益；年化收益和最大回撤字段只是中证500价格指数的同窗背景。

{center_text}

## 全网格

| 阈值 | 均值近10年覆盖 | 二取三近10年覆盖 | 近10年段数 均值/二取三 | 近10年Jaccard | 均值核心 | 二取三核心 | 共同全通过 |
| ---: | ---: | ---: | ---: | ---: | :---: | :---: | :---: |
{chr(10).join(table_rows)}

## 当前状态（{current_row.date}）

- PB {current_row.pb_aggregate:.2f}、ERP {current_row.erp:.2%}、过去一年股息贡献 {current_row.trailing_dividend_contribution:.2%}；
- 无界固定压力：PB {current_row.unbounded_pb_pressure_knot:.3f}、ERP {current_row.unbounded_erp_pressure_knot:.3f}、股息 {current_row.unbounded_dividend_pressure_knot:.3f}；
- 三项均值：**{current_row.unbounded_mean_knot:.3f}**；二取三中位数：**{current_row.unbounded_median_knot:.3f}**；旧离散固定分：{current_row.old_fixed_risk:.2f}；
- 边界中点口径的均值/中位数为{current_row.unbounded_mean_mid:.3f}/{current_row.unbounded_median_mid:.3f}，与整数锚点严格相差0.50；
- 是否达到共同设计中心：均值{"是" if current_row.mean_design_center_active else "否"}、二取三{"是" if current_row.median_design_center_active else "否"}。

## 单因子影响与奇偶

- 全样本月度均值与中位数相关系数为{gap.mean_median_correlation:.4f}；绝对差中位数{gap.absolute_gap_median:.3f}，90%分位{gap.absolute_gap_p90:.3f}，最大{gap.absolute_gap_max:.3f}；
- v5全部日/月、整数/中点、PB/ERP/股息裁剪奇偶最大误差为{parity.max_abs_error.max():.2e}；
- 中位数规则就是原始经济条件至少二取三，不依赖历史分位或覆盖率匹配。

## 数据和边界

- 真实输入为冻结v5正式日/月CSV，样本2007-01-15—2026-08-17；价格是中证500价格指数，不是全收益指数；
- 本层没有交易、持仓、手续费、滑点、保证金或执行时点，因此不报告策略收益；
- 当前结论只属于估值本体研究，状态为`RESEARCH_ONLY_NOT_LIVE_APPROVED`。
"""


def make_scan_record(decision: dict[str, Any]) -> str:
    decision_text = (
        f"joint structural band {decision['selected_band_low']:.2f}-"
        f"{decision['selected_band_high']:.2f}; center "
        f"{decision['design_center_threshold']:.2f}; research only"
        if decision["platform_found"]
        else "no joint structural platform; keep no threshold"
    )
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `{SCAN.name}`
- Date/timezone: 2026-08-18, Asia/Shanghai
- Project: 中证500固定经济单位无界估值
- Version: `{VERSION}`
- Subsystem: valuation body
- Scan type: outcome-free fixed-unit mean/median threshold scan
- Entrypoint: `ic_fixed_valuation_unbounded_score_v6.py`
- Source-change rule: `research_only_no_source_change`

## Research Question

- Baseline: v5 capped score is formula/coverage diagnostic only; no live default exists.
- Candidate grid: unbounded mean and unbounded median, 1.50-3.00 by 0.05; 62 candidates.
- Decision target: a three-point-or-wider jointly confirmed structural platform.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.
- Strategy outcomes are prohibited from threshold selection.

## Implementation Anchor and Data Snapshot

- Frozen v5 daily/monthly formal CSV inputs.
- CSI500 price-index and valuation sample: 2007-01-15 to 2026-08-17; 4,761 daily and 236 month-end rows.
- No warmup, cache write, vendor refresh, randomness, or future-row merge.
- v5 clipped-score parity and 11 historical-vintage formula invariance are mandatory.

## Cost and Execution Assumptions

- No trades, fills, positions, leverage, hedge, commission, slippage, financing, or borrow cost.
- Price-index return fields are identical context values within each window and cannot rank candidates.

## Commands

```powershell
uv run ic_fixed_valuation_unbounded_score_v6.py
uv run --with pytest --with numpy --with pandas --with matplotlib pytest -q test_ic_fixed_valuation_unbounded_score_v6.py
```

## Output Files

- `scan_summary.csv`: 310 long rows.
- `window_metrics.csv`: 62 wide rows.
- `scan_meta.json`, `record.md`, `command_log.txt`.

## Stability Classification

- Mean independent platforms: {format_platforms(decision["mean_independent_platforms"])}.
- Median independent platforms: {format_platforms(decision["median_independent_platforms"])}.
- Final stability: `{"joint_structural_platform" if decision["platform_found"] else "no_joint_structural_platform"}`.

## Decision

- Decision: {decision_text}.
- No production source, strategy threshold, or live signal is promoted.

## User-Facing Summary

The scan only defines a valuation state. Full strategy return/drawdown comparisons are intentionally not part of this layer.
"""


def write_scan_files(
    long: pd.DataFrame,
    wide: pd.DataFrame,
    decision: dict[str, Any],
    input_hashes: dict[str, str],
) -> None:
    long.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "candidate_count": int(long["candidate"].nunique()),
            "window_rows": len(long),
            "parity_check": {
                "v5_clipped_formula_required": True,
                "historical_vintage_invariance_required": True,
            },
            "source_hashes": input_hashes,
            "runtime_override_plan": "pure immutable vector comparisons; no source mutation",
            "decision_detail": decision,
            "warnings": [
                "Underlying price metrics are context only and not strategy returns.",
                "No live or production default threshold exists.",
            ],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SCAN / "record.md").write_text(make_scan_record(decision), encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nformal_scan_command=uv run ic_fixed_valuation_unbounded_score_v6.py\n"
            "working_directory=D:\\动量策略\\新策略研究\n"
            "cache_writes=none\n"
        )


def main() -> None:
    input_hashes = verify_inputs()
    monthly = add_unbounded_scores(pd.read_csv(MONTHLY_INPUT, parse_dates=["date"]))
    daily = add_unbounded_scores(pd.read_csv(DAILY_INPUT, parse_dates=["date"]))
    required = ["price_close", "pb_aggregate", "erp", "trailing_dividend_contribution"]
    if len(monthly) != 236 or len(daily) != 4_761:
        raise RuntimeError("Unexpected sample size")
    if monthly[required].isna().any().any() or daily[required].isna().any().any():
        raise RuntimeError("Missing values in fixed-score inputs")
    if (
        not monthly["date"].is_monotonic_increasing
        or not daily["date"].is_monotonic_increasing
    ):
        raise RuntimeError("Input dates are not ordered")

    shift_errors = {}
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        for name in (*PRESSURE_NAMES, "mean", "median"):
            knot = (
                frame[f"unbounded_{name}_pressure_knot"]
                if name in PRESSURE_NAMES
                else frame[f"unbounded_{name}_knot"]
            )
            mid = (
                frame[f"unbounded_{name}_pressure_mid"]
                if name in PRESSURE_NAMES
                else frame[f"unbounded_{name}_mid"]
            )
            shift_errors[f"{frequency}_{name}"] = float(
                np.max(np.abs((knot - mid) - 0.50))
            )
    if max(shift_errors.values()) > 1e-12:
        raise RuntimeError(f"Knot/mid shift parity failed: {shift_errors}")

    threshold_map = raw_threshold_map()
    economic = make_economic_boundary(monthly)
    selection, decision = make_threshold_selection(monthly, economic)
    parity, overlap = make_v5_parity(daily, monthly)
    vintage = make_vintage_invariance(monthly)
    gap_summary, gap_extremes = make_gap_diagnostics(daily, monthly)
    price_context = make_price_context(daily)
    scan_long, scan_wide = make_threshold_scan(daily, monthly, selection, price_context)
    current = make_current_state(daily, decision, threshold_map)

    if len(threshold_map) != 31 or len(economic) != 62 or len(selection) != 31:
        raise RuntimeError("Structural grid size mismatch")
    if scan_long["candidate"].nunique() != 62 or len(scan_long) != 310:
        raise RuntimeError("Scan grid size mismatch")
    if parity["max_abs_error"].max() > 1e-12:
        raise RuntimeError("v5 clipped formula parity failed")
    vintage_error_columns = [
        column for column in vintage if column.endswith("max_abs_error")
    ]
    if vintage[vintage_error_columns].max().max() > 1e-12:
        raise RuntimeError("Historical fixed-score invariance failed")
    if (
        not vintage["all_family_threshold_states_match"].all()
        or vintage["future_rows_used"].any()
    ):
        raise RuntimeError("Historical threshold-state invariance failed")
    if not scan_long.groupby("segment")["ann_return"].nunique().eq(1).all():
        raise RuntimeError("Candidate price context differs within a window")

    STAGING.mkdir(parents=True)
    monthly.to_csv(STAGING / "monthly_unbounded_fixed_scores.csv", index=False)
    daily.to_csv(
        STAGING / "daily_unbounded_fixed_scores.csv.gz",
        index=False,
        compression="gzip",
    )
    threshold_map.to_csv(STAGING / "raw_two_of_three_threshold_map.csv", index=False)
    economic.to_csv(STAGING / "economic_boundary_map.csv", index=False)
    selection.to_csv(STAGING / "threshold_selection.csv", index=False)
    parity.to_csv(STAGING / "v5_clipped_formula_parity.csv", index=False)
    overlap.to_csv(STAGING / "v5_overlap_coverage_diagnostic.csv", index=False)
    vintage.to_csv(STAGING / "vintage_formula_invariance.csv", index=False)
    gap_summary.to_csv(STAGING / "mean_median_gap_summary.csv", index=False)
    gap_extremes.to_csv(STAGING / "mean_median_extreme_months.csv", index=False)
    current.to_csv(STAGING / "current_unbounded_state.csv", index=False)
    price_context.to_csv(STAGING / "underlying_price_context.csv", index=False)
    scan_long.to_csv(STAGING / "threshold_scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "threshold_window_metrics.csv", index=False)

    setup_font()
    plot_threshold_structure(selection, decision, STAGING)
    plot_joint_confirmation(selection, STAGING)
    plot_score_history(monthly, STAGING)
    plot_raw_threshold_map(threshold_map, STAGING)
    plot_mean_median_gap(monthly, STAGING)
    (STAGING / "record.md").write_text(
        make_record(selection, decision, current, threshold_map, parity, gap_summary),
        encoding="utf-8",
    )

    integrity = {
        "status": "passed",
        "version": VERSION,
        "research_only_not_live_approved": True,
        "strategy_returns_used_for_selection": False,
        "input_hashes": input_hashes,
        "spec_hash": sha256(SPEC),
        "sample": {
            "daily_rows": len(daily),
            "monthly_rows": len(monthly),
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "timezone": "Asia/Shanghai",
            "underlying": "CSI500 price index context",
        },
        "candidate_count": int(scan_long["candidate"].nunique()),
        "scan_rows": len(scan_long),
        "threshold_count": len(selection),
        "economic_rows": len(economic),
        "v5_parity_max_abs_error": float(parity["max_abs_error"].max()),
        "knot_mid_shift_max_abs_error": float(max(shift_errors.values())),
        "fixed_formula_vintage_max_abs_error": float(
            vintage[vintage_error_columns].max().max()
        ),
        "all_vintage_threshold_states_match": bool(
            vintage["all_family_threshold_states_match"].all()
        ),
        "vintage_no_future_rows": bool(~vintage["future_rows_used"].any()),
        "price_context_identical_within_window": True,
        "decision": decision,
    }
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        path.name: sha256(path)
        for path in sorted(STAGING.iterdir())
        if path.is_file() and path.name != "output_manifest.json"
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_scan_files(scan_long, scan_wide, decision, input_hashes)
    shutil.move(str(STAGING), str(OUTPUT))
    print(json.dumps(integrity, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
