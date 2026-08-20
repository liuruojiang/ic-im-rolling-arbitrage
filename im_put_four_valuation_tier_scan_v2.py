from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import ic_im_put_max_protection_scan_v1 as v1
import im_mo_adaptive_valuation_mom120_floor_v12 as im_v12
import im_valuation_window_ladder_scan_v7 as valuation_v7


ROOT = Path(__file__).resolve().parent
VERSION = "im_put_four_valuation_tier_scan_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "32d9dd31c64a3417f3b6c12e64e42cfce697e0dc7b0bb5695037dce21f196a21"
RUN = ROOT / "quant_param_scan_runs" / (
    "20260820_ic_im_rolling_arbitrage_im_put_four_valuation_tier_scan_v2_"
    "im_put_valuation_four_tier_ladder"
)
DAILY_DIR = RUN / "daily_outputs"

IM_SCHEDULE = v1.IM_SCHEDULE
IM_FROZEN = v1.IM_FROZEN
WINDOWS = v1.WINDOWS
BEIJING = ZoneInfo("Asia/Shanghai")
WINDOW_MONTHS = 57

CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "candidate": "IM_baseline_3",
        "policy": "baseline",
        "quantiles": None,
        "threshold": np.nan,
        "max_target": 3.0,
    },
    {
        "candidate": "IM_existing_val_top_4",
        "policy": "existing_val_top",
        "quantiles": None,
        "threshold": 0.95,
        "max_target": 4.0,
    },
    {
        "candidate": "IM_4tier_q750_850_900_950",
        "policy": "rolling_four_tier",
        "quantiles": (0.750, 0.850, 0.900, 0.950),
        "threshold": 0.950,
        "max_target": 4.0,
    },
    {
        "candidate": "IM_4tier_q750_850_900_925",
        "policy": "rolling_four_tier",
        "quantiles": (0.750, 0.850, 0.900, 0.925),
        "threshold": 0.925,
        "max_target": 4.0,
    },
    {
        "candidate": "IM_4tier_q725_825_875_925",
        "policy": "rolling_four_tier",
        "quantiles": (0.725, 0.825, 0.875, 0.925),
        "threshold": 0.925,
        "max_target": 4.0,
    },
    {
        "candidate": "IM_4tier_q700_800_875_925",
        "policy": "rolling_four_tier",
        "quantiles": (0.700, 0.800, 0.875, 0.925),
        "threshold": 0.925,
        "max_target": 4.0,
    },
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_preregistration() -> None:
    actual = sha256(SPEC)
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if actual != SPEC_SHA256 or sidecar != SPEC_SHA256:
        raise RuntimeError(f"Preregistered specification hash mismatch: {actual} / {sidecar}")
    if not RUN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {RUN}")
    for output in (RUN / "scan_summary.csv", RUN / "window_metrics.csv"):
        if output.exists():
            raise FileExistsError(f"Formal output already exists: {output}")


def build_thresholds(
    monthly: pd.DataFrame, eval_dates: pd.Series
) -> pd.DataFrame:
    ladders = [item for item in CANDIDATES if item["quantiles"] is not None]
    months = pd.Series(pd.to_datetime(eval_dates)).dt.to_period("M").dt.to_timestamp().unique()
    ordered = monthly.sort_values("date")
    rows: list[dict[str, Any]] = []
    for month_value in sorted(months):
        month = pd.Timestamp(month_value)
        sample = ordered[ordered["date"].lt(month)].tail(WINDOW_MONTHS)
        if len(sample) != WINDOW_MONTHS:
            raise RuntimeError(f"Insufficient causal valuation history for {month.date()}: {len(sample)}")
        if sample["date"].max() >= month:
            raise RuntimeError(f"Future valuation row used for {month.date()}")
        values = sample["unbounded_median_knot"].astype(float).to_numpy()
        for definition in ladders:
            thresholds = np.quantile(values, definition["quantiles"], method="linear")
            if not np.all(np.diff(thresholds) > 0):
                raise RuntimeError(f"Non-increasing thresholds: {definition['candidate']} / {month}")
            rows.append(
                {
                    "candidate": definition["candidate"],
                    "effective_month": month,
                    "sample_months": len(sample),
                    "window_start": sample["date"].min(),
                    "window_end": sample["date"].max(),
                    "max_input_date": sample["date"].max(),
                    "threshold_1_new": float(thresholds[0]),
                    "threshold_2_new": float(thresholds[1]),
                    "threshold_3_new": float(thresholds[2]),
                    "threshold_4_new": float(thresholds[3]),
                }
            )
    return pd.DataFrame(rows)


