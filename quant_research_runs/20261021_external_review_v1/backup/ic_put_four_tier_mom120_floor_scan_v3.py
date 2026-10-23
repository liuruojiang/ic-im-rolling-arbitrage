from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_im_put_max_protection_scan_v1 as v1
import ic_put_four_valuation_tier_scan_v2 as v2


ROOT = Path(__file__).resolve().parent
VERSION = "ic_put_four_tier_mom120_floor_scan_v3"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "0b806c2b88f0006b772353a2cc1f899d55b1a50b089b2693fc8c1dea3c4fec96"
RUN = ROOT / "quant_param_scan_runs" / (
    "20260820_ic_im_rolling_arbitrage_ic_put_four_tier_mom120_floor_scan_v3_"
    "ic_put_four_tier_mom120_delta_floor"
)
DAILY_DIR = RUN / "daily_outputs"
WINDOWS = v1.WINDOWS

FAMILIES: dict[str, tuple[float, float, float, float]] = {
    "cons4": (1.90, 2.00, 2.05, 2.10),
    "wide4": (1.90, 1.95, 2.00, 2.05),
}
FLOORS = (0.00, 0.25, 0.50, 0.75, 1.00)


def candidate_definitions() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = [
        {
            "candidate": "IC_frozen_3tier_mom025",
            "policy": "frozen_baseline",
            "family": "frozen_3tier",
            "thresholds": None,
            "mom_floor": 0.25,
            "threshold": 0.25,
            "max_target": 0.75,
        }
    ]
    for family, thresholds in FAMILIES.items():
        for floor in FLOORS:
            rows.append(
                {
                    "candidate": f"IC_{family}_mom{int(round(floor * 100)):03d}",
                    "policy": "four_tier_mom_floor",
                    "family": family,
                    "thresholds": thresholds,
                    "mom_floor": floor,
                    "threshold": floor,
                    "max_target": 1.00,
                }
            )
    return tuple(rows)


CANDIDATES = candidate_definitions()


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


