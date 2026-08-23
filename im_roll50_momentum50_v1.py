from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_v1"
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
BARE_PATH = (
    ROOT / "data" / "im_roll_momentum_blend_v1" / "upstream_im_roll_output" / "daily_nav.csv"
)
MOMENTUM_PATH = ROOT / "outputs" / "im_momentum_gated_roll_v1" / "daily_nav.csv"
WITHDRAWN_PATH = ROOT / "outputs" / "im_roll_momentum_blend_v1" / "daily_curves.csv.gz"
OUTPUT_DIR = ROOT / "outputs" / VERSION
ANNUALIZATION_DAYS = 252.0
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> str:
    expected = SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC_PATH)
    if expected != actual:
        raise RuntimeError(f"Specification hash mismatch: {expected} != {actual}")
    return actual


def build_daily() -> pd.DataFrame:
    bare = pd.read_csv(BARE_PATH, parse_dates=["date"])[
        [
            "date", "contract", "im_gross_ret", "cost_rate", "im_net_plus_cash_ret",
            "roll_from", "roll_to",
        ]
    ].rename(
        columns={
            "cost_rate": "bare_futures_cost_rate",
            "im_net_plus_cash_ret": "bare_roll_ret",
        }
    )
    momentum = pd.read_csv(MOMENTUM_PATH, parse_dates=["date"])[
        [
            "date", "target_im_units", "futures_cost_rate", "cash_weight", "strategy_ret",
        ]
    ].rename(
        columns={
            "futures_cost_rate": "momentum_futures_cost_rate",
            "cash_weight": "momentum_cash_weight",
            "strategy_ret": "momentum_gated_ret",
        }
    )
    daily = bare.merge(momentum, on="date", validate="one_to_one")
    daily["blend_ret"] = 0.5 * daily["bare_roll_ret"] + 0.5 * daily["momentum_gated_ret"]
    daily["total_im_units"] = 0.5 + 0.5 * daily["target_im_units"]
    daily["cash_weight"] = 0.5 * 0.70 + 0.5 * daily["momentum_cash_weight"]
    daily["futures_gross_ret"] = daily["total_im_units"] * daily["im_gross_ret"]
    daily["futures_cost_rate_linear"] = (
        0.5 * daily["bare_futures_cost_rate"]
        + 0.5 * daily["momentum_futures_cost_rate"]
    )
    daily["nav"] = (1.0 + daily["blend_ret"]).cumprod()
    allowed_units = {0.5, 0.75, 1.0}
    if not set(daily["total_im_units"].unique()).issubset(allowed_units):
        raise RuntimeError("Unexpected combined IM exposure")
    expected_cash = 1.0 - 0.30 * daily["total_im_units"]
    if (expected_cash - daily["cash_weight"]).abs().max() > 1e-12:
        raise RuntimeError("Combined cash weight does not match 30% margin/buffer accounting")
    return daily


def metrics_for_returns(returns: pd.Series) -> dict[str, float]:
    nav = (1.0 + returns.astype(float)).cumprod()
    ann_return = float(nav.iloc[-1] ** (ANNUALIZATION_DAYS / len(returns)) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS))
    return {
        "rows": float(len(returns)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": float((nav / nav.cummax() - 1.0).min()),
        "sharpe_repo": float(ann_return / ann_vol) if ann_vol > 1e-12 else 0.0,
        "final_nav": float(nav.iloc[-1]),
    }


def build_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    series = {
        "bare_roll_im": "bare_roll_ret",
        "momentum_gated_roll_im": "momentum_gated_ret",
        "roll50_momentum50": "blend_ret",
    }
    end = daily["date"].max()
    rows: list[dict[str, object]] = []
    for strategy, column in series.items():
        for window, offset in WINDOWS:
            requested_start = None if offset is None else end - offset
            sample = daily if requested_start is None else daily.loc[daily["date"] >= requested_start]
            actual_start = sample["date"].min()
            available = bool(
                requested_start is None or actual_start <= requested_start + pd.Timedelta(days=7)
            )
            values = metrics_for_returns(sample[column]) if available else {
                key: np.nan
                for key in ("rows", "ann_return", "ann_vol", "max_dd", "sharpe_repo", "final_nav")
            }
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": available,
                    "unavailable_reason": "" if available else (
                        f"IM history starts {daily['date'].min().date()}, shorter than requested {window} window"
                    ),
                    "start": actual_start.date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **values,
                }
            )
    metrics = pd.DataFrame(rows)
    bare = metrics.loc[metrics["strategy"].eq("bare_roll_im"), ["window", "ann_return", "max_dd"]].rename(
        columns={"ann_return": "bare_ann_return", "max_dd": "bare_max_dd"}
    )
    momentum = metrics.loc[
        metrics["strategy"].eq("momentum_gated_roll_im"), ["window", "ann_return", "max_dd"]
    ].rename(columns={"ann_return": "momentum_ann_return", "max_dd": "momentum_max_dd"})
    metrics = metrics.merge(bare, on="window", validate="many_to_one").merge(
        momentum, on="window", validate="many_to_one"
    )
    metrics["ann_delta_vs_bare_pp"] = 100.0 * (metrics["ann_return"] - metrics["bare_ann_return"])
    metrics["dd_improvement_vs_bare_pp"] = 100.0 * (metrics["max_dd"] - metrics["bare_max_dd"])
    metrics["ann_delta_vs_momentum_pp"] = 100.0 * (
        metrics["ann_return"] - metrics["momentum_ann_return"]
    )
    metrics["dd_improvement_vs_momentum_pp"] = 100.0 * (
        metrics["max_dd"] - metrics["momentum_max_dd"]
    )
    return metrics


