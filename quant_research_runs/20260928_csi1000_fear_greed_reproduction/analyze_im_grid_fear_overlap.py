from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[1]
FEAR_BANDS = ["0–24，极冷", "25–44，偏冷", "45–54，中性", "55–74，偏热", "75–100，极热"]
ENTRY_LINE = 1.6


def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, **kwargs)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    fear = read_csv(RUN / "aligned_daily_inputs.csv", parse_dates=["date"])[
        ["date", "fear_greed_index", "fear_band"]
    ]
    valuation_path = ROOT / "quant_param_scan_runs/20260908_im_put_revalidation_layer1_v1/valuation_state_through_last_required_eval.csv.gz"
    valuation = read_csv(valuation_path, parse_dates=["date"])[["date", "score"]].rename(
        columns={"score": "im_valuation_score"}
    )
    grid_path = ROOT / "quant_param_scan_runs/20260914_im_grid_half_full_audit_v3/real_original160_half_daily.csv.gz"
    grid = read_csv(grid_path, parse_dates=["date"])[
        ["date", "overlay_held_eod", "overlay_buy", "overlay_sell"]
    ]
    events_path = ROOT / "quant_param_scan_runs/20260914_im_grid_half_full_audit_v3/real_original160_half_grid_trades_quarter1.csv.gz"
    events = read_csv(events_path, parse_dates=["signal_date", "execution_date"])
    events = events.loc[events["action"].eq("buy")].copy()

    daily = fear.merge(valuation, on="date", how="left", validate="one_to_one")
    daily = daily.merge(grid, on="date", how="left", validate="one_to_one")
    daily["im_entry_zone"] = daily["im_valuation_score"].le(ENTRY_LINE + 1e-12)
    daily["grid_state_available"] = daily["overlay_held_eod"].notna()
    daily["grid_held"] = daily["overlay_held_eod"].gt(0)
    daily["fear_band"] = pd.Categorical(daily["fear_band"], categories=FEAR_BANDS, ordered=True)

    rows: list[dict[str, object]] = []
    for band in FEAR_BANDS:
        group = daily.loc[daily["fear_band"].eq(band)]
        with_score = group.loc[group["im_valuation_score"].notna()]
        with_grid = group.loc[group["grid_state_available"]]
        rows.append({
            "fear_band": band,
            "fear_days": int(len(group)),
            "valuation_score_days": int(len(with_score)),
            "im_entry_zone_days_score_le_1_6": int(with_score["im_entry_zone"].sum()),
            "share_entry_zone_within_band": float(with_score["im_entry_zone"].mean()) if len(with_score) else float("nan"),
            "grid_state_days": int(len(with_grid)),
            "grid_held_days": int(with_grid["grid_held"].sum()),
            "share_grid_held_within_band": float(with_grid["grid_held"].mean()) if len(with_grid) else float("nan"),
            "median_im_score": float(with_score["im_valuation_score"].median()) if len(with_score) else float("nan"),
        })
    summary = pd.DataFrame(rows)
    low = daily.loc[daily["fear_greed_index"].lt(25)].copy()
    if len(low) != 29 or low["im_valuation_score"].isna().any() or low["overlay_held_eod"].isna().any():
        raise ValueError("The 0–24 fear observations must all have historical IM valuation and grid-state data")

    event_join = events.merge(
        fear[["date", "fear_greed_index", "fear_band"]],
        left_on="signal_date", right_on="date", how="left", validate="one_to_one",
    ).drop(columns="date")
    event_join["signal_in_extreme_fear"] = event_join["fear_greed_index"].lt(25)
    jul_low = low.loc[low["date"].between("2026-07-01", "2026-07-31")].copy()

    daily.to_csv(RUN / "im_grid_fear_overlap_by_day.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(RUN / "im_grid_fear_overlap_summary.csv", index=False, encoding="utf-8-sig")
    low.to_csv(RUN / "im_grid_extreme_fear_days.csv", index=False, encoding="utf-8-sig")
    event_join.to_csv(RUN / "im_grid_buy_events_vs_fear.csv", index=False, encoding="utf-8-sig")

    zone_days = int(daily["im_valuation_score"].notna().sum())
    total_zone = int(daily.loc[daily["im_valuation_score"].notna(), "im_entry_zone"].sum())
    both = int(low["im_entry_zone"].sum())
    held = int(low["grid_held"].sum())
    low_entry_events = int(event_join["signal_in_extreme_fear"].sum())
    md = [
        "# 中证1000极冷读数与IM估值网格入场区重合度",
        "",
        "## 结论",
        "",
        f"按当前IM网格入场线 `估值分≤{ENTRY_LINE:.1f}` 回看：恐贪0–24档共{len(low)}个评分日，其中{both}日（{both/len(low):.1%}）同日IM估值分也在网格入场区；{held}日（{held/len(low):.1%}）网格当日已有持仓。两种定义下都约一半，属于中等重合，不是高度重合。",
        f"在有估值分的{zone_days}个日期里，IM估值分≤{ENTRY_LINE:.1f}共{total_zone}日；其中只有{both}日同时处于恐贪0–24档（{both/total_zone:.1%}）。因此，低恐贪不是IM网格入场条件的替代指标。",
        f"实际网格新开仓事件共{len(event_join)}次，其中{low_entry_events}次信号日同时为0–24极冷。极冷日的网格已持仓占比高于新开仓交集，是因为有些极冷日发生在网格已于更早日期买入之后。",
        "",
        "## 各恐贪档位的同日交集",
        "",
        "“入场区”按同日IM估值分≤1.6判断；“已持仓”按历史复核日线的收盘后网格状态判断。",
        "",
        "| 恐贪档 | 恐贪日数 | 有估值分日数 | 同日IM分≤1.6 | 占本档 | 网格状态可用日 | 已持仓日 | 占状态可用日 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        md.append(
            f"| {row['fear_band']} | {row['fear_days']} | {row['valuation_score_days']} | "
            f"{row['im_entry_zone_days_score_le_1_6']} | {row['share_entry_zone_within_band']:.1%} | "
            f"{row['grid_state_days']} | {row['grid_held_days']} | {row['share_grid_held_within_band']:.1%} |"
        )
    md.extend([
        "",
        "## 极冷日与网格分数",
        "",
        "| 日期 | 恐贪分 | IM估值分 | 同日处于≤1.6入场区 | 收盘后网格已持有 |",
        "|---|---:|---:|---|---|",
    ])
    for row in low.itertuples():
        md.append(
            f"| {row.date.date().isoformat()} | {row.fear_greed_index:.2f} | {row.im_valuation_score:.6f} | "
            f"{'是' if row.im_entry_zone else '否'} | {'是' if row.grid_held else '否'} |"
        )
    md.extend([
        "",
        "## 最新一轮明显回撤",
        "",
        f"2026年7月共有{len(jul_low)}个0–24极冷评分日；其中IM估值分最小/最大为{jul_low['im_valuation_score'].min():.3f}/{jul_low['im_valuation_score'].max():.3f}，全部高于1.6入场线，且网格状态均为空仓。也就是说，这次恐慌读数很低，但按IM估值网格规则没有买入。",
        "",
        "## 买入事件",
        "",
        "| 买入信号日 | 执行日 | IM估值分 | 同日恐贪分 | 是否0–24极冷 |",
        "|---|---|---:|---:|---|",
    ])
    for row in event_join.itertuples():
        fear_score = "缺分" if pd.isna(row.fear_greed_index) else f"{row.fear_greed_index:.2f}"
        md.append(
            f"| {row.signal_date.date().isoformat()} | {row.execution_date.date().isoformat()} | {row.signal_value:.6f} | "
            f"{fear_score} | {'是' if row.signal_in_extreme_fear else '否'} |"
        )
    md.extend([
        "",
        "## 口径与限制",
        "",
        "- 解释范围限定为中证1000对应的IM估值网格；IC网格以中证500估值分运行，不用中证1000恐贪分直接代替。",
        "- 当前IM网格为估值分≤1.6进入、≥2.0退出，0.5倍增量；信号日收盘确认，下一交易日开盘执行。历史入场区比较固定使用≤1.6；本报告只比较日期与估值状态，不重算策略收益，也不改变正式信号。",
        "- 极冷恐贪日29个均有估值和网格状态数据。估值状态文件截至2026-09-04，因此全部恐贪样本中的10个较晚评分日没有IM估值分；这些日期不含0–24低分。分档百分比只用有估值分/状态的日期作分母。",
        "- IM估值分来自项目2026-09-14完整组合网格历史复核所用日频状态；网格持仓来自相同复核的真实IM路径（截至2026-09-07）。它是已审计历史候选路径，不表示用户账户实际持仓。",
        "- 恐贪读数与估值分是不同构造的指标。低分与估值网格区间有部分同时出现，但不存在一一对应关系；这组重叠统计是历史描述，不是网格规则有效性的因果检验。",
        "",
        "## 数据与文件",
        "",
        f"- 恐贪及行情原复现区间：2022-07-22至2026-09-18；恐贪CSV输入SHA-256：`{sha256(RUN / 'inputs/fear_greed_full.csv')}`。",
        f"- IM估值状态：`quant_param_scan_runs/20260908_im_put_revalidation_layer1_v1/valuation_state_through_last_required_eval.csv.gz`；SHA-256：`{sha256(valuation_path)}`。",
        f"- IM网格路径/事件：`quant_param_scan_runs/20260914_im_grid_half_full_audit_v3/`；路径SHA-256：`{sha256(grid_path)}`；事件SHA-256：`{sha256(events_path)}`。",
        "- `analyze_im_grid_fear_overlap.py`：复算脚本；`im_grid_fear_overlap_by_day.csv`：逐日交集；`im_grid_fear_overlap_summary.csv`：分档汇总；`im_grid_extreme_fear_days.csv`：极冷日期；`im_grid_buy_events_vs_fear.csv`：网格新买入事件对照。",
    ])
    (RUN / "im_grid_fear_overlap.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"extreme_fear_days={len(low)} same_day_entry_zone={both} ({both/len(low):.1%}) grid_held={held} ({held/len(low):.1%})")
    print(f"all_entry_zone_days={total_zone}/{zone_days}, low_fear_share={both/total_zone:.1%}; buy_events={len(event_join)}, low_fear_buy_signals={low_entry_events}")
    print(summary.to_string(index=False))
    print(event_join[["signal_date", "execution_date", "signal_value", "fear_greed_index", "signal_in_extreme_fear"]].to_string(index=False))
    print("july_2026", len(jul_low), round(float(jul_low.im_valuation_score.min()), 6), round(float(jul_low.im_valuation_score.max()), 6), int(jul_low.grid_held.sum()))


if __name__ == "__main__":
    main()
