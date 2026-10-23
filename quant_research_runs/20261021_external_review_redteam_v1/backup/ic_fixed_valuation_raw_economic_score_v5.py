#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "pandas",
# ]
# ///
"""Build an outcome-free CSI500 valuation score in fixed economic units."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_raw_economic_score_v5"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V4_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_economic_threshold_v4"
MONTHLY_INPUT = V4_OUTPUT / "monthly_equal3_threshold_scores.csv"
DAILY_INPUT = V4_OUTPUT / "daily_equal3_threshold_scores.csv.gz"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_raw_economic_score_v5_fixed_unit_threshold"
)
EXPECTED_HASHES = {
    ROOT / "ic_fixed_valuation_economic_threshold_v4.py": (
        "e5f2395c72b626f602881f148847cb3772c4c1cd5e68a41d79db24033cd68cf7"
    ),
    ROOT / "test_ic_fixed_valuation_economic_threshold_v4.py": (
        "0ec83b5c68066bcca9db17e5d77aa32c7d8219c972c7d01b5f0c39442cc6c6a4"
    ),
    ROOT / "docs" / "ic_fixed_valuation_economic_threshold_v4_spec.md": (
        "695ad3a75998eee4a7000e3142c7ad4ad7becb938852266b7570d3bb0b10fa1e"
    ),
    ROOT / "docs" / "ic_fixed_valuation_economic_threshold_v4_postrun_audit.md": (
        "ef10b87464a77819d1acc0da0392959ff066521ce474ccee99f93c5b4d19a26e"
    ),
    MONTHLY_INPUT: "d80f475ee1bfc2886eefadd05bc74861df019d6a401dd290f3239b518f753ebb",
    DAILY_INPUT: "c51942e9b2b53abb70187460bca1878ed2688fcd924ea754b68ae38e2bcaa1fa",
    V4_OUTPUT / "integrity_checks.json": (
        "4512d34aae98efa37ef4b4a1edb8f48d75f36e953288eafd6ea3dc1c17dbcb96"
    ),
    V4_OUTPUT / "output_manifest.json": (
        "73c43f6e5f71de38c3f30e7cea0c48ca02e007a73d7a796e7d800129c7ed6221"
    ),
    V4_OUTPUT / "record.md": (
        "2ae63e9ef88decf619fcd4d4b8d3c1130b7619bd52d9132a5067eca21f603722"
    ),
}
THRESHOLDS = np.round(np.arange(1.40, 2.00 + 0.0001, 0.05), 2)
OLD_THRESHOLDS = (1.50, 1.75, 2.00)
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
ECONOMIC_FIELDS = {
    "pb_aggregate": "PB",
    "erp": "ERP",
    "trailing_dividend_contribution": "过去一年股息贡献",
}
PRESSURE_FIELDS = ("pb_pressure", "erp_pressure", "dividend_pressure")
ANNUALIZATION_DAYS = 252
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
        actual = sha256(output / name)
        if actual != expected:
            raise RuntimeError(f"Upstream manifest mismatch: {output / name}")


def verify_inputs() -> dict[str, str]:
    expected_spec = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sha256(SPEC) != expected_spec:
        raise RuntimeError("Frozen v5 specification mismatch")
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
    verify_manifest(V4_OUTPUT)
    integrity = json.loads(
        (V4_OUTPUT / "integrity_checks.json").read_text(encoding="utf-8")
    )
    if integrity.get("status") != "passed":
        raise RuntimeError("v4 upstream is not authoritative")
    return {str(path.relative_to(ROOT)): sha256(path) for path in EXPECTED_HASHES}


def fixed_components(frame: pd.DataFrame, convention: str) -> pd.DataFrame:
    pb = pd.to_numeric(frame["pb_aggregate"])
    erp = pd.to_numeric(frame["erp"])
    dividend = pd.to_numeric(frame["trailing_dividend_contribution"])
    if convention == "knot":
        pb_pressure = ((pb - 1.50) / 0.50).clip(0.0, 2.0)
        erp_pressure = ((0.045 - erp) / 0.015).clip(0.0, 2.0)
        dividend_pressure = ((0.030 - dividend) / 0.010).clip(0.0, 2.0)
    elif convention == "mid":
        pb_pressure = (0.50 + (pb - 2.00) / 0.50).clip(0.0, 2.0)
        erp_pressure = (0.50 + (0.030 - erp) / 0.015).clip(0.0, 2.0)
        dividend_pressure = (0.50 + (0.020 - dividend) / 0.010).clip(0.0, 2.0)
    else:
        raise ValueError(f"Unknown convention: {convention}")
    result = pd.DataFrame(
        {
            f"pb_pressure_{convention}": pb_pressure,
            f"erp_pressure_{convention}": erp_pressure,
            f"dividend_pressure_{convention}": dividend_pressure,
        },
        index=frame.index,
    )
    result[f"fixed_equal3_{convention}"] = result.mean(axis=1)
    return result


def add_fixed_scores(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for convention in ("knot", "mid"):
        components = fixed_components(result, convention)
        for column in components:
            result[column] = components[column]
    result["old_fixed_risk"] = old_fixed_risk(result)
    return result


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
    return (
        1.0
        if union == 0
        else float(np.logical_and(left_values, right_values).sum() / union)
    )


def window_boundaries(anchor: pd.Timestamp) -> dict[str, pd.Timestamp | None]:
    return {
        "full": None,
        "last_10y": anchor - pd.DateOffset(years=10),
        "last_5y": anchor - pd.DateOffset(years=5),
        "last_3y": anchor - pd.DateOffset(years=3),
        "last_1y": anchor - pd.DateOffset(years=1),
    }


def price_metrics(part: pd.DataFrame) -> dict[str, object]:
    ordered = part.sort_values("date").reset_index(drop=True)
    returns = ordered["price_close"].pct_change().dropna()
    years = (ordered["date"].iloc[-1] - ordered["date"].iloc[0]).days / 365.2425
    nav = ordered["price_close"] / ordered["price_close"].iloc[0]
    return {
        "start": ordered["date"].iloc[0].date().isoformat(),
        "end": ordered["date"].iloc[-1].date().isoformat(),
        "rows": len(ordered),
        "ann_return": float(
            (ordered["price_close"].iloc[-1] / ordered["price_close"].iloc[0])
            ** (1 / years)
            - 1
        ),
        "ann_vol": float(returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS)),
        "sharpe_repo": float(
            returns.mean() / returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS)
        ),
        "max_dd": float((nav / nav.cummax() - 1).min()),
    }


def make_price_context(daily: pd.DataFrame) -> pd.DataFrame:
    anchor = pd.Timestamp(daily["date"].max())
    rows = []
    for segment, boundary in window_boundaries(anchor).items():
        part = daily if boundary is None else daily[daily["date"] >= boundary]
        rows.append({"segment": segment, **price_metrics(part)})
    return pd.DataFrame(rows)


def quantile_fields(part: pd.DataFrame, prefix: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for field in ECONOMIC_FIELDS:
        for label, q in (("p25", 0.25), ("median", 0.50), ("p75", 0.75)):
            result[f"{prefix}_{field}_{label}"] = (
                float(part[field].quantile(q)) if not part.empty else math.nan
            )
    return result


def make_economic_boundary(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    score = monthly["fixed_equal3_knot"]
    pressure_cols = [f"{name}_knot" for name in PRESSURE_FIELDS]
    for threshold in THRESHOLDS:
        active = score.ge(threshold)
        local = (score - threshold).abs().le(0.05 + 1e-12)
        crossing = active & ~active.shift(1, fill_value=False)
        active_frame = monthly.loc[active]
        local_frame = monthly.loc[local]
        crossing_frame = monthly.loc[crossing]
        factor_count = monthly.loc[active, pressure_cols].ge(1.0).sum(axis=1)
        row: dict[str, object] = {
            "threshold": float(threshold),
            "local_band_low": float(threshold - 0.05),
            "local_band_high": float(threshold + 0.05),
            "local_months": int(local.sum()),
            "active_months": int(active.sum()),
            "upward_crossings": int(crossing.sum()),
            "active_share_at_least2_components_ge_1": (
                float((factor_count >= 2).mean()) if active.any() else math.nan
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


def make_convention_sensitivity(
    monthly: pd.DataFrame, daily: pd.DataFrame
) -> pd.DataFrame:
    monthly_recent = monthly["date"] >= monthly["date"].max() - pd.DateOffset(years=10)
    daily_recent = daily["date"] >= daily["date"].max() - pd.DateOffset(years=10)
    rows = []
    for threshold in THRESHOLDS:
        paired = float(threshold - 0.50)
        primary_month = monthly["fixed_equal3_knot"].ge(threshold)
        paired_month = monthly["fixed_equal3_mid"].ge(paired)
        primary_day = daily["fixed_equal3_knot"].ge(threshold)
        paired_day = daily["fixed_equal3_mid"].ge(paired)
        rows.append(
            {
                "threshold": float(threshold),
                "paired_mid_threshold": paired,
                "full_monthly_jaccard": jaccard(primary_month, paired_month),
                "recent10_monthly_jaccard": jaccard(
                    primary_month.loc[monthly_recent], paired_month.loc[monthly_recent]
                ),
                "full_daily_jaccard": jaccard(primary_day, paired_day),
                "recent10_daily_jaccard": jaccard(
                    primary_day.loc[daily_recent], paired_day.loc[daily_recent]
                ),
                "full_monthly_primary_ratio": float(primary_month.mean()),
                "full_monthly_mid_ratio": float(paired_month.mean()),
                "recent10_monthly_primary_ratio": float(
                    primary_month.loc[monthly_recent].mean()
                ),
                "recent10_monthly_mid_ratio": float(
                    paired_month.loc[monthly_recent].mean()
                ),
                "recent10_monthly_ratio_abs_diff": float(
                    abs(
                        primary_month.loc[monthly_recent].mean()
                        - paired_month.loc[monthly_recent].mean()
                    )
                ),
                "current_primary_active": bool(primary_day.iloc[-1]),
                "current_mid_active": bool(paired_day.iloc[-1]),
            }
        )
    return pd.DataFrame(rows)


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
    rows: list[dict[str, object]] = []
    for vintage in vintage_dates(monthly):
        history = monthly[monthly["date"] <= vintage].copy().reset_index(drop=True)
        recalculated = add_fixed_scores(
            history[
                [
                    "date",
                    "price_close",
                    "pb_aggregate",
                    "erp",
                    "trailing_dividend_contribution",
                ]
            ]
        )
        knot_error = float(
            np.max(
                np.abs(recalculated["fixed_equal3_knot"] - history["fixed_equal3_knot"])
            )
        )
        mid_error = float(
            np.max(
                np.abs(recalculated["fixed_equal3_mid"] - history["fixed_equal3_mid"])
            )
        )
        states_match = all(
            np.array_equal(
                recalculated["fixed_equal3_knot"].ge(threshold),
                history["fixed_equal3_knot"].ge(threshold),
            )
            for threshold in THRESHOLDS
        )
        rows.append(
            {
                "vintage_date": vintage.date().isoformat(),
                "history_start": history["date"].min().date().isoformat(),
                "history_end": history["date"].max().date().isoformat(),
                "history_months": len(history),
                "knot_score_max_abs_error": knot_error,
                "mid_score_max_abs_error": mid_error,
                "all_threshold_states_match": states_match,
                "future_rows_used": False,
            }
        )
    return pd.DataFrame(rows)


def recent_episode_count(active: pd.Series, mask: pd.Series) -> int:
    part = active.loc[mask].reset_index(drop=True)
    return episode_stats(part)[0] if not part.empty else 0


def select_platform(table: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    table = table.copy()
    bands: list[list[int]] = []
    current_band: list[int] = []
    previous: float | None = None
    for index, row in table.iterrows():
        threshold = float(row["threshold"])
        if bool(row["all_individual_gates_pass"]):
            if previous is None or np.isclose(threshold - previous, 0.05):
                current_band.append(index)
            else:
                if current_band:
                    bands.append(current_band)
                current_band = [index]
            previous = threshold
        else:
            if current_band:
                bands.append(current_band)
            current_band = []
            previous = None
    if current_band:
        bands.append(current_band)
    valid_bands = [indices for indices in bands if len(indices) >= 3]
    table["band_id"] = 0
    for band_id, indices in enumerate(valid_bands, start=1):
        table.loc[indices, "band_id"] = band_id
    table["in_selected_band"] = False
    table["is_design_center"] = False
    if not valid_bands:
        return table, {
            "platform_found": False,
            "selected_band_low": None,
            "selected_band_high": None,
            "selected_band_points": 0,
            "design_center_threshold": None,
            "threshold_is_live_approved": False,
        }
    selected_indices = max(
        valid_bands,
        key=lambda indices: (
            len(indices),
            float(table.loc[indices, "threshold"].min()),
        ),
    )
    center_index = selected_indices[(len(selected_indices) - 1) // 2]
    table.loc[selected_indices, "in_selected_band"] = True
    table.loc[center_index, "is_design_center"] = True
    return table, {
        "platform_found": True,
        "selected_band_low": float(table.loc[selected_indices, "threshold"].min()),
        "selected_band_high": float(table.loc[selected_indices, "threshold"].max()),
        "selected_band_points": len(selected_indices),
        "design_center_threshold": float(table.loc[center_index, "threshold"]),
        "threshold_is_live_approved": False,
    }


def make_threshold_selection(
    monthly: pd.DataFrame,
    daily: pd.DataFrame,
    economic: pd.DataFrame,
    sensitivity: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    recent_mask = monthly["date"] >= monthly["date"].max() - pd.DateOffset(years=10)
    economic_index = economic.set_index("threshold")
    sensitivity_index = sensitivity.set_index("threshold")
    rows = []
    for threshold in THRESHOLDS:
        active = monthly["fixed_equal3_knot"].ge(threshold)
        full_episodes, full_longest = episode_stats(active)
        recent_episodes = recent_episode_count(active, recent_mask)
        recent_ratio = float(active.loc[recent_mask].mean())
        econ = economic_index.loc[threshold]
        sense = sensitivity_index.loc[threshold]
        gates = {
            "gate_episode_count": bool(full_episodes >= 5 and recent_episodes >= 2),
            "gate_recent_tail_coverage": bool(0.05 <= recent_ratio <= 0.30),
            "gate_broad_factor_evidence": bool(
                econ["active_share_at_least2_components_ge_1"] >= 0.90
            ),
            "gate_local_boundary_sample": bool(int(econ["local_months"]) >= 8),
            "gate_convention_full_jaccard": bool(sense["full_monthly_jaccard"] >= 0.85),
            "gate_convention_recent_jaccard_and_coverage": bool(
                sense["recent10_monthly_jaccard"] >= 0.85
                and sense["recent10_monthly_ratio_abs_diff"] <= 0.05
            ),
            "gate_convention_current_state": bool(
                sense["current_primary_active"] == sense["current_mid_active"]
            ),
        }
        rows.append(
            {
                "threshold": float(threshold),
                "paired_mid_threshold": float(sense["paired_mid_threshold"]),
                "full_monthly_activation": float(active.mean()),
                "recent_10y_monthly_activation": recent_ratio,
                "full_monthly_episodes": full_episodes,
                "full_longest_active_months": full_longest,
                "recent_10y_monthly_episodes": recent_episodes,
                "local_months": int(econ["local_months"]),
                "broad_factor_share": float(
                    econ["active_share_at_least2_components_ge_1"]
                ),
                "convention_full_monthly_jaccard": float(sense["full_monthly_jaccard"]),
                "convention_recent10_monthly_jaccard": float(
                    sense["recent10_monthly_jaccard"]
                ),
                "convention_recent10_activation_abs_diff": float(
                    sense["recent10_monthly_ratio_abs_diff"]
                ),
                "current_active": bool(
                    daily["fixed_equal3_knot"].iloc[-1] >= threshold
                ),
                **gates,
                "all_individual_gates_pass": bool(all(gates.values())),
            }
        )
    return select_platform(pd.DataFrame(rows))


def make_clipping_summary(daily: pd.DataFrame, monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    anchor = daily["date"].max()
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        scopes = {
            "full": frame,
            "last_10y": frame[frame["date"] >= anchor - pd.DateOffset(years=10)],
        }
        for scope, part in scopes.items():
            for convention in ("knot", "mid"):
                for component in PRESSURE_FIELDS:
                    values = part[f"{component}_{convention}"]
                    rows.append(
                        {
                            "frequency": frequency,
                            "scope": scope,
                            "convention": convention,
                            "component": component,
                            "rows": len(values),
                            "clipped_at_zero_ratio": float(values.eq(0).mean()),
                            "clipped_at_two_ratio": float(values.eq(2).mean()),
                            "strictly_continuous_ratio": float(
                                values.between(0, 2, inclusive="neither").mean()
                            ),
                        }
                    )
    return pd.DataFrame(rows)


def make_old_fixed_diagnostic(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> pd.DataFrame:
    anchor = daily["date"].max()
    scopes = {
        "full_monthly": monthly,
        "full_daily": daily,
        "recent10_monthly": monthly[
            monthly["date"] >= anchor - pd.DateOffset(years=10)
        ],
        "recent10_daily": daily[daily["date"] >= anchor - pd.DateOffset(years=10)],
    }
    rows = []
    for threshold in OLD_THRESHOLDS:
        for scope, part in scopes.items():
            active = part["old_fixed_risk"].ge(threshold)
            episodes, longest = episode_stats(active)
            rows.append(
                {
                    "old_fixed_threshold": threshold,
                    "scope": scope,
                    "rows": len(part),
                    "activation_ratio": float(active.mean()),
                    "episodes": episodes,
                    "longest_active_rows": longest,
                }
            )
    return pd.DataFrame(rows)


def make_current_state(
    daily: pd.DataFrame, decision: dict[str, object]
) -> pd.DataFrame:
    current = daily.iloc[-1]
    center = decision["design_center_threshold"]
    return pd.DataFrame(
        [
            {
                "date": current["date"].date().isoformat(),
                "pb_aggregate": float(current["pb_aggregate"]),
                "erp": float(current["erp"]),
                "trailing_dividend_contribution": float(
                    current["trailing_dividend_contribution"]
                ),
                "pb_pressure_knot": float(current["pb_pressure_knot"]),
                "erp_pressure_knot": float(current["erp_pressure_knot"]),
                "dividend_pressure_knot": float(current["dividend_pressure_knot"]),
                "fixed_equal3_knot": float(current["fixed_equal3_knot"]),
                "fixed_equal3_mid": float(current["fixed_equal3_mid"]),
                "old_fixed_risk": float(current["old_fixed_risk"]),
                "design_center_threshold": center,
                "design_center_active": bool(
                    center is not None and current["fixed_equal3_knot"] >= float(center)
                ),
            }
        ]
    )


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
    for threshold in THRESHOLDS:
        selected = selection_index.loc[threshold]
        candidate = f"fixed_equal3_knot_ge_{round(threshold * 100):03d}"
        for context in price_context.itertuples(index=False):
            boundary = boundaries[context.segment]
            daily_part = daily if boundary is None else daily[daily["date"] >= boundary]
            monthly_part = (
                monthly if boundary is None else monthly[monthly["date"] >= boundary]
            )
            month_active = monthly_part["fixed_equal3_knot"].ge(threshold)
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
                    "threshold": float(threshold),
                    "activation_day_ratio": float(
                        daily_part["fixed_equal3_knot"].ge(threshold).mean()
                    ),
                    "activation_month_ratio": float(month_active.mean()),
                    "monthly_episodes": episodes,
                    "longest_active_months": longest,
                    "current_active": bool(selected["current_active"]),
                    "all_individual_gates_pass": bool(
                        selected["all_individual_gates_pass"]
                    ),
                    "in_selected_band": bool(selected["in_selected_band"]),
                    "is_design_center": bool(selected["is_design_center"]),
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
            "threshold": first["threshold"],
            "current_active": first["current_active"],
            "all_individual_gates_pass": first["all_individual_gates_pass"],
            "in_selected_band": first["in_selected_band"],
            "is_design_center": first["is_design_center"],
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


def setup_font() -> None:
    if FONT_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_PATH))
        plt.rcParams["font.family"] = font_manager.FontProperties(
            fname=str(FONT_PATH)
        ).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def plot_threshold_structure(
    selection: pd.DataFrame, decision: dict[str, object], output: Path
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    axes[0].plot(
        selection["threshold"],
        selection["full_monthly_activation"] * 100,
        marker="o",
        label="全样本",
    )
    axes[0].plot(
        selection["threshold"],
        selection["recent_10y_monthly_activation"] * 100,
        marker="o",
        label="最近10年",
    )
    axes[0].axhspan(5, 30, color="#70AD47", alpha=0.12)
    axes[0].set_ylabel("月度覆盖率（%）")
    axes[0].set_title("固定经济总分的高估覆盖")
    axes[0].legend(frameon=False)
    axes[1].plot(
        selection["threshold"],
        selection["full_monthly_episodes"],
        marker="o",
        label="全样本",
    )
    axes[1].plot(
        selection["threshold"],
        selection["recent_10y_monthly_episodes"],
        marker="o",
        label="最近10年",
    )
    axes[1].axhline(5, color="#4472C4", linestyle="--", alpha=0.6)
    axes[1].axhline(2, color="#ED7D31", linestyle="--", alpha=0.6)
    axes[1].set_ylabel("独立启动段数")
    axes[1].set_title("事件重复性")
    axes[1].legend(frameon=False)
    for ax in axes:
        ax.set_xlabel("主定义阈值")
        ax.grid(alpha=0.2)
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
    fig.suptitle("中证500固定经济单位估值：结构平台扫描", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "threshold_structure.png", dpi=180)
    plt.close(fig)


def plot_convention_sensitivity(sensitivity: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    axes[0].plot(
        sensitivity["threshold"],
        sensitivity["full_monthly_jaccard"],
        marker="o",
        label="全样本月度",
    )
    axes[0].plot(
        sensitivity["threshold"],
        sensitivity["recent10_monthly_jaccard"],
        marker="o",
        label="最近10年月度",
    )
    axes[0].axhline(0.85, color="#C00000", linestyle="--")
    axes[0].set_ylim(0.75, 1.01)
    axes[0].set_ylabel("Jaccard")
    axes[0].set_title("整数锚点与边界中点的状态一致性")
    axes[0].legend(frameon=False)
    axes[1].plot(
        sensitivity["threshold"],
        sensitivity["recent10_monthly_primary_ratio"] * 100,
        marker="o",
        label="整数锚点",
    )
    axes[1].plot(
        sensitivity["threshold"],
        sensitivity["recent10_monthly_mid_ratio"] * 100,
        marker="o",
        label="边界中点配对线",
    )
    axes[1].set_ylabel("最近10年月度覆盖率（%）")
    axes[1].set_title("配对口径覆盖率")
    axes[1].legend(frameon=False)
    for ax in axes:
        ax.set_xlabel("主定义阈值")
        ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(output / "convention_sensitivity.png", dpi=180)
    plt.close(fig)


def plot_economic_boundary(economic: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.3))
    for ax, (field, label) in zip(axes, ECONOMIC_FIELDS.items(), strict=True):
        scale = 100 if field != "pb_aggregate" else 1
        ax.plot(
            economic["threshold"],
            economic[f"boundary_{field}_median"] * scale,
            marker="o",
        )
        ax.fill_between(
            economic["threshold"],
            economic[f"boundary_{field}_p25"] * scale,
            economic[f"boundary_{field}_p75"] * scale,
            alpha=0.18,
        )
        ax.set_title(label)
        ax.set_xlabel("主定义阈值")
        ax.grid(alpha=0.2)
        if field != "pb_aggregate":
            ax.set_ylabel("百分比点")
    fig.suptitle("固定经济总分边界附近的原始经济量", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "economic_boundary_map.png", dpi=180)
    plt.close(fig)


def plot_score_history(monthly: pd.DataFrame, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 5.6))
    ax.plot(
        monthly["date"],
        monthly["fixed_equal3_knot"],
        label="整数锚点主定义",
        linewidth=2,
    )
    ax.plot(
        monthly["date"], monthly["fixed_equal3_mid"], label="边界中点定义", alpha=0.85
    )
    ax.set_ylim(-0.03, 2.05)
    ax.set_ylabel("固定经济总分")
    ax.set_title("中证500固定经济单位估值历史")
    ax.grid(alpha=0.2)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output / "fixed_score_history.png", dpi=180)
    plt.close(fig)


def format_economic(field: str, value: float) -> str:
    return f"{value:.2%}" if field != "pb_aggregate" else f"{value:.2f}"


def make_record(
    selection: pd.DataFrame,
    decision: dict[str, object],
    economic: pd.DataFrame,
    sensitivity: pd.DataFrame,
    current: pd.DataFrame,
    clipping: pd.DataFrame,
) -> str:
    if decision["platform_found"]:
        center = float(decision["design_center_threshold"])
        center_row = selection[np.isclose(selection["threshold"], center)].iloc[0]
        center_econ = economic[np.isclose(economic["threshold"], center)].iloc[0]
        center_sense = sensitivity[np.isclose(sensitivity["threshold"], center)].iloc[0]
        platform_text = (
            f"通过的连续结构平台为 **{decision['selected_band_low']:.2f}—"
            f"{decision['selected_band_high']:.2f}**，共{decision['selected_band_points']}个点；"
            f"机械设计中心为 **{center:.2f}**。"
        )
        center_text = f"""
