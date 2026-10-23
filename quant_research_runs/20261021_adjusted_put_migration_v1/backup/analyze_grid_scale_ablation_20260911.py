from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / "quant_param_scan_runs" / "20260911_ic_im_grid_scale_ablation_v1"
REFRESH_DIR = ROOT / "outputs" / "nav_r7_complete_refresh_20260911"

IC_FORMAL = (
    ROOT
    / "quant_param_scan_runs"
    / "20260904_ic_v13_full_roll_tenor_timing_v2"
    / "candidate_checkpoints"
    / "quarter_T3_fixed.csv.gz"
)
IC_TARGET = ROOT / "outputs" / "ic_mainline_v1_3" / "target_schedule.csv.gz"
IM_FORMAL = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz"
IM_COMPONENTS = (
    ROOT
    / "quant_param_scan_runs"
    / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_put_coverage_scope"
    / "daily_outputs"
    / "coverage_candidates.csv.gz"
)
IM_PARENT_COMPONENTS = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_im_grid160_put_carry_scan_v23"
    / "daily_outputs"
    / "daily_candidates.csv.gz"
)
IC_TAIL = REFRESH_DIR / "ic_tail_daily.csv"
IM_TAIL = REFRESH_DIR / "im_tail_daily.csv"
HISTORICAL_SIGNALS = REFRESH_DIR / "historical_signals.json"

GRID_SCALES = (1.0, 0.5, 0.0)
MARGIN_RATE = 0.30
CASH_ANNUAL = 0.03
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252.0) - 1.0

