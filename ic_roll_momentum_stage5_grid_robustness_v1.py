from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "ic_roll_momentum_stage5_grid_robustness_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
STAGE4_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage4_grid_guidance_v1" / "daily_nav.csv.gz"
STAGE4_MANIFEST = ROOT / "outputs" / "ic_roll_momentum_stage4_grid_guidance_v1" / "run_manifest.json"
GRID_FROZEN = ROOT / "outputs" / "ic_put_grid_call_combined_v2" / "daily_candidates.csv.gz"
RUN = ROOT / "quant_param_scan_runs" / (
    "20260823_ic_roll_momentum_50_50_ic_roll_momentum_stage5_grid_robustness_v1_"
    "ic_valuation_grid_entry_exit_thresholds_and_grid_guidance"
)
DAILY_DIR = RUN / "daily_outputs"

SPEC_SHA256 = "f66c04618310aa5ba265ee2e1a08cb7cd7c9bd2715b8e20264727227aae0ad9b"
FROZEN_HASHES = {
    SPEC: SPEC_SHA256,
    STAGE4_DAILY: "d91ed155fbaa8cc2fe31820217a5b5d89747ef6b4a4e9fea1ed49d9ed51cc78b",
    STAGE4_MANIFEST: "fb61c299beca42a377c0ed37bea98fdcc2234f153ff7a650935abf8039d403a2",
    GRID_FROZEN: "15e38d5754f25bddf829b5fec1b8692c1d6a55a4af902385740f5f507ead15b2",
}

ENTRIES = (0.250, 0.375, 0.500)
EXITS = (0.875, 1.000, 1.125)
MODES = ("independent", "guided")
DEFAULT_LOW = 0.375
DEFAULT_HIGH = 1.000
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
REAL_PUT_START = pd.Timestamp("2022-09-19")
SEGMENTS = (
    ("full", None),
    ("last_10y", pd.DateOffset(years=10)),
    ("last_5y", pd.DateOffset(years=5)),
    ("last_3y", pd.DateOffset(years=3)),
    ("last_1y", pd.DateOffset(years=1)),
    ("real_put_period", "real"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def candidate_label(mode: str, low: float, high: float) -> str:
    return f"{mode}_L{int(round(low * 1000)):04d}_H{int(round(high * 1000)):04d}"


def candidate_definitions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"candidate": "bare_put_no_grid", "mode": "no_grid", "entry": np.nan, "exit": np.nan}
    ]
    for mode in MODES:
        for low in ENTRIES:
            for high in EXITS:
                rows.append(
                    {
                        "candidate": candidate_label(mode, low, high),
                        "mode": mode,
                        "entry": low,
                        "exit": high,
                    }
                )
    return rows


CANDIDATES = candidate_definitions()
DEFAULT_CANDIDATES = {
    mode: candidate_label(mode, DEFAULT_LOW, DEFAULT_HIGH) for mode in MODES
}


def verify_inputs() -> dict[str, str]:
    if not RUN.exists():
        raise FileNotFoundError(f"Initialized run folder missing: {RUN}")
    for name in (
        "scan_summary.csv", "window_metrics.csv", "cycle_attribution.csv",
        "leave_one_cycle_out.csv",
    ):
        if (RUN / name).exists():
            raise FileExistsError(f"Scan output already exists: {RUN / name}")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Specification sidecar mismatch")
    hashes: dict[str, str] = {}
    for path, expected in FROZEN_HASHES.items():
        if not path.exists():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen input changed: {path}: {actual} != {expected}")
        hashes[str(path.relative_to(ROOT))] = actual
    return hashes


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    base = pd.read_csv(STAGE4_DAILY, parse_dates=["date"], low_memory=False)
    base = base.sort_values("date").reset_index(drop=True)
    frozen = pd.read_csv(GRID_FROZEN, parse_dates=["date"], low_memory=False)
    market = frozen[frozen["candidate"].eq("model_grid_only")][
        ["date", "contract", "open", "settle", "pre_settle", "valuation_score", "roll_event"]
    ].sort_values("date").reset_index(drop=True)
    market = market.merge(
        base[["date", "momentum_weight"]], on="date", validate="one_to_one"
    )
    if len(base) != len(market) or not base["date"].equals(market["date"]):
        raise RuntimeError("Base/market calendar mismatch")
    required = ["open", "settle", "pre_settle", "valuation_score", "momentum_weight"]
    if market[required].isna().any().any():
        raise RuntimeError(f"Missing market inputs: {market[required].isna().sum().to_dict()}")
    return base, market


