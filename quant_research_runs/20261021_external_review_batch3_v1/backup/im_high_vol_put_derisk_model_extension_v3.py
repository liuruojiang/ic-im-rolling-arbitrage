from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_high_vol_put_derisk_ablation_scan_v2 as real_v2
import im_mo_reconstructed_floor_selection_v14 as v14
import im_put_four_valuation_tier_scan_v2 as four_tier
import im_valuation_window_ladder_scan_v7 as valuation_v7


ROOT = Path(__file__).resolve().parent
VERSION = "im_high_vol_put_derisk_model_extension_v3"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "e1f09cd84ff60dee1b4f23f4d481e157d764697c6bd0e94644644fcda69a5d75"
RUN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260825_ic_im_rolling_arbitrage_im_model_proxy_extension_im_core_put_high_vol_extension_proxy_iv_gate_derisk_2015_2026"
)
V14_SCHEDULE = ROOT / "outputs" / "im_mo_reconstructed_floor_selection_v14" / "signal_schedules.csv.gz"
V14_MANIFEST = ROOT / "outputs" / "im_mo_reconstructed_floor_selection_v14" / "data_manifest.json"

BASELINE = "proxy_hybrid_v2_put"
NO_PUT = "proxy_no_put_full_core"
THRESHOLDS = (0.35, 0.375, 0.40)
SHAPES = real_v2.SHAPES
WINDOWS = real_v2.WINDOWS
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
RESIZE_ONE_WAY_COST = 0.0001
STRESS_WINDOWS = {
    "2015_crash": (pd.Timestamp("2015-06-01"), pd.Timestamp("2015-09-30")),
    "2018": (pd.Timestamp("2018-01-01"), pd.Timestamp("2018-12-31")),
    "2020_h1": (pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-30")),
    "2022": (pd.Timestamp("2022-01-01"), pd.Timestamp("2022-12-31")),
}


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
        raise RuntimeError("Preregistered model-extension specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Preregistered model-extension sidecar mismatch")
    meta_path = RUN / "scan_meta.json"
    if not meta_path.exists():
        raise FileNotFoundError("Initialized model-extension scan folder is missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError(f"Model-extension scan is not in init phase: {meta.get('phase')}")
    return meta


def candidate_name(mode: str, threshold: float | None = None, shape: str = "none") -> str:
    if mode in {BASELINE, NO_PUT}:
        return mode
    return real_v2.candidate_name(mode, threshold, shape)


def build_hybrid_schedule() -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    raw = pd.read_csv(
        V14_SCHEDULE,
        parse_dates=["eval_date", "execution_date"],
        low_memory=False,
    )
    source = raw[raw["floor_qty"].eq(3)].sort_values("eval_date").reset_index(drop=True).copy()
    if source.duplicated("eval_date").any():
        raise RuntimeError("Duplicate model schedule evaluation date")
    monthly = valuation_v7.load_inputs()["monthly"].sort_values("date")
    months = source["eval_date"].dt.to_period("M").dt.to_timestamp()
    valid_months = sorted(
        month
        for month in months.unique()
        if int(monthly["date"].lt(pd.Timestamp(month)).sum()) >= four_tier.WINDOW_MONTHS
    )
    if not valid_months:
        raise RuntimeError("No causal 57-month four-tier model window")
    four_tier_start = pd.Timestamp(valid_months[0])
    late_mask = months.ge(four_tier_start)
    late_source = source[late_mask].copy()
    states = v14.v10.load_v7_states()
    valuation_state = states[states["candidate"].eq("dual_w57_q750_850_950")][
        ["date", "unbounded_median_knot", "rolling_percentile", "absolute_tier"]
    ].copy()
    threshold_table = four_tier.build_thresholds(monthly, late_source["eval_date"])
    definition = next(
        item
        for item in four_tier.CANDIDATES
        if item["candidate"] == "IM_4tier_q750_850_900_925"
    )
    threshold_selected = threshold_table[
        threshold_table["candidate"].eq(definition["candidate"])
    ].copy()
    late = four_tier.build_schedule(
        late_source, definition, valuation_state, threshold_selected
    )

    schedule = source.copy()
    base_target = schedule["binary_target_qty"].astype(int).to_numpy()
    negative = schedule["momentum_120"].astype(float).lt(0.0).to_numpy()
    target = np.maximum(base_target, np.where(negative, 4, 0))
    late_target = np.maximum(
        late["new_valuation_tier"].astype(int).to_numpy(),
        np.where(late["momentum_120"].astype(float).lt(0.0).to_numpy(), 4, 0),
    )
    target[late_mask.to_numpy()] = late_target
    schedule["binary_target_qty"] = target.astype(int)
    schedule["three_tier_target_qty"] = target.astype(int)
    schedule["candidate"] = BASELINE
    schedule["schedule_candidate"] = BASELINE
    schedule["hybrid_four_tier_available"] = late_mask.to_numpy()
    if not set(schedule["binary_target_qty"].unique()).issubset({0, 1, 2, 3, 4}):
        raise RuntimeError("Invalid hybrid model Put target")
    if not (
        schedule.loc[schedule["momentum_120"].astype(float).lt(0.0), "binary_target_qty"]
        .astype(int)
        .eq(4)
        .all()
    ):
        raise RuntimeError("Hybrid MOM120-negative floor is not four")
    return schedule, threshold_selected, four_tier_start


def build_proxy_iv_signal(schedule: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    lookup = market.set_index("date")["sigma_close"]
    rows: list[dict[str, Any]] = []
    for event in schedule.itertuples(index=False):
        eval_date = pd.Timestamp(event.eval_date)
        execution_date = pd.Timestamp(event.execution_date)
        initial = eval_date < pd.Timestamp(market["date"].min())
        sigma = np.nan if initial else float(lookup.loc[eval_date])
        rows.append(
            {
                "eval_date": eval_date,
                "execution_date": execution_date,
                "baseline_put_target_qty": int(event.binary_target_qty),
                "proxy_put_iv": sigma,
                "initial_exception": initial,
            }
        )
    signal = pd.DataFrame(rows)
    missing = signal[signal["proxy_put_iv"].isna() & ~signal["initial_exception"]]
    if len(missing):
        raise RuntimeError(f"Unexpected proxy IV gaps: {len(missing)}")
    if not (signal["execution_date"] > signal["eval_date"]).all():
        raise RuntimeError("Non-causal proxy signal timing")
    return signal


def build_policy(
    schedule: pd.DataFrame,
    signal: pd.DataFrame,
    mode: str,
    threshold: float | None,
    shape: str,
    candidate: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = schedule[["eval_date", "execution_date", "binary_target_qty"]].rename(
        columns={"binary_target_qty": "schedule_target"}
    ).merge(signal, on=["eval_date", "execution_date"], validate="one_to_one")
    if not policy["schedule_target"].eq(policy["baseline_put_target_qty"]).all():
        raise RuntimeError("Proxy signal target mismatch")
    policy["candidate"] = candidate
    policy["mode"] = mode
    policy["shape"] = shape
    policy["iv_threshold"] = threshold
    policy["high_iv_gate"] = False if threshold is None else policy["proxy_put_iv"].gt(threshold).fillna(False)
    policy["core_scale_target"] = 1.0
    if mode in {"derisk_keep_put", "replace_put_derisk"}:
        active = policy["high_iv_gate"]
        policy.loc[active, "core_scale_target"] = policy.loc[active, "proxy_put_iv"].map(
            lambda value: real_v2.scale_for_shape(float(value), float(threshold), shape)
        )
    policy["candidate_put_target_qty"] = policy["baseline_put_target_qty"].astype(int)
    if mode == NO_PUT:
        policy["candidate_put_target_qty"] = 0
    elif mode in {"gate_only", "replace_put_derisk"}:
        policy.loc[policy["high_iv_gate"], "candidate_put_target_qty"] = 0
    result = schedule.copy()
    result["binary_target_qty"] = policy["candidate_put_target_qty"].to_numpy(dtype=int)
    result["three_tier_target_qty"] = result["binary_target_qty"]
    result["candidate"] = candidate
    result["schedule_candidate"] = candidate
    return result, policy


def recompose(
    base: pd.DataFrame,
    overlay: pd.DataFrame,
    policy: pd.DataFrame,
    candidate: str,
    mode: str,
    threshold: float | None,
    shape: str,
) -> pd.DataFrame:
    frame = base.merge(overlay, on="date", validate="one_to_one")
    audit = policy.set_index("execution_date")
    eod = audit["core_scale_target"].reindex(frame["date"])
    gate = audit["high_iv_gate"].reindex(frame["date"])
    proxy_iv = audit["proxy_put_iv"].reindex(frame["date"])
    if eod.isna().any() or gate.isna().any():
        raise RuntimeError(f"Missing model policy alignment for {candidate}")
    frame["core_scale_eod"] = eod.to_numpy(dtype=float)
    frame["core_scale_held"] = frame["core_scale_eod"].shift(1).fillna(1.0)
    frame["core_scale_change"] = frame["core_scale_eod"].diff().fillna(0.0).abs()
    frame["high_iv_gate"] = gate.to_numpy(dtype=bool)
    frame["proxy_put_iv_signal"] = proxy_iv.to_numpy(dtype=float)
    frame["futures_resize_cost_rate"] = frame["core_scale_change"] * RESIZE_ONE_WAY_COST
    frame["scaled_gross_ret"] = frame["gross_ret"] * frame["core_scale_held"]
    frame["scaled_futures_cost_rate"] = (
        frame["cost_rate"] * frame["core_scale_held"]
        + frame["futures_resize_cost_rate"]
    )
    gross = frame["scaled_gross_ret"] + frame["put_pnl_ret"]
    frame["ret"] = (
        (1.0 + gross)
        * (1.0 - frame["scaled_futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        - 1.0
    )
    frame["cash_weight_raw"] = (
        1.0 - 0.30 * frame["core_scale_eod"] - frame["put_mark_fraction"]
    )
    frame["cash_weight"] = frame["cash_weight_raw"].clip(lower=0.0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    frame["nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["drawdown"] = frame["nav"] / frame["nav"].cummax() - 1.0
    frame["candidate"] = candidate
    frame["mode"] = mode
    frame["shape"] = shape
    frame["iv_threshold"] = threshold
    frame["call_resize_cost_rate"] = 0.0
    frame["total_im_units"] = frame["core_scale_eod"]
    if frame[["cash_ret", "nav", "drawdown"]].isna().any().any():
        raise RuntimeError(f"Invalid theoretical result for {candidate}")
    if frame["cash_ret"].le(-1.0).any():
        raise RuntimeError(f"Theoretical daily loss <= -100% for {candidate}")
    return frame


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return real_v2.build_metrics(daily)


def build_stress_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        for label, (start, end) in STRESS_WINDOWS.items():
            sample = group[group["date"].between(start, end)].copy()
            returns = sample["cash_ret"].astype(float)
            nav = (1.0 + returns).cumprod()
            dd = nav / nav.cummax() - 1.0
            rows.append(
                {
                    "candidate": candidate,
                    "mode": group["mode"].iloc[0],
                    "shape": group["shape"].iloc[0],
                    "iv_threshold": group["iv_threshold"].iloc[0],
                    "stress_window": label,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "rows": len(sample),
                    "total_return": float(nav.iloc[-1] - 1.0),
                    "max_dd": float(dd.min()),
                    "worst_5d": real_v2.rolling_compound_min(returns, 5),
                    "avg_core_scale": float(sample["core_scale_eod"].mean()),
                    "gate_days": int(sample["high_iv_gate"].sum()),
                }
            )
    return pd.DataFrame(rows)


def evaluate_decision(summary: pd.DataFrame) -> tuple[str, str, str | None, pd.DataFrame]:
    tables = {
        segment: summary[summary["segment"].eq(segment)].set_index("candidate")
        for segment in ("full", "last_10y", "last_5y")
    }
    rows: list[dict[str, Any]] = []
    for mode in ("gate_only", "derisk_keep_put", "replace_put_derisk"):
        shapes = ("none",) if mode == "gate_only" else SHAPES
        for shape in shapes:
            for threshold in THRESHOLDS:
                name = candidate_name(mode, threshold, shape)
                defensive = True
                return_only = True
                for segment, table in tables.items():
                    row, base = table.loc[name], table.loc[BASELINE]
                    defensive = defensive and bool(
                        row["max_dd"] >= base["max_dd"] + 0.01
                        and row["ann_return"] >= base["ann_return"] - 0.02
                    )
                    return_only = return_only and bool(
                        row["ann_return"] > base["ann_return"]
                        and row["sharpe_repo"] > base["sharpe_repo"]
                        and row["max_dd"] >= base["max_dd"] - 0.01
                    )
                f, base_f = tables["full"].loc[name], tables["full"].loc[BASELINE]
                defensive = defensive and bool(
                    f["worst_20d"] >= base_f["worst_20d"] + 0.01
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
                    }
                )
    gates = pd.DataFrame(rows)
    gates["defensive_platform"] = False
    gates["return_platform"] = False
    for (mode, shape), group in gates.groupby(["mode", "shape"], sort=False):
        for test, platform in (
            ("defensive_individual_pass", "defensive_platform"),
            ("return_individual_pass", "return_platform"),
        ):
            passed = set(group.loc[group[test], "iv_threshold"].astype(float))
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
        return "watchlist", "proxy_defensive_confirmation", selected, gates
    returned = gates[gates["return_platform"]]
    if len(returned):
        selected = str(returned.sort_values("full_ann_delta_pp", ascending=False).iloc[0]["candidate"])
        return "watchlist", "proxy_return_confirmation", selected, gates
    return "keep_default", "real_sample_data_sensitive", None, gates


def make_record(
    meta: dict[str, Any],
    summary: pd.DataFrame,
    stress: pd.DataFrame,
    decision: str,
    stability: str,
    selected: str | None,
    parity: float,
    four_tier_start: pd.Timestamp,
    market_checks: dict[str, Any],
) -> str:
    full = summary[summary["segment"].eq("full")].sort_values("ann_return", ascending=False)
    base = full[full["candidate"].eq(BASELINE)].iloc[0]
    lines = [
        "# IM 高波停Put / 降仓理论代理扩展 v3",
        "",
        "## Run Metadata",
        "",
        f"- Run id: `{meta['run_id']}`",
        "- Date/timezone: 2026-08-25 / Asia/Shanghai",
        "- Evidence layer: theoretical/proxy only; not real IM/MO execution.",
        "",
        "## Research Question",
        "",
        "Test whether the real-sample 35%-40% high-IV Put-gating return phenomenon and high-vol derisk behavior persist across a 2015-2026 proxy history.",
        "",
        "## Implementation Anchor",
        "",
        "- Model market: `im_mo_csi1000_put_protection_battery_v6.model_market`.",
        "- Model Put execution: `im_mo_close_execution_v8.run_model_normal_close`.",
        f"- Hybrid schedule uses full four-tier thresholds from {four_tier_start.date()}; earlier dates use certified reconstructed 0-3 valuation tiers plus MOM120-negative floor4.",
        f"- Baseline assembly parity max absolute error: {parity:.3e}.",
        "",
        "## Data Snapshot",
        "",
        f"- Proxy sample: {base['start']} to {base['end']}; {int(base['rows'])} rows.",
        f"- Model-market checks: `{json.dumps(market_checks, ensure_ascii=False, default=str)}`.",
        "- Direction leg is CSI1000 total-return proxy; Put IV is QVIX scaled by CSI1000/50ETF realized-vol ratio.",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- T proxy-IV signal, T+1 close target, next-session core exposure.",
        "- Theoretical Put and model roll costs plus 1bp normalized core resize cost; 30% margin/buffer and net 3% cash.",
        "- Excludes real IM basis, Call, grid, MO surface, spread, impact, capacity, non-fill, dynamic margin, tax, and integer rounding.",
        "",
        "## Runtime Override Plan",
        "",
        "- Research-only in-memory candidate schedules; no frozen files changed.",
        "",
        "## Commands",
        "",
        "```powershell",
        "python im_high_vol_put_derisk_model_extension_v3.py",
        "```",
        "",
        "## Output Files",
        "",
        "- `scan_summary.csv`, `window_metrics.csv`, `stress_period_metrics.csv`",
        "- `decision_gates.csv`, `parity_checks.csv`, `daily_outputs/`",
        "",
        "## Full-Sample Results",
        "",
        "| candidate | mode | IV | shape | CAGR | Sharpe | MaxDD | worst20d | avg core |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in full.head(15).itertuples(index=False):
        threshold = "-" if pd.isna(row.iv_threshold) else f"{row.iv_threshold:.1%}"
        lines.append(
            f"| {row.candidate} | {row.mode} | {threshold} | {row.shape} | {row.ann_return:.2%} | {row.sharpe_repo:.3f} | {row.max_dd:.2%} | {row.worst_20d:.2%} | {row.avg_weight:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Window Results",
            "",
            "Full/10Y/5Y/3Y/1Y are in the standard CSVs. Preregistered 2015/2018/2020/2022 stress windows are in `stress_period_metrics.csv`.",
            "",
            "## Stability Classification",
            "",
            f"- Label: `{stability}`",
            f"- Selected: `{selected or 'none'}`",
            "- This label can only confirm or reject directional consistency with the short real sample.",
            "",
            "## Decision",
            "",
            f"- Decision: `{decision}`",
            "- No model result can change frozen v2 or authorize live/Poe behavior.",
            "",
            "## User-Facing Summary",
            "",
            f"Decision `{decision}`, stability `{stability}`, selected `{selected or 'none'}`. Theoretical/proxy evidence only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    meta = verify_preregistration()
    market, market_checks = v14.v6.model_market()
    base = v14.v6.model_baseline(market)
    baseline_schedule, threshold_table, four_tier_start = build_hybrid_schedule()
    proxy_signal = build_proxy_iv_signal(baseline_schedule, market)
    baseline_overlay, baseline_trades, _ = v14.v8.run_model_normal_close(
        market, baseline_schedule, "3m", 0.95, BASELINE
    )
    assembled = v14.v6.assemble_layer("model", base, {BASELINE: baseline_overlay})
    assembled_baseline = assembled[assembled["candidate"].eq(BASELINE)].sort_values("date")

    daily_parts: list[pd.DataFrame] = []
    policy_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = [baseline_trades.assign(candidate=BASELINE)]

    _, baseline_policy = build_policy(
        baseline_schedule, proxy_signal, BASELINE, None, "none", BASELINE
    )
    baseline = recompose(
        base, baseline_overlay, baseline_policy, BASELINE, BASELINE, None, "none"
    )
    parity = float((baseline["cash_ret"] - assembled_baseline["cash_ret"].to_numpy()).abs().max())
    if parity > 1e-12:
        raise RuntimeError(f"Model baseline assembly mismatch: {parity}")
    daily_parts.append(baseline)
    policy_parts.append(baseline_policy)

    no_schedule, no_policy = build_policy(
        baseline_schedule, proxy_signal, NO_PUT, None, "none", NO_PUT
    )
    no_overlay, no_trades, _ = v14.v8.run_model_normal_close(
        market, no_schedule, "3m", 0.95, NO_PUT
    )
    daily_parts.append(recompose(base, no_overlay, no_policy, NO_PUT, NO_PUT, None, "none"))
    policy_parts.append(no_policy)
    if len(no_trades):
        trade_parts.append(no_trades)

    for threshold in THRESHOLDS:
        gate_name = candidate_name("gate_only", threshold, "none")
        gate_schedule, gate_policy = build_policy(
            baseline_schedule, proxy_signal, "gate_only", threshold, "none", gate_name
        )
        gate_overlay, gate_trades, _ = v14.v8.run_model_normal_close(
            market, gate_schedule, "3m", 0.95, gate_name
        )
        daily_parts.append(
            recompose(base, gate_overlay, gate_policy, gate_name, "gate_only", threshold, "none")
        )
        policy_parts.append(gate_policy)
        if len(gate_trades):
            trade_parts.append(gate_trades)
        for shape in SHAPES:
            keep_name = candidate_name("derisk_keep_put", threshold, shape)
            keep_schedule, keep_policy = build_policy(
                baseline_schedule,
                proxy_signal,
                "derisk_keep_put",
                threshold,
                shape,
                keep_name,
            )
            if not np.array_equal(
                keep_schedule["binary_target_qty"].to_numpy(),
                baseline_schedule["binary_target_qty"].to_numpy(),
            ):
                raise RuntimeError("Proxy derisk_keep_put changed Put target")
            daily_parts.append(
                recompose(
                    base,
                    baseline_overlay,
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
                baseline_schedule,
                proxy_signal,
                "replace_put_derisk",
                threshold,
                shape,
                replace_name,
            )
            if not np.array_equal(
                replace_schedule["binary_target_qty"].to_numpy(),
                gate_schedule["binary_target_qty"].to_numpy(),
            ):
                raise RuntimeError("Proxy replacement Put target differs from gate-only")
            daily_parts.append(
                recompose(
                    base,
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
    stress = build_stress_metrics(daily)
    decision, stability, selected, gates = evaluate_decision(summary)

    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(exist_ok=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    stress.to_csv(RUN / "stress_period_metrics.csv", index=False, encoding="utf-8-sig")
    gates.to_csv(RUN / "decision_gates.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {
                "check": "model_baseline_assembly_cash_ret",
                "max_abs_error": parity,
                "threshold": 1e-12,
                "pass": parity <= 1e-12,
            }
        ]
    ).to_csv(RUN / "parity_checks.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(daily_dir / "daily_candidates.csv.gz", index=False, compression="gzip")
    policies.to_csv(daily_dir / "policy_audit.csv.gz", index=False, compression="gzip")
    trades.to_csv(daily_dir / "put_trades.csv.gz", index=False, compression="gzip")
    proxy_signal.to_csv(daily_dir / "proxy_iv_signal.csv.gz", index=False, compression="gzip")
    baseline_schedule.to_csv(daily_dir / "hybrid_baseline_schedule.csv.gz", index=False, compression="gzip")
    threshold_table.to_csv(daily_dir / "four_tier_thresholds.csv.gz", index=False, compression="gzip")

    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {
                "candidate": BASELINE,
                "description": "hybrid reconstructed valuation plus MOM120 floor4 proxy model",
                "assembly_parity_max_abs": parity,
                "full_four_tier_start": str(four_tier_start.date()),
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
                "layer": "theoretical_proxy_not_real_im_mo",
                "start": str(market["date"].min().date()),
                "end": str(market["date"].max().date()),
                "rows": len(market),
                "direction": "CSI1000 total return proxy",
                "volatility": "50ETF QVIX scaled by CSI1000/50ETF 60d realized-vol ratio",
                "v14_manifest_sha256": sha256(V14_MANIFEST),
            },
            "cost_model": {
                "performance_margin_per_core": 0.30,
                "cash_annual_net": 0.03,
                "core_resize_one_way": RESIZE_ONE_WAY_COST,
                "excluded": [
                    "real_IM_basis",
                    "grid",
                    "call",
                    "real_MO_surface",
                    "spread",
                    "impact",
                    "capacity",
                    "nonfill",
                    "dynamic_margin",
                    "tax",
                    "integer_rounding",
                ],
            },
            "parity_check": {"pass": True, "max_abs_error": parity, "threshold": 1e-12},
            "outputs": {
                **meta["outputs"],
                "stress_period_metrics": str(RUN / "stress_period_metrics.csv"),
                "decision_gates": str(RUN / "decision_gates.csv"),
                "parity_checks": str(RUN / "parity_checks.csv"),
                "daily": str(daily_dir / "daily_candidates.csv.gz"),
                "policy_audit": str(daily_dir / "policy_audit.csv.gz"),
                "put_trades": str(daily_dir / "put_trades.csv.gz"),
                "proxy_iv_signal": str(daily_dir / "proxy_iv_signal.csv.gz"),
                "hybrid_baseline_schedule": str(daily_dir / "hybrid_baseline_schedule.csv.gz"),
            },
            "decision": decision,
            "stability_label": stability,
            "selected_candidate": selected,
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): sha256(SPEC),
                str(Path(__file__).relative_to(ROOT)): sha256(Path(__file__)),
                str(Path(real_v2.__file__).relative_to(ROOT)): sha256(Path(real_v2.__file__)),
                str(V14_SCHEDULE.relative_to(ROOT)): sha256(V14_SCHEDULE),
                str(V14_MANIFEST.relative_to(ROOT)): sha256(V14_MANIFEST),
            },
            "warnings": [
                "Theoretical/proxy layer; not executable pre-listing IM/MO evidence.",
                "Pre-57-month history uses reconstructed 0-3 valuation tier plus MOM120 floor4.",
                "Grid and Call are excluded from this model extension.",
                "No model result can alter frozen V2 or authorize production/Poe.",
            ],
            "git_status_after": git_value("status", "--short"),
        }
    )
    (RUN / "scan_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (RUN / "record.md").write_text(
        make_record(
            meta,
            summary,
            stress,
            decision,
            stability,
            selected,
            parity,
            four_tier_start,
            market_checks,
        ),
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
                "baseline_parity_max_abs": parity,
                "full_four_tier_start": str(four_tier_start.date()),
                "run": str(RUN),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
