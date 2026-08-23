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


ROOT = Path(__file__).resolve().parent
VERSION = "ic_roll_momentum_stage4_grid_guidance_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
STAGE3_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage3_grid_v1" / "daily_nav.csv.gz"
STAGE3_MANIFEST = ROOT / "outputs" / "ic_roll_momentum_stage3_grid_v1" / "run_manifest.json"
GRID_FROZEN = ROOT / "outputs" / "ic_put_grid_call_combined_v2" / "daily_candidates.csv.gz"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

SPEC_SHA256 = "cc87249590c11d7cac8cc8675794adceae25ff896a358f536b1986375d3dd63d"
FROZEN_HASHES = {
    SPEC: SPEC_SHA256,
    STAGE3_DAILY: "479e7c6cd1cc0fc81a12db3de7256ff1b716448e743c051a05a237678f535ddf",
    STAGE3_MANIFEST: "65dd81d961153627a614a377794acd7a84f4562757bbaa369ed731ef41501050",
    GRID_FROZEN: "15e38d5754f25bddf829b5fec1b8692c1d6a55a4af902385740f5f507ead15b2",
}

LOW = 0.375
HIGH = 1.000
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
REAL_PUT_START = pd.Timestamp("2022-09-19")
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
    ("real_put_period", "real"),
)
BASES = {
    "no_put": "50:50，不加Put",
    "bare_put": "Put只保护裸滚50%",
    "both_put": "Put保护裸滚及实际动量袖",
}
MODES = {
    "no_grid": "不加网格",
    "independent": "独立网格",
    "guided": "动量指导网格",
}
STRATEGIES = {
    f"{base}_{mode}": f"{base_label}，{mode_label}"
    for base, base_label in BASES.items()
    for mode, mode_label in MODES.items()
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
    base = pd.read_csv(STAGE3_DAILY, parse_dates=["date"], low_memory=False)
    base = base.sort_values("date").reset_index(drop=True)
    frozen = pd.read_csv(GRID_FROZEN, parse_dates=["date"], low_memory=False)
    market = frozen[frozen["candidate"].eq("model_grid_only")][
        [
            "date", "contract", "open", "settle", "pre_settle",
            "valuation_score", "roll_event", "overlay_held_before", "overlay_held_eod",
            "overlay_buy", "overlay_sell", "overlay_gross_ret",
            "overlay_trade_cost_rate", "overlay_roll_cost_rate", "overlay_cost_rate",
            "signal_date_executed", "signal_score_executed",
        ]
    ].sort_values("date").reset_index(drop=True)
    joined = market.merge(
        base[["date", "momentum_weight"]], on="date", validate="one_to_one"
    )
    if len(joined) != len(base) or not joined["date"].equals(base["date"]):
        raise RuntimeError("Market/stage-3 calendar mismatch")
    if joined[["open", "settle", "pre_settle", "valuation_score", "momentum_weight"]].isna().any().any():
        raise RuntimeError("Missing guided-grid input")
    if set(joined["momentum_weight"].astype(float).unique()) != {0.0, 0.5, 1.0}:
        raise RuntimeError("Unexpected momentum weights")
    return base, joined


def run_guided_grid(market: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    state = False
    pending: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    dates = list(pd.DatetimeIndex(market["date"]))

    for index, row in enumerate(market.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        allowed = float(row.momentum_weight) > 0.0
        held_before = state
        buy = False
        sell = False
        action_reason = ""
        signal_date: pd.Timestamp | pd.NaT = pd.NaT
        signal_score = np.nan

        if pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            signal_date = pd.Timestamp(pending["signal_date"])
            signal_score = float(pending["signal_score"])
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError("Duplicate guided-grid buy")
                if allowed:
                    state = True
                    buy = True
                    action_reason = "valuation_buy_momentum_pass"
                else:
                    events.append(
                        {
                            "action": "blocked_buy", "reason": "valuation_buy_momentum_off",
                            "signal_date": signal_date, "signal_score": signal_score,
                            "execution_date": day, "momentum_weight": float(row.momentum_weight),
                            "contract": row.contract, "execution_open": float(row.open),
                        }
                    )
            else:
                if not state:
                    raise RuntimeError("Guided-grid valuation sell while flat")
                state = False
                sell = True
                action_reason = "valuation_sell"
            pending = None

        if state and not allowed:
            if buy:
                raise RuntimeError("Guided grid bought while momentum was off")
            state = False
            sell = True
            action_reason = "momentum_forced_exit"
            signal_date = dates[index - 1] if index > 0 else day
            signal_score = float(row.valuation_score)

        held_eod = state
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

        if buy or sell:
            events.append(
                {
                    "action": "buy" if buy else "sell", "reason": action_reason,
                    "signal_date": signal_date, "signal_score": signal_score,
                    "execution_date": day, "momentum_weight": float(row.momentum_weight),
                    "contract": row.contract, "execution_open": float(row.open),
                }
            )

        rows.append(
            {
                "date": day,
                "momentum_allowed": int(allowed),
                "overlay_held_before": int(held_before),
                "overlay_held_eod": int(held_eod),
                "overlay_buy": int(buy),
                "overlay_sell": int(sell),
                "overlay_gross_ret": gross,
                "overlay_trade_cost_rate": trade_cost,
                "overlay_roll_cost_rate": roll_cost,
                "overlay_cost_rate": trade_cost + roll_cost,
                "valuation_score": float(row.valuation_score),
                "roll_event": bool(row.roll_event),
                "signal_date_executed": signal_date,
                "signal_score_executed": signal_score,
            }
        )

        if pending is None:
            score = float(row.valuation_score)
            action = "buy" if (not state and score <= LOW + 1e-12) else None
            if state and score >= HIGH - 1e-12:
                action = "sell"
            if action is not None and index + 1 < len(dates):
                pending = {
                    "action": action,
                    "signal_date": day,
                    "signal_score": score,
                    "execution_date": dates[index + 1],
                }

    daily = pd.DataFrame(rows)
    event_frame = pd.DataFrame(events)
    if event_frame.empty:
        event_frame = pd.DataFrame(
            columns=[
                "action", "reason", "signal_date", "signal_score", "execution_date",
                "momentum_weight", "contract", "execution_open",
            ]
        )
    flat_violation = int(
        (daily["overlay_held_eod"].eq(1) & daily["momentum_allowed"].eq(0)).sum()
    )
    if flat_violation:
        raise RuntimeError(f"Guided grid held while momentum was off: {flat_violation}")
    audit = {
        "guided_entries": int(event_frame["action"].eq("buy").sum()),
        "guided_exits": int(event_frame["action"].eq("sell").sum()),
        "guided_blocked_buys": int(event_frame["action"].eq("blocked_buy").sum()),
        "guided_momentum_forced_exits": int(event_frame["reason"].eq("momentum_forced_exit").sum()),
        "guided_holding_days": int(daily["overlay_held_eod"].sum()),
        "guided_roll_cost_events": int(daily["overlay_roll_cost_rate"].gt(0).sum()),
        "guided_cost_total": float(daily["overlay_cost_rate"].sum()),
        "guided_flat_momentum_holding_violations": flat_violation,
        "guided_ending_state": int(state),
    }
    return daily, event_frame, audit


def independent_events(market: pd.DataFrame) -> pd.DataFrame:
    events = market[market["overlay_buy"].eq(1) | market["overlay_sell"].eq(1)][
        [
            "date", "contract", "open", "overlay_buy", "overlay_sell",
            "signal_date_executed", "signal_score_executed", "momentum_weight",
        ]
    ].copy()
    events["action"] = np.where(events["overlay_buy"].eq(1), "buy", "sell")
    events["reason"] = "valuation"
    return events.rename(
        columns={
            "date": "execution_date", "open": "execution_open",
            "signal_score_executed": "signal_score", "signal_date_executed": "signal_date",
        }
    )[
        [
            "action", "reason", "signal_date", "signal_score", "execution_date",
            "momentum_weight", "contract", "execution_open",
        ]
    ]


def add_path(
    frame: pd.DataFrame,
    base_name: str,
    mode: str,
    guided: pd.DataFrame,
) -> None:
    strategy = f"{base_name}_{mode}"
    source_no = f"{base_name}_no_grid"
    source_grid = f"{base_name}_grid"
    if mode == "no_grid":
        for suffix in ("ret", "cash_weight", "total_ic_units"):
            frame[f"{strategy}_{suffix}"] = frame[f"{source_no}_{suffix}"]
        frame[f"{strategy}_grid_held_eod"] = 0.0
        frame[f"{strategy}_grid_cost_rate"] = 0.0
    elif mode == "independent":
        for suffix in ("ret", "cash_weight", "total_ic_units"):
            frame[f"{strategy}_{suffix}"] = frame[f"{source_grid}_{suffix}"]
        frame[f"{strategy}_grid_held_eod"] = frame["grid_overlay_held_eod"]
        frame[f"{strategy}_grid_cost_rate"] = frame["grid_overlay_cost_rate"]
    elif mode == "guided":
        held = guided["overlay_held_eod"].astype(float)
        grid_net = (
            (1.0 + guided["overlay_gross_ret"])
            * (1.0 - guided["overlay_cost_rate"])
            - 1.0
        )
        base_ret = frame[f"{source_no}_ret"]
        base_cash = frame[f"{source_no}_cash_weight"]
        base_pre_cash = base_ret - base_cash * CASH_DAILY
        cash = base_cash - MARGIN_RATE * held
        if cash.lt(-1e-12).any():
            raise RuntimeError(f"Negative guided-grid cash: {base_name}: {cash.min()}")
        frame[f"{strategy}_cash_weight"] = cash
        frame[f"{strategy}_total_ic_units"] = frame["roll50_momentum50_ic_units"] + held
        frame[f"{strategy}_ret"] = base_pre_cash + grid_net + cash * CASH_DAILY
        frame[f"{strategy}_grid_held_eod"] = held
        frame[f"{strategy}_grid_cost_rate"] = guided["overlay_cost_rate"]
    else:
        raise ValueError(mode)

    ret = frame[f"{strategy}_ret"].astype(float)
    if ret.isna().any() or ret.le(-1.0).any():
        raise RuntimeError(f"Invalid return path: {strategy}")
    frame[f"{strategy}_nav"] = (1.0 + ret).cumprod()
    frame[f"{strategy}_drawdown"] = (
        frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0
    )


def assemble_daily(base: pd.DataFrame, market: pd.DataFrame, guided: pd.DataFrame) -> pd.DataFrame:
    frame = base.copy()
    for base_name in BASES:
        frame[f"stage3_{base_name}_no_grid_ret"] = frame[f"{base_name}_no_grid_ret"]
        frame[f"stage3_{base_name}_grid_ret"] = frame[f"{base_name}_grid_ret"]
    for column in (
        "momentum_allowed", "overlay_held_before", "overlay_held_eod", "overlay_buy",
        "overlay_sell", "overlay_gross_ret", "overlay_trade_cost_rate",
        "overlay_roll_cost_rate", "overlay_cost_rate", "signal_date_executed",
        "signal_score_executed",
    ):
        frame[f"guided_{column}"] = guided[column].to_numpy()
    for base_name in BASES:
        for mode in MODES:
            add_path(frame, base_name, mode, guided)
    return frame


def metric_values(sample: pd.DataFrame, strategy: str) -> dict[str, float]:
    ret = sample[f"{strategy}_ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    ann_return = float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0)
    ann_vol = float(ret.std(ddof=0) * math.sqrt(252.0))
    max_dd = float(dd.min())
    return {
        "rows": int(len(sample)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": max_dd,
        "calmar": ann_return / abs(max_dd) if max_dd < -1e-12 else np.nan,
        "final_nav": float(nav.iloc[-1]),
        "avg_total_ic_units": float(sample[f"{strategy}_total_ic_units"].mean()),
        "max_total_ic_units": float(sample[f"{strategy}_total_ic_units"].max()),
        "grid_holding_days": int(sample[f"{strategy}_grid_held_eod"].sum()),
        "grid_cost_total": float(sample[f"{strategy}_grid_cost_rate"].sum()),
        "min_cash_weight": float(sample[f"{strategy}_cash_weight"].min()),
    }


def build_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    end = frame["date"].max()
    rows: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        base_name, mode = strategy.rsplit("_", 1)
        if strategy.endswith("_no_grid"):
            base_name = strategy.removesuffix("_no_grid")
            mode = "no_grid"
        for window, offset in WINDOWS:
            if offset is None:
                sample = frame
            elif offset == "real":
                sample = frame[frame["date"].ge(REAL_PUT_START)]
            else:
                sample = frame[frame["date"].ge(end - offset)]
            rows.append(
                {
                    "strategy": strategy, "base": base_name, "mode": mode,
                    "window": window, "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metric_values(sample, strategy),
                }
            )
    return pd.DataFrame(rows)


def build_comparisons(metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for base_name in BASES:
        blocks = {
            mode: metrics[metrics["strategy"].eq(f"{base_name}_{mode}")].set_index("window")
            for mode in MODES
        }
        for window in blocks["guided"].index:
            row = {"base": base_name, "window": window}
            for mode, block in blocks.items():
                row[f"{mode}_ann_return"] = float(block.loc[window, "ann_return"])
                row[f"{mode}_max_dd"] = float(block.loc[window, "max_dd"])
            row["guided_minus_independent_ann_return_pp"] = 100.0 * (
                row["guided_ann_return"] - row["independent_ann_return"]
            )
            row["guided_minus_independent_max_dd_pp"] = 100.0 * (
                row["guided_max_dd"] - row["independent_max_dd"]
            )
            row["guided_minus_no_grid_ann_return_pp"] = 100.0 * (
                row["guided_ann_return"] - row["no_grid_ann_return"]
            )
            row["guided_minus_no_grid_max_dd_pp"] = 100.0 * (
                row["guided_max_dd"] - row["no_grid_max_dd"]
            )
            rows.append(row)
    return pd.DataFrame(rows)


def build_annual(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy in STRATEGIES:
            rows.append({"year": int(year), "strategy": strategy, **metric_values(sample, strategy)})
    return pd.DataFrame(rows)


def build_validation(
    frame: pd.DataFrame, market: pd.DataFrame, guided: pd.DataFrame, audit: dict[str, Any]
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for base_name in BASES:
        checks[f"{base_name}_no_grid_stage3_parity_max_abs"] = float(
            (frame[f"{base_name}_no_grid_ret"] - frame[f"stage3_{base_name}_no_grid_ret"]).abs().max()
        )
        checks[f"{base_name}_independent_stage3_parity_max_abs"] = float(
            (frame[f"{base_name}_independent_ret"] - frame[f"stage3_{base_name}_grid_ret"]).abs().max()
        )
    checks["independent_grid_component_parity_max_abs"] = max(
        float((frame[f"grid_{column}"] - market[column]).abs().max())
        for column in (
            "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
            "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate",
            "overlay_cost_rate",
        )
    )
    checks["guided_state_values"] = sorted(float(value) for value in guided["overlay_held_eod"].unique())
    checks["guided_held_when_momentum_off"] = int(
        (guided["overlay_held_eod"].eq(1) & frame["momentum_weight"].eq(0)).sum()
    )
    checks["min_cash_weight"] = {
        strategy: float(frame[f"{strategy}_cash_weight"].min()) for strategy in STRATEGIES
    }
    checks.update(audit)
    checks["all_checks_passed"] = bool(
        max(checks[f"{base_name}_no_grid_stage3_parity_max_abs"] for base_name in BASES) <= 1e-15
        and max(checks[f"{base_name}_independent_stage3_parity_max_abs"] for base_name in BASES) <= 1e-15
        and checks["independent_grid_component_parity_max_abs"] <= 1e-15
        and checks["guided_state_values"] == [0.0, 1.0]
        and checks["guided_held_when_momentum_off"] == 0
        and checks["guided_flat_momentum_holding_violations"] == 0
        and min(checks["min_cash_weight"].values()) >= -1e-12
    )
    return checks


def pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def write_record(metrics: pd.DataFrame, comparisons: pd.DataFrame, validation: dict[str, Any]) -> None:
    windows = ("full", "10y", "5y", "3y", "1y", "real_put_period")
    lines = [
        "|路径|全周期|近10年|近5年|近3年|近1年|真实Put期|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, label in STRATEGIES.items():
        block = metrics[metrics["strategy"].eq(strategy)].set_index("window")
        cells = [
            f"{pct(float(block.loc[window, 'ann_return']))} / {pct(float(block.loc[window, 'max_dd']))}"
            for window in windows
        ]
        lines.append(f"|{label}|{'|'.join(cells)}|")
    real = comparisons[comparisons["window"].eq("real_put_period")].set_index("base")
    text = f"""# IC 分层研究第四层：独立网格 vs 动量指导网格 v1

状态：研究完成；未批准实盘  
样本：2015-04-16 至 2026-08-14

每格为年化收益 / 最大回撤。

{chr(10).join(lines)}

## 动量指导相对独立网格（真实 Put 期）

- 不加 Put：年化变化 {real.loc['no_put', 'guided_minus_independent_ann_return_pp']:+.2f}pp，最大回撤变化 {real.loc['no_put', 'guided_minus_independent_max_dd_pp']:+.2f}pp。
- Put 只保护裸滚：年化变化 {real.loc['bare_put', 'guided_minus_independent_ann_return_pp']:+.2f}pp，最大回撤变化 {real.loc['bare_put', 'guided_minus_independent_max_dd_pp']:+.2f}pp。
- 两袖都加 Put：年化变化 {real.loc['both_put', 'guided_minus_independent_ann_return_pp']:+.2f}pp，最大回撤变化 {real.loc['both_put', 'guided_minus_independent_max_dd_pp']:+.2f}pp。

## 执行与事件

- 动量指导规则：只有已落地动量权重>0才允许网格买入；权重归零时用该交易日开盘退出；0.5和1均允许完整1倍网格。
- 指导网格开仓/退出 {validation['guided_entries']} / {validation['guided_exits']} 次，阻止买入 {validation['guided_blocked_buys']} 次，动量强制退出 {validation['guided_momentum_forced_exits']} 次，持仓 {validation['guided_holding_days']} 日。
- 独立网格仍只有3个估值循环、130个持仓日；动量指导切分交易片段，但没有增加独立估值周期。

## 审计与边界

- 独立网格逐日复现第三层最大误差 {max(validation[f'{name}_independent_stage3_parity_max_abs'] for name in BASES):.3e}。
- 指导网格在动量权重0时的日终持仓违规数为 {validation['guided_held_when_momentum_off']}；现金权重全部非负。
- 期货采用官方IC开盘/结算价；每边1bp、换月双边2bp；未计盘口冲击、动态保证金上调或开盘不可成交偏差。
- 本层不修改冻结 V2 主线、Poe 或实盘配置。
"""
    (STAGING / "record.md").write_text(text, encoding="utf-8")


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def main() -> None:
    input_hashes = verify_inputs()
    STAGING.mkdir(parents=True)
    try:
        base, market = load_inputs()
        guided, guided_events, guided_audit = run_guided_grid(market)
        independent = independent_events(market)
        daily = assemble_daily(base, market, guided)
        metrics = build_metrics(daily)
        comparisons = build_comparisons(metrics)
        annual = build_annual(daily)
        validation = build_validation(daily, market, guided, guided_audit)
        if not validation["all_checks_passed"]:
            raise RuntimeError(f"Formal validation failed: {validation}")

        daily.to_csv(STAGING / "daily_nav.csv.gz", index=False, compression="gzip")
        guided_events.to_csv(STAGING / "guided_grid_events.csv", index=False)
        independent.to_csv(STAGING / "independent_grid_events.csv", index=False)
        metrics.to_csv(STAGING / "metrics_by_window.csv", index=False)
        comparisons.to_csv(STAGING / "grid_mode_comparison.csv", index=False)
        annual.to_csv(STAGING / "annual_metrics.csv", index=False)
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_record(metrics, comparisons, validation)

        files: dict[str, dict[str, Any]] = {}
        for path in sorted(STAGING.iterdir()):
            if path.name != "run_manifest.json":
                files[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        manifest = {
            "version": VERSION,
            "status": "research_only_not_live_approved",
            "created_at": datetime.now().astimezone().isoformat(),
            "git_commit": git_value("rev-parse", "HEAD"),
            "git_dirty": bool(git_value("status", "--porcelain")),
            "spec_sha256": SPEC_SHA256,
            "sample": {
                "start": str(daily["date"].min().date()),
                "end": str(daily["date"].max().date()),
                "rows": int(len(daily)),
                "real_put_start": str(REAL_PUT_START.date()),
            },
            "fixed_rules": {
                "grid_entry": LOW, "grid_exit": HIGH,
                "grid_additional_ic_units": 1.0,
                "guided_gate": "momentum_weight_gt_0",
                "guided_forced_exit": "same_execution_day_open_when_landed_weight_is_zero",
                "grid_put": "excluded", "call": "excluded",
            },
            "cost_and_capital": {
                "one_way_grid_futures_cost": ONE_WAY_COST,
                "margin_buffer_per_1x_ic": MARGIN_RATE,
                "cash_assumed_net_annual_return": 0.03,
            },
            "inputs": input_hashes,
            "files": files,
        }
        (STAGING / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        STAGING.rename(OUTPUT)
        print(metrics.to_string(index=False))
        print("\nComparisons:\n", comparisons.to_string(index=False))
        print(f"Formal output: {OUTPUT}")
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


if __name__ == "__main__":
    main()
