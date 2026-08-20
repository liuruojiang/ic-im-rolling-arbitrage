from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_im_put_max_protection_scan_v1 as base
import im_put_four_valuation_tier_scan_v2 as im_v2
import im_mo_adaptive_valuation_mom120_floor_v12 as im_v12
import im_valuation_window_ladder_scan_v7 as valuation_v7


ROOT = Path(__file__).resolve().parent
VERSION = "im_put_four_tier_mom120_floor_scan_v3"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "4fa049a07050d2125a8d5f02fbd13098aced4cffe312235b6ea53782382010da"
RUN = ROOT / "quant_param_scan_runs" / (
    "20260820_ic_im_im_put_four_tier_mom120_floor_scan_v3_"
    "im_put_four_tier_mom120_floor_qty"
)
DAILY_DIR = RUN / "daily_outputs"
WINDOWS = base.WINDOWS
SOURCE_CANDIDATE = "IM_4tier_q750_850_900_925"

CANDIDATES: tuple[dict[str, Any], ...] = tuple(
    {
        "candidate": f"IM_4tier_mom_floor_{floor}",
        "policy": "four_tier_mom120_floor",
        "threshold": float(floor),
        "mom_floor_qty": floor,
        "max_target": 4.0,
    }
    for floor in range(5)
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
    for output in (RUN / "scan_summary.csv", RUN / "window_metrics.csv", DAILY_DIR):
        if output.exists():
            raise FileExistsError(f"Formal scan output already exists: {output}")


def load_inputs() -> tuple[
    Any,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    upstream, _a, _b, _c, _d, raw_options = im_v12.v4.load_inputs()
    active_im = im_v12.v8.active_im_closes(upstream)
    expiry_map = im_v12.v4.actual_expiry_map(raw_options, upstream)
    options = im_v12.v4.prepare_options(raw_options, expiry_map)
    source = pd.read_csv(
        base.IM_SCHEDULE,
        parse_dates=["eval_date", "execution_date"],
        low_memory=False,
    )
    source = source[
        source["layer"].eq("real")
        & source["candidate"].eq("valmom_center_floor3")
    ].copy()
    if source["momentum_120"].astype(float).eq(0.0).any():
        raise RuntimeError("MOM120==0 exists; strict negative boundary requires review")
    states = im_v12.v10.load_v7_states()
    valuation_state = states[states["candidate"].eq("dual_w57_q750_850_950")][
        ["date", "unbounded_median_knot", "rolling_percentile", "absolute_tier"]
    ].copy()
    monthly = valuation_v7.load_inputs()["monthly"]
    thresholds = im_v2.build_thresholds(monthly, source["eval_date"])
    thresholds = thresholds[thresholds["candidate"].eq(SOURCE_CANDIDATE)].copy()
    frozen = pd.read_csv(base.IM_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("full_put_grid_call")
    ].sort_values("date").copy()
    return upstream, active_im, options, source, valuation_state, thresholds, frozen


def build_schedule(
    source: pd.DataFrame,
    valuation_state: pd.DataFrame,
    thresholds: pd.DataFrame,
    definition: dict[str, Any],
) -> pd.DataFrame:
    source_definition = next(
        item for item in im_v2.CANDIDATES if item["candidate"] == SOURCE_CANDIDATE
    )
    schedule = im_v2.build_schedule(
        source, source_definition, valuation_state, thresholds
    )
    negative = schedule["momentum_120"].astype(float).lt(0.0)
    active = schedule["mom120_active"].fillna(False).astype(bool)
    if not active.equals(negative):
        raise RuntimeError("Stored IM momentum state does not equal strict MOM120 < 0")
    valuation_target = schedule["new_valuation_tier"].astype(int).to_numpy()
    floor = int(definition["mom_floor_qty"])
    momentum_target = np.where(negative.to_numpy(), floor, 0)
    target = np.maximum(valuation_target, momentum_target).astype(int)
    schedule["binary_target_qty"] = target
    schedule["three_tier_target_qty"] = target
    schedule["mom120_floor_qty_new"] = momentum_target.astype(int)
    schedule["mom_negative"] = negative
    schedule["mom_floor_binding"] = negative & (momentum_target > valuation_target)
    schedule["candidate"] = definition["candidate"]
    schedule["schedule_candidate"] = definition["candidate"]
    if not set(schedule["binary_target_qty"].unique()).issubset({0, 1, 2, 3, 4}):
        raise RuntimeError(f"Invalid target for {definition['candidate']}")
    return schedule


def run_scan() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, float]:
    upstream, active_im, options, source, valuation_state, thresholds, frozen = load_inputs()

    baseline_definition = next(
        item for item in im_v2.CANDIDATES if item["candidate"] == "IM_baseline_3"
    )
    baseline_schedule = im_v2.build_schedule(
        source, baseline_definition, valuation_state, thresholds
    )
    baseline_overlay, _baseline_trades, _lives = im_v12.v8.run_real_normal_close(
        upstream, options, active_im, baseline_schedule, "3m", 0.95, "IM_baseline_3"
    )
    baseline_daily = base.recompose_im(frozen, baseline_overlay, "IM_baseline_3")
    parity = float(
        np.max(
            np.abs(
                baseline_daily.sort_values("date")["cash_ret"].to_numpy()
                - frozen["cash_ret"].to_numpy()
            )
        )
    )
    if parity > 1e-12:
        raise RuntimeError(f"Frozen IM baseline parity failed: {parity}")

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for definition in CANDIDATES:
        schedule = build_schedule(source, valuation_state, thresholds, definition)
        overlay, trades, _lives = im_v12.v8.run_real_normal_close(
            upstream,
            options,
            active_im,
            schedule,
            "3m",
            0.95,
            definition["candidate"],
        )
        daily_parts.append(base.recompose_im(frozen, overlay, definition["candidate"]))
        schedule_parts.append(schedule.assign(product="IM"))
        if len(trades):
            trade_parts.append(trades.assign(product="IM"))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    thresholds = thresholds.assign(candidate="IM_four_tier_floor_family")
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
            rows.append(base.metric_row(candidate, "IM", segment, sample, definitions[candidate]))
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        definition = definitions[candidate]
        row: dict[str, Any] = {
            "candidate": candidate,
            "product": "IM",
            "policy": definition["policy"],
            "mom_floor_qty": definition["mom_floor_qty"],
            "threshold": definition["threshold"],
            "max_target": definition["max_target"],
        }
        for item in group.itertuples(index=False):
            for metric in ("ann_return", "ann_vol", "sharpe_repo", "max_dd"):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return summary, pd.DataFrame(wide_rows)


def build_exposure(
    daily: pd.DataFrame, schedules: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        schedule = schedules[schedules["candidate"].eq(candidate)].sort_values("eval_date")
        trade = trades[trades["candidate"].eq(candidate)].copy()
        target4 = schedule["binary_target_qty"].astype(int).eq(4)
        target4_events = target4 & ~target4.shift(fill_value=False)
        held_qty = group["put_fraction"].astype(float) * 2.0
        operational = (
            0.15 * group["total_im_units"].astype(float)
            + group["put_mark_fraction"].fillna(0.0).astype(float)
            + group["call_margin_fraction"].fillna(0.0).astype(float)
        )
        actual_positions = (
            trade.assign(actual_execution_date=pd.to_datetime(trade["actual_execution_date"]))
            .sort_values("actual_execution_date")
            .groupby("actual_execution_date", sort=True)["new_qty"]
            .last()
        )
        expected = (
            actual_positions.reindex(group["date"])
            .ffill()
            .fillna(0.0)
            .to_numpy(dtype=float)
        )
        if not np.allclose(held_qty.to_numpy(dtype=float), expected, atol=1e-12):
            raise RuntimeError(f"Actual put holdings do not match trades: {candidate}")
        rows.append(
            {
                "candidate": candidate,
                "mom_floor_qty": int(schedule["mom120_floor_qty_new"].max()),
                "mom_negative_days": int(schedule["mom_negative"].sum()),
                "mom_floor_binding_days": int(schedule["mom_floor_binding"].sum()),
                "target_4_days": int(target4.sum()),
                "target_4_events": int(target4_events.sum()),
                "held_4_days": int(held_qty.eq(4.0).sum()),
                "put_cost_total": float(group["put_cost_rate"].sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "min_cash_weight_raw": float(group["cash_weight_raw"].min()),
                "max_operational_eod_capital_15pct": float(operational.max()),
                "trade_events": int(len(trade)),
                "actual_execution_early_errors": int(
                    (
                        pd.to_datetime(trade["actual_execution_date"])
                        < pd.to_datetime(trade["scheduled_execution_date"])
                    ).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def add_comparisons(wide: pd.DataFrame, exposure: pd.DataFrame) -> pd.DataFrame:
    result = wide.merge(exposure, on=["candidate", "mom_floor_qty"], validate="one_to_one")
    reference = result[result["mom_floor_qty"].eq(3)].iloc[0]
    for metric in ("ann_return", "sharpe_repo", "max_dd"):
        for window in ("full", "last_5y", "last_3y", "last_1y"):
            column = f"{metric}_{window}"
            result[f"{column}_vs_floor3"] = result[column] - float(reference[column])
    result["decision_hint"] = np.where(
        result["mom_floor_qty"].eq(3), "reference_floor3", "context"
    )
    return result


def write_artifacts(
    daily: pd.DataFrame,
    schedules: pd.DataFrame,
    trades: pd.DataFrame,
    thresholds: pd.DataFrame,
    summary: pd.DataFrame,
    wide: pd.DataFrame,
    exposure: pd.DataFrame,
    parity: float,
) -> None:
    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules.to_csv(DAILY_DIR / "target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(DAILY_DIR / "put_trades.csv.gz", index=False, compression="gzip")
    thresholds.to_csv(DAILY_DIR / "rolling_thresholds.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    exposure.to_csv(RUN / "exposure_diagnostics.csv", index=False)
    pd.DataFrame(
        [{"product": "IM", "metric": "v1_cash_ret_max_abs", "value": parity}]
    ).to_csv(RUN / "parity_checks.csv", index=False)

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "one_parameter_sweep",
            "baseline": {"primary": "IM_4tier_mom_floor_3"},
            "candidate_grid": CANDIDATES,
            "data_snapshot": {
                "real": ["2022-07-22", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "im_mo": "CFFEX official daily IM/MO data inherited from official path",
                "valuation_window_months": 57,
                "valuation_quantiles": [0.75, 0.85, 0.90, 0.925],
            },
            "cost_model": {
                "performance_margin_buffer_per_future_unit": 0.30,
                "operational_margin_user_upper_bound": 0.15,
                "cash_annual": 0.03,
                "execution": "T close signal / T+1 official close",
                "put_cost": "official inherited per-side MO cost",
                "excluded": [
                    "bid-ask spread", "close impact", "price-limit non-fill",
                    "order-book capacity", "dynamic margin hike", "tax",
                ],
            },
            "parity_check": {"cash_ret_max_abs": parity, "tolerance": 1e-12},
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): SPEC_SHA256,
                str(base.IM_SCHEDULE.relative_to(ROOT)): sha256(base.IM_SCHEDULE),
                str(base.IM_FROZEN.relative_to(ROOT)): sha256(base.IM_FROZEN),
                str(Path(im_v12.v8.__file__).relative_to(ROOT)): sha256(Path(im_v12.v8.__file__)),
                str(Path(valuation_v7.__file__).relative_to(ROOT)): sha256(Path(valuation_v7.__file__)),
            },
            "cache_write_risk": "none observed; frozen local inputs loaded read-only",
            "warnings": [
                "real option sample only",
                "10y/5y windows clip to real sample start",
                "no independent out-of-sample set",
                "daily close is not closing-auction fill or capacity evidence",
                "15% operational margin is user-provided and not independently verified",
            ],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    selected_columns = [
        "mom_floor_qty", "ann_return_full", "sharpe_repo_full", "max_dd_full",
        "ann_return_last_3y", "sharpe_repo_last_3y", "max_dd_last_3y",
        "ann_return_last_1y", "sharpe_repo_last_1y", "max_dd_last_1y",
        "mom_floor_binding_days", "target_4_days", "put_cost_total",
        "max_operational_eod_capital_15pct",
    ]
    record = f"""# IM 四档估值下 MOM120 负动量 Put 下限扫描 v3

## Scope

- Objective: 在固定四档估值规则下比较负动量最低0/1/2/3/4张MO Put。
- Status: 真实数据参数扫描；正式主线与POE暂不更新。
- Real sample: 2022-07-22 to 2026-08-14.

## Execution and Frictions

- T收盘评估、T+1官方收盘执行；真实挂牌MO链；3个月目标期限；95%目标行权价；月度重置。
- 绩效使用30%期货保证金/缓冲和剩余现金年化3%；15%只用于用户提供的操作资金上限。
- 未包含买卖价差、收盘冲击、容量、涨跌停未成交、动态保证金上调与税费。

## Results

```text
{wide[selected_columns].sort_values('mom_floor_qty').to_string(index=False)}
```

## Integrity

- 冻结v1基线逐日复算最大绝对误差：{parity:.3e}。
- 滚动阈值仅使用生效月以前57个月末数据。
- Put逐日持仓按真实成交事件核对；网格与Call组件继承冻结路径。

## Decision

- Decision: `pending_research_judgment`.
- Stability: `pending`.
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -m pytest -q test_im_put_four_tier_mom120_floor_scan_v3.py\n")
        handle.write("python im_put_four_tier_mom120_floor_scan_v3.py\n")


def main() -> None:
    verify_preregistration()
    daily, schedules, trades, thresholds, parity = run_scan()
    summary, wide = build_metrics(daily)
    exposure = build_exposure(daily, schedules, trades)
    wide = add_comparisons(wide, exposure)
    write_artifacts(daily, schedules, trades, thresholds, summary, wide, exposure, parity)
    print(wide.sort_values("mom_floor_qty").to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()

