from __future__ import annotations

import hashlib
import json
import math
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

from ic_fixed_valuation_factor_structure_v3_1 import (
    component_col,
    prepare_base,
    raw_col,
    score_one_half,
)
from ic_fixed_valuation_weighted_distribution_v2 import (
    episode_stats,
    jaccard,
    price_metrics,
    weighted_quantile,
)


ROOT = Path(__file__).resolve().parent
VERSION = "ic_fixed_valuation_economic_threshold_v4"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V1_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_time_weight_v1"
V3_OUTPUT = ROOT / "outputs" / "ic_fixed_valuation_factor_structure_v3_1"
MONTHLY_INPUT = V3_OUTPUT / "monthly_factor_structure_scores.csv"
DAILY_INPUT = V3_OUTPUT / "daily_factor_structure_scores.csv.gz"
BASE_MONTHLY_INPUT = V1_OUTPUT / "monthly_weight_map.csv"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_500_ic_fixed_valuation_economic_threshold_v4_valuation_body_equal3_raw_risk_threshold"
)
EXPECTED_HASHES = {
    ROOT / "ic_fixed_valuation_factor_structure_v3_1.py": "1a94b74d1bae0e213b6a3cb4e9b76faaa034934d90825faaed3027ce642474cf",
    ROOT / "ic_fixed_valuation_weighted_distribution_v2.py": "5b34965ef99e076a3cb42e10db74c8f4b9fe13da13b4157d0f5b7229c9d1ce5c",
    ROOT / "docs" / "ic_fixed_valuation_factor_structure_v3_1_spec.md": "6165d5d2c84d22cddcd731ed87e82bb046706690d429aab58fb095f35de92ebb",
    ROOT / "docs" / "ic_fixed_valuation_factor_structure_v3_1_postrun_audit.md": "173159bba932f5237577e1d2de05b843e2b0b24f687de9ada26115641bb18069",
    MONTHLY_INPUT: "fadd75499b394df530254941a221bddeab5eac81a858fd7f35958779e000f540",
    DAILY_INPUT: "4bc724585aa90e1d456779d013af5156a16d39397ba5ff8a44ccef974a3a447d",
    V3_OUTPUT / "integrity_checks.json": "085db4e2f44fc919b33f957a101a37094f156fb03e23e6f1fc79c7b6634054e9",
    V3_OUTPUT / "output_manifest.json": "2e4309230e021ae8d1a7e97e35860251738b27d90816489ddac36401cab2e8fe",
    V3_OUTPUT / "record.md": "73cabc3623d1430f9c0fa4d0af5d368f728b22fafe7f27f4887e85f1ba73a702",
    BASE_MONTHLY_INPUT: "3ff4a866e090722cd23666f65af38d0d397bf627454491bfe9e55a554cc01e0a",
}
HALF_LIVES = {"hl07p5": 7.5, "hl10p0": 10.0, "hl12p0": 12.0}
WEIGHT_COLUMNS = {
    "hl07p5": "weight__exp_hl07p5",
    "hl10p0": "weight__exp_hl10p0",
    "hl12p0": "weight__exp_hl12p0",
}
THRESHOLDS = np.round(np.arange(0.50, 0.90 + 0.0001, 0.01), 2)
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
OLD_THRESHOLDS = (1.50, 1.75, 2.00)
ECONOMIC_FIELDS = {
    "pb_aggregate": "PB",
    "erp": "ERP",
    "trailing_dividend_contribution": "股息贡献",
}
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
        raise RuntimeError("Frozen v4 specification mismatch")
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
    verify_manifest(V3_OUTPUT)
    integrity = json.loads((V3_OUTPUT / "integrity_checks.json").read_text(encoding="utf-8"))
    if integrity.get("status") != "passed" or integrity["selection"]["design_center"] != "equal3":
        raise RuntimeError("v3.1 is not an authoritative equal3 design center")
    return {str(path.relative_to(ROOT)): sha256(path) for path in EXPECTED_HASHES}


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


def conditional_quantiles(
    part: pd.DataFrame, weights: pd.Series, field: str, prefix: str
) -> dict[str, float]:
    if part.empty:
        return {f"{prefix}_{name}": math.nan for name in ("p25", "median", "p75")}
    return {
        f"{prefix}_p25": weighted_quantile(part[field], weights, 0.25),
        f"{prefix}_median": weighted_quantile(part[field], weights, 0.50),
        f"{prefix}_p75": weighted_quantile(part[field], weights, 0.75),
    }


