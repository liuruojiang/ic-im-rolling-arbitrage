from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_tier_relationship_v3 as v3
import im_regime_aware_valuation_v5 as v5

ROOT = Path(__file__).resolve().parent
VERSION = "im_finite_window_valuation_v6"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "4c76b1216417df32c5c450c7c05b549b1ee16e70113671403d0f12b53a8a1384"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}_staging"
V3_OUTPUT = ROOT / "outputs" / "im_fixed_valuation_tier_relationship_v3"
V5_OUTPUT = ROOT / "outputs" / "im_regime_aware_valuation_v5"
SCAN = ROOT / "quant_param_scan_runs" / "20260819_im_finite_window_valuation_v6"

WINDOW_MONTHS = (48, 60, 72)
LADDERS = {
    "q708090": (0.70, 0.80, 0.90),
    "q758595": (0.75, 0.85, 0.95),
}
PRIMARY = "dual_w60_q758595"
NEIGHBORS = ("dual_w48_q758595", "dual_w72_q758595")
WINDOWS = v3.WINDOWS
EXPECTED_DAILY_ROWS = 2634
EXPECTED_MONTHLY_ROWS = 131

INPUT_HASHES = {
    ROOT
    / "im_regime_aware_valuation_v5.py": "c2e50861e9adb113b131ae495c338bc524275ea6db5ce1343977da2892297476",
    ROOT
    / "docs"
    / "im_regime_aware_valuation_v5_spec.md": "982d5a97dff744afe35481b52080e9c1acdd73c9ae9434f775e927975aa510d0",
    ROOT
    / "docs"
    / "im_regime_aware_valuation_v5_postrun_audit.md": "f4c4eda8e54ce582637f92d4f1be0df22ed44b6bcad671504e99259c3a2fa933",
    V5_OUTPUT
    / "decision_summary.json": "c6e4f212dc7dd4bcae8f88414a60c1ff8cd40b1b304c2959f3f5b3b4fc1804e2",
    V5_OUTPUT
    / "integrity_checks.json": "63ff210681def6230a3fa6a45c5317825b199c921e085c4057cb7f0e91d83d09",
    V5_OUTPUT
    / "output_manifest.json": "50e47eee82b1990eb40fc1d1e24cdaab7774c47b711ea933a7cb1495b760c1c2",
    ROOT
    / "im_fixed_valuation_tier_relationship_v3.py": "4e5c36ab2dcc5ec9d8e6d3ba3c8dd4ee9e2bf705c54c620390326efab967fe4d",
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
        raise RuntimeError("Frozen v6 specification mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_HASH:
        raise RuntimeError("Frozen v6 specification sidecar mismatch")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v6 output or staging already exists")
    if not SCAN.exists():
        raise FileNotFoundError("Initialized v6 parameter-scan directory is missing")
    for path, expected in INPUT_HASHES.items():
        if not path.exists() or sha256(path) != expected:
            raise RuntimeError(f"Frozen v6 input changed: {path}")
    v5_decision = json.loads(
        (V5_OUTPUT / "decision_summary.json").read_text(encoding="utf-8")
    )
    if v5_decision["decision"] != "no_regime_aware_valuation_candidate":
        raise RuntimeError("Frozen v5 rejection input mismatch")
    return {str(path.relative_to(ROOT)): value for path, value in INPUT_HASHES.items()}


def candidate_definitions() -> pd.DataFrame:
    rows: list[dict[str, Any]] = [
        {
            "candidate": "absolute_v3",
            "kind": "absolute",
            "window_months": np.nan,
            "half_life_years": np.nan,
            "ladder": "absolute_245_250_260",
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
                        "half_life_years": np.nan,
                        "ladder": ladder,
                        "quantiles": "/".join(f"{value:.2f}" for value in quantiles),
                        "eligible": kind == "dual" and ladder == "q758595",
                    }
                )
    return pd.DataFrame(rows)


def load_inputs() -> dict[str, pd.DataFrame]:
    return v5.load_inputs()


def calibration_row(
    monthly: pd.DataFrame,
    effective_month: pd.Timestamp,
    window_months: int,
    ladder: str,
) -> dict[str, Any]:
    month = pd.Timestamp(effective_month).to_period("M").to_timestamp()
    history = monthly[monthly["date"].lt(month)].sort_values("date")
    window = history.tail(window_months).copy()
    available = len(window) == window_months
    row: dict[str, Any] = {
        "effective_month": month,
        "window_months": window_months,
        "ladder": ladder,
        "history_months": len(history),
        "sample_months": len(window),
        "window_start": window["date"].min() if len(window) else pd.NaT,
        "window_end": window["date"].max() if len(window) else pd.NaT,
        "max_input_date": window["date"].max() if len(window) else pd.NaT,
        "future_rows_used": int(monthly[monthly["date"].ge(month)].index.isin(window.index).sum()),
        "available": available,
        "pre2022_observations": int(window["date"].lt("2022-01-01").sum()),
        "pre2022_observation_share": (
            float(window["date"].lt("2022-01-01").mean()) if len(window) else np.nan
        ),
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
        return row
    values = window["unbounded_median_knot"].astype(float).to_numpy()
    quantiles = np.quantile(values, LADDERS[ladder], method="linear")
    row.update(
        {
            "threshold_1": float(quantiles[0]),
            "threshold_2": float(quantiles[1]),
            "threshold_3": float(quantiles[2]),
            "strictly_increasing": bool(np.all(np.diff(quantiles) > 0)),
        }
    )
    return row


def rolling_thresholds(daily: pd.DataFrame, monthly: pd.DataFrame) -> pd.DataFrame:
    periods = pd.period_range(
        daily["date"].min().to_period("M"),
        daily["date"].max().to_period("M"),
        freq="M",
    )
    rows = [
        calibration_row(monthly, period.to_timestamp(), window, ladder)
        for period in periods
        for window in WINDOW_MONTHS
        for ladder in LADDERS
    ]
    return pd.DataFrame(rows)


def assign_relative_tier(
    frame: pd.DataFrame,
    thresholds: pd.DataFrame,
    window_months: int,
    ladder: str,
) -> pd.DataFrame:
    selected = thresholds[
        thresholds["window_months"].eq(window_months)
        & thresholds["ladder"].eq(ladder)
    ][
        [
            "effective_month",
            "sample_months",
            "window_start",
            "window_end",
            "max_input_date",
            "pre2022_observation_share",
            "available",
            "strictly_increasing",
            "threshold_1",
            "threshold_2",
            "threshold_3",
        ]
    ]
    result = frame.copy()
    result["effective_month"] = result["date"].dt.to_period("M").dt.to_timestamp()
    result = result.merge(selected, on="effective_month", validate="many_to_one")
    calibrated = result["available"] & result["strictly_increasing"]
    score = result["unbounded_median_knot"].astype(float)
    result["relative_tier"] = np.select(
        [
            calibrated & score.ge(result["threshold_3"]),
            calibrated & score.ge(result["threshold_2"]),
            calibrated & score.ge(result["threshold_1"]),
        ],
        [3, 2, 1],
        default=0,
    ).astype(int)
    result["calibrated"] = calibrated.astype(bool)
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
        absolute["effective_month"] = absolute["date"].dt.to_period("M").dt.to_timestamp()
        for column in (
            "sample_months",
            "window_start",
            "window_end",
            "max_input_date",
            "pre2022_observation_share",
            "threshold_1",
            "threshold_2",
            "threshold_3",
        ):
            absolute[column] = np.nan
        outputs[frequency].append(absolute)
        for window in WINDOW_MONTHS:
            for ladder in LADDERS:
                relative = assign_relative_tier(frame, thresholds, window, ladder)
                relative["absolute_tier"] = relative["median_tier"].astype(int)
                for kind in ("relative", "dual"):
                    item = relative.copy()
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
        "sample_months",
        "window_start",
        "window_end",
        "max_input_date",
        "pre2022_observation_share",
        "threshold_1",
        "threshold_2",
        "threshold_3",
    ]
    expected = set(definitions["candidate"])
    result_daily = pd.concat(outputs["daily"], ignore_index=True)[keep]
    result_monthly = pd.concat(outputs["monthly"], ignore_index=True)[keep]
    if set(result_daily["candidate"]) != expected or set(result_monthly["candidate"]) != expected:
        raise RuntimeError("Finite-window candidate state construction mismatch")
    return result_daily, result_monthly


def state_metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    metrics = v5.state_metrics(frame)
    metrics["calibrated_ratio"] = float(frame["calibrated"].mean())
    return metrics


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
            daily_part = daily if boundary is None else daily[daily["date"].ge(boundary)]
            monthly_part = monthly if boundary is None else monthly[monthly["date"].ge(boundary)]
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
                    "window_months": definition.window_months,
                    "ladder": definition.ladder,
                    **{f"daily_{key}": value for key, value in dm.items()},
                    **{f"monthly_{key}": value for key, value in mm.items()},
                    "metric_semantics": "underlying_price_index_context_only_no_strategy_return",
                }
            )
    long = pd.DataFrame(rows)
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
        "daily_calibrated_ratio",
        "monthly_nonzero_ratio",
    )
    wide_rows: list[dict[str, Any]] = []
    for candidate, part in long.groupby("candidate", sort=False):
        definition = definitions[definitions["candidate"].eq(candidate)].iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "kind": definition["kind"],
            "window_months": definition["window_months"],
            "ladder": definition["ladder"],
        }
        for item in part.itertuples(index=False):
            for metric in metrics:
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def epoch_metrics(daily_states: pd.DataFrame) -> pd.DataFrame:
    epochs = {
        "full": (pd.Timestamp("2015-10-19"), pd.Timestamp("2026-08-17")),
        "2022_now": (pd.Timestamp("2022-01-01"), pd.Timestamp("2026-08-17")),
        "2025_now": (pd.Timestamp("2025-01-01"), pd.Timestamp("2026-08-17")),
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
                    "active_years": int(part.loc[part["final_tier"].gt(0), "date"].dt.year.nunique()),
                }
            )
    return pd.DataFrame(rows)


