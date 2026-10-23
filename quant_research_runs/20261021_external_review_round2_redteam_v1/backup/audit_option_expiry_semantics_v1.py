from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from im_monthly_roll_valuation_gated_put_v2 import add_option_expiry
from im_put_maturity_valuation_tiers_v3 import actual_expiry_map


ROOT = Path(__file__).resolve().parent
VERSION = "option_expiry_semantics_audit_v1"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"
PLAN = ROOT / "docs" / f"{VERSION}_plan.md"

INPUTS = {
    "plan": PLAN,
    "script": Path(__file__),
    "ic_put_v21_trades": ROOT / "outputs/ic_510500_put_mom120_delta_floor_v21/trade_audit.csv",
    "ic_put_v21_daily": ROOT / "outputs/ic_510500_put_mom120_delta_floor_v21/daily_candidates.csv.gz",
    "im_put_v12_trades": ROOT / "outputs/im_mo_adaptive_valuation_mom120_floor_v12/trade_audit.csv.gz",
    "im_put_v12_lifecycles": ROOT / "outputs/im_mo_adaptive_valuation_mom120_floor_v12/lifecycle_audit.csv",
    "im_options": ROOT / "data/im_monthly_roll_3m_lowest_put_v1/cffex_mo_puts.csv",
    "im_daily": ROOT / "outputs/im_monthly_roll_3m_lowest_put_v1/daily_nav.csv",
    "im_call_v27_trades": ROOT / "outputs/im_mo_call_daily_d10_threat_roll_v27/call_trades.csv",
    "ic_call_v9_signals": ROOT / "outputs/ic_510500_call_daily_iv_delta_grid_v9/signals.csv",
    "ic_call_v10_signals": ROOT / "outputs/ic_510500_call_daily_iv_tenor_delta_grid_v10/signals.csv",
    "ic_call_v11_signals": ROOT / "outputs/ic_510500_call_daily_iv_dte_target_grid_v11/signals.csv",
    "ic_call_v12_signals": ROOT / "outputs/ic_510500_call_daily_iv_dte60_delta_ladder_v12/signals.csv",
    "ic_put_v13_lifecycles": ROOT / "outputs/ic_510500_put_absolute_momentum_protection_tool_v13/hold_expiry_lifecycles.csv",
    "im_put_v9_lifecycles": ROOT / "outputs/im_mo_close_execution_full_battery_v9/lifecycle_audit.csv",
    "cyb_v3_cycles": ROOT / "outputs/cyb_etf_option_synthetic_roll_v3/monthly_cycles.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fourth_wednesday(month: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> pd.Timestamp:
    month = pd.Timestamp(month.year, month.month, 1)
    days = pd.date_range(month, month + pd.offsets.MonthEnd(0), freq="D")
    target = pd.Timestamp(days[days.weekday == 2][3])
    later = trade_dates[trade_dates >= target]
    return pd.Timestamp(later[0]) if len(later) else target


def stats_row(
    scope: str,
    rule: str,
    sample: str,
    metric: str,
    values: pd.Series | list[float],
    unit: str,
    source: str,
    note: str,
) -> dict[str, object]:
    data = pd.Series(values, dtype="float64").dropna()
    if data.empty:
        raise RuntimeError(f"Empty audit metric: {scope}/{metric}")
    return {
        "scope": scope,
        "canonical_rule": rule,
        "sample": sample,
        "metric": metric,
        "count": int(len(data)),
        "min": float(data.min()),
        "p10": float(data.quantile(0.10)),
        "median": float(data.median()),
        "p90": float(data.quantile(0.90)),
        "max": float(data.max()),
        "unit": unit,
        "source": source,
        "note": note,
    }


def selected_signal_dte(path: Path, candidate: str) -> pd.DataFrame:
    data = pd.read_csv(path, parse_dates=["eval_date", "selection_expiry"])
    data = data[
        data["candidate"].eq(candidate)
        & data["selection_expiry"].notna()
        & data["selection_delta"].notna()
    ].copy()
    data["signal_dte"] = (data["selection_expiry"] - data["eval_date"]).dt.days
    return data


def glossary() -> pd.DataFrame:
    rows = [
        {
            "term": "当月/calendar_same_month",
            "canonical_definition": "期权合约月份等于信号日所在日历月；到期后本月可能已无合格合约",
            "must_specify": "信号日和交易所实际到期日",
            "prohibited_shortcut": "把最近可交易到期日自动称为当月",
        },
        {
            "term": "前月/front_next_eligible",
            "canonical_definition": "在给定锚点之后严格更晚的最近实际挂牌到期日",
            "must_specify": "锚点是执行日、当前期货到期日还是当前期权到期日",
            "prohibited_shortcut": "前一个日历月；或固定约30D",
        },
        {
            "term": "下月/calendar_plus_1m",
            "canonical_definition": "锚点日期加1个日历月所对应的目标月；若取最近挂牌必须另写选择规则",
            "must_specify": "日历目标还是挂牌序号",
            "prohibited_shortcut": "第一个挂牌到期日",
        },
        {
            "term": "下下月/calendar_plus_2m",
            "canonical_definition": "锚点日期加2个日历月所对应的目标月；若取最近挂牌必须另写选择规则",
            "must_specify": "日历目标还是挂牌序号",
            "prohibited_shortcut": "当前参考到期日后的第二个挂牌到期日",
        },
        {
            "term": "挂牌第二档/listed_rank_2",
            "canonical_definition": "按实际到期日排序，取锚点之后第二个挂牌到期日",
            "must_specify": "锚点与缺档时是否跳过",
            "prohibited_shortcut": "下下月或约60D",
        },
        {
            "term": "2M目标/target_plus_2_calendar_months",
            "canonical_definition": "锚点加2个日历月，取实际到期日离目标最近的挂牌月；等距取更晚者",
            "must_specify": "锚点和等距规则",
            "prohibited_shortcut": "固定60D",
        },
        {
            "term": "3M目标/target_plus_3_calendar_months",
            "canonical_definition": "锚点加3个日历月，取实际到期日离目标最近的挂牌月；等距取更晚者",
            "must_specify": "锚点和等距规则",
            "prohibited_shortcut": "固定90D或覆盖三次期货月换",
        },
        {
            "term": "DTE60硬区间/dte60_hard_band",
            "canonical_definition": "在指定日期测量实际自然日DTE，45—75日内取最接近60日；等距取较短者",
            "must_specify": "信号日DTE还是执行日DTE",
            "prohibited_shortcut": "两个月、下下月或挂牌第二档",
        },
        {
            "term": "DTE90硬区间/dte90_hard_band",
            "canonical_definition": "在指定日期测量实际自然日DTE，75—105日内取最接近90日；等距取较短者",
            "must_specify": "信号日DTE还是执行日DTE",
            "prohibited_shortcut": "三个月或挂牌第三档",
        },
        {
            "term": "严格三周期/strict_3_roll_cycles",
            "canonical_definition": "期权到期日在第3个期货月换日之后且第4个期货月换日之前",
            "must_specify": "期货月换日序列及边界是否严格",
            "prohibited_shortcut": "固定90D或3M目标",
        },
        {
            "term": "随月换/monthly_reset",
            "canonical_definition": "每次期货月换节点平旧期权并按当日规则重建；即使合约月份相同也可重置行权价和张数",
            "must_specify": "同月是否重置、成交时点和成本",
            "prohibited_shortcut": "持有到期或期权期限为一个月",
        },
        {
            "term": "持有到期/hold_to_expiry",
            "canonical_definition": "建仓后不因期货月换主动平仓，只在到期或更高优先级退出条件发生时结束",
            "must_specify": "提前退出条件与到期结算日",
            "prohibited_shortcut": "随期货每月换仓",
        },
        {
            "term": "救援后移一挂牌档/rescue_next_listed",
            "canonical_definition": "从旧期权到期日出发，取严格更晚的最近实际挂牌到期日",
            "must_specify": "挂牌稀疏时允许跨季度，以及连续救援是否继续从当前远月向后",
            "prohibited_shortcut": "向后固定一个日历月",
        },
    ]
    return pd.DataFrame(rows)


def inventory() -> pd.DataFrame:
    rows = [
        ["IC Put current", "v21 / combined v2", "3个月95%，随IC月换", "target_plus_3_calendar_months + nearest_listed + monthly_reset", "execution close", "每次IC月换重置；不持有到期", "warning", "3M是目标日期，不是固定90D；同一合约月也可能重置"],
        ["IM Put current", "v12/v14 mainline", "3个月95%，随IM月换", "target_plus_3_calendar_months + nearest_listed + monthly_reset", "signal close; maintenance uses execution close", "月换或信号变化时重建；不持有到期", "warning", "真实DTE随挂牌结构变化，不是固定90D"],
        ["IC Put legacy", "v10 legacy in v13 audit", "3个月持有到期", "target_plus_3_calendar_months + nearest_listed + hold_to_expiry", "entry date", "到期退出", "deprecated", "实际覆盖2—5次IC月换，不等于严格三周期"],
        ["IC Put strict", "v13", "严格持有三周期", "strict_3_roll_cycles + hold_to_expiry", "entry date and IC roll calendar", "到期退出", "pass", "每个已完成生命周期覆盖3次IC月换"],
        ["IM Put historical tenor", "v9", "前月/2个月/3个月", "front_next_eligible or target_plus_2m/3m + nearest_listed", "signal/maintenance dates", "月度退出版本", "warning", "各档是选择规则，不是固定30/60/90D"],
        ["IM Put strict", "v9", "严格持有三周期", "strict_3_roll_cycles + hold_to_expiry", "entry date and IM roll calendar", "到期退出", "pass", "完成生命周期覆盖3次IM月换"],
        ["IC Call front", "v9 and earlier", "前月", "front_next_eligible after current IC reference expiry", "signal close", "T+1 close成交；月换维护", "warning", "没有最小DTE，真实信号DTE可低至约5日"],
        ["IC Call listed rank", "v10", "下月/下下月", "listed_rank_1/listed_rank_2 after current IC reference expiry", "signal close", "T+1 close成交；月换维护", "deprecated", "下下月曾跳到121D；不得再用日历月份简称"],
        ["IC Call fixed DTE", "v11", "60天/90天", "dte60_hard_band / dte90_hard_band", "signal close", "T+1 close成交；月换维护", "pass", "执行日DTE可因T+1漂移，硬约束只作用于信号日"],
        ["IC Call current test", "v12", "60天D4/D6/D8梯度", "dte60_hard_band + signal_delta_bins", "signal close", "T+1 close成交；月换维护", "pass", "期限与Delta上限均在信号日判断；执行日允许市场漂移"],
        ["IM Call current", "v27", "前月 + 5%救援向后一个月", "front_next_eligible; rescue_next_listed", "daily signal close", "T+1 close成交；救援最多5次", "high_risk", "代码是向后一个挂牌档，不是固定+1月；连续救援曾把新仓DTE推到312日"],
        ["CYB synthetic future", "v3", "月度合成期货", "same_expiry_call_put_cycle", "previous expiry close", "到下一实际ETF期权到期日", "pass", "周期边界连续，DTE为实际月度自然日"],
        ["STAR50 option research", "workspace audit", "科创50期权", "not_available", "not_available", "not_available", "not_auditable", "工作区没有正式冻结的科创50期权策略工件"],
    ]
    return pd.DataFrame(rows, columns=["scope", "versions", "legacy_wording", "canonical_rule", "anchor", "maintenance_or_exit", "status", "finding"])


def main() -> None:
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError(f"Formal output/staging already exists: {OUTPUT} / {STAGING}")
    missing = [str(path) for path in INPUTS.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing audit inputs: {missing}")

    observed: list[dict[str, object]] = []
    examples: list[dict[str, object]] = []

    # IC Put current mainline: execution-date +3 calendar months, nearest listed month.
    ic_put = pd.read_csv(INPUTS["ic_put_v21_trades"], parse_dates=["actual_execution_date", "new_month"])
    ic_put = ic_put[
        ic_put["candidate"].eq("real_l190_mom25")
        & ic_put["action"].isin(["close_buy", "close_roll_monthly"])
    ].copy()
    ic_daily = pd.read_csv(INPUTS["ic_put_v21_daily"], usecols=["date"], parse_dates=["date"])
    ic_calendar = pd.DatetimeIndex(sorted(ic_daily["date"].drop_duplicates()))
    ic_put["expiry"] = ic_put["new_month"].map(lambda m: fourth_wednesday(pd.Timestamp(m), ic_calendar))
    ic_put["target_date"] = ic_put["actual_execution_date"] + pd.DateOffset(months=3)
    ic_put["signal_dte"] = (ic_put["expiry"] - ic_put["actual_execution_date"]).dt.days
    ic_put["target_gap"] = (ic_put["expiry"] - ic_put["target_date"]).dt.days
    observed.append(stats_row("IC Put current", "target_plus_3m_nearest_listed_monthly_reset", "real", "execution_date_dte", ic_put["signal_dte"], "calendar_days", "IC Put v21 trade audit", "open and monthly-reset legs"))
    observed.append(stats_row("IC Put current", "target_plus_3m_nearest_listed_monthly_reset", "real", "expiry_minus_3m_target", ic_put["target_gap"], "calendar_days", "IC Put v21 trade audit", "negative means chosen expiry before target"))
    observed.append(stats_row("IC Put current", "monthly_reset", "real", "same_contract_month_reset", ic_put["same_month_reset"].astype(int), "binary", "IC Put v21 trade audit", "1 means monthly reset retained the same option contract month"))
    for row in pd.concat([ic_put.nsmallest(1, "target_gap"), ic_put.nlargest(1, "signal_dte")]).drop_duplicates().itertuples():
        examples.append({"scope": "IC Put current", "date": row.actual_execution_date.date().isoformat(), "old_expiry": "", "new_expiry": row.expiry.date().isoformat(), "observed_dte": int(row.signal_dte), "step_days": "", "target_date": row.target_date.date().isoformat(), "target_gap_days": int(row.target_gap), "finding": "3M nearest-listed can be materially shorter or longer than 90D"})

    # IM Put current mainline: actual MO expiry map, execution-date target in maintenance.
    im_trades = pd.read_csv(INPUTS["im_put_v12_trades"], parse_dates=["actual_execution_date", "target_date", "desired_contract_month"])
    im_trades = im_trades[
        im_trades["layer"].eq("real")
        & im_trades["candidate"].eq("valmom_center_floor3")
        & im_trades["action"].isin(["close_buy", "close_roll"])
    ].copy()
    raw_options = add_option_expiry(pd.read_csv(INPUTS["im_options"], parse_dates=["date"]))
    im_daily = pd.read_csv(INPUTS["im_daily"], usecols=["date"], parse_dates=["date"])
    expiry_map = actual_expiry_map(raw_options, im_daily)
    im_trades["expiry"] = im_trades["desired_contract_month"].map(expiry_map)
    if im_trades["expiry"].isna().any():
        raise RuntimeError("Missing IM actual expiry mapping")
    im_trades["signal_dte"] = (im_trades["expiry"] - im_trades["actual_execution_date"]).dt.days
    im_trades["target_gap"] = (im_trades["expiry"] - im_trades["target_date"]).dt.days
    observed.append(stats_row("IM Put current", "target_plus_3m_nearest_listed_monthly_reset", "real", "execution_date_dte", im_trades["signal_dte"], "calendar_days", "IM Put v12 trade audit + MO actual expiry map", "open and monthly-roll legs"))
    observed.append(stats_row("IM Put current", "target_plus_3m_nearest_listed_monthly_reset", "real", "expiry_minus_3m_target", im_trades["target_gap"], "calendar_days", "IM Put v12 trade audit + MO actual expiry map", "negative means chosen expiry before target"))

    # IM Call current mainline and rescue chain.
    im_call = pd.read_csv(INPUTS["im_call_v27_trades"], parse_dates=["eval_date", "old_expiry", "new_expiry"])
    im_call = im_call[im_call["layer"].eq("real") & im_call["candidate"].str.contains("threat5", na=False)].copy()
    normal = im_call[im_call["reason"].isin(["daily_entry", "monthly"]) & im_call["new_expiry"].notna()].copy()
    normal["signal_dte"] = (normal["new_expiry"] - normal["eval_date"]).dt.days
    rescue = im_call[im_call["reason"].eq("threat_roll") & im_call["new_expiry"].notna()].copy()
    rescue["signal_dte"] = (rescue["new_expiry"] - rescue["eval_date"]).dt.days
    rescue["expiry_step"] = (rescue["new_expiry"] - rescue["old_expiry"]).dt.days
    observed.append(stats_row("IM Call current", "front_next_eligible", "real", "normal_signal_dte", normal["signal_dte"], "calendar_days", "IM Call v27 trade audit", "daily entries and monthly maintenance"))
    observed.append(stats_row("IM Call current", "rescue_next_listed", "real", "rescue_signal_dte", rescue["signal_dte"], "calendar_days", "IM Call v27 trade audit", "new expiry measured from rescue signal date"))
    observed.append(stats_row("IM Call current", "rescue_next_listed", "real", "old_to_new_expiry_step", rescue["expiry_step"], "calendar_days", "IM Call v27 trade audit", "one listed step can span a quarterly gap"))
    for row in rescue.sort_values("eval_date").itertuples():
        if row.eval_date >= pd.Timestamp("2024-09-01") and row.eval_date <= pd.Timestamp("2024-11-30"):
            examples.append({"scope": "IM Call rescue", "date": row.eval_date.date().isoformat(), "old_expiry": row.old_expiry.date().isoformat(), "new_expiry": row.new_expiry.date().isoformat(), "observed_dte": int(row.signal_dte), "step_days": int(row.expiry_step), "target_date": "", "target_gap_days": "", "finding": "consecutive next-listed rescues accumulated a far-dated Call"})

    # IC Call historical and current date selection semantics.
    v9 = selected_signal_dte(INPUTS["ic_call_v9_signals"], "real_daily_d04_iv26")
    v10 = selected_signal_dte(INPUTS["ic_call_v10_signals"], "real_daily_t2_d04_iv26")
    v11_60 = selected_signal_dte(INPUTS["ic_call_v11_signals"], "real_daily_dte60_d04_iv26")
    v11_90 = selected_signal_dte(INPUTS["ic_call_v11_signals"], "real_daily_dte90_d10_iv26")
    v12 = selected_signal_dte(INPUTS["ic_call_v12_signals"], "real_daily_dte60_d4_then_d6_then_d8_iv26")
    observed.append(stats_row("IC Call v9", "front_next_eligible", "real", "signal_dte", v9["signal_dte"], "calendar_days", "IC Call v9 signals", "selected rows, including gate failures"))
    observed.append(stats_row("IC Call v10", "listed_rank_2", "real", "signal_dte", v10["signal_dte"], "calendar_days", "IC Call v10 signals", "deprecated 'next2' label"))
    observed.append(stats_row("IC Call v11", "dte60_hard_band", "real", "signal_dte", v11_60["signal_dte"], "calendar_days", "IC Call v11 signals", "45—75 hard band"))
    observed.append(stats_row("IC Call v11", "dte90_hard_band", "real", "signal_dte", v11_90["signal_dte"], "calendar_days", "IC Call v11 signals", "75—105 hard band"))
    observed.append(stats_row("IC Call v12", "dte60_hard_band", "real", "signal_dte", v12["signal_dte"], "calendar_days", "IC Call v12 signals", "45—75 hard band"))
    v10_long = v10.nlargest(1, "signal_dte").iloc[0]
    examples.append({"scope": "IC Call v10", "date": v10_long["eval_date"].date().isoformat(), "old_expiry": "", "new_expiry": v10_long["selection_expiry"].date().isoformat(), "observed_dte": int(v10_long["signal_dte"]), "step_days": "", "target_date": "", "target_gap_days": "", "finding": "listed-rank 2 is not calendar+2m or fixed60D"})

    # IC and IM hold-to-expiry cycle audits.
    ic_life = pd.read_csv(INPUTS["ic_put_v13_lifecycles"])
    for candidate, rule, sample in [
        ("model_v10_legacy_hold3m_m85", "legacy_3m_nearest_listed_hold_expiry", "model"),
        ("real_v10_legacy_hold3m_m85", "legacy_3m_nearest_listed_hold_expiry", "real"),
    ]:
        subset = ic_life[ic_life["candidate"].eq(candidate) & ic_life["completed"].astype(bool)]
        observed.append(stats_row("IC Put hold expiry", rule, sample, "calendar_holding_days", subset["calendar_days"], "calendar_days", "IC Put v13 lifecycle audit", "completed lifecycles"))
        observed.append(stats_row("IC Put hold expiry", rule, sample, "ic_rolls_covered", subset["ic_rolls_covered"], "roll_count", "IC Put v13 lifecycle audit", "completed lifecycles"))
    for sample in ["model", "real"]:
        subset = ic_life[ic_life["candidate"].eq(f"{sample}_3cycle_hold_expiry_m95") & ic_life["completed"].astype(bool)]
        observed.append(stats_row("IC Put strict", "strict_3_roll_cycles", sample, "calendar_holding_days", subset["calendar_days"], "calendar_days", "IC Put v13 lifecycle audit", "completed m95 lifecycles"))
        observed.append(stats_row("IC Put strict", "strict_3_roll_cycles", sample, "ic_rolls_covered", subset["ic_rolls_covered"], "roll_count", "IC Put v13 lifecycle audit", "completed m95 lifecycles"))

    im_life = pd.read_csv(INPUTS["im_put_v9_lifecycles"], parse_dates=["entry_date", "expiry"])
    im_strict = im_life[im_life["candidate"].eq("fixed175_or_mom120_3cycle_hold_expiry_m95")]
    for sample in ["model", "real"]:
        subset = im_strict[im_strict["layer"].eq(sample)]
        observed.append(stats_row("IM Put strict", "strict_3_roll_cycles", sample, "im_rolls_covered", subset["covered_rolls"], "roll_count", "IM Put v9 lifecycle audit", "completed m95 lifecycles"))

    # CYB synthetic monthly option cycle.
    cyb = pd.read_csv(INPUTS["cyb_v3_cycles"], parse_dates=["entry_date", "end_date", "expiry"])
    observed.append(stats_row("CYB synthetic", "same_expiry_monthly_cycle", "real", "calendar_holding_days", cyb["calendar_days"], "calendar_days", "CYB synthetic v3 monthly cycles", "completed and terminal-open cycles"))

    observed_df = pd.DataFrame(observed)
    examples_df = pd.DataFrame(examples)
    checks = {
        "input_files_present": True,
        "ic_put_current_open_or_monthly_reset_count_is_51": len(ic_put) == 51,
        "ic_put_current_has_same_month_resets": int(ic_put["same_month_reset"].sum()) > 0,
        "im_put_current_open_or_monthly_roll_count_is_59": len(im_trades) == 59,
        "ic_v10_listed_rank_2_reaches_at_least_121d": int(v10["signal_dte"].max()) >= 121,
        "ic_v11_dte60_signal_band": bool(v11_60["signal_dte"].between(45, 75).all()),
        "ic_v11_dte90_signal_band": bool(v11_90["signal_dte"].between(75, 105).all()),
        "ic_v12_dte60_signal_band": bool(v12["signal_dte"].between(45, 75).all()),
        "im_call_rescue_expiry_strictly_later": bool((rescue["expiry_step"] > 0).all()),
        "im_call_rescue_reaches_at_least_300d": int(rescue["signal_dte"].max()) >= 300,
        "im_call_rescue_has_quarterly_gap": int(rescue["expiry_step"].max()) >= 90,
        "ic_legacy_3m_is_not_strict_3cycle": bool((ic_life[ic_life["candidate"].str.contains("legacy") & ic_life["completed"].astype(bool)]["ic_rolls_covered"] != 3).any()),
        "ic_strict_3cycle_all_completed_cover_3": bool((ic_life[ic_life["candidate"].str.contains("3cycle") & ic_life["completed"].astype(bool)]["ic_rolls_covered"] == 3).all()),
        "im_strict_3cycle_all_cover_3": bool((im_strict["covered_rolls"] == 3).all()),
        "cyb_cycle_expiry_matches_end": bool((cyb.loc[cyb["status"].eq("completed"), "expiry"] == cyb.loc[cyb["status"].eq("completed"), "end_date"]).all()),
        "no_negative_observed_dte": bool((observed_df.loc[observed_df["metric"].str.contains("dte"), "min"] >= 0).all()),
    }
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise RuntimeError(f"Date semantic audit checks failed: {failed}")

    STAGING.mkdir(parents=True)
    glossary().to_csv(STAGING / "canonical_glossary.csv", index=False, encoding="utf-8-sig")
    inventory().to_csv(STAGING / "semantic_inventory.csv", index=False, encoding="utf-8-sig")
    observed_df.to_csv(STAGING / "observed_dte_summary.csv", index=False, encoding="utf-8-sig")
    examples_df.to_csv(STAGING / "exception_examples.csv", index=False, encoding="utf-8-sig")
    (STAGING / "audit_checks.json").write_text(json.dumps({"audit_complete": True, "checks": checks}, ensure_ascii=False, indent=2), encoding="utf-8")

    input_hashes = {name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for name, path in INPUTS.items()}
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "purpose": "read-only option expiry and roll-date semantic audit; no strategy return rerun",
        "input_hashes": input_hashes,
        "audit_status": "complete_with_material_findings",
        "live_approval": False,
        "material_findings": [
            "IC v10 next2 was listed-rank 2 and reached 121D; it was not calendar+2m.",
            "IM v27 rescue was next-listed and repeated rescue reached 312D; it was not fixed +1 calendar month.",
            "IC/IM Put 3M mainlines use a calendar target plus nearest listed expiry and monthly reset; they are not fixed 90D or hold-to-expiry.",
        ],
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    record = f"""# 期权日期语义统一审计 v1

## 结论

审计完成，所有冻结输出均保持只读。当前 IC Call v12 的 60D 定义通过硬区间检查；IC/IM Put 主线的“3个月”代码可复现，但应改称“3M目标最近挂牌、随期货月换重置”，不能理解为固定90D或持有到期。

最重要的高风险项是 IM Call v27：代码中的5%救援是从当前到期日向后取“下一个挂牌到期日”，不是固定向后一个日历月。连续救援的真实新仓DTE最高达到 {int(rescue['signal_dte'].max())} 日，单步到期日后移最高 {int(rescue['expiry_step'].max())} 日。冻结结果只证明该代码口径，不证明“固定后移一个月”的口径。

## 关键观测

- IC Put 当前主线开仓/月换事件 {len(ic_put)} 次，执行日DTE {int(ic_put['signal_dte'].min())}—{int(ic_put['signal_dte'].max())} 日，中位 {float(ic_put['signal_dte'].median()):.0f} 日；其中 {int(ic_put['same_month_reset'].sum())} 次在同一合约月内重置。
- IM Put 当前代表线开仓/月换事件 {len(im_trades)} 次，执行日DTE {int(im_trades['signal_dte'].min())}—{int(im_trades['signal_dte'].max())} 日，中位 {float(im_trades['signal_dte'].median()):.0f} 日。
- IC Call v10 挂牌第二档真实信号DTE最高 {int(v10['signal_dte'].max())} 日；v11/v12的60D信号全部在45—75日硬区间。
- IC Put旧“3个月持有到期”覆盖2—5次IC月换；严格三周期版本的已完成生命周期全部覆盖3次。
- 创业板合成期货月度周期DTE为 {int(cyb['calendar_days'].min())}—{int(cyb['calendar_days'].max())} 日，中位 {float(cyb['calendar_days'].median()):.0f} 日，已完成周期的结束日等于实际到期日。
- 工作区没有可供本次审计的正式科创50期权策略工件，不能对其历史口头定义背书。

## 后续强制命名

新研究不得单独使用“前月/下月/下下月”。必须写成 `front_next_eligible`、`calendar_plus_1m/2m`、`listed_rank_1/2` 或 `DTE目标+硬区间`。所有DTE必须注明信号日还是执行日；所有“3M”必须注明目标日、最近挂牌、月换重置或持有到期。

## 状态

本文件是研究审计证据，不是交易建议或实盘批准。IM v27 的收益不在本次重跑范围；在进入实盘流程前，必须由用户明确选择“next-listed”还是“固定+1日历月”救援语义，并以新版本预注册验证。
"""
    (STAGING / "record.md").write_text(record, encoding="utf-8")
    (STAGING / "command_log.txt").write_text(f"python {Path(__file__).name}\n", encoding="utf-8")

    files = sorted(path for path in STAGING.iterdir() if path.name != "output_manifest.json")
    out_manifest = {
        "version": VERSION,
        "files": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in files},
    }
    (STAGING / "output_manifest.json").write_text(json.dumps(out_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(STAGING, OUTPUT)
    print(json.dumps({"version": VERSION, "output": str(OUTPUT), "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

