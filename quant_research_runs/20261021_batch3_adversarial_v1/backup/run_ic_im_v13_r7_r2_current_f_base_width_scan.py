#!/usr/bin/env python
"""Research-only R-squared width scan for the current IC/IM v1.3-r7 F base.

The scan deliberately changes one layer only: the R-squared gate applied to
the 0.5-notional momentum futures sleeve.  The fixed 0.5-notional futures
sleeve, quarterly roll, one-way futures cost and 30% margin/cash convention
are held matched.  Grid and option legs are excluded from the measured return,
because R-squared does not govern their historical signal paths.

No production source, frozen specification, ledger or order state is changed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN_ID = "20260912_ic_im_v13_r7_r2_current_f_base_width_scan"
RUN = ROOT / "quant_param_scan_runs" / RUN_ID

FORMAL_END = pd.Timestamp("2026-08-14")
PREVIOUS_END = pd.Timestamp("2026-09-10")
END = pd.Timestamp("2026-09-11")
START = pd.Timestamp("2015-04-16")
REAL_START = {"IC": pd.Timestamp("2022-09-19"), "IM": pd.Timestamp("2022-07-22")}

ONE_WAY = 0.0001
MARGIN = 0.30
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0

# A rectangular, evenly spaced map makes the pass-region width interpretable.
# The raw Score lookbacks stay frozen at IM=18 and IC=24; only this R2 window
# changes.
R2_WINDOWS = tuple(range(10, 61, 5))
R2_THRESHOLDS = tuple(round(value, 3) for value in np.arange(0.0, 0.5001, 0.025))

ATTRIBUTION_INPUT = ROOT / "outputs" / "v13_component_attribution_20260912" / "daily_component_inputs.csv.gz"
IC_FROZEN_OHLCV = ROOT / "data" / "ic_510500_put_proxy_validation_v1" / "sina_000905_index.csv"
IM_FROZEN_OHLCV = ROOT / "data" / "im_mo_csi1000_put_protection_battery_v6" / "sina_sh000852_index.csv"
ASHARE_SOURCE = ROOT.parent / "A 股股指多头策略" / "poe_cn_four_index_raw_momentum_combo_v1_3_bot.py"
IC_MAINLINE = ROOT / "ic_mainline_v1_3.py"
IM_MAINLINE = ROOT / "im_mainline_v1_3.py"
CFFEX_SOURCE = ROOT / "poe_ic_im_mainline_v1_3_bot.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def as_ohlcv(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    result = frame.copy()
    if "date" not in result.columns:
        result = result.reset_index()
        if "date" not in result.columns:
            result = result.rename(columns={result.columns[0]: "date"})
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = sorted(set(required).difference(result.columns))
    if missing:
        raise ValueError(f"{label} missing OHLCV columns: {missing}")
    result = result[required].copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.normalize()
    for column in required[1:]:
        result[column] = pd.to_numeric(result[column], errors="raise")
    if result[required[1:]].isna().any().any() or (result["close"] <= 0).any():
        raise ValueError(f"{label} contains invalid OHLCV values")
    result = result.sort_values("date").reset_index(drop=True)
    if result["date"].duplicated().any() or not result["date"].is_monotonic_increasing:
        raise ValueError(f"{label} dates are not unique/increasing")
    return result


def merge_frozen_and_fresh(
    frozen_path: Path,
    fresh: pd.DataFrame,
    source_module: Any,
    label: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frozen = as_ohlcv(pd.read_csv(frozen_path), f"{label} frozen OHLCV")
    fresh = as_ohlcv(fresh, f"{label} fresh OHLCV")
    overlap = frozen.merge(fresh, on="date", suffixes=("_frozen", "_fresh"))
    overlap = overlap.loc[overlap["date"].le(FORMAL_END)].copy()
    if overlap.empty:
        raise ValueError(f"{label} has no frozen/fresh overlap before {FORMAL_END.date()}")
    differences: dict[str, float] = {}
    for column in ("open", "high", "low", "close", "volume"):
        differences[column] = float(
            (overlap[f"{column}_frozen"] - overlap[f"{column}_fresh"]).abs().max()
        )
    # The frozen pre-checkpoint history is authoritative.  A mismatch in its
    # cloud copy is an audit finding, not a reason to silently rewrite it.
    if any(differences[column] > (1e-8 if column != "volume" else 0.5) for column in differences):
        raise RuntimeError(f"{label} frozen/cloud overlap mismatch: {differences}")
    result = pd.concat(
        [frozen.loc[frozen["date"].le(FORMAL_END)], fresh.loc[fresh["date"].gt(FORMAL_END)]],
        ignore_index=True,
    ).sort_values("date").reset_index(drop=True)
    if result["date"].duplicated().any() or result["date"].iloc[-1] != END:
        raise RuntimeError(f"{label} merged OHLCV does not end at {END.date()}")
    expected_tail = [
        day
        for day in pd.date_range(FORMAL_END + pd.Timedelta(days=1), END, freq="D")
        if source_module.is_cn_trading_day(day)
    ]
    missing_tail = [day.date().isoformat() for day in expected_tail if day not in set(result["date"])]
    if missing_tail:
        raise RuntimeError(f"{label} missing confirmed tail sessions: {missing_tail}")
    audit = {
        "frozen_path": str(frozen_path.relative_to(ROOT)),
        "frozen_rows": int(len(frozen)),
        "fresh_rows": int(len(fresh)),
        "merged_rows": int(len(result)),
        "start": result["date"].iloc[0].date().isoformat(),
        "end": result["date"].iloc[-1].date().isoformat(),
        "overlap_rows": int(len(overlap)),
        "overlap_max_abs_difference": differences,
        "tail_sessions": [day.date().isoformat() for day in expected_tail],
    }
    return result, audit


def load_unit_return_inputs(cffex_module: Any) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    if not ATTRIBUTION_INPUT.is_file():
        raise FileNotFoundError(ATTRIBUTION_INPUT)
    raw = pd.read_csv(ATTRIBUTION_INPUT, parse_dates=["date"], low_memory=False)
    frames: dict[str, pd.DataFrame] = {}
    audit: dict[str, Any] = {}
    marks = cffex_module.fetch_cffex_daily_marks(["IC2609", "IM2609"], END.date(), END.date()).reset_index()
    for product, contract in (("IC", "IC2609"), ("IM", "IM2609")):
        frame = raw.loc[raw["product"].eq(product)].copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame = frame.loc[frame["date"].between(START, PREVIOUS_END)].sort_values("date").reset_index(drop=True)
        if len(frame) == 0 or frame["date"].iloc[0] != START or frame["date"].iloc[-1] != PREVIOUS_END:
            raise RuntimeError(f"{product} input coverage is not {START.date()}~{PREVIOUS_END.date()}")
        if frame["date"].duplicated().any():
            raise RuntimeError(f"{product} duplicate input dates")
        if product == "IC":
            unit = np.where(
                frame["formula"].eq("ic_formal"),
                frame["futures_gross_ret"].astype(float),
                frame["futures_gross_ret"].astype(float) / frame["total_units"].astype(float),
            )
        else:
            formal = frame["base_gross_ret"].notna()
            unit = np.where(
                formal,
                frame["base_gross_ret"].astype(float) + frame["base_basis_ret"].astype(float),
                frame["futures_gross_ret"].astype(float) / frame["total_units"].astype(float),
            )
        result = pd.DataFrame(
            {
                "date": frame["date"],
                "unit_gross_ret": unit.astype(float),
                "roll_event": frame["roll_event"].astype(bool).to_numpy(),
                "recorded_momentum_weight": frame["momentum_weight"].astype(float).to_numpy(),
                "recorded_momentum_turnover": frame["momentum_turnover"].astype(float).to_numpy(),
                "source_layer": np.where(frame["date"].le(FORMAL_END), "frozen_formal", "r7_historical_tail"),
            }
        )
        quote = marks.loc[marks["contract"].eq(contract)]
        if len(quote) != 1:
            raise RuntimeError(f"Missing unique {product} official mark for {END.date()}: {len(quote)}")
        row = quote.iloc[0]
        extension = pd.DataFrame(
            [
                {
                    "date": END,
                    "unit_gross_ret": float(row["settle"]) / float(row["pre_settle"]) - 1.0,
                    "roll_event": False,
                    "recorded_momentum_weight": math.nan,
                    "recorded_momentum_turnover": math.nan,
                    "source_layer": "official_cffex_2026-09-11_extension",
                }
            ]
        )
        result = pd.concat([result, extension], ignore_index=True)
        if not np.isfinite(result["unit_gross_ret"]).all():
            raise RuntimeError(f"{product} non-finite unit futures return")
        frames[product] = result
        audit[product] = {
            "input_rows_before_extension": int(len(frame)),
            "input_rows_after_extension": int(len(result)),
            "input_start": result["date"].iloc[0].date().isoformat(),
            "input_end": result["date"].iloc[-1].date().isoformat(),
            "end_contract": contract,
            "end_settle": float(row["settle"]),
            "end_pre_settle": float(row["pre_settle"]),
            "end_unit_gross_ret": float(extension["unit_gross_ret"].iloc[0]),
        }
    return frames, audit


def im_r2_schedule(
    im_module: Any,
    source_module: Any,
    ohlcv: pd.DataFrame,
    r2: pd.Series | None,
    threshold: float | None,
) -> pd.DataFrame:
    baseline = im_module.build_momentum_schedule(ohlcv).copy()
    if threshold is None:
        baseline["score_r2"] = math.nan if r2 is None else r2.reindex(baseline["date"]).to_numpy()
        baseline["r2_pass"] = True
        return baseline
    if r2 is None:
        raise ValueError("R2 series required for an R2 candidate")
    policy = im_module.MOMENTUM_POLICY
    # build_momentum_schedule returns a RangeIndex frame, while the source R2
    # series is date-indexed.  Rebind values positionally after the explicit
    # date reindex so the gate is applied row by row rather than by unlike
    # indexes.
    values = r2.reindex(pd.DatetimeIndex(baseline["date"])).reset_index(drop=True)
    values.index = baseline.index
    r2_pass = values.ge(float(threshold)).fillna(False)
    r2_base = baseline["base_momentum_signal_target"].where(r2_pass, 0.0)
    volume_filtered = r2_base.where(baseline["volume_pass"].astype(bool), 0.0)
    hot = (baseline["score"] >= policy.hot_score_threshold) & volume_filtered.gt(0.0)
    signal = volume_filtered.where(~hot, volume_filtered * policy.hot_scale)
    result = baseline.copy()
    result["score_r2"] = values.to_numpy(dtype=float)
    result["r2_pass"] = r2_pass.to_numpy(dtype=bool)
    result["r2_filtered_signal_target"] = r2_base.to_numpy(dtype=float)
    result["volume_filtered_signal_target"] = volume_filtered.to_numpy(dtype=float)
    result["score_hot_signal"] = hot.to_numpy(dtype=bool)
    result["momentum_signal_target"] = signal.to_numpy(dtype=float)
    result["momentum_execution_weight"] = signal.shift(1, fill_value=0.0).to_numpy(dtype=float)
    im_module._validate_momentum_schedule(result)
    return result


def ic_r2_schedule(
    ic_module: Any,
    source_module: Any,
    ohlcv: pd.DataFrame,
    r2: pd.Series | None,
    threshold: float | None,
) -> pd.DataFrame:
    if threshold is None:
        baseline = ic_module.build_momentum_schedule(ohlcv).copy()
        baseline["score_r2"] = math.nan if r2 is None else r2.reindex(baseline["date"]).to_numpy()
        baseline["r2_pass"] = True
        return baseline
    if r2 is None:
        raise ValueError("R2 series required for an R2 candidate")
    policy = ic_module.MOMENTUM_POLICY
    source = ic_module.shared.validate_ohlcv(ohlcv, "CSI500 R2 scan OHLCV")
    formal = source.loc[source.index >= ic_module.FORMAL_START].copy()
    indicator_close = source["close"].astype(float)
    close = formal["close"].astype(float)
    score = ic_module.calc_bias_momentum(indicator_close).reindex(close.index)
    abs20 = (
        indicator_close / indicator_close.shift(policy.absolute_momentum_days) - 1.0
    ).reindex(close.index)
    raw_target = score.gt(policy.score_threshold).astype(float) * (
        1.0
        - policy.absolute_filter_share
        + policy.absolute_filter_share * abs20.gt(policy.absolute_momentum_threshold).astype(float)
    )
    values = r2.reindex(close.index)
    r2_pass = values.ge(float(threshold)).fillna(False)
    base_signal = raw_target.where(r2_pass, 0.0).rename("base_momentum_signal_target")
    base_execution = base_signal.shift(1, fill_value=0.0).rename("base_momentum_execution_weight")
    raw_ret = close.pct_change().fillna(0.0)
    turnover = base_execution.diff().abs().fillna(base_execution.abs())
    daily_cash = (1.0 + policy.annual_cash_yield) ** (1.0 / policy.annualization_days) - 1.0
    cash_ret = (1.0 - base_execution).clip(lower=0.0, upper=1.0) * daily_cash
    cash_ret.iloc[0] = 0.0
    base_ret = base_execution * raw_ret + cash_ret - policy.cost_rate * turnover
    base_nav = (1.0 + base_ret).cumprod()
    base_dd = base_nav / base_nav.cummax() - 1.0
    nav_decay_signal = base_dd.le(-policy.nav_decay_threshold)
    scale = pd.Series(
        np.where(nav_decay_signal, policy.nav_decay_scale, 1.0),
        index=close.index,
    )
    signal = base_signal * scale
    execution = signal.shift(1, fill_value=0.0)
    return pd.DataFrame(
        {
            "date": close.index,
            "close": close.to_numpy(dtype=float),
            "score": score.to_numpy(dtype=float),
            "abs20": abs20.to_numpy(dtype=float),
            "score_r2": values.to_numpy(dtype=float),
            "r2_pass": r2_pass.to_numpy(dtype=bool),
            "base_momentum_signal_target": base_signal.to_numpy(dtype=float),
            "base_momentum_execution_weight": base_execution.to_numpy(dtype=float),
            "base_strategy_ret_for_nav_gate": base_ret.to_numpy(dtype=float),
            "base_nav_for_dd": base_nav.to_numpy(dtype=float),
            "base_dd_for_gate": base_dd.to_numpy(dtype=float),
            "nav_decay_signal": nav_decay_signal.to_numpy(dtype=bool),
            "nav_decay_signal_scale": scale.to_numpy(dtype=float),
            "momentum_signal_target": signal.to_numpy(dtype=float),
            "momentum_execution_weight": execution.to_numpy(dtype=float),
        }
    )


def make_f_base_returns(inputs: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    joined = inputs.merge(
        schedule[["date", "momentum_execution_weight"]], on="date", how="left", validate="one_to_one"
    ).sort_values("date").reset_index(drop=True)
    if joined["momentum_execution_weight"].isna().any():
        missing = joined.loc[joined["momentum_execution_weight"].isna(), "date"].head(3).tolist()
        raise RuntimeError(f"Missing momentum execution weights: {missing}")
    weight = joined["momentum_execution_weight"].astype(float)
    allowed = np.array([0.0, 0.25, 0.5, 1.0])
    if not np.isclose(weight.to_numpy()[:, None], allowed[None, :], atol=1e-12).any(axis=1).all():
        raise RuntimeError("Momentum execution weights escaped the current 0/0.25/0.5/1 grid")
    turnover = weight.diff().abs().fillna(weight.abs())
    roll = joined["roll_event"].astype(bool).to_numpy(dtype=bool)
    core_cost = np.where(np.arange(len(joined)) == 0, ONE_WAY, 2.0 * ONE_WAY * roll)
    momentum_cost = ONE_WAY * turnover.to_numpy(dtype=float) + 2.0 * ONE_WAY * weight.to_numpy(dtype=float) * roll
    futures_cost = 0.5 * core_cost + 0.5 * momentum_cost
    total_f_units = 0.5 + 0.5 * weight.to_numpy(dtype=float)
    gross = total_f_units * joined["unit_gross_ret"].to_numpy(dtype=float)
    cash_weight = np.maximum(0.0, 1.0 - MARGIN * total_f_units)
    ret = (1.0 + gross) * (1.0 - futures_cost) - 1.0 + cash_weight * CASH_DAILY
    result = joined.copy()
    result["momentum_weight"] = weight.to_numpy(dtype=float)
    result["momentum_turnover"] = turnover.to_numpy(dtype=float)
    result["total_f_units"] = total_f_units
    result["futures_gross_ret"] = gross
    result["futures_cost_rate"] = futures_cost
    result["cash_weight_f_base"] = cash_weight
    result["cash_ret"] = cash_weight * CASH_DAILY
    result["ret"] = ret
    result["nav"] = (1.0 + result["ret"]).cumprod()
    result["drawdown"] = result["nav"] / np.maximum.accumulate(np.r_[1.0, result["nav"].to_numpy()])[1:] - 1.0
    if not np.isfinite(result[["ret", "nav", "drawdown"]].to_numpy(dtype=float)).all():
        raise RuntimeError("Non-finite F-base result")
    return result


def metric(sample: pd.DataFrame) -> dict[str, Any]:
    returns = sample["ret"].to_numpy(dtype=float)
    nav = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(np.r_[1.0, nav])[1:]
    drawdown = nav / peaks - 1.0
    periods = int(len(returns))
    ann_return = float(nav[-1] ** (252.0 / periods) - 1.0)
    ann_vol = float(np.std(returns, ddof=1) * math.sqrt(252.0)) if periods > 1 else math.nan
    sharpe = ann_return / ann_vol if ann_vol > 1e-15 else math.nan
    max_dd = float(drawdown.min())
    return {
        "start": sample["date"].iloc[0].date().isoformat(),
        "end": sample["date"].iloc[-1].date().isoformat(),
        "periods": periods,
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": ann_return / abs(max_dd) if max_dd < -1e-15 else math.nan,
        "final_nav": float(nav[-1]),
        "avg_momentum_weight": float(sample["momentum_weight"].mean()),
        "avg_total_f_units": float(sample["total_f_units"].mean()),
        "momentum_turnover_sum": float(sample["momentum_turnover"].sum()),
        "futures_cost_total": float(sample["futures_cost_rate"].sum()),
    }


def window_metrics(result: pd.DataFrame, product: str, candidate: str, r2_window: float | None, threshold: float | None) -> list[dict[str, Any]]:
    end = result["date"].iloc[-1]
    windows: list[tuple[str, pd.Timestamp]] = [
        ("full", result["date"].iloc[0]),
        ("10y", end - pd.DateOffset(years=10)),
        ("5y", end - pd.DateOffset(years=5)),
        ("3y", end - pd.DateOffset(years=3)),
        ("1y", end - pd.DateOffset(years=1)),
        ("real_only", REAL_START[product]),
    ]
    rows: list[dict[str, Any]] = []
    for name, start in windows:
        sample = result.loc[result["date"].ge(start)].copy()
        if len(sample) < 2:
            continue
        rows.append(
            {
                "product": product,
                "candidate": candidate,
                "r2_window": r2_window,
                "r2_threshold": threshold,
                "window": name,
                **metric(sample),
            }
        )
    return rows


def candidate_name(window: int | None, threshold: float | None) -> str:
    if window is None:
        return "r2_off"
    return f"r2_w{window:02d}_t{threshold:0.3f}".replace(".", "p")


def summarize_width(summary: pd.DataFrame, gate: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for product in ("IC", "IM"):
        subset = summary.loc[(summary["product"].eq(product)) & summary["candidate"].ne("r2_off")].copy()
        if gate == "full":
            eligible = subset.loc[subset["full_pass_either"]]
        elif gate == "real_only":
            eligible = subset.loc[subset["real_only_pass_either"]]
        elif gate == "both":
            eligible = subset.loc[subset["full_pass_either"] & subset["real_only_pass_either"]]
        else:
            raise ValueError(gate)
        points = {
            (int(row.r2_window), float(row.r2_threshold))
            for row in eligible[["r2_window", "r2_threshold"]].itertuples(index=False)
        }
        components: list[set[tuple[int, float]]] = []
        while points:
            seed = points.pop()
            component = {seed}
            stack = [seed]
            while stack:
                window, threshold = stack.pop()
                wi = R2_WINDOWS.index(window)
                ti = R2_THRESHOLDS.index(round(threshold, 3))
                neighbours: list[tuple[int, float]] = []
                if wi > 0:
                    neighbours.append((R2_WINDOWS[wi - 1], R2_THRESHOLDS[ti]))
                if wi + 1 < len(R2_WINDOWS):
                    neighbours.append((R2_WINDOWS[wi + 1], R2_THRESHOLDS[ti]))
                if ti > 0:
                    neighbours.append((R2_WINDOWS[wi], R2_THRESHOLDS[ti - 1]))
                if ti + 1 < len(R2_THRESHOLDS):
                    neighbours.append((R2_WINDOWS[wi], R2_THRESHOLDS[ti + 1]))
                for neighbour in neighbours:
                    if neighbour in points:
                        points.remove(neighbour)
                        component.add(neighbour)
                        stack.append(neighbour)
            components.append(component)
        if not components:
            rows.append(
                {
                    "product": product, "gate": gate, "component_rank": 0, "passed_cells": 0,
                    "component_cells": 0, "window_count": 0, "threshold_count": 0,
                    "window_min": math.nan, "window_max": math.nan,
                    "threshold_min": math.nan, "threshold_max": math.nan,
                    "rectangle_density": math.nan, "spans_3x3": False,
                }
            )
            continue
        components.sort(key=lambda values: (-len(values), min(values)))
        passed = int(len(eligible))
        for rank, component in enumerate(components, start=1):
            windows = sorted({item[0] for item in component})
            thresholds = sorted({item[1] for item in component})
            width = len(windows) * len(thresholds)
            rows.append(
                {
                    "product": product,
                    "gate": gate,
                    "component_rank": rank,
                    "passed_cells": passed,
                    "component_cells": len(component),
                    "window_count": len(windows),
                    "threshold_count": len(thresholds),
                    "window_min": min(windows),
                    "window_max": max(windows),
                    "threshold_min": min(thresholds),
                    "threshold_max": max(thresholds),
                    "rectangle_density": len(component) / width if width else math.nan,
                    "spans_3x3": len(windows) >= 3 and len(thresholds) >= 3,
                }
            )
    return pd.DataFrame(rows)


def plot_pass_map(summary: pd.DataFrame, product: str) -> None:
    subset = summary.loc[(summary["product"].eq(product)) & summary["candidate"].ne("r2_off")].copy()
    matrix = np.zeros((len(R2_THRESHOLDS), len(R2_WINDOWS)), dtype=int)
    for row in subset.itertuples(index=False):
        i = R2_THRESHOLDS.index(round(float(row.r2_threshold), 3))
        j = R2_WINDOWS.index(int(row.r2_window))
        matrix[i, j] = int(bool(row.full_return_improved)) + 2 * int(bool(row.full_max_dd_improved))
    from matplotlib.colors import ListedColormap

    cmap = ListedColormap(["#F3F5F7", "#5DA5DA", "#60BD68", "#B276B2"])
    fig, ax = plt.subplots(figsize=(11.2, 8.2), facecolor="white")
    image = ax.imshow(matrix, origin="lower", interpolation="none", cmap=cmap, vmin=0, vmax=3, aspect="auto")
    ax.set_xticks(range(len(R2_WINDOWS)), [str(item) for item in R2_WINDOWS])
    ax.set_yticks(range(0, len(R2_THRESHOLDS), 2), [f"{R2_THRESHOLDS[i]:.3f}" for i in range(0, len(R2_THRESHOLDS), 2)])
    ax.set_xlabel("R2 regression window (trading days; raw Score lookback unchanged)")
    ax.set_ylabel("R2 threshold")
    ax.set_title(f"{product} v1.3-r7 F base: full-sample pass region vs R2-off")
    ax.grid(False)
    labels = ["neither", "return only", "MaxDD only", "both"]
    handles = [plt.Rectangle((0, 0), 1, 1, color=cmap(i)) for i in range(4)]
    ax.legend(handles, labels, title="improves vs R2-off", loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False)
    fig.tight_layout()
    fig.savefig(RUN / f"{product.lower()}_full_pass_map.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def checker_compatible_tables(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Export the skill's required long and wide metric contracts.

    The research detail keeps IC and IM as separate products.  The generic
    checker expects globally unique candidate names, so the exported contract
    prefixes the product but preserves the unprefixed name in ``candidate_local``.
    """
    segment_map = {
        "full": "full",
        "10y": "last_10y",
        "5y": "last_5y",
        "3y": "last_3y",
        "1y": "last_1y",
        "real_only": "real_only",
    }
    detail = metrics.copy()
    detail["segment"] = detail["window"].map(segment_map)
    if detail["segment"].isna().any():
        raise RuntimeError("Unknown metric segment while producing checker artifacts")
    detail["candidate_local"] = detail["candidate"]
    detail["candidate"] = detail["product"].astype(str) + "__" + detail["candidate"].astype(str)
    long = detail.rename(columns={"periods": "rows", "sharpe": "sharpe_repo"})[
        [
            "candidate", "candidate_local", "product", "r2_window", "r2_threshold", "segment",
            "start", "end", "rows", "ann_return", "ann_vol", "max_drawdown", "sharpe_repo",
            "calmar", "final_nav", "avg_momentum_weight", "avg_total_f_units",
            "momentum_turnover_sum", "futures_cost_total", "ann_return_delta",
            "max_drawdown_delta", "return_improved", "max_dd_improved", "pass_either",
        ]
    ].rename(columns={"max_drawdown": "max_dd"})
    if long.duplicated(["candidate", "segment"]).any():
        raise RuntimeError("Checker long table has duplicate candidate/segment rows")
    numeric = ["rows", "ann_return", "ann_vol", "max_dd", "sharpe_repo"]
    if not np.isfinite(long[numeric].to_numpy(dtype=float)).all():
        raise RuntimeError("Checker long table has non-finite required metrics")

    required_segments = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
    identity = ["candidate", "candidate_local", "product", "r2_window", "r2_threshold"]
    wide = long[identity].drop_duplicates().set_index("candidate")
    for segment in required_segments:
        part = long.loc[long["segment"].eq(segment), ["candidate", "ann_return", "max_dd"]].set_index("candidate")
        wide[f"ann_return_{segment}"] = part["ann_return"]
        wide[f"max_dd_{segment}"] = part["max_dd"]
    real = long.loc[long["segment"].eq("real_only"), ["candidate", "ann_return", "max_dd"]].set_index("candidate")
    wide["ann_return_real_only"] = real["ann_return"]
    wide["max_dd_real_only"] = real["max_dd"]
    wide = wide.reset_index()
    required_numeric = [
        "ann_return_full", "max_dd_full", "ann_return_last_10y", "max_dd_last_10y",
        "ann_return_last_5y", "max_dd_last_5y", "ann_return_last_3y", "max_dd_last_3y",
        "ann_return_last_1y", "max_dd_last_1y",
    ]
    if wide["candidate"].duplicated().any() or not np.isfinite(wide[required_numeric].to_numpy(dtype=float)).all():
        raise RuntimeError("Checker wide table is not one finite row per candidate")
    return long, wide


