from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_im_put_max_protection_scan_v1 as base
import ic_put_four_tier_mom120_floor_scan_v3 as ic_v3
import im_put_four_valuation_tier_scan_v2 as im_v2
import im_mo_adaptive_valuation_mom120_floor_v12 as im_v12
import im_valuation_window_ladder_scan_v7 as valuation_v7


ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_system_mainlines_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_SIDECAR = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_HASH = "244dc8774d099433f125ebdb4940c8fc26c70368f4818f7e81f29240ed6ecc7d"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

IC_SELECTED = "IC_wide4_mom050"
IC_BASELINE = "IC_frozen_3tier_mom025"
IM_SELECTED = "IM_4tier_q750_850_900_925_mom4"
IM_BASELINE = "IM_baseline_3"
WINDOWS = base.WINDOWS

IC_DEFINITION: dict[str, Any] = {
    "candidate": IC_SELECTED,
    "policy": "four_tier_mom_floor",
    "family": "wide4",
    "thresholds": (1.90, 1.95, 2.00, 2.05),
    "mom_floor": 0.50,
    "threshold": 0.50,
    "max_target": 1.00,
}
IC_BASELINE_DEFINITION = next(
    item for item in ic_v3.CANDIDATES if item["candidate"] == IC_BASELINE
)
IM_DEFINITION: dict[str, Any] = {
    "candidate": IM_SELECTED,
    "policy": "rolling_four_tier_mom4",
    "quantiles": (0.750, 0.850, 0.900, 0.925),
    "threshold": 0.925,
    "max_target": 4.0,
}
IM_THRESHOLD_SOURCE = "IM_4tier_q750_850_900_925"
IM_BASELINE_DEFINITION = next(
    item for item in im_v2.CANDIDATES if item["candidate"] == IM_BASELINE
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_preregistration() -> None:
    actual = sha256(SPEC)
    sidecar = SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower()
    if actual != SPEC_HASH or sidecar != SPEC_HASH:
        raise RuntimeError(f"Preregistered specification hash mismatch: {actual} / {sidecar}")
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError("Formal v2 mainline output or staging folder already exists")


def run_ic() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, float, dict[str, Any]]:
    frames, _daily_valuation, market, market_checks = base.ic_v20.v19.v18.load_close_inputs()
    roll_dates = base.ic_v20.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    source = pd.read_csv(
        base.IC_SCHEDULE, parse_dates=["eval_date", "execution_date"], low_memory=False
    )
    source = source[
        source["layer"].eq("real") & source["signal_variant"].eq("l190_mom25")
    ].copy()
    if source["momentum_120"].astype(float).eq(0.0).any():
        raise RuntimeError("IC MOM120==0 boundary exists; strict negative rule requires review")
    frozen = pd.read_csv(base.IC_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[frozen["candidate"].eq("real_grid_only")].sort_values("date").copy()

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for definition in (IC_BASELINE_DEFINITION, IC_DEFINITION):
        schedule = ic_v3.build_schedule(source, definition)
        overlay, trades = base.ic_v20.run_real_delta(
            frames["ic"], schedule, frames, market, definition["candidate"], roll_dates
        )
        daily_parts.append(base.recompose_ic(frozen, overlay, definition["candidate"]))
        schedule_parts.append(schedule.assign(product="IC"))
        if len(trades):
            trade_parts.append(trades.assign(product="IC"))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    baseline = daily[daily["candidate"].eq(IC_BASELINE)].sort_values("date")
    parity = float(
        np.max(np.abs(baseline["cash_ret"].to_numpy() - frozen["cash_ret"].to_numpy()))
    )
    return daily, schedules, trades, parity, market_checks


def _im_source_data() -> tuple[
    Any, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame
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
        raise RuntimeError("IM MOM120==0 boundary exists; strict negative rule requires review")
    states = im_v12.v10.load_v7_states()
    valuation_state = states[states["candidate"].eq("dual_w57_q750_850_950")][
        ["date", "unbounded_median_knot", "rolling_percentile", "absolute_tier"]
    ].copy()
    monthly = valuation_v7.load_inputs()["monthly"]
    thresholds = im_v2.build_thresholds(monthly, source["eval_date"])
    thresholds = thresholds[thresholds["candidate"].eq(IM_THRESHOLD_SOURCE)].copy()
    frozen = pd.read_csv(base.IM_FROZEN, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("full_put_grid_call")
    ].sort_values("date").copy()
    return upstream, active_im, options, source, valuation_state, thresholds, frozen


def build_im_selected_schedule(
    source: pd.DataFrame, valuation_state: pd.DataFrame, thresholds: pd.DataFrame
) -> pd.DataFrame:
    source_definition = next(
        item for item in im_v2.CANDIDATES if item["candidate"] == IM_THRESHOLD_SOURCE
    )
    schedule = im_v2.build_schedule(source, source_definition, valuation_state, thresholds)
    negative = schedule["momentum_120"].astype(float).lt(0.0)
    active = schedule["mom120_active"].fillna(False).astype(bool)
    if not active.equals(negative):
        raise RuntimeError("IM stored MOM120 state does not match strict MOM120 < 0")
    valuation_target = schedule["new_valuation_tier"].astype(int).to_numpy()
    target = np.maximum(valuation_target, np.where(negative.to_numpy(), 4, 0))
    schedule["binary_target_qty"] = target.astype(int)
    schedule["three_tier_target_qty"] = target.astype(int)
    schedule["mom120_floor_qty_new"] = np.where(negative, 4, 0).astype(int)
    schedule["mom_floor_binding"] = negative & (
        schedule["new_valuation_tier"].astype(int) < 4
    )
    schedule["candidate"] = IM_SELECTED
    schedule["schedule_candidate"] = IM_SELECTED
    return schedule


def run_im() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, float]:
    upstream, active_im, options, source, valuation_state, thresholds, frozen = _im_source_data()
    baseline = im_v2.build_schedule(
        source, IM_BASELINE_DEFINITION, valuation_state, thresholds
    )
    selected = build_im_selected_schedule(source, valuation_state, thresholds)

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    for schedule, candidate in ((baseline, IM_BASELINE), (selected, IM_SELECTED)):
        overlay, trades, _lives = im_v12.v8.run_real_normal_close(
            upstream, options, active_im, schedule, "3m", 0.95, candidate
        )
        daily_parts.append(base.recompose_im(frozen, overlay, candidate))
        schedule_parts.append(schedule.assign(product="IM"))
        if len(trades):
            trade_parts.append(trades.assign(product="IM"))
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    recalculated = daily[daily["candidate"].eq(IM_BASELINE)].sort_values("date")
    parity = float(
        np.max(np.abs(recalculated["cash_ret"].to_numpy() - frozen["cash_ret"].to_numpy()))
    )
    thresholds = thresholds.assign(candidate=IM_SELECTED)
    return daily, schedules, trades, thresholds, parity


def build_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    definitions = {IC_SELECTED: IC_DEFINITION, IM_SELECTED: IM_DEFINITION}
    rows: list[dict[str, Any]] = []
    selected = daily[daily["candidate"].isin(definitions)].copy()
    for (product, candidate), group in selected.groupby(["product", "candidate"], sort=False):
        group = group.sort_values("date")
        end = group["date"].max()
        for window in WINDOWS:
            if window == "full":
                start = group["date"].min()
            else:
                years = int(window.removeprefix("last_").removesuffix("y"))
                start = max(group["date"].min(), end - pd.DateOffset(years=years))
            sample = group[group["date"].ge(start)].copy()
            row = base.metric_row(candidate, product, window, sample, definitions[candidate])
            row["window"] = row.pop("segment")
            rows.append(row)
    return pd.DataFrame(rows)


def _max_abs_difference(left: pd.DataFrame, right: pd.DataFrame, columns: list[str]) -> float:
    a = left.sort_values("date")[columns].fillna(0.0).to_numpy(dtype=float)
    b = right.sort_values("date")[columns].fillna(0.0).to_numpy(dtype=float)
    return float(np.max(np.abs(a - b)))


def build_integrity(
    ic_daily: pd.DataFrame,
    ic_schedules: pd.DataFrame,
    im_daily: pd.DataFrame,
    im_schedules: pd.DataFrame,
    im_trades: pd.DataFrame,
    thresholds: pd.DataFrame,
    ic_parity: float,
    im_parity: float,
) -> tuple[dict[str, Any], pd.DataFrame]:
    ic_selected = ic_daily[ic_daily["candidate"].eq(IC_SELECTED)].sort_values("date")
    im_selected = im_daily[im_daily["candidate"].eq(IM_SELECTED)].sort_values("date")
    ic_frozen = pd.read_csv(base.IC_FROZEN, parse_dates=["date"], low_memory=False)
    ic_frozen = ic_frozen[ic_frozen["candidate"].eq("real_grid_only")].sort_values("date")
    im_frozen = pd.read_csv(base.IM_FROZEN, parse_dates=["date"], low_memory=False)
    im_frozen = im_frozen[
        im_frozen["layer"].eq("real") & im_frozen["candidate"].eq("full_put_grid_call")
    ].sort_values("date")

    ic_schedule = ic_schedules[ic_schedules["candidate"].eq(IC_SELECTED)].copy()
    im_schedule = im_schedules[im_schedules["candidate"].eq(IM_SELECTED)].copy()
    im_trade = im_trades[im_trades["candidate"].eq(IM_SELECTED)].copy()
    ic_negative = ic_schedule["momentum_120"].astype(float).lt(0.0)
    im_negative = im_schedule["momentum_120"].astype(float).lt(0.0)
    ic_allowed = {0.0, 0.25, 0.50, 0.75, 1.0}
    im_allowed = {0, 1, 2, 3, 4}
    ic_call_cols = [
        "call_pnl_ret", "call_cost_rate", "call_mark_fraction",
        "call_margin_fraction", "call_coverage",
    ]
    ic_call_abs = float(ic_selected[ic_call_cols].fillna(0.0).abs().to_numpy().max())
    ic_grid_error = _max_abs_difference(
        ic_selected, ic_frozen,
        ["ic_gross_ret", "overlay_gross_ret", "futures_cost_rate", "total_ic_units"],
    )
    im_component_error = _max_abs_difference(
        im_selected, im_frozen,
        [
            "base_gross_ret", "overlay_gross_ret", "call_pnl_ret",
            "futures_cost_rate", "call_cost_rate", "total_im_units",
            "call_mark_fraction", "call_margin_fraction", "call_coverage",
        ],
    )
    operational = pd.DataFrame(
        {
            "date": im_selected["date"],
            "total_im_units": im_selected["total_im_units"],
            "operational_futures_margin_15pct": 0.15 * im_selected["total_im_units"],
            "put_mark_fraction": im_selected["put_mark_fraction"],
            "call_margin_fraction": im_selected["call_margin_fraction"],
        }
    )
    operational["operational_eod_capital_15pct"] = (
        operational["operational_futures_margin_15pct"]
        + operational["put_mark_fraction"].fillna(0.0)
        + operational["call_margin_fraction"].fillna(0.0)
    )
    threshold_causal = bool(
        (pd.to_datetime(thresholds["max_input_date"])
         < pd.to_datetime(thresholds["effective_month"])).all()
    )
    actual_positions = (
        im_trade.assign(actual_execution_date=pd.to_datetime(im_trade["actual_execution_date"]))
        .sort_values("actual_execution_date")
        .groupby("actual_execution_date", sort=True)["new_qty"]
        .last()
    )
    expected_fraction = (
        actual_positions.reindex(im_selected["date"])
        .ffill()
        .fillna(0.0)
        .to_numpy(dtype=float)
        * 0.5
    )
    checks = {
        "ic_v1_baseline_parity": ic_parity <= 1e-12,
        "im_v1_baseline_parity": im_parity <= 1e-12,
        "ic_four_tier_targets_only": set(ic_schedule["target_delta"].astype(float).unique()).issubset(ic_allowed),
        "ic_mom_negative_floor_50": bool((ic_schedule.loc[ic_negative, "target_delta"] >= 0.50 - 1e-12).all()),
        "ic_target_cap_100": bool((ic_schedule["target_delta"] <= 1.0 + 1e-12).all()),
        "ic_t_plus_1_causal": bool((ic_schedule["execution_date"] > ic_schedule["eval_date"]).all()),
        "ic_call_fields_zero": ic_call_abs == 0.0,
        "ic_has_call_false": int(ic_selected["has_call"].fillna(False).astype(bool).sum()) == 0,
        "ic_grid_component_unchanged": ic_grid_error <= 1e-12,
        "im_four_tier_targets_only": set(im_schedule["binary_target_qty"].astype(int).unique()).issubset(im_allowed),
        "im_mom_negative_floor_4": bool((im_schedule.loc[im_negative, "binary_target_qty"] == 4).all()),
        "im_target_cap_4": bool((im_schedule["binary_target_qty"] <= 4).all()),
        "im_t_plus_1_causal": bool((im_schedule["execution_date"] > im_schedule["eval_date"]).all()),
        "im_thresholds_strictly_causal": threshold_causal,
        "im_grid_and_call_components_unchanged": im_component_error <= 1e-12,
        "im_put_fraction_matches_actual_trades": bool(np.allclose(
            im_selected["put_fraction"].astype(float).to_numpy(),
            expected_fraction,
            atol=1e-12,
        )),
        "im_actual_trade_qty_matches_target": bool(
            np.allclose(
                im_trade["new_qty"].fillna(0.0).astype(float),
                im_trade["target_qty"].fillna(0.0).astype(float),
                atol=1e-12,
            )
        ),
        "im_actual_execution_not_before_schedule": bool(
            (
                pd.to_datetime(im_trade["actual_execution_date"])
                >= pd.to_datetime(im_trade["scheduled_execution_date"])
            ).all()
        ),
        "im_operational_eod_15pct_below_100": float(operational["operational_eod_capital_15pct"].max()) <= 1.0 + 1e-12,
        "research_only_not_live_approved": True,
    }
    diagnostics = {
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "ic_v1_baseline_cash_ret_max_abs": ic_parity,
        "im_v1_baseline_cash_ret_max_abs": im_parity,
        "ic_call_economic_fields_max_abs": ic_call_abs,
        "ic_grid_component_max_abs": ic_grid_error,
        "im_grid_call_component_max_abs": im_component_error,
        "im_operational_eod_15pct_max": float(operational["operational_eod_capital_15pct"].max()),
    }
    if not diagnostics["all_checks_passed"]:
        raise RuntimeError(f"V2 mainline integrity failed: {diagnostics}")
    return diagnostics, operational


def build_current_state(
    daily: pd.DataFrame, schedules: pd.DataFrame, thresholds: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for product, candidate in (("IC", IC_SELECTED), ("IM", IM_SELECTED)):
        day = daily[daily["candidate"].eq(candidate)].sort_values("date").iloc[-1]
        schedule = schedules[schedules["candidate"].eq(candidate)].sort_values("execution_date").iloc[-1]
        row: dict[str, Any] = {
            "product": product,
            "data_date": day["date"],
            "signal_eval_date": schedule["eval_date"],
            "signal_execution_date": schedule["execution_date"],
            "valuation_score": float(
                schedule["unbounded_median_knot"] if product == "IC" else schedule["score_state"]
            ),
            "momentum_120": float(schedule["momentum_120"]),
            "put_contract": day["put_contract"],
        }
        if product == "IC":
            row.update(
                target=float(schedule["target_delta"]),
                target_unit="absolute_delta",
                put_qty=int(day["put_qty"]),
                valuation_tier=int(schedule["valuation_tier_new"]),
            )
        else:
            current_thresholds = thresholds.sort_values("effective_month").iloc[-1]
            row.update(
                target=int(schedule["binary_target_qty"]),
                target_unit="put_contracts_per_core_im",
                put_qty=int(round(float(day["put_fraction"]) / 0.5)),
                valuation_tier=int(schedule["new_valuation_tier"]),
                relative_tier=int(schedule["new_relative_tier"]),
                threshold_1=float(current_thresholds["threshold_1_new"]),
                threshold_2=float(current_thresholds["threshold_2_new"]),
                threshold_3=float(current_thresholds["threshold_3_new"]),
                threshold_4=float(current_thresholds["threshold_4_new"]),
            )
        rows.append(row)
    return pd.DataFrame(rows)


def write_outputs(
    daily: pd.DataFrame,
    schedules: pd.DataFrame,
    trades: pd.DataFrame,
    thresholds: pd.DataFrame,
    metrics: pd.DataFrame,
    current: pd.DataFrame,
    integrity: dict[str, Any],
    operational: pd.DataFrame,
    source_hashes: dict[str, str],
) -> None:
    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules.to_csv(STAGING / "target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(STAGING / "put_trades.csv.gz", index=False, compression="gzip")
    thresholds.to_csv(STAGING / "im_rolling_thresholds.csv.gz", index=False, compression="gzip")
    metrics.to_csv(STAGING / "mainline_metrics.csv", index=False, encoding="utf-8-sig")
    current.to_csv(STAGING / "current_state.csv", index=False, encoding="utf-8-sig")
    operational.to_csv(
        STAGING / "im_operational_capital_15pct.csv", index=False, encoding="utf-8-sig"
    )
    (STAGING / "integrity_checks.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    state = {
        "version": VERSION,
        "ic": {
            "put_valuation_thresholds": [1.90, 1.95, 2.00, 2.05],
            "put_target_delta": [0.25, 0.50, 0.75, 1.00],
            "mom120_negative_floor_delta": 0.50,
            "grid": {"entry": 0.375, "exit": 1.000, "max_additional_units": 1},
            "call": "excluded",
        },
        "im": {
            "put_absolute_thresholds": [2.45, 2.50, 2.60],
            "put_relative_quantiles": [0.75, 0.85, 0.90, 0.925],
            "relative_window_months": 57,
            "mom120_negative_floor_puts": 4,
            "max_puts_per_core_im": 4,
            "grid": {"entry": 0.85, "exit": 1.25, "max_additional_units": 1},
            "call": "v1_daily_d10_iv26_threat5_core_only",
            "rescue_expiry": "rescue_next_listed",
        },
        "performance_margin_buffer": 0.30,
        "operational_margin_user_upper_bound": 0.15,
        "operational_margin_independently_verified": False,
        "live_approved": False,
    }
    (STAGING / "mainline_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    data_manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_HASH,
        "source_hashes": source_hashes,
        "data_end": str(pd.to_datetime(daily["date"]).max().date()),
        "live_approved": False,
    }
    (STAGING / "data_manifest.json").write_text(
        json.dumps(data_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ic_full = metrics[(metrics["product"].eq("IC")) & metrics["window"].eq("full")].iloc[0]
    im_full = metrics[(metrics["product"].eq("IM")) & metrics["window"].eq("full")].iloc[0]
    ic_now = current[current["product"].eq("IC")].iloc[0]
    im_now = current[current["product"].eq("IM")].iloc[0]
    record = f"""# 滚 IC / IM 系统研究主线冻结 v2

状态：`mainlines_v2_frozen_research_only`；未批准实盘。

## 最终规则

- IC：估值 `1.90/1.95/2.00/2.05` 对应 `25%/50%/75%/100%`目标绝对Delta；`MOM120<0`最低50%；网格不变；不卖Call。
- IM：57个月滚动相对估值 `75%/85%/90%/92.5%` 对应1/2/3/4张，与绝对估值轴取较大值；`MOM120<0`最低4张；网格和Call不变。
- IM救援期限仍为`rescue_next_listed`。

## 真实历史回测

- IC全样本CAGR/Sharpe/MaxDD：{float(ic_full['ann_return']):.2%} / {float(ic_full['sharpe_repo']):.3f} / {float(ic_full['max_dd']):.2%}。
- IM全样本CAGR/Sharpe/MaxDD：{float(im_full['ann_return']):.2%} / {float(im_full['sharpe_repo']):.3f} / {float(im_full['max_dd']):.2%}。
- 收益按每1倍期货30%保证金/风险缓冲核算；15%只用于用户提供的操作资金上限复核。

## 截至正式数据日的状态

- IC：MOM120 {float(ic_now['momentum_120']):.2%}，目标Delta {float(ic_now['target']):.0%}，{int(ic_now['put_qty'])}张 `{ic_now['put_contract']}`。
- IM：MOM120 {float(im_now['momentum_120']):.2%}，目标{int(im_now['target'])}张，当前{int(im_now['put_qty'])}张 `{im_now['put_contract']}`。

## 完整性

- v1基线日收益复算误差：IC {integrity['ic_v1_baseline_cash_ret_max_abs']:.3e}；IM {integrity['im_v1_baseline_cash_ret_max_abs']:.3e}。
- IC网格组件误差：{integrity['ic_grid_component_max_abs']:.3e}；IM网格与Call组件误差：{integrity['im_grid_call_component_max_abs']:.3e}。
- 全部{len(integrity['checks'])}项强制检查通过。
"""
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(
        "python freeze_ic_im_system_mainlines_v2.py\n", encoding="utf-8"
    )
    files = sorted(path for path in STAGING.iterdir() if path.name != "output_manifest.json")
    manifest = {
        path.name: {"size": path.stat().st_size, "sha256": sha256(path)} for path in files
    }
    (STAGING / "output_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(STAGING, OUTPUT)


def main() -> None:
    verify_preregistration()
    source_files = [
        SPEC,
        base.IC_SCHEDULE,
        base.IC_FROZEN,
        base.IM_SCHEDULE,
        base.IM_FROZEN,
        Path(ic_v3.__file__),
        Path(im_v2.__file__),
    ]
    source_hashes = {
        str(path.relative_to(ROOT)): sha256(path) for path in source_files
    }
    ic_daily, ic_schedules, ic_trades, ic_parity, market_checks = run_ic()
    im_daily, im_schedules, im_trades, thresholds, im_parity = run_im()
    daily = pd.concat(
        [
            ic_daily[ic_daily["candidate"].eq(IC_SELECTED)],
            im_daily[im_daily["candidate"].eq(IM_SELECTED)],
        ],
        ignore_index=True,
        sort=False,
    )
    schedules = pd.concat(
        [
            ic_schedules[ic_schedules["candidate"].eq(IC_SELECTED)],
            im_schedules[im_schedules["candidate"].eq(IM_SELECTED)],
        ],
        ignore_index=True,
        sort=False,
    )
    trades = pd.concat(
        [
            ic_trades[ic_trades["candidate"].eq(IC_SELECTED)],
            im_trades[im_trades["candidate"].eq(IM_SELECTED)],
        ],
        ignore_index=True,
        sort=False,
    )
    integrity, operational = build_integrity(
        ic_daily, ic_schedules, im_daily, im_schedules, im_trades,
        thresholds, ic_parity, im_parity
    )
    integrity["ic_market_checks"] = {
        key: (
            {"rows": len(value), "columns": list(value.columns)}
            if isinstance(value, pd.DataFrame)
            else (
                value.item()
                if isinstance(value, np.generic)
                else value
            )
        )
        for key, value in market_checks.items()
    }
    metrics = build_metrics(daily)
    current = build_current_state(daily, schedules, thresholds)
    write_outputs(
        daily, schedules, trades, thresholds, metrics, current,
        integrity, operational, source_hashes,
    )
    print(json.dumps({
        "version": VERSION,
        "output": str(OUTPUT),
        "all_checks_passed": integrity["all_checks_passed"],
        "current_state": current.to_dict(orient="records"),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
