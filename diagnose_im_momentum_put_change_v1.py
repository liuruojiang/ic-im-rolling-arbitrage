from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import scan_im_v13_put_coverage_scope_v1 as scope_v1


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "im_momentum_put_change_diagnosis_v1"
OLD_DAILY = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v4" / "daily_nav.csv.gz"
NEW_SCAN_DAILY = (
    ROOT
    / "quant_param_scan_runs"
    / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_execution_timing_put_coverage_scope_timing"
    / "daily_outputs"
    / "coverage_candidates.csv.gz"
)
REAL_START = pd.Timestamp("2022-07-22")


def metrics(ret: pd.Series) -> dict[str, float]:
    ret = ret.astype(float)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1))
    return {
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0),
        "ann_vol": std * math.sqrt(252.0),
        "sharpe": float(ret.mean()) / std * math.sqrt(252.0),
        "max_dd": float(dd.min()),
        "worst_20d": float(np.expm1(np.log1p(ret).rolling(20).sum().min())),
        "worst_60d": float(np.expm1(np.log1p(ret).rolling(60).sum().min())),
    }


def lag_extra(extra: pd.Series, dates: pd.Series) -> pd.Series:
    result = extra.shift(1, fill_value=0.0).astype(float)
    result.loc[dates.eq(REAL_START)] = 0.0
    return result


def load_frame() -> pd.DataFrame:
    frame = scope_v1.load_components()
    signal_detail = pd.read_csv(
        scope_v1.fixed.IM_TARGET,
        parse_dates=["date"],
        usecols=["date", "volume_pass", "score_hot_signal"],
    )
    frame = frame.merge(signal_detail, on="date", validate="one_to_one")
    old = pd.read_csv(OLD_DAILY, parse_dates=["date"], low_memory=False)[
        [
            "date",
            "momentum_weight",
            "v2_target_put_qty",
            "put_pnl_ret",
            "put_mark_fraction",
            "put_contract",
        ]
    ].rename(
        columns={
            "momentum_weight": "old_momentum_weight",
            "v2_target_put_qty": "old_put_qty",
            "put_pnl_ret": "old_put_pnl_ret",
            "put_mark_fraction": "old_put_mark_fraction",
            "put_contract": "old_put_contract",
        }
    )
    frame = frame.merge(old, on="date", validate="one_to_one")
    frame["new_momentum_weight"] = frame["momentum_execution_weight"].astype(float)
    frame["new_put_qty"] = frame["put_qty"].astype(float)
    frame["new_put_pnl_ret"] = frame["put_pnl_ret"].astype(float)
    frame["new_put_mark_fraction"] = frame["put_mark_fraction"].astype(float)
    frame["new_put_contract"] = frame["put_contract"]
    return frame


