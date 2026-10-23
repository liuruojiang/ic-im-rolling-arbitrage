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


ROOT = Path(__file__).resolve().parent
VERSION = "ic_put_four_valuation_tier_scan_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "a6a2afcad7075796b7949ab27bbbb8dba60099de1b213ca5ee358a835b82801a"
RUN = ROOT / "quant_param_scan_runs" / (
    "20260820_ic_im_rolling_arbitrage_ic_put_four_valuation_tier_scan_v2_"
    "ic_put_valuation_four_tier_ladder"
)
DAILY_DIR = RUN / "daily_outputs"
WINDOWS = v1.WINDOWS
BEIJING = ZoneInfo("Asia/Shanghai")

CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "candidate": "IC_baseline_075",
        "policy": "baseline",
        "thresholds": None,
        "threshold": np.nan,
        "max_target": 0.75,
    },
    {
        "candidate": "IC_existing_top_100",
        "policy": "existing_top",
        "thresholds": (1.90, 2.00, 2.10),
        "threshold": 2.10,
        "max_target": 1.00,
    },
    {
        "candidate": "IC_4tier_1900_2000_2050_2100",
        "policy": "four_tier",
        "thresholds": (1.900, 2.000, 2.050, 2.100),
        "threshold": 2.100,
        "max_target": 1.00,
    },
    {
        "candidate": "IC_4tier_1900_2000_2050_2075",
        "policy": "four_tier",
        "thresholds": (1.900, 2.000, 2.050, 2.075),
        "threshold": 2.075,
        "max_target": 1.00,
    },
    {
        "candidate": "IC_4tier_1900_1975_2050_2075",
        "policy": "four_tier",
        "thresholds": (1.900, 1.975, 2.050, 2.075),
        "threshold": 2.075,
        "max_target": 1.00,
    },
    {
        "candidate": "IC_4tier_1900_1950_2000_2050",
        "policy": "four_tier",
        "thresholds": (1.900, 1.950, 2.000, 2.050),
        "threshold": 2.050,
        "max_target": 1.00,
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


def _tier_from_score(score: pd.Series, thresholds: tuple[float, ...]) -> np.ndarray:
    if len(thresholds) == 3:
        return np.select(
            [score.ge(thresholds[2]), score.ge(thresholds[1]), score.ge(thresholds[0])],
            [3, 2, 1],
            default=0,
        ).astype(int)
    if len(thresholds) == 4:
        return np.select(
            [
                score.ge(thresholds[3]),
                score.ge(thresholds[2]),
                score.ge(thresholds[1]),
                score.ge(thresholds[0]),
            ],
            [4, 3, 2, 1],
            default=0,
        ).astype(int)
    raise ValueError(f"Unsupported threshold count: {len(thresholds)}")


def build_schedule(base: pd.DataFrame, definition: dict[str, Any]) -> pd.DataFrame:
    result = base.copy()
    score = result["unbounded_median_knot"].astype(float)
    original_target = result["target_delta"].astype(float).to_numpy()
    policy = definition["policy"]
    if policy == "baseline":
        target = original_target
        valuation_tier = _tier_from_score(score, (1.90, 2.00, 2.10))
    elif policy == "existing_top":
        valuation_tier = _tier_from_score(score, definition["thresholds"])
        target = np.where(score.ge(2.10), 1.00, original_target)
        valuation_tier = np.where(valuation_tier >= 3, 4, valuation_tier)
    elif policy == "four_tier":
        thresholds = definition["thresholds"]
        if not np.all(np.diff(thresholds) > 0):
            raise RuntimeError(f"Non-increasing thresholds: {definition['candidate']}")
        valuation_tier = _tier_from_score(score, thresholds)
        valuation_target = valuation_tier.astype(float) * 0.25
        momentum_floor = np.where(result["momentum_floor_on"].eq(True).to_numpy(), 0.25, 0.0)
        target = np.maximum(valuation_target, momentum_floor)
    else:
        raise ValueError(f"Unsupported policy: {policy}")
    result["target_delta"] = np.asarray(target, dtype=float)
    result["binary_target_fraction"] = result["target_delta"]
    result["three_tier_target_fraction"] = result["target_delta"]
    result["risk_tier"] = np.rint(result["target_delta"] / 0.25).astype(int)
    result["valuation_tier_new"] = np.asarray(valuation_tier, dtype=int)
    result["signal_variant"] = definition["candidate"]
    result["candidate"] = definition["candidate"]
    result["schedule_candidate"] = definition["candidate"]
    if not result["target_delta"].between(0.0, definition["max_target"]).all():
        raise RuntimeError(f"Invalid target delta: {definition['candidate']}")
    return result


def run_scan() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, float, dict[str, Any]]:
    frames, _daily_valuation, market, market_checks = v1.ic_v20.v19.v18.load_close_inputs()
    roll_dates = v1.ic_v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    base_schedule = pd.read_csv(
        v1.IC_SCHEDULE, parse_dates=["eval_date", "execution_date"], low_memory=False
    )
    base_schedule = base_schedule[
        base_schedule["layer"].eq("real")
        & base_schedule["signal_variant"].eq("l190_mom25")
    ].copy()
    frozen = pd.read_csv(v1.IC_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[frozen["candidate"].eq("real_grid_only")].sort_values("date").copy()

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for definition in CANDIDATES:
        schedule = build_schedule(base_schedule, definition)
        overlay, trades = v1.ic_v20.run_real_delta(
            frames["ic"], schedule, frames, market, definition["candidate"], roll_dates
        )
        daily_parts.append(v1.recompose_ic(frozen, overlay, definition["candidate"]))
        schedule_parts.append(schedule.assign(product="IC"))
        if len(trades):
            trade_parts.append(trades.assign(product="IC"))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    baseline = daily[daily["candidate"].eq("IC_baseline_075")].sort_values("date")
    parity = float(
        np.max(
            np.abs(
                baseline["cash_ret"].to_numpy()
                - frozen.sort_values("date")["cash_ret"].to_numpy()
            )
        )
    )
    if parity > 1e-12:
        raise RuntimeError(f"Frozen IC mainline parity failed: {parity}")
    return daily, schedules, trades, parity, market_checks


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
            rows.append(v1.metric_row(candidate, "IC", segment, sample, definitions[candidate]))
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "product": "IC",
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
        target4 = schedule["target_delta"].ge(1.0 - 1e-12)
        events = target4 & ~target4.shift(fill_value=False)
        held_target4 = group.sort_values("date")["target_delta"].ge(1.0 - 1e-12)
        trade = trades[trades["candidate"].eq(candidate)]
        tier = schedule["valuation_tier_new"].astype(int)
        rows.append(
            {
                "candidate": candidate,
                "target_100_days": int(target4.sum()),
                "target_100_events": int(events.sum()),
                "held_target_100_days": int(held_target4.sum()),
                "tier0_days": int(tier.eq(0).sum()),
                "tier1_days": int(tier.eq(1).sum()),
                "tier2_days": int(tier.eq(2).sum()),
                "tier3_days": int(tier.eq(3).sum()),
                "tier4_days": int(tier.eq(4).sum()),
                "max_put_qty": float(group["put_qty"].max()),
                "max_effective_delta": float(group["effective_delta_hedge_ratio"].max()),
                "max_put_fraction": float(group["actual_notional_fraction"].max()),
                "put_cost_total": float(group["put_cost_rate"].sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "min_cash_weight_raw": float(group["cash_weight_raw"].min()),
                "trade_events": int(len(trade)),
            }
        )
    return pd.DataFrame(rows)


def add_decisions(wide: pd.DataFrame, exposure: pd.DataFrame) -> tuple[pd.DataFrame, str, str]:
    result = wide.merge(exposure, on="candidate", validate="one_to_one")
    base = result[result["candidate"].eq("IC_baseline_075")].iloc[0]
    hints: list[str] = []
    for row in result.itertuples(index=False):
        if row.candidate == "IC_baseline_075":
            hints.append("baseline")
            continue
        trigger_ok = row.target_100_days >= 5 and row.target_100_events >= 2
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
    family_passes = result[
        result["policy"].eq("four_tier")
        & result["decision_hint"].eq("promotion_gate_pass")
    ]
    if len(family_passes) >= 2:
        decision, stability = "carry_ic_four_tier_family_to_research_review", "narrow_stable"
    elif len(family_passes) == 1:
        decision, stability = "keep_baseline_ic_four_tier_peak_only", "peak_only"
    else:
        decision, stability = "keep_baseline_no_ic_four_tier_candidate_passed", "reject"
    return result, decision, stability


def write_artifacts(
    daily: pd.DataFrame,
    schedules: pd.DataFrame,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    wide: pd.DataFrame,
    exposure: pd.DataFrame,
    parity: float,
    market_checks: dict[str, Any],
    decision: str,
    stability: str,
) -> None:
    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules.to_csv(DAILY_DIR / "target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(DAILY_DIR / "put_trades.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    exposure.to_csv(RUN / "exposure_diagnostics.csv", index=False)
    pd.DataFrame([{"product": "IC", "metric": "cash_ret_max_abs", "value": parity}]).to_csv(
        RUN / "parity_checks.csv", index=False
    )

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"primary": "IC_baseline_075"},
            "candidate_grid": [
                {**item, "thresholds": list(item["thresholds"]) if item["thresholds"] else None}
                for item in CANDIDATES
            ],
            "data_snapshot": {
                "real": ["2022-09-19", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "ic": "official real IC path",
                "put": "frozen SSE/Sina 510500 Put histories used by official real path",
                "valuation": "CSI500 PB/ERP/realized-dividend unbounded median knot",
                "adjustment_mode": "index valuation features and official-path futures/options prices",
                "market_checks": market_checks,
            },
            "cost_model": {
                "margin_buffer_per_future_unit": 0.30,
                "cash_annual": 0.03,
                "put_cost": "official inherited IC/510500 Put side cost",
                "execution": "T close signal / T+1 common-session close",
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
                str(v1.IC_SCHEDULE.relative_to(ROOT)): sha256(v1.IC_SCHEDULE),
                str(v1.IC_FROZEN.relative_to(ROOT)): sha256(v1.IC_FROZEN),
                str(Path(v1.ic_v20.__file__).relative_to(ROOT)): sha256(Path(v1.ic_v20.__file__)),
            },
            "cache_write_risk": "none observed; frozen local inputs loaded read-only",
            "warnings": [
                "real option sample only",
                "10y/5y windows clip to real sample start",
                "no independent out-of-sample set",
                "daily close is not closing-auction fill or capacity evidence",
                "ETF option quantity is integer-rounded from execution-date option Delta",
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
            "target_100_days",
            "target_100_events",
            "max_put_qty",
            "max_effective_delta",
            "decision_hint",
        ]
    ]
    record = f"""# IC Put估值四档细分扫描 v2

## Scope

- Objective: 在中证500现有1.90—2.10估值区间内，把IC Put保护由三档细分为四档，最高目标绝对Delta 100%。
- Status: 真实数据研究扫描；冻结主线未修改。
- Observed result: 以下数字均来自本次真实IC/510500 Put运行。

## Code and Data

- Entry point: `ic_put_four_valuation_tier_scan_v2.py`.
- Official execution reused: `{Path(v1.ic_v20.__file__).name}` `run_real_delta`.
- Real sample: 2022-09-19 to 2026-08-14.
- Frozen baseline parity max absolute error: {parity:.3e}.

## Execution and Frictions

- T收盘信号，T+1共同交易日收盘执行；三个月目标期限；95%目标行权价；随IC月换重置。
- 30%期货保证金/缓冲、剩余现金年化3%；继承IC、网格和Put成本；IC不含Call。
- 买卖价差、冲击、容量、涨跌停未成交、动态保证金上调与税费未计入。

## Full Results

```text
{full.to_string(index=False)}
```

## Integrity

- 基准同批重跑并通过逐日一致性。
- 所有估值阈值均在结果产生前冻结；目标Delta与动态取整张数详见`daily_outputs/`。
- 所有窗口及资金占用见`scan_summary.csv`、`window_metrics.csv`和`exposure_diagnostics.csv`。

## Decision

- Decision: `{decision}`.
- Stability: `{stability}`.
- 冻结主线保持不变，等待用户研究审阅。
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -m pytest -q test_ic_put_four_valuation_tier_scan_v2.py\n")
        handle.write("python ic_put_four_valuation_tier_scan_v2.py\n")


def main() -> None:
    verify_preregistration()
    daily, schedules, trades, parity, market_checks = run_scan()
    summary, wide = build_metrics(daily)
    exposure = build_exposure(daily, schedules, trades)
    wide, decision, stability = add_decisions(wide, exposure)
    write_artifacts(
        daily,
        schedules,
        trades,
        summary,
        wide,
        exposure,
        parity,
        market_checks,
        decision,
        stability,
    )
    print(wide.to_json(orient="records", force_ascii=False, indent=2))
    print(json.dumps({"decision": decision, "stability": stability}, ensure_ascii=False))


if __name__ == "__main__":
    main()
