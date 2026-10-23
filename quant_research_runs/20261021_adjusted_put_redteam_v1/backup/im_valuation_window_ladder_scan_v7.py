from __future__ import annotations

import hashlib
import json
import subprocess
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_finite_window_valuation_v6 as v6
import im_fixed_valuation_tier_relationship_v3 as v3
import im_regime_aware_valuation_v5 as v5

ROOT = Path(__file__).resolve().parent
VERSION = "im_valuation_window_ladder_scan_v7"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "2a92ef1f1708d6930e8d56d9d0ed84f5de3c2bf5c57288d8e44ff6b4e21cde6f"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V3_OUTPUT = ROOT / "outputs" / "im_fixed_valuation_tier_relationship_v3"
V6_OUTPUT = ROOT / "outputs" / "im_finite_window_valuation_v6"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_valuation_window_ladder_scan_v7"

WINDOW_MONTHS = tuple(range(36, 85, 3))
LADDERS = {
    "q700_800_900": (0.700, 0.800, 0.900),
    "q725_825_925": (0.725, 0.825, 0.925),
    "q750_850_950": (0.750, 0.850, 0.950),
    "q775_875_975": (0.775, 0.875, 0.975),
}
LADDER_RANK = {name: rank for rank, name in enumerate(LADDERS)}
WINDOWS = v3.WINDOWS
EXPECTED_DAILY_ROWS = 2634
EXPECTED_MONTHLY_ROWS = 131
EXPECTED_CANDIDATES = 1 + len(WINDOW_MONTHS) * len(LADDERS) * 2
EXPECTED_DUAL_CELLS = len(WINDOW_MONTHS) * len(LADDERS)
ANCHOR_WINDOW = 60
ANCHOR_LADDER = "q750_850_950"

