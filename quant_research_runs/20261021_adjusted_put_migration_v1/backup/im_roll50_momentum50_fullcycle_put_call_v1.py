from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_mo_call_overwrite_delta_tenor_v19 as v19


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_fullcycle_put_call_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
BASE_DAILY = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v1" / "daily_nav.csv.gz"
CALL_DAILY = ROOT / "outputs" / "im_mo_call_daily_d10_threat_roll_v27" / "daily_candidates.csv.gz"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

CALL_CANDIDATE = "front_d10_iv26_daily_threat5_up5_next1_max5"
REAL_START = pd.Timestamp("2022-07-22")
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
SIDE_COST = 0.0001

FROZEN_HASHES = {
    SPEC: "3bff361f712a423f72f29cadf1011e97eb2cc7189f61abf58b5d98444278dd0a",
    BASE_DAILY: "2d858c1f1eb2e5b45166af637386ece40736554f9c7e18c486c0dba7bce0e44f",
    CALL_DAILY: "f7cb51a1fe9885aba71f403f7f0ef2b5033c46c295caaa9953d767f82721b8b2",
    v19.CALL_DATA: "3c5bd3f5b4ca057a87fa8e0c0d1600980d773125b207b7d2c858500d2927f4c0",
    v19.IM_UPSTREAM: "0a3719ade254a32eaf1886dc7d00e9d84aa93498e9a2fecf2868cbefefb60b99",
}

WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)

