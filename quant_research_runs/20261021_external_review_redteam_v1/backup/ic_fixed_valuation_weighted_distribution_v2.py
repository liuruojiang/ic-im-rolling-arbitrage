from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_weighted_distribution_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OUTPUT = ROOT / "outputs" / VERSION
V1_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_time_weight_v1"
V1_SPEC = ROOT / "docs" / "ic_fixed_valuation_time_weight_v1_spec.md"
V1_AUDIT = ROOT / "docs" / "ic_fixed_valuation_time_weight_v1_postrun_audit.md"
MONTHLY_INPUT = V1_OUTPUT / "monthly_weight_map.csv"
DAILY_INPUT = V1_OUTPUT / "daily_weight_map.csv.gz"
V1_INTEGRITY = V1_OUTPUT / "integrity_checks.json"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_weighted_distribution_v2_valuation_body_half_life_and_continuous_risk_threshold"
)
EXPECTED_HASHES = {
    MONTHLY_INPUT: "3ff4a866e090722cd23666f65af38d0d397bf627454491bfe9e55a554cc01e0a",
    DAILY_INPUT: "244676065643b97419fe1b84aa32bff3a05a340818956f30029c035013f02102",
    V1_INTEGRITY: "4ed3d23a4ebdede56ea1a656b8025dc080aeb972f86a5a630c60a498cf92f205",
    V1_SPEC: "d062b0e7d9c4a60d55c4e1d4cb0505d672fab6037048f5c9a49ca6b22862194d",
    V1_AUDIT: "b53abf714816f5dd6fcde6c8784e7bd23ff55ebdf6b16c3963d01d0bf7ccd71c",
}
HALF_LIVES = {"hl07p5": 7.5, "hl10p0": 10.0, "hl12p0": 12.0}
V1_WEIGHT_COLUMNS = {
    "hl07p5": "weight__exp_hl07p5",
    "hl10p0": "weight__exp_hl10p0",
    "hl12p0": "weight__exp_hl12p0",
}
THRESHOLDS = np.round(np.arange(0.500, 0.950 + 0.0001, 0.025), 3)
FIELDS = {
    "pe_aggregate_ttm": {"label": "聚合TTM PE", "high_is_risk": True},
    "pb_aggregate": {"label": "聚合PB", "high_is_risk": True},
    "erp": {"label": "简化ERP", "high_is_risk": False},
    "trailing_dividend_contribution": {"label": "过去一年已实现股息贡献", "high_is_risk": False},
}
OLD_RAW_THRESHOLDS = {
    "pe_aggregate_ttm": [20.0, 25.0, 30.0, 35.0, 40.0],
    "pb_aggregate": [2.0, 2.5],
    "erp": [0.015, 0.030],
    "trailing_dividend_contribution": [0.010, 0.020],
}
WINDOWS = ["full", "last_10y", "last_5y", "last_3y", "last_1y"]
ANNUALIZATION_DAYS = 244.0
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
        raise RuntimeError("Frozen v2 specification mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {SCAN}")
    for path, expected in EXPECTED_HASHES.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen v1 input mismatch: {path}: {actual} != {expected}")
    integrity = json.loads(V1_INTEGRITY.read_text(encoding="utf-8"))
    if integrity.get("status") != "passed" or integrity.get("design_center") != "exp_hl10p0":
        raise RuntimeError("v1 integrity or design center is not authoritative")
    return {str(path.relative_to(ROOT)): sha256(path) for path in EXPECTED_HASHES}


def weighted_quantile(values: pd.Series, weights: pd.Series, quantile: float) -> float:
    order = np.argsort(values.to_numpy(dtype=float), kind="mergesort")
    sorted_values = values.to_numpy(dtype=float)[order]
    sorted_weights = weights.to_numpy(dtype=float)[order]
    cumulative = np.cumsum(sorted_weights) / sorted_weights.sum()
    index = int(np.searchsorted(cumulative, quantile, side="left"))
    return float(sorted_values[min(index, len(sorted_values) - 1)])


def weighted_risk_mapper(
    calibration: pd.Series, weights: pd.Series, high_value_is_risk: bool
):
    values = calibration.to_numpy(dtype=float)
    weight_values = weights.to_numpy(dtype=float)
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    sorted_weights = weight_values[order]
    cumulative = np.cumsum(sorted_weights) / sorted_weights.sum()

    def map_values(targets: pd.Series | np.ndarray) -> np.ndarray:
        target_array = np.asarray(targets, dtype=float)
        if high_value_is_risk:
            indices = np.searchsorted(sorted_values, target_array, side="right") - 1
            return np.where(indices >= 0, cumulative[np.maximum(indices, 0)], 0.0)
        indices = np.searchsorted(sorted_values, target_array, side="left") - 1
        below = np.where(indices >= 0, cumulative[np.maximum(indices, 0)], 0.0)
        return 1.0 - below

    return map_values


def old_fixed_risk(frame: pd.DataFrame) -> pd.Series:
    pb = np.select(
        [frame["pb_aggregate"].ge(2.50), frame["pb_aggregate"].ge(2.00)],
        [2.0, 1.0],
        default=0.0,
    )
    erp = np.select(
        [frame["erp"].le(0.015), frame["erp"].le(0.030)],
        [2.0, 1.0],
        default=0.0,
    )
    dividend = np.select(
        [
            frame["trailing_dividend_contribution"].lt(0.010),
            frame["trailing_dividend_contribution"].lt(0.020),
        ],
        [2.0, 1.0],
        default=0.0,
    )
    return pd.Series(0.25 * pb + 0.50 * erp + 0.25 * dividend, index=frame.index)


