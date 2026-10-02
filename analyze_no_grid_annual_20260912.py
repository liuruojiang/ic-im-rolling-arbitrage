from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "annual_no_grid_model_real_20260912"
REFRESH = ROOT / "outputs" / "nav_r7_complete_refresh_20260911"

IC_FORMAL = (
    ROOT
    / "quant_param_scan_runs"
    / "20260904_ic_v13_full_roll_tenor_timing_v2"
    / "candidate_checkpoints"
    / "quarter_T3_fixed.csv.gz"
)
IC_TARGET = ROOT / "outputs" / "ic_mainline_v1_3" / "target_schedule.csv.gz"
IC_FULL = REFRESH / "ic_full_daily.csv.gz"
IC_TAIL = REFRESH / "ic_tail_daily.csv"

IM_FORMAL = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz"
IM_COMPONENTS = (
    ROOT
    / "quant_param_scan_runs"
    / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_put_coverage_scope"
    / "daily_outputs"
    / "coverage_candidates.csv.gz"
)
IM_PARENT = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_im_grid160_put_carry_scan_v23"
    / "daily_outputs"
    / "daily_candidates.csv.gz"
)
IM_FULL = REFRESH / "im_full_daily.csv.gz"
IM_TAIL = REFRESH / "im_tail_daily.csv"

HISTORICAL_SIGNALS = REFRESH / "historical_signals.json"

START_DATE = pd.Timestamp("2015-04-16")
MARGIN_RATE = 0.30
CASH_DAILY = (1.0 + 0.03) ** (1.0 / 252.0) - 1.0


def read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_signal_grid_state() -> dict[tuple[str, pd.Timestamp], float]:
    with HISTORICAL_SIGNALS.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    result: dict[tuple[str, pd.Timestamp], float] = {}
    for date_text, day in raw.items():
        date = pd.Timestamp(date_text)
        for product in ("IC", "IM"):
            signal = day.get(product, {}) or {}
            value = signal.get("grid_current", signal.get("grid_held_eod", signal.get("grid_target", 0.0)))
            result[(product, date)] = float(value or 0.0)
    return result


def prepare_ic() -> tuple[pd.DataFrame, dict[str, object]]:
    formal = read_csv(IC_FORMAL)
    target = read_csv(IC_TARGET)[["date", "grid_held_eod", "grid_ic_units", "total_ic_units"]]
    formal = formal.merge(target, on="date", how="left", validate="one_to_one")
    if formal["grid_held_eod"].isna().any():
        raise RuntimeError("IC formal replay has missing grid state")
    if (formal["total_units"] - formal["total_ic_units"]).abs().max() > 1e-12:
        raise RuntimeError("IC formal total-unit parity failed")

    tail = read_csv(IC_TAIL)
    signals = load_signal_grid_state()
    tail["grid_units"] = [signals.get(("IC", date), 0.0) for date in tail["date"]]
    tail["grid_net_increment"] = 0.0

    formal_out = formal[
        [
            "date",
            "ret",
            "cash_weight",
            "total_units",
            "data_layer",
            "futures_gross_ret",
            "grid_net_increment",
            "grid_held_eod",
        ]
    ].rename(columns={"grid_held_eod": "grid_units"})
    tail_out = tail[
        [
            "date",
            "ret",
            "cash_weight",
            "total_units",
            "data_layer",
            "futures_gross_ret",
            "grid_net_increment",
            "grid_units",
        ]
    ]
    current = pd.concat([formal_out, tail_out], ignore_index=True).sort_values("date")
    if current["date"].duplicated().any():
        raise RuntimeError("IC formal/tail dates overlap")

    current["base_ret_without_grid"] = (
        current["ret"]
        - current["grid_net_increment"]
        - current["cash_weight"] * CASH_DAILY
    )
    current["no_grid_cash_weight"] = current["cash_weight"] + MARGIN_RATE * current["grid_units"]
    current["no_grid_ret"] = (
        current["base_ret_without_grid"]
        + current["no_grid_cash_weight"] * CASH_DAILY
    )
    current["product"] = "IC"

    official = read_csv(IC_FULL)[["date", "ret"]].rename(columns={"ret": "official_ret"})
    check = current.merge(official, on="date", how="outer", validate="one_to_one")
    parity = float((check["ret"] - check["official_ret"]).abs().max())
    if parity > 1e-12:
        raise RuntimeError(f"IC current curve parity failed: {parity}")

    return current, {
        "start": current["date"].min().date().isoformat(),
        "end": current["date"].max().date().isoformat(),
        "rows": int(len(current)),
        "real_start": current.loc[current["data_layer"].astype(str).str.startswith("real"), "date"].min().date().isoformat(),
        "current_curve_max_abs_error": parity,
    }


