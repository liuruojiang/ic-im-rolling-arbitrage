from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


RUN_DIR = Path(__file__).resolve().parent
INPUT_DIR = RUN_DIR / "inputs"
START_DATE = pd.Timestamp("2022-07-22")
END_DATE = pd.Timestamp("2026-09-18")
HORIZONS = (5, 10, 20)
FEAR_BANDS = ("0–24，极冷", "25–44，偏冷", "45–54，中性", "55–74，偏热", "75–100，极热")
FEAR_EDGES = (0.0, 25.0, 45.0, 55.0, 75.0, 101.0)
FEAR_SOURCE_URL = "https://baifenwei.com/data/fear-greed/fear-greed-full.csv"
CSINDEX_API = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def max_drawdown(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    if values.size == 0 or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Drawdown path must contain finite positive prices")
    running_peak = np.maximum.accumulate(values)
    return float(np.min(values / running_peak - 1.0))


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    fear_path = INPUT_DIR / "fear_greed_full.csv"
    price_path = INPUT_DIR / "csi1000_official_ohlc_response.json"

    fear_all = pd.read_csv(fear_path, parse_dates=["date"])
    if list(fear_all.columns) != ["date", "fear_greed_index"]:
        raise ValueError(f"Unexpected fear CSV columns: {fear_all.columns.tolist()}")
    if fear_all["date"].duplicated().any():
        raise ValueError("Fear CSV contains duplicate dates")
    fear = fear_all.loc[fear_all["date"].between(START_DATE, END_DATE)].copy()
    fear["fear_greed_index"] = pd.to_numeric(fear["fear_greed_index"], errors="raise")

    payload = json.loads(price_path.read_text(encoding="utf-8"))
    if payload.get("success") is not True or not payload.get("data"):
        raise ValueError("Official CSI index response is unsuccessful or empty")
    raw = pd.DataFrame(payload["data"])
    required = {"tradeDate", "indexCode", "open", "high", "low", "close"}
    if not required.issubset(raw.columns):
        raise ValueError(f"Official CSI response is missing columns: {sorted(required - set(raw.columns))}")
    if set(raw["indexCode"].astype(str)) != {"000852"}:
        raise ValueError("Official CSI response contains an unexpected index code")
    prices = raw[["tradeDate", "open", "high", "low", "close"]].rename(
        columns={"tradeDate": "date"}
    )
    prices["date"] = pd.to_datetime(prices["date"], format="%Y%m%d", errors="raise")
    for column in ("open", "high", "low", "close"):
        prices[column] = pd.to_numeric(prices[column], errors="raise")
    prices = prices.loc[prices["date"].between(START_DATE, END_DATE)].copy()
    if prices["date"].duplicated().any():
        raise ValueError("Official CSI response contains duplicate dates")
    if not np.isfinite(prices[["open", "high", "low", "close"]].to_numpy()).all():
        raise ValueError("Official CSI response contains non-finite prices")
    if (prices[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Official CSI response contains non-positive prices")

    prices = prices.sort_values("date").reset_index(drop=True)
    fear = fear.sort_values("date").reset_index(drop=True)
    merged = fear.merge(prices, on="date", how="inner", validate="one_to_one")
    merged = merged.sort_values("date").reset_index(drop=True)
    if merged.empty or merged["date"].iloc[0] != START_DATE or merged["date"].iloc[-1] != END_DATE:
        raise ValueError("Aligned series do not span the registered start and end dates")
    if merged["fear_greed_index"].isna().any():
        raise ValueError("Aligned fear series contains missing scores")
    if not merged["fear_greed_index"].between(0, 100, inclusive="both").all():
        raise ValueError("Fear scores fall outside the expected 0–100 scale")

    merged["fear_band"] = pd.cut(
        merged["fear_greed_index"],
        bins=FEAR_EDGES,
        labels=FEAR_BANDS,
        right=False,
        include_lowest=True,
    )
    if merged["fear_band"].isna().any():
        raise ValueError("One or more fear scores were not assigned to a screenshot band")

    unmatched_price_dates = sorted(set(prices["date"]) - set(fear["date"]))
    unmatched_fear_dates = sorted(set(fear["date"]) - set(prices["date"]))
    details: dict[str, object] = {
        "fear_csv_rows_full": int(len(fear_all)),
        "fear_csv_first_date_full": str(fear_all["date"].min().date()),
        "fear_csv_last_date_full": str(fear_all["date"].max().date()),
        "fear_rows_in_window": int(len(fear)),
        "official_price_rows_in_window": int(len(prices)),
        "aligned_rows": int(len(merged)),
        "aligned_first_date": str(merged["date"].min().date()),
        "aligned_last_date": str(merged["date"].max().date()),
        "official_dates_without_fear_score": [str(value.date()) for value in unmatched_price_dates],
        "fear_dates_without_official_price": [str(value.date()) for value in unmatched_fear_dates],
        "fear_score_min": float(merged["fear_greed_index"].min()),
        "fear_score_max": float(merged["fear_greed_index"].max()),
        "fear_band_full_window_counts": {
            str(band): int(count)
            for band, count in merged["fear_band"].value_counts(sort=False).items()
        },
    }
    return merged, fear, prices, details


def build_episodes(aligned: pd.DataFrame) -> pd.DataFrame:
    episodes: list[dict[str, object]] = []
    for horizon in HORIZONS:
        last_signal_position = len(aligned) - horizon - 1
        for signal_position in range(last_signal_position + 1):
            entry_position = signal_position + 1
            exit_position = signal_position + horizon
            signal = aligned.iloc[signal_position]
            entry = aligned.iloc[entry_position]
            exit_row = aligned.iloc[exit_position]
            close_path = aligned.iloc[entry_position : exit_position + 1]["close"].to_numpy(dtype=float)
            path = np.concatenate(([float(entry["open"])], close_path))
            episodes.append(
                {
                    "signal_date": signal["date"].date().isoformat(),
                    "fear_greed_index": float(signal["fear_greed_index"]),
                    "fear_band": str(signal["fear_band"]),
                    "horizon_trading_days": horizon,
                    "entry_date": entry["date"].date().isoformat(),
                    "entry_open": float(entry["open"]),
                    "exit_date": exit_row["date"].date().isoformat(),
                    "exit_close": float(exit_row["close"]),
                    "forward_return": float(exit_row["close"] / entry["open"] - 1.0),
                    "interval_max_drawdown": max_drawdown(path),
                }
            )
    return pd.DataFrame(episodes)


def build_official_calendar_episodes(fear: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Use every official market session after the signal, even when a fear score is missing."""
    score_by_date = fear.set_index("date")["fear_greed_index"]
    calendar = prices.copy().sort_values("date").reset_index(drop=True)
    calendar["fear_greed_index"] = calendar["date"].map(score_by_date)
    calendar["fear_band"] = pd.cut(
        calendar["fear_greed_index"],
        bins=FEAR_EDGES,
        labels=FEAR_BANDS,
        right=False,
        include_lowest=True,
    )

    episodes: list[dict[str, object]] = []
    for horizon in HORIZONS:
        for signal_position in range(len(calendar) - horizon):
            signal = calendar.iloc[signal_position]
            if pd.isna(signal["fear_greed_index"]):
                continue
            entry_position = signal_position + 1
            exit_position = signal_position + horizon
            entry = calendar.iloc[entry_position]
            exit_row = calendar.iloc[exit_position]
            close_path = calendar.iloc[entry_position : exit_position + 1]["close"].to_numpy(dtype=float)
            path = np.concatenate(([float(entry["open"])], close_path))
            episodes.append(
                {
                    "signal_date": signal["date"].date().isoformat(),
                    "fear_greed_index": float(signal["fear_greed_index"]),
                    "fear_band": str(signal["fear_band"]),
                    "horizon_trading_days": horizon,
                    "entry_date": entry["date"].date().isoformat(),
                    "entry_open": float(entry["open"]),
                    "exit_date": exit_row["date"].date().isoformat(),
                    "exit_close": float(exit_row["close"]),
                    "forward_return": float(exit_row["close"] / entry["open"] - 1.0),
                    "interval_max_drawdown": max_drawdown(path),
                }
            )
    return pd.DataFrame(episodes)


def summarize_episodes(episodes: pd.DataFrame) -> pd.DataFrame:
    summary = (
        episodes.groupby(["horizon_trading_days", "fear_band"], observed=True, sort=False)
        .agg(
            sample_count=("forward_return", "size"),
            mean_forward_return=("forward_return", "mean"),
            mean_interval_max_drawdown=("interval_max_drawdown", "mean"),
        )
        .reset_index()
    )
    summary["fear_band"] = pd.Categorical(summary["fear_band"], categories=FEAR_BANDS, ordered=True)
    return summary.sort_values(["horizon_trading_days", "fear_band"]).reset_index(drop=True)


def display_percent(value: float) -> str:
    return f"{value:+.2%}"


def main() -> None:
    aligned, fear, prices, input_details = load_inputs()
    episodes = build_episodes(aligned)
    summary = summarize_episodes(episodes)
    calendar_episodes = build_official_calendar_episodes(fear, prices)
    calendar_summary = summarize_episodes(calendar_episodes)

    expected_counts = {
        5: 1005,
        10: 1000,
        20: 990,
    }
    counts_by_horizon = {
        int(horizon): int(group["sample_count"].sum())
        for horizon, group in summary.groupby("horizon_trading_days", sort=True)
    }
    if counts_by_horizon != expected_counts:
        raise ValueError(f"Forward sample counts changed: {counts_by_horizon}")
    calendar_counts_by_horizon = {
        int(horizon): int(group["sample_count"].sum())
        for horizon, group in calendar_summary.groupby("horizon_trading_days", sort=True)
    }
    if calendar_counts_by_horizon != expected_counts:
        raise ValueError(f"Official-calendar sample counts changed: {calendar_counts_by_horizon}")

    paired = episodes.merge(
        calendar_episodes,
        on=["signal_date", "horizon_trading_days"],
        how="inner",
        suffixes=("_joined", "_calendar"),
        validate="one_to_one",
    )
    if len(paired) != len(episodes) or len(paired) != len(calendar_episodes):
        raise ValueError("Screenshot and official-calendar episode sets do not match")
    paired["entry_or_exit_date_changed"] = (
        paired["entry_date_joined"].ne(paired["entry_date_calendar"])
        | paired["exit_date_joined"].ne(paired["exit_date_calendar"])
    )
    changed_dates_by_horizon = {
        int(horizon): int(group["entry_or_exit_date_changed"].sum())
        for horizon, group in paired.groupby("horizon_trading_days", sort=True)
    }

    sensitivity = summary.merge(
        calendar_summary,
        on=["horizon_trading_days", "fear_band"],
        how="inner",
        suffixes=("_screenshot_join", "_official_calendar"),
        validate="one_to_one",
    )
    sensitivity["return_difference_percentage_points"] = (
        sensitivity["mean_forward_return_official_calendar"]
        - sensitivity["mean_forward_return_screenshot_join"]
    ) * 100.0
    sensitivity["drawdown_difference_percentage_points"] = (
        sensitivity["mean_interval_max_drawdown_official_calendar"]
        - sensitivity["mean_interval_max_drawdown_screenshot_join"]
    ) * 100.0

    aligned.to_csv(RUN_DIR / "aligned_daily_inputs.csv", index=False, encoding="utf-8-sig")
    episodes.to_csv(RUN_DIR / "episode_results.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN_DIR / "results.csv", index=False, encoding="utf-8-sig")
    calendar_episodes.to_csv(RUN_DIR / "strict_calendar_episode_results.csv", index=False, encoding="utf-8-sig")
    calendar_summary.to_csv(RUN_DIR / "strict_calendar_results.csv", index=False, encoding="utf-8-sig")
    sensitivity.to_csv(RUN_DIR / "calendar_sensitivity.csv", index=False, encoding="utf-8-sig")

    comparison: dict[str, object] = {
        "available": False,
        "path": "data/ic_im_valuation_risk_premium_forecast_v3/csindex_000852.csv",
    }
    local_close_path = RUN_DIR.parents[1] / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv"
    if local_close_path.exists():
        local = pd.read_csv(local_close_path, parse_dates=["date"])[["date", "close"]]
        official = prices[["date", "close"]]
        overlap = official.merge(local, on="date", how="inner", suffixes=("_official_api", "_local"), validate="one_to_one")
        if not overlap.empty:
            comparison = {
                "available": True,
                "path": "data/ic_im_valuation_risk_premium_forecast_v3/csindex_000852.csv",
                "overlap_rows": int(len(overlap)),
                "maximum_absolute_close_difference": float(
                    (overlap["close_official_api"] - overlap["close_local"]).abs().max()
                ),
                "all_overlapping_closes_equal": bool(
                    (overlap["close_official_api"] == overlap["close_local"]).all()
                ),
            }

    fear_path = INPUT_DIR / "fear_greed_full.csv"
    price_path = INPUT_DIR / "csi1000_official_ohlc_response.json"
    manifest = {
        "status": "research_diagnostic_reproduction",
        "generated_at_asia_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "window": {"start": str(START_DATE.date()), "end": str(END_DATE.date())},
        "fear_data_source": {
            "url": FEAR_SOURCE_URL,
            "page_url": "https://baifenwei.com/indicator/fear-greed/",
            "methodology_url": "https://baifenwei.com/methodology/",
            "snapshot": str(fear_path.relative_to(RUN_DIR)),
            "sha256": sha256(fear_path),
            "fields": ["date", "fear_greed_index"],
            "source_description": "Baifenwei A-share composite fear-greed series; downloaded full history, then restricted to the registered sample window.",
            "current_page_band_labels": ["0–25", "26–45", "46–55", "56–75", "76–100"],
        },
        "price_data_source": {
            "url": CSINDEX_API,
            "query": {"indexCode": "000852", "startDate": "20220722", "endDate": "20260918"},
            "snapshot": str(price_path.relative_to(RUN_DIR)),
            "sha256": sha256(price_path),
            "fields_used": ["tradeDate", "open", "close"],
            "adjustment": "official CSI 1000 price index levels; no price adjustment",
        },
        "input_coverage": input_details,
        "price_close_crosscheck": comparison,
        "calculation": {
            "signal_clock": "Fear-greed value dated t is assumed known after t close.",
            "screenshot_reproduction_entry": "Open of the next row after the date inner-join; this reproduces every displayed screenshot cell at two decimals.",
            "screenshot_reproduction_exit": "Close at N rows after the signal on the inner-joined series; entry session counts as holding day 1.",
            "strict_calendar_entry": "Open of the next official CSI 1000 session after the signal date, even if that session has no fear score.",
            "strict_calendar_exit": "Close of the Nth official CSI 1000 session after the signal date; all intervening official closes enter the drawdown path.",
            "forward_return": "exit close / entry open - 1; arithmetic mean across signal episodes.",
            "interval_max_drawdown": "For each episode, use [entry open, close of entry session, ..., close of exit session] and compute the minimum price / prior running peak - 1; report the arithmetic mean of episode drawdowns.",
            "fear_bands": {
                "0–24，极冷": "[0, 25)",
                "25–44，偏冷": "[25, 45)",
                "45–54，中性": "[45, 55)",
                "55–74，偏热": "[55, 75)",
                "75–100，极热": "[75, 101)",
            },
            "transaction_costs": "none; descriptive conditional index statistics only",
            "overlap": "Signal windows overlap; sample counts are not independent events.",
        },
        "valid_samples_by_horizon": counts_by_horizon,
        "strict_calendar_valid_samples_by_horizon": calendar_counts_by_horizon,
        "calendar_alignment_sensitivity": {
            "episode_entry_or_exit_dates_changed_by_horizon": changed_dates_by_horizon,
            "source_of_difference": "Fear CSV has no score on 2026-07-27; the strict variant preserves that official price session in subsequent holding paths.",
        },
        "outputs": {
            "aligned_daily_inputs.csv": int(len(aligned)),
            "episode_results.csv": int(len(episodes)),
            "results.csv": int(len(summary)),
            "strict_calendar_episode_results.csv": int(len(calendar_episodes)),
            "strict_calendar_results.csv": int(len(calendar_summary)),
            "calendar_sensitivity.csv": int(len(sensitivity)),
        },
        "runtime": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "script_sha256": sha256(Path(__file__).resolve()),
        },
    }
    (RUN_DIR / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def make_table_lines(table: pd.DataFrame) -> list[str]:
        wide = table.pivot(index="fear_band", columns="horizon_trading_days", values=[
            "sample_count", "mean_forward_return", "mean_interval_max_drawdown"
        ])
        row_lines: list[str] = []
        for band in FEAR_BANDS:
            row = [band]
            for horizon in HORIZONS:
                mean_return = float(wide.loc[band, ("mean_forward_return", horizon)])
                mean_drawdown = float(wide.loc[band, ("mean_interval_max_drawdown", horizon)])
                row.append(f"{display_percent(mean_return)} / {display_percent(mean_drawdown)}")
            row.append(str(int(wide.loc[band, ("sample_count", 20)])))
            row_lines.append("| " + " | ".join(row) + " |")
        return row_lines

    screenshot_rows = make_table_lines(summary)
    calendar_rows = make_table_lines(calendar_summary)

    record = [
        "# 中证1000恐贪分组前瞻表现复现",
        "",
        "状态：研究诊断，非 IC/IM 正式主线绩效，不构成交易信号。",
        "",
        "## 结果",
        "",
        "### 截图口径复现",
        "",
        "数值格式为该恐贪档位下的后续平均收益 / 平均区间最大回撤；回撤为负数。收益和回撤均为逐样本简单算术平均。",
        "",
        "| 恐贪读数 | 5 日 | 10 日 | 20 日 | 20 日样本数 |",
        "| --- | ---: | ---: | ---: | ---: |",
        *screenshot_rows,
        "",
        "### 完整官方交易日历口径",
        "",
        "以下敏感性结果严格按官方交易日历取下一交易日开盘，并在持有路径中保留无恐贪值日期的官方收盘价。",
        "",
        "| 恐贪读数 | 5 日 | 10 日 | 20 日 | 20 日样本数 |",
        "| --- | ---: | ---: | ---: | ---: |",
        *calendar_rows,
        "",
        "## 复现口径",
        "",
        f"- 恐贪与官方中证1000行情按交易日内连接，窗口 {START_DATE.date()} 至 {END_DATE.date()}；恐贪有 {input_details['fear_rows_in_window']} 行，官方行情有 {input_details['official_price_rows_in_window']} 行，连接后 {input_details['aligned_rows']} 行。未匹配的官方交易日：{', '.join(input_details['official_dates_without_fear_score']) or '无'}。",
        "- 截图表可由 1,010 行内连接数据按行号前移精确复现。字面上严格按官方交易日执行的结果见上表，避免把恐贪缺值日从价格持有路径中删掉。",
        "- 两种口径均假设信号日收盘后读取恐贪值；下一交易日开盘买入，持有 N 个官方交易日后收盘退出。收益 = 退出收盘 / 入场开盘 − 1。",
        "- 单个区间最大回撤以入场开盘价为初始净值，之后使用入场日至退出日的每日收盘价计算；报告的是各区间最大回撤的算术平均。",
        "- 截图分档按 [0,25)、[25,45)、[45,55)、[55,75)、[75,101) 实现。无成本、无滑点；重叠持有窗口不是独立事件。",
        f"- 各期限有效样本：5 日 {counts_by_horizon[5]}，10 日 {counts_by_horizon[10]}，20 日 {counts_by_horizon[20]}。",
        "",
        "## 数据和限制",
        "",
        f"- 恐贪：百分位网完整日频 CSV，快照日期 {manifest['generated_at_asia_shanghai'][:10]}；这是网站派生序列，不含六项原始子指标。网站当前页面显示的分档为 0–25、26–45、46–55、56–75、76–100；本次按截图区间 0–24、25–44、45–54、55–74、75–100 复现。",
        f"- 行情：中证指数官方 000852 日行情接口，保留原始 API JSON；既有本地官方收盘缓存与本次官方接口在 {comparison.get('overlap_rows', 0)} 个重叠交易日上最大绝对差为 {comparison.get('maximum_absolute_close_difference', 'N/A')}。",
        f"- 恐贪序列缺少 2026-07-27；内连接后行号法会在穿过该日的少数区间跳过该日收盘。日历口径两表相同样本数，但入场/退出日期有变动：5/10/20 日分别 {changed_dates_by_horizon[5]}/{changed_dates_by_horizon[10]}/{changed_dates_by_horizon[20]} 个样本。",
        "- 20 日观察期只使用截至 2026-09-18 已完整结束的区间，因此末尾 20 个信号日不能进入 20 日结果。",
        "- 此表是条件分组的历史描述，不证明恐贪指数有预测能力，也不等于中证1000可成交策略回测。",
        "",
        "## 复现文件",
        "",
        "- `reproduce.py`：确定性计算脚本；仅读取本目录输入快照。",
        "- `inputs/fear_greed_full.csv`：百分位网原始 CSV 快照。",
        "- `inputs/csi1000_official_ohlc_response.json`：中证指数官方原始接口响应。",
        "- `aligned_daily_inputs.csv`：1,010 行对齐输入。",
        "- `episode_results.csv`：每个信号日、持有期限的逐样本收益与区间最大回撤。",
        "- `results.csv`：分组汇总。",
        "- `strict_calendar_episode_results.csv` / `strict_calendar_results.csv`：完整官方交易日历口径的逐样本和汇总结果。",
        "- `calendar_sensitivity.csv`：两种计算口径的分组差值。",
        "- `data_manifest.json`：来源、快照 SHA-256、覆盖、口径和运行环境。",
        "",
    ]
    (RUN_DIR / "record.md").write_text("\n".join(record), encoding="utf-8")

    print(summary.to_string(index=False, float_format=lambda value: f"{value:.8f}"))
    print("\nStrict official calendar:")
    print(calendar_summary.to_string(index=False, float_format=lambda value: f"{value:.8f}"))
    print(f"\nWrote outputs under: {RUN_DIR}")


if __name__ == "__main__":
    main()