def add_continuous_scores(
    target: pd.DataFrame, calibration: pd.DataFrame
) -> pd.DataFrame:
    result = target.copy()
    result["old_fixed_risk"] = old_fixed_risk(result)
    for half_label, weight_column in V1_WEIGHT_COLUMNS.items():
        weights = calibration[weight_column]
        for field, config in FIELDS.items():
            mapper = weighted_risk_mapper(
                calibration[field], weights, bool(config["high_is_risk"])
            )
            result[f"{field}__risk__{half_label}"] = mapper(result[field])
        result[f"continuous_risk__{half_label}"] = (
            0.25 * result[f"pb_aggregate__risk__{half_label}"]
            + 0.50 * result[f"erp__risk__{half_label}"]
            + 0.25
            * result[f"trailing_dividend_contribution__risk__{half_label}"]
        )
    return result


def make_quantile_grid(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for half_label, half_life in HALF_LIVES.items():
        weights = monthly[V1_WEIGHT_COLUMNS[half_label]]
        for field, config in FIELDS.items():
            for quantile in np.arange(0.01, 1.00, 0.01):
                rows.append(
                    {
                        "half_label": half_label,
                        "half_life_years": half_life,
                        "field": field,
                        "label": config["label"],
                        "quantile": round(float(quantile), 2),
                        "value": weighted_quantile(monthly[field], weights, float(quantile)),
                    }
                )
    return pd.DataFrame(rows)


def make_old_threshold_locations(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for half_label, half_life in HALF_LIVES.items():
        weights = monthly[V1_WEIGHT_COLUMNS[half_label]]
        for field, thresholds in OLD_RAW_THRESHOLDS.items():
            cdf_mapper = weighted_risk_mapper(monthly[field], weights, True)
            risk_mapper = weighted_risk_mapper(
                monthly[field], weights, bool(FIELDS[field]["high_is_risk"])
            )
            for threshold in thresholds:
                rows.append(
                    {
                        "half_label": half_label,
                        "half_life_years": half_life,
                        "field": field,
                        "label": FIELDS[field]["label"],
                        "raw_threshold": threshold,
                        "ordinary_weighted_percentile": float(cdf_mapper(np.array([threshold]))[0]),
                        "risk_weighted_percentile": float(risk_mapper(np.array([threshold]))[0]),
                        "high_value_is_risk": bool(FIELDS[field]["high_is_risk"]),
                    }
                )
    return pd.DataFrame(rows)


def weighted_summary(values: pd.Series, weights: pd.Series) -> dict[str, float]:
    normalized = weights / weights.sum()
    return {
        "weighted_mean": float(np.average(values, weights=normalized)),
        "p10": weighted_quantile(values, normalized, 0.10),
        "p25": weighted_quantile(values, normalized, 0.25),
        "median": weighted_quantile(values, normalized, 0.50),
        "p75": weighted_quantile(values, normalized, 0.75),
        "p90": weighted_quantile(values, normalized, 0.90),
    }


def make_old_score_crosswalk(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for score, part in monthly.groupby("old_fixed_risk", sort=True):
        weights = part[V1_WEIGHT_COLUMNS["hl10p0"]]
        rows.append(
            {
                "old_fixed_risk": float(score),
                "months": int(len(part)),
                "center_weight_share": float(weights.sum()),
                **weighted_summary(part["continuous_risk__hl10p0"], weights),
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


def episode_stats(active: pd.Series) -> tuple[int, int]:
    values = active.astype(bool).to_numpy()
    starts = int(np.sum(values & ~np.r_[False, values[:-1]]))
    longest = 0
    current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return starts, longest


def jaccard(left: pd.Series, right: pd.Series) -> float:
    left_values = left.astype(bool).to_numpy()
    right_values = right.astype(bool).to_numpy()
    union = np.logical_or(left_values, right_values).sum()
    return 1.0 if union == 0 else float(np.logical_and(left_values, right_values).sum() / union)


def price_metrics(part: pd.DataFrame) -> dict[str, object]:
    ordered = part.sort_values("date").reset_index(drop=True)
    returns = ordered["price_close"].pct_change().dropna()
    years = (ordered["date"].iloc[-1] - ordered["date"].iloc[0]).days / 365.2425
    cagr = float(
        (ordered["price_close"].iloc[-1] / ordered["price_close"].iloc[0]) ** (1 / years)
        - 1
    )
    vol = float(returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS))
    sharpe = float(returns.mean() / returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS))
    nav = ordered["price_close"] / ordered["price_close"].iloc[0]
    return {
        "start": ordered["date"].iloc[0].date().isoformat(),
        "end": ordered["date"].iloc[-1].date().isoformat(),
        "rows": int(len(ordered)),
        "ann_return": cagr,
        "ann_vol": vol,
        "sharpe_repo": sharpe,
        "max_dd": float((nav / nav.cummax() - 1).min()),
    }


def make_price_context(daily: pd.DataFrame, anchor: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for segment, boundary in window_boundaries(anchor).items():
        part = daily if boundary is None else daily[daily["date"] >= boundary]
        rows.append({"segment": segment, **price_metrics(part)})
    return pd.DataFrame(rows)


def make_threshold_coverage(
    daily: pd.DataFrame, monthly: pd.DataFrame, price_context: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor = pd.Timestamp(daily["date"].max())
    boundaries = window_boundaries(anchor)
    threshold_stability: dict[float, dict[str, float | bool]] = {}
    for threshold in THRESHOLDS:
        center = monthly["continuous_risk__hl10p0"] >= threshold
        low = monthly["continuous_risk__hl07p5"] >= threshold
        high = monthly["continuous_risk__hl12p0"] >= threshold
        recent = monthly["date"] >= boundaries["last_10y"]
        recent_shares = [
            float((monthly.loc[recent, f"continuous_risk__{label}"] >= threshold).mean())
            for label in HALF_LIVES
        ]
        left_jaccard = jaccard(low, center)
        right_jaccard = jaccard(high, center)
        spread = max(recent_shares) - min(recent_shares)
        threshold_stability[float(threshold)] = {
            "jaccard_hl07p5_vs_center": left_jaccard,
            "jaccard_hl12p0_vs_center": right_jaccard,
            "recent_10y_activation_spread": spread,
            "mapping_stable": bool(
                left_jaccard >= 0.80 and right_jaccard >= 0.80 and spread <= 0.05
            ),
        }

    rows: list[dict[str, object]] = []
    for half_label, half_life in HALF_LIVES.items():
        score_column = f"continuous_risk__{half_label}"
        for threshold in THRESHOLDS:
            candidate = f"{half_label}__risk_ge_{int(round(threshold * 1000)):03d}"
            active_month = monthly[score_column] >= threshold
            episodes, longest = episode_stats(active_month)
            stability = threshold_stability[float(threshold)]
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
                        "half_label": half_label,
                        "half_life_years": half_life,
                        "risk_threshold": float(threshold),
                        "activation_day_ratio": float((daily_part[score_column] >= threshold).mean()),
                        "activation_month_ratio": float((monthly_part[score_column] >= threshold).mean()),
                        "monthly_episodes_full": episodes,
                        "longest_active_months_full": longest,
                        "current_active": bool(active_month.iloc[-1]),
                        **stability,
                        "metric_semantics": "underlying_price_index_context_only_no_strategy_return",
                    }
                )
    long = pd.DataFrame(rows)
    wide_rows: list[dict[str, object]] = []
    for candidate, part in long.groupby("candidate", sort=False):
        first = part.iloc[0]
        row: dict[str, object] = {
            "candidate": candidate,
            "half_label": first["half_label"],
            "half_life_years": first["half_life_years"],
            "risk_threshold": first["risk_threshold"],
            "monthly_episodes_full": first["monthly_episodes_full"],
            "longest_active_months_full": first["longest_active_months_full"],
            "current_active": first["current_active"],
            "jaccard_hl07p5_vs_center": first["jaccard_hl07p5_vs_center"],
            "jaccard_hl12p0_vs_center": first["jaccard_hl12p0_vs_center"],
            "recent_10y_activation_spread": first["recent_10y_activation_spread"],
            "mapping_stable": first["mapping_stable"],
        }
        for item in part.itertuples(index=False):
            row[f"ann_return_{item.segment}"] = item.ann_return
            row[f"max_dd_{item.segment}"] = item.max_dd
            row[f"sharpe_repo_{item.segment}"] = item.sharpe_repo
            row[f"activation_day_ratio_{item.segment}"] = item.activation_day_ratio
            row[f"activation_month_ratio_{item.segment}"] = item.activation_month_ratio
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def make_old_fixed_coverage(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> pd.DataFrame:
    anchor = pd.Timestamp(daily["date"].max())
    rows: list[dict[str, object]] = []
    for threshold in (1.50, 1.75, 2.00):
        active_month = monthly["old_fixed_risk"] >= threshold
        episodes, longest = episode_stats(active_month)
        for segment, boundary in window_boundaries(anchor).items():
            daily_part = daily if boundary is None else daily[daily["date"] >= boundary]
            monthly_part = monthly if boundary is None else monthly[monthly["date"] >= boundary]
            rows.append(
                {
                    "old_fixed_threshold": threshold,
                    "segment": segment,
                    "start": daily_part["date"].min().date().isoformat(),
                    "end": daily_part["date"].max().date().isoformat(),
                    "daily_rows": len(daily_part),
                    "monthly_rows": len(monthly_part),
                    "activation_day_ratio": float(
                        (daily_part["old_fixed_risk"] >= threshold).mean()
                    ),
                    "activation_month_ratio": float(
                        (monthly_part["old_fixed_risk"] >= threshold).mean()
                    ),
                    "monthly_episodes_full": episodes,
                    "longest_active_months_full": longest,
                    "current_active": bool(active_month.iloc[-1]),
                }
            )
    return pd.DataFrame(rows)


def make_vintage_tables(
    monthly: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    vintage_dates = []
    for year in range(2016, 2026):
        part = monthly[(monthly["date"].dt.year == year) & (monthly["date"].dt.month == 12)]
        if part.empty:
            raise RuntimeError(f"Missing December vintage for {year}")
        vintage_dates.append(pd.Timestamp(part["date"].max()))
    vintage_dates.append(pd.Timestamp(monthly["date"].max()))
    quantile_rows: list[dict[str, object]] = []
    threshold_rows: list[dict[str, object]] = []
    for vintage in vintage_dates:
        history = monthly[monthly["date"] <= vintage].copy()
        ages = (vintage - history["date"]).dt.days / 365.2425
        weights = np.power(2.0, -ages / 10.0)
        weights = pd.Series(weights / weights.sum(), index=history.index)
        if history["date"].max() > vintage:
            raise RuntimeError("Future monthly observation entered vintage")
        for field, config in FIELDS.items():
            for quantile in (0.10, 0.25, 0.50, 0.75, 0.90):
                quantile_rows.append(
                    {
                        "vintage_date": vintage.date().isoformat(),
                        "history_start": history["date"].min().date().isoformat(),
                        "history_end": history["date"].max().date().isoformat(),
                        "history_months": len(history),
                        "field": field,
                        "label": config["label"],
                        "quantile": quantile,
                        "value": weighted_quantile(history[field], weights, quantile),
                    }
                )
            cdf_mapper = weighted_risk_mapper(history[field], weights, True)
            risk_mapper = weighted_risk_mapper(
                history[field], weights, bool(config["high_is_risk"])
            )
            for threshold in OLD_RAW_THRESHOLDS[field]:
                threshold_rows.append(
                    {
                        "vintage_date": vintage.date().isoformat(),
                        "history_end": history["date"].max().date().isoformat(),
                        "field": field,
                        "label": config["label"],
                        "raw_threshold": threshold,
                        "ordinary_weighted_percentile": float(
                            cdf_mapper(np.array([threshold]))[0]
                        ),
                        "risk_weighted_percentile": float(
                            risk_mapper(np.array([threshold]))[0]
                        ),
                    }
                )
    quantiles = pd.DataFrame(quantile_rows)
    old_thresholds = pd.DataFrame(threshold_rows)
    drift_rows: list[dict[str, object]] = []
    current = quantiles[quantiles["vintage_date"] == quantiles["vintage_date"].max()]
    for field in FIELDS:
        current_field = current[current["field"] == field].set_index("quantile")
        current_iqr = float(current_field.loc[0.75, "value"] - current_field.loc[0.25, "value"])
        for quantile in (0.10, 0.25, 0.50, 0.75, 0.90):
            path = quantiles[
                (quantiles["field"] == field) & (quantiles["quantile"] == quantile)
            ].sort_values("vintage_date")
            values = path["value"].to_numpy(dtype=float)
            drift_rows.append(
                {
                    "field": field,
                    "label": FIELDS[field]["label"],
                    "quantile": quantile,
                    "first_vintage": path["vintage_date"].iloc[0],
                    "last_vintage": path["vintage_date"].iloc[-1],
                    "first_value": values[0],
                    "last_value": values[-1],
                    "absolute_drift": values[-1] - values[0],
                    "current_iqr": current_iqr,
                    "drift_in_current_iqr": (values[-1] - values[0]) / current_iqr,
                    "max_annual_change": float(np.max(np.abs(np.diff(values)))),
                    "max_annual_change_in_current_iqr": float(
                        np.max(np.abs(np.diff(values))) / current_iqr
                    ),
                }
            )
    return quantiles, old_thresholds, pd.DataFrame(drift_rows)


def weighted_corr(frame: pd.DataFrame, columns: list[str], weights: pd.Series) -> pd.DataFrame:
    values = frame[columns].to_numpy(dtype=float)
    normalized = weights.to_numpy(dtype=float)
    normalized = normalized / normalized.sum()
    means = np.sum(values * normalized[:, None], axis=0)
    centered = values - means
    covariance = (centered * normalized[:, None]).T @ centered
    scale = np.sqrt(np.diag(covariance))
    corr = covariance / np.outer(scale, scale)
    return pd.DataFrame(corr, index=columns, columns=columns)


def make_correlations(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = monthly.copy()
    frame["earnings_yield"] = 1.0 / frame["pe_aggregate_ttm"]
    columns = [
        "pe_aggregate_ttm",
        "pb_aggregate",
        "earnings_yield",
        "gov10y_yield",
        "erp",
        "trailing_dividend_contribution",
    ]
    weights = frame[V1_WEIGHT_COLUMNS["hl10p0"]]
    pearson = weighted_corr(frame, columns, weights)
    ranked = frame.copy()
    ranked[columns] = ranked[columns].rank(method="average", pct=True)
    spearman = weighted_corr(ranked, columns, weights)
    return pearson, spearman


def setup_font() -> None:
    if FONT_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_PATH))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT_PATH)).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def plot_weighted_distributions(monthly: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.5))
    colors = {"hl07p5": "#2ca02c", "hl10p0": "#d62728", "hl12p0": "#9467bd"}
    labels = {"hl07p5": "7.5年", "hl10p0": "10年中心", "hl12p0": "12年"}
    for ax, (field, config) in zip(axes.ravel(), FIELDS.items(), strict=True):
        for half_label in HALF_LIVES:
            values = monthly[field].to_numpy(dtype=float)
            weights = monthly[V1_WEIGHT_COLUMNS[half_label]].to_numpy(dtype=float)
            order = np.argsort(values, kind="mergesort")
            ax.plot(values[order], np.cumsum(weights[order]), color=colors[half_label], linewidth=2, label=labels[half_label])
        for threshold in OLD_RAW_THRESHOLDS[field]:
            ax.axvline(threshold, color="#333333", linestyle="--", alpha=0.55)
        if field in {"erp", "trailing_dividend_contribution"}:
            ax.xaxis.set_major_formatter(lambda value, _: f"{value:.1%}")
        ax.set_title(str(config["label"]))
        ax.set_ylabel("累计加权分布")
        ax.grid(alpha=0.22)
    axes[0, 0].legend(frameon=False)
    fig.suptitle("中证500固定估值：7.5/10/12年加权分布与旧经济门槛", fontsize=16, y=0.99)
    fig.tight_layout()
    fig.savefig(output / "weighted_distributions.png", dpi=180)
    plt.close(fig)


def plot_threshold_coverage(coverage: pd.DataFrame, output: Path) -> None:
    full = coverage[coverage["segment"] == "full"]
    recent = coverage[coverage["segment"] == "last_10y"]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), sharey=True)
    for ax, part, title in (
        (axes[0], full, "全样本日度覆盖率"),
        (axes[1], recent, "最近10年日度覆盖率"),
    ):
        for label, years in HALF_LIVES.items():
            line = part[part["half_label"] == label].sort_values("risk_threshold")
            ax.plot(line["risk_threshold"], line["activation_day_ratio"] * 100, marker="o", markersize=3, linewidth=2, label=f"{years:g}年")
        ax.set_title(title)
        ax.set_xlabel("连续风险启动门槛")
        ax.grid(alpha=0.22)
    axes[0].set_ylabel("高风险覆盖率（%）")
    axes[0].legend(frameon=False)
    fig.suptitle("连续门槛每提高0.025带来的覆盖率变化", fontsize=16, y=1.02)
    fig.tight_layout()
    fig.savefig(output / "continuous_threshold_coverage.png", dpi=180)
    plt.close(fig)