### 设计中心 {center:.2f}

- 全样本/最近10年月度覆盖：{center_row.full_monthly_activation:.2%}/{center_row.recent_10y_monthly_activation:.2%}；
- 全样本/最近10年独立启动段：{int(center_row.full_monthly_episodes)}/{int(center_row.recent_10y_monthly_episodes)}；
- 启动月至少两个分量达到1分：{center_row.broad_factor_share:.2%}；
- 整数锚点与配对边界中点口径的全样本/最近10年Jaccard：{center_sense.full_monthly_jaccard:.2%}/{center_sense.recent10_monthly_jaccard:.2%}；
- 局部经济边界月数：{int(center_econ.local_months)}。

| 经济变量 | 边界25% | 边界中位数 | 边界75% | 向上穿越月中位数 |
| --- | ---: | ---: | ---: | ---: |
| PB | {format_economic("pb_aggregate", center_econ.boundary_pb_aggregate_p25)} | {format_economic("pb_aggregate", center_econ.boundary_pb_aggregate_median)} | {format_economic("pb_aggregate", center_econ.boundary_pb_aggregate_p75)} | {format_economic("pb_aggregate", center_econ.crossing_pb_aggregate_median)} |
| ERP | {format_economic("erp", center_econ.boundary_erp_p25)} | {format_economic("erp", center_econ.boundary_erp_median)} | {format_economic("erp", center_econ.boundary_erp_p75)} | {format_economic("erp", center_econ.crossing_erp_median)} |
| 过去一年股息贡献 | {format_economic("trailing_dividend_contribution", center_econ.boundary_trailing_dividend_contribution_p25)} | {format_economic("trailing_dividend_contribution", center_econ.boundary_trailing_dividend_contribution_median)} | {format_economic("trailing_dividend_contribution", center_econ.boundary_trailing_dividend_contribution_p75)} | {format_economic("trailing_dividend_contribution", center_econ.crossing_trailing_dividend_contribution_median)} |
"""
    else:
        platform_text = "没有任何至少3个相邻点的平台通过全部预注册门槛，因此本版不选择固定高估门槛。"
        center_text = ""
    current_row = current.iloc[0]
    display = selection[
        [
            "threshold",
            "full_monthly_activation",
            "recent_10y_monthly_activation",
            "full_monthly_episodes",
            "recent_10y_monthly_episodes",
            "convention_recent10_monthly_jaccard",
            "all_individual_gates_pass",
        ]
    ]
    table_rows = [
        f"| {row.threshold:.2f} | {row.full_monthly_activation:.2%} | "
        f"{row.recent_10y_monthly_activation:.2%} | {int(row.full_monthly_episodes)} | "
        f"{int(row.recent_10y_monthly_episodes)} | "
        f"{row.convention_recent10_monthly_jaccard:.2%} | "
        f"{'是' if row.all_individual_gates_pass else '否'} |"
        for row in display.itertuples(index=False)
    ]
    continuous = clipping[
        (clipping["frequency"] == "monthly")
        & (clipping["scope"] == "full")
        & (clipping["convention"] == "knot")
    ]["strictly_continuous_ratio"].mean()
    return f"""# 中证500固定经济单位连续估值 v5

