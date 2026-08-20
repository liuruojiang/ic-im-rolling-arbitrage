from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_fixed_valuation_unbounded_score_v6 as ic_v6

ROOT = Path(__file__).resolve().parent
VERSION = "im_fixed_valuation_tier_relationship_v3"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "dbc096f7dfbbfec2724f6889e0000564b283c8b52dc00e73da18e430ba3759c5"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V2_OUTPUT = ROOT / "outputs" / "im_fixed_valuation_duration_normalized_v2"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260818_1000_im_fixed_valuation_tier_relationship_v3_valuation_body_mean_median_tier_relation"
)

INPUT_HASHES = {
    ROOT / "im_fixed_valuation_duration_normalized_v2.py": "4cc0238b025d3d7c369e37d889f2fc624c85aa3925f5903decd36a3daea6b6f4",
    ROOT / "docs" / "im_fixed_valuation_duration_normalized_v2_spec.md": "8b623e3ee8f061bdc54efdf845ee4a518c588882550d6658887e33ac6abeded8",
    ROOT / "docs" / "im_fixed_valuation_duration_normalized_v2_postrun_audit.md": "a97181738c22b2f24d0c0e097ca6956086694465f056291762d1f570f7748e8d",
    ROOT / "docs" / "ic_510500_put_research_mainline_v1.md": "6da92d886f184277cffcdbbbd706d43ee057c7e1d4502410b8c7b12cde8eb4b5",
    V2_OUTPUT / "daily_unbounded_fixed_scores.csv.gz": "1e186ffc943ebcc16769cb86c79fd817bb1d754660f90d8d8a4b9d74a479a49f",
    V2_OUTPUT / "monthly_unbounded_fixed_scores.csv": "1b173ae29df570825836af7c9c97b6c851254bc7eca8dd91fc45af6546db3cbc",
    V2_OUTPUT / "economic_boundary.csv": "fb300003bc512054b79b47c0f722d1d0bb50a48b95ec30b0172a92317cffb065",
    V2_OUTPUT / "factor_structure_summary.csv": "37536c528c113f6982e91d7ca9c46262ab7ec5df90e474416d15917a14ef201b",
    V2_OUTPUT / "price_index_context.csv": "1b04a18efe8b73f5becb164d8276b5ed07b216f647931873771815983ec6ac8c",
    V2_OUTPUT / "raw_threshold_map.csv": "32748e84e963643bd6000671067f7be81f17775cbe05f2268c10d240543c0465",
    V2_OUTPUT / "duration_gate_definition.csv": "900fcf2cc277da37117fba659425072339fda0fe2983bd66346e2efa48b2e9b3",
    V2_OUTPUT / "temporal_episode_audit.csv": "edb14daa07584d5ca0898a4e948290618dc9e66b983998e8b740913b22cbaeb1",
    V2_OUTPUT / "threshold_selection_v2.csv": "554b176c5dd6665f6d36330c250829c0c3b0238d4ee0eb2ddd3cfb21238903ac",
    V2_OUTPUT / "current_state.csv": "b6c8af52f257b34caa836e230bb642a83c350595b8cd7c08c9f6a37f638ab87e",
    V2_OUTPUT / "decision_summary.json": "3dd22dc83a64dc68342827685561432bd24122cfe0a25c0b5e7857ff92e926e9",
    V2_OUTPUT / "integrity_checks.json": "3a6bdae037704d97c25070935cc7d92f07f9e13f454c60d52eab810e73d7eb2a",
    V2_OUTPUT / "output_manifest.json": "c88aea0ff1093826093ee16e886711ecec2a53e4cd109c7e1a3c64c3e3039db4",
}

TIER_THRESHOLDS = (2.45, 2.50, 2.60)
CANDIDATE_COLUMNS = {
    "median_primary": "median_tier",
    "mean_primary": "mean_tier",
    "consensus_min": "consensus_min_tier",
    "either_max": "either_max_tier",
}
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
EXPECTED_DAILY_ROWS = 2634
EXPECTED_MONTHLY_ROWS = 131


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def verify_v2_manifest() -> None:
    manifest = json.loads(
        (V2_OUTPUT / "output_manifest.json").read_text(encoding="utf-8")
    )
    for name, item in manifest["files"].items():
        path = V2_OUTPUT / name
        if not path.exists() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Frozen v2 output manifest mismatch: {path}")


def verify_frozen_inputs(*, require_fresh_output: bool) -> dict[str, str]:
    if sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v3 specification mismatch")
    sidecar_hash = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar_hash != SPEC_HASH:
        raise RuntimeError("Frozen v3 specification sidecar mismatch")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v3 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v3 parameter scan folder is missing")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v3 input changed: {path}")
    verify_v2_manifest()
    return {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()}