def plot_old_crosswalk(monthly: pd.DataFrame, output: Path) -> None:
    groups = []
    labels = []
    for score, part in monthly.groupby("old_fixed_risk", sort=True):
        groups.append(part["continuous_risk__hl10p0"].to_numpy(dtype=float))
        labels.append(f"{score:g}\n({len(part)}月)")
    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.boxplot(groups, tick_labels=labels, showfliers=False, whis=(10, 90))
    ax.set_title("旧0.25离散总分与10年中心连续风险的交叉映射")
    ax.set_xlabel("旧固定风险分（括号为月数）")
    ax.set_ylabel("连续风险分")
    ax.grid(axis="y", alpha=0.22)
    fig.tight_layout()
    fig.savefig(output / "old_fixed_score_crosswalk.png", dpi=180)
    plt.close(fig)


def plot_vintage_drift(vintage: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.5), sharex=True)
    quantile_labels = {0.25: "25%", 0.50: "中位数", 0.75: "75%"}
    for ax, (field, config) in zip(axes.ravel(), FIELDS.items(), strict=True):
        part = vintage[vintage["field"] == field].copy()
        part["vintage_date"] = pd.to_datetime(part["vintage_date"])
        for quantile in (0.25, 0.50, 0.75):
            line = part[part["quantile"] == quantile]
            ax.plot(line["vintage_date"], line["value"], marker="o", linewidth=2, label=quantile_labels[quantile])
        if field in {"erp", "trailing_dividend_contribution"}:
            ax.yaxis.set_major_formatter(lambda value, _: f"{value:.1%}")
        ax.set_title(str(config["label"]))
        ax.grid(alpha=0.22)
    axes[0, 0].legend(frameon=False)
    fig.suptitle("只用各年度当时历史计算的10年半衰期估值锚", fontsize=16, y=0.99)
    fig.tight_layout()
    fig.savefig(output / "vintage_anchor_drift.png", dpi=180)
    plt.close(fig)


