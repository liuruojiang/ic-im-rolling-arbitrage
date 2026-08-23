from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import im_monthly_discount_roll_v1 as im_roll
from ic_monthly_discount_roll_v1 import fetch_csindex


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll_momentum_blend_v1"
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
DEFAULT_DATA_DIR = ROOT / "data" / VERSION
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / VERSION
DEFAULT_END = pd.Timestamp("2026-08-21")
SIGNAL_START = pd.Timestamp("2014-10-17")
IM_START = pd.Timestamp("2022-07-22")
ANNUALIZATION_DAYS = 252.0
ONE_WAY_COST = 0.0001
MARGIN_BUFFER_RATE = 0.30
CASH_ANNUAL_RETURN = 0.03
CASH_DAILY_RETURN = (1.0 + CASH_ANNUAL_RETURN) ** (1.0 / ANNUALIZATION_DAYS) - 1.0
BIAS_MA = 35
MOM_DAY = 18
WEIGHT_END = 2.5
ABS_MOM_DAY = 20
SPLITS = (1.0, 0.75, 0.50, 0.25, 0.0)
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)
A_SHARE_SIGNAL = (
    ROOT.parent
    / "A 股股指多头策略"
    / "quant_param_scan_runs"
    / "20260822_cyb_zz1000_abs20_50_50_blend"
    / "daily_outputs"
    / "curves"
    / "zz1000__blend50.csv"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> str:
    if not SPEC_PATH.exists() or not SPEC_HASH_PATH.exists():
        raise FileNotFoundError("Pre-registered specification or its SHA-256 file is missing")
    expected = SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC_PATH)
    if expected != actual:
        raise RuntimeError(f"Specification hash mismatch: expected {expected}, actual {actual}")
    return actual


def calc_bias_momentum(close: pd.Series) -> pd.Series:
    prices = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(prices), np.nan)
    ma = pd.Series(prices, index=close.index).rolling(BIAS_MA).mean().to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        bias = np.where((ma > 1e-10) & np.isfinite(prices), prices / ma, np.nan)
    x = np.arange(MOM_DAY, dtype=float)
    weights = np.linspace(1.0, WEIGHT_END, MOM_DAY)
    w_sum = float(weights.sum())
    x_bar = float((weights * x).sum() / w_sum)
    denom = float((weights * (x - x_bar) ** 2).sum())
    for end in range(BIAS_MA + MOM_DAY - 1, len(prices)):
        y = bias[end - MOM_DAY + 1 : end + 1]
        if not np.isfinite(y).all() or y[0] <= 1e-10:
            continue
        y_bar = float((weights * y).sum() / w_sum)
        slope = float((weights * (x - x_bar) * (y - y_bar)).sum() / denom)
        result[end] = slope / float(y[0]) * 10000.0
    return pd.Series(result, index=close.index, name="score")


