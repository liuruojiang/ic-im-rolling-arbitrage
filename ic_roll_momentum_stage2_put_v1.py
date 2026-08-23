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

import freeze_ic_im_system_mainlines_v2 as frozen_v2
import ic_510500_put_tiered_notional_delta_v20 as put_engine
import ic_put_four_tier_mom120_floor_scan_v3 as put_rules


ROOT = Path(__file__).resolve().parent
VERSION = "ic_roll_momentum_stage2_put_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
STAGE1_DAILY = ROOT / "outputs" / "ic_roll_momentum_stage1_v1" / "daily_nav.csv.gz"
STAGE1_MANIFEST = ROOT / "outputs" / "ic_roll_momentum_stage1_v1" / "run_manifest.json"
SOURCE_SCHEDULE = (
    ROOT / "outputs" / "ic_510500_put_mom120_delta_floor_v21" / "evaluation_schedule.csv.gz"
)
V2_DAILY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
V2_SCHEDULE = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "target_schedules.csv.gz"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

SPEC_SHA256 = "93567b6cd340f221249b775c968d2447797a90331d1985ad0bdd516ebaf8370f"
FROZEN_HASHES = {
    SPEC: SPEC_SHA256,
    STAGE1_DAILY: "b696e865e3ac956c8ebe176506261779f9c1d216645c328c04f2ec824cd8d209",
    STAGE1_MANIFEST: "cdc0c5d1903ff8b030dd16f822a1964e0bf7795a715612635f40190973707e8f",
    SOURCE_SCHEDULE: "dba99b2aa67a52c9b17a25e03e89325207aae6614bc651052b99168575a38d7a",
    V2_DAILY: "6cbf2a441515087ac6d6ce98b03de8c54d87334e8288e0b0b9e8720155c8da35",
    V2_SCHEDULE: "4a7612d230882e7061da2b61d1016d9480ea11de7c4acf945980aa46a8b1d501",
}

CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
REAL_START = pd.Timestamp("2022-09-19")
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
    ("real_put_period", "real"),
)
STRATEGIES = {
    "roll50_momentum50_no_put": "50:50，不加Put",
    "put_bare50_only": "Put只保护裸滚50%",
    "put_both_sleeves": "Put保护裸滚及实际动量袖",
}
PUT_VARIANTS = ("put_bare50_only", "put_both_sleeves")


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
    frame = pd.read_csv(STAGE1_DAILY, parse_dates=["date"], low_memory=False)
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame["date"].duplicated().any():
        raise RuntimeError("Duplicate stage-1 dates")
    expected = {0.0, 0.5, 1.0}
    observed = set(frame["momentum_weight"].astype(float).unique())
    if observed != expected:
        raise RuntimeError(f"Unexpected momentum weights: {observed}")
    return frame


def build_v2_schedule(base: pd.DataFrame) -> pd.DataFrame:
    source = pd.read_csv(
        SOURCE_SCHEDULE, parse_dates=["eval_date", "execution_date"], low_memory=False
    )
    source = source[source["signal_variant"].eq("l190_mom25")].copy()
    if set(source["layer"].unique()) != {"model", "real"}:
        raise RuntimeError("Both model and real schedules are required")
    selected = put_rules.build_schedule(source, frozen_v2.IC_DEFINITION)
    selected["v2_target_delta"] = selected["target_delta"].astype(float)
    selected["v2_risk_tier"] = selected["risk_tier"].astype(int)

    allowed = {0.0, 0.25, 0.50, 0.75, 1.00}
    observed = set(np.round(selected["v2_target_delta"].unique(), 12))
    if not observed.issubset(allowed):
        raise RuntimeError(f"Unexpected V2 targets: {observed}")
    negative = selected["momentum_120"].astype(float).lt(0.0)
    if not selected.loc[negative, "v2_target_delta"].ge(0.50 - 1e-12).all():
        raise RuntimeError("V2 negative-MOM120 50% floor failed")
    if not selected["v2_target_delta"].le(1.0 + 1e-12).all():
        raise RuntimeError("V2 target cap failed")
    regular = selected[~selected["initial_exception"].fillna(False).astype(bool)]
    if not regular["execution_date"].gt(regular["eval_date"]).all():
        raise RuntimeError("Put signal T+1 check failed")

    weights = base[["date", "momentum_weight"]].rename(columns={"date": "execution_date"})
    selected = selected.merge(weights, on="execution_date", how="left", validate="many_to_one")
    if selected["momentum_weight"].isna().any():
        raise RuntimeError("Missing momentum weight on Put execution date")
    return selected.sort_values(["layer", "execution_date"]).reset_index(drop=True)