def format_value(field: str, value: float) -> str:
    if field in {"erp", "trailing_dividend_contribution"}:
        return f"{value:.2%}"
    return f"{value:.2f}"


def make_record(
    monthly: pd.DataFrame,
    daily: pd.DataFrame,
    quantiles: pd.DataFrame,
    locations: pd.DataFrame,
    crosswalk: pd.DataFrame,
    coverage: pd.DataFrame,
    old_coverage: pd.DataFrame,
    vintage_drift: pd.DataFrame,
    pearson: pd.DataFrame,
) -> str:
    current = daily.iloc[-1]
    current_rows = []
    for field, config in FIELDS.items():
        current_rows.append(
            f"| {config['label']} | {format_value(field, float(current[field]))} | {float(current[f'{field}__risk__hl07p5']):.2%} | {float(current[f'{field}__risk__hl10p0']):.2%} | {float(current[f'{field}__risk__hl12p0']):.2%} |"
        )
    old_rows = []
    center_locations = locations[locations["half_label"] == "hl10p0"]
    for row in center_locations.itertuples(index=False):
        old_rows.append(
            f"| {row.label} | {format_value(row.field, row.raw_threshold)} | {row.ordinary_weighted_percentile:.2%} | {row.risk_weighted_percentile:.2%} |"
        )
    stable_thresholds = sorted(
        coverage.loc[
            (coverage["segment"] == "full")
            & (coverage["half_label"] == "hl10p0")
            & coverage["mapping_stable"],
            "risk_threshold",
        ].unique()
    )
    center_full = coverage[
        (coverage["segment"] == "full") & (coverage["half_label"] == "hl10p0")
    ].sort_values("risk_threshold")
    selected_display = center_full[center_full["risk_threshold"].isin([0.60, 0.70, 0.75, 0.80, 0.85, 0.90])]
    coverage_rows = [
        f"| {row.risk_threshold:.3f} | {row.activation_day_ratio:.2%} | {int(row.monthly_episodes_full)} | {int(row.longest_active_months_full)} | {'是' if row.mapping_stable else '否'} |"
        for row in selected_display.itertuples(index=False)
    ]
    old_full = old_coverage[old_coverage["segment"] == "full"]
    old_compare = [
        f"| 旧固定>={row.old_fixed_threshold:.2f} | {row.activation_day_ratio:.2%} | {int(row.monthly_episodes_full)} | {int(row.longest_active_months_full)} | {'开' if row.current_active else '关'} |"
        for row in old_full.itertuples(index=False)
    ]
    stable_text = (
        "无"
        if not stable_thresholds
        else f"{min(stable_thresholds):.3f}—{max(stable_thresholds):.3f}（其中可能有离散缺口，详见CSV）"
    )
    return f"""# 中证500固定估值连续映射 v2

## 结论

本版已把旧0.25离散估值分拆成固定加权分布上的连续风险映射，并完成0.500—0.950、步长0.025的覆盖诊断。决定固定为`research_mapping_only`：**没有选择交易门槛，没有回测收益，未批准实盘**。

- 10年半衰期为中心，7.5/12年为强制敏感性；结构稳定门槛范围：{stable_text}。
- 当前2026-08-17连续风险：7.5/10/12年分别为{current['continuous_risk__hl07p5']:.2%}/{current['continuous_risk__hl10p0']:.2%}/{current['continuous_risk__hl12p0']:.2%}；旧固定风险分为{current['old_fixed_risk']:.2f}。
- 当前全历史映射用于未来固定研究锚，不能倒用于2007—2026收益回测；后续策略层必须另建开发/留出设计。

## 当前估值在固定加权分布中的位置

| 指标 | 当前值 | 7.5年风险分位 | 10年风险分位 | 12年风险分位 |
|---|---:|---:|---:|---:|
{chr(10).join(current_rows)}

## 旧原始门槛在10年中心分布中的位置

| 指标 | 旧门槛 | 普通分位 | 风险分位 |
|---|---:|---:|---:|
{chr(10).join(old_rows)}

PE门槛只作审计；主连续分数仍是PB 25% + ERP 50% + 股息贡献25%，避免PE与ERP重复计权。

## 连续门槛覆盖诊断（10年中心，全样本）

| 连续门槛 | 日度覆盖 | 月度episode | 最长连续月 | 7.5/10/12映射稳定 |
|---|---:|---:|---:|---|
{chr(10).join(coverage_rows)}

{chr(10).join(old_compare)}

上表只说明门槛每提高0.025会删掉多少估值状态，不说明这些状态能预测下跌。

## 变量冗余

- 10年中心加权Pearson：PE—ERP为{pearson.loc['pe_aggregate_ttm', 'erp']:.3f}，盈利收益率—ERP为{pearson.loc['earnings_yield', 'erp']:.3f}，PB—ERP为{pearson.loc['pb_aggregate', 'erp']:.3f}。
- 相关性支持继续把PE作为审计项而不直接叠加主分数；完整Pearson/Spearman矩阵见CSV。本版没有据此重调25%/50%/25%权重。

## 年度截面

- 2016—2025年末及2026-08均只使用当时已有历史复算10年半衰期锚。
- 四项估值的10%/25%/50%/75%/90%路径见`vintage_quantiles.csv`，按2026 IQR归一的漂移见`vintage_drift_diagnostics.csv`。
- 这项审计只判断经济锚是否漂移，不与后续收益配对。

## 数据与验证

- 月末校准：{monthly['date'].min().date().isoformat()}—{monthly['date'].max().date().isoformat()}，{len(monthly)}个月；日度映射{len(daily):,}行。
- 完整1%分位表：`weighted_quantile_grid.csv`；旧离散交叉表：`old_fixed_score_crosswalk.csv`；57候选：`continuous_threshold_coverage.csv`。
- 所有风险方向、权重和、候选数量、Jaccard、年度截面因果性由`integrity_checks.json`和独立pytest复算。
"""