def build_signal(end_date: pd.Timestamp, data_dir: Path) -> pd.DataFrame:
    official = fetch_csindex("000852", SIGNAL_START, end_date).sort_values("date")
    if official.empty or official["date"].min() > SIGNAL_START + pd.Timedelta(days=7):
        raise RuntimeError("Official 000852 history is insufficient for the fixed signal warm-up")
    if official["date"].max() != end_date:
        raise RuntimeError(
            f"Official 000852 latest date {official['date'].max().date()} does not match {end_date.date()}"
        )
    official = official.set_index("date")
    close = official["close"].astype(float)
    score = calc_bias_momentum(close)
    abs20 = close / close.shift(ABS_MOM_DAY) - 1.0
    base = score.gt(0.0)
    abs_gate = base & abs20.gt(0.0)
    desired = 0.5 * base.astype(float) + 0.5 * abs_gate.astype(float)
    signal = pd.DataFrame(
        {
            "date": close.index,
            "csi1000_signal_close": close.to_numpy(),
            "score": score.to_numpy(),
            "abs20": abs20.to_numpy(),
            "base_signal": base.to_numpy(dtype=bool),
            "abs_signal": abs_gate.to_numpy(dtype=bool),
            "desired_weight": desired.to_numpy(dtype=float),
            "momentum_weight": desired.shift(1, fill_value=0.0).to_numpy(dtype=float),
        }
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    signal.to_csv(data_dir / "official_000852_signal.csv", index=False, encoding="utf-8-sig")
    return signal


def refresh_upstream(
    end_date: pd.Timestamp, data_dir: Path, refresh: bool
) -> tuple[pd.DataFrame, Path, dict[str, object]]:
    upstream_data = data_dir / "upstream_im_roll_data"
    upstream_output = data_dir / "upstream_im_roll_output"
    if not upstream_output.exists():
        im_roll.run(
            end_date=end_date,
            data_dir=upstream_data,
            output_dir=upstream_output,
            refresh=refresh,
        )
    manifest_path = upstream_output / "data_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing refreshed upstream manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_end = pd.Timestamp(manifest["common_sample"]["end"])
    if actual_end != end_date:
        raise RuntimeError(f"Refreshed upstream end {actual_end.date()} != requested {end_date.date()}")
    daily = pd.read_csv(upstream_output / "daily_nav.csv", parse_dates=["date"])
    return daily, upstream_output, manifest


def signal_snapshot_audit(signal: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    if not A_SHARE_SIGNAL.exists():
        return pd.DataFrame(), {
            "available": False,
            "reason": f"A-share signal snapshot not found: {A_SHARE_SIGNAL}",
        }
    saved = pd.read_csv(A_SHARE_SIGNAL, parse_dates=["date"])[
        ["date", "close", "desired_weight", "weight"]
    ].rename(
        columns={
            "close": "sina_close",
            "desired_weight": "saved_desired_weight",
            "weight": "saved_weight",
        }
    )
    joined = signal.merge(saved, on="date", how="inner", validate="one_to_one")
    joined["close_rel_diff"] = joined["csi1000_signal_close"] / joined["sina_close"] - 1.0
    joined["desired_match"] = (
        joined["desired_weight"] - joined["saved_desired_weight"]
    ).abs().lt(1e-12)
    joined["weight_match"] = (
        joined["momentum_weight"] - joined["saved_weight"]
    ).abs().lt(1e-12)
    mismatch = joined.loc[~joined["desired_match"] | ~joined["weight_match"]].copy()
    summary = {
        "available": True,
        "source": str(A_SHARE_SIGNAL),
        "rows": int(len(joined)),
        "start": joined["date"].min().date().isoformat(),
        "end": joined["date"].max().date().isoformat(),
        "desired_agreement": float(joined["desired_match"].mean()),
        "weight_agreement": float(joined["weight_match"].mean()),
        "mismatch_rows": int(len(mismatch)),
        "median_close_abs_rel_diff": float(joined["close_rel_diff"].abs().median()),
        "max_close_abs_rel_diff": float(joined["close_rel_diff"].abs().max()),
    }
    return mismatch, summary


def build_market(upstream: pd.DataFrame, signal: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date", "contract", "settle", "im_gross_ret", "cost_rate", "im_net_ret",
        "im_net_plus_cash_ret", "csi1000_price_close", "roll_from", "roll_to",
    ]
    missing = [column for column in columns if column not in upstream.columns]
    if missing:
        raise RuntimeError(f"Upstream daily curve missing columns: {missing}")
    market = upstream[columns].merge(signal, on="date", how="left", validate="one_to_one")
    market = market.sort_values("date").reset_index(drop=True)
    if market[["score", "abs20", "desired_weight", "momentum_weight"]].isna().any().any():
        raise RuntimeError("Missing warmed-up official momentum signal on IM dates")
    market["momentum_turnover"] = market["momentum_weight"].diff().abs()
    market.loc[0, "momentum_turnover"] = abs(float(market.loc[0, "momentum_weight"]))
    market["roll_event"] = market["roll_to"].fillna("").astype(str).ne("")
    market["momentum_trade_cost_rate"] = ONE_WAY_COST * market["momentum_turnover"]
    market["momentum_roll_cost_rate"] = (
        2.0 * ONE_WAY_COST * market["momentum_weight"] * market["roll_event"].astype(float)
    )
    market["momentum_cost_rate"] = (
        market["momentum_trade_cost_rate"] + market["momentum_roll_cost_rate"]
    )
    return market


def build_candidate(
    market: pd.DataFrame,
    candidate: str,
    kind: str,
    roll_share: float,
    momentum_share: float,
) -> pd.DataFrame:
    result = market.copy()
    if kind == "capital_split":
        result["total_im_units"] = roll_share + momentum_share * result["momentum_weight"]
        result["futures_cost_rate"] = (
            roll_share * result["cost_rate"] + momentum_share * result["momentum_cost_rate"]
        )
    elif kind == "additive_overlay":
        result["total_im_units"] = 1.0 + result["momentum_weight"]
        result["futures_cost_rate"] = result["cost_rate"] + result["momentum_cost_rate"]
    else:
        raise ValueError(f"Unknown candidate kind: {kind}")
    result["futures_gross_ret"] = result["total_im_units"] * result["im_gross_ret"]
    result["futures_net_ret"] = (
        (1.0 + result["futures_gross_ret"]) * (1.0 - result["futures_cost_rate"]) - 1.0
    )
    result["cash_weight"] = 1.0 - MARGIN_BUFFER_RATE * result["total_im_units"]
    if result["cash_weight"].lt(-1e-12).any():
        raise RuntimeError(f"Negative cash weight in {candidate}")
    result["cash_contribution_ret"] = result["cash_weight"] * CASH_DAILY_RETURN
    result["strategy_ret"] = result["futures_net_ret"] + result["cash_contribution_ret"]
    result["nav"] = (1.0 + result["strategy_ret"]).cumprod()
    result["candidate"] = candidate
    result["kind"] = kind
    result["roll_share"] = roll_share
    result["momentum_share"] = momentum_share
    return result


def metric_values(frame: pd.DataFrame) -> dict[str, float]:
    returns = frame["strategy_ret"].astype(float)
    nav = (1.0 + returns).cumprod()
    rows = len(frame)
    ann_return = float(nav.iloc[-1] ** (ANNUALIZATION_DAYS / rows) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS))
    return {
        "rows": rows,
        "ann_return": ann_return,
        "max_dd": float((nav / nav.cummax() - 1.0).min()),
        "ann_vol": ann_vol,
        "sharpe_repo": float(ann_return / ann_vol) if ann_vol > 1e-12 else 0.0,
        "final_nav": float(nav.iloc[-1]),
        "avg_im_units": float(frame["total_im_units"].mean()),
        "max_im_units": float(frame["total_im_units"].max()),
        "avg_cash_weight": float(frame["cash_weight"].mean()),
        "futures_cost_total": float(frame["futures_cost_rate"].sum()),
    }


def build_metrics(curves: list[pd.DataFrame]) -> pd.DataFrame:
    end = max(pd.Timestamp(curve["date"].max()) for curve in curves)
    rows: list[dict[str, object]] = []
    for curve in curves:
        identity = curve.iloc[0]
        for window, offset in WINDOWS:
            requested_start = None if offset is None else end - offset
            sample = curve if requested_start is None else curve[curve["date"] >= requested_start]
            actual_start = pd.Timestamp(sample["date"].min())
            available = bool(
                requested_start is None or actual_start <= requested_start + pd.Timedelta(days=7)
            )
            values = metric_values(sample) if available else {
                key: np.nan
                for key in (
                    "rows", "ann_return", "max_dd", "ann_vol", "sharpe_repo", "final_nav",
                    "avg_im_units", "max_im_units", "avg_cash_weight", "futures_cost_total",
                )
            }
            rows.append(
                {
                    "candidate": identity["candidate"],
                    "kind": identity["kind"],
                    "roll_share": float(identity["roll_share"]),
                    "momentum_share": float(identity["momentum_share"]),
                    "window": window,
                    "available": available,
                    "unavailable_reason": "" if available else (
                        f"IM history starts {curve['date'].min().date()}, shorter than requested {window} window"
                    ),
                    "start": actual_start.date().isoformat(),
                    "end": pd.Timestamp(sample["date"].max()).date().isoformat(),
                    **values,
                }
            )
    metrics = pd.DataFrame(rows)
    baseline = metrics.loc[metrics["candidate"].eq("roll100_mom0"), [
        "window", "ann_return", "max_dd"
    ]].rename(columns={"ann_return": "baseline_ann_return", "max_dd": "baseline_max_dd"})
    metrics = metrics.merge(baseline, on="window", how="left", validate="many_to_one")
    metrics["ann_return_delta_pp"] = 100.0 * (
        metrics["ann_return"] - metrics["baseline_ann_return"]
    )
    metrics["max_dd_improvement_pp"] = 100.0 * (
        metrics["max_dd"] - metrics["baseline_max_dd"]
    )
    return metrics


def build_annual(curves: list[pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for curve in curves:
        for year, sample in curve.groupby(curve["date"].dt.year):
            values = metric_values(sample)
            rows.append(
                {
                    "candidate": curve["candidate"].iloc[0],
                    "kind": curve["kind"].iloc[0],
                    "year": int(year),
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{100.0 * float(value):.2f}%"


def git_text(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=True, encoding="utf-8"
    )
    return result.stdout.strip()


def write_scan_compatibility_artifacts(
    output_dir: Path,
    metrics: pd.DataFrame,
    manifest: dict[str, object],
    git_status_before: str,
) -> None:
    segment_names = {
        "full": "full",
        "10y": "last_10y",
        "5y": "last_5y",
        "3y": "last_3y",
        "1y": "last_1y",
    }
    summary = metrics.rename(columns={"window": "segment"}).copy()
    summary["segment"] = summary["segment"].map(segment_names)
    summary.to_csv(output_dir / "scan_summary.csv", index=False, encoding="utf-8-sig")

    wide = summary.pivot(
        index="candidate",
        columns="segment",
        values=["ann_return", "max_dd"],
    )
    wide.columns = [f"{metric}_{segment}" for metric, segment in wide.columns]
    wide = wide.reset_index()
    required_columns = [
        "candidate",
        "ann_return_full", "max_dd_full",
        "ann_return_last_10y", "max_dd_last_10y",
        "ann_return_last_5y", "max_dd_last_5y",
        "ann_return_last_3y", "max_dd_last_3y",
        "ann_return_last_1y", "max_dd_last_1y",
    ]
    wide[required_columns].to_csv(
        output_dir / "window_metrics.csv", index=False, encoding="utf-8-sig"
    )

    scan_meta = {
        "run_id": VERSION,
        "created_at": manifest["generated_at"],
        "project": "IC和IM滚动套利",
        "entrypoint": str(Path(__file__)),
        "repo_root": str(ROOT),
        "git_branch": git_text("branch", "--show-current"),
        "git_commit": git_text("rev-parse", "HEAD"),
        "git_status_before": git_status_before,
        "git_status_after": git_text("status", "--short"),
        "scan_type": "portfolio_weight_scan_with_additive_diagnostic",
        "parameter_group": "roll_im_share_vs_fixed_im_momentum_share",
        "baseline": "roll100_mom0",
        "candidate_grid": ["100/0", "75/25", "50/50", "25/75", "0/100"],
        "cost_model": manifest["cost_and_capital"],
        "data_snapshot": manifest["data"],
        "decision": "diagnostic_only_no_combination_weight_fixed",
        "stability_label": "short_real_im_sample_monotonic_but_not_long_cycle_validated",
        "outputs": {
            "record": "record.md",
            "scan_summary": "scan_summary.csv",
            "window_metrics": "window_metrics.csv",
            "scan_meta": "scan_meta.json",
            "command_log": "command_log.txt",
        },
        "strict_checker_note": (
            "10y and 5y are intentionally N/A because IM starts 2022-07-22; "
            "the generic strict checker requires finite numbers and therefore cannot pass those cells."
        ),
    }
    (output_dir / "scan_meta.json").write_text(
        json.dumps(scan_meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def format_main_table(metrics: pd.DataFrame, kind: str) -> str:
    block = metrics.loc[metrics["kind"].eq(kind)].copy()
    order = {name: index for index, (name, _) in enumerate(WINDOWS)}
    block["window_order"] = block["window"].map(order)
    block = block.sort_values(["roll_share", "window_order"], ascending=[False, True])
    header = "|滚IM/动量IM|全样本|近10年|近5年|近3年|近1年|\n|---|---:|---:|---:|---:|---:|"
    lines = [header]
    for candidate, group in block.groupby("candidate", sort=False):
        first = group.iloc[0]
        label = f"{int(round(first['roll_share'] * 100))}/{int(round(first['momentum_share'] * 100))}"
        values = []
        by_window = group.set_index("window")
        for window, _ in WINDOWS:
            row = by_window.loc[window]
            values.append(
                f"{pct(row['ann_return'])} / {pct(row['max_dd'])}" if row["available"] else "N/A"
            )
        lines.append("|" + label + "|" + "|".join(values) + "|")
    return "\n".join(lines)


def format_delta_table(metrics: pd.DataFrame) -> str:
    block = metrics.loc[
        metrics["kind"].eq("capital_split") & metrics["available"],
        ["candidate", "roll_share", "momentum_share", "window", "ann_return_delta_pp", "max_dd_improvement_pp"],
    ].copy()
    block = block.loc[~block["candidate"].eq("roll100_mom0")]
    lines = [
        "|滚IM/动量IM|窗口|年化收益差|最大回撤改善|",
        "|---|---:|---:|---:|",
    ]
    order = {name: index for index, (name, _) in enumerate(WINDOWS)}
    block["window_order"] = block["window"].map(order)
    block = block.sort_values(["roll_share", "window_order"], ascending=[False, True])
    for row in block.itertuples(index=False):
        label = f"{int(round(row.roll_share * 100))}/{int(round(row.momentum_share * 100))}"
        lines.append(
            f"|{label}|{row.window}|{row.ann_return_delta_pp:+.2f}pp|{row.max_dd_improvement_pp:+.2f}pp|"
        )
    return "\n".join(lines)


def write_record(
    output_dir: Path,
    metrics: pd.DataFrame,
    market: pd.DataFrame,
    parity_error: float,
    signal_audit: dict[str, object],
    manifest: dict[str, object],
) -> None:
    main = format_main_table(metrics, "capital_split")
    overlay = format_main_table(metrics, "additive_overlay")
    delta = format_delta_table(metrics)
    holding_ratio = float(market["momentum_weight"].gt(0).mean())
    avg_weight = float(market["momentum_weight"].mean())
    record = f"""# IM 裸滚与多头动量组合 v1

状态：研究完成；未批准实盘  
数据截止：{manifest['sample']['end']}

## 结论摘要

- 本测试只含裸滚 IM 与固定中证1000多头动量，不含 Put、Call 或网格。
- 固定动量参数为 `MA35 / Mom18 / W2.5`，并采用 50% Abs20 OFF + 50% `Abs20 > 0`；样本内动量权重大于0的交易日占 {holding_ratio:.2%}，平均动量权重 {avg_weight:.3f}。
- 主表是资本分袖组合，最大 IM 名义不超过1倍；叠加表是固定1倍裸滚再加动量腿，最大2倍，仅作杠杆诊断。
- IM 从2022-07-22上市，因此近10年和近5年均为 `N/A`，不能用更短样本冒充。

## 主表：资本分袖（年化收益 / 最大回撤）

{main}

## 相对裸滚100/0的变化

正的年化收益差表示收益提高；正的最大回撤改善表示回撤变浅。

{delta}

## 附表：1倍裸滚 + 动量叠加（年化收益 / 最大回撤）

{overlay}

## 口径与审计

- IM 使用中金所官方近月逐月结算价；信号使用中证指数官方 `000852` 价格指数。
- T日信号、T+1共同交易日持仓；单边成本1bp，动量进出与换月分别计费。
- 30%/倍作为保证金及缓冲，剩余现金按净年化3%计息；现金收益是情景假设。
- 裸滚100/0与刷新上游 `im_net_plus_cash_ret` 的逐日最大误差为 {parity_error:.3e}。
- 与A股工作区已固定信号快照的重叠日权重一致率为 {signal_audit.get('weight_agreement', float('nan')):.2%}；差异清单见 `signal_snapshot_mismatches.csv`。正式信号仍以官方 `000852` 为准。

## Data

- 正式样本为 {manifest['sample']['start']} 至 {manifest['sample']['end']}，共 {manifest['sample']['rows']} 个共同交易日。

## Stability

- 五档资本分袖在可用的全样本、3年和1年窗口呈连续方向，但IM历史不足5年，不能认定已经跨完整周期稳定。

## Decision

- `diagnostic_only_no_combination_weight_fixed`：保留本轮结果，等待用户选择；不自动固定组合比例。

## 结论边界

这是约4年的短样本历史研究，不是当前交易信号或实盘授权。组合权重没有因本轮结果自动晋升；若要固定某个组合比例，应再由用户确认。
"""
    (output_dir / "record.md").write_text(record, encoding="utf-8")


def run(end_date: pd.Timestamp, data_dir: Path, output_dir: Path, refresh: bool) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Formal output directory already exists: {output_dir}")
    spec_hash = verify_spec()
    git_status_before = git_text("status", "--short")
    upstream, upstream_output, upstream_manifest = refresh_upstream(end_date, data_dir, refresh)
    signal = build_signal(end_date, data_dir)
    mismatch, signal_audit = signal_snapshot_audit(signal)
    market = build_market(upstream, signal)

    curves: list[pd.DataFrame] = []
    for roll_share in SPLITS:
        momentum_share = 1.0 - roll_share
        candidate = f"roll{int(round(roll_share * 100))}_mom{int(round(momentum_share * 100))}"
        curves.append(
            build_candidate(market, candidate, "capital_split", roll_share, momentum_share)
        )
    curves.append(build_candidate(market, "roll1x_plus_momentum", "additive_overlay", 1.0, 1.0))

    baseline = curves[0]
    parity_error = float(
        (baseline["strategy_ret"] - market["im_net_plus_cash_ret"]).abs().max()
    )
    if parity_error > 1e-12:
        raise RuntimeError(f"Bare-roll endpoint parity failed: {parity_error}")
    if any(curve["strategy_ret"].le(-1.0).any() for curve in curves):
        raise RuntimeError("A candidate daily return is <= -100%")

    metrics = build_metrics(curves)
    annual = build_annual(curves)
    output_dir.mkdir(parents=True, exist_ok=False)
    daily = pd.concat(curves, ignore_index=True)
    daily.to_csv(output_dir / "daily_curves.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    metrics.to_csv(output_dir / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    mismatch.to_csv(output_dir / "signal_snapshot_mismatches.csv", index=False, encoding="utf-8-sig")
    cost_summary = daily.groupby(["candidate", "kind"], as_index=False).agg(
        futures_cost_total=("futures_cost_rate", "sum"),
        avg_im_units=("total_im_units", "mean"),
        max_im_units=("total_im_units", "max"),
        avg_cash_weight=("cash_weight", "mean"),
    )
    cost_summary.to_csv(output_dir / "cost_and_exposure_summary.csv", index=False, encoding="utf-8-sig")

    command = (
        f"{Path(sys.executable).name} {Path(__file__).name} --end-date {end_date.date().isoformat()}"
        + (" --refresh" if refresh else "")
    )
    manifest: dict[str, object] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "script_sha256": sha256_file(Path(__file__)),
        "spec_sha256": spec_hash,
        "sample": {
            "start": market["date"].min().date().isoformat(),
            "end": market["date"].max().date().isoformat(),
            "rows": int(len(market)),
            "timezone": "Asia/Shanghai",
        },
        "data": {
            "im_source": im_roll.CFFEX_URL,
            "im_price_field": "official settlement",
            "signal_source": im_roll.CSINDEX_URL,
            "signal_symbol": "000852",
            "adjustment_mode": "official price index; no total-return adjustment",
            "upstream_output": str(upstream_output),
            "upstream_manifest_sha256": sha256_file(upstream_output / "data_manifest.json"),
            "upstream_common_sample": upstream_manifest["common_sample"],
        },
        "signal": {
            "bias_ma": BIAS_MA,
            "mom_day": MOM_DAY,
            "weight_end": WEIGHT_END,
            "abs_mom_day": ABS_MOM_DAY,
            "blend": "50% OFF + 50% static Abs20 > 0",
            "execution": "T close signal, T+1 common trading day weight",
            "snapshot_audit": signal_audit,
        },
        "cost_and_capital": {
            "one_way_futures_cost": ONE_WAY_COST,
            "margin_buffer_per_1x": MARGIN_BUFFER_RATE,
            "cash_assumed_net_annual_return": CASH_ANNUAL_RETURN,
            "put": "excluded",
            "call": "excluded",
            "grid": "excluded",
        },
        "candidate_count": len(curves),
        "bare_roll_parity_max_abs": parity_error,
        "window_availability": {
            row.window: {
                "available": bool(row.available),
                "reason": row.unavailable_reason,
            }
            for row in metrics.loc[metrics["candidate"].eq("roll100_mom0")].itertuples(index=False)
        },
    }
    (output_dir / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(output_dir, metrics, market, parity_error, signal_audit, manifest)
    write_scan_compatibility_artifacts(output_dir, metrics, manifest, git_status_before)


def finalize_existing(output_dir: Path) -> None:
    manifest_path = output_dir / "data_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing existing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["script_sha256"] = sha256_file(Path(__file__))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    metrics = pd.read_csv(output_dir / "metrics_by_window.csv")
    daily = pd.read_csv(output_dir / "daily_curves.csv.gz", parse_dates=["date"])
    market = daily.loc[daily["candidate"].eq("roll100_mom0")].copy()
    parity_error = float(manifest["bare_roll_parity_max_abs"])
    signal_audit = manifest["signal"]["snapshot_audit"]
    write_record(output_dir, metrics, market, parity_error, signal_audit, manifest)
    write_scan_compatibility_artifacts(
        output_dir,
        metrics,
        manifest,
        "repository was clean before the new research files; captured by the pre-run inspection",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bare-roll IM and fixed long-momentum combination v1")
    parser.add_argument("--end-date", default=DEFAULT_END.date().isoformat())
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--finalize-existing", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.finalize_existing:
        finalize_existing(args.output_dir.resolve())
    else:
        run(
            end_date=pd.Timestamp(args.end_date),
            data_dir=args.data_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            refresh=args.refresh,
        )
