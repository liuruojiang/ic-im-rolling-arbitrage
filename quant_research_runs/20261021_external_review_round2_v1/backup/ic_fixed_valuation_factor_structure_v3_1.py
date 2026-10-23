from __future__ import annotations

import hashlib
import json
import math
import shutil
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

from ic_fixed_valuation_weighted_distribution_v2 import (
    episode_stats,
    jaccard,
    price_metrics,
    weighted_corr,
    weighted_risk_mapper,
)


ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_factor_structure_v3_1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V1_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_time_weight_v1"
V2_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_weighted_distribution_v2"
V3_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_factor_structure_v3"
MONTHLY_INPUT = V1_OUTPUT / "monthly_weight_map.csv"
DAILY_INPUT = V1_OUTPUT / "daily_weight_map.csv.gz"
V2_MONTHLY = V2_OUTPUT / "monthly_continuous_scores.csv"
V2_DAILY = V2_OUTPUT / "daily_continuous_scores.csv.gz"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_factor_structure_v3_1_valuation_body_factor_structure_half_life_threshold"
)
EXPECTED_HASHES = {
    MONTHLY_INPUT: "3ff4a866e090722cd23666f65af38d0d397bf627454491bfe9e55a554cc01e0a",
    DAILY_INPUT: "244676065643b97419fe1b84aa32bff3a05a340818956f30029c035013f02102",
    V1_OUTPUT / "integrity_checks.json": "4ed3d23a4ebdede56ea1a656b8025dc080aeb972f86a5a630c60a498cf92f205",
    ROOT / "docs" / "ic_fixed_valuation_time_weight_v1_spec.md": "d062b0e7d9c4a60d55c4e1d4cb0505d672fab6037048f5c9a49ca6b22862194d",
    ROOT / "docs" / "ic_fixed_valuation_time_weight_v1_postrun_audit.md": "b53abf714816f5dd6fcde6c8784e7bd23ff55ebdf6b16c3963d01d0bf7ccd71c",
    ROOT / "ic_fixed_valuation_weighted_distribution_v2.py": "5b34965ef99e076a3cb42e10db74c8f4b9fe13da13b4157d0f5b7229c9d1ce5c",
    V2_MONTHLY: "485d6e29d29c878bbdd643800276107fa743097c0fb204fc3823d6f707e7d175",
    V2_DAILY: "75424efca5e6f66750a48c17c167be70b0f182f9e09a11cf2cf8bed02ea2e44c",
    V2_OUTPUT / "integrity_checks.json": "9f505d7d8004f034dd76336521bfc75487f2d2b93bccc98ccaace3a0e45af152",
    ROOT / "docs" / "ic_fixed_valuation_weighted_distribution_v2_spec.md": "cbb7ed0bbc1d9262fd429d136e9a132dc38f20cbee4f6a2f862f3d52e31a39ae",
    ROOT / "docs" / "ic_fixed_valuation_weighted_distribution_v2_postrun_audit.md": "8e46800ea5a859b5eee80aa35d4f1c7ef83d5b88691a63a103ae1d6552541498",
    ROOT / "docs" / "ic_fixed_valuation_factor_structure_v3_spec.md": "ea3095a489ea03d7021730cf931ace31b44b6b2435131ea3f18486c93a52cf6a",
}
HALF_LIVES = {"hl07p5": 7.5, "hl10p0": 10.0, "hl12p0": 12.0}
WEIGHT_COLUMNS = {
    "hl07p5": "weight__exp_hl07p5",
    "hl10p0": "weight__exp_hl10p0",
    "hl12p0": "weight__exp_hl12p0",
}
FACTOR_FIELDS = {
    "pb": ("pb_aggregate", True),
    "erp": ("erp", False),
    "dividend": ("trailing_dividend_contribution", False),
    "earnings_yield": ("earnings_yield", False),
    "gov10y": ("gov10y_yield", True),
}
STRUCTURES = (
    "legacy_255025",
    "equal3",
    "pb_erp_equal",
    "erp_split_equal4",
    "pc1_three",
)
STRUCTURE_LABELS = {
    "legacy_255025": "旧25/50/25",
    "equal3": "三因子等权",
    "pb_erp_equal": "PB+ERP等权",
    "erp_split_equal4": "拆ERP四因子等权",
    "pc1_three": "三因子PC1",
}
THRESHOLDS = np.round(np.arange(0.600, 0.900 + 0.0001, 0.025), 3)
PROBE_THRESHOLDS = (0.70, 0.75, 0.80)
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    expected_spec = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sha256(SPEC) != expected_spec:
        raise RuntimeError("Frozen v3 specification mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if STAGING.exists():
        raise FileExistsError(f"Staging output already exists: {STAGING}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {SCAN}")
    for path, expected in EXPECTED_HASHES.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen input mismatch: {path}: {actual} != {expected}")
    for path in (V1_OUTPUT / "integrity_checks.json", V2_OUTPUT / "integrity_checks.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("status") != "passed":
            raise RuntimeError(f"Upstream integrity failed: {path}")
    return {str(path.relative_to(ROOT)): sha256(path) for path in EXPECTED_HASHES}


def verify_output_manifest(output: Path) -> None:
    manifest_path = output / "output_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        actual = sha256(output / name)
        if actual != expected:
            raise RuntimeError(f"Prior formal output changed: {output / name}")


def prepare_base(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["date"])
    frame["earnings_yield"] = 1.0 / frame["pe_aggregate_ttm"]
    return frame


def component_col(factor: str, half_label: str) -> str:
    return f"risk_component__{factor}__{half_label}"


def raw_col(structure: str, half_label: str) -> str:
    return f"raw_score__{structure}__{half_label}"


def norm_col(structure: str, half_label: str) -> str:
    return f"normalized_risk__{structure}__{half_label}"


def weighted_pca_fit(
    calibration: pd.DataFrame, columns: list[str], weights: pd.Series
) -> dict[str, np.ndarray | float]:
    values = calibration[columns].to_numpy(dtype=float)
    normalized = weights.to_numpy(dtype=float)
    normalized = normalized / normalized.sum()
    means = np.sum(values * normalized[:, None], axis=0)
    centered = values - means
    variances = np.sum(normalized[:, None] * centered**2, axis=0)
    scales = np.sqrt(variances)
    standardized = centered / scales
    correlation = (standardized * normalized[:, None]).T @ standardized
    eigenvalues, eigenvectors = np.linalg.eigh(correlation)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    loadings = eigenvectors[:, order[0]]
    if float(loadings.sum()) < 0:
        loadings = -loadings
    return {
        "means": means,
        "scales": scales,
        "loadings": loadings,
        "eigenvalues": eigenvalues,
        "explained_ratio": float(eigenvalues[0] / eigenvalues.sum()),
        "effective_dimension": float(eigenvalues.sum() ** 2 / np.sum(eigenvalues**2)),
    }


def weighted_pca_project(
    frame: pd.DataFrame, columns: list[str], fit: dict[str, np.ndarray | float]
) -> np.ndarray:
    values = frame[columns].to_numpy(dtype=float)
    return ((values - fit["means"]) / fit["scales"]) @ fit["loadings"]


def add_linear_structures(frame: pd.DataFrame, half_label: str) -> None:
    pb = frame[component_col("pb", half_label)]
    erp = frame[component_col("erp", half_label)]
    dividend = frame[component_col("dividend", half_label)]
    earnings_yield = frame[component_col("earnings_yield", half_label)]
    gov10y = frame[component_col("gov10y", half_label)]
    frame[raw_col("legacy_255025", half_label)] = 0.25 * pb + 0.50 * erp + 0.25 * dividend
    frame[raw_col("equal3", half_label)] = (pb + erp + dividend) / 3.0
    frame[raw_col("pb_erp_equal", half_label)] = (pb + erp) / 2.0
    frame[raw_col("erp_split_equal4", half_label)] = (
        pb + earnings_yield + gov10y + dividend
    ) / 4.0


def score_one_half(
    calibration: pd.DataFrame,
    targets: dict[str, pd.DataFrame],
    weights: pd.Series,
    half_label: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, object], pd.DataFrame]:
    cal = calibration.copy()
    results = {name: frame.copy() for name, frame in targets.items()}
    for factor, (field, high_is_risk) in FACTOR_FIELDS.items():
        mapper = weighted_risk_mapper(calibration[field], weights, high_is_risk)
        column = component_col(factor, half_label)
        cal[column] = mapper(calibration[field])
        for frame in results.values():
            frame[column] = mapper(frame[field])
    add_linear_structures(cal, half_label)
    for frame in results.values():
        add_linear_structures(frame, half_label)

    pca_columns = [
        component_col("pb", half_label),
        component_col("erp", half_label),
        component_col("dividend", half_label),
    ]
    fit = weighted_pca_fit(cal, pca_columns, weights)
    pc_column = raw_col("pc1_three", half_label)
    cal[pc_column] = weighted_pca_project(cal, pca_columns, fit)
    for frame in results.values():
        frame[pc_column] = weighted_pca_project(frame, pca_columns, fit)

    for structure in STRUCTURES:
        mapper = weighted_risk_mapper(cal[raw_col(structure, half_label)], weights, True)
        column = norm_col(structure, half_label)
        cal[column] = np.clip(
            mapper(cal[raw_col(structure, half_label)]), 0.0, 1.0
        )
        for frame in results.values():
            frame[column] = np.clip(
                mapper(frame[raw_col(structure, half_label)]), 0.0, 1.0
            )

    # Preserve the frozen v3 calculations exactly, then clamp only the serialized
    # component boundary values.  The affected excess is at most machine epsilon.
    for factor in FACTOR_FIELDS:
        column = component_col(factor, half_label)
        cal[column] = np.clip(cal[column], 0.0, 1.0)
        for frame in results.values():
            frame[column] = np.clip(frame[column], 0.0, 1.0)

    loadings = np.asarray(fit["loadings"], dtype=float)
    eigenvalues = np.asarray(fit["eigenvalues"], dtype=float)
    diagnostic = {
        "half_label": half_label,
        "half_life_years": HALF_LIVES.get(half_label, 10.0),
        "pc1_explained_ratio": fit["explained_ratio"],
        "effective_dimension_three": fit["effective_dimension"],
        "loading_pb": loadings[0],
        "loading_erp": loadings[1],
        "loading_dividend": loadings[2],
        "eigenvalue_1": eigenvalues[0],
        "eigenvalue_2": eigenvalues[1],
        "eigenvalue_3": eigenvalues[2],
        "all_loadings_positive": bool((loadings > 0).all()),
        "equal3_pc1_spearman": float(
            cal[[norm_col("equal3", half_label), norm_col("pc1_three", half_label)]]
            .corr(method="spearman")
            .iloc[0, 1]
        ),
        "equal3_pc1_pearson": float(
            cal[[norm_col("equal3", half_label), norm_col("pc1_three", half_label)]]
            .corr(method="pearson")
            .iloc[0, 1]
        ),
    }
    return results, diagnostic, cal


def build_scores(
    monthly: pd.DataFrame, daily: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    scored_monthly = monthly.copy()
    scored_daily = daily.copy()
    diagnostics: list[dict[str, object]] = []
    correlation_rows: list[dict[str, object]] = []
    for half_label, weight_column in WEIGHT_COLUMNS.items():
        results, diagnostic, cal = score_one_half(
            monthly,
            {"monthly": scored_monthly, "daily": scored_daily},
            monthly[weight_column],
            half_label,
        )
        scored_monthly = results["monthly"]
        scored_daily = results["daily"]
        diagnostics.append(diagnostic)
        factor_columns = [component_col(name, half_label) for name in FACTOR_FIELDS]
        pearson = weighted_corr(cal, factor_columns, monthly[weight_column])
        ranked = cal.copy()
        ranked[factor_columns] = ranked[factor_columns].rank(method="average", pct=True)
        spearman = weighted_corr(ranked, factor_columns, monthly[weight_column])
        for left, right in combinations(FACTOR_FIELDS, 2):
            left_col = component_col(left, half_label)
            right_col = component_col(right, half_label)
            correlation_rows.append(
                {
                    "half_label": half_label,
                    "half_life_years": HALF_LIVES[half_label],
                    "factor_left": left,
                    "factor_right": right,
                    "weighted_pearson": float(pearson.loc[left_col, right_col]),
                    "weighted_rank_correlation": float(spearman.loc[left_col, right_col]),
                }
            )
    return (
        scored_monthly,
        scored_daily,
        pd.DataFrame(diagnostics),
        pd.DataFrame(correlation_rows),
    )


def make_structure_similarity(
    monthly: pd.DataFrame, daily: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for half_label in HALF_LIVES:
        for left, right in combinations(STRUCTURES, 2):
            left_col = norm_col(left, half_label)
            right_col = norm_col(right, half_label)
            pearson = float(monthly[[left_col, right_col]].corr().iloc[0, 1])
            spearman = float(monthly[[left_col, right_col]].corr(method="spearman").iloc[0, 1])
            current_diff = float(abs(daily[left_col].iloc[-1] - daily[right_col].iloc[-1]))
            for threshold in PROBE_THRESHOLDS:
                rows.append(
                    {
                        "half_label": half_label,
                        "half_life_years": HALF_LIVES[half_label],
                        "structure_left": left,
                        "structure_right": right,
                        "threshold": threshold,
                        "pearson": pearson,
                        "spearman": spearman,
                        "jaccard": jaccard(
                            monthly[left_col] >= threshold,
                            monthly[right_col] >= threshold,
                        ),
                        "current_absolute_difference": current_diff,
                    }
                )
    return pd.DataFrame(rows)


def make_time_robustness(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for structure in STRUCTURES:
        center_col = norm_col(structure, "hl10p0")
        for comparison in ("hl07p5", "hl12p0"):
            comparison_col = norm_col(structure, comparison)
            pearson = float(monthly[[center_col, comparison_col]].corr().iloc[0, 1])
            spearman = float(
                monthly[[center_col, comparison_col]].corr(method="spearman").iloc[0, 1]
            )
            for threshold in PROBE_THRESHOLDS:
                rows.append(
                    {
                        "structure": structure,
                        "center_half_label": "hl10p0",
                        "comparison_half_label": comparison,
                        "threshold": threshold,
                        "pearson": pearson,
                        "spearman": spearman,
                        "jaccard": jaccard(
                            monthly[center_col] >= threshold,
                            monthly[comparison_col] >= threshold,
                        ),
                    }
                )
    return pd.DataFrame(rows)


def window_boundaries(anchor: pd.Timestamp) -> dict[str, pd.Timestamp | None]:
    return {
        "full": None,
        "last_10y": anchor - pd.DateOffset(years=10),
        "last_5y": anchor - pd.DateOffset(years=5),
        "last_3y": anchor - pd.DateOffset(years=3),
        "last_1y": anchor - pd.DateOffset(years=1),
    }


def make_price_context(daily: pd.DataFrame) -> pd.DataFrame:
    anchor = pd.Timestamp(daily["date"].max())
    rows = []
    for segment, boundary in window_boundaries(anchor).items():
        part = daily if boundary is None else daily[daily["date"] >= boundary]
        rows.append({"segment": segment, **price_metrics(part)})
    return pd.DataFrame(rows)


def make_threshold_scan(
    daily: pd.DataFrame, monthly: pd.DataFrame, price_context: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor = pd.Timestamp(daily["date"].max())
    boundaries = window_boundaries(anchor)
    rows: list[dict[str, object]] = []
    for structure in STRUCTURES:
        for half_label, half_life in HALF_LIVES.items():
            column = norm_col(structure, half_label)
            for threshold in THRESHOLDS:
                candidate = f"{structure}__{half_label}__risk_ge_{int(round(threshold * 1000)):03d}"
                full_active = monthly[column] >= threshold
                episodes, longest = episode_stats(full_active)
                center_active = monthly[norm_col(structure, "hl10p0")] >= threshold
                left_j = jaccard(
                    monthly[norm_col(structure, "hl07p5")] >= threshold, center_active
                )
                right_j = jaccard(
                    monthly[norm_col(structure, "hl12p0")] >= threshold, center_active
                )
                for context in price_context.itertuples(index=False):
                    boundary = boundaries[context.segment]
                    daily_part = daily if boundary is None else daily[daily["date"] >= boundary]
                    monthly_part = monthly if boundary is None else monthly[monthly["date"] >= boundary]
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
                            "structure": structure,
                            "half_label": half_label,
                            "half_life_years": half_life,
                            "risk_threshold": float(threshold),
                            "activation_day_ratio": float((daily_part[column] >= threshold).mean()),
                            "activation_month_ratio": float((monthly_part[column] >= threshold).mean()),
                            "monthly_episodes_full": episodes,
                            "longest_active_months_full": longest,
                            "current_active": bool(full_active.iloc[-1]),
                            "jaccard_hl07p5_vs_center": left_j,
                            "jaccard_hl12p0_vs_center": right_j,
                            "metric_semantics": "underlying_price_index_context_only_no_strategy_return",
                        }
                    )
    long = pd.DataFrame(rows)
    wide_rows: list[dict[str, object]] = []
    for candidate, part in long.groupby("candidate", sort=False):
        first = part.iloc[0]
        row: dict[str, object] = {
            "candidate": candidate,
            "structure": first["structure"],
            "half_label": first["half_label"],
            "half_life_years": first["half_life_years"],
            "risk_threshold": first["risk_threshold"],
            "monthly_episodes_full": first["monthly_episodes_full"],
            "longest_active_months_full": first["longest_active_months_full"],
            "current_active": first["current_active"],
            "jaccard_hl07p5_vs_center": first["jaccard_hl07p5_vs_center"],
            "jaccard_hl12p0_vs_center": first["jaccard_hl12p0_vs_center"],
        }
        for item in part.itertuples(index=False):
            row[f"ann_return_{item.segment}"] = item.ann_return
            row[f"ann_vol_{item.segment}"] = item.ann_vol
            row[f"sharpe_repo_{item.segment}"] = item.sharpe_repo
            row[f"max_dd_{item.segment}"] = item.max_dd
            row[f"activation_day_ratio_{item.segment}"] = item.activation_day_ratio
            row[f"activation_month_ratio_{item.segment}"] = item.activation_month_ratio
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def make_current_state(daily: pd.DataFrame) -> pd.DataFrame:
    current = daily.iloc[-1]
    rows: list[dict[str, object]] = []
    for structure in STRUCTURES:
        for half_label, half_life in HALF_LIVES.items():
            normalized = float(current[norm_col(structure, half_label)])
            rows.append(
                {
                    "date": current["date"].date().isoformat(),
                    "structure": structure,
                    "label": STRUCTURE_LABELS[structure],
                    "half_label": half_label,
                    "half_life_years": half_life,
                    "raw_score": float(current[raw_col(structure, half_label)]),
                    "normalized_risk": normalized,
                    "active_ge_070": normalized >= 0.70,
                    "active_ge_075": normalized >= 0.75,
                    "active_ge_080": normalized >= 0.80,
                }
            )
    return pd.DataFrame(rows)


def vintage_dates(monthly: pd.DataFrame) -> list[pd.Timestamp]:
    dates: list[pd.Timestamp] = []
    for year in range(2016, 2026):
        part = monthly[(monthly["date"].dt.year == year) & (monthly["date"].dt.month == 12)]
        if part.empty:
            raise RuntimeError(f"Missing December vintage for {year}")
        dates.append(pd.Timestamp(part["date"].max()))
    dates.append(pd.Timestamp(monthly["date"].max()))
    return dates


def make_vintage_audit(
    monthly: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pca_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    for vintage in vintage_dates(monthly):
        history = monthly[monthly["date"] <= vintage].copy()
        ages = (vintage - history["date"]).dt.days / 365.2425
        weights = pd.Series(np.power(2.0, -ages / 10.0), index=history.index)
        weights = weights / weights.sum()
        results, diagnostic, scored = score_one_half(
            history, {"history": history}, weights, "hl10p0"
        )
        if history["date"].max() > vintage:
            raise RuntimeError("Future row entered vintage audit")
        equal_col = norm_col("equal3", "hl10p0")
        pc_col = norm_col("pc1_three", "hl10p0")
        diagnostic.update(
            {
                "vintage_date": vintage.date().isoformat(),
                "history_start": history["date"].min().date().isoformat(),
                "history_end": history["date"].max().date().isoformat(),
                "history_months": len(history),
                "jaccard_070": jaccard(scored[equal_col] >= 0.70, scored[pc_col] >= 0.70),
                "jaccard_075": jaccard(scored[equal_col] >= 0.75, scored[pc_col] >= 0.75),
                "jaccard_080": jaccard(scored[equal_col] >= 0.80, scored[pc_col] >= 0.80),
            }
        )
        pca_rows.append(diagnostic)
        last = results["history"].iloc[-1]
        for structure in STRUCTURES:
            state_rows.append(
                {
                    "vintage_date": vintage.date().isoformat(),
                    "history_start": history["date"].min().date().isoformat(),
                    "history_end": history["date"].max().date().isoformat(),
                    "history_months": len(history),
                    "structure": structure,
                    "raw_score": float(last[raw_col(structure, "hl10p0")]),
                    "normalized_risk": float(last[norm_col(structure, "hl10p0")]),
                }
            )
    return pd.DataFrame(pca_rows), pd.DataFrame(state_rows)


def selection_decision(
    pca: pd.DataFrame,
    similarity: pd.DataFrame,
    time_robustness: pd.DataFrame,
    current: pd.DataFrame,
) -> dict[str, object]:
    common_cycle = bool(
        pca["pc1_explained_ratio"].ge(0.80).all() and pca["all_loadings_positive"].all()
    )
    proxy_pair = similarity[
        (similarity["structure_left"] == "equal3")
        & (similarity["structure_right"] == "pc1_three")
    ]
    equal_spearman = bool(proxy_pair["spearman"].ge(0.98).all())
    equal_jaccard = bool(proxy_pair["jaccard"].ge(0.85).all())
    equal_current = current[current["structure"] == "equal3"].set_index("half_label")
    pc_current = current[current["structure"] == "pc1_three"].set_index("half_label")
    current_max_diff = float(
        (equal_current["normalized_risk"] - pc_current["normalized_risk"]).abs().max()
    )
    equal_time = time_robustness[time_robustness["structure"] == "equal3"]
    pc_time = time_robustness[time_robustness["structure"] == "pc1_three"]
    equal_time_pass = bool(
        equal_time["spearman"].ge(0.98).all() and equal_time["jaccard"].ge(0.85).all()
    )
    pc_time_pass = bool(
        pc_time["spearman"].ge(0.98).all() and pc_time["jaccard"].ge(0.85).all()
    )
    equal_proxy_pass = bool(
        equal_spearman and equal_jaccard and current_max_diff <= 0.05 and equal_time_pass
    )
    if common_cycle and equal_proxy_pass:
        design_center = "equal3"
        confirmation = "pc1_three"
    elif common_cycle and pc_time_pass:
        design_center = "pc1_three"
        confirmation = "equal3"
    else:
        design_center = "none"
        confirmation = "none"
    return {
        "common_cycle_pass": common_cycle,
        "equal3_pc1_spearman_pass": equal_spearman,
        "equal3_pc1_jaccard_pass": equal_jaccard,
        "equal3_pc1_current_max_abs_diff": current_max_diff,
        "equal3_time_robustness_pass": equal_time_pass,
        "pc1_time_robustness_pass": pc_time_pass,
        "equal3_proxy_pass": equal_proxy_pass,
        "design_center": design_center,
        "confirmation_structure": confirmation,
        "threshold_selected": False,
    }


def setup_font() -> None:
    if FONT_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_PATH))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT_PATH)).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def plot_current(current: pd.DataFrame, output: Path) -> None:
    center = current[current["half_label"] == "hl10p0"].copy()
    fig, ax = plt.subplots(figsize=(11.5, 6.2))
    bars = ax.bar(center["label"], center["normalized_risk"] * 100, color="#4472C4")
    for bar, value in zip(bars, center["normalized_risk"], strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1, f"{value:.1%}", ha="center")
    ax.axhline(70, color="#ED7D31", linestyle="--", label="0.70探针")
    ax.axhline(75, color="#C00000", linestyle="--", label="0.75探针")
    ax.set_ylim(0, 100)
    ax.set_ylabel("10年半衰期标准化风险（%）")
    ax.set_title("2026-08-17：五种估值因子结构的当前状态")
    ax.grid(axis="y", alpha=0.2)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output / "current_factor_structures.png", dpi=180)
    plt.close(fig)