def build_pair(
    frame: pd.DataFrame,
    *,
    signal_family: str,
    put_family: str,
    grid_call_on: bool,
) -> pd.DataFrame:
    weight = frame[f"{signal_family}_momentum_weight"].astype(float)
    parent_qty = frame[f"{put_family}_put_qty"].astype(float)
    parent_pnl = frame[f"{put_family}_put_pnl_ret"].astype(float)
    parent_mark = frame[f"{put_family}_put_mark_fraction"].astype(float)
    parent_contract = frame[f"{put_family}_put_contract"]

    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    turnover.loc[frame.index[frame["date"].eq(REAL_START)][0]] = abs(
        float(weight.loc[frame.index[frame["date"].eq(REAL_START)][0]])
    )
    momentum_cost_full = (
        scope_v1.fixed.im_proxy.ONE_WAY_COST * turnover
        + 2.0
        * scope_v1.fixed.im_proxy.ONE_WAY_COST
        * weight
        * frame["roll_event"].astype(float)
    )
    base_gross = frame["base_gross_ret"].astype(float) + frame["base_basis_ret"].astype(float)
    grid_units = frame["grid_units"].astype(float) if grid_call_on else pd.Series(0.0, index=frame.index)
    overlay_gross = (
        frame["overlay_gross_ret"].astype(float) + frame["overlay_basis_ret"].astype(float)
        if grid_call_on
        else pd.Series(0.0, index=frame.index)
    )
    overlay_cost = (
        frame["overlay_cost_rate"].astype(float)
        if grid_call_on
        else pd.Series(0.0, index=frame.index)
    )
    futures_gross = (0.5 + 0.5 * weight) * base_gross + overlay_gross
    futures_cost = (
        0.5 * frame["base_futures_cost_rate"].astype(float)
        + 0.5 * momentum_cost_full
        + overlay_cost
    )
    if grid_call_on:
        call_pnl = 0.5 * frame["call_pnl_ret"].astype(float)
        call_cost = 0.5 * frame["call_cost_rate"].astype(float)
        call_margin = 0.5 * frame["call_margin_fraction"].astype(float)
    else:
        call_pnl = pd.Series(0.0, index=frame.index)
        call_cost = pd.Series(0.0, index=frame.index)
        call_margin = pd.Series(0.0, index=frame.index)

    rows: list[pd.DataFrame] = []
    for protection, extra in (
        ("core_only", pd.Series(0.0, index=frame.index)),
        ("core_plus_momentum", 0.5 * weight),
    ):
        end_scale = 0.5 + extra
        pnl_scale = 0.5 + lag_extra(extra, frame["date"])
        target_qty = parent_qty * end_scale
        put_cost, sides = scope_v1.reconstruct_cost(frame["date"], parent_contract, target_qty)
        put_pnl = parent_pnl * pnl_scale
        put_mark = parent_mark * end_scale
        total_units = 0.5 + 0.5 * weight + grid_units
        cash = (
            1.0
            - scope_v1.fixed.im_proxy.MARGIN_BUFFER_RATE * total_units
            - put_mark
            - call_margin
        )
        pre_cash = (
            (1.0 + futures_gross + put_pnl + call_pnl)
            * (1.0 - futures_cost)
            * (1.0 - put_cost)
            * (1.0 - call_cost)
            - 1.0
        )
        ret = pre_cash + cash * scope_v1.fixed.im_proxy.CASH_DAILY_RETURN
        rows.append(
            pd.DataFrame(
                {
                    "date": frame["date"],
                    "protection": protection,
                    "ret": ret,
                    "weight": weight,
                    "put_qty": target_qty,
                    "put_cost": put_cost,
                    "put_sides": sides,
                    "cash": cash,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    frame = load_frame()
    scenarios = {
        "s1_old_signal_old_put_no_grid_call": ("old", "old", False),
        "s2_new_signal_old_put_no_grid_call": ("new", "old", False),
        "s3_old_signal_new_put_no_grid_call": ("old", "new", False),
        "s4_new_signal_new_put_no_grid_call": ("new", "new", False),
        "s5_old_signal_new_put_with_grid_call": ("old", "new", True),
        "s6_new_signal_new_put_with_grid_call": ("new", "new", True),
    }
    daily_rows: list[pd.DataFrame] = []
    metric_rows: list[dict[str, object]] = []
    for scenario, (signal, put, context) in scenarios.items():
        daily = build_pair(frame, signal_family=signal, put_family=put, grid_call_on=context)
        daily["scenario"] = scenario
        daily_rows.append(daily)
        real = daily[daily["date"].gt(REAL_START)]
        values: dict[str, dict[str, float]] = {}
        for protection in ("core_only", "core_plus_momentum"):
            sample = real[real["protection"].eq(protection)]
            values[protection] = metrics(sample["ret"].reset_index(drop=True))
            metric_rows.append(
                {
                    "scenario": scenario,
                    "signal_family": signal,
                    "put_family": put,
                    "grid_call_on": context,
                    "protection": protection,
                    **values[protection],
                    "put_cost_total": float(sample["put_cost"].sum()),
                    "avg_put_qty": float(sample["put_qty"].mean()),
                }
            )
        metric_rows.append(
            {
                "scenario": scenario,
                "signal_family": signal,
                "put_family": put,
                "grid_call_on": context,
                "protection": "incremental_put_delta",
                **{
                    key: values["core_plus_momentum"][key] - values["core_only"][key]
                    for key in values["core_only"]
                },
                "put_cost_total": float(
                    real.loc[real["protection"].eq("core_plus_momentum"), "put_cost"].sum()
                    - real.loc[real["protection"].eq("core_only"), "put_cost"].sum()
                ),
                "avg_put_qty": float(
                    real.loc[real["protection"].eq("core_plus_momentum"), "put_qty"].mean()
                    - real.loc[real["protection"].eq("core_only"), "put_qty"].mean()
                ),
            }
        )

    all_daily = pd.concat(daily_rows, ignore_index=True)
    metrics_table = pd.DataFrame(metric_rows)
    real = frame[frame["date"].gt(REAL_START)].copy()
    old_w = real["old_momentum_weight"].astype(float)
    new_w = real["new_momentum_weight"].astype(float)
    signal_stats = {
        "rows": int(len(real)),
        "old_weight_counts": {str(k): int(v) for k, v in old_w.value_counts().sort_index().items()},
        "new_weight_counts": {str(k): int(v) for k, v in new_w.value_counts().sort_index().items()},
        "different_days": int(old_w.ne(new_w).sum()),
        "new_lower_days": int(new_w.lt(old_w).sum()),
        "new_higher_days": int(new_w.gt(old_w).sum()),
        "old_average_weight": float(old_w.mean()),
        "new_average_weight": float(new_w.mean()),
        "volume_block_days": int((~real["volume_pass"].astype(bool)).sum()),
        "hot_score_exit_days": int(real["score_hot_signal"].astype(bool).sum()),
    }
    old_q = real["old_put_qty"].astype(float)
    new_q = real["new_put_qty"].astype(float)
    put_stats = {
        "different_days": int(old_q.ne(new_q).sum()),
        "old_greater_days": int(old_q.gt(new_q).sum()),
        "new_greater_days": int(new_q.gt(old_q).sum()),
        "old_average_parent_qty": float(old_q.mean()),
        "new_average_parent_qty": float(new_q.mean()),
        "old_max_parent_qty": float(old_q.max()),
        "new_max_parent_qty": float(new_q.max()),
    }

    # The final hybrid must reproduce the v2 coverage-scan daily paths.
    scan = pd.read_csv(NEW_SCAN_DAILY, parse_dates=["date"], low_memory=False)
    s6 = all_daily[all_daily["scenario"].eq("s6_new_signal_new_put_with_grid_call")]
    parity: dict[str, float] = {}
    for protection, candidate in (
        ("core_only", "core_only_current"),
        ("core_plus_momentum", "core_plus_momentum"),
    ):
        left = s6[s6["protection"].eq(protection)][["date", "ret"]]
        right = scan[scan["candidate"].eq(candidate)][["date", "ret"]]
        joined = left.merge(right, on="date", suffixes=("_hybrid", "_scan"), validate="one_to_one")
        parity[protection] = float((joined["ret_hybrid"] - joined["ret_scan"]).abs().max())
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"Hybrid parity failed: {parity}")

    OUTPUT.mkdir(parents=True)
    metrics_table.to_csv(OUTPUT / "matched_ablation_metrics.csv", index=False)
    all_daily.to_csv(OUTPUT / "matched_ablation_daily.csv.gz", index=False, compression="gzip")
    (OUTPUT / "diagnostics.json").write_text(
        json.dumps(
            {
                "status": "research_diagnosis_only_not_live_approved",
                "real_window": ["2022-07-25", "2026-08-14"],
                "signal_stats": signal_stats,
                "put_stats": put_stats,
                "final_path_parity_max_abs": parity,
                "limitations": [
                    "hybrids use the current v5 futures/component framework to isolate signal, Put rule, and context",
                    "old formal v4 remains the authority for the old headline result",
                    "normalized fractional options; no bid-ask, impact, capacity, or integer mapping",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(metrics_table[metrics_table["protection"].eq("incremental_put_delta")].to_string(index=False))
    print(json.dumps({"signal_stats": signal_stats, "put_stats": put_stats, "parity": parity}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
