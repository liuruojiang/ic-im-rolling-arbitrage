from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import freeze_ic_im_system_mainlines_v2 as mainline
import ic_im_put_max_protection_scan_v1 as metric_base
import im_mo_csi1000_put_protection_battery_v6 as market_v6
import im_monthly_discount_roll_v1 as im_roll


ROOT = Path(__file__).resolve().parent
VERSION = "im_put_iv_derisk_overlay_scan_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "05a7b70e5429613f7e8f0e2eb777f2276805090c1604c457e70aab72f7182006"
RUN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260825_ic_im_rolling_arbitrage_im_mainline_v2_im_core_put_replacement_put_iv_threshold_core_derisk"
)
V2_DAILY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
V2_MANIFEST = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "data_manifest.json"

BASELINE = "v2_frozen_put"
THRESHOLDS = (0.25, 0.30, 0.35, 0.40)
MIN_CORE_SCALES = (0.25, 0.50, 0.75)
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
FUTURES_RESIZE_ONE_WAY_COST = 0.0001
CALL_RESIZE_ONE_WAY_COST = 0.0001


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def candidate_name(threshold: float, min_core_scale: float) -> str:
    return f"iv{int(round(threshold * 100)):02d}_floor{int(round(min_core_scale * 100)):02d}"


def verify_preregistration() -> dict[str, Any]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Preregistered v1 specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Preregistered v1 specification sidecar mismatch")
    if not RUN.exists():
        raise FileNotFoundError("Initialized quant scan run folder is missing")
    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init":
        raise RuntimeError(f"Scan run is not in init phase: {meta.get('phase')}")
    return meta


def implied_put_volatility(
    price: float,
    spot: float,
    strike: float,
    rate: float,
    dividend: float,
    years: float,
) -> float | None:
    if min(price, spot, strike, years) <= 0:
        return None
    low, high = 0.01, 5.0
    low_price = market_v6.proxy.bs_put(spot, strike, rate, dividend, low, years)
    high_price = market_v6.proxy.bs_put(spot, strike, rate, dividend, high, years)
    if price < low_price - 1e-8 or price > high_price + 1e-8:
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        value = market_v6.proxy.bs_put(spot, strike, rate, dividend, mid, years)
        if value < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def derisk_scale(iv: float, threshold: float, min_core_scale: float) -> float:
    if not math.isfinite(iv) or iv <= threshold:
        return 1.0
    return max(min_core_scale, threshold / iv)