## 结论

{platform_text}

本版已经彻底移除历史分位：同一天的 PB、ERP、股息输入，无论从2007年、2015年还是任意更晚时点开始计算，固定分数都相同。本版没有查看策略收益、IC贴水或PUT损益来选择门槛。

{center_text}

## 全网格

| 阈值 | 全样本月覆盖 | 最近10年月覆盖 | 全样本段数 | 最近10年段数 | 口径Jaccard | 单点全通过 |
| ---: | ---: | ---: | ---: | ---: | ---: | :---: |
{chr(10).join(table_rows)}

## 当前状态（{current_row.date}）

- PB {current_row.pb_aggregate:.2f}，固定压力 {current_row.pb_pressure_knot:.3f}；
- ERP {current_row.erp:.2%}，固定压力 {current_row.erp_pressure_knot:.3f}；
- 过去一年股息贡献 {current_row.trailing_dividend_contribution:.2%}，固定压力 {current_row.dividend_pressure_knot:.3f}；
- 主定义 `fixed_equal3_knot`：**{current_row.fixed_equal3_knot:.3f}**；边界中点定义：{current_row.fixed_equal3_mid:.3f}；旧离散固定分：{current_row.old_fixed_risk:.2f}；
- 是否达到设计中心：{"是" if current_row.design_center_active else "否"}。

