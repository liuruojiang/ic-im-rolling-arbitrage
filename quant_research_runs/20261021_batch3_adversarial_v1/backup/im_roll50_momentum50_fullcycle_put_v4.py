from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import freeze_ic_im_system_mainlines_v2 as frozen_v2
import im_roll50_momentum50_fullcycle_put_v1 as put_v1
import im_roll50_momentum50_fullcycle_put_v3 as v3


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_fullcycle_put_v4"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
V3_DAILY = ROOT / "outputs/im_roll50_momentum50_fullcycle_put_v3/daily_nav.csv.gz"
V3_MANIFEST = ROOT / "outputs/im_roll50_momentum50_fullcycle_put_v3/data_manifest.json"
OUTPUT = ROOT / "outputs" / VERSION
SPEC_SHA256 = "1fd4b2cb8300c28be10b6eafcec819bb095dca3cffcc2ef4f1489620d2dfaf43"
PINNED = {
    V3_DAILY: "3f7a6236a40b7de8633ef0234f3ce080e5fbef08ee3560e2a4fcf701769854cb",
    V3_MANIFEST: "345854bf3a78bf9befd63be14be49dd647dd65dc7fe00e939f30dd0abfb5663a",
}
NEW = "momentum_valuation_only_put"
STRATEGIES = {
    "no_put": "no_put_ret",
    "bare_full_put_only": "bare_full_put_only_ret",
    "both_full_put_v3": "sleeve_matched_dynamic_put_ret",
    NEW: f"{NEW}_ret",
}
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)
VALUATION_MODEL_LABEL = "IM_4tier_q750_850_900_925_valuation_only_model"
VALUATION_REAL_LABEL = "IM_4tier_q750_850_900_925_valuation_only_real"


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


