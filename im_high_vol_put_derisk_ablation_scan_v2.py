from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import freeze_ic_im_system_mainlines_v2 as mainline
import im_put_iv_derisk_overlay_scan_v1 as stage1


ROOT = Path(__file__).resolve().parent
VERSION = "im_high_vol_put_derisk_ablation_scan_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "41fb852e920b0de8e32c17d0d1c278b03146d96d7c0294974f2730d918af8993"
RUN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260825_ic_im_rolling_arbitrage_im_mainline_v2_im_high_vol_put_derisk_ablation_iv_threshold_derisk_shape_ablation"
)
V2_DAILY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
V2_MANIFEST = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "data_manifest.json"

BASELINE = "v2_baseline"
NO_PUT = "no_put_full_core"
THRESHOLDS = (0.30, 0.325, 0.35, 0.375, 0.40)
SHAPES = ("inverse_f50", "linear5_f25", "linear10_f25")
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
FUTURES_RESIZE_ONE_WAY_COST = 0.0001
CALL_RESIZE_ONE_WAY_COST = 0.0001


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def verify_preregistration() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Preregistered v2 specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Preregistered v2 specification sidecar mismatch")
    meta_path = RUN / "scan_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError("Initialized scan folder is missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError(f"Scan is not in init phase: {meta.get('phase')}")
    return meta


def threshold_label(threshold: float) -> str:
    return f"{int(round(threshold * 1000)):03d}"


def candidate_name(mode: str, threshold: float | None = None, shape: str = "none") -> str:
    if mode in {BASELINE, NO_PUT}:
        return mode
    if threshold is None:
        raise ValueError("Threshold is required")
    return f"{mode}__iv{threshold_label(threshold)}__{shape}"


def scale_for_shape(iv: float, threshold: float, shape: str) -> float:
    if not math.isfinite(iv) or iv <= threshold:
        return 1.0
    if shape == "inverse_f50":
        return max(0.50, threshold / iv)
    if shape == "linear5_f25":
        return max(0.25, 1.0 - 5.0 * (iv - threshold))
    if shape == "linear10_f25":
        return max(0.25, 1.0 - 10.0 * (iv - threshold))
    if shape == "none":
        return 1.0
    raise ValueError(f"Unknown derisk shape: {shape}")