## 定义解释

- 主定义把旧切换点解释成连续分的整数锚点：PB 2.00/2.50、ERP 3.00%/1.50%、股息2.00%/1.00%分别对应1/2分；
- 边界中点定义只是预注册敏感性对照，并以主阈值减0.50机械配对，没有事后找最相似阈值；
- 全样本月度三分量处于严格连续区间的平均比例为{continuous:.2%}，其余观测触及0或2的截断；
- 总分是一张三维边界面，边界中位数不是三个必须同时满足的硬条件。

## 边界

- 价格指数收益字段只用于满足扫描审计协议，同一窗口所有候选完全相同；
- 本版只选择估值本体的研究平台，不检验任何保护策略损益；
- 状态为 `RESEARCH_ONLY_NOT_LIVE_APPROVED`，不得据此自动或人工下单。
"""


def make_scan_record(decision: dict[str, object]) -> str:
    decision_text = (
        f"selected structural band {decision['selected_band_low']:.2f}-"
        f"{decision['selected_band_high']:.2f}; center "
        f"{decision['design_center_threshold']:.2f}"
        if decision["platform_found"]
        else "no structural fixed-unit threshold platform passed"
    )
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Version: `{VERSION}`
- Entrypoint: `ic_fixed_valuation_raw_economic_score_v5.py`
- Scope: outcome-free CSI500 fixed-unit valuation threshold scan.

## Grid and Evidence

- Primary score thresholds 1.40-2.00 by 0.05; 13 candidates and five windows; 65 long rows.
- Paired midpoint-convention thresholds are mechanically primary minus 0.50 and are diagnostic only.
- Sample: 2007-01-15 to 2026-08-17; 4,761 daily and 236 month-end rows.
- Price-index return fields are context only and identical across candidates within each segment.
- No trade or cost model applies because this definition audit contains no trades.

## Decision

- Decision: {decision_text}.
- Stability label: `{"structural_platform" if decision["platform_found"] else "no_structural_platform"}`.
- No strategy or live threshold is promoted.
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
            "candidate_count": int(long["candidate"].nunique()),
            "window_rows": len(long),
            "decision": decision,
            "stability_label": (
                "structural_platform"
                if decision["platform_found"]
                else "no_structural_platform"
            ),
            "outputs": {
                "record": str(SCAN / "record.md"),
                "scan_summary": str(SCAN / "scan_summary.csv"),
                "window_metrics": str(SCAN / "window_metrics.csv"),
                "scan_meta": str(SCAN / "scan_meta.json"),
                "command_log": str(SCAN / "command_log.txt"),
            },
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\nuv run ic_fixed_valuation_raw_economic_score_v5.py\n")


def main() -> None:
    input_hashes = verify_inputs()
    monthly = add_fixed_scores(pd.read_csv(MONTHLY_INPUT, parse_dates=["date"]))
    daily = add_fixed_scores(pd.read_csv(DAILY_INPUT, parse_dates=["date"]))
    if len(monthly) != 236 or len(daily) != 4_761:
        raise RuntimeError("Unexpected sample size")
    required = ["pb_aggregate", "erp", "trailing_dividend_contribution", "price_close"]
    if monthly[required].isna().any().any() or daily[required].isna().any().any():
        raise RuntimeError("Missing values in fixed-score inputs")

    economic = make_economic_boundary(monthly)
    sensitivity = make_convention_sensitivity(monthly, daily)
    vintage = make_vintage_invariance(monthly)
    selection, decision = make_threshold_selection(
        monthly, daily, economic, sensitivity
    )
    clipping = make_clipping_summary(daily, monthly)
    old_diagnostic = make_old_fixed_diagnostic(daily, monthly)
    current = make_current_state(daily, decision)
    price_context = make_price_context(daily)
    scan_long, scan_wide = make_threshold_scan(daily, monthly, selection, price_context)

    if len(economic) != 13 or len(sensitivity) != 13 or len(vintage) != 11:
        raise RuntimeError("Structural output size mismatch")
    if scan_long["candidate"].nunique() != 13 or len(scan_long) != 65:
        raise RuntimeError("Scan grid size mismatch")
    if not scan_long.groupby("segment")["ann_return"].nunique().eq(1).all():
        raise RuntimeError("Candidate price context differs within a window")
    if (
        max(
            vintage["knot_score_max_abs_error"].max(),
            vintage["mid_score_max_abs_error"].max(),
        )
        > 1e-12
    ):
        raise RuntimeError("Fixed score changed across historical vintages")
    if (
        not vintage["all_threshold_states_match"].all()
        or vintage["future_rows_used"].any()
    ):
        raise RuntimeError("Historical vintage invariance failed")

    STAGING.mkdir(parents=True)
    monthly.to_csv(STAGING / "monthly_fixed_economic_scores.csv", index=False)
    daily.to_csv(
        STAGING / "daily_fixed_economic_scores.csv.gz", index=False, compression="gzip"
    )
    economic.to_csv(STAGING / "economic_boundary_map.csv", index=False)
    sensitivity.to_csv(STAGING / "convention_sensitivity.csv", index=False)
    vintage.to_csv(STAGING / "vintage_formula_invariance.csv", index=False)
    selection.to_csv(STAGING / "threshold_selection.csv", index=False)
    clipping.to_csv(STAGING / "component_clipping_summary.csv", index=False)
    old_diagnostic.to_csv(STAGING / "old_fixed_diagnostic.csv", index=False)
    current.to_csv(STAGING / "current_fixed_state.csv", index=False)
    price_context.to_csv(STAGING / "underlying_price_context.csv", index=False)
    scan_long.to_csv(STAGING / "threshold_scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "threshold_window_metrics.csv", index=False)

    setup_font()
    plot_threshold_structure(selection, decision, STAGING)
    plot_convention_sensitivity(sensitivity, STAGING)
    plot_economic_boundary(economic, STAGING)
    plot_score_history(monthly, STAGING)
    (STAGING / "record.md").write_text(
        make_record(selection, decision, economic, sensitivity, current, clipping),
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
        },
        "candidate_count": int(scan_long["candidate"].nunique()),
        "scan_rows": len(scan_long),
        "economic_rows": len(economic),
        "sensitivity_rows": len(sensitivity),
        "vintage_rows": len(vintage),
        "fixed_formula_vintage_max_abs_error": float(
            max(
                vintage["knot_score_max_abs_error"].max(),
                vintage["mid_score_max_abs_error"].max(),
            )
        ),
        "all_vintage_threshold_states_match": bool(
            vintage["all_threshold_states_match"].all()
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
    write_scan_files(scan_long, scan_wide, decision)
    (SCAN / "record.md").write_text(make_scan_record(decision), encoding="utf-8")
    shutil.move(str(STAGING), str(OUTPUT))
    print(json.dumps(integrity, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
