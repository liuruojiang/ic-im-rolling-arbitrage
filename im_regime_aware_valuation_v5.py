from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_tier_relationship_v3 as v3

ROOT = Path(__file__).resolve().parent
VERSION = "im_regime_aware_valuation_v5"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "982d5a97dff744afe35481b52080e9c1acdd73c9ae9434f775e927975aa510d0"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V3_OUTPUT = ROOT / "outputs" / "im_fixed_valuation_tier_relationship_v3"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_1000_im_regime_aware_valuation_v5"

HALF_LIVES = (5.0, 7.5, 10.0)
LADDERS = {
    "q708090": (0.70, 0.80, 0.90),
    "q758595": (0.75, 0.85, 0.95),
}
MIN_MONTHS = 36
PRIMARY = "dual_h75_q758595"
NEIGHBORS = ("dual_h5_q758595", "dual_h10_q758595")
WINDOWS = v3.WINDOWS
EXPECTED_DAILY_ROWS = 2634
EXPECTED_MONTHLY_ROWS = 131

INPUT_HASHES = {
    ROOT
    / "im_fixed_valuation_tier_relationship_v3.py": "4e5c36ab2dcc5ec9d8e6d3ba3c8dd4ee9e2bf705c54c620390326efab967fe4d",
    ROOT
    / "docs"
    / "im_fixed_valuation_tier_relationship_v3_spec.md": "dbc096f7dfbbfec2724f6889e0000564b283c8b52dc00e73da18e430ba3759c5",
    ROOT
    / "docs"
    / "im_fixed_valuation_tier_relationship_v3_postrun_audit.md": "502817a954274f412092ee20eceef9c4b0bf6390ce0ccaa22f51efef49ac882b",
    ROOT
    / "docs"
    / "im_mo_fixed_valuation_delta_put_v4_postrun_audit.md": "21ec9cbf1e862c4fa720ed58c6ef9530d17f4dc2f86e3b84b979528092f47cf6",
    V3_OUTPUT
    / "daily_tier_states.csv.gz": "dd91b80172553a1dbe53e79bdc5870ca32af7e7ed5171c001e356ad28c9e3912",
    V3_OUTPUT
    / "monthly_tier_states.csv": "cf0fd930dea38d06598ed20e259578d88d35394f31875ec526db0c2584e565e7",
    V3_OUTPUT
    / "price_index_context.csv": "1b04a18efe8b73f5becb164d8276b5ed07b216f647931873771815983ec6ac8c",
    V3_OUTPUT
    / "decision_summary.json": "75be2c3c9c5bdfb0fae1af382e31cdaffa2f197d6bb9f4c894abf62b0b0c5cf8",
    V3_OUTPUT
    / "integrity_checks.json": "8d9d818833cf2e081e9184a168878f74843e05ed1fa6059a06da164bc62f1503",
    V3_OUTPUT
    / "data_manifest.json": "7a9575314d5e760f56e6223c33fe9ac83e0d38bcacf0e263a814dfbac3dc544d",
    V3_OUTPUT
    / "output_manifest.json": "d428a21b4d8e40ab4c9a4146f5607aaaac64ae382df2d94fb6e3594878c75dbf",
}


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
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip()


