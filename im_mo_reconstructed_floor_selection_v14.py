from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_2015_valuation_reconstruction_v13 as v13
import im_mo_adaptive_valuation_tier_put_v10 as v10
import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_reconstructed_floor_selection_v14"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "7a0bcbc15019a75b1527c06c15be9cd0a57f6b660ec5ce1114b09de5356c9bc0"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260819_im_mo_reconstructed_floor_selection_v14_im_mo_put_research_mainline_reconstructed_mom120_floor_1_2_3"
)

V13_EARLY = ROOT / "outputs" / "im_mo_2015_valuation_reconstruction_v13" / "early_valuation_reconstruction.csv"
V13_DAILY = ROOT / "outputs" / "im_mo_2015_valuation_reconstruction_v13" / "daily_candidates.csv.gz"
V12_DAILY = ROOT / "outputs" / "im_mo_adaptive_valuation_mom120_floor_v12" / "daily_candidates.csv.gz"
V12_CHURN = ROOT / "outputs" / "im_mo_adaptive_valuation_mom120_floor_v12" / "trade_churn_audit.csv"

INPUT_HASHES = {
    V13_EARLY: "db86aad168267e4919cbf07157ce1d4544b473060737362d047277500b80ee56",
    V13_DAILY: "a041824146e1d76ecad177cc966a0f40ed8d2b1c865268a34fdd89cbd744ae81",
    V12_DAILY: "5a75a9a6aa15d56f023a77b336b660f010b1f87834985385295bfba51e29ba9c",
    V12_CHURN: "f07deda60ce4e603e39bea04d20870cb1dd02a55d6f42fc904fe80e574b15d56",
    ROOT / "im_mo_2015_valuation_reconstruction_v13.py": "4b40100287ed85bf04cf5f7ad93c40f9c678039103fd61e890819df032d3ebd5",
    ROOT / "im_mo_adaptive_valuation_tier_put_v10.py": "4d13b669a73a3782e089d6d35e0a3b7be68e11b61c8f66895cc62a1911e7a894",
    ROOT / "im_mo_close_execution_v8.py": "4ac38a47dac471bcaea77e817f6d74a5fe8ccb65484aa79a4844c80b2226eace",
    ROOT / "im_mo_csi1000_put_protection_battery_v6.py": "7a1043bc5add7bb7d7f09e448dd715715befe08e2ce42dbcf36af849f7999f3d",
}