def build_schedule(
    base: pd.DataFrame,
    definition: dict[str, Any],
    valuation_state: pd.DataFrame,
    thresholds: pd.DataFrame,
) -> pd.DataFrame:
    state = valuation_state.rename(
        columns={
            "unbounded_median_knot": "score_state",
            "rolling_percentile": "percentile_state",
            "absolute_tier": "absolute_tier_state",
        }
    )
    result = base.copy().merge(
        state,
        left_on="eval_date",
        right_on="date",
        how="left",
        validate="one_to_one",
    )
    if result[["score_state", "absolute_tier_state"]].isna().any().any():
        raise RuntimeError("Missing valuation state on scheduled evaluation date")
    result["effective_month"] = result["eval_date"].dt.to_period("M").dt.to_timestamp()
    original_target = result["binary_target_qty"].astype(int).to_numpy()
    policy = definition["policy"]
    if policy == "baseline":
        target = original_target
        result["new_relative_tier"] = np.nan
        result["new_valuation_tier"] = result["valuation_tier"].astype(int)
    elif policy == "existing_val_top":
        target = np.where(result["valuation_tier"].astype(int).to_numpy() >= 3, 4, original_target)
        result["new_relative_tier"] = np.nan
        result["new_valuation_tier"] = np.where(
            result["valuation_tier"].astype(int).to_numpy() >= 3,
            4,
            result["valuation_tier"].astype(int).to_numpy(),
        )
    elif policy == "rolling_four_tier":
        selected = thresholds[thresholds["candidate"].eq(definition["candidate"])].drop(
            columns="candidate"
        )
        result = result.merge(selected, on="effective_month", how="left", validate="many_to_one")
        threshold_columns = [f"threshold_{idx}_new" for idx in range(1, 5)]
        if result[threshold_columns].isna().any().any():
            raise RuntimeError(f"Missing four-tier thresholds: {definition['candidate']}")
        score = result["score_state"].astype(float)
        relative = np.select(
            [
                score.ge(result["threshold_4_new"]),
                score.ge(result["threshold_3_new"]),
                score.ge(result["threshold_2_new"]),
                score.ge(result["threshold_1_new"]),
            ],
            [4, 3, 2, 1],
            default=0,
        ).astype(int)
        valuation_target = np.maximum(
            result["absolute_tier_state"].astype(int).to_numpy(), relative
        )
        momentum_floor = np.where(
            result["mom120_active"].fillna(False).astype(bool).to_numpy(), 3, 0
        )
        target = np.maximum(valuation_target, momentum_floor)
        result["new_relative_tier"] = relative
        result["new_valuation_tier"] = valuation_target
    else:
        raise ValueError(f"Unsupported policy: {policy}")
    result["binary_target_qty"] = np.asarray(target, dtype=int)
    result["three_tier_target_qty"] = np.asarray(target, dtype=int)
    result["candidate"] = definition["candidate"]
    result["schedule_candidate"] = definition["candidate"]
    return result.drop(columns="date")


def run_scan() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, float]:
    upstream, _a, _b, _c, _d, raw_options = im_v12.v4.load_inputs()
    active_im = im_v12.v8.active_im_closes(upstream)
    expiry_map = im_v12.v4.actual_expiry_map(raw_options, upstream)
    options = im_v12.v4.prepare_options(raw_options, expiry_map)
    base_schedule = pd.read_csv(IM_SCHEDULE, parse_dates=["eval_date", "execution_date"])
    base_schedule = base_schedule[
        base_schedule["layer"].eq("real")
        & base_schedule["candidate"].eq("valmom_center_floor3")
    ].copy()
    states = im_v12.v10.load_v7_states()
    valuation_state = states[states["candidate"].eq("dual_w57_q750_850_950")][
        ["date", "unbounded_median_knot", "rolling_percentile", "absolute_tier"]
    ].copy()
    valuation_inputs = valuation_v7.load_inputs()
    thresholds = build_thresholds(valuation_inputs["monthly"], base_schedule["eval_date"])
    frozen = pd.read_csv(IM_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("full_put_grid_call")
    ].sort_values("date").copy()

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for definition in CANDIDATES:
        schedule = build_schedule(base_schedule, definition, valuation_state, thresholds)
        overlay, trades, _lives = im_v12.v8.run_real_normal_close(
            upstream, options, active_im, schedule, "3m", 0.95, definition["candidate"]
        )
        daily_parts.append(v1.recompose_im(frozen, overlay, definition["candidate"]))
        schedule_parts.append(schedule.assign(product="IM"))
        if len(trades):
            trade_parts.append(trades.assign(product="IM"))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    baseline = daily[daily["candidate"].eq("IM_baseline_3")].sort_values("date")
    parity = float(
        np.max(
            np.abs(
                baseline["cash_ret"].to_numpy()
                - frozen.sort_values("date")["cash_ret"].to_numpy()
            )
        )
    )
    if parity > 1e-12:
        raise RuntimeError(f"Frozen IM mainline parity failed: {parity}")
    return daily, schedules, trades, thresholds, parity


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    definitions = {item["candidate"]: item for item in CANDIDATES}
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        end = group["date"].max()
        for segment in WINDOWS:
            if segment == "full":
                start = group["date"].min()
            else:
                years = int(segment.removeprefix("last_").removesuffix("y"))
                start = max(group["date"].min(), end - pd.DateOffset(years=years))
            sample = group[group["date"].ge(start)].copy()
            rows.append(v1.metric_row(candidate, "IM", segment, sample, definitions[candidate]))
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "product": "IM",
            "policy": first["policy"],
            "threshold": first["threshold"],
            "max_target": first["max_target"],
        }
        for item in group.itertuples(index=False):
            for metric in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return summary, pd.DataFrame(wide_rows)


