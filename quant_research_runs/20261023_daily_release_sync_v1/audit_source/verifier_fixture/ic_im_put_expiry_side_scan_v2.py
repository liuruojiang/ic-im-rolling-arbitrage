from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ic_510500_put_mom120_delta_floor_v21 as ic21
import im_mo_adaptive_valuation_mom120_floor_v12 as im12

ROOT = Path(__file__).resolve().parent
VERSION = "ic_im_put_expiry_side_scan_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
RUN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260820_ic_im_ic_im_mainlines_v1_ic_510500_put_im_mo_put_expiry_selection_side"
)
IC_OFFICIAL = (
    ROOT
    / "outputs"
    / "ic_510500_put_mom120_delta_floor_v21"
    / "daily_candidates.csv.gz"
)
IC_FULL = ROOT / "outputs" / "ic_put_grid_call_combined_v2" / "daily_candidates.csv.gz"
IM_OFFICIAL = (
    ROOT
    / "outputs"
    / "im_mo_adaptive_valuation_mom120_floor_v12"
    / "daily_candidates.csv.gz"
)
IM_FULL = (
    ROOT / "outputs" / "im_put_grid_call_final_audit_v1" / "daily_candidates.csv.gz"
)
TRADING_DAYS = 252
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0
MARGIN_PER_UNIT = 0.30
RULES = ("nearest_absolute", "on_or_after")
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def performance(values: pd.Series) -> dict[str, float]:
    returns = values.astype(float).reset_index(drop=True)
    if returns.empty or returns.le(-1.0).any() or not np.isfinite(returns).all():
        raise RuntimeError("Invalid return series")
    nav = (1.0 + returns).cumprod()
    ann_return = float(nav.iloc[-1] ** (TRADING_DAYS / len(returns)) - 1.0)
    ann_vol = float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS))
    std = float(returns.std(ddof=1))
    drawdown = nav / nav.cummax() - 1.0
    return {
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe_repo": float(returns.mean() / std * math.sqrt(TRADING_DAYS)),
        "max_dd": float(drawdown.min()),
    }


def window_slice(frame: pd.DataFrame, window: str) -> pd.DataFrame:
    ordered = frame.sort_values("date")
    if window == "full":
        return ordered
    years = int(window.removeprefix("last_").removesuffix("y"))
    start = pd.Timestamp(ordered["date"].max()) - pd.DateOffset(years=years)
    return ordered[ordered["date"].ge(start)]


def ic_on_or_after_month(
    snapshots: pd.DataFrame,
    day: pd.Timestamp,
    tenor: str,
    trade_dates: pd.DatetimeIndex,
) -> pd.Timestamp | None:
    v6 = ic21.v20.v19.v18.v13.v6
    proxy = v6.proxy
    target = day + pd.DateOffset(months=v6.TENOR_MONTHS[tenor])
    chain = snapshots[snapshots["date"].eq(day)]
    if chain.empty:
        return None
    months = chain[["contract_month"]].drop_duplicates().copy()
    months["expiry"] = months["contract_month"].map(
        lambda value: proxy.fourth_wednesday(value, trade_dates)
    )
    months = months[months["expiry"].gt(day)]
    later = months[months["expiry"].ge(target)].sort_values(
        ["expiry", "contract_month"]
    )
    if not later.empty:
        return pd.Timestamp(later.iloc[0]["contract_month"])
    return v6.proxy.select_real_month(snapshots, day, target, trade_dates)