def event_audit(monthly_states: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "2022_now": (pd.Timestamp("2022-01-01"), pd.Timestamp("2026-08-17")),
        "2025_now": (pd.Timestamp("2025-01-01"), pd.Timestamp("2026-08-17")),
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
                        "active_years": int(part.loc[active.to_numpy(), "date"].dt.year.nunique()),
                        "start_dates": "|".join(part.iloc[positions]["date"].dt.strftime("%Y-%m-%d")),
                    }
                )
    return pd.DataFrame(rows)


def threshold_drift(thresholds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    available = thresholds[thresholds["available"] & thresholds["strictly_increasing"]]
    for (window, ladder), group in available.groupby(["window_months", "ladder"]):
        group = group.sort_values("effective_month")
        row: dict[str, Any] = {
            "window_months": int(window),
            "ladder": ladder,
            "available_months": len(group),
            "first_month": group["effective_month"].min(),
            "last_month": group["effective_month"].max(),
        }
        for number in (1, 2, 3):
            changes = group[f"threshold_{number}"].diff().abs().dropna()
            row[f"threshold_{number}_median_abs_change"] = float(changes.median())
            row[f"threshold_{number}_p95_abs_change"] = float(changes.quantile(0.95))
            row[f"threshold_{number}_max_abs_change"] = float(changes.max())
        rows.append(row)
    return pd.DataFrame(rows)


def stability_matrix(daily_states: pd.DataFrame) -> pd.DataFrame:
    candidates = [PRIMARY, *NEIGHBORS]
    part = daily_states[
        daily_states["candidate"].isin(candidates) & daily_states["date"].ge("2022-01-01")
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
                "nonzero_jaccard": v5.nonzero_jaccard(left, right),
                "mean_abs_tier_difference": float((left - right).abs().mean()),
            }
        )
    return pd.DataFrame(rows)