def prepare_im() -> tuple[pd.DataFrame, dict[str, object]]:
    formal = read_csv(IM_FORMAL)
    components = read_csv(IM_COMPONENTS)
    components = components[components["candidate"].eq("core_only_current")].copy()
    component_cols = [
        "date",
        "ret",
        "cash_weight",
        "total_units",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "call_pnl_ret",
        "call_cost_rate",
        "futures_gross_ret",
        "futures_cost_rate",
        "grid_units",
        "data_layer",
    ]
    components = components[component_cols]
    formal_check = formal[["date", "ret"]].merge(
        components[["date", "ret"]].rename(columns={"ret": "component_ret"}),
        on="date",
        how="inner",
        validate="one_to_one",
    )
    component_parity = float((formal_check["ret"] - formal_check["component_ret"]).abs().max())
    if component_parity > 1e-12:
        raise RuntimeError(f"IM formal/component parity failed: {component_parity}")

    parent = read_csv(IM_PARENT)
    model_dates = set(components.loc[components["data_layer"].eq("model"), "date"])
    real_dates = set(components.loc[components["data_layer"].astype(str).str.startswith("real"), "date"])
    parent = parent[
        (
            parent["candidate"].eq("model_avg_basis__current_4tier_mom3")
            & parent["date"].isin(model_dates)
        )
        | (
            parent["candidate"].eq("real_actual_basis__current_4tier_mom3")
            & parent["date"].isin(real_dates)
        )
    ][
        [
            "date",
            "overlay_gross_ret",
            "overlay_basis_ret",
            "overlay_cost_rate",
            "overlay_held_eod",
        ]
    ].copy()
    parent["grid_gross_component"] = parent["overlay_gross_ret"].astype(float) + parent["overlay_basis_ret"].astype(float)
    parent = parent.rename(columns={"overlay_held_eod": "parent_grid_units"})
    current = components.merge(parent, on="date", how="left", validate="one_to_one")
    current[["grid_gross_component", "overlay_cost_rate", "parent_grid_units"]] = current[
        ["grid_gross_component", "overlay_cost_rate", "parent_grid_units"]
    ].fillna(0.0)
    if (current["grid_units"] - current["parent_grid_units"]).abs().max() > 1e-12:
        raise RuntimeError("IM grid state parity with parent component failed")

    tail = read_csv(IM_TAIL)
    signals = load_signal_grid_state()
    tail["grid_units"] = [signals.get(("IM", date), 0.0) for date in tail["date"]]
    tail["grid_gross_component"] = 0.0
    tail["overlay_cost_rate"] = 0.0
    tail["parent_grid_units"] = tail["grid_units"]
    tail = tail[
        [
            "date",
            "ret",
            "cash_weight",
            "total_units",
            "put_pnl_ret",
            "put_cost_rate",
            "put_mark_fraction",
            "call_pnl_ret",
            "call_cost_rate",
            "futures_gross_ret",
            "futures_cost_rate",
            "grid_units",
            "data_layer",
            "grid_gross_component",
            "overlay_cost_rate",
            "parent_grid_units",
        ]
    ]
    current = pd.concat([current, tail], ignore_index=True).sort_values("date")
    if current["date"].duplicated().any():
        raise RuntimeError("IM formal/tail dates overlap")

    current["no_grid_total_units"] = current["total_units"] - current["grid_units"]
    current["no_grid_cash_weight"] = current["cash_weight"] + MARGIN_RATE * current["grid_units"]
    current["no_grid_futures_gross"] = current["futures_gross_ret"] - current["grid_gross_component"]
    current["no_grid_futures_cost"] = current["futures_cost_rate"] - current["overlay_cost_rate"]
    pre_cash = (
        (1.0 + current["no_grid_futures_gross"] + current["put_pnl_ret"] + current["call_pnl_ret"])
        * (1.0 - current["no_grid_futures_cost"])
        * (1.0 - current["put_cost_rate"])
        * (1.0 - current["call_cost_rate"])
        - 1.0
    )
    current["no_grid_ret"] = pre_cash + current["no_grid_cash_weight"] * CASH_DAILY
    current["product"] = "IM"

    official = read_csv(IM_FULL)[["date", "ret"]].rename(columns={"ret": "official_ret"})
    check = current.merge(official, on="date", how="outer", validate="one_to_one")
    parity = float((check["ret"] - check["official_ret"]).abs().max())
    if parity > 1e-12:
        raise RuntimeError(f"IM current curve parity failed: {parity}")

    return current, {
        "start": current["date"].min().date().isoformat(),
        "end": current["date"].max().date().isoformat(),
        "rows": int(len(current)),
        "real_start": current.loc[current["data_layer"].astype(str).str.startswith("real"), "date"].min().date().isoformat(),
        "current_curve_max_abs_error": parity,
        "formal_component_max_abs_error": component_parity,
    }