def verify_inputs(*, require_fresh_output: bool) -> dict[str, str]:
    if sha256(SPEC) != SPEC_HASH:
        raise RuntimeError("Frozen v5 specification mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_HASH:
        raise RuntimeError("Frozen v5 specification sidecar mismatch")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v5 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v5 scan directory is missing")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v5 input changed: {path}")
    decision = json.loads(
        (V3_OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    if decision["selected_relationship"] != "median_primary" or decision[
        "tier_thresholds"
    ] != [2.45, 2.5, 2.6]:
        raise RuntimeError("Frozen v3 median tier input mismatch")
    return {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()}


def half_life_label(value: float) -> str:
    return "75" if math.isclose(value, 7.5) else str(int(value))


def candidate_definitions() -> pd.DataFrame:
    rows: list[dict[str, Any]] = [
        {
            "candidate": "absolute_v3",
            "kind": "absolute",
            "half_life_years": np.nan,
            "ladder": "absolute_245_250_260",
            "quantiles": "",
            "eligible": False,
        }
    ]
    for half_life in HALF_LIVES:
        label = half_life_label(half_life)
        for ladder, quantiles in LADDERS.items():
            for kind in ("relative", "dual"):
                rows.append(
                    {
                        "candidate": f"{kind}_h{label}_{ladder}",
                        "kind": kind,
                        "half_life_years": half_life,
                        "ladder": ladder,
                        "quantiles": "/".join(f"{value:.2f}" for value in quantiles),
                        "eligible": kind == "dual" and ladder == "q758595",
                    }
                )
    return pd.DataFrame(rows)


def load_inputs() -> dict[str, pd.DataFrame]:
    return {
        "daily": pd.read_csv(
            V3_OUTPUT / "daily_tier_states.csv.gz", parse_dates=["date"]
        ),
        "monthly": pd.read_csv(
            V3_OUTPUT / "monthly_tier_states.csv", parse_dates=["date"]
        ),
        "price_context": pd.read_csv(V3_OUTPUT / "price_index_context.csv"),
    }


def weighted_quantile(values: pd.Series, weights: pd.Series, quantile: float) -> float:
    if not 0 < quantile < 1:
        raise ValueError("Weighted quantile must be strictly between zero and one")
    frame = pd.DataFrame(
        {"value": values.astype(float), "weight": weights.astype(float)}
    ).sort_values(["value"], kind="mergesort")
    if frame.empty or frame["weight"].le(0).any():
        raise ValueError("Invalid weighted quantile sample")
    cumulative = frame["weight"].cumsum()
    target = quantile * float(frame["weight"].sum())
    return float(frame.loc[cumulative.ge(target), "value"].iloc[0])


def calibration_row(
    monthly: pd.DataFrame,
    year: int,
    half_life: float,
    ladder: str,
) -> dict[str, Any]:
    as_of = pd.Timestamp(year - 1, 12, 31)
    history = monthly[monthly["date"].le(as_of)].copy()
    row: dict[str, Any] = {
        "year": year,
        "as_of": as_of,
        "half_life_years": half_life,
        "half_life_label": half_life_label(half_life),
        "ladder": ladder,
        "sample_months": len(history),
        "calibration_start": history["date"].min() if len(history) else pd.NaT,
        "calibration_end": history["date"].max() if len(history) else pd.NaT,
        "max_input_date": history["date"].max() if len(history) else pd.NaT,
        "future_rows_used": int(history["date"].gt(as_of).sum()),
        "available": len(history) >= MIN_MONTHS,
    }
    if len(history) < MIN_MONTHS:
        return {
            **row,
            "effective_months": np.nan,
            "last3_weight_share": np.nan,
            "last5_weight_share": np.nan,
            "last10_weight_share": np.nan,
            "pre2022_weight_share": np.nan,
            "threshold_1": np.nan,
            "threshold_2": np.nan,
            "threshold_3": np.nan,
            "strictly_increasing": False,
        }
    age_years = (as_of - history["date"]).dt.days / 365.2425
    history["weight"] = np.power(0.5, age_years / half_life)
    total = float(history["weight"].sum())
    quantiles = LADDERS[ladder]
    thresholds = [
        weighted_quantile(history["unbounded_median_knot"], history["weight"], quantile)
        for quantile in quantiles
    ]
    effective = total**2 / float((history["weight"] ** 2).sum())

    def share_since(years: int) -> float:
        boundary = as_of - pd.DateOffset(years=years)
        return float(history.loc[history["date"].gt(boundary), "weight"].sum() / total)

    return {
        **row,
        "effective_months": effective,
        "last3_weight_share": share_since(3),
        "last5_weight_share": share_since(5),
        "last10_weight_share": share_since(10),
        "pre2022_weight_share": float(
            history.loc[history["date"].lt(pd.Timestamp("2022-01-01")), "weight"].sum()
            / total
        ),
        "threshold_1": thresholds[0],
        "threshold_2": thresholds[1],
        "threshold_3": thresholds[2],
        "strictly_increasing": bool(thresholds[0] < thresholds[1] < thresholds[2]),
    }


def annual_thresholds(monthly: pd.DataFrame) -> pd.DataFrame:
    years = range(
        int(monthly["date"].dt.year.min()), int(monthly["date"].dt.year.max()) + 1
    )
    rows = [
        calibration_row(monthly, year, half_life, ladder)
        for half_life in HALF_LIVES
        for ladder in LADDERS
        for year in years
    ]
    return pd.DataFrame(rows)


def assign_relative_tier(
    frame: pd.DataFrame, threshold: pd.DataFrame, half_life: float, ladder: str
) -> pd.DataFrame:
    selected = threshold[
        threshold["half_life_years"].eq(half_life) & threshold["ladder"].eq(ladder)
    ][
        [
            "year",
            "available",
            "threshold_1",
            "threshold_2",
            "threshold_3",
        ]
    ]
    result = frame.copy()
    result["year"] = result["date"].dt.year
    result = result.merge(selected, on="year", how="left", validate="many_to_one")
    available = result["available"].fillna(False)
    score = result["unbounded_median_knot"]
    result["relative_tier"] = np.select(
        [
            available & score.ge(result["threshold_3"]),
            available & score.ge(result["threshold_2"]),
            available & score.ge(result["threshold_1"]),
        ],
        [3, 2, 1],
        default=0,
    ).astype(int)
    result["calibrated"] = available.astype(bool)
    return result


def build_states(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    thresholds: pd.DataFrame,
    definitions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    outputs: dict[str, list[pd.DataFrame]] = {"daily": [], "monthly": []}
    for frequency, frame in (("daily", daily), ("monthly", monthly)):
        absolute = frame.copy()
        absolute["candidate"] = "absolute_v3"
        absolute["absolute_tier"] = absolute["median_tier"].astype(int)
        absolute["relative_tier"] = 0
        absolute["final_tier"] = absolute["absolute_tier"]
        absolute["calibrated"] = False
        absolute["threshold_1"] = np.nan
        absolute["threshold_2"] = np.nan
        absolute["threshold_3"] = np.nan
        outputs[frequency].append(absolute)
        for half_life in HALF_LIVES:
            h_label = half_life_label(half_life)
            for ladder in LADDERS:
                relative = assign_relative_tier(frame, thresholds, half_life, ladder)
                relative["absolute_tier"] = relative["median_tier"].astype(int)
                for kind in ("relative", "dual"):
                    item = relative.copy()
                    item["candidate"] = f"{kind}_h{h_label}_{ladder}"
                    item["final_tier"] = (
                        item["relative_tier"]
                        if kind == "relative"
                        else item[["absolute_tier", "relative_tier"]].max(axis=1)
                    ).astype(int)
                    outputs[frequency].append(item)
    keep = [
        "date",
        "candidate",
        "price_close",
        "tri_close",
        "pb_aggregate",
        "erp",
        "trailing_dividend_contribution",
        "unbounded_median_knot",
        "absolute_tier",
        "relative_tier",
        "final_tier",
        "calibrated",
        "threshold_1",
        "threshold_2",
        "threshold_3",
    ]
    expected = set(definitions["candidate"])
    result_daily = pd.concat(outputs["daily"], ignore_index=True)[keep]
    result_monthly = pd.concat(outputs["monthly"], ignore_index=True)[keep]
    if (
        set(result_daily["candidate"]) != expected
        or set(result_monthly["candidate"]) != expected
    ):
        raise RuntimeError("Candidate state construction mismatch")
    return result_daily, result_monthly


def state_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    tier = frame["final_tier"].astype(int).reset_index(drop=True)
    active = tier.gt(0)
    episodes, longest = v3.ic_v6.episode_stats(active)
    comparison = frame["absolute_tier"].astype(int).reset_index(drop=True)
    transitions = v3.transitions_per_year(
        frame.sort_values("date").reset_index(drop=True), "final_tier"
    )
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
        "exact_agreement_vs_absolute": float(tier.eq(comparison).mean()),
        "mean_abs_tier_diff_vs_absolute": float((tier - comparison).abs().mean()),
        **transitions,
    }


def make_scan(
    daily_states: pd.DataFrame,
    monthly_states: pd.DataFrame,
    price_context: pd.DataFrame,
    definitions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor = daily_states["date"].max()
    boundaries = v3.ic_v6.window_boundaries(anchor)
    context = price_context.set_index("segment")
    rows: list[dict[str, Any]] = []
    for definition in definitions.itertuples(index=False):
        daily = daily_states[daily_states["candidate"].eq(definition.candidate)]
        monthly = monthly_states[monthly_states["candidate"].eq(definition.candidate)]
        for segment in WINDOWS:
            boundary = boundaries[segment]
            daily_part = (
                daily if boundary is None else daily[daily["date"].ge(boundary)]
            )
            monthly_part = (
                monthly if boundary is None else monthly[monthly["date"].ge(boundary)]
            )
            price = context.loc[segment]
            dm = state_metrics(daily_part)
            mm = state_metrics(monthly_part)
            rows.append(
                {
                    "candidate": definition.candidate,
                    "segment": segment,
                    "start": price["start"],
                    "end": price["end"],
                    "rows": int(price["rows"]),
                    "ann_return": float(price["ann_return"]),
                    "ann_vol": float(price["ann_vol"]),
                    "sharpe_repo": float(price["sharpe_repo"]),
                    "max_dd": float(price["max_dd"]),
                    "kind": definition.kind,
                    "half_life_years": definition.half_life_years,
                    "ladder": definition.ladder,
                    **{f"daily_{key}": value for key, value in dm.items()},
                    **{f"monthly_{key}": value for key, value in mm.items()},
                    "metric_semantics": "underlying_price_index_context_only_no_strategy_return",
                }
            )
    long = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    metrics = (
        "ann_return",
        "ann_vol",
        "sharpe_repo",
        "max_dd",
        "daily_avg_tier",
        "daily_nonzero_ratio",
        "daily_tier2plus_ratio",
        "daily_tier3plus_ratio",
        "daily_annualized_transitions",
        "monthly_nonzero_ratio",
    )
    for candidate, part in long.groupby("candidate", sort=False):
        row: dict[str, Any] = {"candidate": candidate}
        for item in part.itertuples(index=False):
            for metric in metrics:
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def epoch_metrics(daily_states: pd.DataFrame) -> pd.DataFrame:
    epochs = {
        "2015_2018": (pd.Timestamp("2015-10-19"), pd.Timestamp("2018-12-31")),
        "2019_2021": (pd.Timestamp("2019-01-01"), pd.Timestamp("2021-12-31")),
        "2022_now": (pd.Timestamp("2022-01-01"), pd.Timestamp("2026-08-17")),
        "2019_now": (pd.Timestamp("2019-01-01"), pd.Timestamp("2026-08-17")),
    }
    rows: list[dict[str, Any]] = []
    for candidate, group in daily_states.groupby("candidate"):
        for epoch, (start, end) in epochs.items():
            part = group[group["date"].between(start, end)].copy()
            rows.append(
                {
                    "candidate": candidate,
                    "epoch": epoch,
                    "start": part["date"].min(),
                    "end": part["date"].max(),
                    "rows": len(part),
                    **state_metrics(part),
                    "active_years": int(
                        part.loc[part["final_tier"].gt(0), "date"].dt.year.nunique()
                    ),
                }
            )
    return pd.DataFrame(rows)


def event_audit(monthly_states: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "2019_2021": (pd.Timestamp("2019-01-01"), pd.Timestamp("2021-12-31")),
        "2022_now": (pd.Timestamp("2022-01-01"), pd.Timestamp("2026-08-17")),
    }
    rows: list[dict[str, Any]] = []
    for candidate, group in monthly_states.groupby("candidate"):
        for period, (start, end) in periods.items():
            part = group[group["date"].between(start, end)].sort_values("date")
            for level in (1, 2, 3):
                active = part["final_tier"].ge(level).reset_index(drop=True)
                episodes, longest = v3.ic_v6.episode_stats(active)
                values = active.to_numpy(bool)
                starts = values & ~np.r_[False, values[:-1]]
                positions = np.flatnonzero(starts)
                rows.append(
                    {
                        "candidate": candidate,
                        "period": period,
                        "level_at_least": level,
                        "rows": len(part),
                        "activation_ratio": float(active.mean()),
                        "episodes": episodes,
                        "longest_months": longest,
                        "active_years": int(
                            part.loc[active.to_numpy(), "date"].dt.year.nunique()
                        ),
                        "start_dates": "|".join(
                            part.iloc[positions]["date"].dt.strftime("%Y-%m-%d")
                        ),
                    }
                )
    return pd.DataFrame(rows)


def threshold_drift(thresholds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    available = thresholds[thresholds["available"]].copy()
    for (half_life, ladder), group in available.groupby(["half_life_years", "ladder"]):
        group = group.sort_values("year")
        row: dict[str, Any] = {
            "half_life_years": half_life,
            "ladder": ladder,
            "available_years": len(group),
            "first_year": int(group["year"].min()),
            "last_year": int(group["year"].max()),
        }
        for number in (1, 2, 3):
            changes = group[f"threshold_{number}"].diff().abs().dropna()
            row[f"threshold_{number}_median_abs_change"] = float(changes.median())
            row[f"threshold_{number}_max_abs_change"] = float(changes.max())
        rows.append(row)
    return pd.DataFrame(rows)


def nonzero_jaccard(left: pd.Series, right: pd.Series) -> float:
    left_active = left.astype(int).gt(0)
    right_active = right.astype(int).gt(0)
    union = left_active | right_active
    return (
        1.0
        if not union.any()
        else float((left_active & right_active).sum() / union.sum())
    )


def stability_matrix(daily_states: pd.DataFrame) -> pd.DataFrame:
    candidates = [PRIMARY, *NEIGHBORS]
    part = daily_states[
        daily_states["candidate"].isin(candidates)
        & daily_states["date"].ge("2022-01-01")
    ][["date", "candidate", "final_tier"]]
    pivot = part.pivot(index="date", columns="candidate", values="final_tier")
    rows: list[dict[str, Any]] = []
    for neighbor in NEIGHBORS:
        left = pivot[PRIMARY].astype(int)
        right = pivot[neighbor].astype(int)
        rows.append(
            {
                "primary": PRIMARY,
                "neighbor": neighbor,
                "rows": len(pivot),
                "exact_tier_agreement": float(left.eq(right).mean()),
                "weighted_kappa": v3.weighted_kappa(left, right),
                "nonzero_jaccard": nonzero_jaccard(left, right),
                "mean_abs_tier_difference": float((left - right).abs().mean()),
            }
        )
    return pd.DataFrame(rows)


def vintage_audit(monthly: pd.DataFrame, thresholds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in thresholds[thresholds["available"]].itertuples(index=False):
        recalculated = calibration_row(
            monthly[monthly["date"].le(pd.Timestamp(row.as_of))],
            int(row.year),
            float(row.half_life_years),
            str(row.ladder),
        )
        maximum = max(
            abs(
                float(recalculated[f"threshold_{number}"])
                - float(getattr(row, f"threshold_{number}"))
            )
            for number in (1, 2, 3)
        )
        rows.append(
            {
                "year": int(row.year),
                "half_life_years": float(row.half_life_years),
                "ladder": row.ladder,
                "as_of": row.as_of,
                "formal_max_input_date": row.max_input_date,
                "recalculated_max_input_date": recalculated["max_input_date"],
                "threshold_max_abs_error": maximum,
                "future_rows_used": int(recalculated["future_rows_used"]),
            }
        )
    return pd.DataFrame(rows)


def current_state(
    daily_states: pd.DataFrame, thresholds: pd.DataFrame, definitions: pd.DataFrame
) -> pd.DataFrame:
    latest = daily_states["date"].max()
    current = daily_states[daily_states["date"].eq(latest)].copy()
    current = current.merge(
        definitions[["candidate", "kind", "half_life_years", "ladder", "eligible"]],
        on="candidate",
        validate="one_to_one",
    )
    return current[
        [
            "date",
            "candidate",
            "kind",
            "half_life_years",
            "ladder",
            "eligible",
            "unbounded_median_knot",
            "absolute_tier",
            "relative_tier",
            "final_tier",
            "threshold_1",
            "threshold_2",
            "threshold_3",
        ]
    ].sort_values(["kind", "candidate"])


def candidate_gate(
    candidate: str,
    epochs: pd.DataFrame,
    events: pd.DataFrame,
) -> dict[str, Any]:
    recent = epochs[
        epochs["candidate"].eq(candidate) & epochs["epoch"].eq("2022_now")
    ].iloc[0]
    since2019 = epochs[
        epochs["candidate"].eq(candidate) & epochs["epoch"].eq("2019_now")
    ].iloc[0]
    event_recent = events[
        events["candidate"].eq(candidate) & events["period"].eq("2022_now")
    ].set_index("level_at_least")
    event_prior = events[
        events["candidate"].eq(candidate) & events["period"].eq("2019_2021")
    ].set_index("level_at_least")
    return {
        "coverage_gate": bool(
            0.10 <= recent["nonzero_ratio"] <= 0.40
            and 0.04 <= recent["tier2plus_ratio"] <= 0.30
            and 0.01 <= recent["tier3plus_ratio"] <= 0.20
        ),
        "exact_tier_resolution_gate": bool(
            recent["tier1_ratio"] >= 0.01
            and recent["tier2_ratio"] >= 0.01
            and recent["tier3_ratio"] >= 0.01
        ),
        "recent_event_gate": bool(
            int(event_recent.loc[1, "episodes"]) >= 2
            and int(event_recent.loc[2, "episodes"]) >= 2
            and int(event_recent.loc[3, "episodes"]) >= 1
            and int(recent["active_years"]) >= 3
        ),
        "prior_event_gate": bool(int(event_prior.loc[1, "episodes"]) >= 1),
        "churn_gate": bool(float(since2019["annualized_transitions"]) <= 24.0),
        "recent_nonzero_ratio": float(recent["nonzero_ratio"]),
        "recent_tier1_ratio": float(recent["tier1_ratio"]),
        "recent_tier2_ratio": float(recent["tier2_ratio"]),
        "recent_tier3_ratio": float(recent["tier3_ratio"]),
        "recent_level1_episodes": int(event_recent.loc[1, "episodes"]),
        "recent_level2_episodes": int(event_recent.loc[2, "episodes"]),
        "recent_level3_episodes": int(event_recent.loc[3, "episodes"]),
        "recent_active_years": int(recent["active_years"]),
        "since2019_annualized_transitions": float(since2019["annualized_transitions"]),
    }


def select_candidate(
    epochs: pd.DataFrame,
    events: pd.DataFrame,
    drift: pd.DataFrame,
    stability: pd.DataFrame,
    thresholds: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    gates: dict[str, dict[str, Any]] = {}
    for candidate in (PRIMARY, *NEIGHBORS):
        gate = candidate_gate(candidate, epochs, events)
        gate["core_gate_pass"] = all(
            gate[key]
            for key in (
                "coverage_gate",
                "exact_tier_resolution_gate",
                "recent_event_gate",
                "prior_event_gate",
                "churn_gate",
            )
        )
        gates[candidate] = gate
        rows.append({"candidate": candidate, **gate})

    drift_row = drift[
        drift["half_life_years"].eq(7.5) & drift["ladder"].eq("q758595")
    ].iloc[0]
    drift_pass = bool(
        all(
            float(drift_row[f"threshold_{number}_median_abs_change"]) <= 0.35
            and float(drift_row[f"threshold_{number}_max_abs_change"]) <= 0.75
            for number in (1, 2, 3)
        )
    )
    current_calibration = thresholds[
        thresholds["half_life_years"].eq(7.5)
        & thresholds["ladder"].eq("q758595")
        & thresholds["year"].eq(2026)
    ].iloc[0]
    weight_pass = bool(float(current_calibration["last5_weight_share"]) >= 0.50)
    stability_pass = bool(
        stability["exact_tier_agreement"].ge(0.70).all()
        and stability["weighted_kappa"].ge(0.60).all()
        and stability["nonzero_jaccard"].ge(0.75).all()
    )
    primary_pass = bool(gates[PRIMARY]["core_gate_pass"] and drift_pass and weight_pass)
    neighbor_pass = bool(
        all(gates[candidate]["core_gate_pass"] for candidate in NEIGHBORS)
        and stability_pass
    )
    if primary_pass and neighbor_pass:
        decision = "freeze_dual_h75_q758595_for_next_put_layer"
        stability_label = "wide_stable"
        selected = PRIMARY
    elif primary_pass:
        decision = "watchlist_regime_aware_dual_axis"
        stability_label = "peak_only"
        selected = None
    else:
        decision = "no_regime_aware_valuation_candidate"
        stability_label = "reject"
        selected = None
    summary = {
        "decision": decision,
        "stability_label": stability_label,
        "selected_candidate": selected,
        "primary_candidate": PRIMARY,
        "primary_core_gate_pass": gates[PRIMARY]["core_gate_pass"],
        "primary_drift_pass": drift_pass,
        "primary_weight_pass": weight_pass,
        "neighbor_core_gate_pass": all(
            gates[candidate]["core_gate_pass"] for candidate in NEIGHBORS
        ),
        "neighbor_stability_pass": stability_pass,
        "current_last5_weight_share": float(current_calibration["last5_weight_share"]),
        "current_pre2022_weight_share": float(
            current_calibration["pre2022_weight_share"]
        ),
        "selection_uses_strategy_outcomes": False,
        "live_approved": False,
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }
    return pd.DataFrame(rows), summary


def integrity_checks(
    inputs: dict[str, pd.DataFrame],
    definitions: pd.DataFrame,
    thresholds: pd.DataFrame,
    daily_states: pd.DataFrame,
    monthly_states: pd.DataFrame,
    scan_long: pd.DataFrame,
    vintage: pd.DataFrame,
) -> dict[str, Any]:
    absolute = daily_states[daily_states["candidate"].eq("absolute_v3")]
    source = inputs["daily"][["date", "median_tier"]]
    absolute_join = absolute[["date", "final_tier"]].merge(
        source, on="date", validate="one_to_one"
    )
    dual = daily_states[daily_states["candidate"].str.startswith("dual_")]
    dual_identity = int(
        (
            dual["final_tier"] != dual[["absolute_tier", "relative_tier"]].max(axis=1)
        ).sum()
    )
    price_uniques = (
        scan_long.groupby("segment")[["ann_return", "ann_vol", "sharpe_repo", "max_dd"]]
        .nunique()
        .max()
        .max()
    )
    available_thresholds = thresholds[thresholds["available"]]
    checks = {
        "candidate_count": len(definitions),
        "daily_source_rows": len(inputs["daily"]),
        "monthly_source_rows": len(inputs["monthly"]),
        "daily_state_rows": len(daily_states),
        "monthly_state_rows": len(monthly_states),
        "scan_rows": len(scan_long),
        "absolute_v3_parity_failures": int(
            (absolute_join["final_tier"] != absolute_join["median_tier"]).sum()
        ),
        "dual_max_identity_failures": dual_identity,
        "tier_out_of_range_rows": int(
            (~daily_states["final_tier"].isin([0, 1, 2, 3])).sum()
        ),
        "available_threshold_rows": len(available_thresholds),
        "threshold_order_failures": int(
            (~available_thresholds["strictly_increasing"]).sum()
        ),
        "threshold_future_rows_used": int(thresholds["future_rows_used"].sum()),
        "vintage_threshold_max_abs_error": float(
            vintage["threshold_max_abs_error"].max()
        ),
        "vintage_future_rows_used": int(vintage["future_rows_used"].sum()),
        "price_context_max_unique_per_window": int(price_uniques),
        "candidate_window_duplicates": int(
            scan_long.duplicated(["candidate", "segment"]).sum()
        ),
        "earliest_relative_available_year": int(available_thresholds["year"].min()),
    }
    checks["integrity_pass"] = bool(
        checks["candidate_count"] == 13
        and checks["daily_source_rows"] == EXPECTED_DAILY_ROWS
        and checks["monthly_source_rows"] == EXPECTED_MONTHLY_ROWS
        and checks["daily_state_rows"] == EXPECTED_DAILY_ROWS * len(definitions)
        and checks["monthly_state_rows"] == EXPECTED_MONTHLY_ROWS * len(definitions)
        and checks["scan_rows"] == len(definitions) * len(WINDOWS)
        and checks["absolute_v3_parity_failures"] == 0
        and checks["dual_max_identity_failures"] == 0
        and checks["tier_out_of_range_rows"] == 0
        and checks["threshold_order_failures"] == 0
        and checks["threshold_future_rows_used"] == 0
        and checks["vintage_threshold_max_abs_error"] <= 1e-14
        and checks["vintage_future_rows_used"] == 0
        and checks["price_context_max_unique_per_window"] == 1
        and checks["candidate_window_duplicates"] == 0
        and checks["earliest_relative_available_year"] == 2019
    )
    if not checks["integrity_pass"]:
        raise RuntimeError(f"IM regime-aware valuation integrity failed: {checks}")
    return checks


def build_record(
    summary: dict[str, Any],
    current: pd.DataFrame,
    epochs: pd.DataFrame,
    stability: pd.DataFrame,
    drift: pd.DataFrame,
    checks: dict[str, Any],
) -> str:
    current_display = current[
        current["candidate"].isin(["absolute_v3", PRIMARY, *NEIGHBORS])
    ]
    epoch_display = epochs[
        epochs["candidate"].isin(["absolute_v3", PRIMARY, *NEIGHBORS])
        & epochs["epoch"].isin(["2019_2021", "2022_now"])
    ][
        [
            "candidate",
            "epoch",
            "nonzero_ratio",
            "tier1_ratio",
            "tier2_ratio",
            "tier3_ratio",
            "nonzero_episodes",
            "active_years",
            "annualized_transitions",
        ]
    ]
    drift_display = drift[drift["ladder"].eq("q758595")]
    return "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            "> 双轴估值本体研究：绝对经济轴 + 年度锁定近期环境轴；不含Put或策略收益。",
            "",
            "## 预注册决定",
            "",
            f"- 决定：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
            f"- 主候选：`{PRIMARY}`；主自身/漂移/近期权重门槛：{summary['primary_core_gate_pass']}/{summary['primary_drift_pass']}/{summary['primary_weight_pass']}。",
            f"- 邻点自身/状态一致性：{summary['neighbor_core_gate_pass']}/{summary['neighbor_stability_pass']}。",
            f"- 2026校准最近5年权重{summary['current_last5_weight_share']:.2%}，2022年前权重{summary['current_pre2022_weight_share']:.2%}。",
            "",
            "## 当前状态",
            "",
            current_display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 时代状态分布",
            "",
            epoch_display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 半衰期邻点",
            "",
            stability.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 年度阈值漂移",
            "",
            drift_display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 完整性与边界",
            "",
            f"- 最早近期轴年份：{checks['earliest_relative_available_year']}；未来行使用{checks['threshold_future_rows_used']}；历史时点最大重算误差{checks['vintage_threshold_max_abs_error']:.3e}。",
            "- 收益、波动、Sharpe和MaxDD只表示中证1000价格指数同窗背景，没有参与候选选择。",
            "- 本版没有持仓、交易、期权、手续费、滑点、保证金或现金收益；通过也只允许进入下一层研究。",
            "- 当前档位仅为研究审计，不是交易指令；未批准实盘。",
            "",
        ]
    )


def output_manifest(folder: Path) -> dict[str, Any]:
    return {
        "version": VERSION,
        "spec_sha256": SPEC_HASH,
        "files": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sorted(folder.iterdir())
            if path.name != "output_manifest.json"
        },
    }


def write_outputs(
    source_hashes: dict[str, str],
    inputs: dict[str, pd.DataFrame],
    definitions: pd.DataFrame,
    thresholds: pd.DataFrame,
    daily_states: pd.DataFrame,
    monthly_states: pd.DataFrame,
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    epochs: pd.DataFrame,
    events: pd.DataFrame,
    drift: pd.DataFrame,
    stability: pd.DataFrame,
    vintage: pd.DataFrame,
    current: pd.DataFrame,
    gate_table: pd.DataFrame,
    summary: dict[str, Any],
    checks: dict[str, Any],
) -> None:
    STAGING.mkdir(parents=True, exist_ok=False)
    definitions.to_csv(STAGING / "candidate_definitions.csv", index=False)
    thresholds.to_csv(STAGING / "annual_thresholds.csv", index=False)
    daily_states.to_csv(
        STAGING / "daily_candidate_states.csv.gz", index=False, compression="gzip"
    )
    monthly_states.to_csv(STAGING / "monthly_candidate_states.csv", index=False)
    scan_long.to_csv(STAGING / "scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "window_metrics.csv", index=False)
    epochs.to_csv(STAGING / "epoch_state_metrics.csv", index=False)
    events.to_csv(STAGING / "event_audit.csv", index=False)
    drift.to_csv(STAGING / "threshold_drift.csv", index=False)
    stability.to_csv(STAGING / "half_life_stability.csv", index=False)
    vintage.to_csv(STAGING / "vintage_invariance.csv", index=False)
    current.to_csv(STAGING / "current_state.csv", index=False)
    gate_table.to_csv(STAGING / "candidate_gate_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "record.md").write_text(
        build_record(summary, current, epochs, stability, drift, checks),
        encoding="utf-8",
    )
    command_log = (
        "python im_regime_aware_valuation_v5.py\n"
        "python -m pytest -q test_im_regime_aware_valuation_v5.py\n"
        "uvx ruff check im_regime_aware_valuation_v5.py test_im_regime_aware_valuation_v5.py\n"
    )
    (STAGING / "command_log.txt").write_text(command_log, encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "spec_sha256": SPEC_HASH,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "data_snapshot": {
            "start": str(inputs["daily"]["date"].min().date()),
            "end": str(inputs["daily"]["date"].max().date()),
            "daily_rows": len(inputs["daily"]),
            "monthly_rows": len(inputs["monthly"]),
            "timezone": "Asia/Shanghai",
            "price_mode": "CSI1000 price index context",
            "cache_writes": "none",
        },
        "calibration": {
            "half_lives": list(HALF_LIVES),
            "ladders": LADDERS,
            "minimum_months": MIN_MONTHS,
            "schedule": "prior year-end, locked for full calendar year",
            "combination": "max(absolute_v3_tier, relative_tier)",
        },
        "decision": summary,
        "integrity": checks,
        "execution_and_cost": "not_applicable_valuation_state_only",
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "git_status": git_status(),
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_manifest(STAGING), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    STAGING.rename(OUTPUT)

    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "preregistered_candidate_bundle_causal_regime_weighting",
            "baseline": {"candidate": "absolute_v3"},
            "candidate_grid": definitions.to_dict("records"),
            "data_snapshot": manifest["data_snapshot"],
            "cost_model": {"applicable": False},
            "source_hashes": source_hashes,
            "warnings": [
                "state-selection only; no strategy outcome used",
                "relative thresholds start only after 36 monthly observations",
                "annual threshold lock is causal but not independent OOS validation",
                "research only, not live approved",
            ],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (SCAN / "record.md").write_text(
        "\n".join(
            [
                f"# {VERSION} Parameter Scan Record",
                "",
                "## Research Question",
                "",
                "- Preserve the frozen absolute economic tier and add a causal, annually locked recency-weighted relative tier.",
                f"- Primary: `{PRIMARY}`; neighbors: `{NEIGHBORS[0]}`, `{NEIGHBORS[1]}`; q70/80/90 is diagnostic only.",
                "- No IM, MO, Put, basis, or strategy return is used for selection.",
                "",
                "## Data and calibration",
                "",
                f"- Frozen local data: {inputs['daily']['date'].min().date()} to {inputs['daily']['date'].max().date()}, {len(inputs['daily'])} daily and {len(inputs['monthly'])} monthly rows.",
                "- Prior year-end data only; 36-month warmup; 5/7.5/10-year half-lives; annual thresholds locked for the full next year.",
                "- Price-index metrics are identical context fields and cannot select candidates.",
                "",
                "## Decision",
                "",
                f"- Decision: `{summary['decision']}`.",
                f"- Stability: `{summary['stability_label']}`.",
                f"- Selected: `{summary['selected_candidate']}`.",
                "- Research only; not live approved.",
                "",
                "## Commands",
                "",
                "```powershell",
                command_log.rstrip(),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command_log)


def main() -> None:
    source_hashes = verify_inputs(require_fresh_output=True)
    inputs = load_inputs()
    definitions = candidate_definitions()
    thresholds = annual_thresholds(inputs["monthly"])
    daily_states, monthly_states = build_states(
        inputs["daily"], inputs["monthly"], thresholds, definitions
    )
    scan_long, scan_wide = make_scan(
        daily_states, monthly_states, inputs["price_context"], definitions
    )
    epochs = epoch_metrics(daily_states)
    events = event_audit(monthly_states)
    drift = threshold_drift(thresholds)
    stability = stability_matrix(daily_states)
    vintage = vintage_audit(inputs["monthly"], thresholds)
    current = current_state(daily_states, thresholds, definitions)
    gate_table, summary = select_candidate(epochs, events, drift, stability, thresholds)
    checks = integrity_checks(
        inputs,
        definitions,
        thresholds,
        daily_states,
        monthly_states,
        scan_long,
        vintage,
    )
    write_outputs(
        source_hashes,
        inputs,
        definitions,
        thresholds,
        daily_states,
        monthly_states,
        scan_long,
        scan_wide,
        epochs,
        events,
        drift,
        stability,
        vintage,
        current,
        gate_table,
        summary,
        checks,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