def make_scan_record(
    monthly: pd.DataFrame, daily: pd.DataFrame, coverage: pd.DataFrame
) -> str:
    stable = coverage[
        (coverage["segment"] == "full")
        & (coverage["half_label"] == "hl10p0")
        & coverage["mapping_stable"]
    ]["risk_threshold"].tolist()
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `20260818_500_ic_fixed_valuation_weighted_distribution_v2_valuation_body_half_life_and_continuous_risk_threshold`
- Run date: 2026-08-18
- Timezone: Asia/Shanghai
- Operator: Codex
- Project: 中证500固定估值连续映射
- Repo or workspace path: `D:\\动量策略\\新策略研究`
- Version or strategy family: `{VERSION}`
- Sleeve or subsystem: `valuation_body`
- Parameter group: `half_life_and_continuous_risk_threshold`
- Scan type: `two_parameter_structural_mapping`

## Research Question

- Baseline: old fixed 0.25-step valuation score, reported separately.
- Candidate grid: 7.5/10/12-year half-life x continuous risk threshold 0.500-0.950 step 0.025 = 57.
- Decision target: map coverage and cross-half-life stability only; no return-based selection.
- Source-change rule: `research_only_no_source_change`.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.
- `mapping_stable`: both center Jaccards >=80% and recent-10-year activation spread <=5pp.

