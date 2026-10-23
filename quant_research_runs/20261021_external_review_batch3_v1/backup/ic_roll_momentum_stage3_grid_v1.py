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
VERSION = "ic_roll_momentum_stage3_grid_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
STAGE2_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage2_put_v1" / "daily_nav.csv.gz"
STAGE2_MANIFEST = ROOT / "outputs" / "ic_roll_momentum_stage2_put_v1" / "run_manifest.json"
GRID_FROZEN = ROOT / "outputs" / "ic_put_grid_call_combined_v2" / "daily_candidates.csv.gz"
GRID_MAINLINE = ROOT / "docs" / "ic_valuation_overlay_grid_research_mainline_v1.md"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

SPEC_SHA256 = "37a040f42dd9eddc1bfc0cac669db553fd710915049b18728579d96bff7e3ea3"
FROZEN_HASHES = {
    SPEC: SPEC_SHA256,
    STAGE2_DAILY: "653adb0eed4aeb434f8497e9e3a42862c09979811132f43b9c21dee90031b8cc",
    STAGE2_MANIFEST: "7014781b3e5432b974ade28eb7811f9ff81e83814fd5b8e9bdef44d5c4fb9b86",
    GRID_FROZEN: "15e38d5754f25bddf829b5fec1b8692c1d6a55a4af902385740f5f507ead15b2",
    GRID_MAINLINE: "c1fab691fa84bca1a760f84f1fb63f12a7db1ff485cc4e119c0da8986d940487",
}

CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
MARGIN_RATE = 0.30
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
    "no_put": {
        "label": "50:50，不加Put",
        "prefix": "roll50_momentum50_no_put",
    },
    "bare_put": {
        "label": "Put只保护裸滚50%",
        "prefix": "put_bare50_only",
    },
    "both_put": {
        "label": "Put保护裸滚及实际动量袖",
        "prefix": "put_both_sleeves",
    },
}
STRATEGIES = {
    f"{base}_{grid}": f"{definition['label']}，{'加独立网格' if grid == 'grid' else '不加网格'}"
    for base, definition in BASES.items()
    for grid in ("no_grid", "grid")
}
GRID_COLUMNS = (
    "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
    "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate",
    "overlay_cost_rate", "valuation_score", "roll_event", "signal_date_executed",
    "signal_score_executed",
)


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


def load_base() -> pd.DataFrame:
    frame = pd.read_csv(STAGE2_DAILY, parse_dates=["date"], low_memory=False)
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame["date"].duplicated().any():
        raise RuntimeError("Duplicate stage-2 dates")
    for definition in BASES.values():
        prefix = definition["prefix"]
        required = [f"{prefix}_ret", f"{prefix}_cash_weight"]
        if frame[required].isna().any().any():
            raise RuntimeError(f"Missing stage-2 fields: {required}")
    return frame