LABELS = {
    1.0: "original_grid_1.0",
    0.5: "grid_0.5",
    0.0: "no_grid_0.0",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def load_signal_grid_state() -> dict[tuple[str, pd.Timestamp], float]:
    with HISTORICAL_SIGNALS.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    result: dict[tuple[str, pd.Timestamp], float] = {}
    for date_text, day in raw.items():
        date = pd.Timestamp(date_text)
        for product in ("IC", "IM"):
            signal = day.get(product, {}) or {}
            value = signal.get("grid_current", signal.get("grid_held_eod", signal.get("grid_target", 0.0)))
            result[(product, date)] = float(value or 0.0)
    return result


def prepare_ic() -> tuple[pd.DataFrame, dict[str, float]]:
    formal = read_csv(IC_FORMAL)
    target = read_csv(IC_TARGET)
    target_cols = ["date", "grid_held_eod", "grid_ic_units", "total_ic_units"]
    target = target[target_cols].drop_duplicates("date")
    formal = formal.merge(target, on="date", how="left", validate="one_to_one")
    if formal["grid_held_eod"].isna().any():
        raise RuntimeError("IC formal rows missing grid state")
    if (formal["total_units"] - formal["total_ic_units"]).abs().max() > 1e-12:
        raise RuntimeError("IC total unit parity failed")

    tail = read_csv(IC_TAIL)
    signals = load_signal_grid_state()
    tail["grid_units"] = [signals.get(("IC", date), 0.0) for date in tail["date"]]
    tail["grid_net_increment"] = 0.0
    tail["data_layer"] = tail.get("data_layer", "real")

    formal_out = formal[
        [
            "date",
            "ret",
            "cash_weight",
            "total_units",
            "data_layer",
            "futures_gross_ret",
            "grid_net_increment",
            "put_pnl_ret",
            "put_cost_rate",
            "put_mark_fraction",
            "put_qty",
            "grid_held_eod",
        ]
    ].rename(columns={"grid_held_eod": "grid_units"})
    tail_out = tail[
        [
            "date",
            "ret",
            "cash_weight",
            "total_units",
            "data_layer",
            "futures_gross_ret",
            "grid_net_increment",
            "put_pnl_ret",
            "put_cost_rate",
            "put_mark_fraction",
            "put_qty",
            "grid_units",
        ]
    ]
    current = pd.concat([formal_out, tail_out], ignore_index=True).sort_values("date")
    if current["date"].duplicated().any():
        raise RuntimeError("IC formal/tail overlap")
    current = current[current["data_layer"].astype(str).str.startswith("real")].copy()
    current["grid_units"] = current["grid_units"].astype(float)
    current["grid_net_increment"] = current["grid_net_increment"].astype(float)

    records: list[pd.DataFrame] = []
    q1_formula_error = 0.0
    for scale in GRID_SCALES:
        row = current.copy()
        row["product"] = "IC"
        row["candidate"] = LABELS[scale]
        row["grid_scale"] = scale
        row["grid_units_scaled"] = row["grid_units"] * scale
        row["total_units_scaled"] = row["total_units"] - row["grid_units"] + row["grid_units_scaled"]
        row["cash_weight_scaled"] = row["cash_weight"] + MARGIN_RATE * (1.0 - scale) * row["grid_units"]
        row["grid_net_increment_scaled"] = scale * row["grid_net_increment"]
        base_ret = row["ret"] - row["grid_net_increment"] - row["cash_weight"] * CASH_DAILY
        row["ret_variant"] = base_ret + row["grid_net_increment_scaled"] + row["cash_weight_scaled"] * CASH_DAILY
        if scale == 1.0:
            q1_formula_error = float((row["ret_variant"] - row["ret"]).abs().max())
            row["ret_variant"] = row["ret"]
        records.append(row)

    result = pd.concat(records, ignore_index=True)
    audit = {
        "real_start": current["date"].min().date().isoformat(),
        "end": current["date"].max().date().isoformat(),
        "rows": int(len(current)),
        "q1_formula_max_abs_error": q1_formula_error,
    }
    return result, audit


def prepare_im() -> tuple[pd.DataFrame, dict[str, float]]:
    formal = read_csv(IM_FORMAL)
    components = read_csv(IM_COMPONENTS)
    components = components[components["candidate"].eq("core_only_current")].copy()
    if components["date"].duplicated().any():
        raise RuntimeError("IM current component rows are not unique")
    common = formal[["date", "ret", "data_layer"]].merge(
        components[["date", "ret", "data_layer"]].rename(
            columns={"ret": "component_ret", "data_layer": "component_data_layer"}
        ),
        on="date",
        how="inner",
        validate="one_to_one",
    )
    formal_component_error = float((common["ret"] - common["component_ret"]).abs().max())
    if formal_component_error > 1e-12:
        raise RuntimeError(f"IM formal/component parity failed: {formal_component_error}")

    parent = read_csv(IM_PARENT_COMPONENTS)
    parent = parent[parent["candidate"].eq("real_actual_basis__current_4tier_mom3")].copy()
    parent = parent[
        [
            "date",
            "overlay_gross_ret",
            "overlay_basis_ret",
            "overlay_cost_rate",
            "overlay_held_eod",
        ]
    ]
    parent["grid_gross_component"] = parent["overlay_gross_ret"].astype(float) + parent["overlay_basis_ret"].astype(float)
    parent = parent.rename(columns={"overlay_held_eod": "parent_grid_units"})

    component_cols = [
        "date",
        "ret",
        "cash_weight",
        "total_units",
        "put_qty_normalized",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "call_pnl_ret",
        "call_cost_rate",
        "futures_gross_ret",
        "futures_cost_rate",
        "grid_units",
        "data_layer",
    ]
    formal_current = components[component_cols].merge(parent, on="date", how="left", validate="one_to_one")
    formal_current["grid_gross_component"] = formal_current["grid_gross_component"].fillna(0.0)
    formal_current["overlay_cost_rate"] = formal_current["overlay_cost_rate"].fillna(0.0)
    formal_current["parent_grid_units"] = formal_current["parent_grid_units"].fillna(0.0)
    formal_current["data_layer"] = formal_current["data_layer"].astype(str)

    tail = read_csv(IM_TAIL)
    signals = load_signal_grid_state()
    tail["grid_units"] = [signals.get(("IM", date), 0.0) for date in tail["date"]]
    tail["grid_gross_component"] = 0.0
    tail["overlay_cost_rate"] = 0.0
    tail["parent_grid_units"] = tail["grid_units"]
    tail["put_qty_normalized"] = tail["put_qty_normalized"].astype(float)
    tail = tail[
        [
            "date",
            "ret",
            "cash_weight",
            "total_units",
            "put_qty_normalized",
            "put_pnl_ret",
            "put_cost_rate",
            "put_mark_fraction",
            "call_pnl_ret",
            "call_cost_rate",
            "futures_gross_ret",
            "futures_cost_rate",
            "grid_units",
            "data_layer",
            "grid_gross_component",
            "overlay_cost_rate",
            "parent_grid_units",
        ]
    ]

    current = pd.concat([formal_current, tail], ignore_index=True).sort_values("date")
    if current["date"].duplicated().any():
        raise RuntimeError("IM formal/tail overlap")
    current = current[current["data_layer"].astype(str).str.startswith("real")].copy()
    if (current["grid_units"] - current["parent_grid_units"]).abs().max() > 1e-12:
        raise RuntimeError("IM grid state parity with parent component failed")

    records: list[pd.DataFrame] = []
    q1_formula_error = 0.0
    for scale in GRID_SCALES:
        row = current.copy()
        row["product"] = "IM"
        row["candidate"] = LABELS[scale]
        row["grid_scale"] = scale
        row["grid_units_scaled"] = row["grid_units"] * scale
        row["total_units_scaled"] = row["total_units"] - row["grid_units"] + row["grid_units_scaled"]
        row["cash_weight_scaled"] = (
            row["cash_weight"] + MARGIN_RATE * (1.0 - scale) * row["grid_units"]
        ).clip(lower=0.0)
        row["futures_gross_scaled"] = row["futures_gross_ret"] - (1.0 - scale) * row["grid_gross_component"]
        row["futures_cost_scaled"] = row["futures_cost_rate"] - (1.0 - scale) * row["overlay_cost_rate"]
        pre_cash = (
            (1.0 + row["futures_gross_scaled"] + row["put_pnl_ret"] + row["call_pnl_ret"])
            * (1.0 - row["futures_cost_scaled"])
            * (1.0 - row["put_cost_rate"])
            * (1.0 - row["call_cost_rate"])
            - 1.0
        )
        row["ret_variant"] = pre_cash + row["cash_weight_scaled"] * CASH_DAILY
        if scale == 1.0:
            q1_formula_error = float((row["ret_variant"] - row["ret"]).abs().max())
            row["ret_variant"] = row["ret"]
        records.append(row)

    result = pd.concat(records, ignore_index=True)
    audit = {
        "real_start": current["date"].min().date().isoformat(),
        "end": current["date"].max().date().isoformat(),
        "rows": int(len(current)),
        "formal_component_max_abs_error": formal_component_error,
        "q1_formula_max_abs_error": q1_formula_error,
    }
    return result, audit


def window_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    end = daily["date"].max()
    real_start = daily["date"].min()
    windows = {
        "real_full": real_start,
        "10Y": end - pd.DateOffset(years=10),
        "5Y": end - pd.DateOffset(years=5),
        "3Y": end - pd.DateOffset(years=3),
        "1Y": end - pd.DateOffset(years=1),
    }
    for (product, candidate), group in daily.groupby(["product", "candidate"], sort=False):
        group = group.sort_values("date")
        for window, requested_start in windows.items():
            available = requested_start >= real_start if window != "real_full" else True
            if not available:
                rows.append(
                    {
                        "product": product,
                        "candidate": candidate,
                        "grid_scale": float(group["grid_scale"].iloc[0]),
                        "window": window,
                        "available": False,
                        "reason": "真实数据起点晚于该窗口起点",
                    }
                )
                continue
            sample = group[group["date"].ge(requested_start)].copy()
            returns = sample["ret_variant"].astype(float)
            nav = (1.0 + returns).cumprod()
            drawdown = nav / nav.cummax() - 1.0
            final_nav = float(nav.iloc[-1])
            rows.append(
                {
                    "product": product,
                    "candidate": candidate,
                    "grid_scale": float(group["grid_scale"].iloc[0]),
                    "window": window,
                    "available": True,
                    "reason": "",
                    "start": sample["date"].iloc[0].date().isoformat(),
                    "end": sample["date"].iloc[-1].date().isoformat(),
                    "rows": int(len(sample)),
                    "total_return": final_nav - 1.0,
                    "annualized_return": final_nav ** (252.0 / len(sample)) - 1.0,
                    "annualized_volatility": float(returns.std(ddof=1) * np.sqrt(252.0)),
                    "sharpe_repo": (
                        float(returns.mean() / returns.std(ddof=1) * np.sqrt(252.0))
                        if float(returns.std(ddof=1)) > 0
                        else 0.0
                    ),
                    "max_drawdown": float(drawdown.min()),
                    "max_drawdown_date": sample.loc[drawdown.idxmin(), "date"].date().isoformat(),
                }
            )
    result = pd.DataFrame(rows)
    baseline = result[result["candidate"].eq(LABELS[1.0])][
        ["product", "window", "annualized_return", "max_drawdown"]
    ].rename(
        columns={
            "annualized_return": "original_annualized_return",
            "max_drawdown": "original_max_drawdown",
        }
    )
    result = result.merge(baseline, on=["product", "window"], how="left")
    result["annualized_return_delta_vs_original"] = result["annualized_return"] - result["original_annualized_return"]
    result["max_drawdown_delta_vs_original"] = result["max_drawdown"] - result["original_max_drawdown"]
    return result


def standardize_metrics(metrics: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, str]]]:
    segment_map = {
        "real_full": "full",
        "10Y": "last_10y",
        "5Y": "last_5y",
        "3Y": "last_3y",
        "1Y": "last_1y",
    }
    long = metrics.copy()
    long["segment"] = long["window"].map(segment_map)
    long["candidate_id"] = long["product"] + "__" + long["candidate"]
    summary_rows: list[dict[str, object]] = []
    unavailable: dict[str, dict[str, str]] = {}
    for _, row in long.iterrows():
        candidate_id = str(row["candidate_id"])
        segment = str(row["segment"])
        available = bool(row["available"])
        item: dict[str, object] = {
            "candidate": candidate_id,
            "product": row["product"],
            "display_candidate": row["candidate"],
            "grid_scale": row["grid_scale"],
            "segment": segment,
            "available": available,
            "start": row.get("start", "") if available else "",
            "end": row.get("end", "") if available else "",
            "rows": int(row["rows"]) if available else 0,
            "requested_window_available": available,
            "clipped_to_available_history": False,
            "reason": row.get("reason", "") if not available else "",
        }
        if available:
            item.update(
                {
                    "total_return": row["total_return"],
                    "ann_return": row["annualized_return"],
                    "ann_vol": row["annualized_volatility"],
                    "sharpe_repo": row["sharpe_repo"],
                    "max_dd": row["max_drawdown"],
                }
            )
        else:
            item.update(
                {
                    "total_return": "N/A",
                    "ann_return": "N/A",
                    "ann_vol": "N/A",
                    "sharpe_repo": "N/A",
                    "max_dd": "N/A",
                }
            )
            if segment != "full":
                unavailable.setdefault(candidate_id, {})[segment] = str(row["reason"])
        summary_rows.append(item)

    summary = pd.DataFrame(summary_rows)
    pivot_rows: list[dict[str, object]] = []
    for candidate_id, group in long.groupby("candidate_id", sort=False):
        first = group.iloc[0]
        item: dict[str, object] = {
            "candidate": candidate_id,
            "product": first["product"],
            "display_candidate": first["candidate"],
            "grid_scale": first["grid_scale"],
        }
        for window, segment in segment_map.items():
            match = group[group["segment"].eq(segment)].iloc[0]
            for source, target in (
                ("annualized_return", "ann_return"),
                ("annualized_volatility", "ann_vol"),
                ("sharpe_repo", "sharpe_repo"),
                ("max_drawdown", "max_dd"),
            ):
                key = f"{target}_{segment}"
                item[key] = match[source] if bool(match["available"]) else "N/A"
        pivot_rows.append(item)
    pivot = pd.DataFrame(pivot_rows)
    return summary, pivot, unavailable