def compound(values: pd.Series) -> float | None:
    if values.empty:
        return None
    return float((1.0 + values.astype(float)).prod() - 1.0)


def build_annual_table(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    daily = daily.copy()
    daily["year"] = daily["date"].dt.year
    daily["is_real"] = daily["data_layer"].astype(str).str.startswith("real")
    for (product, year), group in daily.groupby(["product", "year"], sort=True):
        model = group[~group["is_real"]]
        real = group[group["is_real"]]
        model_return = compound(model["no_grid_ret"])
        real_return = compound(real["no_grid_ret"])
        annual_return = compound(group["no_grid_ret"])
        if len(model) and len(real):
            regime = "模拟+真实"
        elif len(model):
            regime = "模拟"
        else:
            regime = "真实"
        rows.append(
            {
                "product": product,
                "year": int(year),
                "start": group["date"].min().date().isoformat(),
                "end": group["date"].max().date().isoformat(),
                "rows": int(len(group)),
                "model_rows": int(len(model)),
                "real_rows": int(len(real)),
                "data_regime": regime,
                "model_return": model_return,
                "real_return": real_return,
                "annual_return_no_grid": annual_return,
            }
        )
    return pd.DataFrame(rows)


def build_report(annual: pd.DataFrame, audits: dict[str, dict[str, object]]) -> str:
    lines = [
        "# 去掉网格后的年度收益统计",
        "",
        "- 策略路径：v1.3-r7 研究回放；无网格仅用于研究，不改写冻结主线、不生成交易指令。",
        "- 起点：2015-04-16，按IM上市后的共同可用交易日开始。2015年为不完整年度，2026年为截至2026-09-10的年内数据。",
        "- 计算：每日净收益复合为年度收益；2022年同时展示模拟段和真实段，全年收益按两段实际顺序连续复合。",
        "- 网格去除：网格收益和网格成本移除，释放的每1倍网格30%保证金按年化3%现金收益计入；核心期货、动量、Put、Call、非网格成本和执行日历保持不变。",
        "",
    ]
    for product in ("IC", "IM"):
        lines.extend([f"## {product}", "", "| 年份 | 数据段 | 模拟收益 | 真实收益 | 无网格全年收益 | 天数 |", "|---:|---|---:|---:|---:|---:|"])
        block = annual[annual["product"].eq(product)]
        for _, row in block.iterrows():
            pct = lambda value: "N/A" if pd.isna(value) else f"{value:.2%}"
            lines.append(
                f"| {int(row['year'])} | {row['data_regime']} | {pct(row['model_return'])} | {pct(row['real_return'])} | {pct(row['annual_return_no_grid'])} | {int(row['rows'])} |"
            )
        lines.append("")
    lines.extend(
        [
            "## 验证",
            "",
            f"- IC当前曲线逐日Parity最大收益误差：{audits['IC']['current_curve_max_abs_error']:.3e}。",
            f"- IM当前曲线逐日Parity最大收益误差：{audits['IM']['current_curve_max_abs_error']:.3e}。",
            f"- IM正式组件与正式输出最大收益误差：{audits['IM']['formal_component_max_abs_error']:.3e}。",
            "- 已验证完整数据截止2026-09-10；2026-09-11不在当前完整行情续接和历史信号中，因此不补写。",
            "",
            "## 口径边界",
            "",
            "- 2015年至真实起点之间的收益是模拟数据延伸，不应与真实数据期视为同等证据。",
            "- 这份年度表是研究审计结果，不是当前持仓、最新信号或下单建议。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ic, ic_audit = prepare_ic()
    im, im_audit = prepare_im()
    daily = pd.concat([ic, im], ignore_index=True).sort_values(["product", "date"])
    if daily["date"].min() != START_DATE:
        raise RuntimeError(f"common start date mismatch: {daily['date'].min().date()}")
    if daily["no_grid_ret"].le(-1.0).any() or not np.isfinite(daily["no_grid_ret"]).all():
        raise RuntimeError("no-grid return contains invalid values")

    annual = build_annual_table(daily)
    daily_out = daily[
        [
            "product",
            "date",
            "data_layer",
            "ret",
            "grid_units",
            "cash_weight",
            "no_grid_cash_weight",
            "no_grid_ret",
        ]
    ]
    daily_out.to_csv(OUT / "daily_no_grid.csv.gz", index=False, compression="gzip")
    annual.to_csv(OUT / "annual_no_grid_returns.csv", index=False)
    (OUT / "annual_no_grid_report.md").write_text(
        build_report(annual, {"IC": ic_audit, "IM": im_audit}), encoding="utf-8"
    )

    sources = {
        "ic_formal": IC_FORMAL,
        "ic_target": IC_TARGET,
        "ic_full": IC_FULL,
        "ic_tail": IC_TAIL,
        "im_formal": IM_FORMAL,
        "im_components": IM_COMPONENTS,
        "im_parent": IM_PARENT,
        "im_full": IM_FULL,
        "im_tail": IM_TAIL,
        "historical_signals": HISTORICAL_SIGNALS,
    }
    metadata = {
        "created_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "strategy_path": "v1.3-r7 research replay; no-grid ablation",
        "start": START_DATE.date().isoformat(),
        "end": daily["date"].max().date().isoformat(),
        "latest_complete_trading_day": "2026-09-10",
        "real_starts": {"IC": ic_audit["real_start"], "IM": im_audit["real_start"]},
        "assumptions": {
            "grid_scale": 0.0,
            "margin_rate_per_grid_unit": MARGIN_RATE,
            "cash_annual": 0.03,
            "cash_daily": CASH_DAILY,
            "calendar": "existing v1.3-r7 formal/tail trading dates",
            "model_extension": "formal model layer from 2015-04-16 to each sleeve real-data start",
        },
        "audits": {"IC": ic_audit, "IM": im_audit},
        "source_sha256": {name: sha256_file(path) for name, path in sources.items()},
        "outputs": {
            "annual": "annual_no_grid_returns.csv",
            "daily": "daily_no_grid.csv.gz",
            "report": "annual_no_grid_report.md",
        },
        "research_only": True,
    }
    (OUT / "analysis.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"audits": {"IC": ic_audit, "IM": im_audit}}, ensure_ascii=False, indent=2))
    print(annual.to_string(index=False))


if __name__ == "__main__":
    main()
