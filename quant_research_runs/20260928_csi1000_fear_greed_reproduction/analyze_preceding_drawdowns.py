from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

RUN_DIR = Path(__file__).resolve().parent
INPUT_DIR = RUN_DIR / "inputs"
START = pd.Timestamp("2022-07-22")
END = pd.Timestamp("2026-09-18")
BANDS = ["0–24，极冷", "25–44，偏冷", "45–54，中性", "55–74，偏热", "75–100，极热"]
EDGES = [0.0, 25.0, 45.0, 55.0, 75.0, 101.0]


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def main() -> None:
    fear_all = pd.read_csv(INPUT_DIR / "fear_greed_full.csv", parse_dates=["date"])
    fear = fear_all.loc[fear_all["date"].between(START, END), ["date", "fear_greed_index"]].copy()
    fear["fear_greed_index"] = pd.to_numeric(fear["fear_greed_index"], errors="raise")

    payload = json.loads((INPUT_DIR / "csi1000_official_ohlc_response.json").read_text(encoding="utf-8"))
    official = pd.DataFrame(payload["data"])
    official = official[["tradeDate", "close"]].rename(columns={"tradeDate": "date"})
    official["date"] = pd.to_datetime(official["date"], format="%Y%m%d", errors="raise")
    official["close"] = pd.to_numeric(official["close"], errors="raise")
    official = official.loc[official["date"].between(START, END)].copy()

    # Use the project's validated official close cache only for pre-window warm-up.
    local_path = RUN_DIR.parents[1] / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv"
    local = pd.read_csv(local_path, parse_dates=["date"])[["date", "close"]]
    overlap = official.merge(local, on="date", suffixes=("_api", "_local"), validate="one_to_one")
    max_overlap_diff = float((overlap["close_api"] - overlap["close_local"]).abs().max())
    if max_overlap_diff != 0.0:
        raise ValueError(f"Official cache differs from current official response: {max_overlap_diff}")
    prehistory = local.loc[local["date"] < START].copy()
    prices = pd.concat([prehistory, official], ignore_index=True).sort_values("date").reset_index(drop=True)
    if prices["date"].duplicated().any() or not prices["date"].is_monotonic_increasing:
        raise ValueError("Combined official-price series has duplicate or unordered dates")
    if (prices["close"] <= 0).any() or not np.isfinite(prices["close"].to_numpy()).all():
        raise ValueError("Combined closes must be finite and positive")

    close = prices["close"].to_numpy(dtype=float)
    for horizon in (1, 5, 20, 60):
        prices[f"prior_return_{horizon}d"] = prices["close"] / prices["close"].shift(horizon) - 1.0
    for horizon in (20, 60):
        prices[f"drawdown_from_{horizon}d_high"] = (
            prices["close"] / prices["close"].rolling(horizon + 1, min_periods=horizon + 1).max() - 1.0
        )
        values = np.full(len(prices), np.nan, dtype=float)
        for i in range(horizon, len(prices)):
            path = close[i - horizon : i + 1]
            values[i] = np.min(path / np.maximum.accumulate(path) - 1.0)
        prices[f"trailing_max_drawdown_{horizon}d"] = values

    calendar = prices.loc[prices["date"].between(START, END)].copy()
    fear_by_date = fear.set_index("date")["fear_greed_index"]
    calendar["fear_greed_index"] = calendar["date"].map(fear_by_date)
    calendar["fear_band"] = pd.cut(
        calendar["fear_greed_index"], bins=EDGES, labels=BANDS, right=False, include_lowest=True
    )
    scored = calendar.loc[calendar["fear_greed_index"].notna()].copy()
    if len(scored) != len(fear) or scored["prior_return_60d"].isna().any():
        raise ValueError("Fear/calendar alignment or prior-history warm-up is incomplete")
    scored["fear_band"] = pd.Categorical(scored["fear_band"], categories=BANDS, ordered=True)

    variables = [
        "prior_return_1d", "prior_return_5d", "prior_return_20d", "prior_return_60d",
        "drawdown_from_20d_high", "drawdown_from_60d_high",
        "trailing_max_drawdown_20d", "trailing_max_drawdown_60d",
    ]
    rows: list[dict[str, object]] = []
    for band, group in scored.groupby("fear_band", observed=False, sort=False):
        if pd.isna(band):
            continue
        row: dict[str, object] = {"fear_band": str(band), "n_scored_days": int(len(group))}
        for variable in variables:
            values = group[variable].dropna()
            row[f"{variable}_mean"] = float(values.mean())
            row[f"{variable}_median"] = float(values.median())
        r20 = group["prior_return_20d"].dropna()
        dd60 = group["drawdown_from_60d_high"].dropna()
        mdd20 = group["trailing_max_drawdown_20d"].dropna()
        row["prior_20d_return_negative_share"] = float((r20 < 0).mean())
        row["prior_20d_return_le_minus5pct_share"] = float((r20 <= -0.05).mean())
        row["prior_20d_return_le_minus10pct_share"] = float((r20 <= -0.10).mean())
        row["current_60d_peak_drawdown_le_minus5pct_share"] = float((dd60 <= -0.05).mean())
        row["current_60d_peak_drawdown_le_minus10pct_share"] = float((dd60 <= -0.10).mean())
        row["trailing_20d_max_drawdown_le_minus10pct_share"] = float((mdd20 <= -0.10).mean())
        rows.append(row)
    summary = pd.DataFrame(rows)
    summary["fear_band"] = pd.Categorical(summary["fear_band"], categories=BANDS, ordered=True)
    summary = summary.sort_values("fear_band").reset_index(drop=True)

    # Form distinct extreme-fear runs on the full official calendar, so missing-score dates break a run.
    calendar["extreme_fear"] = calendar["fear_greed_index"].lt(25).fillna(False)
    calendar["run_start"] = calendar["extreme_fear"] & ~calendar["extreme_fear"].shift(1, fill_value=False)
    calendar["run_id"] = calendar["run_start"].cumsum()
    episode_rows: list[dict[str, object]] = []
    for run_id, group in calendar.loc[calendar["extreme_fear"]].groupby("run_id", sort=False):
        episode_rows.append({
            "episode_id": int(run_id),
            "start_date": group["date"].min().date().isoformat(),
            "end_date": group["date"].max().date().isoformat(),
            "scored_trading_days": int(len(group)),
            "minimum_score": float(group["fear_greed_index"].min()),
            "end_date_prior_20d_return": float(group["prior_return_20d"].iloc[-1]),
            "end_date_drawdown_from_60d_high": float(group["drawdown_from_60d_high"].iloc[-1]),
        })
    episodes = pd.DataFrame(episode_rows)

    scored.to_csv(RUN_DIR / "preceding_market_context_by_day.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN_DIR / "preceding_market_context_summary.csv", index=False, encoding="utf-8-sig")
    episodes.to_csv(RUN_DIR / "extreme_fear_episodes.csv", index=False, encoding="utf-8-sig")

    extreme = summary.loc[summary["fear_band"] == BANDS[0]].iloc[0]
    other = scored.loc[scored["fear_band"] != BANDS[0]]
    low = scored.loc[scored["fear_band"] == BANDS[0]]
    output = [
        "# 低恐贪分与评分日前中证1000跌幅",
        "",
        "## 结论口径",
        "",
        "本分析只检验低分出现时指数此前已经发生了什么，不把低分后的反弹/下跌混进来。恐贪值按评分日期与中证1000官方交易日对齐；前期收益按收盘价计算，回撤以日收盘价为准。",
        "",
        "- 前20日收益：评分日收盘 / 20个官方交易日前收盘 − 1。",
        "- 距60日高点回撤：评分日收盘 / 最近61个收盘点最高值 − 1。",
        "- 前20日最大回撤：最近21个收盘点内，按逐日收盘价计算峰谷最大回撤。",
        "- “低分”沿用截图的0–24档；阈值统计仅是便于阅读的描述性切点，不代表网站判定“大跌”的官方定义。",
        "",
        "## 分档统计",
        "",
        "| 恐贪档 | 日数 | 前5日收益均值/中位数 | 前20日收益均值/中位数 | 距60日高点回撤均值/中位数 | 前20日最大回撤均值/中位数 | 前20日跌超5%占比 | 前20日跌超10%占比 | 距60日高点跌超10%占比 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in summary.iterrows():
        output.append(
            f"| {row['fear_band']} | {int(row['n_scored_days'])} | "
            f"{pct(row['prior_return_5d_mean'])} / {pct(row['prior_return_5d_median'])} | "
            f"{pct(row['prior_return_20d_mean'])} / {pct(row['prior_return_20d_median'])} | "
            f"{pct(row['drawdown_from_60d_high_mean'])} / {pct(row['drawdown_from_60d_high_median'])} | "
            f"{pct(row['trailing_max_drawdown_20d_mean'])} / {pct(row['trailing_max_drawdown_20d_median'])} | "
            f"{row['prior_20d_return_le_minus5pct_share']:.1%} | "
            f"{row['prior_20d_return_le_minus10pct_share']:.1%} | "
            f"{row['current_60d_peak_drawdown_le_minus10pct_share']:.1%} |"
        )
    output.extend([
        "",
        "## 极冷档观察",
        "",
        f"0–24档有{len(low)}个评分日，拆成{len(episodes)}段连续极冷区间。前20日收益中位数为{pct(extreme['prior_return_20d_median'])}；评分日收盘距60日高点回撤中位数为{pct(extreme['drawdown_from_60d_high_median'])}。",
        f"评分日前20日收益为负的占{float((low['prior_return_20d'] < 0).mean()):.1%}，跌幅达到5%或以上的占{float(extreme['prior_20d_return_le_minus5pct_share']):.1%}，达到10%或以上的占{float(extreme['prior_20d_return_le_minus10pct_share']):.1%}；距60日高点已回撤至少10%的占{float(extreme['current_60d_peak_drawdown_le_minus10pct_share']):.1%}。",
        f"作比较，其他评分档合计{len(other)}个评分日，前20日收益中位数为{pct(other['prior_return_20d'].median())}，距60日高点回撤中位数为{pct(other['drawdown_from_60d_high'].median())}。这些是重叠日度观察，不能当作独立事件样本或因果检验。",
        "",
        "## 极冷区间",
        "",
        "| 开始 | 结束 | 交易日数 | 最低分 | 区间末日前20日收益 | 区间末日距60日高点回撤 |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for _, row in episodes.iterrows():
        output.append(
            f"| {row['start_date']} | {row['end_date']} | {int(row['scored_trading_days'])} | {row['minimum_score']:.2f} | "
            f"{pct(row['end_date_prior_20d_return'])} | {pct(row['end_date_drawdown_from_60d_high'])} |"
        )
    output.extend([
        "",
        "## 数据来源与边界",
        "",
        "- 恐贪分数：本次截图复现所保存的百分位网原始日频CSV快照，区间2022-07-22至2026-09-18。",
        "- 中证1000：前期热身使用项目内官方收盘缓存，评分区间使用已保存的中证指数官网000852日行情响应。两者986个重叠日期收盘价最大差为0。",
        "- 官网响应有1011个官方交易日，评分有1010个值；无分数日不被当成恐贪低分。评分日计算均保留完整官方行情序列。",
        "- 样本是同一历史时期内的条件描述。低分会连续出现，日观察和滚动窗口彼此重叠；本分析不证明评分导致价格变化，也不验证IV。",
        "",
        "## 文件",
        "",
        "- `analyze_preceding_drawdowns.py`：重算脚本，仅使用本研究产物及项目缓存。",
        "- `preceding_market_context_by_day.csv`：每个评分日的分数、前期收益和回撤。",
        "- `preceding_market_context_summary.csv`：按恐贪档位汇总。",
        "- `extreme_fear_episodes.csv`：0–24极冷连续区间。",
    ])
    (RUN_DIR / "preceding_market_context.md").write_text("\n".join(output) + "\n", encoding="utf-8")
    print(f"official_overlap_rows={len(overlap)} max_close_diff={max_overlap_diff}")
    print(f"prehistory_rows={len(prehistory)} official_rows={len(official)} scored_rows={len(scored)} extreme_episodes={len(episodes)}")
    print(summary[["fear_band", "n_scored_days", "prior_return_5d_mean", "prior_return_20d_mean", "prior_return_20d_median", "drawdown_from_60d_high_median", "trailing_max_drawdown_20d_median", "prior_20d_return_le_minus10pct_share", "current_60d_peak_drawdown_le_minus10pct_share"]].to_string(index=False))
    print(episodes.to_string(index=False))


if __name__ == "__main__":
    main()