def build_annual(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    series = {
        "bare_roll_im": "bare_roll_ret",
        "momentum_gated_roll_im": "momentum_gated_ret",
        "roll50_momentum50": "blend_ret",
    }
    for year, sample in daily.groupby(daily["date"].dt.year):
        for strategy, column in series.items():
            rows.append(
                {
                    "year": int(year),
                    "strategy": strategy,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metrics_for_returns(sample[column]),
                }
            )
    return pd.DataFrame(rows)


def withdrawn_parity(daily: pd.DataFrame) -> dict[str, object]:
    if not WITHDRAWN_PATH.exists():
        return {"available": False, "reason": str(WITHDRAWN_PATH)}
    old = pd.read_csv(WITHDRAWN_PATH, parse_dates=["date"])
    old = old.loc[old["candidate"].eq("roll50_mom50"), ["date", "strategy_ret"]]
    joined = daily[["date", "blend_ret"]].merge(old, on="date", validate="one_to_one")
    error = float((joined["blend_ret"] - joined["strategy_ret"]).abs().max())
    if error > 1e-5:
        raise RuntimeError(f"Prior aggregate-cost endpoint differs unexpectedly: {error}")
    return {
        "available": True,
        "rows": int(len(joined)),
        "max_abs_daily_error": error,
        "reason": (
            "The withdrawn run aggregated exposure before applying costs. The corrected capital-sleeve "
            "method linearly combines each sleeve's already-net daily return, avoiding gross-cost cross terms."
        ),
    }


def pct(value: object) -> str:
    if pd.isna(value):
        return "N/A"
    return f"{100.0 * float(value):.2f}%"


def write_record(daily: pd.DataFrame, metrics: pd.DataFrame, manifest: dict[str, object]) -> None:
    labels = {
        "bare_roll_im": "单纯滚IM",
        "momentum_gated_roll_im": "动量门控滚IM",
        "roll50_momentum50": "50%滚IM + 50%动量门控",
    }
    rows = [
        "|策略|全样本|近10年|近5年|近3年|近1年|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for strategy, label in labels.items():
        block = metrics.loc[metrics["strategy"].eq(strategy)].set_index("window")
        values = []
        for window, _ in WINDOWS:
            row = block.loc[window]
            values.append(f"{pct(row['ann_return'])} / {pct(row['max_dd'])}" if row["available"] else "N/A")
        rows.append(f"|{label}|{'|'.join(values)}|")
    blend = metrics.loc[metrics["strategy"].eq("roll50_momentum50") & metrics["available"]]
    delta_rows = [
        "|窗口|相对单纯滚IM年化差|相对单纯滚IM回撤改善|相对动量门控年化差|相对动量门控回撤变化|",
        "|---|---:|---:|---:|---:|",
    ]
    for row in blend.itertuples(index=False):
        delta_rows.append(
            f"|{row.window}|{row.ann_delta_vs_bare_pp:+.2f}pp|{row.dd_improvement_vs_bare_pp:+.2f}pp|"
            f"{row.ann_delta_vs_momentum_pp:+.2f}pp|{row.dd_improvement_vs_momentum_pp:+.2f}pp|"
        )
    text = f"""# 50%单纯滚IM + 50%动量门控滚IM v1

状态：用户指定的固定比例研究完成；未批准实盘  
数据截止：{manifest['sample']['end']}

## 1. Scope

- 固定50%资本配置始终滚动IM，50%配置动量门控滚IM。
- 这是两袖资本等权，不是1倍裸滚再叠加额外杠杆。
- 结果来自真实官方数据运行，不是估算。

## 2. Code and Data Provenance

- IM：中金所官方逐月结算价；信号：中证指数官方`000852`价格指数。
- 正式样本：{manifest['sample']['start']}至{manifest['sample']['end']}，{manifest['sample']['rows']}个共同交易日。
- 价格指数不复权；时区Asia/Shanghai。

## 3. Execution Details

- 单纯滚IM袖始终保持该袖1倍名义；动量袖目标为0/0.5/1，有仓才滚动。
- 组合总IM名义为0.5/0.75/1倍，样本平均{daily['total_im_units'].mean():.3f}倍。
- 单边成本1bp；每1倍IM按30%保证金及缓冲，其余现金按净年化3%计息。
- Put、Call和网格全部关闭。

## 4. Comparison Setup

- 三条路径使用相同日期、同一IM合约序列、相同成本与现金假设。
- 组合逐日收益严格等于两条输入逐日净收益各50%，再进行复利。

## 5. Key Results

每格为年化收益 / 最大回撤。

{chr(10).join(rows)}

### 50/50组合的相对变化

正的回撤变化表示回撤变浅。

{chr(10).join(delta_rows)}

## 6. Trading Frictions and Constraints

- 已含进出成本、实际持仓换月成本和现金收益；未含盘口滑点、冲击成本、动态保证金上调和容量限制。

## 7. Integrity Checks

- 两条输入逐日一对一对齐；组合日收益等权重构误差0。
- 总IM名义只出现0.5、0.75、1；现金权重与30%保证金口径一致。
- 与旧误解释报告的聚合成本算法逐日最大差异为{manifest['withdrawn_endpoint_parity'].get('max_abs_daily_error', float('nan')):.3e}；旧算法先合并敞口再乘成本，本版按两袖已扣费日收益各50%线性相加，后者才是正式资本分袖口径。

## 8. Risks and Caveats

- IM历史不足5年，10年和5年窗口为N/A；现有优势尚未跨完整周期验证。
- 现金年化3%为研究假设；本结果不是当前交易信号。

## 9. Backup and Rollback

- 本版只新增独立规格、脚本和输出，没有覆盖现有两条策略；回滚方式为停用本组合研究路径。

## Decision

- `user_selected_fixed_50_50_research_path`：固定保留50%单纯滚IM + 50%动量门控滚IM研究路径，未批准实盘。
"""
    (OUTPUT_DIR / "record.md").write_text(text, encoding="utf-8")


def run() -> None:
    if OUTPUT_DIR.exists():
        raise FileExistsError(f"Formal output already exists: {OUTPUT_DIR}")
    spec_hash = verify_spec()
    daily = build_daily()
    metrics = build_metrics(daily)
    annual = build_annual(daily)
    parity = withdrawn_parity(daily)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT_DIR / "daily_nav.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(OUTPUT_DIR / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(OUTPUT_DIR / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    command = f"{Path(sys.executable).name} {Path(__file__).name}"
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "script_sha256": sha256_file(Path(__file__)),
        "spec_sha256": spec_hash,
        "sample": {
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "rows": int(len(daily)),
            "timezone": "Asia/Shanghai",
        },
        "inputs": {
            "bare_roll_daily": str(BARE_PATH),
            "bare_roll_daily_sha256": sha256_file(BARE_PATH),
            "momentum_gated_daily": str(MOMENTUM_PATH),
            "momentum_gated_daily_sha256": sha256_file(MOMENTUM_PATH),
        },
        "allocation": {
            "bare_roll_share": 0.5,
            "momentum_gated_share": 0.5,
            "possible_total_im_units": [0.5, 0.75, 1.0],
            "average_total_im_units": float(daily["total_im_units"].mean()),
            "average_cash_weight": float(daily["cash_weight"].mean()),
        },
        "cost_and_overlays": {
            "one_way_cost": 0.0001,
            "margin_buffer_per_1x": 0.30,
            "cash_assumed_net_annual_return": 0.03,
            "put": "excluded",
            "call": "excluded",
            "grid": "excluded",
        },
        "withdrawn_endpoint_parity": parity,
        "decision": "user_selected_fixed_50_50_research_path",
    }
    (OUTPUT_DIR / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT_DIR / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(daily, metrics, manifest)


if __name__ == "__main__":
    run()