def build_policy(
    schedule: pd.DataFrame,
    iv_signal: pd.DataFrame,
    mode: str,
    threshold: float | None,
    shape: str,
    candidate: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = schedule[["eval_date", "execution_date", "binary_target_qty"]].rename(
        columns={"binary_target_qty": "baseline_put_target_qty_schedule"}
    ).merge(
        iv_signal,
        on=["eval_date", "execution_date"],
        how="left",
        validate="one_to_one",
    )
    if not (
        policy["baseline_put_target_qty_schedule"]
        == policy["baseline_put_target_qty"]
    ).all():
        raise RuntimeError("IV signal and schedule Put targets differ")
    policy["candidate"] = candidate
    policy["mode"] = mode
    policy["shape"] = shape
    policy["iv_threshold"] = threshold
    if threshold is None:
        policy["high_iv_gate"] = False
    else:
        policy["high_iv_gate"] = policy["put_implied_vol"].gt(threshold).fillna(False)
    policy["core_scale_target"] = 1.0
    if mode in {"derisk_keep_put", "replace_put_derisk"}:
        active = policy["high_iv_gate"]
        policy.loc[active, "core_scale_target"] = policy.loc[
            active, "put_implied_vol"
        ].map(lambda value: scale_for_shape(float(value), float(threshold), shape))
    policy["candidate_put_target_qty"] = policy["baseline_put_target_qty"].astype(int)
    if mode == NO_PUT:
        policy["candidate_put_target_qty"] = 0
    elif mode in {"gate_only", "replace_put_derisk"}:
        policy.loc[policy["high_iv_gate"], "candidate_put_target_qty"] = 0

    candidate_schedule = schedule.copy()
    candidate_schedule["binary_target_qty"] = policy["candidate_put_target_qty"].to_numpy(dtype=int)
    candidate_schedule["three_tier_target_qty"] = candidate_schedule["binary_target_qty"]
    candidate_schedule["candidate"] = candidate
    candidate_schedule["schedule_candidate"] = candidate
    return candidate_schedule, policy


def recompose(
    official: pd.DataFrame,
    upstream: pd.DataFrame,
    put_overlay: pd.DataFrame,
    policy: pd.DataFrame,
    candidate: str,
    mode: str,
    threshold: float | None,
    shape: str,
) -> pd.DataFrame:
    replace_columns = [
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_fraction",
        "put_contract",
    ]
    frame = official.drop(columns=replace_columns).merge(
        put_overlay[["date", *replace_columns]],
        on="date",
        how="inner",
        validate="one_to_one",
    )
    frame = frame.merge(
        upstream[["date", "cost_rate"]].rename(columns={"cost_rate": "base_roll_cost_rate"}),
        on="date",
        how="left",
        validate="one_to_one",
    )
    audit = policy.set_index("execution_date")
    eod = audit["core_scale_target"].reindex(frame["date"])
    gate = audit["high_iv_gate"].reindex(frame["date"])
    iv = audit["put_implied_vol"].reindex(frame["date"])
    if eod.isna().any() or gate.isna().any():
        raise RuntimeError(f"Missing policy alignment for {candidate}")
    frame["core_scale_eod"] = eod.to_numpy(dtype=float)
    frame["core_scale_held"] = frame["core_scale_eod"].shift(1).fillna(1.0)
    frame["core_scale_change"] = frame["core_scale_eod"].diff().fillna(0.0).abs()
    frame["high_iv_gate"] = gate.to_numpy(dtype=bool)
    frame["put_implied_vol_signal"] = iv.to_numpy(dtype=float)
    frame["grid_im_units"] = frame["total_im_units"] - 1.0
    grid_cost = frame["futures_cost_rate"] - frame["base_roll_cost_rate"]
    if float(grid_cost.min()) < -1e-12:
        raise RuntimeError(f"Negative grid cost for {candidate}")
    frame["grid_futures_cost_rate"] = grid_cost.clip(lower=0.0)
    frame["futures_resize_cost_rate"] = (
        frame["core_scale_change"] * FUTURES_RESIZE_ONE_WAY_COST
    )
    call_active = (
        frame["call_contract"].fillna("").astype(str).ne("")
        | frame["call_contract"].fillna("").astype(str).shift(1).fillna("").ne("")
    )
    frame["call_resize_cost_rate"] = (
        frame["core_scale_change"] * CALL_RESIZE_ONE_WAY_COST * call_active.astype(float)
    )
    frame["base_gross_ret"] = frame["base_gross_ret"] * frame["core_scale_held"]
    frame["call_pnl_ret"] = frame["call_pnl_ret"] * frame["core_scale_held"]
    frame["call_cost_rate"] = (
        frame["call_cost_rate"] * frame["core_scale_eod"]
        + frame["call_resize_cost_rate"]
    )
    frame["call_mark_fraction"] = frame["call_mark_fraction"] * frame["core_scale_eod"]
    frame["call_margin_fraction"] = frame["call_margin_fraction"] * frame["core_scale_eod"]
    frame["call_coverage"] = frame["call_coverage"] * frame["core_scale_eod"]
    frame["futures_cost_rate"] = (
        frame["grid_futures_cost_rate"]
        + frame["base_roll_cost_rate"] * frame["core_scale_held"]
        + frame["futures_resize_cost_rate"]
    )
    frame["total_im_units"] = frame["grid_im_units"] + frame["core_scale_eod"]
    gross = (
        frame["base_gross_ret"]
        + frame["overlay_gross_ret"]
        + frame["put_pnl_ret"]
        + frame["call_pnl_ret"]
    )
    frame["ret"] = (
        (1.0 + gross)
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
    )
    frame["cash_weight_raw"] = (
        1.0
        - 0.30 * frame["total_im_units"]
        - frame["put_mark_fraction"]
        - frame["call_margin_fraction"]
    )
    frame["cash_weight"] = frame["cash_weight_raw"].clip(lower=0.0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    frame["nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["drawdown"] = frame["nav"] / frame["nav"].cummax() - 1.0
    frame["candidate"] = candidate
    frame["mode"] = mode
    frame["shape"] = shape
    frame["iv_threshold"] = threshold
    if frame[["cash_ret", "nav", "drawdown"]].isna().any().any():
        raise RuntimeError(f"Invalid daily values for {candidate}")
    if frame["cash_ret"].le(-1.0).any():
        raise RuntimeError(f"Daily loss <= -100% for {candidate}")
    if float(frame["cash_weight_raw"].min()) < -1e-12:
        raise RuntimeError(f"Negative cash weight for {candidate}")
    return frame


def segment_sample(group: pd.DataFrame, segment: str) -> tuple[pd.DataFrame, bool]:
    end = pd.Timestamp(group["date"].max())
    if segment == "full":
        return group.copy(), True
    years = int(segment.removeprefix("last_").removesuffix("y"))
    requested = end - pd.DateOffset(years=years)
    complete = pd.Timestamp(group["date"].min()) <= requested
    start = max(pd.Timestamp(group["date"].min()), requested)
    return group[group["date"].ge(start)].copy(), complete


def rolling_compound_min(returns: pd.Series, window: int) -> float:
    value = (1.0 + returns).rolling(window).apply(np.prod, raw=True) - 1.0
    return float(value.min()) if value.notna().any() else np.nan


def metric_row(group: pd.DataFrame, segment: str) -> dict[str, Any]:
    sample, window_complete = segment_sample(group, segment)
    returns = sample["cash_ret"].astype(float)
    rows = len(sample)
    nav = (1.0 + returns).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    std = float(returns.std(ddof=1)) if rows > 1 else 0.0
    tail_n = max(1, int(math.ceil(rows * 0.05)))
    sorted_returns = returns.sort_values()
    return {
        "candidate": str(group["candidate"].iloc[0]),
        "mode": str(group["mode"].iloc[0]),
        "shape": str(group["shape"].iloc[0]),
        "iv_threshold": group["iv_threshold"].iloc[0],
        "segment": segment,
        "start": sample["date"].min().date().isoformat(),
        "end": sample["date"].max().date().isoformat(),
        "rows": rows,
        "window_complete": window_complete,
        "ann_return": float(nav.iloc[-1] ** (252.0 / rows) - 1.0),
        "ann_vol": std * math.sqrt(252.0),
        "sharpe_repo": float(returns.mean()) / std * math.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(drawdown.min()),
        "worst_5d": rolling_compound_min(returns, 5),
        "worst_20d": rolling_compound_min(returns, 20),
        "cvar_5_daily": float(sorted_returns.iloc[:tail_n].mean()),
        "avg_weight": float(sample["core_scale_eod"].mean()),
        "held_day_avg_weight": float(sample["core_scale_held"].mean()),
        "min_core_scale": float(sample["core_scale_eod"].min()),
        "holding_days": int(sample["high_iv_gate"].sum()),
        "holding_day_ratio": float(sample["high_iv_gate"].mean()),
        "derisk_days": int(sample["core_scale_eod"].lt(1.0 - 1e-12).sum()),
        "core_turnover": float(sample["core_scale_change"].sum()),
        "put_protected_days": int(sample["put_fraction"].gt(0).sum()),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "put_pnl_sum": float(sample["put_pnl_ret"].sum()),
        "futures_resize_cost_total": float(sample["futures_resize_cost_rate"].sum()),
        "call_resize_cost_total": float(sample["call_resize_cost_rate"].sum()),
        "min_cash_weight_raw": float(sample["cash_weight_raw"].min()),
        "max_total_im_units": float(sample["total_im_units"].max()),
    }


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for _, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        for segment in WINDOWS:
            rows.append(metric_row(group, segment))
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "mode": first["mode"],
            "shape": first["shape"],
            "iv_threshold": first["iv_threshold"],
        }
        for item in group.itertuples(index=False):
            for metric in (
                "ann_return",
                "ann_vol",
                "sharpe_repo",
                "max_dd",
                "worst_5d",
                "worst_20d",
                "cvar_5_daily",
                "avg_weight",
                "holding_day_ratio",
            ):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
            row[f"window_complete_{item.segment}"] = item.window_complete
        wide_rows.append(row)
    return summary, pd.DataFrame(wide_rows)


def build_ablation(summary: pd.DataFrame) -> pd.DataFrame:
    full = summary[summary["segment"].eq("full")].set_index("candidate")
    base = full.loc[BASELINE]
    no_put = full.loc[NO_PUT]
    rows: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        gate_name = candidate_name("gate_only", threshold, "none")
        gate = full.loc[gate_name]
        for shape in SHAPES:
            keep_name = candidate_name("derisk_keep_put", threshold, shape)
            replace_name = candidate_name("replace_put_derisk", threshold, shape)
            keep = full.loc[keep_name]
            replace = full.loc[replace_name]
            rows.append(
                {
                    "iv_threshold": threshold,
                    "shape": shape,
                    "baseline_ann_return": base["ann_return"],
                    "no_put_ann_return": no_put["ann_return"],
                    "gate_only_ann_return": gate["ann_return"],
                    "derisk_keep_put_ann_return": keep["ann_return"],
                    "replace_put_derisk_ann_return": replace["ann_return"],
                    "gate_only_ann_delta_pp": 100.0 * (gate["ann_return"] - base["ann_return"]),
                    "derisk_keep_put_ann_delta_pp": 100.0 * (keep["ann_return"] - base["ann_return"]),
                    "replace_ann_delta_pp": 100.0 * (replace["ann_return"] - base["ann_return"]),
                    "derisk_increment_vs_gate_pp": 100.0 * (replace["ann_return"] - gate["ann_return"]),
                    "baseline_max_dd": base["max_dd"],
                    "gate_only_max_dd": gate["max_dd"],
                    "derisk_keep_put_max_dd": keep["max_dd"],
                    "replace_put_derisk_max_dd": replace["max_dd"],
                    "replace_mdd_improvement_pp": 100.0 * (replace["max_dd"] - base["max_dd"]),
                    "derisk_mdd_increment_vs_gate_pp": 100.0 * (replace["max_dd"] - gate["max_dd"]),
                }
            )
    return pd.DataFrame(rows)


def evaluate_decision(summary: pd.DataFrame) -> tuple[str, str, str | None, pd.DataFrame]:
    full = summary[summary["segment"].eq("full")].set_index("candidate")
    three = summary[summary["segment"].eq("last_3y")].set_index("candidate")
    one = summary[summary["segment"].eq("last_1y")].set_index("candidate")
    base_f, base_3, base_1 = full.loc[BASELINE], three.loc[BASELINE], one.loc[BASELINE]
    rows: list[dict[str, Any]] = []
    scan_modes = ("gate_only", "derisk_keep_put", "replace_put_derisk")
    for mode in scan_modes:
        shapes = ("none",) if mode == "gate_only" else SHAPES
        for shape in shapes:
            for threshold in THRESHOLDS:
                name = candidate_name(mode, threshold, shape)
                f, t, o = full.loc[name], three.loc[name], one.loc[name]
                defensive = bool(
                    f["max_dd"] >= base_f["max_dd"] + 0.01
                    and t["max_dd"] >= base_3["max_dd"] + 0.01
                    and f["worst_20d"] >= base_f["worst_20d"] + 0.01
                    and f["ann_return"] >= base_f["ann_return"] - 0.02
                    and f["sharpe_repo"] >= base_f["sharpe_repo"] - 0.05
                    and o["max_dd"] >= base_1["max_dd"] - 0.01
                    and f["min_cash_weight_raw"] >= -1e-12
                )
                return_only = bool(
                    f["ann_return"] > base_f["ann_return"]
                    and t["ann_return"] > base_3["ann_return"]
                    and f["sharpe_repo"] > base_f["sharpe_repo"]
                    and f["max_dd"] >= base_f["max_dd"] - 0.005
                    and t["max_dd"] >= base_3["max_dd"] - 0.005
                    and o["max_dd"] >= base_1["max_dd"] - 0.005
                    and f["min_cash_weight_raw"] >= -1e-12
                )
                rows.append(
                    {
                        "candidate": name,
                        "mode": mode,
                        "shape": shape,
                        "iv_threshold": threshold,
                        "defensive_individual_pass": defensive,
                        "return_individual_pass": return_only,
                        "full_ann_delta_pp": 100.0 * (f["ann_return"] - base_f["ann_return"]),
                        "full_mdd_improvement_pp": 100.0 * (f["max_dd"] - base_f["max_dd"]),
                        "full_worst20_improvement_pp": 100.0 * (f["worst_20d"] - base_f["worst_20d"]),
                        "full_sharpe_delta": f["sharpe_repo"] - base_f["sharpe_repo"],
                    }
                )
    gates = pd.DataFrame(rows)
    gates["defensive_platform"] = False
    gates["return_platform"] = False
    for (mode, shape), group in gates.groupby(["mode", "shape"], sort=False):
        group = group.sort_values("iv_threshold")
        for column, platform in (
            ("defensive_individual_pass", "defensive_platform"),
            ("return_individual_pass", "return_platform"),
        ):
            passed = set(group.loc[group[column], "iv_threshold"].astype(float))
            connected: set[float] = set()
            for left, right in zip(THRESHOLDS[:-1], THRESHOLDS[1:]):
                if left in passed and right in passed:
                    connected.update((left, right))
            mask = (
                gates["mode"].eq(mode)
                & gates["shape"].eq(shape)
                & gates["iv_threshold"].isin(connected)
            )
            gates.loc[mask, platform] = True
    defensive = gates[gates["defensive_platform"]]
    if len(defensive):
        selected = str(
            defensive.sort_values(
                ["full_mdd_improvement_pp", "full_ann_delta_pp"], ascending=False
            ).iloc[0]["candidate"]
        )
        return "watchlist", "defensive_watchlist", selected, gates
    return_platform = gates[gates["return_platform"]]
    if len(return_platform):
        selected = str(
            return_platform.sort_values("full_ann_delta_pp", ascending=False).iloc[0]["candidate"]
        )
        return "watchlist", "return_only_not_defensive", selected, gates
    return "keep_default", "reject", None, gates


def make_record(
    meta: dict[str, Any],
    summary: pd.DataFrame,
    decision: str,
    stability: str,
    selected: str | None,
    parity_max: float,
    iv_signal: pd.DataFrame,
    model_checks: dict[str, Any],
) -> str:
    full = summary[summary["segment"].eq("full")].copy()
    top = full.sort_values("ann_return", ascending=False).head(15)
    baseline = full[full["candidate"].eq(BASELINE)].iloc[0]
    no_put = full[full["candidate"].eq(NO_PUT)].iloc[0]
    lines = [
        "# IM 高波降仓 / Put替换匹配消融扫描 v2",
        "",
        "## Run Metadata",
        "",
        f"- Run id: `{meta['run_id']}`",
        "- Date/timezone: 2026-08-25 / Asia/Shanghai",
        "- Source-change rule: research-only; frozen v2/Poe/production unchanged.",
        "",
        "## Research Question",
        "",
        "Separate the effects of high-IV Put gating, high-vol core derisking, and their combination on the matched frozen-v2 full IM path.",
        "Explicit Put transaction fees are reported but are not a primary rejection gate.",
        "",
        "## Implementation Anchor",
        "",
        "- Frozen baseline: `outputs/ic_im_system_mainlines_v2/daily_candidates.csv.gz`.",
        "- Official Put runner: `im_mo_close_execution_v8.run_real_normal_close`.",
        f"- Frozen Put path parity max absolute error: {parity_max:.3e}.",
        "",
        "## Data Snapshot",
        "",
        f"- Real sample: {baseline['start']} to {baseline['end']}; {int(baseline['rows'])} rows.",
        f"- Causal IV observations: {int(iv_signal['put_implied_vol'].notna().sum())}.",
        f"- Theoretical market construction was loaded only to obtain the same causal rate/dividend inputs for real IV inversion: `{json.dumps(model_checks, ensure_ascii=False, default=str)}`.",
        "- 10Y/5Y real evidence is N/A; artifact rows use the available real sample only.",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- T-close IV signal, T+1 official-close target change; new core scale earns from the next session.",
        "- Inherited Put/futures/Call costs plus 1bp normalized futures and active-Call resize costs.",
        "- 30% performance margin/buffer per IM and net 3% cash return.",
        "- Excludes spread, impact, non-fill, capacity, dynamic margin hikes, taxes, and integer rounding.",
        "",
        "## Runtime Override Plan",
        "",
        "- All candidate schedules are in-memory copies; baseline is rerun in the same process.",
        "",
        "## Commands",
        "",
        "```powershell",
        "python im_high_vol_put_derisk_ablation_scan_v2.py",
        "```",
        "",
        "## Output Files",
        "",
        "- `scan_summary.csv`, `window_metrics.csv`, `ablation_summary.csv`",
        "- `decision_gates.csv`, `parity_checks.csv`, `daily_outputs/`",
        "",
        "## Full-Sample Results",
        "",
        f"Baseline CAGR/Sharpe/MaxDD: {baseline['ann_return']:.2%} / {baseline['sharpe_repo']:.3f} / {baseline['max_dd']:.2%}.",
        f"No-Put full-core CAGR/Sharpe/MaxDD: {no_put['ann_return']:.2%} / {no_put['sharpe_repo']:.3f} / {no_put['max_dd']:.2%}.",
        "",
        "| candidate | mode | threshold | shape | CAGR | Sharpe | MaxDD | worst20d | avg core |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in top.itertuples(index=False):
        threshold = "-" if pd.isna(row.iv_threshold) else f"{row.iv_threshold:.1%}"
        lines.append(
            f"| {row.candidate} | {row.mode} | {threshold} | {row.shape} | {row.ann_return:.2%} | {row.sharpe_repo:.3f} | {row.max_dd:.2%} | {row.worst_20d:.2%} | {row.avg_weight:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Window Results",
            "",
            "See `scan_summary.csv` and `window_metrics.csv`. Full/3Y/1Y are real; 10Y/5Y are incomplete available-sample rows and must be user-facing N/A.",
            "",
            "## Stability Classification",
            "",
            f"- Label: `{stability}`",
            f"- Selected candidate: `{selected or 'none'}`",
            "- Defensive and return-only platforms are evaluated separately by the preregistered adjacent-threshold rules.",
            "- No result is a fresh holdout or live authorization.",
            "",
            "## Decision",
            "",
            f"- Decision: `{decision}`",
            "- Frozen v2 remains unchanged. A model/proxy extension requires a separate preregistration after this real-data ablation identifies a stable family.",
            "",
            "## User-Facing Summary",
            "",
            f"Decision `{decision}`, stability `{stability}`, selected `{selected or 'none'}`. Research evidence only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    meta = verify_preregistration()
    upstream, active_im, options, source, valuation_state, thresholds, _ = mainline._im_source_data()
    schedule = mainline.build_im_selected_schedule(source, valuation_state, thresholds)
    official = pd.read_csv(V2_DAILY, parse_dates=["date"], low_memory=False)
    official = official[official["product"].eq("IM")].sort_values("date").reset_index(drop=True)
    baseline_overlay, baseline_trades, _ = mainline.im_v12.v8.run_real_normal_close(
        upstream, options, active_im, schedule, "3m", 0.95, "v2_parity"
    )
    parity_max, parity_checks = stage1.baseline_put_parity(official, baseline_overlay)
    model_market, model_checks = stage1.market_v6.model_market()
    iv_signal = stage1.build_iv_signal(schedule, options, active_im, model_market)

    daily_parts: list[pd.DataFrame] = []
    policy_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []

    baseline_schedule, baseline_policy = build_policy(
        schedule, iv_signal, BASELINE, None, "none", BASELINE
    )
    baseline = recompose(
        official, upstream, baseline_overlay, baseline_policy, BASELINE, BASELINE, None, "none"
    )
    baseline_error = float((baseline["cash_ret"] - official["cash_ret"]).abs().max())
    if baseline_error > 1e-12:
        raise RuntimeError(f"Full baseline recomposition mismatch: {baseline_error}")
    daily_parts.append(baseline)
    policy_parts.append(baseline_policy)
    trade_parts.append(baseline_trades.assign(candidate=BASELINE))

    no_put_schedule, no_put_policy = build_policy(
        schedule, iv_signal, NO_PUT, None, "none", NO_PUT
    )
    no_put_overlay, no_put_trades, _ = mainline.im_v12.v8.run_real_normal_close(
        upstream, options, active_im, no_put_schedule, "3m", 0.95, NO_PUT
    )
    daily_parts.append(
        recompose(official, upstream, no_put_overlay, no_put_policy, NO_PUT, NO_PUT, None, "none")
    )
    policy_parts.append(no_put_policy)
    if len(no_put_trades):
        trade_parts.append(no_put_trades)

    overlay_cache: dict[tuple[str, float], tuple[pd.DataFrame, pd.DataFrame]] = {}
    for threshold in THRESHOLDS:
        gate_name = candidate_name("gate_only", threshold, "none")
        gate_schedule, gate_policy = build_policy(
            schedule, iv_signal, "gate_only", threshold, "none", gate_name
        )
        gate_overlay, gate_trades, _ = mainline.im_v12.v8.run_real_normal_close(
            upstream, options, active_im, gate_schedule, "3m", 0.95, gate_name
        )
        overlay_cache[("gate", threshold)] = (gate_overlay, gate_trades)
        daily_parts.append(
            recompose(
                official, upstream, gate_overlay, gate_policy, gate_name, "gate_only", threshold, "none"
            )
        )
        policy_parts.append(gate_policy)
        if len(gate_trades):
            trade_parts.append(gate_trades)

        for shape in SHAPES:
            keep_name = candidate_name("derisk_keep_put", threshold, shape)
            keep_schedule, keep_policy = build_policy(
                schedule, iv_signal, "derisk_keep_put", threshold, shape, keep_name
            )
            if not np.array_equal(
                keep_schedule["binary_target_qty"].to_numpy(),
                schedule["binary_target_qty"].to_numpy(),
            ):
                raise RuntimeError("derisk_keep_put changed Put target")
            keep_overlay = baseline_overlay.copy()
            daily_parts.append(
                recompose(
                    official,
                    upstream,
                    keep_overlay,
                    keep_policy,
                    keep_name,
                    "derisk_keep_put",
                    threshold,
                    shape,
                )
            )
            policy_parts.append(keep_policy)

            replace_name = candidate_name("replace_put_derisk", threshold, shape)
            replace_schedule, replace_policy = build_policy(
                schedule, iv_signal, "replace_put_derisk", threshold, shape, replace_name
            )
            if not np.array_equal(
                replace_schedule["binary_target_qty"].to_numpy(),
                gate_schedule["binary_target_qty"].to_numpy(),
            ):
                raise RuntimeError("Replacement Put target differs from gate-only")
            daily_parts.append(
                recompose(
                    official,
                    upstream,
                    gate_overlay,
                    replace_policy,
                    replace_name,
                    "replace_put_derisk",
                    threshold,
                    shape,
                )
            )
            policy_parts.append(replace_policy)

    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    policies = pd.concat(policy_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    summary, wide = build_metrics(daily)
    ablation = build_ablation(summary)
    decision, stability, selected, decision_gates = evaluate_decision(summary)

    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(exist_ok=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    ablation.to_csv(RUN / "ablation_summary.csv", index=False, encoding="utf-8-sig")
    decision_gates.to_csv(RUN / "decision_gates.csv", index=False, encoding="utf-8-sig")
    parity_checks.to_csv(RUN / "parity_checks.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(daily_dir / "daily_candidates.csv.gz", index=False, compression="gzip")
    policies.to_csv(daily_dir / "policy_audit.csv.gz", index=False, compression="gzip")
    trades.to_csv(daily_dir / "put_trades.csv.gz", index=False, compression="gzip")
    iv_signal.to_csv(daily_dir / "iv_signal.csv.gz", index=False, compression="gzip")

    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {
                "candidate": BASELINE,
                "authority": "outputs/ic_im_system_mainlines_v2",
                "put_parity_max_abs": parity_max,
                "full_recomposition_max_abs": baseline_error,
            },
            "candidate_grid": [
                {"mode": "gate_only", "iv_threshold": threshold, "shape": "none"}
                for threshold in THRESHOLDS
            ]
            + [
                {"mode": mode, "iv_threshold": threshold, "shape": shape}
                for mode in ("derisk_keep_put", "replace_put_derisk")
                for threshold in THRESHOLDS
                for shape in SHAPES
            ],
            "data_snapshot": {
                "source": "real CFFEX IM/MO plus CSI1000 index and local government yield",
                "start": str(official["date"].min().date()),
                "end": str(official["date"].max().date()),
                "rows": len(official),
                "real_history_under_5y": True,
                "v2_manifest_sha256": sha256(V2_MANIFEST),
            },
            "cost_model": {
                "performance_margin_per_im": 0.30,
                "cash_annual_net": 0.03,
                "futures_resize_one_way": FUTURES_RESIZE_ONE_WAY_COST,
                "call_resize_one_way": CALL_RESIZE_ONE_WAY_COST,
                "put_transaction_cost_is_secondary_decision_metric": True,
                "excluded": [
                    "bid_ask_spread",
                    "close_impact",
                    "price_limit_nonfill",
                    "order_book_capacity",
                    "dynamic_margin_hike",
                    "tax",
                    "integer_contract_rounding",
                ],
            },
            "parity_check": {
                "pass": True,
                "put_max_abs": parity_max,
                "full_recomposition_max_abs": baseline_error,
                "threshold": 1e-12,
            },
            "outputs": {
                **meta["outputs"],
                "ablation_summary": str(RUN / "ablation_summary.csv"),
                "decision_gates": str(RUN / "decision_gates.csv"),
                "parity_checks": str(RUN / "parity_checks.csv"),
                "daily": str(daily_dir / "daily_candidates.csv.gz"),
                "policy_audit": str(daily_dir / "policy_audit.csv.gz"),
                "put_trades": str(daily_dir / "put_trades.csv.gz"),
                "iv_signal": str(daily_dir / "iv_signal.csv.gz"),
            },
            "decision": decision,
            "stability_label": stability,
            "selected_candidate": selected,
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): sha256(SPEC),
                str(Path(__file__).relative_to(ROOT)): sha256(Path(__file__)),
                str(Path(stage1.__file__).relative_to(ROOT)): sha256(Path(stage1.__file__)),
                str(V2_DAILY.relative_to(ROOT)): sha256(V2_DAILY),
                str(V2_MANIFEST.relative_to(ROOT)): sha256(V2_MANIFEST),
            },
            "warnings": [
                "Real MO history is shorter than five years; no fresh holdout.",
                "10Y/5Y artifact rows reuse the available real sample and are user-facing N/A.",
                "Fractional core/Call scaling is normalized research exposure, not integer execution.",
                "Model/proxy extension is deferred to a separately preregistered stage after real ablation selection.",
            ],
            "git_status_after": git_value("status", "--short"),
        }
    )
    (RUN / "scan_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (RUN / "record.md").write_text(
        make_record(meta, summary, decision, stability, selected, parity_max, iv_signal, model_checks),
        encoding="utf-8",
    )
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython {Path(__file__).name}\n")
    print(
        json.dumps(
            {
                "decision": decision,
                "stability_label": stability,
                "selected_candidate": selected,
                "candidate_count": int(summary["candidate"].nunique()),
                "put_parity_max_abs": parity_max,
                "full_recomposition_max_abs": baseline_error,
                "run": str(RUN),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
