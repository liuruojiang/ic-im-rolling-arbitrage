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

import im_fixed_valuation_overlay_lower_boundary_scan_v17 as v17


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_fullcycle_put_call_grid_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
BASE_DETAIL = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v1" / "daily_nav.csv.gz"
BASE_CALL = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_call_v1" / "daily_nav.csv.gz"
V17_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "daily_candidates.csv.gz"
V17_TRADES = ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17" / "overlay_trade_audit.csv"
V15_SCRIPT = ROOT / "im_fixed_valuation_overlay_entry_exit_scan_v15.py"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

SPEC_SHA256 = "387b499cbd93de22c1bf769a5f34247dd32bcd0cd67eb3a5279390c3e53ee3a8"
REAL_START = pd.Timestamp("2022-07-22")
LOW = 0.85
HIGH = 1.25
GRID_CANDIDATE = "fixed_L0.85_H1.25"
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0

FROZEN_HASHES = {
    SPEC: SPEC_SHA256,
    BASE_DETAIL: "2d858c1f1eb2e5b45166af637386ece40736554f9c7e18c486c0dba7bce0e44f",
    BASE_CALL: "0bc99dc20696f9de8eb1e7410543f45cbbc0bf02de5d3322e5d981213f3bb869",
    V17_DAILY: "626d585ef131078fe720fe3368fbf1e31d2ad1ca9fa6ea1b3ca9f41046c7af70",
    V17_TRADES: "92d801768ded5229692fee5c2779415550262c732b910eebaf52b067d17806f2",
    V15_SCRIPT: "d80e7286de1d6571c59b79f965f78a733e7a8243fc3724d581d792d31b3a3aa0",
}

WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)