def vintage_audit(monthly: pd.DataFrame, thresholds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in thresholds[thresholds["available"]].itertuples(index=False):
        truncated = monthly[monthly["date"].lt(pd.Timestamp(row.effective_month))]
        recalculated = calibration_row(
            truncated,
            pd.Timestamp(row.effective_month),
            int(row.window_months),
            str(row.ladder),
        )
        maximum = max(
            abs(float(recalculated[f"threshold_{number}"]) - float(getattr(row, f"threshold_{number}")))
            for number in (1, 2, 3)
        )
        rows.append(
            {
                "effective_month": row.effective_month,
                "window_months": int(row.window_months),
                "ladder": row.ladder,
                "formal_max_input_date": row.max_input_date,
                "recalculated_max_input_date": recalculated["max_input_date"],
                "threshold_max_abs_error": maximum,
                "future_rows_used": int(recalculated["future_rows_used"]),
            }
        )
    return pd.DataFrame(rows)


def rolling_window_audit(monthly: pd.DataFrame, thresholds: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (window, ladder), group in thresholds[thresholds["available"]].groupby(
        ["window_months", "ladder"]
    ):
        group = group.sort_values("effective_month")
        previous_dates: set[pd.Timestamp] | None = None
        for row in group.itertuples(index=False):
            dates = set(
                monthly[
                    monthly["date"].lt(pd.Timestamp(row.effective_month))
                ].sort_values("date").tail(int(window))["date"]
            )
            overlap = len(dates & previous_dates) if previous_dates is not None else np.nan
            rows.append(
                {
                    "effective_month": row.effective_month,
                    "window_months": int(window),
                    "ladder": ladder,
                    "sample_months": len(dates),
                    "overlap_with_prior": overlap,
                    "expected_overlap": int(window) - 1 if previous_dates is not None else np.nan,
                    "roll_is_expected": True if previous_dates is None else overlap == int(window) - 1,
                }
            )
            previous_dates = dates
    return pd.DataFrame(rows)


def current_state(daily_states: pd.DataFrame, definitions: pd.DataFrame) -> pd.DataFrame:
    latest = daily_states["date"].max()
    current = daily_states[daily_states["date"].eq(latest)].copy()
    current = current.merge(
        definitions[["candidate", "kind", "window_months", "ladder", "eligible"]],
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
            "eligible",
            "unbounded_median_knot",
            "absolute_tier",
            "relative_tier",
            "final_tier",
            "sample_months",
            "window_start",
            "window_end",
            "pre2022_observation_share",
            "threshold_1",
            "threshold_2",
            "threshold_3",
        ]
    ].sort_values(["kind", "candidate"])


def candidate_gate(candidate: str, epochs: pd.DataFrame, events: pd.DataFrame) -> dict[str, Any]:
    recent = epochs[
        epochs["candidate"].eq(candidate) & epochs["epoch"].eq("2022_now")
    ].iloc[0]
    event_recent = events[
        events["candidate"].eq(candidate) & events["period"].eq("2022_now")
    ].set_index("level_at_least")
    return {
        "coverage_gate": bool(
            0.12 <= recent["nonzero_ratio"] <= 0.45
            and 0.05 <= recent["tier2plus_ratio"] <= 0.30
            and 0.01 <= recent["tier3plus_ratio"] <= 0.18
        ),
        "exact_tier_resolution_gate": bool(
            recent["tier1_ratio"] >= 0.01
            and recent["tier2_ratio"] >= 0.01
            and recent["tier3_ratio"] >= 0.01
        ),
        "recent_event_gate": bool(
            int(event_recent.loc[1, "episodes"]) >= 3
            and int(event_recent.loc[2, "episodes"]) >= 2
            and int(event_recent.loc[3, "episodes"]) >= 1
            and int(recent["active_years"]) >= 3
        ),
        "churn_gate": bool(float(recent["annualized_transitions"]) <= 24.0),
        "recent_nonzero_ratio": float(recent["nonzero_ratio"]),
        "recent_tier1_ratio": float(recent["tier1_ratio"]),
        "recent_tier2_ratio": float(recent["tier2_ratio"]),
        "recent_tier3_ratio": float(recent["tier3_ratio"]),
        "recent_level1_episodes": int(event_recent.loc[1, "episodes"]),
        "recent_level2_episodes": int(event_recent.loc[2, "episodes"]),
        "recent_level3_episodes": int(event_recent.loc[3, "episodes"]),
        "recent_active_years": int(recent["active_years"]),
        "recent_annualized_transitions": float(recent["annualized_transitions"]),
    }


def select_candidate(
    epochs: pd.DataFrame,
    events: pd.DataFrame,
    drift: pd.DataFrame,
    stability: pd.DataFrame,
    current: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    gates: dict[str, dict[str, Any]] = {}
    for candidate in (PRIMARY, *NEIGHBORS):
        gate = candidate_gate(candidate, epochs, events)
        gate["core_gate_pass"] = all(
            gate[key]
            for key in ("coverage_gate", "exact_tier_resolution_gate", "recent_event_gate", "churn_gate")
        )
        gates[candidate] = gate
        rows.append({"candidate": candidate, **gate})

    drift_row = drift[
        drift["window_months"].eq(60) & drift["ladder"].eq("q758595")
    ].iloc[0]
    drift_pass = bool(
        all(
            float(drift_row[f"threshold_{number}_median_abs_change"]) <= 0.05
            and float(drift_row[f"threshold_{number}_p95_abs_change"]) <= 0.25
            and float(drift_row[f"threshold_{number}_max_abs_change"]) <= 0.75
            for number in (1, 2, 3)
        )
    )
    current_row = current[current["candidate"].eq(PRIMARY)].iloc[0]
    current_window_pass = bool(
        int(current_row["sample_months"]) == 60
        and pd.Timestamp(current_row["window_end"]) <= pd.Timestamp("2026-07-31")
        and float(current_row["pre2022_observation_share"]) <= 0.10
    )
    stability_pass = bool(
        stability["exact_tier_agreement"].ge(0.70).all()
        and stability["weighted_kappa"].ge(0.60).all()
        and stability["nonzero_jaccard"].ge(0.70).all()
    )
    primary_pass = bool(gates[PRIMARY]["core_gate_pass"] and drift_pass and current_window_pass)
    neighbor_pass = bool(
        all(gates[candidate]["core_gate_pass"] for candidate in NEIGHBORS)
        and stability_pass
    )
    if primary_pass and neighbor_pass:
        decision = "freeze_dual_w60_q758595_for_next_put_layer"
        stability_label = "wide_stable"
        selected: str | None = PRIMARY
    elif primary_pass:
        decision = "watchlist_finite_window_dual_axis"
        stability_label = "peak_only"
        selected = None
    else:
        decision = "no_finite_window_valuation_candidate"
        stability_label = "reject"
        selected = None
    summary = {
        "decision": decision,
        "stability_label": stability_label,
        "selected_candidate": selected,
        "primary_candidate": PRIMARY,
        "primary_core_gate_pass": gates[PRIMARY]["core_gate_pass"],
        "primary_drift_pass": drift_pass,
        "primary_current_window_pass": current_window_pass,
        "neighbor_core_gate_pass": all(gates[candidate]["core_gate_pass"] for candidate in NEIGHBORS),
        "neighbor_stability_pass": stability_pass,
        "current_score": float(current_row["unbounded_median_knot"]),
        "current_absolute_tier": int(current_row["absolute_tier"]),
        "current_relative_tier": int(current_row["relative_tier"]),
        "current_final_tier": int(current_row["final_tier"]),
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
    rolling_audit: pd.DataFrame,
) -> dict[str, Any]:
    absolute = daily_states[daily_states["candidate"].eq("absolute_v3")]
    parity = absolute[["date", "final_tier"]].merge(
        inputs["daily"][["date", "median_tier"]], on="date", validate="one_to_one"
    )
    dual = daily_states[daily_states["candidate"].str.startswith("dual_")]
    available = thresholds[thresholds["available"]]
    strict_available = available[available["strictly_increasing"]]
    price_uniques = (
        scan_long.groupby("segment")[["ann_return", "ann_vol", "sharpe_repo", "max_dd"]]
        .nunique()
        .max()
        .max()
    )
    earliest = {
        int(window): str(group["effective_month"].min().date())
        for window, group in available.groupby("window_months")
    }
    checks = {
        "candidate_count": len(definitions),
        "daily_source_rows": len(inputs["daily"]),
        "monthly_source_rows": len(inputs["monthly"]),
        "daily_state_rows": len(daily_states),
        "monthly_state_rows": len(monthly_states),
        "scan_rows": len(scan_long),
        "absolute_v3_parity_failures": int((parity["final_tier"] != parity["median_tier"]).sum()),
        "dual_max_identity_failures": int(
            (dual["final_tier"] != dual[["absolute_tier", "relative_tier"]].max(axis=1)).sum()
        ),
        "tier_out_of_range_rows": int((~daily_states["final_tier"].isin([0, 1, 2, 3])).sum()),
        "available_threshold_rows": len(available),
        "available_sample_size_failures": int((available["sample_months"] != available["window_months"]).sum()),
        "threshold_order_failures": int((~available["strictly_increasing"]).sum()),
        "threshold_future_rows_used": int(thresholds["future_rows_used"].sum()),
        "threshold_effective_date_failures": int(
            (pd.to_datetime(strict_available["max_input_date"]) >= pd.to_datetime(strict_available["effective_month"])).sum()
        ),
        "vintage_threshold_max_abs_error": float(vintage["threshold_max_abs_error"].max()),
        "vintage_future_rows_used": int(vintage["future_rows_used"].sum()),
        "unexpected_monthly_roll_rows": int((~rolling_audit["roll_is_expected"]).sum()),
        "price_context_max_unique_per_window": int(price_uniques),
        "candidate_window_duplicates": int(scan_long.duplicated(["candidate", "segment"]).sum()),
        "earliest_available_month_by_window": earliest,
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
        and checks["available_sample_size_failures"] == 0
        and checks["threshold_order_failures"] == 0
        and checks["threshold_future_rows_used"] == 0
        and checks["threshold_effective_date_failures"] == 0
        and checks["vintage_threshold_max_abs_error"] <= 1e-14
        and checks["vintage_future_rows_used"] == 0
        and checks["unexpected_monthly_roll_rows"] == 0
        and checks["price_context_max_unique_per_window"] == 1
        and checks["candidate_window_duplicates"] == 0
        and earliest == {48: "2019-10-01", 60: "2020-10-01", 72: "2021-10-01"}
    )
    if not checks["integrity_pass"]:
        raise RuntimeError(f"IM finite-window valuation integrity failed: {checks}")
    return checks


def build_record(
    summary: dict[str, Any],
    current: pd.DataFrame,
    epochs: pd.DataFrame,
    stability: pd.DataFrame,
    drift: pd.DataFrame,
    checks: dict[str, Any],
) -> str:
    display_candidates = ["absolute_v3", PRIMARY, *NEIGHBORS]
    current_display = current[current["candidate"].isin(display_candidates)]
    epoch_display = epochs[
        epochs["candidate"].isin(display_candidates) & epochs["epoch"].isin(["2022_now", "2025_now"])
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
            "> 有限滚动窗口双轴估值本体；只选择状态结构，不含Put或策略收益。",
            "",
            "## 预注册决定",
            "",
            f"- 决定：`{summary['decision']}`；稳定性：`{summary['stability_label']}`。",
            f"- 主候选：`{PRIMARY}`；主自身/门槛漂移/当前窗口：{summary['primary_core_gate_pass']}/{summary['primary_drift_pass']}/{summary['primary_current_window_pass']}。",
            f"- 邻点自身/与主线一致性：{summary['neighbor_core_gate_pass']}/{summary['neighbor_stability_pass']}。",
            "",
            "## 当前状态",
            "",
            current_display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 近期状态分布",
            "",
            epoch_display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 窗口邻点",
            "",
            stability.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 月度门槛漂移",
            "",
            drift_display.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## 完整性与边界",
            "",
            f"- 未来行使用{checks['threshold_future_rows_used']}；历史月份重算最大误差{checks['vintage_threshold_max_abs_error']:.3e}；异常滚动窗口{checks['unexpected_monthly_roll_rows']}。",
            "- 收益、波动、Sharpe和MaxDD只是中证1000价格指数同窗背景，未用于选择。",
            "- 本版没有持仓、交易、期权、手续费、滑点、保证金或现金收益；未批准实盘。",
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
    rolling_audit: pd.DataFrame,
    current: pd.DataFrame,
    gate_table: pd.DataFrame,
    summary: dict[str, Any],
    checks: dict[str, Any],
) -> None:
    STAGING.mkdir(parents=True, exist_ok=False)
    definitions.to_csv(STAGING / "candidate_definitions.csv", index=False)
    thresholds.to_csv(STAGING / "monthly_thresholds.csv", index=False)
    daily_states.to_csv(STAGING / "daily_candidate_states.csv.gz", index=False, compression="gzip")
    monthly_states.to_csv(STAGING / "monthly_candidate_states.csv", index=False)
    scan_long.to_csv(STAGING / "scan_summary.csv", index=False)
    scan_wide.to_csv(STAGING / "window_metrics.csv", index=False)
    epochs.to_csv(STAGING / "epoch_state_metrics.csv", index=False)
    events.to_csv(STAGING / "event_audit.csv", index=False)
    drift.to_csv(STAGING / "threshold_drift.csv", index=False)
    stability.to_csv(STAGING / "window_stability.csv", index=False)
    vintage.to_csv(STAGING / "vintage_invariance.csv", index=False)
    rolling_audit.to_csv(STAGING / "rolling_window_audit.csv", index=False)
    current.to_csv(STAGING / "current_state.csv", index=False)
    gate_table.to_csv(STAGING / "candidate_gate_table.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "record.md").write_text(
        build_record(summary, current, epochs, stability, drift, checks), encoding="utf-8"
    )
    command_log = (
        "python im_finite_window_valuation_v6.py\n"
        "python -m pytest -q test_im_finite_window_valuation_v6.py\n"
        "uvx ruff check im_finite_window_valuation_v6.py test_im_finite_window_valuation_v6.py\n"
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
            "adjustment_mode": "official price-index level; valuation inputs frozen from v3",
            "cache_writes": "none",
        },
        "calibration": {
            "window_months": list(WINDOW_MONTHS),
            "ladders": LADDERS,
            "schedule": "prior month-end data only; locked for full current calendar month",
            "quantile_method": "numpy_linear",
            "combination": "max(absolute_v3_tier, rolling_relative_tier)",
        },
        "decision": summary,
        "integrity": checks,
        "execution_and_cost": "not_applicable_valuation_state_only",
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
        "git_status": git_status(),
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (STAGING / "output_manifest.json").write_text(
        json.dumps(output_manifest(STAGING), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    STAGING.rename(OUTPUT)

    scan_long.to_csv(SCAN / "scan_summary.csv", index=False)
    scan_wide.to_csv(SCAN / "window_metrics.csv", index=False)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "preregistered_candidate_bundle_finite_rolling_window",
            "baseline": {"candidate": "absolute_v3"},
            "candidate_grid": definitions.to_dict("records"),
            "data_snapshot": manifest["data_snapshot"],
            "cost_model": {"applicable": False},
            "source_hashes": source_hashes,
            "warnings": [
                "state-selection only; no strategy outcome used",
                "48/60/72-month candidates have different warmup starts",
                "monthly threshold lock is causal but not independent OOS validation",
                "research only, not live approved",
            ],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (SCAN / "record.md").write_text(
        "\n".join(
            [
                f"# {VERSION} Parameter Scan Record",
                "",
                "## Research Question",
                "",
                "- Replace infinite-tail time weighting with finite rolling valuation windows while preserving the v3 absolute economic tier.",
                f"- Primary: `{PRIMARY}`; neighbors: `{NEIGHBORS[0]}`, `{NEIGHBORS[1]}`; q70/80/90 is diagnostic only.",
                "- No IM, MO, Put, basis, or strategy return is used for selection.",
                "",
                "## Data and calibration",
                "",
                f"- Frozen local data: {inputs['daily']['date'].min().date()} to {inputs['daily']['date'].max().date()}, {len(inputs['daily'])} daily and {len(inputs['monthly'])} monthly rows.",
                "- Prior month-end data only; complete 48/60/72-month windows; monthly thresholds locked for the full next month.",
                "- Price-index metrics are identical context fields and cannot select candidates; costs and execution are not applicable.",
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
    thresholds = rolling_thresholds(inputs["daily"], inputs["monthly"])
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
    rolling_audit = rolling_window_audit(inputs["monthly"], thresholds)
    current = current_state(daily_states, definitions)
    gate_table, summary = select_candidate(epochs, events, drift, stability, current)
    checks = integrity_checks(
        inputs,
        definitions,
        thresholds,
        daily_states,
        monthly_states,
        scan_long,
        vintage,
        rolling_audit,
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
        rolling_audit,
        current,
        gate_table,
        summary,
        checks,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