def build_valuation_only_put() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    market, market_checks = put_v1.im_v12.v6.model_market()
    state, state_audit = put_v1.current_rule_state()
    state = state.copy()
    state["full_target_qty"] = state["target_qty"].astype(int)
    state["target_qty"] = state["valuation_tier"].astype(int)
    model_schedule = put_v1.im_v12.build_momentum_schedule(
        state,
        VALUATION_MODEL_LABEL,
        pd.DatetimeIndex(market["date"]),
        "dual57_four_tier_abs_valuation_only",
    )
    model, model_trades, model_lives = put_v1.im_v12.v8.run_model_normal_close(
        market, model_schedule, "3m", 0.95, VALUATION_MODEL_LABEL
    )
    model = model[model["date"].lt(put_v1.REAL_START)].copy()
    last_model = model.index[-1]
    model.loc[last_model, "put_cost_rate"] += float(model.loc[last_model, "put_fraction"]) * put_v1.MODEL_SIDE_COST
    model["put_source"] = "theoretical_valuation_only_put"

    upstream, active_im, options, source, valuation_state, thresholds, _frozen = frozen_v2._im_source_data()
    real_schedule = frozen_v2.build_im_selected_schedule(source, valuation_state, thresholds).copy()
    real_schedule["full_target_qty"] = real_schedule["binary_target_qty"].astype(int)
    valuation_qty = real_schedule["new_valuation_tier"].astype(int)
    real_schedule["binary_target_qty"] = valuation_qty
    real_schedule["three_tier_target_qty"] = valuation_qty
    real_schedule["candidate"] = VALUATION_REAL_LABEL
    real_schedule["schedule_candidate"] = VALUATION_REAL_LABEL
    real, real_trades, real_lives = put_v1.im_v12.v8.run_real_normal_close(
        upstream, options, active_im, real_schedule, "3m", 0.95, VALUATION_REAL_LABEL
    )
    real = real[real["date"].ge(put_v1.REAL_START)].copy()
    real["put_source"] = "real_mo_valuation_only"

    columns = ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction", "put_contract", "put_source"]
    put = pd.concat([model[columns], real[columns]], ignore_index=True, sort=False).sort_values("date").reset_index(drop=True)
    if put.duplicated("date").any() or put[["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]].isna().any().any():
        raise RuntimeError("Invalid valuation-only IM Put component")
    schedules = pd.concat(
        [model_schedule.assign(layer="model"), real_schedule.assign(layer="real")],
        ignore_index=True,
        sort=False,
    )
    audit = {
        "market_check_keys": sorted(str(key) for key in market_checks),
        "state_audit": state_audit,
        "model_trade_events": int(len(model_trades)),
        "real_trade_events": int(len(real_trades)),
        "model_lifecycles": int(len(model_lives)),
        "real_lifecycles": int(len(real_lives)),
        "model_start": model["date"].min().date().isoformat(),
        "model_end": model["date"].max().date().isoformat(),
        "real_start": real["date"].min().date().isoformat(),
        "real_end": real["date"].max().date().isoformat(),
    }
    return put, schedules, audit


def add_return_path(frame: pd.DataFrame, label: str, pnl: pd.Series, cost: pd.Series, mark: pd.Series) -> None:
    frame[f"{label}_put_pnl_ret"] = pnl
    frame[f"{label}_put_cost_rate"] = cost
    frame[f"{label}_put_mark_fraction"] = mark
    frame[f"{label}_pre_cash_ret"] = (1.0 + frame["baseline_pre_cash_ret"] + pnl) * (1.0 - cost) - 1.0
    raw_cash = frame["blend_cash_weight"] - mark
    if raw_cash.lt(-1e-12).any():
        raise RuntimeError(f"Negative cash in {label}")
    frame[f"{label}_cash_weight"] = raw_cash.clip(lower=0.0)
    frame[f"{label}_ret"] = frame[f"{label}_pre_cash_ret"] + frame[f"{label}_cash_weight"] * v3.CASH_DAILY


def build_daily(valuation_put: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = pd.read_csv(V3_DAILY, parse_dates=["date"], low_memory=False).sort_values("date").reset_index(drop=True)
    frame = frame.merge(
        valuation_put.rename(columns={c: f"valuation_only_{c}" for c in valuation_put.columns if c != "date"}),
        on="date", how="inner", validate="one_to_one",
    )
    if len(frame) != 2756:
        raise RuntimeError(f"Unexpected IM common rows: {len(frame)}")
    momentum_scale = 0.5 * frame["momentum_weight"].astype(float)
    frame["momentum_valuation_put_scale"] = momentum_scale
    for field in ("put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"):
        frame[f"momentum_valuation_{field}"] = frame[f"valuation_only_{field}"] * momentum_scale
    frame["momentum_valuation_put_qty"] = 2.0 * frame["momentum_valuation_put_fraction"]

    add_return_path(
        frame, "bare_full_put_only",
        frame["bare_sleeve_put_pnl_ret"], frame["bare_sleeve_put_cost_rate"], frame["bare_sleeve_put_mark_fraction"],
    )
    combined_pnl = frame["bare_sleeve_put_pnl_ret"] + frame["momentum_valuation_put_pnl_ret"]
    combined_cost = frame["bare_sleeve_put_cost_rate"] + frame["momentum_valuation_put_cost_rate"]
    combined_mark = frame["bare_sleeve_put_mark_fraction"] + frame["momentum_valuation_put_mark_fraction"]
    add_return_path(frame, NEW, combined_pnl, combined_cost, combined_mark)
    frame[f"{NEW}_put_qty"] = frame["bare_sleeve_put_qty"] + frame["momentum_valuation_put_qty"]
    frame[f"{NEW}_put_notional_fraction"] = frame["bare_sleeve_put_notional_fraction"] + frame["momentum_valuation_put_fraction"]

    if (frame[f"{NEW}_put_qty"] > frame["combined_put_qty"] + 1e-12).any():
        raise RuntimeError("Removing MOM120 increased IM Put quantity")
    if frame[f"{NEW}_put_qty"].max() > 4.0 + 1e-12:
        raise RuntimeError("New IM Put path exceeds four-contract cap")
    for strategy, column in STRATEGIES.items():
        frame[f"{strategy}_nav"] = (1.0 + frame[column]).cumprod()
        frame[f"{strategy}_drawdown"] = frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0

    removed = frame["combined_put_qty"] - frame[f"{NEW}_put_qty"]
    audit = {
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "rows": int(len(frame)),
        "proxy_rows": int(frame["put_source"].eq("theoretical_csi1000_put").sum()),
        "real_rows": int(frame["put_source"].eq("real_mo_frozen_v2").sum()),
        "put_quantity_reduced_days": int(removed.gt(1e-12).sum()),
        "average_qty_reduction_when_changed": float(removed[removed.gt(1e-12)].mean()),
        "max_old_combined_put_qty": float(frame["combined_put_qty"].max()),
        "max_new_combined_put_qty": float(frame[f"{NEW}_put_qty"].max()),
        "min_new_cash_weight": float(frame[f"{NEW}_cash_weight"].min()),
        "new_vs_bare_return_path_max_abs": float((frame[f"{NEW}_ret"] - frame["bare_full_put_only_ret"]).abs().max()),
    }
    return frame, audit


def metric_values(ret: pd.Series) -> dict[str, float]:
    nav = (1.0 + ret.astype(float)).cumprod()
    ann = float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0)
    vol = float(ret.std(ddof=0) * math.sqrt(252.0))
    dd = float((nav / nav.cummax() - 1.0).min())
    return {"rows": int(len(ret)), "ann_return": ann, "ann_vol": vol, "sharpe_repo": ann / vol if vol > 1e-12 else 0.0, "max_dd": dd, "final_nav": float(nav.iloc[-1])}


def build_metrics(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    end = frame["date"].max()
    windows = []
    for strategy, column in STRATEGIES.items():
        for window, offset in WINDOWS:
            sample = frame if offset is None else frame[frame["date"].ge(end - offset)]
            windows.append({"strategy": strategy, "window": window, "start": sample["date"].min().date().isoformat(), "end": sample["date"].max().date().isoformat(), "proxy_rows": int(sample["put_source"].eq("theoretical_csi1000_put").sum()), "real_rows": int(sample["put_source"].eq("real_mo_frozen_v2").sum()), **metric_values(sample[column])})
    phases = []
    for phase, mask in (("theoretical_put_proxy", frame["put_source"].eq("theoretical_csi1000_put")), ("real_im_mo", frame["put_source"].eq("real_mo_frozen_v2"))):
        sample = frame[mask]
        for strategy, column in STRATEGIES.items():
            phases.append({"phase": phase, "strategy": strategy, "start": sample["date"].min().date().isoformat(), "end": sample["date"].max().date().isoformat(), **metric_values(sample[column])})
    annual = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy, column in STRATEGIES.items():
            annual.append({"year": int(year), "strategy": strategy, **metric_values(sample[column])})
    return pd.DataFrame(windows), pd.DataFrame(phases), pd.DataFrame(annual)


def write_record(metrics: pd.DataFrame, phases: pd.DataFrame, audit: dict[str, object]) -> None:
    labels = {"no_put": "不加Put", "bare_full_put_only": "只保护裸滚袖", "both_full_put_v3": "原v3两袖完整V2 Put", NEW: "动量袖去MOM120下限"}
    lines = ["|路径|全样本|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in labels.items():
        block = metrics[metrics["strategy"].eq(strategy)].set_index("window")
        lines.append(f"|{label}|" + "|".join(f"{100*block.loc[w,'ann_return']:.2f}% / {100*block.loc[w,'max_dd']:.2f}%" for w, _ in WINDOWS) + "|")
    phase_lines = ["|分层|路径|年化收益|最大回撤|", "|---|---|---:|---:|"]
    for phase, phase_label in (("theoretical_put_proxy", "理论Put/代理IM"), ("real_im_mo", "真实IM/MO")):
        for strategy, label in labels.items():
            row = phases[(phases["phase"].eq(phase)) & (phases["strategy"].eq(strategy))].iloc[0]
            phase_lines.append(f"|{phase_label}|{label}|{100*row.ann_return:.2f}%|{100*row.max_dd:.2f}%|")
    text = f"""# 50%裸滚IM + 50%动量IM：动量袖Put去MOM120下限 v4

状态：规则消融研究完成；未批准实盘

## Key Results

每格为年化收益 / 最大回撤。

{chr(10).join(lines)}

### 理论与真实分层

{chr(10).join(phase_lines)}

## Rule and Integrity

- 裸滚袖继续采用完整V2估值与MOM120四张下限；动量袖Put只采用估值目标。
- 新路径Put数量低于原v3的天数：{audit['put_quantity_reduced_days']}；变动日平均减少{audit['average_qty_reduction_when_changed']:.2f}张。
- 原v3/新路径最大组合Put：{audit['max_old_combined_put_qty']:.2f}/{audit['max_new_combined_put_qty']:.2f}张；新路径最低现金{audit['min_new_cash_weight']:.2%}。
- 底仓、期限、T+1、成本、现金与共同日期不变；Call和网格关闭。

## Data and Limits

- 样本{audit['start']}至{audit['end']}；理论Put/代理IM {audit['proxy_rows']}日，真实IM/MO {audit['real_rows']}日。
- 上市前平均贴水回填及理论Put含未来信息与模型风险；不能视为真实可执行历史。
- 真实段未计盘口价差、冲击、容量、动态保证金、价格限制和整数合约映射误差。
- 本结果仅为研究，不修改冻结IM V2主线、Poe或交易配置。
"""
    (OUTPUT / "record.md").write_text(text, encoding="utf-8")


def run() -> None:
    inputs = verify_inputs()
    valuation_put, schedules, engine_audit = build_valuation_only_put()
    frame, audit = build_daily(valuation_put)
    audit.update(engine_audit)
    metrics, phases, annual = build_metrics(frame)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    frame.to_csv(OUTPUT / "daily_nav.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    metrics.to_csv(OUTPUT / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    phases.to_csv(OUTPUT / "phase_metrics.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    schedules.to_csv(OUTPUT / "valuation_only_put_schedules.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    (OUTPUT / "diagnostics.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    manifest = {"generated_at": datetime.now().astimezone().isoformat(), "command": f"{Path(sys.executable).name} {Path(__file__).name}", "status": "research_only_not_live", "inputs": inputs, "audit": audit}
    (OUTPUT / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (OUTPUT / "command_log.txt").write_text(manifest["command"] + "\n", encoding="utf-8")
    write_record(metrics, phases, audit)


if __name__ == "__main__":
    run()
