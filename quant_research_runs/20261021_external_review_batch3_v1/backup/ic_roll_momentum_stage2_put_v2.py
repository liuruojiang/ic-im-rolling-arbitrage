from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import ic_roll_momentum_stage2_put_v1 as v1


ROOT = Path(__file__).resolve().parent
VERSION = "ic_roll_momentum_stage2_put_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OLD_DAILY = ROOT / "outputs/ic_roll_momentum_stage2_put_v1/daily_nav.csv.gz"
OLD_MANIFEST = ROOT / "outputs/ic_roll_momentum_stage2_put_v1/run_manifest.json"
OUTPUT = ROOT / "outputs" / VERSION
SPEC_SHA256 = "ca3b11788c266d18e491e22e21e385796dddb14ae05bbbd002df9096d2356787"
PINNED = {
    OLD_DAILY: "653adb0eed4aeb434f8497e9e3a42862c09979811132f43b9c21dee90031b8cc",
    OLD_MANIFEST: "7014781b3e5432b974ade28eb7811f9ff81e83814fd5b8e9bdef44d5c4fb9b86",
}
NEW = "put_momentum_valuation_only"
STRATEGIES = (
    "roll50_momentum50_no_put",
    "put_bare50_only",
    "put_both_sleeves",
    NEW,
)
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
    ("real_put_period", "real"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH_FILE, *PINNED):
        if not path.exists():
            raise FileNotFoundError(path)
    actual_spec = sha256(SPEC)
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if actual_spec != SPEC_SHA256 or sidecar != SPEC_SHA256:
        raise RuntimeError("Specification hash mismatch")
    for path, expected in PINNED.items():
        if sha256(path) != expected:
            raise RuntimeError(f"Pinned input changed: {path}")
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    return {str(SPEC.relative_to(ROOT)): actual_spec, **{str(p.relative_to(ROOT)): sha256(p) for p in PINNED}}


def build_new_schedule(selected: pd.DataFrame) -> pd.DataFrame:
    schedule = selected.copy()
    valuation_target = schedule["valuation_tier_new"].astype(float) * 0.25
    full_target = schedule["v2_target_delta"].astype(float)
    momentum_weight = schedule["momentum_weight"].astype(float)
    target = 0.50 * full_target + 0.50 * momentum_weight * valuation_target
    schedule["valuation_only_target_delta"] = valuation_target
    schedule["bare_full_target_delta"] = 0.50 * full_target
    schedule["momentum_valuation_target_delta"] = 0.50 * momentum_weight * valuation_target
    schedule["target_delta"] = target
    schedule["binary_target_fraction"] = target
    schedule["three_tier_target_fraction"] = target
    schedule["signal_variant"] = NEW
    schedule["candidate"] = NEW
    schedule["schedule_candidate"] = NEW
    if not target.between(0.0, 1.0).all():
        raise RuntimeError("New IC target outside 0..1")
    identity = (target - schedule["bare_full_target_delta"] - schedule["momentum_valuation_target_delta"]).abs().max()
    if float(identity) > 1e-14:
        raise RuntimeError(f"IC target identity failed: {identity}")
    return schedule