def make_economic_boundary(monthly: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for half_label, half_life in HALF_LIVES.items():
        score_col = raw_col("equal3", half_label)
        component_cols = [component_col(name, half_label) for name in ("pb", "erp", "dividend")]
        weight_col = WEIGHT_COLUMNS[half_label]
        for threshold in THRESHOLDS:
            active = monthly[score_col] >= threshold
            local = (monthly[score_col] - threshold).abs() <= 0.02 + 1e-12
            crossing = active & ~active.shift(1, fill_value=False)
            active_frame = monthly.loc[active]
            local_frame = monthly.loc[local]
            crossing_frame = monthly.loc[crossing]
            row: dict[str, object] = {
                "half_label": half_label,
                "half_life_years": half_life,
                "threshold": float(threshold),
                "local_band_low": float(threshold - 0.02),
                "local_band_high": float(threshold + 0.02),
                "local_months": int(local.sum()),
                "active_months": int(active.sum()),
                "upward_crossings": int(crossing.sum()),
            }
            if active.any():
                counts_050 = monthly.loc[active, component_cols].ge(0.50).sum(axis=1)
                counts_060 = monthly.loc[active, component_cols].ge(0.60).sum(axis=1)
                row.update(
                    {
                        "active_share_at_least2_components_ge_050": float((counts_050 >= 2).mean()),
                        "active_share_at_least2_components_ge_060": float((counts_060 >= 2).mean()),
                        "active_share_all3_components_ge_050": float((counts_050 == 3).mean()),
                    }
                )
            else:
                row.update(
                    {
                        "active_share_at_least2_components_ge_050": math.nan,
                        "active_share_at_least2_components_ge_060": math.nan,
                        "active_share_all3_components_ge_050": math.nan,
                    }
                )
            for field in ECONOMIC_FIELDS:
                row.update(
                    conditional_quantiles(
                        local_frame,
                        local_frame[weight_col],
                        field,
                        f"boundary_{field}",
                    )
                )
                row.update(
                    conditional_quantiles(
                        active_frame,
                        active_frame[weight_col],
                        field,
                        f"active_{field}",
                    )
                )
                row[f"crossing_{field}_median"] = (
                    float(crossing_frame[field].median()) if not crossing_frame.empty else math.nan
                )
            rows.append(row)
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


def make_vintage_stability(
    base_monthly: pd.DataFrame, final_monthly: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for vintage in vintage_dates(base_monthly):
        history = base_monthly[base_monthly["date"] <= vintage].copy()
        ages = (vintage - history["date"]).dt.days / 365.2425
        weights = pd.Series(np.power(2.0, -ages / 10.0), index=history.index)
        weights = weights / weights.sum()
        results, _, _ = score_one_half(
            history, {"history": history}, weights, "hl10p0"
        )
        causal = results["history"].reset_index(drop=True)
        final = final_monthly[final_monthly["date"] <= vintage].reset_index(drop=True)
        if len(causal) != len(final) or not causal["date"].equals(final["date"]):
            raise RuntimeError("Vintage/final alignment mismatch")
        if causal["date"].max() > vintage:
            raise RuntimeError("Future row entered vintage audit")
        for threshold in THRESHOLDS:
            causal_active = causal[raw_col("equal3", "hl10p0")] >= threshold
            final_active = final[raw_col("equal3", "hl10p0")] >= threshold
            local = (
                causal[raw_col("equal3", "hl10p0")] - threshold
            ).abs() <= 0.02 + 1e-12
            local_frame = causal.loc[local]
            local_weights = weights.reset_index(drop=True).loc[local]
            row: dict[str, object] = {
                "vintage_date": vintage.date().isoformat(),
                "history_start": causal["date"].min().date().isoformat(),
                "history_end": causal["date"].max().date().isoformat(),
                "history_months": len(causal),
                "threshold": float(threshold),
                "causal_activation_ratio": float(causal_active.mean()),
                "final_mapping_activation_ratio": float(final_active.mean()),
                "causal_vs_final_jaccard": jaccard(causal_active, final_active),
                "causal_local_months": int(local.sum()),
            }
            for field in ECONOMIC_FIELDS:
                row[f"causal_boundary_{field}_median"] = (
                    weighted_quantile(local_frame[field], local_weights, 0.50)
                    if not local_frame.empty
                    else math.nan
                )
            rows.append(row)
    return pd.DataFrame(rows)


def recent_episode_count(active: pd.Series, mask: pd.Series) -> int:
    part = active.loc[mask].reset_index(drop=True)
    return episode_stats(part)[0] if not part.empty else 0


def make_threshold_selection(
    monthly: pd.DataFrame,
    economic: pd.DataFrame,
    vintage: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    recent_mask = monthly["date"] >= monthly["date"].max() - pd.DateOffset(years=10)
    economic_center = economic[economic["half_label"] == "hl10p0"].set_index("threshold")
    rows: list[dict[str, object]] = []
    for threshold in THRESHOLDS:
        center = monthly[raw_col("equal3", "hl10p0")] >= threshold
        low = monthly[raw_col("equal3", "hl07p5")] >= threshold
        high = monthly[raw_col("equal3", "hl12p0")] >= threshold
        recent_ratios = [
            float((monthly.loc[recent_mask, raw_col("equal3", half_label)] >= threshold).mean())
            for half_label in HALF_LIVES
        ]
        full_episodes, full_longest = episode_stats(center)
        vintage_part = vintage[np.isclose(vintage["threshold"], threshold)].copy()
        vintage_part["vintage_date"] = pd.to_datetime(vintage_part["vintage_date"])
        min_all = float(vintage_part["causal_vs_final_jaccard"].min())
        min_2019 = float(
            vintage_part.loc[
                vintage_part["vintage_date"] >= pd.Timestamp("2019-01-01"),
                "causal_vs_final_jaccard",
            ].min()
        )
        econ = economic_center.loc[threshold]
        gates = {
            "gate_threshold_ge_067": bool(threshold >= 0.67 - 1e-12),
            "gate_cross_half_jaccard": bool(jaccard(low, center) >= 0.85 and jaccard(high, center) >= 0.85),
            "gate_recent_activation_spread": bool(max(recent_ratios) - min(recent_ratios) <= 0.05),
            "gate_episode_count": bool(full_episodes >= 5 and recent_episode_count(center, recent_mask) >= 2),
            "gate_recent_tail_coverage": bool(0.05 <= float(center.loc[recent_mask].mean()) <= 0.30),
            "gate_broad_factor_evidence": bool(econ["active_share_at_least2_components_ge_050"] >= 0.90),
            "gate_vintage_stability": bool(min_all >= 0.70 and min_2019 >= 0.80),
            "gate_local_boundary_sample": bool(int(econ["local_months"]) >= 8),
        }
        rows.append(
            {
                "threshold": float(threshold),
                "jaccard_hl07p5_vs_center": jaccard(low, center),
                "jaccard_hl12p0_vs_center": jaccard(high, center),
                "recent_10y_activation_hl07p5": recent_ratios[0],
                "recent_10y_activation_hl10p0": recent_ratios[1],
                "recent_10y_activation_hl12p0": recent_ratios[2],
                "recent_10y_activation_spread": max(recent_ratios) - min(recent_ratios),
                "full_monthly_episodes": full_episodes,
                "full_longest_active_months": full_longest,
                "recent_10y_monthly_episodes": recent_episode_count(center, recent_mask),
                "vintage_min_jaccard_all": min_all,
                "vintage_min_jaccard_2019plus": min_2019,
                "local_months": int(econ["local_months"]),
                "broad_factor_share": float(econ["active_share_at_least2_components_ge_050"]),
                **gates,
                "all_individual_gates_pass": bool(all(gates.values())),
            }
        )
    table = pd.DataFrame(rows)
    band_id = 0
    current_band: list[int] = []
    bands: list[list[int]] = []
    previous_threshold: float | None = None
    for index, row in table.iterrows():
        if bool(row["all_individual_gates_pass"]):
            threshold = float(row["threshold"])
            if previous_threshold is None or np.isclose(threshold - previous_threshold, 0.01):
                current_band.append(index)
            else:
                if current_band:
                    bands.append(current_band)
                current_band = [index]
            previous_threshold = threshold
        else:
            if current_band:
                bands.append(current_band)
            current_band = []
            previous_threshold = None
    if current_band:
        bands.append(current_band)
    valid_bands = [indices for indices in bands if len(indices) >= 3]
    table["band_id"] = 0
    for indices in valid_bands:
        band_id += 1
        table.loc[indices, "band_id"] = band_id
    table["in_selected_band"] = False
    table["is_design_center"] = False
    if valid_bands:
        selected_indices = sorted(
            valid_bands,
            key=lambda indices: (len(indices), float(table.loc[indices, "threshold"].min())),
            reverse=True,
        )[0]
        center_index = selected_indices[(len(selected_indices) - 1) // 2]
        table.loc[selected_indices, "in_selected_band"] = True
        table.loc[center_index, "is_design_center"] = True
        decision = {
            "platform_found": True,
            "selected_band_low": float(table.loc[selected_indices, "threshold"].min()),
            "selected_band_high": float(table.loc[selected_indices, "threshold"].max()),
            "selected_band_points": len(selected_indices),
            "design_center_threshold": float(table.loc[center_index, "threshold"]),
            "threshold_is_live_approved": False,
        }
    else:
        decision = {
            "platform_found": False,
            "selected_band_low": None,
            "selected_band_high": None,
            "selected_band_points": 0,
            "design_center_threshold": None,
            "threshold_is_live_approved": False,
        }
    return table, decision


def make_old_crosswalk(
    monthly: pd.DataFrame, daily: pd.DataFrame
) -> pd.DataFrame:
    monthly = monthly.copy()
    daily = daily.copy()
    monthly["old_fixed_risk"] = old_fixed_risk(monthly)
    daily["old_fixed_risk"] = old_fixed_risk(daily)
    anchor = daily["date"].max()
    scopes = {
        "full_monthly": (monthly, pd.Series(True, index=monthly.index)),
        "full_daily": (daily, pd.Series(True, index=daily.index)),
        "recent10_monthly": (monthly, monthly["date"] >= anchor - pd.DateOffset(years=10)),
        "recent10_daily": (daily, daily["date"] >= anchor - pd.DateOffset(years=10)),
    }
    rows: list[dict[str, object]] = []
    score_col = raw_col("equal3", "hl10p0")
    for old_threshold in OLD_THRESHOLDS:
        for scope, (frame, mask) in scopes.items():
            part = frame.loc[mask]
            old_ratio = float((part["old_fixed_risk"] >= old_threshold).mean())
            candidate_ratios = pd.Series(
                {float(threshold): float((part[score_col] >= threshold).mean()) for threshold in THRESHOLDS}
            )
            difference = (candidate_ratios - old_ratio).abs()
            closest_threshold = float(difference.index[difference.argmin()])
            rows.append(
                {
                    "old_fixed_threshold": old_threshold,
                    "scope": scope,
                    "old_activation_ratio": old_ratio,
                    "closest_equal3_raw_threshold": closest_threshold,
                    "equal3_activation_ratio": float(candidate_ratios.loc[closest_threshold]),
                    "absolute_activation_difference": float(difference.loc[closest_threshold]),
                }
            )
    return pd.DataFrame(rows)


def make_threshold_scan(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    selection: pd.DataFrame,
    price_context: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor = daily["date"].max()
    boundaries = window_boundaries(anchor)
    selection_map = selection.set_index("threshold")
    rows: list[dict[str, object]] = []
    for half_label, half_life in HALF_LIVES.items():
        score_col = raw_col("equal3", half_label)
        for threshold in THRESHOLDS:
            full_active = monthly[score_col] >= threshold
            episodes, longest = episode_stats(full_active)
            selected = selection_map.loc[threshold]
            candidate = f"{half_label}__equal3_raw_ge_{int(round(threshold * 100)):02d}"
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
                        "threshold": float(threshold),
                        "activation_day_ratio": float((daily_part[score_col] >= threshold).mean()),
                        "activation_month_ratio": float((monthly_part[score_col] >= threshold).mean()),
                        "monthly_episodes_full": episodes,
                        "longest_active_months_full": longest,
                        "current_active": bool(full_active.iloc[-1]),
                        "all_individual_gates_pass_center": bool(selected["all_individual_gates_pass"]),
                        "in_selected_band_center": bool(selected["in_selected_band"]),
                        "is_design_center": bool(selected["is_design_center"]),
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
            "threshold": first["threshold"],
            "monthly_episodes_full": first["monthly_episodes_full"],
            "longest_active_months_full": first["longest_active_months_full"],
            "current_active": first["current_active"],
            "all_individual_gates_pass_center": first["all_individual_gates_pass_center"],
            "in_selected_band_center": first["in_selected_band_center"],
            "is_design_center": first["is_design_center"],
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


def make_current_state(
    daily: pd.DataFrame, decision: dict[str, object]
) -> pd.DataFrame:
    current = daily.iloc[-1]
    center = decision["design_center_threshold"]
    rows = []
    for half_label, half_life in HALF_LIVES.items():
        raw_score = float(current[raw_col("equal3", half_label)])
        row = {
            "date": current["date"].date().isoformat(),
            "half_label": half_label,
            "half_life_years": half_life,
            "pb": float(current["pb_aggregate"]),
            "erp": float(current["erp"]),
            "trailing_dividend_contribution": float(current["trailing_dividend_contribution"]),
            "pb_risk": float(current[component_col("pb", half_label)]),
            "erp_risk": float(current[component_col("erp", half_label)]),
            "dividend_risk": float(current[component_col("dividend", half_label)]),
            "equal3_raw": raw_score,
            "design_center_threshold": center,
            "design_center_active": bool(center is not None and raw_score >= float(center)),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def setup_font() -> None:
    if FONT_PATH.exists():
        font_manager.fontManager.addfont(str(FONT_PATH))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT_PATH)).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def plot_threshold_stability(
    selection: pd.DataFrame, decision: dict[str, object], output: Path
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    for column, label in (
        ("recent_10y_activation_hl07p5", "7.5年"),
        ("recent_10y_activation_hl10p0", "10年中心"),
        ("recent_10y_activation_hl12p0", "12年"),
    ):
        axes[0].plot(selection["threshold"], selection[column] * 100, label=label, linewidth=2)
    axes[0].axhspan(5, 30, color="#70AD47", alpha=0.10)
    axes[0].set_title("最近10年月度高估覆盖率")
    axes[0].set_xlabel("equal3原始风险门槛")
    axes[0].set_ylabel("覆盖率（%）")
    axes[0].grid(alpha=0.2)
    axes[0].legend(frameon=False)
    axes[1].plot(selection["threshold"], selection["jaccard_hl07p5_vs_center"], label="7.5年/10年")
    axes[1].plot(selection["threshold"], selection["jaccard_hl12p0_vs_center"], label="12年/10年")
    axes[1].plot(selection["threshold"], selection["vintage_min_jaccard_2019plus"], label="2019后历史时点最低")
    axes[1].axhline(0.85, color="#C00000", linestyle="--", alpha=0.7)
    axes[1].set_ylim(0.5, 1.02)
    axes[1].set_title("跨权重与历史时点状态一致性")
    axes[1].set_xlabel("equal3原始风险门槛")
    axes[1].set_ylabel("Jaccard")
    axes[1].grid(alpha=0.2)
    axes[1].legend(frameon=False)
    if decision["platform_found"]:
        for ax in axes:
            ax.axvspan(decision["selected_band_low"], decision["selected_band_high"], color="#FFC000", alpha=0.18)
            ax.axvline(decision["design_center_threshold"], color="#C00000", linestyle=":")
    fig.suptitle("中证500固定高估门槛：纯结构平台扫描", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "threshold_stability.png", dpi=180)
    plt.close(fig)


def plot_economic_boundary(
    economic: pd.DataFrame, decision: dict[str, object], output: Path
) -> None:
    center = economic[economic["half_label"] == "hl10p0"].copy()
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.6))
    for ax, (field, label) in zip(axes, ECONOMIC_FIELDS.items(), strict=True):
        ax.plot(center["threshold"], center[f"boundary_{field}_median"], color="#4472C4", linewidth=2, label="局部中位数")
        ax.fill_between(
            center["threshold"],
            center[f"boundary_{field}_p25"],
            center[f"boundary_{field}_p75"],
            color="#4472C4",
            alpha=0.18,
            label="局部25%—75%",
        )
        if field in {"erp", "trailing_dividend_contribution"}:
            ax.yaxis.set_major_formatter(lambda value, _: f"{value:.1%}")
        if decision["platform_found"]:
            ax.axvspan(decision["selected_band_low"], decision["selected_band_high"], color="#FFC000", alpha=0.16)
        ax.set_title(label)
        ax.set_xlabel("equal3原始风险门槛")
        ax.grid(alpha=0.2)
    axes[0].legend(frameon=False)
    fig.suptitle("10年中心：门槛附近对应的经济状态分布", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "economic_boundary_map.png", dpi=180)
    plt.close(fig)


def plot_vintage_heatmap(vintage: pd.DataFrame, output: Path) -> None:
    pivot = vintage.pivot(index="vintage_date", columns="threshold", values="causal_vs_final_jaccard")
    fig, ax = plt.subplots(figsize=(15, 6.5))
    image = ax.imshow(pivot.to_numpy(), aspect="auto", cmap="YlGn", vmin=0.5, vmax=1.0)
    tick_positions = np.arange(0, len(pivot.columns), 5)
    ax.set_xticks(tick_positions, [f"{pivot.columns[i]:.2f}" for i in tick_positions])
    ax.set_yticks(range(len(pivot.index)), [str(item) for item in pivot.index])
    ax.set_xlabel("equal3原始风险门槛")
    ax.set_ylabel("历史时点")
    ax.set_title("只用当时历史重算：与最终冻结映射的状态Jaccard")
    fig.colorbar(image, ax=ax, label="Jaccard")
    fig.tight_layout()
    fig.savefig(output / "vintage_threshold_jaccard.png", dpi=180)
    plt.close(fig)


def plot_old_crosswalk(crosswalk: pd.DataFrame, output: Path) -> None:
    monthly = crosswalk[crosswalk["scope"].isin(["full_monthly", "recent10_monthly"])]
    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    width = 0.035
    x = np.arange(len(OLD_THRESHOLDS))
    full = monthly[monthly["scope"] == "full_monthly"].sort_values("old_fixed_threshold")
    recent = monthly[monthly["scope"] == "recent10_monthly"].sort_values("old_fixed_threshold")
    ax.bar(x - width / 2, full["closest_equal3_raw_threshold"], width=0.22, label="全样本月度")
    ax.bar(x + width / 2 + 0.18, recent["closest_equal3_raw_threshold"], width=0.22, label="最近10年月度")
    ax.set_xticks(x + 0.09, [f"旧>={value:.2f}" for value in OLD_THRESHOLDS])
    ax.set_ylim(0.45, 0.92)
    ax.set_ylabel("覆盖率最接近的equal3原始门槛")
    ax.set_title("旧固定总分只能做覆盖率交叉映射")
    ax.grid(axis="y", alpha=0.2)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output / "old_fixed_coverage_crosswalk.png", dpi=180)
    plt.close(fig)


def format_economic(field: str, value: float) -> str:
    return f"{value:.2%}" if field in {"erp", "trailing_dividend_contribution"} else f"{value:.2f}"


def make_record(
    selection: pd.DataFrame,
    decision: dict[str, object],
    economic: pd.DataFrame,
    crosswalk: pd.DataFrame,
    current: pd.DataFrame,
    vintage: pd.DataFrame,
) -> str:
    if decision["platform_found"]:
        center_threshold = float(decision["design_center_threshold"])
        center_selection = selection[np.isclose(selection["threshold"], center_threshold)].iloc[0]
        center_economic = economic[
            (economic["half_label"] == "hl10p0")
            & np.isclose(economic["threshold"], center_threshold)
        ].iloc[0]
        platform_text = (
            f"通过的连续平台为 **{decision['selected_band_low']:.2f}—{decision['selected_band_high']:.2f}**，"
            f"共{decision['selected_band_points']}个点；机械设计中心为 **{center_threshold:.2f}**。"
        )
        economic_rows = []
        for field, label in ECONOMIC_FIELDS.items():
            economic_rows.append(
                f"| {label} | {format_economic(field, center_economic[f'boundary_{field}_p25'])} | {format_economic(field, center_economic[f'boundary_{field}_median'])} | {format_economic(field, center_economic[f'boundary_{field}_p75'])} | {format_economic(field, center_economic[f'crossing_{field}_median'])} |"
            )
        center_detail = f"""
### 设计中心 {center_threshold:.2f} 的结构证据

- 7.5/10年月度Jaccard：{center_selection['jaccard_hl07p5_vs_center']:.2%}；12/10年：{center_selection['jaccard_hl12p0_vs_center']:.2%}；
- 最近10年10年中心覆盖率：{center_selection['recent_10y_activation_hl10p0']:.2%}，三种时间权重差：{center_selection['recent_10y_activation_spread']:.2%}；
- 全样本/最近10年启动段：{int(center_selection['full_monthly_episodes'])}/{int(center_selection['recent_10y_monthly_episodes'])}；
- 历史时点最低Jaccard：全时点{center_selection['vintage_min_jaccard_all']:.2%}，2019年后{center_selection['vintage_min_jaccard_2019plus']:.2%}；
- 启动月中至少两个分量超过50%风险位置：{center_selection['broad_factor_share']:.2%}。

| 经济变量 | 局部25% | 局部中位数 | 局部75% | 向上穿越月中位数 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(economic_rows)}
"""
    else:
        platform_text = "没有任何至少3个相邻点的平台通过全部预注册门槛，因此本版不选择固定高估门槛。"
        center_detail = ""

    current_center = current[current["half_label"] == "hl10p0"].iloc[0]
    cross_rows = []
    for old_threshold in OLD_THRESHOLDS:
        full = crosswalk[
            (crosswalk["old_fixed_threshold"] == old_threshold)
            & (crosswalk["scope"] == "full_monthly")
        ].iloc[0]
        recent = crosswalk[
            (crosswalk["old_fixed_threshold"] == old_threshold)
            & (crosswalk["scope"] == "recent10_monthly")
        ].iloc[0]
        cross_rows.append(
            f"| >={old_threshold:.2f} | {full.old_activation_ratio:.2%} | {full.closest_equal3_raw_threshold:.2f} | {recent.old_activation_ratio:.2%} | {recent.closest_equal3_raw_threshold:.2f} |"
        )
    vintage_summary = vintage.groupby("threshold")["causal_vs_final_jaccard"].min()
    return f"""# 中证500固定估值经济门槛 v4

## 结论

{platform_text}

这里的门槛作用于PB、ERP、股息三个风险分位的**原始平均值**，不是合成分再次CDF后的历史分位。本版没有查看任何后续收益、IC贴水或PUT损益来选择门槛。

{center_detail}

## 当前状态（2026-08-17）

- PB：{current_center.pb:.2f}，风险分量{current_center.pb_risk:.2%}；
- ERP：{current_center.erp:.2%}，风险分量{current_center.erp_risk:.2%}；
- 过去一年股息贡献：{current_center.trailing_dividend_contribution:.2%}，风险分量{current_center.dividend_risk:.2%}；
- 10年中心 `equal3_raw`：**{current_center.equal3_raw:.2%}**；
- 是否达到设计中心：{'是' if current_center.design_center_active else '否'}。

## 与旧固定总分的覆盖率交叉映射

| 旧固定线 | 全样本月覆盖 | 等价equal3线 | 最近10年月覆盖 | 等价equal3线 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(cross_rows)}

这只是覆盖率接近。旧离散分与新连续分的经济含义不同，同一旧门槛在全样本和最近10年可能映射到不同新门槛，不能称为一一换算。

## 历史时点限制

- 11个历史时点、41条门槛，共{len(vintage)}行因果定义审计；
- 全网格最弱历史时点Jaccard：{vintage_summary.min():.2%}；
- 选择只使用预注册的分段门槛，不连接任何未来收益。

## 边界

- 等权门槛是一张三维边界面，表中的PB/ERP/股息中位数不是必须同时满足的三个硬条件；
- 价格指数收益字段只是扫描协议背景，同窗所有候选完全相同；
- 本版只提供固定估值候选平台，不是PUT启动规则，状态为`RESEARCH_ONLY_NOT_LIVE_APPROVED`。
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
            "scan_type": "outcome_free_fixed_economic_threshold_structure_scan",
            "baseline": {"structure": "equal3_raw", "threshold": None, "half_life_years": 10.0},
            "candidate_grid": {
                "thresholds": [float(item) for item in THRESHOLDS],
                "half_lives_years": list(HALF_LIVES.values()),
            },
            "data_snapshot": {
                "start": long["start"].min(),
                "end": long["end"].max(),
                "daily_rows": 4_761,
                "monthly_rows": 236,
                "strategy_returns_used_for_selection": False,
            },
            "cost_model": {
                "applicable": False,
                "reason": "outcome-free valuation threshold definition with no trades",
            },
            "candidate_count": int(long["candidate"].nunique()),
            "window_rows": len(long),
            "decision": decision,
            "stability_label": "structural_platform" if decision["platform_found"] else "no_structural_platform",
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython ic_fixed_valuation_economic_threshold_v4.py\n")


def make_scan_record(decision: dict[str, object]) -> str:
    decision_text = (
        f"selected structural band {decision['selected_band_low']:.2f}-{decision['selected_band_high']:.2f}; center {decision['design_center_threshold']:.2f}"
        if decision["platform_found"]
        else "no structural threshold platform passed"
    )
    return f"""# Quant Parameter Scan Record

## Run Metadata

- Version: `{VERSION}`
- Entrypoint: `ic_fixed_valuation_economic_threshold_v4.py`
- Scope: outcome-free CSI500 valuation-body threshold scan.

## Research Question and Data

- Equal3 raw risk thresholds 0.50-0.90 by 0.01 across 7.5/10/12-year time half-lives.
- CSI500 valuation sample: 2007-01-15 to 2026-08-17; 4,761 daily and 236 month-end rows.
- 123 candidates and five mandatory windows; 615 long rows.
- Price-index return fields are context only and are identical across candidates within each segment.
- Cost model is not applicable because this definition audit contains no trades.

## Stability

- Gate set: cross-half-life Jaccard, recent activation spread, episode recurrence, tail coverage, broad-factor evidence, causal-vintage Jaccard, and local economic sample count.
- A valid platform requires at least three adjacent passing thresholds.

## Decision

- Decision: {decision_text}.
- Stability label: `{'structural_platform' if decision['platform_found'] else 'no_structural_platform'}`.
- No strategy or live threshold is promoted.
"""


def main() -> None:
    input_hashes = verify_inputs()
    monthly = pd.read_csv(MONTHLY_INPUT, parse_dates=["date"])
    daily = pd.read_csv(DAILY_INPUT, parse_dates=["date"])
    base_monthly = prepare_base(BASE_MONTHLY_INPUT)
    if len(monthly) != 236 or len(daily) != 4_761 or len(base_monthly) != 236:
        raise RuntimeError("Unexpected sample size")
    if not monthly["date"].equals(base_monthly["date"]):
        raise RuntimeError("Base/v3.1 monthly date mismatch")
    parity_errors: dict[str, float] = {}
    for half_label in HALF_LIVES:
        expected = (
            monthly[component_col("pb", half_label)]
            + monthly[component_col("erp", half_label)]
            + monthly[component_col("dividend", half_label)]
        ) / 3.0
        parity_errors[half_label] = float(
            np.max(np.abs(expected - monthly[raw_col("equal3", half_label)]))
        )
    if max(parity_errors.values()) > 1e-12:
        raise RuntimeError(f"Equal3 parity failed: {parity_errors}")

    monthly["old_fixed_risk"] = old_fixed_risk(monthly)
    daily["old_fixed_risk"] = old_fixed_risk(daily)
    economic = make_economic_boundary(monthly)
    vintage = make_vintage_stability(base_monthly, monthly)
    selection, decision = make_threshold_selection(monthly, economic, vintage)
    crosswalk = make_old_crosswalk(monthly, daily)
    current = make_current_state(daily, decision)
    price_context = make_price_context(daily)
    scan_long, scan_wide = make_threshold_scan(daily, monthly, selection, price_context)

    if len(economic) != 123 or len(vintage) != 451:
        raise RuntimeError("Economic or vintage grid size mismatch")
    if scan_long["candidate"].nunique() != 123 or len(scan_long) != 615:
        raise RuntimeError("Scan grid size mismatch")
    if not scan_long.groupby("segment")["ann_return"].nunique().eq(1).all():
        raise RuntimeError("Candidate price context differs within a window")
    if (pd.to_datetime(vintage["history_end"]) > pd.to_datetime(vintage["vintage_date"])).any():
        raise RuntimeError("Vintage audit contains future rows")

    STAGING.mkdir(parents=True)
    monthly.to_csv(STAGING / "monthly_equal3_threshold_scores.csv", index=False)
    daily.to_csv(STAGING / "daily_equal3_threshold_scores.csv.gz", index=False, compression="gzip")
    economic.to_csv(STAGING / "economic_boundary_map.csv", index=False)
    vintage.to_csv(STAGING / "vintage_threshold_stability.csv", index=False)
    selection.to_csv(STAGING / "threshold_selection.csv", index=False)
    crosswalk.to_csv(STAGING / "old_fixed_coverage_crosswalk.csv", index=False)
    current.to_csv(STAGING / "current_threshold_state.csv", index=False)
    price_context.to_csv(STAGING / "underlying_price_context.csv", index=False)
    scan_long.to_csv(STAGING / "threshold_scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "threshold_window_metrics.csv", index=False)

    setup_font()
    plot_threshold_stability(selection, decision, STAGING)
    plot_economic_boundary(economic, decision, STAGING)
    plot_vintage_heatmap(vintage, STAGING)
    plot_old_crosswalk(crosswalk, STAGING)

    record = make_record(selection, decision, economic, crosswalk, current, vintage)
    (STAGING / "record.md").write_text(record, encoding="utf-8")
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
        "equal3_parity_max_abs_error": max(parity_errors.values()),
        "candidate_count": int(scan_long["candidate"].nunique()),
        "scan_rows": len(scan_long),
        "economic_rows": len(economic),
        "vintage_rows": len(vintage),
        "vintage_no_future_rows": True,
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