## Implementation Anchor

- Official entrypoint: `{VERSION}.py`.
- Frozen input: `outputs/ic_fixed_valuation_time_weight_v1/`.
- Function chain: `weighted_risk_mapper -> add_continuous_scores -> make_threshold_coverage`.

## Data Snapshot

- Monthly calibration: {len(monthly)} rows, {monthly['date'].min().date().isoformat()} to {monthly['date'].max().date().isoformat()}.
- Daily mapping: {len(daily)} rows, {daily['date'].min().date().isoformat()} to {daily['date'].max().date().isoformat()}.
- Adjustment mode: official price index unadjusted; dividend contribution inherited from frozen v1.
- Cache write risk: none.

## Cost and Execution Assumptions

- No strategy, trades, commission, slippage, financing, hedge, leverage, Put, or fill timing.
- `ann_return` and `max_dd` are CSI500 price-index context, identical within each window and excluded from selection.

## Runtime Override Plan

- New research harness only; no production or frozen prior logic changed.
- Full-2026 distribution is explicitly prohibited from causal historical performance testing.

## Commands

```powershell
python {VERSION}.py
python -m pytest -q test_{VERSION}.py
```

## Output Files

- `scan_summary.csv`: 57 candidates x 5 mandatory windows.
- `window_metrics.csv`: wide structural mapping metrics.
- Formal output: `outputs/{VERSION}/`.