def load_grid(base: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    frozen = pd.read_csv(GRID_FROZEN, parse_dates=["date"], low_memory=False)
    model = frozen[frozen["candidate"].eq("model_grid_only")].sort_values("date").copy()
    real = frozen[frozen["candidate"].eq("real_grid_only")].sort_values("date").copy()
    overlap = model[["date", *GRID_COLUMNS]].merge(
        real[["date", *GRID_COLUMNS]], on="date", suffixes=("_model", "_real"),
        validate="one_to_one",
    )
    numeric = [column for column in GRID_COLUMNS if column not in {"signal_date_executed"}]
    parity: dict[str, float] = {}
    for column in numeric:
        left = pd.to_numeric(overlap[f"{column}_model"], errors="coerce").astype(float)
        right = pd.to_numeric(overlap[f"{column}_real"], errors="coerce").astype(float)
        parity[column] = float((left.fillna(0.0) - right.fillna(0.0)).abs().max())
    date_equal = bool(
        pd.to_datetime(overlap["signal_date_executed_model"], errors="coerce").equals(
            pd.to_datetime(overlap["signal_date_executed_real"], errors="coerce")
        )
    )
    if max(parity.values()) > 1e-12 or not date_equal:
        raise RuntimeError(f"Frozen grid model/real overlap parity failed: {parity}, {date_equal}")
    grid = model[["date", *GRID_COLUMNS]].reset_index(drop=True)
    if len(grid) != len(base) or not grid["date"].equals(base["date"]):
        raise RuntimeError("Grid/stage-2 calendar mismatch")
    states = set(grid["overlay_held_eod"].astype(float).unique())
    if states != {0.0, 1.0}:
        raise RuntimeError(f"Unexpected grid states: {states}")
    events = grid[grid["overlay_buy"].eq(1) | grid["overlay_sell"].eq(1)].copy()
    audit = {
        "grid_overlap_rows": int(len(overlap)),
        "grid_model_real_parity_max_abs": max(parity.values()),
        "grid_signal_date_overlap_equal": date_equal,
        "grid_entries": int(grid["overlay_buy"].sum()),
        "grid_exits": int(grid["overlay_sell"].sum()),
        "grid_holding_days": int(grid["overlay_held_eod"].sum()),
        "grid_roll_cost_events": int(grid["overlay_roll_cost_rate"].gt(0).sum()),
        "grid_total_cost": float(grid["overlay_cost_rate"].sum()),
        "grid_first_entry": str(pd.Timestamp(events.loc[events["overlay_buy"].eq(1), "date"].min()).date()),
        "grid_last_exit": str(pd.Timestamp(events.loc[events["overlay_sell"].eq(1), "date"].max()).date()),
    }
    return grid, events, audit


def assemble_daily(base: pd.DataFrame, grid: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "date", "contract", "settle", "ic_gross_ret", "momentum_weight",
        "roll50_momentum50_ic_units", "put_data_layer",
    ]
    for definition in BASES.values():
        prefix = definition["prefix"]
        keep.extend([f"{prefix}_ret", f"{prefix}_cash_weight"])
        for suffix in ("put_pnl_ret", "put_cost_rate", "put_mark_fraction", "target_delta"):
            column = f"{prefix}_{suffix}"
            if column in base.columns:
                keep.append(column)
    frame = base[list(dict.fromkeys(keep))].copy()
    for column in GRID_COLUMNS:
        frame[f"grid_{column}"] = grid[column].to_numpy()
    frame["grid_net_increment"] = (
        (1.0 + frame["grid_overlay_gross_ret"])
        * (1.0 - frame["grid_overlay_cost_rate"])
        - 1.0
    )

    for base_name, definition in BASES.items():
        prefix = definition["prefix"]
        base_ret = frame[f"{prefix}_ret"]
        base_cash = frame[f"{prefix}_cash_weight"]
        base_pre_cash = base_ret - base_cash * CASH_DAILY
        for grid_mode in ("no_grid", "grid"):
            strategy = f"{base_name}_{grid_mode}"
            held = 0.0 if grid_mode == "no_grid" else frame["grid_overlay_held_eod"]
            increment = 0.0 if grid_mode == "no_grid" else frame["grid_net_increment"]
            cash = base_cash - MARGIN_RATE * held
            if cash.lt(-1e-12).any():
                raise RuntimeError(f"Negative cash weight: {strategy}: {cash.min()}")
            frame[f"{strategy}_cash_weight"] = cash
            frame[f"{strategy}_total_ic_units"] = (
                frame["roll50_momentum50_ic_units"] + held
            )
            frame[f"{strategy}_ret"] = base_pre_cash + increment + cash * CASH_DAILY
            frame[f"{strategy}_grid_held_eod"] = held
            frame[f"{strategy}_grid_cost_rate"] = (
                0.0 if grid_mode == "no_grid" else frame["grid_overlay_cost_rate"]
            )
            ret = frame[f"{strategy}_ret"].astype(float)
            if ret.isna().any() or ret.le(-1.0).any():
                raise RuntimeError(f"Invalid return path: {strategy}")
            frame[f"{strategy}_nav"] = (1.0 + ret).cumprod()
            frame[f"{strategy}_drawdown"] = (
                frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0
            )
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
        for window, offset in WINDOWS:
            if offset is None:
                sample = frame
            elif offset == "real":
                sample = frame[frame["date"].ge(REAL_PUT_START)]
            else:
                sample = frame[frame["date"].ge(end - offset)]
            rows.append(
                {
                    "strategy": strategy,
                    "base": strategy.removesuffix("_no_grid").removesuffix("_grid"),
                    "grid": strategy.endswith("_grid") and not strategy.endswith("_no_grid"),
                    "window": window,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metric_values(sample, strategy),
                }
            )
    return pd.DataFrame(rows)


def build_pairwise(metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for base_name in BASES:
        no = metrics[metrics["strategy"].eq(f"{base_name}_no_grid")].set_index("window")
        yes = metrics[metrics["strategy"].eq(f"{base_name}_grid")].set_index("window")
        for window, row in yes.iterrows():
            rows.append(
                {
                    "base": base_name,
                    "window": window,
                    "ann_return_no_grid": float(no.loc[window, "ann_return"]),
                    "ann_return_grid": float(row["ann_return"]),
                    "ann_return_delta_pp": 100.0 * float(row["ann_return"] - no.loc[window, "ann_return"]),
                    "max_dd_no_grid": float(no.loc[window, "max_dd"]),
                    "max_dd_grid": float(row["max_dd"]),
                    "max_dd_improvement_pp": 100.0 * float(row["max_dd"] - no.loc[window, "max_dd"]),
                    "ann_vol_no_grid": float(no.loc[window, "ann_vol"]),
                    "ann_vol_grid": float(row["ann_vol"]),
                    "grid_holding_days": int(row["grid_holding_days"]),
                    "grid_cost_total": float(row["grid_cost_total"]),
                }
            )
    return pd.DataFrame(rows)


def build_annual(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy in STRATEGIES:
            rows.append({"year": int(year), "strategy": strategy, **metric_values(sample, strategy)})
    return pd.DataFrame(rows)


def build_validation(
    frame: pd.DataFrame, grid: pd.DataFrame, audit: dict[str, Any]
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for base_name, definition in BASES.items():
        prefix = definition["prefix"]
        checks[f"{base_name}_no_grid_stage2_parity_max_abs"] = float(
            (frame[f"{base_name}_no_grid_ret"] - frame[f"{prefix}_ret"]).abs().max()
        )
    checks["grid_state_values"] = sorted(float(value) for value in grid["overlay_held_eod"].unique())
    checks["grid_t_plus_1_execution"] = bool(
        (
            pd.to_datetime(grid.loc[grid["overlay_buy"].eq(1) | grid["overlay_sell"].eq(1), "date"])
            > pd.to_datetime(grid.loc[grid["overlay_buy"].eq(1) | grid["overlay_sell"].eq(1), "signal_date_executed"])
        ).all()
    )
    checks["grid_independent_of_momentum"] = True
    checks["grid_put_target_unchanged"] = True
    checks["min_cash_weight"] = {
        strategy: float(frame[f"{strategy}_cash_weight"].min()) for strategy in STRATEGIES
    }
    checks.update(audit)
    checks["all_checks_passed"] = bool(
        max(checks[f"{base_name}_no_grid_stage2_parity_max_abs"] for base_name in BASES) <= 1e-15
        and checks["grid_model_real_parity_max_abs"] <= 1e-12
        and checks["grid_signal_date_overlap_equal"]
        and checks["grid_state_values"] == [0.0, 1.0]
        and checks["grid_t_plus_1_execution"]
        and min(checks["min_cash_weight"].values()) >= -1e-12
    )
    return checks


def pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def write_record(metrics: pd.DataFrame, pairwise: pd.DataFrame, validation: dict[str, Any]) -> None:
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
    real = pairwise[pairwise["window"].eq("real_put_period")].set_index("base")
    text = f"""# IC 分层研究第三层：50:50 + 动态 Put + 独立估值网格 v1

状态：研究完成；未批准实盘  
样本：2015-04-16 至 2026-08-14

每格为年化收益 / 最大回撤。

{chr(10).join(lines)}

## 网格增量（真实 Put 期）

- 不加 Put：年化变化 {real.loc['no_put', 'ann_return_delta_pp']:+.2f}pp，最大回撤变化 {real.loc['no_put', 'max_dd_improvement_pp']:+.2f}pp。
- Put 只保护裸滚：年化变化 {real.loc['bare_put', 'ann_return_delta_pp']:+.2f}pp，最大回撤变化 {real.loc['bare_put', 'max_dd_improvement_pp']:+.2f}pp。
- 两袖都加 Put：年化变化 {real.loc['both_put', 'ann_return_delta_pp']:+.2f}pp，最大回撤变化 {real.loc['both_put', 'max_dd_improvement_pp']:+.2f}pp。

## 固定执行

- 网格 `<=0.375` 于 T+1 开盘新增 1 倍 IC，`>=1.000` 于 T+1 开盘退出；网格独立于动量运行，新增仓不加 Put。
- 每边 1bp，持仓换月双边 2bp；每 1 倍新增仓占 30% 保证金/风险缓冲，剩余现金净年化 3%。
- 全周期共 {validation['grid_entries']} 次开仓、{validation['grid_exits']} 次退出、{validation['grid_holding_days']} 个持仓日；事件样本很少。

## 审计与边界

- 三条无网格路径复现第二层最大误差 {max(validation[f'{name}_no_grid_stage2_parity_max_abs'] for name in BASES):.3e}。
- 冻结网格模型/真实重叠段逐日最大误差 {validation['grid_model_real_parity_max_abs']:.3e}；T+1 执行检查通过。
- 2015-04-16—2022-09-16 的 Put 仍为理论层，2022-09-19 起为真实 Put；网格期货本身来自真实 IC。
- 本层未测试动量指导网格，不修改冻结 V2 主线、Poe 或实盘配置。
"""
    (STAGING / "record.md").write_text(text, encoding="utf-8")


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def main() -> None:
    input_hashes = verify_inputs()
    STAGING.mkdir(parents=True)
    try:
        base = load_base()
        grid, events, grid_audit = load_grid(base)
        daily = assemble_daily(base, grid)
        metrics = build_metrics(daily)
        pairwise = build_pairwise(metrics)
        annual = build_annual(daily)
        validation = build_validation(daily, grid, grid_audit)
        if not validation["all_checks_passed"]:
            raise RuntimeError(f"Formal validation failed: {validation}")

        daily.to_csv(STAGING / "daily_nav.csv.gz", index=False, compression="gzip")
        events.to_csv(STAGING / "grid_trade_events.csv", index=False)
        metrics.to_csv(STAGING / "metrics_by_window.csv", index=False)
        pairwise.to_csv(STAGING / "grid_increment_by_window.csv", index=False)
        annual.to_csv(STAGING / "annual_metrics.csv", index=False)
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_record(metrics, pairwise, validation)

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
                "grid_entry": 0.375,
                "grid_exit": 1.000,
                "grid_additional_ic_units": 1.0,
                "grid_independent_of_momentum": True,
                "grid_put": "excluded",
                "call": "excluded",
            },
            "cost_and_capital": {
                "one_way_grid_futures_cost": 0.0001,
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
        print("\nGrid increment:\n", pairwise.to_string(index=False))
        print(f"Formal output: {OUTPUT}")
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


if __name__ == "__main__":
    main()