INPUT_HASHES = {
    ROOT
    / "im_finite_window_valuation_v6.py": "f952892f2b1553d84340a059e8237809536040a15a05eab493d51fa04d901e07",
    ROOT
    / "docs"
    / "im_finite_window_valuation_v6_spec.md": "4c76b1216417df32c5c450c7c05b549b1ee16e70113671403d0f12b53a8a1384",
    ROOT
    / "docs"
    / "im_finite_window_valuation_v6_postrun_audit.md": "2fcccaf65fbcb2873c36aa379f6c795ee2c38871155160bb9f731ee475eb0dbf",
    V6_OUTPUT
    / "decision_summary.json": "b850d1057125913af28c8ed16fa09c64c09e8ec4bdb61b6245fa3efb1d9ffde2",
    V6_OUTPUT
    / "integrity_checks.json": "fcebd7a441452a936ef31130065cb9380a34469dae27c94cce6a07c59b16227d",
    V6_OUTPUT
    / "output_manifest.json": "a84ca5c7677a73e4ccc641aa466982556a4809bfba095ed6227839a32cd0ef1b",
    V3_OUTPUT
    / "daily_tier_states.csv.gz": "dd91b80172553a1dbe53e79bdc5870ca32af7e7ed5171c001e356ad28c9e3912",
    V3_OUTPUT
    / "monthly_tier_states.csv": "cf0fd930dea38d06598ed20e259578d88d35394f31875ec526db0c2584e565e7",
    V3_OUTPUT
    / "price_index_context.csv": "1b04a18efe8b73f5becb164d8276b5ed07b216f647931873771815983ec6ac8c",
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
        raise RuntimeError("Frozen v7 specification mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_HASH:
        raise RuntimeError("Frozen v7 specification sidecar mismatch")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v7 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v7 scan directory is missing")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v7 input changed: {path}")
    v6_decision = json.loads(
        (V6_OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    if v6_decision["decision"] != "no_finite_window_valuation_candidate":
        raise RuntimeError("Frozen v6 rejection input mismatch")
    return {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()}


def load_inputs() -> dict[str, pd.DataFrame]:
    return v6.load_inputs()


def candidate_definitions() -> pd.DataFrame:
    rows: list[dict[str, Any]] = [
        {
            "candidate": "absolute_v3",
            "kind": "absolute",
            "window_months": np.nan,
            "ladder": "absolute_245_250_260",
            "ladder_rank": np.nan,
            "quantiles": "",
            "eligible": False,
        }
    ]
    for window in WINDOW_MONTHS:
        for ladder, quantiles in LADDERS.items():
            for kind in ("relative", "dual"):
                rows.append(
                    {
                        "candidate": f"{kind}_w{window}_{ladder}",
                        "kind": kind,
                        "window_months": window,
                        "ladder": ladder,
                        "ladder_rank": LADDER_RANK[ladder],
                        "quantiles": "/".join(f"{value:.3f}" for value in quantiles),
                        "eligible": kind == "dual",
                    }
                )
    return pd.DataFrame(rows)


def build_samples(
    daily: pd.DataFrame, monthly: pd.DataFrame
) -> dict[tuple[pd.Timestamp, int], pd.DataFrame]:
    periods = pd.period_range(
        daily["date"].min().to_period("M"),
        daily["date"].max().to_period("M"),
        freq="M",
    )
    samples: dict[tuple[pd.Timestamp, int], pd.DataFrame] = {}
    ordered = monthly.sort_values("date")
    for period in periods:
        month = period.to_timestamp()
        history = ordered[ordered["date"].lt(month)]
        for window in WINDOW_MONTHS:
            samples[(month, window)] = history.tail(window)[
                ["date", "unbounded_median_knot"]
            ].copy()
    return samples


def rolling_thresholds(
    samples: dict[tuple[pd.Timestamp, int], pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (month, window), sample in samples.items():
        available = len(sample) == window
        values = sample["unbounded_median_knot"].astype(float).to_numpy()
        for ladder, quantiles in LADDERS.items():
            row: dict[str, Any] = {
                "effective_month": month,
                "window_months": window,
                "ladder": ladder,
                "ladder_rank": LADDER_RANK[ladder],
                "sample_months": len(sample),
                "window_start": sample["date"].min() if len(sample) else pd.NaT,
                "window_end": sample["date"].max() if len(sample) else pd.NaT,
                "max_input_date": sample["date"].max() if len(sample) else pd.NaT,
                "future_rows_used": int(sample["date"].ge(month).sum()),
                "available": available,
            }
            if not available:
                row.update(
                    {
                        "threshold_1": np.nan,
                        "threshold_2": np.nan,
                        "threshold_3": np.nan,
                        "strictly_increasing": False,
                    }
                )
            else:
                thresholds = np.quantile(values, quantiles, method="linear")
                row.update(
                    {
                        "threshold_1": float(thresholds[0]),
                        "threshold_2": float(thresholds[1]),
                        "threshold_3": float(thresholds[2]),
                        "strictly_increasing": bool(np.all(np.diff(thresholds) > 0)),
                    }
                )
            rows.append(row)
    return pd.DataFrame(rows)


def percentile_states(
    frame: pd.DataFrame,
    samples: dict[tuple[pd.Timestamp, int], pd.DataFrame],
) -> pd.DataFrame:
    base = frame[["date", "unbounded_median_knot"]].copy()
    base["effective_month"] = base["date"].dt.to_period("M").dt.to_timestamp()
    outputs: list[pd.DataFrame] = []
    for window in WINDOW_MONTHS:
        part = base.copy()
        percentiles: list[float] = []
        calibrated: list[bool] = []
        for row in part.itertuples(index=False):
            sample = samples[(pd.Timestamp(row.effective_month), window)]
            is_calibrated = len(sample) == window
            calibrated.append(is_calibrated)
            if is_calibrated:
                values = sample["unbounded_median_knot"].astype(float).to_numpy()
                percentiles.append(
                    float(np.mean(values <= float(row.unbounded_median_knot)))
                )
            else:
                percentiles.append(np.nan)
        part["window_months"] = window
        part["rolling_percentile"] = percentiles
        part["calibrated"] = calibrated
        outputs.append(part)
    return pd.concat(outputs, ignore_index=True)


def _absolute_state(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["effective_month"] = result["date"].dt.to_period("M").dt.to_timestamp()
    result["candidate"] = "absolute_v3"
    result["absolute_tier"] = result["median_tier"].astype(int)
    result["relative_tier"] = 0
    result["final_tier"] = result["absolute_tier"]
    result["calibrated"] = False
    result["rolling_percentile"] = np.nan
    result["sample_months"] = np.nan
    result["window_start"] = pd.Series(
        pd.NaT, index=result.index, dtype="datetime64[ns]"
    )
    result["window_end"] = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns]")
    for column in ("threshold_1", "threshold_2", "threshold_3"):
        result[column] = np.nan
    return result


def build_states(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    thresholds: pd.DataFrame,
    daily_percentiles: pd.DataFrame,
    monthly_percentiles: pd.DataFrame,
    definitions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    outputs: dict[str, list[pd.DataFrame]] = {"daily": [], "monthly": []}
    for frequency, frame, pct_states in (
        ("daily", daily, daily_percentiles),
        ("monthly", monthly, monthly_percentiles),
    ):
        outputs[frequency].append(_absolute_state(frame))
        for window in WINDOW_MONTHS:
            pct = pct_states[pct_states["window_months"].eq(window)][
                ["date", "rolling_percentile"]
            ]
            for ladder in LADDERS:
                selected = thresholds[
                    thresholds["window_months"].eq(window)
                    & thresholds["ladder"].eq(ladder)
                ][
                    [
                        "effective_month",
                        "sample_months",
                        "window_start",
                        "window_end",
                        "available",
                        "strictly_increasing",
                        "threshold_1",
                        "threshold_2",
                        "threshold_3",
                    ]
                ]
                base = frame.copy()
                base["effective_month"] = (
                    base["date"].dt.to_period("M").dt.to_timestamp()
                )
                base = base.merge(pct, on="date", validate="one_to_one")
                base = base.merge(
                    selected, on="effective_month", validate="many_to_one"
                )
                calibrated = base["available"] & base["strictly_increasing"]
                score = base["unbounded_median_knot"].astype(float)
                base["relative_tier"] = np.select(
                    [
                        calibrated & score.ge(base["threshold_3"]),
                        calibrated & score.ge(base["threshold_2"]),
                        calibrated & score.ge(base["threshold_1"]),
                    ],
                    [3, 2, 1],
                    default=0,
                ).astype(int)
                base["absolute_tier"] = base["median_tier"].astype(int)
                base["calibrated"] = calibrated.astype(bool)
                for kind in ("relative", "dual"):
                    item = base.copy()
                    item["candidate"] = f"{kind}_w{window}_{ladder}"
                    item["final_tier"] = (
                        item["relative_tier"]
                        if kind == "relative"
                        else item[["absolute_tier", "relative_tier"]].max(axis=1)
                    ).astype(int)
                    outputs[frequency].append(item)
    keep = [
        "date",
        "effective_month",
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
        "rolling_percentile",
        "sample_months",
        "window_start",
        "window_end",
        "threshold_1",
        "threshold_2",
        "threshold_3",
    ]
    daily_states = pd.concat(outputs["daily"], ignore_index=True)[keep]
    monthly_states = pd.concat(outputs["monthly"], ignore_index=True)[keep]
    expected = set(definitions["candidate"])
    if (
        set(daily_states["candidate"]) != expected
        or set(monthly_states["candidate"]) != expected
    ):
        raise RuntimeError("Expanded window-ladder state construction mismatch")
    return daily_states, monthly_states


def window_regime_diagnostics(percentiles: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "2022_2024": (pd.Timestamp("2022-01-01"), pd.Timestamp("2024-12-31")),
        "2025": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        "2026": (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-08-17")),
    }
    rows: list[dict[str, Any]] = []
    latest = percentiles["date"].max()
    for window, group in percentiles.groupby("window_months"):
        row: dict[str, Any] = {"window_months": int(window)}
        for period, (start, end) in periods.items():
            part = group[group["date"].between(start, end)]
            calibrated = part[part["calibrated"]]
            row[f"calibrated_ratio_{period}"] = float(part["calibrated"].mean())
            row[f"median_percentile_{period}"] = float(
                calibrated["rolling_percentile"].median()
            )
            row[f"p90_percentile_{period}"] = float(
                calibrated["rolling_percentile"].quantile(0.90)
            )
            row[f"mean_percentile_{period}"] = float(
                calibrated["rolling_percentile"].mean()
            )
        current = group[group["date"].eq(latest)].iloc[0]
        row["current_percentile"] = float(current["rolling_percentile"])
        row["coverage_gate"] = bool(
            row["calibrated_ratio_2022_2024"] >= 0.75
            and row["calibrated_ratio_2025"] == 1.0
            and row["calibrated_ratio_2026"] == 1.0
        )
        row["bear_low_gate"] = bool(
            row["median_percentile_2022_2024"] <= 0.55
            and row["p90_percentile_2022_2024"] <= 0.80
        )
        row["repair_gate"] = bool(
            row["median_percentile_2025"] >= row["median_percentile_2022_2024"] + 0.10
            and row["median_percentile_2025"] <= 0.75
        )
        row["high_2026_gate"] = bool(
            row["median_percentile_2026"] >= 0.70
            and row["median_percentile_2026"] >= row["median_percentile_2025"] + 0.10
        )
        row["current_gate"] = bool(0.70 <= row["current_percentile"] <= 0.95)
        row["semantic_pass"] = all(
            row[key]
            for key in (
                "coverage_gate",
                "bear_low_gate",
                "repair_gate",
                "high_2026_gate",
                "current_gate",
            )
        )
        rows.append(row)
    return pd.DataFrame(rows)


def candidate_regime_metrics(daily_states: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "2022_2024": (pd.Timestamp("2022-01-01"), pd.Timestamp("2024-12-31")),
        "2025": (pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")),
        "2026": (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-08-17")),
        "2022_now": (pd.Timestamp("2022-01-01"), pd.Timestamp("2026-08-17")),
    }
    rows: list[dict[str, Any]] = []
    dual = daily_states[daily_states["candidate"].str.startswith("dual_")]
    for candidate, group in dual.groupby("candidate"):
        for period, (start, end) in periods.items():
            part = group[group["date"].between(start, end)].copy()
            rows.append(
                {
                    "candidate": candidate,
                    "period": period,
                    "start": part["date"].min(),
                    "end": part["date"].max(),
                    "rows": len(part),
                    **v6.state_metrics(part),
                }
            )
    return pd.DataFrame(rows)


def monthly_event_counts(monthly_states: pd.DataFrame) -> pd.DataFrame:
    part = monthly_states[
        monthly_states["candidate"].str.startswith("dual_")
        & monthly_states["date"].between("2026-01-01", "2026-08-17")
    ]
    rows: list[dict[str, Any]] = []
    for candidate, group in part.groupby("candidate"):
        active = group.sort_values("date")["final_tier"].ge(1).reset_index(drop=True)
        episodes, longest = v3.ic_v6.episode_stats(active)
        rows.append(
            {
                "candidate": candidate,
                "nonzero_monthly_episodes_2026": episodes,
                "longest_nonzero_months_2026": longest,
            }
        )
    return pd.DataFrame(rows)


def threshold_drift(thresholds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    available = thresholds[thresholds["available"] & thresholds["strictly_increasing"]]
    for (window, ladder), group in available.groupby(["window_months", "ladder"]):
        group = group.sort_values("effective_month")
        row: dict[str, Any] = {"window_months": int(window), "ladder": ladder}
        for number in (1, 2, 3):
            changes = group[f"threshold_{number}"].diff().abs().dropna()
            row[f"threshold_{number}_median_abs_change"] = float(changes.median())
            row[f"threshold_{number}_p95_abs_change"] = float(changes.quantile(0.95))
            row[f"threshold_{number}_max_abs_change"] = float(changes.max())
        rows.append(row)
    return pd.DataFrame(rows)


def current_states(
    daily_states: pd.DataFrame, definitions: pd.DataFrame
) -> pd.DataFrame:
    latest = daily_states["date"].max()
    current = daily_states[daily_states["date"].eq(latest)].copy()
    current = current.merge(
        definitions[
            ["candidate", "kind", "window_months", "ladder", "ladder_rank", "eligible"]
        ],
        on="candidate",
        validate="one_to_one",
    )
    return current[
        [
            "date",
            "candidate",
            "kind",
            "window_months",
            "ladder",
            "ladder_rank",
            "eligible",
            "unbounded_median_knot",
            "rolling_percentile",
            "absolute_tier",
            "relative_tier",
            "final_tier",
            "window_start",
            "window_end",
            "threshold_1",
            "threshold_2",
            "threshold_3",
        ]
    ].sort_values(["kind", "window_months", "ladder_rank"])


def build_candidate_gates(
    definitions: pd.DataFrame,
    window_diagnostics: pd.DataFrame,
    regime_metrics: pd.DataFrame,
    events: pd.DataFrame,
    drift: pd.DataFrame,
    current: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metrics = regime_metrics.set_index(["candidate", "period"])
    event_index = events.set_index("candidate")
    drift_index = drift.set_index(["window_months", "ladder"])
    window_index = window_diagnostics.set_index("window_months")
    current_index = current.set_index("candidate")
    dual_definitions = definitions[definitions["kind"].eq("dual")]
    for definition in dual_definitions.itertuples(index=False):
        candidate = definition.candidate
        window = int(definition.window_months)
        ladder = definition.ladder
        low = metrics.loc[(candidate, "2022_2024")]
        repair = metrics.loc[(candidate, "2025")]
        high = metrics.loc[(candidate, "2026")]
        recent = metrics.loc[(candidate, "2022_now")]
        current_row = current_index.loc[candidate]
        drift_row = drift_index.loc[(window, ladder)]
        semantic_pass = bool(window_index.loc[window, "semantic_pass"])
        low_gate = bool(
            low["nonzero_ratio"] <= 0.10
            and low["tier2plus_ratio"] <= 0.05
            and low["tier3plus_ratio"] <= 0.02
        )
        repair_gate = bool(
            repair["avg_tier"] <= 1.00
            and repair["nonzero_ratio"] <= 0.50
            and repair["tier3plus_ratio"] <= 0.10
        )
        high_gate = bool(
            high["nonzero_ratio"] >= 0.50
            and high["tier3plus_ratio"] <= 0.30
            and int(event_index.loc[candidate, "nonzero_monthly_episodes_2026"]) >= 1
        )
        current_gate = int(current_row["final_tier"]) in (1, 2)
        churn_gate = float(recent["annualized_transitions"]) <= 24.0
        drift_gate = all(
            float(drift_row[f"threshold_{number}_median_abs_change"]) <= 0.05
            and float(drift_row[f"threshold_{number}_p95_abs_change"]) <= 0.25
            and float(drift_row[f"threshold_{number}_max_abs_change"]) <= 0.75
            for number in (1, 2, 3)
        )
        rows.append(
            {
                "candidate": candidate,
                "window_months": window,
                "ladder": ladder,
                "ladder_rank": int(definition.ladder_rank),
                "semantic_pass": semantic_pass,
                "low_2022_2024_gate": low_gate,
                "repair_2025_gate": repair_gate,
                "high_2026_gate": high_gate,
                "current_tier_gate": current_gate,
                "churn_gate": churn_gate,
                "drift_gate": drift_gate,
                "nonzero_2022_2024": float(low["nonzero_ratio"]),
                "tier2plus_2022_2024": float(low["tier2plus_ratio"]),
                "tier3_2022_2024": float(low["tier3plus_ratio"]),
                "avg_tier_2025": float(repair["avg_tier"]),
                "nonzero_2025": float(repair["nonzero_ratio"]),
                "tier3_2025": float(repair["tier3plus_ratio"]),
                "nonzero_2026": float(high["nonzero_ratio"]),
                "tier3_2026": float(high["tier3plus_ratio"]),
                "episodes_2026": int(
                    event_index.loc[candidate, "nonzero_monthly_episodes_2026"]
                ),
                "current_tier": int(current_row["final_tier"]),
                "current_percentile": float(current_row["rolling_percentile"]),
                "annualized_transitions_2022_now": float(
                    recent["annualized_transitions"]
                ),
                "candidate_pass": bool(
                    semantic_pass
                    and low_gate
                    and repair_gate
                    and high_gate
                    and current_gate
                    and churn_gate
                    and drift_gate
                ),
            }
        )
    return pd.DataFrame(rows)


def grid_neighbors(cell: tuple[int, int]) -> list[tuple[int, int]]:
    window, rank = cell
    candidates = [
        (window - 3, rank),
        (window + 3, rank),
        (window, rank - 1),
        (window, rank + 1),
    ]
    return [
        item
        for item in candidates
        if item[0] in WINDOW_MONTHS and 0 <= item[1] < len(LADDERS)
    ]


def identify_platforms(
    gates: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    table = gates.copy()
    passing = table[table["candidate_pass"]]
    coords = set(
        zip(passing["window_months"].astype(int), passing["ladder_rank"].astype(int))
    )
    components: list[set[tuple[int, int]]] = []
    remaining = set(coords)
    while remaining:
        start = remaining.pop()
        component = {start}
        queue: deque[tuple[int, int]] = deque([start])
        while queue:
            cell = queue.popleft()
            for neighbor in grid_neighbors(cell):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)

    component_rows: list[dict[str, Any]] = []
    coord_to_component: dict[tuple[int, int], int] = {}
    for component_id, component in enumerate(components, start=1):
        windows = sorted({cell[0] for cell in component})
        ranks = sorted({cell[1] for cell in component})
        for cell in component:
            coord_to_component[cell] = component_id
        qualifies = bool(
            len(component) >= 6
            and len(windows) >= 3
            and len(ranks) >= 2
            and not (set(windows).issubset({min(WINDOW_MONTHS), max(WINDOW_MONTHS)}))
        )
        component_rows.append(
            {
                "platform_id": component_id,
                "cell_count": len(component),
                "window_count": len(windows),
                "ladder_count": len(ranks),
                "min_window": min(windows),
                "max_window": max(windows),
                "min_ladder_rank": min(ranks),
                "max_ladder_rank": max(ranks),
                "contains_v6_anchor": (ANCHOR_WINDOW, LADDER_RANK[ANCHOR_LADDER])
                in component,
                "qualifies": qualifies,
                "cells": "|".join(
                    f"{window}:{rank}" for window, rank in sorted(component)
                ),
            }
        )
    components_table = pd.DataFrame(
        component_rows,
        columns=[
            "platform_id",
            "cell_count",
            "window_count",
            "ladder_count",
            "min_window",
            "max_window",
            "min_ladder_rank",
            "max_ladder_rank",
            "contains_v6_anchor",
            "qualifies",
            "cells",
        ],
    )
    table["platform_id"] = [
        coord_to_component.get((int(row.window_months), int(row.ladder_rank)), np.nan)
        for row in table.itertuples(index=False)
    ]
    table["passing_neighbor_count"] = [
        sum(
            neighbor in coords
            for neighbor in grid_neighbors(
                (int(row.window_months), int(row.ladder_rank))
            )
        )
        if row.candidate_pass
        else 0
        for row in table.itertuples(index=False)
    ]

    qualifying = components_table[components_table["qualifies"]]
    selected_platform_id: int | None = None
    center_candidate: str | None = None
    local_support = False
    if not qualifying.empty:
        ranked = qualifying.sort_values(
            [
                "cell_count",
                "window_count",
                "ladder_count",
                "contains_v6_anchor",
                "max_window",
                "max_ladder_rank",
            ],
            ascending=[False, False, False, False, False, False],
        )
        selected_platform_id = int(ranked.iloc[0]["platform_id"])
        cells = table[
            table["platform_id"].eq(selected_platform_id) & table["candidate_pass"]
        ].copy()
        median_window = float(cells["window_months"].median())
        median_rank = float(cells["ladder_rank"].median())
        cells["center_distance"] = (
            cells["window_months"] - median_window
        ).abs() / 3 + (cells["ladder_rank"] - median_rank).abs()
        cells["anchor_distance"] = (
            cells["window_months"] - ANCHOR_WINDOW
        ).abs() / 3 + (cells["ladder_rank"] - LADDER_RANK[ANCHOR_LADDER]).abs()
        center = cells.sort_values(
            [
                "passing_neighbor_count",
                "center_distance",
                "anchor_distance",
                "window_months",
                "ladder_rank",
            ],
            ascending=[False, True, True, False, False],
        ).iloc[0]
        center_candidate = str(center["candidate"])
        center_coord = (int(center["window_months"]), int(center["ladder_rank"]))
        component_coords = {
            (int(row.window_months), int(row.ladder_rank))
            for row in cells.itertuples(index=False)
        }
        left_right = {
            (center_coord[0] - 3, center_coord[1]),
            (center_coord[0] + 3, center_coord[1]),
        }
        ladder_neighbor = any(
            (center_coord[0], center_coord[1] + offset) in component_coords
            for offset in (-1, 1)
        )
        local_support = bool(left_right.issubset(component_coords) and ladder_neighbor)
        table["selected_center"] = table["candidate"].eq(center_candidate)
    else:
        table["selected_center"] = False

    passing_count = int(table["candidate_pass"].sum())
    if selected_platform_id is not None and local_support:
        decision = "freeze_window_ladder_platform_center_for_next_put_layer"
        stability_label = "wide_stable"
        selected_candidate = center_candidate
    elif passing_count >= 3:
        decision = "watchlist_window_ladder_ridge"
        stability_label = "peak_only"
        selected_candidate = None
    else:
        decision = "no_window_ladder_candidate"
        stability_label = "reject"
        selected_candidate = None
    selection = {
        "decision": decision,
        "stability_label": stability_label,
        "selected_candidate": selected_candidate,
        "platform_center": center_candidate,
        "selected_platform_id": selected_platform_id,
        "local_support_pass": local_support,
        "passing_candidate_count": passing_count,
        "component_count": len(components_table),
        "qualifying_platform_count": int(components_table["qualifies"].sum())
        if len(components_table)
        else 0,
    }
    return table, components_table, selection


def selected_stability(
    daily_states: pd.DataFrame,
    gate_table: pd.DataFrame,
    center_candidate: str | None,
) -> pd.DataFrame:
    columns = [
        "center",
        "neighbor",
        "neighbor_type",
        "rows",
        "exact_tier_agreement",
        "weighted_kappa",
        "nonzero_jaccard",
        "mean_abs_tier_difference",
        "center_current_tier",
        "neighbor_current_tier",
    ]
    if center_candidate is None:
        return pd.DataFrame(columns=columns)
    center_row = gate_table[gate_table["candidate"].eq(center_candidate)].iloc[0]
    window = int(center_row["window_months"])
    rank = int(center_row["ladder_rank"])
    component_id = center_row["platform_id"]
    neighbor_rows = gate_table[
        gate_table["candidate_pass"]
        & gate_table["platform_id"].eq(component_id)
        & (
            (
                gate_table["ladder_rank"].eq(rank)
                & gate_table["window_months"].isin([window - 3, window + 3])
            )
            | (
                gate_table["window_months"].eq(window)
                & gate_table["ladder_rank"].isin([rank - 1, rank + 1])
            )
        )
    ]
    candidates = [center_candidate, *neighbor_rows["candidate"].tolist()]
    part = daily_states[
        daily_states["candidate"].isin(candidates)
        & daily_states["date"].ge("2022-01-01")
    ][["date", "candidate", "final_tier"]]
    pivot = part.pivot(index="date", columns="candidate", values="final_tier")
    center = pivot[center_candidate].astype(int)
    rows: list[dict[str, Any]] = []
    for neighbor in neighbor_rows.itertuples(index=False):
        other = pivot[neighbor.candidate].astype(int)
        neighbor_type = "window" if int(neighbor.window_months) != window else "ladder"
        rows.append(
            {
                "center": center_candidate,
                "neighbor": neighbor.candidate,
                "neighbor_type": neighbor_type,
                "rows": len(pivot),
                "exact_tier_agreement": float(center.eq(other).mean()),
                "weighted_kappa": v3.weighted_kappa(center, other),
                "nonzero_jaccard": v5.nonzero_jaccard(center, other),
                "mean_abs_tier_difference": float((center - other).abs().mean()),
                "center_current_tier": int(center.iloc[-1]),
                "neighbor_current_tier": int(other.iloc[-1]),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def rolling_window_audit(
    samples: dict[tuple[pd.Timestamp, int], pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for window in WINDOW_MONTHS:
        available = sorted(
            (month, sample)
            for (month, item_window), sample in samples.items()
            if item_window == window and len(sample) == window
        )
        prior: set[pd.Timestamp] | None = None
        for month, sample in available:
            dates = set(sample["date"])
            overlap = len(dates & prior) if prior is not None else np.nan
            rows.append(
                {
                    "effective_month": month,
                    "window_months": window,
                    "sample_months": len(dates),
                    "overlap_with_prior": overlap,
                    "expected_overlap": window - 1 if prior is not None else np.nan,
                    "roll_is_expected": True
                    if prior is None
                    else overlap == window - 1,
                }
            )
            prior = dates
    return pd.DataFrame(rows)


def vintage_audit(
    samples: dict[tuple[pd.Timestamp, int], pd.DataFrame],
    thresholds: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in thresholds[thresholds["available"]].itertuples(index=False):
        sample = samples[(pd.Timestamp(row.effective_month), int(row.window_months))]
        recalculated = np.quantile(
            sample["unbounded_median_knot"].astype(float).to_numpy(),
            LADDERS[str(row.ladder)],
            method="linear",
        )
        maximum = max(
            abs(
                float(recalculated[number - 1])
                - float(getattr(row, f"threshold_{number}"))
            )
            for number in (1, 2, 3)
        )
        rows.append(
            {
                "effective_month": row.effective_month,
                "window_months": int(row.window_months),
                "ladder": row.ladder,
                "threshold_max_abs_error": maximum,
                "future_rows_used": int(
                    sample["date"].ge(pd.Timestamp(row.effective_month)).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def make_scan(
    daily_states: pd.DataFrame,
    monthly_states: pd.DataFrame,
    price_context: pd.DataFrame,
    definitions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return v6.make_scan(daily_states, monthly_states, price_context, definitions)


def integrity_checks(
    inputs: dict[str, pd.DataFrame],
    definitions: pd.DataFrame,
    thresholds: pd.DataFrame,
    daily_percentiles: pd.DataFrame,
    daily_states: pd.DataFrame,
    monthly_states: pd.DataFrame,
    scan_long: pd.DataFrame,
    gates: pd.DataFrame,
    vintage: pd.DataFrame,
    rolling_audit: pd.DataFrame,
) -> dict[str, Any]:
    absolute = daily_states[daily_states["candidate"].eq("absolute_v3")]
    parity = absolute[["date", "final_tier"]].merge(
        inputs["daily"][["date", "median_tier"]], on="date", validate="one_to_one"
    )
    dual = daily_states[daily_states["candidate"].str.startswith("dual_")]
    available = thresholds[thresholds["available"]]
    strict = available[available["strictly_increasing"]]
    price_uniques = (
        scan_long.groupby("segment")[["ann_return", "ann_vol", "sharpe_repo", "max_dd"]]
        .nunique()
        .max()
        .max()
    )
    pct_available = daily_percentiles[daily_percentiles["calibrated"]][
        "rolling_percentile"
    ]
    checks = {
        "candidate_count": len(definitions),
        "dual_grid_cells": len(gates),
        "daily_source_rows": len(inputs["daily"]),
        "monthly_source_rows": len(inputs["monthly"]),
        "daily_percentile_rows": len(daily_percentiles),
        "daily_state_rows": len(daily_states),
        "monthly_state_rows": len(monthly_states),
        "scan_rows": len(scan_long),
        "threshold_rows": len(thresholds),
        "available_threshold_rows": len(available),
        "available_sample_size_failures": int(
            (available["sample_months"] != available["window_months"]).sum()
        ),
        "threshold_order_failures": int((~available["strictly_increasing"]).sum()),
        "threshold_future_rows_used": int(thresholds["future_rows_used"].sum()),
        "threshold_effective_date_failures": int(
            (
                pd.to_datetime(strict["max_input_date"])
                >= pd.to_datetime(strict["effective_month"])
            ).sum()
        ),
        "vintage_threshold_max_abs_error": float(
            vintage["threshold_max_abs_error"].max()
        ),
        "vintage_future_rows_used": int(vintage["future_rows_used"].sum()),
        "unexpected_monthly_roll_rows": int((~rolling_audit["roll_is_expected"]).sum()),
        "percentile_out_of_range_rows": int((~pct_available.between(0.0, 1.0)).sum()),
        "absolute_v3_parity_failures": int(
            (parity["final_tier"] != parity["median_tier"]).sum()
        ),
        "dual_max_identity_failures": int(
            (
                dual["final_tier"]
                != dual[["absolute_tier", "relative_tier"]].max(axis=1)
            ).sum()
        ),
        "tier_out_of_range_rows": int(
            (~daily_states["final_tier"].isin([0, 1, 2, 3])).sum()
        ),
        "price_context_max_unique_per_window": int(price_uniques),
        "candidate_window_duplicates": int(
            scan_long.duplicated(["candidate", "segment"]).sum()
        ),
    }
    expected_threshold_rows = EXPECTED_MONTHLY_ROWS * len(WINDOW_MONTHS) * len(LADDERS)
    checks["integrity_pass"] = bool(
        checks["candidate_count"] == EXPECTED_CANDIDATES
        and checks["dual_grid_cells"] == EXPECTED_DUAL_CELLS
        and checks["daily_source_rows"] == EXPECTED_DAILY_ROWS
        and checks["monthly_source_rows"] == EXPECTED_MONTHLY_ROWS
        and checks["daily_percentile_rows"] == EXPECTED_DAILY_ROWS * len(WINDOW_MONTHS)
        and checks["daily_state_rows"] == EXPECTED_DAILY_ROWS * EXPECTED_CANDIDATES
        and checks["monthly_state_rows"] == EXPECTED_MONTHLY_ROWS * EXPECTED_CANDIDATES
        and checks["scan_rows"] == EXPECTED_CANDIDATES * len(WINDOWS)
        and checks["threshold_rows"] == expected_threshold_rows
        and checks["available_sample_size_failures"] == 0
        and checks["threshold_order_failures"] == 0
        and checks["threshold_future_rows_used"] == 0
        and checks["threshold_effective_date_failures"] == 0
        and checks["vintage_threshold_max_abs_error"] <= 1e-14
        and checks["vintage_future_rows_used"] == 0
        and checks["unexpected_monthly_roll_rows"] == 0
        and checks["percentile_out_of_range_rows"] == 0
        and checks["absolute_v3_parity_failures"] == 0
        and checks["dual_max_identity_failures"] == 0
        and checks["tier_out_of_range_rows"] == 0
        and checks["price_context_max_unique_per_window"] == 1
        and checks["candidate_window_duplicates"] == 0
    )
    if not checks["integrity_pass"]:
        raise RuntimeError(f"IM expanded valuation scan integrity failed: {checks}")
    return checks


def build_record(
    summary: dict[str, Any],
    window_diagnostics: pd.DataFrame,
    gates: pd.DataFrame,
    components: pd.DataFrame,
    stability: pd.DataFrame,
    current: pd.DataFrame,
    price_context: pd.DataFrame,
    checks: dict[str, Any],
) -> str:
    passing = gates[gates["candidate_pass"]][
        [
            "candidate",
            "window_months",
            "ladder",
            "current_tier",
            "nonzero_2022_2024",
            "nonzero_2025",
            "nonzero_2026",
            "tier3_2026",
            "platform_id",
            "passing_neighbor_count",
            "selected_center",
        ]
    ]
    selected_current = current[current["candidate"].eq(summary.get("platform_center"))]
    return "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            "> 年代语义约束下的窗口×分位阶梯二维扫描；不含Put或策略收益。",
            "",
            "## 决定",
            "",
            f"- 决定：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
            f"- 平台中心：`{summary['platform_center']}`；正式选中：`{summary['selected_candidate']}`。",
            f"- 通过单元格{summary['passing_candidate_count']}个，合格平台{summary['qualifying_platform_count']}个，中心局部支持={summary['local_support_pass']}。",
            "",
            "## 窗口年代语义",
            "",
            window_diagnostics.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 通过单元格",
            "",
            passing.to_markdown(index=False, floatfmt=".4f")
            if len(passing)
            else "无。",
            "",
            "## 平台",
            "",
            components.to_markdown(index=False) if len(components) else "无。",
            "",
            "## 平台中心当前状态",
            "",
            selected_current.to_markdown(index=False, floatfmt=".6f")
            if len(selected_current)
            else "无。",
            "",
            "## 中心确认线",
            "",
            stability.to_markdown(index=False, floatfmt=".6f")
            if len(stability)
            else "无。",
            "",
            "## 中证1000价格指数背景（不参与选择）",
            "",
            price_context.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 完整性与边界",
            "",
            f"- 未来行使用{checks['threshold_future_rows_used']}；历史重算最大误差{checks['vintage_threshold_max_abs_error']:.3e}；异常窗口滚动{checks['unexpected_monthly_roll_rows']}。",
            "- 2022—2026年代语义是用户确认后的语义校准，不是独立样本外验证。",
            "- 本版不含交易、Put、费用、滑点、保证金、现金收益或流动性；未批准实盘。",
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
    daily_percentiles: pd.DataFrame,
    monthly_percentiles: pd.DataFrame,
    daily_states: pd.DataFrame,
    monthly_states: pd.DataFrame,
    scan_long: pd.DataFrame,
    scan_wide: pd.DataFrame,
    window_diagnostics: pd.DataFrame,
    regime_metrics: pd.DataFrame,
    events: pd.DataFrame,
    drift: pd.DataFrame,
    gates: pd.DataFrame,
    components: pd.DataFrame,
    stability: pd.DataFrame,
    current: pd.DataFrame,
    rolling_audit: pd.DataFrame,
    vintage: pd.DataFrame,
    summary: dict[str, Any],
    checks: dict[str, Any],
) -> None:
    STAGING.mkdir(parents=True, exist_ok=False)
    definitions.to_csv(STAGING / "candidate_definitions.csv", index=False)
    thresholds.to_csv(
        STAGING / "monthly_thresholds.csv.gz", index=False, compression="gzip"
    )
    daily_percentiles.to_csv(
        STAGING / "daily_window_percentiles.csv.gz", index=False, compression="gzip"
    )
    monthly_percentiles.to_csv(STAGING / "monthly_window_percentiles.csv", index=False)
    daily_states.to_csv(
        STAGING / "daily_candidate_states.csv.gz", index=False, compression="gzip"
    )
    monthly_states.to_csv(
        STAGING / "monthly_candidate_states.csv.gz", index=False, compression="gzip"
    )
    scan_long.to_csv(STAGING / "scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "window_metrics.csv", index=False)
    window_diagnostics.to_csv(STAGING / "window_regime_diagnostics.csv", index=False)
    regime_metrics.to_csv(STAGING / "candidate_regime_metrics.csv", index=False)
    events.to_csv(STAGING / "monthly_event_counts.csv", index=False)
    drift.to_csv(STAGING / "threshold_drift.csv", index=False)
    gates.to_csv(STAGING / "candidate_gate_grid.csv", index=False)
    components.to_csv(STAGING / "platform_components.csv", index=False)
    stability.to_csv(STAGING / "selected_center_stability.csv", index=False)
    current.to_csv(STAGING / "current_state.csv", index=False)
    rolling_audit.to_csv(STAGING / "rolling_window_audit.csv", index=False)
    vintage.to_csv(
        STAGING / "vintage_invariance.csv.gz", index=False, compression="gzip"
    )
    (STAGING / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "record.md").write_text(
        build_record(
            summary,
            window_diagnostics,
            gates,
            components,
            stability,
            current,
            inputs["price_context"],
            checks,
        ),
        encoding="utf-8",
    )
    command_log = (
        "python im_valuation_window_ladder_scan_v7.py\n"
        "python -m pytest -q test_im_valuation_window_ladder_scan_v7.py\n"
        "uvx ruff check im_valuation_window_ladder_scan_v7.py test_im_valuation_window_ladder_scan_v7.py\n"
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
            "price_mode": "CSI1000 official price-index context",
            "adjustment_mode": "official price-index level; frozen v3 valuation inputs",
            "cache_writes": "none",
        },
        "grid": {
            "window_months": list(WINDOW_MONTHS),
            "ladders": LADDERS,
            "quantile_method": "numpy_linear",
            "percentile_method": "weak_ecdf_count_le_over_n",
            "combination": "max(absolute_v3_tier, rolling_relative_tier)",
        },
        "selection": summary,
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
            "scan_type": "two_parameter_grid_with_connected_platform_selection",
            "baseline": {"candidate": "absolute_v3"},
            "candidate_grid": definitions.to_dict("records"),
            "data_snapshot": manifest["data_snapshot"],
            "cost_model": {"applicable": False},
            "source_hashes": source_hashes,
            "warnings": [
                "state-selection only; no strategy outcome used",
                "2022-2026 regime semantics include user-confirmed hindsight and are not independent OOS validation",
                "candidate warmup starts differ by rolling window length",
                "research only; not live approved",
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
                "- Find a connected 2D platform across 36-84 month windows and four quantile ladders.",
                "- Freeze user-confirmed regime semantics: 2022-2024 low, 2025 repair, 2026 moderately high.",
                "- Do not select on IM, MO, Put, basis, or subsequent return.",
                "",
                "## Data and calibration",
                "",
                f"- Frozen local data: {inputs['daily']['date'].min().date()} to {inputs['daily']['date'].max().date()}, {len(inputs['daily'])} daily and {len(inputs['monthly'])} monthly rows.",
                "- Prior month-end data only; complete rolling windows; monthly thresholds locked for the full next month.",
                "- Price-index return and drawdown are identical context fields and cannot select candidates; costs and execution are not applicable.",
                "",
                "## Decision",
                "",
                f"- Decision: `{summary['decision']}`.",
                f"- Stability: `{summary['stability_label']}`.",
                f"- Selected: `{summary['selected_candidate']}`.",
                f"- Platform center: `{summary['platform_center']}`.",
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
    samples = build_samples(inputs["daily"], inputs["monthly"])
    thresholds = rolling_thresholds(samples)
    daily_percentiles = percentile_states(inputs["daily"], samples)
    monthly_percentiles = percentile_states(inputs["monthly"], samples)
    daily_states, monthly_states = build_states(
        inputs["daily"],
        inputs["monthly"],
        thresholds,
        daily_percentiles,
        monthly_percentiles,
        definitions,
    )
    scan_long, scan_wide = make_scan(
        daily_states, monthly_states, inputs["price_context"], definitions
    )
    window_diagnostics = window_regime_diagnostics(daily_percentiles)
    regime_metrics = candidate_regime_metrics(daily_states)
    events = monthly_event_counts(monthly_states)
    drift = threshold_drift(thresholds)
    current = current_states(daily_states, definitions)
    gates = build_candidate_gates(
        definitions,
        window_diagnostics,
        regime_metrics,
        events,
        drift,
        current,
    )
    gates, components, summary = identify_platforms(gates)
    stability = selected_stability(daily_states, gates, summary["platform_center"])
    rolling_audit = rolling_window_audit(samples)
    vintage = vintage_audit(samples, thresholds)
    checks = integrity_checks(
        inputs,
        definitions,
        thresholds,
        daily_percentiles,
        daily_states,
        monthly_states,
        scan_long,
        gates,
        vintage,
        rolling_audit,
    )
    if summary["platform_center"] is not None:
        selected_row = current[
            current["candidate"].eq(summary["platform_center"])
        ].iloc[0]
        summary.update(
            {
                "selected_window_months": int(selected_row["window_months"]),
                "selected_ladder": str(selected_row["ladder"]),
                "selected_current_percentile": float(
                    selected_row["rolling_percentile"]
                ),
                "selected_current_tier": int(selected_row["final_tier"]),
                "selected_thresholds": [
                    float(selected_row["threshold_1"]),
                    float(selected_row["threshold_2"]),
                    float(selected_row["threshold_3"]),
                ],
            }
        )
    summary.update(
        {
            "selection_uses_strategy_outcomes": False,
            "semantic_calibration_is_independent_oos": False,
            "live_approved": False,
            "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        }
    )
    write_outputs(
        source_hashes,
        inputs,
        definitions,
        thresholds,
        daily_percentiles,
        monthly_percentiles,
        daily_states,
        monthly_states,
        scan_long,
        scan_wide,
        window_diagnostics,
        regime_metrics,
        events,
        drift,
        gates,
        components,
        stability,
        current,
        rolling_audit,
        vintage,
        summary,
        checks,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