def simulate_grid(
    market: pd.DataFrame, low: float, high: float, mode: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if mode not in MODES:
        raise ValueError(mode)
    state = False
    pending: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    dates = list(pd.DatetimeIndex(market["date"]))
    cycle_counter = 0
    active_cycle = 0

    for index, row in enumerate(market.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        allowed = mode == "independent" or float(row.momentum_weight) > 0.0
        held_before = state
        buy = False
        sell = False
        blocked = False
        reason = ""
        signal_date: pd.Timestamp | pd.NaT = pd.NaT
        signal_score = np.nan

        if pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            signal_date = pd.Timestamp(pending["signal_date"])
            signal_score = float(pending["signal_score"])
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError("Duplicate grid buy")
                if allowed:
                    state = True
                    buy = True
                    cycle_counter += 1
                    active_cycle = cycle_counter
                    reason = "valuation_buy"
                else:
                    blocked = True
                    reason = "valuation_buy_momentum_off"
            else:
                if not state:
                    raise RuntimeError("Grid valuation sell while flat")
                state = False
                sell = True
                reason = "valuation_sell"
            pending = None

        if mode == "guided" and state and not allowed:
            state = False
            sell = True
            reason = "momentum_forced_exit"
            signal_date = dates[index - 1] if index > 0 else day
            signal_score = float(row.valuation_score)

        held_eod = state
        return_cycle = active_cycle if (held_before or held_eod or buy or sell) else 0
        if held_before and held_eod:
            gross = float(row.settle) / float(row.pre_settle) - 1.0
        elif not held_before and held_eod:
            gross = float(row.settle) / float(row.open) - 1.0
        elif held_before and not held_eod:
            gross = float(row.open) / float(row.pre_settle) - 1.0
        else:
            gross = 0.0
        trade_cost = ONE_WAY_COST * (int(buy) + int(sell))
        roll_cost = 2.0 * ONE_WAY_COST if held_eod and bool(row.roll_event) else 0.0

        if buy or sell or blocked:
            events.append(
                {
                    "mode": mode, "entry": low, "exit": high,
                    "candidate": candidate_label(mode, low, high),
                    "action": "blocked_buy" if blocked else ("buy" if buy else "sell"),
                    "reason": reason, "cycle_id": return_cycle,
                    "signal_date": signal_date, "signal_score": signal_score,
                    "execution_date": day, "momentum_weight": float(row.momentum_weight),
                    "contract": row.contract, "execution_open": float(row.open),
                }
            )

        rows.append(
            {
                "date": day, "mode": mode, "entry": low, "exit": high,
                "candidate": candidate_label(mode, low, high),
                "momentum_allowed": int(allowed), "cycle_id": return_cycle,
                "overlay_held_before": int(held_before), "overlay_held_eod": int(held_eod),
                "overlay_buy": int(buy), "overlay_sell": int(sell),
                "overlay_gross_ret": gross,
                "overlay_trade_cost_rate": trade_cost,
                "overlay_roll_cost_rate": roll_cost,
                "overlay_cost_rate": trade_cost + roll_cost,
            }
        )

        if sell:
            active_cycle = 0
        if pending is None:
            score = float(row.valuation_score)
            action = "buy" if (not state and score <= low + 1e-12) else None
            if state and score >= high - 1e-12:
                action = "sell"
            if action is not None and index + 1 < len(dates):
                pending = {
                    "action": action, "signal_date": day, "signal_score": score,
                    "execution_date": dates[index + 1],
                }

    daily = pd.DataFrame(rows)
    event_frame = pd.DataFrame(events)
    if mode == "guided":
        violation = daily["overlay_held_eod"].eq(1) & daily["momentum_allowed"].eq(0)
        if violation.any():
            raise RuntimeError("Guided grid held while momentum was off")
    return daily, event_frame


def compose(base: pd.DataFrame, overlay: pd.DataFrame | None, candidate: str) -> pd.DataFrame:
    result = base[
        [
            "date", "momentum_weight", "bare_put_no_grid_ret",
            "bare_put_no_grid_cash_weight", "roll50_momentum50_ic_units",
        ]
    ].copy()
    base_pre_cash = (
        result["bare_put_no_grid_ret"]
        - result["bare_put_no_grid_cash_weight"] * CASH_DAILY
    )
    if overlay is None:
        result["grid_held_eod"] = 0.0
        result["grid_gross_ret"] = 0.0
        result["grid_cost_rate"] = 0.0
        result["cycle_id"] = 0
    else:
        if len(overlay) != len(result) or not overlay["date"].equals(result["date"]):
            raise RuntimeError(f"Overlay calendar mismatch: {candidate}")
        result["grid_held_eod"] = overlay["overlay_held_eod"].to_numpy(dtype=float)
        result["grid_gross_ret"] = overlay["overlay_gross_ret"].to_numpy(dtype=float)
        result["grid_cost_rate"] = overlay["overlay_cost_rate"].to_numpy(dtype=float)
        result["cycle_id"] = overlay["cycle_id"].to_numpy(dtype=int)
    result["grid_net_increment"] = (
        (1.0 + result["grid_gross_ret"]) * (1.0 - result["grid_cost_rate"]) - 1.0
    )
    result["cash_weight"] = (
        result["bare_put_no_grid_cash_weight"] - MARGIN_RATE * result["grid_held_eod"]
    )
    if result["cash_weight"].lt(-1e-12).any():
        raise RuntimeError(f"Negative cash weight: {candidate}")
    result["ret"] = base_pre_cash + result["grid_net_increment"] + result["cash_weight"] * CASH_DAILY
    result["total_ic_units"] = result["roll50_momentum50_ic_units"] + result["grid_held_eod"]
    result["candidate"] = candidate
    if result["ret"].isna().any() or result["ret"].le(-1.0).any():
        raise RuntimeError(f"Invalid return path: {candidate}")
    result["nav"] = (1.0 + result["ret"]).cumprod()
    result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
    return result


def metric_row(candidate: str, definition: dict[str, Any], segment: str, sample: pd.DataFrame) -> dict[str, Any]:
    ret = sample["ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1)) if len(ret) > 1 else 0.0
    ann_return = float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0)
    return {
        "candidate": candidate, "segment": segment,
        "mode": definition["mode"], "entry": definition["entry"], "exit": definition["exit"],
        "start": sample["date"].min().date().isoformat(),
        "end": sample["date"].max().date().isoformat(), "rows": int(len(sample)),
        "ann_return": ann_return, "ann_vol": float(ret.std(ddof=0) * math.sqrt(252.0)),
        "sharpe_repo": float(ret.mean()) / std * math.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(dd.min()), "final_nav": float(nav.iloc[-1]),
        "holding_days": int(sample["grid_held_eod"].sum()),
        "holding_day_ratio": float(sample["grid_held_eod"].mean()),
        "cost_total": float(sample["grid_cost_rate"].sum()),
        "avg_total_ic_units": float(sample["total_ic_units"].mean()),
        "max_total_ic_units": float(sample["total_ic_units"].max()),
        "min_cash_weight": float(sample["cash_weight"].min()),
    }


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    definitions = {row["candidate"]: row for row in CANDIDATES}
    end = daily["date"].max()
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date")
        for segment, offset in SEGMENTS:
            if offset is None:
                sample = group
            elif offset == "real":
                sample = group[group["date"].ge(REAL_PUT_START)]
            else:
                sample = group[group["date"].ge(end - offset)]
            rows.append(metric_row(candidate, definitions[candidate], segment, sample))
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    required = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
    for candidate, group in summary.groupby("candidate", sort=False):
        block = group.set_index("segment")
        definition = definitions[candidate]
        row: dict[str, Any] = {
            "candidate": candidate, "mode": definition["mode"],
            "entry": definition["entry"], "exit": definition["exit"],
        }
        for segment in required:
            row[f"ann_return_{segment}"] = float(block.loc[segment, "ann_return"])
            row[f"max_dd_{segment}"] = float(block.loc[segment, "max_dd"])
        row["sharpe_repo_full"] = float(block.loc["full", "sharpe_repo"])
        row["holding_days_full"] = int(block.loc["full", "holding_days"])
        row["cost_total_full"] = float(block.loc["full", "cost_total"])
        wide_rows.append(row)
    return summary, pd.DataFrame(wide_rows)


def default_cycle_attribution(
    base_path: pd.DataFrame,
    paths: dict[str, pd.DataFrame],
    overlays: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, float]]:
    independent = overlays[DEFAULT_CANDIDATES["independent"]]
    cycles = sorted(int(value) for value in independent["cycle_id"].unique() if int(value) > 0)
    rows: list[dict[str, Any]] = []
    concentration: dict[str, float] = {}
    for mode in MODES:
        candidate = DEFAULT_CANDIDATES[mode]
        path = paths[candidate]
        overlay = overlays[candidate]
        mode_rows: list[dict[str, Any]] = []
        for cycle in cycles:
            cycle_mask = independent["cycle_id"].eq(cycle)
            sample_overlay = overlay.loc[cycle_mask]
            sample_path = path.loc[cycle_mask]
            sample_base = base_path.loc[cycle_mask]
            grid_net = (
                (1.0 + sample_overlay["overlay_gross_ret"])
                * (1.0 - sample_overlay["overlay_cost_rate"])
                - 1.0
            )
            grid_nav = (1.0 + grid_net).cumprod()
            contribution_log = float(
                np.log1p(sample_path["ret"]).sum() - np.log1p(sample_base["ret"]).sum()
            )
            mode_rows.append(
                {
                    "mode": mode, "candidate": candidate, "cycle_id": cycle,
                    "start": sample_path["date"].min().date().isoformat(),
                    "end": sample_path["date"].max().date().isoformat(),
                    "calendar_rows": int(len(sample_path)),
                    "holding_days": int(sample_overlay["overlay_held_eod"].sum()),
                    "entry_events": int(sample_overlay["overlay_buy"].sum()),
                    "exit_events": int(sample_overlay["overlay_sell"].sum()),
                    "grid_sleeve_return": float(grid_nav.iloc[-1] - 1.0),
                    "grid_sleeve_max_dd": float((grid_nav / grid_nav.cummax() - 1.0).min()),
                    "strategy_relative_log_contribution": contribution_log,
                    "momentum_allowed_ratio": float(
                        base_path.loc[cycle_mask, "momentum_weight"].gt(0).mean()
                    ),
                }
            )
        contributions = np.array(
            [abs(row["strategy_relative_log_contribution"]) for row in mode_rows], dtype=float
        )
        total = float(contributions.sum())
        largest = float(contributions.max() / total) if total > 0 else 1.0
        concentration[mode] = largest
        for row in mode_rows:
            row["absolute_contribution_share"] = (
                abs(float(row["strategy_relative_log_contribution"])) / total if total > 0 else np.nan
            )
        rows.extend(mode_rows)
    return pd.DataFrame(rows), concentration


def leave_one_cycle_out(
    base: pd.DataFrame,
    base_path: pd.DataFrame,
    paths: dict[str, pd.DataFrame],
    overlays: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    independent = overlays[DEFAULT_CANDIDATES["independent"]]
    cycles = sorted(int(value) for value in independent["cycle_id"].unique() if int(value) > 0)
    baseline_ann = metric_row(
        "bare_put_no_grid", CANDIDATES[0], "full", base_path
    )["ann_return"]
    rows: list[dict[str, Any]] = []
    for mode in MODES:
        candidate = DEFAULT_CANDIDATES[mode]
        default_ann = metric_row(candidate, next(x for x in CANDIDATES if x["candidate"] == candidate), "full", paths[candidate])["ann_return"]
        default_increment = default_ann - baseline_ann
        for cycle in cycles:
            overlay = overlays[candidate].copy()
            mask = independent["cycle_id"].eq(cycle)
            for column in (
                "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
                "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate",
                "overlay_cost_rate", "cycle_id",
            ):
                overlay.loc[mask, column] = 0
            loo_path = compose(base, overlay, f"{candidate}_drop_cycle{cycle}")
            values = metric_row(
                candidate, next(x for x in CANDIDATES if x["candidate"] == candidate),
                "full", loo_path,
            )
            increment = float(values["ann_return"] - baseline_ann)
            rows.append(
                {
                    "mode": mode, "candidate": candidate, "dropped_cycle": cycle,
                    "ann_return": values["ann_return"], "max_dd": values["max_dd"],
                    "ann_increment_vs_no_grid": increment,
                    "default_ann_increment_vs_no_grid": default_increment,
                    "increment_retention_ratio": increment / default_increment if default_increment > 0 else np.nan,
                    "pass_retain_half": bool(increment >= 0.5 * default_increment - 1e-12),
                }
            )
    return pd.DataFrame(rows)


def surface_decision(
    summary: pd.DataFrame,
    cycle_concentration: dict[str, float],
    loo: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    full = summary[summary["segment"].eq("full")].set_index("candidate")
    baseline = full.loc["bare_put_no_grid"]
    rows: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    for mode in MODES:
        candidates = [row["candidate"] for row in CANDIDATES if row["mode"] == mode]
        block = full.loc[candidates]
        passed = (
            block["ann_return"].gt(float(baseline["ann_return"]) + 1e-12)
            & block["max_dd"].ge(float(baseline["max_dd"]) - 0.03 - 1e-12)
        )
        breadth = int(passed.sum())
        breadth_pass = breadth >= 6
        concentration = float(cycle_concentration[mode])
        concentration_pass = concentration <= 0.50 + 1e-12
        mode_loo = loo[loo["mode"].eq(mode)]
        loo_pass = bool(mode_loo["pass_retain_half"].all())
        checks[mode] = {
            "nearby_pass_count": breadth,
            "nearby_total": len(candidates),
            "nearby_breadth_pass": breadth_pass,
            "largest_cycle_absolute_contribution_share": concentration,
            "cycle_concentration_pass": concentration_pass,
            "leave_one_cycle_out_pass": loo_pass,
            "minimum_loo_retention_ratio": float(mode_loo["increment_retention_ratio"].min()),
        }
        for candidate in candidates:
            definition = next(row for row in CANDIDATES if row["candidate"] == candidate)
            rows.append(
                {
                    "candidate": candidate, "mode": mode,
                    "entry": definition["entry"], "exit": definition["exit"],
                    "ann_return_full": float(full.loc[candidate, "ann_return"]),
                    "max_dd_full": float(full.loc[candidate, "max_dd"]),
                    "ann_return_delta_vs_no_grid_pp": 100.0 * float(
                        full.loc[candidate, "ann_return"] - baseline["ann_return"]
                    ),
                    "max_dd_delta_vs_no_grid_pp": 100.0 * float(
                        full.loc[candidate, "max_dd"] - baseline["max_dd"]
                    ),
                    "nearby_point_pass": bool(passed.loc[candidate]),
                    "is_default": candidate == DEFAULT_CANDIDATES[mode],
                }
            )
    all_pass = all(
        item["nearby_breadth_pass"]
        and item["cycle_concentration_pass"]
        and item["leave_one_cycle_out_pass"]
        for item in checks.values()
    )
    decision = "watchlist" if all_pass else "keep_default"
    stability = "wide_stable" if all_pass else "data_sensitive"
    return pd.DataFrame(rows), {
        "mode_checks": checks,
        "all_preregistered_robustness_conditions_passed": all_pass,
        "decision": decision,
        "stability_label": stability,
    }


def update_scan_meta(
    source_hashes: dict[str, str], decision: dict[str, Any], elapsed: float
) -> None:
    path = RUN / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "two_parameter_grid",
            "baseline": {
                "candidate": "bare_put_no_grid",
                "description": "50pct bare IC plus 50pct momentum IC, Put protects bare sleeve only, no grid",
            },
            "candidate_grid": [row["candidate"] for row in CANDIDATES],
            "data_snapshot": {
                "start": "2015-04-16", "end": "2026-08-14", "rows": 2756,
                "ic": "CFFEX official active IC open/settlement series",
                "valuation": "frozen CSI500 unbounded two-of-three fixed-economic-unit score",
                "put_model_end": "2022-09-16", "real_put_start": "2022-09-19",
                "timezone": "Asia/Shanghai", "cache_writes": "none",
            },
            "cost_model": {
                "grid_futures_one_way": ONE_WAY_COST,
                "grid_roll_round_trip": 2.0 * ONE_WAY_COST,
                "margin_buffer_per_1x_ic": MARGIN_RATE,
                "cash_net_annual": 0.03,
                "slippage_or_open_impact": "not modeled",
            },
            "parity_check": {
                "no_grid": "against stage4 formal daily path",
                "default_independent": "against stage4 formal daily path",
                "default_guided": "against stage4 formal daily path",
            },
            "source_hashes": source_hashes,
            "decision": decision["decision"],
            "stability_label": decision["stability_label"],
            "elapsed_sec": elapsed,
            "warnings": [
                "Only three independent valuation cycles exist in the full IC sample",
                "Frozen combined artifact does not retain volume; capacity is not tested",
                "Put is theoretical before 2022-09-19",
                "Working tree was already dirty before the scan",
            ],
        }
    )
    meta["outputs"].update(
        {
            "daily_outputs": str((DAILY_DIR / "daily_candidates.csv.gz").relative_to(ROOT)),
            "cycle_attribution": str((RUN / "cycle_attribution.csv").relative_to(ROOT)),
            "leave_one_cycle_out": str((RUN / "leave_one_cycle_out.csv").relative_to(ROOT)),
            "threshold_surface": str((RUN / "threshold_surface.csv").relative_to(ROOT)),
            "parity_checks": str((RUN / "parity_checks.csv").relative_to(ROOT)),
        }
    )
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def write_record(
    summary: pd.DataFrame,
    wide: pd.DataFrame,
    cycles: pd.DataFrame,
    loo: pd.DataFrame,
    decision: dict[str, Any],
    parity: dict[str, float],
) -> None:
    full = summary[summary["segment"].eq("full")].set_index("candidate")
    defaults = ["bare_put_no_grid", DEFAULT_CANDIDATES["independent"], DEFAULT_CANDIDATES["guided"]]
    table = ["|candidate|ann_return|max_dd|holding_days|cost_total|", "|---|---:|---:|---:|---:|"]
    for candidate in defaults:
        row = full.loc[candidate]
        table.append(
            f"|{candidate}|{row['ann_return']:.4%}|{row['max_dd']:.4%}|{int(row['holding_days'])}|{row['cost_total']:.4%}|"
        )
    mode_lines: list[str] = []
    for mode, checks in decision["mode_checks"].items():
        mode_lines.append(
            f"- {mode}: 邻域 {checks['nearby_pass_count']}/{checks['nearby_total']}；"
            f"最大周期贡献 {checks['largest_cycle_absolute_contribution_share']:.2%}；"
            f"最低留一保留比例 {checks['minimum_loo_retention_ratio']:.2%}。"
        )
    text = f"""# IC 网格坚韧性扫描 v1

## Run Metadata

- Run id: `{RUN.name}`
- Run date: {datetime.now().astimezone().isoformat()}
- Timezone: Asia/Shanghai
- Project: IC roll momentum 50:50
- Repo: `{ROOT}`
- Strategy: `{VERSION}`
- Scan type: two_parameter_grid plus cycle leave-one-out
- Source-change rule: `research_only_no_source_change`

## Research Question

- Baseline: `bare_put_no_grid`。
- Grid: entry {ENTRIES}, exit {EXITS}, modes {MODES}。
- Required windows: full, 10Y, 5Y, 3Y, 1Y；另报真实Put期。
- Promotion threshold: 邻域至少6/9通过、最大单周期贡献不超过50%、所有留一周期保留至少50%原增量。

## Implementation Anchor

- Entry point: `{ROOT / 'ic_roll_momentum_stage5_grid_robustness_v1.py'}`。
- Baseline artifact: `{STAGE4_DAILY}`。
- Market/grid artifact: `{GRID_FROZEN}`。
- 默认无网格/独立/指导逐日复现误差：{parity['no_grid']:.3e} / {parity['independent']:.3e} / {parity['guided']:.3e}。

## Data Snapshot

- 2015-04-16—2026-08-14，共2756个IC交易日。
- IC为中金所官方活跃合约开盘/结算价；估值为冻结无界二取三分数。
- Put在2022-09-19前为理论层，之后为真实510500ETF Put。
- 本轮只读既有数据，无缓存写入；运行前工作树已存在其他未跟踪研究文件。

## Cost and Execution Assumptions

- 网格每边1bp、持仓换月双边2bp；每1倍IC占30%保证金/缓冲；现金净年化3%。
- T收盘信号、T+1官方开盘执行；未计盘口冲击、开盘不可成交偏差、动态保证金或容量。
- 冻结组合产物不含成交量字段，未做容量验证。

## Runtime Override Plan

- 运行时循环候选阈值，不修改冻结源码或主线。
- 默认点与无网格控制同批运行，并对第四层正式路径做逐日校验。

## Commands

```powershell
python ic_roll_momentum_stage5_grid_robustness_v1.py
python -m pytest -q test_ic_roll_momentum_stage5_grid_robustness_v1.py
```

## Output Files

- `scan_summary.csv`：长表窗口指标。
- `window_metrics.csv`：宽表窗口指标。
- `daily_outputs/daily_candidates.csv.gz`：逐日候选。
- `cycle_attribution.csv`：默认周期归因。
- `leave_one_cycle_out.csv`：留一周期。
- `threshold_surface.csv`：邻域通过表。
- `parity_checks.csv`：基线复现。

## Full-Sample Results

{chr(10).join(table)}

## Window Results

完整窗口见`window_metrics.csv`；全部数值来自本次实际运行。

## Stability Classification

- Label: `{decision['stability_label']}`。
{chr(10).join(mode_lines)}
- 三个独立估值周期不能提供真正独立的长期验证；动量指导只是切分这些周期。

## Decision

- Decision: `{decision['decision']}`。
- 若任一集中度或留一条件失败，核心维持无网格；网格只保留研究卫星身份。
- 不修改V2主线、Poe或实盘配置。

## User-Facing Summary

结论以邻域、逐周期和留一结果共同决定，不依据单一最高年化点。
"""
    (RUN / "record.md").write_text(text, encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    source_hashes = verify_inputs()
    base, market = load_inputs()
    overlays: dict[str, pd.DataFrame] = {}
    paths: dict[str, pd.DataFrame] = {}
    event_parts: list[pd.DataFrame] = []

    base_path = compose(base, None, "bare_put_no_grid")
    paths["bare_put_no_grid"] = base_path
    daily_parts = [base_path]
    for definition in CANDIDATES[1:]:
        overlay, events = simulate_grid(
            market, float(definition["entry"]), float(definition["exit"]), definition["mode"]
        )
        candidate = definition["candidate"]
        overlays[candidate] = overlay
        path = compose(base, overlay, candidate)
        paths[candidate] = path
        daily_parts.append(path)
        if len(events):
            event_parts.append(events)
    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    events = pd.concat(event_parts, ignore_index=True, sort=False)

    no_grid_error = float(
        (base_path["ret"] - base["bare_put_no_grid_ret"]).abs().max()
    )
    independent_error = float(
        (
            paths[DEFAULT_CANDIDATES["independent"]]["ret"]
            - base["bare_put_independent_ret"]
        ).abs().max()
    )
    guided_error = float(
        (
            paths[DEFAULT_CANDIDATES["guided"]]["ret"]
            - base["bare_put_guided_ret"]
        ).abs().max()
    )
    parity = {"no_grid": no_grid_error, "independent": independent_error, "guided": guided_error}
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"Stage-4 parity failed: {parity}")

    summary, wide = build_metrics(daily)
    cycles, concentration = default_cycle_attribution(base_path, paths, overlays)
    loo = leave_one_cycle_out(base, base_path, paths, overlays)
    surface, decision = surface_decision(summary, concentration, loo)

    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    events.to_csv(RUN / "grid_trade_events.csv.gz", index=False, compression="gzip")
    summary.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    cycles.to_csv(RUN / "cycle_attribution.csv", index=False)
    loo.to_csv(RUN / "leave_one_cycle_out.csv", index=False)
    surface.to_csv(RUN / "threshold_surface.csv", index=False)
    pd.DataFrame(
        [{"path": name, "cash_ret_max_abs": value, "pass": value <= 1e-12} for name, value in parity.items()]
    ).to_csv(RUN / "parity_checks.csv", index=False)
    (RUN / "integrity_checks.json").write_text(
        json.dumps(
            {
                "candidate_count": len(CANDIDATES),
                "daily_rows": int(len(daily)),
                "date_start": str(base["date"].min().date()),
                "date_end": str(base["date"].max().date()),
                "default_cycle_count": int(cycles["cycle_id"].nunique()),
                "parity": parity,
                "decision": decision,
                "all_daily_finite": bool(np.isfinite(daily["ret"]).all()),
                "min_cash_weight": float(daily["cash_weight"].min()),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    elapsed = time.perf_counter() - started
    update_scan_meta(source_hashes, decision, elapsed)
    write_record(summary, wide, cycles, loo, decision, parity)
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n[{datetime.now().astimezone().isoformat()}] cwd={ROOT}\n"
            f"python ic_roll_momentum_stage5_grid_robustness_v1.py\n"
            f"elapsed_sec={elapsed:.3f}\n"
        )
    print(summary[summary["segment"].eq("full")].to_string(index=False))
    print("\nCycle attribution:\n", cycles.to_string(index=False))
    print("\nLeave one cycle out:\n", loo.to_string(index=False))
    print("\nDecision:\n", json.dumps(decision, ensure_ascii=False, indent=2))
    print(f"Run folder: {RUN}")


if __name__ == "__main__":
    main()