def build_exposure(daily: pd.DataFrame, schedules: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        schedule = schedules[schedules["candidate"].eq(candidate)].sort_values("eval_date")
        target4 = schedule["binary_target_qty"].astype(int).eq(4)
        events = target4 & ~target4.shift(fill_value=False)
        actual_qty = group.sort_values("date")["put_fraction"].astype(float) * 2.0
        trade = trades[trades["candidate"].eq(candidate)]
        tier = schedule["new_valuation_tier"].fillna(-1).astype(int)
        rows.append(
            {
                "candidate": candidate,
                "target_4_days": int(target4.sum()),
                "target_4_events": int(events.sum()),
                "held_4_days": int(actual_qty.eq(4.0).sum()),
                "tier0_days": int(tier.eq(0).sum()),
                "tier1_days": int(tier.eq(1).sum()),
                "tier2_days": int(tier.eq(2).sum()),
                "tier3_days": int(tier.eq(3).sum()),
                "tier4_days": int(tier.eq(4).sum()),
                "put_cost_total": float(group["put_cost_rate"].sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "min_cash_weight_raw": float(group["cash_weight_raw"].min()),
                "trade_events": int(len(trade)),
            }
        )
    return pd.DataFrame(rows)


def add_decisions(wide: pd.DataFrame, exposure: pd.DataFrame) -> tuple[pd.DataFrame, str, str]:
    result = wide.merge(exposure, on="candidate", validate="one_to_one")
    base = result[result["candidate"].eq("IM_baseline_3")].iloc[0]
    hints: list[str] = []
    for row in result.itertuples(index=False):
        if row.candidate == "IM_baseline_3":
            hints.append("baseline")
            continue
        trigger_ok = row.target_4_days >= 5 and row.target_4_events >= 2
        recent_both_worse = (
            row.ann_return_last_3y < base.ann_return_last_3y
            and row.max_dd_last_3y < base.max_dd_last_3y
            and row.ann_return_last_1y < base.ann_return_last_1y
            and row.max_dd_last_1y < base.max_dd_last_1y
        )
        passed = (
            trigger_ok
            and row.max_dd_full >= base.max_dd_full - 1e-12
            and row.ann_return_full >= base.ann_return_full - 0.02 - 1e-12
            and row.sharpe_repo_full >= base.sharpe_repo_full - 1e-12
            and not recent_both_worse
            and row.min_cash_weight_raw >= -1e-12
        )
        hints.append("promotion_gate_pass" if passed else "keep_baseline")
    result["decision_hint"] = hints
    four_tier_passes = result[
        result["policy"].eq("rolling_four_tier")
        & result["decision_hint"].eq("promotion_gate_pass")
    ]
    if len(four_tier_passes) >= 2:
        decision, stability = "carry_four_tier_family_to_research_review", "narrow_stable"
    elif len(four_tier_passes) == 1:
        decision, stability = "keep_baseline_four_tier_peak_only", "peak_only"
    else:
        decision, stability = "keep_baseline_no_four_tier_candidate_passed", "reject"
    return result, decision, stability


def write_artifacts(
    daily: pd.DataFrame,
    schedules: pd.DataFrame,
    trades: pd.DataFrame,
    thresholds: pd.DataFrame,
    summary: pd.DataFrame,
    wide: pd.DataFrame,
    exposure: pd.DataFrame,
    parity: float,
    decision: str,
    stability: str,
) -> None:
    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules.to_csv(DAILY_DIR / "target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(DAILY_DIR / "put_trades.csv.gz", index=False, compression="gzip")
    thresholds.to_csv(DAILY_DIR / "rolling_thresholds.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    exposure.to_csv(RUN / "exposure_diagnostics.csv", index=False)
    pd.DataFrame([{"product": "IM", "metric": "cash_ret_max_abs", "value": parity}]).to_csv(
        RUN / "parity_checks.csv", index=False
    )

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"primary": "IM_baseline_3"},
            "candidate_grid": [
                {**item, "quantiles": list(item["quantiles"]) if item["quantiles"] else None}
                for item in CANDIDATES
            ],
            "data_snapshot": {
                "real": ["2022-07-22", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "im_mo": "CFFEX official daily IM/MO data inherited from official path",
                "valuation": str(valuation_v7.V3_OUTPUT.relative_to(ROOT)),
                "valuation_window_months": WINDOW_MONTHS,
                "adjustment_mode": "index valuation features and raw official futures/options daily prices",
            },
            "cost_model": {
                "margin_buffer_per_future_unit": 0.30,
                "cash_annual": 0.03,
                "put_cost": "official inherited per-side MO cost",
                "execution": "T close signal / T+1 official close",
                "excluded": [
                    "bid-ask spread",
                    "close impact",
                    "price-limit non-fill",
                    "order-book capacity",
                    "dynamic margin hike",
                    "tax",
                ],
            },
            "parity_check": {"cash_ret_max_abs": parity, "tolerance": 1e-12},
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): SPEC_SHA256,
                str(IM_SCHEDULE.relative_to(ROOT)): sha256(IM_SCHEDULE),
                str(IM_FROZEN.relative_to(ROOT)): sha256(IM_FROZEN),
                str(Path(im_v12.v8.__file__).relative_to(ROOT)): sha256(Path(im_v12.v8.__file__)),
                str(Path(valuation_v7.__file__).relative_to(ROOT)): sha256(Path(valuation_v7.__file__)),
            },
            "cache_write_risk": "none observed; frozen local inputs loaded read-only",
            "warnings": [
                "real option sample only",
                "10y/5y windows clip to real sample start",
                "no independent out-of-sample set",
                "daily close is not closing-auction fill or capacity evidence",
            ],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    full = wide[
        [
            "candidate",
            "ann_return_full",
            "sharpe_repo_full",
            "max_dd_full",
            "ann_return_last_3y",
            "max_dd_last_3y",
            "ann_return_last_1y",
            "max_dd_last_1y",
            "target_4_days",
            "target_4_events",
            "held_4_days",
            "decision_hint",
        ]
    ]
    record = f"""# IM Put估值四档细分扫描 v2

## Scope

- Objective: 在现有57个月滚动估值分布内，把IM Put估值保护由三档细分为四档。
- Status: 真实数据研究扫描；不修改冻结主线。
- Observed result: 以下数字均来自本次真实IM/MO运行。

## Code and Data

- Entry point: `im_put_four_valuation_tier_scan_v2.py`.
- Official execution reused: `{Path(im_v12.v8.__file__).name}` `run_real_normal_close`.
- Real sample: 2022-07-22 to 2026-08-14.
- Valuation thresholds: effective month以前57个月末，线性分位数；未来行使用数为0。
- Frozen baseline parity max absolute error: {parity:.3e}.

## Execution and Frictions

- T收盘信号，T+1官方收盘执行；三个月目标期限；95%目标行权价；月度重置。
- 30%期货保证金/缓冲、剩余现金年化3%；继承Put、期货、网格与Call成本。
- 买卖价差、冲击、容量、涨跌停未成交、动态保证金上调与税费未计入。

## Full Results

```text
{full.to_string(index=False)}
```

## Integrity

- 基准同批重跑并通过逐日一致性。
- 新阈值仅使用生效月以前数据；详细阈值见`daily_outputs/rolling_thresholds.csv.gz`。
- 所有窗口及资金占用见`scan_summary.csv`、`window_metrics.csv`和`exposure_diagnostics.csv`。

## Decision

- Decision: `{decision}`.
- Stability: `{stability}`.
- 冻结主线保持不变，等待用户研究审阅。
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -m pytest -q test_im_put_four_valuation_tier_scan_v2.py\n")
        handle.write("python im_put_four_valuation_tier_scan_v2.py\n")


def main() -> None:
    verify_preregistration()
    daily, schedules, trades, thresholds, parity = run_scan()
    summary, wide = build_metrics(daily)
    exposure = build_exposure(daily, schedules, trades)
    wide, decision, stability = add_decisions(wide, exposure)
    write_artifacts(
        daily,
        schedules,
        trades,
        thresholds,
        summary,
        wide,
        exposure,
        parity,
        decision,
        stability,
    )
    print(wide.to_json(orient="records", force_ascii=False, indent=2))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
