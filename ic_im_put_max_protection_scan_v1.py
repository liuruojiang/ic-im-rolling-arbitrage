from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import ic_510500_put_tiered_notional_delta_v20 as ic_v20
import im_mo_adaptive_valuation_mom120_floor_v12 as im_v12


ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_put_max_protection_scan_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "91833fb24069e8367eb295a671e0b1b6b903e6ea4da3a2d355baa44b31adac97"
RUN = ROOT / "quant_param_scan_runs" / (
    "20260820_ic_im_rolling_arbitrage_ic_im_put_max_protection_scan_v1_"
    "ic_and_im_put_sleeves_ic_max_delta_im_max_put_quantity"
)
DAILY_DIR = RUN / "daily_outputs"

IC_SCHEDULE = (
    ROOT
    / "outputs"
    / "ic_510500_put_mom120_delta_floor_v21"
    / "evaluation_schedule.csv.gz"
)
IC_FROZEN = ROOT / "outputs" / "ic_put_grid_call_combined_v2" / "daily_candidates.csv.gz"
IM_SCHEDULE = (
    ROOT
    / "outputs"
    / "im_mo_adaptive_valuation_mom120_floor_v12"
    / "signal_schedules.csv.gz"
)
IM_FROZEN = ROOT / "outputs" / "im_put_grid_call_final_audit_v1" / "daily_candidates.csv.gz"

CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
BEIJING = ZoneInfo("Asia/Shanghai")

IC_CANDIDATES = (
    {"candidate": "IC_baseline_075", "policy": "baseline", "threshold": np.nan, "max_target": 0.75},
    {"candidate": "IC_top_trigger_100", "policy": "top_trigger", "threshold": 2.10, "max_target": 1.00},
    {"candidate": "IC_add_100_at_215", "policy": "add_tier", "threshold": 2.15, "max_target": 1.00},
    {"candidate": "IC_add_100_at_220", "policy": "add_tier", "threshold": 2.20, "max_target": 1.00},
    {"candidate": "IC_add_100_at_230", "policy": "add_tier", "threshold": 2.30, "max_target": 1.00},
)