def load_inputs() -> dict[str, Any]:
    return {
        "daily": pd.read_csv(
            V2_OUTPUT / "daily_unbounded_fixed_scores.csv.gz", parse_dates=["date"]
        ),
        "monthly": pd.read_csv(
            V2_OUTPUT / "monthly_unbounded_fixed_scores.csv", parse_dates=["date"]
        ),
        "price_context": pd.read_csv(V2_OUTPUT / "price_index_context.csv"),
        "threshold_selection": pd.read_csv(
            V2_OUTPUT / "threshold_selection_v2.csv"
        ),
        "duration_definition": pd.read_csv(
            V2_OUTPUT / "duration_gate_definition.csv"
        ),
        "decision_v2": json.loads(
            (V2_OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
        ),
        "factor_summary": pd.read_csv(
            V2_OUTPUT / "factor_structure_summary.csv"
        ),
    }


def score_to_tier(score: pd.Series) -> pd.Series:
    values = np.select(
        [score.ge(2.60), score.ge(2.50), score.ge(2.45)],
        [3, 2, 1],
        default=0,
    )
    return pd.Series(values.astype(int), index=score.index)


def add_tier_states(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["mean_tier"] = score_to_tier(result["unbounded_mean_knot"])
    result["median_tier"] = score_to_tier(result["unbounded_median_knot"])
    result["consensus_min_tier"] = result[["mean_tier", "median_tier"]].min(
        axis=1
    )
    result["either_max_tier"] = result[["mean_tier", "median_tier"]].max(axis=1)
    return result


def tier_definition() -> pd.DataFrame:
    rows = [
        {
            "tier": 0,
            "score_lower_inclusive": math.nan,
            "score_upper_exclusive": 2.45,
            "future_severity_candidate": 0.00,
            "origin": "below_v2_joint_platform",
        }
    ]
    for tier, threshold in enumerate(TIER_THRESHOLDS, start=1):
        upper = TIER_THRESHOLDS[tier] if tier < len(TIER_THRESHOLDS) else math.nan
        rows.append(
            {
                "tier": tier,
                "score_lower_inclusive": threshold,
                "score_upper_exclusive": upper,
                "future_severity_candidate": tier * 0.25,
                "origin": (
                    "v2_joint_lower"
                    if tier == 1
                    else "v2_design_center"
                    if tier == 2
                    else "v2_joint_upper"
                ),
            }
        )
    return pd.DataFrame(rows)


def economic_tier_map() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "tier": tier,
                "threshold": threshold,
                "pb_at_least": 1.50 + 0.50 * threshold,
                "erp_at_most": 0.045 - 0.015 * threshold,
                "dividend_at_most": 0.030 - 0.010 * threshold,
                "median_semantics": "at_least_two_of_three",
            }
            for tier, threshold in enumerate(TIER_THRESHOLDS, start=1)
        ]
    )


def weighted_kappa(left: pd.Series, right: pd.Series) -> float:
    matrix = pd.crosstab(left, right).reindex(
        index=range(4), columns=range(4), fill_value=0
    )
    values = matrix.to_numpy(float)
    values /= values.sum()
    weights = np.abs(np.subtract.outer(np.arange(4), np.arange(4))) / 3.0
    observed = float((weights * values).sum())
    expected_matrix = np.outer(values.sum(axis=1), values.sum(axis=0))
    expected = float((weights * expected_matrix).sum())
    return 1.0 if expected == 0.0 else float(1.0 - observed / expected)


def transitions_per_year(frame: pd.DataFrame, column: str) -> dict[str, float | int]:
    tiers = frame[column].astype(int).reset_index(drop=True)
    changes = tiers.diff().fillna(0).astype(int)
    years = max(
        (frame["date"].iloc[-1] - frame["date"].iloc[0]).days / 365.2425,
        1 / 365.2425,
    )
    return {
        "transitions": int(changes.ne(0).sum()),
        "annualized_transitions": float(changes.ne(0).sum() / years),
        "upgrades": int(changes.gt(0).sum()),
        "downgrades": int(changes.lt(0).sum()),
        "multi_tier_jumps": int(changes.abs().ge(2).sum()),
    }


def state_metrics(
    frame: pd.DataFrame, column: str, median_column: str = "median_tier"
) -> dict[str, float | int]:
    tier = frame[column].astype(int)
    active = tier.gt(0)
    episodes, longest = ic_v6.episode_stats(active)
    transitions = transitions_per_year(frame, column)
    return {
        "avg_tier": float(tier.mean()),
        "tier0_ratio": float(tier.eq(0).mean()),
        "tier1_ratio": float(tier.eq(1).mean()),
        "tier2_ratio": float(tier.eq(2).mean()),
        "tier3_ratio": float(tier.eq(3).mean()),
        "nonzero_ratio": float(active.mean()),
        "tier1plus_ratio": float(tier.ge(1).mean()),
        "tier2plus_ratio": float(tier.ge(2).mean()),
        "tier3plus_ratio": float(tier.ge(3).mean()),
        "nonzero_episodes": episodes,
        "longest_nonzero_rows": longest,
        "exact_agreement_vs_median": float(
            tier.eq(frame[median_column].astype(int)).mean()
        ),
        "mean_abs_tier_diff_vs_median": float(
            (tier - frame[median_column].astype(int)).abs().mean()
        ),
        **transitions,
    }