## Full-Sample Results

- Center stable thresholds: {stable}.
- Complete coverage and Jaccard metrics are in the result CSVs.

## Window Results

- Five mandatory windows contain candidate activation ratios plus identical underlying-index context metrics.

## Stability Classification

- Label: `distribution_mapped_no_threshold_selected`.
- No threshold is promoted or ranked by future outcomes.

## Decision

- `research_mapping_only`.

## User-Facing Summary

- The coarse old score is replaced by an auditable continuous map; causal strategy validation is deferred to a new preregistered layer.
"""


def main() -> None:
    input_hashes = verify_inputs()
    monthly = pd.read_csv(MONTHLY_INPUT, parse_dates=["date"])
    daily = pd.read_csv(DAILY_INPUT, parse_dates=["date"])
    if len(monthly) != 236 or len(daily) != 4_761:
        raise RuntimeError("Unexpected v1 row counts")
    if monthly["date"].min() != pd.Timestamp("2007-01-31") or monthly["date"].max() != pd.Timestamp("2026-08-17"):
        raise RuntimeError("Unexpected monthly date range")
    if daily["date"].min() != pd.Timestamp("2007-01-15") or daily["date"].max() != pd.Timestamp("2026-08-17"):
        raise RuntimeError("Unexpected daily date range")
    required = ["date", "price_close", "gov10y_yield", *FIELDS, *V1_WEIGHT_COLUMNS.values()]
    if monthly[required].isna().any().any() or daily[["date", "price_close", *FIELDS]].isna().any().any():
        raise RuntimeError("Missing frozen valuation inputs")

    monthly_scored = add_continuous_scores(monthly, monthly)
    daily_scored = add_continuous_scores(daily, monthly)
    quantile_grid = make_quantile_grid(monthly)
    old_locations = make_old_threshold_locations(monthly)
    old_crosswalk = make_old_score_crosswalk(monthly_scored)
    price_context = make_price_context(daily_scored, pd.Timestamp(daily_scored["date"].max()))
    threshold_coverage, window_metrics = make_threshold_coverage(
        daily_scored, monthly_scored, price_context
    )
    old_coverage = make_old_fixed_coverage(daily_scored, monthly_scored)
    vintage_quantiles, vintage_old_thresholds, vintage_drift = make_vintage_tables(monthly)
    pearson, spearman = make_correlations(monthly)

    risk_columns = [
        column
        for column in monthly_scored.columns
        if "__risk__" in column or column.startswith("continuous_risk__")
    ]
    allowed_old_scores = {round(value, 2) for value in np.arange(0.0, 2.01, 0.25)}
    stable_rows = threshold_coverage[
        (threshold_coverage["segment"] == "full")
        & (threshold_coverage["half_label"] == "hl10p0")
        & threshold_coverage["mapping_stable"]
    ]
    monotonic_checks = {}
    for half_label in HALF_LIVES:
        for field, config in FIELDS.items():
            ordered = daily_scored.sort_values(field)
            risks = ordered[f"{field}__risk__{half_label}"].to_numpy(dtype=float)
            differences = np.diff(risks)
            monotonic_checks[f"{field}__{half_label}"] = bool(
                (differences >= -1e-12).all()
                if config["high_is_risk"]
                else (differences <= 1e-12).all()
            )
    integrity = {
        "spec_hash_match": sha256(SPEC)
        == SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower(),
        "v1_input_hashes_match": all(sha256(path) == expected for path, expected in EXPECTED_HASHES.items()),
        "monthly_rows": len(monthly_scored),
        "daily_rows": len(daily_scored),
        "monthly_dates_unique_increasing": bool(
            not monthly_scored["date"].duplicated().any()
            and monthly_scored["date"].is_monotonic_increasing
        ),
        "daily_dates_unique_increasing": bool(
            not daily_scored["date"].duplicated().any()
            and daily_scored["date"].is_monotonic_increasing
        ),
        "v1_weight_sums": {
            label: float(monthly[column].sum())
            for label, column in V1_WEIGHT_COLUMNS.items()
        },
        "all_risks_in_unit_interval": bool(
            ((monthly_scored[risk_columns] >= -1e-12) & (monthly_scored[risk_columns] <= 1 + 1e-12)).all().all()
            and ((daily_scored[risk_columns] >= -1e-12) & (daily_scored[risk_columns] <= 1 + 1e-12)).all().all()
        ),
        "risk_direction_monotonic": monotonic_checks,
        "all_risk_directions_pass": all(monotonic_checks.values()),
        "old_fixed_scores_allowed": bool(
            set(round(float(value), 2) for value in daily_scored["old_fixed_risk"].unique())
            <= allowed_old_scores
        ),
        "old_fixed_score_count": int(daily_scored["old_fixed_risk"].nunique()),
        "continuous_unique_daily_values": {
            label: int(daily_scored[f"continuous_risk__{label}"].nunique())
            for label in HALF_LIVES
        },
        "quantile_grid_rows": len(quantile_grid),
        "candidate_count": int(threshold_coverage["candidate"].nunique()),
        "scan_rows": len(threshold_coverage),
        "required_candidate_windows": bool(
            threshold_coverage.groupby("candidate")["segment"].apply(set).eq(set(WINDOWS)).all()
        ),
        "mapping_stable_center_thresholds": stable_rows["risk_threshold"].tolist(),
        "vintage_no_future_rows": bool(
            (pd.to_datetime(vintage_quantiles["history_end"]) <= pd.to_datetime(vintage_quantiles["vintage_date"])).all()
        ),
        "vintage_count": int(vintage_quantiles["vintage_date"].nunique()),
        "status": "passed",
    }
    booleans = [value for value in integrity.values() if isinstance(value, bool)]
    if not all(booleans) or integrity["candidate_count"] != 57 or integrity["scan_rows"] != 285:
        raise RuntimeError(f"Integrity failure: {integrity}")

    OUTPUT.mkdir(parents=True, exist_ok=False)
    setup_font()
    output_daily_columns = ["date", "price_close", *FIELDS, "old_fixed_risk", *risk_columns]
    output_monthly_columns = [
        "date",
        "price_close",
        *FIELDS,
        *V1_WEIGHT_COLUMNS.values(),
        "old_fixed_risk",
        *risk_columns,
    ]
    daily_scored[output_daily_columns].to_csv(
        OUTPUT / "daily_continuous_scores.csv.gz",
        index=False,
        encoding="utf-8-sig",
        compression={"method": "gzip", "mtime": 0},
    )
    monthly_scored[output_monthly_columns].to_csv(
        OUTPUT / "monthly_continuous_scores.csv", index=False, encoding="utf-8-sig"
    )
    quantile_grid.to_csv(OUTPUT / "weighted_quantile_grid.csv", index=False, encoding="utf-8-sig")
    old_locations.to_csv(OUTPUT / "old_threshold_locations.csv", index=False, encoding="utf-8-sig")
    old_crosswalk.to_csv(OUTPUT / "old_fixed_score_crosswalk.csv", index=False, encoding="utf-8-sig")
    threshold_coverage.to_csv(OUTPUT / "continuous_threshold_coverage.csv", index=False, encoding="utf-8-sig")
    old_coverage.to_csv(OUTPUT / "old_fixed_threshold_coverage.csv", index=False, encoding="utf-8-sig")
    price_context.to_csv(OUTPUT / "price_index_window_context.csv", index=False, encoding="utf-8-sig")
    vintage_quantiles.to_csv(OUTPUT / "vintage_quantiles.csv", index=False, encoding="utf-8-sig")
    vintage_old_thresholds.to_csv(OUTPUT / "vintage_old_threshold_risk.csv", index=False, encoding="utf-8-sig")
    vintage_drift.to_csv(OUTPUT / "vintage_drift_diagnostics.csv", index=False, encoding="utf-8-sig")
    pearson.to_csv(OUTPUT / "weighted_pearson_correlation.csv", encoding="utf-8-sig")
    spearman.to_csv(OUTPUT / "weighted_spearman_correlation.csv", encoding="utf-8-sig")
    plot_weighted_distributions(monthly, OUTPUT)
    plot_threshold_coverage(threshold_coverage, OUTPUT)
    plot_old_crosswalk(monthly_scored, OUTPUT)
    plot_vintage_drift(vintage_quantiles, OUTPUT)
    manifest = {
        "version": VERSION,
        "created_at": "2026-08-18",
        "timezone": "Asia/Shanghai",
        "input_hashes": input_hashes,
        "spec_sha256": sha256(SPEC),
        "entrypoint_sha256": sha256(Path(__file__)),
        "monthly_rows": len(monthly_scored),
        "daily_rows": len(daily_scored),
        "status": "research_mapping_only_not_live",
        "future_performance_used": False,
        "execution_model": "not_applicable",
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT / "record.md").write_text(
        make_record(
            monthly_scored,
            daily_scored,
            quantile_grid,
            old_locations,
            old_crosswalk,
            threshold_coverage,
            old_coverage,
            vintage_drift,
            pearson,
        ),
        encoding="utf-8",
    )

    threshold_coverage.to_csv(SCAN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (SCAN / "record.md").write_text(
        make_scan_record(monthly_scored, daily_scored, threshold_coverage), encoding="utf-8"
    )
    scan_meta_path = SCAN / "scan_meta.json"
    scan_meta = json.loads(scan_meta_path.read_text(encoding="utf-8"))
    scan_meta.update(
        {
            "scan_type": "two_parameter_structural_mapping",
            "baseline": {
                "name": "old_fixed_risk",
                "thresholds": [1.50, 1.75, 2.00],
                "role": "coverage_reference_only",
            },
            "candidate_grid": sorted(threshold_coverage["candidate"].unique().tolist()),
            "data_snapshot": {
                "monthly_rows": len(monthly_scored),
                "daily_rows": len(daily_scored),
                "start": daily_scored["date"].min().date().isoformat(),
                "end": daily_scored["date"].max().date().isoformat(),
                "half_lives": list(HALF_LIVES.values()),
                "thresholds": THRESHOLDS.tolist(),
            },
            "cost_model": {
                "applicable": False,
                "reason": "valuation mapping only; no trades or execution",
            },
            "source_hashes": input_hashes,
            "cache_write_risk": "none",
            "warnings": [
                "full-2026 valuation map is non-causal for historical performance and must not be backtested",
                "ann_return/max_dd are underlying index context only and identical within each window",
                "no threshold selected and not approved for live use",
            ],
        }
    )
    scan_meta_path.write_text(
        json.dumps(scan_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\n2026-08-18 | cwd={ROOT} | python {VERSION}.py\n")

    print(
        json.dumps(
            {
                "monthly_rows": len(monthly_scored),
                "daily_rows": len(daily_scored),
                "candidates": threshold_coverage["candidate"].nunique(),
                "stable_center_thresholds": stable_rows["risk_threshold"].tolist(),
                "current_center_risk": float(daily_scored.iloc[-1]["continuous_risk__hl10p0"]),
                "output": str(OUTPUT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