def scaled_schedule(selected: pd.DataFrame, variant: str) -> pd.DataFrame:
    schedule = selected.copy()
    if variant == "put_bare50_only":
        schedule["put_sleeve_scale"] = 0.50
    elif variant == "put_both_sleeves":
        schedule["put_sleeve_scale"] = 0.50 + 0.50 * schedule["momentum_weight"].astype(float)
    else:
        raise ValueError(variant)
    target = schedule["v2_target_delta"] * schedule["put_sleeve_scale"]
    schedule["target_delta"] = target
    schedule["binary_target_fraction"] = target
    schedule["three_tier_target_fraction"] = target
    schedule["signal_variant"] = variant
    schedule["candidate"] = variant
    schedule["schedule_candidate"] = variant
    identity = float((target - schedule["v2_target_delta"] * schedule["put_sleeve_scale"]).abs().max())
    if identity > 1e-14:
        raise RuntimeError(f"Target identity failed: {variant}: {identity}")
    return schedule


def run_overlays(
    base: pd.DataFrame, selected: pd.DataFrame
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    frames, _valuation, market, market_checks = put_engine.v19.v18.load_close_inputs()
    roll_dates = put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])

    # Re-run the unscaled real V2 path to prove that the selected rules and engine
    # still reproduce the frozen formal Put components before creating variants.
    raw_real, _raw_trades = put_engine.run_real_delta(
        frames["ic"], selected, frames, market, frozen_v2.IC_SELECTED, roll_dates
    )
    frozen = pd.read_csv(V2_DAILY, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["product"].eq("IC") & frozen["candidate"].eq(frozen_v2.IC_SELECTED)
    ].sort_values("date")
    columns = ["put_pnl_ret", "put_cost_rate", "put_mark_fraction"]
    parity = raw_real[["date", *columns]].merge(
        frozen[["date", *columns]], on="date", suffixes=("_rerun", "_frozen"),
        validate="one_to_one",
    )
    parity_error = max(
        float((parity[f"{column}_rerun"] - parity[f"{column}_frozen"]).abs().max())
        for column in columns
    )
    if len(parity) != len(frozen) or parity_error > 1e-12:
        raise RuntimeError(f"Frozen real V2 Put parity failed: {len(parity)}, {parity_error}")

    overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    for variant in PUT_VARIANTS:
        schedule = scaled_schedule(selected, variant)
        model_overlay, model_trades = put_engine.run_model_delta(
            frames["ic"], schedule, market, variant, roll_dates
        )
        real_overlay, real_trades = put_engine.run_real_delta(
            frames["ic"], schedule, frames, market, variant, roll_dates
        )
        model_overlay = model_overlay[model_overlay["date"].lt(REAL_START)].assign(layer="model")
        real_overlay = real_overlay[real_overlay["date"].ge(REAL_START)].assign(layer="real")
        combined = pd.concat([model_overlay, real_overlay], ignore_index=True, sort=False)
        combined = combined.sort_values("date").reset_index(drop=True)
        if len(combined) != len(base) or not combined["date"].equals(base["date"]):
            raise RuntimeError(f"Put layer splice mismatch: {variant}")
        overlays[variant] = combined

        schedule_parts.append(schedule.assign(variant=variant))
        if len(model_trades):
            trade_parts.append(model_trades.assign(layer="model", variant=variant))
        if len(real_trades):
            trade_parts.append(real_trades.assign(layer="real", variant=variant))

    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)
    audit = {
        "frozen_v2_real_put_component_parity_max_abs": parity_error,
        "frozen_v2_real_rows": int(len(frozen)),
        "model_end": str(base.loc[base["date"].lt(REAL_START), "date"].max().date()),
        "real_start": str(base.loc[base["date"].ge(REAL_START), "date"].min().date()),
        "market_checks": market_checks,
    }
    return overlays, schedules, trades, audit