def run_new_overlay(base: pd.DataFrame, schedule: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    frames, _valuation, market, market_checks = v1.put_engine.v19.v18.load_close_inputs()
    roll_dates = v1.put_engine.v19.v18.v13.v6.forced_roll_dates(frames["ic"])
    model, model_trades = v1.put_engine.run_model_delta(frames["ic"], schedule, market, NEW, roll_dates)
    real, real_trades = v1.put_engine.run_real_delta(frames["ic"], schedule, frames, market, NEW, roll_dates)
    model = model[model["date"].lt(v1.REAL_START)].assign(layer="model")
    real = real[real["date"].ge(v1.REAL_START)].assign(layer="real")
    overlay = pd.concat([model, real], ignore_index=True, sort=False).sort_values("date").reset_index(drop=True)
    if len(overlay) != len(base) or not overlay["date"].equals(base["date"]):
        raise RuntimeError("IC model/real splice mismatch")
    trades = pd.concat(
        [model_trades.assign(layer="model"), real_trades.assign(layer="real")],
        ignore_index=True,
        sort=False,
    )
    return overlay, trades, {
        "market_check_keys": sorted(str(key) for key in market_checks),
        "model_trade_events": int(len(model_trades)),
        "real_trade_events": int(len(real_trades)),
    }


def assemble(base: pd.DataFrame, overlay: pd.DataFrame) -> pd.DataFrame:
    old = pd.read_csv(OLD_DAILY, parse_dates=["date"], low_memory=False).sort_values("date").reset_index(drop=True)
    if len(old) != len(base) or not old["date"].equals(base["date"]):
        raise RuntimeError("IC v1 output/base mismatch")
    result = old.copy()
    for column in (
        "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_contract", "put_qty",
        "target_delta", "actual_notional_fraction", "effective_delta_hedge_ratio", "layer",
    ):
        result[f"{NEW}_{column}"] = overlay[column].to_numpy()
    base_pre_cash = result["roll50_momentum50_ic_ret"] - result["roll50_momentum50_ic_cash_weight"] * v1.CASH_DAILY
    cash = result["roll50_momentum50_ic_cash_weight"] - result[f"{NEW}_put_mark_fraction"]
    if cash.lt(-1e-12).any():
        raise RuntimeError("IC new Put path has negative cash")
    result[f"{NEW}_cash_weight"] = cash.clip(lower=0.0)
    result[f"{NEW}_ret"] = (
        (1.0 + base_pre_cash + result[f"{NEW}_put_pnl_ret"])
        * (1.0 - result[f"{NEW}_put_cost_rate"])
        - 1.0
        + result[f"{NEW}_cash_weight"] * v1.CASH_DAILY
    )
    for strategy in STRATEGIES:
        result[f"{strategy}_nav"] = (1.0 + result[f"{strategy}_ret"]).cumprod()
        result[f"{strategy}_drawdown"] = result[f"{strategy}_nav"] / result[f"{strategy}_nav"].cummax() - 1.0
    return result


def metrics_one(sample: pd.DataFrame, strategy: str) -> dict[str, float]:
    ret = sample[f"{strategy}_ret"].astype(float)
    nav = (1.0 + ret).cumprod()
    ann = float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0)
    vol = float(ret.std(ddof=0) * math.sqrt(252.0))
    dd = float((nav / nav.cummax() - 1.0).min())
    return {
        "rows": int(len(sample)), "ann_return": ann, "ann_vol": vol, "max_dd": dd,
        "sharpe_repo": ann / vol if vol > 1e-12 else 0.0, "final_nav": float(nav.iloc[-1]),
        "put_cost_total": float(sample.get(f"{strategy}_put_cost_rate", pd.Series(0.0, index=sample.index)).sum()),
        "max_put_mark_fraction": float(sample.get(f"{strategy}_put_mark_fraction", pd.Series(0.0, index=sample.index)).max()),
        "min_cash_weight": float(sample[f"{strategy}_cash_weight"].min()),
    }


def build_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    end = frame["date"].max()
    rows: list[dict[str, object]] = []
    for strategy in STRATEGIES:
        for window, offset in WINDOWS:
            if offset == "real":
                sample = frame[frame["date"].ge(v1.REAL_START)]
            elif offset is None:
                sample = frame
            else:
                sample = frame[frame["date"].ge(end - offset)]
            rows.append({"strategy": strategy, "window": window, "start": sample["date"].min().date().isoformat(), "end": sample["date"].max().date().isoformat(), **metrics_one(sample, strategy)})
    return pd.DataFrame(rows)