def build_iv_signal(
    schedule: pd.DataFrame,
    options: pd.DataFrame,
    active_im: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    im_close = active_im.set_index("date")["close"]
    market_lookup = market.set_index("date")
    option_start = pd.Timestamp(options["date"].min())
    rows: list[dict[str, Any]] = []
    for event in schedule.sort_values("eval_date").itertuples(index=False):
        eval_date = pd.Timestamp(event.eval_date)
        execution_date = pd.Timestamp(event.execution_date)
        target_qty = int(event.binary_target_qty)
        row: dict[str, Any] = {
            "eval_date": eval_date,
            "execution_date": execution_date,
            "baseline_put_target_qty": target_qty,
            "iv_contract": "",
            "iv_contract_month": pd.NaT,
            "iv_actual_expiry": pd.NaT,
            "iv_strike": np.nan,
            "iv_option_close": np.nan,
            "iv_spot_close": np.nan,
            "put_implied_vol": np.nan,
            "initial_listing_exception": eval_date < option_start,
        }
        if eval_date >= option_start:
            if eval_date not in im_close.index or eval_date not in market_lookup.index:
                raise RuntimeError(f"Missing causal IM or market row on {eval_date.date()}")
            target_date = mainline.im_v12.v4.tenor_target_date(
                "3m", eval_date, execution_date, False
            )
            desired_month = mainline.im_v12.v4.selected_month(options, eval_date, target_date)
            quote = mainline.im_v12.v8.select_close_contract(
                options, im_close, eval_date, desired_month, 0.95
            )
            if quote is None:
                raise RuntimeError(f"No liquid causal 95% MO Put quote on {eval_date.date()}")
            market_row = market_lookup.loc[eval_date]
            years = (pd.Timestamp(quote["actual_expiry"]) - eval_date).days / 365.0
            iv = implied_put_volatility(
                float(quote["close"]),
                float(market_row["spot_close"]),
                float(quote["strike"]),
                float(market_row["rate_close"]),
                float(market_row["dividend_close"]),
                years,
            )
            if iv is None or not math.isfinite(iv):
                raise RuntimeError(f"No valid implied Put volatility on {eval_date.date()}")
            row.update(
                {
                    "iv_contract": str(quote["contract"]),
                    "iv_contract_month": pd.Timestamp(quote["contract_month"]),
                    "iv_actual_expiry": pd.Timestamp(quote["actual_expiry"]),
                    "iv_strike": float(quote["strike"]),
                    "iv_option_close": float(quote["close"]),
                    "iv_spot_close": float(market_row["spot_close"]),
                    "put_implied_vol": float(iv),
                }
            )
        rows.append(row)
    signal = pd.DataFrame(rows)
    missing = signal[
        signal["put_implied_vol"].isna() & ~signal["initial_listing_exception"]
    ]
    if len(missing):
        raise RuntimeError(f"Unexpected IV gaps: {len(missing)}")
    if not (signal["execution_date"] > signal["eval_date"]).all():
        raise RuntimeError("Non-causal T/T+1 signal row")
    return signal


def baseline_put_parity(
    official: pd.DataFrame, overlay: pd.DataFrame
) -> tuple[float, pd.DataFrame]:
    columns = [
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_fraction",
    ]
    joined = official[["date", *columns]].merge(
        overlay[["date", *columns]],
        on="date",
        suffixes=("_official", "_rerun"),
        validate="one_to_one",
    )
    rows: list[dict[str, Any]] = []
    for column in columns:
        error = float(
            (
                joined[f"{column}_official"].fillna(0.0)
                - joined[f"{column}_rerun"].fillna(0.0)
            )
            .abs()
            .max()
        )
        rows.append({"check": column, "max_abs_error": error, "pass": error <= 1e-12})
    contract = official[["date", "put_contract"]].merge(
        overlay[["date", "put_contract"]],
        on="date",
        suffixes=("_official", "_rerun"),
        validate="one_to_one",
    )
    mismatches = int(
        (
            contract["put_contract_official"].fillna("").astype(str)
            != contract["put_contract_rerun"].fillna("").astype(str)
        ).sum()
    )
    rows.append(
        {
            "check": "put_contract_mismatch_rows",
            "max_abs_error": float(mismatches),
            "pass": mismatches == 0,
        }
    )
    checks = pd.DataFrame(rows)
    maximum = float(checks.loc[checks["check"] != "put_contract_mismatch_rows", "max_abs_error"].max())
    if not checks["pass"].all():
        raise RuntimeError(f"Frozen v2 Put parity failed: {checks.to_dict('records')}")
    return maximum, checks


def build_candidate_schedule(
    schedule: pd.DataFrame,
    iv_signal: pd.DataFrame,
    threshold: float,
    min_core_scale: float,
    candidate: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    policy = schedule.merge(
        iv_signal,
        on=["eval_date", "execution_date"],
        how="left",
        validate="one_to_one",
    )
    policy["high_iv_gate"] = (
        policy["put_implied_vol"].gt(threshold)
        & policy["baseline_put_target_qty"].gt(0)
    )
    policy["core_scale_target"] = 1.0
    gated = policy["high_iv_gate"]
    policy.loc[gated, "core_scale_target"] = policy.loc[gated, "put_implied_vol"].map(
        lambda value: derisk_scale(float(value), threshold, min_core_scale)
    )
    policy["candidate_put_target_qty"] = policy["baseline_put_target_qty"]
    policy.loc[gated, "candidate_put_target_qty"] = 0
    # Preserve the official schedule schema directly.  The audit merge has an
    # overlapping initial_listing_exception field and therefore suffixes it.
    result = schedule.copy()
    result["binary_target_qty"] = policy["candidate_put_target_qty"].astype(int)
    result["three_tier_target_qty"] = result["binary_target_qty"]
    result["candidate"] = candidate
    result["schedule_candidate"] = candidate
    audit = policy[
        [
            "eval_date",
            "execution_date",
            "baseline_put_target_qty",
            "put_implied_vol",
            "iv_contract",
            "high_iv_gate",
            "core_scale_target",
            "candidate_put_target_qty",
        ]
    ].copy()
    audit["candidate"] = candidate
    audit["iv_threshold"] = threshold
    audit["min_core_scale"] = min_core_scale
    return result, audit


def recompose_candidate(
    official: pd.DataFrame,
    upstream: pd.DataFrame,
    put_overlay: pd.DataFrame,
    policy: pd.DataFrame,
    candidate: str,
    threshold: float,
    min_core_scale: float,
) -> pd.DataFrame:
    frame = official.drop(
        columns=[
            "put_pnl_ret",
            "put_cost_rate",
            "put_mark_fraction",
            "put_fraction",
            "put_contract",
        ]
    ).merge(
        put_overlay[
            [
                "date",
                "put_pnl_ret",
                "put_cost_rate",
                "put_mark_fraction",
                "put_fraction",
                "put_contract",
            ]
        ],
        on="date",
        how="inner",
        validate="one_to_one",
    )
    frame = frame.merge(
        upstream[["date", "cost_rate"]].rename(columns={"cost_rate": "base_roll_cost_rate"}),
        on="date",
        how="left",
        validate="one_to_one",
    )
    eod = policy.set_index("execution_date")["core_scale_target"].reindex(frame["date"])
    if eod.isna().any():
        raise RuntimeError(f"Missing EOD core scale for {candidate}")
    frame["core_scale_eod"] = eod.to_numpy(dtype=float)
    frame["core_scale_held"] = frame["core_scale_eod"].shift(1).fillna(1.0)
    frame["core_scale_change"] = frame["core_scale_eod"].diff().fillna(0.0).abs()
    frame["grid_im_units"] = frame["total_im_units"] - 1.0
    grid_cost = frame["futures_cost_rate"] - frame["base_roll_cost_rate"]
    if float(grid_cost.min()) < -1e-12:
        raise RuntimeError(f"Negative derived grid cost for {candidate}")
    frame["grid_futures_cost_rate"] = grid_cost.clip(lower=0.0)
    frame["futures_resize_cost_rate"] = (
        frame["core_scale_change"] * FUTURES_RESIZE_ONE_WAY_COST
    )
    call_active = (
        frame["call_contract"].fillna("").astype(str).ne("")
        | frame["call_contract"].fillna("").astype(str).shift(1).fillna("").ne("")
    )
    frame["call_resize_cost_rate"] = (
        frame["core_scale_change"] * CALL_RESIZE_ONE_WAY_COST * call_active.astype(float)
    )
    frame["base_gross_ret"] = frame["base_gross_ret"] * frame["core_scale_held"]
    frame["call_pnl_ret"] = frame["call_pnl_ret"] * frame["core_scale_held"]
    frame["call_cost_rate"] = (
        frame["call_cost_rate"] * frame["core_scale_eod"]
        + frame["call_resize_cost_rate"]
    )
    frame["call_mark_fraction"] = frame["call_mark_fraction"] * frame["core_scale_eod"]
    frame["call_margin_fraction"] = frame["call_margin_fraction"] * frame["core_scale_eod"]
    frame["call_coverage"] = frame["call_coverage"] * frame["core_scale_eod"]
    frame["futures_cost_rate"] = (
        frame["grid_futures_cost_rate"]
        + frame["base_roll_cost_rate"] * frame["core_scale_held"]
        + frame["futures_resize_cost_rate"]
    )
    frame["total_im_units"] = frame["grid_im_units"] + frame["core_scale_eod"]
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
    frame["nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["drawdown"] = frame["nav"] / frame["nav"].cummax() - 1.0
    frame["candidate"] = candidate
    frame["iv_threshold"] = threshold
    frame["min_core_scale"] = min_core_scale
    if frame[["cash_ret", "nav", "drawdown"]].isna().any().any():
        raise RuntimeError(f"Invalid daily result for {candidate}")
    if frame["cash_ret"].le(-1.0).any():
        raise RuntimeError(f"Daily loss <= -100% for {candidate}")
    return frame


def segment_sample(group: pd.DataFrame, segment: str) -> tuple[pd.DataFrame, bool]:
    end = pd.Timestamp(group["date"].max())
    if segment == "full":
        return group.copy(), True
    years = int(segment.removeprefix("last_").removesuffix("y"))
    requested = end - pd.DateOffset(years=years)
    complete = pd.Timestamp(group["date"].min()) <= requested
    return group[group["date"].ge(max(pd.Timestamp(group["date"].min()), requested))].copy(), complete


def metric_row(
    group: pd.DataFrame,
    segment: str,
    threshold: float | None,
    min_core_scale: float | None,
) -> dict[str, Any]:
    sample, window_complete = segment_sample(group, segment)
    returns = sample["cash_ret"].astype(float)
    nav = (1.0 + returns).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    rows = len(sample)
    std = float(returns.std(ddof=1)) if rows > 1 else 0.0
    high_iv = sample.get("high_iv_gate", pd.Series(False, index=sample.index)).fillna(False)
    return {
        "candidate": str(group["candidate"].iloc[0]),
        "segment": segment,
        "start": sample["date"].min().date().isoformat(),
        "end": sample["date"].max().date().isoformat(),
        "rows": rows,
        "window_complete": window_complete,
        "ann_return": float(nav.iloc[-1] ** (252.0 / rows) - 1.0),
        "ann_vol": std * math.sqrt(252.0),
        "sharpe_repo": float(returns.mean()) / std * math.sqrt(252.0) if std > 0 else 0.0,
        "max_dd": float(drawdown.min()),
        "iv_threshold": threshold,
        "min_core_scale": min_core_scale,
        "avg_weight": float(sample.get("core_scale_eod", pd.Series(1.0, index=sample.index)).mean()),
        "held_day_avg_weight": float(sample.get("core_scale_held", pd.Series(1.0, index=sample.index)).mean()),
        "holding_days": int(high_iv.sum()),
        "holding_day_ratio": float(high_iv.mean()),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "futures_resize_cost_total": float(sample.get("futures_resize_cost_rate", pd.Series(0.0, index=sample.index)).sum()),
        "call_resize_cost_total": float(sample.get("call_resize_cost_rate", pd.Series(0.0, index=sample.index)).sum()),
        "min_cash_weight_raw": float(sample["cash_weight_raw"].min()),
        "max_total_im_units": float(sample["total_im_units"].max()),
    }


def build_metrics(
    daily: pd.DataFrame, definitions: dict[str, tuple[float | None, float | None]]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for candidate, group in daily.groupby("candidate", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        threshold, floor = definitions[candidate]
        for segment in WINDOWS:
            rows.append(metric_row(group, segment, threshold, floor))
    summary = pd.DataFrame(rows)
    wide_rows: list[dict[str, Any]] = []
    for candidate, group in summary.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": candidate,
            "iv_threshold": first["iv_threshold"],
            "min_core_scale": first["min_core_scale"],
        }
        for item in group.itertuples(index=False):
            row[f"ann_return_{item.segment}"] = item.ann_return
            row[f"ann_vol_{item.segment}"] = item.ann_vol
            row[f"sharpe_repo_{item.segment}"] = item.sharpe_repo
            row[f"max_dd_{item.segment}"] = item.max_dd
            row[f"avg_weight_{item.segment}"] = item.avg_weight
            row[f"holding_day_ratio_{item.segment}"] = item.holding_day_ratio
            row[f"window_complete_{item.segment}"] = item.window_complete
        wide_rows.append(row)
    return summary, pd.DataFrame(wide_rows)


def choose_decision(
    summary: pd.DataFrame,
) -> tuple[str, str, str | None, pd.DataFrame]:
    full = summary[summary["segment"].eq("full")].set_index("candidate")
    three = summary[summary["segment"].eq("last_3y")].set_index("candidate")
    one = summary[summary["segment"].eq("last_1y")].set_index("candidate")
    base = full.loc[BASELINE]
    rows: list[dict[str, Any]] = []
    for candidate, row in full.drop(index=BASELINE).iterrows():
        cost_reduction = 1.0 - float(row["put_cost_total"]) / float(base["put_cost_total"])
        passed = bool(
            float(row["ann_return"]) >= float(base["ann_return"]) - 0.01
            and float(row["sharpe_repo"]) >= float(base["sharpe_repo"])
            and float(row["max_dd"]) >= float(base["max_dd"]) + 0.01
            and float(three.loc[candidate, "max_dd"]) >= float(three.loc[BASELINE, "max_dd"]) - 0.01
            and float(one.loc[candidate, "max_dd"]) >= float(one.loc[BASELINE, "max_dd"]) - 0.01
            and cost_reduction >= 0.25
            and float(row["min_cash_weight_raw"]) >= -1e-12
        )
        rows.append(
            {
                "candidate": candidate,
                "individual_gate_pass": passed,
                "full_ann_delta_pp": 100.0 * (float(row["ann_return"]) - float(base["ann_return"])),
                "full_max_dd_improvement_pp": 100.0 * (float(row["max_dd"]) - float(base["max_dd"])),
                "full_sharpe_delta": float(row["sharpe_repo"]) - float(base["sharpe_repo"]),
                "put_cost_reduction": cost_reduction,
            }
        )
    gates = pd.DataFrame(rows)
    passed = gates[gates["individual_gate_pass"]].copy()
    connected: set[str] = set()
    params = {
        candidate_name(threshold, floor): (threshold, floor)
        for threshold in THRESHOLDS
        for floor in MIN_CORE_SCALES
    }
    passed_names = set(passed["candidate"])
    for name in passed_names:
        threshold, floor = params[name]
        neighbors = {
            candidate_name(other, floor)
            for other in THRESHOLDS
            if abs(THRESHOLDS.index(other) - THRESHOLDS.index(threshold)) == 1
        } | {
            candidate_name(threshold, other)
            for other in MIN_CORE_SCALES
            if abs(MIN_CORE_SCALES.index(other) - MIN_CORE_SCALES.index(floor)) == 1
        }
        if neighbors & passed_names:
            connected.add(name)
    gates["platform_pass"] = gates["candidate"].isin(connected)
    if connected:
        eligible = gates[gates["platform_pass"]].sort_values(
            ["full_max_dd_improvement_pp", "full_ann_delta_pp"], ascending=False
        )
        selected = str(eligible.iloc[0]["candidate"])
        stability = "wide_stable" if len(connected) >= 3 else "narrow_stable"
        return "watchlist", stability, selected, gates
    return "keep_default", "reject", None, gates


def make_record(
    meta: dict[str, Any],
    summary: pd.DataFrame,
    decision: str,
    stability: str,
    selected: str | None,
    parity_max: float,
    iv_signal: pd.DataFrame,
    market_checks: dict[str, Any],
) -> str:
    full = summary[summary["segment"].eq("full")].sort_values("candidate")
    lines = [
        "# IM Put 高隐波停买 / 核心降仓参数扫描 v1",
        "",
        "## Run Metadata",
        "",
        f"- Run id: `{meta['run_id']}`",
        "- Date/timezone: 2026-08-25 / Asia/Shanghai",
        "- Project: IC/IM rolling arbitrage; IM frozen v2 research baseline",
        f"- Git commit before: `{meta['git_commit']}`",
        "- Source-change rule: `research_only_no_source_change`; v2 frozen files unchanged.",
        "",
        "## Research Question",
        "",
        "When the causal close IV of the would-be 3m/95% MO Put is above a threshold, set Put target to zero and scale the fixed core IM as max(floor, threshold/IV).",
        "Candidate grid: IV 25/30/35/40% x core floor 25/50/75%, plus the v2 baseline.",
        "Decision target: watchlist only; the real MO history is shorter than five years.",
        "Promotion and rerun gates are frozen in the hashed specification.",
        "",
        "## Implementation Anchor",
        "",
        "- Official baseline: `outputs/ic_im_system_mainlines_v2/daily_candidates.csv.gz`.",
        "- Loader/schedule: `freeze_ic_im_system_mainlines_v2._im_source_data` and `build_im_selected_schedule`.",
        "- Put execution: `im_mo_close_execution_v8.run_real_normal_close`.",
        f"- Frozen baseline Put parity max absolute error: {parity_max:.3e}.",
        "",
        "## Data Snapshot",
        "",
        f"- Real CFFEX IM/MO sample: {full.iloc[0]['start']} to {full.iloc[0]['end']}, {int(full.iloc[0]['rows'])} trading rows.",
        f"- Causal IV rows: {int(iv_signal['put_implied_vol'].notna().sum())}; one initial-listing exception: {int(iv_signal['initial_listing_exception'].sum())}.",
        f"- Model-market construction checks: `{json.dumps(market_checks, ensure_ascii=False, default=str)}`.",
        "- Sources: CFFEX official IM/MO close/settlement/volume/open-interest; CSI1000 price and total-return index; local 10Y government-yield series.",
        "- 10Y and 5Y requested windows are incomplete and must be shown as N/A in user-facing reporting; artifact rows retain available-sample metrics for machine checks.",
        "",
        "## Cost and Execution Assumptions",
        "",
        "- Signal T close; Put/core change T+1 official close; new core weight earns returns from the following session.",
        "- Futures resize 1bp x absolute weight change; active Call basket resize 1bp x absolute weight change.",
        "- Original future/Put/Call costs and net 3% cash return are inherited; performance margin is 30% per IM unit.",
        "- Excluded: bid-ask spread, close impact, price-limit non-fill, order-book capacity, dynamic margin hikes, tax, and integer contract rounding.",
        "",
        "## Runtime Override Plan",
        "",
        "- Candidate schedules are in-memory copies; frozen source/spec/output files are not changed.",
        "- The default is included in the same run and reconciled to frozen v2 at <=1e-12.",
        "",
        "## Commands",
        "",
        "```powershell",
        "python im_put_iv_derisk_overlay_scan_v1.py",
        "```",
        "",
        "## Output Files",
        "",
        "- `scan_summary.csv`, `window_metrics.csv`, `scan_meta.json`, `command_log.txt`",
        "- `daily_outputs/daily_candidates.csv.gz`, `iv_signal.csv.gz`, `policy_audit.csv.gz`, `put_trades.csv.gz`",
        "- `parity_checks.csv`, `decision_gates.csv`",
        "",
        "## Full-Sample Results",
        "",
        "| candidate | IV | floor | CAGR | vol | Sharpe | MaxDD | avg core | gate days | Put cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in full.itertuples(index=False):
        iv = "baseline" if pd.isna(row.iv_threshold) else f"{row.iv_threshold:.0%}"
        floor = "baseline" if pd.isna(row.min_core_scale) else f"{row.min_core_scale:.0%}"
        lines.append(
            f"| {row.candidate} | {iv} | {floor} | {row.ann_return:.2%} | {row.ann_vol:.2%} | {row.sharpe_repo:.3f} | {row.max_dd:.2%} | {row.avg_weight:.2%} | {row.holding_days} | {row.put_cost_total:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Window Results",
            "",
            "Full/3Y/1Y are reported in CSV. 10Y/5Y are incomplete because the real MO sample starts in 2022; their machine rows use the available sample only.",
            "",
            "## Stability Classification",
            "",
            f"- Label: `{stability}`",
            f"- Selected watchlist candidate: `{selected or 'none'}`",
            "- Stability is determined only by the preregistered adjacent-candidate platform rule.",
            "- Data sensitivity: only one real MO history of about four years; no fresh holdout.",
            "- Leverage caveat: normalized fractional core/Call sizing is not an integer-contract feasibility test.",
            "",
            "## Decision",
            "",
            f"- Decision: `{decision}`",
            "- Recommended next action: retain frozen v2 unless a watchlist platform is found; any follow-up needs a new preregistration and cannot alter live/production state without explicit approval.",
            "",
            "## User-Facing Summary",
            "",
            f"Decision `{decision}`; stability `{stability}`; selected `{selected or 'none'}`. This is research evidence only and not an order recommendation.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    meta = verify_preregistration()
    upstream, active_im, options, source, valuation_state, thresholds, _frozen = (
        mainline._im_source_data()
    )
    schedule = mainline.build_im_selected_schedule(source, valuation_state, thresholds)
    official = pd.read_csv(V2_DAILY, parse_dates=["date"], low_memory=False)
    official = official[official["product"].eq("IM")].sort_values("date").reset_index(drop=True)
    baseline_overlay, baseline_trades, _ = mainline.im_v12.v8.run_real_normal_close(
        upstream, options, active_im, schedule, "3m", 0.95, "baseline_parity"
    )
    parity_max, parity_checks = baseline_put_parity(official, baseline_overlay)
    market, market_checks = market_v6.model_market()
    iv_signal = build_iv_signal(schedule, options, active_im, market)

    baseline = official.copy()
    baseline["candidate"] = BASELINE
    baseline["core_scale_eod"] = 1.0
    baseline["core_scale_held"] = 1.0
    baseline["core_scale_change"] = 0.0
    baseline["futures_resize_cost_rate"] = 0.0
    baseline["call_resize_cost_rate"] = 0.0
    baseline["high_iv_gate"] = False
    baseline["iv_threshold"] = np.nan
    baseline["min_core_scale"] = np.nan
    daily_parts = [baseline]
    policy_parts: list[pd.DataFrame] = []
    trade_parts = [baseline_trades.assign(candidate=BASELINE)]
    definitions: dict[str, tuple[float | None, float | None]] = {BASELINE: (None, None)}

    for threshold in THRESHOLDS:
        for floor in MIN_CORE_SCALES:
            candidate = candidate_name(threshold, floor)
            candidate_schedule, policy = build_candidate_schedule(
                schedule, iv_signal, threshold, floor, candidate
            )
            overlay, trades, _ = mainline.im_v12.v8.run_real_normal_close(
                upstream, options, active_im, candidate_schedule, "3m", 0.95, candidate
            )
            frame = recompose_candidate(
                official, upstream, overlay, policy, candidate, threshold, floor
            )
            frame = frame.merge(
                policy[["execution_date", "high_iv_gate"]].rename(columns={"execution_date": "date"}),
                on="date",
                how="left",
                validate="one_to_one",
            )
            daily_parts.append(frame)
            policy_parts.append(policy)
            if len(trades):
                trade_parts.append(trades)
            definitions[candidate] = (threshold, floor)

    daily = pd.concat(daily_parts, ignore_index=True, sort=False)
    policies = pd.concat(policy_parts, ignore_index=True, sort=False)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    summary, wide = build_metrics(daily, definitions)
    decision, stability, selected, gates = choose_decision(summary)

    daily_dir = RUN / "daily_outputs"
    daily_dir.mkdir(exist_ok=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    parity_checks.to_csv(RUN / "parity_checks.csv", index=False, encoding="utf-8-sig")
    gates.to_csv(RUN / "decision_gates.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(daily_dir / "daily_candidates.csv.gz", index=False, compression="gzip")
    iv_signal.to_csv(daily_dir / "iv_signal.csv.gz", index=False, compression="gzip")
    policies.to_csv(daily_dir / "policy_audit.csv.gz", index=False, compression="gzip")
    trades.to_csv(daily_dir / "put_trades.csv.gz", index=False, compression="gzip")

    meta.update(
        {
            "scan_type": "two_parameter_grid",
            "baseline": {
                "candidate": BASELINE,
                "authority": "outputs/ic_im_system_mainlines_v2",
                "put_parity_max_abs": parity_max,
            },
            "candidate_grid": [
                {
                    "candidate": candidate_name(threshold, floor),
                    "iv_threshold": threshold,
                    "min_core_scale": floor,
                }
                for threshold in THRESHOLDS
                for floor in MIN_CORE_SCALES
            ],
            "data_snapshot": {
                "source": "real CFFEX IM/MO plus CSI1000 index and local government yield",
                "start": str(official["date"].min().date()),
                "end": str(official["date"].max().date()),
                "rows": len(official),
                "real_history_under_5y": True,
                "v2_manifest_sha256": sha256(V2_MANIFEST),
            },
            "cost_model": {
                "performance_margin_per_im": 0.30,
                "cash_annual_net": 0.03,
                "futures_resize_one_way": FUTURES_RESIZE_ONE_WAY_COST,
                "call_resize_one_way": CALL_RESIZE_ONE_WAY_COST,
                "inherited_put_call_future_costs": True,
                "excluded": [
                    "bid_ask_spread",
                    "close_impact",
                    "price_limit_nonfill",
                    "order_book_capacity",
                    "dynamic_margin_hike",
                    "tax",
                    "integer_contract_rounding",
                ],
            },
            "parity_check": {
                "pass": True,
                "max_abs_error": parity_max,
                "threshold": 1e-12,
            },
            "outputs": {
                **meta["outputs"],
                "daily": str(daily_dir / "daily_candidates.csv.gz"),
                "iv_signal": str(daily_dir / "iv_signal.csv.gz"),
                "policy_audit": str(daily_dir / "policy_audit.csv.gz"),
                "put_trades": str(daily_dir / "put_trades.csv.gz"),
                "parity_checks": str(RUN / "parity_checks.csv"),
                "decision_gates": str(RUN / "decision_gates.csv"),
            },
            "decision": decision,
            "stability_label": stability,
            "selected_candidate": selected,
            "source_hashes": {
                str(SPEC.relative_to(ROOT)): sha256(SPEC),
                str(Path(__file__).relative_to(ROOT)): sha256(Path(__file__)),
                str(V2_DAILY.relative_to(ROOT)): sha256(V2_DAILY),
                str(V2_MANIFEST.relative_to(ROOT)): sha256(V2_MANIFEST),
            },
            "warnings": [
                "Real MO history begins 2022-07-22; 10Y and 5Y windows are incomplete.",
                "Fractional core/Call sizing is normalized research exposure, not integer contract execution.",
                "Frozen v2 and live/production sources were not changed.",
            ],
            "git_status_after": git_value("status", "--short"),
        }
    )
    (RUN / "scan_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (RUN / "record.md").write_text(
        make_record(meta, summary, decision, stability, selected, parity_max, iv_signal, market_checks),
        encoding="utf-8",
    )
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"cwd={ROOT}\npython im_put_iv_derisk_overlay_scan_v1.py\n")
    print(
        json.dumps(
            {
                "decision": decision,
                "stability_label": stability,
                "selected_candidate": selected,
                "parity_max_abs": parity_max,
                "run": str(RUN),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
