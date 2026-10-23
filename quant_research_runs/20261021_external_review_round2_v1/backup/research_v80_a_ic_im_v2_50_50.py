from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import warnings
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
MOMENTUM_ROOT = ROOT.parent / "A股美股动量组合策略"
V80_SOURCE = MOMENTUM_ROOT / "mnt_bot V 8.0 plus.py"
V80_PANEL = (
    MOMENTUM_ROOT
    / "outputs"
    / "v78_v79_vol_management_and_sleeve_diversification_20260824"
    / "latest_market_data"
    / "cn_close.csv"
)
V80_OLD_A_RETURNS = (
    MOMENTUM_ROOT
    / "outputs"
    / "v80_suba_olda_only_implementation_20260828"
    / "daily_returns.csv"
)
IC_IM_DAILY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "daily_candidates.csv.gz"
IC_IM_MANIFEST = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "data_manifest.json"
IC_IM_INTEGRITY = ROOT / "outputs" / "ic_im_system_mainlines_v2" / "integrity_checks.json"
OUTPUT = ROOT / "outputs" / "a_v80_ic_im_v2_50_50_20260901"

FORMAL_A_START = pd.Timestamp("2017-05-26")
ANNUALIZATION_DAYS = 252.0
WINDOWS = (("Full", None), ("10Y", 10), ("5Y", 5), ("3Y", 3), ("1Y", 1))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_head(path: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def load_v80_module():
    spec = importlib.util.spec_from_file_location("v80_a_ic_im_combo", V80_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load V8.0 authority: {V80_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_return(series: pd.Series, name: str) -> pd.Series:
    out = pd.Series(series, copy=True)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out = pd.to_numeric(out, errors="coerce").dropna().sort_index()
    if out.index.has_duplicates:
        raise RuntimeError(f"{name} has duplicate dates")
    if (out <= -1.0).any():
        raise RuntimeError(f"{name} contains return <= -100%")
    return out.astype(float).rename(name)


def metric_row(name: str, window: str, sample: pd.Series, status: str) -> dict[str, object]:
    if status != "formal" or len(sample) < 2:
        return {
            "series": name,
            "window": window,
            "status": status,
            "start": None,
            "end": sample.index.max().date().isoformat() if len(sample) else None,
            "rows": 0,
            "total_return": None,
            "cagr_calendar": None,
            "annualized_return_252": None,
            "annualized_volatility_252": None,
            "sharpe_repo_252": None,
            "max_drawdown": None,
            "calmar_calendar": None,
        }
    nav = (1.0 + sample).cumprod()
    years = (sample.index[-1] - sample.index[0]).days / 365.25
    total = float(nav.iloc[-1] - 1.0)
    cagr = float(nav.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else np.nan
    ann_252 = float(nav.iloc[-1] ** (ANNUALIZATION_DAYS / len(sample)) - 1.0)
    std = float(sample.std(ddof=1))
    ann_vol = std * np.sqrt(ANNUALIZATION_DAYS)
    sharpe = float(sample.mean()) / std * np.sqrt(ANNUALIZATION_DAYS) if std > 0 else np.nan
    max_dd = float((nav / nav.cummax() - 1.0).min())
    return {
        "series": name,
        "window": window,
        "status": status,
        "start": sample.index[0].date().isoformat(),
        "end": sample.index[-1].date().isoformat(),
        "rows": int(len(sample)),
        "total_return": total,
        "cagr_calendar": cagr,
        "annualized_return_252": ann_252,
        "annualized_volatility_252": ann_vol,
        "sharpe_repo_252": sharpe,
        "max_drawdown": max_dd,
        "calmar_calendar": cagr / abs(max_dd) if max_dd < 0 else np.nan,
    }


def standard_metrics(series_map: dict[str, pd.Series], end: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for name, values in series_map.items():
        series = clean_return(values, name)
        series = series[series.index <= end]
        for label, years in WINDOWS:
            if years is None:
                sample = series
                status = "formal" if len(sample) >= 2 else "N/A: no data"
            else:
                requested_start = end - pd.DateOffset(years=years)
                if len(series) == 0 or series.index[0] > requested_start + pd.Timedelta(days=7):
                    sample = series.iloc[0:0]
                    first = series.index[0].date().isoformat() if len(series) else "no data"
                    status = f"N/A: history starts {first}, after required {requested_start.date().isoformat()}"
                else:
                    sample = series[series.index >= requested_start]
                    status = "formal" if len(sample) >= 2 else "N/A: insufficient rows"
            rows.append(metric_row(name, label, sample, status))
    return pd.DataFrame(rows)


def monthly_rebalanced_return(returns: pd.DataFrame, target: pd.Series) -> pd.Series:
    weights = target.astype(float).copy()
    values: list[float] = []
    last_period = None
    for date, row in returns.iterrows():
        period = date.to_period("M")
        if last_period is None or period != last_period:
            weights = target.astype(float).copy()
        day_return = float((weights * row).sum())
        values.append(day_return)
        weights = weights * (1.0 + row)
        weights = weights / float(weights.sum())
        last_period = period
    return pd.Series(values, index=returns.index, name="A50_IC25_IM25_monthly_rebalance")


def fmt_pct(value: object) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.2%}"


def fmt_num(value: object) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.3f}"


def build_report(metrics: pd.DataFrame, common_metrics: pd.DataFrame, audit: dict[str, object]) -> str:
    focus_order = ["A50_IC25_IM25", "A50_IC50", "A50_IM50", "IC50_IM50", "V80_A", "IC_V2", "IM_V2"]
    focus = metrics[metrics["series"].isin(focus_order)].copy()
    focus["series"] = pd.Categorical(focus["series"], focus_order, ordered=True)
    focus["window"] = pd.Categorical(focus["window"], [x[0] for x in WINDOWS], ordered=True)
    focus = focus.sort_values(["series", "window"])
    table = [
        "| 序列 | 窗口 | 状态 | 起止 | CAGR | 年化波动 | Sharpe | MaxDD |",
        "|:--|:--|:--|:--|--:|--:|--:|--:|",
    ]
    labels = {
        "A50_IC25_IM25": "50%A + 25%IC + 25%IM（主测试）",
        "A50_IC50": "50%A + 50%IC",
        "A50_IM50": "50%A + 50%IM",
        "IC50_IM50": "50%IC + 50%IM（套利块）",
        "V80_A": "V8.0 A",
        "IC_V2": "IC V2",
        "IM_V2": "IM V2",
    }
    for row in focus.itertuples(index=False):
        span = f"{row.start}~{row.end}" if row.start else "—"
        table.append(
            f"| {labels[str(row.series)]} | {row.window} | {row.status} | {span} | "
            f"{fmt_pct(row.cagr_calendar)} | {fmt_pct(row.annualized_volatility_252)} | "
            f"{fmt_num(row.sharpe_repo_252)} | {fmt_pct(row.max_drawdown)} |"
        )

    full = common_metrics[common_metrics["window"].eq("Full")].set_index("series")
    primary = full.loc["A50_IC25_IM25"]
    monthly = full.loc["A50_IC25_IM25_monthly_rebalance"]
    a = full.loc["V80_A_common"]
    block = full.loc["IC50_IM50_common"]
    corr = audit["correlations"]
    lines = [
        "# V8.0 A 与 IC/IM V2 50:50 组合研究",
        "",
        "## 结论",
        "",
        f"- 主测试按 **50% V8.0 A + 25% IC V2 + 25% IM V2**，共同正式区间为 "
        f"`{audit['common_start']}` 至 `{audit['common_end']}`。Full CAGR 为 **{fmt_pct(primary.cagr_calendar)}**，"
        f"最大回撤 **{fmt_pct(primary.max_drawdown)}**，年化波动 **{fmt_pct(primary.annualized_volatility_252)}**，"
        f"repo口径Sharpe **{fmt_num(primary.sharpe_repo_252)}**。",
        f"- 同区间 A 单独 CAGR/MaxDD 为 {fmt_pct(a.cagr_calendar)} / {fmt_pct(a.max_drawdown)}；"
        f"IC/IM各半套利块为 {fmt_pct(block.cagr_calendar)} / {fmt_pct(block.max_drawdown)}。",
        f"- A 与 IC/IM 套利块日收益相关系数为 **{float(corr['A_vs_ICIM']):.3f}**；IC 与 IM 为 "
        f"**{float(corr['IC_vs_IM']):.3f}**。",
        f"- 月度再平衡敏感性为 CAGR {fmt_pct(monthly.cagr_calendar)}、MaxDD {fmt_pct(monthly.max_drawdown)}；"
        "该行不另扣跨袖套资金再平衡成本，只用于检查每日固定权重假设。",
        "- 10Y/5Y组合窗口为 N/A：当前 V2 真实期权正式路径分别始于 2022-09-19（IC）和 2022-07-22（IM），"
        "不能用发布前代理数据回填。",
        "",
        "## 标准窗口结果",
        "",
        *table,
        "",
        "## 口径",
        "",
        "- 权威策略：IC/IM 使用冻结 V2；A 使用当前 `mnt_bot V 8.0 plus.py` 的 Sub-A 生产链。",
        "- 权重：主测试等价于先把 IC/IM 各半形成套利块，再由 A 与套利块各占 50%；日收益线性合成，"
        "等价于每日维持目标资金权重。",
        "- 成本：三条底层收益均保留各自正式交易成本。A 含单边 0.10% 换手成本及 3% 现金/融资；"
        "IC/IM 含期货、期权、展期成本及每1倍期货30%保证金/缓冲下的现金收益。未另加跨策略资金划转成本。",
        "- 执行：A 为 T日收盘目标、影响下一行收益；IC/IM 继承冻结 V2 的期货网格 T+1开盘、期权 T+1共同交易日收盘等正式时点。",
        "- 数据：A 为真实指数/债券指数收盘面板；成交额风控为中证2000 CSIndex 官方成交额与创业板 Sohu 同指数成交额。"
        "IC/IM 为冻结输出中的真实期货、期权、指数与估值逐日路径。",
        "- 年化：主表 CAGR 按实际日历跨度；波动率与 Sharpe 按 252 个交易日，Sharpe 沿用仓库的零额外无风险扣减口径。",
        "",
        "## 完整性检查",
        "",
        f"- V8.0 旧参数回放与已核验日收益最大误差：`{audit['v80_old_parameter_parity_max_abs']:.3e}`。",
        f"- IC/IM 冻结 V2 完整性：`all_checks_passed={audit['ic_im_integrity_all_passed']}`。",
        f"- 共同日期行数：`{audit['common_rows']}`；共同区间内三条路径缺失日期数："
        f"A={audit['missing_dates_in_common_span']['V80_A']}、IC={audit['missing_dates_in_common_span']['IC_V2']}、"
        f"IM={audit['missing_dates_in_common_span']['IM_V2']}。未做前向填充。",
        "",
        "## 边界",
        "",
        "本结果是研究审计，不是实盘授权、最新信号或下单建议。组合可用正式历史不足5年，且没有模拟跨策略资金调拨、"
        "涨跌停/冲击成本、保证金临时上调或期权盘口容量；进入资金配置前还应做权重区间、再平衡频率和压力期资金占用研究。",
        "",
        "详细产物：`daily_returns.csv`、`window_metrics.csv`、`common_sample_metrics.csv`、`correlations.csv`、"
        "`monthly_returns.csv`、`audit.json`、`nav_drawdown.png`。",
    ]
    return "\n".join(lines) + "\n"


def plot_nav_drawdown(returns: pd.DataFrame, path: Path) -> None:
    labels = {
        "V80_A": "V8.0 A",
        "IC50_IM50": "IC/IM 50:50 block",
        "A50_IC25_IM25": "A 50% + IC 25% + IM 25%",
    }
    selected = returns[list(labels)].copy()
    nav = (1.0 + selected).cumprod()
    dd = nav / nav.cummax() - 1.0
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for name in selected:
        axes[0].plot(nav.index, nav[name], label=labels[name], linewidth=1.7)
        axes[1].plot(dd.index, dd[name], label=labels[name], linewidth=1.3)
    axes[0].set_ylabel("NAV (rebased to 1)")
    axes[1].set_ylabel("Drawdown")
    axes[1].yaxis.set_major_formatter(lambda value, _pos: f"{value:.0%}")
    axes[0].grid(alpha=0.25)
    axes[1].grid(alpha=0.25)
    axes[0].legend(loc="upper left")
    fig.suptitle("V8.0 A and IC/IM V2 combination (formal common sample)")
    fig.tight_layout()
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    required = [V80_SOURCE, V80_PANEL, V80_OLD_A_RETURNS, IC_IM_DAILY, IC_IM_MANIFEST, IC_IM_INTEGRITY]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required files: {missing}")
    OUTPUT.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(IC_IM_MANIFEST.read_text(encoding="utf-8"))
    integrity = json.loads(IC_IM_INTEGRITY.read_text(encoding="utf-8"))
    if not integrity.get("all_checks_passed"):
        raise RuntimeError("Frozen IC/IM V2 integrity checks are not all passed")
    formal_end = pd.Timestamp(manifest["data_end"])

    v80 = load_v80_module()
    cn_close = pd.read_csv(V80_PANEL, parse_dates=["date"]).set_index("date").sort_index()
    cn_close = cn_close[cn_close.index <= formal_end]
    if v80.CN_BOND_CODE not in cn_close.columns:
        raise RuntimeError("Frozen V8.0 panel is missing the official bond column")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        volume_signal, volume_feature = v80._load_suba_volume_signal(expected_date=formal_end)
    volume_feature = volume_feature.copy()
    volume_feature.index = pd.to_datetime(volume_feature.index).tz_localize(None).normalize()
    volume_signal = pd.Series(volume_signal, copy=True)
    volume_signal.index = pd.to_datetime(volume_signal.index).tz_localize(None).normalize()
    if v80._suba_volume_feature_has_unresolved(volume_feature.loc[:formal_end]):
        raise RuntimeError("V8.0 amount overlay has unresolved rows in the formal sample")

    current_a = v80.run_cn_strategy(cn_close, v80.CN_EQUITY_CODES)
    current_a = v80._apply_suba_volume_overlay_policy(
        current_a, cn_close, volume_signal, volume_feature, allow_unresolved_suba_volume=False
    )
    a_return = clean_return(current_a["return"], "V80_A")
    a_return = a_return[(a_return.index >= FORMAL_A_START) & (a_return.index <= formal_end)]

    current_target_vol = float(v80.CN_TARGET_VOL)
    current_max_lev = float(v80.CN_MAX_LEV)
    v80.CN_TARGET_VOL = 0.30
    v80.CN_MAX_LEV = 1.50
    try:
        old_a = v80.run_cn_strategy(cn_close, v80.CN_EQUITY_CODES)
        old_a = v80._apply_suba_volume_overlay_policy(
            old_a, cn_close, volume_signal, volume_feature, allow_unresolved_suba_volume=False
        )
    finally:
        v80.CN_TARGET_VOL = current_target_vol
        v80.CN_MAX_LEV = current_max_lev
    old_saved = pd.read_csv(V80_OLD_A_RETURNS, encoding="utf-8-sig", parse_dates=["date"]).set_index("date").iloc[:, -1]
    old_pair = pd.concat([old_a["return"].rename("replay"), old_saved.rename("saved")], axis=1, join="inner")
    old_pair = old_pair[old_pair.index <= formal_end].dropna()
    old_parity = float((old_pair["replay"] - old_pair["saved"]).abs().max())
    if old_parity > 1e-12:
        raise RuntimeError(f"V8.0 official-chain parity failed: {old_parity}")

    frozen = pd.read_csv(IC_IM_DAILY, parse_dates=["date"], low_memory=False)
    selected = frozen[
        ((frozen["product"] == "IC") & (frozen["candidate"] == "IC_wide4_mom050"))
        | ((frozen["product"] == "IM") & (frozen["candidate"] == "IM_4tier_q750_850_900_925_mom4"))
    ].copy()
    ic = clean_return(selected[selected["product"].eq("IC")].set_index("date")["cash_ret"], "IC_V2")
    im = clean_return(selected[selected["product"].eq("IM")].set_index("date")["cash_ret"], "IM_V2")
    if ic.index.max() != formal_end or im.index.max() != formal_end:
        raise RuntimeError("IC/IM selected paths do not end on the frozen formal date")

    native_icim = pd.concat([ic, im], axis=1, join="inner").dropna()
    icim = clean_return(native_icim.mul([0.5, 0.5]).sum(axis=1), "IC50_IM50")
    all_common = pd.concat([a_return, ic, im], axis=1, join="inner").dropna()
    common_start = all_common.index.min()
    common_end = all_common.index.max()
    if common_end != formal_end:
        raise RuntimeError(f"Formal common end drifted: {common_end} != {formal_end}")

    in_common_span = {
        "V80_A": a_return[(a_return.index >= common_start) & (a_return.index <= common_end)],
        "IC_V2": ic[(ic.index >= common_start) & (ic.index <= common_end)],
        "IM_V2": im[(im.index >= common_start) & (im.index <= common_end)],
    }
    reference_dates = set().union(*(set(series.index) for series in in_common_span.values()))
    missing_dates: dict[str, int] = {}
    for name, series in in_common_span.items():
        missing_dates[name] = len(reference_dates - set(series.index))

    combined = pd.DataFrame(index=all_common.index)
    combined["V80_A"] = all_common["V80_A"]
    combined["IC_V2"] = all_common["IC_V2"]
    combined["IM_V2"] = all_common["IM_V2"]
    combined["IC50_IM50"] = 0.5 * combined["IC_V2"] + 0.5 * combined["IM_V2"]
    combined["A50_IC25_IM25"] = 0.5 * combined["V80_A"] + 0.25 * combined["IC_V2"] + 0.25 * combined["IM_V2"]
    combined["A50_IC50"] = 0.5 * combined["V80_A"] + 0.5 * combined["IC_V2"]
    combined["A50_IM50"] = 0.5 * combined["V80_A"] + 0.5 * combined["IM_V2"]
    combined["A50_IC25_IM25_monthly_rebalance"] = monthly_rebalanced_return(
        combined[["V80_A", "IC_V2", "IM_V2"]], pd.Series({"V80_A": 0.5, "IC_V2": 0.25, "IM_V2": 0.25})
    )

    native_series = {
        "V80_A": a_return,
        "IC_V2": ic,
        "IM_V2": im,
        "IC50_IM50": icim,
        "A50_IC25_IM25": combined["A50_IC25_IM25"],
        "A50_IC50": combined["A50_IC50"],
        "A50_IM50": combined["A50_IM50"],
    }
    metrics = standard_metrics(native_series, formal_end)
    common_series = {
        "V80_A_common": combined["V80_A"],
        "IC_V2_common": combined["IC_V2"],
        "IM_V2_common": combined["IM_V2"],
        "IC50_IM50_common": combined["IC50_IM50"],
        "A50_IC25_IM25": combined["A50_IC25_IM25"],
        "A50_IC50": combined["A50_IC50"],
        "A50_IM50": combined["A50_IM50"],
        "A50_IC25_IM25_monthly_rebalance": combined["A50_IC25_IM25_monthly_rebalance"],
    }
    common_metrics = standard_metrics(common_series, formal_end)

    correlations = combined[["V80_A", "IC_V2", "IM_V2", "IC50_IM50"]].corr()
    corr_summary = {
        "A_vs_ICIM": float(correlations.loc["V80_A", "IC50_IM50"]),
        "IC_vs_IM": float(correlations.loc["IC_V2", "IM_V2"]),
        "A_vs_IC": float(correlations.loc["V80_A", "IC_V2"]),
        "A_vs_IM": float(correlations.loc["V80_A", "IM_V2"]),
    }
    sources = volume_feature.loc[:formal_end, ["zz2000_source", "cyb_source"]].drop_duplicates()
    audit = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "live_approved": False,
        "weights": {"V80_A": 0.50, "IC_V2": 0.25, "IM_V2": 0.25},
        "weight_convention": "fixed daily target weights; monthly rebalance included as no-extra-cost sensitivity",
        "common_start": common_start.date().isoformat(),
        "common_end": common_end.date().isoformat(),
        "common_rows": int(len(combined)),
        "missing_dates_in_common_span": missing_dates,
        "correlations": corr_summary,
        "v80_authority": str(V80_SOURCE),
        "v80_git_head": git_head(MOMENTUM_ROOT),
        "v80_source_sha256": sha256(V80_SOURCE),
        "v80_panel": str(V80_PANEL),
        "v80_panel_sha256": sha256(V80_PANEL),
        "v80_parameters": {
            "target_vol": current_target_vol,
            "vol_window": int(v80.CN_VOL_WINDOW),
            "min_leverage": float(v80.CN_MIN_LEV),
            "max_leverage": current_max_lev,
            "one_way_commission": float(v80.CN_COMMISSION),
            "cash_and_financing_annual": float(v80.CN_RF_ANNUAL),
        },
        "v80_amount_sources": sources.to_dict(orient="records"),
        "v80_old_parameter_parity_max_abs": old_parity,
        "ic_im_authority": str(IC_IM_DAILY),
        "ic_im_git_head": git_head(ROOT),
        "ic_im_daily_sha256": sha256(IC_IM_DAILY),
        "ic_im_manifest_sha256": sha256(IC_IM_MANIFEST),
        "ic_im_integrity_all_passed": bool(integrity["all_checks_passed"]),
        "ic_im_frozen_data_end": manifest["data_end"],
        "annualization": {
            "cagr": "actual calendar span",
            "volatility_and_sharpe": ANNUALIZATION_DAYS,
            "sharpe_extra_risk_free_subtraction": 0.0,
        },
        "cross_sleeve_rebalance_cost": 0.0,
        "known_omissions": [
            "cross-strategy capital-transfer cost",
            "A-share limit-up/limit-down executability",
            "market impact and slippage beyond embedded strategy costs",
            "temporary margin hikes and live option-book capacity",
        ],
    }

    output_daily = combined.copy()
    for column in list(output_daily.columns):
        output_daily[f"NAV_{column}"] = (1.0 + output_daily[column]).cumprod()
    output_daily.to_csv(OUTPUT / "daily_returns.csv", encoding="utf-8-sig", index_label="date")
    metrics.to_csv(OUTPUT / "window_metrics.csv", encoding="utf-8-sig", index=False)
    common_metrics.to_csv(OUTPUT / "common_sample_metrics.csv", encoding="utf-8-sig", index=False)
    correlations.to_csv(OUTPUT / "correlations.csv", encoding="utf-8-sig")
    (1.0 + combined).resample("ME").prod().sub(1.0).to_csv(
        OUTPUT / "monthly_returns.csv", encoding="utf-8-sig", index_label="month"
    )
    volume_output = volume_feature.loc[:formal_end].copy()
    volume_output["combined_signal_used"] = volume_signal.reindex(volume_output.index)
    volume_output.to_csv(OUTPUT / "v80_amount_overlay_evidence.csv", encoding="utf-8-sig", index_label="date")
    (OUTPUT / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    plot_nav_drawdown(combined, OUTPUT / "nav_drawdown.png")
    (OUTPUT / "report.md").write_text(build_report(metrics, common_metrics, audit), encoding="utf-8")
    (OUTPUT / "command_log.txt").write_text(
        f'python "{Path(__file__).name}"\n', encoding="utf-8"
    )

    primary = common_metrics[
        common_metrics["series"].eq("A50_IC25_IM25") & common_metrics["window"].eq("Full")
    ].iloc[0]
    print(f"Output: {OUTPUT}")
    print(f"Common formal sample: {common_start.date()} to {common_end.date()} ({len(combined)} rows)")
    print(
        "A50/IC25/IM25 Full: "
        f"CAGR={primary['cagr_calendar']:.6%}, "
        f"Vol={primary['annualized_volatility_252']:.6%}, "
        f"Sharpe={primary['sharpe_repo_252']:.6f}, "
        f"MaxDD={primary['max_drawdown']:.6%}"
    )
    print(f"V8 old-parameter parity max abs: {old_parity:.3e}")


if __name__ == "__main__":
    main()