IM_CANDIDATES = (
    {"candidate": "IM_baseline_3", "policy": "baseline", "threshold": np.nan, "max_target": 3.0},
    {"candidate": "IM_top_trigger_4", "policy": "top_trigger", "threshold": 3.0, "max_target": 4.0},
    {"candidate": "IM_mom_floor_4", "policy": "mom_floor", "threshold": 0.0, "max_target": 4.0},
    {"candidate": "IM_add_4_abs265_rel99", "policy": "add_tier", "threshold": 2.65, "max_target": 4.0},
    {"candidate": "IM_add_4_abs270_rel99", "policy": "add_tier", "threshold": 2.70, "max_target": 4.0},
    {"candidate": "IM_add_4_abs275_rel99", "policy": "add_tier", "threshold": 2.75, "max_target": 4.0},
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
        raise RuntimeError(
            f"Preregistered specification hash mismatch: {actual} / {sidecar}"
        )
    if not RUN.exists():
        raise FileNotFoundError(f"Initialized parameter-scan folder missing: {RUN}")
    for output in (RUN / "scan_summary.csv", RUN / "window_metrics.csv"):
        if output.exists():
            raise FileExistsError(f"Scan result already exists and will not be overwritten: {output}")


def build_ic_schedule(base: pd.DataFrame, definition: dict[str, Any]) -> pd.DataFrame:
    result = base.copy()
    target = result["target_delta"].astype(float).to_numpy(copy=True)
    policy = definition["policy"]
    if policy == "top_trigger":
        target = np.where(target >= 0.75 - 1e-12, 1.0, target)
    elif policy == "add_tier":
        target = np.where(
            result["unbounded_median_knot"].astype(float).to_numpy()
            >= float(definition["threshold"]) - 1e-12,
            1.0,
            target,
        )
    elif policy != "baseline":
        raise ValueError(f"Unsupported IC policy: {policy}")
    result["target_delta"] = target
    result["binary_target_fraction"] = target
    result["three_tier_target_fraction"] = target
    result["risk_tier"] = np.select(
        [target >= 1.0 - 1e-12, target >= 0.75 - 1e-12, target >= 0.50 - 1e-12, target > 0],
        [4, 3, 2, 1],
        default=0,
    ).astype(int)
    result["signal_variant"] = definition["candidate"]
    return result


def build_im_schedule(
    base: pd.DataFrame,
    definition: dict[str, Any],
    valuation_state: pd.DataFrame,
) -> pd.DataFrame:
    result = base.copy().merge(
        valuation_state,
        left_on="eval_date",
        right_on="date",
        how="left",
        validate="one_to_one",
        suffixes=("", "_state"),
    )
    if result[["score_state", "percentile_state"]].isna().any().any():
        raise RuntimeError("IM valuation state is missing on a scheduled evaluation date")
    target = result["binary_target_qty"].astype(int).to_numpy(copy=True)
    policy = definition["policy"]
    if policy == "top_trigger":
        target = np.where(target >= 3, 4, target)
    elif policy == "mom_floor":
        target = np.maximum(
            result["valuation_tier"].fillna(0).astype(int).to_numpy(),
            np.where(result["mom120_active"].fillna(False).astype(bool), 4, 0),
        )
    elif policy == "add_tier":
        fourth = (
            result["score_state"].astype(float).to_numpy()
            >= float(definition["threshold"]) - 1e-12
        ) | (result["percentile_state"].astype(float).to_numpy() >= 0.99 - 1e-12)
        target = np.where(fourth, 4, target)
    elif policy != "baseline":
        raise ValueError(f"Unsupported IM policy: {policy}")
    result["binary_target_qty"] = target.astype(int)
    result["three_tier_target_qty"] = target.astype(int)
    result["candidate"] = definition["candidate"]
    result["schedule_candidate"] = definition["candidate"]
    return result.drop(columns=["date"])


def recompose_ic(
    frozen: pd.DataFrame, put: pd.DataFrame, candidate: str
) -> pd.DataFrame:
    columns = [
        "date",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_contract",
        "put_qty",
        "target_delta",
        "actual_notional_fraction",
        "abs_put_delta",
        "effective_delta_hedge_ratio",
    ]
    replacement = put[columns].copy()
    frame = frozen.drop(columns=[column for column in columns[1:] if column in frozen]).merge(
        replacement, on="date", how="inner", validate="one_to_one"
    )
    if len(frame) != len(frozen) or len(frame) != len(put):
        raise RuntimeError(f"IC date alignment failed for {candidate}")
    gross = frame["ic_gross_ret"] + frame["overlay_gross_ret"] + frame["put_pnl_ret"]
    frame["ret"] = (
        (1.0 + gross)
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        - 1.0
    )
    frame["cash_weight_raw"] = (
        1.0 - 0.30 * frame["total_ic_units"] - frame["put_mark_fraction"]
    )
    frame["cash_weight"] = frame["cash_weight_raw"].clip(lower=0.0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    frame["candidate"] = candidate
    frame["product"] = "IC"
    return add_nav(frame)


def recompose_im(
    frozen: pd.DataFrame, put: pd.DataFrame, candidate: str
) -> pd.DataFrame:
    columns = [
        "date",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_fraction",
        "put_contract",
    ]
    replacement = put[columns].copy()
    frame = frozen.drop(columns=[column for column in columns[1:] if column in frozen]).merge(
        replacement, on="date", how="inner", validate="one_to_one"
    )
    if len(frame) != len(frozen) or len(frame) != len(put):
        raise RuntimeError(f"IM date alignment failed for {candidate}")
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
    frame["candidate"] = candidate
    frame["product"] = "IM"
    return add_nav(frame)


def add_nav(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.sort_values("date").reset_index(drop=True).copy()
    result["nav"] = (1.0 + result["cash_ret"]).cumprod()
    result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
    if result[["cash_ret", "nav", "drawdown"]].isna().any().any():
        raise RuntimeError(f"Invalid daily values for {result['candidate'].iloc[0]}")
    if (result["cash_ret"] <= -1.0).any():
        raise RuntimeError(f"Daily loss <= -100% for {result['candidate'].iloc[0]}")
    return result


def run_ic() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    frames, _daily_valuation, market, market_checks = ic_v20.v19.v18.load_close_inputs()
    roll_dates = ic_v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    base_schedule = pd.read_csv(IC_SCHEDULE, parse_dates=["eval_date", "execution_date"])
    base_schedule = base_schedule[
        base_schedule["layer"].eq("real")
        & base_schedule["signal_variant"].eq("l190_mom25")
    ].copy()
    frozen = pd.read_csv(IC_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[frozen["candidate"].eq("real_grid_only")].sort_values("date").copy()
    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for definition in IC_CANDIDATES:
        schedule = build_ic_schedule(base_schedule, definition)
        overlay, trades = ic_v20.run_real_delta(
            frames["ic"], schedule, frames, market, definition["candidate"], roll_dates
        )
        daily_parts.append(recompose_ic(frozen, overlay, definition["candidate"]))
        schedule_parts.append(schedule.assign(candidate=definition["candidate"], product="IC"))
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
        raise RuntimeError(f"IC frozen mainline parity failed: {parity}")
    return daily, schedules, trades, {"cash_ret_max_abs": parity, "market_checks": market_checks}


def run_im() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
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
        ["date", "unbounded_median_knot", "rolling_percentile"]
    ].rename(
        columns={
            "unbounded_median_knot": "score_state",
            "rolling_percentile": "percentile_state",
        }
    )
    frozen = pd.read_csv(IM_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("full_put_grid_call")
    ].sort_values("date").copy()
    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for definition in IM_CANDIDATES:
        schedule = build_im_schedule(base_schedule, definition, valuation_state)
        overlay, trades, _lives = im_v12.v8.run_real_normal_close(
            upstream,
            options,
            active_im,
            schedule,
            "3m",
            0.95,
            definition["candidate"],
        )
        daily_parts.append(recompose_im(frozen, overlay, definition["candidate"]))
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
        raise RuntimeError(f"IM frozen mainline parity failed: {parity}")
    return daily, schedules, trades, {"cash_ret_max_abs": parity}


def metric_row(
    candidate: str,
    product: str,
    segment: str,
    sample: pd.DataFrame,
    definition: dict[str, Any],
) -> dict[str, Any]:
    returns = sample["cash_ret"].astype(float)
    rows = len(returns)
    nav = (1.0 + returns).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    std = float(returns.std(ddof=1)) if rows > 1 else 0.0
    return {
        "candidate": candidate,
        "product": product,
        "policy": definition["policy"],
        "threshold": definition["threshold"],
        "max_target": definition["max_target"],
        "segment": segment,
        "start": sample["date"].min().date().isoformat(),
        "end": sample["date"].max().date().isoformat(),
        "rows": rows,
        "ann_return": float(nav.iloc[-1] ** (252.0 / rows) - 1.0),
        "ann_vol": std * np.sqrt(252.0),
        "sharpe_repo": float(returns.mean()) / std * np.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(drawdown.min()),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "max_put_mark_fraction": float(sample["put_mark_fraction"].max()),
        "min_cash_weight_raw": float(sample["cash_weight_raw"].min()),
    }


def build_metrics(
    daily: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    definitions = {
        item["candidate"]: item for item in (*IC_CANDIDATES, *IM_CANDIDATES)
    }
    rows: list[dict[str, Any]] = []
    for (product, candidate), group in daily.groupby(["product", "candidate"], sort=False):
        group = group.sort_values("date")
        end = group["date"].max()
        for segment in WINDOWS:
            if segment == "full":
                start = group["date"].min()
            else:
                years = int(segment.removeprefix("last_").removesuffix("y"))
                start = max(group["date"].min(), end - pd.DateOffset(years=years))
            sample = group[group["date"].ge(start)].copy()
            rows.append(
                metric_row(candidate, product, segment, sample, definitions[candidate])
            )
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "product": first["product"],
            "policy": first["policy"],
            "threshold": first["threshold"],
            "max_target": first["max_target"],
        }
        for item in group.itertuples(index=False):
            row[f"ann_return_{item.segment}"] = item.ann_return
            row[f"ann_vol_{item.segment}"] = item.ann_vol
            row[f"sharpe_repo_{item.segment}"] = item.sharpe_repo
            row[f"max_dd_{item.segment}"] = item.max_dd
        wide_rows.append(row)
    return summary, pd.DataFrame(wide_rows)


def build_exposure(
    daily: pd.DataFrame, schedules: pd.DataFrame, trades: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (product, candidate), group in daily.groupby(["product", "candidate"], sort=False):
        schedule = schedules[schedules["candidate"].eq(candidate)]
        trade = trades[trades["candidate"].eq(candidate)] if len(trades) else trades
        row = {
            "product": product,
            "candidate": candidate,
            "days": len(group),
            "put_cost_total": float(group["put_cost_rate"].sum()),
            "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
            "min_cash_weight_raw": float(group["cash_weight_raw"].min()),
            "trade_events": int(len(trade)),
        }
        if product == "IC":
            row.update(
                {
                    "max_put_qty": float(group["put_qty"].max()),
                    "max_effective_delta": float(group["effective_delta_hedge_ratio"].max()),
                    "target_top_days": int(schedule["target_delta"].ge(1.0 - 1e-12).sum()),
                    "max_put_fraction": float(group["actual_notional_fraction"].max()),
                }
            )
        else:
            row.update(
                {
                    "max_put_qty": float((group["put_fraction"] * 2.0).max()),
                    "max_effective_delta": np.nan,
                    "target_top_days": int(schedule["binary_target_qty"].ge(4).sum()),
                    "max_put_fraction": float(group["put_fraction"].max()),
                }
            )
        rows.append(row)
    return pd.DataFrame(rows)


def add_decision_hints(wide: pd.DataFrame) -> pd.DataFrame:
    result = wide.copy()
    baselines = {
        "IC": result[result["candidate"].eq("IC_baseline_075")].iloc[0],
        "IM": result[result["candidate"].eq("IM_baseline_3")].iloc[0],
    }
    hints = []
    for row in result.itertuples(index=False):
        base = baselines[row.product]
        if row.candidate == base["candidate"]:
            hints.append("baseline")
            continue
        dd_improvement = row.max_dd_full - float(base["max_dd_full"])
        return_delta = row.ann_return_full - float(base["ann_return_full"])
        sharpe_ok = row.sharpe_repo_full >= float(base["sharpe_repo_full"]) - 1e-12
        recent_both_worse = (
            row.ann_return_last_3y < float(base["ann_return_last_3y"])
            and row.max_dd_last_3y < float(base["max_dd_last_3y"])
            and row.ann_return_last_1y < float(base["ann_return_last_1y"])
            and row.max_dd_last_1y < float(base["max_dd_last_1y"])
        )
        passed = (
            dd_improvement >= 0.01 - 1e-12
            and return_delta >= -0.02 - 1e-12
            and sharpe_ok
            and not recent_both_worse
        )
        hints.append("promotion_gate_pass" if passed else "keep_baseline")
    result["decision_hint"] = hints
    return result


def write_artifacts(
    daily: pd.DataFrame,
    schedules: pd.DataFrame,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    wide: pd.DataFrame,
    exposure: pd.DataFrame,
    parity: pd.DataFrame,
) -> None:
    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules.to_csv(DAILY_DIR / "target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(DAILY_DIR / "put_trades.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    exposure.to_csv(RUN / "exposure_diagnostics.csv", index=False)
    parity.to_csv(RUN / "parity_checks.csv", index=False)

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "baseline": {"IC": "IC_baseline_075", "IM": "IM_baseline_3"},
            "candidate_grid": [*IC_CANDIDATES, *IM_CANDIDATES],
            "data_snapshot": {
                "IC": ["2022-09-19", "2026-08-14"],
                "IM": ["2022-07-22", "2026-08-14"],
                "timezone": "Asia/Shanghai",
                "IC_put_source": "frozen SSE/Sina 510500 Put histories used by official real path",
                "IM_put_source": "CFFEX official MO daily open/close/settlement/volume/open-interest",
                "benchmarks": "not used for candidate ranking",
            },
            "cost_model": {
                "margin_buffer_per_future_unit": 0.30,
                "cash_annual": 0.03,
                "put_cost": "inherited official IC/IM Put side costs",
                "futures_grid_call_cost": "inherited frozen mainline costs",
                "execution": "T close signal / T+1 official close for Put; frozen grid and IM Call paths",
                "excluded": [
                    "bid-ask spread",
                    "close impact",
                    "price-limit non-fill",
                    "order-book capacity",
                    "dynamic margin hike",
                    "tax",
                ],
            },
            "parity_check": parity.to_dict("records"),
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): SPEC_SHA256,
                str(IC_SCHEDULE.relative_to(ROOT)): sha256(IC_SCHEDULE),
                str(IC_FROZEN.relative_to(ROOT)): sha256(IC_FROZEN),
                str(IM_SCHEDULE.relative_to(ROOT)): sha256(IM_SCHEDULE),
                str(IM_FROZEN.relative_to(ROOT)): sha256(IM_FROZEN),
                str(Path(ic_v20.__file__).relative_to(ROOT)): sha256(Path(ic_v20.__file__)),
                str(Path(im_v12.__file__).relative_to(ROOT)): sha256(Path(im_v12.__file__)),
            },
            "cache_write_risk": "none observed; frozen local data loaded read-only",
            "warnings": [
                "real-option sample only; no theoretical pre-listing path used",
                "10y/5y windows clip to each product real sample start",
                "daily close is not closing-auction fill or capacity evidence",
                "no independent out-of-sample set",
            ],
        }
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    full = wide[
        [
            "candidate",
            "product",
            "ann_return_full",
            "sharpe_repo_full",
            "max_dd_full",
            "ann_return_last_3y",
            "max_dd_last_3y",
            "ann_return_last_1y",
            "max_dd_last_1y",
            "decision_hint",
        ]
    ]
    record = f"""# IC / IM Put Maximum Protection Scan v1

## Run Metadata

- Run id: `{meta['run_id']}`
- Run timestamp: {datetime.now(BEIJING).isoformat(timespec='seconds')}
- Timezone: Asia/Shanghai
- Project: IC / IM rolling arbitrage
- Scan type: candidate_bundle
- Source-change rule: `research_only_no_source_change`

## Research Question

- IC: test maximum target absolute Delta 0.75 versus 1.00.
- IM: test maximum MO Put quantity 3 versus 4 per fixed core IM.
- Candidate definitions and promotion gates were frozen before execution in `{SPEC.relative_to(ROOT)}`.

## Implementation Anchor

- IC official sizing/execution: `{Path(ic_v20.__file__).name}` `run_real_delta`.
- IM official sizing/execution: `{Path(im_v12.v8.__file__).name}` `run_real_normal_close`.
- Frozen full-system recomposition: IC `real_grid_only`; IM real `full_put_grid_call`.
- Baselines rerun in the same batch and checked daily against frozen mainline returns.

## Data Snapshot

- IC real sample: 2022-09-19 to 2026-08-14.
- IM real sample: 2022-07-22 to 2026-08-14.
- IC data: official real IC path plus frozen SSE/Sina 510500 Put histories.
- IM data: CFFEX official IM/MO daily data.
- Trading calendar and timezone: official common trading dates, Asia/Shanghai.
- No theoretical pre-listing option path is used for the decision.

## Cost and Execution Assumptions

- 30% margin/buffer per future unit and 3% annual cash yield.
- Put T close signal / T+1 official close; monthly reset; 3m target; 95% strike target.
- Frozen futures, grid and IM Call costs retained.
- Bid/ask, close impact, price-limit non-fill, capacity, dynamic margin hike and tax excluded.

## Runtime Override Plan

- New schedules are created in memory; frozen source files are read-only.
- Default candidates are included and rerun in the same batch.
- Daily baseline parity tolerance: 1e-12.

## Commands

```powershell
python -m pytest -q test_ic_im_put_max_protection_scan_v1.py
python ic_im_put_max_protection_scan_v1.py
```

## Output Files

- `scan_summary.csv`
- `window_metrics.csv`
- `exposure_diagnostics.csv`
- `parity_checks.csv`
- `daily_outputs/`

## Full-Sample Results

```text
{full.to_string(index=False)}
```

## Window Results

See `scan_summary.csv` and `window_metrics.csv` for all full/10Y/5Y/3Y/1Y rows.

## Stability Classification

- Pending final review after numeric outputs are inspected.

## Decision

- Pending finalization; `decision_hint` applies the preregistered gates mechanically.

## User-Facing Summary

- Pending final review and strict artifact audit.
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\npython -m pytest -q test_ic_im_put_max_protection_scan_v1.py\n")
        handle.write("python ic_im_put_max_protection_scan_v1.py\n")


def main() -> None:
    verify_preregistration()
    ic_daily, ic_schedules, ic_trades, ic_parity = run_ic()
    im_daily, im_schedules, im_trades, im_parity = run_im()
    daily = pd.concat([ic_daily, im_daily], ignore_index=True, sort=False)
    schedules = pd.concat([ic_schedules, im_schedules], ignore_index=True, sort=False)
    trades = pd.concat([ic_trades, im_trades], ignore_index=True, sort=False)
    summary, wide = build_metrics(daily)
    wide = add_decision_hints(wide)
    exposure = build_exposure(daily, schedules, trades)
    parity = pd.DataFrame(
        [
            {"product": "IC", "metric": "cash_ret_max_abs", "value": ic_parity["cash_ret_max_abs"]},
            {"product": "IM", "metric": "cash_ret_max_abs", "value": im_parity["cash_ret_max_abs"]},
        ]
    )
    write_artifacts(daily, schedules, trades, summary, wide, exposure, parity)
    print(wide.to_json(orient="records", force_ascii=False, indent=2))


if __name__ == "__main__":
    main()