FLOORS = [1, 2, 3]
NO_PUT = "no_put"
LEGACY = "legacy_fixed175_or_mom120"
WINDOWS = ["full", "last_10y", "last_5y", "last_3y", "last_1y"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_name(floor: int) -> str:
    return f"reconstructed_valmom_floor{floor}"


def verify_inputs(*, require_fresh_output: bool) -> None:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen v14 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen v14 sidecar mismatch")
    for path, expected in INPUT_HASHES.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen v14 input changed: {path.name}: {actual}")
    if not SCAN.exists():
        raise RuntimeError("v14 parameter-scan artifact was not initialized")
    if require_fresh_output and (OUTPUT.exists() or STAGING.exists()):
        raise FileExistsError("Formal v14 output or staging directory already exists")


def candidate_definitions() -> pd.DataFrame:
    rows = [
        {
            "candidate": NO_PUT,
            "family": "baseline",
            "floor_qty": 0,
            "selection_role": "context",
        },
        {
            "candidate": LEGACY,
            "family": "legacy_reference",
            "floor_qty": 2,
            "selection_role": "historical_default",
        },
    ]
    for floor in FLOORS:
        rows.append(
            {
                "candidate": candidate_name(floor),
                "family": "reconstructed_valmom",
                "floor_qty": floor,
                "selection_role": "formal",
            }
        )
    return pd.DataFrame(rows)


def build_schedule(floor: int, early: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    base_name = f"valmom_center_floor{floor}"
    schedule = v13.load_v12_schedule(base_name)
    schedule["v12_target_qty"] = schedule["binary_target_qty"].astype(int)
    early_map = early.set_index("date")["certified_tier"]
    pre = schedule["eval_date"].lt(v13.CUTOVER_EVAL)
    mapped = schedule.loc[pre, "eval_date"].map(early_map)
    if mapped.isna().any():
        raise RuntimeError(f"Missing reconstructed valuation for floor {floor}")
    schedule["reconstructed_certified_tier"] = 0
    schedule.loc[pre, "reconstructed_certified_tier"] = mapped.astype(int).to_numpy()
    schedule["binary_target_qty"] = np.maximum(
        schedule["v12_target_qty"], schedule["reconstructed_certified_tier"]
    ).astype(int)
    schedule["three_tier_target_qty"] = schedule["binary_target_qty"]
    label = candidate_name(floor)
    schedule["candidate"] = label
    schedule["schedule_candidate"] = label
    schedule["source_state"] = f"v13_reconstructed_valuation_plus_mom120_floor{floor}"
    post = schedule["eval_date"].ge(v13.CUTOVER_EVAL)
    post_errors = int(
        schedule.loc[post, "binary_target_qty"].ne(
            schedule.loc[post, "v12_target_qty"]
        ).sum()
    )
    if post_errors:
        raise RuntimeError(f"Post-cutover target changed for floor {floor}")
    return schedule, {
        "floor_qty": floor,
        "pre_cutover_rows": int(pre.sum()),
        "pre_cutover_changed_rows": int(
            schedule.loc[pre, "binary_target_qty"].ne(
                schedule.loc[pre, "v12_target_qty"]
            ).sum()
        ),
        "post_cutover_target_errors": post_errors,
        "first_positive_execution": schedule.loc[
            schedule["binary_target_qty"].gt(0), "execution_date"
        ].min().date().isoformat(),
    }


def path_parity(
    left: pd.DataFrame, right: pd.DataFrame, columns: list[str]
) -> dict[str, float]:
    joined = left[["date", *columns]].merge(
        right[["date", *columns]],
        on="date",
        suffixes=("_left", "_right"),
        validate="one_to_one",
    )
    return {
        column: float(
            (joined[f"{column}_left"] - joined[f"{column}_right"]).abs().max()
        )
        for column in columns
    }


def add_peak_trough(formal: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    result = formal.copy()
    peak_dates: list[str | None] = []
    trough_dates: list[str | None] = []
    for row in result.itertuples(index=False):
        if not bool(row.available):
            peak_dates.append(None)
            trough_dates.append(None)
            continue
        sample = daily[
            daily["layer"].eq(row.layer)
            & daily["candidate"].eq(row.candidate)
            & daily["date"].ge(pd.Timestamp(row.actual_start))
        ].sort_values("date")
        wealth = (1.0 + sample["cash_ret"].astype(float)).cumprod()
        drawdown = wealth / wealth.cummax() - 1.0
        trough_index = drawdown.idxmin()
        peak_index = wealth.loc[:trough_index].idxmax()
        peak_dates.append(sample.loc[peak_index, "date"].date().isoformat())
        trough_dates.append(sample.loc[trough_index, "date"].date().isoformat())
    result["peak_date"] = peak_dates
    result["trough_date"] = trough_dates
    return result


def metric_value(
    formal: pd.DataFrame, layer: str, candidate: str, window: str, column: str
) -> float:
    row = formal[
        formal["layer"].eq(layer)
        & formal["candidate"].eq(candidate)
        & formal["window"].eq(window)
    ]
    if len(row) != 1 or not bool(row.iloc[0]["available"]):
        raise RuntimeError(f"Missing metric: {layer}/{candidate}/{window}/{column}")
    return float(row.iloc[0][column])


def cost_value(
    stress: pd.DataFrame,
    candidate: str,
    multiplier: float,
    column: str = "ann_return",
) -> float:
    row = stress[
        stress["layer"].eq("real")
        & stress["candidate"].eq(candidate)
        & stress["window"].eq("full")
        & stress["cost_multiplier"].eq(multiplier)
    ]
    if len(row) != 1:
        raise RuntimeError(f"Missing cost metric: {candidate}/{multiplier}")
    return float(row.iloc[0][column])


def make_selection(
    formal: pd.DataFrame,
    stress: pd.DataFrame,
    churn: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for floor in FLOORS:
        candidate = candidate_name(floor)
        model_full_dd = (
            metric_value(formal, "model", candidate, "full", "max_dd")
            - metric_value(formal, "model", NO_PUT, "full", "max_dd")
        ) * 100.0
        model_10y_dd = (
            metric_value(formal, "model", candidate, "last_10y", "max_dd")
            - metric_value(formal, "model", NO_PUT, "last_10y", "max_dd")
        ) * 100.0
        real_full_maxdd = metric_value(formal, "real", candidate, "full", "max_dd")
        real_full_dd = (
            real_full_maxdd
            - metric_value(formal, "real", NO_PUT, "full", "max_dd")
        ) * 100.0
        real_3y_dd = (
            metric_value(formal, "real", candidate, "last_3y", "max_dd")
            - metric_value(formal, "real", NO_PUT, "last_3y", "max_dd")
        ) * 100.0
        positive_returns = bool(
            metric_value(formal, "model", candidate, "full", "ann_return") > 0
            and metric_value(formal, "model", candidate, "last_10y", "ann_return") > 0
            and metric_value(formal, "real", candidate, "full", "ann_return") > 0
        )
        cost_2x_delta = (
            cost_value(stress, candidate, 2.0) - cost_value(stress, NO_PUT, 2.0)
        ) * 100.0
        cost_5x_delta = (
            cost_value(stress, candidate, 5.0) - cost_value(stress, NO_PUT, 5.0)
        ) * 100.0
        churn_source = f"valmom_center_floor{floor}"
        churn_row = churn[
            churn["candidate"].eq(churn_source)
            & churn["period"].eq("last_1y")
        ].iloc[0]
        signal_adjustments = float(churn_row["annualized_signal_adjustments"])
        legacy_model_dd = (
            metric_value(formal, "model", candidate, "full", "max_dd")
            - metric_value(formal, "model", LEGACY, "full", "max_dd")
        ) * 100.0
        legacy_real_dd = (
            metric_value(formal, "real", candidate, "full", "max_dd")
            - metric_value(formal, "real", LEGACY, "full", "max_dd")
        ) * 100.0
        legacy_model_ann = (
            metric_value(formal, "model", candidate, "full", "ann_return")
            - metric_value(formal, "model", LEGACY, "full", "ann_return")
        ) * 100.0
        legacy_real_ann = (
            metric_value(formal, "real", candidate, "full", "ann_return")
            - metric_value(formal, "real", LEGACY, "full", "ann_return")
        ) * 100.0
        legacy_pass = bool(
            legacy_model_dd >= -0.50
            and legacy_real_dd >= -0.50
            and legacy_model_ann >= -1.0
            and legacy_real_ann >= -1.0
        )
        core_dd_pass = bool(
            model_full_dd >= 10.0
            and model_10y_dd >= 15.0
            and real_full_dd >= 15.0
            and real_full_maxdd >= -0.20
            and real_3y_dd >= 10.0
        )
        cost_pass = bool(cost_2x_delta >= -1e-12 and cost_5x_delta >= -0.50)
        churn_pass = bool(signal_adjustments <= 24.0 + 1e-12)
        eligible = bool(
            core_dd_pass
            and positive_returns
            and cost_pass
            and churn_pass
            and legacy_pass
        )
        rows.append(
            {
                "floor_qty": floor,
                "candidate": candidate,
                "model_full_dd_improvement_pp": model_full_dd,
                "model_10y_dd_improvement_pp": model_10y_dd,
                "real_full_dd_improvement_pp": real_full_dd,
                "real_full_max_dd": real_full_maxdd,
                "real_3y_dd_improvement_pp": real_3y_dd,
                "positive_return_pass": positive_returns,
                "real_2x_ann_delta_vs_no_put_pp": cost_2x_delta,
                "real_5x_ann_delta_vs_no_put_pp": cost_5x_delta,
                "cost_pass": cost_pass,
                "last1y_signal_adjustments_ann": signal_adjustments,
                "churn_pass": churn_pass,
                "legacy_model_dd_increment_pp": legacy_model_dd,
                "legacy_real_dd_increment_pp": legacy_real_dd,
                "legacy_model_ann_increment_pp": legacy_model_ann,
                "legacy_real_ann_increment_pp": legacy_real_ann,
                "legacy_pass": legacy_pass,
                "core_dd_pass": core_dd_pass,
                "selection_eligible": eligible,
            }
        )
    table = pd.DataFrame(rows)
    eligible_floors = sorted(
        table.loc[table["selection_eligible"], "floor_qty"].astype(int).tolist()
    )
    base_floor = eligible_floors[0] if eligible_floors else None
    incremental_rows: list[dict[str, Any]] = []
    selected_floor = base_floor
    defensive_floor: int | None = None
    if base_floor is not None and base_floor + 1 in FLOORS:
        low = candidate_name(base_floor)
        high = candidate_name(base_floor + 1)
        model_deltas = {
            window: (
                metric_value(formal, "model", high, window, "max_dd")
                - metric_value(formal, "model", low, window, "max_dd")
            )
            * 100.0
            for window in WINDOWS
        }
        real_windows = ["full", "last_3y", "last_1y"]
        real_deltas = {
            window: (
                metric_value(formal, "real", high, window, "max_dd")
                - metric_value(formal, "real", low, window, "max_dd")
            )
            * 100.0
            for window in real_windows
        }
        cost_2x_increment = (
            cost_value(stress, high, 2.0) - cost_value(stress, low, 2.0)
        ) * 100.0
        cost_5x_increment = (
            cost_value(stress, high, 5.0) - cost_value(stress, low, 5.0)
        ) * 100.0
        model_increment_pass = bool(max(model_deltas.values()) >= 2.0)
        real_increment_pass = bool(max(real_deltas.values()) >= 2.0)
        cost_increment_pass = bool(
            cost_2x_increment >= -1e-12 and cost_5x_increment >= -0.25
        )
        upgrade_pass = bool(
            (base_floor + 1) in eligible_floors
            and model_increment_pass
            and real_increment_pass
            and cost_increment_pass
        )
        incremental_rows.append(
            {
                "low_floor": base_floor,
                "high_floor": base_floor + 1,
                **{f"model_{key}_dd_increment_pp": value for key, value in model_deltas.items()},
                **{f"real_{key}_dd_increment_pp": value for key, value in real_deltas.items()},
                "real_2x_ann_increment_pp": cost_2x_increment,
                "real_5x_ann_increment_pp": cost_5x_increment,
                "model_increment_pass": model_increment_pass,
                "real_increment_pass": real_increment_pass,
                "cost_increment_pass": cost_increment_pass,
                "upgrade_pass": upgrade_pass,
            }
        )
        if upgrade_pass:
            selected_floor = base_floor + 1
        elif (base_floor + 1) in eligible_floors:
            defensive_floor = base_floor + 1
    incremental = pd.DataFrame(incremental_rows)
    if selected_floor is not None:
        conclusion = (
            f"select_reconstructed_valmom_floor{selected_floor}_for_im_put_research_mainline"
        )
        stability = (
            "wide_stable"
            if any(abs(value - selected_floor) == 1 for value in eligible_floors)
            else "narrow_stable"
        )
    elif table["core_dd_pass"].any():
        conclusion = "watchlist_reconstructed_floor"
        stability = "narrow_stable"
    else:
        conclusion = "no_reconstructed_floor_candidate"
        stability = "reject"
    summary = {
        "conclusion": conclusion,
        "stability_label": stability,
        "eligible_floors": eligible_floors,
        "selected_floor_qty": selected_floor,
        "selected_candidate": candidate_name(selected_floor)
        if selected_floor is not None
        else None,
        "defensive_stress_floor_qty": defensive_floor,
        "defensive_stress_candidate": candidate_name(defensive_floor)
        if defensive_floor is not None
        else None,
        "live_approved": False,
        "research_status": "research_mainline_only",
    }
    return table, incremental, summary


def scan_tables(
    formal: pd.DataFrame, definitions: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    model = formal[formal["layer"].eq("model")].copy()
    scan = model.rename(
        columns={"window": "segment", "actual_start": "start"}
    )[
        [
            "candidate",
            "segment",
            "start",
            "end",
            "rows",
            "ann_return",
            "ann_vol",
            "sharpe_repo",
            "max_dd",
        ]
    ].merge(definitions, on="candidate", how="left", validate="many_to_one")
    metrics = ["ann_return", "max_dd"]
    wide = model.pivot(index="candidate", columns="window", values=metrics)
    wide.columns = [f"{metric}_{window}" for metric, window in wide.columns]
    wide = wide.reset_index().merge(
        definitions, on="candidate", how="left", validate="one_to_one"
    )
    ordered = ["candidate", "family", "floor_qty", "selection_role"]
    for window in WINDOWS:
        ordered.extend([f"ann_return_{window}", f"max_dd_{window}"])
    return scan, wide[ordered]


def make_record(
    formal: pd.DataFrame,
    selection: pd.DataFrame,
    incremental: pd.DataFrame,
    summary: dict[str, Any],
    parity: dict[str, Any],
    schedule_audits: list[dict[str, Any]],
    stress: pd.DataFrame,
) -> str:
    focus = formal[
        formal["candidate"].isin([NO_PUT, LEGACY, *[candidate_name(f) for f in FLOORS]])
        & (
            formal["layer"].eq("model")
            | formal["window"].isin(["full", "last_3y", "last_1y"])
        )
    ][
        [
            "layer",
            "candidate",
            "window",
            "available",
            "ann_return",
            "max_dd",
            "peak_date",
            "trough_date",
        ]
    ]
    cost_focus = stress[
        stress["layer"].eq("real")
        & stress["window"].eq("full")
        & stress["cost_multiplier"].isin([1.0, 2.0, 5.0])
    ][["candidate", "cost_multiplier", "ann_return", "max_dd"]]
    return "\n".join(
        [
            f"# {VERSION} 正式记录",
            "",
            "> 风险预算再裁决；结果部分已在v12/v13可见，不是盲样本参数发现；未批准实盘。",
            "",
            "## Decision",
            "",
            f"- Decision: `{summary['conclusion']}`.",
            f"- Stability: `{summary['stability_label']}`.",
            f"- Selected: `{summary['selected_candidate']}`.",
            f"- Defensive stress line: `{summary['defensive_stress_candidate']}`.",
            "",
            "## Metrics",
            "",
            focus.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Selection Gates",
            "",
            selection.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Higher-Floor Increment",
            "",
            incremental.to_markdown(index=False, floatfmt=".6f")
            if len(incremental)
            else "No eligible base floor.",
            "",
            "## Real Cost Stress",
            "",
            cost_focus.to_markdown(index=False, floatfmt=".6f"),
            "",
            "## Integrity",
            "",
            f"- Parity: `{json.dumps(parity, ensure_ascii=False)}`.",
            f"- Schedule audit: `{json.dumps(schedule_audits, ensure_ascii=False)}`.",
            "- Model layer is theoretical; real layer reuses official v12 IM/MO close/settle paths. Bid/ask, close impact and capacity remain excluded.",
            "",
        ]
    )


def update_scan(
    scan_summary: pd.DataFrame,
    window_metrics: pd.DataFrame,
    definitions: pd.DataFrame,
    summary: dict[str, Any],
    record: str,
    source_hashes: dict[str, str],
) -> None:
    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    command = (
        "python -m pytest -q test_im_mo_reconstructed_floor_selection_v14.py\n"
        "python im_mo_reconstructed_floor_selection_v14.py\n"
    )
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"primary": NO_PUT, "legacy": LEGACY},
            "candidate_grid": definitions.to_dict("records"),
            "data_snapshot": {
                "model": ["2015-04-16", "2026-08-14"],
                "real": ["2022-07-22", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "model_source": str(V13_DAILY.relative_to(ROOT)),
                "real_source": str(V12_DAILY.relative_to(ROOT)),
            },
            "cost_model": {
                "put_cost_multipliers": [1.0, 2.0, 5.0],
                "cash_weight": 0.70,
                "cash_annual_return": 0.03,
                "signal": "T close",
                "execution": "T+1 close",
                "bid_ask_close_impact_capacity": "excluded",
            },
            "source_hashes": source_hashes,
            "research_conclusion": summary,
            "warnings": [
                "partially known-result policy adjudication, not blind parameter discovery",
                "model layer is theoretical and not pre-listing IM/MO",
                "real 10y/5y are unavailable",
                "not live approved",
            ],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    verify_inputs(require_fresh_output=True)
    definitions = candidate_definitions()
    early = pd.read_csv(V13_EARLY, parse_dates=["date"])
    schedules: dict[int, pd.DataFrame] = {}
    schedule_audits: list[dict[str, Any]] = []
    for floor in FLOORS:
        schedules[floor], audit = build_schedule(floor, early)
        schedule_audits.append(audit)

    market, market_checks = v6.model_market()
    model_base = v6.model_baseline(market)
    overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    life_parts: list[pd.DataFrame] = []
    for floor in FLOORS:
        label = candidate_name(floor)
        overlay, trades, lives = v8.run_model_normal_close(
            market, schedules[floor], "3m", 0.95, label
        )
        overlays[label] = overlay
        if len(trades):
            trade_parts.append(trades)
        if len(lives):
            copy = lives.copy()
            copy["layer"] = "model"
            life_parts.append(copy)
    generated_model = v10.add_nav(v6.assemble_layer("model", model_base, overlays))

    v12_daily = pd.read_csv(V12_DAILY, parse_dates=["date"])
    model_legacy = v12_daily[
        v12_daily["layer"].eq("model") & v12_daily["candidate"].eq(LEGACY)
    ].copy()
    model = pd.concat([generated_model, model_legacy], ignore_index=True)
    real_parts: list[pd.DataFrame] = []
    for candidate in [NO_PUT, LEGACY]:
        real_parts.append(
            v12_daily[
                v12_daily["layer"].eq("real")
                & v12_daily["candidate"].eq(candidate)
            ].copy()
        )
    for floor in FLOORS:
        part = v12_daily[
            v12_daily["layer"].eq("real")
            & v12_daily["candidate"].eq(f"valmom_center_floor{floor}")
        ].copy()
        part["candidate"] = candidate_name(floor)
        real_parts.append(part)
    real = pd.concat(real_parts, ignore_index=True)
    daily = pd.concat([model, real], ignore_index=True).sort_values(
        ["layer", "candidate", "date"]
    )
    expected = set(definitions["candidate"])
    for layer in ["model", "real"]:
        subset = daily[daily["layer"].eq(layer)]
        if set(subset["candidate"]) != expected:
            raise RuntimeError(f"Incomplete v14 candidate set: {layer}")
        if subset.duplicated(["candidate", "date"]).any():
            raise RuntimeError(f"Duplicate v14 candidate/date: {layer}")

    v13_daily = pd.read_csv(V13_DAILY, parse_dates=["date"])
    parity: dict[str, Any] = {}
    parity_columns = ["cash_ret", "cash_nav", "ret", "nav", "put_fraction"]
    for floor in [2, 3]:
        left = daily[
            daily["layer"].eq("model")
            & daily["candidate"].eq(candidate_name(floor))
        ]
        right = v13_daily[
            v13_daily["layer"].eq("model")
            & v13_daily["candidate"].eq(candidate_name(floor))
        ]
        values = path_parity(left, right, parity_columns)
        parity[f"model_floor{floor}_vs_v13"] = values
        if max(values.values()) > 1e-14:
            raise RuntimeError(f"v13 parity failed for floor {floor}: {values}")
    for candidate in [NO_PUT, LEGACY, *[candidate_name(f) for f in FLOORS]]:
        old_name = (
            candidate
            if candidate in {NO_PUT, LEGACY}
            else f"valmom_center_floor{int(candidate[-1])}"
        )
        left = daily[
            daily["layer"].eq("real") & daily["candidate"].eq(candidate)
        ]
        right = v12_daily[
            v12_daily["layer"].eq("real") & v12_daily["candidate"].eq(old_name)
        ]
        values = path_parity(left, right, parity_columns)
        parity[f"real_{candidate}_vs_v12"] = values
        if max(values.values()) > 1e-14:
            raise RuntimeError(f"v12 real parity failed: {candidate}: {values}")

    formal, annual = v6.metrics_tables(daily)
    formal = add_peak_trough(formal, daily)
    stress_daily, stress = v10.cost_sensitivity(daily)
    churn = pd.read_csv(V12_CHURN)
    selection, incremental, summary = make_selection(formal, stress, churn)
    scan_summary, window_metrics = scan_tables(formal, definitions)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(life_parts, ignore_index=True, sort=False)
    schedule_frame = pd.concat(
        [frame.assign(floor_qty=floor) for floor, frame in schedules.items()],
        ignore_index=True,
        sort=False,
    )
    exposure = (
        daily.groupby(["layer", "candidate"], as_index=False)
        .agg(
            rows=("date", "size"),
            protected_days=("put_fraction", lambda value: int(value.gt(0).sum())),
            average_put_fraction=("put_fraction", "mean"),
            put_cost_sum=("put_cost_rate", "sum"),
        )
    )
    record = make_record(
        formal, selection, incremental, summary, parity, schedule_audits, stress
    )
    source_paths = [SPEC, Path(__file__), *INPUT_HASHES]
    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path) for path in source_paths
    }
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "model_market_checks": market_checks,
        "schedule_audits": schedule_audits,
        "parity": parity,
        "decision": summary,
        "research_status": "research_mainline_only_not_live_approved",
    }

    STAGING.mkdir(parents=True, exist_ok=False)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    stress_daily.to_csv(STAGING / "cost_stress_daily.csv.gz", index=False, compression="gzip")
    stress.to_csv(STAGING / "cost_stress_metrics.csv", index=False)
    selection.to_csv(STAGING / "selection_gate_table.csv", index=False)
    incremental.to_csv(STAGING / "higher_floor_increment.csv", index=False)
    definitions.to_csv(STAGING / "candidate_definitions.csv", index=False)
    exposure.to_csv(STAGING / "exposure_cost.csv", index=False)
    schedule_frame.to_csv(
        STAGING / "signal_schedules.csv.gz", index=False, compression="gzip"
    )
    trades.to_csv(STAGING / "model_trade_audit.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(STAGING / "model_lifecycle_audit.csv", index=False)
    (STAGING / "decision_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (STAGING / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    command = (
        "python -m pytest -q test_im_mo_reconstructed_floor_selection_v14.py\n"
        "python im_mo_reconstructed_floor_selection_v14.py\n"
    )
    (STAGING / "command_log.txt").write_text(command, encoding="utf-8")
    update_scan(
        scan_summary,
        window_metrics,
        definitions,
        summary,
        record,
        source_hashes,
    )
    STAGING.replace(OUTPUT)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
