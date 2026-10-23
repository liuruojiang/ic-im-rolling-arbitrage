from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import im_mo_adaptive_valuation_mom120_floor_v12 as im_v12
import im_mo_adaptive_valuation_tier_put_v10 as im_v10
import im_put_four_valuation_tier_scan_v2 as im_four
import im_valuation_window_ladder_scan_v7 as valuation_v7


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_fullcycle_put_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
BASELINE = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_proxy_v1" / "daily_nav.csv.gz"
REAL_V2 = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
OUTPUT = ROOT / "outputs" / VERSION
REAL_START = pd.Timestamp("2022-07-22")
REAL_CANDIDATE = "IM_4tier_q750_850_900_925_mom4"
MODEL_LABEL = "IM_4tier_q750_850_900_925_mom4_model"
MODEL_SIDE_COST = 0.0001
CASH_ANNUAL = 0.03
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252.0) - 1.0
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)
STRATEGIES = {
    "no_put": "no_put_ret",
    "put_fixed_0p5_core": "put_fixed_0p5_core_ret",
    "put_scaled_total_exposure": "put_scaled_total_exposure_ret",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH, BASELINE, REAL_V2):
        if not path.exists():
            raise FileNotFoundError(path)
    expected = SPEC_HASH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256(SPEC)
    if actual != expected:
        raise RuntimeError(f"Specification hash mismatch: {actual} != {expected}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output already exists: {OUTPUT}")
    return {
        str(SPEC.relative_to(ROOT)): actual,
        str(BASELINE.relative_to(ROOT)): sha256(BASELINE),
        str(REAL_V2.relative_to(ROOT)): sha256(REAL_V2),
    }


def current_rule_state() -> tuple[pd.DataFrame, dict[str, object]]:
    daily_valuation, parity = im_v12.v4.build_daily_valuation()
    if max(parity.values()) > 1e-14:
        raise RuntimeError(f"Valuation feature parity failed: {parity}")
    legacy = im_v12.v6.signal_state(daily_valuation)[
        ["date", "tri_close_all", "momentum_120"]
    ].copy()
    if legacy["momentum_120"].dropna().eq(0.0).any():
        raise RuntimeError("MOM120 == 0 boundary exists; strict-negative rule needs review")

    stored = im_v10.load_v7_states()
    stored = stored[stored["candidate"].eq("dual_w57_q750_850_950")][
        ["date", "unbounded_median_knot", "absolute_tier"]
    ].rename(
        columns={
            "unbounded_median_knot": "valuation_score",
            "absolute_tier": "absolute_tier",
        }
    )
    state = legacy.merge(stored, on="date", how="left", validate="one_to_one")
    state["absolute_tier"] = state["absolute_tier"].fillna(0).astype(int)
    state["effective_month"] = state["date"].dt.to_period("M").dt.to_timestamp()

    monthly = valuation_v7.load_inputs()["monthly"].sort_values("date")
    quantiles = (0.750, 0.850, 0.900, 0.925)
    threshold_rows: list[dict[str, object]] = []
    for month in sorted(state["effective_month"].unique()):
        month = pd.Timestamp(month)
        sample = monthly[monthly["date"].lt(month)].tail(57)
        row: dict[str, object] = {
            "effective_month": month,
            "relative_calibrated": len(sample) == 57,
            "relative_sample_months": int(len(sample)),
            "relative_window_start": sample["date"].min() if len(sample) else pd.NaT,
            "relative_window_end": sample["date"].max() if len(sample) else pd.NaT,
        }
        if len(sample) == 57:
            values = sample["unbounded_median_knot"].astype(float).to_numpy()
            levels = np.quantile(values, quantiles, method="linear")
            if not np.all(np.diff(levels) > 0):
                raise RuntimeError(f"Non-increasing relative thresholds for {month.date()}")
            for number, value in enumerate(levels, start=1):
                row[f"threshold_{number}"] = float(value)
        else:
            for number in range(1, 5):
                row[f"threshold_{number}"] = np.nan
        threshold_rows.append(row)
    thresholds = pd.DataFrame(threshold_rows)
    state = state.merge(thresholds, on="effective_month", how="left", validate="many_to_one")

    calibrated = state["relative_calibrated"].fillna(False).astype(bool)
    score = state["valuation_score"].astype(float)
    relative = np.select(
        [
            calibrated & score.ge(state["threshold_4"]),
            calibrated & score.ge(state["threshold_3"]),
            calibrated & score.ge(state["threshold_2"]),
            calibrated & score.ge(state["threshold_1"]),
        ],
        [4, 3, 2, 1],
        default=0,
    ).astype(int)
    state["relative_tier"] = relative
    state["valuation_tier"] = np.maximum(
        state["absolute_tier"].to_numpy(dtype=int), relative
    )
    negative = state["momentum_120"].notna() & state["momentum_120"].lt(0.0)
    state["mom120_active"] = negative
    state["mom120_floor_qty"] = np.where(negative, 4, 0).astype(int)
    state["target_qty"] = np.maximum(
        state["valuation_tier"].to_numpy(dtype=int),
        state["mom120_floor_qty"].to_numpy(dtype=int),
    )
    if not state["target_qty"].between(0, 4).all():
        raise RuntimeError("Current-rule theoretical target outside 0..4")
    audit = {
        "state_start": state["date"].min().date().isoformat(),
        "state_end": state["date"].max().date().isoformat(),
        "first_relative_calibrated_date": (
            state.loc[calibrated, "date"].min().date().isoformat()
            if calibrated.any()
            else None
        ),
        "mom120_negative_days": int(negative.sum()),
        "mom120_floor_binding_days": int(
            (negative & state["valuation_tier"].lt(4)).sum()
        ),
        "relative_tier4_days": int(state["relative_tier"].eq(4).sum()),
        "absolute_tier3_days": int(state["absolute_tier"].eq(3).sum()),
        "target_counts": {
            str(int(key)): int(value)
            for key, value in state["target_qty"].value_counts().sort_index().items()
        },
    }
    return state, audit