def assemble_daily(base: pd.DataFrame, overlays: dict[str, pd.DataFrame]) -> pd.DataFrame:
    keep = [
        "date", "contract", "settle", "ic_gross_ret", "roll_event", "momentum_weight",
        "roll50_momentum50_ic_units", "roll50_momentum50_ic_futures_gross_ret",
        "roll50_momentum50_ic_futures_cost_rate", "roll50_momentum50_ic_cash_weight",
        "roll50_momentum50_ic_ret",
    ]
    result = base[keep].copy()
    result["put_data_layer"] = np.where(result["date"].lt(REAL_START), "model", "real")
    result["roll50_momentum50_no_put_ret"] = result["roll50_momentum50_ic_ret"]
    result["roll50_momentum50_no_put_put_pnl_ret"] = 0.0
    result["roll50_momentum50_no_put_put_cost_rate"] = 0.0
    result["roll50_momentum50_no_put_put_mark_fraction"] = 0.0
    result["roll50_momentum50_no_put_cash_weight"] = result[
        "roll50_momentum50_ic_cash_weight"
    ]

    base_pre_cash = (
        result["roll50_momentum50_ic_ret"]
        - result["roll50_momentum50_ic_cash_weight"] * CASH_DAILY
    )
    for variant, overlay in overlays.items():
        for column in (
            "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_contract",
            "put_qty", "target_delta", "actual_notional_fraction",
            "effective_delta_hedge_ratio", "layer",
        ):
            result[f"{variant}_{column}"] = overlay[column].to_numpy()
        cash = result["roll50_momentum50_ic_cash_weight"] - result[
            f"{variant}_put_mark_fraction"
        ]
        if cash.lt(-1e-12).any():
            raise RuntimeError(f"Negative cash weight: {variant}")
        result[f"{variant}_cash_weight"] = cash.clip(lower=0.0)
        result[f"{variant}_ret"] = (
            (1.0 + base_pre_cash + result[f"{variant}_put_pnl_ret"])
            * (1.0 - result[f"{variant}_put_cost_rate"])
            - 1.0
            + result[f"{variant}_cash_weight"] * CASH_DAILY
        )

    no_put_error = float(
        (result["roll50_momentum50_no_put_ret"] - result["roll50_momentum50_ic_ret"])
        .abs().max()
    )
    if no_put_error > 1e-15:
        raise RuntimeError(f"No-Put baseline parity failed: {no_put_error}")
    for strategy in STRATEGIES:
        ret = result[f"{strategy}_ret"].astype(float)
        if ret.isna().any() or ret.le(-1.0).any():
            raise RuntimeError(f"Invalid return path: {strategy}")
        result[f"{strategy}_nav"] = (1.0 + ret).cumprod()
        result[f"{strategy}_drawdown"] = (
            result[f"{strategy}_nav"] / result[f"{strategy}_nav"].cummax() - 1.0
        )
    return result


