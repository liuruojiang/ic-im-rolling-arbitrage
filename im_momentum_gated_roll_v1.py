from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_momentum_gated_roll_v1"
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
DEFAULT_UPSTREAM = ROOT / "data" / "im_roll_momentum_blend_v1" / "upstream_im_roll_output"
DEFAULT_SIGNAL = ROOT / "data" / "im_roll_momentum_blend_v1" / "official_000852_signal.csv"
DEFAULT_OUTPUT = ROOT / "outputs" / VERSION
WITHDRAWN_OUTPUT = ROOT / "outputs" / "im_roll_momentum_blend_v1"
ANNUALIZATION_DAYS = 252.0
ONE_WAY_COST = 0.0001
MARGIN_BUFFER_RATE = 0.30
CASH_ANNUAL_RETURN = 0.03
CASH_DAILY_RETURN = (1.0 + CASH_ANNUAL_RETURN) ** (1.0 / ANNUALIZATION_DAYS) - 1.0
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


def load_inputs(upstream_dir: Path, signal_path: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    manifest_path = upstream_dir / "data_manifest.json"
    daily_path = upstream_dir / "daily_nav.csv"
    for path in (manifest_path, daily_path, signal_path):
        if not path.exists():
            raise FileNotFoundError(path)
    upstream_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    upstream = pd.read_csv(daily_path, parse_dates=["date"])
    signal = pd.read_csv(signal_path, parse_dates=["date"])[
        [
            "date", "csi1000_signal_close", "score", "abs20", "desired_weight",
            "momentum_weight",
        ]
    ]
    required = {
        "date", "contract", "settle", "im_gross_ret", "cost_rate",
        "csi1000_price_close", "roll_from", "roll_to",
    }
    missing = sorted(required - set(upstream.columns))
    if missing:
        raise RuntimeError(f"Upstream daily file missing columns: {missing}")
    daily = upstream[list(required)].merge(signal, on="date", how="left", validate="one_to_one")
    daily = daily.sort_values("date").reset_index(drop=True)
    expected_end = pd.Timestamp(upstream_manifest["common_sample"]["end"])
    if daily["date"].max() != expected_end:
        raise RuntimeError("Upstream manifest end date does not match daily data")
    if daily[["score", "abs20", "desired_weight", "momentum_weight"]].isna().any().any():
        raise RuntimeError("Missing warmed-up momentum signal on executable IM dates")
    allowed_weights = {0.0, 0.5, 1.0}
    observed_weights = set(daily["momentum_weight"].astype(float).unique())
    if not observed_weights.issubset(allowed_weights):
        raise RuntimeError(f"Unexpected momentum weights: {sorted(observed_weights)}")
    return daily, upstream_manifest


def simulate(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = daily.copy()
    result["target_im_units"] = result["momentum_weight"].astype(float)
    result["turnover"] = result["target_im_units"].diff().abs()
    result.loc[0, "turnover"] = abs(float(result.loc[0, "target_im_units"]))
    result["roll_event"] = result["roll_to"].fillna("").astype(str).ne("")
    result["trade_cost_rate"] = ONE_WAY_COST * result["turnover"]
    result["roll_cost_rate"] = (
        2.0 * ONE_WAY_COST * result["target_im_units"] * result["roll_event"].astype(float)
    )
    result["futures_cost_rate"] = result["trade_cost_rate"] + result["roll_cost_rate"]
    result["futures_gross_ret"] = result["target_im_units"] * result["im_gross_ret"]
    result["futures_net_ret"] = (
        (1.0 + result["futures_gross_ret"]) * (1.0 - result["futures_cost_rate"]) - 1.0
    )
    result["cash_weight"] = 1.0 - MARGIN_BUFFER_RATE * result["target_im_units"]
    result["cash_contribution_ret"] = result["cash_weight"] * CASH_DAILY_RETURN
    result["strategy_ret"] = result["futures_net_ret"] + result["cash_contribution_ret"]
    result["nav"] = (1.0 + result["strategy_ret"]).cumprod()

    flat = result["target_im_units"].eq(0.0)
    if result.loc[flat, "futures_gross_ret"].abs().max() > 1e-15:
        raise RuntimeError("Flat dates contain IM gross return")
    if result.loc[flat, "roll_cost_rate"].abs().max() > 1e-15:
        raise RuntimeError("Flat dates contain IM roll cost")
    if (result.loc[flat, "cash_weight"] - 1.0).abs().max() > 1e-15:
        raise RuntimeError("Flat dates are not 100% cash")

    events = result.loc[
        result["turnover"].gt(0) | result["roll_event"],
        [
            "date", "contract", "target_im_units", "turnover", "roll_event",
            "trade_cost_rate", "roll_cost_rate", "roll_from", "roll_to",
        ],
    ].copy()
    return result, events


def metric_values(sample: pd.DataFrame) -> dict[str, float]:
    returns = sample["strategy_ret"].astype(float)
    nav = (1.0 + returns).cumprod()
    ann_return = float(nav.iloc[-1] ** (ANNUALIZATION_DAYS / len(sample)) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS))
    return {
        "rows": float(len(sample)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": float((nav / nav.cummax() - 1.0).min()),
        "sharpe_repo": float(ann_return / ann_vol) if ann_vol > 1e-12 else 0.0,
        "final_nav": float(nav.iloc[-1]),
        "avg_im_units": float(sample["target_im_units"].mean()),
        "avg_cash_weight": float(sample["cash_weight"].mean()),
        "cost_total": float(sample["futures_cost_rate"].sum()),
    }


def build_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    end = pd.Timestamp(daily["date"].max())
    rows: list[dict[str, object]] = []
    for window, offset in WINDOWS:
        requested_start = None if offset is None else end - offset
        sample = daily if requested_start is None else daily.loc[daily["date"] >= requested_start]
        actual_start = pd.Timestamp(sample["date"].min())
        available = bool(
            requested_start is None or actual_start <= requested_start + pd.Timedelta(days=7)
        )
        values = metric_values(sample) if available else {
            key: np.nan
            for key in (
                "rows", "ann_return", "ann_vol", "max_dd", "sharpe_repo", "final_nav",
                "avg_im_units", "avg_cash_weight", "cost_total",
            )
        }
        rows.append(
            {
                "strategy": "im_momentum_gated_roll",
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
    return pd.DataFrame(rows)


def build_annual(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, sample in daily.groupby(daily["date"].dt.year):
        rows.append(
            {
                "year": int(year),
                "start": sample["date"].min().date().isoformat(),
                "end": sample["date"].max().date().isoformat(),
                **metric_values(sample),
            }
        )
    return pd.DataFrame(rows)


def withdrawn_parity(daily: pd.DataFrame) -> dict[str, object]:
    path = WITHDRAWN_OUTPUT / "daily_curves.csv.gz"
    if not path.exists():
        return {"available": False, "reason": f"Missing withdrawn daily file: {path}"}
    old = pd.read_csv(path, parse_dates=["date"])
    old = old.loc[old["candidate"].eq("roll0_mom100"), ["date", "strategy_ret"]].rename(
        columns={"strategy_ret": "withdrawn_endpoint_ret"}
    )
    joined = daily[["date", "strategy_ret"]].merge(old, on="date", validate="one_to_one")
    error = float((joined["strategy_ret"] - joined["withdrawn_endpoint_ret"]).abs().max())
    if error > 1e-12:
        raise RuntimeError(f"Corrected path does not match prior pure-momentum endpoint: {error}")
    return {"available": True, "rows": int(len(joined)), "max_abs_daily_return_error": error}


def pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{100.0 * float(value):.2f}%"


def write_record(
    output_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    manifest: dict[str, object],
) -> None:
    by_window = metrics.set_index("window")
    values = []
    for window, _ in WINDOWS:
        row = by_window.loc[window]
        values.append(f"{pct(row['ann_return'])} / {pct(row['max_dd'])}" if row["available"] else "N/A")
    table = (
        "|策略|全样本|近10年|近5年|近3年|近1年|\n"
        "|---|---:|---:|---:|---:|---:|\n"
        f"|动量门控滚IM|{'|'.join(values)}|"
    )
    record = f"""# IM 动量门控滚动 v1

状态：研究完成；未批准实盘  
数据截止：{manifest['sample']['end']}

## 1. Scope

- 目标：动量只负责发出 IM 持仓目标；有仓才持有和滚动，空仓全部转现金。
- 市场：中证1000股指期货 IM。
- 结果性质：真实官方数据回测观察，不是推断，也不是当前交易信号。

## 2. Code and Data Provenance

- IM：中金所官方逐月历史包，官方结算价；信号：中证指数官方 `000852` 价格指数。
- 正式样本：{manifest['sample']['start']} 至 {manifest['sample']['end']}，共 {manifest['sample']['rows']} 个共同交易日。
- 指数为价格指数、不复权；时区 Asia/Shanghai。

## 3. Execution Details

- 参数：`MA35 / Mom18 / W2.5`，50% Abs20 OFF + 50% `Abs20 > 0`。
- T日收盘确认，T+1共同交易日使用 0/0.5/1 目标仓位。
- 目标为0时不持有、不滚动；目标大于0时才按实际仓位持有和换月。

## 4. Comparison Setup

- 本版只有一条正式策略，不再进行“裸滚与动量策略配比”扫描。
- 与上一版误解释报告中的纯动量端点逐日复核误差：{manifest['withdrawn_endpoint_parity'].get('max_abs_daily_return_error', float('nan')):.3e}。

## 5. Key Results

每格为年化收益 / 最大回撤。

{table}

- 样本平均 IM 目标仓位 {daily['target_im_units'].mean():.3f}；空仓日占 {daily['target_im_units'].eq(0).mean():.2%}。
- 10年和5年为N/A，因为IM从2022-07-22才上市。

## 6. Trading Frictions and Constraints

- 单边成本1bp；仓位变化按实际变化计费，持仓换月按两边2bp乘以目标仓位计费。
- 每1倍IM占用30%保证金及缓冲；其余现金按净年化3%的固定情景计息。
- 未加入盘口滑点、冲击成本、保证金动态上调、最终结算不可成交风险或流动性容量限制。

## 7. Integrity Checks

- T信号到T+1仓位，无未来函数；官方指数和IM按共同交易日一对一合并。
- 所有IM日期均有完成暖机的Score和Abs20；目标仓位只出现0、0.5、1。
- 空仓日IM毛收益、换月成本均为0，现金权重为100%；检查通过。
- Put、Call、网格均未进入逐日收益文件。

## 8. Risks and Caveats

- IM可执行样本仅约4年，未覆盖完整10年或5年周期。
- 现金年化3%为资金管理假设，不代表历史可实现产品收益。
- 本轮没有改变动量参数，也没有生成或批准实时交易指令。

## 9. Backup and Rollback

- 上一版误解释文件备份：`{manifest['backup_path']}`。
- 回滚方式：恢复该备份；正确版本独立保存在本目录，不覆盖旧输出。

## Decision

- `corrected_interpretation_retain_single_gated_roll_path`：撤回组合权重解释，只保留动量门控滚IM路径。
"""
    (output_dir / "record.md").write_text(record, encoding="utf-8")


def run(upstream_dir: Path, signal_path: Path, output_dir: Path, backup_path: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Formal output already exists: {output_dir}")
    spec_hash = verify_spec()
    source, upstream_manifest = load_inputs(upstream_dir, signal_path)
    daily, events = simulate(source)
    metrics = build_metrics(daily)
    annual = build_annual(daily)
    parity = withdrawn_parity(daily)

    output_dir.mkdir(parents=True, exist_ok=False)
    daily.to_csv(output_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    events.to_csv(output_dir / "events_and_rolls.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(output_dir / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_metrics.csv", index=False, encoding="utf-8-sig")

    command = (
        f"{Path(sys.executable).name} {Path(__file__).name} "
        f"--upstream-dir \"{upstream_dir}\" --signal-file \"{signal_path}\" "
        f"--output-dir \"{output_dir}\""
    )
    manifest: dict[str, object] = {
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
        "data": {
            "upstream_dir": str(upstream_dir),
            "upstream_daily_sha256": sha256_file(upstream_dir / "daily_nav.csv"),
            "upstream_manifest_sha256": sha256_file(upstream_dir / "data_manifest.json"),
            "upstream_common_sample": upstream_manifest["common_sample"],
            "signal_file": str(signal_path),
            "signal_sha256": sha256_file(signal_path),
            "im_source": "CFFEX official monthly archives",
            "signal_source": "CSIndex official 000852 price index",
        },
        "signal": {
            "parameters": "MA35 / Mom18 / W2.5",
            "abs20": "50% OFF + 50% static > 0",
            "target_values": [0.0, 0.5, 1.0],
            "timing": "T close signal -> T+1 common trading day target",
        },
        "cost_and_cash": {
            "one_way_cost": ONE_WAY_COST,
            "roll_cost": "2bp * target_im_units on roll dates only when target > 0",
            "margin_buffer_per_1x": MARGIN_BUFFER_RATE,
            "cash_assumed_net_annual_return": CASH_ANNUAL_RETURN,
            "flat_state": "0 IM units, 100% cash",
            "put": "excluded",
            "call": "excluded",
            "grid": "excluded",
        },
        "integrity": {
            "flat_days": int(daily["target_im_units"].eq(0).sum()),
            "half_position_days": int(daily["target_im_units"].eq(0.5).sum()),
            "full_position_days": int(daily["target_im_units"].eq(1).sum()),
            "position_change_days": int(daily["turnover"].gt(0).sum()),
            "roll_events_total": int(daily["roll_event"].sum()),
            "roll_events_while_held": int((daily["roll_event"] & daily["target_im_units"].gt(0)).sum()),
            "forbidden_overlay_columns": 0,
        },
        "withdrawn_endpoint_parity": parity,
        "backup_path": str(backup_path),
        "decision": "corrected_interpretation_retain_single_gated_roll_path",
    }
    (output_dir / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(output_dir, daily, metrics, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Momentum-gated IM rolling strategy v1")
    parser.add_argument("--upstream-dir", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--signal-file", type=Path, default=DEFAULT_SIGNAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--backup-path",
        type=Path,
        default=ROOT / ".codex_backups" / "20260822_225143",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        upstream_dir=args.upstream_dir.resolve(),
        signal_path=args.signal_file.resolve(),
        output_dir=args.output_dir.resolve(),
        backup_path=args.backup_path.resolve(),
    )