def run_ic_rule(
    rule: str,
    frames: dict[str, pd.DataFrame],
    market: pd.DataFrame,
    schedule: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    v20 = ic21.v20
    v6 = v20.v19.v18.v13.v6
    proxy = v20.v19.v18.v13.proxy
    roll_dates = v6.forced_roll_dates(frames["ic"])
    label = f"IC_put_{rule}"
    original = v6.desired_real_month
    if rule == "on_or_after":
        v6.desired_real_month = ic_on_or_after_month
    try:
        overlay, trades = v20.run_real_delta(
            frames["ic"], schedule, frames, market, label, roll_dates
        )
    finally:
        v6.desired_real_month = original
    daily = proxy.assemble_candidate(overlay, frames["ic"]).sort_values("date")
    return daily.reset_index(drop=True), trades.reset_index(drop=True)


def rebuild_ic_full(put_daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    frozen = pd.read_csv(IC_FULL, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("real_grid_only")
    ].sort_values("date")
    put = put_daily[
        ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_contract"]
    ].copy()
    frame = frozen.drop(
        columns=["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_contract"]
    ).merge(put, on="date", validate="one_to_one")
    gross = (
        frame["ic_gross_ret"]
        + frame["overlay_gross_ret"]
        + frame["put_pnl_ret"]
        + frame["call_pnl_ret"]
    )
    frame["cash_weight"] = (
        1.0
        - MARGIN_PER_UNIT * frame["total_ic_units"]
        - frame["put_mark_fraction"]
        - frame["call_margin_fraction"]
    ).clip(lower=0.0)
    frame["cash_ret"] = (
        (1.0 + gross)
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
        + frame["cash_weight"] * CASH_DAILY
    )
    frame["candidate"] = f"IC_full_{rule}"
    return frame.reset_index(drop=True)


def im_on_or_after_month(
    options: pd.DataFrame, day: pd.Timestamp, target_date: pd.Timestamp
) -> pd.Timestamp:
    chain = options[options["date"].eq(day) & options["actual_expiry"].gt(day)][
        ["contract_month", "actual_expiry"]
    ].drop_duplicates()
    if chain.empty:
        raise RuntimeError(f"No future option month on {day.date()}")
    later = chain[chain["actual_expiry"].ge(target_date)].sort_values(
        ["actual_expiry", "contract_month"]
    )
    if not later.empty:
        return pd.Timestamp(later.iloc[0]["contract_month"])
    return im12.v4.selected_month(options, day, target_date)


def run_im_rule(
    rule: str,
    upstream: pd.DataFrame,
    options: pd.DataFrame,
    active_im: pd.Series,
    schedule: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    label = f"IM_put_{rule}"
    candidate_schedule = schedule.copy()
    candidate_schedule["candidate"] = label
    original = im12.v8.v4.selected_month
    if rule == "on_or_after":
        im12.v8.v4.selected_month = im_on_or_after_month
    try:
        overlay, trades, _lives = im12.v8.run_real_normal_close(
            upstream,
            options,
            active_im,
            candidate_schedule,
            "3m",
            0.95,
            label,
        )
    finally:
        im12.v8.v4.selected_month = original
    real_base = upstream[["date", "im_gross_ret", "cost_rate", "im_net_ret"]].rename(
        columns={"im_gross_ret": "gross_ret", "im_net_ret": "net_ret"}
    )
    daily = im12.v6.assemble_layer("real", real_base, {label: overlay})
    daily = im12.v10.add_nav(daily)
    daily = daily[daily["candidate"].eq(label)].sort_values("date")
    return daily.reset_index(drop=True), trades.reset_index(drop=True)


def rebuild_im_full(put_daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    frozen = pd.read_csv(IM_FULL, parse_dates=["date"])
    frozen = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("full_put_grid_call")
    ].sort_values("date")
    put = put_daily[
        ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_contract"]
    ].copy()
    frame = frozen.drop(
        columns=["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_contract"]
    ).merge(put, on="date", validate="one_to_one")
    gross = (
        frame["base_gross_ret"]
        + frame["overlay_gross_ret"]
        + frame["put_pnl_ret"]
        + frame["call_pnl_ret"]
    )
    frame["cash_weight"] = (
        1.0
        - MARGIN_PER_UNIT * frame["total_im_units"]
        - frame["put_mark_fraction"]
        - frame["call_margin_fraction"]
    ).clip(lower=0.0)
    frame["cash_ret"] = (
        (1.0 + gross)
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
        + frame["cash_weight"] * CASH_DAILY
    )
    frame["candidate"] = f"IM_full_{rule}"
    return frame.reset_index(drop=True)


def parity_check(
    observed: pd.DataFrame,
    expected_path: Path,
    expected_candidate: str,
    column: str = "cash_ret",
) -> float:
    expected = pd.read_csv(expected_path, parse_dates=["date"], low_memory=False)
    expected = expected[expected["candidate"].eq(expected_candidate)]
    if "layer" in expected:
        expected = expected[expected["layer"].eq("real")]
    merged = observed[["date", column]].merge(
        expected[["date", column]],
        on="date",
        suffixes=("_new", "_old"),
        validate="one_to_one",
    )
    if len(merged) != len(observed) or len(merged) != len(expected):
        raise RuntimeError(f"Parity date mismatch: {expected_candidate}")
    return float((merged[f"{column}_new"] - merged[f"{column}_old"]).abs().max())


def trade_diagnostics(
    product: str,
    rule: str,
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    expiry_lookup: dict[pd.Timestamp, pd.Timestamp] | None = None,
) -> dict[str, Any]:
    month_column = "new_month" if "new_month" in trades else "desired_contract_month"
    months = pd.to_datetime(trades[month_column], errors="coerce")
    if product == "IM":
        contract_changed = trades["new_contract"].fillna("").ne("") & trades[
            "new_contract"
        ].fillna("").ne(trades["old_contract"].fillna(""))
        entries = trades[months.notna() & contract_changed].copy()
    else:
        entries = trades[months.notna()].copy()
    if entries.empty:
        return {
            "product": product,
            "expiry_rule": rule,
            "entry_or_roll_events": 0,
            "avg_entry_dte": 0.0,
            "avg_expiry_minus_target_days": 0.0,
            "put_cost_total": float(daily["put_cost_rate"].sum()),
            "avg_put_mark_fraction": float(daily["put_mark_fraction"].mean()),
        }
    entries["actual_execution_date"] = pd.to_datetime(entries["actual_execution_date"])
    entries["new_month"] = pd.to_datetime(entries[month_column])
    if product == "IC":
        trade_dates = pd.DatetimeIndex(daily["date"])
        entries["expiry"] = entries["new_month"].map(
            lambda value: ic21.v20.v19.v18.v13.proxy.fourth_wednesday(
                value, trade_dates
            )
        )
    else:
        if expiry_lookup is None:
            raise RuntimeError("IM actual-expiry lookup is required")
        entries["expiry"] = entries["new_month"].map(expiry_lookup)
        if entries["expiry"].isna().any():
            raise RuntimeError("Missing IM actual expiry in diagnostics")
    entries["target"] = (
        pd.to_datetime(entries["target_date"])
        if "target_date" in entries
        else entries["actual_execution_date"] + pd.DateOffset(months=3)
    )
    return {
        "product": product,
        "expiry_rule": rule,
        "entry_or_roll_events": len(entries),
        "avg_entry_dte": float(
            (entries["expiry"] - entries["actual_execution_date"]).dt.days.mean()
        ),
        "avg_expiry_minus_target_days": float(
            (entries["expiry"] - entries["target"]).dt.days.mean()
        ),
        "put_cost_total": float(daily["put_cost_rate"].sum()),
        "avg_put_mark_fraction": float(daily["put_mark_fraction"].mean()),
    }


def metric_rows(
    candidates: dict[str, pd.DataFrame],
    diagnostics: dict[tuple[str, str], dict[str, Any]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, frame in candidates.items():
        product, scope, rule = candidate.split("_", 2)
        diag = diagnostics[(product, rule)]
        for window in WINDOWS:
            sample = window_slice(frame, window)
            values = performance(sample["cash_ret"])
            rows.append(
                {
                    "candidate": candidate,
                    "segment": window,
                    "start": sample["date"].min().date(),
                    "end": sample["date"].max().date(),
                    "rows": len(sample),
                    **values,
                    "product": product,
                    "portfolio_scope": scope,
                    "expiry_rule": rule,
                    "put_cost_total": diag["put_cost_total"],
                    "avg_put_mark_fraction": diag["avg_put_mark_fraction"],
                    "entry_or_roll_events": diag["entry_or_roll_events"],
                    "avg_entry_dte": diag["avg_entry_dte"],
                    "avg_expiry_minus_target_days": diag[
                        "avg_expiry_minus_target_days"
                    ],
                }
            )
    long = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in long.groupby("candidate", sort=True):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "product": first["product"],
            "portfolio_scope": first["portfolio_scope"],
            "expiry_rule": first["expiry_rule"],
            "put_cost_total": first["put_cost_total"],
            "avg_put_mark_fraction": first["avg_put_mark_fraction"],
            "entry_or_roll_events": first["entry_or_roll_events"],
            "avg_entry_dte": first["avg_entry_dte"],
            "avg_expiry_minus_target_days": first["avg_expiry_minus_target_days"],
        }
        for item in group.itertuples(index=False):
            row[f"ann_return_{item.segment}"] = item.ann_return
            row[f"max_dd_{item.segment}"] = item.max_dd
            row[f"sharpe_repo_{item.segment}"] = item.sharpe_repo
        row["decision_hint"] = "review_against_same_product_baseline"
        row["stability_label"] = "pending_review"
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def main() -> None:
    started = time.perf_counter()
    if not RUN.exists() or not SPEC.exists():
        raise FileNotFoundError(
            "Scan scaffold or preregistered specification is missing"
        )

    frames, daily_valuation, market, _market_checks = (
        ic21.v20.v19.v18.load_close_inputs()
    )
    signal_inputs, _signal_checks = ic21.v20.v19.v18.build_signal_inputs(
        daily_valuation
    )
    ic_schedule, _ = ic21.build_schedules(frames["ic"], daily_valuation, signal_inputs)
    ic_schedule = ic_schedule[
        ic_schedule["layer"].eq("real") & ic_schedule["signal_variant"].eq("l190_mom25")
    ].copy()

    definitions = im12.candidate_definitions()
    valuation_states = im12.v10.load_v7_states()
    im_market, _ = im12.v6.model_market()
    upstream, _, _, _, _, raw_options = im12.v4.load_inputs()
    im_daily_valuation, feature_diffs = im12.v4.build_daily_valuation()
    if max(feature_diffs.values()) > 1e-14:
        raise RuntimeError(f"IM valuation parity failed: {feature_diffs}")
    legacy_state = im12.v6.signal_state(im_daily_valuation)
    schedules = im12.build_schedules(
        definitions,
        valuation_states,
        legacy_state,
        pd.DatetimeIndex(im_market["date"]),
        pd.DatetimeIndex(upstream["date"]),
    )
    im_schedule = schedules[("real", "valmom_center_floor3")].copy()
    active_im = im12.v8.active_im_closes(upstream)
    expiry_map = im12.v4.actual_expiry_map(raw_options, upstream)
    options = im12.v4.prepare_options(raw_options, expiry_map)
    im_expiry_lookup = {
        pd.Timestamp(row.contract_month): pd.Timestamp(row.actual_expiry)
        for row in options[["contract_month", "actual_expiry"]]
        .drop_duplicates()
        .itertuples(index=False)
    }

    candidates: dict[str, pd.DataFrame] = {}
    diagnostics: dict[tuple[str, str], dict[str, Any]] = {}
    parity: list[dict[str, Any]] = []
    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(exist_ok=True)

    for rule in RULES:
        ic_put, ic_trades = run_ic_rule(rule, frames, market, ic_schedule)
        ic_full = rebuild_ic_full(ic_put, rule)
        im_put, im_trades = run_im_rule(rule, upstream, options, active_im, im_schedule)
        im_full = rebuild_im_full(im_put, rule)
        for label, frame in {
            f"IC_put_{rule}": ic_put,
            f"IC_full_{rule}": ic_full,
            f"IM_put_{rule}": im_put,
            f"IM_full_{rule}": im_full,
        }.items():
            candidates[label] = frame
            frame.to_csv(daily_dir / f"{label}.csv.gz", index=False, compression="gzip")
        diagnostics[("IC", rule)] = trade_diagnostics("IC", rule, ic_put, ic_trades)
        diagnostics[("IM", rule)] = trade_diagnostics(
            "IM", rule, im_put, im_trades, im_expiry_lookup
        )

    parity.extend(
        [
            {
                "check": "IC_put_baseline_vs_v21",
                "max_abs": parity_check(
                    candidates["IC_put_nearest_absolute"],
                    IC_OFFICIAL,
                    "real_l190_mom25",
                ),
            },
            {
                "check": "IC_full_rebuild_vs_frozen",
                "max_abs": parity_check(
                    candidates["IC_full_nearest_absolute"],
                    IC_FULL,
                    "real_grid_only",
                ),
            },
            {
                "check": "IM_put_baseline_vs_v12",
                "max_abs": parity_check(
                    candidates["IM_put_nearest_absolute"],
                    IM_OFFICIAL,
                    "valmom_center_floor3",
                ),
            },
            {
                "check": "IM_full_rebuild_vs_frozen",
                "max_abs": parity_check(
                    candidates["IM_full_nearest_absolute"],
                    IM_FULL,
                    "full_put_grid_call",
                ),
            },
        ]
    )
    parity_frame = pd.DataFrame(parity)
    if parity_frame["max_abs"].max() > 1e-12:
        raise RuntimeError(f"Baseline parity failed:\n{parity_frame}")

    long, wide = metric_rows(candidates, diagnostics)
    long.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    pd.DataFrame(diagnostics.values()).to_csv(
        RUN / "selection_diagnostics.csv", index=False
    )
    parity_frame.to_csv(RUN / "parity_checks.csv", index=False)

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "candidate_bundle",
            "parameter_group": "expiry_selection_side",
            "baseline": {"expiry_rule": "nearest_absolute"},
            "candidate_grid": [
                {"expiry_rule": "nearest_absolute"},
                {"expiry_rule": "on_or_after"},
            ],
            "data_snapshot": {
                "IC": {
                    "start": str(
                        candidates["IC_put_nearest_absolute"]["date"].min().date()
                    ),
                    "end": str(
                        candidates["IC_put_nearest_absolute"]["date"].max().date()
                    ),
                    "source": "frozen real 510500 ETF Put snapshots/histories",
                },
                "IM": {
                    "start": str(
                        candidates["IM_put_nearest_absolute"]["date"].min().date()
                    ),
                    "end": str(
                        candidates["IM_put_nearest_absolute"]["date"].max().date()
                    ),
                    "source": "frozen real CFFEX MO chain",
                },
            },
            "cost_model": {
                "put_costs": "inherited frozen IC v21 and IM v12 side costs",
                "futures_grid_call_costs": "inherited frozen combined mainlines",
                "cash": "30% margin per futures unit and 3% annual cash return",
                "slippage": "no extra slippage beyond frozen side costs",
            },
            "parity_check": parity,
            "cache_write_risk": "none; frozen inputs read-only, outputs only under this run folder",
            "source_hashes": {
                "spec": sha256(SPEC),
                "ic21": sha256(ROOT / "ic_510500_put_mom120_delta_floor_v21.py"),
                "im12": sha256(ROOT / "im_mo_adaptive_valuation_mom120_floor_v12.py"),
            },
            "elapsed_sec": time.perf_counter() - started,
            "warnings": [
                "Real option samples are shorter than five years; last_10y and last_5y clip to available sample.",
                "510500 option histories are frozen third-party daily closes, not executable bid/ask fills.",
            ],
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nuv run --with pandas --with numpy --with requests --with openpyxl "
            "python ic_im_put_expiry_side_scan_v2.py\n"
        )
    print(parity_frame.to_string(index=False))
    print(wide.to_string(index=False))


if __name__ == "__main__":
    main()