def metric_values(sample: pd.DataFrame, strategy: str) -> dict[str, float]:
    ret = sample[f"{strategy}_ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    ann_return = float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0)
    ann_vol = float(ret.std(ddof=0) * math.sqrt(252.0))
    put_cost = (
        float(sample[f"{strategy}_put_cost_rate"].sum())
        if f"{strategy}_put_cost_rate" in sample else 0.0
    )
    put_mark = (
        float(sample[f"{strategy}_put_mark_fraction"].max())
        if f"{strategy}_put_mark_fraction" in sample else 0.0
    )
    return {
        "rows": int(len(sample)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": float(dd.min()),
        "calmar": ann_return / abs(float(dd.min())) if float(dd.min()) < -1e-12 else np.nan,
        "final_nav": float(nav.iloc[-1]),
        "avg_ic_units": float(sample["roll50_momentum50_ic_units"].mean()),
        "put_cost_total": put_cost,
        "max_put_mark_fraction": put_mark,
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
                sample = frame[frame["date"].ge(REAL_START)]
            else:
                sample = frame[frame["date"].ge(end - offset)]
            values = metric_values(sample, strategy)
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **values,
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
    frame: pd.DataFrame, schedules: pd.DataFrame, trades: pd.DataFrame, audit: dict[str, Any]
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["no_put_stage1_parity_max_abs"] = float(
        (frame["roll50_momentum50_no_put_ret"] - frame["roll50_momentum50_ic_ret"])
        .abs().max()
    )
    for variant in PUT_VARIANTS:
        schedule = schedules[schedules["variant"].eq(variant)]
        expected_scale = (
            pd.Series(0.50, index=schedule.index)
            if variant == "put_bare50_only"
            else 0.50 + 0.50 * schedule["momentum_weight"].astype(float)
        )
        checks[f"{variant}_target_identity_max_abs"] = float(
            (schedule["target_delta"] - schedule["v2_target_delta"] * expected_scale)
            .abs().max()
        )
        checks[f"{variant}_trade_events_model"] = int(
            ((trades["variant"].eq(variant)) & (trades["layer"].eq("model"))).sum()
        )
        checks[f"{variant}_trade_events_real"] = int(
            ((trades["variant"].eq(variant)) & (trades["layer"].eq("real"))).sum()
        )
        checks[f"{variant}_min_cash_weight"] = float(frame[f"{variant}_cash_weight"].min())
    checks.update(audit)
    checks["all_checks_passed"] = bool(
        checks["no_put_stage1_parity_max_abs"] <= 1e-15
        and checks["put_bare50_only_target_identity_max_abs"] <= 1e-14
        and checks["put_both_sleeves_target_identity_max_abs"] <= 1e-14
        and checks["frozen_v2_real_put_component_parity_max_abs"] <= 1e-12
        and checks["put_bare50_only_min_cash_weight"] >= -1e-12
        and checks["put_both_sleeves_min_cash_weight"] >= -1e-12
    )
    return checks


def pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def write_record(metrics: pd.DataFrame, validation: dict[str, Any]) -> None:
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
    text = f"""# IC 分层研究第二层：50:50 底座叠加动态 Put v1

状态：研究完成；未批准实盘  
全样本：2015-04-16 至 2026-08-14

每格为年化收益 / 最大回撤。

{chr(10).join(lines)}

## 固定规则

- 底座固定为 50% 裸滚 IC + 50% 动量门控 IC；Call 和网格关闭。
- Put 为当前 IC V2：1.90/1.95/2.00/2.05 对应每完整受保护袖 25%/50%/75%/100% 目标 Delta；MOM120<0 最低 50%，两者取较大值。
- “只保护裸滚”总目标为 V2 目标的 50%；“两袖都保护”总目标为 V2 目标乘以实际 0.5/0.75/1.0 倍 IC 名义。
- 动量袖启停直接进入 Put 执行引擎，包含新增调仓和成本，不是事后缩放 Put 收益。

## 数据边界

- 2015-04-16—2022-09-16 为真实 IC + QVIX/Black-Scholes 理论 Put。
- 2022-09-19—2026-08-14 为真实 IC + 真实 510500ETF Put、实际挂牌合约及整数张数。
- `真实Put期`单列用于避免理论段主导解释；近3年和近1年也全部位于真实期权段。

## 审计

- 无 Put 路径复现第一层最大误差 {validation['no_put_stage1_parity_max_abs']:.3e}。
- 重新执行冻结 V2 真实 Put 组件最大误差 {validation['frozen_v2_real_put_component_parity_max_abs']:.3e}。
- 只保护裸滚 / 两袖保护的模型调仓事件：{validation['put_bare50_only_trade_events_model']} / {validation['put_both_sleeves_trade_events_model']}；真实调仓事件：{validation['put_bare50_only_trade_events_real']} / {validation['put_both_sleeves_trade_events_real']}。
- 结果只用于分层研究，不修改冻结 V2 主线、Poe 或实盘配置。
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
        selected = build_v2_schedule(base)
        overlays, schedules, trades, overlay_audit = run_overlays(base, selected)
        daily = assemble_daily(base, overlays)
        metrics = build_metrics(daily)
        annual = build_annual(daily)
        validation = build_validation(daily, schedules, trades, overlay_audit)
        if not validation["all_checks_passed"]:
            raise RuntimeError(f"Formal validation failed: {validation}")

        daily.to_csv(STAGING / "daily_nav.csv.gz", index=False, compression="gzip")
        schedules.to_csv(STAGING / "put_target_schedules.csv.gz", index=False, compression="gzip")
        trades.to_csv(STAGING / "put_trades.csv.gz", index=False, compression="gzip")
        metrics.to_csv(STAGING / "metrics_by_window.csv", index=False)
        annual.to_csv(STAGING / "annual_metrics.csv", index=False)
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        write_record(metrics, validation)

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
                "model_put_end": validation["model_end"],
                "real_put_start": validation["real_start"],
            },
            "fixed_rules": {
                "base": "50pct_bare_roll_ic_plus_50pct_momentum_gated_ic",
                "momentum": "CSI500_MA110_Mom24_W2_Abs20_OFF50_gt0_50",
                "put": frozen_v2.IC_DEFINITION,
                "call": "excluded",
                "grid": "excluded",
            },
            "cost_and_capital": {
                "put_one_way_cost": put_engine.PUT_SIDE_COST,
                "margin_buffer_per_1x_ic": 0.30,
                "cash_assumed_net_annual_return": 0.03,
            },
            "inputs": input_hashes,
            "files": files,
        }
        (STAGING / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        STAGING.rename(OUTPUT)
        print(metrics.to_string(index=False))
        print(f"Formal output: {OUTPUT}")
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


if __name__ == "__main__":
    main()