STRATEGIES = {
    "no_grid": "不加网格",
    "grid_independent": "网格独立运行",
    "grid_momentum_guided": "网格接受动量门控",
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
    if not SPEC_HASH.exists() or SPEC_HASH.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Specification hash sidecar mismatch")
    hashes: dict[str, str] = {}
    for path, expected in FROZEN_HASHES.items():
        if not path.exists():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen input changed: {path}: {actual} != {expected}")
        hashes[str(path.relative_to(ROOT))] = actual
    v17.verify_inputs(require_fresh_output=False)
    return hashes


def guided_overlay(
    market: pd.DataFrame,
    history: pd.DataFrame,
    momentum: pd.Series,
    layer: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    start = pd.Timestamp(market["date"].min())
    carry, carry_date, carry_value = v17.b.state_before_start(
        history, "unbounded_median_knot", LOW, HIGH, start
    )
    gate = pd.Series(momentum.to_numpy(dtype=float), index=pd.DatetimeIndex(momentum.index)).gt(0.0)
    state = False
    pending: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    dates = list(pd.DatetimeIndex(market["date"]))

    for index, row in enumerate(market.itertuples(index=False)):
        day = pd.Timestamp(row.date)
        allowed = bool(gate.loc[day])
        held_before = state
        buy = False
        sell = False
        reason = ""
        signal_date: pd.Timestamp | pd.NaT = pd.NaT
        signal_value = np.nan

        if index == 0 and carry:
            signal_date, signal_value = carry_date, carry_value
            if allowed:
                state, buy, reason = True, True, "initial_carry_gate_pass"
            else:
                events.append(
                    {
                        "layer": layer, "action": "blocked_buy", "signal_date": signal_date,
                        "signal_value": signal_value, "execution_date": day,
                        "execution_reason": "initial_carry_momentum_off", "momentum_weight": 0.0,
                        "execution_contract": row.contract, "execution_open": float(row.open_unit),
                        "execution_volume": row.execution_volume,
                    }
                )
        elif pending is not None and pd.Timestamp(pending["execution_date"]) == day:
            signal_date = pd.Timestamp(pending["signal_date"])
            signal_value = float(pending["signal_value"])
            if pending["action"] == "buy":
                if state:
                    raise RuntimeError("Duplicate guided grid buy")
                if allowed:
                    state, buy, reason = True, True, "valuation_buy_gate_pass"
                else:
                    events.append(
                        {
                            "layer": layer, "action": "blocked_buy", "signal_date": signal_date,
                            "signal_value": signal_value, "execution_date": day,
                            "execution_reason": "valuation_buy_momentum_off", "momentum_weight": 0.0,
                            "execution_contract": row.contract, "execution_open": float(row.open_unit),
                            "execution_volume": row.execution_volume,
                        }
                    )
            else:
                if not state:
                    raise RuntimeError("Guided grid sell while flat")
                state, sell, reason = False, True, "valuation_sell"
            pending = None

        if state and not allowed:
            if buy:
                raise RuntimeError("Guided grid bought while momentum gate was off")
            state, sell, reason = False, True, "momentum_forced_exit"
            signal_date = day
            signal_value = float(row.unbounded_median_knot) if not pd.isna(row.unbounded_median_knot) else np.nan

        held_eod = state
        if held_before and held_eod:
            gross = float(row.settle_unit) / float(row.pre_settle_unit) - 1.0
        elif not held_before and held_eod:
            gross = float(row.settle_unit) / float(row.open_unit) - 1.0
        elif held_before and not held_eod:
            gross = float(row.open_unit) / float(row.pre_settle_unit) - 1.0
        else:
            gross = 0.0
        trade_cost = ONE_WAY_COST * (int(buy) + int(sell))
        roll_cost = 2.0 * ONE_WAY_COST if held_eod and bool(row.roll_event) else 0.0

        if buy or sell:
            events.append(
                {
                    "layer": layer, "action": "buy" if buy else "sell",
                    "signal_date": signal_date, "signal_value": signal_value,
                    "execution_date": day, "execution_reason": reason,
                    "momentum_weight": float(momentum.loc[day]),
                    "execution_contract": row.contract, "execution_open": float(row.open_unit),
                    "execution_volume": row.execution_volume,
                }
            )

        value = row.unbounded_median_knot
        rows.append(
            {
                "date": day,
                "layer": layer,
                "signal_value": value,
                "momentum_gate": int(allowed),
                "overlay_held_before": int(held_before),
                "overlay_held_eod": int(held_eod),
                "overlay_buy": int(buy),
                "overlay_sell": int(sell),
                "overlay_gross_ret": gross,
                "overlay_trade_cost_rate": trade_cost,
                "overlay_roll_cost_rate": roll_cost,
                "overlay_cost_rate": trade_cost + roll_cost,
                "roll_event": bool(row.roll_event),
            }
        )

        if pending is None and not pd.isna(value):
            numeric = float(value)
            action = "buy" if (not state and numeric <= LOW + 1e-12) else None
            if state and numeric >= HIGH - 1e-12:
                action = "sell"
            if action is not None:
                pending = {
                    "action": action,
                    "signal_date": day,
                    "signal_value": numeric,
                    "execution_date": dates[index + 1] if index + 1 < len(dates) else pd.NaT,
                }

    daily = pd.DataFrame(rows)
    event_frame = pd.DataFrame(events)
    if event_frame.empty:
        event_frame = pd.DataFrame(
            columns=[
                "layer", "action", "signal_date", "signal_value", "execution_date",
                "execution_reason", "momentum_weight", "execution_contract",
                "execution_open", "execution_volume",
            ]
        )
    cycle = {
        "layer": layer,
        "entries": int(event_frame["action"].eq("buy").sum()),
        "exits": int(event_frame["action"].eq("sell").sum()),
        "blocked_buys": int(event_frame["action"].eq("blocked_buy").sum()),
        "forced_exits": int(event_frame["execution_reason"].eq("momentum_forced_exit").sum()),
        "holding_days": int(daily["overlay_held_eod"].sum()),
        "ending_state": int(state),
        "pending_order_end": int(pending is not None),
    }
    return daily, event_frame, cycle


def prepare_overlays(
    detail: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    base, score, percentile = v17.b.load_sources()
    model_market, model_checks = v17.b.build_model_market(base, score, percentile)
    real_market, real_checks = v17.b.build_real_market(base, score, percentile)
    history = score[["date", "unbounded_median_knot"]].copy()
    momentum = pd.Series(
        detail["momentum_weight"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(detail["date"]),
    )

    independent_parts: list[pd.DataFrame] = []
    guided_parts: list[pd.DataFrame] = []
    guided_events: list[pd.DataFrame] = []
    cycles: list[dict[str, Any]] = []
    for layer, market in (("model", model_market), ("real", real_market)):
        independent, _, _ = v17.b.simulate_overlay(
            market, history, "unbounded_median_knot", LOW, HIGH,
            GRID_CANDIDATE, "fixed_score", layer,
        )
        independent["layer"] = layer
        independent_parts.append(independent)
        layer_momentum = pd.Series(
            momentum.loc[pd.DatetimeIndex(market["date"])].to_numpy(),
            index=pd.DatetimeIndex(market["date"]),
        )
        guided, events, cycle = guided_overlay(market, history, layer_momentum, layer)
        guided_parts.append(guided)
        guided_events.append(events)
        cycles.append(cycle)

    independent_all = pd.concat(independent_parts, ignore_index=True)
    guided_all = pd.concat(guided_parts, ignore_index=True)
    events_all = pd.concat(guided_events, ignore_index=True)

    frozen = pd.read_csv(V17_DAILY, parse_dates=["date"], low_memory=False)
    frozen = frozen[frozen["candidate"].eq(GRID_CANDIDATE)].copy()
    columns = (
        "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
        "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate",
        "overlay_cost_rate",
    )
    joined = frozen[["layer", "date", *columns]].merge(
        independent_all[["layer", "date", *columns]],
        on=["layer", "date"], suffixes=("_frozen", "_rebuilt"), validate="one_to_one",
    )
    parity: dict[str, float] = {}
    for column in columns:
        parity[column] = float((joined[f"{column}_frozen"] - joined[f"{column}_rebuilt"]).abs().max())
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"Independent grid parity failed: {parity}")

    def splice(frame: pd.DataFrame) -> pd.DataFrame:
        return pd.concat(
            [
                frame[frame["layer"].eq("model") & frame["date"].lt(REAL_START)],
                frame[frame["layer"].eq("real") & frame["date"].ge(REAL_START)],
            ],
            ignore_index=True,
        ).sort_values("date").reset_index(drop=True)

    checks = {
        "independent_grid_parity_max_abs": max(parity.values()),
        "independent_grid_parity_by_column": parity,
        "model_market": model_checks,
        "real_market": real_checks,
        "guided_cycles": cycles,
    }
    return splice(independent_all), splice(guided_all), events_all, checks


def add_strategy(frame: pd.DataFrame, name: str, overlay: pd.DataFrame | None) -> None:
    if overlay is None:
        for column in (
            "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
            "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate",
            "overlay_cost_rate",
        ):
            frame[f"{name}_{column}"] = 0.0
    else:
        columns = [
            "date", "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell",
            "overlay_gross_ret", "overlay_trade_cost_rate", "overlay_roll_cost_rate",
            "overlay_cost_rate",
        ]
        renamed = overlay[columns].rename(
            columns={column: f"{name}_{column}" for column in columns if column != "date"}
        )
        joined = frame[["date"]].merge(renamed, on="date", validate="one_to_one")
        if len(joined) != len(frame):
            raise RuntimeError(f"Grid/base calendar loss: {name}")
        for column in renamed.columns:
            if column != "date":
                frame[column] = joined[column].to_numpy()

    frame[f"{name}_total_im_units"] = (
        frame["total_im_units"] + frame[f"{name}_overlay_held_eod"]
    )
    combined = (
        frame["baseline_pre_cash_ret"]
        + frame["put_fixed_0p5_core_put_pnl_ret"]
        + frame["call_bare_only_call_pnl_ret"]
        + frame[f"{name}_overlay_gross_ret"]
    )
    frame[f"{name}_pre_cash_ret"] = (
        (1.0 + combined)
        * (1.0 - frame["put_fixed_0p5_core_put_cost_rate"])
        * (1.0 - frame["call_bare_only_call_cost_rate"])
        * (1.0 - frame[f"{name}_overlay_cost_rate"])
        - 1.0
    )
    frame[f"{name}_cash_weight_raw"] = (
        frame["blend_cash_weight"]
        - frame["put_fixed_0p5_core_put_mark_fraction"]
        - frame["call_bare_only_call_margin_fraction"]
        - MARGIN_RATE * frame[f"{name}_overlay_held_eod"]
    )
    if frame[f"{name}_cash_weight_raw"].lt(-1e-12).any():
        raise RuntimeError(f"Negative cash weight: {name}")
    frame[f"{name}_cash_weight"] = frame[f"{name}_cash_weight_raw"].clip(lower=0.0)
    frame[f"{name}_ret"] = (
        frame[f"{name}_pre_cash_ret"] + frame[f"{name}_cash_weight"] * CASH_DAILY
    )
    frame[f"{name}_nav"] = (1.0 + frame[f"{name}_ret"]).cumprod()
    frame[f"{name}_drawdown"] = frame[f"{name}_nav"] / frame[f"{name}_nav"].cummax() - 1.0


def build_daily(
    detail: pd.DataFrame,
    call: pd.DataFrame,
    independent: pd.DataFrame,
    guided: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    call_columns = [
        "date", "call_bare_only_call_pnl_ret", "call_bare_only_call_cost_rate",
        "call_bare_only_call_mark_fraction", "call_bare_only_call_margin_fraction",
        "call_bare_only_call_coverage", "call_bare_only_ret",
    ]
    frame = detail.merge(call[call_columns], on="date", validate="one_to_one")
    add_strategy(frame, "no_grid", None)
    no_grid_error = float((frame["no_grid_ret"] - frame["call_bare_only_ret"]).abs().max())
    if no_grid_error > 1e-12:
        raise RuntimeError(f"No-grid baseline parity failed: {no_grid_error}")
    add_strategy(frame, "grid_independent", independent)
    add_strategy(frame, "grid_momentum_guided", guided)

    lag_error = float(
        (frame["momentum_weight"].iloc[1:].to_numpy() - frame["desired_weight"].shift(1).iloc[1:].to_numpy()).max()
    )
    lag_abs_error = float(
        np.abs(frame["momentum_weight"].iloc[1:].to_numpy() - frame["desired_weight"].shift(1).iloc[1:].to_numpy()).max()
    )
    if lag_abs_error > 1e-14:
        raise RuntimeError(f"Momentum T+1 alignment failed: {lag_error}, {lag_abs_error}")
    for strategy in STRATEGIES:
        if frame[f"{strategy}_ret"].isna().any() or frame[f"{strategy}_ret"].le(-1.0).any():
            raise RuntimeError(f"Invalid return path: {strategy}")
    audit = {
        "rows": int(len(frame)),
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "no_grid_baseline_parity_max_abs": no_grid_error,
        "momentum_t_plus_1_alignment_max_abs": lag_abs_error,
        "min_cash_weight": {strategy: float(frame[f"{strategy}_cash_weight_raw"].min()) for strategy in STRATEGIES},
        "max_total_im_units": {strategy: float(frame[f"{strategy}_total_im_units"].max()) for strategy in STRATEGIES},
        "holding_days": {strategy: int(frame[f"{strategy}_overlay_held_eod"].sum()) for strategy in STRATEGIES},
        "grid_entries": {strategy: int(frame[f"{strategy}_overlay_buy"].sum()) for strategy in STRATEGIES},
        "grid_exits": {strategy: int(frame[f"{strategy}_overlay_sell"].sum()) for strategy in STRATEGIES},
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
            values = metrics(sample[f"{strategy}_ret"]) if available else {
                key: np.nan for key in (
                    "rows", "ann_return", "ann_vol", "max_dd", "calmar",
                    "sharpe_repo", "cumulative_return", "final_nav",
                )
            }
            rows.append(
                {
                    "strategy": strategy, "window": window, "available": available,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(), **values,
                }
            )
    return pd.DataFrame(rows)


def build_comparison(window: pd.DataFrame) -> pd.DataFrame:
    lookup = window.set_index(["strategy", "window"])
    rows: list[dict[str, Any]] = []
    pairs = (
        ("grid_independent", "no_grid"),
        ("grid_momentum_guided", "no_grid"),
        ("grid_momentum_guided", "grid_independent"),
    )
    for candidate, baseline in pairs:
        for name, _ in WINDOWS:
            item = lookup.loc[(candidate, name)]
            base = lookup.loc[(baseline, name)]
            rows.append(
                {
                    "strategy": candidate, "baseline": baseline, "window": name,
                    "delta_ann_return": float(item.ann_return - base.ann_return),
                    "delta_max_dd": float(item.max_dd - base.max_dd),
                    "delta_ann_vol": float(item.ann_vol - base.ann_vol),
                    "delta_final_nav": float(item.final_nav - base.final_nav),
                }
            )
    return pd.DataFrame(rows)


def pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{100.0 * float(value):.2f}%"


def write_record(
    frame: pd.DataFrame,
    window: pd.DataFrame,
    comparison: pd.DataFrame,
    events: pd.DataFrame,
    checks: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    table = ["|路径|全周期|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in STRATEGIES.items():
        block = window[window["strategy"].eq(strategy)].set_index("window")
        cells = [f"{pct(block.loc[name, 'ann_return'])} / {pct(block.loc[name, 'max_dd'])}" for name, _ in WINDOWS]
        table.append(f"|{label}|{'|'.join(cells)}|")

    versus = comparison[
        comparison["strategy"].eq("grid_momentum_guided")
        & comparison["baseline"].eq("grid_independent")
    ].set_index("window")
    delta_rows = ["|窗口|动量门控相对独立：年化变化|最大回撤变化|", "|---|---:|---:|"]
    for name, _ in WINDOWS:
        delta_rows.append(
            f"|{name}|{100*versus.loc[name, 'delta_ann_return']:+.2f}个百分点|"
            f"{100*versus.loc[name, 'delta_max_dd']:+.2f}个百分点|"
        )

    real_events = events[events["layer"].eq("real")]
    event_rows = ["|执行日|事件|原因|动量权重|估值分数|", "|---|---|---|---:|---:|"]
    for row in real_events.itertuples(index=False):
        value = "N/A" if pd.isna(row.signal_value) else f"{float(row.signal_value):.4f}"
        event_rows.append(
            f"|{pd.Timestamp(row.execution_date).date()}|{row.action}|{row.execution_reason}|"
            f"{float(row.momentum_weight):.1f}|{value}|"
        )

    text = f"""# IM 50/50 + Put + Call：网格独立/动量门控对比 v1

状态：研究完成；未批准实盘  
共同样本：{frame['date'].min().date().isoformat()} 至 {frame['date'].max().date().isoformat()}

## 当前规则答案

冻结 IM V2 网格是独立运行的：仅看估值 `<=0.85 / >=1.25`，不接受动量指导；新增1倍网格仓不加 Put 或 Call。

## 结果

每格为年化收益 / 最大回撤。三条路径的固定基线均为：裸滚50%加动态Put和Call；动量50%不加Put/Call。

{chr(10).join(table)}

## 动量门控相对独立网格

{chr(10).join(delta_rows)}

## 动量门控真实事件

{chr(10).join(event_rows)}

## 审计与限制

- 独立网格逐日复现冻结组件，最大误差 {checks['independent_grid_parity_max_abs']:.3e}；不加网格复现上一轮误差 {audit['no_grid_baseline_parity_max_abs']:.3e}。
- 动量目标与上一交易日信号错位误差 {audit['momentum_t_plus_1_alignment_max_abs']:.3e}，门控没有使用当日收盘后信息。
- 独立/动量门控持有日：{audit['holding_days']['grid_independent']} / {audit['holding_days']['grid_momentum_guided']}；完整开仓次数：{audit['grid_entries']['grid_independent']} / {audit['grid_entries']['grid_momentum_guided']}。
- 最低现金权重：独立 {audit['min_cash_weight']['grid_independent']:.4f}；动量门控 {audit['min_cash_weight']['grid_momentum_guided']:.4f}。最高总IM名义均为 {max(audit['max_total_im_units']['grid_independent'], audit['max_total_im_units']['grid_momentum_guided']):.1f} 倍。
- 网格历史信号高度集中在2024年的两轮极端低估修复，样本只有两次独立周期；不能把漂亮结果视为稳健性已确认。
- 2022-07-22起为真实IM，之前为理论/代理段；网格实际有效事件全部发生在真实期。结果不修改V2主线或实盘面。
"""
    (STAGING / "record.md").write_text(text, encoding="utf-8")


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def main() -> None:
    input_hashes = verify_inputs()
    STAGING.mkdir(parents=True)
    try:
        detail = pd.read_csv(BASE_DETAIL, parse_dates=["date"], low_memory=False).sort_values("date")
        call = pd.read_csv(BASE_CALL, parse_dates=["date"], low_memory=False).sort_values("date")
        independent, guided, events, checks = prepare_overlays(detail)
        daily, audit = build_daily(detail, call, independent, guided)
        window = build_metrics(daily)
        comparison = build_comparison(window)

        invalid_real_quotes = int(
            (
                events["layer"].eq("real")
                & events["action"].isin(["buy", "sell"])
                & (events["execution_open"].le(0) | events["execution_volume"].le(0))
            ).sum()
        )
        checks["invalid_real_execution_quotes"] = invalid_real_quotes
        checks["all_checks_passed"] = bool(
            checks["independent_grid_parity_max_abs"] <= 1e-12
            and audit["no_grid_baseline_parity_max_abs"] <= 1e-12
            and audit["momentum_t_plus_1_alignment_max_abs"] <= 1e-14
            and min(audit["min_cash_weight"].values()) >= -1e-12
            and invalid_real_quotes == 0
            and all(item["pending_order_end"] == 0 for item in checks["guided_cycles"])
        )
        if not checks["all_checks_passed"]:
            raise RuntimeError(f"Formal validation failed: {checks}, {audit}")

        keep = ["date", "phase", "momentum_weight", "desired_weight", "put_source"]
        for strategy in STRATEGIES:
            keep.extend(
                [
                    f"{strategy}_overlay_held_before", f"{strategy}_overlay_held_eod",
                    f"{strategy}_overlay_buy", f"{strategy}_overlay_sell",
                    f"{strategy}_overlay_gross_ret", f"{strategy}_overlay_cost_rate",
                    f"{strategy}_total_im_units", f"{strategy}_cash_weight",
                    f"{strategy}_ret", f"{strategy}_nav", f"{strategy}_drawdown",
                ]
            )
        daily[keep].to_csv(STAGING / "daily_nav.csv.gz", index=False, compression="gzip")
        window.to_csv(STAGING / "metrics_by_window.csv", index=False)
        comparison.to_csv(STAGING / "comparison.csv", index=False)
        events.to_csv(STAGING / "grid_trades.csv", index=False)
        validation = {
            "version": VERSION,
            "created_at": datetime.now().astimezone().isoformat(),
            "input_hashes": input_hashes,
            "checks": checks,
            "daily_audit": audit,
        }
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        write_record(daily, window, comparison, events, checks, audit)

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