def build_annual(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy in STRATEGIES:
            rows.append({"year": int(year), "strategy": strategy, **metrics_one(sample, strategy)})
    return pd.DataFrame(rows)


def write_record(metrics: pd.DataFrame, audit: dict[str, object]) -> None:
    labels = {
        "roll50_momentum50_no_put": "不加Put",
        "put_bare50_only": "只保护裸滚袖",
        "put_both_sleeves": "原两袖完整V2 Put",
        NEW: "动量袖去MOM120下限",
    }
    lines = ["|路径|全样本|近10年|近5年|近3年|近1年|真实Put期|", "|---|---:|---:|---:|---:|---:|---:|"]
    for strategy, label in labels.items():
        block = metrics[metrics["strategy"].eq(strategy)].set_index("window")
        cells = [f"{100*block.loc[w,'ann_return']:.2f}% / {100*block.loc[w,'max_dd']:.2f}%" for w, _ in WINDOWS]
        lines.append(f"|{label}|{'|'.join(cells)}|")
    text = f"""# IC第二层v2：动量袖Put去MOM120下限

状态：规则消融研究完成；未批准实盘

## Key Results

每格为年化收益 / 最大回撤。

{chr(10).join(lines)}

## Rule

- 裸滚50%袖继续使用完整IC V2 Put及`MOM120<0`最低50% Delta。
- 动量袖Put只使用估值四档，不使用MOM120下限；动量空仓时该袖Put为0。
- 新旧路径使用相同底仓、样本、期限、T+1执行、成本和现金口径。

## Integrity

- 新目标与两袖公式最大误差：{audit['target_identity_max_abs']:.3e}。
- 动量空仓时新路径与只保护裸滚目标最大误差：{audit['flat_momentum_target_vs_bare_max_abs']:.3e}。
- 新路径与旧两袖目标不同的计划行：{audit['target_changed_rows']}；其中MOM120下限原本绑定且动量持仓的行：{audit['removed_floor_binding_rows']}。
- 新路径模型/真实调仓事件：{audit['model_trade_events']} / {audit['real_trade_events']}；最低现金：{audit['min_cash_weight']:.2%}。

## Data and Limits

- 样本2015-04-16至2026-08-14；2022-09-19前为理论Put，之后为真实510500ETF Put。
- 真实IC、理论/真实Put、期权费用、IC换月与交易成本均计入；Call与网格关闭。
- 理论段不能视为真实成交；未计盘口价差、冲击、容量、动态保证金和价格限制。
- 本结果仅为研究，不修改冻结IC V2主线、Poe或交易配置。
"""
    (OUTPUT / "record.md").write_text(text, encoding="utf-8")


def run() -> None:
    inputs = verify_inputs()
    base = v1.load_base()
    selected = v1.build_v2_schedule(base)
    schedule = build_new_schedule(selected)
    overlay, trades, engine_audit = run_new_overlay(base, schedule)
    frame = assemble(base, overlay)
    metrics = build_metrics(frame)
    annual = build_annual(frame)
    target_identity = float((schedule["target_delta"] - schedule["bare_full_target_delta"] - schedule["momentum_valuation_target_delta"]).abs().max())
    bare_target = 0.5 * schedule["v2_target_delta"]
    flat = schedule["momentum_weight"].eq(0.0)
    audit = {
        **engine_audit,
        "target_identity_max_abs": target_identity,
        "flat_momentum_target_vs_bare_max_abs": float((schedule.loc[flat, "target_delta"] - bare_target.loc[flat]).abs().max()),
        "target_changed_rows": int((schedule["target_delta"] - (0.5 + 0.5 * schedule["momentum_weight"]) * schedule["v2_target_delta"]).abs().gt(1e-12).sum()),
        "removed_floor_binding_rows": int((schedule["mom_floor_binding"].fillna(False).astype(bool) & schedule["momentum_weight"].gt(0)).sum()),
        "min_cash_weight": float(frame[f"{NEW}_cash_weight"].min()),
    }
    OUTPUT.mkdir(parents=True, exist_ok=False)
    frame.to_csv(OUTPUT / "daily_nav.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    metrics.to_csv(OUTPUT / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    schedule.to_csv(OUTPUT / "put_target_schedule.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    trades.to_csv(OUTPUT / "put_trades.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    (OUTPUT / "diagnostics.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"generated_at": datetime.now().astimezone().isoformat(), "command": f"{Path(sys.executable).name} {Path(__file__).name}", "status": "research_only_not_live", "inputs": inputs, "audit": audit}
    (OUTPUT / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / "command_log.txt").write_text(manifest["command"] + "\n", encoding="utf-8")
    write_record(metrics, audit)


if __name__ == "__main__":
    run()