def build_schedule(base: pd.DataFrame, definition: dict[str, Any]) -> pd.DataFrame:
    result = base.copy()
    if definition["policy"] == "frozen_baseline":
        result["valuation_tier_new"] = v2._tier_from_score(
            result["unbounded_median_knot"].astype(float), (1.90, 2.00, 2.10)
        )
        result["mom_negative"] = result["momentum_120"].astype(float).lt(0.0)
        result["mom_floor_binding"] = (
            result["mom_negative"] & result["target_delta"].astype(float).eq(0.25)
        )
    else:
        score = result["unbounded_median_knot"].astype(float)
        valuation_tier = v2._tier_from_score(score, definition["thresholds"])
        valuation_target = valuation_tier.astype(float) * 0.25
        mom_negative = result["momentum_120"].astype(float).lt(0.0).to_numpy()
        floor = float(definition["mom_floor"])
        momentum_target = np.where(mom_negative, floor, 0.0)
        target = np.maximum(valuation_target, momentum_target)
        result["target_delta"] = target
        result["binary_target_fraction"] = target
        result["three_tier_target_fraction"] = target
        result["risk_tier"] = np.rint(target / 0.25).astype(int)
        result["valuation_tier_new"] = valuation_tier
        result["mom_negative"] = mom_negative
        result["mom_floor_binding"] = mom_negative & (momentum_target > valuation_target + 1e-12)
    result["signal_variant"] = definition["candidate"]
    result["candidate"] = definition["candidate"]
    result["schedule_candidate"] = definition["candidate"]
    if not result["target_delta"].between(0.0, definition["max_target"]).all():
        raise RuntimeError(f"Invalid target Delta: {definition['candidate']}")
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
    if int(base_schedule["momentum_120"].astype(float).eq(0.0).sum()) != 0:
        raise RuntimeError("MOM120==0 exists; strict <0 requires separate boundary treatment")
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
    baseline = daily[daily["candidate"].eq("IC_frozen_3tier_mom025")].sort_values("date")
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
            row = v1.metric_row(candidate, "IC", segment, sample, definitions[candidate])
            row["family"] = definitions[candidate]["family"]
            row["mom_floor"] = definitions[candidate]["mom_floor"]
            rows.append(row)
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "product": "IC",
            "policy": first["policy"],
            "family": first["family"],
            "mom_floor": first["mom_floor"],
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
        target100 = schedule["target_delta"].ge(1.0 - 1e-12)
        target100_events = target100 & ~target100.shift(fill_value=False)
        trade = trades[trades["candidate"].eq(candidate)]
        rows.append(
            {
                "candidate": candidate,
                "mom_negative_days": int(schedule["mom_negative"].sum()),
                "mom_floor_binding_days": int(schedule["mom_floor_binding"].sum()),
                "target_100_days": int(target100.sum()),
                "target_100_events": int(target100_events.sum()),
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
    result["ann_return_vs_mom025_pp"] = np.nan
    result["max_dd_vs_mom025_pp"] = np.nan
    result["sharpe_vs_mom025"] = np.nan
    result["decision_hint"] = "context"
    common_pass_floors: set[float] | None = None
    for family in FAMILIES:
        part = result[result["family"].eq(family)]
        base = part[part["mom_floor"].eq(0.25)].iloc[0]
        passed_floors: set[float] = set()
        for idx, row in part.iterrows():
            result.loc[idx, "ann_return_vs_mom025_pp"] = (
                row["ann_return_full"] - base["ann_return_full"]
            ) * 100.0
            result.loc[idx, "max_dd_vs_mom025_pp"] = (
                row["max_dd_full"] - base["max_dd_full"]
            ) * 100.0
            result.loc[idx, "sharpe_vs_mom025"] = (
                row["sharpe_repo_full"] - base["sharpe_repo_full"]
            )
            floor = float(row["mom_floor"])
            if abs(floor - 0.25) < 1e-12:
                result.loc[idx, "decision_hint"] = "mom025_reference"
                continue
            recent_both_worse = (
                row["ann_return_last_3y"] < base["ann_return_last_3y"]
                and row["max_dd_last_3y"] < base["max_dd_last_3y"]
                and row["ann_return_last_1y"] < base["ann_return_last_1y"]
                and row["max_dd_last_1y"] < base["max_dd_last_1y"]
            )
            if floor == 0.0:
                passed = (
                    row["max_dd_full"] >= base["max_dd_full"] - 0.005 - 1e-12
                    and row["ann_return_full"] >= base["ann_return_full"] - 1e-12
                    and row["sharpe_repo_full"] >= base["sharpe_repo_full"] - 1e-12
                    and not recent_both_worse
                    and row["min_cash_weight_raw"] >= -1e-12
                )
            else:
                passed = (
                    row["max_dd_full"] >= base["max_dd_full"] + 0.01 - 1e-12
                    and row["ann_return_full"] >= base["ann_return_full"] - 0.02 - 1e-12
                    and row["sharpe_repo_full"] >= base["sharpe_repo_full"] - 1e-12
                    and not recent_both_worse
                    and row["min_cash_weight_raw"] >= -1e-12
                )
            result.loc[idx, "decision_hint"] = (
                "replacement_gate_pass" if passed else "retain_mom025"
            )
            if passed:
                passed_floors.add(floor)
        common_pass_floors = (
            passed_floors
            if common_pass_floors is None
            else common_pass_floors.intersection(passed_floors)
        )
    common_pass_floors = common_pass_floors or set()
    if common_pass_floors:
        values = ",".join(f"{value:.2f}" for value in sorted(common_pass_floors))
        decision = f"carry_common_mom_floor_alternatives_{values}_to_review"
        stability = "narrow_stable"
    else:
        decision = "retain_mom120_floor_025_under_ic_four_tier"
        stability = "wide_stable"
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
            "scan_type": "grid",
            "baseline": {
                "frozen": "IC_frozen_3tier_mom025",
                "within_family": "mom_floor_0.25",
            },
            "candidate_grid": [
                {**item, "thresholds": list(item["thresholds"]) if item["thresholds"] else None}
                for item in CANDIDATES
            ],
            "data_snapshot": {
                "real": ["2022-09-19", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "mom_negative_days": 445,
                "mom_equal_zero_days": 0,
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
                "target Delta is not a hard cap on realized effective Delta",
                "bid-ask, close impact and capacity excluded",
            ],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    table = wide[wide["family"].isin(FAMILIES)][
        [
            "candidate",
            "family",
            "mom_floor",
            "ann_return_full",
            "sharpe_repo_full",
            "max_dd_full",
            "ann_return_last_1y",
            "max_dd_last_1y",
            "mom_floor_binding_days",
            "max_effective_delta",
            "ann_return_vs_mom025_pp",
            "max_dd_vs_mom025_pp",
            "decision_hint",
        ]
    ]
    record = f"""# IC Put四档估值下MOM120保底Delta扫描 v3

## Scope

- Objective: 比较MOM120<0时0%/25%/50%/75%/100%最低目标Delta。
- Four-tier families: conservative 1.90/2.00/2.05/2.10 and wide 1.90/1.95/2.00/2.05.
- Observed result: 以下数字来自真实IC/510500 Put回测。

## Data and Execution

- Real sample: 2022-09-19 to 2026-08-14; MOM120<0 days: 445; MOM120==0 days: 0.
- T收盘信号，T+1共同交易日收盘执行；三个月目标期限；95%目标行权价；随IC月换重置。
- Frozen baseline parity max absolute error: {parity:.3e}.
- 30%期货保证金/缓冲、剩余现金年化3%；继承IC、网格和Put成本；IC不含Call。
- 买卖价差、冲击、容量、涨跌停未成交、动态保证金上调和税费未计入。

## Results

```text
{table.to_string(index=False)}
```

## Decision

- Decision: `{decision}`.
- Stability: `{stability}`.
- 冻结主线及v2首次正式输出均未修改。
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -m pytest -q test_ic_put_four_tier_mom120_floor_scan_v3.py\n")
        handle.write("python ic_put_four_tier_mom120_floor_scan_v3.py\n")


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