STRATEGIES = {
    "no_call": "不卖Call",
    "call_bare_only": "Call仅覆盖裸滚50%",
    "call_both_sleeves": "裸滚袖与动量袖均卖Call",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError(f"Formal or staging output already exists: {OUTPUT}")
    if not SPEC_HASH.exists():
        raise FileNotFoundError(SPEC_HASH)
    expected_sidecar = SPEC_HASH.read_text(encoding="utf-8").split()[0].lower()
    if expected_sidecar != FROZEN_HASHES[SPEC]:
        raise RuntimeError("Specification sidecar does not match preregistered hash")
    hashes: dict[str, str] = {}
    for path, expected in FROZEN_HASHES.items():
        if not path.exists():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen input changed: {path}: {actual} != {expected}")
        hashes[str(path.relative_to(ROOT))] = actual
    return hashes


def contract_name(value: Any) -> str:
    return "" if pd.isna(value) else str(value)


def prepare_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = pd.read_csv(BASE_DAILY, parse_dates=["date"], low_memory=False).sort_values("date")
    raw = pd.read_csv(
        CALL_DAILY,
        parse_dates=["date", "call_expiry"],
        low_memory=False,
    )
    raw = raw[raw["candidate"].eq(CALL_CANDIDATE)].copy()
    model = raw[raw["layer"].eq("model") & raw["date"].lt(REAL_START)].sort_values("date")
    real = raw[raw["layer"].eq("real") & raw["date"].ge(REAL_START)].sort_values("date")
    combined_dates = pd.concat([model[["date"]], real[["date"]]], ignore_index=True)
    if combined_dates["date"].duplicated().any():
        raise RuntimeError("Duplicate combined Call dates")
    expected_dates = pd.DatetimeIndex(base["date"])
    actual_dates = pd.DatetimeIndex(combined_dates["date"].sort_values())
    if not expected_dates.equals(actual_dates):
        missing = expected_dates.difference(actual_dates)
        extra = actual_dates.difference(expected_dates)
        raise RuntimeError(f"Call/base calendar mismatch: missing={list(missing[:3])}, extra={list(extra[:3])}")

    upstream = v19.load_upstream().sort_values("date").reset_index(drop=True)
    calls = v19.prepare_calls(pd.DatetimeIndex(upstream["date"]))
    if not pd.DatetimeIndex(real["date"]).equals(pd.DatetimeIndex(upstream["date"])):
        raise RuntimeError("Real Call and IM upstream calendars differ")
    return base.reset_index(drop=True), model.reset_index(drop=True), real.reset_index(drop=True), calls


def model_overlay(raw: pd.DataFrame, target: pd.Series) -> pd.DataFrame:
    target_by_date = pd.Series(target.to_numpy(dtype=float), index=pd.DatetimeIndex(target.index))
    previous_contract = ""
    previous_scale = 0.0
    rows: list[dict[str, Any]] = []
    for row in raw.itertuples(index=False):
        day = pd.Timestamp(row.date)
        current_contract = contract_name(row.call_contract)
        target_scale = float(target_by_date.loc[day]) if current_contract else 0.0
        pnl = float(row.call_pnl_ret) * previous_scale
        if current_contract == previous_contract:
            cost = abs(target_scale - previous_scale) * SIDE_COST if current_contract else 0.0
        else:
            cost = (previous_scale if previous_contract else 0.0) * SIDE_COST
            cost += (target_scale if current_contract else 0.0) * SIDE_COST
        rows.append(
            {
                "date": day,
                "layer": "model",
                "call_contract": current_contract,
                "call_target_scale": target_scale,
                "call_pnl_ret": pnl,
                "call_cost_rate": cost,
                "call_mark_fraction": target_scale * float(row.call_mark_fraction),
                "call_margin_fraction": target_scale * float(row.call_margin_fraction),
                "call_coverage": target_scale * float(row.call_coverage),
            }
        )
        previous_contract = current_contract
        previous_scale = target_scale
    return pd.DataFrame(rows)


def require_quote(
    lookup: pd.DataFrame,
    contract: str,
    day: pd.Timestamp,
    purpose: str,
) -> pd.Series:
    quote = v19.quote_row(lookup, contract, day)
    if quote is None:
        raise RuntimeError(f"Missing {purpose} quote: {contract} {day.date()}")
    if float(quote["close"]) <= 0 or float(quote["settle"]) <= 0:
        raise RuntimeError(f"Invalid {purpose} quote: {contract} {day.date()}")
    return quote


def real_overlay(
    raw: pd.DataFrame,
    target: pd.Series,
    calls: pd.DataFrame,
) -> pd.DataFrame:
    target_by_date = pd.Series(target.to_numpy(dtype=float), index=pd.DatetimeIndex(target.index))
    upstream = v19.load_upstream().sort_values("date").reset_index(drop=True)
    prior_im = upstream["settle"].shift(1)
    prior_im.iloc[0] = upstream.iloc[0]["settle"]
    denominators = pd.Series(prior_im.to_numpy(dtype=float), index=pd.DatetimeIndex(upstream["date"]))
    lookup = calls.set_index(["contract", "date"])

    previous_contract = ""
    previous_scale = 0.0
    previous_settle = np.nan
    rows: list[dict[str, Any]] = []
    for row in raw.itertuples(index=False):
        day = pd.Timestamp(row.date)
        denominator = float(denominators.loc[day])
        current_contract = contract_name(row.call_contract)
        target_scale = float(target_by_date.loc[day]) if current_contract else 0.0
        pnl = 0.0
        cost = 0.0
        current_settle = np.nan

        if previous_contract and current_contract == previous_contract:
            quote = require_quote(lookup, current_contract, day, "active Call")
            close = float(quote["close"])
            current_settle = float(quote["settle"])
            if abs(target_scale - previous_scale) <= 1e-15:
                pnl = previous_scale * (float(previous_settle) - current_settle) / denominator
            else:
                pnl = previous_scale * (float(previous_settle) - close) / denominator
                pnl += target_scale * (close - current_settle) / denominator
                cost = abs(target_scale - previous_scale) * SIDE_COST
        elif current_contract != previous_contract:
            if previous_contract:
                old_quote = require_quote(lookup, previous_contract, day, "closing Call")
                pnl += previous_scale * (
                    float(previous_settle) - float(old_quote["close"])
                ) / denominator
                cost += previous_scale * SIDE_COST
            if current_contract:
                new_quote = require_quote(lookup, current_contract, day, "opening Call")
                close = float(new_quote["close"])
                current_settle = float(new_quote["settle"])
                pnl += target_scale * (close - current_settle) / denominator
                cost += target_scale * SIDE_COST

        rows.append(
            {
                "date": day,
                "layer": "real",
                "call_contract": current_contract,
                "call_target_scale": target_scale,
                "call_pnl_ret": pnl,
                "call_cost_rate": cost,
                "call_mark_fraction": target_scale * float(row.call_mark_fraction),
                "call_margin_fraction": target_scale * float(row.call_margin_fraction),
                "call_coverage": target_scale * float(row.call_coverage),
            }
        )
        previous_contract = current_contract
        previous_scale = target_scale
        previous_settle = current_settle if current_contract else np.nan
    return pd.DataFrame(rows)


def simulate_overlay(
    model: pd.DataFrame,
    real: pd.DataFrame,
    scale: pd.Series,
    calls: pd.DataFrame,
) -> pd.DataFrame:
    scale_by_date = pd.Series(scale.to_numpy(dtype=float), index=pd.DatetimeIndex(scale.index))
    model_scale = pd.Series(
        scale_by_date.loc[pd.DatetimeIndex(model["date"])].to_numpy(),
        index=pd.DatetimeIndex(model["date"]),
    )
    real_scale = pd.Series(
        scale_by_date.loc[pd.DatetimeIndex(real["date"])].to_numpy(),
        index=pd.DatetimeIndex(real["date"]),
    )
    return pd.concat(
        [model_overlay(model, model_scale), real_overlay(real, real_scale, calls)],
        ignore_index=True,
    ).sort_values("date").reset_index(drop=True)


def parity_audit(
    model: pd.DataFrame,
    real: pd.DataFrame,
    calls: pd.DataFrame,
) -> dict[str, Any]:
    raw = pd.concat([model, real], ignore_index=True).sort_values("date").reset_index(drop=True)
    ones = pd.Series(1.0, index=pd.DatetimeIndex(raw["date"]))
    rebuilt = simulate_overlay(model, real, ones, calls)
    merged = raw.merge(rebuilt, on=["date", "layer"], suffixes=("_raw", "_rebuilt"), validate="one_to_one")
    columns = (
        "call_pnl_ret",
        "call_cost_rate",
        "call_mark_fraction",
        "call_margin_fraction",
        "call_coverage",
    )
    by_layer: dict[str, Any] = {}
    overall = 0.0
    for layer, group in merged.groupby("layer"):
        diffs: dict[str, float] = {}
        for column in columns:
            diff = float((group[f"{column}_raw"] - group[f"{column}_rebuilt"]).abs().max())
            diffs[column] = diff
            overall = max(overall, diff)
        contract_match = bool(
            group["call_contract_raw"].fillna("").astype(str).equals(
                group["call_contract_rebuilt"].fillna("").astype(str)
            )
        )
        by_layer[layer] = {"rows": int(len(group)), "max_abs_diff": diffs, "contract_match": contract_match}
    passed = overall <= 1e-12 and all(item["contract_match"] for item in by_layer.values())
    if not passed:
        raise RuntimeError(f"Constant-one Call reconstruction parity failed: {by_layer}")
    return {"pass": passed, "overall_max_abs_diff": overall, "by_layer": by_layer}


def add_strategy(frame: pd.DataFrame, name: str, overlay: pd.DataFrame | None) -> None:
    if overlay is None:
        frame[f"{name}_call_target_scale"] = 0.0
        frame[f"{name}_call_pnl_ret"] = 0.0
        frame[f"{name}_call_cost_rate"] = 0.0
        frame[f"{name}_call_mark_fraction"] = 0.0
        frame[f"{name}_call_margin_fraction"] = 0.0
        frame[f"{name}_call_coverage"] = 0.0
    else:
        renamed = overlay.rename(
            columns={
                column: f"{name}_{column}"
                for column in (
                    "call_target_scale",
                    "call_pnl_ret",
                    "call_cost_rate",
                    "call_mark_fraction",
                    "call_margin_fraction",
                    "call_coverage",
                )
            }
        ).drop(columns=["layer", "call_contract"])
        original_len = len(frame)
        joined = frame[["date"]].merge(renamed, on="date", validate="one_to_one")
        if len(joined) != original_len:
            raise RuntimeError(f"Overlay join loss: {name}")
        for column in renamed.columns:
            if column != "date":
                frame[column] = joined[column].to_numpy()

    frame[f"{name}_pre_cash_ret"] = (
        (
            1.0
            + frame["baseline_pre_cash_ret"]
            + frame["put_fixed_0p5_core_put_pnl_ret"]
            + frame[f"{name}_call_pnl_ret"]
        )
        * (1.0 - frame["put_fixed_0p5_core_put_cost_rate"])
        * (1.0 - frame[f"{name}_call_cost_rate"])
        - 1.0
    )
    frame[f"{name}_cash_weight_raw"] = (
        frame["put_fixed_0p5_core_cash_weight"] - frame[f"{name}_call_margin_fraction"]
    )
    if frame[f"{name}_cash_weight_raw"].lt(-1e-12).any():
        raise RuntimeError(f"Call margin exceeds cash buffer: {name}")
    frame[f"{name}_cash_weight"] = frame[f"{name}_cash_weight_raw"].clip(lower=0.0)
    frame[f"{name}_ret"] = (
        frame[f"{name}_pre_cash_ret"] + frame[f"{name}_cash_weight"] * CASH_DAILY
    )
    frame[f"{name}_nav"] = (1.0 + frame[f"{name}_ret"]).cumprod()
    frame[f"{name}_drawdown"] = (
        frame[f"{name}_nav"] / frame[f"{name}_nav"].cummax() - 1.0
    )


def build_daily(
    base: pd.DataFrame,
    model: pd.DataFrame,
    real: pd.DataFrame,
    calls: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = base.copy()
    dates = pd.DatetimeIndex(frame["date"])
    bare_scale = pd.Series(0.5, index=dates)
    both_scale = pd.Series(0.5 + 0.5 * frame["momentum_weight"].to_numpy(dtype=float), index=dates)
    bare = simulate_overlay(model, real, bare_scale, calls)
    both = simulate_overlay(model, real, both_scale, calls)

    add_strategy(frame, "no_call", None)
    frozen_diff = float((frame["no_call_ret"] - frame["put_fixed_0p5_core_ret"]).abs().max())
    if frozen_diff > 1e-14:
        raise RuntimeError(f"No-Call baseline parity failed: {frozen_diff}")
    add_strategy(frame, "call_bare_only", bare)
    add_strategy(frame, "call_both_sleeves", both)

    for strategy in STRATEGIES:
        ret = frame[f"{strategy}_ret"]
        if ret.isna().any() or ret.le(-1.0).any():
            raise RuntimeError(f"Invalid return path: {strategy}")
    audit = {
        "rows": int(len(frame)),
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "model_rows": int(frame["date"].lt(REAL_START).sum()),
        "real_rows": int(frame["date"].ge(REAL_START).sum()),
        "no_call_baseline_parity_max_abs": frozen_diff,
        "momentum_weight_values": sorted(float(value) for value in frame["momentum_weight"].unique()),
        "both_call_target_values": sorted(float(value) for value in frame["call_both_sleeves_call_target_scale"].unique()),
        "min_cash_weight": {
            strategy: float(frame[f"{strategy}_cash_weight_raw"].min()) for strategy in STRATEGIES
        },
        "max_call_margin_fraction": {
            strategy: float(frame[f"{strategy}_call_margin_fraction"].max()) for strategy in STRATEGIES
        },
        "call_cost_sum": {
            strategy: float(frame[f"{strategy}_call_cost_rate"].sum()) for strategy in STRATEGIES
        },
    }
    return frame, audit


def metrics(returns: pd.Series) -> dict[str, float]:
    values = returns.astype(float)
    nav = (1.0 + values).cumprod()
    ann_return = float(nav.iloc[-1] ** (252.0 / len(values)) - 1.0)
    ann_vol = float(values.std(ddof=0) * math.sqrt(252.0))
    drawdown = nav / nav.cummax() - 1.0
    max_dd = float(drawdown.min())
    return {
        "rows": int(len(values)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": max_dd,
        "calmar": ann_return / abs(max_dd) if max_dd < -1e-12 else np.nan,
        "sharpe_repo": ann_return / ann_vol if ann_vol > 1e-12 else np.nan,
        "cumulative_return": float(nav.iloc[-1] - 1.0),
        "final_nav": float(nav.iloc[-1]),
    }


def build_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    end = frame["date"].max()
    rows: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        for window, offset in WINDOWS:
            requested = None if offset is None else end - offset
            sample = frame if requested is None else frame[frame["date"].ge(requested)]
            available = bool(requested is None or sample["date"].min() <= requested + pd.Timedelta(days=7))
            result = metrics(sample[f"{strategy}_ret"]) if available else {
                key: np.nan
                for key in (
                    "rows", "ann_return", "ann_vol", "max_dd", "calmar",
                    "sharpe_repo", "cumulative_return", "final_nav",
                )
            }
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": available,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "model_rows": int(sample["date"].lt(REAL_START).sum()),
                    "real_rows": int(sample["date"].ge(REAL_START).sum()),
                    **result,
                }
            )
    return pd.DataFrame(rows)


def build_comparison(window: pd.DataFrame) -> pd.DataFrame:
    base = window[window["strategy"].eq("no_call")].set_index("window")
    rows: list[dict[str, Any]] = []
    for strategy in ("call_bare_only", "call_both_sleeves"):
        candidate = window[window["strategy"].eq(strategy)].set_index("window")
        for name, _ in WINDOWS:
            rows.append(
                {
                    "strategy": strategy,
                    "window": name,
                    "delta_ann_return": float(candidate.loc[name, "ann_return"] - base.loc[name, "ann_return"]),
                    "delta_max_dd": float(candidate.loc[name, "max_dd"] - base.loc[name, "max_dd"]),
                    "delta_calmar": float(candidate.loc[name, "calmar"] - base.loc[name, "calmar"]),
                    "delta_final_nav": float(candidate.loc[name, "final_nav"] - base.loc[name, "final_nav"]),
                }
            )
    return pd.DataFrame(rows)


def build_drawdowns(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        nav = frame[f"{strategy}_nav"]
        dd = frame[f"{strategy}_drawdown"]
        trough_index = dd.idxmin()
        peak_index = nav.loc[:trough_index].idxmax()
        rows.append(
            {
                "strategy": strategy,
                "peak": frame.loc[peak_index, "date"].date().isoformat(),
                "trough": frame.loc[trough_index, "date"].date().isoformat(),
                "max_dd": float(dd.loc[trough_index]),
                "recovered_by_end": bool(nav.iloc[-1] >= nav.loc[peak_index]),
            }
        )
    return pd.DataFrame(rows)


def pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{100.0 * float(value):.2f}%"


def write_record(
    frame: pd.DataFrame,
    window: pd.DataFrame,
    comparison: pd.DataFrame,
    drawdowns: pd.DataFrame,
    parity: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    table = ["|路径|全周期|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in STRATEGIES.items():
        block = window[window["strategy"].eq(strategy)].set_index("window")
        cells = [f"{pct(block.loc[name, 'ann_return'])} / {pct(block.loc[name, 'max_dd'])}" for name, _ in WINDOWS]
        table.append(f"|{label}|{'|'.join(cells)}|")

    delta = ["|路径|窗口|年化变化|最大回撤变化|", "|---|---:|---:|---:|"]
    for row in comparison.itertuples(index=False):
        delta.append(
            f"|{STRATEGIES[row.strategy]}|{row.window}|{100*row.delta_ann_return:+.2f}个百分点|"
            f"{100*row.delta_max_dd:+.2f}个百分点|"
        )

    dd_rows = ["|路径|峰值日|谷底日|最大回撤|期末修复|", "|---|---:|---:|---:|---:|"]
    for row in drawdowns.itertuples(index=False):
        dd_rows.append(
            f"|{STRATEGIES[row.strategy]}|{row.peak}|{row.trough}|{pct(row.max_dd)}|"
            f"{'是' if row.recovered_by_end else '否'}|"
        )

    both_vs_bare = window.pivot(index="window", columns="strategy", values="ann_return")
    recent_advantages = {
        name: float(both_vs_bare.loc[name, "call_both_sleeves"] - both_vs_bare.loc[name, "call_bare_only"])
        for name in ("full", "10y", "5y", "3y", "1y")
    }
    text = f"""# IM 50/50 + 动态Put + 卖Call袖级对比 v1

状态：研究完成；未批准实盘  
共同样本：{frame['date'].min().date().isoformat()} 至 {frame['date'].max().date().isoformat()}

## 结果

每格为年化收益 / 最大回撤。`no_call`、两种 Call 路径都维持“Put 仅保护裸滚 50% 袖”。

{chr(10).join(table)}

## 相对不卖Call

{chr(10).join(delta)}

## 最大回撤区间

{chr(10).join(dd_rows)}

## 执行与审计

- 情况1 Call 名义固定为 0.5 倍；情况2 Call 名义集合为 {audit['both_call_target_values']}。
- 动量权重实际集合为 {audit['momentum_weight_values']}；动量变化日按收盘增减 Call，并单独计成本。
- 恒定 1 倍 Call 重建校验通过：逐日组件最大绝对误差 {parity['overall_max_abs_diff']:.3e}。
- 最低剩余现金权重：情况1 {audit['min_cash_weight']['call_bare_only']:.4f}；情况2 {audit['min_cash_weight']['call_both_sleeves']:.4f}。
- “两袖都卖 Call”相对“只裸滚袖卖 Call”的年化差值：{recent_advantages}。
- 网格关闭；Call 规则固定为每日 D10、IV≥26%、5%威胁上移并换到严格更晚挂牌期限、最多5次。
- 上市前为理论 Call/IM 代理段；2022-07-22 起为真实 IM/MO。结果是研究诊断，不同步 V2 主线或实盘。
"""
    (STAGING / "record.md").write_text(text, encoding="utf-8")


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def main() -> None:
    input_hashes = verify_inputs()
    STAGING.mkdir(parents=True)
    try:
        base, model, real, calls = prepare_inputs()
        parity = parity_audit(model, real, calls)
        daily, audit = build_daily(base, model, real, calls)
        window = build_metrics(daily)
        comparison = build_comparison(window)
        drawdowns = build_drawdowns(daily)

        keep = ["date", "phase", "momentum_weight", "total_im_units", "put_source"]
        for strategy in STRATEGIES:
            keep.extend(
                [
                    f"{strategy}_call_target_scale",
                    f"{strategy}_call_pnl_ret",
                    f"{strategy}_call_cost_rate",
                    f"{strategy}_call_mark_fraction",
                    f"{strategy}_call_margin_fraction",
                    f"{strategy}_call_coverage",
                    f"{strategy}_cash_weight",
                    f"{strategy}_ret",
                    f"{strategy}_nav",
                    f"{strategy}_drawdown",
                ]
            )
        daily[keep].to_csv(STAGING / "daily_nav.csv.gz", index=False, compression="gzip")
        window.to_csv(STAGING / "metrics_by_window.csv", index=False)
        comparison.to_csv(STAGING / "comparison.csv", index=False)
        drawdowns.to_csv(STAGING / "drawdowns.csv", index=False)

        validation = {
            "version": VERSION,
            "created_at": datetime.now().astimezone().isoformat(),
            "input_hashes": input_hashes,
            "constant_one_call_reconstruction": parity,
            "daily_audit": audit,
        }
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_record(daily, window, comparison, drawdowns, parity, audit)

        files: dict[str, dict[str, Any]] = {}
        for path in sorted(STAGING.iterdir()):
            if path.name == "run_manifest.json":
                continue
            files[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        manifest = {
            "version": VERSION,
            "status": "research_only_not_live_approved",
            "created_at": datetime.now().astimezone().isoformat(),
            "git_commit": git_value("rev-parse", "HEAD"),
            "git_dirty": bool(git_value("status", "--porcelain")),
            "spec_sha256": FROZEN_HASHES[SPEC],
            "inputs": input_hashes,
            "files": files,
        }
        (STAGING / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        STAGING.rename(OUTPUT)
        print(window.to_string(index=False))
        print(f"Formal output: {OUTPUT}")
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


if __name__ == "__main__":
    main()