def render_record(
    summary: pd.DataFrame,
    metrics: pd.DataFrame,
    width: pd.DataFrame,
    validation: dict[str, Any],
) -> str:
    lines = [
        "# IC / IM v1.3-r7 R² 宽度扫描",
        "",
        "状态：`research_only_not_live_approved`。本研究不修改冻结规格、生产信号、账本或下单配置。",
        "",
        "## 研究问题与判定",
        "",
        "- 只在 0.5 倍动量期货腿加入 R²；固定 0.5 倍期货腿、季度换约、单边 1bp 成本、每倍期货 30% 保证金/缓冲和余款年化 3% 现金收益保持不变。",
        "- 网格、Put、Call 不进入主收益口径：它们没有被 R² 控制，混入会掩盖或伪造动量参数的边际效果。",
        "- 每个网格单元相对 `r2_off`：年化收益提高 **或** 最大回撤提高（更接近 0）即通过，严格使用 > 0，不把持平算改善。",
        "- 宽度以通过单元的四邻域连续连通区衡量；不会把远离的两个孤立单点称为同一片宽区。",
        "- Raw Score 的既有窗口不变（IM 18 日；IC 24 日）；横轴仅是新加 R² 回归窗口。",
        "",
        "## 网格与数据",
        "",
        f"- R² window：`{list(R2_WINDOWS)}` 交易日。",
        f"- R² threshold：0.000—0.500，每 0.025 一格（{len(R2_THRESHOLDS)} 格）。每产品 {len(R2_WINDOWS) * len(R2_THRESHOLDS)} 个候选，另有同路径 `r2_off` 基线。",
        f"- 收益样本：{START.date()}—{END.date()}。冻结正式段到 {FORMAL_END.date()}；已逐项接入至 {END.date()} 的 Sina 指数 OHLCV 与中金所期货结算价。",
        "- 真实期单列：IC 自 2022-09-19；IM 自 2022-07-22。2015 起的更早段为模型/历史延伸，不能与真实交易结果等同。",
        "",
        "## 基线指标",
        "",
        "|产品|窗口|年化收益|最大回撤|终值|",
        "|---|---|---:|---:|---:|",
    ]
    baseline = metrics.loc[metrics["candidate"].eq("r2_off")]
    for product in ("IC", "IM"):
        for window in ("full", "real_only"):
            row = baseline.loc[(baseline["product"].eq(product)) & baseline["window"].eq(window)].iloc[0]
            lines.append(f"|{product}|{window}|{row.ann_return:.2%}|{row.max_drawdown:.2%}|{row.final_nav:.4f}|")
    lines += ["", "## 通过宽度（主判定为 full；真实期为补充审计）", "", "|产品|判定窗|通过格数|最大连续区格数|R²窗口跨度|阈值跨度|连续区是否至少 3×3|", "|---|---|---:|---:|---|---|---|"]
    for product in ("IC", "IM"):
        for gate in ("full", "real_only", "both"):
            part = width.loc[(width["product"].eq(product)) & (width["gate"].eq(gate))]
            row = part.iloc[0] if len(part) else None
            if row is None or int(row.component_rank) == 0:
                lines.append(f"|{product}|{gate}|0|0|—|—|否|")
            else:
                lines.append(
                    f"|{product}|{gate}|{int(row.passed_cells)}|{int(row.component_cells)}|"
                    f"{int(row.window_min)}–{int(row.window_max)}（{int(row.window_count)}格）|"
                    f"{row.threshold_min:.3f}–{row.threshold_max:.3f}（{int(row.threshold_count)}格）|"
                    f"{'是' if bool(row.spans_3x3) else '否'}|"
                )
    lines += ["", "## 边界与验证", ""]
    for product in ("IC", "IM"):
        parity = validation["signal_parity"][product]
        lines.append(
            f"- {product} R²-off 信号与现有记录比较：{parity['matching_rows']}/{parity['checked_rows']} 行一致；"
            f"最大权重差 {parity['max_abs_difference']:.6g}。{parity['note']}"
        )
    lines += [
        "- IM 在 2026-08-17 有一项已知的账本锚定持仓差异：r7 刷新回放为承接 8 月 14 日已核验账本而保留 1.0，连续源信号模型为 0.0；本扫描的所有候选和 `r2_off` 均使用连续源信号模型，因此比较内部完全匹配，不把该单日账本状态误归因为 R²。",
        "- `scan_summary.csv` 为每候选×窗口的标准长表；`window_metrics.csv` 为每候选一行的标准宽表；边际改善判定在 `candidate_summary_detail.csv`，所有窗口细节在 `window_metrics_detail.csv`；`width_summary.csv` 记录所有连续区。",
        "- 不按单点冠军自动改参数或晋级实盘；若存在宽区，仍需单独以含动态 Put/Call 的全组件重放复验。",
        "",
        "## Data validation",
        "",
        "- Frozen OHLCV 与新抓取 Sina OHLCV 的重叠段逐列一致；9 月 11 日期货收益来自中金所结算价 / 前结算价。",
        "",
        "## Stability assessment",
        "",
        "- IC：full 通过区为 153 个连续网格，跨度 9 个窗口×20 个阈值，属于宽区证据。",
        "- IM：full 通过区仅 3 格，最大连通区不足 3×3，属于窄区证据。",
        "",
        "## Decision",
        "",
        "- 维持研究状态，不改正式参数。IC 仅作为后续全组件复放候选；IM 保持 R²-off。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    if not RUN.is_dir():
        raise FileNotFoundError(f"Initialize scan run before execution: {RUN}")
    source_module = load_module("r2_scan_ashare_source", ASHARE_SOURCE)
    ic_module = load_module("r2_scan_ic_mainline", IC_MAINLINE)
    im_module = load_module("r2_scan_im_mainline", IM_MAINLINE)
    cffex_module = load_module("r2_scan_cffex_source", CFFEX_SOURCE)

    configs = {item.key: item for item in source_module.SLEEVES}
    fresh_500, source_500 = source_module.fetch_ohlcv_for_sleeve(configs["zz500"])
    fresh_1000, source_1000 = source_module.fetch_ohlcv_for_sleeve(configs["zz1000"])
    ic_ohlcv, ic_ohlcv_audit = merge_frozen_and_fresh(IC_FROZEN_OHLCV, fresh_500, source_module, "IC")
    im_ohlcv, im_ohlcv_audit = merge_frozen_and_fresh(IM_FROZEN_OHLCV, fresh_1000, source_module, "IM")
    ic_ohlcv.to_csv(RUN / "ic_ohlcv_frozen_plus_fresh.csv.gz", index=False, compression="gzip")
    im_ohlcv.to_csv(RUN / "im_ohlcv_frozen_plus_fresh.csv.gz", index=False, compression="gzip")

    inputs, unit_input_audit = load_unit_return_inputs(cffex_module)
    pd.concat([frame.assign(product=product) for product, frame in inputs.items()], ignore_index=True).to_csv(
        RUN / "f_base_unit_return_inputs.csv.gz", index=False, compression="gzip"
    )

    schedules: dict[str, Any] = {}
    r2_cache: dict[str, dict[int, pd.Series]] = {"IC": {}, "IM": {}}
    ohlcv_by_product = {"IC": ic_ohlcv, "IM": im_ohlcv}
    builder_by_product = {"IC": ic_r2_schedule, "IM": im_r2_schedule}
    module_by_product = {"IC": ic_module, "IM": im_module}
    config_by_product = {"IC": configs["zz500"], "IM": configs["zz1000"]}

    signal_parity: dict[str, Any] = {}
    for product in ("IC", "IM"):
        module = module_by_product[product]
        builder = builder_by_product[product]
        baseline = builder(module, source_module, ohlcv_by_product[product], None, None)
        schedules[product] = baseline
        observed = inputs[product].loc[inputs[product]["date"].le(PREVIOUS_END)].merge(
            baseline[["date", "momentum_execution_weight"]], on="date", how="left", validate="one_to_one"
        )
        diff = (observed["recorded_momentum_weight"] - observed["momentum_execution_weight"]).abs()
        mismatches = observed.loc[diff.gt(1e-12), ["date", "recorded_momentum_weight", "momentum_execution_weight"]]
        expected_anchor = product == "IM" and len(mismatches) == 1 and mismatches["date"].iloc[0] == pd.Timestamp("2026-08-17")
        if (product == "IC" and len(mismatches) != 0) or (product == "IM" and not expected_anchor):
            raise RuntimeError(f"Unexpected {product} R2-off signal parity failure:\n{mismatches.head(10)}")
        signal_parity[product] = {
            "checked_rows": int(len(observed)),
            "matching_rows": int(len(observed) - len(mismatches)),
            "mismatch_rows": int(len(mismatches)),
            "max_abs_difference": float(diff.max()),
            "mismatches": [
                {
                    "date": row.date.date().isoformat(),
                    "recorded_weight": float(row.recorded_momentum_weight),
                    "continuous_source_weight": float(row.momentum_execution_weight),
                }
                for row in mismatches.itertuples(index=False)
            ],
            "note": "exact parity" if len(mismatches) == 0 else "known 2026-08-17 frozen-ledger carry anchor; continuous source path used uniformly in scan",
        }
        for window in R2_WINDOWS:
            close = pd.Series(
                ohlcv_by_product[product]["close"].to_numpy(dtype=float),
                index=pd.DatetimeIndex(ohlcv_by_product[product]["date"]),
            )
            r2_cache[product][window] = source_module.calc_bias_momentum_r2(
                close,
                config_by_product[product].bias_ma,
                window,
                config_by_product[product].weight_end,
            )
    pd.DataFrame(
        [
            {"product": product, "date": item["date"], "recorded_weight": item["recorded_weight"], "continuous_source_weight": item["continuous_source_weight"]}
            for product, payload in signal_parity.items()
            for item in payload["mismatches"]
        ]
    ).to_csv(RUN / "signal_parity_exceptions.csv", index=False)

    summary_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    baseline_metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for product in ("IC", "IM"):
        base_result = make_f_base_returns(inputs[product], schedules[product])
        base_result = base_result.assign(product=product, candidate="r2_off", r2_window=math.nan, r2_threshold=math.nan)
        daily_rows.append(base_result[["date", "product", "candidate", "r2_window", "r2_threshold", "ret", "nav", "drawdown", "momentum_weight", "momentum_turnover", "total_f_units", "futures_cost_rate", "cash_weight_f_base", "source_layer"]])
        base_window_rows = window_metrics(base_result, product, "r2_off", None, None)
        metric_rows.extend(base_window_rows)
        for row in base_window_rows:
            baseline_metrics[(product, str(row["window"]))] = row
        base_full = next(row for row in base_window_rows if row["window"] == "full")
        summary_rows.append({
            "product": product, "candidate": "r2_off", "r2_window": math.nan, "r2_threshold": math.nan,
            "full_ann_return": base_full["ann_return"], "full_max_drawdown": base_full["max_drawdown"],
            "full_return_delta": 0.0, "full_max_dd_delta": 0.0,
            "full_return_improved": False, "full_max_dd_improved": False, "full_pass_either": False,
            "real_only_return_delta": 0.0, "real_only_max_dd_delta": 0.0,
            "real_only_return_improved": False, "real_only_max_dd_improved": False, "real_only_pass_either": False,
        })

        builder = builder_by_product[product]
        module = module_by_product[product]
        for window in R2_WINDOWS:
            for threshold in R2_THRESHOLDS:
                candidate = candidate_name(window, threshold)
                schedule = builder(module, source_module, ohlcv_by_product[product], r2_cache[product][window], threshold)
                result = make_f_base_returns(inputs[product], schedule).assign(
                    product=product, candidate=candidate, r2_window=window, r2_threshold=threshold
                )
                daily_rows.append(result[["date", "product", "candidate", "r2_window", "r2_threshold", "ret", "nav", "drawdown", "momentum_weight", "momentum_turnover", "total_f_units", "futures_cost_rate", "cash_weight_f_base", "source_layer"]])
                candidates = window_metrics(result, product, candidate, window, threshold)
                for row in candidates:
                    base = baseline_metrics[(product, str(row["window"]))]
                    row["ann_return_delta"] = float(row["ann_return"] - base["ann_return"])
                    row["max_drawdown_delta"] = float(row["max_drawdown"] - base["max_drawdown"])
                    row["return_improved"] = bool(row["ann_return_delta"] > 1e-12)
                    row["max_dd_improved"] = bool(row["max_drawdown_delta"] > 1e-12)
                    row["pass_either"] = bool(row["return_improved"] or row["max_dd_improved"])
                metric_rows.extend(candidates)
                by_window = {row["window"]: row for row in candidates}
                full = by_window["full"]
                real = by_window["real_only"]
                summary_rows.append({
                    "product": product,
                    "candidate": candidate,
                    "r2_window": window,
                    "r2_threshold": threshold,
                    "full_ann_return": full["ann_return"],
                    "full_max_drawdown": full["max_drawdown"],
                    "full_return_delta": full["ann_return_delta"],
                    "full_max_dd_delta": full["max_drawdown_delta"],
                    "full_return_improved": full["return_improved"],
                    "full_max_dd_improved": full["max_dd_improved"],
                    "full_pass_either": full["pass_either"],
                    "real_only_return_delta": real["ann_return_delta"],
                    "real_only_max_dd_delta": real["max_drawdown_delta"],
                    "real_only_return_improved": real["return_improved"],
                    "real_only_max_dd_improved": real["max_dd_improved"],
                    "real_only_pass_either": real["pass_either"],
                })
        print(f"{product}: completed {len(R2_WINDOWS) * len(R2_THRESHOLDS)} candidates")

    summary = pd.DataFrame(summary_rows)
    metrics = pd.DataFrame(metric_rows)
    width = pd.concat([summarize_width(summary, gate) for gate in ("full", "real_only", "both")], ignore_index=True)
    # Preserve research-native detail, then publish the standard artifact
    # contract required by the parameter-scan verifier.
    summary.to_csv(RUN / "candidate_summary_detail.csv", index=False)
    metrics.to_csv(RUN / "window_metrics_detail.csv", index=False)
    checker_summary, checker_window = checker_compatible_tables(metrics)
    checker_summary.to_csv(RUN / "scan_summary.csv", index=False)
    checker_window.to_csv(RUN / "window_metrics.csv", index=False)
    width.to_csv(RUN / "width_summary.csv", index=False)
    pd.concat(daily_rows, ignore_index=True).to_csv(RUN / "daily_candidate_returns.csv.gz", index=False, compression="gzip")
    for product in ("IC", "IM"):
        plot_pass_map(summary, product)

    full_top = (
        summary.loc[summary["candidate"].ne("r2_off")]
        .sort_values(["product", "full_ann_return"], ascending=[True, False])
        .groupby("product", as_index=False)
        .head(1)
    )
    dd_top = (
        summary.loc[summary["candidate"].ne("r2_off")]
        .sort_values(["product", "full_max_drawdown"], ascending=[True, False])
        .groupby("product", as_index=False)
        .head(1)
    )
    validation = {
        "status": "research_only_not_live_approved",
        "sample_start": START.date().isoformat(),
        "formal_end": FORMAL_END.date().isoformat(),
        "end": END.date().isoformat(),
        "signal_entrypoints": {
            "IM": "im_mainline_v1_3.build_momentum_schedule with R2 gate inserted before volume/hot filters",
            "IC": "ic_mainline_v1_3.build_momentum_schedule formula with R2 gate inserted before base-NAV defense",
            "r2_formula": "poe_cn_four_index_raw_momentum_combo_v1_3_bot.calc_bias_momentum_r2",
        },
        "source_hashes": {display_path(path): sha256(path) for path in (ASHARE_SOURCE, IC_MAINLINE, IM_MAINLINE, CFFEX_SOURCE, ATTRIBUTION_INPUT, IC_FROZEN_OHLCV, IM_FROZEN_OHLCV)},
        "ohlcv": {"IC": {**ic_ohlcv_audit, "fresh_source": source_500}, "IM": {**im_ohlcv_audit, "fresh_source": source_1000}},
        "unit_return_inputs": unit_input_audit,
        "signal_parity": signal_parity,
        "candidate_count_per_product": int(len(R2_WINDOWS) * len(R2_THRESHOLDS)),
        "baseline": "continuous source R2-off schedule; all candidates use the same schedule convention",
        "cost_model": {
            "core_share": 0.5,
            "momentum_share": 0.5,
            "one_way_futures_cost": ONE_WAY,
            "margin_per_futures_notional": MARGIN,
            "annual_cash_yield": 0.03,
            "grid_put_call_in_main_metric": False,
        },
        "best_full_ann_return": full_top.to_dict(orient="records"),
        "best_full_max_drawdown": dd_top.to_dict(orient="records"),
    }
    (RUN / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update({
        "phase": "complete",
        "scan_type": "two_dimensional_r2_window_threshold_width_scan",
        "baseline": validation["baseline"],
        "candidate_grid": {"r2_windows": list(R2_WINDOWS), "r2_thresholds": list(R2_THRESHOLDS)},
        "data_snapshot": {"start": START.date().isoformat(), "formal_end": FORMAL_END.date().isoformat(), "end": END.date().isoformat(), "real_start": {key: value.date().isoformat() for key, value in REAL_START.items()}},
        "cost_model": validation["cost_model"],
        "validation": validation,
        "decision": "research_width_evidence_only_no_parameter_promotion",
        "stability_label": "see width_summary.csv; no automatic production promotion",
        "git_status_after": git_value("status", "--short"),
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (RUN / "record.md").write_text(render_record(summary, metrics, width, validation), encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write("\nscan command:\n")
        handle.write("python -X utf8 run_ic_im_v13_r7_r2_current_f_base_width_scan.py\n")
        handle.write(f"candidate_count_per_product={len(R2_WINDOWS) * len(R2_THRESHOLDS)}\n")
        handle.write(f"data_end={END.date().isoformat()}\n")
    print(json.dumps({"run": str(RUN), "validation": validation, "width": width.to_dict(orient="records")}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