def write_record(
    metrics: pd.DataFrame,
    ic_audit: dict[str, float],
    im_audit: dict[str, float],
    hashes: dict[str, str],
) -> None:
    real_metrics = metrics[metrics["window"].eq("real_full") & metrics["available"].eq(True)].copy()
    lines = [
        "# Grid Scale Ablation Record",
        "",
        "## Research question",
        "",
        "Compare the current v1.3-r7 IC/IM path at grid scale 1.0 (original), 0.5, and 0.0 (no grid).",
        "All signal dates, futures/Put/Call paths, cash-return assumption, and the refreshed tail through the latest complete trading day are held fixed.",
        "",
        "## Scope and definition",
        "",
        f"- Real-data scope: IC {ic_audit['real_start']} to {ic_audit['end']}; IM {im_audit['real_start']} to {im_audit['end']}; latest complete day 2026-09-10.",
        "- grid=0.5: grid P&L, grid trading cost, grid margin usage, and grid unit exposure are scaled to 50%; entry/exit and roll calendar are unchanged.",
        "- grid=0: grid contribution is removed; the released 30% margin per grid unit becomes cash. Core, momentum, Put, Call, and all non-grid costs remain unchanged.",
        "- This is research-only. It does not change the registered mainline or authorize trading.",
        "",
        "## Data snapshot",
        "",
        "- Source is the refreshed local formal replay plus the same current-entrypoint real-data tail; latest complete trading day is 2026-09-10.",
        "- 5Y and 10Y real-data windows are explicitly unavailable because the real-data starts are 2022-09-19 (IC) and 2022-07-22 (IM).",
        "",
        "## Parity and audit",
        "",
        f"- IC formal current-component parity max abs return error: {ic_audit['q1_formula_max_abs_error']:.3e}.",
        f"- IM formal current-component parity max abs return error: {im_audit['formal_component_max_abs_error']:.3e}.",
        f"- IM reconstructed grid=1.0 formula max abs return error: {im_audit['q1_formula_max_abs_error']:.3e}.",
        "- The grid=1.0 candidate in the output is the original refreshed return series; the formula error is retained as an audit, not used to overwrite the baseline.",
        "",
        "## Real full-window results",
        "",
        "| product | candidate | annualized return | max drawdown |",
        "| --- | --- | ---: | ---: |",
    ]
    for _, row in real_metrics.sort_values(["product", "grid_scale"], ascending=[True, False]).iterrows():
        lines.append(
            f"| {row['product']} | {row['candidate']} | {row['annualized_return']:.4%} | {row['max_drawdown']:.4%} |"
        )
    lines.extend(
        [
            "",
            "## Inputs",
            "",
        ]
    )
    for name, digest in hashes.items():
        lines.append(f"- `{name}` SHA-256: `{digest}`")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "- Decision: comparison only; no promotion decision is made in this run.",
            "- Required next step before any production discussion: review the full/3Y/1Y drawdown and exposure trade-off against the registered mainline evidence.",
            "",
            "## Stability classification",
            "",
            "- Label: descriptive ablation, not a parameter-robustness scan; only three pre-specified grid scales were compared.",
            "- The candidate grid changes exposure sizing while holding the signal/calendar fixed; it is not an approval to promote a new mainline.",
        ]
    )
    (RUN_DIR / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    ic, ic_audit = prepare_ic()
    im, im_audit = prepare_im()
    daily = pd.concat([ic, im], ignore_index=True)
    daily["ret_variant"] = daily["ret_variant"].astype(float)
    if daily["ret_variant"].le(-1.0).any() or not np.isfinite(daily["ret_variant"]).all():
        raise RuntimeError("Variant returns contain invalid values")
    daily["nav"] = daily.groupby(["product", "candidate"], sort=False)["ret_variant"].transform(lambda s: (1.0 + s).cumprod())
    daily["drawdown"] = daily.groupby(["product", "candidate"], sort=False)["nav"].transform(
        lambda s: s / s.cummax() - 1.0
    )

    metric_columns = [
        "product",
        "date",
        "candidate",
        "grid_scale",
        "data_layer",
        "ret_variant",
        "nav",
        "drawdown",
        "grid_units",
        "grid_units_scaled",
        "total_units",
        "total_units_scaled",
        "cash_weight",
        "cash_weight_scaled",
    ]
    daily[metric_columns].sort_values(["product", "date", "grid_scale"], ascending=[True, True, False]).to_csv(
        RUN_DIR / "daily_candidates.csv.gz", index=False, compression="gzip"
    )

    metrics = window_metrics(daily)
    summary, window_table, unavailable_segments = standardize_metrics(metrics)
    metrics.to_csv(RUN_DIR / "window_metrics_long.csv", index=False)
    window_table.to_csv(RUN_DIR / "window_metrics.csv", index=False)
    summary.to_csv(RUN_DIR / "scan_summary.csv", index=False)

    source_paths = {
        "ic_formal": IC_FORMAL,
        "ic_target": IC_TARGET,
        "im_formal": IM_FORMAL,
        "im_components": IM_COMPONENTS,
        "im_parent_components": IM_PARENT_COMPONENTS,
        "ic_tail": IC_TAIL,
        "im_tail": IM_TAIL,
        "historical_signals": HISTORICAL_SIGNALS,
    }
    hashes = {name: sha256_file(path) for name, path in source_paths.items()}
    previous_meta = json.loads((RUN_DIR / "scan_meta.json").read_text(encoding="utf-8"))
    git_status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=False, capture_output=True, text=True
    ).stdout.strip()
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False, capture_output=True, text=True
    ).stdout.strip()
    git_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, check=False, capture_output=True, text=True
    ).stdout.strip()
    meta = {
        **previous_meta,
        "phase": "complete",
        "created_at": previous_meta.get("created_at") or pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "entrypoint": "analyze_grid_scale_ablation_20260911.py",
        "repo_root": str(ROOT),
        "git_branch": git_branch or previous_meta.get("git_branch", "main"),
        "git_commit": git_commit or previous_meta.get("git_commit", "unknown"),
        "git_status_before": previous_meta.get(
            "git_status_before", "pre-existing dirty worktree recorded at scan initialization"
        )
        or "pre-existing dirty worktree recorded at scan initialization",
        "git_status_after": git_status,
        "scan_type": "single_parameter_ablation",
        "parameter_group": "grid_scale",
        "baseline": {
            "IC": {"value": 1.0, "label": "IC__" + LABELS[1.0]},
            "IM": {"value": 1.0, "label": "IM__" + LABELS[1.0]},
        },
        "candidate_grid": [
            {
                "value": value,
                "labels": {product: f"{product}__{LABELS[value]}" for product in ("IC", "IM")},
            }
            for value in GRID_SCALES
        ],
        "real_data_scope": {
            "IC": {"start": ic_audit["real_start"], "end": ic_audit["end"], "rows": ic_audit["rows"]},
            "IM": {"start": im_audit["real_start"], "end": im_audit["end"], "rows": im_audit["rows"]},
        },
        "latest_complete_trading_day": "2026-09-10",
        "cash_annual": CASH_ANNUAL,
        "cash_daily": CASH_DAILY,
        "margin_rate_per_grid_unit": MARGIN_RATE,
        "repo_root": str(ROOT),
        "cost_and_execution": "same refreshed v1.3-r7 path; grid gross/cost/margin scaled, signal calendar unchanged",
        "cost_model": {
            "cash_annual": CASH_ANNUAL,
            "grid_cost": "existing formal grid cost scaled linearly with grid_scale",
            "non_grid_cost": "unchanged",
            "execution": "same current-entrypoint timing and real historical marks",
        },
        "data_snapshot": {
            "latest_complete_trading_day": "2026-09-10",
            "IC_real_start": ic_audit["real_start"],
            "IM_real_start": im_audit["real_start"],
            "source_sha256": hashes,
        },
        "parity": {"IC": ic_audit, "IM": im_audit},
        "source_sha256": hashes,
        "unavailable_segments": unavailable_segments,
        "outputs": {
            "daily_candidates": "daily_candidates.csv.gz",
            "scan_summary": "scan_summary.csv",
            "window_metrics": "window_metrics.csv",
            "window_metrics_long": "window_metrics_long.csv",
            "record": "record.md",
            "scan_meta": "scan_meta.json",
            "command_log": "command_log.txt",
        },
        "decision": "comparison_only_no_promotion",
        "stability_label": "descriptive_ablation_only",
        "research_only": True,
    }
    (RUN_DIR / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RUN_DIR / "command_log.txt").write_text(
        "python -X utf8 analyze_grid_scale_ablation_20260911.py\n", encoding="utf-8"
    )
    write_record(metrics, ic_audit, im_audit, hashes)

    print(json.dumps({"ic_audit": ic_audit, "im_audit": im_audit}, ensure_ascii=False, indent=2))
    print(summary[summary["segment"].eq("full")][["candidate", "ann_return", "max_dd"]].to_string(index=False))


if __name__ == "__main__":
    main()