def build_put_components() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    market, market_checks = im_v12.v6.model_market()
    state, state_audit = current_rule_state()
    model_dates = pd.DatetimeIndex(market["date"])
    schedule = im_v12.build_momentum_schedule(
        state,
        MODEL_LABEL,
        model_dates,
        "dual57_four_tier_abs_mom120_floor4",
    )
    model, model_trades, model_lives = im_v12.v8.run_model_normal_close(
        market, schedule, "3m", 0.95, MODEL_LABEL
    )
    model = model[model["date"].lt(REAL_START)].copy()
    if model.empty:
        raise RuntimeError("Empty theoretical Put component")
    last_model = model.index[-1]
    model.loc[last_model, "put_cost_rate"] += (
        float(model.loc[last_model, "put_fraction"]) * MODEL_SIDE_COST
    )
    model["put_source"] = "theoretical_csi1000_put"

    real = pd.read_csv(REAL_V2, parse_dates=["date"], low_memory=False)
    real = real[
        real["product"].eq("IM") & real["candidate"].eq(REAL_CANDIDATE)
    ][
        [
            "date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction",
            "put_fraction", "put_contract",
        ]
    ].sort_values("date")
    if real.empty or real["date"].min() != REAL_START:
        raise RuntimeError("Frozen real V2 Put component has unexpected coverage")
    real["put_source"] = "real_mo_frozen_v2"
    put = pd.concat([model, real], ignore_index=True, sort=False).sort_values("date")
    if put.duplicated("date").any():
        raise RuntimeError("Duplicate theoretical/real Put dates")
    required = ["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]
    if put[required].isna().any().any():
        raise RuntimeError("Missing Put component values")
    audit = {
        "market_checks": market_checks,
        "state": state_audit,
        "model_start": model["date"].min().date().isoformat(),
        "model_end": model["date"].max().date().isoformat(),
        "model_rows": int(len(model)),
        "real_start": real["date"].min().date().isoformat(),
        "real_end": real["date"].max().date().isoformat(),
        "real_rows": int(len(real)),
        "theoretical_transition_exit_cost": float(
            model.loc[last_model, "put_fraction"] * MODEL_SIDE_COST
        ),
        "raw_max_put_mark_fraction": float(put["put_mark_fraction"].max()),
        "raw_put_cost_total": float(put["put_cost_rate"].sum()),
        "model_trade_events": int(len(model_trades)),
        "model_lifecycles": int(len(model_lives)),
    }
    return put, schedule, state, audit


def add_strategy(frame: pd.DataFrame, label: str, scale: pd.Series | float) -> None:
    scale_values = pd.Series(scale, index=frame.index, dtype=float)
    frame[f"{label}_put_scale"] = scale_values
    frame[f"{label}_put_pnl_ret"] = scale_values * frame["put_pnl_ret"]
    frame[f"{label}_put_cost_rate"] = scale_values * frame["put_cost_rate"]
    frame[f"{label}_put_mark_fraction"] = scale_values * frame["put_mark_fraction"]
    frame[f"{label}_put_fraction"] = scale_values * frame["put_fraction"]
    frame[f"{label}_pre_cash_ret"] = (
        (1.0 + frame["baseline_pre_cash_ret"] + frame[f"{label}_put_pnl_ret"])
        * (1.0 - frame[f"{label}_put_cost_rate"])
        - 1.0
    )
    frame[f"{label}_cash_weight_raw"] = (
        frame["blend_cash_weight"] - frame[f"{label}_put_mark_fraction"]
    )
    if frame[f"{label}_cash_weight_raw"].lt(-1e-12).any():
        raise RuntimeError(f"Put capital exceeds cash buffer: {label}")
    frame[f"{label}_cash_weight"] = frame[f"{label}_cash_weight_raw"].clip(lower=0.0)
    frame[f"{label}_ret"] = (
        frame[f"{label}_pre_cash_ret"]
        + frame[f"{label}_cash_weight"] * CASH_DAILY
    )


def build_daily(put: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    baseline = pd.read_csv(BASELINE, parse_dates=["date"], low_memory=False)
    start, end = put["date"].min(), put["date"].max()
    baseline = baseline[baseline["date"].between(start, end)].copy()
    missing_base = sorted(set(put["date"]) - set(baseline["date"]))
    missing_put = sorted(set(baseline["date"]) - set(put["date"]))
    if missing_base:
        raise RuntimeError(f"Put dates missing from baseline: {missing_base[:3]}")
    if len(missing_put) > 5:
        raise RuntimeError(f"Excessive theoretical Put calendar gaps: {missing_put[:10]}")
    frame = baseline.merge(put, on="date", how="inner", validate="one_to_one")
    if len(frame) != len(put):
        raise RuntimeError("Unexpected Put/baseline inner-join loss")
    frame["baseline_pre_cash_ret"] = (
        frame["blend_ret"] - frame["blend_cash_weight"] * CASH_DAILY
    )
    frame["no_put_ret"] = frame["blend_ret"]
    add_strategy(frame, "put_fixed_0p5_core", 0.5)
    add_strategy(frame, "put_scaled_total_exposure", frame["total_im_units"])

    for strategy, column in STRATEGIES.items():
        frame[f"{strategy}_nav"] = (1.0 + frame[column]).cumprod()
        frame[f"{strategy}_drawdown"] = (
            frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0
        )
        if frame[column].le(-1.0).any() or frame[column].isna().any():
            raise RuntimeError(f"Invalid return path: {strategy}")

    real_source = pd.read_csv(REAL_V2, parse_dates=["date"], low_memory=False)
    real_source = real_source[
        real_source["product"].eq("IM") & real_source["candidate"].eq(REAL_CANDIDATE)
    ].sort_values("date")
    joined = frame[frame["put_source"].eq("real_mo_frozen_v2")].merge(
        real_source[
            ["date", "put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]
        ],
        on="date",
        suffixes=("_used", "_frozen"),
        validate="one_to_one",
    )
    parity = 0.0
    for column in ("put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"):
        parity = max(
            parity,
            float((joined[f"{column}_used"] - joined[f"{column}_frozen"]).abs().max()),
        )
    if parity > 1e-14:
        raise RuntimeError(f"Frozen real Put component parity failed: {parity}")
    audit = {
        "real_put_component_parity_max_abs": parity,
        "rows": int(len(frame)),
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "proxy_rows": int(frame["put_source"].eq("theoretical_csi1000_put").sum()),
        "real_rows": int(frame["put_source"].eq("real_mo_frozen_v2").sum()),
        "baseline_dates_dropped_for_missing_put": [
            pd.Timestamp(value).date().isoformat() for value in missing_put
        ],
        "max_total_im_units": float(frame["total_im_units"].max()),
        "min_total_im_units": float(frame["total_im_units"].min()),
        "fixed_core_min_cash": float(frame["put_fixed_0p5_core_cash_weight_raw"].min()),
        "total_scaled_min_cash": float(frame["put_scaled_total_exposure_cash_weight_raw"].min()),
        "fixed_core_max_put_mark": float(frame["put_fixed_0p5_core_put_mark_fraction"].max()),
        "total_scaled_max_put_mark": float(frame["put_scaled_total_exposure_put_mark_fraction"].max()),
    }
    return frame, audit


def metrics(returns: pd.Series) -> dict[str, float]:
    nav = (1.0 + returns.astype(float)).cumprod()
    ann_return = float(nav.iloc[-1] ** (252.0 / len(returns)) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(252.0))
    dd = nav / nav.cummax() - 1.0
    return {
        "rows": int(len(returns)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe_repo": ann_return / ann_vol if ann_vol > 1e-12 else 0.0,
        "max_dd": float(dd.min()),
        "final_nav": float(nav.iloc[-1]),
    }


def build_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    end = frame["date"].max()
    rows: list[dict[str, object]] = []
    for strategy, column in STRATEGIES.items():
        for window, offset in WINDOWS:
            requested = None if offset is None else end - offset
            sample = frame if requested is None else frame[frame["date"].ge(requested)]
            available = bool(
                requested is None
                or sample["date"].min() <= requested + pd.Timedelta(days=7)
            )
            values = metrics(sample[column]) if available else {
                key: np.nan
                for key in ("rows", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "final_nav")
            }
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": available,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "proxy_rows": int(sample["put_source"].eq("theoretical_csi1000_put").sum()),
                    "real_rows": int(sample["put_source"].eq("real_mo_frozen_v2").sum()),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def build_annual(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy, column in STRATEGIES.items():
            rows.append(
                {
                    "year": int(year),
                    "strategy": strategy,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "phase": (
                        "proxy" if sample["put_source"].eq("theoretical_csi1000_put").all()
                        else "real" if sample["put_source"].eq("real_mo_frozen_v2").all()
                        else "mixed"
                    ),
                    **metrics(sample[column]),
                }
            )
    return pd.DataFrame(rows)


def build_phase_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    periods = {
        "theoretical_put_proxy": frame["put_source"].eq("theoretical_csi1000_put"),
        "real_im_mo": frame["put_source"].eq("real_mo_frozen_v2"),
        "2015_available": frame["date"].dt.year.eq(2015),
    }
    rows: list[dict[str, object]] = []
    for phase, mask in periods.items():
        sample = frame[mask]
        for strategy, column in STRATEGIES.items():
            rows.append(
                {
                    "phase": phase,
                    "strategy": strategy,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metrics(sample[column]),
                }
            )
    return pd.DataFrame(rows)


def drawdown_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
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


def pct(value: object) -> str:
    return "N/A" if pd.isna(value) else f"{100.0 * float(value):.2f}%"


def write_record(
    frame: pd.DataFrame,
    window: pd.DataFrame,
    phase: pd.DataFrame,
    dd: pd.DataFrame,
    put_audit: dict[str, object],
    daily_audit: dict[str, object],
) -> None:
    labels = {
        "no_put": "不加Put",
        "put_fixed_0p5_core": "Put仅保护固定0.5倍核心",
        "put_scaled_total_exposure": "Put按总IM敞口同比例",
    }
    table = ["|路径|全样本|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in labels.items():
        block = window[window["strategy"].eq(strategy)].set_index("window")
        cells = [
            f"{pct(block.loc[name, 'ann_return'])} / {pct(block.loc[name, 'max_dd'])}"
            for name, _ in WINDOWS
        ]
        table.append(f"|{label}|{'|'.join(cells)}|")

    delta_rows = ["|Put路径|全样本年化变化|全样本回撤改善|", "|---|---:|---:|"]
    base = window[(window["strategy"].eq("no_put")) & (window["window"].eq("full"))].iloc[0]
    for strategy in ("put_fixed_0p5_core", "put_scaled_total_exposure"):
        row = window[(window["strategy"].eq(strategy)) & (window["window"].eq("full"))].iloc[0]
        delta_rows.append(
            f"|{labels[strategy]}|{(row.ann_return-base.ann_return)*100:+.2f}个百分点|"
            f"{(row.max_dd-base.max_dd)*100:+.2f}个百分点|"
        )

    dd_rows = ["|路径|峰值日|谷底日|最大回撤|期末已修复|", "|---|---:|---:|---:|---:|"]
    for row in dd.itertuples(index=False):
        dd_rows.append(
            f"|{labels[row.strategy]}|{row.peak}|{row.trough}|{pct(row.max_dd)}|"
            f"{'是' if row.recovered_by_end else '否'}|"
        )

    phase_pivot = phase[phase["phase"].isin(["theoretical_put_proxy", "real_im_mo"])].copy()
    phase_rows = ["|分层|路径|年化收益|最大回撤|", "|---|---|---:|---:|"]
    phase_labels = {"theoretical_put_proxy": "理论Put/代理IM", "real_im_mo": "真实IM/MO"}
    for phase_name in ("theoretical_put_proxy", "real_im_mo"):
        for strategy in STRATEGIES:
            row = phase_pivot[
                phase_pivot["phase"].eq(phase_name) & phase_pivot["strategy"].eq(strategy)
            ].iloc[0]
            phase_rows.append(
                f"|{phase_labels[phase_name]}|{labels[strategy]}|"
                f"{pct(row.ann_return)}|{pct(row.max_dd)}|"
            )

    text = f"""# 50%滚IM + 50%动量门控：加入Put v1

状态：研究完成；未批准实盘  
共同数据截止：{frame['date'].max().date().isoformat()}

## 结果

每格为年化收益 / 最大回撤。全样本与10年含理论Put和上市前平均贴水代理；近3年、1年为真实IM/MO期。

{chr(10).join(table)}

{chr(10).join(delta_rows)}

### 理论层与真实层拆分

{chr(10).join(phase_rows)}

全样本Put回撤反而更深，原因集中在理论层的2015年：`MOM120 < 0`直到2015-09-01才触发，Put在9月2日才建仓，错过6月至8月主要下跌；随后高波动状态下买入的理论Put在反弹中损耗。真实IM/MO期则不同，固定0.5倍核心保护同时提高收益并压低回撤。

## 最大回撤区间

{chr(10).join(dd_rows)}

## 固定规则

- 底仓：50%单纯滚IM + 50%动量门控滚IM；`MA35 / Mom18 / W2.5`；Abs20为50% OFF + 50%大于0。
- Put：当前IM V2的绝对估值三档 + 57个月相对估值四档 + `MOM120 < 0`最低4张；约3个月、95%行权价、T+1收盘。
- 主比较`put_fixed_0p5_core`只保护持续存在的0.5倍核心滚IM袖；总敞口路径是分数张数归一化研究对照。
- Call与网格关闭；Put市值从现金扣除；底仓期货成本及Put费用均已计入。

## 数据边界

- 理论Put：{put_audit['model_start']}至{put_audit['model_end']}，{put_audit['model_rows']}日。
- 真实MO冻结组件：{put_audit['real_start']}至{put_audit['real_end']}，{put_audit['real_rows']}日。
- 57个月相对估值轴首次可校准日：{put_audit['state']['first_relative_calibrated_date']}；此前相对轴为0，但绝对估值与MOM120轴仍运行。
- 冻结真实Put组件复现最大误差：{daily_audit['real_put_component_parity_max_abs']:.3e}。
- 主路径最大Put市值占净资产{daily_audit['fixed_core_max_put_mark']:.2%}，最低剩余现金{daily_audit['fixed_core_min_cash']:.2%}；总敞口路径分别为{daily_audit['total_scaled_max_put_mark']:.2%}和{daily_audit['total_scaled_min_cash']:.2%}。

## 限制

- 上市前IM贴水是用上市后平均值回填，Put是理论定价；两者均不可视为2015年真实可成交记录。
- 理论Put起点为2015-04-16，因此不把2014-10-17至2015-04-15无期权模型的底仓段混入正式Put对比。
- 真实Put冻结数据比底仓少一周，所以所有路径共同截到2026-08-14。
- 未计盘口价差、冲击、收盘容量、动态保证金、涨跌停/价格限制及整数合约误差。

## Decision

`research_comparison_only_not_live_approved`：本版只回答在固定底仓上加入当前Put规则后的收益/回撤变化，不改动V2生产主线。
"""
    (OUTPUT / "record.md").write_text(text, encoding="utf-8")


def run() -> None:
    hashes = verify_inputs()
    put, schedule, state, put_audit = build_put_components()
    frame, daily_audit = build_daily(put)
    window = build_metrics(frame)
    annual = build_annual(frame)
    phase = build_phase_metrics(frame)
    dd = drawdown_table(frame)

    OUTPUT.mkdir(parents=True, exist_ok=False)
    frame.to_csv(OUTPUT / "daily_nav.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    window.to_csv(OUTPUT / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    phase.to_csv(OUTPUT / "phase_metrics.csv", index=False, encoding="utf-8-sig")
    dd.to_csv(OUTPUT / "drawdown_episodes.csv", index=False, encoding="utf-8-sig")
    schedule.to_csv(OUTPUT / "theoretical_put_schedule.csv.gz", index=False, compression="gzip")
    state.to_csv(OUTPUT / "put_signal_state.csv.gz", index=False, compression="gzip")
    (OUTPUT / "diagnostics.json").write_text(
        json.dumps({"put": put_audit, "daily": daily_audit}, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "proxy_and_real_research_only_not_approved_for_live",
        "command": f"{Path(sys.executable).name} {Path(__file__).name}",
        "script_sha256": sha256(Path(__file__)),
        "input_hashes": hashes,
        "sample": {
            "start": daily_audit["start"],
            "end": daily_audit["end"],
            "rows": daily_audit["rows"],
            "proxy_rows": daily_audit["proxy_rows"],
            "real_rows": daily_audit["real_rows"],
            "timezone": "Asia/Shanghai",
        },
        "put_rule": {
            "absolute_thresholds": [2.45, 2.50, 2.60],
            "relative_window_months": 57,
            "relative_quantiles": [0.75, 0.85, 0.90, 0.925],
            "mom120_floor": 4,
            "mom120_boundary": "strictly_less_than_zero",
            "tenor": "3m",
            "moneyness": 0.95,
            "execution": "T signal / T+1 close",
        },
        "capital": {
            "futures_margin_buffer_per_unit": 0.30,
            "cash_annual": CASH_ANNUAL,
            "fixed_core_put_scale": 0.5,
            "total_exposure_put_scale": "daily total_im_units 0.5/0.75/1.0",
        },
        "exclusions": ["call", "grid", "bid_ask", "impact", "dynamic_margin", "integer_contract_rounding"],
        "diagnostics": {"put": put_audit, "daily": daily_audit},
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "command_log.txt").write_text(manifest["command"] + "\n", encoding="utf-8")
    write_record(frame, window, phase, dd, put_audit, daily_audit)


if __name__ == "__main__":
    run()
