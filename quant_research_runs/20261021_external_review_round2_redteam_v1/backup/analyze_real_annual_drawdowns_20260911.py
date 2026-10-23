"""Research-only annual returns and maximum-drawdown attribution for the refreshed IC/IM path."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
REFRESH = ROOT / "outputs" / "nav_r7_complete_refresh_20260911"
OUTPUT = ROOT / "outputs" / "annual_real_performance_20260911"
END = pd.Timestamp("2026-09-10")
CASH_DAILY = 0.00011730371383444904

IC_FORMAL = ROOT / "quant_param_scan_runs" / "20260904_ic_v13_full_roll_tenor_timing_v2" / "candidate_checkpoints" / "quarter_T3_fixed.csv.gz"
IM_FORMAL = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz"
IM_COMPONENTS = ROOT / "quant_param_scan_runs" / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_put_coverage_scope" / "daily_outputs" / "coverage_candidates.csv.gz"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="infer", parse_dates=["date"])


def append_tail(formal: pd.DataFrame, tail_name: str) -> pd.DataFrame:
    tail = read_csv(REFRESH / tail_name)
    result = pd.concat([formal, tail], ignore_index=True)
    return result.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def load_return_stream(product: str) -> pd.DataFrame:
    if product == "IC":
        return append_tail(read_csv(IC_FORMAL), "ic_tail_daily.csv")
    if product == "IM":
        return append_tail(read_csv(IM_FORMAL), "im_tail_daily.csv")
    raise ValueError(product)


def load_component_stream(product: str) -> pd.DataFrame:
    if product == "IC":
        formal = read_csv(IC_FORMAL)
        return append_tail(formal, "ic_tail_daily.csv")
    if product == "IM":
        all_candidates = read_csv(IM_COMPONENTS)
        formal = all_candidates[all_candidates["candidate"].eq("core_only_current")].copy()
        return append_tail(formal, "im_tail_daily.csv")
    raise ValueError(product)


def real_start(frame: pd.DataFrame, product: str) -> pd.Timestamp:
    if product == "IM":
        return pd.Timestamp("2022-07-22")
    real = frame.loc[frame["data_layer"].astype(str).eq("real"), "date"]
    if real.empty:
        raise RuntimeError("IC real data layer start is missing")
    return pd.Timestamp(real.iloc[0])


def add_real_nav(frame: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    result = frame.loc[frame["date"].ge(start) & frame["date"].le(END)].copy()
    result = result.sort_values("date").reset_index(drop=True)
    result["nav_real"] = (1.0 + result["ret"].astype(float)).cumprod()
    result["peak_nav_real"] = result["nav_real"].cummax()
    result["drawdown_real"] = result["nav_real"] / result["peak_nav_real"] - 1.0
    return result


def annual_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, group in frame.groupby(frame["date"].dt.year, sort=True):
        group = group.reset_index(drop=True)
        year_start = pd.Timestamp(f"{year}-01-01")
        previous = frame.loc[frame["date"].lt(year_start), "nav_real"]
        prior_nav = float(previous.iloc[-1]) if not previous.empty else 1.0
        path = pd.concat(
            [pd.Series([prior_nav]), prior_nav * (1.0 + group["ret"]).cumprod()],
            ignore_index=True,
        )
        dd = path / path.cummax() - 1.0
        trough_pos = int(dd.iloc[1:].argmin()) + 1
        peak_pos = int(path.iloc[: trough_pos + 1].argmax())
        peak_date = (
            str(group.loc[peak_pos - 1, "date"].date())
            if peak_pos > 0
            else str((year_start - pd.Timedelta(days=1)).date())
        )
        rows.append(
            {
                "year": int(year),
                "period": (
                    "YTD"
                    if year == int(END.year)
                    else ("partial_first_real_year" if group["date"].iloc[0].year == year and group["date"].iloc[0].month != 1 else "full_year")
                ),
                "start": str(group["date"].iloc[0].date()),
                "end": str(group["date"].iloc[-1].date()),
                "rows": int(len(group)),
                "return": float((1.0 + group["ret"]).prod() - 1.0),
                "max_drawdown": float(dd.iloc[1:].min()),
                "drawdown_peak": peak_date,
                "drawdown_trough": str(group.loc[trough_pos - 1, "date"].date()),
            }
        )
    return pd.DataFrame(rows)


def overall_drawdown(frame: pd.DataFrame) -> dict[str, object]:
    trough_idx = int(frame["drawdown_real"].idxmin())
    peak_idx = int(frame.loc[:trough_idx, "nav_real"].idxmax())
    peak_nav = float(frame.loc[peak_idx, "nav_real"])
    recovery = frame.loc[(frame.index > trough_idx) & frame["nav_real"].ge(peak_nav), "date"]
    return {
        "peak_date": str(frame.loc[peak_idx, "date"].date()),
        "trough_date": str(frame.loc[trough_idx, "date"].date()),
        "max_drawdown": float(frame.loc[trough_idx, "drawdown_real"]),
        "peak_nav": peak_nav,
        "trough_nav": float(frame.loc[trough_idx, "nav_real"]),
        "recovery_date": str(recovery.iloc[0].date()) if not recovery.empty else None,
        "drawdown_rows": int(trough_idx - peak_idx),
    }


def compound(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return float((1.0 + values).prod() - 1.0)


def cost_factor(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    return float((1.0 - values).prod() - 1.0)


def attribution(product: str, returns: pd.DataFrame, components: pd.DataFrame, start: pd.Timestamp) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    returns = add_real_nav(returns, start)
    dd = overall_drawdown(returns)
    peak = pd.Timestamp(dd["peak_date"])
    trough = pd.Timestamp(dd["trough_date"])
    components = components.loc[components["date"].ge(start) & components["date"].le(END)].copy()
    components = components.drop_duplicates("date", keep="last").sort_values("date")
    window = components.loc[components["date"].gt(peak) & components["date"].le(trough)].copy()
    if window.empty:
        raise RuntimeError(f"empty maximum drawdown window for {product}")
    window["cash_carry_ret"] = window["cash_weight"].astype(float) * CASH_DAILY
    row: dict[str, object] = {
        "product": product,
        "real_start": str(start.date()),
        "peak_date": str(peak.date()),
        "trough_date": str(trough.date()),
        "recovery_date": dd["recovery_date"],
        "max_drawdown": dd["max_drawdown"],
        "window_start_after_peak": str(window["date"].iloc[0].date()),
        "window_days": int(len(window)),
        "window_net_return": compound(window, "ret"),
        "futures_gross_compound": compound(window, "futures_gross_ret"),
        "put_pnl_compound": compound(window, "put_pnl_ret"),
        "call_pnl_compound": compound(window, "call_pnl_ret"),
        "cash_carry_compound": compound(window, "cash_carry_ret"),
        "futures_cost_factor": cost_factor(window, "futures_cost_rate"),
        "put_cost_factor": cost_factor(window, "put_cost_rate"),
        "call_cost_factor": cost_factor(window, "call_cost_rate"),
        "simple_return_sum": float(window["ret"].sum()),
        "simple_futures_gross_sum": float(window["futures_gross_ret"].sum()),
        "simple_put_pnl_sum": float(window["put_pnl_ret"].sum()),
        "simple_call_pnl_sum": float(window.get("call_pnl_ret", pd.Series(0.0, index=window.index)).sum()),
        "simple_cash_carry_sum": float(window["cash_carry_ret"].sum()),
        "avg_total_units": float(window["total_units"].mean()),
        "max_total_units": float(window["total_units"].max()),
    }
    monthly = (
        window.assign(month=window["date"].dt.to_period("M").astype(str))
        .groupby("month", as_index=False)
        .agg(
            days=("date", "size"),
            net_return_sum=("ret", "sum"),
            futures_gross_sum=("futures_gross_ret", "sum"),
            put_pnl_sum=("put_pnl_ret", "sum"),
            call_pnl_sum=("call_pnl_ret", "sum") if "call_pnl_ret" in window else ("ret", lambda x: 0.0),
            avg_total_units=("total_units", "mean"),
            avg_momentum_units=("momentum_units", "mean") if "momentum_units" in window else ("total_units", lambda x: 0.0),
            avg_grid_units=("grid_units", "mean") if "grid_units" in window else ("total_units", lambda x: 0.0),
        )
    )
    daily = window.sort_values("ret").head(10).copy()
    daily.insert(0, "product", product)
    return row, monthly, daily


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    annual: list[pd.DataFrame] = []
    attr: list[dict[str, object]] = []
    monthly: list[pd.DataFrame] = []
    worst: list[pd.DataFrame] = []
    audit: dict[str, object] = {"version": "annual-real-performance-20260911", "end": str(END.date()), "products": {}}
    for product in ("IC", "IM"):
        returns = load_return_stream(product)
        components = load_component_stream(product)
        start = real_start(returns, product)
        frame = add_real_nav(returns, start)
        annual_frame = annual_metrics(frame)
        annual_frame.insert(0, "product", product)
        annual.append(annual_frame)
        row, month_frame, worst_frame = attribution(product, returns, components, start)
        attr.append(row)
        month_frame.insert(0, "product", product)
        monthly.append(month_frame)
        worst.append(worst_frame)
        audit["products"][product] = {
            "real_start": str(start.date()),
            "end": str(frame["date"].iloc[-1].date()),
            "rows": int(len(frame)),
            "final_nav_from_real": float(frame["nav_real"].iloc[-1]),
            "overall": row,
        }

    annual_frame = pd.concat(annual, ignore_index=True)
    attr_frame = pd.DataFrame(attr)
    monthly_frame = pd.concat(monthly, ignore_index=True)
    worst_frame = pd.concat(worst, ignore_index=True)
    annual_frame.to_csv(OUTPUT / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    attr_frame.to_csv(OUTPUT / "max_drawdown_attribution.csv", index=False, encoding="utf-8-sig")
    monthly_frame.to_csv(OUTPUT / "max_drawdown_monthly.csv", index=False, encoding="utf-8-sig")
    worst_frame.to_csv(OUTPUT / "max_drawdown_worst_days.csv", index=False, encoding="utf-8-sig")
    (OUTPUT / "analysis.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    record = [
        "# IC/IM 真实数据段年度收益与最大回撤分析",
        "",
        f"- 计算截止：`{END.date()}`；收益流来自 `outputs/nav_r7_complete_refresh_20260911` 的完整刷新路径。",
        "- 真实数据段：IC 从 2022-09-19 起；IM 从 2022-07-22 起。",
        "- 年度收益为自然年累计净收益；2026 为截至 2026-09-10 的 YTD；年度最大回撤从上一年末净值继续计算。",
        "- 最大回撤归因使用同一回撤区间的逐日组件流。组件收益分别复利，因正式引擎存在乘法成本和现金加项，组件百分比不做简单相加。",
        "- 状态：研究审计，不改变冻结主线、账本或生产授权。",
        "",
        "## 产物",
        "",
        "- `annual_metrics.csv`：年度收益与年内最大回撤。",
        "- `max_drawdown_attribution.csv`：各品种最大回撤区间与组件归因。",
        "- `max_drawdown_monthly.csv`：最大回撤区间的月度分段。",
        "- `max_drawdown_worst_days.csv`：回撤区间最差交易日。",
        "- `analysis.json`：日期、行数、最终净值和归因审计信息。",
    ]
    (OUTPUT / "record.md").write_text("\n".join(record) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "annual": annual_frame.to_dict("records"), "attribution": attr}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