def plot_timeseries(monthly: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    colors = ["#A5A5A5", "#4472C4", "#70AD47", "#ED7D31", "#C00000"]
    for structure, color in zip(STRUCTURES, colors, strict=True):
        ax.plot(
            monthly["date"],
            monthly[norm_col(structure, "hl10p0")],
            label=STRUCTURE_LABELS[structure],
            color=color,
            linewidth=1.8 if structure in {"equal3", "pc1_three"} else 1.1,
            alpha=0.95 if structure in {"equal3", "pc1_three"} else 0.7,
        )
    ax.axhline(0.75, color="#222222", linestyle="--", alpha=0.6)
    ax.set_ylim(0, 1.02)
    ax.set_ylabel("标准化风险")
    ax.set_title("中证500五种估值结构：10年半衰期月度风险路径")
    ax.grid(alpha=0.2)
    ax.legend(ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(output / "factor_structure_timeseries.png", dpi=180)
    plt.close(fig)


def plot_similarity(similarity: pd.DataFrame, output: Path) -> None:
    center = similarity[
        (similarity["half_label"] == "hl10p0") & (similarity["threshold"] == 0.75)
    ]
    matrix = pd.DataFrame(np.eye(len(STRUCTURES)), index=STRUCTURES, columns=STRUCTURES)
    for row in center.itertuples(index=False):
        matrix.loc[row.structure_left, row.structure_right] = row.spearman
        matrix.loc[row.structure_right, row.structure_left] = row.spearman
    fig, ax = plt.subplots(figsize=(8.7, 7.3))
    image = ax.imshow(matrix, cmap="YlOrRd", vmin=0.85, vmax=1.0)
    labels = [STRUCTURE_LABELS[item] for item in STRUCTURES]
    ax.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
    ax.set_yticks(range(len(labels)), labels)
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, f"{matrix.iloc[i, j]:.3f}", ha="center", va="center", fontsize=9)
    fig.colorbar(image, ax=ax, label="月度Spearman")
    ax.set_title("10年半衰期：因子结构风险排序相似度")
    fig.tight_layout()
    fig.savefig(output / "structure_similarity_heatmap.png", dpi=180)
    plt.close(fig)


def plot_pca_vintage(vintage: pd.DataFrame, output: Path) -> None:
    part = vintage.copy()
    part["vintage_date"] = pd.to_datetime(part["vintage_date"])
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    for column, label in (
        ("loading_pb", "PB"),
        ("loading_erp", "ERP"),
        ("loading_dividend", "股息"),
    ):
        axes[0].plot(part["vintage_date"], part[column], marker="o", label=label)
    axes[0].set_title("只用当时历史重算的PC1载荷")
    axes[0].set_ylabel("L2归一载荷")
    axes[0].grid(alpha=0.2)
    axes[0].legend(frameon=False)
    axes[1].plot(part["vintage_date"], part["pc1_explained_ratio"] * 100, marker="o", color="#C00000")
    axes[1].axhline(80, color="#555555", linestyle="--")
    axes[1].set_title("PC1方差解释率")
    axes[1].set_ylabel("解释率（%）")
    axes[1].grid(alpha=0.2)
    fig.suptitle("历史时点因果审计：共同估值周期是否稳定", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "pca_vintage_stability.png", dpi=180)
    plt.close(fig)


def make_record(
    pca: pd.DataFrame,
    similarity: pd.DataFrame,
    time_robustness: pd.DataFrame,
    current: pd.DataFrame,
    vintage_pca: pd.DataFrame,
    factor_corr: pd.DataFrame,
    decision: dict[str, object],
) -> str:
    pca_rows = []
    for row in pca.itertuples(index=False):
        pca_rows.append(
            f"| {row.half_life_years:g}年 | {row.pc1_explained_ratio:.2%} | {row.effective_dimension_three:.2f} | {row.loading_pb:.3f} | {row.loading_erp:.3f} | {row.loading_dividend:.3f} | {row.equal3_pc1_spearman:.4f} |"
        )
    current_rows = []
    center = current[current["half_label"] == "hl10p0"]
    for row in center.itertuples(index=False):
        current_rows.append(
            f"| {row.label} | {row.raw_score:.4f} | {row.normalized_risk:.2%} | {'是' if row.active_ge_070 else '否'} | {'是' if row.active_ge_075 else '否'} |"
        )
    proxy = similarity[
        (similarity["structure_left"] == "equal3")
        & (similarity["structure_right"] == "pc1_three")
    ]
    proxy_rows = []
    for half_label, part in proxy.groupby("half_label", sort=False):
        proxy_rows.append(
            f"| {HALF_LIVES[half_label]:g}年 | {part['spearman'].iloc[0]:.4f} | {part['jaccard'].min():.2%} | {part['current_absolute_difference'].iloc[0]:.2%} |"
        )
    center_corr = factor_corr[factor_corr["half_label"] == "hl10p0"]
    selected_corr = center_corr[
        center_corr.apply(
            lambda row: {row["factor_left"], row["factor_right"]}
            in ({"pb", "erp"}, {"pb", "dividend"}, {"erp", "dividend"}),
            axis=1,
        )
    ]
    corr_rows = [
        f"| {row.factor_left}—{row.factor_right} | {row.weighted_pearson:.3f} | {row.weighted_rank_correlation:.3f} |"
        for row in selected_corr.itertuples(index=False)
    ]
    vintage_min = float(vintage_pca["pc1_explained_ratio"].min())
    vintage_max = float(vintage_pca["pc1_explained_ratio"].max())
    load_ranges = {
        name: (float(vintage_pca[column].min()), float(vintage_pca[column].max()))
        for name, column in (
            ("PB", "loading_pb"),
            ("ERP", "loading_erp"),
            ("股息", "loading_dividend"),
        )
    }
    design = str(decision["design_center"])
    conclusion = (
        "三因子等权通过预注册规则，作为下一版透明设计中心；PC1只作统计确认。"
        if design == "equal3"
        else "三因子等权未完整复制共同因子，下一版以PC1为统计设计中心。"
        if design == "pc1_three"
        else "共同因子或时间权重稳健性未通过，本版不产生设计中心。"
    )
    return f"""# 中证500固定估值因子结构 v3.1

## 结论

{conclusion}

最重要的发现不是哪一个权重收益更好，而是 PB、ERP、股息三项主要在描述同一个估值周期。旧 `25/50/25` 把 ERP 配成 50%，并没有得到“两个独立信息源”的统计证据；它更像对同一共同因子的主观偏重。因此，旧权重继续保留作历史基线，不再作为估值本体的默认结构。

本版没有使用 IC、贴水、PUT 或任何未来收益选择结构，也没有选择实盘启动门槛。

## 全历史静态结构

| 时间半衰期 | PC1解释率 | 三因子有效维数 | PB载荷 | ERP载荷 | 股息载荷 | 等权与PC1 Spearman |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(pca_rows)}

“有效维数”若接近 1，表示三个指标基本是同一个周期；若接近 3，才意味着三个相对独立的维度。这里应结合 PC1 解释率阅读。

### 10年中心风险分量相关性

| 风险分量 | 加权Pearson | 加权秩相关 |
| --- | ---: | ---: |
{chr(10).join(corr_rows)}

## 等权三因子能否替代 PCA

| 时间半衰期 | 月度Spearman | 0.70/0.75/0.80最差Jaccard | 当前差异 |
| --- | ---: | ---: | ---: |
{chr(10).join(proxy_rows)}

- 共同周期门槛通过：{'是' if decision['common_cycle_pass'] else '否'}；
- 等权/PCA 相关性通过：{'是' if decision['equal3_pc1_spearman_pass'] else '否'}；
- 三档状态一致性通过：{'是' if decision['equal3_pc1_jaccard_pass'] else '否'}；
- 当前最大绝对差：{decision['equal3_pc1_current_max_abs_diff']:.2%}；
- 等权结构跨 7.5/10/12 年稳健性通过：{'是' if decision['equal3_time_robustness_pass'] else '否'}。

## 当前状态（2026-08-17，10年时间权重中心）

| 结构 | 原始合成分 | 标准化风险 | >=0.70 | >=0.75 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(current_rows)}

原始分只能在同一结构内解释；跨结构比较必须看“标准化风险”。门槛列只是结构探针，不是本版选出的交易规则。

## ERP 拆分的含义

`ERP = 盈利收益率 - 10年国债收益率` 已在日度和月度数据逐点验证。把 ERP 拆成 EY 与国债后再四项等权，目的不是增加因子数量，而是检查利率项是否提供独立维度。若拆分结构仍与前三种结构高度一致，就不能因“四项”这个名字把它当成四份独立证据。

## 历史时点因果审计

- 2016—2025 年末及 2026-08，共 11 个时点；每次仅用当时可见历史重算；
- PC1 解释率范围：{vintage_min:.2%}—{vintage_max:.2%}；
- PB 载荷范围：{load_ranges['PB'][0]:.3f}—{load_ranges['PB'][1]:.3f}；
- ERP 载荷范围：{load_ranges['ERP'][0]:.3f}—{load_ranges['ERP'][1]:.3f}；
- 股息载荷范围：{load_ranges['股息'][0]:.3f}—{load_ranges['股息'][1]:.3f}。

该审计只验证定义随样本扩展是否稳定，没有把任一历史时点与后续收益配对。

## 下一层建议

下一版才研究固定经济门槛：以 `{design}` 为估值本体中心，先把风险百分位反解成 PB、ERP、股息的经济状态区间，并对 1.50—2.00 旧固定总分附近做更细的门槛平台，而不是直接跳到策略收益优化。PC1 继续作为“是否仍在测同一个周期”的影子基准。

## 审计边界

- 参数候选：195；窗口扫描行：975；
- 扫描表中的年化收益、波动、Sharpe、最大回撤全部是同窗口中证500价格指数背景，不是候选策略绩效；
- 研究状态：`RESEARCH_ONLY_NOT_LIVE_APPROVED`。
"""


def write_scan_files(
    long: pd.DataFrame, wide: pd.DataFrame, decision: dict[str, object]
) -> None:
    long.to_csv(SCAN / "scan_summary.csv", index=False)
    wide.to_csv(SCAN / "window_metrics.csv", index=False)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "complete",
            "scan_type": "outcome_free_factor_structure_and_state_threshold_probe",
            "baseline": {"structure": "legacy_255025", "half_life": 10.0},
            "candidate_grid": {
                "structures": list(STRUCTURES),
                "half_lives_years": list(HALF_LIVES.values()),
                "risk_thresholds": [float(value) for value in THRESHOLDS],
            },
            "data_snapshot": {
                "start": long["start"].min(),
                "end": long["end"].max(),
                "price_index": "CSI500 price index",
                "strategy_returns_used_for_selection": False,
            },
            "cost_model": {
                "applicable": False,
                "reason": "outcome-free valuation structure audit with no trades",
            },
            "candidate_count": int(long["candidate"].nunique()),
            "window_rows": int(len(long)),
            "decision": decision,
            "stability_label": "structural_only",
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython ic_fixed_valuation_factor_structure_v3_1.py\n")


def main() -> None:
    input_hashes = verify_inputs()
    verify_output_manifest(V3_OUTPUT)
    monthly = prepare_base(MONTHLY_INPUT)
    daily = prepare_base(DAILY_INPUT)
    if len(monthly) != 236 or len(daily) != 4_761:
        raise RuntimeError("Unexpected sample size")
    erp_error_monthly = float((monthly["earnings_yield"] - monthly["gov10y_yield"] - monthly["erp"]).abs().max())
    erp_error_daily = float((daily["earnings_yield"] - daily["gov10y_yield"] - daily["erp"]).abs().max())
    if max(erp_error_monthly, erp_error_daily) > 1e-12:
        raise RuntimeError("ERP decomposition mismatch")

    scored_monthly, scored_daily, pca, factor_corr = build_scores(monthly, daily)
    v2_monthly = pd.read_csv(V2_MONTHLY, parse_dates=["date"])
    v2_daily = pd.read_csv(V2_DAILY, parse_dates=["date"])
    if not scored_monthly["date"].equals(v2_monthly["date"]) or not scored_daily["date"].equals(v2_daily["date"]):
        raise RuntimeError("v2 parity date mismatch")
    parity_errors = {}
    for half_label in HALF_LIVES:
        parity_errors[half_label] = max(
            float(
                np.max(
                    np.abs(
                        scored_monthly[raw_col("legacy_255025", half_label)]
                        - v2_monthly[f"continuous_risk__{half_label}"]
                    )
                )
            ),
            float(
                np.max(
                    np.abs(
                        scored_daily[raw_col("legacy_255025", half_label)]
                        - v2_daily[f"continuous_risk__{half_label}"]
                    )
                )
            ),
        )
    if max(parity_errors.values()) > 1e-12:
        raise RuntimeError(f"Legacy score parity failed: {parity_errors}")

    v3_monthly = pd.read_csv(
        V3_OUTPUT / "monthly_factor_structure_scores.csv", parse_dates=["date"]
    )
    v3_daily = pd.read_csv(
        V3_OUTPUT / "daily_factor_structure_scores.csv.gz", parse_dates=["date"]
    )
    score_columns = [
        column
        for column in scored_daily.columns
        if column.startswith("risk_component__")
        or column.startswith("raw_score__")
        or column.startswith("normalized_risk__")
    ]
    correction_max_abs_error = max(
        float(
            np.max(
                np.abs(
                    scored_monthly[score_columns].to_numpy(dtype=float)
                    - v3_monthly[score_columns].to_numpy(dtype=float)
                )
            )
        ),
        float(
            np.max(
                np.abs(
                    scored_daily[score_columns].to_numpy(dtype=float)
                    - v3_daily[score_columns].to_numpy(dtype=float)
                )
            )
        ),
    )
    if correction_max_abs_error > 2e-15:
        raise RuntimeError(
            f"Numerical correction exceeded preregistered tolerance: {correction_max_abs_error}"
        )

    bounded_columns = [
        column
        for column in score_columns
        if column.startswith("risk_component__")
        or column.startswith("normalized_risk__")
    ]
    bounds_pass = bool(
        scored_monthly[bounded_columns].ge(0).all().all()
        and scored_monthly[bounded_columns].le(1).all().all()
        and scored_daily[bounded_columns].ge(0).all().all()
        and scored_daily[bounded_columns].le(1).all().all()
    )
    if not bounds_pass:
        raise RuntimeError("Clipped component or normalized score remains outside [0,1]")

    similarity = make_structure_similarity(scored_monthly, scored_daily)
    time_robustness = make_time_robustness(scored_monthly)
    current = make_current_state(scored_daily)
    vintage_pca, vintage_state = make_vintage_audit(monthly)
    price_context = make_price_context(scored_daily)
    threshold_long, threshold_wide = make_threshold_scan(
        scored_daily, scored_monthly, price_context
    )
    decision = selection_decision(pca, similarity, time_robustness, current)
    states_identical = bool(
        all(
            np.array_equal(
                scored_daily[norm_col(structure, half_label)].to_numpy() >= threshold,
                v3_daily[norm_col(structure, half_label)].to_numpy() >= threshold,
            )
            for structure in STRUCTURES
            for half_label in HALF_LIVES
            for threshold in THRESHOLDS
        )
    )
    v3_decision = json.loads(
        (V3_OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )["selection"]
    selection_identical = decision == v3_decision
    if not states_identical or not selection_identical:
        raise RuntimeError("Numerical correction changed state flags or selection")

    if threshold_long["candidate"].nunique() != 195 or len(threshold_long) != 975:
        raise RuntimeError("Candidate grid size mismatch")
    if not threshold_long.groupby("segment")["ann_return"].nunique().eq(1).all():
        raise RuntimeError("Price context differs across candidates")
    if (pd.to_datetime(vintage_pca["history_end"]) > pd.to_datetime(vintage_pca["vintage_date"])).any():
        raise RuntimeError("Vintage audit contains future data")

    STAGING.mkdir(parents=True)
    scored_monthly.to_csv(STAGING / "monthly_factor_structure_scores.csv", index=False)
    scored_daily.to_csv(STAGING / "daily_factor_structure_scores.csv.gz", index=False, compression="gzip")
    pca.to_csv(STAGING / "pca_diagnostics.csv", index=False)
    factor_corr.to_csv(STAGING / "factor_risk_correlations.csv", index=False)
    similarity.to_csv(STAGING / "structure_similarity.csv", index=False)
    time_robustness.to_csv(STAGING / "time_weight_robustness.csv", index=False)
    current.to_csv(STAGING / "current_factor_state.csv", index=False)
    vintage_pca.to_csv(STAGING / "vintage_pca_diagnostics.csv", index=False)
    vintage_state.to_csv(STAGING / "vintage_factor_state.csv", index=False)
    price_context.to_csv(STAGING / "underlying_price_context.csv", index=False)
    threshold_long.to_csv(STAGING / "factor_structure_threshold_coverage.csv", index=False)
    threshold_wide.to_csv(STAGING / "factor_structure_window_metrics.csv", index=False)

    setup_font()
    plot_current(current, STAGING)
    plot_timeseries(scored_monthly, STAGING)
    plot_similarity(similarity, STAGING)
    plot_pca_vintage(vintage_pca, STAGING)

    record = make_record(
        pca,
        similarity,
        time_robustness,
        current,
        vintage_pca,
        factor_corr,
        decision,
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    integrity = {
        "status": "passed",
        "version": VERSION,
        "research_only_not_live_approved": True,
        "strategy_returns_used_for_selection": False,
        "sample": {
            "daily_rows": len(scored_daily),
            "monthly_rows": len(scored_monthly),
            "start": scored_daily["date"].min().date().isoformat(),
            "end": scored_daily["date"].max().date().isoformat(),
        },
        "input_hashes": input_hashes,
        "spec_hash": sha256(SPEC),
        "erp_decomposition_max_abs_error": max(erp_error_monthly, erp_error_daily),
        "legacy_v2_parity_max_abs_error": max(parity_errors.values()),
        "v3_numerical_correction_max_abs_error": correction_max_abs_error,
        "v3_threshold_states_identical": states_identical,
        "v3_selection_identical": selection_identical,
        "candidate_count": int(threshold_long["candidate"].nunique()),
        "scan_rows": len(threshold_long),
        "windows": list(WINDOWS),
        "vintage_count": int(vintage_pca["vintage_date"].nunique()),
        "vintage_no_future_rows": True,
        "all_component_and_normalized_scores_in_unit_interval": bounds_pass,
        "price_context_identical_within_window": True,
        "selection": decision,
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
    write_scan_files(threshold_long, threshold_wide, decision)
    shutil.move(str(STAGING), str(OUTPUT))
    print(json.dumps(integrity, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