def agreement_summary(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        anchor = frame["date"].max()
        masks = {
            "full": pd.Series(True, index=frame.index),
            "last_10y": frame["date"] >= anchor - pd.DateOffset(years=10),
        }
        for segment, mask in masks.items():
            part = frame.loc[mask]
            left = part["mean_tier"].astype(int)
            right = part["median_tier"].astype(int)
            difference = (left - right).abs()
            rows.append(
                {
                    "frequency": frequency,
                    "segment": segment,
                    "start": part["date"].min().date().isoformat(),
                    "end": part["date"].max().date().isoformat(),
                    "rows": len(part),
                    "exact_tier_agreement": float(left.eq(right).mean()),
                    "weighted_kappa": weighted_kappa(left, right),
                    "mean_abs_tier_difference": float(difference.mean()),
                    "severe_difference_ge_2_ratio": float(difference.ge(2).mean()),
                    "mean_higher_ratio": float(left.gt(right).mean()),
                    "median_higher_ratio": float(right.gt(left).mean()),
                }
            )
    return pd.DataFrame(rows)


def confusion_matrix_table(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        anchor = frame["date"].max()
        for segment, part in (
            ("full", frame),
            ("last_10y", frame[frame["date"] >= anchor - pd.DateOffset(years=10)]),
        ):
            matrix = pd.crosstab(part["mean_tier"], part["median_tier"]).reindex(
                index=range(4), columns=range(4), fill_value=0
            )
            for mean_tier in range(4):
                for median_tier in range(4):
                    rows.append(
                        {
                            "frequency": frequency,
                            "segment": segment,
                            "mean_tier": mean_tier,
                            "median_tier": median_tier,
                            "rows": int(matrix.loc[mean_tier, median_tier]),
                        }
                    )
    return pd.DataFrame(rows)


def cumulative_event_audit(monthly: pd.DataFrame) -> pd.DataFrame:
    anchor = monthly["date"].max()
    recent_mask = monthly["date"] >= anchor - pd.DateOffset(years=10)
    split_index = math.ceil(len(monthly) / 2)
    rows = []
    for candidate, column in CANDIDATE_COLUMNS.items():
        tier = monthly[column].astype(int)
        for level in (1, 2, 3):
            active = tier.ge(level)
            full_episodes, full_longest = ic_v6.episode_stats(active)
            recent_episodes = ic_v6.recent_episode_count(active, recent_mask)
            values = active.to_numpy(bool)
            starts = values & ~np.r_[False, values[:-1]]
            positions = np.flatnonzero(starts)
            early = positions[positions < split_index]
            late = positions[positions >= split_index]
            rows.append(
                {
                    "candidate": candidate,
                    "level_at_least": level,
                    "threshold": TIER_THRESHOLDS[level - 1],
                    "full_activation_ratio": float(active.mean()),
                    "recent10_activation_ratio": float(active.loc[recent_mask].mean()),
                    "full_episodes": full_episodes,
                    "recent10_episodes": recent_episodes,
                    "full_longest_months": full_longest,
                    "early_episode_starts": len(early),
                    "late_episode_starts": len(late),
                    "early_start_dates": "|".join(
                        monthly["date"].iloc[early].dt.strftime("%Y-%m-%d")
                    ),
                    "late_start_dates": "|".join(
                        monthly["date"].iloc[late].dt.strftime("%Y-%m-%d")
                    ),
                    "event_temporal_gate_pass": bool(
                        full_episodes >= 3
                        and recent_episodes >= 2
                        and len(early) >= 1
                        and len(late) >= 1
                    ),
                }
            )
    return pd.DataFrame(rows)


def make_scan(
    daily: pd.DataFrame, monthly: pd.DataFrame, price_context: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor = daily["date"].max()
    boundaries = ic_v6.window_boundaries(anchor)
    rows = []
    context_index = price_context.set_index("segment")
    for candidate, column in CANDIDATE_COLUMNS.items():
        for segment in WINDOWS:
            boundary = boundaries[segment]
            daily_part = daily if boundary is None else daily[daily["date"] >= boundary]
            monthly_part = (
                monthly
                if boundary is None
                else monthly[monthly["date"] >= boundary]
            )
            context = context_index.loc[segment]
            daily_metrics = state_metrics(daily_part, column)
            monthly_metrics = state_metrics(monthly_part, column)
            rows.append(
                {
                    "candidate": candidate,
                    "segment": segment,
                    "start": context["start"],
                    "end": context["end"],
                    "rows": int(context["rows"]),
                    "ann_return": float(context["ann_return"]),
                    "ann_vol": float(context["ann_vol"]),
                    "sharpe_repo": float(context["sharpe_repo"]),
                    "max_dd": float(context["max_dd"]),
                    "relationship": candidate,
                    **{f"daily_{key}": value for key, value in daily_metrics.items()},
                    **{
                        f"monthly_{key}": value
                        for key, value in monthly_metrics.items()
                    },
                    "metric_semantics": (
                        "underlying_price_index_context_only_no_strategy_return"
                    ),
                }
            )
    long = pd.DataFrame(rows)
    wide_rows = []
    state_wide_metrics = (
        "daily_avg_tier",
        "daily_nonzero_ratio",
        "daily_annualized_transitions",
        "monthly_avg_tier",
        "monthly_nonzero_ratio",
        "monthly_exact_agreement_vs_median",
    )
    for candidate, part in long.groupby("candidate", sort=False):
        row: dict[str, Any] = {"candidate": candidate, "relationship": candidate}
        for item in part.itertuples(index=False):
            for metric in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
            for metric in state_wide_metrics:
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def disagreement_extremes(daily: pd.DataFrame) -> pd.DataFrame:
    result = daily[
        [
            "date",
            "price_close",
            "pb_aggregate",
            "erp",
            "trailing_dividend_contribution",
            "unbounded_mean_knot",
            "unbounded_median_knot",
            "mean_tier",
            "median_tier",
        ]
    ].copy()
    result["absolute_tier_difference"] = (
        result["mean_tier"] - result["median_tier"]
    ).abs()
    result["absolute_score_difference"] = (
        result["unbounded_mean_knot"] - result["unbounded_median_knot"]
    ).abs()
    return result.sort_values(
        ["absolute_tier_difference", "absolute_score_difference", "date"],
        ascending=[False, False, True],
    ).head(100)


def vintage_invariance(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    vintages = []
    for year in range(2016, 2026):
        part = monthly[
            (monthly["date"].dt.year == year) & (monthly["date"].dt.month == 12)
        ]
        vintages.append(part["date"].max())
    vintages.append(monthly["date"].max())
    for vintage in vintages:
        history = monthly[monthly["date"] <= vintage].copy()
        recomputed = add_tier_states(
            history.drop(
                columns=[
                    "mean_tier",
                    "median_tier",
                    "consensus_min_tier",
                    "either_max_tier",
                ]
            )
        )
        mismatch = sum(
            int((recomputed[column] != history[column]).sum())
            for column in CANDIDATE_COLUMNS.values()
        )
        rows.append(
            {
                "vintage_date": vintage.date().isoformat(),
                "history_start": history["date"].min().date().isoformat(),
                "history_end": history["date"].max().date().isoformat(),
                "history_months": len(history),
                "tier_state_mismatches": mismatch,
                "future_rows_used": False,
            }
        )
    return pd.DataFrame(rows)


def select_relationship(
    agreement: pd.DataFrame,
    events: pd.DataFrame,
    scan_long: pd.DataFrame,
) -> dict[str, Any]:
    indexed = agreement.set_index(["frequency", "segment"])
    monthly_full = indexed.loc[("monthly", "full")]
    monthly_recent = indexed.loc[("monthly", "last_10y")]
    daily_full = indexed.loc[("daily", "full")]
    daily_recent = indexed.loc[("daily", "last_10y")]
    definition_gates = {
        "gate_monthly_exact_agreement_full": bool(
            monthly_full["exact_tier_agreement"] >= 0.85
        ),
        "gate_monthly_exact_agreement_recent10": bool(
            monthly_recent["exact_tier_agreement"] >= 0.85
        ),
        "gate_daily_exact_agreement_full": bool(
            daily_full["exact_tier_agreement"] >= 0.80
        ),
        "gate_daily_exact_agreement_recent10": bool(
            daily_recent["exact_tier_agreement"] >= 0.80
        ),
        "gate_monthly_severe_difference_full": bool(
            monthly_full["severe_difference_ge_2_ratio"] <= 0.05
        ),
        "gate_monthly_severe_difference_recent10": bool(
            monthly_recent["severe_difference_ge_2_ratio"] <= 0.05
        ),
    }
    median_events = events[events["candidate"] == "median_primary"]
    median_event_gate = bool(median_events["event_temporal_gate_pass"].all())
    median_transition_rate = float(
        scan_long[
            (scan_long["candidate"] == "median_primary")
            & (scan_long["segment"] == "full")
        ]["daily_annualized_transitions"].iloc[0]
    )
    median_transition_gate = bool(median_transition_rate <= 24.0)
    definition_pass = bool(all(definition_gates.values()))
    primary_pass = bool(
        definition_pass and median_event_gate and median_transition_gate
    )

    consensus_events = events[events["candidate"] == "consensus_min"]
    consensus_level1_recent = float(
        consensus_events[consensus_events["level_at_least"] == 1][
            "recent10_activation_ratio"
        ].iloc[0]
    )
    consensus_transition_rate = float(
        scan_long[
            (scan_long["candidate"] == "consensus_min")
            & (scan_long["segment"] == "full")
        ]["daily_annualized_transitions"].iloc[0]
    )
    fallback_gates = {
        "gate_consensus_recent10_level1_coverage": bool(
            0.05 <= consensus_level1_recent <= 0.30
        ),
        "gate_consensus_all_event_temporal": bool(
            consensus_events["event_temporal_gate_pass"].all()
        ),
        "gate_consensus_daily_transition_rate": bool(
            consensus_transition_rate <= 24.0
        ),
    }
    fallback_eligible = bool(
        not definition_pass and median_event_gate and median_transition_gate
    )
    fallback_pass = bool(fallback_eligible and all(fallback_gates.values()))

    if primary_pass:
        selected = "median_primary"
        decision = "freeze_median_tier_ladder_candidate_for_next_put_layer"
        label = "narrow_stable"
    elif fallback_pass:
        selected = "consensus_min"
        decision = "freeze_consensus_min_tier_ladder_candidate_for_next_put_layer"
        label = "narrow_stable"
    else:
        selected = None
        decision = "no_executable_tier_relationship"
        label = "reject"
    return {
        "decision": decision,
        "stability_label": label,
        "selected_relationship": selected,
        "tier_thresholds": list(TIER_THRESHOLDS),
        "primary_pass": primary_pass,
        "definition_agreement_pass": definition_pass,
        "median_event_temporal_pass": median_event_gate,
        "median_daily_transition_rate": median_transition_rate,
        "median_daily_transition_pass": median_transition_gate,
        "fallback_eligible": fallback_eligible,
        "fallback_pass": fallback_pass,
        "consensus_level1_recent10_coverage": consensus_level1_recent,
        "consensus_daily_transition_rate": consensus_transition_rate,
        **definition_gates,
        **fallback_gates,
        "selection_uses_strategy_outcomes": False,
        "live_approved": False,
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }


def current_state(daily: pd.DataFrame, decision: dict[str, Any]) -> pd.DataFrame:
    current = daily.iloc[-1]
    return pd.DataFrame(
        [
            {
                "date": current["date"].date().isoformat(),
                "pb_aggregate": float(current["pb_aggregate"]),
                "erp": float(current["erp"]),
                "trailing_dividend_contribution": float(
                    current["trailing_dividend_contribution"]
                ),
                "unbounded_mean_knot": float(current["unbounded_mean_knot"]),
                "unbounded_median_knot": float(
                    current["unbounded_median_knot"]
                ),
                "mean_tier": int(current["mean_tier"]),
                "median_tier": int(current["median_tier"]),
                "consensus_min_tier": int(current["consensus_min_tier"]),
                "either_max_tier": int(current["either_max_tier"]),
                "selected_relationship": decision["selected_relationship"],
                "selected_tier": (
                    int(current[CANDIDATE_COLUMNS[decision["selected_relationship"]]])
                    if decision["selected_relationship"] is not None
                    else math.nan
                ),
                "is_trade_instruction": False,
            }
        ]
    )


def integrity_checks(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    inputs: dict[str, Any],
    agreement: pd.DataFrame,
    confusion: pd.DataFrame,
    events: pd.DataFrame,
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    vintages: pd.DataFrame,
    decision: dict[str, Any],
) -> dict[str, Any]:
    raw_equivalence_failures = 0
    for level, threshold in enumerate(TIER_THRESHOLDS, start=1):
        count = (
            daily["pb_aggregate"].ge(1.50 + 0.50 * threshold).astype(int)
            + daily["erp"].le(0.045 - 0.015 * threshold).astype(int)
            + daily["trailing_dividend_contribution"]
            .le(0.030 - 0.010 * threshold)
            .astype(int)
        )
        raw_equivalence_failures += int(
            (daily["median_tier"].ge(level) != count.ge(2)).sum()
        )
    price_columns = ["ann_return", "ann_vol", "sharpe_repo", "max_dd"]
    price_context_rebuilt = ic_v6.make_price_context(daily)
    price_error = float(
        np.nanmax(
            np.abs(
                price_context_rebuilt[price_columns].to_numpy(float)
                - inputs["price_context"][price_columns].to_numpy(float)
            )
        )
    )
    min_formula_failures = int(
        (
            daily["consensus_min_tier"]
            != daily[["mean_tier", "median_tier"]].min(axis=1)
        ).sum()
    )
    max_formula_failures = int(
        (
            daily["either_max_tier"]
            != daily[["mean_tier", "median_tier"]].max(axis=1)
        ).sum()
    )
    confusion_expected_rows = 2 * 2 * 4 * 4
    confusion_total_mismatches = 0
    for frequency, expected_frame in (("daily", daily), ("monthly", monthly)):
        anchor = expected_frame["date"].max()
        for segment, expected in (
            ("full", len(expected_frame)),
            (
                "last_10y",
                int(
                    (
                        expected_frame["date"]
                        >= anchor - pd.DateOffset(years=10)
                    ).sum()
                ),
            ),
        ):
            actual = int(
                confusion[
                    (confusion["frequency"] == frequency)
                    & (confusion["segment"] == segment)
                ]["rows"].sum()
            )
            confusion_total_mismatches += int(actual != expected)
    expected_candidates = set(CANDIDATE_COLUMNS)
    required_segments = set(WINDOWS)
    candidate_window_complete = all(
        set(part["segment"]) == required_segments
        for _, part in scan_long.groupby("candidate")
    )
    v2_decision = inputs["decision_v2"]
    checks = {
        "daily_rows": len(daily),
        "monthly_rows": len(monthly),
        "start": daily["date"].min().date().isoformat(),
        "end": daily["date"].max().date().isoformat(),
        "v2_platform_low": v2_decision["selected_band_low"],
        "v2_platform_high": v2_decision["selected_band_high"],
        "v2_design_center": v2_decision["design_center_threshold"],
        "candidate_count": int(scan_long["candidate"].nunique()),
        "scan_rows": len(scan_long),
        "wide_rows": len(scan_wide),
        "agreement_rows": len(agreement),
        "confusion_rows": len(confusion),
        "event_rows": len(events),
        "candidate_sets_match": bool(
            set(scan_long["candidate"]) == expected_candidates
            and set(scan_wide["candidate"]) == expected_candidates
        ),
        "candidate_window_complete": bool(candidate_window_complete),
        "tier_value_bounds_pass": bool(
            daily[list(CANDIDATE_COLUMNS.values())].isin(range(4)).all().all()
        ),
        "median_raw_two_of_three_failures": raw_equivalence_failures,
        "consensus_min_formula_failures": min_formula_failures,
        "either_max_formula_failures": max_formula_failures,
        "confusion_expected_rows": confusion_expected_rows,
        "confusion_total_mismatches": confusion_total_mismatches,
        "price_context_max_abs_error_vs_v2": price_error,
        "price_context_unique_max_per_window": int(
            scan_long.groupby("segment")[price_columns]
            .nunique(dropna=False)
            .max()
            .max()
        ),
        "vintage_tier_state_mismatches": int(
            vintages["tier_state_mismatches"].sum()
        ),
        "vintage_future_rows_used": bool(vintages["future_rows_used"].any()),
        "selection_uses_strategy_outcomes": decision[
            "selection_uses_strategy_outcomes"
        ],
    }
    checks["all_checks_passed"] = bool(
        checks["daily_rows"] == EXPECTED_DAILY_ROWS
        and checks["monthly_rows"] == EXPECTED_MONTHLY_ROWS
        and checks["start"] == "2015-10-19"
        and checks["end"] == "2026-08-17"
        and checks["v2_platform_low"] == 2.45
        and checks["v2_platform_high"] == 2.60
        and checks["v2_design_center"] == 2.50
        and checks["candidate_count"] == 4
        and checks["scan_rows"] == 20
        and checks["wide_rows"] == 4
        and checks["agreement_rows"] == 4
        and checks["confusion_rows"] == confusion_expected_rows
        and checks["event_rows"] == 12
        and checks["candidate_sets_match"]
        and checks["candidate_window_complete"]
        and checks["tier_value_bounds_pass"]
        and checks["median_raw_two_of_three_failures"] == 0
        and checks["consensus_min_formula_failures"] == 0
        and checks["either_max_formula_failures"] == 0
        and checks["confusion_total_mismatches"] == 0
        and checks["price_context_max_abs_error_vs_v2"] <= 1e-15
        and checks["price_context_unique_max_per_window"] == 1
        and checks["vintage_tier_state_mismatches"] == 0
        and not checks["vintage_future_rows_used"]
        and not checks["selection_uses_strategy_outcomes"]
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"IM tier relationship integrity failed: {checks}")
    return checks


def build_record(
    agreement: pd.DataFrame,
    events: pd.DataFrame,
    scan_long: pd.DataFrame,
    decision: dict[str, Any],
    current: pd.DataFrame,
) -> str:
    agreement_rows = []
    for row in agreement.itertuples(index=False):
        agreement_rows.append(
            f"| {row.frequency} | {row.segment} | {row.exact_tier_agreement:.2%} | "
            f"{row.weighted_kappa:.3f} | {row.severe_difference_ge_2_ratio:.2%} |"
        )
    event_rows = []
    for row in events.itertuples(index=False):
        if row.candidate not in {"median_primary", "consensus_min"}:
            continue
        event_rows.append(
            f"| {row.candidate} | >={int(row.level_at_least)} | "
            f"{row.recent10_activation_ratio:.2%} | {int(row.full_episodes)}/"
            f"{int(row.recent10_episodes)} | {int(row.early_episode_starts)}/"
            f"{int(row.late_episode_starts)} | "
            f"{'是' if row.event_temporal_gate_pass else '否'} |"
        )
    full_rows = []
    for row in scan_long[scan_long["segment"] == "full"].itertuples(index=False):
        full_rows.append(
            f"| {row.candidate} | {row.daily_avg_tier:.3f} | "
            f"{row.daily_nonzero_ratio:.2%} | {row.daily_annualized_transitions:.2f} | "
            f"{row.monthly_nonzero_ratio:.2%} |"
        )
    now = current.iloc[0]
    return f"""# 中证1000固定经济估值分档与双定义关系 v3

## 结论

- 决定：`{decision['decision']}`；选择：`{decision['selected_relationship']}`；稳定性：`{decision['stability_label']}`。
- 固定阶梯：2.45 / 2.50 / 2.60 -> 1 / 2 / 3档；没有读取任何策略收益。

## 均值—二取三一致性

| 频率 | 窗口 | 精确档位一致率 | 加权Kappa | 相差至少2档 |
| --- | --- | ---: | ---: | ---: |
{chr(10).join(agreement_rows)}

## 事件与时间广度

| 候选 | 累计档位 | 近10年覆盖 | 全样本/近10年段数 | 前/后启动 | 通过 |
| --- | ---: | ---: | ---: | ---: | :---: |
{chr(10).join(event_rows)}

## 全样本状态形态

| 候选 | 日均档位 | 日度非零 | 日度年化切换 | 月末非零 |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(full_rows)}

## 当前状态（{now.date}）

- 均值{now.unbounded_mean_knot:.3f}/档位{int(now.mean_tier)}；二取三{now.unbounded_median_knot:.3f}/档位{int(now.median_tier)}；
- 共同较低档{int(now.consensus_min_tier)}，任一较高档{int(now.either_max_tier)}，选择规则当前档位{now.selected_tier}；
- 估值状态只作为下一层输入候选，不是Put或交易指令。

## 边界

- 真实冻结样本2015-10-19—2026-08-17；中证1000价格指数背景；
- 无交易、费用、滑点、保证金、现金收益、MO、Put、网格或Call；
- 收益和回撤字段只是同窗价格指数背景，不参与选择；未批准实盘。
"""


def update_scan_artifacts(
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    record: str,
    decision: dict[str, Any],
    input_hashes: dict[str, str],
    git_before: str,
) -> None:
    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    scan_record = f"""# Quant Parameter Scan Record

## Run Metadata

- Run id: `{SCAN.name}`
- Run date: 2026-08-18
- Timezone: Asia/Shanghai
- Project: 中证1000固定估值本体
- Version: `{VERSION}`
- Subsystem: valuation_body
- Parameter group: mean_median_tier_relation
- Scan type: candidate_bundle / outcome-free state relationship
- Target entrypoint: `im_fixed_valuation_tier_relationship_v3.py`
- Working tree before: `{git_before}`
- Working tree after: recorded by finalizer

## Research Question

- Baseline: v2 fixed scores and 2.45—2.60 joint platform.
- Candidate grid: median, mean, min-consensus and max-either relations on a fixed 2.45/2.50/2.60 ladder.
- Decision target: freeze one valuation-state candidate for the next Put layer or reject.
- Source-change rule: research_only_no_source_change.
- Required windows: full, last_10y, last_5y, last_3y, last_1y.
- Required metrics: state coverage, tier agreement, transitions, episodes and price-index context.
- Promotion threshold: preregistered agreement/event/temporal/chatter gates; no strategy returns.
- Rerun triggers: any frozen hash, formula, candidate, window or context parity failure.

## Implementation Anchor

- Official upstream: frozen `outputs/im_fixed_valuation_duration_normalized_v2/`.
- Score columns are reused without recomputation or refresh.
- IC research mainline is architecture context only; no IC threshold or Put result is transferred.

## Data Snapshot

- Sample: 2015-10-19—2026-08-17; 2,634 daily rows and 131 month ends.
- Sources: frozen v2 Legulegu/CSI/ChinaBond-derived artifacts.
- Adjustment mode: official CSI1000 price index context.
- Trading calendar/timezone: China trading days / Asia/Shanghai.
- Cache write risk: none; no network download.

## Cost and Execution Assumptions

- No trades, commission, slippage, financing, leverage, cash return, hedge or options.
- Daily tiers diagnose future signal churn; no fill timing is assumed.

## Runtime Override Plan

- No override and no production default.
- All four relations are generated in one run from the same frozen scores.
- Median two-of-three and min/max formula parity are mandatory.

## Commands

```powershell
python im_fixed_valuation_tier_relationship_v3.py
python -m pytest -q test_im_fixed_valuation_tier_relationship_v3.py
uvx ruff check im_fixed_valuation_tier_relationship_v3.py test_im_fixed_valuation_tier_relationship_v3.py
```

## Output Files

- `record.md`, `scan_summary.csv`, `window_metrics.csv`, `scan_meta.json`, `command_log.txt`.
- Formal state and audit tables are in `outputs/{VERSION}/`.

## Full-Sample Results

See `window_metrics.csv`; performance fields are underlying-index context only.

## Window Results

See `scan_summary.csv` for all required windows and state metrics.

## Stability Classification

- Label: `{decision['stability_label']}`.
- Selected relationship: `{decision['selected_relationship']}`.
- Data sensitivity: source platform is a v1-failure-triggered narrow secondary confirmation.

## Decision

- Decision: `{decision['decision']}`.
- Recommended next action: user review before starting the MO Put layer.

## User-Facing Summary

{record}
"""
    (SCAN / "record.md").write_text(scan_record, encoding="utf-8")
    meta = json.loads((SCAN / "scan_meta.json").read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle_outcome_free_state_relationship",
            "parameter_group": "mean_median_tier_relation",
            "baseline": {
                "definition": "v2 fixed scores and fixed 2.45/2.50/2.60 ladder"
            },
            "candidate_grid": [
                {"candidate": candidate, "column": column}
                for candidate, column in CANDIDATE_COLUMNS.items()
            ],
            "data_snapshot": {
                "start": "2015-10-19",
                "end": "2026-08-17",
                "daily_rows": EXPECTED_DAILY_ROWS,
                "monthly_rows": EXPECTED_MONTHLY_ROWS,
                "source": "frozen v2 local real-data artifacts",
                "cache_writes": "none",
            },
            "cost_model": {"applicable": False},
            "decision": decision["decision"],
            "stability_label": decision["stability_label"],
            "source_hashes": input_hashes,
            "warnings": [
                "selection is state-structure-only; no strategy return used",
                "v2 platform is secondary confirmation and narrow",
                "research only, not live approved",
            ],
        }
    )
    (SCAN / "scan_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SCAN / "command_log.txt").write_text(
        "Working directory: D:\\动量策略\\新策略研究\n"
        "python im_fixed_valuation_tier_relationship_v3.py\n"
        "python -m pytest -q test_im_fixed_valuation_tier_relationship_v3.py\n"
        "uvx ruff check im_fixed_valuation_tier_relationship_v3.py "
        "test_im_fixed_valuation_tier_relationship_v3.py\n",
        encoding="utf-8",
    )


def main() -> None:
    input_hashes = verify_frozen_inputs(require_fresh_output=True)
    git_before = git_status()
    inputs = load_inputs()
    daily = add_tier_states(inputs["daily"])
    monthly = add_tier_states(inputs["monthly"])
    definition = tier_definition()
    economic_map = economic_tier_map()
    agreement = agreement_summary(daily, monthly)
    confusion = confusion_matrix_table(daily, monthly)
    events = cumulative_event_audit(monthly)
    price_context = ic_v6.make_price_context(daily)
    scan_long, scan_wide = make_scan(daily, monthly, price_context)
    decision = select_relationship(agreement, events, scan_long)
    current = current_state(daily, decision)
    extremes = disagreement_extremes(daily)
    vintages = vintage_invariance(monthly)
    checks = integrity_checks(
        daily,
        monthly,
        inputs,
        agreement,
        confusion,
        events,
        scan_long,
        scan_wide,
        vintages,
        decision,
    )
    record = build_record(
        agreement, events, scan_long, decision, current
    )

    STAGING.mkdir(parents=True)
    daily.to_csv(
        STAGING / "daily_tier_states.csv.gz", index=False, compression="gzip"
    )
    monthly.to_csv(STAGING / "monthly_tier_states.csv", index=False)
    definition.to_csv(STAGING / "tier_definition.csv", index=False)
    economic_map.to_csv(STAGING / "tier_economic_map.csv", index=False)
    agreement.to_csv(STAGING / "mean_median_agreement.csv", index=False)
    confusion.to_csv(STAGING / "mean_median_confusion_matrix.csv", index=False)
    events.to_csv(STAGING / "cumulative_event_audit.csv", index=False)
    extremes.to_csv(STAGING / "disagreement_extremes.csv", index=False)
    vintages.to_csv(STAGING / "vintage_invariance.csv", index=False)
    price_context.to_csv(STAGING / "price_index_context.csv", index=False)
    scan_long.to_csv(STAGING / "scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "window_metrics.csv", index=False)
    current.to_csv(STAGING / "current_state.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python im_fixed_valuation_tier_relationship_v3.py\n"
        "python -m pytest -q test_im_fixed_valuation_tier_relationship_v3.py\n"
        "uvx ruff check im_fixed_valuation_tier_relationship_v3.py "
        "test_im_fixed_valuation_tier_relationship_v3.py\n",
        encoding="utf-8",
    )
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now(UTC).astimezone().isoformat(),
        "spec_sha256": SPEC_HASH,
        "script_sha256": sha256(Path(__file__)),
        "input_hashes": input_hashes,
        "sample": {
            "start": "2015-10-19",
            "end": "2026-08-17",
            "daily_rows": len(daily),
            "monthly_rows": len(monthly),
        },
        "selection_uses_strategy_outcomes": False,
        "decision": decision,
        "integrity": checks,
        "git_status_before": git_before,
        "git_status_after": git_status(),
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files = {
        path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(STAGING.iterdir())
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(
            {"version": VERSION, "spec_sha256": SPEC_HASH, "files": files},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    update_scan_artifacts(
        scan_long, scan_wide, record, decision, input_hashes, git_before
    )
    try:
        STAGING.rename(OUTPUT)
    except Exception:
        failed = (
            ROOT
            / "outputs"
            / f"{VERSION}_failed_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        )
        if STAGING.exists():
            shutil.move(str(STAGING), str(failed))
        raise
    print(json.dumps({"decision": decision, "integrity": checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
